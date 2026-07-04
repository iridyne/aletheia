from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _iso(hours_ago: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _insert_opp(db, idx: int) -> str:
    oid = f"opp-funnel-{idx}"
    db.execute(
        "INSERT INTO opportunities "
        "(id, symbol, market, direction_bias, confidence, source_agent, created_at, status) "
        "VALUES (?, 'BTC/USDT', 'usdt_futures', 'long', 'high', 'test', ?, 'open')",
        (oid, _iso()),
    )
    return oid


def _insert_signal(db, idx: int, action: str = "signal_pending", proposal_id: str | None = None) -> None:
    db.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, action, conviction, reasoning, proposal_id, opportunity_id, created_at) "
        "VALUES (?, 'BTC/USDT', 'long', ?, 70, 'test', ?, ?, ?)",
        (f"pi-funnel-{action}-{idx}", action, proposal_id, f"opp-funnel-{idx}", _iso()),
    )


def _insert_proposal(db, idx: int, status: str = "pending", notional: float = 25.0) -> str:
    pid = f"prop-funnel-{idx}"
    db.execute(
        "INSERT INTO trade_proposals "
        "(id, opportunity_id, symbol, market_type, direction, order_type, quantity, price, "
        "execution_mode, status, created_at) "
        "VALUES (?, ?, 'BTC/USDT', 'usdt_futures', 'long', 'limit', ?, 100, "
        "'bitget_demo', ?, ?)",
        (pid, f"opp-funnel-{idx}", notional / 100.0, status, _iso()),
    )
    return pid


def _insert_risk(db, idx: int, approved: bool, reason: str = "risk_limit") -> None:
    db.execute(
        "INSERT INTO risk_decisions "
        "(id, proposal_id, approved, risk_score, violations, message, created_at) "
        "VALUES (?, ?, ?, 1.0, ?, ?, ?)",
        (
            f"risk-funnel-{idx}",
            f"prop-funnel-{idx}",
            1 if approved else 0,
            f'["{reason}"]',
            reason,
            _iso(),
        ),
    )


def _insert_order(db, idx: int, status: str = "filled") -> None:
    db.execute(
        "INSERT INTO orders "
        "(id, proposal_id, risk_decision_id, symbol, market_type, direction, order_type, "
        "quantity, price, execution_mode, status, created_at, filled_at) "
        "VALUES (?, ?, ?, 'BTC/USDT', 'usdt_futures', 'long', 'limit', 0.25, 100, "
        "'bitget_demo', ?, ?, ?)",
        (f"order-funnel-{idx}", f"prop-funnel-{idx}", f"risk-funnel-{idx}", status, _iso(), _iso()),
    )


def _insert_position(db, idx: int, notional: float = 25.0) -> None:
    db.execute(
        "INSERT INTO positions "
        "(id, symbol, market_type, direction, quantity, entry_price, execution_mode, "
        "proposal_id, order_id, status, leverage, opened_at) "
        "VALUES (?, 'BTC/USDT', 'usdt_futures', 'long', ?, 100, 'bitget_demo', "
        "?, ?, 'open', 1, ?)",
        (
            f"pos-funnel-{idx}",
            notional / 100.0,
            f"prop-funnel-{idx}",
            f"order-funnel-{idx}",
            _iso(),
        ),
    )


def test_trade_funnel_missing_tables_returns_insufficient_data_not_crash():
    from aletheia.analytics.trade_funnel import build_trade_funnel_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute("DROP TABLE opportunities")
    db.commit()

    report = build_trade_funnel_audit(hours=24, include_ranker_snapshot=False)

    assert report["main_choke_point"]["severity"] == "insufficient_data"
    assert report["counts"]["opportunities_created"] == 0
    assert report["top_dropoffs"] == []


