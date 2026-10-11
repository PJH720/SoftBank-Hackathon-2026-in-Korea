"""Request-local healer choices must override server defaults without modifying them."""

import os
from types import SimpleNamespace

import pytest

from app import healer
from app.schemas import DeployResult, ErrorCategory, LlmConfig

DOCKERFILE = 'FROM python:3.11-slim\nCMD ["python", "app.py"]\n'
PATCHED = DOCKERFILE + "ENV FIXED=1\n"
ERROR = "unrecognized container build failure"


def run_heal(config=None, **kwargs):
    failed = DeployResult(success=False, target="cloudrun", exit_code=1, stderr=ERROR)
    return healer.heal(
        "/tmp/app",
        "cloudrun",
        failed,
        DOCKERFILE,
        lambda *args: DeployResult(success=True, target="cloudrun", exit_code=0),
        llm_config=config,
        **kwargs,
    )


@pytest.mark.parametrize("provider", ["openai", "local"])
def test_explicit_provider_uses_patch_even_with_anthropic_key(monkeypatch, provider):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    config = LlmConfig(
        provider=provider,
        model="selected-model",
        base_url="http://local.test/v1" if provider == "local" else None,
    )
    calls = []
    monkeypatch.setattr(healer, "default_repo_repair", lambda *a, **k: pytest.fail("Claude agent selected"))

    def patch(*args, llm_config):
        calls.append(llm_config)
        return PATCHED

    monkeypatch.setattr(healer, "default_llm_patch", patch)
    before = dict(os.environ)
    assert run_heal(config).success
    assert calls == [config]
    assert dict(os.environ) == before


