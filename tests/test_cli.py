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


def test_gateway_resume_blocks_when_llm_unavailable(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.agent_runtime import resume_gate

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    cfg = loader.load_config()
    cfg.runtime.paused = True
    loader.save_config(cfg)
    monkeypatch.setattr(
        resume_gate,
        "resume_if_llm_available_sync",
        lambda **kwargs: {
            "allowed": False,
            "reason": "llm_unavailable",
            "checked": 1,
            "results": [{"provider": "anthropic_compatible", "usable": False, "error": "429"}],
        },
    )

    result = runner.invoke(app, ["gateway", "resume"])

    assert result.exit_code == 1
    assert "llm_unavailable" in result.output
    assert loader.load_config().runtime.paused is True


def test_model_probe_reports_real_inference_result(monkeypatch):
    from aletheia.agent_runtime import startup_gate

    async def fake_probe(_cfg):
        return {
            "any_usable": True,
            "checked": 2,
            "results": [
                {"provider": "anthropic_compatible", "usable": True, "error": None},
                {"provider": "openai_compatible", "usable": False, "error": "down"},
            ],
        }

    monkeypatch.setattr(startup_gate, "probe_llm_providers_at_startup", fake_probe)

    result = runner.invoke(app, ["model", "probe"])

    assert result.exit_code == 0
    assert "anthropic_compatible" in result.output
    assert "usable" in result.output


def test_permission_status(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    result = runner.invoke(app, ["permission", "status"])
    assert result.exit_code == 0
    assert "Paper" in result.output


# ---------------------------------------------------------------------------
# inspect status / pnl / orders --stats
# ---------------------------------------------------------------------------


def test_inspect_status(tmp_path, monkeypatch):
    """inspect status runs without error and produces expected sections."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

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

    result = runner.invoke(app, ["inspect", "status"])
    assert result.exit_code == 0
    assert "=== Portfolio ===" in result.output
    assert "=== PnL" in result.output
    assert "=== Orders" in result.output
    assert "=== Data Quality ===" in result.output
    reset_db()


def test_inspect_pnl(tmp_path, monkeypatch):
    """inspect pnl runs without error."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

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

    result = runner.invoke(app, ["inspect", "pnl"])
    assert result.exit_code == 0
    assert "Today" in result.output
    assert "Rolling" in result.output
    reset_db()


def test_inspect_orders_stats(tmp_path, monkeypatch):
    """inspect orders --stats runs without error."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

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

    result = runner.invoke(app, ["inspect", "orders", "--stats"])
    assert result.exit_code == 0
    assert "Order Stats" in result.output
    assert "fill_rate" in result.output
    reset_db()


def test_inspect_status_json(tmp_path, monkeypatch):
    """inspect status --json outputs valid JSON."""
    import json as json_mod
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

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

    result = runner.invoke(app, ["inspect", "status", "--json"])
    assert result.exit_code == 0
    data = json_mod.loads(result.output)
    assert "portfolio" in data
    assert "pnl" in data
    assert "orders" in data
    reset_db()


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
