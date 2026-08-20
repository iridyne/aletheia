"""Append-only EventStore with DB + JSONL dual-write and idempotency."""

import json
import logging
import platform
import threading
from datetime import datetime, timedelta, timezone
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


logger = logging.getLogger("aletheia.event_store")
_JSONL_LOCK = threading.Lock()


def _jsonl_path(created_at: str | None = None) -> Path:
    try:
        observed = (
            datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if created_at
            else datetime.now(timezone.utc)
        )
    except ValueError:
        observed = datetime.now(timezone.utc)
    month = observed.astimezone(timezone.utc).strftime("%Y-%m")
    return _events_dir() / f"event_log_{month}.jsonl"


class EventStore:
    """Append-only event store backed by SQLite + JSONL files."""

    def append(self, event: PSEvent) -> PSEvent:
        """Append a single event. Returns the persisted event.

        On idempotency_key conflict returns the previously persisted event
        (same event_id); no duplicate JSONL line is written.
        """
        had_outer_transaction = get_db().in_transaction
        event = self._prepare(event)
        persisted, inserted = self._write_db(event)
        if not had_outer_transaction and (inserted or self._mirror_pending(persisted.event_id)):
            self._attempt_jsonl_mirror(persisted)
        return persisted

    def append_many(self, events: list[PSEvent]) -> list[PSEvent]:
        """Append multiple events atomically to DB, then JSONL.

        Events with an idempotency_key that already exists are returned as-is
        from the DB (same semantics as append); no duplicate JSONL lines are
        written for them.
        """
        prepared = [self._prepare(e) for e in events]
        db = get_db()
        was_in_tx = db.in_transaction
        results: list[tuple[PSEvent, bool]] = []
        from aletheia.storage.repositories import transaction

        with transaction():
            for ev in prepared:
                if ev.idempotency_key:
                    row = db.execute(
                        "SELECT * FROM ps_event_log WHERE idempotency_key = ?",
                        (ev.idempotency_key,),
                    ).fetchone()
                    if row:
                        results.append((self._row_to_event(row), False))
                        continue
                inserted = self._insert_row(db, ev)
                if not inserted:
                    # TOCTOU race on idempotency_key — fetch the winner
                    row = db.execute(
                        "SELECT * FROM ps_event_log WHERE idempotency_key = ?",
                        (ev.idempotency_key,),
                    ).fetchone()
                    results.append((self._row_to_event(row) if row else ev, False))
                else:
                    results.append((ev, True))
        if not was_in_tx:
            for ev, was_inserted in results:
                if was_inserted or self._mirror_pending(ev.event_id):
                    self._attempt_jsonl_mirror(ev)
        return [ev for ev, _ in results]

    def reconcile_jsonl(self, *, limit: int = 100) -> dict[str, int]:
        """Retry due EventStore mirrors without replaying authoritative events."""
        now = _utcnow_iso()
        rows = get_db().execute(
            "SELECT * FROM ps_event_log WHERE jsonl_mirror_status IN ('pending','failed') "
            "AND (jsonl_next_attempt_at IS NULL OR jsonl_next_attempt_at<=?) "
            "ORDER BY created_at LIMIT ?",
            (now, min(max(int(limit), 1), 500)),
        ).fetchall()
        mirrored = sum(self._attempt_jsonl_mirror(self._row_to_event(row)) for row in rows)
        return {"attempted": len(rows), "mirrored": mirrored, "failed": len(rows) - mirrored}

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

    def _write_db(self, event: PSEvent) -> tuple[PSEvent, bool]:
        """Insert event into SQLite. Returns (persisted_event, was_newly_inserted).

        On idempotency_key conflict returns the previously persisted event with
        was_newly_inserted=False. The caller should skip JSONL in that case.
        """
        db = get_db()
        from aletheia.storage.repositories import transaction

        with transaction():
            if event.idempotency_key:
                row = db.execute(
                    "SELECT * FROM ps_event_log WHERE idempotency_key = ?",
                    (event.idempotency_key,),
                ).fetchone()
                if row:
                    return self._row_to_event(row), False
            inserted = self._insert_row(db, event)
            if not inserted:
                # TOCTOU race: another writer committed the same idempotency_key
                # between our SELECT and INSERT. Fetch the winner.
                row = db.execute(
                    "SELECT * FROM ps_event_log WHERE idempotency_key = ?",
                    (event.idempotency_key,),
                ).fetchone()
                return (self._row_to_event(row) if row else event), False
            return event, True

    def _insert_row(self, db, event: PSEvent) -> bool:
        """Raw INSERT (caller manages transaction).

        Returns True if the row was inserted.
        Returns False only when idempotency_key is set and a concurrent writer
        already committed the same key (TOCTOU race).
        Re-raises IntegrityError for any other constraint violation.
        """
        import sqlite3
        try:
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
            return True
        except sqlite3.IntegrityError:
            if event.idempotency_key:
                winner = db.execute(
                    "SELECT event_id FROM ps_event_log WHERE idempotency_key = ?",
                    (event.idempotency_key,),
                ).fetchone()
                if winner:
                    return False  # confirmed TOCTOU race on idempotency_key
            raise

    def _mirror_pending(self, event_id: str) -> bool:
        row = get_db().execute(
            "SELECT jsonl_mirror_status FROM ps_event_log WHERE event_id=?",
            (event_id,),
        ).fetchone()
        return bool(row and row[0] != "mirrored")

    def _attempt_jsonl_mirror(self, event: PSEvent) -> bool:
        db = get_db()
        row = db.execute(
            "SELECT jsonl_attempt_count FROM ps_event_log WHERE event_id=?",
            (event.event_id,),
        ).fetchone()
        attempt_count = int(row[0] or 0) + 1
        now = datetime.now(timezone.utc)
        from aletheia.storage.repositories import transaction

        try:
            self._write_jsonl(event)
        except OSError as exc:
            delay = min(3600, 60 * (2 ** min(attempt_count - 1, 6)))
            with transaction():
                db.execute(
                    "UPDATE ps_event_log SET jsonl_mirror_status='failed', "
                    "jsonl_attempt_count=?, jsonl_next_attempt_at=?, "
                    "jsonl_last_error_class=?, jsonl_mirrored_at=NULL WHERE event_id=?",
                    (
                        attempt_count,
                        (now + timedelta(seconds=delay)).isoformat(),
                        type(exc).__name__,
                        event.event_id,
                    ),
                )
            logger.warning("event JSONL mirror failed for %s (%s)", event.event_id, type(exc).__name__)
            return False
        with transaction():
            db.execute(
                "UPDATE ps_event_log SET jsonl_mirror_status='mirrored', "
                "jsonl_attempt_count=?, jsonl_next_attempt_at=NULL, "
                "jsonl_last_error_class=NULL, jsonl_mirrored_at=? WHERE event_id=?",
                (attempt_count, now.isoformat(), event.event_id),
            )
        return True

    def _write_jsonl(self, event: PSEvent) -> None:
        """Idempotently append one event to its monthly JSONL mirror."""
        line = json.dumps(event.model_dump(), ensure_ascii=False)
        path = _jsonl_path(event.created_at)
        # ponytail: monthly O(n) scan is enough for local logs; add a side index only
        # if measured mirror retries make startup reconciliation material.
        with _JSONL_LOCK:
            if path.exists():
                with path.open("rb") as existing:
                    for raw in existing:
                        try:
                            parsed = json.loads(raw)
                            if isinstance(parsed, dict) and parsed.get("event_id") == event.event_id:
                                return
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
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
