"""Node pool: any SSH-reachable machine with Docker, on any cloud (GCP / Oracle / AWS / your own box).

A *node* is described in nodes.json (path: CLOUDMORPH_NODES_FILE, default backend/nodes.json):

    {"name": "gcp-node-1", "provider": "gcp", "host": "1.2.3.4", "user": "cloudmorph",
     "key": "~/.ssh/id_ed25519", "arch": "amd64", "ports": [8000, 8100]}

Everything goes over plain `ssh` + `docker`, so a node needs nothing installed beyond Docker and a user in
the docker group. Images are shipped with `docker save | ssh docker load` — no registry credentials on nodes.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]
DEFAULT_FILE = Path(__file__).resolve().parents[1] / "nodes.json"


@dataclass
class Node:
    name: str
    host: str
    user: str = "cloudmorph"
    key: str = "~/.ssh/id_ed25519"
    provider: str = "unknown"
    arch: str = "amd64"
    ports: tuple[int, int] = (8000, 8100)

    @property
    def platform(self) -> str:
        return {
            "amd64": "linux/amd64",
            "x86_64": "linux/amd64",
            "arm64": "linux/arm64",
            "aarch64": "linux/arm64",
        }[self.arch]

    def ssh_base(self) -> list[str]:
        return ["ssh", *SSH_OPTS, "-i", os.path.expanduser(self.key), f"{self.user}@{self.host}"]


@dataclass
class Router(Node):
    public_port: int = 80
    domain_suffix: str | None = None  # e.g. "apps.example.com"; None -> <app>.<ip>.nip.io

    def hostname_for(self, app: str) -> str:
        suffix = self.domain_suffix or f"{self.host}.nip.io"
        return f"{app}.{suffix}"


@dataclass
class Metrics:
    node: str
    ok: bool
    load1: float = 0.0
    cpus: int = 1
    mem_total_mb: int = 0
    mem_avail_mb: int = 0
    containers: int = 0
    error: str = ""

    @property
    def score(self) -> float:
        """Lower is better: normalised load + memory pressure + container count."""
        if not self.ok:
            return float("inf")
        mem_pressure = 1 - (self.mem_avail_mb / self.mem_total_mb) if self.mem_total_mb else 0
        return self.load1 / max(self.cpus, 1) + 0.5 * mem_pressure + 0.05 * self.containers


@dataclass
class Registry:
    nodes: list[Node] = field(default_factory=list)
    router: Router | None = None

    def get(self, name: str) -> Node:
        for n in self.nodes:
            if n.name == name:
                return n
        raise KeyError(f"unknown node: {name}")


def load_registry(path: str | os.PathLike | None = None) -> Registry:
    p = Path(path or os.environ.get("CLOUDMORPH_NODES_FILE") or DEFAULT_FILE)
    if not p.exists():
        return Registry()
    raw = json.loads(p.read_text())
    nodes = [Node(**{**n, "ports": tuple(n.get("ports", (8000, 8100)))}) for n in raw.get("nodes", [])]
    router = Router(**raw["router"]) if raw.get("router") else None
    return Registry(nodes=nodes, router=router)


# --------------------------------------------------------------------------- #
# SSH primitives
# --------------------------------------------------------------------------- #
def ssh(
    node: Node, command: str, *, timeout: int = 60, stdin: bytes | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*node.ssh_base(), command], input=stdin, capture_output=True, timeout=timeout, check=False
    )


def ssh_text(node: Node, command: str, *, timeout: int = 60) -> str:
    cp = ssh(node, command, timeout=timeout)
    if cp.returncode != 0:
        raise RuntimeError(
            f"ssh {node.name}: {command!r} failed rc={cp.returncode}: {cp.stderr.decode(errors='replace')[-800:]}"
        )
    return cp.stdout.decode(errors="replace")


_PROBE = (
    "echo L=$(cut -d' ' -f1 /proc/loadavg) C=$(nproc) "
    "MT=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo) MA=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) "
    "N=$(docker ps -q | wc -l)"
)


def probe(node: Node) -> Metrics:
    try:
        out = ssh_text(node, _PROBE, timeout=20)
        kv = dict(part.split("=", 1) for part in out.split())
        return Metrics(
            node=node.name,
            ok=True,
            load1=float(kv["L"]),
            cpus=int(kv["C"]),
            mem_total_mb=int(kv["MT"]),
            mem_avail_mb=int(kv["MA"]),
            containers=int(kv["N"]),
        )
    except Exception as e:  # noqa: BLE001 — an unreachable node is a valid (bad) measurement
        return Metrics(node=node.name, ok=False, error=f"{type(e).__name__}: {e}")


def select_node(
    reg: Registry,
    *,
    arch: str | None = None,
    exclude: set[str] = frozenset(),
    spread_from: set[str] = frozenset(),
) -> tuple[Node, list[Metrics]]:
    """Probe every eligible node and return the least loaded one (plus all measurements, for the UI).
    `spread_from`: providers the app already runs on. A reachable node at another provider wins over a
    less loaded one at the same provider, so replicas spread across clouds and one cloud's outage
    leaves the others serving."""
    candidates = [n for n in reg.nodes if n.name not in exclude and (arch is None or n.arch == arch)]
    if not candidates:
        raise RuntimeError("no eligible node in the pool")
    metrics = [probe(n) for n in candidates]
    pairs = list(zip(candidates, metrics, strict=True))
    fresh = [p for p in pairs if p[1].ok and p[0].provider not in spread_from]
    best = min(fresh or pairs, key=lambda pair: pair[1].score)
    if not best[1].ok:
        raise RuntimeError("no reachable node: " + "; ".join(f"{m.node}: {m.error}" for m in metrics))
    return best[0], metrics


# --------------------------------------------------------------------------- #
# Containers on a node
# --------------------------------------------------------------------------- #
def ship_image(node: Node, image: str, *, timeout: int = 600) -> float:
    """docker save (here) | ssh docker load (there). Returns seconds taken."""
    t0 = time.time()
    save = subprocess.Popen(["docker", "save", image], stdout=subprocess.PIPE)
    load = subprocess.run(
        [*node.ssh_base(), "docker load"],
        stdin=save.stdout,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    save.stdout.close()
    save.wait()
    if save.returncode != 0 or load.returncode != 0:
        raise RuntimeError(
            f"image transfer to {node.name} failed: {load.stderr.decode(errors='replace')[-800:]}"
        )
    return time.time() - t0


def relay_image(src: Node, dst: Node, image: str, *, timeout: int = 600) -> float:
    """Copy an image from one node to another *through this machine* (nodes never hold SSH keys).
    If the image is still present locally (we built it here), ship it straight from here instead."""
    t0 = time.time()
    local = (
        subprocess.run(["docker", "image", "inspect", image], capture_output=True, check=False).returncode
        == 0
    )
    if local:
        ship_image(dst, image, timeout=timeout)
        return time.time() - t0
    save = subprocess.Popen([*src.ssh_base(), f"docker save {shlex.quote(image)}"], stdout=subprocess.PIPE)
    load = subprocess.run(
        [*dst.ssh_base(), "docker load"], stdin=save.stdout, capture_output=True, timeout=timeout, check=False
    )
    save.stdout.close()
    save.wait()
    if save.returncode != 0 or load.returncode != 0:
        raise RuntimeError(
            f"image relay {src.name} -> {dst.name} failed: {load.stderr.decode(errors='replace')[-800:]}"
        )
    return time.time() - t0


def free_port_on(node: Node) -> int:
    lo, hi = node.ports
    used = ssh_text(node, "docker ps --format '{{.Ports}}'", timeout=20)
    taken = {int(p) for p in __import__("re").findall(r":(\d+)->", used)}
    for port in range(lo, hi + 1):
        if port not in taken:
            return port
    raise RuntimeError(f"no free port on {node.name} in {lo}-{hi}")


NETWORK = "cloudmorph"  # legacy shared network; every deploy now gets its own (app_network)


def app_network(app: str) -> str:
    """Docker network of one app: the app and its sidecars only. Docker does not route between bridge
    networks, so another app on the same host cannot reach this app's database even with its password.
    The router reaches apps through their published ports, not through these networks."""
    return f"cm-{app}"


def _network_sh(network: str) -> str:
    q = shlex.quote(network)
    return f"docker network inspect {q} >/dev/null 2>&1 || docker network create {q} >/dev/null; "


def run_container(
    node: Node,
    image: str,
    name: str,
    host_port: int,
    container_port: int,
    env: dict[str, str] | None = None,
    *,
    limits: list[str] | None = None,
    network: str = NETWORK,
) -> str:
    """`limits`: docker run flags such as ["--cpus", "1", "--memory", "512m"] (deployer.Size.docker_flags).
    `network`: the app's own network (app_network) so it shares a network with its sidecars only."""
    env_flags = " ".join(
        f"-e {shlex.quote(f'{k}={v}')}" for k, v in {**(env or {}), "PORT": str(container_port)}.items()
    )
    env_flags += "".join(f" {shlex.quote(flag)}" for flag in limits or [])
    cmd = (
        f"{_network_sh(network)}"
        f"docker rm -f {shlex.quote(name)} >/dev/null 2>&1; "
        f"docker run -d --restart unless-stopped --name {shlex.quote(name)} --network {shlex.quote(network)} "
        f"{env_flags} -p {host_port}:{container_port} {shlex.quote(image)}"
    )
    cp = ssh(node, cmd, timeout=60)
    if (
        cp.returncode != 0
    ):  # not ssh_text: its error repeats the command, and the command holds the env values
        err = cp.stderr.decode(errors="replace")[-800:]
        raise RuntimeError(f"ssh {node.name}: docker run {name} failed rc={cp.returncode}: {err}")
    return cp.stdout.decode(errors="replace").strip()


