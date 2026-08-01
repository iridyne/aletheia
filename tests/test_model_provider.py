"""Tests for model provider layer."""

import asyncio
import pytest
from unittest.mock import patch, AsyncMock
import json

from aletheia.model_provider.base import ModelRequest, ModelHealth, ModelProvider
from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider
from aletheia.model_provider.databricks import DatabricksProvider, _normalize_workspace_url
from aletheia.model_provider.codex_cli import CodexCLIProvider
from aletheia.model_provider.router import (
    clear_cache,
    healthcheck_all,
    invalidate_health_cache,
    invalidate_model_directory,
    get_provider,
    list_models_cached,
)


def test_router_injects_api_key_from_secret_ref(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    clear_cache()

    # Configure a provider with an api_key_secret_ref and store that secret
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.config.secrets import set_secret
    cfg = load_config()
    cfg.models.providers["openai_compatible"] = ProviderConfig(
        type="openai_compatible",
        base_url="http://localhost:8000/v1",
        api_key_secret_ref="model.openai_compatible.api_key",
    )
    save_config(cfg)
    set_secret("model.openai_compatible.api_key", "sk-secret-123")

    provider = get_provider("openai_compatible")
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.api_key == "sk-secret-123"
    clear_cache()


def test_router_returns_openai_by_default(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    clear_cache()
    # Providers ship empty now; seed the default explicitly (mirrors a user who
    # configured openai_compatible as their default via Settings).
    cfg = load_config()
    cfg.models.default_provider = "openai_compatible"
    cfg.models.providers["openai_compatible"] = ProviderConfig(
        type="openai_compatible", base_url="http://localhost:8000/v1"
    )
    save_config(cfg)
    clear_cache()
    provider = get_provider()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "http://localhost:8000/v1"
    clear_cache()


def test_router_returns_anthropic(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    clear_cache()
    cfg = load_config()
    cfg.models.providers["anthropic_compatible"] = ProviderConfig(type="anthropic_compatible")
    save_config(cfg)
    clear_cache()
    provider = get_provider("anthropic_compatible")
    assert isinstance(provider, AnthropicCompatibleProvider)
    clear_cache()


def test_router_returns_codex(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    clear_cache()
    cfg = load_config()
    cfg.models.providers["codex_cli"] = ProviderConfig(type="codex_cli")
    save_config(cfg)
    clear_cache()
    provider = get_provider("codex_cli")
    assert isinstance(provider, CodexCLIProvider)
    assert provider.enabled is False
    clear_cache()


def test_router_raises_clear_error_when_no_provider_configured(tmp_path, monkeypatch):
    """Fresh install ships blank (default_provider=''). get_provider() must raise a
    human-actionable error, not a confusing 'Unknown provider: ' with an empty name."""
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    clear_cache()
    with pytest.raises(ValueError, match="No LLM provider configured"):
        get_provider()  # name=None → default_provider="" on a blank config
    clear_cache()


@pytest.mark.anyio
async def test_model_directory_cache_is_single_flight():
    invalidate_model_directory("test_provider")

    class FakeProvider(ModelProvider):
        calls = 0

        async def list_models(self):
            self.calls += 1
            await asyncio.sleep(0.03)
            return [{"slug": "m1", "display_name": "M1"}]

    provider = FakeProvider()
    first, second, third = await asyncio.gather(
        list_models_cached("test_provider", provider),
        list_models_cached("test_provider", provider),
        list_models_cached("test_provider", provider),
    )

    assert first == second == third
    assert provider.calls == 1
    cached = await list_models_cached("test_provider", provider)
    assert cached == first
    assert provider.calls == 1
    invalidate_model_directory("test_provider")


@pytest.mark.anyio
async def test_http_provider_reuses_loop_owned_client(monkeypatch):
    created = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": [{"id": "m1"}]}

    class FakeClient:
        async def get(self, url, headers=None):
            return FakeResponse()

        async def aclose(self):
            return None

    def factory(*, timeout):
        created.append(timeout)
        return FakeClient()

    monkeypatch.setattr(
        "aletheia.model_provider.openai_compatible.httpx_client",
        factory,
    )
    provider = OpenAICompatibleProvider(base_url="http://model.test/v1", model="m1")

    assert await provider.list_models()
    assert await provider.list_models()
    assert len(created) == 1
    await provider.aclose()


@pytest.mark.anyio
async def test_model_health_cache_is_single_flight(monkeypatch):
    calls = 0

    async def fake_refresh():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.03)
        return [ModelHealth(provider="fake", available=True, model="m1")]

    invalidate_health_cache()
    monkeypatch.setattr(
        "aletheia.model_provider.router._refresh_health_all",
        fake_refresh,
    )
    snapshots = await asyncio.gather(
        healthcheck_all(),
        healthcheck_all(),
        healthcheck_all(),
    )
    assert calls == 1
    assert all(snapshot[0].available for snapshot in snapshots)
    assert (await healthcheck_all())[0].model == "m1"
    assert calls == 1
    invalidate_health_cache()


@pytest.mark.anyio
async def test_codex_healthcheck_disabled():
    provider = CodexCLIProvider(enabled=False)
    health = await provider.healthcheck()
    assert health.available is False
    assert "disabled" in (health.error or "")


@pytest.mark.anyio
async def test_openai_healthcheck_no_server():
    import httpx

    class FailClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **k): raise httpx.ConnectError("refused")
        async def post(self, url, **k): raise httpx.ConnectError("refused")

    provider = OpenAICompatibleProvider(base_url="http://localhost:19999/v1", model="test")
    with patch("aletheia.model_provider.openai_compatible.httpx_client", FailClient):
        health = await provider.healthcheck()
    assert health.available is False


@pytest.mark.anyio
async def test_inline_candidate_key_used_in_healthcheck():
    """Verify-before-commit primitive: a provider built with an inline candidate key
    sends that key in the Authorization header during healthcheck — no secrets store."""
    provider = OpenAICompatibleProvider(base_url="http://probe/v1", model="m", api_key="sk-candidate-xyz")
    seen = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"data": [{"id": "m"}]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None):
            seen["headers"] = headers or {}
            return FakeResp()

    with patch("httpx.AsyncClient", FakeClient):
        health = await provider.healthcheck()
    assert health.available is True
    assert seen["headers"].get("Authorization") == "Bearer sk-candidate-xyz"


@pytest.mark.anyio
async def test_openai_complete_mock():
    provider = OpenAICompatibleProvider(base_url="http://localhost:8000/v1", model="test")
    mock_response = {
        "choices": [{"message": {"content": "Hello world"}}],
        "model": "test",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }

    class FakeResponse:
        status_code = 200
        is_success = True
        def raise_for_status(self): pass
        def json(self): return mock_response

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=FakeResponse()):
        req = ModelRequest(messages=[{"role": "user", "content": "hi"}])
        resp = await provider.complete(req)
        assert resp.content == "Hello world"
        assert resp.model == "test"


