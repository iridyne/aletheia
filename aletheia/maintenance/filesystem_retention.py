"""Filesystem-level retention helpers for non-DB working directories.

These helpers are the ``mtime`` / count-based cousins of the DB-JOIN pruners
in ``aletheia.agent_shell.file_artifacts``. They reclaim disk under the
runtime-home tree — backups, diagnostics bundles, the pre-migration DB copy,
and stale knowledge subdirs — using the retention windows locked in Phase 2.

Every helper here is idempotent: a second call once the target is inside its
window is a no-op. All accept an optional ``data_dir`` for tests; production
callers hit :func:`aletheia.config.loader.get_data_dir`.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from aletheia.config.loader import get_data_dir

logger = logging.getLogger("aletheia.maintenance.filesystem_retention")

# Migration marker path (relative to data_dir) — matches
# ``aletheia.runtime_home_migration.MIGRATION_MARKER``. Duplicated here to
# avoid importing the migration module (which does heavier work at import).
_MIGRATION_MARKER_NAME = ".runtime-home-migration.json"

# Pre-migration DB backup written by the legacy path — see the manual
# migration flow. Fixed filename under the runtime home.
_PRE_MIGRATION_BACKUP_NAME = "app.db.pre-migration-backup"


def _resolve_data_dir(data_dir: Path | None) -> Path:
    return data_dir if data_dir is not None else get_data_dir()


def prune_backups(keep_recent: int = 7, data_dir: Path | None = None) -> int:
    """Rotate ``<data_dir>/backups`` to the newest ``keep_recent`` archives.

    Thin dispatch to :func:`aletheia.backup.service.retain_recent` so the
    scheduler can consume a uniform ``prune_*`` surface. Idempotent.
    """
    # Imported lazily to keep the backup module optional at import time.
    from aletheia.backup.service import retain_recent

    return retain_recent(keep_recent=keep_recent, data_dir=_resolve_data_dir(data_dir))


def prune_diagnostics_dir(days: int = 14, data_dir: Path | None = None) -> int:
    """Delete files under ``<data_dir>/diagnostics`` older than ``days``.

    Diagnostics bundles (thesis lineage snapshots, doctor bundles, deferred
    audits) are point-in-time debug artifacts — none of the learning or audit
    chains reads them, and there is no on-disk index that references them by
    path, so mtime-based aging is safe. Empty directories left behind by the
    sweep are removed.

    Returns the number of files unlinked.
    """
    root = _resolve_data_dir(data_dir) / "diagnostics"
    if not root.is_dir():
        return 0

    cutoff = time.time() - max(0, int(days)) * 86400
    deleted = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            deleted += 1
        except OSError as exc:
            logger.debug("diagnostics prune skipped %s: %s", path.name, exc)

    # Clean up any now-empty subdirectories (bottom-up).
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and path != root:
            try:
                path.rmdir()
            except OSError:
                # Non-empty or permission — skip silently.
                pass

    return deleted


def prune_pre_migration_backup(
    days: int = 30, data_dir: Path | None = None
) -> int:
    """Delete ``app.db.pre-migration-backup`` once the migration is old enough.

    The runtime-home migration writes a marker
    (``.runtime-home-migration.json``) with a ``completed_at`` timestamp and,
    on some paths, leaves a pre-migration DB copy next to the new ``app.db``.
    Once ``days`` have elapsed since the migration completed, the copy is
    unrecoverable-value bytes and can be removed. If the marker is missing or
    unparseable this is a no-op (fail-safe: never delete without an explicit
    completed_at older than the window).

    Returns 1 if the backup was removed, 0 otherwise.
    """
    root = _resolve_data_dir(data_dir)
    marker = root / _MIGRATION_MARKER_NAME
    backup = root / _PRE_MIGRATION_BACKUP_NAME
    if not backup.exists():
        return 0
    if not marker.is_file():
        return 0
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("pre-migration marker unreadable: %s", exc)
        return 0
    completed_raw = payload.get("completed_at") if isinstance(payload, dict) else None
    if not completed_raw:
        return 0
    try:
        completed = datetime.fromisoformat(str(completed_raw).replace("Z", "+00:00"))
    except ValueError as exc:
        logger.debug("pre-migration completed_at unparseable: %s", exc)
        return 0
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - completed).total_seconds() / 86400.0
    if age_days < max(0, int(days)):
        return 0
    try:
        backup.unlink()
    except OSError as exc:
        logger.debug("pre-migration backup unlink failed: %s", exc)
        return 0
    return 1


def prune_knowledge_subdirs(
    days: int = 90,
    data_dir: Path | None = None,
    subdirs: tuple[str, ...] = ("deprecated", "dreaming_logs"),
) -> int:
    """Age out non-canonical files under ``<data_dir>/knowledge/<subdir>``.

    Only sweeps subdirectories explicitly named in ``subdirs``; the primary
    knowledge subdirs (``agent_memory``, ``strategies``, ``research_notes``,
    ``operator_memory``, ``daily_reviews``, ``post_trade_reviews``,
    ``guard_candidates``, ``evidence``) are never touched by this helper —
    they are DB-cross-referenced by ``knowledge_items`` and must not be reaped
    without a corresponding DB sweep.

    Returns the number of files unlinked.
    """
    root = _resolve_data_dir(data_dir) / "knowledge"
    if not root.is_dir():
        return 0

    cutoff = time.time() - max(0, int(days)) * 86400
    deleted = 0
    for name in subdirs:
        target = root / name
        if not target.is_dir():
            continue
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                path.unlink()
                deleted += 1
            except OSError as exc:
                logger.debug("knowledge prune skipped %s: %s", path.name, exc)
        # Clean now-empty inner dirs.
        for path in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir() and path != target:
                try:
                    path.rmdir()
                except OSError:
                    pass
    return deleted


__all__ = [
    "prune_backups",
    "prune_diagnostics_dir",
    "prune_pre_migration_backup",
    "prune_knowledge_subdirs",
]
