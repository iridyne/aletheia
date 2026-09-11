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
        ("Paper", "paper_internal", False, None, "entry_execution_route_unsupported"),
        ("Full", "paper_internal", False, None, "entry_execution_route_unsupported"),
        ("Paper", "bitget_demo", False, "Full", "entry_permission_insufficient"),
        ("Full", "bitget_demo", True, "Full", None),
        ("Observe", "bitget_demo", False, "Full", "entry_permission_insufficient"),
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
        _cfg(permission, "bitget_demo", "live_limited")
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
        order_type="limit", quantity=1, price=100, execution_mode="bitget_demo",
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


def test_full_demo_route_reaches_one_model_and_compiler(_inmemory_db, monkeypatch):
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
            "entry_profile_id": "grounded-anchor-direct",
            "capture_priority": "PRICE_FIRST", "capture_reason": "current structure", "capture_evidence_refs": [invalidation.anchor_id], "risk_tier": "LOW",
            "horizon": "INTRADAY",
            "supporting_fact_refs": [invalidation.anchor_id],
        }

    result = asyncio.run(plan_next_candidate(
        planner, cfg=_cfg("Full", "bitget_demo"), owner_token="owner"
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

    result = asyncio.run(plan_next_candidate(planner, cfg=_cfg("Full", "bitget_demo")))
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

    result = asyncio.run(plan_next_candidate(planner, cfg=_cfg("Full", "bitget_demo")))
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

    result = asyncio.run(plan_next_candidate(planner, cfg=_cfg("Full", "bitget_demo")))
    stored = repo.get_candidate_planning_generation(generation["id"])

    assert result["status"] == "backpressure"
    assert stored["attempt_count"] == 0
    assert stored["model_invoked"] == 0
    assert stored["compiler_invoked"] == 0


def test_post_inference_authority_change_blocks_before_compiler(_inmemory_db, monkeypatch):
    from aletheia.agent_shell.entry_planner import PlanIntentError, plan_next_candidate
    from aletheia.storage import repositories as repo

    candidate = _typed_candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    _bind_test_context(repo, generation)
    monkeypatch.setattr(
        "aletheia.agent_shell.entry_planner.fast_market_data_unavailability_reason",
        lambda *_args: None,
    )
    cfg = _cfg("Full", "bitget_demo")
    calls = []

    async def planner(_context):
        calls.append(_context)
        cfg.agent.permission_mode = "Observe"
        return {
            "decision": "ENTRY_PLAN", "reason_code": "OK",
            "mechanism": "reversal", "direction": "long",
            "primary_anchor_ref": _context["anchors"][0]["anchor_id"],
            "invalidation_anchor_ref": _context["anchors"][1]["anchor_id"],
            "entry_profile_id": "grounded-anchor-direct",
            "capture_priority": "PRICE_FIRST", "capture_reason": "current structure", "capture_evidence_refs": [_context["anchors"][1]["anchor_id"]], "risk_tier": "LOW",
            "horizon": "INTRADAY",
            "supporting_fact_refs": [_context["anchors"][1]["anchor_id"]],
        }

    result = asyncio.run(plan_next_candidate(planner, cfg=cfg))
    stored = repo.get_candidate_planning_generation(generation["id"])

    assert result["status"] == "blocked"
    assert stored["last_stage"] == "post_inference_authority"
    assert stored["model_invoked"] == 1
    assert stored["compiler_invoked"] == 0
    assert stored["outcome_class"] == "planning_blocked"
    assert stored["attempt_count"] == 1
    assert stored["validated_hypothesis_json"] is not None
    cfg.agent.permission_mode = "Full"
    repo.execute_write("UPDATE candidate_planning_generations SET retry_not_before=NULL WHERE id=?",
                       (generation["id"],))
    def compiler(_generation, _context, hypothesis, _cfg):
        assert hypothesis == type(hypothesis).model_validate_json(stored["validated_hypothesis_json"])
        raise PlanIntentError("EXPECTED_COMPILER_STOP")
    monkeypatch.setattr("aletheia.trading.entry_plan_compiler.compile_entry_plan", compiler)
    resumed = asyncio.run(plan_next_candidate(planner, cfg=cfg))
    assert resumed["status"] == "failed" and resumed["model_invoked"] is False
    assert resumed["compiler_invoked"] is True
    assert len(calls) == 1
    assert repo.get_candidate_planning_generation(generation["id"])["validated_hypothesis_json"] == stored["validated_hypothesis_json"]


def test_hypothesis_checkpoint_requires_live_claim_and_is_write_once(_inmemory_db):
    import sqlite3
    from datetime import datetime, timedelta, timezone
    from aletheia.storage import repositories as repo
    from aletheia.trading.entry_analysis import EntryHypothesis

    now = datetime.now(timezone.utc)
    candidate = _typed_candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id, now=now)
    _bind_test_context(repo, generation)
    repo.claim_next_candidate_planning("first-owner", now=now)
    hypothesis = EntryHypothesis(decision="NO_PLAN", reason_code="CONFLICTING_SIGNALS",
                                mechanism="reversal", direction="long", risk_tier="NONE", horizon="NONE")
    expired = now + timedelta(seconds=301)
    assert not repo.bind_candidate_planning_hypothesis(generation["id"], "first-owner",
                                                     hypothesis.model_dump_json(), now=expired)
    claimed = repo.claim_next_candidate_planning("second-owner", now=expired)
    assert claimed["id"] == generation["id"]
    assert not repo.bind_candidate_planning_hypothesis(generation["id"], "first-owner",
                                                     hypothesis.model_dump_json(), now=expired)
    assert repo.bind_candidate_planning_hypothesis(generation["id"], "second-owner",
                                                 hypothesis.model_dump_json(), now=expired)
    checkpoint = repo.get_candidate_planning_generation(generation["id"])["validated_hypothesis_json"]
    assert repo.bind_candidate_planning_hypothesis(generation["id"], "second-owner",
                                                 checkpoint, now=expired)
    other = hypothesis.model_copy(update={"reason_code": "INSUFFICIENT_EVIDENCE"})
    assert not repo.bind_candidate_planning_hypothesis(generation["id"], "second-owner",
                                                     other.model_dump_json(), now=expired)
    with pytest.raises(sqlite3.IntegrityError, match="hypothesis is immutable"):
        repo.execute_write("UPDATE candidate_planning_generations SET validated_hypothesis_json=NULL WHERE id=?",
                           (generation["id"],))
    assert repo.get_candidate_planning_generation(generation["id"])["validated_hypothesis_json"] == checkpoint


def _legacy_structure_candidate(source_at, *, price=100, rank=1):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseMember, UniverseSnapshot
    from aletheia.universe.candidate_opportunity import legacy_candidate_opportunity_context

    snapshot = UniverseSnapshot(created_at=source_at, expires_at="2099-01-01T00:00:00+00:00")
    repo.insert_universe_snapshot(snapshot)
    member = UniverseMember(snapshot_id=snapshot.id, asset_id="asset-btc", venue="bitget",
        symbol="BTC/USDT", market_type="perp", rank=rank, total_score=90-rank,
        scanner_family="momentum", scanner_direction="long", entry_price_usd=price,
        expires_at="2099-01-01T00:00:00+00:00")
    context = legacy_candidate_opportunity_context(candidate_id=member.id,
        candidate=member.model_dump(), detected_at=source_at)
    member.candidate_opportunity_context_json = context.model_dump_json()
    member.candidate_opportunity_context_hash = context.context_hash
    repo.insert_candidate(member)
    return member


def _seed_requalification_bars():
    import math
    from datetime import datetime, timezone
    from aletheia.data.providers import Candle
    from aletheia.storage import repositories as repo

    start = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
    interval = 900_000
    bars = []
    for index in range(104):
        price = 100 + math.sin(index * math.pi / 8)
        bars.append(Candle(start + index * interval, price, price + .2, price - .2, price, 1000))
    repo.upsert_ohlcv_candles("BTC/USDT", "15m", bars, exchange="bitget", market_type="perp")
    repo.execute_write("UPDATE market_ohlcv SET first_persisted_at=?,last_mutated_at=?",
        ("2026-09-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00"))
    return [datetime.fromtimestamp((start + n * interval) / 1000, timezone.utc)
        for n in (100, 101, 104)]





def test_legacy_no_plan_does_not_loop_on_price_rank_or_new_bar(_inmemory_db):
    from aletheia.storage import repositories as repo
    first_at, unchanged_at, _ = _seed_requalification_bars()
    candidate = _legacy_structure_candidate(first_at.isoformat())
    first, _ = repo.ensure_candidate_planning_generation(candidate.id, now=first_at)
    repo.execute_write("UPDATE candidate_planning_generations SET status='no_plan', "
        "result_kind='no_plan',attention_status='terminal' WHERE id=?", (first["id"],))
    replacements = []
    for rank in (2, 3):
        later = _legacy_structure_candidate(unchanged_at.isoformat(), price=104, rank=rank)
        replacements.append(later.id)
        same, created = repo.ensure_candidate_planning_generation(later.id, now=unchanged_at)
        assert not created and same["id"] == first["id"]
    assert repo.get_candidate_planning_generation(first["id"])["last_candidate_id"] == candidate.id
    for candidate_id in replacements:
        associated = repo.get_candidate_planning_for_candidate(candidate_id, now=unchanged_at)
        assert associated is not None
        assert associated["id"] == first["id"]
        repo.ensure_candidate_planning_generation(candidate_id, now=unchanged_at)
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM audit_events WHERE event_type='candidate_planning_associated'"
    ).fetchone()[0] == 2
    rolled_back = _legacy_structure_candidate(unchanged_at.isoformat(), rank=4)
    with pytest.raises(RuntimeError, match="publication failed"):
        with repo.transaction(immediate=True):
            repo.ensure_candidate_planning_generation(rolled_back.id, now=unchanged_at)
            raise RuntimeError("publication failed")
    assert repo.get_candidate_planning_for_candidate(rolled_back.id) is None


