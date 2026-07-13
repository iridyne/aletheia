"""Behavior consumers must not learn or act on administrative position closes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json


def _insert_closed(
    position_id: str,
    pnl: float | None,
    *,
    eligible: bool,
    strategy_id: str = "strategy-1",
    proposal_id: str | None = None,
) -> None:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    from tests.test_outcome_closure import _insert_entry_chain

    now = datetime.now(timezone.utc)
    kwargs = {
        "id": position_id,
        "symbol": "BTC/USDT",
        "market_type": "usdt_futures",
        "direction": "long",
        "quantity": 1.0,
        "entry_price": 100.0,
        "current_price": 100.0 + float(pnl or 0.0),
        "execution_mode": "paper_internal",
        "strategy_id": strategy_id,
        "proposal_id": proposal_id,
        "status": "closed",
        "realized_pnl": pnl,
        "opened_at": (now - timedelta(hours=2)).isoformat(),
        "closed_at": now.isoformat(),
    }
    if eligible:
        opportunity_id, proposal_id, order_id = _insert_entry_chain(
            position_id=position_id,
            symbol="BTC/USDT",
            market_type="usdt_futures",
            direction="long",
            quantity=1.0,
            price=100.0,
            execution_mode="paper_internal",
            proposal_id=proposal_id,
            strategy_id=strategy_id,
        )
        kwargs.update({
            "opportunity_id": opportunity_id,
            "proposal_id": proposal_id,
            "order_id": order_id,
            "close_reason": "verified_exit",
            "close_category": "strategy",
            "entry_price_source": "local_fill",
            "entry_fill_verified": True,
            "exit_price_source": "local_fill",
            "exit_fill_verified": True,
            "entry_fee": 0.0,
            "exit_fee": 0.0,
            "funding_cost": 0.0,
            "funding_source": "local_simulation",
            "outcome_eligible": True,
            "outcome_exclusion_reason": "eligible",
            "outcome_semantics_version": "oi-v1",
        })
    else:
        kwargs.update({
            "close_reason": "reconciled_gone",
            "close_category": "administrative",
            "exit_price_source": "unavailable",
            "exit_fill_verified": False,
            "outcome_eligible": False,
            "outcome_exclusion_reason": "administrative_close",
            "outcome_semantics_version": "oi-v1",
        })
    repo.insert_position(Position(**kwargs))


def test_trade_and_strategy_stats_ignore_administrative_rows():
    from aletheia.analytics.trade_stats import (
        build_strategy_kpis,
        compute_rolling_trade_stats,
    )

    _insert_closed("verified-win", 10.0, eligible=True)
    _insert_closed("verified-loss", -5.0, eligible=True)
    _insert_closed("administrative", -10_000.0, eligible=False)

    rolling = compute_rolling_trade_stats(days=7)
    assert rolling["total_trades"] == 2
    assert rolling["wins"] == 1
    assert rolling["losses"] == 1
    assert rolling["total_pnl"] == 5.0

    strategy = build_strategy_kpis()["strategy-1"]
    assert {key: strategy[key] for key in ("trade_count", "win_rate", "total_pnl")} == {
        "trade_count": 2, "win_rate": 50.0, "total_pnl": 5.0,
    }
    assert strategy["outcome_semantics_version"] == "oi-v1"


def test_operator_strategy_performance_ignores_administrative_rows():
    from aletheia.agent_shell.operator_loop import _get_strategy_performance

    _insert_closed("verified", 8.0, eligible=True)
    _insert_closed("administrative", -1_000.0, eligible=False)

    performance = _get_strategy_performance("strategy-1")
    assert {key: performance[key] for key in (
        "trade_count", "win_rate", "expectancy", "total_pnl",
    )} == {
        "trade_count": 1,
        "win_rate": 100.0,
        "expectancy": 8.0,
        "total_pnl": 8.0,
    }
    assert performance["outcome_semantics_version"] == "oi-v1"


def test_family_pi_context_and_go_live_metrics_ignore_administrative_rows():
    from aletheia.agent_shell.trading_context import _build_layer2_short_term
    from aletheia.analytics.family_scorecard import compute_family_scorecard
    from aletheia.analytics.go_live_scorecard import _genuine_closed_pnls

    _insert_closed("verified", 7.0, eligible=True)
    _insert_closed("administrative", -500.0, eligible=False)

    families = compute_family_scorecard()
    assert len(families) == 1
    assert families[0]["trade_count"] == 1
    assert families[0]["win_rate"] == 100.0
    assert families[0]["outcome_semantics_version"] == "oi-v1"

    layer = _build_layer2_short_term()
    assert layer["outcome_stats"]["total_trades"] == 1
    assert layer["outcome_stats"]["total_pnl"] == 7.0
    assert _genuine_closed_pnls() == [7.0]


def test_calibration_build_uses_only_verified_current_semantics_outcomes():
    from aletheia.analytics.confidence_calibration import build_calibration_report
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    for index in range(5):
        proposal_id = f"proposal-{index}"
        _insert_closed(
            f"verified-{index}", 1.0, eligible=True, proposal_id=proposal_id,
        )
        db.execute(
            "INSERT INTO pi_decisions (id, created_at, action, conviction, proposal_id) "
            "VALUES (?, ?, 'trade', 70, ?)",
            (f"decision-{index}", now, proposal_id),
        )
    _insert_closed("administrative", -10_000.0, eligible=False, proposal_id="proposal-admin")
    db.execute(
        "INSERT INTO pi_decisions (id, created_at, action, conviction, proposal_id) "
        "VALUES ('decision-admin', ?, 'trade', 70, 'proposal-admin')",
        (now,),
    )
    db.commit()

    report = build_calibration_report()
    assert report is not None
    assert report["total_executed"] == 5
    assert report["overall_win_rate"] == 1.0
    assert report["outcome_semantics_version"] == "oi-v1"


def test_old_or_stale_calibration_report_is_rejected_before_pi_consumption():
    from aletheia.analytics.confidence_calibration import get_latest_calibration_report
    from aletheia.storage.models import KnowledgeItem
    from aletheia.storage.repositories import insert_knowledge_item

    old = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_executed": 99,
        "ece": 0.01,
        "ece_grade": "excellent",
    }
    insert_knowledge_item(KnowledgeItem(
        item_type="calibration_report",
        title="legacy calibration",
        content=json.dumps(old),
    ))
    assert get_latest_calibration_report() is None


def test_current_fresh_calibration_report_is_accepted():
    from aletheia.analytics.confidence_calibration import get_latest_calibration_report
    from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION
    from aletheia.analytics.learning_window import CLEAN_WINDOW_ANCHOR_ID
    from aletheia.storage.models import KnowledgeItem
    from aletheia.storage.repositories import insert_knowledge_item

    current = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
        "clean_window_anchor_id": CLEAN_WINDOW_ANCHOR_ID,
        "total_executed": 5,
        "ece": 0.1,
        "ece_grade": "fair",
    }
    insert_knowledge_item(KnowledgeItem(
        item_type="calibration_report",
        title="current calibration",
        content=json.dumps(current),
    ))
    assert get_latest_calibration_report() == current


def test_old_memo_and_reasoning_feedback_are_rejected():
    from aletheia.analytics.insight_synthesizer import get_latest_memo
    from aletheia.analytics.reasoning_feedback import get_latest_reasoning_feedback
    from aletheia.storage.models import KnowledgeItem
    from aletheia.storage.repositories import insert_knowledge_item

    legacy = json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_size": 100,
    })
    insert_knowledge_item(KnowledgeItem(
        item_type="trading_memo", title="legacy memo", content=legacy,
        tags=json.dumps({"valid_until": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}),
    ))
    insert_knowledge_item(KnowledgeItem(
        item_type="reasoning_feedback", title="legacy feedback", content=legacy,
    ))
    assert get_latest_memo() is None
    assert get_latest_reasoning_feedback() is None
