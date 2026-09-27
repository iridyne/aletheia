"""Thin repository functions for SQLite CRUD."""

import hashlib
import json
import logging
import math
import os
import sqlite3
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from typing import Literal, cast

from pydantic import BaseModel, ValidationError

from aletheia.config.exchanges import EXCHANGE_UNIFICATION_MAP, LIVE_EXECUTION_MODES
from aletheia.data.symbols import to_canonical
from aletheia.storage.models import (
    AgentRun,
    AgentRunArtifact,
    AgentRunStatus,
    AuditEvent,
    Fill,
    KnowledgeItem,
    Opportunity,
    Order,
    OrderStatus,
    OutcomeHookJob,
    Position,
    PSLesson,
    PositionDisappearanceCandidate,
    PositionDisappearanceObservation,
    ReconciliationIntegrityIncident,
    RiskDecision,
    RuntimeSession,
    SerenityEvidence,
    SerenityOpportunity,
    SerenityPostMortem,
    SerenityReplaySnapshot,
    SerenityRiskGate,
    SerenityTheme,
    StrategyRecord,
    TradeProposal,
    decode_strategy_parameters,
)
from aletheia.storage.sqlite import (
    SQLITE_PYTHON_STRIP_CHARS_SQL,
    acquire_write_lock,
    execute_many_write,
    execute_write,
    get_db,
    iso_instant_us,
    readonly_db,
    register_instant_us,
    release_write_lock,
)

from aletheia.storage.repositories_positions import *  # noqa: F403
from aletheia.storage.repositories_positions import (
    _first_full_position_path_bucket_open_ms as _first_full_position_path_bucket_open_ms,
    get_position,
    valid_settlement_price_provenance_sql,
)

from aletheia.storage.repositories_universe import *  # noqa: F403
from aletheia.storage.repositories_universe import (
    _assert_twin_outcomes_match as _assert_twin_outcomes_match,
    _benchmark_payload as _benchmark_payload,
    _candidate_attention_valid as _candidate_attention_valid,
    _candidate_selection_retention_state as _candidate_selection_retention_state,
    _effective_candidate_attention as _effective_candidate_attention,
    _filter_outcome_insert_payload as _filter_outcome_insert_payload,
    _filter_outcome_loss_event as _filter_outcome_loss_event,
    _forward_outcome_insert_payload as _forward_outcome_insert_payload,
    _project_candidate_admission as _project_candidate_admission,
    _protected_universe_outcome_ids as _protected_universe_outcome_ids,
    _shadow_candidate_identity_sql as _shadow_candidate_identity_sql,
    _universe_twin_shadow_capture_due_from_latest as _universe_twin_shadow_capture_due_from_latest,
    _validate_universe_twin_prospective_bundle as _validate_universe_twin_prospective_bundle,
    get_candidate_planning_generation as get_candidate_planning_generation,
    get_universe_member as get_universe_member,
    insert_filter_candidate_outcomes_batch as insert_filter_candidate_outcomes_batch,
    list_universe_members as list_universe_members,
    settle_linked_shadow_outcomes as settle_linked_shadow_outcomes,
)

_PROVENANCE_FIELDS = ("runtime_session_id", "producer_commit", "behavior_fingerprint")


def list_lessons(status: str | None = None, limit: int = 100, *, oldest_first: bool = False) -> list[PSLesson]:
    """Read persisted rules independently of the optional lesson authoring surface."""
    db = get_db()
    direction = "ASC" if oldest_first else "DESC"
    if status:
        rows = db.execute(
            f"SELECT * FROM ps_lessons WHERE status = ? ORDER BY created_at {direction} LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = db.execute(
            f"SELECT * FROM ps_lessons ORDER BY created_at {direction} LIMIT ?", (limit,)
        ).fetchall()
    return [PSLesson(**dict(row)) for row in rows]


_CANDIDATE_SELECTION_RECORD_VERSIONS = frozenset({
    "candidate-selection-utility-record/v1",
    "candidate-selection-utility-record/v2",
    "candidate-selection-utility-record/v3",
    "candidate-selection-utility-record/v4",
})
_sqlite_logger = logging.getLogger("aletheia.storage.sqlite")
_retention_metrics_lock = threading.Lock()
_retention_metrics: dict[str, dict[str, float | int]] = {}


_lab_authority: Callable[[], bool] | None = None
_lab_retention_floor: Callable[[str], str | None] | None = None
_lab_retention_refs: Callable[[str], tuple[str, ...]] | None = None
_lab_priority_keys: Callable[..., tuple[set[str], set[str]]] | None = None
_lab_active_benchmark: Callable[[], dict | None] | None = None
_lab_outcome_guard: Callable[[str], AbstractContextManager[bool]] | None = None
_lab_live_window_ids: Callable[[], frozenset[str]] | None = None


def configure_observation_readers(
    *, authority=None, retention_floor=None, retention_refs=None,
    priority_keys=None, active_benchmark=None, outcome_guard=None,
    live_window_ids=None,
) -> None:
    """Compose optional Lab operations without querying runtime state."""
    global _lab_authority, _lab_retention_floor, _lab_retention_refs
    global _lab_priority_keys, _lab_active_benchmark, _lab_outcome_guard
    global _lab_live_window_ids
    _lab_authority = authority
    _lab_retention_floor = retention_floor
    _lab_retention_refs = retention_refs
    _lab_priority_keys = priority_keys
    _lab_active_benchmark = active_benchmark
    _lab_outcome_guard = outcome_guard
    _lab_live_window_ids = live_window_ids


def _canonical_lab_authority() -> bool:
    if _lab_authority is None:
        raise ExperimentStateReadError("Observation reader unavailable")
    return _lab_authority()

_PHYSICAL_PROTECTION_MARKETS = {
    "spot": "spot",
    "perp": "perp",
    "usdt_futures": "perp",
}

POSITION_PATH_MAX_RETRY_ATTEMPTS = 3
_POSITION_PATH_EXECUTION_SCOPE_SQL = "p.execution_mode IN (" + ",".join(
    f"'{mode}'" for mode in (
        *(demo_mode for _, demo_mode in EXCHANGE_UNIFICATION_MAP.values()),
        *LIVE_EXECUTION_MODES.values(),
    )
) + ")"


class PositionProtectionConflict(RuntimeError):
    """A durable protection write conflicts with already-observed authority."""


def _required_protection_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PositionProtectionConflict(f"{field} is required")
    return text


def _strict_physical_protection_market(value: object) -> str:
    normalized = str(value or "").strip().lower()
    physical = _PHYSICAL_PROTECTION_MARKETS.get(normalized)
    if physical is None:
        raise PositionProtectionConflict(f"unknown market_type: {normalized or 'missing'}")
    return physical


def _strict_canonical_protection_symbol(value: object) -> str:
    raw = _required_protection_text(value, "symbol")
    canonical = to_canonical(raw)
    base, separator, quote = canonical.partition("/")
    if raw != canonical or separator != "/" or not base or not quote or "/" in quote:
        raise PositionProtectionConflict("symbol must be canonical BASE/QUOTE")
    return canonical


