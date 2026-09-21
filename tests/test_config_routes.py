"""Tests for model + bitget config API routes (Phase 1).

Critical security invariant: no GET ever returns a raw secret value.
"""

from unittest.mock import patch

import pytest  # pyright: ignore[reportMissingImports]
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app
from aletheia.model_provider.router import clear_cache
from aletheia.config.schema import AletheiaConfig
from tests.model_http import mock_model_probe


def _seed_model(**fields):
    """Stored-state fixture for reads/probes, not evidence of API configuration."""
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig

    cfg = load_config()
    cfg.models.providers["openai_compatible"] = ProviderConfig(type="openai_compatible", **fields)
    save_config(cfg)


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


def _flatten_keys(value, prefix=""):
    if isinstance(value, dict):
        keys = set()
        for k, v in value.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                keys |= _flatten_keys(v, path)
            else:
                keys.add(path)
        return keys
    return {prefix} if prefix else set()


@pytest.mark.anyio
async def test_get_model_config(client):
    _seed_model()
    resp = await client.get("/api/model")
    assert resp.status_code == 200
    data = resp.json()
    assert "openai_compatible" in data["providers"]
    assert data["providers"]["openai_compatible"]["api_key"]["configured"] is False


@pytest.mark.anyio
async def test_guard_settings_reject_retired_promotion_controls(client):
    from aletheia.config.loader import load_config

    before = load_config().guard.model_dump()
    with patch("aletheia.files.jsonl.write_audit") as write_audit:
        for body in (
            {"min_matching_trades": 0}, {"min_matching_trades": -1},
            {"min_matching_trades": 3}, {"auto_promote_enabled": False},
            {"auto_promote_enabled": True, "enabled": not before["enabled"]},
        ):
            response = await client.post(
                "/api/settings/guard", json=body
            )
            assert response.status_code == 422

    assert load_config().guard.model_dump() == before
    write_audit.assert_not_called()


@pytest.mark.anyio
async def test_guard_settings_keep_current_fields_and_historical_decode(client):
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.guard.auto_promote_enabled = True
    cfg.guard.min_matching_trades = 7
    save_config(cfg)
    response = await client.post("/api/settings/guard", json={"max_score_penalty": 17})
    assert response.status_code == 200
    stored = load_config().guard
    assert stored.max_score_penalty == 17
    assert stored.auto_promote_enabled is True and stored.min_matching_trades == 7


def test_doctor_does_not_suggest_changing_retired_guard_flag(monkeypatch):
    from aletheia.config.loader import load_config, save_config
    from aletheia.diagnostics.doctor import Report, _probe_learning_retrieval
    from aletheia.knowledge import guards

    cfg = load_config()
    cfg.guard.auto_promote_enabled = True
    save_config(cfg)
    monkeypatch.setattr(guards, "load_trading_guards", lambda status: [object()] if status == "candidate" else [])
    report = Report()
    _probe_learning_retrieval(report)
    check = next(check for check in report.checks if check.name == "loop.guard_promotion")
    assert check.detail == "0 active guard(s), 1 pending shadow candidate(s)"
    assert check.fix_hint == ""


@pytest.mark.anyio
async def test_get_inference_broker_metrics_is_sanitized(client):
    response = await client.get("/api/model/broker")

    assert response.status_code == 200
    assert set(response.json()) == {"running", "providers"}
    assert "api_key" not in response.text
    assert "oauth" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "raw",
    [
        None,  # Core consumes normalized evidence even without supplier wire data.
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": "The probe asks for acknowledgement.",
                        "thinking_blocks": [{"type": "thinking"}],
                    },
                    "finish_reason": "length",
                }
            ]
        },
        {
            "id": "resp_123",
            "status": "completed",
            "output": [{"type": "reasoning", "summary": []}],
        },
        {
            "id": "msg_123",
            "content": [{"type": "thinking", "thinking": "acknowledged"}],
            "stop_reason": "max_tokens",
        },
    ],
)
async def test_ping_model_accepts_reasoning_only_provider_completion(client, monkeypatch, raw):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aletheia.model_provider.base import ModelResponse
    finished = []
    brokered_complete = AsyncMock(
        return_value=ModelResponse(
            content="",
            model="qwen35-122b-a10b",
            usage={"completion_tokens": 10},
            raw=raw,
            completion_confirmed=True,
            output_tokens=10,
        )
    )
    monkeypatch.setattr(
        "aletheia.model_provider.router.get_provider_async",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "aletheia.model_provider.broker.brokered_complete", brokered_complete,
    )
    monkeypatch.setattr(
        "aletheia.config.loader.load_config",
        lambda: SimpleNamespace(
            models=SimpleNamespace(default_provider="openai_compatible"),
        ),
    )
    monkeypatch.setattr(
        "aletheia.economics.inference_calls.record_inference_started",
        lambda _call: True,
    )
    monkeypatch.setattr(
        "aletheia.economics.inference_calls.record_inference_finished",
        lambda _call, **kwargs: finished.append(kwargs) or True,
    )

    response = await client.post("/api/model/ping")
    assert response.status_code == 200
    payload = response.json()

    assert payload["ok"] is True
    assert payload["model"] == "qwen35-122b-a10b"
    assert payload["response"] == ""
    assert payload["error"] is None
    request = brokered_complete.await_args.args[2]
    assert request.max_tokens == 10
    assert request.allow_model_fallback is False
    assert finished[0]["status"] == "success"


