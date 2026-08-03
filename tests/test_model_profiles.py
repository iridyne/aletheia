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

    from aletheia.model_provider.base import ModelHealth, ModelResponse
    from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
    from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider

    async def healthy(provider):
        return ModelHealth(provider=provider.name, available=True, model=provider.model, models=[provider.model])

    async def complete(_provider_name, provider, _request, **_kwargs):
        return ModelResponse(content="OK", model=provider.model, usage={})

    monkeypatch.setattr(OpenAICompatibleProvider, "healthcheck", healthy)
    monkeypatch.setattr(AnthropicCompatibleProvider, "healthcheck", healthy)
    monkeypatch.setattr("aletheia.model_provider.broker.brokered_complete", complete)
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
async def test_clean_model_state_has_no_enabled_provider_or_routing_role(client):
    http, _ = client

    response = await http.get("/api/model")

    assert response.status_code == 200
    assert response.json() == {
        "default_provider": "",
        "deep_provider": None,
        "fast_provider": None,
        "providers": {},
    }


@pytest.mark.anyio
async def test_failed_profile_verification_does_not_persist_config_or_secret(client, monkeypatch):
    from aletheia.model_provider.base import ModelHealth
    from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider

    async def unavailable(provider):
        return ModelHealth(provider=provider.name, available=False, error="unauthorized")

    monkeypatch.setattr(OpenAICompatibleProvider, "healthcheck", unavailable)
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
async def test_profile_create_rolls_back_secret_when_config_save_fails(client, monkeypatch):
    def fail_save(_cfg):
        raise RuntimeError("simulated config write failure")

    monkeypatch.setattr("aletheia.server.routes.model.save_config", fail_save)
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

    monkeypatch.setattr("aletheia.server.routes.model.save_config", fail_save)
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
        "aletheia.model_provider.openai_compatible.OpenAICompatibleProvider.healthcheck",
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
            "deep_provider": "configured-off",
            "fast_provider": None,
        },
    )

    assert route.status_code == 200
    model = (await http.get("/api/model")).json()
    assert not model["providers"]["configured-off"]["enabled"]
    assert model["default_provider"] == "configured-off"
    assert model["deep_provider"] == "configured-off"
    assert isinstance(model["fast_provider"], type(None))
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
        "aletheia.model_provider.openai_compatible.OpenAICompatibleProvider.healthcheck",
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
            "deep_provider": "active-api",
            "fast_provider": "active-api",
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
    assert model["deep_provider"] is None
    assert model["fast_provider"] is None
    assert get_secret(secret_ref) is None
    assert secret not in deleted.text


@pytest.mark.anyio
async def test_delete_failure_restores_prior_secret_and_keeps_profile(client, monkeypatch):
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.secrets import delete_secret as real_delete_secret, set_secret

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

    calls = 0

    def fail_second_delete(ref):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated second-store failure")
        real_delete_secret(ref)

    monkeypatch.setattr("aletheia.server.routes.model.delete_secret", fail_second_delete)
    with pytest.raises(RuntimeError, match="second-store failure"):
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
        "aletheia.model_provider.openai_compatible.OpenAICompatibleProvider.healthcheck",
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
async def test_singleton_integration_update_uses_canonical_endpoint(client):
    http, _ = client

    response = await http.patch(
        "/api/settings/model-integrations/codex_cli",
        json={
            "enabled": True,
            "codex_model": "gpt-5.5",
            "inference_max_concurrency": 3,
        },
    )

    assert response.status_code == 200
    model = (await http.get("/api/model")).json()
    assert model["providers"]["codex_cli"]["kind"] == "cli"
    assert model["providers"]["codex_cli"]["enabled"]
    assert model["providers"]["codex_cli"]["codex_model"] == "gpt-5.5"
    assert model["providers"]["codex_cli"]["inference_max_concurrency"] == 3


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
async def test_legacy_model_mutation_declares_deprecation(client):
    http, _ = client

    response = await http.post(
        "/api/settings/model",
        json={
            "provider": "openai_compatible",
            "model": "legacy-model",
        },
    )

    assert response.status_code == 200
    assert response.headers["Deprecation"] == "true"
    assert "model-provider-profiles-and-access-methods" in response.headers["Link"]
