"""The one merge-blocking Golden Path for the admitted active runtime scope."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.mark.asyncio
async def test_btc_spot_paper_internal_golden_path(e2e_db, monkeypatch, tmp_path):
    """One real causal chain from opportunity through genuine outcome and review.

    The LLM response is deterministic and external I/O is contained. No business
    module between the System Operator command and durable side effects is mocked.
    """
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import MarketSnapshot, Opportunity

    from aletheia.agent_runtime import runtime_session as rs

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.agent.permission_mode = "Paper"
    cfg.agent.autonomous_learning = False
    cfg.agent.bypass_distress_gate = True
    save_config(cfg)

    # Session A owns everything produced in this boot (opportunity → position).
    rs.reset_current_session()
    session_a = rs.start_runtime_session(cfg)

    snapshot = MarketSnapshot(
        id="golden-snapshot",
        symbol="BTC/USDT",
        market_type="spot",
        exchange="fixture",
        price=60_000.0,
        volume_24h=1_000_000_000.0,
        snapshot_at=datetime.now(timezone.utc).isoformat(),
    )
    opportunity = Opportunity(
        id="golden-opportunity",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        thesis="Deterministic Golden Path opportunity",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        scanner_family="momentum",
    )
    repo.insert_market_snapshot(snapshot)
    repo.insert_opportunity(opportunity)

    # The Operator may consume only the durable Filter/Ranker queue carrier.
    # Run the real paper-trader handoff instead of letting this golden path
    # bypass the production Opportunity -> signal_pending boundary.
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.agents.paper_trader import run_paper_trader

    # Production bootstrap initializes the permission registry before scheduling
    # agents; this isolated E2E fixture must reproduce that boundary explicitly.
    init_tool_registry()
    ranker_result = await run_paper_trader(
        permission_mode="Paper",
        run_id="golden-path-ranker",
    )
    assert ranker_result["errors"] == []
    pending = repo.find_pending_pi_decision_for_opportunity(opportunity.id)
    assert pending is not None
    assert pending["symbol"] == opportunity.symbol
    assert pending["direction"] == opportunity.direction_bias
    assert pending["market_type"] == opportunity.market

    async def deterministic_structured(*args, **kwargs):
        return ({
            "action": "trade",
            "symbol": "BTC/USDT",
            "direction": "long",
            "conviction": 90,
            "reasoning": "The fixed fixture accepts the single admitted opportunity.",
            "bear_case": "The price can reverse after entry.",
            "market_type": "spot",
            "leverage": 1,
            "stop_price": 58_800.0,
            "take_profit": 62_400.0,
            "parameter_rationale": {
                "entry_price": "Use the current fixture snapshot.",
                "stop_loss": "Limit the planned downside to two percent.",
                "take_profit": "Target twice the planned price risk.",
                "notional_usdt": "Use the bounded paper sizing result.",
                "leverage": "Spot uses one times leverage.",
            },
            "risk_factors": ["fixture reversal"],
            "supporting_signals": ["fixed long opportunity"],
            "opposing_signals": ["fixture reversal"],
            "key_assumptions": ["the fixture snapshot is current"],
        }, None)

    async def deterministic_review(*args, **kwargs):
        return (
            "The fixed BTC paper trade kept its opportunity, decision, proposal, "
            "risk approval, fill, position, and verified close connected. The entry "
            "followed the declared long opportunity, the protective prices were fixed "
            "before execution, and the close used exact local fill evidence. This review "
            "is deterministic test evidence; it does not claim that the strategy will "
            "perform similarly outside this replay scenario.",
            None,
        )

    monkeypatch.setattr(
        "aletheia.agents.helpers.llm_structured", deterministic_structured
    )
    monkeypatch.setattr("aletheia.agents.helpers.llm_complete", deterministic_review)
    from aletheia.agent_shell import reflection

    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json")

    from aletheia.agent_shell.commands import evaluate_trading_opportunity

    command_result = await evaluate_trading_opportunity({})
    assert command_result["action"] == "trade"
    assert command_result["proposal_status"] == "executed"

    db = e2e_db
    opportunities = db.execute("SELECT * FROM opportunities").fetchall()
    decisions = db.execute("SELECT * FROM pi_decisions WHERE action='trade'").fetchall()
    proposals = db.execute("SELECT * FROM trade_proposals").fetchall()
    risk_decisions = db.execute("SELECT * FROM risk_decisions").fetchall()
    orders = db.execute("SELECT * FROM orders").fetchall()
    fills = db.execute("SELECT * FROM fills").fetchall()
    positions = db.execute("SELECT * FROM positions").fetchall()
    assert tuple(map(len, (
        opportunities, decisions, proposals, risk_decisions, orders, fills, positions,
    ))) == (1, 1, 1, 1, 1, 1, 1)

    decision = decisions[0]
    proposal = proposals[0]
    risk = risk_decisions[0]
    order = orders[0]
    fill = fills[0]
    position = positions[0]
    assert decision["opportunity_id"] == opportunity.id
    assert decision["proposal_id"] == proposal["id"]
    assert proposal["opportunity_id"] == opportunity.id
    assert risk["proposal_id"] == proposal["id"]
    assert risk["approved"] == 1
    assert order["proposal_id"] == proposal["id"]
    assert order["risk_decision_id"] == risk["id"]
    assert fill["order_id"] == order["id"]
    assert position["proposal_id"] == proposal["id"]
    assert position["order_id"] == order["id"]
    assert position["opportunity_id"] == opportunity.id

    # --- Provenance: every new entity in the chain carries Session A's identity,
    # the producer commit equals the boot commit, and the config fingerprint is
    # consistent across the whole chain. This is the "full attribution" guarantee.
    chain_rows = [opportunities[0], decision, proposal, risk, order, fill, position]
    for row in chain_rows:
        assert row["runtime_session_id"] == session_a["session_id"], (
            f"chain row not attributed to Session A: {dict(row).get('runtime_session_id')}"
        )
        assert row["producer_commit"] == session_a["boot_commit"]
        assert row["behavior_fingerprint"] == session_a["behavior_fingerprint"]
    assert len({row["behavior_fingerprint"] for row in chain_rows}) == 1, (
        "config fingerprint must be identical across one trade chain"
    )

    # --- Restart: end Session A, boot Session B. Session B will CLOSE the position
    # opened by Session A — the two producers must stay distinguishable.
    rs.end_runtime_session(clean=True)
    session_b = rs.start_runtime_session(cfg)
    assert session_b["session_id"] != session_a["session_id"]
    assert session_b["runtime_id"] == session_a["runtime_id"]

    from aletheia.execution.verified_exit import VerifiedExitFill, VerifiedExitResult
    from aletheia.portfolio.outcome_closure import close_position_verified

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    evidence = VerifiedExitResult(
        state="verified_fill",
        reason="golden_path_local_fill",
        broker_id="paper_internal",
        account_environment="local_fixture",
        account_scope="golden_path",
        execution_mode="paper_internal",
        position_id=position["id"],
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        position_absent=True,
        history_complete=True,
        history_start_ms=now_ms - int(timedelta(minutes=5).total_seconds() * 1000),
        history_end_ms=now_ms + 1,
        pages=1,
        source_endpoint="deterministic_fixture",
        fills=(VerifiedExitFill(
            fill_id="golden-exit-fill",
            order_id="golden-exit-order",
            price=61_200.0,
            quantity=float(position["quantity"]),
            fee=61_200.0 * float(position["quantity"]) * 0.0006,
            fee_currency="USDT",
            realized_pnl=None,
            filled_at_ms=now_ms,
        ),),
    )
    close_result = close_position_verified(
        position["id"], evidence, reason="take_profit", source="golden_path"
    )
    assert close_result["closed"] is True

    closed = repo._select_one("positions", position["id"])
    from aletheia.learning.outcomes import assess_realized_position

    genuine = assess_realized_position(closed)
    assert genuine.eligible is True
    assert closed["outcome_eligible"] == 1
    assert closed["exit_fill_verified"] == 1
    assert closed["close_category"] == "strategy"

    # Created by Session A, closed by Session B — the position is NOT collapsed
    # onto the last run. position_created_session != position_closed_session.
    assert closed["runtime_session_id"] == session_a["session_id"]
    assert closed["closed_runtime_session_id"] == session_b["session_id"]
    assert closed["runtime_session_id"] != closed["closed_runtime_session_id"]
    assert closed["closed_producer_commit"] == session_b["boot_commit"]
    rs.reset_current_session()

    from aletheia.agents import post_trade_reviewer
    from aletheia.agent_runtime.tool_registry import init_tool_registry

    # The reviewer imports llm_complete into its module namespace. Patch that
    # external boundary too so collection order cannot resurrect a real provider.
    monkeypatch.setattr(post_trade_reviewer, "llm_complete", deterministic_review)
    init_tool_registry()
    review_result = await post_trade_reviewer.run_post_trade_reviewer(
        permission_mode="Full"
    )
    assert not review_result["errors"]
    reviews = db.execute(
        "SELECT * FROM knowledge_items WHERE item_type='post_trade_review'"
    ).fetchall()
    assert len(reviews) == 1
    assert any(item.startswith("post_trade_review:") for item in review_result["outputs"])

    # The final audit proves a single closed causal chain, not merely table counts.
    assert db.execute(
        "SELECT COUNT(*) FROM positions p "
        "JOIN orders o ON o.id=p.order_id "
        "JOIN risk_decisions r ON r.id=o.risk_decision_id "
        "JOIN trade_proposals t ON t.id=r.proposal_id "
        "JOIN pi_decisions d ON d.proposal_id=t.id "
        "JOIN opportunities x ON x.id=d.opportunity_id "
        "WHERE p.id=? AND p.outcome_eligible=1",
        (position["id"],),
    ).fetchone()[0] == 1
