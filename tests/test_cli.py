"""Tests for CLI basic functionality."""

from typer.testing import CliRunner

from aletheia.main import app

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Aletheia Trading OS" in result.output


def test_version():
    from aletheia import __version__
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"v{__version__}" in result.output


def test_gateway_status(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    result = runner.invoke(app, ["gateway", "status"])
    assert result.exit_code == 0
    assert "stopped" in result.output


def test_gateway_status_shows_autostart(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.gateway import autostart
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(autostart, "status",
                        lambda: {"supported": True, "enabled": False, "task_name": autostart.TASK_NAME})
    result = runner.invoke(app, ["gateway", "status"])
    assert result.exit_code == 0
    assert "Autostart:" in result.output
    assert "disabled" in result.output


def test_gateway_enable_disable_autostart_smoke(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.gateway import autostart
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(autostart, "enable",
                        lambda source="cli": {"supported": True, "enabled": True, "ok": True})
    monkeypatch.setattr(autostart, "disable",
                        lambda source="cli": {"supported": True, "enabled": False, "ok": True})
    r1 = runner.invoke(app, ["gateway", "enable-autostart"])
    assert r1.exit_code == 0
    assert "Autostart enabled" in r1.output
    r2 = runner.invoke(app, ["gateway", "disable-autostart"])
    assert r2.exit_code == 0
    assert "Autostart disabled" in r2.output


def test_permission_status(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    result = runner.invoke(app, ["permission", "status"])
    assert result.exit_code == 0
    assert "Research" in result.output


def test_strategy_list_and_status(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db
    from aletheia.trading.strategy_lifecycle import create_strategy

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()

    strategy = create_strategy("CLI Strategy", "CLI description")
    result = runner.invoke(app, ["strategy", "list"])
    assert result.exit_code == 0
    assert strategy.id in result.output
    assert "CLI Strategy" in result.output

    result = runner.invoke(app, ["strategy", "status", strategy.id])
    assert result.exit_code == 0
    assert "CLI description" in result.output
    assert "Promotion gates:" in result.output
    reset_db()
