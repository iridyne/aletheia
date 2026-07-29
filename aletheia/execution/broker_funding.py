"""Authoritative broker-ledger funding economics for exchange-backed futures.

The exchange adapters normalize private account-bill rows into one small
contract.  This module then proves that a complete ledger window belongs to one
exact Aletheia Position before ``close_position`` is allowed to publish net PnL.
Public funding rates and guessed zeros are deliberately outside this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Iterable, Mapping

from aletheia.data.symbols import to_canonical


class BrokerFundingIntegrityError(ValueError):
    """Private funding evidence is malformed, incomplete, or ambiguous."""


@dataclass(frozen=True)
class BrokerFundingComputation:
    complete: bool
    funding_cost: float | None
    funding_source: str
    evidence: dict


_MODE_ENVIRONMENTS = {
    "bitget_demo": "demo",
    "bitget_live_limited": "live",
    "okx_demo": "demo",
    "okx_live_limited": "live",
    "binance_testnet": "testnet",
    "binance_live_limited": "live",
}


def _utc(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _finite(value: object, *, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise BrokerFundingIntegrityError(f"{field} is not numeric") from None
    if not math.isfinite(parsed):
        raise BrokerFundingIntegrityError(f"{field} is not finite")
    return parsed


def _positive_ms(value: object, *, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise BrokerFundingIntegrityError(f"{field} is not an integer") from None
    if parsed <= 0:
        raise BrokerFundingIntegrityError(f"{field} must be positive")
    return parsed


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


def _deduplicate(events: Iterable[dict]) -> list[dict]:
    unique: dict[str, dict] = {}
    for event in events:
        identity = str(event["event_id"])
        previous = unique.get(identity)
        if previous is not None and previous != event:
            raise BrokerFundingIntegrityError(
                f"conflicting broker funding event identity: {identity}"
            )
        unique[identity] = event
    return sorted(
        unique.values(),
        key=lambda item: (int(item["occurred_at_ms"]), str(item["event_id"])),
    )


def make_complete_ledger(
    *,
    broker_id: str,
    account_environment: str,
    source_endpoint: str,
    history_start_ms: int,
    history_end_ms: int,
    events: Iterable[dict],
) -> dict:
    expected_environment = _MODE_ENVIRONMENTS.get(str(broker_id))
    if expected_environment is None or expected_environment != account_environment:
        raise BrokerFundingIntegrityError("broker funding account identity mismatch")
    start_ms = _positive_ms(history_start_ms, field="history_start_ms")
    end_ms = _positive_ms(history_end_ms, field="history_end_ms")
    if start_ms > end_ms:
        raise BrokerFundingIntegrityError("funding history window is inverted")
    endpoint = str(source_endpoint or "").strip()
    if not endpoint:
        raise BrokerFundingIntegrityError("funding source endpoint is missing")
    normalized_events = _deduplicate(events)
    from aletheia.portfolio.disappearance_confirmation import (
        logical_account_fingerprint,
    )

    account_fingerprint = logical_account_fingerprint(
        broker_id=str(broker_id),
        account_environment=account_environment,
        execution_mode=str(broker_id),
    )
    return {
        "status": "complete",
        "broker_id": str(broker_id),
        "account_environment": account_environment,
        "account_fingerprint": account_fingerprint,
        "source_endpoint": endpoint,
        "history_start_ms": start_ms,
        "history_end_ms": end_ms,
        "observed_at_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
        "events": normalized_events,
    }


def funding_query_window(position: Mapping, closed_at: object) -> tuple[int, int]:
    opened = _utc(position.get("opened_at"))
    closed = _utc(closed_at)
    if opened is None or closed is None or closed <= opened:
        raise BrokerFundingIntegrityError("position funding window is invalid")
    return int(opened.timestamp() * 1000), int(closed.timestamp() * 1000)


def _position_contains_event(
    position: Mapping, event_ms: int, *, target_closed_at: datetime
) -> bool:
    opened = _utc(position.get("opened_at"))
    if opened is None:
        return False
    closed = _utc(position.get("closed_at"))
    if closed is None:
        closed = target_closed_at
    instant = datetime.fromtimestamp(event_ms / 1000, tz=timezone.utc)
    return opened < instant <= closed


def compute_broker_funding(
    *,
    position: Mapping,
    closed_at: object,
    ledger: Mapping,
    candidate_positions: Iterable[Mapping] = (),
) -> BrokerFundingComputation:
    """Attribute a complete private ledger window to one exact Position."""
    position_id = str(position.get("id") or "")
    execution_mode = str(position.get("execution_mode") or "")
    symbol = to_canonical(str(position.get("symbol") or ""))
    direction = str(position.get("direction") or "").lower()
    opened = _utc(position.get("opened_at"))
    closed = _utc(closed_at)
    if (
        not position_id
        or execution_mode not in _MODE_ENVIRONMENTS
        or position.get("market_type") != "usdt_futures"
        or direction not in {"long", "short", "buy", "sell"}
        or opened is None
        or closed is None
        or closed <= opened
    ):
        raise BrokerFundingIntegrityError("position funding identity is incomplete")
    normalized_direction = "long" if direction in {"long", "buy"} else "short"
    opened_ms = int(opened.timestamp() * 1000)
    closed_ms = int(closed.timestamp() * 1000)
    from aletheia.portfolio.disappearance_confirmation import (
        logical_account_fingerprint,
    )

    expected_account_fingerprint = logical_account_fingerprint(
        broker_id=execution_mode,
        account_environment=_MODE_ENVIRONMENTS[execution_mode],
        execution_mode=execution_mode,
    )
    if (
        ledger.get("status") != "complete"
        or ledger.get("broker_id") != execution_mode
        or ledger.get("account_environment") != _MODE_ENVIRONMENTS[execution_mode]
        or ledger.get("account_fingerprint") != expected_account_fingerprint
        or int(ledger.get("history_start_ms") or 0) > opened_ms
        or int(ledger.get("history_end_ms") or 0) < closed_ms
        # A just-closed window is not evidence of zero until the private
        # account ledger has had time to publish its terminal bill rows.
        or int(ledger.get("observed_at_ms") or 0) < closed_ms + 60_000
    ):
        return BrokerFundingComputation(
            complete=False,
            funding_cost=None,
            funding_source="unavailable",
            evidence={
                "status": "incomplete",
                "reason": "broker_funding_history_incomplete",
                "position_id": position_id,
            },
        )

    raw_events = ledger.get("events")
    if not isinstance(raw_events, list):
        raise BrokerFundingIntegrityError("broker funding ledger events are malformed")
    relevant = [
        dict(event)
        for event in raw_events
        if to_canonical(str(event.get("symbol") or "")) == symbol
        and opened_ms < int(event.get("occurred_at_ms") or 0) <= closed_ms
    ]
    all_candidates = [dict(position), *(dict(item) for item in candidate_positions)]
    for event in relevant:
        for candidate in all_candidates:
            if str(candidate.get("id") or "") == position_id:
                continue
            raw_evidence = candidate.get("reconciliation_evidence")
            if not raw_evidence:
                continue
            try:
                parsed_evidence = json.loads(str(raw_evidence))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            funding_evidence = (
                parsed_evidence.get("funding")
                if isinstance(parsed_evidence, dict)
                else None
            )
            used_events = (
                funding_evidence.get("events")
                if isinstance(funding_evidence, dict)
                else None
            )
            if isinstance(used_events, list) and any(
                isinstance(item, dict)
                and (
                    str(item.get("event_id") or "") == str(event["event_id"])
                    or str(event["event_id"]) in {
                        str(value)
                        for value in (
                            item.get("event_ids")
                            if isinstance(item.get("event_ids"), list)
                            else []
                        )
                    }
                )
                for item in used_events
            ):
                return BrokerFundingComputation(
                    complete=False,
                    funding_cost=None,
                    funding_source="unavailable",
                    evidence={
                        "status": "incomplete",
                        "reason": "broker_funding_event_already_claimed",
                        "position_id": position_id,
                        "event_id": event.get("event_id"),
                    },
                )
        event_direction = str(event.get("direction") or "").lower() or None
        matching_positions = []
        for candidate in all_candidates:
            if str(candidate.get("id") or "") in {
                str(item.get("id") or "") for item in matching_positions
            }:
                continue
            if (
                str(candidate.get("execution_mode") or "") == execution_mode
                and str(candidate.get("market_type") or "") == "usdt_futures"
                and to_canonical(str(candidate.get("symbol") or "")) == symbol
                and _position_contains_event(
                    candidate, int(event["occurred_at_ms"]),
                    target_closed_at=closed,
                )
            ):
                candidate_direction = str(candidate.get("direction") or "").lower()
                candidate_direction = (
                    "long" if candidate_direction in {"long", "buy"} else "short"
                )
                if event_direction is None or event_direction == candidate_direction:
                    matching_positions.append(candidate)
        if (
            len(matching_positions) != 1
            or str(matching_positions[0].get("id") or "") != position_id
            or (
                event_direction is not None
                and event_direction != normalized_direction
            )
        ):
            return BrokerFundingComputation(
                complete=False,
                funding_cost=None,
                funding_source="unavailable",
                evidence={
                    "status": "incomplete",
                    "reason": "broker_funding_attribution_ambiguous",
                    "position_id": position_id,
                    "event_id": event.get("event_id"),
                },
            )

    funding_cost = sum(float(event["funding_cost"]) for event in relevant)
    by_instant: dict[str, list[dict]] = {}
    for event in relevant:
        by_instant.setdefault(str(event["scheduled_at"]), []).append(event)
    evidence_events = []
    for scheduled_at, instant_events in sorted(by_instant.items()):
        evidence_events.append({
            "event_id": str(instant_events[0]["event_id"]),
            "event_ids": sorted(str(item["event_id"]) for item in instant_events),
            "scheduled_at": scheduled_at,
            "funding_cost": sum(
                float(item["funding_cost"]) for item in instant_events
            ),
            "cash_change": sum(
                float(item["cash_change"]) for item in instant_events
            ),
            "currency": instant_events[0]["currency"],
            "raw_type": sorted(
                {str(item["raw_type"]) for item in instant_events}
            ),
        })
    evidence = {
        "status": "complete",
        "source": "broker_ledger",
        "accounting_version": "broker-funding-v1",
        "position_id": position_id,
        "symbol": str(position.get("symbol") or ""),
        "execution_mode": execution_mode,
        "account_environment": ledger["account_environment"],
        "account_fingerprint": ledger["account_fingerprint"],
        "source_endpoint": ledger["source_endpoint"],
        "opened_at": opened.isoformat(),
        "closed_at": closed.isoformat(),
        "history_start_ms": int(ledger["history_start_ms"]),
        "history_end_ms": int(ledger["history_end_ms"]),
        "events": evidence_events,
    }
    evidence["fingerprint"] = "sha256:" + hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return BrokerFundingComputation(
        complete=True,
        funding_cost=funding_cost,
        funding_source="broker_ledger",
        evidence=evidence,
    )
