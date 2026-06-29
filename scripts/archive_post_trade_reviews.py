"""archive_post_trade_reviews.py — mark old post_trade_review knowledge items as archived.

These were written by the pre-fix post_trade_reviewer (before 2026-06-25) and
contained hallucinated execution quality instead of real PnL data.  The ranker
no longer reads them (switched to trade_case_study), but they may pollute other
knowledge searches.

Usage:
    uv run python scripts/archive_post_trade_reviews.py [--dry-run] [--before YYYY-MM-DD]

Flags:
    --dry-run   Print counts only; make no DB changes.
    --before    Only archive items created before this date (default: 2026-06-25).

This script never deletes rows — it only sets knowledge_items.status = 'archived'.
Run it from the repo root after reviewing the dry-run output.
"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Count only, no changes")
    parser.add_argument("--before", default="2026-06-25", help="Archive items created before this date (YYYY-MM-DD)")
    args = parser.parse_args()

    from aletheia.storage.sqlite import get_db
    from aletheia.storage.migrations import run_migrations

    run_migrations()
    db = get_db()

    cutoff = args.before + "T00:00:00"
    rows = db.execute(
        "SELECT id, title, created_at FROM knowledge_items"
        " WHERE item_type = 'post_trade_review' AND created_at < ?"
        " AND (status IS NULL OR status != 'archived')"
        " ORDER BY created_at",
        (cutoff,),
    ).fetchall()

    print(f"Found {len(rows)} post_trade_review items before {args.before}")
    if not rows:
        print("Nothing to archive.")
        return

    for r in rows[:5]:
        print(f"  {r['id']} | {r['created_at']} | {r['title'][:60]}")
    if len(rows) > 5:
        print(f"  ... and {len(rows) - 5} more")

    if args.dry_run:
        print("[dry-run] No changes made.")
        return

    confirm = input(f"\nArchive {len(rows)} items? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    db.execute(
        "UPDATE knowledge_items SET status = 'archived'"
        " WHERE item_type = 'post_trade_review' AND created_at < ?"
        " AND (status IS NULL OR status != 'archived')",
        (cutoff,),
    )
    db.commit()
    print(f"Archived {len(rows)} items.")


if __name__ == "__main__":
    main()
