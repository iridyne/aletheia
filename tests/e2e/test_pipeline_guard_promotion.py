"""E2E: guard self-evolution chain — candidate → reachable → promoted to active.

The Tier-1 guard chain was dead (105 candidates, 0 promotions, doctor
loop.guard_promotion) because of three breaks (L-Bug-D):
  1. guard_candidate frontmatter was unparseable (# Title before the --- block);
  2. load_trading_guards only scanned knowledge/guards/, never guard_candidates/;
  3. candidate metadata lacked status / guard_id / a matchable applies_to.

This exercises the REAL chain end-to-end (no module mocking; real DB):
create a candidate → it is reachable by load_trading_guards(status="candidate") →
with a repeated losing pattern, run_promotion_check promotes it to active.
(B0 auto_promote_enabled is the production go-live toggle; the e2e default config
has it on so the mechanism itself is verified.)
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
                   closed_at=datetime.now(timezone.utc).isoformat())
    repo.insert_position(pos)


class TestGuardPromotionChain:
    def test_candidate_reachable_and_promoted(self, e2e_db):
        from aletheia.knowledge.manager import create_guard_candidate
        from aletheia.knowledge.guards import load_trading_guards
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

        # 2) reachable by the promoter, with applies_to bridged from flat fields
        cands = load_trading_guards(status="candidate")
        mine = [g for g in cands if g.guard_id == guard_id]
        assert mine, f"candidate not reachable by load_trading_guards: {[g.guard_id for g in cands]}"
        g = mine[0]
        assert g.applies_to.scanner_family == ["momentum"]
        assert g.applies_to.direction == ["long"]

        # 3) a repeated losing momentum/long pattern (>= min_matching_trades=3, 100% loss)
        for i in range(3):
            _losing_momentum_long_trade(i)

        # 4) promotion check (e2e default config has auto_promote_enabled=True)
        result = run_promotion_check()
        assert result.get("promotions", 0) >= 1, f"candidate should be promoted: {result}"

        # 5) the candidate is now an ACTIVE guard (moved to guards/, status=active)
        active = [g for g in load_trading_guards(status="active") if g.guard_id == guard_id]
        assert active, "promoted guard should now load as active"
        # ...and no longer appears as a candidate
        assert not any(g.guard_id == guard_id for g in load_trading_guards(status="candidate"))
