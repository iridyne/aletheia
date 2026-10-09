from tests.helpers.repo_test_helpers import list_active_candidate_attention

import asyncio
import builtins
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from aletheia.agent_shell.entry_planner import (
    ENTRY_PLANNER_MAX_CONCURRENCY,
    ENTRY_PLANNER_MIN_CONCURRENCY,
    PlannerBackpressure,
    plan_next_candidate,
    run_entry_planner_batch,
)
from aletheia.storage import repositories as repo
from aletheia.storage.models import UniverseAsset, UniverseMember, UniverseSnapshot
from aletheia.trading.entry_plan import EntryPlan
from aletheia.model_provider.base import portable_json_schema_payload

from aletheia.trading.entry_analysis import EntryHypothesis
from aletheia.utils.json_utils import canonical_json_strict
from aletheia.trading.risk_intent import RiskIntent


@pytest.fixture(autouse=True)
def _bitget_market_data_authority(_inmemory_db):
    """Exercise Planner stages under the supported Bitget Demo / Full route."""
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.market_data.ccxt_exchange = "bitget"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    cfg.agent.permission_mode = "Full"
    save_config(cfg)


def _candidate(
    *,
    regime: str = "trending",
    rank: int = 1,
    price: float = 100.0,
    asset_id: str = "asset-btc",
    symbol: str = "BTC/USDT",
    direction: str = "long",
    scanner_family: str = "momentum",
    venue: str = "bitget",
    market_type: str = "perp",
    source_at: str = "2026-09-03T00:00:00+00:00",
    primary_ref: str = "fact-1",
):
    return _typed_candidate(
        mechanism={
            "momentum": "continuation",
            "short_bias": "continuation",
            "mean_reversion": "reversal",
            "derivatives": "positioning_forced_flow",
        }.get(scanner_family, "information_catalyst"),
        family=scanner_family,
        direction=direction,
        rank=rank,
        price=price,
        regime=regime,
        asset_id=asset_id,
        symbol=symbol,
        venue=venue,
        market_type=market_type,
        source_at=source_at,
        primary_ref=primary_ref,
    )


def _typed_candidate(
    *,
    mechanism: str,
    family: str,
    direction: str = "long",
    policy: str = "candidate_selection_utility",
    policy_epoch: str | None = "epoch-1",
    mechanism_version: str | None = None,
    rank: int = 1,
    price: float = 100.0,
    regime: str = "trending",
    risk_flags: str = "[]",
    asset_id: str = "asset-btc",
    symbol: str = "BTC/USDT",
    venue: str = "bitget",
    market_type: str = "perp",
    source_at: str = "2026-09-03T00:00:00+00:00",
    primary_ref: str = "fact-1",
    capability: str = "supported",
    estimated_cost: float | None = None,
):
    from uuid import uuid4

    from aletheia.universe.candidate_opportunity import CandidateOpportunityContext

    candidate_id = uuid4().hex[:12]
    context = CandidateOpportunityContext(
        candidate_id=candidate_id,
        candidate_selection={
            "policy": policy,
            "policy_epoch": policy_epoch,
            "source_record_hash": "a" * 64,
        },
        source={
            "kind": "universe", "id": candidate_id, "trade_school": "short_term",
            "detected_at": source_at,
            "source_cutoff_at": source_at,
            "feature_snapshot_id": "feature-1",
        },
        opportunity={
            "primary_mechanism": mechanism,
            "mechanism_version": mechanism_version or f"{mechanism}/v1",
            "mechanism_source": "target_authority",
            "source_component_id": family,
            "direction": direction,
            "horizon": "4h",
            "economic_utility_pct": 1.0 if policy == "candidate_selection_utility" else None,
            "capability_status": capability,
            "capability_gaps": (),
            "primary_evidence_refs": (primary_ref,),
            "secondary_mechanisms": (),
        },
        context={"regime": regime},
    )
    snapshot = UniverseSnapshot(created_at=source_at, expires_at="2099-01-01T00:00:00+00:00")
    repo._insert("universe_snapshots", snapshot.model_dump())
    candidate = UniverseMember(
        id=candidate_id,
        snapshot_id=snapshot.id,
        asset_id=asset_id,
        venue=venue,
        symbol=symbol,
        market_type=market_type,
        rank=rank,
        total_score=90 - rank,
        scanner_family=family,
        scanner_direction=direction,
        regime_tag=regime,
        risk_flags=risk_flags,
        entry_price_usd=price,
        estimated_cost=estimated_cost,
        expires_at="2099-01-01T00:00:00+00:00",
        candidate_selection_policy=policy,
        candidate_selection_policy_epoch=policy_epoch,
        candidate_selection_utility_pct=(1.0 if policy == "candidate_selection_utility" else None),
        opportunity_mechanism=mechanism,
        opportunity_mechanism_version=mechanism_version or f"{mechanism}/v1",
        opportunity_source_component=family,
        opportunity_source_cutoff_at=source_at,
        opportunity_evidence_hash="b" * 64,
        candidate_opportunity_context_version=context.version,
        candidate_opportunity_context_json=context.model_dump_json(),
        candidate_opportunity_context_hash=context.context_hash,
    )
    repo._insert("universe_members", candidate.model_dump())
    return candidate


@pytest.mark.parametrize("mechanism,family,capability", [
    ("positioning_forced_flow", "derivatives", "observe_only"),
    ("information_catalyst", "catalyst", "unavailable"),
])
def test_candidate_capability_blocks_before_market_or_model_calls(monkeypatch, mechanism, family, capability):
    candidate = _typed_candidate(mechanism=mechanism, family=family, capability=capability)
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)

    def unexpected(*args, **kwargs):
        pytest.fail("capability-blocked Candidate must not acquire market data or call model")

    monkeypatch.setattr(repo, "latest_orderbook", unexpected)
    result = asyncio.run(plan_next_candidate(unexpected, owner_token="capability-owner"))
    assert result["status"] == "blocked"
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert stored["outcome_class"] == "planning_blocked"
    assert stored["outcome_reason"] == f"CANDIDATE_CAPABILITY:{capability}"
    assert not stored["model_invoked"]


def _no_plan_hypothesis(reason_code: str = "INSUFFICIENT_EVIDENCE") -> EntryHypothesis:
    return EntryHypothesis(decision="NO_PLAN", reason_code=reason_code,
                           mechanism="continuation", direction="long", risk_tier="NONE", horizon="NONE")


def _plan(generation: dict, *, profiles=None) -> EntryPlan:
    from aletheia.trading.entry_learning import empty_entry_learning_context
    from aletheia.trading.entry_analysis import (
        EntryAnalysisContext, EntryAnchor, EntryHypothesis, fingerprint,
    )
    from aletheia.utils.json_utils import canonical_json_strict

    now = datetime.now(timezone.utc)
    primary = EntryAnchor(
        venue="bitget", symbol="BTC/USDT", market_type="perp", kind="causal_breakout",
        price=100.0, directional_role="entry_candidate", known_at_ms=int(now.timestamp() * 1000),
        source_fact_refs=(f"candidate:{generation['last_candidate_id']}",), source_fingerprint="a" * 64,
    )
    invalidation = EntryAnchor(
        venue="bitget", symbol="BTC/USDT", market_type="perp", kind="confirmed_swing",
        price=98.0, directional_role="invalidation_candidate", known_at_ms=int(now.timestamp() * 1000),
        source_fact_refs=("swing:test",), source_fingerprint="b" * 64,
    )
    mechanism = generation.get("opportunity_mechanism") or "continuation"
    direction = generation.get("direction") or "long"
    hypothesis = EntryHypothesis(
        decision="ENTRY_PLAN", reason_code="OK", mechanism=mechanism, direction=direction,
        primary_anchor_ref=primary.anchor_id, invalidation_anchor_ref=invalidation.anchor_id,
        entry_profile_id="grounded-anchor-retrace-reclaim", capture_priority="FILL_FIRST", capture_reason="fixture current evidence", capture_evidence_refs=(primary.anchor_id,),
        risk_tier="LOW", horizon="INTRADAY", supporting_fact_refs=(primary.anchor_id,),
        risk_intent=RiskIntent(posture="BASELINE", reason="fixture baseline posture", evidence_refs=(primary.anchor_id,)),
    )
    hypothesis_json = hypothesis.model_dump_json()
    trigger_params = {
        "breakout_level": 100.0,
            "sweep_low": 98.5, "sweep_high": 99.0, "atr_abs": 2.0,
            "geometry_version": "anchor-retrace-reclaim-geometry/v1",
            "geometry_fingerprint": "f" * 64, "pullback_low": 99.0, "pullback_high": 100.0,
        "entry_low": 100.0, "entry_high": 101.0, "invalidation_price": 98.0,
    }
    opportunity = json.loads(
        repo.get_candidate(generation["last_candidate_id"])["candidate_opportunity_context_json"]
    )
    context = EntryAnalysisContext(
        analysis_cutoff_at=now.isoformat(),
        instrument={"venue": "bitget", "symbol": "BTC/USDT", "market_type": "perp", "direction": direction},
        planning_generation={
            "id": generation["id"], "candidate_id": generation["last_candidate_id"],
            "candidate_family": generation["candidate_family"], "cycle_number": generation.get("cycle_number") or 1,
            "entry_behavior_fingerprint": generation.get("entry_behavior_fingerprint") or "b" * 64,
            "schema_fingerprint": generation.get("schema_fingerprint") or "s" * 64,
        },
        candidate={"candidate_price": 100.0, "fact_refs": [primary.source_fact_refs[0]]},
        candle_windows={key: {"availability": "unavailable", "bars": []} for key in ("4h", "1h", "15m")},
        market_structure={"availability": "unavailable", "fact_refs": []},
        quant_context={"availability": "unavailable", "fact_refs": []},
        portfolio_context={"availability": "unavailable", "fact_refs": []},
        execution_context={"availability": "unavailable", "fact_refs": []},
        anchors=(primary, invalidation),
        entry_profiles=({"id": "grounded-anchor-retrace-reclaim"},) if profiles is None else profiles,
        learning_context=empty_entry_learning_context(
            as_of_at=now.isoformat(), mechanism=mechanism, direction=direction,
            market_type="perp", policy=generation["candidate_selection_policy"],
            policy_epoch=generation["candidate_selection_policy_epoch"],
        ),
        unavailable_sections=("candles", "market_structure", "quant", "portfolio", "execution"),
        candidate_opportunity_context=opportunity,
        inference_identity={
            "caller": "agent_shell.entry_planner", "prompt_fingerprint": "f" * 64, "model_provider": "test",
        },
    )
    context_json = canonical_json_strict(context.model_dump(mode="json"))
    repo.execute_write(
        "UPDATE candidate_planning_generations SET planner_input_json=?, planner_input_hash=? WHERE id=?",
        (context_json, fingerprint(context.model_dump(mode="json")), generation["id"]),
    )
    return EntryPlan(
        planning_generation_id=generation["id"],
        candidate_id=generation["last_candidate_id"],
        candidate_family=generation["candidate_family"],
        source_kind="universe", source_id=generation["last_candidate_id"], trade_school="short_term",
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        direction="long",
        thesis="bounded momentum continuation",
        scenario="retrace then reclaim",
        execution_mode="TRIGGERED",
        trigger_spec_id="anchor-retrace-reclaim",
        trigger_params=trigger_params,
        trigger_data_requirements=("quote", "trade", "books5", "candle1m"),
        trigger_freshness_ms=2_000,
        entry_zone=(100, 101),
        invalidation_conditions=("close_below_98",),
        invalidation_price=98,
        stop_price=98,
        target_price=105,
        expected_horizon_hours=4,
        approved_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        order_tactic="OPPOSING_BBO_IOC",
        maker_required=False,
        time_in_force="IOC",
        price_rule="OPPOSING_BBO",
        bbo_reference="ask",
        max_wait_sec=0,
        cancel_replace_policy="CANCEL_ONLY",
        worst_price=101,
        max_requotes=0,
        partial_fill_policy="KEEP_PARTIAL_CANCEL_REMAINDER",
        max_slippage_bps=10,
        max_notional=100,
        max_risk_pct=1,
        max_leverage=2,
        required_permission="Paper",
        execution_account_mode="bitget_demo",
        risk_policy_id="demo-v1",
        risk_policy_revision=1,
        risk_policy_hash="risk-hash",
        producer_commit=generation.get("producer_commit") or "test",
        behavior_fingerprint=generation.get("entry_behavior_fingerprint") or generation.get("behavior_fingerprint") or "b" * 64,
        schema_fingerprint=generation.get("schema_fingerprint") or "s" * 64,
        planning_identity=generation.get("planning_identity") or "p" * 64,
        cycle_number=generation.get("cycle_number") or 1,
        analysis_context_fingerprint=context.context_fingerprint,
        entry_hypothesis_json=hypothesis_json,
        entry_hypothesis_hash=hashlib.sha256(hypothesis_json.encode()).hexdigest(),
        primary_anchor_snapshot=primary.model_dump(mode="json"),
        invalidation_anchor_snapshot=invalidation.model_dump(mode="json"),
        entry_profile_id=hypothesis.entry_profile_id,
        compiled_geometry_fingerprint=fingerprint({
            "entry_zone": (100.0, 101.0), "invalidation_price": 98.0,
            "stop_price": 98.0, "target_price": 105.0, "trigger_params": trigger_params,
        }),
    )


