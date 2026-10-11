"""Secrets for provisioned databases, MySQL/Redis sidecars and env values off the command line."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from app import deployer
from app.deployer import Config, Outcome, Runner, StepError


def _app(path: Path, **files: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (path / name).write_text(body)
    return path


def test_env_vars_file_is_private_and_removed(tmp_path):
    with deployer.env_vars_file({"API_KEY": "s3cret", "N": 1}) as path:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert Path(path).read_text() == '{"API_KEY": "s3cret", "N": "1"}'
    assert not Path(path).exists()
    with deployer.env_vars_file({}) as none:
        assert none is None


def _stub_cloudrun(monkeypatch, *, secret: str | None, prev=None):
    commands: list[list[str]] = []
    files: list[str] = []
    for fn in ("build_image", "push_image"):
        monkeypatch.setattr(deployer, fn, lambda *a, **k: None)
    monkeypatch.setattr(deployer, "serving_revision", lambda *a, **k: prev)
    monkeypatch.setattr(deployer, "latest_revision", lambda *a, **k: "svc-00002-new")
    monkeypatch.setattr(deployer, "tagged_url", lambda *a, **k: "https://candidate---svc.run.app")
    monkeypatch.setattr(deployer, "wait_up", lambda *a, **k: (True, "HTTP 200"))
    monkeypatch.setattr(deployer, "service_owner", lambda *a, **k: None)
    monkeypatch.setattr(deployer, "_revision_env", lambda *a, **k: None)
    url = "postgresql://gb_x:pw@/gb_x?host=/cloudsql/p:r:inst"
    monkeypatch.setattr(deployer, "ensure_cloudsql_database", lambda *a, **k: ("p:r:inst", url))
    monkeypatch.setattr(deployer, "mount_db_secret", lambda *a, **k: secret)

    def fake_run(self, name, cmd, **_):
        commands.append(cmd)
        if "--env-vars-file" in cmd:
            files.append(Path(cmd[cmd.index("--env-vars-file") + 1]).read_text())
        return subprocess.CompletedProcess(cmd, 0, "https://svc.run.app", "")

    monkeypatch.setattr(Runner, "run", fake_run)
    return commands, files, url


def test_cloudrun_mounts_database_url_from_secret_manager(monkeypatch, tmp_path):
    commands, files, url = _stub_cloudrun(monkeypatch, secret="cloudmorph-db-gb_x")
    src = _app(tmp_path / "gb", **{"requirements.txt": "psycopg"})
    out = Outcome(target="cloudrun")
    cfg = Config(project="p", service="svc", tag="t", env={"API_KEY": "k"})
    deployer.deploy_cloudrun(src, cfg, Runner(), out)
    deploy = next(c for c in commands if "deploy" in c)
    assert (
        "--set-secrets=DATABASE_URL=cloudmorph-db-gb_x:latest" in deploy and "--clear-secrets" not in deploy
    )
    assert files == ['{"API_KEY": "k"}']  # DATABASE_URL is not a plain env var any more
    assert not any(
        url in part or "API_KEY=k" in part for part in deploy
    )  # nothing secret on the command line
    assert out.handles["db_secret"] == "cloudmorph-db-gb_x"


def test_cloudrun_falls_back_to_a_plain_env_var_and_clears_old_secrets(monkeypatch, tmp_path):
    commands, files, url = _stub_cloudrun(monkeypatch, secret=None)
    src = _app(tmp_path / "gb", **{"requirements.txt": "psycopg"})
    deployer.deploy_cloudrun(
        src, Config(project="p", service="svc", tag="t"), Runner(), Outcome(target="cloudrun")
    )
    deploy = next(c for c in commands if "deploy" in c)
    assert "--clear-secrets" in deploy and files == [f'{{"DATABASE_URL": "{url}"}}']


def test_cloudrun_without_env_clears_what_an_older_revision_set(monkeypatch, tmp_path):
    commands, files, _ = _stub_cloudrun(monkeypatch, secret=None)
    src = _app(tmp_path / "web", **{"requirements.txt": "flask"})
    deployer.deploy_cloudrun(
        src, Config(project="p", service="svc", tag="t"), Runner(), Outcome(target="cloudrun")
    )
    deploy = next(c for c in commands if "deploy" in c)
    assert "--clear-env-vars" in deploy and not files


def test_cloudrun_refuses_mysql_instead_of_handing_it_postgres(monkeypatch, tmp_path):
    _stub_cloudrun(monkeypatch, secret=None)
    src = _app(tmp_path / "my", **{"requirements.txt": "pymysql"})
    with pytest.raises(StepError) as exc:
        deployer.deploy_cloudrun(
            src, Config(project="p", service="svc", tag="t"), Runner(), Outcome(target="cloudrun")
        )
    assert exc.value.kind == "user_action_required" and "mysql" in exc.value.error


def test_cloudrun_warns_when_redis_cannot_be_provided(monkeypatch, tmp_path):
    _stub_cloudrun(monkeypatch, secret=None)
    src = _app(
        tmp_path / "q", **{"app.py": "import os\nR = os.environ['REDIS_URL']", "requirements.txt": "redis"}
    )
    out = Outcome(target="cloudrun")
    deployer.deploy_cloudrun(src, Config(project="p", service="svc", tag="t"), Runner(), out)
    assert any("REDIS_URL" in w for w in out.warnings)


def _gcloud(monkeypatch, replies: dict):
    calls: list[tuple[list[str], str | None]] = []

    def fake_run(cmd, input=None, **kw):
        calls.append((cmd, input))
        for key, (rc, out) in replies.items():
            if key in " ".join(cmd):
                return subprocess.CompletedProcess(cmd, rc, out, "" if rc == 0 else "ERROR")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(deployer.subprocess, "run", fake_run)
    return calls


URL = "postgresql://gb_x:pw@/gb_x?host=/cloudsql/p:r:inst"


def test_mount_grants_the_runtime_account_and_never_passes_the_value_as_an_argument(monkeypatch):
    calls = _gcloud(
        monkeypatch,
        {
            "versions access latest --secret=cloudmorph-db-gb_x": (0, URL),
            "spec.template.spec.serviceAccountName": (1, ""),
            "projects describe": (0, "123456\n"),
        },
    )
    r = Runner()
    assert (
        deployer.mount_db_secret("svc", URL, Config(project="p", db_secret_mount=True), r)
        == "cloudmorph-db-gb_x"
    )
    grant = next(c for c, _ in calls if "add-iam-policy-binding" in c)
    assert "--member=serviceAccount:123456-compute@developer.gserviceaccount.com" in grant
    assert not any("pw@" in part for c, _ in calls for part in c)


def test_mount_is_off_unless_each_app_has_its_own_account(monkeypatch):
    """Apps share DEPLOYER_APP_SERVICE_ACCOUNT: granting it one app's secret would let every app read it."""
    calls = _gcloud(monkeypatch, {"versions access latest": (0, URL)})
    assert Config(project="p").db_secret_mount is False
    assert deployer.mount_db_secret("svc", URL, Config(project="p"), Runner()) is None
    assert not any("add-iam-policy-binding" in c for c, _ in calls)


