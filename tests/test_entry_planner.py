import asyncio
import json
from datetime import datetime, timedelta, timezone

from aletheia.agent_shell.entry_planner import (
    ENTRY_PLANNER_MAX_CONCURRENCY,
    ENTRY_PLANNER_MIN_CONCURRENCY,
    plan_next_candidate,
    run_entry_planner_batch,
)
from aletheia.storage import repositories as repo
from aletheia.storage.models import UniverseMember, UniverseSnapshot
from aletheia.trading.entry_plan import EntryPlan


def _candidate(
    *,
    regime: str = "trending",
    rank: int = 1,
    price: float = 100.0,
    asset_id: str = "asset-btc",
    symbol: str = "BTC/USDT",
    direction: str = "long",
):
    snapshot = UniverseSnapshot(expires_at="2099-01-01T00:00:00+00:00")
    repo.insert_universe_snapshot(snapshot)
    candidate = UniverseMember(
        snapshot_id=snapshot.id,
        asset_id=asset_id,
        venue="bitget",
        symbol=symbol,
        market_type="perp",
        rank=rank,
        total_score=90 - rank,
        scanner_family="momentum",
        scanner_direction=direction,
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


def test_candidate_planning_claim_has_stable_fifo_fallback_without_rank():
    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    older = _candidate(asset_id="asset-eth", symbol="ETH/USDT")
    older_generation, _ = repo.ensure_candidate_planning_generation(older.id, now=start)
    newer = _candidate()
    repo.ensure_candidate_planning_generation(newer.id, now=start + timedelta(seconds=1))
    db = repo.get_db()
    db.execute(
        "DELETE FROM universe_members WHERE id IN (?, ?)",
        (older.id, newer.id),
    )
    db.commit()

    claimed = repo.claim_next_candidate_planning(
        "fallback-owner",
        now=start + timedelta(seconds=2),
    )

    assert claimed["id"] == older_generation["id"]


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
        return {"result": "NO_PLAN", "reasoning": "should not run"}

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
    assert repo.get_candidate_planning_generation(evicted_generation["id"])["attention_terminal_reason"] == "quota_evicted"


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


def test_entry_planner_batch_uses_bounded_parallel_workers(monkeypatch):
    active = 0
    peak = 0
    calls = 0

    async def fake_run_once(cfg=None):
        nonlocal active, peak, calls
        calls += 1
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return {"status": "no_plan", "model_invoked": True}

    pending = ENTRY_PLANNER_MAX_CONCURRENCY + 5
    remaining = iter((pending, pending - ENTRY_PLANNER_MAX_CONCURRENCY))
    monkeypatch.setattr(repo, "count_pending_candidate_planning", lambda: next(remaining))
    monkeypatch.setattr(
        "aletheia.agent_shell.entry_planner.run_entry_planner_once",
        fake_run_once,
    )

    result = asyncio.run(run_entry_planner_batch())

    assert ENTRY_PLANNER_MIN_CONCURRENCY == 3
    assert calls == peak == ENTRY_PLANNER_MAX_CONCURRENCY == 15
    assert result["planning_attempts"] == ENTRY_PLANNER_MAX_CONCURRENCY
    assert result["pending_queue_remaining"] == 5


def test_entry_planner_batch_claims_rank_order_before_parallel_planning(monkeypatch):
    candidates = [
        _candidate(
            rank=rank,
            asset_id=f"asset-{rank}",
            symbol=f"RANK{rank}/USDT",
        )
        for rank in (4, 1, 3, 2)
    ]
    generations = {
        candidate.rank: repo.ensure_candidate_planning_generation(candidate.id)[0]["id"]
        for candidate in candidates
    }
    started: list[str] = []
    active = 0
    peak = 0
    all_started = asyncio.Event()

    async def planner(context):
        nonlocal active, peak
        started.append(context["planning_generation"]["id"])
        active += 1
        peak = max(peak, active)
        if active == 4:
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=1)
        active -= 1
        return {"result": "NO_PLAN", "reasoning": "bounded parallel test"}

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


def test_no_plan_terminals_candidate_attention():
    candidate = _candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    assert repo.claim_next_candidate_planning("owner")
    assert repo.complete_candidate_planning_no_plan(
        generation["id"],
        "owner",
        {"result": "NO_PLAN", "reasoning": "insufficient geometry"},
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
