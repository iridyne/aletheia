import asyncio
from types import SimpleNamespace

import pytest


def _cfg(permission: str, route: str, live_state: str = "live_disabled"):
    return SimpleNamespace(
        agent=SimpleNamespace(permission_mode=permission),
        exchange=SimpleNamespace(paper_execution_mode=route),
        live=SimpleNamespace(state=live_state),
    )


def _typed_candidate(
    *, source_at: str = "2026-09-03T00:00:00+00:00", primary_ref: str = "fact-1"
):
    from uuid import uuid4

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseMember, UniverseSnapshot
    from aletheia.universe.candidate_opportunity import CandidateOpportunityContextV1

    candidate_id = uuid4().hex[:12]
    context = CandidateOpportunityContextV1(
        candidate_id=candidate_id,
        candidate_selection={
            "policy": "candidate_selection_utility_v1",
            "policy_epoch": "epoch-1",
            "source_record_hash": "a" * 64,
        },
        source={
            "detected_at": source_at, "source_cutoff_at": source_at,
            "feature_snapshot_id": "feature-1",
        },
        opportunity={
            "primary_mechanism": "reversal", "mechanism_version": "reversal/v1",
            "mechanism_source": "target_authority", "source_component_id": "mean_reversion",
            "direction": "long", "horizon": "4h", "economic_utility_pct": 1.0,
            "capability_status": "supported", "capability_gaps": (),
            "primary_evidence_refs": (primary_ref,), "secondary_mechanisms": (),
        },
        context={"regime": "trending"},
    )
    snapshot = UniverseSnapshot(
        created_at=source_at, expires_at="2099-01-01T00:00:00+00:00"
    )
    repo.insert_universe_snapshot(snapshot)
    candidate = UniverseMember(
        id=candidate_id, snapshot_id=snapshot.id, asset_id="asset-btc",
        venue="bitget", symbol="BTC/USDT", market_type="perp", rank=1,
        total_score=90, scanner_family="mean_reversion", scanner_direction="long",
        regime_tag="trending", entry_price_usd=100,
        expires_at="2099-01-01T00:00:00+00:00",
        candidate_selection_policy="candidate_selection_utility_v1",
        candidate_selection_policy_epoch="epoch-1", candidate_selection_utility_pct=1.0,
        opportunity_mechanism="reversal", opportunity_mechanism_version="reversal/v1",
        opportunity_source_component="mean_reversion",
        opportunity_source_cutoff_at=source_at, opportunity_evidence_hash="b" * 64,
        candidate_opportunity_context_version=context.version,
        candidate_opportunity_context_json=context.model_dump_json(),
        candidate_opportunity_context_hash=context.context_hash,
    )
    repo.insert_candidate(candidate)
    # Historical projections can be NULL while the frozen context retains its clock.
    repo.execute_write(
        "UPDATE universe_members SET opportunity_source_cutoff_at=NULL WHERE id=?",
        (candidate.id,),
    )
    return candidate


@pytest.mark.parametrize(
    ("permission", "route", "allowed", "minimum", "reason"),
    [
        ("Paper", "paper_internal", True, "Paper", None),
        ("Full", "paper_internal", True, "Paper", None),
        ("Paper", "bitget_demo", False, "Full", "entry_permission_insufficient"),
        ("Full", "bitget_demo", True, "Full", None),
        ("Observe", "paper_internal", False, "Paper", "entry_permission_insufficient"),
        ("Full", "binance_testnet", False, None, "entry_execution_route_unsupported"),
    ],
)
def test_entry_runtime_authority_matrix(permission, route, allowed, minimum, reason):
    from aletheia.trading.entry_authority import resolve_entry_runtime_authority

    authority = resolve_entry_runtime_authority(_cfg(permission, route))

    assert authority.allowed is allowed
    assert authority.minimum_permission == minimum
    assert authority.reason == reason


@pytest.mark.parametrize("permission", ["Paper", "Full"])
def test_entry_runtime_authority_keeps_live_separate(permission):
    from aletheia.trading.entry_authority import resolve_entry_runtime_authority

    authority = resolve_entry_runtime_authority(
        _cfg(permission, "paper_internal", "live_limited")
    )

    assert authority.allowed is False
    assert authority.reason == "entry_live_authority_unavailable"


