"""Universe Forward Outcome Recorder.

Tracks how universe members perform AFTER being selected, answering:
"did the scanner families actually pick winners?"

Two entry points called from UniverseService.refresh():
  1. register_pending(members) — create outcome rows for each member × horizon
  2. settle_due() — find overdue pending rows, fetch exit price, compute return
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from aletheia.storage.models import UniverseForwardOutcome
from aletheia.storage import repositories as repo

logger = logging.getLogger("aletheia.universe.outcome_recorder")

_HORIZONS: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "24h": timedelta(hours=24),
}


def register_pending(members: list[dict], entry_ts: str) -> int:
    """Create pending outcome rows for each member × horizon.

    Args:
        members: list of member dicts (must have id, snapshot_id, symbol,
                 scanner_family, entry_price_usd).
        entry_ts: ISO timestamp of the universe snapshot creation.

    Returns:
        Number of outcome rows created.
    """
    ts = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    outcomes: list[UniverseForwardOutcome] = []
    for m in members:
        entry_price = m.get("entry_price_usd")
        if not entry_price or entry_price <= 0:
            continue

        for horizon_key, delta in _HORIZONS.items():
            due_at = (ts + delta).isoformat()
            outcomes.append(UniverseForwardOutcome(
                member_id=m["id"],
                snapshot_id=m["snapshot_id"],
                symbol=m["symbol"],
                scanner_family=m.get("scanner_family"),
                horizon=horizon_key,
                entry_price_usd=entry_price,
                entry_ts=entry_ts,
                due_at=due_at,
            ))

    if outcomes:
        repo.insert_forward_outcomes_batch(outcomes)
        logger.debug("Registered %d forward outcome rows", len(outcomes))

    return len(outcomes)


def settle_due() -> int:
    """Settle all overdue pending outcomes by fetching current prices.

    Returns:
        Number of outcomes settled.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    pending = repo.list_pending_forward_outcomes(before_ts=now_iso, limit=500)
    if not pending:
        return 0

    symbols_needed = list({r["symbol"] for r in pending})
    prices = _batch_fetch_prices(symbols_needed)

    settled = 0
    for row in pending:
        symbol = row["symbol"]
        price = prices.get(symbol)
        if price is None or price <= 0:
            repo.fail_forward_outcome(row["id"], settled_at=now_iso)
            continue

        entry = row["entry_price_usd"]
        forward_return = ((price - entry) / entry) * 100.0

        repo.settle_forward_outcome(
            outcome_id=row["id"],
            exit_price=price,
            forward_return=forward_return,
            settled_at=now_iso,
        )
        settled += 1

    if settled:
        logger.info("Settled %d / %d forward outcomes", settled, len(pending))
    return settled


def _batch_fetch_prices(symbols: list[str]) -> dict[str, float | None]:
    """Fetch latest prices for symbols. Uses market_data snapshots first, falls back to None."""
    from aletheia.trading.market_data import latest_snapshot

    prices: dict[str, float | None] = {}
    for sym in symbols:
        try:
            snap = latest_snapshot(sym)
            prices[sym] = float(snap["price"]) if snap and snap.get("price") else None
        except Exception:
            prices[sym] = None
    return prices
