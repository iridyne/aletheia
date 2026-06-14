"""Trade path analysis — MFE/MAE/capture efficiency for closed trades.

Reconstructs the price path during holding window and diagnoses whether
losses are caused by bad entries, bad exits, or bad stop/TP settings.

Read-only. No DB mutations. No LLM calls.
"""
from __future__ import annotations

import logging
from typing import Any

from aletheia.analytics.trade_attribution import build_trade_attribution_rows
from aletheia.storage.repositories import get_ohlcv_candles

logger = logging.getLogger("aletheia.analytics.trade_path")

_DEFAULT_TIMEFRAME = "1h"


def build_trade_path_rows(limit: int = 500, post_exit_bars: int = 0) -> list[dict]:
    """Build MFE/MAE path analysis for closed paper/demo trades.

    Each row enriches the attribution row with price path metrics and
    a deterministic path_bucket diagnosis.
    """
    attribution_rows = build_trade_attribution_rows(limit=limit)
    results: list[dict] = []

    for row in attribution_rows:
        path_row = _analyze_path(row, post_exit_bars)
        results.append(path_row)

    return results


def _analyze_path(row: dict, post_exit_bars: int) -> dict:
    """Analyze price path for a single closed trade."""
    symbol = row.get("symbol")
    direction = row.get("direction")
    entry_price = float(row.get("entry_price") or 0)
    opened_at = row.get("opened_at")
    closed_at = row.get("closed_at")
    close_reason = row.get("close_reason")
    realized_pnl_pct = float(row.get("realized_pnl_pct") or 0)

    # Base output preserves attribution fields
    result = dict(row)
    result["timeframe"] = _DEFAULT_TIMEFRAME

    if not symbol or not entry_price or not opened_at or not closed_at:
        _set_insufficient(result)
        return result

    # Fetch OHLCV candles covering the trade period
    candles = get_ohlcv_candles(symbol, _DEFAULT_TIMEFRAME, limit=500)
    if not candles:
        _set_insufficient(result)
        return result

    # Find candles within the trade window
    entry_ts = _iso_to_ms(opened_at)
    exit_ts = _iso_to_ms(closed_at)
    if entry_ts is None or exit_ts is None:
        _set_insufficient(result)
        return result

    trade_candles = [c for c in candles if entry_ts <= int(c["ts"]) <= exit_ts]
    if not trade_candles:
        # Try broader match: candles overlapping the window
        trade_candles = [
            c for c in candles
            if int(c["ts"]) >= entry_ts - 3600000 and int(c["ts"]) <= exit_ts + 3600000
        ]
    if not trade_candles:
        _set_insufficient(result)
        return result

    # Compute MFE/MAE
    highs = [float(c["high"]) for c in trade_candles]
    lows = [float(c["low"]) for c in trade_candles]
    bars_in_trade = len(trade_candles)

    mfe_pct, mae_pct, time_to_mfe, time_to_mae = _compute_mfe_mae(
        direction, entry_price, highs, lows
    )

    # Capture and efficiency metrics
    mfe_capture_ratio = (realized_pnl_pct / mfe_pct) if mfe_pct > 0 else 0.0
    mae_to_mfe_ratio = (abs(mae_pct) / mfe_pct) if mfe_pct > 0 else 0.0
    exit_efficiency = _compute_exit_efficiency(mfe_pct, mae_pct, realized_pnl_pct)

    # Path bucket classification
    path_bucket, bucket_reason = _classify_path_bucket(
        mfe_pct, mae_pct, realized_pnl_pct, mfe_capture_ratio,
        close_reason, bars_in_trade, time_to_mfe,
    )

    result["bars_in_trade"] = bars_in_trade
    result["entry_ts_ms"] = entry_ts
    result["exit_ts_ms"] = exit_ts
    result["mfe_pct"] = round(mfe_pct, 4)
    result["mae_pct"] = round(mae_pct, 4)
    result["realized_return_pct"] = round(realized_pnl_pct, 4)
    result["mfe_capture_ratio"] = round(mfe_capture_ratio, 4)
    result["mae_to_mfe_ratio"] = round(mae_to_mfe_ratio, 4)
    result["time_to_mfe_bars"] = time_to_mfe
    result["time_to_mae_bars"] = time_to_mae
    result["exit_efficiency_score"] = round(exit_efficiency, 4)
    result["path_bucket"] = path_bucket
    result["path_bucket_reason"] = bucket_reason

    return result


