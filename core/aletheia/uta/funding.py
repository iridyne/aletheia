"""Exchange-neutral funding event validation and history retention."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable

from aletheia.data.symbols import to_canonical

FUNDING_HISTORY_RETENTION_EXPIRED = "funding_history_retention_expired"


def is_funding_history_retention_expired(
    start_at: datetime,
    *,
    received_at: datetime,
    retention_days: int,
) -> bool:
    """Apply the same credential-free retention boundary as the UTA adapter."""
    return start_at < received_at - timedelta(days=retention_days)


def _event(event_id, symbol, amount, currency, timestamp, raw_type) -> dict:
    identity = str(event_id or "").strip()
    canonical = to_canonical(str(symbol or ""))
    settlement = str(currency or "").strip().upper()
    kind = str(raw_type or "").strip()
    try:
        cash_change = Decimal(str(amount))
        occurred_at_ms = int(timestamp)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid UTA funding event") from exc
    if (
        not identity
        or not canonical
        or settlement != "USDT"
        or not kind
        or not cash_change.is_finite()
        or occurred_at_ms <= 0
    ):
        raise ValueError("invalid UTA funding event")
    return {
        "event_id": identity,
        "symbol": canonical,
        "cash_change": cash_change,
        "currency": settlement,
        "occurred_at_ms": occurred_at_ms,
    }


def _deduplicate(events: Iterable[dict]) -> list[dict]:
    unique = {}
    for event in events:
        previous = unique.get(event["event_id"])
        if previous is not None and previous != event:
            raise ValueError("conflicting UTA funding event identity")
        unique[event["event_id"]] = event
    return sorted(
        unique.values(),
        key=lambda event: (event["occurred_at_ms"], event["event_id"]),
    )
