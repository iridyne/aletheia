"""Tests for model provider layer."""

import pytest
from unittest.mock import patch, AsyncMock
import json

from aletheia.model_provider.base import ModelRequest, ModelHealth
from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider
from aletheia.model_provider.codex_cli import CodexCLIProvider
from aletheia.model_provider.router import get_provider, clear_cache


def test_router_injects_api_key_from_secret_ref(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    clear_cache()

    # Configure a provider with an api_key_secret_ref and store that secret
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.secrets import set_secret
    cfg = load_config()
    cfg.models.providers["openai_compatible"].api_key_secret_ref = "model.openai_compatible.api_key"
    save_config(cfg)
    set_secret("model.openai_compatible.api_key", "sk-secret-123")

    provider = get_provider("openai_compatible")
    assert provider.api_key == "sk-secret-123"
    clear_cache()


def test_router_returns_openai_by_default(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    clear_cache()
    provider = get_provider()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "http://localhost:8000/v1"
    clear_cache()


def test_router_returns_anthropic(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    clear_cache()
    provider = get_provider("anthropic_compatible")
    assert isinstance(provider, AnthropicCompatibleProvider)
    clear_cache()


def test_router_returns_codex(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    clear_cache()
    provider = get_provider("codex_cli")
    assert isinstance(provider, CodexCLIProvider)
    assert provider.enabled is False
    clear_cache()


@pytest.mark.anyio
async def test_codex_healthcheck_disabled():
    provider = CodexCLIProvider(enabled=False)
    health = await provider.healthcheck()
    assert health.available is False
    assert "disabled" in health.error


@pytest.mark.anyio
async def test_openai_healthcheck_no_server():
    provider = OpenAICompatibleProvider(base_url="http://localhost:19999/v1", model="test")
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
        def raise_for_status(self): pass
        def json(self): return mock_response

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=FakeResponse()):
        req = ModelRequest(messages=[{"role": "user", "content": "hi"}])
        resp = await provider.complete(req)
        assert resp.content == "Bonjour"
