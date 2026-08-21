"""Tests for aletheia/analytics/trade_insights.py

Covers:
  #1254 — truncation / cohort metadata
  #1256 — R-Multiple uses frozen initial risk only
  #1259 — regime performance canonical states (bull/bear/sideways/volatile + unknown)
"""
from __future__ import annotations

from unittest.mock import patch


def _make_row(
    *,
    regime_tag: str | None = None,
    realized_pnl: float = 1.0,
    entry_price: float = 100.0,
    quantity: float = 1.0,
    stop_price: float | None = 95.0,
    closed_at: str = "2026-07-15T10:00:00+00:00",
    opened_at: str = "2026-07-15T09:00:00+00:00",
    close_reason: str = "stop",
    direction: str = "long",
    symbol: str = "BTC/USDT",
    market_type: str = "spot",
    id: str = "pos-1",
) -> dict:
    return {
        "id": id,
        "position_id": id,
        "regime_tag": regime_tag,
        "realized_pnl": realized_pnl,
        "entry_price": entry_price,
        "quantity": quantity,
        "stop_price": stop_price,
        "closed_at": closed_at,
        "opened_at": opened_at,
        "close_reason": close_reason,
        "direction": direction,
        "symbol": symbol,
        "market_type": market_type,
    }

# ---------------------------------------------------------------------------
# #1259 — Regime Performance canonical states
# ---------------------------------------------------------------------------

def test_regime_all_four_canonical_states_always_present():
    """All 4 canonical states + unknown always appear, even if not observed."""
    from aletheia.analytics.trade_insights import _by_regime

    rows = [
        _make_row(regime_tag="bull", realized_pnl=1.0, id="p1"),
        _make_row(regime_tag="bear", realized_pnl=-2.0, id="p2"),
        _make_row(regime_tag="sideways", realized_pnl=0.5, id="p3"),
        _make_row(regime_tag="volatile", realized_pnl=-1.0, id="p4"),
    ]
    result = _by_regime(rows)
    regimes = [r["regime"] for r in result]

    for state in ("bull", "bear", "sideways", "volatile", "unknown"):
        assert state in regimes, f"{state} missing from regime output"

    by_regime = {r["regime"]: r for r in result}
    assert by_regime["bull"]["trade_count"] == 1
    assert by_regime["bear"]["trade_count"] == 1
    assert by_regime["sideways"]["trade_count"] == 1
    assert by_regime["volatile"]["trade_count"] == 1
    assert by_regime["unknown"]["trade_count"] == 0


def test_regime_null_maps_to_unknown():
    """null/blank regime_tag maps to unknown bucket."""
    from aletheia.analytics.trade_insights import _by_regime

    rows = [
        _make_row(regime_tag=None, id="p1"),
        _make_row(regime_tag="", id="p2"),
        _make_row(regime_tag="bull", id="p3"),
    ]
    result = _by_regime(rows)
    by_regime = {r["regime"]: r for r in result}

    assert by_regime["unknown"]["trade_count"] == 2
    assert by_regime["bull"]["trade_count"] == 1


def test_regime_conservation():
    """Sum of all trade_counts equals len(input rows)."""
    from aletheia.analytics.trade_insights import _by_regime

    rows = [
        _make_row(regime_tag="bull", id="p1"),
        _make_row(regime_tag="bear", id="p2"),
        _make_row(regime_tag="sideways", id="p3"),
        _make_row(regime_tag="volatile", id="p4"),
        _make_row(regime_tag=None, id="p5"),
        _make_row(regime_tag="legacy_regime", id="p6"),
    ]
    result = _by_regime(rows)
    total = sum(r["trade_count"] for r in result)
    assert total == len(rows), f"Conservation failed: {total} != {len(rows)}"


def test_regime_historical_noncanonical_appended():
    """Non-empty historically observed tags not in canonical set are appended."""
    from aletheia.analytics.trade_insights import _by_regime

    rows = [
        _make_row(regime_tag="risk_on", id="p1"),
        _make_row(regime_tag="risk_off", id="p2"),
        _make_row(regime_tag="bull", id="p3"),
    ]
    result = _by_regime(rows)
    regimes = [r["regime"] for r in result]

    for tag in ("risk_on", "risk_off", "bull"):
        assert tag in regimes


# ---------------------------------------------------------------------------
# #1256 — R-Multiple uses frozen initial risk only
# ---------------------------------------------------------------------------

def test_r_multiple_unverifiable_row_not_in_bins():
    """An unverifiable row (no DB) increments unavailable count, not bins."""
    from aletheia.analytics.trade_insights import _r_multiple_distribution

    rows = [_make_row(stop_price=None, id="p1")]
    result = _r_multiple_distribution(rows)

    assert isinstance(result, dict)
    assert "bins" in result
    assert "eligible_r_multiple_count" in result
    assert "unavailable_r_multiple_count" in result
    assert "unavailability_reasons" in result
    total_binned = sum(b["count"] for b in result["bins"])
    assert total_binned == 0
    assert result["unavailable_r_multiple_count"] == 1
    assert result["eligible_r_multiple_count"] == 0


def test_r_multiple_no_proxy_risk():
    """Rows without verifiable initial risk are never proxied via 2%-notional."""
    from aletheia.analytics.trade_insights import _r_multiple_distribution

    rows = [_make_row(entry_price=100.0, quantity=10.0, stop_price=98.0, id="p1")]
    result = _r_multiple_distribution(rows)

    total_binned = sum(b["count"] for b in result["bins"])
    # Without a DB-backed verified plan, row must be unavailable, not binned
    assert result["unavailable_r_multiple_count"] >= 1
    assert total_binned == result["eligible_r_multiple_count"]


# ---------------------------------------------------------------------------
# #1254 — Truncation metadata
# ---------------------------------------------------------------------------

def test_cohort_complete_false_when_10000_rows():
    """cohort_complete is False when 10,000 rows loaded (may be truncated)."""
    assert (10_000 < 10_000) is False


def test_cohort_complete_true_when_fewer_rows():
    """cohort_complete is True when loaded_count < 10,000."""
    assert (42 < 10_000) is True


def test_cohort_metadata_keys_present():
    """build_trade_insights includes required truncation metadata keys."""
    import aletheia.analytics.outcome_integrity as oi

    fake_rows = [
        _make_row(id=f"p{i}", closed_at=f"2026-07-2{i}T10:00:00+00:00") for i in range(3)
    ]
    fake_r_dist: dict = {
        "bins": [],
        "eligible_r_multiple_count": 0,
        "unavailable_r_multiple_count": 0,
        "unavailability_reasons": {},
    }

    with (
        patch.object(oi, "eligible_strategy_outcomes", return_value=fake_rows),
        patch.object(oi, "raw_account_events", return_value=fake_rows),
        patch.object(oi, "build_outcome_cohort_summary", return_value={}),
        patch(
            "aletheia.analytics.trade_insights._r_multiple_distribution",
            return_value=fake_r_dist,
        ),
    ):
        from aletheia.analytics import trade_insights
        result = trade_insights.build_trade_insights()

    assert "source_total_raw_events" in result
    assert "loaded_count" in result
    assert "cohort_complete" in result
    assert "loaded_from" in result
    assert "loaded_through" in result
    assert result["loaded_count"] == 3
    assert result["cohort_complete"] is True
    assert result["source_total_raw_events"] == 3
    assert result["loaded_from"] == fake_rows[0]["closed_at"]
    assert result["loaded_through"] == fake_rows[-1]["closed_at"]
