"""Focused tests for the read-only lco-v1 owner projection."""

from __future__ import annotations

import json
from pathlib import Path


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    loader._config_cache = None
    reset_db()
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def _seed_current_experiment(monkeypatch) -> tuple[str, str]:
    from aletheia.agent_runtime import build_info
    from aletheia.learning.experiments import _policy_epoch
    from aletheia.learning.ledger import (
        insert_experiment,
        insert_hypothesis,
        insert_record,
    )
    from aletheia.learning.models import (
        LearningContributionVerdict,
        LearningExperiment,
        LearningHypothesis,
        LearningJobHealth,
    )
    from aletheia.learning.verdicts import METRIC_VERSION

    monkeypatch.setattr(build_info, "_boot_commit", "commit-current")
    hypothesis = LearningHypothesis(
        fingerprint="lco-fingerprint",
        cohort_id="lco-cohort",
        path_id="universe_family_calibration",
        overlap_group="universe",
        source_type="filter_candidate_outcomes:clean_net:v1",
        scope_json='{"family":"momentum"}',
        action_json='{"type":"opportunity_multiplier"}',
        consumer="universe_allocator",
        target_horizon="1h",
        status="shadow",
        effect_threshold=0.2,
    )
    experiment = LearningExperiment(
        hypothesis_id=hypothesis.hypothesis_id,
        assignment_salt="lco-salt",
        status="active",
        started_at="2026-07-24T00:00:00+00:00",
        min_samples_per_arm=10,
        min_market_days=1,
    )
    assert insert_hypothesis(hypothesis)
    assert insert_experiment(experiment)
    epoch = _policy_epoch(hypothesis.path_id)
    verdict = LearningContributionVerdict(
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        stage="shadow",
        window_start="2026-07-24T00:00:00+00:00",
        window_end="2026-07-24T01:00:00+00:00",
        unique_treatment_n=4,
        unique_control_n=5,
        baseline=-0.1,
        observed=0.2,
        delta=0.3,
        confidence_low=0.01,
        confidence_high=0.5,
        harm_metrics_json=json.dumps({
            "raw_exposure_n": 18,
            "unique_assignment_unit_n": 9,
            "cluster_member_n": 27,
            "allocation_selected_member_n": 7,
            "mirrored_universe_row_n": 7,
            "outcome_source_member_counts": {
                "filter_candidate_outcomes": 27,
            },
            "allocation_selection_source_member_counts": {
                "filter_candidate_outcomes": 27,
            },
            "exclusion_reasons": {"unsettled": 2},
            "invalid_reasons": {},
            "evidence_conflict_fingerprints": [],
            "breached": False,
            "breach_reasons": [],
        }),
        verdict="insufficient_data",
        reason="no_actual_intervention",
        policy_epoch=epoch,
        target_horizon="1h",
        outcome_contract_version="v1",
        metric_version=METRIC_VERSION,
        code_commit="commit-current",
    )
    assert insert_record(
        "learning_contribution_verdicts",
        verdict,
        ignore_duplicates=True,
    )
    health = LearningJobHealth(
        job_id="learning_contribution_evaluator",
        schedule_key="interval:60m",
        status="healthy",
        last_succeeded_at="2026-07-24T01:00:00+00:00",
        last_failed_at="2026-07-23T23:00:00+00:00",
        last_failure_reason=(
            "sqlite OperationalError at /private/local/path: database unavailable"
        ),
        input_watermark="old-window",
    )
    assert insert_record("learning_job_health", health, ignore_duplicates=True)
    return hypothesis.hypothesis_id, experiment.experiment_id


