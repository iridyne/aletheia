"""Pipeline 5: 平仓 → post_trade_review → knowledge → ranker Learning proposal.

Verifies that trade outcomes remain queryable for Learning and that an
unmeasured lesson proposal stays on the ranker's zero-impact baseline.
"""

import pytest
from datetime import datetime, timezone, timedelta

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


class TestKnowledgeRankerBoundary:
    """Knowledge proposals stay neutral until their Learning path is measurable."""

    def test_unmeasured_lesson_penalty_remains_at_zero_delta(self, monkeypatch):
        """A proposed negative lesson is traced but cannot silently change ranker score."""
        from aletheia.agents import paper_trader_ranker as ranker
        from aletheia.regime.detector import MarketRegime

        opp = {
            "id": "opp-learning-boundary",
            "symbol": "SOL/USDT",
            "direction_bias": "long",
            "confidence": "high",
            "confidence_score": 78,
            "scanner_family": "momentum",
        }
        monkeypatch.setattr(
            ranker,
            "get_current_regime",
            lambda: MarketRegime(state="bull", confidence=0.8),
        )
        monkeypatch.setattr(ranker, "_memo_scoring", lambda *_: 0.0)

        monkeypatch.setattr(ranker, "_lesson_penalty", lambda *_: -12.0)
        trace = []
        proposed_score = ranker._compute_trade_score(
            opp, None, {"status": "watch"}, "high",
            direction="long", learning_trace=trace,
        )

        monkeypatch.setattr(ranker, "_lesson_penalty", lambda *_: 0.0)
        baseline_score = ranker._compute_trade_score(
            opp, None, {"status": "watch"}, "high", direction="long",
        )
        assert proposed_score == baseline_score
        case_trace = next(
            row for row in trace
            if row["path_id"] == "ranker_case_score"
        )
        assert case_trace["proposed_delta"] == -12.0
        assert case_trace["actual_delta"] == 0.0
        assert case_trace["authority_applied"] is False
        assert case_trace["reason"] == "missing_candidate_key"


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
