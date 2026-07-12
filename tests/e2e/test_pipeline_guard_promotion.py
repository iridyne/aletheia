"""E2E: guard self-evolution chain under the P0 shadow-only regime (2026-07-12).

History: the Tier-1 guard chain was once dead (L-Bug-D: 105 candidates, 0 promotions);
it was repaired, then over-corrected — auto guards auto-promoted from bare historical
loss-rate correlation into active HARD BLOCKS while their bodies claimed "requires human
review before promotion", bypassing the Learning experiment/authority chain entirely.

The corrected chain (verified end-to-end here, real DB, no module mocking):
  1. an auto-generated candidate is reachable by load_trading_guards(status="candidate")
     and is created SHADOW-ONLY (observe-only);
  2. with a repeated losing pattern AND auto_promote_enabled=True, run_promotion_check
     still does NOT promote it — a learned guard needs experiment authority
     (ranker_learned_guard: AT-01 verdict + AT-02 path authority), which is fail-closed;
  3. the promotion MECHANISM itself is intact: an owner-authored (manual, non-shadow)
     candidate can still be promoted via the explicit owner route.
"""

from datetime import datetime, timezone

from aletheia.storage.models import Opportunity, TradeProposal, Position
from aletheia.storage import repositories as repo


def _losing_momentum_long_trade(i: int):
    """One closed momentum/long trade that lost money, with the full causal chain
    so build_trade_attribution_rows surfaces scanner_family + direction."""
    opp = Opportunity(symbol="SOL/USDT", market="usdt_futures", direction_bias="long",
                      confidence="high", source_agent="market_observer", scanner_family="momentum")
    repo.insert_opportunity(opp)
    prop = TradeProposal(symbol="SOL/USDT", market_type="usdt_futures", direction="long",
                         execution_mode="paper_internal", quantity=1.0, price=100.0,
                         opportunity_id=opp.id)
    repo.insert_trade_proposal(prop)
    pos = Position(symbol="SOL/USDT", market_type="usdt_futures", direction="long",
                   quantity=1.0, entry_price=100.0, current_price=95.0,
                   execution_mode="paper_internal", status="closed", realized_pnl=-5.0,
                   close_reason="stop_loss", proposal_id=prop.id, opportunity_id=opp.id,
                   closed_at=datetime.now(timezone.utc).isoformat(),
                   close_category="strategy", exit_price_source="local_fill",
                   exit_fill_verified=True, outcome_eligible=True,
                   outcome_exclusion_reason="eligible", outcome_semantics_version="oi-v1")
    repo.insert_position(pos)


class TestGuardPromotionChain:
    def test_auto_candidate_is_shadow_and_not_auto_promoted(self, e2e_db):
        from aletheia.knowledge.manager import create_guard_candidate
        from aletheia.knowledge.guards import load_trading_guards, guard_origin
        from aletheia.analytics.guard_promotion import run_promotion_check

        # 1) create an auto-style candidate (mirrors outcome_recorder's metadata)
        guard_id = "auto_momentum_long_solusdt"
        create_guard_candidate(
            title="Auto guard candidate: SOL/USDT momentum 4h loss",
            content="momentum/long on SOL/USDT showed repeated 4h losses",
            metadata={"guard_id": guard_id, "status": "candidate", "symbol": "SOL/USDT",
                      "scanner_family": "momentum", "direction": "long", "auto_generated": True,
                      "predicates": [{"field": "scanner_family", "op": "eq", "value": "momentum"}]},
        )

        # 2) reachable by the promoter, classified learned, and FORCED shadow-only at creation
        cands = load_trading_guards(status="candidate")
        mine = [g for g in cands if g.guard_id == guard_id]
        assert mine, f"candidate not reachable: {[g.guard_id for g in cands]}"
        g = mine[0]
        assert guard_origin(g) == "learned"
        assert g.shadow_only is True, "auto candidate must be created shadow-only"
        assert g.applies_to.symbol == ["SOL/USDT"]

        # 3) a repeated losing momentum/long pattern (>= min_matching_trades=3, 100% loss)
        for i in range(3):
            _losing_momentum_long_trade(i)

        # 4) a healthy durable reviewer checkpoint (so refreeze does not mask the result)
        from aletheia.learning.control import record_learning_job_success
        record_learning_job_success("lesson_reviewer")

        # 5) even with auto_promote_enabled=True and strong loss evidence, a learned
        #    shadow guard is NOT auto-promoted — it lacks experiment authority (fail-closed).
        from aletheia.config.loader import load_config
        cfg = load_config()
        cfg.guard.auto_promote_enabled = True
        result = run_promotion_check()
        assert result.get("promotions", 0) == 0, f"learned guard must not auto-promote: {result}"

        # 6) it stays a shadow candidate; it never became an active blocker
        assert not any(x.guard_id == guard_id for x in load_trading_guards(status="active"))
        still = [x for x in load_trading_guards(status="candidate") if x.guard_id == guard_id]
        assert still and still[0].shadow_only is True

    def test_manual_candidate_can_still_be_promoted_by_owner(self, e2e_db):
        """The promotion MECHANISM is intact: an owner-authored (non-shadow, non-learned)
        candidate is promotable via the explicit owner route."""
        from aletheia.knowledge.manager import create_guard_candidate
        from aletheia.knowledge.guards import load_trading_guards, guard_origin
        from aletheia.analytics.guard_promotion import promote_guard_candidate_to_active

        guard_id = "manual-sol-momentum-block"
        create_guard_candidate(
            title="Owner guard: block SOL momentum",
            content="Owner-authored block on SOL/USDT momentum longs.",
            metadata={"guard_id": guard_id, "status": "candidate", "symbol": "SOL/USDT",
                      "scanner_family": "momentum", "direction": "long",
                      "enforcement": "blocking", "block_candidate": True,
                      "predicates": [{"field": "scanner_family", "op": "eq", "value": "momentum"}]},
        )
        cand = [g for g in load_trading_guards(status="candidate") if g.guard_id == guard_id]
        assert cand and guard_origin(cand[0]) == "manual"
        assert cand[0].shadow_only is False  # manual candidate not forced shadow

        assert promote_guard_candidate_to_active(
            guard_id, actor_type="human", actor_id="owner", reason="owner_review") is True
        active = [g for g in load_trading_guards(status="active") if g.guard_id == guard_id]
        assert active, "owner-authored guard should promote to active"
        assert active[0].block_candidate is True
