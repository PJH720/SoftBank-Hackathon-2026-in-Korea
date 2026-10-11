"""Per-deployment contract and analyzer model priority, without LLM calls."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import analyzer
from app.schemas import DeployRequest, LlmConfig


def test_omitted_or_null_config_preserves_server_defaults():
    assert DeployRequest(source="demo").llm_config is None
    assert DeployRequest(source="demo", llm_config=None).llm_config is None


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("model", [None, "", "   "])
def test_hosted_blank_model_means_default(provider, model):
    assert LlmConfig(provider=provider, model=model).model is None


@pytest.mark.parametrize(
    "config",
    [
        {"provider": "unknown"},
        {"provider": "local"},
        {"provider": "local", "model": "served"},
        {"provider": "local", "model": " ", "base_url": "http://localhost:11434/v1"},
        {"provider": "local", "model": "served", "base_url": "ftp://localhost"},
        {"provider": "local", "model": "served", "base_url": "not-a-url"},
        {"provider": "anthropic", "base_url": "http://localhost"},
        {"provider": "openai", "base_url": "https://example.com"},
        {"provider": "openai", "base_url": ""},
    ],
)
def test_invalid_contract_rejected(config):
    with pytest.raises(ValidationError):
        DeployRequest(source="demo", llm_config=config)


@pytest.mark.parametrize("url", ["http://localhost:11434/v1", "https://local.example/v1"])
def test_local_requires_served_model_and_http_endpoint(url):
    config = LlmConfig(provider="local", model="  served-model  ", base_url=url)
    assert config.model == "served-model" and config.base_url == url


@pytest.mark.parametrize("selection, expected", [(None, "env-model"), ("selected-model", "selected-model")])
def test_analyzer_selection_overrides_environment(monkeypatch, tmp_path, selection, expected):
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("ANALYZER_MODEL", "env-model")
    monkeypatch.setattr(anthropic, "Anthropic", lambda: object())
    monkeypatch.setattr(analyzer, "RepoReader", lambda client, model, repo: SimpleNamespace(model=model))
    assert analyzer.load_model(str(tmp_path), model_name=selection).model == expected


def test_analyzer_blank_env_uses_default(monkeypatch, tmp_path):
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("ANALYZER_MODEL", "")
    monkeypatch.setattr(anthropic, "Anthropic", lambda: object())
    monkeypatch.setattr(analyzer, "RepoReader", lambda client, model, repo: SimpleNamespace(model=model))
    assert analyzer.load_model(str(tmp_path)).model == analyzer.DEFAULT_MODEL


def test_explicit_analyzer_model_uses_claude_even_when_quick_is_ready(monkeypatch, tmp_path):
    from app import quick

    monkeypatch.setenv("ANALYZER_MODE", "auto")
    monkeypatch.setattr(quick, "ready", lambda: True)
    monkeypatch.setattr(quick, "analyze", lambda _: pytest.fail("explicit model must use Claude"))
    calls = []
    monkeypatch.setattr(analyzer, "load_model", lambda src, model_name: calls.append(model_name) or object())
    monkeypatch.setattr(analyzer, "make_inspector", lambda model: None)
    monkeypatch.setattr(analyzer, "make_dockerfile_writer", lambda model: None)
    monkeypatch.setattr(analyzer, "make_dockerfile_judge", lambda model: None)
    sentinel = object()
    monkeypatch.setattr(analyzer, "analyze_with", lambda *args: sentinel)
    assert analyzer.analyze(str(tmp_path), model_name="selected-model") is sentinel
    assert calls == ["selected-model"]
