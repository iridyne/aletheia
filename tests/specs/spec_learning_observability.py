"""Executable claims for Learning Contribution Observability.

The target contract is
docs/design/proposals/learning_observability.md. Every claim currently indexed
in this module is implemented as a behavioral test; this file carries no
strict-xfail placeholder for future target slices.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim
from tests.test_learning_read_models import _seed_current_experiment, _setup_env


_CONTRACT = "learning_observability"


@spec_claim(SpecClaim(
    "LCO-S01", _CONTRACT, "§6 target read APIs", "L2a",
    "A typed GET-only lco-v1 projection is indexed and introduces no Learning mutation endpoint.",
    status="implemented",
))
def test_lco_projection_is_indexed_and_get_only(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    from aletheia.learning.read_models import build_learning_contribution_projection
    from aletheia.server.app import app

    routes = [
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/analytics/learning-contribution"
    ]
    assert len(routes) == 1
    assert getattr(routes[0], "methods", set()) == {"GET"}
    assert build_learning_contribution_projection()["schema_version"] == "lco-v1"
    assert all(
        set(getattr(route, "methods", set())) <= {"GET", "HEAD"}
        for route in app.routes
        if "learning-contribution" in getattr(route, "path", "")
    )


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
        "exposed_treatment_units": 4,
        "exposed_authority_applied_treatment_units": 0,
        "exposed_actual_intervention_treatment_units": 0,
        "exposed_control_units": 5,
        "treatment_assignment_units": 4,
        "assigned_treatment_units": 4,
        "authority_applied_treatment_units": 0,
        "actual_intervention_treatment_units": 0,
        "baseline_treatment_units": 4,
        "control_assignment_units": 5,
        "required_per_arm": 10,
        "complete_assignment_units": 9,
        "cluster_members": 27,
        "selected_members": 7,
        "treatment_members": 12,
        "treatment_selected_members": 2,
        "control_members": 15,
        "control_selected_members": 5,
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
    "The observability UI remains read-only; admission readiness is separate from owner-route "
    "authentication and every owner-gated paper authority check.",
    status="implemented",
))
def test_lco_admission_recommendation_is_read_only(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)

    from aletheia.learning.read_models import build_learning_contribution_projection

    lifecycle = build_learning_contribution_projection()["items"][0]["lifecycle"]
    assert lifecycle["recommended_action"] == "admit_trial"
    assert lifecycle["authority_enabled"] is False
    assert lifecycle["authority_block_reason"] == "experiment_stage_not_paper"
    assert lifecycle["mutation_available"] is False
    assert lifecycle["mutation_unavailable_reason"] == "harm_gate_indeterminate"


@spec_claim(SpecClaim(
    "LCO-C02", _CONTRACT, "§8 UI honesty", "L2c",
    "The UI derives contribution state only from backend measurement facts; counts, activity, PnL "
    "and evaluator health cannot imply effectiveness.",
    status="partial",
))
def test_lco_frontend_uses_backend_measurement_state():
    root = Path(__file__).resolve().parents[2]
    source = (root / "frontend/src/lib/learningContribution.ts").read_text()
    proof = (root / "frontend/src/lib/learningContribution.test.ts").read_text()
    workbench_proof = (root / "frontend/src/pages/Learning.test.tsx").read_text()
    interaction_proof = (root / "frontend/src/pages/Learning.interaction.test.tsx").read_text()
    workbench = (root / "frontend/src/pages/Learning.tsx").read_text()

    assert "return item.measurement.state" in source
    assert "large activity counts" in proof
    assert "toBe('insufficient_data')" in proof
    assert "Portfolio PnL" not in workbench
    assert "not.toContain('Visible PnL chart')" in interaction_proof
    assert "not.toContain('Contradictions needing review: <strong>0</strong>')" in workbench_proof


@spec_claim(SpecClaim(
    "LCO-C03", _CONTRACT, "§7 bounded read path", "L2c",
    "Observability reads perform no write, evaluation, lifecycle transition or authority mutation and use "
    "bounded cached work outside the event loop.",
    status="implemented",
))
@pytest.mark.anyio
async def test_lco_reads_are_side_effect_free_and_off_the_event_loop(
    tmp_path, monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _, experiment_id = _seed_current_experiment(monkeypatch)

    from httpx import ASGITransport, AsyncClient
    from aletheia.learning import lifecycle
    from aletheia.server.app import app
    from aletheia.server.routes import analytics, settings
    from aletheia.storage.sqlite import get_db
    import aletheia.config.loader as config_loader

    config_loader.CONFIG_PATH.write_text(
        "agent:\n  permission_mode: Research\n"
        "operator:\n  permission_mode: Observe\n",
        encoding="utf-8",
    )
    config_loader._config_cache = None
    config_before = config_loader.CONFIG_PATH.read_bytes()

    monkeypatch.setattr(
        lifecycle,
        "run_contribution_evaluator",
        lambda: pytest.fail("read invoked evaluator"),
    )
    monkeypatch.setattr(
        lifecycle,
        "run_lifecycle_controller",
        lambda: pytest.fail("read invoked lifecycle controller"),
    )
    analytics._learning_contribution_cache.clear()
    tables = (
        "learning_hypotheses",
        "learning_experiments",
        "learning_exposures",
        "learning_contribution_verdicts",
        "learning_lifecycle_events",
        "learning_job_health",
        "learning_policy_epochs",
        "audit_events",
    )

    def snapshot():
        db = get_db()
        return {
            table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")]
            for table in tables
        }

    before = snapshot()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        assert (await client.get("/api/analytics/learning-contribution")).status_code == 200
        assert (await client.get("/api/settings/learning/experiments?limit=20")).status_code == 200
        detail = await client.get(
            f"/api/settings/learning/experiments/{experiment_id}"
        )
        assert detail.status_code == 409
    assert snapshot() == before
    assert config_loader.CONFIG_PATH.read_bytes() == config_before
    assert not inspect.iscoroutinefunction(analytics.get_learning_contribution)
    assert not inspect.iscoroutinefunction(settings.list_learning_experiments)
    assert not inspect.iscoroutinefunction(settings.get_learning_experiment)


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


@spec_claim(SpecClaim(
    "LCO-C04", _CONTRACT, "§8.6 query states", "L2c",
    "Loading, genuine empty, unavailable, stale and partial remain distinct, and a failed fetch "
    "never becomes a zero or empty result.",
    status="partial",
))
def test_lco_query_states_have_executable_frontend_proof():
    root = Path(__file__).resolve().parents[2]
    source = (root / "frontend/src/lib/learningContribution.ts").read_text()
    proof = (root / "frontend/src/lib/learningContribution.test.ts").read_text()
    workbench_proof = (root / "frontend/src/pages/Learning.test.tsx").read_text()

    for state in ("loading", "unavailable", "empty", "stale", "partial"):
        assert f"'{state}'" in source
        assert f"toBe('{state}')" in proof
    assert "renders failed reads as unavailable" in workbench_proof
    assert "renders initial reads as loading" in workbench_proof


@spec_claim(SpecClaim(
    "LCO-C05", _CONTRACT, "§8.7 responsive accessibility", "L2c",
    "Desktop and mobile layouts plus native keyboard and label semantics preserve access to the "
    "current measured-path selector and Contribution summary.",
    status="partial",
))
def test_lco_multi_path_control_is_keyboard_native_and_responsive():
    root = Path(__file__).resolve().parents[2]
    selector = (
        root / "frontend/src/components/MeasuredPathSelector.tsx"
    ).read_text()
    css = (root / "frontend/src/index.css").read_text()
    proof = (
        root / "frontend/src/components/MeasuredPathSelector.test.tsx"
    ).read_text()

    assert '<label htmlFor="lco-measured-path">' in selector
    assert 'id="lco-measured-path"' in selector
    assert "aria-label" not in selector
    assert "renderToStaticMarkup" in proof
    assert "universe_family_calibration" in proof
    assert "@media (max-width: 1100px)" in css
    assert "@media (max-width: 700px)" in css


@spec_claim(SpecClaim(
    "LCO-C06", _CONTRACT, "§7 query and performance", "L2c",
    "The current summary uses monotonic TTL caching and emits explainable cold/cache-hit "
    "Server-Timing.",
    status="implemented",
))
@pytest.mark.anyio
async def test_lco_limits_monotonic_cache_and_server_timing(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)

    from httpx import ASGITransport, AsyncClient
    from aletheia.server.app import app
    from aletheia.server.routes import analytics, settings

    class _Rows:
        def fetchall(self):
            return []

    class _LimitDB:
        params = None

        def execute(self, _sql, params):
            self.params = params
            return _Rows()

    limit_db = _LimitDB()
    import aletheia.storage.sqlite as sqlite_module
    real_get_db = sqlite_module.get_db
    monkeypatch.setattr(sqlite_module, "get_db", lambda: limit_db)
    assert settings.list_learning_experiments(limit=-10) == []
    assert limit_db.params == (1,)
    monkeypatch.setattr(sqlite_module, "get_db", real_get_db)

    ticks = iter((100.0, 100.0, 120.0, 131.0, 131.0))
    monkeypatch.setattr(
        analytics, "_learning_contribution_monotonic", lambda: next(ticks),
    )
    analytics._learning_contribution_cache.clear()
    computes = 0
    real_compute = analytics._compute_learning_contribution

    def tracked_compute():
        nonlocal computes
        computes += 1
        return real_compute()

    monkeypatch.setattr(analytics, "_compute_learning_contribution", tracked_compute)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        cold = await client.get("/api/analytics/learning-contribution")
        warm = await client.get("/api/analytics/learning-contribution")
        expired = await client.get("/api/analytics/learning-contribution")

    assert computes == 2
    assert "ledger;dur=" in cold.headers["server-timing"]
    assert "cache_hit;dur=0.0" in warm.headers["server-timing"]
    assert "ledger;dur=" in expired.headers["server-timing"]
    assert cold.json()["generated_at"] == warm.json()["generated_at"]


def test_lco_cache_values_are_not_shared_with_callers(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)

    from fastapi import Response
    from aletheia.server.routes import analytics

    analytics._learning_contribution_cache.clear()
    first = analytics.get_learning_contribution(Response())
    first["schema_version"] = "caller-mutated"
    first["items"].clear()

    second = analytics.get_learning_contribution(Response())
    assert second["schema_version"] == "lco-v1"
    assert second["items"]


def test_lco_older_concurrent_miss_cannot_overwrite_newer_projection(
    tmp_path, monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)

    from fastapi import Response
    from threading import Event, Thread, current_thread
    from aletheia.server.routes import analytics

    older_started = Event()
    newer_published = Event()
    results: dict[str, dict] = {}

    def monotonic():
        name = current_thread().name
        if name == "older-lco":
            return 10.0
        if name == "newer-lco":
            return 20.0 if not newer_published.is_set() else 30.0
        return 31.0

    def compute():
        if current_thread().name == "older-lco":
            older_started.set()
            assert newer_published.wait(2)
            return {"generated_at": "older"}
        assert older_started.wait(2)
        return {"generated_at": "newer"}

    def request(name: str):
        results[name] = analytics.get_learning_contribution(Response())
        if name == "newer":
            newer_published.set()

    monkeypatch.setattr(analytics, "_learning_contribution_monotonic", monotonic)
    monkeypatch.setattr(analytics, "_compute_learning_contribution", compute)
    analytics._learning_contribution_cache.clear()
    older = Thread(target=request, args=("older",), name="older-lco")
    newer = Thread(target=request, args=("newer",), name="newer-lco")
    older.start()
    newer.start()
    older.join(2)
    newer.join(2)

    assert results == {
        "newer": {"generated_at": "newer"},
        "older": {"generated_at": "older"},
    }
    assert analytics.get_learning_contribution(Response())["generated_at"] == "newer"
