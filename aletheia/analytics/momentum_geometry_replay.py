"""Read-only paired replay for the frozen Momentum geometry measurement contract."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from aletheia.storage import repositories as repo

CONTRACT_VERSION = "momentum-geometry-measurement/v2"
CONTROL_SPEC_ID = "momentum-retrace-reclaim"
CONTROL_VERSION = "v1"
CONTROL_IDENTITY = f"{CONTROL_SPEC_ID}/{CONTROL_VERSION}"
TREATMENT_VERSION = "v2"
TREATMENT_IDENTITY = f"{CONTROL_SPEC_ID}/{TREATMENT_VERSION}"
GEOMETRY_VERSION = "momentum-entry-geometry/v2"
ATR_VERSION = "atr-true-range-sma-14/v1"
COST_MODEL_VERSION = "entry-geometry-cost-after/v1"
OUTCOME_VERSION = "momentum-candidate-4h/v1"
_BAR_MS = 15 * 60 * 1000
_HORIZON_MS = 4 * 60 * 60 * 1000


def measurement_contract() -> dict[str, Any]:
    """Return the preregistered contract; this function never reads or writes state."""

    return {
        "contract_version": CONTRACT_VERSION,
        "paired_unit": (
            "entry_plan_id + candidate_id + exact venue/symbol/market + direction + "
            "plan approved_at cutoff + behavior/cost/outcome identities"
        ),
        "control_version": CONTROL_IDENTITY,
        "treatment_version": TREATMENT_IDENTITY,
        "geometry_version": GEOMETRY_VERSION,
        "atr_version": ATR_VERSION,
        "cost_model_version": COST_MODEL_VERSION,
        "outcome_version": OUTCOME_VERSION,
        "no_entry_utility": 0.0,
        "outcome_horizon": "4h_after_plan_t0",
        "sample_rule": {
            "minimum_eligible_candidates": 100,
            "minimum_evaluable_pairs": 50,
            "minimum_utc_dates": 7,
            "stopping_rule": "one_fixed_look_after_all_minima_no_optional_stopping",
        },
        "exclusions": (
            "non_momentum",
            "non_triggered_plan",
            "wrong_instrument",
            "missing_or_mutated_t0_bar",
            "forming_or_discontinuous_bar",
            "non_confirming_breakout",
            "missing_exact_cost_or_outcome_fact",
        ),
        "overlap_rule": "one exact Candidate occurrence and Entry Plan per paired unit; never pool behavior generations",
        "primary_metric": "treatment_cost_after_utility_per_eligible_candidate_minus_control",
        "metrics": (
            "geometry_computable_rate",
            "pullback_rate",
            "sweep_rate",
            "reclaim_rate",
            "trigger_rate",
            "candidate_to_trigger_delay",
            "cost_after_return_1h_4h_24h",
            "mfe",
            "mae",
            "stop_hit",
            "target_hit",
            "spread_slippage_funding_fees",
            "typed_no_trigger_and_unavailable_reasons",
        ),
        "historical_microstructure": "unavailable_without_retained_quote_trade_books5",
        "durable_fact_map": {
            "candidate": "universe_members selected Candidate; exact venue requires non-null venue",
            "scanner_and_denominator": (
                "universe_scanner_runs + filter_candidate_outcomes retain selected and dropped learning keys"
            ),
            "features": (
                "universe_feature_snapshots are asset/time keyed but not Candidate-linked; exact Candidate feature identity unavailable"
            ),
            "causal_15m_bars": (
                "market_ohlcv exact instrument + ts + first_persisted_at/last_mutated_at; causal only when persisted and unchanged by T0"
            ),
            "planning": "candidate_planning_generations + immutable entry_plans",
            "trigger": (
                "fast_trigger_market_snapshots are legacy setup keyed; active EntryPlan quote/trade/books5 history is not retained"
            ),
            "paper_outcome": "EntryPlan -> Order -> Fill -> Position is durable when execution occurred",
        },
    }


def _instant_ms(value: object) -> int:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non_finite_number")
    return result


def _source_hash(instrument: str, bars: Sequence[Mapping[str, Any]]) -> str:
    payload = {
        "instrument": instrument,
        "bars": [
            [
                int(bar["ts"]),
                _finite(bar["open"]),
                _finite(bar["high"]),
                _finite(bar["low"]),
                _finite(bar["close"]),
                _finite(bar["volume"]),
                str(bar.get("provider") or ""),
            ]
            for bar in bars
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _unavailable(reason: str, *, control_breakout_level: float | None = None) -> dict[str, Any]:
    return {
        "verdict": "unavailable",
        "reason": reason,
        "control": {
            "geometry_version": CONTROL_IDENTITY,
            "breakout_level": control_breakout_level,
        },
        "treatment": None,
    }


def _validate_t0_bars(
    bars: Sequence[Mapping[str, Any]],
    *,
    cutoff_ms: int,
) -> str | None:
    if len(bars) != 21:
        return "t0_completed_15m_bars_not_21"
    timestamps = [int(bar["ts"]) for bar in bars]
    if any(right - left != _BAR_MS for left, right in zip(timestamps, timestamps[1:])):
        return "t0_15m_bars_discontinuous"
    if timestamps[-1] + _BAR_MS > cutoff_ms:
        return "t0_bar_forming_or_future"
    providers = {str(bar.get("provider") or "") for bar in bars}
    if "" in providers or len(providers) != 1:
        return "t0_bar_provider_identity_unavailable"
    for bar in bars:
        first_seen = bar.get("first_persisted_at")
        last_mutated = bar.get("last_mutated_at")
        if first_seen is None:
            return "t0_bar_availability_provenance_unavailable"
        if _instant_ms(first_seen) > cutoff_ms:
            return "t0_bar_persisted_after_cutoff"
        if last_mutated is None or _instant_ms(last_mutated) > cutoff_ms:
            return "t0_bar_mutated_after_cutoff"
        try:
            open_, high, low, close = (_finite(bar[key]) for key in ("open", "high", "low", "close"))
        except (KeyError, TypeError, ValueError, OverflowError):
            return "t0_bar_ohlc_invalid"
        if min(open_, high, low, close) <= 0 or low > min(open_, close) or high < max(open_, close):
            return "t0_bar_ohlc_invalid"
    return None


def _atr_abs(bars: Sequence[Mapping[str, Any]]) -> float:
    true_ranges = []
    for previous, current in zip(bars[-15:-1], bars[-14:]):
        high = _finite(current["high"])
        low = _finite(current["low"])
        previous_close = _finite(previous["close"])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    atr = sum(true_ranges) / len(true_ranges)
    if atr <= 0 or not math.isfinite(atr):
        raise ValueError("atr_unavailable")
    return atr


def _candle_path(
    bars: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    breakout: float,
    pullback_band: tuple[float, float],
    sweep_band: tuple[float, float] | None,
    entry_zone: tuple[float, float],
) -> dict[str, Any]:
    pullback_at = sweep_at = reclaim_at = None
    for bar in bars:
        ts, high, low, close = (
            int(bar["ts"]),
            _finite(bar["high"]),
            _finite(bar["low"]),
            _finite(bar["close"]),
        )
        overlaps_pullback = high >= pullback_band[0] and low <= pullback_band[1]
        crossed = low <= breakout if direction == "long" else high >= breakout
        if pullback_at is None and overlaps_pullback and crossed:
            pullback_at = ts
        if pullback_at is None:
            continue
        if sweep_band is None:
            sweep_at = pullback_at
        elif direction == "long" and low < breakout and high >= sweep_band[0]:
            sweep_at = sweep_at or ts
        elif direction == "short" and high > breakout and low <= sweep_band[1]:
            sweep_at = sweep_at or ts
        if sweep_at is None:
            continue
        reclaimed = close > breakout if direction == "long" else close < breakout
        if reclaimed and entry_zone[0] <= close <= entry_zone[1]:
            reclaim_at = ts
            break
    return {
        "pullback_seen_at": pullback_at,
        "sweep_seen_at": sweep_at,
        "reclaim_seen_at": reclaim_at,
        "candle_geometry_status": "reclaim_observed" if reclaim_at is not None else "no_reclaim",
        "trigger_status": "unavailable",
        "trigger_unavailable_reason": "historical_microstructure_not_retained",
    }


def replay_momentum_geometry_pair(
    *,
    candidate: Mapping[str, Any],
    plan: Mapping[str, Any],
    t0_bars: Sequence[Mapping[str, Any]],
    post_t0_bars: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Pair current v1 geometry with causal candle-only v2 treatment geometry."""

    candidate_price = candidate.get("entry_price_usd")
    try:
        candidate_price = _finite(candidate_price)
    except (TypeError, ValueError, OverflowError):
        return _unavailable("candidate_price_unavailable")
    if str(candidate.get("scanner_family") or "").lower() != "momentum":
        return _unavailable("candidate_not_momentum", control_breakout_level=candidate_price)
    if plan.get("execution_mode") != "TRIGGERED":
        return _unavailable("entry_plan_not_triggered", control_breakout_level=candidate_price)
    if plan.get("trigger_spec_id") != CONTROL_SPEC_ID or plan.get("trigger_spec_version") != CONTROL_VERSION:
        return _unavailable("control_trigger_identity_not_v1", control_breakout_level=candidate_price)
    direction = str(plan.get("direction") or "")
    if direction not in {"long", "short"} or direction != candidate.get("scanner_direction"):
        return _unavailable("direction_identity_mismatch", control_breakout_level=candidate_price)
    trigger_params = plan.get("trigger_params")
    if not isinstance(trigger_params, Mapping):
        return _unavailable("control_trigger_params_unavailable", control_breakout_level=candidate_price)
    try:
        control_breakout = _finite(trigger_params.get("breakout_level"))
    except (TypeError, ValueError, OverflowError):
        return _unavailable("control_breakout_unavailable", control_breakout_level=candidate_price)
    if not math.isclose(control_breakout, candidate_price, rel_tol=0.0, abs_tol=1e-12):
        return _unavailable("control_breakout_not_candidate_price", control_breakout_level=control_breakout)
    try:
        cutoff_ms = _instant_ms(plan["approved_at"])
    except (KeyError, TypeError, ValueError):
        return _unavailable("plan_t0_cutoff_unavailable", control_breakout_level=control_breakout)
    bar_error = _validate_t0_bars(t0_bars, cutoff_ms=cutoff_ms)
    if bar_error:
        return _unavailable(bar_error, control_breakout_level=control_breakout)

    breakout_bars, confirmation = t0_bars[:20], t0_bars[20]
    breakout = (
        max(_finite(bar["high"]) for bar in breakout_bars)
        if direction == "long"
        else min(_finite(bar["low"]) for bar in breakout_bars)
    )
    confirmed = (
        _finite(confirmation["close"]) > breakout
        if direction == "long"
        else _finite(confirmation["close"]) < breakout
    )
    if not confirmed:
        return _unavailable("t0_breakout_not_confirmed", control_breakout_level=control_breakout)
    try:
        atr = _atr_abs(t0_bars)
    except ValueError:
        return _unavailable("t0_atr_unavailable", control_breakout_level=control_breakout)

    offset = 0.25 * atr
    if direction == "long":
        pullback_band = (breakout - offset, breakout + offset)
        sweep_band = (breakout - offset, breakout)
        entry_zone = (breakout, breakout + offset)
        invalidation = breakout - atr
    else:
        pullback_band = (breakout - offset, breakout + offset)
        sweep_band = (breakout, breakout + offset)
        entry_zone = (breakout - offset, breakout)
        invalidation = breakout + atr
    control_zone = (_finite(plan["entry_zone"][0]), _finite(plan["entry_zone"][1]))
    control_reference = sum(control_zone) / 2
    control_risk = abs(control_reference - _finite(plan["stop_price"]))
    if control_risk <= 0:
        return _unavailable("control_risk_reward_unavailable", control_breakout_level=control_breakout)
    minimum_risk_reward = abs(_finite(plan["target_price"]) - control_reference) / control_risk
    worst_entry = entry_zone[1] if direction == "long" else entry_zone[0]
    target = (
        worst_entry + minimum_risk_reward * (worst_entry - invalidation)
        if direction == "long"
        else worst_entry - minimum_risk_reward * (invalidation - worst_entry)
    )
    control_path = _candle_path(
        post_t0_bars,
        direction=direction,
        breakout=control_breakout,
        pullback_band=(
            _finite(trigger_params["pullback_low"]),
            _finite(trigger_params["pullback_high"]),
        ),
        sweep_band=None,
        entry_zone=control_zone,
    )
    treatment_path = _candle_path(
        post_t0_bars,
        direction=direction,
        breakout=breakout,
        pullback_band=pullback_band,
        sweep_band=sweep_band,
        entry_zone=entry_zone,
    )
    instrument = f"{plan['venue']}:{plan['symbol']}:{plan['market_type']}"
    source_hash = _source_hash(instrument, t0_bars)
    return {
        "verdict": "candle_geometry_evaluable",
        "reason": None,
        "paired_unit": {
            "entry_plan_id": plan["plan_id"],
            "candidate_id": candidate["id"],
            "instrument": instrument,
            "direction": direction,
            "source_cutoff_at": plan["approved_at"],
            "risk_policy_id": plan.get("risk_policy_id"),
            "risk_policy_revision": plan.get("risk_policy_revision"),
            "behavior_version": plan.get("behavior_version"),
            "cost_model_version": COST_MODEL_VERSION,
            "outcome_version": OUTCOME_VERSION,
        },
        "source_candle_ids": [f"{instrument}:15m:{int(bar['ts'])}" for bar in t0_bars],
        "source_hash": source_hash,
        "control": {
            "geometry_version": CONTROL_IDENTITY,
            "breakout_level": control_breakout,
            "breakout_source": "candidate.entry_price_usd",
            "entry_zone": control_zone,
            "invalidation_price": _finite(plan["invalidation_price"]),
            "target_price": _finite(plan["target_price"]),
            **control_path,
        },
        "treatment": {
            "geometry_version": GEOMETRY_VERSION,
            "trigger_spec_id": CONTROL_SPEC_ID,
            "trigger_spec_version": TREATMENT_VERSION,
            "trigger_spec_identity": TREATMENT_IDENTITY,
            "breakout_level": breakout,
            "atr_abs": atr,
            "atr_version": ATR_VERSION,
            "pullback_band": pullback_band,
            "sweep_band": sweep_band,
            "entry_zone": entry_zone,
            "invalidation_price": invalidation,
            "stop_price": invalidation,
            "target_price": target,
            "minimum_risk_reward": minimum_risk_reward,
            "source_cutoff_at": plan["approved_at"],
            "valid_until": plan["expires_at"],
            "geometry_fingerprint": hashlib.sha256(
                f"{source_hash}:{direction}:{breakout}:{atr}".encode()
            ).hexdigest(),
            **treatment_path,
        },
        "economics": {
            "geometry_lift": None,
            "status": "unavailable",
            "reason": "historical_microstructure_and_exact_execution_costs_not_retained",
            "no_entry_utility": 0.0,
            "spread_bps": candidate.get("spread_bps"),
            "slippage_bps": plan.get("max_slippage_bps"),
            "funding": None,
            "fees": None,
        },
    }


