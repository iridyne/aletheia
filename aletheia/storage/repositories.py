"""Thin repository functions for SQLite CRUD."""

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
import logging
import math
import os
import threading
import sqlite3
import time
import uuid
from collections.abc import Mapping, Sequence
from aletheia.storage.sqlite import (
    acquire_write_lock,
    execute_many_write,
    execute_write,
    get_db,
    release_write_lock,
)
from aletheia.storage.models import (
    AuditEvent,
    AgentRunArtifact,
    AgentRun,
    Opportunity,
    TradeProposal,
    RiskDecision,
    Order,
    Fill,
    Position,
    PositionExitPlanVersion,
    PositionProtectionState,
    PositionProtectionTrigger,
    ExitBreakevenShadowAssignment,
    ExitBreakevenShadowOutcome,
    PositionDisappearanceCandidate,
    PositionDisappearanceObservation,
    ReconciliationIntegrityIncident,
    StrategyRecord,
    KnowledgeItem,
    RuntimeSession,
    SerenityTheme,
    SerenityEvidence,
    SerenityOpportunity,
    SerenityRiskGate,
    SerenityReplaySnapshot,
    SerenityPostMortem,
    SerenityClaim,
    OutcomeHookJob,
    PaperAccountEvent,
)
from aletheia.data.symbols import to_canonical
from pydantic import BaseModel

_PROVENANCE_FIELDS = ("runtime_session_id", "producer_commit", "behavior_fingerprint")
_sqlite_logger = logging.getLogger("aletheia.storage.sqlite")
_retention_metrics_lock = threading.Lock()
_retention_metrics: dict[str, dict[str, float | int]] = {}

_PHYSICAL_PROTECTION_MARKETS = {
    "spot": "spot",
    "perp": "perp",
    "usdt_futures": "perp",
}


class PositionProtectionConflict(RuntimeError):
    """A durable protection write conflicts with already-observed authority."""


class ExitBreakevenShadowConflict(RuntimeError):
    """A prospective exit-shadow fact conflicts with retained evidence."""


def _required_protection_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PositionProtectionConflict(f"{field} is required")
    return text