def test_unchanged_candidate_and_duplicate_wakes_create_one_durable_owner():
    first = _candidate()
    generation, created = repo.ensure_candidate_planning_generation(first.id)
    assert created is True

    churn = _candidate(rank=7, price=103.0)
    same, created = repo.ensure_candidate_planning_generation(churn.id)
    assert created is False
    assert same["id"] == generation["id"]

    claimed = repo.claim_next_candidate_planning("owner-one")
    assert claimed and claimed["id"] == generation["id"]
    assert repo.claim_next_candidate_planning("owner-two") is None


def test_bound_planner_input_prevents_candidate_retargeting():
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    assert repo.claim_next_candidate_planning("owner")["id"] == generation["id"]
    payload = json.dumps({"planning_generation": {"candidate_id": candidate.id}}, sort_keys=True)
    import hashlib

    assert repo.bind_candidate_planner_input(
        generation["id"], "owner", payload, hashlib.sha256(payload.encode()).hexdigest()
    )
    churn = _candidate(rank=8, price=105.0)
    same, created = repo.ensure_candidate_planning_generation(churn.id)

    assert created is False
    assert same["last_candidate_id"] == candidate.id
    assert repo.get_candidate_planning_generation(generation["id"])["last_candidate_id"] == candidate.id
    with pytest.raises(sqlite3.IntegrityError, match="Bound planner Candidate is immutable"):
        repo.get_db().execute(
            "UPDATE candidate_planning_generations SET last_candidate_id=? WHERE id=?",
            (churn.id, generation["id"]),
        )


def test_current_planning_requires_typed_candidate_and_uses_machine_identity():
    old_snapshot = UniverseSnapshot(
        created_at="2026-09-02T00:59:59+00:00", expires_at="2099-01-01T00:00:00+00:00"
    )
    repo._insert("universe_snapshots", old_snapshot.model_dump())
    old = UniverseMember(
        snapshot_id=old_snapshot.id, asset_id="asset-old", venue="bitget",
        symbol="OLD/USDT", market_type="perp", rank=1, total_score=90,
        scanner_family="momentum", scanner_direction="long", entry_price_usd=100,
        expires_at="2099-01-01T00:00:00+00:00",
    )
    repo._insert("universe_members", old.model_dump())
    with pytest.raises(ValueError, match="candidate_opportunity_context_missing"):
        repo.ensure_candidate_planning_generation(old.id)

    momentum = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(momentum.id)
    non_momentum = _candidate(
        asset_id="asset-breakout", symbol="BREAKOUT/USDT", scanner_family="breakout"
    )
    other, _ = repo.ensure_candidate_planning_generation(non_momentum.id)
    assert len(generation["entry_behavior_fingerprint"]) == 64
    assert len(generation["schema_fingerprint"]) == 64
    assert other["entry_behavior_fingerprint"] == generation["entry_behavior_fingerprint"]


@pytest.mark.parametrize(
    ("mechanism", "family"),
    [("reversal", "mean_reversion"), ("positioning_forced_flow", "derivatives")],
)
def test_typed_target_candidate_uses_mechanism_aware_grounded_planner(mechanism, family):
    candidate = _typed_candidate(mechanism=mechanism, family=family)

    generation, created = repo.ensure_candidate_planning_generation(candidate.id)

    assert created is True
    assert len(generation["entry_behavior_fingerprint"]) == 64
    assert generation["opportunity_mechanism"] == mechanism
    assert generation["candidate_selection_policy"] == "candidate_selection_utility"
    assert generation["candidate_selection_policy_epoch"] == "epoch-1"





@pytest.mark.parametrize("terminal_status", ["unavailable", "no_plan", "planned"])
def test_behavior_fingerprint_upgrade_supersedes_each_durable_result_once(terminal_status):
    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    original_result = json.dumps({"reason": "legacy-result"})
    plan = None
    if terminal_status == "planned":
        assert repo.claim_next_candidate_planning("legacy-owner")
        plan = _plan(generation)
        assert repo.complete_candidate_planning_with_plan(
            generation["id"], "legacy-owner", plan
        )[0] == "planned"
    repo.execute_write(
        "UPDATE candidate_planning_generations SET entry_behavior_fingerprint=?, "
        "status=?, result_json=?, attention_status='terminal' WHERE id=?",
        ("0" * 64, terminal_status, original_result, generation["id"]),
    )

    refreshed = _typed_candidate(
        mechanism="reversal", family="mean_reversion", rank=9, price=104
    )
    current, created = repo.ensure_candidate_planning_generation(refreshed.id)
    same, created_again = repo.ensure_candidate_planning_generation(refreshed.id)

    old = repo.get_candidate_planning_generation(generation["id"])
    assert created is True and created_again is False
    assert same["id"] == current["id"]
    assert current["entry_behavior_fingerprint"] != "0" * 64
    assert old["is_current"] == 0
    assert old["attention_terminal_reason"] == "canonical_compatibility_cutover"
    assert old["result_json"] == original_result
    repo.upsert_universe_asset(
        UniverseAsset(
            id=refreshed.asset_id,
            venue=refreshed.venue,
            market_type=refreshed.market_type,
            symbol=refreshed.symbol,
        )
    )
    [current_projection] = repo.list_current_candidate_workbench(refreshed.snapshot_id)
    history = {row["id"]: row for row in repo.list_planning_history()}
    assert "planner_contract_version" not in current_projection
    assert history[generation["id"]]["planner_contract_version"] is None
    assert history[generation["id"]]["attention_terminal_reason"] == (
        "canonical_compatibility_cutover"
    )
    if terminal_status == "planned":
        assert plan is not None
        stored_plan = repo.get_entry_plan(plan.plan_id)
        assert stored_plan["status"] == "invalidated"
        assert stored_plan["terminal_reason"] == "canonical_compatibility_cutover"


def test_a_generation_frozen_under_another_entry_contract_is_not_claimable():
    """A stored input in another contract's shape must not reach the Planner.

    It used to be claimed (the predicate only required a non-null fingerprint),
    failed re-validation, and burned its three attempts as PLANNER_INTERNAL.
    The compatibility cutover replaces it at the next publication instead.
    """
    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    assert repo.count_pending_candidate_planning() == 1
    assert repo.list_candidate_planning_demand()

    for column in ("entry_behavior_fingerprint", "schema_fingerprint"):
        stored = repo.get_candidate_planning_generation(generation["id"])
        assert stored is not None
        original = stored[column]
        repo.execute_write(
            f"UPDATE candidate_planning_generations SET {column}=? WHERE id=?",
            ("0" * 64, generation["id"]),
        )
        assert repo.count_pending_candidate_planning() == 0
        assert repo.list_candidate_planning_demand() == []
        assert repo.claim_next_candidate_planning("stale-owner") is None
        repo.execute_write(
            f"UPDATE candidate_planning_generations SET {column}=? WHERE id=?",
            (original, generation["id"]),
        )

    assert repo.count_pending_candidate_planning() == 1
    claimed = repo.claim_next_candidate_planning("current-owner")
    assert claimed is not None and claimed["id"] == generation["id"]


def test_legacy_false_no_plan_remains_immutable_during_canonical_cutover():
    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    completed_at = datetime.now(timezone.utc).isoformat()
    legacy_result = json.dumps({"reason_code": "AI_ENTRY_RUNTIME_UNSUPPORTED"})
    repo.execute_write(
        "UPDATE candidate_planning_generations SET entry_behavior_fingerprint=NULL, "
        "status='no_plan', result_kind='no_plan', result_json=?, attention_status='terminal', "
        "attention_terminal_at=?, attention_terminal_reason='no_plan', completed_at=?, updated_at=? "
        "WHERE id=?",
        (legacy_result, completed_at, completed_at, completed_at, generation["id"]),
    )
    before = repo.get_candidate_planning_generation(generation["id"])

    refreshed = _typed_candidate(
        mechanism="reversal", family="mean_reversion", rank=9, price=104
    )
    current, created = repo.ensure_candidate_planning_generation(refreshed.id)

    assert created is True
    assert current["previous_generation_id"] == generation["id"]
    assert current["cycle_reason"] == "canonical_compatibility_cutover"
    assert repo.get_candidate_planning_generation(generation["id"]) == before


