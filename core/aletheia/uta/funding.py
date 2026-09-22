"""Venue-private funding row normalization owned by the UTA boundary."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

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


def normalize_bitget_funding_rows(rows: Iterable[Mapping]) -> list[dict]:
    return _deduplicate(
        _event(
            row.get("billId"), row.get("symbol"), row.get("amount"),
            row.get("coin"), row.get("cTime"), row.get("businessType"),
        )
        for row in rows
        if str(row.get("businessType") or "") == "contract_settle_fee"
    )


def normalize_okx_funding_rows(rows: Iterable[Mapping]) -> list[dict]:
    events = []
    for row in rows:
        bill_type = str(row.get("type") or "")
        subtype = str(row.get("subType") or "")
        if bill_type != "8" and subtype != "173":
            continue
        symbol = str(row.get("instId") or "")
        if symbol.upper().endswith("-SWAP"):
            symbol = symbol[:-5]
        events.append(_event(
            row.get("billId"),
            symbol,
            row.get("pnl") if row.get("pnl") not in {None, ""} else row.get("balChg"),
            row.get("ccy"),
            row.get("ts"),
            f"{bill_type}:{subtype}",
        ))
    return _deduplicate(events)


def normalize_binance_funding_rows(rows: Iterable[Mapping]) -> list[dict]:
    return _deduplicate(
        _event(
            row.get("tranId"), row.get("symbol"), row.get("income"),
            row.get("asset"), row.get("time"), row.get("incomeType"),
        )
        for row in rows
        if str(row.get("incomeType") or "") == "FUNDING_FEE"
    )
