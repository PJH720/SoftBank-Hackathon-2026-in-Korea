"""Fast analyzer (owner: 이요환): the local clef-flash picks the service in under a second.

    scan (전처리, no model)  ->  decide (1 call, < 1 s)  ->  inspect (1-2 calls)  ->  analyzer.analyze_with

- scan: walk the repo once. Read the manifests, and collect *candidate* lines for each risk signal, every
  environment variable the code reads, start-command candidates and suspicious lines. A pattern hit is
  never a verdict; it only decides what the model is shown.
- decide: one `choice` question over a ~300-token summary: serverless_container / vm / static_site /
  serverless_function. clef-flash scores all four in one pass (~0.6 s on an M4 Pro), so the dashboard can
  show the service right after the code is read.
- inspect: answers the analyzer's 18 inspectors from one batched call (yes/no for each signal that has
  candidates, what each env var is) plus a malicious-code check. Then analyzer.analyze_with applies the
  same fences and policy as the Claude path: evidence must exist, RULES remove types, fatal risks are not
  released without consent, the repo Dockerfile is screened, and the decision only orders what survives.

What it gives up against the Claude inspectors: a signal with no candidate line counts as "no", the
Dockerfile is the template (no AI edits), and "other risks" are not looked for. ANALYZER_MODE=full keeps
the Claude path.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: pyproject.toml dependencies are skipped
    tomllib = None

from app import analyzer as az
from app import systemone
from app.schemas import AnalysisResult

# decision option -> (analyzer type, what people see, plain explanation)
SERVICES = {
    "serverless_container": (
        2,
        "Cloud Run (서버리스 컨테이너)",
        "요청이 올 때만 켜지는 서버예요. 쓰지 않을 땐 꺼져 있어서 비용이 거의 안 들어요.",
    ),
    "vm": (
        4,
        "항상 켜진 서버 (Docker)",
        "24시간 켜져 있는 컴퓨터예요. 디스크나 메모리에 둔 데이터가 그대로 남아요.",
    ),
    "static_site": (
        5,
        "정적 사이트",
        "완성된 HTML·JS 파일만 올리면 돼요. 서버 코드가 없어서 가장 빠르고 저렴해요.",
    ),
    "serverless_function": (
        1,
        "Cloud Run functions (서버리스 함수)",
        "함수 하나만 올려 두면 요청마다 실행돼요. 가장 가벼운 방식이에요.",
    ),
}
# Tuned on 12 labeled repos (12/12, max 0.45 s): name only what was found, and say plainly that an ordinary
# web server keeps its data in an external database. Listing absent features ("no disk writes …") pulled
# the model toward vm.
DECIDE = systemone.choice(
    "Where should this app run? serverless_container: an ordinary web server or API; data lives in an external "
    "database. vm: the code itself keeps data on local disk or in process memory, holds long-lived connections, "
    "runs scheduled jobs, or needs a GPU. static_site: a frontend with no server code. serverless_function: one "
    "request handler function.",
    dict.fromkeys(SERVICES),  # bare options: same cost as one yes/no question; ties go to the first
)

# Signals in English, short: every token costs ~2 ms on the local model
SIGNALS = {
    "has_server": "Does this code start an HTTP server (routes or a listening port)?",
    "is_spa": "Is this only a frontend whose build output is static HTML/JS/CSS (no server code)?",
    "long_running": "Does it need a process that stays alive between requests (WebSocket/SSE, workers, queue consumers, loops started at boot)?",
    "scheduler": "Does it run periodic jobs inside the process that break features if they never run?",
    "memory_state": "Does it keep user-visible data across requests only in process memory?",
    "sqlite": "Does it use a SQLite file as a data store when it runs?",
    "file_write": "Does it write files to local disk that must survive restarts (uploads, results), not cache or logs?",
    "gpu": "Does it require a GPU to run?",
    "system_packages": "Does it need OS packages a language package manager cannot install (ffmpeg, chromium, tesseract)?",
    "os_windows": "Does it only run on Windows?",
    "os_macos": "Does it only run on macOS?",
    "hardcoded_config": "Are production secrets or production server addresses written in the code itself?",
    "bind_localhost": "Does the server listen only on 127.0.0.1/localhost?",
}
I = re.IGNORECASE
# Candidate lines per signal. A hit only means "show this line to the model".
HINTS = {
    "has_server": re.compile(
        r"\b(FastAPI|Flask|Starlette|Quart|Sanic|Koa|Hono|Fastify)\s*\(|\bexpress\s*\(\s*\)|createServer\s*\(|"
        r"\.listen\s*\(|uvicorn\.run\s*\(|\bapp\.run\s*\(|@SpringBootApplication|ListenAndServe|gin\.(Default|New)\(|"
        r"echo\.New\(|fiber\.New\(|NestFactory\.create|web\.run_app\(|django\.core\.wsgi|get_wsgi_application\(|"
        r"functions_framework|serve\s*\(\s*app"
    ),
    "long_running": re.compile(
        r"WebSocket|socket\.io|websockets|text/event-stream|EventSource|EventSourceResponse|\bwhile\s+True\s*:|"
        r"Celery\s*\(|\bbullmq?\b|KafkaConsumer|kafkajs|amqplib|\bpika\b|\.subscribe\(|threading\.Thread\(|"
        r"asyncio\.create_task\(|new Worker\(",
        I,
    ),
    "scheduler": re.compile(
        r"setInterval\s*\(|node-cron|cron\.schedule|APScheduler|BackgroundScheduler|add_job\(|@Scheduled|"
        r"schedule\.every|beat_schedule|@repeat_every|crontab\(",
        I,
    ),
    "memory_state": re.compile(
        r"^(?:[A-Za-z_]\w*)\s*(?::\s*[\w\[\], |]+)?\s*=\s*(?:\{\}|\[\]|dict\(\)|list\(\)|set\(\)|defaultdict\(|Counter\(|deque\()|"
        r"^(?:const|let|var)\s+\w+\s*=\s*(?:\{\}|\[\]|new (?:Map|Set)\(\))|"
        r"\bstatic\s+(?:final\s+)?(?:Map|List|Set|ConcurrentHashMap)<|^var\s+\w+\s*=\s*(?:map\[|make\(map)|"
        r"MemoryStore|cache\s*=\s*\{\}"
    ),
    "sqlite": re.compile(r"sqlite3?\b|better-sqlite3|sqlite:///|\.sqlite3?['\"]|\.db['\"]", I),
    "file_write": re.compile(
        r"open\([^)\n]*['\"][wax]b?\+?['\"]|\.write_text\(|\.write_bytes\(|\.save\(\s*(?:os\.path|f['\"]|path|file)|"
        r"fs\.(?:writeFile|appendFile|createWriteStream)|multer\(|UPLOAD_FOLDER|FileOutputStream|Files\.write|"
        r"os\.(?:Create|WriteFile|OpenFile)\(|shutil\.(?:copy|move)\("
    ),
    "gpu": re.compile(
        r"\bcuda\b|torch\.device\(|\.to\(['\"]cuda|tensorflow-gpu|onnxruntime-gpu|\bcupy\b|bitsandbytes|\bvllm\b",
        I,
    ),
    "system_packages": re.compile(
        r"ffmpeg|chromium|puppeteer|playwright|selenium|tesseract|wkhtmltopdf|pdfkit|libreoffice|imagemagick|"
        r"\bwand\b|opencv|\bcv2\b|poppler|pdf2image|weasyprint|\bcairo|graphviz",
        I,
    ),
    "os_windows": re.compile(
        r"win32api|win32con|winreg|pywin32|pythoncom|ctypes\.windll|System\.Windows|comtypes"
    ),
    "os_macos": re.compile(r"\bAppKit\b|osascript|pyobjc|NSWorkspace|\bCocoa\b"),
    "hardcoded_config": re.compile(
        r"(?:password|passwd|secret|api_?key|token)\s*[:=]\s*['\"][^'\"\s$%{]{6,}['\"]|"
        r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s'\"@/]*:[^\s'\"@]*@|"
        r"sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}",
        I,
    ),
    "bind_localhost": re.compile(
        r"(?:host|bind|listen|address)[^\n]{0,40}(?:127\.0\.0\.1|localhost)|\bapp\.run\(\s*\)|"
        r"\.listen\([^)\n]*['\"](?:127\.0\.0\.1|localhost)['\"]",
        I,
    ),
}
SUSPICIOUS = re.compile(
    r"(?:curl|wget)[^|\n]{0,200}\|\s*(?:ba|z)?sh\b|base64\s+(?:-d|--decode)|b64decode\(|\batob\(|\beval\s*\(|"
    r"new Function\(|child_process|shell\s*=\s*True|os\.system\(|169\.254\.169\.254|metadata\.google\.internal|"
    r"/dev/tcp/|\bnc\s+-e\b|bash\s+-i\b|xmrig|stratum\+tcp|cryptonight|coinhive|\.ssh/id_|/etc/shadow|"
    r"dict\(os\.environ\)|os\.environ\.copy\(\)|JSON\.stringify\(process\.env\)|[(,]\s*process\.env\s*[,)]",
    I,
)
ENV_READ = re.compile(
    r"(?:os\.environ(?:\.get)?\s*[\[(]\s*|os\.getenv\s*\(\s*|\bgetenv\s*\(\s*|environ\.get\s*\(\s*|"
    r"System\.getenv\s*\(\s*|os\.(?:Getenv|LookupEnv)\s*\(\s*|\bENV(?:\.fetch)?\s*[\[(]\s*|env::var\s*\(\s*|"
    r"Environment\.GetEnvironmentVariable\s*\(\s*|\$_ENV\s*\[\s*|\benv\s*\(\s*)['\"]([A-Z][A-Z0-9_]{1,63})['\"]"
)
PROCESS_ENV = re.compile(
    r"process\.env(?:\.([A-Z][A-Z0-9_]{1,63})\b|\[\s*['\"]([A-Z][A-Z0-9_]{1,63})['\"]\s*\])"
)
PROCESS_ENV_DESTRUCT = re.compile(r"\{([^}]{1,300})\}\s*=\s*process\.env")
PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]{1,63})(?::[^}]*)?\}")
# Set by the platform, not by the user
PLATFORM_ENV = {
    "PORT",
    "PATH",
    "HOME",
    "PWD",
    "HOSTNAME",
    "USER",
    "SHELL",
    "TERM",
    "LANG",
    "TZ",
    "K_SERVICE",
    "K_REVISION",
    "K_CONFIGURATION",
}
# option -> (description shown to the model, kind, required, generate). Tuned on 17 labeled variables (17/17).
# Bare option names got 8/12; without "with no default" a default SQLite path read as a required file.
ENV_KINDS = {
    "gen": (
        "a secret the app only uses to sign or encrypt its own data (any random string works)",
        "secret",
        True,
        True,
    ),
    "ext": ("a password, API key, token or connection URL issued by another service", "secret", True, False),
    "req": ("a plain setting the app needs, with no default", "config", True, False),
    "opt": (
        "a setting with a default value in the code (a default path counts), or one the app works without",
        "config",
        False,
        False,
    ),
    "file": (
        (
            "the path of an existing file the app must be given and only reads (credentials, model, certificate), "
            "with no default"
        ),
        "file",
        True,
        False,
    ),
}
STORES = {
    "postgres": re.compile(r"psycopg|asyncpg|pg8000|\bpg\b|postgres|jackc/pgx|lib/pq|org\.postgresql", I),
    "mysql": re.compile(
        r"pymysql|mysqlclient|mysql2|mysql-connector|go-sql-driver/mysql|\bmysql\b|mariadb", I
    ),
    "redis": re.compile(r"\bredis\b|ioredis|aioredis|go-redis|jedis|lettuce", I),
    "s3": re.compile(r"boto3|@aws-sdk/client-s3|\baws-sdk\b|\bminio\b", I),
}
STORE_ENV = {  # env-name hints per store, for the url/host/... roles
    "postgres": ("DATABASE", "POSTGRES", "PG", "DB_"),
    "mysql": ("DATABASE", "MYSQL", "DB_"),
    "redis": ("REDIS", "CACHE"),
    "s3": ("S3", "AWS", "MINIO", "BUCKET", "STORAGE"),
}
ROLE_SUFFIX = [  # (suffix, role) — first match wins
    ("_URL", "url"),
    ("_URI", "url"),
    ("_DSN", "url"),
    ("_HOST", "host"),
    ("_PORT", "port"),
    ("_USER", "user"),
    ("_USERNAME", "user"),
    ("_PASSWORD", "password"),
    ("_PASS", "password"),
    ("_DB", "database"),
    ("_NAME", "database"),
    ("_DATABASE", "database"),
    ("_BUCKET", "bucket"),
    ("BUCKET", "bucket"),
    ("_ENDPOINT", "endpoint"),
    ("_REGION", "region"),
    ("_ACCESS_KEY_ID", "access_key"),
    ("_ACCESS_KEY", "access_key"),
    ("_SECRET_ACCESS_KEY", "secret_key"),
    ("_SECRET_KEY", "secret_key"),
]
CODE_EXT = {
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".rs",
    ".cs",
}
CONFIG_EXT = {".properties", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".json", ".prisma"}
CONFIG_NAMES = {"Dockerfile", "Procfile", "Makefile", ".env.example", ".env.sample", ".env.template"}
MAX_HITS = 6
MAX_SCAN_FILES = 3000  # text files read by scan(); a monorepo should not stall the preprocessing
MAX_ENV_QUESTIONS = 24
NO_KEEP = 0.2  # a "no" this sure drops its candidate lines (they were pattern noise, not risks to consent to)
YES = 0.5


class Unsupported(ValueError):
    """The fast path cannot build this repo (no template for the language and no Dockerfile)."""


class ModelDown(ValueError):
    """clef-flash stopped answering mid-analysis (analyzer.analyze falls back to the Claude inspectors)."""


@dataclass
class Hit:
    path: str
    line: int
    text: str

    @property
    def ref(self) -> str:
        return f"{self.path}:{self.line}"

    def brief(self, n: int = 90) -> str:
        return f"{self.ref} {systemone.redact(self.text)[:n]}"


@dataclass
class Scan:
    root: Path
    files: list[str] = field(default_factory=list)
    language: str | None = None
    framework: str | None = None
    version: str | None = None
    deps: list[str] = field(default_factory=list)
    signals: dict[str, list[Hit]] = field(default_factory=dict)
    env: dict[str, list[Hit]] = field(default_factory=dict)
    stores: dict[str, list[Hit]] = field(default_factory=dict)
    starts: list[tuple[str, Hit | None]] = field(default_factory=list)  # (command, where it came from)
    install: str | None = None
    build: str | None = None
    static_output: str | None = None
    port_env: bool = False
    port: int | None = None
    health: str | None = None
    dockerfile: str | None = None
    suspicious: list[Hit] = field(default_factory=list)
    build_env: dict[str, list[Hit]] = field(default_factory=dict)
    function: bool = False
    ms: float = 0.0


@dataclass
class Decision:
    service: str
    probabilities: dict[str, float]
    confidence: float
    ms: float  # model call only (the 1-second budget)
    prep_ms: float  # scan, excluded from the budget
    engine: str
    tokens: int
    why: list[str]
    target: str  # where this service runs on CloudMorph

    def prefer(self) -> list[int]:
        """analyzer types, most likely first"""
        return [SERVICES[k][0] for k in sorted(self.probabilities, key=lambda k: -self.probabilities[k])]

    def payload(self) -> dict:
        label, explain = SERVICES[self.service][1:]
        return {
            "service": self.service,
            "target": self.target,
            "label": label,
            "explain": explain,
            "probabilities": {k: round(v, 4) for k, v in self.probabilities.items()},
            "confidence": round(self.confidence, 4),
            "ms": round(self.ms),
            "prep_ms": round(self.prep_ms),
            "engine": self.engine,
            "tokens": self.tokens,
            "why": self.why,
        }


# ---------- scan (전처리) ----------


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def _deps_python(root: Path) -> list[str]:
    deps: list[str] = []
    for p in sorted(root.glob("requirements*.txt")):
        for line in az.read_text(p).splitlines():
            name = re.split(r"[\s<>=!~;\[@]", line.strip(), maxsplit=1)[0]
            if name and not name.startswith(("#", "-")):
                deps.append(name.lower())
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and tomllib:
        try:
            data = tomllib.loads(az.read_text(pyproject))
        except tomllib.TOMLDecodeError:
            data = {}
        for d in (data.get("project") or {}).get("dependencies") or []:
            deps.append(re.split(r"[\s<>=!~;\[@]", d, maxsplit=1)[0].lower())
        deps += [
            k.lower()
            for k in ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
            if k != "python"
        ]
    return list(dict.fromkeys(deps))


def _package_json(root: Path) -> dict:
    p = root / "package.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(az.read_text(p))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _module(rel: str) -> str:
    return rel.removesuffix(".py").replace("/", ".")


def scan(root: Path) -> Scan:
    """Read the repo once. No model call; this is the preprocessing the 1-second budget excludes."""
    t0 = time.perf_counter()
    root = root.resolve()
    s = Scan(root=root)
    texts: dict[str, list[str]] = {}
    for p in az._walk(root):
        rel = _rel(root, p)
        s.files.append(rel)
        readable = (
            p.suffix in CODE_EXT
            or p.suffix in CONFIG_EXT
            or p.name in CONFIG_NAMES
            or p.name.startswith("requirements")
        )
        if readable and len(texts) < MAX_SCAN_FILES:
            texts[rel] = az.read_text(p).splitlines()
    s.signals = {n: [] for n in SIGNALS}
    for rel, lines in texts.items():
        code = Path(rel).suffix in CODE_EXT
        docstring = False
        for i, line in enumerate(lines, 1):
            if len(line) > 400:
                continue
            stripped = line.strip()
            if rel.endswith(".py") and stripped.count('"""') % 2:  # entering or leaving a docstring
                docstring = not docstring
                continue
            if docstring or stripped.startswith(("#", "//", "/*", "* ")):
                continue  # prose, not behaviour
            if code:
                for n, rx in HINTS.items():
                    if len(s.signals[n]) < MAX_HITS and rx.search(line if n == "memory_state" else stripped):
                        s.signals[n].append(Hit(rel, i, stripped))
            suspicious_file = code or Path(rel).name in ("Dockerfile", "Makefile")
            if suspicious_file and len(s.suspicious) < 12 and SUSPICIOUS.search(stripped):
                s.suspicious.append(Hit(rel, i, stripped))
            names = [m.group(1) for m in ENV_READ.finditer(line)]
            names += [a or b for a, b in PROCESS_ENV.findall(line)]
            if m := PROCESS_ENV_DESTRUCT.search(line):
                names += re.findall(r"\b([A-Z][A-Z0-9_]{1,63})\b", m.group(1))
            if Path(rel).suffix in (".properties", ".yml", ".yaml"):
                names += PLACEHOLDER.findall(line)
            for name in names:
                if name not in PLATFORM_ENV:
                    hits = s.env.setdefault(name, [])
                    if len(hits) < 3:
                        hits.append(Hit(rel, i, stripped))
    _stack(s, texts)
    _stores(s, texts)
    _build_env(s, texts)
    if (root / "Dockerfile").is_file() and not (root / "Dockerfile").is_symlink():
        s.dockerfile = az.read_text(root / "Dockerfile")
    s.ms = (time.perf_counter() - t0) * 1000
    return s