def _required_canonical_uuid(value: object, field: str) -> str:
    text = _required_protection_text(value, field)
    try:
        parsed = uuid.UUID(text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise PositionProtectionConflict(f"{field} must be a UUID") from exc
    if str(parsed) != text:
        raise PositionProtectionConflict(f"{field} must be a canonical lowercase UUID")
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


def _utc_timestamp_ms(value: str, field: str) -> int:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PositionProtectionConflict(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PositionProtectionConflict(f"{field} must be UTC")
    return int(parsed.timestamp() * 1000)


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


def retention_metrics_snapshot() -> dict[str, dict[str, float | int]]:
    with _retention_metrics_lock:
        return {table: dict(values) for table, values in _retention_metrics.items()}


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
        pass
    return data


def _insert(table: str, data: dict) -> None:
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    execute_write(
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
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
    table: str, where: str | None = None, params: tuple = (), order: str = "rowid DESC", limit: int = 100
) -> list[dict]:
    db = get_db()
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    sql += f" ORDER BY {order} LIMIT {limit}"
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _select_one(table: str, id_val: str) -> dict | None:
    db = get_db()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (id_val,)).fetchone()
    return dict(row) if row else None


def _update(table: str, id_val: str, data: dict) -> None:
    sets = ", ".join(f"{k} = ?" for k in data.keys())
    execute_write(
        f"UPDATE {table} SET {sets} WHERE id = ?",
        [*data.values(), id_val],
    )


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
        log_args = (wait_ms, held_ms, immediate, outcome)
        if wait_ms >= 100.0 or held_ms >= 500.0:
            _sqlite_logger.warning(
                "sqlite_write_transaction_slow wait_ms=%.1f held_ms=%.1f immediate=%s outcome=%s",
                *log_args,
            )
        elif verbose:
            _sqlite_logger.info(
                "sqlite_write_transaction wait_ms=%.1f held_ms=%.1f immediate=%s outcome=%s",
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


def list_runtime_sessions(runtime_id: str | None = None, limit: int = 50) -> list[dict]:
    if runtime_id:
        return _select_all(
            "runtime_sessions", where="runtime_id = ?", params=(runtime_id,), order="started_at DESC", limit=limit
        )
    return _select_all("runtime_sessions", order="started_at DESC", limit=limit)


# --- Audit Events ---


def insert_audit_event(event: AuditEvent) -> None:
    _insert("audit_events", event.model_dump())


def insert_audit_event_once(event: AuditEvent) -> bool:
    """Insert a caller-identified audit fact once; return False on identity retry."""
    data = event.model_dump()
    columns = ", ".join(data)
    placeholders = ", ".join("?" for _ in data)
    cursor = execute_write(
        f"INSERT INTO audit_events ({columns}) VALUES ({placeholders}) "
        "ON CONFLICT(id) DO NOTHING",
        tuple(data.values()),
    )
    return cursor.rowcount == 1


def list_audit_events(limit: int = 50) -> list[dict]:
    return _select_all("audit_events", limit=limit)


# --- Agent Runs ---


def insert_agent_run(run: AgentRun) -> None:
    _insert("agent_runs", run.model_dump())


def insert_agent_run_artifacts(artifacts: list[AgentRunArtifact]) -> None:
    """Persist current-run artifact references without rewriting legacy outputs."""
    if not artifacts:
        return
    execute_many_write(
        "INSERT OR IGNORE INTO agent_run_artifacts "
        "(run_id, artifact_type, artifact_id) VALUES (?, ?, ?)",
        [
            (artifact.run_id, artifact.artifact_type, artifact.artifact_id)
            for artifact in artifacts
        ],
    )


def update_agent_run(run_id: str, **kwargs) -> None:
    _update("agent_runs", run_id, kwargs)


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


def get_agent_run(run_id: str) -> dict | None:
    return _select_one("agent_runs", run_id)


# --- Opportunities ---


def insert_opportunity(opp: Opportunity) -> None:
    _insert("opportunities", _stamp_provenance(opp.model_dump()))


def list_opportunities(limit: int = 50) -> list[dict]:
    return _select_all("opportunities", limit=limit)


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


_OPEN_TRADE_ASSET_PREDICATE = "COALESCE(NULLIF(asset_class, ''), 'crypto') IN ('crypto', 'equity')"
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


def list_open_trade_opportunities(limit: int = 50) -> list[dict]:
    """Return one bounded, starvation-free open trade workset.

    Legacy rows with a NULL/blank asset class predate the discriminator and are
    crypto opportunities by construction. Prediction observations remain
    research context and never enter this trade workset. Half of each normal
    page is reserved for never-evaluated rows and half for the oldest retryable
    rows; either side may consume unused capacity from the other. This prevents
    sustained new arrivals from permanently hiding already-ranked open work.
    """
    page_size = max(0, int(limit))
    if page_size == 0:
        return []
    db = get_db()
    unranked_quota = (page_size + 1) // 2
    retry_quota = page_size // 2

    def fetch(sql: str, count: int, *, offset: int = 0) -> list[dict]:
        if count <= 0:
            return []
        return [dict(row) for row in db.execute(sql, (int(count), int(offset))).fetchall()]

    unranked = fetch(_OPEN_TRADE_UNRANKED_SQL, unranked_quota)
    retryable = fetch(_OPEN_TRADE_RETRY_SQL, retry_quota)
    remaining = page_size - len(unranked) - len(retryable)
    if remaining > 0 and len(unranked) < unranked_quota:
        retryable.extend(
            fetch(
                _OPEN_TRADE_RETRY_SQL,
                remaining,
                offset=len(retryable),
            )
        )
        remaining = page_size - len(unranked) - len(retryable)
    if remaining > 0 and len(retryable) < retry_quota:
        unranked.extend(
            fetch(
                _OPEN_TRADE_UNRANKED_SQL,
                remaining,
                offset=len(unranked),
            )
        )
        remaining = page_size - len(unranked) - len(retryable)
    # When both initial quotas were short, consume any final capacity without
    # duplicating the rows already returned from either indexed stream.
    if remaining > 0:
        retryable.extend(
            fetch(
                _OPEN_TRADE_RETRY_SQL,
                remaining,
                offset=len(retryable),
            )
        )
        remaining = page_size - len(unranked) - len(retryable)
    if remaining > 0:
        unranked.extend(
            fetch(
                _OPEN_TRADE_UNRANKED_SQL,
                remaining,
                offset=len(unranked),
            )
        )
    return unranked + retryable


def mark_opportunities_ranked(opportunity_ids: list[str], ranked_at: str) -> int:
    """Advance the rotation cursor for one proven workset, exactly once.

    Callers place this update in the same explicit transaction as the immutable
    current-run evidence batch. A missing row is an integrity failure: the batch
    must roll back instead of claiming a workset that was not actually marked.
    """
    ids = list(dict.fromkeys(str(item) for item in opportunity_ids if item))
    if not ids:
        return 0
    db = get_db()
    was_in_tx = db.in_transaction
    placeholders = ",".join("?" for _ in ids)
    cur = execute_write(
        f"UPDATE opportunities SET last_ranked_at=? WHERE id IN ({placeholders})",
        (ranked_at, *ids),
    )
    if cur.rowcount != len(ids):
        raise RuntimeError(f"ranked_opportunity_set_changed:{cur.rowcount}/{len(ids)}")
    if not was_in_tx:
        db.commit()
    return cur.rowcount


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


def update_opportunity(oid: str, **kwargs) -> None:
    _update("opportunities", oid, kwargs)


def update_open_opportunity(oid: str, **kwargs) -> bool:
    """CAS non-lifecycle enrichment onto a still-open Opportunity only."""
    if not kwargs:
        return False
    if "status" in kwargs:
        raise ValueError("Opportunity lifecycle status requires a transition helper")
    sets = ", ".join(f"{key} = ?" for key in kwargs)
    cursor = execute_write(
        f"UPDATE opportunities SET {sets} WHERE id = ? AND status = 'open'",
        [*kwargs.values(), oid],
    )
    return cursor.rowcount == 1


def transition_open_opportunity_to_queued(oid: str) -> bool:
    """Claim one still-open Opportunity for queue insertion without state regression."""
    cur = execute_write(
        "UPDATE opportunities SET status='queued' WHERE id=? AND status='open'",
        (oid,),
    )
    return cur.rowcount == 1


def transition_opportunity_to_reviewed(oid: str) -> bool:
    """Advance a produced/evaluated Opportunity to its terminal reviewed state."""
    cur = execute_write(
        "UPDATE opportunities SET status='reviewed' WHERE id=? AND status IN ('open', 'queued')",
        (oid,),
    )
    return cur.rowcount == 1


def list_opportunities_by_asset_class(asset_class: str, limit: int = 50) -> list[dict]:
    return _select_all("opportunities", where="asset_class = ?", params=(asset_class,), limit=limit)


def find_open_opportunity_by_symbol_snapshot(symbol: str, snapshot_id: str, market: str | None = None) -> dict | None:
    """Find an existing open opportunity for the same symbol in the same universe snapshot.

    When ``market`` is given, also require ``opportunities.market = ?`` so a
    same-symbol opp from a different market (e.g., spot vs perp) does not
    annotate / block the other.  A missing/None market keeps the prior
    behavior (any open opp for the symbol+snapshot is a hit), preserving
    backward compatibility for callers that predate the market dimension.
    """
    db = get_db()
    sql = "SELECT * FROM opportunities WHERE symbol = ? AND universe_snapshot_id = ? AND status = 'open'"
    params: list = [symbol, snapshot_id]
    if market:
        sql += " AND market = ?"
        params.append(market)
    sql += " LIMIT 1"
    row = db.execute(sql, params).fetchone()
    return dict(row) if row else None


def find_live_opportunity_by_symbol_direction(
    symbol: str,
    direction: str,
    market: str | None = None,
) -> dict | None:
    """Return the most-recent live (open or queued, non-expired) opportunity for
    a given symbol+direction+market combination.

    Used by the signal emitter to suppress duplicate emissions when a live candidate
    for the same symbol+direction already occupies the evaluation queue (#326).
    ``valid_until IS NULL`` rows are treated as never-expiring and always considered
    live (paranoid fail-safe: better to suppress one extra signal than to flood the
    queue).
    """
    db = get_db()
    sql = (
        "SELECT * FROM opportunities "
        "WHERE symbol = ? AND direction_bias = ? "
        "AND status IN ('open','queued') "
        "AND (valid_until IS NULL OR datetime(valid_until) > datetime('now'))"
    )
    params: list = [symbol, direction]
    if market:
        sql += " AND market = ?"
        params.append(market)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT 1"
    row = db.execute(sql, params).fetchone()
    return dict(row) if row else None


def find_opportunity_by_symbol_snapshot(
    symbol: str,
    snapshot_id: str,
    market: str | None = None,
) -> dict | None:
    """Find the first opportunity ever created for one snapshot candidate.

    Unlike :func:`find_open_opportunity_by_symbol_snapshot`, this lifecycle
    identity lookup includes terminal rows.  A delayed producer must not create
    a second opportunity merely because the first one has already been reviewed.
    The earliest row is authoritative for legacy databases that already contain
    duplicates.
    """
    db = get_db()
    sql = "SELECT * FROM opportunities WHERE symbol = ? AND universe_snapshot_id = ?"
    params: list = [symbol, snapshot_id]
    if market:
        sql += " AND market = ?"
        params.append(market)
    sql += " ORDER BY created_at ASC, rowid ASC LIMIT 1"
    row = db.execute(sql, params).fetchone()
    return dict(row) if row else None


def insert_opportunity_once_by_snapshot(
    opportunity: Opportunity,
) -> tuple[dict, bool]:
    """Atomically resolve one lifecycle owner for a snapshot instrument.

    The producer-side pre-check remains a useful fast path, but only an IMMEDIATE
    writer transaction can make the final check+insert indivisible across observer
    and universe worker threads. Historical duplicates remain readable; their
    earliest row keeps ownership without destructive cleanup.
    """
    snapshot_id = str(opportunity.universe_snapshot_id or "").strip()
    symbol = str(opportunity.symbol or "").strip()
    market = str(opportunity.market or "").strip()
    if not snapshot_id or not symbol or not market:
        raise ValueError("snapshot Opportunity identity requires symbol, market, snapshot")

    with transaction(immediate=True):
        existing = find_opportunity_by_symbol_snapshot(symbol, snapshot_id, market=market)
        if existing is not None:
            return existing, False
        insert_opportunity(opportunity)
        persisted = _select_one("opportunities", opportunity.id)
        if persisted is None:
            raise RuntimeError("snapshot Opportunity insert did not persist")
        return persisted, True


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


def record_operator_evaluation_event(event) -> dict:
    """Persist one deduplicated non-trading Operator observation.

    This table deliberately sits outside ``pi_decisions``: a heartbeat with no
    queue carrier is not an entry decision and must not consume an Opportunity.
    ``dedup_key`` is supplied by the caller's bounded observation window.
    """
    data = _to_payload_dict(event)
    db = get_db()
    was_in_tx = db.in_transaction
    columns = ", ".join(data)
    placeholders = ", ".join("?" for _ in data)
    execute_write(
        f"INSERT OR IGNORE INTO operator_evaluation_events ({columns}) VALUES ({placeholders})",
        tuple(data.values()),
    )
    row = db.execute(
        "SELECT * FROM operator_evaluation_events WHERE dedup_key=?",
        (data["dedup_key"],),
    ).fetchone()
    if not was_in_tx:
        db.commit()
    if row is None:
        raise RuntimeError("operator evaluation observation did not persist")
    return dict(row)


def update_pi_decision(decision_id: str, **kwargs) -> None:
    """Update the durable pre-submission claim after its proposal attempt settles."""
    _update("pi_decisions", decision_id, kwargs)


def latest_pi_decision(symbol: str, max_age_minutes: int = 30) -> dict | None:
    """Get the most recent PI decision for a symbol within max_age_minutes."""
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
    db = get_db()
    row = db.execute(
        "SELECT * FROM pi_decisions WHERE symbol = ? AND created_at >= ?"
        " ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (symbol, cutoff),
    ).fetchone()
    return dict(row) if row else None


def list_pi_decisions(limit: int = 20) -> list[dict]:
    return _select_all("pi_decisions", order="created_at DESC, rowid DESC", limit=limit)


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


def find_final_pi_decision_for_opportunity(
    opportunity_id: str,
    signal_source: str = "market_observer",
    *,
    action: str | None = None,
) -> dict | None:
    """Return the first authoritative result for one source identity.

    ``action`` narrows idempotency to a position-management intent.  An entry
    decision and a later close/reduce legitimately share one Opportunity, so
    the historical entry must not swallow the management claim. Without an
    explicit action, only terminal entry actions in the current carrier
    generation qualify; an older eviction or final cannot consume a requeue.
    """
    if not opportunity_id:
        return None
    if action:
        action_clause = "AND final.action=?"
        generation_clause = ""
        params: tuple[str, ...] = (
            opportunity_id,
            signal_source or "market_observer",
            action,
        )
    else:
        action_clause = (
            "AND final.action NOT IN ("
            "'signal_pending', 'signal_queue_evicted', 'close', 'reduce')"
        )
        generation_clause = (
            "AND NOT EXISTS ("
            " SELECT 1 FROM pi_decisions pending "
            " WHERE pending.opportunity_id=final.opportunity_id "
            " AND COALESCE(pending.signal_source, 'market_observer')="
            "     COALESCE(final.signal_source, 'market_observer') "
            " AND pending.action='signal_pending' "
            " AND (julianday(pending.created_at)>julianday(final.created_at) "
            "      OR (julianday(pending.created_at)=julianday(final.created_at) "
            "          AND pending.rowid>final.rowid))"
            ") "
        )
        params = (
            opportunity_id,
            signal_source or "market_observer",
        )
    row = (
        get_db()
        .execute(
            "SELECT final.* FROM pi_decisions final WHERE final.opportunity_id=? "
            "AND COALESCE(final.signal_source, 'market_observer')=? "
            f"{action_clause} "
            f"{generation_clause}"
            "ORDER BY julianday(final.created_at) ASC, final.rowid ASC LIMIT 1",
            params,
        )
        .fetchone()
    )
    return dict(row) if row else None


def classify_final_pi_decision_for_opportunity(
    opportunity_id: str,
    signal_source: str,
    *,
    symbol: str,
    direction: str,
    market_type: str,
    action: str | None = None,
) -> dict:
    """Classify current-generation final rows without letting ambiguity consume it."""
    if not opportunity_id:
        return {"status": "none", "decision": None}
    if action:
        action_clause = "AND final.action=?"
        generation_clause = ""
        params = (opportunity_id, signal_source or "market_observer", action)
    else:
        action_clause = (
            "AND final.action NOT IN ("
            "'signal_pending', 'signal_queue_evicted', 'close', 'reduce')"
        )
        generation_clause = (
            "AND NOT EXISTS ("
            " SELECT 1 FROM pi_decisions pending "
            " WHERE pending.opportunity_id=final.opportunity_id "
            " AND COALESCE(pending.signal_source, 'market_observer')="
            "     COALESCE(final.signal_source, 'market_observer') "
            " AND pending.action='signal_pending' "
            " AND (julianday(pending.created_at)>julianday(final.created_at) "
            "      OR (julianday(pending.created_at)=julianday(final.created_at) "
            "          AND pending.rowid>final.rowid))"
            ") "
        )
        params = (
            opportunity_id,
            signal_source or "market_observer",
        )
    rows = (
        get_db()
        .execute(
            "SELECT final.* FROM pi_decisions final WHERE final.opportunity_id=? "
            "AND COALESCE(final.signal_source, 'market_observer')=? "
            f"{action_clause} "
            f"{generation_clause}"
            "ORDER BY julianday(final.created_at) ASC, final.rowid ASC",
            params,
        )
        .fetchall()
    )
    if not rows:
        return {"status": "none", "decision": None}
    if len(rows) != 1:
        return {
            "status": "integrity_unavailable",
            "reason": "ambiguous_final_decisions",
            "decision": None,
        }
    decision = dict(rows[0])
    if any(
        decision.get(field) != expected
        for field, expected in (
            ("symbol", symbol),
            ("direction", direction),
            ("market_type", market_type),
        )
    ):
        return {
            "status": "integrity_unavailable",
            "reason": "conflicting_final_identity",
            "decision": None,
        }
    return {"status": "exact", "decision": decision}


def find_pending_pi_decision_for_opportunity(
    opportunity_id: str,
    signal_source: str = "market_observer",
) -> dict | None:
    """Return the durable queue carrier for one exact source opportunity.

    This intentionally does not hide the row after a final ``submitting`` claim:
    crash recovery must still prove that the original pending carrier existed.
    Callers remain responsible for applying the market-observer TTL.
    """
    rows = list_pending_pi_decisions_for_opportunity(opportunity_id, signal_source)
    return rows[0] if rows else None


def list_pending_pi_decisions_for_opportunity(
    opportunity_id: str,
    signal_source: str = "market_observer",
) -> list[dict]:
    """Return pending carriers in the current source generation, oldest first.

    A queue eviction closes only carriers ordered before it. A later
    ``signal_pending`` starts a new generation, while duplicate carriers inside
    that generation remain visible so execution can fail closed on ambiguity.
    Entry finals stay visible to integrity/recovery checks; their validity is
    classified separately and must never erase a malformed or ambiguous claim.
    """
    if not opportunity_id:
        return []
    rows = (
        get_db()
        .execute(
            "SELECT pending.* FROM pi_decisions pending "
            "WHERE pending.opportunity_id=? "
            "AND pending.action='signal_pending' "
            "AND COALESCE(pending.signal_source, 'market_observer')=? "
            "AND NOT EXISTS ("
            " SELECT 1 FROM pi_decisions boundary "
            " WHERE boundary.opportunity_id=pending.opportunity_id "
            " AND COALESCE(boundary.signal_source, 'market_observer')="
            "     COALESCE(pending.signal_source, 'market_observer') "
            " AND boundary.action='signal_queue_evicted' "
            " AND (julianday(boundary.created_at)>julianday(pending.created_at) "
            "      OR (julianday(boundary.created_at)=julianday(pending.created_at) "
            "          AND boundary.rowid>pending.rowid))"
            ") ORDER BY julianday(pending.created_at) ASC, pending.rowid ASC",
            (opportunity_id, signal_source or "market_observer"),
        )
        .fetchall()
    )
    return [dict(row) for row in rows]


def count_unresolved_signal_pending(max_age_minutes: int = 30) -> int:
    """Count queued market candidates that consume an Operator admission slot.

    ``Opportunity.valid_until`` is authoritative for current rows. The fixed
    queue age remains only as a compatibility fallback for historical rows that
    did not persist a parseable source deadline.

    A candidate whose most recent Operator observation is an active defer
    (``event_kind LIKE 'operator_decision_deferred_%'`` and
    ``defer_until > now()``) does NOT consume an admission slot — Operator has
    already judged it and it is awaiting its next re-evaluation window. Only
    candidates still owed a fresh Operator judgment count here.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
    row = (
        get_db()
        .execute(
            "SELECT COUNT(DISTINCT pending.opportunity_id) "
            "FROM pi_decisions pending "
            "JOIN opportunities source ON source.id=pending.opportunity_id "
            "WHERE pending.action='signal_pending' "
            "AND COALESCE(pending.signal_source, 'market_observer')='market_observer' "
            "AND source.status='queued' "
            "AND ((datetime(source.valid_until) IS NOT NULL "
            "      AND datetime(source.valid_until)>datetime('now')) "
            " OR (datetime(source.valid_until) IS NULL "
            "      AND datetime(pending.created_at)>=datetime(?))) "
            "AND pending.rowid=("
            " SELECT newer.rowid FROM pi_decisions newer "
            " WHERE newer.opportunity_id=pending.opportunity_id "
            " AND COALESCE(newer.signal_source, 'market_observer')="
            "     COALESCE(pending.signal_source, 'market_observer') "
            " AND newer.action='signal_pending' "
            " ORDER BY julianday(newer.created_at) DESC, newer.rowid DESC LIMIT 1"
            ") "
            "AND NOT EXISTS ("
            " SELECT 1 FROM pi_decisions final "
            " WHERE final.opportunity_id=pending.opportunity_id "
            " AND COALESCE(final.signal_source, 'market_observer')="
            "     COALESCE(pending.signal_source, 'market_observer') "
            " AND final.action!='signal_pending' "
            " AND COALESCE(final.proposal_status, '')!='submitting' "
            " AND (julianday(final.created_at)>julianday(pending.created_at) "
            "      OR (julianday(final.created_at)=julianday(pending.created_at) "
            "          AND final.rowid>pending.rowid))"
            ") "
            "AND NOT EXISTS ("
            " SELECT 1 FROM operator_evaluation_events e "
            " WHERE json_extract(e.queue_snapshot_json,'$.opportunity_id')"
            "       = pending.opportunity_id "
            " AND e.event_kind LIKE 'operator_decision_deferred_%' "
            " AND datetime(json_extract(e.queue_snapshot_json,'$.defer_until'))"
            "     > datetime('now')"
            ")",
            (cutoff,),
        )
        .fetchone()
    )
    return int(row[0] or 0)


def list_pending_signal_queue(max_age_minutes: int = 30) -> list[dict]:
    """Return current queue occupants with their conviction score for priority comparison.

    Each dict contains opportunity_id, symbol, conviction.  Same eligibility
    criteria as count_unresolved_signal_pending — only rows that consume an
    admission slot are returned.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
    rows = (
        get_db()
        .execute(
            "SELECT pending.opportunity_id, pending.symbol, pending.conviction "
            "FROM pi_decisions pending "
            "JOIN opportunities source ON source.id=pending.opportunity_id "
            "WHERE pending.action='signal_pending' "
            "AND COALESCE(pending.signal_source, 'market_observer')='market_observer' "
            "AND source.status='queued' "
            "AND ((datetime(source.valid_until) IS NOT NULL "
            "      AND datetime(source.valid_until)>datetime('now')) "
            " OR (datetime(source.valid_until) IS NULL "
            "      AND datetime(pending.created_at)>=datetime(?))) "
            "AND pending.rowid=("
            " SELECT newer.rowid FROM pi_decisions newer "
            " WHERE newer.opportunity_id=pending.opportunity_id "
            " AND COALESCE(newer.signal_source, 'market_observer')="
            "     COALESCE(pending.signal_source, 'market_observer') "
            " AND newer.action='signal_pending' "
            " ORDER BY julianday(newer.created_at) DESC, newer.rowid DESC LIMIT 1"
            ") "
            "AND NOT EXISTS ("
            " SELECT 1 FROM pi_decisions final "
            " WHERE final.opportunity_id=pending.opportunity_id "
            " AND COALESCE(final.signal_source, 'market_observer')="
            "     COALESCE(pending.signal_source, 'market_observer') "
            " AND final.action!='signal_pending' "
            " AND COALESCE(final.proposal_status, '')!='submitting' "
            " AND (julianday(final.created_at)>julianday(pending.created_at) "
            "      OR (julianday(final.created_at)=julianday(pending.created_at) "
            "          AND final.rowid>pending.rowid))"
            ") "
            "AND NOT EXISTS ("
            " SELECT 1 FROM operator_evaluation_events e "
            " WHERE json_extract(e.queue_snapshot_json,'$.opportunity_id')"
            "       = pending.opportunity_id "
            " AND e.event_kind LIKE 'operator_decision_deferred_%' "
            " AND datetime(json_extract(e.queue_snapshot_json,'$.defer_until'))"
            "     > datetime('now')"
            ") "
            "GROUP BY pending.opportunity_id",
            (cutoff,),
        )
        .fetchall()
    )
    return [
        {
            "opportunity_id": str(row["opportunity_id"]),
            "symbol": str(row["symbol"] or ""),
            "conviction": int(row["conviction"] or 0),
        }
        for row in rows
    ]


def transition_queued_opportunity_to_open(oid: str) -> bool:
    """Return a queued Opportunity to open so it can be re-admitted in a later run.

    Used by priority-replacement eviction: a lower-scored incumbent is released
    back to open status and its signal_pending carrier becomes superseded by the
    subsequent eviction pi_decision written by the caller.
    """
    cur = execute_write(
        "UPDATE opportunities SET status='open' WHERE id=? AND status='queued'",
        (oid,),
    )
    return cur.rowcount == 1


def active_defer_opportunity_ids(now_iso: str | None = None) -> set[str]:
    """opportunity_id set whose most recent Operator observation is an active defer.

    An "active defer" means: the ``operator_evaluation_events`` row has
    ``event_kind LIKE 'operator_decision_deferred_%'`` and its
    ``queue_snapshot_json.defer_until`` is still in the future.

    Used by admission accounting (``count_unresolved_signal_pending``) and
    display-layer ordering (``_build_pending_signal_queue``) so both share
    one source of truth for "Operator has already judged, awaiting reopen".
    """
    if now_iso is None:
        now_iso = datetime.now(timezone.utc).isoformat()
    rows = get_db().execute(
        "SELECT json_extract(queue_snapshot_json,'$.opportunity_id') AS opportunity_id "
        "FROM operator_evaluation_events "
        "WHERE event_kind LIKE 'operator_decision_deferred_%' "
        "AND json_extract(queue_snapshot_json,'$.defer_until') > ? "
        "GROUP BY json_extract(queue_snapshot_json,'$.opportunity_id')",
        (now_iso,),
    ).fetchall()
    return {str(row["opportunity_id"]) for row in rows if row["opportunity_id"]}


def next_pending_signal_defer_deadline(now_iso: str | None = None) -> str | None:
    """Return the earliest durable Operator defer that is still owed a review.

    The deadline remains visible after it passes, until the exact market-observer
    carrier receives either a newer Operator observation or a terminal PI
    decision.  This lets the daemon recover the promise after restart and avoids
    heartbeat phase alignment becoming part of trading behavior.
    """
    if now_iso is None:
        now_iso = datetime.now(timezone.utc).isoformat()
    row = get_db().execute(
        "SELECT json_extract(defer.queue_snapshot_json,'$.defer_until') AS deadline "
        "FROM operator_evaluation_events defer "
        "JOIN opportunities source ON source.id="
        " json_extract(defer.queue_snapshot_json,'$.opportunity_id') "
        "WHERE defer.event_kind LIKE 'operator_decision_deferred_%' "
        "AND datetime(json_extract(defer.queue_snapshot_json,'$.defer_until')) IS NOT NULL "
        "AND source.status IN ('queued','reviewed') "
        "AND datetime(source.valid_until)>datetime(?) "
        "AND NOT EXISTS ("
        " SELECT 1 FROM operator_evaluation_events newer "
        " WHERE json_extract(newer.queue_snapshot_json,'$.opportunity_id')="
        "       json_extract(defer.queue_snapshot_json,'$.opportunity_id') "
        " AND (julianday(newer.created_at)>julianday(defer.created_at) "
        "      OR (julianday(newer.created_at)=julianday(defer.created_at) "
        "          AND newer.rowid>defer.rowid))"
        ") "
        "AND EXISTS ("
        " SELECT 1 FROM pi_decisions pending "
        " WHERE pending.opportunity_id=source.id "
        " AND pending.action='signal_pending' "
        " AND COALESCE(pending.signal_source,'market_observer')='market_observer' "
        " AND NOT EXISTS ("
        "  SELECT 1 FROM pi_decisions final "
        "  WHERE final.opportunity_id=pending.opportunity_id "
        "  AND COALESCE(final.signal_source,'market_observer')="
        "      COALESCE(pending.signal_source,'market_observer') "
        "  AND final.symbol IS pending.symbol "
        "  AND final.direction IS pending.direction "
        "  AND final.market_type IS pending.market_type "
        "  AND final.action NOT IN ('signal_pending','close','reduce') "
        "  AND COALESCE(final.proposal_status,'')!='submitting' "
        "  AND (julianday(final.created_at)>julianday(pending.created_at) "
        "       OR (julianday(final.created_at)=julianday(pending.created_at) "
        "           AND final.rowid>pending.rowid))"
        " )"
        ") "
        "ORDER BY datetime(deadline) ASC, defer.rowid ASC LIMIT 1",
        (now_iso,),
    ).fetchone()
    return str(row["deadline"]) if row is not None and row["deadline"] else None


def latest_direct_skip_defer_for_setup(
    symbol: str,
    direction: str,
    market_type: str,
    *,
    window_minutes: int = 120,
) -> dict | None:
    """Most recent ``direct_skip_not_confirmed`` defer for a symbol+direction setup.

    Returns the parsed ``queue_snapshot_json`` (carrying ``material_fingerprint``)
    of the latest such defer within ``window_minutes``, or ``None``. Only defer
    events recorded after #321 carry the symbol/direction/market_type + material
    fingerprint keys, so older rows simply never match (callers fail open = emit).

    Used by emit-source suppression (#321): if the latest direct-skip judgment for
    this setup shares the current material fingerprint, universe refresh must not
    mint yet another fresh-opportunity_id carrier that re-burns an LLM re-eval.
    The caller compares fingerprints — this only bounds the lookback by time so a
    stale skip cannot suppress a setup forever.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=max(int(window_minutes), 1))
    ).isoformat()
    row = get_db().execute(
        "SELECT queue_snapshot_json FROM operator_evaluation_events "
        "WHERE event_kind LIKE 'operator_decision_deferred_%' "
        "AND json_extract(queue_snapshot_json,'$.defer_reason') LIKE 'direct_skip_not_confirmed%' "
        "AND json_extract(queue_snapshot_json,'$.symbol') = ? "
        "AND json_extract(queue_snapshot_json,'$.direction') = ? "
        "AND json_extract(queue_snapshot_json,'$.market_type') = ? "
        "AND created_at >= ? "
        "ORDER BY created_at DESC LIMIT 1",
        (symbol, direction, market_type, cutoff),
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["queue_snapshot_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def latest_skip_validation_for_setup(
    symbol: str,
    direction: str,
    market_type: str,
    *,
    window_minutes: int = 30,
) -> dict | None:
    """Most recent ``operator_skip_validated`` cache row for a symbol+direction setup.

    Returns the parsed ``queue_snapshot_json`` (carrying ``material_fingerprint``
    plus the full validated adverse judgment) within ``window_minutes``, else None.

    Used by reassess reuse (#321): when the model again judges the same setup a
    skip at an unchanged material fingerprint, this lets the caller reuse the prior
    counterfactual confirmation instead of a second LLM call.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=max(int(window_minutes), 1))
    ).isoformat()
    row = get_db().execute(
        "SELECT queue_snapshot_json FROM operator_evaluation_events "
        "WHERE event_kind = 'operator_skip_validated' "
        "AND json_extract(queue_snapshot_json,'$.symbol') = ? "
        "AND json_extract(queue_snapshot_json,'$.direction') = ? "
        "AND json_extract(queue_snapshot_json,'$.market_type') = ? "
        "AND created_at >= ? "
        "ORDER BY created_at DESC LIMIT 1",
        (symbol, direction, market_type, cutoff),
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["queue_snapshot_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def extend_opportunity_valid_until_forward_only(
    opportunity_id: str, new_valid_until_iso: str
) -> int:
    """Slide an Opportunity's valid_until forward if the new deadline is later.

    Idempotent-forward: never shortens an existing later deadline. Only touches
    ``status IN ('open','queued')`` — expired/reviewed rows stay terminal.

    Returns the number of rows updated (0 if the new deadline is not later).
    """
    result = execute_write(
        "UPDATE opportunities SET valid_until = ? "
        "WHERE id = ? AND status IN ('open','queued') "
        "AND (valid_until IS NULL OR valid_until < ?)",
        (new_valid_until_iso, opportunity_id, new_valid_until_iso),
    )
    return int(result.rowcount or 0)


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


def expire_queued_opportunity_with_ttl_evidence(
    opportunity_id: str,
    *,
    evaluated_at: str | None = None,
    reason_detail: str,
    parameters: dict | None = None,
) -> bool:
    """CAS one queued Opportunity to expired with its durable TTL fact.

    Operator preflight and the final Proposal claim both use this primitive so
    terminal state cannot be written without the corresponding rejection fact.
    The inner helper owns a SAVEPOINT; an evidence failure therefore restores
    the queued row even when an outer transaction catches the exception.
    """
    now_iso = evaluated_at or datetime.now(timezone.utc).isoformat()
    with transaction(immediate=True):
        row = (
            get_db()
            .execute(
                "SELECT * FROM opportunities WHERE id=? AND status='queued'",
                (opportunity_id,),
            )
            .fetchone()
        )
        if row is None:
            return False
        return bool(
            _expire_opportunity_rows_with_ttl_evidence(
                [row],
                source_status="queued",
                evaluated_at=now_iso,
                reason_detail=reason_detail,
                parameters=parameters or {},
            )
        )


def expire_stale_queued_opportunities(max_age_minutes: int = 30) -> int:
    """Expire queued work at its source deadline with a legacy age fallback.

    Queue insertion time is not a second, shorter market-validity clock. New
    rows therefore live until ``Opportunity.valid_until``. Only a missing or
    unparsable historical deadline falls back to ``max_age_minutes``.
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=max_age_minutes)).isoformat()
    with transaction(immediate=True):
        rows = (
            get_db()
            .execute(
                "SELECT opportunities.*, pending.created_at AS pending_created_at "
                "FROM opportunities JOIN pi_decisions pending "
                "ON pending.opportunity_id=opportunities.id "
                "AND pending.action='signal_pending' "
                "AND COALESCE(pending.signal_source, 'market_observer')='market_observer' "
                "WHERE opportunities.status='queued' "
                "AND pending.rowid=("
                "  SELECT newer.rowid FROM pi_decisions newer "
                "  WHERE newer.action='signal_pending' "
                "    AND newer.opportunity_id=opportunities.id "
                "    AND COALESCE(newer.signal_source, 'market_observer')='market_observer' "
                "  ORDER BY julianday(newer.created_at) DESC, newer.rowid DESC LIMIT 1"
                ") AND NOT EXISTS ("
                "  SELECT 1 FROM pi_decisions f "
                "  WHERE f.action!='signal_pending' "
                "    AND f.opportunity_id=opportunities.id "
                "    AND COALESCE(f.signal_source, 'market_observer')='market_observer' "
                "    AND (julianday(f.created_at)>julianday(pending.created_at) "
                "         OR (julianday(f.created_at)=julianday(pending.created_at) "
                "             AND f.rowid>pending.rowid))"
                ") AND ("
                "  (datetime(opportunities.valid_until) IS NOT NULL "
                "   AND datetime(opportunities.valid_until)<=datetime(?)) "
                "  OR (datetime(opportunities.valid_until) IS NULL "
                "      AND datetime(pending.created_at)<datetime(?))"
                ")",
                (now.isoformat(), cutoff),
            )
            .fetchall()
        )
        expired = 0
        for raw in rows:
            row = dict(raw)
            valid_until = row.get("valid_until")
            if valid_until and get_db().execute("SELECT datetime(?) IS NOT NULL", (valid_until,)).fetchone()[0]:
                reason_detail = "source Opportunity valid_until elapsed while awaiting System Operator evaluation"
                parameters = {
                    "expiry_basis": "valid_until",
                    "valid_until": str(valid_until),
                }
            else:
                reason_detail = (
                    "legacy pending System Operator evaluation window elapsed "
                    "without a parseable Opportunity valid_until"
                )
                parameters = {
                    "expiry_basis": "pending_created_at_legacy_fallback",
                    "pending_created_at": row.get("pending_created_at"),
                    "max_age_minutes": max_age_minutes,
                }
            expired += _expire_opportunity_rows_with_ttl_evidence(
                [row],
                source_status="queued",
                evaluated_at=now.isoformat(),
                reason_detail=reason_detail,
                parameters=parameters,
            )
        return expired


# --- Capital Events ---


def insert_capital_event(event) -> None:
    _insert("capital_events", _to_payload_dict(event))


def list_capital_events(limit: int = 200) -> list[dict]:
    return _select_all("capital_events", order="detected_at DESC", limit=limit)


def list_capital_events_between(start_at: str, end_at: str) -> list[dict]:
    """Return aggregate external cash flows inside one equity-snapshot window."""
    rows = get_db().execute(
        """
        SELECT *
        FROM capital_events
        WHERE datetime(detected_at) > datetime(?)
          AND datetime(detected_at) <= datetime(?)
        ORDER BY datetime(detected_at) ASC, id ASC
        """,
        (start_at, end_at),
    ).fetchall()
    return [dict(row) for row in rows]


def get_net_deposits() -> float:
    """Sum of all deposits minus withdrawals."""
    db = get_db()
    row = db.execute(
        "SELECT COALESCE(SUM(CASE WHEN event_type='deposit' THEN amount_usdt ELSE -amount_usdt END), 0) AS net FROM capital_events"
    ).fetchone()
    return float(row["net"]) if row else 0.0


def has_capital_event_for_broker(broker_id: str) -> bool:
    db = get_db()
    row = db.execute("SELECT 1 FROM capital_events WHERE broker_id = ? LIMIT 1", (broker_id,)).fetchone()
    return row is not None


def get_first_capital_event_time() -> str | None:
    """Earliest detected_at across all capital events (ISO string or None)."""
    db = get_db()
    row = db.execute("SELECT MIN(detected_at) AS t FROM capital_events").fetchone()
    return row["t"] if row and row["t"] else None


# --- Local paper account events ---


def insert_paper_account_event(event: PaperAccountEvent) -> None:
    _insert("paper_account_events", event.model_dump())


def list_paper_account_events(limit: int = 50) -> list[dict]:
    return _select_all("paper_account_events", order="created_at DESC, rowid DESC", limit=limit)


def get_paper_account_cash_balance() -> float:
    db = get_db()
    row = db.execute("SELECT COALESCE(SUM(amount_usdt), 0) AS cash FROM paper_account_events").fetchone()
    return float(row["cash"]) if row else 0.0


def has_paper_account_event() -> bool:
    db = get_db()
    return db.execute("SELECT 1 FROM paper_account_events LIMIT 1").fetchone() is not None


def has_paper_account_position_event(action: str, position_id: str) -> bool:
    db = get_db()
    return (
        db.execute(
            "SELECT 1 FROM paper_account_events WHERE action = ? AND position_id = ? LIMIT 1",
            (action, position_id),
        ).fetchone()
        is not None
    )


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
    from aletheia.storage.models import new_id, _utcnow

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
    from aletheia.storage.models import new_id, _utcnow

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
    from aletheia.storage.models import new_id, _utcnow

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
    return _select_all(
        "prediction_probability_snapshots", where=where, params=tuple(params), order="ts ASC", limit=limit
    )


def insert_prediction_claim(data: dict) -> None:
    from aletheia.storage.models import new_id, _utcnow

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
    return sha256_json(payload)[:16]


def insert_trade_proposal(p: TradeProposal) -> None:
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
    _update("trade_proposals", pid, kwargs)


def list_trade_proposals(limit: int = 50) -> list[dict]:
    return _select_all("trade_proposals", limit=limit)


def get_trade_proposal(pid: str) -> dict | None:
    return _select_one("trade_proposals", pid)


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
) -> bool:
    """Acquire an absent Proposal execution claim without replacing an owner."""
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write(
        "INSERT OR IGNORE INTO proposal_execution_claims "
        "(proposal_id, owner_token, claimed_at, lease_expires_at) VALUES (?, ?, ?, ?)",
        (proposal_id, owner_token, claimed_at, lease_expires_at),
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
) -> bool:
    """CAS-replace one observed expired execution claim."""
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write(
        "UPDATE proposal_execution_claims "
        "SET owner_token=?, claimed_at=?, lease_expires_at=? "
        "WHERE proposal_id=? AND owner_token=? AND lease_expires_at=?",
        (
            owner_token,
            claimed_at,
            lease_expires_at,
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


def proposal_execution_claim_owned(proposal_id: str, owner_token: str) -> bool:
    """Return whether the exact owner token still controls the Proposal."""
    return (
        get_db()
        .execute(
            "SELECT 1 FROM proposal_execution_claims WHERE proposal_id=? AND owner_token=? LIMIT 1",
            (proposal_id, owner_token),
        )
        .fetchone()
        is not None
    )


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


# --- Risk Decisions ---


def insert_risk_decision(d: RiskDecision) -> None:
    _insert("risk_decisions", _stamp_provenance(d.model_dump()))


def get_authoritative_risk_decision(
    proposal_id: str,
    proposal_commit_hash: str | None,
    evaluated_quantity: float | None,
    policy_hash: str | None = None,
) -> dict | None:
    """Return the authoritative decision for one immutable evaluated snapshot."""
    policy_clause = "AND policy_hash IS ?" if policy_hash is not None else ""
    params = [proposal_id, proposal_commit_hash, evaluated_quantity]
    if policy_hash is not None:
        params.append(policy_hash)
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
        )
        if existing is not None:
            return existing, False
        insert_risk_decision(d)
        stored = _select_one("risk_decisions", d.id)
        if stored is None:  # defensive: an acknowledged insert must be readable
            raise RuntimeError("authoritative risk decision insert was not persisted")
        return stored, True


def list_risk_decisions(limit: int = 50) -> list[dict]:
    return _select_all("risk_decisions", limit=limit)


def list_strategy_risk_decisions(strategy_id: str, limit: int = 1000) -> list[dict]:
    db = get_db()
    rows = db.execute(
        """
        SELECT rd.*
        FROM risk_decisions rd
        JOIN trade_proposals p ON p.id = rd.proposal_id
        WHERE p.strategy_id = ?
        ORDER BY rd.created_at DESC
        LIMIT ?
        """,
        (strategy_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


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
    # Exchange Orders persist the exact submitted plan price. Local paper keeps
    # the deterministic slipped fill price on its Order and the original price
    # on the Proposal, so positivity (not equality) is the honest invariant there.
    if (
        proposal_price <= 0
        or order_price <= 0
        or (execution_mode != "paper_internal" and order_price != proposal_price)
    ):
        raise ValueError("native entry Order price does not match Proposal")
    if execution_mode != "paper_internal" and data.get("client_order_id") != proposal_id:
        raise ValueError("exchange entry Order client identity is not durable")


def validate_native_order_authority(order: Order | Mapping[str, object]) -> None:
    """Fail closed when a new native Order lacks its immutable authority.

    Entry Orders consume exactly one approved authoritative RiskDecision. Close
    Orders consume the already-open Position they reduce. Adopted exchange state
    is deliberately exempt because its synthetic chain records observation, not
    a new Aletheia trading decision.
    """
    data = _to_payload_dict(order)
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
            raise ValueError(
                "native close Order consumes Position authority, not a RiskDecision"
            )
        for field in ("proposal_id", "symbol", "market_type", "direction", "execution_mode"):
            if data.get(field) != position.get(field):
                raise ValueError(f"native close Order {field} does not match Position")
        requested = float(data.get("quantity") or 0.0)
        position_quantity = float(position.get("quantity") or 0.0)
        tolerance = max(1e-12, position_quantity * 1e-9)
        if requested <= 0:
            raise ValueError("native close Order quantity must be positive")
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


def update_order(oid: str, **kwargs) -> None:
    _update("orders", oid, kwargs)


def list_orders(limit: int = 100) -> list[dict]:
    return _select_all("orders", limit=limit)


def list_unresolved_native_entry_orders_for_risk() -> list[dict]:
    """Return every unresolved native entry plus its represented local state.

    This query is intentionally unbounded: truncating accepted exchange Orders
    would let Risk under-count possible exposure.  Close Orders are excluded by
    both their durable reason and the Position pending-close backlink so older
    rows that predate ``close_reason`` cannot consume a new-entry allowance.
    """
    unresolved_states = (
        "pending",
        "submitted",
        "partial",
        "partially_filled",
        "submission_unknown",
        "materialization_missing",
    )
    placeholders = ",".join("?" for _ in unresolved_states)
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
        WHERE o.source = 'native'
          AND o.status IN ({placeholders})
          AND TRIM(COALESCE(o.close_reason, '')) = ''
          AND NOT EXISTS (
              SELECT 1
              FROM positions closing_position
              WHERE closing_position.pending_close_order_id = o.id
          )
        GROUP BY o.id
        ORDER BY o.created_at ASC, o.rowid ASC
        """,
            unresolved_states,
        )
        .fetchall()
    )
    return [dict(row) for row in rows]


