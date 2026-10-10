"""Market data, collector, capital-event and portfolio-snapshot persistence.

Shared helpers are imported inside functions to preserve both import orders
and the facade's transaction, connection and monkeypatch bindings.
"""

import json
import logging
import math
import sqlite3
from datetime import datetime, timedelta, timezone

__all__ = [
    "get_collector_health_state",
    "get_first_capital_event_time",
    "get_latest_market_regime",
    "get_latest_portfolio_snapshot",
    "get_latest_prediction_run",
    "get_net_deposits",
    "get_ohlcv_backfill_state",
    "get_ohlcv_candles",
    "get_ohlcv_coverage",
    "get_ohlcv_newest_ts",
    "get_prediction_event",
    "get_prediction_market_by_id",
    "get_volume_ranking_snapshot",
    "insert_collector_snapshot_batch",
    "latest_coingecko_top",
    "latest_funding_rate",
    "latest_macro_snapshot",
    "latest_market_snapshot_groups",
    "latest_onchain_snapshots",
    "latest_open_interest",
    "list_account_equity_snapshots",
    "list_capital_events_between",
    "list_capital_events_page",
    "list_collector_health_states",
    "list_market_regimes",
    "list_market_snapshots",
    "list_portfolio_snapshots",
    "list_prediction_claims",
    "list_prediction_events",
    "list_prediction_markets_for_event",
    "list_prediction_runs",
    "list_probability_snapshots",
    "macro_snapshot_near",
    "open_interest_near",
    "options_volatility_at",
    "prune_market_ohlcv",
    "prune_market_snapshots",
    "prune_ohlcv_backfill_state",
    "prune_portfolio_snapshots",
    "recent_liquidations",
    "save_volume_ranking_snapshot",
    "upsert_collector_health_state",
    "upsert_ohlcv_backfill_state",
    "upsert_ohlcv_candles",
]


def list_capital_events_page(
    *,
    limit: int = 20,
    before_detected_at: str | None = None,
    before_id: str | None = None,
) -> dict:
    """Return one stable, keyset-paginated page of the retained cash-flow ledger."""
    from aletheia.storage.repositories import get_db

    if (before_detected_at is None) != (before_id is None):
        raise ValueError("before_detected_at and before_id must be provided together")
    page_limit = min(max(int(limit), 1), 100)
    where = ""
    params: tuple[object, ...] = ()
    if before_detected_at is not None and before_id is not None:
        where = " WHERE detected_at < ? OR (detected_at = ? AND id < ?)"
        params = (before_detected_at, before_detected_at, before_id)
    total_row = get_db().execute("SELECT COUNT(*) FROM capital_events").fetchone()
    total = int(total_row[0]) if total_row is not None else 0
    rows = (
        get_db()
        .execute(
            f"SELECT page.* FROM capital_events AS page{where} ORDER BY detected_at DESC, id DESC LIMIT ?",
            (*params, page_limit + 1),
        )
        .fetchall()
    )
    has_more = len(rows) > page_limit
    events = [dict(row) for row in rows[:page_limit]]
    last = events[-1] if has_more and events else None
    return {
        "events": events,
        "total": total,
        "has_more": has_more,
        "next_cursor": ({"detected_at": last["detected_at"], "id": last["id"]} if last is not None else None),
    }


def list_capital_events_between(start_at: str, end_at: str) -> list[dict]:
    """Return aggregate external cash flows inside one equity-snapshot window."""
    from aletheia.storage.repositories import _normalized_utc_datetime, get_db

    rows = (
        get_db()
        .execute(
            """
        SELECT *
        FROM capital_events
        WHERE datetime(detected_at) >= datetime(?)
          AND datetime(detected_at) <= datetime(?)
        """,
            (start_at, end_at),
        )
        .fetchall()
    )
    start = _normalized_utc_datetime(start_at)
    end = _normalized_utc_datetime(end_at)
    events = [dict(row) for row in rows if start < _normalized_utc_datetime(row["detected_at"]) <= end]
    return sorted(
        events,
        key=lambda row: (_normalized_utc_datetime(row["detected_at"]), row["id"]),
    )


def get_net_deposits() -> float:
    """Sum of all deposits minus withdrawals, excluding any historical non-finite rows."""
    from aletheia.storage.repositories import get_db

    db = get_db()
    rows = db.execute("SELECT id, event_type, amount_usdt FROM capital_events").fetchall()
    bad_ids = [r["id"] for r in rows if not math.isfinite(r["amount_usdt"])]
    if bad_ids:
        logging.getLogger(__name__).warning(
            "capital_events rows with non-finite amount_usdt excluded from net: %s", bad_ids
        )
    return sum(
        r["amount_usdt"] if r["event_type"] == "deposit" else -r["amount_usdt"]
        for r in rows
        if math.isfinite(r["amount_usdt"])
    )


