import threading

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

    def __init__(
        self,
        *,
        model="claude-primary",
        fail_models=None,
        empty_models=None,
        detected=None,
    ):
        self.model = model
        self.fail_models = fail_models or {}
        self.empty_models = set(empty_models or ())
        self.detected = detected or []
        self.calls: list[str] = []

    async def complete(self, request):  # noqa: ARG002
        self.calls.append(self.model)
        err = self.fail_models.get(self.model)
        if err:
            if isinstance(err, BaseException):
                raise err
            raise RuntimeError(err)
        if self.model in self.empty_models:
            return ModelResponse(
                content="",
                model=self.model,
                usage={"completion_tokens": 600},
                raw={"usage": {"completion_tokens": 600}},
            )
        return ModelResponse(content=f"ok:{self.model}", model=self.model, raw={"ok": True})

    async def list_models(self):
        return [{"slug": m, "display_name": m} for m in self.detected]


class _TrackingLock:
    def __init__(self):
        self._lock = threading.RLock()
        self._local = threading.local()

    def __enter__(self):
        self._lock.acquire()
        self._local.depth = getattr(self._local, "depth", 0) + 1
        return self

    def __exit__(self, *args):
        self._local.depth -= 1
        self._lock.release()

    def held_by_current_thread(self):
        return getattr(self._local, "depth", 0) > 0


def _lock_checked_model_health_state(fallback, provider: str, model: str, lock):
    guarded_fields = {
        "provider",
        "model",
        "consecutive_failures",
        "cooldown_until",
        "last_error",
        "last_failure_at",
        "last_success_at",
    }

    class LockCheckedModelHealthState(fallback.ModelHealthState):
        def __getattribute__(self, name):
            if name in guarded_fields and not lock.held_by_current_thread():
                raise AssertionError(f"{name} read outside model health lock")
            return super().__getattribute__(name)

        def __setattr__(self, name, value):
            if name in guarded_fields and not lock.held_by_current_thread():
                raise AssertionError(f"{name} mutated outside model health lock")
            super().__setattr__(name, value)

    with lock:
        return LockCheckedModelHealthState(provider=provider, model=model)


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
async def test_complete_with_fallback_tries_next_model_on_empty_content(
    tmp_path, monkeypatch,
):
    _setup_config(tmp_path, monkeypatch, fallback_models=["claude-fallback"])
    from aletheia.model_provider import fallback

    provider = FakeProvider(empty_models={"claude-primary"})
    resp = await fallback.complete_with_fallback(
        ModelRequest(messages=[{"role": "user", "content": "x"}]),
        provider=provider,
    )

    assert resp.content == "ok:claude-fallback"
    assert provider.calls == ["claude-primary", "claude-fallback"]
    assert resp.raw["aletheia_model_fallback"]["fallback_used"] is True
    primary = next(
        item for item in fallback.get_model_health_snapshot()
        if item["model"] == "claude-primary"
    )
    assert primary["consecutive_failures"] == 1


@pytest.mark.anyio
async def test_cross_provider_fallback_uses_target_broker_lane(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch, fallback_models=["@provider_b:model-b"])
    from aletheia.model_provider import broker as broker_module
    from aletheia.model_provider import fallback
    from aletheia.model_provider.broker import InferenceBroker, ProviderPolicy

    source = FakeProvider(fail_models={"claude-primary": "429 Too Many Requests"})
    target = FakeProvider(model="model-b")
    providers = {"provider_b": target}
    monkeypatch.setattr(
        fallback.provider_router,
        "get_provider",
        lambda name: providers[name],
    )
    policies = {
        "anthropic_compatible": ProviderPolicy(
            max_concurrency=2,
            queue_capacity=4,
            reserved_high_priority_slots=0,
            timeout_sec=1.0,
            rate_window_sec=0.05,
        ),
        "provider_b": ProviderPolicy(
            max_concurrency=1,
            queue_capacity=4,
            reserved_high_priority_slots=0,
            timeout_sec=1.0,
            rate_window_sec=0.05,
        ),
    }
    test_broker = InferenceBroker(policy_loader=lambda name: policies[name])
    monkeypatch.setattr(broker_module, "inference_broker", test_broker)

    try:
        response = await fallback.complete_with_fallback(
            ModelRequest(messages=[{"role": "user", "content": "x"}]),
            provider=source,
        )
        assert response.content == "ok:model-b"
        metadata = response.raw["aletheia_model_fallback"]
        assert metadata["provider"] == "anthropic_compatible"
        assert metadata["requested_model"] == "anthropic_compatible/claude-primary"

        snapshot = await test_broker.snapshot()
        assert snapshot["providers"]["provider_b"]["metrics"]["completed"] == 1
        assert snapshot["providers"]["anthropic_compatible"]["metrics"]["failed"] == 1
    finally:
        await test_broker.aclose()