def test_v68_v69_restore_false_no_plan_rows_mutated_by_v67_cutover():
    from aletheia.storage.migrations import (
        _apply_immutable_legacy_entry_cutover_v68,
        _apply_legacy_entry_updated_at_v69,
    )

    candidate = _typed_candidate(
        mechanism="reversal",
        family="mean_reversion",
        source_at="2026-09-03T00:00:00+00:00",
    )
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    completed_at = "2026-09-03T00:01:00+00:00"
    refreshed_at = "2026-09-04T00:00:00+00:00"
    refreshed = _typed_candidate(
        mechanism="reversal",
        family="mean_reversion",
        rank=9,
        price=104,
        source_at=refreshed_at,
    )
    repo.execute_write(
        "UPDATE candidate_planning_generations SET entry_behavior_fingerprint=NULL, "
        "status='no_plan', result_kind='no_plan', result_json=?, attention_status='terminal', "
        "attention_terminal_at=?, attention_terminal_reason='no_plan', completed_at=?, "
        "last_candidate_id=?, updated_at=? "
        "WHERE id=?",
        (
            json.dumps({"reason_code": "AI_ENTRY_RUNTIME_UNSUPPORTED"}),
            completed_at,
            completed_at,
            refreshed.id,
            refreshed_at,
            generation["id"],
        ),
    )
    before = repo.get_candidate_planning_generation(generation["id"])
    current, _ = repo.ensure_candidate_planning_generation(refreshed.id)
    repo.execute_write(
        "UPDATE candidate_planning_generations SET is_current=0, status='superseded', "
        "updated_at=?, attention_terminal_at=?, "
        "attention_terminal_reason='canonical_compatibility_cutover' WHERE id=?",
        (current["created_at"], current["created_at"], generation["id"]),
    )

    _apply_immutable_legacy_entry_cutover_v68(repo.get_db())
    _apply_legacy_entry_updated_at_v69(repo.get_db())

    assert repo.get_candidate_planning_generation(generation["id"]) == before


def test_candidate_planning_claim_prefers_current_rank_over_fifo_age():
    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    rank_twenty = _candidate(
        rank=20,
        asset_id="asset-eth",
        symbol="ETH/USDT",
    )
    older_generation, _ = repo.ensure_candidate_planning_generation(
        rank_twenty.id,
        now=start,
    )
    rank_one = _candidate(rank=1)
    best_generation, _ = repo.ensure_candidate_planning_generation(
        rank_one.id,
        now=start + timedelta(seconds=1),
    )

    claimed = repo.claim_next_candidate_planning(
        "priority-owner",
        now=start + timedelta(seconds=2),
    )

    assert claimed["id"] == best_generation["id"]
    assert claimed["id"] != older_generation["id"]


def test_candidate_planning_claim_has_stable_fifo_tie_breaker():
    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    older = _candidate(asset_id="asset-eth", symbol="ETH/USDT")
    older_generation, _ = repo.ensure_candidate_planning_generation(older.id, now=start)
    newer = _candidate()
    repo.ensure_candidate_planning_generation(newer.id, now=start + timedelta(seconds=1))
    claimed = repo.claim_next_candidate_planning(
        "fallback-owner",
        now=start + timedelta(seconds=2),
    )

    assert claimed["id"] == older_generation["id"]


def test_candidate_planning_claim_fails_closed_without_candidate_evidence():
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    db = repo.get_db()
    db.execute("DELETE FROM universe_members WHERE id=?", (candidate.id,))
    db.commit()

    assert repo.count_pending_candidate_planning() == 0
    assert repo.claim_next_candidate_planning("orphan-owner") is None
    assert repo.get_candidate_planning_generation(generation["id"])["attempt_count"] == 0


def test_candidate_attention_requalification_preserves_hard_deadline():
    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    candidate = _candidate()
    generation, created = repo.ensure_candidate_planning_generation(candidate.id, now=start)
    assert created is True
    assert generation["attention_started_at"] == start.isoformat()
    assert generation["last_requalified_at"] is None
    assert generation["soft_expires_at"] == (start + timedelta(seconds=900)).isoformat()
    assert generation["hard_expires_at"] == (start + timedelta(seconds=3600)).isoformat()

    reselected = _candidate(rank=2, price=101)
    same, created = repo.ensure_candidate_planning_generation(
        reselected.id,
        now=start + timedelta(seconds=600),
    )
    assert created is False
    source = {"scanner_run_id": "run-complete", "snapshot_id": reselected.snapshot_id}
    counts = repo.reconcile_candidate_attention(
        {same["id"]}, producer_scope="universe",
        eligible_identity_keys={("bitget", "BTC/USDT", "perp", "momentum", "long")},
        refresh_source=source,
        soft_lease_sec=900,
        now=start + timedelta(seconds=600),
    )

    stored = repo.get_candidate_planning_generation(generation["id"])
    assert counts == {"requalified": 1, "quota_evicted": 0, "admission_lost": 0}
    assert stored["last_requalified_at"] == (start + timedelta(seconds=600)).isoformat()
    assert stored["soft_expires_at"] == (start + timedelta(seconds=1500)).isoformat()
    assert stored["hard_expires_at"] == generation["hard_expires_at"]
    assert repo.get_candidate_planning_for_candidate(candidate.id)["id"] == generation["id"]
    assert repo.get_candidate_planning_for_candidate(reselected.id)["id"] == generation["id"]
    persisted_source = json.loads(stored["requalification_source_json"])
    assert persisted_source["candidate_opportunity_context"] == json.loads(candidate.candidate_opportunity_context_json)
    assert persisted_source["scanner_run_id"] == "run-complete"


def test_timer_only_universe_ttl_slide_does_not_requalify_attention():
    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id, now=start)
    before = repo.get_candidate_planning_generation(generation["id"])

    repo.refresh_active_snapshot_ttls(candidate.snapshot_id, start + timedelta(seconds=300))

    after = repo.get_candidate_planning_generation(generation["id"])
    assert after["last_requalified_at"] is None
    assert after["requalification_source_json"] == before["requalification_source_json"]
    assert json.loads(after["requalification_source_json"])["candidate_opportunity_context"]["source"]["id"] == candidate.id
    assert after["soft_expires_at"] == before["soft_expires_at"]
    assert after["hard_expires_at"] == before["hard_expires_at"]


def test_attention_expiry_does_not_resurrect_unchanged_identity():
    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id, now=start)

    assert repo.claim_next_candidate_planning("late", now=start + timedelta(seconds=901)) is None
    expired = repo.get_candidate_planning_generation(generation["id"])
    assert expired["attention_status"] == "terminal"
    assert expired["attention_terminal_reason"] == "ttl_expired"
    assert expired["is_current"] == 1

    churn = _candidate(rank=3, price=102)
    same, created = repo.ensure_candidate_planning_generation(churn.id, now=start + timedelta(seconds=902))
    assert created is False
    assert same["id"] == generation["id"]
    assert repo.get_candidate_planning_generation(generation["id"])["attention_status"] == "terminal"


def test_completed_reselection_after_soft_deadline_cannot_revive_attention():
    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id, now=start)
    counts = repo.reconcile_candidate_attention(
        {generation["id"]}, producer_scope="universe",
        eligible_identity_keys={("bitget", "BTC/USDT", "perp", "momentum", "long")},
        refresh_source={"scanner_run_id": "completed-too-late"},
        soft_lease_sec=900,
        now=start + timedelta(seconds=901),
    )
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert counts["requalified"] == 0
    assert stored["attention_status"] == "terminal"
    assert stored["attention_terminal_reason"] == "ttl_expired"
    assert stored["last_requalified_at"] is None


def test_hard_attention_deadline_is_distinct_from_soft_expiry():
    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(
        candidate.id,
        soft_lease_sec=3600,
        max_attention_age_sec=3600,
        now=start,
    )
    repo.expire_candidate_attention(now=start + timedelta(seconds=3600))
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert stored["attention_terminal_reason"] == "max_attention_age"


def test_attention_expiry_rejects_late_plan_without_invalidating_existing_plan():
    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id, now=start)
    assert repo.claim_next_candidate_planning("slow-owner", now=start)

    plan = _plan(generation)
    status, record = repo.complete_candidate_planning_with_plan(
        generation["id"],
        "slow-owner",
        plan,
        now=start + timedelta(seconds=901),
    )
    assert (status, record) == ("stale", None)
    assert repo.get_entry_plan(plan.plan_id) is None


def test_attention_is_rechecked_before_model_invocation(monkeypatch):
    candidate = _candidate()
    repo.ensure_candidate_planning_generation(candidate.id)
    calls = 0

    async def planner(_context):
        nonlocal calls
        calls += 1
        return _no_plan_hypothesis("STALE_FACTS")

    monkeypatch.setattr(repo, "candidate_attention_is_active", lambda _generation_id: False)
    result = asyncio.run(plan_next_candidate(planner))
    assert result["status"] == "stale"
    assert result["model_invoked"] is False
    assert calls == 0


def test_completed_refresh_terminal_reasons_require_exact_eligibility_evidence():
    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    selected = _candidate()
    selected_generation, _ = repo.ensure_candidate_planning_generation(selected.id, now=start)
    evicted = _candidate(asset_id="asset-eth", symbol="ETH/USDT", direction="short")
    evicted_generation, _ = repo.ensure_candidate_planning_generation(evicted.id, now=start)

    assert repo.reconcile_candidate_attention(
        {selected_generation["id"]}, producer_scope="universe",
        eligible_identity_keys=set(),
        refresh_source={"scanner_run_id": "failed"},
        soft_lease_sec=900,
        refresh_complete=False,
        now=start + timedelta(seconds=10),
    ) == {"requalified": 0, "quota_evicted": 0, "admission_lost": 0}
    assert repo.get_candidate_planning_generation(evicted_generation["id"])["attention_status"] == "active"

    counts = repo.reconcile_candidate_attention(
        {selected_generation["id"]}, producer_scope="universe",
        eligible_identity_keys={("bitget", "ETH/USDT", "perp", "momentum", "short")},
        refresh_source={"scanner_run_id": "complete"},
        soft_lease_sec=900,
        now=start + timedelta(seconds=20),
    )
    assert counts["quota_evicted"] == 1
    assert (
        repo.get_candidate_planning_generation(evicted_generation["id"])["attention_terminal_reason"] == "quota_evicted"
    )











def test_semantic_exhaustion_terminalizes_without_compiler(monkeypatch):
    from aletheia.agent_shell.entry_planner import PlanIntentError

    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    _plan(generation)

    async def planner(_context):
        raise PlanIntentError("PLANNER_SEMANTIC_INVALID:hypothesis_fact_ref_not_supplied")

    result = asyncio.run(plan_next_candidate(planner))
    stored = repo.get_candidate_planning_generation(generation["id"])

    assert result["status"] == "failed", (result, stored["result_json"])
    assert stored["compiler_invoked"] == 0
    assert json.loads(stored["result_json"])["reason"].startswith(
        "PLANNER_SEMANTIC_INVALID:hypothesis_fact_ref_not_supplied"
    )











