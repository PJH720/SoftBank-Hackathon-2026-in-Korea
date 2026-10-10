"""ID/password accounts: sign up, log in, log out, and how a login token maps to a user."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import main, users

REMOTE = ("203.0.113.9", 4000)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDMORPH_ACCOUNTS_FILE", str(tmp_path / "accounts.json"))
    monkeypatch.setenv("CLOUDMORPH_API_TOKEN", "admin-t")
    monkeypatch.setenv("CLOUDMORPH_USERS", "carol:carol-t")
    monkeypatch.delenv("CLOUDMORPH_USERS_FILE", raising=False)
    monkeypatch.delenv("CLOUDMORPH_SIGNUP_CODE", raising=False)
    monkeypatch.setattr(users, "SCRYPT", {"n": 2**10, "r": 8, "p": 1, "dklen": 32})  # fast in tests
    users._failures.clear()
    return TestClient(main.create_app(), client=REMOTE)


def _signup(client, name="alice", password="correct horse", **extra):
    return client.post("/auth/signup", json={"username": name, "password": password, **extra})


def _me(client, token):
    return client.get("/projects/me", headers={"X-API-Token": token})


def test_signup_logs_in_and_the_token_is_that_user(client):
    r = _signup(client, name="Alice")  # ids are case-insensitive
    assert r.status_code == 200, r.text
    assert r.json()["user"] == "alice"
    assert _me(client, r.json()["token"]).json() == {"user": "alice", "admin": False}


def test_each_user_only_sees_their_own_projects(client):
    alice = _signup(client, "alice").json()["token"]
    bob = _signup(client, "bob").json()["token"]
    main._projects["shop"] = {"name": "shop", "owner": "alice", "source": "x", "targets": [], "history": []}

    def names(token):
        return [p["name"] for p in client.get("/projects", headers={"X-API-Token": token}).json()["projects"]]

    assert names(alice) == ["shop"]
    assert names(bob) == []


@pytest.mark.parametrize("name", ["alice", "admin", "carol", "olduser"])
def test_ids_in_use_cannot_be_claimed(client, name):
    _signup(client, "alice")
    # an owner left from before accounts existed: a new account with that id would inherit the app
    main._projects["legacy"] = {
        "name": "legacy",
        "owner": "olduser",
        "source": "x",
        "targets": [],
        "history": [],
    }
    assert _signup(client, name).status_code == 409


@pytest.mark.parametrize(
    ("name", "password"),
    [("al", "correct horse"), ("Bad Name", "correct horse"), ("-alice", "correct horse"), ("alice", "short")],
)
def test_bad_ids_and_short_passwords_are_refused(client, name, password):
    assert _signup(client, name, password).status_code == 422


def test_login_logout(client):
    _signup(client, "alice")
    assert client.post("/auth/login", json={"username": "alice", "password": "wrong pass"}).status_code == 401
    assert client.post("/auth/login", json={"username": "nobody", "password": "whatever1"}).status_code == 401
    r = client.post("/auth/login", json={"username": "ALICE", "password": "correct horse"})
    token = r.json()["token"]
    assert r.status_code == 200 and _me(client, token).status_code == 200

    assert client.post("/auth/logout", headers={"X-API-Token": token}).status_code == 200
    assert _me(client, token).status_code == 401


def test_repeated_wrong_passwords_lock_the_id_for_a_while(client):
    _signup(client, "alice")
    for _ in range(users.LOGIN_FAILURES):
        assert (
            client.post("/auth/login", json={"username": "alice", "password": "nope nope"}).status_code == 401
        )
    # even the right password is refused until the lock expires
    r = client.post("/auth/login", json={"username": "alice", "password": "correct horse"})
    assert r.status_code == 429


def test_invite_code(client, monkeypatch):
    monkeypatch.setenv("CLOUDMORPH_SIGNUP_CODE", "team-gold")
    assert client.get("/auth/config").json() == {"signup_code": True}
    assert _signup(client, "alice").status_code == 403
    assert _signup(client, "alice", code="wrong").status_code == 403
    assert _signup(client, "alice", code="team-gold").status_code == 200


def test_store_keeps_no_plaintext_password_or_token(client, tmp_path):
    token = _signup(client, "alice", password="correct horse").json()["token"]
    path = tmp_path / "accounts.json"
    raw = path.read_text()
    assert "correct horse" not in raw and token not in raw
    assert path.stat().st_mode & 0o777 == 0o600
    assert set(json.loads(raw)["users"]["alice"]) == {"salt", "hash", "created_at"}


def test_expired_login_is_refused(client, monkeypatch):
    token = _signup(client, "alice").json()["token"]
    now = users.time.time()
    monkeypatch.setattr(users.time, "time", lambda: now + 8 * 86400)  # default lifetime is 7 days
    assert _me(client, token).status_code == 401


def test_without_any_token_configured_signing_up_turns_the_guard_on(client, monkeypatch):
    monkeypatch.delenv("CLOUDMORPH_API_TOKEN")
    monkeypatch.delenv("CLOUDMORPH_USERS")
    local = TestClient(main.create_app(), client=("127.0.0.1", 5000))
    assert local.get("/projects/me").json()["admin"] is True  # local dev, nobody signed up yet
    _signup(local, "alice")
    assert local.get("/projects/me").status_code == 401  # accounts exist now: a token is needed
