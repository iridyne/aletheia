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


def test_archive_synthetic_trade_case_studies_archives_and_deindexes(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_trade_case_study
    from aletheia.knowledge.maintenance import archive_synthetic_trade_case_studies
    from aletheia.knowledge.retrieval import retrieve_lessons
    from aletheia.storage.sqlite import get_db

    synthetic = create_trade_case_study(
        title="Case: BTC/USDT long (reconciled_gone)",
        content=(
            "BTC/USDT long was closed by reconciled_gone during state reconciliation. "
            "This synthetic close reason should not teach the learning loop a trade lesson."
        ),
        confidence="high_confidence",
    )
    real = create_trade_case_study(
        title="Case: ETH/USDT long (stop_loss)",
        content=(
            "ETH/USDT long stopped out after momentum failed. This is a real closed trade "
            "case study and should stay active for the learning loop."
        ),
        confidence="high_confidence",
    )

    assert retrieve_lessons("BTC reconciled_gone", item_types=["trade_case_study"])

    summary = archive_synthetic_trade_case_studies()
    assert summary["matched"] == 1
    assert summary["archived"] == 1
    assert summary["remaining_active"] == 0
    assert summary["reindexed"] >= 1

    db = get_db()
    synthetic_row = db.execute(
        "SELECT status FROM knowledge_items WHERE id = ?", (synthetic.id,)
    ).fetchone()
    real_row = db.execute("SELECT status FROM knowledge_items WHERE id = ?", (real.id,)).fetchone()
    assert synthetic_row["status"] == "archived"
    assert real_row["status"] == "active"
    assert retrieve_lessons("BTC reconciled_gone", item_types=["trade_case_study"]) == []


def test_archive_synthetic_trade_case_studies_dry_run_and_idempotent(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_trade_case_study
    from aletheia.knowledge.maintenance import archive_synthetic_trade_case_studies

    create_trade_case_study(
        title="Case: SOL/USDT short (demo_validation_cleanup)",
        content=(
            "SOL/USDT short was closed by demo_validation_cleanup during validation cleanup. "
            "This synthetic close reason must be archived rather than used as experience."
        ),
        confidence="medium_confidence",
    )

    dry = archive_synthetic_trade_case_studies(dry_run=True)
    assert dry["dry_run"] is True
    assert dry["matched"] == 1
    assert dry["archived"] == 0

    done = archive_synthetic_trade_case_studies()
    assert done["matched"] == 1
    assert done["archived"] == 1

    again = archive_synthetic_trade_case_studies()
    assert again["matched"] == 0
    assert again["archived"] == 0


def test_archive_synthetic_trade_case_studies_uses_shared_close_reason_list(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_trade_case_study
    from aletheia.knowledge.maintenance import archive_synthetic_trade_case_studies

    for reason in ("units_bug_cleanup", "retry_after_fix"):
        create_trade_case_study(
            title=f"Case: ETH/USDT short ({reason})",
            content=(
                f"ETH/USDT short was closed by {reason}. This administrative close "
                "reason is not a genuine trade outcome and must leave active Learning."
            ),
            confidence="high_confidence",
        )

    summary = archive_synthetic_trade_case_studies()
    assert summary["matched"] == 2
    assert summary["archived"] == 2


def test_downgrade_unindexable_high_confidence_items(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.maintenance import downgrade_unindexable_high_confidence_items
    from aletheia.storage.models import KnowledgeItem
    from aletheia.storage.repositories import insert_knowledge_item
    from aletheia.storage.sqlite import get_db

    empty = KnowledgeItem(
        item_type="operator_assessment",
        title="Operator assessment without persisted body",
        confidence="high_confidence",
        file_path=None,
        content=None,
    )
    good = KnowledgeItem(
        item_type="operator_assessment",
        title="Operator assessment with body",
        confidence="high_confidence",
        file_path=None,
        content="Operator assessment with sufficient persisted body for FTS rebuild.",
    )
    insert_knowledge_item(empty)
    insert_knowledge_item(good)

    dry = downgrade_unindexable_high_confidence_items(dry_run=True)
    assert dry["matched"] == 1
    assert dry["downgraded"] == 0
    assert dry["by_type"] == {"operator_assessment": 1}

    summary = downgrade_unindexable_high_confidence_items()
    assert summary["matched"] == 1
    assert summary["downgraded"] == 1
    assert summary["remaining_high"] == 0
    assert summary["reindexed"] >= 1

    db = get_db()
    empty_row = db.execute("SELECT confidence FROM knowledge_items WHERE id=?", (empty.id,)).fetchone()
    good_row = db.execute("SELECT confidence FROM knowledge_items WHERE id=?", (good.id,)).fetchone()
    assert empty_row["confidence"] == "medium_confidence"
    assert good_row["confidence"] == "high_confidence"

    again = downgrade_unindexable_high_confidence_items()
    assert again["matched"] == 0
    assert again["downgraded"] == 0
