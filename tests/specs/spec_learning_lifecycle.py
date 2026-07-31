"""Executable AT-02 lifecycle and authority contract."""

from __future__ import annotations

import pytest

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
    SpecClaim(
        "LE-L04", "learning_evolution", "§7.1 trial admission", "L2c",
        "Shadow measurement never auto-promotes into paper authority, and caller-provided actor "
        "text cannot cross the public fail-closed API; the separate owner route requires tunnel "
        "protection, explicit confirmation, server-derived epoch identity and every paper gate.",
        status="implemented",
    ),
    SpecClaim(
        "LE-L05", "learning_evolution", "§7.1 current paper lifecycle", "L2c",
        "Paper authority binds activation and current-stage measurements to the same hypothesis, "
        "closed predecessor, declared horizon, and declared path.", status="implemented",
    ),
)
def test_lifecycle_runtime_surface_exists():
    from aletheia.learning.authority import resolve_learning_authority
    from aletheia.learning.lifecycle import (
        admit_shadow_trial,
        inspect_learning_experiment,
        measurement_to_lifecycle_action,
        process_lifecycle,
    )

    assert measurement_to_lifecycle_action("positive") == "promote_stage"
    assert callable(process_lifecycle)
    assert callable(admit_shadow_trial)
    assert callable(resolve_learning_authority)
    assert callable(inspect_learning_experiment)


@pytest.mark.xfail(
    strict=True,
    reason="LE-C07: source outcome to learned-treatment production is not implemented",
)
@spec_claim(SpecClaim(
    "LE-C07", "learning_evolution", "§7-9 causal paper lifecycle", "L2c",
    "A measurement-supported path proves source outcome to learned treatment before the existing "
    "exposure, settlement, owner admission, withdrawal, refreeze, and fallback lifecycle.",
    status="missing",
))
def test_source_outcome_produces_the_measured_treatment():
    pytest.fail("No current source-outcome producer creates the UFC learned treatment")
