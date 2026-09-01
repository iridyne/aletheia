"""Pure, causal Momentum Entry Geometry v2 compiler.

This module deliberately has no repository, provider, model, Risk, or Gateway
access. It turns one exact Candidate/feature snapshot and an admitted 21-bar
15-minute window into immutable treatment geometry. The active Entry Plan and
Fast Monitoring seams consume this contract in Shadow before any qualification.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from numbers import Real
from typing import Any, Literal

GEOMETRY_VERSION = "momentum-entry-geometry/v2"
ATR_VERSION = "atr-true-range-sma-14/v1"
BAR_TIMEFRAME = "15m"
BAR_MS = 15 * 60 * 1000
BAR_COUNT = 21
BREAKOUT_BAR_COUNT = 20
ATR_PERIOD = 14
PULLBACK_ATR = 0.25
INVALIDATION_ATR = 1.0

Direction = Literal["long", "short"]


class MomentumGeometryUnavailable(ValueError):
    """The admitted facts cannot prove a safe, causal v2 geometry."""


@dataclass(frozen=True, slots=True)
class MomentumEntryGeometryV2:
    """Immutable geometry and provenance for one exact Candidate occurrence."""

    geometry_version: str
    atr_version: str
    source_cutoff_at: str
    source_candle_ids: tuple[str, ...]
    source_hash: str
    candidate_id: str
    candidate_snapshot_id: str
    candidate_asset_id: str
    feature_snapshot_id: str
    feature_snapshot_at: str
    instrument: tuple[str, str, str]
    direction: Direction
    breakout_level: float
    atr_abs: float
    pullback_band: tuple[float, float]
    sweep_band: tuple[float, float]
    entry_zone: tuple[float, float]
    invalidation_price: float
    stop_price: float
    target_price: float
    minimum_risk_reward: float
    valid_until: str
    geometry_fingerprint: str

    def trigger_params(self) -> dict[str, Any]:
        """Return the exact v2 TriggerSpec parameter payload."""

        return {
            "breakout_level": self.breakout_level,
            "pullback_low": self.pullback_band[0],
            "pullback_high": self.pullback_band[1],
            "entry_low": self.entry_zone[0],
            "entry_high": self.entry_zone[1],
            "invalidation_price": self.invalidation_price,
            "sweep_low": self.sweep_band[0],
            "sweep_high": self.sweep_band[1],
            "atr_abs": self.atr_abs,
            "geometry_version": self.geometry_version,
            "geometry_fingerprint": self.geometry_fingerprint,
        }

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable immutable evidence projection."""

        return {
            "geometry_version": self.geometry_version,
            "atr_version": self.atr_version,
            "source_cutoff_at": self.source_cutoff_at,
            "source_candle_ids": list(self.source_candle_ids),
            "source_hash": self.source_hash,
            "candidate_id": self.candidate_id,
            "candidate_snapshot_id": self.candidate_snapshot_id,
            "candidate_asset_id": self.candidate_asset_id,
            "feature_snapshot_id": self.feature_snapshot_id,
            "feature_snapshot_at": self.feature_snapshot_at,
            "instrument": list(self.instrument),
            "direction": self.direction,
            "breakout_level": self.breakout_level,
            "atr_abs": self.atr_abs,
            "pullback_band": list(self.pullback_band),
            "sweep_band": list(self.sweep_band),
            "entry_zone": list(self.entry_zone),
            "invalidation_price": self.invalidation_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "minimum_risk_reward": self.minimum_risk_reward,
            "valid_until": self.valid_until,
            "geometry_fingerprint": self.geometry_fingerprint,
        }


def _fail(reason: str) -> MomentumGeometryUnavailable:
    return MomentumGeometryUnavailable(reason)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{field}_unavailable")
    return value.strip()


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _fail(f"{field}_invalid")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise _fail(f"{field}_invalid")
    return result


