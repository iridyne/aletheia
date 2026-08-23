"""Regression: async Agent paths refresh cached expired OAuth tokens.

Covers Issue #1276: OAuth token refresh for async Agent entry paths (llm_complete,
llm_structured, llm_stream) and fallback provider resolution, plus expiry-aware
cache in get_provider_async.
"""

import asyncio
import time

import pytest  # pyright: ignore[reportMissingImports]


@pytest.mark.anyio
async def test_llm_complete_refreshes_expired_oauth_token(tmp_path, monkeypatch):
    """llm_complete with expired OAuth profile triggers async refresh and uses fresh token."""
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.config.secrets import set_secret
    from aletheia.agents.helpers import llm_complete

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)

    cfg = load_config()
    cfg.models.default_provider = "openai_native"
    cfg.models.providers["openai_native"] = ProviderConfig(
        type="openai_native",
        model="gpt-4o",
        enabled=True,
        auth_type="oauth",
        oauth_token_ref="model.openai_native.oauth_token",
        oauth_refresh_ref="model.openai_native.oauth_refresh",
        oauth_expires_at=int(time.time() * 1000) - 1000,  # expired
    )
    save_config(cfg)
    set_secret("model.openai_native.oauth_token", "stale-token")
    set_secret("model.openai_native.oauth_refresh", "refresh-val")

    refresh_calls = 0

    async def mock_refresh(_refresh_token):
        nonlocal refresh_calls
        refresh_calls += 1
        return {
            "api_key": "fresh-token",
            "refresh_token": "rotated-refresh",
            "expires_at": int((time.time() + 3600) * 1000),
        }

    monkeypatch.setattr(
        "aletheia.model_provider.openai_oauth.refresh_openai_tokens", mock_refresh
    )

    captured_headers = {}

    async def mock_brokered_complete(provider_name, provider, request):
        from aletheia.model_provider.base import ModelResponse

        captured_headers.update(provider._headers())
        return ModelResponse(content="response", usage={})

    monkeypatch.setattr(
        "aletheia.model_provider.fallback.brokered_complete", mock_brokered_complete
    )

    # Register a minimal caller spec to satisfy build_inference_call
    from aletheia.model_provider.inference_contract import (
        CALLER_SPECS,
        InferenceCallerSpec,
        OutputMode,
    )

    CALLER_SPECS["test_caller"] = InferenceCallerSpec(
        caller_id="test_caller",
        prompt_template_id="test_caller",
        prompt_version="v1",
        output_mode=OutputMode.TEXT,
    )

    try:
        content, err = await llm_complete(
            user="test", system="You are helpful", caller="test_caller"
        )
    finally:
        CALLER_SPECS.pop("test_caller", None)

    assert err is None
    assert content == "response"
    assert refresh_calls == 1, "Expected exactly one OAuth refresh"
    assert captured_headers.get("Authorization") == "Bearer fresh-token"


@pytest.mark.anyio
async def test_get_provider_async_replaces_cached_provider_on_expiry(tmp_path, monkeypatch):
    """get_provider_async replaces a cached OAuth provider whose token reached expiry window."""
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.config.secrets import set_secret
    from aletheia.model_provider.router import get_provider_async, _cache
    from aletheia.model_provider.openai_native import OpenAINativeProvider

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)

    cfg = load_config()
    cfg.models.providers["openai_native"] = ProviderConfig(
        type="openai_native",
        model="gpt-4o",
        enabled=True,
        auth_type="oauth",
        oauth_token_ref="model.openai_native.oauth_token",
        oauth_refresh_ref="model.openai_native.oauth_refresh",
        oauth_expires_at=int(time.time() * 1000) + 4 * 60 * 1000,  # in refresh window
    )
    save_config(cfg)
    set_secret("model.openai_native.oauth_token", "old-token")
    set_secret("model.openai_native.oauth_refresh", "refresh-val")

    # Pre-populate cache with old provider
    old_provider = OpenAINativeProvider(model="gpt-4o", oauth_token="old-token")
    _cache["openai_native"] = old_provider

    async def mock_refresh(_refresh_token):
        return {
            "api_key": "fresh-token",
            "refresh_token": "rotated",
            "expires_at": int((time.time() + 3600) * 1000),
        }

    monkeypatch.setattr(
        "aletheia.model_provider.openai_oauth.refresh_openai_tokens", mock_refresh
    )

    from aletheia.model_provider.openai_native import OpenAINativeProvider

    provider = await get_provider_async("openai_native")

    assert provider is not old_provider, "Expected cached provider to be replaced"
    assert isinstance(provider, OpenAINativeProvider)
    assert provider.oauth_token == "fresh-token"
    assert _cache["openai_native"] is provider