def test_requalification_uses_closed_history_when_current_candle_exists(_inmemory_db):
    from datetime import timedelta
    from aletheia.storage import repositories as repo

    first_at, _, _ = _seed_requalification_bars()
    candidate = _legacy_structure_candidate((first_at + timedelta(minutes=2)).isoformat())
    proof = repo.prepare_candidate_requalification(candidate.model_dump())
    assert proof is not None
    assert proof["last_closed_at_ms"] == int(first_at.timestamp() * 1000)


@pytest.mark.parametrize("guard", ["superseded_snapshot", "future_cutoff", "corrupt_proof", "missing_proof", "schema_changed", "candle_corrected"])
def test_b1_rejects_unproven_requalification(_inmemory_db, guard):
    from datetime import timedelta
    from aletheia.storage import repositories as repo
    first_at, _, later_at = _seed_requalification_bars()
    candidate = _legacy_structure_candidate(first_at.isoformat())
    first, _ = repo.ensure_candidate_planning_generation(candidate.id, now=first_at)
    repo.execute_write("UPDATE candidate_planning_generations SET status='unavailable',attention_status='terminal' WHERE id=?", (first["id"],))
    later = _legacy_structure_candidate(later_at.isoformat())
    proof = repo.prepare_candidate_requalification(later.model_dump())
    if guard == "superseded_snapshot":
        repo.execute_write("UPDATE universe_snapshots SET status='superseded' WHERE id=?", (later.snapshot_id,))
    elif guard == "corrupt_proof":
        proof.pop("material")
    elif guard == "missing_proof":
        proof = None
    elif guard == "schema_changed":
        repo.execute_write("UPDATE candidate_planning_generations SET schema_fingerprint=? WHERE id=?", ("0"*64, first["id"]))
    elif guard == "candle_corrected":
        repo.execute_write("UPDATE market_ohlcv SET high=high+1,last_mutated_at=? WHERE ts=(SELECT MAX(ts) FROM market_ohlcv)",
            ((later_at + timedelta(seconds=1)).isoformat(),))
    with repo.transaction(immediate=True):
        same, created = repo.ensure_candidate_planning_generation(later.id, requalification=proof,
            now=later_at-timedelta(seconds=1) if guard == "future_cutoff" else later_at)
    assert not created and same["id"] == first["id"]


