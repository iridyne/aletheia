"""Tests for degraded-price fallback in protective close pricing.

When the fresh protective mark is unavailable (price feed degraded), the system
should fall back to any stale-but-available price for Layer-0 safety exits
(catastrophic stop, liquidation proximity) while skipping strategy exits.

This verifies:
1. get_stale_price_for_emergency_close resolution order
2. Layer-0 fires on degraded price
3. Strategy exits are skipped on degraded price
4. Position is still skipped when no price exists at all
"""

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import aletheia.data.price_feed as pf
from aletheia.data.price_feed import (
    _price_cache,
    _instrument_price_cache,
    _cache_lock,
    get_fresh_price_for_protective_close,
)


@pytest.fixture(autouse=True)
def reset_caches():
    """Ensure price caches are clean before and after each test."""
    with _cache_lock:
        _price_cache.clear()
        _instrument_price_cache.clear()
    yield
    with _cache_lock:
        _price_cache.clear()
        _instrument_price_cache.clear()


# ---------------------------------------------------------------------------
# get_stale_price_for_emergency_close tests
# ---------------------------------------------------------------------------


def test_stale_price_from_instrument_cache():
    """Degraded price must use instrument cache entry regardless of age."""
    old_ts = datetime.now(timezone.utc) - timedelta(seconds=200)  # >90s stale
    with _cache_lock:
        _instrument_price_cache[("bitget", "BTC/USDT", "perp")] = {
            "price": 50000.0,
            "ts": old_ts,
        }
    result = pf.get_stale_price_for_emergency_close(
        "BTC/USDT", "usdt_futures", venue="bitget",
    )
    assert result == 50000.0


def test_stale_price_falls_back_to_symbol_cache():
    """Degraded price must fall back to symbol-only cache when instrument cache empty."""
    old_ts = datetime.now(timezone.utc) - timedelta(seconds=200)
    with _cache_lock:
        _price_cache["BTC/USDT"] = {
            "price": 51000.0,
            "ts": old_ts,
        }
    result = pf.get_stale_price_for_emergency_close(
        "BTC/USDT", "usdt_futures", venue="bitget",
    )
    assert result == 51000.0


def test_stale_price_prefers_instrument_over_symbol_cache():
    """Instrument cache takes priority over symbol cache."""
    old_ts = datetime.now(timezone.utc) - timedelta(seconds=200)
    with _cache_lock:
        _instrument_price_cache[("bitget", "BTC/USDT", "perp")] = {
            "price": 50000.0,
            "ts": old_ts,
        }
        _price_cache["BTC/USDT"] = {
            "price": 51000.0,
            "ts": old_ts,
        }
    result = pf.get_stale_price_for_emergency_close(
        "BTC/USDT", "usdt_futures", venue="bitget",
    )
    assert result == 50000.0  # instrument cache value


def test_stale_price_rejects_too_old():
    """Degraded price must reject entries older than the emergency max age."""
    too_old_ts = datetime.now(timezone.utc) - timedelta(seconds=600)  # 10 min
    with _cache_lock:
        _instrument_price_cache[("bitget", "BTC/USDT", "perp")] = {
            "price": 50000.0,
            "ts": too_old_ts,
        }
    result = pf.get_stale_price_for_emergency_close(
        "BTC/USDT", "usdt_futures", venue="bitget",
    )
    assert result is None


def test_stale_price_returns_none_when_empty():
    """Degraded price returns None when no price exists in any cache."""
    result = pf.get_stale_price_for_emergency_close(
        "BTC/USDT", "usdt_futures", venue="bitget",
    )
    assert result is None


def test_stale_price_rejects_invalid_values():
    """Degraded price must reject non-finite, zero, or negative prices."""
    old_ts = datetime.now(timezone.utc) - timedelta(seconds=200)
    for bad_price in [0.0, -1.0, float("nan"), float("inf")]:
        with _cache_lock:
            _instrument_price_cache[("bitget", "BTC/USDT", "perp")] = {
                "price": bad_price,
                "ts": old_ts,
            }
        result = pf.get_stale_price_for_emergency_close(
            "BTC/USDT", "usdt_futures", venue="bitget",
        )
        assert result is None, f"Expected None for price={bad_price}"