def build_momentum_geometry_replay(entry_plan_id: str) -> dict[str, Any]:
    """Load one immutable Entry Plan pair from SQLite without writing state."""

    row = repo.get_entry_plan(entry_plan_id)
    if row is None:
        raise ValueError("entry_plan_not_found")
    try:
        plan = json.loads(str(row["plan_json"]))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("entry_plan_payload_invalid") from exc
    candidate = repo.get_universe_member(str(row["candidate_id"]))
    if candidate is None:
        raise ValueError("entry_plan_candidate_not_found")
    cutoff_ms = _instant_ms(plan["approved_at"])
    bars = repo.get_ohlcv_candles(
        str(row["symbol"]),
        "15m",
        64,
        str(row["venue"]),
        str(row["market_type"]),
        before_ts=cutoff_ms,
        include_provenance=True,
    )
    completed = [bar for bar in bars if int(bar["ts"]) + _BAR_MS <= cutoff_ms]
    t0_bars = completed[-21:]
    future_start = math.ceil(cutoff_ms / _BAR_MS) * _BAR_MS
    future = repo.get_ohlcv_candles(
        str(row["symbol"]),
        "15m",
        64,
        str(row["venue"]),
        str(row["market_type"]),
        before_ts=cutoff_ms + _HORIZON_MS + _BAR_MS,
        include_provenance=True,
    )
    post_t0 = [bar for bar in future if future_start <= int(bar["ts"]) < cutoff_ms + _HORIZON_MS]
    report = replay_momentum_geometry_pair(
        candidate=candidate,
        plan=plan,
        t0_bars=t0_bars,
        post_t0_bars=post_t0,
    )
    return {"contract": measurement_contract(), "report": report}
