"""Model-provider profile and access-method configuration contracts."""

import pytest  # pyright: ignore[reportMissingImports]
from httpx import ASGITransport, AsyncClient  # pyright: ignore[reportMissingImports]

from aletheia.config.secrets import get_secret
from aletheia.model_provider.router import clear_cache, get_provider
from aletheia.server.app import app


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)

    from tests.helpers.model_http import mock_model_probe

    mock_model_probe(monkeypatch)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    clear_cache()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http, tmp_path
    clear_cache()


def test_clean_model_state_is_not_a_default_provider_inconsistency():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.diagnostics.config_consistency import check_config_consistency

    issues = check_config_consistency(AletheiaConfig())

    assert all(issue.field_path != "models.default_provider" for issue in issues)


@pytest.mark.anyio
async def test_tiers_select_distinct_models_on_one_endpoint_without_rebinding_it(client, monkeypatch):
    from aletheia.config.loader import load_config
    from aletheia.model_provider.base import ModelResponse

    http, _ = client
    created = await http.post("/api/settings/model-profiles", json={
        "profile_id": "shared", "display_name": "Shared HTTP", "type": "openai_compatible",
        "base_url": "http://model.test/v1", "model": "preferred", "enabled": True,
    })
    assert created.status_code == 201
    probed = []

    async def probe(endpoint, provider, request, **kwargs):
        probed.append((endpoint, provider.model))
        return ModelResponse(content="OK", model=provider.model)

    monkeypatch.setattr("aletheia.model_provider.broker.brokered_complete", probe)
    pairs = {
        "fast": {"endpoint_id": "shared", "model_id": "small"},
        "deep": {"endpoint_id": "shared", "model_id": "large"},
    }
    assigned = await http.post("/api/settings/model-routing", json=pairs)
    assert assigned.status_code == 200, assigned.text
    view = (await http.get("/api/model")).json()
    assert {tier: view.get(tier) for tier in pairs} == pairs
    assert sorted(probed) == [("shared", "large"), ("shared", "small")]
    assert load_config().models.providers["shared"].model == "preferred"


def test_current_missing_or_unknown_tier_does_not_use_default(monkeypatch):
    from aletheia.config.schema import AletheiaConfig, ProviderConfig
    from aletheia.model_provider import router

    cfg = AletheiaConfig()
    cfg.models.default_provider = "default"
    cfg.models.providers["default"] = ProviderConfig(model="preferred")
    monkeypatch.setattr(router, "load_config", lambda: cfg)
    for tier in ("fast", "deep", "typo"):
        with pytest.raises(ValueError):
            router.get_provider_for_tier(tier)


@pytest.mark.parametrize("tiers", [
    {"deep_provider": "deep", "fast_provider": None},
    {"deep_provider": "deep"},
    {},
    {"deep": None},
    {"fast": None},
    {"deep": {"endpoint_id": "deep", "model_id": "explicit"}, "fast": None},
])
@pytest.mark.parametrize("default_profile", [{"model": "small"}, {}])
def test_historical_tier_decode_binds_old_resolved_model_without_mutating_input(tiers, default_profile):
    import copy
    from aletheia.config.schema import ModelsConfig, ProviderConfig

    raw = {
        "default_provider": "default", **tiers,
        "providers": {"default": default_profile, "deep": {"model": "large"}},
    }
    original = copy.deepcopy(raw)
    parsed = ModelsConfig.model_validate(raw).model_dump()
    for tier in ("fast", "deep"):
        endpoint = tiers.get(f"{tier}_provider") or "default"
        expected = {"endpoint_id": endpoint, "model_id": ProviderConfig(**raw["providers"][endpoint]).model}
        assert parsed.get(tier) == (tiers[tier] if tier in tiers else expected)
    assert "fast_provider" not in parsed and "deep_provider" not in parsed
    assert raw == original


@pytest.mark.parametrize("models", [
    {"providers": [], "deep_provider": "old"},
    {"deep_provider": []},
    {"deep_provider": False},
    {"deep_provider": "old", "deep": None},
])
def test_malformed_or_ambiguous_historical_tiers_fail_validation(models):
    from pydantic import ValidationError
    from aletheia.config.schema import ModelsConfig

    with pytest.raises(ValidationError):
        ModelsConfig.model_validate(models)


