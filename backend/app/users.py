"""ID/password accounts for the dashboard: sign up, log in, log out.

    CLOUDMORPH_ACCOUNTS_FILE   JSON store (default backend/.cloudmorph/accounts.json, written with mode 600)
    CLOUDMORPH_SIGNUP_CODE     when set, signing up needs this invite code (set it on a public server)
    CLOUDMORPH_SESSION_DAYS    how long a login lasts (default 7)

A login hands out a random session token that accounts.identify() maps to the user, exactly like a
per-user API token, so ownership of projects/deployments works the same for both.
Passwords are kept as scrypt(salt) and sessions only as sha256(token): a leaked file gives no usable
password or token. Repeated wrong passwords are refused for a while (LOGIN_LOCK_SECONDS), counted per
client IP: 5 for one id (so a stranger elsewhere can't lock someone out) and 20 for any id from that IP.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Iterable
from pathlib import Path

USER_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{2,31}")  # a subset of accounts' names, at least 3 chars
MIN_PASSWORD, MAX_PASSWORD = 8, 128
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
LOGIN_FAILURES, LOGIN_FAILURES_PER_IP, LOGIN_LOCK_SECONDS = 5, 20, 300
MAX_TRACKED_FAILURES = 10_000  # failure keys kept in memory; random ids must not grow it without bound

_lock = threading.Lock()
# "ip|name" and "ip|*" -> recent failure times, least recently failed first (in memory; a restart clears it)
_failures: dict[str, list[float]] = {}
_DUMMY_SALT = secrets.token_bytes(16)
# (path, inode, mtime_ns, size) -> parsed store. identify() looks a session up on every protected request.
_cache: tuple[tuple, dict] | None = None


class AuthError(ValueError):
    """Sign-up/login refused. `status` is the HTTP status the API answers with."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _path() -> Path:
    default = Path(__file__).resolve().parents[1] / ".cloudmorph" / "accounts.json"
    return Path(os.getenv("CLOUDMORPH_ACCOUNTS_FILE") or default)


def _read() -> dict:
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("users", {})
    data.setdefault("sessions", {})
    return data


def _stamp(path: Path) -> tuple:
    try:
        st = path.stat()
    except OSError:
        return (path, None)
    return (path, st.st_ino, st.st_mtime_ns, st.st_size)  # _write's os.replace always gives a new inode


def _cached() -> dict:
    """The store for lookups, re-read only when the file changed. Read-only: writers use _read()."""
    global _cache
    stamp, cache = _stamp(_path()), _cache
    if cache is None or cache[0] != stamp:
        cache = (stamp, _read())
        _cache = cache
    return cache[1]


def _write(data: dict) -> None:
    global _cache
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)
    _cache = None  # the next lookup re-reads, even if the stamp happens to match


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _session_seconds() -> float:
    try:
        return max(float(os.getenv("CLOUDMORPH_SESSION_DAYS", "7")), 0.01) * 86400
    except ValueError:
        return 7 * 86400


def _new_session(data: dict, name: str) -> str:
    now = time.time()
    data["sessions"] = {k: s for k, s in data["sessions"].items() if s["expires"] > now}
    token = secrets.token_urlsafe(32)
    data["sessions"][_token_key(token)] = {"user": name, "expires": now + _session_seconds()}
    return token


def _recent(key: str, now: float) -> int:
    return sum(1 for t in _failures.get(key, ()) if now - t < LOGIN_LOCK_SECONDS)


def _record_failure(keys: tuple[str, ...], now: float) -> None:
    for key in keys:
        times = [t for t in _failures.pop(key, ()) if now - t < LOGIN_LOCK_SECONDS]
        _failures[key] = [*times, now]  # re-inserted: the dict stays ordered by last failure
    if len(_failures) > MAX_TRACKED_FAILURES:
        for key in [k for k, times in _failures.items() if now - times[-1] >= LOGIN_LOCK_SECONDS]:
            del _failures[key]
        while len(_failures) > MAX_TRACKED_FAILURES:
            del _failures[next(iter(_failures))]


def has_users() -> bool:
    return bool(_cached()["users"])


def signup(name: str, password: str, code: str | None = None, *, taken: Iterable[str] = ()) -> str:
    """Create the account and log it in. `taken`: names already used elsewhere (admin, API-token users,
    owners of existing projects) so a new account never inherits someone else's apps."""
    invite = os.getenv("CLOUDMORPH_SIGNUP_CODE", "")
    if invite and not hmac.compare_digest((code or "").encode(), invite.encode()):
        raise AuthError(403, "invalid sign-up code")
    if not USER_NAME.fullmatch(name):
        raise AuthError(422, "id must be 3-32 chars of a-z, 0-9, '-' or '_' and start with a letter or digit")
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AuthError(422, f"password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters")
    salt = secrets.token_bytes(16)
    digest = _hash_password(password, salt)  # slow part outside the lock
    with _lock:
        data = _read()
        if name in data["users"] or name in set(taken):
            raise AuthError(409, "this id is already taken")
        data["users"][name] = {"salt": salt.hex(), "hash": digest.hex(), "created_at": time.time()}
        token = _new_session(data, name)
        _write(data)
    return token


def login(name: str, password: str, client: str = "") -> str:
    """`client`: the caller's IP, so failed logins lock that caller out and not the account everywhere."""
    now = time.time()
    keys = (f"{client}|{name}", f"{client}|*")
    with _lock:
        if _recent(keys[0], now) >= LOGIN_FAILURES or _recent(keys[1], now) >= LOGIN_FAILURES_PER_IP:
            raise AuthError(429, "too many failed logins; try again in a few minutes")
        user = _cached()["users"].get(name)
    # hash even for an unknown id so response time does not reveal which ids exist
    salt = bytes.fromhex(user["salt"]) if user else _DUMMY_SALT
    digest = _hash_password(password[:MAX_PASSWORD], salt)
    if not user or not hmac.compare_digest(digest, bytes.fromhex(user["hash"])):
        with _lock:
            _record_failure(keys, now)
        raise AuthError(401, "wrong id or password")
    with _lock:
        _failures.pop(keys[0], None)  # the per-IP count stays: logging into your own id must not reset it
        data = _read()
        token = _new_session(data, name)
        _write(data)
    return token


def logout(token: str) -> None:
    with _lock:
        data = _read()
        if data["sessions"].pop(_token_key(token), None) is not None:
            _write(data)


def user_for(token: str) -> str | None:
    """Account name for a live session token, else None."""
    if not token:
        return None
    session = _cached()["sessions"].get(_token_key(token))
    if not session or session["expires"] <= time.time():
        return None
    return session["user"]
