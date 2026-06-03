"""Tests for the secrets CLI (real implementation, not stubs)."""

from unittest.mock import patch
from typer.testing import CliRunner

from aletheia.main import app

runner = CliRunner()


def _isolate(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    # Force the encrypted-file fallback (no real keyring in tests)
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)


def test_secrets_set_stores_value(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    result = runner.invoke(app, ["secrets", "set", "bitget.demo.api_key"], input="my_key_value\nmy_key_value\n")
    assert result.exit_code == 0, result.output
    # Value stored & retrievable
    from aletheia.config.secrets import get_secret
    assert get_secret("bitget.demo.api_key") == "my_key_value"
    # Output is redacted — raw value never echoed
    assert "my_key_value" not in result.output


def test_secrets_list_shows_set_and_missing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from aletheia.config.secrets import set_secret
    set_secret("bitget.demo.api_key", "abcd1234")
    result = runner.invoke(app, ["secrets", "list"])
    assert result.exit_code == 0
    assert "SET" in result.output
    assert "bitget.demo.api_key" in result.output
    assert "MISSING" in result.output  # other keys not set
    # raw secret never printed
    assert "abcd1234" not in result.output


def test_secrets_test_unknown_service(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    result = runner.invoke(app, ["secrets", "test", "binance"])
    assert result.exit_code == 1
    assert "Unknown service" in result.output
