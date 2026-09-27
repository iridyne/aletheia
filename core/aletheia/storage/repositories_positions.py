"""Position and fill persistence.

Shared repository helpers are imported inside functions to avoid the facade
re-export cycle and retain its transaction and connection owners.
"""

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from aletheia.storage.models import PositionDisappearanceObservation

__all__ = [
    "count_deferred_position_path_observation_buckets",
    "count_position_path_observations_since",
    "defer_position_path_observation_bucket",
    "enqueue_position_path_observation_buckets_through",
    "get_active_position_disappearance_candidate",
    "get_active_position_disappearance_candidate_for_position",
    "get_position",
    "get_position_path_bucket_state",
    "get_position_path_observer_cursor",
    "get_position_protection_state",
    "get_position_protection_trigger",
    "insert_position_disappearance_observation",
    "insert_position_path_observation",
    "list_all_open_positions",
    "list_exhausted_deferred_position_path_buckets",
    "list_fills",
    "list_oldest_deferred_position_path_bucket",
    "list_position_disappearance_candidates",
    "list_position_disappearance_observations",
    "list_position_path_observations",
    "list_positions",
    "resolve_deferred_position_path_observation",
    "update_closed_position_settlement",
    "update_open_position_exit_plan",
    "update_open_position_materialization",
    "update_open_position_observation",
    "valid_settlement_price_provenance_sql",
]


def list_fills(limit: int = 100, order_id: str | None = None) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    if order_id:
        return _select_all("fills", where="order_id = ?", params=(order_id,), limit=limit)
    return _select_all("fills", limit=limit)


def update_open_position_materialization(position_id: str, **fields: object) -> None:
    from aletheia.storage.repositories import (
        _POSITION_MATERIALIZATION_FIELDS,
        _update_owned_position_fields,
    )

    _update_owned_position_fields(
        position_id,
        fields,
        allowed=_POSITION_MATERIALIZATION_FIELDS,
        owner="materialization",
        required_status="open",
    )


def update_open_position_exit_plan(position_id: str, **fields: object) -> None:
    from aletheia.storage.repositories import (
        _POSITION_EXIT_PLAN_FIELDS,
        _update_owned_position_fields,
    )

    _update_owned_position_fields(
        position_id,
        fields,
        allowed=_POSITION_EXIT_PLAN_FIELDS,
        owner="exit_plan",
        required_status="open",
    )


def update_open_position_observation(position_id: str, **fields: object) -> None:
    from aletheia.storage.repositories import (
        _POSITION_OBSERVATION_FIELDS,
        _update_owned_position_fields,
    )

    _update_owned_position_fields(
        position_id,
        fields,
        allowed=_POSITION_OBSERVATION_FIELDS,
        owner="observation",
        required_status="open",
    )


def update_closed_position_settlement(position_id: str, **fields: object) -> None:
    from aletheia.storage.repositories import (
        _POSITION_SETTLEMENT_FIELDS,
        _update_owned_position_fields,
    )

    _update_owned_position_fields(
        position_id,
        fields,
        allowed=_POSITION_SETTLEMENT_FIELDS,
        owner="settlement",
        required_status="closed",
    )


def get_position(position_id: str) -> dict | None:
    """Return one Position by durable identity without list pagination."""
    from aletheia.storage.repositories import _select_one

    return _select_one("positions", position_id)


def list_positions(
    status: str | None = None,
    limit: int = 50,
    execution_mode: str | None = None,
) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    clauses: list[str] = []
    params: list[object] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if execution_mode is not None:
        clauses.append("execution_mode = ?")
        params.append(execution_mode)
    return _select_all(
        "positions",
        where=" AND ".join(clauses) if clauses else None,
        params=tuple(params),
        limit=limit,
    )


def list_all_open_positions() -> list[dict]:
    """Return the complete open-position working set for state/safety loops."""
    from aletheia.storage.repositories import get_db

    rows = get_db().execute("SELECT * FROM positions WHERE status='open' ORDER BY rowid DESC").fetchall()
    return [dict(row) for row in rows]


