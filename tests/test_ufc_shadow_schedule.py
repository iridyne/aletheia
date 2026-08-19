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
    """A raise inside the UFC pass must not corrupt filter-shadow output."""
    from aletheia.analytics.filter_controller import run_controller_observe

    with patch(
        "aletheia.analytics.filter_controller._is_fresh_prior_run",
        return_value=False,
    ), patch(
        "aletheia.observation_lab.ufc_shadow.run_ufc_shadow_equivalence_pass",
        side_effect=RuntimeError("simulated UFC evaluator crash"),
    ):
        summary = run_controller_observe(min_samples=20)

    # UFC section swallowed the error and left its summary at the default zero shape.
    assert summary["ufc_shadow_equivalence"] == {
        "checked": 0, "matched": 0, "mismatched": 0, "skipped": 0,
    }
    # Filter-shadow section still produced its ordinary outputs.
    assert "experiments_registered" in summary
    assert "experiments_evaluated" in summary
    assert "recommendations_written" in summary
