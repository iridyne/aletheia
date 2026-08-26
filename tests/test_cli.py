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


def test_gateway_status(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    result = runner.invoke(app, ["gateway", "status"])
    assert result.exit_code == 0
    assert "stopped" in result.output


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


def test_inspect_pnl_labels_today_sample_as_bounded(monkeypatch):
    import json as json_mod
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from aletheia.analytics import execution_mode, outcome_integrity, trade_stats
    from aletheia.maintenance import historical_accounting
    from aletheia.portfolio import pnl as portfolio_pnl

    today = datetime.now(timezone.utc).date().isoformat()
    rows = [
        {
            "symbol": f"SYM{i}/USDT",
            "direction": "long",
            "realized_pnl": -100.0 if i == 0 else 1.0,
            "close_reason": "strategy_exit",
            "closed_at": f"{today}T00:{i % 60:02d}:00+00:00",
        }
        for i in range(201)
    ]

    monkeypatch.setattr(execution_mode, "resolve_selected_execution_mode", lambda: "paper_internal")
    monkeypatch.setattr(outcome_integrity, "raw_account_events", lambda **_kwargs: rows)
    monkeypatch.setattr(
        outcome_integrity,
        "assess_reportable_outcome",
        lambda _row: SimpleNamespace(eligible=True),
    )
    monkeypatch.setattr(
        trade_stats,
        "compute_rolling_trade_stats",
        lambda **_kwargs: {
            "total_pnl": None,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "avg_pnl": 0,
            "best_win": 0,
            "worst_loss": 0,
            "per_symbol": [],
        },
    )
    monkeypatch.setattr(portfolio_pnl, "mark_positions_to_market", lambda **_kwargs: [])
    monkeypatch.setattr(
        historical_accounting,
        "historical_accounting_summary",
        lambda **_kwargs: {
            "historical_reconciled_pnl": None,
            "historical_reconciled_count": 0,
            "historical_reconciliation_unavailable_count": 0,
        },
    )
    monkeypatch.setattr(
        outcome_integrity,
        "build_outcome_cohort_summary",
        lambda **_kwargs: {
            "raw_event_count": 0,
            "eligible_outcome_count": 0,
            "excluded_outcome_count": 0,
            "outcome_exclusion_reasons": {},
            "eligible_realized_pnl": None,
        },
    )

    result = runner.invoke(app, ["inspect", "pnl", "--json"])

    assert result.exit_code == 0
    today_payload = json_mod.loads(result.output)["today"]
    assert today_payload["realized"] == 100.0
    assert today_payload["eligible_count"] == 201
    assert today_payload["shown_count"] == 200
    assert today_payload["omitted_count"] == 1
    assert today_payload["cohort_complete"] is True
    assert today_payload["loaded_from"] == rows[0]["closed_at"]
    assert today_payload["loaded_through"] == rows[-1]["closed_at"]
    assert len(today_payload["trades"]) == 200
    assert sum(row["pnl"] for row in today_payload["trades"]) == 200.0

    human = runner.invoke(app, ["inspect", "pnl"])

    assert human.exit_code == 0
    assert "201 verified outcomes; showing newest 200, 1 omitted" in human.output
    assert "(200 verified outcomes)" not in human.output


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


def test_inspect_opportunity_lifecycle_unknown_has_stable_unavailable_envelope(cli_db):
    import json as json_mod

    args = [
        "inspect",
        "opportunity-lifecycle",
        "--id",
        "does-not-exist",
        "--as-of",
        "2026-08-13T00:10:00Z",
        "--json",
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    data = json_mod.loads(result.output)
    assert data["contract_version"] == "opportunity_lifecycle/v1"
    assert data["availability"] == "unavailable"
    assert data["unavailable_reason"] == "opportunity_not_found"
    assert data["lifecycle"]["positions"] == []

    human = runner.invoke(app, [arg for arg in args if arg != "--json"])
    assert human.exit_code == 0, human.output
    assert "Unavailable reason: opportunity_not_found" in human.output
    assert "Linkage and evidence limits" in human.output


def test_inspect_opportunity_lifecycle_rejects_invalid_as_of(cli_db):
    result = runner.invoke(
        app,
        ["inspect", "opportunity-lifecycle", "--id", "opp-1", "--as-of", "not-a-time"],
    )
    assert result.exit_code == 2
    assert "invalid timestamp; use ISO-8601" in result.output
