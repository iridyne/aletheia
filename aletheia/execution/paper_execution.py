"""Bounded public-market evidence and deterministic local Paper execution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
import math
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia.data.symbols import InstrumentId


PAPER_FILL_MODEL_VERSION = "paper-order-fill/v1"
PAPER_PROTECTIVE_FALLBACK_VERSION = "paper-protective-fallback/v1"
REQUIRED_CHANNELS = ("quote", "trade", "depth", "candle")
_FRESHNESS_MS = {"quote": 5_000, "trade": 5_000, "depth": 5_000, "candle": 90_000}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _validate_evidence_time(
    *,
    captured_at: str,
    expires_at: str,
    channel_evidence: dict[str, dict[str, Any]],
    now: datetime | None,
    prefix: str,
) -> None:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00")).astimezone(timezone.utc)
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{prefix}_time_invalid") from exc
    if (
        captured > current + timedelta(seconds=1)
        or expires <= current
        or expires <= captured
        or expires - captured > timedelta(seconds=5)
    ):
        raise ValueError(f"{prefix}_stale")
    current_ms = int(current.timestamp() * 1000)
    for channel in REQUIRED_CHANNELS:
        received = int(channel_evidence[channel]["last_received_at"])
        if received > current_ms + 1_000 or current_ms - received > _FRESHNESS_MS[channel]:
            raise ValueError(f"{prefix}_channel_stale:{channel}")


class PaperInstrument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    venue: str
    symbol: str
    market_type: Literal["spot", "perp"]


class ReceiptQuote(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    exchange_timestamp_ms: int = Field(ge=0)
    received_timestamp_ms: int = Field(ge=0)
    sequence: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self):
        if self.ask < self.bid:
            raise ValueError("entry_execution_receipt_crossed_quote")
        return self


class ReceiptDepth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    exchange_timestamp_ms: int = Field(ge=0)
    received_timestamp_ms: int = Field(ge=0)
    sequence: int = Field(ge=0)

    @model_validator(mode="after")
    def _valid_levels(self):
        levels = self.bids + self.asks
        if not self.bids or not self.asks or any(price <= 0 or size < 0 for price, size in levels):
            raise ValueError("entry_execution_receipt_depth_invalid")
        return self


class EntryExecutionMarketSnapshotV1(BaseModel):
    """Immutable in-process snapshot used to create one durable attempt receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["entry-execution-market-snapshot/v1"] = "entry-execution-market-snapshot/v1"
    instrument: PaperInstrument
    stream_generation_id: str = Field(min_length=1)
    subscription_generation_id: str = Field(min_length=1)
    required_channels: tuple[Literal["quote", "trade", "depth", "candle"], ...] = REQUIRED_CHANNELS
    quote: ReceiptQuote
    depth: ReceiptDepth
    channel_evidence: dict[str, dict[str, Any]]
    captured_at: str
    expires_at: str
    trigger_evidence_hash: str | None = None

    @model_validator(mode="after")
    def _complete_ready_evidence(self):
        if tuple(self.required_channels) != REQUIRED_CHANNELS or set(self.channel_evidence) != set(REQUIRED_CHANNELS):
            raise ValueError("entry_execution_snapshot_channels_invalid")
        for channel in REQUIRED_CHANNELS:
            evidence = self.channel_evidence[channel]
            if (
                not isinstance(evidence, dict)
                or evidence.get("state") != "ready"
                or evidence.get("reason") is not None
                or not all(
                    isinstance(evidence.get(key), int) and evidence[key] >= 0
                    for key in ("last_exchange_at", "last_received_at", "last_sequence")
                )
            ):
                raise ValueError(f"entry_execution_snapshot_channel_unavailable:{channel}")
        for channel, fact in (("quote", self.quote), ("depth", self.depth)):
            evidence = self.channel_evidence[channel]
            if (
                evidence["last_exchange_at"] != fact.exchange_timestamp_ms
                or evidence["last_received_at"] != fact.received_timestamp_ms
                or evidence["last_sequence"] != fact.sequence
            ):
                raise ValueError(f"entry_execution_snapshot_channel_mismatch:{channel}")
        return self

    def validate_now(self, now: datetime | None = None) -> None:
        _validate_evidence_time(
            captured_at=self.captured_at,
            expires_at=self.expires_at,
            channel_evidence=self.channel_evidence,
            now=now,
            prefix="entry_execution_snapshot",
        )


