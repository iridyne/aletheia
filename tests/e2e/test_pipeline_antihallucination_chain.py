"""E2E: anti-hallucination chain — only full-chain real trades reach the retrieval pool.

The invariant being protected (LEARNING_LOOP_AUDIT §R0 + CR-EX-03):

    opportunity(id) → proposal(opportunity_id) → position(proposal_id, strategy_id,
    opportunity_id) → attribution row → trade_case_study(has_full_chain →
    high_confidence) → FTS retrieval pool

A trade that opened from a real, attributable opportunity becomes a high_confidence
case study and enters the retrieval pool the PI/ranker learn from. A trade missing
the opportunity link stays medium_confidence and is kept OUT of the pool — so the
learning loop cannot be fed by un-attributable (hallucination-prone) cases.

No module-to-module mocking: the proposal goes through the real Execution Gateway,
risk engine, paper broker, PnL close, case builder, and FTS retrieval. Only the DB is
the e2e in-process SQLite (network/LLM are never touched on the paper path).
"""

from aletheia.storage.models import Opportunity, StrategyRecord
from aletheia.storage import repositories as repo


def _run_paper_trade(symbol, entry, exit_price, *, opportunity_id, strategy_id):
    """Open a paper position through the real gateway, then close it. Returns position id."""
    from aletheia.execution.gateway import submit_proposal
    from aletheia.execution.verified_exit import VerifiedExitFill, VerifiedExitResult
    from aletheia.portfolio.outcome_closure import close_position_verified

    res = submit_proposal(
        symbol=symbol, market_type="spot", direction="long",
        quantity=round(60.0 / entry, 6), price=entry,
        execution_mode="paper_internal",
        strategy_id=strategy_id, opportunity_id=opportunity_id,
    )
    assert res["status"] == "executed", f"paper proposal should execute, got {res}"
    pos_id = res["position_id"]
    position = repo._select_one("positions", pos_id)
    quantity = float(position["quantity"])
    close_position_verified(
        pos_id,
        VerifiedExitResult(
            state="verified_fill", reason="local_paper_fill",
            broker_id="paper_internal", account_environment="local",
            account_scope="e2e", execution_mode="paper_internal",
            position_id=pos_id, symbol=symbol, market_type="spot",
            direction="long", position_absent=True, history_complete=True,
            history_start_ms=1, history_end_ms=2, pages=1,
            source_endpoint="local_paper_fill",
            fills=(VerifiedExitFill(
                fill_id=f"exit-{pos_id}", order_id=None, price=exit_price,
                quantity=quantity, fee=0.0, fee_currency="USDT",
                realized_pnl=None, filled_at_ms=2,
            ),),
        ),
        reason="take_profit",
    )
    return pos_id


class TestAntiHallucinationChain:
    def test_full_chain_reaches_pool_broken_chain_excluded(self, e2e_db):
        # ── Full chain: real opportunity + strategy linked all the way down ──
        opp = Opportunity(
            symbol="BTC/USDT", market="spot", direction_bias="long",
            confidence="high", confidence_score=80,
            source_agent="market_observer", scanner_family="momentum",
        )
        repo.insert_opportunity(opp)
        strat = StrategyRecord(name="mom-e2e", state="paper_running")
        repo.insert_strategy(strat)

        btc_pos = _run_paper_trade(
            "BTC/USDT", 60000.0, 63000.0,
            opportunity_id=opp.id, strategy_id=strat.id,
        )

        # The real broker stamped the full causal chain onto the Position (CR-EX-03).
        pos_row = repo._select_one("positions", btc_pos)
        assert pos_row["opportunity_id"] == opp.id
        assert pos_row["proposal_id"]
        assert pos_row["strategy_id"] == strat.id

        # ── Broken chain: a trade with NO opportunity link (un-attributable) ──
        eth_pos = _run_paper_trade(
            "ETH/USDT", 3000.0, 3300.0,
            opportunity_id=None, strategy_id=None,
        )
        eth_row = repo._select_one("positions", eth_pos)
        assert eth_row["opportunity_id"] is None

        # ── Build case studies from the real closed positions ──
        from aletheia.analytics.trade_case_builder import build_case_studies
        created = build_case_studies()
        by_pos = {c["position_id"]: c["case_study_id"] for c in created}
        assert btc_pos in by_pos, "full-chain closed trade must produce a case study"
        assert eth_pos in by_pos, "broken-chain trade still produces a case (just lower confidence)"

        # ── The gate: full chain → high_confidence; broken chain → medium ──
        btc_item = repo._select_one("knowledge_items", by_pos[btc_pos])
        eth_item = repo._select_one("knowledge_items", by_pos[eth_pos])
        assert btc_item["confidence"] == "high_confidence", (
            "a fully-attributed trade (opportunity+proposal+strategy) must be high_confidence"
        )
        assert eth_item["confidence"] == "medium_confidence", (
            "an un-attributable trade must NOT be promoted to high_confidence"
        )

        # ── The pool: retrieval is high_confidence-only, so only the real case surfaces ──
        from aletheia.knowledge.retrieval import retrieve_lessons
        lessons = retrieve_lessons("momentum win take_profit", k=10,
                                   item_types=["trade_case_study"])
        titles = " | ".join(l.title for l in lessons)
        assert any("BTC" in l.title for l in lessons), (
            f"full-chain case must be retrievable from the pool; got: {titles}"
        )
        assert not any("ETH" in l.title for l in lessons), (
            f"broken-chain (medium) case must be kept OUT of the pool; got: {titles}"
        )
