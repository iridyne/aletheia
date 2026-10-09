"""Go-live scorecard reads complete, current evidence (#1968, #1696, #1698)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture(autouse=True)
def _external_account():
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)


def _close(position_id: str, pnl: float, *, quantity: float, hours_ago: float) -> None:
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    _insert_closed_position_via_producer(
        position_id=position_id, symbol="BTC/USDT", market_type="usdt_futures",
        direction="long", quantity=quantity, entry_price=100.0,
        execution_mode="bitget_demo", realized_pnl=pnl,
        closed_at=(datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(),
    )


def _clone_close(source_id: str, count: int) -> None:
    """Copy one fully attributed close (Position + entry Order) ``count`` times."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    series = (
        "WITH RECURSIVE n(value) AS (SELECT 1 UNION ALL SELECT value + 1 FROM n "
        f"WHERE value < {count}) "
    )
    order_cols = [row[1] for row in db.execute("PRAGMA table_info(orders)")]
    order_select = ", ".join(
        f"'{source_id}-order-' || n.value" if col == "id"
        else f"'{source_id}-' || n.value" if col == "position_id"
        else "NULL" if col == "client_order_id"
        else col
        for col in order_cols
    )
    db.execute(
        series + f"INSERT INTO orders ({', '.join(order_cols)}) SELECT {order_select} "
        "FROM orders, n WHERE orders.id = (SELECT order_id FROM positions WHERE id = ?)",
        (source_id,),
    )
    position_cols = [row[1] for row in db.execute("PRAGMA table_info(positions)")]
    position_select = ", ".join(
        f"'{source_id}-' || n.value" if col == "id"
        else f"'{source_id}-order-' || n.value" if col == "order_id"
        else col
        for col in position_cols
    )
    db.execute(
        series + f"INSERT INTO positions ({', '.join(position_cols)}) SELECT {position_select} "
        "FROM positions, n WHERE positions.id = ?",
        (source_id,),
    )
    db.commit()


def test_scorecard_counts_the_oldest_loss_beyond_ten_thousand_closes():
    """#1968: the newest-10k window hid the oldest loss from every PnL criterion."""
    from aletheia.analytics.go_live_scorecard import compute_scorecard

    _close("oldest-loss", -90_000.0, quantity=1000.0, hours_ago=3)
    _close("win", 5.0, quantity=1.0, hours_ago=2)
    _clone_close("win", 9_999)  # 10,001 eligible closes in all

    result = compute_scorecard()

    gate = result["performance_gate"]
    assert gate["eligible_trade_count"] == 10_001
    assert gate["net_realized_pnl"] == pytest.approx(-90_000.0 + 5.0 * 10_000)
    assert gate["status"] == "fail"
    assert result["traffic_light"] == "red"
    assert result["source_raw_event_count"] == 10_001
    assert result["cohort_complete"] is True
    assert result["ordered_source_digest"]


def test_unreadable_cohort_is_a_blocker_even_when_every_criterion_passes(monkeypatch):
    """#1968: a reader failure must block Live, never score an empty sample."""
    from aletheia.analytics import go_live_scorecard as scorecard
    from tests.test_outcome_consumers import _unavailable_cohort

    monkeypatch.setattr(scorecard, "read_complete_outcome_cohort", lambda **_: _unavailable_cohort())
    passing = lambda *_args: scorecard._criterion("other", "other", 100, 0.1, "ok")
    for name in (
        "exchange_connectivity", "risk_violations", "data_coverage",
        "model_health", "reconciliation", "calibration",
    ):
        monkeypatch.setattr(scorecard, "_check_" + name, passing)

    result = scorecard.compute_scorecard()

    assert result["cohort_state"] == "unavailable"
    assert result["cohort_complete"] is False
    assert result["source_raw_event_count"] is None
    # The Live unlock route rejects anything that is not green with no blockers.
    assert result["traffic_light"] == "red"
    assert "Eligible outcome cohort is not available" in result["blockers"]
    pnl_keys = {"trade_volume", "profit_factor", "win_rate", "max_drawdown"}
    for criterion in result["criteria"]:
        if criterion["key"] in pnl_keys:
            assert criterion["detail"] == "unavailable"
            assert criterion["score"] == 0


def test_risk_violation_older_than_the_newest_twenty_decisions_still_fails():
    """#1696: the 24h target covers the whole window, not its newest 20 rows."""
    from aletheia.analytics.go_live_scorecard import _check_risk_violations
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import RiskDecision, TradeProposal

    now = datetime.now(timezone.utc)
    for index in range(21):
        proposal_id = f"risk-proposal-{index}"
        repo._insert("trade_proposals", TradeProposal(
            id=proposal_id, symbol="BTC/USDT", market_type="usdt_futures",
            direction="long", execution_mode="bitget_demo",
        ).model_dump())
        repo._insert("risk_decisions", RiskDecision(
            id=f"risk-decision-{index}", proposal_id=proposal_id, approved=index != 0,
            proposal_execution_mode="bitget_demo",
            # The oldest decision (row 21 by recency) carries the only violation.
            violations='["max_leverage"]' if index == 0 else "[]",
            created_at=(now - timedelta(hours=20) + timedelta(minutes=index)).isoformat(),
        ).model_dump())

    result = _check_risk_violations()

    assert result["status"] == "fail"
    assert result["detail"] == "1 violation events"


def test_active_reconciliation_integrity_incident_fails_reconciliation():
    """#1696: no code writes 'reconciliation_error'; live incidents must count."""
    from aletheia.analytics.go_live_scorecard import _check_reconciliation
    from aletheia.storage.models import ReconciliationIntegrityIncident
    from aletheia.storage.repositories import insert_reconciliation_integrity_incident

    assert _check_reconciliation()["status"] == "pass"
    insert_reconciliation_integrity_incident(ReconciliationIntegrityIncident(
        fingerprint="churn-fixture", churn_count=4,
    ))

    result = _check_reconciliation()

    assert result["status"] == "fail"
    assert "1 active integrity incidents" in result["detail"]


def test_inconclusive_order_reconciliation_fails_reconciliation():
    """#1696: a current reconciliation path's failure fact is a reconciliation error."""
    from aletheia.analytics.go_live_scorecard import _check_reconciliation
    from aletheia.files.jsonl import write_audit

    write_audit({
        "actor": "system", "source": "reconciliation",
        "event_type": "order_reconciliation_inconclusive",
        "entity_type": "order", "entity_id": "order-1",
        "reason": "exchange_order_not_found",
        "message": "Order reconciliation reached inconclusive.",
    })

    result = _check_reconciliation()

    assert result["status"] == "fail"
    assert result["detail"].startswith("1 errors in 7 days")
