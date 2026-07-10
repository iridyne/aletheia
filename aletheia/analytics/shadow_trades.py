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
from datetime import datetime, timedelta, timezone
from uuid import uuid4

logger = logging.getLogger("aletheia.analytics.shadow_trades")


def persist_shadow_batch(
    top_candidates: list[dict],
    selected: dict | list[dict] | None,
    top_n: int = 5,
) -> list[str]:
    """Persist the top_n ranked candidates as shadow trades.

    Args:
        top_candidates: full ranked candidate list (already sorted by trade_score desc).
        selected: candidate(s) actually emitted after occupancy/cooldown checks.
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
    try:
        from aletheia.config.loader import load_config

        venue = load_config().market_data.ccxt_exchange
    except Exception:
        venue = None

    inserted_ids: list[str] = []
    selected_candidates = selected if isinstance(selected, list) else ([selected] if selected else [])
    if isinstance(selected, list):
        selected_opp_ids = {
            item.get("opportunity_id") for item in selected if item.get("opportunity_id")
        }
    elif selected:
        selected_opp_ids = {selected.get("opportunity_id")}
    else:
        selected_opp_ids = set()

    rank_by_opportunity = {
        candidate.get("opportunity_id"): rank
        for rank, candidate in enumerate(top_candidates, start=1)
        if candidate.get("opportunity_id")
    }
    batch_candidates = list(top_candidates[:top_n])
    included_ids = {
        candidate.get("opportunity_id")
        for candidate in batch_candidates
        if candidate.get("opportunity_id")
    }
    for candidate in selected_candidates:
        opp_id = candidate.get("opportunity_id")
        if opp_id and opp_id not in included_ids:
            batch_candidates.append(candidate)
            included_ids.add(opp_id)

    for fallback_rank, candidate in enumerate(batch_candidates, start=1):
        opp_id = candidate.get("opportunity_id")
        rank = rank_by_opportunity.get(opp_id, fallback_rank)
        symbol = candidate.get("symbol") or ""
        direction = candidate.get("direction") or candidate.get("direction_bias") or "long"
        market_type = candidate.get("market_type") or "spot"
        entry_price = _current_price(symbol, market_type=market_type, exact=True)
        outcome_candidate_key = f"opportunity:{opp_id}" if opp_id else None

        shadow = ShadowTrade(
            batch_id=batch_id,
            opportunity_id=opp_id or "",
            symbol=symbol,
            venue=venue,
            market_type=market_type,
            direction=direction,
            scanner_family=candidate.get("scanner_family"),
            trade_score=float(candidate.get("trade_score") or -999),
            rank=rank,
            entry_price=entry_price,
            entry_spread_bps=candidate.get("entry_spread_bps"),
            funding_rate_at_entry=candidate.get("funding_rate_at_entry"),
            outcome_candidate_key=outcome_candidate_key,
            regime_tag=regime,
            was_selected=(opp_id is not None and opp_id in selected_opp_ids),
        )
        try:
            insert_shadow_trade(shadow)
            inserted_ids.append(shadow.id)
        except Exception as exc:
            logger.warning("shadow trade insert failed for %s: %s", symbol, exc)

    logger.debug("shadow batch %s: %d rows, regime=%s", batch_id, len(inserted_ids), regime)
    return inserted_ids


def _parse_entry_dt(created_at: str) -> datetime | None:
    """Parse a shadow_trades.created_at into a tz-aware UTC datetime.

    created_at is stored either as an ISO string with offset (model default,
    e.g. '2026-06-14T10:00:00+00:00') or as the SQLite `datetime('now')` form
    (naive, space-separated, e.g. '2026-06-14 10:00:00') for rows inserted via
    the column default. Both are UTC; a naive value must be tagged UTC so the
    epoch math below does not silently apply the local timezone.
    """
    try:
        dt = datetime.fromisoformat(created_at)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def resolve_stale_shadows(
    min_age_hours: int = 25,
    horizon_hours: int = 24,
    limit: int = 2000,
    dry_run: bool = False,
) -> dict:
    """Resolve unresolved shadow trades by their own historical forward horizon.

    Complements the event-driven resolve_shadow_trades_for_position(): that path
    only fires when the parent position carries an opportunity_id and uses the
    *current* price at close time. The pre-b9ad957 backlog has neither (positions
    with NULL opportunity_id), so those shadows never resolve. Here each shadow is
    resolved INDEPENDENTLY as a fixed-horizon counterfactual: exit = the close of
    the first 1h OHLCV bar at/after (entry_at + horizon_hours). This is the only
    feasible definition for the orphaned backlog (the parent close time is lost)
    and is consistent for the periodic forward catch-up too.

    Returns a counts dict: {resolved, skipped_no_ohlcv, skipped_bad_entry, scanned}.
    With dry_run=True, computes everything but writes nothing.
    """
    from aletheia.storage.repositories import _update
    from aletheia.storage.sqlite import get_db

    db = get_db()
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=min_age_hours)).isoformat()
    rows = db.execute(
        "SELECT id, symbol, direction, entry_price, created_at FROM shadow_trades "
        "WHERE resolved_at IS NULL AND outcome_candidate_key IS NULL "
        "AND created_at < ? ORDER BY created_at ASC LIMIT ?",
        (cutoff_iso, limit),
    ).fetchall()

    counts = {"resolved": 0, "skipped_no_ohlcv": 0, "skipped_bad_entry": 0, "scanned": len(rows)}
    now_iso = datetime.now(timezone.utc).isoformat()

    for row in rows:
        entry = row["entry_price"]
        entry_dt = _parse_entry_dt(row["created_at"] or "")
        if entry is None or float(entry) <= 0 or entry_dt is None:
            counts["skipped_bad_entry"] += 1
            continue

        target_ts_ms = int((entry_dt.timestamp() + horizon_hours * 3600) * 1000)
        bar = db.execute(
            "SELECT close FROM market_ohlcv "
            "WHERE symbol = ? AND timeframe = '1h' AND ts >= ? ORDER BY ts ASC LIMIT 1",
            (row["symbol"], target_ts_ms),
        ).fetchone()
        if bar is None or bar["close"] is None:
            counts["skipped_no_ohlcv"] += 1
            continue

        exit_price = float(bar["close"])
        entry_f = float(entry)
        if (row["direction"] or "long") == "long":
            pnl_pct = round((exit_price - entry_f) / entry_f * 100, 3)
        else:
            pnl_pct = round((entry_f - exit_price) / entry_f * 100, 3)

        if not dry_run:
            try:
                _update("shadow_trades", row["id"], {
                    "exit_price": exit_price,
                    "pnl_pct": pnl_pct,
                    "resolved_at": now_iso,
                })
            except Exception as exc:
                logger.warning("stale shadow resolve failed for %s: %s", row["id"], exc)
                continue
        counts["resolved"] += 1

    return counts


def _current_price(
    symbol: str,
    market_type: str | None = None,
    *,
    exact: bool = False,
) -> float | None:
    """Return the latest market price for a symbol, or None."""
    if not symbol:
        return None
    try:
        from aletheia.trading.market_data import latest_snapshot
        if exact:
            from aletheia.data.symbols import to_canonical
            from aletheia.storage.repositories import list_market_snapshots

            normalized_market = (
                "perp"
                if str(market_type or "spot").lower()
                in {"perp", "futures", "swap", "usdt_futures"}
                else "spot"
            )
            rows = list_market_snapshots(
                symbol=to_canonical(symbol),
                market_type=normalized_market,
                limit=1,
            )
            snap = rows[0] if rows else None
        else:
            snap = latest_snapshot(symbol, market_type=market_type)
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
        where="opportunity_id = ? AND was_selected = 1 AND resolved_at IS NULL "
              "AND outcome_candidate_key IS NULL",
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
