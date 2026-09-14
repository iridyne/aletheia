"""Tests for the secrets CLI (real implementation, not stubs)."""

import pytest
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


@pytest.mark.parametrize("args", [["list"], ["test", "bitget"], ["test", "binance"]])
def test_retired_secret_reads_have_no_authority(monkeypatch, args):
    def forbidden(*_args, **_kwargs):
        pytest.fail("retired command must not read credentials or probe accounts")

    monkeypatch.setattr("aletheia.config.secrets.get_secret", forbidden)
    monkeypatch.setattr("aletheia.uta.manager.probe_account_reachable", forbidden)
    result = runner.invoke(app, ["secrets", *args])
    assert result.exit_code == 2
    assert "No such command" in result.output
