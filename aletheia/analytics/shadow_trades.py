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
import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4

logger = logging.getLogger("aletheia.analytics.shadow_trades")

# Market snapshots are collected every five minutes. A close-time
# counterfactual may use at most two collection intervals of lag; anything older
# is not evidence of the market at the Position close event.
_MAX_EVENT_SNAPSHOT_AGE_SECONDS = 10 * 60


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
        "WHERE resolved_at IS NULL AND exit_price IS NULL "
        "AND outcome_candidate_key IS NULL "
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
    at_or_before: str | None = None,
) -> float | None:
    """Return a market price, optionally pinned to a durable event time."""
    if not symbol:
        return None
    try:
        if at_or_before is not None:
            from aletheia.data.symbols import to_bitget, to_canonical
            from aletheia.storage.sqlite import get_db

            event_dt = _parse_entry_dt(at_or_before)
            if event_dt is None:
                return None
            oldest_usable = (
                event_dt - timedelta(seconds=_MAX_EVENT_SNAPSHOT_AGE_SECONDS)
            ).isoformat()

            canonical = to_canonical(symbol)
            symbols = [canonical]
            legacy_symbol = to_bitget(canonical)
            if legacy_symbol != canonical:
                symbols.append(legacy_symbol)
            normalized_market = (
                "perp"
                if str(market_type or "").lower()
                in {"perp", "futures", "swap", "usdt_futures"}
                else "spot" if str(market_type or "").lower() == "spot" else None
            )

            placeholders = ",".join("?" for _ in symbols)
            base = (
                "SELECT price FROM market_snapshots "
                f"WHERE symbol IN ({placeholders}) "
                "AND datetime(snapshot_at) <= datetime(?) "
                "AND datetime(snapshot_at) >= datetime(?)"
            )
            params: list[object] = [*symbols, event_dt.isoformat(), oldest_usable]
            if normalized_market is not None:
                typed = get_db().execute(
                    base
                    + " AND market_type = ? "
                    "ORDER BY datetime(snapshot_at) DESC, rowid DESC LIMIT 1",
                    (*params, normalized_market),
                ).fetchone()
                if typed is not None:
                    price = float(typed["price"])
                    return price if math.isfinite(price) and price > 0 else None
                # A spot snapshot cannot prove a futures counterfactual (or vice
                # versa), even when it is temporally fresh.
                return None
            fallback = get_db().execute(
                base + " ORDER BY datetime(snapshot_at) DESC, rowid DESC LIMIT 1",
                params,
            ).fetchone()
            if fallback is None:
                return None
            price = float(fallback["price"])
            return price if math.isfinite(price) and price > 0 else None

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


def _normalized_market_type(value: object) -> str | None:
    raw = str(value or "").strip().lower()
    if raw in {"perp", "futures", "swap", "usdt_futures"}:
        return "perp"
    if raw == "spot":
        return "spot"
    return None


