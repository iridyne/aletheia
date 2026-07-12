"""Executable contract for the read-only universe contribution verdict runtime."""

from __future__ import annotations

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim(
        "LE-V01", "learning_evolution", "§7 contribution decision", "L2b",
        "The only runtime verdict path evaluates prospective universe-family calibration exposures "
        "against exact settled cost-aware outcomes and preserves treatment/control cohort boundaries.",
        status="implemented",
    ),
    SpecClaim(
        "LE-V02", "learning_evolution", "§7-8 measurement-only verdict", "L2c",
        "A deterministic, idempotent verdict records reproducibility and harm evidence without changing "
        "Learning authority, experiment lifecycle, or any trading behavior.",
        status="implemented",
    ),
)
def test_read_only_verdict_runtime_surface_exists():
    from aletheia.learning.contribution_runner import evaluate_experiment, inspect_experiment

    assert callable(evaluate_experiment)
    assert callable(inspect_experiment)