def test_anthropic_model_reaches_repo_agent(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("HEALER_MODEL_ANTHROPIC", "claude-server")
    calls = []

    def repair(*args, model_name):
        calls.append(model_name)
        return healer.RepoFix(PATCHED, "fixed", "")

    monkeypatch.setattr(healer, "default_repo_repair", repair)
    assert run_heal(LlmConfig(provider="anthropic", model="claude-selected")).success
    assert calls == ["claude-selected"]


def test_legacy_none_preserves_repo_agent_signature(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    calls = []

    def repair(src, dockerfile, error, category):
        calls.append((src, dockerfile, error, category))
        return healer.RepoFix(PATCHED, "fixed", "")

    monkeypatch.setattr(healer, "default_repo_repair", repair)
    assert run_heal().success
    assert len(calls) == 1


def test_injected_patch_signature_is_unchanged(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    calls = []

    def patch(dockerfile, error, category):
        calls.append(category)
        return PATCHED

    assert run_heal(LlmConfig(provider="openai"), llm_patch_fn=patch).success
    assert calls == [ErrorCategory.UNKNOWN]


@pytest.mark.parametrize(
    "provider,model,url",
    [
        ("anthropic", "claude-selected", None),
        ("openai", "gpt-selected", None),
        ("local", "served-model", "http://local.test/v1"),
    ],
)
def test_selected_model_and_endpoint_override_server_env(monkeypatch, provider, model, url):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("HEALER_MODEL", "claude-server")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://server-local.test/v1")
    calls = []
    monkeypatch.setattr(
        healer, "_call_anthropic", lambda *a, **k: calls.append(("anthropic", a, k)) or PATCHED
    )
    monkeypatch.setattr(
        healer, "_call_openai_compatible", lambda *a, **k: calls.append(("compatible", a, k)) or PATCHED
    )
    before = dict(os.environ)
    config = LlmConfig(provider=provider, model=model, base_url=url)
    assert healer.default_llm_patch(DOCKERFILE, ERROR, ErrorCategory.UNKNOWN, llm_config=config) == PATCHED
    assert len(calls) == 1
    assert calls[0][1][2] == model
    if provider != "anthropic":
        assert calls[0][1][3] == provider
        assert calls[0][2]["base_url"] == (url or "https://api.openai.com/v1")
    assert dict(os.environ) == before


def test_provider_failure_does_not_fall_through(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setattr(healer, "_call_anthropic", lambda *a, **k: pytest.fail("provider fallthrough"))
    monkeypatch.setattr(healer, "_call_openai_compatible", lambda *a, **k: None)
    assert healer._ask_llm("s", "u", "dockerfile", LlmConfig(provider="openai")) is None


@pytest.mark.parametrize("provider,env_model", [("anthropic", "claude-server"), ("openai", "gpt-server")])
def test_blank_model_uses_provider_env(monkeypatch, provider, env_model):
    monkeypatch.setenv(f"HEALER_MODEL_{provider.upper()}", env_model)
    calls = []
    monkeypatch.setattr(healer, "_call_anthropic", lambda *a, **k: calls.append(a[2]) or PATCHED)
    monkeypatch.setattr(healer, "_call_openai_compatible", lambda *a, **k: calls.append(a[2]) or PATCHED)
    healer._ask_llm("s", "u", "dockerfile", LlmConfig(provider=provider, model=" "))
    assert calls == [env_model]


def test_explicit_local_does_not_send_hosted_api_key(monkeypatch):
    import langchain_openai

    monkeypatch.setenv("OPENAI_API_KEY", "hosted-secret")
    captured = []

    class Chat:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def invoke(self, messages):
            return SimpleNamespace(content=PATCHED)

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", Chat)
    config = LlmConfig(provider="local", model="served", base_url="http://local.test/v1")
    assert healer._ask_llm("s", "u", "dockerfile", config) == PATCHED
    assert captured[0]["api_key"] == "EMPTY"
    assert captured[0]["base_url"] == config.base_url


def test_legacy_local_retains_env_key_and_url(monkeypatch):
    import langchain_openai

    monkeypatch.setenv("LLM_PROVIDER", "local")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://server.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-proxy-key")
    monkeypatch.setenv("HEALER_MODEL_LOCAL", "served")
    captured = []

    class Chat:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def invoke(self, messages):
            return SimpleNamespace(content=PATCHED)

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", Chat)
    assert healer._ask_llm("s", "u", "dockerfile") == PATCHED
    assert captured[0]["api_key"] == "legacy-proxy-key"
    assert captured[0]["base_url"] == "http://server.test/v1"


def test_selected_model_is_used_by_repository_reader(monkeypatch, tmp_path):
    import anthropic

    from app import analyzer

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: object())
    models = []

    class Reader:
        def __init__(self, client, model, src):
            models.append(model)
            self.tools = []
            self.specs = []

        def ask(self, schema, messages):
            return SimpleNamespace(dockerfile=PATCHED, rationale="fixed")

    monkeypatch.setattr(analyzer, "RepoReader", Reader)
    fix = healer.default_repo_repair(
        str(tmp_path), DOCKERFILE, ERROR, ErrorCategory.UNKNOWN, model_name="claude-selected"
    )
    assert fix.dockerfile == PATCHED
    assert models == ["claude-selected"]


def test_source_suggestion_uses_selected_provider(monkeypatch):
    calls = []
    monkeypatch.setattr(healer, "_call_anthropic", lambda *a, **k: pytest.fail("wrong provider"))
    monkeypatch.setattr(
        healer, "_call_openai_compatible", lambda *a, **k: calls.append((a, k)) or 'print("fixed")'
    )
    config = LlmConfig(provider="local", model="served", base_url="http://local.test/v1")
    suggestion = healer.default_source_suggestion("app.py", "broken", "trace", llm_config=config)
    assert suggestion == 'print("fixed")\n'
    assert calls[0][0][2:4] == ("served", "local")
    assert calls[0][1]["base_url"] == config.base_url


def test_explicit_openai_client_uses_hosted_endpoint(monkeypatch):
    import langchain_openai

    monkeypatch.setenv("OPENAI_BASE_URL", "http://server-local.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "hosted-secret")
    captured = []

    class Chat:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def invoke(self, messages):
            return SimpleNamespace(content=PATCHED)

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", Chat)
    assert healer._ask_llm("s", "u", "dockerfile", LlmConfig(provider="openai")) == PATCHED
    assert captured[0]["base_url"] == "https://api.openai.com/v1"
    assert captured[0]["api_key"] == "hosted-secret"