@pytest.mark.anyio
async def test_anthropic_complete_mock():
    provider = AnthropicCompatibleProvider(base_url="http://localhost:8001", model="test")
    mock_response = {
        "content": [{"type": "text", "text": "Bonjour"}],
        "model": "test",
        "usage": {},
    }

    class FakeResponse:
        status_code = 200
        is_success = True
        def raise_for_status(self): pass
        def json(self): return mock_response

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=FakeResponse()):
        req = ModelRequest(messages=[{"role": "user", "content": "hi"}])
        resp = await provider.complete(req)
        assert resp.content == "Bonjour"


# ── Databricks provider ──────────────────────────────────────────────────────
# Regression coverage for the "HTTP 404 — not saved" Verify & Save failure: users
# paste the AI-Gateway URL (…/ai-gateway/anthropic) into the Workspace URL field,
# but the provider builds its own /serving-endpoints and /api/2.0 paths, so the
# extra path segment must be stripped to the host root or every request 404s.

def test_databricks_normalizes_gateway_url_to_workspace_root():
    root = "https://adb-123.azuredatabricks.net"
    assert _normalize_workspace_url(f"{root}/ai-gateway/anthropic") == root
    assert _normalize_workspace_url(root) == root
    assert _normalize_workspace_url(f"{root}/") == root
    assert _normalize_workspace_url(f"{root}/serving-endpoints/m/invocations") == root


