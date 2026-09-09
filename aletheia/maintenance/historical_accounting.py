"""Read-only access to archived accounting reconciliations.

These records retain their original evidence and never qualify exchange trading.
"""

from __future__ import annotations

import json
from typing import Any


def historical_accounting_summary(*, execution_mode: str | None = None) -> dict[str, Any]:
    """Read-only reconciliation totals, deliberately separate from verified PnL."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    params: tuple[Any, ...] = ()
    mode_filter = ""
    if execution_mode:
        mode_filter = " AND p.execution_mode=?"
        params = (execution_mode,)
    rows = db.execute(
        "SELECT r.status,r.reconstructed_realized_pnl FROM historical_accounting_reconciliations r JOIN positions p ON p.id=r.position_id WHERE 1=1" + mode_filter,
        params,
    ).fetchall()
    reconstructed = [float(row["reconstructed_realized_pnl"]) for row in rows if row["status"] == "reconstructed" and row["reconstructed_realized_pnl"] is not None]
    return {
        "historical_reconciled_pnl": round(sum(reconstructed), 8) if reconstructed else None,
        "historical_reconciled_count": len(reconstructed),
        "historical_reconciliation_unavailable_count": sum(row["status"] == "unavailable" for row in rows),
    }


def historical_accounting_for_position(position_id: str) -> dict[str, Any] | None:
    from aletheia.storage.sqlite import get_db

    row = get_db().execute("SELECT * FROM historical_accounting_reconciliations WHERE position_id=?", (position_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    try:
        result["evidence"] = json.loads(result.pop("evidence_json"))
    except json.JSONDecodeError:
        result["evidence"] = {"status": "unreadable_evidence"}
    return result