class EntryExecutionReceiptV1(BaseModel):
    """Short-lived exact market authority for one Proposal execution attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["entry-execution-receipt/v1"] = "entry-execution-receipt/v1"
    attempt_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    entry_plan_id: str = Field(min_length=1)
    instrument: PaperInstrument
    stream_generation_id: str = Field(min_length=1)
    subscription_generation_id: str = Field(min_length=1)
    required_channels: tuple[Literal["quote", "trade", "depth", "candle"], ...]
    quote: ReceiptQuote
    depth: ReceiptDepth
    channel_evidence: dict[str, dict[str, Any]]
    trigger_evidence_hash: str | None = None
    captured_at: str
    expires_at: str
    receipt_hash: str

    @classmethod
    def create(
        cls,
        snapshot: EntryExecutionMarketSnapshotV1,
        *,
        attempt_id: str,
        proposal_id: str,
        entry_plan_id: str,
    ) -> "EntryExecutionReceiptV1":
        payload = {
            "version": "entry-execution-receipt/v1",
            "attempt_id": attempt_id,
            "proposal_id": proposal_id,
            "entry_plan_id": entry_plan_id,
            **snapshot.model_dump(mode="json", exclude={"version"}),
        }
        return cls(**payload, receipt_hash=_digest(payload))

    def validate_now(self, now: datetime | None = None) -> None:
        payload = self.model_dump(mode="json", exclude={"receipt_hash"})
        if _digest(payload) != self.receipt_hash:
            raise ValueError("entry_execution_receipt_hash_mismatch")
        _validate_evidence_time(
            captured_at=self.captured_at,
            expires_at=self.expires_at,
            channel_evidence=self.channel_evidence,
            now=now,
            prefix="entry_execution_receipt",
        )


class DepthFill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    quantity: float = Field(ge=0)
    vwap: float | None = Field(default=None, gt=0)
    levels: tuple[tuple[float, float], ...] = ()


def consume_known_depth(
    *,
    direction: str,
    quantity: float,
    limit_price: float,
    depth: ReceiptDepth,
) -> DepthFill:
    """Walk only visible opposing depth inside the immutable IOC price bound."""

    requested = float(quantity)
    limit = float(limit_price)
    if not math.isfinite(requested) or requested <= 0 or not math.isfinite(limit) or limit <= 0:
        raise ValueError("paper_ioc_bounds_invalid")
    if direction in {"long", "buy"}:
        levels = sorted(depth.asks)
        eligible = lambda price: price <= limit
    elif direction in {"short", "sell"}:
        levels = sorted(depth.bids, reverse=True)
        eligible = lambda price: price >= limit
    else:
        raise ValueError("paper_ioc_direction_invalid")
    remaining = requested
    consumed: list[tuple[float, float]] = []
    for price, available in levels:
        if not eligible(price) or remaining <= 0:
            break
        take = min(remaining, float(available))
        if take > 0:
            consumed.append((float(price), take))
            remaining -= take
    filled = sum(size for _, size in consumed)
    vwap = sum(price * size for price, size in consumed) / filled if filled > 0 else None
    return DepthFill(quantity=filled, vwap=vwap, levels=tuple(consumed))


def exact_public_event_id(event: Any) -> str:
    instrument = event.instrument
    return _digest({
        "venue": instrument.exchange,
        "symbol": instrument.symbol,
        "market_type": instrument.market_type,
        "event_type": event.event_type,
        "venue_event_id": getattr(event, "event_id", None),
        "exchange_timestamp_ms": event.exchange_timestamp_ms,
        "sequence": event.sequence,
        "price": event.price,
        "size": event.size,
        "side": event.side,
    })


def normalize_paper_order(
    *, venue: str, symbol: str, market_type: str, price: float, quantity: float
) -> tuple[float, float]:
    """Validate exact public venue rules and floor only quantity to known precision."""

    from aletheia.storage.sqlite import get_db

    row = get_db().execute(
        "SELECT * FROM universe_registry WHERE venue=? AND symbol=? AND market_type=? "
        "AND status='active' AND COALESCE(is_blacklisted, 0)=0 LIMIT 1",
        (venue, symbol, market_type),
    ).fetchone()
    if row is None:
        raise ValueError("paper_instrument_rules_unavailable")
    rule = dict(row)
    if rule.get("price_precision") is None or rule.get("amount_precision") is None or rule.get("min_notional") is None:
        raise ValueError("paper_instrument_rules_unavailable")
    price_precision = int(rule["price_precision"])
    amount_precision = int(rule["amount_precision"])
    if price_precision < 0 or amount_precision < 0:
        raise ValueError("paper_instrument_rules_invalid")
    price_value = Decimal(str(price))
    price_quantum = Decimal(1).scaleb(-price_precision)
    if price_value != price_value.quantize(price_quantum, rounding=ROUND_DOWN):
        raise ValueError("paper_price_precision_invalid")
    amount_quantum = Decimal(1).scaleb(-amount_precision)
    normalized_quantity = Decimal(str(quantity)).quantize(amount_quantum, rounding=ROUND_DOWN)
    if normalized_quantity <= 0 or price_value * normalized_quantity < Decimal(str(rule["min_notional"])):
        raise ValueError("paper_min_notional_unavailable")
    return float(price_value), float(normalized_quantity)


class PaperMarketState:
    """One bounded state per exact instrument; no tick persistence or book rebuild."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._stream_generation_id = "unavailable"
        self._subscriptions: dict[InstrumentId, str] = {}
        self._events: dict[InstrumentId, dict[str, Any]] = {}
        self._reasons: dict[tuple[InstrumentId, str], str] = {}

    def begin_stream(self, instruments: set[InstrumentId]) -> None:
        with self._lock:
            self._stream_generation_id = uuid4().hex
            self._subscriptions = {item: uuid4().hex for item in instruments}
            self._events = {item: {} for item in instruments}
            self._reasons.clear()

    def set_instruments(self, instruments: set[InstrumentId]) -> None:
        with self._lock:
            removed = set(self._subscriptions) - instruments
            for item in removed:
                self._subscriptions.pop(item, None)
                self._events.pop(item, None)
                for channel in REQUIRED_CHANNELS:
                    self._reasons.pop((item, channel), None)
            for item in instruments - set(self._subscriptions):
                self._subscriptions[item] = uuid4().hex
                self._events[item] = {}

    def observe(self, event: Any) -> None:
        with self._lock:
            if event.instrument not in self._subscriptions or event.event_type not in REQUIRED_CHANNELS:
                return
            event_generation = getattr(event, "subscription_generation_id", None)
            if event_generation and event_generation != self._subscriptions[event.instrument]:
                self._subscriptions[event.instrument] = event_generation
                self._events[event.instrument] = {}
                for channel in REQUIRED_CHANNELS:
                    self._reasons.pop((event.instrument, channel), None)
            prior = self._events[event.instrument].get(event.event_type)
            if prior is not None and int(event.sequence) <= int(prior.sequence):
                self._reasons[(event.instrument, event.event_type)] = "sequence_discontinuous"
                return
            self._events[event.instrument][event.event_type] = event
            self._reasons.pop((event.instrument, event.event_type), None)

    def readiness(self, instrument: InstrumentId, *, now_ms: int | None = None) -> dict[str, Any]:
        current_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock:
            generation = self._subscriptions.get(instrument)
            events = dict(self._events.get(instrument, {}))
            channels: dict[str, dict[str, Any]] = {}
            for channel in REQUIRED_CHANNELS:
                event = events.get(channel)
                reason = self._reasons.get((instrument, channel))
                if event is None:
                    state, reason = "warming", reason or "event_missing"
                elif current_ms < int(event.received_timestamp_ms):
                    state, reason = "degraded", "event_from_future"
                elif current_ms - int(event.received_timestamp_ms) > _FRESHNESS_MS[channel]:
                    state, reason = "degraded", "event_stale"
                elif reason:
                    state = "degraded"
                else:
                    state = "ready"
                channels[channel] = {
                    "state": state,
                    "last_exchange_at": getattr(event, "exchange_timestamp_ms", None),
                    "last_received_at": getattr(event, "received_timestamp_ms", None),
                    "last_sequence": getattr(event, "sequence", None),
                    "reason": reason,
                }
            if generation is None:
                state = "queued"
            elif all(item["state"] == "ready" for item in channels.values()):
                state = "ready"
            elif any(item["state"] == "degraded" for item in channels.values()):
                state = "degraded"
            else:
                state = "warming"
            return {
                "state": state,
                "instrument": {
                    "venue": instrument.exchange,
                    "symbol": instrument.symbol,
                    "market_type": instrument.market_type,
                },
                "stream_generation_id": self._stream_generation_id,
                "subscription_generation_id": generation,
                "required_channels": list(REQUIRED_CHANNELS),
                "channels": channels,
                "updated_at": datetime.fromtimestamp(current_ms / 1000, tz=timezone.utc).isoformat(),
            }

    def snapshot(
        self,
        instrument: InstrumentId,
        *,
        ttl_ms: int = 2_000,
        trigger_evidence: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> EntryExecutionMarketSnapshotV1:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        ready = self.readiness(instrument, now_ms=int(current.timestamp() * 1000))
        if ready["state"] != "ready":
            failed = next(
                (name for name, item in ready["channels"].items() if item["state"] != "ready"),
                "stream",
            )
            raise ValueError(f"paper_monitoring_channel_unavailable:{failed}")
        with self._lock:
            events = dict(self._events[instrument])
            quote = events["quote"]
            depth = events["depth"]
            return EntryExecutionMarketSnapshotV1(
                instrument=PaperInstrument(
                    venue=instrument.exchange,
                    symbol=instrument.symbol,
                    market_type=instrument.market_type,
                ),
                stream_generation_id=self._stream_generation_id,
                subscription_generation_id=self._subscriptions[instrument],
                quote=ReceiptQuote(
                    bid=quote.bid,
                    ask=quote.ask,
                    exchange_timestamp_ms=quote.exchange_timestamp_ms,
                    received_timestamp_ms=quote.received_timestamp_ms,
                    sequence=quote.sequence,
                ),
                depth=ReceiptDepth(
                    bids=depth.bids,
                    asks=depth.asks,
                    exchange_timestamp_ms=depth.exchange_timestamp_ms,
                    received_timestamp_ms=depth.received_timestamp_ms,
                    sequence=depth.sequence,
                ),
                channel_evidence=ready["channels"],
                captured_at=current.isoformat(),
                expires_at=(current + timedelta(milliseconds=max(1, min(int(ttl_ms), 5_000)))).isoformat(),
                trigger_evidence_hash=_digest(trigger_evidence) if trigger_evidence is not None else None,
            )

    def validate_snapshot_generation(
        self,
        snapshot: EntryExecutionMarketSnapshotV1,
    ) -> None:
        instrument = InstrumentId.from_parts(
            snapshot.instrument.venue,
            snapshot.instrument.symbol,
            snapshot.instrument.market_type,
        )
        with self._lock:
            if snapshot.stream_generation_id != self._stream_generation_id:
                raise ValueError("entry_execution_snapshot_stream_generation_mismatch")
            if snapshot.subscription_generation_id != self._subscriptions.get(instrument):
                raise ValueError("entry_execution_snapshot_subscription_generation_mismatch")

    def projection(self) -> list[dict[str, Any]]:
        with self._lock:
            instruments = sorted(
                self._subscriptions,
                key=lambda item: (item.exchange, item.market_type, item.symbol),
            )
        return [self.readiness(item) for item in instruments]


paper_market_state = PaperMarketState()


def mark_open_order_monitoring_gap() -> int:
    """Persist one bounded gap marker; never infer execution during the gap."""

    from aletheia.storage import repositories as repo

    evidence = _canonical({
        "model_version": PAPER_FILL_MODEL_VERSION,
        "state": "monitoring_gap",
        "fills_during_gap": "not_inferred",
        "resumed_at": datetime.now(timezone.utc).isoformat(),
    })
    with repo.transaction(immediate=True):
        cur = repo.execute_write(
            "UPDATE orders SET paper_execution_evidence=? "
            "WHERE execution_mode='paper_internal' AND paper_fill_model_version=? "
            "AND status IN ('pending','partially_filled')",
            (evidence, PAPER_FILL_MODEL_VERSION),
        )
    return int(cur.rowcount)