def test_databricks_builds_invocations_url_from_normalized_root():
    p = DatabricksProvider(
        workspace_url="https://adb-123.azuredatabricks.net/ai-gateway/anthropic",
        model="databricks-claude-opus-4-8",
    )
    assert p.workspace_url == "https://adb-123.azuredatabricks.net"
    assert p.base_url == (
        "https://adb-123.azuredatabricks.net"
        "/serving-endpoints/databricks-claude-opus-4-8/invocations"
    )


@pytest.mark.anyio
async def test_databricks_complete_omits_temperature():
    """claude-opus-4-8 on Databricks hard-rejects `temperature` with HTTP 400, so the
    invocations payload must not include it. No caller overrides the default anyway."""
    p = DatabricksProvider(workspace_url="https://adb-123.azuredatabricks.net", model="m")
    sent = {}

    class FakeResponse:
        status_code = 200
        is_success = True
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": "OK"}}], "model": "m", "usage": {}}

    async def fake_post(self, url, json=None, headers=None):  # noqa: F811
        sent["payload"] = json
        return FakeResponse()

    with patch("httpx.AsyncClient.post", new=fake_post):
        resp = await p.complete(ModelRequest(messages=[{"role": "user", "content": "hi"}], temperature=0.9))
    assert resp.content == "OK"
    assert "temperature" not in sent["payload"]


# ── list_models tests ───────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_openai_list_models_parses_data_array():
    provider = OpenAICompatibleProvider(base_url="http://gw/v1", model="m")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None): return FakeResp()

    with patch("httpx.AsyncClient", FakeClient):
        models = await provider.list_models()
    assert len(models) == 2
    assert models[0] == {"slug": "gpt-4o", "display_name": "gpt-4o"}
    assert models[1] == {"slug": "gpt-4o-mini", "display_name": "gpt-4o-mini"}


@pytest.mark.anyio
async def test_openai_list_models_returns_empty_on_failure():
    import httpx

    class FailClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **k): raise httpx.ConnectError("refused")

    provider = OpenAICompatibleProvider(base_url="http://localhost:19999/v1", model="m")
    with patch("httpx.AsyncClient", FailClient):
        models = await provider.list_models()
    assert models == []


@pytest.mark.anyio
async def test_anthropic_list_models_parses_data_array():
    provider = AnthropicCompatibleProvider(base_url="http://gw", model="m")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"data": [{"id": "claude-sonnet-4-20250514"}, {"id": "claude-haiku-4-5-20251001"}]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None): return FakeResp()

    with patch("httpx.AsyncClient", FakeClient):
        models = await provider.list_models()
    assert len(models) == 2
    assert models[0]["slug"] == "claude-sonnet-4-20250514"


@pytest.mark.anyio
async def test_anthropic_list_models_handles_plain_list():
    """Some gateways return a flat list of model IDs."""
    provider = AnthropicCompatibleProvider(base_url="http://gw", model="m")

    class FakeResp:
        status_code = 200
        def json(self):
            return ["claude-opus-4", "claude-sonnet-4"]

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None): return FakeResp()

    with patch("httpx.AsyncClient", FakeClient):
        models = await provider.list_models()
    assert len(models) == 2
    assert models[0] == {"slug": "claude-opus-4", "display_name": "claude-opus-4"}


@pytest.mark.anyio
async def test_anthropic_list_models_returns_empty_on_failure():
    import httpx

    class FailClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **k): raise httpx.ConnectError("refused")

    provider = AnthropicCompatibleProvider(base_url="http://localhost:19999", model="m")
    with patch("httpx.AsyncClient", FailClient):
        models = await provider.list_models()
    assert models == []


@pytest.mark.anyio
async def test_databricks_list_models_parses_endpoints():
    p = DatabricksProvider(workspace_url="https://adb-123.azuredatabricks.net", model="m")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"endpoints": [
                {"name": "databricks-claude-opus-4-8", "state": {"ready": "READY"}},
                {"name": "databricks-meta-llama-3", "state": {"ready": "NOT_READY"}},
            ]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None): return FakeResp()

    with patch("httpx.AsyncClient", FakeClient):
        models = await p.list_models()
    assert len(models) == 2
    assert models[0] == {
        "slug": "databricks-claude-opus-4-8",
        "display_name": "databricks-claude-opus-4-8",
        "ready_state": "READY",
    }
    assert models[1] == {
        "slug": "databricks-meta-llama-3",
        "display_name": "databricks-meta-llama-3 (not_ready)",
        "ready_state": "NOT_READY",
    }


