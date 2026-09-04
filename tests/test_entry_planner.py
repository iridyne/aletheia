import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from aletheia.agent_shell.entry_planner import (
    ENTRY_PLANNER_MAX_CONCURRENCY,
    ENTRY_PLANNER_MIN_CONCURRENCY,
    PlanIntent,
    _PlanIntentWire,
    PlannerBackpressure,
    plan_next_candidate,
    run_entry_planner_batch,
)
from aletheia.storage import repositories as repo
from aletheia.storage.models import UniverseAsset, UniverseMember, UniverseSnapshot
from aletheia.trading.entry_plan import EntryPlan
from aletheia.model_provider.base import portable_json_schema_payload

_PlanIntentWireSchema = _PlanIntentWire


@pytest.fixture(autouse=True)
def _bitget_market_data_authority(_inmemory_db):
    """Keep this module's Bitget candidates aligned with its market-data authority."""
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.market_data.ccxt_exchange = "bitget"
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
    policy: str = "candidate_selection_utility_v1",
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
):
    from uuid import uuid4

    from aletheia.universe.candidate_opportunity import CandidateOpportunityContextV1

    candidate_id = uuid4().hex[:12]
    context = CandidateOpportunityContextV1(
        candidate_id=candidate_id,
        candidate_selection={
            "policy": policy,
            "policy_epoch": policy_epoch,
            "source_record_hash": "a" * 64,
        },
        source={
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
            "economic_utility_pct": 1.0 if policy == "candidate_selection_utility_v1" else None,
            "capability_status": "supported",
            "capability_gaps": (),
            "primary_evidence_refs": (primary_ref,),
            "secondary_mechanisms": (),
        },
        context={"regime": regime},
    )
    snapshot = UniverseSnapshot(created_at=source_at, expires_at="2099-01-01T00:00:00+00:00")
    repo.insert_universe_snapshot(snapshot)
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
        expires_at="2099-01-01T00:00:00+00:00",
        candidate_selection_policy=policy,
        candidate_selection_policy_epoch=policy_epoch,
        candidate_selection_utility_pct=(1.0 if policy == "candidate_selection_utility_v1" else None),
        opportunity_mechanism=mechanism,
        opportunity_mechanism_version=mechanism_version or f"{mechanism}/v1",
        opportunity_source_component=family,
        opportunity_source_cutoff_at=source_at,
        opportunity_evidence_hash="b" * 64,
        candidate_opportunity_context_version=context.version,
        candidate_opportunity_context_json=context.model_dump_json(),
        candidate_opportunity_context_hash=context.context_hash,
    )
    repo.insert_candidate(candidate)
    return candidate


def _no_plan_intent(
    reason_code: str = "INSUFFICIENT_EVIDENCE",
) -> PlanIntent:
    return PlanIntent(
        decision="NO_PLAN",
        reason_code=reason_code,
        thesis="",
        scenario="",
        evidence_refs=[],
        execution_mode=None,
        order_tactic=None,
        trigger_spec_id=None,
        trigger_spec_version=None,
        risk_tier="NONE",
        horizon="NONE",
    )


def _plan(generation: dict) -> EntryPlan:
    now = datetime.now(timezone.utc)
    return EntryPlan(
        planning_generation_id=generation["id"],
        candidate_id=generation["last_candidate_id"],
        candidate_family=generation["candidate_family"],
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        direction="long",
        thesis="bounded momentum continuation",
        scenario="retrace then reclaim",
        execution_mode="TRIGGERED",
        trigger_spec_id="momentum-retrace-reclaim",
        trigger_spec_version="v1",
        trigger_params={
            "breakout_level": 100,
            "pullback_low": 99,
            "pullback_high": 100,
            "entry_low": 100,
            "entry_high": 101,
            "invalidation_price": 98,
        },
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
        behavior_version="entry-planner/v1",
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
    payload = json.dumps({"frozen_candidate_id": candidate.id}, sort_keys=True)
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
    repo.insert_universe_snapshot(old_snapshot)
    old = UniverseMember(
        snapshot_id=old_snapshot.id, asset_id="asset-old", venue="bitget",
        symbol="OLD/USDT", market_type="perp", rank=1, total_score=90,
        scanner_family="momentum", scanner_direction="long", entry_price_usd=100,
        expires_at="2099-01-01T00:00:00+00:00",
    )
    repo.insert_candidate(old)
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
    assert generation["identity_version"] == "candidate-planning/v2"
    assert len(generation["entry_behavior_fingerprint"]) == 64
    assert generation["opportunity_mechanism"] == mechanism
    assert generation["candidate_selection_policy"] == "candidate_selection_utility_v1"
    assert generation["candidate_selection_policy_epoch"] == "epoch-1"


def test_mechanism_planning_identity_ignores_churn_and_rotates_authority_dimensions():
    first = _typed_candidate(mechanism="reversal", family="mean_reversion")
    generation, _ = repo.ensure_candidate_planning_generation(first.id)
    churn = _typed_candidate(
        mechanism="reversal", family="mean_reversion", rank=9, price=103.0,
        regime="sideways", risk_flags='["new-risk"]',
    )
    same, created = repo.ensure_candidate_planning_generation(churn.id)
    assert created is False
    assert same["id"] == generation["id"]

    plan_json = "{}"
    repo._insert("entry_plans", {
        "id": "mechanism-old-plan", "planning_generation_id": generation["id"],
        "candidate_id": same["last_candidate_id"], "planning_key": generation["planning_key"],
        "version": "entry-plan/v2", "venue": "bitget", "symbol": "BTC/USDT",
        "market_type": "perp", "direction": "long", "execution_mode": "RESTING_MAKER",
        "plan_json": plan_json, "plan_hash": hashlib.sha256(plan_json.encode()).hexdigest(),
        "status": "waiting", "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })

    changed = _typed_candidate(
        mechanism="positioning_forced_flow", family="derivatives"
    )
    replacement, created = repo.ensure_candidate_planning_generation(changed.id)
    assert created is True
    assert replacement["id"] != generation["id"]
    assert repo.get_candidate_planning_generation(generation["id"])["is_current"] == 0
    old_plan = repo.get_entry_plan("mechanism-old-plan")
    assert old_plan["status"] == "invalidated"
    assert old_plan["terminal_reason"] == "incompatible_planning_generation"


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
    assert "planner_contract_version" not in history[generation["id"]]
    assert history[generation["id"]]["attention_terminal_reason"] == (
        "canonical_compatibility_cutover"
    )
    if terminal_status == "planned":
        assert plan is not None
        stored_plan = repo.get_entry_plan(plan.plan_id)
        assert stored_plan["status"] == "invalidated"
        assert stored_plan["terminal_reason"] == "canonical_compatibility_cutover"


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
        {same["id"]},
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
    assert persisted_source == {
        **source,
        "planning_generation_id": generation["id"],
        "planning_identity": generation["planning_identity"],
    }


def test_timer_only_universe_ttl_slide_does_not_requalify_attention():
    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id, now=start)
    before = repo.get_candidate_planning_generation(generation["id"])

    repo.refresh_active_snapshot_ttls(candidate.snapshot_id, start + timedelta(seconds=300))

    after = repo.get_candidate_planning_generation(generation["id"])
    assert after["last_requalified_at"] is None
    assert after["requalification_source_json"] is None
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
        {generation["id"]},
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
        return _no_plan_intent("STALE_FACTS")

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
        {selected_generation["id"]},
        eligible_identity_keys=set(),
        refresh_source={"scanner_run_id": "failed"},
        soft_lease_sec=900,
        refresh_complete=False,
        now=start + timedelta(seconds=10),
    ) == {"requalified": 0, "quota_evicted": 0, "admission_lost": 0}
    assert repo.get_candidate_planning_generation(evicted_generation["id"])["attention_status"] == "active"

    counts = repo.reconcile_candidate_attention(
        {selected_generation["id"]},
        eligible_identity_keys={("bitget", "ETH/USDT", "perp", "momentum", "short")},
        refresh_source={"scanner_run_id": "complete"},
        soft_lease_sec=900,
        now=start + timedelta(seconds=20),
    )
    assert counts["quota_evicted"] == 1
    assert (
        repo.get_candidate_planning_generation(evicted_generation["id"])["attention_terminal_reason"] == "quota_evicted"
    )


def test_no_plan_is_durable_until_new_generation():
    candidate = _candidate(scanner_family="breakout")
    repo.ensure_candidate_planning_generation(candidate.id)
    calls = 0

    async def planner(_context):
        nonlocal calls
        calls += 1
        return _no_plan_intent()

    assert asyncio.run(plan_next_candidate(planner))["status"] == "no_plan"
    assert asyncio.run(plan_next_candidate(planner))["status"] == "idle"
    assert calls == 1

    replacement = _candidate(regime="sideways", scanner_family="breakout")
    next_generation, created = repo.ensure_candidate_planning_generation(replacement.id)
    assert created is True
    assert next_generation["id"] != candidate.id
    assert asyncio.run(plan_next_candidate(planner))["status"] == "no_plan"
    assert calls == 2


def test_plan_intent_no_plan_persists_reason_code(monkeypatch):
    candidate = _candidate(scanner_family="breakout")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)

    def unexpected_defer(*_args, **_kwargs):
        raise AssertionError("NO_PLAN must not defer candidate planning")

    monkeypatch.setattr(repo, "defer_candidate_planning", unexpected_defer)
    monkeypatch.setattr(repo, "defer_candidate_planning_with_code", unexpected_defer)

    async def planner(_context):
        return _no_plan_intent("STALE_FACTS")

    result = asyncio.run(plan_next_candidate(planner))
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert result["status"] == "no_plan"
    assert json.loads(stored["result_json"])["reason_code"] == "STALE_FACTS"


