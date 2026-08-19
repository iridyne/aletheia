"""End-to-end Lab runner integration test (G5).

All Cluster 1-5 tests are unit-level with heavy mocking. This one test
proves the full chain works against a real SQLite pair:

  scheduler tick
    → run_lab_windows()
        → checkpoint save/verify (G3)
        → filter_shadow_evaluate window
            → capture_frozen_units_for (writes lab.db)
            → evaluate_active_shadow_experiments (writes app.db evaluations)
        → ufc_shadow_equivalence window
            → run_ufc_shadow_equivalence_pass
                (skips gracefully when no UFC experiment seeded)

If this test passes, the deployment on Fedora executes the same code
path against the same schema shape — the intermediate mocks in the
unit tests are validated together.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _seed_ranker_experiment_and_cohort(start: datetime, *, count: int = 25) -> str:
    """Register one ranker_confidence_gate experiment + a settled cohort.

    Returns the ``filter_shadow_experiments.id`` of the registered experiment.
    """
    from aletheia.counterfactual.filter_experiments import register_replay_ready_experiments
    from aletheia.storage.sqlite import get_db
    from tests.test_filter_shadow_experiments import (
        _ready_ranker_replay,
        _seed_ranker_cohort,
    )

    version = _seed_ranker_cohort(start, count=count)
    register_replay_ready_experiments(
        [_ready_ranker_replay(filter_version=version)], now=start
    )
    row = get_db().execute(
        "SELECT id FROM filter_shadow_experiments "
        "WHERE filter_name='ranker_confidence_gate' LIMIT 1"
    ).fetchone()
    assert row is not None
    return row["id"]


def test_end_to_end_lab_runner_writes_all_expected_state():
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.observation_lab.runner import run_lab_windows
    from aletheia.storage.sqlite import get_db

    start = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    experiment_id = _seed_ranker_experiment_and_cohort(start, count=25)

    # Fresh lab.db + fresh app.db → the runner should produce state in
    # both files after one pass.
    summary = run_lab_windows()

    # (1) Runner reports both default windows ran cleanly.
    assert summary["windows_run"] == 2
    assert summary["windows_errored"] == 0
    assert "filter_shadow_evaluate" in summary["results"]
    assert "ufc_shadow_equivalence" in summary["results"]

    # (2) filter_shadow_evaluate captured 50 units (25 passed + 25 rejected).
    filter_result = summary["results"]["filter_shadow_evaluate"]
    assert filter_result.get("frozen_units_captured") == 50
    assert filter_result.get("evaluation", {}).get("evaluated") == 1

    # (3) lab.db has the frozen units bound to this experiment.
    lab_units = get_lab_db().execute(
        "SELECT COUNT(*) AS n FROM filter_shadow_frozen_units WHERE experiment_id=?",
        (experiment_id,),
    ).fetchone()
    assert lab_units["n"] == 50

    # (4) app.db recorded an evaluation snapshot.
    evaluations = get_db().execute(
        "SELECT COUNT(*) AS n FROM filter_shadow_evaluations WHERE experiment_id=?",
        (experiment_id,),
    ).fetchone()
    assert evaluations["n"] >= 1

    # (5) UFC equivalence window ran; without a seeded learning experiment
    # the pass is a clean no-op that returns zero counts.
    ufc_result = summary["results"]["ufc_shadow_equivalence"]
    assert ufc_result == {"checked": 0, "matched": 0, "mismatched": 0, "skipped": 0}

    # (6) G3 wiring: one joint checkpoint was recorded.
    cp = summary["joint_checkpoint"]
    assert cp["ok"] is True
    assert cp["current_fingerprint"]
    cp_rows = get_lab_db().execute(
        "SELECT COUNT(*) AS n FROM joint_checkpoints"
    ).fetchone()
    assert cp_rows["n"] == 1


def test_end_to_end_second_pass_is_idempotent_on_quiet_state():
    """Two passes in a row on unchanged state — no duplicate frozen units,
    no duplicate checkpoint rows, checkpoint reports match."""
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.observation_lab.runner import run_lab_windows

    start = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    experiment_id = _seed_ranker_experiment_and_cohort(start, count=10)

    first = run_lab_windows()
    second = run_lab_windows()

    # Frozen units captured only on the first pass — INSERT OR IGNORE means
    # a repeat run adds zero.
    assert first["results"]["filter_shadow_evaluate"]["frozen_units_captured"] == 20
    assert second["results"]["filter_shadow_evaluate"]["frozen_units_captured"] == 0

    # Second checkpoint reports fingerprint_match (or first_checkpoint if
    # the ordering flipped — both are ok) and does not add a duplicate row.
    assert second["joint_checkpoint"]["ok"] is True
    cp_rows = get_lab_db().execute(
        "SELECT COUNT(*) AS n FROM joint_checkpoints"
    ).fetchone()
    assert cp_rows["n"] == 1  # idempotent save on unchanged fingerprint
    _ = experiment_id  # keep bound to make the test's intent explicit