@pytest.mark.anyio
async def test_ping_model_records_error_when_empty_completion_has_no_evidence(client, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aletheia.model_provider.base import ModelResponse
    finished = []
    monkeypatch.setattr(
        "aletheia.model_provider.router.get_provider_async",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "aletheia.model_provider.broker.brokered_complete",
        AsyncMock(return_value=ModelResponse(
            content="", model="empty-model", raw={"status": "completed"},
            completion_confirmed=False,
        )),
    )
    monkeypatch.setattr(
        "aletheia.config.loader.load_config",
        lambda: SimpleNamespace(
            models=SimpleNamespace(default_provider="openai_compatible"),
        ),
    )
    monkeypatch.setattr(
        "aletheia.economics.inference_calls.record_inference_started",
        lambda _call: True,
    )
    monkeypatch.setattr(
        "aletheia.economics.inference_calls.record_inference_finished",
        lambda _call, **kwargs: finished.append(kwargs) or True,
    )

    response = await client.post("/api/model/ping")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"] == "empty response"
    assert payload["verified"] is False
    assert finished == [{"status": "error", "error_type": "EmptyModelResponse"}]


@pytest.mark.anyio
async def test_economics_shadow_settings_are_explicit_and_default_off(client):
    settings = (await client.get("/api/settings")).json()
    assert settings["economics"] == {
        "enabled": False,
        "budgets_shadow_enabled": False,
        "enforcement_mode": "warn_only",
        "harness_header_default_on": False,
        "prompt_guardrail_detection_enabled": False,
    }

    enabled = await client.post("/api/settings/economics", json={
        "enabled": True,
        "budgets_shadow_enabled": True,
    })
    assert enabled.status_code == 200
    assert enabled.json()["economics"]["budgets_shadow_enabled"] is True

    disabled = await client.post("/api/settings/economics", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["economics"]["enabled"] is False
    assert disabled.json()["economics"]["budgets_shadow_enabled"] is False


@pytest.mark.anyio
async def test_app_config_bind_host_is_managed_by_remote_control(client):
    settings = (await client.get("/api/settings")).json()
    assert settings["app"]["bind_host"] == "127.0.0.1"

    resp = await client.post("/api/settings/app", json={"bind_host": "0.0.0.0"})
    assert resp.status_code == 400
    assert "Remote Control" in resp.json()["detail"]

    settings = (await client.get("/api/settings")).json()
    assert settings["app"]["bind_host"] == "127.0.0.1"


@pytest.mark.anyio
async def test_app_config_bind_host_rejects_invalid(client):
    resp = await client.post("/api/settings/app", json={"bind_host": "not-a-host!!"})
    assert resp.status_code == 422

    resp2 = await client.post("/api/settings/app", json={"bind_host": ""})
    assert resp2.status_code == 422


@pytest.mark.anyio
async def test_set_model_provider(client, monkeypatch):
    mock_model_probe(monkeypatch)
    resp = await client.post("/api/settings/model-profiles", json={
        "profile_id": "openai_compatible", "type": "openai_compatible", "display_name": "HTTP",
        "base_url": "http://localhost:9000/v1",
        "model": "my-model",
    })
    assert resp.status_code == 201
    resp = await client.post("/api/settings/model-routing", json={"default_provider": "openai_compatible"})
    assert resp.status_code == 200
    assert resp.json()["default_provider"] == "openai_compatible"

    resp = await client.get("/api/model")
    assert resp.json()["providers"]["openai_compatible"]["base_url"] == "http://localhost:9000/v1"
    assert resp.json()["providers"]["openai_compatible"]["model"] == "my-model"


@pytest.mark.anyio
async def test_set_model_inference_limits_are_visible_and_validated(client, monkeypatch):
    mock_model_probe(monkeypatch)
    response = await client.post("/api/settings/model-profiles", json={
        "profile_id": "openai_compatible", "type": "openai_compatible", "display_name": "HTTP",
        "base_url": "http://model.test/v1", "model": "m1",
        "inference_max_concurrency": 3,
        "inference_requests_per_minute": 40,
        "inference_tpm": 90_000,
        "inference_queue_capacity": 20,
        "inference_reserved_high_priority_slots": 4,
        "inference_timeout_sec": 180,
    })
    assert response.status_code == 201

    provider = (await client.get("/api/model")).json()["providers"]["openai_compatible"]
    assert provider["inference_max_concurrency"] == 3
    assert provider["inference_requests_per_minute"] == 40
    assert provider["inference_tpm"] == 90_000
    assert provider["inference_queue_capacity"] == 20
    assert provider["inference_reserved_high_priority_slots"] == 4
    assert provider["inference_timeout_sec"] == 180

    invalid = await client.patch("/api/settings/model-profiles/openai_compatible", json={
        "inference_queue_capacity": 2,
        "inference_reserved_high_priority_slots": 3,
    })
    assert invalid.status_code == 422


@pytest.mark.anyio
async def test_set_model_api_key_is_write_only(client, monkeypatch):
    """POST accepts an api_key, but GET must never return the raw value."""
    mock_model_probe(monkeypatch)
    secret = "sk-super-secret-value-9999"
    resp = await client.post("/api/settings/model-profiles", json={
        "profile_id": "openai_compatible", "type": "openai_compatible", "display_name": "HTTP",
        "base_url": "http://model.test/v1", "model": "m1",
        "api_key": secret,
    })
    assert resp.status_code == 201
    body = resp.json()
    # Response reports configured + last4 only
    assert body["api_key"]["configured"] is True
    assert body["api_key"]["last4"] == "9999"
    assert secret not in resp.text

    # GET must not leak it either
    resp = await client.get("/api/model")
    assert secret not in resp.text
    assert resp.json()["providers"]["openai_compatible"]["api_key"]["configured"] is True


@pytest.mark.anyio
async def test_set_model_invalid_provider(client):
    resp = await client.post("/api/settings/model-profiles", json={
        "profile_id": "http", "type": "nonexistent", "display_name": "HTTP",
    })
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_bitget_credentials_write_only(client):
    secret_key = "bg-api-key-abcd"
    resp = await client.post("/api/settings/exchange-credentials", json={
        "profile": "demo",
        "api_key": secret_key,
        "api_secret": "bg-secret-xyz",
        "passphrase": "bg-pass-1234",
    })
    assert resp.status_code == 200
    assert secret_key not in resp.text

    resp = await client.get("/api/bitget/credentials-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["demo"]["api_key"]["configured"] is True
    assert data["demo"]["api_key"]["last4"] == "abcd"
    # raw secrets never present
    assert secret_key not in resp.text
    assert "bg-secret-xyz" not in resp.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/binance-testnet/credentials-status"),
        ("POST", "/api/settings/binance-testnet-credentials"),
        ("POST", "/api/binance-testnet/test-connection"),
    ],
)
async def test_binance_testnet_compatibility_routes_are_removed(client, method, path):
    response = await client.request(method, path, json={} if method == "POST" else None)
    assert response.status_code == 404


@pytest.mark.anyio
async def test_exchange_credentials_invalid_profile(client):
    resp = await client.post("/api/settings/exchange-credentials", json={
        "profile": "production", "api_key": "x",
    })
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_model_health_endpoint(client):
    resp = await client.get("/api/model/health")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert all("provider" in h and "available" in h for h in data)


@pytest.mark.anyio
async def test_model_health_unknown_profile_is_explicit(client):
    _seed_model(enabled=False)

    response = await client.get("/api/model/health")
    assert response.status_code == 200
    entry = next(item for item in response.json() if item["provider"] == "openai_compatible")
    assert entry["available"] is False
    assert entry["observed_at"] is None
    assert entry["age_seconds"] is None
    assert entry["freshness"] == "unknown"
    assert entry["cached"] is False
    assert entry["stale_after_seconds"] == 30.0


@pytest.mark.anyio
async def test_model_health_reports_probe_and_cache_state(client, monkeypatch):
    from datetime import datetime, timezone

    from aletheia.model_provider.base import ModelHealth
    import aletheia.model_provider.router as router

    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    async def fake_refresh():
        return [
            ModelHealth(
                provider="fake",
                available=True,
                model="model-a",
                observed_at=observed_at,
            ),
            ModelHealth(
                provider="failed",
                available=False,
                error="probe failed",
                observed_at=observed_at,
            ),
        ]

    monkeypatch.setattr(router, "_refresh_health_all", fake_refresh)
    router.invalidate_health_cache()

    first = await client.get("/api/model/health")
    second = await client.get("/api/model/health")
    assert first.json()[0]["freshness"] == "fresh"
    assert first.json()[0]["cached"] is False
    assert first.json()[0]["age_seconds"] is not None
    assert second.json()[0]["cached"] is True
    assert second.json()[0]["observed_at"] == observed_at
    assert second.json()[1]["available"] is False
    assert second.json()[1]["freshness"] == "fresh"


def test_model_health_openapi_exposes_observation_contract():
    from aletheia.server.app import app

    response = app.openapi()["paths"]["/api/model/health"]["get"]["responses"]["200"]
    schema = response["content"]["application/json"]["schema"]
    assert schema["type"] == "array"
    assert schema["items"] == {"$ref": "#/components/schemas/ModelHealthRecord"}
    properties = app.openapi()["components"]["schemas"]["ModelHealthRecord"]["properties"]
    assert {"observed_at", "age_seconds", "freshness", "cached", "stale_after_seconds"} <= set(properties)


def test_model_health_freshness_boundary_is_server_owned():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from aletheia.server.routes.model import _model_health_record

    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    stale = SimpleNamespace(
        provider="fake",
        available=True,
        observed_at=(now - timedelta(seconds=30.001)).isoformat(),
        stale_after_seconds=30.0,
    )
    fresh = SimpleNamespace(
        provider="fake",
        available=True,
        observed_at=(now - timedelta(seconds=30)).isoformat(),
        stale_after_seconds=30.0,
    )

    assert _model_health_record(stale, cached=True, now=now).freshness == "stale"
    assert _model_health_record(fresh, cached=True, now=now).freshness == "fresh"
    assert _model_health_record(SimpleNamespace(provider="fake"), cached=False, now=now).freshness == "unknown"


@pytest.mark.anyio
async def test_model_health_separates_reachability_and_verification(client, monkeypatch):
    from aletheia.model_provider.base import ModelHealth
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider

    async def healthy(self):
        return ModelHealth(provider="openai_compatible", available=True, model=self.model)

    monkeypatch.setattr(OpenAICompatibleProvider, "healthcheck", healthy)
    _seed_model(base_url="http://model.test/v1", model="model-a", enabled=True)
    assigned = await client.post("/api/settings/model-routing", json={"default_provider": "openai_compatible"})
    assert assigned.status_code == 200

    response = await client.get("/api/model/health")
    assert response.status_code == 200
    entry = next(item for item in response.json() if item["provider"] == "openai_compatible")
    assert entry["configured"] is True
    assert entry["enabled"] is True
    assert entry["reachable"] is True
    assert entry["verified"] is None
    assert entry["state"] == "reachable"
    assert entry["reason_code"] == "reachable"
    assert entry["routing_roles"] == ["default"]


@pytest.mark.anyio
async def test_model_profile_verification_persists_and_config_change_invalidates(
    client, monkeypatch
):
    from aletheia.model_provider.base import ModelHealth, ModelResponse
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider

    async def healthy(self):
        return ModelHealth(provider="openai_compatible", available=True, model=self.model)

    async def inference(*args, **kwargs):
        return ModelResponse(content="OK", model="model-a", usage={})

    monkeypatch.setattr(OpenAICompatibleProvider, "healthcheck", healthy)
    monkeypatch.setattr(
        "aletheia.model_provider.broker.brokered_complete", inference
    )
    monkeypatch.setattr(
        "aletheia.economics.inference_calls.record_inference_started",
        lambda _call: True,
    )
    monkeypatch.setattr(
        "aletheia.economics.inference_calls.record_inference_finished",
        lambda _call, **_kwargs: True,
    )

    created = await client.post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "openai_compatible",
            "display_name": "Primary API",
            "type": "openai_compatible",
            "base_url": "http://model.test/v1",
            "model": "model-a",
            "enabled": True,
        },
    )
    assert created.status_code == 201, created.text
    verification = created.json()["verification"]
    assert verification["verified"] is True
    assert verification["verified_at"]
    assert verification["verification_identity"]

    healthy_response = await client.get("/api/model/health")
    entry = next(
        item for item in healthy_response.json() if item["provider"] == "openai_compatible"
    )
    assert entry["reachable"] is True
    assert entry["verified"] is True
    assert entry["state"] == "verified"

    # Stored configuration drift is not a verified Settings update. Preserve
    # the old verification record to prove the read side rejects its identity.
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.models.providers["openai_compatible"].base_url = "http://other.test/v1"
    save_config(cfg)
    clear_cache()
    after_change = await client.get("/api/model/health")
    changed_entry = next(
        item for item in after_change.json() if item["provider"] == "openai_compatible"
    )
    assert changed_entry["reachable"] is True
    assert changed_entry["verified"] is None
    assert changed_entry["verification_invalidated"] is True
    assert changed_entry["state"] == "reachable"


@pytest.mark.anyio
async def test_model_health_lists_unconfigured_registry_rows(client):
    response = await client.get("/api/model/health")
    assert response.status_code == 200
    rows = {item["provider"]: item for item in response.json()}
    assert rows["openai_compatible"]["configured"] is False
    assert rows["openai_compatible"]["state"] == "not_configured"
    assert rows["openai_compatible"]["reason_code"] == "missing_configuration"
    assert rows["openai_compatible"]["reachable"] is None
    assert rows["openai_compatible"]["verified"] is None


