"""Tests for universe API routes."""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from aletheia.config.schema import UniverseConfig


def test_workbench_progress_uses_same_cache_without_ledger_probe(client, monkeypatch):
    from aletheia.agent_runtime import health
    from aletheia.storage import repositories
    monkeypatch.setattr(health, "_candidate_path_progress", {
        "generated_at": "2000-01-01T00:00:00+00:00", "ttl_seconds": 1200,
        "availability": {"available": True, "reason": None},
        "ready_planning_count": 7, "issues": ["candidate_planning_stalled"],
    })
    def forbidden(**kwargs):
        raise AssertionError("Workbench must only read the progress cache")
    monkeypatch.setattr(repositories, "get_candidate_path_progress", forbidden)
    response = client.get("/api/universe/workbench")
    assert response.status_code == 200
    payload = response.json()
    assert payload["progress"]["ready_planning_count"] == 7
    assert payload["progress"]["availability"] == health.get_candidate_path_progress_snapshot()["availability"]
    assert payload["availability"]["progress"]["available"] is False
    assert payload["availability"]["progress"]["stale"] is True
    monkeypatch.setattr(health, "_candidate_path_progress", None)
    missing = client.get("/api/universe/workbench").json()["progress"]
    assert missing["ready_planning_count"] is None
    assert missing["availability"]["reason"] == "not_sampled"


@pytest.fixture
def client():
    from aletheia.server.app import app
    return TestClient(app)


@pytest.fixture
def disabled_cfg():
    cfg = MagicMock()
    cfg.universe = UniverseConfig(enabled=False)
    cfg.market_data = MagicMock()
    return cfg


@pytest.fixture
def enabled_cfg():
    cfg = MagicMock()
    cfg.universe = UniverseConfig(enabled=True)
    cfg.market_data = MagicMock()
    return cfg


def test_get_current_when_disabled(client, disabled_cfg):
    with patch("aletheia.universe.service.load_config", return_value=disabled_cfg):
        with patch("aletheia.server.routes.universe.load_config", return_value=disabled_cfg):
            with patch("aletheia.server.routes.universe.repo.get_active_universe_snapshot", return_value=None):
                resp = client.get("/api/universe/current")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fallback"] is True
    assert data["enabled"] is False
    assert data["members"] == []


def test_get_current_with_active_snapshot(client, enabled_cfg):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_members = [
        {"symbol": "DOGE/USDT", "scanner_family": "momentum", "total_score": 75,
         "snapshot_id": "snap_1", "id": "m1", "execution_eligible": 1,
         "expires_at": future, "rank": 1},
    ]
    snap = {"id": "snap_1", "status": "active", "created_at": "2025-01-01T00:00:00"}
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.server.routes.universe.load_config", return_value=enabled_cfg):
            with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
                with patch("aletheia.universe.service.repo.list_universe_members", return_value=mock_members):
                    with patch("aletheia.server.routes.universe.repo.get_active_universe_snapshot", return_value=snap):
                        resp = client.get("/api/universe/current")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert len(data["members"]) == 1
    assert data["members"][0]["symbol"] == "DOGE/USDT"


def test_get_current_uses_service_snapshot_namespace_with_mixed_config(client):
    cfg = MagicMock()
    cfg.universe = UniverseConfig(enabled=True, market_type="mixed")
    cfg.market_data = MagicMock()
    snap = {
        "id": "snap_mixed_assets",
        "status": "active",
        "summary": '{"scanned": 1000, "filtered": 245, "selected": 28}',
    }
    members = [{"id": "member_1", "snapshot_id": snap["id"], "symbol": "BTC/USDT"}]

    with patch("aletheia.universe.service.load_config", return_value=cfg), \
         patch("aletheia.server.routes.universe.load_config", return_value=cfg), \
         patch(
             "aletheia.server.routes.universe.repo.get_active_universe_snapshot",
             return_value=snap,
         ) as get_snapshot, \
         patch(
             "aletheia.universe.service.UniverseService.get_current_universe",
             return_value=members,
         ):
        resp = client.get("/api/universe/current")

    assert resp.status_code == 200
    data = resp.json()
    assert data["snapshot"] == snap
    assert data["members"] == [
        {
            **members[0],
            "entry_plan_id": None,
            "entry_plan_status": None,
            "entry_plan_execution_mode": None,
            "setup_state": None,
            "triggered_opportunity_id": None,
        }
    ]
    assert data["fallback"] is False
    get_snapshot.assert_called_once_with()


