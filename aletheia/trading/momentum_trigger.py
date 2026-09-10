"""Shadow-only Momentum TriggerSpec state machine.

Consumes one exact instrument's public fast-stream facts and returns one
deterministic TriggerReady bundle. It has no repository, provider, LLM, Risk,
or Gateway access; the watcher decides whether the bundle is only observed or
may reach the existing execution path.
"""

from collections import deque
from datetime import datetime, timezone
import math
from typing import Literal, cast

from aletheia.data.symbols import InstrumentId
from aletheia.universe.fast_stream import FastMarketEvent, events_share_generation
from aletheia.universe.fast_trigger import TriggerPolicy, TriggerReady

TRIGGER_SPEC_ID = "momentum-retrace-reclaim-geometry"
GEOMETRY_VERSION = "momentum-entry-geometry/v2"
_LEGACY_GEOMETRY_VERSION = "anchor-retrace-reclaim-geometry/v1"
State = Literal["armed", "pullback", "sweep", "reclaim", "triggered", "invalidated"]


def _positive(value: float | int | str, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"momentum_trigger_{field}_invalid") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"momentum_trigger_{field}_invalid")
    return number


def _zone(value: tuple[float, float], field: str) -> tuple[float, float]:
    try:
        low, high = (_positive(value[0], f"{field}_low"), _positive(value[1], f"{field}_high"))
    except (IndexError, TypeError) as exc:
        raise ValueError(f"momentum_trigger_{field}_invalid") from exc
    if low > high:
        raise ValueError(f"momentum_trigger_{field}_invalid")
    return low, high


def health_reason_requires_resync(reason: str | None) -> bool:
    """Is this engine-integrity fault one only a fresh engine can clear?

    An ordering, routing or stream-generation fault leaves the rolling
    quote/trade/depth windows holding facts whose order is no longer proven, so
    the engine correctly refuses every later event -- and nothing inside it ever
    clears that refusal.  The watcher owns the lifecycle, so it must discard the
    instance and re-arm from empty rather than keep a permanently silent engine.

    Excluded on purpose: ``momentum_trigger_structural_invalidation`` (the Plan
    is genuinely invalidated and the watcher terminalizes it) and
    ``subscription_unconfirmed`` (``confirm_subscription`` already owns it).
    """

    if reason is None:
        return False
    return (
        reason.startswith("sequence_anomaly:")
        or reason.endswith("instrument_mismatch")
        or reason == "market_evidence_generation_mismatch"
    )