def test_v4_no_plan_invalid_identity_is_replaced_before_persistence(monkeypatch):
    from types import SimpleNamespace

    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    context = SimpleNamespace(
        candle_windows={"15m": {"availability": "available"}},
        anchors=(SimpleNamespace(anchor_id="candidate:anchor"),),
        entry_profiles=({"id": "grounded-anchor-resting-maker", "version": "v1"},),
        candidate={"fact_refs": [f"candidate:{candidate.id}"]},
        market_structure={"fact_refs": []}, quant_context={"fact_refs": []},
        portfolio_context={"fact_refs": []}, execution_context={"fact_refs": []},
        candidate_opportunity_context={
            "opportunity": {"primary_mechanism": "reversal", "direction": "long"},
        },
        learning_context=SimpleNamespace(evidence_refs=()),
        model_dump=lambda **_kwargs: {"context": "v4"},
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.entry_planner.build_entry_analysis_context",
        lambda *_args, **_kwargs: context,
    )

    async def planner(_context):
        return {
            "decision": "NO_PLAN", "reason_code": "CONFLICTING_SIGNALS",
            "mechanism": "continuation", "direction": "long",
            "risk_tier": "NONE", "horizon": "NONE",
        }

    result = asyncio.run(plan_next_candidate(planner))
    stored = repo.get_candidate_planning_generation(generation["id"])

    assert result["status"] == "no_plan"
    persisted = json.loads(stored["result_json"])
    assert persisted["reason_code"] == "INVALID_HYPOTHESIS_REFERENCE"
    assert persisted["source"] == "deterministic_validation"


def test_entry_planner_persists_plan_for_paper_internal():
    from aletheia.config.loader import load_config

    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    # Exercise the still-supported historical Planner V2/Entry Plan V1 path.
    repo.execute_write(
        "UPDATE candidate_planning_generations SET planner_contract_version='entry-planner/v2' WHERE id=?",
        (generation["id"],),
    )
    db = repo.get_db()
    db.execute(
        "INSERT INTO universe_feature_snapshots (id, asset_id, ts, atr_pct) VALUES (?, ?, ?, ?)",
        ("feature-btc-entry-plan", candidate.asset_id, datetime.now(timezone.utc).isoformat(), 1.5),
    )
    db.commit()

    async def planner(_context):
        return PlanIntent(
            decision="ENTRY_PLAN",
            reason_code="OK",
            thesis="bounded momentum continuation",
            scenario="retrace then reclaim",
            evidence_refs=[],
            execution_mode="TRIGGERED",
            order_tactic="OPPOSING_BBO_IOC",
            trigger_spec_id="momentum-retrace-reclaim",
            trigger_spec_version="v1",
            risk_tier="LOW",
            horizon="INTRADAY",
        )

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    result = asyncio.run(plan_next_candidate(planner, cfg=cfg))
    assert result["status"] == "planned", result
    plan = EntryPlan.model_validate_json(repo.get_entry_plan(result["entry_plan_id"])["plan_json"])
    assert plan.planning_generation_id == generation["id"]
    assert plan.execution_account_mode == "paper_internal"
    assert plan.stop_price < 100 < plan.target_price
    assert plan.trigger_data_requirements == ("quote", "trade", "books5", "candle1m")
    assert plan.max_leverage == 2


