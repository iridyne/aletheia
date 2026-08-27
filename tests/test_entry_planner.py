import asyncio
from datetime import datetime, timedelta, timezone

from aletheia.agent_shell.entry_planner import plan_next_candidate
from aletheia.storage import repositories as repo
from aletheia.storage.models import UniverseMember, UniverseSnapshot
from aletheia.trading.entry_plan import EntryPlan


def _candidate(*, regime: str = "trending", rank: int = 1, price: float = 100.0):
    snapshot = UniverseSnapshot(expires_at="2099-01-01T00:00:00+00:00")
    repo.insert_universe_snapshot(snapshot)
    candidate = UniverseMember(
        snapshot_id=snapshot.id,
        asset_id="asset-btc",
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        rank=rank,
        total_score=90 - rank,
        scanner_family="momentum",
        scanner_direction="long",
        regime_tag=regime,
        entry_price_usd=price,
        expires_at="2099-01-01T00:00:00+00:00",
    )
    repo.insert_candidate(candidate)
    return candidate


def _plan(generation: dict) -> EntryPlan:
    now = datetime.now(timezone.utc)
    return EntryPlan(
        planning_generation_id=generation["id"],
        candidate_id=generation["last_candidate_id"],
        candidate_family="momentum",
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


def test_no_plan_is_durable_until_new_generation():
    candidate = _candidate()
    repo.ensure_candidate_planning_generation(candidate.id)
    calls = 0

    async def planner(_context):
        nonlocal calls
        calls += 1
        return {"result": "NO_PLAN", "reasoning": "no bounded entry geometry"}

    assert asyncio.run(plan_next_candidate(planner))["status"] == "no_plan"
    assert asyncio.run(plan_next_candidate(planner))["status"] == "idle"
    assert calls == 1

    replacement = _candidate(regime="sideways")
    next_generation, created = repo.ensure_candidate_planning_generation(replacement.id)
    assert created is True
    assert next_generation["id"] != candidate.id
    assert asyncio.run(plan_next_candidate(planner))["status"] == "no_plan"
    assert calls == 2


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


def test_expired_planning_claim_is_recovered_after_restart():
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    start = datetime(2026, 8, 27, tzinfo=timezone.utc)
    assert repo.claim_next_candidate_planning("dead-process", lease_seconds=1, now=start)["id"] == generation["id"]
    assert repo.claim_next_candidate_planning("early", now=start) is None
    recovered = repo.claim_next_candidate_planning("new-process", now=start + timedelta(seconds=2))
    assert recovered and recovered["owner_token"] == "new-process"


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
