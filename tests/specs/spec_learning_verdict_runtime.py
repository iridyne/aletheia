"""Learning contribution route reads canonical Lab counts and honest failures."""

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(SpecClaim(
    "LE-V04", "learning_evolution", "§7 learning-contribution route verdict counts", "L2b",
    "The route counts persisted canonical Lab measurements, never legacy verdicts.",
    status="implemented",
))
def test_learning_contribution_route_returns_lab_verdict_counts():
    from aletheia.observation_lab.canonical import register_observation_spec, record_observation_result
    from aletheia.observation_lab.db import reset_lab_db
    from aletheia.server.routes.analytics import get_learning_contribution
    from aletheia.storage.sqlite import get_db

    reset_lab_db()
    get_db().execute("UPDATE observation_authority_state SET authority_source='lab'")
    get_db().commit()
    for index, verdict in enumerate(("insufficient_data", "invalid", "insufficient_data")):
        root = register_observation_spec({
            "observation_id": f"verdict-count-{index}", "path_id": "universe_family_calibration",
            "filter_name": f"count-{index}", "stage": "shadow", "scanner_family": "momentum",
            "direction": "long", "market_type": "perp", "horizon": "4h",
            "control": {"value": 1.0}, "treatment": {"value": 2.0},
            "behavior_policy_epoch_ref": "epoch-lab",
        })
        record_observation_result(
            observation_id=root["observation_id"], generation=1, spec_hash=root["spec_hash"],
            source_cutoff='{"main_import_contiguous_sequence":0}', verdict=verdict,
            disposition="evaluated", result={},
        )
    result = get_learning_contribution()
    assert result["verdicts"]["total"] == 3
    assert {row["verdict"]: row["n"] for row in result["verdicts"]["by_verdict"]} == {
        "insufficient_data": 2, "invalid": 1,
    }


@spec_claim(SpecClaim(
    "LE-V05", "learning_evolution", "§7 learning-contribution route error honesty", "L2b",
    "Missing Lab evidence is unavailable, not an empty successful measurement.",
    status="implemented",
))
def test_learning_contribution_route_error_is_not_silent_zero():
    from aletheia.observation_lab.db import get_lab_db, reset_lab_db
    from aletheia.server.routes.analytics import get_learning_contribution
    from aletheia.storage.sqlite import get_db

    reset_lab_db()
    get_db().execute("UPDATE observation_authority_state SET authority_source='lab'")
    get_db().commit()
    lab = get_lab_db()
    lab.execute("DROP TABLE observation_windows")
    lab.commit()
    result = get_learning_contribution()
    assert result["availability"] == "unavailable"
    assert result["verdicts"]["total"] is None
    assert result["verdicts"]["query_error"] == "canonical_lab_projection_unavailable"
    assert "no such table" not in str(result)
