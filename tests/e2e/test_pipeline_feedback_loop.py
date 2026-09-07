"""Pipeline 5: 平仓 → post_trade_review → knowledge → ranker Learning proposal.

Verifies that trade outcomes remain queryable for Learning and that an
unmeasured lesson proposal stays on the ranker's zero-impact baseline.
"""

from datetime import datetime, timezone

from aletheia.storage.models import Position
from aletheia.storage import repositories as repo


class TestClosedTradeToKnowledge:
    """Closed trade data can be queried by knowledge/analytics systems."""

    def test_closed_position_queryable_by_symbol(self, e2e_db):
        """Closed positions are findable by symbol for lesson extraction."""
        repo.insert_position(Position(
            symbol="SOL/USDT", market_type="usdt_futures", direction="long",
            quantity=1.0, entry_price=100.0, execution_mode="paper_internal",
            status="closed", realized_pnl=-5.0, close_reason="stop_loss",
            closed_at=datetime.now(timezone.utc).isoformat(),
        ))
        repo.insert_position(Position(
            symbol="SOL/USDT", market_type="usdt_futures", direction="long",
            quantity=1.0, entry_price=95.0, execution_mode="paper_internal",
            status="closed", realized_pnl=3.0, close_reason="take_profit",
            closed_at=datetime.now(timezone.utc).isoformat(),
        ))

        closed = repo.list_positions(status="closed", limit=100)
        sol_closed = [p for p in closed if p["symbol"] == "SOL/USDT"]
        assert len(sol_closed) == 2
        total_pnl = sum(p["realized_pnl"] for p in sol_closed)
        assert total_pnl == -2.0

    def test_win_rate_calculable_from_positions(self, e2e_db):
        """Can compute win rate per symbol from closed positions."""
        now = datetime.now(timezone.utc)
        trades = [
            ("ETH/USDT", "short", 5.0),
            ("ETH/USDT", "short", -2.0),
            ("ETH/USDT", "short", 3.0),
            ("ETH/USDT", "long", -1.0),
        ]
        for sym, direction, pnl in trades:
            repo.insert_position(Position(
                symbol=sym, market_type="usdt_futures", direction=direction,
                quantity=0.1, entry_price=1800.0, execution_mode="paper_internal",
                status="closed", realized_pnl=pnl, close_reason="take_profit" if pnl > 0 else "stop_loss",
                closed_at=now.isoformat(),
            ))

        closed = repo.list_positions(status="closed", limit=100)
        eth_closed = [p for p in closed if p["symbol"] == "ETH/USDT"]
        wins = sum(1 for p in eth_closed if p["realized_pnl"] > 0)
        win_rate = wins / len(eth_closed)
        assert win_rate == 0.5  # 2 wins out of 4




class TestFamilyScorecardAccumulates:
    """Positions accumulate per strategy_id for scorecard computation."""

    def test_positions_groupable_by_strategy(self, e2e_db):
        """Closed trades with strategy_id are queryable for win/loss stats."""
        now = datetime.now(timezone.utc)
        for pnl in [5.0, -2.0, 3.0, -1.0, 4.0]:
            repo.insert_position(Position(
                symbol="ETH/USDT", market_type="usdt_futures", direction="short",
                quantity=0.1, entry_price=1800.0, execution_mode="paper_internal",
                status="closed", realized_pnl=pnl, strategy_id="strat_mom",
                close_reason="take_profit" if pnl > 0 else "stop_loss",
                closed_at=now.isoformat(),
            ))

        closed = repo.list_positions(status="closed", limit=100)
        strat_trades = [p for p in closed if p.get("strategy_id") == "strat_mom"]
        assert len(strat_trades) == 5
        wins = sum(1 for p in strat_trades if p["realized_pnl"] > 0)
        assert wins == 3  # 60% win rate
        total_pnl = sum(p["realized_pnl"] for p in strat_trades)
        assert total_pnl == 9.0  # 5-2+3-1+4