@pytest.mark.anyio
async def test_databricks_list_models_returns_empty_on_failure():
    import httpx

    class FailClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **k): raise httpx.ConnectError("refused")

    p = DatabricksProvider(workspace_url="https://adb-123.azuredatabricks.net", model="m")
    with patch("httpx.AsyncClient", FailClient):
        models = await p.list_models()
    assert models == []


# ── Anthropic Native provider ────────────────────────────────────────────────

from aletheia.model_provider.anthropic_native import AnthropicNativeProvider


def test_anthropic_native_headers():
    provider = AnthropicNativeProvider(model="claude-sonnet-4-6", api_key="sk-ant-test123")
    headers = provider._headers()
    assert headers["x-api-key"] == "sk-ant-test123"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in headers


@pytest.mark.anyio
async def test_anthropic_native_complete_mock():
    provider = AnthropicNativeProvider(model="claude-sonnet-4-6", api_key="sk-test")
    mock_response = {
        "content": [{"type": "text", "text": "Hello from Anthropic"}],
        "model": "claude-sonnet-4-6",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    class FakeResponse:
        status_code = 200
        is_success = True
        def raise_for_status(self): pass
        def json(self): return mock_response

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=FakeResponse()):
        req = ModelRequest(messages=[{"role": "user", "content": "hi"}])
        resp = await provider.complete(req)
        assert resp.content == "Hello from Anthropic"
        assert resp.model == "claude-sonnet-4-6"


@pytest.mark.anyio
async def test_anthropic_native_list_models():
    provider = AnthropicNativeProvider(api_key="sk-test")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"data": [
                {"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8", "type": "model"},
                {"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6", "type": "model"},
            ]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None): return FakeResp()

    with patch("httpx.AsyncClient", FakeClient):
        models = await provider.list_models()
    assert len(models) == 2
    assert models[0]["slug"] == "claude-opus-4-8"
    assert models[0]["display_name"] == "Claude Opus 4.8"
    assert models[1]["slug"] == "claude-sonnet-4-6"


@pytest.mark.anyio
async def test_anthropic_native_healthcheck_success():
    provider = AnthropicNativeProvider(model="claude-sonnet-4-6", api_key="sk-test")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"data": [{"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6"}]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None): return FakeResp()

    with patch("httpx.AsyncClient", FakeClient):
        health = await provider.healthcheck()
    assert health.available is True
    assert "claude-sonnet-4-6" in health.models


@pytest.mark.anyio
async def test_anthropic_native_healthcheck_failure():
    import httpx

    class FailClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **k): raise httpx.ConnectError("refused")

    provider = AnthropicNativeProvider(model="claude-sonnet-4-6")
    with patch("httpx.AsyncClient", FailClient):
        health = await provider.healthcheck()
    assert health.available is False


# ── OpenAI Native provider ───────────────────────────────────────────────────

from aletheia.model_provider.openai_native import OpenAINativeProvider


def test_openai_native_headers():
    provider = OpenAINativeProvider(model="gpt-4o", api_key="sk-openai-test123")
    headers = provider._headers()
    assert headers["Authorization"] == "Bearer sk-openai-test123"
    assert "x-api-key" not in headers


@pytest.mark.anyio
async def test_openai_native_complete_mock():
    provider = OpenAINativeProvider(model="gpt-4o", api_key="sk-test")
    mock_response = {
        "choices": [{"message": {"content": "Hello from OpenAI"}}],
        "model": "gpt-4o",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }

    class FakeResponse:
        status_code = 200
        is_success = True
        def raise_for_status(self): pass
        def json(self): return mock_response

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=FakeResponse()):
        req = ModelRequest(messages=[{"role": "user", "content": "hi"}])
        resp = await provider.complete(req)
        assert resp.content == "Hello from OpenAI"
        assert resp.model == "gpt-4o"