@pytest.mark.anyio
@pytest.mark.parametrize("tiers", [{"deep_provider": "shared", "fast_provider": None}, {}])
async def test_historical_tier_loading_never_writes_or_reads_credentials(client, monkeypatch, tiers):
    import yaml
    import aletheia.config.loader as loader
    from aletheia.config.schema import ProviderConfig

    _, directory = client
    cfg = loader.load_config()
    cfg.models.default_provider = "shared"
    cfg.models.providers["shared"] = ProviderConfig(
        type="openai_compatible", model="preferred", base_url="http://model.test/v1",
        api_key_secret_ref="test-reference",
    )
    loader.save_config(cfg)
    path = directory / "config.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["models"].pop("deep")
    raw["models"].pop("fast")
    raw["models"].update(tiers)
    path.write_text(yaml.safe_dump(raw))
    original = path.read_bytes()

    def forbidden(*_args, **_kwargs):
        pytest.fail("Historical tier decode must not write config or resolve credentials")

    monkeypatch.setattr(loader, "save_config", forbidden)
    monkeypatch.setattr("aletheia.config.secrets.get_secret", forbidden)
    monkeypatch.setattr("extensions.model_endpoints.http_runtime.get_secret", forbidden)
    parsed = loader.load_config(use_cache=False)
    assert parsed.models.fast == parsed.models.deep
    assert parsed.models.fast.model_id == "preferred"
    assert path.read_bytes() == original


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["probe", "identity_drift"])
async def test_tier_assignment_failure_preserves_roles_and_preferred_model(client, monkeypatch, failure):
    from aletheia.config.loader import load_config, mutate_config
    from aletheia.model_provider.base import ModelResponse

    http, _ = client
    created = await http.post("/api/settings/model-profiles", json={
        "profile_id": "shared", "display_name": "Shared HTTP", "type": "openai_compatible",
        "base_url": "http://model.test/v1", "model": "preferred", "enabled": True,
    })
    assert created.status_code == 201
    before = load_config().models.model_dump()

    async def probe(endpoint, provider, request, **kwargs):
        if provider.model == "small":
            if failure == "probe":
                raise RuntimeError("test probe failure")
            def change(cfg):
                cfg.models.providers[endpoint].base_url = "http://changed.test/v1"
                return True
            mutate_config(change)
        return ModelResponse(content="OK", model=provider.model)

    monkeypatch.setattr("aletheia.model_provider.broker.brokered_complete", probe)
    response = await http.post("/api/settings/model-routing", json={
        "deep": {"endpoint_id": "shared", "model_id": "large"},
        "fast": {"endpoint_id": "shared", "model_id": "small"},
    })
    assert response.status_code == (422 if failure == "probe" else 409), response.text
    after = load_config().models.model_dump()
    assert after["deep"] == before["deep"] is None
    assert after["fast"] == before["fast"] is None
    assert after["providers"]["shared"]["model"] == "preferred"
    if failure == "probe":
        assert after == before
    else:
        assert after["providers"]["shared"]["base_url"] == "http://changed.test/v1"


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [
    {"deep_provider": "shared"},
    {"fast": {"endpoint_id": "shared", "model_id": " "}},
    {"fast": {"endpoint_id": " ", "model_id": "small"}},
    {"deep": {"endpoint_id": "shared", "model_id": "large", "command": "unused"}},
])
async def test_current_tier_requests_reject_legacy_and_incomplete_identity(client, payload):
    http, _ = client
    response = await http.post("/api/settings/model-routing", json=payload)
    assert response.status_code == 422


def test_tier_provider_clones_keep_independent_models_and_shared_endpoint(monkeypatch):
    from types import SimpleNamespace
    from aletheia.config.schema import AletheiaConfig, ModelSelection, ProviderConfig
    from aletheia.model_provider import router

    cfg = AletheiaConfig()
    cfg.models.providers["shared"] = ProviderConfig(
        type="openai_compatible", base_url="http://model.test/v1", model="preferred", enabled=True,
    )
    cached = SimpleNamespace(model="preferred", base_url="http://model.test/v1")
    monkeypatch.setattr(router, "load_config", lambda: cfg)
    monkeypatch.setattr(router, "_cache", {"shared": cached})
    small = router.get_provider(ModelSelection(endpoint_id="shared", model_id="small"))
    large = router.get_provider(ModelSelection(endpoint_id="shared", model_id="large"))
    assert small is not large and small is not cached and large is not cached
    assert (small.model, large.model, cached.model) == ("small", "large", "preferred")
    assert small.base_url == large.base_url == cached.base_url


@pytest.mark.anyio
async def test_clean_model_state_has_no_enabled_provider_or_routing_role(client):
    http, _ = client

    response = await http.get("/api/model")

    assert response.status_code == 200
    assert response.json() == {
        "default_provider": "",
        "deep": None,
        "fast": None,
        "providers": {},
    }