def _first(texts: dict, rx: re.Pattern, files=None):
    for rel in files or texts:
        for i, line in enumerate(texts.get(rel, []), 1):
            if m := rx.search(line):
                return Hit(rel, i, line.strip()), m
    return None, None


def _stack(s: Scan, texts: dict) -> None:
    root = s.root
    pkg = _package_json(root)
    py_files = [f for f in texts if f.endswith(".py")]
    py_manifest = any((root / n).is_file() for n in ("requirements.txt", "pyproject.toml", "Pipfile"))
    node_server = any(
        d in {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})} for d in NODE_SERVER
    )
    if (root / "go.mod").is_file():
        _go(s, texts)
    elif any((root / n).is_file() for n in ("pom.xml", "build.gradle", "build.gradle.kts")):
        _java(s, texts)
    elif pkg and (
        node_server or not py_manifest
    ):  # a Python app may carry a package.json for frontend tooling
        _node(s, texts, pkg)
    elif py_files or py_manifest:
        _python(s, texts, py_files)
    elif (root / "index.html").is_file():
        s.language, s.static_output = "html", "."
    else:
        for marker, lang in (("Gemfile", "ruby"), ("composer.json", "php"), ("Cargo.toml", "rust")):
            if (root / marker).is_file():
                s.language = lang
        if s.language is None and any(f.endswith(".csproj") for f in s.files):
            s.language = "dotnet"
    # health route candidates (an existing GET path the verify step can use)
    _, m = _first(texts, re.compile(r"""['"](/(?:api/)?(?:health|healthz|ping|status))['"]"""))
    s.health = m.group(1) if m else None
    s.function = s.function or bool(
        _first(
            texts,
            re.compile(r"@functions_framework\.http|functions\.http\("),
            [f for f in texts if Path(f).suffix in CODE_EXT],
        )[0]
    )


