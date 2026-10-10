"""Target "agent": the hub queues per-user jobs; an agent (simulated over HTTP here) runs them."""

from __future__ import annotations

import io
import tarfile
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import agents, main
from app.agents import AgentHub

ALICE = {"X-API-Token": "alice-t"}
BOB = {"X-API-Token": "bob-t"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CLOUDMORPH_API_TOKEN", "admin-t")
    monkeypatch.setenv("CLOUDMORPH_USERS", "alice:alice-t,bob:bob-t")
    hub = AgentHub()
    monkeypatch.setattr(main, "agent_hub", hub)
    monkeypatch.setattr(agents, "PICKUP_SECONDS", 3)
    monkeypatch.setattr(agents, "POLL_SECONDS", 0.3)
    return TestClient(main.create_app(), client=("203.0.113.5", 1)), hub


@pytest.fixture
def src(tmp_path):
    (tmp_path / "app.py").write_text("print('hi')\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret remote\n")
    return tmp_path


def _dispatch(hub, owner, src, app="shop"):
    """Run hub.dispatch in a thread like the pipeline does; returns (thread, result box, log lines)."""
    box, lines = {}, []
    t = threading.Thread(
        target=lambda: box.setdefault("r", hub.dispatch(owner, app, str(src), "FROM x\n", lines.append))
    )
    t.start()
    return t, box, lines


def _poll_until_job(client, headers, name="pc", tries=20):
    for _ in range(tries):
        job = client.post(
            "/agent/poll", json={"name": name, "info": {"arch": "arm64"}}, headers=headers
        ).json()["job"]
        if job:
            return job
    return None


def test_no_agent_fails_fast_as_user_action(client, src):
    _, hub = client
    r = hub.dispatch("alice", "shop", str(src), "FROM x\n", lambda line: None)
    assert not r.success and "kind=user_action_required" in r.stderr and r.target == "agent"


def test_agent_runs_the_job_and_reports_back(client, src):
    c, hub = client
    c.post("/agent/poll", json={"name": "pc", "info": {}}, headers=ALICE)  # register
    t, box, lines = _dispatch(hub, "alice", src)
    job = _poll_until_job(c, ALICE)
    assert job and job["app"] == "shop"

    archive = c.get(f"/agent/jobs/{job['id']}/source", headers=ALICE)
    with tarfile.open(fileobj=io.BytesIO(archive.content)) as tar:
        names = tar.getnames()
    assert "./app.py" in names and "./Dockerfile" in names
    assert not any(".git" in n for n in names)  # repo metadata never leaves the server

    c.post(f"/agent/jobs/{job['id']}/log", json={"lines": ["building"]}, headers=ALICE)
    c.post(
        f"/agent/jobs/{job['id']}/result",
        json={"success": True, "url": "https://x.trycloudflare.com", "duration_sec": 3.0},
        headers=ALICE,
    )
    t.join(10)
    assert box["r"].success and box["r"].url == "https://x.trycloudflare.com"
    assert "building" in lines
    agent = c.get("/agent/status", headers=ALICE).json()["agents"][0]
    assert agent["name"] == "pc" and agent["online"] and not agent["busy"]


def test_other_users_agent_never_sees_the_job(client, src):
    c, hub = client
    c.post("/agent/poll", json={"name": "alice-pc", "info": {}}, headers=ALICE)
    t, box, _ = _dispatch(hub, "alice", src)
    time.sleep(0.2)
    assert c.post("/agent/poll", json={"name": "bob-pc", "info": {}}, headers=BOB).json()["job"] is None
    assert [a["name"] for a in c.get("/agent/status", headers=BOB).json()["agents"]] == ["bob-pc"]
    job = _poll_until_job(c, ALICE, "alice-pc")
    assert c.get(f"/agent/jobs/{job['id']}/source", headers=BOB).status_code == 404
    assert c.post(f"/agent/jobs/{job['id']}/result", json={"success": True}, headers=BOB).status_code == 404
    c.post(f"/agent/jobs/{job['id']}/result", json={"success": False, "stderr": "boom"}, headers=ALICE)
    t.join(10)
    assert not box["r"].success and box["r"].stderr == "boom"


def test_redeploy_goes_back_to_the_machine_that_runs_the_app(client, src):
    c, hub = client
    for name in ("pc-a", "pc-b"):
        c.post("/agent/poll", json={"name": name, "info": {}}, headers=ALICE)
    t, box, _ = _dispatch(hub, "alice", src)
    job = _poll_until_job(c, ALICE, "pc-a")
    c.post(f"/agent/jobs/{job['id']}/result", json={"success": True, "url": "u"}, headers=ALICE)
    t.join(10)

    t, box, _ = _dispatch(hub, "alice", src)
    time.sleep(0.2)
    assert c.post("/agent/poll", json={"name": "pc-b", "info": {}}, headers=ALICE).json()["job"] is None
    job = _poll_until_job(c, ALICE, "pc-a")
    assert job is not None
    c.post(f"/agent/jobs/{job['id']}/result", json={"success": True, "url": "u2"}, headers=ALICE)
    t.join(10)
    assert box["r"].url == "u2"


def test_unpicked_job_fails_instead_of_hanging(client, src, monkeypatch):
    _, hub = client
    hub.poll("alice", "pc", {})  # online, but it never polls again
    r = hub.dispatch("alice", "shop", str(src), "FROM x\n", lambda line: None)
    assert not r.success and "no agent picked up" in r.stderr


def test_agent_endpoints_need_a_token(client):
    c, _ = client
    assert c.post("/agent/poll", json={"name": "pc"}).status_code == 401
    assert c.get("/agent/script").status_code == 401
    script = c.get("/agent/script", headers=ALICE)
    assert script.status_code == 200 and "CloudMorph on-premises agent" in script.text
