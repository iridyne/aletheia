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