def test_candidate_read_model_prefers_current_entry_plan(monkeypatch):
    from aletheia.server.routes.universe import _attach_candidate_handoff_state
    from aletheia.storage import repositories as repo

    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []
    monkeypatch.setattr("aletheia.storage.sqlite.get_db", lambda: db)
    monkeypatch.setattr(repo, "get_candidate_planning_for_candidate", lambda _: {
        "is_current": 1, "entry_plan_id": "plan-1"})
    monkeypatch.setattr(repo, "get_entry_plan", lambda _: {
        "id": "plan-1", "status": "active", "execution_mode": "RESTING_MAKER"})
    [candidate] = _attach_candidate_handoff_state([{"id": "candidate-1"}])
    assert candidate["entry_plan_id"] == "plan-1"
    assert candidate["entry_plan_status"] == "active"
    assert candidate["entry_plan_execution_mode"] == "RESTING_MAKER"
    assert candidate["setup_state"] is None


@pytest.mark.parametrize("owner", ["generation", "plan", "legacy"])
def test_candidate_handoff_read_failure_is_not_no_plan(monkeypatch, owner):
    from aletheia.server.routes.universe import _attach_candidate_handoff_state
    from aletheia.storage import repositories as repo

    def unavailable(*args):
        raise RuntimeError("read unavailable")
    db = MagicMock()
    db.execute.side_effect = unavailable if owner == "legacy" else None
    monkeypatch.setattr("aletheia.storage.sqlite.get_db", lambda: db)
    monkeypatch.setattr(repo, "get_candidate_planning_for_candidate", unavailable if owner == "generation"
        else lambda _: {"is_current": 1, "entry_plan_id": "plan-1"})
    monkeypatch.setattr(repo, "get_entry_plan", unavailable if owner == "plan" else lambda _: None)
    with pytest.raises(RuntimeError, match="read unavailable"):
        _attach_candidate_handoff_state([{"id": "candidate-1"}])


def test_entry_plan_detail_lineage_reports_missing_plan(tmp_path, monkeypatch):
    from tests.test_sqlite_writer import _setup_db
    from aletheia.server.routes.universe import _entry_plan_lineage

    _setup_db(tmp_path, monkeypatch)
    lineage = _entry_plan_lineage({"id": "missing-plan"})
    assert lineage["availability"] == {"available": False, "reason": "entry_plan_missing"}
    assert lineage["execution_state"] == "UNAVAILABLE"


def test_workbench_surfaces_partial_read_failure_without_fake_zero(client, enabled_cfg):
    with patch("aletheia.server.routes.universe.load_config", return_value=enabled_cfg), \
         patch("aletheia.server.routes.universe.repo.get_active_universe_snapshot", return_value=None), \
         patch(
             "aletheia.server.routes.universe.repo.get_entry_workbench_summary",
             side_effect=RuntimeError("db unavailable"),
         ), \
         patch("aletheia.server.routes.universe.repo.list_active_entry_plans", return_value=[]), \
         patch("aletheia.server.routes.universe.repo.list_current_candidate_workbench", return_value=[]), \
         patch("aletheia.server.routes.universe.repo.list_entry_workbench_candidates", return_value=[]):
        response = client.get("/api/universe/workbench")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] is None
    assert body["availability"]["summary"] == {
        "available": False,
        "stale": False,
        "reason": "RuntimeError",
    }
    assert body["universe"]["status"] == "no_active_snapshot"
    assert body["candidate_attention"] == []
    assert body["current_candidates"] == []


def test_workbench_disabled_overrides_old_snapshot_without_fabricating_candidates(
    client, disabled_cfg
):
    old_snapshot = {
        "id": "old-snapshot",
        "status": "active",
        "expires_at": "2020-01-01T00:00:00+00:00",
    }
    plans = [{"id": "plan-1", "status": "waiting"}]
    with patch("aletheia.server.routes.universe.load_config", return_value=disabled_cfg), \
         patch("aletheia.server.routes.universe.repo.get_active_universe_snapshot", return_value=old_snapshot), \
         patch("aletheia.server.routes.universe.repo.get_entry_workbench_summary", return_value={}), \
         patch("aletheia.server.routes.universe._active_entry_plan_projections", return_value=plans), \
         patch("aletheia.server.routes.universe.repo.list_current_candidate_workbench", return_value=[]), \
         patch("aletheia.server.routes.universe.repo.list_entry_workbench_candidates", return_value=[]):
        response = client.get("/api/universe/workbench")

    body = response.json()
    assert body["universe"]["status"] == "disabled"
    assert body["availability"]["universe"]["stale"] is True
    assert body["universe"]["fallback_instruments"] == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    assert body["candidate_attention"] == []
    assert body["entry_plans"] == plans