def _finite_protection_number(
    value: object,
    field: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PositionProtectionConflict(f"{field} must be numeric") from exc
    if not math.isfinite(parsed):
        raise PositionProtectionConflict(f"{field} must be finite")
    if positive and parsed <= 0:
        raise PositionProtectionConflict(f"{field} must be positive")
    if nonnegative and parsed < 0:
        raise PositionProtectionConflict(f"{field} must be nonnegative")
    return parsed


def hash_position_exit_plan_json(plan_json: str) -> str:
    """Return the SHA-256 of one canonical JSON ExitPlan payload."""

    try:
        payload = json.loads(plan_json)
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PositionProtectionConflict("plan_json must be finite valid JSON") from exc
    if not isinstance(payload, dict):
        raise PositionProtectionConflict("plan_json must be a JSON object")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_position_protection_bar(
    *,
    venue: str,
    symbol: str,
    market_type: str,
    timeframe: str,
    bar_open_ms: int,
    bar_close_ms: int,
    bar_open: float,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    bar_volume: float,
    provider: str,
) -> str:
    """Hash one exact provider/instrument/1m OHLCV fact."""

    payload = {
        "venue": _required_protection_text(venue, "venue").lower(),
        "symbol": _strict_canonical_protection_symbol(symbol),
        "market_type": _strict_physical_protection_market(market_type),
        "timeframe": _required_protection_text(timeframe, "timeframe"),
        "bar_open_ms": int(bar_open_ms),
        "bar_close_ms": int(bar_close_ms),
        "bar_open": _finite_protection_number(bar_open, "bar_open", positive=True),
        "bar_high": _finite_protection_number(bar_high, "bar_high", positive=True),
        "bar_low": _finite_protection_number(bar_low, "bar_low", positive=True),
        "bar_close": _finite_protection_number(bar_close, "bar_close", positive=True),
        "bar_volume": _finite_protection_number(
            bar_volume,
            "bar_volume",
            nonnegative=True,
        ),
        "provider": _required_protection_text(provider, "provider"),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _record_retention_batch(table: str, deleted: int, duration_ms: float) -> None:
    with _retention_metrics_lock:
        metrics = _retention_metrics.setdefault(
            table,
            {"batches": 0, "deleted": 0, "total_ms": 0.0, "max_batch_ms": 0.0},
        )
        metrics["batches"] += 1
        metrics["deleted"] += deleted
        metrics["total_ms"] = round(float(metrics["total_ms"]) + duration_ms, 3)
        metrics["max_batch_ms"] = round(
            max(float(metrics["max_batch_ms"]), duration_ms),
            3,
        )


def _to_payload_dict(value: object) -> dict:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"Unsupported payload type: {type(value)!r}")


def _stamp_provenance(data: dict) -> dict:
    """Fill Runtime Session provenance on a core-entity row at insert time.

    Only fills a provenance field that EXISTS in the row and is still None, so an
    explicit caller value always wins and a row with no active session stays NULL
    (legacy, never fabricated). Lazy import avoids a repositories↔runtime_session
    import cycle. Best-effort — a provenance failure must not block a real insert.
    """
    try:
        if not any(f in data and data.get(f) is None for f in _PROVENANCE_FIELDS):
            return data
        from aletheia.agent_runtime.runtime_session import current_provenance

        prov = current_provenance()
        for f in _PROVENANCE_FIELDS:
            if f in data and data.get(f) is None and prov.get(f) is not None:
                data[f] = prov[f]
    except Exception:
        logger.warning("repositories: provenance fill failed for row", exc_info=True)
    return data


def _insert(table: str, data: dict, *, ignore_existing_id: bool = False) -> None:
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    execute_write(
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        + (" ON CONFLICT(id) DO NOTHING" if ignore_existing_id else ""),
        list(data.values()),
    )


def _insert_many(table: str, rows: list[dict]) -> int:
    """Insert homogeneous rows with one executemany call in the current transaction."""

    if not rows:
        return 0
    columns = list(rows[0])
    if any(list(row) != columns for row in rows):
        raise ValueError(f"{table} batch rows must have identical ordered columns")
    placeholders = ", ".join(["?"] * len(columns))
    values = [[row[column] for column in columns] for row in rows]

    execute_many_write(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    return len(rows)


def _select_all(
    table: str,
    where: str | None = None,
    params: tuple = (),
    order: str = "rowid DESC",
    limit: int | None = 100,
) -> list[dict]:
    db = get_db()
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    sql += f" ORDER BY {order}"
    if limit is not None:
        sql += f" LIMIT {max(1, limit)}"
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _select_one(table: str, id_val: str) -> dict | None:
    db = get_db()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (id_val,)).fetchone()
    return dict(row) if row else None


def _update(table: str, id_val: str, data: dict):
    sets = ", ".join(f"{k} = ?" for k in data.keys())
    return execute_write(
        f"UPDATE {table} SET {sets} WHERE id = ?",
        [*data.values(), id_val],
    )


def _update_pending(table: str, id_val: str, data: dict) -> bool:
    sets = ", ".join(f"{k} = ?" for k in data.keys())
    terminal_guard = ""
    params: list = [*data.values(), id_val]
    if table == "filter_candidate_outcomes":
        row = get_db().execute(
            "SELECT benchmark_window_id FROM filter_candidate_outcomes WHERE id=?", (id_val,)
        ).fetchone()
        if row and row["benchmark_window_id"]:
            terminal_guard = (
                " AND (NOT EXISTS (SELECT 1 FROM universe_discovery_benchmarks WHERE id=?) "
                "OR EXISTS (SELECT 1 FROM universe_discovery_benchmarks WHERE id=? AND status='active'))"
            )
            params.extend([row["benchmark_window_id"], row["benchmark_window_id"]])
    result = execute_write(
        f"UPDATE {table} SET {sets} WHERE id = ? AND status = 'pending'{terminal_guard}",
        params,
    )
    return bool(result.rowcount == 1)


@contextmanager
def transaction(*, immediate: bool = False, savepoint: bool = False):
    db = get_db()
    if db.in_transaction:
        if savepoint:
            nested = f"repo_tx_{uuid.uuid4().hex}"
            db.execute(f"SAVEPOINT {nested}")
            try:
                yield
                db.execute(f"RELEASE SAVEPOINT {nested}")
            except Exception:
                db.execute(f"ROLLBACK TO SAVEPOINT {nested}")
                db.execute(f"RELEASE SAVEPOINT {nested}")
                raise
            return
        yield
        return
    # Acquire the process-wide write lock before issuing BEGIN.  This serializes
    # all write transactions at the Python level so that cross-thread "database is
    # locked" OperationalErrors cannot occur during normal operation.  Any timeout
    # here means a caller is holding a write transaction open across I/O — a bug,
    # not a concurrency problem.
    frame = traceback.extract_stack(limit=3)[0]
    origin = f"{frame.filename}:{frame.lineno}:{frame.name}"
    wait_ms = acquire_write_lock()
    acquired_at = time.perf_counter()
    outcome = "begin_failed"
    try:
        # BEGIN IMMEDIATE is reserved for short compare-and-write claims.  It
        # acquires SQLite's writer slot before the read so concurrent workers cannot
        # both see an absent row and then perform the same external side effect.
        db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        outcome = "committed"
        try:
            yield
            db.commit()
        except Exception:
            outcome = "rolled_back"
            db.rollback()
            raise
    finally:
        held_ms = (time.perf_counter() - acquired_at) * 1000.0
        release_write_lock()
        verbose = os.environ.get("ALETHEIA_SQLITE_TIMING", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        log_args = (wait_ms, held_ms, immediate, outcome, origin)
        if wait_ms >= 100.0 or held_ms >= 500.0:
            _sqlite_logger.warning(
                "sqlite_write_transaction_slow wait_ms=%.1f held_ms=%.1f immediate=%s outcome=%s origin=%s",
                *log_args,
            )
        elif verbose:
            _sqlite_logger.info(
                "sqlite_write_transaction wait_ms=%.1f held_ms=%.1f immediate=%s outcome=%s origin=%s",
                *log_args,
            )


# --- Runtime Sessions (one row per Gateway process lifecycle) ---


def insert_runtime_session(session: RuntimeSession) -> None:
    _insert("runtime_sessions", session.model_dump())


def update_runtime_session(session_id: str, **kwargs) -> None:
    db = get_db()
    sets = ", ".join(f"{k} = ?" for k in kwargs.keys())
    execute_write(
        f"UPDATE runtime_sessions SET {sets} WHERE session_id = ?",
        [*kwargs.values(), session_id],
    )


def get_runtime_session(session_id: str) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM runtime_sessions WHERE session_id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


# --- Audit Events ---


def insert_audit_event(event: AuditEvent) -> None:
    _insert("audit_events", event.model_dump())


def insert_audit_event_once(event: AuditEvent) -> bool:
    """Insert a caller-identified audit fact once; return False on identity retry."""
    data = event.model_dump()
    columns = ", ".join(data)
    placeholders = ", ".join("?" for _ in data)
    cursor = execute_write(
        f"INSERT INTO audit_events ({columns}) VALUES ({placeholders}) ON CONFLICT(id) DO NOTHING",
        tuple(data.values()),
    )
    return cursor.rowcount == 1


def insert_audit_event_outbox(event: AuditEvent) -> None:
    """Persist an audit fact and enroll its JSONL mirror in the same transaction."""

    db = get_db()
    if not db.in_transaction:
        raise RuntimeError("audit outbox enrollment requires a transaction")
    if not insert_audit_event_once(event):
        raise RuntimeError("audit outbox identity conflict")
    db.execute(
        "UPDATE audit_events SET jsonl_mirror_status='pending', "
        "jsonl_attempt_count=0, jsonl_last_error_class=NULL, "
        "jsonl_mirrored_at=NULL WHERE id=?",
        (event.id,),
    )


def reconcile_audit_event_outbox(
    event_types: Sequence[str],
    *,
    limit: int = 100,
) -> bool:
    """Retry committed audit JSONL mirrors; return whether the backlog drained."""

    kinds = tuple(dict.fromkeys(str(value) for value in event_types if value))
    if not kinds:
        return True
    placeholders = ",".join("?" for _ in kinds)
    rows = get_db().execute(
        "SELECT id, timestamp, actor, source, event_type, entity_type, entity_id, "
        "before_state, after_state, message FROM audit_events WHERE "
        f"event_type IN ({placeholders}) AND jsonl_mirror_status IN ('pending','failed') "
        "ORDER BY timestamp, id LIMIT ?",
        (*kinds, min(max(int(limit), 1), 100)),
    ).fetchall()
    if not rows:
        return True

    def state(value: object) -> object:
        try:
            return json.loads(str(value)) if value is not None else None
        except (TypeError, ValueError):
            return str(value)

    events = [{
        "id": row["id"],
        "timestamp": row["timestamp"],
        "actor": row["actor"],
        "source": row["source"],
        "event_type": row["event_type"],
        "entity_type": row["entity_type"],
        "entity_id": row["entity_id"],
        "before": state(row["before_state"]),
        "after": state(row["after_state"]),
        "message": row["message"],
    } for row in rows]
    error_class = None
    try:
        from aletheia.files.jsonl import write_audits_deduplicated

        mirrored = write_audits_deduplicated(events, lock_timeout_sec=1.0)
    except Exception as exc:
        mirrored = set()
        error_class = type(exc).__name__
    now = datetime.now(timezone.utc).isoformat()
    with transaction(immediate=True):
        for event in events:
            audit_id = str(event["id"])
            success = audit_id in mirrored
            get_db().execute(
                "UPDATE audit_events SET jsonl_mirror_status=?, "
                "jsonl_attempt_count=COALESCE(jsonl_attempt_count,0)+1, "
                "jsonl_last_error_class=?, jsonl_mirrored_at=? WHERE id=?",
                (
                    "mirrored" if success else "failed",
                    None if success else error_class or "incomplete_mirror",
                    now if success else None,
                    audit_id,
                ),
            )
    return len(mirrored) == len(events)


def list_audit_events(
    limit: int = 50, event_type: str | None = None,
) -> list[dict]:
    if event_type:
        return _select_all(
            "audit_events",
            where="event_type = ?",
            params=(event_type,),
            limit=limit,
        )
    return _select_all("audit_events", limit=limit)


def latest_mirrored_webhook_delivery_event() -> dict | None:
    row = get_db().execute(
        "SELECT * FROM audit_events WHERE event_type='webhook_delivery' "
        "AND jsonl_mirror_status='mirrored' AND json_valid(after_state) "
        "ORDER BY json_extract(after_state, '$.attempted_at') DESC, "
        "timestamp DESC, id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


# --- Agent Runs ---


_AGENT_RUN_ACTIVE_STATUSES = ("queued", "running")
_AGENT_RUN_TERMINAL_STATUSES = frozenset({
    "completed",
    "completed_with_errors",
    "failed",
    "timed_out",
    "stale",
    "cancelled",
    "rejected_queue_full",
})
_AGENT_RUN_TERMINAL_TRANSITIONS = {
    "queued": frozenset({"failed", "stale", "cancelled", "rejected_queue_full"}),
    "running": frozenset({
        "completed",
        "completed_with_errors",
        "failed",
        "timed_out",
        "stale",
        "cancelled",
    }),
}


def claim_agent_run(run: AgentRun) -> str | None:
    """Insert one active run or return the exact conflicting run identity."""

    with transaction(immediate=True):
        existing = get_db().execute(
            "SELECT id FROM agent_runs WHERE agent_name=? "
            "AND status IN ('queued','running') LIMIT 1",
            (run.agent_name,),
        ).fetchone()
        if existing is not None:
            return str(existing["id"])
        try:
            _insert("agent_runs", run.model_dump())
        except sqlite3.IntegrityError:
            winner = get_db().execute(
                "SELECT id FROM agent_runs WHERE agent_name=? "
                "AND status IN ('queued','running') LIMIT 1",
                (run.agent_name,),
            ).fetchone()
            if winner is None:
                raise
            return str(winner["id"])
    return None


def insert_agent_run_artifacts(artifacts: list[AgentRunArtifact]) -> None:
    """Persist current-run artifact references without rewriting legacy outputs."""
    if not artifacts:
        return
    rows = [
        (
            artifact.run_id,
            artifact.artifact_type,
            _durable_agent_artifact_id(artifact.artifact_type, artifact.artifact_id),
        )
        for artifact in artifacts
    ]
    execute_many_write(
        "INSERT OR IGNORE INTO agent_run_artifacts "
        "(run_id, artifact_type, artifact_id, reference_version) VALUES (?, ?, ?, 'v2')",
        rows,
    )


def _durable_agent_artifact_id(artifact_type: str, artifact_id: str) -> str:
    """Promote a late-written pending PI artifact to its one durable final row."""
    if artifact_type != "pi_decision":
        return artifact_id
    rows = (
        get_db()
        .execute(
            "SELECT final.id FROM pi_decisions pending "
            "JOIN pi_decisions final ON final.opportunity_id=pending.opportunity_id "
            " AND COALESCE(final.signal_source, 'market_observer')="
            "     COALESCE(pending.signal_source, 'market_observer') "
            " AND final.action IN ('trade','scale_in','flip','skip','watch') "
            " AND (julianday(final.created_at)>julianday(pending.created_at) "
            "      OR (julianday(final.created_at)=julianday(pending.created_at) "
            "          AND final.rowid>pending.rowid)) "
            "WHERE pending.id=? AND pending.action='signal_pending' "
            "AND NOT EXISTS ("
            " SELECT 1 FROM pi_decisions newer_pending "
            " WHERE newer_pending.opportunity_id=pending.opportunity_id "
            " AND COALESCE(newer_pending.signal_source, 'market_observer')="
            "     COALESCE(pending.signal_source, 'market_observer') "
            " AND newer_pending.action='signal_pending' "
            " AND (julianday(newer_pending.created_at)>julianday(pending.created_at) "
            "      OR (julianday(newer_pending.created_at)=julianday(pending.created_at) "
            "          AND newer_pending.rowid>pending.rowid)) "
            " AND (julianday(newer_pending.created_at)<julianday(final.created_at) "
            "      OR (julianday(newer_pending.created_at)=julianday(final.created_at) "
            "          AND newer_pending.rowid<final.rowid))"
            ") ORDER BY julianday(final.created_at), final.rowid LIMIT 2",
            (artifact_id,),
        )
        .fetchall()
    )
    return str(rows[0][0]) if len(rows) == 1 else artifact_id


def begin_agent_run(
    run_id: str,
    *,
    permission_mode: str,
    model_provider: str | None,
) -> bool:
    result = execute_write(
        "UPDATE agent_runs SET status='running', permission_mode=?, model_provider=? "
        "WHERE id=? AND status='queued'",
        (permission_mode, model_provider, run_id),
    )
    return result.rowcount == 1


def finish_agent_run(
    run_id: str,
    *,
    expected_status: AgentRunStatus,
    status: AgentRunStatus,
    ended_at: str,
    outputs_created: str,
    errors: str,
    tools_called: str,
    cost_estimate: float | None,
    model_name: str | None = None,
) -> bool:
    if status not in _AGENT_RUN_TERMINAL_STATUSES:
        raise ValueError("invalid_agent_run_terminal_status")
    if status not in _AGENT_RUN_TERMINAL_TRANSITIONS.get(expected_status, ()):
        raise ValueError("invalid_agent_run_transition")
    result = execute_write(
        "UPDATE agent_runs SET status=?, ended_at=?, outputs_created=?, errors=?, "
        "tools_called=?, cost_estimate=?, model_name=COALESCE(?, model_name) "
        "WHERE id=? AND status=?",
        (
            status,
            ended_at,
            outputs_created,
            errors,
            tools_called,
            cost_estimate,
            model_name,
            run_id,
            expected_status,
        ),
    )
    return result.rowcount == 1


def mark_agent_run_stale(run_id: str, *, ended_at: str, errors: str) -> bool:
    result = execute_write(
        "UPDATE agent_runs SET status='stale', ended_at=?, errors=? "
        "WHERE id=? AND status IN ('queued','running')",
        (ended_at, errors, run_id),
    )
    return result.rowcount == 1


def list_agent_runs(limit: int = 50, *, days: int | None = None) -> list[dict]:
    if days is None:
        return _select_all("agent_runs", limit=limit)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _select_all(
        "agent_runs",
        where="replace(started_at,' ','T') >= replace(?,' ','T')",
        params=(cutoff,),
        order="replace(started_at,' ','T') DESC",
        limit=limit,
    )


def list_agent_runs_window(*, hours: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return _select_all(
        "agent_runs",
        where=(
            "replace(COALESCE(NULLIF(ended_at,''),started_at),' ','T') "
            ">= replace(?,' ','T')"
        ),
        params=(cutoff,),
        order=(
            "replace(COALESCE(NULLIF(ended_at,''),started_at),' ','T') DESC, "
            "rowid DESC"
        ),
        limit=None,
    )


def list_latest_agent_runs() -> list[dict]:
    rows = get_db().execute(
        """
        WITH ranked AS (
            SELECT agent_runs.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY agent_name
                       ORDER BY replace(COALESCE(NULLIF(ended_at,''),started_at),' ','T') DESC,
                                rowid DESC
                   ) AS _rank
            FROM agent_runs
        )
        SELECT * FROM ranked WHERE _rank = 1
        ORDER BY replace(COALESCE(NULLIF(ended_at,''),started_at),' ','T') DESC, id DESC
        """
    ).fetchall()
    result = [dict(row) for row in rows]
    for row in result:
        row.pop("_rank", None)
    return result


def get_agent_run(run_id: str) -> dict | None:
    return _select_one("agent_runs", run_id)


# --- Opportunities ---


def insert_opportunity(opp: Opportunity) -> None:
    if (
        (opp.asset_class == "crypto" or opp.asset_subclass == "stock_linked_perp")
        and opp.execution_eligible
        and opp.source_agent in {"fast_trigger", "universe_scanner"}
        and opp.generation_version != "opportunity-generation/v2"
    ):
        raise ValueError("execution-eligible producer requires an opportunity-generation/v2 record")
    _insert("opportunities", _stamp_provenance(opp.model_dump()))


OPPORTUNITY_GENERATION_VERSION = "opportunity-generation/v2"


def list_opportunities(limit: int = 50) -> list[dict]:
    return _select_all("opportunities", limit=limit)


def list_opportunity_instruments() -> list[dict]:
    """Return one latest-state summary for each exact Opportunity instrument."""
    rows = (
        get_db()
        .execute(
            """
        WITH ranked AS (
            SELECT
                symbol,
                market,
                created_at,
                status,
                direction_bias,
                COUNT(*) OVER (PARTITION BY symbol, market) AS total_count,
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END)
                    OVER (PARTITION BY symbol, market) AS open_count,
                ROW_NUMBER() OVER (
                    PARTITION BY symbol, market
                    ORDER BY datetime(created_at) DESC, rowid DESC
                ) AS latest_rank
            FROM opportunities
        )
        SELECT
            symbol,
            market,
            total_count,
            open_count,
            created_at AS latest_created_at,
            status AS latest_status,
            direction_bias AS latest_direction_bias
        FROM ranked
        WHERE latest_rank = 1
        ORDER BY (open_count > 0) DESC, datetime(latest_created_at) DESC
        """
        )
        .fetchall()
    )
    return [dict(row) for row in rows]


def list_opportunities_for_instrument(
    symbol: str,
    market: str,
    limit: int = 500,
) -> list[dict]:
    return _select_all(
        "opportunities",
        where="symbol = ? AND market = ?",
        params=(symbol, market),
        order="created_at DESC, rowid DESC",
        limit=limit,
    )


def list_open_opportunities(limit: int = 50) -> list[dict]:
    """Return a bounded FIFO working set without reviewed-history crowd-out.

    Oldest-first ordering guarantees repeated bounded runs advance an existing
    backlog even while newer Opportunities continue to arrive.
    """
    return _select_all(
        "opportunities",
        where="status = 'open'",
        order="created_at ASC, rowid ASC",
        limit=limit,
    )


_OPEN_TRADE_ASSET_PREDICATE = """
COALESCE(NULLIF(asset_class, ''), 'crypto') IN ('crypto', 'equity')
AND COALESCE(execution_eligible, 1)=1
AND NOT (
    COALESCE(NULLIF(asset_class, ''), 'crypto')='crypto'
    AND COALESCE(source_agent, '') IN ('fast_trigger', 'universe_scanner')
    AND (
        COALESCE(generation_version, '')!='opportunity-generation/v2'
        OR setup_id IS NULL
    )
)
"""
_OPEN_TRADE_UNRANKED_SQL = f"""
    SELECT * FROM opportunities
    INDEXED BY idx_opportunities_trade_unranked_rotation
    WHERE status='open'
      AND last_ranked_at IS NULL
      AND {_OPEN_TRADE_ASSET_PREDICATE}
    ORDER BY datetime(created_at) ASC, id ASC
    LIMIT ? OFFSET ?
"""
_OPEN_TRADE_RETRY_SQL = f"""
    SELECT * FROM opportunities
    INDEXED BY idx_opportunities_trade_retry_rotation
    WHERE status='open'
      AND last_ranked_at IS NOT NULL
      AND {_OPEN_TRADE_ASSET_PREDICATE}
    ORDER BY datetime(last_ranked_at) ASC, datetime(created_at) ASC, id ASC
    LIMIT ? OFFSET ?
"""


def find_divergence_opportunity_by_source_version(
    source_version: str,
) -> dict | None:
    """Return the earliest divergence row for one producer source version.

    Lifecycle state is deliberately unrestricted so a delayed retry resolves
    the original row after it has already been queued or reviewed.
    """
    if not source_version:
        return None
    row = (
        get_db()
        .execute(
            "SELECT * FROM opportunities "
            "WHERE source_agent='divergence_scanner' "
            "AND json_valid(metadata) "
            "AND json_extract(metadata, '$.source_version') = ? "
            "ORDER BY datetime(created_at) ASC, rowid ASC LIMIT 1",
            (source_version,),
        )
        .fetchone()
    )
    return dict(row) if row else None


def get_opportunity(oid: str) -> dict | None:
    return _select_one("opportunities", oid)


def list_opportunities_by_asset_class(asset_class: str, limit: int = 50) -> list[dict]:
    return _select_all("opportunities", where="asset_class = ?", params=(asset_class,), limit=limit)


def insert_divergence_opportunity_once(
    opportunity: Opportunity,
    *,
    source_version: str,
) -> tuple[dict, bool]:
    """Atomically resolve one lifecycle owner for a divergence source version."""
    identity = str(source_version or "").strip()
    if not identity or opportunity.source_agent != "divergence_scanner":
        raise ValueError("divergence Opportunity identity is invalid")
    try:
        metadata = json.loads(opportunity.metadata or "{}")
    except (TypeError, ValueError) as exc:
        raise ValueError("divergence Opportunity metadata is invalid") from exc
    if metadata.get("source_version") != identity:
        raise ValueError("divergence Opportunity source version mismatch")

    with transaction(immediate=True):
        existing = find_divergence_opportunity_by_source_version(identity)
        if existing is not None:
            return existing, False
        insert_opportunity(opportunity)
        persisted = _select_one("opportunities", opportunity.id)
        if persisted is None:
            raise RuntimeError("divergence Opportunity insert did not persist")
        return persisted, True


# --- PI Decisions ---


def insert_pi_decision(decision) -> None:
    """Persist a System Operator trading decision to the database."""
    data = _to_payload_dict(decision)
    _insert("pi_decisions", _stamp_provenance(data))


def update_pi_decision(decision_id: str, **kwargs) -> None:
    """Update the durable pre-submission claim after its proposal attempt settles."""
    _update("pi_decisions", decision_id, kwargs)


def get_pi_decision_for_proposal(proposal_id: str) -> dict | None:
    """Return the unique final PiDecision linked to one exact Proposal id."""
    if not proposal_id:
        return None
    rows = (
        get_db()
        .execute(
            "SELECT * FROM pi_decisions WHERE proposal_id=? "
            "AND action!='signal_pending' ORDER BY datetime(created_at) ASC, rowid ASC LIMIT 2",
            (proposal_id,),
        )
        .fetchall()
    )
    if len(rows) > 1:
        return {
            "_integrity_error": "ambiguous_integrity_error",
            "proposal_id": proposal_id,
        }
    return dict(rows[0]) if rows else None


def _expire_opportunity_rows_with_ttl_evidence(
    rows,
    *,
    source_status: str,
    evaluated_at: str,
    reason_detail: str,
    parameters: dict,
) -> int:
    """Expire selected rows only after their append-only rejection fact exists.

    The caller holds the surrounding SQLite write transaction.  If evidence
    insertion fails, the preceding lifecycle update rolls back with it, leaving the
    Opportunity retryable instead of silently terminal.
    """
    db = get_db()
    savepoint = f"ttl_expiry_{uuid.uuid4().hex}"
    db.execute(f"SAVEPOINT {savepoint}")
    try:
        expired_count = 0
        for raw in rows:
            row = dict(raw)
            opportunity_id = str(row["id"])
            dedup_key = f"opportunity_ttl:{source_status}:{opportunity_id}"
            updated = execute_write(
                "UPDATE opportunities SET status='expired' WHERE id=? AND status=?",
                (opportunity_id, source_status),
            )
            if updated.rowcount != 1:
                continue
            execute_write(
                "INSERT OR IGNORE INTO filter_decisions ("
                "id, dedup_key, candidate_key, candidate_kind, parent_candidate_key, "
                "run_id, opportunity_id, universe_snapshot_id, universe_member_id, "
                "proposal_id, venue, symbol, market_type, direction, scanner_family, "
                "regime_tag, filter_name, stage, sequence_no, decision, reason_code, "
                "reason_detail, input_snapshot, parameter_snapshot, filter_version, "
                "code_commit, outcome_candidate_key, evaluated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    dedup_key,
                    dedup_key,
                    f"opportunity:{opportunity_id}",
                    "opportunity",
                    None,
                    None,
                    opportunity_id,
                    row.get("universe_snapshot_id"),
                    row.get("universe_member_id"),
                    None,
                    None,
                    str(row.get("symbol") or "unknown"),
                    str(row.get("market") or "spot"),
                    row.get("direction_bias"),
                    row.get("scanner_family"),
                    row.get("regime_tag"),
                    "opportunity_ttl",
                    "lifecycle_expiry",
                    0,
                    "rejected",
                    "ttl_expired",
                    reason_detail,
                    json.dumps(
                        {"source_status": source_status},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    json.dumps(parameters, sort_keys=True, separators=(",", ":")),
                    "opportunity_ttl:v1",
                    None,
                    None,
                    evaluated_at,
                ),
            )
            evidence = db.execute(
                "SELECT 1 FROM filter_decisions WHERE dedup_key=?",
                (dedup_key,),
            ).fetchone()
            if evidence is None:
                raise RuntimeError(f"ttl evidence unavailable for Opportunity {opportunity_id}")
            expired_count += 1
        db.execute(f"RELEASE SAVEPOINT {savepoint}")
        return expired_count
    except Exception:
        db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        db.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


# --- Capital Events ---


def _normalized_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def insert_capital_event(event) -> None:
    if not math.isfinite(event.amount_usdt):
        raise ValueError(f"capital event amount_usdt must be finite, got {event.amount_usdt!r}")
    _insert("capital_events", _to_payload_dict(event))


def list_capital_events_page(
    *,
    limit: int = 20,
    before_detected_at: str | None = None,
    before_id: str | None = None,
) -> dict:
    """Return one stable, keyset-paginated page of the retained cash-flow ledger."""
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


def has_capital_event_for_broker(broker_id: str) -> bool:
    db = get_db()
    row = db.execute("SELECT 1 FROM capital_events WHERE broker_id = ? LIMIT 1", (broker_id,)).fetchone()
    return row is not None


def get_first_capital_event_time() -> str | None:
    """Earliest detected_at across all capital events (ISO string or None)."""
    db = get_db()
    row = db.execute("SELECT MIN(detected_at) AS t FROM capital_events").fetchone()
    return row["t"] if row and row["t"] else None


# --- Prediction Observer Runs ---


def insert_prediction_run(run_id: str, categories: list) -> None:
    import json as _json

    from aletheia.storage.models import _utcnow

    _insert(
        "prediction_observer_runs",
        {
            "id": run_id,
            "started_at": _utcnow(),
            "status": "running",
            "categories": _json.dumps(categories),
        },
    )


def update_prediction_run(
    run_id: str,
    *,
    status: str = "completed",
    markets_scanned: int = 0,
    opportunities_created: int = 0,
    errors: list | None = None,
) -> None:
    import json as _json

    from aletheia.storage.models import _utcnow

    data: dict = {
        "status": status,
        "completed_at": _utcnow(),
        "markets_scanned": markets_scanned,
        "opportunities_created": opportunities_created,
    }
    if errors:
        data["errors"] = _json.dumps(errors)
    _update("prediction_observer_runs", run_id, data)


def list_prediction_runs(limit: int = 20) -> list[dict]:
    return _select_all("prediction_observer_runs", limit=limit)


def get_latest_prediction_run() -> dict | None:
    rows = _select_all("prediction_observer_runs", order="started_at DESC", limit=1)
    return rows[0] if rows else None


# --- Prediction Normalized Tables ---


def upsert_prediction_event(data: dict) -> str:
    """Insert or update a prediction event by external_event_id."""
    from aletheia.storage.models import _utcnow, new_id

    db = get_db()
    ext_id = data["external_event_id"]
    existing = db.execute(
        "SELECT id FROM prediction_events WHERE external_event_id = ? AND source = ?",
        (ext_id, data.get("source", "polymarket")),
    ).fetchone()
    if existing:
        row_id = existing["id"]
        data["updated_at"] = _utcnow()
        data.pop("id", None)
        data.pop("created_at", None)
        sets = ", ".join(f"{k} = ?" for k in data.keys())
        execute_write(f"UPDATE prediction_events SET {sets} WHERE id = ?", [*data.values(), row_id])
    else:
        row_id = data.get("id") or new_id()
        data["id"] = row_id
        data.setdefault("created_at", _utcnow())
        data.setdefault("updated_at", _utcnow())
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        execute_write(f"INSERT INTO prediction_events ({cols}) VALUES ({placeholders})", list(data.values()))
    return row_id


def upsert_prediction_market(data: dict) -> str:
    """Insert or update a prediction market by external_market_id."""
    from aletheia.storage.models import _utcnow, new_id

    db = get_db()
    ext_id = data["external_market_id"]
    existing = db.execute(
        "SELECT id FROM prediction_markets WHERE external_market_id = ? AND source = ?",
        (ext_id, data.get("source", "polymarket")),
    ).fetchone()
    if existing:
        row_id = existing["id"]
        data["updated_at"] = _utcnow()
        data.pop("id", None)
        sets = ", ".join(f"{k} = ?" for k in data.keys())
        execute_write(f"UPDATE prediction_markets SET {sets} WHERE id = ?", [*data.values(), row_id])
    else:
        row_id = data.get("id") or new_id()
        data["id"] = row_id
        data.setdefault("updated_at", _utcnow())
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        execute_write(f"INSERT INTO prediction_markets ({cols}) VALUES ({placeholders})", list(data.values()))
    return row_id


def insert_probability_snapshot(snapshot: dict) -> None:
    from aletheia.storage.models import _utcnow, new_id

    snapshot.setdefault("id", new_id())
    snapshot.setdefault("created_at", _utcnow())
    _insert("prediction_probability_snapshots", snapshot)


def list_prediction_events(category: str | None = None, status: str = "active", limit: int = 50) -> list[dict]:
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
    return _select_one("prediction_events", event_id)


def list_prediction_markets_for_event(event_id: str, limit: int = 50) -> list[dict]:
    return _select_all("prediction_markets", where="event_id = ?", params=(event_id,), limit=limit)


def get_prediction_market_by_id(market_id: str) -> dict | None:
    return _select_one("prediction_markets", market_id)


def list_probability_snapshots(market_id: str, since: str | None = None, limit: int = 200) -> list[dict]:
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


def insert_prediction_claim(data: dict) -> None:
    from aletheia.storage.models import _utcnow, new_id

    data.setdefault("id", new_id())
    data.setdefault("created_at", _utcnow())
    _insert("prediction_market_claims", data)


def list_prediction_claims(
    market_id: str | None = None,
    claim_type: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> list[dict]:
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


# --- Trade Proposals ---

# TaG hash chain (P0-1, 2026-06-29). See:
#   docs/research/agents-learning/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md §五 P0-1
# Fields excluded from the hash because they mutate after insert:
#   - status (pending → executed / rejected via update_trade_proposal)
#   - commit_hash (the field being computed)
# Everything else is treated as immutable; mutating any of these post-insert
# would invalidate the chain (caught by the L2c spec re-compute check).
_PROPOSAL_HASH_EXCLUDES = {"status", "commit_hash"}

# Risk working-set reads need complete immutable snapshots without one query per
# Order/reservation. Generate the projections from the storage models so a new
# hash-covered field cannot be silently omitted from authority validation.
_RISK_PROPOSAL_PREFIX = "risk_proposal__"
_RISK_DECISION_PREFIX = "risk_decision__"
_RISK_PROPOSAL_PROJECTION = ", ".join(
    f"tp.{field} AS {_RISK_PROPOSAL_PREFIX}{field}" for field in TradeProposal.model_fields
)
_RISK_DECISION_PROJECTION = ", ".join(
    f"rd.{field} AS {_RISK_DECISION_PREFIX}{field}" for field in RiskDecision.model_fields
)


def _compute_proposal_hash(p: TradeProposal) -> str:
    """Return 16-char SHA-256 prefix of the proposal's immutable content + parent_hash.

    Stable for a given proposal as long as no excluded field is mutated. 16 hex
    chars = 64-bit collision space (OpenAlice's reference impl uses 8; we widen
    to 16 because the cost is trivial and false-collision risk drops by ~2^32).
    """
    from aletheia.storage.hashing import sha256_json

    payload = p.model_dump(exclude=_PROPOSAL_HASH_EXCLUDES)
    # Additive identity: NULL historical rows retain their exact original hash.
    if p.entry_leg_index is None:
        payload.pop("entry_leg_index")
    return sha256_json(payload)[:16]


def validate_trade_proposal_commit_hash(
    proposal: TradeProposal | Mapping[str, object],
) -> str:
    """Return the stored Proposal hash only if immutable content still matches it."""
    try:
        stored = proposal if isinstance(proposal, TradeProposal) else TradeProposal.model_validate(proposal)
    except ValidationError as exc:
        raise ValueError("stored proposal content cannot be validated") from exc
    if stored.commit_hash is None:
        raise ValueError("stored proposal has no commit hash")
    if _compute_proposal_hash(stored) != stored.commit_hash:
        raise ValueError("stored proposal content does not match commit hash")
    return stored.commit_hash


def insert_trade_proposal(
    p: TradeProposal,
    *,
    decision_inputs: object | None = None,
) -> None:
    # Provenance is immutable Proposal identity, so stamp it on both the model
    # and persisted payload *before* computing the hash. Hashing first produced
    # a commit that could never be verified from a real runtime-stamped row.
    stamped = _stamp_provenance(p.model_dump())
    for field in _PROVENANCE_FIELDS:
        if field in stamped:
            setattr(p, field, stamped[field])

    # Pydantic assignment validation is intentionally not global.  A caller can
    # therefore assign an integer to a float field after construction; SQLite
    # reads that REAL back as a float, and the same immutable content would hash
    # differently (59000 vs 59000.0). Revalidate once at the persistence boundary
    # and copy normalized values onto the caller model before hashing.
    normalized = TradeProposal.model_validate(p.model_dump())
    for field, value in normalized.model_dump().items():
        setattr(p, field, value)

    # Parent lookup + insert is one short writer claim. Without this, two
    # concurrent proposals can both select the same parent and fork the global
    # append-only chain even though each individual hash is valid.
    with transaction(immediate=True):
        db = get_db()
        from aletheia.storage.proposal_hypothesis import freeze_position_hypothesis

        freeze_position_hypothesis(
            p,
            db=db,
            decision=decision_inputs,
        )
        row = db.execute(
            "SELECT commit_hash FROM trade_proposals WHERE commit_hash IS NOT NULL ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        current_tip = row[0] if row else None
        if p.commit_hash is None:
            p.parent_hash = current_tip
            p.commit_hash = _compute_proposal_hash(p)
        else:
            if _compute_proposal_hash(p) != p.commit_hash:
                raise ValueError("proposal commit hash does not match immutable content")
            # A caller may pre-compute a valid child of an earlier tip.  The
            # hash is individually valid but accepting it would fork the global
            # Proposal chain.  Recompute against the new tip or reject; never
            # accept two siblings.
            if p.parent_hash != current_tip:
                raise ValueError("proposal parent hash is stale")
        _insert("trade_proposals", p.model_dump())


def update_trade_proposal(pid: str, **kwargs) -> None:
    if set(kwargs) - {"status"}:
        raise ValueError("immutable Proposal fields cannot be updated")
    _update("trade_proposals", pid, kwargs)


def list_trade_proposals(limit: int = 50, status: str | None = None) -> list[dict]:
    if status:
        return _select_all("trade_proposals", where="status = ?", params=(status,), limit=limit)
    return _select_all("trade_proposals", limit=limit)


def get_trade_proposal(pid: str) -> dict | None:
    return _select_one("trade_proposals", pid)


def get_trade_proposal_by_entry_plan(entry_plan_id: str, *, leg_index: int | None = None) -> dict | None:
    row = (
        get_db()
        .execute(
            "SELECT * FROM trade_proposals WHERE entry_plan_id=? "
            "AND (? IS NULL OR COALESCE(entry_leg_index,0)=?) "
            "ORDER BY COALESCE(entry_leg_index,0) DESC LIMIT 1",
            (entry_plan_id, leg_index, leg_index),
        )
        .fetchone()
    )
    return dict(row) if row else None


def get_proposal_execution_claim(proposal_id: str) -> dict | None:
    """Return the durable execution owner/lease for one Proposal."""
    row = (
        get_db()
        .execute(
            "SELECT * FROM proposal_execution_claims WHERE proposal_id=?",
            (proposal_id,),
        )
        .fetchone()
    )
    return dict(row) if row else None


def proposal_execution_claim_is_fresh(claim: dict, now: datetime) -> bool:
    try:
        expires = datetime.fromisoformat(str(claim.get("lease_expires_at")).replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > now
    except (TypeError, ValueError):
        return False


def get_entry_risk_reservation(proposal_id: str) -> dict | None:
    """Return one durable pre-Order Risk allowance, if present."""
    row = (
        get_db()
        .execute(
            "SELECT * FROM entry_risk_reservations WHERE proposal_id=?",
            (proposal_id,),
        )
        .fetchone()
    )
    return dict(row) if row else None


def acquire_entry_risk_reservation(
    *,
    proposal_id: str,
    proposal_commit_hash: str,
    symbol: str,
    execution_mode: str,
    quantity: float,
    price: float,
    market_type: str | None = None,
    leverage: float | None = None,
) -> bool:
    """Acquire or verify one exact durable pre-Order Risk allowance.

    The authoritative Risk caller owns the surrounding ``BEGIN IMMEDIATE`` that
    covered its working-set read. Identical replays are idempotent; a conflicting
    immutable snapshot fails closed.
    """
    if market_type is None or leverage is None:
        proposal = get_trade_proposal(proposal_id)
        if proposal is None:
            raise ValueError("entry Risk reservation requires a stored Proposal identity")
        if market_type is None:
            market_type = str(proposal.get("market_type") or "")
        if leverage is None:
            leverage = float(proposal.get("leverage") or 1.0)
    if not proposal_id or not proposal_commit_hash or not symbol or not market_type or not execution_mode:
        raise ValueError("entry Risk reservation requires complete Proposal identity")
    quantity = float(quantity)
    price = float(price)
    leverage = float(leverage)
    if quantity <= 0 or price <= 0 or leverage <= 0:
        raise ValueError("entry Risk reservation requires positive quantity, price, and leverage")

    existing = get_entry_risk_reservation(proposal_id)
    expected = {
        "proposal_commit_hash": proposal_commit_hash,
        "symbol": symbol,
        "market_type": market_type,
        "execution_mode": execution_mode,
        "quantity": quantity,
        "price": price,
        "leverage": leverage,
    }
    if existing is not None:
        conflicts = [key for key, value in expected.items() if existing.get(key) != value]
        if conflicts:
            raise ValueError(
                "entry Risk reservation conflicts with immutable Proposal snapshot: " + ",".join(conflicts)
            )
        return False

    db = get_db()
    was_in_tx = db.in_transaction
    execute_write(
        "INSERT INTO entry_risk_reservations "
        "(proposal_id,proposal_commit_hash,symbol,market_type,execution_mode,"
        "quantity,price,leverage) VALUES (?,?,?,?,?,?,?,?)",
        (
            proposal_id,
            proposal_commit_hash,
            symbol,
            market_type,
            execution_mode,
            quantity,
            price,
            leverage,
        ),
    )
    if not was_in_tx:
        db.commit()
    return True


def release_entry_risk_reservation(proposal_id: str) -> bool:
    """Release a pre-Order allowance after durable handoff or terminal failure."""
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write(
        "DELETE FROM entry_risk_reservations WHERE proposal_id=?",
        (proposal_id,),
    )
    if not was_in_tx:
        db.commit()
    return cur.rowcount == 1


def list_active_entry_risk_reservations_for_risk(
    *,
    exclude_proposal_id: str | None = None,
) -> list[dict]:
    """Return every unresolved allowance not represented by a durable Order.

    No row limit is permitted. Excluding rows that already have any Order avoids
    double counting while Gateway performs its best-effort reservation cleanup.
    A reservation whose Proposal is provably terminal is also inactive even if
    physical cleanup failed.  Missing Proposals and unknown statuses remain in
    the working set so Risk continues to fail closed on ambiguous state.
    """
    sql = (
        "SELECT r.*, "
        f"{_RISK_PROPOSAL_PROJECTION}, "
        "(SELECT COUNT(*) FROM risk_decisions authority "
        " WHERE authority.proposal_id=r.proposal_id "
        "   AND authority.decision_role='authoritative' "
        "   AND authority.approved=1 "
        "   AND authority.proposal_commit_hash=r.proposal_commit_hash "
        "   AND authority.evaluated_quantity=r.quantity "
        "   AND authority.allowed_execution_mode=r.execution_mode "
        "   AND authority.proposal_execution_mode=r.execution_mode "
        "   AND COALESCE(authority.violations, '[]')='[]'"
        ") AS exact_authoritative_decision_count "
        "FROM entry_risk_reservations r "
        "LEFT JOIN trade_proposals tp ON tp.id=r.proposal_id "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM orders o WHERE o.proposal_id=r.proposal_id"
        ") "
        "AND COALESCE(tp.status, '') NOT IN ('rejected','error','executed')"
    )
    params: list = []
    if exclude_proposal_id is not None:
        sql += " AND r.proposal_id != ?"
        params.append(exclude_proposal_id)
    sql += " ORDER BY r.created_at ASC, r.rowid ASC"
    rows = get_db().execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def insert_proposal_execution_claim(
    proposal_id: str,
    owner_token: str,
    claimed_at: str,
    lease_expires_at: str,
    *,
    execution_attempt_id: str | None = None,
    execution_receipt_hash: str | None = None,
    execution_receipt_json: str | None = None,
) -> bool:
    """Acquire an absent Proposal execution claim without replacing an owner."""
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write(
        "INSERT OR IGNORE INTO proposal_execution_claims "
        "(proposal_id, owner_token, claimed_at, lease_expires_at, execution_attempt_id, "
        "execution_receipt_hash, execution_receipt_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            proposal_id,
            owner_token,
            claimed_at,
            lease_expires_at,
            execution_attempt_id,
            execution_receipt_hash,
            execution_receipt_json,
        ),
    )
    if not was_in_tx:
        db.commit()
    return cur.rowcount == 1


def replace_proposal_execution_claim(
    proposal_id: str,
    *,
    expected_owner_token: str,
    expected_lease_expires_at: str,
    owner_token: str,
    claimed_at: str,
    lease_expires_at: str,
    execution_attempt_id: str | None = None,
    execution_receipt_hash: str | None = None,
    execution_receipt_json: str | None = None,
) -> bool:
    """CAS-replace one observed expired execution claim."""
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write(
        "UPDATE proposal_execution_claims "
        "SET owner_token=?, claimed_at=?, lease_expires_at=?, execution_attempt_id=?, "
        "execution_receipt_hash=?, execution_receipt_json=? "
        "WHERE proposal_id=? AND owner_token=? AND lease_expires_at=?",
        (
            owner_token,
            claimed_at,
            lease_expires_at,
            execution_attempt_id,
            execution_receipt_hash,
            execution_receipt_json,
            proposal_id,
            expected_owner_token,
            expected_lease_expires_at,
        ),
    )
    if not was_in_tx:
        db.commit()
    return cur.rowcount == 1


def release_proposal_execution_claim(proposal_id: str, owner_token: str) -> bool:
    """CAS-release only the caller's own execution claim."""
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write(
        "DELETE FROM proposal_execution_claims WHERE proposal_id=? AND owner_token=?",
        (proposal_id, owner_token),
    )
    if not was_in_tx:
        db.commit()
    return cur.rowcount == 1


def has_order_for_proposal(proposal_id: str) -> bool:
    """Return whether any durable Order already represents this Proposal."""
    return (
        get_db()
        .execute(
            "SELECT 1 FROM orders WHERE proposal_id=? LIMIT 1",
            (proposal_id,),
        )
        .fetchone()
        is not None
    )


def get_order_for_entry_plan(entry_plan_id: str) -> dict | None:
    """Read the latest leg Order; the Plan backlink is historical provenance only."""
    row = get_db().execute(
        "SELECT o.* FROM orders o LEFT JOIN trade_proposals p ON p.id=o.proposal_id "
        "WHERE o.entry_plan_id=? AND o.close_reason IS NULL "
        "ORDER BY COALESCE(p.entry_leg_index,0) DESC, o.created_at DESC, o.id DESC LIMIT 1",
        (entry_plan_id,),
    ).fetchone()
    return dict(row) if row else None


# --- Risk Decisions ---


def insert_risk_decision(d: RiskDecision) -> None:
    _insert("risk_decisions", _stamp_provenance(d.model_dump()))


def get_authoritative_risk_decision(
    proposal_id: str,
    proposal_commit_hash: str | None,
    evaluated_quantity: float | None,
    policy_hash: str | None = None,
    execution_attempt_id: str | None = None,
) -> dict | None:
    """Return the authoritative decision for one immutable evaluated snapshot."""
    policy_clause = "AND policy_hash IS ?" if policy_hash is not None else ""
    attempt_clause = "AND execution_attempt_id IS ?" if execution_attempt_id is not None else ""
    params = [proposal_id, proposal_commit_hash, evaluated_quantity]
    if policy_hash is not None:
        params.append(policy_hash)
    if execution_attempt_id is not None:
        params.append(execution_attempt_id)
    row = (
        get_db()
        .execute(
            f"""
        SELECT *
        FROM risk_decisions
        WHERE proposal_id = ?
          AND decision_role = 'authoritative'
          AND proposal_commit_hash IS ?
          AND evaluated_quantity IS ?
          {policy_clause}
          {attempt_clause}
        ORDER BY rowid ASC
        LIMIT 1
        """,
            tuple(params),
        )
        .fetchone()
    )
    return dict(row) if row else None


def insert_authoritative_risk_decision(d: RiskDecision) -> tuple[dict, bool]:
    """Insert one authoritative snapshot decision, or return its prior result.

    ``BEGIN IMMEDIATE`` makes the lookup-and-insert atomic across worker threads
    and processes. The partial unique index is the final guard. Historical rows
    have ``decision_role='legacy'`` and therefore never make a migration fail.
    """
    if d.decision_role != "authoritative":
        raise ValueError("authoritative decision insert requires decision_role='authoritative'")

    with transaction(immediate=True):
        existing = get_authoritative_risk_decision(
            d.proposal_id,
            d.proposal_commit_hash,
            d.evaluated_quantity,
            d.policy_hash,
            d.execution_attempt_id,
        )
        if existing is not None:
            return existing, False
        insert_risk_decision(d)
        stored = _select_one("risk_decisions", d.id)
        if stored is None:  # defensive: an acknowledged insert must be readable
            raise RuntimeError("authoritative risk decision insert was not persisted")
        return stored, True


def list_risk_decisions(limit: int = 50, approved: bool | None = None) -> list[dict]:
    if approved is not None:
        return _select_all(
            "risk_decisions",
            where="approved = ?",
            params=(int(approved),),
            limit=limit,
        )
    return _select_all("risk_decisions", limit=limit)


# --- Orders ---


def get_order(oid: str) -> dict | None:
    return _select_one("orders", oid)


def _validate_native_entry_order_snapshot(
    data: dict,
    proposal: dict | None,
    decision: dict | None,
) -> None:
    """Validate one native entry against already-read immutable authority."""
    proposal_id = data.get("proposal_id")
    risk_decision_id = data.get("risk_decision_id")
    if not proposal_id or not risk_decision_id:
        raise ValueError("native entry Order requires Proposal and authoritative RiskDecision")
    if proposal is None or proposal.get("source") != "native":
        raise ValueError("native entry Order requires a native Proposal")
    if proposal.get("id") != proposal_id:
        raise ValueError("native entry Order Proposal identity does not match")
    if decision is None or decision.get("id") != risk_decision_id or decision.get("proposal_id") != proposal_id:
        raise ValueError("native entry Order RiskDecision does not match Proposal")
    if not bool(decision.get("approved")):
        raise ValueError("native entry Order requires an approved RiskDecision")
    if decision.get("decision_role") != "authoritative":
        raise ValueError("native entry Order requires an authoritative RiskDecision")
    try:
        decision_violations = json.loads(decision.get("violations") or "[]")
    except (TypeError, ValueError):
        decision_violations = None
    if decision_violations != []:
        raise ValueError("native entry Order approved RiskDecision is contradictory")

    stored = TradeProposal.model_validate(proposal)
    stored_hash = stored.commit_hash
    if not stored_hash or _compute_proposal_hash(stored) != stored_hash:
        raise ValueError("native entry Order Proposal integrity check failed")
    if decision.get("proposal_commit_hash") != stored_hash:
        raise ValueError("native entry Order RiskDecision snapshot is stale")

    try:
        evaluated_quantity = float(decision.get("evaluated_quantity"))
        order_quantity = float(data.get("quantity"))
    except (TypeError, ValueError):
        raise ValueError("native entry Order lacks evaluated quantity authority") from None
    if evaluated_quantity <= 0 or order_quantity <= 0 or order_quantity != evaluated_quantity:
        raise ValueError("native entry Order quantity does not match RiskDecision")

    execution_mode = data.get("execution_mode")
    if (
        decision.get("allowed_execution_mode") != execution_mode
        or decision.get("proposal_execution_mode") != stored.execution_mode
        or execution_mode != stored.execution_mode
    ):
        raise ValueError("native entry Order execution mode lacks matching authority")
    for field in ("symbol", "market_type", "direction", "order_type"):
        if data.get(field) != getattr(stored, field):
            raise ValueError(f"native entry Order {field} does not match Proposal")
    try:
        order_price = float(data.get("price"))
        proposal_price = float(stored.price)
    except (TypeError, ValueError):
        raise ValueError("native entry Order price lacks Proposal authority") from None
    # Every exchange Order preserves the submitted Proposal price.
    if (
        proposal_price <= 0
        or order_price <= 0
        or order_price != proposal_price
    ):
        raise ValueError("native entry Order price does not match Proposal")
    if data.get("client_order_id") != proposal_id:
        raise ValueError("exchange entry Order client identity is not durable")


def validate_native_order_authority(order: Order | Mapping[str, object]) -> None:
    """Fail closed when a new native Order lacks its immutable authority.

    Entry Orders consume exactly one approved authoritative RiskDecision. Close
    Orders consume the already-open Position they reduce. Adopted exchange state
    is deliberately exempt because its synthetic chain records observation, not
    a new Aletheia trading decision.
    """
    from aletheia.uta.manager import UTA_EXECUTION_MODES

    data = _to_payload_dict(order)
    if data.get("execution_mode") not in UTA_EXECUTION_MODES:
        raise ValueError("unsupported Order execution mode")
    if data.get("source", "native") != "native":
        return

    position_id = data.get("position_id")
    position = _select_one("positions", position_id) if position_id else None
    pending_owner = bool(position and position.get("pending_close_order_id") == data.get("id"))
    is_close = bool(data.get("close_reason")) or pending_owner
    if is_close:
        if position is None:
            raise ValueError("native close Order requires an existing Position")
        if position.get("status") != "open":
            raise ValueError("native close Order requires an open Position")
        if data.get("risk_decision_id") is not None:
            raise ValueError("native close Order consumes Position authority, not a RiskDecision")
        for field in ("proposal_id", "symbol", "market_type", "direction", "execution_mode"):
            if data.get(field) != position.get(field):
                raise ValueError(f"native close Order {field} does not match Position")
        requested = float(data.get("quantity") or 0.0)
        position_quantity = float(position.get("quantity") or 0.0)
        tolerance = max(1e-12, position_quantity * 1e-9)
        if requested <= 0:
            raise ValueError("native close Order quantity must be positive")
        protection_suffix = {
            "protective_stop": "sl",
            "take_profit": "tp",
        }.get(data.get("close_reason"))
        if protection_suffix and str(data.get("client_order_id") or "").startswith("protect-"):
            if requested > position_quantity + tolerance:
                raise ValueError("native protection Order exceeds Position quantity")
            expected_client_id = f"protect-{position_id}-{protection_suffix}"
            if data.get("client_order_id") != expected_client_id:
                raise ValueError("native protection Order client identity is not durable")
            return
        is_partial_reduce = requested < position_quantity - tolerance
        if is_partial_reduce:
            # Partial reduce: quantity must be strictly less than position quantity
            return
        # Full close: quantity must equal position quantity and client_order_id must be durable
        if abs(requested - position_quantity) > tolerance:
            raise ValueError("native close Order quantity does not match Position")
        expected_client_id = f"close-{position_id}"
        if data.get("client_order_id") != expected_client_id:
            raise ValueError("native close Order client identity is not durable")
        return

    proposal_id = data.get("proposal_id")
    risk_decision_id = data.get("risk_decision_id")
    proposal = get_trade_proposal(proposal_id) if proposal_id else None
    decision = _select_one("risk_decisions", risk_decision_id) if risk_decision_id else None
    _validate_native_entry_order_snapshot(data, proposal, decision)


def insert_order(o: Order) -> None:
    validate_native_order_authority(o)
    _insert("orders", _stamp_provenance(o.model_dump()))


_ORDER_METADATA_FIELDS = frozenset({
    "exchange_order_id",
    "position_id",
    "quantity",
    "order_tactic",
    "time_in_force",
    "matching_stream_generation_id",
    "matching_subscription_generation_id",
    "monitoring_gap_started_at",
    # Submission intent and account identity, stamped before the venue call.
    # These describe what is being sent, never where the Order is in its
    # lifecycle — status/error_reason/filled_at stay with transition_order().
    "uta_account_identity_json",
    # Entry Plan cancel convergence bookkeeping (see gateway.cancel_entry_plan_order).
    "entry_cancel_attempts",
    "entry_cancel_retry_after",
    "order_type",
    "price",
})
_ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    "pending": frozenset({
        "submitted", "submission_unknown", "submission_rejected", "materialization_missing",
        "partial", "partially_filled", "filled", "overfilled", "cancelled", "error",
    }),
    "open": frozenset({
        "materialization_missing", "partial", "partially_filled", "filled",
        "overfilled", "cancelled", "error",
    }),
    "submitted": frozenset({
        "submission_unknown", "materialization_missing", "partial",
        "partially_filled", "filled", "overfilled", "cancelled", "error",
    }),
    "submission_unknown": frozenset({
        "submitted", "submission_rejected", "materialization_missing", "partial", "partially_filled",
        "filled", "overfilled", "cancelled", "error",
    }),
    "submission_rejected": frozenset(),
    "materialization_missing": frozenset({
        "submitted", "partial", "partially_filled", "filled", "overfilled",
        "cancelled", "error",
    }),
    "partial": frozenset({
        "materialization_missing", "partially_filled", "filled", "overfilled",
        "cancelled", "error",
    }),
    "partially_filled": frozenset({
        "materialization_missing", "filled", "overfilled", "cancelled", "error",
    }),
    # A venue cancellation can race already-executed private fills. Durable fill
    # evidence wins, while all other terminal states remain monotonic.
    "cancelled": frozenset({"partially_filled", "filled", "overfilled"}),
    "filled": frozenset(),
    "overfilled": frozenset(),
    "error": frozenset(),
    # Historical terminals: reachable in stored rows, never a transition target.
    "executed": frozenset(),
    "failed": frozenset(),
    "rejected": frozenset(),
}


class OrderTransitionConflict(RuntimeError):
    pass


def update_order_metadata(oid: str, **kwargs: object) -> None:
    unknown = set(kwargs) - _ORDER_METADATA_FIELDS
    if unknown:
        raise ValueError("invalid_order_metadata_fields:" + ",".join(sorted(unknown)))
    _update("orders", oid, kwargs)


def replace_order_error_reason(
    order_id: str,
    *,
    expected_status: OrderStatus,
    expected_reason: str,
    error_reason: str,
) -> None:
    """CAS-update an Order error reason without touching other lifecycle fields."""
    updated = execute_write(
        "UPDATE orders SET error_reason=? "
        "WHERE id=? AND status=? AND error_reason=?",
        (error_reason, order_id, expected_status, expected_reason),
    )
    if updated.rowcount != 1:
        raise OrderTransitionConflict(f"order_error_reason_conflict:{order_id}")


def transition_order(
    order_id: str,
    *,
    status: OrderStatus,
    exchange_order_id: str | None = None,
    position_id: str | None = None,
    error_reason: str | None = None,
    filled_at: str | None = None,
) -> dict:
    """Apply one legal Order transition; EntryPlan owns its aggregate authority."""

    with transaction(immediate=True):
        order = get_order(order_id)
        if order is None:
            raise ValueError(f"order not found: {order_id}")
        current = cast(OrderStatus, order["status"])
        if current != status and status not in _ORDER_TRANSITIONS.get(current, ()):
            raise ValueError(f"invalid_order_transition:{current}->{status}")
        updated = execute_write(
            "UPDATE orders SET exchange_order_id=COALESCE(?,exchange_order_id), "
            "position_id=COALESCE(?,position_id), status=?, error_reason=?, filled_at=? "
            "WHERE id=? AND status=?",
            (
                exchange_order_id,
                position_id,
                status,
                error_reason,
                filled_at,
                order_id,
                current,
            ),
        )
        if updated.rowcount != 1:
            raise OrderTransitionConflict(f"order_transition_conflict:{order_id}")

        return get_order(order_id) or {}


def finalize_exchange_acknowledgement(
    order_id: str,
    *,
    exchange_order_id: str | None,
    status: OrderStatus,
    error_reason: str | None,
    close_position_id: str | None = None,
    pending_close_status: str | None = None,
) -> dict:
    """Apply one aggregate venue acknowledgement without overriding private fills."""
    incoming_exchange_id = str(exchange_order_id or "").strip() or None
    with transaction(immediate=True):
        order = get_order(order_id)
        if order is None:
            raise ValueError(f"order not found: {order_id}")
        current_exchange_id = str(order.get("exchange_order_id") or "").strip() or None
        if current_exchange_id and incoming_exchange_id and current_exchange_id != incoming_exchange_id:
            raise ValueError("exchange acknowledgement identity conflicts with durable Order")

        position = get_position(close_position_id) if close_position_id else None
        owns_close_claim = close_position_id is None or bool(
            position
            and order.get("position_id") == close_position_id
            and position.get("status") == "open"
            and position.get("pending_close_order_id") == order_id
        )
        applied = False
        if order.get("status") in {"pending", "submission_unknown"} and owns_close_claim:
            transition_order(
                order_id,
                exchange_order_id=current_exchange_id or incoming_exchange_id,
                status=status,
                error_reason=error_reason,
            )
            applied = True
            if applied and close_position_id:
                update_position_close_claim(
                    close_position_id,
                    order_id=order_id,
                    status=str(pending_close_status or status),
                )

        order = get_order(order_id)
        position_id = order.get("position_id") if order else close_position_id
        position = get_position(position_id) if position_id else None
        fills = [
            dict(row)
            for row in get_db()
            .execute(
                "SELECT id, fill_price, fill_quantity FROM fills WHERE order_id=? ORDER BY filled_at, rowid LIMIT 2",
                (order_id,),
            )
            .fetchall()
        ]
        fill = fills[0] if len(fills) == 1 else None

    return {
        "applied": applied,
        "order_id": order_id,
        "exchange_order_id": order.get("exchange_order_id") if order else None,
        "order_status": order.get("status") if order else None,
        "fill_id": fill.get("id") if fill else None,
        "position_id": position_id,
        "fill_price": fill.get("fill_price") if fill else None,
        "fill_quantity": fill.get("fill_quantity") if fill else 0,
        "position_status": position.get("status") if position else None,
    }


def list_orders(
    limit: int = 100,
    status: str | None = None,
    entry_plan_id: str | None = None,
) -> list[dict]:
    if entry_plan_id:
        where = "entry_plan_id = ?"
        params: tuple[object, ...] = (entry_plan_id,)
        if status:
            where += " AND status = ?"
            params += (status,)
        return _select_all("orders", where=where, params=params, limit=limit)
    if status:
        return _select_all("orders", where="status = ?", params=(status,), limit=limit)
    return _select_all("orders", limit=limit)


# Shared Order ownership definition. SQL predicates use the caller's `o` alias.
UNRESOLVED_NATIVE_ENTRY_STATES = (
    "open", "pending", "submitted", "partial", "partially_filled",
    "submission_unknown", "materialization_missing",
)
NATIVE_ENTRY_ORDER_SQL = """
    o.source = 'native' AND TRIM(COALESCE(o.close_reason, '')) = ''
    AND NOT EXISTS (
        SELECT 1 FROM positions closing_position
        WHERE closing_position.pending_close_order_id = o.id
    )
"""


def list_unresolved_native_entry_orders_for_risk() -> list[dict]:
    """Return every unresolved native entry plus its represented local state.

    This query is intentionally unbounded: truncating accepted exchange Orders
    would let Risk under-count possible exposure.  Close Orders are excluded by
    both their durable reason and the Position pending-close backlink so older
    rows that predate ``close_reason`` cannot consume a new-entry allowance.
    """
    placeholders = ",".join("?" for _ in UNRESOLVED_NATIVE_ENTRY_STATES)
    rows = (
        get_db()
        .execute(
            f"""
        SELECT
            o.*,
            {_RISK_PROPOSAL_PROJECTION},
            {_RISK_DECISION_PROJECTION},
            tp.leverage AS proposal_leverage,
            tp.market_type AS proposal_market_type,
            tp.commit_hash AS proposal_commit_hash,
            COUNT(f.id) AS persisted_fill_count,
            COALESCE(SUM(f.fill_quantity), 0.0) AS persisted_fill_quantity,
            COALESCE(SUM(
                CASE
                    WHEN f.id IS NOT NULL
                     AND (f.fill_quantity IS NULL OR f.fill_quantity <= 0)
                    THEN 1 ELSE 0
                END
            ), 0) AS invalid_fill_quantity_count,
            (
                SELECT COUNT(*)
                FROM positions linked_position
                WHERE linked_position.status = 'open'
                  AND (
                      linked_position.id = o.position_id
                      OR linked_position.order_id = o.id
                  )
            ) AS linked_open_position_count,
            (
                SELECT COUNT(*)
                FROM positions matching_position
                WHERE matching_position.status = 'open'
                  AND matching_position.id = o.position_id
                  AND matching_position.symbol = o.symbol
                  AND matching_position.market_type = o.market_type
                  AND matching_position.direction = o.direction
                  AND matching_position.execution_mode = o.execution_mode
            ) AS matching_open_position_count,
            (
                SELECT MAX(matching_position.quantity)
                FROM positions matching_position
                WHERE matching_position.status = 'open'
                  AND matching_position.id = o.position_id
                  AND matching_position.symbol = o.symbol
                  AND matching_position.market_type = o.market_type
                  AND matching_position.direction = o.direction
                  AND matching_position.execution_mode = o.execution_mode
            ) AS represented_position_quantity,
            (
                SELECT COALESCE(SUM(represented_fill.fill_quantity), 0.0)
                FROM positions representing_position
                JOIN orders represented_order
                  ON (
                      represented_order.id = representing_position.order_id
                      OR represented_order.position_id = representing_position.id
                  )
                JOIN fills represented_fill
                  ON represented_fill.order_id = represented_order.id
                WHERE representing_position.status = 'open'
                  AND representing_position.id = o.position_id
                  AND representing_position.symbol = o.symbol
                  AND representing_position.market_type = o.market_type
                  AND representing_position.direction = o.direction
                  AND representing_position.execution_mode = o.execution_mode
                  AND represented_order.source = 'native'
                  AND TRIM(COALESCE(represented_order.close_reason, '')) = ''
                  AND represented_order.id != COALESCE(
                      representing_position.pending_close_order_id,
                      ''
                  )
            ) AS represented_entry_fill_quantity,
            (
                SELECT COUNT(*)
                FROM positions representing_position
                JOIN orders represented_order
                  ON (
                      represented_order.id = representing_position.order_id
                      OR represented_order.position_id = representing_position.id
                  )
                JOIN fills represented_fill
                  ON represented_fill.order_id = represented_order.id
                WHERE representing_position.status = 'open'
                  AND representing_position.id = o.position_id
                  AND representing_position.symbol = o.symbol
                  AND representing_position.market_type = o.market_type
                  AND representing_position.direction = o.direction
                  AND representing_position.execution_mode = o.execution_mode
                  AND represented_order.source = 'native'
                  AND TRIM(COALESCE(represented_order.close_reason, '')) = ''
                  AND represented_order.id != COALESCE(
                      representing_position.pending_close_order_id,
                      ''
                  )
                  AND (
                      represented_fill.fill_quantity IS NULL
                      OR represented_fill.fill_quantity <= 0
                  )
            ) AS invalid_represented_entry_fill_count
        FROM orders o
        LEFT JOIN fills f ON f.order_id = o.id
        LEFT JOIN trade_proposals tp ON tp.id = o.proposal_id
        LEFT JOIN risk_decisions rd ON rd.id = o.risk_decision_id
        WHERE {NATIVE_ENTRY_ORDER_SQL}
          AND o.status IN ({placeholders})
        GROUP BY o.id
        ORDER BY o.created_at ASC, o.rowid ASC
        """,
            UNRESOLVED_NATIVE_ENTRY_STATES,
        )
        .fetchall()
    )
    return [dict(row) for row in rows]


# --- Fills ---


def insert_fill(f: Fill) -> None:
    _insert("fills", _stamp_provenance(f.model_dump()))
    try:
        _seed_entry_fill_outcome(f)
    except Exception as exc:
        # The exchange Fill is the authoritative fact and must never be lost
        # because its observational follow-up could not be registered.
        _sqlite_logger.warning(
            "entry fill outcome seed unavailable fill_id=%s error=%s",
            f.id,
            type(exc).__name__,
        )


def _seed_entry_fill_outcome(f: Fill) -> None:
    """Register the existing fixed-horizon owner at the actual Fill T0."""

    db = get_db()
    owner = db.execute(
        "SELECT o.entry_plan_id,ep.candidate_id,ep.venue,ep.symbol,"
        "ep.market_type,ep.direction,um.scanner_family "
        "FROM orders o JOIN entry_plans ep ON ep.id=o.entry_plan_id "
        "LEFT JOIN universe_members um ON um.id=ep.candidate_id "
        "WHERE o.id=? AND o.entry_plan_id IS NOT NULL AND o.close_reason IS NULL",
        (f.order_id,),
    ).fetchone()
    if owner is None:
        return
    fill_time = datetime.fromisoformat(f.filled_at.replace("Z", "+00:00"))
    if fill_time.tzinfo is None:
        raise ValueError("entry_fill_time_timezone_required")
    fill_at = fill_time.astimezone(timezone.utc).isoformat(timespec="microseconds")
    orderbook = db.execute(
        "SELECT spread_bps FROM orderbook_snapshots WHERE symbol=? AND exchange=? "
        "AND market_type=? AND julianday(observed_at)<=julianday(?) "
        "ORDER BY julianday(observed_at) DESC,id DESC LIMIT 1",
        (owner["symbol"], owner["venue"], owner["market_type"], fill_at),
    ).fetchone()
    funding = None
    if owner["market_type"] == "perp":
        funding = db.execute(
            "SELECT funding_rate FROM funding_rate_snapshots WHERE symbol=? "
            "AND exchange=? AND julianday(observed_at)<=julianday(?) "
            f"AND {LEGACY_DERIVATIVES_WHERE} "
            "ORDER BY julianday(observed_at) DESC,id DESC LIMIT 1",
            (owner["symbol"], owner["venue"], fill_at),
        ).fetchone()
    from aletheia.analytics.filter_evidence import build_candidate_outcomes

    outcomes = build_candidate_outcomes(
        outcome_candidate_key=f"entry-fill:{f.id}",
        venue=str(owner["venue"]),
        symbol=str(owner["symbol"]),
        market_type=str(owner["market_type"]),
        direction=str(owner["direction"]),
        scanner_family=(str(owner["scanner_family"]) if owner["scanner_family"] else None),
        regime_tag=None,
        entry_price_usd=float(f.fill_price),
        entry_spread_bps=(float(orderbook[0]) if orderbook is not None else None),
        funding_rate_at_entry=(float(funding[0]) if funding is not None else None),
        entry_ts=fill_at,
        allocation_selected=True,
        horizons=("4h",),
    )
    insert_filter_candidate_outcomes_batch(outcomes)


# --- Positions ---


def insert_position(p: Position) -> None:
    from aletheia.uta.manager import UTA_EXECUTION_MODES

    if p.execution_mode not in UTA_EXECUTION_MODES:
        raise ValueError("unsupported Position execution mode")
    _insert("positions", _stamp_provenance(p.model_dump()))


class PositionClaimConflict(RuntimeError):
    pass


def _update_owned_position_fields(
    position_id: str,
    fields: Mapping[str, object],
    *,
    allowed: frozenset[str],
    owner: str,
    required_status: str,
) -> None:
    unknown = set(fields) - allowed
    if unknown or not fields:
        detail = ",".join(sorted(unknown)) if unknown else "empty"
        raise ValueError(f"invalid_{owner}_position_fields:{detail}")
    sets = ", ".join(f"{name}=?" for name in fields)
    updated = execute_write(
        f"UPDATE positions SET {sets} WHERE id=? AND status=?",
        (*fields.values(), position_id, required_status),
    )
    if updated.rowcount != 1:
        raise PositionClaimConflict(f"{owner}_position_conflict:{position_id}")


_POSITION_MATERIALIZATION_FIELDS = frozenset({
    "quantity", "entry_price", "current_price", "entry_fee", "slippage_cost",
    "liquidation_price", "entry_fill_verified", "entry_price_source", "leverage",
    "trade_school", "reconciliation_identity", "reconciliation_evidence",
})


_POSITION_EXIT_PLAN_FIELDS = frozenset({
    "stop_price", "take_profit_price", "expected_horizon_hours", "trailing_json",
    "thesis_invalidation_json", "stop_source", "exit_plan_version",
    "exit_plan_effective_at", "exit_limit_price", "exit_limit_source",
    "exchange_tpsl_order_id", "trail_high_water", "exit_reasoning",
})


_POSITION_OBSERVATION_FIELDS = frozenset({
    "current_price", "unrealized_pnl", "unrealized_pnl_source",
    "horizon_wake_sent_at", "last_market_signal_action",
    "last_market_signal_reason", "last_market_signal_at",
    "last_market_signal_evidence",
})


_POSITION_SETTLEMENT_FIELDS = frozenset({
    "exit_fee",
    "realized_pnl", "funding_cost", "funding_source", "exit_reasoning",
    "exit_price_source", "exit_fill_verified", "outcome_eligible",
    "outcome_exclusion_reason", "reconciliation_evidence",
})


_POSITION_CLOSE_FIELDS = frozenset({
    "status", "current_price", "realized_pnl", "unrealized_pnl",
    "close_reason", "closed_at", "btc_exit_price", "entry_fee", "exit_fee",
    "funding_cost", "funding_source", "slippage_cost", "exit_reasoning",
    "close_category", "exit_price_source", "exit_fill_verified",
    "reconciliation_identity", "reconciliation_evidence",
    "outcome_semantics_version", "outcome_eligible", "outcome_exclusion_reason",
    "closed_runtime_session_id", "closed_producer_commit",
    "closed_behavior_fingerprint", "pending_close_order_id",
    "pending_close_status", "pending_close_requested_at",
})


def finalize_position_close(position_id: str, fields: Mapping[str, object]) -> None:
    """Persist one complete close snapshot with an open-state CAS."""

    unknown = set(fields) - _POSITION_CLOSE_FIELDS
    if unknown or fields.get("status") != "closed":
        detail = ",".join(sorted(unknown)) if unknown else "status"
        raise ValueError(f"invalid_position_close_fields:{detail}")
    sets = ", ".join(f"{name}=?" for name in fields)
    updated = execute_write(
        f"UPDATE positions SET {sets} WHERE id=? AND status='open'",
        (*fields.values(), position_id),
    )
    if updated.rowcount != 1:
        raise PositionClaimConflict(f"position_close_conflict:{position_id}")


def reopen_position_for_verified_repair(
    position_id: str,
    fields: Mapping[str, object],
) -> None:
    """Explicit repair-only closed-to-open CAS before replaying verified evidence."""

    if fields.get("status") != "open" or set(fields) - _POSITION_CLOSE_FIELDS:
        raise ValueError("invalid_position_reopen_fields")
    sets = ", ".join(f"{name}=?" for name in fields)
    updated = execute_write(
        f"UPDATE positions SET {sets} WHERE id=? AND status='closed'",
        (*fields.values(), position_id),
    )
    if updated.rowcount != 1:
        raise PositionClaimConflict(f"position_reopen_conflict:{position_id}")


def claim_position_close(
    position_id: str,
    *,
    order_id: str,
    status: str,
    requested_at: str,
) -> None:
    """Claim one open Position for exactly one durable close Order."""

    updated = execute_write(
        "UPDATE positions SET pending_close_order_id=?, pending_close_status=?, "
        "pending_close_requested_at=? WHERE id=? AND status='open' "
        "AND (pending_close_order_id IS NULL OR pending_close_status='error')",
        (order_id, status, requested_at, position_id),
    )
    if updated.rowcount != 1:
        raise PositionClaimConflict(f"position_close_claim_conflict:{position_id}")


def update_position_close_claim(
    position_id: str,
    *,
    order_id: str,
    status: str,
) -> None:
    updated = execute_write(
        "UPDATE positions SET pending_close_status=? "
        "WHERE id=? AND status='open' AND pending_close_order_id=?",
        (status, position_id, order_id),
    )
    if updated.rowcount != 1:
        raise PositionClaimConflict(f"position_close_claim_lost:{position_id}")


def release_position_close_claim(position_id: str, *, order_id: str) -> None:
    updated = execute_write(
        "UPDATE positions SET pending_close_order_id=NULL, pending_close_status=NULL, "
        "pending_close_requested_at=NULL WHERE id=? AND pending_close_order_id=?",
        (position_id, order_id),
    )
    if updated.rowcount != 1:
        raise PositionClaimConflict(f"position_close_release_conflict:{position_id}")


def list_positions_for_path_observation_recovery() -> list[dict]:
    """Return external open/closed Positions whose expected path may need recovery."""

    rows = get_db().execute(
        "SELECT p.* FROM positions p WHERE status IN ('open','closed') "
        f"AND {_POSITION_PATH_EXECUTION_SCOPE_SQL} ORDER BY p.rowid DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def list_strategy_positions(strategy_id: str, status: str | None = None, limit: int = 1000) -> list[dict]:
    if status:
        return _select_all(
            "positions",
            where="strategy_id = ? AND status = ?",
            params=(strategy_id, status),
            order="COALESCE(closed_at, opened_at) ASC",
            limit=limit,
        )
    return _select_all(
        "positions",
        where="strategy_id = ?",
        params=(strategy_id,),
        order="COALESCE(closed_at, opened_at) ASC",
        limit=limit,
    )


def list_closed_positions(
    since_iso: str | None = None,
    execution_mode: str | None = None,
    limit: int = 10000,
) -> list[dict]:
    """Closed positions, newest first, optionally filtered by close time / mode.

    Used by exit-attribution analytics (M0, exit-intent plan). ``since_iso`` is
    compared against ``closed_at`` (UTC ISO-8601 strings sort chronologically),
    so pass an ISO timestamp to bound the window.
    """
    clauses = ["status = 'closed'"]
    params: list = []
    if since_iso is not None:
        clauses.append("closed_at >= ?")
        params.append(since_iso)
    if execution_mode is not None:
        clauses.append("execution_mode = ?")
        params.append(execution_mode)
    return _select_all(
        "positions",
        where=" AND ".join(clauses),
        params=tuple(params),
        order="COALESCE(closed_at, opened_at) DESC",
        limit=limit,
    )


def insert_position_close_event(event: dict) -> None:
    """Append one row to the per-close ledger (exit-intent R1). One row per close EVENT
    (full close = one row; a partial reduce = one row per reduction)."""
    _insert("position_close_events", event)


def list_position_close_events(
    since_iso: str | None = None,
    execution_mode: str | None = None,
    position_id: str | None = None,
    limit: int = 10000,
) -> list[dict]:
    """Close-event ledger rows, newest first, optionally filtered."""
    clauses: list[str] = []
    params: list = []
    if since_iso is not None:
        clauses.append("closed_at >= ?")
        params.append(since_iso)
    if execution_mode is not None:
        clauses.append("execution_mode = ?")
        params.append(execution_mode)
    if position_id is not None:
        clauses.append("position_id = ?")
        params.append(position_id)
    return _select_all(
        "position_close_events",
        where=(" AND ".join(clauses) if clauses else None),
        params=tuple(params),
        order="closed_at DESC",
        limit=limit,
    )


def insert_position_disappearance_candidate(
    candidate: PositionDisappearanceCandidate,
) -> None:
    """Persist confirmation state; does not perform or authorize a close."""
    _insert("position_disappearance_candidates", candidate.model_dump())


def update_position_disappearance_candidate(candidate_id: str, **kwargs) -> None:
    _update("position_disappearance_candidates", candidate_id, kwargs)


def insert_reconciliation_integrity_incident(
    incident: ReconciliationIntegrityIncident,
) -> None:
    """Persist one incident; the partial unique index enforces one active row/type."""
    db = get_db()
    was_in_tx = db.in_transaction
    data = incident.model_dump()
    columns = ", ".join(data)
    placeholders = ", ".join("?" for _ in data)
    execute_write(
        f"INSERT INTO reconciliation_integrity_incidents ({columns}) VALUES ({placeholders})",
        list(data.values()),
    )
    if not was_in_tx:
        db.commit()


def get_active_reconciliation_integrity_incident(
    incident_type: str = "close_reopen_churn",
) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM reconciliation_integrity_incidents "
        "WHERE incident_type=? AND status='active' "
        "ORDER BY last_detected_at DESC LIMIT 1",
        (incident_type,),
    ).fetchone()
    return dict(row) if row else None


def get_reconciliation_integrity_incident(incident_id: str) -> dict | None:
    return _select_one("reconciliation_integrity_incidents", incident_id)


def update_reconciliation_integrity_incident(incident_id: str, **kwargs) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    sets = ", ".join(f"{key} = ?" for key in kwargs)
    execute_write(
        f"UPDATE reconciliation_integrity_incidents SET {sets} WHERE id = ?",
        [*kwargs.values(), incident_id],
    )
    if not was_in_tx:
        db.commit()


# --- Strategy Records ---


def get_strategy(sid: str) -> dict | None:
    return _select_one("strategy_records", sid)


def list_strategies(limit: int = 50) -> list[dict]:
    return _select_all("strategy_records", limit=limit)


# --- Knowledge Items ---


def insert_knowledge_item(k: KnowledgeItem) -> None:
    if k.content is not None and k.content_format is None:
        from aletheia.files.knowledge_document import canonicalize_inline_document

        k = k.model_copy(update=canonicalize_inline_document(k.content))
    _insert("knowledge_items", k.model_dump())


def insert_knowledge_item_if_absent(
    k: KnowledgeItem,
    *,
    title_window_hours: int | None = None,
) -> tuple[bool, str | None, str | None]:
    """Atomically insert a knowledge item unless its semantic identity exists."""
    if k.content is not None and k.content_format is None:
        from aletheia.files.knowledge_document import canonicalize_inline_document

        k = k.model_copy(update=canonicalize_inline_document(k.content))

    db = get_db()
    content_hash = str(k.content_hash or "").strip()
    with transaction(immediate=True):
        if title_window_hours is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=title_window_hours)).isoformat()
            existing = db.execute(
                """
                SELECT id FROM knowledge_items
                WHERE item_type = ? AND title = ?
                  AND replace(created_at, ' ', 'T') > replace(?, ' ', 'T')
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (k.item_type, k.title, cutoff),
            ).fetchone()
            if existing is not None:
                return False, str(existing["id"]), "title_window"

        if content_hash:
            existing = db.execute(
                """
                SELECT id FROM knowledge_items
                WHERE item_type = ? AND content_hash = ?
                ORDER BY created_at ASC, rowid ASC
                LIMIT 1
                """,
                (k.item_type, content_hash),
            ).fetchone()
            if existing is not None:
                return False, str(existing["id"]), "duplicate"

        _insert("knowledge_items", k.model_dump())
    return True, None, None


def update_knowledge_item(kid: str, **kwargs) -> None:
    _update("knowledge_items", kid, kwargs)


def get_knowledge_item(kid: str) -> dict | None:
    return _select_one("knowledge_items", kid)


def list_knowledge_items(
    item_type: str | None = None, limit: int = 50, strategy_id: str | None = None, status: str = "active"
) -> list[dict]:
    conditions = []
    params_list: list = []
    if item_type:
        conditions.append("item_type = ?")
        params_list.append(item_type)
    if strategy_id is not None:
        conditions.append("strategy_id = ?")
        params_list.append(strategy_id)
    if status != "all":
        conditions.append("(status = ? OR status IS NULL)")
        params_list.append(status)
    where = " AND ".join(conditions) if conditions else None
    return _select_all("knowledge_items", where=where, params=tuple(params_list), limit=limit)


def list_pending_trade_case_studies(
    limit: int = 10,
    *,
    after_cursor: str | None = None,
    before_cursor: str | None = None,
) -> list[dict]:
    """Return oldest active trade cases without active lesson evidence."""

    bounded_limit = max(1, min(int(limit), 100))
    where = """
        ki.item_type = 'trade_case_study'
        AND (ki.status = 'active' OR ki.status IS NULL)
        AND NOT EXISTS (
            SELECT 1
            FROM ps_lessons AS pl
            WHERE pl.source_type = 'trade_case_study'
              AND (pl.status IS NULL OR pl.status IN ('proposed', 'accepted', 'applied'))
              AND (
                  pl.source_id = ki.id
                  OR instr(pl.evidence_json, '"' || ki.id || '"') > 0
              )
        )
    """
    params: list[str | int] = []
    if after_cursor:
        created_at, separator, case_id = str(after_cursor).partition("|")
        if separator and created_at and case_id:
            where += """
                AND (
                    ki.created_at > ?
                    OR (ki.created_at = ? AND ki.id > ?)
                )
            """
            params.extend((created_at, created_at, case_id))
    if before_cursor:
        created_at, separator, case_id = str(before_cursor).partition("|")
        if separator and created_at and case_id:
            where += """
                AND (
                    ki.created_at < ?
                    OR (ki.created_at = ? AND ki.id < ?)
                )
            """
            params.extend((created_at, created_at, case_id))
    rows = (
        get_db()
        .execute(
            f"""
        SELECT ki.*
        FROM knowledge_items AS ki
        WHERE {where}
        ORDER BY ki.created_at ASC, ki.id ASC
        LIMIT ?
        """,
            (*params, bounded_limit),
        )
        .fetchall()
    )
    return [dict(row) for row in rows]


def pending_trade_case_studies_summary() -> tuple[int, str | None]:
    """Return pending case count and oldest creation time for job health."""

    row = (
        get_db()
        .execute(
            """
        SELECT COUNT(*) AS pending_count, MIN(ki.created_at) AS oldest_created_at
        FROM knowledge_items AS ki
        WHERE ki.item_type = 'trade_case_study'
          AND (ki.status = 'active' OR ki.status IS NULL)
          AND NOT EXISTS (
              SELECT 1
              FROM ps_lessons AS pl
              WHERE pl.source_type = 'trade_case_study'
                AND (pl.status IS NULL OR pl.status IN ('proposed', 'accepted', 'applied'))
                AND (
                    pl.source_id = ki.id
                    OR instr(pl.evidence_json, '"' || ki.id || '"') > 0
                )
          )
        """
        )
        .fetchone()
    )
    return int(row["pending_count"] or 0), row["oldest_created_at"]


def knowledge_hash_exists(item_type: str, content_hash: str) -> bool:
    """Check if a knowledge item with this content hash already exists."""
    rows = _select_all(
        "knowledge_items",
        where="item_type = ? AND content_hash = ?",
        params=(item_type, content_hash),
        limit=1,
    )
    return len(rows) > 0


def knowledge_title_exists_within(item_type: str, title: str, hours: int = 6) -> bool:
    """Check if a knowledge item with same type+title was created within N hours."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    row = db.execute(
        "SELECT 1 FROM knowledge_items WHERE item_type = ? AND title = ? AND created_at > datetime('now', ?) LIMIT 1",
        (item_type, title, f"-{hours} hours"),
    ).fetchone()
    return row is not None


# --- Serenity Lens ---


def insert_serenity_theme(t: SerenityTheme) -> None:
    _insert("serenity_themes", t.model_dump())


def insert_serenity_theme_with_claim_links(
    theme: SerenityTheme,
    links: list[tuple[str, str, float | None]],
) -> None:
    with transaction(savepoint=True):
        insert_serenity_theme(theme)
        insert_serenity_theme_claims_bulk(links)


def get_serenity_theme(theme_id: str) -> dict | None:
    return _select_one("serenity_themes", theme_id)


def list_serenity_themes(status: str | None = "active", limit: int = 100) -> list[dict]:
    where: str | None = None
    params: list = []
    if status:
        where = "status = ?"
        params.append(status)
    return _select_all("serenity_themes", where=where, params=tuple(params), limit=limit)


def update_serenity_theme(theme_id: str, **kwargs) -> None:
    _update("serenity_themes", theme_id, kwargs)


def insert_serenity_evidence(e: SerenityEvidence) -> list[str]:
    from aletheia.trading.serenity_evidence import (
        assess_opportunity_tier,
        assess_researchability,
    )

    with transaction(immediate=True, savepoint=True):
        _insert("serenity_evidence", e.model_dump())
        evidence = list_serenity_evidence(theme_id=e.theme_id, limit=1000)
        assessed_tier, assessed_by = assess_opportunity_tier({}, evidence)
        researchability_grade, _ = assess_researchability(evidence)
        now = datetime.now(timezone.utc).isoformat()
        execute_write(
            "UPDATE serenity_opportunities SET assessed_tier=?, assessed_by=?, "
            "researchability_grade=?, updated_at=? "
            "WHERE theme_id=? AND status='active'",
            (
                assessed_tier,
                assessed_by,
                researchability_grade,
                now,
                e.theme_id,
            ),
        )
        active_ids = [
            str(row["id"])
            for row in get_db()
            .execute(
                "SELECT id FROM serenity_opportunities WHERE theme_id=? AND status='active' ORDER BY rowid ASC",
                (e.theme_id,),
            )
            .fetchall()
        ]
    return active_ids


def list_serenity_evidence(theme_id: str | None = None, limit: int = 100) -> list[dict]:
    where: str | None = None
    params: list = []
    if theme_id is not None:
        where = "theme_id = ?"
        params.append(theme_id)
    return _select_all("serenity_evidence", where=where, params=tuple(params), limit=limit)


def insert_serenity_opportunity(o: SerenityOpportunity) -> None:
    data = o.model_dump()
    _insert("serenity_opportunities", data)
    try:
        from aletheia.analytics.causal_controls import record_serenity_control_cohort

        record_serenity_control_cohort(data)
    except Exception:
        logger.warning("repositories: control cohort record failed for serenity opportunity", exc_info=True)


def get_serenity_opportunity(opportunity_id: str) -> dict | None:
    return _select_one("serenity_opportunities", opportunity_id)


def list_serenity_opportunities(
    theme_id: str | None = None,
    status: str | None = None,
    stage: str | None = None,
    limit: int = 100,
) -> list[dict]:
    conditions: list[str] = []
    params: list = []
    if theme_id is not None:
        conditions.append("theme_id = ?")
        params.append(theme_id)
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    if stage is not None:
        conditions.append("stage = ?")
        params.append(stage)
    where = " AND ".join(conditions) if conditions else None
    return _select_all("serenity_opportunities", where=where, params=tuple(params), limit=limit)


def update_serenity_opportunity(opp_id: str, **kwargs) -> None:
    _update("serenity_opportunities", opp_id, kwargs)


def transition_active_serenity_opportunities_for_theme(
    theme_id: str,
    *,
    status: str,
    updated_at: str,
) -> int:
    result = execute_write(
        "UPDATE serenity_opportunities SET status=?, updated_at=? "
        "WHERE theme_id=? AND status='active'",
        (status, updated_at, theme_id),
    )
    return result.rowcount


def transition_serenity_theme_status(
    theme_id: str,
    *,
    status: str,
    updated_at: str,
) -> int | None:
    """Update one theme and atomically terminalize all active children."""
    with transaction(immediate=True):
        theme = execute_write(
            "UPDATE serenity_themes SET status=?, updated_at=? WHERE id=?",
            (status, updated_at, theme_id),
        )
        if theme.rowcount != 1:
            return None
        if status not in {"dismissed", "expired"}:
            return 0
        return transition_active_serenity_opportunities_for_theme(
            theme_id,
            status=status,
            updated_at=updated_at,
        )


def insert_serenity_risk_gate(g: SerenityRiskGate) -> None:
    _insert("serenity_risk_gates", g.model_dump())


def list_serenity_risk_gates(opportunity_id: str, limit: int = 100) -> list[dict]:
    return _select_all("serenity_risk_gates", where="opportunity_id = ?", params=(opportunity_id,), limit=limit)


def insert_serenity_replay_snapshot(s: SerenityReplaySnapshot) -> None:
    _insert("serenity_replay_snapshots", s.model_dump())


def list_serenity_replay_snapshots(opportunity_id: str, limit: int = 200) -> list[dict]:
    return _select_all(
        "serenity_replay_snapshots",
        where="opportunity_id = ?",
        params=(opportunity_id,),
        order="created_at DESC",
        limit=limit,
    )


def insert_serenity_post_mortem(p: SerenityPostMortem) -> bool:
    data = p.model_dump()
    if not p.position_id:
        _insert("serenity_post_mortems", data)
        return True

    cols = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    cursor = execute_write(
        f"INSERT INTO serenity_post_mortems ({cols}) VALUES ({placeholders}) ON CONFLICT(position_id) DO NOTHING",
        list(data.values()),
    )
    if cursor.rowcount == 1:
        return True
    existing = (
        get_db()
        .execute(
            "SELECT opportunity_id FROM serenity_post_mortems WHERE position_id = ?",
            (p.position_id,),
        )
        .fetchone()
    )
    if existing is None or existing["opportunity_id"] != p.opportunity_id:
        raise ValueError("Serenity post-mortem Position identity conflict")
    return False


def list_serenity_post_mortems(opportunity_id: str | None = None, limit: int = 100) -> list[dict]:
    conditions = ["invalidated_at IS NULL"]
    params: list = []
    if opportunity_id is not None:
        conditions.append("opportunity_id = ?")
        params.append(opportunity_id)
    return _select_all(
        "serenity_post_mortems",
        where=" AND ".join(conditions),
        params=tuple(params),
        limit=limit,
    )


# --- Durable Outcome hook outbox ---


def enqueue_outcome_hook_job(
    position_id: str,
    hook_type: str,
    *,
    next_attempt_at: str | None = None,
) -> dict:
    """Insert one durable hook intent, preserving an existing retry/completion row."""
    job = OutcomeHookJob(
        position_id=position_id,
        hook_type=hook_type,
        **({"next_attempt_at": next_attempt_at} if next_attempt_at is not None else {}),
    )
    data = job.model_dump()
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    execute_write(
        f"INSERT INTO outcome_hook_jobs ({cols}) VALUES ({placeholders}) "
        "ON CONFLICT(position_id, hook_type) DO NOTHING",
        list(data.values()),
    )
    row = (
        get_db()
        .execute(
            "SELECT * FROM outcome_hook_jobs WHERE position_id = ? AND hook_type = ?",
            (position_id, hook_type),
        )
        .fetchone()
    )
    if row is None:
        raise RuntimeError("Outcome hook job insert did not persist")
    return dict(row)


def claim_outcome_hook_job(
    *,
    position_id: str | None = None,
    hook_type: str | None = None,
    now: str | None = None,
    stale_after_seconds: int = 300,
) -> dict | None:
    """Atomically lease one due job and recover abandoned processing leases."""
    from aletheia.storage.models import new_id

    now_dt = _parse_outcome_hook_time(now)
    now_iso = now_dt.isoformat()
    stale_before = (now_dt - timedelta(seconds=max(0, stale_after_seconds))).isoformat()
    with transaction(immediate=True):
        db = get_db()
        execute_write(
            "UPDATE outcome_hook_jobs SET status = 'failed', "
            "next_attempt_at = ?, locked_at = NULL, claim_token = NULL, "
            "last_error = 'stale_processing_lease', updated_at = ? "
            "WHERE status = 'processing' AND locked_at IS NOT NULL "
            "AND datetime(locked_at) <= datetime(?)",
            (now_iso, now_iso, stale_before),
        )
        conditions = [
            "status IN ('pending', 'failed')",
            "datetime(next_attempt_at) <= datetime(?)",
        ]
        params: list[object] = [now_iso]
        if position_id is not None:
            conditions.append("position_id = ?")
            params.append(position_id)
        if hook_type is not None:
            conditions.append("hook_type = ?")
            params.append(hook_type)
        row = db.execute(
            "SELECT * FROM outcome_hook_jobs WHERE "
            + " AND ".join(conditions)
            + " ORDER BY datetime(next_attempt_at) ASC, datetime(created_at) ASC, id ASC "
            "LIMIT 1",
            params,
        ).fetchone()
        if row is None:
            return None
        claim_token = new_id()
        cursor = execute_write(
            "UPDATE outcome_hook_jobs SET status = 'processing', attempts = attempts + 1, "
            "locked_at = ?, claim_token = ?, updated_at = ? "
            "WHERE id = ? AND status IN ('pending', 'failed')",
            (now_iso, claim_token, now_iso, row["id"]),
        )
        if cursor.rowcount != 1:
            return None
        claimed = db.execute(
            "SELECT * FROM outcome_hook_jobs WHERE id = ?",
            (row["id"],),
        ).fetchone()
        return dict(claimed) if claimed is not None else None


def mark_outcome_hook_job_completed(job_id: str, claim_token: str, *, now: str | None = None) -> bool:
    now_iso = _parse_outcome_hook_time(now).isoformat()
    cursor = execute_write(
        "UPDATE outcome_hook_jobs SET status = 'completed', completed_at = ?, "
        "updated_at = ?, locked_at = NULL, claim_token = NULL, last_error = NULL "
        "WHERE id = ? AND status = 'processing' AND claim_token = ?",
        (now_iso, now_iso, job_id, claim_token),
    )
    return cursor.rowcount == 1


def mark_outcome_hook_job_skipped(
    job_id: str,
    claim_token: str,
    reason: str,
    *,
    now: str | None = None,
) -> bool:
    now_iso = _parse_outcome_hook_time(now).isoformat()
    cursor = execute_write(
        "UPDATE outcome_hook_jobs SET status = 'skipped', completed_at = ?, "
        "updated_at = ?, locked_at = NULL, claim_token = NULL, last_error = ? "
        "WHERE id = ? AND status = 'processing' AND claim_token = ?",
        (now_iso, now_iso, reason[:1000], job_id, claim_token),
    )
    return cursor.rowcount == 1


def mark_outcome_hook_job_failed(
    job_id: str,
    claim_token: str,
    error: str,
    *,
    now: str | None = None,
    retry_delay_seconds: int = 60,
) -> bool:
    now_dt = _parse_outcome_hook_time(now)
    now_iso = now_dt.isoformat()
    retry_at = (now_dt + timedelta(seconds=max(1, retry_delay_seconds))).isoformat()
    cursor = execute_write(
        "UPDATE outcome_hook_jobs SET status = 'failed', next_attempt_at = ?, "
        "updated_at = ?, locked_at = NULL, claim_token = NULL, last_error = ? "
        "WHERE id = ? AND status = 'processing' AND claim_token = ?",
        (retry_at, now_iso, error[:1000], job_id, claim_token),
    )
    return cursor.rowcount == 1


def _parse_outcome_hook_time(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# --- Serenity Research Runs ---


def insert_serenity_research_run(r) -> None:
    _insert("serenity_research_runs", r.model_dump())


def get_serenity_research_retry(parent_run_id: str, retry_count: int) -> dict | None:
    row = (
        get_db()
        .execute(
            "SELECT * FROM serenity_research_runs "
            "WHERE parent_run_id = ? AND retry_count = ? "
            "ORDER BY created_at, id LIMIT 1",
            (parent_run_id, retry_count),
        )
        .fetchone()
    )
    return dict(row) if row else None


def insert_serenity_research_retry(r) -> tuple[dict, bool]:
    """Insert one durable retry identity, returning the winner on a race."""

    retry_key = getattr(r, "idempotency_key", None)
    if not r.parent_run_id or not isinstance(retry_key, str) or not retry_key.startswith("serenity-retry:"):
        raise ValueError("Serenity retry identity is required")
    try:
        insert_serenity_research_run(r)
    except sqlite3.IntegrityError:
        existing = get_serenity_research_retry(r.parent_run_id, r.retry_count)
        if existing is None:
            raise
        return existing, True
    inserted = get_serenity_research_run(r.id)
    if inserted is None:
        raise RuntimeError("inserted Serenity retry is unavailable")
    return inserted, False


def get_serenity_research_run(run_id: str) -> dict | None:
    return _select_one("serenity_research_runs", run_id)


def list_serenity_research_runs(
    status: str | None = None,
    trigger_type: str | None = None,
    limit: int = 20,
) -> list[dict]:
    conditions: list[str] = []
    params: list = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if trigger_type:
        conditions.append("trigger_type = ?")
        params.append(trigger_type)
    where = " AND ".join(conditions) if conditions else None
    return _select_all(
        "serenity_research_runs", where=where, params=tuple(params), order="created_at DESC", limit=limit
    )


def update_serenity_research_run(run_id: str, **kwargs) -> None:
    _update("serenity_research_runs", run_id, kwargs)


def claim_serenity_research_run(run_id: str, *, now: str) -> bool:
    """Claim one queued run without reviving a concurrently cancelled run."""
    cursor = execute_write(
        "UPDATE serenity_research_runs SET status = 'running', started_at = ?, "
        "updated_at = ? WHERE id = ? AND status = 'queued'",
        (now, now, run_id),
    )
    return cursor.rowcount == 1


def fail_unclaimed_serenity_research_run(
    run_id: str,
    *,
    error_type: str,
    error_message: str | None,
    error_stage: str | None,
    now: str,
) -> bool:
    """Fail a queued run when worker startup crashes before the claim commits."""
    cursor = execute_write(
        "UPDATE serenity_research_runs SET status = 'failed', error_type = ?, "
        "error_message = ?, error_stage = ?, finished_at = ?, updated_at = ? "
        "WHERE id = ? AND status = 'queued'",
        (error_type, error_message, error_stage, now, now, run_id),
    )
    return cursor.rowcount == 1


def request_serenity_research_run_cancel(run_id: str, *, now: str) -> tuple[str | None, bool]:
    """Cancel queued work or request cooperative cancellation of running work."""
    queued = execute_write(
        "UPDATE serenity_research_runs SET status = 'cancelled', finished_at = ?, "
        "updated_at = ? WHERE id = ? AND status = 'queued'",
        (now, now, run_id),
    )
    if queued.rowcount == 1:
        return "cancelled", True
    running = execute_write(
        "UPDATE serenity_research_runs SET status = 'cancel_requested', updated_at = ? "
        "WHERE id = ? AND status = 'running'",
        (now, run_id),
    )
    if running.rowcount == 1:
        return "cancel_requested", True
    current = get_serenity_research_run(run_id)
    return (str(current["status"]) if current else None), False


def acknowledge_serenity_research_run_cancel(run_id: str, *, now: str) -> bool:
    """Write terminal cancellation only after the running worker has stopped."""
    cursor = execute_write(
        "UPDATE serenity_research_runs SET status = 'cancelled', finished_at = ?, "
        "next_retry_at = NULL, updated_at = ? "
        "WHERE id = ? AND status = 'cancel_requested'",
        (now, now, run_id),
    )
    return cursor.rowcount == 1


def finish_serenity_research_run_with_event(run_id: str, status: str, event, **kwargs) -> bool:
    """Commit a terminal run transition and its terminal event atomically."""
    if status not in {"completed", "completed_empty", "failed", "rate_limited"}:
        raise ValueError(f"invalid Serenity terminal status: {status}")
    allowed = {
        "stage",
        "finished_at",
        "updated_at",
        "artifact_counts",
        "error_type",
        "error_message",
        "error_stage",
        "next_retry_at",
    }
    unknown = set(kwargs) - allowed
    if unknown:
        raise ValueError(f"invalid Serenity terminal fields: {sorted(unknown)}")
    values = {"status": status, **kwargs}
    sets = ", ".join(f"{key} = ?" for key in values)
    with transaction(savepoint=True):
        cursor = execute_write(
            f"UPDATE serenity_research_runs SET {sets} WHERE id = ? AND status = 'running'",
            [*values.values(), run_id],
        )
        if cursor.rowcount != 1:
            return False
        insert_serenity_run_event(event)
    return True


def get_active_serenity_research_run() -> dict | None:
    rows = list_active_serenity_research_runs(limit=1)
    return rows[0] if rows else None


def list_active_serenity_research_runs(limit: int = 100) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM serenity_research_runs "
        "WHERE status IN ('queued', 'running', 'cancel_requested', 'waiting_retry') "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_latest_successful_serenity_research_run(
    *, not_after: str | None = None,
) -> dict | None:
    db = get_db()
    time_limit = (
        "AND julianday(COALESCE(finished_at, created_at)) <= julianday(?) "
        if not_after is not None else ""
    )
    row = db.execute(
        "SELECT * FROM serenity_research_runs "
        "WHERE status IN ('completed', 'completed_empty') "
        f"{time_limit}"
        "ORDER BY COALESCE(finished_at, created_at) DESC LIMIT 1",
        (not_after,) if not_after is not None else (),
    ).fetchone()
    return dict(row) if row else None


def get_serenity_research_run_by_idempotency_key(key: str) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM serenity_research_runs WHERE idempotency_key = ?", (key,)).fetchone()
    return dict(row) if row else None


# --- Serenity Run Events ---


def insert_serenity_run_event(e) -> None:
    _insert("serenity_run_events", e.model_dump())


def list_serenity_run_events(run_id: str, limit: int = 200) -> list[dict]:
    return _select_all("serenity_run_events", where="run_id = ?", params=(run_id,), order="created_at ASC", limit=limit)


# --- Serenity User Profiles ---


def get_serenity_user_profile() -> dict | None:
    rows = _select_all("serenity_user_profiles", order="updated_at DESC", limit=1)
    return rows[0] if rows else None


def upsert_serenity_user_profile(profile) -> None:
    existing = get_serenity_user_profile()
    if existing:
        _update("serenity_user_profiles", existing["id"], profile.model_dump())
    else:
        _insert("serenity_user_profiles", profile.model_dump())


def update_serenity_user_profile_fields(profile_id: str, **kwargs) -> None:
    _update("serenity_user_profiles", profile_id, kwargs)


# --- Serenity Profile Snapshots ---


def insert_serenity_profile_snapshot(snap) -> None:
    _insert("serenity_profile_snapshots", snap.model_dump())


def get_serenity_profile_snapshot(snapshot_id: str) -> dict | None:
    return _select_one("serenity_profile_snapshots", snapshot_id)


# --- Serenity Feedback Events ---


def insert_serenity_feedback_event(e) -> None:
    _insert("serenity_feedback_events", e.model_dump())


def list_serenity_feedback_events(target_type: str, target_id: str, limit: int = 50) -> list[dict]:
    return _select_all(
        "serenity_feedback_events",
        where="target_type = ? AND target_id = ?",
        params=(target_type, target_id),
        limit=limit,
    )


# --- Serenity Claims ---


def insert_serenity_claim(c) -> None:
    data = c.model_dump()
    data["price_relevant"] = int(data.get("price_relevant", False))
    _insert("serenity_claims", data)


def list_serenity_claims(run_id: str | None = None, price_relevant: bool | None = None, limit: int = 200) -> list[dict]:
    where_parts = []
    params: list = []
    if run_id:
        where_parts.append("run_id = ?")
        params.append(run_id)
    if price_relevant is not None:
        where_parts.append("price_relevant = ?")
        params.append(int(price_relevant))
    where = " AND ".join(where_parts) if where_parts else None
    return _select_all("serenity_claims", where=where, params=tuple(params), limit=limit)


# --- Serenity Theme-Claims Link ---


def insert_serenity_theme_claims_bulk(links: list[tuple[str, str, float | None]]) -> None:
    with transaction(savepoint=True):
        for theme_id, claim_id, relevance_score in links:
            execute_write(
                "INSERT OR IGNORE INTO serenity_theme_claims (theme_id, claim_id, relevance_score) VALUES (?, ?, ?)",
                (theme_id, claim_id, relevance_score),
            )


def list_serenity_claims_for_theme(theme_id: str, limit: int = 200) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT c.*, tc.relevance_score FROM serenity_claims c "
        "JOIN serenity_theme_claims tc ON tc.claim_id = c.id "
        "WHERE tc.theme_id = ? ORDER BY c.created_at DESC LIMIT ?",
        (theme_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Serenity Agent Locks ---


def acquire_serenity_lock(lock_key: str, owner_run_id: str, ttl_seconds: int = 600) -> bool:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()

    # Atomic acquire: purge expired, then INSERT OR IGNORE (PK = lock_key).
    # Two concurrent threads both DELETE expired + INSERT OR IGNORE: only one
    # INSERT succeeds (the other is a no-op due to PK constraint). No TOCTOU.
    execute_write(
        "DELETE FROM serenity_agent_locks WHERE lock_key = ? AND expires_at < ?",
        (lock_key, now.isoformat()),
    )
    inserted = execute_write(
        "INSERT OR IGNORE INTO serenity_agent_locks "
        "(lock_key, owner_run_id, acquired_at, expires_at) VALUES (?, ?, ?, ?)",
        (lock_key, owner_run_id, now.isoformat(), expires_at),
    )
    return inserted.rowcount > 0


def release_serenity_lock(lock_key: str, owner_run_id: str) -> bool:
    result = execute_write(
        "DELETE FROM serenity_agent_locks WHERE lock_key = ? AND owner_run_id = ?",
        (lock_key, owner_run_id),
    )
    return result.rowcount > 0


# --- Market Snapshots ---


def insert_market_snapshot(snap) -> None:
    _insert("market_snapshots", snap.model_dump())


def insert_market_snapshot_batch(snaps) -> int:
    """Persist a list of MarketSnapshot objects in one transaction.

    Replaces N individual INSERT+COMMIT calls with a single executemany
    so that collect_market_snapshots acquires the write lock exactly once
    per collection cycle instead of once per ticker.  Returns the row count.
    """
    rows = [s.model_dump() for s in snaps]
    return _insert_many("market_snapshots", rows)


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
    rows = get_db().execute(
        "SELECT exchange, market_type, MAX(snapshot_at) AS snapshot_at "
        "FROM market_snapshots GROUP BY exchange, market_type"
    ).fetchall()
    return [dict(row) for row in rows]


# --- Portfolio Snapshots ---


def insert_portfolio_snapshot(snap) -> None:
    from aletheia.uta.manager import UTA_EXECUTION_MODES

    if snap.execution_mode not in {*UTA_EXECUTION_MODES, "exchange_account"}:
        raise ValueError("unsupported PortfolioSnapshot execution mode")
    _insert("portfolio_snapshots", snap.model_dump())


def _snapshot_cutoff_us(days: int) -> int:
    return iso_instant_us((datetime.now(timezone.utc) - timedelta(days=days)).isoformat()) or 0


def list_portfolio_snapshots(limit: int | None = None, days: int | None = None) -> list[dict]:
    """Return the latest snapshot window in chronological order.

    When `days` is provided it is the authoritative range selector: all rows
    inside the window are returned so range selectors (7D/30D/90D/All) stay
    distinct even when the table grows beyond any cap. `limit` is an optional
    safety cap that takes the most-recent rows inside the window.

    Without `days`, `limit` guards against returning the entire table; it caps
    from the newest end of the full series (default 100 rows).
    """
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
    rows = _select_all("portfolio_snapshots", order="snapshot_at DESC, id DESC", limit=1)
    return rows[0] if rows else None


# --- Agent Tool Calls ---


def _step_timestamp() -> str:
    """UTC timestamp with microsecond precision for step/tool-call ordering.

    Phase 3 A8 removed the double-write to `agent_run_steps`, so `list_run_steps`
    now UNIONs `agent_run_steps` with `agent_tool_calls` at read time. To keep
    cross-table ordering stable when several rows are inserted in the same
    wall-clock second (common in tests and hot loops), both writers stamp
    `created_at` with microsecond precision here rather than relying on the
    column DEFAULT `datetime('now')` which is only second-precision.

    Format matches SQLite's `datetime()` shape (space separator, optional
    fractional seconds) so `datetime()`/`julianday()` comparisons and lexical
    sort agree.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def list_tool_calls(run_id: str | None = None, limit: int = 100) -> list[dict]:
    if run_id:
        try:
            from aletheia.agents.helpers import flush_run_telemetry

            flush_run_telemetry(run_id)
        except Exception:
            logger.warning("repositories: flush_run_telemetry failed", exc_info=True)
        return _select_all("agent_tool_calls", where="run_id = ?", params=(run_id,), limit=limit)
    try:
        from aletheia.agents.helpers import flush_all_agent_telemetry

        flush_all_agent_telemetry()
    except Exception:
        logger.warning("repositories: flush_all_agent_telemetry failed", exc_info=True)
    return _select_all("agent_tool_calls", limit=limit)


def insert_agent_telemetry_batch(events: list[dict]) -> int:
    """Flush buffered, non-authoritative Agent trace telemetry in one transaction."""

    from aletheia.storage.models import new_id

    tool_rows: list[dict] = []
    step_rows: list[dict] = []
    for event in events:
        created_at = event.get("created_at") or _step_timestamp()
        if event.get("kind") == "tool":
            tool_rows.append(
                {
                    "id": new_id(),
                    "run_id": event["run_id"],
                    "tool_name": event["tool_name"],
                    "tool_class": event["tool_class"],
                    "input_data": event.get("input_data"),
                    "output_data": event.get("output_data"),
                    "permitted": 1 if event.get("permitted") else 0,
                    "inference_call_id": event.get("inference_call_id"),
                    "duration_ms": event.get("duration_ms"),
                    "created_at": created_at,
                }
            )
        elif event.get("kind") == "step":
            step_rows.append(
                {
                    "id": new_id(),
                    "run_id": event["run_id"],
                    "step_number": event["step_number"],
                    "action": event["action"],
                    "input_data": event.get("input_data"),
                    "output_data": event.get("output_data"),
                    "created_at": created_at,
                }
            )
    with transaction():
        _insert_many("agent_tool_calls", tool_rows)
        _insert_many("agent_run_steps", step_rows)
    return len(tool_rows) + len(step_rows)


def list_run_steps(run_id: str, limit: int = 500) -> list[dict]:
    """Return a run's reasoning/action trace in execution order.

    UNIONs `agent_run_steps` (non-tool reasoning recorded via `record_step`) with
    `agent_tool_calls` (tool events recorded via `log_tool`) so the trace stays
    complete after Phase 3 A8 removed the double-write to `agent_run_steps`.
    Rows are merged by `created_at` (with `rowid` as an intra-table tiebreaker)
    and `step_number` is reassigned by position so the merged sequence numbers
    1..N regardless of what the writer stored. Tool-call rows are surfaced with
    `action = 'tool:<tool_name>'` and a `source='tool_call'` marker so callers
    that want to style them distinctly can.
    """
    try:
        from aletheia.agents.helpers import flush_run_telemetry

        flush_run_telemetry(run_id)
    except Exception:
        logger.warning("repositories: flush_run_telemetry failed in list_run_steps", exc_info=True)
    db = get_db()
    steps = db.execute(
        "SELECT id, run_id, action, input_data, output_data, created_at, "
        "rowid AS _rowid "
        "FROM agent_run_steps WHERE run_id = ? ORDER BY created_at ASC, rowid ASC "
        "LIMIT ?",
        (run_id, limit),
    ).fetchall()
    tool_calls = db.execute(
        "SELECT id, run_id, tool_name, tool_class, permitted, input_data, "
        "output_data, created_at, rowid AS _rowid "
        "FROM agent_tool_calls WHERE run_id = ? ORDER BY created_at ASC, rowid ASC "
        "LIMIT ?",
        (run_id, limit),
    ).fetchall()
    merged: list[dict] = []
    for raw in steps:
        row = dict(raw)
        row["source"] = "step"
        merged.append(row)
    for raw in tool_calls:
        row = dict(raw)
        permitted = bool(row.pop("permitted", 1))
        tool_name = row.pop("tool_name", "") or ""
        row.pop("tool_class", None)
        row["action"] = f"tool:{tool_name}" + ("" if permitted else " (denied)")
        row["source"] = "tool_call"
        merged.append(row)
    merged.sort(
        key=lambda r: (
            r.get("created_at") or "",
            0 if r.get("source") == "step" else 1,
            r.get("_rowid") or 0,
        )
    )
    merged = merged[:limit]
    for idx, row in enumerate(merged, start=1):
        row["step_number"] = idx
        row.pop("_rowid", None)
    return merged


# --- Data retention / cleanup (Component 3) ---
#
# Only high-volume TELEMETRY may be pruned. Trust-bearing causal-chain tables
# (trade_proposals, risk_decisions, orders, fills, positions), audit_events, and
# knowledge_items are intentionally absent from this whitelist — their permanence
# is the safety guarantee (trust §1.1 + AGENTS.md "hard deletion forbidden").
_PRUNABLE = {
    "agent_runs": "started_at",
    "agent_run_steps": "created_at",
    "agent_tool_calls": "created_at",
    "market_snapshots": "snapshot_at",
    "portfolio_snapshots": "snapshot_at",
    # Analytics/telemetry tables that grew unbounded with no prune path (DG-1):
    # serenity_claims (research evidence) + shadow_trades (counterfactual analysis).
    # NOT causal-chain tables — safe to prune old rows. 90-day retention.
    "serenity_claims": "created_at",
    "shadow_trades": "created_at",
    # Filter evidence is analytical and reproducible from source decisions. Keep
    # enough history for paired cohorts without allowing 10-minute batches to
    # grow the SQLite file forever. Authoritative RiskDecision/order rows remain
    # outside this whitelist.
    "filter_decision_batches": "evaluated_at",
    "filter_decisions": "evaluated_at",
    "filter_candidate_outcomes": "entry_ts",
    # No-candidate heartbeat observations are operational telemetry only. They
    # never authorize an entry or represent a candidate outcome, so a 30-day
    # window preserves diagnosis while preventing a five-minute heartbeat stream
    # from growing the local SQLite database indefinitely.
    "operator_evaluation_events": "created_at",
    "ohlcv_backfill_state": "updated_at",
    # Phase 3 data-retention additions (2026-07-17). Telemetry-only tables that
    # were unbounded prior to this pass. Audit-chain + learning tables remain
    # intentionally absent (see AGENTS.md hard rules). Only entries where the
    # column is an ISO-8601 timestamp are listed — bespoke-column tables
    # (integer-ms `ts` on market_ohlcv/backtest_trades, cascade-only children
    # like universe_members/filter_shadow_evaluations) rely on their dedicated
    # prune_* functions.
    "alpha_control_observations": "entry_ts",
    "controller_recommendations": "created_at",
    "agent_shell_sessions": "created_at",
    "agent_shell_commands": "created_at",
    "agent_shell_events": "created_at",
    "agent_shell_heartbeats": "created_at",
    "agent_shell_messages": "created_at",
    "agent_shell_artifacts": "created_at",
    "universe_snapshots": "created_at",
    "universe_scanner_runs": "started_at",
    "universe_feature_snapshots": "ts",
    "market_regimes": "detected_at",
    "serenity_evidence": "created_at",
    "serenity_run_events": "created_at",
    "runtime_sessions": "started_at",
    "backtest_runs": "created_at",
    "filter_shadow_experiments": "started_at",
}

_CONDITIONALLY_PRUNABLE = {
    # pi_decisions contains both transient queue rows and causal decision rows.
    # It is prunable only with an explicit action filter supplied by the caller.
    "pi_decisions": "created_at",
    # knowledge_items is a causal table, but system_action rows are pure
    # telemetry noise (1200+/day) that floods the limit-30 window. Pruning
    # ONLY item_type='system_action' older than N days is safe because A2
    # fix made PI query with explicit item_type filters.
    "knowledge_items": "created_at",
}


def _delete_old_rows(
    table: str,
    days: int,
    extra_where: str | None = None,
    extra_params: tuple = (),
) -> int:
    """Delete rows in a WHITELISTED telemetry table older than `days`. Returns count."""
    if table in _PRUNABLE:
        ts_col = _PRUNABLE[table]
    elif table in _CONDITIONALLY_PRUNABLE and extra_where:
        ts_col = _CONDITIONALLY_PRUNABLE[table]
    else:
        raise ValueError(f"table {table!r} is not prunable")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    where = f"{ts_col} < ?"
    params = (cutoff,)
    if extra_where:
        where = f"({where}) AND ({extra_where})"
        params = (*params, *extra_params)
    return _delete_by_rowid_batches(table, where, params)


def _delete_by_rowid_batches(
    table: str,
    where: str,
    params: tuple = (),
    *,
    batch_size: int = 5_000,
) -> int:
    """Delete telemetry in bounded writer transactions and publish batch metrics."""

    db = get_db()
    total = 0
    size = max(1, int(batch_size))
    while True:
        rowids = [
            row[0]
            for row in db.execute(
                f"SELECT rowid FROM {table} WHERE {where} ORDER BY rowid LIMIT ?",
                (*params, size),
            ).fetchall()
        ]
        if not rowids:
            break
        placeholders = ",".join("?" for _ in rowids)
        started = time.monotonic()
        with transaction():
            cur = execute_write(
                f"DELETE FROM {table} WHERE rowid IN ({placeholders})",
                rowids,
            )
        deleted = max(0, cur.rowcount or 0)
        duration_ms = (time.monotonic() - started) * 1000
        _record_retention_batch(table, deleted, duration_ms)
        total += deleted
        if len(rowids) < size or deleted == 0:
            break
    return total


def _prune_filter_candidate_outcomes_batches(
    *,
    cutoff: str,
    retention_days: int,
    protected_refs: tuple[str, ...] = (),
    configured_candidate_epoch: str | None = None,
    candidate_policy_available: bool = True,
    batch_size: int = 5_000,
) -> int:
    db = get_db()
    total = 0
    size = max(1, int(batch_size))
    scan_after = 0
    while True:
        started = time.monotonic()
        pending_rows: list = []
        pending_by_horizon: dict[str, int] = {}
        with transaction():
            candidate_clause = (
                "AND (COALESCE(scanner_family, '') <> 'candidate_selection_utility' "
                "OR outcome_candidate_key NOT IN ("
                "SELECT j.value FROM universe_twin_shadow_records r "
                "JOIN universe_twin_shadow_batches b ON b.scanner_run_id=r.scanner_run_id "
                "JOIN json_each(r.compact_output_json, '$.outcome_candidate_keys') j "
                "WHERE b.record_version IN ('candidate-selection-utility-record/v1', "
                "'candidate-selection-utility-record/v2', 'candidate-selection-utility-record/v3', "
                "'candidate-selection-utility-record/v4') "
                "AND ((b.policy_epoch_id IN ("
                "SELECT policy_epoch_id FROM universe_twin_shadow_batches "
                "WHERE record_version IN ('candidate-selection-utility-record/v1', "
                "'candidate-selection-utility-record/v2', 'candidate-selection-utility-record/v3', "
                "'candidate-selection-utility-record/v4') "
                "AND promotion_eligible=1 AND policy_epoch_id IS NOT NULL "
                "UNION SELECT ? WHERE ? IS NOT NULL "
                "UNION SELECT COALESCE(NULLIF(entity_id, ''), CASE WHEN "
                "json_valid(after_state) THEN json_extract(after_state, "
                "'$.candidate_selection_policy_epoch') END) FROM audit_events "
                "WHERE event_type='candidate_selection_authority_admitted') AND ("
                "NOT EXISTS (SELECT 1 FROM audit_events admitted WHERE "
                "admitted.event_type='candidate_selection_authority_admitted' AND "
                "COALESCE(NULLIF(admitted.entity_id, ''), CASE WHEN "
                "json_valid(admitted.after_state) THEN json_extract(admitted.after_state, "
                "'$.candidate_selection_policy_epoch') END)=b.policy_epoch_id) OR "
                "b.captured_at <= (SELECT MIN(admitted.timestamp) FROM audit_events admitted WHERE "
                "admitted.event_type='candidate_selection_authority_admitted' AND "
                "COALESCE(NULLIF(admitted.entity_id, ''), CASE WHEN "
                "json_valid(admitted.after_state) THEN json_extract(admitted.after_state, "
                "'$.candidate_selection_policy_epoch') END)=b.policy_epoch_id))) OR b.captured_at <= ("
                "SELECT MAX(json_extract(policy_json, '$.estimator_training_cutoff')) "
                "FROM universe_twin_shadow_batches WHERE policy_epoch_id IN ("
                "SELECT policy_epoch_id FROM universe_twin_shadow_batches "
                "WHERE record_version IN ('candidate-selection-utility-record/v1', "
                "'candidate-selection-utility-record/v2', 'candidate-selection-utility-record/v3', "
                "'candidate-selection-utility-record/v4') "
                "AND promotion_eligible=1 AND policy_epoch_id IS NOT NULL "
                "UNION SELECT ? WHERE ? IS NOT NULL "
                "UNION SELECT COALESCE(NULLIF(entity_id, ''), CASE WHEN "
                "json_valid(after_state) THEN json_extract(after_state, "
                "'$.candidate_selection_policy_epoch') END) FROM audit_events "
                "WHERE event_type='candidate_selection_authority_admitted'))))) "
                if candidate_policy_available else
                "AND COALESCE(scanner_family, '') <> 'candidate_selection_utility' "
            )
            params: tuple[object, ...] = (
                (
                    cutoff,
                    scan_after,
                    configured_candidate_epoch, configured_candidate_epoch,
                    configured_candidate_epoch, configured_candidate_epoch,
                    size,
                )
                if candidate_policy_available else (cutoff, scan_after, size)
            )
            scanned_rows = db.execute(
                "SELECT rowid, id, horizon, status, outcome_candidate_key "
                "FROM filter_candidate_outcomes "
                f"WHERE entry_ts < ? AND rowid > ? {candidate_clause} "
                "ORDER BY rowid LIMIT ?",
                params,
            ).fetchall()
            if scanned_rows:
                scan_after = int(scanned_rows[-1][0])
            rows = list(scanned_rows)
            if protected_refs:
                protected = set(protected_refs)
                rows = [
                    row for row in rows
                    if str(row[1]) not in protected and str(row[4]) not in protected
                ]
            if not scanned_rows:
                break
            if not rows:
                continue
            pending_rows = [row for row in rows if row[3] == "pending"]
            if pending_rows:
                event = _filter_outcome_loss_event(
                    cutoff=cutoff,
                    retention_days=retention_days,
                    pending_rows=pending_rows,
                )
                pending_by_horizon = json.loads(event.after_state or "{}").get("by_horizon", {})
                if not insert_audit_event_once(event):
                    raise RuntimeError("filter_candidate_outcomes prune batch audit already exists while rows remain")
            rowids = [int(row[0]) for row in rows]
            placeholders = ",".join("?" for _ in rowids)
            cur = execute_write(
                f"DELETE FROM filter_candidate_outcomes WHERE rowid IN ({placeholders})",
                rowids,
            )
            deleted = max(0, cur.rowcount or 0)
            if deleted != len(rowids):
                raise RuntimeError(
                    f"filter_candidate_outcomes prune deleted {deleted} rows after selecting {len(rowids)}"
                )
        duration_ms = (time.monotonic() - started) * 1000
        _record_retention_batch("filter_candidate_outcomes", deleted, duration_ms)
        total += deleted
        if pending_rows:
            _sqlite_logger.warning(
                "prune_filter_candidate_outcomes: %d filter-outcome rows reached the "
                "%s retention cutoff still unmeasured (%s) — the canonical settler is "
                "not keeping up; deleting them and recording the loss in audit_events",
                len(pending_rows),
                cutoff,
                " ".join(f"{k}={v}" for k, v in sorted(pending_by_horizon.items())),
            )
        if len(scanned_rows) < size and deleted == 0:
            break
    return total


def _delete_market_ohlcv_batches(
    where: str,
    params: tuple = (),
    *,
    batch_size: int = 5_000,
) -> int:
    """Delete bounded OHLCV telemetry batches by its durable composite key.

    ``market_ohlcv`` may use SQLite's WITHOUT ROWID layout after explicit
    offline compaction, so its retention path must never depend on the hidden
    rowid implementation detail.
    """

    total = 0
    size = max(1, int(batch_size))
    key_columns = "exchange,symbol,market_type,timeframe,ts"
    while True:
        started = time.monotonic()
        with transaction():
            cur = execute_write(
                "DELETE FROM market_ohlcv WHERE "
                f"({key_columns}) IN (SELECT {key_columns} FROM market_ohlcv "
                f"WHERE {where} ORDER BY timeframe,ts,exchange,symbol,market_type LIMIT ?)",
                (*params, size),
            )
        deleted = max(0, cur.rowcount or 0)
        duration_ms = (time.monotonic() - started) * 1000
        _record_retention_batch("market_ohlcv", deleted, duration_ms)
        total += deleted
        if deleted < size:
            break
    return total


def prune_agent_runs(days: int = 90) -> int:
    # agent_run_steps and agent_tool_calls have FK → agent_runs with no ON DELETE CASCADE.
    # Delete children first in the same transaction to avoid IntegrityError.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    total = 0
    while True:
        run_ids = [
            row[0]
            for row in db.execute(
                "SELECT id FROM agent_runs WHERE started_at < ? ORDER BY started_at LIMIT 1000",
                (cutoff,),
            ).fetchall()
        ]
        if not run_ids:
            break
        placeholders = ",".join("?" * len(run_ids))
        started = time.monotonic()
        with transaction():
            execute_write(
                f"DELETE FROM agent_run_artifacts WHERE run_id IN ({placeholders})",
                run_ids,
            )
            execute_write(
                f"DELETE FROM agent_run_steps WHERE run_id IN ({placeholders})",
                run_ids,
            )
            execute_write(
                f"DELETE FROM agent_tool_calls WHERE run_id IN ({placeholders})",
                run_ids,
            )
            cur = execute_write(
                f"DELETE FROM agent_runs WHERE id IN ({placeholders})",
                run_ids,
            )
        deleted = max(0, cur.rowcount or 0)
        _record_retention_batch(
            "agent_runs",
            deleted,
            (time.monotonic() - started) * 1000,
        )
        total += deleted
        if len(run_ids) < 1000 or deleted == 0:
            break
    return total


def prune_run_steps(days: int = 14) -> int:
    """Prune telemetry-only agent_run_steps rows. Default lowered 30d→14d (Phase 3)."""
    return _delete_old_rows("agent_run_steps", days)


def prune_tool_calls(days: int = 90) -> int:
    return _delete_old_rows("agent_tool_calls", days)


def prune_market_snapshots(days: int = 30) -> int:
    return _delete_old_rows("market_snapshots", days)


def prune_portfolio_snapshots(days: int = 90) -> int:
    return _delete_old_rows("portfolio_snapshots", days)


def prune_ohlcv_backfill_state(days: int = 90) -> int:
    """Drop stale operational retries after an instrument has left the Universe."""
    return _delete_old_rows("ohlcv_backfill_state", days)


def prune_serenity_claims(days: int = 90) -> int:
    # serenity_theme_claims has FK → serenity_claims with no ON DELETE CASCADE.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    claim_ids = [
        row[0] for row in db.execute("SELECT id FROM serenity_claims WHERE created_at < ?", (cutoff,)).fetchall()
    ]
    if not claim_ids:
        return 0
    placeholders = ",".join("?" * len(claim_ids))
    with transaction():
        execute_write(f"DELETE FROM serenity_theme_claims WHERE claim_id IN ({placeholders})", claim_ids)
        execute_write("DELETE FROM serenity_claims WHERE created_at < ?", (cutoff,))
    return len(claim_ids)


def prune_filter_decision_batches(days: int = 30) -> int:
    claim = _observation_retention_floor("filter_decision_batches")
    cutoff = _effective_cutoff(days, claim)
    return _delete_by_rowid_batches("filter_decision_batches", "evaluated_at < ?", (cutoff,))


class ExperimentStateReadError(Exception):
    """Raised when experiment state cannot be reliably read from storage."""

    pass


_AUTO_EXPERIMENT_FLOOR = object()


def min_open_experiment_exposure_at() -> str | None:
    """Return the earliest durable exposure of an active open experiment.

    Raises:
        ExperimentStateReadError: If the query fails and state cannot be proven.
    """
    if _canonical_lab_authority():
        # The Lab claim ledger is the only Experiment-retention authority after
        # cutover; legacy rows are historical evidence and cannot widen Main TTL.
        return None
    db = get_db()
    try:
        row = db.execute(
            "SELECT MIN(x.exposed_at) AS m "
            "FROM learning_experiments e JOIN learning_exposures x "
            "ON x.experiment_id=e.experiment_id "
            "WHERE e.ended_at IS NULL AND e.status='active'"
        ).fetchone()
    except Exception as e:
        raise ExperimentStateReadError(f"Failed to query experiment state: {e}") from e
    if not row:
        return None
    val = row["m"] if hasattr(row, "keys") else row[0]
    return str(val) if val else None


def _resolve_experiment_floor(value: str | None | object) -> str | None:
    if value is _AUTO_EXPERIMENT_FLOOR:
        if _canonical_lab_authority():
            return None
        return min_open_experiment_exposure_at()
    return cast(str | None, value)

def _observation_retention_floor(domain: str) -> str | None:
    if _lab_retention_floor is None:
        raise ExperimentStateReadError("Observation reader unavailable")
    return _lab_retention_floor(domain)


def _active_observation_claim_refs(domain: str) -> tuple[str, ...]:
    if _lab_retention_refs is None:
        raise ExperimentStateReadError("Observation reader unavailable")
    return _lab_retention_refs(domain)


def _effective_cutoff(
    days: int,
    min_experiment_started_at: str | None,
) -> str:
    """Compute the clean_window_cutoff. Delete only rows older than min(default, open-experiment)."""
    default = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if not min_experiment_started_at:
        return default
    try:
        experiment = datetime.fromisoformat(min_experiment_started_at.replace("Z", "+00:00"))
        if experiment.tzinfo is None:
            experiment = experiment.replace(tzinfo=timezone.utc)
        return min(
            datetime.fromisoformat(default),
            experiment.astimezone(timezone.utc),
        ).isoformat()
    except (AttributeError, TypeError, ValueError):
        return default


def prune_filter_decisions(
    days: int = 45,
    min_experiment_started_at: str | None | object = _AUTO_EXPERIMENT_FLOOR,
) -> int:
    """Prune filter_decisions with clean_window_cutoff guard.

    Default lowered 90d→45d (Phase 3). If an open learning_experiment started
    before ``now - days``, keep everything back to that experiment's start so
    the paired-cohort read stays reproducible.
    """
    min_experiment_started_at = _resolve_experiment_floor(min_experiment_started_at)
    cutoff = _effective_cutoff(days, min_experiment_started_at)
    return _delete_by_rowid_batches("filter_decisions", "evaluated_at < ?", (cutoff,))


def prune_filter_candidate_outcomes(
    days: int = 30,
    min_experiment_started_at: str | None | object = _AUTO_EXPERIMENT_FLOOR,
) -> int:
    """Prune filter_candidate_outcomes with clean_window_cutoff guard.

    Default lowered 90d→30d (Phase 3). Guard prevents deleting outcome rows an
    open experiment still needs for its paired cohort — the effective cutoff is
    the OLDER of ``now - days`` and the earliest open-experiment start.
    """
    try:
        lab_authority = _canonical_lab_authority()
    except ExperimentStateReadError:
        return 0
    protected_refs: tuple[str, ...] = ()
    if lab_authority:
        # Once Lab owns the generation, exact source identities are the only
        # protection needed; an experiment-wide date floor would retain
        # unrelated telemetry and hide missing claim state.
        try:
            protected_refs = _active_observation_claim_refs("filter_candidate_outcomes")
        except ExperimentStateReadError:
            return 0
        cutoff = _effective_cutoff(days, None)
    else:
        min_experiment_started_at = _resolve_experiment_floor(min_experiment_started_at)
        observation_floor = _observation_retention_floor("filter_candidate_outcomes")
        if observation_floor:
            min_experiment_started_at = min(
                value for value in (min_experiment_started_at, observation_floor) if value
            )
        cutoff = _effective_cutoff(days, min_experiment_started_at)
    candidate_policy_available, configured_candidate_epoch = (
        _candidate_selection_retention_state()
    )

    # A row still 'pending' at the cutoff was never measured, and deleting it
    # leaves nothing behind that says so: every coverage aggregation matches
    # status='settled', and doctor's stale-overdue count only counts rows that
    # still exist, so a queue that never caught up ends up indistinguishable from
    # one that drained. The gap closes itself and the ledger reads as complete.
    #
    # Deletion still has to happen — retention is what keeps the file bounded, and
    # a row this old is past the settler's reach anyway (the 1m/5m bars its window
    # needs expired days earlier). What must not happen is the silence or a
    # forecast audit. Each short batch records the exact pending rows it deletes in
    # the same SQLite transaction as the DELETE.
    return _prune_filter_candidate_outcomes_batches(
        cutoff=cutoff,
        retention_days=days,
        protected_refs=protected_refs,
        configured_candidate_epoch=configured_candidate_epoch,
        candidate_policy_available=candidate_policy_available,
    )


def prune_operator_evaluation_events(days: int = 30) -> int:
    return _delete_old_rows("operator_evaluation_events", days)


# A pending carrier is removable only after one exact source/opportunity identity
# has one unambiguous terminal entry decision. skip/watch are terminal Operator
# decisions themselves and therefore remain part of the permanent audit trail.
# Position-management decisions are intentionally excluded: a later close/reduce
# must not make an otherwise unresolved entry carrier look consumed.
_PENDING_FINAL_ACTIONS = ("trade", "scale_in", "flip", "skip", "watch")


def prune_pi_decisions(days: int = 7) -> int:
    """Delete only old pending carriers with one explicit settled final row.

    Missing/legacy identity, no final, multiple finals, and a final still in
    ``submitting`` all remain durable for fail-closed recovery and audit. Every
    non-pending Operator decision is retained permanently.
    """
    placeholders = ",".join("?" for _ in _PENDING_FINAL_ACTIONS)
    exact_final = (
        "f.action IN (" + placeholders + ") "
        "AND f.opportunity_id=pi_decisions.opportunity_id "
        "AND f.signal_source=pi_decisions.signal_source "
        "AND datetime(f.created_at)>=datetime(pi_decisions.created_at)"
    )
    return _delete_old_rows(
        "pi_decisions",
        days,
        extra_where=(
            "action='signal_pending' "
            "AND opportunity_id IS NOT NULL "
            "AND signal_source IS NOT NULL "
            f"AND (SELECT COUNT(*) FROM pi_decisions AS f WHERE {exact_final})=1 "
            "AND EXISTS ("
            "SELECT 1 FROM pi_decisions AS f "
            f"WHERE {exact_final} "
            "AND f.proposal_status IS NOT NULL "
            "AND f.proposal_status!='submitting'"
            ") "
            "AND NOT EXISTS ("
            " SELECT 1 FROM agent_run_artifacts artifact "
            " WHERE artifact.artifact_type='pi_decision' "
            " AND artifact.artifact_id=pi_decisions.id"
            ")"
        ),
        extra_params=(*_PENDING_FINAL_ACTIONS, *_PENDING_FINAL_ACTIONS),
    )


def prune_system_actions(days: int = 7) -> int:
    """Delete old system_action rows from knowledge_items.

    system_action is pure telemetry noise (~1200 rows/day) that used to flood
    the limit-30 window before the A2 fix added explicit item_type filters.
    Safe to prune because no learning/PI path queries system_action anymore.

    #44 note (2026-07-08): knowledge_items is otherwise governed by the
    no-hard-delete rule (audit reconstruction). This function is the ONE
    documented exception — telemetry rows are not causal/knowledge content
    and their unbounded growth would swamp the audit path itself. To keep
    the accountability trail honest despite the hard delete, we emit an
    audit event summarising the sweep (row count + oldest cutoff) so an
    operator can grep pruning history without keeping the row bodies.
    """
    deleted = _delete_old_rows(
        "knowledge_items",
        days,
        extra_where="item_type = ?",
        extra_params=("system_action",),
    )
    if deleted > 0:
        try:
            from aletheia.files.jsonl import write_audit

            write_audit(
                {
                    "actor": "system",
                    "source": "retention",
                    "event_type": "knowledge_items_pruned",
                    "entity_type": "knowledge_items",
                    "message": (
                        f"prune_system_actions: hard-deleted {deleted} system_action rows "
                        f"older than {days} days (documented exception to no-hard-delete)"
                    ),
                    "after": {
                        "deleted_count": deleted,
                        "cutoff_days": days,
                        "item_type": "system_action",
                    },
                }
            )
        except Exception:
            # Retention MUST NOT crash if the audit write fails — the point of
            # the audit hook is best-effort observability, not the retention
            # decision itself.
            logger.warning("storage: audit event write for pruned system_action rows failed; audit trail incomplete", exc_info=True)
    return deleted


# ---------------------------------------------------------------------------
# OHLCV rolling window store
# ---------------------------------------------------------------------------

_OHLCV_WINDOW_DEFAULT = 50_000  # max bars kept per (symbol, timeframe) pair
_OHLCV_PRUNE_MARGIN = 1_000


def _ohlcv_window() -> int:
    """Read max_bars_per_pair from config, fallback to default."""
    try:
        from aletheia.config.loader import load_config

        return load_config().historical_data.max_bars_per_pair
    except Exception:
        return _OHLCV_WINDOW_DEFAULT


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


def _resolve_ohlcv_scope(db, symbol: str, timeframe: str, exchange: str, market_type: str) -> tuple[str, str] | None:
    """Choose one coherent instrument for legacy partially-scoped readers."""
    if exchange and market_type:
        return exchange.lower(), market_type
    where = ["symbol = ?", "timeframe = ?"]
    params: list = [symbol, timeframe]
    if exchange:
        where.append("exchange = ?")
        params.append(exchange.lower())
    if market_type:
        where.append("market_type = ?")
        params.append(market_type)
    row = db.execute(
        "SELECT exchange,market_type,MAX(ts) newest FROM market_ohlcv WHERE "
        + " AND ".join(where)
        + " GROUP BY exchange,market_type "
        "ORDER BY CASE WHEN exchange='legacy_unknown' THEN 1 ELSE 0 END, newest DESC LIMIT 1",
        params,
    ).fetchone()
    return (row["exchange"], row["market_type"]) if row else None


def get_ohlcv_coverage(symbol: str, timeframe: str, exchange: str = "", market_type: str = "") -> dict:
    """Return coverage stats for a symbol+timeframe pair."""
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
    db = get_db()
    row = db.execute("SELECT * FROM collector_health_state WHERE domain=?", (domain,)).fetchone()
    return dict(row) if row else None


def list_collector_health_states() -> list[dict]:
    """Return one latest-state row per collector domain."""
    db = get_db()
    rows = db.execute("SELECT * FROM collector_health_state ORDER BY domain").fetchall()
    return [dict(row) for row in rows]


def upsert_collector_health_state(state: dict) -> None:
    """Persist one bounded collector state using the canonical transaction pattern."""
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


# --- Universe Generator ---

from aletheia.storage.models import (
    CandidatePlanningGeneration,
    EntryPlanRecord,
    UniverseAsset,
    UniverseFeatureSnapshot,
    UniverseMember,
    UniversePolicy,
    UniverseScannerRun,
    UniverseSnapshot,
    UniverseTwinShadowBatch,
    UniverseTwinShadowEvidence,
)
logger = logging.getLogger("aletheia.storage.repositories")


def insert_universe_feature_snapshot(snap: UniverseFeatureSnapshot) -> None:
    data = snap.model_dump()
    data.pop("raw", None)
    _insert("universe_feature_snapshots", data)


def insert_universe_feature_snapshots_batch(
    snapshots: list[UniverseFeatureSnapshot],
) -> int:
    """Persist one feature-collection pass in one executemany transaction."""

    rows = []
    for snapshot in snapshots:
        data = snapshot.model_dump()
        data.pop("raw", None)
        rows.append(data)
    with transaction():
        return _insert_many("universe_feature_snapshots", rows)


def insert_universe_scanner_run(run: UniverseScannerRun) -> None:
    _insert("universe_scanner_runs", run.model_dump())


def update_universe_scanner_run(run_id: str, **kwargs) -> None:
    _update("universe_scanner_runs", run_id, kwargs)


_TWIN_OUTCOME_IMMUTABLE_FIELDS = (
    "id",
    "outcome_candidate_key",
    "venue",
    "symbol",
    "market_type",
    "direction",
    "scanner_family",
    "regime_tag",
    "horizon",
    "allocation_selected",
    "entry_price_usd",
    "entry_spread_bps",
    "funding_rate_at_entry",
    "entry_ts",
    "due_at",
)


def publish_candidate_snapshot(snap: UniverseSnapshot, members: list[UniverseMember]) -> int:
    """Persist one source-neutral snapshot/member unit inside its owner's transaction."""
    if any(member.snapshot_id != snap.id for member in members):
        raise ValueError("candidate_snapshot_mismatch")
    with transaction(immediate=True, savepoint=True):
        _insert("universe_snapshots", snap.model_dump())
        return _insert_many("universe_members", [member.model_dump() for member in members])


def expire_opportunities_from_superseded_snapshots(market_type: str = "crypto") -> int:
    """Expire unclaimed generations from superseded snapshots with evidence.

    An ``open`` row is a per-snapshot analytical generation and ends when that
    snapshot is superseded. A ``queued`` row is already a durable Operator work
    carrier: its origin ids remain immutable, but its own ``valid_until`` decides
    freshness. Reviewed/expired rows are preserved. A SAVEPOINT makes the
    lifecycle CAS and append-only fact all-or-nothing even when this helper joins
    Universe publication's transaction.
    """
    db = get_db()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    with transaction(immediate=True):
        rows = db.execute(
            "SELECT o.* FROM opportunities o "
            "JOIN universe_snapshots s ON s.id=o.universe_snapshot_id "
            "WHERE o.status='open' "
            "AND s.source_kind='universe' AND s.market_type=? AND s.status='superseded' "
            "ORDER BY o.created_at, o.rowid",
            (market_type,),
        ).fetchall()
        savepoint = f"snapshot_cascade_{uuid.uuid4().hex}"
        db.execute(f"SAVEPOINT {savepoint}")
        try:
            expired = 0
            for raw in rows:
                row = dict(raw)
                opportunity_id = str(row["id"])
                source_status = str(row["status"])
                dedup_key = f"universe_snapshot_status:{row.get('universe_snapshot_id')}:{opportunity_id}"
                updated = execute_write(
                    "UPDATE opportunities SET status='expired' WHERE id=? AND status=?",
                    (opportunity_id, source_status),
                )
                if updated.rowcount != 1:
                    continue
                execute_write(
                    "INSERT OR IGNORE INTO filter_decisions ("
                    "id,dedup_key,candidate_key,candidate_kind,parent_candidate_key,"
                    "run_id,opportunity_id,universe_snapshot_id,universe_member_id,"
                    "proposal_id,venue,symbol,market_type,direction,scanner_family,"
                    "regime_tag,filter_name,stage,sequence_no,decision,reason_code,"
                    "reason_detail,input_snapshot,parameter_snapshot,filter_version,"
                    "code_commit,outcome_candidate_key,evaluated_at"
                    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        dedup_key,
                        dedup_key,
                        f"opportunity:{opportunity_id}",
                        "opportunity",
                        None,
                        None,
                        opportunity_id,
                        row.get("universe_snapshot_id"),
                        row.get("universe_member_id"),
                        None,
                        None,
                        str(row.get("symbol") or "unknown"),
                        str(row.get("market") or "spot"),
                        row.get("direction_bias"),
                        row.get("scanner_family"),
                        row.get("regime_tag"),
                        "universe_snapshot_status",
                        "opportunity_lifecycle",
                        0,
                        "rejected",
                        "universe_snapshot_superseded",
                        "parent Universe snapshot is no longer active",
                        json.dumps({"source_status": source_status}, sort_keys=True),
                        json.dumps({"required_snapshot_status": "active"}, sort_keys=True),
                        "universe_snapshot_status:v1",
                        None,
                        None,
                        evaluated_at,
                    ),
                )
                if db.execute("SELECT 1 FROM filter_decisions WHERE dedup_key=?", (dedup_key,)).fetchone() is None:
                    raise RuntimeError(f"snapshot expiry evidence unavailable for Opportunity {opportunity_id}")
                expired += 1
            db.execute(f"RELEASE SAVEPOINT {savepoint}")
            return expired
        except Exception:
            db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            db.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise


def expire_universe_snapshots(market_type: str = "crypto", except_id: str | None = None) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    if except_id:
        execute_write(
            "UPDATE universe_snapshots SET status = 'superseded' "
            "WHERE status = 'active' AND source_kind='universe' AND market_type = ? AND id != ?",
            (market_type, except_id),
        )
    else:
        execute_write(
            "UPDATE universe_snapshots SET status = 'superseded' WHERE status = 'active' AND source_kind='universe' AND market_type = ?",
            (market_type,),
        )
    if not was_in_tx:
        db.commit()


list_candidates = list_universe_members


def refresh_active_snapshot_ttls(snapshot_id: str, now: datetime) -> int:
    """Renew current Candidate leases without rewriting historical Opportunity rows."""
    with transaction(immediate=True, savepoint=True):
        members = _select_all(
            "universe_members", where="snapshot_id = ?", params=(snapshot_id,), limit=1000,
        )
        updated = 0
        for member in members:
            ttl_sec = int(member["ttl_sec"])
            if ttl_sec <= 0:
                continue
            expires_at = (now + timedelta(seconds=ttl_sec)).isoformat()
            result = execute_write(
                "UPDATE universe_members SET expires_at=? "
                "WHERE id=? AND (expires_at IS NULL OR expires_at<?)",
                (expires_at, member["id"], expires_at),
            )
            updated += result.rowcount
    return updated


get_candidate = get_universe_member


def get_entry_plan_execution_lineage(plan_ids: list[str]) -> dict[str, dict]:
    """Read exact execution and settlement links from one SQLite snapshot."""
    from aletheia.portfolio.pnl import _realized_economics_value
    from aletheia.trading.trading_mandate import normalize_physical_market_type
    from aletheia.analytics.outcome_semantics import validated_outcome_economics
    from aletheia.trading.entry_learning import _instant

    cutoff = datetime.now(timezone.utc)

    identifiers = list(dict.fromkeys(plan_ids))
    result = {}
    with transaction():
        db = get_db()
        for offset in range(0, len(identifiers), 200):
            batch = identifiers[offset:offset + 200]
            placeholders = ",".join("?" for _ in batch)
            plans = {row["id"]: dict(row) for row in db.execute(
                f"SELECT * FROM entry_plans WHERE id IN ({placeholders})", batch
            )}
            linked_ids = [p["order_id"] for p in plans.values() if p.get("order_id")]
            linked_marks = ",".join("?" for _ in linked_ids) or "NULL"
            orders = {row["id"]: dict(row) for row in db.execute(
                f"SELECT * FROM orders WHERE entry_plan_id IN ({placeholders}) "
                f"OR id IN ({linked_marks})", [*batch, *linked_ids]
            )}
            proposal_ids = [o["proposal_id"] for o in orders.values() if o.get("proposal_id")]
            proposals = {row["id"]: dict(row) for row in db.execute(
                "SELECT * FROM trade_proposals WHERE id IN (SELECT value FROM json_each(?))",
                (json.dumps(proposal_ids),),
            )}
            order_ids = list(orders)
            marks = ",".join("?" for _ in order_ids) or "NULL"
            fills = [dict(row) for row in db.execute(
                f"SELECT * FROM fills WHERE order_id IN ({marks})", order_ids
            )]
            outcomes = [dict(row) for row in db.execute(
                "SELECT * FROM filter_candidate_outcomes WHERE horizon='4h' "
                "AND outcome_candidate_key IN (SELECT value FROM json_each(?))",
                (json.dumps([f"entry-fill:{f['id']}" for f in fills]),),
            )]
            position_ids = [o["position_id"] for o in orders.values() if o.get("position_id")]
            position_marks = ",".join("?" for _ in position_ids) or "NULL"
            positions = {row["id"]: dict(row) for row in db.execute(
                f"SELECT * FROM positions WHERE order_id IN ({marks}) "
                f"OR id IN ({position_marks})", [*order_ids, *position_ids]
            )}
            pids = list(positions)
            pm = ",".join("?" for _ in pids) or "NULL"
            closes = [dict(row) for row in db.execute(
                f"SELECT * FROM orders WHERE position_id IN ({pm}) AND close_reason IS NOT NULL", pids
            )]
            cids = [row["id"] for row in closes]
            close_fills = [dict(row) for row in db.execute(
                "SELECT * FROM fills WHERE order_id IN (SELECT value FROM json_each(?))", (json.dumps(cids),)
            )]
            events = [dict(row) for row in db.execute(
                f"SELECT * FROM position_close_events WHERE position_id IN ({pm}) AND is_partial=0", pids
            )]
            cash = [dict(row) for row in db.execute(
                f"SELECT * FROM paper_account_events WHERE position_id IN ({pm}) "
                "AND action IN ('position_closed','manual_credit','manual_debit')", pids
            )]
            for plan_id in batch:
                item = {"orders": [], "fills": None, "position": None,
                        "close_orders": [], "close_fills": [],
                        "entry_outcomes": [],
                        "availability": {"available": True, "reason": None},
                        "economics": {"status": "not_applicable", "reason": None},
                        "execution_state": "NOT_STARTED"}
                result[plan_id] = item
                plan = plans.get(plan_id)
                related = [o for o in orders.values() if o.get("entry_plan_id") == plan_id and not o.get("close_reason")]
                order = orders.get(plan.get("order_id")) if plan else None
                reason = None
                if plan is None:
                    reason = "entry_plan_missing"
                elif plan.get("order_id") and order is None:
                    reason = "linked_order_missing"
                elif order and related and order["id"] not in {o["id"] for o in related}:
                    reason = "order_relation_ambiguous"
                elif order and order.get("entry_plan_id") not in (None, plan_id):
                    reason = "order_relation_conflict"
                if not related and order and not reason:
                    related = [order]
                related.sort(key=lambda o: ((proposals.get(o.get("proposal_id")) or {}).get("entry_leg_index") or 0,
                                           str(o["created_at"]), o["id"]))
                if related and not reason:
                    payload = json.loads(plan["plan_json"])
                    legs = [(proposals.get(o.get("proposal_id")) or {}).get("entry_leg_index") for o in related]
                    if ((payload.get("capture_policy_id") and legs not in ([0], [0, 1]))
                        or (not payload.get("capture_policy_id") and len(related) > 1)):
                        reason = "order_relation_ambiguous"
                for linked in related if not reason else []:
                    proposal = proposals.get(linked.get("proposal_id"))
                    if (proposal is None or proposal.get("entry_plan_id") != plan_id
                        or normalize_physical_market_type(linked.get("market_type")) != plan["market_type"]
                        or to_canonical(str(linked.get("symbol") or "")) != plan["symbol"]
                        or linked.get("direction") != plan["direction"]):
                        reason = "order_proposal_identity_conflict"
                order = related[-1] if related else None
                if reason:
                    item.update(availability={"available": False, "reason": reason}, execution_state="UNAVAILABLE")
                    continue
                if order is None:
                    if plan["status"] in {"ordered", "partially_filled", "filled"}:
                        item.update(availability={"available": False, "reason": "expected_order_missing"}, execution_state="UNAVAILABLE")
                    continue
                entry_order_ids = {o["id"] for o in related}
                of = [f for f in fills if f["order_id"] in entry_order_ids]
                quantity = sum(float(f["fill_quantity"]) for f in of)
                item["orders"] = [{**{k: linked.get(k) for k in (
                    "id", "proposal_id", "risk_decision_id", "exchange_order_id", "status", "quantity",
                    "position_id", "created_at", "filled_at", "order_tactic", "error_reason")},
                    "entry_leg_index": proposals[linked["proposal_id"]].get("entry_leg_index")}
                    for linked in related]
                item["fills"] = {"ids": [f["id"] for f in of], "count": len(of), "quantity": quantity,
                                 "latest_at": max((str(f["filled_at"]) for f in of), default=None)}
                for fill in of:
                    matching = [outcome for outcome in outcomes if outcome["outcome_candidate_key"] == f"entry-fill:{fill['id']}"]
                    summary = {"fill_id": fill["id"], "horizon": "4h", "status": "unavailable", "reason": "fixed_horizon_outcome_missing"}
                    if len(matching) == 1:
                        outcome = matching[0]
                        summary.update(id=outcome["id"], due_at=outcome.get("due_at"), settled_at=outcome.get("settled_at"))
                        exact = all(outcome.get(key) == plan.get(key) for key in ("venue", "symbol", "market_type", "direction"))
                        try:
                            exact = exact and _instant(outcome["entry_ts"]) == _instant(fill["filled_at"])
                            ready = _instant(outcome["due_at"]) <= cutoff and _instant(outcome["settled_at"]) <= cutoff
                        except (TypeError, ValueError):
                            ready = False
                        economics = validated_outcome_economics(outcome) if exact and ready else None
                        if exact and outcome["status"] == "pending":
                            summary.update(status="pending", reason="fixed_horizon_pending")
                        elif economics is not None and outcome["status"] == "settled":
                            summary.update(status="complete", reason=None, economics=economics)
                        else:
                            summary["reason"] = "fixed_horizon_evidence_unavailable"
                    item["entry_outcomes"].append(summary)
                entry_position_ids = {o["position_id"] for o in related if o.get("position_id")}
                ps = [p for p in positions.values() if p["id"] in entry_position_ids or p.get("order_id") in entry_order_ids]
                if len(ps) > 1 or (quantity > 0 and not ps):
                    item.update(availability={"available": False, "reason": "position_relation_unavailable"}, execution_state="UNAVAILABLE")
                    continue
                if not ps:
                    item["execution_state"] = ("CANCEL_PENDING" if order["status"] == "cancel_pending" else
                        "NO_FILL" if order["status"] in {"cancelled", "rejected", "failed"} and quantity == 0 else "ORDER_WORKING")
                    continue
                if not of:
                    item.update(availability={"available": False, "reason": "entry_fill_relation_missing"}, execution_state="UNAVAILABLE")
                    continue
                position = ps[0]
                root = next((o for o in related if o["id"] == position.get("order_id")), None)
                if (root is None or position.get("proposal_id") != root.get("proposal_id")
                    or position.get("symbol") != order.get("symbol")
                    or normalize_physical_market_type(position.get("market_type")) != plan["market_type"]
                    or position.get("direction") != order.get("direction")
                    or position.get("execution_mode") != order.get("execution_mode")):
                    item.update(availability={"available": False, "reason": "position_proposal_conflict"}, execution_state="UNAVAILABLE")
                    continue
                item["position"] = {k: position.get(k) for k in (
                    "id", "status", "quantity", "entry_price", "opened_at", "closed_at", "pending_close_order_id",
                    "pending_close_status", "outcome_eligible", "outcome_exclusion_reason")}
                pc = [c for c in closes if c["position_id"] == position["id"]]
                pci = {c["id"] for c in pc}
                item["close_orders"] = [{k: c.get(k) for k in ("id", "status", "quantity", "created_at", "filled_at")} for c in pc]
                item["close_fills"] = [{k: f.get(k) for k in ("id", "order_id", "fill_quantity", "filled_at")} for f in close_fills if f["order_id"] in pci]
                if position["status"] == "open":
                    item["execution_state"] = "POSITION_CLOSING" if position.get("pending_close_order_id") or position.get("pending_close_status") else "POSITION_OPEN"
                    continue
                exclusion = str(position.get("outcome_exclusion_reason") or "")
                if exclusion in {"funding_permanently_unrecoverable", "invalid_exit_price_evidence"}:
                    item["economics"] = {"status": "excluded", "reason": exclusion}
                    item["execution_state"] = "OUTCOME_EXCLUDED"
                    continue
                pnl = _realized_economics_value(position)
                if pnl is None:
                    item["economics"] = {"status": "pending", "reason": "costs_incomplete"}
                    item["execution_state"] = "CLOSED_PENDING_ECONOMICS"
                    continue
                pe = [e for e in events if e["position_id"] == position["id"]]
                ce = [e for e in cash if e["position_id"] == position["id"]]
                if position["execution_mode"] == "paper_internal" and (
                    len(pe) != 1 or pe[0].get("realized_pnl") is None
                    or not math.isclose(float(pe[0]["realized_pnl"]), pnl, rel_tol=0, abs_tol=1e-8)
                    or not any(e["action"] == "position_closed" for e in ce)
                    or not math.isclose(sum(float(e["amount_usdt"]) for e in ce), pnl, rel_tol=0, abs_tol=1e-8)
                ):
                    item["economics"] = {"status": "unavailable", "reason": "settlement_projection_inconsistent"}
                    item["execution_state"] = "UNAVAILABLE"
                elif not item["close_fills"]:
                    item["economics"] = {"status": "unavailable", "reason": "close_fill_relation_missing"}
                    item["execution_state"] = "UNAVAILABLE"
                else:
                    item["economics"] = {"status": "complete", "reason": None}
                    item["execution_state"] = "CLOSED"
    return result


def list_candidate_planning_cycles_batch(*, planning_keys: list[str]) -> dict[str, list[dict]]:
    keys = list(dict.fromkeys(planning_keys))
    result = {key: [] for key in keys}
    with transaction():
        for offset in range(0, len(keys), 200):
            batch = keys[offset:offset + 200]
            placeholders = ",".join("?" for _ in batch)
            rows = [dict(row) for row in get_db().execute(
                "SELECT pg.*, ep.id AS linked_entry_plan_id, ep.status AS entry_plan_status, "
                "ep.execution_mode, ep.created_at AS entry_plan_created_at, "
                "ep.updated_at AS entry_plan_updated_at, ep.terminal_at AS entry_plan_terminal_at, "
                "ep.terminal_reason AS entry_plan_terminal_reason, "
                "ep.monitoring_state AS entry_plan_monitoring_state, ep.monitoring_evidence_json, "
                "ep.trigger_activation_id, ta.created_at AS activation_created_at, "
                "ta.expires_at AS activation_expires_at, ta.activation_json "
                "FROM candidate_planning_generations pg "
                "LEFT JOIN entry_plans ep ON ep.planning_generation_id=pg.id "
                "LEFT JOIN entry_trigger_activations ta ON ta.id=ep.trigger_activation_id "
                f"WHERE pg.planning_key IN ({placeholders}) ORDER BY pg.cycle_number,pg.created_at,pg.id",
                batch,
            )]
            for row in rows:
                row["entry_plan_id"] = row.pop("linked_entry_plan_id")
            activation_history: dict[str, list[dict]] = {}
            for activation in get_db().execute(
                "SELECT ta.plan_id, ta.created_at FROM entry_trigger_activations ta "
                "JOIN entry_plans ep ON ep.id=ta.plan_id "
                "JOIN candidate_planning_generations pg ON pg.id=ep.planning_generation_id "
                f"WHERE pg.planning_key IN ({placeholders}) ORDER BY ta.created_at,ta.id", batch,
            ):
                activation_history.setdefault(activation["plan_id"], []).append(dict(activation))
            lineage = get_entry_plan_execution_lineage([row["entry_plan_id"] for row in rows if row.get("entry_plan_id")])
            for row in rows:
                monitoring = json.loads(row.get("monitoring_evidence_json") or "{}")
                row["passive_entry"] = monitoring.get("passive_entry")
                row["monitoring_changed_at"] = monitoring.get("state_changed_at")
                row["activation_history"] = activation_history.get(row.get("entry_plan_id"), [])
                if row.get("entry_plan_status") in {"active", "triggered", "risk_reserved"}:
                    row["activation_recoverable"] = entry_plan_activation_can_refresh(row["entry_plan_id"])
                    snapshot = json.loads(row.get("activation_json") or "{}").get("snapshot", {})
                    row["activation_stream_current"] = (monitoring.get("state") == "ready"
                        and snapshot.get("stream_generation_id") == monitoring.get("stream_generation_id")
                        and snapshot.get("subscription_generation_id") == monitoring.get("subscription_generation_id"))
                row["execution"] = lineage.get(row.get("entry_plan_id"))
                result[row["planning_key"]].append(row)
    return result


def expire_candidate_attention(*, now: datetime | None = None) -> dict[str, int]:
    """Terminalize active Candidate attention by its frozen soft/hard clocks."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp = current.isoformat()
    counts = {"ttl_expired": 0, "max_attention_age": 0}
    with transaction(immediate=True):
        rows = (
            get_db()
            .execute(
                "SELECT id, soft_expires_at, hard_expires_at "
                "FROM candidate_planning_generations "
                "WHERE is_current=1 AND attention_status='active' "
                "AND (soft_expires_at<=? OR hard_expires_at<=?)",
                (timestamp, timestamp),
            )
            .fetchall()
        )
        for row in rows:
            reason = "max_attention_age" if str(row["hard_expires_at"]) <= timestamp else "ttl_expired"
            updated = execute_write(
                "UPDATE candidate_planning_generations SET attention_status='terminal', "
                "attention_terminal_at=?, attention_terminal_reason=?, updated_at=? "
                "WHERE id=? AND attention_status='active'",
                (timestamp, reason, timestamp, row["id"]),
            )
            counts[reason] += int(updated.rowcount or 0)
    return counts


def candidate_attention_is_active(
    generation_id: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Re-read current attention at a planning side-effect boundary."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp = current.isoformat()
    expire_candidate_attention(now=current)
    row = (
        get_db()
        .execute(
            "SELECT pg.* FROM candidate_planning_generations pg "
            "JOIN universe_members um ON um.id=pg.last_candidate_id "
            "WHERE pg.id=? AND pg.is_current=1 AND um.execution_eligible=1",
            (generation_id,),
        )
        .fetchone()
    )
    return bool(row and _candidate_attention_valid(dict(row), timestamp))


def reconcile_candidate_attention(
    selected_generation_ids: set[str],
    *,
    producer_scope: Literal["universe", "serenity"],
    eligible_identity_keys: set[tuple[str, str, str, str, str]],
    refresh_source: dict,
    soft_lease_sec: int,
    new_generation_ids: set[str] | None = None,
    refresh_complete: bool = True,
    now: datetime | None = None,
) -> dict[str, int]:
    """Apply one completed Universe allocation as attention evidence.

    A failed/partial refresh calls this with ``refresh_complete=False`` and is a
    strict no-op. New generations already own their initial lease; only a later
    completed re-selection records ``last_requalified_at``.
    """

    if not refresh_complete:
        return {"requalified": 0, "quota_evicted": 0, "admission_lost": 0}
    if not 300 <= int(soft_lease_sec) <= 86_400:
        raise ValueError("candidate_attention_soft_lease_out_of_bounds")
    if len(json.dumps(refresh_source, sort_keys=True, separators=(",", ":"))) > 3_500:
        raise ValueError("candidate_attention_source_too_large")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp = current.isoformat()
    requested_soft = (current + timedelta(seconds=int(soft_lease_sec))).isoformat()
    new_generation_ids = new_generation_ids or set()
    counts = {"requalified": 0, "quota_evicted": 0, "admission_lost": 0}
    expire_candidate_attention(now=current)
    with transaction(immediate=True):
        rows = [
            dict(row)
            for row in get_db()
            .execute("SELECT * FROM candidate_planning_generations WHERE is_current=1 AND attention_status='active'")
            .fetchall()
        ]
        for row in rows:
            birth = get_candidate(str(row["candidate_id"]))
            birth_context = json.loads(row["requalification_source_json"] or "{}").get("candidate_opportunity_context")
            if birth_context is None:
                birth_context = json.loads(birth["candidate_opportunity_context_json"]) if birth and birth.get("candidate_opportunity_context_json") else {}
            if birth_context.get("source", {}).get("kind", "universe") != producer_scope:
                continue
            generation_id = str(row["id"])
            if generation_id in selected_generation_ids:
                if generation_id in new_generation_ids:
                    continue
                soft_expires_at = min(requested_soft, str(row["hard_expires_at"]))
                source_json = json.dumps(
                    {
                        **refresh_source,
                        **{k: v for k, v in json.loads(row["requalification_source_json"] or "{}").items() if k in {"birth_material", "candidate_opportunity_context"}},
                        "planning_generation_id": generation_id,
                        "planning_identity": row["planning_identity"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                updated = execute_write(
                    "UPDATE candidate_planning_generations SET last_requalified_at=?, "
                    "requalification_source_json=?, soft_expires_at=?, updated_at=? "
                    "WHERE id=? AND attention_status='active'",
                    (timestamp, source_json, soft_expires_at, timestamp, generation_id),
                )
                counts["requalified"] += int(updated.rowcount or 0)
                continue
            identity_key = (
                str(row["venue"]),
                str(row["symbol"]),
                str(row["market_type"]),
                str(row["candidate_family"]),
                str(row["direction"]),
            )
            reason = "quota_evicted" if identity_key in eligible_identity_keys else "admission_lost"
            updated = execute_write(
                "UPDATE candidate_planning_generations SET attention_status='terminal', "
                "attention_terminal_at=?, attention_terminal_reason=?, updated_at=? "
                "WHERE id=? AND attention_status='active'",
                (timestamp, reason, timestamp, generation_id),
            )
            counts[reason] += int(updated.rowcount or 0)
    return counts


def prepare_candidate_requalification(candidate: dict) -> dict | None:
    """Freeze bounded local PA evidence before entering publication's writer."""
    from aletheia.storage.sqlite import read_snapshot
    from aletheia.trading.entry_analysis import entry_causal_windows, fingerprint, planning_structure_material
    from aletheia.trading.entry_analysis import entry_orderbook_available
    from aletheia.trading.entry_contract import entry_contract_fingerprints
    from aletheia.trading.entry_plan import candidate_planning_identity
    from aletheia.universe.candidate_opportunity import CandidateOpportunityContextV1

    if get_db().in_transaction:
        raise ValueError("requalification_preparation_inside_transaction")
    if not candidate.get("execution_eligible") or not candidate.get("candidate_opportunity_context_json"):
        return None
    context = CandidateOpportunityContextV1.model_validate_json(candidate["candidate_opportunity_context_json"])
    cutoff = _normalized_utc_datetime(str(context.source["source_cutoff_at"]))
    behavior, schema = entry_contract_fingerprints()
    instrument = tuple(str(candidate[key]) for key in ("venue", "symbol", "market_type"))
    with read_snapshot():
        current = get_db().execute(
            "SELECT id FROM candidate_planning_generations "
            "WHERE venue=? AND symbol=? AND market_type=? AND is_current=1 "
            "ORDER BY (entry_behavior_fingerprint IS NOT NULL) DESC,created_at DESC,id DESC LIMIT 1",
            instrument,
        ).fetchone()
        rows = get_ohlcv_candles(candidate["symbol"], "15m", 96, candidate["venue"],
            candidate["market_type"], int(cutoff.timestamp()*1000) - 900_000 + 1, include_provenance=True)
        book = latest_orderbook(candidate["symbol"], candidate["venue"], candidate["market_type"])
    windows, structures, _, _ = entry_causal_windows(*instrument, candidate["scanner_direction"], {"15m": rows}, cutoff)
    material = planning_structure_material(structures)
    if material is None:
        return None
    proof = {
        "candidate_id": candidate["id"], "snapshot_id": candidate["snapshot_id"],
        "candidate_context_hash": context.context_hash,
        "expected_generation_id": current["id"] if current else None,
        "planning_key": candidate_planning_identity(candidate)[0],
        "instrument": dict(zip(("venue", "symbol", "market_type"), instrument)),
        "source_cutoff_at": cutoff.isoformat(),
        "entry_behavior_fingerprint": behavior, "schema_fingerprint": schema,
        "source_fingerprint": windows["15m"]["source_fingerprint"],
        "last_closed_at_ms": windows["15m"]["bars"][-1]["ts"] + 900_000,
        "material": material,
        "orderbook": ({k: book[k] for k in ("id", "observed_at", "bid_price", "ask_price", "spread_bps", "total_depth_10bps_usd")}
            if entry_orderbook_available(book, cutoff) else None),
    }
    return {**proof, "proof_hash": fingerprint(proof)}


def ensure_candidate_planning_generation(
    candidate_id: str,
    *,
    manual_replan: bool = False,
    soft_lease_sec: int = 900,
    max_attention_age_sec: int = 3600,
    now: datetime | None = None,
    requalification: dict | None = None,
) -> tuple[dict, bool]:
    """Own one thesis-level planning generation without snapshot/rank churn."""

    from aletheia.trading.entry_plan import candidate_planning_identity
    from aletheia.trading.entry_contract import entry_contract_fingerprints
    from aletheia.universe.candidate_opportunity import CandidateOpportunityContextV1
    from aletheia.trading.entry_analysis import fingerprint, planning_structure_material

    if requalification is None and not get_db().in_transaction:
        preparation_candidate = get_candidate(candidate_id)
        if preparation_candidate is not None:
            requalification = prepare_candidate_requalification(preparation_candidate)

    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not 300 <= int(soft_lease_sec) <= int(max_attention_age_sec) <= 86_400:
        raise ValueError("candidate_attention_bounds_invalid")
    timestamp = current_time.isoformat()
    with transaction(immediate=True):
        candidate = get_candidate(candidate_id)
        if candidate is None:
            raise ValueError("candidate_not_found")
        if not bool(candidate.get("execution_eligible")):
            raise ValueError("candidate_execution_ineligible")
        context = (
            CandidateOpportunityContextV1.model_validate_json(
                candidate["candidate_opportunity_context_json"]
            )
            if candidate.get("candidate_opportunity_context_json") else None
        )
        if context is None:
            raise ValueError("candidate_opportunity_context_missing")
        entry_behavior_fingerprint, schema_fingerprint = entry_contract_fingerprints()
        planning_key, planning_identity = candidate_planning_identity(candidate)
        existing_row = (
            get_db().execute(
                "SELECT * FROM candidate_planning_generations "
                "WHERE venue=? AND symbol=? AND market_type=? AND is_current=1 "
                "ORDER BY (entry_behavior_fingerprint IS NOT NULL) DESC,created_at DESC,id DESC LIMIT 1",
                (str(candidate["venue"]), str(candidate["symbol"]), str(candidate["market_type"])),
            ).fetchone()
        )
        existing = dict(existing_row) if existing_row else None
        existing_candidate = get_candidate(str(existing["last_candidate_id"])) if existing is not None else None
        eligibility_recovered = bool(
            existing is not None
            and (
                existing.get("attention_terminal_reason") == "admission_lost"
                or (existing_candidate is not None and not bool(existing_candidate.get("execution_eligible")))
            )
        )
        existing_plan_row = (
            get_db().execute(
                "SELECT * FROM entry_plans WHERE planning_generation_id=?",
                (existing["id"],),
            ).fetchone()
            if existing is not None else None
        )
        existing_plan = dict(existing_plan_row) if existing_plan_row else None
        plan_expiry = None
        if existing_plan is not None:
            try:
                plan_expiry = json.loads(str(existing_plan.get("plan_json") or ""))["expires_at"]
            except (KeyError, TypeError, json.JSONDecodeError):
                plan_expiry = None
        candidate_snapshot_complete = bool(
            get_db().execute(
                "SELECT 1 FROM universe_snapshots WHERE id=? AND status='active'",
                (candidate["snapshot_id"],),
            ).fetchone()
        )
        try:
            newer_requalification_cutoff = bool(
                plan_expiry
                and _normalized_utc_datetime(str(context.source["source_cutoff_at"]))
                > _normalized_utc_datetime(str(plan_expiry))
            )
        except (TypeError, ValueError):
            newer_requalification_cutoff = False
        try:
            existing_context = (
                CandidateOpportunityContextV1.model_validate_json(
                    existing_candidate["candidate_opportunity_context_json"]
                )
                if existing_candidate and existing_candidate.get("candidate_opportunity_context_json")
                else None
            )
            later_completed_requalification = bool(
                existing_candidate
                and str(existing.get("last_candidate_id")) != candidate_id
                and candidate_snapshot_complete
                and existing_context is not None
                and _normalized_utc_datetime(str(context.source["source_cutoff_at"]))
                > _normalized_utc_datetime(
                    str(existing_context.source["source_cutoff_at"])
                )
            )
        except (TypeError, ValueError):
            later_completed_requalification = False
        unresolved_execution = bool(
            existing_plan is not None
            and (
                existing_plan.get("status") in {"ordered", "partially_filled"}
                or get_db().execute(
                    "SELECT 1 FROM trade_proposals p LEFT JOIN proposal_execution_claims c ON c.proposal_id=p.id "
                    "WHERE p.entry_plan_id=? AND (c.lease_expires_at>? "
                    "OR p.status IN ('executing','submission_unknown','materialization_missing') "
                    "OR (p.status='executed' AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.proposal_id=p.id))) LIMIT 1",
                    (existing_plan["id"], timestamp),
                ).fetchone()
                or get_db().execute(
                    "SELECT 1 FROM orders o WHERE o.entry_plan_id=? AND ("
                    "o.status IN ('open','pending','executing','submitted','partial','partially_filled',"
                    "'submission_unknown','materialization_missing') OR "
                    "EXISTS (SELECT 1 FROM positions p WHERE p.order_id=o.id AND p.status='open') OR "
                    "EXISTS (SELECT 1 FROM fills f WHERE f.order_id=o.id AND NOT EXISTS "
                    "(SELECT 1 FROM positions p WHERE p.order_id=o.id AND p.status='closed'))) LIMIT 1",
                    (existing_plan["id"],),
                ).fetchone()
            )
        )
        source_changed = False
        if existing is not None:
            birth = get_candidate(str(existing["candidate_id"]))
            birth_context = json.loads(existing["requalification_source_json"] or "{}").get("candidate_opportunity_context")
            if birth_context is None:
                birth_context = json.loads(birth["candidate_opportunity_context_json"]) if birth and birth.get("candidate_opportunity_context_json") else {}
            birth_source = birth_context.get("source", {})
            source_changed = (birth_source.get("kind", "universe") != context.source.get("kind", "universe")
                or (context.source.get("kind") == "serenity" and birth_source.get("id") != context.source["id"]))
            if source_changed and (existing["attention_status"] == "active" or unresolved_execution
                or (existing_plan is not None and existing_plan["status"] in _ACTIVE_ENTRY_PLAN_STATUSES)):
                raise ValueError("instrument_owned")
        # The proof is prepared without the writer. Recheck its exact owner and
        # publication identity here; a concurrent successor makes it stale.
        if requalification is not None:
            proof_payload = {k: v for k, v in requalification.items() if k != "proof_hash"}
            expected = {
                "candidate_id": candidate_id, "snapshot_id": candidate["snapshot_id"],
                "candidate_context_hash": context.context_hash,
                "expected_generation_id": existing["id"] if existing else None,
                "planning_key": planning_key,
                "instrument": {k: candidate[k] for k in ("venue", "symbol", "market_type")},
                "source_cutoff_at": _normalized_utc_datetime(str(context.source["source_cutoff_at"])).isoformat(),
                "entry_behavior_fingerprint": entry_behavior_fingerprint,
                "schema_fingerprint": schema_fingerprint,
            }
            if (not candidate_snapshot_complete
                or context.candidate_id != candidate_id
                or context.context_hash != candidate.get("candidate_opportunity_context_hash")
                or _normalized_utc_datetime(expected["source_cutoff_at"]) > current_time
                or any(requalification.get(k) != v for k, v in expected.items())
                or fingerprint(proof_payload) != requalification.get("proof_hash")):
                requalification = None
            if requalification is not None:
                # Candle corrections may land between preparation and this writer.
                # Compare the exact admitted rows; do not rerun the structure kernel here.
                rows = get_ohlcv_candles(
                    candidate["symbol"], "15m", 96, candidate["venue"],
                    candidate["market_type"],
                    int(_normalized_utc_datetime(expected["source_cutoff_at"]).timestamp()*1000) - 900_000 + 1,
                    include_provenance=True,
                )
                if (fingerprint(rows) != requalification.get("source_fingerprint")
                    or not rows
                    or rows[-1]["ts"] + 900_000 != requalification.get("last_closed_at_ms")):
                    requalification = None
        structure_requalified = False
        dependency_recovered = False
        if (requalification is not None and existing is not None
            and existing["planning_key"] == planning_key
            and existing.get("entry_behavior_fingerprint") == entry_behavior_fingerprint
            and existing.get("schema_fingerprint") == schema_fingerprint
            and existing.get("attention_status") == "terminal"
            and existing_plan is None and not unresolved_execution
            and later_completed_requalification):
            birth = json.loads(existing["requalification_source_json"] or "{}").get("birth_material")
            baseline = birth["material"] if birth else None
            compared_at = birth["source_cutoff_at"] if birth else existing_context.source["source_cutoff_at"]
            if existing.get("planner_input_json"):
                frozen = json.loads(existing["planner_input_json"])
                frozen_material = planning_structure_material(frozen.get("market_structure", {}))
                if frozen_material is not None:
                    baseline = frozen_material
                compared_at = max(_normalized_utc_datetime(compared_at),
                    _normalized_utc_datetime(frozen["analysis_cutoff_at"])).isoformat()
            material = requalification["material"]
            prior_ids = {f["id"] for f in baseline["facts"]} if baseline else set()
            structure_requalified = bool(
                baseline and baseline["fingerprint"] != material["fingerprint"]
                and any(f["id"] not in prior_ids
                    and f["known_at_ms"] > _normalized_utc_datetime(compared_at).timestamp()*1000
                    for f in material["facts"])
            )
            blocked_reason = json.loads(existing["result_json"] or "{}").get("reason")
            dependency_failure = blocked_reason in {
                "ENTRY_ANALYSIS_EXECUTION_CONTEXT_UNAVAILABLE",
                "ENTRY_ANALYSIS_REQUIRED_CONTEXT_UNAVAILABLE",
                "ENTRY_ANALYSIS_MARKET_QUALITY_UNAVAILABLE",
            }
            if dependency_failure:
                structure_requalified = False
            book = requalification.get("orderbook")
            dependency_recovered = bool(
                existing.get("result_kind") == "unavailable"
                and not existing.get("model_invoked") and not existing.get("planner_input_json")
                and dependency_failure
                and book and existing.get("attention_terminal_at")
                and requalification.get("last_closed_at_ms", 0)
                    > max(_normalized_utc_datetime(compared_at),
                        _normalized_utc_datetime(existing["attention_terminal_at"])).timestamp()*1000
                and _normalized_utc_datetime(book["observed_at"])
                    > _normalized_utc_datetime(existing["attention_terminal_at"])
            )


        eligibility_recovered = bool(
            eligibility_recovered
            and later_completed_requalification
            and not unresolved_execution
        )
        expired_requalified = bool(
            existing is not None
            and existing_plan is not None
            and existing_plan.get("status") == "expired"
            and str(existing.get("last_candidate_id")) != candidate_id
            and candidate_snapshot_complete
            and newer_requalification_cutoff
            and not unresolved_execution
        )
        material_input_changed = bool(
            existing is not None
            and ((existing["planning_identity"] != planning_identity and later_completed_requalification)
                 or source_changed)
            and not unresolved_execution
        )
        compatibility_cutover = bool(
            existing is not None
            and existing.get("entry_behavior_fingerprint") != entry_behavior_fingerprint
            and not unresolved_execution
        )
        immutable_legacy_runtime_no_plan = bool(
            compatibility_cutover
            and existing is not None
            and existing.get("entry_behavior_fingerprint") is None
            and existing.get("result_kind") == "no_plan"
            and "AI_ENTRY_RUNTIME_UNSUPPORTED" in str(existing.get("result_json") or "")
        )
        manual_replan_allowed = bool(
            manual_replan
            and existing is not None
            and later_completed_requalification
            and not unresolved_execution
        )
        if (
            existing is not None
            and not manual_replan_allowed
            and not eligibility_recovered
            and not expired_requalified
            and not material_input_changed
            and not structure_requalified
            and not dependency_recovered
            and not compatibility_cutover
        ):
            if candidate_id not in {existing["candidate_id"], existing["last_candidate_id"]}:
                insert_audit_event_once(AuditEvent(
                    id=f"candidate-planning:{candidate_id}:{existing['id']}",
                    timestamp=timestamp, actor="system", source="universe",
                    event_type="candidate_planning_associated", entity_type="candidate",
                    entity_id=candidate_id,
                    after_state=json.dumps({"planning_generation_id": existing["id"]}),
                    message="Candidate retained the existing planning cycle",
                ))
            execute_write(
                "UPDATE candidate_planning_generations SET "
                "updated_at=? WHERE id=?",
                (timestamp, existing["id"]),
            )
            existing["updated_at"] = timestamp
            return existing, False

        reason = (
            "manual_replan"
            if manual_replan_allowed
            else "plan_expired_requalified"
            if expired_requalified
            else "canonical_compatibility_cutover"
            if compatibility_cutover
            else "material_input_changed"
            if material_input_changed
            else "confirmed_structure_requalified"
            if structure_requalified
            else "planning_dependencies_recovered"
            if dependency_recovered
            else "execution_eligibility_recovered"
            if eligibility_recovered
            else "incompatible_planning_generation"
        )
        if existing is not None and not immutable_legacy_runtime_no_plan:
            terminal_reason = (
                "canonical_compatibility_cutover"
                if reason == "canonical_compatibility_cutover"
                else "planning_generation_superseded"
            )
            superseded = execute_write(
                "UPDATE candidate_planning_generations SET is_current=0, "
                "status='superseded', attention_status='terminal', "
                "attention_terminal_at=?, attention_terminal_reason=?, "
                "updated_at=? WHERE id=? AND is_current=1",
                (timestamp, terminal_reason, timestamp, existing["id"]),
            )
            if superseded.rowcount != 1:
                raise ValueError("candidate_requalification_cas_lost")
            execute_write(
                "UPDATE entry_plans SET status='invalidated', terminal_at=?, "
                "terminal_reason=?, updated_at=? WHERE planning_generation_id=? "
                "AND status IN ('waiting','risk_reserved','triggered')",
                (timestamp, reason, timestamp, existing["id"]),
            )

        generation = CandidatePlanningGeneration(
            planning_key=planning_key,
            planning_identity=planning_identity,
            candidate_id=candidate_id,
            last_candidate_id=candidate_id,
            venue=str(candidate["venue"]),
            symbol=str(candidate["symbol"]),
            market_type=str(candidate["market_type"]),
            candidate_family=str(candidate["scanner_family"]),
            direction=str(candidate["scanner_direction"]),
            opportunity_mechanism=(
                str(context.opportunity["primary_mechanism"]) if context else None
            ),
            candidate_selection_policy=(
                str(context.candidate_selection["policy"]) if context else None
            ),
            candidate_selection_policy_epoch=(
                context.candidate_selection.get("policy_epoch") if context else None
            ),
            mechanism_version=(
                str(context.opportunity["mechanism_version"]) if context else None
            ),
            created_at=timestamp,
            updated_at=timestamp,
            attention_started_at=timestamp,
            soft_expires_at=(current_time + timedelta(seconds=int(soft_lease_sec))).isoformat(),
            hard_expires_at=(current_time + timedelta(seconds=int(max_attention_age_sec))).isoformat(),
            attention_max_age_sec=int(max_attention_age_sec),
            cycle_number=(
                int(
                    get_db().execute(
                        "SELECT COALESCE(MAX(cycle_number),0)+1 FROM candidate_planning_generations WHERE planning_key=?",
                        (planning_key,),
                    ).fetchone()[0]
                )
            ),
            previous_generation_id=str(existing["id"]) if existing is not None else None,
            cycle_reason=(
                "explicit_owner_replan" if manual_replan_allowed
                else "plan_expired_requalified" if expired_requalified
                else "canonical_compatibility_cutover" if compatibility_cutover
                else "runtime_authority_recovered" if eligibility_recovered
                else "material_input_changed" if material_input_changed
                else "confirmed_structure_requalified" if structure_requalified
                else "planning_dependencies_recovered" if dependency_recovered
                else "initial_candidate"
            ),
            requalification_source_json=json.dumps({"candidate_opportunity_context": context.model_dump(mode="json"),
                **({"birth_material": requalification} if requalification is not None else {})}, sort_keys=True, separators=(",", ":")),
            requalification_candidate_id=candidate_id,
            entry_behavior_fingerprint=entry_behavior_fingerprint,
            schema_fingerprint=schema_fingerprint,
        )
        data = _stamp_provenance(generation.model_dump())
        _insert("candidate_planning_generations", data)
        return data, True


def claim_next_candidate_planning(
    owner_token: str,
    *,
    lease_seconds: int = 300,
    max_attempts: int = 3,
    now: datetime | None = None,
    resume_only: bool = False,
) -> dict | None:
    """CAS one current generation; duplicate wakes return no second owner."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp = current.isoformat()
    expire_candidate_attention(now=current)
    lease_until = (current + timedelta(seconds=lease_seconds)).isoformat()
    with transaction(immediate=True):
        row = (
            get_db()
            .execute(
                "SELECT pg.* FROM candidate_planning_generations pg "
                "JOIN universe_members um ON um.id=pg.last_candidate_id "
                "WHERE pg.is_current=1 AND pg.attention_status='active' "
                "AND pg.entry_behavior_fingerprint IS NOT NULL "
                "AND (?=0 OR pg.validated_hypothesis_json IS NOT NULL) "
                "AND um.execution_eligible=1 "
                "AND pg.soft_expires_at>? AND pg.hard_expires_at>? "
                "AND pg.attempt_count<? AND (pg.status='pending' "
                "OR (pg.status='unavailable' "
                "AND (pg.retry_not_before IS NULL OR pg.retry_not_before<=?)) "
                "OR (pg.status='planning' AND pg.claim_expires_at<=?)) "
                "ORDER BY CASE WHEN um.rank IS NULL THEN 1 ELSE 0 END, um.rank ASC, "
                "pg.created_at ASC, pg.id ASC LIMIT 1",
                (resume_only, timestamp, timestamp, max_attempts, timestamp, timestamp),
            )
            .fetchone()
        )
        if row is None:
            return None
        generation_id = row["id"]
        claimed = execute_write(
            "UPDATE candidate_planning_generations SET status='planning', "
            "owner_token=?, claim_expires_at=?, retry_not_before=NULL, "
            "attempt_count=attempt_count+1, planning_started_at=COALESCE(planning_started_at,?), "
            "last_stage='planning', outcome_class=NULL, outcome_reason=NULL, updated_at=? "
            "WHERE id=? AND is_current=1 "
            "AND entry_behavior_fingerprint IS NOT NULL "
            "AND attention_status='active' AND soft_expires_at>? AND hard_expires_at>? "
            "AND attempt_count<? AND EXISTS (SELECT 1 FROM universe_members um "
            "WHERE um.id=candidate_planning_generations.last_candidate_id "
            "AND um.execution_eligible=1) AND (status='pending' "
            "OR (status='unavailable' AND (retry_not_before IS NULL OR retry_not_before<=?)) "
            "OR (status='planning' AND claim_expires_at<=?))",
            (
                owner_token,
                lease_until,
                timestamp,
                timestamp,
                generation_id,
                timestamp,
                timestamp,
                max_attempts,
                timestamp,
                timestamp,
            ),
        )
        if claimed.rowcount != 1:
            return None
        result = (
            get_db()
            .execute(
                "SELECT * FROM candidate_planning_generations WHERE id=?",
                (generation_id,),
            )
            .fetchone()
        )
        return dict(result) if result else None


def get_candidate_path_progress(*, now: datetime | None = None) -> dict:
    """Read current Candidate debt in one snapshot, without evaluator or write paths."""
    from aletheia.config.loader import load_config
    from aletheia.trading.entry_profiles import compatible_entry_profiles
    from aletheia.universe.candidate_opportunity import CandidateOpportunityContextV1

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp = current.isoformat()
    cfg = load_config()
    policy = cfg.universe.candidate_selection_policy
    epoch = cfg.universe.candidate_selection_policy_epoch
    result = {
        "generated_at": timestamp, "availability": {"available": True, "reason": None},
        "last_attempt_at": None, "last_success_at": None, "last_snapshot_id": None,
        "selection_policy": policy, "policy_epoch": epoch,
        "selected_count": None, "planning_supported_count": None, "observe_only_count": None,
        "selection_reason_counts": {}, "ready_planning_count": 0, "oldest_ready_at": None,
        "expired_claim_count": 0, "due_outcome_count": 0, "oldest_due_at": None,
        "issues": [], "blocking_reason_counts": {}, "shadow_capture_status": None,
        "shadow_capture_reason": None,
    }
    with readonly_db() as db:
        if not db.in_transaction:
            db.execute("BEGIN")
        latest = db.execute("SELECT * FROM universe_scanner_runs ORDER BY started_at DESC,id DESC LIMIT 1").fetchone()
        success = db.execute("SELECT * FROM universe_scanner_runs WHERE status='completed' "
                             "ORDER BY completed_at DESC,id DESC LIMIT 1").fetchone()
        if latest:
            result["last_attempt_at"] = latest["started_at"]
            if latest["status"] == "failed":
                result["issues"].append("candidate_refresh_failed")
        if success:
            result["last_success_at"] = success["completed_at"]
            result["last_snapshot_id"] = success["snapshot_id"]
        source = latest or success
        if source:
            metrics = json.loads(source["metrics"] or "{}").get("candidate_path", {})
            if not metrics:
                result["availability"] = {"available": False, "reason": "scanner_metrics_unavailable"}
            elif metrics.get("selection_policy", policy) != policy:
                result["availability"] = {"available": False, "reason": "selection_identity_unavailable"}
            for key in ("selected_count", "planning_supported_count", "observe_only_count",
                        "selection_reason_counts", "shadow_capture_status", "shadow_capture_reason"):
                if key in metrics:
                    result[key] = metrics[key]
            # A post-commit capture may have finished after its scanner status update was lost.
            # Reconcile in the projection only: GET/health never repairs durable ledgers.
            batch = db.execute("SELECT policy_epoch_id FROM universe_twin_shadow_batches "
                               "WHERE scanner_run_id=?", (source["id"],)).fetchone()
            if result["shadow_capture_status"] == "pending" and batch:
                result["shadow_capture_status"] = "captured"
                result["shadow_capture_reason"] = None
            if policy == "legacy_total_score_v1":
                epoch_row = db.execute(
                    "SELECT b.policy_epoch_id, b.policy_json FROM universe_twin_shadow_batches b "
                    "JOIN universe_scanner_runs s ON s.id=b.scanner_run_id "
                    "WHERE b.policy_epoch_id IS NOT NULL AND s.status='completed' "
                    "AND b.record_version LIKE 'candidate-selection-utility-record/%' "
                    "ORDER BY b.captured_at DESC,b.scanner_run_id DESC LIMIT 1"
                ).fetchone()
                epoch = epoch_row[0] if epoch_row else None
                if epoch_row:
                    from aletheia.universe.candidate_selection_authority import candidate_selection_policy_identity
                    frozen = json.loads(epoch_row["policy_json"]).get("selection_policy", {})
                    control = frozen.get("legacy_control", {})
                    control.pop("family_spread_tolerances", None)
                    control.pop("family_spread_tolerance_source", None)
                    if frozen != candidate_selection_policy_identity(cfg):
                        epoch = None
                        result["blocking_reason_counts"]["selection_epoch_unavailable"] = 1
        result["policy_epoch"] = epoch
        result["watchable_plan_counts"] = {
            row["state"]: row["count"] for row in db.execute(
                "SELECT COALESCE(monitoring_state,'unavailable') AS state, COUNT(*) AS count "
                "FROM entry_plans WHERE status IN ('waiting','active','triggered','risk_reserved','ordered','partially_filled') "
                "GROUP BY COALESCE(monitoring_state,'unavailable')"
            ).fetchall()
        }
        rows = db.execute(
            "SELECT pg.*, um.candidate_opportunity_context_json AS context_json, "
            "um.candidate_opportunity_context_hash AS context_hash, um.id AS member_id, "
            "um.venue AS member_venue, um.symbol AS member_symbol, um.market_type AS member_market "
            "FROM candidate_planning_generations pg LEFT JOIN universe_members um ON um.id=pg.last_candidate_id "
            "WHERE pg.is_current=1 AND pg.attention_status='active' "
            "AND pg.entry_behavior_fingerprint IS NOT NULL AND (um.execution_eligible=1 OR um.id IS NULL) "
            "AND pg.soft_expires_at>? AND pg.hard_expires_at>? AND pg.attempt_count<3 "
            "AND pg.entry_plan_id IS NULL AND NOT EXISTS (SELECT 1 FROM entry_plans ep "
            "WHERE ep.planning_generation_id=pg.id AND ep.status IN "
            "('waiting','active','triggered','risk_reserved','ordered','partially_filled')) "
            "AND (pg.status='pending' OR (pg.status='unavailable' "
            "AND (pg.retry_not_before IS NULL OR pg.retry_not_before<=?)) "
            "OR (pg.status='planning' AND pg.claim_expires_at<=?))",
            (timestamp, timestamp, timestamp, timestamp),
        ).fetchall()
        clocks = []
        blockers = result["blocking_reason_counts"]
        for row in rows:
            try:
                context = CandidateOpportunityContextV1.model_validate_json(row["context_json"] or "")
                if (context.candidate_id != row["member_id"] or context.context_hash != row["context_hash"]
                    or any(row[field] != row["member_" + alias] for field, alias in
                           (("venue", "venue"), ("symbol", "symbol"), ("market_type", "market")))
                    or context.opportunity["primary_mechanism"] != row["opportunity_mechanism"]
                    or context.opportunity["direction"] != row["direction"]
                    or context.candidate_selection["policy"] != row["candidate_selection_policy"]
                    or context.candidate_selection.get("policy_epoch") != row["candidate_selection_policy_epoch"]):
                    raise ValueError("identity")
            except ValueError:
                blockers["planning_identity_unavailable"] = blockers.get("planning_identity_unavailable", 0) + 1
                result["availability"] = {"available": False, "reason": "planning_identity_unavailable"}
                continue
            if context.opportunity["capability_status"] != "supported" or not compatible_entry_profiles(row["opportunity_mechanism"]):
                continue
            clock = row[{"pending": "created_at", "unavailable": "retry_not_before", "planning": "claim_expires_at"}[row["status"]]]
            result["ready_planning_count"] += 1
            if row["status"] == "planning":
                result["expired_claim_count"] += 1
            if row["status"] == "unavailable":
                reason = str(row["outcome_reason"] or "dependency_unavailable").partition(":")[0]
                # Provider exception text is not a diagnostic dimension.
                reason = reason if reason in {
                    "inference_queue_full", "PLANNER_TIMEOUT", "PLANNER_UNAVAILABLE", "PLANNER_INTERNAL",
                    "PLANNER_CANCELLED_DISPATCH_UNKNOWN", "PLANNER_CANCELLED_BEFORE_DISPATCH",
                    "ENTRY_ANALYSIS_EXECUTION_CONTEXT_UNAVAILABLE", "ENTRY_ANALYSIS_REQUIRED_CONTEXT_UNAVAILABLE",
                    "ENTRY_ANALYSIS_MARKET_QUALITY_UNAVAILABLE",
                    "ENTRY_PLAN_COMPILE_UNAVAILABLE", "COMPILER_INTERNAL",
                } else "dependency_unavailable"
                blockers[reason] = blockers.get(reason, 0) + 1
            try:
                clock_at = datetime.fromisoformat(clock)
                if clock_at.tzinfo is None or clock_at > current:
                    raise ValueError("clock")
            except (TypeError, ValueError):
                result["availability"] = {"available": False, "reason": "planning_clock_unknown"}
                continue
            clocks.append(clock_at)
            if (current - clock_at).total_seconds() > (180 if row["status"] == "planning" else 480):
                if "candidate_planning_stalled" not in result["issues"]:
                    result["issues"].append("candidate_planning_stalled")
        if clocks:
            result["oldest_ready_at"] = min(clocks).isoformat()
        # Scope JSON expansion to one durable epoch first, not the entire outcome population.
        relation_gaps = db.execute(
            "SELECT COUNT(*) FROM entry_plans ep "
            "JOIN candidate_planning_generations pg ON pg.id=ep.planning_generation_id "
            "LEFT JOIN orders ord ON ord.id=ep.order_id "
            "WHERE pg.candidate_selection_policy=? AND pg.candidate_selection_policy_epoch IS ? "
            "AND ((ep.order_id IS NOT NULL AND (ord.id IS NULL OR ord.close_reason IS NOT NULL "
            "OR (ord.entry_plan_id IS NOT NULL AND ord.entry_plan_id!=ep.id))) "
            "OR (ep.order_id IS NULL AND ep.status IN ('ordered','partially_filled','filled') "
            "AND (SELECT COUNT(*) FROM orders exact_order WHERE exact_order.entry_plan_id=ep.id "
            "AND exact_order.close_reason IS NULL)!=1))",
            (policy, epoch if policy == "candidate_selection_utility_v1" else None),
        ).fetchone()[0]
        if relation_gaps:
            result["availability"] = {"available": False, "reason": "entry_outcome_identity_unavailable"}
            blockers["entry_outcome_identity_unavailable"] = relation_gaps
        due = db.execute(
            "WITH epoch_batches AS MATERIALIZED (SELECT scanner_run_id FROM universe_twin_shadow_batches "
            "WHERE policy_epoch_id=? AND record_version LIKE 'candidate-selection-utility-record/%'), "
            "selection_keys AS (SELECT j.value AS key FROM epoch_batches b "
            "JOIN universe_twin_shadow_records r ON r.scanner_run_id=b.scanner_run_id "
            "JOIN json_each(r.compact_output_json,'$.outcome_candidate_keys') j), "
            "entry_keys AS (SELECT 'entry-fill:'||f.id AS key FROM candidate_planning_generations pg "
            "JOIN entry_plans ep ON ep.planning_generation_id=pg.id "
            "JOIN orders ord ON (ord.id=ep.order_id OR (ep.order_id IS NULL AND ord.entry_plan_id=ep.id)) "
            "JOIN fills f ON f.order_id=ord.id "
            "WHERE pg.candidate_selection_policy=? AND pg.candidate_selection_policy_epoch IS ? "
            "AND (ord.entry_plan_id IS NULL OR ord.entry_plan_id=ep.id) AND ord.close_reason IS NULL), "
            "keys AS (SELECT key FROM selection_keys UNION SELECT key FROM entry_keys) "
            "SELECT COUNT(*) AS count, MIN(o.due_at) AS oldest FROM filter_candidate_outcomes o "
            "JOIN keys ON keys.key=o.outcome_candidate_key "
            "WHERE o.horizon='4h' AND o.status='pending' AND o.due_at<=?",
            (epoch, policy, epoch if policy == "candidate_selection_utility_v1" else None, timestamp),
        ).fetchone()
        result["due_outcome_count"] = due["count"]
        result["oldest_due_at"] = due["oldest"]
        if due["count"]:
            # Deferred provider reasons are not persisted per outcome by the current settler.
            blockers["settlement_reason_unavailable"] = due["count"]
        if due["oldest"]:
            if (current - datetime.fromisoformat(due["oldest"])).total_seconds() > 900:
                result["issues"].append("candidate_outcome_backlog")
    return result


def count_pending_candidate_planning(
    max_attempts: int = 3,
    *,
    now: datetime | None = None,
    resume_only: bool = False,
) -> int:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp = current.isoformat()
    expire_candidate_attention(now=current)
    row = (
        get_db()
        .execute(
            "SELECT COUNT(*) FROM candidate_planning_generations pg "
            "JOIN universe_members um ON um.id=pg.last_candidate_id "
            "WHERE pg.is_current=1 AND pg.attention_status='active' "
            "AND pg.entry_behavior_fingerprint IS NOT NULL "
            "AND (?=0 OR pg.validated_hypothesis_json IS NOT NULL) "
            "AND um.execution_eligible=1 "
            "AND pg.soft_expires_at>? AND pg.hard_expires_at>? "
            "AND pg.attempt_count<? AND (pg.status='pending' "
            "OR (pg.status='unavailable' AND (pg.retry_not_before IS NULL OR pg.retry_not_before<=?)) "
            "OR (pg.status='planning' AND pg.claim_expires_at<=?))",
            (resume_only, timestamp, timestamp, max_attempts, timestamp, timestamp),
        )
        .fetchone()
    )
    return int(row[0] or 0) if row else 0


def complete_candidate_planning_no_plan(
    generation_id: str,
    owner_token: str,
    result: dict,
    *,
    now: datetime | None = None,
) -> bool:
    """Persist a durable no-plan result; heartbeat cannot reopen it."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp = current.isoformat()
    expire_candidate_attention(now=current)
    payload = json.dumps(result, sort_keys=True, separators=(",", ":"))
    updated = execute_write(
        "UPDATE candidate_planning_generations SET status='no_plan', "
        "result_kind='no_plan', result_json=?, owner_token=NULL, claim_expires_at=NULL, "
        "completed_at=?, attention_status='terminal', attention_terminal_at=?, "
        "attention_terminal_reason='no_plan', last_stage='planning_decision', "
        "outcome_class='no_plan', outcome_reason=json_extract(?,'$.reason_code'), "
        "updated_at=? WHERE id=? AND is_current=1 "
        "AND attention_status='active' AND soft_expires_at>? AND hard_expires_at>? "
        "AND EXISTS (SELECT 1 FROM universe_members um "
        "WHERE um.id=candidate_planning_generations.last_candidate_id "
        "AND um.execution_eligible=1) AND status='planning' AND owner_token=?",
        (payload, timestamp, timestamp, payload, timestamp, generation_id, timestamp, timestamp, owner_token),
    )
    if updated.rowcount != 1:
        execute_write(
            "UPDATE candidate_planning_generations SET result_kind='stale_result', "
            "result_json=?, owner_token=NULL, claim_expires_at=NULL, completed_at=?, updated_at=? "
            "WHERE id=? AND status='planning' AND owner_token=?",
            (payload, timestamp, timestamp, generation_id, owner_token),
        )
    return updated.rowcount == 1


def block_candidate_planning(
    generation_id: str,
    owner_token: str,
    reason: str,
    *,
    stage: str,
    model_invoked: bool = False,
    compiler_invoked: bool = False,
    retry_seconds: int = 60,
    now: datetime | None = None,
) -> bool:
    """Persist a resumable operational block without consuming a model attempt."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp = current.isoformat()
    payload = json.dumps(
        {"outcome_class": "planning_blocked", "reason": str(reason)[:160], "stage": stage},
        sort_keys=True,
        separators=(",", ":"),
    )
    updated = execute_write(
        "UPDATE candidate_planning_generations SET status='unavailable', "
        "result_kind='unavailable', result_json=?, retry_not_before=?, "
        "owner_token=NULL, claim_expires_at=NULL, completed_at=NULL, "
        "attempt_count=CASE WHEN ? THEN attempt_count ELSE MAX(attempt_count-1,0) END, "
        "last_stage=?, "
        "model_invoked=MAX(model_invoked,?), model_invoked_at=CASE WHEN ? THEN COALESCE(model_invoked_at,?) ELSE model_invoked_at END, "
        "compiler_invoked=MAX(compiler_invoked,?), compiler_invoked_at=CASE WHEN ? THEN COALESCE(compiler_invoked_at,?) ELSE compiler_invoked_at END, "
        "outcome_class='planning_blocked', outcome_reason=?, updated_at=? "
        "WHERE id=? AND is_current=1 AND attention_status='active' "
        "AND status='planning' AND owner_token=?",
        (
            payload, (current + timedelta(seconds=retry_seconds)).isoformat(),
            model_invoked, stage, model_invoked, model_invoked, timestamp,
            compiler_invoked, compiler_invoked, timestamp, str(reason)[:160], timestamp,
            generation_id, owner_token,
        ),
    )
    return updated.rowcount == 1


def mark_candidate_planning_stage(
    generation_id: str,
    owner_token: str,
    stage: str,
    *,
    model_invoked: bool = False,
    compiler_invoked: bool = False,
    now: datetime | None = None,
) -> bool:
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    updated = execute_write(
        "UPDATE candidate_planning_generations SET last_stage=?, "
        "model_invoked=MAX(model_invoked,?), "
        "model_invoked_at=CASE WHEN ? THEN COALESCE(model_invoked_at,?) ELSE model_invoked_at END, "
        "compiler_invoked=MAX(compiler_invoked,?), "
        "compiler_invoked_at=CASE WHEN ? THEN COALESCE(compiler_invoked_at,?) ELSE compiler_invoked_at END, "
        "updated_at=? WHERE id=? AND is_current=1 AND status='planning' AND owner_token=?",
        (
            stage, model_invoked, model_invoked, timestamp,
            compiler_invoked, compiler_invoked, timestamp,
            timestamp, generation_id, owner_token,
        ),
    )
    return updated.rowcount == 1


def fail_candidate_planning(
    generation_id: str,
    owner_token: str,
    reason: str,
    *,
    stage: str,
    model_invoked: bool,
    compiler_invoked: bool,
    now: datetime | None = None,
) -> bool:
    """Persist a terminal non-economic validation or Compiler failure."""

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    payload = json.dumps(
        {"outcome_class": "planning_failed", "reason": str(reason)[:160], "stage": stage},
        sort_keys=True,
        separators=(",", ":"),
    )
    updated = execute_write(
        "UPDATE candidate_planning_generations SET status='unavailable', result_kind='unavailable', "
        "result_json=?, retry_not_before=NULL, owner_token=NULL, claim_expires_at=NULL, "
        "completed_at=?, attention_status='terminal', attention_terminal_at=?, "
        "attention_terminal_reason='planning_failed', last_stage=?, "
        "model_invoked=MAX(model_invoked,?), model_invoked_at=CASE WHEN ? THEN COALESCE(model_invoked_at,?) ELSE model_invoked_at END, "
        "compiler_invoked=MAX(compiler_invoked,?), compiler_invoked_at=CASE WHEN ? THEN COALESCE(compiler_invoked_at,?) ELSE compiler_invoked_at END, "
        "outcome_class='planning_failed', outcome_reason=?, updated_at=? "
        "WHERE id=? AND is_current=1 AND status='planning' AND owner_token=?",
        (
            payload, timestamp, timestamp, stage,
            model_invoked, model_invoked, timestamp,
            compiler_invoked, compiler_invoked, timestamp,
            str(reason)[:160], timestamp, generation_id, owner_token,
        ),
    )
    return updated.rowcount == 1


def defer_candidate_planning(
    generation_id: str,
    owner_token: str,
    reason: str,
    *,
    retry_seconds: int = 60,
    max_attempts: int = 3,
    now: datetime | None = None,
    stage: str = "provider",
    model_invoked: bool = False,
    compiler_invoked: bool = False,
) -> bool:
    """Bound provider unavailability; never heartbeat-loop beyond max attempts."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp = current.isoformat()
    expire_candidate_attention(now=current)
    row = (
        get_db()
        .execute(
            "SELECT attempt_count, attention_status FROM candidate_planning_generations WHERE id=?",
            (generation_id,),
        )
        .fetchone()
    )
    if row is None or row["attention_status"] != "active":
        return False
    terminal = int(row["attempt_count"] or 0) >= max_attempts
    retry_at = None if terminal else (current + timedelta(seconds=retry_seconds)).isoformat()
    payload = json.dumps({"reason": str(reason)[:160]}, sort_keys=True)
    updated = execute_write(
        "UPDATE candidate_planning_generations SET status='unavailable', "
        "result_kind='unavailable', result_json=?, retry_not_before=?, "
        "owner_token=NULL, claim_expires_at=NULL, completed_at=?, "
        "attention_status=CASE WHEN ? THEN 'terminal' ELSE attention_status END, "
        "attention_terminal_at=CASE WHEN ? THEN ? ELSE attention_terminal_at END, "
        "attention_terminal_reason=CASE WHEN ? THEN 'planning_unavailable' ELSE attention_terminal_reason END, "
        "last_stage=?, model_invoked=MAX(model_invoked,?), "
        "model_invoked_at=CASE WHEN ? THEN COALESCE(model_invoked_at,?) ELSE model_invoked_at END, "
        "compiler_invoked=MAX(compiler_invoked,?), "
        "compiler_invoked_at=CASE WHEN ? THEN COALESCE(compiler_invoked_at,?) ELSE compiler_invoked_at END, "
        "outcome_class='planning_unavailable', outcome_reason=?, "
        "updated_at=? WHERE id=? AND is_current=1 AND attention_status='active' "
        "AND status='planning' AND owner_token=?",
        (
            payload,
            retry_at,
            timestamp if terminal else None,
            terminal,
            terminal,
            timestamp,
            terminal,
            stage,
            model_invoked,
            model_invoked,
            timestamp,
            compiler_invoked,
            compiler_invoked,
            timestamp,
            str(reason)[:160],
            timestamp,
            generation_id,
            owner_token,
        ),
    )
    return updated.rowcount == 1


def defer_candidate_planning_with_code(
    generation_id: str,
    owner_token: str,
    code: str,
    detail: str = "",
    *,
    retry_seconds: int = 60,
    max_attempts: int = 3,
    now: datetime | None = None,
    stage: str = "provider",
    model_invoked: bool = False,
    compiler_invoked: bool = False,
) -> bool:
    """Persist a bounded planner reason code while retaining retry semantics."""

    reason = str(code) if not detail else f"{str(code)}:{str(detail)}"
    return defer_candidate_planning(
        generation_id,
        owner_token,
        reason[:160],
        retry_seconds=retry_seconds,
        max_attempts=max_attempts,
        now=now,
        stage=stage,
        model_invoked=model_invoked,
        compiler_invoked=compiler_invoked,
    )


def release_candidate_planning_claim(
    generation_id: str,
    owner_token: str,
    reason: str,
    *,
    retry_seconds: int = 0,
    now: datetime | None = None,
) -> bool:
    """Release locally refused/cancelled work without consuming a model attempt."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp = current.isoformat()
    retry_at = (current + timedelta(seconds=max(int(retry_seconds), 0))).isoformat()
    payload = json.dumps({"reason": str(reason)[:160]}, sort_keys=True)
    updated = execute_write(
        "UPDATE candidate_planning_generations SET status='unavailable', "
        "attempt_count=MAX(attempt_count-1, 0), result_kind='unavailable', "
        "result_json=?, retry_not_before=?, owner_token=NULL, claim_expires_at=NULL, "
        "outcome_class='planning_unavailable', outcome_reason=?, "
        "completed_at=NULL, updated_at=? WHERE id=? AND is_current=1 "
        "AND attention_status='active' AND status='planning' AND owner_token=?",
        (payload, retry_at, str(reason)[:160], timestamp, generation_id, owner_token),
    )
    return updated.rowcount == 1


def bind_candidate_planner_input(
    generation_id: str, owner_token: str, payload: str, digest: str
) -> bool:
    """Bind a claimed generation to one immutable canonical model input."""
    if hashlib.sha256(payload.encode()).hexdigest() != digest:
        raise ValueError("planner_input_hash_mismatch")
    updated = execute_write(
        "UPDATE candidate_planning_generations SET planner_input_json=?,planner_input_hash=? "
        "WHERE id=? AND is_current=1 AND status='planning' AND owner_token=? "
        "AND (planner_input_hash IS NULL OR planner_input_hash=?)",
        (payload, digest, generation_id, owner_token, digest),
    )
    return updated.rowcount == 1


def bind_candidate_planning_hypothesis(
    generation_id: str, owner_token: str, payload: str,
    *, now: datetime | None = None,
) -> bool:
    """Checkpoint one validated model result under the existing claim and frozen input."""
    from aletheia.trading.entry_analysis import EntryAnalysisContext, EntryHypothesis, canonical_json, fingerprint, validate_hypothesis_references

    generation = get_candidate_planning_generation(generation_id)
    if generation is None or not generation.get("planner_input_json"):
        return False
    context = EntryAnalysisContext.model_validate_json(generation["planner_input_json"])
    if fingerprint(context.model_dump(mode="json")) != generation["planner_input_hash"]:
        raise ValueError("planner_input_hash_mismatch")
    hypothesis = EntryHypothesis.model_validate_json(payload)
    validate_hypothesis_references(hypothesis, context)
    payload = canonical_json(hypothesis.model_dump(mode="json"))
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    updated = execute_write(
        "UPDATE candidate_planning_generations SET validated_hypothesis_json=?, "
        "last_stage='hypothesis_validated', updated_at=? "
        "WHERE id=? AND is_current=1 AND status='planning' AND owner_token=? "
        "AND attention_status='active' AND planner_input_hash=? "
        "AND claim_expires_at>? AND soft_expires_at>? AND hard_expires_at>? "
        "AND (validated_hypothesis_json IS NULL OR validated_hypothesis_json=?)",
        (payload, timestamp, generation_id, owner_token, generation["planner_input_hash"],
         timestamp, timestamp, timestamp, payload),
    )
    return updated.rowcount == 1


def complete_candidate_planning_with_plan(
    generation_id: str,
    owner_token: str,
    plan,
    *,
    now: datetime | None = None,
) -> tuple[str, dict | None]:
    """Activate only a still-current claimed generation; retain stale result audit."""

    from aletheia.trading.entry_plan import EntryPlan, EntryPlanActivationError, entry_plan_payload

    plan = EntryPlan.model_validate(plan)
    if plan.planning_generation_id != generation_id:
        raise EntryPlanActivationError("entry_plan_generation_mismatch")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp = current.isoformat()
    expire_candidate_attention(now=current)
    payload, digest = entry_plan_payload(plan)
    with transaction(immediate=True):
        row = (
            get_db()
            .execute(
                "SELECT pg.*, um.execution_eligible AS candidate_execution_eligible "
                "FROM candidate_planning_generations pg "
                "LEFT JOIN universe_members um ON um.id=pg.last_candidate_id "
                "WHERE pg.id=?",
                (generation_id,),
            )
            .fetchone()
        )
        if row is None or row["owner_token"] != owner_token:
            return "claim_lost", None
        if (
            not bool(row["is_current"])
            or not bool(row["candidate_execution_eligible"])
            or not _candidate_attention_valid(dict(row), timestamp)
        ):
            execute_write(
                "UPDATE candidate_planning_generations SET result_kind='stale_result', "
                "result_json=?, owner_token=NULL, claim_expires_at=NULL, "
                "completed_at=?, updated_at=? WHERE id=?",
                (payload, timestamp, timestamp, generation_id),
            )
            return "stale", None
        if row["status"] != "planning":
            return "claim_lost", None
        if (row["validated_hypothesis_json"] is not None
            and plan.entry_hypothesis_json != row["validated_hypothesis_json"]):
            raise EntryPlanActivationError("entry_plan_hypothesis_checkpoint_mismatch")
        if plan.entry_hypothesis_json is not None:
            from aletheia.trading.entry_analysis import EntryAnalysisContext, fingerprint
            from aletheia.trading.entry_contract import entry_contract_fingerprints

            if (row["entry_behavior_fingerprint"], row["schema_fingerprint"]) != entry_contract_fingerprints():
                raise EntryPlanActivationError("entry_plan_running_contract_mismatch")

            try:
                frozen_context = EntryAnalysisContext.model_validate_json(
                    row["planner_input_json"] or ""
                )
            except Exception as exc:
                raise EntryPlanActivationError("entry_plan_frozen_context_invalid") from exc
            if (
                not row["entry_behavior_fingerprint"]
                or not row["schema_fingerprint"]
                or row["planner_input_hash"]
                != fingerprint(frozen_context.model_dump(mode="json"))
                or plan.analysis_context_fingerprint
                != frozen_context.context_fingerprint
                or frozen_context.planning_generation.get("entry_behavior_fingerprint")
                != row["entry_behavior_fingerprint"]
                or frozen_context.planning_generation.get("schema_fingerprint")
                != row["schema_fingerprint"]
            ):
                raise EntryPlanActivationError("entry_plan_frozen_context_mismatch")
            opportunity = frozen_context.candidate_opportunity_context
            selection = opportunity.get("candidate_selection") or {}
            mechanism = opportunity.get("opportunity") or {}
            if (
                opportunity.get("context_hash")
                != (get_candidate(str(row["last_candidate_id"])) or {}).get(
                    "candidate_opportunity_context_hash"
                )
                or mechanism.get("primary_mechanism") != row["opportunity_mechanism"]
                or mechanism.get("direction") != row["direction"]
                or mechanism.get("mechanism_version") != row["mechanism_version"]
                or selection.get("policy") != row["candidate_selection_policy"]
                or selection.get("policy_epoch") != row["candidate_selection_policy_epoch"]
            ):
                raise EntryPlanActivationError("entry_plan_candidate_opportunity_mismatch")
        if (
            plan.candidate_id != row["last_candidate_id"]
            or plan.venue != row["venue"]
            or plan.symbol != row["symbol"]
            or plan.market_type != row["market_type"]
            or plan.direction != row["direction"]
            or plan.candidate_family != row["candidate_family"]
        ):
            raise EntryPlanActivationError("entry_plan_candidate_identity_mismatch")
        record = EntryPlanRecord(
            id=plan.plan_id,
            planning_generation_id=generation_id,
            candidate_id=plan.candidate_id,
            planning_key=row["planning_key"],
            venue=plan.venue,
            symbol=plan.symbol,
            market_type=plan.market_type,
            direction=plan.direction,
            execution_mode=plan.execution_mode,
            plan_json=payload,
            plan_hash=digest,
            status="active" if plan.activation_policy == "IMMEDIATE" else "waiting",
            created_at=timestamp,
            updated_at=timestamp,
        )
        record_data = _stamp_provenance(record.model_dump())
        _insert("entry_plans", record_data)
        completed = execute_write(
            "UPDATE candidate_planning_generations SET status='planned', "
            "result_kind='entry_plan', result_json=?, entry_plan_id=?, "
            "owner_token=NULL, claim_expires_at=NULL, completed_at=?, "
            "attention_status='terminal', attention_terminal_at=?, "
            "attention_terminal_reason='entry_plan_created', last_stage='plan_created', "
            "outcome_class='entry_plan', outcome_reason='entry_plan_created', updated_at=? "
            "WHERE id=? AND is_current=1 AND attention_status='active' "
            "AND soft_expires_at>? AND hard_expires_at>? "
            "AND EXISTS (SELECT 1 FROM universe_members um "
            "WHERE um.id=candidate_planning_generations.last_candidate_id "
            "AND um.execution_eligible=1) AND status='planning' AND owner_token=?",
            (
                payload,
                plan.plan_id,
                timestamp,
                timestamp,
                timestamp,
                generation_id,
                timestamp,
                timestamp,
                owner_token,
            ),
        )
        if completed.rowcount != 1:
            raise RuntimeError("candidate_attention_completion_cas_failed")
        return "planned", record_data


def get_entry_plan(plan_id: str) -> dict | None:
    return _select_one("entry_plans", plan_id)


_ACTIVE_ENTRY_PLAN_STATUSES = (
    "waiting",
    "risk_reserved",
    "triggered",
    "ordered",
    "partially_filled",
)


def list_active_entry_plans(limit: int = 100) -> list[dict]:
    placeholders = ",".join("?" for _ in _ACTIVE_ENTRY_PLAN_STATUSES)
    return [
        dict(row)
        for row in get_db()
        .execute(
            f"SELECT * FROM entry_plans WHERE status IN ({placeholders}) ORDER BY updated_at DESC, id DESC LIMIT ?",
            (*_ACTIVE_ENTRY_PLAN_STATUSES, max(1, min(int(limit), 200))),
        )
        .fetchall()
    ]
def list_entry_plans(status: str | None = None, limit: int = 100) -> list[dict]:
    """List entry plans filtered by status ('active', 'expired', other, or all)."""
    norm_status = (status or "active").strip().lower()
    lim = max(1, min(int(limit), 200))
    if norm_status == "active":
        placeholders = ",".join("?" for _ in _ACTIVE_ENTRY_PLAN_STATUSES)
        query = f"SELECT * FROM entry_plans WHERE status IN ({placeholders}) ORDER BY updated_at DESC, id DESC LIMIT ?"
        params = (*_ACTIVE_ENTRY_PLAN_STATUSES, lim)
    elif norm_status == "expired":
        query = "SELECT * FROM entry_plans WHERE status = 'expired' ORDER BY updated_at DESC, id DESC LIMIT ?"
        params = (lim,)
    elif norm_status == "all":
        query = "SELECT * FROM entry_plans ORDER BY updated_at DESC, id DESC LIMIT ?"
        params = (lim,)
    else:
        query = "SELECT * FROM entry_plans WHERE status = ? ORDER BY updated_at DESC, id DESC LIMIT ?"
        params = (norm_status, lim)
    return [dict(row) for row in get_db().execute(query, params).fetchall()]


def list_current_candidate_workbench(snapshot_id: str) -> list[dict]:
    """Project every Candidate in one exact Snapshot with optional planning facts."""

    rows = [
        {
            **dict(row),
            "venue": row["venue"] or row["registry_venue"],
        }
        for row in get_db()
        .execute(
            "SELECT um.*, ur.venue AS registry_venue, "
            "pg.id AS planning_generation_id, pg.status AS planning_status, "
            "pg.result_kind, pg.result_json AS planning_result_json, "
            "pg.attempt_count, pg.attention_status, pg.soft_expires_at, "
            "pg.hard_expires_at, pg.attention_terminal_reason, pg.planning_key, "
            "pg.cycle_number, pg.last_stage, pg.model_invoked, pg.compiler_invoked, "
            "pg.outcome_class, pg.outcome_reason, "
            "ep.id AS entry_plan_id, ep.status AS entry_plan_status, "
            "ep.terminal_reason AS entry_plan_terminal_reason "
            "FROM universe_members um "
            "JOIN universe_registry ur ON ur.id=um.asset_id "
            "LEFT JOIN candidate_planning_generations pg "
            "ON pg.last_candidate_id=um.id AND pg.is_current=1 "
            "LEFT JOIN entry_plans ep ON ep.planning_generation_id=pg.id "
            "WHERE um.snapshot_id=? ORDER BY um.rank, um.id",
            (snapshot_id,),
        )
        .fetchall()
    ]
    from aletheia.trading.entry_lifecycle import project_candidate_entry_lifecycle

    cycles = list_candidate_planning_cycles_batch(planning_keys=[str(row["planning_key"]) for row in rows if row.get("planning_key")])
    return [
        {
            **_project_candidate_admission(row),
            **project_candidate_entry_lifecycle(
                row,
                cycles.get(str(row.get("planning_key")), []),
            ),
        }
        for row in rows
    ]


def get_entry_workbench_summary(*, now: datetime | None = None) -> dict[str, int]:
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_iso = timestamp.isoformat()
    warning_iso = (timestamp + timedelta(seconds=300)).isoformat()
    placeholders = ",".join("?" for _ in _ACTIVE_ENTRY_PLAN_STATUSES)
    row = (
        get_db()
        .execute(
            f"SELECT "
            f"(SELECT COUNT(*) FROM entry_plans WHERE status IN ({placeholders})) AS active_entry_plans, "
            "(SELECT COUNT(*) FROM candidate_planning_generations "
            " WHERE is_current=1 AND attention_status='active' "
            " AND soft_expires_at>? AND hard_expires_at>? AND attempt_count<3 "
            " AND COALESCE(outcome_class,'')!='planning_blocked' "
            " AND (status='pending' OR status='unavailable')) AS needs_planning, "
            "(SELECT COUNT(*) FROM candidate_planning_generations "
            " WHERE is_current=1 AND attention_status='active' "
            " AND MIN(soft_expires_at, hard_expires_at)>? "
            " AND MIN(soft_expires_at, hard_expires_at)<=?) + "
            f"(SELECT COUNT(*) FROM entry_plans WHERE status IN ({placeholders}) "
            " AND json_extract(plan_json, '$.expires_at')>? "
            " AND json_extract(plan_json, '$.expires_at')<=?) AS expiring_soon",
            (
                *_ACTIVE_ENTRY_PLAN_STATUSES,
                now_iso,
                now_iso,
                now_iso,
                warning_iso,
                *_ACTIVE_ENTRY_PLAN_STATUSES,
                now_iso,
                warning_iso,
            ),
        )
        .fetchone()
    )
    return {key: int(row[key] or 0) for key in row.keys()}


def list_planning_history(
    *,
    limit: int = 50,
    before: tuple[str, str] | None = None,
    now: datetime | None = None,
) -> list[dict]:
    where = ""
    params: list[object] = []
    if before is not None:
        where = "WHERE (pg.updated_at < ? OR (pg.updated_at = ? AND pg.id < ?))"
        params.extend((before[0], before[0], before[1]))
    params.append(max(1, min(int(limit), 201)))
    rows = [
        _effective_candidate_attention(dict(row), now=now)
        for row in get_db()
        .execute(
            "SELECT pg.*, ep.status AS entry_plan_status, ep.execution_mode, "
            "ep.order_id, ep.terminal_at AS entry_plan_terminal_at, "
            "ep.terminal_reason AS entry_plan_terminal_reason "
            "FROM candidate_planning_generations pg "
            "LEFT JOIN entry_plans ep ON ep.planning_generation_id=pg.id "
            f"{where} ORDER BY pg.updated_at DESC, pg.id DESC LIMIT ?",
            tuple(params),
        )
        .fetchall()
    ]
    return rows


def list_watchable_entry_plans(limit: int | None = 100) -> list[dict]:
    return _select_all(
        "entry_plans",
        where=("status IN ('waiting','active','triggered','risk_reserved','ordered','partially_filled')"),
        order="created_at ASC",
        limit=limit,
    )


def watchable_entry_plan_high_watermark() -> tuple[str, str] | None:
    row = (
        get_db()
        .execute(
            "SELECT created_at, id FROM entry_plans "
            "WHERE status IN ('waiting','active','triggered','risk_reserved','ordered','partially_filled') "
            "ORDER BY created_at DESC, id DESC LIMIT 1"
        )
        .fetchone()
    )
    return (str(row["created_at"]), str(row["id"])) if row else None


def list_watchable_entry_plans_page(
    *,
    after: tuple[str, str] | None,
    through: tuple[str, str],
    limit: int = 100,
) -> list[dict]:
    where = [
        "status IN ('waiting','active','triggered','risk_reserved','ordered','partially_filled')",
        "(created_at < ? OR (created_at = ? AND id <= ?))",
    ]
    params: list[str] = [through[0], through[0], through[1]]
    if after is not None:
        where.append("(created_at > ? OR (created_at = ? AND id > ?))")
        params.extend((after[0], after[0], after[1]))
    return _select_all(
        "entry_plans",
        where=" AND ".join(where),
        params=tuple(params),
        order="created_at ASC, id ASC",
        limit=limit,
    )


def get_entry_trigger_activation(activation_id: str) -> dict | None:
    row = get_db().execute("SELECT * FROM entry_trigger_activations WHERE id=?", (activation_id,)).fetchone()
    return dict(row) if row else None


def entry_plan_activation_can_refresh(plan_id: str, *, now: datetime | None = None) -> bool:
    """A new trigger can refresh an unsubmitted intent, never an exchange handoff."""
    if get_order_for_entry_plan(plan_id) is not None:
        return False
    proposals = _select_all("trade_proposals", where="entry_plan_id=?", params=(plan_id,), limit=None)
    for proposal in proposals:
        claim = get_proposal_execution_claim(proposal["id"])
        if (proposal["status"] not in {"pending", "approved"}
            or has_order_for_proposal(proposal["id"])
            or (claim is not None and proposal_execution_claim_is_fresh(claim, now or datetime.now(timezone.utc)))):
            return False
    return True


def bind_entry_trigger_activation(activation, *, expected_activation_id: str | None, now: datetime | None = None) -> bool:
    """Watcher-owned CAS: retain immutable proof before making the Plan executable."""
    from aletheia.execution.entry_plan_execution import require_entry_plan_runtime_authority
    from aletheia.trading.entry_activation import EntryTriggerActivationV1
    from aletheia.trading.entry_compat import decode_entry_plan

    activation = EntryTriggerActivationV1.model_validate(activation)
    with transaction(immediate=True):
        now = now or datetime.now(timezone.utc)
        row = get_entry_plan(activation.plan_id)
        if row is None or row["status"] not in {"waiting", "active"}:
            return False
        if not entry_plan_activation_can_refresh(activation.plan_id, now=now):
            return False
        plan = decode_entry_plan(row["plan_json"])
        require_entry_plan_runtime_authority(plan)
        activation.require_valid(plan, now=now)
        if row["plan_hash"] != activation.plan_hash:
            raise ValueError("entry_plan_hash_mismatch")
        if row.get("trigger_activation_id") != expected_activation_id:
            persisted = get_entry_trigger_activation(activation.activation_id)
            return (row.get("trigger_activation_id") == activation.activation_id
                    and persisted is not None and persisted["activation_hash"] == activation.activation_hash)
        monitoring = json.loads(row.get("monitoring_evidence_json") or "{}")
        snapshot = activation.snapshot
        if (row.get("monitoring_state") != "ready"
            or monitoring.get("stream_generation_id") != snapshot.stream_generation_id
            or monitoring.get("subscription_generation_id") != snapshot.subscription_generation_id):
            raise ValueError("entry_trigger_activation_monitoring_mismatch")
        if expected_activation_id:
            previous = EntryTriggerActivationV1.model_validate_json(
                get_entry_trigger_activation(expected_activation_id)["activation_json"])
            if (datetime.fromisoformat(previous.expires_at) > now
                and previous.snapshot.stream_generation_id == snapshot.stream_generation_id
                and previous.snapshot.subscription_generation_id == snapshot.subscription_generation_id):
                return False
        execute_write("INSERT INTO entry_trigger_activations "
            "(id,plan_id,activation_hash,activation_json,created_at,expires_at) VALUES (?,?,?,?,?,?)",
            (activation.activation_id, activation.plan_id, activation.activation_hash,
             activation.model_dump_json(), activation.created_at, activation.expires_at))
        updated = execute_write("UPDATE entry_plans SET trigger_activation_id=?, "
            "status=CASE WHEN status='waiting' THEN 'active' ELSE status END,updated_at=? "
            "WHERE id=? AND trigger_activation_id IS ? AND status IN ('waiting','active')",
            (activation.activation_id, now.isoformat(), activation.plan_id, expected_activation_id))
        if updated.rowcount != 1:
            raise ValueError("entry_trigger_activation_claim_lost")
        return True


def transition_entry_plan(
    plan_id: str,
    *,
    expected_status: str,
    status: str,
    order_id: str | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Monotonic lifecycle CAS; terminal plans cannot revive."""

    from aletheia.trading.entry_plan import entry_plan_transition_allowed

    if not entry_plan_transition_allowed(expected_status, status):
        raise ValueError("invalid_entry_plan_transition")
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    terminal = status in {"filled", "invalidated", "expired", "cancelled", "failed"}
    updated = execute_write(
        "UPDATE entry_plans SET status=?, order_id=COALESCE(?, order_id), updated_at=?, "
        "terminal_at=?, terminal_reason=? WHERE id=? AND status=?",
        (
            status,
            order_id,
            timestamp,
            timestamp if terminal else None,
            reason if terminal else None,
            plan_id,
            expected_status,
        ),
    )
    return updated.rowcount == 1


def record_entry_plan_monitoring(plan_id: str, evidence: dict) -> bool:
    """Persist only material readiness transitions, never every market tick."""

    row = get_entry_plan(plan_id)
    if row is None:
        return False

    def signature(value: dict) -> tuple:
        channels = value.get("channels") if isinstance(value.get("channels"), dict) else {}
        passive = value.get("passive_entry") or {}
        return (
            value.get("state"),
            value.get("stream_generation_id"),
            value.get("subscription_generation_id"),
            tuple(value.get("required_channels") or ()),
            passive.get("eligible"), passive.get("ever_eligible"),
            tuple(
                (name, facts.get("state"), facts.get("reason"))
                for name, facts in sorted(channels.items())
                if isinstance(facts, dict)
            ),
        )

    try:
        existing = json.loads(row.get("monitoring_evidence_json") or "null")
    except (TypeError, json.JSONDecodeError):
        existing = None
    previous_passive = existing.get("passive_entry", {}) if isinstance(existing, dict) else {}
    if "passive_entry" in evidence:
        passive = evidence["passive_entry"]
        first_eligible_at = previous_passive.get("first_eligible_at")
        if passive["eligible"] and not previous_passive.get("ever_eligible"):
            first_eligible_at = passive.get("observed_at")
        evidence = {**evidence, "passive_entry": {**passive,
            "first_eligible_at": first_eligible_at,
            "ever_eligible": bool(previous_passive.get("ever_eligible") or passive["eligible"]),
        }}
    elif previous_passive:
        evidence = {**evidence, "passive_entry": previous_passive}
    if isinstance(existing, dict) and signature(existing) == signature(evidence):
        return False
    evidence = {**evidence, "state_changed_at": datetime.now(timezone.utc).isoformat()}
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    execute_write(
        "UPDATE entry_plans SET monitoring_state=?, monitoring_evidence_json=?, "
        "monitoring_evidence_hash=? WHERE id=?",
        (
            str(evidence.get("state") or "unavailable"),
            payload,
            hashlib.sha256(payload.encode()).hexdigest(),
            plan_id,
        ),
    )
    return True


def list_opportunities_by_member(member_id: str, limit: int = 5) -> list[dict]:
    return _select_all(
        "opportunities",
        where=("universe_member_id = ? AND setup_id IS NOT NULL AND generation_version='opportunity-generation/v2'"),
        params=(member_id,),
        order="created_at DESC",
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Universe Forward Outcomes
# ---------------------------------------------------------------------------

_OUTCOME_MARKET_SQL = (
    "CASE WHEN market_type IN ('perp','futures','swap','usdt_futures') "
    "THEN 'perp' ELSE COALESCE(market_type, 'spot') END"
)
SHADOW_OUTCOME_ENTRY_ALIGNMENT_MAX_SEC = 60


def complete_outcome_economics_sql(table_alias: str = "") -> str:
    """Return the shared SQLite predicate for decision-grade net outcomes."""
    prefix = f"{table_alias}." if table_alias else ""
    return (
        f"typeof({prefix}venue) = 'text' "
        f"AND TRIM({prefix}venue, {SQLITE_PYTHON_STRIP_CHARS_SQL}) <> '' "
        f"AND typeof({prefix}direction) = 'text' "
        f"AND LOWER({prefix}direction) IN ('long', 'short') "
        f"AND {valid_settlement_price_provenance_sql(table_alias)} "
        f"AND typeof({prefix}entry_price_usd) IN ('integer', 'real') "
        f"AND {prefix}entry_price_usd > 0 "
        f"AND {prefix}entry_price_usd < 1e999 "
        f"AND typeof({prefix}exit_price_usd) IN ('integer', 'real') "
        f"AND {prefix}exit_price_usd > 0 "
        f"AND {prefix}exit_price_usd < 1e999 "
        f"AND typeof({prefix}directional_return_pct) IN ('integer', 'real') "
        f"AND ABS({prefix}directional_return_pct) < 1e999 "
        f"AND ABS({prefix}directional_return_pct - ("
        f"CASE WHEN LOWER({prefix}direction) = 'short' THEN -1.0 ELSE 1.0 END "
        f"* (({prefix}exit_price_usd - {prefix}entry_price_usd) "
        f"/ {prefix}entry_price_usd * 100.0))) <= 1e-8 "
        f"AND typeof({prefix}estimated_fee_pct) IN ('integer', 'real') "
        f"AND {prefix}estimated_fee_pct >= 0 "
        f"AND {prefix}estimated_fee_pct < 1e999 "
        f"AND typeof({prefix}estimated_spread_slippage_pct) IN ('integer', 'real') "
        f"AND {prefix}estimated_spread_slippage_pct >= 0 "
        f"AND {prefix}estimated_spread_slippage_pct < 1e999 "
        f"AND typeof({prefix}estimated_funding_pct) IN ('integer', 'real') "
        f"AND ABS({prefix}estimated_funding_pct) < 1e999 "
        f"AND typeof({prefix}market_type) = 'text' "
        f"AND LOWER({prefix}market_type) IN "
        "('spot', 'perp', 'futures', 'swap', 'usdt_futures') "
        f"AND (LOWER({prefix}market_type) <> 'spot' "
        f"OR {prefix}estimated_funding_pct = 0) "
        f"AND typeof({prefix}net_return_pct) IN ('integer', 'real') "
        f"AND ABS({prefix}net_return_pct - ({prefix}directional_return_pct "
        f"- {prefix}estimated_fee_pct - {prefix}estimated_spread_slippage_pct "
        f"- {prefix}estimated_funding_pct)) <= 1e-8 "
        f"AND typeof({prefix}cost_model_version) = 'text' "
        f"AND TRIM({prefix}cost_model_version, {SQLITE_PYTHON_STRIP_CHARS_SQL}) <> '' "
        f"AND typeof({prefix}min_tradable_edge_pct) IN ('integer', 'real') "
        f"AND {prefix}min_tradable_edge_pct >= 0 "
        f"AND {prefix}min_tradable_edge_pct < 1e999"
    )


_COMPLETE_OUTCOME_ECONOMICS_SQL = complete_outcome_economics_sql()


def _settle_outcome_with_exact_venue(
    table: str,
    outcome_id: str,
    updates: dict,
    *,
    kind: str,
) -> bool:
    guard = nullcontext(True)
    initial = _select_one(table, outcome_id)
    window_id = (
        str(initial.get("benchmark_window_id"))
        if table == "filter_candidate_outcomes" and initial and initial.get("benchmark_window_id")
        else None
    )
    if window_id:
        if _lab_outcome_guard is None:
            raise ExperimentStateReadError("Observation outcome guard unavailable")
        guard = _lab_outcome_guard(window_id)
    with guard as write_allowed:
        if not write_allowed:
            return False
        with transaction(immediate=True):
            row = _select_one(table, outcome_id)
            if row is None or str(row.get("status") or "").lower() != "pending":
                return False
            venue = row.get("venue")
            if not isinstance(venue, str) or not venue.strip():
                raise ValueError(f"{kind} outcome requires a nonblank venue")
            candidate = {**row, **updates}
            if candidate.get("net_return_pct") is not None:
                from aletheia.analytics.outcome_semantics import (
                    valid_settlement_price_provenance,
                )

                if (
                    "settlement_price_source" not in updates
                    or "settlement_lag_sec" not in updates
                    or not valid_settlement_price_provenance(candidate)
                ):
                    raise ValueError(f"{kind} outcome requires decision-grade price provenance")
            if table == "filter_candidate_outcomes":
                try:
                    from aletheia.agent_runtime.runtime_session import current_provenance

                    provenance = current_provenance()
                except Exception as exc:
                    logger.warning("repositories: settlement provenance unavailable (%s)", type(exc).__name__)
                    provenance = {}
                updates = {
                    **updates,
                    "settlement_runtime_session_id": provenance.get("runtime_session_id"),
                    "settlement_producer_commit": provenance.get("producer_commit"),
                    "settlement_behavior_fingerprint": provenance.get("behavior_fingerprint"),
                }
            won = _update_pending(table, outcome_id, updates)
            if won and table == "filter_candidate_outcomes" and row.get("horizon") == "24h":
                settle_linked_shadow_outcomes(str(row.get("outcome_candidate_key") or ""))
            return won


def insert_forward_outcomes_batch(outcomes: list) -> None:
    if not outcomes:
        return
    with transaction():
        _insert_many(
            "universe_forward_outcomes",
            [_forward_outcome_insert_payload(outcome) for outcome in outcomes],
        )


# ---------------------------------------------------------------------------
# Opportunity filter evidence
# ---------------------------------------------------------------------------


_UNIVERSE_DISCOVERY_BATCH_COLUMNS = (
    "id", "dedup_key", "run_id", "candidate_kind", "filter_name", "stage",
    "candidate_count", "passed_count", "rejected_count", "not_applicable_count",
    "error_count", "candidate_facts", "parameter_snapshot", "filter_version",
    "code_commit", "evaluated_at", "benchmark_window_id", "benchmark_batch_id",
)
_UNIVERSE_DISCOVERY_OUTCOME_COLUMNS = (
    "id", "outcome_candidate_key", "venue", "symbol", "market_type", "direction",
    "scanner_family", "regime_tag", "horizon", "allocation_selected",
    "entry_price_usd", "entry_spread_bps", "funding_rate_at_entry", "entry_ts",
    "due_at", "settled_at", "exit_price_usd", "gross_return_pct",
    "directional_return_pct", "estimated_fee_pct", "estimated_spread_slippage_pct",
    "estimated_funding_pct", "net_return_pct", "min_tradable_edge_pct",
    "min_cost_covering_edge_pct", "mfe_pct",
    "mae_pct", "settlement_price_source", "settlement_lag_sec", "cost_model_version",
    "analysis_notional_quote", "producer_runtime_session_id", "producer_commit",
    "producer_behavior_fingerprint", "settlement_runtime_session_id",
    "settlement_producer_commit", "settlement_behavior_fingerprint", "status",
    "rsi_1h_at_entry", "rsi_15m_at_entry", "return_24h_at_entry",
    "direction_exhaustion_signal", "benchmark_window_id", "benchmark_batch_id",
    "benchmark_cohorts", "benchmark_cost_snapshot", "benchmark_path_facts",
)


def capture_universe_discovery_batch(
    window_id: str,
    batch,
    outcomes: list[dict],
    *,
    captured_at: str,
    cadence_sec: int = 4 * 60 * 60,
    max_batches: int = 84,
    window_snapshot: dict | None = None,
) -> dict:
    """Atomically accept one benchmark population batch and its outcome intents."""
    batch_data = _benchmark_payload(batch)
    batch_id = str(batch_data.get("benchmark_batch_id") or batch_data.get("id") or "")
    if not batch_id:
        raise ValueError("benchmark batch id is required")
    batch_data.update({"benchmark_window_id": window_id, "benchmark_batch_id": batch_id})
    captured_dt = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
    if captured_dt.tzinfo is None:
        captured_dt = captured_dt.replace(tzinfo=timezone.utc)
    captured_dt = captured_dt.astimezone(timezone.utc)

    with transaction(immediate=True):
        stored_window = get_db().execute(
            "SELECT * FROM universe_discovery_benchmarks WHERE id=?", (window_id,)
        ).fetchone()
        window = window_snapshot or stored_window
        if window is None:
            raise KeyError(window_id)
        if str(window["status"]) != "active":
            return {"accepted": False, "reason": f"window_{window['status']}", "batch_id": batch_id}
        existing = get_db().execute(
            "SELECT b.id, b.code_commit, ("
            "SELECT o.producer_runtime_session_id FROM filter_candidate_outcomes o "
            "WHERE o.benchmark_window_id=b.benchmark_window_id AND o.benchmark_batch_id=b.benchmark_batch_id "
            "AND o.producer_runtime_session_id IS NOT NULL LIMIT 1) AS producer_runtime_session_id "
            "FROM filter_decision_batches b WHERE b.benchmark_window_id=? AND b.benchmark_batch_id=?",
            (window_id, batch_id),
        ).fetchone()
        if existing:
            return {
                "accepted": True, "idempotent": True, "reason": "already_captured",
                "batch_id": batch_id, "_producer_commit": existing["code_commit"],
                "_producer_runtime_session_id": existing["producer_runtime_session_id"],
            }
        capture_count = get_db().execute(
            "SELECT COUNT(*) FROM filter_decision_batches WHERE benchmark_window_id=?",
            (window_id,),
        ).fetchone()[0]
        if int(capture_count) >= max_batches:
            return {"accepted": False, "reason": "capture_limit_reached", "batch_id": batch_id}
        started_dt = datetime.fromisoformat(str(window["started_at"]).replace("Z", "+00:00"))
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=timezone.utc)
        if captured_dt < started_dt.astimezone(timezone.utc):
            return {"accepted": False, "reason": "capture_before_window", "batch_id": batch_id}
        end_dt = datetime.fromisoformat(
            str(dict(window).get("capture_ends_at") or window["collect_until"]).replace("Z", "+00:00")
        )
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        if captured_dt >= end_dt.astimezone(timezone.utc):
            return {"accepted": False, "reason": "capture_window_closed", "batch_id": batch_id}
        latest = get_db().execute(
            "SELECT evaluated_at FROM filter_decision_batches "
            "WHERE benchmark_window_id=? ORDER BY evaluated_at DESC, id DESC LIMIT 1",
            (window_id,),
        ).fetchone()
        if latest and latest["evaluated_at"]:
            previous_dt = datetime.fromisoformat(str(latest["evaluated_at"]).replace("Z", "+00:00"))
            if previous_dt.tzinfo is None:
                previous_dt = previous_dt.replace(tzinfo=timezone.utc)
            if (captured_dt - previous_dt.astimezone(timezone.utc)).total_seconds() < cadence_sec:
                return {"accepted": False, "reason": "cadence_not_due", "batch_id": batch_id}

        batch_row = {column: batch_data[column] for column in _UNIVERSE_DISCOVERY_BATCH_COLUMNS}
        _insert("filter_decision_batches", batch_row)

        outcome_rows: list[dict] = []
        for outcome in outcomes:
            data = _benchmark_payload(outcome)
            data.update({"benchmark_window_id": window_id, "benchmark_batch_id": batch_id})
            if "benchmark_cohorts" not in data or data["benchmark_cohorts"] is None:
                data["benchmark_cohorts"] = "[]"
            outcome_rows.append({column: data.get(column) for column in _UNIVERSE_DISCOVERY_OUTCOME_COLUMNS})
        inserted_outcomes = 0
        if outcome_rows:
            columns = list(_UNIVERSE_DISCOVERY_OUTCOME_COLUMNS)
            before = get_db().total_changes
            execute_many_write(
                f"INSERT INTO filter_candidate_outcomes ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)}) "
                "ON CONFLICT(outcome_candidate_key, horizon) DO NOTHING",
                [[row[column] for column in columns] for row in outcome_rows],
            )
            inserted_outcomes = get_db().total_changes - before
        if stored_window is not None:
            execute_write(
                "UPDATE universe_discovery_benchmarks SET capture_count=capture_count+1, last_capture_at=? WHERE id=?",
                (captured_dt.isoformat(), window_id),
            )
    return {
        "accepted": True,
        "idempotent": False,
        "batch_id": batch_id,
        "outcomes_inserted": inserted_outcomes,
    }


# A row is unreachable when the outcome write guard would refuse its window. The
# guard resolves a window through two authorities — the legacy registry for windows
# it knows, the Lab for everything else — and refuses a window neither has heard of.
# "The registry has no active row" alone is NOT that question: it also matches a live
# Lab-native window, and closing on it destroyed 96 live rows in
# tests/test_universe_discovery_benchmark.py. So the narrow registry-only form below
# is only the fallback for when the Lab reader is unavailable.
FILTER_OUTCOME_BENCHMARK_DEAD_SQL = (
    "(benchmark_window_id IS NOT NULL AND EXISTS ("
    "SELECT 1 FROM universe_discovery_benchmarks b "
    "WHERE b.id=filter_candidate_outcomes.benchmark_window_id AND b.status<>'active'))"
)


def list_observation_filter_outcome_priority_keys(*, deadline: float) -> tuple[set[str], set[str]]:
    if _lab_priority_keys is None:
        raise OSError("Observation reader unavailable")
    return _lab_priority_keys(deadline=deadline)


_PRIORITY_SCAN_BATCH_ROWS = 2000
# The open-experiment keys attach to RECENT candidates, so when a backlog exists the
# priority rows sit at the new end of the due range and a short scan finds none of
# them (measured: 0 matches in the oldest 60k of a 193k-row backlog). The cap must
# therefore admit a whole realistic backlog; the caller's deadline is the real guard.
_PRIORITY_SCAN_CAP_ROWS = 400000


# --- Filter feedback controller (shadow recommendations) ---


def insert_controller_recommendation(rec: dict) -> None:
    """Append one shadow recommendation row. Never updates existing rows."""
    _insert("controller_recommendations", rec)


def list_filter_shadow_experiments(*, status: str | None = None, limit: int = 100) -> list[dict]:
    if _canonical_lab_authority():
        return []
    if status:
        return _select_all(
            "filter_shadow_experiments",
            where="status = ?",
            params=(status,),
            order="started_at DESC",
            limit=limit,
        )
    return _select_all("filter_shadow_experiments", order="started_at DESC", limit=limit)


# Observation Lab v1 (Issue #1072): ``filter_shadow_frozen_units`` lives in the
# Lab's own SQLite file. See ``extensions.observation_lab.units`` for its writer
# helpers — this module intentionally does not touch it, keeping ``app.db``
# repositories unaware of Lab state.


def calibration_stats(*, cost_model_version: str) -> list[dict]:
    """Aggregate one exact cost-model cohort by family/direction/market/horizon.

    Legacy gross-only rows are intentionally excluded. A win must clear the
    persisted minimum tradable edge after fees, spread/slippage and funding.
    """
    from aletheia.analytics.learning_window import clean_window_cutoff

    db = get_db()
    cutoff = clean_window_cutoff("1970-01-01T00:00:00+00:00")
    rows = db.execute(
        "SELECT scanner_family, direction, "
        f"{_OUTCOME_MARKET_SQL} AS market_type, horizon, "
        "cost_model_version, "
        "COUNT(*) as n, "
        "AVG(net_return_pct) as avg_return, "
        "SUM(CASE WHEN net_return_pct > min_cost_covering_edge_pct "
        "  THEN 1 ELSE 0 END) as win_count, "
        "MIN(net_return_pct) as min_return, "
        "MAX(net_return_pct) as max_return "
        "FROM universe_forward_outcomes "
        "WHERE status = 'settled' "
        "AND entry_ts >= ? "
        "AND cost_model_version = ? "
        f"AND {_COMPLETE_OUTCOME_ECONOMICS_SQL} "
        "AND direction IN ('long', 'short') "
        f"GROUP BY scanner_family, direction, {_OUTCOME_MARKET_SQL}, horizon, "
        "cost_model_version "
        "ORDER BY scanner_family, direction, market_type, horizon",
        (cutoff, cost_model_version),
    ).fetchall()
    lineage = _calibration_source_lineage(db, cutoff, cost_model_version)
    result = []
    for raw in rows:
        row = dict(raw)
        key = (
            row.get("scanner_family"),
            row.get("direction"),
            row.get("market_type"),
            row.get("horizon"),
        )
        source = lineage.get(key)
        row["source_outcome_fingerprint"] = source["fingerprint"] if source else None
        row["source_window_start"] = cutoff
        row["independent_n"] = source["independent_n"] if source else 0
        row["distinct_symbols"] = source["distinct_symbols"] if source else 0
        row["distinct_days"] = source["distinct_days"] if source else 0
        result.append(row)
    return result


def _horizon_to_seconds(h: str) -> int:
    if h.endswith("m"):
        return int(h[:-1]) * 60
    if h.endswith("h"):
        return int(h[:-1]) * 3600
    if h.endswith("d"):
        return int(h[:-1]) * 86400
    raise ValueError(f"unknown horizon: {h!r}")


def _horizon_separated_counts(
    rows: list[tuple[str, str, str]],
    horizon: str,
) -> tuple[int, int, int]:
    """Greedy horizon-separated counts for (symbol, venue, entry_ts) rows.

    Within each (symbol, venue) pair, admit a row only if its entry_ts is at
    least one target horizon after the last admitted row for that pair.
    Returns (independent_n, distinct_symbols, distinct_days).
    """
    from datetime import datetime

    try:
        horizon_sec = _horizon_to_seconds(horizon)
    except (ValueError, TypeError):
        horizon_sec = 0

    sorted_rows = sorted(rows, key=lambda r: (r[0], r[1], r[2]))
    last_admitted: dict[tuple, float] = {}
    admitted_symbols: set[str] = set()
    admitted_days: set[str] = set()
    independent_n = 0

    for symbol, venue, entry_ts_str in sorted_rows:
        try:
            ts = datetime.fromisoformat(entry_ts_str.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            continue
        sv = (symbol, venue)
        last = last_admitted.get(sv)
        if last is None or ts >= last + horizon_sec:
            last_admitted[sv] = ts
            independent_n += 1
            admitted_symbols.add(symbol)
            admitted_days.add(entry_ts_str[:10])

    return independent_n, len(admitted_symbols), len(admitted_days)


def _calibration_source_lineage(
    db,
    cutoff: str,
    cost_model_version: str,
) -> dict[tuple, dict]:
    """Hash every exact row consumed by ``calibration_stats`` without retaining it."""
    cursor = db.execute(
        "SELECT scanner_family, direction, "
        f"{_OUTCOME_MARKET_SQL} AS market_type, horizon, id, "
        "outcome_candidate_key, member_id, symbol, venue, entry_ts, settled_at, net_return_pct, "
        "min_tradable_edge_pct, cost_model_version "
        "FROM universe_forward_outcomes "
        "WHERE status = 'settled' "
        "AND entry_ts >= ? "
        "AND cost_model_version = ? "
        f"AND {_COMPLETE_OUTCOME_ECONOMICS_SQL} "
        "AND direction IN ('long', 'short') "
        "ORDER BY scanner_family, direction, market_type, horizon, id",
        (cutoff, cost_model_version),
    )
    hashers: dict[tuple, object] = {}
    counts: dict[tuple, int] = {}
    group_rows: dict[tuple, list] = {}
    for raw in cursor:
        row = dict(raw)
        key = (
            row.pop("scanner_family"),
            row.pop("direction"),
            row.pop("market_type"),
            row.pop("horizon"),
        )
        symbol = row.pop("symbol", None) or ""
        venue = row.pop("venue", None) or ""
        entry_ts = row.get("entry_ts")
        hasher = hashers.setdefault(key, hashlib.sha256())
        canonical = json.dumps(row, sort_keys=True, separators=(",", ":"))
        hasher.update(canonical.encode("utf-8"))
        hasher.update(b"\n")
        counts[key] = counts.get(key, 0) + 1
        if entry_ts:
            group_rows.setdefault(key, []).append((symbol, venue, entry_ts))
    result = {}
    for key, hasher in hashers.items():
        horizon = key[3]
        ind_n, dist_sym, dist_days = _horizon_separated_counts(group_rows.get(key, []), horizon)
        result[key] = {
            "fingerprint": hasher.hexdigest(),
            "count": counts[key],
            "independent_n": ind_n,
            "distinct_symbols": dist_sym,
            "distinct_days": dist_days,
        }
    return result


def family_spread_tolerance_stats(
    *,
    cost_model_version: str,
    cutoff: str | None = None,
    settled_through: str | None = None,
) -> list[dict]:
    """Per-family spread sensitivity from settled universe forward outcomes.

    Splits outcomes into low-spread (<=15 bps) and high-spread (>15 bps) buckets to
    measure whether a family retains positive net expectancy at higher spreads. Used
    by filter_advisor to compute per-family spread tolerance multipliers.
    """
    from aletheia.analytics.learning_window import clean_window_cutoff

    db = get_db()
    cutoff = cutoff or clean_window_cutoff("1970-01-01T00:00:00+00:00")
    settled_clause = "AND settled_at <= ? " if settled_through is not None else ""
    rows = db.execute(
        "SELECT scanner_family, direction, "
        f"{_OUTCOME_MARKET_SQL} AS market_type, horizon, "
        "cost_model_version, "
        "COUNT(*) AS n, "
        "AVG(net_return_pct) AS avg_net, "
        "MIN(net_return_pct) AS min_net, "
        "COUNT(CASE WHEN entry_spread_bps <= 15 THEN 1 END) AS n_low_spread, "
        "AVG(CASE WHEN entry_spread_bps <= 15 THEN net_return_pct END) AS avg_net_low_spread, "
        "COUNT(CASE WHEN entry_spread_bps > 15 THEN 1 END) AS n_high_spread, "
        "AVG(CASE WHEN entry_spread_bps > 15 THEN net_return_pct END) AS avg_net_high_spread "
        "FROM universe_forward_outcomes "
        "WHERE status = 'settled' "
        "AND cost_model_version = ? "
        "AND scanner_family IS NOT NULL "
        "AND entry_spread_bps IS NOT NULL "
        f"AND {_COMPLETE_OUTCOME_ECONOMICS_SQL} "
        "AND direction IN ('long', 'short') "
        "AND entry_ts >= ? "
        f"{settled_clause}"
        f"GROUP BY scanner_family, direction, {_OUTCOME_MARKET_SQL}, horizon, "
        "cost_model_version "
        "ORDER BY scanner_family, direction, market_type, horizon",
        (
            (cost_model_version, cutoff, settled_through)
            if settled_through is not None else (cost_model_version, cutoff)
        ),
    ).fetchall()
    return [dict(r) for r in rows]


def family_direction_edge(
    horizon: str = "24h",
    within_days: int = 7,
    *,
    cost_model_version: str,
) -> list[dict]:
    """Per-(scanner_family, direction, market_type) cost-aware edge.

    Risk live-family evidence and explicit research query. Like
    calibration_stats(), this keeps long/short and spot/perpetual evidence
    separate; unlike calibration_stats(), it selects one horizon and recent
    time window. Reuses the existing universe_forward_outcomes store and
    excludes legacy gross-only rows.

    Returns rows: {scanner_family, direction, market_type, n, win_rate, avg_return}.
    Only families with n >= 1 in the window are returned.
    """
    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
    rows = db.execute(
        "SELECT scanner_family, direction, "
        f"{_OUTCOME_MARKET_SQL} AS market_type, "
        "cost_model_version, "
        "COUNT(*) as n, "
        "AVG(net_return_pct) as avg_return, "
        "SUM(CASE WHEN net_return_pct > min_cost_covering_edge_pct "
        "  THEN 1 ELSE 0 END) as win_count "
        "FROM universe_forward_outcomes "
        "WHERE status = 'settled' AND horizon = ? "
        "AND settled_at >= ? "
        "AND cost_model_version = ? "
        f"AND {_COMPLETE_OUTCOME_ECONOMICS_SQL} "
        "AND scanner_family IS NOT NULL "
        "AND direction IN ('long', 'short') "
        f"GROUP BY scanner_family, direction, {_OUTCOME_MARKET_SQL}, "
        "cost_model_version "
        "ORDER BY n DESC",
        (horizon, cutoff, cost_model_version),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        n = d.get("n") or 0
        d["win_rate"] = round((d.get("win_count") or 0) / n * 100, 1) if n else None
        d["avg_return"] = round(d["avg_return"], 3) if d.get("avg_return") is not None else None
        out.append(d)
    return out


def guard_candidate_exists_recent(
    symbol: str,
    scanner_family: str,
    within_days: int = 7,
    *,
    direction: str | None = None,
    market_type: str | None = None,
) -> bool:
    """Return whether the same scoped auto-guard candidate already exists."""
    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
    title_pattern = f"%{symbol}%{scanner_family}%"
    if direction:
        title_pattern += f"{direction}%"
    if market_type:
        title_pattern += f"{market_type}%"
    row = db.execute(
        "SELECT 1 FROM knowledge_items "
        "WHERE item_type = 'guard_candidate' "
        "AND title LIKE ? "
        "AND created_at >= ? "
        "LIMIT 1",
        (title_pattern, cutoff),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Data Collectors (market data)
# ---------------------------------------------------------------------------


def insert_orderbook_snapshot(data: dict) -> None:
    from aletheia.storage.models import new_id

    if "id" not in data:
        data["id"] = new_id()
    _insert("orderbook_snapshots", data)


def latest_orderbook(symbol: str, exchange: str = "", market_type: str = "") -> dict | None:
    where = ["symbol = ?"]
    params: list = [symbol]
    if exchange:
        where.append("exchange = ?")
        params.append(exchange.lower())
    if market_type:
        where.append("market_type = ?")
        params.append(market_type)
    rows = _select_all(
        "orderbook_snapshots",
        where=" AND ".join(where),
        params=tuple(params),
        order="observed_at DESC",
        limit=1,
    )
    return rows[0] if rows else None


# Write-side dedup thresholds (Phase 3). If the newest row for a (symbol,
# exchange) has identical values and source-time provenance AND is younger
# than the heartbeat floor, skip the INSERT. Once the newest row crosses the
# floor,
# the next INSERT lands regardless — the doctor probe reads MAX(observed_at)
# so a stale-newest would trip freshness thresholds.
_FUNDING_DEDUP_FIELDS = (
    "funding_rate",
    "funding_rate_annualized",
    "next_funding_time",
    "mark_price",
)
_FUNDING_HEARTBEAT_FLOOR_MINUTES = 10


def _funding_source_semantics(data: dict) -> dict:
    metadata = json.loads(data.get("derivatives_metadata_json") or "{}")
    # Acquisition advances on native retries; only changed source semantics
    # should bypass the existing ten-minute numeric heartbeat.
    metadata.pop("acquired_at", None)
    metadata.pop("source_observed_at", None)
    return metadata


def insert_funding_rate_snapshot(data: dict) -> None:
    """Skip unchanged rate observations within the ten-minute heartbeat window."""
    from aletheia.storage.models import new_id

    if "id" not in data:
        data["id"] = new_id()
    symbol = data.get("symbol")
    exchange = data.get("exchange")
    if symbol and exchange is not None:
        db = get_db()
        register_instant_us(db)
        row = db.execute(
            "SELECT funding_rate, funding_rate_annualized, next_funding_time, "
            "mark_price, observed_at, source_observed_at, acquired_at, "
            "source_time_status, derivatives_metadata_json "
            "FROM funding_rate_snapshots "
            "WHERE symbol = ? AND exchange = ? "
            "ORDER BY COALESCE(aletheia_instant_us(acquired_at), "
            "aletheia_instant_us(observed_at)) DESC, rowid DESC LIMIT 1",
            (symbol, exchange),
        ).fetchone()
        if row is not None:
            latest = dict(row)
            same = (
                all(data.get(field) == latest.get(field) for field in _FUNDING_DEDUP_FIELDS)
                and _funding_source_semantics(data) == _funding_source_semantics(latest)
                and data.get("source_time_status") == latest.get("source_time_status")
                and iso_instant_us(data.get("source_observed_at"))
                == iso_instant_us(latest.get("source_observed_at"))
                and (iso_instant_us(data.get("acquired_at")) is None)
                == (iso_instant_us(latest.get("acquired_at")) is None)
            )
            heartbeat_at = latest.get("acquired_at") or latest.get("observed_at")
            if same and heartbeat_at:
                try:
                    prev_ts = datetime.fromisoformat(str(heartbeat_at))
                    if prev_ts.tzinfo is None:
                        prev_ts = prev_ts.replace(tzinfo=timezone.utc)
                    age = datetime.now(timezone.utc) - prev_ts
                    if age < timedelta(minutes=_FUNDING_HEARTBEAT_FLOOR_MINUTES):
                        return
                except (TypeError, ValueError):
                    logger.warning("repositories: heartbeat parse failed for funding snapshot", exc_info=True)
    _insert("funding_rate_snapshots", data,
        ignore_existing_id=bool(json.loads(data.get('derivatives_metadata_json') or '{}').get('observation_only')))


# Cross-venue / secondary feeds (Hyperliquid and Coinalyze aggregate data)
# share the funding/OI tables via the `exchange` tag, but must NOT win the
# per-symbol "latest" read that the LLM context, universe features and the
# primary collector's own delta all consume as the PRIMARY trading venue's
# view. Excluded by default; pass exchange=<name> to target one venue exactly.
_CROSS_VENUE_TAGS = ("hyperliquid", "hyperliquid_predicted", "coinalyze")
LEGACY_DERIVATIVES_WHERE = "COALESCE(json_extract(derivatives_metadata_json, '$.observation_only'), 0) = 0"


def _latest_deriv_where(exchange: str | None) -> tuple[str, tuple]:
    """Build (where, params) for a per-symbol latest derivatives read.

    exchange=None  → primary view: any venue EXCEPT the cross-venue secondaries.
    exchange="x"   → exactly that venue's rows.
    """
    if exchange is not None:
        return f"symbol = ? AND exchange = ? AND {LEGACY_DERIVATIVES_WHERE}", (exchange,)
    placeholders = ",".join("?" * len(_CROSS_VENUE_TAGS))
    return (
        f"symbol = ? AND (exchange IS NULL OR exchange NOT IN ({placeholders})) AND {LEGACY_DERIVATIVES_WHERE}",
        _CROSS_VENUE_TAGS,
    )


def latest_funding_rate(symbol: str, exchange: str | None = None) -> dict | None:
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


def insert_open_interest_snapshot(data: dict) -> None:
    from aletheia.storage.models import new_id

    if "id" not in data:
        data["id"] = new_id()
    _insert("open_interest_snapshots", data,
        ignore_existing_id=bool(json.loads(data.get('derivatives_metadata_json') or '{}').get('observation_only')))


def insert_perp_price_snapshot(data: dict) -> None:
    _insert('perp_price_snapshots', data, ignore_existing_id=True)


class CollectorBatchPersistError(RuntimeError):
    """Identify which collector domain caused an atomic batch rollback."""

    def __init__(self, stage: str, cause: BaseException) -> None:
        self.stage = stage
        super().__init__(f"collector batch {stage} persistence failed: {cause}")


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


def insert_liquidation_event(data: dict) -> None:
    from aletheia.storage.models import new_id

    if "id" not in data:
        data["id"] = new_id()
    _insert("liquidation_events", data)


def recent_liquidations(symbol: str, hours: int = 1) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return _select_all(
        "liquidation_events",
        where="symbol = ? AND event_time > ?",
        params=(symbol, cutoff),
        order="event_time DESC",
        limit=200,
    )


def insert_perp_structure_snapshot(data: dict) -> None:
    """Persist one observation-only perp structure snapshot."""
    from aletheia.storage.models import new_id

    if "id" not in data:
        data["id"] = new_id()
    if "raw_json" not in data:
        data["raw_json"] = "{}"
    _insert("perp_structure_snapshots", data)


def insert_perp_structure_shadow_outcome(data: dict) -> None:
    """Insert one observation-only Position X forward outcome row."""
    _insert("perp_structure_shadow_outcomes", data)


def insert_macro_snapshot(data: dict) -> None:
    from aletheia.storage.models import new_id

    if "id" not in data:
        data["id"] = new_id()
    _insert("macro_snapshots", data)


def latest_macro_snapshot() -> dict | None:
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


# ---------------------------------------------------------------------------
# Round 2 — Step 3: CoinGecko Top-N
# Bulk insert uses canonical BEGIN/COMMIT (mirrors the canonical claim insert).
# DO NOT replace with per-row _insert() — sqlite.py opens connections with
# isolation_level=None (autocommit), so a per-row loop becomes an N-commit storm.
# ---------------------------------------------------------------------------


def insert_coingecko_top_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    from aletheia.storage.models import new_id

    prepared = [dict(row) for row in rows]
    for row in prepared:
        row.setdefault("id", new_id())
    with transaction():
        return _insert_many("coingecko_top_snapshots", prepared)


def latest_coingecko_top(top_n: int = 50) -> list[dict]:
    """Return the most recent batch's top_n rows.

    New rows are pinned by batch_id so mixed provider source times cannot split
    one acquisition batch. Legacy rows fall back to the original observed_at pin.
    """
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


def prune_coingecko_top_snapshots(days: int = 3) -> int:
    """~14k rows/day; default lowered 14d→3d (Phase 3). Freshness doctor probe
    reads MAX(observed_at) which is unaffected by shorter history."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches("coingecko_top_snapshots", "observed_at < ?", (cutoff,))


def prune_orderbook_snapshots(days: int = 7) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches("orderbook_snapshots", "observed_at < ?", (cutoff,))


def prune_perp_structure_snapshots(days: int = 30) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches("perp_structure_snapshots", "observed_at < ?", (cutoff,))


def insert_fred_source_batch(data: dict) -> None:
    from aletheia.data.macro_liquidity import SERIES, VERSION, REVISION, clock, source_batch

    sid = data.get("series_id")
    if (sid not in SERIES or (data.get("unit"), data.get("frequency")) != SERIES[sid][:2]
        or data.get("derivation_version") != VERSION or data.get("revision_policy") != REVISION
        or len(data.get("observations", [])) > 32):
        raise ValueError("invalid_fred_source_batch")
    data = {**data, "acquired_at": clock(data["acquired_at"]).isoformat()}
    checked = source_batch(sid, dict(units="lin", observations=[dict(
        date=p["observation_date"], value="." if p["value"] is None else p["value"],
        realtime_start=p.get("realtime_start"), realtime_end=p.get("realtime_end"),
    ) for p in data["observations"]]), data["acquired_at"], reason=data.get("unavailable_reason"))
    if data != checked:
        raise ValueError("invalid_fred_source_batch")
    payload = json.dumps(data, allow_nan=False)
    if len(payload.encode()) > 16384:
        raise ValueError("oversized_fred_source_batch")
    _insert("fred_source_batches", dict(series_id=sid, acquired_at=data["acquired_at"], payload_json=payload))


def prune_fred_source_batches(days: int = 90) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches("fred_source_batches", "acquired_at < ?", (cutoff,))


def insert_options_volatility_snapshot(data: dict) -> None:
    from aletheia.data.options_volatility import OptionsVolatilitySnapshot

    snapshot = OptionsVolatilitySnapshot.model_validate(data)
    acquired = datetime.fromisoformat(snapshot.acquired_at)
    if acquired.tzinfo is None:
        raise ValueError("acquisition_timezone_required")
    snapshot.acquired_at = acquired.astimezone(timezone.utc).isoformat()
    _insert("options_volatility_snapshots", {
        "symbol": snapshot.symbol, "acquired_at": snapshot.acquired_at,
        "snapshot_json": json.dumps(snapshot.model_dump(), allow_nan=False),
    })


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


def prune_options_volatility_snapshots(days: int = 30) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches("options_volatility_snapshots", "acquired_at < ?", (cutoff,))


def insert_options_structure_snapshot(data: dict) -> None:
    """Persist one OptionsMarketStructureSnapshot row."""
    _insert("options_market_structure_snapshots", data)


def prune_options_structure_snapshots(days: int = 30) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches("options_market_structure_snapshots", "observed_at < ?", (cutoff,))


# ---------------------------------------------------------------------------
# Round 2 — Step 5: DefiLlama on-chain snapshots
# ---------------------------------------------------------------------------


def insert_onchain_snapshot_batch(rows: list[dict]) -> int:
    """Persist one DefiLlama result set with one executemany call."""

    if not rows:
        return 0
    from aletheia.storage.models import new_id

    prepared = [dict(row) for row in rows]
    for row in prepared:
        row.setdefault("id", new_id())
    with transaction():
        return _insert_many("onchain_snapshots", prepared)


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


def prune_onchain_snapshots(days: int = 30) -> int:
    """Prune onchain_snapshots older than `days`. Three rows/cycle × 15min cadence
    ≈ 8640 rows/day — capped at 30d default (≈ 259k rows max).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches("onchain_snapshots", "observed_at < ?", (cutoff,))


def prune_prediction_probability_snapshots(days: int = 90) -> int:
    """#150: prune prediction_probability_snapshots older than `days`. Observation-only
    telemetry (prediction_observer, 30-min cadence, gated on prediction_enabled) that
    previously had no retention path."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches("prediction_probability_snapshots", "ts < ?", (cutoff,))


def prune_liquidation_events(days: int = 7) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches("liquidation_events", "observed_at < ?", (cutoff,))


def _max_open_position_hold_hours() -> int:
    """Return the largest recorded 'open position hold' cap in hours (defensive default 48)."""
    db = get_db()
    try:
        row = db.execute(
            "SELECT MAX(CAST("
            "(julianday(COALESCE(closed_at, datetime('now'))) - julianday(opened_at)) * 24 "
            "AS INTEGER)) AS max_hours FROM positions"
        ).fetchone()
    except Exception:
        return 48
    if not row:
        return 48
    val = row["max_hours"] if hasattr(row, "keys") else row[0]
    try:
        return max(48, int(val or 0))
    except (TypeError, ValueError):
        return 48


def prune_funding_rate_snapshots(days: int = 7) -> int:
    """Prune funding-rate history. Default 7d + hard floor max(7d, 2×max_hold_hours).

    Latest-row reads are always available (heartbeat floor enforced in
    insert_funding_rate_snapshot), so we can safely trim to a small window.
    """
    floor_hours = max(24 * days, 2 * _max_open_position_hold_hours())
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=floor_hours)).isoformat()
    return _delete_by_rowid_batches(
        "funding_rate_snapshots",
        "observed_at < ?",
        (cutoff,),
    )


def prune_open_interest_snapshots(days: int = 7) -> int:
    """Prune open-interest history. Default lowered 30d→7d (Phase 3)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches(
        "open_interest_snapshots",
        "observed_at < ?",
        (cutoff,),
    )


def prune_perp_price_snapshots(days: int = 7) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches('perp_price_snapshots', 'observed_at < ?', (cutoff,))


def prune_universe_feature_snapshots(days: int = 14) -> int:
    """Prune universe_feature_snapshots. Default lowered 30d→14d (Phase 3)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches(
        "universe_feature_snapshots",
        "ts < ?",
        (cutoff,),
    )


def prune_universe_forward_outcomes(
    days: int = 30,
    min_experiment_started_at: str | None | object = _AUTO_EXPERIMENT_FLOOR,
) -> int:
    """Prune selected-member outcomes without deleting open-experiment fallback evidence."""
    protected_refs: tuple[str, ...] = ()
    try:
        lab_authority = _canonical_lab_authority()
    except ExperimentStateReadError:
        return 0
    if lab_authority:
        try:
            protected_refs = _active_observation_claim_refs("universe_forward_outcomes")
        except ExperimentStateReadError:
            return 0
        cutoff = _effective_cutoff(days, None)
    else:
        min_experiment_started_at = _resolve_experiment_floor(min_experiment_started_at)
        cutoff = _effective_cutoff(days, min_experiment_started_at)
    if lab_authority and protected_refs:
        # Keep the exact outcome identity; no global Experiment date floor is
        # consulted after Lab cutover.
        if len(protected_refs) > 900:
            # ponytail: keep this bounded and fail closed; a future bulk
            # cleanup can use a temporary claim table instead of widening SQL.
            return 0
        placeholders = ",".join("?" for _ in protected_refs)
        return _delete_by_rowid_batches(
            "universe_forward_outcomes",
            f"entry_ts < ? AND id NOT IN ({placeholders}) "
            f"AND (outcome_candidate_key IS NULL OR outcome_candidate_key NOT IN ({placeholders}))",
            (cutoff, *protected_refs, *protected_refs),
        )
    return _delete_by_rowid_batches(
        "universe_forward_outcomes",
        "entry_ts < ?",
        (cutoff,),
    )


def prune_macro_snapshots(days: int = 90) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches("macro_snapshots", "observed_at < ?", (cutoff,))


def save_volume_ranking_snapshot(symbols: list, observed_at: str) -> None:
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
    import json

    db = get_db()
    row = db.execute(
        "SELECT symbols_json, observed_at FROM volume_ranking_snapshots ORDER BY observed_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {}
    return {"symbols": json.loads(row[0]), "observed_at": row[1]}


# agent_shell_* telemetry pruning. These tables are liveness/working-memory
# telemetry, NOT causal-chain tables (so they are intentionally NOT in the
# _PRUNABLE causal whitelist — see test_causal_tables_not_in_prunable_whitelist).
# Wired into scheduler._run_cleanup; the prune call-sites existed but the repo
# functions were omitted (incomplete refactor ba80a62), which raised
# AttributeError mid-cleanup and silently skipped the data_pruned audit write.
def prune_agent_shell_heartbeats(days: int = 2) -> int:
    """Prune old agent_shell_heartbeats rows (latest-row-only liveness telemetry)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches("agent_shell_heartbeats", "created_at < ?", (cutoff,))


def prune_agent_shell_events(days: int = 14) -> int:
    """Prune old agent_shell_events rows (telemetry, no causal-chain value)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches("agent_shell_events", "created_at < ?", (cutoff,))


def prune_agent_shell_messages(
    days_context: int = 3,
    days_other: int = 14,
) -> int:
    """Prune agent_shell_messages with type-tiered TTL (Phase 3).

    - message_type='context' rows: dropped after ``days_context`` days (default 3).
    - all other message_type values: dropped after ``days_other`` days (default 14).
    """
    now = datetime.now(timezone.utc)
    ctx_cutoff = (now - timedelta(days=days_context)).isoformat()
    other_cutoff = (now - timedelta(days=days_other)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    deleted = 0
    ctx_cur = execute_write(
        "DELETE FROM agent_shell_messages WHERE message_type = 'context' AND created_at < ?",
        (ctx_cutoff,),
    )
    deleted += ctx_cur.rowcount or 0
    other_cur = execute_write(
        "DELETE FROM agent_shell_messages WHERE (message_type IS NULL OR message_type != 'context') AND created_at < ?",
        (other_cutoff,),
    )
    deleted += other_cur.rowcount or 0
    if not was_in_tx:
        db.commit()
    return deleted


# Artifact type buckets. Anything outside the strategy/proposal buckets falls
# through to the 14-day default.
_ARTIFACT_TYPES_STRATEGY = ("strategy_evaluation",)
_ARTIFACT_TYPES_PROPOSAL = ("proposal", "report", "daily_summary")


def prune_agent_shell_artifacts(
    days_strategy: int = 7,
    days_proposal: int = 30,
    days_other: int = 14,
) -> int:
    """Prune agent_shell_artifacts with type-tiered TTL (Phase 3).

    - strategy_evaluation rows: ``days_strategy`` (default 7).
    - proposal / report / daily_summary rows: ``days_proposal`` (default 30).
    - all other artifact_type values: ``days_other`` (default 14).
    """
    now = datetime.now(timezone.utc)
    strat_cutoff = (now - timedelta(days=days_strategy)).isoformat()
    prop_cutoff = (now - timedelta(days=days_proposal)).isoformat()
    other_cutoff = (now - timedelta(days=days_other)).isoformat()
    strat_placeholders = ",".join("?" * len(_ARTIFACT_TYPES_STRATEGY))
    prop_placeholders = ",".join("?" * len(_ARTIFACT_TYPES_PROPOSAL))
    combined_types = (*_ARTIFACT_TYPES_STRATEGY, *_ARTIFACT_TYPES_PROPOSAL)
    combined_placeholders = ",".join("?" * len(combined_types))
    db = get_db()
    was_in_tx = db.in_transaction
    deleted = 0
    strat_cur = execute_write(
        f"DELETE FROM agent_shell_artifacts WHERE artifact_type IN ({strat_placeholders}) AND created_at < ?",
        (*_ARTIFACT_TYPES_STRATEGY, strat_cutoff),
    )
    deleted += strat_cur.rowcount or 0
    prop_cur = execute_write(
        f"DELETE FROM agent_shell_artifacts WHERE artifact_type IN ({prop_placeholders}) AND created_at < ?",
        (*_ARTIFACT_TYPES_PROPOSAL, prop_cutoff),
    )
    deleted += prop_cur.rowcount or 0
    other_cur = execute_write(
        f"DELETE FROM agent_shell_artifacts "
        f"WHERE (artifact_type IS NULL OR artifact_type NOT IN ({combined_placeholders})) "
        f"AND created_at < ?",
        (*combined_types, other_cutoff),
    )
    deleted += other_cur.rowcount or 0
    if not was_in_tx:
        db.commit()
    return deleted


# ---------------------------------------------------------------------------
# Phase 3 (2026-07-17): new prune functions
# All targets are telemetry / analytics — none are on the audit chain or the
# 8 learning tables. Parent tables cascade-delete their children FIRST in the
# same transaction, following the prune_agent_runs reference pattern.
# ---------------------------------------------------------------------------


def prune_alpha_control_observations(days: int = 60) -> int:
    """Prune alpha-control observation rows older than ``days`` (by entry_ts)."""
    return _delete_old_rows("alpha_control_observations", days)


def prune_controller_recommendations(days: int = 30) -> int:
    """Prune controller_recommendations rows older than ``days`` (by created_at).

    Rows are append-only shadow-log advice — none of the entries are ever
    executed against production, so lossy pruning is safe.
    """
    return _delete_old_rows("controller_recommendations", days)


def prune_market_regimes(days: int = 60) -> int:
    """Prune market_regimes older than ``days``. Latest row protected by heartbeat floor
    in insert_market_regime — the doctor probe reads MAX(detected_at) unaffected."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches("market_regimes", "detected_at < ?", (cutoff,))


def prune_serenity_evidence(days: int = 60) -> int:
    """Prune serenity_evidence older than ``days`` (by created_at)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches("serenity_evidence", "created_at < ?", (cutoff,))


def prune_serenity_run_events(days: int = 60) -> int:
    """Prune serenity_run_events older than ``days`` (by created_at)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _delete_by_rowid_batches("serenity_run_events", "created_at < ?", (cutoff,))


def prune_runtime_sessions(days: int = 90) -> int:
    """Prune finished runtime_sessions older than ``days`` (by started_at).

    Only rows with ``ended_at IS NOT NULL`` are eligible so a still-running boot
    is never removed. Sessions referenced by retained Candidate qualification
    outcomes remain durable because replay still verifies their attestation.
    Other FK-less audit references were fully consumed at write time.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write(
        "DELETE FROM runtime_sessions WHERE ended_at IS NOT NULL AND started_at < ? "
        "AND NOT EXISTS (SELECT 1 FROM filter_candidate_outcomes o "
        "WHERE o.scanner_family='candidate_selection_utility' AND ("
        "o.producer_runtime_session_id=runtime_sessions.session_id OR "
        "o.settlement_runtime_session_id=runtime_sessions.session_id))",
        (cutoff,),
    )
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def scan_orphan_session_ids() -> dict:
    """Read-only scan for orphaned runtime_session_id references across core entity tables.

    An orphan is a non-NULL ``runtime_session_id`` (or ``closed_runtime_session_id``)
    value in a core trading-fact table that has no matching row in ``runtime_sessions``.

    Returns a dict with:
        total_orphan_refs  — total count of orphaned column values found
        by_table           — {table_name: {column: orphan_count}}
        orphan_session_ids — set of unknown session IDs referenced by child rows

    Never modifies anything. Never raises — returns an error key on exception.
    """
    _ENTITY_TABLES_COLS = [
        ("opportunities", "runtime_session_id"),
        ("pi_decisions", "runtime_session_id"),
        ("trade_proposals", "runtime_session_id"),
        ("risk_decisions", "runtime_session_id"),
        ("orders", "runtime_session_id"),
        ("fills", "runtime_session_id"),
        ("positions", "runtime_session_id"),
        ("positions", "closed_runtime_session_id"),
    ]
    try:
        db = get_db()
        known = {r[0] for r in db.execute("SELECT session_id FROM runtime_sessions").fetchall()}
        total = 0
        by_table: dict[str, dict] = {}
        orphan_ids: set[str] = set()
        for table, col in _ENTITY_TABLES_COLS:
            rows = db.execute(f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL").fetchall()
            orphans = [r[0] for r in rows if r[0] not in known]
            if orphans:
                by_table.setdefault(table, {})[col] = len(orphans)
                orphan_ids.update(orphans)
                total += len(orphans)
        return {
            "total_orphan_refs": total,
            "by_table": by_table,
            "orphan_session_ids": list(orphan_ids),
        }
    except Exception as e:
        return {"error": str(e), "total_orphan_refs": 0, "by_table": {}, "orphan_session_ids": []}


def prune_backtest_trades(days: int = 60) -> int:
    """Prune backtest_trades whose parent run created_at is older than ``days``.

    Exposed for symmetry with the _PRUNABLE whitelist; ``prune_backtest_runs``
    cascades through this function first inside a single transaction.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write(
        "DELETE FROM backtest_trades WHERE run_id IN (  SELECT id FROM backtest_runs WHERE created_at < ?)",
        (cutoff,),
    )
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_backtest_runs(days: int = 60) -> int:
    """Prune backtest_runs older than ``days`` + cascade-delete backtest_trades.

    backtest_trades.run_id has no ON DELETE CASCADE; children must be deleted
    first in the same transaction to avoid FK IntegrityError.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    run_ids = [row[0] for row in db.execute("SELECT id FROM backtest_runs WHERE created_at < ?", (cutoff,)).fetchall()]
    if not run_ids:
        return 0
    placeholders = ",".join("?" * len(run_ids))
    with transaction():
        execute_write(
            f"DELETE FROM backtest_trades WHERE run_id IN ({placeholders})",
            run_ids,
        )
        execute_write("DELETE FROM backtest_runs WHERE created_at < ?", (cutoff,))
    return len(run_ids)


def prune_filter_shadow_experiments(days: int = 90) -> int:
    """Prune completed filter_shadow_experiments older than ``days`` + cascade evaluations.

    Excludes rows still in ``status='collecting'`` — a still-open experiment
    keeps its evidence indefinitely. FK on filter_shadow_evaluations.experiment_id
    lacks ON DELETE CASCADE so children are deleted first.
    """
    try:
        if _canonical_lab_authority():
            return 0
    except ExperimentStateReadError:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    exp_ids = [
        row[0]
        for row in db.execute(
            "SELECT id FROM filter_shadow_experiments WHERE status != 'collecting' AND started_at < ?",
            (cutoff,),
        ).fetchall()
    ]
    if not exp_ids:
        return 0
    placeholders = ",".join("?" * len(exp_ids))
    with transaction():
        execute_write(
            f"DELETE FROM filter_shadow_evaluations WHERE experiment_id IN ({placeholders})",
            exp_ids,
        )
        execute_write(
            f"DELETE FROM filter_shadow_experiments WHERE id IN ({placeholders})",
            exp_ids,
        )
    return len(exp_ids)


def prune_universe_snapshots(
    days: int = 30,
    min_experiment_started_at: str | None | object = _AUTO_EXPERIMENT_FLOOR,
) -> int:
    """Prune universe_snapshots older than ``days`` + cascade-delete universe_members.

    FK universe_members.snapshot_id has no ON DELETE CASCADE; children must be
    removed first in the same transaction. universe_forward_outcomes references
    universe_members. Snapshots with fallback outcomes protected by an active
    experiment remain until those outcomes leave their guarded window.
    """
    try:
        lab_authority = _canonical_lab_authority()
    except ExperimentStateReadError:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    protected_snapshots: tuple[str, ...] = ()
    if lab_authority:
        try:
            refs = _active_observation_claim_refs("universe_forward_outcomes")
            _, protected_snapshots = _protected_universe_outcome_ids(get_db(), refs)
        except (ExperimentStateReadError, sqlite3.Error):
            return 0
        if protected_snapshots in {
            ("__claim_set_too_large__",),
            ("__claim_source_unavailable__",),
        }:
            return 0
        outcome_cutoff = _effective_cutoff(days, None)
    else:
        min_experiment_started_at = _resolve_experiment_floor(min_experiment_started_at)
        outcome_cutoff = _effective_cutoff(days, min_experiment_started_at)
    db = get_db()
    protected_clause = ""
    protected_params: tuple[str, ...] = ()
    if protected_snapshots:
        placeholders = ",".join("?" for _ in protected_snapshots)
        protected_clause = f" AND s.id NOT IN ({placeholders})"
        protected_params = protected_snapshots
    snap_ids = [
        row[0]
        for row in db.execute(
            "SELECT s.id FROM universe_snapshots s WHERE s.created_at < ? "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM universe_members m "
            "  JOIN universe_forward_outcomes o ON o.member_id=m.id "
            "  WHERE m.snapshot_id=s.id AND o.entry_ts>=?"
            ")" + protected_clause,
            (cutoff, outcome_cutoff, *protected_params),
        ).fetchall()
    ]
    if not snap_ids:
        return 0
    placeholders = ",".join("?" * len(snap_ids))
    with transaction():
        # universe_forward_outcomes references universe_members. Delete those
        # forward-outcome rows first to avoid FK IntegrityError.
        execute_write(
            f"DELETE FROM universe_forward_outcomes WHERE member_id IN ("
            f"  SELECT id FROM universe_members WHERE snapshot_id IN ({placeholders})"
            f")",
            snap_ids,
        )
        execute_write(
            f"DELETE FROM universe_members WHERE snapshot_id IN ({placeholders})",
            snap_ids,
        )
        execute_write(
            f"DELETE FROM universe_snapshots WHERE id IN ({placeholders})",
            snap_ids,
        )
    return len(snap_ids)


def prune_agent_shell_commands(days: int = 14) -> int:
    """Prune agent_shell_commands older than ``days`` (by created_at)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write("DELETE FROM agent_shell_commands WHERE created_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_agent_shell_tasks(
    days_completed: int = 14,
    days_failed: int = 30,
) -> int:
    """Prune agent_shell_tasks + cascade-delete their events and artifacts.

    - completed rows older than ``days_completed`` (default 14).
    - failed/cancelled rows older than ``days_failed`` (default 30).
    - status IN ('running','pending') OR completed_at IS NULL are EXEMPT.

    agent_shell_events.task_id and agent_shell_artifacts.task_id both have no
    ON DELETE CASCADE — children are wiped first in the same transaction.
    """
    now = datetime.now(timezone.utc)
    completed_cutoff = (now - timedelta(days=days_completed)).isoformat()
    failed_cutoff = (now - timedelta(days=days_failed)).isoformat()
    db = get_db()
    task_ids = [
        row[0]
        for row in db.execute(
            "SELECT id FROM agent_shell_tasks "
            "WHERE completed_at IS NOT NULL AND ("
            "  (status = 'completed' AND completed_at < ?)"
            "  OR (status IN ('failed','cancelled') AND completed_at < ?)"
            ")",
            (completed_cutoff, failed_cutoff),
        ).fetchall()
    ]
    if not task_ids:
        return 0
    placeholders = ",".join("?" * len(task_ids))
    with transaction():
        execute_write(
            f"DELETE FROM agent_shell_events WHERE task_id IN ({placeholders})",
            task_ids,
        )
        execute_write(
            f"DELETE FROM agent_shell_artifacts WHERE task_id IN ({placeholders})",
            task_ids,
        )
        execute_write(
            f"DELETE FROM agent_shell_tasks WHERE id IN ({placeholders})",
            task_ids,
        )
    return len(task_ids)


def prune_agent_shell_sessions(days: int = 14) -> int:
    """Prune agent_shell_sessions + cascade every child telemetry row.

    Parent-window MUST be >= max child window (14d covers commands/messages/
    events/artifacts/tasks at their per-table windows). FK columns on all
    children lack ON DELETE CASCADE, so children are deleted first in one txn.
    Excludes rows named as ``parent_session_id`` of a younger session so the
    lineage-trace summary keeps working.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    session_ids = [
        row[0]
        for row in db.execute(
            "SELECT id FROM agent_shell_sessions "
            "WHERE created_at < ? "
            "AND id NOT IN ("
            "  SELECT DISTINCT parent_session_id FROM agent_shell_sessions "
            "  WHERE parent_session_id IS NOT NULL"
            ")",
            (cutoff,),
        ).fetchall()
    ]
    if not session_ids:
        return 0
    placeholders = ",".join("?" * len(session_ids))
    with transaction():
        execute_write(
            f"DELETE FROM agent_shell_messages WHERE session_id IN ({placeholders})",
            session_ids,
        )
        execute_write(
            f"DELETE FROM agent_shell_events WHERE session_id IN ({placeholders})",
            session_ids,
        )
        execute_write(
            f"DELETE FROM agent_shell_artifacts WHERE session_id IN ({placeholders})",
            session_ids,
        )
        execute_write(
            f"DELETE FROM agent_shell_commands WHERE session_id IN ({placeholders})",
            session_ids,
        )
        # agent_shell_tasks has no session_id column; it references tasks via
        # events/artifacts task_id which we already cleared above. Nothing
        # further to do for tasks here.
        execute_write(
            f"DELETE FROM agent_shell_sessions WHERE id IN ({placeholders})",
            session_ids,
        )
    return len(session_ids)


# Per-timeframe millisecond TTLs. Tuple order matches the config schema
# additions in Agent 7's aletheia/config/schema.py (retention.market_ohlcv_*).
_MARKET_OHLCV_TTL_DAYS_DEFAULT = {
    "1m": 2,
    "5m": 7,
    "15m": 30,
    "1h": 90,
    "4h": 180,
    "1d": 730,
}


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


def expire_stale_serenity_themes() -> int:
    """Soft-expire Serenity themes whose deadline has elapsed and cascade to children.

    Finds active/watching SerenityTheme rows where expires_at is in the past,
    transitions them to 'expired', and cascades the same status to their active
    SerenityOpportunity children so the pending-signal queue treats them as stale
    (#199). Writes are atomic; the transaction is not held across I/O.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with transaction(immediate=True):
        db = get_db()
        expired_themes = db.execute(
            "SELECT id FROM serenity_themes "
            "WHERE expires_at IS NOT NULL "
            "AND datetime(expires_at) <= datetime('now') "
            "AND status NOT IN ('dismissed', 'expired')",
        ).fetchall()
        if not expired_themes:
            return 0
        theme_ids = [row["id"] for row in expired_themes]
        for theme_id in theme_ids:
            update_serenity_theme(theme_id, status="expired", updated_at=now_iso)
            transition_active_serenity_opportunities_for_theme(
                theme_id,
                status="expired",
                updated_at=now_iso,
            )
        return len(theme_ids)


def expire_stale_opportunities(ttl_hours: int = 48) -> int:
    """Apply the global retention ceiling to still-open Opportunities.

    ``valid_until`` is intentionally not a hard transition here. The Ranker's
    family-TTL gate owns the documented soft-decay window and terminalizes a
    candidate only at its hard cap with exact filter evidence. A daily cleanup
    must not race that policy by converting every just-expired generation into
    a hard rejection. Traded/reviewed/queued opportunities are not affected.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    cutoff = (now - timedelta(hours=ttl_hours)).isoformat()
    with transaction(immediate=True):
        rows = (
            get_db()
            .execute(
                "SELECT * FROM opportunities WHERE status='open' AND datetime(created_at)<datetime(?)",
                (cutoff,),
            )
            .fetchall()
        )
        return _expire_opportunity_rows_with_ttl_evidence(
            rows,
            source_status="open",
            evaluated_at=now_iso,
            reason_detail="global open Opportunity retention window elapsed",
            parameters={
                "expiry_basis": "created_at_global_retention",
                "ttl_hours": ttl_hours,
            },
        )


# ── Edge Lab: Shadow Trades ──────────────────────────────────────────────────


def insert_shadow_trade(shadow) -> None:
    data = _to_payload_dict(shadow)
    keyed_result_fields = (
        "exit_price",
        "pnl_pct",
        "gross_return_pct",
        "net_pnl_pct",
        "estimated_fee_pct",
        "estimated_spread_slippage_pct",
        "estimated_funding_pct",
        "min_tradable_edge_pct",
        "settlement_price_source",
        "settlement_lag_sec",
        "cost_model_version",
        "btc_return_pct",
        "resolved_at",
    )
    if str(data.get("outcome_candidate_key") or "").strip() and any(
        data.get(field) is not None for field in keyed_result_fields
    ):
        raise ValueError("keyed shadow results require canonical projection")
    _insert("shadow_trades", data)


# ── Market Regime ─────────────────────────────────────────────────────────────

# Write-side dedup fields for market_regimes. `signals` is compared as a JSON
# text — insert_market_regime always normalises it to json.dumps before we
# reach the guard, so string equality is a semantic equality.
_MARKET_REGIME_DEDUP_FIELDS = (
    "state",
    "confidence",
    "long_bias",
    "short_bias",
    "signals",
)
_MARKET_REGIME_HEARTBEAT_FLOOR_MINUTES = 60


def insert_market_regime(record) -> None:
    """Insert a market_regimes row, skipping identical repeats.

    Guard (Phase 3): identical to insert_funding_rate_snapshot — if the newest
    row has the same state/bias/signals AND ``detected_at`` is younger than
    ``_MARKET_REGIME_HEARTBEAT_FLOOR_MINUTES`` minutes, we skip. Doctor probes
    read MAX(detected_at); we preserve that heartbeat by forcing a write once
    the newest row crosses the floor.
    """
    data = _to_payload_dict(record)
    if isinstance(data.get("signals"), dict):
        data["signals"] = json.dumps(data["signals"])
    db = get_db()
    row = db.execute(
        "SELECT state, confidence, long_bias, short_bias, signals, detected_at "
        "FROM market_regimes ORDER BY detected_at DESC LIMIT 1"
    ).fetchone()
    if row is not None:
        latest = dict(row)
        same = all(data.get(field) == latest.get(field) for field in _MARKET_REGIME_DEDUP_FIELDS)
        if same and latest.get("detected_at"):
            try:
                prev_ts = datetime.fromisoformat(latest["detected_at"])
                if prev_ts.tzinfo is None:
                    prev_ts = prev_ts.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - prev_ts
                if age < timedelta(minutes=_MARKET_REGIME_HEARTBEAT_FLOOR_MINUTES):
                    return
            except (TypeError, ValueError):
                logger.warning("repositories: heartbeat parse failed for market regime", exc_info=True)
    _insert("market_regimes", data)


def get_latest_market_regime(*, now: datetime | None = None) -> dict | None:
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
    rows = _select_all("market_regimes", order="detected_at DESC", limit=limit)
    for row in rows:
        if row.get("signals"):
            try:
                row["signals"] = json.loads(row["signals"])
            except (json.JSONDecodeError, TypeError):
                logger.warning("repositories: signals JSON parse failed in list_market_regimes", exc_info=True)
    return rows


# ── Universe Feature Snapshots (batch read) ───────────────────────────────────


def plan_market_ohlcv_tiering(
    *,
    exchange: str,
    symbol: str,
    market_type: str,
    timeframe: str,
    hot_bars: int = 2_048,
    cold_chunk_bars: int = 4_096,
):
    """Build a format-neutral hot/cold plan from one exact stored series.

    This is read-only.  No rows are removed until a future configured cold
    backend returns durable, read-back-verified receipts.
    """
    from aletheia.storage.market_ohlcv_tiering import (
        OHLCVSeriesIdentity,
        OHLCVTieringPolicy,
        OHLCVTierManager,
    )

    db = get_db()
    rows = db.execute(
        "SELECT ts,open,high,low,close,volume,provider FROM market_ohlcv "
        "WHERE exchange=? AND symbol=? AND market_type=? AND timeframe=? ORDER BY ts",
        (exchange.lower(), symbol, market_type, timeframe),
    ).fetchall()
    manager = OHLCVTierManager(OHLCVTieringPolicy(hot_bars=hot_bars, cold_chunk_bars=cold_chunk_bars))
    return manager.plan(
        OHLCVSeriesIdentity(exchange.lower(), symbol, market_type, timeframe),
        rows,
    )

def latest_perp_structure(symbol: str, source: str | None = None) -> dict | None:
    """Return the newest perp structure row for a symbol."""
    if source is not None:
        rows = _select_all(
            "perp_structure_snapshots",
            where="symbol = ? AND source = ?",
            params=(symbol, source),
            order="as_of_time DESC, observed_at DESC",
            limit=1,
        )
    else:
        rows = _select_all(
            "perp_structure_snapshots",
            where="symbol = ?",
            params=(symbol,),
            order="as_of_time DESC, observed_at DESC",
            limit=1,
        )
    return rows[0] if rows else None

def latest_options_structure(symbol: str) -> dict | None:
    rows = _select_all(
        "options_market_structure_snapshots",
        where="symbol = ?",
        params=(symbol,),
        order="observed_at DESC",
        limit=1,
    )
    return rows[0] if rows else None


def batch_news(
    symbols: list[str],
    since_iso: str,
    limit_per_symbol: int = 3,
) -> dict[str, list[dict]]:
    """Return exact, analyzed, relevant news grouped by requested symbol.

    Historical ``symbols_mentioned`` values are hints only. Every row is
    revalidated against article text so old substring false positives cannot
    re-enter Observer context.
    """
    if not symbols:
        return {}
    from aletheia.data.news_entities import news_matches_symbol

    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = db.execute(
        "SELECT id, title, source, summary, content, published_at, symbols_mentioned, "
        "relevance_score, analyzed_at FROM news_items "
        "WHERE status = 'analyzed' AND relevance_score >= 0.6 "
        "AND datetime(published_at) > datetime(?) "
        "AND datetime(published_at) <= datetime(?) "
        "ORDER BY published_at DESC LIMIT 500",
        (since_iso, now_iso),
    ).fetchall()
    result: dict[str, list] = {s: [] for s in symbols}
    for row in rows:
        item = dict(row)
        for sym in symbols:
            if len(result[sym]) >= limit_per_symbol:
                continue
            if news_matches_symbol(sym, item.get("title"), item.get("summary"), item.get("content")):
                result[sym].append(item)
    return result
