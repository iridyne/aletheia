"""binance private funding bill normalization for UTA."""

from typing import Iterable, Mapping

from aletheia.uta.funding import _deduplicate, _event


def normalize_binance_funding_rows(rows: Iterable[Mapping]) -> list[dict]:
    return _deduplicate(
        _event(
            row.get("tranId"), row.get("symbol"), row.get("income"),
            row.get("asset"), row.get("time"), row.get("incomeType"),
        )
        for row in rows
        if str(row.get("incomeType") or "") == "FUNDING_FEE"
    )