def test_compiler_bounds_risk_and_clamps_spot_leverage(monkeypatch):
    from types import SimpleNamespace

    from aletheia.config.loader import load_config
    from aletheia.trading.entry_plan_compiler import compile_entry_plan

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    policy = SimpleNamespace(
        policy_id="test-policy",
        revision=1,
        capital=SimpleNamespace(
            max_position_margin_usdt=1_000_000,
            max_total_exposure_pct=100,
        ),
        loss=SimpleNamespace(max_single_trade_risk_pct=1.0),
    )
    resolved = SimpleNamespace(policy=policy, policy_hash="test-policy-hash")
    monkeypatch.setattr(
        "aletheia.trading.entry_plan_compiler.resolve_risk_policy",
        lambda *_args, **_kwargs: resolved,
    )
    monkeypatch.setattr(
        "aletheia.trading.entry_plan_compiler.get_max_leverage",
        lambda *_args, **_kwargs: 20,
    )
    monkeypatch.setattr(
        "aletheia.risk.engine.account_equity_for_execution",
        lambda *_args, **_kwargs: 100_000.0,
    )
    monkeypatch.setattr(
        "aletheia.risk.engine.per_symbol_notional_ceiling",
        lambda *_args, **_kwargs: 1_000_000_000.0,
    )

    def compile_for(
        market_type,
        *,
        execution_mode="RESTING_MAKER",
        order_tactic="POST_ONLY_LIMIT",
        treatment=False,
    ):
        triggered = execution_mode == "TRIGGERED"
        candidate = {"entry_price_usd": 100.0}
        if treatment:
            candidate["learning_candidate_key"] = "momentum-candidate-1"
        return compile_entry_plan(
            {
                "id": f"generation-{market_type}-{execution_mode}-{order_tactic}",
                "last_candidate_id": f"candidate-{market_type}",
                "candidate_family": "momentum",
                "venue": "bitget",
                "symbol": "BTC/USDT",
                "market_type": market_type,
                "direction": "long",
            },
            candidate,
            {"atr_pct": 10.0},
            PlanIntent(
                decision="ENTRY_PLAN",
                reason_code="OK",
                thesis="x",
                scenario="y",
                evidence_refs=[],
                execution_mode=execution_mode,
                order_tactic=order_tactic,
                trigger_spec_id="momentum-retrace-reclaim" if triggered else None,
                trigger_spec_version="v1" if triggered else None,
                risk_tier="LOW",
                horizon="INTRADAY",
            ),
            cfg,
            candles=[{}] if treatment else None,
        )

    perp = compile_for("perp")
    spot = compile_for("spot")
    triggered_maker = compile_for(
        "perp",
        execution_mode="TRIGGERED",
        order_tactic="POST_ONLY_LIMIT",
    )
    triggered_taker = compile_for(
        "perp",
        execution_mode="TRIGGERED",
        order_tactic="OPPOSING_BBO_IOC",
    )
    worst_entry = perp.entry_zone[1]
    risk_budget = 100_000.0 * perp.max_risk_pct / 100.0
    stop_fraction = abs(worst_entry - perp.stop_price) / worst_entry
    assert perp.max_notional <= risk_budget / stop_fraction
    assert spot.max_leverage == 1
    assert perp.order_tactic == "POST_ONLY_LIMIT"
    assert perp.max_wait_sec == 60
    assert triggered_maker.execution_mode == "TRIGGERED"
    assert triggered_maker.order_tactic == "POST_ONLY_LIMIT"
    assert triggered_maker.max_wait_sec == 60
    assert triggered_maker.trigger_spec_id == "momentum-retrace-reclaim"
    assert triggered_taker.order_tactic == "OPPOSING_BBO_IOC"
    assert triggered_taker.max_wait_sec == 0
    assert triggered_taker.bbo_reference == "ask"
    assert triggered_taker.max_slippage_bps == 20.0
    assert triggered_taker.worst_price == pytest.approx(100.2)
    trigger_params = {
        "breakout_level": 100.0,
        "pullback_low": 99.0,
        "pullback_high": 100.0,
        "entry_low": 100.0,
        "entry_high": 101.0,
        "invalidation_price": 98.0,
        "sweep_low": 99.0,
        "sweep_high": 100.0,
        "atr_abs": 2.0,
        "geometry_version": "momentum-entry-geometry/v2",
        "geometry_fingerprint": "a" * 64,
    }
    geometry = SimpleNamespace(
        entry_zone=(100.0, 101.0),
        stop_price=98.0,
        target_price=106.0,
        trigger_params=lambda: trigger_params,
    )
    monkeypatch.setattr(
        "aletheia.trading.entry_plan_compiler.compile_momentum_entry_geometry_v2",
        lambda **_kwargs: geometry,
    )
    monkeypatch.setattr(
        "aletheia.learning.experiments.apply_numeric_treatment",
        lambda **_kwargs: SimpleNamespace(value=1.0, exposure_id="exposure-treatment"),
    )
    treatment_plan = compile_for(
        "perp",
        execution_mode="TRIGGERED",
        order_tactic="OPPOSING_BBO_IOC",
        treatment=True,
    )
    assert treatment_plan.trigger_spec_version == "v2"
    assert treatment_plan.treatment_exposure_id == "exposure-treatment"
    assert treatment_plan.max_risk_pct == triggered_taker.max_risk_pct
    assert treatment_plan.max_leverage == triggered_taker.max_leverage


def test_ai_entry_compiler_emits_v2_without_momentum_treatment(monkeypatch):
    from types import SimpleNamespace
    from aletheia.config.loader import load_config
    from aletheia.trading.entry_analysis import EntryAnalysisContextV1, EntryAnchorV1, EntryHypothesisV1
    from aletheia.trading.entry_plan_compiler import compile_ai_entry_plan

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    policy = SimpleNamespace(policy_id="test", revision=1, capital=SimpleNamespace(max_position_margin_usdt=100_000, max_total_exposure_pct=100), loss=SimpleNamespace(max_single_trade_risk_pct=1.0))
    monkeypatch.setattr("aletheia.trading.entry_plan_compiler.resolve_risk_policy", lambda *_a, **_k: SimpleNamespace(policy=policy, policy_hash="hash"))
    monkeypatch.setattr("aletheia.trading.entry_plan_compiler.get_max_leverage", lambda *_a, **_k: 20)
    monkeypatch.setattr("aletheia.risk.engine.account_equity_for_execution", lambda *_a, **_k: 100_000.0)
    monkeypatch.setattr("aletheia.risk.engine.per_symbol_notional_ceiling", lambda *_a, **_k: 1_000_000.0)
    primary = EntryAnchorV1(venue="bitget", symbol="BTC/USDT", market_type="perp", kind="candidate_reference", price=100, directional_role="entry_candidate", known_at_ms=1, source_fact_refs=("candidate:c1",), source_fingerprint="candidate")
    invalidation = EntryAnchorV1(venue="bitget", symbol="BTC/USDT", market_type="perp", kind="confirmed_swing", price=95, directional_role="invalidation_candidate", known_at_ms=1, source_fact_refs=("swing:s1",), source_fingerprint="swing")
    context = EntryAnalysisContextV1(
        analysis_cutoff_at="2026-09-02T00:00:00Z", instrument={"venue":"bitget","symbol":"BTC/USDT","market_type":"perp","direction":"long"}, planning_generation={"id":"g1","planner_contract_version":"entry-planner/v3","candidate_id":"c1","candidate_family":"momentum"}, candidate={"candidate_price":100,"fact_refs":["candidate:c1"]}, candle_windows={"4h":{"availability":"unavailable","bars":[]},"1h":{"availability":"unavailable","bars":[]},"15m":{"availability":"available","bars":[{} for _ in range(96)]}}, market_structure={"15m":{"atr14":2.0,"break_tolerance":0.1},"fact_refs":[]}, quant_context={"values":{"atr_pct":2},"fact_refs":[]}, portfolio_context={"availability":"unavailable"}, execution_context={"availability":"available","bid":99.9,"ask":100.1,"spread_bps":2,"depth_10bps_usd":100_000,"estimated_costs":0.1}, anchors=(primary,invalidation), entry_profiles=({"id":"anchor-retrace-reclaim","version":"v1"},), learning_context={"availability":"unavailable"},
    )
    hypothesis = EntryHypothesisV1(decision="ENTRY_PLAN", reason_code="OK", thesis="bounded", scenario="reclaim", setup_archetype="continuation", primary_anchor_ref=primary.anchor_id, invalidation_anchor_ref=invalidation.anchor_id, entry_profile_id="anchor-retrace-reclaim", entry_profile_version="v1", execution_urgency="BOUNDED_URGENT", risk_tier="LOW", horizon="INTRADAY", supporting_fact_refs=(primary.anchor_id,))
    plan = compile_ai_entry_plan({"id":"g1","last_candidate_id":"candidate-b","candidate_family":"momentum","venue":"bitget","symbol":"BTC/USDT","market_type":"perp","direction":"long"}, context, hypothesis, cfg)
    assert plan.candidate_id == "c1"
    assert (plan.version, plan.behavior_version, plan.treatment_exposure_id) == ("entry-plan/v2", "ai-entry-hypothesis-compiler/v1", None)
    assert plan.trigger_spec_id == "anchor-retrace-reclaim"