def _python(s: Scan, texts: dict, py_files: list[str]) -> None:
    root = s.root
    s.language = "python"
    s.deps = _deps_python(root)
    for fw in (
        "fastapi",
        "flask",
        "django",
        "starlette",
        "quart",
        "sanic",
        "aiohttp",
        "streamlit",
        "gradio",
        "functions-framework",
    ):
        if fw in s.deps:
            s.framework = fw
            break
    if (root / "requirements.txt").is_file():
        s.install = "pip install --no-cache-dir -r requirements.txt"
    elif (root / "pyproject.toml").is_file():
        s.install = "pip install --no-cache-dir ."
    elif (root / "Pipfile").is_file():
        s.install = "pip install --no-cache-dir pipenv && pipenv install --system --deploy"
    for name in (".python-version", "runtime.txt"):
        if (root / name).is_file() and (m := re.search(r"(\d+\.\d+)", az.read_text(root / name))):
            s.version = m.group(1)
    extra: list[str] = []
    app_rx = re.compile(r"^(\w+)\s*(?::\s*\w+)?\s*=\s*(FastAPI|Starlette|Flask|Quart|Sanic)\(")
    for rel in py_files:
        for i, line in enumerate(texts[rel], 1):
            if m := app_rx.match(line):
                var, kind, mod = m.group(1), m.group(2), _module(rel)
                hit = Hit(rel, i, line.strip())
                if kind in ("FastAPI", "Starlette"):
                    s.starts.append((f"uvicorn {mod}:{var} --host 0.0.0.0 --port $PORT", hit))
                    if "uvicorn" not in s.deps:
                        extra.append("uvicorn")
                elif kind == "Flask" and "gunicorn" in s.deps:
                    s.starts.append((f"gunicorn --bind 0.0.0.0:$PORT {mod}:{var}", hit))
                elif kind == "Flask":
                    s.starts.append((f"flask --app {mod}:{var} run --host 0.0.0.0 --port $PORT", hit))
                else:
                    s.starts.append((f"python {rel}", hit))
    if (root / "manage.py").is_file():
        wsgi = next((f for f in py_files if f.endswith("/wsgi.py")), None)
        if wsgi and "gunicorn" in s.deps:
            s.starts.append((f"gunicorn --bind 0.0.0.0:$PORT {_module(wsgi)}:application", None))
        s.starts.append(("python manage.py runserver 0.0.0.0:$PORT", None))
    if s.framework == "streamlit":
        hit, _ = _first(texts, re.compile(r"import streamlit"), py_files)
        if hit:
            s.starts.insert(
                0, (f"streamlit run {hit.path} --server.port $PORT --server.address 0.0.0.0", hit)
            )
    if s.framework == "functions-framework":
        hit, m = _first(
            texts, re.compile(r"@functions_framework\.http\s*$|^def\s+(\w+)\s*\(\s*request\b"), py_files
        )
        fn_hit, fn = _first(texts, re.compile(r"^def\s+(\w+)\s*\("), [hit.path] if hit else py_files)
        if fn:
            s.starts.insert(0, (f"functions-framework --target={fn.group(1)} --port $PORT", fn_hit))
            s.function = True
    _procfile(s)
    for rel in py_files:
        if any("__main__" in line for line in texts[rel]):
            s.starts.append((f"python {rel}", None))
    s.starts = list({c: (c, h) for c, h in s.starts}.values())
    if extra and s.install:
        s.install += " && pip install --no-cache-dir " + " ".join(extra)
    s.port_env = bool(
        _first(
            texts,
            re.compile(r"""environ(?:\.get)?\s*[\[(]\s*['"]PORT['"]|getenv\(\s*['"]PORT['"]"""),
            py_files,
        )[0]
    )
    s.port_env = s.port_env or any("$PORT" in c for c, _ in s.starts)
    _, m = _first(texts, re.compile(r"port\s*=\s*(\d{4,5})\b"), py_files)
    s.port = int(m.group(1)) if m else None


