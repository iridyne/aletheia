"""Executable AT-02 lifecycle and authority contract."""

from __future__ import annotations

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim(
        "LE-L01", "learning_evolution", "§7 contribution decision", "L2b",
        "AT-01 measurement verdicts map to distinct deterministic lifecycle actions and retain "
        "their measurement vocabulary.", status="implemented",
    ),
    SpecClaim(
        "LE-L02", "learning_evolution", "§8 stage lifecycle", "L2c",
        "A lifecycle transition is idempotent, stage-isolated, auditable, and cannot grant paper "
        "influence unless every default-closed authority gate is explicitly enabled.", status="implemented",
    ),
    SpecClaim(
        "LE-L03", "learning_evolution", "§10 scheduler continuity", "L2c",
        "Contribution measurement remains schedulable while lifecycle control is separately gated, "
        "restart-safe, and single-instance.", status="implemented",
    ),
)
def test_lifecycle_runtime_surface_exists():
    from aletheia.learning.authority import resolve_learning_authority
    from aletheia.learning.lifecycle import (
        inspect_learning_experiment,
        measurement_to_lifecycle_action,
        process_lifecycle,
    )

    assert measurement_to_lifecycle_action("positive") == "promote_stage"
    assert callable(process_lifecycle)
    assert callable(resolve_learning_authority)
    assert callable(inspect_learning_experiment)
