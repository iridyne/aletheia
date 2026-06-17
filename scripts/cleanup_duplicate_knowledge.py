"""One-time cleanup: deduplicate historical knowledge files and DB rows.

Usage:
    uv run python scripts/cleanup_duplicate_knowledge.py [--dry-run] [--backfill] [--delete-orphans]

Modes:
  (default)         Dedup DB rows + backfill content_hash (safe, no file deletion)
  --backfill        Also backfill orphan disk files into the DB (§9 fix)
  --delete-orphans  Also delete disk files that have no DB row (DESTRUCTIVE)
  --dry-run         Show what would happen without making changes

Steps:
1. Backfill content_hash for DB rows missing it
2. Deduplicate: keep oldest per (item_type, content_hash), archive rest + delete their files
3. Backfill orphan files into DB (--backfill) OR delete them (--delete-orphans)
4. Report stats

Safe to run multiple times (idempotent).
"""

import hashlib
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aletheia.storage.sqlite import get_db

_MIN_CONTENT_LENGTH = 80
_PLACEHOLDER_PATTERNS = [
    "Content here",
    "(LLM unavailable",
    "Synthesized 3 research notes",
    "LLM unavailable for synthesis",
]

# Map subdirectory name → item_type
_DIR_TO_TYPE = {
    "case_studies": "trade_case_study",
    "research_notes": "research_note",
    "dreaming_logs": "dreaming_log",
    "post_trade_reviews": "post_trade_review",
    "daily_reviews": "daily_review",
    "guards": "trading_guard",
    "guard_candidates": "guard_candidate",
    "strategies": "strategy_memory",
}


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.strip().encode()).hexdigest()[:16]


def _is_quality_pass(content: str) -> bool:
    """Reject placeholder or near-empty content."""
    if len(content.strip()) < _MIN_CONTENT_LENGTH:
        return False
    return not any(p in content for p in _PLACEHOLDER_PATTERNS)


def _infer_item_type(path: Path, knowledge_dir: Path) -> str | None:
    """Infer item_type from the subdirectory name."""
    try:
        relative = path.relative_to(knowledge_dir)
        parts = relative.parts
        if parts:
            return _DIR_TO_TYPE.get(parts[0])
    except ValueError:
        pass
    return None


def _extract_title(content: str, path: Path) -> str:
    """Extract title from first markdown heading or filename."""
    for line in content.split("\n")[:10]:
        if line.startswith("# "):
            return line[2:].strip()[:200]
    return path.stem.replace("_", " ").replace("-", " ")[:200]