@pytest.mark.anyio
@pytest.mark.parametrize("provider_type", ["codex_cli", "claude_cli", "pi_cli", "openai_native", "anthropic_native", "databricks"])
async def test_retired_provider_history_is_read_only_and_never_resolves_secrets(
    client, monkeypatch, provider_type
):
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.model_provider import router as model_router
    from aletheia.server.routes import ui
    from extensions.model_endpoints import credential_routes as model_routes

    http, _ = client
    cfg = load_config()
    cfg.models.providers["historical-cli"] = ProviderConfig(
        type=provider_type,
        enabled=True,
        api_key_secret_ref="historical-api-reference",
        oauth_token_ref="historical-oauth-reference",
        command="must-not-execute",
        codex_model="historical-model",
        verification_verified=True,
        verification_identity="historical-identity",
    )
    cfg.models.default_provider = "historical-cli"
    save_config(cfg)
    before = load_config().models.model_dump()

    def forbidden(*_args, **_kwargs):
        pytest.fail("retired provider must not resolve a credential or execute")

    monkeypatch.setattr("extensions.model_endpoints.http_runtime.get_secret", forbidden)
    monkeypatch.setattr(model_routes, "get_secret", forbidden)
    assert not ui._model_credential_configured(cfg)
    view = (await http.get("/api/model")).json()
    assert view["default_provider"] == "historical-cli"
    row = view["providers"]["historical-cli"]
    assert row["kind"] == "unknown"
    assert row["configured"] is False
    assert row["api_key"] == row["oauth"] == {"configured": False, "last4": None}
    assert "command" not in row and "codex_model" not in row
    ping = await http.post("/api/model/ping")
    assert ping.status_code == 200
    assert ping.json()["ok"] is False
    assert ping.json()["verification_persisted"] is False
    for role in ("default_provider", "deep", "fast"):
        target = "historical-cli" if role == "default_provider" else {"endpoint_id": "historical-cli", "model_id": "m1"}
        response = await http.post("/api/settings/model-routing", json={role: target})
        assert response.status_code == 422
    for path in ("/api/model/detect-models", "/api/model/test-candidate"):
        response = await http.post(path, json={"provider": "historical-cli"})
        assert response.status_code == 400
    response = await http.patch(
        f"/api/settings/model-integrations/{provider_type}", json={"enabled": True}
    )
    assert response.status_code == 404
    assert load_config().models.model_dump() == before
    response = await http.post("/api/settings/model-routing", json={"default_provider": None})
    assert response.status_code == 200
    assert load_config().models.default_provider == ""


