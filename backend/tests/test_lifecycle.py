"""Cloud SQL instance creation, schema migrations, and dropping databases only after a backup."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import deployer, nodes
from app.deployer import Config, Runner, StepError


def _app(path: Path, **files: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(body)
    return path


class _SqlRunner(Runner):
    """Runner whose `cloudsql instance` lookup fails until the instance was created."""

    def __init__(self, missing: bool):
        super().__init__()
        self.missing, self.commands = missing, []

    def run(self, name, cmd, **kw):
        self.commands.append(cmd)
        if name == "cloudsql create instance":
            self.missing = False
        if name == "cloudsql instance" and self.missing:
            return subprocess.CompletedProcess(
                cmd, 1, "", "ERROR: HTTPError 404: The Cloud SQL instance does not exist."
            )
        out = {"cloudsql instance": "RUNNABLE p:r:inst\n"}.get(name, "")
        return subprocess.CompletedProcess(cmd, 0, out, "")


def test_missing_instance_is_created_only_when_asked(monkeypatch):
    monkeypatch.setattr(
        deployer.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "", "")
    )
    with pytest.raises(StepError) as exc:
        deployer.ensure_cloudsql_database("gb", Config(project="p"), _SqlRunner(missing=True))
    assert exc.value.kind == "user_action_required" and "CLOUDSQL_AUTOCREATE=1" in exc.value.error
    r = _SqlRunner(missing=True)
    conn, _ = deployer.ensure_cloudsql_database("gb", Config(project="p", cloudsql_autocreate=True), r)
    create = next(c for c in r.commands if c[:4] == ["gcloud", "sql", "instances", "create"])
    assert {"--database-version=POSTGRES_16", "--edition=ENTERPRISE", "--tier=db-f1-micro"} <= set(create)
    assert conn == "p:r:inst"


@pytest.mark.parametrize(
    "files, command",
    [
        ({"alembic.ini": "[alembic]"}, ["alembic", "upgrade", "head"]),
        (
            {"manage.py": "from django.core.management import execute_from_command_line\n"},
            ["python", "manage.py", "migrate", "--noinput"],
        ),
        (
            {"prisma/schema.prisma": "x", "prisma/migrations/0_init/migration.sql": "x"},
            ["npx", "prisma", "migrate", "deploy"],
        ),
        ({"prisma/schema.prisma": "x"}, None),  # db push projects have no migrations to apply
        ({"manage.py": "print('a django-free script')"}, None),
    ],
)
def test_migration_tools_are_detected(tmp_path, files, command):
    found = deployer.detect_migration(_app(tmp_path, **files))
    assert (found[0] if found else None) == command


def test_migrations_need_a_database_and_can_be_turned_off(tmp_path):
    src = _app(tmp_path, **{"alembic.ini": "[alembic]"})
    assert deployer.migration_for(src, Config(), Runner(), has_database=False) is None
    assert deployer.migration_for(src, Config(migrate=False), Runner(), has_database=True) is None
    assert deployer.migration_for(src, Config(), Runner(), has_database=True) == [
        "alembic",
        "upgrade",
        "head",
    ]


def test_local_migration_overrides_the_entrypoint_and_keeps_values_off_the_command_line(monkeypatch):
    seen = []
    monkeypatch.setattr(
        Runner,
        "run",
        lambda self, name, cmd, **kw: (
            seen.append((cmd, kw.get("env"))) or subprocess.CompletedProcess(cmd, 0, "", "")
        ),
    )
    deployer.migrate_local(
        "img:1", ["alembic", "upgrade", "head"], {"DATABASE_URL": "postgresql://app:pw@db/app"}, Runner()
    )
    cmd, env = seen[0]
    assert cmd[-5:] == ["--entrypoint", "alembic", "img:1", "upgrade", "head"]
    assert "-e" in cmd and "DATABASE_URL" in cmd and not any("pw@" in c for c in cmd)
    assert env == {"DATABASE_URL": "postgresql://app:pw@db/app"}


def test_failed_migration_stops_the_release(monkeypatch):
    monkeypatch.setattr(
        Runner,
        "run",
        lambda self, name, cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "relation exists"),
    )
    with pytest.raises(StepError) as exc:
        deployer.migrate_local("img:1", ["alembic", "upgrade", "head"], {}, Runner())
    assert (
        exc.value.kind == "runtime_error"
        and "not started" in exc.value.error
        and "relation exists" in exc.value.app_logs
    )


def test_cloudrun_migration_job_uses_the_service_database_and_identity(monkeypatch):
    seen = []
    monkeypatch.setattr(
        Runner,
        "run",
        lambda self, name, cmd, **kw: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    monkeypatch.setattr(deployer, "_runtime_service_account", lambda *a: "sa@p.iam.gserviceaccount.com")
    deployer.migrate_cloudrun(
        "gb",
        "img:1",
        ["python", "manage.py", "migrate", "--noinput"],
        Config(project="p"),
        Runner(),
        conn="p:r:inst",
        db_secret=("DATABASE_URL", "cloudmorph-db-gb_x"),
        env={},
    )
    cmd = seen[0]
    assert cmd[:5] == ["gcloud", "run", "jobs", "deploy", "gb-migrate"]
    assert (
        cmd[cmd.index("--command") + 1] == "python"
        and cmd[cmd.index("--args") + 1] == "manage.py,migrate,--noinput"
    )
    assert {"--execute-now", "--wait", "--set-secrets=DATABASE_URL=cloudmorph-db-gb_x:latest"} <= set(cmd)
    assert cmd[cmd.index("--set-cloudsql-instances") + 1] == "p:r:inst"
    assert cmd[cmd.index("--service-account") + 1] == "sa@p.iam.gserviceaccount.com"


def test_sidecars_of_handles_old_and_new_deploys():
    assert deployer.sidecars_of({"db_container": "gb-db"}) == [
        ("postgres", "gb-db", "gb-dbdata")
    ]  # before engines
    assert deployer.sidecars_of(
        {"db_container": "q-db", "database": "mysql", "cache_container": "q-redis"}
    ) == [
        ("mysql", "q-db", "q-mysqldata"),
        ("redis", "q-redis", "q-redisdata"),
    ]


def _docker(monkeypatch, *, container: bool, volume: bool, dump_rc: int = 0):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(cmd, 0 if container else 1)
        if cmd[:3] == ["docker", "volume", "inspect"]:
            return subprocess.CompletedProcess(cmd, 0 if volume else 1)
        if cmd[:2] == ["docker", "exec"]:
            if kw.get("stdout"):
                kw["stdout"].write(b"-- dump\n")
            return subprocess.CompletedProcess(cmd, dump_rc, None, b"pg_dump: error")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(deployer.subprocess, "run", fake_run)
    return calls


def test_drop_dumps_then_removes_container_and_volume(monkeypatch, tmp_path):
    monkeypatch.setattr(deployer, "BACKUP_DIR", tmp_path)
    calls = _docker(monkeypatch, container=True, volume=True)
    done = deployer.drop_local_sidecars({"db_container": "gb-db", "database": "postgres"})
    (backup,) = tmp_path.iterdir()
    assert backup.read_text() == "-- dump\n" and oct(backup.stat().st_mode)[-3:] == "600"
    assert ["docker", "volume", "rm", "gb-dbdata"] in calls and done[0].startswith("backed up gb-db")


def test_drop_keeps_everything_when_the_backup_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(deployer, "BACKUP_DIR", tmp_path)
    calls = _docker(monkeypatch, container=True, volume=True, dump_rc=1)
    with pytest.raises(RuntimeError, match="backup failed"):
        deployer.drop_local_sidecars({"db_container": "gb-db"})
    assert not any(c[:2] in (["docker", "rm"], ["docker", "volume"]) and "rm" in c for c in calls)
    assert not list(tmp_path.iterdir())  # no half-written dump left behind


def test_drop_refuses_a_volume_without_its_container(monkeypatch, tmp_path):
    monkeypatch.setattr(deployer, "BACKUP_DIR", tmp_path)
    _docker(monkeypatch, container=False, volume=True)
    with pytest.raises(RuntimeError, match="cannot be dumped"):
        deployer.drop_local_sidecars({"db_container": "gb-db"})
    _docker(monkeypatch, container=False, volume=False)
    assert deployer.drop_local_sidecars({"db_container": "gb-db"}) == []  # already gone: idempotent


def test_cloudsql_drop_needs_a_backup_bucket(monkeypatch):
    handles = {"cloudsql_database": "gb_x", "cloudsql_connection": "p:r:inst"}
    monkeypatch.delenv("CLOUDSQL_BACKUP_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="CLOUDSQL_BACKUP_BUCKET"):
        deployer.drop_cloudsql_database(handles, Config(project="p"))
    calls = []
    monkeypatch.setenv("CLOUDSQL_BACKUP_BUCKET", "gs://backups/")
    monkeypatch.setattr(
        deployer.subprocess,
        "run",
        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    done = deployer.drop_cloudsql_database(handles, Config(project="p"))
    assert calls[0][:5] == ["gcloud", "sql", "export", "sql", "inst"] and calls[0][5].startswith(
        "gs://backups/gb_x-"
    )
    assert [c[:4] for c in calls[1:]] == [
        ["gcloud", "sql", "databases", "delete"],
        ["gcloud", "sql", "users", "delete"],
        ["gcloud", "secrets", "delete", "cloudmorph-db-gb_x"],
    ]
    assert len(done) == 4


def test_cloudsql_drop_stops_when_the_export_fails(monkeypatch):
    monkeypatch.setenv("CLOUDSQL_BACKUP_BUCKET", "backups")
    calls = []
    monkeypatch.setattr(
        deployer.subprocess,
        "run",
        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 1, "", "denied"),
    )
    with pytest.raises(RuntimeError, match="denied"):
        deployer.drop_cloudsql_database(
            {"cloudsql_database": "gb_x", "cloudsql_connection": "p:r:inst"}, Config(project="p")
        )
    assert len(calls) == 1  # nothing deleted


def test_node_drop_writes_the_dump_here_before_removing(monkeypatch, tmp_path):
    sent = []

    def fake_ssh(node, cmd, timeout=60, stdin=None):
        sent.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"-- dump\n", b"")

    monkeypatch.setattr(nodes, "ssh", fake_ssh)
    dest = tmp_path / "x.sql"
    nodes.drop_sidecar(nodes.Node(name="a", host="1"), "gb-db", "gb-dbdata", "pg_dump -U app app", dest)
    assert dest.read_bytes() == b"-- dump\n" and "docker volume rm gb-dbdata" in sent[1]
