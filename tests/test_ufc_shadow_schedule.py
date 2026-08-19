"""UFC shadow-equivalence pass is wired into the observe cycle (#1072 Packet 1).

Two isolation contracts to prove:

1. The observe cycle invokes ``run_ufc_shadow_equivalence_pass`` and surfaces
   its summary in the return dict.
2. A failing UFC pass never suppresses filter-shadow work, and vice versa —
   they run under independent try/except blocks per new #1072 §"Failure
   isolation".
"""

from __future__ import annotations

from unittest.mock import patch


def test_observe_cycle_reports_ufc_shadow_equivalence_summary():
    from aletheia.analytics.filter_controller import run_controller_observe

    fake_summary = {"checked": 3, "matched": 3, "mismatched": 0, "skipped": 0}

    with patch(
        "aletheia.analytics.filter_controller._is_fresh_prior_run",
        return_value=False,
    ), patch(
        "aletheia.observation_lab.ufc_shadow.run_ufc_shadow_equivalence_pass",
        return_value=fake_summary,
    ):
        summary = run_controller_observe(min_samples=20)

    assert summary["ufc_shadow_equivalence"] == fake_summary


def test_observe_cycle_isolates_ufc_pass_failure_from_filter_shadow():
    """A raise inside the UFC pass must not corrupt filter-shadow output.

    Cluster 2 routes UFC through the Lab runner; the runner catches per-window
    exceptions and marks that window's result with ``status=error``. The
    observe cycle still returns all filter-shadow keys unaffected — that's
    the load-bearing isolation contract per new #1072 §"Failure isolation".
    """
    from aletheia.analytics.filter_controller import run_controller_observe

    with patch(
        "aletheia.analytics.filter_controller._is_fresh_prior_run",
        return_value=False,
    ), patch(
        "aletheia.observation_lab.ufc_shadow.run_ufc_shadow_equivalence_pass",
        side_effect=RuntimeError("simulated UFC evaluator crash"),
    ):
        summary = run_controller_observe(min_samples=20)

    # Runner surfaced the error under this window's own result key. The
    # exception did not propagate; every other window and every filter-shadow
    # step still completed.
    ufc_result = summary["ufc_shadow_equivalence"]
    assert ufc_result.get("status") == "error"
    assert "simulated UFC evaluator crash" in ufc_result.get("error", "")
    assert "experiments_registered" in summary
    assert "experiments_evaluated" in summary
    assert "recommendations_written" in summary
    assert summary["lab_windows"]["windows_errored"] >= 1