def test_mechanism_aware_compiler_preserves_upstream_identity_without_numeric_ai_authority(monkeypatch):
    from types import SimpleNamespace

    from aletheia.config.loader import load_config
    from aletheia.learning.entry_context import empty_entry_learning_context
    from aletheia.trading.entry_analysis import (
        EntryAnalysisContext, EntryAnchor, EntryHypothesis,
        canonical_json, fingerprint,
    )
    from aletheia.trading.entry_contract import entry_prompt_fingerprint
    from aletheia.trading.entry_plan_compiler import compile_entry_plan

    cfg = load_config()
    cfg.agent.permission_mode = "Full"
    cfg.exchange.paper_execution_mode = "paper_internal"
    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    assert repo.claim_next_candidate_planning("v4-owner")["id"] == generation["id"]
    policy = SimpleNamespace(
        policy_id="test", revision=1,
        capital=SimpleNamespace(max_position_margin_usdt=100_000, max_total_exposure_pct=100),
        loss=SimpleNamespace(max_single_trade_risk_pct=1.0),
    )
    monkeypatch.setattr(
        "aletheia.trading.entry_plan_compiler.resolve_risk_policy",
        lambda *_a, **_k: SimpleNamespace(policy=policy, policy_hash="hash"),
    )
    monkeypatch.setattr("aletheia.trading.entry_plan_compiler.get_max_leverage", lambda *_a, **_k: 20)
    monkeypatch.setattr("aletheia.risk.engine.account_equity_for_execution", lambda *_a, **_k: 100_000.0)
    monkeypatch.setattr("aletheia.risk.engine.per_symbol_notional_ceiling", lambda *_a, **_k: 1_000_000.0)
    primary = EntryAnchor(
        venue="bitget", symbol="BTC/USDT", market_type="perp", kind="candidate_reference",
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
        entry_profiles=({"id": "grounded-anchor-resting-maker", "version": "v1"},),
        learning_context=empty_entry_learning_context(
            as_of_at="2026-09-03T00:00:00+00:00", mechanism="reversal", direction="long",
            market_type="perp", policy="candidate_selection_utility_v1", policy_epoch="epoch-1",
        ),
    )
    hypothesis = EntryHypothesis(
        decision="ENTRY_PLAN", reason_code="OK", mechanism="reversal", direction="long",
        primary_anchor_ref=primary.anchor_id, invalidation_anchor_ref=invalidation.anchor_id,
        entry_profile_id="grounded-anchor-resting-maker", entry_profile_version="v1",
        execution_urgency="PASSIVE", risk_tier="LOW", horizon="INTRADAY",
        supporting_fact_refs=(invalidation.anchor_id,),
    )
    input_payload = canonical_json(context.model_dump(mode="json"))
    assert repo.bind_candidate_planner_input(
        generation["id"], "v4-owner", input_payload,
        fingerprint(context.model_dump(mode="json")),
    )
    plan = compile_entry_plan(generation, context, hypothesis, cfg)

    assert plan.entry_hypothesis_version == "entry-hypothesis/v2"
    assert plan.analysis_context_version == "entry-analysis-context/v2"
    assert plan.entry_profile_id == "grounded-anchor-resting-maker"
    assert plan.behavior_version == generation["entry_behavior_fingerprint"]
    assert plan.thesis == "reversal grounded entry"
    assert plan.required_permission == "Paper"
    assert plan.execution_account_mode == "paper_internal"
    assert repo.complete_candidate_planning_with_plan(
        generation["id"], "v4-owner", plan,
    )[0] == "planned"
    monkeypatch.setattr("aletheia.config.loader.load_config", lambda **_kwargs: cfg)
    from aletheia.execution.entry_plan_execution import _require_ai_entry_authority

    _require_ai_entry_authority(plan)


