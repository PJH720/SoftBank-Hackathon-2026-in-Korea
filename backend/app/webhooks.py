"""GitHub push -> redeploy (CD).

Per-project link (multi-user, preferred): POST /projects/{name}/cd creates a link with its own id and secret.

    GitHub ──POST /webhook/github/<link id> (X-Hub-Signature-256 with that link's secret)
          ──▶ verify HMAC ──▶ same repo? allowed branch? ──▶ redeploy that project with *its* name, targets, owner

    The webhook is registered on GitHub with the user's own token (used once, never stored), or by hand
    with the URL + secret the API returns. A leaked secret only reaches that one project.

Shared endpoint (legacy, operator only): POST /webhook/github signed with CLOUDMORPH_WEBHOOK_SECRET.
    Redeploys every unlinked project whose source is the pushed repo; with none, deploys the repo under
    its own name to CLOUDMORPH_CD_TARGETS as admin.

Env:
    CLOUDMORPH_WEBHOOK_SECRET   secret of the shared endpoint (unset -> shared endpoint disabled)
    CLOUDMORPH_CD_TARGETS       shared endpoint only: comma list, default "local,cloudrun"
    CLOUDMORPH_CD_BRANCHES      shared endpoint only: branches to deploy; default: the repository's default branch
    CLOUDMORPH_PUBLIC_URL       base URL GitHub should call (default: taken from the request)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """GitHub sends `X-Hub-Signature-256: sha256=<hex hmac of the raw body>`."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[len("sha256=") :])


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:40]
    return s or "app"


@dataclass
class PushInfo:
    repo_full_name: str
    clone_url: str
    branch: str
    default_branch: str
    sha: str
    message: str
    pusher: str
    deleted: bool

    @property
    def app_name(self) -> str:
        return slug(self.repo_full_name.split("/")[-1])


def parse_push(payload: dict) -> PushInfo | None:
    """Extract what we need from a GitHub `push` event; None when it is not a branch push."""
    ref = payload.get("ref") or ""
    if not ref.startswith("refs/heads/"):
        return None  # tags etc.
    repo = payload.get("repository") or {}
    head = payload.get("head_commit") or {}
    return PushInfo(
        repo_full_name=repo.get("full_name") or repo.get("name") or "unknown/unknown",
        clone_url=repo.get("clone_url") or repo.get("html_url", "") + ".git",
        branch=ref[len("refs/heads/") :],
        default_branch=repo.get("default_branch") or "main",
        sha=(payload.get("after") or head.get("id") or "")[:12],
        message=(head.get("message") or "").splitlines()[0][:120] if head.get("message") else "",
        pusher=(payload.get("pusher") or {}).get("name") or (payload.get("sender") or {}).get("login") or "?",
        deleted=bool(payload.get("deleted")),
    )


def cd_targets() -> list[str]:
    raw = os.getenv("CLOUDMORPH_CD_TARGETS", "local,cloudrun")
    return [t.strip() for t in raw.split(",") if t.strip()]


def branch_allowed(info: PushInfo) -> bool:
    raw = os.getenv("CLOUDMORPH_CD_BRANCHES", "").strip()
    allowed = {b.strip() for b in raw.split(",") if b.strip()} or {info.default_branch}
    return info.branch in allowed


# --------------------------------------------------------------------------- #
# Per-project links
# --------------------------------------------------------------------------- #
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
GITHUB_API = "https://api.github.com"


def github_repo(url: str | None) -> str | None:
    """ "owner/repo" (lowercase) for a github.com HTTPS URL, else None."""
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "github.com":
        return None
    path = parsed.path.strip("/").removesuffix(".git")
    return path.lower() if _REPO.fullmatch(path) else None


def new_link() -> tuple[str, str]:
    """(link id that goes in the webhook URL, HMAC secret)"""
    return secrets.token_hex(8), secrets.token_hex(24)


def public_link(link: dict) -> dict:
    """What the dashboard may see: everything but the secret."""
    return {k: v for k, v in link.items() if k != "secret"}


class GitHubError(Exception):
    """GitHub API refused (bad token, no admin rights on the repo, ...). Message is safe to show."""


def _github(method: str, path: str, token: str, body: dict | None = None):
    req = urllib.request.Request(
        GITHUB_API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cloudmorph",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("message", "")
        except ValueError:
            msg = ""
        hint = {
            401: "token is invalid",
            403: "token lacks admin:repo_hook",
            404: "no admin access to the repo",
        }
        raise GitHubError(f"GitHub {e.code}: {msg or hint.get(e.code, 'request failed')}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise GitHubError(f"GitHub unreachable: {e}") from None


def create_github_hook(repo: str, url: str, secret: str, token: str) -> int:
    """Register a push webhook on `repo` with the user's token. Returns GitHub's hook id."""
    hook = _github(
        "POST",
        f"/repos/{repo}/hooks",
        token,
        {
            "name": "web",
            "active": True,
            "events": ["push"],
            "config": {"url": url, "content_type": "json", "secret": secret, "insecure_ssl": "0"},
        },
    )
    return int(hook["id"])


def delete_github_hook(repo: str, hook_id: int, token: str) -> None:
    try:
        _github("DELETE", f"/repos/{repo}/hooks/{int(hook_id)}", token)
    except GitHubError as e:
        if "404" not in str(e):  # already gone on GitHub: nothing left to remove
            raise


def link_branch_allowed(link: dict, info: PushInfo) -> bool:
    """A link deploys its chosen branch, or the repository's default branch when none was chosen."""
    return info.branch == (link.get("branch") or info.default_branch)