def _procfile(s: Scan) -> None:
    p = s.root / "Procfile"
    if p.is_file():
        for i, line in enumerate(az.read_text(p).splitlines(), 1):
            if line.startswith("web:"):
                s.starts.insert(0, (line[4:].strip(), Hit("Procfile", i, line.strip())))


NODE_SERVER = (
    "express",
    "fastify",
    "koa",
    "@hapi/hapi",
    "hapi",
    "next",
    "nuxt",
    "@nestjs/core",
    "@remix-run/serve",
    "hono",
    "socket.io",
    "@sveltejs/adapter-node",
)
NODE_STATIC = {  # build tool -> output folder
    "vite": "dist",
    "react-scripts": "build",
    "@vue/cli-service": "dist",
    "parcel": "dist",
    "astro": "dist",
    "gatsby": "public",
    "@angular/cli": "dist",
}


def _node(s: Scan, texts: dict, pkg: dict) -> None:
    root = s.root
    s.language = "node"
    deps = {**(pkg.get("devDependencies") or {}), **(pkg.get("dependencies") or {})}
    s.deps = list(deps)[:40]
    scripts = pkg.get("scripts") or {}
    for fw in (
        "next",
        "nuxt",
        "@nestjs/core",
        "express",
        "fastify",
        "koa",
        "hono",
        "vite",
        "react-scripts",
        "astro",
        "@angular/core",
        "gatsby",
    ):
        if fw in deps:
            s.framework = fw.removeprefix("@nestjs/").replace("core", "nest") if fw == "@nestjs/core" else fw
            break
    if m := re.search(r"(\d+)", str((pkg.get("engines") or {}).get("node") or "")):
        s.version = m.group(1)
    if (root / "package-lock.json").is_file():
        s.install = "npm ci"
    elif (root / "yarn.lock").is_file():
        s.install = "corepack enable && yarn install --frozen-lockfile"
    elif (root / "pnpm-lock.yaml").is_file():
        s.install = "corepack enable && pnpm install --frozen-lockfile"
    else:
        s.install = "npm install"
    runner = "npm run" if s.install.startswith("npm") else s.install.split()[2]
    if "build" in scripts:
        s.build = f"{runner} build"
    server = any(d in deps for d in NODE_SERVER)
    tool = next((t for t in NODE_STATIC if t in deps), None)
    if tool and not server:
        s.static_output = NODE_STATIC[tool]
        if tool == "@angular/cli":
            s.static_output = f"dist/{pkg.get('name') or ''}".rstrip("/")
        return
    start = scripts.get("start") or ""
    if "next" in deps:
        s.starts.append(("npx next start -H 0.0.0.0 -p $PORT", None))
    if start and not re.search(r"react-scripts start|vite(?:\s|$)|ng serve|webpack serve", start):
        s.starts.append(("npm start" if runner == "npm run" else f"{runner} start", None))
    for f in [
        pkg.get("main"),
        "server.js",
        "index.js",
        "app.js",
        "main.js",
        "src/index.js",
        "src/server.js",
        "dist/main.js",
    ]:
        if f and (root / f).is_file():
            s.starts.append((f"node {f}", None))
    _procfile(s)
    s.starts = list({c: (c, h) for c, h in s.starts}.values())
    js = [f for f in texts if Path(f).suffix in (".js", ".mjs", ".cjs", ".ts")]
    s.port_env = (
        bool(_first(texts, re.compile(r"process\.env\.PORT|process\.env\[['\"]PORT"), js)[0])
        or "next" in deps
    )
    _, m = _first(texts, re.compile(r"\.listen\(\s*(\d{4,5})"), js)
    s.port = int(m.group(1)) if m else None