@pytest.mark.parametrize("execution", ["waiting", "order", "fill", "position", "active_claim", "executing", "submission_unknown", "materialization_missing", "executed_without_order"])
def test_b1_new_structure_cannot_supersede_execution(_inmemory_db, execution):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order, Fill, Position
    first_at, _, later_at = _seed_requalification_bars()
    candidate = _legacy_structure_candidate(first_at.isoformat())
    first, _ = repo.ensure_candidate_planning_generation(candidate.id, now=first_at)
    _insert_expired_plan(repo, first, candidate.id, "plan-b1", first_at.isoformat())
    if execution in {"active_claim", "executing", "submission_unknown", "materialization_missing", "executed_without_order"}:
        from datetime import timedelta
        status = "pending" if execution == "active_claim" else "executed" if execution == "executed_without_order" else execution
        repo.execute_write("INSERT INTO trade_proposals(id,entry_plan_id,symbol,market_type,direction,status) "
            "VALUES ('proposal-b1','plan-b1','BTC/USDT','perp','long',?)", (status,))
        if execution == "active_claim":
            repo.execute_write("INSERT INTO proposal_execution_claims(proposal_id,owner_token,claimed_at,lease_expires_at) "
                "VALUES ('proposal-b1','gateway',?,?)", (first_at.isoformat(), (later_at+timedelta(hours=1)).isoformat()))
    elif execution in {"waiting", "triggered", "risk_reserved"}:
        repo.execute_write("UPDATE entry_plans SET status=? WHERE id='plan-b1'", (execution,))
    else:
        order = Order(symbol="BTC/USDT", market_type="perp", direction="long", order_type="limit",
            quantity=1, price=100, execution_mode="bitget_demo", entry_plan_id="plan-b1",
            status="submission_unknown" if execution == "order" else "filled")
        repo._insert("orders", order.model_dump())
        if execution == "fill":
            repo._insert("fills", Fill(order_id=order.id, fill_price=100, fill_quantity=.5).model_dump())
        if execution == "position":
            repo._insert("positions", Position(symbol="BTC/USDT", market_type="perp", direction="long",
                quantity=1, entry_price=100, execution_mode="bitget_demo", order_id=order.id).model_dump())
    repo.execute_write("UPDATE candidate_planning_generations SET attention_status='terminal' WHERE id=?", (first["id"],))
    later = _legacy_structure_candidate(later_at.isoformat())
    same, created = repo.ensure_candidate_planning_generation(later.id, now=later_at)
    assert not created and same["id"] == first["id"]

    if execution == "active_claim":
        # An expired pending claim has not submitted; it must not strand recovery.
        repo.execute_write("UPDATE proposal_execution_claims SET lease_expires_at=? WHERE proposal_id='proposal-b1'",
                           (first_at.isoformat(),))
    elif execution in {"executing", "submission_unknown", "materialization_missing", "executed_without_order"}:
        # Fixture represents Gateway finishing reconciliation without an Order.
        repo.execute_write("UPDATE trade_proposals SET status='rejected' WHERE id='proposal-b1'")
    else:
        return
    successor, created = repo.ensure_candidate_planning_generation(later.id, now=later_at)
    assert created and successor["previous_generation_id"] == first["id"]


