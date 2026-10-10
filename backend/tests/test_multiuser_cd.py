"""Several users on one server: who owns which app, and per-project push-to-deploy links."""

from __future__ import annotations

import hashlib
import hmac
import json
import stat

import pytest
from fastapi.testclient import TestClient

from app import accounts, main, webhooks

REMOTE = ("203.0.113.9", 4000)
ADMIN = {"X-API-Token": "admin-t"}
ALICE = {"X-API-Token": "alice-t"}
BOB = {"X-API-Token": "bob-t"}
REPO_URL = "https://github.com/Alice/Shop.git"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("CLOUDMORPH_API_TOKEN", "admin-t")
    monkeypatch.setenv("CLOUDMORPH_USERS", "alice:alice-t, bob:bob-t")
    monkeypatch.setenv("CLOUDMORPH_PUBLIC_URL", "https://cm.example")
    monkeypatch.delenv("CLOUDMORPH_USERS_FILE", raising=False)


@pytest.fixture
def real(env, monkeypatch):
    """Real _launch, but the source never resolves, so nothing is deployed."""

    def no_source(*a, **kw):
        raise ValueError("test: no source")

    monkeypatch.setattr(main, "prepare_source", no_source)
    return TestClient(main.create_app(), client=REMOTE)


@pytest.fixture
def fake(env):
    app = main.create_app()
    launched = []

    def launch(source, targets, **kw):
        launched.append({"source": source, "targets": targets, **kw})
        return f"dep{len(launched)}"

    app.state.launch = launch
    return TestClient(app, client=REMOTE), launched


def _project(name, owner, source=REPO_URL, targets=("cloudrun",), cd=None):
    main._projects[name] = {
        "name": name,
        "owner": owner,
        "source": source,
        "targets": list(targets),
        "history": [],
        "cd": cd,
    }


def _push(repo="Alice/Shop", branch="main", default="main"):
    return {
        "ref": f"refs/heads/{branch}",
        "after": "0123456789abcdef",
        "repository": {
            "full_name": repo,
            "clone_url": f"https://github.com/{repo}.git",
            "default_branch": default,
        },
        "head_commit": {"id": "0123456789abcdef", "message": "feat: x"},
        "pusher": {"name": "alice"},
    }


def _send(client, path, payload, secret, event="push"):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(path, content=body, headers={"X-GitHub-Event": event, "X-Hub-Signature-256": sig})


# ---------- accounts ----------


def test_identify_users_and_admin(env):
    assert accounts.identify("admin-t") == accounts.LOCAL_ADMIN
    assert accounts.identify("alice-t") == accounts.User("alice")
    assert accounts.identify("nope") is None and accounts.identify("") is None


def test_users_file_and_reserved_name(env, tmp_path, monkeypatch):
    f = tmp_path / "users.json"
    f.write_text(json.dumps({"carol": "carol-t", "admin": "sneaky"}))
    monkeypatch.setenv("CLOUDMORPH_USERS_FILE", str(f))
    assert accounts.identify("carol-t") == accounts.User("carol")
    assert accounts.identify("sneaky") is None  # nobody can register as admin through the user list


def test_whoami(real):
    assert real.get("/projects/me", headers=ALICE).json() == {"user": "alice", "admin": False}
    assert real.get("/projects/me", headers=ADMIN).json() == {"user": "admin", "admin": True}
    assert real.get("/projects/me", headers={"X-API-Token": "x"}).status_code == 401


# ---------- ownership ----------


def test_app_name_belongs_to_first_deployer(real):
    body = {"source": REPO_URL, "targets": ["local"], "name": "shop"}
    assert real.post("/deploy", json=body, headers=ALICE).status_code == 200
    r = real.post("/deploy", json=body, headers=BOB)
    assert r.status_code == 409 and "another user" in r.json()["detail"]
    assert real.post("/deploy", json=body, headers=ALICE).status_code == 200  # owner redeploys freely
    assert real.post("/deploy", json=body, headers=ADMIN).status_code == 409  # admin can't hijack either


