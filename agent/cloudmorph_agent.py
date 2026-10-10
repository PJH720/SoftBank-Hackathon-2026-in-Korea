#!/usr/bin/env python3
"""CloudMorph on-premises agent: deploy CloudMorph jobs onto *this* machine's Docker.

    python3 cloudmorph_agent.py --server https://cloudmorph.example --token <your API token>

Only Python 3.9+ and Docker are needed (no pip install). cloudflared is optional: with it, each app gets
a public https://*.trycloudflare.com URL; without it (or with --private) the app is reachable only from
this machine at http://127.0.0.1:<port>.

How it works: the agent polls the server over outbound HTTPS (no public IP, no open port needed), takes
deployments you started with the "agent" target, downloads the source, builds and runs it here, checks
it answers, and reports the result. If it fails, the server's healer patches the Dockerfile and sends it
back here. It only ever receives jobs of the user who owns the token.

Containers keep running after the agent stops (label cloudmorph.app=<name>); public tunnels stop with it.
Remove everything: docker rm -f $(docker ps -aq --filter label=cloudmorph.app)
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 8080  # container contract: the app listens on 0.0.0.0:$PORT
VERIFY_SECONDS = 90
BUILD_SECONDS = 1800
TAIL = 6000


class Fail(Exception):
    def __init__(
        self, stage: str, kind: str, message: str, logs: str = "", diagnosis: str = ""
    ):
        super().__init__(message)
        self.stage, self.kind, self.message, self.logs, self.diagnosis = (
            stage,
            kind,
            message,
            logs,
            diagnosis,
        )

    def stderr(self) -> str:
        """Same layout as the server's deployer (header, diagnosis, error, runtime logs) for healer."""
        out = f"[deployer] stage={self.stage} kind={self.kind} target=agent\n"
        if self.diagnosis:
            out += self.diagnosis + "\n"
        out += f"--- error ---\n{self.message[-TAIL:]}"
        if self.logs:
            out += f"\n--- runtime logs ---\n{self.logs[-TAIL:]}"
        return out