@pytest.mark.anyio
async def test_complete_with_fallback_never_uses_discovery_when_config_list_empty(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch, fallback_models=[])
    from aletheia.model_provider import fallback

    provider = FakeProvider(
        fail_models={"claude-primary": "502 Bad Gateway"},
        detected=["claude-primary", "claude-detected"],
    )
    from unittest.mock import AsyncMock

    discovery = AsyncMock(return_value=[{"slug": "claude-detected"}])
    monkeypatch.setattr(fallback.provider_router, "list_models_cached", discovery)
    with pytest.raises(RuntimeError, match="502 Bad Gateway"):
        await fallback.complete_with_fallback(
            ModelRequest(messages=[{"role": "user", "content": "x"}]),
            provider=provider,
        )

    assert provider.calls == ["claude-primary"]
    discovery.assert_not_awaited()


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
    assert meta["skipped_cooldown_models"] == ["anthropic_compatible/claude-primary"]
    health = fallback.get_model_health_snapshot()
    primary = next(x for x in health if x["model"] == "claude-primary")
    assert primary["cooling_down"] is True


def test_model_health_success_and_failure_transitions_hold_the_lock(monkeypatch):
    from aletheia.model_provider import fallback

    lock = _TrackingLock()
    events = []
    monkeypatch.setattr(fallback, "_MODEL_HEALTH_LOCK", lock)
    monkeypatch.setattr(fallback, "_COOLDOWN_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr(fallback, "_COOLDOWN_SEC", 60)
    monkeypatch.setattr(fallback, "emit_infra_event", lambda **event: events.append(event))
    with lock:
        fallback._MODEL_HEALTH.clear()
        fallback._MODEL_HEALTH[("provider-a", "model-a")] = (
            _lock_checked_model_health_state(fallback, "provider-a", "model-a", lock)
        )

    fallback._record_model_failure("provider-a", "model-a", RuntimeError("first"))
    fallback._record_model_failure("provider-a", "model-a", RuntimeError("second"))
    fallback._record_model_success("provider-a", "model-a")

    codes = [event["code"] for event in events]
    assert codes == ["llm_model_cooldown", "llm_model_recovered"]
    [snapshot] = fallback.get_model_health_snapshot()
    assert snapshot["consecutive_failures"] == 0
    assert snapshot["cooling_down"] is False


def test_model_health_snapshot_serializes_state_under_the_lock(monkeypatch):
    from aletheia.model_provider import fallback

    lock = _TrackingLock()
    monkeypatch.setattr(fallback, "_MODEL_HEALTH_LOCK", lock)
    with lock:
        fallback._MODEL_HEALTH.clear()
        state = _lock_checked_model_health_state(
            fallback, "provider-a", "model-a", lock,
        )
        state.consecutive_failures = 2
        state.cooldown_until = 9_999_999_999.0
        fallback._MODEL_HEALTH[("provider-a", "model-a")] = state

    [snapshot] = fallback.get_model_health_snapshot()
    assert snapshot["provider"] == "provider-a"
    assert snapshot["model"] == "model-a"
    assert snapshot["consecutive_failures"] == 2
    assert snapshot["cooling_down"] is True


def test_concurrent_model_failures_are_counted_and_emit_one_cooldown(monkeypatch):
    from aletheia.model_provider import fallback

    fallback.reset_model_health()
    events = []
    monkeypatch.setattr(fallback, "_COOLDOWN_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr(fallback, "_COOLDOWN_SEC", 60)
    monkeypatch.setattr(fallback, "emit_infra_event", lambda **event: events.append(event))
    barrier = threading.Barrier(3)

    def _fail(label):
        barrier.wait(timeout=5)
        fallback._record_model_failure("provider-a", "model-a", RuntimeError(label))

    threads = [threading.Thread(target=_fail, args=(label,)) for label in ("a", "b")]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    [snapshot] = fallback.get_model_health_snapshot()
    assert snapshot["consecutive_failures"] == 2
    assert snapshot["cooling_down"] is True
    assert [event["code"] for event in events] == ["llm_model_cooldown"]


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
async def test_settings_model_persists_fallback_models_without_key_echo(client, monkeypatch):
    from tests.model_http import mock_model_probe

    mock_model_probe(monkeypatch)
    secret = "sk-secret-should-not-echo"
    resp = await client.post("/api/settings/model-profiles", json={
        "profile_id": "anthropic_compatible", "type": "anthropic_compatible", "display_name": "HTTP",
        "base_url": "http://model.test", "model": "m1",
        "model_fallback_enabled": True,
        "fallback_models": ["claude-b", " claude-b ", "claude-c"],
        "api_key": secret,
    })
    assert resp.status_code == 201
    assert secret not in resp.text

    cfg = await client.get("/api/model")
    assert secret not in cfg.text
    provider = cfg.json()["providers"]["anthropic_compatible"]
    assert provider["model_fallback_enabled"] is True
    assert provider["fallback_models"] == ["claude-b", "claude-c"]
