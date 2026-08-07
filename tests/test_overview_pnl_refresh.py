"""Overview PnL must keep polling and explain unavailable realized economics."""

from pathlib import Path
from datetime import datetime


ROOT = Path(__file__).resolve().parents[1]


def test_overview_portfolio_and_curve_poll_at_status_cadence() -> None:
    """Opening Overview must not freeze the money panel after its first fetch."""
    pnl_zone = (ROOT / "frontend/src/components/PnLZone.tsx").read_text(encoding="utf-8")
    chart = (ROOT / "frontend/src/charts/AccountEquityChart.tsx").read_text(encoding="utf-8")

    assert "const OVERVIEW_PNL_REFRESH_MS = 10_000" in pnl_zone
    assert "refetchInterval: OVERVIEW_PNL_REFRESH_MS" in pnl_zone
    assert "AccountEquityChart" in pnl_zone
    assert "getAccountEquityHistory" in chart
    assert "refetchInterval: 60_000" in chart


def test_overview_curve_uses_the_same_current_portfolio_reading() -> None:
    """The hero and provisional chart tail must advance from one query result."""
    pnl_zone = (ROOT / "frontend/src/components/PnLZone.tsx").read_text(encoding="utf-8")
    chart = (ROOT / "frontend/src/charts/AccountEquityChart.tsx").read_text(encoding="utf-8")

    assert "const uta = p?.uta_state" in pnl_zone
    assert "queryKey: ['utaState']" not in pnl_zone
    assert "currentPoint={totalRaw" in pnl_zone
    assert "rawSeries.update" in chart
    assert "chart.remove()" in chart
    assert "Full replacement is reserved for durable-history" in chart


def test_portfolio_response_timestamps_the_cached_computation(monkeypatch) -> None:
    """as_of remains stable for a cached reading and names its computation time."""
    from aletheia.server.routes import data as data_routes

    monkeypatch.setattr(data_routes, "_portfolio_cache", None)
    monkeypatch.setattr(
        data_routes,
        "compute_portfolio",
        lambda execution_mode=None: {"account_equity_usdt": 4321.0},
    )

    first = data_routes.get_portfolio()
    second = data_routes.get_portfolio()

    assert first == second
    assert first["account_equity_usdt"] == 4321.0
    assert datetime.fromisoformat(first["as_of"])


def test_overview_explains_unavailable_realized_pnl() -> None:
    """An honest unavailable result is actionable, never an unexplained dash."""
    pnl_zone = (ROOT / "frontend/src/components/PnLZone.tsx").read_text(encoding="utf-8")

    # Method B: partial sums shown with a completeness warning rather than blocking
    # the display entirely.  "unverified" is the user-visible flag for incomplete closes.
    assert "unverified" in pnl_zone
    assert "funding or close economics unavailable" in pnl_zone


def test_overview_curve_distinguishes_live_mark_from_verified_realized() -> None:
    """Raw/verified analysis remains separate from the single Overview curve."""
    chart = (ROOT / "frontend/src/charts/PnLChart.tsx").read_text(encoding="utf-8")
    overview_chart = (ROOT / "frontend/src/charts/AccountEquityChart.tsx").read_text(encoding="utf-8")
    client = (ROOT / "frontend/src/api/client.ts").read_text(encoding="utf-8")

    assert "current_mark_point" in client
    assert "Live mark (closed raw + open uPnL)" in chart
    assert "No verified strategy outcomes yet" in chart
    assert "raw_recorded_points" not in overview_chart
    assert "genuine_points" not in overview_chart