def _verified_position_exit_price(position: dict, shadow: dict) -> float | None:
    """Reuse exact verified fill economics only for the same instrument."""
    if not position.get("exit_fill_verified"):
        return None
    if position.get("exit_price_source") not in {"local_fill", "broker_fill"}:
        return None
    try:
        from aletheia.data.symbols import to_canonical

        if to_canonical(str(position.get("symbol") or "")) != to_canonical(
            str(shadow.get("symbol") or "")
        ):
            return None
    except (TypeError, ValueError):
        return None
    position_market = _normalized_market_type(position.get("market_type"))
    shadow_market = _normalized_market_type(shadow.get("market_type"))
    if position_market is None or position_market != shadow_market:
        return None
    try:
        price = float(position.get("current_price"))
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def resolve_shadow_trades_for_position(position: dict) -> int:
    """Resolve all shadow trades matching the position's opportunity.

    Called at position close time to stamp exit_price and pnl on all shadow
    trades from the same batch (counterfactuals included).

    Returns number of rows updated.
    """
    from aletheia.storage.repositories import (
        _select_all,
        anchor_shadow_trade_exit_price_if_pending,
        resolve_shadow_trade_if_pending,
        transaction,
    )

    opp_id = position.get("opportunity_id") or position.get("proposal_opportunity_id")
    if not opp_id:
        return 0

    # Find the shadow row that was_selected for this opportunity
    selected_rows = _select_all(
        "shadow_trades",
        where="opportunity_id = ? AND was_selected = 1 "
              "AND outcome_candidate_key IS NULL",
        params=(opp_id,),
        limit=1,
    )
    if not selected_rows:
        return 0

    batch_id = selected_rows[0].get("batch_id")
    if not batch_id:
        return 0

    event_time = str(position.get("closed_at") or "").strip()
    if not event_time:
        raise ValueError("shadow feedback requires the Position close time")

    # Resolve ALL shadows in the batch (both selected and counterfactuals)
    batch_rows = _select_all(
        "shadow_trades",
        where="batch_id = ? AND resolved_at IS NULL",
        params=(batch_id,),
        limit=50,
    )

    # Phase 1: determine and persist every available first-event price before
    # resolving any row. `exit_price` is already the durable economic field; an
    # unresolved row with only this field populated is an anchored-but-undelivered
    # feedback row. A crash or per-row failure can therefore replay without
    # sampling a later market price. Historical snapshot lookup is pinned to the
    # immutable Position.closed_at for a row that had no prior anchor.
    anchors: dict[str, float] = {}
    missing_anchor_ids: list[str] = []
    failures: list[str] = []
    for row in batch_rows:
        row_id = str(row.get("id") or "")
        try:
            entry = float(row.get("entry_price"))
        except (TypeError, ValueError):
            entry = 0.0
        if not math.isfinite(entry) or entry <= 0:
            failures.append(f"{row.get('id', 'unknown')}: invalid entry price")
            continue

        existing_anchor = row.get("exit_price")
        if existing_anchor is None:
            exit_price = _verified_position_exit_price(position, row)
            if exit_price is None:
                exit_price = _current_price(
                    row.get("symbol", ""),
                    market_type=row.get("market_type"),
                    at_or_before=event_time,
                )
            missing_anchor_ids.append(row_id)
        else:
            try:
                exit_price = float(existing_anchor)
            except (TypeError, ValueError):
                exit_price = None
        if (
            exit_price is None
            or not math.isfinite(float(exit_price))
            or float(exit_price) <= 0
        ):
            failures.append(f"{row.get('id', 'unknown')}: exit price unavailable")
            continue
        anchors[row_id] = float(exit_price)

    if anchors:
        with transaction(immediate=True):
            for row_id in missing_anchor_ids:
                exit_price = anchors.get(row_id)
                if exit_price is not None:
                    anchor_shadow_trade_exit_price_if_pending(
                        row_id,
                        exit_price=exit_price,
                    )

    # Refresh after the anchor transaction so a replay always consumes the
    # stored price, including when another idempotent worker won a CAS race.
    batch_rows = _select_all(
        "shadow_trades",
        where="batch_id = ? AND resolved_at IS NULL",
        params=(batch_id,),
        limit=50,
    )

    updated = 0
    for row in batch_rows:
        row_id = str(row.get("id") or "")
        if row_id not in anchors:
            continue
        entry = float(row["entry_price"])
        exit_price = float(row["exit_price"])
        direction = row.get("direction", "long")
        if direction == "long":
            pnl_pct = round((exit_price - entry) / entry * 100, 3)
        else:
            pnl_pct = round((entry - exit_price) / entry * 100, 3)

        try:
            if resolve_shadow_trade_if_pending(
                row["id"],
                exit_price=exit_price,
                pnl_pct=pnl_pct,
                resolved_at=event_time,
            ):
                updated += 1
        except Exception as exc:
            logger.warning("shadow resolve failed for %s: %s", row["id"], exc)
            failures.append(f"{row['id']}: {type(exc).__name__}: {exc}")
    if failures:
        raise RuntimeError(
            "shadow batch incomplete: " + "; ".join(failures[:10])
        )
    return updated