@pytest.mark.real_db
def test_b1_concurrent_publications_and_reopen_create_one_successor(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from aletheia.storage import repositories as repo, sqlite as store
    from tests.test_sqlite_writer import _setup_db
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage.models import UniverseAsset
    repo.upsert_universe_asset(UniverseAsset(id="asset-btc", venue="bitget", market_type="perp", symbol="BTC/USDT"))
    try:
        first_at, _, later_at = _seed_requalification_bars()
        candidate = _legacy_structure_candidate(first_at.isoformat())
        first, _ = repo.ensure_candidate_planning_generation(candidate.id, now=first_at)
        repo.execute_write("UPDATE candidate_planning_generations SET status='unavailable',attention_status='terminal' WHERE id=?", (first["id"],))
        later = [_legacy_structure_candidate(later_at.isoformat()) for _ in range(2)]
        proofs = [repo.prepare_candidate_requalification(c.model_dump()) for c in later]
        def publish(index):
            with repo.transaction(immediate=True):
                return repo.ensure_candidate_planning_generation(later[index].id,
                    requalification=proofs[index], now=later_at)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish, range(2)))
        assert sum(created for _, created in results) == 1
        assert results[0][0]["id"] == results[1][0]["id"]
        successor = results[0][0]
        store.reset_db()
        same, created = repo.ensure_candidate_planning_generation(later[0].id, now=later_at)
        assert not created and same["id"] == successor["id"]
        assert repo.get_db().execute("SELECT COUNT(*) FROM candidate_planning_generations").fetchone()[0] == 2
    finally:
        store.reset_db()