def test_workbench_projects_snapshot_candidates_without_planning_generation(client, enabled_cfg):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    snapshot = {"id": "snapshot-1", "status": "active", "expires_at": future}
    rows = [{
        "id": "candidate-1", "snapshot_id": "snapshot-1", "venue": "binance",
        "market_type": "perp", "symbol": "BTC/USDT", "rank": 1,
        "execution_eligible": 0, "risk_flags": '["fast_market_data_venue_unavailable"]',
        "planning_generation_id": None, "planning_status": None,
        "entry_plan_id": None, "entry_plan_status": None,
    }]
    with patch("aletheia.server.routes.universe.load_config", return_value=enabled_cfg), \
         patch("aletheia.server.routes.universe.repo.get_active_universe_snapshot", return_value=snapshot), \
         patch("aletheia.server.routes.universe.repo.get_entry_workbench_summary", return_value={}), \
         patch("aletheia.server.routes.universe._active_entry_plan_projections", return_value=[]), \
         patch("aletheia.server.routes.universe.repo.list_current_candidate_workbench", return_value=rows), \
         patch("aletheia.server.routes.universe.repo.list_entry_workbench_candidates", return_value=[]):
        body = client.get("/api/universe/workbench").json()

    assert body["current_candidates"] == rows
    assert body["candidate_attention"] == []
    assert body["availability"]["current_candidates"]["available"] is True


def test_current_candidate_repository_left_joins_optional_planning_facts():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseAsset, UniverseMember, UniverseSnapshot

    asset = UniverseAsset(id="asset-1", venue="binance", market_type="perp", symbol="BTC/USDT")
    snapshot = UniverseSnapshot(id="snapshot-1", expires_at="2099-01-01T00:00:00+00:00")
    candidate = UniverseMember(
        id="candidate-1", snapshot_id=snapshot.id, asset_id=asset.id,
        venue=None, market_type=asset.market_type, symbol=asset.symbol,
        rank=1, total_score=90, scanner_family="momentum",
        expires_at="2099-01-01T00:00:00+00:00",
        execution_eligible=False,
        risk_flags='["fast_market_data_venue_unavailable"]',
    )
    repo.insert_universe_asset(asset)
    repo.insert_universe_snapshot(snapshot)
    repo.insert_universe_member(candidate)

    [row] = repo.list_current_candidate_workbench(snapshot.id)

    assert row["id"] == candidate.id
    assert row["venue"] == "binance"
    assert row["planning_generation_id"] is None
    assert row["entry_plan_id"] is None
    assert row["risk_flags"] == '["fast_market_data_venue_unavailable"]'


def test_candidate_alias_and_exact_trade_plan_filter(client, monkeypatch):
    monkeypatch.setattr(
        "aletheia.server.routes.universe.get_member",
        lambda candidate_id: {"member": {"id": candidate_id}},
    )
    attention = {
        "id": "generation-1",
        "attention_status": "terminal",
        "attention_terminal_reason": "entry_plan_created",
    }
    monkeypatch.setattr(
        "aletheia.server.routes.universe.repo.get_candidate_planning_for_candidate",
        lambda candidate_id: attention if candidate_id == "candidate-1" else None,
    )
    monkeypatch.setattr(
        "aletheia.server.routes.universe.repo.get_candidate_entry_lifecycle",
        lambda candidate_id: None,
    )
    calls = []
    monkeypatch.setattr(
        "aletheia.server.routes.data.repo.list_orders",
        lambda **kwargs: calls.append(kwargs) or [],
    )

    candidate = client.get("/api/universe/candidates/candidate-1")
    trades = client.get("/api/trades?entry_plan_id=plan-1")

    assert candidate.json()["member"]["id"] == "candidate-1"
    assert candidate.json()["attention"] == attention
    assert trades.status_code == 200
    assert calls == [{"entry_plan_id": "plan-1"}]