def test_lco_projection_separates_measurement_integrity_and_authority(
    tmp_path,
    monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    hypothesis_id, experiment_id = _seed_current_experiment(monkeypatch)

    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    result = build_learning_contribution_projection()
    assert result["schema_version"] == "lco-v1"
    assert result["availability"] == "available"
    item = result["items"][0]
    assert item["hypothesis_id"] == hypothesis_id
    assert item["experiment_id"] == experiment_id
    assert item["current_identity"]["matches_running_cohort"] is True
    assert item["measurement"]["state"] == "insufficient_data"
    assert item["samples"] == {
        "raw_exposures": 18,
        "treatment_assignment_units": 4,
        "control_assignment_units": 5,
        "required_per_arm": 10,
        "complete_assignment_units": 9,
        "cluster_members": 27,
        "selected_members": 7,
    }
    assert item["integrity"]["outcome_source_member_counts"] == {
        "filter_candidate_outcomes": 27,
    }
    assert item["lifecycle"]["recommended_action"] == "admit_trial"
    assert item["lifecycle"]["mutation_available"] is False
    assert item["lifecycle"]["authority_enabled"] is False


def test_lco_projection_reports_protected_admission_readiness(
    tmp_path,
    monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)
    import aletheia.learning.lifecycle as lifecycle

    monkeypatch.setattr(
        lifecycle,
        "trial_admission_gate_reason",
        lambda _path_id: None,
    )
    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    lifecycle_view = build_learning_contribution_projection()["items"][0]["lifecycle"]
    assert lifecycle_view["recommended_action"] == "admit_trial"
    assert lifecycle_view["authority_enabled"] is False
    assert lifecycle_view["mutation_available"] is True
    assert lifecycle_view["mutation_unavailable_reason"] is None


def test_lco_projection_keeps_stale_verdict_historical(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _, experiment_id = _seed_current_experiment(monkeypatch)
    from aletheia.storage.sqlite import get_db

    get_db().execute(
        "UPDATE learning_contribution_verdicts SET code_commit='old-commit' "
        "WHERE experiment_id=?",
        (experiment_id,),
    )
    get_db().commit()

    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    item = build_learning_contribution_projection()["items"][0]
    assert item["measurement"]["state"] == "not_measured"
    assert item["measurement"]["reason"] == "code_commit_mismatch"
    assert item["latest_historical_measurement"]["state"] == "insufficient_data"
    assert item["latest_historical_measurement"]["current"] is False


def test_lco_projection_never_publishes_a_stale_verdicts_numbers_as_current(
    tmp_path,
    monkeypatch,
):
    """A superseded verdict's aggregates must not sit beside its zeroed arm counts.

    ``measurement.*`` was gated on identity but ``samples.*`` and ``integrity.*``
    were not, so a single item could report ``state=not_measured`` with ``arms
    0/0`` and, from that very same superseded row, ``harm_breached=true`` and
    ``raw_exposures=41``.  Nobody reading that can tell whether a live harm signal
    exists.  Gate all three blocks together and keep the facts in the block that
    is labelled not-current.
    """
    _setup_env(tmp_path, monkeypatch)
    _, experiment_id = _seed_current_experiment(monkeypatch)
    from aletheia.storage.sqlite import get_db

    db = get_db()
    metrics = json.loads(
        db.execute(
            "SELECT harm_metrics_json FROM learning_contribution_verdicts "
            "WHERE experiment_id=?",
            (experiment_id,),
        ).fetchone()[0]
    )
    metrics["breached"] = True
    metrics["breach_reasons"] = ["mae_p95_worsening"]
    db.execute(
        "UPDATE learning_contribution_verdicts SET code_commit='old-commit', "
        "harm_metrics_json=? WHERE experiment_id=?",
        (json.dumps(metrics), experiment_id),
    )
    db.commit()

    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    item = build_learning_contribution_projection()["items"][0]
    assert item["current_identity"]["mismatch_reason"] == "code_commit_mismatch"
    assert item["measurement"]["state"] == "not_measured"

    samples = item["samples"]
    integrity = item["integrity"]
    assert samples["treatment_assignment_units"] == 0
    assert samples["control_assignment_units"] == 0
    assert samples["raw_exposures"] == 0
    assert samples["complete_assignment_units"] == 0
    assert samples["cluster_members"] == 0
    assert samples["selected_members"] == 0
    assert integrity["harm_breached"] is False
    assert integrity["breach_reasons"] == []
    assert integrity["matching_mirror_rows"] == 0
    assert integrity["outcome_source_member_counts"] == {}
    # The gate's own requirement is an experiment fact, not a verdict fact.
    assert samples["required_per_arm"] == 10

    # Nothing is hidden: the block labelled not-current carries every fact.
    historical = item["latest_historical_measurement"]
    assert historical["current"] is False
    assert historical["harm_breached"] is True
    assert historical["breach_reasons"] == ["mae_p95_worsening"]
    assert historical["raw_exposures"] == 18
    assert historical["complete_assignment_units"] == 9
    assert historical["treatment_assignment_units"] == 4
    assert historical["control_assignment_units"] == 5
    assert historical["window_end"] == "2026-07-24T01:00:00+00:00"


def test_lco_projection_reads_the_newest_evaluation_not_the_latest_window(
    tmp_path,
    monkeypatch,
):
    """The 1970 sentinel window must not make a superseded verdict look current.

    The projection keeps its own copy of the latest-verdict selector, so it needs
    the same evaluation-order guarantee as get_latest_measurement_verdict: a cohort
    that empties out stamps the sentinel window, which sorts after every real
    window and silently re-elected the previous row.
    """
    _setup_env(tmp_path, monkeypatch)
    _, experiment_id = _seed_current_experiment(monkeypatch)

    from aletheia.learning.experiments import _policy_epoch
    from aletheia.learning.ledger import get_record, insert_record
    from aletheia.learning.models import LearningContributionVerdict
    from aletheia.learning.verdicts import METRIC_VERSION

    from aletheia.storage.sqlite import get_db

    seeded_id = _seeded_verdict_id(experiment_id)
    db = get_db()
    # The seed leaves created_at at "now"; pin it so evaluation order is explicit
    # and the sentinel window is the only thing that could reorder these two.
    db.execute(
        "UPDATE learning_contribution_verdicts SET created_at=? WHERE verdict_id=?",
        ("2026-07-24T02:00:00+00:00", seeded_id),
    )
    db.commit()
    superseded = get_record("learning_contribution_verdicts", seeded_id)
    assert superseded is not None
    emptied = LearningContributionVerdict(
        experiment_id=experiment_id,
        hypothesis_id=superseded["hypothesis_id"],
        stage="shadow",
        window_start="1970-01-01T00:00:00+00:00",
        window_end="1970-01-01T00:00:00+00:00",
        unique_treatment_n=0,
        unique_control_n=0,
        harm_metrics_json=json.dumps({"breached": False, "breach_reasons": []}),
        verdict="insufficient_data",
        reason="insufficient unique prospective treatment/control samples",
        created_at="2026-07-24T09:00:00+00:00",
        policy_epoch=_policy_epoch("universe_family_calibration"),
        target_horizon="1h",
        outcome_contract_version="v1",
        metric_version=METRIC_VERSION,
        code_commit="commit-current",
    )
    assert insert_record(
        "learning_contribution_verdicts", emptied, ignore_duplicates=True
    )

    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    item = build_learning_contribution_projection()["items"][0]
    assert item["latest_historical_measurement"]["verdict_id"] == emptied.verdict_id
    assert item["samples"]["treatment_assignment_units"] == 0
    assert item["samples"]["control_assignment_units"] == 0


def _seeded_verdict_id(experiment_id: str) -> str:
    from aletheia.storage.sqlite import get_db

    return str(
        get_db()
        .execute(
            "SELECT verdict_id FROM learning_contribution_verdicts "
            "WHERE experiment_id=?",
            (experiment_id,),
        )
        .fetchone()[0]
    )


def test_lco_projection_sanitizes_failure_and_is_side_effect_free(
    tmp_path,
    monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)
    from aletheia.storage.sqlite import get_db

    tables = (
        "learning_hypotheses",
        "learning_experiments",
        "learning_exposures",
        "learning_contribution_verdicts",
        "learning_lifecycle_events",
        "learning_job_health",
        "learning_policy_epochs",
    )
    before = {
        table: [
            dict(row)
            for row in get_db().execute(
                f"SELECT * FROM {table} ORDER BY rowid"
            ).fetchall()
        ]
        for table in tables
    }
    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    result = build_learning_contribution_projection()
    after = {
        table: [
            dict(row)
            for row in get_db().execute(
                f"SELECT * FROM {table} ORDER BY rowid"
            ).fetchall()
        ]
        for table in tables
    }
    assert after == before
    evaluator = result["operations"]["evaluator"]
    assert evaluator["last_failure_code"] == "database_unavailable"
    assert "last_error" not in evaluator
    assert "last_failure_reason" not in evaluator
    assert "/private/local/path" not in json.dumps(result)
    universe = next(
        path
        for path in result["paths"]
        if path["path_id"] == "universe_family_calibration"
    )
    assert universe["consumer_wired"] is True
    assert universe["settings_supported"] is True
    assert universe["measurement_supported"] is True


def test_lco_projection_reports_an_overdue_evaluator_as_overdue(
    tmp_path,
    monkeypatch,
):
    """A job that simply stopped running must not keep reporting ``healthy``.

    Regression (#444): ``status`` is written only on success or failure, so an
    evaluator that stops firing altogether holds its last ``healthy`` value
    forever -- indistinguishable from one that is actually running.
    """
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)
    from aletheia.storage.sqlite import get_db
    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    db = get_db()
    db.execute(
        "UPDATE learning_job_health SET next_due_at=? WHERE job_id=?",
        ("2026-07-24T02:00:00+00:00", "learning_contribution_evaluator"),
    )
    db.commit()

    evaluator = build_learning_contribution_projection()["operations"]["evaluator"]

    assert evaluator["overdue"] is True
    assert evaluator["overdue_seconds"] > 0
    assert evaluator["current_status"] == "overdue"
    # The stored column is untouched: the signal is derived at read time only.
    assert db.execute(
        "SELECT status FROM learning_job_health WHERE job_id=?",
        ("learning_contribution_evaluator",),
    ).fetchone()[0] == "healthy"


def test_lco_projection_does_not_mark_a_job_overdue_before_it_is_due(
    tmp_path,
    monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)
    from datetime import datetime, timedelta, timezone

    from aletheia.storage.sqlite import get_db
    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    due = datetime.now(timezone.utc) + timedelta(minutes=30)
    db = get_db()
    db.execute(
        "UPDATE learning_job_health SET next_due_at=? WHERE job_id=?",
        (due.isoformat(), "learning_contribution_evaluator"),
    )
    db.commit()

    evaluator = build_learning_contribution_projection()["operations"]["evaluator"]

    assert evaluator["overdue"] is False
    assert evaluator["overdue_seconds"] is None
    assert evaluator["current_status"] == "healthy"


def test_unavailable_lco_envelope_does_not_invent_empty_success():
    from aletheia.learning.read_models import (
        unavailable_learning_contribution_projection,
    )

    result = unavailable_learning_contribution_projection(
        "projection_read_failed"
    )
    assert result["availability"] == "unavailable"
    assert result["unavailable_reason"] == "projection_read_failed"
    assert result["data_freshness"]["state"] == "unavailable"
    assert result["operations"] is None


def test_lco_projection_prioritizes_influence_paths_when_window_is_full(
    tmp_path,
    monkeypatch,
):
    """A measured experiment stays visible however many newer ones are opened.

    ``paper_influence_paths`` is an authority allowlist and ships empty, so an
    earlier fix that tiered only on it did not apply to any deployed
    configuration: 485 experiments on a path that is not measurement-supported --
    busy, but incapable of ever producing a verdict -- drove the real projection to
    zero universe_family_calibration items at the default limit.
    Part A pins the visibility floor with an empty allowlist — measurement-supported
    paths outrank unmeasurable ones.  Part B keeps the allowlist working on top of
    it.  Both parts also pin that tiering did not widen the window itself.
    """
    _setup_env(tmp_path, monkeypatch)
    _, ufc_experiment_id = _seed_current_experiment(monkeypatch)

    from aletheia.learning.ledger import insert_experiment, insert_hypothesis
    from aletheia.learning.models import LearningExperiment, LearningHypothesis

    # 21 newer open experiments with a different path_id fill the window.
    newer_ts = "2099-01-01T00:00:00+00:00"
    for i in range(21):
        h = LearningHypothesis(
            fingerprint=f"filler-fp-{i}",
            cohort_id=f"filler-cohort-{i}",
            path_id="filler_path",
            overlap_group="filler",
            source_type="filter_candidate_outcomes:clean_net:v1",
            scope_json="{}",
            action_json="{}",
            consumer="universe_allocator",
            target_horizon="1h",
            status="shadow",
            effect_threshold=0.2,
            created_at=newer_ts,
            updated_at=newer_ts,
        )
        e = LearningExperiment(
            hypothesis_id=h.hypothesis_id,
            assignment_salt=f"filler-salt-{i}",
            status="active",
            started_at=newer_ts,
            min_samples_per_arm=10,
            min_market_days=1,
            created_at=newer_ts,
        )
        assert insert_hypothesis(h)
        assert insert_experiment(e)

    import aletheia.config.loader as loader_mod
    from aletheia.config.schema import AletheiaConfig, LearningConfig

    # Part A: empty paper_influence_paths — the deployed configuration. A
    # measurement-supported path must still be visible, and the unmeasurable
    # filler path must be what gives up the slot.
    monkeypatch.setattr(loader_mod, "load_config", lambda: AletheiaConfig())

    from aletheia.learning.read_models import build_learning_contribution_projection

    result_default = build_learning_contribution_projection(limit=20)
    returned_ids_default = {item["experiment_id"] for item in result_default["items"]}
    assert ufc_experiment_id in returned_ids_default, (
        "A measured experiment must stay visible with an empty influence allowlist"
    )
    assert len(result_default["items"]) == 20
    assert sum(
        item["path_id"] == "filler_path" for item in result_default["items"]
    ) == 19, "the unmeasurable filler path must yield exactly the one contested slot"

    # Part B: paper_influence_paths=[universe_family_calibration] → UFC is prioritized.
    influence_cfg = AletheiaConfig(
        learning=LearningConfig(paper_influence_paths=["universe_family_calibration"])
    )
    monkeypatch.setattr(loader_mod, "load_config", lambda: influence_cfg)

    result_influence = build_learning_contribution_projection(limit=20)
    returned_ids_influence = {item["experiment_id"] for item in result_influence["items"]}
    assert ufc_experiment_id in returned_ids_influence, (
        "With paper_influence_paths=[universe_family_calibration], "
        "UFC must appear within the limit-20 window"
    )
    assert len(result_influence["items"]) == 20
