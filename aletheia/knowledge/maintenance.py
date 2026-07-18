"""One-time knowledge-store maintenance for the Round-1 anti-hallucination cleanup.

`reclassify_legacy_reviews()` — the pre-C1 reviewer graded every paper fill's
"execution quality" and stamped `high_confidence` on it, which is the hallucination
source identified in LEARNING_LOOP_AUDIT_2026-06-26. Three code fixes already stop new
pollution and stop it reaching decisions:
  - C1: the reviewer now emits `medium_confidence`;
  - B1: the ranker's `_lesson_penalty` no longer retrieves `post_trade_review`;
  - dbce73e: the advisory path (`router._ITEM_TYPES_BY_ROLE`) excludes it for every role.

This function applies C1's corrected judgment *retroactively* to the ~1.8k legacy
`high_confidence` `post_trade_review` rows accumulated during Round 1: it downgrades
their stored confidence to `medium_confidence` so they fall out of the
`high_confidence`-only retrieval gate (and out of the FTS index, which `reindex_all`
only populates with high_confidence items).

It does NOT delete anything — the audit trail and the items themselves stay intact;
only the retrieval-confidence label changes, so it is reversible. It is idempotent
(re-running matches 0 rows). This is the data-side counterpart to the code fixes above
and is the only thing that immediately de-skews the live `loop.retrieval_pool` probe;
without it the skew is harmless (no decision path reads reviews) but persists until
Round 2's full-chain trade_case_study volume dilutes it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("aletheia.knowledge.maintenance")

_LEGACY_TYPE = "post_trade_review"
_FROM = "high_confidence"
_TO = "medium_confidence"


def reclassify_legacy_reviews(*, dry_run: bool = False) -> dict:
    """Downgrade legacy high_confidence post_trade_review rows to medium_confidence.

    Returns a summary dict: {matched, reclassified, remaining_high, reindexed, dry_run}.
    Never raises into the caller for the count probe; the UPDATE itself is allowed to
    surface errors so a partial mutation is visible rather than silently swallowed.
    """
    from aletheia.storage.sqlite import get_db
    from aletheia.knowledge.retrieval import reindex_all

    db = get_db()
    matched = db.execute(
        "SELECT COUNT(*) FROM knowledge_items "
        "WHERE item_type=? AND confidence=?",
        (_LEGACY_TYPE, _FROM),
    ).fetchone()[0]

    if dry_run or matched == 0:
        return {"matched": matched, "reclassified": 0, "remaining_high": matched,
                "reindexed": None, "dry_run": dry_run}

    # SQLite concurrency invariant (CLAUDE.md): capture in_transaction BEFORE the write,
    # commit only if we opened the tx — sqlite3 auto-opens one on UPDATE.
    was_in_tx = db.in_transaction
    db.execute(
        "UPDATE knowledge_items SET confidence=? WHERE item_type=? AND confidence=?",
        (_TO, _LEGACY_TYPE, _FROM),
    )
    if not was_in_tx:
        db.commit()

    # Rebuild FTS so the downgraded rows leave the index (reindex_all only indexes
    # high_confidence + active items).
    reindexed = reindex_all()

    remaining_high = db.execute(
        "SELECT COUNT(*) FROM knowledge_items "
        "WHERE item_type=? AND confidence=?",
        (_LEGACY_TYPE, _FROM),
    ).fetchone()[0]

    summary = {"matched": matched, "reclassified": matched - remaining_high,
               "remaining_high": remaining_high, "reindexed": reindexed, "dry_run": False}
    logger.info("reclassify_legacy_reviews: %s", summary)
    return summary


def archive_synthetic_trade_case_studies(*, dry_run: bool = False) -> dict:
    """Archive historical synthetic trade case studies and rebuild FTS.

    New case-study generation already skips synthetic close reasons. This maintenance
    entry applies that rule retroactively to old rows that were created before the
    filter existed. It never deletes knowledge; matching rows move from active to
    archived so they disappear from active Learning reads and from the high-confidence
    FTS retrieval pool after reindexing.
    """
    from aletheia.storage.sqlite import get_db
    from aletheia.knowledge.retrieval import reindex_all
    from aletheia.analytics.close_reasons import SYNTHETIC_CLOSE_REASONS

    db = get_db()
    like_clauses: list[str] = []
    params: list[str] = []
    for column in ("title", "content", "tags", "file_path"):
        for reason in SYNTHETIC_CLOSE_REASONS:
            like_clauses.append(f"COALESCE({column}, '') LIKE ?")
            params.append(f"%{reason}%")

    predicate = " OR ".join(like_clauses)
    matched = db.execute(
        "SELECT COUNT(*) FROM knowledge_items "
        "WHERE item_type='trade_case_study' AND status='active' "
        f"AND ({predicate})",
        tuple(params),
    ).fetchone()[0]

    if dry_run or matched == 0:
        return {
            "matched": matched,
            "archived": 0,
            "remaining_active": matched,
            "reindexed": None,
            "dry_run": dry_run,
        }

    was_in_tx = db.in_transaction
    db.execute(
        "UPDATE knowledge_items SET status='archived', updated_at=? "
        "WHERE item_type='trade_case_study' AND status='active' "
        f"AND ({predicate})",
        (datetime.now(timezone.utc).isoformat(), *params),
    )
    if not was_in_tx:
        db.commit()

    remaining_active = db.execute(
        "SELECT COUNT(*) FROM knowledge_items "
        "WHERE item_type='trade_case_study' AND status='active' "
        f"AND ({predicate})",
        tuple(params),
    ).fetchone()[0]
    reindexed = reindex_all()

    summary = {
        "matched": matched,
        "archived": matched - remaining_active,
        "remaining_active": remaining_active,
        "reindexed": reindexed,
        "dry_run": False,
    }
    logger.info("archive_synthetic_trade_case_studies: %s", summary)
    return summary


def downgrade_unindexable_high_confidence_items(*, dry_run: bool = False) -> dict:
    """Downgrade active high-confidence knowledge rows that have no retrievable body.

    A high-confidence knowledge item is expected to be reconstructable into FTS from
    either its markdown file or the DB `content` column. Rows with neither are audit
    stubs, not learnable material. This keeps the row intact but changes confidence
    to medium_confidence so FTS coverage probes stop counting it as eligible.
    """
    from aletheia.storage.sqlite import get_db
    from aletheia.knowledge.retrieval import reindex_all

    db = get_db()
    rows = db.execute(
        "SELECT id, item_type, file_path, content FROM knowledge_items "
        "WHERE status='active' AND confidence='high_confidence'"
    ).fetchall()

    from aletheia.knowledge.retrieval import _read_body

    matched_ids: list[str] = []
    by_type: dict[str, int] = {}
    for row in rows:
        # Reuse retrieval._read_body so the Windows/POSIX legacy path migration
        # logic is applied — otherwise rows that could still be recovered via
        # scan-recovery-sources would be silently downgraded.
        body = _read_body(row["file_path"])
        if body.strip() or str(row["content"] or "").strip():
            continue
        matched_ids.append(row["id"])
        item_type = row["item_type"] or "unknown"
        by_type[item_type] = by_type.get(item_type, 0) + 1

    if dry_run or not matched_ids:
        return {
            "matched": len(matched_ids),
            "downgraded": 0,
            "remaining_high": len(matched_ids),
            "by_type": by_type,
            "reindexed": None,
            "dry_run": dry_run,
        }

    placeholders = ", ".join("?" for _ in matched_ids)
    was_in_tx = db.in_transaction
    db.execute(
        f"UPDATE knowledge_items SET confidence='medium_confidence', updated_at=? "
        f"WHERE id IN ({placeholders})",
        (datetime.now(timezone.utc).isoformat(), *matched_ids),
    )
    if not was_in_tx:
        db.commit()

    remaining_high = 0
    for item_id in matched_ids:
        row = db.execute(
            "SELECT confidence FROM knowledge_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row and row["confidence"] == "high_confidence":
            remaining_high += 1

    reindexed = reindex_all()
    summary = {
        "matched": len(matched_ids),
        "downgraded": len(matched_ids) - remaining_high,
        "remaining_high": remaining_high,
        "by_type": by_type,
        "reindexed": reindexed,
        "dry_run": False,
    }
    logger.info("downgrade_unindexable_high_confidence_items: %s", summary)
    return summary


def backfill_content_from_disk(*, dry_run: bool = False) -> dict:
    """Copy Markdown bodies from disk into ``knowledge_items.content``.

    Complements Phase E1's dual-write writers: pre-existing rows still have an
    empty ``content`` column but readable ``file_path``. This backfill closes
    that gap so a subsequent runtime move / disk cleanup cannot break FTS.
    Idempotent, additive — never truncates or hard-deletes.
    """
    from aletheia.storage.sqlite import get_db
    from aletheia.knowledge.retrieval import _read_body, reindex_all

    db = get_db()
    rows = db.execute(
        "SELECT id, file_path FROM knowledge_items "
        "WHERE status='active' AND COALESCE(TRIM(content), '') = '' "
        "AND file_path IS NOT NULL AND TRIM(file_path) != ''"
    ).fetchall()

    to_update: list[tuple[str, str]] = []
    for row in rows:
        body = _read_body(row["file_path"])
        if not body.strip():
            continue
        to_update.append((row["id"], body))

    if dry_run or not to_update:
        return {
            "matched": len(to_update),
            "backfilled": 0,
            "reindexed": None,
            "dry_run": dry_run,
        }

    was_in_tx = db.in_transaction
    for item_id, body in to_update:
        db.execute(
            "UPDATE knowledge_items SET content=?, updated_at=? WHERE id=?",
            (body, datetime.now(timezone.utc).isoformat(), item_id),
        )
    if not was_in_tx:
        db.commit()

    reindexed = reindex_all()
    summary = {
        "matched": len(to_update),
        "backfilled": len(to_update),
        "reindexed": reindexed,
        "dry_run": False,
    }
    logger.info("backfill_content_from_disk: %s", summary)
    return summary


def downgrade_unattributed_trade_case_studies(*, dry_run: bool = False) -> dict:
    """Downgrade high-confidence trade cases that lack scanner_family attribution.

    A high-confidence trade_case_study is used as structured learning evidence by
    retrieval and opportunity ranking. If the case has no real scanner_family in
    frontmatter, the historical causal chain is incomplete; guessing from symbol,
    direction, or prose would fabricate attribution. Keep the row active/auditable,
    but move it out of the high-confidence learning pool.
    """
    from aletheia.storage.sqlite import get_db
    from aletheia.knowledge.frontmatter import parse_frontmatter, parse_frontmatter_text
    from aletheia.knowledge.retrieval import reindex_all

    db = get_db()
    rows = db.execute(
        "SELECT id, file_path, content FROM knowledge_items "
        "WHERE item_type='trade_case_study' "
        "AND status='active' AND confidence='high_confidence'"
    ).fetchall()

    matched_ids: list[str] = []
    for row in rows:
        meta = {}
        file_path = row["file_path"]
        if file_path:
            try:
                path = Path(file_path)
                if path.exists():
                    meta, _ = parse_frontmatter(path)
            except Exception:
                meta = {}
        if not meta and str(row["content"] or "").strip():
            meta, _ = parse_frontmatter_text(str(row["content"]))
        if not str(meta.get("scanner_family") or "").strip():
            matched_ids.append(row["id"])

    if dry_run or not matched_ids:
        return {
            "matched": len(matched_ids),
            "downgraded": 0,
            "remaining_high": len(matched_ids),
            "reindexed": None,
            "dry_run": dry_run,
        }

    placeholders = ", ".join("?" for _ in matched_ids)
    was_in_tx = db.in_transaction
    db.execute(
        f"UPDATE knowledge_items SET confidence='medium_confidence', updated_at=? "
        f"WHERE id IN ({placeholders})",
        (datetime.now(timezone.utc).isoformat(), *matched_ids),
    )
    if not was_in_tx:
        db.commit()

    remaining_high = db.execute(
        "SELECT COUNT(*) FROM knowledge_items "
        "WHERE item_type='trade_case_study' "
        "AND status='active' AND confidence='high_confidence' "
        f"AND id IN ({placeholders})",
        tuple(matched_ids),
    ).fetchone()[0]
    reindexed = reindex_all()

    summary = {
        "matched": len(matched_ids),
        "downgraded": len(matched_ids) - remaining_high,
        "remaining_high": remaining_high,
        "reindexed": reindexed,
        "dry_run": False,
    }
    logger.info("downgrade_unattributed_trade_case_studies: %s", summary)
    return summary