@pytest.mark.anyio
async def test_openai_native_list_models_filters_chat_models():
    provider = OpenAINativeProvider(api_key="sk-test")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"data": [
                {"id": "gpt-4o"},
                {"id": "gpt-4o-mini"},
                {"id": "dall-e-3"},
                {"id": "text-embedding-3-large"},
                {"id": "o3-mini"},
            ]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None): return FakeResp()

    with patch("httpx.AsyncClient", FakeClient):
        models = await provider.list_models()
    slugs = [m["slug"] for m in models]
    assert "gpt-4o" in slugs
    assert "gpt-4o-mini" in slugs
    assert "o3-mini" in slugs
    assert "dall-e-3" not in slugs
    assert "text-embedding-3-large" not in slugs


@pytest.mark.anyio
async def test_openai_native_healthcheck_success():
    provider = OpenAINativeProvider(model="gpt-4o", api_key="sk-test")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None): return FakeResp()

    with patch("httpx.AsyncClient", FakeClient):
        health = await provider.healthcheck()
    assert health.available is True
    assert "gpt-4o" in health.models


@pytest.mark.anyio
async def test_openai_native_healthcheck_failure():
    import httpx

    class FailClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **k): raise httpx.ConnectError("refused")

    provider = OpenAINativeProvider(model="gpt-4o")
    with patch("httpx.AsyncClient", FailClient):
        health = await provider.healthcheck()
    assert health.available is False


def test_router_returns_anthropic_native(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    clear_cache()

    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.config.secrets import set_secret
    cfg = load_config()
    cfg.models.providers["anthropic_native"] = ProviderConfig(
        type="anthropic_native", model="claude-sonnet-4-6",
        api_key_secret_ref="model.anthropic_native.api_key",
    )
    save_config(cfg)
    set_secret("model.anthropic_native.api_key", "sk-ant-test")

    provider = get_provider("anthropic_native")
    assert isinstance(provider, AnthropicNativeProvider)
    assert provider.model == "claude-sonnet-4-6"
    assert provider.api_key == "sk-ant-test"
    clear_cache()


def test_router_returns_openai_native(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    clear_cache()

    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.config.secrets import set_secret
    cfg = load_config()
    cfg.models.providers["openai_native"] = ProviderConfig(
        type="openai_native", model="gpt-4o",
        api_key_secret_ref="model.openai_native.api_key",
    )
    save_config(cfg)
    set_secret("model.openai_native.api_key", "sk-openai-test")

    provider = get_provider("openai_native")
    assert isinstance(provider, OpenAINativeProvider)
    assert provider.model == "gpt-4o"
    assert provider.api_key == "sk-openai-test"
    clear_cache()


# ── Streaming tests ──────────────────────────────────────────────────────────

from aletheia.model_provider.base import StreamChunk


@pytest.mark.anyio
async def test_anthropic_native_stream():
    """Verify SSE parsing yields correct StreamChunks."""
    provider = AnthropicNativeProvider(model="claude-sonnet-4-6", api_key="sk-test")

    sse_lines = [
        'event: content_block_start',
        'data: {"type":"content_block_start","index":0}',
        '',
        'event: content_block_delta',
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}',
        '',
        'event: content_block_delta',
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" world"}}',
        '',
        'event: message_delta',
        'data: {"type":"message_delta","usage":{"output_tokens":5}}',
        '',
        'event: message_stop',
        'data: {"type":"message_stop"}',
        '',
    ]

    class FakeStreamResp:
        status_code = 200
        is_success = True
        def raise_for_status(self): pass
        async def aiter_lines(self):
            for line in sse_lines:
                yield line
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def stream(self, method, url, json=None, headers=None):  # noqa: F811
            return FakeStreamResp()

    with patch("httpx.AsyncClient", FakeClient):
        chunks = []
        async for chunk in provider.stream(ModelRequest(messages=[{"role": "user", "content": "hi"}])):
            chunks.append(chunk)

    assert chunks[0].content == "Hello"
    assert chunks[1].content == " world"
    assert any(c.done for c in chunks)
    assert any(c.usage is not None for c in chunks)