class SidecarRefused(RuntimeError):
    """A sidecar that cannot start without losing or exposing data; a person has to decide."""


@dataclass(frozen=True)
class Sidecar:
    """A database container next to the app on the app's own network (local and node targets).
    The password is random per sidecar and lives in the container's own env (`secret_env`), so a redeploy
    reads it back from `docker inspect` instead of storing it anywhere else."""

    engine: str
    image: str
    suffix: str  # container `<app>-<suffix>`
    volume: str  # volume `<app>-<volume>`
    data: str  # mount point of the volume
    secret_env: str
    url: str  # .format(scheme=, pw=, host=)
    scheme: str  # default URL scheme (the app's own form, e.g. mysql+pymysql, comes from the analyzer)
    port: int
    # sh inside the container, exit 0 once the *real* server takes the app's password over the network
    # (images first run a socket/localhost-only init server)
    ready: str
    env: tuple[str, ...] = ()
    entrypoint: str | None = None
    command: tuple[str, ...] = ()
    # sh that sets the password to $secret_env on a volume older than the container; None: cannot, so a
    # volume whose container is gone is refused (its password is lost with the container)
    reset: str | None = None
    ready_timeout: int = 90
    user: str = "app"
    database: str = "app"

    def conn(self, password: str, host: str, scheme: str | None = None) -> dict[str, str]:
        """Connection values by role (EnvRequirement.role): what the deployer fills into the app's env."""
        values = {"host": host, "port": str(self.port), "user": self.user, "password": password}
        values |= {"database": self.database}
        values["url"] = self.url.format(scheme=scheme or self.scheme, pw=password, host=host)
        if self.engine == "s3":
            values |= {
                "endpoint": values["url"],
                "bucket": "app",
                "access_key": self.user,
                "secret_key": password,
                "region": "us-east-1",
            }
        return values