def _go(s: Scan, texts: dict) -> None:
    root = s.root
    s.language = "go"
    mod = az.read_text(root / "go.mod")
    if m := re.search(r"^go\s+(\d+\.\d+)", mod, re.MULTILINE):
        s.version = m.group(1)
    s.deps = re.findall(r"^\s*(?:require\s+)?([\w.\-]+/[\w.\-/]+)\s+v", mod, re.MULTILINE)[:30]
    for fw, name in (
        ("gin-gonic/gin", "gin"),
        ("labstack/echo", "echo"),
        ("gofiber/fiber", "fiber"),
        ("go-chi/chi", "chi"),
    ):
        if any(fw in d for d in s.deps):
            s.framework = name
            break
    pkg = "."
    if not (root / "main.go").is_file():
        mains = sorted(f for f in s.files if f.endswith("/main.go") and f.startswith("cmd/"))
        pkg = "./" + mains[0].rsplit("/", 1)[0] if mains else "."
    tidy = "go mod download" if (root / "go.sum").is_file() else "go mod tidy"
    s.build = f"{tidy} && CGO_ENABLED=0 go build -o /app/server {pkg}"
    s.starts.append(("/app/server", None))
    go = [f for f in texts if f.endswith(".go")]
    s.port_env = bool(_first(texts, re.compile(r"""Getenv\(\s*"PORT"\s*\)"""), go)[0])


