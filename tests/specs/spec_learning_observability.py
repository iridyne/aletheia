"""Executable claims for Learning Contribution Observability.

The target contract is
docs/design/proposals/learning_observability.md. Implemented slices use
behavioral tests; remaining target slices stay strict-xfail so a partial owner
projection cannot be mistaken for the full workbench.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim
from tests.test_learning_read_models import _seed_current_experiment, _setup_env


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


@spec_claim(SpecClaim(
    "LCO-B01", _CONTRACT, "§6.1 cohort integrity", "L2b",
    "The projection distinguishes raw exposures, complete treatment/control assignment units, "
    "member and selected counts, exclusions, and invalid reasons.",
    status="implemented",
))
def test_lco_cluster_integrity_projection_is_distinct(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)

    from aletheia.learning.read_models import build_learning_contribution_projection

    item = build_learning_contribution_projection()["items"][0]
    assert item["samples"] == {
        "raw_exposures": 18,
        "treatment_assignment_units": 4,
        "control_assignment_units": 5,
        "required_per_arm": 10,
        "complete_assignment_units": 9,
        "cluster_members": 27,
        "selected_members": 7,
    }
    assert item["integrity"]["exclusion_reasons"] == {"unsettled": 2}
    assert item["integrity"]["invalid_reasons"] == {}


@spec_claim(SpecClaim(
    "LCO-B02", _CONTRACT, "§6.5 outcome provenance", "L2b",
    "Canonical outcomes, fallback rows, matching mirrors, allocation inference and proven conflicts "
    "remain distinct, and conflicting evidence contributes no sample.",
    status="implemented",
))
def test_lco_outcome_provenance_projection_is_exact_and_distinct(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)

    from aletheia.learning.read_models import build_learning_contribution_projection

    integrity = build_learning_contribution_projection()["items"][0]["integrity"]
    assert integrity["outcome_source_member_counts"] == {
        "filter_candidate_outcomes": 27,
    }
    assert integrity["matching_mirror_rows"] == 7
    assert integrity["conflict_count"] == 1
    assert integrity["evidence_conflict_provenance_truncated"] is False
    conflict = integrity["evidence_conflicts"][0]
    assert conflict["canonical"] == {
        "source": "filter_candidate_outcomes",
        "row_id": 41,
    }
    assert conflict["mirrors"] == [{
        "source": "universe_forward_outcomes",
        "row_id": 82,
    }]
    assert conflict["differing_fields"] == {
        "net_return_pct": {"canonical": 1.0, "mirrors": [-1.0]},
    }


@spec_claim(SpecClaim(
    "LCO-B03", _CONTRACT, "§3.3 measurement identity", "L2b",
    "The current identity-matched metric/cohort verdict is separate from append-only historical "
    "metric, epoch, stage, horizon, outcome-contract and behavior-contract versions; build "
    "identity remains provenance only.",
    status="implemented",
))
def test_lco_current_and_historical_verdicts_are_separate(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _, experiment_id = _seed_current_experiment(monkeypatch)

    from aletheia.learning.read_models import build_learning_contribution_projection
    from aletheia.storage.sqlite import get_db

    get_db().execute(
        "UPDATE learning_contribution_verdicts SET behavior_contract_hash='old-behavior', "
        "code_commit='old-commit' "
        "WHERE experiment_id=?",
        (experiment_id,),
    )
    get_db().commit()
    item = build_learning_contribution_projection()["items"][0]
    assert item["measurement"]["state"] == "not_measured"
    assert item["measurement"]["reason"] == "behavior_contract_mismatch"
    assert item["measurement"]["delta"] is None
    assert item["latest_historical_measurement"]["state"] == "insufficient_data"
    assert item["latest_historical_measurement"]["code_commit"] == "old-commit"
    assert item["latest_historical_measurement"]["current"] is False


@spec_claim(SpecClaim(
    "LCO-B04", _CONTRACT, "§6.6 operational health", "L2b",
    "Current job state, last success, next due, sanitized durable last failure, input watermark and "
    "catch-up due state are projected independently; raw stored error text never leaves the backend.",
    status="implemented",
))
def test_lco_scheduler_recovery_projection_is_sanitized(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)

    from aletheia.learning.read_models import build_learning_contribution_projection

    result = build_learning_contribution_projection()
    evaluator = result["operations"]["evaluator"]
    assert evaluator["current_status"] == "healthy"
    assert evaluator["last_succeeded_at"] == "2026-07-24T01:00:00+00:00"
    assert evaluator["input_watermark"] == "old-window"
    assert evaluator["last_failure_code"] == "database_unavailable"
    assert "last_error" not in evaluator
    assert "last_failure_reason" not in evaluator
    assert "/private/local/path" not in json.dumps(result)


@spec_claim(SpecClaim(
    "LCO-C01", _CONTRACT, "§1 authority boundary", "L2c",
    "The observability UI remains read-only; admission readiness is true only when the separately "
    "protected owner route and every default-closed paper gate are ready.",
    status="implemented",
))
def test_lco_admission_recommendation_is_read_only(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)

    from aletheia.learning.read_models import build_learning_contribution_projection

    lifecycle = build_learning_contribution_projection()["items"][0]["lifecycle"]
    assert lifecycle["recommended_action"] == "admit_trial"
    assert lifecycle["authority_enabled"] is False
    assert lifecycle["authority_block_reason"] == "read_only_projection"
    assert lifecycle["mutation_available"] is False
    assert lifecycle["mutation_unavailable_reason"] == "lifecycle_automation_disabled"


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


@spec_claim(SpecClaim(
    "LCO-S02", _CONTRACT, "§1 excluded trust boundaries", "L2a",
    "The observability projection and routes import no Risk, Execution, exchange, secrets, "
    "permission or live modules.",
    status="implemented",
))
def test_lco_forbidden_import_boundary():
    import aletheia.learning.read_models as read_models
    from aletheia.server.routes.analytics import get_learning_contribution

    forbidden = (
        "aletheia.risk",
        "aletheia.execution",
        "aletheia.exchanges",
        "aletheia.config.secrets",
        "aletheia.agent_runtime.permissions",
        "aletheia.server.routes.live",
    )
    trees = (
        ast.parse(inspect.getsource(read_models)),
        ast.parse(textwrap.dedent(inspect.getsource(get_learning_contribution))),
    )
    imported: set[str] = set()
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not {
        module
        for module in imported
        if any(module == root or module.startswith(f"{root}.") for root in forbidden)
    }


@spec_claim(SpecClaim(
    "LCO-B05", _CONTRACT, "§6.2 path capability", "L2b",
    "Consumer wiring, Settings support and measurement support are independently projected and "
    "cannot collapse into one supported flag.",
    status="implemented",
))
def test_lco_path_capabilities_remain_independent(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)

    from aletheia.learning.read_models import build_learning_contribution_projection

    paths = {
        path["path_id"]: path
        for path in build_learning_contribution_projection()["paths"]
    }
    universe = paths["universe_family_calibration"]
    assert universe["consumer_wired"] is True
    assert universe["settings_supported"] is True
    assert universe["measurement_supported"] is True
    observer = paths["observer_advisory_bundle"]
    assert observer["consumer_wired"] is True
    assert observer["settings_supported"] is True
    assert observer["measurement_supported"] is False

    from aletheia.learning.registry import paper_influence_path_options

    observer_option = next(
        option
        for option in paper_influence_path_options()
        if option["path_id"] == "observer_advisory_bundle"
    )
    assert observer_option["supported"] is False
    assert observer_option["status"] == "measurement_unwired"
    assert any(
        len({
            path["consumer_wired"],
            path["settings_supported"],
            path["measurement_supported"],
        }) > 1
        for path in paths.values()
    )


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
