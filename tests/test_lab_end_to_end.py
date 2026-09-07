"""Canonical Lab runner proof against isolated Main and Lab SQLite stores."""

from datetime import datetime, timezone


def test_lab_runner_captures_once_and_keeps_checkpoint_and_legacy_evidence():
    from aletheia.counterfactual.filter_experiments import register_replay_ready_experiments
    from aletheia.observation_lab.canonical import set_authority_source
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.observation_lab.runner import run_lab_windows
    from aletheia.storage.sqlite import get_db
    from tests.test_filter_shadow_experiments import _ready_ranker_replay, _seed_ranker_cohort

    set_authority_source("lab", actor="test", reason="runner cutover")
    start = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    version = _seed_ranker_cohort(start, count=25)
    register_replay_ready_experiments([_ready_ranker_replay(filter_version=version)], now=start)
    lab = get_lab_db()
    root = lab.execute("SELECT observation_id FROM observation_windows WHERE kind='filter_shadow'").fetchone()[0]

    first = run_lab_windows()
    assert first["windows_errored"] == 0
    assert first["joint_checkpoint"]["ok"] is True
    assert lab.execute(
        "SELECT COUNT(*) FROM observation_exposures WHERE observation_id=?", (root,),
    ).fetchone()[0] == 50
    assert lab.execute(
        "SELECT COUNT(*) FROM observation_evaluations WHERE observation_id=?", (root,),
    ).fetchone()[0] == 1
    assert get_db().execute("SELECT COUNT(*) FROM filter_shadow_evaluations").fetchone()[0] == 0
    assert lab.execute("SELECT COUNT(*) FROM ufc_shadow_equivalence").fetchone()[0] == 0

    second = run_lab_windows()
    assert second["windows_errored"] == 0
    assert second["joint_checkpoint"]["ok"] is True
    assert lab.execute(
        "SELECT COUNT(*) FROM observation_exposures WHERE observation_id=?", (root,),
    ).fetchone()[0] == 50


def test_lab_runner_rejects_legacy_authority_without_advancing_old_experiment():
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.observation_lab.runner import run_lab_windows
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO filter_shadow_experiments "
        "(id, experiment_key, created_at, started_at, filter_name, filter_version, stage, "
        "scanner_family, direction, market_type, horizon, control_parameter, treatment_parameter, status, "
        "min_samples, min_coverage, min_edge_delta_pct, source_replay, production_mutation_allowed) "
        "VALUES ('legacy', 'legacy', '2026-01-01', '2026-01-01', 'family_ttl', 'v1', "
        "'shadow', 'momentum', 'long', 'perp', '4h', '{}', '{}', 'collecting', "
        "10, 0.5, 0, '{}', 0)"
    )
    before = tuple(db.execute("SELECT * FROM filter_shadow_experiments").fetchone())
    result = run_lab_windows()
    projection = result["results"]["filter_shadow_evaluate"]
    assert projection["status"] == "error"
    assert "authority_source_unavailable" in projection["error"]
    assert tuple(db.execute("SELECT * FROM filter_shadow_experiments").fetchone()) == before
    assert db.execute("SELECT COUNT(*) FROM filter_shadow_evaluations").fetchone()[0] == 0
    assert get_lab_db().execute("SELECT COUNT(*) FROM ufc_shadow_equivalence").fetchone()[0] == 0