def list_strategy_orders(
    strategy_id: str,
    execution_mode_prefix: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    db = get_db()
    sql = """
        SELECT o.*
        FROM orders o
        JOIN trade_proposals p ON p.id = o.proposal_id
        WHERE p.strategy_id = ?
    """
    params: list[object] = [strategy_id]
    if execution_mode_prefix:
        if execution_mode_prefix == "paper":
            sql += " AND (o.execution_mode = ? OR o.execution_mode = ?)"
            params.extend(["paper_internal", "bitget_demo"])
        else:
            sql += " AND o.execution_mode LIKE ?"
            params.append(f"{execution_mode_prefix}%")
    sql += " ORDER BY o.created_at ASC LIMIT ?"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# --- Fills ---


def insert_fill(f: Fill) -> None:
    _insert("fills", _stamp_provenance(f.model_dump()))


def list_fills(limit: int = 100) -> list[dict]:
    return _select_all("fills", limit=limit)


def list_strategy_fills(
    strategy_id: str,
    execution_mode_prefix: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    db = get_db()
    sql = """
        SELECT f.*
        FROM fills f
        JOIN orders o ON o.id = f.order_id
        JOIN trade_proposals p ON p.id = o.proposal_id
        WHERE p.strategy_id = ?
    """
    params: list[object] = [strategy_id]
    if execution_mode_prefix:
        if execution_mode_prefix == "paper":
            sql += " AND (o.execution_mode = ? OR o.execution_mode = ?)"
            params.extend(["paper_internal", "bitget_demo"])
        else:
            sql += " AND o.execution_mode LIKE ?"
            params.append(f"{execution_mode_prefix}%")
    sql += " ORDER BY f.filled_at ASC LIMIT ?"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# --- Positions ---


def insert_position(p: Position) -> None:
    _insert("positions", _stamp_provenance(p.model_dump()))


def update_position(pid: str, **kwargs) -> None:
    _update("positions", pid, kwargs)


def get_position(position_id: str) -> dict | None:
    """Return one Position by durable identity without list pagination."""
    return _select_one("positions", position_id)


def list_positions(status: str | None = None, limit: int = 50) -> list[dict]:
    if status:
        return _select_all("positions", where="status = ?", params=(status,), limit=limit)
    return _select_all("positions", limit=limit)


def list_all_open_positions() -> list[dict]:
    """Return the complete open-position working set for state/safety loops."""
    rows = get_db().execute("SELECT * FROM positions WHERE status='open' ORDER BY rowid DESC").fetchall()
    return [dict(row) for row in rows]


def _optional_plan_price_equal(left: object, right: object) -> bool:
    if left is None or right is None:
        return left is None and right is None
    try:
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12)
    except (TypeError, ValueError, OverflowError):
        return False


def _validate_position_exit_plan(plan: PositionExitPlanVersion) -> dict:
    data = plan.model_dump()
    _required_protection_text(data["position_id"], "position_id")
    _utc_timestamp_ms(data["effective_at"], "effective_at")
    created_ms = _utc_timestamp_ms(data["created_at"], "created_at")
    if created_ms < _utc_timestamp_ms(data["effective_at"], "effective_at"):
        raise PositionProtectionConflict("created_at cannot precede effective_at")
    if not any(data[field] is not None for field in ("stop_price", "take_profit_price", "exit_limit_price")):
        raise PositionProtectionConflict("ExitPlan must resolve at least one exit level")
    if (data["exit_limit_price"] is None) != (data["exit_limit_source"] is None):
        raise PositionProtectionConflict("exit_limit_price and exit_limit_source must be resolved together")
    _required_protection_text(data["provenance"], "provenance")
    expected_hash = hash_position_exit_plan_json(data["plan_json"])
    if data["plan_hash"] != expected_hash:
        raise PositionProtectionConflict("plan_hash does not match plan_json")
    payload = json.loads(data["plan_json"])
    for field in (
        "stop_price",
        "take_profit_price",
        "exit_limit_price",
        "exit_limit_source",
    ):
        if field not in payload:
            raise PositionProtectionConflict(f"plan_json is missing {field}")
        if field == "exit_limit_source":
            if payload[field] != data[field]:
                raise PositionProtectionConflict("plan_json exit_limit_source does not match resolved plan")
        elif not _optional_plan_price_equal(payload[field], data[field]):
            raise PositionProtectionConflict(f"plan_json {field} does not match resolved plan")
    return data


def append_position_exit_plan_version(
    plan: PositionExitPlanVersion,
    *,
    expected_effective_at: str | None,
) -> bool:
    """Append one contiguous ExitPlan and atomically advance its Position projection.

    Exact retries return False.  A stale projection, version gap, or conflicting
    reuse of ``(position_id, version)`` fails closed before any write commits.
    """

    data = _validate_position_exit_plan(plan)
    db = get_db()
    with transaction(immediate=True):
        existing = db.execute(
            "SELECT * FROM position_exit_plan_versions WHERE position_id=? AND version=?",
            (plan.position_id, plan.version),
        ).fetchone()
        if existing is not None:
            row = dict(existing)
            if all(row.get(field) == value for field, value in data.items()):
                projection = db.execute(
                    "SELECT exit_plan_effective_at FROM positions WHERE id=?",
                    (plan.position_id,),
                ).fetchone()
                if projection is None or projection["exit_plan_effective_at"] != plan.effective_at:
                    raise PositionProtectionConflict("exact ExitPlan retry is no longer the current projection")
                return False
            raise PositionProtectionConflict("ExitPlan version already exists with different immutable content")

        position = db.execute(
            "SELECT id, status, exit_plan_effective_at FROM positions WHERE id=?",
            (plan.position_id,),
        ).fetchone()
        if position is None:
            raise PositionProtectionConflict("Position does not exist")
        if position["status"] != "open":
            raise PositionProtectionConflict("ExitPlan can only be appended to an open Position")
        if position["exit_plan_effective_at"] != expected_effective_at:
            raise PositionProtectionConflict("ExitPlan projection compare-and-set failed")

        latest = db.execute(
            "SELECT version, effective_at FROM position_exit_plan_versions "
            "WHERE position_id=? ORDER BY version DESC LIMIT 1",
            (plan.position_id,),
        ).fetchone()
        expected_version = 1 if latest is None else int(latest["version"]) + 1
        if plan.version != expected_version:
            raise PositionProtectionConflict(f"ExitPlan expected version {expected_version}, got {plan.version}")
        if latest is not None and _utc_timestamp_ms(
            plan.effective_at,
            "effective_at",
        ) <= _utc_timestamp_ms(latest["effective_at"], "previous effective_at"):
            raise PositionProtectionConflict("ExitPlan effective_at must advance")

        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        execute_write(
            f"INSERT INTO position_exit_plan_versions ({columns}) VALUES ({placeholders})",
            tuple(data.values()),
        )
        projected = db.execute(
            "SELECT exit_plan_effective_at FROM positions WHERE id=?",
            (plan.position_id,),
        ).fetchone()
        if projected is None or projected["exit_plan_effective_at"] != plan.effective_at:
            raise PositionProtectionConflict("ExitPlan projection did not advance atomically")
    return True


def get_current_position_exit_plan(position_id: str) -> dict | None:
    row = (
        get_db()
        .execute(
            "SELECT plan.* FROM positions AS position "
            "JOIN position_exit_plan_versions AS plan "
            "ON plan.position_id=position.id "
            "AND plan.effective_at=position.exit_plan_effective_at "
            "WHERE position.id=? ORDER BY plan.version DESC LIMIT 1",
            (position_id,),
        )
        .fetchone()
    )
    return dict(row) if row is not None else None


def _state_activation_payload_matches(row: dict, data: dict) -> bool:
    return all(row.get(field) == value for field, value in data.items())


def insert_position_protection_state(state: PositionProtectionState) -> bool:
    """Activate one exact 1m identity once; no historical Position is backfilled."""

    data = state.model_dump()
    if state.status != "active":
        raise PositionProtectionConflict("protection state must activate as active")
    if (
        any(
            value is not None
            for value in (
                state.last_evaluated_bar_open_ms,
                state.last_inspected_bar_open_ms,
                state.gap_reason,
                state.gap_detected_at,
                state.recent_error,
            )
        )
        or state.gap_count != 0
    ):
        raise PositionProtectionConflict("new protection state cannot contain history")
    venue = _required_protection_text(state.venue, "venue").lower()
    if venue != state.venue:
        raise PositionProtectionConflict("venue must be normalized lowercase")
    canonical_symbol = _strict_canonical_protection_symbol(state.symbol)
    if state.timeframe != "1m":
        raise PositionProtectionConflict("protection timeframe must be 1m")
    if state.next_bar_open_ms % 60_000 != 0:
        raise PositionProtectionConflict("next_bar_open_ms must be 1m aligned")
    activated_ms = _utc_timestamp_ms(state.activated_at, "activated_at")
    _utc_timestamp_ms(state.created_at, "created_at")
    _utc_timestamp_ms(state.updated_at, "updated_at")
    first_not_backdated = ((activated_ms + 59_999) // 60_000) * 60_000
    if state.next_bar_open_ms < first_not_backdated:
        raise PositionProtectionConflict("protection cursor cannot predate activation")

    db = get_db()
    with transaction(immediate=True):
        existing = db.execute(
            "SELECT * FROM position_protection_state WHERE position_id=?",
            (state.position_id,),
        ).fetchone()
        if existing is not None:
            if _state_activation_payload_matches(dict(existing), data):
                return False
            raise PositionProtectionConflict(
                "Position protection state already exists with different immutable content"
            )

        position = db.execute(
            "SELECT id, symbol, market_type, status, opened_at, exit_plan_effective_at FROM positions WHERE id=?",
            (state.position_id,),
        ).fetchone()
        if position is None:
            raise PositionProtectionConflict("Position does not exist")
        if position["status"] != "open":
            raise PositionProtectionConflict("only an open Position can be protected")
        if to_canonical(position["symbol"]) != canonical_symbol:
            raise PositionProtectionConflict("protection symbol does not match Position")
        if _strict_physical_protection_market(position["market_type"]) != state.market_type:
            raise PositionProtectionConflict("protection market_type does not match Position")
        current_plan = get_current_position_exit_plan(state.position_id)
        if current_plan is None:
            raise PositionProtectionConflict("protection activation requires an ExitPlan")
        if (
            _utc_timestamp_ms(
                current_plan["effective_at"],
                "ExitPlan effective_at",
            )
            != activated_ms
        ):
            raise PositionProtectionConflict("protection activation cannot backdate or outlive its initial ExitPlan")
        if (
            position["opened_at"]
            and _utc_timestamp_ms(
                position["opened_at"],
                "Position opened_at",
            )
            > activated_ms
        ):
            raise PositionProtectionConflict("protection cannot activate before Position open")

        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        cursor = execute_write(
            f"INSERT INTO position_protection_state ({columns}) "
            f"VALUES ({placeholders}) ON CONFLICT(position_id) DO NOTHING",
            tuple(data.values()),
        )
        if cursor.rowcount != 1:
            raise PositionProtectionConflict("protection state insert lost its CAS")
    return True


def get_position_protection_state(position_id: str) -> dict | None:
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

    row = (
        get_db()
        .execute(
            "SELECT * FROM position_protection_triggers WHERE position_id=?",
            (position_id,),
        )
        .fetchone()
    )
    return dict(row) if row is not None else None


def list_position_protection_work(limit: int = 200) -> list[dict]:
    """Return bounded active/triggered local-paper protection work."""

    rows = get_db().execute(
        "SELECT state.* FROM position_protection_state AS state "
        "JOIN positions AS position ON position.id=state.position_id "
        "WHERE position.execution_mode='paper_internal' "
        "AND state.status IN ('active','gap','triggered') "
        "ORDER BY CASE state.status WHEN 'triggered' THEN 0 ELSE 1 END, "
        "state.next_bar_open_ms, state.position_id LIMIT ?",
        (max(1, min(int(limit), 1000)),),
    ).fetchall()
    return [dict(row) for row in rows]


def get_exact_position_protection_bar(state: dict) -> dict | None:
    """Read exactly the frozen 1m instrument/cursor; never select a fallback."""

    row = get_db().execute(
        "SELECT ts,open,high,low,close,volume,provider,exchange,market_type "
        "FROM market_ohlcv WHERE exchange=? AND symbol=? AND market_type=? "
        "AND timeframe='1m' AND ts=?",
        (
            state["venue"],
            state["symbol"],
            state["market_type"],
            int(state["next_bar_open_ms"]),
        ),
    ).fetchone()
    return dict(row) if row is not None else None


def get_position_exit_plan_for_bar(
    position_id: str,
    *,
    bar_open_ms: int,
    bar_close_ms: int,
) -> tuple[dict | None, bool]:
    """Return the plan at bar open and whether its successor bisects the bar."""

    bar_open_at = datetime.fromtimestamp(bar_open_ms / 1000, timezone.utc).isoformat()
    db = get_db()
    row = db.execute(
        "SELECT * FROM position_exit_plan_versions WHERE position_id=? "
        "AND julianday(effective_at) <= julianday(?) "
        "ORDER BY version DESC LIMIT 1",
        (position_id, bar_open_at),
    ).fetchone()
    if row is None:
        return None, False
    plan = dict(row)
    successor = db.execute(
        "SELECT effective_at FROM position_exit_plan_versions "
        "WHERE position_id=? AND version>? ORDER BY version ASC LIMIT 1",
        (position_id, plan["version"]),
    ).fetchone()
    bisected = bool(
        successor is not None
        and _utc_timestamp_ms(successor["effective_at"], "next ExitPlan effective_at")
        < bar_close_ms
    )
    return plan, bisected


def get_local_paper_close_chain(position_id: str) -> tuple[dict | None, dict | None]:
    """Return the first durable local close Order/Fill for crash recovery."""

    db = get_db()
    order = db.execute(
        "SELECT * FROM orders WHERE position_id=? "
        "AND execution_mode='paper_internal' AND close_reason IS NOT NULL "
        "ORDER BY rowid ASC LIMIT 1",
        (position_id,),
    ).fetchone()
    if order is None:
        return None, None
    fill = db.execute(
        "SELECT * FROM fills WHERE order_id=? ORDER BY rowid ASC LIMIT 1",
        (order["id"],),
    ).fetchone()
    return dict(order), dict(fill) if fill is not None else None


def record_position_protection_gap(
    position_id: str,
    *,
    expected_next_bar_open_ms: int,
    inspected_bar_open_ms: int,
    reason: str,
    detected_at: str,
    recent_error: str | None = None,
) -> bool:
    """CAS one newly inspected missing 1m bar without advancing the cursor."""

    if inspected_bar_open_ms != expected_next_bar_open_ms:
        raise PositionProtectionConflict("gap inspection must target the exact cursor")
    if inspected_bar_open_ms % 60_000 != 0:
        raise PositionProtectionConflict("gap cursor must be 1m aligned")
    _required_protection_text(reason, "gap reason")
    if _utc_timestamp_ms(detected_at, "gap detected_at") < inspected_bar_open_ms + 60_000:
        raise PositionProtectionConflict("a gap cannot be recorded before the bar closes")
    error = str(recent_error)[:1000] if recent_error is not None else None
    cursor = execute_write(
        "UPDATE position_protection_state SET "
        "last_inspected_bar_open_ms=?, status='gap', gap_reason=?, "
        "gap_detected_at=?, gap_count=gap_count+1, recent_error=?, updated_at=? "
        "WHERE position_id=? AND status IN ('active','gap') "
        "AND trigger_id IS NULL AND next_bar_open_ms=? "
        "AND (last_inspected_bar_open_ms IS NULL "
        "OR last_inspected_bar_open_ms < ?)",
        (
            inspected_bar_open_ms,
            reason,
            detected_at,
            error,
            detected_at,
            position_id,
            expected_next_bar_open_ms,
            inspected_bar_open_ms,
        ),
    )
    return cursor.rowcount == 1


def advance_position_protection_cursor(
    position_id: str,
    *,
    expected_next_bar_open_ms: int,
    evaluated_bar_open_ms: int,
    next_bar_open_ms: int,
    evaluated_at: str,
) -> bool:
    """CAS exactly one contiguous evaluated 1m bar and clear an active gap."""

    if evaluated_bar_open_ms != expected_next_bar_open_ms:
        raise PositionProtectionConflict("evaluated bar must equal the exact cursor")
    if next_bar_open_ms != evaluated_bar_open_ms + 60_000:
        raise PositionProtectionConflict("protection cursor must advance exactly one 1m bar")
    if evaluated_bar_open_ms % 60_000 != 0:
        raise PositionProtectionConflict("evaluated cursor must be 1m aligned")
    if _utc_timestamp_ms(evaluated_at, "evaluated_at") < evaluated_bar_open_ms + 60_000:
        raise PositionProtectionConflict("an incomplete bar cannot advance the cursor")
    cursor = execute_write(
        "UPDATE position_protection_state SET "
        "last_inspected_bar_open_ms=?, last_evaluated_bar_open_ms=?, "
        "next_bar_open_ms=?, status='active', gap_reason=NULL, "
        "gap_detected_at=NULL, recent_error=NULL, updated_at=? "
        "WHERE position_id=? AND status IN ('active','gap') "
        "AND trigger_id IS NULL AND next_bar_open_ms=? "
        "AND (last_inspected_bar_open_ms IS NULL "
        "OR last_inspected_bar_open_ms <= ?)",
        (
            evaluated_bar_open_ms,
            evaluated_bar_open_ms,
            next_bar_open_ms,
            evaluated_at,
            position_id,
            expected_next_bar_open_ms,
            evaluated_bar_open_ms,
        ),
    )
    return cursor.rowcount == 1


def skip_position_protection_boundary_bar(
    position_id: str,
    *,
    expected_next_bar_open_ms: int,
    skipped_at: str,
) -> bool:
    """Advance one permanently ambiguous ExitPlan-boundary bar as an explicit gap."""

    if expected_next_bar_open_ms % 60_000 != 0:
        raise PositionProtectionConflict("boundary cursor must be 1m aligned")
    if _utc_timestamp_ms(skipped_at, "skipped_at") < expected_next_bar_open_ms + 60_000:
        raise PositionProtectionConflict("an incomplete boundary bar cannot be skipped")
    cursor = execute_write(
        "UPDATE position_protection_state SET last_inspected_bar_open_ms=?, "
        "next_bar_open_ms=?, status='gap', gap_reason='exit_plan_boundary_unordered', "
        "gap_detected_at=?, gap_count=gap_count+1, recent_error=NULL, updated_at=? "
        "WHERE position_id=? AND status IN ('active','gap') AND trigger_id IS NULL "
        "AND next_bar_open_ms=?",
        (
            expected_next_bar_open_ms,
            expected_next_bar_open_ms + 60_000,
            skipped_at,
            skipped_at,
            position_id,
            expected_next_bar_open_ms,
        ),
    )
    return cursor.rowcount == 1


def block_position_protection_integrity(
    position_id: str,
    *,
    reason: str,
    blocked_at: str,
) -> bool:
    """CAS a non-materializable protection path into a terminal visible state."""

    _required_protection_text(reason, "integrity block reason")
    _utc_timestamp_ms(blocked_at, "blocked_at")
    cursor = execute_write(
        "UPDATE position_protection_state SET status='integrity_blocked', "
        "final_state='integrity_blocked', recent_error=?, updated_at=? "
        "WHERE position_id=? AND status IN ('active','gap','triggered')",
        (reason[:1000], blocked_at, position_id),
    )
    return cursor.rowcount == 1


def supersede_position_protection_after_close(position_id: str) -> bool:
    """Retire an untriggered cursor after another native path closed Position."""

    db = get_db()
    with transaction(immediate=True):
        state_row = db.execute(
            "SELECT * FROM position_protection_state WHERE position_id=?",
            (position_id,),
        ).fetchone()
        if state_row is None:
            return False
        state = dict(state_row)
        if state["status"] == "superseded":
            return False
        if state["trigger_id"] is not None or state["status"] not in {"active", "gap"}:
            raise PositionProtectionConflict(
                "only untriggered active protection can be superseded"
            )
        position_row = db.execute(
            "SELECT status, closed_at FROM positions WHERE id=?",
            (position_id,),
        ).fetchone()
        if (
            position_row is None
            or position_row["status"] != "closed"
            or not position_row["closed_at"]
        ):
            raise PositionProtectionConflict(
                "protection supersession requires a durably closed Position"
            )
        _utc_timestamp_ms(position_row["closed_at"], "Position closed_at")
        cursor = execute_write(
            "UPDATE position_protection_state SET status='superseded', updated_at=? "
            "WHERE position_id=? AND trigger_id IS NULL "
            "AND status IN ('active','gap')",
            (position_row["closed_at"], position_id),
        )
        if cursor.rowcount != 1:
            raise PositionProtectionConflict(
                "protection supersession compare-and-set failed"
            )
    return True


def _trigger_row_matches(row: dict, trigger: PositionProtectionTrigger) -> bool:
    return all(row.get(field) == value for field, value in trigger.model_dump().items())


def _validate_trigger_evidence(
    trigger: PositionProtectionTrigger,
    *,
    state: dict,
    plan: dict,
) -> None:
    if (
        trigger.venue,
        trigger.symbol,
        trigger.market_type,
        trigger.timeframe,
    ) != (
        state["venue"],
        state["symbol"],
        state["market_type"],
        state["timeframe"],
    ):
        raise PositionProtectionConflict("trigger identity does not match frozen state")
    if trigger.bar_open_ms != state["next_bar_open_ms"]:
        raise PositionProtectionConflict("trigger bar does not match the exact cursor")
    if trigger.bar_open_ms % 60_000 != 0 or trigger.bar_close_ms != trigger.bar_open_ms + 60_000:
        raise PositionProtectionConflict("trigger must contain one exact aligned 1m bar")
    if _utc_timestamp_ms(trigger.detected_at, "trigger detected_at") < trigger.bar_close_ms:
        raise PositionProtectionConflict("trigger cannot use an incomplete bar")
    if _utc_timestamp_ms(state["activated_at"], "activated_at") > trigger.bar_open_ms:
        raise PositionProtectionConflict("trigger bar predates protection activation")
    if (
        plan["version"] != trigger.plan_version
        or plan["effective_at"] != trigger.plan_effective_at
        or plan["plan_hash"] != trigger.plan_hash
    ):
        raise PositionProtectionConflict("trigger ExitPlan identity is inconsistent")
    if _utc_timestamp_ms(trigger.plan_effective_at, "plan effective_at") > trigger.bar_open_ms:
        raise PositionProtectionConflict("trigger cannot use a future ExitPlan")

    expected_stop = plan["stop_price"]
    expected_upside = plan["exit_limit_price"] if plan["exit_limit_price"] is not None else plan["take_profit_price"]
    if not _optional_plan_price_equal(trigger.stop_price, expected_stop):
        raise PositionProtectionConflict("trigger stop does not match resolved ExitPlan")
    if not _optional_plan_price_equal(trigger.upside_price, expected_upside):
        raise PositionProtectionConflict("trigger upside does not match resolved ExitPlan")
    if trigger.stop_price is None and trigger.upside_price is None:
        raise PositionProtectionConflict("trigger requires a resolved stop or upside")

    if trigger.bar_high < max(
        trigger.bar_open,
        trigger.bar_low,
        trigger.bar_close,
    ) or trigger.bar_low > min(
        trigger.bar_open,
        trigger.bar_high,
        trigger.bar_close,
    ):
        raise PositionProtectionConflict("trigger OHLC is internally inconsistent")
    expected_bar_hash = hash_position_protection_bar(
        venue=trigger.venue,
        symbol=trigger.symbol,
        market_type=trigger.market_type,
        timeframe=trigger.timeframe,
        bar_open_ms=trigger.bar_open_ms,
        bar_close_ms=trigger.bar_close_ms,
        bar_open=trigger.bar_open,
        bar_high=trigger.bar_high,
        bar_low=trigger.bar_low,
        bar_close=trigger.bar_close,
        bar_volume=trigger.bar_volume,
        provider=trigger.provider,
    )
    if trigger.bar_hash != expected_bar_hash:
        raise PositionProtectionConflict("trigger bar_hash does not match exact OHLCV")

    if not trigger.stop_hit and not trigger.target_hit:
        raise PositionProtectionConflict("trigger evidence contains no crossed barrier")
    expected_reason = (
        "stop_loss" if trigger.stop_hit else "exit_limit" if plan["exit_limit_price"] is not None else "take_profit"
    )
    if trigger.reason != expected_reason:
        raise PositionProtectionConflict(f"trigger reason must be {expected_reason} for resolved first-hit evidence")
    _required_protection_text(trigger.provider, "trigger provider")
    _required_canonical_uuid(trigger.trigger_id, "trigger_id")


def record_position_protection_trigger(trigger: PositionProtectionTrigger) -> bool:
    """CAS the first coherent trigger bundle; it can never be replaced."""

    db = get_db()
    with transaction(immediate=True):
        state_row = db.execute(
            "SELECT * FROM position_protection_state WHERE position_id=?",
            (trigger.position_id,),
        ).fetchone()
        if state_row is None:
            raise PositionProtectionConflict("Position protection state does not exist")
        state = dict(state_row)
        if state["trigger_id"] is not None:
            existing = db.execute(
                "SELECT * FROM position_protection_triggers WHERE position_id=?",
                (trigger.position_id,),
            ).fetchone()
            if existing is not None and _trigger_row_matches(dict(existing), trigger):
                return False
            raise PositionProtectionConflict("first trigger already recorded")
        if state["status"] not in {"active", "gap"}:
            raise PositionProtectionConflict("protection state cannot accept a trigger")
        position_row = db.execute(
            "SELECT status FROM positions WHERE id=?",
            (trigger.position_id,),
        ).fetchone()
        if position_row is None or position_row["status"] != "open":
            raise PositionProtectionConflict("trigger requires an open Position")
        bar_open_at = datetime.fromtimestamp(
            trigger.bar_open_ms / 1000,
            timezone.utc,
        ).isoformat()
        plan_row = db.execute(
            "SELECT * FROM position_exit_plan_versions "
            "WHERE position_id=? AND julianday(effective_at) <= julianday(?) "
            "ORDER BY version DESC LIMIT 1",
            (trigger.position_id, bar_open_at),
        ).fetchone()
        if plan_row is None:
            raise PositionProtectionConflict("no ExitPlan is effective at trigger bar open")
        plan = dict(plan_row)
        if plan["version"] != trigger.plan_version:
            raise PositionProtectionConflict(
                "trigger ExitPlan is not authoritative at bar open"
            )
        next_plan = db.execute(
            "SELECT effective_at FROM position_exit_plan_versions "
            "WHERE position_id=? AND version>? ORDER BY version ASC LIMIT 1",
            (trigger.position_id, trigger.plan_version),
        ).fetchone()
        if next_plan is not None and _utc_timestamp_ms(
            next_plan["effective_at"],
            "next ExitPlan effective_at",
        ) < trigger.bar_close_ms:
            raise PositionProtectionConflict(
                "trigger bar crosses an ExitPlan effective boundary"
            )
        _validate_trigger_evidence(
            trigger,
            state=state,
            plan=plan,
        )

        data = trigger.model_dump()
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        cursor = execute_write(
            f"INSERT INTO position_protection_triggers ({columns}) VALUES ({placeholders})",
            tuple(data.values()),
        )
        if cursor.rowcount != 1:
            raise PositionProtectionConflict("first-trigger insert failed")
        projected = db.execute(
            "SELECT trigger_id, status FROM position_protection_state WHERE position_id=?",
            (trigger.position_id,),
        ).fetchone()
        if projected is None or projected["trigger_id"] != trigger.trigger_id or projected["status"] != "triggered":
            raise PositionProtectionConflict("first-trigger projection did not advance")
    return True


def _validate_protection_close_order(
    *,
    state: dict,
    trigger: dict,
    position: dict,
    order: dict,
) -> None:
    if order.get("position_id") != position["id"]:
        raise PositionProtectionConflict("close Order does not link the protected Position")
    if order.get("risk_decision_id") is not None:
        raise PositionProtectionConflict("native close Order must not claim entry Risk authority")
    if not str(order.get("close_reason") or "").strip():
        raise PositionProtectionConflict("close Order has no durable close reason")
    if _utc_timestamp_ms(order.get("created_at"), "close Order created_at") < _utc_timestamp_ms(
        trigger["detected_at"],
        "trigger detected_at",
    ):
        raise PositionProtectionConflict("close Order predates its protection trigger")
    if to_canonical(order.get("symbol")) != state["symbol"]:
        raise PositionProtectionConflict("close Order symbol does not match protection identity")
    if _strict_physical_protection_market(order.get("market_type")) != state["market_type"]:
        raise PositionProtectionConflict("close Order market does not match protection identity")
    for field in ("direction", "execution_mode", "proposal_id"):
        if order.get(field) != position.get(field):
            raise PositionProtectionConflict(f"close Order {field} does not match Position")
    order_quantity = _finite_protection_number(
        order.get("quantity"),
        "close Order quantity",
        positive=True,
    )
    position_quantity = _finite_protection_number(
        position.get("quantity"),
        "Position quantity",
        positive=True,
    )
    if not math.isclose(order_quantity, position_quantity, rel_tol=1e-12, abs_tol=1e-12):
        raise PositionProtectionConflict("close Order quantity does not match Position")


def link_position_protection_close_order(
    position_id: str,
    *,
    trigger_id: str,
    close_order_id: str,
) -> bool:
    """Link the first exact native close Order without changing close behavior."""

    db = get_db()
    with transaction(immediate=True):
        state_row = db.execute(
            "SELECT * FROM position_protection_state WHERE position_id=?",
            (position_id,),
        ).fetchone()
        if state_row is None:
            raise PositionProtectionConflict("Position protection state does not exist")
        state = dict(state_row)
        if state["trigger_id"] != trigger_id:
            raise PositionProtectionConflict("close Order trigger identity does not match")
        trigger_row = db.execute(
            "SELECT * FROM position_protection_triggers WHERE position_id=? AND trigger_id=?",
            (position_id, trigger_id),
        ).fetchone()
        if trigger_row is None:
            raise PositionProtectionConflict("protection trigger evidence does not exist")
        if state["close_order_id"] is not None:
            if state["close_order_id"] == close_order_id:
                return False
            raise PositionProtectionConflict("close Order linkage is already write-once")
        position_row = db.execute(
            "SELECT * FROM positions WHERE id=?",
            (position_id,),
        ).fetchone()
        order_row = db.execute(
            "SELECT * FROM orders WHERE id=?",
            (close_order_id,),
        ).fetchone()
        if position_row is None or order_row is None:
            raise PositionProtectionConflict("close Order or Position does not exist")
        _validate_protection_close_order(
            state=state,
            trigger=dict(trigger_row),
            position=dict(position_row),
            order=dict(order_row),
        )
        cursor = execute_write(
            "UPDATE position_protection_state SET close_order_id=?, updated_at=? "
            "WHERE position_id=? AND trigger_id=? AND close_order_id IS NULL "
            "AND status='triggered'",
            (
                close_order_id,
                datetime.now(timezone.utc).isoformat(),
                position_id,
                trigger_id,
            ),
        )
        if cursor.rowcount != 1:
            raise PositionProtectionConflict("close Order linkage compare-and-set failed")
    return True


def materialize_position_protection_close(
    position_id: str,
    *,
    trigger_id: str,
    close_order_id: str,
    close_fill_id: str,
) -> bool:
    """Attach the exact close Fill once after the Position is durably closed."""

    db = get_db()
    with transaction(immediate=True):
        state_row = db.execute(
            "SELECT * FROM position_protection_state WHERE position_id=?",
            (position_id,),
        ).fetchone()
        if state_row is None:
            raise PositionProtectionConflict("Position protection state does not exist")
        state = dict(state_row)
        if state["trigger_id"] != trigger_id or state["close_order_id"] != close_order_id:
            raise PositionProtectionConflict("close Fill linkage identity does not match")
        trigger_row = db.execute(
            "SELECT * FROM position_protection_triggers WHERE position_id=? AND trigger_id=?",
            (position_id, trigger_id),
        ).fetchone()
        if trigger_row is None:
            raise PositionProtectionConflict("protection trigger evidence does not exist")
        trigger = dict(trigger_row)
        fill_row = db.execute(
            "SELECT * FROM fills WHERE id=?",
            (close_fill_id,),
        ).fetchone()
        if fill_row is None:
            raise PositionProtectionConflict("close Fill does not exist")
        fill = dict(fill_row)
        if state["close_fill_id"] is not None:
            if (
                state["close_fill_id"] == close_fill_id
                and state["close_fill_price"] == fill["fill_price"]
                and state["close_filled_at"] == fill["filled_at"]
                and state["final_state"] == "materialized"
                and state["status"] == "materialized"
            ):
                return False
            raise PositionProtectionConflict("close Fill linkage is already write-once")

        position_row = db.execute(
            "SELECT * FROM positions WHERE id=?",
            (position_id,),
        ).fetchone()
        order_row = db.execute(
            "SELECT * FROM orders WHERE id=?",
            (close_order_id,),
        ).fetchone()
        if position_row is None or order_row is None:
            raise PositionProtectionConflict("close Order or Position does not exist")
        position = dict(position_row)
        order = dict(order_row)
        _validate_protection_close_order(
            state=state,
            trigger=trigger,
            position=position,
            order=order,
        )
        if position["status"] != "closed":
            raise PositionProtectionConflict("Position is not durably closed")
        if order["status"] != "filled" or fill["order_id"] != close_order_id:
            raise PositionProtectionConflict("close Fill does not materialize the linked Order")
        fill_price = _finite_protection_number(
            fill["fill_price"],
            "close Fill price",
            positive=True,
        )
        fill_quantity = _finite_protection_number(
            fill["fill_quantity"],
            "close Fill quantity",
            positive=True,
        )
        order_quantity = _finite_protection_number(
            order["quantity"],
            "close Order quantity",
            positive=True,
        )
        if not math.isclose(fill_quantity, order_quantity, rel_tol=1e-12, abs_tol=1e-12):
            raise PositionProtectionConflict("close Fill quantity does not match close Order")
        filled_at_ms = _utc_timestamp_ms(fill["filled_at"], "close Fill filled_at")
        if filled_at_ms < _utc_timestamp_ms(order["created_at"], "close Order created_at"):
            raise PositionProtectionConflict("close Fill predates its linked Order")
        if filled_at_ms < _utc_timestamp_ms(
            trigger["detected_at"],
            "trigger detected_at",
        ):
            raise PositionProtectionConflict("close Fill predates its protection trigger")

        cursor = execute_write(
            "UPDATE position_protection_state SET close_fill_id=?, "
            "close_fill_price=?, close_filled_at=?, final_state='materialized', "
            "status='materialized', updated_at=? "
            "WHERE position_id=? AND trigger_id=? AND close_order_id=? "
            "AND close_fill_id IS NULL AND status='triggered'",
            (
                close_fill_id,
                fill_price,
                fill["filled_at"],
                datetime.now(timezone.utc).isoformat(),
                position_id,
                trigger_id,
                close_order_id,
            ),
        )
        if cursor.rowcount != 1:
            raise PositionProtectionConflict("close Fill linkage compare-and-set failed")
    return True


def insert_exit_breakeven_shadow_assignment(
    assignment: ExitBreakevenShadowAssignment,
) -> bool:
    """Append one prospective assignment; exact retries are idempotent."""

    data = assignment.model_dump()
    expected_rule_hash = hashlib.sha256(assignment.rule_json.encode("utf-8")).hexdigest()
    if assignment.rule_hash != expected_rule_hash:
        raise ExitBreakevenShadowConflict("exit breakeven shadow rule_hash mismatch")
    db = get_db()
    with transaction(immediate=True):
        existing = db.execute(
            "SELECT * FROM exit_breakeven_shadow_assignments WHERE position_id=?",
            (assignment.position_id,),
        ).fetchone()
        if existing is not None:
            if all(dict(existing).get(field) == value for field, value in data.items()):
                return False
            raise ExitBreakevenShadowConflict(
                "exit breakeven shadow assignment already exists with different content"
            )
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        execute_write(
            f"INSERT INTO exit_breakeven_shadow_assignments ({columns}) "
            f"VALUES ({placeholders})",
            tuple(data.values()),
        )
    return True


def get_exit_breakeven_shadow_assignment(position_id: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM exit_breakeven_shadow_assignments WHERE position_id=?",
        (position_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def insert_exit_breakeven_shadow_outcome(
    outcome: ExitBreakevenShadowOutcome,
) -> bool:
    """Append one paired result; retained outcomes can never be revised."""

    data = outcome.model_dump()
    expected_evidence_hash = hashlib.sha256(
        outcome.evidence_json.encode("utf-8")
    ).hexdigest()
    if outcome.evidence_hash != expected_evidence_hash:
        raise ExitBreakevenShadowConflict("exit breakeven shadow evidence_hash mismatch")
    db = get_db()
    with transaction(immediate=True):
        existing = db.execute(
            "SELECT * FROM exit_breakeven_shadow_outcomes WHERE position_id=?",
            (outcome.position_id,),
        ).fetchone()
        if existing is not None:
            if all(dict(existing).get(field) == value for field, value in data.items()):
                return False
            raise ExitBreakevenShadowConflict(
                "exit breakeven shadow outcome already exists with different content"
            )
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        execute_write(
            f"INSERT INTO exit_breakeven_shadow_outcomes ({columns}) "
            f"VALUES ({placeholders})",
            tuple(data.values()),
        )
    return True


def get_exit_breakeven_shadow_outcome(position_id: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM exit_breakeven_shadow_outcomes WHERE position_id=?",
        (position_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def list_exit_breakeven_shadow_outcomes(limit: int = 1000) -> list[dict]:
    if limit < 1 or limit > 10000:
        raise ValueError("limit must be between 1 and 10000")
    rows = get_db().execute(
        "SELECT outcome.*, assignment.direction, assignment.market_type, "
        "assignment.treatment_id, assignment.code_commit, position.close_reason "
        "FROM exit_breakeven_shadow_outcomes AS outcome "
        "JOIN exit_breakeven_shadow_assignments AS assignment "
        "ON assignment.position_id=outcome.position_id "
        "LEFT JOIN positions AS position ON position.id=outcome.position_id "
        "ORDER BY outcome.settled_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def find_compatible_open_position(
    symbol: str,
    market_type: str,
    direction: str,
    execution_mode: str,
    strategy_id: str | None,
    *,
    proposal_id: str | None = None,
) -> dict | None:
    """Find one exact aggregation target without a bounded list scan.

    Local paper passes ``proposal_id`` so a second independent Proposal can
    never be hidden as an averaging leg in the first Position. Exchange adapters
    retain their existing strategy-level lookup until their own scale-in intent
    has a durable contract.
    """
    sql = (
        "SELECT * FROM positions WHERE status='open' "
        "AND symbol=? AND market_type=? AND direction=? AND execution_mode=?"
    )
    params: list = [symbol, market_type, direction, execution_mode]
    if proposal_id is not None:
        sql += " AND proposal_id=?"
        params.append(proposal_id)
    if strategy_id is not None:
        sql += " AND COALESCE(strategy_id, '') IN ('', ?)"
        params.append(strategy_id)
    sql += " ORDER BY rowid ASC LIMIT 1"
    row = get_db().execute(sql, params).fetchone()
    return dict(row) if row else None


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


def get_active_position_disappearance_candidate(identity_key: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM position_disappearance_candidates WHERE identity_key=? AND status='pending' LIMIT 1",
        (identity_key,),
    ).fetchone()
    return dict(row) if row else None


def get_active_position_disappearance_candidate_for_position(
    position_id: str,
) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM position_disappearance_candidates "
        "WHERE position_id=? AND status='pending' ORDER BY updated_at DESC LIMIT 1",
        (position_id,),
    ).fetchone()
    return dict(row) if row else None


def update_position_disappearance_candidate(candidate_id: str, **kwargs) -> None:
    _update("position_disappearance_candidates", candidate_id, kwargs)


def list_position_disappearance_candidates(*, status: str | None = None, limit: int = 100) -> list[dict]:
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
    return _select_all(
        "position_disappearance_observations",
        where="candidate_id = ?",
        params=(candidate_id,),
        order="observed_at ASC",
        limit=limit,
    )


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


def insert_strategy(s: StrategyRecord) -> None:
    _insert("strategy_records", s.model_dump())


def update_strategy(sid: str, **kwargs) -> None:
    _update("strategy_records", sid, kwargs)


def get_strategy(sid: str) -> dict | None:
    return _select_one("strategy_records", sid)


def list_strategies(limit: int = 50) -> list[dict]:
    return _select_all("strategy_records", limit=limit)


# --- Knowledge Items ---


def insert_knowledge_item(k: KnowledgeItem) -> None:
    _insert("knowledge_items", k.model_dump())


def update_knowledge_item(kid: str, **kwargs) -> None:
    _update("knowledge_items", kid, kwargs)


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


def enqueue_serenity_theme_for_pi(
    theme_id: str,
    opportunity_ids: list[str],
    *,
    min_score_for_pi: float | None = None,
    new_carriers_only: bool = False,
) -> list[str]:
    """Atomically hand one exact Serenity opportunity set to the PI queue.

    Existing exact pending carriers are successful idempotent facts. Theme
    promotion commits only after every requested active opportunity owns a
    durable pending carrier; any failure rolls back carriers and intent updates.
    """
    from aletheia.storage.models import PiDecision

    ordered_ids = list(dict.fromkeys(str(item) for item in opportunity_ids if item))
    if not ordered_ids:
        raise ValueError("Serenity PI handoff requires at least one opportunity")

    def exact_pending_carrier(
        opportunity_id: str,
        opportunity: dict,
    ) -> dict | None:
        pending = find_pending_pi_decision_for_opportunity(
            opportunity_id,
            signal_source="serenity",
        )
        if pending is None:
            return None
        mismatched = [
            field for field in ("symbol", "direction", "market_type") if pending.get(field) != opportunity.get(field)
        ]
        if mismatched:
            raise ValueError(
                "Serenity pending carrier conflicts with opportunity identity: "
                f"{opportunity_id}:" + ",".join(mismatched)
            )
        return pending

    def exact_final_decision(
        opportunity_id: str,
        opportunity: dict,
    ) -> dict | None:
        classification = classify_final_pi_decision_for_opportunity(
            opportunity_id,
            "serenity",
            symbol=opportunity.get("symbol"),
            direction=opportunity.get("direction"),
            market_type=opportunity.get("market_type"),
        )
        if classification["status"] == "integrity_unavailable":
            raise ValueError(
                f"Serenity final decision integrity unavailable: {opportunity_id}:{classification['reason']}"
            )
        final = classification.get("decision")
        if final is None:
            return None
        if final.get("action") not in {"trade", "skip"}:
            return None
        return final

    with transaction(immediate=True):
        theme = get_serenity_theme(theme_id)
        if theme is None:
            raise ValueError(f"Serenity theme not found: {theme_id}")

        placeholders = ",".join("?" for _ in ordered_ids)
        rows = (
            get_db()
            .execute(
                f"SELECT * FROM serenity_opportunities WHERE id IN ({placeholders}) AND theme_id=? AND status='active'",
                (*ordered_ids, theme_id),
            )
            .fetchall()
        )
        by_id = {row["id"]: dict(row) for row in rows}
        missing = [opportunity_id for opportunity_id in ordered_ids if opportunity_id not in by_id]
        if missing:
            raise ValueError(
                "Serenity PI handoff requires active opportunities from the same theme: " + ",".join(missing)
            )

        theme_status = str(theme.get("status") or "")
        if theme_status not in {"active", "promoted"}:
            raise ValueError(f"Serenity theme status does not allow PI handoff: {theme_status}")

        if min_score_for_pi is not None:
            from aletheia.trading.serenity_scoring import score_serenity_opportunity

            qualified_ids = []
            for opportunity_id in ordered_ids:
                opportunity = by_id[opportunity_id]
                evidence = list_serenity_evidence(theme_id=opportunity["theme_id"], limit=200)
                gates = list_serenity_risk_gates(opportunity_id=opportunity_id, limit=200)
                score = score_serenity_opportunity(
                    opportunity,
                    evidence=evidence,
                    risk_gates=gates,
                )["score"]
                if score >= min_score_for_pi:
                    qualified_ids.append(opportunity_id)
            ordered_ids = qualified_ids

        now = datetime.now(timezone.utc).isoformat()
        new_carrier_ids: list[str] = []
        for opportunity_id in ordered_ids:
            opportunity = by_id[opportunity_id]
            pending = exact_pending_carrier(opportunity_id, opportunity)
            final = exact_final_decision(opportunity_id, opportunity)
            if pending is None and final is None:
                insert_pi_decision(
                    PiDecision(
                        symbol=opportunity.get("symbol"),
                        direction=opportunity.get("direction"),
                        market_type=opportunity.get("market_type"),
                        action="signal_pending",
                        conviction=0,
                        reasoning=(f"Serenity promote: theme={theme_id[:8]} opp={opportunity_id[:8]}"),
                        opportunity_id=opportunity_id,
                        signal_source="serenity",
                        debate_ran=False,
                    )
                )
                new_carrier_ids.append(opportunity_id)
            update_serenity_opportunity(
                opportunity_id,
                action_intent="pi_evaluate",
                updated_at=now,
            )

        durable_ids = [
            opportunity_id
            for opportunity_id in ordered_ids
            if (
                exact_pending_carrier(opportunity_id, by_id[opportunity_id]) is not None
                or exact_final_decision(opportunity_id, by_id[opportunity_id]) is not None
            )
        ]
        if durable_ids != ordered_ids:
            raise RuntimeError("Serenity PI handoff is missing a durable pending carrier")

        active_rows = (
            get_db()
            .execute(
                "SELECT * FROM serenity_opportunities WHERE theme_id=? AND status='active'",
                (theme_id,),
            )
            .fetchall()
        )
        all_active_have_carriers = all(
            (
                exact_pending_carrier(row["id"], dict(row)) is not None
                or exact_final_decision(row["id"], dict(row)) is not None
            )
            for row in active_rows
        )
        update_serenity_theme(
            theme_id,
            status="promoted" if all_active_have_carriers else "active",
            promoted_at=now if all_active_have_carriers else None,
            updated_at=now,
        )
        return new_carrier_ids if new_carriers_only else durable_ids


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
        pass


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


def transition_serenity_opportunity_to_reviewed(opportunity_id: str) -> bool:
    """Terminalize one exact active Serenity source without touching trade state."""
    result = execute_write(
        "UPDATE serenity_opportunities SET status='reviewed', updated_at=? WHERE id=? AND status='active'",
        (datetime.now(timezone.utc).isoformat(), opportunity_id),
    )
    return result.rowcount == 1


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
    where: str | None = None
    params: list = []
    if opportunity_id is not None:
        where = "opportunity_id = ?"
        params.append(opportunity_id)
    return _select_all("serenity_post_mortems", where=where, params=tuple(params), limit=limit)


# --- Durable Outcome hook outbox ---


def enqueue_outcome_hook_job(position_id: str, hook_type: str) -> dict:
    """Insert one durable hook intent, preserving an existing retry/completion row."""
    job = OutcomeHookJob(position_id=position_id, hook_type=hook_type)
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


def list_outcome_hook_jobs(
    *,
    position_id: str | None = None,
    hook_type: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict]:
    conditions: list[str] = []
    params: list[object] = []
    if position_id is not None:
        conditions.append("position_id = ?")
        params.append(position_id)
    if hook_type is not None:
        conditions.append("hook_type = ?")
        params.append(hook_type)
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    return _select_all(
        "outcome_hook_jobs",
        where=" AND ".join(conditions) if conditions else None,
        params=tuple(params),
        order="created_at ASC, id ASC",
        limit=limit,
    )


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
    row = get_db().execute(
        "SELECT * FROM serenity_research_runs "
        "WHERE parent_run_id = ? AND retry_count = ? "
        "ORDER BY created_at, id LIMIT 1",
        (parent_run_id, retry_count),
    ).fetchone()
    return dict(row) if row else None


def insert_serenity_research_retry(r) -> tuple[dict, bool]:
    """Insert one durable retry identity, returning the winner on a race."""

    retry_key = getattr(r, "idempotency_key", None)
    if (
        not r.parent_run_id
        or not isinstance(retry_key, str)
        or not retry_key.startswith("serenity-retry:")
    ):
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


def get_active_serenity_research_run() -> dict | None:
    rows = list_active_serenity_research_runs(limit=1)
    return rows[0] if rows else None


def list_active_serenity_research_runs(limit: int = 100) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM serenity_research_runs WHERE status IN ('queued', 'running', 'waiting_retry') "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_latest_successful_serenity_research_run() -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM serenity_research_runs "
        "WHERE status IN ('completed', 'completed_empty') "
        "ORDER BY COALESCE(finished_at, created_at) DESC LIMIT 1"
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


def get_serenity_claim(claim_id: str) -> dict | None:
    return _select_one("serenity_claims", claim_id)


def insert_serenity_claims_bulk(claims: list) -> None:
    if not claims:
        return
    db = get_db()
    with transaction():
        for c in claims:
            data = c.model_dump()
            data["price_relevant"] = int(data.get("price_relevant", False))
            cols = ", ".join(data.keys())
            placeholders = ", ".join(["?"] * len(data))
            execute_write(f"INSERT INTO serenity_claims ({cols}) VALUES ({placeholders})", list(data.values()))


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


def insert_serenity_theme_claim(theme_id: str, claim_id: str, relevance_score: float | None = None) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    execute_write(
        "INSERT OR IGNORE INTO serenity_theme_claims (theme_id, claim_id, relevance_score) VALUES (?, ?, ?)",
        (theme_id, claim_id, relevance_score),
    )
    if not was_in_tx:
        db.commit()


def insert_serenity_theme_claims_bulk(links: list[tuple[str, str, float | None]]) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    for theme_id, claim_id, relevance_score in links:
        execute_write(
            "INSERT OR IGNORE INTO serenity_theme_claims (theme_id, claim_id, relevance_score) VALUES (?, ?, ?)",
            (theme_id, claim_id, relevance_score),
        )
    if not was_in_tx:
        db.commit()


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
    from datetime import datetime, timezone, timedelta

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
    limit: int = 100,
    exchange: str = "",
    market_type: str | None = None,
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
    return _select_all(
        "market_snapshots",
        where=where,
        params=tuple(params),
        order="COALESCE(snapshot_at, '') DESC, rowid DESC",
        limit=limit,
    )


# --- Portfolio Snapshots ---


def insert_portfolio_snapshot(snap) -> None:
    _insert("portfolio_snapshots", snap.model_dump())


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
    if days is not None and days > 0:
        if limit and limit > 0:
            rows = db.execute(
                """
                SELECT * FROM (
                    SELECT *
                    FROM portfolio_snapshots
                    WHERE snapshot_at >= datetime('now', ?)
                    ORDER BY snapshot_at DESC, id DESC
                    LIMIT ?
                )
                ORDER BY snapshot_at ASC, id ASC
                """,
                (f"-{days} days", limit),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT *
                FROM portfolio_snapshots
                WHERE snapshot_at >= datetime('now', ?)
                ORDER BY snapshot_at ASC, id ASC
                """,
                (f"-{days} days",),
            ).fetchall()
    else:
        cap = limit if (limit and limit > 0) else 100
        rows = db.execute(
            """
            SELECT *
            FROM (
                SELECT *
                FROM portfolio_snapshots
                ORDER BY snapshot_at DESC, id DESC
                LIMIT ?
            )
            ORDER BY snapshot_at ASC, id ASC
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
    params: list[object] = [execution_mode]
    where = "execution_mode = ? AND account_equity_usdt IS NOT NULL"
    if days is not None and days > 0:
        where += " AND snapshot_at >= datetime('now', ?)"
        params.append(f"-{days} days")
    if limit and limit > 0:
        rows = db.execute(
            f"""
            SELECT * FROM (
                SELECT * FROM portfolio_snapshots
                WHERE {where}
                ORDER BY snapshot_at DESC, id DESC
                LIMIT ?
            )
            ORDER BY snapshot_at ASC, id ASC
            """,
            (*params, limit),
        ).fetchall()
    else:
        rows = db.execute(
            f"""
            SELECT * FROM portfolio_snapshots
            WHERE {where}
            ORDER BY snapshot_at ASC, id ASC
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


def insert_tool_call(
    run_id: str,
    tool_name: str,
    tool_class: str,
    permitted: bool,
    input_data: str | None = None,
    output_data: str | None = None,
    inference_call_id: str | None = None,
    duration_ms: int | None = None,
) -> None:
    from aletheia.storage.models import new_id

    _insert(
        "agent_tool_calls",
        {
            "id": new_id(),
            "run_id": run_id,
            "tool_name": tool_name,
            "tool_class": tool_class,
            "input_data": input_data,
            "output_data": output_data,
            "permitted": 1 if permitted else 0,
            "inference_call_id": inference_call_id,
            "duration_ms": duration_ms,
            "created_at": _step_timestamp(),
        },
    )


def list_tool_calls(run_id: str | None = None, limit: int = 100) -> list[dict]:
    if run_id:
        try:
            from aletheia.agents.helpers import flush_run_telemetry

            flush_run_telemetry(run_id)
        except Exception:
            pass
        return _select_all("agent_tool_calls", where="run_id = ?", params=(run_id,), limit=limit)
    try:
        from aletheia.agents.helpers import flush_all_agent_telemetry

        flush_all_agent_telemetry()
    except Exception:
        pass
    return _select_all("agent_tool_calls", limit=limit)


def insert_run_step(
    run_id: str, step_number: int, action: str, input_data: str | None = None, output_data: str | None = None
) -> None:
    """Record one reasoning/action step of an agent run (ordered by insertion time).

    `step_number` is preserved on the row for compatibility, but callers that read
    via `list_run_steps` see a step number reassigned by merged execution order
    (see `list_run_steps`).
    """
    from aletheia.storage.models import new_id

    _insert(
        "agent_run_steps",
        {
            "id": new_id(),
            "run_id": run_id,
            "step_number": step_number,
            "action": action,
            "input_data": input_data,
            "output_data": output_data,
            "created_at": _step_timestamp(),
        },
    )


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
        pass
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


def prune_shadow_trades(days: int = 90) -> int:
    return _delete_old_rows("shadow_trades", days)


def prune_filter_decision_batches(days: int = 30) -> int:
    return _delete_old_rows("filter_decision_batches", days)


def min_open_experiment_exposure_at() -> str | None:
    """Return the earliest durable exposure of an active open experiment."""
    db = get_db()
    try:
        row = db.execute(
            "SELECT MIN(x.exposed_at) AS m "
            "FROM learning_experiments e JOIN learning_exposures x "
            "ON x.experiment_id=e.experiment_id "
            "WHERE e.ended_at IS NULL AND e.status='active'"
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    val = row["m"] if hasattr(row, "keys") else row[0]
    return str(val) if val else None


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
    min_experiment_started_at: str | None = None,
) -> int:
    """Prune filter_decisions with clean_window_cutoff guard.

    Default lowered 90d→45d (Phase 3). If an open learning_experiment started
    before ``now - days``, keep everything back to that experiment's start so
    the paired-cohort read stays reproducible.
    """
    if min_experiment_started_at is None:
        min_experiment_started_at = min_open_experiment_exposure_at()
    cutoff = _effective_cutoff(days, min_experiment_started_at)
    return _delete_by_rowid_batches("filter_decisions", "evaluated_at < ?", (cutoff,))


def prune_filter_candidate_outcomes(
    days: int = 30,
    min_experiment_started_at: str | None = None,
) -> int:
    """Prune filter_candidate_outcomes with clean_window_cutoff guard.

    Default lowered 90d→30d (Phase 3). Guard prevents deleting outcome rows an
    open experiment still needs for its paired cohort — the effective cutoff is
    the OLDER of ``now - days`` and the earliest open-experiment start.
    """
    if min_experiment_started_at is None:
        min_experiment_started_at = min_open_experiment_exposure_at()
    cutoff = _effective_cutoff(days, min_experiment_started_at)

    # A row still 'pending' at the cutoff was never measured, and deleting it
    # leaves nothing behind that says so: every coverage aggregation matches
    # status='settled', and doctor's stale-overdue count only counts rows that
    # still exist, so a queue that never caught up ends up indistinguishable from
    # one that drained. The gap closes itself and the ledger reads as complete.
    #
    # Deletion still has to happen — retention is what keeps the file bounded, and
    # a row this old is past the settler's reach anyway (the 1m/5m bars its window
    # needs expired days earlier). What must not happen is the silence. So record
    # the loss in audit_events, which is deliberately outside _PRUNABLE and never
    # pruned, before the rows go.
    unmeasured = get_db().execute(
        "SELECT horizon, COUNT(*) FROM filter_candidate_outcomes "
        "WHERE status = 'pending' AND entry_ts < ? GROUP BY horizon",
        (cutoff,),
    ).fetchall()
    dropped = sum(row[1] for row in unmeasured)
    if dropped:
        by_horizon = {str(row[0]): int(row[1]) for row in unmeasured}
        _sqlite_logger.warning(
            "prune_filter_candidate_outcomes: %d filter-outcome rows reached the "
            "%s retention cutoff still unmeasured (%s) — the canonical settler is "
            "not keeping up; deleting them and recording the loss in audit_events",
            dropped,
            cutoff,
            " ".join(f"{k}={v}" for k, v in sorted(by_horizon.items())),
        )
        insert_audit_event(AuditEvent(
            actor="system",
            source="prune_filter_candidate_outcomes",
            event_type="filter_outcome_evidence_dropped_unmeasured",
            entity_type="filter_candidate_outcomes",
            entity_id=cutoff,
            after_state=json.dumps({
                "dropped_unmeasured": dropped,
                "by_horizon": by_horizon,
                "cutoff": cutoff,
                "retention_days": days,
            }, sort_keys=True),
            message=(
                f"{dropped} filter-outcome rows were deleted at the {cutoff} "
                f"retention cutoff while still 'pending'. Their counterfactual "
                f"returns were never measured, so Learning contribution and filter "
                f"feedback over any window containing them are computed on an "
                f"incomplete population."
            ),
        ))
    return _delete_by_rowid_batches(
        "filter_candidate_outcomes", "entry_ts < ?", (cutoff,)
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
            ")"
        ),
        extra_params=(*_PENDING_FINAL_ACTIONS, *_PENDING_FINAL_ACTIONS),
    )


def prune_signal_pending_stale(minutes: int = 30) -> int:
    """Delete old market-observer carriers only after their source is terminal.

    A queued source remains restart-recoverable even when old, and a final row in
    ``submitting`` remains an active execution-recovery claim. Serenity carriers
    have a different age contract and are intentionally excluded.
    """
    db = get_db()
    cur = execute_write(
        "DELETE FROM pi_decisions WHERE action='signal_pending' "
        "AND COALESCE(signal_source, 'market_observer')='market_observer' "
        "AND datetime(created_at) < datetime('now', ? || ' minutes') "
        "AND EXISTS ("
        " SELECT 1 FROM opportunities source "
        " WHERE source.id=pi_decisions.opportunity_id "
        " AND source.status IN ('reviewed','expired')"
        ") "
        "AND NOT EXISTS ("
        " SELECT 1 FROM pi_decisions final "
        " WHERE final.opportunity_id=pi_decisions.opportunity_id "
        " AND COALESCE(final.signal_source, 'market_observer')="
        "     COALESCE(pi_decisions.signal_source, 'market_observer') "
        " AND final.action!='signal_pending' "
        " AND final.proposal_status='submitting'"
        ")",
        (f"-{minutes}",),
    )
    return cur.rowcount


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
            pass
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
    execute_many_write(
        "INSERT OR REPLACE INTO market_ohlcv "
        "(exchange, symbol, market_type, timeframe, ts, open, high, low, close, volume, provider) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
) -> list[dict]:
    """Return up to `limit` most-recent OHLCV bars, oldest-first.

    When exchange/market_type are supplied, queries are scoped to that exact
    instrument. Unscoped legacy callers should be migrated by their owners.
    """
    db = get_db()
    scope = _resolve_ohlcv_scope(db, symbol, timeframe, exchange, market_type)
    if scope is None:
        return []
    scoped_exchange, scoped_market_type = scope
    before_clause = " AND ts < ?" if before_ts is not None else ""
    params: tuple = (scoped_exchange, symbol, scoped_market_type, timeframe)
    if before_ts is not None:
        params = (*params, int(before_ts))
    rows = db.execute(
        "SELECT ts, open, high, low, close, volume, provider, exchange, market_type "
        "FROM market_ohlcv WHERE exchange=? AND symbol=? AND market_type=? AND timeframe=? "
        + before_clause
        + " ORDER BY ts DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    # Reverse so callers get oldest→newest
    return [dict(r) for r in reversed(rows)]


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
        OHLCVTierManager,
        OHLCVTieringPolicy,
    )

    db = get_db()
    rows = db.execute(
        "SELECT ts,open,high,low,close,volume,provider FROM market_ohlcv "
        "WHERE exchange=? AND symbol=? AND market_type=? AND timeframe=? ORDER BY ts",
        (exchange.lower(), symbol, market_type, timeframe),
    ).fetchall()
    manager = OHLCVTierManager(
        OHLCVTieringPolicy(hot_bars=hot_bars, cold_chunk_bars=cold_chunk_bars)
    )
    return manager.plan(
        OHLCVSeriesIdentity(exchange.lower(), symbol, market_type, timeframe),
        rows,
    )


# --- Universe Generator ---

from aletheia.storage.models import (
    UniverseAsset,
    UniverseFeatureSnapshot,
    UniverseScannerRun,
    UniverseSnapshot,
    UniverseMember,
    UniversePolicy,
    UniverseTwinShadowBatch,
    UniverseTwinShadowEvidence,
)


def insert_universe_asset(asset: UniverseAsset) -> None:
    _insert("universe_registry", asset.model_dump())


def upsert_universe_asset(asset: UniverseAsset) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    row = db.execute(
        "SELECT id FROM universe_registry WHERE venue = ? AND symbol = ? AND market_type = ?",
        (asset.venue, asset.symbol, asset.market_type),
    ).fetchone()
    if row:
        execute_write(
            "UPDATE universe_registry SET status = ?, last_seen_at = ?, "
            "min_notional = ?, price_precision = ?, amount_precision = ?, metadata = ? "
            "WHERE id = ?",
            (
                asset.status,
                asset.last_seen_at,
                asset.min_notional,
                asset.price_precision,
                asset.amount_precision,
                asset.metadata,
                row["id"],
            ),
        )
    else:
        data = asset.model_dump()
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        execute_write(f"INSERT INTO universe_registry ({cols}) VALUES ({placeholders})", list(data.values()))
    if not was_in_tx:
        db.commit()


def sync_universe_assets(
    assets: list[UniverseAsset],
    *,
    active_venue: str,
    stale_cutoff_iso: str,
) -> tuple[int, int, int]:
    """Bulk upsert one registry refresh and set-deactivate stale rows atomically."""

    rows = [asset.model_dump() for asset in assets]
    columns = list(rows[0]) if rows else list(UniverseAsset.model_fields)
    placeholders = ", ".join(["?"] * len(columns))
    update_columns = [
        "status",
        "last_seen_at",
        "min_notional",
        "price_precision",
        "amount_precision",
        "is_blacklisted",
        "blacklist_reason",
        "metadata",
    ]
    update_sql = ", ".join(f"{column}=excluded.{column}" for column in update_columns)
    db = get_db()
    with transaction():
        if rows:
            execute_many_write(
                f"INSERT INTO universe_registry ({', '.join(columns)}) "
                f"VALUES ({placeholders}) "
                "ON CONFLICT(venue, symbol, market_type) DO UPDATE SET "
                f"{update_sql}",
                [[row[column] for column in columns] for row in rows],
            )
        other = execute_write(
            "UPDATE universe_registry SET status='inactive' WHERE venue != ? AND status='active'",
            (active_venue,),
        ).rowcount
        stale = execute_write(
            "UPDATE universe_registry SET status='inactive' WHERE venue = ? AND status='active' AND last_seen_at < ?",
            (active_venue, stale_cutoff_iso),
        ).rowcount
    return len(rows), other, stale


def deactivate_universe_assets_not_in_venue(active_venue: str) -> int:
    """Mark all registry rows from other venues as inactive."""
    db = get_db()
    was_in_tx = db.in_transaction
    result = execute_write(
        "UPDATE universe_registry SET status='inactive' WHERE venue != ? AND status='active'",
        (active_venue,),
    )
    if not was_in_tx:
        db.commit()
    return result.rowcount


def deactivate_stale_universe_assets(venue: str, cutoff_iso: str) -> int:
    """Mark registry rows for this venue not seen since cutoff as inactive."""
    db = get_db()
    was_in_tx = db.in_transaction
    result = execute_write(
        "UPDATE universe_registry SET status='inactive' WHERE venue = ? AND status='active' AND last_seen_at < ?",
        (venue, cutoff_iso),
    )
    if not was_in_tx:
        db.commit()
    return result.rowcount


def list_universe_assets(
    venue: str | None = None,
    active_only: bool = True,
    limit: int | None = 500,
) -> list[dict]:
    db = get_db()
    sql = "SELECT * FROM universe_registry"
    params: list = []
    conditions = []
    if venue:
        conditions.append("venue = ?")
        params.append(venue)
    if active_only:
        conditions.append("status = 'active'")
        conditions.append("is_blacklisted = 0")
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY last_seen_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def get_universe_asset(asset_id: str) -> dict | None:
    return _select_one("universe_registry", asset_id)


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


def _universe_twin_shadow_capture_due_from_latest(
    captured_at: str,
    latest_captured_at: str | None,
    *,
    min_interval_sec: int,
) -> bool:
    if latest_captured_at is None:
        return True
    candidate = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    latest = datetime.fromisoformat(latest_captured_at.replace("Z", "+00:00"))
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=timezone.utc)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return candidate - latest >= timedelta(seconds=max(0, min_interval_sec))


def universe_twin_shadow_capture_due(
    captured_at: str,
    *,
    min_interval_sec: int = 55 * 60,
) -> bool:
    """Return whether a compact diagnostic sample may be captured now."""

    row = get_db().execute(
        "SELECT captured_at FROM universe_twin_shadow_batches "
        "ORDER BY captured_at DESC LIMIT 1"
    ).fetchone()
    latest = str(row["captured_at"]) if row else None
    return _universe_twin_shadow_capture_due_from_latest(
        captured_at,
        latest,
        min_interval_sec=min_interval_sec,
    )


def relative_rank_shadow_capture_due(
    captured_at: str,
    *,
    min_interval_sec: int = 55 * 60,
) -> bool:
    row = get_db().execute(
        "SELECT captured_at FROM universe_relative_rank_shadow_batches "
        "ORDER BY captured_at DESC LIMIT 1"
    ).fetchone()
    return _universe_twin_shadow_capture_due_from_latest(
        captured_at,
        str(row["captured_at"]) if row else None,
        min_interval_sec=min_interval_sec,
    )


def _validate_universe_twin_prospective_bundle(
    batch: UniverseTwinShadowBatch,
    evidence: Sequence[UniverseTwinShadowEvidence],
    outcome_rows: Sequence[Mapping],
) -> None:
    """Reject partial or internally inconsistent prospective identities."""

    prospective_markers = (
        "experiment_id",
        "policy_epoch_id",
        "behavior_contract_hash",
        "assignment_unit_key",
        "assignment_contract_version",
        "assignment_arm",
        "assignment_propensity",
        "treatment_coefficient",
        "treatment_policy",
        "control_policy",
        "primary_horizon",
        "outcome_contract_version",
    )
    prospective = bool(outcome_rows) or any(
        getattr(batch, field) is not None for field in prospective_markers
    )
    if not prospective:
        return
    required_batch_fields = (
        "experiment_id",
        "policy_epoch_id",
        "behavior_contract_hash",
        "assignment_unit_key",
        "assignment_contract_version",
        "assignment_arm",
        "assignment_propensity",
        "treatment_coefficient",
        "treatment_policy",
        "control_policy",
        "primary_horizon",
        "outcome_contract_version",
        "metric_version",
    )
    if any(getattr(batch, field) is None for field in required_batch_fields):
        raise ValueError("universe_twin_shadow_experiment_identity_incomplete")
    if batch.assignment_unit_key != batch.scanner_run_id:
        raise ValueError("universe_twin_shadow_assignment_unit_mismatch")
    if batch.assignment_arm not in {"treatment", "control"}:
        raise ValueError("universe_twin_shadow_assignment_arm_invalid")
    if (
        batch.assignment_propensity is None
        or not 0 < batch.assignment_propensity <= 1
    ):
        raise ValueError("universe_twin_shadow_assignment_propensity_invalid")
    expected_coefficient = 1.0 if batch.assignment_arm == "treatment" else 0.0
    if batch.treatment_coefficient != expected_coefficient:
        raise ValueError("universe_twin_shadow_treatment_coefficient_mismatch")
    if batch.promotion_eligible:
        raise ValueError("universe_twin_shadow_promotion_not_allowed")
    if any(item.assigned_policy_selected is None for item in evidence):
        raise ValueError("universe_twin_shadow_assigned_selection_missing")
    selected_count = sum(bool(item.assigned_policy_selected) for item in evidence)
    if len(outcome_rows) != selected_count:
        raise ValueError("universe_twin_shadow_outcome_count_mismatch")

    record_identity_fields = (
        "experiment_id",
        "policy_epoch_id",
        "behavior_contract_hash",
        "availability_profile_id",
        "assignment_unit_key",
        "assignment_arm",
        "assignment_propensity",
        "treatment_coefficient",
        "primary_horizon",
        "outcome_contract_version",
        "metric_version",
    )
    for item in evidence:
        if any(
            getattr(item, field) != getattr(batch, field)
            for field in record_identity_fields
        ):
            raise ValueError("universe_twin_shadow_record_experiment_identity_mismatch")
        if item.shadow_authority_applied:
            raise ValueError("universe_twin_shadow_authority_not_allowed")
        if item.promotion_eligible:
            raise ValueError("universe_twin_shadow_promotion_not_allowed")


def insert_universe_twin_shadow_bundle(
    batch: UniverseTwinShadowBatch,
    evidence: Sequence[UniverseTwinShadowEvidence],
    *,
    outcomes: Sequence | None = None,
    min_interval_sec: int = 55 * 60,
) -> int:
    """Atomically persist one immutable, cadence-bounded diagnostic batch.

    An identical retry is idempotent.  Reusing an identity with different
    canonical content is rejected so replay drift cannot be silently hidden.
    A second scanner run inside the minimum interval is skipped in the same
    writer transaction, preventing concurrent refreshes from bypassing cadence.
    """

    batch_data = batch.model_dump()
    evidence_rows = [item.model_dump() for item in evidence]
    outcome_rows = [item.model_dump() for item in (outcomes or ())]
    if batch.candidate_count != len(evidence_rows):
        raise ValueError("universe_twin_shadow_candidate_count_mismatch")
    if any(item.scanner_run_id != batch.scanner_run_id for item in evidence):
        raise ValueError("universe_twin_shadow_scanner_run_mismatch")
    if sorted(item.candidate_index for item in evidence) != list(range(len(evidence))):
        raise ValueError("universe_twin_shadow_candidate_index_not_contiguous")
    if any(item.captured_at != batch.captured_at for item in evidence):
        raise ValueError("universe_twin_shadow_capture_time_mismatch")
    _validate_universe_twin_prospective_bundle(batch, evidence, outcome_rows)
    if batch.experiment_id is not None:
        outcome_by_key = {
            str(row["outcome_candidate_key"]): row for row in outcome_rows
        }
        if len(outcome_by_key) != len(outcome_rows):
            raise ValueError("universe_twin_shadow_duplicate_outcome_identity")
        for item in evidence:
            if not item.assigned_policy_selected:
                if item.outcome_candidate_key:
                    raise ValueError("universe_twin_shadow_unselected_outcome_not_allowed")
                continue
            if not item.outcome_candidate_key:
                raise ValueError("universe_twin_shadow_record_outcome_missing")
            row = outcome_by_key.get(item.outcome_candidate_key)
            if row is None:
                raise ValueError("universe_twin_shadow_record_outcome_mismatch")
            if row.get("horizon") != item.primary_horizon:
                raise ValueError("universe_twin_shadow_outcome_horizon_mismatch")
            if row.get("entry_ts") != batch.captured_at:
                raise ValueError("universe_twin_shadow_outcome_entry_time_mismatch")
            selected = row.get("allocation_selected")
            if (
                selected is None
                or item.assigned_policy_selected is None
                or bool(selected) != bool(item.assigned_policy_selected)
            ):
                raise ValueError("universe_twin_shadow_outcome_selection_mismatch")
            try:
                contract = json.loads(item.compact_output_json)["evidence_contract"]
                assigned_direction = str(contract["assigned_policy_direction"])
                assigned_family = contract["assigned_policy_family"]
            except (KeyError, TypeError, ValueError):
                raise ValueError(
                    "universe_twin_shadow_assigned_direction_missing"
                ) from None
            if row.get("direction") != assigned_direction:
                raise ValueError("universe_twin_shadow_outcome_direction_mismatch")
            if row.get("scanner_family") != assigned_family:
                raise ValueError("universe_twin_shadow_outcome_family_mismatch")
    db = get_db()
    with transaction(immediate=True):
        stored_batch = db.execute(
            "SELECT batch_hash FROM universe_twin_shadow_batches WHERE scanner_run_id=?",
            (batch.scanner_run_id,),
        ).fetchone()
        if stored_batch is not None:
            if stored_batch["batch_hash"] != batch.batch_hash:
                raise ValueError("universe_twin_shadow_batch_identity_conflict")
            _assert_twin_outcomes_match(outcome_rows)
            return 0

        latest = db.execute(
            "SELECT captured_at FROM universe_twin_shadow_batches "
            "ORDER BY captured_at DESC LIMIT 1"
        ).fetchone()
        if not _universe_twin_shadow_capture_due_from_latest(
            batch.captured_at,
            str(latest["captured_at"]) if latest else None,
            min_interval_sec=min_interval_sec,
        ):
            return 0

        columns = list(batch_data)
        execute_write(
            f"INSERT INTO universe_twin_shadow_batches "
            f"({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            [batch_data[column] for column in columns],
        )
        stored_batch = db.execute(
            "SELECT batch_hash FROM universe_twin_shadow_batches WHERE scanner_run_id=?",
            (batch.scanner_run_id,),
        ).fetchone()
        if stored_batch is None or stored_batch["batch_hash"] != batch.batch_hash:
            raise ValueError("universe_twin_shadow_batch_identity_conflict")

        inserted_evidence = 0
        if evidence_rows:
            record_columns = list(evidence_rows[0])
            before = db.total_changes
            execute_many_write(
                f"INSERT OR IGNORE INTO universe_twin_shadow_records "
                f"({', '.join(record_columns)}) VALUES "
                f"({', '.join('?' for _ in record_columns)})",
                [[row[column] for column in record_columns] for row in evidence_rows],
            )
            inserted_evidence = db.total_changes - before
            for item in evidence:
                stored = db.execute(
                    "SELECT record_hash FROM universe_twin_shadow_records WHERE id=?",
                    (item.id,),
                ).fetchone()
                if stored is None or stored["record_hash"] != item.record_hash:
                    raise ValueError("universe_twin_shadow_record_identity_conflict")
        if outcome_rows:
            _assert_twin_outcomes_match(outcome_rows, allow_missing=True)
            insert_filter_candidate_outcomes_batch(list(outcomes or ()))
            _assert_twin_outcomes_match(outcome_rows)
    return inserted_evidence


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


def _assert_twin_outcomes_match(
    expected_rows: Sequence[Mapping],
    *,
    allow_missing: bool = False,
) -> None:
    """Reject identity reuse while allowing legitimate later settlement fields."""

    db = get_db()
    for expected in expected_rows:
        stored = db.execute(
            "SELECT * FROM filter_candidate_outcomes WHERE id=?",
            (expected["id"],),
        ).fetchone()
        if stored is None:
            if allow_missing:
                continue
            raise ValueError("universe_twin_shadow_outcome_identity_conflict")
        if any(stored[field] != expected.get(field) for field in _TWIN_OUTCOME_IMMUTABLE_FIELDS):
            raise ValueError("universe_twin_shadow_outcome_identity_conflict")


def get_universe_twin_shadow_batch(scanner_run_id: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM universe_twin_shadow_batches WHERE scanner_run_id=?",
        (scanner_run_id,),
    ).fetchone()
    return dict(row) if row else None


def list_universe_twin_shadow_records(scanner_run_id: str) -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM universe_twin_shadow_records WHERE scanner_run_id=? "
        "ORDER BY candidate_index",
        (scanner_run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def insert_relative_rank_shadow_bundle(
    batch,
    *,
    outcomes: Sequence | None = None,
    min_interval_sec: int = 55 * 60,
) -> int:
    """Atomically persist one isolated relative-rank cluster and its outcomes."""

    batch_data = batch.model_dump()
    outcome_rows = [item.model_dump() for item in outcomes or ()]
    try:
        records = json.loads(batch.records_json)
    except (TypeError, ValueError):
        raise ValueError("relative_rank_shadow_records_invalid") from None
    if not isinstance(records, list) or len(records) != batch.candidate_count:
        raise ValueError("relative_rank_shadow_candidate_count_mismatch")
    if batch.assignment_unit_key != batch.scanner_run_id:
        raise ValueError("relative_rank_shadow_assignment_unit_mismatch")
    if batch.promotion_eligible:
        raise ValueError("relative_rank_shadow_promotion_not_allowed")
    selected_keys = {
        str(record.get("outcome_candidate_key"))
        for record in records
        if record.get("assigned_policy_selected")
    }
    if "None" in selected_keys or len(selected_keys) != len(outcome_rows):
        raise ValueError("relative_rank_shadow_outcome_count_mismatch")
    if selected_keys != {
        str(row.get("outcome_candidate_key")) for row in outcome_rows
    }:
        raise ValueError("relative_rank_shadow_outcome_identity_mismatch")

    db = get_db()
    with transaction(immediate=True):
        stored = db.execute(
            "SELECT batch_hash FROM universe_relative_rank_shadow_batches "
            "WHERE scanner_run_id=?",
            (batch.scanner_run_id,),
        ).fetchone()
        if stored is not None:
            if stored["batch_hash"] != batch.batch_hash:
                raise ValueError("relative_rank_shadow_batch_identity_conflict")
            _assert_twin_outcomes_match(outcome_rows)
            return 0
        latest = db.execute(
            "SELECT captured_at FROM universe_relative_rank_shadow_batches "
            "ORDER BY captured_at DESC LIMIT 1"
        ).fetchone()
        if not _universe_twin_shadow_capture_due_from_latest(
            batch.captured_at,
            str(latest["captured_at"]) if latest else None,
            min_interval_sec=min_interval_sec,
        ):
            return 0
        columns = list(batch_data)
        execute_write(
            "INSERT INTO universe_relative_rank_shadow_batches "
            f"({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            [batch_data[column] for column in columns],
        )
        if outcome_rows:
            _assert_twin_outcomes_match(outcome_rows, allow_missing=True)
            insert_filter_candidate_outcomes_batch(list(outcomes or ()))
            _assert_twin_outcomes_match(outcome_rows)
    return 1


def list_relative_rank_shadow_batches(policy_epoch_id: str) -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM universe_relative_rank_shadow_batches "
        "WHERE policy_epoch_id=? ORDER BY captured_at",
        (policy_epoch_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def insert_universe_snapshot(snap: UniverseSnapshot) -> None:
    _insert("universe_snapshots", snap.model_dump())


def get_active_universe_snapshot(market_type: str = "crypto") -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM universe_snapshots WHERE status = 'active' AND market_type = ? ORDER BY created_at DESC LIMIT 1",
        (market_type,),
    ).fetchone()
    return dict(row) if row else None


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
            "AND s.market_type=? AND s.status='superseded' "
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
            "WHERE status = 'active' AND market_type = ? AND id != ?",
            (market_type, except_id),
        )
    else:
        execute_write(
            "UPDATE universe_snapshots SET status = 'superseded' WHERE status = 'active' AND market_type = ?",
            (market_type,),
        )
    if not was_in_tx:
        db.commit()


def insert_universe_member(member: UniverseMember) -> None:
    _insert("universe_members", member.model_dump())


def insert_universe_members_batch(members: list[UniverseMember]) -> int:
    if not members:
        return 0
    return _insert_many(
        "universe_members",
        [member.model_dump() for member in members],
    )


def list_universe_members(snapshot_id: str, limit: int = 50) -> list[dict]:
    return _select_all(
        "universe_members",
        where="snapshot_id = ?",
        params=(snapshot_id,),
        order="rank ASC",
        limit=limit,
    )


def refresh_active_snapshot_ttls(snapshot_id: str, now: datetime) -> tuple[int, int]:
    """Slide expires_at forward on every member of a reused snapshot, and mirror
    onto linked open or queued opportunities' valid_until.

    Used by universe refresh's change-suppression branch (service._atomic_universe_publication
    is skipped when the allocation fingerprint is unchanged). Without this slide,
    the members' original expiry — `member.created_at + ttl_sec` — lapses even
    though the allocation is still confirmed valid. The subsequent ranker scan
    then hard-rejects every carrier with skip_reason=ttl_expired.

    Only slides forward (`WHERE expires_at < new_expires_at`) so a legitimate
    longer expiry set elsewhere is never shortened. Queued rows are included
    because reuse of the same active source snapshot is explicit revalidation;
    expired/reviewed rows remain terminal.

    Returns (members_updated, opportunities_updated).
    """
    db = get_db()
    was_in_tx = db.in_transaction

    members = _select_all(
        "universe_members",
        where="snapshot_id = ?",
        params=(snapshot_id,),
        limit=1000,
    )

    n_members = 0
    n_opps = 0

    def _apply() -> None:
        nonlocal n_members, n_opps
        for m in members:
            ttl_sec = int(m.get("ttl_sec") or 0)
            if ttl_sec <= 0:
                continue
            new_expires = (now + timedelta(seconds=ttl_sec)).isoformat()
            cur_m = execute_write(
                "UPDATE universe_members SET expires_at = ? WHERE id = ? AND (expires_at IS NULL OR expires_at < ?)",
                (new_expires, m["id"], new_expires),
            )
            if cur_m.rowcount and cur_m.rowcount > 0:
                n_members += cur_m.rowcount
            cur_o = execute_write(
                "UPDATE opportunities SET valid_until = ? "
                "WHERE universe_member_id = ? AND status IN ('open','queued') "
                "AND (valid_until IS NULL OR valid_until < ?)",
                (new_expires, m["id"], new_expires),
            )
            if cur_o.rowcount and cur_o.rowcount > 0:
                n_opps += cur_o.rowcount

    if was_in_tx:
        _apply()
    else:
        with transaction(immediate=True):
            _apply()

    return n_members, n_opps


def get_universe_member(member_id: str) -> dict | None:
    return _select_one("universe_members", member_id)


def get_active_universe_members(limit: int = 20) -> list[dict]:
    """Return top-ranked members from the currently active universe snapshot."""
    snap = get_active_universe_snapshot()
    if not snap:
        return []
    return list_universe_members(snap["id"], limit=limit)


def get_latest_feature_snapshot(asset_id: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM universe_feature_snapshots WHERE asset_id = ? ORDER BY ts DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    return dict(row) if row else None


def list_opportunities_by_member(member_id: str, limit: int = 5) -> list[dict]:
    return _select_all(
        "opportunities",
        where="universe_member_id = ?",
        params=(member_id,),
        order="created_at DESC",
        limit=limit,
    )


def insert_universe_policy(policy: UniversePolicy) -> None:
    _insert("universe_policies", policy.model_dump())


def get_active_universe_policy(market_type: str = "crypto") -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM universe_policies WHERE active = 1 AND market_type = ? ORDER BY created_at DESC LIMIT 1",
        (market_type,),
    ).fetchone()
    return dict(row) if row else None


def list_universe_snapshots(market_type: str = "crypto", limit: int = 20) -> list[dict]:
    return _select_all(
        "universe_snapshots",
        where="market_type = ?",
        params=(market_type,),
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


def insert_forward_outcome(outcome) -> None:
    _insert("universe_forward_outcomes", outcome.model_dump())


def insert_forward_outcomes_batch(outcomes: list) -> None:
    if not outcomes:
        return
    with transaction():
        _insert_many(
            "universe_forward_outcomes",
            [outcome.model_dump() for outcome in outcomes],
        )


def list_pending_forward_outcomes(before_ts: str, limit: int = 500) -> list[dict]:
    return _select_all(
        "universe_forward_outcomes",
        where="status = 'pending' AND due_at <= ?",
        params=(before_ts,),
        order="due_at ASC",
        limit=limit,
    )


def settle_forward_outcome(
    outcome_id: str,
    exit_price: float,
    forward_return: float,
    settled_at: str,
    *,
    economics: dict | None = None,
    settlement_price_source: str | None = None,
    settlement_lag_sec: float | None = None,
) -> None:
    updates = {
        "exit_price_usd": exit_price,
        "forward_return_pct": forward_return,
        "settled_at": settled_at,
        "status": "settled",
    }
    if economics:
        updates.update(economics)
    if settlement_price_source is not None:
        updates["settlement_price_source"] = settlement_price_source
    if settlement_lag_sec is not None:
        updates["settlement_lag_sec"] = settlement_lag_sec
    _update("universe_forward_outcomes", outcome_id, updates)


def fail_forward_outcome(
    outcome_id: str,
    settled_at: str,
    *,
    exit_price: float | None = None,
    settlement_price_source: str | None = None,
) -> None:
    """Record a terminal "not measurable" verdict for one forward row.

    Pass ``exit_price``/``settlement_price_source`` whenever the venue DID answer
    and the answer was rejected — a >10x deviation from entry, which is how
    cross-symbol contamination presents. Recording the rejected observation makes
    the row say what was seen, and leaves NULL meaning exactly one thing: no
    observation was ever taken.

    ``maintenance.settlement_requeue`` depends on that distinction to separate
    evidence destroyed by an unreachable venue from a genuine verdict, so never
    fail a row with a price in hand without passing it. Neither column is read by
    any consumer of a failed row: those all gate on ``net_return_pct IS NOT NULL``
    (outcome contract §8), which a failed row never has.
    """
    updates: dict[str, object] = {
        "settled_at": settled_at,
        "status": "failed",
    }
    if exit_price is not None:
        updates["exit_price_usd"] = exit_price
    if settlement_price_source is not None:
        updates["settlement_price_source"] = settlement_price_source
    _update("universe_forward_outcomes", outcome_id, updates)


def cancel_all_pending_forward_outcomes() -> int:
    """Batch-cancel all pending forward outcomes (used on venue/strategy switch)."""
    db = get_db()
    was_in_tx = db.in_transaction
    result = execute_write(
        "UPDATE universe_forward_outcomes SET status = 'cancelled' WHERE status = 'pending'",
    )
    if not was_in_tx:
        db.commit()
    return result.rowcount


def list_forward_outcomes_by_member(member_id: str) -> list[dict]:
    return _select_all(
        "universe_forward_outcomes",
        where="member_id = ?",
        params=(member_id,),
        order="horizon ASC",
    )


# ---------------------------------------------------------------------------
# Opportunity filter evidence
# ---------------------------------------------------------------------------


def insert_filter_decision(decision) -> bool:
    """Insert one immutable filter fact; duplicate dedup keys are ignored."""
    data = decision.model_dump()
    db = get_db()
    was_in_tx = db.in_transaction
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    cur = execute_write(
        f"INSERT OR IGNORE INTO filter_decisions ({cols}) VALUES ({placeholders})",
        list(data.values()),
    )
    if not was_in_tx:
        db.commit()
    return bool(cur.rowcount)


def insert_filter_decision_batch(batch) -> bool:
    """Insert one immutable compressed filter batch."""
    data = batch.model_dump()
    db = get_db()
    was_in_tx = db.in_transaction
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    cur = execute_write(
        f"INSERT OR IGNORE INTO filter_decision_batches ({cols}) VALUES ({placeholders})",
        list(data.values()),
    )
    if not was_in_tx:
        db.commit()
    return bool(cur.rowcount)


def insert_filter_candidate_outcomes_batch(outcomes: list) -> int:
    """Register fixed-horizon candidate outcomes without duplicating a candidate.

    Uses an upsert so that a later call carrying a definitive allocation_selected
    (True or False) can update a row that was first written with NULL by the filter
    evidence path. COALESCE(excluded.allocation_selected, existing) ensures an
    explicit True/False always wins, while a NULL incoming value never overwrites
    an already-resolved value.
    """
    if not outcomes:
        return 0
    rows = [outcome.model_dump() for outcome in outcomes]
    columns = list(rows[0])
    placeholders = ", ".join("?" for _ in columns)
    db = get_db()
    before = db.total_changes
    with transaction():
        execute_many_write(
            f"INSERT INTO filter_candidate_outcomes ({', '.join(columns)}) VALUES ({placeholders})"
            " ON CONFLICT(id) DO UPDATE SET"
            " allocation_selected = COALESCE(excluded.allocation_selected,"
            "                                filter_candidate_outcomes.allocation_selected)",
            [[row[column] for column in columns] for row in rows],
        )
    return db.total_changes - before


def list_pending_filter_candidate_outcomes(
    before_ts: str,
    limit: int = 200,
) -> list[dict]:
    return _select_all(
        "filter_candidate_outcomes",
        where="status = 'pending' AND due_at <= ?",
        params=(before_ts,),
        order="due_at ASC",
        limit=limit,
    )


def list_open_learning_outcome_candidate_keys(
    path_ids: tuple[str, ...],
    *,
    behavior_contract_hash: str | None = None,
) -> set[str]:
    """Return exact outcome keys attached to active experiments in open epochs.

    When ``behavior_contract_hash`` is supplied, restrict the keys to the
    current measured behavior so settlement can prioritize usable evidence
    without treating unrelated builds as different cohorts.
    """
    if not path_ids:
        return set()
    placeholders = ", ".join("?" for _ in path_ids)
    behavior_clause = (
        " AND le.behavior_contract_hash=?" if behavior_contract_hash else ""
    )
    parameters: tuple[object, ...] = (
        (*path_ids, behavior_contract_hash)
        if behavior_contract_hash
        else path_ids
    )
    rows = get_db().execute(
        "SELECT DISTINCT le.outcome_candidate_key "
        "FROM learning_exposures le "
        "JOIN learning_experiments e "
        "  ON e.experiment_id=le.experiment_id AND e.status='active' "
        "JOIN learning_hypotheses h ON h.hypothesis_id=le.hypothesis_id "
        "JOIN learning_policy_epochs pe "
        "  ON pe.epoch_id=le.concurrent_policy_epoch AND pe.ended_at IS NULL "
        f"WHERE h.path_id IN ({placeholders}) "
        "AND pe.epoch_kind=('decision_policy:' || h.path_id) "
        "AND le.outcome_candidate_key IS NOT NULL"
        f"{behavior_clause}",
        parameters,
    ).fetchall()
    return {
        str(row["outcome_candidate_key"])
        for row in rows
        if row["outcome_candidate_key"]
    }


def list_open_universe_twin_outcome_candidate_keys() -> set[str]:
    """Return exact horizon-qualified keys for unsettled Task-6 outcomes.

    Twin comparison outcomes are deliberately not Learning exposures, so the
    ordinary experiment join cannot discover them. A policy rotation must not
    strand the closing epoch's final 4h rows behind a historical canonical
    backlog, so pending outcome state—not open-epoch state—is authoritative.
    """

    rows = get_db().execute(
        "SELECT DISTINCT r.outcome_candidate_key, r.primary_horizon "
        "FROM universe_twin_shadow_records r "
        "JOIN universe_twin_shadow_batches b "
        "  ON b.scanner_run_id=r.scanner_run_id "
        "JOIN filter_candidate_outcomes o "
        "  ON o.outcome_candidate_key=r.outcome_candidate_key "
        " AND o.horizon=r.primary_horizon "
        "WHERE b.experiment_id IS NOT NULL "
        "AND b.policy_epoch_id IS NOT NULL "
        "AND o.status='pending' "
        "AND r.outcome_candidate_key IS NOT NULL "
        "AND r.primary_horizon IS NOT NULL"
    ).fetchall()
    return {
        f"{row['outcome_candidate_key']}:{row['primary_horizon']}"
        for row in rows
    }


_PRIORITY_SCAN_BATCH_ROWS = 2000
# The open-experiment keys attach to RECENT candidates, so when a backlog exists the
# priority rows sit at the new end of the due range and a short scan finds none of
# them (measured: 0 matches in the oldest 60k of a 193k-row backlog). The cap must
# therefore admit a whole realistic backlog; the caller's deadline is the real guard.
_PRIORITY_SCAN_CAP_ROWS = 400000


def list_pending_filter_candidate_outcomes_for_keys(
    before_ts: str,
    outcome_candidate_keys: set[str],
    limit: int = 200,
    *,
    deadline: float | None = None,
) -> tuple[list[dict], bool]:
    """Return (due canonical outcomes for exact Learning keys, truncated).

    Cost scales with ``limit``, NOT with the size of ``outcome_candidate_keys``.
    It used to be the other way around: the key set was chunked 400 at a time into
    ``outcome_candidate_key IN (...)`` queries, each of which fetched *all* its
    matches before a final sort truncated to ``limit``. With ~34k open-experiment
    keys and ~190k due rows that measured 33s — more than the caller's entire
    settlement budget — so the settlement loop never ran an iteration. It returned
    0 without raising and without logging, and canonical settlement stopped dead
    for hours while the forward mirror kept draining normally.

    Adding ``ORDER BY due_at LIMIT`` per chunk did NOT fix it (measured 36s): each
    chunk's matches are spread across the whole due range, so SQLite still had to
    walk almost all of it before the limit could stop it. The direction is
    therefore inverted — walk the cheap ``(status, due_at)`` index in due order
    and test key membership in Python. That reads only as far as it needs to
    collect ``limit`` rows, and yields the most-overdue priority rows first
    instead of an arbitrary chunk's worth.

    ``deadline`` is a ``time.monotonic()`` value. ``truncated`` is True when the
    walk stopped on the deadline or the scan cap, so the caller can say so instead
    of presenting a partial priority set as the complete one.
    """
    if not outcome_candidate_keys or limit <= 0:
        return [], False

    wanted: set[tuple[str, str]] = set()
    for key in outcome_candidate_keys:
        base, separator, horizon = str(key).rpartition(":")
        if separator and horizon in {"15m", "1h", "4h", "24h"}:
            wanted.add((base, horizon))
    if not wanted:
        return [], False

    db = get_db()
    matched_ids: list[str] = []
    scanned = 0
    truncated = False
    # Keyset pagination over (due_at, id) so each batch is an ordered index range
    # read rather than a growing OFFSET scan. Only the four columns needed to test
    # membership are read here; full rows are fetched for matches alone.
    last_due = ""
    last_id = ""
    while len(matched_ids) < limit:
        if deadline is not None and time.monotonic() >= deadline:
            truncated = True
            break
        if scanned >= _PRIORITY_SCAN_CAP_ROWS:
            truncated = True
            break
        rows = db.execute(
            "SELECT id, outcome_candidate_key, horizon, due_at "
            "FROM filter_candidate_outcomes "
            "WHERE status='pending' AND due_at <= ? "
            "AND (due_at > ? OR (due_at = ? AND id > ?)) "
            "ORDER BY due_at ASC, id ASC LIMIT ?",
            (before_ts, last_due, last_due, last_id, _PRIORITY_SCAN_BATCH_ROWS),
        ).fetchall()
        if not rows:
            break
        for row in rows:
            if (str(row["outcome_candidate_key"]), str(row["horizon"])) in wanted:
                matched_ids.append(str(row["id"]))
                if len(matched_ids) >= limit:
                    break
        scanned += len(rows)
        last_due = str(rows[-1]["due_at"])
        last_id = str(rows[-1]["id"])
        if len(rows) < _PRIORITY_SCAN_BATCH_ROWS:
            break  # reached the end of the due range

    if not matched_ids:
        return [], truncated

    by_id: dict[str, dict] = {}
    for offset in range(0, len(matched_ids), 400):
        chunk = matched_ids[offset:offset + 400]
        placeholders = ", ".join("?" for _ in chunk)
        for row in db.execute(
            f"SELECT * FROM filter_candidate_outcomes WHERE id IN ({placeholders})",
            tuple(chunk),
        ).fetchall():
            by_id[str(row["id"])] = dict(row)

    # Preserve the due_at order the scan established.
    return [by_id[i] for i in matched_ids if i in by_id], truncated


def settle_filter_candidate_outcome(
    outcome_id: str,
    *,
    exit_price: float,
    settled_at: str,
    economics: dict,
    settlement_price_source: str | None,
    settlement_lag_sec: float | None,
) -> None:
    updates = {
        "exit_price_usd": exit_price,
        "settled_at": settled_at,
        "status": "settled",
        **economics,
    }
    if settlement_price_source is not None:
        updates["settlement_price_source"] = settlement_price_source
    if settlement_lag_sec is not None:
        updates["settlement_lag_sec"] = settlement_lag_sec
    _update("filter_candidate_outcomes", outcome_id, updates)


def fail_filter_candidate_outcome(
    outcome_id: str,
    settled_at: str,
    *,
    exit_price: float | None = None,
    settlement_price_source: str | None = None,
) -> None:
    """Record a terminal "not measurable" verdict for one canonical row.

    See :func:`fail_forward_outcome` for why a rejected observation is recorded
    rather than dropped: NULL on both columns is the marker that no observation
    was ever taken, which is what ``maintenance.settlement_requeue`` reclaims.
    """
    updates: dict[str, object] = {
        "settled_at": settled_at,
        "status": "failed",
    }
    if exit_price is not None:
        updates["exit_price_usd"] = exit_price
    if settlement_price_source is not None:
        updates["settlement_price_source"] = settlement_price_source
    _update("filter_candidate_outcomes", outcome_id, updates)


def settle_linked_shadow_outcomes(
    outcome_candidate_key: str,
    *,
    exit_price: float,
    settled_at: str,
    economics: dict,
    settlement_price_source: str | None,
    settlement_lag_sec: float | None,
) -> int:
    """Project a settled shared 24h result onto linked shadow rows."""
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write(
        "UPDATE shadow_trades SET exit_price=?, pnl_pct=?, gross_return_pct=?, "
        "net_pnl_pct=?, estimated_fee_pct=?, estimated_spread_slippage_pct=?, "
        "estimated_funding_pct=?, min_tradable_edge_pct=?, "
        "settlement_price_source=?, settlement_lag_sec=?, cost_model_version=?, "
        "resolved_at=? WHERE outcome_candidate_key=? AND resolved_at IS NULL",
        (
            exit_price,
            economics.get("directional_return_pct"),
            economics.get("gross_return_pct"),
            economics.get("net_return_pct"),
            economics.get("estimated_fee_pct"),
            economics.get("estimated_spread_slippage_pct"),
            economics.get("estimated_funding_pct"),
            economics.get("min_tradable_edge_pct"),
            settlement_price_source,
            settlement_lag_sec,
            economics.get("cost_model_version"),
            settled_at,
            outcome_candidate_key,
        ),
    )
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def mark_outlier_outcomes_failed(max_abs_return_pct: float = 1_000_000) -> int:
    """Mark settled rows with physically impossible returns as failed.

    Catches cross-symbol price contamination (e.g. BTC's price applied to PEPE
    when ccxt returns wrong data without raising). Idempotent — safe to call on
    every refresh; after the first pass it finds 0 rows and returns immediately.
    """
    db = get_db()
    result = execute_write(
        "UPDATE universe_forward_outcomes "
        "SET status = 'failed' "
        "WHERE status = 'settled' "
        "AND (ABS(forward_return_pct) > ? OR ABS(net_return_pct) > ?)",
        (max_abs_return_pct, max_abs_return_pct),
    )
    return result.rowcount


# --- Filter feedback controller (shadow recommendations) ---


def insert_controller_recommendation(rec: dict) -> None:
    """Append one shadow recommendation row. Never updates existing rows."""
    _insert("controller_recommendations", rec)


def list_controller_recommendations(limit: int = 200, run_id: str | None = None) -> list[dict]:
    """Most recent recommendations, optionally scoped to one observe run."""
    if run_id:
        return _select_all(
            "controller_recommendations",
            where="run_id = ?",
            params=(run_id,),
            order="created_at DESC",
            limit=limit,
        )
    return _select_all("controller_recommendations", order="created_at DESC", limit=limit)


def latest_controller_run_id() -> str | None:
    """run_id of the most recent observe cycle, or None if the table is empty."""
    db = get_db()
    row = db.execute(
        "SELECT run_id FROM controller_recommendations WHERE run_id IS NOT NULL ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return row["run_id"] if row else None


def insert_filter_shadow_experiment(experiment: dict) -> bool:
    """Insert an immutable prospective plan, returning False for a duplicate key."""
    db = get_db()
    was_in_tx = db.in_transaction
    cols = ", ".join(experiment.keys())
    placeholders = ", ".join(["?"] * len(experiment))
    cursor = execute_write(
        f"INSERT OR IGNORE INTO filter_shadow_experiments ({cols}) VALUES ({placeholders})",
        list(experiment.values()),
    )
    if not was_in_tx:
        db.commit()
    return bool(cursor.rowcount)


def list_filter_shadow_experiments(*, status: str | None = None, limit: int = 100) -> list[dict]:
    if status:
        return _select_all(
            "filter_shadow_experiments",
            where="status = ?",
            params=(status,),
            order="started_at DESC",
            limit=limit,
        )
    return _select_all("filter_shadow_experiments", order="started_at DESC", limit=limit)


def update_filter_shadow_experiment(experiment_id: str, **values) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    sets = ", ".join(f"{key} = ?" for key in values)
    execute_write(
        f"UPDATE filter_shadow_experiments SET {sets} WHERE id = ?",
        [*values.values(), experiment_id],
    )
    if not was_in_tx:
        db.commit()


def insert_filter_shadow_evaluation(evaluation: dict) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    _insert("filter_shadow_evaluations", evaluation)
    if not was_in_tx:
        db.commit()


def latest_filter_shadow_evaluation(experiment_id: str) -> dict | None:
    row = (
        get_db()
        .execute(
            "SELECT * FROM filter_shadow_evaluations WHERE experiment_id=? ORDER BY created_at DESC LIMIT 1",
            (experiment_id,),
        )
        .fetchone()
    )
    return dict(row) if row else None


def calibration_stats() -> list[dict]:
    """Aggregate cost-aware returns by family + direction + market + horizon.

    Legacy gross-only rows are intentionally excluded. A win must clear the
    persisted minimum tradable edge after fees, spread/slippage and funding.
    """
    from aletheia.analytics.learning_window import clean_window_cutoff

    db = get_db()
    cutoff = clean_window_cutoff("1970-01-01T00:00:00+00:00")
    rows = db.execute(
        "SELECT scanner_family, direction, "
        f"{_OUTCOME_MARKET_SQL} AS market_type, horizon, "
        "COUNT(*) as n, "
        "AVG(net_return_pct) as avg_return, "
        "SUM(CASE WHEN net_return_pct > COALESCE(min_tradable_edge_pct, 0) "
        "  THEN 1 ELSE 0 END) as win_count, "
        "MIN(net_return_pct) as min_return, "
        "MAX(net_return_pct) as max_return "
        "FROM universe_forward_outcomes "
        "WHERE status = 'settled' "
        "AND entry_ts >= ? "
        "AND net_return_pct IS NOT NULL "
        "AND cost_model_version IS NOT NULL "
        "AND ABS(net_return_pct) <= 100 "
        "AND direction IN ('long', 'short') "
        f"GROUP BY scanner_family, direction, {_OUTCOME_MARKET_SQL}, horizon "
        "ORDER BY scanner_family, direction, market_type, horizon",
        (cutoff,),
    ).fetchall()
    lineage = _calibration_source_lineage(db, cutoff)
    result = []
    for raw in rows:
        row = dict(raw)
        key = (
            row.get("scanner_family"), row.get("direction"),
            row.get("market_type"), row.get("horizon"),
        )
        source = lineage.get(key)
        row["source_outcome_fingerprint"] = (
            source["fingerprint"] if source else None
        )
        row["source_window_start"] = cutoff
        result.append(row)
    return result


def _calibration_source_lineage(db, cutoff: str) -> dict[tuple, dict]:
    """Hash every exact row consumed by ``calibration_stats`` without retaining it."""
    cursor = db.execute(
        "SELECT scanner_family, direction, "
        f"{_OUTCOME_MARKET_SQL} AS market_type, horizon, id, "
        "outcome_candidate_key, member_id, entry_ts, settled_at, net_return_pct, "
        "min_tradable_edge_pct, cost_model_version "
        "FROM universe_forward_outcomes "
        "WHERE status = 'settled' "
        "AND entry_ts >= ? "
        "AND net_return_pct IS NOT NULL "
        "AND cost_model_version IS NOT NULL "
        "AND ABS(net_return_pct) <= 100 "
        "AND direction IN ('long', 'short') "
        "ORDER BY scanner_family, direction, market_type, horizon, id",
        (cutoff,),
    )
    hashers: dict[tuple, object] = {}
    counts: dict[tuple, int] = {}
    for raw in cursor:
        row = dict(raw)
        key = (
            row.pop("scanner_family"), row.pop("direction"),
            row.pop("market_type"), row.pop("horizon"),
        )
        hasher = hashers.setdefault(key, hashlib.sha256())
        canonical = json.dumps(row, sort_keys=True, separators=(",", ":"))
        hasher.update(canonical.encode("utf-8"))
        hasher.update(b"\n")
        counts[key] = counts.get(key, 0) + 1
    return {
        key: {"fingerprint": hasher.hexdigest(), "count": counts[key]}
        for key, hasher in hashers.items()
    }


def filter_pressure_stats() -> list[dict]:
    """Aggregate cost-aware returns for filter-rejected candidates where scanner_family is known.

    These rows come from risk/PI filter stages that run after scanner assignment, so
    scanner_family is populated. Returns per-family miss rates: how often do this
    family's rejected candidates produce positive net returns?
    """
    from aletheia.analytics.learning_window import clean_window_cutoff

    db = get_db()
    cutoff = clean_window_cutoff("1970-01-01T00:00:00+00:00")
    rows = db.execute(
        "SELECT scanner_family, direction, "
        f"{_OUTCOME_MARKET_SQL} AS market_type, horizon, "
        "COUNT(*) AS n, "
        "AVG(net_return_pct) AS avg_net, "
        "MIN(net_return_pct) AS min_net, "
        "MAX(net_return_pct) AS max_net, "
        "AVG(CASE WHEN net_return_pct > COALESCE(min_tradable_edge_pct, 0) "
        "  THEN 1.0 ELSE 0.0 END) AS miss_rate "
        "FROM filter_candidate_outcomes "
        "WHERE status = 'settled' "
        "AND scanner_family IS NOT NULL "
        "AND cost_model_version IS NOT NULL "
        "AND net_return_pct IS NOT NULL "
        "AND ABS(net_return_pct) <= 100 "
        "AND direction IN ('long', 'short') "
        "AND entry_ts >= ? "
        f"GROUP BY scanner_family, direction, {_OUTCOME_MARKET_SQL}, horizon "
        "ORDER BY scanner_family, direction, market_type, horizon",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def family_spread_tolerance_stats() -> list[dict]:
    """Per-family spread sensitivity from settled universe forward outcomes.

    Splits outcomes into low-spread (<=15 bps) and high-spread (>15 bps) buckets to
    measure whether a family retains positive net expectancy at higher spreads. Used
    by filter_advisor to compute per-family spread tolerance multipliers.
    """
    from aletheia.analytics.learning_window import clean_window_cutoff

    db = get_db()
    cutoff = clean_window_cutoff("1970-01-01T00:00:00+00:00")
    rows = db.execute(
        "SELECT scanner_family, direction, "
        f"{_OUTCOME_MARKET_SQL} AS market_type, horizon, "
        "COUNT(*) AS n, "
        "AVG(net_return_pct) AS avg_net, "
        "MIN(net_return_pct) AS min_net, "
        "COUNT(CASE WHEN entry_spread_bps <= 15 THEN 1 END) AS n_low_spread, "
        "AVG(CASE WHEN entry_spread_bps <= 15 THEN net_return_pct END) AS avg_net_low_spread, "
        "COUNT(CASE WHEN entry_spread_bps > 15 THEN 1 END) AS n_high_spread, "
        "AVG(CASE WHEN entry_spread_bps > 15 THEN net_return_pct END) AS avg_net_high_spread "
        "FROM universe_forward_outcomes "
        "WHERE status = 'settled' "
        "AND scanner_family IS NOT NULL "
        "AND entry_spread_bps IS NOT NULL "
        "AND cost_model_version IS NOT NULL "
        "AND net_return_pct IS NOT NULL "
        "AND ABS(net_return_pct) <= 100 "
        "AND direction IN ('long', 'short') "
        "AND entry_ts >= ? "
        f"GROUP BY scanner_family, direction, {_OUTCOME_MARKET_SQL}, horizon "
        "ORDER BY scanner_family, direction, market_type, horizon",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def family_direction_edge(horizon: str = "24h", within_days: int = 7) -> list[dict]:
    """Per-(scanner_family, direction, market_type) cost-aware edge.

    Thesis evidence for the System Operator. Like calibration_stats(), this keeps
    long/short and spot/perpetual evidence separate; unlike calibration_stats(),
    it selects one horizon and recent time window for decision-time context.
    Reuses the existing universe_forward_outcomes store and excludes legacy
    gross-only rows.

    Returns rows: {scanner_family, direction, market_type, n, win_rate, avg_return}.
    Only families with n >= 1 in the window are returned.
    """
    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
    rows = db.execute(
        "SELECT scanner_family, direction, "
        f"{_OUTCOME_MARKET_SQL} AS market_type, "
        "COUNT(*) as n, "
        "AVG(net_return_pct) as avg_return, "
        "SUM(CASE WHEN net_return_pct > COALESCE(min_tradable_edge_pct, 0) "
        "  THEN 1 ELSE 0 END) as win_count "
        "FROM universe_forward_outcomes "
        "WHERE status = 'settled' AND horizon = ? "
        "AND settled_at >= ? AND net_return_pct IS NOT NULL "
        "AND ABS(net_return_pct) <= 100 "
        "AND scanner_family IS NOT NULL "
        "AND direction IN ('long', 'short') "
        f"GROUP BY scanner_family, direction, {_OUTCOME_MARKET_SQL} "
        "ORDER BY n DESC",
        (horizon, cutoff),
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
# exchange) is identical to the incoming payload AND younger than the
# heartbeat floor, skip the INSERT. Once the newest row crosses the floor,
# the next INSERT lands regardless — the doctor probe reads MAX(observed_at)
# so a stale-newest would trip freshness thresholds.
_FUNDING_DEDUP_FIELDS = (
    "funding_rate",
    "funding_rate_annualized",
    "next_funding_time",
    "mark_price",
)
_FUNDING_HEARTBEAT_FLOOR_MINUTES = 10
# Must be strictly less than local_funding._MAX_OBSERVATION_GAP (15 min) minus the
# collection interval (2 min), so a stable-rate symbol always has at least one
# snapshot inside the settlement verification window even when the rate never changes.


def insert_funding_rate_snapshot(data: dict) -> None:
    """Insert a funding_rate_snapshots row, skipping identical repeats.

    Guard (Phase 3): if the newest row for the same (symbol, exchange) has
    the same funding_rate / annualized / next_funding_time / mark_price AND
    its ``observed_at`` is younger than ``_FUNDING_HEARTBEAT_FLOOR_MINUTES``
    minutes ago, the insert is skipped. This is idempotent — collectors that
    poll on a 2-minute cadence stop writing 5× duplicate rows per 10-minute
    window while still emitting one heartbeat row every ~10 minutes so both
    freshness probes and settlement-window checks stay green.

    Settlement guard: if ``next_funding_time`` is within the heartbeat floor
    window from now, the dedup is bypassed and the row is always written.
    This guarantees a snapshot exists inside local_funding's observation gap
    even when the funding rate has been stable for an extended period.
    """
    from aletheia.storage.models import new_id

    if "id" not in data:
        data["id"] = new_id()
    symbol = data.get("symbol")
    exchange = data.get("exchange")
    if symbol and exchange is not None:
        # Settlement guard: always write when settlement is imminent so that
        # local_funding._verify_funding_settlements can find a snapshot within
        # its _MAX_OBSERVATION_GAP window.
        settlement_imminent = False
        nft_raw = data.get("next_funding_time")
        if nft_raw:
            try:
                nft = datetime.fromisoformat(str(nft_raw))
                if nft.tzinfo is None:
                    nft = nft.replace(tzinfo=timezone.utc)
                time_to_settlement = nft - datetime.now(timezone.utc)
                if timedelta(0) <= time_to_settlement <= timedelta(minutes=_FUNDING_HEARTBEAT_FLOOR_MINUTES):
                    settlement_imminent = True
            except (TypeError, ValueError):
                pass
        if not settlement_imminent:
            db = get_db()
            row = db.execute(
                "SELECT funding_rate, funding_rate_annualized, next_funding_time, "
                "mark_price, observed_at "
                "FROM funding_rate_snapshots "
                "WHERE symbol = ? AND exchange = ? "
                "ORDER BY observed_at DESC LIMIT 1",
                (symbol, exchange),
            ).fetchone()
            if row is not None:
                latest = dict(row)
                same = all(data.get(field) == latest.get(field) for field in _FUNDING_DEDUP_FIELDS)
                if same and latest.get("observed_at"):
                    try:
                        prev_ts = datetime.fromisoformat(latest["observed_at"])
                        if prev_ts.tzinfo is None:
                            prev_ts = prev_ts.replace(tzinfo=timezone.utc)
                        age = datetime.now(timezone.utc) - prev_ts
                        if age < timedelta(minutes=_FUNDING_HEARTBEAT_FLOOR_MINUTES):
                            return
                    except (TypeError, ValueError):
                        pass
    _insert("funding_rate_snapshots", data)


# Cross-venue / secondary feeds (Hyperliquid and Coinalyze aggregate data)
# share the funding/OI tables via the `exchange` tag, but must NOT win the
# per-symbol "latest" read that the LLM context, universe features and the
# primary collector's own delta all consume as the PRIMARY trading venue's
# view. Excluded by default; pass exchange=<name> to target one venue exactly.
_CROSS_VENUE_TAGS = ("hyperliquid", "hyperliquid_predicted", "coinalyze")


def _latest_deriv_where(exchange: str | None) -> tuple[str, tuple]:
    """Build (where, params) for a per-symbol latest derivatives read.

    exchange=None  → primary view: any venue EXCEPT the cross-venue secondaries.
    exchange="x"   → exactly that venue's rows.
    """
    if exchange is not None:
        return "symbol = ? AND exchange = ?", (exchange,)
    placeholders = ",".join("?" * len(_CROSS_VENUE_TAGS))
    return (
        f"symbol = ? AND (exchange IS NULL OR exchange NOT IN ({placeholders}))",
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
    return rows[0] if rows else None


def insert_open_interest_snapshot(data: dict) -> None:
    from aletheia.storage.models import new_id

    if "id" not in data:
        data["id"] = new_id()
    _insert("open_interest_snapshots", data)


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
    return rows[0] if rows else None


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
        where="symbol = ? AND exchange = ? AND observed_at BETWEEN ? AND ?",
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


def list_due_perp_structure_shadow_outcomes(
    before_ts: str,
    limit: int = 100,
) -> list[dict]:
    return _select_all(
        "perp_structure_shadow_outcomes",
        where="status = 'pending' AND due_at <= ?",
        params=(before_ts,),
        order="due_at ASC",
        limit=limit,
    )


def settle_perp_structure_shadow_outcome(
    outcome_id: str,
    *,
    exit_price: float,
    directional_return_pct: float,
    settled_at: str,
    price_source: str | None,
    settlement_lag_sec: float | None,
) -> None:
    _update(
        "perp_structure_shadow_outcomes",
        outcome_id,
        {
            "exit_price_usd": exit_price,
            "directional_return_pct": directional_return_pct,
            "settled_at": settled_at,
            "settlement_price_source": price_source,
            "settlement_lag_sec": settlement_lag_sec,
            "status": "settled",
        },
    )


def fail_perp_structure_shadow_outcome(
    outcome_id: str,
    settled_at: str,
    *,
    exit_price: float | None = None,
    settlement_price_source: str | None = None,
) -> None:
    """Record a terminal "not measurable" verdict for one Position X shadow row.

    See :func:`fail_forward_outcome` for why a rejected observation is recorded
    rather than dropped.
    """
    updates: dict[str, object] = {
        "settled_at": settled_at,
        "status": "failed",
    }
    if exit_price is not None:
        updates["exit_price_usd"] = exit_price
    if settlement_price_source is not None:
        updates["settlement_price_source"] = settlement_price_source
    _update("perp_structure_shadow_outcomes", outcome_id, updates)


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


def batch_perp_structures(symbols: list[str]) -> dict[str, dict]:
    """Return {symbol: latest_row} for all given symbols (latest by as_of_time DESC)."""
    if not symbols:
        return {}
    placeholders = ",".join("?" * len(symbols))
    db = get_db()
    rows = db.execute(
        f"""
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY as_of_time DESC, observed_at DESC
            ) AS _rn
            FROM perp_structure_snapshots
            WHERE symbol IN ({placeholders})
        ) WHERE _rn = 1
        """,
        tuple(symbols),
    ).fetchall()
    return {dict(r)["symbol"]: dict(r) for r in rows}


def batch_funding_rates(symbols: list[str], exchange: str) -> dict[str, dict]:
    """Return {symbol: latest_row} for exact exchange (latest by observed_at DESC)."""
    if not symbols:
        return {}
    placeholders = ",".join("?" * len(symbols))
    db = get_db()
    rows = db.execute(
        f"""
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY observed_at DESC
            ) AS _rn
            FROM funding_rate_snapshots
            WHERE symbol IN ({placeholders}) AND exchange = ?
        ) WHERE _rn = 1
        """,
        (*symbols, exchange),
    ).fetchall()
    return {dict(r)["symbol"]: dict(r) for r in rows}


def batch_open_interest(symbols: list[str], exchange: str) -> dict[str, dict]:
    """Return the newest exact-exchange OI row for each requested symbol."""
    if not symbols:
        return {}
    placeholders = ",".join("?" * len(symbols))
    rows = (
        get_db()
        .execute(
            f"""
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY observed_at DESC
            ) AS _rn
            FROM open_interest_snapshots
            WHERE symbol IN ({placeholders}) AND exchange = ?
        ) WHERE _rn = 1
        """,
            (*symbols, exchange),
        )
        .fetchall()
    )
    return {dict(row)["symbol"]: dict(row) for row in rows}


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


def insert_macro_snapshot(data: dict) -> None:
    from aletheia.storage.models import new_id

    if "id" not in data:
        data["id"] = new_id()
    _insert("macro_snapshots", data)


def latest_macro_snapshot() -> dict | None:
    rows = _select_all("macro_snapshots", order="observed_at DESC", limit=1)
    return rows[0] if rows else None


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
# Bulk insert uses canonical BEGIN/COMMIT (mirrors insert_serenity_claims_bulk).
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
    """Return the most recent batch's top_n rows, all sharing one observed_at.

    Two-step read pins to MAX(observed_at) first — otherwise a top_n=50 read
    could mix two adjacent batches if 100 rows landed in one tick (per spec MS-C04).
    """
    db = get_db()
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


def prune_perp_structure_shadow_outcomes(days: int = 30) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write("DELETE FROM perp_structure_shadow_outcomes WHERE entry_ts < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


# ---------------------------------------------------------------------------
# Round 2 — Step 5: DefiLlama on-chain snapshots
# ---------------------------------------------------------------------------


def insert_onchain_snapshot(data: dict) -> None:
    """Insert one onchain_snapshots row (stablecoin_mcap | chain_tvl | dex_volume).

    Follows the was_in_tx pattern: sqlite.py opens connections with
    isolation_level=None (autocommit), so we must explicitly BEGIN/COMMIT
    only when NOT already inside a transaction started by the caller.
    """
    from aletheia.storage.models import new_id

    if "id" not in data:
        data["id"] = new_id()
    _insert("onchain_snapshots", data)


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
    min_experiment_started_at: str | None = None,
) -> int:
    """Prune selected-member outcomes without deleting open-experiment fallback evidence."""
    if min_experiment_started_at is None:
        min_experiment_started_at = min_open_experiment_exposure_at()
    cutoff = _effective_cutoff(days, min_experiment_started_at)
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
    is never removed. Provenance fields on audit-chain rows (runtime_session_id)
    become dangling FK-less references but the referenced row was fully consumed
    at write time — audit reconstruction does not require the session record.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write(
        "DELETE FROM runtime_sessions WHERE ended_at IS NOT NULL AND started_at < ?",
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
        ("opportunities",  "runtime_session_id"),
        ("pi_decisions",   "runtime_session_id"),
        ("trade_proposals","runtime_session_id"),
        ("risk_decisions", "runtime_session_id"),
        ("orders",         "runtime_session_id"),
        ("fills",          "runtime_session_id"),
        ("positions",      "runtime_session_id"),
        ("positions",      "closed_runtime_session_id"),
    ]
    try:
        db = get_db()
        known = {
            r[0] for r in db.execute("SELECT session_id FROM runtime_sessions").fetchall()
        }
        total = 0
        by_table: dict[str, dict] = {}
        orphan_ids: set[str] = set()
        for table, col in _ENTITY_TABLES_COLS:
            rows = db.execute(
                f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL"
            ).fetchall()
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


def prune_filter_shadow_evaluations(days: int = 90) -> int:
    """Prune filter_shadow_evaluations older than ``days`` (by created_at).

    Called first by ``prune_filter_shadow_experiments`` to cascade-delete
    children; can also be invoked independently.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write("DELETE FROM filter_shadow_evaluations WHERE created_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_filter_shadow_experiments(days: int = 90) -> int:
    """Prune completed filter_shadow_experiments older than ``days`` + cascade evaluations.

    Excludes rows still in ``status='collecting'`` — a still-open experiment
    keeps its evidence indefinitely. FK on filter_shadow_evaluations.experiment_id
    lacks ON DELETE CASCADE so children are deleted first.
    """
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


def prune_universe_members(
    days: int = 30,
    min_experiment_started_at: str | None = None,
) -> int:
    """Prune universe_members whose parent snapshot is older than ``days``.

    Exposed for the _PRUNABLE whitelist. ``prune_universe_snapshots`` calls
    this first inside a single transaction.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if min_experiment_started_at is None:
        min_experiment_started_at = min_open_experiment_exposure_at()
    outcome_cutoff = _effective_cutoff(days, min_experiment_started_at)
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write(
        "DELETE FROM universe_members WHERE snapshot_id IN ("
        "  SELECT id FROM universe_snapshots WHERE created_at < ?"
        ") AND NOT EXISTS ("
        "  SELECT 1 FROM universe_forward_outcomes o "
        "  WHERE o.member_id=universe_members.id AND o.entry_ts>=?"
        ")",
        (cutoff, outcome_cutoff),
    )
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_universe_snapshots(
    days: int = 30,
    min_experiment_started_at: str | None = None,
) -> int:
    """Prune universe_snapshots older than ``days`` + cascade-delete universe_members.

    FK universe_members.snapshot_id has no ON DELETE CASCADE; children must be
    removed first in the same transaction. universe_forward_outcomes references
    universe_members. Snapshots with fallback outcomes protected by an active
    experiment remain until those outcomes leave their guarded window.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if min_experiment_started_at is None:
        min_experiment_started_at = min_open_experiment_exposure_at()
    outcome_cutoff = _effective_cutoff(days, min_experiment_started_at)
    db = get_db()
    snap_ids = [
        row[0]
        for row in db.execute(
            "SELECT s.id FROM universe_snapshots s WHERE s.created_at < ? "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM universe_members m "
            "  JOIN universe_forward_outcomes o ON o.member_id=m.id "
            "  WHERE m.snapshot_id=s.id AND o.entry_ts>=?"
            ")",
            (cutoff, outcome_cutoff),
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


def prune_universe_scanner_runs(days: int = 30) -> int:
    """Prune universe_scanner_runs older than ``days`` (by started_at)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write("DELETE FROM universe_scanner_runs WHERE started_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_universe_twin_shadow_evidence(
    days: int = 30,
) -> int:
    """Prune sampled diagnostic evidence after its fixed retention window.

    Records are deleted before their logical parent batch. Canonical
    fixed-horizon outcomes follow their own retention, and Twin writes no
    Learning exposure or verdict, so open experiments do not extend this
    evidence lifetime.
    """

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    with transaction(immediate=True):
        deleted = 0
        run_rows = db.execute(
            "SELECT scanner_run_id FROM universe_twin_shadow_batches WHERE captured_at < ?",
            (cutoff,),
        ).fetchall()
        run_ids = [str(row[0]) for row in run_rows]
        if run_ids:
            placeholders = ", ".join("?" for _ in run_ids)
            deleted += execute_write(
                f"DELETE FROM universe_twin_shadow_records WHERE scanner_run_id IN ({placeholders})",
                run_ids,
            ).rowcount or 0
            deleted += execute_write(
                f"DELETE FROM universe_twin_shadow_batches WHERE scanner_run_id IN ({placeholders})",
                run_ids,
            ).rowcount or 0
        deleted += execute_write(
            "DELETE FROM universe_relative_rank_shadow_batches WHERE captured_at < ?",
            (cutoff,),
        ).rowcount or 0
    return deleted


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
            execute_write(
                "UPDATE serenity_opportunities SET status='expired', updated_at=? "
                "WHERE theme_id=? AND status='active'",
                (now_iso, theme_id),
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
    from aletheia.storage.models import ShadowTrade

    data = _to_payload_dict(shadow)
    _insert("shadow_trades", data)


def update_shadow_trade(shadow_id: str, **kwargs) -> None:
    _update("shadow_trades", shadow_id, kwargs)


def anchor_shadow_trade_exit_price_if_pending(
    shadow_id: str,
    *,
    exit_price: float,
) -> bool:
    """Persist the first close-event price without marking the row resolved."""
    db = get_db()
    was_in_tx = db.in_transaction
    cursor = execute_write(
        "UPDATE shadow_trades SET exit_price = ? WHERE id = ? AND resolved_at IS NULL AND exit_price IS NULL",
        (exit_price, shadow_id),
    )
    if not was_in_tx:
        db.commit()
    return cursor.rowcount == 1


def resolve_shadow_trade_if_pending(
    shadow_id: str,
    *,
    exit_price: float,
    pnl_pct: float,
    resolved_at: str,
    economics: dict | None = None,
    settlement_price_source: str | None = None,
    settlement_lag_sec: float | None = None,
) -> bool:
    """Resolve one shadow row once, leaving an already-resolved row untouched.

    When ``economics`` is provided (dict returned by
    ``aletheia.universe.outcome_economics.estimate_outcome_economics``), the
    cost-model fields are populated in the same UPDATE. Rows resolved without
    ``economics`` remain compatible with the historic minimal-writer contract:
    only ``exit_price``, ``pnl_pct``, ``resolved_at`` change.
    """
    db = get_db()
    was_in_tx = db.in_transaction
    if not economics:
        cursor = execute_write(
            "UPDATE shadow_trades SET exit_price = ?, pnl_pct = ?, resolved_at = ? "
            "WHERE id = ? AND resolved_at IS NULL",
            (exit_price, pnl_pct, resolved_at, shadow_id),
        )
    else:
        cursor = execute_write(
            "UPDATE shadow_trades SET "
            " exit_price = ?, pnl_pct = ?, "
            " gross_return_pct = ?, net_pnl_pct = ?, "
            " estimated_fee_pct = ?, estimated_spread_slippage_pct = ?, "
            " estimated_funding_pct = ?, min_tradable_edge_pct = ?, "
            " settlement_price_source = ?, settlement_lag_sec = ?, "
            " cost_model_version = ?, resolved_at = ? "
            "WHERE id = ? AND resolved_at IS NULL",
            (
                exit_price,
                pnl_pct,
                economics.get("gross_return_pct"),
                economics.get("net_return_pct"),
                economics.get("estimated_fee_pct"),
                economics.get("estimated_spread_slippage_pct"),
                economics.get("estimated_funding_pct"),
                economics.get("min_tradable_edge_pct"),
                settlement_price_source,
                settlement_lag_sec,
                economics.get("cost_model_version"),
                resolved_at,
                shadow_id,
            ),
        )
    if not was_in_tx:
        db.commit()
    return cursor.rowcount == 1


def list_shadow_trades(
    batch_id: str | None = None,
    scanner_family: str | None = None,
    resolved: bool | None = None,
    limit: int = 200,
) -> list[dict]:
    conditions: list[str] = []
    params: list = []
    if batch_id is not None:
        conditions.append("batch_id = ?")
        params.append(batch_id)
    if scanner_family is not None:
        conditions.append("scanner_family = ?")
        params.append(scanner_family)
    if resolved is True:
        conditions.append("resolved_at IS NOT NULL")
    elif resolved is False:
        conditions.append("resolved_at IS NULL")
    where = " AND ".join(conditions) if conditions else None
    return _select_all("shadow_trades", where=where, params=tuple(params), limit=limit)


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
                pass
    _insert("market_regimes", data)


def get_latest_market_regime() -> dict | None:
    rows = _select_all("market_regimes", order="detected_at DESC", limit=1)
    if not rows:
        return None
    row = rows[0]
    if row.get("signals"):
        try:
            row["signals"] = json.loads(row["signals"])
        except (json.JSONDecodeError, TypeError):
            pass
    return row


def list_market_regimes(limit: int = 50) -> list[dict]:
    rows = _select_all("market_regimes", order="detected_at DESC", limit=limit)
    for row in rows:
        if row.get("signals"):
            try:
                row["signals"] = json.loads(row["signals"])
            except (json.JSONDecodeError, TypeError):
                pass
    return rows


# ── Universe Feature Snapshots (batch read) ───────────────────────────────────


def list_universe_feature_snapshots(limit: int = 50) -> list[dict]:
    """Return recent feature snapshots with their registry instrument identity.

    ``universe_feature_snapshots`` stores only ``asset_id``; consumers that need
    a symbol or market must resolve it through ``universe_registry`` instead of
    treating an unjoined row as an incomplete feature vector.
    """
    db = get_db()
    rows = db.execute(
        "SELECT ufs.*, ur.symbol, ur.market_type, ur.venue "
        "FROM universe_feature_snapshots ufs "
        "JOIN universe_registry ur ON ur.id = ufs.asset_id "
        "ORDER BY datetime(ufs.ts) DESC, ufs.rowid DESC LIMIT ?",
        (max(0, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]
