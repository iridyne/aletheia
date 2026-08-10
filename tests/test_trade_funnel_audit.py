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


def _insert_filter_decision(
    db,
    idx: int,
    *,
    stage: str,
    filter_name: str,
    decision: str = "passed",
    reason_code: str = "test",
) -> None:
    candidate_key = (
        f"pi_decision:pi-funnel-trade-{idx}"
        if stage == "proposal_submission"
        else f"opportunity:opp-funnel-{idx}"
    )
    db.execute(
        "INSERT INTO filter_decisions "
        "(id, dedup_key, candidate_key, candidate_kind, opportunity_id, symbol, "
        "market_type, direction, filter_name, stage, decision, reason_code, "
        "filter_version, evaluated_at) "
        "VALUES (?, ?, ?, ?, ?, 'BTC/USDT', 'usdt_futures', 'long', ?, ?, ?, ?, 'test-v1', ?)",
        (
            f"fd-{stage}-{filter_name}-{idx}",
            f"dedup-{stage}-{filter_name}-{idx}",
            candidate_key,
            "pi_decision" if stage == "proposal_submission" else "opportunity",
            f"opp-funnel-{idx}",
            filter_name,
            stage,
            decision,
            reason_code,
            _iso(),
        ),
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
        _insert_signal(db, i, "skip")
    db.commit()

    report = build_trade_funnel_audit(hours=24, min_sample_size=1, include_ranker_snapshot=False)

    assert report["counts"]["signal_pending_written"] == 4
    assert report["counts"]["system_operator_evaluated"] == 4
    assert report["counts"]["decisions_trade"] == 0
    assert report["main_choke_point"]["stage"] == "system_operator_decision"
    assert "4 evaluated" in report["main_choke_point"]["summary"]


def test_trade_funnel_conversion_uses_linked_opportunity_cohort():
    """Unrelated decisions in the same clock window must not create >100% rates."""
    from aletheia.analytics.trade_funnel import build_trade_funnel_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, 1)
    _insert_signal(db, 1, "signal_pending")
    _insert_signal(db, 1, "trade")
    _insert_opp(db, 2)
    _insert_signal(db, 2, "skip")  # linked to cohort, but bypassed signal_pending
    for i in range(20, 24):
        _insert_signal(db, i, "skip")  # no matching opportunity in this cohort
    db.commit()

    report = build_trade_funnel_audit(hours=24, include_ranker_snapshot=False)

    assert report["counts"]["system_operator_evaluated"] == 6  # event throughput remains visible
    assert report["causal_cohort_counts"]["system_operator_evaluated"] == 1
    assert report["causal_cohort_counts"]["decisions_trade"] == 1
    assert report["conversion_rates"]["signal_pending_to_evaluated"] == 1.0
    assert all(
        rate is None or 0.0 <= rate <= 1.0
        for rate in report["conversion_rates"].values()
    )


def test_trade_funnel_counts_durable_operator_defer_as_evaluated():
    """A deferred carrier was consumed even though it has no terminal PI action."""
    import json

    from aletheia.analytics.trade_funnel import build_trade_funnel_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, 1)
    _insert_signal(db, 1, "signal_pending")
    db.execute(
        "INSERT INTO operator_evaluation_events "
        "(id,dedup_key,event_kind,trigger,pending_unresolved_total,pending_visible_count,"
        "queue_snapshot_json,data_health_json,created_at) "
        "VALUES ('defer-funnel-1','defer-funnel-key-1',"
        "'operator_decision_deferred_unchanged_context','signal',1,1,?,?,?)",
        (
            json.dumps({"opportunity_id": "opp-funnel-1"}),
            "{}",
            _iso(),
        ),
    )
    db.commit()

    report = build_trade_funnel_audit(
        hours=24,
        min_sample_size=1,
        include_ranker_snapshot=False,
    )

    assert report["causal_cohort_counts"]["system_operator_evaluated"] == 1
    assert report["conversion_rates"]["signal_pending_to_evaluated"] == 1.0
    assert report["main_choke_point"]["stage"] == "system_operator_decision"


