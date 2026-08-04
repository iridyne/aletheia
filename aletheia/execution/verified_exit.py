"""Broker-neutral, read-only evidence contract for a disappeared position.

This module does not close positions, place orders, or mutate accounting. Exchange
clients implement the protocol by directly checking the scoped venue position and
private fill history. Any incomplete or ambiguous evidence remains inconclusive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import time
from typing import Literal

from aletheia.data.symbols import to_canonical


VerifiedExitState = Literal[
    "verified_fill",
    "confirmed_absent_without_fill",
    "inconclusive",
]


@dataclass(frozen=True)
class VerifiedExitRequest:
    broker_id: str
    account_environment: str
    account_scope: str
    execution_mode: str
    position_id: str
    symbol: str
    market_type: str
    direction: Literal["long", "short"]
    quantity: float
    history_start_ms: int
    history_end_ms: int
    # Exact exchange Order identities for entry legs already represented by the
    # local Position.  Providers may ignore only these known opening fills while
    # scanning a complete private history window; any other non-close activity
    # remains ambiguous and therefore fail-closed.
    known_entry_order_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        required = (
            self.broker_id,
            self.account_environment,
            self.account_scope,
            self.execution_mode,
            self.position_id,
            self.symbol,
            self.market_type,
        )
        if any(not str(value).strip() for value in required):
            raise ValueError("verified-exit identity fields must be non-empty")
        if self.direction not in {"long", "short"}:
            raise ValueError("direction must be long or short")
        if not math.isfinite(float(self.quantity)) or self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.history_start_ms < 1 or self.history_end_ms <= self.history_start_ms:
            raise ValueError("verified-exit history window is invalid")
        object.__setattr__(self, "symbol", to_canonical(self.symbol))
        normalized_entry_ids = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in self.known_entry_order_ids
                if str(value).strip()
            )
        )
        object.__setattr__(self, "known_entry_order_ids", normalized_entry_ids)


@dataclass(frozen=True)
class VerifiedExitFill:
    fill_id: str
    order_id: str | None
    price: float
    quantity: float
    fee: float | None
    fee_currency: str | None
    realized_pnl: float | None
    filled_at_ms: int


@dataclass(frozen=True)
class VerifiedExitResult:
    state: VerifiedExitState
    reason: str
    broker_id: str
    account_environment: str
    account_scope: str
    execution_mode: str
    position_id: str
    symbol: str
    market_type: str
    direction: str
    position_absent: bool
    history_complete: bool
    history_start_ms: int
    history_end_ms: int
    pages: int
    source_endpoint: str
    fills: tuple[VerifiedExitFill, ...] = field(default_factory=tuple)


def _usable_fee(value: float | None) -> bool:
    try:
        fee = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(fee) and fee >= 0


def private_history_window_available(
    request: VerifiedExitRequest,
    *,
    retention_days: int = 90,
    max_span_days: int = 90,
) -> bool:
    """Reject windows the venue cannot prove complete, including expired history."""

    day_ms = 24 * 60 * 60 * 1000
    now_ms = int(time.time() * 1000)
    return (
        request.history_end_ms - request.history_start_ms <= max_span_days * day_ms
        and request.history_start_ms >= now_ms - retention_days * day_ms
        and request.history_end_ms <= now_ms + 5 * 60 * 1000
    )


def inconclusive_verified_exit(
    request: VerifiedExitRequest,
    reason: str,
    *,
    position_absent: bool = False,
    history_complete: bool = False,
    pages: int = 0,
    source_endpoint: str = "unavailable",
) -> VerifiedExitResult:
    return _result(
        request,
        state="inconclusive",
        reason=reason,
        position_absent=position_absent,
        history_complete=history_complete,
        pages=pages,
        source_endpoint=source_endpoint,
    )


def classify_verified_exit(
    request: VerifiedExitRequest,
    *,
    position_absent: bool,
    history_complete: bool,
    close_fills: tuple[VerifiedExitFill, ...],
    ambiguous_activity: bool,
    pages: int,
    source_endpoint: str,
) -> VerifiedExitResult:
    """Apply the shared fail-closed decision after an adapter normalizes evidence."""

    if not position_absent:
        return inconclusive_verified_exit(
            request,
            "position_present",
            history_complete=history_complete,
            pages=pages,
            source_endpoint=source_endpoint,
        )
    if not history_complete:
        return inconclusive_verified_exit(
            request,
            "fill_history_incomplete",
            position_absent=True,
            pages=pages,
            source_endpoint=source_endpoint,
        )
    if ambiguous_activity:
        return inconclusive_verified_exit(
            request,
            "ambiguous_fill_activity",
            position_absent=True,
            history_complete=True,
            pages=pages,
            source_endpoint=source_endpoint,
        )
    if not close_fills:
        return _result(
            request,
            state="confirmed_absent_without_fill",
            reason="position_absent_complete_history_no_fill",
            position_absent=True,
            history_complete=True,
            pages=pages,
            source_endpoint=source_endpoint,
        )

    fill_ids = [fill.fill_id for fill in close_fills]
    if len(fill_ids) != len(set(fill_ids)):
        return inconclusive_verified_exit(
            request,
            "duplicate_fill_evidence",
            position_absent=True,
            history_complete=True,
            pages=pages,
            source_endpoint=source_endpoint,
        )
    total_quantity = sum(fill.quantity for fill in close_fills)
    tolerance = max(1e-12, request.quantity * 1e-8)
    if total_quantity + tolerance < request.quantity:
        return inconclusive_verified_exit(
            request,
            "partial_close_evidence",
            position_absent=True,
            history_complete=True,
            pages=pages,
            source_endpoint=source_endpoint,
        )
    if any(
        not fill.fill_id
        or not fill.order_id
        or not math.isfinite(fill.price)
        or fill.price <= 0
        or not math.isfinite(fill.quantity)
        or fill.quantity <= 0
        or not _usable_fee(fill.fee)
        or not request.history_start_ms <= fill.filled_at_ms <= request.history_end_ms
        for fill in close_fills
    ):
        return inconclusive_verified_exit(
            request,
            "malformed_fill_evidence",
            position_absent=True,
            history_complete=True,
            pages=pages,
            source_endpoint=source_endpoint,
        )
    return _result(
        request,
        state="verified_fill",
        reason="verified_private_close_fills",
        position_absent=True,
        history_complete=True,
        pages=pages,
        source_endpoint=source_endpoint,
        fills=tuple(sorted(close_fills, key=lambda fill: (fill.filled_at_ms, fill.fill_id))),
    )


def _result(
    request: VerifiedExitRequest,
    *,
    state: VerifiedExitState,
    reason: str,
    position_absent: bool,
    history_complete: bool,
    pages: int,
    source_endpoint: str,
    fills: tuple[VerifiedExitFill, ...] = (),
) -> VerifiedExitResult:
    return VerifiedExitResult(
        state=state,
        reason=reason,
        broker_id=request.broker_id,
        account_environment=request.account_environment,
        account_scope=request.account_scope,
        execution_mode=request.execution_mode,
        position_id=request.position_id,
        symbol=request.symbol,
        market_type=request.market_type,
        direction=request.direction,
        position_absent=position_absent,
        history_complete=history_complete,
        history_start_ms=request.history_start_ms,
        history_end_ms=request.history_end_ms,
        pages=pages,
        source_endpoint=source_endpoint,
        fills=fills,
    )