@pytest.mark.parametrize("model_invoked", [False, True])
@pytest.mark.parametrize("window", ["same", "new_bar", "new_structure"])
def test_b1_terminal_dependency_recovery_requires_real_book_and_no_model(_inmemory_db, model_invoked, window):
    from datetime import timedelta
    from aletheia.storage import repositories as repo
    from aletheia.data.models import OrderbookSnapshot
    first_at, new_bar_at, new_structure_at = _seed_requalification_bars()
    later_at = {"same": first_at + timedelta(minutes=2),
                "new_bar": new_bar_at, "new_structure": new_structure_at}[window]
    candidate = _legacy_structure_candidate(first_at.isoformat())
    first, _ = repo.ensure_candidate_planning_generation(candidate.id, now=first_at)
    repo.execute_write("UPDATE candidate_planning_generations SET status='unavailable',result_kind='unavailable', "
        "result_json=?,attention_status='terminal',attention_terminal_at=?,model_invoked=? WHERE id=?",
        ('{"reason":"ENTRY_ANALYSIS_EXECUTION_CONTEXT_UNAVAILABLE"}', (first_at+timedelta(minutes=1)).isoformat(), model_invoked, first["id"]))
    later = _legacy_structure_candidate(later_at.isoformat())
    same, created = repo.ensure_candidate_planning_generation(later.id, now=later_at)
    assert not created and same["id"] == first["id"]
    book = OrderbookSnapshot(symbol="BTC/USDT", exchange="bitget", market_type="perp",
        bid_price=99.9, ask_price=100.1, spread_bps=20, total_depth_10bps_usd=10000,
        bid_depth_10bps_usd=5000, ask_depth_10bps_usd=5000,
        observed_at=(later_at-timedelta(seconds=1)).isoformat())
    repo.insert_orderbook_snapshot(book.model_dump())
    recovered, created = repo.ensure_candidate_planning_generation(later.id, now=later_at)
    assert created is (not model_invoked and window != "same")
    if created:
        assert recovered["cycle_reason"] == "planning_dependencies_recovered"
        assert recovered["previous_generation_id"] == first["id"]


