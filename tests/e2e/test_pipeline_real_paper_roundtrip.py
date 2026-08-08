"""Real local-paper round trip through Gateway, Risk, brokers, and feedback.

The only seeded business inputs are an Opportunity and its Strategy.  Proposal,
RiskDecision, entry/close Orders and Fills, Position, and Outcome state must all
be produced by their production entry points against the isolated e2e SQLite DB.
"""

from datetime import datetime, timezone

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage.models import MarketSnapshot, Opportunity, StrategyRecord


@pytest.fixture
def paper_internal_config(e2e_db):
    """Select the real local-paper mode and restore the temp config afterward."""
    from aletheia.config.loader import load_config, save_config

    original = load_config()
    configured = original.model_copy(deep=True)
    configured.agent.permission_mode = "Paper"
    configured.exchange.paper_execution_mode = "paper_internal"
    save_config(configured)
    try:
        yield configured
    finally:
        save_config(original)


def test_real_gateway_paper_roundtrip_preserves_one_causal_chain(
    paper_internal_config, monkeypatch,
):
    from aletheia.analytics.factor_feedback import build_factor_feedback
    from aletheia.analytics.trade_attribution import build_trade_attribution_rows
    from aletheia.execution.gateway import submit_proposal
    from aletheia.learning.outcomes import assess_realized_position
    from aletheia.portfolio.position_manager import close_position_routed

    assert paper_internal_config.exchange.paper_execution_mode == "paper_internal"

    # External observations and delivery are outside this pipeline proof.  Keep
    # their values deterministic while retaining the real Gateway/broker flow.
    monkeypatch.setattr(
        "aletheia.execution.paper_broker._btc_price_now", lambda: 60_000.0,
    )
    monkeypatch.setattr(
        "aletheia.portfolio.pnl._btc_price_at_close", lambda: 61_000.0,
    )
    monkeypatch.setattr(
        "aletheia.notifications.notify_trade_fill", lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "aletheia.notifications.notify_operator", lambda **_kwargs: None,
    )

    opportunity = Opportunity(
        symbol="ETH/USDT",
        market="spot",
        direction_bias="long",
        thesis="real local-paper causal round trip",
        confidence="high",
        confidence_score=80,
        source_agent="market_observer",
        scanner_family="roundtrip_e2e",
    )
    strategy = StrategyRecord(
        name="real-paper-roundtrip",
        state="paper_running",
        market_type_affinity="spot",
    )
    repo.insert_opportunity(opportunity)
    repo.insert_strategy(strategy)
    repo.insert_market_snapshot(MarketSnapshot(
        id="real-paper-entry-mark",
        symbol=opportunity.symbol,
        market_type="spot",
        exchange=paper_internal_config.market_data.ccxt_exchange,
        price=2_000.0,
        snapshot_at=datetime.now(timezone.utc).isoformat(),
    ))

    opened = submit_proposal(
        symbol=opportunity.symbol,
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=2_000.0,
        stop_price=1_900.0,
        take_profit=2_200.0,
        risk_reward=2.0,
        execution_mode="paper_internal",
        strategy_id=strategy.id,
        opportunity_id=opportunity.id,
        agent_rationale="real-path round-trip evidence",
    )

    assert opened["status"] == "executed", opened
    proposal = repo.get_trade_proposal(opened["proposal_id"])
    entry_order = repo.get_order(opened["order_id"])
    entry_fill = next(
        row for row in repo.list_fills(limit=10) if row["id"] == opened["fill_id"]
    )
    position = repo._select_one("positions", opened["position_id"])
    authoritative = [
        row for row in repo.list_risk_decisions(limit=10)
        if row["proposal_id"] == opened["proposal_id"]
        and row["decision_role"] == "authoritative"
    ]

    assert proposal is not None
    assert entry_order is not None
    assert position is not None
    assert len(authoritative) == 1
    risk = authoritative[0]
    assert risk["approved"] == 1
    assert risk["proposal_commit_hash"] == proposal["commit_hash"]
    assert risk["evaluated_quantity"] == pytest.approx(proposal["quantity"])
    assert entry_order["risk_decision_id"] == risk["id"]
    assert entry_order["proposal_id"] == proposal["id"]
    assert entry_order["position_id"] == position["id"]
    assert entry_fill["order_id"] == entry_order["id"]
    assert position["proposal_id"] == proposal["id"]
    assert position["order_id"] == entry_order["id"]
    assert position["opportunity_id"] == opportunity.id
    assert position["strategy_id"] == strategy.id
    assert proposal["opportunity_id"] == opportunity.id

    repo.insert_market_snapshot(MarketSnapshot(
        id="real-paper-close-mark",
        symbol=opportunity.symbol,
        market_type="spot",
        exchange=paper_internal_config.market_data.ccxt_exchange,
        price=2_100.0,
        snapshot_at=datetime.now(timezone.utc).isoformat(),
    ))
    closed = close_position_routed(
        position["id"],
        "take_profit",
        decision_source="e2e_real_paper_roundtrip",
    )

    close_order = repo.get_order(closed["order_id"])
    close_fill = next(
        row for row in repo.list_fills(limit=10) if row["id"] == closed["fill_id"]
    )
    position = repo._select_one("positions", position["id"])
    assert close_order is not None
    assert position is not None
    assert close_order["proposal_id"] == proposal["id"]
    assert close_order["position_id"] == position["id"]
    assert close_order["client_order_id"] == f"close-{position['id']}"
    assert close_order["close_reason"] == "take_profit"
    assert close_fill["order_id"] == close_order["id"]
    assert {row["id"] for row in repo.list_orders(limit=10)} == {
        entry_order["id"], close_order["id"],
    }
    assert {row["id"] for row in repo.list_fills(limit=10)} == {
        entry_fill["id"], close_fill["id"],
    }
    assert [row["id"] for row in repo.list_positions(limit=10)] == [position["id"]]
    assert position["status"] == "closed"
    assert position["current_price"] == pytest.approx(close_fill["fill_price"])
    assert position["entry_price"] == pytest.approx(entry_fill["fill_price"])
    assert position["entry_fee"] == pytest.approx(entry_fill["fee"])
    assert position["exit_fee"] == pytest.approx(close_fill["fee"])

    expected_net_pnl = (
        (float(close_fill["fill_price"]) - float(entry_fill["fill_price"]))
        * float(position["quantity"])
        - float(entry_fill["fee"])
        - float(close_fill["fee"])
    )
    assert position["funding_cost"] == pytest.approx(0.0)
    assert position["funding_source"] == "not_applicable"
    assert position["realized_pnl"] == pytest.approx(expected_net_pnl)
    assert closed["realized_pnl"] == pytest.approx(expected_net_pnl)
    assert position["outcome_eligible"] == 1
    assert position["outcome_exclusion_reason"] == "eligible"
    assessment = assess_realized_position(position)
    assert assessment.eligible is True
    assert assessment.outcome_key == f"position:{position['id']}"

    attribution = [
        row for row in build_trade_attribution_rows(limit=10)
        if row["position_id"] == position["id"]
    ]
    assert len(attribution) == 1
    attributed = attribution[0]
    assert attributed["proposal_id"] == proposal["id"]
    assert attributed["order_id"] == entry_order["id"]
    assert attributed["opportunity_id"] == opportunity.id
    assert attributed["strategy_id"] == strategy.id
    assert attributed["risk_approved"] == 1
    assert attributed["realized_pnl"] == pytest.approx(expected_net_pnl)

    jobs = repo.list_outcome_hook_jobs(position_id=position["id"])
    assert {job["hook_type"] for job in jobs} == {
        "shadow_trades",
        "exit_breakeven_shadow",
    }
    assert all(job["position_id"] == position["id"] for job in jobs)
    assert all(job["status"] == "completed" for job in jobs)
    assert all(job["attempts"] == 1 for job in jobs)

    feedback = build_factor_feedback(limit=10, min_trades=1)
    scanner_feedback = next(
        row for row in feedback
        if row["group_type"] == "scanner"
        and row["factor_key"] == opportunity.scanner_family
    )
    assert scanner_feedback["trade_count"] == 1
    assert scanner_feedback["total_pnl"] == pytest.approx(
        round(expected_net_pnl, 4)
    )