def _java(s: Scan, texts: dict) -> None:
    root = s.root
    s.language = "java"
    pom, gradle = (
        root / "pom.xml",
        next((root / n for n in ("build.gradle.kts", "build.gradle") if (root / n).is_file()), None),
    )
    text = az.read_text(pom) if pom.is_file() else az.read_text(gradle) if gradle else ""
    if "spring-boot" in text:
        s.framework = "spring-boot"
    if m := re.search(
        r"<java\.version>(\d+)|JavaVersion\.VERSION_(\d+)|languageVersion\.set\(JavaLanguageVersion\.of\((\d+)|JavaLanguageVersion\.of\((\d+)",
        text,
    ):
        s.version = next(g for g in m.groups() if g)
    if pom.is_file():
        s.build = (
            "./mvnw -q -DskipTests package" if (root / "mvnw").is_file() else "mvn -q -DskipTests package"
        )
        jar = "target/*.jar"
    else:
        s.build = (
            "./gradlew -q bootJar -x test" if (root / "gradlew").is_file() else "gradle -q bootJar -x test"
        )
        jar = "$(ls build/libs/*.jar | grep -v plain | head -1)"
    s.starts.append((f"java -Dserver.port=$PORT -jar {jar}", None))
    s.port_env = True


def _stores(s: Scan, texts: dict) -> None:
    deps = " ".join(s.deps)
    for kind, rx in STORES.items():
        hits = [Hit("deps", 0, d) for d in s.deps if rx.fullmatch(d) or rx.search(d)][:2]
        for rel, lines in texts.items():
            if Path(rel).suffix not in CODE_EXT and not rel.endswith(".prisma"):
                continue
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if len(hits) >= 4:
                    break
                imports = re.match(
                    r"(import|from|require|const|use|using|#include)\b", stripped
                ) and rx.search(stripped)
                prisma = rx is STORES["postgres"] and re.search(r"provider\s*=\s*\"postgresql\"", stripped)
                if imports or prisma:
                    hits.append(Hit(rel, i, stripped))
        if hits and (kind != "s3" or "boto3" in deps or any(h.path != "deps" for h in hits)):
            s.stores[kind] = [h for h in hits if h.path != "deps"] or hits


def _build_env(s: Scan, texts: dict) -> None:
    """Values the build needs from outside: Dockerfile ARGs without a default, BuildKit secret ids,
    and ${VARS} in package-manager configs."""
    for rel, lines in texts.items():
        name = Path(rel).name
        for i, line in enumerate(lines, 1):
            if name == "Dockerfile":
                if m := re.match(r"\s*ARG\s+([A-Z][A-Z0-9_]*)\s*$", line):
                    s.build_env.setdefault(m.group(1), []).append(Hit(rel, i, line.strip()))
                for sid in re.findall(r"--mount=type=secret,id=([A-Za-z0-9_]+)", line):
                    s.build_env.setdefault(sid, []).append(Hit(rel, i, line.strip()))
    for cfg in (".npmrc", "settings.xml", "pip.conf"):
        p = s.root / cfg
        if p.is_file():
            for i, line in enumerate(az.read_text(p).splitlines(), 1):
                for var in PLACEHOLDER.findall(line):
                    s.build_env.setdefault(var, []).append(Hit(cfg, i, line.strip()))


# ---------- decide (< 1 s) ----------

STATE_FIELDS = [  # (signal, how it reads in the decision state)
    ("has_server", "server code"),
    ("is_spa", "static frontend"),
    ("long_running", "long connections / workers"),
    ("scheduler", "periodic jobs"),
    ("memory_state", "data kept in memory"),
    ("sqlite", "sqlite"),
    ("file_write", "writes to local disk"),
    ("gpu", "gpu"),
    ("system_packages", "os packages"),
    ("os_windows", "windows only"),
    ("os_macos", "macos only"),
]


def decision_state(s: Scan) -> str:
    """~100-250 tokens: every line here costs time inside the 1-second budget. Only what was found."""
    lines = [f"language: {s.language or 'unknown'}" + (f", framework: {s.framework}" if s.framework else "")]
    if s.deps:
        lines.append("dependencies: " + ", ".join(s.deps[:10]))
    if s.static_output and not s.starts:
        lines.append(f"build output is static files ({s.static_output})")
    if s.function:
        lines.append("has a functions-framework request handler")
    for n, label in STATE_FIELDS:
        if hits := s.signals.get(n):
            lines.append(f"{label}: " + " | ".join(h.brief(70) for h in hits[:2]))
    if s.stores:
        lines.append("databases: " + ", ".join(s.stores))
    return "\n".join(lines)[:1100]


def decide(s: Scan, ep: systemone.Endpoint) -> Decision:
    a = ep.ask(decision_state(s), {"service": DECIDE}, timeout=10)
    service, probs, conf = a.choice("service")
    why = []
    for n, label in (
        ("has_server", "서버 코드"),
        ("sqlite", "SQLite"),
        ("file_write", "디스크에 파일 쓰기"),
        ("memory_state", "메모리에 둔 데이터"),
        ("long_running", "오래 붙어 있는 연결·백그라운드 작업"),
        ("scheduler", "주기 작업"),
        ("gpu", "GPU"),
        ("system_packages", "OS 패키지"),
    ):
        if hits := s.signals.get(n):
            why.append(f"{label}: {hits[0].ref}")
    if s.stores:
        why.append("데이터베이스: " + ", ".join(s.stores))
    if s.static_output and not s.starts:
        why.append(f"빌드 결과가 정적 파일이에요 ({s.static_output})")
    kind = SERVICES[service][0]
    target = "function" if kind == 1 and s.function else az.TARGETS.get(kind, "cloudrun")
    return Decision(service, probs, conf, a.ms, s.ms, ep.name, a.input_tokens, why, target)