def _instant(value: Any, field: str) -> tuple[str, int]:
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise _fail(f"{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise _fail(f"{field}_timezone_required")
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(), int(parsed.timestamp() * 1000)


def _bar_number(bar: Mapping[str, Any], field: str, *, positive: bool = False) -> float:
    try:
        return _number(bar[field], f"bar_{field}", positive=positive)
    except KeyError as exc:
        raise _fail(f"bar_{field}_unavailable") from exc


def _validate_identity(
    candidate: Mapping[str, Any],
    feature: Mapping[str, Any],
    *,
    direction: Direction,
    cutoff_ms: int,
) -> tuple[str, str, str, str, str, str, str, int]:
    candidate_id = _text(candidate.get("id"), "candidate_id")
    snapshot_id = _text(candidate.get("snapshot_id"), "candidate_snapshot_id")
    asset_id = _text(candidate.get("asset_id"), "candidate_asset_id")
    venue = _text(candidate.get("venue"), "candidate_venue").lower()
    symbol = _text(candidate.get("symbol"), "candidate_symbol")
    market_type = _text(candidate.get("market_type"), "candidate_market_type")
    if market_type not in {"spot", "perp"}:
        raise _fail("candidate_market_type_invalid")
    if direction == "short" and market_type == "spot":
        raise _fail("momentum_spot_short_forbidden")
    if str(candidate.get("scanner_family") or "").lower() != "momentum":
        raise _fail("candidate_not_momentum")
    if candidate.get("scanner_direction") != direction:
        raise _fail("direction_identity_mismatch")

    feature_id = _text(feature.get("id"), "feature_snapshot_id")
    if _text(feature.get("asset_id"), "feature_asset_id") != asset_id:
        raise _fail("feature_candidate_identity_mismatch")
    _, feature_ms = _instant(feature.get("ts"), "feature_snapshot_ts")
    if feature_ms > cutoff_ms:
        raise _fail("feature_snapshot_after_cutoff")
    return (
        candidate_id,
        snapshot_id,
        asset_id,
        venue,
        symbol,
        market_type,
        feature_id,
        feature_ms,
    )


def _validate_bars(
    candles: Sequence[Mapping[str, Any]],
    *,
    venue: str,
    symbol: str,
    market_type: str,
    cutoff_ms: int,
) -> tuple[dict[str, Any], ...]:
    if len(candles) != BAR_COUNT:
        raise _fail("momentum_geometry_requires_21_bars")
    if any(not isinstance(bar, Mapping) for bar in candles):
        raise _fail("momentum_geometry_bar_invalid")

    timestamps: list[int] = []
    providers: set[str] = set()
    rows: list[dict[str, Any]] = []
    for raw in candles:
        try:
            ts = raw["ts"]
            if isinstance(ts, bool) or not isinstance(ts, int):
                raise _fail("bar_timestamp_invalid")
            if ts < 0 or ts % BAR_MS:
                raise _fail("bar_timestamp_invalid")
            if raw.get("is_closed") is False:
                raise _fail("bar_forming")
            provider = _text(raw.get("provider"), "bar_provider")
            providers.add(provider)
            for key, expected in (
                ("exchange", venue),
                ("symbol", symbol),
                ("market_type", market_type),
                ("timeframe", BAR_TIMEFRAME),
            ):
                actual = raw.get(key)
                if actual is not None and str(actual) != expected:
                    raise _fail("bar_instrument_identity_mismatch")
            first_seen = raw.get("first_persisted_at")
            last_mutated = raw.get("last_mutated_at")
            if first_seen is None or last_mutated is None:
                raise _fail("bar_availability_provenance_unavailable")
            _, first_seen_ms = _instant(first_seen, "bar_first_persisted_at")
            _, last_mutated_ms = _instant(last_mutated, "bar_last_mutated_at")
            if first_seen_ms > last_mutated_ms:
                raise _fail("bar_provenance_order_invalid")
            if first_seen_ms > cutoff_ms:
                raise _fail("bar_persisted_after_cutoff")
            if last_mutated_ms > cutoff_ms:
                raise _fail("bar_mutated_after_cutoff")
            open_ = _bar_number(raw, "open", positive=True)
            high = _bar_number(raw, "high", positive=True)
            low = _bar_number(raw, "low", positive=True)
            close = _bar_number(raw, "close", positive=True)
            volume = _bar_number(raw, "volume")
            if volume < 0 or low > min(open_, close) or high < max(open_, close) or low > high:
                raise _fail("bar_ohlcv_invalid")
        except KeyError as exc:
            raise _fail("bar_field_unavailable") from exc
        timestamps.append(ts)
        rows.append(
            {
                "ts": ts,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "provider": provider,
                "first_persisted_at": first_seen,
                "last_mutated_at": last_mutated,
            }
        )

    if len(providers) != 1:
        raise _fail("bar_provider_identity_unavailable")
    if any(right - left != BAR_MS for left, right in zip(timestamps, timestamps[1:])):
        raise _fail("bar_sequence_discontinuous")
    expected_last = ((cutoff_ms - BAR_MS) // BAR_MS) * BAR_MS
    if timestamps[-1] != expected_last:
        raise _fail("latest_completed_bar_missing")
    return tuple(rows)


def _source_hash(
    *,
    candidate_id: str,
    snapshot_id: str,
    asset_id: str,
    feature_id: str,
    feature_ms: int,
    instrument: tuple[str, str, str],
    direction: Direction,
    cutoff_at: str,
    bars: Sequence[Mapping[str, Any]],
) -> str:
    payload = {
        "candidate_asset_id": asset_id,
        "candidate_id": candidate_id,
        "candidate_snapshot_id": snapshot_id,
        "feature_snapshot_id": feature_id,
        "feature_snapshot_at_ms": feature_ms,
        "instrument": instrument,
        "direction": direction,
        "source_cutoff_at": cutoff_at,
        "bars": [
            [
                bar["ts"],
                bar["open"],
                bar["high"],
                bar["low"],
                bar["close"],
                bar["volume"],
                bar["provider"],
                bar["first_persisted_at"],
                bar["last_mutated_at"],
            ]
            for bar in bars
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _atr_abs(bars: Sequence[Mapping[str, Any]]) -> float:
    true_ranges = []
    for index in range(BAR_COUNT - ATR_PERIOD, BAR_COUNT):
        current = bars[index]
        previous = bars[index - 1]
        true_ranges.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            )
        )
    atr = sum(true_ranges) / ATR_PERIOD
    if not math.isfinite(atr) or atr <= 0:
        raise _fail("atr_unavailable")
    return atr


def _risk_reward(
    *,
    direction: Direction,
    entry: float,
    stop: float,
    target: float,
) -> float:
    if direction == "long":
        risk = entry - stop
        reward = target - entry
    else:
        risk = stop - entry
        reward = entry - target
    if risk <= 0 or reward <= 0:
        raise _fail("geometry_risk_reward_invalid")
    ratio = reward / risk
    if not math.isfinite(ratio):
        raise _fail("geometry_risk_reward_invalid")
    return ratio


def compile_momentum_entry_geometry_v2(
    *,
    candidate: object,
    feature: object,
    candles: object,
    direction: Direction,
    minimum_risk_reward: float,
    source_cutoff_at: str | datetime,
    valid_until: str | datetime,
) -> MomentumEntryGeometryV2:
    """Compile one causal Momentum geometry or raise a typed unavailable error."""

    if not isinstance(candidate, Mapping) or not isinstance(feature, Mapping):
        raise _fail("candidate_feature_facts_invalid")
    if not isinstance(candles, Sequence) or isinstance(candles, (str, bytes)):
        raise _fail("momentum_geometry_bar_invalid")
    if direction not in {"long", "short"}:
        raise _fail("direction_invalid")
    cutoff_at, cutoff_ms = _instant(source_cutoff_at, "source_cutoff_at")
    valid_until_at, valid_until_ms = _instant(valid_until, "valid_until")
    if valid_until_ms <= cutoff_ms:
        raise _fail("geometry_validity_window_invalid")
    minimum_risk_reward = _number(minimum_risk_reward, "minimum_risk_reward", positive=True)
    if minimum_risk_reward > 10.0:
        raise _fail("minimum_risk_reward_out_of_range")

    (
        candidate_id,
        snapshot_id,
        asset_id,
        venue,
        symbol,
        market_type,
        feature_id,
        feature_ms,
    ) = _validate_identity(candidate, feature, direction=direction, cutoff_ms=cutoff_ms)
    bars = _validate_bars(
        candles,
        venue=venue,
        symbol=symbol,
        market_type=market_type,
        cutoff_ms=cutoff_ms,
    )
    breakout_bars = bars[:BREAKOUT_BAR_COUNT]
    confirmation = bars[BREAKOUT_BAR_COUNT]
    breakout = (
        max(bar["high"] for bar in breakout_bars)
        if direction == "long"
        else min(bar["low"] for bar in breakout_bars)
    )
    confirmed = (
        confirmation["close"] > breakout
        if direction == "long"
        else confirmation["close"] < breakout
    )
    if not confirmed:
        raise _fail("breakout_confirmation_missing")

    atr_abs = _atr_abs(bars)
    quarter = PULLBACK_ATR * atr_abs
    if direction == "long":
        pullback_band = (breakout - quarter, breakout + quarter)
        sweep_band = (breakout - quarter, breakout)
        entry_zone = (breakout, breakout + quarter)
        invalidation = breakout - INVALIDATION_ATR * atr_abs
        worst_entry = entry_zone[1]
        target = worst_entry + minimum_risk_reward * (worst_entry - invalidation)
    else:
        pullback_band = (breakout - quarter, breakout + quarter)
        sweep_band = (breakout, breakout + quarter)
        entry_zone = (breakout - quarter, breakout)
        invalidation = breakout + INVALIDATION_ATR * atr_abs
        worst_entry = entry_zone[0]
        target = worst_entry - minimum_risk_reward * (invalidation - worst_entry)

    derived = (*pullback_band, *sweep_band, *entry_zone, invalidation, target)
    if any(not math.isfinite(value) or value <= 0 for value in derived):
        raise _fail("geometry_price_invalid")
    if direction == "long" and not (invalidation < entry_zone[0] <= entry_zone[1] < target):
        raise _fail("long_geometry_wrong_side")
    if direction == "short" and not (target < entry_zone[0] <= entry_zone[1] < invalidation):
        raise _fail("short_geometry_wrong_side")
    for entry in entry_zone:
        if _risk_reward(direction=direction, entry=entry, stop=invalidation, target=target) + 1e-12 < minimum_risk_reward:
            raise _fail("geometry_risk_reward_below_minimum")

    instrument = (venue, symbol, market_type)
    source_hash = _source_hash(
        candidate_id=candidate_id,
        snapshot_id=snapshot_id,
        asset_id=asset_id,
        feature_id=feature_id,
        feature_ms=feature_ms,
        instrument=instrument,
        direction=direction,
        cutoff_at=cutoff_at,
        bars=bars,
    )
    source_candle_ids = tuple(
        f"{venue}:{symbol}:{market_type}:{BAR_TIMEFRAME}:{bar['ts']}" for bar in bars
    )
    fingerprint_payload = {
        "geometry_version": GEOMETRY_VERSION,
        "atr_version": ATR_VERSION,
        "source_hash": source_hash,
        "candidate_id": candidate_id,
        "candidate_snapshot_id": snapshot_id,
        "feature_snapshot_id": feature_id,
        "instrument": instrument,
        "direction": direction,
        "breakout_level": breakout,
        "atr_abs": atr_abs,
        "pullback_band": pullback_band,
        "sweep_band": sweep_band,
        "entry_zone": entry_zone,
        "invalidation_price": invalidation,
        "stop_price": invalidation,
        "target_price": target,
        "minimum_risk_reward": minimum_risk_reward,
        "source_cutoff_at": cutoff_at,
        "valid_until": valid_until_at,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    return MomentumEntryGeometryV2(
        geometry_version=GEOMETRY_VERSION,
        atr_version=ATR_VERSION,
        source_cutoff_at=cutoff_at,
        source_candle_ids=source_candle_ids,
        source_hash=source_hash,
        candidate_id=candidate_id,
        candidate_snapshot_id=snapshot_id,
        candidate_asset_id=asset_id,
        feature_snapshot_id=feature_id,
        feature_snapshot_at=datetime.fromtimestamp(feature_ms / 1000, tz=timezone.utc).isoformat(),
        instrument=instrument,
        direction=direction,
        breakout_level=breakout,
        atr_abs=atr_abs,
        pullback_band=pullback_band,
        sweep_band=sweep_band,
        entry_zone=entry_zone,
        invalidation_price=invalidation,
        stop_price=invalidation,
        target_price=target,
        minimum_risk_reward=minimum_risk_reward,
        valid_until=valid_until_at,
        geometry_fingerprint=fingerprint,
    )