def test_runtime_block_resumes_same_cycle_without_model_attempt(_inmemory_db):
    from datetime import datetime, timedelta, timezone

    from aletheia.storage import repositories as repo
    candidate = _typed_candidate()
    start = datetime(2026, 9, 4, tzinfo=timezone.utc)
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id, now=start)
    claimed = repo.claim_next_candidate_planning("owner", now=start)
    assert claimed and claimed["attempt_count"] == 1

    assert repo.block_candidate_planning(
        generation["id"], "owner", "entry_permission_insufficient",
        stage="runtime_preflight", now=start,
    )
    blocked = repo.get_candidate_planning_generation(generation["id"])

    assert blocked["outcome_class"] == "planning_blocked"
    assert blocked["last_stage"] == "runtime_preflight"
    assert blocked["model_invoked"] == 0
    assert blocked["compiler_invoked"] == 0
    assert blocked["attempt_count"] == 0
    assert blocked["cycle_number"] == 1
    assert repo.claim_next_candidate_planning(
        "recovered", now=start + timedelta(seconds=61)
    )["id"] == generation["id"]


def test_legacy_runtime_no_plan_is_not_economic_learning_evidence():
    from aletheia.trading.entry_compat import classify_legacy_planning_outcome

    outcome = classify_legacy_planning_outcome(
        {"status": "no_plan", "result_json": '{"reason_code":"AI_ENTRY_RUNTIME_UNSUPPORTED"}'}
    )

    assert outcome.outcome_class == "planning_blocked"
    assert outcome.economic_no_plan is False


def test_legacy_entry_reader_preserves_exact_payload_bytes_and_hashes():
    import hashlib

    from aletheia.trading.entry_compat import read_legacy_entry_record

    planner_input = '{ "context_version": "entry-analysis-context/v1" }'
    plan_json = '{ "version": "entry-plan/v2", "status": "waiting" }'
    result_json = '{ "reason_code": "AI_ENTRY_RUNTIME_UNSUPPORTED" }'
    generation = {
        "planner_contract_version": "entry-planner/v3",
        "planner_input_json": planner_input,
        "planner_input_hash": hashlib.sha256(planner_input.encode()).hexdigest(),
        "result_json": result_json,
    }
    plan = {
        "plan_json": plan_json,
        "plan_hash": hashlib.sha256(plan_json.encode()).hexdigest(),
    }

    projected = read_legacy_entry_record(generation, plan)

    assert projected.planner_input_json == planner_input
    assert projected.plan_json == plan_json
    assert projected.result_json == result_json
    assert projected.planner_input_hash_valid is True
    assert projected.plan_hash_valid is True
    assert generation["planner_input_json"] == planner_input
    assert plan["plan_json"] == plan_json


def _insert_expired_plan(repo, generation: dict, candidate_id: str, plan_id: str, expires_at: str):
    import hashlib
    import json

    payload = json.dumps({"expires_at": expires_at}, sort_keys=True, separators=(",", ":"))
    repo._insert("entry_plans", {
        "id": plan_id, "planning_generation_id": generation["id"],
        "candidate_id": candidate_id, "planning_key": generation["planning_key"],
        "version": "entry-plan/v1", "venue": "bitget", "symbol": "BTC/USDT",
        "market_type": "perp", "direction": "long", "execution_mode": "RESTING_MAKER",
        "plan_json": payload, "plan_hash": hashlib.sha256(payload.encode()).hexdigest(),
        "status": "expired", "created_at": expires_at, "updated_at": expires_at,
        "terminal_at": expires_at, "terminal_reason": "plan_expired",
    })


def _bind_test_context(repo, generation: dict) -> None:
    from aletheia.trading.entry_analysis import canonical_json, fingerprint
    from tests.test_entry_analysis import _anchor, _context_v2

    context = _context_v2((
        _anchor("candidate_reference", 100.0, f"candidate:{generation['last_candidate_id']}"),
        _anchor("confirmed_swing", 95.0, "swing:s1"),
    ))
    repo.execute_write(
        "UPDATE candidate_planning_generations SET planner_input_json=?,planner_input_hash=? WHERE id=?",
        (
            canonical_json(context.model_dump(mode="json")),
            fingerprint(context.model_dump(mode="json")),
            generation["id"],
        ),
    )