def test_refresh_when_disabled(client, disabled_cfg):
    with patch("aletheia.server.routes.universe.load_config", return_value=disabled_cfg):
        resp = client.post("/api/universe/refresh")
    assert resp.status_code == 400
    assert "disabled" in resp.json()["detail"]


def test_refresh_uses_service_snapshot_namespace_with_mixed_config(client):
    cfg = MagicMock()
    cfg.universe = UniverseConfig(enabled=True, market_type="mixed")

    with patch("aletheia.server.routes.universe.load_config", return_value=cfg), \
         patch(
             "aletheia.universe.service.UniverseService.refresh",
             new_callable=AsyncMock,
             return_value="snap_1",
         ) as refresh:
        resp = client.post("/api/universe/refresh")

    assert resp.status_code == 200
    assert resp.json()["snapshot_id"] == "snap_1"
    refresh.assert_awaited_once_with()


def test_list_snapshots(client):
    mock_snaps = [
        {"id": "s1", "status": "active", "created_at": "2025-01-01"},
        {"id": "s2", "status": "superseded", "created_at": "2024-12-31"},
    ]
    with patch("aletheia.server.routes.universe.repo.list_universe_snapshots", return_value=mock_snaps):
        resp = client.get("/api/universe/snapshots")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


def test_candidate_reads_hide_retired_scores_without_rewriting_history(client):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseAsset, UniverseMember, UniverseSnapshot
    from aletheia.storage.sqlite import get_db

    asset = UniverseAsset(id="asset", venue="bitget", market_type="perp", symbol="BTC/USDT")
    snapshot = UniverseSnapshot(id="snapshot", expires_at="2099-01-01T00:00:00+00:00")
    repo.insert_universe_asset(asset)
    repo.insert_universe_snapshot(snapshot)
    for rank, policy in enumerate(("candidate_selection_utility_v1", "legacy_total_score_v1"), 1):
        repo.insert_universe_member(UniverseMember(
            id=policy, snapshot_id=snapshot.id, asset_id=asset.id, symbol=asset.symbol,
            market_type=asset.market_type, rank=rank, total_score=90, liquidity_score=75,
            expires_at=snapshot.expires_at,
        ))
    # Reproduce stored pre-decoupling score fields; this exercises the read
    # projection, not the current Candidate writer's Context validation.
    get_db().execute(
        "UPDATE universe_members SET candidate_selection_policy = ? WHERE id = ?",
        ("candidate_selection_utility_v1", "candidate_selection_utility_v1"),
    )

    response = client.get(f"/api/universe/snapshots/{snapshot.id}")
    assert response.status_code == 200
    for member in response.json()["members"]:
        current = member["id"] == "candidate_selection_utility_v1"
        assert member["total_score"] == (None if current else 90)
        assert member["liquidity_score"] == (None if current else 75)
        detail = client.get(f"/api/universe/members/{member['id']}")
        assert detail.status_code == 200
        assert detail.json()["member"]["total_score"] == member["total_score"]
        assert detail.json()["member"]["liquidity_score"] == member["liquidity_score"]
        stored = repo.get_universe_member(member["id"])
        assert stored["total_score"] == 90
        assert stored["liquidity_score"] == 75


