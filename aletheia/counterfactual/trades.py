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

logger = logging.getLogger("aletheia.counterfactual.trades")


def _max_event_snapshot_age_seconds() -> int:
    """Two market_data collection intervals worth of lag.

    A close-time counterfactual may use at most two collection intervals of
    market_snapshot lag; anything older is not evidence of the market at the
    Position close event. Reads the cadence from the scheduler so a change to
    the ``market_data`` job interval stays in sync automatically instead of
    silently drifting.
    """
    try:
        from aletheia.agent_runtime.scheduler import MARKET_DATA_INTERVAL_MINUTES

        return int(MARKET_DATA_INTERVAL_MINUTES) * 60 * 2
    except Exception:
        return 10 * 60


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
    policy_epoch_id = _current_policy_epoch()
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
            policy_epoch_id=policy_epoch_id,
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


def _compute_shadow_economics(
    *,
    row: dict,
    exit_price: float,
    hold_seconds: float,
) -> dict | None:
    """Compute cost-aware economics for a shadow_trade being resolved.

    Wraps ``estimate_outcome_economics`` with config-loaded fee/spread/slip
    defaults so every resolver (event-driven Path 1 and periodic Path 2)
    populates the same cost-model shape as filter_candidate_outcomes settlement.
    Returns None if the required entry-side fields are missing or invalid —
    the caller then falls back to the historic minimal-writer path (pnl_pct
    only), preserving observability without spuriously populating economics.

    net_return_pct may still be None inside the returned dict when funding
    data is unavailable for a perp row; this is intentional (`assess_candidate_outcome`
    fails closed on `net_economics_unavailable`, which is the correct signal).
    """
    try:
        from aletheia.universe.outcome_economics import estimate_outcome_economics
        from aletheia.config.loader import load_config
    except Exception:
        return None
    try:
        entry = float(row.get("entry_price"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(entry) or entry <= 0:
        return None
    direction = str(row.get("direction") or "").lower()
    if direction not in {"long", "short"}:
        return None
    try:
        cfg = load_config()
    except Exception:
        return None
    market_type = str(row.get("market_type") or "spot").lower()
    entry_spread = row.get("entry_spread_bps")
    funding_rate = row.get("funding_rate_at_entry")
    try:
        return estimate_outcome_economics(
            entry_price=entry,
            exit_price=float(exit_price),
            direction=direction,
            market_type=market_type,
            hold_seconds=max(0.0, float(hold_seconds or 0.0)),
            entry_spread_bps=float(entry_spread) if entry_spread is not None else None,
            fallback_spread_bps=float(getattr(cfg.universe, "max_spread_bps", 0.0) or 0.0),
            funding_rate_at_entry=(
                float(funding_rate) if funding_rate is not None else None
            ),
            execution_fee_pct=float(getattr(cfg.trading, "execution_fee_pct", 0.0) or 0.0),
            slippage_bps=float(getattr(cfg.trading, "slippage_bps", 0.0) or 0.0),
            price_quality_ok=True,
        )
    except Exception as exc:
        logger.debug("shadow economics unavailable for %s: %s", row.get("id"), exc)
        return None


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
    from aletheia.storage.repositories import resolve_shadow_trade_if_pending
    from aletheia.storage.sqlite import get_db

    db = get_db()
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=min_age_hours)).isoformat()
    rows = db.execute(
        "SELECT id, symbol, direction, entry_price, entry_spread_bps, "
        " funding_rate_at_entry, market_type, created_at "
        "FROM shadow_trades "
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

        # Fixed horizon = horizon_hours (job default 24h). Fill the same
        # cost-aware economics shape as the filter_candidate_outcomes settler so
        # downstream consumers (reflection, alpha_attribution, learning verdict)
        # can trust net_pnl_pct + cost_model_version consistently across paths.
        hold_seconds = float(horizon_hours) * 3600.0
        economics = _compute_shadow_economics(
            row=dict(row), exit_price=exit_price, hold_seconds=hold_seconds,
        )

        if not dry_run:
            try:
                resolve_shadow_trade_if_pending(
                    row["id"],
                    exit_price=exit_price,
                    pnl_pct=pnl_pct,
                    resolved_at=now_iso,
                    economics=economics,
                    settlement_price_source="stale_forward_ohlcv" if economics else None,
                    settlement_lag_sec=hold_seconds if economics else None,
                )
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
                event_dt - timedelta(seconds=_max_event_snapshot_age_seconds())
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


def list_deduped_shadows(
    *,
    days: int,
    symbols: list[str] | None = None,
    policy_epoch_id: str | None = None,
    columns: tuple[str, ...] = (
        "symbol",
        "direction",
        "trade_score",
        "net_pnl_pct",
        "was_selected",
    ),
) -> list[dict]:
    """Return one resolved shadow row per candidate (latest tick per ``outcome_candidate_key``).

    A candidate is re-scanned many times, appending a shadow row each tick;
    reading the raw table therefore over-weights long-lived candidates. Every
    analytics/reporting consumer must collapse to one vote per candidate — this
    helper is the canonical implementation. Prior to its introduction each
    consumer maintained its own ``PARTITION BY outcome_candidate_key`` CTE,
    which was memory-flagged as an easy-to-forget correctness trap.

    Args:
        days: lookback window in days (``created_at >= now-days``).
        symbols: canonical symbol filter (e.g. ``["BTC/USDT"]``); ``None`` for all.
        policy_epoch_id: when set, restrict to rows written under the same
            policy epoch — required whenever the caller crosses an epoch
            rotation (see counterfactual guidance L0 completeness).
        columns: projection list. Every column must exist in ``shadow_trades``.
    """
    from aletheia.storage.sqlite import get_db

    params: list[object] = [f"-{int(days)} days"]
    symbol_clause = ""
    if symbols:
        placeholders = ",".join("?" for _ in symbols)
        symbol_clause = f"AND symbol IN ({placeholders})"
        params.extend(symbols)
    epoch_clause = ""
    if policy_epoch_id:
        epoch_clause = "AND policy_epoch_id = ?"
        params.append(policy_epoch_id)

    projection = ", ".join(columns)
    sql = f"""
        WITH resolved AS (
            SELECT
                {projection},
                outcome_candidate_key,
                ROW_NUMBER() OVER (
                    PARTITION BY outcome_candidate_key
                    ORDER BY created_at DESC
                ) AS rn
            FROM shadow_trades
            WHERE net_pnl_pct IS NOT NULL
              AND outcome_candidate_key IS NOT NULL
              AND created_at >= datetime('now', ?)
              {symbol_clause}
              {epoch_clause}
        )
        SELECT {projection}
        FROM resolved
        WHERE rn = 1
    """
    return [dict(r) for r in get_db().execute(sql, params).fetchall()]


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


def _current_policy_epoch() -> str | None:
    """Return the currently active policy epoch for cross-epoch partitioning.

    Reuses the skip-remediation epoch because it captures the operator + ranker
    + threshold state that also governs which candidates land in a shadow batch;
    a new policy that could shift shadow-trade interpretation rotates this
    epoch. Returns None on failure so shadow persistence still succeeds — a
    row without ``policy_epoch_id`` degrades gracefully into a legacy partition.
    """
    try:
        from aletheia.agent_shell.skip_policy import ensure_skip_remediation_policy_epoch

        return ensure_skip_remediation_policy_epoch()
    except Exception as exc:
        logger.debug("policy_epoch unavailable for shadow batch: %s", exc)
        return None


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

    event_dt = _parse_entry_dt(event_time)

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

        # Cost-model economics. Hold seconds = event_time - created_at (the shadow
        # is conceptually held for the same wall-clock window as the parent Position).
        entry_dt = _parse_entry_dt(str(row.get("created_at") or ""))
        hold_seconds = 0.0
        if entry_dt is not None and event_dt is not None:
            hold_seconds = max(0.0, (event_dt - entry_dt).total_seconds())
        economics = _compute_shadow_economics(
            row=row, exit_price=exit_price, hold_seconds=hold_seconds,
        )

        try:
            if resolve_shadow_trade_if_pending(
                row["id"],
                exit_price=exit_price,
                pnl_pct=pnl_pct,
                resolved_at=event_time,
                economics=economics,
                settlement_price_source="position_close" if economics else None,
                settlement_lag_sec=hold_seconds if economics else None,
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
