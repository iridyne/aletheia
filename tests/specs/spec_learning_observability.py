"""Executable target claims for Learning Contribution Observability.

The target contract is
docs/design/contracts/learning_observability_contract.md. These strict-xfail
placeholders keep the unimplemented owner-facing projection visible in the SDD
matrix. An implementation must replace each placeholder with a real behavioral
test before changing its status to implemented.
"""

from __future__ import annotations

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


_CONTRACT = "learning_observability"


def _missing(claim_id: str):
    return pytest.mark.xfail(
        strict=True,
        reason=f"{claim_id}: Learning Contribution Observability is not implemented",
    )


@_missing("LCO-S01")
@spec_claim(SpecClaim(
    "LCO-S01", _CONTRACT, "§6 target read APIs", "L2a",
    "A typed GET-only lco-v1 projection is indexed and introduces no Learning mutation endpoint.",
    status="missing",
))
def test_lco_get_only_projection_is_not_implemented():
    pytest.fail("LCO-S01 target behavior is intentionally missing")


@_missing("LCO-B01")
@spec_claim(SpecClaim(
    "LCO-B01", _CONTRACT, "§6.1 cohort integrity", "L2b",
    "The projection distinguishes raw exposures, complete treatment/control assignment units, "
    "member and selected counts, exclusions, and invalid reasons.",
    status="missing",
))
def test_lco_cluster_integrity_projection_is_not_implemented():
    pytest.fail("LCO-B01 target behavior is intentionally missing")


@_missing("LCO-B02")
@spec_claim(SpecClaim(
    "LCO-B02", _CONTRACT, "§6.5 outcome provenance", "L2b",
    "Canonical outcomes, fallback rows, matching mirrors, allocation inference and proven conflicts "
    "remain distinct, and conflicting evidence contributes no sample.",
    status="missing",
))
def test_lco_outcome_provenance_projection_is_not_implemented():
    pytest.fail("LCO-B02 target behavior is intentionally missing")


@_missing("LCO-B03")
@spec_claim(SpecClaim(
    "LCO-B03", _CONTRACT, "§3.3 measurement identity", "L2b",
    "The current identity-matched metric/cohort verdict is separate from append-only historical "
    "metric, epoch, stage, horizon, outcome-contract and build versions.",
    status="missing",
))
def test_lco_current_and_historical_verdicts_are_not_implemented():
    pytest.fail("LCO-B03 target behavior is intentionally missing")


@_missing("LCO-B04")
@spec_claim(SpecClaim(
    "LCO-B04", _CONTRACT, "§6.6 operational health", "L2b",
    "Current job state, last success, next due, sanitized durable last failure, input watermark and "
    "catch-up due state are projected independently; raw stored error text never leaves the backend.",
    status="missing",
))
def test_lco_scheduler_recovery_projection_is_not_implemented():
    pytest.fail("LCO-B04 target behavior is intentionally missing")


@_missing("LCO-C01")
@spec_claim(SpecClaim(
    "LCO-C01", _CONTRACT, "§1 authority boundary", "L2c",
    "admit_trial is exposed only as a read-only recommendation with mutation_available=false and "
    "never implies readiness or owner authentication.",
    status="missing",
))
def test_lco_admission_recommendation_boundary_is_not_implemented():
    pytest.fail("LCO-C01 target behavior is intentionally missing")


@_missing("LCO-C02")
@spec_claim(SpecClaim(
    "LCO-C02", _CONTRACT, "§8 UI honesty", "L2c",
    "The UI derives contribution state only from backend measurement facts; counts, activity, PnL "
    "and evaluator health cannot imply effectiveness.",
    status="missing",
))
def test_lco_frontend_honesty_states_are_not_implemented():
    pytest.fail("LCO-C02 target behavior is intentionally missing")


@_missing("LCO-C03")
@spec_claim(SpecClaim(
    "LCO-C03", _CONTRACT, "§7 bounded read path", "L2c",
    "Observability reads perform no write, evaluation, lifecycle or authority side effect and use "
    "bounded cached work outside the event loop.",
    status="missing",
))
def test_lco_bounded_side_effect_free_reads_are_not_implemented():
    pytest.fail("LCO-C03 target behavior is intentionally missing")


@_missing("LCO-S02")
@spec_claim(SpecClaim(
    "LCO-S02", _CONTRACT, "§1 excluded trust boundaries", "L2a",
    "The observability projection and routes import no Risk, Execution, exchange, secrets, "
    "permission or live modules.",
    status="missing",
))
def test_lco_forbidden_import_boundary_is_not_implemented():
    pytest.fail("LCO-S02 target behavior is intentionally missing")


@_missing("LCO-B05")
@spec_claim(SpecClaim(
    "LCO-B05", _CONTRACT, "§6.2 path capability", "L2b",
    "Consumer wiring, Settings support and measurement support are independently projected and "
    "cannot collapse into one supported flag.",
    status="missing",
))
def test_lco_path_capability_projection_is_not_implemented():
    pytest.fail("LCO-B05 target behavior is intentionally missing")


@_missing("LCO-C04")
@spec_claim(SpecClaim(
    "LCO-C04", _CONTRACT, "§8.6 query states", "L2c",
    "Loading, genuine empty, unavailable, stale and partial remain distinct, and a failed fetch "
    "never becomes a zero or empty result.",
    status="missing",
))
def test_lco_query_state_honesty_is_not_implemented():
    pytest.fail("LCO-C04 target behavior is intentionally missing")


@_missing("LCO-C05")
@spec_claim(SpecClaim(
    "LCO-C05", _CONTRACT, "§8.7 responsive accessibility", "L2c",
    "Desktop and mobile layouts plus keyboard and ARIA behavior preserve access to LCO tabs, "
    "tables and detail drawers.",
    status="missing",
))
def test_lco_responsive_accessibility_is_not_implemented():
    pytest.fail("LCO-C05 target behavior is intentionally missing")


@_missing("LCO-C06")
@spec_claim(SpecClaim(
    "LCO-C06", _CONTRACT, "§7 query and performance", "L2c",
    "Server-enforced limits, monotonic TTL caching, Server-Timing and direct cold/warm backend "
    "measurements enforce the LCO performance contract.",
    status="missing",
))
def test_lco_performance_evidence_is_not_implemented():
    pytest.fail("LCO-C06 target behavior is intentionally missing")
