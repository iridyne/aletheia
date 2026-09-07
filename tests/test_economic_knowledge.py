"""Archived positive verdicts never become current Lab qualifications."""


def test_legacy_positive_verdict_is_preserved_without_current_authority():
    from aletheia.learning.economic_knowledge import (
        assess_economic_knowledge, project_current_economic_knowledge,
        project_economic_knowledge_as_of,
    )
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO learning_contribution_verdicts "
        "(verdict_id, experiment_id, hypothesis_id, window_start, window_end, "
        "unique_treatment_n, unique_control_n, verdict, reason, created_at) "
        "VALUES ('archived-positive', 'old-exp', 'old-hyp', '2026-08-01', "
        "'2026-08-08', 50, 50, 'positive', 'historical evidence', '2026-08-08')"
    )
    before = dict(db.execute("SELECT * FROM learning_contribution_verdicts").fetchone())
    changes = db.total_changes
    assessment = assess_economic_knowledge('archived-positive')
    assert assessment.status == 'unavailable'
    assert assessment.fact is None
    assert project_current_economic_knowledge() == []
    assert project_economic_knowledge_as_of('2026-09-01T00:00:00+00:00') == []
    assert dict(db.execute("SELECT * FROM learning_contribution_verdicts").fetchone()) == before
    assert db.total_changes == changes
