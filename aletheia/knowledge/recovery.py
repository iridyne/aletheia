"""Read-only recovery scanner for orphan high-confidence knowledge bodies.

Historical ``knowledge_items`` rows carry a ``file_path`` that once pointed to a
Markdown file on the writer's machine (Windows or POSIX). When runtime data
moved to ``~/.aletheia`` and old repository-root ``data/knowledge`` directories
were cleaned, those paths went stale — the disk body is gone from the FTS
index, but the row still claims ``high_confidence``.

This module is READ-ONLY. It reports which orphan rows can still be resolved
against local candidate roots so a downstream ``recover-from-source`` command
can copy the files back into ``~/.aletheia/knowledge/…`` and refresh
``file_path``. Rows that resolve nowhere are the ones eligible for a deliberate
downgrade via ``downgrade_unindexable_high_confidence_items``.

Scope note: rows whose DB ``content`` is already populated are excluded — they
would be handled by the ``backfill-content`` path, not by recovering markdown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from aletheia.config.loader import get_data_dir

logger = logging.getLogger("aletheia.knowledge.recovery")


@dataclass
class RecoveryCandidate:
    item_id: str
    item_type: str
    title: str
    original_path: str
    resolved_path: str | None
    strategy: str  # "existing" | "legacy_migration" | "basename_scan" | ""

    def as_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "item_type": self.item_type,
            "title": self.title,
            "original_path": self.original_path,
            "resolved_path": self.resolved_path,
            "strategy": self.strategy,
        }


def _candidate_roots(extra_roots: list[Path] | None = None) -> list[Path]:
    """Return existing candidate roots for knowledge markdown files.

    Order matters — the runtime data dir wins first so a partially-populated
    ``~/.aletheia/knowledge`` is preferred over a legacy repository checkout.
    """
    roots: list[Path] = [
        get_data_dir() / "knowledge",
        Path.home() / "Projects" / "aletheia" / "data" / "knowledge",
        Path.home() / "Library" / "Application Support" / "aletheia" / "knowledge",
    ]
    for extra in extra_roots or []:
        roots.append(extra)
    return [r for r in roots if r.exists() and r.is_dir()]


def _legacy_relative(file_path: str) -> Path | None:
    """Extract the fragment after ``knowledge`` from a legacy path string.

    Accepts both Windows and POSIX literals. Returns ``None`` when the string
    does not contain a ``knowledge`` segment; the caller falls back to a
    basename search.
    """
    for path_type in (PureWindowsPath, PurePosixPath):
        try:
            parts = path_type(file_path).parts
        except Exception:
            continue
        lowered = [p.lower() for p in parts]
        if "knowledge" not in lowered:
            continue
        idx = lowered.index("knowledge")
        rel_parts = parts[idx + 1 :]
        if rel_parts:
            return Path(*rel_parts)
    return None


def resolve_orphan_body(
    file_path: str | None, roots: list[Path]
) -> tuple[Path | None, str]:
    """Locate the on-disk body for a single ``file_path``.

    Returns a ``(resolved_path, strategy)`` tuple. ``strategy`` records how the
    match was made so operators can audit ambiguous resolutions.
    """
    if not file_path:
        return None, ""

    try:
        direct = Path(file_path)
        if direct.is_file():
            return direct, "existing"
    except Exception:
        pass

    rel = _legacy_relative(file_path)
    if rel is not None:
        for root in roots:
            candidate = (root / rel).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                continue
            if candidate.is_file():
                return candidate, "legacy_migration"

    basename = Path(file_path.replace("\\", "/")).name
    if not basename:
        return None, ""
    hits: list[Path] = []
    for root in roots:
        try:
            for hit in root.rglob(basename):
                if hit.is_file():
                    hits.append(hit)
                    if len(hits) > 1:
                        break
        except Exception:
            continue
        if len(hits) > 1:
            break
    if len(hits) == 1:
        return hits[0], "basename_scan"
    return None, ""


def scan_orphan_bodies(
    extra_roots: list[Path] | None = None,
    limit: int | None = None,
) -> dict:
    """Report on high-confidence rows whose body is currently unreachable.

    Returns a summary suitable for JSON output plus a per-item candidate list.
    Never mutates the database.
    """
    from aletheia.storage.sqlite import get_db

    db = get_db()
    roots = _candidate_roots(extra_roots)
    rows = db.execute(
        "SELECT id, item_type, title, file_path, content "
        "FROM knowledge_items "
        "WHERE status='active' AND confidence='high_confidence' "
        "AND COALESCE(TRIM(content), '') = '' "
        "ORDER BY created_at DESC"
    ).fetchall()

    if limit is not None:
        rows = rows[:limit]

    candidates: list[RecoveryCandidate] = []
    by_type: dict[str, dict[str, int]] = {}
    by_strategy: dict[str, int] = {}
    for row in rows:
        resolved, strategy = resolve_orphan_body(row["file_path"], roots)
        item_type = row["item_type"] or "unknown"
        stats = by_type.setdefault(
            item_type, {"total": 0, "recoverable": 0}
        )
        stats["total"] += 1
        if resolved is not None:
            stats["recoverable"] += 1
            by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
        candidates.append(
            RecoveryCandidate(
                item_id=row["id"],
                item_type=item_type,
                title=row["title"] or "",
                original_path=row["file_path"] or "",
                resolved_path=str(resolved) if resolved else None,
                strategy=strategy,
            )
        )

    total = len(candidates)
    recoverable = sum(1 for c in candidates if c.resolved_path)
    return {
        "roots_scanned": [str(r) for r in roots],
        "total_orphans": total,
        "recoverable": recoverable,
        "unrecoverable": total - recoverable,
        "by_type": by_type,
        "by_strategy": by_strategy,
        "candidates": [c.as_dict() for c in candidates],
    }
