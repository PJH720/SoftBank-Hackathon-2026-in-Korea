"""Tests for the local Caddy configuration validator command."""

from __future__ import annotations

from types import SimpleNamespace

from scripts import validate_caddy_config


def test_validator_uses_local_image_and_native_json_config_without_adapter(monkeypatch, capsys):
    commands = []
    image_id = "sha256:" + "a" * 64

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        if command[1:3] == ["image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout=image_id + "\n", stderr="")
        if command[-1] == "version":
            return SimpleNamespace(returncode=0, stdout="v2.11.7\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="Valid configuration\n", stderr="")

    monkeypatch.setattr(validate_caddy_config.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(validate_caddy_config.subprocess, "run", fake_run)

    assert validate_caddy_config.main() == 0

    validate_command, validate_kwargs = commands[-1]
    assert validate_command[-3:] == ["validate", "--config", "/etc/caddy/config.json"]
    assert "--adapter" not in validate_command
    assert validate_command[2] == "--pull=never"
    assert validate_kwargs["capture_output"] is True
    output = capsys.readouterr()
    assert "v2.11.7" in output.out
    assert "Valid configuration" in output.out


def test_validator_preserves_exact_caddy_validation_error(monkeypatch, capsys):
    commands = []
    error = "Error: provision http: unknown log writer module: file\n"

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[1:3] == ["image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="sha256:" + "b" * 64, stderr="")
        if command[-1] == "version":
            return SimpleNamespace(returncode=0, stdout="v2.11.7\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr=error)

    monkeypatch.setattr(validate_caddy_config.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(validate_caddy_config.subprocess, "run", fake_run)

    assert validate_caddy_config.main() == 1
    assert capsys.readouterr().err.endswith(error)


def test_validator_reports_failure_without_diagnostic_output(monkeypatch, capsys):
    def fake_run(command, **kwargs):
        if command[1:3] == ["image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="sha256:" + "c" * 64, stderr="")
        if command[-1] == "version":
            return SimpleNamespace(returncode=0, stdout="v2.11.7\n", stderr="")
        return SimpleNamespace(returncode=125, stdout="", stderr="")

    monkeypatch.setattr(validate_caddy_config.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(validate_caddy_config.subprocess, "run", fake_run)

    assert validate_caddy_config.main() == 125
    assert capsys.readouterr().err.endswith("Caddy config validation failed with exit code 125.\n")