def test_ai_entry_v3_frozen_lineage_reaches_attributed_proposal(monkeypatch):
    from types import SimpleNamespace

    from aletheia.config.loader import load_config, save_config
    from aletheia.data.symbols import InstrumentId
    from aletheia.execution.entry_plan_execution import build_entry_plan_order_intent
    from aletheia.trading.entry_analysis import (
        EntryAnalysisContextV1, EntryAnchorV1, EntryHypothesisV1, canonical_json, fingerprint,
    )
    from aletheia.trading.entry_plan_compiler import compile_ai_entry_plan
    from aletheia.trading.momentum_trigger import MomentumTriggerEngineV2
    from aletheia.universe.fast_stream import FastMarketEvent
    from aletheia.universe.fast_trigger import TriggerPolicy

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.live.state = "live_disabled"
    save_config(cfg)
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    assert generation["planner_contract_version"] == "entry-planner/v3"
    assert repo.claim_next_candidate_planning("v3-owner")["id"] == generation["id"]

    primary = EntryAnchorV1(
        venue="bitget", symbol="BTC/USDT", market_type="perp", kind="candidate_reference",
        price=100, directional_role="entry_candidate", known_at_ms=1,
        source_fact_refs=(f"candidate:{candidate.id}",), source_fingerprint="candidate",
    )
    invalidation = EntryAnchorV1(
        venue="bitget", symbol="BTC/USDT", market_type="perp", kind="confirmed_swing",
        price=95, directional_role="invalidation_candidate", known_at_ms=1,
        source_fact_refs=("swing:s1",), source_fingerprint="swing",
    )
    context = EntryAnalysisContextV1(
        analysis_cutoff_at=datetime.now(timezone.utc).isoformat(),
        instrument={"venue": "bitget", "symbol": "BTC/USDT", "market_type": "perp", "direction": "long"},
        planning_generation={"id": generation["id"], "planner_contract_version": "entry-planner/v3", "candidate_id": candidate.id, "candidate_family": "momentum"},
        candidate={"candidate_price": 100, "fact_refs": [f"candidate:{candidate.id}"]},
        candle_windows={"4h": {"availability": "unavailable", "bars": []}, "1h": {"availability": "unavailable", "bars": []}, "15m": {"availability": "available", "bars": [{} for _ in range(96)]}},
        market_structure={"15m": {"atr14": 2.0, "break_tolerance": 0.1}, "fact_refs": []},
        quant_context={"values": {"atr_pct": 2.0}, "fact_refs": []},
        portfolio_context={"availability": "available", "fact_refs": []},
        execution_context={"availability": "available", "bid": 99.9, "ask": 100.1, "spread_bps": 2, "depth_10bps_usd": 100_000, "estimated_costs": 0.1, "fact_refs": []},
        anchors=(primary, invalidation),
        entry_profiles=({"id": "anchor-retrace-reclaim", "version": "v1"},),
        learning_context={"availability": "unavailable"},
    )
    input_payload = canonical_json(context.model_dump(mode="json"))
    assert repo.bind_candidate_planner_input(
        generation["id"], "v3-owner", input_payload,
        fingerprint(context.model_dump(mode="json")),
    )
    hypothesis = EntryHypothesisV1(
        decision="ENTRY_PLAN", reason_code="OK", thesis="bounded", scenario="reclaim",
        setup_archetype="continuation", primary_anchor_ref=primary.anchor_id,
        invalidation_anchor_ref=invalidation.anchor_id,
        entry_profile_id="anchor-retrace-reclaim", entry_profile_version="v1",
        execution_urgency="BOUNDED_URGENT", risk_tier="LOW", horizon="INTRADAY",
        supporting_fact_refs=(primary.anchor_id,),
    )
    policy = SimpleNamespace(
        policy_id="test", revision=1,
        capital=SimpleNamespace(max_position_margin_usdt=100_000, max_total_exposure_pct=100),
        loss=SimpleNamespace(max_single_trade_risk_pct=1.0),
    )
    monkeypatch.setattr("aletheia.trading.entry_plan_compiler.resolve_risk_policy", lambda *_a, **_k: SimpleNamespace(policy=policy, policy_hash="hash"))
    monkeypatch.setattr("aletheia.trading.entry_plan_compiler.get_max_leverage", lambda *_a, **_k: 20)
    monkeypatch.setattr("aletheia.risk.engine.account_equity_for_execution", lambda *_a, **_k: 100_000.0)
    monkeypatch.setattr("aletheia.risk.engine.per_symbol_notional_ceiling", lambda *_a, **_k: 1_000_000.0)
    plan = compile_ai_entry_plan(generation, context, hypothesis, cfg)
    assert repo.complete_candidate_planning_with_plan(
        generation["id"], "v3-owner", plan,
    )[0] == "planned"
    stored = EntryPlan.model_validate_json(repo.get_entry_plan(plan.plan_id)["plan_json"])

    params = stored.trigger_params
    engine = MomentumTriggerEngineV2(
        instrument=InstrumentId.from_parts("bitget", "BTC/USDT", "perp"),
        direction="long", breakout_level=params["breakout_level"],
        pullback_band=(params["pullback_low"], params["pullback_high"]),
        sweep_band=(params["sweep_low"], params["sweep_high"]),
        entry_zone=(params["entry_low"], params["entry_high"]),
        invalidation_price=params["invalidation_price"], atr_abs=params["atr_abs"],
        geometry_version=params["geometry_version"], geometry_fingerprint=params["geometry_fingerprint"],
        policy=TriggerPolicy(max_spread_bps=50, min_depth_10bps_usd=100),
        trigger_spec_id=stored.trigger_spec_id, trigger_spec_version=stored.trigger_spec_version,
    )
    engine.confirm_subscription()
    instrument = engine.instrument
    event = lambda kind, at, sequence, **values: FastMarketEvent(
        event_type=kind, instrument=instrument, exchange_timestamp_ms=at,
        received_timestamp_ms=at, sequence=sequence, **values,
    )
    events = (
        event("quote", 1_000, 1, bid=99.9, ask=99.95),
        event("depth", 1_050, 1, bids=((100.1, 10.0),), asks=((100.2, 10.0),)),
        event("trade", 1_100, 1, price=100.0, size=10.0, side="buy"),
        event("trade", 1_250, 2, price=100.0, size=4.0, side="sell"),
        event("quote", 1_200, 2, bid=99.8, ask=99.9),
        event("quote", 1_500, 3, bid=100.05, ask=100.15),
        event("quote", 1_900, 4, bid=100.1, ask=100.2),
    )
    ready = None
    for item in events:
        ready = engine.observe(item) or ready
    observed = datetime.now(timezone.utc)
    proposal = build_entry_plan_order_intent(
        stored, bid=ready.bid, ask=ready.ask, observed_at=observed.isoformat(),
        source_observed_at=observed.isoformat(), trigger_evidence=ready.entry_trigger,
        now=observed,
    )
    attribution = json.loads(proposal.attribution_snapshot)
    assert attribution["entry_plan_version"] == "entry-plan/v2"
    assert attribution["entry_behavior_version"] == "ai-entry-hypothesis-compiler/v1"
    assert attribution["analysis_context_fingerprint"] == context.context_fingerprint
    assert attribution["entry_trigger"]["trigger_spec_id"] == "anchor-retrace-reclaim"


def test_plan_intent_portable_schema_requires_every_strict_property():
    schema = portable_json_schema_payload(_PlanIntentWireSchema)
    properties = schema["properties"]
    assert set(schema["required"]) == set(properties)
    assert set(properties) == set(PlanIntent.model_fields)
    assert schema["additionalProperties"] is False
    assert properties["order_tactic"]["type"] == ["string", "null"]
    encoded = json.dumps(schema, sort_keys=True)
    assert "$ref" not in encoded
    assert "$defs" not in encoded
    assert "anyOf" not in encoded