def _compute_mfe_mae(
    direction: str, entry_price: float, highs: list[float], lows: list[float]
) -> tuple[float, float, int, int]:
    """Compute MFE/MAE and time-to-peak bars.

    Returns: (mfe_pct, mae_pct, time_to_mfe_bars, time_to_mae_bars)
    MFE is always >= 0, MAE is always <= 0.
    """
    if direction == "long":
        max_high = max(highs)
        min_low = min(lows)
        mfe_pct = (max_high - entry_price) / entry_price * 100
        mae_pct = (min_low - entry_price) / entry_price * 100
        time_to_mfe = highs.index(max_high)
        time_to_mae = lows.index(min_low)
    else:  # short
        min_low = min(lows)
        max_high = max(highs)
        mfe_pct = (entry_price - min_low) / entry_price * 100
        mae_pct = (entry_price - max_high) / entry_price * 100
        time_to_mfe = lows.index(min_low)
        time_to_mae = highs.index(max_high)

    # Clamp: MFE >= 0, MAE <= 0
    mfe_pct = max(mfe_pct, 0.0)
    mae_pct = min(mae_pct, 0.0)

    return mfe_pct, mae_pct, time_to_mfe, time_to_mae


def _compute_exit_efficiency(mfe_pct: float, mae_pct: float, realized_pct: float) -> float:
    """Exit efficiency: how much of the available range was captured.

    Score 0-100. 100 = exited at MFE, 0 = exited at MAE.
    """
    total_range = mfe_pct - mae_pct  # always positive (mfe>=0, mae<=0)
    if total_range <= 0:
        return 50.0
    position_in_range = realized_pct - mae_pct
    return max(0.0, min(100.0, (position_in_range / total_range) * 100))


def _classify_path_bucket(
    mfe_pct: float,
    mae_pct: float,
    realized_pct: float,
    mfe_capture_ratio: float,
    close_reason: str | None,
    bars_in_trade: int,
    time_to_mfe: int,
) -> tuple[str, str]:
    """Classify trade into a path bucket."""
    # Bad entry: price never moved favorably
    if mfe_pct < 0.3 and realized_pct <= 0:
        return "bad_entry", f"mfe={mfe_pct:.2f}%_too_low"

    # Stop too tight: stopped out but had meaningful favorable move first
    if close_reason == "stop_loss" and mfe_pct >= 0.8:
        return "stop_too_tight", f"mfe={mfe_pct:.2f}%_but_stopped"

    # Edge lost: had big MFE but realized <= 0
    if mfe_pct >= 1.0 and realized_pct <= 0:
        return "edge_lost", f"mfe={mfe_pct:.2f}%_but_realized={realized_pct:.2f}%"

    # Time exit decay: max_hold with early MFE but weak final return
    if close_reason == "max_hold" and mfe_pct > realized_pct + 0.5:
        return "time_exit_decay", f"mfe={mfe_pct:.2f}%_decayed_to={realized_pct:.2f}%"

    # Clean win
    if realized_pct > 0 and mfe_capture_ratio >= 0.5:
        return "clean_win", f"captured={mfe_capture_ratio:.0%}_of_mfe"

    # Take profit too early (only if we see post-exit data, simplified here)
    if close_reason == "take_profit" and mfe_capture_ratio < 0.5 and mfe_pct > 1.0:
        return "take_profit_too_early", f"capture_ratio={mfe_capture_ratio:.2f}_with_mfe={mfe_pct:.2f}%"

    return "uncategorized", f"mfe={mfe_pct:.2f}%_mae={mae_pct:.2f}%_realized={realized_pct:.2f}%"


def _set_insufficient(result: dict) -> None:
    """Set all path fields to None/insufficient."""
    result["bars_in_trade"] = None
    result["entry_ts_ms"] = None
    result["exit_ts_ms"] = None
    result["mfe_pct"] = None
    result["mae_pct"] = None
    result["realized_return_pct"] = result.get("realized_pnl_pct")
    result["mfe_capture_ratio"] = None
    result["mae_to_mfe_ratio"] = None
    result["time_to_mfe_bars"] = None
    result["time_to_mae_bars"] = None
    result["exit_efficiency_score"] = None
    result["path_bucket"] = "insufficient_path_data"
    result["path_bucket_reason"] = "missing_ohlcv_or_timestamps"


def _iso_to_ms(iso: str | None) -> int | None:
    """Convert ISO timestamp to milliseconds since epoch."""
    if not iso:
        return None
    try:
        from datetime import datetime, timezone
        patterns = [
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ]
        for p in patterns:
            try:
                dt = datetime.strptime(iso, p)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except ValueError:
                continue
    except Exception:
        pass
    return None