def get_first_capital_event_time() -> str | None:
    """Earliest detected_at across all capital events (ISO string or None)."""
    from aletheia.storage.repositories import get_db

    db = get_db()
    row = db.execute("SELECT MIN(detected_at) AS t FROM capital_events").fetchone()
    return row["t"] if row and row["t"] else None


def list_prediction_runs(limit: int = 20) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    return _select_all("prediction_observer_runs", limit=limit)


def get_latest_prediction_run() -> dict | None:
    from aletheia.storage.repositories import _select_all

    rows = _select_all("prediction_observer_runs", order="started_at DESC", limit=1)
    return rows[0] if rows else None


def list_prediction_events(category: str | None = None, status: str = "active", limit: int = 50) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    parts = []
    params: list = []
    if status:
        parts.append("status = ?")
        params.append(status)
    if category:
        parts.append("category LIKE ?")
        params.append(f"%{category}%")
    where = " AND ".join(parts) if parts else None
    return _select_all("prediction_events", where=where, params=tuple(params), order="updated_at DESC", limit=limit)


def get_prediction_event(event_id: str) -> dict | None:
    from aletheia.storage.repositories import _select_one

    return _select_one("prediction_events", event_id)


def list_prediction_markets_for_event(event_id: str, limit: int = 50) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    return _select_all("prediction_markets", where="event_id = ?", params=(event_id,), limit=limit)


def get_prediction_market_by_id(market_id: str) -> dict | None:
    from aletheia.storage.repositories import _select_one

    return _select_one("prediction_markets", market_id)


def list_probability_snapshots(market_id: str, since: str | None = None, limit: int = 200) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    parts = ["market_id = ?"]
    params: list = [market_id]
    if since:
        parts.append("ts >= ?")
        params.append(since)
    where = " AND ".join(parts)
    snapshots = _select_all(
        "prediction_probability_snapshots",
        where=where,
        params=tuple(params),
        order="ts DESC, rowid DESC",
        limit=limit,
    )
    return list(reversed(snapshots))


