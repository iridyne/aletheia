"""Current Learning projection reads Lab evidence, never legacy experiment authority."""

import pytest


def test_lab_projection_is_read_only_and_never_grants_treatment_authority():
    from aletheia.learning.read_models import build_learning_contribution_projection
    from aletheia.observation_lab.canonical import register_observation_spec
    from aletheia.observation_lab.db import get_lab_db, reset_lab_db
    from aletheia.storage.sqlite import get_db

    reset_lab_db()
    get_db().execute("UPDATE observation_authority_state SET authority_source='lab'")
    root = register_observation_spec({
        "observation_id": "learning-projection",
        "path_id": "universe_family_calibration",
        "filter_name": "test-filter",
        "stage": "shadow",
        "scanner_family": "momentum",
        "direction": "long",
        "market_type": "perp",
        "horizon": "4h",
        "control": {"value": 1.0},
        "treatment": {"value": 2.0},
        "behavior_policy_epoch_ref": "epoch-lab",
    })
    lab = get_lab_db()
    before = (get_db().total_changes, lab.total_changes)
    projection = build_learning_contribution_projection()
    item, = projection["items"]
    assert item["experiment_id"] == f"{root['observation_id']}:g1"
    assert item["measurement"]["state"] == "not_measured"
    assert item["measurement"]["authority_eligible"] is False
    assert item["current_identity"]["matches_running_cohort"] is None
    assert item["lifecycle"]["authority_enabled"] is False
    assert item["lifecycle"]["mutation_available"] is False
    assert (get_db().total_changes, lab.total_changes) == before


@pytest.mark.parametrize("authority", ["legacy", "missing"])
def test_missing_current_authority_never_projects_legacy_experiments(authority):
    from aletheia.learning.read_models import build_learning_contribution_projection
    from aletheia.storage.sqlite import get_db

    db = get_db()
    if authority == "missing":
        db.execute("DELETE FROM observation_authority_state")
    else:
        db.execute("UPDATE observation_authority_state SET authority_source='legacy'")
    before = db.total_changes
    result = build_learning_contribution_projection()
    assert result["availability"] == "unavailable"
    assert result["items"] == []
    assert result["data_freshness"]["state"] == "unavailable"
    assert db.total_changes == before


def test_unavailable_lco_envelope_does_not_invent_empty_success():
    from aletheia.learning.read_models import unavailable_learning_contribution_projection

    result = unavailable_learning_contribution_projection("projection_read_failed")
    assert result["availability"] == "unavailable"
    assert result["unavailable_reason"] == "projection_read_failed"
    assert result["data_freshness"]["state"] == "unavailable"
    assert result["data_freshness"]["stale_after_sec"] is None
    assert result["operations"] is None