def test_trade_funnel_uses_persisted_ranker_and_submission_evidence():
    from aletheia.analytics.trade_funnel import (
        build_trade_funnel_audit,
        render_trade_funnel_audit,
    )
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for idx in (1, 2):
        _insert_opp(db, idx)
        _insert_filter_decision(
            db,
            idx,
            stage="paper_ranker",
            filter_name="execution_eligibility",
        )
    _insert_filter_decision(
        db,
        1,
        stage="paper_trader_selection",
        filter_name="signal_batch_limit",
    )
    _insert_signal(db, 1, "signal_pending")
    _insert_signal(db, 1, "trade")
    _insert_filter_decision(
        db,
        1,
        stage="proposal_submission",
        filter_name="direction_bias_brake",
        decision="rejected",
        reason_code="direction_overheated",
    )
    db.commit()

    report = build_trade_funnel_audit(
        hours=24,
        min_sample_size=1,
        include_ranker_snapshot=False,
    )

    assert report["counts"]["ranker_candidates"] == 2
    assert report["counts"]["ranker_eligible"] == 1
    assert report["causal_cohort_counts"]["ranker_candidates"] == 2
    assert report["causal_cohort_counts"]["ranker_eligible"] == 1
    assert report["proposal_submission_reasons"] == [
        {"reason": "direction_overheated", "count": 1}
    ]
    assert report["main_choke_point"]["stage"] == "proposal_submission"
    assert "direction_overheated" in report["main_choke_point"]["summary"]
    rendered = render_trade_funnel_audit(report)
    assert "ranker_candidates: 2" in rendered
    assert "direction_overheated: 1" in rendered


def test_trade_funnel_cohort_uses_latest_terminal_decision_once():
    """One opportunity with repeated evaluations is one converted opportunity."""
    from aletheia.analytics.trade_funnel import build_trade_funnel_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, 1)
    _insert_signal(db, 1, "signal_pending")
    _insert_signal(db, 1, "watch")
    _insert_signal(db, 1, "trade")
    db.commit()

    report = build_trade_funnel_audit(hours=24, include_ranker_snapshot=False)
    cohort = report["causal_cohort_counts"]

    assert cohort["system_operator_evaluated"] == 1
    assert cohort["decisions_trade"] == 1
    assert cohort["decisions_watch"] == 0
    assert report["conversion_rates"]["evaluated_to_trade_decision"] == 1.0


def test_trade_funnel_identifies_risk_engine_rejection_choke_point():
    from aletheia.analytics.trade_funnel import build_trade_funnel_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(3):
        _insert_opp(db, i)
        pid = _insert_proposal(db, i)
        _insert_signal(db, i, "signal_pending")
        _insert_signal(db, i, "trade", proposal_id=pid)
        _insert_risk(db, i, approved=False, reason="max_demo_position_notional")
    db.commit()

    report = build_trade_funnel_audit(hours=24, min_sample_size=1, include_ranker_snapshot=False)

    assert report["counts"]["proposals_created"] == 3
    assert report["counts"]["risk_rejected"] == 3
    assert report["risk_rejection_reasons"][0]["reason"] == "max_demo_position_notional"
    assert report["main_choke_point"]["stage"] == "risk_engine"


def test_trade_funnel_small_rejected_sample_never_reports_ok_with_zero_positions():
    from aletheia.analytics.trade_funnel import build_trade_funnel_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(2):
        _insert_opp(db, i)
        pid = _insert_proposal(db, i)
        _insert_signal(db, i, "signal_pending")
        _insert_signal(db, i, "trade", proposal_id=pid)
        _insert_risk(db, i, approved=False, reason="risk_limit")
    _insert_opp(db, 99)  # enough upstream evidence; proposal sample remains thin
    db.commit()

    report = build_trade_funnel_audit(
        hours=24,
        min_sample_size=3,
        include_ranker_snapshot=False,
    )
    choke = report["main_choke_point"]

    assert choke["stage"] == "risk_engine"
    assert choke["severity"] == "insufficient_data"
    assert "2" in choke["summary"]


def test_trade_funnel_groups_dynamic_risk_amounts_under_one_reason():
    from aletheia.analytics.trade_funnel import build_trade_funnel_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i, notional in enumerate((68.8, 71.2, 83.4)):
        _insert_opp(db, i)
        _insert_proposal(db, i)
        _insert_risk(
            db,
            i,
            approved=False,
            reason=(
                f"Position notional ${notional} exceeds "
                "max demo position notional $0.0"
            ),
        )
    db.commit()

    report = build_trade_funnel_audit(hours=24, include_ranker_snapshot=False)
    top = report["risk_rejection_reasons"][0]

    assert top["count"] == 3
    assert top["reason"] == (
        "Position notional $N exceeds max demo position notional $N"
    )
    assert "$68.8" in top["example"]


def test_trade_funnel_groups_machine_risk_tokens_with_dynamic_numbers():
    from aletheia.analytics.trade_funnel import _risk_reason_bucket

    assert _risk_reason_bucket(
        "exceeds_demo_position_notional:68.79983666>0.0"
    ) == "exceeds_demo_position_notional:N>N"


def test_trade_funnel_identifies_execution_gap_after_risk_approval():
    from aletheia.analytics.trade_funnel import build_trade_funnel_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(2):
        _insert_opp(db, i)
        pid = _insert_proposal(db, i)
        _insert_signal(db, i, "signal_pending")
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
        _insert_signal(db, i, "signal_pending")
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
