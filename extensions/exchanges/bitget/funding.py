"""bitget private funding bill normalization for UTA."""

from typing import Iterable, Mapping

from aletheia.uta.funding import _deduplicate, _event


def normalize_bitget_funding_rows(rows: Iterable[Mapping]) -> list[dict]:
    return _deduplicate(
        _event(
            row.get("billId"), row.get("symbol"), row.get("amount"),
            row.get("coin"), row.get("cTime"), row.get("businessType"),
        )
        for row in rows
        if str(row.get("businessType") or "") == "contract_settle_fee"
    )
