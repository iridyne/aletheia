"""Owner maintenance for Learning records that cannot produce measurements."""

from __future__ import annotations


def _open_experiment(
    path_id: str,
    token: str,
    *,
    stage: str = "shadow",
    scope_json: str = "{}",
) -> tuple[str, str]:
    from aletheia.learning.ledger import insert_experiment, insert_hypothesis
    from aletheia.learning.models import LearningExperiment, LearningHypothesis

    hypothesis_id = f"hypothesis-{token}"
    experiment_id = f"experiment-{token}"
    assert insert_hypothesis(LearningHypothesis(
        hypothesis_id=hypothesis_id,
        fingerprint=f"fingerprint-{token}",
        cohort_id=f"cohort-{token}",
        path_id=path_id,
        overlap_group="test",
        source_type="test",
        scope_json=scope_json,
        action_json="{}",
        consumer="test",
        target_horizon="4h",
        status="shadow",
    ))
    assert insert_experiment(LearningExperiment(
        experiment_id=experiment_id,
        hypothesis_id=hypothesis_id,
        stage=stage,
        assignment_salt=f"salt-{token}",
        status="active",
    ))
    return hypothesis_id, experiment_id


def test_archive_unmeasurable_experiments_is_dry_run_by_default_and_idempotent():
    from aletheia.learning.maintenance import archive_unmeasurable_experiments
    from aletheia.storage.sqlite import get_db

    unsupported_hypothesis, unsupported_experiment = _open_experiment(
        "observer_advisory_bundle", "unsupported"
    )
    supported_hypothesis, supported_experiment = _open_experiment(
        "universe_family_calibration", "supported"
    )
    db = get_db()

    planned = archive_unmeasurable_experiments()

    assert planned == {
        "dry_run": True,
        "experiments": 1,
        "hypotheses": 1,
        "paths": {"observer_advisory_bundle": 1},
    }
    assert db.execute(
        "SELECT ended_at FROM learning_experiments WHERE experiment_id=?",
        (unsupported_experiment,),
    ).fetchone()[0] is None

    applied = archive_unmeasurable_experiments(dry_run=False)

    assert applied == {**planned, "dry_run": False}
    unsupported = db.execute(
        "SELECT status, ended_at FROM learning_experiments WHERE experiment_id=?",
        (unsupported_experiment,),
    ).fetchone()
    assert unsupported["status"] == "archived"
    assert unsupported["ended_at"] is not None
    assert db.execute(
        "SELECT status FROM learning_hypotheses WHERE hypothesis_id=?",
        (unsupported_hypothesis,),
    ).fetchone()[0] == "archived"
    event = db.execute(
        "SELECT action, reason, audit_actor, audit_source, audit_mirror_status "
        "FROM learning_lifecycle_events WHERE experiment_id=?",
        (unsupported_experiment,),
    ).fetchone()
    assert tuple(event) == (
        "archive_unmeasurable",
        "owner_maintenance:measurement_unsupported",
        "human",
        "cli",
        "mirrored",
    )

    supported = db.execute(
        "SELECT status, ended_at FROM learning_experiments WHERE experiment_id=?",
        (supported_experiment,),
    ).fetchone()
    assert tuple(supported) == ("active", None)
    assert db.execute(
        "SELECT status FROM learning_hypotheses WHERE hypothesis_id=?",
        (supported_hypothesis,),
    ).fetchone()[0] == "shadow"
    assert archive_unmeasurable_experiments(dry_run=False)["experiments"] == 0


def test_archive_reconciles_its_event_ahead_of_existing_backlog():
    from aletheia.learning.ledger import insert_lifecycle_event
    from aletheia.learning.maintenance import archive_unmeasurable_experiments
    from aletheia.learning.models import LearningLifecycleEvent
    from aletheia.storage.sqlite import get_db

    _, unsupported_experiment = _open_experiment(
        "observer_advisory_bundle", "targeted"
    )
    backlog_hypothesis, backlog_experiment = _open_experiment(
        "universe_family_calibration", "backlog"
    )
    backlog = LearningLifecycleEvent(
        event_id="000-old-backlog",
        dedup_key="old-backlog",
        hypothesis_id=backlog_hypothesis,
        experiment_id=backlog_experiment,
        from_stage="shadow",
        action="refreeze",
        reason="test",
    )
    assert insert_lifecycle_event(backlog)

    archive_unmeasurable_experiments(dry_run=False)

    rows = get_db().execute(
        "SELECT experiment_id, audit_mirror_status FROM learning_lifecycle_events "
        "ORDER BY event_id"
    ).fetchall()
    statuses = {row["experiment_id"]: row["audit_mirror_status"] for row in rows}
    assert statuses[backlog_experiment] == "pending"
    assert statuses[unsupported_experiment] == "mirrored"



def test_refreeze_redundant_ufc_shadows_keeps_exact_paper_loop():
    from aletheia.learning.maintenance import (
        refreeze_redundant_universe_family_shadows,
    )
    from aletheia.storage.sqlite import get_db

    keep_hypothesis, keep_experiment = _open_experiment(
        "universe_family_calibration",
        "keep-paper",
        stage="paper_10",
        scope_json='{"direction":"long","family":"momentum","market_type":"spot"}',
    )
    shadow_hypothesis, shadow_experiment = _open_experiment(
        "universe_family_calibration", "redundant-shadow"
    )
    other_hypothesis, other_experiment = _open_experiment(
        "operator_learning_bundle", "other-path"
    )

    planned = refreeze_redundant_universe_family_shadows(
        keep_experiment_id=keep_experiment,
    )
    assert planned == {
        "dry_run": True,
        "keep_experiment_id": keep_experiment,
        "refrozen_experiments": 1,
        "refrozen_hypotheses": 1,
    }

    applied = refreeze_redundant_universe_family_shadows(
        keep_experiment_id=keep_experiment,
        dry_run=False,
    )
    assert applied == {**planned, "dry_run": False}
    db = get_db()
    assert tuple(db.execute(
        "SELECT status, ended_at FROM learning_experiments WHERE experiment_id=?",
        (keep_experiment,),
    ).fetchone()) == ("active", None)
    assert db.execute(
        "SELECT status FROM learning_hypotheses WHERE hypothesis_id=?",
        (keep_hypothesis,),
    ).fetchone()[0] == "shadow"
    refrozen = db.execute(
        "SELECT status, ended_at FROM learning_experiments WHERE experiment_id=?",
        (shadow_experiment,),
    ).fetchone()
    assert refrozen["status"] == "refrozen"
    assert refrozen["ended_at"] is not None
    assert db.execute(
        "SELECT status FROM learning_hypotheses WHERE hypothesis_id=?",
        (shadow_hypothesis,),
    ).fetchone()[0] == "refrozen"
    assert tuple(db.execute(
        "SELECT status, ended_at FROM learning_experiments WHERE experiment_id=?",
        (other_experiment,),
    ).fetchone()) == ("active", None)
    assert db.execute(
        "SELECT status FROM learning_hypotheses WHERE hypothesis_id=?",
        (other_hypothesis,),
    ).fetchone()[0] == "shadow"
    event = db.execute(
        "SELECT action, audit_actor, audit_source, audit_mirror_status "
        "FROM learning_lifecycle_events WHERE experiment_id=?",
        (shadow_experiment,),
    ).fetchone()
    assert tuple(event) == ("refreeze", "human", "cli", "mirrored")
    assert refreeze_redundant_universe_family_shadows(
        keep_experiment_id=keep_experiment,
        dry_run=False,
    )["refrozen_experiments"] == 0