"""Append-only EventStore with DB + JSONL dual-write and idempotency."""

import json
import platform
from datetime import datetime, timezone
from pathlib import Path

from aletheia.config.loader import get_data_dir
from aletheia.storage.hashing import canonical_json, sha256_bytes
from aletheia.storage.models import PSEvent, new_id
from aletheia.storage.sqlite import get_db


def _machine_id() -> str:
    return platform.node() or "unknown"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _events_dir() -> Path:
    d = get_data_dir() / "events"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _jsonl_path() -> Path:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    return _events_dir() / f"event_log_{month}.jsonl"


class EventStore:
    """Append-only event store backed by SQLite + JSONL files."""

    def append(self, event: PSEvent) -> PSEvent:
        """Append a single event. Returns the persisted event.

        On an idempotency_key conflict the existing DB row is returned and no
        JSONL line is written.  On any other integrity violation the error
        propagates and no JSONL line is written.
        """
        prepared = self._prepare(event)
        persisted = self._write_db(prepared)
        if persisted.event_id == prepared.event_id:
            self._write_jsonl(persisted)
        return persisted

    def append_many(self, events: list[PSEvent]) -> list[PSEvent]:
        """Append multiple events atomically to DB, then JSONL.

        Per-event idempotency: events whose idempotency_key already exists are
        resolved to the existing DB row before the transaction opens.  JSONL is
        written only for rows that were actually inserted.
        """
        prepared = [self._prepare(e) for e in events]
        db = get_db()

        results: list[PSEvent] = []
        to_insert: list[PSEvent] = []
        for ev in prepared:
            if ev.idempotency_key:
                existing_row = db.execute(
                    "SELECT * FROM ps_event_log WHERE idempotency_key = ?",
                    (ev.idempotency_key,),
                ).fetchone()
                if existing_row:
                    results.append(self._row_to_event(existing_row))
                    continue
            results.append(ev)
            to_insert.append(ev)

        if to_insert:
            was_in_tx = db.in_transaction
            if not was_in_tx:
                db.execute("BEGIN")
            try:
                for ev in to_insert:
                    self._insert_row(db, ev)
                if not was_in_tx:
                    db.commit()
            except Exception:
                if not was_in_tx:
                    db.rollback()
                raise
            for ev in to_insert:
                self._write_jsonl(ev)

        return results

    def get(self, event_id: str) -> PSEvent | None:
        """Retrieve a single event by ID."""
        db = get_db()
        row = db.execute(
            "SELECT * FROM ps_event_log WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_event(row)

    def list_by_aggregate(
        self, aggregate_type: str, aggregate_id: str, limit: int = 200
    ) -> list[PSEvent]:
        """List events for a specific aggregate, ordered chronologically."""
        db = get_db()
        rows = db.execute(
            "SELECT * FROM ps_event_log "
            "WHERE aggregate_type = ? AND aggregate_id = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (aggregate_type, aggregate_id, limit),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def list_since(self, since: str = "", limit: int = 500) -> list[PSEvent]:
        """List events created after a timestamp (ISO string)."""
        db = get_db()
        rows = db.execute(
            "SELECT * FROM ps_event_log WHERE created_at > ? ORDER BY created_at ASC LIMIT ?",
            (since, limit),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def list_by_correlation(self, correlation_id: str, limit: int = 200) -> list[PSEvent]:
        """List events sharing a correlation ID."""
        db = get_db()
        rows = db.execute(
            "SELECT * FROM ps_event_log WHERE correlation_id = ? ORDER BY created_at ASC LIMIT ?",
            (correlation_id, limit),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def list_by_type(self, event_type: str, limit: int = 100) -> list[PSEvent]:
        """List events of a given type."""
        db = get_db()
        rows = db.execute(
            "SELECT * FROM ps_event_log WHERE event_type = ? ORDER BY created_at DESC LIMIT ?",
            (event_type, limit),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prepare(self, event: PSEvent) -> PSEvent:
        """Fill computed fields: event_id, payload_hash, machine_id, created_at."""
        if not event.event_id:
            event.event_id = new_id()
        if not event.created_at:
            event.created_at = _utcnow_iso()
        if not event.machine_id:
            event.machine_id = _machine_id()
        payload_obj = json.loads(event.payload_json) if event.payload_json else {}
        event.payload_json = canonical_json(payload_obj)
        event.payload_hash = sha256_bytes(event.payload_json.encode("utf-8"))
        return event

    def _write_db(self, event: PSEvent) -> PSEvent:
        """Insert event into SQLite.

        Returns the persisted event — the newly inserted one, or the existing
        DB row on an idempotency_key conflict.  A concurrent writer winning the
        TOCTOU window is handled by a second lookup after the IntegrityError.
        All other constraint violations propagate as IntegrityError.
        """
        import sqlite3

        db = get_db()
        was_in_tx = db.in_transaction
        if event.idempotency_key:
            existing_row = db.execute(
                "SELECT * FROM ps_event_log WHERE idempotency_key = ?",
                (event.idempotency_key,),
            ).fetchone()
            if existing_row:
                return self._row_to_event(existing_row)
        try:
            self._insert_row(db, event)
        except sqlite3.IntegrityError:
            if event.idempotency_key:
                existing_row = db.execute(
                    "SELECT * FROM ps_event_log WHERE idempotency_key = ?",
                    (event.idempotency_key,),
                ).fetchone()
                if existing_row:
                    return self._row_to_event(existing_row)
            raise
        if not was_in_tx:
            db.commit()
        return event

    def _insert_row(self, db, event: PSEvent) -> None:
        """Raw INSERT (caller manages transaction). Raises IntegrityError on violation."""
        db.execute(
            "INSERT INTO ps_event_log "
            "(event_id, event_type, aggregate_type, aggregate_id, "
            "actor_type, actor_id, run_id, causation_id, correlation_id, "
            "idempotency_key, payload_json, payload_hash, created_at, "
            "machine_id, schema_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id, event.event_type,
                event.aggregate_type, event.aggregate_id,
                event.actor_type, event.actor_id,
                event.run_id, event.causation_id, event.correlation_id,
                event.idempotency_key,
                event.payload_json, event.payload_hash,
                event.created_at, event.machine_id, event.schema_version,
            ),
        )

    def _write_jsonl(self, event: PSEvent) -> None:
        """Append event as one JSON line to the monthly JSONL file."""
        line = json.dumps(event.model_dump(), ensure_ascii=False)
        path = _jsonl_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _row_to_event(self, row) -> PSEvent:
        """Convert a sqlite3.Row to PSEvent."""
        return PSEvent(
            event_id=row["event_id"],
            event_type=row["event_type"],
            aggregate_type=row["aggregate_type"],
            aggregate_id=row["aggregate_id"],
            actor_type=row["actor_type"],
            actor_id=row["actor_id"],
            run_id=row["run_id"],
            causation_id=row["causation_id"],
            correlation_id=row["correlation_id"],
            idempotency_key=row["idempotency_key"],
            payload_json=row["payload_json"],
            payload_hash=row["payload_hash"],
            created_at=row["created_at"],
            machine_id=row["machine_id"],
            schema_version=row["schema_version"],
        )


# Module-level singleton for convenience
_store: EventStore | None = None


def get_event_store() -> EventStore:
    """Get the module-level EventStore singleton."""
    global _store
    if _store is None:
        _store = EventStore()
    return _store


def make_event(
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    *,
    actor_type: str = "system",
    actor_id: str = "gateway",
    payload: dict | None = None,
    run_id: str | None = None,
    causation_id: str | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
) -> PSEvent:
    """Helper to construct an event with sensible defaults."""
    return PSEvent(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        actor_type=actor_type,
        actor_id=actor_id,
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
        run_id=run_id,
        causation_id=causation_id,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )
