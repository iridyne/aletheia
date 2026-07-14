"""E2E: futures leverage identity and the local-paper funding boundary.

Leverage remains hash-covered Proposal intent, including on the PI submission path.
Local paper futures entry is intentionally unavailable until a trustworthy
per-Position funding producer exists: Risk must preserve the Proposal and leverage
facts while rejecting before Order/Position materialization. Exchange-side leverage
round-trips remain covered by the demo/live adapter tests.

No module-to-module mocking — each Proposal goes through the real Execution Gateway
and Risk Engine, then proves that no paper broker side effect occurred.
"""

from aletheia.storage.models import Opportunity, StrategyRecord
from aletheia.storage import repositories as repo


class TestFuturesLeverageAcceptance:
    def test_paper_futures_entry_rejected_without_funding_producer(self, e2e_db):
        from aletheia.execution.gateway import submit_proposal

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
        assert res["status"] == "rejected", res
        assert "local_paper_futures_funding_unavailable" in res["risk_violations"]
        proposal = repo.get_trade_proposal(res["proposal_id"])
        assert proposal["leverage"] == 3
        assert proposal["opportunity_id"] == opp.id
        assert proposal["strategy_id"] == strat.id
        assert repo.list_orders() == []
        assert repo.list_positions() == []

    async def test_pi_decision_futures_proposal_persists_leverage(self, e2e_db):
        """The PI path preserves futures identity and clamped leverage on rejection."""
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
        market_data.latest_snapshot = lambda s, market_type=None: {"price": 150.0}
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

        assert result and result.get("status") == "rejected", result
        assert "local_paper_futures_funding_unavailable" in result["risk_violations"]
        proposal = repo.get_trade_proposal(result["proposal_id"])
        assert proposal["market_type"] == "usdt_futures"
        # SOL is mid-tier → demo cap 3; the PI's request of 10 must be clamped to 3.
        assert proposal["leverage"] == 3
        assert proposal["opportunity_id"] == opp.id
        assert repo.list_orders() == []
        assert repo.list_positions() == []
