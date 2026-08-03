"""OAuth refresh is single-flight across concurrent provider resolution."""

import asyncio

import pytest  # pyright: ignore[reportMissingImports]


@pytest.mark.anyio
async def test_concurrent_oauth_refresh_is_single_flight(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.config.secrets import set_secret
    from aletheia.model_provider.router import _resolve_oauth_token_async

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)

    cfg = load_config()
    cfg.models.providers["openai_native"] = ProviderConfig(
        type="openai_native",
        enabled=True,
        auth_type="oauth",
        oauth_token_ref="model.openai_native.oauth_token",
        oauth_refresh_ref="model.openai_native.oauth_refresh",
        oauth_expires_at=1,
    )
    save_config(cfg)
    set_secret("model.openai_native.oauth_token", "expired-token")
    set_secret("model.openai_native.oauth_refresh", "refresh-token")

    calls = 0

    async def refresh(_refresh_token):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return {
            "api_key": "fresh-token",
            "refresh_token": "rotated-refresh-token",
            "expires_at": 4_102_444_800_000,
        }

    monkeypatch.setattr("aletheia.model_provider.openai_oauth.refresh_openai_tokens", refresh)
    profile = load_config().models.providers["openai_native"]
    tokens = await asyncio.gather(
        *[_resolve_oauth_token_async("openai_native", profile) for _ in range(8)]
    )

    assert tokens == ["fresh-token"] * 8
    assert calls == 1


@pytest.mark.anyio
async def test_sync_follower_on_event_loop_never_blocks_async_refresh():
    from concurrent.futures import Future

    from aletheia.model_provider import router

    pending: Future[str | None] = Future()
    with router._oauth_refresh_lock:
        router._oauth_refresh_inflight["openai_native"] = pending
    try:
        assert router._refresh_oauth_singleflight("openai_native") is None
    finally:
        with router._oauth_refresh_lock:
            router._oauth_refresh_inflight.pop("openai_native", None)
        pending.cancel()