def test_runtime_account_is_the_app_service_account_deploy_uses(monkeypatch):
    monkeypatch.setattr(deployer.subprocess, "run", lambda *a, **k: pytest.fail("no lookup needed"))
    cfg = Config(project="p", app_service_account="cloudmorph-apps")
    assert deployer._runtime_service_account("svc", cfg) == "cloudmorph-apps@p.iam.gserviceaccount.com"


def test_mount_refuses_a_stale_secret(monkeypatch):
    _gcloud(
        monkeypatch, {"versions access latest": (0, "postgresql://gb_x:OLD@/gb_x?host=/cloudsql/p:r:inst")}
    )
    assert deployer.mount_db_secret("svc", URL, Config(project="p"), Runner()) is None
    assert deployer.mount_db_secret("svc", URL, Config(project="p", db_secret=False), Runner()) is None


def test_remember_creates_the_secret_on_first_use_through_stdin(monkeypatch):
    calls = _gcloud(monkeypatch, {"versions add": (1, "")})
    monkeypatch.setattr(
        deployer.subprocess,
        "run",
        lambda cmd, input=None, **kw: (
            calls.append((cmd, input))
            or subprocess.CompletedProcess(
                cmd, 1 if "add" in cmd else 0, "", "NOT_FOUND: Secret [x] not found" if "add" in cmd else ""
            )
        ),
    )
    assert deployer.remember_db_url(Config(project="p"), Runner(), "gb_x", URL)
    create = calls[-1]
    assert create[0][:4] == ["gcloud", "secrets", "create", "cloudmorph-db-gb_x"] and create[1] == URL


