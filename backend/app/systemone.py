"""System One decision models: clef-flash (local) and Jev (TypeSafe, hosted).

Both answer the same request, so one small client serves both:

    POST {base}/v1/systemone  {"model", "state", "questions"} -> {"model", "answers", "usage"}

A question is one of
    noul    yes/no           -> answer["noul"] = P(true)
    choice  pick one option  -> answer["choice"], answer["probabilities"], answer["confidence"]
    score   ordered levels   -> answer["score"] (probability-weighted level), answer["probabilities"]

The model scores every option of every question without generating text, which is what makes a
sub-second decision possible. On an M4 Pro, clef-flash reads about 460 input tokens per second, so a
decision that must finish in 1 s has to stay under ~400 tokens (state + questions).

Environment
    CLEF_BASE_URL     local clef-flash (Ollama >= 0.35.1), e.g. http://127.0.0.1:11436 — see scripts/clef-up.sh.
                      Unset: nothing calls the local model (opt-in, so tests and servers without it stay quiet).
    CLEF_MODEL        default clef-flash
    TYPESAFE_API_KEY  enables Jev (hosted). JEV_BASE_URL (https://api.typesafe.ai), JEV_MODEL (jev-latest)
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

# Ollama rejects bodies over 64 KiB without images; stay under it with room for the questions.
MAX_BODY_BYTES = 60_000

# Secrets in code lines and logs are masked before they reach a model (Jev is a hosted service), events or
# notes. Code that only *reads* a secret (api_key=os.environ[...]) is left readable.
_SECRET = re.compile(
    r"(?P<k>(?:password|passwd|secret|api_?key|token)\s*[:=]\s*['\"]?)"
    r"(?!(?:os|process|env|getenv|config|settings|self|System|ENV|import)\b)[^'\"\s,;&)]{3,}"
    r"|(?P<u>://[^\s'\"@/:]*:)[^\s'\"@]+(?=@)"
    r"|(?P<b>Bearer\s+)[A-Za-z0-9._~+/=-]{8,}"
    r"|(?P<p>\b(?:sk-|AKIA|ghp_|gho_|xox[bp]-))[A-Za-z0-9-]{8,}",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    """'postgres://app:hunter2@db' -> 'postgres://app:***@db', 'password="x1y2"' -> 'password="***"'."""
    return _SECRET.sub(lambda m: (m["k"] or m["u"] or m["b"] or m["p"]) + "***", text)


class SystemOneError(RuntimeError):
    pass


@dataclass(frozen=True)
class Answers:
    answers: dict
    model: str
    ms: float  # round trip of the model call only
    input_tokens: int = 0

    def noul(self, key: str) -> float:
        return float(self.answers[key]["noul"])

    def choice(self, key: str) -> tuple[str, dict[str, float], float]:
        a = self.answers[key]
        return (
            a["choice"],
            {k: float(v) for k, v in a["probabilities"].items()},
            float(a.get("confidence", 0)),
        )

    def score(self, key: str) -> tuple[float, dict[str, float]]:
        a = self.answers[key]
        return float(a["score"]), {k: float(v) for k, v in a["probabilities"].items()}


@dataclass(frozen=True)
class Endpoint:
    name: str  # what people see: "clef-flash (로컬)", "Jev"
    base_url: str
    model: str
    api_key: str | None = None
    local: bool = False  # Ollama: keep the model loaded between calls

    def ask(self, state, questions: dict, timeout: float = 15.0) -> Answers:
        """One call. Raises SystemOneError on any failure (callers decide what is safe to do then)."""
        payload: dict = {"model": self.model, "state": state, "questions": questions}
        if self.local:
            payload["keep_alive"] = -1
        body = json.dumps(payload, ensure_ascii=False).encode()
        if len(body) > MAX_BODY_BYTES:
            raise SystemOneError(f"request too large ({len(body)} bytes)")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = self.base_url.rstrip("/") + "/v1/systemone"
        for attempt in range(2):  # Jev: one retry on 429 Too Many Requests / 529 Overloaded
            t0 = time.perf_counter()
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    out = json.loads(r.read())
                ms = (time.perf_counter() - t0) * 1000
                usage = out.get("usage") or {}
                return Answers(
                    out["answers"], out.get("model", self.model), ms, int(usage.get("input_tokens", 0))
                )
            except urllib.error.HTTPError as e:
                if e.code in (429, 529) and attempt == 0:
                    time.sleep(0.3)
                    continue
                detail = e.read()[:300].decode(errors="replace")
                raise SystemOneError(f"{self.name}: HTTP {e.code} {detail}") from None
            except (OSError, ValueError, KeyError) as e:
                raise SystemOneError(f"{self.name}: {type(e).__name__}: {e}") from None
        raise SystemOneError(f"{self.name}: overloaded")


def noul(instructions: str, true: str | None = None, false: str | None = None) -> dict:
    q: dict = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {k: v for k, v in (("true", true), ("false", false)) if v}
    return q


def choice(instructions: str, options: dict[str, str | None]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions: str, levels: list[str]) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": levels}  # Jev allows 2..10 levels


def clef() -> Endpoint | None:
    """The local clef-flash, when CLEF_BASE_URL is set."""
    base = os.environ.get("CLEF_BASE_URL")
    if not base:
        return None
    return Endpoint("clef-flash (로컬)", base, os.environ.get("CLEF_MODEL") or "clef-flash", local=True)


def jev() -> Endpoint | None:
    """TypeSafe's hosted Jev, when TYPESAFE_API_KEY is set."""
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        return None
    base = os.environ.get("JEV_BASE_URL") or "https://api.typesafe.ai"
    return Endpoint("Jev", base, os.environ.get("JEV_MODEL") or "jev-latest", api_key=key)


_ready: dict[str, tuple[float, bool]] = {}
_ready_lock = threading.Lock()


def ready(ep: Endpoint | None, ttl: float = 30.0) -> bool:
    """Is the local model up and pulled? Cached for `ttl` seconds; costs one GET /api/tags (no inference).
    Hosted endpoints are assumed up (a failed call is handled where it happens)."""
    if ep is None:
        return False
    if not ep.local:
        return True
    now = time.monotonic()
    with _ready_lock:
        hit = _ready.get(ep.base_url)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        with urllib.request.urlopen(ep.base_url.rstrip("/") + "/api/tags", timeout=0.5) as r:
            names = {m.get("name", "") for m in json.loads(r.read()).get("models", [])}
        ok = any(n == ep.model or n.startswith(ep.model + ":") for n in names)
    except (OSError, ValueError):
        ok = False
    with _ready_lock:
        _ready[ep.base_url] = (now, ok)
    return ok