@pytest.mark.anyio
async def test_openai_native_stream():
    """Verify SSE parsing yields correct StreamChunks."""
    provider = OpenAINativeProvider(model="gpt-4o", api_key="sk-test")

    sse_lines = [
        'data: {"choices":[{"delta":{"content":"Hi"}}]}',
        'data: {"choices":[{"delta":{"content":" there"}}]}',
        'data: [DONE]',
    ]

    class FakeStreamResp:
        status_code = 200
        is_success = True
        def raise_for_status(self): pass
        async def aiter_lines(self):
            for line in sse_lines:
                yield line
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def stream(self, method, url, json=None, headers=None):  # noqa: F811
            return FakeStreamResp()

    with patch("httpx.AsyncClient", FakeClient):
        chunks = []
        async for chunk in provider.stream(ModelRequest(messages=[{"role": "user", "content": "hi"}])):
            chunks.append(chunk)

    assert chunks[0].content == "Hi"
    assert chunks[1].content == " there"
    assert chunks[-1].done is True


@pytest.mark.anyio
async def test_base_stream_fallback():
    """Base ModelProvider.stream() falls back to complete() and yields one chunk."""
    from aletheia.model_provider.base import ModelProvider, ModelResponse as MR

    class FakeProvider(ModelProvider):
        async def complete(self, request):
            return MR(content="full response", model="m", usage={"total": 10})

    provider = FakeProvider()
    chunks = []
    async for chunk in provider.stream(ModelRequest(messages=[{"role": "user", "content": "hi"}])):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].content == "full response"
    assert chunks[0].done is True
    assert chunks[0].usage == {"total": 10}


# ── Enriched model metadata tests ────────────────────────────────────────────

@pytest.mark.anyio
async def test_anthropic_native_list_models_enriched():
    provider = AnthropicNativeProvider(api_key="sk-test")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"data": [
                {"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8", "created_at": "2025-05-01"},
                {"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6"},
                {"id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5"},
            ]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None): return FakeResp()

    with patch("httpx.AsyncClient", FakeClient):
        models = await provider.list_models()

    assert models[0]["tier"] == "flagship"
    assert models[0]["context_window"] == 1_000_000
    assert "vision" in models[0]["capabilities"]
    assert models[0]["created_at"] == "2025-05-01"

    assert models[1]["tier"] == "standard"
    assert models[2]["tier"] == "fast"
    assert models[2]["context_window"] == 200_000


@pytest.mark.anyio
async def test_openai_native_list_models_enriched():
    provider = OpenAINativeProvider(api_key="sk-test")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"data": [
                {"id": "gpt-4o", "created": 1700000000},
                {"id": "gpt-4o-mini", "created": 1700000001},
                {"id": "o3-mini", "created": 1700000002},
            ]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None): return FakeResp()

    with patch("httpx.AsyncClient", FakeClient):
        models = await provider.list_models()

    gpt4o = next(m for m in models if m["slug"] == "gpt-4o")
    assert gpt4o["tier"] == "flagship"
    assert gpt4o["context_window"] == 128_000
    assert "vision" in gpt4o["capabilities"]

    mini = next(m for m in models if m["slug"] == "gpt-4o-mini")
    assert mini["tier"] == "standard"

    o3 = next(m for m in models if m["slug"] == "o3-mini")
    assert o3["tier"] == "reasoning"


# ── OAuth subscription provider regression tests ────────────────────────────


def test_anthropic_native_oauth_headers():
    """OAuth token takes priority over api_key and uses Bearer scheme."""
    provider = AnthropicNativeProvider(
        model="claude-sonnet-4-6", api_key="sk-ant-key", oauth_token="oat_token123"
    )
    headers = provider._headers()
    assert headers["Authorization"] == "Bearer oat_token123"
    assert "x-api-key" not in headers


def test_openai_native_oauth_headers():
    """OAuth token takes priority over api_key."""
    provider = OpenAINativeProvider(model="gpt-4o", api_key="sk-key", oauth_token="oat_openai")
    headers = provider._headers()
    assert headers["Authorization"] == "Bearer oat_openai"


def test_router_passes_oauth_token_to_provider(tmp_path, monkeypatch):
    """When auth_type=oauth and oauth_token_ref is set, router passes oauth_token."""
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    clear_cache()

    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.config.secrets import set_secret
    cfg = load_config()
    cfg.models.providers["anthropic_native"] = ProviderConfig(
        type="anthropic_native", model="claude-sonnet-4-6",
        auth_type="oauth", oauth_token_ref="model.anthropic_native.oauth_token",
    )
    save_config(cfg)
    set_secret("model.anthropic_native.oauth_token", "oat_test_tok")

    provider = get_provider("anthropic_native")
    assert isinstance(provider, AnthropicNativeProvider)
    assert provider.oauth_token == "oat_test_tok"
    assert provider._headers()["Authorization"] == "Bearer oat_test_tok"
    clear_cache()


