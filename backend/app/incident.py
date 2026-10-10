"""Incident response (owner: 이요환): watch every live app, let Jev judge what broke, and fix it fast.

    every GUARD_INTERVAL s: probe each live target (HTTP + container state)
        -> something wrong? open an incident
        -> Jev, one call (~0.5 s hosted, ~1.3 s on the local clef-flash): how bad, likely cause, first action
        -> guarded action: restart · start_database · rollback · failover · scale_out · wait
        -> probe until healthy (resolved), or give up after MAX_ATTEMPTS (escalated: a person has to look)

Jev is TypeSafe's hosted decision model (TYPESAFE_API_KEY). Without the key the local clef-flash answers the
same System One request; without either, a few plain rules keep the obvious cases (a stopped container, a
stopped database) covered. Tuned on six labeled incidents: cause 6/6, action 6/6.

A drill (Guard.drill) breaks a live app on purpose — kill its container, stop its database, or pretend its
platform is down — and the guard has to notice and recover on its own.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from app import systemone

log = logging.getLogger("incident")

MAX_ATTEMPTS = 3
SLOW_MS = 3000
DRILL_OUTAGE_S = 90  # how long a pretend platform outage lasts

TARGET_KO = {
    "local": "내 컴퓨터",
    "cloudrun": "Cloud Run",
    "node": "VM 노드",
    "function": "Cloud Run functions",
}
CAUSES = {  # option -> (shown to Jev, what people read)
    "app_crashed": ("the app process crashed or exited", "앱이 멈췄어요"),
    "bad_release": ("a new version started failing right after a deploy", "새 버전에 문제가 있어요"),
    "database_down": ("its database is stopped or unreachable", "DB가 꺼졌어요"),
    "overloaded": ("too much traffic or resources exhausted (slow, timeouts)", "요청이 너무 많아요"),
    "network": (
        "the public link or tunnel broke while the app itself is fine",
        "인터넷 연결 통로가 끊겼어요",
    ),
    "platform_outage": ("the hosting platform or region itself is down", "클라우드 자체에 장애가 났어요"),
}
ACTIONS = {  # option -> (shown to Jev, what we are about to do, what we did, what we could not do)
    "restart": ("restart the app", "앱을 다시 켤게요", "앱을 다시 켰어요", "앱을 다시 켜지 못했어요"),
    "start_database": (
        "start the stopped database",
        "DB를 다시 켤게요",
        "DB를 다시 켰어요",
        "DB를 켜지 못했어요",
    ),
    "rollback": (
        "go back to the previous version",
        "이전 버전으로 되돌릴게요",
        "이전 버전으로 되돌렸어요",
        "이전 버전으로 되돌리지 못했어요",
    ),
    "failover": (
        "run the app on another platform (local <-> cloud)",
        "다른 곳에 옮겨 띄울게요",
        "다른 곳에 옮겨 띄웠어요",
        "다른 곳에 옮기지 못했어요",
    ),
    "scale_out": (
        "add another copy of the app",
        "앱을 하나 더 띄울게요",
        "앱을 하나 더 띄웠어요",
        "앱을 더 띄우지 못했어요",
    ),
    "wait": ("watch a little longer", "조금 더 지켜볼게요", "지켜봤어요", "지켜보지 못했어요"),
}
SEVERITY = ["fine", "slow", "some errors", "down", "whole platform down"]
SEVERITY_KO = ["괜찮아요", "느려요", "일부 오류", "서비스 중단", "재해 (플랫폼 전체 장애)"]


@dataclass
class Probe:
    app: str
    target: str
    ok: bool
    url: str | None = None
    http: int | None = None
    ms: float | None = None
    error: str | None = None
    container: str | None = None  # docker state of the app container (local)
    exit_code: int | None = None
    db: str | None = None  # docker state of the database sidecar (local)
    body: str | None = None
    at: float = field(default_factory=time.time)

    def problem(self) -> str:
        """What is wrong, in a sentence a second-year student gets."""
        if self.error and self.error.startswith("platform outage"):
            return f"{TARGET_KO.get(self.target, self.target)} 쪽에 연결이 안 돼요 (장애 훈련)"
        if self.container and self.container != "running":
            return f"앱 컨테이너가 꺼졌어요 (상태 {self.container}, 종료 코드 {self.exit_code})"
        if self.db and self.db != "running":
            return f"DB가 꺼져 있어요 (상태 {self.db})"
        if self.http is None:
            return f"주소에 접속이 안 돼요 ({self.error or '응답 없음'})"
        if self.http >= 500:
            return f"앱이 오류를 돌려줘요 (HTTP {self.http})"
        return f"응답이 느려요 ({self.ms:.0f}ms)"


@dataclass
class Incident:
    id: str
    app: str
    target: str
    opened: float
    probe: dict
    status: str = "open"  # open -> acting -> resolved | escalated
    verdicts: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)
    resolved: float | None = None
    drill: str | None = None

    def say(self, text: str, kind: str = "info") -> None:
        self.timeline.append({"at": time.time(), "text": text, "kind": kind})


# --------------------------------------------------------------------------- #
# probes
# --------------------------------------------------------------------------- #
def _docker_state(name: str | None) -> tuple[str | None, int | None]:
    if not name:
        return None, None
    try:
        cp = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}", name],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", None
    if cp.returncode != 0:
        return "missing", None
    status, code = (cp.stdout.split() + ["0"])[:2]
    return status, int(code) if code.lstrip("-").isdigit() else None


def _http(url: str, timeout: float = 2.5) -> tuple[int | None, float, str | None, str | None]:
    """(status, ms, error, body excerpt) for GET url/health, falling back to url/ on 404."""
    t0 = time.perf_counter()
    last: tuple = (None, 0.0, "not tried", None)
    for path in ("/health", "/"):
        try:
            with urllib.request.urlopen(url.rstrip("/") + path, timeout=timeout) as r:
                return r.status, (time.perf_counter() - t0) * 1000, None, None
        except urllib.error.HTTPError as e:
            body = e.read()[:200].decode(errors="replace")
            last = (e.code, (time.perf_counter() - t0) * 1000, None, body)
            if e.code != 404:
                return last
        except Exception as e:  # noqa: BLE001 — refused / timeout / DNS all mean "no answer"
            return None, (time.perf_counter() - t0) * 1000, type(e).__name__, None
    return last


def docker_logs(name: str | None, tail: int = 12) -> list[str]:
    if not name:
        return []
    try:
        cp = subprocess.run(
            ["docker", "logs", "--tail", str(tail), name],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line[:200] for line in (cp.stdout + cp.stderr).splitlines()[-tail:]]


def probe_target(app: str, target: str, handles: dict, url: str | None) -> Probe:
    """One look at one live target. Local: container + database state and the local URL. Others: the public URL."""
    if target == "local":
        url = handles.get("local_url") or url
        container, code = _docker_state(handles.get("container_name"))
        db, _ = _docker_state(handles.get("db_container"))
    else:
        container = code = db = None
    http, ms, error, body = _http(url) if url else (None, 0.0, "no url", None)
    ok = (
        (container in (None, "running"))
        and (db in (None, "running"))
        and http is not None
        and http < 500
        and ms < SLOW_MS
    )
    return Probe(app, target, ok, url, http, round(ms), error, container, code, db, body)


# --------------------------------------------------------------------------- #
# decide (Jev)
# --------------------------------------------------------------------------- #
def engine_name(ep: systemone.Endpoint | None) -> str:
    if ep is None:
        return "규칙 (Jev 없음)"
    return "Jev" if ep.name == "Jev" else "clef-flash (로컬·Jev 호환)"


def decider() -> systemone.Endpoint | None:
    """Jev when TYPESAFE_API_KEY is set, else the local clef-flash (same API), else None (rules)."""
    ep = systemone.jev()
    if ep is not None:
        return ep
    ep = systemone.clef()
    return ep if systemone.ready(ep) else None


def allowed_actions(target: str, handles: dict, can_failover: bool) -> list[str]:
    actions = []
    if target in ("local", "node"):
        actions.append("restart")
    if target == "local" and handles.get("db_container"):
        actions.append("start_database")
    if target == "cloudrun" and handles.get("previous_revision"):
        actions.append("rollback")
    if can_failover:
        actions.append("failover")
    if target == "node":
        actions.append("scale_out")
    return [*actions, "wait"]


def rules(p: Probe, allowed: list[str]) -> tuple[str, str]:
    """Without any decision model: only the cases a rule cannot get wrong."""
    if p.container and p.container != "running" and "restart" in allowed:
        return "app_crashed", "restart"
    if p.db and p.db != "running" and "start_database" in allowed:
        return "database_down", "start_database"
    return "app_crashed", "wait"


def judge(p: Probe, logs: list[str], tried: list[str], allowed: list[str], context: dict) -> dict:
    """Ask Jev (or clef-flash) once. Returns the verdict the dashboard shows. Never raises."""
    ep = decider()
    if ep is None:
        cause, action = rules(p, allowed)
        return {"engine": "규칙 (Jev 없음)", "ms": 0, "cause": cause, "action": action, "severity": 3.0}
    # every input token costs ~2 ms on the local model: symptoms that are set, the last few log lines
    symptoms = {k: v for k, v in asdict(p).items() if v is not None and k not in ("app", "ok", "at", "url")}
    if "body" in symptoms:
        symptoms["body"] = systemone.redact(symptoms["body"])[:120]
    state = {
        "target": p.target,
        "symptoms": symptoms,
        # Jev is a hosted service: app logs can carry connection strings and tokens, so mask them first
        "recent_logs": [systemone.redact(line)[:120] for line in logs[-5:]],
        "already_tried": tried,
        **context,
    }
    questions = {
        "severity": systemone.score("How bad is it?", SEVERITY),
        "cause": systemone.choice("What is the most likely cause?", {k: v[0] for k, v in CAUSES.items()}),
        "action": systemone.choice("What should we do first?", {k: ACTIONS[k][0] for k in allowed}),
    }
    try:
        a = ep.ask(json.dumps(state, ensure_ascii=False)[:6000], questions, timeout=20)
    except systemone.SystemOneError as e:
        log.warning("decision model failed: %s", e)
        cause, action = rules(p, allowed)
        return {
            "engine": f"규칙 ({ep.name} 실패)",
            "ms": 0,
            "cause": cause,
            "action": action,
            "severity": 3.0,
        }
    severity, _ = a.score("severity")
    cause, cp, _ = a.choice("cause")
    action, ap, _ = a.choice("action")
    return {
        "engine": engine_name(ep),
        "ms": round(a.ms),
        "tokens": a.input_tokens,
        "severity": round(severity, 2),
        "severity_label": SEVERITY_KO[min(4, round(severity))],
        "cause": cause,
        "cause_p": round(cp[cause], 3),
        "action": action,
        "action_p": round(ap[action], 3),
        "actions": {k: round(v, 3) for k, v in ap.items()},
    }


# --------------------------------------------------------------------------- #
# the guard
# --------------------------------------------------------------------------- #
class Guard:
    """Watches projects in a background thread. Callbacks keep it decoupled from main.py:
    projects() -> [project dict]   busy(name) -> deployment in progress
    failover(project, target) -> deployment_id   job(deployment_id) -> {"completed", "targets"} | None"""

    def __init__(
        self,
        projects: Callable[[], list[dict]],
        busy: Callable[[str], bool],
        failover: Callable[[dict, str], str] | None = None,
        job: Callable[[str], dict | None] | None = None,
        interval: float | None = None,
        can_failover: Callable[[dict, str], str | None] | None = None,
        spawn: Callable | None = None,
    ):
        self.projects, self.busy, self.failover_fn, self.job = projects, busy, failover, job
        # each incident is handled in its own thread so one slow recovery never delays the next probe
        self.spawn = spawn or (lambda fn, *args: threading.Thread(target=fn, args=args, daemon=True).start())
        self.recover_wait = 25.0  # seconds to wait for health after an action
        self.can_failover = can_failover or (lambda project, target: None)
        self.interval = interval or float(os.environ.get("GUARD_INTERVAL", "3"))
        self.incidents: deque[Incident] = deque(maxlen=50)
        self.health: dict[str, dict[str, dict]] = {}
        self.faults: dict[tuple[str, str], float] = {}  # (app, target) -> pretend-outage deadline
        self.drills: dict[tuple[str, str], str] = {}  # (app, target) -> drill kind, until its incident opens
        self.muted: dict[tuple[str, str], float] = {}  # moved away after a failover: stop paging
        self._open: dict[tuple[str, str], Incident] = {}
        self._strikes: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.started: float | None = None

    # ---- loop ----
    def start(self) -> None:
        self.started = time.time()
        threading.Thread(target=self._run, daemon=True, name="incident-guard").start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:  # the guard must outlive any single bad round (logged)
                log.exception("guard round failed")

    def tick(self) -> None:
        """One round: probe every live target; open an incident for anything that stays wrong."""
        seen = set()
        for project in list(self.projects()):
            name = project.get("name")
            if not name:
                continue
            if project.get("last_status") == "running" or self.busy(name):  # a deploy is replacing it
                seen.update((name, t) for t in (project.get("live") or {}))
                continue
            # copies: main.py's event loop may move or stop the app while this thread looks at it
            for target, handles in list((project.get("live") or {}).items()):
                key = (name, target)
                seen.add(key)
                p = self._probe(project, target, handles or {})
                with self._lock:
                    self.health.setdefault(name, {})[target] = asdict(p)
                    if p.ok or key in self._open or self.muted.get(key, 0) > time.time():
                        self._strikes.pop(key, None)
                        continue
                    # a stopped container is certain; a failed HTTP check must repeat once (no flapping)
                    strikes = self._strikes[key] = self._strikes.get(key, 0) + 1
                    certain = (
                        p.container not in (None, "running")
                        or p.db not in (None, "running")
                        or self.faults.get(key, 0) > time.time()
                    )
                    if not certain and strikes < 2:
                        continue
                    incident = Incident(uuid.uuid4().hex[:10], name, target, time.time(), asdict(p))
                    incident.drill = self.drills.pop(key, None)
                    incident.say(
                        f"{name} 앱({TARGET_KO.get(target, target)})에 문제가 생겼어요: {p.problem()}",
                        "alert",
                    )
                    self._open[key] = incident
                    self.incidents.appendleft(incident)
                self.spawn(self._respond, incident, project, handles or {})
        with self._lock:
            for name in list(self.health):  # stopped apps leave the map
                self.health[name] = {t: v for t, v in self.health[name].items() if (name, t) in seen}
                if not self.health[name]:
                    del self.health[name]

    def _probe(self, project: dict, target: str, handles: dict) -> Probe:
        name = project["name"]
        if self.faults.get((name, target), 0) > time.time():
            return Probe(
                name,
                target,
                False,
                (project.get("urls") or {}).get(target),
                None,
                None,
                "platform outage (drill)",
            )
        return probe_target(name, target, handles, (project.get("urls") or {}).get(target))

    # ---- respond ----
    def _respond(self, incident: Incident, project: dict, handles: dict) -> None:
        key = (incident.app, incident.target)
        tried: list[str] = []
        destination = self.can_failover(project, incident.target)
        allowed = allowed_actions(incident.target, handles, destination is not None)
        context = {"deployed_seconds_ago": round(time.time() - project.get("updated_at", time.time()))}
        if handles.get("previous_revision"):
            context["previous_version"] = handles["previous_revision"]
        if destination:
            context["can_failover_to"] = destination
        try:
            for _ in range(MAX_ATTEMPTS):
                project, handles = self._fresh(project, incident.target, handles)
                p = self._probe(project, incident.target, handles)
                if p.ok:
                    break
                logs = docker_logs(handles.get("container_name")) if incident.target == "local" else []
                verdict = judge(p, logs, tried, allowed, context)
                incident.verdicts.append(verdict)
                action = verdict["action"]
                why = CAUSES[verdict["cause"]][1]
                if verdict["ms"]:
                    said = f"{verdict['engine']}가 {verdict['ms'] / 1000:.1f}초 만에 판단했어요"
                    said += f": {why} ({verdict['cause_p']:.0%})"
                else:
                    said = f"판단 모델이 없어 규칙으로 정했어요: {why}"
                incident.say(f"{said} → {ACTIONS[action][1]}", "decision")
                if verdict["cause"] == "platform_outage" and action != "failover":
                    # restarting on a platform that is down does nothing; say what would have helped
                    incident.status = "escalated"
                    incident.say(
                        "플랫폼 자체에 장애가 나서 다시 켜도 소용없어요. 옮길 곳(GCP_PROJECT_ID나 노드 풀)을 "
                        "설정해 두면 다음에는 자동으로 옮겨요.",
                        "error",
                    )
                    return
                incident.status = "acting"
                t0 = time.time()
                ok, detail = self._act(action, incident, project, handles, destination)
                incident.actions.append(
                    {"action": action, "ok": ok, "detail": detail, "seconds": round(time.time() - t0, 1)}
                )
                tried.append(action)
                if ok and action == "failover":
                    self.muted[key] = time.time() + 600
                    self._resolve(incident, f"{destination}에서 서비스를 이어가요. {detail}")
                    return
                if ok and action != "wait":
                    incident.say(
                        f"{ACTIONS[action][2]} ({time.time() - t0:.1f}초). {detail}".strip(), "action"
                    )
                elif not ok:
                    incident.say(f"{ACTIONS[action][3]}: {detail}", "error")
                project, handles = self._fresh(
                    project, incident.target, handles
                )  # a redeploy has new handles
                if self._recovered(project, incident.target, handles):
                    self._resolve(incident)
                    return
            project, handles = self._fresh(project, incident.target, handles)
            if self._probe(project, incident.target, handles).ok:
                self._resolve(incident)
                return
            incident.status = "escalated"
            incident.say(f"{MAX_ATTEMPTS}번 시도했지만 돌아오지 않았어요. 직접 확인해 주세요.", "error")
        finally:
            with self._lock:
                self._open.pop(key, None)
                self._strikes.pop(key, None)

    def _fresh(self, project: dict, target: str, handles: dict) -> tuple[dict, dict]:
        """The project as main.py has it now: a redeploy replaces the container (new handles)."""
        for p in list(self.projects()):
            if p.get("name") == project.get("name"):
                return p, dict((p.get("live") or {}).get(target) or handles)
        return project, handles

    def _recovered(self, project: dict, target: str, handles: dict) -> bool:
        deadline = time.time() + self.recover_wait
        while time.time() < deadline:
            if self._probe(project, target, handles).ok:
                return True
            time.sleep(1)
        return False

    def _resolve(self, incident: Incident, how: str | None = None) -> None:
        incident.status, incident.resolved = "resolved", time.time()
        took = incident.resolved - incident.opened
        incident.say(how or f"다시 정상이에요. 문제를 알아챈 뒤 {took:.1f}초 만에 돌아왔어요.", "resolved")

    def _act(self, action: str, incident: Incident, project: dict, handles: dict, destination: str | None):
        """(ok, detail). Each action is idempotent and bounded in time."""
        if action == "wait":
            time.sleep(self.interval)
            return True, ""
        if action == "restart" and incident.target == "local":
            name = handles.get("container_name")
            cp = subprocess.run(
                ["docker", "restart", "-t", "2", name],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            if cp.returncode != 0 and "No such container" in cp.stderr and self.failover_fn and self.job:
                return self._act("failover", incident, project, handles, "local")  # gone: build it again
            return cp.returncode == 0, (cp.stderr.strip()[-200:] if cp.returncode else "")
        if action == "start_database":
            db = handles.get("db_container")
            cp = subprocess.run(
                ["docker", "start", db], capture_output=True, text=True, check=False, timeout=60
            )
            if cp.returncode != 0:
                return False, cp.stderr.strip()[-200:]
            for _ in range(30):  # the app's own reconnect needs a ready database
                ready = subprocess.run(
                    ["docker", "exec", db, "pg_isready", "-q"], capture_output=True, check=False
                )
                if ready.returncode == 0:
                    return True, ""
                time.sleep(1)
            return False, "DB가 준비되지 않았어요"
        if action == "rollback":
            from app import deployer

            cfg = deployer.Config.from_env()
            cfg.project, cfg.region = (
                handles.get("project") or cfg.project,
                handles.get("region") or cfg.region,
            )
            ok = deployer.rollback_cloudrun(handles["service"], handles["previous_revision"], cfg)
            return (
                ok,
                f"{handles['previous_revision']}로 트래픽을 옮겼어요" if ok else "gcloud 명령이 실패했어요",
            )
        if action == "failover":
            if not (destination and self.failover_fn and self.job):
                return False, "옮길 곳이 설정돼 있지 않아요 (GCP_PROJECT_ID나 노드 풀이 필요해요)"
            deployment_id = self.failover_fn(project, destination)
            incident.say(
                f"{TARGET_KO.get(destination, destination)}에 새로 띄우는 중이에요 (배포 {deployment_id})",
                "action",
            )
            deadline = time.time() + 900
            while time.time() < deadline:
                status = self.job(deployment_id) or {}
                if status.get("completed"):
                    done = (status.get("targets") or {}).get(destination) or {}
                    return bool(done.get("success")), done.get("url") or ""
                time.sleep(2)
            return False, "15분 안에 끝나지 않았어요"
        if action == "scale_out" and incident.target == "node":
            from app import fleet

            app = fleet.load_state().apps.get(incident.app)
            if app is None:
                return False, "노드 풀에 없는 앱이에요"
            fleet.scale(incident.app, len(app.replicas) + 1)
            return True, ""
        if action == "restart" and incident.target == "node":
            from app import nodes as nodepool

            node = nodepool.load_registry().get(handles["node"])
            cp = nodepool.ssh(node, f"docker restart -t 2 {handles['container_name']}")
            return cp.returncode == 0, ""
        return False, f"{TARGET_KO.get(incident.target, incident.target)}에서는 할 수 없는 조치예요"

    # ---- drills ----
    def drill(self, project: dict, kind: str) -> dict:
        """Break a live target on purpose. crash: kill the app container. db_down: stop the database.
        outage: pretend the platform is unreachable for a while (nothing is touched)."""
        name = project["name"]
        live = project.get("live") or {}
        if kind in ("crash", "db_down"):
            handles = live.get("local") or {}
            victim = handles.get("container_name") if kind == "crash" else handles.get("db_container")
            if not victim:
                raise ValueError(
                    "이 훈련은 내 컴퓨터(local)에 떠 있는 앱에서만 할 수 있어요"
                    + (" (DB가 있는 앱)" if kind == "db_down" else "")
                )
            cmd = ["docker", "kill", victim] if kind == "crash" else ["docker", "stop", "-t", "1", victim]
            cp = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)
            if cp.returncode != 0:
                raise RuntimeError(cp.stderr.strip()[-200:])
            target = "local"
        elif kind == "outage":
            target = next(iter(live), None)
            if target is None:
                raise ValueError("떠 있는 곳이 없어요")
            self.faults[(name, target)] = time.time() + DRILL_OUTAGE_S
        else:
            raise ValueError(f"모르는 훈련이에요: {kind}")
        self.drills[(name, target)] = kind
        return {"app": name, "target": target, "kind": kind, "at": time.time()}

    # ---- for the dashboard ----
    def snapshot(self) -> dict:
        ep = decider()
        with self._lock:
            return {
                "guard": {
                    "running": self.started is not None and not self._stop.is_set(),
                    "interval": self.interval,
                    "engine": engine_name(ep),
                    "jev": systemone.jev() is not None,
                },
                "health": json.loads(json.dumps(self.health)),
                "incidents": [asdict(i) for i in self.incidents],
            }