class MomentumTriggerEngine:
    """Deterministic ARMED→PULLBACK→SWEEP→RECLAIM→TRIGGERED engine."""

    def __init__(
        self,
        *,
        instrument: InstrumentId,
        direction: Literal["long", "short"],
        breakout_level: float,
        pullback_band: tuple[float, float],
        sweep_band: tuple[float, float],
        entry_zone: tuple[float, float],
        invalidation_price: float,
        atr_abs: float,
        geometry_version: str,
        geometry_fingerprint: str,
        policy: TriggerPolicy,
        trigger_spec_id: str = TRIGGER_SPEC_ID,
    ) -> None:
        if direction not in {"long", "short"}:
            raise ValueError("momentum_trigger_direction_invalid")
        self.instrument = instrument
        self.direction = direction
        self.breakout_level = _positive(breakout_level, "breakout_level")
        self.pullback_band = _zone(pullback_band, "pullback_band")
        self.sweep_band = _zone(sweep_band, "sweep_band")
        self.entry_zone = _zone(entry_zone, "entry_zone")
        self.invalidation_price = _positive(invalidation_price, "invalidation_price")
        self.atr_abs = _positive(atr_abs, "atr_abs")
        if geometry_version not in {GEOMETRY_VERSION, _LEGACY_GEOMETRY_VERSION}:
            raise ValueError("momentum_trigger_geometry_version_invalid")
        if (
            not isinstance(geometry_fingerprint, str)
            or len(geometry_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in geometry_fingerprint)
        ):
            raise ValueError("momentum_trigger_geometry_fingerprint_invalid")
        self.geometry_version = geometry_version
        self.geometry_fingerprint = geometry_fingerprint
        if trigger_spec_id not in {TRIGGER_SPEC_ID, "anchor-retrace-reclaim"}:
            raise ValueError("momentum_trigger_spec_identity_invalid")
        self.trigger_spec_id = trigger_spec_id
        self.policy = policy
        self.state: State = "armed"
        self.state_path: list[State] = ["armed"]
        self._activation_events: dict[str, FastMarketEvent] = {}
        self.subscription_confirmed = False
        self.health_reason: str | None = None
        self.final_requirement_reason: str | None = None
        self._sequences: dict[str, int] = {}
        self._quote: FastMarketEvent | None = None
        self._depth: FastMarketEvent | None = None
        self._trades: deque[FastMarketEvent] = deque()
        self._pullback_seen_ms: int | None = None
        self._sweep_seen_ms: int | None = None
        self._reclaim_seen_ms: int | None = None
        self._first_reclaim_quote_ms: int | None = None
        self._first_reclaim_quote_sequence: int | None = None
        self._closed_candles_1m: deque[dict] = deque(maxlen=120)
        self._current_candle_1m: dict | None = None

    def preload_closed_candles(self, candles: list[dict]) -> None:
        """Warm bounded local structure history without changing trigger state."""

        self._closed_candles_1m.clear()
        for candle in candles[-120:]:
            self._closed_candles_1m.append(dict(candle))

    def confirm_subscription(self) -> None:
        self.subscription_confirmed = True
        if self.health_reason == "subscription_unconfirmed":
            self.health_reason = None

    def _advance(self, state: State, observed_at_ms: int) -> None:
        if self.state == state:
            return
        self.state = state
        self.state_path.append(state)
        if state in {"pullback", "sweep", "reclaim", "triggered"} and self._quote is not None:
            self._activation_events[state] = self._quote
        if state == "pullback":
            self._pullback_seen_ms = observed_at_ms
        elif state == "sweep":
            self._sweep_seen_ms = observed_at_ms
        elif state == "reclaim":
            self._reclaim_seen_ms = observed_at_ms

    def _invalidate_if_breached(self, event: FastMarketEvent) -> bool:
        if event.event_type != "quote" or event.bid is None or event.ask is None:
            return False
        breached = (
            float(event.bid) <= self.invalidation_price
            if self.direction == "long"
            else float(event.ask) >= self.invalidation_price
        )
        if breached and self.state not in {"triggered", "invalidated"}:
            self._advance("invalidated", event.received_timestamp_ms)
            self.health_reason = "momentum_trigger_structural_invalidation"
            return True
        return False

    def _sequence_is_valid(self, event: FastMarketEvent) -> bool:
        if event.instrument != self.instrument:
            self.health_reason = "momentum_trigger_instrument_mismatch"
            return False
        previous = self._sequences.get(event.event_type)
        if previous is not None and event.sequence <= previous:
            self.health_reason = f"sequence_anomaly:{event.event_type}"
            return False
        self._sequences[event.event_type] = event.sequence
        return self.health_reason is None

    def _pullback_mid(self, mid: float) -> bool:
        return self.pullback_band[0] <= mid <= self.pullback_band[1] and (
            mid <= self.breakout_level if self.direction == "long" else mid >= self.breakout_level
        )

    def _sweep_mid(self, mid: float) -> bool:
        return (
            self.sweep_band[0] <= mid < self.breakout_level
            if self.direction == "long"
            else self.breakout_level < mid <= self.sweep_band[1]
        )

    def _reclaimed_mid(self, mid: float) -> bool:
        return mid > self.breakout_level if self.direction == "long" else mid < self.breakout_level

    def observe(self, event: FastMarketEvent) -> TriggerReady | None:
        """Consume one ordered public event; return only on v2 trigger readiness."""

        if not self._sequence_is_valid(event):
            return None
        if self._invalidate_if_breached(event):
            return None
        if self.state in {"triggered", "invalidated"}:
            return None

        if event.event_type == "trade":
            self._trades.append(event)
            self._prune_trades(event.received_timestamp_ms)
            return None
        if event.event_type == "depth":
            self._depth = event
            return None
        if event.event_type == "candle" and event.candle is not None:
            open_, high, low, close, volume = event.candle
            candle = {
                "ts": event.exchange_timestamp_ms,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
            if self._current_candle_1m is not None and candle["ts"] > self._current_candle_1m["ts"]:
                self._closed_candles_1m.append(self._current_candle_1m)
            if self._current_candle_1m is None or candle["ts"] >= self._current_candle_1m["ts"]:
                self._current_candle_1m = candle
            return None
        if event.event_type != "quote" or event.bid is None or event.ask is None:
            return None

        self._quote = event
        mid = (float(event.bid) + float(event.ask)) / 2.0
        if self.state == "armed" and self._pullback_mid(mid):
            self._advance("pullback", event.received_timestamp_ms)
        elif self.state == "pullback" and self._sweep_mid(mid):
            self._advance("sweep", event.received_timestamp_ms)
        elif self.state == "sweep" and self._reclaimed_mid(mid):
            self._advance("reclaim", event.received_timestamp_ms)
            self._first_reclaim_quote_ms = event.received_timestamp_ms
            self._first_reclaim_quote_sequence = event.sequence
        elif self.state == "reclaim":
            if not self._reclaimed_mid(mid):
                if self._sweep_mid(mid):
                    self._advance("sweep", event.received_timestamp_ms)
                else:
                    self._advance("pullback", event.received_timestamp_ms)
                self._first_reclaim_quote_ms = None
                self._first_reclaim_quote_sequence = None
            elif self._first_reclaim_quote_ms is None:
                self._first_reclaim_quote_ms = event.received_timestamp_ms
                self._first_reclaim_quote_sequence = event.sequence
            elif event.received_timestamp_ms - self._first_reclaim_quote_ms >= 300:
                ready = self.evaluate(event.received_timestamp_ms)
                if ready is not None:
                    return ready
        return None

    def _prune_trades(self, now_ms: int) -> None:
        cutoff = now_ms - 10_000
        while self._trades and self._trades[0].received_timestamp_ms < cutoff:
            self._trades.popleft()

    def evaluate(self, now_ms: int) -> TriggerReady | None:
        if (
            self.health_reason
            or not self.subscription_confirmed
            or self.state != "reclaim"
            or self._quote is None
            or self._depth is None
            or not self._trades
        ):
            return None
        self._prune_trades(now_ms)
        if not self._trades:
            self.final_requirement_reason = "trade_window_empty"
            return None
        quote, depth = self._quote, self._depth
        if not events_share_generation(quote, depth, *self._trades):
            self.health_reason = "market_evidence_generation_mismatch"
            return None
        if (
            now_ms - quote.received_timestamp_ms > 1_000
            or now_ms - depth.received_timestamp_ms > 1_000
            or now_ms - self._trades[-1].received_timestamp_ms > 2_000
        ):
            self.final_requirement_reason = "market_fact_stale"
            return None
        bid = float(quote.bid) if quote.bid is not None else 0.0
        ask = float(quote.ask) if quote.ask is not None else 0.0
        mid = (bid + ask) / 2.0
        spread = (ask - bid) / mid * 10_000
        if spread > self.policy.max_spread_bps:
            self.final_requirement_reason = "spread"
            return None
        depth_value = sum(
            price * size
            for price, size in depth.bids + depth.asks
            if abs(price / mid - 1.0) <= 0.001
        )
        if depth_value < self.policy.min_depth_10bps_usd:
            self.final_requirement_reason = "depth"
            return None
        buy = sum(float(item.price or 0.0) * float(item.size or 0.0) for item in self._trades if item.side == "buy")
        total = sum(float(item.price or 0.0) * float(item.size or 0.0) for item in self._trades)
        if total <= 0:
            self.final_requirement_reason = "order_flow"
            return None
        ratio = buy / total
        if (self.direction == "long" and ratio < 0.60) or (
            self.direction == "short" and ratio > 0.40
        ):
            self.final_requirement_reason = "order_flow"
            return None
        entry = ask if self.direction == "long" else bid
        if not self.entry_zone[0] <= entry <= self.entry_zone[1]:
            self.final_requirement_reason = "entry_zone"
            return None

        self.final_requirement_reason = None

        self._advance("triggered", now_ms)
        from aletheia.universe.market_structure import evaluate_market_structure

        structure = evaluate_market_structure(
            list(self._closed_candles_1m), direction=cast(Literal["long", "short"], self.direction)
        )
        observed = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()
        return TriggerReady(
            observed_at=observed,
            bid=bid,
            ask=ask,
            spread_bps=spread,
            depth_10bps_usd=depth_value,
            taker_buy_ratio=ratio,
            quote_exchange_timestamp_ms=quote.exchange_timestamp_ms,
            quote_received_timestamp_ms=quote.received_timestamp_ms,
            trade_exchange_timestamp_ms=self._trades[-1].exchange_timestamp_ms,
            trade_received_timestamp_ms=self._trades[-1].received_timestamp_ms,
            depth_exchange_timestamp_ms=depth.exchange_timestamp_ms,
            depth_received_timestamp_ms=depth.received_timestamp_ms,
            quote_sequence=quote.sequence,
            trade_sequence=self._trades[-1].sequence,
            depth_sequence=depth.sequence,
            stream_generation_id=quote.stream_generation_id,
            subscription_generation_id=quote.subscription_generation_id,
            depth_bids=depth.bids,
            depth_asks=depth.asks,
            entry_price=entry,
            activation_events=tuple(self._activation_events[state] for state in ("pullback", "sweep", "reclaim", "triggered")),
            entry_trigger={
                "trigger_spec_id": self.trigger_spec_id,
                "geometry_version": self.geometry_version,
                "geometry_fingerprint": self.geometry_fingerprint,
                "breakout_level": self.breakout_level,
                "atr_abs": self.atr_abs,
                "pullback_seen_at_ms": self._pullback_seen_ms,
                "sweep_seen_at_ms": self._sweep_seen_ms,
                "reclaim_seen_at_ms": self._reclaim_seen_ms,
                "first_reclaim_quote_at_ms": self._first_reclaim_quote_ms,
                "first_reclaim_quote_sequence": self._first_reclaim_quote_sequence,
                "confirming_reclaim_quote_at_ms": quote.received_timestamp_ms,
                "confirming_reclaim_quote_sequence": quote.sequence,
                "minimum_confirmation_spacing_ms": 300,
                "state_path": list(self.state_path),
            },
            market_structure=structure,
        )