def test_plan_intent_wire_schema_canonicalizes_no_plan_but_rejects_invalid_entry():
    no_plan = {
        **_no_plan_intent("CONFLICTING_SIGNALS").model_dump(mode="json"),
        "execution_mode": "RESTING_MAKER",
        "order_tactic": "POST_ONLY_LIMIT",
    }
    assert _PlanIntentWireSchema.model_validate(no_plan) == _no_plan_intent("CONFLICTING_SIGNALS")
    assert no_plan["execution_mode"] is None
    assert no_plan["order_tactic"] is None

    invalid_entry = {
        **no_plan,
        "decision": "ENTRY_PLAN",
        "reason_code": "OK",
        "thesis": "x",
        "scenario": "y",
        "execution_mode": "RESTING_MAKER",
        "order_tactic": "OPPOSING_BBO_IOC",
        "risk_tier": "LOW",
        "horizon": "INTRADAY",
    }
    with pytest.raises(ValueError, match="RESTING_MAKER_TACTIC_INVALID"):
        _PlanIntentWireSchema.model_validate(invalid_entry)


@pytest.mark.parametrize(
    ("execution_mode", "order_tactic", "triggered"),
    [
        ("RESTING_MAKER", "POST_ONLY_LIMIT", False),
        ("TRIGGERED", "POST_ONLY_LIMIT", True),
        ("TRIGGERED", "OPPOSING_BBO_IOC", True),
    ],
)
def test_plan_intent_accepts_only_canonical_tactic_matrix(
    execution_mode,
    order_tactic,
    triggered,
):
    intent = PlanIntent(
        decision="ENTRY_PLAN",
        reason_code="OK",
        thesis="x",
        scenario="y",
        evidence_refs=[],
        execution_mode=execution_mode,
        order_tactic=order_tactic,
        trigger_spec_id="momentum-retrace-reclaim" if triggered else None,
        trigger_spec_version="v1" if triggered else None,
        risk_tier="LOW",
        horizon="INTRADAY",
    )
    assert intent.order_tactic == order_tactic


def test_plan_intent_rejects_resting_taker():
    with pytest.raises(ValueError, match="RESTING_MAKER_TACTIC_INVALID"):
        PlanIntent(
            decision="ENTRY_PLAN",
            reason_code="OK",
            thesis="x",
            scenario="y",
            evidence_refs=[],
            execution_mode="RESTING_MAKER",
            order_tactic="OPPOSING_BBO_IOC",
            trigger_spec_id=None,
            trigger_spec_version=None,
            risk_tier="LOW",
            horizon="INTRADAY",
        )


def test_unsupported_candidate_family_trigger_terminalizes_once():
    candidate = _candidate(scanner_family="value")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    repo.execute_write(
        "INSERT INTO universe_feature_snapshots (id, asset_id, ts, atr_pct) VALUES (?, ?, ?, ?)",
        (
            "feature-unsupported-family",
            candidate.asset_id,
            datetime.now(timezone.utc).isoformat(),
            1.5,
        ),
    )
    calls = 0

    async def planner(context):
        nonlocal calls
        calls += 1
        assert context["compatible_trigger_specs"] == []
        return PlanIntent(
            decision="ENTRY_PLAN",
            reason_code="OK",
            thesis="x",
            scenario="y",
            evidence_refs=[],
            execution_mode="TRIGGERED",
            order_tactic="POST_ONLY_LIMIT",
            trigger_spec_id="momentum-retrace-reclaim",
            trigger_spec_version="v1",
            risk_tier="LOW",
            horizon="INTRADAY",
        )

    first = asyncio.run(plan_next_candidate(planner))
    second = asyncio.run(plan_next_candidate(planner))
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert first["status"] == "no_plan"
    assert second["status"] == "idle"
    assert calls == 1
    assert stored["attempt_count"] == 1
    assert json.loads(stored["result_json"])["reason_code"] == "NO_COMPATIBLE_TRIGGER_SPEC"


def test_missing_trigger_identity_terminalizes_after_one_physical_call(monkeypatch):
    candidate = _candidate(scanner_family="breakout")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    calls = 0

    async def invalid_structured(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "decision": "ENTRY_PLAN",
            "reason_code": "OK",
            "thesis": "x",
            "scenario": "y",
            "evidence_refs": [],
            "execution_mode": "TRIGGERED",
            "order_tactic": "POST_ONLY_LIMIT",
            "trigger_spec_id": None,
            "trigger_spec_version": None,
            "risk_tier": "LOW",
            "horizon": "INTRADAY",
        }, None

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", invalid_structured)
    from aletheia.agent_shell.entry_planner import system_operator_entry_planner

    first = asyncio.run(plan_next_candidate(system_operator_entry_planner))
    second = asyncio.run(plan_next_candidate(system_operator_entry_planner))
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert first["status"] == "no_plan"
    assert second["status"] == "idle"
    assert calls == 1
    assert stored["attempt_count"] == 1
    assert json.loads(stored["result_json"])["reason_code"] == "TRIGGER_SPEC_NOT_REGISTERED"


def test_legacy_venue_mismatch_consumes_no_model_call():
    from aletheia.config.loader import load_config

    candidate = _candidate(venue="binance")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    calls = 0

    async def planner(_context):
        nonlocal calls
        calls += 1
        return _no_plan_intent()

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    result = asyncio.run(plan_next_candidate(planner, cfg=cfg))
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert result["status"] == "no_plan"
    assert result["model_invoked"] is False
    assert calls == 0
    assert json.loads(stored["result_json"])["reason_code"].startswith("EXECUTION_VENUE_UNAVAILABLE:")


def test_binance_testnet_spot_candidate_consumes_no_model_call():
    from aletheia.config.loader import load_config
    from aletheia.universe.fast_stream import fast_market_data_unavailability_reason

    candidate = _candidate(venue="binance", market_type="spot")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    calls = 0

    async def planner(_context):
        nonlocal calls
        calls += 1
        return _no_plan_intent()

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "binance_testnet"
    result = asyncio.run(plan_next_candidate(planner, cfg=cfg))
    stored = repo.get_candidate_planning_generation(generation["id"])

    assert result["status"] == "no_plan"
    assert result["model_invoked"] is False
    assert calls == 0
    assert json.loads(stored["result_json"])["reason_code"].startswith(
        "EXECUTION_VENUE_UNAVAILABLE:"
    )
    assert fast_market_data_unavailability_reason(cfg, "binance", "perp") is None
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.market_data.ccxt_exchange = "binance"
    assert fast_market_data_unavailability_reason(cfg, "binance", "spot") is None


