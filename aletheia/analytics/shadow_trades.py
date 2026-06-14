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
