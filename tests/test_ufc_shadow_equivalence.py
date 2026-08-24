"""UFC shadow-equivalence pass tests (Issue #1072 Packet 1).

These tests exercise the pass/fail/skip logic of
``run_ufc_shadow_equivalence_pass`` in isolation. Building a full UFC
end-to-end cohort in a test would require standing up hypotheses, exposures,
policy epochs, and outcomes — that path is already covered by the existing
Learning tests. Here we monkeypatch the two heavy dependencies
(``list_open_experiments``, ``build_experiment_cohorts``,
``evaluate_cohort``, ``get_latest_measurement_verdict``) so we can pin exact
inputs and confirm the comparison + recording behavior.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from aletheia.learning.registry import get_learning_path
from aletheia.universe.outcome_economics import COST_MODEL_VERSION


def _fake_experiment_row(exp_id: str = "exp-1") -> dict:
    return {
        "experiment_id": exp_id,
        "hypothesis_id": "hyp-1",
        "stage": "shadow",
        "fixed_look_generation": 0,
        "stage_generation": 0,
    }


class _FakeVerdictEval:
    """Duck-type for VerdictEvaluation — only fields ufc_shadow reads."""
    def __init__(self, verdict: str, treatment_n: int, control_n: int) -> None:
        self.verdict = verdict
        self.unique_treatment_n = treatment_n
        self.unique_control_n = control_n


class _FakeCohort:
    def __init__(
        self,
        *,
        policy_epoch: str = "epoch-1",
        behavior_contract_hash: str = "behavior-current",
    ) -> None:
        path = get_learning_path("universe_family_calibration")
        self.key = SimpleNamespace(
            experiment_id="exp-1",
            hypothesis_id="hyp-1",
            policy_epoch=policy_epoch,
            horizon="4h",
            outcome_contract_version=path.outcome_contract_version,
            behavior_contract_hash=behavior_contract_hash,
        )
        self.experiment = SimpleNamespace(stage="shadow")
        self.hypothesis = SimpleNamespace(target_horizon="4h")


def _stored_verdict(
    verdict: str,
    treatment_n: int,
    control_n: int,
    *,
    policy_epoch: str = "epoch-1",
    behavior_contract_hash: str = "behavior-current",
) -> dict:
    path = get_learning_path("universe_family_calibration")
    return {
        "verdict": verdict,
        "unique_treatment_n": treatment_n,
        "unique_control_n": control_n,
        "policy_epoch": policy_epoch,
        "stage": "shadow",
        "hypothesis_id": "hyp-1",
        "target_horizon": "4h",
        "outcome_contract_version": path.outcome_contract_version,
        "metric_version": path.metric_version,
        "behavior_contract_hash": behavior_contract_hash,
        "harm_metrics_json": json.dumps(
            {"expected_cost_model_version": COST_MODEL_VERSION}
        ),
    }


def test_equivalence_pass_records_match():
    from aletheia.observation_lab.ufc_shadow import (
        latest_equivalence_record,
        run_ufc_shadow_equivalence_pass,
    )

    stored = _stored_verdict("positive", 50, 50)
    recomputed = _FakeVerdictEval("positive", 50, 50)

    with patch(
        "aletheia.learning.ledger.list_open_experiments",
        return_value=[_fake_experiment_row()],
    ), patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value=stored,
    ), patch(
        "aletheia.learning.cohorts.build_experiment_cohorts",
        return_value=[_FakeCohort()],
    ), patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value="epoch-1",
    ), patch(
        "aletheia.learning.behavior_contracts.current_behavior_contract_hash",
        return_value="behavior-current",
    ), patch(
        "aletheia.learning.verdicts.evaluate_cohort",
        return_value=recomputed,
    ):
        summary = run_ufc_shadow_equivalence_pass()

    assert summary == {"checked": 1, "matched": 1, "mismatched": 0, "skipped": 0}
    record = latest_equivalence_record("ufc:exp-1:epoch-1:0")
    assert record is not None
    assert record["matched"] == 1
    assert record["mismatch_field"] is None
    assert record["learning_verdict"] == "positive"
    assert record["recomputed_verdict"] == "positive"


def test_equivalence_pass_fails_closed_on_verdict_mismatch():
    from aletheia.observation_lab.ufc_shadow import (
        latest_equivalence_record,
        run_ufc_shadow_equivalence_pass,
    )

    stored = _stored_verdict("positive", 50, 50)
    recomputed = _FakeVerdictEval("negative", 50, 50)  # <-- disagrees with stored

    with patch(
        "aletheia.learning.ledger.list_open_experiments",
        return_value=[_fake_experiment_row()],
    ), patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value=stored,
    ), patch(
        "aletheia.learning.cohorts.build_experiment_cohorts",
        return_value=[_FakeCohort()],
    ), patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value="epoch-1",
    ), patch(
        "aletheia.learning.behavior_contracts.current_behavior_contract_hash",
        return_value="behavior-current",
    ), patch(
        "aletheia.learning.verdicts.evaluate_cohort",
        return_value=recomputed,
    ):
        summary = run_ufc_shadow_equivalence_pass()

    assert summary == {"checked": 1, "matched": 0, "mismatched": 1, "skipped": 0}
    record = latest_equivalence_record("ufc:exp-1:epoch-1:0")
    assert record is not None
    assert record["matched"] == 0
    assert record["mismatch_field"] == "verdict"
    assert record["learning_verdict"] == "positive"
    assert record["recomputed_verdict"] == "negative"


def test_equivalence_pass_skips_when_no_exact_current_verdict():
    from aletheia.observation_lab.ufc_shadow import (
        latest_equivalence_record,
        run_ufc_shadow_equivalence_pass,
    )

    with patch(
        "aletheia.learning.ledger.list_open_experiments",
        return_value=[_fake_experiment_row()],
    ), patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value=None,
    ), patch(
        "aletheia.learning.cohorts.build_experiment_cohorts",
        return_value=[_FakeCohort()],
    ), patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value="epoch-1",
    ), patch(
        "aletheia.learning.behavior_contracts.current_behavior_contract_hash",
        return_value="behavior-current",
    ):
        summary = run_ufc_shadow_equivalence_pass()

    assert summary == {"checked": 0, "matched": 0, "mismatched": 0, "skipped": 1}
    record = latest_equivalence_record("ufc:exp-1:epoch-1:0")
    assert record is not None
    assert record["notes"] == "no_exact_current_verdict"


def test_multi_epoch_pass_selects_exact_current_identity():
    from aletheia.observation_lab.ufc_shadow import (
        latest_equivalence_record,
        run_ufc_shadow_equivalence_pass,
    )

    historical = _FakeCohort(
        policy_epoch="a-historical", behavior_contract_hash="behavior-old"
    )
    current = _FakeCohort(policy_epoch="z-current")
    stored = _stored_verdict(
        "insufficient_data", 9, 9, policy_epoch="z-current"
    )
    recomputed = _FakeVerdictEval("insufficient_data", 10, 10)
    path = get_learning_path("universe_family_calibration")

    with patch(
        "aletheia.learning.ledger.list_open_experiments",
        return_value=[_fake_experiment_row()],
    ), patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value=stored,
    ) as verdict_lookup, patch(
        "aletheia.learning.cohorts.build_experiment_cohorts",
        return_value=[historical, current],
    ), patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value="z-current",
    ), patch(
        "aletheia.learning.behavior_contracts.current_behavior_contract_hash",
        return_value="behavior-current",
    ), patch(
        "aletheia.learning.verdicts.evaluate_cohort",
        return_value=recomputed,
    ) as evaluate:
        summary = run_ufc_shadow_equivalence_pass()

    assert summary == {"checked": 1, "matched": 1, "mismatched": 0, "skipped": 0}
    evaluate.assert_called_once_with(current)
    verdict_lookup.assert_called_once_with(
        "exp-1",
        policy_epoch="z-current",
        stage="shadow",
        hypothesis_id="hyp-1",
        target_horizon="4h",
        outcome_contract_version=path.outcome_contract_version,
        metric_version=path.metric_version,
        behavior_contract_hash="behavior-current",
        inference_role="monitoring",
    )
    record = latest_equivalence_record("ufc:exp-1:z-current:0")
    assert record is not None
    assert record["matched"] == 1
    assert latest_equivalence_record("ufc:exp-1:a-historical:0") is None


def test_equivalence_pass_skips_when_exact_current_cohort_is_missing():
    from aletheia.observation_lab.ufc_shadow import (
        latest_equivalence_record,
        run_ufc_shadow_equivalence_pass,
    )

    with patch(
        "aletheia.learning.ledger.list_open_experiments",
        return_value=[_fake_experiment_row()],
    ), patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
    ) as verdict_lookup, patch(
        "aletheia.learning.cohorts.build_experiment_cohorts",
        return_value=[_FakeCohort(policy_epoch="historical")],
    ), patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value="current",
    ), patch(
        "aletheia.learning.behavior_contracts.current_behavior_contract_hash",
        return_value="behavior-current",
    ), patch(
        "aletheia.learning.verdicts.evaluate_cohort",
    ) as evaluate:
        summary = run_ufc_shadow_equivalence_pass()

    assert summary == {"checked": 0, "matched": 0, "mismatched": 0, "skipped": 1}
    verdict_lookup.assert_not_called()
    evaluate.assert_not_called()
    record = latest_equivalence_record("ufc:exp-1:current:0")
    assert record is not None
    assert record["notes"] == "no_exact_current_cohort"


def test_equivalence_pass_skips_when_current_policy_epoch_is_unavailable():
    from aletheia.observation_lab.ufc_shadow import (
        latest_equivalence_record,
        run_ufc_shadow_equivalence_pass,
    )

    with patch(
        "aletheia.learning.ledger.list_open_experiments",
        return_value=[_fake_experiment_row()],
    ), patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
    ) as verdict_lookup, patch(
        "aletheia.learning.cohorts.build_experiment_cohorts",
    ) as build_cohorts, patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value=None,
    ), patch(
        "aletheia.learning.verdicts.evaluate_cohort",
    ) as evaluate:
        summary = run_ufc_shadow_equivalence_pass()

    assert summary == {"checked": 0, "matched": 0, "mismatched": 0, "skipped": 1}
    verdict_lookup.assert_not_called()
    build_cohorts.assert_not_called()
    evaluate.assert_not_called()
    record = latest_equivalence_record("ufc:exp-1:unknown:0")
    assert record is not None
    assert record["notes"] == "current_policy_epoch_unavailable"


def test_equivalence_pass_rejects_stale_cost_identity():
    from aletheia.observation_lab.ufc_shadow import (
        latest_equivalence_record,
        run_ufc_shadow_equivalence_pass,
    )

    stored = _stored_verdict("positive", 50, 50)
    stored["harm_metrics_json"] = json.dumps(
        {"expected_cost_model_version": "historical-cost-model"}
    )
    with patch(
        "aletheia.learning.ledger.list_open_experiments",
        return_value=[_fake_experiment_row()],
    ), patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value=stored,
    ), patch(
        "aletheia.learning.cohorts.build_experiment_cohorts",
        return_value=[_FakeCohort()],
    ), patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value="epoch-1",
    ), patch(
        "aletheia.learning.behavior_contracts.current_behavior_contract_hash",
        return_value="behavior-current",
    ), patch(
        "aletheia.learning.verdicts.evaluate_cohort",
    ) as evaluate:
        summary = run_ufc_shadow_equivalence_pass()

    assert summary == {"checked": 0, "matched": 0, "mismatched": 1, "skipped": 0}
    evaluate.assert_not_called()
    record = latest_equivalence_record("ufc:exp-1:epoch-1:0")
    assert record is not None
    assert record["mismatch_field"] == "cost_model_version_mismatch"


def test_equivalence_pass_matches_when_only_counts_drift():
    """Verdict-only equivalence: sample counts legitimately drift between the
    stored verdict's snapshot and Lab's fresh recompute (market-day coverage
    gate re-qualifies with each pass). As long as the conclusion is the same,
    the pass is a match — counts land in the audit fields only."""
    from aletheia.observation_lab.ufc_shadow import (
        latest_equivalence_record,
        run_ufc_shadow_equivalence_pass,
    )

    stored = _stored_verdict("insufficient_data", 68, 65)
    recomputed = _FakeVerdictEval("insufficient_data", 5, 13)  # counts drift, verdict same

    with patch(
        "aletheia.learning.ledger.list_open_experiments",
        return_value=[_fake_experiment_row()],
    ), patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value=stored,
    ), patch(
        "aletheia.learning.cohorts.build_experiment_cohorts",
        return_value=[_FakeCohort()],
    ), patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value="epoch-1",
    ), patch(
        "aletheia.learning.behavior_contracts.current_behavior_contract_hash",
        return_value="behavior-current",
    ), patch(
        "aletheia.learning.verdicts.evaluate_cohort",
        return_value=recomputed,
    ):
        summary = run_ufc_shadow_equivalence_pass()

    assert summary == {"checked": 1, "matched": 1, "mismatched": 0, "skipped": 0}
    record = latest_equivalence_record("ufc:exp-1:epoch-1:0")
    assert record is not None
    assert record["matched"] == 1
    assert record["mismatch_field"] is None
    # But the drifted counts are still preserved in the audit row.
    assert record["learning_treatment_n"] == 68
    assert record["recomputed_treatment_n"] == 5
    assert record["learning_control_n"] == 65
    assert record["recomputed_control_n"] == 13


def test_equivalence_pass_treats_evaluator_error_as_mismatch():
    from aletheia.observation_lab.ufc_shadow import (
        latest_equivalence_record,
        run_ufc_shadow_equivalence_pass,
    )

    stored = _stored_verdict("positive", 50, 50)

    def _raise(_cohort):
        raise RuntimeError("evaluator broke")

    with patch(
        "aletheia.learning.ledger.list_open_experiments",
        return_value=[_fake_experiment_row()],
    ), patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value=stored,
    ), patch(
        "aletheia.learning.cohorts.build_experiment_cohorts",
        return_value=[_FakeCohort()],
    ), patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value="epoch-1",
    ), patch(
        "aletheia.learning.behavior_contracts.current_behavior_contract_hash",
        return_value="behavior-current",
    ), patch(
        "aletheia.learning.verdicts.evaluate_cohort",
        side_effect=_raise,
    ):
        summary = run_ufc_shadow_equivalence_pass()

    assert summary["mismatched"] == 1
    record = latest_equivalence_record("ufc:exp-1:epoch-1:0")
    assert record is not None
    assert record["mismatch_field"] == "evaluator_error"


def test_equivalence_pass_leaves_no_row_when_no_open_experiments():
    from aletheia.observation_lab.ufc_shadow import run_ufc_shadow_equivalence_pass

    with patch(
        "aletheia.learning.ledger.list_open_experiments",
        return_value=[],
    ):
        summary = run_ufc_shadow_equivalence_pass()

    assert summary == {"checked": 0, "matched": 0, "mismatched": 0, "skipped": 0}
