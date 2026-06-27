"""Tests for the Round-1 anti-hallucination knowledge-store cleanup.

Locks reclassify_legacy_reviews (aletheia.knowledge.maintenance): a legacy
high_confidence post_trade_review must be downgraded to medium_confidence and thereby
fall out of the high_confidence retrieval gate / FTS index, without being deleted, and
the operation must be idempotent.
"""

from tests.test_experience_loop import setup_env


def test_reclassify_legacy_reviews_downgrades_and_deindexes(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_post_trade_review
    from aletheia.knowledge.retrieval import retrieve_lessons
    from aletheia.knowledge.maintenance import reclassify_legacy_reviews
    from aletheia.storage.sqlite import get_db

    # Seed a review in the LEGACY state (pre-C1 reviewer stamped high_confidence).
    create_post_trade_review(
        title="Post-Trade Review: AVAX/USDT long",
        content=(
            "AVAX/USDT momentum entry stopped out at a drawdown; execution quality was "
            "poor and the thesis failed to play out across the whole 4h holding window."
        ),
        confidence="high_confidence",
    )

    # Precondition: indexed and retrievable through the high_confidence gate.
    assert retrieve_lessons("AVAX momentum drawdown stopped", item_types=["post_trade_review"]), (
        "precondition: the high_confidence review must be retrievable before cleanup"
    )

    summary = reclassify_legacy_reviews()
    assert summary["matched"] >= 1
    assert summary["reclassified"] == summary["matched"]
    assert summary["remaining_high"] == 0

    # Item still exists (not deleted) — only its confidence label changed.
    db = get_db()
    row = db.execute(
        "SELECT confidence FROM knowledge_items WHERE item_type='post_trade_review'"
    ).fetchone()
    assert row is not None, "reclassify must NOT delete the item (audit trail intact)"
    assert row["confidence"] == "medium_confidence"

    # Downgraded → no longer passes the high_confidence retrieval gate.
    assert retrieve_lessons("AVAX momentum drawdown stopped", item_types=["post_trade_review"]) == []


def test_reclassify_legacy_reviews_idempotent_and_dry_run(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_post_trade_review
    from aletheia.knowledge.maintenance import reclassify_legacy_reviews

    create_post_trade_review(
        title="Post-Trade Review: ETH short",
        content=(
            "ETH/USDT short thesis on a momentum fade; the position closed at a small loss "
            "after the breakout reclaimed the range and execution slippage widened the cost."
        ),
        confidence="high_confidence",
    )

    # dry_run reports the match without mutating.
    dry = reclassify_legacy_reviews(dry_run=True)
    assert dry["dry_run"] is True and dry["matched"] >= 1 and dry["reclassified"] == 0

    reclassify_legacy_reviews()
    # Second real run finds nothing left to do.
    again = reclassify_legacy_reviews()
    assert again["matched"] == 0 and again["reclassified"] == 0
