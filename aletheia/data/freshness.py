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
    # Detector runs about every 10 minutes and writes an unchanged-state heartbeat
    # at 60 minutes; allow one bounded retry window before regime authority expires.
    "regime": timedelta(minutes=90),
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

    if not row:
        return FreshnessVerdict(False, "missing", None, max_age.total_seconds())

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    timestamp_values = [row.get(timestamp_field)]
    source_status = row.get("source_time_status") if timestamp_field == "observed_at" else None
    if source_status is not None:
        if source_status not in {"provided", "missing", "invalid", "future"}:
            source_status = "invalid"
        if source_status not in {"provided", "missing"}:
            return FreshnessVerdict(
                False,
                source_status,
                None,
                max_age.total_seconds(),
            )
        # "provided": use the exchange-sourced timestamp + acquisition time as
        # the two-clock bound.  "missing": the exchange gave no timestamp but
        # the data itself is valid; fall back to acquired_at for staleness so
        # the domain does not stay permanently "invalid" on exchanges that do
        # not emit a source timestamp (e.g. Bitget funding via CCXT).
        if source_status == "provided":
            timestamp_values = [row.get("source_observed_at"), row.get("acquired_at")]
        else:  # "missing"
            timestamp_values = [row.get("acquired_at")]

    ages: list[timedelta] = []
    for value in timestamp_values:
        if not value:
            return FreshnessVerdict(False, "missing", None, max_age.total_seconds())
        observed_at = _as_utc(value)
        if observed_at is None:
            return FreshnessVerdict(False, "invalid", None, max_age.total_seconds())
        age = current - observed_at
        age_seconds = age.total_seconds()
        if age < -MAX_FUTURE_SKEW:
            return FreshnessVerdict(False, "future", age_seconds, max_age.total_seconds())
        ages.append(age)

    oldest_age = max(ages)
    age_seconds = oldest_age.total_seconds()
    if oldest_age > max_age:
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