@pytest.mark.parametrize("profile", ["grounded-anchor-direct", "grounded-anchor-retrace-reclaim"])
@pytest.mark.parametrize("compiler_error", [None, "COMPILER_RISK_BUDGET_UNAVAILABLE", "COMPILER_RETRY_EXHAUSTED", "FROZEN_CONTEXT_IDENTITY_MISMATCH", "RUNNING_CONTRACT_CHANGED", "HYPOTHESIS_CHECKPOINT_MISMATCH", "stop_distance_below_vol_target:0.0125<0.0150"])
def test_full_demo_canonical_planner_persists_compiled_plan(monkeypatch, profile, compiler_error):
    from types import SimpleNamespace

    from aletheia.config.loader import load_config
    from aletheia.trading.entry_learning import empty_entry_learning_context
    from aletheia.trading.entry_analysis import (
        EntryAnalysisContext, EntryAnchor, EntryHypothesis,
        fingerprint,
    )
    from aletheia.utils.json_utils import canonical_json_strict
    from aletheia.trading.entry_contract import entry_prompt_fingerprint

    cfg = load_config()
    cfg.agent.permission_mode = "Full"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    generation["producer_commit"] = "test-commit"
    repo.execute_write(
        "UPDATE candidate_planning_generations SET producer_commit=? WHERE id=?",
        (generation["producer_commit"], generation["id"]),
    )
    policy = SimpleNamespace(
        policy_id="test", revision=1,
        capital=SimpleNamespace(max_position_margin_usdt=100_000, max_total_exposure_pct=100),
        loss=SimpleNamespace(max_single_trade_risk_pct=1.0, reduced_risk_multiplier=0.5),
        margin=SimpleNamespace(min_entry_liquidation_buffer_pct=5.0),
    )
    monkeypatch.setattr(
        "aletheia.trading.entry_plan_compiler.resolve_risk_policy",
        lambda *_a, **_k: SimpleNamespace(policy=policy, policy_hash="hash"),
    )
    monkeypatch.setattr("aletheia.trading.entry_plan_compiler.get_max_leverage", lambda *_a, **_k: 20)
    monkeypatch.setattr("aletheia.risk.engine.account_available_for_execution", lambda *_a, **_k: 100_000.0)
    monkeypatch.setattr("aletheia.risk.engine.per_symbol_notional_ceiling", lambda *_a, **_k: 1_000_000.0)
    monkeypatch.setattr("aletheia.risk.engine._account_equity", lambda *_a, **_k: 100_000.0)
    from tests.helpers.venue_margin_fixture import seed_venue_margin_envelope
    seed_venue_margin_envelope()
    primary = EntryAnchor(
        venue="bitget", symbol="BTC/USDT", market_type="perp",
        kind="candidate_reference" if profile == "grounded-anchor-direct" else "confirmed_swing",
        price=100, directional_role="entry_candidate", known_at_ms=1,
        source_fact_refs=("candidate:c1",), source_fingerprint="candidate",
    )
    invalidation = EntryAnchor(
        venue="bitget", symbol="BTC/USDT", market_type="perp", kind="confirmed_swing",
        price=95, directional_role="invalidation_candidate", known_at_ms=1,
        source_fact_refs=("swing:s1",), source_fingerprint="swing",
    )
    opportunity = json.loads(candidate.candidate_opportunity_context_json)
    context = EntryAnalysisContext(
        analysis_cutoff_at="2026-09-03T00:00:00+00:00",
        instrument={"venue": "bitget", "symbol": "BTC/USDT", "market_type": "perp", "direction": "long"},
        planning_generation={
            "id": generation["id"], "candidate_id": candidate.id,
            "candidate_family": "mean_reversion", "cycle_number": 1,
            "entry_behavior_fingerprint": generation["entry_behavior_fingerprint"],
            "schema_fingerprint": generation["schema_fingerprint"],
        },
        candidate={"candidate_price": 100, "fact_refs": [f"candidate:{candidate.id}"]},
        candidate_opportunity_context=opportunity,
        inference_identity={
            "caller": "agent_shell.entry_planner",
            "prompt_fingerprint": entry_prompt_fingerprint(),
            "model_provider": "test",
        },
        candle_windows={"4h": {"availability": "unavailable", "bars": []}, "1h": {"availability": "unavailable", "bars": []}, "15m": {"availability": "available", "bars": [{} for _ in range(96)]}},
        market_structure={"15m": {"availability": "available", "atr14": 2.0, "break_tolerance": 0.1}, "fact_refs": [invalidation.anchor_id]},
        quant_context={"values": {"atr_pct": 2.0}, "fact_refs": []},
        portfolio_context={"availability": "available", "fact_refs": []},
        execution_context={"availability": "available", "bid": 99.9, "ask": 100.1, "spread_bps": 2, "depth_10bps_usd": 100_000, "estimated_costs": 0.1, "fact_refs": []},
        anchors=(primary, invalidation),
        entry_profiles=({"id": profile},),
        learning_context=empty_entry_learning_context(
            as_of_at="2026-09-03T00:00:00+00:00", mechanism="reversal", direction="long",
            market_type="perp", policy="candidate_selection_utility", policy_epoch="epoch-1",
        ),
    )
    hypothesis = EntryHypothesis(
        decision="ENTRY_PLAN", reason_code="OK", mechanism="reversal", direction="long",
        primary_anchor_ref=primary.anchor_id, invalidation_anchor_ref=invalidation.anchor_id,
        entry_profile_id=profile,
        capture_priority="PRICE_FIRST" if profile == "grounded-anchor-direct" else "FILL_FIRST", capture_reason="fixture current evidence", capture_evidence_refs=(invalidation.anchor_id,),
        risk_tier="LOW", horizon="INTRADAY",
        supporting_fact_refs=(invalidation.anchor_id,),
        risk_intent=RiskIntent(posture="BASELINE", reason="fixture baseline posture", evidence_refs=(invalidation.anchor_id,)),
    )
    input_payload = canonical_json_strict(context.model_dump(mode="json"))
    repo.execute_write(
        "UPDATE candidate_planning_generations SET planner_input_json=?, "
        "planner_input_hash=? WHERE id=?",
        (input_payload, fingerprint(context.model_dump(mode="json")), generation["id"]),
    )

    from aletheia.agents import helpers
    from aletheia.agent_shell.entry_planner import system_operator_entry_planner
    from aletheia.model_provider.base import ModelResponse

    requests = []
    responses = iter([
        ModelResponse(
            content=json.dumps({
                **hypothesis.model_dump(mode="json"),
                "supporting_fact_refs": ["missing:fact"],
            }),
            model="test-model", raw={"usage": {}},
        ),
        ModelResponse(
            content=hypothesis.model_dump_json(),
            model="test-model", raw={"usage": {}},
        ),
    ])

    async def complete(request, **_kwargs):
        requests.append(request)
        return next(responses)

    async def provider(_name):
        return object()

    monkeypatch.setattr(helpers, "complete_with_fallback", complete)
    monkeypatch.setattr(helpers, "get_provider_async", provider)

    async def planner(planner_context):
        assert planner_context["candle_windows"]["15m"]["bars"] == [{} for _ in range(96)]
        assert planner_context["market_structure"]["fact_refs"] == [invalidation.anchor_id]
        return await system_operator_entry_planner(planner_context, "test")

    if compiler_error:
        from aletheia.agent_shell.entry_planner import PlanIntentError
        from aletheia.trading.entry_plan_compiler import compile_entry_plan

        def fail_compile(*args, **kwargs):
            if compiler_error in {"RUNNING_CONTRACT_CHANGED", "HYPOTHESIS_CHECKPOINT_MISMATCH"}:
                plan = compile_entry_plan(*args, **kwargs)
                if compiler_error == "HYPOTHESIS_CHECKPOINT_MISMATCH":
                    changed = hypothesis.model_copy(update={
                        "supporting_fact_refs": (invalidation.anchor_id, primary.anchor_id),
                    })
                    payload = canonical_json_strict(changed.model_dump(mode="json"))
                    return plan.model_copy(update={"entry_hypothesis_json": payload,
                        "entry_hypothesis_hash": hashlib.sha256(payload.encode()).hexdigest()})
                monkeypatch.setattr("aletheia.trading.entry_contract.entry_contract_fingerprints",
                    lambda: ("new-running-behavior", "new-running-schema"))
                return plan
            code, _, detail = str(compiler_error).partition(":")
            if compiler_error == "COMPILER_RETRY_EXHAUSTED":
                code = "COMPILER_RISK_BUDGET_UNAVAILABLE"
            raise PlanIntentError(code, detail)
        monkeypatch.setattr("aletheia.trading.entry_plan_compiler.compile_entry_plan", fail_compile)
    result = asyncio.run(
        plan_next_candidate(planner, cfg=cfg, owner_token="canonical-owner")
    )
    if compiler_error:
        stored = repo.get_candidate_planning_generation(generation["id"])
        operational = compiler_error in {"COMPILER_RISK_BUDGET_UNAVAILABLE", "COMPILER_RETRY_EXHAUSTED"}
        assert result["status"] == ("unavailable" if operational else "failed")
        assert stored["outcome_class"] == ("planning_unavailable" if operational else "planning_failed")
        assert stored["planner_input_json"] == input_payload
        assert stored["model_invoked"] == stored["compiler_invoked"] == 1
        assert stored["attention_status"] == ("active" if operational else "terminal")
        expected_activation = {
            "RUNNING_CONTRACT_CHANGED": "entry_plan_running_contract_mismatch",
            "HYPOTHESIS_CHECKPOINT_MISMATCH": "entry_plan_hypothesis_checkpoint_mismatch",
        }.get(compiler_error)
        if expected_activation:
            assert stored["outcome_reason"] == f"ENTRY_PLAN_ACTIVATION_INVALID:{expected_activation}"
        if ":" in str(compiler_error):
            # A rejection that measured something must keep the measurement: the
            # typed reason stays the code consumers match on (#3743).
            measured = json.loads(stored["result_json"])
            assert stored["outcome_reason"] == "ENTRY_PLAN_COMPILE_INVALID:stop_distance_below_vol_target"
            assert measured["reason"] == stored["outcome_reason"]
            assert measured["detail"] == "0.0125<0.0150"
        if compiler_error == "COMPILER_RETRY_EXHAUSTED":
            for attempt in (2, 3):
                repo.execute_write("UPDATE candidate_planning_generations SET retry_not_before=NULL WHERE id=?",
                                   (generation["id"],))
                retry = asyncio.run(plan_next_candidate(planner, cfg=cfg, owner_token=f"retry-{attempt}"))
                assert retry["status"] == "unavailable" and retry["model_invoked"] is False
                after = repo.get_candidate_planning_generation(generation["id"])
                assert after["attempt_count"] == attempt
                assert after["validated_hypothesis_json"] == stored["validated_hypothesis_json"]
            assert after["attention_status"] == "terminal"
            assert len(requests) == 2
            assert asyncio.run(plan_next_candidate(planner, cfg=cfg))["status"] == "idle"
            return
        if operational:
            # Resume through a new claim after the dependency recovers. The two
            # physical model responses above are exhausted: only compilation may repeat.
            monkeypatch.setattr("aletheia.trading.entry_plan_compiler.compile_entry_plan", compile_entry_plan)
            repo.execute_write("UPDATE candidate_planning_generations SET retry_not_before=NULL WHERE id=?",
                               (generation["id"],))
            async def no_model_capacity(*_args, **_kwargs):
                return 0
            monkeypatch.setattr("aletheia.model_provider.broker.inference_broker.available_capacity", no_model_capacity)
            fresh = _typed_candidate(mechanism="reversal", family="mean_reversion",
                                     symbol="ETH/USDT", asset_id="asset-eth", rank=1)
            fresh_generation, _ = repo.ensure_candidate_planning_generation(fresh.id)
            repo.execute_write("UPDATE universe_members SET rank=2 WHERE id=?", (candidate.id,))
            batch = asyncio.run(run_entry_planner_batch(cfg=cfg))
            assert batch["status"] == "batch", "Compiler recovery must not require an inference slot"
            recovered = batch["results"][0]
            assert recovered["status"] == "planned"
            assert recovered["model_invoked"] is False and recovered["compiler_invoked"] is True
            after = repo.get_candidate_planning_generation(generation["id"])
            assert after["cycle_number"] == stored["cycle_number"]
            assert after["planner_input_json"] == stored["planner_input_json"]
            assert after["planner_input_hash"] == stored["planner_input_hash"]
            assert after["model_invoked_at"] == stored["model_invoked_at"]
            assert after["validated_hypothesis_json"] == stored["validated_hypothesis_json"]
            assert len(requests) == 2
            assert repo.get_candidate_planning_generation(fresh_generation["id"])["attempt_count"] == 0
            compiled = EntryPlan.model_validate_json(repo.get_entry_plan(recovered["entry_plan_id"])["plan_json"])
            assert json.loads(compiled.entry_hypothesis_json) == hypothesis.model_dump(mode="json")
        return
    assert result["status"] == "planned", repo.get_candidate_planning_generation(generation["id"])["result_json"]
    stored = repo.get_candidate_planning_generation(generation["id"])
    plan = EntryPlan.model_validate_json(
        repo.get_entry_plan(result["entry_plan_id"])["plan_json"]
    )

    assert plan.entry_profile_id == profile
    if profile == "grounded-anchor-retrace-reclaim":
        assert plan.execution_mode == "TRIGGERED"
        assert plan.trigger_spec_id == "anchor-retrace-reclaim"
        assert plan.order_tactic == "OPPOSING_BBO_IOC"
    assert plan.behavior_fingerprint == generation["entry_behavior_fingerprint"]
    assert plan.analysis_context_fingerprint == context.context_fingerprint
    assert plan.thesis == "reversal grounded entry"
    assert plan.required_permission == "Full"
    assert plan.execution_account_mode == "bitget_demo"
    assert stored["model_invoked"] == stored["compiler_invoked"] == 1
    assert stored["outcome_class"] == "entry_plan"
    assert len(requests) == 2
    assert requests[1].exclude_models == ()
    monkeypatch.setattr("aletheia.config.loader.load_config", lambda **_kwargs: cfg)
    from aletheia.execution.entry_plan_execution import _require_ai_entry_authority

    _require_ai_entry_authority(plan)
    if plan.execution_mode == "TRIGGERED":
        from aletheia.execution.entry_plan_execution import build_entry_plan_order_intent

        with pytest.raises(ValueError, match="entry_trigger_activation_required"):
            build_entry_plan_order_intent(
                plan, bid=100, ask=100.05,
                observed_at=plan.approved_at, source_observed_at=plan.approved_at,
            )
        with pytest.raises(ValueError, match="entry_trigger_activation_required"):
            build_entry_plan_order_intent(
                plan, bid=100, ask=100.05,
                observed_at=plan.approved_at, source_observed_at=plan.approved_at,
                trigger_activation_id="caller-authored",
                trigger_activation_hash=plan.trigger_params["geometry_fingerprint"],
            )
        assert repo.get_entry_plan(plan.plan_id)["status"] == "waiting"
    original_record = repo.get_entry_plan(plan.plan_id)
    for running in (
        ("changed-behavior", generation["schema_fingerprint"]),
        (generation["entry_behavior_fingerprint"], "changed-schema"),
    ):
        monkeypatch.setattr(
            "aletheia.trading.entry_contract.entry_contract_fingerprints",
            lambda running=running: running,
        )
        with pytest.raises(ValueError, match="ai_entry_running_contract_mismatch"):
            _require_ai_entry_authority(plan)
    assert repo.get_entry_plan(plan.plan_id) == original_record