SIDECARS = {
    "postgres": Sidecar(
        engine="postgres",
        image="postgres:16-alpine",
        suffix="db",
        volume="dbdata",
        data="/var/lib/postgresql/data",
        secret_env="POSTGRES_PASSWORD",
        env=("POSTGRES_USER=app", "POSTGRES_DB=app"),
        url="{scheme}://app:{pw}@{host}:5432/app",
        scheme="postgresql",
        port=5432,
        ready="pg_isready -h 127.0.0.1 -U app -q",
        # the image trusts connections from inside the container, so this needs no old password
        reset="psql -U app -d app -qc \"ALTER USER app PASSWORD '$POSTGRES_PASSWORD'\"",
    ),
    "mysql": Sidecar(
        engine="mysql",
        image="mysql:8.4",
        # not `<app>-db` (Postgres): an app may use both. A new volume name too, so a MySQL sidecar made
        # under the old `<app>-db` name never shares its data directory with this one.
        suffix="mysql",
        volume="mysql-data",
        data="/var/lib/mysql",
        secret_env="MYSQL_PASSWORD",
        env=("MYSQL_USER=app", "MYSQL_DATABASE=app", "MYSQL_RANDOM_ROOT_PASSWORD=yes"),
        url="{scheme}://app:{pw}@{host}:3306/app",
        scheme="mysql",
        port=3306,
        # older drivers (node `mysql`, pymysql without cryptography) cannot do 8.x's caching_sha2 auth
        command=("--mysql-native-password=ON", "--authentication-policy=mysql_native_password"),
        # over TCP: the first-boot init server listens on the socket only, then restarts
        ready='mysql -h127.0.0.1 -uapp -p"$MYSQL_PASSWORD" -e "SELECT 1" app >/dev/null 2>&1',
        ready_timeout=180,
    ),
    "redis": Sidecar(
        engine="redis",
        image="redis:7-alpine",
        suffix="redis",
        volume="redisdata",
        data="/data",
        secret_env="REDIS_PASSWORD",
        command=("sh", "-c", 'exec redis-server --appendonly yes --requirepass "$REDIS_PASSWORD"'),
        url="{scheme}://:{pw}@{host}:6379/0",
        scheme="redis",
        port=6379,
        ready='redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping | grep -q PONG',
        reset="true",  # the password comes from the command line on every start, not from the data
        user="",
        database="0",
    ),
    "mongodb": Sidecar(
        engine="mongodb",
        image="mongo:7",
        suffix="mongo",
        volume="mongodata",
        data="/data/db",
        secret_env="MONGO_INITDB_ROOT_PASSWORD",
        env=("MONGO_INITDB_ROOT_USERNAME=app",),
        url="{scheme}://app:{pw}@{host}:27017/app?authSource=admin",
        scheme="mongodb",
        port=27017,
        # the container's own address: the init server listens on localhost only
        ready='mongosh --quiet "mongodb://app:$MONGO_INITDB_ROOT_PASSWORD@$(hostname -i):27017/?authSource=admin" '
        "--eval 'db.runCommand({ping:1}).ok' >/dev/null 2>&1",
        ready_timeout=120,
    ),
    "s3": Sidecar(
        # SeaweedFS S3 gateway (minio/minio left Docker Hub). One identity, access key "app", secret = the
        # sidecar password; requests without it are refused. Bucket "app" is created once the filer is up.
        engine="s3",
        image="chrislusf/seaweedfs",
        suffix="s3",
        volume="s3data",
        data="/data",
        secret_env="S3_SECRET_KEY",
        url="{scheme}://{host}:8333",
        scheme="http",
        port=8333,
        entrypoint="sh",
        command=(
            "-c",
            (
                'printf \'{"identities":[{"name":"app","credentials":[{"accessKey":"app","secretKey":"%s"}],'
                '"actions":["Admin","Read","Write","List","Tagging"]}]}\' "$S3_SECRET_KEY" > /tmp/s3.json '
                "&& exec weed server -s3 -dir=/data -s3.config=/tmp/s3.json"
            ),
        ),
        ready="echo 's3.bucket.create -name app' | weed shell >/dev/null 2>&1; "
        "echo s3.bucket.list | weed shell 2>/dev/null | grep -qw app && "
        "wget -q -S --spider http://127.0.0.1:8333/ 2>&1 | grep -q HTTP/",
        reset="true",  # the key is written from the env on every start
        database="",
    ),
}


