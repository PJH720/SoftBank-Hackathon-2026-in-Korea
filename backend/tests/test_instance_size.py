"""Instance size (cpu/memory/instance counts) and healer's out-of-memory patch — no Docker / gcloud."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import deployer, fleet, healer, nodes
from app.deployer import Config, Runner, Size, StepError
from app.schemas import ErrorCategory


def _app(path: Path, **files: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (path / name).write_text(body)
    return path


def test_dependencies_call_for_a_size(tmp_path):
    ml = _app(tmp_path / "ml", **{"requirements.txt": "fastapi\ntorch==2.4\npandas"})
    assert deployer.detect_size(ml) == (
        {"cpu": "2", "memory": "4Gi"},
        "requirements.txt: torch",
    )  # heaviest wins
    browser = _app(tmp_path / "b", **{"package.json": '{"dependencies": {"puppeteer": "^22"}}'})
    assert deployer.detect_size(browser)[0] == {"cpu": "1", "memory": "2Gi"}
    assert deployer.detect_size(_app(tmp_path / "j", **{"pom.xml": "<project/>"}))[0] == {"memory": "1Gi"}
    assert deployer.detect_size(_app(tmp_path / "s", **{"requirements.txt": "flask"})) == ({}, "")
    # code that merely mentions a library is not a dependency
    assert deployer.detect_size(_app(tmp_path / "c", **{"app.py": "# could use torch here"})) == ({}, "")


def test_size_precedence_dependencies_then_config_then_dockerfile(tmp_path):
    src = _app(tmp_path, **{"requirements.txt": "numpy", "Dockerfile": "FROM python:3.12\nCMD x\n"})
    r = Runner()
    assert deployer.size_for(src, Config(), r) == Size(None, 1024)
    assert r.steps[-1].cmd == "memory=1Gi (requirements.txt: numpy)"
    assert deployer.size_for(src, Config(memory="2Gi", cpu="2"), r) == Size(2.0, 2048)
    (src / "Dockerfile").write_text('FROM python:3.12\nLABEL cloudmorph.memory="4Gi"\nCMD x\n')
    assert deployer.size_for(src, Config(memory="2Gi"), r) == Size(None, 4096)
    assert r.steps[-1].cmd == "memory=4Gi (Dockerfile)"
    with pytest.raises(StepError) as bad:
        deployer.size_for(src, Config(cpu="lots"), r)
    assert bad.value.kind == "user_action_required"


def test_memory_units():
    assert [deployer.memory_mib(v) for v in ("512Mi", "1Gi", "1.5Gi", "768", "2G")] == [
        512,
        1024,
        1536,
        768,
        2048,
    ]
    assert deployer.format_mib(1536) == "1536Mi" and deployer.format_mib(2048) == "2Gi"
    assert Size(0.5, 768).docker_flags() == ["--cpus", "0.5", "--memory", "768m"]


@pytest.mark.parametrize(
    "asked, fitted",
    [
        (Size(None, 512), Size(None, 512)),
        (Size(None, 6144), Size(2.0, 6144)),  # >4Gi needs 2 vCPU
        (Size(1.0, 12288), Size(4.0, 12288)),
        (Size(4.0, None), Size(4.0, 2048)),  # 4 vCPU needs 2Gi
        (Size(0.5, 256), Size(1.0, 256)),
    ],
)
def test_cloudrun_size_is_rounded_up_to_a_valid_pair(asked, fitted):
    assert deployer.fit_cloudrun(asked)[0] == fitted


def test_cloudrun_size_beyond_the_platform_is_a_user_decision():
    with pytest.raises(StepError):
        deployer.fit_cloudrun(Size(None, 64 * 1024))


def test_a_database_caps_cloud_run_instances_unless_set():
    assert deployer.scaling_flags(Config()) == []
    assert deployer.scaling_flags(Config(), database=True) == ["--max-instances", "4"]
    assert deployer.scaling_flags(Config(max_instances=20), database=True) == ["--max-instances", "20"]
    full = Config(min_instances=1, concurrency=40, request_timeout=120)
    assert deployer.scaling_flags(full) == [
        "--min-instances",
        "1",
        "--concurrency",
        "40",
        "--timeout",
        "120s",
    ]


def test_config_reads_size_from_env(monkeypatch):
    monkeypatch.setenv("DEPLOYER_MEMORY", "1Gi")
    monkeypatch.setenv("DEPLOYER_MAX_INSTANCES", "3")
    cfg = Config.from_env()
    assert cfg.memory == "1Gi" and cfg.max_instances == 3 and cfg.min_instances is None


def test_cloudrun_deploy_passes_size_and_scaling(monkeypatch, tmp_path):
    commands: list[list[str]] = []
    for fn in ("build_image", "push_image"):
        monkeypatch.setattr(deployer, fn, lambda *a, **k: None)
    monkeypatch.setattr(deployer, "serving_revision", lambda *a, **k: None)
    monkeypatch.setattr(deployer, "latest_revision", lambda *a, **k: "svc-00001-new")
    monkeypatch.setattr(deployer, "tagged_url", lambda *a, **k: "https://candidate---svc.run.app")
    monkeypatch.setattr(deployer, "wait_up", lambda *a, **k: (True, "HTTP 200"))
    monkeypatch.setattr(
        Runner,
        "run",
        lambda self, n, cmd, **_: commands.append(cmd) or subprocess.CompletedProcess(cmd, 0, "u", ""),
    )
    src = _app(tmp_path / "ml", **{"requirements.txt": "transformers"})
    out = deployer.Outcome(target="cloudrun")
    deployer.deploy_cloudrun(src, Config(project="p", service="svc", tag="t", concurrency=8), Runner(), out)
    deploy = next(c for c in commands if "deploy" in c)
    assert deploy[deploy.index("--cpu") + 1] == "2" and deploy[deploy.index("--memory") + 1] == "4Gi"
    assert deploy[deploy.index("--concurrency") + 1] == "8"
    assert out.handles["size"] == {"cpu": "2", "memory": "4Gi"}


def test_node_containers_and_their_replicas_get_the_limits(monkeypatch, tmp_path):
    sent: list[str] = []
    monkeypatch.setattr(
        nodes,
        "ssh",
        lambda node, cmd, timeout=60: sent.append(cmd) or subprocess.CompletedProcess(cmd, 0, b"cid", b""),
    )
    nodes.run_container(nodes.Node(name="a", host="1"), "img", "c", 8000, 8080, limits=["--memory", "1024m"])
    assert " --memory 1024m -p 8000:8080 img" in sent[0]

    monkeypatch.setattr(fleet, "STATE_FILE", tmp_path / "fleet.json")
    app = fleet.App(name="a", image="i", container_port=8080, hostname="h", limits=["--cpus", "2"])
    fleet.save_state(fleet.State(apps={"a": app}))
    assert fleet.load_state().apps["a"].limits == ["--cpus", "2"]  # scale_out reads it back


def test_oom_kill_is_reported_in_words_healer_knows():
    note = deployer.oom_note(True, Size(None, 1024))
    assert "OOMKilled" in note and "memory limit of 1Gi" in note
    assert deployer.oom_note(False, Size(None, 1024)) == ""


@pytest.mark.parametrize(
    "log",
    [
        "container status=exited exit_code=137 (OOMKilled: ... exceeded its memory limit of 1Gi)",
        "Memory limit of 512 MiB exceeded with 530 MiB used. Consider increasing the memory limit",
        'Exception in thread "main" java.lang.OutOfMemoryError: Java heap space',
        'Traceback (most recent call last):\n  File "/app/app.py", line 3\nMemoryError',
    ],
)
def test_out_of_memory_is_classified_first(log):
    assert healer.classify_error(log + "\nConnection refused") == ErrorCategory.OUT_OF_MEMORY


def test_memory_patch_doubles_the_limit_until_8gi():
    df = 'FROM python:3.12\nCMD ["python", "app.py"]\n'
    after, why = healer._patch_memory(df, "Memory limit of 512 MiB exceeded with 600 MiB used")
    assert 'LABEL cloudmorph.memory="1Gi"\nCMD' in after and "1Gi" in why
    again, _ = healer._patch_memory(after, "OOMKilled")
    assert 'cloudmorph.memory="2Gi"' in again and again.count("cloudmorph.memory") == 1
    # no label yet: the size deployer reported is the starting point (detected 4Gi must not drop to 1Gi)
    header = "[deployer] stage=verify kind=runtime_error target=local\n[deployer] instance cpu=2 memory=4Gi\nOOMKilled"
    assert 'cloudmorph.memory="8Gi"' in healer._patch_memory(df, header)[0]
    assert healer._patch_memory('FROM x\nLABEL cloudmorph.memory="8Gi"\n', "OOMKilled") is None


def test_deployer_size_reads_what_healer_wrote(tmp_path):
    df, _ = healer._patch_memory('FROM python:3.12\nCMD ["x"]\n', "OOMKilled")
    src = _app(tmp_path, Dockerfile=df)
    assert deployer.size_for(src, Config(), Runner()).memory == "1Gi"


def test_heal_graph_raises_memory_instead_of_stopping_on_a_memoryerror(tmp_path):
    attempts: list[str] = []

    def fake_deploy(src_dir, dockerfile, target):
        attempts.append(dockerfile)
        from app.schemas import DeployResult

        return DeployResult(success=True, target=target, exit_code=0, url="http://ok")

    log = "[deployer] stage=verify kind=runtime_error target=local\nTraceback (most recent call last):\nMemoryError"
    from app.schemas import DeployResult

    failed = DeployResult(success=False, target="local", exit_code=1, stderr=log)
    report = healer.heal(
        str(tmp_path),
        "local",
        failed,
        'FROM python:3.12\nCMD ["python", "app.py"]\n',
        fake_deploy,
        llm_patch_fn=lambda *a: None,
    )
    assert (
        report.success
        and report.records[0].category == ErrorCategory.OUT_OF_MEMORY
        and 'cloudmorph.memory="1Gi"' in attempts[0]
    )


def test_an_oom_kill_is_healed_not_stopped_as_a_silent_crash():
    """An OOM-killed container exits 137 (SIGKILL) with no traceback: without the OOM category first,
    healer's needs_human_stop would call it a silent crash and give up."""
    log = (
        "[deployer] stage=verify kind=runtime_error target=local\n"
        "[deployer] instance memory=512Mi\n"
        "container exited during startup (connection refused); see runtime logs for the traceback\n"
        "--- error ---\n"
        "container status=exited exit_code=137 (OOMKilled: the container ran out of memory and exceeded "
        "its memory limit of 512Mi); connection refused\n"
        "--- runtime logs ---\n"
        "loading model weights\n"
    )
    assert healer.needs_human_stop(log) is not None  # what would have happened
    attempts: list[str] = []

    def fake_deploy(src_dir, dockerfile, target):
        from app.schemas import DeployResult

        attempts.append(dockerfile)
        return DeployResult(success=True, target=target, exit_code=0, url="http://ok")

    from app.schemas import DeployResult

    failed = DeployResult(success=False, target="local", exit_code=1, stderr=log)
    report = healer.heal(
        "/tmp",
        "local",
        failed,
        'FROM python:3.12\nCMD ["python", "app.py"]\n',
        fake_deploy,
        llm_patch_fn=lambda *a: None,
    )
    assert report.success and 'cloudmorph.memory="1Gi"' in attempts[0]