def test_expired_plan_replans_only_after_later_completed_requalification(_inmemory_db):
    from aletheia.storage import repositories as repo
    first_candidate = _typed_candidate(
        source_at="2026-09-03T00:00:00+00:00",
    )
    first, _ = repo.ensure_candidate_planning_generation(first_candidate.id)
    _insert_expired_plan(
        repo, first, first_candidate.id, "expired-1", "2026-09-03T01:00:00+00:00"
    )

    stale = _typed_candidate(
        source_at="2026-09-03T00:30:00+00:00",
    )
    same, created = repo.ensure_candidate_planning_generation(stale.id)
    assert created is False
    assert same["id"] == first["id"]

    later = _typed_candidate(
        source_at="2026-09-03T02:00:00+00:00",
    )
    second, created = repo.ensure_candidate_planning_generation(later.id)
    assert created is True
    assert second["cycle_number"] == 2
    assert second["previous_generation_id"] == first["id"]
    assert second["cycle_reason"] == "plan_expired_requalified"

    lifecycle = repo.get_candidate_entry_lifecycle(later.id)
    assert lifecycle["lifecycle_state"] == "REPLAN_QUEUED"
    assert lifecycle["expired_plan_count"] == 1

    replayed, created = repo.ensure_candidate_planning_generation(later.id)
    assert created is False
    assert replayed["id"] == second["id"]
    _insert_expired_plan(
        repo, second, later.id, "expired-2", "2026-09-03T03:00:00+00:00"
    )
    replayed, created = repo.ensure_candidate_planning_generation(later.id)
    assert created is False
    assert replayed["id"] == second["id"]
    lifecycle = repo.get_candidate_entry_lifecycle(later.id)
    assert lifecycle["lifecycle_state"] == "PLAN_EXPIRED"
    assert lifecycle["current_cycle_number"] == 2
    assert lifecycle["expired_plan_count"] == 2
    assert "candidate_selected" in {
        event["event"] for event in lifecycle["cycles"][0]["events"]
    }
    assert "candidate_requalified" in {
        event["event"] for event in lifecycle["cycles"][1]["events"]
    }


def test_no_plan_rotates_only_for_later_material_evidence(_inmemory_db):
    from aletheia.storage import repositories as repo
    first_candidate = _typed_candidate(
        source_at="2026-09-03T00:00:00+00:00", primary_ref="fact-a",
    )
    first, _ = repo.ensure_candidate_planning_generation(first_candidate.id)
    _bind_test_context(repo, first)
    frozen = repo.get_candidate_planning_generation(first["id"])
    repo.execute_write(
        "UPDATE candidate_planning_generations SET status='no_plan', result_kind='no_plan', "
        "result_json='{}', attention_status='terminal' WHERE id=?",
        (first["id"],),
    )
    same_evidence = _typed_candidate(
        source_at="2026-09-03T01:00:00+00:00", primary_ref="fact-a",
    )
    same, created = repo.ensure_candidate_planning_generation(same_evidence.id)
    assert created is False
    assert same["id"] == first["id"]

    for source_at, status in (
        ("2026-09-03T00:00:00+00:00", "active"),
        ("2026-09-02T23:00:00+00:00", "active"),
        ("2026-09-03T02:00:00+00:00", "superseded"),
    ):
        unavailable = _typed_candidate(source_at=source_at, primary_ref="fact-b")
        repo.execute_write("UPDATE universe_snapshots SET status=? WHERE id=?", (status, unavailable.snapshot_id))
        same, created = repo.ensure_candidate_planning_generation(unavailable.id)
        assert created is False and same["id"] == first["id"]
    new_evidence = _typed_candidate(
        source_at="2026-09-03T02:00:00+00:00", primary_ref="fact-b",
    )
    second, created = repo.ensure_candidate_planning_generation(new_evidence.id)
    assert created is True
    assert second["cycle_number"] == 2
    assert second["cycle_reason"] == "material_input_changed"
    replay, created = repo.ensure_candidate_planning_generation(new_evidence.id)
    assert not created and replay["id"] == second["id"]
    old = repo.get_candidate_planning_generation(first["id"])
    assert (old["planner_input_json"], old["planner_input_hash"]) == (
        frozen["planner_input_json"], frozen["planner_input_hash"],
    )
    assert not repo.bind_candidate_planner_input(
        first["id"], "old-owner", frozen["planner_input_json"], frozen["planner_input_hash"],
    )