@pytest.mark.anyio
@pytest.mark.parametrize("provider_type", ["openai_compatible", "anthropic_compatible"])
@pytest.mark.parametrize("operation", ["detect-models", "test-candidate"])
@pytest.mark.parametrize("explicit_key", [False, True])
async def test_candidate_key_stays_bound_to_its_endpoint(client, monkeypatch, provider_type, operation, explicit_key):
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.model_provider.base import ModelHealth

    http, _ = client
    cfg = load_config()
    cfg.models.providers["scoped"] = ProviderConfig(
        type=provider_type, base_url="https://configured.example/v1", model="configured",
        api_key_secret_ref="model.profile.scoped.api_key",
    )
    save_config(cfg)
    reads = []
    observed = []

    def secret(ref):
        reads.append(ref)
        return "synthetic-stored-key"

    async def catalogue(provider):
        assert getattr(provider, "_aletheia_provider_name", None) == "scoped"
        observed.append((provider.base_url, provider.api_key))
        return []

    async def health(provider):
        await catalogue(provider)
        return ModelHealth(provider=provider.name, available=True, model=provider.model)

    monkeypatch.setattr("extensions.model_endpoints.credential_routes.get_secret", secret)
    adapter = f"extensions.model_endpoints.{provider_type}." + (
        "OpenAICompatibleProvider" if provider_type == "openai_compatible" else "AnthropicCompatibleProvider"
    )
    monkeypatch.setattr(adapter + ".list_models", catalogue)
    monkeypatch.setattr(adapter + ".healthcheck", health)
    payload = {"provider": "scoped"}
    if operation == "test-candidate":
        payload["model"] = "another-model"
    if explicit_key:
        payload.update(base_url="https://candidate.example/v1", api_key="  synthetic-candidate-key  ")
    response = await http.post(f"/api/model/{operation}", json=payload)
    assert response.status_code == 200, response.text
    assert observed == [(
        "https://candidate.example/v1" if explicit_key else "https://configured.example/v1",
        "synthetic-candidate-key" if explicit_key else "synthetic-stored-key",
    )]
    assert reads == ([] if explicit_key else ["model.profile.scoped.api_key"])


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["command", "mode", "codex_model", "default", "set_as_deep", "set_as_fast"])
async def test_retired_settings_fields_are_rejected(client, field):
    """A retired transport field must not be accepted by the live profile writer.

    The former fixed-provider mutation endpoint is retired outright and owns no
    route (asserted by spec LLMH-S07), so probing its status code here pinned
    route-matching order rather than behavior.
    """
    http, _ = client
    response = await http.post(
        "/api/settings/model-profiles", json={
            "profile_id": "http", "display_name": "HTTP", "type": "openai_compatible", field: "retired",
        }
    )
    assert response.status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize("method,path", [
    ("POST", "/model/openai-oauth/start"),
    ("GET", "/model/openai-oauth/status"),
    ("POST", "/model/openai-oauth/finalize"),
    ("POST", "/model/openai-oauth/cancel"),
    ("POST", "/model/openai-oauth/disconnect"),
    ("POST", "/model/claude-oauth/start"),
    ("POST", "/model/claude-oauth/complete"),
    ("GET", "/model/claude-oauth/status"),
    ("POST", "/model/claude-oauth/cancel"),
    ("POST", "/model/claude-oauth/disconnect"),
])
async def test_retired_oauth_routes_are_absent(client, method, path):
    response = await client[0].request(method, f"/api{path}")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_http_profile_never_resolves_or_deletes_historical_oauth_refs(client, monkeypatch):
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.model_provider import router as model_router
    from aletheia.server.routes import ui
    from extensions.model_endpoints import credential_routes as model_routes

    http, _ = client
    cfg = load_config()
    profile = ProviderConfig(
        type="openai_compatible", enabled=True, base_url="https://example.invalid/v1",
        api_key_secret_ref="current-api-key", oauth_token_ref="historical-access",
        oauth_refresh_ref="historical-refresh",
    )
    cfg.models.providers["http-profile"] = profile
    save_config(cfg)
    reads, deletes = [], []

    def read(ref, **kwargs):
        assert ref == "current-api-key"
        reads.append(ref)
        return "synthetic-api-key"

    def delete(ref):
        assert ref == "current-api-key"
        deletes.append(ref)

    from extensions.model_endpoints import http_runtime

    for module in (http_runtime, model_routes):
        monkeypatch.setattr(module, "get_secret", read)
    monkeypatch.setattr(model_routes, "delete_secret", delete)
    assert model_router.provider_configuration_identity("http-profile", profile)
    assert ui._model_credential_configured(cfg)
    response = await http.get("/api/model")
    assert response.status_code == 200
    assert response.json()["providers"]["http-profile"]["oauth"]["configured"] is False
    response = await http.delete("/api/settings/model-profiles/http-profile", params={"confirm": "true"})
    assert response.status_code == 200
    assert reads and deletes == ["current-api-key"]
    assert "http-profile" not in load_config().models.providers


@pytest.mark.anyio
@pytest.mark.parametrize("method,path", [
    ("POST", "/api/model/detect-models"),
    ("POST", "/api/model/test-candidate"),
    ("POST", "/api/settings/model-profiles"),
    ("PATCH", "/api/settings/model-profiles/http"),
])
async def test_workspace_fields_are_rejected_on_http_requests(client, monkeypatch, method, path):
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider

    async def catalogue(_self):
        return []

    monkeypatch.setattr(OpenAICompatibleProvider, "list_models", catalogue)
    cfg = load_config()
    cfg.models.providers["http"] = ProviderConfig(type="openai_compatible", base_url="https://example.invalid/v1")
    save_config(cfg)
    body = {"workspace_url": "https://retired-workspace.invalid"}
    if path == "/api/settings/model-profiles":
        body.update(profile_id="new-http", display_name="HTTP", type="openai_compatible", base_url="https://example.invalid/v1")
    elif method == "POST":
        body["provider"] = "http"
    response = await client[0].request(method, path, json=body)
    assert response.status_code == 422


@pytest.mark.anyio
async def test_failed_profile_verification_does_not_persist_config_or_secret(client, monkeypatch):
    from aletheia.model_provider.base import ModelHealth
    import aletheia.model_provider.broker as broker
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider

    async def unavailable(provider):
        return ModelHealth(provider=provider.name, available=False, error="unauthorized")

    async def failed_inference(*_args, **_kwargs):
        raise RuntimeError("unauthorized")

    monkeypatch.setattr(OpenAICompatibleProvider, "healthcheck", unavailable)
    monkeypatch.setattr(broker, "brokered_complete", failed_inference)
    response = await client[0].post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "unverified-api",
            "display_name": "Unverified API",
            "type": "openai_compatible",
            "base_url": "https://unverified.example/v1",
            "model": "test-model",
            "api_key": "must-not-be-saved",
        },
    )

    assert response.status_code == 422
    assert "unverified-api" not in (await client[0].get("/api/model")).json()["providers"]
    assert get_secret("model.profile.unverified-api.api_key") is None


