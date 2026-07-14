"""E2E: local-paper futures leverage and complete funding economics.

The public funding schedule is seeded as the external observation boundary.  Every
business fact after that boundary is produced by the real Gateway, Risk Engine,
paper broker, central close, Outcome, and Attribution paths.  The resulting source
is explicitly ``local_simulation``; exchange-account funding remains a separate
broker-ledger requirement.
"""

from datetime import datetime, timedelta, timezone
import json

import pytest

from aletheia.storage.models import Opportunity, StrategyRecord
from aletheia.storage import repositories as repo


def _seed_fresh_schedule(symbol: str) -> tuple[datetime, datetime]:
    observed = datetime.now(timezone.utc) - timedelta(minutes=1)
    target = observed + timedelta(hours=4)
    repo.insert_funding_rate_snapshot({
        "symbol": symbol,
        "exchange": "okx",
        "funding_rate": 0.0001,
        "funding_rate_annualized": 0.1095,
        "next_funding_time": target.isoformat(),
        "observed_at": observed.isoformat(),
    })
    return observed, target


@pytest.fixture
def local_paper_futures_config(e2e_db):
    from aletheia.config.loader import load_config, save_config

    original = load_config()
    configured = original.model_copy(deep=True)
    configured.agent.permission_mode = "Paper"
    configured.exchange.paper_execution_mode = "paper_internal"
    configured.exchange.enabled_markets["usdt_futures"] = True
    save_config(configured)
    try:
        yield configured
    finally:
        save_config(original)


class TestFuturesLeverageAcceptance:
    def test_paper_futures_round_trip_has_simulated_funding_and_eligible_outcome(
        self, local_paper_futures_config, monkeypatch,
    ):
        from aletheia.analytics.trade_attribution import build_trade_attribution_rows
        from aletheia.execution.gateway import submit_proposal
        from aletheia.learning.outcomes import assess_realized_position
        from aletheia.portfolio.position_manager import close_position_routed

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
        _observed, next_settlement = _seed_fresh_schedule(opp.symbol)

        # 3x BTC futures, notional 60 → weighted notional 180.
        entry = 60000.0
        res = submit_proposal(
            symbol="BTC/USDT", market_type="usdt_futures", direction="long",
            quantity=round(60.0 / entry, 6), price=entry, leverage=3,
            execution_mode="paper_internal",
            strategy_id=strat.id, opportunity_id=opp.id,
        )
        assert res["status"] == "executed", res
        proposal = repo.get_trade_proposal(res["proposal_id"])
        assert proposal["leverage"] == 3
        assert proposal["opportunity_id"] == opp.id
        assert proposal["strategy_id"] == strat.id
        entry_order = repo.get_order(res["order_id"])
        entry_fill = next(
            row for row in repo.list_fills(limit=10) if row["id"] == res["fill_id"]
        )
        opened = repo._select_one("positions", res["position_id"])
        assert opened["leverage"] == 3
        assert opened["proposal_id"] == proposal["id"]
        assert entry_order["position_id"] == opened["id"]
        assert entry_order["filled_at"] == entry_fill["filled_at"] == opened["opened_at"]

        closed_result = close_position_routed(
            opened["id"],
            "take_profit",
            fallback_exit_price=entry * 1.01,
            decision_source="e2e_local_futures_funding",
        )
        closed = repo._select_one("positions", opened["id"])
        close_order = repo.get_order(closed_result["order_id"])
        close_fill = next(
            row
            for row in repo.list_fills(limit=10)
            if row["id"] == closed_result["fill_id"]
        )

        assert close_order["position_id"] == closed["id"]
        assert close_fill["order_id"] == close_order["id"]
        assert closed["funding_source"] == "local_simulation"
        # Entry and close occur before the seeded next settlement: zero is an
        # observed zero-event window, not a guessed absence of funding.
        assert closed["funding_cost"] == pytest.approx(0.0)
        expected_net = (
            (float(close_fill["fill_price"]) - float(entry_fill["fill_price"]))
            * float(closed["quantity"])
            - float(entry_fill["fee"])
            - float(close_fill["fee"])
        )
        assert closed["realized_pnl"] == pytest.approx(expected_net)
        assert closed["outcome_eligible"] == 1
        assert closed["outcome_exclusion_reason"] == "eligible"
        funding = json.loads(closed["reconciliation_evidence"])["funding"]
        assert funding["status"] == "complete"
        assert funding["source"] == "local_simulation"
        assert funding["events"] == []
        assert datetime.fromisoformat(
            funding["next_settlement_anchor"]["scheduled_at"]
        ) == next_settlement
        assert assess_realized_position(closed).eligible is True
        attribution = [
            row
            for row in build_trade_attribution_rows(limit=10)
            if row["position_id"] == closed["id"]
        ]
        assert len(attribution) == 1
        assert attribution[0]["opportunity_id"] == opp.id
        assert attribution[0]["realized_pnl"] == pytest.approx(expected_net)

    async def test_pi_decision_futures_proposal_persists_leverage(
        self, local_paper_futures_config,
    ):
        """The PI path preserves futures identity and clamped leverage on execution."""
        from aletheia.agent_shell.trading_decision import TradingDecision, submit_decision_as_proposal
        from aletheia.trading import market_data

        opp = Opportunity(symbol="SOL/USDT", market="usdt_futures", direction_bias="long",
                          confidence="high", confidence_score=75, source_agent="market_observer",
                          scanner_family="momentum")
        repo.insert_opportunity(opp)
        _seed_fresh_schedule(opp.symbol)

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

        assert result and result.get("status") == "executed", result
        proposal = repo.get_trade_proposal(result["proposal_id"])
        assert proposal["market_type"] == "usdt_futures"
        # SOL is mid-tier → demo cap 3; the PI's request of 10 must be clamped to 3.
        assert proposal["leverage"] == 3
        assert proposal["opportunity_id"] == opp.id
        order = repo.get_order(result["order_id"])
        position = repo._select_one("positions", result["position_id"])
        assert order["proposal_id"] == proposal["id"]
        assert order["position_id"] == position["id"]
        assert position["leverage"] == 3
        assert position["opportunity_id"] == opp.id
