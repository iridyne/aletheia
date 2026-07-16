"""Tests for CLI basic functionality."""
from typer.testing import CliRunner

from aletheia.main import app

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Aletheia Trading OS" in result.output
    assert "opportunity" in result.output
    assert "flow" not in result.output
    assert "alpha" not in result.output
    assert "funnel" not in result.output
    assert "sizing" not in result.output


def test_version():
    from aletheia import __version__
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"v{__version__}" in result.output


def test_agent_workflow_cli_is_registered():
    result = runner.invoke(app, ["agent", "workflow", "check", "--help"])

    assert result.exit_code == 0
    assert "Validate local Task Lite state" in result.output


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


def test_gateway_status_labels_local_paper_without_an_exchange(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    cfg = loader.load_config()
    cfg.exchange.name = "okx"  # Retained preference, not an active connection.
    cfg.exchange.paper_execution_mode = "paper_internal"
    loader.save_config(cfg)

    result = runner.invoke(app, ["gateway", "status"])

    assert result.exit_code == 0
    assert "Exchange: Local paper (no external exchange)" in result.output
    assert "Exchange: okx" not in result.output


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

import pytest


@pytest.fixture()
def cli_db(tmp_path, monkeypatch):
    """Set up isolated DB + config for CLI tests."""
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    yield tmp_path
    reset_db()


def test_inspect_status(cli_db):
    """inspect status runs without error and produces expected sections."""
    result = runner.invoke(app, ["inspect", "status"])
    assert result.exit_code == 0
    assert "=== Portfolio ===" in result.output
    assert "=== PnL" in result.output
    assert "=== Orders" in result.output
    assert "=== Data Quality ===" in result.output


def test_inspect_pnl(cli_db):
    """inspect pnl runs without error."""
    result = runner.invoke(app, ["inspect", "pnl"])
    assert result.exit_code == 0
    assert "Today" in result.output
    assert "Rolling" in result.output


def test_inspect_orders_stats(cli_db):
    """inspect orders --stats runs without error."""
    result = runner.invoke(app, ["inspect", "orders", "--stats"])
    assert result.exit_code == 0
    assert "Order Stats" in result.output
    assert "fill_rate" in result.output


def test_inspect_status_json(cli_db):
    """inspect status --json outputs valid JSON."""
    import json as json_mod

    result = runner.invoke(app, ["inspect", "status", "--json"])
    assert result.exit_code == 0
    data = json_mod.loads(result.output)
    assert "portfolio" in data
    assert "pnl" in data
    assert "orders" in data


def test_inspect_agent_runs_uses_current_agent_run_fields(cli_db):
    import json as json_mod

    from aletheia.storage.models import AgentRun
    from aletheia.storage.repositories import insert_agent_run

    insert_agent_run(
        AgentRun(
            agent_name="paper_trader",
            trigger_type="scheduled",
            permission_mode="Observe",
            status="failed",
            started_at="2026-01-01T00:00:00+00:00",
            ended_at="2026-01-01T00:01:00+00:00",
            errors='["provider timeout"]',
        )
    )

    result = runner.invoke(app, ["inspect", "agent-runs", "--json"])

    assert result.exit_code == 0
    rows = json_mod.loads(result.output)
    assert len(rows) == 1
    assert rows[0]["agent"] == "paper_trader"
    assert rows[0]["status"] == "failed"
    assert rows[0]["duration_s"] == 60.0
    assert rows[0]["error"] == "provider timeout"