def test_trade_funnel_missing_downstream_table_keeps_available_upstream_counts():
    from aletheia.analytics.trade_funnel import build_trade_funnel_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, 1)
    db.execute("DROP TABLE orders")
    db.commit()

    report = build_trade_funnel_audit(hours=24, include_ranker_snapshot=False)

    assert report["counts"]["opportunities_created"] == 1
    assert report["counts"]["orders_executed"] == 0
    assert report["schema_errors"]
    assert report["main_choke_point"]["severity"] in {"insufficient_data", "warn"}


def test_trade_funnel_identifies_system_operator_skip_choke_point():
    from aletheia.analytics.trade_funnel import build_trade_funnel_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(4):
        _insert_opp(db, i)
        _insert_signal(db, i, "signal_pending")
        _insert_signal(db, i + 10, "skip")
    db.commit()

    report = build_trade_funnel_audit(hours=24, min_sample_size=1, include_ranker_snapshot=False)

    assert report["counts"]["signal_pending_written"] == 4
    assert report["counts"]["system_operator_evaluated"] == 4
    assert report["counts"]["decisions_trade"] == 0
    assert report["main_choke_point"]["stage"] == "system_operator_decision"
    assert "4 evaluated" in report["main_choke_point"]["summary"]


def test_trade_funnel_identifies_risk_engine_rejection_choke_point():
    from aletheia.analytics.trade_funnel import build_trade_funnel_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(3):
        _insert_opp(db, i)
        pid = _insert_proposal(db, i)
        _insert_signal(db, i, "trade", proposal_id=pid)
        _insert_risk(db, i, approved=False, reason="max_demo_position_notional")
    db.commit()

    report = build_trade_funnel_audit(hours=24, min_sample_size=1, include_ranker_snapshot=False)

    assert report["counts"]["proposals_created"] == 3
    assert report["counts"]["risk_rejected"] == 3
    assert report["risk_rejection_reasons"][0]["reason"] == "max_demo_position_notional"
    assert report["main_choke_point"]["stage"] == "risk_engine"


def test_trade_funnel_identifies_execution_gap_after_risk_approval():
    from aletheia.analytics.trade_funnel import build_trade_funnel_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(2):
        _insert_opp(db, i)
        pid = _insert_proposal(db, i)
        _insert_signal(db, i, "trade", proposal_id=pid)
        _insert_risk(db, i, approved=True)
    db.commit()

    report = build_trade_funnel_audit(hours=24, min_sample_size=1, include_ranker_snapshot=False)

    assert report["counts"]["risk_approved"] == 2
    assert report["counts"]["orders_executed"] == 0
    assert report["main_choke_point"]["stage"] == "execution_gateway"


def test_trade_funnel_points_tiny_positions_to_sizing_trace():
    from aletheia.analytics.trade_funnel import build_trade_funnel_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(3):
        _insert_opp(db, i)
        pid = _insert_proposal(db, i, status="executed", notional=1.5)
        _insert_signal(db, i, "trade", proposal_id=pid)
        _insert_risk(db, i, approved=True)
        _insert_order(db, i)
        _insert_position(db, i, notional=1.5)
    db.commit()

    report = build_trade_funnel_audit(hours=24, min_sample_size=1, include_ranker_snapshot=False)

    assert report["counts"]["positions_opened"] == 3
    assert report["position_stats"]["median_notional_usdt"] == 1.5
    assert report["main_choke_point"]["stage"] == "sizing_tiny_positions"
    assert "Sizing Trace Audit" in report["main_choke_point"]["next_action"]


def test_trade_funnel_cli_outputs_human_and_json_reports():
    import json
    from typer.testing import CliRunner

    from aletheia.main import app

    runner = CliRunner()
    human = runner.invoke(app, ["funnel", "audit", "--hours", "24"])
    machine = runner.invoke(app, ["funnel", "audit", "--days", "7", "--json"])

    assert human.exit_code == 0
    assert "Trade Funnel Audit" in human.output
    assert machine.exit_code == 0
    payload = json.loads(machine.output)
    assert payload["window"]["days"] == 7
    assert "main_choke_point" in payload
