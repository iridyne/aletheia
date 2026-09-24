"""Verified external closes → portfolio, equity-curve API and daily PnL reads."""

from datetime import datetime, timezone, timedelta
from functools import partial

from tests.test_outcome_closure import (
    _insert_closed_position_via_producer,
    clean_window_admits,
)


_closed = partial(
    _insert_closed_position_via_producer,
    symbol="ETH/USDT", market_type="spot", direction="long",
    quantity=1.0, entry_price=200.0,
)


class TestClosedPositionToPortfolio:
    def test_realized_pnl_appears_in_summary(self, e2e_db):
        _closed(
            position_id="closed", execution_mode="bitget_demo", realized_pnl=3.82,
            entry_fee=0.1, exit_fee=0.08,
            closed_at=datetime.now(timezone.utc).isoformat(),
        )
        from aletheia.portfolio.pnl import compute_portfolio

        summary = compute_portfolio(execution_mode="bitget_demo")
        assert summary["realized_pnl"] == 3.82

    def test_mode_isolation(self, e2e_db):
        now = datetime.now(timezone.utc).isoformat()
        _closed(position_id="bitget", execution_mode="bitget_demo", realized_pnl=10.0, closed_at=now)
        _closed(position_id="okx", execution_mode="okx_demo", realized_pnl=25.0, closed_at=now)

        from aletheia.portfolio.pnl import compute_portfolio

        bitget_summary = compute_portfolio(execution_mode="bitget_demo")
        assert bitget_summary["realized_pnl"] == 10.0
        assert bitget_summary["all_realized_pnl"] == 35.0
        assert compute_portfolio(execution_mode="okx_demo")["realized_pnl"] == 25.0


class TestEquityCurveData:
    def test_equity_curve_builds_from_positions(self, e2e_db):
        now = datetime.now(timezone.utc)
        for i in range(5):
            _closed(
                position_id=f"close-{i}", execution_mode="bitget_demo",
                realized_pnl=float(i + 1),
                closed_at=(now - timedelta(minutes=5-i)).isoformat(),
            )

        from aletheia.server.routes.analytics import get_equity_curve

        curve = get_equity_curve(execution_mode="bitget_demo")
        assert curve["eligible_outcome_count"] == 5
        assert [point["cumulative_pnl"] for point in curve["genuine_points"]] == [1, 3, 6, 10, 15]


class TestTodayRealized:
    """The compatibility daily-PnL read keeps its date and account filters."""

    def test_today_pnl_sums_correctly(self, e2e_db):
        now = datetime.now(timezone.utc)
        for position_id, pnl, closed_at in (
            ("loss", -5.0, now), ("gain", 3.0, now),
            ("yesterday", -100.0, now - timedelta(days=1)),
        ):
            # This test is about day bucketing, not outcome eligibility: an
            # earlier calendar day may legitimately sit outside the clean window,
            # so the expectation is derived from the anchor rather than assumed.
            _closed(
                position_id=position_id, execution_mode="okx_demo",
                realized_pnl=pnl, closed_at=closed_at.isoformat(),
                expected_outcome_eligible=clean_window_admits(closed_at.isoformat()),
            )
        from aletheia.portfolio.pnl import today_realized_pnl

        assert today_realized_pnl(execution_mode="okx_demo") == -2.0

    def test_today_pnl_mode_filter(self, e2e_db):
        now = datetime.now(timezone.utc).isoformat()
        _closed(position_id="bitget", execution_mode="bitget_demo", realized_pnl=10.0, closed_at=now)
        _closed(position_id="okx", execution_mode="okx_demo", realized_pnl=20.0, closed_at=now)
        from aletheia.portfolio.pnl import today_realized_pnl

        assert today_realized_pnl(execution_mode="bitget_demo") == 10.0
        assert today_realized_pnl(execution_mode="okx_demo") == 20.0
        assert today_realized_pnl(execution_mode=None) == 30.0