def test_binance_testnet_perp_candidate_reaches_v3_planner(monkeypatch):
    from aletheia.config.loader import load_config

    candidate = _candidate(venue="binance", market_type="perp")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    calls = 0

    class AvailableContext:
        candle_windows = {"15m": {"availability": "available"}}
        anchors = (object(),)

        def model_dump(self, **_kwargs):
            return {"candidate_id": candidate.id}

    monkeypatch.setattr(
        "aletheia.agent_shell.entry_planner.build_entry_analysis_context",
        lambda *_args, **_kwargs: AvailableContext(),
    )

    async def planner(_context):
        nonlocal calls
        calls += 1
        return {
            "decision": "NO_PLAN",
            "reason_code": "INSUFFICIENT_EVIDENCE",
            "thesis": "",
            "scenario": "",
            "risk_tier": "NONE",
            "horizon": "NONE",
        }

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "binance_testnet"
    cfg.live.state = "live_disabled"
    result = asyncio.run(plan_next_candidate(planner, cfg=cfg))

    assert generation["planner_contract_version"] == "entry-planner/v3"
    assert result["status"] == "no_plan"
    assert result["model_invoked"] is True
    assert calls == 1


def test_system_operator_uses_deep_alias_none_reasoning_and_tactic_prompt(monkeypatch):
    captured = {}

    async def structured(system, _user, **kwargs):
        captured.update({"system": system, **kwargs})
        return _no_plan_intent().model_dump(mode="json"), None

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", structured)
    from aletheia.agent_shell.entry_planner import system_operator_entry_planner

    result = asyncio.run(
        system_operator_entry_planner(
            {"candidate": {"symbol": "BTC/USDT"}},
            "deep-paid",
        )
    )
    assert result == _no_plan_intent()
    assert captured["provider"] == "deep-paid"
    assert captured["reasoning_effort"] == "none"
    assert captured["schema"] is _PlanIntentWireSchema
    assert captured["caller"] == "agent_shell.entry_planner"
    assert "TRIGGERED + POST_ONLY_LIMIT" in captured["system"]
    assert "TRIGGERED + OPPOSING_BBO_IOC" in captured["system"]


def test_system_operator_v2_uses_distinct_prompt_identity(monkeypatch):
    from aletheia.trading.entry_analysis import EntryHypothesisV2

    captured = {}

    async def structured(_system, _user, **kwargs):
        captured.update(kwargs)
        return {
            "decision": "NO_PLAN", "reason_code": "CONFLICTING_SIGNALS",
            "mechanism": "reversal", "direction": "long",
            "risk_tier": "NONE", "horizon": "NONE",
        }, None

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", structured)
    from aletheia.agent_shell.entry_planner import system_operator_entry_planner

    result = asyncio.run(system_operator_entry_planner({
        "context_version": "entry-analysis-context/v2",
        "planning_generation": {"id": "generation-v2"},
        "candidate": {"symbol": "BTC/USDT"},
    }, "deep-paid"))

    assert result.decision == "NO_PLAN"
    assert captured["caller"] == "agent_shell.entry_planner.v2"
    assert captured["schema"] is EntryHypothesisV2


def test_system_operator_canonicalizes_no_plan_execution_noise(monkeypatch):
    async def structured(*_args, **_kwargs):
        return {
            **_no_plan_intent("CONFLICTING_SIGNALS").model_dump(mode="json"),
            "execution_mode": "RESTING_MAKER",
            "order_tactic": "POST_ONLY_LIMIT",
            "risk_tier": "LOW",
            "horizon": "INTRADAY",
        }, None

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", structured)
    from aletheia.agent_shell.entry_planner import system_operator_entry_planner

    result = asyncio.run(system_operator_entry_planner({"candidate": {"symbol": "ETH/USDT"}}))
    assert result == _no_plan_intent("CONFLICTING_SIGNALS")


def test_planner_empty_content_records_planner_empty_content_reason(monkeypatch):
    candidate = _candidate(scanner_family="breakout")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)

    async def empty_structured(*_args, **_kwargs):
        return None, "structured_empty_content"

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", empty_structured)
    from aletheia.agent_shell.entry_planner import system_operator_entry_planner

    result = asyncio.run(plan_next_candidate(system_operator_entry_planner))
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert result["status"] == "unavailable"
    assert json.loads(stored["result_json"])["reason"].startswith("PLANNER_EMPTY_CONTENT")


def test_planner_schema_invalid_records_planner_schema_invalid_reason(monkeypatch):
    candidate = _candidate(scanner_family="breakout")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)

    async def invalid_structured(*_args, **_kwargs):
        return None, "structured_schema_invalid: literal_error at decision"

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", invalid_structured)
    from aletheia.agent_shell.entry_planner import system_operator_entry_planner

    result = asyncio.run(plan_next_candidate(system_operator_entry_planner))
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert result["status"] == "unavailable"
    assert json.loads(stored["result_json"])["reason"].startswith("PLANNER_SCHEMA_INVALID:")


def test_entry_planner_batch_uses_bounded_parallel_workers(monkeypatch):
    active = 0
    peak = 0
    calls = 0
    provider_names = []

    async def fake_run_once(cfg=None, *, provider_name=None):
        nonlocal active, peak, calls
        calls += 1
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        provider_names.append(provider_name)
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
        lambda _tier: "deep-paid",
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


def test_entry_planner_batch_claims_rank_order_before_parallel_planning(monkeypatch):
    candidates = [
        _candidate(
            rank=rank,
            asset_id=f"asset-{rank}",
            symbol=f"RANK{rank}/USDT",
            scanner_family="breakout",
        )
        for rank in (4, 1, 3, 2)
    ]
    generations = {
        candidate.rank: repo.ensure_candidate_planning_generation(candidate.id)[0]["id"] for candidate in candidates
    }
    started: list[str] = []
    active = 0
    peak = 0
    all_started = asyncio.Event()

    async def planner(context, _provider_name=None):
        nonlocal active, peak
        started.append(context["planning_generation"]["id"])
        active += 1
        peak = max(peak, active)
        if active == 4:
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=1)
        active -= 1
        return _no_plan_intent()

    monkeypatch.setattr(
        "aletheia.agent_shell.entry_planner.system_operator_entry_planner",
        planner,
    )

    result = asyncio.run(run_entry_planner_batch())

    assert started == [generations[rank] for rank in (1, 2, 3, 4)]
    assert peak == 4
    assert result["planning_attempts"] == 4
    assert result["pending_queue_remaining"] == 0


def test_stale_llm_result_cannot_activate_entry_plan():
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    assert repo.claim_next_candidate_planning("slow-owner") is not None

    replacement = _candidate(regime="sideways")
    repo.ensure_candidate_planning_generation(replacement.id)

    plan = _plan(generation)
    status, record = repo.complete_candidate_planning_with_plan(generation["id"], "slow-owner", plan)
    assert status == "stale"
    assert record is None
    assert repo.get_entry_plan(plan.plan_id) is None


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


def test_local_broker_backpressure_does_not_consume_attempt():
    candidate = _candidate(scanner_family="breakout")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)

    async def refused(_context):
        raise PlannerBackpressure("inference_queue_full")

    result = asyncio.run(plan_next_candidate(refused, owner_token="pressure-owner"))
    stored = repo.get_candidate_planning_generation(generation["id"])

    assert result["status"] == "backpressure"
    assert result["model_invoked"] is False
    assert stored["attempt_count"] == 0
    assert stored["status"] == "unavailable"
    assert stored["attention_status"] == "active"