def list_prediction_claims(
    market_id: str | None = None,
    claim_type: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    parts = []
    params: list = []
    if market_id:
        parts.append("market_id = ?")
        params.append(market_id)
    if claim_type:
        parts.append("claim_type = ?")
        params.append(claim_type)
    if since:
        parts.append("datetime(created_at) >= datetime(?)")
        params.append(since)
    where = " AND ".join(parts) if parts else None
    return _select_all(
        "prediction_market_claims", where=where, params=tuple(params), order="created_at DESC", limit=limit
    )


def list_market_snapshots(
    symbol: str | None = None,
    limit: int | None = 100,
    exchange: str = "",
    market_type: str | None = None,
    latest_per_symbol: bool = False,
) -> list[dict]:
    """Return market snapshots, optionally filtered by symbol, exchange, and/or market_type.

    ``market_type`` uses the snapshot-storage vocabulary ("spot"/"perp"/"futures"),
    which is the InstrumentId physical vocabulary — NOT the DB/execution string
    "usdt_futures". Callers holding a proposal/DB market_type must map it first
    (see ``market_data._snapshot_market_type``). None preserves prior behavior.
    """
    from aletheia.storage.repositories import _select_all, get_db

    conditions: list[str] = []
    params: list = []
    if symbol:
        conditions.append("symbol = ?")
        params.append(symbol)
    if exchange:
        conditions.append("exchange = ?")
        params.append(exchange)
    if market_type:
        conditions.append("market_type = ?")
        params.append(market_type)
    where = " AND ".join(conditions) if conditions else None
    if latest_per_symbol:
        where_sql = f"WHERE {where}" if where else ""
        sql = f"""
            WITH ranked AS (
                SELECT rowid AS snapshot_rowid,
                       ROW_NUMBER() OVER (
                           PARTITION BY symbol
                           ORDER BY COALESCE(snapshot_at, '') DESC, rowid DESC
                       ) AS snapshot_rank
                FROM market_snapshots
                {where_sql}
            )
            SELECT snapshots.*
            FROM market_snapshots AS snapshots
            JOIN ranked
              ON ranked.snapshot_rowid = snapshots.rowid
             AND ranked.snapshot_rank = 1
            ORDER BY COALESCE(snapshots.snapshot_at, '') DESC, snapshots.rowid DESC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, limit))
        return [dict(row) for row in get_db().execute(sql, tuple(params)).fetchall()]
    return _select_all(
        "market_snapshots",
        where=where,
        params=tuple(params),
        order="COALESCE(snapshot_at, '') DESC, rowid DESC",
        limit=limit,
    )


def latest_market_snapshot_groups() -> list[dict]:
    """Return the newest persisted row timestamp for each physical source group."""
    from aletheia.storage.repositories import get_db

    rows = get_db().execute(
        "SELECT exchange, market_type, MAX(snapshot_at) AS snapshot_at "
        "FROM market_snapshots GROUP BY exchange, market_type"
    ).fetchall()
    return [dict(row) for row in rows]


def list_portfolio_snapshots(limit: int | None = None, days: int | None = None) -> list[dict]:
    """Return the latest snapshot window in chronological order.

    When `days` is provided it is the authoritative range selector: all rows
    inside the window are returned so range selectors (7D/30D/90D/All) stay
    distinct even when the table grows beyond any cap. `limit` is an optional
    safety cap that takes the most-recent rows inside the window.

    Without `days`, `limit` guards against returning the entire table; it caps
    from the newest end of the full series (default 100 rows).
    """
    from aletheia.storage.repositories import _snapshot_cutoff_us, get_db, register_instant_us

    db = get_db()
    register_instant_us(db)
    if days is not None and days > 0:
        cutoff_us = _snapshot_cutoff_us(days)
        if limit and limit > 0:
            rows = db.execute(
                """
                SELECT * FROM (
                    SELECT *
                    FROM portfolio_snapshots
                    WHERE aletheia_instant_us(snapshot_at) >= ?
                    ORDER BY aletheia_instant_us(snapshot_at) DESC, id DESC
                    LIMIT ?
                )
                ORDER BY aletheia_instant_us(snapshot_at) ASC, id ASC
                """,
                (cutoff_us, limit),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT *
                FROM portfolio_snapshots
                WHERE aletheia_instant_us(snapshot_at) >= ?
                ORDER BY aletheia_instant_us(snapshot_at) ASC, id ASC
                """,
                (cutoff_us,),
            ).fetchall()
    else:
        cap = limit if (limit and limit > 0) else 100
        rows = db.execute(
            """
            SELECT *
            FROM (
                SELECT *
                FROM portfolio_snapshots
                ORDER BY aletheia_instant_us(snapshot_at) DESC, id DESC
                LIMIT ?
            )
            ORDER BY aletheia_instant_us(snapshot_at) ASC, id ASC
            """,
            (cap,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_account_equity_snapshots(
    execution_mode: str,
    limit: int | None = None,
    days: int | None = None,
) -> list[dict]:
    """Return one explicitly-account-valued snapshot series in ascending order.

    `portfolio_snapshots.total_value_usdt` is intentionally not used as the
    predicate: historical local-paper rows stored position notional there.  A
    missing `account_equity_usdt` therefore means *not an account reading*, not
    a zero balance.

    The `days` window is the authoritative range selector. Rows are returned in
    ascending time order spanning the WHOLE window. `limit` is an optional safety
    cap: when provided it takes the most-recent `limit` rows inside the window
    (via an inner DESC sub-select) so chart consumers see the latest data, not
    the oldest. The default is None (no cap) so all range selectors remain distinct.
    """
    from aletheia.storage.repositories import _snapshot_cutoff_us, get_db, register_instant_us

    db = get_db()
    register_instant_us(db)
    params: list[object] = [execution_mode]
    where = "execution_mode = ? AND account_equity_usdt IS NOT NULL"
    if days is not None and days > 0:
        where += " AND aletheia_instant_us(snapshot_at) >= ?"
        params.append(_snapshot_cutoff_us(days))
    if limit and limit > 0:
        rows = db.execute(
            f"""
            SELECT * FROM (
                SELECT * FROM portfolio_snapshots
                WHERE {where}
                ORDER BY aletheia_instant_us(snapshot_at) DESC, id DESC
                LIMIT ?
            )
            ORDER BY aletheia_instant_us(snapshot_at) ASC, id ASC
            """,
            (*params, limit),
        ).fetchall()
    else:
        rows = db.execute(
            f"""
            SELECT * FROM portfolio_snapshots
            WHERE {where}
            ORDER BY aletheia_instant_us(snapshot_at) ASC, id ASC
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def get_latest_portfolio_snapshot() -> dict | None:
    from aletheia.storage.repositories import _select_all

    rows = _select_all("portfolio_snapshots", order="snapshot_at DESC, id DESC", limit=1)
    return rows[0] if rows else None


def prune_market_snapshots(days: int = 30) -> int:
    from aletheia.storage.repositories import _delete_old_rows

    return _delete_old_rows("market_snapshots", days)


def prune_portfolio_snapshots(days: int = 90) -> int:
    from aletheia.storage.repositories import _delete_old_rows

    return _delete_old_rows("portfolio_snapshots", days)


def prune_ohlcv_backfill_state(days: int = 90) -> int:
    """Drop stale operational retries after an instrument has left the Universe."""
    from aletheia.storage.repositories import _delete_old_rows

    return _delete_old_rows("ohlcv_backfill_state", days)


def upsert_ohlcv_candles(
    symbol: str,
    timeframe: str,
    candles: list,  # list of Candle dataclass from aletheia.data.providers
    provider: str = "ccxt",
    exchange: str = "",
    market_type: str = "spot",
    window: int | None = None,
) -> None:
    """Insert or replace OHLCV bars, then prune to rolling window.

    window: explicit bar limit; None reads from config (default 50000).
    exchange + market_type are part of the primary key. Unknown legacy callers
    are isolated under `legacy_unknown` instead of impersonating a real venue.
    """
    from aletheia.storage.repositories import (
        _OHLCV_PRUNE_MARGIN,
        _ohlcv_window,
        datetime,
        execute_many_write,
        execute_write,
        get_db,
    )

    if not candles:
        return
    effective_exchange = (exchange or "legacy_unknown").lower()
    effective_market_type = market_type or "spot"
    effective_window = max(1, int(window if window is not None else _ohlcv_window()))
    db = get_db()
    was_in_tx = db.in_transaction
    mutated_at = datetime.now(timezone.utc).isoformat()
    execute_many_write(
        "INSERT INTO market_ohlcv "
        "(exchange, symbol, market_type, timeframe, ts, open, high, low, close, volume, provider, "
        "first_persisted_at, last_mutated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(exchange, symbol, market_type, timeframe, ts) DO UPDATE SET "
        "open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, "
        "volume=excluded.volume, provider=excluded.provider, "
        "first_persisted_at=COALESCE(market_ohlcv.first_persisted_at, excluded.first_persisted_at), "
        "last_mutated_at=excluded.last_mutated_at "
        "WHERE market_ohlcv.open IS NOT excluded.open "
        "OR market_ohlcv.high IS NOT excluded.high "
        "OR market_ohlcv.low IS NOT excluded.low "
        "OR market_ohlcv.close IS NOT excluded.close "
        "OR market_ohlcv.volume IS NOT excluded.volume "
        "OR market_ohlcv.provider IS NOT excluded.provider"
        " OR market_ohlcv.first_persisted_at IS NULL "
        "OR market_ohlcv.last_mutated_at IS NULL",
        [
            (
                effective_exchange,
                symbol,
                effective_market_type,
                timeframe,
                c.timestamp,
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume,
                provider,
                mutated_at,
                mutated_at,
            )
            for c in candles
        ],
    )
    scope = (effective_exchange, symbol, effective_market_type, timeframe)
    row_count = db.execute(
        "SELECT COUNT(*) FROM market_ohlcv WHERE exchange=? AND symbol=? AND market_type=? AND timeframe=?",
        scope,
    ).fetchone()[0]
    if row_count > effective_window + _OHLCV_PRUNE_MARGIN:
        cutoff_row = db.execute(
            "SELECT ts FROM market_ohlcv "
            "WHERE exchange=? AND symbol=? AND market_type=? AND timeframe=? "
            "ORDER BY ts DESC LIMIT 1 OFFSET ?",
            (*scope, effective_window - 1),
        ).fetchone()
        if cutoff_row is not None:
            execute_write(
                "DELETE FROM market_ohlcv WHERE exchange=? AND symbol=? AND market_type=? AND timeframe=? AND ts < ?",
                (*scope, cutoff_row[0]),
            )
    if not was_in_tx:
        db.commit()


def get_ohlcv_coverage(symbol: str, timeframe: str, exchange: str = "", market_type: str = "") -> dict:
    """Return coverage stats for a symbol+timeframe pair."""
    from aletheia.storage.repositories import _resolve_ohlcv_scope, get_db

    db = get_db()
    scope = _resolve_ohlcv_scope(db, symbol, timeframe, exchange, market_type)
    if scope is None:
        return {"bar_count": 0, "oldest_ts": None, "newest_ts": None}
    scoped_exchange, scoped_market_type = scope
    row = db.execute(
        "SELECT COUNT(*) as cnt, MIN(ts) as oldest_ts, MAX(ts) as newest_ts "
        "FROM market_ohlcv WHERE exchange=? AND symbol=? AND market_type=? AND timeframe=?",
        (scoped_exchange, symbol, scoped_market_type, timeframe),
    ).fetchone()
    return {
        "bar_count": row[0] or 0,
        "oldest_ts": row[1],
        "newest_ts": row[2],
    }


def get_ohlcv_newest_ts(symbol: str, timeframe: str, exchange: str = "", market_type: str = "") -> int | None:
    """Return the newest timestamp stored for a symbol+timeframe, or None."""
    from aletheia.storage.repositories import _resolve_ohlcv_scope, get_db

    db = get_db()
    scope = _resolve_ohlcv_scope(db, symbol, timeframe, exchange, market_type)
    if scope is None:
        return None
    scoped_exchange, scoped_market_type = scope
    row = db.execute(
        "SELECT MAX(ts) FROM market_ohlcv WHERE exchange=? AND symbol=? AND market_type=? AND timeframe=?",
        (scoped_exchange, symbol, scoped_market_type, timeframe),
    ).fetchone()
    return row[0] if row else None


def get_ohlcv_backfill_state(exchange: str, symbol: str, market_type: str, timeframe: str) -> dict | None:
    """Return durable retry state for one exact OHLCV instrument/timeframe."""
    from aletheia.storage.repositories import get_db

    db = get_db()
    row = db.execute(
        "SELECT * FROM ohlcv_backfill_state WHERE exchange=? AND symbol=? AND market_type=? AND timeframe=?",
        (exchange.lower(), symbol, market_type, timeframe),
    ).fetchone()
    return dict(row) if row else None


def upsert_ohlcv_backfill_state(
    *,
    exchange: str,
    symbol: str,
    market_type: str,
    timeframe: str,
    status: str,
    bar_count: int,
    consecutive_failures: int,
    last_attempt_at: str | None,
    next_attempt_at: str | None,
    last_error: str | None,
) -> None:
    """Persist one exact catch-up state without leaking a caller transaction."""
    from aletheia.storage.repositories import execute_write, get_db

    db = get_db()
    was_in_tx = db.in_transaction
    execute_write(
        "INSERT INTO ohlcv_backfill_state "
        "(exchange,symbol,market_type,timeframe,status,bar_count,consecutive_failures,"
        " last_attempt_at,next_attempt_at,last_error,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now')) "
        "ON CONFLICT(exchange,symbol,market_type,timeframe) DO UPDATE SET "
        "status=excluded.status, bar_count=excluded.bar_count, "
        "consecutive_failures=excluded.consecutive_failures, "
        "last_attempt_at=excluded.last_attempt_at, next_attempt_at=excluded.next_attempt_at, "
        "last_error=excluded.last_error, updated_at=datetime('now')",
        (
            exchange.lower(),
            symbol,
            market_type,
            timeframe,
            status,
            int(bar_count),
            int(consecutive_failures),
            last_attempt_at,
            next_attempt_at,
            last_error,
        ),
    )
    if not was_in_tx:
        db.commit()


def get_collector_health_state(domain: str) -> dict | None:
    """Return the latest bounded operational state for one collector domain."""
    from aletheia.storage.repositories import get_db

    db = get_db()
    row = db.execute("SELECT * FROM collector_health_state WHERE domain=?", (domain,)).fetchone()
    return dict(row) if row else None


def list_collector_health_states() -> list[dict]:
    """Return one latest-state row per collector domain."""
    from aletheia.storage.repositories import get_db

    db = get_db()
    rows = db.execute("SELECT * FROM collector_health_state ORDER BY domain").fetchall()
    return [dict(row) for row in rows]


def upsert_collector_health_state(state: dict) -> None:
    """Persist one bounded collector state using the canonical transaction pattern."""
    from aletheia.storage.repositories import execute_write, get_db

    columns = (
        "domain",
        "cadence_sec",
        "last_status",
        "last_tick",
        "last_scheduled_at",
        "last_attempt_at",
        "last_finished_at",
        "last_success_at",
        "duration_ms",
        "consecutive_problems",
        "attempts",
        "successes",
        "partial_results",
        "empty_results",
        "timeouts",
        "budget_skips",
        "failures",
        "last_error_code",
        "derivatives_coverage_json",
    )
    values = tuple(state.get(column) for column in columns)
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns if column != "domain")
    db = get_db()
    was_in_tx = db.in_transaction
    execute_write(
        f"INSERT INTO collector_health_state ({','.join(columns)},updated_at) "
        f"VALUES ({','.join('?' for _ in columns)},datetime('now')) "
        f"ON CONFLICT(domain) DO UPDATE SET {updates},updated_at=datetime('now')",
        values,
    )
    if not was_in_tx:
        db.commit()


def get_ohlcv_candles(
    symbol: str,
    timeframe: str,
    limit: int = 100,
    exchange: str = "",
    market_type: str = "",
    before_ts: int | None = None,
    *,
    db: sqlite3.Connection | None = None,
    include_provenance: bool = False,
) -> list[dict]:
    """Return up to `limit` most-recent OHLCV bars, oldest-first.

    When exchange/market_type are supplied, queries are scoped to that exact
    instrument. Unscoped legacy callers should be migrated by their owners.
    """
    from aletheia.storage.repositories import _resolve_ohlcv_scope, get_db

    db = db or get_db()
    scope = _resolve_ohlcv_scope(db, symbol, timeframe, exchange, market_type)
    if scope is None:
        return []
    scoped_exchange, scoped_market_type = scope
    before_clause = " AND ts < ?" if before_ts is not None else ""
    params: tuple = (scoped_exchange, symbol, scoped_market_type, timeframe)
    if before_ts is not None:
        params = (*params, int(before_ts))
    columns = {str(row[1]) for row in db.execute("PRAGMA table_info(market_ohlcv)").fetchall()}
    provenance_sql = ""
    if include_provenance:
        first_seen_sql = "first_persisted_at" if "first_persisted_at" in columns else "NULL"
        last_mutated_sql = "last_mutated_at" if "last_mutated_at" in columns else "NULL"
        provenance_sql = f", {first_seen_sql} AS first_persisted_at, {last_mutated_sql} AS last_mutated_at"
    rows = db.execute(
        "SELECT ts, open, high, low, close, volume, provider, exchange, market_type"
        + provenance_sql
        + " FROM market_ohlcv WHERE exchange=? AND symbol=? AND market_type=? AND timeframe=? "
        + before_clause
        + " ORDER BY ts DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    # Reverse so callers get oldest→newest
    return [dict(r) for r in reversed(rows)]


def latest_funding_rate(symbol: str, exchange: str | None = None) -> dict | None:
    from aletheia.storage.repositories import _latest_deriv_where, _select_all

    where_tail, extra = _latest_deriv_where(exchange)
    rows = _select_all(
        "funding_rate_snapshots",
        where=where_tail,
        params=(symbol, *extra),
        order="observed_at DESC",
        limit=1,
    )
    from aletheia.data.derivatives_facts import project_runtime_snapshot
    return project_runtime_snapshot(rows[0], 'funding') if rows else None


def insert_collector_snapshot_batch(
    *,
    orderbooks: list[dict] | None = None,
    funding: list[dict] | None = None,
    open_interest: list[dict] | None = None,
    liquidations: list[dict] | None = None,
) -> dict[str, int]:
    """Persist one collector tick in one short transaction.

    Funding keeps its existing duplicate-heartbeat guard. The guard still reads
    per instrument, but all accepted DML shares one commit instead of generating
    one WAL commit per snapshot.
    """
    from aletheia.storage.repositories import (
        CollectorBatchPersistError,
        get_db,
        insert_funding_rate_snapshot,
        insert_liquidation_event,
        insert_open_interest_snapshot,
        insert_orderbook_snapshot,
        transaction,
    )


    counts = {"orderbooks": 0, "funding": 0, "open_interest": 0}
    if liquidations is not None:
        counts["liquidations"] = 0
    if not orderbooks and not funding and not open_interest and not liquidations:
        return counts
    with transaction():
        try:
            for row in orderbooks or []:
                insert_orderbook_snapshot(dict(row))
                counts["orderbooks"] += 1
        except Exception as exc:
            raise CollectorBatchPersistError("orderbooks", exc) from exc
        try:
            before = get_db().total_changes
            for row in funding or []:
                insert_funding_rate_snapshot(dict(row))
            counts["funding"] = get_db().total_changes - before
        except Exception as exc:
            raise CollectorBatchPersistError("funding", exc) from exc
        try:
            for row in open_interest or []:
                insert_open_interest_snapshot(dict(row))
                counts["open_interest"] += 1
        except Exception as exc:
            raise CollectorBatchPersistError("open_interest", exc) from exc
        try:
            for row in liquidations or []:
                insert_liquidation_event(dict(row))
                counts["liquidations"] += 1
        except Exception as exc:
            raise CollectorBatchPersistError("liquidations", exc) from exc
    return counts


def latest_open_interest(symbol: str, exchange: str | None = None) -> dict | None:
    from aletheia.storage.repositories import _latest_deriv_where, _select_all

    where_tail, extra = _latest_deriv_where(exchange)
    rows = _select_all(
        "open_interest_snapshots",
        where=where_tail,
        params=(symbol, *extra),
        order="observed_at DESC",
        limit=1,
    )
    from aletheia.data.derivatives_facts import project_runtime_snapshot
    return project_runtime_snapshot(rows[0], 'open_interest') if rows else None


def open_interest_near(
    symbol: str,
    exchange: str,
    *,
    target_age_min: float = 60.0,
    band_min: tuple[float, float] = (45.0, 75.0),
    reference_at: str | datetime | None = None,
) -> dict | None:
    """Return the OI snapshot for ``symbol`` on ``exchange`` observed closest to
    ``target_age_min`` minutes ago, restricted to the ``band_min`` age window.

    The derivatives collector uses this to compute a TRUE ~1h open-interest
    delta instead of a delta against the immediately-preceding (~2 min)
    snapshot — a 2-min delta almost never clears the scanner's 1h-calibrated
    thresholds, so the derivatives family scored ~0 in steady state.

    ``reference_at`` defaults to wall-clock now; collectors pass their own
    observation time so replayed rows use the same window. ``exchange`` is
    REQUIRED and matched exactly. The open_interest_snapshots
    table is multi-venue with heterogeneous units (e.g. OKX stores contracts,
    Hyperliquid stores USD), so a venue-blind neighbour lookup mixes units and
    fabricates ±100%–2500% deltas. Returns None when no snapshot falls in the
    band (e.g. after a collector gap) — the caller MUST treat a missing
    neighbour as "no delta", never fall back to a stale or foreign-venue row.
    """
    from aletheia.storage.repositories import LEGACY_DERIVATIVES_WHERE, _select_all

    if reference_at is None:
        now = datetime.now(timezone.utc)
    elif isinstance(reference_at, datetime):
        now = reference_at
    else:
        try:
            now = datetime.fromisoformat(str(reference_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    lo, hi = band_min
    oldest = (now - timedelta(minutes=hi)).isoformat()
    newest = (now - timedelta(minutes=lo)).isoformat()
    rows = _select_all(
        "open_interest_snapshots",
        where=f"symbol = ? AND exchange = ? AND observed_at BETWEEN ? AND ? AND {LEGACY_DERIVATIVES_WHERE}",
        params=(symbol, exchange, oldest, newest),
        order="observed_at DESC",
        limit=64,
    )
    if not rows:
        return None
    target = now - timedelta(minutes=target_age_min)

    def _age_gap(row: dict) -> float:
        observed = row.get("observed_at")
        try:
            ts = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return float("inf")
        return abs((ts - target).total_seconds())

    return min(rows, key=_age_gap)


def recent_liquidations(symbol: str, hours: int = 1) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return _select_all(
        "liquidation_events",
        where="symbol = ? AND event_time > ?",
        params=(symbol, cutoff),
        order="event_time DESC",
        limit=200,
    )


def latest_macro_snapshot() -> dict | None:
    from aletheia.storage.repositories import _select_all

    from aletheia.data.freshness import fresh_macro_row

    rows = _select_all("macro_snapshots", order="observed_at DESC", limit=1)
    return fresh_macro_row(rows[0]) if rows else None


def macro_snapshot_near(
    reference_at: str | datetime,
    *,
    target_age_days: float = 7.0,
    tolerance_hours: float = 12.0,
) -> dict | None:
    """Return the valid market-cap row nearest the requested historical age."""
    from aletheia.storage.repositories import _select_all

    if isinstance(reference_at, datetime):
        reference = reference_at
    else:
        try:
            reference = datetime.fromisoformat(str(reference_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    target = reference - timedelta(days=target_age_days)
    oldest = (target - timedelta(hours=tolerance_hours)).isoformat()
    newest = (target + timedelta(hours=tolerance_hours)).isoformat()
    rows = _select_all(
        "macro_snapshots",
        where=("total_market_cap_usd IS NOT NULL AND total_market_cap_usd > 0 AND observed_at BETWEEN ? AND ?"),
        params=(oldest, newest),
        order="observed_at DESC",
        limit=64,
    )
    if not rows:
        return None

    def _gap(row: dict) -> float:
        try:
            observed = datetime.fromisoformat(str(row.get("observed_at")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return float("inf")
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return abs((observed - target).total_seconds())

    return min(rows, key=_gap)


def latest_coingecko_top(top_n: int = 50) -> list[dict]:
    """Return the most recent batch's top_n rows.

    New rows are pinned by batch_id so mixed provider source times cannot split
    one acquisition batch. Legacy rows fall back to the original observed_at pin.
    """
    from aletheia.storage.repositories import _select_all, get_db

    db = get_db()
    batch_row = db.execute(
        "SELECT batch_id FROM coingecko_top_snapshots "
        "WHERE batch_id IS NOT NULL AND batch_id != '' "
        "ORDER BY acquired_at DESC, observed_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    if batch_row and batch_row["batch_id"]:
        return _select_all(
            "coingecko_top_snapshots",
            where="batch_id = ?",
            params=(batch_row["batch_id"],),
            order="market_cap_rank ASC",
            limit=top_n,
        )
    latest_row = db.execute("SELECT MAX(observed_at) AS m FROM coingecko_top_snapshots").fetchone()
    if not latest_row or not latest_row["m"]:
        return []
    return _select_all(
        "coingecko_top_snapshots",
        where="observed_at = ?",
        params=(latest_row["m"],),
        order="market_cap_rank ASC",
        limit=top_n,
    )


def options_volatility_at(symbol: str, cutoff: str) -> dict:
    """Research-only as-of read. Never create/migrate a database on inspection."""
    from aletheia.storage.sqlite import readonly_db
    from aletheia.data.options_volatility import OptionsVolatilitySnapshot

    end = datetime.fromisoformat(cutoff)
    if end.tzinfo is None or symbol not in ("BTC", "ETH"):
        raise ValueError("BTC/ETH symbol and timezone-aware cutoff required")
    cutoff = end.astimezone(timezone.utc).isoformat()
    unavailable = {"available": False, "reason": "no_snapshot", "authority": "observation_only"}
    with readonly_db(allow_missing=True) as db:
        if db is None or not db.execute("SELECT 1 FROM sqlite_master WHERE name='options_volatility_snapshots'").fetchone():
            return unavailable
        row = db.execute(
            "SELECT id, snapshot_json FROM options_volatility_snapshots "
            "WHERE symbol=? AND acquired_at<=? ORDER BY acquired_at DESC,id DESC LIMIT 1",
            (symbol, cutoff),
        ).fetchone()
    if row is None:
        return unavailable
    snapshot = OptionsVolatilitySnapshot.model_validate_json(row["snapshot_json"])
    age = (end-datetime.fromisoformat(snapshot.acquired_at)).total_seconds()
    if not 0 <= age <= 1800:
        return {**unavailable, "reason": "stale_snapshot"}
    if any(datetime.fromisoformat(r["source_time"]) > end for r in snapshot.inputs):
        return {**unavailable, "reason": "future_source"}
    return {"available": True, "id": row["id"], "cutoff": cutoff, **snapshot.model_dump()}


def latest_onchain_snapshots(
    metric_type: str | None = None,
    source: str = "defillama",
    limit: int = 1,
) -> list[dict]:
    """Return the most recent onchain_snapshots rows.

    metric_type: filter to a specific type (stablecoin_mcap / chain_tvl / dex_volume).
                 None = return latest of all types (may be mixed).
    source: defaults to 'defillama'.
    limit: number of rows to return per (source, metric_type) pair.
    """
    from aletheia.storage.repositories import _select_all

    if metric_type:
        return _select_all(
            "onchain_snapshots",
            where="source = ? AND metric_type = ?",
            params=(source, metric_type),
            order="observed_at DESC",
            limit=limit,
        )
    return _select_all(
        "onchain_snapshots",
        where="source = ?",
        params=(source,),
        order="observed_at DESC",
        limit=limit * 3,  # up to 3 metric types × limit rows each
    )


def save_volume_ranking_snapshot(symbols: list, observed_at: str) -> None:
    from aletheia.storage.repositories import execute_write, get_db

    import json

    db = get_db()
    was_in_tx = db.in_transaction
    execute_write(
        "INSERT INTO volume_ranking_snapshots (symbols_json, observed_at) VALUES (?, ?)",
        (json.dumps(symbols), observed_at),
    )
    if not was_in_tx:
        db.commit()
    execute_write(
        "DELETE FROM volume_ranking_snapshots WHERE id NOT IN ("
        "  SELECT id FROM volume_ranking_snapshots ORDER BY observed_at DESC LIMIT 5"
        ")"
    )
    if not was_in_tx:
        db.commit()


def get_volume_ranking_snapshot() -> dict:
    from aletheia.storage.repositories import get_db

    import json

    db = get_db()
    row = db.execute(
        "SELECT symbols_json, observed_at FROM volume_ranking_snapshots ORDER BY observed_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {}
    return {"symbols": json.loads(row[0]), "observed_at": row[1]}


def prune_market_ohlcv(
    ttl_days: dict[str, int] | None = None,
) -> int:
    """Prune market_ohlcv per-timeframe (Phase 3) + drop legacy_unknown wholesale.

    ``ttl_days`` maps timeframe → retention days. Rows are stored with
    ``ts`` in unix milliseconds so the cutoff is converted to ms per-timeframe.
    Unknown timeframes are left untouched (defensive).

    Legacy rows with ``exchange='legacy_unknown'`` are hard-deleted entirely
    regardless of age — those were the fall-back for callers that never
    supplied a real venue and are of zero forensic value.
    """
    from aletheia.storage.repositories import _MARKET_OHLCV_TTL_DAYS_DEFAULT, _delete_market_ohlcv_batches

    ttl = dict(_MARKET_OHLCV_TTL_DAYS_DEFAULT)
    if ttl_days:
        ttl.update(ttl_days)
    now = datetime.now(timezone.utc)
    total = 0
    for timeframe, days in ttl.items():
        cutoff_ms = int((now - timedelta(days=days)).timestamp() * 1000)
        total += _delete_market_ohlcv_batches(
            "timeframe = ? AND ts < ?",
            (timeframe, cutoff_ms),
        )
    total += _delete_market_ohlcv_batches(
        "exchange = ?",
        ("legacy_unknown",),
    )
    return total


def get_latest_market_regime(*, now: datetime | None = None) -> dict | None:
    from aletheia.storage.repositories import _select_all, logger

    rows = _select_all("market_regimes", order="detected_at DESC", limit=1)
    if not rows:
        return None
    row = rows[0]
    if row.get("signals"):
        try:
            row["signals"] = json.loads(row["signals"])
        except (json.JSONDecodeError, TypeError):
            logger.warning("repositories: signals JSON parse failed for market regime", exc_info=True)
    from aletheia.data.freshness import assess_freshness

    verdict = assess_freshness(
        row,
        "regime",
        now=now,
        timestamp_field="detected_at",
    )
    row["freshness"] = verdict.state
    row["age_seconds"] = verdict.age_seconds
    if not verdict.usable:
        # Retain only provenance for diagnostics; stale regime values must not
        # remain usable by ranking, sizing, attribution, or Operator context.
        row["state"] = None
        row["confidence"] = None
        row["long_bias"] = None
        row["short_bias"] = None
    return row


def list_market_regimes(limit: int = 50) -> list[dict]:
    from aletheia.storage.repositories import _select_all, logger

    rows = _select_all("market_regimes", order="detected_at DESC", limit=limit)
    for row in rows:
        if row.get("signals"):
            try:
                row["signals"] = json.loads(row["signals"])
            except (json.JSONDecodeError, TypeError):
                logger.warning("repositories: signals JSON parse failed in list_market_regimes", exc_info=True)
    return rows
