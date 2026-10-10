"""On-premises target "agent": deploy onto the user's own machine, not the CloudMorph server.

    user's PC: python3 cloudmorph_agent.py --server https://<cloudmorph> --token <their API token>
       │  outbound HTTPS only (no public IP, no inbound port): long-poll POST /agent/poll
       ▼
    server: target "agent" -> AgentHub.dispatch() queues a job for *that user's* agents and blocks the
            pipeline thread until the agent reports back, so analyze -> deploy -> heal works unchanged:
            healer patches the Dockerfile and the next attempt goes to the same machine.
       ▲
    agent: GET source tarball -> docker build/run on the PC -> health check -> optional cloudflared
           tunnel -> POST result (stderr in the deployer layout, so healer classifies it the same way)

Multi-user: an agent authenticates with its user's API token and only ever receives that user's jobs.
A redeploy of an app goes back to the agent that ran it last (its containers/data live there) when that
agent is online. No agent online -> the attempt fails at once as user_action_required (healer stops).
State is in memory: jobs only live while a pipeline thread waits on them; agents re-register by polling.
"""

from __future__ import annotations

import os
import secrets
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.schemas import DeployResult

ONLINE_SECONDS = 45  # agent counts as online if it polled / sent logs this recently
PICKUP_SECONDS = 60  # a queued job nobody picks up fails instead of hanging the pipeline
POLL_SECONDS = 25.0  # how long one /agent/poll waits for a job (under proxy/agent read timeouts)
JOB_TIMEOUT_SECONDS = int(os.getenv("CLOUDMORPH_AGENT_JOB_TIMEOUT", "1800"))
ARCHIVE_SKIP = {".git", ".deployer", "node_modules", ".venv", "venv", "__pycache__"}


def _fail(stage: str, kind: str, message: str) -> DeployResult:
    """Same stderr header as deployer.py, so healer's unhealable-kind rule applies."""
    return DeployResult(
        success=False,
        target="agent",
        exit_code=1,
        stderr=f"[deployer] stage={stage} kind={kind} target=agent\n{message}\n--- error ---\n{message}",
    )


@dataclass
class Agent:
    owner: str
    name: str
    info: dict = field(default_factory=dict)
    last_seen: float = 0.0
    job: str | None = None  # job currently assigned

    @property
    def online(self) -> bool:
        return time.time() - self.last_seen < ONLINE_SECONDS


@dataclass
class Job:
    id: str
    owner: str
    app: str
    archive: Path
    prefer: str | None  # agent that ran this app last
    log: Callable[[str], None]
    created: float = field(default_factory=time.time)
    agent: str | None = None
    result: dict | None = None
    done: threading.Event = field(default_factory=threading.Event)


class AgentHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agents: dict[tuple[str, str], Agent] = {}
        self._jobs: dict[str, Job] = {}
        self._queue: list[str] = []
        self._last_agent: dict[tuple[str, str], str] = {}  # (owner, app) -> agent name

    # ---------- what the dashboard sees ----------

    def agents_of(self, owner: str) -> list[dict]:
        with self._lock:
            return [
                {
                    "name": a.name,
                    "online": a.online,
                    "last_seen": a.last_seen,
                    "busy": a.job is not None,
                    "info": a.info,
                }
                for a in self._agents.values()
                if a.owner == owner
            ]

    def has_online(self, owner: str) -> bool:
        with self._lock:
            return any(a.online for a in self._agents.values() if a.owner == owner)

    # ---------- pipeline side (worker thread) ----------

    def dispatch(self, owner: str, app: str, src_dir: str, dockerfile: str, log: Callable[[str], None]):
        """deploy_fn for target "agent": blocks until the agent reports, then returns its DeployResult."""
        if not self.has_online(owner):
            return _fail(
                "preflight",
                "user_action_required",
                f"no on-premises agent online for user {owner}: run cloudmorph_agent.py on your machine",
            )
        if dockerfile:
            Path(src_dir, "Dockerfile").write_text(
                dockerfile if dockerfile.endswith("\n") else dockerfile + "\n"
            )
        fd, name = tempfile.mkstemp(prefix="cloudmorph-agent-", suffix=".tar.gz")
        os.close(fd)
        archive = Path(name)
        with self._lock:
            job = Job(secrets.token_hex(10), owner, app, archive, self._last_agent.get((owner, app)), log)
        try:
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(src_dir, arcname=".", filter=_skip_heavy)
            with self._lock:
                self._jobs[job.id] = job
                self._queue.append(job.id)
            log("agent: job queued for your machine")
            return self._wait(job)
        finally:
            with self._lock:
                self._jobs.pop(job.id, None)
                if job.id in self._queue:
                    self._queue.remove(job.id)
                for a in self._agents.values():
                    if a.job == job.id:
                        a.job = None
            archive.unlink(missing_ok=True)

    def _wait(self, job: Job) -> DeployResult:
        deadline = job.created + JOB_TIMEOUT_SECONDS
        while not job.done.wait(2):
            with self._lock:
                agent = self._agents.get((job.owner, job.agent)) if job.agent else None
                waiting = job.agent is None
            now = time.time()
            if waiting and now - job.created > PICKUP_SECONDS:
                return _fail(
                    "preflight", "user_action_required", "no agent picked up the job (is it running?)"
                )
            if agent is not None and not agent.online:
                return _fail("deploy", "user_action_required", f"agent {agent.name} went offline mid-deploy")
            if now > deadline:
                return _fail("deploy", "timeout", f"agent did not finish within {JOB_TIMEOUT_SECONDS}s")
        r = job.result or {}
        with self._lock:
            if r.get("success") and job.agent:
                self._last_agent[(job.owner, job.app)] = job.agent
        return DeployResult(
            success=bool(r.get("success")),
            target="agent",
            exit_code=int(r.get("exit_code") or (0 if r.get("success") else 1)),
            stdout=str(r.get("stdout") or "")[-6000:],
            stderr=str(r.get("stderr") or "")[-12000:],
            url=r.get("url") or None,
            duration_sec=r.get("duration_sec"),
        )

    # ---------- agent side (HTTP endpoints) ----------

    def poll(self, owner: str, name: str, info: dict) -> dict | None:
        """Heartbeat + take the next job for this owner, if this agent is free. Job dict or None."""
        with self._lock:
            agent = self._agents.setdefault((owner, name), Agent(owner, name))
            agent.info, agent.last_seen = info, time.time()
            if agent.job is not None:
                return None
            for job_id in self._queue:
                job = self._jobs[job_id]
                if job.owner != owner:
                    continue
                preferred = self._agents.get((owner, job.prefer)) if job.prefer else None
                if preferred is not None and preferred.online and preferred.name != name:
                    continue  # its data/containers are on another online machine
                self._queue.remove(job_id)
                job.agent, agent.job = name, job_id
                return {"id": job.id, "app": job.app}
        return None

    def job_for(self, owner: str, job_id: str, name: str | None = None) -> Job | None:
        """The job if it belongs to this owner (and, when given, is assigned to this agent); heartbeats."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.owner != owner or (name is not None and job.agent != name):
                return None
            agent = self._agents.get((owner, job.agent or ""))
            if agent:
                agent.last_seen = time.time()
            return job

    def finish(self, job: Job, result: dict) -> None:
        with self._lock:
            agent = self._agents.get((job.owner, job.agent or ""))
            if agent and agent.job == job.id:
                agent.job = None
        job.result = result
        job.done.set()


def _skip_heavy(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = Path(info.name).parts
    if any(p in ARCHIVE_SKIP for p in parts) or info.issym() or info.islnk():
        return None
    return info


hub = AgentHub()
