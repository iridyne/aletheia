"""Causal, exact-instrument market-structure derived data."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import math
import time
from typing import Literal, Sequence

from aletheia.data.ccxt_provider import timeframe_ms
from aletheia.data.providers import Candle, MarketDataProvider
from aletheia.data.symbols import InstrumentId


CONTRACT_VERSION = "market-structure-provider/v1"
CORE_ALGORITHM_VERSION = "causal-market-structure/v1"
COMPAT_ALGORITHM_VERSION = "market-structure/v2"
FVG_ALGORITHM_VERSION = "smc-fvg/0.0.27-v1"
FRACTAL_K = 2
MIN_BARS = 30
WINDOW_BARS = 120
FETCH_LIMIT = 121
ATR_PERIOD = 14
ATR_METHOD = "simple_latest_tr_mean"
BREAK_TOLERANCE_ATR = 0.05
COMPAT_MAX_SWING_AGE = 60

_PARAMS = {
    "atr_method": ATR_METHOD,
    "atr_period": ATR_PERIOD,
    "break_tolerance_atr": "0.05",
    "fractal_k": FRACTAL_K,
    "min_bars": MIN_BARS,
    "window_bars": WINDOW_BARS,
}


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _float_identity(value: float) -> str:
    return format(value, ".17g")


def _sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


PARAMS_HASH = _sha256(_PARAMS)

StructureState = Literal["bullish", "bearish", "range", "unconfirmed"]
Availability = Literal["available", "unavailable"]
HistoryQuality = Literal["complete", "partial", "unavailable"]
FVGAvailability = Literal["not_requested", "available", "unavailable"]


@dataclass(frozen=True, slots=True)
class StructureProvenance:
    instrument: InstrumentId
    timeframe: str
    requested_as_of_ms: int | None
    as_of_ms: int | None
    first_bar_open_at_ms: int | None
    last_bar_open_at_ms: int | None
    first_bar_close_at_ms: int | None
    last_bar_close_at_ms: int | None
    admitted_bar_count: int
    requested_limit: int
    fetched_bar_count: int
    provider_outcome: str | None
    provider_failure_reason: str | None
    source_provider: str | None
    source_exchange: str | None
    history_quality: HistoryQuality
    geometry_fingerprint: str | None
    source_candle_fingerprint: str | None
    core_algorithm_version: str
    params_hash: str
    fvg_algorithm_version: str | None
    computed_at_ms: int

    @property
    def instrument_id(self) -> InstrumentId:
        return self.instrument


@dataclass(frozen=True, slots=True)
class SwingPoint:
    id: str
    kind: Literal["high", "low"]
    price: float
    origin_at_ms: int
    confirmed_at_ms: int
    known_at_ms: int
    core_algorithm_version: str
    params_hash: str


@dataclass(frozen=True, slots=True)
class StructureEvent:
    id: str
    type: Literal["BOS", "CHOCH"]
    direction: Literal["bullish", "bearish"]
    broken_swing_id: str
    broken_level: float
    origin_at_ms: int
    confirmed_at_ms: int
    break_at_ms: int
    known_at_ms: int
    breaking_close: float
    atr14: float
    break_tolerance: float
    pre_break_structure_state: StructureState
    core_algorithm_version: str
    params_hash: str


@dataclass(frozen=True, slots=True)
class StructureZone:
    id: str
    type: Literal["FVG"]
    direction: Literal["bullish", "bearish"]
    top: float
    bottom: float
    origin_at_ms: int
    known_at_ms: int
    status: Literal["open", "mitigated"]
    mitigated_at_ms: int | None
    fvg_algorithm_version: str


@dataclass(frozen=True, slots=True)
class MarketStructureSnapshot:
    contract_version: str
    instrument: InstrumentId
    timeframe: str
    requested_as_of_ms: int | None
    as_of_ms: int | None
    availability: Availability
    unavailable_reason: str | None
    structure_state: StructureState | None
    atr14: float | None
    break_tolerance: float | None
    swings: tuple[SwingPoint, ...]
    events: tuple[StructureEvent, ...]
    latest_event: StructureEvent | None
    fvg_availability: FVGAvailability
    fvg_unavailable_reason: str | None
    fvg_zones: tuple[StructureZone, ...]
    provenance: StructureProvenance

    @property
    def instrument_id(self) -> InstrumentId:
        return self.instrument


@dataclass(frozen=True, slots=True)
class GeometrySwing:
    kind: Literal["high", "low"]
    price: float
    bar_index: int
    origin_at_ms: int
    confirmed_at_ms: int
    known_at_ms: int


@dataclass(frozen=True, slots=True)
class GeometryEvent:
    type: Literal["BOS", "CHOCH"]
    direction: Literal["bullish", "bearish"]
    broken_swing: GeometrySwing
    break_at_ms: int
    known_at_ms: int
    breaking_close: float
    atr14: float
    break_tolerance: float
    pre_break_structure_state: StructureState


@dataclass(frozen=True, slots=True)
class MarketStructureGeometry:
    availability: Availability
    unavailable_reason: str | None
    structure_state: StructureState | None
    atr14: float | None
    break_tolerance: float | None
    swings: tuple[GeometrySwing, ...]
    events: tuple[GeometryEvent, ...]
    geometry_fingerprint: str | None


def _geometry_bar_payload(candle: Candle) -> list[object]:
    return [
        candle.timestamp,
        _float_identity(candle.open),
        _float_identity(candle.high),
        _float_identity(candle.low),
        _float_identity(candle.close),
        _float_identity(candle.volume),
        candle.is_closed,
    ]


def _source_bar_payload(candle: Candle) -> list[object]:
    return _geometry_bar_payload(candle) + [candle.provider, candle.exchange]


def _series_error(candles: Sequence[Candle], tf_ms: int) -> str | None:
    for candle in candles:
        if not isinstance(candle, Candle) or not candle.is_closed:
            return "invalid_candle"
        values = (
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
        )
        if (
            isinstance(candle.timestamp, bool)
            or not isinstance(candle.timestamp, int)
            or candle.timestamp < 0
            or any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in values
            )
            or not all(math.isfinite(value) for value in values)
            or min(candle.open, candle.high, candle.low, candle.close) <= 0
            or candle.volume < 0
            or candle.high < max(candle.open, candle.close)
            or candle.low > min(candle.open, candle.close)
        ):
            return "invalid_candle"
    for left, right in zip(candles, candles[1:]):
        if right.timestamp <= left.timestamp:
            return "duplicate_or_out_of_order"
        if right.timestamp - left.timestamp != tf_ms:
            return "candle_gap"
    return None


def _structure_state(
    highs: Sequence[GeometrySwing], lows: Sequence[GeometrySwing]
) -> StructureState:
    if len(highs) < 2 or len(lows) < 2:
        return "unconfirmed"
    if highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price:
        return "bullish"
    if highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price:
        return "bearish"
    return "range"


def _atr14_at(candles: Sequence[Candle], index: int) -> float | None:
    if index < ATR_PERIOD:
        return None
    true_ranges = [
        max(
            candles[item].high - candles[item].low,
            abs(candles[item].high - candles[item - 1].close),
            abs(candles[item].low - candles[item - 1].close),
        )
        for item in range(index - ATR_PERIOD + 1, index + 1)
    ]
    return sum(true_ranges) / ATR_PERIOD


def _apply_break(
    active_high: GeometrySwing | None,
    active_low: GeometrySwing | None,
    current: Candle,
    *,
    tf_ms: int,
    atr14: float,
    state: StructureState,
) -> tuple[GeometrySwing | None, GeometrySwing | None, GeometryEvent | None]:
    tolerance = BREAK_TOLERANCE_ATR * atr14
    upward = (
        active_high is not None
        and current.close > active_high.price + tolerance
    )
    downward = (
        active_low is not None
        and current.close < active_low.price - tolerance
    )
    crossed_high, crossed_low = active_high, active_low
    if upward:
        active_high = None
    if downward:
        active_low = None
    if upward == downward:
        return active_high, active_low, None
    if upward and state in ("bullish", "bearish"):
        event_type = "BOS" if state == "bullish" else "CHOCH"
        direction = "bullish"
        broken = crossed_high
    elif downward and state in ("bullish", "bearish"):
        event_type = "CHOCH" if state == "bullish" else "BOS"
        direction = "bearish"
        broken = crossed_low
    else:
        return active_high, active_low, None
    assert broken is not None
    break_at_ms = current.timestamp + tf_ms
    return active_high, active_low, GeometryEvent(
        event_type,
        direction,
        broken,
        break_at_ms,
        break_at_ms,
        current.close,
        atr14,
        tolerance,
        state,
    )


def derive_market_structure(
    candles: Sequence[Candle], *, tf_ms: int
) -> MarketStructureGeometry:
    """Walk one bounded closed-bar prefix and derive neutral causal geometry."""

    rows = tuple(candles[-WINDOW_BARS:])
    error = _series_error(rows, tf_ms)
    if error:
        return MarketStructureGeometry(
            "unavailable", error, None, None, None, (), (), None
        )
    fingerprint = _sha256([_geometry_bar_payload(row) for row in rows])
    if len(rows) < MIN_BARS:
        return MarketStructureGeometry(
            "unavailable",
            "insufficient_closed_bars",
            None,
            None,
            None,
            (),
            (),
            fingerprint,
        )

    highs: list[GeometrySwing] = []
    lows: list[GeometrySwing] = []
    swings: list[GeometrySwing] = []
    events: list[GeometryEvent] = []
    active_high: GeometrySwing | None = None
    active_low: GeometrySwing | None = None
    final_atr: float | None = None

    for current_index, current in enumerate(rows):
        final_atr = _atr14_at(rows, current_index)
        pivot_index = current_index - FRACTAL_K
        if pivot_index >= FRACTAL_K:
            pivot = rows[pivot_index]
            neighbors = (
                rows[pivot_index - 2],
                rows[pivot_index - 1],
                rows[pivot_index + 1],
                rows[pivot_index + 2],
            )
            confirmed_at_ms = current.timestamp + tf_ms
            if all(pivot.high > neighbor.high for neighbor in neighbors):
                swing = GeometrySwing(
                    "high",
                    pivot.high,
                    pivot_index,
                    pivot.timestamp,
                    confirmed_at_ms,
                    confirmed_at_ms,
                )
                highs.append(swing)
                swings.append(swing)
                active_high = swing
            if all(pivot.low < neighbor.low for neighbor in neighbors):
                swing = GeometrySwing(
                    "low",
                    pivot.low,
                    pivot_index,
                    pivot.timestamp,
                    confirmed_at_ms,
                    confirmed_at_ms,
                )
                lows.append(swing)
                swings.append(swing)
                active_low = swing

        state = _structure_state(highs, lows)
        if final_atr is None:
            continue
        active_high, active_low, event = _apply_break(
            active_high,
            active_low,
            current,
            tf_ms=tf_ms,
            atr14=final_atr,
            state=state,
        )
        if event is not None:
            events.append(event)

    if final_atr is None:
        return MarketStructureGeometry(
            "unavailable",
            "atr_unavailable",
            None,
            None,
            None,
            tuple(swings),
            tuple(events),
            fingerprint,
        )
    state = _structure_state(highs, lows)
    return MarketStructureGeometry(
        "available",
        None,
        state,
        final_atr,
        BREAK_TOLERANCE_ATR * final_atr,
        tuple(swings),
        tuple(events),
        fingerprint,
    )


def _swing_id(
    swing: GeometrySwing, instrument: InstrumentId, timeframe: str
) -> str:
    return _sha256({
        "confirmed_at_ms": swing.confirmed_at_ms,
        "contract_version": CONTRACT_VERSION,
        "core_algorithm_version": CORE_ALGORITHM_VERSION,
        "instrument_id": str(instrument),
        "kind": swing.kind,
        "origin_at_ms": swing.origin_at_ms,
        "params_hash": PARAMS_HASH,
        "price": _float_identity(swing.price),
        "timeframe": timeframe,
    })


def _event_id(
    event: GeometryEvent,
    broken_swing_id: str,
    instrument: InstrumentId,
    timeframe: str,
) -> str:
    return _sha256({
        "atr14": _float_identity(event.atr14),
        "break_at_ms": event.break_at_ms,
        "break_tolerance": _float_identity(event.break_tolerance),
        "breaking_close": _float_identity(event.breaking_close),
        "broken_swing_id": broken_swing_id,
        "contract_version": CONTRACT_VERSION,
        "core_algorithm_version": CORE_ALGORITHM_VERSION,
        "direction": event.direction,
        "event_type": event.type,
        "instrument_id": str(instrument),
        "params_hash": PARAMS_HASH,
        "timeframe": timeframe,
    })


def _canonical_facts(
    geometry: MarketStructureGeometry,
    instrument: InstrumentId,
    timeframe: str,
) -> tuple[tuple[SwingPoint, ...], tuple[StructureEvent, ...]]:
    swing_ids = {
        swing: _swing_id(swing, instrument, timeframe)
        for swing in geometry.swings
    }
    swings = tuple(
        SwingPoint(
            swing_ids[swing],
            swing.kind,
            swing.price,
            swing.origin_at_ms,
            swing.confirmed_at_ms,
            swing.known_at_ms,
            CORE_ALGORITHM_VERSION,
            PARAMS_HASH,
        )
        for swing in geometry.swings
    )
    events = tuple(
        StructureEvent(
            _event_id(
                event,
                swing_ids[event.broken_swing],
                instrument,
                timeframe,
            ),
            event.type,
            event.direction,
            swing_ids[event.broken_swing],
            event.broken_swing.price,
            event.broken_swing.origin_at_ms,
            event.broken_swing.confirmed_at_ms,
            event.break_at_ms,
            event.known_at_ms,
            event.breaking_close,
            event.atr14,
            event.break_tolerance,
            event.pre_break_structure_state,
            CORE_ALGORITHM_VERSION,
            PARAMS_HASH,
        )
        for event in geometry.events
    )
    return swings, events


def _source_identity(candles: Sequence[Candle], field: str) -> str | None:
    values = {
        str(getattr(candle, field) or "")
        for candle in candles
        if str(getattr(candle, field) or "")
    }
    return next(iter(values)) if len(values) == 1 else None


class MarketStructureProvider:
    """One bounded read wrapper over a caller-owned market-data provider."""

    def __init__(self, market_data: MarketDataProvider):
        self._market_data = market_data

    async def get_snapshot(
        self,
        instrument: InstrumentId,
        timeframe: str,
        *,
        as_of_ms: int | None = None,
        enrichments: frozenset[str] = frozenset(),
    ) -> MarketStructureSnapshot:
        if not isinstance(instrument, InstrumentId):
            raise ValueError("market_structure_instrument_required")
        tf_ms = timeframe_ms(timeframe)
        if not tf_ms:
            raise ValueError(f"market_structure_timeframe_unsupported:{timeframe}")
        if as_of_ms is not None and (
            isinstance(as_of_ms, bool)
            or not isinstance(as_of_ms, int)
            or as_of_ms < 0
        ):
            raise ValueError("market_structure_as_of_invalid")
        unsupported = sorted(set(enrichments) - {"fvg"})
        if unsupported:
            raise ValueError(
                f"market_structure_enrichment_unsupported:{unsupported[0]}"
            )

        since = (
            None
            if as_of_ms is None
            else max(0, as_of_ms - FETCH_LIMIT * tf_ms)
        )
        try:
            fetched = await self._market_data.get_ohlcv(
                instrument,
                timeframe=timeframe,
                limit=FETCH_LIMIT,
                since=since,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            computed_at_ms = time.time_ns() // 1_000_000
            return self._unavailable(
                instrument,
                timeframe,
                as_of_ms,
                tf_ms,
                computed_at_ms,
                "provider_unavailable",
                fvg_requested="fvg" in enrichments,
                provider_failure_reason=type(exc).__name__,
            )

        computed_at_ms = time.time_ns() // 1_000_000
        provider_outcome = str(getattr(fetched, "outcome", "")) or None
        try:
            source_rows = tuple(fetched)
        except Exception:
            return self._unavailable(
                instrument,
                timeframe,
                as_of_ms,
                tf_ms,
                computed_at_ms,
                "provider_unavailable",
                fvg_requested="fvg" in enrichments,
                provider_outcome=provider_outcome,
                provider_failure_reason="invalid_provider_result",
            )
        if any(not isinstance(candle, Candle) for candle in source_rows):
            return self._unavailable(
                instrument,
                timeframe,
                as_of_ms,
                tf_ms,
                computed_at_ms,
                "invalid_candle",
                fvg_requested="fvg" in enrichments,
                fetched_bar_count=len(source_rows),
                provider_outcome=provider_outcome,
            )
        if any(not str(candle.exchange or "") for candle in source_rows):
            return self._unavailable(
                instrument,
                timeframe,
                as_of_ms,
                tf_ms,
                computed_at_ms,
                "source_venue_unproven",
                fvg_requested="fvg" in enrichments,
                fetched_bar_count=len(source_rows),
                provider_outcome=provider_outcome,
            )
        if any(
            str(candle.exchange).lower() != instrument.exchange.lower()
            for candle in source_rows
        ):
            return self._unavailable(
                instrument,
                timeframe,
                as_of_ms,
                tf_ms,
                computed_at_ms,
                "source_venue_mismatch",
                fvg_requested="fvg" in enrichments,
                fetched_bar_count=len(source_rows),
                provider_outcome=provider_outcome,
            )

        if as_of_ms is None:
            closed = tuple(
                candle
                for candle in source_rows
                if candle.is_closed
                and candle.timestamp + tf_ms <= computed_at_ms
            )
            error = _series_error(closed, tf_ms)
            admitted = closed[-WINDOW_BARS:]
        else:
            closed = tuple(candle for candle in source_rows if candle.is_closed)
            admitted = tuple(
                candle
                for candle in closed
                if candle.timestamp + tf_ms <= as_of_ms
            )[-WINDOW_BARS:]
            error = _series_error(admitted, tf_ms)
        if not admitted:
            reason = (
                "empty_ohlcv"
                if as_of_ms is None
                else "historical_window_unavailable"
            )
            return self._unavailable(
                instrument,
                timeframe,
                as_of_ms,
                tf_ms,
                computed_at_ms,
                reason,
                fvg_requested="fvg" in enrichments,
                fetched_bar_count=len(source_rows),
                provider_outcome=provider_outcome,
            )
        if error:
            return self._unavailable(
                instrument,
                timeframe,
                as_of_ms,
                tf_ms,
                computed_at_ms,
                error,
                bars=admitted,
                fvg_requested="fvg" in enrichments,
                fetched_bar_count=len(source_rows),
                provider_outcome=provider_outcome,
            )
        effective_as_of_ms = admitted[-1].timestamp + tf_ms
        if (
            as_of_ms is not None
            and as_of_ms - effective_as_of_ms >= tf_ms
        ):
            return self._unavailable(
                instrument,
                timeframe,
                as_of_ms,
                tf_ms,
                computed_at_ms,
                "historical_tail_gap",
                bars=admitted,
                fvg_requested="fvg" in enrichments,
                fetched_bar_count=len(source_rows),
                provider_outcome=provider_outcome,
            )

        geometry = derive_market_structure(admitted, tf_ms=tf_ms)
        source_fingerprint = _sha256([
            _source_bar_payload(row) for row in admitted
        ])
        if geometry.availability == "unavailable":
            return self._unavailable(
                instrument,
                timeframe,
                as_of_ms,
                tf_ms,
                computed_at_ms,
                geometry.unavailable_reason or "provider_unavailable",
                bars=admitted,
                fvg_requested="fvg" in enrichments,
                fetched_bar_count=len(source_rows),
                provider_outcome=provider_outcome,
                geometry_fingerprint=geometry.geometry_fingerprint,
                source_candle_fingerprint=source_fingerprint,
            )

        swings, events = _canonical_facts(geometry, instrument, timeframe)
        fvg_requested = "fvg" in enrichments
        fvg_availability: FVGAvailability = (
            "available" if fvg_requested else "not_requested"
        )
        fvg_reason: str | None = None
        zones: tuple[StructureZone, ...] = ()
        if fvg_requested:
            try:
                from aletheia.data.smc_enrichment import derive_fvg_zones

                zones = derive_fvg_zones(
                    admitted,
                    instrument=instrument,
                    timeframe=timeframe,
                    tf_ms=tf_ms,
                )
            except Exception as exc:
                fvg_availability = "unavailable"
                fvg_reason = f"fvg_calculation_failed:{type(exc).__name__}"

        history_quality: HistoryQuality = (
            "complete" if len(admitted) == WINDOW_BARS else "partial"
        )
        provenance = self._provenance(
            instrument,
            timeframe,
            as_of_ms,
            tf_ms,
            computed_at_ms,
            admitted,
            history_quality,
            len(source_rows),
            provider_outcome,
            None,
            geometry.geometry_fingerprint,
            source_fingerprint,
            fvg_requested,
        )
        return MarketStructureSnapshot(
            CONTRACT_VERSION,
            instrument,
            timeframe,
            as_of_ms,
            effective_as_of_ms,
            "available",
            None,
            geometry.structure_state,
            geometry.atr14,
            geometry.break_tolerance,
            swings,
            events,
            events[-1] if events else None,
            fvg_availability,
            fvg_reason,
            zones,
            provenance,
        )

    @staticmethod
    def _provenance(
        instrument: InstrumentId,
        timeframe: str,
        requested_as_of_ms: int | None,
        tf_ms: int,
        computed_at_ms: int,
        bars: Sequence[Candle],
        history_quality: HistoryQuality,
        fetched_bar_count: int,
        provider_outcome: str | None,
        provider_failure_reason: str | None,
        geometry_fingerprint: str | None,
        source_candle_fingerprint: str | None,
        fvg_requested: bool,
    ) -> StructureProvenance:
        first = bars[0] if bars else None
        last = bars[-1] if bars else None
        return StructureProvenance(
            instrument,
            timeframe,
            requested_as_of_ms,
            last.timestamp + tf_ms if last else None,
            first.timestamp if first else None,
            last.timestamp if last else None,
            first.timestamp + tf_ms if first else None,
            last.timestamp + tf_ms if last else None,
            len(bars),
            FETCH_LIMIT,
            fetched_bar_count,
            provider_outcome,
            provider_failure_reason,
            _source_identity(bars, "provider"),
            _source_identity(bars, "exchange"),
            history_quality,
            geometry_fingerprint,
            source_candle_fingerprint,
            CORE_ALGORITHM_VERSION,
            PARAMS_HASH,
            FVG_ALGORITHM_VERSION if fvg_requested else None,
            computed_at_ms,
        )

    @classmethod
    def _unavailable(
        cls,
        instrument: InstrumentId,
        timeframe: str,
        requested_as_of_ms: int | None,
        tf_ms: int,
        computed_at_ms: int,
        reason: str,
        *,
        bars: Sequence[Candle] = (),
        fvg_requested: bool,
        fetched_bar_count: int = 0,
        provider_outcome: str | None = None,
        provider_failure_reason: str | None = None,
        geometry_fingerprint: str | None = None,
        source_candle_fingerprint: str | None = None,
    ) -> MarketStructureSnapshot:
        provenance = cls._provenance(
            instrument,
            timeframe,
            requested_as_of_ms,
            tf_ms,
            computed_at_ms,
            bars,
            "unavailable",
            fetched_bar_count,
            provider_outcome,
            provider_failure_reason or reason,
            geometry_fingerprint,
            source_candle_fingerprint,
            fvg_requested,
        )
        fvg_reason = f"core_unavailable:{reason}" if fvg_requested else None
        return MarketStructureSnapshot(
            CONTRACT_VERSION,
            instrument,
            timeframe,
            requested_as_of_ms,
            provenance.as_of_ms,
            "unavailable",
            reason,
            None,
            None,
            None,
            (),
            (),
            None,
            "unavailable" if fvg_requested else "not_requested",
            fvg_reason,
            (),
            provenance,
        )
