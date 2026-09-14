"""Tests for model provider layer."""

import asyncio
import threading
import pytest  # pyright: ignore[reportMissingImports]
from unittest.mock import patch, AsyncMock
from tests.model_http import model_http

from aletheia.model_provider.base import ModelRequest, ModelHealth, ModelProvider, ProviderResponseError
from aletheia.model_provider.openai_compatible import (
    OpenAICompatibleProvider,
    _extract_chat_completion_text,
)
from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider
from aletheia.model_provider.router import (
    clear_cache,
    healthcheck_all,
    invalidate_health_cache,
    invalidate_model_directory,
    get_provider,
    get_provider_async,
    list_models_cached,
    ProviderNotConfigured,
)


@pytest.fixture(autouse=True)
def _bind_http_constructor(monkeypatch):
    from aletheia.model_provider import router
    from aletheia.model_provider.http_runtime import build_http_provider, http_configuration_identity

    monkeypatch.setattr(router, "_provider_factory", build_http_provider)
    monkeypatch.setattr(router, "_provider_identity_reader", http_configuration_identity)


@pytest.mark.parametrize("bound", [None, "selected-endpoint"])
def test_attempt_endpoint_identity_is_never_guessed_from_current_config(monkeypatch, bound):
    from types import SimpleNamespace
    from aletheia.model_provider.base import _configured_provider_name, clone_provider_with_model

    reads = []

    def config():
        reads.append(True)
        return SimpleNamespace(models=SimpleNamespace(providers={
            "unrelated-endpoint": SimpleNamespace(type="openai_compatible", base_url="https://shared.test", model="same"),
        }))

    monkeypatch.setattr("aletheia.config.loader.load_config", config)
    provider = SimpleNamespace(name="unbound-transport", base_url="https://shared.test", model="same")
    if bound:
        provider._aletheia_provider_name = bound
    assert _configured_provider_name(provider) == (bound or "unbound-transport")
    assert _configured_provider_name(clone_provider_with_model(provider, "another-model")) == (bound or "unbound-transport")
    assert reads == []


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
        enabled=True,
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
        type="openai_compatible",
        base_url="http://localhost:8000/v1",
        enabled=True,
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
    cfg.models.providers["anthropic_compatible"] = ProviderConfig(
        type="anthropic_compatible",
        base_url="http://localhost:8001",
        enabled=True,
    )
    save_config(cfg)
    clear_cache()
    provider = get_provider("anthropic_compatible")
    assert isinstance(provider, AnthropicCompatibleProvider)
    assert provider.protocol == "anthropic_messages"
    clear_cache()