@pytest.mark.anyio
async def test_profile_creation_trusts_inference_when_catalogue_is_unavailable(
    client, monkeypatch
):
    from aletheia.model_provider.base import ModelHealth
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider

    async def unavailable_catalogue(provider):
        return ModelHealth(
            provider=provider.name,
            available=False,
            model=provider.model,
            error="model catalogue unavailable",
        )

    monkeypatch.setattr(OpenAICompatibleProvider, "healthcheck", unavailable_catalogue)
    response = await client[0].post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "probe-verified-api",
            "display_name": "Probe Verified API",
            "type": "openai_compatible",
            "base_url": "https://probe-verified.example/v1",
            "model": "test-model",
            "api_key": "verified-by-inference",
        },
    )

    assert response.status_code == 201
    providers = (await client[0].get("/api/model")).json()["providers"]
    assert "probe-verified-api" in providers
    assert providers["probe-verified-api"]["api_key"]["configured"] is True


@pytest.mark.anyio
async def test_openai_profile_rejects_invalid_protocol_before_probe(client, monkeypatch):
    from unittest.mock import AsyncMock

    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider

    healthcheck = AsyncMock(side_effect=AssertionError("provider must not be called"))
    monkeypatch.setattr(OpenAICompatibleProvider, "healthcheck", healthcheck)

    response = await client[0].post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "bad-openai-protocol",
            "display_name": "Bad OpenAI Protocol",
            "type": "openai_compatible",
            "base_url": "https://openai-compatible.example/v1",
            "model": "test-model",
            "protocol": "anthropic_messages",
            "api_key": "must-not-be-saved",
        },
    )

    assert response.status_code == 422
    assert "Unsupported openai_compatible protocol" in response.text
    assert "bad-openai-protocol" not in (await client[0].get("/api/model")).json()["providers"]
    assert get_secret("model.profile.bad-openai-protocol.api_key") is None
    healthcheck.assert_not_called()


@pytest.mark.anyio
async def test_anthropic_profile_uses_registry_protocol_authority(client):
    response = await client[0].post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "anthropic-api",
            "display_name": "Anthropic API",
            "type": "anthropic_compatible",
            "base_url": "https://anthropic-compatible.example",
            "model": "claude-test",
            "api_key": "anthropic-secret",
        },
    )

    assert response.status_code == 201
    providers = (await client[0].get("/api/model")).json()["providers"]
    assert providers["anthropic-api"]["protocol"] == "anthropic_messages"

    invalid = await client[0].patch(
        "/api/settings/model-profiles/anthropic-api",
        json={"protocol": "chat_completions"},
    )
    assert invalid.status_code == 422
    assert "Unsupported anthropic_compatible protocol" in invalid.text


@pytest.mark.anyio
async def test_test_candidate_rejects_invalid_protocol_before_probe(client, monkeypatch):
    from unittest.mock import AsyncMock

    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider

    healthcheck = AsyncMock(side_effect=AssertionError("provider must not be called"))
    monkeypatch.setattr(OpenAICompatibleProvider, "healthcheck", healthcheck)

    response = await client[0].post(
        "/api/model/test-candidate",
        json={
            "provider": "openai_compatible",
            "type": "openai_compatible",
            "base_url": "https://openai-compatible.example/v1",
            "model": "test-model",
            "protocol": "bogus",
            "api_key": "candidate-secret",
            "probe": True,
        },
    )

    assert response.status_code == 422
    assert "Unsupported openai_compatible protocol" in response.text
    healthcheck.assert_not_called()


@pytest.mark.anyio
async def test_detect_models_rejects_invalid_protocol_before_provider_call(client, monkeypatch):
    from unittest.mock import AsyncMock

    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider

    list_models = AsyncMock(side_effect=AssertionError("provider must not be called"))
    monkeypatch.setattr(OpenAICompatibleProvider, "list_models", list_models)

    response = await client[0].post(
        "/api/model/detect-models",
        json={
            "provider": "openai_compatible",
            "type": "openai_compatible",
            "base_url": "https://openai-compatible.example/v1",
            "protocol": "anthropic_messages",
            "api_key": "candidate-secret",
        },
    )

    assert response.status_code == 422
    assert "Unsupported openai_compatible protocol" in response.text
    list_models.assert_not_called()