def main():
    dry_run = "--dry-run" in sys.argv
    do_backfill = "--backfill" in sys.argv
    do_delete_orphans = "--delete-orphans" in sys.argv

    if dry_run:
        print("[DRY RUN] No changes will be made.\n")

    db = get_db()

    # Step 1: Backfill content_hash for rows missing it
    print("=== Step 1: Backfill content_hash ===")
    rows_without_hash = db.execute(
        "SELECT id, file_path, item_type FROM knowledge_items WHERE content_hash IS NULL"
    ).fetchall()
    backfilled = 0
    for row in rows_without_hash:
        file_path = row[1]
        if file_path and Path(file_path).exists():
            try:
                content = Path(file_path).read_text(encoding="utf-8")
                ch = _content_hash(content)
                if not dry_run:
                    db.execute(
                        "UPDATE knowledge_items SET content_hash = ? WHERE id = ?",
                        (ch, row[0]),
                    )
                backfilled += 1
            except Exception:
                pass
    if not dry_run:
        db.commit()
    print(f"  Backfilled: {backfilled}/{len(rows_without_hash)} rows\n")

    # Step 2: Find duplicates by (item_type, content_hash)
    print("=== Step 2: Deduplicate by content_hash ===")
    dupes = db.execute("""
        SELECT item_type, content_hash, COUNT(*) as cnt
        FROM knowledge_items
        WHERE content_hash IS NOT NULL AND status = 'active'
        GROUP BY item_type, content_hash
        HAVING cnt > 1
        ORDER BY cnt DESC
    """).fetchall()
    print(f"  Duplicate groups found: {len(dupes)}")

    archived_count = 0
    files_removed = 0
    for item_type, content_hash, cnt in dupes:
        group = db.execute(
            "SELECT id, file_path, created_at FROM knowledge_items "
            "WHERE item_type = ? AND content_hash = ? AND status = 'active' "
            "ORDER BY created_at ASC",
            (item_type, content_hash),
        ).fetchall()
        for row in group[1:]:
            item_id, file_path = row[0], row[1]
            if not dry_run:
                db.execute(
                    "UPDATE knowledge_items SET status = 'archived' WHERE id = ?",
                    (item_id,),
                )
            archived_count += 1
            if file_path:
                p = Path(file_path)
                if p.exists():
                    if not dry_run:
                        p.unlink()
                    files_removed += 1

    if not dry_run:
        db.commit()
    print(f"  Archived: {archived_count} duplicate DB rows")
    print(f"  Removed: {files_removed} duplicate files\n")

    # Step 3: Handle orphan files (on disk but not in DB)
    print("=== Step 3: Orphan file handling ===")
    from aletheia.config.loader import get_data_dir
    knowledge_dir = get_data_dir() / "knowledge"
    if not knowledge_dir.exists():
        print("  Knowledge dir not found, skipping.\n")
    else:
        all_files = set(str(p) for p in knowledge_dir.rglob("*.md"))
        db_files = set(
            row[0] for row in db.execute(
                "SELECT file_path FROM knowledge_items WHERE file_path IS NOT NULL"
            ).fetchall()
            if row[0]
        )
        orphans = all_files - db_files
        print(f"  Orphan files found: {len(orphans)}")

        if do_backfill:
            print("  Mode: BACKFILL (inserting quality-passing orphans into DB)")
            inserted = 0
            skipped_quality = 0
            skipped_dup = 0
            skipped_type = 0
            for orphan_path_str in sorted(orphans):
                p = Path(orphan_path_str)
                if not p.exists():
                    continue

                item_type = _infer_item_type(p, knowledge_dir)
                if not item_type:
                    skipped_type += 1
                    continue

                try:
                    content = p.read_text(encoding="utf-8")
                except Exception:
                    continue

                if not _is_quality_pass(content):
                    skipped_quality += 1
                    if not dry_run:
                        p.unlink()
                    continue

                ch = _content_hash(content)
                existing = db.execute(
                    "SELECT 1 FROM knowledge_items WHERE item_type = ? AND content_hash = ? LIMIT 1",
                    (item_type, ch),
                ).fetchone()
                if existing:
                    skipped_dup += 1
                    if not dry_run:
                        p.unlink()
                    continue

                title = _extract_title(content, p)
                item_id = f"backfill_{ch}_{item_type}"

                if not dry_run:
                    now = datetime.now(timezone.utc).isoformat()
                    db.execute(
                        "INSERT INTO knowledge_items "
                        "(id, item_type, title, file_path, confidence, status, content_hash, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (item_id, item_type, title, str(p), "medium_confidence", "active", ch, now),
                    )
                inserted += 1

            if not dry_run:
                db.commit()
            print(f"  Inserted: {inserted}")
            print(f"  Skipped (quality gate): {skipped_quality} (files deleted)")
            print(f"  Skipped (duplicate hash): {skipped_dup} (files deleted)")
            print(f"  Skipped (unknown type): {skipped_type}\n")

        elif do_delete_orphans:
            print("  Mode: DELETE (removing orphan files)")
            orphan_removed = 0
            for orphan_path_str in orphans:
                p = Path(orphan_path_str)
                if p.exists():
                    if not dry_run:
                        p.unlink()
                    orphan_removed += 1
            print(f"  Removed: {orphan_removed}\n")

        else:
            print("  Mode: REPORT ONLY (use --backfill or --delete-orphans to act)")
            # Show breakdown by type
            type_counts: dict[str, int] = {}
            for orphan_path_str in orphans:
                p = Path(orphan_path_str)
                item_type = _infer_item_type(p, knowledge_dir) or "unknown"
                type_counts[item_type] = type_counts.get(item_type, 0) + 1
            for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
                print(f"    {t}: {c}")
            print()

    # Step 4: Summary
    print("=== Summary ===")
    total_active = db.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE status = 'active'"
    ).fetchone()[0]
    total_archived = db.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE status = 'archived'"
    ).fetchone()[0]
    disk_count = len(list(knowledge_dir.rglob("*.md"))) if knowledge_dir.exists() else 0
    print(f"  Active DB items: {total_active}")
    print(f"  Archived DB items: {total_archived}")
    print(f"  Disk files: {disk_count}")
    print(f"  Sync ratio: {total_active}/{disk_count} = {total_active/max(disk_count,1):.0%}")
    if dry_run:
        print("\n[DRY RUN] Rerun without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