def test_cloudsql_reuses_the_password_kept_in_secret_manager(monkeypatch):
    """A mounted secret hides the URL from the revision env, and another machine has no serving URL at hand:
    the stored password is reused instead of resetting it under the live revision."""
    calls = _gcloud(monkeypatch, {"versions access latest --secret=cloudmorph-db-gb_x": (0, URL)})

    class R(Runner):
        def run(self, name, cmd, **kw):
            out = {
                "cloudsql instance": "RUNNABLE p:r:inst\n",
                "cloudsql databases": "gb_x\n",
                "cloudsql users": "gb_x\n",
            }
            return subprocess.CompletedProcess(cmd, 0, out.get(name, ""), "")

    monkeypatch.setattr(deployer, "db_identifier", lambda app, owner: "gb_x")
    assert deployer.ensure_cloudsql_database("gb", Config(project="p"), R()) == ("p:r:inst", URL)
    assert not [c for c, _ in calls if c[:3] == ["gcloud", "sql", "users"]]


def test_redis_and_mysql_are_detected_with_the_scheme_the_code_uses(tmp_path):
    src = _app(
        tmp_path,
        **{
            "app.py": 'import os\nURL = os.getenv("DATABASE_URL", "mysql+pymysql://u:p@localhost/db")\n'
            'R = os.environ["REDIS_URL"]',
            "requirements.txt": "sqlalchemy\npymysql\nredis",
        },
    )
    r = Runner()
    assert deployer.database_for(src, Config(), r) == "mysql"
    assert deployer.url_scheme(src, "mysql") == "mysql+pymysql"
    assert deployer.cache_for(src, Config(), r) == "redis"
    assert deployer.cache_for(src, Config(cache=False), r) is None
    assert deployer.url_scheme(src, "postgres") == "postgresql"


def _req(name, role, kind="postgres", template=None):
    from app.schemas import EnvRequirement

    return EnvRequirement(
        name=name, scope="runtime", secret=False, resource=kind, role=role, template=template
    )


def _conn(kind, password="pw", app="shop"):
    from app import nodes

    sc = nodes.SIDECARS[kind]
    return sc.conn(password, f"{app}-{sc.suffix}")


def test_without_an_analysis_the_code_scan_guesses_database_and_redis(tmp_path):
    src = _app(
        tmp_path / "q-local",
        **{
            "app.py": "import os\nos.environ['DATABASE_URL']\nos.environ['REDIS_URL']",
            "requirements.txt": "psycopg\nredis",
        },
    )
    got = deployer.store_settings(src, Config(), Runner())
    assert {k: [(e.name, e.role) for e in v] for k, v in got.items()} == {
        "postgres": [("DATABASE_URL", "url")],
        "redis": [("REDIS_URL", "url")],
    }
    # the analyzer's stores replace the guess, even when it found none
    assert deployer.store_settings(src, Config(stores=[]), Runner()) == {}
    # the user's own value wins
    assert "postgres" not in deployer.store_settings(
        src, Config(env={"DATABASE_URL": "postgresql://mine"}), Runner()
    )


def test_store_settings_respect_switches_and_dashboard_new(tmp_path):
    redis, pg = [_req("REDIS_HOST", "host", "redis")], [_req("DB_URL", "url")]
    assert deployer.store_settings(tmp_path, Config(stores=redis), Runner()) == {"redis": redis}
    assert deployer.store_settings(tmp_path, Config(stores=redis, cache=False), Runner()) == {}
    assert deployer.store_settings(tmp_path, Config(stores=pg, skip_stores=("postgres",)), Runner()) == {}
    # dashboard "new Postgres" on an app whose analysis found no store: DATABASE_URL is made
    made = deployer.store_settings(tmp_path, Config(stores=[], database="postgres"), Runner())
    assert [(e.name, e.role) for e in made["postgres"]] == [("DATABASE_URL", "url")]