def test_get_policy_returns_config_defaults(client, disabled_cfg):
    with patch("aletheia.server.routes.universe.load_config", return_value=disabled_cfg):
        with patch("aletheia.server.routes.universe.repo.get_active_universe_policy", return_value=None):
            resp = client.get("/api/universe/policy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "config_defaults"
    assert data["explore_ratio"] == 0.2


def test_get_member_not_found(client):
    with patch("aletheia.server.routes.universe.repo.get_universe_member", return_value=None):
        resp = client.get("/api/universe/members/nonexistent")
    assert resp.status_code == 404


def test_get_member_found(client):
    member = {"id": "m1", "asset_id": "a1", "symbol": "BTC/USDT", "scanner_family": "momentum", "rank": 1}
    features = {"id": "f1", "asset_id": "a1", "rsi_14": 55.0, "atr_pct": 2.1}
    opps = [{"id": "o1", "symbol": "BTC/USDT", "status": "open", "direction_bias": "long", "created_at": "2026-01-01T00:00:00"}]
    with patch("aletheia.server.routes.universe.repo.get_universe_member", return_value=member), \
         patch("aletheia.server.routes.universe.repo.get_latest_feature_snapshot", return_value=features), \
         patch("aletheia.server.routes.universe.repo.list_opportunities_by_member", return_value=opps):
        resp = client.get("/api/universe/members/m1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["member"]["symbol"] == "BTC/USDT"
    assert body["member"]["setup_state"] is None
    assert body["features"]["rsi_14"] == 55.0
    assert body["entry_plans"] == []
    assert len(body["opportunities"]) == 1


def test_registry_stats(client):
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = {"c": 100}
    with patch("aletheia.storage.sqlite.get_db", return_value=mock_db):
        resp = client.get("/api/universe/registry/stats")
    assert resp.status_code == 200

def test_workbench_supports_plan_status_filter(client, enabled_cfg):
    expired_plan = {
        "id": "expired-plan-1",
        "planning_generation_id": "gen-1",
        "candidate_id": "c-1",
        "status": "expired",
        "plan_json": json.dumps({"thesis": "test", "expires_at": "2026-01-01T00:00:00Z"}),
        "plan_hash": "a" * 64,
        "contract_valid": True,
    }
    with patch("aletheia.server.routes.universe.load_config", return_value=enabled_cfg), \
         patch("aletheia.server.routes.universe.repo.get_active_universe_snapshot", return_value=None), \
         patch("aletheia.server.routes.universe.repo.get_entry_workbench_summary", return_value={}), \
         patch("aletheia.server.routes.universe.repo.list_entry_plans", return_value=[expired_plan]), \
         patch("aletheia.server.routes.universe.repo.list_current_candidate_workbench", return_value=[]), \
         patch("aletheia.server.routes.universe.repo.list_entry_workbench_candidates", return_value=[]):
        resp = client.get("/api/universe/workbench?plan_status=expired")
    plans = resp.json()["entry_plans"]
    assert len(plans) == 1
    assert plans[0]["id"] == "expired-plan-1"
    assert plans[0]["status"] == "expired"


def test_universe_openapi_preserves_repository_projection_json(client, monkeypatch):
    """Read schemas must retain SQLite nulls, numeric flags and nested ledger facts."""
    from pydantic import TypeAdapter
    from aletheia.config.loader import load_config, save_config
    from aletheia.server.routes import universe
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseAsset, UniverseFeatureSnapshot, UniversePolicy
    from tests.test_entry_planner import _candidate, _plan
    from tests.test_universe_calibration import _settled
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION

    cfg = load_config()
    cfg.market_data.ccxt_exchange = "bitget"
    save_config(cfg)
    instant = datetime.now(timezone.utc)

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant

    monkeypatch.setattr(universe, "datetime", FrozenClock)
    candidate = _candidate()
    repo.insert_universe_asset(UniverseAsset(
        id=candidate.asset_id, venue="bitget", market_type="perp", symbol=candidate.symbol,
    ))
    repo.insert_universe_feature_snapshot(UniverseFeatureSnapshot(asset_id=candidate.asset_id))
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    repo.claim_next_candidate_planning("schema-proof")
    plan = _plan(generation)
    assert repo.complete_candidate_planning_with_plan(generation["id"], "schema-proof", plan)[0] == "planned"
    repo.insert_forward_outcomes_batch([
        _settled("BTC/USDT", "long", 1.0, member_id=candidate.id, cost_model_version=COST_MODEL_VERSION),
    ])

    calls = [
        ("/current", universe.get_current_universe, {}, universe.UniverseCurrentResponse),
        ("/snapshots", universe.list_snapshots, {}, list[universe.UniverseSnapshot]),
        (f"/snapshots/{candidate.snapshot_id}", universe.get_snapshot, {"snapshot_id": candidate.snapshot_id}, universe.UniverseSnapshotResponse),
        (f"/candidates/{candidate.id}", universe.get_candidate, {"candidate_id": candidate.id}, universe.CandidateDetail),
        (f"/entry-plans/{plan.plan_id}", universe.get_entry_plan_detail, {"plan_id": plan.plan_id}, universe.EntryPlanProjection),
        ("/workbench", universe.get_entry_workbench, {"limit": 100, "plan_status": "active"}, universe.EntryWorkbenchResponse),
        ("/planning-history", universe.get_planning_history, {"limit": 50}, universe.PlanningHistoryResponse),
        ("/policy", universe.get_current_policy, {}, universe.UniverseStoredPolicy | universe.UniverseDefaultPolicy),
        ("/registry/stats", universe.registry_stats, {}, universe.UniverseRegistryStats),
        ("/calibration", universe.get_calibration, {}, universe.CalibrationResponse),
    ]
    for path, endpoint, kwargs, schema in calls:
        original = endpoint(**kwargs)
        response = client.get(f"/api/universe{path}")
        assert response.status_code == 200, path
        assert response.json() == original, path
        adapter = TypeAdapter(schema)
        validated = adapter.validate_json(response.content, strict=True)
        assert adapter.dump_python(validated, mode="json", exclude_unset=True) == original, path

    policy = UniversePolicy(active=True)
    repo.insert_universe_policy(policy)
    stored = client.get("/api/universe/policy").json()
    assert TypeAdapter(universe.UniverseStoredPolicy).validate_python(stored, strict=True) == stored
    cfg.universe.enabled = False
    monkeypatch.setattr(universe, "load_config", lambda: cfg)
    monkeypatch.setattr("aletheia.universe.service.load_config", lambda: cfg)
    fallback = client.get("/api/universe/current").json()
    assert fallback["members"] == []
    assert fallback["fallback"] is False
    assert TypeAdapter(universe.UniverseCurrentResponse).validate_python(fallback, strict=True) == fallback

    assert client.get("/api/universe/calibration").json()["by_cohort_horizon"][0]["source_outcome_fingerprint"]
    with patch.object(universe.repo, "get_active_universe_snapshot", side_effect=RuntimeError("read unavailable")):
        partial = client.get("/api/universe/workbench").json()
    assert partial["universe"]["enabled"] is None
    assert partial["current_candidates"] is None
    assert partial["availability"]["current_candidates"]["available"] is False
    adapter = TypeAdapter(universe.EntryWorkbenchResponse)
    parsed = adapter.validate_json(json.dumps(partial), strict=True)
    assert adapter.dump_python(parsed, mode="json", exclude_unset=True) == partial
    assert client.get("/api/universe/snapshots/missing").status_code == 404
    assert client.get("/api/universe/entry-plans/missing").status_code == 404
    with patch.object(universe.repo, "get_entry_plan", return_value={"plan_json": "{}", "plan_hash": "bad"}):
        assert client.get("/api/universe/entry-plans/corrupt").status_code == 409

    import hashlib
    legacy_payload = {**plan.model_dump(mode="json"), "version": "entry-plan/v1",
                      "trigger_spec_version": "v2", "historical_note": {"reason": None}}
    legacy_payload.pop("evidence_refs")
    legacy_json = json.dumps(legacy_payload)
    historical = {**repo.get_entry_plan(plan.plan_id), "plan_json": legacy_json,
                  "plan_hash": hashlib.sha256(legacy_json.encode()).hexdigest()}
    with patch.object(universe.repo, "get_entry_plan", return_value=historical):
        response = client.get(f"/api/universe/entry-plans/{plan.plan_id}")
        assert response.status_code == 200
        legacy = response.json()
    adapter = TypeAdapter(universe.EntryPlanProjection)
    parsed = adapter.validate_json(json.dumps(legacy), strict=True)
    assert adapter.dump_python(parsed, mode="json", exclude_unset=True) == legacy
    assert legacy["plan"] == legacy_payload

    from aletheia.server.routes.settings import CandidateSelectionStatusResponse
    selection = client.get("/api/settings/universe/candidate-selection").json()
    assert TypeAdapter(CandidateSelectionStatusResponse).validate_json(json.dumps(selection), strict=True) == selection
    cfg.universe.enabled = True
    with patch("aletheia.universe.service.UniverseService.refresh", new_callable=AsyncMock, return_value=None):
        assert client.post("/api/universe/refresh").status_code == 500
    with patch("aletheia.universe.service.UniverseService.refresh", new_callable=AsyncMock, return_value="snapshot-proof"):
        refreshed = client.post("/api/universe/refresh")
    assert refreshed.status_code == 200
    assert TypeAdapter(universe.UniverseRefreshResponse).validate_json(refreshed.content, strict=True) == refreshed.json()