def test_router_oauth_token_takes_priority_over_api_key(tmp_path, monkeypatch):
    """When both api_key and oauth_token are configured, oauth_token wins."""
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    clear_cache()

    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.config.secrets import set_secret
    cfg = load_config()
    cfg.models.providers["openai_native"] = ProviderConfig(
        type="openai_native", model="gpt-4o",
        api_key_secret_ref="model.openai_native.api_key",
        auth_type="oauth", oauth_token_ref="model.openai_native.oauth_token",
    )
    save_config(cfg)
    set_secret("model.openai_native.api_key", "sk-fallback")
    set_secret("model.openai_native.oauth_token", "oat_primary")

    provider = get_provider("openai_native")
    assert isinstance(provider, OpenAINativeProvider)
    assert provider.oauth_token == "oat_primary"
    assert provider._headers()["Authorization"] == "Bearer oat_primary"
    clear_cache()


@pytest.mark.anyio
async def test_healthcheck_all_includes_oauth_provider(tmp_path, monkeypatch):
    """OAuth providers must NOT be skipped by healthcheck_all()."""
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    clear_cache()

    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.config.secrets import set_secret
    from aletheia.model_provider.router import healthcheck_all
    cfg = load_config()
    cfg.models.providers["openai_native"] = ProviderConfig(
        type="openai_native", model="gpt-4o", enabled=True,
        auth_type="oauth", oauth_token_ref="model.openai_native.oauth_token",
    )
    save_config(cfg)
    set_secret("model.openai_native.oauth_token", "oat_test")

    results = await healthcheck_all()
    provider_names = [r.provider for r in results]
    assert "openai_native" in provider_names
    clear_cache()


def test_resolve_oauth_token_preserves_old_token_on_async_skip(monkeypatch):
    """When refresh is skipped (async context), old token must be preserved."""
    from aletheia.model_provider.router import _resolve_oauth_token
    from aletheia.config.schema import ProviderConfig
    import time

    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)

    pcfg = ProviderConfig(
        type="openai_native", auth_type="oauth",
        oauth_token_ref="model.openai_native.oauth_token",
        oauth_refresh_ref="model.openai_native.oauth_refresh",
        oauth_expires_at=int(time.time() * 1000) - 1000,  # already expired
    )

    monkeypatch.setattr(
        "aletheia.config.secrets.get_secret",
        lambda ref: "old_valid_token" if "oauth_token" in ref else "refresh_val",
    )

    import asyncio
    async def _test():
        return _resolve_oauth_token("openai_native", pcfg)

    token = asyncio.run(_test())
    assert token == "old_valid_token"


def test_persist_openai_oauth_sets_default_model(tmp_path, monkeypatch):
    """_persist_openai_oauth sets model=gpt-4o when current is 'default'."""
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)

    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    cfg = load_config()
    cfg.models.providers["openai_native"] = ProviderConfig(type="openai_native", model="default")
    save_config(cfg)

    from aletheia.server.routes.model import _persist_openai_oauth
    _persist_openai_oauth({"api_key": "sk-oauth-key", "refresh_token": "rt", "expires_at": 999999})

    cfg = load_config()
    assert cfg.models.providers["openai_native"].model == "gpt-4o"
    assert cfg.models.providers["openai_native"].auth_type == "oauth"


def test_persist_claude_oauth_sets_default_model(tmp_path, monkeypatch):
    """_persist_claude_oauth sets model=claude-sonnet-4-6 when current is 'default'."""
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)

    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    cfg = load_config()
    cfg.models.providers["anthropic_native"] = ProviderConfig(type="anthropic_native", model="default")
    save_config(cfg)

    from aletheia.server.routes.model import _persist_claude_oauth
    _persist_claude_oauth({"access_token": "at", "refresh_token": "rt", "expires_at": 999999})

    cfg = load_config()
    assert cfg.models.providers["anthropic_native"].model == "claude-sonnet-4-6"
    assert cfg.models.providers["anthropic_native"].auth_type == "oauth"


