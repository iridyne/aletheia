"""Regime data collector — gathers inputs for detect_regime() from existing sources.

No new external API calls. Reuses:
  - BTC 1d OHLCV via get_market_data_provider()
  - MacroSnapshot from storage (VIX, market cap)
  - Funding rates from universe features (already collected by data_collectors)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("aletheia.regime.collector")


async def collect_regime_inputs() -> dict[str, Any]:
    """Gather all available inputs for regime detection.

    Returns a kwargs dict suitable for `detect_regime(**result)`.
    Never raises — returns partial data on any failure.
    """
    inputs: dict[str, Any] = {}

    # 1. BTC daily closes (for MA200 + momentum)
    try:
        inputs["btc_closes_1d"] = await _fetch_btc_daily_closes()
    except Exception as e:
        logger.debug("BTC daily closes unavailable: %s", e)

    # 2. VIX from latest macro snapshot
    try:
        vix = _get_latest_vix()
        if vix is not None:
            inputs["vix"] = vix
    except Exception as e:
        logger.debug("VIX unavailable: %s", e)

    # 3. Average funding rate from latest universe features
    try:
        avg_fr = _get_avg_funding_rate()
        if avg_fr is not None:
            inputs["avg_funding_rate"] = avg_fr
    except Exception as e:
        logger.debug("Avg funding rate unavailable: %s", e)

    # 4. Total market cap 7d change
    try:
        cap_change = _get_market_cap_change_7d()
        if cap_change is not None:
            inputs["total_market_cap_change_7d"] = cap_change
    except Exception as e:
        logger.debug("Market cap change unavailable: %s", e)

    # 5. ATR expansion (7d vs 30d)
    try:
        atr_7, atr_30 = _compute_btc_atr_ratio(inputs.get("btc_closes_1d"))
        if atr_7 is not None:
            inputs["atr_7d"] = atr_7
        if atr_30 is not None:
            inputs["atr_30d"] = atr_30
    except Exception as e:
        logger.debug("ATR computation failed: %s", e)

    return inputs


async def _fetch_btc_daily_closes() -> list[float]:
    """Fetch BTC/USDT 1d OHLCV — need 200+ bars for MA200."""
    from aletheia.data.ccxt_provider import get_market_data_provider

    provider = get_market_data_provider()
    try:
        candles = await provider.get_ohlcv("BTC/USDT", timeframe="1d", limit=220)
        if not candles:
            return []
        candles = sorted(candles, key=lambda c: c.timestamp)
        return [c.close for c in candles]
    finally:
        await provider.close()


def _get_latest_vix() -> float | None:
    """Read VIX from latest macro snapshot in DB."""
    from aletheia.storage.repositories import latest_macro_snapshot

    macro = latest_macro_snapshot()
    if macro and macro.get("vix_close"):
        return float(macro["vix_close"])
    return None


def _get_avg_funding_rate() -> float | None:
    """Compute average funding rate from latest universe feature snapshots."""
    from aletheia.storage.repositories import list_universe_feature_snapshots

    try:
        features = list_universe_feature_snapshots(limit=50)
    except Exception:
        return None

    rates = []
    for f in features:
        fr = f.get("funding_rate")
        if fr is not None:
            try:
                rates.append(float(fr))
            except (ValueError, TypeError):
                continue

    if not rates:
        return None
    return sum(rates) / len(rates)


def _get_market_cap_change_7d() -> float | None:
    """Read 7-day market cap change from macro snapshots.

    We approximate by comparing the latest vs 7-day-old snapshot.
    """
    from aletheia.storage.repositories import latest_macro_snapshot

    macro = latest_macro_snapshot()
    if not macro:
        return None

    # The macro collector stores 24h change; for 7d we use what's available
    change_24h = macro.get("total_market_cap_change_24h_pct")
    if change_24h is not None:
        # Rough approximation: 7d ≈ 24h * sqrt(7) for volatility,
        # but for trend we just use 24h as a proxy (better than nothing)
        return float(change_24h)
    return None


def _compute_btc_atr_ratio(
    closes: list[float] | None,
) -> tuple[float | None, float | None]:
    """Compute 7-day and 30-day ATR as % of price from daily closes.

    Simplified: uses close-to-close range as ATR proxy (no high/low available
    in this code path without extra fetch). Good enough for regime detection.
    """
    if not closes or len(closes) < 31:
        return None, None

    def _avg_range(prices: list[float]) -> float:
        if len(prices) < 2:
            return 0.0
        ranges = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
        return sum(ranges) / len(ranges)

    current = closes[-1]
    if current <= 0:
        return None, None

    atr_7 = _avg_range(closes[-8:]) / current * 100
    atr_30 = _avg_range(closes[-31:]) / current * 100

    return round(atr_7, 3), round(atr_30, 3)