def test_passive_plan_waits_for_market_without_repricing_or_replanning(monkeypatch):
    from aletheia.config.loader import load_config
    from aletheia.execution.entry_plan_execution import build_entry_plan_order_intent
    from aletheia.trading.entry_analysis import EntryAnalysisContext, EntryHypothesis, fingerprint
    from tests.test_entry_analysis import _anchor, _context_v2

    cfg = load_config()
    monkeypatch.setattr("aletheia.risk.engine.account_available_for_execution", lambda *_args: 5000.0)
    monkeypatch.setattr("aletheia.risk.engine._account_equity", lambda *_args, **_kwargs: 5000.0)
    from tests.helpers.venue_margin_fixture import seed_venue_margin_envelope
    seed_venue_margin_envelope()
    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    repo.execute_write("UPDATE candidate_planning_generations SET producer_commit='controlled-passive-test' WHERE id=?",
                       (generation["id"],))
    primary = _anchor("candidate_reference", 100, f"candidate:{candidate.id}")
    invalidation = _anchor("confirmed_swing", 95, "swing:1")
    data = _context_v2((primary, invalidation)).model_dump(mode="json", exclude={"context_fingerprint"})
    data["planning_generation"] = {"id": generation["id"], "candidate_id": candidate.id,
        "candidate_family": "mean_reversion", "cycle_number": 1,
        "entry_behavior_fingerprint": generation["entry_behavior_fingerprint"],
        "schema_fingerprint": generation["schema_fingerprint"]}
    data["candidate_opportunity_context"] = json.loads(candidate.candidate_opportunity_context_json)
    data["candidate"]["candidate_price"] = 100
    data["market_structure"]["15m"].update(atr14=2, break_tolerance=0.1)
    data["execution_context"].update(availability="available", bid=99.89, ask=99.9,
        spread_bps=1.01, depth_10bps_usd=100000, estimated_costs=0.1)
    context = EntryAnalysisContext.model_validate(data)
    frozen = canonical_json_strict(context.model_dump(mode="json"))
    repo.execute_write("UPDATE candidate_planning_generations SET planner_input_json=?,planner_input_hash=? WHERE id=?",
                       (frozen, fingerprint(context.model_dump(mode="json")), generation["id"]))
    calls = []
    async def model(value):
        calls.append(value)
        return EntryHypothesis(decision="ENTRY_PLAN", reason_code="OK", mechanism="reversal", direction="long",
            primary_anchor_ref=primary.anchor_id, invalidation_anchor_ref=invalidation.anchor_id,
            entry_profile_id="grounded-anchor-direct", capture_priority="PRICE_FIRST", capture_reason="fixture current evidence", capture_evidence_refs=(invalidation.anchor_id,),
            risk_tier="LOW", horizon="INTRADAY", supporting_fact_refs=(invalidation.anchor_id,),
            risk_intent=RiskIntent(posture="BASELINE", reason="fixture baseline posture", evidence_refs=(invalidation.anchor_id,)))

    result = asyncio.run(plan_next_candidate(model, cfg=cfg))
    assert result["status"] == "planned", repo.get_candidate_planning_generation(generation["id"])["outcome_reason"]
    record = repo.get_entry_plan(result["entry_plan_id"])
    plan = EntryPlan.model_validate_json(record["plan_json"])
    now = datetime.now(timezone.utc)
    times = dict(observed_at=now.isoformat(), source_observed_at=now.isoformat(), now=now)
    with pytest.raises(ValueError, match="post_only_would_cross_bbo"):
        build_entry_plan_order_intent(plan, bid=99.89, ask=99.9, **times)
    assert repo.get_trade_proposal_by_entry_plan(plan.plan_id) is None
    from aletheia.agent_runtime.fast_trigger_watch import _entry_plan_expiry_reason

    monitoring = {"state": "ready", "passive_entry": {"eligible": False, "observed_at": now.isoformat()}}
    assert repo.record_entry_plan_monitoring(plan.plan_id, monitoring)
    assert _entry_plan_expiry_reason(repo.get_entry_plan(plan.plan_id)) == "passive_entry_never_became_eligible"
    lifecycle = repo.get_candidate_entry_lifecycle(candidate.id)
    assert lifecycle["lifecycle_state"] == "WAITING_MARKET"
    assert lifecycle["lifecycle_reason"] == "post_only_would_cross_bbo"
    assert lifecycle["state_changed_at"] == now.isoformat()
    intent = build_entry_plan_order_intent(plan, bid=99.99, ask=100.01, **times)
    assert intent.price == plan.limit_price == 100
    assert intent.order_tactic == "POST_ONLY_LIMIT" and intent.post_only is True
    ready = {"state": "ready", "passive_entry": {"eligible": True, "observed_at": now.isoformat()}}
    assert repo.record_entry_plan_monitoring(plan.plan_id, ready)
    assert repo.get_candidate_entry_lifecycle(candidate.id)["lifecycle_state"] == "PLAN_READY"
    assert repo.record_entry_plan_monitoring(plan.plan_id, monitoring)
    assert _entry_plan_expiry_reason(repo.get_entry_plan(plan.plan_id)) == "entry_plan_expired"
    assert repo.record_entry_plan_monitoring(plan.plan_id, {"state": "unavailable"})
    assert _entry_plan_expiry_reason(repo.get_entry_plan(plan.plan_id)) == "monitoring_stream_unavailable"
    assert json.loads(repo.get_entry_plan(plan.plan_id)["monitoring_evidence_json"])["passive_entry"]["ever_eligible"] is True
    assert repo.get_entry_plan(plan.plan_id)["plan_json"] == record["plan_json"]
    assert repo.get_candidate_planning_generation(generation["id"])["planner_input_json"] == frozen
    assert asyncio.run(plan_next_candidate(model, cfg=cfg))["status"] == "idle"
    assert len(calls) == 1


def test_entry_hypothesis_portable_schema_requires_every_strict_property():
    schema = portable_json_schema_payload(EntryHypothesis)
    properties = schema["properties"]
    assert set(schema["required"]) == set(properties)
    assert set(properties) == set(EntryHypothesis.model_fields)
    assert schema["additionalProperties"] is False
    assert properties["capture_priority"]["type"] == ["string", "null"]
    encoded = json.dumps(schema, sort_keys=True)
    assert "$ref" not in encoded
    assert "$defs" not in encoded
    assert "anyOf" not in encoded







































def test_entry_planner_batch_uses_bounded_parallel_workers(monkeypatch):
    from aletheia.config.schema import ModelSelection

    selection = ModelSelection(endpoint_id="deep-paid", model_id="large")
    active = 0
    peak = 0
    calls = 0
    provider_names = []

    async def fake_run_once(cfg=None, *, provider_name=None, resume_only=False):
        assert resume_only is False
        nonlocal active, peak, calls
        calls += 1
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        assert provider_name is selection
        provider_names.append(provider_name.endpoint_id)
        return {"status": "no_plan", "model_invoked": True}

    async def capacity(provider_name, **_kwargs):
        provider_names.append(provider_name)
        return ENTRY_PLANNER_MAX_CONCURRENCY

    pending = ENTRY_PLANNER_MAX_CONCURRENCY + 5
    remaining = iter((pending, pending - ENTRY_PLANNER_MAX_CONCURRENCY))
    monkeypatch.setattr(repo, "count_pending_candidate_planning", lambda: next(remaining))
    monkeypatch.setattr(
        "aletheia.agent_shell.entry_planner.run_entry_planner_once",
        fake_run_once,
    )
    monkeypatch.setattr(
        "aletheia.model_provider.router.get_provider_for_tier",
        lambda _tier: selection,
    )
    monkeypatch.setattr(
        "aletheia.model_provider.broker.inference_broker.available_capacity",
        capacity,
    )

    result = asyncio.run(run_entry_planner_batch())

    assert ENTRY_PLANNER_MIN_CONCURRENCY == 3
    assert calls == peak == ENTRY_PLANNER_MAX_CONCURRENCY == 15
    assert result["planning_attempts"] == ENTRY_PLANNER_MAX_CONCURRENCY
    assert result["pending_queue_remaining"] == 5
    assert provider_names == ["deep-paid"] * (ENTRY_PLANNER_MAX_CONCURRENCY + 1)








