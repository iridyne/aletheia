"""Retired model configurations have no execution or credential authority."""

import pytest

from aletheia.config.schema import AletheiaConfig, ProviderConfig
from aletheia.model_provider import router


def test_historical_workspace_cannot_configure_an_http_endpoint():
    profile = ProviderConfig(
        type="openai_compatible", enabled=True, workspace_url="https://retired-workspace.invalid",
    )
    assert not router.provider_is_configured("http", profile)


@pytest.mark.parametrize("provider_type", ["codex_cli", "claude_cli", "pi_cli", "openai_native", "anthropic_native", "databricks"])
@pytest.mark.parametrize("name", ["historical-cli", "openai_compatible"])
@pytest.mark.anyio
async def test_retired_model_type_cannot_probe_or_resolve_credentials(
    monkeypatch, provider_type, name
):
    cfg = AletheiaConfig()
    pcfg = ProviderConfig(
        type=provider_type,
        enabled=True,
        api_key_secret_ref="historical-reference",
        base_url="https://must-not-rebind.invalid",
    )
    cfg.models.providers[name] = pcfg
    cfg.models.default_provider = name
    monkeypatch.setattr(router, "load_config", lambda: cfg)

    def forbidden(*_args, **_kwargs):
        pytest.fail("retired configuration must not resolve credentials")

    monkeypatch.setattr("aletheia.model_provider.http_runtime.get_secret", forbidden)
    router.clear_cache()
    try:
        assert not router.provider_is_configured(name, pcfg)
        for get_provider in (router.get_provider, router.get_provider_async):
            with pytest.raises(router.ProviderNotConfigured):
                result = get_provider(name)
                if get_provider is router.get_provider_async:
                    await result
        snapshot = router.provider_verification_snapshot(name, pcfg)
        assert snapshot["verified"] is None
        health = await router.healthcheck_all(force=True)
        row = next(item for item in health if item.provider == name)
        assert not row.configured
        assert not row.available
        assert row.verified is None
    finally:
        router.clear_cache()


@pytest.mark.parametrize("name", ["codex_cli", "claude_cli", "pi_cli", "openai_compatible"])
@pytest.mark.parametrize("provider_type", ["codex_cli", "openai_native", "anthropic_native", "databricks"])
def test_operator_cli_cannot_reactivate_a_retired_transport(monkeypatch, name, provider_type):
    from typer.testing import CliRunner
    from aletheia.cli.model import model_app
    import aletheia.config.loader as loader

    cfg = AletheiaConfig()
    cfg.models.providers[name] = ProviderConfig(type=provider_type, enabled=False)
    monkeypatch.setattr(loader, "load_config", lambda: cfg)

    def forbidden(*_args, **_kwargs):
        pytest.fail("retired model cannot prompt for credentials or save configuration")

    monkeypatch.setattr(loader, "save_config", forbidden)
    monkeypatch.setattr("typer.prompt", forbidden)
    result = CliRunner().invoke(model_app, ["set", "--provider", name, "--api-key"])
    assert result.exit_code == 1
    assert not cfg.models.providers[name].enabled
