"""E2E: anti-hallucination chain — only full-chain real trades reach the retrieval pool.

The invariant being protected (LEARNING_LOOP_AUDIT §R0 + CR-EX-03):

    opportunity(id) → proposal(opportunity_id) → position(proposal_id, strategy_id,
    opportunity_id) → verified attributable outcome → trade_case_study →
    FTS retrieval pool

A trade that opened from a real, attributable opportunity becomes a high_confidence
case study and enters the retrieval pool the PI/ranker learn from. A trade missing
the opportunity link is excluded before case creation — so the learning loop cannot
be fed by un-attributable (hallucination-prone) outcomes.

No module-to-module mocking: the proposal goes through the real Execution Gateway,
risk engine, paper broker, PnL close, case builder, and FTS retrieval. Only the DB is
the e2e in-process SQLite (network/LLM are never touched on the paper path).
"""

from datetime import datetime, timezone

from aletheia.storage.models import MarketSnapshot, Opportunity, StrategyRecord
from aletheia.storage import repositories as repo


def _run_paper_trade(
    symbol,
    entry,
    exit_price,
    *,
    opportunity_id,
    strategy_id,
    stop_price=None,
    take_profit=None,
    risk_reward=None,
    source="native",
):
    """Open a paper position through the real gateway, then close it. Returns position id."""
    from aletheia.execution.gateway import submit_proposal
    from aletheia.execution.verified_exit import VerifiedExitFill, VerifiedExitResult
    from aletheia.portfolio.outcome_closure import close_position_verified

    res = submit_proposal(
        symbol=symbol, market_type="spot", direction="long",
        quantity=round(60.0 / entry, 6), price=entry,
        execution_mode="paper_internal",
        strategy_id=strategy_id, opportunity_id=opportunity_id,
        stop_price=stop_price, take_profit=take_profit, risk_reward=risk_reward,
        source=source,
    )
    assert res["status"] == "executed", f"paper proposal should execute, got {res}"
    pos_id = res["position_id"]
    position = repo._select_one("positions", pos_id)
    quantity = float(position["quantity"])
    filled_at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    close_position_verified(
        pos_id,
        VerifiedExitResult(
            state="verified_fill", reason="local_paper_fill",
            broker_id="paper_internal", account_environment="local",
            account_scope="e2e", execution_mode="paper_internal",
            position_id=pos_id, symbol=symbol, market_type="spot",
            direction="long", position_absent=True, history_complete=True,
            history_start_ms=filled_at_ms - 1,
            history_end_ms=filled_at_ms + 1,
            pages=1,
            source_endpoint="local_paper_fill",
            fills=(VerifiedExitFill(
                fill_id=f"exit-{pos_id}",
                order_id=f"local-paper-exit-{pos_id}",
                price=exit_price,
                quantity=quantity, fee=0.0, fee_currency="USDT",
                realized_pnl=None, filled_at_ms=filled_at_ms,
            ),),
        ),
        reason="take_profit",
    )
    return pos_id


class TestAntiHallucinationChain:
    def test_full_chain_reaches_pool_broken_chain_excluded(self, e2e_db):
        from aletheia.config.loader import load_config

        # paper_internal + native proposals require a fresh market snapshot at
        # cfg.market_data.ccxt_exchange ("binance").  Seed it for the BTC trade.
        cfg = load_config()
        repo.insert_market_snapshot(MarketSnapshot(
            id="antihallucination-btc-snapshot",
            symbol="BTC/USDT",
            market_type="spot",
            exchange=cfg.market_data.ccxt_exchange,
            price=60_000.0,
            volume_24h=1_000_000_000.0,
            snapshot_at=datetime.now(timezone.utc).isoformat(),
        ))

        # ── Full chain: real opportunity + strategy linked all the way down ──
        opp = Opportunity(
            symbol="BTC/USDT", market="spot", direction_bias="long",
            confidence="high", confidence_score=80,
            source_agent="market_observer", scanner_family="momentum",
        )
        repo.insert_opportunity(opp)
        strat = StrategyRecord(name="mom-e2e", state="paper_running")
        repo.insert_strategy(strat)

        # BTC: full native path — needs a complete entry plan.
        # stop=58800 (−2%), tp=62400 (+4%), rr=2.0
        btc_pos = _run_paper_trade(
            "BTC/USDT", 60000.0, 63000.0,
            opportunity_id=opp.id, strategy_id=strat.id,
            stop_price=58_800.0, take_profit=62_400.0, risk_reward=2.0,
        )

        # The real broker stamped the full causal chain onto the Position (CR-EX-03).
        pos_row = repo._select_one("positions", btc_pos)
        assert pos_row["opportunity_id"] == opp.id
        assert pos_row["proposal_id"]
        assert pos_row["strategy_id"] == strat.id

        # ── Broken chain: a trade with NO opportunity link (un-attributable) ──
        # Use source="adopted" so the risk engine skips the native paper checks
        # (paper_market_data_unavailable, proposal_opportunity_required,
        # incomplete_entry_plan) that require a linked opportunity.  This mirrors
        # a reconciler-adopted position that is intentionally missing attribution.
        eth_pos = _run_paper_trade(
            "ETH/USDT", 3000.0, 3300.0,
            opportunity_id=None, strategy_id=None,
            source="adopted",
        )
        eth_row = repo._select_one("positions", eth_pos)
        assert eth_row["opportunity_id"] is None
        assert eth_row["outcome_eligible"] == 0
        assert eth_row["outcome_exclusion_reason"] == "missing_attribution"

        # ── Build case studies from the real closed positions ──
        from aletheia.analytics.trade_case_builder import build_case_studies
        result = build_case_studies()
        by_pos = {c["position_id"]: c["case_study_id"] for c in result["created"]}
        assert btc_pos in by_pos, "full-chain closed trade must produce a case study"
        assert eth_pos not in by_pos, "broken-chain trade must not enter the learning corpus"

        # ── The gate: only the fully attributed chain becomes a case study ──
        btc_item = repo._select_one("knowledge_items", by_pos[btc_pos])
        assert btc_item["confidence"] == "high_confidence", (
            "a fully-attributed trade (opportunity+proposal+strategy) must be high_confidence"
        )

        # ── The pool: only the real, attributable case can surface ──
        from aletheia.knowledge.retrieval import retrieve_lessons
        lessons = retrieve_lessons("momentum win take_profit", k=10,
                                   item_types=["trade_case_study"])
        titles = " | ".join(l.title for l in lessons)
        assert any("BTC" in l.title for l in lessons), (
            f"full-chain case must be retrievable from the pool; got: {titles}"
        )
        assert not any("ETH" in l.title for l in lessons), (
            f"broken-chain outcome must be kept OUT of the pool; got: {titles}"
        )
