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

from unittest.mock import patch


def _fake_experiment_row(exp_id: str = "exp-1") -> dict:
    return {
        "experiment_id": exp_id,
        "hypothesis_id": "hyp-1",
        "policy_epoch": "epoch-1",
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
    """Sentinel cohort; the fake evaluator ignores its contents."""
    pass


def test_equivalence_pass_records_match():
    from aletheia.observation_lab.ufc_shadow import (
        latest_equivalence_record,
        run_ufc_shadow_equivalence_pass,
    )

    stored = {"verdict": "positive", "unique_treatment_n": 50, "unique_control_n": 50, "policy_epoch": "epoch-1"}
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

    stored = {"verdict": "positive", "unique_treatment_n": 50, "unique_control_n": 50, "policy_epoch": "epoch-1"}
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


def test_equivalence_pass_skips_when_no_stored_verdict_yet():
    from aletheia.observation_lab.ufc_shadow import (
        latest_equivalence_record,
        run_ufc_shadow_equivalence_pass,
    )

    # G1: with no stored verdict AND no learning config, the epoch resolver
    # falls through to the "unknown" sentinel, so the observation_id label
    # comes out as ``ufc:exp-1:unknown:0``. The label is incidental — the
    # test is really asserting the "no verdict → skip" path.
    with patch(
        "aletheia.learning.ledger.list_open_experiments",
        return_value=[_fake_experiment_row()],
    ), patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value=None,
    ), patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value="epoch-1",
    ):
        summary = run_ufc_shadow_equivalence_pass()

    assert summary == {"checked": 0, "matched": 0, "mismatched": 0, "skipped": 1}
    record = latest_equivalence_record("ufc:exp-1:epoch-1:0")
    assert record is not None
    assert record["notes"] == "no_stored_verdict_yet"


def test_equivalence_pass_matches_when_only_counts_drift():
    """Verdict-only equivalence: sample counts legitimately drift between the
    stored verdict's snapshot and Lab's fresh recompute (market-day coverage
    gate re-qualifies with each pass). As long as the conclusion is the same,
    the pass is a match — counts land in the audit fields only."""
    from aletheia.observation_lab.ufc_shadow import (
        latest_equivalence_record,
        run_ufc_shadow_equivalence_pass,
    )

    stored = {"verdict": "insufficient_data", "unique_treatment_n": 68, "unique_control_n": 65, "policy_epoch": "epoch-1"}
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

    stored = {"verdict": "positive", "unique_treatment_n": 50, "unique_control_n": 50, "policy_epoch": "epoch-1"}

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