def test_unresolved_prior_order_prevents_successor_cycle(_inmemory_db):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    first_candidate = _typed_candidate(source_at="2026-09-03T00:00:00+00:00")
    first, _ = repo.ensure_candidate_planning_generation(first_candidate.id)
    _insert_expired_plan(
        repo, first, first_candidate.id, "expired-with-order", "2026-09-03T01:00:00+00:00"
    )
    order = Order(
        symbol="BTC/USDT", market_type="perp", direction="long",
        order_type="limit", quantity=1, price=100, execution_mode="paper_internal",
        entry_plan_id="expired-with-order", status="submission_unknown",
    )
    repo._insert("orders", order.model_dump())

    later = _typed_candidate(source_at="2026-09-03T02:00:00+00:00")
    same, created = repo.ensure_candidate_planning_generation(later.id)
    manual, manual_created = repo.ensure_candidate_planning_generation(
        later.id, manual_replan=True
    )

    assert created is False and manual_created is False
    assert same["id"] == manual["id"] == first["id"]


def test_owner_candidate_projection_hides_numeric_planner_labels(_inmemory_db):
    from aletheia.server.routes.universe import get_candidate
    from aletheia.storage import repositories as repo
    candidate = _typed_candidate()
    assert repo.get_candidate_entry_lifecycle(candidate.id)["lifecycle_state"] == (
        "AWAITING_PLANNING"
    )
    repo.ensure_candidate_planning_generation(candidate.id)

    detail = get_candidate(candidate.id)

    assert "planner_contract_version" not in detail["attention"]
    assert detail["lifecycle"]["current_cycle_number"] == 1
    assert detail["lifecycle"]["lifecycle_state"] == "PLANNING_QUEUED"


def test_full_internal_route_reaches_one_model_and_compiler(_inmemory_db, monkeypatch):
    from aletheia.agent_shell.entry_planner import PlanIntentError, plan_next_candidate
    from aletheia.storage import repositories as repo
    from aletheia.trading.entry_analysis import fingerprint
    from tests.test_entry_analysis import _anchor, _context_v2
    candidate = _typed_candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    primary = _anchor("candidate_reference", 100.0, f"candidate:{candidate.id}")
    invalidation = _anchor("confirmed_swing", 95.0, "swing:s1")
    context = _context_v2((primary, invalidation))
    payload = context.model_dump_json()
    repo.execute_write(
        "UPDATE candidate_planning_generations SET planner_input_json=?,planner_input_hash=? WHERE id=?",
        (payload, fingerprint(context.model_dump(mode="json")), generation["id"]),
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.entry_planner.fast_market_data_unavailability_reason",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.entry_planner.validate_hypothesis_references",
        lambda *_args: None,
    )
    compiler_calls = 0

    def compiler(*_args):
        nonlocal compiler_calls
        compiler_calls += 1
        raise PlanIntentError("EXPECTED_COMPILER_STOP")

    monkeypatch.setattr("aletheia.trading.entry_plan_compiler.compile_entry_plan", compiler)

    async def planner(_context):
        return {
            "decision": "ENTRY_PLAN", "reason_code": "OK",
            "mechanism": "reversal", "direction": "long",
            "primary_anchor_ref": primary.anchor_id,
            "invalidation_anchor_ref": invalidation.anchor_id,
            "entry_profile_id": "grounded-anchor-resting-maker",
            "execution_urgency": "PASSIVE", "risk_tier": "LOW",
            "horizon": "INTRADAY",
            "supporting_fact_refs": [f"candidate:{candidate.id}"],
        }

    result = asyncio.run(plan_next_candidate(
        planner, cfg=_cfg("Full", "paper_internal"), owner_token="owner"
    ))
    stored = repo.get_candidate_planning_generation(generation["id"])

    assert result["status"] == "failed"
    assert compiler_calls == 1
    assert stored["model_invoked"] == 1
    assert stored["compiler_invoked"] == 1
    assert stored["outcome_class"] == "planning_failed"


