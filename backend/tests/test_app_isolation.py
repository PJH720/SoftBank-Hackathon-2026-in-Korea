"""User apps on cloudrun/function run as a dedicated no-role service account, not the default compute
account (Editor): their code can read the runtime account's token from the metadata server."""

from __future__ import annotations

from app import deployer
from app.deployer import Config, Outcome, Runner
from tests.test_deployer_review_fixes import _stub_cloudrun
from tests.test_function_target import SAMPLE, FakeRunner, _src


def test_short_name_expands_to_project_account():
    cfg = Config(project="demo-proj", app_service_account="cloudmorph-apps")
    assert cfg.runtime_account() == "cloudmorph-apps@demo-proj.iam.gserviceaccount.com"
    assert Config(app_service_account="a@b.iam.gserviceaccount.com").runtime_account() == (
        "a@b.iam.gserviceaccount.com"
    )
    assert Config(project="demo-proj").runtime_account() is None


def test_from_env_reads_app_service_account(monkeypatch):
    monkeypatch.setenv("DEPLOYER_APP_SERVICE_ACCOUNT", "cloudmorph-apps")
    assert deployer.Config.from_env().app_service_account == "cloudmorph-apps"


def test_cloudrun_deploy_runs_as_app_account(monkeypatch, tmp_path):
    commands = _stub_cloudrun(monkeypatch, latest="svc-00002-new")
    cfg = Config(project="demo-proj", service="svc", tag="t1", app_service_account="cloudmorph-apps")
    deployer.deploy_cloudrun(tmp_path, cfg, Runner(), Outcome(target="cloudrun"))
    deploy = next(c for c in commands if "deploy" in c and "--image" in c)
    assert (
        deploy[deploy.index("--service-account") + 1] == "cloudmorph-apps@demo-proj.iam.gserviceaccount.com"
    )


def test_cloudrun_deploy_without_setting_keeps_gcloud_default(monkeypatch, tmp_path):
    commands = _stub_cloudrun(monkeypatch, latest="svc-00002-new")
    deployer.deploy_cloudrun(
        tmp_path, Config(project="demo-proj", service="svc", tag="t1"), Runner(), Outcome(target="cloudrun")
    )
    deploy = next(c for c in commands if "deploy" in c and "--image" in c)
    assert "--service-account" not in deploy


def test_function_deploy_runs_as_app_account(tmp_path, monkeypatch):
    src = _src(tmp_path / "fn", **{"main.py": (SAMPLE / "main.py").read_text()})
    monkeypatch.setattr(deployer, "wait_up", lambda url, path, timeout, **kw: (True, "HTTP 200"))
    r = FakeRunner()
    cfg = Config(project="p", app_service_account="cloudmorph-apps")
    deployer.deploy_function(src, cfg, r, Outcome(target="function"))
    cmd = r.calls[0]
    assert cmd[cmd.index("--service-account") + 1] == "cloudmorph-apps@p.iam.gserviceaccount.com"