def test_users_only_see_their_own_projects_and_jobs(real):
    dep = real.post(
        "/deploy", json={"source": REPO_URL, "targets": ["local"], "name": "shop"}, headers=ALICE
    ).json()["deployment_id"]
    _project("legacy", accounts.ADMIN)

    names = lambda h: {p["name"] for p in real.get("/projects", headers=h).json()["projects"]}
    assert names(ALICE) == {"shop"}
    assert names(BOB) == set()
    assert names(ADMIN) == {"shop", "legacy"}
    assert real.get(f"/deploy/{dep}", headers=BOB).status_code == 404
    assert real.get(f"/deploy/{dep}/events?token=bob-t").status_code == 404
    assert real.get(f"/deploy/{dep}", headers=ALICE).status_code == 200


# ---------- linking ----------


def test_manual_link_returns_secret_once(fake):
    client, _ = fake
    _project("shop", "alice")
    r = client.post("/projects/shop/cd", json={}, headers=ALICE)
    assert r.status_code == 200
    link = r.json()
    assert link["mode"] == "manual" and link["repo"] == "alice/shop" and len(link["secret"]) == 48
    assert link["url"] == f"https://cm.example/webhook/github/{link['id']}"

    listed = client.get("/projects", headers=ALICE).json()["projects"][0]["cd"]
    assert "secret" not in listed and listed["id"] == link["id"]
    assert client.post("/projects/shop/cd", json={}, headers=ALICE).status_code == 409


def test_link_rules(fake):
    client, _ = fake
    _project("shop", "alice")
    _project("zip", "alice", source="/srv/app.zip")
    assert client.post("/projects/shop/cd", json={}, headers=BOB).status_code == 404
    assert client.post("/projects/zip/cd", json={}, headers=ALICE).status_code == 422
    assert client.post("/projects/shop/cd", json={"branch": "a b"}, headers=ALICE).status_code == 422
    assert client.post("/projects/shop/cd", json={}).status_code == 401


def test_auto_link_registers_hook_and_never_keeps_token(fake, monkeypatch):
    client, _ = fake
    _project("shop", "alice")
    calls = []
    monkeypatch.setattr(
        webhooks,
        "create_github_hook",
        lambda repo, url, secret, token: calls.append((repo, url, token)) or 42,
    )
    r = client.post("/projects/shop/cd", json={"github_token": " ghp_x ", "branch": "release"}, headers=ALICE)
    assert r.status_code == 200
    out = r.json()
    assert out["mode"] == "auto" and out["hook_id"] == 42 and "secret" not in out
    assert calls == [("alice/shop", out["url"], "ghp_x")]
    assert "ghp_x" not in main.PROJECTS_FILE.read_text()
    assert stat.S_IMODE(main.PROJECTS_FILE.stat().st_mode) == 0o600


def test_auto_link_github_error_is_reported_and_nothing_saved(fake, monkeypatch):
    client, _ = fake
    _project("shop", "alice")

    def refuse(*a):
        raise webhooks.GitHubError("GitHub 404: no admin access to the repo")

    monkeypatch.setattr(webhooks, "create_github_hook", refuse)
    r = client.post("/projects/shop/cd", json={"github_token": "t"}, headers=ALICE)
    assert r.status_code == 502 and "admin access" in r.json()["detail"]
    assert main._projects["shop"]["cd"] is None


def test_unlink_deletes_github_hook_with_token(fake, monkeypatch):
    client, _ = fake
    _project("shop", "alice", cd={"id": "l1", "secret": "s", "repo": "alice/shop", "hook_id": 42})
    deleted = []
    monkeypatch.setattr(webhooks, "delete_github_hook", lambda repo, hid, token: deleted.append((repo, hid)))
    assert client.post("/projects/shop/cd/unlink", json={}, headers=BOB).status_code == 404
    r = client.post("/projects/shop/cd/unlink", json={"github_token": "t"}, headers=ALICE)
    assert r.json() == {"unlinked": True, "removed_on_github": True} and deleted == [("alice/shop", 42)]
    assert _send(client, "/webhook/github/l1", _push(), "s").status_code == 401  # pushes stop at once


