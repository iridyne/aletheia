"""Overview PnL must keep polling and explain unavailable realized economics."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_overview_portfolio_and_curve_poll_at_status_cadence() -> None:
    """Opening Overview must not freeze the money panel after its first fetch."""
    pnl_zone = (ROOT / "frontend/src/components/PnLZone.tsx").read_text(encoding="utf-8")
    chart = (ROOT / "frontend/src/charts/PnLChart.tsx").read_text(encoding="utf-8")

    assert "const OVERVIEW_PNL_REFRESH_MS = 10_000" in pnl_zone
    assert "refetchInterval: OVERVIEW_PNL_REFRESH_MS" in pnl_zone
    assert "refetchInterval: 10_000" in chart


def test_overview_explains_unavailable_realized_pnl() -> None:
    """An honest unavailable result is actionable, never an unexplained dash."""
    pnl_zone = (ROOT / "frontend/src/components/PnLZone.tsx").read_text(encoding="utf-8")

    assert "not yet verifiable" in pnl_zone
    assert "closes need verification" in pnl_zone
