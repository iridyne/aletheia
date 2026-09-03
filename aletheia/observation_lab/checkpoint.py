"""Joint app.db + lab.db + active-manifest checkpoint (Issue #1072 Cluster 5).

New #1072 §"Storage and runtime boundary" (verbatim):

> Managed backup/restore must treat ``app.db``, ``lab.db`` and active
> manifests as one consistent evidence checkpoint. If consistency cannot be
> proven after restore, affected observations fail closed as invalidated;
> they must not silently fall back to a different live cohort.

This module implements the minimum enforcement of that rule:

1. **Compute** a joint fingerprint at pass time — sha256 over
   ``(app migration head + count, sorted active manifest hashes)``.
2. **Persist** every fingerprint to ``lab.db::joint_checkpoints``.
3. **Verify** on Lab startup / every runner pass: fetch the latest stored
   fingerprint, recompute against current state. If they disagree, at
   least one of the three inputs was restored to a different point in
   time — that is exactly the "cannot prove consistency" case.

Ponytail scope: this is the primitive. Actual invalidation of affected
observations (marking them ``invalidated`` in ``filter_shadow_experiments``
/ future ``observation_specs``) is a caller responsibility once Packet 2
wires manifests into the real UFC path. Here we return the verdict and
let the caller decide the blast radius.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


# Checkpoints prove durable evidence identity, not scheduler heartbeats or
# mirror retry counters.  The latter change during a quiet bridge tick and
# must not make a coherent restore look divergent.
_CHECKPOINT_EXCLUDED_TABLES = frozenset({"observation_job_health"})
_CHECKPOINT_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "observation_import_cursors": (
        "source_name", "contiguous_sequence", "retry_sequences_json",
        "source_checkpoint_json", "source_checkpoint_fingerprint",
    ),
    "lab_audit_outbox": (
        "audit_id", "dedup_key", "event_type", "observation_id", "generation",
        "payload_json",
    ),
}
_OUTBOX_MUTABLE_COLUMNS = frozenset({
    "acknowledged_at",
    "lab_application_status",
    "lab_applied_observation_id",
    "lab_applied_generation",
    "lab_applied_event_id",
    "lab_application_error",
    "lab_applied_at",
})


@dataclass(frozen=True)
class JointCheckpoint:
    """One fingerprint of the (app.db + lab.db + active manifests) state."""

    checkpoint_id: str
    recorded_at: str
    app_migration_head: str
    app_migration_count: int
    active_manifest_hashes: tuple[str, ...]
    active_manifest_count: int
    lab_schema_head: str
    lab_schema_count: int
    lab_state_fingerprint: str
    checkpoint_ledger_fingerprint: str
    main_outbox_high_watermark: int
    main_outbox_pending_count: int
    main_outbox_fingerprint: str
    joint_fingerprint: str
    notes: str | None = None


@dataclass(frozen=True)
class CheckpointVerification:
    """Verdict of ``verify_joint_checkpoint``. ``ok=True`` means restore consistent."""

    ok: bool
    last: JointCheckpoint | None
    current_fingerprint: str
    reason: str
    mismatch_components: tuple[str, ...] = ()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _read_app_migration_state() -> tuple[str, int]:
    """Return (latest applied migration_id, total count).

    Uses production ``aletheia_schema_migrations`` ledger directly — this is
    a Lab-side read of an authoritative production fact. Any drift here is
    itself information: a Lab whose app.db has fewer migrations than the
    code expects is signalling a restore mismatch even before we hash.
    """
    from aletheia.storage.sqlite import get_db

    rows = get_db().execute(
        "SELECT migration_id FROM aletheia_schema_migrations "
        "ORDER BY migration_id DESC"
    ).fetchall()
    if not rows:
        return ("<empty>", 0)
    head = str(rows[0][0])
    return (head, len(rows))


def _read_app_outbox_state(*, through_sequence: int | None = None) -> tuple[int, int, str]:
    """Return the logical watermark and pending transport frontier."""
    from aletheia.storage.sqlite import get_db

    try:
        db = get_db()
        immutable_columns = [
            str(row[1])
            for row in db.execute(
                "PRAGMA table_info(observation_delivery_outbox)"
            ).fetchall()
            if str(row[1]) not in _OUTBOX_MUTABLE_COLUMNS
        ]
        if not immutable_columns:
            raise sqlite3.Error("outbox_schema_unavailable")
        sql = (
            f"SELECT {', '.join(immutable_columns)} "
            "FROM observation_delivery_outbox WHERE acknowledged_at IS NULL"
        )
        params: tuple[int, ...] = ()
        if through_sequence is not None:
            sql += " AND sequence<=?"
            params = (int(through_sequence),)
        rows = db.execute(f"{sql} ORDER BY sequence", params).fetchall()
        sequence_row = db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='observation_delivery_outbox'"
        ).fetchone()
        logical_watermark = int(sequence_row[0]) if sequence_row is not None else 0
    except Exception:
        return (-1, -1, "<unavailable>")
    watermark = (
        min(logical_watermark, int(through_sequence))
        if through_sequence is not None
        else logical_watermark
    )
    fingerprint = hashlib.sha256(
        _canonical_json([tuple(row) for row in rows]).encode("utf-8")
    ).hexdigest()
    return watermark, len(rows), fingerprint


def _mismatch_components(
    last: JointCheckpoint,
    current: JointCheckpoint,
) -> tuple[str, ...]:
    components: list[str] = []
    if (
        last.app_migration_head != current.app_migration_head
        or last.app_migration_count != current.app_migration_count
    ):
        components.append("app_migration")
    if last.active_manifest_hashes != current.active_manifest_hashes:
        components.append("active_manifests")
    if (
        last.lab_schema_head != current.lab_schema_head
        or last.lab_schema_count != current.lab_schema_count
    ):
        components.append("lab_schema")
    if last.lab_state_fingerprint != current.lab_state_fingerprint:
        components.append("lab_state")
    if last.checkpoint_ledger_fingerprint != current.checkpoint_ledger_fingerprint:
        components.append("checkpoint_ledger")
    if (
        last.main_outbox_high_watermark != current.main_outbox_high_watermark
        or last.main_outbox_pending_count != current.main_outbox_pending_count
        or last.main_outbox_fingerprint != current.main_outbox_fingerprint
    ):
        components.append("main_outbox")
    return tuple(components)


def _main_outbox_advanced_append_only(
    last: JointCheckpoint,
    current: JointCheckpoint,
) -> bool:
    """Accept pending receipts only when the checkpointed prefix is intact."""
    if current.main_outbox_high_watermark <= last.main_outbox_high_watermark:
        return False
    if (
        current.app_migration_head != last.app_migration_head
        or current.app_migration_count != last.app_migration_count
        or current.active_manifest_hashes != last.active_manifest_hashes
        or current.lab_schema_head != last.lab_schema_head
        or current.lab_schema_count != last.lab_schema_count
        or current.lab_state_fingerprint != last.lab_state_fingerprint
        or current.checkpoint_ledger_fingerprint != last.checkpoint_ledger_fingerprint
    ):
        return False
    prefix = _read_app_outbox_state(
        through_sequence=last.main_outbox_high_watermark
    )
    if prefix != (
        last.main_outbox_high_watermark,
        last.main_outbox_pending_count,
        last.main_outbox_fingerprint,
    ):
        return False
    from aletheia.storage.sqlite import get_db

    try:
        acknowledged_suffix = get_db().execute(
            "SELECT 1 FROM observation_delivery_outbox "
            "WHERE sequence>? AND acknowledged_at IS NOT NULL LIMIT 1",
            (last.main_outbox_high_watermark,),
        ).fetchone()
    except Exception:
        return False
    return acknowledged_suffix is None


def _active_manifest_hashes(
    now: str | None = None,
    *,
    db: sqlite3.Connection | None = None,
) -> tuple[str, ...]:
    """Sorted, deduplicated hashes read through one Lab snapshot."""
    from aletheia.observation_lab.manifest import load_active_manifests

    if db is None:
        with _lab_read_snapshot() as connection:
            return _active_manifest_hashes(now, db=connection)
    manifests = load_active_manifests(now=now, db=db)
    return tuple(sorted({m.manifest_hash for m in manifests}))


def _lab_schema_state(*, db: sqlite3.Connection | None = None) -> tuple[str, int]:
    if db is None:
        with _lab_read_snapshot() as connection:
            return _lab_schema_state(db=connection)
    rows = db.execute(
        "SELECT migration_id FROM lab_schema_migrations ORDER BY rowid DESC"
    ).fetchall()
    if not rows:
        return ("<empty>", 0)
    return str(rows[0][0]), len(rows)


@contextmanager
def _lab_read_snapshot():
    """Hold one consistent Lab read transaction across a checkpoint digest."""
    # Checkpoint computation is a read-only proof surface.  ``get_lab_db``
    # materializes the schema when the file is missing, which would turn a
    # missing/corrupt Lab restore into a new empty authority.  Use the physical
    # read-only boundary so absence remains an explicit proof failure.
    from aletheia.observation_lab.db import readonly_lab_db

    with readonly_lab_db() as db:
        yield db


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    return value


def _lab_state_fingerprint(*, db: sqlite3.Connection | None = None) -> str:
    """Hash every Lab table except the checkpoint ledger itself.

    The checkpoint row is intentionally excluded from this digest because it
    records the digest. The separate ledger digest below covers prior rows in
    a stable way, so saving a quiet checkpoint does not create self-drift.
    """
    if db is None:
        with _lab_read_snapshot() as connection:
            return _lab_state_fingerprint(db=connection)
    tables = db.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name <> 'joint_checkpoints' "
        "ORDER BY name"
    ).fetchall()
    material: list[Any] = []
    for table in tables:
        name = str(table["name"])
        if name in _CHECKPOINT_EXCLUDED_TABLES:
            continue
        columns = [
            str(row["name"])
            for row in db.execute(f'PRAGMA table_info("{name}")').fetchall()
        ]
        selected_columns = _CHECKPOINT_TABLE_COLUMNS.get(name, tuple(columns))
        if not selected_columns:
            continue
        if any(column not in columns for column in selected_columns):
            raise RuntimeError(f"checkpoint_lab_schema_unavailable:{name}")
        rows = db.execute(f'SELECT * FROM "{name}"').fetchall()
        encoded_rows = sorted(
            [
                [_json_value(row[column]) for column in selected_columns]
                for row in rows
            ],
            key=_canonical_json,
        )
        material.append({
            "name": name,
            "sql": table["sql"],
            "columns": list(selected_columns),
            "rows": encoded_rows,
        })
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _checkpoint_ledger_fingerprint(
    lab_state_fingerprint: str,
    *,
    db: sqlite3.Connection | None = None,
) -> str:
    """Hash historical checkpoint rows while excluding the current state rows."""
    if db is None:
        with _lab_read_snapshot() as connection:
            return _checkpoint_ledger_fingerprint(lab_state_fingerprint, db=connection)
    rows = db.execute(
        "SELECT checkpoint_id, app_migration_head, app_migration_count, "
        "active_manifest_hashes, active_manifest_count, lab_schema_head, "
        "lab_schema_count, lab_state_fingerprint, checkpoint_ledger_fingerprint, "
        "joint_fingerprint FROM joint_checkpoints ORDER BY rowid"
    ).fetchall()
    material = [
        {
            "checkpoint_id": row["checkpoint_id"],
            "app_migration_head": row["app_migration_head"],
            "app_migration_count": row["app_migration_count"],
            "active_manifest_hashes": row["active_manifest_hashes"],
            "active_manifest_count": row["active_manifest_count"],
            "lab_schema_head": row["lab_schema_head"],
            "lab_schema_count": row["lab_schema_count"],
            "lab_state_fingerprint": row["lab_state_fingerprint"],
            "checkpoint_ledger_fingerprint": row["checkpoint_ledger_fingerprint"],
            "joint_fingerprint": row["joint_fingerprint"],
        }
        for row in rows
        if row["lab_state_fingerprint"] != lab_state_fingerprint
    ]
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _fingerprint(
    *,
    app_migration_head: str,
    app_migration_count: int,
    active_manifest_hashes: tuple[str, ...],
    lab_schema_head: str,
    lab_schema_count: int,
    lab_state_fingerprint: str,
    checkpoint_ledger_fingerprint: str,
    main_outbox_high_watermark: int,
    main_outbox_pending_count: int,
    main_outbox_fingerprint: str,
) -> str:
    payload = {
        "app_migration_head": app_migration_head,
        "app_migration_count": int(app_migration_count),
        "active_manifest_hashes": list(active_manifest_hashes),
        "lab_schema_head": lab_schema_head,
        "lab_schema_count": int(lab_schema_count),
        "lab_state_fingerprint": lab_state_fingerprint,
        "checkpoint_ledger_fingerprint": checkpoint_ledger_fingerprint,
        "main_outbox_high_watermark": int(main_outbox_high_watermark),
        "main_outbox_pending_count": int(main_outbox_pending_count),
        "main_outbox_fingerprint": main_outbox_fingerprint,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_joint_checkpoint(
    *, notes: str | None = None, create_lab: bool = True,
) -> JointCheckpoint:
    """Snapshot current app + lab + manifest state as a fingerprint.

    Does NOT persist. Callers use ``save_joint_checkpoint`` to append. Kept
    separate so tests and CLI can inspect the current shape without touching
    the ledger.
    """
    if create_lab:
        # Explicit checkpoint creation is an operator/runner write boundary;
        # materialize the Lab schema only for that caller.  Verification and
        # runtime resolution can pass ``create_lab=False`` to preserve a
        # missing Lab as unavailable evidence.
        from aletheia.observation_lab.db import get_lab_db

        get_lab_db()
    head, count = _read_app_migration_state()
    outbox_watermark, outbox_pending, outbox_fingerprint = _read_app_outbox_state()
    with _lab_read_snapshot() as lab:
        # Keep every Lab-derived component on the same physical read
        # transaction.  A manifest publish/revocation or lifecycle write that
        # races this digest must land wholly before or after the checkpoint;
        # nested connections would otherwise create a mixed cut.
        hashes = _active_manifest_hashes(db=lab)
        lab_head, lab_count = _lab_schema_state(db=lab)
        lab_state = _lab_state_fingerprint(db=lab)
        ledger_state = _checkpoint_ledger_fingerprint(lab_state, db=lab)
    fingerprint = _fingerprint(
        app_migration_head=head,
        app_migration_count=count,
        active_manifest_hashes=hashes,
        lab_schema_head=lab_head,
        lab_schema_count=lab_count,
        lab_state_fingerprint=lab_state,
        checkpoint_ledger_fingerprint=ledger_state,
        main_outbox_high_watermark=outbox_watermark,
        main_outbox_pending_count=outbox_pending,
        main_outbox_fingerprint=outbox_fingerprint,
    )
    return JointCheckpoint(
        checkpoint_id=uuid.uuid4().hex,
        recorded_at=_now(),
        app_migration_head=head,
        app_migration_count=count,
        active_manifest_hashes=hashes,
        active_manifest_count=len(hashes),
        lab_schema_head=lab_head,
        lab_schema_count=lab_count,
        lab_state_fingerprint=lab_state,
        checkpoint_ledger_fingerprint=ledger_state,
        main_outbox_high_watermark=outbox_watermark,
        main_outbox_pending_count=outbox_pending,
        main_outbox_fingerprint=outbox_fingerprint,
        joint_fingerprint=fingerprint,
        notes=notes,
    )


def save_joint_checkpoint(checkpoint: JointCheckpoint) -> None:
    """Append one checkpoint. Idempotent no-op if the exact same
    ``joint_fingerprint`` is already the most recent row (avoids ledger
    noise when the underlying state is quiet).
    """
    from aletheia.observation_lab.db import get_lab_db

    db = get_lab_db()
    latest = db.execute(
        "SELECT joint_fingerprint FROM joint_checkpoints "
        "ORDER BY recorded_at DESC LIMIT 1"
    ).fetchone()
    if latest is not None and latest["joint_fingerprint"] == checkpoint.joint_fingerprint:
        return
    db.execute(
        "INSERT INTO joint_checkpoints ("
        "checkpoint_id, recorded_at, app_migration_head, app_migration_count, "
        "active_manifest_hashes, active_manifest_count, joint_fingerprint, notes"
        ", lab_schema_head, lab_schema_count, lab_state_fingerprint, "
        "checkpoint_ledger_fingerprint, main_outbox_high_watermark, main_outbox_pending_count, "
        "main_outbox_fingerprint) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            checkpoint.checkpoint_id,
            checkpoint.recorded_at,
            checkpoint.app_migration_head,
            checkpoint.app_migration_count,
            _canonical_json(list(checkpoint.active_manifest_hashes)),
            checkpoint.active_manifest_count,
            checkpoint.joint_fingerprint,
            checkpoint.notes,
            checkpoint.lab_schema_head,
            checkpoint.lab_schema_count,
            checkpoint.lab_state_fingerprint,
            checkpoint.checkpoint_ledger_fingerprint,
            checkpoint.main_outbox_high_watermark,
            checkpoint.main_outbox_pending_count,
            checkpoint.main_outbox_fingerprint,
        ),
    )


def latest_joint_checkpoint() -> JointCheckpoint | None:
    """Read the most recent stored checkpoint, or None on a fresh Lab."""
    from aletheia.observation_lab.db import readonly_lab_db

    with readonly_lab_db(allow_missing=True) as db:
        if db is None:
            return None
        row = db.execute(
            "SELECT * FROM joint_checkpoints ORDER BY recorded_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return JointCheckpoint(
        checkpoint_id=row["checkpoint_id"],
        recorded_at=row["recorded_at"],
        app_migration_head=row["app_migration_head"],
        app_migration_count=int(row["app_migration_count"]),
        active_manifest_hashes=tuple(json.loads(row["active_manifest_hashes"])),
        active_manifest_count=int(row["active_manifest_count"]),
        lab_schema_head=str(row["lab_schema_head"] or "<legacy>"),
        lab_schema_count=int(row["lab_schema_count"] or 0),
        lab_state_fingerprint=str(row["lab_state_fingerprint"] or "<legacy>"),
        checkpoint_ledger_fingerprint=str(row["checkpoint_ledger_fingerprint"] or "<legacy>"),
        main_outbox_high_watermark=int(row["main_outbox_high_watermark"] if row["main_outbox_high_watermark"] is not None else -1),
        main_outbox_pending_count=int(row["main_outbox_pending_count"] if row["main_outbox_pending_count"] is not None else -1),
        main_outbox_fingerprint=str(row["main_outbox_fingerprint"] or "<legacy>"),
        joint_fingerprint=row["joint_fingerprint"],
        notes=row["notes"],
    )


def verify_joint_checkpoint(
    *,
    create_lab: bool = True,
    allow_main_outbox_progress: bool = False,
) -> CheckpointVerification:
    """Compare current joint state against the latest stored checkpoint.

    Returns:
    - ``ok=True`` if the most recent stored fingerprint equals the freshly-
      computed one, or if the stored ledger is empty (first-ever pass).
    - The scheduled runner may explicitly accept an intact checkpointed Main
      prefix followed only by pending receipts, then import and checkpoint it.
    - ``ok=False`` with a typed reason otherwise. The caller MUST NOT
      silently proceed; failing closed on the affected Observations is
      the required response per new #1072 §"Storage and runtime boundary".
    """
    try:
        current = compute_joint_checkpoint(create_lab=create_lab)
    except (FileNotFoundError, OSError, RuntimeError, sqlite3.Error, ValueError, TypeError):
        try:
            last = latest_joint_checkpoint()
        except (FileNotFoundError, OSError, sqlite3.Error, ValueError, TypeError):
            last = None
        return CheckpointVerification(
            ok=False,
            last=last,
            current_fingerprint="<unavailable>",
            reason="lab_unavailable",
        )
    try:
        last = latest_joint_checkpoint()
    except (FileNotFoundError, OSError, sqlite3.Error, ValueError, TypeError):
        return CheckpointVerification(
            ok=False,
            last=None,
            current_fingerprint=current.joint_fingerprint,
            reason="lab_unavailable",
        )
    if (
        current.main_outbox_high_watermark < 0
        or current.main_outbox_pending_count < 0
        or current.main_outbox_fingerprint == "<unavailable>"
    ):
        return CheckpointVerification(
            ok=False,
            last=last,
            current_fingerprint=current.joint_fingerprint,
            reason="main_outbox_unavailable",
        )
    if last is None:
        return CheckpointVerification(
            ok=True,
            last=None,
            current_fingerprint=current.joint_fingerprint,
            reason="first_checkpoint",
        )
    if last.joint_fingerprint == current.joint_fingerprint:
        return CheckpointVerification(
            ok=True,
            last=last,
            current_fingerprint=current.joint_fingerprint,
            reason="fingerprint_match",
        )
    if allow_main_outbox_progress and _main_outbox_advanced_append_only(
        last, current
    ):
        return CheckpointVerification(
            ok=True,
            last=last,
            current_fingerprint=current.joint_fingerprint,
            reason="main_outbox_append_only",
        )
    mismatch_components = _mismatch_components(last, current)
    return CheckpointVerification(
        ok=False,
        last=last,
        current_fingerprint=current.joint_fingerprint,
        reason=(
            f"fingerprint_mismatch: app_head({last.app_migration_head}→"
            f"{current.app_migration_head}) count({last.app_migration_count}→"
            f"{current.app_migration_count}) manifests({last.active_manifest_count}→"
            f"{current.active_manifest_count}) lab_schema({last.lab_schema_head}/"
            f"{last.lab_schema_count}→{current.lab_schema_head}/"
            f"{current.lab_schema_count}) lab_state({last.lab_state_fingerprint}→"
            f"{current.lab_state_fingerprint}) outbox({last.main_outbox_high_watermark}→"
            f"{current.main_outbox_high_watermark})"
        ),
        mismatch_components=mismatch_components,
    )
