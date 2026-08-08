"""Fail-closed trade-path evidence for closed trades.

The path is measured from exact-instrument, closed one-minute candles.  Holding
and post-exit windows are separate: a candle which straddles entry or exit is
never presented as an exact in-position observation.  Those boundary candles
only provide conservative excursion bounds; when a diagnosis depends on an
unknown within-bar ordering the row is explicitly ambiguous.

Read-only. No DB mutations, trading decisions, config writes, or LLM calls.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from aletheia.analytics.trade_attribution import build_trade_attribution_rows
from aletheia.storage import repositories as repo

logger = logging.getLogger("aletheia.analytics.trade_path")

_DEFAULT_TIMEFRAME = "1m"
_BAR_MS = 60_000
_MAX_PATH_BARS = 10_000
_BAD_ENTRY_MFE_PCT = 0.3
_STOP_FAVORABLE_MFE_PCT = 0.8
_EDGE_LOST_MFE_PCT = 1.0
_POST_EXIT_CONTINUATION_PCT = 1.0
_PHYSICAL_MARKET_TYPES = {
    "spot": "spot",
    "perp": "perp",
    "usdt_futures": "perp",
}


@dataclass(frozen=True)
class _InstrumentIdentity:
    exchange: str
    market_type: str
    source: str


def build_trade_path_rows(limit: int = 500, post_exit_bars: int = 0) -> list[dict]:
    """Build diagnostic MFE/MAE evidence for closed paper/demo trades.

    ``post_exit_bars`` is a prospective observation window in complete 1m bars.
    It is diagnostic only and never alters an exit or any other trading action.
    """
    attribution_rows = build_trade_attribution_rows(limit=limit)
    return [_analyze_path(row, post_exit_bars) for row in attribution_rows]


def _analyze_path(row: dict, post_exit_bars: int) -> dict:
    """Analyze one closed trade without inventing identity, coverage, or order."""
    result = dict(row)
    _initialize_evidence_fields(result, post_exit_bars)

    if not isinstance(post_exit_bars, int) or isinstance(post_exit_bars, bool):
        _set_insufficient(result, "invalid_post_exit_bars")
        return result
    if post_exit_bars < 0:
        _set_insufficient(result, "invalid_post_exit_bars")
        return result

    symbol = str(row.get("symbol") or "").strip()
    direction = str(row.get("direction") or "").strip().lower()
    entry_price = _positive_float(row.get("entry_price"))
    exit_price = _positive_float(row.get("exit_price"))
    realized_pnl_pct = _finite_float(row.get("realized_pnl_pct"))
    entry_ts = _iso_to_ms(row.get("opened_at"))
    exit_ts = _iso_to_ms(row.get("closed_at"))

    result["entry_ts_ms"] = entry_ts
    result["exit_ts_ms"] = exit_ts
    if (
        not symbol
        or direction not in {"long", "short"}
        or entry_price is None
        or exit_price is None
        or realized_pnl_pct is None
        or entry_ts is None
        or exit_ts is None
        or exit_ts <= entry_ts
    ):
        _set_insufficient(result, "missing_or_invalid_trade_identity")
        return result

    identity, identity_error = _resolve_instrument_identity(row)
    if identity is None:
        _set_insufficient(result, identity_error or "instrument_identity_unavailable")
        return result
    result["ohlcv_exchange"] = identity.exchange
    result["ohlcv_market_type"] = identity.market_type
    result["ohlcv_identity_source"] = identity.source

    entry_bar_ts = _floor_bar(entry_ts)
    holding_end_exclusive = _ceil_bar(exit_ts)
    post_start_ts = _ceil_bar(exit_ts)
    post_end_ts = post_start_ts + post_exit_bars * _BAR_MS
    fetch_end_ts = post_end_ts if post_exit_bars else holding_end_exclusive
    requested_span = max(0, (fetch_end_ts - entry_bar_ts) // _BAR_MS)
    if requested_span <= 0 or requested_span > _MAX_PATH_BARS:
        _set_insufficient(result, "requested_path_window_unavailable")
        return result

    try:
        candles = repo.get_ohlcv_candles(
            symbol,
            _DEFAULT_TIMEFRAME,
            limit=requested_span,
            exchange=identity.exchange,
            market_type=identity.market_type,
            before_ts=fetch_end_ts,
        )
    except Exception as exc:  # read failure is unavailable evidence, never zero
        logger.debug("trade path OHLCV read failed: %s", type(exc).__name__)
        _set_insufficient(result, "ohlcv_read_unavailable")
        return result

    candle_by_ts, candle_error = _validated_candle_map(
        candles,
        start_ts=entry_bar_ts,
        end_ts=fetch_end_ts,
        identity=identity,
    )
    if candle_by_ts is None:
        _set_insufficient(result, candle_error or "invalid_ohlcv")
        return result

    full_start_ts = _ceil_bar(entry_ts)
    full_end_ts = _floor_bar(exit_ts)
    full_holding_ts = list(range(full_start_ts, full_end_ts, _BAR_MS))
    # Exact trigger/linkage evidence is independent of complete path coverage.
    # Preserve its conservative pre-stop MFE lower bound even when an unrelated
    # entry/exit boundary candle is unavailable.
    available_full_holding = [
        candle_by_ts[ts] for ts in full_holding_ts if ts in candle_by_ts
    ]
    _attach_first_stop_barrier_evidence(
        result,
        row=row,
        identity=identity,
        entry_ts=entry_ts,
        exit_ts=exit_ts,
        entry_price=entry_price,
        direction=direction,
        full_holding=available_full_holding,
    )

    holding_ts = list(range(entry_bar_ts, holding_end_exclusive, _BAR_MS))
    missing_holding = [ts for ts in holding_ts if ts not in candle_by_ts]
    result["holding_bars_expected"] = len(holding_ts)
    result["holding_bars_observed"] = len(holding_ts) - len(missing_holding)
    if missing_holding:
        _set_insufficient(result, "holding_path_coverage_unavailable")
        return result

    full_holding = available_full_holding
    overlapping_holding = [candle_by_ts[ts] for ts in holding_ts]
    result["bars_in_trade"] = len(full_holding)
    result["boundary_bars_excluded"] = len(holding_ts) - len(full_holding_ts)

    # Full bars plus exact entry/exit fills are confirmed in-position points.
    confirmed_prices = [entry_price]
    if exit_price is not None:
        confirmed_prices.append(exit_price)
    mfe_lower, mae_upper = _excursion_values(
        direction,
        entry_price,
        full_holding,
        confirmed_prices,
    )
    # Overlapping boundary candles may contain pre-entry/post-exit prices.  They
    # bound the true excursion but cannot be treated as exact holding evidence.
    mfe_upper, mae_lower = _excursion_values(
        direction,
        entry_price,
        overlapping_holding,
        confirmed_prices,
    )
    result["mfe_lower_bound_pct"] = round(mfe_lower, 4)
    result["mfe_upper_bound_pct"] = round(mfe_upper, 4)
    result["mae_lower_bound_pct"] = round(mae_lower, 4)
    result["mae_upper_bound_pct"] = round(mae_upper, 4)
    result["realized_return_pct"] = round(realized_pnl_pct, 4)

    exact_boundaries = entry_ts % _BAR_MS == 0 and exit_ts % _BAR_MS == 0
    if exact_boundaries:
        mfe, mae, time_to_mfe, time_to_mae = _compute_exact_path_metrics(
            direction,
            entry_price,
            full_holding,
            exit_price,
        )
        result["time_to_mfe_bars"] = time_to_mfe
        result["time_to_mae_bars"] = time_to_mae
        _set_exact_metrics(result, mfe, mae, realized_pnl_pct)
        result["path_evidence_status"] = "available"
        result["path_evidence_reason"] = "complete_exact_1m_path"
    else:
        result["path_evidence_status"] = "bounded"
        result["path_evidence_reason"] = "partial_boundary_bars_excluded"

    post_status, post_mfe, _ = _analyze_post_exit(
        result,
        candle_by_ts,
        post_start_ts=post_start_ts,
        post_exit_bars=post_exit_bars,
        direction=direction,
        exit_price=exit_price,
    )
    if post_status == "available" and post_mfe is not None and post_mfe >= _POST_EXIT_CONTINUATION_PCT:
        result["post_exit_noncausal_observation"] = "post_exit_favorable_continuation"
        result["post_exit_observation_reason"] = f"post_exit_mfe={post_mfe:.2f}%_without_realizability_claim"

    bucket, bucket_reason, ambiguous = _classify_bounded_path(
        mfe_lower=mfe_lower,
        mfe_upper=mfe_upper,
        mae_lower=mae_lower,
        realized_pct=realized_pnl_pct,
        close_reason=row.get("close_reason"),
    )
    pre_stop_mfe = result.get("pre_stop_mfe_lower_bound_pct")
    if (
        result.get("first_stop_barrier_status") == "available"
        and pre_stop_mfe is not None
        and float(pre_stop_mfe) >= _STOP_FAVORABLE_MFE_PCT
    ):
        result["path_bucket"] = "profit_giveback_before_stop"
        result["path_bucket_reason"] = f"pre_stop_mfe_lower_bound={float(pre_stop_mfe):.2f}%"
        return result
    if result.get("first_stop_barrier_status") == "available" and ambiguous and row.get("close_reason") == "stop_loss":
        # The first stop bar is known, but OHLC cannot order the favorable and
        # adverse extremes inside that same minute. Only earlier complete bars
        # can prove giveback-before-stop.
        bucket = "possible_favorable_excursion_in_boundary_bar"
        bucket_reason = "intrabar_and_stop_barrier_order_unavailable"
    if ambiguous:
        if bucket != "insufficient_path_data":
            result["noncausal_path_observation"] = bucket
        result["path_evidence_status"] = "ambiguous"
        result["path_evidence_reason"] = bucket_reason
        result["path_bucket"] = "insufficient_path_data"
    elif exact_boundaries:
        result["path_bucket"] = bucket
    else:
        # Keep conservative diagnoses visible to humans, but do not let the
        # existing policy-advice consumer treat a bounded path as exact MFE/MAE.
        result["bounded_path_bucket"] = bucket
        result["bounded_path_bucket_reason"] = bucket_reason
        result["path_bucket"] = "insufficient_path_data"
        bucket_reason = "bounded_path_not_actionable"
    result["path_bucket_reason"] = bucket_reason
    return result


def _resolve_instrument_identity(
    row: dict[str, Any],
) -> tuple[_InstrumentIdentity | None, str | None]:
    """Resolve exact OHLCV scope from row identity or its Universe lineage."""
    symbol = str(row.get("symbol") or "").strip()
    market_type = _physical_market_type(row.get("market_type"))
    direct_exchange = str(row.get("exchange") or row.get("venue") or "").strip().lower()

    if direct_exchange:
        if not market_type or direct_exchange == "legacy_unknown":
            return None, "instrument_identity_unavailable"
        return (
            _InstrumentIdentity(direct_exchange, market_type, "attribution_row"),
            None,
        )

    member_id = str(row.get("universe_member_id") or "").strip()
    if not member_id:
        return None, "instrument_identity_unavailable"
    try:
        member = repo.get_universe_member(member_id)
        asset_id = str((member or {}).get("asset_id") or "").strip()
        asset = repo.get_universe_asset(asset_id) if asset_id else None
    except Exception:
        return None, "instrument_identity_unavailable"
    if not member or not asset:
        return None, "instrument_identity_unavailable"

    exchange = str(asset.get("venue") or "").strip().lower()
    asset_symbol = str(asset.get("symbol") or "").strip()
    asset_market_type = _physical_market_type(asset.get("market_type"))
    member_symbol = str(member.get("symbol") or "").strip()
    member_market_type = _physical_market_type(member.get("market_type"))
    if (
        not exchange
        or exchange == "legacy_unknown"
        or not market_type
        or not asset_market_type
        or not member_market_type
    ):
        return None, "instrument_identity_unavailable"
    if (
        symbol != asset_symbol
        or symbol != member_symbol
        or market_type != asset_market_type
        or market_type != member_market_type
    ):
        return None, "instrument_identity_mismatch"
    return _InstrumentIdentity(exchange, market_type, "universe_registry"), None


def _attach_first_stop_barrier_evidence(
    result: dict[str, Any],
    *,
    row: dict[str, Any],
    identity: _InstrumentIdentity,
    entry_ts: int,
    exit_ts: int,
    entry_price: float,
    direction: str,
    full_holding: list[dict],
) -> None:
    """Attach ordered pre-stop evidence from one materialized exact trigger.

    Version-one plans have no earlier plan-boundary bar that the protective
    cursor could have skipped as intrinsically unordered. Later plan versions
    remain fail-closed until durable boundary-gap history exists.
    """
    if row.get("close_reason") != "stop_loss":
        result["first_stop_barrier_status"] = "not_applicable"
        result["first_stop_barrier_reason"] = "close_reason_not_stop_loss"
        return
    position_id = str(row.get("position_id") or "").strip()
    if not position_id:
        result["first_stop_barrier_reason"] = "position_identity_unavailable"
        return
    try:
        state = repo.get_position_protection_state(position_id)
        trigger = repo.get_position_protection_trigger(position_id)
    except Exception as exc:
        logger.debug("protection evidence read failed: %s", type(exc).__name__)
        result["first_stop_barrier_reason"] = "protection_evidence_read_unavailable"
        return
    if state is None or trigger is None:
        result["first_stop_barrier_reason"] = "first_stop_barrier_cross_unavailable"
        return
    if int(trigger.get("plan_version") or 0) != 1:
        result["first_stop_barrier_reason"] = "exit_plan_changed_before_first_stop"
        return

    trigger_open = _integer(trigger.get("bar_open_ms"))
    trigger_close = _integer(trigger.get("bar_close_ms"))
    activated_at = _iso_to_ms(state.get("activated_at"))
    detected_at = _iso_to_ms(trigger.get("detected_at"))
    filled_at = _iso_to_ms(state.get("close_filled_at"))
    fill_price = _positive_float(state.get("close_fill_price"))
    exit_price = _positive_float(row.get("exit_price"))
    identity_matches = (
        str(state.get("venue") or "").lower() == identity.exchange
        and str(state.get("symbol") or "") == str(row.get("symbol") or "")
        and str(state.get("market_type") or "").lower() == identity.market_type
        and state.get("timeframe") == _DEFAULT_TIMEFRAME
        and str(trigger.get("venue") or "").lower() == identity.exchange
        and str(trigger.get("symbol") or "") == str(row.get("symbol") or "")
        and str(trigger.get("market_type") or "").lower() == identity.market_type
        and trigger.get("timeframe") == _DEFAULT_TIMEFRAME
    )
    linkage_matches = (
        state.get("position_id") == position_id
        and trigger.get("position_id") == position_id
        and state.get("trigger_id") == trigger.get("trigger_id")
        and state.get("status") == "materialized"
        and state.get("final_state") == "materialized"
        and bool(state.get("close_fill_id"))
        and trigger.get("reason") == "stop_loss"
        and bool(trigger.get("stop_hit"))
    )
    timing_matches = (
        trigger_open is not None
        and trigger_close == trigger_open + _BAR_MS
        and trigger_close == _integer(state.get("next_bar_open_ms"))
        and trigger_open == _integer(state.get("last_evaluated_bar_open_ms"))
        and trigger_open == _integer(state.get("last_inspected_bar_open_ms"))
        and activated_at is not None
        and activated_at >= entry_ts
        and _ceil_bar(activated_at) == _ceil_bar(entry_ts)
        and trigger_open >= _ceil_bar(entry_ts)
        and trigger_close <= exit_ts
        and detected_at is not None
        and detected_at >= trigger_close
        and filled_at == exit_ts
    )
    price_matches = (
        fill_price is not None
        and exit_price is not None
        and math.isclose(fill_price, exit_price, rel_tol=1e-12, abs_tol=1e-12)
    )
    if not (identity_matches and linkage_matches and timing_matches and price_matches):
        result["first_stop_barrier_reason"] = "protection_evidence_mismatch"
        return

    pre_trigger = [
        candle for candle in full_holding if _integer(candle.get("ts")) is not None and int(candle["ts"]) < trigger_open
    ]
    pre_mfe, _ = _excursion_values(
        direction,
        entry_price,
        pre_trigger,
        [entry_price],
    )
    result["first_stop_barrier_status"] = "available"
    result["first_stop_barrier_reason"] = "materialized_exact_1m_first_trigger"
    result["first_stop_barrier_open_ms"] = trigger_open
    result["pre_stop_mfe_lower_bound_pct"] = round(pre_mfe, 4)


def _validated_candle_map(
    candles: list[dict],
    *,
    start_ts: int,
    end_ts: int,
    identity: _InstrumentIdentity,
) -> tuple[dict[int, dict] | None, str | None]:
    """Validate exact identity, one-minute alignment, uniqueness, and OHLC."""
    selected: dict[int, dict] = {}
    for candle in candles:
        try:
            ts = int(candle["ts"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None, "invalid_ohlcv_timestamp"
        if not start_ts <= ts < end_ts:
            continue
        if ts % _BAR_MS != 0 or ts in selected:
            return None, "invalid_ohlcv_timeline"
        if (
            str(candle.get("exchange") or "").strip().lower() != identity.exchange
            or str(candle.get("market_type") or "").strip().lower() != identity.market_type
        ):
            return None, "ohlcv_identity_mismatch"
        values = [_positive_float(candle.get(key)) for key in ("open", "high", "low", "close")]
        if any(value is None for value in values):
            return None, "invalid_ohlcv_values"
        open_price, high, low, close = values
        assert open_price is not None and high is not None and low is not None and close is not None
        if high < max(open_price, close) or low > min(open_price, close) or low > high:
            return None, "invalid_ohlcv_values"
        selected[ts] = candle
    return selected, None


def _analyze_post_exit(
    result: dict,
    candle_by_ts: dict[int, dict],
    *,
    post_start_ts: int,
    post_exit_bars: int,
    direction: str,
    exit_price: float | None,
) -> tuple[str, float | None, float | None]:
    """Measure only complete candles which start after the recorded exit."""
    if post_exit_bars == 0:
        return "not_requested", None, None
    if exit_price is None:
        result["post_exit_path_status"] = "unavailable"
        result["post_exit_path_reason"] = "exit_price_unavailable"
        return "unavailable", None, None

    expected_ts = [post_start_ts + i * _BAR_MS for i in range(post_exit_bars)]
    observed = [candle_by_ts[ts] for ts in expected_ts if ts in candle_by_ts]
    result["post_exit_bars_observed"] = len(observed)
    result["post_exit_window_start_ts_ms"] = post_start_ts
    result["post_exit_window_end_ts_ms"] = post_start_ts + post_exit_bars * _BAR_MS
    if len(observed) != post_exit_bars:
        result["post_exit_path_status"] = "unavailable"
        result["post_exit_path_reason"] = "post_exit_coverage_unavailable"
        return "unavailable", None, None

    post_mfe, post_mae = _excursion_values(
        direction,
        exit_price,
        observed,
        [exit_price],
    )
    result["post_exit_mfe_pct"] = round(post_mfe, 4)
    result["post_exit_mae_pct"] = round(post_mae, 4)
    result["post_exit_path_status"] = "available"
    result["post_exit_path_reason"] = "complete_post_exit_1m_bars"
    return "available", post_mfe, post_mae


def _excursion_values(
    direction: str,
    reference_price: float,
    candles: list[dict],
    point_prices: list[float],
) -> tuple[float, float]:
    highs = [float(c["high"]) for c in candles] + point_prices
    lows = [float(c["low"]) for c in candles] + point_prices
    mfe, mae, _, _ = _compute_mfe_mae(direction, reference_price, highs, lows)
    return mfe, mae


def _set_exact_metrics(
    result: dict,
    mfe_pct: float,
    mae_pct: float,
    realized_pnl_pct: float,
) -> None:
    mfe_capture_ratio = (realized_pnl_pct / mfe_pct) if mfe_pct > 0 else 0.0
    mae_to_mfe_ratio = (abs(mae_pct) / mfe_pct) if mfe_pct > 0 else 0.0
    result["mfe_pct"] = round(mfe_pct, 4)
    result["mae_pct"] = round(mae_pct, 4)
    result["mfe_capture_ratio"] = round(mfe_capture_ratio, 4)
    result["mae_to_mfe_ratio"] = round(mae_to_mfe_ratio, 4)
    result["exit_efficiency_score"] = round(_compute_exit_efficiency(mfe_pct, mae_pct, realized_pnl_pct), 4)


def _compute_exact_path_metrics(
    direction: str,
    entry_price: float,
    candles: list[dict],
    exit_price: float | None,
) -> tuple[float, float, int, int]:
    """Compute exact excursion and elapsed-bar timing including entry/exit."""
    favorable: list[tuple[float, int]] = [(entry_price, 0)]
    adverse: list[tuple[float, int]] = [(entry_price, 0)]
    for elapsed_bars, candle in enumerate(candles, start=1):
        if direction == "long":
            favorable.append((float(candle["high"]), elapsed_bars))
            adverse.append((float(candle["low"]), elapsed_bars))
        else:
            favorable.append((float(candle["low"]), elapsed_bars))
            adverse.append((float(candle["high"]), elapsed_bars))
    if exit_price is not None:
        elapsed_bars = len(candles)
        favorable.append((exit_price, elapsed_bars))
        adverse.append((exit_price, elapsed_bars))

    if direction == "long":
        favorable_price, time_to_mfe = max(favorable, key=lambda item: item[0])
        adverse_price, time_to_mae = min(adverse, key=lambda item: item[0])
        mfe_pct = (favorable_price - entry_price) / entry_price * 100
        mae_pct = (adverse_price - entry_price) / entry_price * 100
    else:
        favorable_price, time_to_mfe = min(favorable, key=lambda item: item[0])
        adverse_price, time_to_mae = max(adverse, key=lambda item: item[0])
        mfe_pct = (entry_price - favorable_price) / entry_price * 100
        mae_pct = (entry_price - adverse_price) / entry_price * 100
    return max(mfe_pct, 0.0), min(mae_pct, 0.0), time_to_mfe, time_to_mae


def _classify_bounded_path(
    *,
    mfe_lower: float,
    mfe_upper: float,
    mae_lower: float,
    realized_pct: float,
    close_reason: str | None,
) -> tuple[str, str, bool]:
    """Classify only claims proved by conservative excursion bounds."""
    if mfe_upper < _BAD_ENTRY_MFE_PCT and realized_pct <= 0:
        return "bad_entry", f"mfe_upper_bound={mfe_upper:.2f}%_too_low", False

    if close_reason == "stop_loss":
        if mfe_lower >= _STOP_FAVORABLE_MFE_PCT:
            return (
                "favorable_excursion_during_recorded_holding_window",
                "first_stop_barrier_cross_unavailable",
                True,
            )
        if mfe_upper >= _STOP_FAVORABLE_MFE_PCT:
            return (
                "possible_favorable_excursion_in_boundary_bar",
                "intrabar_and_stop_barrier_order_unavailable",
                True,
            )

    if mfe_lower >= _EDGE_LOST_MFE_PCT and realized_pct <= 0:
        return (
            "edge_lost",
            f"mfe_lower_bound={mfe_lower:.2f}%_but_realized={realized_pct:.2f}%",
            False,
        )

    if close_reason == "max_hold" and mfe_lower > realized_pct + 0.5:
        return (
            "time_exit_decay",
            f"mfe_lower_bound={mfe_lower:.2f}%_decayed_to={realized_pct:.2f}%",
            False,
        )

    conservative_capture = (realized_pct / mfe_upper) if mfe_upper > 0 else 0.0
    if realized_pct > 0 and conservative_capture >= 0.5:
        return (
            "clean_win",
            f"captured_at_least={conservative_capture:.0%}_of_mfe",
            False,
        )

    return (
        "uncategorized",
        f"mfe_bounds={mfe_lower:.2f}%..{mfe_upper:.2f}%_mae_lower={mae_lower:.2f}%",
        False,
    )


def _compute_mfe_mae(
    direction: str, entry_price: float, highs: list[float], lows: list[float]
) -> tuple[float, float, int, int]:
    """Compute MFE/MAE and bar indices; MFE >= 0 and MAE <= 0."""
    if direction == "long":
        max_high = max(highs)
        min_low = min(lows)
        mfe_pct = (max_high - entry_price) / entry_price * 100
        mae_pct = (min_low - entry_price) / entry_price * 100
        time_to_mfe = highs.index(max_high)
        time_to_mae = lows.index(min_low)
    else:
        min_low = min(lows)
        max_high = max(highs)
        mfe_pct = (entry_price - min_low) / entry_price * 100
        mae_pct = (entry_price - max_high) / entry_price * 100
        time_to_mfe = lows.index(min_low)
        time_to_mae = highs.index(max_high)
    return max(mfe_pct, 0.0), min(mae_pct, 0.0), time_to_mfe, time_to_mae


def _compute_exit_efficiency(mfe_pct: float, mae_pct: float, realized_pct: float) -> float:
    """Return realized location in the measured excursion range, clamped 0..100."""
    total_range = mfe_pct - mae_pct
    if total_range <= 0:
        return 50.0
    return max(0.0, min(100.0, ((realized_pct - mae_pct) / total_range) * 100))


def _classify_path_bucket(
    mfe_pct: float,
    mae_pct: float,
    realized_pct: float,
    mfe_capture_ratio: float,
    close_reason: str | None,
    bars_in_trade: int,
    time_to_mfe: int,
) -> tuple[str, str]:
    """Compatibility helper for callers with an already exact path."""
    del bars_in_trade, time_to_mfe
    bucket, reason, ambiguous = _classify_bounded_path(
        mfe_lower=mfe_pct,
        mfe_upper=mfe_pct,
        mae_lower=mae_pct,
        realized_pct=realized_pct,
        close_reason=close_reason,
    )
    if ambiguous:
        return "insufficient_path_data", reason
    if bucket == "clean_win" and mfe_capture_ratio < 0.5:
        return "uncategorized", reason
    return bucket, reason


def _initialize_evidence_fields(result: dict, post_exit_bars: Any) -> None:
    result.update(
        {
            "timeframe": _DEFAULT_TIMEFRAME,
            "path_evidence_status": "unavailable",
            "path_evidence_reason": None,
            "ohlcv_exchange": None,
            "ohlcv_market_type": None,
            "ohlcv_identity_source": None,
            "holding_bars_expected": None,
            "holding_bars_observed": None,
            "bars_in_trade": None,
            "boundary_bars_excluded": None,
            "entry_ts_ms": None,
            "exit_ts_ms": None,
            "mfe_pct": None,
            "mae_pct": None,
            "mfe_lower_bound_pct": None,
            "mfe_upper_bound_pct": None,
            "mae_lower_bound_pct": None,
            "mae_upper_bound_pct": None,
            "realized_return_pct": result.get("realized_pnl_pct"),
            "mfe_capture_ratio": None,
            "mae_to_mfe_ratio": None,
            "time_to_mfe_bars": None,
            "time_to_mae_bars": None,
            "exit_efficiency_score": None,
            "post_exit_bars_requested": post_exit_bars,
            "post_exit_bars_observed": 0,
            "post_exit_window_start_ts_ms": None,
            "post_exit_window_end_ts_ms": None,
            "post_exit_mfe_pct": None,
            "post_exit_mae_pct": None,
            "post_exit_path_status": "not_requested" if post_exit_bars == 0 else "unavailable",
            "post_exit_path_reason": None,
            "post_exit_noncausal_observation": None,
            "post_exit_observation_reason": None,
            "path_bucket": "insufficient_path_data",
            "path_bucket_reason": None,
            "bounded_path_bucket": None,
            "bounded_path_bucket_reason": None,
            "noncausal_path_observation": None,
            "first_stop_barrier_status": "unavailable",
            "first_stop_barrier_reason": None,
            "first_stop_barrier_open_ms": None,
            "pre_stop_mfe_lower_bound_pct": None,
        }
    )


def _set_insufficient(result: dict, reason: str = "missing_ohlcv_or_timestamps") -> None:
    """Mark evidence unavailable while preserving any proven identity/timestamps."""
    result["path_evidence_status"] = "unavailable"
    result["path_evidence_reason"] = reason
    result["path_bucket"] = "insufficient_path_data"
    result["path_bucket_reason"] = reason


def _floor_bar(ts_ms: int) -> int:
    return ts_ms - (ts_ms % _BAR_MS)


def _ceil_bar(ts_ms: int) -> int:
    return ((ts_ms + _BAR_MS - 1) // _BAR_MS) * _BAR_MS


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _positive_float(value: Any) -> float | None:
    parsed = _finite_float(value)
    return parsed if parsed is not None and parsed > 0 else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _physical_market_type(value: Any) -> str | None:
    """Map known Position aliases to the physical OHLCV instrument type."""
    normalized = str(value or "").strip().lower()
    return _PHYSICAL_MARKET_TYPES.get(normalized)


def _iso_to_ms(iso: str | None) -> int | None:
    """Convert an ISO timestamp to UTC milliseconds."""
    if not iso:
        return None
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError, OverflowError):
        return None
