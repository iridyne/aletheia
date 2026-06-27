"""E2E: futures leverage acceptance criteria via the paper pipeline.

Validates the Round 2 futures enablement WITHOUT a live exchange — the paper path
exercises every Aletheia-side node the demo path does (leverage persistence,
liquidation estimate, account-leverage ratio, full causal chain). The only thing
paper does NOT exercise is the exchange-side set_leverage round-trip (demo/live
only), which is unit-tested separately.

Acceptance criteria (futures_round2_deferred §Validation):
  1. open a futures position with leverage > 1
  2. liquidation price displayed/recorded correctly
  3. account leverage ratio updates
  4. CausalChain shows all nodes (opportunity → proposal → position)

No module-to-module mocking — proposal goes through the real Execution Gateway,
risk engine, and paper broker.
"""

from aletheia.storage.models import Opportunity, StrategyRecord
from aletheia.storage import repositories as repo


class TestFuturesLeverageAcceptance:
    def test_paper_futures_open_with_leverage(self, e2e_db):
        from aletheia.execution.gateway import submit_proposal
        from aletheia.risk.engine import check_account_leverage
        from aletheia.config.loader import load_config

        # A perp opportunity carrying its funding/OI state (F4) + a linked strategy.
        opp = Opportunity(
            symbol="BTC/USDT", market="usdt_futures", direction_bias="long",
            confidence="high", confidence_score=82, source_agent="market_observer",
            scanner_family="momentum", funding_rate=0.0001, open_interest=1.0e9,
            oi_delta_1h=2.5,
        )
        repo.insert_opportunity(opp)
        strat = StrategyRecord(name="fut-e2e", state="paper_running")
        repo.insert_strategy(strat)

        # 3x BTC futures, notional 60 → weighted notional 180.
        entry = 60000.0
        res = submit_proposal(
            symbol="BTC/USDT", market_type="usdt_futures", direction="long",
            quantity=round(60.0 / entry, 6), price=entry, leverage=3,
            execution_mode="paper_internal",
            strategy_id=strat.id, opportunity_id=opp.id,
        )
        assert res["status"] == "executed", res
        pos = repo._select_one("positions", res["position_id"])

        # (1) leverage > 1 persisted
        assert pos["leverage"] == 3
        # (2) liquidation price recorded, below entry for a long, sane magnitude
        assert pos["liquidation_price"] is not None
        assert 0 < pos["liquidation_price"] < entry
        # (4) full causal chain on the position row
        assert pos["opportunity_id"] == opp.id
        assert pos["proposal_id"]
        assert pos["strategy_id"] == strat.id

        # (3) account leverage ratio reflects the leveraged exposure.
        # demo equity falls back to starting_capital_usdt (100) with no cached state;
        # weighted notional = 60 * 3 = 180 → ratio 1.8x, under the 3.0x demo cap.
        cfg = load_config()
        acct = check_account_leverage(cfg)
        assert acct["ratio"] is not None
        assert acct["ratio"] > 60.0 / acct["equity"], "ratio must be amplified by leverage"

    async def test_pi_decision_futures_proposal_persists_leverage(self, e2e_db):
        """The PI-decision submit path (F2) carries market_type + clamped leverage all
        the way to a persisted Position (F1)."""
        from aletheia.agent_shell.trading_decision import TradingDecision, submit_decision_as_proposal
        from aletheia.trading import market_data

        opp = Opportunity(symbol="SOL/USDT", market="usdt_futures", direction_bias="long",
                          confidence="high", confidence_score=75, source_agent="market_observer",
                          scanner_family="momentum")
        repo.insert_opportunity(opp)

        # Stub the snapshot lookup the submit path uses for price (market data is the
        # network boundary; everything downstream is real).
        import aletheia.agent_shell.trading_decision as td
        orig = market_data.latest_snapshot
        market_data.latest_snapshot = lambda s: {"price": 150.0}
        try:
            decision = TradingDecision(
                action="trade", symbol="SOL/USDT", direction="long",
                conviction=80, reasoning="strong perp setup",
                position_size_usdt=60.0, market_type="usdt_futures", leverage=10,
                opportunity_id=opp.id,
            )
            result = await submit_decision_as_proposal(decision, execution_mode="paper_internal")
        finally:
            market_data.latest_snapshot = orig

        assert result and result.get("status") == "executed", result
        pos = repo._select_one("positions", result["position_id"])
        assert pos["market_type"] == "usdt_futures"
        # SOL is mid-tier → demo cap 3; the PI's request of 10 must be clamped to 3.
        assert pos["leverage"] == 3
        assert pos["liquidation_price"] is not None
