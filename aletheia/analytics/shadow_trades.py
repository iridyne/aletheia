"""Edge Lab: shadow trade persistence.

After the paper_trader ranker selects a winner, we persist the Top-N candidates
as shadow trades so we can later answer: "what would have happened if we'd traded
the #2 or #3 ranked opportunity instead?"

Each tick produces one `batch_id` group. `was_selected=True` marks the actual
trade; the rest are counterfactuals. Entry price and regime tag are stamped at
creation time using live market data and the latest macro snapshot.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger("aletheia.analytics.shadow_trades")


def persist_shadow_batch(
    top_candidates: list[dict],
    selected: dict | None,
    top_n: int = 5,
) -> list[str]:
    """Persist the top_n ranked candidates as shadow trades.

    Args:
        top_candidates: full ranked candidate list (already sorted by trade_score desc).
        selected: the candidate that was actually selected (may be None).
        top_n: how many shadow rows to write per tick.

    Returns:
        list of inserted shadow trade IDs.
    """
    from aletheia.storage.models import ShadowTrade
    from aletheia.storage.repositories import insert_shadow_trade

    if not top_candidates:
        return []

    batch_id = uuid4().hex[:12]
    now_iso = datetime.now(timezone.utc).isoformat()
    regime = _current_regime()

    inserted_ids: list[str] = []
    selected_opp_id = selected.get("opportunity_id") if selected else None

    for rank, candidate in enumerate(top_candidates[:top_n], start=1):
        opp_id = candidate.get("opportunity_id")
        symbol = candidate.get("symbol") or ""
        direction = candidate.get("direction") or candidate.get("direction_bias") or "long"
        entry_price = _current_price(symbol)

        shadow = ShadowTrade(
            batch_id=batch_id,
            opportunity_id=opp_id or "",
            symbol=symbol,
            direction=direction,
            scanner_family=candidate.get("scanner_family"),
            trade_score=float(candidate.get("trade_score") or -999),
            rank=rank,
            entry_price=entry_price,
            regime_tag=regime,
            was_selected=(opp_id is not None and opp_id == selected_opp_id),
        )
        try:
            insert_shadow_trade(shadow)
            inserted_ids.append(shadow.id)
        except Exception as exc:
            logger.warning("shadow trade insert failed for %s: %s", symbol, exc)

    logger.debug("shadow batch %s: %d rows, regime=%s", batch_id, len(inserted_ids), regime)
    return inserted_ids


def _current_price(symbol: str) -> float | None:
    """Return the latest market price for a symbol, or None."""
    if not symbol:
        return None
    try:
        from aletheia.trading.market_data import latest_snapshot
        snap = latest_snapshot(symbol)
        return float(snap["price"]) if snap else None
    except Exception:
        return None


def _current_regime() -> str:
    """Return the current macro regime tag from the latest macro snapshot."""
    try:
        from aletheia.storage.repositories import latest_macro_snapshot
        macro = latest_macro_snapshot()
        if macro and macro.get("risk_regime"):
            return str(macro["risk_regime"])
    except Exception:
        pass
    return "unknown"


def resolve_shadow_trades_for_position(position: dict) -> int:
    """Resolve all shadow trades matching the position's opportunity.

    Called at position close time to stamp exit_price and pnl on all shadow
    trades from the same batch (counterfactuals included).

    Returns number of rows updated.
    """
    from aletheia.storage.repositories import _select_all, _update
    from datetime import datetime, timezone

    opp_id = position.get("opportunity_id") or position.get("proposal_opportunity_id")
    if not opp_id:
        return 0

    # Find the shadow row that was_selected for this opportunity
    selected_rows = _select_all(
        "shadow_trades",
        where="opportunity_id = ? AND was_selected = 1 AND resolved_at IS NULL",
        params=(opp_id,),
        limit=1,
    )
    if not selected_rows:
        return 0

    batch_id = selected_rows[0].get("batch_id")
    if not batch_id:
        return 0

    # Resolve ALL shadows in the batch (both selected and counterfactuals)
    batch_rows = _select_all(
        "shadow_trades",
        where="batch_id = ? AND resolved_at IS NULL",
        params=(batch_id,),
        limit=50,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    updated = 0
    for row in batch_rows:
        entry = row.get("entry_price")
        if entry is None or entry <= 0:
            continue
        symbol = row.get("symbol", "")
        exit_price = _current_price(symbol)
        if exit_price is None:
            continue
        direction = row.get("direction", "long")
        if direction == "long":
            pnl_pct = round((exit_price - entry) / entry * 100, 3)
        else:
            pnl_pct = round((entry - exit_price) / entry * 100, 3)

        try:
            _update("shadow_trades", row["id"], {
                "exit_price": exit_price,
                "pnl_pct": pnl_pct,
                "resolved_at": now_iso,
            })
            updated += 1
        except Exception as exc:
            logger.warning("shadow resolve failed for %s: %s", row["id"], exc)
    return updated
