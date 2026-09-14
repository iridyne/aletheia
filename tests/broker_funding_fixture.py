"""Historical native-ledger fixtures; never a production funding reader."""

from datetime import datetime, timezone
from typing import Iterable, Mapping

from aletheia.data.symbols import to_canonical
from aletheia.execution.broker_funding import (
    BrokerFundingIntegrityError,
    _deduplicate,
    _finite,
    _positive_ms,
)


def require_retained_history(
    start_time_ms: object,
    *,
    retention_days: int,
    source: str,
    now_ms: int | None = None,
) -> None:
    """Fail closed when an API cannot prove the requested ledger window."""
    start_ms = _positive_ms(start_time_ms, field="history_start_ms")
    observed_ms = (
        int(now_ms)
        if now_ms is not None
        else int(datetime.now(timezone.utc).timestamp() * 1000)
    )
    oldest_supported_ms = observed_ms - int(retention_days) * 24 * 60 * 60 * 1000
    if start_ms < oldest_supported_ms:
        raise BrokerFundingIntegrityError(
            f"{source} cannot prove funding history older than {retention_days} days"
        )



def _event(
    *,
    event_id: object,
    symbol: object,
    cash_change: object,
    currency: object,
    occurred_at_ms: object,
    raw_type: object,
    direction: object = None,
) -> dict:
    event_identity = str(event_id or "").strip()
    raw_symbol = str(symbol or "").strip()
    settlement_currency = str(currency or "").strip().upper()
    event_type = str(raw_type or "").strip()
    if not event_identity or not raw_symbol or not event_type:
        raise BrokerFundingIntegrityError("funding event identity is incomplete")
    if settlement_currency != "USDT":
        raise BrokerFundingIntegrityError(
            f"unsupported funding settlement currency: {settlement_currency or '?'}"
        )
    timestamp_ms = _positive_ms(occurred_at_ms, field="funding event timestamp")
    signed_cash_change = _finite(cash_change, field="funding cash change")
    normalized_direction = str(direction or "").strip().lower() or None
    if normalized_direction not in {None, "long", "short"}:
        raise BrokerFundingIntegrityError("funding event direction is invalid")
    return {
        "event_id": event_identity,
        "symbol": to_canonical(raw_symbol),
        "cash_change": signed_cash_change,
        # Venue ledgers expose signed account cash change. A payment is negative
        # cash, hence positive cost; a rebate is negative cost.
        "funding_cost": -signed_cash_change,
        "currency": settlement_currency,
        "scheduled_at": datetime.fromtimestamp(
            timestamp_ms / 1000, tz=timezone.utc
        ).isoformat(),
        "occurred_at_ms": timestamp_ms,
        "raw_type": event_type,
        "direction": normalized_direction,
    }


def normalize_bitget_funding_rows(rows: Iterable[Mapping]) -> list[dict]:
    events = []
    for row in rows:
        if str(row.get("businessType") or "") != "contract_settle_fee":
            continue
        events.append(_event(
            event_id=row.get("billId"),
            symbol=row.get("symbol"),
            cash_change=row.get("amount"),
            currency=row.get("coin"),
            occurred_at_ms=row.get("cTime"),
            raw_type=row.get("businessType"),
        ))
    return _deduplicate(events)

def normalize_okx_funding_rows(rows: Iterable[Mapping]) -> list[dict]:
    events = []
    for row in rows:
        bill_type = str(row.get("type") or "")
        subtype = str(row.get("subType") or "")
        if bill_type != "8" and subtype != "173":
            continue
        cash_change = row.get("pnl")
        if cash_change in (None, ""):
            cash_change = row.get("balChg")
        raw_direction = str(row.get("posSide") or "").lower()
        instrument_id = str(row.get("instId") or "")
        if instrument_id.upper().endswith("-SWAP"):
            instrument_id = instrument_id[:-5]
        events.append(_event(
            event_id=row.get("billId"),
            symbol=instrument_id,
            cash_change=cash_change,
            currency=row.get("ccy"),
            occurred_at_ms=row.get("ts"),
            raw_type=f"{bill_type}:{subtype}",
            direction=raw_direction if raw_direction in {"long", "short"} else None,
        ))
    return _deduplicate(events)


def normalize_binance_funding_rows(rows: Iterable[Mapping]) -> list[dict]:
    events = []
    for row in rows:
        if str(row.get("incomeType") or "") != "FUNDING_FEE":
            continue
        events.append(_event(
            event_id=row.get("tranId"),
            symbol=row.get("symbol"),
            cash_change=row.get("income"),
            currency=row.get("asset"),
            occurred_at_ms=row.get("time"),
            raw_type=row.get("incomeType"),
        ))
    return _deduplicate(events)