def test_ineligible_candidate_cannot_complete_claimed_entry_plan():
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    assert repo.claim_next_candidate_planning("slow-owner") is not None
    repo.execute_write("UPDATE universe_members SET execution_eligible=0 WHERE id=?", (candidate.id,))

    plan = _plan(generation)
    status, record = repo.complete_candidate_planning_with_plan(generation["id"], "slow-owner", plan)

    assert status == "stale"
    assert record is None
    assert repo.get_entry_plan(plan.plan_id) is None





def test_entry_planner_batch_cancels_and_awaits_siblings_on_fatal_failure(monkeypatch):
    from aletheia.config.schema import ModelSelection

    monkeypatch.setattr(
        "aletheia.model_provider.router.get_provider_for_tier",
        lambda _tier: ModelSelection(endpoint_id="test", model_id="test-model"),
    )
    started = 0
    all_started = asyncio.Event()
    cancelled = 0

    async def worker(cfg=None, *, provider_name=None, resume_only=False):
        assert resume_only is False
        nonlocal started, cancelled
        index = started
        started += 1
        if started == 3:
            all_started.set()
        await all_started.wait()
        if index == 0:
            raise RuntimeError("fatal storage failure")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled += 1
            raise

    async def capacity(*_args, **_kwargs):
        return 3

    monkeypatch.setattr(repo, "count_pending_candidate_planning", lambda: 3)
    monkeypatch.setattr("aletheia.agent_shell.entry_planner.run_entry_planner_once", worker)
    monkeypatch.setattr(
        "aletheia.model_provider.broker.inference_broker.available_capacity",
        capacity,
    )

    try:
        asyncio.run(run_entry_planner_batch())
    except ExceptionGroup as exc:
        assert any(isinstance(item, RuntimeError) for item in exc.exceptions)
    else:
        raise AssertionError("fatal worker failure must propagate")

    assert started == 3
    assert cancelled == 2


def test_expired_planning_claim_is_recovered_after_restart():
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    start = datetime(2026, 8, 27, tzinfo=timezone.utc)
    assert repo.claim_next_candidate_planning("dead-process", lease_seconds=1, now=start)["id"] == generation["id"]
    assert repo.claim_next_candidate_planning("early", now=start) is None
    recovered = repo.claim_next_candidate_planning("new-process", now=start + timedelta(seconds=2))
    assert recovered and recovered["owner_token"] == "new-process"


def test_retry_backoff_is_not_reported_as_immediate_planning_work():
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    start = datetime(2026, 8, 27, tzinfo=timezone.utc)
    repo.claim_next_candidate_planning("owner", now=start)
    assert repo.defer_candidate_planning(generation["id"], "owner", "provider_unavailable", now=start)
    assert repo.count_pending_candidate_planning(now=start + timedelta(seconds=30)) == 0
    assert repo.count_pending_candidate_planning(now=start + timedelta(seconds=61)) == 1


def test_retry_exhaustion_terminals_attention_as_planning_unavailable():
    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id, now=start)
    for attempt in range(3):
        attempt_at = start + timedelta(seconds=attempt * 61)
        assert repo.claim_next_candidate_planning(f"owner-{attempt}", now=attempt_at)
        assert repo.defer_candidate_planning(
            generation["id"],
            f"owner-{attempt}",
            "provider_unavailable",
            retry_seconds=60,
            now=attempt_at,
        )
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert stored["status"] == "unavailable"
    assert stored["attention_status"] == "terminal"
    assert stored["attention_terminal_reason"] == "planning_unavailable"





def test_planner_contract_migration_preserves_legacy_rows_and_current_index():
    from aletheia.storage.migrations import _apply_planner_contract_version_v56

    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE candidate_planning_generations "
        "(id TEXT PRIMARY KEY, planning_key TEXT NOT NULL, is_current INTEGER NOT NULL)"
    )
    db.execute(
        "CREATE UNIQUE INDEX idx_candidate_planning_current "
        "ON candidate_planning_generations(planning_key) WHERE is_current=1"
    )
    db.execute("INSERT INTO candidate_planning_generations VALUES ('legacy','key',1)")

    _apply_planner_contract_version_v56(db)
    _apply_planner_contract_version_v56(db)

    row = db.execute("SELECT * FROM candidate_planning_generations").fetchone()
    indexes = {item[1] for item in db.execute("PRAGMA index_list(candidate_planning_generations)")}
    assert row == ("legacy", "key", 1, "entry-planner/v1")
    assert "idx_candidate_planning_current" in indexes
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO candidate_planning_generations VALUES ('duplicate','key',1,'entry-planner/v2')")


def test_no_plan_terminals_candidate_attention():
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    assert repo.claim_next_candidate_planning("owner")
    assert repo.complete_candidate_planning_no_plan(
        generation["id"],
        "owner",
        _no_plan_hypothesis().model_dump(mode="json"),
    )
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert stored["attention_status"] == "terminal"
    assert stored["attention_terminal_reason"] == "no_plan"


def test_rank_snapshot_churn_does_not_invalidate_active_plan():
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    repo.claim_next_candidate_planning("owner")
    plan = _plan(generation)
    assert repo.complete_candidate_planning_with_plan(generation["id"], "owner", plan)[0] == "planned"

    churn = _candidate(rank=9, price=106)
    same, created = repo.ensure_candidate_planning_generation(churn.id)
    assert created is False
    assert same["id"] == generation["id"]
    assert repo.get_entry_plan(plan.plan_id)["status"] == "waiting"

    active_plan_ids = {row["id"] for row in repo.list_active_entry_plans()}
    assert plan.plan_id in active_plan_ids

    repo.get_db().execute("DELETE FROM universe_members WHERE id=?", (candidate.id,))
    repo.get_db().commit()
    assert repo.get_entry_plan(plan.plan_id)["status"] == "waiting"


def test_entry_plan_terminal_state_cannot_revive():
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    repo.claim_next_candidate_planning("owner")
    plan = _plan(generation)
    repo.complete_candidate_planning_with_plan(generation["id"], "owner", plan)
    assert repo.transition_entry_plan(plan.plan_id, expected_status="waiting", status="invalidated")
    try:
        repo.transition_entry_plan(plan.plan_id, expected_status="invalidated", status="waiting")
    except ValueError as exc:
        assert str(exc) == "invalid_entry_plan_transition"
    else:
        raise AssertionError("terminal Entry Plan revived")





def test_terminal_entry_plan_detail_validates_by_exact_id():
    from aletheia.server.routes.universe import get_entry_plan_detail

    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    repo.claim_next_candidate_planning("owner")
    plan = _plan(generation)
    assert repo.complete_candidate_planning_with_plan(generation["id"], "owner", plan)[0] == "planned"
    assert repo.transition_entry_plan(plan.plan_id, expected_status="waiting", status="expired")

    detail = get_entry_plan_detail(plan.plan_id)
    assert detail["id"] == plan.plan_id
    assert detail["status"] == "expired"
    assert detail["contract_valid"] is True
    assert detail["plan"]["thesis"] == plan.thesis


def test_workbench_plan_active_filter_has_exact_candidate_handoff():
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    repo.claim_next_candidate_planning("owner")
    plan = _plan(generation)
    assert repo.complete_candidate_planning_with_plan(generation["id"], "owner", plan)[0] == "planned"

    [row] = repo.list_entry_workbench_candidates()

    assert row["attention_status"] == "terminal"
    assert row["attention_terminal_reason"] == "entry_plan_created"
    assert row["active_entry_plan_id"] == plan.plan_id
    assert row["active_entry_plan_status"] == "waiting"


def test_planning_history_uses_stable_bounded_cursor():
    from fastapi.testclient import TestClient
    from aletheia.server.app import app

    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    generations = []
    for index, symbol in enumerate(("BTC/USDT", "ETH/USDT", "SOL/USDT")):
        candidate = _candidate(asset_id=f"asset-{index}", symbol=symbol)
        generation, _ = repo.ensure_candidate_planning_generation(
            candidate.id, now=start + timedelta(seconds=index // 2),
        )
        generations.append(generation)

    client = TestClient(app)
    first = client.get("/api/universe/planning-history", params={"limit": 2})
    assert first.status_code == 200
    first_page = first.json()
    second = client.get("/api/universe/planning-history", params={
        "limit": 2, "before": first_page["next_before"],
    })
    assert second.status_code == 200
    second_page = second.json()
    ids = [row["id"] for row in first_page["items"] + second_page["items"]]
    assert len(first_page["items"]) == 2
    assert len(ids) == len(set(ids)) == 3
    assert set(ids) == {row["id"] for row in generations}
    assert second_page["next_before"] is None
    assert client.get("/api/universe/planning-history", params={"before": "invalid"}).status_code == 422
    assert client.get("/api/universe/workbench", params={"limit": 0}).status_code == 422


def test_planning_history_list_omits_planner_blobs():
    from fastapi.testclient import TestClient
    from aletheia.server.app import app

    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    repo.execute_write(
        "UPDATE candidate_planning_generations SET planner_input_json=?, "
        "requalification_source_json=?, validated_hypothesis_json=? WHERE id=?",
        (json.dumps({"x": "x" * 100_000}), "{}", "{}", generation["id"]),
    )

    body = TestClient(app).get("/api/universe/planning-history").json()

    [item] = body["items"]
    assert item["id"] == generation["id"]
    assert not {"planner_input_json", "requalification_source_json", "validated_hypothesis_json"} & item.keys()
    assert len(json.dumps(body)) < 20_000


def test_expired_attention_projects_consistently_without_sweep_or_mutation():
    from aletheia.server.routes.universe import get_candidate

    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id, now=start)
    after_hard_deadline = start + timedelta(hours=2)

    assert list_active_candidate_attention() == []
    detail = get_candidate(candidate.id)["attention"]
    [history] = repo.list_planning_history(now=after_hard_deadline)

    assert detail["attention_status"] == history["attention_status"] == "terminal"
    assert detail["attention_terminal_reason"] == "max_attention_age"
    assert history["attention_terminal_reason"] == "max_attention_age"
    persisted = repo.get_candidate_planning_generation(generation["id"])
    assert persisted["attention_status"] == "active"