def sidecar_script(sc: Sidecar, app: str, password: str) -> str:
    """sh that starts (or reuses) the sidecar and prints the password it runs with."""
    name, volume, q = f"{app}-{sc.suffix}", f"{app}-{sc.volume}", shlex.quote
    net = app_network(app)
    run = " ".join(
        [
            f"docker run -d --restart unless-stopped --name {q(name)} --network {q(net)}",
            f"-e {q(f'{sc.secret_env}={password}')}",
            *(f"-e {q(e)}" for e in sc.env),
            f"--entrypoint {q(sc.entrypoint)}" if sc.entrypoint else "",
            f"-v {q(f'{volume}:{sc.data}')} {q(sc.image)}",
            *(q(c) for c in sc.command),
        ]
    )
    orphan = (
        ""
        if sc.reset
        else f"if docker volume inspect {q(volume)} >/dev/null 2>&1; then echo {q(orphan_volume_message(sc, app))} >&2; exit 3; fi; "
    )
    reset = f'[ -n "$fresh" ] && docker exec {q(name)} sh -c {q(sc.reset)} >/dev/null; ' if sc.reset else ""
    # A sidecar made before per-app networks sits on the shared one: join it to the app's network too
    # (connect fails harmlessly when it is already there).
    return (
        f"{_network_sh(net)}"
        f"fresh=; if docker inspect {q(name)} >/dev/null 2>&1; then "
        # a container of that name running another image is someone else's data: never start or reuse it
        f"img=$(docker inspect -f '{{{{.Config.Image}}}}' {q(name)}); [ \"$img\" = {q(sc.image)} ] || "
        f'{{ echo {q(name)} runs "$img", not {q(sc.image)} >&2; exit 3; }}; '
        f"docker start {q(name)} >/dev/null; "
        f"docker network connect {q(net)} {q(name)} >/dev/null 2>&1 || true; "
        f"else {orphan}{run} >/dev/null && fresh=1; fi; "
        f"ok=; for i in $(seq 1 {sc.ready_timeout}); do docker exec {q(name)} sh -c {q(sc.ready)} && ok=1 && break; sleep 1; done; "
        f'[ -n "$ok" ] || {{ echo {q(f"{sc.engine} sidecar {name} did not become ready")} >&2; exit 1; }}; '
        f"{reset}"
        f"docker inspect -f '{{{{range .Config.Env}}}}{{{{println .}}}}{{{{end}}}}' {q(name)} | sed -n 's/^{sc.secret_env}=//p'"
    )


