"""Shared utilities for all exchange brokers (OKX, Bitget, Binance).

Functions here are exchange-agnostic — they must work for any venue.
"""


def entry_acknowledgement_state(status: object) -> tuple[str, str | None]:
    """Return durable Order state for an exchange entry acknowledgement."""
    normalized = str(status or "").strip().lower()
    if normalized in {"filled", "partially_filled"}:
        return "materialization_missing", "private_fill_evidence_pending"
    if normalized in {"submitted", "open"}:
        return "submitted", None
    if normalized in {"cancelled", "canceled", "rejected", "expired"}:
        return "cancelled", "explicit_venue_rejection"
    return "submission_unknown", "unrecognized_venue_order_status"


def entry_acknowledgement_result(acknowledgement: dict) -> dict:
    return {
        key: acknowledgement[key]
        for key in (
            "order_id",
            "exchange_order_id",
            "order_status",
            "fill_id",
            "position_id",
            "fill_price",
            "fill_quantity",
        )
    }


def close_acknowledgement_result(acknowledgement: dict) -> dict:
    return {
        "order_id": acknowledgement["order_id"],
        "exchange_order_id": acknowledgement["exchange_order_id"],
        "close_order_id": acknowledgement["order_id"],
        "status": acknowledgement["order_status"],
        "closed": acknowledgement["position_status"] == "closed",
        "position_id": acknowledgement["position_id"],
        "fill_price": acknowledgement["fill_price"],
        "fill_quantity": acknowledgement["fill_quantity"],
    }
