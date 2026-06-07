"""Tests for secrets handling and redaction."""

from unittest.mock import patch
from aletheia.config.secrets import redact, set_secret, get_secret


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