# The scan and the decision are reused by analyze() for the same folder (main.py emits the decision first)
_cache: dict[str, tuple[float, Scan, Decision | None]] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 600


def _cached(root: Path, ep: systemone.Endpoint | None, need_decision: bool) -> tuple[Scan, Decision | None]:
    key = str(root.resolve())
    with _cache_lock:
        hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL and (hit[2] or not need_decision):
        return hit[1], hit[2]
    s = hit[1] if hit and time.monotonic() - hit[0] < CACHE_TTL else scan(root)
    d = decide(s, ep) if (need_decision and ep) else None
    with _cache_lock:
        _cache[key] = (time.monotonic(), s, d)
        for k in [k for k, v in _cache.items() if time.monotonic() - v[0] > CACHE_TTL]:
            _cache.pop(k, None)
    return s, d


def ready() -> bool:
    return systemone.ready(systemone.clef())


def decision_for(src_dir: str) -> dict | None:
    """The service clef-flash picks for `src_dir` (payload for the dashboard), or None without clef-flash."""
    ep = systemone.clef()
    if not systemone.ready(ep):
        return None
    _, d = _cached(Path(src_dir), ep, need_decision=True)
    return d.payload() if d else None


# ---------- inspect: the analyzer's inspectors, answered from one batched call ----------


def _evidence(hits: list[Hit]) -> list[az.Evidence]:
    return [az.Evidence(evidence=h.ref, text=h.text) for h in hits if h.line > 0]


def _env_question(name: str, hits: list[Hit]) -> dict:
    code = " | ".join(systemone.redact(h.text)[:100] for h in hits[:2])
    return systemone.choice(
        f"What kind of value does the app read from {name}? Code: {code}",
        {k: v[0] for k, v in ENV_KINDS.items()},
    )


def _roles(kind: str, names: list[str]) -> list[az.EnvRole]:
    roles, used = [], set()
    for name in names:
        if not name.upper().startswith(STORE_ENV[kind]) and name != "DATABASE_URL":
            continue
        for suffix, role in ROLE_SUFFIX:
            if name.endswith(suffix) and role not in used:
                if kind != "s3" and role in ("bucket", "endpoint", "region", "access_key", "secret_key"):
                    break
                roles.append(az.EnvRole(role=role, name=name))
                used.add(role)
                break
    return roles


def make_inspector(reports: dict):
    """inspect(name) -> the same report models the Claude inspectors return, from answers asked up front."""
    return reports.__getitem__


def _ask_all(s: Scan, ep: systemone.Endpoint) -> dict:
    """Every inspector's report from one batched call (+ one for suspicious lines). Raises SystemOneError."""
    reports: dict = {}
    questions: dict = {}
    for n, hits in s.signals.items():
        if hits:
            questions[f"sig_{n}"] = systemone.noul(
                SIGNALS[n] + " Evidence: " + " | ".join(h.brief(90) for h in hits[:3])
            )
    env_names = sorted(s.env, key=lambda k: -len(s.env[k]))[:MAX_ENV_QUESTIONS]
    for name in env_names:
        questions[f"env_{name}"] = _env_question(name, s.env[name])
    if len(s.starts) > 1:
        questions["start"] = systemone.choice(
            "Which command starts this app's web server?", {c: None for c, _ in s.starts[:8]}
        )
    answers = ep.ask(decision_state(s), questions, timeout=60) if questions else None
    # signals: no candidate line -> "no" (that is the trade for speed; ANALYZER_MODE=full reads everything)
    for n in SIGNALS:
        hits = s.signals[n]
        if not hits:
            reports[n] = az.SignalReport(value="no", reason="관련 코드를 찾지 못했어요.", evidence=[])
            continue
        p = answers.noul(f"sig_{n}")
        keep = hits if p >= NO_KEEP else []
        reports[n] = az.SignalReport(
            value="yes" if p >= YES else "no",
            reason=f"clef-flash 판단 {p:.0%} ({hits[0].ref})",
            evidence=_evidence(keep),
            tools=[t for t in az.APT if any(re.search(t, h.text, I) for h in hits)]
            if n == "system_packages"
            else [],
        )
    # 정적 프런트엔드: a build tool with static output and no server is decided by the scan
    if s.static_output and not s.starts and not s.signals["has_server"]:
        reports["is_spa"] = az.SignalReport(
            value="yes", reason=f"빌드 결과가 정적 파일이에요 ({s.static_output}).", evidence=[]
        )
    start = s.starts[0][0] if s.starts else None
    if answers and "start" in answers.answers:
        start = answers.choice("start")[0]
    reports["stack"] = az.StackReport(
        language=s.language or "unknown",
        framework=s.framework,
        version=s.version,
        install=s.install,
        build=s.build,
        start=start,
        static_output=s.static_output if not s.starts else None,
        port_env="PORT" if s.port_env else None,
        port=s.port or (8080 if s.port_env else None),
        health=s.health,
        reason="코드와 매니페스트에서 찾았어요.",
        evidence=[],
    )
    env_items, kinds = [], {}
    for name in s.env:
        if f"env_{name}" in (answers.answers if answers else {}):
            kinds[name] = answers.choice(f"env_{name}")[0]
        _, kind, required, generate = ENV_KINDS[kinds.get(name, "opt")]
        env_items.append(
            az.EnvItem(
                name=name, kind=kind, required=required, generate=generate, evidence=_evidence(s.env[name])
            )
        )
    reports["env"] = az.EnvReport(items=env_items)
    reports["resources"] = az.ResourcesReport(
        items=[
            az.ResourceItem(kind=kind, env=_roles(kind, list(s.env)), evidence=_evidence(hits))
            for kind, hits in s.stores.items()
        ]
    )
    reports["build_env"] = az.BuildEnvReport(
        items=[
            az.BuildEnvItem(
                name=n,
                kind="secret" if re.search(r"TOKEN|SECRET|PASSWORD|KEY", n) else "config",
                reason="빌드할 때 밖에서 받아야 하는 값",
                evidence=_evidence(h),
            )
            for n, h in s.build_env.items()
        ]
    )
    reports["risks"] = az.RisksReport(items=[])
    reports["malicious"] = _malicious(s, ep)
    return reports