# ---------- per-project webhook ----------

LINK = {"id": "link1", "secret": "s3", "repo": "alice/shop", "branch": None}


def test_link_webhook_redeploys_that_project_as_its_owner(fake):
    client, launched = fake
    _project("shop", "alice", targets=("cloudrun", "node"), cd=dict(LINK))
    assert _send(client, "/webhook/github/link1", {"zen": "hi"}, "s3", event="ping").json()["name"] == "shop"
    r = _send(client, "/webhook/github/link1", _push(), "s3")
    assert r.json()["deployment_id"] == "dep1"
    (call,) = launched
    assert call["name"] == "shop" and call["owner"] == "alice" and call["targets"] == ["cloudrun", "node"]
    assert call["source"] == "https://github.com/alice/shop.git" and call["ref"] == "main"
    assert main._projects["shop"]["cd"]["last_delivery"]["event"] == "push"


def test_link_webhook_rejects_wrong_secret_unknown_id_other_repo_and_branch(fake):
    client, launched = fake
    _project("shop", "alice", cd=dict(LINK))
    assert _send(client, "/webhook/github/link1", _push(), "wrong").status_code == 401
    assert _send(client, "/webhook/github/nope", _push(), "s3").status_code == 401
    assert "not linked" in _send(client, "/webhook/github/link1", _push(repo="eve/x"), "s3").json()["ignored"]
    assert "branch" in _send(client, "/webhook/github/link1", _push(branch="dev"), "s3").json()["ignored"]
    assert launched == []


def test_link_webhook_uses_chosen_branch(fake):
    client, launched = fake
    _project("shop", "alice", cd={**LINK, "branch": "release"})
    assert "ignored" in _send(client, "/webhook/github/link1", _push(branch="main"), "s3").json()
    assert _send(client, "/webhook/github/link1", _push(branch="release"), "s3").json()["ok"]
    assert launched[0]["ref"] == "release"


def test_another_users_secret_cannot_trigger_my_project(fake):
    client, launched = fake
    _project("shop", "alice", cd=dict(LINK))
    _project(
        "blog", "bob", source="https://github.com/bob/blog", cd={**LINK, "id": "link2", "secret": "bob-s"}
    )
    assert _send(client, "/webhook/github/link1", _push(), "bob-s").status_code == 401
    assert launched == []


# ---------- shared (legacy) webhook ----------


def test_shared_webhook_redeploys_unlinked_projects_of_that_repo(fake, monkeypatch):
    client, launched = fake
    monkeypatch.setenv("CLOUDMORPH_WEBHOOK_SECRET", "shared")
    _project("shop-alice", "alice")
    _project("shop-bob", "bob", source="https://github.com/alice/shop")
    _project("shop-linked", "alice", cd=dict(LINK))
    _project("other", "bob", source="https://github.com/bob/other")
    r = _send(client, "/webhook/github", _push(), "shared").json()
    assert {d["name"] for d in r["deployments"]} == {"shop-alice", "shop-bob"}
    assert {(c["name"], c["owner"]) for c in launched} == {("shop-alice", "alice"), ("shop-bob", "bob")}


def test_shared_webhook_fallback_never_takes_a_users_name(env, monkeypatch):
    monkeypatch.setenv("CLOUDMORPH_WEBHOOK_SECRET", "shared")
    monkeypatch.setenv("CLOUDMORPH_CD_TARGETS", "local")
    _project("shop", "bob", source="https://github.com/bob/unrelated")
    client = TestClient(main.create_app(), client=REMOTE)
    r = _send(client, "/webhook/github", _push(repo="acme/shop"), "shared").json()
    assert "another user" in r["ignored"]


def test_github_repo_normalization():
    assert webhooks.github_repo("https://github.com/Alice/Shop.git") == "alice/shop"
    assert webhooks.github_repo("https://github.com/alice/shop/") == "alice/shop"
    assert webhooks.github_repo("https://gitlab.com/alice/shop") is None
    assert webhooks.github_repo("sample-apps/guestbook") is None
    assert webhooks.github_repo(None) is None
