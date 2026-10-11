"""The admin account (CLOUDMORPH_ADMIN_PASSWORD) and the /admin console routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main, users

REMOTE = ("203.0.113.9", 4000)
ADMIN_PW = "admin password 1"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDMORPH_ACCOUNTS_FILE", str(tmp_path / "accounts.json"))
    monkeypatch.setenv("CLOUDMORPH_ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.setenv("CLOUDMORPH_USERS", "carol:carol-t")
    monkeypatch.delenv("CLOUDMORPH_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDMORPH_USERS_FILE", raising=False)
    monkeypatch.delenv("CLOUDMORPH_SIGNUP_CODE", raising=False)
    monkeypatch.setattr(users, "SCRYPT", {"n": 2**10, "r": 8, "p": 1, "dklen": 32})  # fast in tests
    monkeypatch.setattr(main, "_projects", {})
    users._failures.clear()
    return monkeypatch


@pytest.fixture
def client(env):
    return TestClient(main.create_app(), client=REMOTE)


def _login(client, name, password):
    r = client.post("/auth/login", json={"username": name, "password": password})
    assert r.status_code == 200, r.text
    return {"X-API-Token": r.json()["token"]}


def _signup(client, name, password="correct horse"):
    r = client.post("/auth/signup", json={"username": name, "password": password})
    assert r.status_code == 200, r.text
    return {"X-API-Token": r.json()["token"]}


def test_admin_logs_in_with_the_env_password_and_is_admin(client):
    admin = _login(client, "admin", ADMIN_PW)
    assert client.get("/projects/me", headers=admin).json() == {"user": "admin", "admin": True}


def test_admin_sees_and_stops_every_users_apps(client):
    _signup(client, "alice")
    main._projects["shop"] = {"name": "shop", "owner": "alice", "source": "x", "targets": [], "live": {}}
    admin = _login(client, "admin", ADMIN_PW)
    assert [p["owner"] for p in client.get("/projects", headers=admin).json()["projects"]] == ["alice"]
    assert client.post("/projects/shop/stop", headers=admin).status_code == 200


def test_nobody_else_gets_into_admin(client):
    alice = _signup(client, "alice")
    assert client.get("/admin/users", headers=alice).status_code == 403
    assert client.get("/admin/users", headers={"X-API-Token": "carol-t"}).status_code == 403
    assert client.get("/admin/users").status_code == 401
    assert (
        client.post("/auth/signup", json={"username": "admin", "password": "whatever12"}).status_code == 409
    )


def test_users_lists_accounts_tokens_and_owners_without_secrets(client):
    _signup(client, "alice")
    main._projects["shop"] = {"name": "shop", "owner": "alice", "live": {"local": {}}}
    main._projects["old"] = {"name": "old", "owner": "olduser"}
    main._projects["legacy"] = {"name": "legacy"}  # written before owners existed: admin's
    admin = _login(client, "admin", ADMIN_PW)
    listed = {u["name"]: u for u in client.get("/admin/users", headers=admin).json()["users"]}
    assert {n: u["kind"] for n, u in listed.items()} == {
        "admin": "admin",
        "alice": "account",
        "carol": "token",
        "olduser": "owner",
    }
    assert (listed["alice"]["apps"], listed["alice"]["live"], listed["alice"]["sessions"]) == (1, 1, 1)
    assert listed["admin"]["apps"] == 1
    assert listed["alice"]["last_login"] is None  # signed up, never logged in since
    assert not any(k in u for u in listed.values() for k in ("hash", "salt", "token"))


def test_reset_password_ends_the_old_logins(client):
    alice = _signup(client, "alice")
    admin = _login(client, "admin", ADMIN_PW)
    r = client.post("/admin/users/alice/password", json={"password": "new password"}, headers=admin)
    assert r.status_code == 200, r.text
    assert client.get("/projects/me", headers=alice).status_code == 401
    _login(client, "alice", "new password")
    short = client.post("/admin/users/alice/password", json={"password": "short"}, headers=admin)
    assert short.status_code == 422


def test_logout_everywhere_and_delete(client):
    alice = _signup(client, "alice")
    admin = _login(client, "admin", ADMIN_PW)
    assert client.post("/admin/users/alice/logout", headers=admin).json() == {"name": "alice", "ended": 1}
    assert client.get("/projects/me", headers=alice).status_code == 401
    assert client.post("/admin/users/alice/delete", headers=admin).status_code == 200
    assert (
        client.post("/auth/login", json={"username": "alice", "password": "correct horse"}).status_code == 401
    )
    assert client.post("/admin/users/alice/delete", headers=admin).status_code == 404


def test_the_admin_account_itself_is_managed_by_the_env(client):
    admin = _login(client, "admin", ADMIN_PW)
    for path in ("password", "logout", "delete"):
        r = client.post(f"/admin/users/admin/{path}", json={"password": "another one"}, headers=admin)
        assert r.status_code == 409


def test_changing_the_env_password_logs_admin_out_and_unsetting_it_removes_admin(env, client):
    admin = _login(client, "admin", ADMIN_PW)
    TestClient(main.create_app(), client=REMOTE)  # restart, same password: still logged in
    assert client.get("/projects/me", headers=admin).status_code == 200

    env.setenv("CLOUDMORPH_ADMIN_PASSWORD", "rotated password")
    restarted = TestClient(main.create_app(), client=REMOTE)
    assert restarted.get("/projects/me", headers=admin).status_code == 401
    _login(restarted, "admin", "rotated password")

    _signup(restarted, "alice")  # keeps the server configured once admin is gone
    env.delenv("CLOUDMORPH_ADMIN_PASSWORD")
    gone = TestClient(main.create_app(), client=REMOTE)
    assert (
        gone.post("/auth/login", json={"username": "admin", "password": "rotated password"}).status_code
        == 401
    )


def test_a_too_short_admin_password_stops_the_server(env):
    env.setenv("CLOUDMORPH_ADMIN_PASSWORD", "short")
    with pytest.raises(ValueError, match="admin password"):
        main.create_app()
