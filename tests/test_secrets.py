"""Tests for secrets handling and redaction."""

from unittest.mock import patch
import pytest  # pyright: ignore[reportMissingImports]
from aletheia.config.secrets import delete_secret, get_secret, redact, set_secret


def test_redact_log_text_covers_bare_secret_and_access_headers():
    """Regression #133: log-tail redaction must catch bare `secret` and ACCESS-KEY/ACCESS-SIGN headers."""
    from aletheia.gateway.logs import redact_log_text
    assert "SECRETVAL9" not in redact_log_text('{"secret":"SECRETVAL9"}')
    assert "SECRETVAL9" not in redact_log_text("secret=SECRETVAL9")
    assert "bgkey12345" not in redact_log_text("ACCESS-KEY: bgkey12345")
    assert "signxyz678" not in redact_log_text("ACCESS-SIGN: signxyz678")
    # Previously-covered forms must still be redacted.
    assert "passval42" not in redact_log_text("ACCESS-PASSPHRASE: passval42")
    assert "apival123" not in redact_log_text('{"apiKey":"apival123"}')
    # A non-secret word containing 'secret' as a value key is still masked; unrelated text survives.
    assert "hello world" in redact_log_text("hello world")


def test_redact_short():
    assert redact("abc") == "***"
    assert redact("") == "***"
    assert redact(None) == "***"


def test_redact_normal():
    # redact() shows only the last 4 chars behind a mask (no leading plaintext).
    r = redact("my_secret_key_123")
    assert r == "***_123"
    assert r.startswith("***")
    assert r.endswith("_123")
    assert "secret" not in r


def test_file_fallback_roundtrip(tmp_path):
    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=None):
        set_secret("test.key", "super_secret_value")
        val = get_secret("test.key")
        assert val == "super_secret_value"
        assert get_secret("nonexistent") is None


def test_empty_keyring_falls_through_to_authoritative_file_copy(tmp_path):
    class EmptyKeyring:
        def get_password(self, _service, _key):
            return None

    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=None):
        set_secret("model.profile.alpha.api_key", "file-only-secret")
    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=EmptyKeyring()):
        assert get_secret("model.profile.alpha.api_key") == "file-only-secret"


def test_preview_mode_never_reads_or_writes_secrets(monkeypatch):
    import aletheia.config.secrets as secrets

    monkeypatch.setattr(secrets, "is_preview_mode", lambda: True)
    monkeypatch.setattr(
        secrets,
        "_get_keyring",
        lambda: (_ for _ in ()).throw(AssertionError("preview must not read keyring")),
    )
    monkeypatch.setattr(
        secrets,
        "_file_get",
        lambda _key: (_ for _ in ()).throw(AssertionError("preview must not read file secrets")),
    )

    assert secrets.get_secret("bitget.api_key") is None
    with pytest.raises(secrets.PreviewSecretAccessError, match="preview mode"):
        secrets.set_secret("bitget.api_key", "not-a-real-secret")


def test_delete_secret_removes_encrypted_fallback(tmp_path):
    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=None):
        set_secret("model.profile.alpha.api_key", "profile-secret")
        delete_secret("model.profile.alpha.api_key")
        assert get_secret("model.profile.alpha.api_key") is None


def test_delete_secret_fails_closed_when_keyring_cannot_be_inspected(tmp_path):
    class BrokenKeyring:
        def get_password(self, _service, _key):
            raise RuntimeError("keyring unavailable")

    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=None):
        set_secret("model.profile.alpha.api_key", "profile-secret")
    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=BrokenKeyring()):
        with pytest.raises(RuntimeError, match="inspect secret"):
            delete_secret("model.profile.alpha.api_key")
    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=None):
        assert get_secret("model.profile.alpha.api_key") == "profile-secret"


def test_delete_secret_keeps_retryable_keyring_copy_on_delete_failure(tmp_path):
    class BrokenDeleteKeyring:
        def get_password(self, _service, _key):
            return "profile-secret"

        def delete_password(self, _service, _key):
            raise RuntimeError("delete unavailable")

    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=None):
        set_secret("model.profile.alpha.api_key", "profile-secret")
    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=BrokenDeleteKeyring()):
        with pytest.raises(RuntimeError, match="delete secret"):
            delete_secret("model.profile.alpha.api_key")
    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=None):
        assert get_secret("model.profile.alpha.api_key") is None


def test_preview_mode_blocks_secret_deletion(monkeypatch):
    import aletheia.config.secrets as secrets

    monkeypatch.setattr(secrets, "is_preview_mode", lambda: True)
    with pytest.raises(secrets.PreviewSecretAccessError, match="preview mode"):
        secrets.delete_secret("model.profile.alpha.api_key")
