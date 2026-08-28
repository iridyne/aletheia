"""Tests for CLI basic functionality."""
import pytest
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
    assert result.exit_code == 1
    assert "stopped" in result.output


def test_gateway_open_dashboard_reports_browser_launch_failure(tmp_path, monkeypatch):
    import webbrowser
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    launched = []
    monkeypatch.setattr(webbrowser, "open", lambda url: launched.append(url) or False)

    result = runner.invoke(app, ["gateway", "open-dashboard"])

    assert result.exit_code == 1
    assert launched == ["http://127.0.0.1:8787"]
    assert "Could not open a browser" in result.output
    assert "http://127.0.0.1:8787" in result.output


def test_gateway_open_dashboard_succeeds_when_browser_launches(tmp_path, monkeypatch):
    import webbrowser
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    launched = []
    monkeypatch.setattr(webbrowser, "open", lambda url: launched.append(url) or True)

    result = runner.invoke(app, ["gateway", "open-dashboard"])

    assert result.exit_code == 0
    assert launched == ["http://127.0.0.1:8787"]


def test_gateway_status_labels_local_paper_without_an_exchange(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    cfg = loader.load_config()
    cfg.exchange.name = "okx"  # Retained preference, not an active connection.
    cfg.exchange.paper_execution_mode = "paper_internal"
    loader.save_config(cfg)

    result = runner.invoke(app, ["gateway", "status"])

    assert result.exit_code == 1
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


@pytest.mark.parametrize(
    ("results", "exit_code"),
    [
        ([], 1),
        ([{"provider": "openai", "available": False, "state": "unavailable"}], 1),
        ([{"provider": "openai", "available": True, "state": "reachable"}], 0),
        (
            [
                {"provider": "openai", "available": True, "state": "reachable"},
                {"provider": "backup", "available": False, "state": "unavailable"},
            ],
            0,
        ),
    ],
)
def test_model_test_exit_reflects_aggregate_provider_health(monkeypatch, results, exit_code):
    from aletheia.model_provider.base import ModelHealth

    async def fake_healthcheck_all():
        return [ModelHealth(enabled=True, **result) for result in results]

    monkeypatch.setattr("aletheia.model_provider.router.healthcheck_all", fake_healthcheck_all)

    result = runner.invoke(app, ["model", "test"])

    assert result.exit_code == exit_code
    if exit_code:
        assert "No enabled model provider is reachable." in result.output


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


def test_inspect_filtered_proposals_apply_filter_before_limit(cli_db):
    import json as json_mod

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    repo.insert_trade_proposal(
        TradeProposal(
            id="old-pending-proposal",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            status="pending",
        )
    )
    for idx in range(25):
        repo.insert_trade_proposal(
            TradeProposal(
                id=f"new-approved-proposal-{idx}",
                symbol="ETH/USDT",
                market_type="spot",
                direction="short",
                status="approved",
            )
        )

    filtered = runner.invoke(app, ["inspect", "proposals", "--status", "pending", "--limit", "2", "--json"])
    unfiltered = runner.invoke(app, ["inspect", "proposals", "--status", "all", "--limit", "1", "--json"])

    assert filtered.exit_code == 0
    assert [row["id"] for row in json_mod.loads(filtered.output)] == ["old-pending-proposal"]
    assert json_mod.loads(unfiltered.output)[0]["id"].startswith("new-approved-proposal-")


def test_inspect_rejected_risk_applies_filter_before_limit(cli_db):
    import json as json_mod

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import RiskDecision

    repo.insert_risk_decision(RiskDecision(id="old-rejected-risk", proposal_id="proposal-old", approved=False))
    for idx in range(25):
        repo.insert_risk_decision(
            RiskDecision(id=f"new-approved-risk-{idx}", proposal_id=f"proposal-{idx}", approved=True)
        )

    result = runner.invoke(app, ["inspect", "risk", "--rejected-only", "--limit", "2", "--json"])

    assert result.exit_code == 0
    rows = json_mod.loads(result.output)
    assert [row["id"] for row in rows] == ["old-rejected-risk"]
    assert all(not row["approved"] for row in rows)


def test_inspect_filtered_orders_apply_filter_before_limit(cli_db):
    import json as json_mod

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    repo.insert_order(
        Order(
            id="old-failed-order",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=1,
            execution_mode="paper_internal",
            status="failed",
            source="adopted",
        )
    )
    for idx in range(25):
        repo.insert_order(
            Order(
                id=f"new-filled-order-{idx}",
                symbol="ETH/USDT",
                market_type="spot",
                direction="short",
                order_type="market",
                quantity=1,
                execution_mode="paper_internal",
                status="filled",
                source="adopted",
            )
        )

    result = runner.invoke(app, ["inspect", "orders", "--status", "failed", "--limit", "2", "--json"])

    assert result.exit_code == 0
    rows = json_mod.loads(result.output)
    assert [row["id"] for row in rows] == ["old-failed-order"]
    assert all(row["status"] == "failed" for row in rows)


def test_inspect_filtered_fills_apply_filter_before_limit(cli_db):
    import json as json_mod

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill

    repo.insert_fill(Fill(id="old-target-fill", order_id="target-order", fill_price=100, fill_quantity=1))
    for idx in range(25):
        repo.insert_fill(Fill(id=f"new-other-fill-{idx}", order_id=f"other-order-{idx}", fill_price=100, fill_quantity=1))

    result = runner.invoke(app, ["inspect", "fills", "--order-id", "target-order", "--limit", "2", "--json"])

    assert result.exit_code == 0
    rows = json_mod.loads(result.output)
    assert [row["id"] for row in rows] == ["old-target-fill"]
    assert all(row["order_id"] == "target-order" for row in rows)


def test_inspect_positions_uses_verified_realized_pnl_projection(cli_db):
    """Closed positions must not publish raw realized PnL as verified economics."""
    import json as json_mod

    from aletheia.storage.models import Position
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import insert_position

    common = {
        "market_type": "usdt_futures",
        "direction": "long",
        "quantity": 1.0,
        "entry_price": 100.0,
        "current_price": 110.0,
        "entry_fee": 0.0,
        "exit_fee": 0.0,
        "execution_mode": "paper_internal",
        "status": "closed",
        "closed_at": "2026-08-26T00:00:00+00:00",
    }
    insert_position(
        Position(
            **common,
            id="verified",
            symbol="BTC/USDT",
            realized_pnl=10.0,
            funding_cost=0.0,
            funding_source="local_simulation",
        )
    )
    insert_position(
        Position(
            **common,
            id="missing-funding",
            symbol="ETH/USDT",
            realized_pnl=10.0,
            funding_cost=None,
            funding_source="unavailable",
            outcome_exclusion_reason="incomplete_funding_economics",
        )
    )
    insert_position(
        Position(
            **common,
            id="mismatched-raw",
            symbol="SOL/USDT",
            realized_pnl=999.0,
            funding_cost=0.0,
            funding_source="local_simulation",
            outcome_exclusion_reason="",
        )
    )
    get_db().execute(
        """INSERT INTO historical_accounting_reconciliations
           (position_id,reconciliation_version,status,reconstructed_realized_pnl,
            funding_cost,method,reason,evidence_json,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            "mismatched-raw",
            "historical-local-paper-accounting-v1",
            "reconstructed",
            9.5,
            0.5,
            "deterministic_test",
            "legacy",
            "{}",
            "2026-08-26T00:00:00+00:00",
            "2026-08-26T00:00:00+00:00",
        ),
    )

    result = runner.invoke(
        app,
        ["inspect", "positions", "--status", "closed", "--execution-mode", "paper_internal", "--json"],
    )

    assert result.exit_code == 0, result.output
    rows = {row["id"]: row for row in json_mod.loads(result.output)}
    assert rows["verified"]["realized_pnl"] == 10.0
    assert rows["verified"]["realized_pnl_complete"] is True
    assert rows["missing-funding"]["realized_pnl"] is None
    assert rows["missing-funding"]["realized_pnl_complete"] is False
    assert (
        rows["missing-funding"]["realized_pnl_unavailable_reason"]
        == "incomplete_funding_economics"
    )
    assert rows["mismatched-raw"]["realized_pnl"] is None
    assert rows["mismatched-raw"]["realized_pnl_complete"] is False
    assert rows["mismatched-raw"]["historical_accounting_pnl"] == 9.5

    human = runner.invoke(app, ["inspect", "positions", "--status", "closed"])
    assert human.exit_code == 0, human.output
    assert "realized_pnl=999.0" not in human.output
    assert "historical_accounting_pnl=9.5" in human.output
    assert "realized_pnl_complete=False" in human.output


def test_inspect_status_json(cli_db):
    """inspect status --json outputs valid JSON."""
    import json as json_mod

    result = runner.invoke(app, ["inspect", "status", "--json"])
    assert result.exit_code == 0
    data = json_mod.loads(result.output)
    assert "portfolio" in data
    assert "pnl" in data
    assert "orders" in data


def test_inspect_status_json_reports_fresh_zero_exchange_account(cli_db, monkeypatch):
    """Zero equity is a real value, not a missing exchange-account fact."""
    import json as json_mod

    import aletheia.portfolio.exchange_sync as exchange_sync

    monkeypatch.setattr(
        exchange_sync,
        "get_cached_state",
        lambda: {"equity_usdt": 0.0, "age_seconds": 3.0},
    )
    monkeypatch.setattr(exchange_sync, "status_for", lambda _state: "ok")
    monkeypatch.setattr(exchange_sync, "active_broker_view_complete", lambda: (True, "okx_demo connected"))

    result = runner.invoke(app, ["inspect", "status", "--json"])

    assert result.exit_code == 0, result.output
    data = json_mod.loads(result.output)
    assert data["availability"] == "available"
    assert data["portfolio"]["equity_usdt"] == 0.0
    assert data["portfolio"]["exchange_account"] == {
        "status": "ok",
        "equity_usdt": 0.0,
        "age_sec": 3.0,
        "complete": True,
        "completeness_reason": "okx_demo connected",
        "reason": None,
    }


def test_inspect_status_labels_stale_exchange_account(cli_db, monkeypatch):
    import json as json_mod

    import aletheia.portfolio.exchange_sync as exchange_sync

    monkeypatch.setattr(
        exchange_sync,
        "get_cached_state",
        lambda: {"total_equity_usdt": 123.45, "age_seconds": 999.0},
    )
    monkeypatch.setattr(exchange_sync, "status_for", lambda _state: "stale")
    monkeypatch.setattr(exchange_sync, "active_broker_view_complete", lambda: (True, "okx_demo connected"))

    result = runner.invoke(app, ["inspect", "status", "--json"])
    human = runner.invoke(app, ["inspect", "status"])

    assert result.exit_code == 0, result.output
    data = json_mod.loads(result.output)
    assert data["availability"] == "partial"
    assert data["portfolio"]["equity_usdt"] == 123.45
    assert data["portfolio"]["exchange_account"]["status"] == "stale"
    assert data["portfolio"]["exchange_account"]["reason"] == "cache_stale"
    assert human.exit_code == 0, human.output
    assert "[exchange_account: stale]" in human.output
    assert "exchange_account: cache_stale" in human.output


def test_inspect_status_reports_exchange_account_read_failure(cli_db, monkeypatch):
    import json as json_mod

    import aletheia.portfolio.exchange_sync as exchange_sync

    def fail_read():
        raise RuntimeError("secret backend path leaked")

    monkeypatch.setattr(exchange_sync, "get_cached_state", fail_read)

    result = runner.invoke(app, ["inspect", "status", "--json"])

    assert result.exit_code == 0, result.output
    data = json_mod.loads(result.output)
    assert data["availability"] == "partial"
    account = data["portfolio"]["exchange_account"]
    assert account["status"] == "error"
    assert account["reason"] == "read_failed"
    assert "secret backend path leaked" not in result.output


def test_inspect_status_marks_incomplete_active_broker_view_partial(cli_db, monkeypatch):
    import json as json_mod

    import aletheia.portfolio.exchange_sync as exchange_sync

    monkeypatch.setattr(
        exchange_sync,
        "get_cached_state",
        lambda: {"equity_usdt": 1000.0, "age_seconds": 1.0},
    )
    monkeypatch.setattr(exchange_sync, "status_for", lambda _state: "ok")
    monkeypatch.setattr(
        exchange_sync,
        "active_broker_view_complete",
        lambda: (False, "okx_demo not connected (have ['bitget_demo'])"),
    )

    result = runner.invoke(app, ["inspect", "status", "--json"])

    assert result.exit_code == 0, result.output
    data = json_mod.loads(result.output)
    assert data["availability"] == "partial"
    account = data["portfolio"]["exchange_account"]
    assert account["status"] == "ok"
    assert account["complete"] is False
    assert account["reason"] == "active_broker_view_incomplete"
    assert account["completeness_reason"] == "okx_demo not connected (have ['bitget_demo'])"


def test_inspect_strategies_marks_malformed_parameters_partial(cli_db):
    import json as json_mod

    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO strategy_records (id, name, state, parameters) VALUES (?, ?, ?, ?)",
        ("valid-strategy", "Valid Strategy", "draft", '{"symbol":"BTC/USDT","ruleset":{"entry":"x"}}'),
    )
    db.execute(
        "INSERT INTO strategy_records (id, name, state, parameters) VALUES (?, ?, ?, ?)",
        ("empty-strategy", "Empty Strategy", "draft", "{}"),
    )
    db.execute(
        "INSERT INTO strategy_records (id, name, state, parameters) VALUES (?, ?, ?, ?)",
        ("bad-strategy", "Bad Strategy", "draft", "{broken"),
    )
    db.execute(
        "INSERT INTO strategy_records (id, name, state, parameters) VALUES (?, ?, ?, ?)",
        ("non-object-strategy", "Non Object Strategy", "draft", "[]"),
    )
    db.commit()

    result = runner.invoke(app, ["inspect", "strategies", "--json"])

    assert result.exit_code == 0, result.output
    payload = json_mod.loads(result.output)
    assert payload["availability"] == "partial"
    assert payload["invalid_parameters_count"] == 2
    rows = {row["id"]: row for row in payload["rows"]}
    assert rows["valid-strategy"]["parameters_status"] == "available"
    assert rows["valid-strategy"]["symbol"] == "BTC/USDT"
    assert rows["valid-strategy"]["has_ruleset"] is True
    assert rows["empty-strategy"]["parameters_status"] == "available"
    assert rows["empty-strategy"]["has_ruleset"] is False
    assert rows["bad-strategy"]["parameters_status"] == "invalid"
    assert rows["bad-strategy"]["parameters_error"] == "invalid_json"
    assert rows["bad-strategy"]["has_ruleset"] is None
    assert rows["non-object-strategy"]["parameters_error"] == "not_json_object"

    human = runner.invoke(app, ["inspect", "strategies"])
    assert human.exit_code == 0, human.output
    assert "id=bad-strategy" in human.output
    assert "parameters_status=invalid" in human.output
    assert "{broken" not in human.output


def test_strategy_parameters_write_path_requires_json_object(cli_db):
    from pydantic import ValidationError

    from aletheia.storage.models import StrategyParametersInvalid, StrategyRecord
    from aletheia.storage.repositories import insert_strategy, update_strategy

    with pytest.raises(ValidationError):
        StrategyRecord(name="Malformed", parameters="{broken")

    strategy = StrategyRecord(id="strategy-1", name="Valid", parameters="{}")
    insert_strategy(strategy)

    with pytest.raises(StrategyParametersInvalid) as exc:
        update_strategy("strategy-1", parameters='["not", "an", "object"]')
    assert exc.value.reason == "not_json_object"


def test_inspect_factor_feedback_formats_win_rate_ratio_as_percent(monkeypatch):
    import json as json_mod

    from aletheia.analytics import factor_feedback as factor_feedback_module

    rows = [
        {"factor_key": "zero", "total_trades": 2, "win_rate": 0, "expectancy": 0, "profit_factor": 0, "status": "watch"},
        {"factor_key": "third", "total_trades": 3, "win_rate": 0.3333, "expectancy": 1, "profit_factor": 1, "status": "watch"},
        {"factor_key": "half", "total_trades": 2, "win_rate": 0.5, "expectancy": 1, "profit_factor": 1, "status": "watch"},
        {"factor_key": "three-quarters", "total_trades": 4, "win_rate": 0.75, "expectancy": 1, "profit_factor": 2, "status": "active"},
        {"factor_key": "all", "total_trades": 2, "win_rate": 1.0, "expectancy": 1, "profit_factor": 9, "status": "preferred"},
        {"factor_key": "unbounded", "trade_count": 3, "win_rate": 1.0, "expectancy": 1, "profit_factor": None, "profit_factor_state": "unbounded_no_losses", "status": "active"},
        {"factor_key": "missing", "total_trades": 2, "win_rate": None, "expectancy": 1, "profit_factor": 1, "status": "watch"},
    ]
    monkeypatch.setattr(
        factor_feedback_module,
        "build_factor_feedback",
        lambda *, limit, min_trades: rows,
    )

    result = runner.invoke(app, ["inspect", "factor-feedback", "--limit", "10"])

    assert result.exit_code == 0, result.output
    for key, rendered in [
        ("zero", "0%"),
        ("third", "33%"),
        ("half", "50%"),
        ("three-quarters", "75%"),
        ("all", "100%"),
        ("unbounded", "∞"),
        ("missing", "n/a"),
    ]:
        assert any(key in line and rendered in line for line in result.output.splitlines())

    raw = runner.invoke(app, ["inspect", "factor-feedback", "--limit", "10", "--json"])
    assert raw.exit_code == 0, raw.output
    payload = json_mod.loads(raw.output)
    assert payload[3]["factor_key"] == "three-quarters"
    assert payload[3]["win_rate"] == 0.75


def test_inspect_equity_uses_current_account_equity_series(cli_db):
    import json as json_mod

    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        """
        INSERT INTO portfolio_snapshots
        (id, total_value_usdt, account_equity_usdt, unrealized_pnl, realized_pnl,
         positions_count, execution_mode)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("legacy", 9999, None, 99, 0, 9, "paper_internal"),
    )
    db.execute(
        """
        INSERT INTO portfolio_snapshots
        (id, total_value_usdt, account_equity_usdt, unrealized_pnl, realized_pnl,
         positions_count, execution_mode)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("paper", 1000, 1000, 12.5, 40, 2, "paper_internal"),
    )
    db.execute(
        """
        INSERT INTO portfolio_snapshots
        (id, total_value_usdt, account_equity_usdt, unrealized_pnl, realized_pnl,
         positions_count, execution_mode)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("exchange", 2000, 2000, -5, 10, 1, "exchange_account"),
    )
    db.commit()

    result = runner.invoke(app, ["inspect", "equity", "--json"])

    assert result.exit_code == 0, result.output
    rows = json_mod.loads(result.output)
    assert len(rows) == 1
    assert rows[0]["execution_mode"] == "paper_internal"
    assert rows[0]["account_equity_usdt"] == 1000
    assert rows[0]["positions_count"] == 2
    assert "equity_usdt" not in rows[0]
    assert "open_positions" not in rows[0]

    exchange = runner.invoke(
        app,
        ["inspect", "equity", "--execution-mode", "exchange_account", "--json"],
    )
    assert exchange.exit_code == 0, exchange.output
    assert json_mod.loads(exchange.output)[0]["account_equity_usdt"] == 2000

    human = runner.invoke(app, ["inspect", "equity"])
    assert human.exit_code == 0, human.output
    assert "mode=paper_internal" in human.output
    assert "eq=$1000" in human.output
    assert "pos=2" in human.output
    assert "9999" not in human.output


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