@pytest.mark.anyio
async def test_universe_settings_are_visible_and_persisted(client):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json()["universe"]["enabled"] is True

    resp = await client.post(
        "/api/settings/universe",
        json={
            "enabled": False,
            "max_candidates": 20,
            "entry_ttl_sec": 600,
            "candidate_max_continuous_age_sec": 1800,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    from pydantic import TypeAdapter
    from aletheia.server.routes.settings import UniverseConfigUpdateResponse
    adapter = TypeAdapter(UniverseConfigUpdateResponse)
    parsed = adapter.validate_json(resp.content, strict=True)
    assert adapter.dump_python(parsed, mode="json", exclude_unset=True) == body
    assert body["universe"]["enabled"] is False
    assert body["universe"]["max_candidates"] == 20
    assert body["universe"]["entry_ttl_sec"] == 600
    assert body["universe"]["candidate_max_continuous_age_sec"] == 1800

    resp = await client.get("/api/settings")
    assert resp.json()["universe"]["enabled"] is False
    assert resp.json()["universe"]["max_candidates"] == 20









@pytest.mark.anyio
async def test_universe_policy_inputs_rotate_identity_without_admission(client):
    from aletheia.config.loader import load_config
    from aletheia.universe.candidate_selection_authority import candidate_selection_authority_block_reason
    before = load_config().universe.candidate_selection_policy_epoch
    response = await client.post("/api/settings/universe", json={"max_candidates": 17})
    assert response.status_code == 200
    current = load_config()
    assert current.universe.max_candidates == 17
    assert current.universe.candidate_selection_policy_epoch != before
    assert candidate_selection_authority_block_reason(current) is None
    for action in ("admit", "rollback"):
        assert (await client.post(f"/api/settings/universe/candidate-selection/{action}", json={})).status_code == 404


@pytest.mark.anyio
async def test_candidate_identity_config_write_holds_publication_permit(
    client, monkeypatch,
):
    from contextlib import contextmanager

    from aletheia.server.routes import settings
    from aletheia.storage import repositories as repo

    inside_publication_permit = False
    real_transaction = repo.transaction
    real_mutate_config = settings.mutate_config

    @contextmanager
    def tracked_transaction(*, immediate=False, savepoint=False):
        nonlocal inside_publication_permit
        with real_transaction(immediate=immediate, savepoint=savepoint):
            inside_publication_permit = True
            try:
                yield
            finally:
                inside_publication_permit = False

    def checked_mutation(mutator):
        assert inside_publication_permit
        return real_mutate_config(mutator)

    monkeypatch.setattr(repo, "transaction", tracked_transaction)
    monkeypatch.setattr(settings, "mutate_config", checked_mutation)
    response = await client.post("/api/settings/fast-trigger", json={"mode": "off"})

    assert response.status_code == 200














def test_candidate_selection_transition_journal_recovers_after_process_exit():
    from aletheia.config.loader import get_data_dir, load_config, save_config
    from aletheia.config.schema import UniverseConfig
    from aletheia.storage.models import AuditEvent
    from aletheia.universe.candidate_eligibility import canonical_fingerprint
    from aletheia.universe.candidate_selection_authority import (
        candidate_selection_policy_identity,
        recover_candidate_selection_authority_transition,
        stage_candidate_selection_authority_transition,
    )

    cfg = load_config()
    epoch = canonical_fingerprint(candidate_selection_policy_identity(cfg))
    target = UniverseConfig.model_validate({
        **cfg.universe.model_dump(mode="json"),
        "candidate_selection_policy": "candidate_selection_utility_v1",
        "candidate_selection_policy_epoch": epoch,
    })
    legacy = UniverseConfig.model_validate({
        **target.model_dump(mode="json"),
        "candidate_selection_policy": "legacy_total_score_v1",
        "candidate_selection_policy_epoch": None,
    })
    cfg.universe = target
    save_config(cfg)
    event = AuditEvent(
        actor="human",
        source="dashboard",
        event_type="candidate_selection_authority_rolled_back",
        entity_type="universe_config",
        entity_id=epoch,
    )
    stage_candidate_selection_authority_transition(
        audit_event_id=event.id,
        before=target,
        after=legacy,
    )
    cfg.universe = legacy
    save_config(cfg)

    assert recover_candidate_selection_authority_transition() is True
    restored = load_config().universe
    assert restored.candidate_selection_policy == "candidate_selection_utility_v1"
    assert restored.candidate_selection_policy_epoch == epoch
    from aletheia.universe.candidate_selection_authority import candidate_selection_authority_block_reason
    assert candidate_selection_authority_block_reason(load_config()) is None
    assert not (get_data_dir() / "candidate_selection_authority_transition.json").exists()


def test_candidate_authority_audit_jsonl_uses_committed_outbox(monkeypatch):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import AuditEvent

    event = AuditEvent(
        actor="human",
        source="dashboard",
        event_type="candidate_selection_authority_admitted",
        entity_type="universe_config",
        entity_id="candidate-selection-utility:outbox",
    )
    calls = []
    monkeypatch.setattr(
        "aletheia.files.jsonl.write_audits_deduplicated",
        lambda events, **_kwargs: calls.append(events) or set(),
    )
    with repo.transaction(immediate=True):
        repo.insert_audit_event_outbox(event)
        assert calls == []

    assert repo.reconcile_audit_event_outbox((event.event_type,)) is False
    assert calls and calls[0][0]["id"] == event.id
    row = repo.get_db().execute(
        "SELECT jsonl_mirror_status FROM audit_events WHERE id=?",
        (event.id,),
    ).fetchone()
    assert row["jsonl_mirror_status"] == "failed"


@pytest.mark.anyio
async def test_fast_trigger_settings_are_visible_bounded_and_persisted(client):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json()["fast_trigger"]["mode"] == "shadow"

    resp = await client.post(
        "/api/settings/fast-trigger",
        json={
            "enabled": False,
            "mode": "off",
            "max_setups": 7,
            "authorization_ttl_minutes": 45,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["runtime_status"]["reason"] == "fast_trigger_disabled"

    resp = await client.get("/api/settings")
    assert resp.json()["fast_trigger"]["max_setups"] == 7
    assert resp.json()["fast_trigger"]["authorization_ttl_minutes"] == 45

    invalid = await client.post(
        "/api/settings/fast-trigger", json={"max_setups": 11}
    )
    assert invalid.status_code == 422


@pytest.mark.anyio
async def test_learning_lifecycle_defaults_enable_only_the_measured_paper_path(client):
    response = await client.get("/api/settings")
    assert response.status_code == 200
    learning = response.json()["learning"]
    assert learning == {
        "contribution_evaluation_enabled": True,
        "lifecycle_automation_enabled": True,
        "paper_influence_enabled": True,
        "paper_influence_paths": ["universe_family_calibration"],
        "auto_refreeze": True,
        "evaluation_interval_minutes": 60,
        "paper_active_allocation_pct": 0.80,
    }


@pytest.mark.anyio
async def test_learning_settings_persist_and_cap_active_paper_allocation(client):
    response = await client.post("/api/settings/learning", json={
        "lifecycle_automation_enabled": True,
        "paper_influence_enabled": True,
        "paper_influence_paths": [" universe_family_calibration ", "universe_family_calibration"],
        "paper_active_allocation_pct": 0.80,
    })
    assert response.status_code == 200
    learning = response.json()["learning"]
    assert learning["paper_influence_paths"] == ["universe_family_calibration"]
    assert learning["paper_active_allocation_pct"] == 0.80

    rejected = await client.post("/api/settings/learning", json={"paper_active_allocation_pct": 0.91})
    assert rejected.status_code == 400

    unsupported = await client.post(
        "/api/settings/learning", json={"paper_influence_paths": ["advisory_skill"]},
    )
    assert unsupported.status_code == 400
    measurement_unwired = await client.post(
        "/api/settings/learning",
        json={"paper_influence_paths": ["ranker_learned_guard"]},
    )
    assert measurement_unwired.status_code == 400
    assert "not fully supported" in measurement_unwired.json()["detail"]
    unknown = await client.post(
        "/api/settings/learning", json={"paper_influence_paths": ["made_up_path"]},
    )
    assert unknown.status_code == 400

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.learning.paper_influence_enabled = False
    cfg.learning.paper_influence_paths = ["ranker_learned_guard"]
    save_config(cfg)
    toggle_only = await client.post(
        "/api/settings/learning", json={"paper_influence_enabled": True},
    )
    assert toggle_only.status_code == 400


@pytest.mark.anyio
async def test_learning_settings_expose_only_measurable_paper_path_options(client):
    response = await client.get("/api/settings")
    assert response.status_code == 200
    options = response.json()["learning_path_options"]
    by_id = {option["path_id"]: option for option in options}
    assert by_id["universe_family_calibration"]["supported"] is True
    assert by_id["ranker_learned_guard"] == {
        "path_id": "ranker_learned_guard",
        "label": "learned trading guards",
        "supported": False,
        "status": "measurement_unwired",
    }
    assert by_id["operator_learning_bundle"] == {
        "path_id": "operator_learning_bundle",
        "label": "memo/reasoning/calibration/reflection/lessons",
        "supported": False,
        "status": "measurement_unwired",
    }
    assert by_id["advisory_skill"] == {
        "path_id": "advisory_skill",
        "label": "proven hypothesis skill",
        "supported": False,
        "status": "consumer_unwired",
    }


@pytest.mark.anyio
async def test_settings_response_covers_sanitized_config_schema(client):
    """Every non-secret config.yaml key should be visible on Settings."""
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    actual_keys = _flatten_keys(resp.json())
    expected_keys = _flatten_keys(AletheiaConfig().model_dump(mode="json"))

    secret_suffixes = (
        ".api_key_secret_ref",
        ".oauth_token_ref",
        ".oauth_refresh_ref",
    )
    hidden_keys = {"tunnel.credentials_path", "webhook.secret"}
    expected_visible = {
        key for key in expected_keys
        if not (key.startswith("models.providers.") and key.endswith(secret_suffixes))
        and key not in hidden_keys
    }

    missing = sorted(expected_visible - actual_keys)
    assert not missing

    raw = resp.text.lower()
    assert "api_key_secret_ref" not in raw
    assert "oauth_token_ref" not in raw
    assert "oauth_refresh_ref" not in raw


@pytest.mark.anyio
async def test_get_settings_masks_inline_secret_fields(client):
    """Regression #1: GET /api/settings must not return coinalyze_api_key or proxy credentials raw."""
    from aletheia.config.loader import save_config
    cfg = AletheiaConfig()
    cfg.market_data.coinalyze_api_key = "COINALYZESECRET12345"
    cfg.network.proxy_url = "http://myuser:mypassword@127.0.0.1:7890"
    save_config(cfg)

    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    raw = resp.text
    assert "COINALYZESECRET12345" not in raw
    assert "mypassword" not in raw and "myuser" not in raw

    data = resp.json()
    # Key stays present (UI reads the section) but the value is masked.
    assert "coinalyze_api_key" in data["market_data"]
    assert data["market_data"]["coinalyze_api_key"] != "COINALYZESECRET12345"
    # Proxy host/port kept for diagnostics; only the user:pass is stripped.
    assert "127.0.0.1:7890" in data["network"]["proxy_url"]


@pytest.mark.anyio
async def test_webhook_secret_presence_and_explicit_clear_are_truthful(client):
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.webhook.secret = "WEBHOOKSECRET123"
    cfg.webhook.url = "https://example.test/hook"
    save_config(cfg)

    configured = await client.get("/api/settings")
    assert configured.status_code == 200
    assert configured.json()["webhook"]["secret_configured"] is True
    assert "WEBHOOKSECRET123" not in configured.text

    cleared = await client.post("/api/settings/webhook", json={"secret": ""})
    assert cleared.status_code == 200
    assert "WEBHOOKSECRET123" not in cleared.text
    assert "secret" not in cleared.json()["updated"]
    assert cleared.json()["updated"]["secret_configured"] is False
    assert load_config().webhook.secret == ""

    unconfigured = await client.get("/api/settings")
    assert unconfigured.json()["webhook"]["secret_configured"] is False


def test_settings_openapi_exposes_redacted_typed_response_contract():
    response_schema = app.openapi()["paths"]["/api/settings"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema == {"$ref": "#/components/schemas/SettingsResponse"}

    schemas = app.openapi()["components"]["schemas"]
    settings = schemas["SettingsResponse"]["properties"]
    assert settings["models"]["$ref"] == "#/components/schemas/SettingsModelsResponse"
    assert settings["network"]["$ref"] == "#/components/schemas/NetworkConfig"
    assert settings["fast_trigger"]["$ref"] == "#/components/schemas/FastTriggerConfig"
    assert "secret" not in schemas["SettingsWebhookResponse"]["properties"]
    assert schemas["SettingsWebhookResponse"]["properties"]["last_delivery"]
    assert "credentials_path" not in schemas["SettingsTunnelResponse"]["properties"]


@pytest.mark.anyio
async def test_get_settings_projects_sanitized_webhook_delivery(client, monkeypatch):
    from aletheia import notifications

    delivery = {
            "status": "failed",
            "attempted_at": "2026-09-02T08:00:00+00:00",
            "receiver_host": "receiver.example",
            "event_type": "trade_fill",
            "entity_type": "position",
            "entity_id": "position-1",
            "http_status": 500,
            "error_class": "HTTPError",
            "latency_ms": 12,
    }
    monkeypatch.setattr(notifications, "webhook_delivery_status", lambda: delivery)

    response = await client.get("/api/settings")

    assert response.status_code == 200
    assert response.json()["webhook"]["last_delivery"] == delivery


@pytest.mark.anyio
async def test_settings_post_does_not_echo_secrets(client):
    """Regression #2/#4: webhook/network POST responses must not echo the submitted secret/creds."""
    with patch("aletheia.files.jsonl.write_audit") as write_audit:
        r = await client.post("/api/settings/webhook", json={"secret": "WEBHOOKSECRET999"})
    assert r.status_code == 200
    assert "WEBHOOKSECRET999" not in r.text
    assert "secret" not in r.json()["updated"]
    assert r.json()["updated"]["secret_configured"] is True
    event = write_audit.call_args.args[0]
    assert "secret" not in event["before"]
    assert "secret" not in event["after"]
    assert event["after"]["secret_configured"] is True

    r2 = await client.post("/api/settings/network", json={"proxy_url": "http://uu:ppw@10.0.0.9:1080"})
    assert r2.status_code == 200
    assert "ppw" not in r2.text and "uu:ppw" not in r2.text
    # host still surfaced
    assert "10.0.0.9:1080" in r2.text


@pytest.mark.anyio
async def test_risk_config_saves_per_symbol_entry_count(client):
    """The Settings risk form must be able to round-trip its shared symbol cap.

    The cap is a COUNT of full-size entries, not a currency amount — the engine
    derives the USD ceiling from sizing's formula so it cannot go stale.
    """
    response = await client.post(
        "/api/settings/risk",
        json={"max_per_symbol_entries": 5},
    )

    assert response.status_code == 200, response.text
    assert response.json()["risk"]["max_per_symbol_entries"] == 5

    settings = (await client.get("/api/settings")).json()
    assert settings["risk"]["max_per_symbol_entries"] == 5


@pytest.mark.anyio
async def test_demo_risk_config_has_no_application_level_upper_caps(client):
    """Paper limits may exceed the old UI ceilings while retaining positive minima."""
    uncapped = {
        "max_demo_position_margin_usdt": 250_000.0,
        "max_demo_single_trade_risk_pct": 50.0,
        "max_open_positions_demo": 500,
        "max_account_leverage_demo": 25.0,
    }

    response = await client.post("/api/settings/risk", json=uncapped)

    assert response.status_code == 200, response.text
    assert response.json()["updated"] == uncapped

    invalid = await client.post(
        "/api/settings/risk",
        json={"max_open_positions_demo": 0},
    )
    assert invalid.status_code == 400


@pytest.mark.anyio
async def test_legacy_risk_update_rejects_nonfinite_without_mutating_policy(client):
    before = (await client.get("/api/settings/risk-policies")).json()["demo"]

    response = await client.post(
        "/api/settings/risk",
        content='{"max_account_leverage_demo":NaN}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    after = (await client.get("/api/settings/risk-policies")).json()["demo"]
    assert after == before


@pytest.mark.anyio
async def test_risk_policy_copy_is_revision_guarded_and_preserves_live_identity(client):
    policies = (await client.get("/api/settings/risk-policies")).json()
    demo = policies["demo"]
    live = policies["live"]
    assert demo["capital"]["base_source"] == "account_equity"
    assert live["capital"]["base_source"] == "account_equity"

    # Default Demo has leverage (125x futures, 200 positions, 20% per-trade risk) that
    # exceeds Live ceilings. First bring Demo within Live bounds so the copy succeeds.
    safe_demo = {k: v for k, v in demo.items() if k != "policy_hash"}
    safe_demo["leverage"] = live["leverage"]
    safe_demo["positions"] = live["positions"]
    safe_demo["loss"] = live["loss"]
    safe_demo["capital"] = live["capital"]
    safe_demo["margin"] = live["margin"]
    safe_demo["correlation"] = live["correlation"]
    put = await client.put(
        "/api/settings/risk-policies/demo",
        json={
            "policy": safe_demo,
            "expected_revision": demo["revision"],
            "confirm_target_replacement": True,
            "reason": "set safe demo for copy test",
        },
    )
    assert put.status_code == 200, put.text

    policies = (await client.get("/api/settings/risk-policies")).json()
    demo = policies["demo"]
    live = policies["live"]

    copied = await client.post(
        "/api/settings/risk-policies/copy",
        json={
            "source": "demo",
            "target": "live",
            "source_revision": demo["revision"],
            "target_revision": live["revision"],
            "confirm_target_replacement": True,
            "reason": "test copy",
        },
    )
    assert copied.status_code == 200, copied.text
    body = copied.json()
    assert body["live"]["revision"] == live["revision"] + 1
    assert body["live"]["policy_id"] == live["policy_id"]
    assert body["live"]["capital"] == demo["capital"]
    assert body["live"]["eligibility"] == live["eligibility"]

    stale = await client.post(
        "/api/settings/risk-policies/copy",
        json={
            "source": "demo",
            "target": "live",
            "source_revision": demo["revision"],
            "target_revision": live["revision"],
            "confirm_target_replacement": True,
            "reason": "stale retry",
        },
    )
    assert stale.status_code == 409


@pytest.mark.anyio
async def test_live_policy_write_enforces_safety_ceilings(client):
    """PUT /risk-policies/live rejects values above each Live ceiling (Class A fix)."""
    policies = (await client.get("/api/settings/risk-policies")).json()
    live = policies["live"]
    rev = live["revision"]

    over_limit_cases = [
        # (nested path, over-limit value, sub-key to set)
        ("leverage",    "futures_cap",                    21),
        ("leverage",    "major_cap",                      11),
        ("leverage",    "mid_cap",                        6),
        ("leverage",    "alt_cap",                        4),
        ("leverage",    "account_cap",                    5.1),
        ("positions",   "max_open_positions",             51),
        ("loss",        "max_daily_loss_pct",             20.1),
        ("loss",        "max_single_trade_risk_pct",      10.1),
        ("capital",     "max_total_exposure_pct",         50.1),
        ("capital",     "max_position_margin_usdt",       100_001.0),
        ("capital",     "base_usdt",                      100_001.0),
        ("capital",     "max_per_symbol_entries",         21),
        ("margin",      "min_entry_liquidation_buffer_pct", 20.1),
        ("margin",      "runtime_liquidation_buffer_pct",   20.1),
        ("margin",      "warning_ratio",                  0.96),
        ("margin",      "block_ratio",                    1.0),
        ("correlation", "max_sector_concentration_pct",   100.1),
        ("correlation", "max_portfolio_beta",             10.1),
        ("correlation", "min_positions_for_concentration", 201),
    ]

    for group, field, bad_value in over_limit_cases:
        policy = {k: v for k, v in live.items() if k != "policy_hash"}
        policy[group] = {**live[group], field: bad_value}
        resp = await client.put(
            "/api/settings/risk-policies/live",
            json={
                "policy": policy,
                "expected_revision": rev,
                "confirm_target_replacement": True,
                "reason": f"over-limit test: {group}.{field}={bad_value}",
            },
        )
        assert resp.status_code == 400, (
            f"{group}.{field}={bad_value} should be rejected, got {resp.status_code}: {resp.text}"
        )

    # Config must be unchanged after all rejected writes.
    after = (await client.get("/api/settings/risk-policies")).json()
    assert after["live"]["revision"] == rev

    policy = {k: v for k, v in live.items() if k != "policy_hash"}
    policy["eligibility"] = None
    missing_gate = await client.put(
        "/api/settings/risk-policies/live",
        json={
            "policy": policy,
            "expected_revision": rev,
            "confirm_target_replacement": True,
            "reason": "missing live eligibility gate",
        },
    )
    assert missing_gate.status_code == 400
    unchanged = (await client.get("/api/settings/risk-policies")).json()
    assert unchanged["live"]["revision"] == rev


@pytest.mark.anyio
async def test_live_policy_write_accepts_exact_boundary_values(client):
    """Exact ceiling values are valid and must return 200."""
    policies = (await client.get("/api/settings/risk-policies")).json()
    live = policies["live"]
    rev = live["revision"]

    at_ceiling = {k: v for k, v in live.items() if k != "policy_hash"}
    at_ceiling["leverage"] = {
        "futures_cap": 20, "major_cap": 10, "mid_cap": 5, "alt_cap": 3, "account_cap": 5.0,
    }
    at_ceiling["positions"] = {"max_open_positions": 50, "allow_short": True}
    at_ceiling["loss"] = {"max_daily_loss_pct": 20.0, "max_single_trade_risk_pct": 10.0}
    at_ceiling["capital"] = {
        **live["capital"],
        "max_total_exposure_pct": 50.0,
        "max_position_margin_usdt": 100_000.0,
        "base_usdt": 100_000.0,
        "max_per_symbol_entries": 20,
    }
    at_ceiling["margin"] = {
        "warning_ratio": 0.95,
        "block_ratio": 0.99,
        "min_entry_liquidation_buffer_pct": 20.0,
        "runtime_liquidation_buffer_pct": 20.0,
    }
    at_ceiling["correlation"] = {
        **live["correlation"],
        "max_sector_concentration_pct": 100.0,
        "max_portfolio_beta": 10.0,
        "min_positions_for_concentration": 200,
    }
    resp = await client.put(
        "/api/settings/risk-policies/live",
        json={
            "policy": at_ceiling,
            "expected_revision": rev,
            "confirm_target_replacement": True,
            "reason": "at-ceiling acceptance test",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["live"]["revision"] == rev + 1


@pytest.mark.anyio
async def test_copy_demo_to_live_rejects_when_demo_exceeds_live_ceilings(client):
    """Default Demo has 125x leverage; copy Demo→Live must return 400."""
    policies = (await client.get("/api/settings/risk-policies")).json()
    demo = policies["demo"]
    live = policies["live"]

    # Default Demo has leverage/positions/risk far above Live bounds.
    resp = await client.post(
        "/api/settings/risk-policies/copy",
        json={
            "source": "demo",
            "target": "live",
            "source_revision": demo["revision"],
            "target_revision": live["revision"],
            "confirm_target_replacement": True,
            "reason": "should fail",
        },
    )
    assert resp.status_code == 400, resp.text

    # Live must be unchanged.
    after = (await client.get("/api/settings/risk-policies")).json()
    assert after["live"]["revision"] == live["revision"]


@pytest.mark.anyio
async def test_demo_policy_write_allows_values_above_live_ceilings(client):
    """Demo has no application-level upper caps; high values must be accepted."""
    policies = (await client.get("/api/settings/risk-policies")).json()
    demo = policies["demo"]
    rev = demo["revision"]

    high_demo = {k: v for k, v in demo.items() if k != "policy_hash"}
    high_demo["leverage"] = {
        "futures_cap": 125, "major_cap": 20, "mid_cap": 10, "alt_cap": 5, "account_cap": 10.0,
    }
    high_demo["positions"] = {"max_open_positions": 200, "allow_short": True}
    high_demo["loss"] = {"max_daily_loss_pct": 20.0, "max_single_trade_risk_pct": 20.0}
    high_demo["eligibility"] = {
        **demo["eligibility"],
        "family_min_samples": 7,
        "min_regime_confidence_to_enter": 0.75,
    }
    resp = await client.put(
        "/api/settings/risk-policies/demo",
        json={
            "policy": high_demo,
            "expected_revision": rev,
            "confirm_target_replacement": True,
            "reason": "demo high-value test",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["demo"]["eligibility"] == high_demo["eligibility"]


@pytest.mark.anyio
async def test_min_positions_for_concentration_is_validated_for_live(client):
    """correlation.min_positions_for_concentration: out-of-range → 400 for Live."""
    policies = (await client.get("/api/settings/risk-policies")).json()
    live = policies["live"]
    rev = live["revision"]

    for bad_val in (0, 201):
        policy = {k: v for k, v in live.items() if k != "policy_hash"}
        policy["correlation"] = {**live["correlation"], "min_positions_for_concentration": bad_val}
        resp = await client.put(
            "/api/settings/risk-policies/live",
            json={
                "policy": policy,
                "expected_revision": rev,
                "confirm_target_replacement": True,
                "reason": f"bad min_positions={bad_val}",
            },
        )
        assert resp.status_code == 400, f"Expected 400 for min_positions={bad_val}"

    # In-range must pass.
    policy = {k: v for k, v in live.items() if k != "policy_hash"}
    policy["correlation"] = {**live["correlation"], "min_positions_for_concentration": 10}
    resp = await client.put(
        "/api/settings/risk-policies/live",
        json={
            "policy": policy,
            "expected_revision": rev,
            "confirm_target_replacement": True,
            "reason": "valid min_positions=10",
        },
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.anyio
async def test_risk_policy_reset_defaults_is_mode_scoped_and_revision_guarded(client):
    policies = (await client.get("/api/settings/risk-policies")).json()
    demo = policies["demo"]
    live = policies["live"]

    reset = await client.post(
        "/api/settings/risk-policies/demo/reset-defaults",
        json={
            "expected_revision": demo["revision"],
            "confirm_target_replacement": True,
            "reason": "test reset",
        },
    )

    assert reset.status_code == 200, reset.text
    body = reset.json()
    assert body["demo"]["revision"] == demo["revision"] + 1
    assert body["demo"]["policy_id"] == demo["policy_id"]
    assert body["demo"]["capital"]["max_position_margin_usdt"] == 100_000.0
    assert body["live"] == live

    stale = await client.post(
        "/api/settings/risk-policies/demo/reset-defaults",
        json={
            "expected_revision": demo["revision"],
            "confirm_target_replacement": True,
            "reason": "stale reset",
        },
    )
    assert stale.status_code == 409


@pytest.mark.anyio
async def test_remote_control_interfaces_are_queryable(client):
    response = await client.get("/api/settings/remote-control/interfaces")
    assert response.status_code == 200
    assert isinstance(response.json()["interfaces"], list)


@pytest.mark.anyio
async def test_remote_control_token_is_optional_and_disabled_by_default(client):
    """Remote Control can be reachable over EasyTier without forcing a token in MVP mode."""
    no_adapter = await client.post("/api/settings/remote-control", json={"enabled": True})
    assert no_adapter.status_code == 400

    enabled = await client.post(
        "/api/settings/remote-control",
        json={"enabled": True, "bind_hosts": ["10.42.0.8"]},
    )
    assert enabled.status_code == 200
    assert enabled.json()["remote_control"] == {
        "enabled": True,
        "bind_hosts": ["10.42.0.8"],
        "require_token": False,
    }
    assert (await client.get("/api/settings")).status_code == 200

    missing_token = await client.post(
        "/api/settings/remote-control",
        json={"require_token": True},
    )
    assert missing_token.status_code == 400
    assert (await client.get("/api/settings")).status_code == 200

    token = "REMOTEOWNER42"
    protected = await client.post(
        "/api/settings/remote-control",
        json={"require_token": True, "api_token": token},
    )
    assert protected.status_code == 200
    assert token not in protected.text
    assert protected.json()["remote_control"] == {
        "enabled": True,
        "bind_hosts": ["10.42.0.8"],
        "require_token": True,
    }

    blocked = await client.get("/api/settings")
    assert blocked.status_code == 401
    allowed = await client.get("/api/settings", headers={"X-Aletheia-Token": token})
    assert allowed.status_code == 200
    assert token not in allowed.text

    disabled = await client.post(
        "/api/settings/remote-control",
        json={"enabled": False, "require_token": False},
        headers={"X-Aletheia-Token": token},
    )
    assert disabled.status_code == 200
    assert disabled.json()["remote_control"] == {
        "enabled": False,
        "bind_hosts": ["10.42.0.8"],
        "require_token": False,
    }
    assert (await client.get("/api/settings")).status_code == 200


@pytest.mark.anyio
async def test_tunnel_guard_protects_risk_config_mutation(client):
    """Regression #137: with the tunnel enabled, POST /settings/risk requires the token."""
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.secrets import set_secret
    cfg = load_config()
    cfg.tunnel.enabled = True
    save_config(cfg)
    set_secret("tunnel_api_token", "TUNNELTOKEN123")

    # No token -> blocked (401): risk config cannot be mutated over a public tunnel.
    r = await client.post("/api/settings/risk", json={"max_leverage_major_live": 3})
    assert r.status_code == 401

    # Correct token -> the auth guard passes (route then handles it, not a 401).
    r2 = await client.post(
        "/api/settings/risk",
        json={"max_leverage_major_live": 3},
        headers={"X-Aletheia-Token": "TUNNELTOKEN123"},
    )
    assert r2.status_code != 401


@pytest.mark.anyio
async def test_tunnel_guard_protects_operator_command_surfaces(client):
    """Regression #42: operator command/pause/resume/wake/trigger and capital
    events must be protected by the tunnel token when the tunnel is enabled."""
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.secrets import set_secret
    cfg = load_config()
    cfg.tunnel.enabled = True
    save_config(cfg)
    set_secret("tunnel_api_token", "TUNNELTOKEN42")

    protected_posts = [
        "/api/operator/command",
        "/api/operator/pause",
        "/api/operator/resume",
        "/api/operator/wake",
        "/api/capital/events",
    ]
    for path in protected_posts:
        r = await client.post(path, json={})
        assert r.status_code == 401, f"{path} should be blocked without token, got {r.status_code}"

    # With correct token, the auth guard passes (downstream may 422/400/403, but not 401).
    for path in protected_posts:
        r = await client.post(path, json={}, headers={"X-Aletheia-Token": "TUNNELTOKEN42"})
        assert r.status_code != 401, f"{path} should pass auth guard with token, got {r.status_code}"


@pytest.mark.anyio
async def test_tunnel_guard_protects_learning_mutations_before_side_effects(
    client,
    monkeypatch,
):
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.secrets import set_secret
    import aletheia.files.jsonl as jsonl
    import aletheia.server.routes.settings as settings_routes

    cfg = load_config()
    cfg.tunnel.enabled = True
    cfg.agent.autonomous_learning = False
    save_config(cfg)

    saved = []
    audited = []
    real_save_config = settings_routes.save_config
    real_write_audit = jsonl.write_audit

    def tracked_save_config(updated):
        saved.append(updated.agent.autonomous_learning)
        return real_save_config(updated)

    def tracked_write_audit(event):
        audited.append(event)
        return real_write_audit(event)

    monkeypatch.setattr(settings_routes, "save_config", tracked_save_config)
    monkeypatch.setattr(jsonl, "write_audit", tracked_write_audit)

    config = await client.post("/api/settings/learning", json={})
    admission = await client.post(
        "/api/settings/learning/experiments/example/admit-trial",
        json={},
    )
    master_toggle_without_secret = await client.post(
        "/api/settings/autonomous-learning",
        json={"enabled": True},
    )
    assert config.status_code == 401
    assert admission.status_code == 401
    assert master_toggle_without_secret.status_code == 401
    assert saved == []
    assert audited == []
    assert load_config().agent.autonomous_learning is False

    set_secret("tunnel_api_token", "LEARNINGOWNER42")
    master_toggle_wrong_token = await client.post(
        "/api/settings/autonomous-learning",
        json={"enabled": True},
        headers={"X-Aletheia-Token": "wrong"},
    )
    assert master_toggle_wrong_token.status_code == 401
    assert saved == []
    assert audited == []
    assert load_config().agent.autonomous_learning is False

    headers = {"X-Aletheia-Token": "LEARNINGOWNER42"}
    master_toggle = await client.post(
        "/api/settings/autonomous-learning",
        json={"enabled": True},
        headers=headers,
    )
    assert master_toggle.status_code == 200
    assert saved == [True]
    assert [event["event_type"] for event in audited] == [
        "autonomous_learning_changed",
    ]
    assert load_config().agent.autonomous_learning is True

    assert (await client.post(
        "/api/settings/learning",
        json={},
        headers=headers,
    )).status_code != 401
    assert (await client.post(
        "/api/settings/learning/experiments/example/admit-trial",
        json={},
        headers=headers,
    )).status_code != 401

    # Read-only experiment inspection also requires the tunnel credential under
    # the default-deny policy (#398): read access exposes strategy state.
    read = await client.get("/api/settings/learning/experiments/example", headers=headers)
    assert read.status_code != 401


@pytest.mark.anyio
async def test_autonomous_learning_toggle_remains_local_without_tunnel(client):
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.tunnel.enabled = False
    cfg.agent.autonomous_learning = False
    save_config(cfg)

    response = await client.post(
        "/api/settings/autonomous-learning",
        json={"enabled": True},
    )

    assert response.status_code == 200
    assert response.json() == {"old": False, "new": True}
    assert load_config().agent.autonomous_learning is True


@pytest.mark.anyio
async def test_trial_admission_requires_explicit_confirmation(client):
    base = "/api/settings/learning/experiments/example/admit-trial"
    payload = {
        "verdict_id": "verdict",
        "expected_policy_epoch": "epoch",
        "reason": "begin bounded paper trial",
    }
    missing = await client.post(base, json=payload)
    denied = await client.post(base, json={**payload, "confirm_admission": False})
    assert missing.status_code == 422
    assert denied.status_code == 422


@pytest.mark.anyio
async def test_retired_trial_admission_requires_canonical_owner_surface(client):
    response = await client.post(
        "/api/settings/learning/experiments/experiment/admit-trial",
        json={
            "verdict_id": "verdict",
            "expected_policy_epoch": "epoch-current",
            "reason": "begin bounded paper trial",
            "confirm_admission": True,
        },
    )

    assert response.status_code == 409


def test_restart_audit_actor_source_maps_automated_triggers():
    """Regression #132: automated restart triggers are not recorded as actor='human'."""
    from aletheia.server.routes.gateway import _restart_audit_actor_source
    assert _restart_audit_actor_source("git_hook") == ("system", "git_hook")
    assert _restart_audit_actor_source("file_change") == ("dev_watcher", "dev_watcher")
    assert _restart_audit_actor_source("manual") == ("human", "dashboard")
    # unknown reasons default to human/dashboard (dashboard-originated)
    assert _restart_audit_actor_source("whatever")[0] == "human"


@pytest.mark.anyio
async def test_settings_update_controls_persist_new_config_fields(client):
    resp = await client.post(
        "/api/settings/exchange-markets",
        json={"spot": True, "usdt_futures": True, "paper_execution_mode": "bitget_demo", "confirm_exchange_divergence": True},
    )
    assert resp.status_code == 200
    assert resp.json()["paper_execution_mode"] == "bitget_demo"

    resp = await client.post(
        "/api/settings/trading",
        json={"slippage_bps": 8.5, "execution_fee_pct": 0.07, "auto_adopt_enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["trading"]["slippage_bps"] == 8.5
    assert resp.json()["trading"]["execution_fee_pct"] == 0.07
    assert resp.json()["trading"]["auto_adopt_enabled"] is False

    resp = await client.post(
        "/api/settings/health-config",
        json={
            "self_heal_enabled": False,
            "self_heal_min_interval_sec": 2400,
            "self_heal_max_per_hour": 1,
            "self_heal_code_drift": False,
            "self_heal_market_data": False,
            "self_heal_port_cleanup": False,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["updated"]["self_heal_enabled"] is False
    assert resp.json()["updated"]["self_heal_min_interval_sec"] == 2400
    assert resp.json()["updated"]["self_heal_max_per_hour"] == 1

    resp = await client.post(
        "/api/settings/universe",
        json={
            "min_depth_10bps_usd": 50000,
            "blacklist": ["doge/usdt", " DOGE/USDT ", "pepe/usdt"],
            "fallback_symbols": ["btc/usdt", "eth/usdt"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["universe"]["min_depth_10bps_usd"] == 50000
    assert resp.json()["universe"]["blacklist"] == ["DOGE/USDT", "PEPE/USDT"]
    assert resp.json()["universe"]["fallback_symbols"] == ["BTC/USDT", "ETH/USDT"]

    settings = (await client.get("/api/settings")).json()
    assert settings["exchange"]["paper_execution_mode"] == "bitget_demo"
    assert settings["trading"]["auto_adopt_enabled"] is False
    assert settings["health"]["self_heal_enabled"] is False
    assert settings["universe"]["fallback_symbols"] == ["BTC/USDT", "ETH/USDT"]


@pytest.mark.anyio
async def test_uta_profile_change_advances_account_generation(client):
    from aletheia.config.loader import load_config

    before = load_config().exchange
    assert before.uta_account_config_generation_for("bitget_demo") == 0

    response = await client.post(
        "/api/settings/exchange-markets",
        json={
            "spot": True,
            "usdt_futures": True,
            "paper_execution_mode": "bitget_demo",
            "bitget_profile": "uta_v3",
            "confirm_exchange_divergence": True,
        },
    )

    assert response.status_code == 200
    current = load_config().exchange
    assert current.uta_account_config_generation_for("bitget_demo") == 1
    assert current.uta_account_config_generation_for("bitget_live_limited") == 1


@pytest.mark.anyio
async def test_retired_uta_selector_is_rejected(client):
    response = await client.post(
        "/api/settings/exchange-markets",
        json={
            "spot": True,
            "usdt_futures": True,
            "uta_read_boundary": "uta_v1",
        },
    )

    assert response.status_code == 422



@pytest.mark.anyio
async def test_trading_mandate_settings_control_validates_and_redacts_nested_unknowns(client):
    mandate = {
        "enabled": True,
        "contract_version": "trading_mandate/v1",
        "mandate_id": "settings-test",
        "mandate_version": 1,
        "status": "active",
        "enforcement_mode": "observe_only",
        "market_scope": {
            "allowed_asset_classes": ["crypto"],
            "allowed_physical_market_types": ["spot"],
        },
        "edge_scope": {"allowed_directions": ["long"]},
    }
    saved = await client.post("/api/settings/trading-mandate", json={"mandate": mandate})
    assert saved.status_code == 200
    assert saved.json()["trading_mandate"]["resolution_status"] == "active"
    assert saved.json()["trading_mandate"]["market_scope"]["allowed_asset_classes"] == ["crypto"]

    settings = (await client.get("/api/settings")).json()
    assert settings["trading_mandate"]["mandate_id"] == "settings-test"

    malformed = {
        **mandate,
        "market_scope": {**mandate["market_scope"], "api_key": "do-not-return"},
    }
    rejected = await client.post("/api/settings/trading-mandate", json={"mandate": malformed})
    assert rejected.status_code == 422
    assert "do-not-return" not in rejected.text

    disabled = await client.post("/api/settings/trading-mandate", json={"mandate": None})
    assert disabled.status_code == 200
    assert disabled.json()["trading_mandate"]["resolution_status"] == "not_configured"


@pytest.mark.anyio
async def test_settings_rejects_mismatched_or_invalid_config_controls(client):
    """UI/API mismatches must fail loudly instead of silently ignoring fields."""
    resp = await client.post("/api/settings/trading", json={"slip_bps": 3})
    assert resp.status_code == 422

    resp = await client.post(
        "/api/settings/exchange-markets",
        json={"spot": True, "usdt_futures": True, "paper_execution_mode": "unknown"},
    )
    assert resp.status_code == 400

    resp = await client.post("/api/settings/universe", json={"fallback_symbols": []})
    assert resp.status_code == 400

    resp = await client.post(
        "/api/settings/universe",
        json={"entry_ttl_sec": 1200, "candidate_max_continuous_age_sec": 600},
    )
    assert resp.status_code == 400
    assert "must be >= entry_ttl_sec" in resp.json()["detail"]

    resp = await client.post("/api/settings/market-data", json={"ccxt_exchange": "unknown"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_trading_disable_persists_rollout_fence_and_reports_invalidation_failure(
    client, monkeypatch,
):
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.trading.persona_sizing_enabled = True
    cfg.trading.persona_progressive_unlock_enabled = True
    save_config(cfg)
    monkeypatch.setattr(
        "aletheia.trading.persona_rollout.invalidate_persona_rollouts",
        lambda **_kwargs: None,
    )

    response = await client.post(
        "/api/settings/trading",
        json={"persona_progressive_unlock_enabled": False},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "persona_rollout_invalidation_failed"
    saved = load_config().trading
    assert saved.persona_progressive_unlock_enabled is False
    assert saved.persona_rollout_generation == 1


@pytest.mark.anyio
async def test_trading_enable_reports_rollout_creation_failure(client, monkeypatch):
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.trading.persona_sizing_enabled = True
    cfg.trading.persona_progressive_unlock_enabled = False
    save_config(cfg)
    monkeypatch.setattr(
        "aletheia.trading.persona_rollout.current_persona_rollout",
        lambda *_args, **_kwargs: None,
    )

    response = await client.post(
        "/api/settings/trading",
        json={"persona_progressive_unlock_enabled": True},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "persona_rollout_creation_failed"
    assert load_config().trading.persona_progressive_unlock_enabled is True


@pytest.mark.anyio
async def test_universe_candidate_attention_pair_updates_atomically(client):
    from aletheia.config.loader import load_config

    increased = await client.post(
        "/api/settings/universe",
        json={"entry_ttl_sec": 2000, "candidate_max_continuous_age_sec": 3000},
    )
    assert increased.status_code == 200
    assert increased.json()["universe"]["entry_ttl_sec"] == 2000
    assert increased.json()["universe"]["candidate_max_continuous_age_sec"] == 3000

    decreased = await client.post(
        "/api/settings/universe",
        json={"entry_ttl_sec": 600, "candidate_max_continuous_age_sec": 900},
    )
    assert decreased.status_code == 200

    rejected = await client.post(
        "/api/settings/universe",
        json={"entry_ttl_sec": 1200, "candidate_max_continuous_age_sec": 600},
    )
    assert rejected.status_code == 400
    stored = load_config().universe
    assert stored.entry_ttl_sec == 600
    assert stored.candidate_max_continuous_age_sec == 900


@pytest.mark.anyio
async def test_market_data_validation_is_atomic_before_save(client):
    from aletheia.config.loader import load_config

    before = load_config().market_data.ccxt_fallback
    resp = await client.post(
        "/api/settings/market-data",
        json={"ccxt_fallback": "okx", "ohlcv_cache_ttl_min": 999},
    )
    assert resp.status_code == 400
    assert load_config().market_data.ccxt_fallback == before


@pytest.mark.anyio
async def test_market_data_exchange_divergence_requires_explicit_confirmation(client):
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "okx_demo"
    cfg.exchange.name = "okx"
    cfg.market_data.ccxt_exchange = "okx"
    save_config(cfg)

    rejected = await client.post(
        "/api/settings/market-data",
        json={"ccxt_exchange": "binance"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "exchange_source_divergence_confirmation_required"
    assert load_config().market_data.ccxt_exchange == "okx"

    confirmed = await client.post(
        "/api/settings/market-data",
        json={"ccxt_exchange": "binance", "confirm_exchange_divergence": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["exchange_alignment"]["status"] == "diverged"
    assert load_config().market_data.ccxt_exchange == "binance"


@pytest.mark.anyio
async def test_execution_target_change_also_requires_divergence_confirmation(client):
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.exchange.paper_execution_mode = ""
    cfg.market_data.ccxt_exchange = "binance"
    save_config(cfg)
    body = {"spot": True, "usdt_futures": True, "paper_execution_mode": "okx_demo"}

    rejected = await client.post("/api/settings/exchange-markets", json=body)
    assert rejected.status_code == 409
    assert load_config().exchange.paper_execution_mode == ""

    body["confirm_exchange_divergence"] = True
    confirmed = await client.post("/api/settings/exchange-markets", json=body)
    assert confirmed.status_code == 200
    assert confirmed.json()["exchange_alignment"]["status"] == "diverged"
    assert load_config().exchange.paper_execution_mode == "okx_demo"

# Retired model subprocess routes have no execution surface.
@pytest.mark.anyio
@pytest.mark.parametrize("method,path", [
    ("post", "/api/model/codex/login-start"),
    ("get", "/api/model/codex/login-status"),
    ("post", "/api/model/codex/login-cancel"),
    ("get", "/api/model/codex/models"),
])
async def test_codex_routes_are_removed(client, method, path):
    response = await client.request(method, path)
    assert response.status_code == 404


# --- Verified-Connection: model provider candidate-key test (verify-before-commit) ---

@pytest.mark.anyio
async def test_model_test_candidate_pass(client, monkeypatch):
    """A reachable candidate key returns available=true, echoes no key, persists nothing."""
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider
    from aletheia.model_provider.base import ModelHealth

    async def fake_health(self):
        return ModelHealth(provider="openai_compatible", available=True, model=self.model)
    monkeypatch.setattr(OpenAICompatibleProvider, "healthcheck", fake_health)

    # Seed the provider (no key) so we can prove the probe adds none.
    _seed_model()

    secret = "sk-candidate-not-saved-7777"
    resp = await client.post("/api/model/test-candidate", json={
        "provider": "openai_compatible",
        "base_url": "http://localhost:9000/v1",
        "model": "probe-model",
        "api_key": secret,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    # candidate key must never be echoed
    assert secret not in resp.text
    # nothing persisted: the provider still has no configured key
    cfg = await client.get("/api/model")
    assert cfg.json()["providers"]["openai_compatible"]["api_key"]["configured"] is False


@pytest.mark.anyio
async def test_model_test_candidate_probe_succeeds_without_model_catalogue(client, monkeypatch):
    """An inference probe must not require optional /models list permission."""
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider
    from aletheia.model_provider.base import ModelHealth
    import aletheia.model_provider.broker as broker

    async def unavailable_catalogue(self):
        return ModelHealth(provider="openai_compatible", available=False, model=self.model)

    async def successful_inference(*args, **kwargs):
        return None

    monkeypatch.setattr(OpenAICompatibleProvider, "healthcheck", unavailable_catalogue)
    monkeypatch.setattr(broker, "brokered_complete", successful_inference)
    _seed_model()

    resp = await client.post("/api/model/test-candidate", json={
        "provider": "openai_compatible",
        "base_url": "http://localhost:9000/v1",
        "model": "probe-model",
        "api_key": "sk-candidate-not-saved-8888",
        "probe": True,
    })
    assert resp.status_code == 200
    assert resp.json()["available"] is True


@pytest.mark.anyio
async def test_model_test_candidate_fail(client, monkeypatch):
    """An unreachable candidate returns available=false + error, persists nothing."""
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider
    from aletheia.model_provider.base import ModelHealth

    async def fake_health(self):
        return ModelHealth(provider="openai_compatible", available=False, error="HTTP 401")
    monkeypatch.setattr(OpenAICompatibleProvider, "healthcheck", fake_health)

    # Seed the provider (no key) so we can prove the probe adds none.
    _seed_model()

    resp = await client.post("/api/model/test-candidate", json={
        "provider": "openai_compatible",
        "base_url": "http://localhost:9000/v1",
        "api_key": "sk-bad",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "401" in (body["error"] or "")
    cfg = await client.get("/api/model")
    assert cfg.json()["providers"]["openai_compatible"]["api_key"]["configured"] is False


@pytest.mark.anyio
async def test_model_test_candidate_codex_rejected(client):
    """Retired providers cannot accept a candidate credential or run a probe."""
    resp = await client.post("/api/model/test-candidate", json={"provider": "codex_cli"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_model_test_candidate_invalid_provider(client):
    resp = await client.post("/api/model/test-candidate", json={"provider": "nonexistent"})
    assert resp.status_code == 400


# --- Verified-Connection: bitget candidate-cred test (Option Z, verify-before-commit) ---

@pytest.mark.anyio
async def test_bitget_test_candidate_pass(client, monkeypatch):
    """Reachable candidate creds return connected=true, echo no creds, persist nothing."""
    import httpx

    class FakeResp:
        def json(self):
            return {"code": "00000", "data": {}}

    class FakeAsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    secret = "bg-candidate-secret-5555"
    resp = await client.post("/api/bitget/test-candidate", json={
        "profile": "demo",
        "api_key": "bg-cand-key",
        "api_secret": secret,
        "passphrase": "bg-cand-pass",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["profile"] == "demo"
    assert secret not in resp.text
    # nothing persisted: demo creds still unconfigured
    cs = await client.get("/api/bitget/credentials-status")
    assert cs.json()["demo"]["api_key"]["configured"] is False


@pytest.mark.anyio
async def test_bitget_test_candidate_fail(client, monkeypatch):
    """Rejected candidate creds never echo an upstream message or candidate secret."""
    import httpx

    upstream_secret = "bitget-upstream-secret-CAFEBABE"

    class FakeResp:
        def json(self):
            return {"code": "40012", "msg": f"apikey does not match secret={upstream_secret}"}

    class FakeAsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    resp = await client.post("/api/bitget/test-candidate", json={
        "profile": "demo",
        "api_key": "bad", "api_secret": "bad", "passphrase": "bad",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False
    assert body["error"] == "credential_probe_rejected"
    assert upstream_secret not in resp.text
    cs = await client.get("/api/bitget/credentials-status")
    assert cs.json()["demo"]["api_key"]["configured"] is False


@pytest.mark.anyio
async def test_bitget_test_candidate_invalid_profile(client):
    resp = await client.post("/api/bitget/test-candidate", json={
        "profile": "production", "api_key": "x", "api_secret": "y", "passphrase": "z",
    })
    assert resp.status_code == 400


# --- Verified-Connection: telegram reachability health (facet A only) ---

@pytest.mark.anyio
async def test_telegram_health_not_configured(client):
    """No bot token configured -> reachable false, honest not_configured, no token echo."""
    resp = await client.get("/api/telegram/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is False
    assert body.get("error") == "not_configured"


@pytest.mark.anyio
async def test_telegram_health_reachable(client, monkeypatch):
    """A configured + reachable token reports the bot username, never the token."""
    import aletheia.telegram.routes as tg_routes
    from aletheia.config.secrets import set_secret

    token = "123456:super-secret-bot-token"
    set_secret("telegram.bot_token", token)

    class FakeResp:
        def json(self):
            return {"ok": True, "result": {"username": "aletheia_bot"}}

    class FakeAsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return FakeResp()

    monkeypatch.setattr(tg_routes, "httpx_client", lambda **kwargs: FakeAsyncClient())

    resp = await client.get("/api/telegram/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is True
    assert body["username"] == "aletheia_bot"
    assert token not in resp.text

@pytest.mark.anyio
@pytest.mark.parametrize("provider_type,protocol", [
    ("openai_compatible", "chat_completions"),
    ("openai_compatible", "responses"),
    ("anthropic_compatible", "anthropic_messages"),
])
async def test_http_candidate_can_verify_without_catalogue_permission(client, monkeypatch, provider_type, protocol):
    """Manual models remain verifiable when the endpoint catalogue is forbidden."""
    from aletheia.model_provider.base import ModelHealth, ModelResponse
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider
    from extensions.model_endpoints.anthropic_compatible import AnthropicCompatibleProvider

    provider_class = OpenAICompatibleProvider if provider_type == "openai_compatible" else AnthropicCompatibleProvider

    async def no_catalogue(self):
        return ModelHealth(
            provider=self.name,
            available=False,
            model=self.model,
            error="HTTP model catalogue unavailable",
        )

    async def successful_inference(self, request):
        return ModelResponse(content="OK", model=self.model, usage={})

    monkeypatch.setattr(provider_class, "healthcheck", no_catalogue)
    monkeypatch.setattr(provider_class, "complete", successful_inference)

    secret = "synthetic-probe-only-1357"
    response = await client.post(
        "/api/model/test-candidate",
        json={
            "provider": provider_type,
            "base_url": "https://manual-endpoint.invalid/v1",
            "protocol": protocol,
            "model": "known-endpoint",
            "api_key": secret,
            "probe": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["available"] is True
    assert secret not in response.text

    config = await client.get("/api/model")
    assert provider_type not in config.json()["providers"]
    assert secret not in config.text


@pytest.mark.anyio
async def test_pi_daemon_apply_failure_is_typed_and_keeps_desired_actual_distinct(
    client, monkeypatch
):
    from aletheia.config.loader import load_config

    async def fail_reconfigure(**_kwargs):
        raise RuntimeError("private runtime detail")

    actual = {
        "running": False,
        "event_set": False,
        "lock_held": False,
        "task_generation": 7,
        "started_at": None,
        "active_params": None,
        "last_error_summary": "task_start_failed",
    }
    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.reconfigure_pi_daemon",
        fail_reconfigure,
    )
    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.daemon_status",
        lambda: actual,
    )

    with patch("aletheia.files.jsonl.write_audit") as write_audit:
        response = await client.post(
            "/api/settings/pi_daemon",
            json={"heartbeat_sec": 120},
        )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "runtime_apply_failed"
    assert detail["persisted"] is True
    assert detail["applied"] is False
    assert detail["runtime_status"]["configured"] is True
    assert detail["runtime_status"]["running"] is False
    assert detail["runtime_status"]["blocker"] == "reconfigure_error"
    assert "private runtime detail" not in response.text
    assert load_config().pi_daemon.heartbeat_sec == 120
    audit = write_audit.call_args.args[0]
    assert audit["event_type"] == "pi_daemon_config_apply_failed"
    assert audit["persisted"] is True
    assert audit["applied"] is False

    settings = (await client.get("/api/settings")).json()
    assert settings["pi_daemon"]["heartbeat_sec"] == 120
    assert settings["pi_daemon_runtime"]["running"] is False


@pytest.mark.anyio
async def test_pi_daemon_success_reports_verified_effective_state(client, monkeypatch):
    async def apply_config(**_kwargs):
        return {"configured": False, "running": False, "blocker": None}

    actual = {
        "running": False,
        "event_set": False,
        "lock_held": False,
        "task_generation": 8,
        "started_at": None,
        "active_params": None,
        "last_error_summary": None,
    }
    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.reconfigure_pi_daemon",
        apply_config,
    )
    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.daemon_status",
        lambda: actual,
    )

    with patch("aletheia.files.jsonl.write_audit") as write_audit:
        response = await client.post(
            "/api/settings/pi_daemon",
            json={"enabled": False},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["persisted"] is True
    assert payload["applied"] is True
    assert payload["runtime_status"]["configured"] is False
    assert payload["runtime_status"]["running"] is False
    assert write_audit.call_args.args[0]["event_type"] == "pi_daemon_config_changed"