@pytest.mark.anyio
async def test_profile_create_rolls_back_secret_when_config_save_fails(client, monkeypatch):
    def fail_save(_cfg):
        raise RuntimeError("simulated config write failure")

    monkeypatch.setattr("extensions.model_endpoints.credential_routes.save_config", fail_save)
    with pytest.raises(RuntimeError, match="config write failure"):
        await client[0].post(
            "/api/settings/model-profiles",
            json={
                "profile_id": "orphan-api",
                "display_name": "Orphan API",
                "type": "openai_compatible",
                "base_url": "https://orphan.example/v1",
                "model": "test-model",
                "api_key": "must-be-rolled-back",
            },
        )

    assert get_secret("model.profile.orphan-api.api_key") is None


@pytest.mark.anyio
async def test_profile_update_restores_previous_secret_when_config_save_fails(client, monkeypatch):
    await client[0].post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "stable-api",
            "display_name": "Stable API",
            "type": "openai_compatible",
            "base_url": "https://stable.example/v1",
            "model": "test-model",
            "api_key": "previous-secret",
        },
    )

    def fail_save(_cfg):
        raise RuntimeError("simulated config write failure")

    monkeypatch.setattr("extensions.model_endpoints.credential_routes.save_config", fail_save)
    with pytest.raises(RuntimeError, match="config write failure"):
        await client[0].patch(
            "/api/settings/model-profiles/stable-api",
            json={"api_key": "replacement-secret"},
        )

    assert get_secret("model.profile.stable-api.api_key") == "previous-secret"


@pytest.mark.anyio
async def test_multiple_api_profiles_of_same_type_keep_independent_secrets(client):
    http, _ = client
    first_secret = "sk-first-profile-1111"
    second_secret = "sk-second-profile-2222"

    first = await http.post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "primary-api",
            "display_name": "Primary API",
            "type": "openai_compatible",
            "base_url": "https://primary.example/v1",
            "model": "primary-model",
            "api_key": first_secret,
        },
    )
    second = await http.post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "backup-api",
            "display_name": "Backup API",
            "type": "openai_compatible",
            "base_url": "https://backup.example/v1",
            "model": "backup-model",
            "api_key": second_secret,
        },
    )

    assert first.status_code == 201
    assert second.status_code == 201
    data = (await http.get("/api/model")).json()
    assert set(data["providers"]) == {"primary-api", "backup-api"}
    assert data["providers"]["primary-api"]["type"] == "openai_compatible"
    assert data["providers"]["primary-api"]["display_name"] == "Primary API"
    assert data["providers"]["primary-api"]["kind"] == "api"
    assert not data["providers"]["primary-api"]["enabled"]
    assert not data["providers"]["backup-api"]["enabled"]
    assert data["default_provider"] == ""
    assert data["providers"]["primary-api"]["api_key"] == {
        "configured": True,
        "last4": "1111",
    }
    assert data["providers"]["backup-api"]["api_key"] == {
        "configured": True,
        "last4": "2222",
    }
    assert first_secret not in str(data)
    assert second_secret not in str(data)


@pytest.mark.anyio
async def test_custom_profile_inference_limits_use_canonical_update_without_reverification(client, monkeypatch):
    from aletheia.config.loader import load_config

    await client[0].post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "limited-api",
            "display_name": "Limited API",
            "type": "openai_compatible",
            "base_url": "https://limited.example/v1",
            "model": "test-model",
            "api_key": "profile-secret",
        },
    )

    async def must_not_verify(_provider):
        raise AssertionError("admission-only updates must not call the provider")

    monkeypatch.setattr(
        "extensions.model_endpoints.openai_compatible.OpenAICompatibleProvider.healthcheck",
        must_not_verify,
    )
    response = await client[0].patch(
        "/api/settings/model-profiles/limited-api",
        json={
            "inference_max_concurrency": 4,
            "inference_requests_per_minute": 90,
            "inference_tpm": 240000,
        },
    )

    assert response.status_code == 200
    profile = load_config().models.providers["limited-api"]
    assert profile.inference_max_concurrency == 4
    assert profile.inference_requests_per_minute == 90
    assert profile.inference_tokens_per_minute == 240000


