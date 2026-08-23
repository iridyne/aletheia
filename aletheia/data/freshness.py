"""Shared fail-closed freshness policy for decision-context market data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Mapping, TypeVar


FreshnessState = Literal["fresh", "missing", "invalid", "future", "stale"]

# These are decision-use limits, not source update frequencies. They allow several
# bounded collector attempts before data is removed from scoring and LLM context.
DOMAIN_MAX_AGE: dict[str, timedelta] = {
    "orderbook": timedelta(minutes=15),
    "funding": timedelta(minutes=15),
    "open_interest": timedelta(minutes=15),
    "macro": timedelta(minutes=30),
    "onchain": timedelta(minutes=30),
    "cross_venue_funding": timedelta(minutes=30),
    "cross_venue_open_interest": timedelta(minutes=30),
    "perp_structure": timedelta(minutes=60),
    "options_structure": timedelta(minutes=30),
}
MAX_FUTURE_SKEW = timedelta(minutes=5)

RowT = TypeVar("RowT", bound=Mapping)


@dataclass(frozen=True)
class FreshnessVerdict:
    usable: bool
    state: FreshnessState
    age_seconds: float | None
    max_age_seconds: float


def _as_utc(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def assess_freshness(
    row: Mapping | None,
    domain: str,
    *,
    now: datetime | None = None,
    timestamp_field: str = "observed_at",
) -> FreshnessVerdict:
    """Classify one persisted row for decision use.

    Missing or malformed timestamps fail closed. A small future skew is tolerated
    for host clock differences; larger future timestamps are rejected rather than
    remaining fresh indefinitely.
    """
    try:
        max_age = DOMAIN_MAX_AGE[domain]
    except KeyError as exc:
        raise ValueError(f"unknown freshness domain: {domain}") from exc

    if not row or not row.get(timestamp_field):
        return FreshnessVerdict(False, "missing", None, max_age.total_seconds())

    observed_at = _as_utc(row.get(timestamp_field))
    if observed_at is None:
        return FreshnessVerdict(False, "invalid", None, max_age.total_seconds())

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    age = current - observed_at
    age_seconds = age.total_seconds()
    if age < -MAX_FUTURE_SKEW:
        return FreshnessVerdict(False, "future", age_seconds, max_age.total_seconds())
    if age > max_age:
        return FreshnessVerdict(False, "stale", age_seconds, max_age.total_seconds())
    return FreshnessVerdict(True, "fresh", age_seconds, max_age.total_seconds())


def fresh_row(
    row: RowT | None,
    domain: str,
    *,
    now: datetime | None = None,
    timestamp_field: str = "observed_at",
) -> RowT | None:
    """Return the original row only when the entire snapshot is decision-usable."""
    verdict = assess_freshness(row, domain, now=now, timestamp_field=timestamp_field)
    return row if verdict.usable else None


def fresh_rows(
    rows: list[RowT],
    domain: str,
    *,
    now: datetime | None = None,
    timestamp_field: str = "observed_at",
) -> list[RowT]:
    """Filter rows without mutating values or partially retaining stale snapshots."""
    return [
        row for row in rows
        if assess_freshness(
            row, domain, now=now, timestamp_field=timestamp_field,
        ).usable
    ]