def test_current_economic_no_plan_is_terminal_and_not_retried(_inmemory_db, monkeypatch):
    from aletheia.agent_shell.entry_planner import plan_next_candidate
    from aletheia.storage import repositories as repo

    candidate = _typed_candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    _bind_test_context(repo, generation)
    monkeypatch.setattr(
        "aletheia.agent_shell.entry_planner.fast_market_data_unavailability_reason",
        lambda *_args: None,
    )

    async def planner(_context):
        return {
            "decision": "NO_PLAN", "reason_code": "CONFLICTING_SIGNALS",
            "mechanism": "reversal", "direction": "long",
            "risk_tier": "NONE", "horizon": "NONE",
        }

    result = asyncio.run(plan_next_candidate(planner, cfg=_cfg("Full", "paper_internal")))
    stored = repo.get_candidate_planning_generation(generation["id"])

    assert result["status"] == "no_plan"
    assert stored["outcome_class"] == "no_plan"
    assert stored["model_invoked"] == 1
    assert stored["compiler_invoked"] == 0
    assert repo.claim_next_candidate_planning("duplicate") is None


def test_current_schema_failure_is_non_economic_and_terminal(_inmemory_db, monkeypatch):
    from aletheia.agent_shell.entry_planner import plan_next_candidate
    from aletheia.storage import repositories as repo

    candidate = _typed_candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    _bind_test_context(repo, generation)
    monkeypatch.setattr(
        "aletheia.agent_shell.entry_planner.fast_market_data_unavailability_reason",
        lambda *_args: None,
    )

    async def planner(_context):
        return {"decision": "ENTRY_PLAN"}

    result = asyncio.run(plan_next_candidate(planner, cfg=_cfg("Full", "paper_internal")))
    stored = repo.get_candidate_planning_generation(generation["id"])

    assert result["status"] == "failed"
    assert stored["outcome_class"] == "planning_failed"
    assert stored["attention_status"] == "terminal"
    assert stored["model_invoked"] == 1
    assert stored["compiler_invoked"] == 0


def test_current_broker_backpressure_consumes_no_attempt(_inmemory_db, monkeypatch):
    from aletheia.agent_shell.entry_planner import (
        PlannerBackpressure,
        plan_next_candidate,
    )
    from aletheia.storage import repositories as repo

    candidate = _typed_candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    _bind_test_context(repo, generation)
    monkeypatch.setattr(
        "aletheia.agent_shell.entry_planner.fast_market_data_unavailability_reason",
        lambda *_args: None,
    )

    async def planner(_context):
        raise PlannerBackpressure("inference_queue_full")

    result = asyncio.run(plan_next_candidate(planner, cfg=_cfg("Full", "paper_internal")))
    stored = repo.get_candidate_planning_generation(generation["id"])

    assert result["status"] == "backpressure"
    assert stored["attempt_count"] == 0
    assert stored["model_invoked"] == 0
    assert stored["compiler_invoked"] == 0


def test_post_inference_authority_change_blocks_before_compiler(_inmemory_db, monkeypatch):
    from aletheia.agent_shell.entry_planner import plan_next_candidate
    from aletheia.storage import repositories as repo

    candidate = _typed_candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    _bind_test_context(repo, generation)
    monkeypatch.setattr(
        "aletheia.agent_shell.entry_planner.fast_market_data_unavailability_reason",
        lambda *_args: None,
    )
    cfg = _cfg("Full", "paper_internal")

    async def planner(_context):
        cfg.agent.permission_mode = "Observe"
        return {
            "decision": "ENTRY_PLAN", "reason_code": "OK",
            "mechanism": "reversal", "direction": "long",
            "primary_anchor_ref": "ignored-until-authority-recovers",
            "invalidation_anchor_ref": "also-ignored",
            "entry_profile_id": "grounded-anchor-resting-maker",
            "execution_urgency": "PASSIVE", "risk_tier": "LOW",
            "horizon": "INTRADAY",
            "supporting_fact_refs": ["ignored"],
        }

    result = asyncio.run(plan_next_candidate(planner, cfg=cfg))
    stored = repo.get_candidate_planning_generation(generation["id"])

    assert result["status"] == "blocked"
    assert stored["last_stage"] == "post_inference_authority"
    assert stored["model_invoked"] == 1
    assert stored["compiler_invoked"] == 0
    assert stored["outcome_class"] == "planning_blocked"
    assert stored["attempt_count"] == 1