class Server:
    def __init__(self, base: str, token: str):
        self.base, self.token = base.rstrip("/"), token

    def call(
        self, method: str, path: str, body: dict | None = None, timeout: float = 40
    ):
        req = urllib.request.Request(
            self.base + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "X-API-Token": self.token,
                "Content-Type": "application/json",
                "User-Agent": "cloudmorph-agent",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return (
                json.loads(raw)
                if raw and r.headers.get_content_type() == "application/json"
                else raw
            )

    def download(self, path: str, dest: Path) -> None:
        req = urllib.request.Request(
            self.base + path, headers={"X-API-Token": self.token}
        )
        with urllib.request.urlopen(req, timeout=300) as r, dest.open("wb") as f:
            shutil.copyfileobj(r, f)


class LogPump:
    """Buffers progress lines and posts them every 2 s. The post doubles as the heartbeat during long builds."""

    def __init__(self, server: Server, job_id: str):
        self.server, self.job_id = server, job_id
        self.lines: list[str] = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def __call__(self, line: str) -> None:
        print(f"  {line}", flush=True)
        with self.lock:
            self.lines.append(line[:500])

    def _flush(self) -> None:
        with self.lock:
            lines, self.lines = self.lines[:200], self.lines[200:]
        try:
            self.server.call(
                "POST", f"/agent/jobs/{self.job_id}/log", {"lines": lines}, timeout=15
            )
        except (urllib.error.URLError, OSError):
            pass  # transient; the next flush retries the heartbeat

    def _run(self) -> None:
        while not self.stop.wait(2):
            self._flush()

    def close(self) -> None:
        self.stop.set()
        self.thread.join(5)
        self._flush()


def run(cmd: list[str], log, timeout: float, stage: str, kind: str) -> str:
    """Run a command, streaming its output to the log. Returns the output; raises Fail on error."""
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    out: list[str] = []
    deadline = time.time() + timeout

    def reader():
        for line in p.stdout:
            out.append(line)
            if line.strip():
                log(line.rstrip())

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    while p.poll() is None:
        if time.time() > deadline:
            p.kill()
            raise Fail(
                stage,
                "timeout",
                f"'{' '.join(cmd[:2])}' exceeded {int(timeout)}s\n"
                + "".join(out)[-TAIL:],
            )
        time.sleep(0.5)
    t.join(5)
    text = "".join(out)
    if p.returncode != 0:
        raise Fail(stage, kind, text or f"{cmd[0]} exited {p.returncode}")
    return text


_LISTENING = re.compile(
    r"Listening at:|listening on|Running on http|Server running|started server on",
    re.IGNORECASE,
)
_CRASHED = re.compile(
    r"Traceback \(most recent call last\)|Error:|Exception|Worker failed to boot|exited with code",
    re.IGNORECASE,
)


def diagnose(why: str, logs: str, exited: bool) -> str:
    """Same one-line diagnosis as deployer.diagnose_not_reachable (healer keys its port rule on it)."""
    if exited:
        return f"container exited during startup ({why}); see runtime logs for the traceback"
    if "HTTP 5" in why:
        return f"app is listening on PORT={PORT} but answered an error ({why}); see runtime logs"
    if _LISTENING.search(logs) and not _CRASHED.search(logs):
        return (
            f"container started but failed to start and listen on the port defined by PORT={PORT} "
            f"({why}); the app is probably bound to another port or to 127.0.0.1"
        )
    return f"container did not answer on PORT={PORT} ({why}); see runtime logs"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_ok(url: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status < 500, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return e.code < 500, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError) as e:
        return False, str(getattr(e, "reason", e))


def container_running(name: str) -> bool:
    cp = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return cp.stdout.strip() == "true"


def container_logs(name: str) -> str:
    cp = subprocess.run(
        ["docker", "logs", "--tail", "200", name],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    return cp.stdout + cp.stderr


def safe_extract(archive: Path, dest: Path) -> None:
    with tarfile.open(archive) as tar:
        for m in tar.getmembers():
            target = (dest / m.name).resolve()
            if (
                not target.is_relative_to(dest.resolve())
                or m.issym()
                or m.islnk()
                or m.isdev()
            ):
                raise Fail(
                    "preflight",
                    "internal_error",
                    f"unsafe path in source archive: {m.name}",
                )
        tar.extractall(dest)


def start_tunnel(port: int, log) -> tuple[subprocess.Popen | None, str | None]:
    exe = shutil.which("cloudflared")
    if not exe:
        return None, None
    p = subprocess.Popen(
        [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    found: list[str] = []

    def reader():
        for line in p.stderr:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and not found:
                found.append(m.group(0))

    threading.Thread(target=reader, daemon=True).start()
    for _ in range(60):
        if found or p.poll() is not None:
            break
        time.sleep(0.5)
    if not found:
        p.kill()
        log(
            "tunnel: cloudflared did not return a URL; the app stays private on this machine"
        )
        return None, None
    return p, found[0]


class Agent:
    def __init__(self, server: Server, name: str, public: bool, workdir: Path):
        self.server, self.name, self.public, self.workdir = (
            server,
            name,
            public,
            workdir,
        )
        self.apps: dict[str, dict] = {}  # app -> {"container", "tunnel": Popen|None}

    def info(self) -> dict:
        docker = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "os": f"{platform.system()} {platform.release()}",
            "arch": platform.machine(),
            "cpus": os.cpu_count(),
            "docker": docker.stdout.strip() or None,
            "public": self.public and bool(shutil.which("cloudflared")),
            "apps": sorted(self.apps),
        }

    def deploy(self, job: dict, log) -> dict:
        app, started = job["app"], time.time()
        safe_app = re.sub(r"[^a-z0-9-]", "-", app.lower())[:40] or "app"
        tag = time.strftime("%Y%m%d-%H%M%S")
        src = self.workdir / safe_app / tag
        src.mkdir(parents=True, exist_ok=True)
        archive = src.with_suffix(".tar.gz")
        image, cname, port = (
            f"cloudmorph-{safe_app}:{tag}",
            f"cloudmorph-{safe_app}-{tag}",
            free_port(),
        )
        try:
            log(f"agent {self.name}: downloading source")
            self.server.download(f"/agent/jobs/{job['id']}/source", archive)
            safe_extract(archive, src)
            log(f"agent {self.name}: docker build {image}")
            run(
                ["docker", "build", "-t", image, str(src)],
                log,
                BUILD_SECONDS,
                "build",
                "build_error",
            )
            log(f"agent {self.name}: docker run on 127.0.0.1:{port}")
            run(
                ["docker", "run", "-d", "--name", cname, "--label", f"cloudmorph.app={safe_app}",
                 "-e", f"PORT={PORT}", "-p", f"127.0.0.1:{port}:{PORT}", image],
                log, 120, "deploy", "deploy_error",
            )  # fmt: skip
            local = f"http://127.0.0.1:{port}"
            ok, why = False, "not checked"
            for _ in range(VERIFY_SECONDS // 2):
                if not container_running(cname):
                    why = "container exited"
                    break
                ok, why = http_ok(local + "/health")
                if not ok:
                    ok, why = http_ok(local + "/")
                if ok:
                    break
                time.sleep(2)
            if not ok:
                exited = not container_running(cname)
                logs = container_logs(cname)
                subprocess.run(
                    ["docker", "rm", "-f", cname], capture_output=True, check=False
                )
                raise Fail(
                    "verify",
                    "runtime_error",
                    f"app on {local} did not answer: {why}",
                    logs,
                    diagnose(why, logs, exited),
                )
            url, tunnel = local, None
            if self.public:
                tunnel, public = start_tunnel(port, log)
                url = public or local
            # the new version answers: retire the previous one of this app (blue/green on this machine)
            old = self.apps.get(app)
            if old:
                if old.get("tunnel"):
                    old["tunnel"].terminate()
                subprocess.run(
                    ["docker", "rm", "-f", old["container"]],
                    capture_output=True,
                    check=False,
                )
            self.apps[app] = {"container": cname, "tunnel": tunnel}
            log(f"agent {self.name}: {app} is up at {url}")
            return {
                "success": True,
                "url": url,
                "duration_sec": time.time() - started,
                "stdout": f"local {local}",
            }
        except Fail as e:
            return {
                "success": False,
                "stderr": e.stderr(),
                "duration_sec": time.time() - started,
            }
        except Exception as e:  # noqa: BLE001 — report instead of dying; the agent keeps serving
            return {
                "success": False,
                "stderr": Fail(
                    "deploy", "internal_error", f"{type(e).__name__}: {e}"
                ).stderr(),
                "duration_sec": time.time() - started,
            }
        finally:
            archive.unlink(missing_ok=True)
            shutil.rmtree(src, ignore_errors=True)

    def serve(self) -> None:
        print(
            f"CloudMorph agent '{self.name}' -> {self.server.base}  (public URLs: {self.info()['public']})",
            flush=True,
        )
        backoff = 1
        while True:
            try:
                r = self.server.call(
                    "POST",
                    "/agent/poll",
                    {"name": self.name, "info": self.info()},
                    timeout=40,
                )
                backoff = 1
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    sys.exit(
                        "server rejected the token (401). Use your CloudMorph API token."
                    )
                print(f"poll failed: HTTP {e.code}; retrying", flush=True)
                time.sleep(min(backoff, 30))
                backoff *= 2
                continue
            except (urllib.error.URLError, OSError) as e:
                print(f"server unreachable ({e}); retrying", flush=True)
                time.sleep(min(backoff, 30))
                backoff *= 2
                continue
            job = (r or {}).get("job") if isinstance(r, dict) else None
            if not job:
                continue
            print(f"job {job['id']}: deploy {job['app']}", flush=True)
            log = LogPump(self.server, job["id"])
            try:
                result = self.deploy(job, log)
            finally:
                log.close()
            try:
                self.server.call(
                    "POST", f"/agent/jobs/{job['id']}/result", result, timeout=30
                )
            except (urllib.error.URLError, OSError) as e:
                print(f"could not report result: {e}", flush=True)
            print(
                f"job {job['id']}: {'ok ' + result.get('url', '') if result['success'] else 'failed'}",
                flush=True,
            )

    def shutdown(self, *_):
        for a in self.apps.values():
            if a.get("tunnel"):
                a["tunnel"].terminate()
        print("\nagent stopped (containers keep running; public tunnels closed)")
        sys.exit(0)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="CloudMorph on-premises agent")
    ap.add_argument(
        "--server", default=os.getenv("CLOUDMORPH_SERVER"), help="CloudMorph URL"
    )
    ap.add_argument(
        "--token",
        default=os.getenv("CLOUDMORPH_TOKEN"),
        help="your CloudMorph API token",
    )
    ap.add_argument(
        "--name",
        default=socket.gethostname().split(".")[0],
        help="agent name shown in the dashboard",
    )
    ap.add_argument(
        "--private",
        action="store_true",
        help="no public tunnel: apps only on http://127.0.0.1",
    )
    ap.add_argument("--workdir", default=str(Path.home() / ".cloudmorph-agent"))
    a = ap.parse_args(argv)
    if not a.server or not a.token:
        ap.error(
            "--server and --token are required (or CLOUDMORPH_SERVER / CLOUDMORPH_TOKEN)"
        )
    if (
        subprocess.run(["docker", "info"], capture_output=True, check=False).returncode
        != 0
    ):
        sys.exit("Docker is not running on this machine. Start Docker and try again.")
    name = re.sub(r"[^A-Za-z0-9_.-]", "-", a.name)[:40] or "agent"
    agent = Agent(Server(a.server, a.token), name, not a.private, Path(a.workdir))
    signal.signal(signal.SIGINT, agent.shutdown)
    signal.signal(signal.SIGTERM, agent.shutdown)
    agent.serve()


if __name__ == "__main__":
    main()
