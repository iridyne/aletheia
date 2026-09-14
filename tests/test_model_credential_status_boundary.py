"""Credential status crosses the Model boundary without exposing a raw value."""

import pytest


@pytest.mark.parametrize("value,expected", [
    (None, {"configured": False, "last4": None}),
    ("synthetic-value-1234", {"configured": True, "last4": "1234"}),
    ("abc", {"configured": True, "last4": "****"}),
    (RuntimeError("synthetic-sensitive-detail"), {
        "configured": None, "last4": None, "unavailable_reason": "model_credentials_unavailable",
    }),
])
def test_http_status_resolves_only_its_own_reference(monkeypatch, value, expected):
    from aletheia.config.schema import ProviderConfig
    from aletheia.model_provider import http_runtime

    refs = []

    def secret(ref):
        refs.append(ref)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(http_runtime, "get_secret", secret)
    cfg = ProviderConfig(
        type="openai_compatible", api_key_secret_ref="selected-reference",
        oauth_token_ref="historical-must-not-read", oauth_refresh_ref="historical-refresh",
    )
    assert http_runtime.http_credential_status("selected", cfg) == expected
    assert refs == ["selected-reference"]
    cfg.type = "codex_cli"
    assert http_runtime.http_credential_status("selected", cfg) == {"configured": False, "last4": None}
    assert refs == ["selected-reference"]


def test_core_and_ui_project_only_sanitized_status_and_keep_absence_unknown(monkeypatch):
    from aletheia.config.schema import AletheiaConfig, ProviderConfig
    from aletheia.model_provider import router
    from aletheia.server.routes import model, ui

    cfg = AletheiaConfig()
    cfg.models.providers["selected"] = ProviderConfig(
        type="openai_compatible", api_key_secret_ref="selected-reference",
    )
    metadata = {"configured": True, "last4": "1234"}
    monkeypatch.setattr(router, "_provider_status_reader", lambda name, pcfg: metadata)
    monkeypatch.setattr(model, "load_config", lambda: cfg)

    def forbidden(*_args):
        pytest.fail("Core metadata read must not resolve raw credentials")

    monkeypatch.setattr(model, "get_secret", forbidden)
    assert not hasattr(ui, "get_secret")
    assert model.get_model_config()["providers"]["selected"]["api_key"] == metadata
    assert ui._model_credential_configured(cfg) is True
    monkeypatch.setattr(router, "_provider_status_reader", None)
    status = model.get_model_config()["providers"]["selected"]["api_key"]
    assert status == {"configured": None, "last4": None, "unavailable_reason": "model_adapter_unavailable"}
    assert ui._model_credential_configured(cfg) is None
    cfg.models.providers.clear()
    assert ui._model_credential_configured(cfg) is False