def test_persist_oauth_does_not_override_user_model(tmp_path, monkeypatch):
    """If user already selected a model, persist should not overwrite it."""
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)

    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    cfg = load_config()
    cfg.models.providers["openai_native"] = ProviderConfig(type="openai_native", model="o3-mini")
    save_config(cfg)

    from aletheia.server.routes.model import _persist_openai_oauth
    _persist_openai_oauth({"api_key": "sk-oauth-key", "refresh_token": "rt", "expires_at": 999999})

    cfg = load_config()
    assert cfg.models.providers["openai_native"].model == "o3-mini"

def test_router_returns_databricks_with_workspace_protocol(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    clear_cache()
    cfg = load_config()
    cfg.models.providers["databricks"] = ProviderConfig(
        type="databricks",
        workspace_url="https://adb-123.azuredatabricks.net/ai-gateway/openai",
        protocol="responses",
        model="research-model",
    )
    save_config(cfg)

    provider = get_provider("databricks")
    assert isinstance(provider, DatabricksProvider)
    assert provider.workspace_url == "https://adb-123.azuredatabricks.net"
    assert provider.databricks_protocol == "responses"
    assert provider.model == "research-model"
    clear_cache()

@pytest.mark.anyio
async def test_databricks_ai_gateway_anthropic_request_shape():
    p = DatabricksProvider(
        workspace_url="https://adb-123.azuredatabricks.net",
        model="claude-sonnet",
        protocol="anthropic_messages",
        api_key="dapi-test",
    )
    sent = {}

    class FakeResponse:
        status_code = 200
        is_success = True

        def json(self):
            return {
                "content": [{"type": "text", "text": "OK"}],
                "model": "claude-sonnet",
                "usage": {"input_tokens": 3, "output_tokens": 1},
            }

    async def fake_post(self, url, json=None, headers=None):
        sent.update(url=url, payload=json, headers=headers)
        return FakeResponse()

    with patch("httpx.AsyncClient.post", new=fake_post):
        response = await p.complete(
            ModelRequest(
                messages=[{"role": "user", "content": "hi"}],
                system="be concise",
            )
        )

    assert response.content == "OK"
    assert sent["url"].endswith("/ai-gateway/anthropic/v1/messages")
    assert sent["headers"]["Authorization"] == "Bearer dapi-test"
    assert sent["headers"]["anthropic-version"] == "2023-06-01"
    assert sent["payload"]["system"] == "be concise"
    assert "temperature" not in sent["payload"]

@pytest.mark.anyio
async def test_databricks_ai_gateway_responses_omits_temperature():
    p = DatabricksProvider(
        workspace_url="https://adb-123.azuredatabricks.net",
        model="gpt-model",
        protocol="responses",
    )
    sent = {}

    class FakeResponse:
        status_code = 200
        is_success = True

        def json(self):
            return {"output_text": "OK", "model": "gpt-model", "usage": {}}

    async def fake_post(self, url, json=None, headers=None):
        sent.update(url=url, payload=json)
        return FakeResponse()

    with patch("httpx.AsyncClient.post", new=fake_post):
        response = await p.complete(
            ModelRequest(
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.9,
            )
        )

    assert response.content == "OK"
    assert sent["url"].endswith("/ai-gateway/openai/v1/responses")
    assert "temperature" not in sent["payload"]

@pytest.mark.anyio
async def test_databricks_health_is_honest_when_configured_endpoint_is_missing(monkeypatch):
    provider = DatabricksProvider(
        workspace_url="https://adb-123.azuredatabricks.net",
        model="missing-endpoint",
    )

    async def catalogue():
        return [{"slug": "other-endpoint", "display_name": "other-endpoint", "ready_state": "READY"}]

    monkeypatch.setattr(provider, "list_models", catalogue)
    health = await provider.healthcheck()
    assert health.available is False
    assert health.error == "configured serving endpoint not found"


def test_databricks_rejects_plaintext_remote_workspace_but_allows_loopback():
    with pytest.raises(ValueError, match="must use HTTPS"):
        DatabricksProvider(
            workspace_url="http://adb-123.azuredatabricks.net",
            model="endpoint",
        )

    provider = DatabricksProvider(
        workspace_url="http://127.0.0.1:9000",
        model="endpoint",
    )
    assert provider.workspace_url == "http://127.0.0.1:9000"