def test_settings_are_filled_by_role_in_the_apps_own_names():
    mysql = [
        _req("DB_HOST", "host", "mysql"),
        _req("DB_PORT", "port", "mysql"),
        _req("DB_PASS", "password", "mysql"),
        _req("SPRING_DATASOURCE_URL", "url", "mysql", "jdbc:mysql://{host}:{port}/{database}"),
    ]
    assert deployer.fill_store_env(mysql, _conn("mysql")) == {
        "DB_HOST": "shop-mysql",
        "DB_PORT": "3306",
        "DB_PASS": "pw",
        "SPRING_DATASOURCE_URL": "jdbc:mysql://shop-mysql:3306/app",
    }
    s3 = [
        _req("S3_BUCKET", "bucket", "s3"),
        _req("S3_KEY", "access_key", "s3"),
        _req("S3_SECRET", "secret_key", "s3"),
    ]
    assert deployer.fill_store_env(s3, _conn("s3")) == {
        "AWS_ENDPOINT_URL_S3": "http://shop-s3:8333",  # SDKs reach it even without an endpoint setting
        "S3_BUCKET": "app",
        "S3_KEY": "app",
        "S3_SECRET": "pw",
    }
    mongo = deployer.fill_store_env([_req("MONGO_URL", "url", "mongodb")], _conn("mongodb"))
    assert mongo == {"MONGO_URL": "mongodb://app:pw@shop-mongo:27017/app?authSource=admin"}
    async_pg = "postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"
    assert deployer.fill_store_env([_req("DB", "url", template=async_pg)], _conn("postgres")) == {
        "DB": "postgresql+asyncpg://app:pw@shop-db:5432/app"
    }


def test_cloudsql_socket_urls_follow_the_apps_form():
    conn = deployer.cloudsql_conn("p:r:i", "postgresql://u:pw@/d?host=/cloudsql/p:r:i")
    assert (conn["host"], conn["user"], conn["password"], conn["database"]) == (
        "/cloudsql/p:r:i",
        "u",
        "pw",
        "d",
    )
    asyncpg = "postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"
    assert deployer.render_url(asyncpg, conn) == "postgresql+asyncpg://u:pw@/d?host=/cloudsql/p:r:i"
    assert deployer.render_url("jdbc:postgresql://{host}:{port}/{database}", conn) is None  # JDBC: no socket
    assert deployer.render_url("x://{nope}", conn) is None


def test_local_deploy_fills_each_store_into_the_apps_own_names(monkeypatch, tmp_path):
    seen = []
    for fn in ("build_image",):
        monkeypatch.setattr(deployer, fn, lambda *a, **k: None)
    monkeypatch.setattr(deployer, "cleanup_local", lambda handles, **kw: [])
    monkeypatch.setattr(deployer, "wait_up", lambda *a, **k: (True, ""))
    monkeypatch.setattr(
        deployer,
        "ensure_local_sidecar",
        lambda app, kind, r, scheme=None: seen.append(kind) or _conn(kind, app=app),
    )
    runs = []
    monkeypatch.setattr(
        Runner,
        "run",
        lambda self, name, cmd, **kw: (
            runs.append((cmd, kw.get("env"))) or subprocess.CompletedProcess(cmd, 0, "cid", "")
        ),
    )
    stores = [
        _req("DB_HOST", "host", "mysql"),
        _req("DB_PASS", "password", "mysql"),
        _req("CACHE", "url", "redis"),
    ]
    out = Outcome(target="local")
    deployer.deploy_local(
        _app(tmp_path / "shop"), Config(tunnel=False, stores=stores, env={"API_KEY": "k"}), Runner(), out
    )
    assert seen == ["mysql", "redis"]
    _, env = next((c, e) for c, e in runs if c[:2] == ["docker", "run"])
    assert env == {
        "API_KEY": "k",
        "DB_HOST": "shop-mysql",
        "DB_PASS": "pw",
        "CACHE": "redis://:pw@shop-redis:6379/0",
    }
    assert [s["container"] for s in out.handles["sidecars"]] == ["shop-mysql", "shop-redis"]
    assert out.handles["database"] == "mysql" and out.handles["cache_container"] == "shop-redis"


def test_unmakeable_stores_are_left_to_the_user_with_a_warning(monkeypatch, tmp_path):
    out = Outcome(target="local")
    env: dict = {}
    deployer._attach_stores(
        tmp_path,
        Config(stores=[_req("KAFKA", "url", "other")]),
        Runner(),
        out,
        env,
        "local",
        lambda k: pytest.fail("no"),
    )
    assert env == {} and "KAFKA" in out.warnings[0]


def test_cloudrun_fills_split_postgres_settings_from_cloud_sql(monkeypatch, tmp_path):
    _, files, _ = _stub_cloudrun(monkeypatch, secret=None)
    stores = [_req("PGHOST", "host"), _req("PGPASSWORD", "password"), _req("PGDATABASE", "database")]
    deployer.deploy_cloudrun(
        tmp_path,
        Config(project="p", service="svc", tag="t", stores=stores),
        Runner(),
        Outcome(target="cloudrun"),
    )
    import json

    assert json.loads(files[0]) == {"PGHOST": "/cloudsql/p:r:inst", "PGPASSWORD": "pw", "PGDATABASE": "gb_x"}
