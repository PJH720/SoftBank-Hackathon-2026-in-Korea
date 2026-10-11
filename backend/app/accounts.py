"""Who is calling: API token -> user. Several people share one CloudMorph server.

    CLOUDMORPH_API_TOKEN     operator token -> user "admin" (sees every project; legacy projects belong to it)
    CLOUDMORPH_USERS         "alice:<token>,bob:<token>"          per-user tokens (optional)
    CLOUDMORPH_USERS_FILE    JSON {"alice": "<token>", ...}       same, from a file (optional)
    sign-up / login          users.py: ID + password accounts; a login's session token works like the above
    CLOUDMORPH_ADMIN_PASSWORD  "admin" can also log in with this password; that session is admin as well

A user only sees and changes their own projects, deployments and CD links. An app name belongs to the
user who deployed it first (Cloud Run service names are shared across the whole GCP project, so two
users must not overwrite each other's service by picking the same name).
With no token configured at all, only a direct loopback client passes and it acts as admin (local dev).
Read at call time so tests and a restarted server pick up changes.
"""

from __future__ import annotations

import hmac
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from app import users

ADMIN = "admin"
_USER_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


@dataclass(frozen=True)
class User:
    name: str
    admin: bool = False

    def owns(self, record: dict) -> bool:
        """Projects written before owners existed belong to admin."""
        return self.admin or record.get("owner", ADMIN) == self.name


LOCAL_ADMIN = User(ADMIN, admin=True)


def user_tokens() -> dict[str, str]:
    """name -> token for the per-user tokens (admin token not included)."""
    out: dict[str, str] = {}
    path = os.getenv("CLOUDMORPH_USERS_FILE")
    if path:
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            data = {}
        out.update({str(k): str(v) for k, v in data.items() if v})
    for item in os.getenv("CLOUDMORPH_USERS", "").split(","):
        name, _, token = item.strip().partition(":")
        if name and token:
            out[name.strip()] = token.strip()
    return {n: t for n, t in out.items() if _USER_NAME.fullmatch(n) and n != ADMIN}


def configured() -> bool:
    return bool(os.getenv("CLOUDMORPH_API_TOKEN")) or bool(user_tokens()) or users.has_users()


def identify(supplied: str) -> User | None:
    """User for this token, or None. Every candidate is compared in constant time."""
    if not supplied:
        return None
    found = None
    admin_token = os.getenv("CLOUDMORPH_API_TOKEN", "")
    if admin_token and hmac.compare_digest(supplied.encode(), admin_token.encode()):
        found = LOCAL_ADMIN
    for name, token in user_tokens().items():
        if hmac.compare_digest(supplied.encode(), token.encode()) and found is None:
            found = User(name)
    # login sessions are looked up by sha256(token), so there is nothing to compare one by one
    session_user = users.user_for(supplied)
    if found is None and session_user:
        found = LOCAL_ADMIN if session_user == ADMIN else User(session_user)
    return found