def test_fresh_stored_book_does_not_import_optional_collector(monkeypatch):
    from aletheia.agent_shell import entry_planner
    from extensions.data.collectors.microstructure import _parse_orderbook

    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion", estimated_cost=0.1)
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    book = _parse_orderbook("BTC/USDT", "bitget", {
        "bids": [[99.99, 200]], "asks": [[100.01, 200]],
    }, 0, market_type="perp")
    repo.insert_orderbook_snapshot(book.model_dump())
    reached = []

    def context_probe(*args, **kwargs):
        reached.append(kwargs["orderbook"])
        raise ValueError("stop_before_model")

    async def forbidden_model(*args):
        pytest.fail("probe must not invoke model")

    real_import = builtins.__import__
    def without_collector(name, *args, **kwargs):
        if name == "extensions.data.collectors.microstructure":
            raise ModuleNotFoundError(name, name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(entry_planner, "build_entry_analysis_context", context_probe)
    monkeypatch.setattr(builtins, "__import__", without_collector)
    result = asyncio.run(plan_next_candidate(forbidden_model))
    assert result["status"] == "unavailable"
    assert reached, repo.get_candidate_planning_generation(generation["id"])["result_json"]
    assert reached[0]["symbol"] == "BTC/USDT"


@pytest.mark.parametrize("bad_book", ["missing", "venue", "market", "future", "stale", "timeout", "load", "fetch", "invalid", "persistence", "factory", "capability", "close", "malformed"])
def test_first_freeze_waits_for_exact_fresh_book_then_resumes(monkeypatch, bad_book, caplog):
    from extensions.data.collectors.microstructure import _parse_orderbook
    from aletheia.data import providers

    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion", estimated_cost=0.1)
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    from aletheia.data.providers import Candle

    interval = 15 * 60 * 1000
    end = int(datetime.now(timezone.utc).timestamp() * 1000) // interval * interval
    repo.upsert_ohlcv_candles(
        "BTC/USDT", "15m",
        [Candle(end - (96 - i) * interval, 100, 102, 98, 100, 1000) for i in range(96)],
        exchange="bitget", market_type="perp",
    )
    known = datetime.fromtimestamp((end - 1000) / 1000, timezone.utc).isoformat()
    repo.execute_write("UPDATE market_ohlcv SET first_persisted_at=?,last_mutated_at=?", (known, known))
    repo.execute_write("UPDATE orderbook_snapshots SET observed_at='2000-01-01T00:00:00+00:00'")
    recovered = False
    calls = []

    async def observe(self, symbol, *, market_type):
        assert symbol == "BTC/USDT" and market_type == "perp"
        if not recovered and bad_book == "timeout":
            raise TimeoutError
        snapshot = _parse_orderbook(
            "BTC/USDT", "bitget", {"bids": [[99.99, 200]], "asks": [[100.01, 200]]},
            0, market_type="perp",
        )
        if not recovered:
            if bad_book in {"load", "fetch", "invalid"}:
                if bad_book == "invalid":
                    return None
                raise providers.MarketDataObservationError(
                    "load_markets" if bad_book == "load" else "fetch_order_book",
                    TimeoutError() if bad_book == "load" else ConnectionError(),
                )
            if bad_book == "missing":
                return None
            if bad_book == "malformed":
                return []
            if bad_book == "venue":
                snapshot.exchange = "binance"
            elif bad_book == "market":
                snapshot.market_type = "spot"
            elif bad_book in {"future", "stale"}:
                snapshot.observed_at = (datetime.now(timezone.utc) + timedelta(
                    seconds=60 if bad_book == "future" else -60
                )).isoformat()
        return snapshot.model_dump()

    async def planner(context):
        calls.append(context)
        raise PlannerBackpressure("busy")

    closed = []
    class Provider:
        get_orderbook_observation = observe
        async def close(self):
            closed.append(True)
            if not recovered and bad_book == "close":
                raise RuntimeError("private close detail")
    def factory(venue, *, exact_venue):
        assert venue == "bitget" and exact_venue
        if not recovered and bad_book == "factory":
            raise RuntimeError("private factory detail")
        if not recovered and bad_book == "capability":
            from types import SimpleNamespace
            return SimpleNamespace(close=Provider().close)
        return Provider()
    monkeypatch.setattr(providers, "get_market_data_provider", factory)
    insert = repo.insert_orderbook_snapshot
    def persist(book):
        if not recovered and bad_book == "persistence":
            raise sqlite3.OperationalError("private database detail")
        return insert(book)
    monkeypatch.setattr(repo, "insert_orderbook_snapshot", persist)
    result = asyncio.run(plan_next_candidate(planner))
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert result["status"] == "unavailable" and calls == []
    assert closed == ([] if bad_book == "factory" else [True])
    assert stored["planner_input_json"] is None and stored["attempt_count"] == 0
    diagnostic = [record for record in caplog.records if record.msg == "entry_orderbook_unavailable"][-1]
    assert diagnostic.category == {
        "missing": "invalid_payload", "venue": "identity_mismatch", "market": "identity_mismatch",
        "future": "future", "stale": "stale", "timeout": "timeout",
        "load": "collector_error", "fetch": "collector_error", "invalid": "invalid_payload",
        "persistence": "persistence_error",
        "factory": "collector_error", "capability": "collector_error",
        "close": "collector_error", "malformed": "invalid_payload",
    }[bad_book]
    assert "private database detail" not in caplog.text
    assert "private close detail" not in caplog.text
    assert "private factory detail" not in caplog.text
    recovered = True
    repo.execute_write("UPDATE candidate_planning_generations SET retry_not_before=NULL WHERE id=?", (generation["id"],))
    result = asyncio.run(plan_next_candidate(planner))
    assert result["status"] == "backpressure", repo.get_candidate_planning_generation(generation["id"])["result_json"]
    assert len(calls) == 1
    assert closed == ([True] if bad_book == "factory" else [True, True])
    frozen = repo.get_candidate_planning_generation(generation["id"])
    assert frozen["planner_input_json"] is not None
    book = repo.latest_orderbook("BTC/USDT", "bitget", "perp")
    assert calls[0]["execution_context"]["availability"] == "available"
    assert book["id"] in str(calls[0]["execution_context"]["fact_refs"])


@pytest.mark.parametrize("frozen", [False, True])
def test_missing_profiles_are_non_economic_blocks(monkeypatch, frozen):
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    if frozen:
        _plan(generation, profiles=())
    else:
        monkeypatch.setattr("aletheia.trading.entry_profiles.compatible_entry_profiles", lambda _: ())
    def unexpected(*args, **kwargs):
        pytest.fail("missing profiles must block before market/model")
    monkeypatch.setattr(repo, "latest_orderbook", unexpected)
    result = asyncio.run(plan_next_candidate(unexpected))
    row = repo.get_candidate_planning_generation(generation["id"])
    assert result["status"] == "blocked"
    assert row["outcome_class"] == "planning_blocked"
    assert row["outcome_reason"] == "NO_VALID_ENTRY_PROFILE"
    assert row["attempt_count"] == 0


@pytest.mark.parametrize("phase", ["prefetch", "model", "persistence"])
@pytest.mark.parametrize("replace_owner", [False, True])
def test_cancellation_recovers_only_owned_claim(monkeypatch, phase, replace_owner):
    from aletheia.trading.entry_lifecycle import project_candidate_entry_lifecycle
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    if phase != "prefetch":
        _plan(generation)
    original = repo.get_candidate_planning_generation(generation["id"])
    def cancel(*args, **kwargs):
        if replace_owner:
            repo.execute_write("UPDATE candidate_planning_generations SET owner_token='new-owner' WHERE id=?", (generation["id"],))
        raise asyncio.CancelledError
    async def planner(context):
        if phase == "model":
            cancel()
        return {"decision": "NO_PLAN", "reason_code": "INSUFFICIENT_EVIDENCE",
                "mechanism": "continuation", "direction": "long", "risk_tier": "NONE", "horizon": "NONE"}
    if phase == "prefetch":
        monkeypatch.setattr(repo, "latest_orderbook", cancel)
    elif phase == "persistence":
        monkeypatch.setattr(repo, "complete_candidate_planning_no_plan", cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(plan_next_candidate(planner, owner_token="cancel-owner"))
    row = repo.get_candidate_planning_generation(generation["id"])
    assert row["planner_input_json"] == original["planner_input_json"]
    assert row["planner_input_hash"] == original["planner_input_hash"]
    if replace_owner:
        assert row["status"] == "planning"
        assert row["owner_token"] == "new-owner"
        return
    assert row["owner_token"] is None
    assert row["status"] == "unavailable"
    assert row["outcome_class"] == "planning_unavailable"
    assert row["attempt_count"] == (0 if phase == "prefetch" else 1)
    assert bool(row["model_invoked"]) == (phase == "persistence")
    if phase == "model":
        events = project_candidate_entry_lifecycle({}, [row])["cycles"][0]["events"]
        assert "model_dispatch_unavailable" in {event["event"] for event in events}
        assert "model_skipped" not in {event["event"] for event in events}


@pytest.mark.parametrize("operation", ["release", "block", "defer", "fail"])
def test_non_economic_results_preserve_invocation_history(operation):
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    repo.claim_next_candidate_planning("owner")
    repo.mark_candidate_planning_stage(generation["id"], "owner", "compiler", model_invoked=True, compiler_invoked=True)
    if operation == "release":
        repo.release_candidate_planning_claim(generation["id"], "owner", "DEPENDENCY_UNAVAILABLE")
    else:
        getattr(repo, f"{operation}_candidate_planning")(
            generation["id"], "owner", "DEPENDENCY_UNAVAILABLE", stage="runtime_preflight",
            model_invoked=False, compiler_invoked=False)
    row = repo.get_candidate_planning_generation(generation["id"])
    assert row["model_invoked"] == row["compiler_invoked"] == 1
    assert row["outcome_reason"] == "DEPENDENCY_UNAVAILABLE"
    if operation == "release":
        assert row["last_stage"] == "compiler"
        assert row["outcome_class"] == "planning_unavailable"


@pytest.mark.parametrize("phase", ["prefetch", "model"])
def test_real_task_cancellation_is_shielded_and_dispatch_retries_are_bounded(monkeypatch, phase):
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    if phase == "model":
        _plan(generation)
    else:
        monkeypatch.setattr(repo, "latest_orderbook", lambda *args: None)
    original = repo.get_candidate_planning_generation(generation["id"])

    async def exercise():
        closed = []
        for attempt in range(3 if phase == "model" else 1):
            entered = asyncio.Event()
            async def wait_for_cancel(*args, **kwargs):
                entered.set()
                await asyncio.Event().wait()
            if phase == "prefetch":
                from types import SimpleNamespace
                async def close():
                    closed.append(True)
                monkeypatch.setattr("aletheia.data.providers.get_market_data_provider", lambda *args, **kwargs: SimpleNamespace(
                    get_orderbook_observation=wait_for_cancel, close=close,
                ))
            task = asyncio.create_task(plan_next_candidate(wait_for_cancel))
            await asyncio.wait_for(entered.wait(), timeout=1)
            task.cancel()
            asyncio.get_running_loop().call_soon(task.cancel)
            with pytest.raises(asyncio.CancelledError):
                await task
            if phase == "prefetch":
                assert closed == [True]
            row = repo.get_candidate_planning_generation(generation["id"])
            assert row["owner_token"] is None
            assert row["attempt_count"] == (attempt + 1 if phase == "model" else 0)
            assert row["planner_input_hash"] == original["planner_input_hash"]
            assert not row["model_invoked"]
            if phase == "model" and attempt < 2:
                assert row["attention_status"] == "active"
                repo.execute_write("UPDATE candidate_planning_generations SET retry_not_before=? WHERE id=?",
                                   ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), generation["id"]))
        if phase == "model":
            assert row["attention_status"] == "terminal"
            assert row["attention_terminal_reason"] == "planning_unavailable"
            assert repo.claim_next_candidate_planning("later") is None
    asyncio.run(exercise())