MALICIOUS_Q = (
    "Is this line malicious? It runs when our servers build or start an unknown repository. Malicious: mining, "
    "reverse shells, reading cloud metadata, sending environment variables or files to unknown hosts, running "
    "scripts downloaded from bare IPs or decoded at runtime. Not malicious: normal installs and tools the app "
    "uses. Line: "
)
MAX_SUSPICIOUS = 6


def _install_hooks(s: Scan) -> list[Hit]:
    """npm lifecycle scripts run during `npm install`, i.e. on our build machine. The line text is copied
    from the file as-is: analyze_with drops evidence that does not match the file."""
    scripts = _package_json(s.root).get("scripts") or {}
    hooks = [h for h in ("preinstall", "install", "postinstall", "prepare") if scripts.get(h)]
    if not hooks:
        return []
    lines = az.read_text(s.root / "package.json").splitlines()
    return [
        Hit("package.json", i, line.strip())
        for hook in hooks
        for i, line in enumerate(lines, 1)
        if f'"{hook}"' in line
    ]


def _malicious(s: Scan, ep: systemone.Endpoint):
    """One yes/no per suspicious line (tuned 5/5 on labeled lines; one question over all lines missed a
    lone postinstall hook). If clef-flash cannot answer, nothing is built: analyze() raises ModelDown."""
    lines = list({h.ref: h for h in [*_install_hooks(s), *s.suspicious]}.values())[:MAX_SUSPICIOUS]
    if not lines:
        return az.RisksReport(items=[])
    questions = {f"m{i}": systemone.noul(MALICIOUS_Q + h.brief(220)) for i, h in enumerate(lines)}
    a = ep.ask(
        "Code from an unknown repository that will be built and run on our servers.",
        questions,
        timeout=30,
    )
    found = [(h, a.noul(f"m{i}")) for i, h in enumerate(lines) if a.noul(f"m{i}") >= YES]
    return az.RisksReport(
        items=[
            az.Risk(
                title="앱 기능과 상관없는 위험한 코드",
                reason=f"clef-flash 판단 {p:.0%}",
                evidence=_evidence([h]),
            )
            for h, p in found
        ]
    )


def make_template_writer():
    """The template is the Dockerfile (no AI edits in the fast path). healer fixes it if the deploy fails."""

    def write(app: dict, port: int, draft: str | None) -> az.DockerfileReport:
        if draft is None:
            raise Unsupported(f"{app.get('language')}용 템플릿이 없어요")
        return az.DockerfileReport(dockerfile=draft, changes=[])

    return write


DOCKERFILE_SUSPICIOUS = SUSPICIOUS


def make_judge(ep: systemone.Endpoint):
    """Screens the repo's own Dockerfile: suspicious lines -> clef-flash yes/no -> which line."""

    def judge(numbered: str) -> az.DockerfileVerdict:
        rows = [line.split(": ", 1) for line in numbered.splitlines() if ": " in line]
        flagged = [(int(n), text) for n, text in rows if n.isdigit() and DOCKERFILE_SUSPICIOUS.search(text)]
        if not flagged:
            return az.DockerfileVerdict(findings=[])
        shown = " | ".join(f"line {n}: {t[:160]}" for n, t in flagged[:8])
        a = ep.ask(
            "A Dockerfile from an unknown repository, about to be built on our servers.",
            {
                "harmful": systemone.noul(
                    "Does any of these Dockerfile lines do something unrelated to building or running the app "
                    f"(mining, reverse shell, metadata access, sending data out, running downloaded scripts)? {shown}"
                ),
                "line": systemone.choice(
                    "Which line is the most harmful?", {str(n): None for n, _ in flagged[:8]}
                ),
            },
            timeout=30,
        )
        if a.noul("harmful") < YES:
            return az.DockerfileVerdict(findings=[])
        n = int(a.choice("line")[0])
        text = dict(flagged)[n]
        return az.DockerfileVerdict(
            findings=[az.DockerfileFinding(line=n, text=text, why=f"clef-flash 판단 {a.noul('harmful'):.0%}")]
        )

    return judge


def analyze(src_dir: str) -> AnalysisResult:
    """Same contract as analyzer.analyze, in a few seconds. Raises Unsupported when the fast path cannot
    build this repo (analyzer.analyze then falls back to the Claude inspectors if there is a key)."""
    ep = systemone.clef()
    if not systemone.ready(ep):
        raise ValueError(
            "clef-flash가 켜져 있지 않아요 (scripts/clef-up.sh로 켜고 CLEF_BASE_URL을 넣어 주세요)"
        )
    root = Path(src_dir).resolve()
    try:
        s, d = _cached(root, ep, need_decision=True)
    except systemone.SystemOneError as e:
        raise ModelDown(f"clef-flash가 답하지 않아요: {e}") from e
    if s.dockerfile is None and s.language not in az.BASE and not s.static_output:
        raise Unsupported(f"빠른 분석은 아직 {s.language or '이 언어'}용 Dockerfile을 만들지 못해요")
    if s.dockerfile is None and not s.static_output and not s.starts:
        raise Unsupported("시작 명령을 찾지 못했어요")
    try:
        reports = _ask_all(s, ep)
    except systemone.SystemOneError as e:
        raise ModelDown(f"clef-flash가 답하지 않아요: {e}") from e
    targets = {**az.TARGETS, 1: "function"} if s.function else az.TARGETS
    result = az.analyze_with(
        str(root),
        make_inspector(reports),
        make_template_writer(),
        make_judge(ep),
        prefer=d.prefer() if d else None,
        targets=targets,
    )
    if d:
        label = SERVICES[d.service][1]
        result.notes.insert(
            0,
            f"clef-flash가 {d.ms / 1000:.2f}초 만에 판단했어요: {label} (확률 {d.probabilities[d.service]:.0%}). "
            f"코드를 읽는 전처리는 {d.prep_ms:.0f}ms 걸렸어요.",
        )
    return result
