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
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class JointCheckpoint:
    """One fingerprint of the (app.db + lab.db + active manifests) state."""

    checkpoint_id: str
    recorded_at: str
    app_migration_head: str
    app_migration_count: int
    active_manifest_hashes: tuple[str, ...]
    active_manifest_count: int
    joint_fingerprint: str
    notes: str | None = None


@dataclass(frozen=True)
class CheckpointVerification:
    """Verdict of ``verify_joint_checkpoint``. ``ok=True`` means restore consistent."""

    ok: bool
    last: JointCheckpoint | None
    current_fingerprint: str
    reason: str


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


def _active_manifest_hashes(now: str | None = None) -> tuple[str, ...]:
    """Sorted, deduplicated hash tuple for every currently-effective manifest."""
    from aletheia.observation_lab.manifest import load_active_manifests

    manifests = load_active_manifests(now=now)
    return tuple(sorted({m.manifest_hash for m in manifests}))


def _fingerprint(
    *,
    app_migration_head: str,
    app_migration_count: int,
    active_manifest_hashes: tuple[str, ...],
) -> str:
    payload = {
        "app_migration_head": app_migration_head,
        "app_migration_count": int(app_migration_count),
        "active_manifest_hashes": list(active_manifest_hashes),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_joint_checkpoint(*, notes: str | None = None) -> JointCheckpoint:
    """Snapshot current app + lab + manifest state as a fingerprint.

    Does NOT persist. Callers use ``save_joint_checkpoint`` to append. Kept
    separate so tests and CLI can inspect the current shape without touching
    the ledger.
    """
    head, count = _read_app_migration_state()
    hashes = _active_manifest_hashes()
    fingerprint = _fingerprint(
        app_migration_head=head,
        app_migration_count=count,
        active_manifest_hashes=hashes,
    )
    return JointCheckpoint(
        checkpoint_id=uuid.uuid4().hex,
        recorded_at=_now(),
        app_migration_head=head,
        app_migration_count=count,
        active_manifest_hashes=hashes,
        active_manifest_count=len(hashes),
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
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            checkpoint.checkpoint_id,
            checkpoint.recorded_at,
            checkpoint.app_migration_head,
            checkpoint.app_migration_count,
            _canonical_json(list(checkpoint.active_manifest_hashes)),
            checkpoint.active_manifest_count,
            checkpoint.joint_fingerprint,
            checkpoint.notes,
        ),
    )


def latest_joint_checkpoint() -> JointCheckpoint | None:
    """Read the most recent stored checkpoint, or None on a fresh Lab."""
    from aletheia.observation_lab.db import get_lab_db

    row = get_lab_db().execute(
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
        joint_fingerprint=row["joint_fingerprint"],
        notes=row["notes"],
    )


def verify_joint_checkpoint() -> CheckpointVerification:
    """Compare current joint state against the latest stored checkpoint.

    Returns:
    - ``ok=True`` if the most recent stored fingerprint equals the freshly-
      computed one, OR if the stored ledger is empty (first-ever pass).
    - ``ok=False`` with a typed reason otherwise. The caller MUST NOT
      silently proceed; failing closed on the affected Observations is
      the required response per new #1072 §"Storage and runtime boundary".
    """
    current = compute_joint_checkpoint()
    last = latest_joint_checkpoint()
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
    return CheckpointVerification(
        ok=False,
        last=last,
        current_fingerprint=current.joint_fingerprint,
        reason=(
            f"fingerprint_mismatch: app_head({last.app_migration_head}→"
            f"{current.app_migration_head}) count({last.app_migration_count}→"
            f"{current.app_migration_count}) manifests({last.active_manifest_count}→"
            f"{current.active_manifest_count})"
        ),
    )