def orphan_volume_message(sc: Sidecar, app: str) -> str:
    return (
        f"volume {app}-{sc.volume} holds {sc.engine} data but its container {app}-{sc.suffix} is gone, so its "
        f"password is lost; restore the container or remove the volume (docker volume rm {app}-{sc.volume})"
    )


def ensure_sidecar(node: Node, app: str, engine: str, *, scheme: str | None = None) -> dict[str, str]:
    """Start (or reuse) the `engine` sidecar on the app's network on the node; return its connection
    values by role (Sidecar.conn). Data lives in a named volume, so redeploys of the app keep their rows."""
    import secrets

    sc = SIDECARS[engine]
    cp = ssh(node, sidecar_script(sc, app, secrets.token_hex(16)), timeout=sc.ready_timeout + 60)
    err = cp.stderr.decode(errors="replace").strip()
    if cp.returncode == 3:
        raise SidecarRefused(err)
    if cp.returncode != 0:  # not ssh_text: its error repeats the command, and the command holds the password
        raise RuntimeError(f"ssh {node.name}: {engine} sidecar failed rc={cp.returncode}: {err[-800:]}")
    out = cp.stdout.decode(errors="replace").strip()
    password = out.splitlines()[-1] if out else ""
    return sc.conn(password, f"{app}-{sc.suffix}", scheme)


def ensure_postgres(node: Node, app: str) -> str:
    return ensure_sidecar(node, app, "postgres")["url"]


def drop_sidecar(node: Node, container: str, volume: str, dump: str | None, dest: Path | None) -> None:
    """Dump the sidecar's database to `dest` on this machine (when `dump` is given), then remove the
    container and its volume. A failed dump raises before anything is removed."""
    q = shlex.quote
    if dump and dest is not None:
        cp = ssh(
            node,
            f"docker start {q(container)} >/dev/null && docker exec {q(container)} sh -c {q(dump)}",
            timeout=900,
        )
        if cp.returncode != 0:
            raise RuntimeError(
                f"not dropping {container} on {node.name}: backup failed: {cp.stderr.decode(errors='replace')[-300:]}"
            )
        dest.write_bytes(cp.stdout)
    ssh(
        node,
        f"docker rm -f {q(container)} >/dev/null 2>&1; docker volume rm {q(volume)} >/dev/null 2>&1",
        timeout=60,
    )


