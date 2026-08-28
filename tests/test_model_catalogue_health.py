"""Selected model health must reflect the current provider catalogue."""

import pytest

from aletheia.model_provider.base import ModelHealth
from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider
from aletheia.model_provider.anthropic_native import AnthropicNativeProvider
from aletheia.model_provider.databricks import DatabricksProvider
from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
from aletheia.model_provider.openai_native import OpenAINativeProvider
from aletheia.model_provider import router


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider",
    [
        OpenAICompatibleProvider(base_url="http://gateway", model="removed-model"),
        AnthropicCompatibleProvider(base_url="http://gateway", model="removed-model"),
        OpenAINativeProvider(model="removed-model"),
        AnthropicNativeProvider(model="removed-model"),
        DatabricksProvider(
            workspace_url="https://adb-123.azuredatabricks.net",
            model="removed-model",
            protocol="responses",
        ),
    ],
    ids=["openai-compatible", "anthropic-compatible", "openai-native", "anthropic-native", "databricks"],
)
async def test_missing_selected_model_is_unavailable(provider, monkeypatch):
    async def catalogue():
        return [{"slug": "current-model", "display_name": "current-model"}]

    monkeypatch.setattr(provider, "list_models", catalogue)
    health = await provider.healthcheck()

    assert health.available is False
    assert health.reason_code == "configured_model_not_found"
    assert health.model == "removed-model"
    assert health.models == ["current-model"]


@pytest.mark.anyio
async def test_compatible_default_alias_remains_explicitly_supported(monkeypatch):
    provider = OpenAICompatibleProvider(base_url="http://gateway", model="default")

    async def catalogue():
        return [{"slug": "current-model", "display_name": "current-model"}]

    monkeypatch.setattr(provider, "list_models", catalogue)
    health = await provider.healthcheck()

    assert health.available is True
    assert health.model == "default"


def test_fresh_catalogue_mismatch_survives_historical_verification(monkeypatch):
    monkeypatch.setattr(
        router,
        "provider_verification_snapshot",
        lambda *_args: {
            "verified": True,
            "verified_at": "2026-08-20T00:00:00Z",
            "verification_identity": "old",
            "verification_source": "model_ping",
            "verification_invalidated": False,
            "verification_detail": None,
        },
    )
    cfg = type("Config", (), {
        "models": type("Models", (), {
            "default_provider": "openai_compatible",
            "deep_provider": None,
            "fast_provider": None,
        })(),
    })()
    pcfg = type("ProviderConfig", (), {"enabled": True})()
    health = router._decorate_health(
        "openai_compatible",
        ModelHealth(
            provider="openai_compatible",
            available=False,
            model="removed-model",
            models=["current-model"],
            error="configured model not found in current catalogue",
            reason_code="configured_model_not_found",
            observed_at="2026-08-28T00:00:00Z",
        ),
        cfg,
        pcfg,
        configured=True,
    )

    assert health.state == "unavailable"
    assert health.reason_code == "configured_model_not_found"
    assert health.verified is True
