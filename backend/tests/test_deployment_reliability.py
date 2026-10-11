"""Lifecycle regressions; all infrastructure commands are mocked."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import accounts, deployer, fleet, job_store, main, nodes, storage
from app.schemas import PipelineEvent


@pytest.mark.parametrize(
    "responses,expected,paths",
    [
        ([200], True, ["/health"]),
        ([204], True, ["/health"]),
        ([404, 200], True, ["/health", "/"]),
        ([405, 204], True, ["/health", "/"]),
        ([404, 404], False, ["/health", "/"]),
        ([404, 401], False, ["/health", "/"]),
        ([401], False, ["/health"]),
        ([403], False, ["/health"]),
        ([500], False, ["/health"]),
        ([None], False, ["/health"]),
    ],
)
def test_readiness_requires_success_and_only_missing_route_falls_back(
    monkeypatch, responses, expected, paths
):
    calls = []
    values = iter(responses)

    def probe(url):
        calls.append(url.removeprefix("http://app"))
        status = next(values)
        return status, f"HTTP {status}"

    monkeypatch.setattr(deployer, "http_status", probe)
    ok, _ = deployer.wait_up("http://app", "/health", 10, is_dead=lambda: True)
    assert ok is expected
    assert calls == paths


def test_fleet_never_accepts_404_as_ready(monkeypatch):
    import urllib.error

    def missing(*args, **kwargs):
        raise urllib.error.HTTPError("http://app", 404, "missing", {}, None)

    monkeypatch.setattr(fleet.urllib.request, "urlopen", missing)
    monkeypatch.setattr(fleet, "READY_TRIES", 1)
    monkeypatch.setattr(fleet, "READY_INTERVAL", 0)
    assert not fleet._wait_ready(fleet.Replica("n", "c", "host", 8000))


@pytest.mark.parametrize("rc,stderr,expected", [(0, "", ["deleted function demo"]), (1, "NOT_FOUND", [])])
def test_stop_function_uses_saved_project_and_region(monkeypatch, rc, stderr, expected):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, rc, "", stderr)

    monkeypatch.setattr(deployer.subprocess, "run", run)
    assert (
        deployer.teardown("function", {"function": "demo", "project": "old-p", "region": "old-r"}) == expected
    )
    assert calls[0][0] == [
        "gcloud",
        "functions",
        "delete",
        "demo",
        "--project",
        "old-p",
        "--region",
        "old-r",
        "--quiet",
    ]
    assert calls[0][1]["timeout"] == 300


def test_function_delete_permission_failure_is_not_success(monkeypatch):
    monkeypatch.setattr(
        deployer.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "PERMISSION_DENIED"),
    )
    with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
        deployer.teardown("function", {"function": "demo"})


def test_concurrent_registration_keeps_both_apps_and_routes(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet, "STATE_FILE", tmp_path / "fleet.json")
    reg = nodes.Registry(router=nodes.Router("router", "192.0.2.1"))
    monkeypatch.setattr(nodes, "load_registry", lambda: reg)
    inside_first, release_first, second_started, second_read = (threading.Event() for _ in range(4))
    original_load = fleet.load_state
    routes = []

    def load():
        if threading.current_thread().name == "second":
            second_read.set()
        return original_load()

    def push(reg, state):
        routes.append(set(state.apps))
        if set(state.apps) == {"first"}:
            inside_first.set()
            assert release_first.wait(5)

    def register(name):
        threading.current_thread().name = name
        if name == "second":
            second_started.set()
        return fleet.register(name, f"{name}:v1", 8080, fleet.Replica("n", name, "192.0.2.2", 8000))

    monkeypatch.setattr(fleet, "load_state", load)
    monkeypatch.setattr(fleet, "push_config", push)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(register, "first")
        assert inside_first.wait(5)
        second = pool.submit(register, "second")
        assert second_started.wait(5)
        try:
            assert not second_read.wait(0.1)  # cannot even read a stale snapshot while the first writes
        finally:
            release_first.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert set(original_load().apps) == {"first", "second"}
    assert routes[-1] == {"first", "second"}


def test_file_lock_also_serializes_another_process(tmp_path):
    lock, marker = tmp_path / "state.lock", tmp_path / "entered"
    script = """
import sys
from pathlib import Path
from app.storage import file_lock
print('ready', flush=True)
with file_lock(Path(sys.argv[1])):
    Path(sys.argv[2]).write_text('entered')
