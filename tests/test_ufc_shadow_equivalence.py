"""Retired UFC equivalence evidence remains readable without recomputation."""


def test_historical_equivalence_latest_record_is_read_without_writes():
    from extensions.observation_lab.db import get_lab_db
    from extensions.observation_lab.ufc_shadow import decode_equivalence_result, latest_equivalence_record

    db = get_lab_db()
    for identity, timestamp, matched, note in (
        ("old", "2026-01-01", 1, None),
        ("new", "2026-01-02", 0, "no_exact_current_verdict"),
    ):
        db.execute(
            "INSERT INTO ufc_shadow_equivalence "
            "(id, observation_id, experiment_id, cohort_key, checked_at, matched, notes) "
            "VALUES (?, 'history', 'legacy-experiment', 'legacy-cohort', ?, ?, ?)",
            (identity, timestamp, matched, note),
        )
    before = [tuple(row) for row in db.execute("SELECT * FROM ufc_shadow_equivalence ORDER BY id")]
    latest = latest_equivalence_record("history")
    assert latest["id"] == "new"
    assert decode_equivalence_result(latest) == "skipped"
    assert [tuple(row) for row in db.execute("SELECT * FROM ufc_shadow_equivalence ORDER BY id")] == before


def test_historical_equivalence_read_does_not_create_missing_lab():
    from extensions.observation_lab.db import lab_db_path
    from extensions.observation_lab.ufc_shadow import latest_equivalence_record

    assert not lab_db_path().exists()
    assert latest_equivalence_record("history") is None
    assert not lab_db_path().exists()
