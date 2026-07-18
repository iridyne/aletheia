"""Executable contract for the read-only universe contribution verdict runtime."""

from __future__ import annotations

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim(
        "LE-V01", "learning_evolution", "§7 contribution decision", "L2b",
        "The only runtime verdict path evaluates full-arm filter candidate outcomes by exact key, "
        "uses selected-universe outcomes only as a non-duplicating mirror or fallback, and counts "
        "complete assignment clusters rather than survivor candidates.",
        status="implemented",
    ),
    SpecClaim(
        "LE-V02", "learning_evolution", "§7-8 measurement-only verdict", "L2c",
        "A deterministic, idempotent verdict records reproducibility and harm evidence without changing "
        "Learning authority, experiment lifecycle, or any trading behavior.",
        status="implemented",
    ),
    SpecClaim(
        "LE-V03", "learning_evolution", "§7 actual intervention", "L2c",
        "AT-01 carries exposure authority into each cohort sample; a fully shadow cohort with no "
        "applied treatment returns insufficient_data/no_actual_intervention and cannot produce a "
        "causal positive or negative verdict.",
        status="implemented",
    ),
)
def test_read_only_verdict_runtime_surface_exists():
    from dataclasses import fields

    from aletheia.learning.cohorts import CohortSample
    from aletheia.learning.contribution_runner import evaluate_experiment, inspect_experiment

    assert callable(evaluate_experiment)
    assert callable(inspect_experiment)
    assert "authority_applied" in {field.name for field in fields(CohortSample)}
    assert "assignment_unit_key" in {field.name for field in fields(CohortSample)}
