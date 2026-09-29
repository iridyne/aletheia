"""Tests for the spot-sweep gamma flip estimation (issue #1161)."""

import asyncio

import math

import pytest

from extensions.data.collectors.options_structure import _bsm_gamma, _gamma_flip_spot_sweep


def _make_entry(strike: float, option_type: str, oi: float, spot: float, T: float, sigma: float) -> dict:
    gamma = _bsm_gamma(spot, strike, T, sigma)
    assert gamma is not None
    return {"strike": strike, "type": option_type, "oi": oi, "T": T, "sigma": sigma, "gamma": gamma}


def test_gamma_flip_returns_none_for_empty_entries():
    assert _gamma_flip_spot_sweep([], spot=50000.0) is None


def test_gamma_flip_spot_sweep_finds_crossing():
    """Synthetic chain: heavy call OI above spot (put OI below) → net GEX flips as spot rises."""
    spot = 50000.0
    T = 0.1
    sigma = 0.6

    # Heavy call OI at 60k (above spot) → strong positive GEX contribution at high spot
    # Heavy put OI at 40k (below spot) → strong negative GEX contribution at low spot
    # The crossing should land somewhere between 40k and 60k
    entries = [
        _make_entry(60000.0, "C", oi=1000.0, spot=spot, T=T, sigma=sigma),
        _make_entry(40000.0, "P", oi=1000.0, spot=spot, T=T, sigma=sigma),
    ]

    flip = _gamma_flip_spot_sweep(entries, spot, grid_pct=0.35, n_points=500)

    # There should be a crossing within the grid
    assert flip is not None
    assert 40000.0 * 0.65 < flip < 60000.0 * 1.35

    # Verify it is actually near a sign change
    def net_gex(s: float) -> float:
        total = 0.0
        for e in entries:
            g = _bsm_gamma(s, e["strike"], e["T"], e["sigma"])
            if g is None:
                continue
            sign = 1.0 if e["type"] == "C" else -1.0
            total += sign * g * e["oi"] * s * s * 0.01
        return total

    gex_below = net_gex(flip * 0.99)
    gex_above = net_gex(flip * 1.01)
    # Signs should differ across the flip point
    assert gex_below * gex_above <= 0.0


def test_gamma_flip_returns_none_when_no_crossing():
    """All calls, no puts → net GEX always positive over the sweep range."""
    spot = 50000.0
    entries = [
        _make_entry(50000.0, "C", oi=1000.0, spot=spot, T=0.1, sigma=0.6),
        _make_entry(55000.0, "C", oi=500.0, spot=spot, T=0.1, sigma=0.6),
    ]
    flip = _gamma_flip_spot_sweep(entries, spot)
    assert flip is None


def test_bsm_gamma_rejects_non_finite_inputs():
    assert _bsm_gamma(float("nan"), 50_000.0, 0.1, 0.6) is None
    assert _bsm_gamma(50_000.0, float("inf"), 0.1, 0.6) is None


def test_options_snapshot_rejects_non_finite_values():
    from aletheia.data.models import OptionsMarketStructureSnapshot

    with pytest.raises(ValueError):
        OptionsMarketStructureSnapshot(
            symbol="BTC",
            underlying_price=float("nan"),
            observed_at="2026-08-29T00:00:00+00:00",
        )


def test_options_collector_rejects_non_finite_source_values(monkeypatch):
    import extensions.data.collectors.options_structure as mod

    payload = {
        "result": [
            {
                "instrument_name": f"BTC-30DEC30-{50_000 + i * 1_000}-C",
                "open_interest": "NaN" if i == 0 else "10",
                "mid_iv": "60",
                "underlying_price": "50000",
            }
            for i in range(5)
        ]
    }

    from unittest.mock import AsyncMock, MagicMock

    exchange = MagicMock()
    exchange.publicGetGetBookSummaryByCurrency = AsyncMock(return_value=payload)

    with pytest.raises(ValueError, match="invalid_numeric_inputs"):
        asyncio.run(mod._collect_currency(exchange, "BTC"))
