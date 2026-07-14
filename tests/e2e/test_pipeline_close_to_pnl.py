"""Pipeline 4: 平仓 → realized_pnl → equity_curve → 前端显示.

Verifies that closed positions with realized_pnl correctly appear in:
- compute_portfolio() summary
- /api/analytics/equity-curve endpoint
- today_realized_pnl() for risk engine daily-loss check
"""

import pytest
from datetime import datetime, timezone, timedelta

from aletheia.storage.models import Position
from aletheia.storage import repositories as repo


class TestClosedPositionToPortfolio:
    """realized_pnl from close_position flows into compute_portfolio."""

    def test_realized_pnl_appears_in_summary(self, e2e_db):
        """Closed position's PnL shows up in compute_portfolio."""
        pos = Position(
            symbol="ETH/USDT",
            market_type="usdt_futures",
            direction="long",
            quantity=0.1,
            entry_price=1700.0,
            execution_mode="paper_internal",
            funding_cost=0.0,
            funding_source="local_simulation",
            status="open",
            opened_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        )
        repo.insert_position(pos)

        from aletheia.portfolio.pnl import close_position, compute_portfolio
        close_position(pos.id, exit_price=1740.0, reason="take_profit")

        summary = compute_portfolio(execution_mode="paper_internal")
        assert summary["realized_pnl"] != 0
        # Gross = (1740-1700)*0.1 = 4.0, minus fees
        assert summary["realized_pnl"] > 3.0

    def test_mode_isolation(self, e2e_db):
        """paper_internal and okx_demo PnL are isolated in compute_portfolio."""
        paper = Position(
            symbol="BTC/USDT", market_type="usdt_futures", direction="long",
            quantity=0.001, entry_price=60000.0, execution_mode="paper_internal",
            current_price=70000.0, status="closed", realized_pnl=10.0,
            entry_fee=0.0, exit_fee=0.0,
            funding_cost=0.0, funding_source="local_simulation",
            closed_at=datetime.now(timezone.utc).isoformat(),
        )
        okx = Position(
            symbol="ETH/USDT", market_type="usdt_futures", direction="short",
            quantity=0.1, entry_price=1800.0, current_price=1550.0,
            execution_mode="okx_demo", status="closed", realized_pnl=25.0,
            entry_fee=0.0, exit_fee=0.0,
            funding_cost=0.0, funding_source="broker_ledger",
            closed_at=datetime.now(timezone.utc).isoformat(),
        )
        repo.insert_position(paper)
        repo.insert_position(okx)

        from aletheia.portfolio.pnl import compute_portfolio
        paper_summary = compute_portfolio(execution_mode="paper_internal")
        assert paper_summary["realized_pnl"] == 10.0
        assert paper_summary["all_realized_pnl"] == 35.0

        okx_summary = compute_portfolio(execution_mode="okx_demo")
        assert okx_summary["realized_pnl"] == 25.0


class TestEquityCurveData:
    """Closed positions correctly form equity curve data."""

    def test_equity_curve_builds_from_positions(self, e2e_db):
        """The equity-curve query produces correct cumulative PnL from positions."""
        now = datetime.now(timezone.utc)
        for i in range(5):
            repo.insert_position(Position(
                symbol="ETH/USDT", market_type="spot", direction="long",
                quantity=0.1, entry_price=1700.0 + i,
                execution_mode="paper_internal", status="closed",
                realized_pnl=float(i + 1),
                closed_at=(now + timedelta(minutes=i)).isoformat(),
            ))

        # Replicate the equity-curve endpoint query directly (tests data flow, not HTTP)
        from aletheia.storage.sqlite import get_db
        db = get_db()
        rows = db.execute(
            "SELECT realized_pnl, closed_at FROM positions "
            "WHERE status='closed' AND closed_at IS NOT NULL "
            "ORDER BY closed_at ASC"
        ).fetchall()

        assert len(rows) == 5
        cum = 0.0
        for r in rows:
            cum += r["realized_pnl"]
        assert cum == 15.0  # 1+2+3+4+5


class TestTodayRealizedForRisk:
    """today_realized_pnl correctly feeds risk engine daily-loss check."""

    def test_today_pnl_sums_correctly(self, e2e_db):
        now = datetime.now(timezone.utc)
        repo.insert_position(Position(
            symbol="BTC/USDT", market_type="spot", direction="long",
            quantity=0.001, entry_price=60000.0,
            current_price=55000.0,
            execution_mode="okx_demo", status="closed",
            realized_pnl=-5.0,
            entry_fee=0.0, exit_fee=0.0,
            funding_cost=0.0, funding_source="not_applicable",
            closed_at=now.isoformat(),
        ))
        repo.insert_position(Position(
            symbol="ETH/USDT", market_type="spot", direction="short",
            quantity=0.01, entry_price=1800.0,
            current_price=1500.0,
            execution_mode="okx_demo", status="closed",
            realized_pnl=3.0,
            entry_fee=0.0, exit_fee=0.0,
            funding_cost=0.0, funding_source="not_applicable",
            closed_at=now.isoformat(),
        ))
        # Yesterday's trade should NOT count
        repo.insert_position(Position(
            symbol="SOL/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=70.0,
            execution_mode="okx_demo", status="closed",
            realized_pnl=-100.0,
            closed_at=(now - timedelta(days=1)).isoformat(),
        ))

        from aletheia.portfolio.pnl import today_realized_pnl
        result = today_realized_pnl(execution_mode="okx_demo")
        assert result == -2.0  # -5 + 3

    def test_today_pnl_mode_filter(self, e2e_db):
        now = datetime.now(timezone.utc)
        repo.insert_position(Position(
            symbol="BTC/USDT", market_type="spot", direction="long",
            quantity=0.001, entry_price=60000.0,
            current_price=70000.0,
            execution_mode="paper_internal", status="closed",
            realized_pnl=10.0,
            entry_fee=0.0, exit_fee=0.0,
            funding_cost=0.0, funding_source="not_applicable",
            closed_at=now.isoformat(),
        ))
        repo.insert_position(Position(
            symbol="ETH/USDT", market_type="spot", direction="long",
            quantity=0.01, entry_price=1800.0,
            current_price=3800.0,
            execution_mode="okx_demo", status="closed",
            realized_pnl=20.0,
            entry_fee=0.0, exit_fee=0.0,
            funding_cost=0.0, funding_source="not_applicable",
            closed_at=now.isoformat(),
        ))

        from aletheia.portfolio.pnl import today_realized_pnl
        assert today_realized_pnl(execution_mode="paper_internal") == 10.0
        assert today_realized_pnl(execution_mode="okx_demo") == 20.0
        assert today_realized_pnl(execution_mode=None) == 30.0