@pytest.mark.anyio
async def test_concurrent_agent_calls_single_flight_oauth_refresh(tmp_path, monkeypatch):
    """Concurrent async Agent calls trigger exactly one OAuth refresh (single-flight)."""
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.config.secrets import set_secret
    from aletheia.agents.helpers import llm_complete

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)

    cfg = load_config()
    cfg.models.default_provider = "anthropic_native"
    cfg.models.providers["anthropic_native"] = ProviderConfig(
        type="anthropic_native",
        model="claude-sonnet-4-6",
        enabled=True,
        auth_type="oauth",
        oauth_token_ref="model.anthropic_native.oauth_token",
        oauth_refresh_ref="model.anthropic_native.oauth_refresh",
        oauth_expires_at=int(time.time() * 1000) - 500,  # expired
    )
    save_config(cfg)
    set_secret("model.anthropic_native.oauth_token", "expired-token")
    set_secret("model.anthropic_native.oauth_refresh", "refresh-val")

    refresh_calls = 0

    async def mock_refresh(_refresh_token):
        nonlocal refresh_calls
        refresh_calls += 1
        await asyncio.sleep(0.02)  # simulate network delay
        return {
            "access_token": "fresh-token",
            "refresh_token": "rotated",
            "expires_at": int((time.time() + 3600) * 1000),
        }

    monkeypatch.setattr(
        "aletheia.model_provider.claude_oauth.refresh_claude_tokens", mock_refresh
    )

    async def mock_brokered_complete(provider_name, provider, request):
        from aletheia.model_provider.base import ModelResponse

        return ModelResponse(content="response", usage={})

    monkeypatch.setattr(
        "aletheia.model_provider.fallback.brokered_complete", mock_brokered_complete
    )

    # Register caller spec
    from aletheia.model_provider.inference_contract import (
        CALLER_SPECS,
        InferenceCallerSpec,
        OutputMode,
    )

    for i in range(8):
        CALLER_SPECS[f"caller_{i}"] = InferenceCallerSpec(
            caller_id=f"caller_{i}",
            prompt_template_id=f"caller_{i}",
            prompt_version="v1",
            output_mode=OutputMode.TEXT,
        )

    try:
        results = await asyncio.gather(
            *[
                llm_complete(user=f"test {i}", system="You are helpful", caller=f"caller_{i}")
                for i in range(8)
            ]
        )
    finally:
        for i in range(8):
            CALLER_SPECS.pop(f"caller_{i}", None)


@pytest.mark.anyio
async def test_hard_expiry_fails_closed_no_stale_token(tmp_path, monkeypatch):
    """When OAuth token is hard-expired and refresh fails, no stale provider is cached or used."""
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.config.secrets import set_secret
    from aletheia.model_provider.router import get_provider_async, _cache
    from aletheia.model_provider.openai_native import OpenAINativeProvider

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)

    cfg = load_config()
    cfg.models.providers["openai_native"] = ProviderConfig(
        type="openai_native",
        model="gpt-4o",
        enabled=True,
        auth_type="oauth",
        oauth_token_ref="model.openai_native.oauth_token",
        oauth_refresh_ref="model.openai_native.oauth_refresh",
        oauth_expires_at=int(time.time() * 1000) - 1000,  # already expired
    )
    save_config(cfg)
    set_secret("model.openai_native.oauth_token", "stale-token")
    set_secret("model.openai_native.oauth_refresh", "refresh-val")

    # Pre-populate cache with stale provider
    _cache["openai_native"] = OpenAINativeProvider(model="gpt-4o", oauth_token="stale-token")

    async def mock_resolve_oauth_fail(name, pcfg):
        raise RuntimeError("OAuth service unavailable")

    monkeypatch.setattr(
        "aletheia.model_provider.router._resolve_oauth_token_async", mock_resolve_oauth_fail
    )

    with pytest.raises(ValueError, match="has expired and refresh failed"):
        await get_provider_async("openai_native")

    assert "openai_native" not in _cache, "Stale provider should be removed from cache"


@pytest.mark.anyio
async def test_proactive_refresh_within_margin_anthropic(tmp_path, monkeypatch):
    """Anthropic OAuth token in proactive margin is refreshed before hard expiry."""
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.config.secrets import set_secret
    from aletheia.model_provider.router import get_provider_async

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)

    cfg = load_config()
    # Token expires in 3 minutes — inside the 5-minute refresh margin
    cfg.models.providers["anthropic_native"] = ProviderConfig(
        type="anthropic_native",
        model="claude-sonnet-4-6",
        enabled=True,
        auth_type="oauth",
        oauth_token_ref="model.anthropic_native.oauth_token",
        oauth_refresh_ref="model.anthropic_native.oauth_refresh",
        oauth_expires_at=int(time.time() * 1000) + 3 * 60 * 1000,
    )
    save_config(cfg)
    set_secret("model.anthropic_native.oauth_token", "margin-token")
    set_secret("model.anthropic_native.oauth_refresh", "refresh-val")

    refresh_calls = 0

    async def mock_refresh(_refresh_token):
        nonlocal refresh_calls
        refresh_calls += 1
        return {
            "access_token": "proactive-fresh-token",
            "refresh_token": "rotated",
            "expires_at": int((time.time() + 3600) * 1000),
        }

    monkeypatch.setattr(
        "aletheia.model_provider.claude_oauth.refresh_claude_tokens", mock_refresh
    )

    from aletheia.model_provider.anthropic_native import AnthropicNativeProvider

    provider = await get_provider_async("anthropic_native")

    assert refresh_calls == 1
    assert isinstance(provider, AnthropicNativeProvider)
    assert provider.oauth_token == "proactive-fresh-token"