@pytest.mark.anyio
async def test_role_assignment_does_not_enable_profile(client):
    http, _ = client
    await http.post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "configured-off",
            "display_name": "Configured but off",
            "type": "openai_compatible",
            "base_url": "https://example.test/v1",
            "model": "test-model",
        },
    )

    route = await http.post(
        "/api/settings/model-routing",
        json={
            "default_provider": "configured-off",
            "deep": {"endpoint_id": "configured-off", "model_id": "test-model"},
            "fast": None,
        },
    )

    assert route.status_code == 200
    model = (await http.get("/api/model")).json()
    assert not model["providers"]["configured-off"]["enabled"]
    assert model["default_provider"] == "configured-off"
    assert model["deep"] == {"endpoint_id": "configured-off", "model_id": "test-model"}
    assert model["fast"] is None
    with pytest.raises(ValueError, match="disabled"):
        get_provider("configured-off")


@pytest.mark.anyio
async def test_assigned_profile_can_be_disabled_without_losing_role(client, monkeypatch):
    http, _ = client
    await http.post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "switchable-api",
            "display_name": "Switchable",
            "type": "openai_compatible",
            "base_url": "https://example.test/v1",
            "model": "test-model",
            "enabled": True,
        },
    )
    await http.post(
        "/api/settings/model-routing",
        json={
            "default_provider": "switchable-api",
        },
    )

    async def must_not_verify(_provider):
        raise AssertionError("disabling an unavailable profile must not verify it")

    monkeypatch.setattr(
        "extensions.model_endpoints.openai_compatible.OpenAICompatibleProvider.healthcheck",
        must_not_verify,
    )
    response = await http.patch(
        "/api/settings/model-profiles/switchable-api",
        json={
            "enabled": False,
        },
    )

    assert response.status_code == 200
    model = (await http.get("/api/model")).json()
    assert model["default_provider"] == "switchable-api"
    assert not model["providers"]["switchable-api"]["enabled"]
    with pytest.raises(ValueError, match="disabled"):
        get_provider()


@pytest.mark.anyio
async def test_confirmed_delete_clears_roles_and_provider_owned_secret(client):
    http, _ = client
    secret = "sk-delete-me-9876"
    await http.post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "active-api",
            "display_name": "Active API",
            "type": "anthropic_compatible",
            "base_url": "https://anthropic.example",
            "model": "active-model",
            "enabled": True,
            "api_key": secret,
        },
    )
    await http.post(
        "/api/settings/model-routing",
        json={
            "default_provider": "active-api",
            "deep": {"endpoint_id": "active-api", "model_id": "active-model"},
            "fast": {"endpoint_id": "active-api", "model_id": "active-model"},
        },
    )
    secret_ref = "model.profile.active-api.api_key"
    assert get_secret(secret_ref) == secret

    rejected = await http.delete("/api/settings/model-profiles/active-api")
    deleted = await http.delete(
        "/api/settings/model-profiles/active-api",
        params={"confirm": "true"},
    )

    assert rejected.status_code == 409
    assert deleted.status_code == 200
    assert deleted.json()["cleared_roles"] == ["default", "deep", "fast"]
    model = (await http.get("/api/model")).json()
    assert model["providers"] == {}
    assert model["default_provider"] == ""
    assert model["deep"] is None
    assert model["fast"] is None
    assert get_secret(secret_ref) is None
    assert secret not in deleted.text


@pytest.mark.anyio
async def test_delete_failure_restores_prior_secret_and_keeps_profile(client, monkeypatch):
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.secrets import set_secret

    http, _ = client
    await http.post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "retry-delete-api",
            "display_name": "Retry Delete API",
            "type": "openai_compatible",
            "base_url": "https://delete.example/v1",
            "model": "delete-model",
            "api_key": "primary-secret",
        },
    )
    cfg = load_config()
    profile = cfg.models.providers["retry-delete-api"]
    profile.oauth_token_ref = "model.profile.retry-delete-api.oauth_token"
    save_config(cfg)
    set_secret(profile.oauth_token_ref, "secondary-secret")

    def fail_save(_cfg):
        raise RuntimeError("simulated configuration-store failure")

    monkeypatch.setattr("extensions.model_endpoints.credential_routes.save_config", fail_save)
    with pytest.raises(RuntimeError, match="configuration-store failure"):
        await http.delete(
            "/api/settings/model-profiles/retry-delete-api",
            params={"confirm": "true"},
        )

    assert "retry-delete-api" in load_config().models.providers
    assert get_secret("model.profile.retry-delete-api.api_key") == "primary-secret"
    assert get_secret("model.profile.retry-delete-api.oauth_token") == "secondary-secret"


