"""Process-local health state for public market-data venues."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import threading
import time
from typing import Any

from aletheia.infra.events import emit_infra_event

_COOLDOWN_SEC = 300
_VENUE_HEALTH: dict[str, "VenueHealthState"] = {}
_VENUE_HEALTH_LOCK = threading.Lock()
_REFUSAL_TEXT_MARKERS = (
    "418",
    "429",
    "-1003",
    "banned",
    "rate limit",
    "ratelimit",
    "too many requests",
    "ddosprotection",
)


class VenueCoolingDownError(RuntimeError):
    """Raised locally when a venue was recently refusing this process."""


@dataclass
class VenueHealthState:
    venue: str
    cooldown_until: float = 0
    last_error: str | None = None
    last_failure_at: float | None = None
    last_success_at: float | None = None


def is_venue_refusal(exc: BaseException) -> bool:
    """Whether an error says the venue is refusing this host, not this symbol."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) or getattr(response, "status", None)
    if status in {418, 429}:
        return True
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return any(marker in name or marker in text for marker in _REFUSAL_TEXT_MARKERS)


def is_venue_available(venue: str) -> bool:
    key = venue.strip().lower()
    with _VENUE_HEALTH_LOCK:
        state = _VENUE_HEALTH.get(key)
        return not state or state.cooldown_until <= time.time()


def _state(venue: str) -> VenueHealthState:
    key = venue.strip().lower()
    state = _VENUE_HEALTH.get(key)
    if state is None:
        state = VenueHealthState(venue=key)
        _VENUE_HEALTH[key] = state
    return state


def record_venue_failure(venue: str, exc: BaseException) -> None:
    """Open the process-wide breaker after an explicit venue refusal."""
    now = time.time()
    with _VENUE_HEALTH_LOCK:
        state = _state(venue)
        state.cooldown_until = now + _COOLDOWN_SEC
        state.last_error = str(exc)[:300]
        state.last_failure_at = now
    emit_infra_event(
        severity="warning",
        source="market_data",
        code="market_data_venue_cooldown",
        entity_id=state.venue,
        message=f"Market-data venue cooling down after refusing requests: {state.venue}",
        action="venue_skipped",
        metadata={"venue": state.venue, "cooldown_sec": _COOLDOWN_SEC},
        dedupe_sec=60,
    )


def record_venue_success(venue: str) -> None:
    """Clear a cooling venue only after it returns a real market-data response."""
    now = time.time()
    with _VENUE_HEALTH_LOCK:
        state = _state(venue)
        was_cooling = state.cooldown_until > now
        state.cooldown_until = 0
        state.last_error = None
        state.last_success_at = now
    if was_cooling:
        emit_infra_event(
            severity="info",
            source="market_data",
            code="market_data_venue_recovered",
            entity_id=state.venue,
            message=f"Market-data venue recovered: {state.venue}",
            action="venue_reenabled",
            dedupe_sec=60,
        )


def get_venue_health_snapshot() -> list[dict[str, Any]]:
    """Return sanitized process-local venue health for operator diagnostics."""
    now = time.time()
    with _VENUE_HEALTH_LOCK:
        states = [VenueHealthState(**asdict(state)) for state in _VENUE_HEALTH.values()]
    return [
        {
            **asdict(state),
            "cooling_down": state.cooldown_until > now,
            "cooldown_remaining_sec": max(0, round(state.cooldown_until - now, 1)),
        }
        for state in sorted(states, key=lambda item: item.venue)
    ]


def reset_venue_health() -> None:
    """Test helper."""
    with _VENUE_HEALTH_LOCK:
        _VENUE_HEALTH.clear()
