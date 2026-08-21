import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from aletheia.model_provider.base import ModelRequest, ModelResponse
from aletheia.model_provider.router import clear_cache
from aletheia.server.app import app

pytestmark = pytest.mark.real_llm


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    clear_cache()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    clear_cache()


def _setup_config(tmp_path, monkeypatch, *, fallback_models=None):
    import aletheia.config.loader as loader
    from aletheia.config.schema import AletheiaConfig, ProviderConfig
    from aletheia.config.loader import save_config
    from aletheia.model_provider.router import clear_cache

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    cfg = AletheiaConfig()
    cfg.models.default_provider = "anthropic_compatible"
    # Providers ship empty now — seed the one under test explicitly.
    pcfg = cfg.models.providers["anthropic_compatible"] = ProviderConfig(
        type="anthropic_compatible", enabled=True
    )
    pcfg.model = "claude-primary"
    pcfg.fallback_models = fallback_models or []
    save_config(cfg)
    clear_cache()
    from aletheia.model_provider.fallback import reset_model_health
    from aletheia.infra.events import clear_infra_events
    reset_model_health()
    clear_infra_events()
    return cfg


class FakeProvider:
    name = "anthropic_compatible"

    def __init__(self, *, model="claude-primary", fail_models=None, detected=None):
        self.model = model
        self.fail_models = fail_models or {}
        self.detected = detected or []
        self.calls: list[str] = []

    async def complete(self, request):  # noqa: ARG002
        self.calls.append(self.model)
        err = self.fail_models.get(self.model)
        if err:
            if isinstance(err, BaseException):
                raise err
            raise RuntimeError(err)
        return ModelResponse(content=f"ok:{self.model}", model=self.model, raw={"ok": True})

    async def list_models(self):
        return [{"slug": m, "display_name": m} for m in self.detected]


@pytest.mark.anyio
async def test_complete_with_fallback_tries_configured_model_on_429(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch, fallback_models=["claude-fallback"])
    from aletheia.model_provider import fallback

    provider = FakeProvider(fail_models={"claude-primary": "429 Too Many Requests"})
    resp = await fallback.complete_with_fallback(
        ModelRequest(messages=[{"role": "user", "content": "x"}]),
        provider=provider,
    )

    assert resp.content == "ok:claude-fallback"
    assert provider.calls == ["claude-primary", "claude-fallback"]
    meta = resp.raw["aletheia_model_fallback"]
    assert meta["fallback_used"] is True
    assert meta["attempted_models"] == ["claude-primary", "claude-fallback"]


@pytest.mark.anyio
async def test_discovered_models_not_used_as_fallback(tmp_path, monkeypatch):
    """Discovered catalogue models are no longer automatic fallback candidates."""
    _setup_config(tmp_path, monkeypatch, fallback_models=[])
    from aletheia.model_provider import fallback

    provider = FakeProvider(
        fail_models={"claude-primary": "502 Bad Gateway"},
        detected=["claude-primary", "claude-detected", "claude-audio"],
    )

    with pytest.raises(RuntimeError, match="502"):
        await fallback.complete_with_fallback(
            ModelRequest(messages=[{"role": "user", "content": "x"}]),
            provider=provider,
        )

    # Only primary was tried; discovered models were NOT attempted
    assert provider.calls == ["claude-primary"]

@pytest.mark.anyio
async def test_complete_with_fallback_retries_after_connect_error(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch, fallback_models=["claude-fallback"])
    from aletheia.model_provider import fallback

    provider = FakeProvider(
        fail_models={"claude-primary": httpx.ConnectError("connection reset")}
    )
    resp = await fallback.complete_with_fallback(
        ModelRequest(messages=[{"role": "user", "content": "x"}]),
        provider=provider,
    )

    assert resp.content == "ok:claude-fallback"
    assert provider.calls == ["claude-primary", "claude-fallback"]


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection reset"),
        httpx.RemoteProtocolError("peer disconnected"),
        httpx.ProxyError("proxy unavailable"),
    ],
)
def test_transport_failures_are_transient(exc):
    from aletheia.model_provider.fallback import is_transient_model_error

    assert is_transient_model_error(exc) is True