def run_once(
    node: Node, image: str, argv: list[str], env: dict[str, str], *, network: str = NETWORK
) -> tuple[int, str]:
    """`docker run --rm` on the app's network (migrations); (returncode, output)."""
    env_flags = " ".join(f"-e {shlex.quote(f'{k}={v}')}" for k, v in env.items())
    cmd = (
        f"{_network_sh(network)}"
        f"docker run --rm --network {shlex.quote(network)} {env_flags} --entrypoint {shlex.quote(argv[0])} "
        f"{shlex.quote(image)} {' '.join(shlex.quote(a) for a in argv[1:])} 2>&1"
    )
    cp = ssh(node, cmd, timeout=600)
    return cp.returncode, cp.stdout.decode(errors="replace")


def container_oom_killed(node: Node, name: str) -> bool:
    cp = ssh(node, f"docker inspect -f '{{{{.State.OOMKilled}}}}' {shlex.quote(name)}", timeout=20)
    return cp.returncode == 0 and cp.stdout.decode().strip() == "true"


_ENV_SPLIT = "--cloudmorph-image-env--"


def container_env(node: Node, name: str) -> dict[str, str]:
    """The env a running container was started with, minus what its image already sets (PATH, ...).
    Scale-out reads it back from the primary replica, so API keys and DATABASE_URL reach every replica
    without CloudMorph keeping a copy of them anywhere."""
    q = shlex.quote(name)
    fmt = "'{{range .Config.Env}}{{println .}}{{end}}'"
    cp = ssh(
        node,
        f"docker inspect -f {fmt} {q} && echo {_ENV_SPLIT} && "
        f"docker image inspect -f {fmt} $(docker inspect -f '{{{{.Image}}}}' {q})",
        timeout=20,
    )
    if cp.returncode != 0:
        raise RuntimeError(
            f"cannot read env of {name} on {node.name}: {cp.stderr.decode(errors='replace')[-300:]}"
        )
    started, _, image = cp.stdout.decode(errors="replace").partition(_ENV_SPLIT)
    baked = {line for line in image.splitlines() if line}
    pairs = (line.split("=", 1) for line in started.splitlines() if "=" in line and line not in baked)
    return {k: v for k, v in pairs if k != "PORT"}


def container_status(node: Node, name: str) -> tuple[str, int]:
    cp = ssh(
        node,
        f"docker inspect -f '{{{{.State.Status}}}} {{{{.State.ExitCode}}}}' {shlex.quote(name)}",
        timeout=20,
    )
    if cp.returncode != 0:
        return "missing", -1
    status, code = cp.stdout.decode().split()
    return status, int(code)


def container_logs(node: Node, name: str, tail: int = 200) -> str:
    cp = ssh(node, f"docker logs --tail {tail} {shlex.quote(name)} 2>&1", timeout=30)
    return cp.stdout.decode(errors="replace")[-6000:]


def container_cpu_percent(node: Node, name: str) -> float | None:
    cp = ssh(node, f"docker stats --no-stream --format '{{{{.CPUPerc}}}}' {shlex.quote(name)}", timeout=30)
    if cp.returncode != 0:
        return None
    try:
        return float(cp.stdout.decode().strip().rstrip("%"))
    except ValueError:
        return None


def container_cpu_usage_usec(node: Node, name: str) -> int | None:
    """Cumulative CPU time of the container (cgroup v2 cpu.stat). Two readings -> exact average CPU% over
    the interval, unlike `docker stats` which is a 1-second snapshot."""
    cp = ssh(
        node,
        f"docker exec {shlex.quote(name)} cat /sys/fs/cgroup/cpu.stat 2>/dev/null | awk '/^usage_usec/{{print $2}}'",
        timeout=20,
    )
    try:
        return int(cp.stdout.decode().strip())
    except ValueError:
        return None


def remove_container(node: Node, name: str) -> bool:
    return ssh(node, f"docker rm -f {shlex.quote(name)}", timeout=30).returncode == 0


def remove_network(node: Node, network: str) -> bool:
    """Drop an app's network once nothing is attached (Docker refuses while a sidecar still is, which is
    what keeps a kept database reachable). Docker's default pools allow only ~30 bridge networks per host."""
    return ssh(node, f"docker network rm {shlex.quote(network)}", timeout=30).returncode == 0


def metrics_dict(ms: list[Metrics]) -> list[dict]:
    return [{**asdict(m), "score": None if m.score == float("inf") else round(m.score, 3)} for m in ms]
