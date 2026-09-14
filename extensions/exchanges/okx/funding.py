"""okx private funding bill normalization for UTA."""

from typing import Iterable, Mapping

from aletheia.uta.funding import _deduplicate, _event


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