def test_timeout_does_not_expand_across_the_full_model_chain():
    from aletheia.model_provider.fallback import is_transient_model_error

    assert is_transient_model_error(httpx.ReadTimeout("provider stalled")) is False


@pytest.mark.anyio
async def test_transient_failures_put_model_in_cooldown_and_skip_it(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch, fallback_models=["claude-fallback"])
    from aletheia.model_provider import fallback

    monkeypatch.setattr(fallback, "_COOLDOWN_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr(fallback, "_COOLDOWN_SEC", 60)
    provider = FakeProvider(fail_models={"claude-primary": "429 Too Many Requests"})

    for _ in range(2):
        resp = await fallback.complete_with_fallback(
            ModelRequest(messages=[{"role": "user", "content": "x"}]),
            provider=provider,
        )
        assert resp.content == "ok:claude-fallback"

    resp = await fallback.complete_with_fallback(
        ModelRequest(messages=[{"role": "user", "content": "x"}]),
        provider=provider,
    )
    assert resp.content == "ok:claude-fallback"
    assert provider.calls == [
        "claude-primary", "claude-fallback",
        "claude-primary", "claude-fallback",
        "claude-fallback",
    ]
    meta = resp.raw["aletheia_model_fallback"]
    assert meta["skipped_cooldown_models"] == ["claude-primary"]
    health = fallback.get_model_health_snapshot()
    primary = next(x for x in health if x["model"] == "claude-primary")
    assert primary["cooling_down"] is True


@pytest.mark.anyio
async def test_infra_events_expose_llm_cooldown(tmp_path, client, monkeypatch):
    _setup_config(tmp_path, monkeypatch, fallback_models=["claude-fallback"])
    from aletheia.model_provider import fallback

    monkeypatch.setattr(fallback, "_COOLDOWN_FAILURE_THRESHOLD", 1)
    provider = FakeProvider(fail_models={"claude-primary": "502 Bad Gateway"})
    await fallback.complete_with_fallback(
        ModelRequest(messages=[{"role": "user", "content": "x"}]),
        provider=provider,
    )

    resp = await client.get("/api/infra/events")
    assert resp.status_code == 200
    codes = [e["code"] for e in resp.json()["events"]]
    assert "llm_model_cooldown" in codes


@pytest.mark.anyio
async def test_empty_fallback_models_no_catalogue_tried_on_429(tmp_path, monkeypatch):
    """When fallback_models is empty, a primary 429 raises without trying any catalogue model."""
    _setup_config(tmp_path, monkeypatch, fallback_models=[])
    from aletheia.model_provider import fallback

    provider = FakeProvider(
        fail_models={"claude-primary": "429 Too Many Requests"},
        detected=["claude-primary", "claude-embedding", "claude-audio"],
    )

    with pytest.raises(RuntimeError, match="429"):
        await fallback.complete_with_fallback(
            ModelRequest(messages=[{"role": "user", "content": "x"}]),
            provider=provider,
        )

    # No catalogue models tried
    assert provider.calls == ["claude-primary"]

@pytest.mark.anyio
async def test_complete_with_fallback_does_not_hide_non_transient_errors(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch, fallback_models=["claude-fallback"])
    from aletheia.model_provider import fallback

    provider = FakeProvider(fail_models={"claude-primary": "401 Unauthorized"})
    with pytest.raises(RuntimeError, match="401 Unauthorized"):
        await fallback.complete_with_fallback(
            ModelRequest(messages=[{"role": "user", "content": "x"}]),
            provider=provider,
        )

    assert provider.calls == ["claude-primary"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("exc", "code"),
    [
        ("InferenceQueueFull", "llm_broker_queue_full"),
        ("InferencePolicyReload", "llm_broker_policy_reload"),
        ("InferenceTimeout", "llm_broker_timeout"),
        ("InferenceRateLimitExceeded", "llm_broker_rate_limit_exceeded"),
        ("InferenceBrokerClosed", "llm_broker_closed"),
    ],
)
async def test_terminal_broker_failures_emit_safe_typed_events(tmp_path, monkeypatch, exc, code):
    _setup_config(tmp_path, monkeypatch, fallback_models=["claude-fallback"])
    from aletheia.infra.events import list_infra_events
    from aletheia.model_provider import fallback
    from aletheia.model_provider import broker

    secret = "sk-terminal-secret"
    prompt = "prompt that must never reach observability"
    failure = getattr(broker, exc)(f"Authorization: Bearer {secret}; {prompt}")

    async def fail(*args, **kwargs):  # noqa: ARG001
        raise failure

    monkeypatch.setattr(fallback, "brokered_complete", fail)
    with pytest.raises(type(failure)):
        await fallback.complete_with_fallback(
            ModelRequest(messages=[{"role": "user", "content": prompt}]),
            provider=FakeProvider(),
        )

    event = next(event for event in list_infra_events() if event["code"] == code)
    assert event["metadata"]["attempted_models"] == ["claude-primary"]
    assert secret not in str(event)
    assert prompt not in str(event)


@pytest.mark.anyio
async def test_no_candidate_broker_failure_emits_safe_typed_event(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch)
    from aletheia.infra.events import list_infra_events
    from aletheia.model_provider import fallback
    from aletheia.model_provider.broker import InferenceQueueFull

    secret = "sk-terminal-secret"

    async def no_models(*args, **kwargs):  # noqa: ARG001
        return []

    async def fail(*args, **kwargs):  # noqa: ARG001
        raise InferenceQueueFull(f"Authorization: Bearer {secret}")

    monkeypatch.setattr(fallback, "_candidate_models", no_models)
    monkeypatch.setattr(fallback, "brokered_complete", fail)
    with pytest.raises(InferenceQueueFull):
        await fallback.complete_with_fallback(ModelRequest(messages=[]), provider=FakeProvider(model=""))

    event = next(event for event in list_infra_events() if event["code"] == "llm_broker_queue_full")
    assert event["metadata"]["attempted_models"] == []
    assert secret not in str(event)


@pytest.mark.anyio
async def test_terminal_provider_failures_emit_safe_typed_events(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch, fallback_models=["claude-fallback"])
    from aletheia.infra.events import list_infra_events
    from aletheia.model_provider import fallback

    secret = "sk-terminal-secret"
    prompt = "prompt that must never reach observability"
    provider = FakeProvider(fail_models={"claude-primary": f"401 {secret}; {prompt}"})
    with pytest.raises(RuntimeError):
        await fallback.complete_with_fallback(
            ModelRequest(messages=[{"role": "user", "content": prompt}]),
            provider=provider,
        )

    event = next(event for event in list_infra_events() if event["code"] == "llm_call_non_transient_error")
    assert event["metadata"]["attempted_models"] == ["claude-primary"]
    assert secret not in str(event)
    assert prompt not in str(event)


@pytest.mark.anyio
async def test_exhausted_transient_fallback_emits_safe_typed_event(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch, fallback_models=["claude-fallback"])
    from aletheia.infra.events import list_infra_events
    from aletheia.model_provider import fallback

    secret = "sk-terminal-secret"
    prompt = "prompt that must never reach observability"
    provider = FakeProvider(
        fail_models={
            "claude-primary": f"503 {secret}; {prompt}",
            "claude-fallback": f"503 {secret}; {prompt}",
        }
    )
    with pytest.raises(RuntimeError):
        await fallback.complete_with_fallback(
            ModelRequest(messages=[{"role": "user", "content": prompt}]),
            provider=provider,
        )

    event = next(event for event in list_infra_events() if event["code"] == "llm_call_exhausted")
    assert event["metadata"]["attempted_models"] == ["claude-primary", "claude-fallback"]
    assert secret not in str(event)
    assert prompt not in str(event)


@pytest.mark.anyio
async def test_settings_model_persists_fallback_models_without_key_echo(client):
    secret = "sk-secret-should-not-echo"
    resp = await client.post("/api/settings/model", json={
        "provider": "anthropic_compatible",
        "model_fallback_enabled": True,
        "fallback_models": ["claude-b", " claude-b ", "claude-c"],
        "api_key": secret,
    })
    assert resp.status_code == 200
    assert secret not in resp.text

    cfg = await client.get("/api/model")
    assert secret not in cfg.text
    provider = cfg.json()["providers"]["anthropic_compatible"]
    assert provider["model_fallback_enabled"] is True
    assert provider["fallback_models"] == ["claude-b", "claude-c"]