def _first_full_position_path_bucket_open_ms(opened_at: object) -> int | None:
    try:
        if isinstance(opened_at, datetime):
            parsed = opened_at
        else:
            raw = str(opened_at or "").strip()
            if not raw:
                return None
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        opened_ms = math.ceil(parsed.astimezone(timezone.utc).timestamp() * 1000)
    except (OSError, OverflowError, TypeError, ValueError):
        return None
    return ((opened_ms + 300_000 - 1) // 300_000) * 300_000


def get_position_path_observer_cursor() -> dict | None:
    """Return the durable bucket-enrollment cursor, if initialized."""
    from aletheia.storage.repositories import get_db


    row = (
        get_db()
        .execute(
            "SELECT stream_id,last_enqueued_bucket_open_ms,updated_at "
            "FROM position_path_observer_cursor "
            "WHERE stream_id='position_path_observer'"
        )
        .fetchone()
    )
    return dict(row) if row is not None else None


def enqueue_position_path_observation_buckets_through(
    target_bucket_open_ms: int,
    *,
    observed_at: str,
) -> dict[str, int]:
    """Enroll eligible Position/bucket pairs through one completed bucket."""
    from aletheia.storage.repositories import (
        _POSITION_PATH_EXECUTION_SCOPE_SQL,
        execute_write,
        get_db,
        iso_instant_us,
        register_instant_us,
        transaction,
    )


    target = int(target_bucket_open_ms)
    if target < 0 or target % 300_000:
        raise ValueError("target_bucket_open_ms must be a non-negative 5m boundary")

    with transaction(immediate=True):
        db = get_db()
        register_instant_us(db)
        cursor = db.execute(
            "SELECT last_enqueued_bucket_open_ms "
            "FROM position_path_observer_cursor "
            "WHERE stream_id='position_path_observer'"
        ).fetchone()
        if cursor is None:
            buckets = [target]
        else:
            last = int(cursor["last_enqueued_bucket_open_ms"])
            if target <= last:
                return {
                    "enqueued_buckets": 0,
                    "enqueued_position_buckets": 0,
                    "catchup_bucket_count": 0,
                }
            buckets = list(range(last + 300_000, target + 300_000, 300_000))

        inserted_position_buckets = 0
        for bucket_open_ms in buckets:
            bucket_open = datetime.fromtimestamp(bucket_open_ms / 1000, tz=timezone.utc).isoformat()
            bucket_end = datetime.fromtimestamp((bucket_open_ms + 300_000) / 1000, tz=timezone.utc).isoformat()
            positions = db.execute(
                "SELECT id,opened_at,status,closed_at FROM positions p "
                f"WHERE {_POSITION_PATH_EXECUTION_SCOPE_SQL} "
                "AND aletheia_instant_us(opened_at) <= ? "
                "AND (status='open' OR ("
                "status='closed' AND aletheia_instant_us(closed_at) >= ?)) "
                "ORDER BY rowid",
                (iso_instant_us(bucket_open), iso_instant_us(bucket_end)),
            ).fetchall()
            for position in positions:
                first_bucket_open_ms = _first_full_position_path_bucket_open_ms(position["opened_at"])
                if first_bucket_open_ms is None or bucket_open_ms < first_bucket_open_ms:
                    continue
                inserted = execute_write(
                    "INSERT INTO position_path_observation_deferred_buckets "
                    "(position_id,bucket_open_ms,first_deferred_at,last_attempt_at,"
                    "attempt_count,reason) "
                    "SELECT ?,?,?,?,?,? "
                    "WHERE NOT EXISTS ("
                    "SELECT 1 FROM position_path_observations "
                    "WHERE position_id=? AND bucket_open_ms=?"
                    ") AND NOT EXISTS ("
                    "SELECT 1 FROM position_path_observation_deferred_buckets "
                    "WHERE position_id=? AND bucket_open_ms=?"
                    ")",
                    (
                        str(position["id"]),
                        bucket_open_ms,
                        observed_at,
                        observed_at,
                        0,
                        "scheduled",
                        str(position["id"]),
                        bucket_open_ms,
                        str(position["id"]),
                        bucket_open_ms,
                    ),
                )
                inserted_position_buckets += max(0, int(inserted.rowcount))

        if cursor is None:
            execute_write(
                "INSERT INTO position_path_observer_cursor "
                "(stream_id,last_enqueued_bucket_open_ms,updated_at) "
                "VALUES ('position_path_observer',?,?)",
                (target, observed_at),
            )
        else:
            execute_write(
                "UPDATE position_path_observer_cursor "
                "SET last_enqueued_bucket_open_ms=?,updated_at=? "
                "WHERE stream_id='position_path_observer'",
                (target, observed_at),
            )
        return {
            "enqueued_buckets": len(buckets),
            "enqueued_position_buckets": inserted_position_buckets,
            "catchup_bucket_count": max(0, len(buckets) - 1),
        }


def get_position_path_bucket_state(
    position_ids: Sequence[str],
) -> dict[str, dict[str, set[int]]]:
    """Return materialized and deferred bucket identities for each Position."""
    from aletheia.storage.repositories import get_db


    state = {str(position_id): {"observed": set(), "deferred": set()} for position_id in position_ids}
    for offset in range(0, len(position_ids), 400):
        chunk = position_ids[offset : offset + 400]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        for table, key in (
            ("position_path_observations", "observed"),
            ("position_path_observation_deferred_buckets", "deferred"),
        ):
            rows = (
                get_db()
                .execute(
                    f"SELECT position_id,bucket_open_ms FROM {table} WHERE position_id IN ({placeholders})",
                    chunk,
                )
                .fetchall()
            )
            for row in rows:
                position_state = state.get(str(row["position_id"]))
                if position_state is not None:
                    position_state[key].add(int(row["bucket_open_ms"]))
    return state


def defer_position_path_observation_bucket(
    position_id: str,
    *,
    bucket_open_ms: int,
    attempted_at: str,
    reason: str,
) -> int:
    """Durably schedule or revisit one Position/bucket and return retry attempts."""
    from aletheia.storage.repositories import (
        execute_write,
        get_db,
    )


    db = get_db()
    was_in_tx = db.in_transaction
    is_attempt = reason != "scheduled"
    execute_write(
        "INSERT INTO position_path_observation_deferred_buckets "
        "(position_id,bucket_open_ms,first_deferred_at,last_attempt_at,attempt_count,reason) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(position_id,bucket_open_ms) DO UPDATE SET "
        "last_attempt_at=excluded.last_attempt_at,"
        "attempt_count=position_path_observation_deferred_buckets.attempt_count+excluded.attempt_count,"
        "reason=excluded.reason",
        (
            position_id,
            bucket_open_ms,
            attempted_at,
            attempted_at,
            1 if is_attempt else 0,
            reason,
        ),
    )
    row = db.execute(
        "SELECT attempt_count FROM position_path_observation_deferred_buckets WHERE position_id=? AND bucket_open_ms=?",
        (position_id, bucket_open_ms),
    ).fetchone()
    if not was_in_tx:
        db.commit()
    return int(row["attempt_count"]) if row is not None else 0


def list_exhausted_deferred_position_path_buckets(*, limit: int = 2_000) -> list[dict]:
    """Return retry rows that must be materialized as terminal unavailable facts."""
    from aletheia.storage.repositories import (
        POSITION_PATH_MAX_RETRY_ATTEMPTS,
        _POSITION_PATH_EXECUTION_SCOPE_SQL,
        get_db,
    )


    bounded_limit = max(1, min(int(limit), 2_000))
    rows = (
        get_db()
        .execute(
            "SELECT d.* FROM position_path_observation_deferred_buckets d "
            "JOIN positions p ON p.id=d.position_id "
            f"WHERE {_POSITION_PATH_EXECUTION_SCOPE_SQL} AND d.attempt_count >= ? "
            "ORDER BY d.bucket_open_ms, d.position_id LIMIT ?",
            (POSITION_PATH_MAX_RETRY_ATTEMPTS, bounded_limit),
        )
        .fetchall()
    )
    return [dict(row) for row in rows]


def list_oldest_deferred_position_path_bucket(*, limit: int = 2_000) -> list[dict]:
    """Return the oldest due bucket, at most one queued bucket per Position.

    Open Positions are selected before closed-history recovery. The observer's
    first cursor does not backfill history, but recovery still enrolls the
    oldest missing bucket for every closed row. Without this live preference a
    months-old closed book starves the current open book: one global
    ``MIN(bucket_open_ms)`` stays on pre-observer closes and never reaches
    currently open Positions.
    """
    from aletheia.storage.repositories import (
        POSITION_PATH_MAX_RETRY_ATTEMPTS,
        _POSITION_PATH_EXECUTION_SCOPE_SQL,
        get_db,
    )


    bounded_limit = max(1, min(int(limit), 2_000))
    due_sql = (
        "SELECT d.* FROM position_path_observation_deferred_buckets d "
        "JOIN positions p ON p.id=d.position_id "
        f"WHERE {_POSITION_PATH_EXECUTION_SCOPE_SQL} AND d.attempt_count < ? "
        "AND NOT EXISTS ("
        "SELECT 1 FROM position_path_observation_deferred_buckets earlier "
        "WHERE earlier.position_id=d.position_id "
        "AND earlier.bucket_open_ms < d.bucket_open_ms"
        ")"
    )
    live_rows = (
        get_db()
        .execute(
            f"WITH due AS ({due_sql}) "
            "SELECT due.* FROM due "
            "INNER JOIN positions p ON p.id=due.position_id "
            "WHERE p.status='open' "
            "AND due.bucket_open_ms=("
            "SELECT MIN(due2.bucket_open_ms) FROM due due2 "
            "INNER JOIN positions p2 ON p2.id=due2.position_id "
            "WHERE p2.status='open'"
            ") "
            "ORDER BY due.position_id LIMIT ?",
            (POSITION_PATH_MAX_RETRY_ATTEMPTS, bounded_limit),
        )
        .fetchall()
    )
    if live_rows:
        return [dict(row) for row in live_rows]
    rows = (
        get_db()
        .execute(
            f"WITH due AS ({due_sql}) SELECT * FROM due ORDER BY bucket_open_ms, position_id LIMIT ?",
            (POSITION_PATH_MAX_RETRY_ATTEMPTS, bounded_limit),
        )
        .fetchall()
    )
    return [dict(row) for row in rows]


def count_deferred_position_path_observation_buckets() -> int:
    """Return the active external Position/bucket retry queue size."""
    from aletheia.storage.repositories import (
        _POSITION_PATH_EXECUTION_SCOPE_SQL,
        get_db,
    )


    row = get_db().execute(
        "SELECT COUNT(*) AS count FROM position_path_observation_deferred_buckets d "
        "JOIN positions p ON p.id=d.position_id "
        f"WHERE {_POSITION_PATH_EXECUTION_SCOPE_SQL}"
    ).fetchone()
    return int(row["count"]) if row else 0


def count_position_path_observations_since(since_bucket_open_ms: int) -> dict[str, int]:
    """Count external-account observations in the trailing window."""
    from aletheia.storage.repositories import (
        _POSITION_PATH_EXECUTION_SCOPE_SQL,
        get_db,
    )


    rows = (
        get_db()
        .execute(
            "SELECT o.evidence_status, COUNT(*) AS count "
            "FROM position_path_observations o JOIN positions p ON p.id=o.position_id "
            f"WHERE {_POSITION_PATH_EXECUTION_SCOPE_SQL} AND o.bucket_open_ms >= ? "
            "GROUP BY o.evidence_status",
            (int(since_bucket_open_ms),),
        )
        .fetchall()
    )
    counts = {"available": 0, "unavailable": 0}
    for row in rows:
        status = str(row["evidence_status"])
        if status in counts:
            counts[status] = int(row["count"])
    return counts


def resolve_deferred_position_path_observation(position_id: str, *, bucket_open_ms: int) -> None:
    """Remove one retry only after it became an immutable fact or ineligible."""
    from aletheia.storage.repositories import (
        execute_write,
        get_db,
    )


    db = get_db()
    was_in_tx = db.in_transaction
    execute_write(
        "DELETE FROM position_path_observation_deferred_buckets WHERE position_id=? AND bucket_open_ms=?",
        (position_id, bucket_open_ms),
    )
    if not was_in_tx:
        db.commit()


def insert_position_path_observation(observation: Mapping[str, object]) -> bool:
    """Insert one immutable Position/five-minute bucket, idempotently."""
    from aletheia.storage.repositories import (
        execute_write,
        get_db,
    )


    data = dict(observation)
    columns = (
        "position_id",
        "bucket_open_ms",
        "observed_at",
        "venue",
        "symbol",
        "market_type",
        "direction",
        "entry_price",
        "quantity",
        "bar_open",
        "bar_high",
        "bar_low",
        "bar_close",
        "bar_volume",
        "bar_provider",
        "mark_price",
        "unrealized_pnl",
        "entry_fee",
        "funding_cost",
        "funding_source",
        "stop_price",
        "take_profit_price",
        "exit_plan_version",
        "evidence_status",
        "evidence_reason",
        "producer_commit",
        "runtime_session_id",
        "capture_mode",
    )
    missing = [column for column in columns if column not in data]
    if missing:
        raise ValueError("position path observation is missing columns: " + ", ".join(missing))
    db = get_db()
    was_in_tx = db.in_transaction
    cursor = execute_write(
        f"INSERT INTO position_path_observations ({','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in columns)}) "
        "ON CONFLICT(position_id, bucket_open_ms) DO NOTHING",
        tuple(data[column] for column in columns),
    )
    if not was_in_tx:
        db.commit()
    return cursor.rowcount == 1


def list_position_path_observations(
    position_id: str,
    *,
    limit: int = 10_000,
) -> list[dict]:
    """Return one Position's immutable observations oldest-first."""
    from aletheia.storage.repositories import get_db


    bounded_limit = max(1, min(int(limit), 10_000))
    rows = (
        get_db()
        .execute(
            "SELECT * FROM position_path_observations WHERE position_id=? ORDER BY bucket_open_ms ASC LIMIT ?",
            (position_id, bounded_limit),
        )
        .fetchall()
    )
    return [dict(row) for row in rows]


def get_position_protection_state(position_id: str) -> dict | None:
    from aletheia.storage.repositories import get_db

    row = (
        get_db()
        .execute(
            "SELECT * FROM position_protection_state WHERE position_id=?",
            (position_id,),
        )
        .fetchone()
    )
    return dict(row) if row is not None else None


def get_position_protection_trigger(position_id: str) -> dict | None:
    """Return the immutable first-trigger fact for one protected Position."""
    from aletheia.storage.repositories import get_db


    row = (
        get_db()
        .execute(
            "SELECT * FROM position_protection_triggers WHERE position_id=?",
            (position_id,),
        )
        .fetchone()
    )
    return dict(row) if row is not None else None


def get_active_position_disappearance_candidate(identity_key: str) -> dict | None:
    from aletheia.storage.repositories import get_db

    db = get_db()
    row = db.execute(
        "SELECT * FROM position_disappearance_candidates WHERE identity_key=? AND status='pending' LIMIT 1",
        (identity_key,),
    ).fetchone()
    return dict(row) if row else None


def get_active_position_disappearance_candidate_for_position(
    position_id: str,
) -> dict | None:
    from aletheia.storage.repositories import get_db

    db = get_db()
    row = db.execute(
        "SELECT * FROM position_disappearance_candidates "
        "WHERE position_id=? AND status='pending' ORDER BY updated_at DESC LIMIT 1",
        (position_id,),
    ).fetchone()
    return dict(row) if row else None


def list_position_disappearance_candidates(*, status: str | None = None, limit: int = 100) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    if status:
        return _select_all(
            "position_disappearance_candidates",
            where="status = ?",
            params=(status,),
            order="updated_at DESC",
            limit=limit,
        )
    return _select_all("position_disappearance_candidates", order="updated_at DESC", limit=limit)


def insert_position_disappearance_observation(
    observation: PositionDisappearanceObservation,
) -> bool:
    """Insert one immutable observation; return False for an exact retry."""
    from aletheia.storage.repositories import (
        execute_write,
        get_db,
    )

    db = get_db()
    was_in_tx = db.in_transaction
    data = observation.model_dump()
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    cur = execute_write(
        f"INSERT OR IGNORE INTO position_disappearance_observations ({cols}) VALUES ({placeholders})",
        list(data.values()),
    )
    if not was_in_tx:
        db.commit()
    return cur.rowcount == 1


def list_position_disappearance_observations(candidate_id: str, *, limit: int = 100) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    return _select_all(
        "position_disappearance_observations",
        where="candidate_id = ?",
        params=(candidate_id,),
        order="observed_at ASC",
        limit=limit,
    )


def valid_settlement_price_provenance_sql(table_alias: str = "") -> str:
    """Return the SQLite equivalent of persisted price-provenance validation."""
    prefix = f"{table_alias}." if table_alias else ""
    return (
        f"typeof({prefix}horizon) = 'text' "
        f"AND {prefix}horizon IN ('15m', '1h', '4h', '24h') AND "
        f"typeof({prefix}settlement_price_source) = 'text' "
        f"AND typeof({prefix}settlement_lag_sec) IN ('integer', 'real') "
        f"AND {prefix}settlement_lag_sec >= 0 "
        f"AND {prefix}settlement_lag_sec < 1e999 "
        f"AND ({prefix}settlement_price_source IN "
        "('horizon_ohlcv', 'horizon_ohlcv_local') "
        f"OR ({prefix}settlement_price_source = 'near_due_ticker' "
        f"AND {prefix}settlement_lag_sec <= 0.1 * CASE {prefix}horizon "
        "WHEN '15m' THEN 900 WHEN '1h' THEN 3600 "
        "WHEN '4h' THEN 14400 WHEN '24h' THEN 86400 END)) "
    )