# ---------------------------------------------------------------------------
# Integration: position_manager degraded-price handling
# ---------------------------------------------------------------------------


def _make_position(symbol="BTC/USDT", direction="long", entry=50000.0, **kwargs):
    return {
        "id": "pos_test_1",
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry,
        "market_type": "usdt_futures",
        "execution_mode": "bitget_demo",
        "leverage": 1,
        "status": "open",
        "pending_close_status": None,
        **kwargs,
    }


def _make_risk_view(enabled=True, pct=10.0):
    """Minimal risk view compatible with _catastrophic_stop_pct."""
    return MagicMock(
        catastrophic_stop_enabled=enabled,
        catastrophic_stop_pct=pct,
        catastrophic_stop_pct_serenity=15.0,
        runtime_liquidation_monitor_enabled=False,
    )


def test_layer0_fires_on_degraded_price():
    """Layer-0 catastrophic stop must fire even with a degraded (stale) price."""
    from aletheia.portfolio import position_manager as pm

    position = _make_position(entry=50000.0)
    risk_view = _make_risk_view(enabled=True, pct=10.0)

    # Fresh price unavailable, degraded price available.
    # Patch at position_manager level where the functions are imported.
    with patch("aletheia.portfolio.position_manager.get_fresh_price_for_protective_close", return_value=None), \
         patch("aletheia.portfolio.position_manager.get_stale_price_for_emergency_close", return_value=44000.0), \
         patch("aletheia.portfolio.position_manager.load_config") as mock_cfg, \
         patch("aletheia.portfolio.position_manager._position_risk", return_value=risk_view), \
         patch.object(pm, "list_all_open_positions", return_value=[position]):
        mock_cfg.return_value.market_data.ccxt_exchange = "bitget"
        mock_cfg.return_value.trading.market_aware_exits.enabled = False

        result = pm.evaluate_open_positions()

        # Layer-0 should fire: (50000-44000)/50000 = 12% > 10% threshold
        assert result["catastrophic_exits"] == 1


def test_strategy_exit_skipped_on_degraded_price():
    """Strategy exits (SL/TP/max-hold) must be skipped when price is degraded."""
    from aletheia.portfolio import position_manager as pm

    position = _make_position(entry=50000.0)
    risk_view = _make_risk_view(enabled=True, pct=10.0)

    with patch("aletheia.portfolio.position_manager.get_fresh_price_for_protective_close", return_value=None), \
         patch("aletheia.portfolio.position_manager.get_stale_price_for_emergency_close", return_value=49000.0), \
         patch("aletheia.portfolio.position_manager.load_config") as mock_cfg, \
         patch("aletheia.portfolio.position_manager._position_risk", return_value=risk_view), \
         patch.object(pm, "list_all_open_positions", return_value=[position]), \
         patch("aletheia.portfolio.position_manager._decide_exit") as mock_decide, \
         patch("aletheia.portfolio.position_manager._maybe_update_trailing_stop"):
        mock_cfg.return_value.market_data.ccxt_exchange = "bitget"
        mock_cfg.return_value.trading.market_aware_exits.enabled = False

        result = pm.evaluate_open_positions()

        # Strategy exit must NOT be called on degraded price
        mock_decide.assert_not_called()
        # Position should NOT be closed (49000 is within 10% of 50000)
        assert result["closed"] == 0


def test_skip_when_no_price_at_all():
    """Position must be skipped when neither fresh nor degraded price is available."""
    from aletheia.portfolio import position_manager as pm

    position = _make_position(entry=50000.0)

    with patch("aletheia.portfolio.position_manager.get_fresh_price_for_protective_close", return_value=None), \
         patch("aletheia.portfolio.position_manager.get_stale_price_for_emergency_close", return_value=None), \
         patch("aletheia.portfolio.position_manager.load_config") as mock_cfg, \
         patch.object(pm, "list_all_open_positions", return_value=[position]):
        mock_cfg.return_value.market_data.ccxt_exchange = "bitget"

        result = pm.evaluate_open_positions()

        # Position checked but not closed
        assert result["checked"] == 1
        assert result["closed"] == 0