@pytest.mark.anyio
async def test_candidate_probe_resolves_adapter_from_profile_identity(client, monkeypatch):
    http, _ = client
    await http.post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "probe-api",
            "display_name": "Probe API",
            "type": "openai_compatible",
            "base_url": "https://probe.example/v1",
            "model": "probe-model",
            "api_key": "probe-placeholder",
        },
    )

    async def available(provider):
        from aletheia.model_provider.base import ModelHealth

        return ModelHealth(provider=provider.name, available=True, model="probe-model")

    monkeypatch.setattr(
        "extensions.model_endpoints.openai_compatible.OpenAICompatibleProvider.healthcheck",
        available,
    )
    response = await http.post(
        "/api/model/test-candidate",
        json={
            "provider": "probe-api",
            "model": "probe-model",
        },
    )

    assert response.status_code == 200
    assert response.json()["available"]


@pytest.mark.anyio
async def test_retired_singleton_integration_has_no_mutation_endpoint(client):
    http, _ = client

    response = await http.patch(
        "/api/settings/model-integrations/openai_native",
        json={
            "enabled": True,
            "model": "gpt-5.5",
            "inference_max_concurrency": 3,
        },
    )

    assert response.status_code == 404
    model = (await http.get("/api/model")).json()
    assert model["providers"] == {}


@pytest.mark.anyio
@pytest.mark.parametrize("provider_type, override", [
    ("openai_compatible", {"base_url": "https://different.example/v1"}),
    ("anthropic_compatible", {"base_url": "https://different.example/v1"}),
    ("openai_compatible", {"base_url": "https://configured.example/another-tenant"}),
    ("openai_compatible", {"protocol": "responses"}),
    ("anthropic_compatible", {"auth_scheme": "bearer"}),
])
@pytest.mark.parametrize("operation", ["detect-models", "test-candidate", "update-profile"])
async def test_candidate_cannot_send_stored_key_to_changed_endpoint(client, monkeypatch, provider_type, override, operation):
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig

    http, _ = client
    cfg = load_config()
    cfg.models.providers["scoped"] = ProviderConfig(
        type=provider_type, base_url="https://configured.example/v1", model="configured",
        api_key_secret_ref="model.profile.scoped.api_key",
    )
    save_config(cfg)
    before = load_config().models.model_dump()
    reads = []
    probes = []

    def secret(ref):
        reads.append(ref)
        return "synthetic-configured-key"

    async def catalogue(provider):
        probes.append(provider.base_url)
        return []

    monkeypatch.setattr("aletheia.config.secrets.get_secret", secret)
    monkeypatch.setattr("extensions.model_endpoints.http_runtime.get_secret", secret)
    monkeypatch.setattr("extensions.model_endpoints.credential_routes.get_secret", secret)
    monkeypatch.setattr(f"extensions.model_endpoints.{provider_type}." + (
        "OpenAICompatibleProvider" if provider_type == "openai_compatible" else "AnthropicCompatibleProvider"
    ) + ".list_models", catalogue)
    payload = override
    if operation == "update-profile":
        response = await http.patch("/api/settings/model-profiles/scoped", json=payload)
    else:
        response = await http.post(f"/api/model/{operation}", json={"provider": "scoped", **payload})
    assert response.status_code == 422
    assert response.json()["detail"] == "candidate_credentials_required_for_changed_endpoint"
    assert reads == probes == []
    assert load_config().models.model_dump() == before


@pytest.mark.anyio
async def test_profile_ids_are_validated_and_cannot_collide(client):
    http, _ = client

    invalid = await http.post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "Not valid!",
            "display_name": "Invalid",
            "type": "openai_compatible",
        },
    )
    created = await http.post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "same-id",
            "display_name": "First",
            "type": "openai_compatible",
        },
    )
    duplicate = await http.post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "same-id",
            "display_name": "Second",
            "type": "openai_compatible",
        },
    )
    singleton_type = await http.post(
        "/api/settings/model-profiles",
        json={
            "profile_id": "codex-copy",
            "display_name": "Codex copy",
            "type": "codex_cli",
        },
    )

    assert invalid.status_code == 422
    assert created.status_code == 201
    assert duplicate.status_code == 409
    assert singleton_type.status_code == 422


@pytest.mark.anyio
async def test_legacy_model_mutation_is_absent_and_cannot_write(client, monkeypatch):
    from aletheia.config.loader import load_config
    from extensions.model_endpoints import credential_routes as model_routes

    http, _ = client
    before = load_config().models.model_dump()
    writes = []
    monkeypatch.setattr(model_routes, "set_secret", lambda *args: writes.append(args))

    response = await http.post(
        "/api/settings/model",
        json={
            "provider": "openai_compatible",
            "model": "legacy-model",
            "api_key": "synthetic-retired-route-key",
            "default": True,
            "set_as_deep": True,
            "set_as_fast": True,
        },
    )

    assert response.status_code == 404
    assert load_config().models.model_dump() == before
    assert writes == []
