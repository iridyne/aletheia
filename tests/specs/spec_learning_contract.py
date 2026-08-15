"""Executable boundary claims for the current Learning contract."""

from tests.specs.spec_meta import SpecClaim, spec_claim


_CONTRACT = "learning_contract"


@spec_claim(SpecClaim(
    "LC-S01", _CONTRACT, "§1.2 measured-path maturity", "L2a",
    "Only paths with an executable exact outcome/statistical-unit adapter are "
    "measurement-supported; consumer wiring and Settings visibility do not imply "
    "measurement or authority.",
    status="implemented",
))
def test_only_declared_closed_loops_are_measurement_supported():
    from aletheia.learning.registry import LEARNING_PATHS

    supported = {
        path.path_id
        for path in LEARNING_PATHS
        if path.measurement_supported
    }
    assert supported == {"universe_family_calibration", "ranker_case_score"}
    assert any(
        path.consumer_wired and not path.measurement_supported
        for path in LEARNING_PATHS
    )
    assert all(
        not path.measurement_supported or path.consumer_wired
        for path in LEARNING_PATHS
    )


@spec_claim(SpecClaim(
    "LC-C01", _CONTRACT, "§1.2 unsupported-path fallback", "L2c",
    "A consumer-wired but measurement-unsupported path returns its static baseline and creates "
    "no hypothesis, experiment, exposure, or authority identity.",
    status="implemented",
))
def test_measurement_unsupported_path_cannot_create_ledger_authority(monkeypatch):
    from aletheia.learning import experiments
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(experiments, "_learning_gate_reason", lambda: None)
    tables = (
        "learning_hypotheses",
        "learning_experiments",
        "learning_exposures",
    )
    db = get_db()
    before = {
        table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in tables
    }

    decision = experiments.apply_numeric_treatment(
        path_id="operator_learning_bundle",
        scope={"consumer": "system_operator"},
        action={"type": "prompt_bundle", "enabled": True},
        source_cohort="operator:contract-test",
        target_horizon="4h",
        candidate_key="opportunity:contract-test",
        baseline=0.0,
        treatment=1.0,
        consumer="system_operator",
        consumer_stage="prompt_context",
    )

    assert decision.value == 0.0
    assert decision.applied is False
    assert decision.reason == "measurement_unsupported"
    assert decision.hypothesis_id is None
    assert decision.experiment_id is None
    assert decision.exposure_id is None
    assert {
        table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in tables
    } == before
