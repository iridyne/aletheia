"""One-time cleanup: deduplicate historical knowledge files and DB rows.

Usage:
    uv run python scripts/cleanup_duplicate_knowledge.py [--dry-run]

What it does:
1. Scans all knowledge_items in DB, groups by (item_type, content_hash)
2. For each group with >1 item: keeps the oldest, archives the rest
3. Removes orphan markdown files whose content_hash matches an archived row
4. Backfills content_hash for rows that lack it (pre-migration data)
5. Reports stats

Safe to run multiple times (idempotent).
"""

import hashlib
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aletheia.storage.sqlite import get_connection


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.strip().encode()).hexdigest()[:16]


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("[DRY RUN] No changes will be made.\n")

    db = get_connection()

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
        # Keep the oldest (smallest created_at), archive the rest
        group = db.execute(
            "SELECT id, file_path, created_at FROM knowledge_items "
            "WHERE item_type = ? AND content_hash = ? AND status = 'active' "
            "ORDER BY created_at ASC",
            (item_type, content_hash),
        ).fetchall()
        # Keep first, archive rest
        for row in group[1:]:
            item_id, file_path = row[0], row[1]
            if not dry_run:
                db.execute(
                    "UPDATE knowledge_items SET status = 'archived' WHERE id = ?",
                    (item_id,),
                )
            archived_count += 1
            # Remove the file if it exists
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

    # Step 3: Find orphan files (on disk but not in DB)
    print("=== Step 3: Orphan file cleanup ===")
    from aletheia.files.markdown import get_knowledge_dir
    knowledge_dir = get_knowledge_dir()
    if knowledge_dir.exists():
        all_files = set(str(p) for p in knowledge_dir.rglob("*.md"))
        db_files = set(
            row[0] for row in db.execute(
                "SELECT file_path FROM knowledge_items WHERE file_path IS NOT NULL AND status = 'active'"
            ).fetchall()
            if row[0]
        )
        orphans = all_files - db_files
        orphan_removed = 0
        for orphan_path in orphans:
            p = Path(orphan_path)
            if p.exists():
                if not dry_run:
                    p.unlink()
                orphan_removed += 1
        print(f"  Orphan files found: {len(orphans)}")
        print(f"  Removed: {orphan_removed}\n")
    else:
        print("  Knowledge dir not found, skipping.\n")

    # Step 4: Summary
    print("=== Summary ===")
    total_active = db.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE status = 'active'"
    ).fetchone()[0]
    total_archived = db.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE status = 'archived'"
    ).fetchone()[0]
    print(f"  Active items remaining: {total_active}")
    print(f"  Archived items: {total_archived}")
    if dry_run:
        print("\n[DRY RUN] Rerun without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