def test_legacy_retirement_preserves_submission_evidence(_inmemory_db):
    from aletheia.storage import repositories as repo
    from aletheia.storage.migrations import _apply_retired_entry_authority_v72

    db = repo.get_db()
    for identity, status in (("unsubmitted", "pending"), ("owned", "approved"),
                             ("unknown", "executing"), ("with-order", "pending")):
        db.execute("INSERT INTO trade_proposals(id,symbol,market_type,direction,status) "
                   "VALUES (?,'BTC/USDT','perp','long',?)", (identity, status))
    db.execute("INSERT INTO proposal_execution_claims(proposal_id,owner_token,claimed_at,lease_expires_at) VALUES "
               "('owned','owner','2026-09-01','2026-09-02')")
    db.execute("INSERT INTO orders(id,proposal_id,symbol,market_type,direction,order_type,quantity,execution_mode,status) "
               "VALUES ('order','with-order','BTC/USDT','perp','long','limit',1,'bitget_demo','submitted')")
    db.execute("INSERT INTO pi_decisions(id,action) VALUES ('old-pending','signal_pending')")
    db.commit()
    _apply_retired_entry_authority_v72(db)
    assert dict(db.execute("SELECT id,status FROM trade_proposals")) == {
        "unsubmitted": "rejected", "owned": "approved", "unknown": "executing", "with-order": "pending",
    }
    assert db.execute("SELECT action FROM pi_decisions WHERE id='old-pending'").fetchone()[0] == "entry_path_retired"
    assert db.execute("SELECT status FROM orders WHERE id='order'").fetchone()[0] == "submitted"
    assert db.execute("SELECT owner_token FROM proposal_execution_claims WHERE proposal_id='owned'").fetchone()[0] == "owner"
    events = list(db.execute("SELECT entity_id,before_state,after_state FROM audit_events WHERE event_type='entry_path_retired'"))
    assert {row[0] for row in events} == {"unsubmitted", "old-pending"}
    _apply_retired_entry_authority_v72(db)
    assert list(db.execute("SELECT entity_id,before_state,after_state FROM audit_events WHERE event_type='entry_path_retired'")) == events


@pytest.mark.parametrize("terminal", [False, True])
def test_unavailable_lifecycle_only_promises_retry_while_cycle_is_active(terminal):
    from aletheia.trading.entry_lifecycle import project_candidate_entry_lifecycle

    cycle = {"id": "generation", "is_current": 1, "cycle_number": 1,
             "outcome_class": "planning_unavailable", "outcome_reason": "PROVIDER_TIMEOUT",
             "attention_status": "terminal" if terminal else "active"}
    lifecycle = project_candidate_entry_lifecycle({}, [cycle])
    assert lifecycle["lifecycle_state"] == "PLANNING_UNAVAILABLE"
    assert lifecycle["next_action"] == (
        "await_material_input_or_owner_replan" if terminal else "bounded_retry")


def test_expired_activation_reports_actual_expiry_as_state_transition_time():
    from aletheia.trading.entry_lifecycle import project_candidate_entry_lifecycle

    cycle = {"id": "generation", "is_current": 1, "cycle_number": 1,
             "entry_plan_status": "triggered", "activation_recoverable": True,
             "activation_expires_at": "2026-01-01T00:00:02+00:00",
             "entry_plan_updated_at": "2026-01-01T00:00:00+00:00"}
    lifecycle = project_candidate_entry_lifecycle({}, [cycle])
    assert lifecycle["lifecycle_reason"] == "entry_trigger_activation_expired"
    assert lifecycle["state_changed_at"] == cycle["activation_expires_at"]


def test_monitoring_generation_change_uses_its_own_time_without_inventing_history():
    from aletheia.trading.entry_lifecycle import project_candidate_entry_lifecycle

    cycle = {"id": "generation", "is_current": 1, "cycle_number": 1,
             "entry_plan_status": "triggered", "activation_recoverable": True,
             "activation_expires_at": "2099-01-01T00:00:02+00:00", "activation_stream_current": False,
             "entry_plan_updated_at": "2026-01-01T00:00:00+00:00"}
    assert project_candidate_entry_lifecycle({}, [cycle])["state_changed_at"] is None
    cycle["monitoring_changed_at"] = "2026-01-01T00:00:01+00:00"
    assert project_candidate_entry_lifecycle({}, [cycle])["state_changed_at"] == cycle["monitoring_changed_at"]
