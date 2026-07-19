"""Regime data collector — gathers inputs for detect_regime() from existing sources.

No new external API calls. Reuses:
  - BTC 1d OHLCV via get_market_data_provider()
  - MacroSnapshot from storage (VIX, market cap)
  - Funding rates from universe features (already collected by data_collectors)
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aletheia.regime.collector")


async def collect_regime_inputs() -> dict[str, Any]:
    """Gather all available inputs for regime detection.

    Returns a kwargs dict suitable for `detect_regime(**result)`.
    Never raises — returns partial data on any failure.
    """
    from aletheia.learning.opportunity_information_policy import (
        ensure_opportunity_information_policy_epoch,
    )

    ensure_opportunity_information_policy_epoch()
    inputs: dict[str, Any] = {}

    btc_candles = []
    # 1. Completed BTC daily candles (for MA200 + momentum + true range)
    try:
        btc_candles = await _fetch_btc_daily_candles()
        closes = [float(c.close) for c in btc_candles]
        if closes and all(math.isfinite(close) and close > 0 for close in closes):
            inputs["btc_closes_1d"] = closes
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
        atr_7, atr_30 = _compute_btc_atr_ratio(btc_candles)
        if atr_7 is not None:
            inputs["atr_7d"] = atr_7
        if atr_30 is not None:
            inputs["atr_30d"] = atr_30
    except Exception as e:
        logger.debug("ATR computation failed: %s", e)

    return inputs


async def _fetch_btc_daily_candles() -> list:
    """Fetch completed BTC/USDT 1d OHLCV — need 200+ bars for MA200."""
    from aletheia.data.ccxt_provider import get_market_data_provider

    provider = get_market_data_provider()
    try:
        candles = await provider.get_ohlcv("BTC/USDT", timeframe="1d", limit=220)
        if not candles:
            return []
        return _completed_daily_candles(candles)
    finally:
        await provider.close()


def _get_latest_vix() -> float | None:
    """Read VIX from latest macro snapshot in DB."""
    from aletheia.data.freshness import fresh_row
    from aletheia.storage.repositories import latest_macro_snapshot

    macro = fresh_row(latest_macro_snapshot(), "macro")
    if macro and macro.get("vix_close") is not None:
        try:
            value = float(macro["vix_close"])
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) and value >= 0 else None
    return None


def _get_avg_funding_rate() -> float | None:
    """Average each current derivative instrument's latest fresh funding row."""
    from aletheia.data.freshness import fresh_row
    from aletheia.storage.repositories import list_universe_feature_snapshots

    try:
        features = list_universe_feature_snapshots(limit=1000)
    except Exception:
        return None

    rates = []
    seen_assets: set[str] = set()
    for f in features:
        asset_id = str(f.get("asset_id") or "")
        if not asset_id or asset_id in seen_assets:
            continue
        seen_assets.add(asset_id)
        if str(f.get("market_type") or "").lower() not in {
            "perp", "swap", "futures", "usdt_futures",
        }:
            continue
        if fresh_row(f, "funding", timestamp_field="ts") is None:
            continue
        fr = f.get("funding_rate")
        if fr is not None:
            try:
                parsed = float(fr)
            except (ValueError, TypeError):
                continue
            if math.isfinite(parsed):
                rates.append(parsed)

    if not rates:
        return None
    return sum(rates) / len(rates)


def _get_market_cap_change_7d() -> float | None:
    """Compute market-cap change from latest and real seven-day baseline rows."""
    from aletheia.data.freshness import fresh_row
    from aletheia.storage.repositories import latest_macro_snapshot, macro_snapshot_near

    macro = fresh_row(latest_macro_snapshot(), "macro")
    if not macro:
        return None
    latest_cap = macro.get("total_market_cap_usd")
    observed_at = macro.get("observed_at")
    if latest_cap is None or not observed_at:
        return None
    baseline = macro_snapshot_near(observed_at, target_age_days=7.0)
    if not baseline or baseline.get("total_market_cap_usd") is None:
        return None
    try:
        latest_value = float(latest_cap)
        baseline_value = float(baseline["total_market_cap_usd"])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(latest_value) or not math.isfinite(baseline_value):
        return None
    if latest_value <= 0 or baseline_value <= 0:
        return None
    return round((latest_value - baseline_value) / baseline_value * 100, 3)


_DAY_MS = 86_400_000


def _completed_daily_candles(candles: list, *, now_ms: int | None = None) -> list:
    """Return oldest-first daily bars whose full UTC interval has elapsed."""
    if now_ms is None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return sorted(
        [c for c in candles if int(c.timestamp) + _DAY_MS <= now_ms],
        key=lambda c: c.timestamp,
    )


def _compute_btc_atr_ratio(
    candles: list | None,
    *,
    now_ms: int | None = None,
) -> tuple[float | None, float | None]:
    """Compute 7-day and 30-day true-range averages from completed daily bars."""
    if not candles:
        return None, None
    completed = _completed_daily_candles(candles, now_ms=now_ms)
    if len(completed) < 31:
        return None, None

    for candle in completed:
        try:
            values = (float(candle.high), float(candle.low), float(candle.close))
        except (TypeError, ValueError):
            return None, None
        if not all(math.isfinite(value) for value in values):
            return None, None

    true_ranges = []
    for previous, current_candle in zip(completed, completed[1:]):
        true_ranges.append(max(
            float(current_candle.high) - float(current_candle.low),
            abs(float(current_candle.high) - float(previous.close)),
            abs(float(current_candle.low) - float(previous.close)),
        ))

    current = float(completed[-1].close)
    if not math.isfinite(current) or current <= 0:
        return None, None

    atr_7 = sum(true_ranges[-7:]) / 7 / current * 100
    atr_30 = sum(true_ranges[-30:]) / 30 / current * 100

    if not math.isfinite(atr_7) or not math.isfinite(atr_30):
        return None, None
    return round(atr_7, 3), round(atr_30, 3)
