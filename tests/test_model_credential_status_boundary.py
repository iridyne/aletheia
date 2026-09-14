"""Credential status crosses the Model boundary without exposing a raw value."""

import pytest


@pytest.mark.parametrize("store", ["corrupt", "invalid-json", "invalid-value", "missing"])
@pytest.mark.parametrize("keyring_state", ["absent", "failed", "present"])
def test_status_preserves_real_store_failures(monkeypatch, tmp_path, store, keyring_state):
    from aletheia.config import secrets
    from aletheia.config.schema import ProviderConfig
    from aletheia.model_provider.http_runtime import http_credential_status

    monkeypatch.setattr(secrets, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(secrets, "is_preview_mode", lambda: False)
    ref = "selected-reference"

    class Keyring:
        def get_password(self, service, key):
            assert key == ref
            if keyring_state == "failed":
                raise RuntimeError("synthetic-sensitive-detail")
            return "synthetic-1234" if keyring_state == "present" else None

    monkeypatch.setattr(secrets, "_get_keyring", lambda: Keyring())
    if store != "missing":
        content = b"corrupt"
        if store != "corrupt":
            content = secrets._get_fernet().encrypt(
                b"not-json" if store == "invalid-json" else b'{"selected-reference": 42}'
            )
        (tmp_path / "secrets.enc").write_bytes(content)
    cfg = ProviderConfig(type="openai_compatible", api_key_secret_ref=ref)
    status = http_credential_status("selected", cfg)
    if keyring_state == "present":
        assert status == {"configured": True, "last4": "1234"}
    elif store == "missing" and keyring_state == "absent":
        assert status == {"configured": False, "last4": None}
    else:
        assert status == {
            "configured": None, "last4": None,
            "unavailable_reason": "model_credentials_unavailable",
        }


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

    def secret(ref, *, strict):
        assert strict is True
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

    assert not hasattr(model, "get_secret")
    monkeypatch.setattr("aletheia.config.secrets.get_secret", forbidden)
    assert not hasattr(ui, "get_secret")
    assert model.get_model_config()["providers"]["selected"]["api_key"] == metadata
    assert ui._model_credential_configured(cfg) is True
    monkeypatch.setattr(router, "_provider_status_reader", None)
    status = model.get_model_config()["providers"]["selected"]["api_key"]
    assert status == {"configured": None, "last4": None, "unavailable_reason": "model_adapter_unavailable"}
    assert ui._model_credential_configured(cfg) is None
    cfg.models.providers.clear()
    assert ui._model_credential_configured(cfg) is False