"""
    # Reentrancy is needed when a public operation invokes another operation.
    with storage.file_lock(lock), storage.file_lock(lock):
        child = subprocess.Popen(
            [sys.executable, "-c", script, str(lock), str(marker)], stdout=subprocess.PIPE, text=True
        )
        try:
            assert child.stdout.readline().strip() == "ready"
            with pytest.raises(subprocess.TimeoutExpired):
                child.wait(timeout=0.1)
            assert not marker.exists()
        except BaseException:
            child.kill()
            child.wait()
            raise
    try:
        assert child.wait(timeout=5) == 0
        assert marker.read_text() == "entered"
    finally:
        child.stdout.close()


def test_atomic_write_failure_preserves_previous_state(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    storage.write_json(path, {"old": True})

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError):
        storage.write_json(path, {"new": True})
    assert json.loads(path.read_text()) == {"old": True}
    assert not list(tmp_path.glob(".state.json-*"))
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.fixture
def registry(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(main, "_jobs", {})
    monkeypatch.setattr(main, "_projects", {})
    return tmp_path


def test_restart_restores_verified_resource_and_marks_unknown_target_failed(registry):
    src = registry / "workspace" / "demo-local"
    src.mkdir(parents=True)
    deployer._write_state(
        src,
        deployer.Outcome(
            target="local", ok=True, url="http://app", handles={"container_name": "verified-container"}
        ),
    )
    job = main.Job(
        deployment_id="restart",
        app_name="demo",
        owner="alice",
        state="running",
        targets={"local": {"success": None}, "cloudrun": {"success": None}},
        target_dirs={"local": str(src)},
        env_values={"API_KEY": "never-journal-this"},
    )
    main._checkpoint_job(job)
    main._projects["demo"] = {
        "last_deployment_id": "restart",
        "last_status": "running",
        "owner": "alice",
        "live": {"cloudrun": {"service": "previous"}},
        "urls": {"cloudrun": "http://previous"},
    }
    main._restore_jobs()
    restored = main._jobs["restart"]
    assert restored.completed and restored.state == "partial_failure"
    assert restored.targets["local"]["success"] is True
    assert restored.targets["cloudrun"]["success"] is False
    assert "not replayed" in restored.error
    assert main._projects["demo"]["live"] == {
        "local": {"container_name": "verified-container"},
        "cloudrun": {"service": "previous"},
    }
    assert main._projects["demo"]["urls"]["cloudrun"] == "http://previous"
    assert "never-journal-this" not in (main._job_directory() / "restart.json").read_text()

    async def check_owner_and_sse():
        app = main.create_app()
        status = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/deploy/{deployment_id}")
        events = next(
            r.endpoint for r in app.routes if getattr(r, "path", None) == "/deploy/{deployment_id}/events"
        )
        alice = SimpleNamespace(state=SimpleNamespace(user=accounts.User("alice")))
        bob = SimpleNamespace(state=SimpleNamespace(user=accounts.User("bob")))
        assert (await status("restart", alice))["completed"]
        with pytest.raises(HTTPException) as denied:
            await status("restart", bob)
        assert denied.value.status_code == 404
        response = await events("restart", alice)
        stream = [event async for event in response.body_iterator]
        assert {event["event"] for event in stream} == {"done", "error"}

    asyncio.run(check_owner_and_sse())
    # Recovery is stable across another restart and does not turn unknown into success.
    main._jobs.clear()
    main._restore_jobs()
    assert main._jobs["restart"].state == "partial_failure"


def test_completed_history_never_resurrects_a_stopped_app(registry):
    job = main.Job(
        deployment_id="done",
        app_name="demo",
        state="completed",
        completed=True,
        targets={"local": {"success": True, "url": "http://old"}},
        live={"local": {"container_name": "old"}},
    )
    main._checkpoint_job(job)
    main._projects["demo"] = {"last_deployment_id": "done", "last_status": "stopped", "live": {}}
    main._restore_jobs()
    assert main._jobs["done"].completed
    assert main._projects["demo"]["last_status"] == "stopped"
    assert main._projects["demo"]["live"] == {}


def test_old_in_memory_running_project_is_not_left_running_after_upgrade(registry):
    main._projects["demo"] = {"last_deployment_id": "legacy", "last_status": "running", "targets": ["local"]}
    main._restore_jobs()
    assert main._jobs["legacy"].completed and main._jobs["legacy"].state == "failed"
    assert main._projects["demo"]["last_status"] == "failed"


def test_completed_target_handles_survive_deleted_workspace(registry):
    job = main.Job(
        deployment_id="cloud",
        app_name="demo",
        state="running",
        targets={"cloudrun": {"success": True, "url": "http://cloud"}},
        live={"cloudrun": {"service": "demo"}},
        target_dirs={"cloudrun": str(registry / "gone")},
    )
    main._checkpoint_job(job)
    main._restore_jobs()
    assert main._jobs["cloud"].state == "completed"
    assert main._jobs["cloud"].live["cloudrun"]["service"] == "demo"


def test_corrupt_journal_does_not_silently_drop_jobs(registry):
    directory = main._job_directory()
    directory.mkdir()
    (directory / "bad.json").write_text("{")
    with pytest.raises(ValueError):
        main._restore_jobs()


def test_journal_failure_rejects_deployment_before_starting_work(registry, monkeypatch):
    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(job_store, "save", fail)

    async def launch():
        app = main.create_app()
        with pytest.raises(HTTPException) as denied:
            app.state.launch("sample-apps/healthy", ["local"])
        assert denied.value.status_code == 503
        assert not main._jobs

    asyncio.run(launch())


def test_publish_checkpoints_terminal_job_without_raw_events(registry):
    job = main.Job(deployment_id="final", app_name="demo", state="completed")
    main._publish(
        job, PipelineEvent(type="done", payload={"target": "local", "url": "http://app", "log": "private"})
    )
    main._publish(job, None)
    saved = dict(job_store.load(main._job_directory()))["final"]
    assert saved["completed"] and saved["targets"]["local"]["success"]
    assert "private" not in json.dumps(saved)