@pytest.mark.parametrize("field,wrong", [("mechanism", "continuation"), ("direction", "short")])
@pytest.mark.parametrize("repair", [True, False])
def test_system_planner_repairs_identity_without_rewriting(monkeypatch, field, wrong, repair):
    from tests.test_entry_analysis import _maximal_context
    from aletheia.agent_shell.entry_planner import system_operator_entry_planner, PlanIntentError
    from aletheia.agents import helpers
    from aletheia.model_provider.base import ModelResponse
    from aletheia.trading.entry_analysis import EntryHypothesis

    context = _maximal_context()
    valid = EntryHypothesis(
        decision="NO_PLAN", reason_code="CONFLICTING_SIGNALS", mechanism="reversal",
        direction="long", risk_tier="NONE", horizon="NONE",
    ).model_dump(mode="json")
    responses = iter([{**valid, field: wrong}, valid if repair else {**valid, field: wrong}])
    calls = []

    async def complete(request, **kwargs):
        calls.append(request)
        return ModelResponse(content=json.dumps(next(responses)), model="test")

    async def provider(name):
        return object()

    monkeypatch.setattr(helpers, "complete_with_fallback", complete)
    monkeypatch.setattr(helpers, "get_provider_async", provider)
    if repair:
        result = asyncio.run(system_operator_entry_planner(context.model_dump(mode="json"), "test"))
        assert result.model_dump(mode="json") == valid
    else:
        with pytest.raises(PlanIntentError, match="PLANNER_SCHEMA_INVALID:"):
            asyncio.run(system_operator_entry_planner(context.model_dump(mode="json"), "test"))
    assert len(calls) == 2
    assert calls[0].structured_schema["properties"][field]["enum"] == [valid[field]]


@pytest.mark.parametrize("bad_book", ["wide", "thin"])
def test_market_quality_wait_does_not_bind_and_recovers_same_generation(monkeypatch, bad_book):
    from extensions.data.collectors.microstructure import _parse_orderbook
    from aletheia.data.providers import Candle
    from aletheia.trading.entry_plan_compiler import require_entry_market_quality
    from aletheia.config.loader import load_config

    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion", estimated_cost=0.1)
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    interval = 15 * 60 * 1000
    end = int(datetime.now(timezone.utc).timestamp() * 1000) // interval * interval
    repo.upsert_ohlcv_candles("BTC/USDT", "15m",
        [Candle(end - (96-i)*interval, 100, 102, 98, 100, 1000) for i in range(96)],
        exchange="bitget", market_type="perp")
    known = datetime.fromtimestamp((end-1000)/1000, timezone.utc).isoformat()
    repo.execute_write("UPDATE market_ohlcv SET first_persisted_at=?,last_mutated_at=?", (known, known))

    def book(recovered=False):
        snapshot = _parse_orderbook("BTC/USDT", "bitget",
            {"bids": [[99.99, 200]], "asks": [[100.01, 200]]}, 0, market_type="perp")
        if not recovered:
            if bad_book == "wide":
                snapshot.spread_bps = 26
            else:
                snapshot.total_depth_10bps_usd = 24999
        data = snapshot.model_dump()
        repo.insert_orderbook_snapshot(data)
        return data

    book()
    calls = []
    async def planner(context):
        require_entry_market_quality(context["execution_context"], load_config())
        calls.append(context)
        raise PlannerBackpressure("busy")

    result = asyncio.run(plan_next_candidate(planner))
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert result["reason"] == "ENTRY_ANALYSIS_MARKET_QUALITY_UNAVAILABLE"
    assert result["model_invoked"] is False and result["compiler_invoked"] is False
    assert not calls and stored["planner_input_json"] is None
    assert stored["validated_hypothesis_json"] is None and stored["entry_plan_id"] is None
    assert stored["attention_status"] == "active" and stored["attempt_count"] == 0
    assert stored["retry_not_before"] is not None
    recovered = book(True)
    repo.execute_write("UPDATE candidate_planning_generations SET retry_not_before=NULL WHERE id=?", (generation["id"],))
    result = asyncio.run(plan_next_candidate(planner))
    assert result["status"] == "backpressure"
    assert len(calls) == 1 and recovered["id"] in str(calls[0]["execution_context"]["fact_refs"])
    assert result["planning_generation_id"] == generation["id"]
    assert repo.get_db().execute("SELECT count(*) FROM candidate_planning_generations").fetchone()[0] == 1
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert stored["planner_input_json"] is not None and stored["entry_plan_id"] is None
    return stored


@pytest.mark.parametrize("updates", [{"spread_bps": 26}, {"depth_10bps_usd": 24999}, {"estimated_costs": -1}])
def test_direct_compiler_keeps_exact_market_quality_backstop(monkeypatch, updates):
    from types import SimpleNamespace
    from aletheia.config.loader import load_config
    from aletheia.agent_shell.entry_planner import PlanIntentError
    from aletheia.trading.entry_plan_compiler import compile_entry_plan
    monkeypatch.setattr("aletheia.trading.entry_analysis.validate_hypothesis_references", lambda *a: None)
    monkeypatch.setattr("aletheia.trading.entry_authority.require_entry_runtime_authority", lambda *a: None)
    context = SimpleNamespace(execution_context=dict(availability="available",spread_bps=2,
        depth_10bps_usd=100000,estimated_costs=.1) | updates)
    with pytest.raises(PlanIntentError, match="EXECUTION_CONTEXT_UNAVAILABLE"):
        compile_entry_plan({"market_type": "perp", "direction": "long"}, context, None, load_config())


def test_unbound_candidate_refresh_preserves_birth_context_owner():
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    repo.claim_next_candidate_planning("owner")
    replacement = _candidate(rank=8, price=105)
    same, created = repo.ensure_candidate_planning_generation(replacement.id)
    assert not created and same["id"] == generation["id"]
    assert same["last_candidate_id"] == candidate.id
    source = json.loads(same["requalification_source_json"])["candidate_opportunity_context"]
    assert source["candidate_id"] == candidate.id
    assert source["context_hash"] == repo.get_candidate(same["last_candidate_id"])["candidate_opportunity_context_hash"]
    assert repo.get_candidate_planning_for_candidate(replacement.id)["id"] == generation["id"]


def test_spot_short_blocks_before_market_model_and_compiler(monkeypatch):
    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion", market_type="spot", direction="short")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    def unexpected(*args, **kwargs):
        pytest.fail("Spot short must stop before market/model work")
    monkeypatch.setattr(repo, "latest_orderbook", unexpected)
    result = asyncio.run(plan_next_candidate(unexpected))
    assert result["reason"] == "spot_short_not_allowed"
    assert not result["model_invoked"] and not result["compiler_invoked"]
    assert repo.get_candidate_planning_generation(generation["id"])["planner_input_json"] is None
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert stored["attention_status"] == "terminal" and stored["outcome_class"] == "planning_failed"
    assert stored["retry_not_before"] is None
    assert asyncio.run(plan_next_candidate(unexpected))["status"] == "idle"


def test_replacement_candidate_handoff_uses_canonical_cycle_association():
    from aletheia.server.routes.universe import _attach_candidate_handoff_state
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    replacement = _candidate(rank=8)
    repo.ensure_candidate_planning_generation(replacement.id)
    repo.claim_next_candidate_planning("owner")
    plan = _plan(generation)
    status, record = repo.complete_candidate_planning_with_plan(generation["id"], "owner", plan)
    assert status == "planned"
    [projection] = _attach_candidate_handoff_state([repo.get_candidate(replacement.id)])
    assert projection["entry_plan_id"] == record["id"]
    assert projection["entry_plan_status"] == record["status"]


def test_release_candidate_planning_claim_detail_is_optional():
    """#3750: ``detail`` mirrors ``fail_candidate_planning`` -- persisted beside
    ``reason`` only when non-empty, never changing the existing shape."""
    for i, (reason, detail, expected) in enumerate([
        ("SOME_REASON", "", {"reason": "SOME_REASON"}),
        ("SOME_REASON", "category=stale elapsed_ms=12",
         {"reason": "SOME_REASON", "detail": "category=stale elapsed_ms=12"}),
    ]):
        candidate = _candidate(asset_id=f"asset-{i}", symbol=f"SYM{i}/USDT")
        generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
        repo.claim_next_candidate_planning("owner")
        kwargs = {"retry_seconds": 1}
        if detail:
            kwargs["detail"] = detail
        assert repo.release_candidate_planning_claim(generation["id"], "owner", reason, **kwargs)
        row = repo.get_candidate_planning_generation(generation["id"])
        assert json.loads(row["result_json"]) == expected


def test_live_fetch_failure_orderbook_records_category(monkeypatch):
    """Mechanism A (#3750): a live-fetch failure's category now survives past
    the dropped ``logger.warning`` into the persisted ``result_json.detail``."""
    from aletheia.data import providers

    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion", estimated_cost=0.1)
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)

    class Provider:
        async def get_orderbook_observation(self, symbol, *, market_type):
            raise TimeoutError
        async def close(self):
            pass
    monkeypatch.setattr(providers, "get_market_data_provider", lambda *a, **k: Provider())

    async def forbidden_model(*args):
        pytest.fail("model must not be invoked when execution context is unavailable")

    result = asyncio.run(plan_next_candidate(forbidden_model))
    assert result["status"] == "unavailable"
    stored = repo.get_candidate_planning_generation(generation["id"])
    payload = json.loads(stored["result_json"])
    assert payload["reason"] == "ENTRY_ANALYSIS_EXECUTION_CONTEXT_UNAVAILABLE"
    assert payload["detail"].startswith("category=timeout elapsed_ms=")


def test_inner_fresh_outer_stale_orderbook_records_mechanism(monkeypatch):
    """Mechanism B (#3750): the persisted book was fresh at
    ``_ensure_entry_orderbook``'s own check, but the caller's fresh recheck a
    moment later falls outside the 2s window -- previously left no trace at
    all; now persists ``inner_fresh_outer_stale:elapsed_ms=...``."""
    from aletheia.agent_shell import entry_planner
    from extensions.data.collectors.microstructure import _parse_orderbook

    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion", estimated_cost=0.1)
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    base = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
    book = _parse_orderbook("BTC/USDT", "bitget", {
        "bids": [[99.99, 200]], "asks": [[100.01, 200]],
    }, 0, market_type="perp")
    book.observed_at = base.isoformat()
    repo.insert_orderbook_snapshot(book.model_dump())

    class AdvancingClock(datetime):
        calls = 0
        @classmethod
        def now(cls, tz=None):
            cls.calls += 1
            return base if cls.calls == 1 else base + timedelta(seconds=3)

    monkeypatch.setattr(entry_planner, "datetime", AdvancingClock)

    async def forbidden_model(*args):
        pytest.fail("model must not be invoked when execution context is unavailable")

    result = asyncio.run(plan_next_candidate(forbidden_model))
    assert result["status"] == "unavailable"
    stored = repo.get_candidate_planning_generation(generation["id"])
    payload = json.loads(stored["result_json"])
    assert payload["reason"] == "ENTRY_ANALYSIS_EXECUTION_CONTEXT_UNAVAILABLE"
    assert payload["detail"] == "inner_fresh_outer_stale:elapsed_ms=3000"
