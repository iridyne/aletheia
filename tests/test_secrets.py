"""Tests for secrets handling and redaction."""

import os
import stat
from unittest.mock import patch
import pytest  # pyright: ignore[reportMissingImports]
from aletheia.config.secrets import (
    delete_secret,
    get_credential_generation,
    get_secret,
    is_secret_key,
    redact,
    set_secret,
)


@pytest.mark.parametrize(
    "key",
    (
        "ApiKey",
        "apiKey",
        "APIKey",
        "api_key",
        "api-key",
        "PrivateKey",
        "privateKey",
        "private_key",
        "token",
        "passphrase",
    ),
)
def test_secret_key_classifier_normalizes_case_and_separators(key: str) -> None:
    assert is_secret_key(key)


@pytest.mark.parametrize("key", ("key", "monkey", "keyboard", "key_name"))
def test_secret_key_classifier_does_not_match_key_alone(key: str) -> None:
    assert not is_secret_key(key)


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


def test_secret_files_are_owner_only(tmp_path):
    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=None):
        set_secret("test.key", "super_secret_value")

    assert stat.S_IMODE((tmp_path / ".secret.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "secrets.enc").stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX umask semantics")
def test_secret_files_ignore_permissive_umask(tmp_path):
    previous_umask = os.umask(0o022)
    try:
        with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
             patch("aletheia.config.secrets._get_keyring", return_value=None):
            set_secret("test.key", "super_secret_value")
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE((tmp_path / ".secret.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "secrets.enc").stat().st_mode) == 0o600


def test_existing_secret_files_are_tightened_before_read(tmp_path):
    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=None):
        set_secret("test.key", "super_secret_value")
        os.chmod(tmp_path / ".secret.key", 0o644)
        os.chmod(tmp_path / "secrets.enc", 0o644)
        assert get_secret("test.key") == "super_secret_value"

    assert stat.S_IMODE((tmp_path / ".secret.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "secrets.enc").stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink ownership semantics")
def test_secret_key_symlink_is_rejected(tmp_path):
    target = tmp_path / "other.key"
    target.write_bytes(b"not-a-secret-key")
    (tmp_path / ".secret.key").symlink_to(target)

    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path):
        with pytest.raises(RuntimeError, match="must not be a symlink"):
            import aletheia.config.secrets as secrets

            secrets._get_fernet()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink ownership semantics")
def test_secret_store_symlink_is_rejected(tmp_path):
    target = tmp_path / "other.enc"
    target.write_bytes(b"not-a-secret-store")
    (tmp_path / "secrets.enc").symlink_to(target)

    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path):
        with pytest.raises(RuntimeError, match="must not be a symlink"):
            get_secret("test.key")


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership semantics")
def test_foreign_owner_secret_file_is_rejected(tmp_path):
    import aletheia.config.secrets as secrets

    key_path = tmp_path / ".secret.key"
    key_path.write_bytes(b"invalid")
    with (
        patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path),
        patch.object(secrets.os, "getuid", return_value=os.getuid() + 1),
    ):
        with pytest.raises(RuntimeError, match="unexpected owner"):
            secrets._get_fernet()


def test_exchange_credential_mutations_advance_isolated_generation(tmp_path):
    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=None):
        assert get_credential_generation("bitget.demo") == 0
        set_secret("bitget.demo.api_key", "first")
        assert get_credential_generation("bitget.demo") == 1
        set_secret("bitget.demo.api_key", "second")
        assert get_credential_generation("bitget.demo") == 2
        set_secret("okx.demo.api_key", "other")
        assert get_credential_generation("bitget.demo") == 2
        assert get_credential_generation("okx.demo") == 1
        delete_secret("bitget.demo.api_key")
        assert get_credential_generation("bitget.demo") == 3


def test_corrupt_credential_generation_fails_closed(tmp_path):
    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=None):
        set_secret("bitget.demo.api_key", "first")
        (tmp_path / "secrets.enc").write_bytes(b"corrupt")
        with pytest.raises(RuntimeError, match="credential generation"):
            get_credential_generation("bitget.demo")


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


def test_stale_keyring_cannot_override_generation_bound_file_value(tmp_path):
    class StaleKeyring:
        def get_password(self, _service, key):
            return "old-keyring-value" if key == "bitget.demo.api_key" else None

        def set_password(self, _service, _key, _value):
            raise RuntimeError("keyring write unavailable")

    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=StaleKeyring()):
        set_secret("bitget.demo.api_key", "new-file-value")

        assert get_secret("bitget.demo.api_key") == "new-file-value"
        assert get_credential_generation("bitget.demo") == 1


def test_keyring_only_reference_read_does_not_migrate_storage(tmp_path, monkeypatch):
    reads = []

    class Keyring:
        def get_password(self, _service, key):
            reads.append(key)
            return "synthetic-keyring-only-value"

    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: Keyring())
    assert get_secret("selected-reference") == "synthetic-keyring-only-value"
    assert reads == ["selected-reference"]
    assert not (tmp_path / "secrets.enc").exists()
    assert not (tmp_path / ".secret.key").exists()


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


def test_locked_keyring_prompt_cannot_hang_a_reader(tmp_path, monkeypatch):
    """A Secret Service collection that waits for an unlock prompt must read as unavailable, not block."""
    import threading
    import time

    from aletheia.config import secrets

    release = threading.Event()

    class PromptingKeyring:
        def get_password(self, _service, _key):
            release.wait(3)

    monkeypatch.setattr(secrets, "_KEYRING_TIMEOUT_SEC", 0.2)
    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=PromptingKeyring()):
        started = time.monotonic()
        assert get_secret("bitget.demo.api_key") is None
        assert get_secret("bitget.demo.api_key") is None  # second read must not queue behind the first
        with pytest.raises(RuntimeError, match="secret_store_unavailable"):
            get_secret("bitget.demo.api_key", strict=True)
        assert time.monotonic() - started < 2
    release.set()


def test_concurrent_reads_on_a_healthy_slow_keyring_are_not_reported_unavailable(tmp_path):
    """Two threads reading at once must both get the keyring's answer; only a call that really stalls is unavailable."""
    import threading
    import time

    results: list[object] = []

    class SlowKeyring:
        def get_password(self, _service, _key):
            time.sleep(0.05)
            return None

    def read():
        try:
            results.append(get_secret("bitget.demo.api_key", strict=True))
        except RuntimeError as exc:
            results.append(str(exc))

    with patch("aletheia.config.secrets.get_data_dir", return_value=tmp_path), \
         patch("aletheia.config.secrets._get_keyring", return_value=SlowKeyring()):
        threads = [threading.Thread(target=read) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
    assert results == [None, None]
