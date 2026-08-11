"""The one merge-blocking Golden Path for the admitted active runtime scope.

MAINTENANCE NOTE — 继续优化 Golden Path 本身的回报已经极低，不推荐继续投入。
---------------------------------------------------------------------------
这个测试已经覆盖了 BTC/USDT spot long 的完整因果链：opportunity → decision →
proposal → risk → order → fill → position → close → attribution → review。
最后的 SQL JOIN 是真正的链路完整性证明，任何字段改名或接口不匹配都会在此炸掉。

**不值得继续做的方向**：
- 再加 ETH/short/futures/medium-confidence 的 Golden Path 变体 — 报酬递减
- 把更多 LLM mock 替换成真实调用 — 会引入不确定性，测试变得不可靠

**真正的盲区在别处**（见 test_pipeline_failure_paths.py）：
- 失败路径：信号不足时系统如何处理、风险拒绝后状态是否正确持久化
- 运行态健康：进程存活、调度是否在运行 — 这是 doctor 的职责，测试无法替代

Owner 决策（2026-07-14）：Golden Path 冻结在当前形态，不继续扩展。
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest


@pytest.mark.asyncio
async def test_btc_spot_paper_internal_golden_path(e2e_db, monkeypatch, tmp_path):
    """One real causal chain from opportunity through genuine outcome and review.

    The LLM response is deterministic and external I/O is contained. No business
    module between the System Operator command and durable side effects is mocked.
    """
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import (
        MarketSnapshot,
        Opportunity,
        UniverseAsset,
        UniverseFeatureSnapshot,
    )

    from aletheia.agent_runtime import runtime_session as rs

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.agent.permission_mode = "Paper"
    cfg.agent.autonomous_learning = False
    cfg.agent.bypass_distress_gate = True
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": False}
    cfg.exchange.preferred_market_type = "spot"
    cfg.exchange.symbol_list = ["BTC/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    cfg.market_data.observed_symbols = ["BTC/USDT"]
    cfg.market_data.observed_instruments = []
    cfg.market_data.observed_xstocks = []
    cfg.market_data.observed_stock_tokens = []
    cfg.market_data.prediction_enabled = False
    cfg.universe.enabled = True
    save_config(cfg)

    # Session A owns everything produced in this boot (opportunity → position).
    rs.reset_current_session()
    session_a = rs.start_runtime_session(cfg)

    snapshot = MarketSnapshot(
        id="golden-snapshot",
        symbol="BTC/USDT",
        market_type="spot",
        exchange=cfg.market_data.ccxt_exchange,
        price=60_000.0,
        volume_24h=1_000_000_000.0,
        snapshot_at=datetime.now(timezone.utc).isoformat(),
    )
    repo.insert_market_snapshot(snapshot)
    # The deterministic public-data boundary includes the exact spot book used
    # by proposal-time tradability. The same exact market snapshot above owns
    # the 24h volume input; neither input may fall back across venue or market.
    repo.insert_orderbook_snapshot({
        "symbol": "BTC/USDT",
        "exchange": cfg.market_data.ccxt_exchange,
        "market_type": "spot",
        "bid_price": 59_999.0,
        "ask_price": 60_001.0,
        "spread_bps": 0.33,
        "bid_depth_10bps_usd": 500_000.0,
        "ask_depth_10bps_usd": 500_000.0,
        "total_depth_10bps_usd": 1_000_000.0,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    })

    # The public market/LLM boundaries are deterministic. The fixture starts at
    # the deterministic producer admission boundary; Observer may only enrich
    # that existing lifecycle owner.
    import aletheia.agents.market_observer as market_observer
    from tests.observer_admission_fixture import admit_observer_instrument

    admitted_owner = admit_observer_instrument(
        monkeypatch,
        market_observer,
    )

    async def deterministic_market_context(
        symbols, *, market_type="spot", exchange=None, **_kwargs
    ):
        assert symbols == ["BTC/USDT"]
        assert market_type == "spot"
        return (
            "### BTC/USDT\n"
            "| TF | Close | SMA20 | SMA50 | RSI14 | ATR14 | Mom10 |\n"
            "| 1h | 60000 | 59000 | 58000 | 68 | 900 | +3.2% |\n"
            "Breakout momentum is confirmed above both moving averages.",
            {
                "symbols": {
                    "BTC/USDT": {"1h": {"close": 60_000.0, "rsi_14": 68.0}}
                },
                "errors": [],
                "exchange": exchange,
            },
        )

    async def deterministic_observer_read(*_args, **_kwargs):
        return ({
            "direction_bias": "long",
            "confidence": "high",
            "confidence_score": 90,
            "rationale": "BTC holds above SMA20 and SMA50 with positive momentum.",
            "key_observations": [
                "BTC 1h RSI is 68.",
                "BTC trades at 60000 above SMA20 59000 and SMA50 58000.",
            ],
        }, None)

    monkeypatch.setattr(
        "aletheia.trading.market_context.build_market_context",
        deterministic_market_context,
    )
    monkeypatch.setattr(
        "aletheia.agents.market_observer.llm_structured",
        deterministic_observer_read,
    )
    from aletheia.agent_runtime.orchestrator import run_agent

    observer_result = await run_agent("market_observer", trigger_type="manual")
    assert observer_result["status"] == "completed"
    assert observer_result["errors"] == []
    produced = repo.list_opportunities(limit=10)
    assert len(produced) == 1
    opportunity = Opportunity.model_validate(produced[0])
    assert opportunity.id == admitted_owner.id
    assert opportunity.source_agent == "universe_scanner"
    assert observer_result["outputs"] == [f"opportunity:{opportunity.id}"]

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
            "observed_facts": [{
                "fact": "The fixed long opportunity is queued.",
                "evidence_refs": [f"opportunity:{opportunity.id}"],
            }],
            "interpretations": ["The fixed setup supports a bounded paper entry."],
            "invalidation_conditions": ["Price reaches the declared stop."],
            "uncertainty_reason": "This is one deterministic fixture setup.",
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

    # Operator judgment requires a fresh exact-instrument technical snapshot;
    # a market price row alone is execution price authority, not technical
    # context authority.  Without this seed evaluate_trading_opportunity returns
    # evaluation_deferred with reason candidate_technical_snapshot_unavailable.
    repo.upsert_universe_asset(UniverseAsset(
        id="golden-path-asset",
        venue="fixture",
        market_type="spot",
        symbol="BTC/USDT",
    ))
    repo.insert_universe_feature_snapshots_batch([UniverseFeatureSnapshot(
        id="golden-path-feature",
        asset_id="golden-path-asset",
        ts=datetime.now(timezone.utc).isoformat(),
        rsi_14=68.0,
        return_1h=3.2,
        return_4h=4.0,
        return_24h=6.0,
        data_freshness_sec=0,
    )])

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
    proposal_meta = json.loads(proposal["agent_rationale"].split("\n---\n", 1)[1])
    proposal_motivation = proposal_meta["trade_motivation"]
    decision_motivation = json.loads(decision["evidence_manifest_json"])["trade_motivation"]
    assert decision_motivation == proposal_motivation
    assert proposal_motivation["schema_version"] == "structured-trade-motivation/v1"
    assert proposal_motivation["action_intent"] == "long"
    assert proposal_motivation["observed_facts"][0]["evidence_refs"] == [
        f"opportunity:{opportunity.id}"
    ]
    assert proposal_motivation["invalidation_conditions"] == [
        "Price reaches the declared stop."
    ]
    assert proposal_motivation["evidence_manifest"]
    attribution = json.loads(proposal["attribution_snapshot"])
    assert attribution["market_context"]["instrument"] == {
        "symbol": "BTC/USDT",
        "exchange": cfg.market_data.ccxt_exchange,
        "market_type": "spot",
    }
    assert attribution["market_context"]["tradability"]["is_tradable"] is True

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

    # Close through the same production router used by automatic/manual local
    # paper exits.  This must create the real close Order and Fill before the
    # central Outcome/feedback hooks run; a hand-built VerifiedExitResult would
    # prove only the second half of the business chain.
    from aletheia.portfolio.position_manager import close_position_routed

    close_result = close_position_routed(
        position["id"],
        "take_profit",
        fallback_exit_price=61_200.0,
        decision_source="golden_path",
    )
    assert close_result["closed"] is True

    closed = repo._select_one("positions", position["id"])
    close_order = repo.get_order(close_result["order_id"])
    close_fill = next(
        row
        for row in repo.list_fills(limit=10)
        if row["id"] == close_result["fill_id"]
    )
    assert close_order["proposal_id"] == proposal["id"]
    assert close_order["position_id"] == position["id"]
    assert close_order["client_order_id"] == f"close-{position['id']}"
    assert close_order["close_reason"] == "take_profit"
    assert close_fill["order_id"] == close_order["id"]
    assert {row["id"] for row in repo.list_orders(limit=10)} == {
        order["id"], close_order["id"],
    }
    assert {row["id"] for row in repo.list_fills(limit=10)} == {
        fill["id"], close_fill["id"],
    }
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

    # The same Position must be visible to the real attribution/outbox/feedback
    # consumers.  This closes the proof gap where earlier tests covered upstream
    # and downstream in separate transactions.
    from aletheia.analytics.factor_feedback import build_factor_feedback
    from aletheia.analytics.trade_attribution import build_trade_attribution_rows

    attribution = [
        row
        for row in build_trade_attribution_rows(limit=10)
        if row["position_id"] == position["id"]
    ]
    assert len(attribution) == 1
    assert attribution[0]["proposal_id"] == proposal["id"]
    assert attribution[0]["order_id"] == order["id"]
    assert attribution[0]["opportunity_id"] == opportunity.id
    assert attribution[0]["risk_approved"] == 1
    assert attribution[0]["realized_pnl"] == pytest.approx(closed["realized_pnl"])

    jobs = repo.list_outcome_hook_jobs(position_id=position["id"])
    assert {job["hook_type"] for job in jobs} == {
        "shadow_trades",
        "exit_trailing_recovery",
    }
    assert repo.get_exit_breakeven_shadow_assignment(position["id"]) is None
    jobs_by_type = {job["hook_type"]: job for job in jobs}
    assert jobs_by_type["shadow_trades"]["status"] == "completed"
    assert jobs_by_type["shadow_trades"]["attempts"] == 1
    assert jobs_by_type["exit_trailing_recovery"]["status"] == "pending"
    assert jobs_by_type["exit_trailing_recovery"]["attempts"] == 0

    feedback = build_factor_feedback(limit=10, min_trades=1)
    scanner_feedback = next(
        row
        for row in feedback
        if row["group_type"] == "scanner"
        and row["factor_key"] == opportunity.scanner_family
    )
    assert scanner_feedback["trade_count"] == 1
    assert scanner_feedback["total_pnl"] == pytest.approx(
        round(float(closed["realized_pnl"]), 4)
    )
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