def test_entry_planner_batch_cancels_and_awaits_siblings_on_fatal_failure(monkeypatch):
    started = 0
    all_started = asyncio.Event()
    cancelled = 0

    async def worker(cfg=None, *, provider_name=None):
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


def test_compile_validation_is_terminal_after_one_attempt(monkeypatch):
    from aletheia.config.loader import load_config

    candidate = _candidate(scanner_family="value")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    repo.execute_write(
        "INSERT INTO universe_feature_snapshots (id, asset_id, ts, atr_pct) VALUES (?, ?, ?, ?)",
        ("feature-compile-invalid", candidate.asset_id, datetime.now(timezone.utc).isoformat(), 1.5),
    )

    async def planner(_context):
        return PlanIntent(
            decision="ENTRY_PLAN",
            reason_code="OK",
            thesis="x",
            scenario="y",
            evidence_refs=[],
            execution_mode="RESTING_MAKER",
            order_tactic="POST_ONLY_LIMIT",
            trigger_spec_id=None,
            trigger_spec_version=None,
            risk_tier="LOW",
            horizon="INTRADAY",
        )

    def invalid_compile(*_args, **_kwargs):
        raise ValueError("stop_reference_invalid:unsafe detail is not persisted")

    monkeypatch.setattr(
        "aletheia.trading.entry_plan_compiler.compile_entry_plan", invalid_compile
    )
    cfg = load_config()
    first = asyncio.run(plan_next_candidate(planner, cfg=cfg))
    second = asyncio.run(plan_next_candidate(planner, cfg=cfg))
    stored = repo.get_candidate_planning_generation(generation["id"])

    assert first["status"] == "unavailable"
    assert second["status"] == "idle"
    assert stored["attempt_count"] == 1
    assert stored["attention_status"] == "terminal"
    assert json.loads(stored["result_json"])["reason"] == (
        "ENTRY_PLAN_COMPILE_INVALID:stop_reference_invalid"
    )


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
        _no_plan_intent().model_dump(mode="json"),
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


def test_incompatible_generation_explicitly_supersedes_active_plan():
    candidate = _candidate(regime="trending")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    repo.claim_next_candidate_planning("owner")
    plan = _plan(generation)
    repo.complete_candidate_planning_with_plan(generation["id"], "owner", plan)

    replacement = _candidate(regime="sideways", rank=6, price=103)
    next_generation, created = repo.ensure_candidate_planning_generation(replacement.id)
    assert created is True
    assert next_generation["id"] != generation["id"]
    prior_generation = repo.get_candidate_planning_generation(generation["id"])
    assert prior_generation["attention_status"] == "terminal"
    assert prior_generation["attention_terminal_reason"] == "planning_generation_superseded"
    stored = repo.get_entry_plan(plan.plan_id)
    assert stored["status"] == "invalidated"
    assert stored["terminal_reason"] == "incompatible_planning_generation"


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
    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    generations = []
    for index, symbol in enumerate(("BTC/USDT", "ETH/USDT", "SOL/USDT")):
        candidate = _candidate(asset_id=f"asset-{index}", symbol=symbol)
        generation, _ = repo.ensure_candidate_planning_generation(
            candidate.id,
            now=start + timedelta(seconds=index),
        )
        generations.append(generation)

    first_page = repo.list_planning_history(limit=2)
    cursor = (first_page[-1]["updated_at"], first_page[-1]["id"])
    second_page = repo.list_planning_history(limit=2, before=cursor)
    assert len(first_page) == 2
    assert len(second_page) == 1
    assert {row["id"] for row in first_page}.isdisjoint({row["id"] for row in second_page})


def test_expired_attention_projects_consistently_without_sweep_or_mutation():
    from aletheia.server.routes.universe import get_candidate

    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id, now=start)
    after_hard_deadline = start + timedelta(hours=2)

    assert repo.list_active_candidate_attention() == []
    detail = get_candidate(candidate.id)["attention"]
    [history] = repo.list_planning_history(now=after_hard_deadline)

    assert detail["attention_status"] == history["attention_status"] == "terminal"
    assert detail["attention_terminal_reason"] == "max_attention_age"
    assert history["attention_terminal_reason"] == "max_attention_age"
    persisted = repo.get_candidate_planning_generation(generation["id"])
    assert persisted["attention_status"] == "active"
    assert persisted["attention_terminal_reason"] is None


# These checks bind to the numeric fork, its untyped fixtures, or its removed
# compiler signatures. Keep their bodies readable during the schema compatibility
# window; current equivalents live in test_entry_lifecycle_cutover.py.
_SUPERSEDED_BY_CANONICAL_ENTRY_LIFECYCLE = (
    "test_mechanism_planning_identity_ignores_churn_and_rotates_authority_dimensions",
    "test_no_plan_is_durable_until_new_generation",
    "test_plan_intent_no_plan_persists_reason_code",
    "test_v4_no_plan_invalid_identity_is_replaced_before_persistence",
    "test_entry_planner_persists_plan_for_paper_internal",
    "test_compiler_bounds_risk_and_clamps_spot_leverage",
    "test_ai_entry_compiler_emits_v2_without_momentum_treatment",
    "test_ai_entry_v3_frozen_lineage_reaches_attributed_proposal",
    "test_plan_intent_wire_schema_canonicalizes_no_plan_but_rejects_invalid_entry",
    "test_unsupported_candidate_family_trigger_terminalizes_once",
    "test_missing_trigger_identity_terminalizes_after_one_physical_call",
    "test_legacy_venue_mismatch_consumes_no_model_call",
    "test_binance_testnet_spot_candidate_consumes_no_model_call",
    "test_binance_testnet_perp_candidate_reaches_v3_planner",
    "test_system_operator_uses_deep_alias_none_reasoning_and_tactic_prompt",
    "test_system_operator_v2_uses_distinct_prompt_identity",
    "test_system_operator_canonicalizes_no_plan_execution_noise",
    "test_planner_empty_content_records_planner_empty_content_reason",
    "test_planner_schema_invalid_records_planner_schema_invalid_reason",
    "test_entry_planner_batch_claims_rank_order_before_parallel_planning",
    "test_stale_llm_result_cannot_activate_entry_plan",
    "test_local_broker_backpressure_does_not_consume_attempt",
    "test_compile_validation_is_terminal_after_one_attempt",
    "test_incompatible_generation_explicitly_supersedes_active_plan",
)
for _test_name in _SUPERSEDED_BY_CANONICAL_ENTRY_LIFECYCLE:
    globals()[_test_name] = pytest.mark.skip(
        reason="#2851 replaced the legacy Entry runtime contracts"
    )(globals()[_test_name])