def test_router_rejects_enabled_provider_without_endpoint(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    clear_cache()
    cfg = load_config()
    cfg.models.providers["missing-endpoint"] = ProviderConfig(
        type="openai_compatible",
        enabled=True,
    )
    save_config(cfg)

    with pytest.raises(ProviderNotConfigured, match="not_configured"):
        get_provider("missing-endpoint")
    clear_cache()


@pytest.mark.anyio
async def test_async_provider_configuration_check_runs_off_event_loop(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.model_provider.http_runtime.get_secret", lambda _ref: "secret")
    clear_cache()
    cfg = load_config()
    cfg.models.providers["async-provider"] = ProviderConfig(
        type="openai_compatible",
        base_url="http://localhost:8000/v1",
        enabled=True,
    )
    save_config(cfg)
    loop_thread = threading.get_ident()
    check_thread = None

    def record_check(_name, _pcfg):
        nonlocal check_thread
        check_thread = threading.get_ident()

    monkeypatch.setattr("aletheia.model_provider.router._require_provider_configured", record_check)
    await get_provider_async("async-provider")

    assert check_thread is not None
    assert check_thread != loop_thread
    clear_cache()


@pytest.mark.anyio
async def test_async_router_shares_one_cached_instance(monkeypatch):
    import asyncio
    import time
    from aletheia.config.schema import AletheiaConfig, ProviderConfig
    from aletheia.model_provider import router

    cfg = AletheiaConfig()
    cfg.models.providers["http"] = ProviderConfig(
        type="openai_compatible", enabled=True, base_url="https://example.invalid/v1",
        api_key_secret_ref="synthetic-reference",
    )
    monkeypatch.setattr(router, "load_config", lambda: cfg)

    def read(_ref):
        time.sleep(0.02)
        return "synthetic-key"

    monkeypatch.setattr("aletheia.model_provider.http_runtime.get_secret", read)
    clear_cache()
    try:
        providers = await asyncio.gather(*(get_provider_async("http") for _ in range(8)))
        assert all(provider is providers[0] for provider in providers)
        assert get_provider("http") is providers[0]
    finally:
        clear_cache()


@pytest.mark.anyio
async def test_router_binds_exact_profile_identity(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    clear_cache()
    cfg = load_config()
    for name in ("profile-a", "profile-b"):
        cfg.models.providers[name] = ProviderConfig(
            type="openai_compatible",
            base_url="http://localhost:8000/v1",
            model="shared-model",
            enabled=True,
        )
    save_config(cfg)

    sync_provider = get_provider("profile-a")
    assert sync_provider._aletheia_provider_name == "profile-a"

    clear_cache()
    async_provider = await get_provider_async("profile-b")
    assert async_provider._aletheia_provider_name == "profile-b"
    clear_cache()


def test_cached_provider_is_revalidated_when_config_becomes_unconfigured(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    clear_cache()
    cfg = load_config()
    cfg.models.providers["mutable-provider"] = ProviderConfig(
        type="openai_compatible",
        base_url="http://localhost:8000/v1",
        enabled=True,
    )
    save_config(cfg)
    assert get_provider("mutable-provider") is not None

    cfg = load_config()
    cfg.models.providers["mutable-provider"].base_url = ""
    save_config(cfg)
    with pytest.raises(ProviderNotConfigured, match="not_configured"):
        get_provider("mutable-provider")
    clear_cache()


def test_router_rejects_invalid_loaded_http_protocol(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    clear_cache()
    cfg = load_config()
    cfg.models.providers["bad_profile"] = ProviderConfig.model_validate(
        {
            "type": "openai_compatible",
            "base_url": "http://localhost:8000/v1",
            "protocol": "anthropic_messages",
            "enabled": True,
        }
    )
    save_config(cfg)
    clear_cache()

    with pytest.raises(ValueError, match="Unsupported openai_compatible protocol"):
        get_provider("bad_profile")
    clear_cache()


def test_openai_provider_rejects_unknown_protocol_before_chat_fallback():
    with pytest.raises(ValueError, match="Unsupported openai_compatible protocol"):
        OpenAICompatibleProvider(
            base_url="http://localhost:8000/v1",
            model="test",
            protocol="chat_completion",
        )


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
    import httpx

    created = []

    def factory(*, timeout):
        client = httpx.AsyncClient(
            timeout=timeout,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [{"id": "m1"}]})),
        )
        created.append(client)
        return client

    monkeypatch.setattr(
        "aletheia.model_provider.openai_compatible.httpx_client",
        factory,
    )
    provider = OpenAICompatibleProvider(base_url="http://model.test/v1", model="m1")

    assert await provider.list_models()
    assert await provider.list_models()
    assert len(created) == 1
    await provider.aclose()
    assert created[0].is_closed


@pytest.mark.anyio
@pytest.mark.parametrize("provider_type", [OpenAICompatibleProvider, AnthropicCompatibleProvider])
async def test_cold_model_clones_share_owner_pool_and_keep_loop_isolation(provider_type):
    from aletheia.model_provider.base import clone_provider_with_model

    class Client:
        closed = False

        async def aclose(self):
            self.closed = True

    made = []

    def factory(**_kwargs):
        client = Client()
        made.append(client)
        return client

    owner = provider_type(base_url="http://model.test/v1", model="preferred")
    small = clone_provider_with_model(owner, "small")
    large = clone_provider_with_model(owner, "large")
    main_client = small._get_http_client(factory)
    assert large._get_http_client(factory) is main_client
    assert owner._get_http_client(factory) is main_client
    assert len(made) == 1
    assert (owner.model, small.model, large.model) == ("preferred", "small", "large")

    async def other_loop():
        other = clone_provider_with_model(owner, "other")
        other_client = other._get_http_client(factory)
        assert other_client is not main_client
        await owner.aclose()
        assert other_client.closed

    await asyncio.to_thread(lambda: asyncio.run(other_loop()))
    assert len(made) == 2 and not main_client.closed
    await owner.aclose()
    assert main_client.closed
    assert owner._http_clients == small._http_clients == large._http_clients == {}


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
async def test_healthcheck_all_probes_enabled_keyless_local_provider(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)

    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig

    cfg = load_config()
    cfg.models.providers["openai_compatible"] = ProviderConfig(
        type="openai_compatible",
        base_url="http://localhost:8000/v1",
        model="local-model",
        enabled=True,
    )
    save_config(cfg)
    calls = 0

    async def healthy(self):
        nonlocal calls
        calls += 1
        return ModelHealth(provider="openai_compatible", available=True, model=self.model)

    monkeypatch.setattr(OpenAICompatibleProvider, "healthcheck", healthy)
    clear_cache()
    results = await healthcheck_all(force=True)

    assert calls == 1
    assert results[0].available is True
    assert results[0].observed_at is not None
    clear_cache()


@pytest.mark.anyio
async def test_openai_healthcheck_no_server():
    import httpx  # pyright: ignore[reportMissingImports]

    class FailClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            raise httpx.ConnectError("refused")

        async def post(self, url, **k):
            raise httpx.ConnectError("refused")

    provider = OpenAICompatibleProvider(base_url="http://localhost:19999/v1", model="test")
    with model_http(get=FailClient().get):
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
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            seen["headers"] = headers or {}
            return FakeResp()

    with model_http(get=FakeClient().get):
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

        def raise_for_status(self):
            pass

        def json(self):
            return mock_response

    with model_http(post=AsyncMock(return_value=FakeResponse())):
        req = ModelRequest(messages=[{"role": "user", "content": "hi"}])
        resp = await provider.complete(req)
        assert resp.content == "Hello world"
        assert resp.model == "test"


def test_openai_chat_missing_choices_is_provider_response_error():
    with pytest.raises(ProviderResponseError, match="openai_chat_missing_choices"):
        _extract_chat_completion_text({"id": "cmpl-glm", "object": "chat.completion"})


def test_openai_chat_extracts_legacy_choice_text():
    assert _extract_chat_completion_text({"choices": [{"text": "plain"}]}) == "plain"


@pytest.mark.anyio
async def test_openai_complete_missing_choices_raises_provider_response_error():
    provider = OpenAICompatibleProvider(base_url="http://localhost:8000/v1", model="glm-5.2")

    class FakeResponse:
        status_code = 200
        is_success = True

        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "cmpl-glm"}

    with model_http(post=AsyncMock(return_value=FakeResponse())):
        with pytest.raises(ProviderResponseError, match="openai_chat_missing_choices"):
            await provider.complete(ModelRequest(messages=[{"role": "user", "content": "hi"}]))


@pytest.mark.anyio
async def test_openai_complete_sends_explicit_reasoning_effort_only_when_requested():
    provider = OpenAICompatibleProvider(base_url="http://localhost:8000/v1", model="test")
    mock_response = {
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "model": "test",
        "usage": {"completion_tokens": 6},
    }

    class FakeResponse:
        status_code = 200

        def json(self):
            return mock_response

    post = AsyncMock(return_value=FakeResponse())
    with model_http(post=post):
        await provider.complete(
            ModelRequest(
                messages=[{"role": "user", "content": "return json"}],
                reasoning_effort="none",
            )
        )
        await provider.complete(ModelRequest(messages=[{"role": "user", "content": "hello"}]))

    first_payload = post.await_args_list[0].kwargs["json"]
    second_payload = post.await_args_list[1].kwargs["json"]
    assert first_payload["reasoning_effort"] == "none"
    assert "reasoning_effort" not in second_payload


@pytest.mark.anyio
async def test_openai_complete_retries_once_without_unsupported_reasoning_effort():
    import httpx

    provider = OpenAICompatibleProvider(base_url="http://localhost:8000/v1", model="test")
    unsupported = httpx.Response(
        400,
        request=httpx.Request("POST", "http://localhost:8000/v1/chat/completions"),
        json={"error": {"message": 'json: unknown field "reasoning_effort"'}},
    )
    success = httpx.Response(
        200,
        request=httpx.Request("POST", "http://localhost:8000/v1/chat/completions"),
        json={
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "model": "test",
            "usage": {"completion_tokens": 6},
        },
    )
    post = AsyncMock(side_effect=[unsupported, success])

    with model_http(post=post):
        response = await provider.complete(
            ModelRequest(
                messages=[{"role": "user", "content": "return json"}],
                reasoning_effort="none",
            )
        )

    assert response.content == '{"ok": true}'
    assert post.await_count == 2
    assert post.await_args_list[0].kwargs["json"]["reasoning_effort"] == "none"
    assert "reasoning_effort" not in post.await_args_list[1].kwargs["json"]


@pytest.mark.anyio
async def test_openai_complete_does_not_retry_unrelated_bad_request():
    import httpx

    provider = OpenAICompatibleProvider(base_url="http://localhost:8000/v1", model="test")
    bad_request = httpx.Response(
        400,
        request=httpx.Request("POST", "http://localhost:8000/v1/chat/completions"),
        json={"error": {"message": "invalid model"}},
    )
    post = AsyncMock(return_value=bad_request)

    with model_http(post=post):
        with pytest.raises(httpx.HTTPStatusError):
            await provider.complete(
                ModelRequest(
                    messages=[{"role": "user", "content": "return json"}],
                    reasoning_effort="none",
                )
            )

    assert post.await_count == 1


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

        def raise_for_status(self):
            pass

        def json(self):
            return mock_response

    with model_http(provider="anthropic_compatible", post=AsyncMock(return_value=FakeResponse())):
        req = ModelRequest(messages=[{"role": "user", "content": "hi"}])
        resp = await provider.complete(req)
        assert resp.content == "Bonjour"


# ── list_models tests ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_openai_list_models_parses_data_array():
    provider = OpenAICompatibleProvider(base_url="http://gw/v1", model="m")

    class FakeResp:
        status_code = 200

        def json(self):
            return {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return FakeResp()

    with model_http(get=FakeClient().get):
        models = await provider.list_models()
    assert len(models) == 2
    assert models[0] == {"slug": "gpt-4o", "display_name": "gpt-4o"}
    assert models[1] == {"slug": "gpt-4o-mini", "display_name": "gpt-4o-mini"}


@pytest.mark.anyio
async def test_openai_list_models_returns_empty_on_failure():
    import httpx  # pyright: ignore[reportMissingImports]

    class FailClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            raise httpx.ConnectError("refused")

    provider = OpenAICompatibleProvider(base_url="http://localhost:19999/v1", model="m")
    with model_http(get=FailClient().get):
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
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return FakeResp()

    with model_http(provider="anthropic_compatible", get=FakeClient().get):
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
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return FakeResp()

    with model_http(provider="anthropic_compatible", get=FakeClient().get):
        models = await provider.list_models()
    assert len(models) == 2
    assert models[0] == {"slug": "claude-opus-4", "display_name": "claude-opus-4"}


@pytest.mark.anyio
async def test_anthropic_list_models_returns_empty_on_failure():
    import httpx  # pyright: ignore[reportMissingImports]

    class FailClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            raise httpx.ConnectError("refused")

    provider = AnthropicCompatibleProvider(base_url="http://localhost:19999", model="m")
    with model_http(provider="anthropic_compatible", get=FailClient().get):
        models = await provider.list_models()
    assert models == []


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


# ── Responses protocol payload contract ─────────────────────────────────────


@pytest.mark.anyio
async def test_responses_protocol_sends_strict_json_schema_in_text_format():
    """Responses payload must place json_schema under text.format — never top-level."""
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="deepseek-v4-flash",
        protocol="responses",
    )
    sent = {}

    class FakeResponse:
        status_code = 200
        is_success = True

        def json(self):
            return {"output_text": '{"ok": true}', "model": "deepseek-v4-flash", "usage": {}}

    async def fake_post(url, json=None, headers=None):
        sent.update(url=url, payload=json)
        return FakeResponse()

    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    with model_http(post=fake_post):
        await provider.complete(
            ModelRequest(
                messages=[{"role": "user", "content": "go"}],
                response_format="json_schema",
                structured_schema=schema,
                structured_schema_name="plan_intent",
            )
        )

    fmt = sent["payload"]["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["name"] == "plan_intent"
    assert fmt["strict"] is True
    assert fmt["schema"] == schema
    assert "response_format" not in sent["payload"]


@pytest.mark.anyio
async def test_responses_protocol_maps_reasoning_effort_to_reasoning_object():
    """reasoning_effort must be forwarded as reasoning={"effort": ...}, not the flat key."""
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="deepseek-v4-flash",
        protocol="responses",
    )
    sent = {}

    class FakeResponse:
        status_code = 200
        is_success = True

        def json(self):
            return {"output_text": "ok", "model": "deepseek-v4-flash", "usage": {}}

    async def fake_post(url, json=None, headers=None):
        sent.update(url=url, payload=json)
        return FakeResponse()

    with model_http(post=fake_post):
        await provider.complete(
            ModelRequest(
                messages=[{"role": "user", "content": "go"}],
                reasoning_effort="none",
            )
        )

    assert sent["payload"]["reasoning"] == {"effort": "none"}
    assert "reasoning_effort" not in sent["payload"]


@pytest.mark.anyio
@pytest.mark.parametrize("rejected_param", ["reasoning", "reasoning.effort"])
async def test_responses_protocol_retries_without_reasoning_preserving_schema(rejected_param):
    """On explicit reasoning-field rejection, retry once and keep text.format unchanged."""
    import httpx

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="deepseek-v4-flash",
        protocol="responses",
    )
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    rejected = httpx.Response(
        400,
        request=httpx.Request("POST", "http://localhost:8000/v1/responses"),
        json={"error": {"param": rejected_param, "message": f"Unknown parameter: '{rejected_param}'"}},
    )
    ok_body = {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"ok": true}'}]}],
        "model": "deepseek-v4-flash",
        "usage": {},
    }
    success = httpx.Response(
        200,
        request=httpx.Request("POST", "http://localhost:8000/v1/responses"),
        json=ok_body,
    )
    post = AsyncMock(side_effect=[rejected, success])

    with model_http(post=post):
        response = await provider.complete(
            ModelRequest(
                messages=[{"role": "user", "content": "go"}],
                response_format="json_schema",
                structured_schema=schema,
                structured_schema_name="plan_intent",
                reasoning_effort="none",
            )
        )

    assert response.content == '{"ok": true}'
    assert post.await_count == 2
    first = post.await_args_list[0].kwargs["json"]
    second = post.await_args_list[1].kwargs["json"]
    # First attempt included reasoning
    assert first["reasoning"] == {"effort": "none"}
    assert first["text"]["format"]["type"] == "json_schema"
    assert first["text"]["format"]["strict"] is True
    # Retry drops reasoning but leaves text.format intact
    assert "reasoning" not in second
    assert second["text"]["format"]["type"] == "json_schema"
    assert second["text"]["format"]["name"] == "plan_intent"
    assert second["text"]["format"]["strict"] is True
    assert second["text"]["format"]["schema"] == schema
