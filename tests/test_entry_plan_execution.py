from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

from aletheia.execution.entry_plan_execution import build_entry_plan_order_intent
from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    Fill,
    Order,
    RiskDecision,
    UniverseAsset,
    UniverseMember,
    UniverseSnapshot,
)
from aletheia.trading.entry_plan import EntryPlan, entry_plan_payload


def _plan(*, mode="TRIGGERED", tactic="OPPOSING_BBO_IOC", account_mode="bitget_demo") -> EntryPlan:
    now = datetime.now(timezone.utc)
    triggered = mode == "TRIGGERED"
    maker = tactic == "POST_ONLY_LIMIT"
    return EntryPlan(
        planning_generation_id="gen-1",
        candidate_id="candidate-1",
        candidate_family="momentum",
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        direction="long",
        thesis="bounded continuation",
        scenario="retrace reclaim",
        execution_mode=mode,
        trigger_spec_id="momentum-retrace-reclaim" if triggered else None,
        trigger_spec_version="v1" if triggered else None,
        trigger_params={
            "breakout_level": 100,
            "pullback_low": 99,
            "pullback_high": 100,
            "entry_low": 100,
            "entry_high": 101,
            "invalidation_price": 98,
        }
        if triggered
        else {},
        trigger_data_requirements=("quote", "trade", "books5", "candle1m") if triggered else (),
        trigger_freshness_ms=2_000 if triggered else None,
        entry_zone=(100, 101),
        invalidation_conditions=("price_below_98",),
        invalidation_price=98,
        stop_price=98,
        target_price=105,
        expected_horizon_hours=4,
        approved_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        order_tactic=tactic,
        maker_required=maker,
        time_in_force="POST_ONLY" if maker else "IOC",
        limit_price=100 if maker else None,
        price_rule="EXACT_LIMIT" if maker else "OPPOSING_BBO",
        bbo_reference=None if maker else "ask",
        max_wait_sec=60 if maker else 0,
        cancel_replace_policy="CANCEL_ONLY",
        max_requotes=0,
        partial_fill_policy="KEEP_PARTIAL_CANCEL_REMAINDER",
        max_slippage_bps=None if maker else 10,
        worst_price=None if maker else 101.2,
        max_notional=100,
        max_risk_pct=0.25,
        max_leverage=2,
        required_permission="Paper",
        execution_account_mode=account_mode,
        risk_policy_id="demo-v1",
        risk_policy_revision=1,
        risk_policy_hash="risk-hash",
        behavior_version="entry-planner/v1",
    )


def test_momentum_v2_requires_and_preserves_treatment_receipt(monkeypatch):
    plan = _plan(account_mode="paper_internal").model_copy(
        update={
            "trigger_spec_version": "v2",
            "treatment_exposure_id": "exposure-treatment",
            "trigger_params": {
                **_plan().trigger_params,
                "geometry_version": "momentum-entry-geometry/v2",
                "geometry_fingerprint": "f" * 64,
            },
        }
    )
    monkeypatch.setattr(
        "aletheia.learning.ledger.get_exposure",
        lambda _exposure_id: {
            "path_id": "momentum_entry_geometry",
            "assignment": "treatment",
            "after_json": json.dumps({"value": 1.0}),
            "manifest_json": json.dumps({"geometry_available": True}),
        },
    )
    observed_at = datetime.now(timezone.utc).isoformat()
    proposal = build_entry_plan_order_intent(
        plan, bid=100, ask=100.05, observed_at=observed_at, source_observed_at=observed_at
    )
    attribution = json.loads(proposal.attribution_snapshot)
    assert attribution["treatment_exposure_id"] == "exposure-treatment"
    assert attribution["geometry_version"] == "momentum-entry-geometry/v2"

    with pytest.raises(ValueError, match="entry_plan_treatment_exposure_required"):
        build_entry_plan_order_intent(
            plan.model_copy(update={"treatment_exposure_id": None}),
            bid=100,
            ask=100.05,
            observed_at=observed_at,
            source_observed_at=observed_at,
        )
def test_plain_limit_is_not_maker_and_post_only_crossing_fails_closed():
    plan = _plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT")
    with pytest.raises(ValueError, match="post_only_would_cross_bbo"):
        build_entry_plan_order_intent(
            plan,
            bid=99.9,
            ask=100,
            observed_at=datetime.now(timezone.utc).isoformat(),
            source_observed_at=datetime.now(timezone.utc).isoformat(),
        )


def test_ai_entry_runtime_authority_requires_exact_paper_and_frozen_lineage(monkeypatch):
    from aletheia.execution.entry_plan_execution import _require_ai_entry_authority
    from aletheia.trading.entry_analysis import EntryAnalysisContextV1, fingerprint

    instant = datetime(2026, 9, 2, tzinfo=timezone.utc)
    context = EntryAnalysisContextV1(
        analysis_cutoff_at=instant.isoformat(),
        instrument={"venue": "bitget", "symbol": "BTC/USDT", "market_type": "perp", "direction": "long"},
        planning_generation={"id": "g-v3", "planner_contract_version": "entry-planner/v3", "candidate_id": "c1", "candidate_family": "momentum"},
        candidate={"fact_refs": ["candidate:c1"]},
        candle_windows={tf: {"availability": "unavailable", "bars": []} for tf in ("4h", "1h", "15m")},
        market_structure={"fact_refs": []}, quant_context={"fact_refs": []},
        portfolio_context={"availability": "available", "fact_refs": []},
        execution_context={"availability": "available", "fact_refs": []}, anchors=(),
        entry_profiles=(), learning_context={"availability": "unavailable"},
    )
    generation = {
        "id": "g-v3", "planner_contract_version": "entry-planner/v3",
        "planner_input_json": context.model_dump_json(),
        "planner_input_hash": fingerprint(context.model_dump(mode="json")),
    }
    plan = SimpleNamespace(
        version="entry-plan/v2", planning_generation_id="g-v3", candidate_id="c1",
        analysis_context_fingerprint=context.context_fingerprint,
        venue="bitget", symbol="BTC/USDT", market_type="perp", direction="long",
        required_permission="Paper", execution_account_mode="paper_internal",
    )
    cfg = SimpleNamespace(
        agent=SimpleNamespace(permission_mode="Paper"),
        exchange=SimpleNamespace(paper_execution_mode="paper_internal"),
        live=SimpleNamespace(state="live_disabled"),
    )
    monkeypatch.setattr(repo, "get_candidate_planning_generation", lambda _id: generation)
    monkeypatch.setattr("aletheia.config.loader.load_config", lambda **_kwargs: cfg)
    _require_ai_entry_authority(plan)

    cfg.agent.permission_mode = "Manual"
    with pytest.raises(ValueError, match="ai_entry_runtime_authority_unavailable"):
        _require_ai_entry_authority(plan)
    cfg.agent.permission_mode = "Paper"
    generation["planner_input_hash"] = "0" * 64
    with pytest.raises(ValueError, match="ai_entry_context_identity_mismatch"):
        _require_ai_entry_authority(plan)


def test_ai_entry_trigger_identity_must_match_plan(monkeypatch):
    monkeypatch.setattr(
        "aletheia.execution.entry_plan_execution.require_entry_plan_runtime_authority",
        lambda _plan: None,
    )
    plan = _plan().model_copy(update={
        "version": "entry-plan/v2",
        "trigger_spec_id": "anchor-retrace-reclaim",
        "trigger_spec_version": "v1",
        "trigger_params": {**_plan().trigger_params, "geometry_fingerprint": "a" * 64},
    })
    now = datetime.now(timezone.utc).isoformat()
    with pytest.raises(ValueError, match="ai_entry_trigger_evidence_required"):
        build_entry_plan_order_intent(
            plan, bid=100, ask=100.05, observed_at=now, source_observed_at=now,
        )
    with pytest.raises(ValueError, match="ai_entry_trigger_identity_mismatch"):
        build_entry_plan_order_intent(
            plan, bid=100, ask=100.05, observed_at=now, source_observed_at=now,
            trigger_evidence={
                "trigger_spec_id": "momentum-retrace-reclaim",
                "trigger_spec_version": "v2",
                "geometry_fingerprint": "a" * 64,
            },
        )


def test_urgent_entry_uses_exact_opposing_bbo_ioc():
    plan = _plan()
    received_at = datetime.now(timezone.utc).isoformat()
    source_at = datetime.now(timezone.utc).isoformat()
    proposal = build_entry_plan_order_intent(
        plan,
        bid=100,
        ask=100.05,
        observed_at=received_at,
        source_observed_at=source_at,
    )
    assert proposal.order_type == "limit"
    assert proposal.price == 100.05
    assert proposal.time_in_force == "ioc"
    assert proposal.post_only is False
    evidence = json.loads(proposal.attribution_snapshot)
    assert evidence["market_received_at"] == received_at
    assert evidence["market_source_at"] == source_at


def test_urgent_entry_never_chases_beyond_worst_price():
    plan = _plan().model_copy(update={"worst_price": 100.04})
    with pytest.raises(ValueError, match="bbo_worst_price_exceeded"):
        build_entry_plan_order_intent(
            plan,
            bid=100,
            ask=100.05,
            observed_at=datetime.now(timezone.utc).isoformat(),
            source_observed_at=datetime.now(timezone.utc).isoformat(),
        )


def test_urgent_entry_cannot_escape_the_plan_zone():
    with pytest.raises(ValueError, match="entry_price_outside_plan_zone"):
        build_entry_plan_order_intent(
            _plan(),
            bid=101.09,
            ask=101.1,
            observed_at=datetime.now(timezone.utc).isoformat(),
            source_observed_at=datetime.now(timezone.utc).isoformat(),
        )


@pytest.mark.parametrize(
    "direction,invalidation,stop_price,target_price,error",
    [
        ("long", 100.5, 98, 105, "long_invalidation_above_entry"),
        ("short", 99.5, 102, 95, "short_invalidation_below_entry"),
    ],
)
def test_entry_plan_invalidation_must_stay_protective_to_selected_entry(
    direction, invalidation, stop_price, target_price, error,
):
    payload = _plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT").model_dump()
    payload.update(
        direction=direction,
        entry_zone=(99, 101),
        limit_price=100,
        invalidation_price=invalidation,
        stop_price=stop_price,
        target_price=target_price,
    )

    with pytest.raises(ValueError, match=error):
        EntryPlan(**payload)


def test_dynamic_entry_plan_invalidation_covers_the_full_entry_zone():
    payload = _plan().model_dump()
    payload.update(entry_zone=(99, 101), invalidation_price=99.5)
    payload["trigger_params"] = {
        **payload["trigger_params"],
        "entry_low": 99,
        "entry_high": 101,
        "invalidation_price": 99.5,
    }

    with pytest.raises(ValueError, match="long_invalidation_above_entry"):
        EntryPlan(**payload)


def test_realized_liquidity_comes_only_from_private_fill_evidence():
    from aletheia.execution.exchange_materialization import parse_bitget_private_fills

    base = {
        "orderId": "EX-1",
        "tradeId": "fill-1",
        "fillPrice": "100",
        "fillQuantity": "1",
        "fee": "0.01",
        "feeCoin": "USDT",
        "cTime": "1787788800000",
    }
    assert parse_bitget_private_fills([{**base, "tradeScope": "M"}])[0].realized_liquidity == "maker"
    assert parse_bitget_private_fills([base])[0].realized_liquidity is None


def test_canonical_risk_enforces_the_bound_entry_plan_envelope(monkeypatch):
    from types import SimpleNamespace

    from aletheia.risk import engine

    plan = _plan()
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "triggered"},
    )
    monkeypatch.setattr(
        engine,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 1},
    )
    proposal = build_entry_plan_order_intent(
        plan,
        bid=100,
        ask=100.05,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source_observed_at=datetime.now(timezone.utc).isoformat(),
    ).model_copy(update={"quantity": 2.0})
    resolved = SimpleNamespace(
        policy=SimpleNamespace(policy_id="demo-v1", revision=1),
        policy_hash="risk-hash",
    )

    violations = engine._entry_plan_authority_violations(
        proposal,
        capital_base=1_000,
        resolved_policy=resolved,
    )

    assert "entry_plan_max_notional_exceeded" in violations
    assert "entry_plan_max_risk_exceeded" in violations


@pytest.mark.parametrize(
    ("field", "value", "violation"),
    [
        ("order_type", "market", "entry_plan_order_type_mismatch"),
        ("stop_price", 99, "entry_plan_stop_price_mismatch"),
        ("stop_source", "default", "entry_plan_stop_source_mismatch"),
        ("take_profit", 106, "entry_plan_take_profit_mismatch"),
        ("expected_horizon_hours", 5, "entry_plan_expected_horizon_hours_mismatch"),
        ("order_tactic", "POST_ONLY_LIMIT", "entry_plan_order_tactic_mismatch"),
        ("time_in_force", "GTC", "entry_plan_time_in_force_mismatch"),
        ("post_only", True, "entry_plan_post_only_mismatch"),
        ("bbo_reference", "bid", "entry_plan_bbo_reference_mismatch"),
        ("max_slippage_bps", 9, "entry_plan_max_slippage_bps_mismatch"),
        ("worst_price", 100.5, "entry_plan_worst_price_mismatch"),
        ("price", 102, "entry_plan_entry_envelope_exceeded"),
    ],
)
def test_canonical_risk_rejects_proposal_execution_fields_outside_entry_plan(
    monkeypatch, field, value, violation
):
    from types import SimpleNamespace

    from aletheia.risk import engine

    plan = _plan()
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "triggered"},
    )
    monkeypatch.setattr(
        engine,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 1},
    )
    proposal = build_entry_plan_order_intent(
        plan,
        bid=100,
        ask=100.05,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source_observed_at=datetime.now(timezone.utc).isoformat(),
    ).model_copy(update={field: value})
    resolved = SimpleNamespace(
        policy=SimpleNamespace(policy_id="demo-v1", revision=1),
        policy_hash="risk-hash",
    )

    violations = engine._entry_plan_authority_violations(
        proposal,
        capital_base=1_000,
        resolved_policy=resolved,
    )

    assert violation in violations


def test_canonical_risk_enforces_the_plan_worst_price_boundary(monkeypatch):
    from types import SimpleNamespace

    from aletheia.risk import engine

    base_plan = _plan()
    plan = base_plan.model_copy(update={"worst_price": 100.04})
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "triggered"},
    )
    monkeypatch.setattr(
        engine,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 1},
    )
    proposal = build_entry_plan_order_intent(
        base_plan,
        bid=100,
        ask=100.05,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source_observed_at=datetime.now(timezone.utc).isoformat(),
    ).model_copy(update={"worst_price": plan.worst_price})
    resolved = SimpleNamespace(
        policy=SimpleNamespace(policy_id="demo-v1", revision=1),
        policy_hash="risk-hash",
    )

    assert "entry_plan_worst_price_exceeded" in engine._entry_plan_authority_violations(
        proposal, capital_base=1_000, resolved_policy=resolved
    )


def test_canonical_risk_accepts_exact_intent_and_bounded_size_reduction(monkeypatch):
    from types import SimpleNamespace

    from aletheia.risk import engine

    plan = _plan()
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "triggered"},
    )
    monkeypatch.setattr(
        engine,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 1},
    )
    resolved = SimpleNamespace(
        policy=SimpleNamespace(policy_id="demo-v1", revision=1),
        policy_hash="risk-hash",
    )
    proposal = build_entry_plan_order_intent(
        plan,
        bid=100,
        ask=100.05,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source_observed_at=datetime.now(timezone.utc).isoformat(),
    )

    assert engine._entry_plan_authority_violations(
        proposal, capital_base=1_000, resolved_policy=resolved
    ) == []
    reduced = proposal.model_copy(
        update={"quantity": proposal.quantity / 2, "leverage": 1}
    )
    assert engine._entry_plan_authority_violations(
        reduced, capital_base=1_000, resolved_policy=resolved
    ) == []


def test_canonical_risk_accepts_execution_fields_from_a_new_approved_plan(monkeypatch):
    from types import SimpleNamespace

    from aletheia.risk import engine

    original = _plan()
    changed = EntryPlan.model_validate(
        {
            **original.model_dump(),
            "plan_id": "new-plan-version",
            "target_price": 106,
            "expected_horizon_hours": 6,
        }
    )
    payload, digest = entry_plan_payload(changed)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "triggered"},
    )
    monkeypatch.setattr(
        engine,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 1},
    )
    proposal = build_entry_plan_order_intent(
        changed,
        bid=100,
        ask=100.05,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source_observed_at=datetime.now(timezone.utc).isoformat(),
    )
    resolved = SimpleNamespace(
        policy=SimpleNamespace(policy_id="demo-v1", revision=1),
        policy_hash="risk-hash",
    )

    assert engine._entry_plan_authority_violations(
        proposal, capital_base=1_000, resolved_policy=resolved
    ) == []


def test_canonical_risk_requires_exact_resting_maker_limit(monkeypatch):
    from types import SimpleNamespace

    from aletheia.risk import engine

    plan = _plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT")
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "waiting"},
    )
    monkeypatch.setattr(
        engine,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 1},
    )
    proposal = build_entry_plan_order_intent(
        plan,
        bid=99.9,
        ask=100.1,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source_observed_at=datetime.now(timezone.utc).isoformat(),
    ).model_copy(update={"price": 100.5})
    resolved = SimpleNamespace(
        policy=SimpleNamespace(policy_id="demo-v1", revision=1),
        policy_hash="risk-hash",
    )

    assert "entry_plan_limit_price_mismatch" in engine._entry_plan_authority_violations(
        proposal, capital_base=1_000, resolved_policy=resolved
    )


def test_canonical_risk_rejects_superseded_or_expired_entry_plan(monkeypatch):
    from types import SimpleNamespace

    from aletheia.risk import engine

    executable_plan = _plan()
    plan = executable_plan.model_copy(
        update={
            "approved_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        }
    )
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "triggered"},
    )
    monkeypatch.setattr(
        engine,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 0},
    )
    proposal = build_entry_plan_order_intent(
        executable_plan,
        bid=100,
        ask=100.05,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source_observed_at=datetime.now(timezone.utc).isoformat(),
    )
    resolved = SimpleNamespace(
        policy=SimpleNamespace(policy_id="demo-v1", revision=1),
        policy_hash="risk-hash",
    )

    violations = engine._entry_plan_authority_violations(
        proposal,
        capital_base=1_000,
        resolved_policy=resolved,
    )

    assert "entry_plan_generation_superseded" in violations
    assert "entry_plan_expired" in violations


def _persist_waiting_plan(
    *,
    account_mode="bitget_demo",
    mode="TRIGGERED",
    tactic="OPPOSING_BBO_IOC",
    plan_updates=None,
    candidate_id="candidate-1",
    candidate_family="momentum",
) -> EntryPlan:
    repo.upsert_universe_asset(
        UniverseAsset(
            id="asset-btc",
            venue="bitget",
            symbol="BTC/USDT",
            market_type="perp",
            min_notional=1.0,
            price_precision=4,
            amount_precision=6,
        )
    )
    snapshot = UniverseSnapshot(expires_at="2099-01-01T00:00:00+00:00")
    repo.insert_universe_snapshot(snapshot)
    candidate = UniverseMember(
        id=candidate_id,
        snapshot_id=snapshot.id,
        asset_id="asset-btc",
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        rank=1,
        total_score=90,
        scanner_family=candidate_family,
        scanner_direction="long",
        regime_tag="trending",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    repo.insert_candidate(candidate)
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    repo.claim_next_candidate_planning("owner")
    plan = _plan(account_mode=account_mode, mode=mode, tactic=tactic).model_copy(
        update={
            "planning_generation_id": generation["id"],
            "candidate_id": candidate_id,
            "candidate_family": candidate_family,
            **(plan_updates or {}),
        }
    )
    repo.complete_candidate_planning_with_plan(generation["id"], "owner", plan)
    return plan


def _execution_receipt_kwargs(
    now: datetime,
    *,
    depth: bool,
    depth_size: float = 10.0,
) -> dict:
    values = {
        "stream_generation_id": "stream-1",
        "subscription_generation_id": "subscription-1",
        "quote_sequence": 11,
        "required_channels": ("quote", "trade", "depth", "candle") if depth else ("quote",),
    }
    if depth:
        instant_ms = int(now.timestamp() * 1_000)
        values.update(
            {
                "depth_bids": ((100.0, depth_size),),
                "depth_asks": ((100.05, depth_size),),
                "depth_exchange_timestamp_ms": instant_ms,
                "depth_received_timestamp_ms": instant_ms,
                "depth_sequence": 12,
            }
        )
    return values


@pytest.mark.parametrize(
    (
        "account_mode",
        "plan_status",
        "proposal_status",
        "claim_active",
        "order_present",
        "expected_outcome",
        "authority_released",
    ),
    [
        ("paper_internal", "waiting", "executing", False, False, "invalidated", True),
        ("paper_internal", "triggered", "executing", False, False, "invalidated", True),
        ("paper_internal", "risk_reserved", "executing", False, False, "invalidated", True),
        ("paper_internal", "waiting", "executing", True, False, "active_claim", False),
        ("bitget_demo", "triggered", "executing", False, False, "external_execution", False),
        ("bitget_demo", "waiting", "executed", False, False, "external_execution", False),
        ("bitget_demo", "risk_reserved", "executing", False, True, "order_present", False),
    ],
)
def test_watcher_capacity_cleanup_handles_interrupted_pre_order_reservation(
    account_mode,
    plan_status,
    proposal_status,
    claim_active,
    order_present,
    expected_outcome,
    authority_released,
):
    from aletheia.agent_runtime.fast_trigger_watch import (
        _invalidate_pre_order_entry_plan,
    )

    plan = _persist_waiting_plan(
        account_mode=account_mode,
        mode="RESTING_MAKER",
        tactic="POST_ONLY_LIMIT",
    )
    if plan_status in {"triggered", "risk_reserved"}:
        assert repo.transition_entry_plan(
            plan.plan_id,
            expected_status="waiting",
            status="triggered",
        )
    if plan_status == "risk_reserved":
        assert repo.transition_entry_plan(
            plan.plan_id,
            expected_status="triggered",
            status="risk_reserved",
        )
    now = datetime.now(timezone.utc)
    proposal = build_entry_plan_order_intent(
        plan,
        bid=99.9,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
    )
    repo.insert_trade_proposal(proposal)
    repo.update_trade_proposal(proposal.id, status=proposal_status)
    repo.acquire_entry_risk_reservation(
        proposal_id=proposal.id,
        proposal_commit_hash=proposal.commit_hash,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        execution_mode=proposal.execution_mode,
        quantity=proposal.quantity,
        price=proposal.price,
        leverage=proposal.leverage,
    )
    assert repo.insert_proposal_execution_claim(
        proposal.id,
        "interrupted-owner",
        (now - timedelta(seconds=601)).isoformat(),
        (
            (now + timedelta(seconds=60)).isoformat()
            if claim_active
            else (now - timedelta(seconds=1)).isoformat()
        ),
    )
    if order_present:
        decision = RiskDecision(
            proposal_id=proposal.id,
            approved=True,
            allowed_execution_mode=proposal.execution_mode,
            decision_role="authoritative",
            evaluated_quantity=proposal.quantity,
            proposal_commit_hash=proposal.commit_hash,
            proposal_execution_mode=proposal.execution_mode,
        )
        repo.insert_risk_decision(decision)
        repo.insert_order(
            Order(
                proposal_id=proposal.id,
                risk_decision_id=decision.id,
                entry_plan_id=plan.plan_id,
                symbol=proposal.symbol,
                market_type=proposal.market_type,
                direction=proposal.direction,
                order_type=proposal.order_type,
                quantity=proposal.quantity,
                price=proposal.price,
                execution_mode=proposal.execution_mode,
                status="pending",
                source="native",
                client_order_id=proposal.id,
            )
        )

    outcome = _invalidate_pre_order_entry_plan(
        plan.plan_id,
        expected_status=plan_status,
        reason="entry_plan_monitoring_capacity_unavailable",
        now=now,
    )

    assert outcome == expected_outcome
    assert repo.get_entry_plan(plan.plan_id)["status"] == (
        "invalidated" if authority_released else plan_status
    )
    assert (repo.get_entry_risk_reservation(proposal.id) is None) is authority_released
    assert (repo.get_proposal_execution_claim(proposal.id) is None) is authority_released
    assert repo.has_order_for_proposal(proposal.id) is order_present

def _persist_ordered_plan() -> tuple[EntryPlan, Order]:
    plan = _persist_waiting_plan()
    assert repo.transition_entry_plan(plan.plan_id, expected_status="waiting", status="triggered")
    assert repo.transition_entry_plan(plan.plan_id, expected_status="triggered", status="risk_reserved")
    now = datetime.now(timezone.utc)
    proposal = build_entry_plan_order_intent(
        plan,
        bid=99.9,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
    )
    repo.insert_trade_proposal(proposal)
    order = Order(
        proposal_id=proposal.id,
        entry_plan_id=plan.plan_id,
        symbol=plan.symbol,
        market_type="usdt_futures",
        direction=plan.direction,
        order_type="limit",
        quantity=1,
        price=100,
        execution_mode="bitget_demo",
        exchange_order_id="exchange-1",
        status="submitted",
        source="adopted",
    )
    repo.insert_order(order)
    assert repo.transition_entry_plan(
        plan.plan_id,
        expected_status="risk_reserved",
        status="ordered",
        order_id=order.id,
    )
    return plan, order


def test_paper_broker_rejects_forged_entry_plan_limit_authority():
    from aletheia.execution.paper_broker import execute_paper_order

    with pytest.raises(ValueError, match="paper_internal_entry_plan_limit_authority_required"):
        execute_paper_order(
            proposal_id="forged-proposal",
            risk_decision_id="forged-risk",
            symbol="BTC/USDT",
            market_type="perp",
            direction="long",
            order_type="limit",
            quantity=1,
            price=100,
            execution_mode="paper_internal",
            entry_plan_id="forged-plan",
            order_tactic="OPPOSING_BBO_IOC",
            time_in_force="ioc",
            post_only=False,
        )


@pytest.mark.parametrize(
    ("mode", "tactic", "expected_tif", "expected_post_only", "requested_liquidity"),
    (
        ("TRIGGERED", "OPPOSING_BBO_IOC", "ioc", 0, "taker"),
        ("RESTING_MAKER", "POST_ONLY_LIMIT", "post_only", 1, "maker"),
    ),
)
def test_paper_internal_entry_plan_executes_through_risk_and_gateway(
    monkeypatch,
    mode,
    tactic,
    expected_tif,
    expected_post_only,
    requested_liquidity,
):
    from aletheia.execution import gateway
    from aletheia.execution.entry_plan_execution import (
        build_entry_execution_receipt,
        build_entry_plan_order_intent,
        execute_entry_plan,
    )

    plan = _persist_waiting_plan(
        account_mode="paper_internal",
        mode=mode,
        tactic=tactic,
    )

    def approve(proposal, *, decision_role, execution_receipt=None):
        decision = RiskDecision(
            proposal_id=proposal.id,
            approved=True,
            allowed_execution_mode="paper_internal",
            decision_role=decision_role,
            evaluated_quantity=proposal.quantity,
            proposal_commit_hash=proposal.commit_hash,
            proposal_execution_mode=proposal.execution_mode,
        )
        repo.insert_risk_decision(decision)
        return SimpleNamespace(
            approved=True,
            allowed_execution_mode="paper_internal",
            decision_id=decision.id,
            decision_role=decision_role,
        )

    monkeypatch.setattr(gateway, "evaluate_proposal", approve)
    now = datetime.now(timezone.utc)
    result = execute_entry_plan(
        plan.plan_id,
        bid=100,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
        **_execution_receipt_kwargs(now, depth=tactic == "OPPOSING_BBO_IOC"),
    )
    assert result["status"] == (
        "ordered" if tactic == "POST_ONLY_LIMIT" else "executed"
    ), result
    proposal = repo.get_trade_proposal_by_entry_plan(plan.plan_id)
    order = repo.get_order(result["order_id"])
    assert proposal["execution_mode"] == "paper_internal"
    assert order["entry_plan_id"] == plan.plan_id
    assert order["order_tactic"] == tactic
    assert order["time_in_force"] == expected_tif
    assert order["post_only"] == expected_post_only
    assert order["requested_liquidity"] == requested_liquidity
    fills = repo.list_fills(order_id=order["id"])
    if tactic == "POST_ONLY_LIMIT":
        assert fills == []
        assert order["status"] == "open"
        assert repo.get_entry_plan(plan.plan_id)["status"] == "ordered"
    else:
        assert fills[0]["realized_liquidity"] is None
        assert repo.get_entry_plan(plan.plan_id)["status"] == "filled"


def test_paper_internal_entry_plan_passes_real_risk_gate(monkeypatch):
    from aletheia.config.loader import load_config
    from aletheia.data import price_feed
    from aletheia.portfolio import local_funding
    from aletheia.risk.policy import resolve_risk_policy
    from aletheia.execution.entry_plan_execution import execute_entry_plan

    cfg = load_config()
    resolved = resolve_risk_policy(cfg, "paper_internal")
    plan = _persist_waiting_plan(
        account_mode="paper_internal",
        plan_updates={
            "max_notional": 10.0,
            "risk_policy_id": resolved.policy.policy_id,
            "risk_policy_revision": resolved.policy.revision,
            "risk_policy_hash": resolved.policy_hash,
        },
    )
    monkeypatch.setattr(
        price_feed,
        "get_fresh_price_for_protective_close",
        lambda *_args, **_kwargs: 100.05,
    )
    monkeypatch.setattr(
        local_funding,
        "has_fresh_local_funding_schedule",
        lambda *_args, **_kwargs: True,
    )

    now = datetime.now(timezone.utc)
    result = execute_entry_plan(
        plan.plan_id,
        bid=100,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
        **_execution_receipt_kwargs(now, depth=True),
    )

    assert result["status"] == "executed", result
    assert repo.get_entry_plan(plan.plan_id)["status"] == "filled"
    order = repo.get_order(result["order_id"])
    fill = repo.list_fills(order_id=order["id"])[0]
    assert fill["fill_price"] == 100.05
    assert fill["realized_liquidity"] is None
    assert fill["simulated_liquidity"] == "taker"
    assert fill["paper_fill_model_version"] == "paper-order-fill/v1"
    assert fill["execution_attempt_id"] == order["execution_attempt_id"]


def test_paper_entry_plan_crash_replay_rechecks_receipt_expiry(monkeypatch):
    from aletheia.config.loader import load_config
    from aletheia.execution import gateway
    from aletheia.execution.entry_plan_execution import (
        build_entry_execution_receipt,
        build_entry_plan_order_intent,
    )
    from aletheia.portfolio import local_funding
    from aletheia.risk.engine import evaluate_proposal
    from aletheia.risk.policy import resolve_risk_policy

    monkeypatch.setattr(
        local_funding,
        "has_fresh_local_funding_schedule",
        lambda *_args, **_kwargs: True,
    )

    resolved = resolve_risk_policy(load_config(), "paper_internal")
    plan = _persist_waiting_plan(
        account_mode="paper_internal",
        mode="RESTING_MAKER",
        tactic="POST_ONLY_LIMIT",
        plan_updates={
            "max_notional": 10.0,
            "risk_policy_id": resolved.policy.policy_id,
            "risk_policy_revision": resolved.policy.revision,
            "risk_policy_hash": resolved.policy_hash,
        },
    )
    now = datetime.now(timezone.utc)
    proposal = build_entry_plan_order_intent(
        plan,
        bid=99.9,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
    )
    repo.insert_trade_proposal(proposal)
    receipt = build_entry_execution_receipt(
        plan,
        proposal,
        bid=99.9,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        **_execution_receipt_kwargs(now, depth=False),
    )
    approved = evaluate_proposal(
        proposal,
        decision_role="authoritative",
        execution_receipt=receipt,
    )
    assert approved.approved
    assert repo.get_entry_risk_reservation(proposal.id) is not None
    assert repo.get_order_for_entry_plan(plan.plan_id) is None

    expired = receipt.model_copy(
        update={"expires_at": (now - timedelta(seconds=1)).isoformat()}
    )
    expired = expired.model_copy(
        update={
            "receipt_hash": hashlib.sha256(expired.canonical_json().encode()).hexdigest()
        }
    )
    replay = gateway.execute_existing_proposal(
        proposal.id,
        "paper_internal",
        execution_receipt=expired,
    )

    assert replay["status"] == "attempt_rejected"
    assert "entry_execution_receipt_stale" in replay["risk_violations"]
    assert repo.get_order_for_entry_plan(plan.plan_id) is None
    assert len(
        [
            row
            for row in repo.list_risk_decisions(limit=20)
            if row["proposal_id"] == proposal.id
            and row["decision_role"] == "authoritative"
        ]
    ) == 1


def test_paper_ioc_walks_only_visible_depth_and_cancels_remainder(monkeypatch):
    from aletheia.config.loader import load_config
    from aletheia.execution.entry_plan_execution import execute_entry_plan
    from aletheia.portfolio import local_funding
    from aletheia.risk.policy import resolve_risk_policy

    cfg = load_config()
    resolved = resolve_risk_policy(cfg, "paper_internal")
    plan = _persist_waiting_plan(
        account_mode="paper_internal",
        plan_updates={
            "max_notional": 10.0,
            "risk_policy_id": resolved.policy.policy_id,
            "risk_policy_revision": resolved.policy.revision,
            "risk_policy_hash": resolved.policy_hash,
        },
    )
    monkeypatch.setattr(
        local_funding,
        "has_fresh_local_funding_schedule",
        lambda *_args, **_kwargs: True,
    )
    now = datetime.now(timezone.utc)
    result = execute_entry_plan(
        plan.plan_id,
        bid=100,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
        **_execution_receipt_kwargs(now, depth=True, depth_size=0.05),
    )

    order = repo.get_order(result["order_id"])
    fill = repo.list_fills(order_id=order["id"])[0]
    assert result["status"] == "executed"
    assert order["status"] == "cancelled"
    assert order["error_reason"] == "ioc_remainder_cancelled"
    assert fill["fill_quantity"] == 0.05
    assert fill["fill_price"] == 100.05
    assert repo.get_entry_plan(plan.plan_id)["status"] == "cancelled"
    assert repo.get_entry_risk_reservation(order["proposal_id"]) is None


def test_paper_maker_real_risk_places_open_order_without_inventing_fill(monkeypatch):
    from aletheia.config.loader import load_config
    from aletheia.data import price_feed
    from aletheia.execution.entry_plan_execution import execute_entry_plan
    from aletheia.portfolio import local_funding
    from aletheia.risk.policy import resolve_risk_policy

    cfg = load_config()
    resolved = resolve_risk_policy(cfg, "paper_internal")
    plan = _persist_waiting_plan(
        account_mode="paper_internal",
        mode="RESTING_MAKER",
        tactic="POST_ONLY_LIMIT",
        plan_updates={
            "max_notional": 10.0,
            "risk_policy_id": resolved.policy.policy_id,
            "risk_policy_revision": resolved.policy.revision,
            "risk_policy_hash": resolved.policy_hash,
        },
    )
    monkeypatch.setattr(
        price_feed,
        "get_fresh_price_for_protective_close",
        lambda *_args, **_kwargs: 100.05,
    )
    monkeypatch.setattr(
        local_funding,
        "has_fresh_local_funding_schedule",
        lambda *_args, **_kwargs: True,
    )

    now = datetime.now(timezone.utc)
    result = execute_entry_plan(
        plan.plan_id,
        bid=100.01,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
        **_execution_receipt_kwargs(now, depth=False),
    )

    assert result["status"] == "ordered", result
    proposal = repo.get_trade_proposal_by_entry_plan(plan.plan_id)
    order = repo.get_order_for_entry_plan(plan.plan_id)
    assert proposal is not None
    assert order is not None
    assert order["status"] == "open"
    assert order["price"] == plan.limit_price
    assert repo.list_fills(order_id=order["id"]) == []
    assert repo.list_positions(execution_mode="paper_internal") == []
    assert repo.get_entry_plan(plan.plan_id)["status"] == "ordered"
    decision = next(
        row
        for row in repo.list_risk_decisions(limit=10)
        if row["id"] == order["risk_decision_id"]
    )
    receipt = json.loads(decision["execution_receipt_json"])
    assert decision["approved"] == 1
    assert decision["execution_attempt_id"] == order["execution_attempt_id"]
    assert decision["execution_receipt_hash"] == order["execution_receipt_hash"]
    assert receipt["receipt_hash"] == order["execution_receipt_hash"]
    from aletheia.execution.gateway import cancel_entry_plan_order

    assert cancel_entry_plan_order(
        plan.plan_id,
        reason="maker_order_unfilled_until_expiry",
    )["status"] == "cancelled"
    assert repo.get_order(order["id"])["status"] == "cancelled"
    assert repo.get_entry_plan(plan.plan_id)["terminal_reason"] == (
        "maker_order_unfilled_until_expiry"
    )
    assert repo.get_entry_risk_reservation(order["proposal_id"]) is None


def test_paper_entry_plan_requires_exact_active_public_venue_rules(monkeypatch):
    from aletheia.config.loader import load_config
    from aletheia.execution.entry_plan_execution import execute_entry_plan
    from aletheia.portfolio import local_funding
    from aletheia.risk.policy import resolve_risk_policy

    resolved = resolve_risk_policy(load_config(), "paper_internal")
    plan = _persist_waiting_plan(
        account_mode="paper_internal",
        mode="RESTING_MAKER",
        tactic="POST_ONLY_LIMIT",
        plan_updates={
            "max_notional": 10.0,
            "risk_policy_id": resolved.policy.policy_id,
            "risk_policy_revision": resolved.policy.revision,
            "risk_policy_hash": resolved.policy_hash,
        },
    )
    repo.get_db().execute(
        "UPDATE universe_registry SET status='inactive' WHERE id='asset-btc'"
    )
    monkeypatch.setattr(
        local_funding,
        "has_fresh_local_funding_schedule",
        lambda *_args, **_kwargs: True,
    )
    now = datetime.now(timezone.utc)

    result = execute_entry_plan(
        plan.plan_id,
        bid=100.01,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
        **_execution_receipt_kwargs(now, depth=False),
    )

    assert result["status"] == "rejected"
    assert repo.get_order_for_entry_plan(plan.plan_id) is None
    assert repo.get_entry_plan(plan.plan_id)["status"] == "failed"


def test_paper_maker_fills_once_only_after_later_strict_opposite_trade(monkeypatch):
    from aletheia.config.loader import load_config
    from aletheia.execution import gateway
    from aletheia.execution.entry_plan_execution import execute_entry_plan
    from aletheia.portfolio import local_funding
    from aletheia.risk.policy import resolve_risk_policy
    from aletheia.trading.instruments import InstrumentId
    from aletheia.universe.fast_stream import FastMarketEvent

    cfg = load_config()
    resolved = resolve_risk_policy(cfg, "paper_internal")
    plan = _persist_waiting_plan(
        account_mode="paper_internal",
        mode="RESTING_MAKER",
        tactic="POST_ONLY_LIMIT",
        plan_updates={
            "max_notional": 10.0,
            "risk_policy_id": resolved.policy.policy_id,
            "risk_policy_revision": resolved.policy.revision,
            "risk_policy_hash": resolved.policy_hash,
        },
    )
    monkeypatch.setattr(
        local_funding,
        "has_fresh_local_funding_schedule",
        lambda *_args, **_kwargs: True,
    )
    now = datetime.now(timezone.utc) - timedelta(milliseconds=500)
    placed = execute_entry_plan(
        plan.plan_id,
        bid=100.01,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
        **_execution_receipt_kwargs(now, depth=False),
    )
    assert placed["status"] == "ordered"
    placed_order = repo.get_order(placed["order_id"])
    assert repo.get_entry_risk_reservation(placed_order["proposal_id"]) is not None

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")

    def trade(
        *,
        price,
        side,
        sequence,
        size=1,
        received_delta_ms=100,
        stream_generation_id="stream-1",
        subscription_generation_id="subscription-1",
    ):
        instant_ms = int(now.timestamp() * 1_000) + received_delta_ms
        return FastMarketEvent(
            event_type="trade",
            instrument=instrument,
            exchange_timestamp_ms=instant_ms,
            received_timestamp_ms=instant_ms,
            sequence=sequence,
            stream_generation_id=stream_generation_id,
            subscription_generation_id=subscription_generation_id,
            price=price,
            size=size,
            side=side,
        )

    assert gateway.observe_paper_entry_order_event(
        trade(price=100.0, side="sell", sequence=12)
    ) == []
    assert gateway.observe_paper_entry_order_event(
        trade(price=99.99, side="buy", sequence=13)
    ) == []
    first_quantity = float(placed_order["quantity"]) / 2
    event = trade(price=99.99, side="sell", sequence=14, size=first_quantity)
    partial = gateway.observe_paper_entry_order_event(event)
    assert len(partial) == 1
    assert partial[0]["order_status"] == "partially_filled"
    assert gateway.observe_paper_entry_order_event(event) == []
    assert repo.get_order(placed["order_id"])["status"] == "partially_filled"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "partially_filled"
    assert repo.get_entry_risk_reservation(placed_order["proposal_id"]) is not None

    restart_event = trade(
        price=99.98,
        side="sell",
        sequence=1,
        received_delta_ms=200,
        stream_generation_id="stream-2",
        subscription_generation_id="subscription-2",
    )
    assert gateway.observe_paper_entry_order_event(restart_event) == []
    assert gateway.rebind_open_paper_orders_to_monitoring(
        instrument,
        {
            "state": "ready",
            "stream_generation_id": "stream-2",
            "subscription_generation_id": "subscription-2",
        },
    ) == 1
    assert repo.get_order(placed["order_id"])["monitoring_gap_started_at"] is not None
    filled = gateway.observe_paper_entry_order_event(
        restart_event
    )
    assert len(filled) == 1
    assert filled[0]["order_status"] == "filled"

    order = repo.get_order(placed["order_id"])
    fills = repo.list_fills(order_id=order["id"])
    positions = repo.list_positions(execution_mode="paper_internal")
    assert order["status"] == "filled"
    assert len(fills) == 2
    assert sum(fill["fill_quantity"] for fill in fills) == pytest.approx(order["quantity"])
    assert {fill["fill_price"] for fill in fills} == {plan.limit_price}
    assert {fill["realized_liquidity"] for fill in fills} == {None}
    assert {fill["simulated_liquidity"] for fill in fills} == {"maker"}
    assert {fill["paper_fill_model_version"] for fill in fills} == {"paper-order-fill/v1"}
    assert {fill["execution_attempt_id"] for fill in fills} == {
        order["execution_attempt_id"]
    }
    assert len(positions) == 1
    assert positions[0]["proposal_id"] == order["proposal_id"]
    assert positions[0]["order_id"] == order["id"]
    assert repo.get_entry_plan(plan.plan_id)["status"] == "filled"
    assert repo.get_entry_risk_reservation(order["proposal_id"]) is None


def test_expired_execution_attempt_is_retryable_only_with_a_new_receipt(monkeypatch):
    from aletheia.config.loader import load_config
    from aletheia.execution.entry_plan_execution import execute_entry_plan
    from aletheia.portfolio import local_funding
    from aletheia.risk.policy import resolve_risk_policy

    cfg = load_config()
    resolved = resolve_risk_policy(cfg, "paper_internal")
    plan = _persist_waiting_plan(
        account_mode="paper_internal",
        mode="RESTING_MAKER",
        tactic="POST_ONLY_LIMIT",
        plan_updates={
            "max_notional": 10.0,
            "risk_policy_id": resolved.policy.policy_id,
            "risk_policy_revision": resolved.policy.revision,
            "risk_policy_hash": resolved.policy_hash,
        },
    )
    monkeypatch.setattr(
        local_funding,
        "has_fresh_local_funding_schedule",
        lambda *_args, **_kwargs: True,
    )
    stale = datetime.now(timezone.utc) - timedelta(seconds=3)
    rejected = execute_entry_plan(
        plan.plan_id,
        bid=100.01,
        ask=100.05,
        observed_at=stale.isoformat(),
        source_observed_at=stale.isoformat(),
        now=stale,
        **_execution_receipt_kwargs(stale, depth=False),
    )
    proposal = repo.get_trade_proposal_by_entry_plan(plan.plan_id)
    assert rejected["status"] == "attempt_rejected"
    assert rejected["risk_violations"] == ["entry_execution_receipt_stale"]
    assert proposal["status"] == "pending"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "waiting"
    assert repo.get_order_for_entry_plan(plan.plan_id) is None

    fresh = datetime.now(timezone.utc)
    replayed_market_fact = execute_entry_plan(
        plan.plan_id,
        bid=100.01,
        ask=100.05,
        observed_at=fresh.isoformat(),
        source_observed_at=(fresh - timedelta(seconds=3)).isoformat(),
        now=fresh,
        **_execution_receipt_kwargs(fresh, depth=False),
    )
    assert replayed_market_fact["status"] == "attempt_rejected"
    assert replayed_market_fact["risk_violations"] == [
        "entry_execution_receipt_quote_invalid"
    ]

    fresh = datetime.now(timezone.utc)
    placed = execute_entry_plan(
        plan.plan_id,
        bid=100.01,
        ask=100.05,
        observed_at=fresh.isoformat(),
        source_observed_at=fresh.isoformat(),
        now=fresh,
        **_execution_receipt_kwargs(fresh, depth=False),
    )
    decisions = [
        row
        for row in repo.list_risk_decisions(limit=10)
        if row["proposal_id"] == proposal["id"] and row["decision_role"] == "authoritative"
    ]
    assert placed["status"] == "ordered"
    assert len(decisions) == 3
    assert len({row["execution_attempt_id"] for row in decisions}) == 3


def test_one_trade_budget_is_shared_by_paper_orders_in_price_priority(monkeypatch):
    from aletheia.config.loader import load_config
    from aletheia.execution import gateway
    from aletheia.execution.entry_plan_execution import (
        build_entry_execution_receipt,
        execute_entry_plan,
    )
    from aletheia.portfolio import local_funding
    from aletheia.risk.policy import resolve_risk_policy
    from aletheia.trading.instruments import InstrumentId
    from aletheia.universe.fast_stream import FastMarketEvent

    resolved = resolve_risk_policy(load_config(), "paper_internal")
    common = {
        "max_notional": 10.0,
        "risk_policy_id": resolved.policy.policy_id,
        "risk_policy_revision": resolved.policy.revision,
        "risk_policy_hash": resolved.policy_hash,
    }
    high = _persist_waiting_plan(
        account_mode="paper_internal",
        mode="RESTING_MAKER",
        tactic="POST_ONLY_LIMIT",
        candidate_id="candidate-high",
        plan_updates={**common, "limit_price": 100.1},
    )
    low = _persist_waiting_plan(
        account_mode="paper_internal",
        mode="RESTING_MAKER",
        tactic="POST_ONLY_LIMIT",
        candidate_id="candidate-low",
        candidate_family="breakout",
        plan_updates={**common, "limit_price": 100.0},
    )
    monkeypatch.setattr(
        local_funding,
        "has_fresh_local_funding_schedule",
        lambda *_args, **_kwargs: True,
    )
    now = datetime.now(timezone.utc) - timedelta(milliseconds=500)
    high_result = execute_entry_plan(
        high.plan_id,
        bid=100.2,
        ask=100.25,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
        **_execution_receipt_kwargs(now, depth=False),
    )
    low_proposal = build_entry_plan_order_intent(
        low,
        bid=100.2,
        ask=100.25,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
    )
    repo.insert_trade_proposal(low_proposal)
    low_receipt = build_entry_execution_receipt(
        low,
        low_proposal,
        bid=100.2,
        ask=100.25,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        **_execution_receipt_kwargs(now, depth=False),
    )
    low_decision = RiskDecision(
        proposal_id=low_proposal.id,
        approved=True,
        allowed_execution_mode="paper_internal",
        proposal_execution_mode="paper_internal",
        decision_role="authoritative",
        evaluated_quantity=low_proposal.quantity,
        proposal_commit_hash=low_proposal.commit_hash,
        execution_attempt_id=low_receipt.attempt_id,
        execution_receipt_hash=low_receipt.receipt_hash,
        execution_receipt_json=low_receipt.model_dump_json(),
    )
    repo.insert_risk_decision(low_decision)
    low_order_model = Order(
        proposal_id=low_proposal.id,
        risk_decision_id=low_decision.id,
        symbol=low_proposal.symbol,
        market_type=low_proposal.market_type,
        direction=low_proposal.direction,
        order_type=low_proposal.order_type,
        quantity=low_proposal.quantity,
        price=low_proposal.price,
        execution_mode="paper_internal",
        entry_plan_id=low.plan_id,
        order_tactic="POST_ONLY_LIMIT",
        time_in_force="post_only",
        post_only=True,
        requested_liquidity="maker",
        execution_attempt_id=low_receipt.attempt_id,
        execution_receipt_hash=low_receipt.receipt_hash,
        paper_fill_model_version="paper-order-fill/v1",
        matching_stream_generation_id=low_receipt.stream_generation_id,
        matching_subscription_generation_id=low_receipt.subscription_generation_id,
        status="open",
    )
    repo.insert_order(low_order_model)
    assert repo.transition_entry_plan(
        low.plan_id,
        expected_status="waiting",
        status="triggered",
    )
    assert repo.transition_entry_plan(
        low.plan_id,
        expected_status="triggered",
        status="risk_reserved",
    )
    assert repo.transition_entry_plan(
        low.plan_id,
        expected_status="risk_reserved",
        status="ordered",
        order_id=low_order_model.id,
    )
    assert high_result["status"] == "ordered", high_result
    high_order = repo.get_order(high_result["order_id"])
    low_order = repo.get_order(low_order_model.id)
    budget = float(high_order["quantity"]) + float(low_order["quantity"]) / 2
    instant_ms = int(now.timestamp() * 1_000) + 100
    matches = gateway.observe_paper_entry_order_event(
        FastMarketEvent(
            event_type="trade",
            instrument=InstrumentId.from_parts("bitget", "BTC/USDT", "perp"),
            exchange_timestamp_ms=instant_ms,
            received_timestamp_ms=instant_ms,
            sequence=50,
            stream_generation_id="stream-1",
            subscription_generation_id="subscription-1",
            price=99.8,
            size=budget,
            side="sell",
        )
    )

    assert [match["order_id"] for match in matches] == [
        high_order["id"],
        low_order["id"],
    ]
    assert repo.get_order(high_order["id"])["status"] == "filled"
    assert repo.get_order(low_order["id"])["status"] == "partially_filled"
    assert sum(
        fill["fill_quantity"] for fill in repo.list_fills(order_id=low_order["id"])
    ) == pytest.approx(float(low_order["quantity"]) / 2)

def test_paper_failure_terminalizes_plan_and_releases_reservation(monkeypatch):
    from aletheia.execution import gateway
    from aletheia.execution.entry_plan_execution import (
        build_entry_execution_receipt,
        build_entry_plan_order_intent,
    )

    plan = _persist_waiting_plan(
        account_mode="paper_internal",
        mode="RESTING_MAKER",
        tactic="POST_ONLY_LIMIT",
    )
    now = datetime.now(timezone.utc)
    proposal = build_entry_plan_order_intent(
        plan,
        bid=99.9,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
    )
    repo.insert_trade_proposal(proposal)
    receipt = build_entry_execution_receipt(
        plan,
        proposal,
        bid=99.9,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        **_execution_receipt_kwargs(now, depth=False),
    )
    repo.acquire_entry_risk_reservation(
        proposal_id=proposal.id,
        proposal_commit_hash=proposal.commit_hash,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        execution_mode=proposal.execution_mode,
        quantity=proposal.quantity,
        price=proposal.price,
        leverage=proposal.leverage,
    )

    def approve(proposal, *, decision_role, execution_receipt=None):
        decision = RiskDecision(
            proposal_id=proposal.id,
            approved=True,
            allowed_execution_mode="paper_internal",
            decision_role=decision_role,
            evaluated_quantity=proposal.quantity,
            proposal_commit_hash=proposal.commit_hash,
            proposal_execution_mode=proposal.execution_mode,
        )
        repo.insert_risk_decision(decision)
        return SimpleNamespace(
            approved=True,
            allowed_execution_mode="paper_internal",
            decision_id=decision.id,
            decision_role=decision_role,
        )

    monkeypatch.setattr(gateway, "evaluate_proposal", approve)

    def fail_paper_order(**_kwargs):
        raise RuntimeError("paper broker failed")

    monkeypatch.setattr(gateway, "execute_paper_order", fail_paper_order)
    result = gateway.execute_existing_proposal(
        proposal.id,
        "paper_internal",
        execution_receipt=receipt,
    )

    assert result["status"] == "error"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "failed"
    assert repo.get_entry_risk_reservation(proposal.id) is None



def test_stale_exact_state_cannot_advance_triggered_plan():
    from aletheia.execution.entry_plan_execution import execute_entry_plan

    plan = _persist_waiting_plan()
    with pytest.raises(ValueError, match="entry_exact_state_stale"):
        execute_entry_plan(
            plan.plan_id,
            bid=100,
            ask=100.05,
            observed_at=(datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
            source_observed_at=datetime.now(timezone.utc).isoformat(),
            stream_generation_id="stream-1",
            subscription_generation_id="subscription-1",
            quote_sequence=1,
        )
    assert repo.get_entry_plan(plan.plan_id)["status"] == "waiting"


def test_entry_plan_monitoring_persists_only_material_readiness_changes():
    plan = _persist_waiting_plan()
    evidence = {
        "version": "entry-plan-monitoring/v1",
        "state": "warming",
        "stream_generation_id": "stream-1",
        "subscription_generation_id": "subscription-1",
        "required_channels": ["quote"],
        "channels": {
            "quote": {
                "state": "warming",
                "reason": "channel_event_not_observed",
                "last_sequence": None,
            }
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    assert repo.record_entry_plan_monitoring(plan.plan_id, evidence) is True
    assert repo.record_entry_plan_monitoring(
        plan.plan_id,
        {
            **evidence,
            "updated_at": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
        },
    ) is False
    ready = {
        **evidence,
        "state": "ready",
        "channels": {"quote": {"state": "ready", "reason": None, "last_sequence": 3}},
    }
    assert repo.record_entry_plan_monitoring(plan.plan_id, ready) is True
    row = repo.get_entry_plan(plan.plan_id)
    payload = row["monitoring_evidence_json"]
    assert row["monitoring_state"] == "ready"
    assert row["monitoring_evidence_hash"] == hashlib.sha256(payload.encode()).hexdigest()


def test_stale_source_state_cannot_advance_triggered_plan():
    from aletheia.execution.entry_plan_execution import execute_entry_plan

    plan = _persist_waiting_plan()
    with pytest.raises(ValueError, match="entry_exact_state_stale"):
        execute_entry_plan(
            plan.plan_id,
            bid=100,
            ask=100.05,
            observed_at=datetime.now(timezone.utc).isoformat(),
            source_observed_at=(
                datetime.now(timezone.utc) - timedelta(seconds=5)
            ).isoformat(),
            stream_generation_id="stream-1",
            subscription_generation_id="subscription-1",
            quote_sequence=1,
        )
    assert repo.get_entry_plan(plan.plan_id)["status"] == "waiting"


def test_gateway_cancel_rechecks_fill_and_fill_wins(monkeypatch):
    from aletheia.execution import bitget_broker, gateway

    plan, order = _persist_ordered_plan()

    def fill_during_cancel(**_kwargs):
        repo.insert_fill(Fill(order_id=order.id, fill_price=100, fill_quantity=1, fee_source="reconciled"))
        repo.update_order(order.id, status="filled")
        return {"cancelled": True, "order_id": "exchange-1"}

    monkeypatch.setattr(bitget_broker, "cancel_entry_order", fill_during_cancel)
    result = gateway.cancel_entry_plan_order(plan.plan_id, reason="price_invalidated")
    assert result["status"] == "filled"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "filled"
    assert repo.get_order(order.id)["status"] == "filled"


def test_gateway_cancel_recovers_exact_entry_order_relation():
    from aletheia.execution import gateway

    plan, order = _persist_ordered_plan()
    repo.get_db().execute(
        "UPDATE entry_plans SET order_id=NULL WHERE id=?",
        (plan.plan_id,),
    )
    assert repo.get_entry_plan(plan.plan_id)["order_id"] is None
    proposal = repo.get_trade_proposal_by_entry_plan(plan.plan_id)
    assert proposal is not None
    close_order = Order(
        proposal_id=proposal["id"],
        symbol=order.symbol,
        market_type=order.market_type,
        direction="short",
        order_type="market",
        quantity=0.5,
        execution_mode=order.execution_mode,
        exchange_order_id="newer-close-order",
        client_order_id="newer-close-order",
        position_id="position-1",
        close_reason="stop_loss",
        status="submitted",
        source="adopted",
        created_at=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
    )
    repo.insert_order(close_order)
    assert repo.get_order_for_entry_plan(plan.plan_id)["id"] == order.id
    repo.insert_fill(
        Fill(
            order_id=order.id,
            fill_price=100,
            fill_quantity=1,
            fee_source="reconciled",
        )
    )
    repo.update_order(order.id, status="filled")

    result = gateway.cancel_entry_plan_order(plan.plan_id, reason="price_invalidated")

    assert result["status"] == "filled"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "filled"


def test_gateway_cancel_keeps_partial_fill_and_closes_remainder(monkeypatch):
    from aletheia.execution import bitget_broker, gateway
    from aletheia.execution import exchange_materialization, reconciliation
    from aletheia.uta import manager
    from aletheia.uta.models import OrderLifecycle, ReadCompleteness

    plan, order = _persist_ordered_plan()

    monkeypatch.setattr(
        bitget_broker,
        "cancel_entry_order",
        lambda **_kwargs: {"cancelled": True, "order_id": "exchange-1"},
    )
    async def exact_order(*_args, **_kwargs):
        return SimpleNamespace(
            completeness=ReadCompleteness.COMPLETE,
            found=SimpleNamespace(
                state=OrderLifecycle.CANCELED,
                filled_quantity=0.5,
            ),
        )

    async def exact_fills(self, **_kwargs):
        return [{"orderId": "exchange-1", "fillQuantity": "0.5"}]

    materialized = []

    def materialize_before_cancel(order_id, rows):
        assert repo.get_order(order_id)["status"] == "submitted"
        materialized.extend(rows)
        repo.insert_fill(Fill(order_id=order_id, fill_price=100, fill_quantity=0.5, fee_source="reconciled"))
        repo.update_order(order_id, status="partially_filled")

    monkeypatch.setattr(manager, "query_uta", exact_order)
    monkeypatch.setattr(
        reconciliation._UTAReconciliationProjection,
        "get_order_fills",
        exact_fills,
    )
    monkeypatch.setattr(
        exchange_materialization,
        "materialize_bitget_entry",
        materialize_before_cancel,
    )
    result = gateway.cancel_entry_plan_order(plan.plan_id, reason="price_invalidated")
    assert result["status"] == "cancelled"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "cancelled"
    assert repo.get_order(order.id)["status"] == "cancelled"
    assert materialized == [{"orderId": "exchange-1", "fillQuantity": "0.5"}]
    assert sum(fill["fill_quantity"] for fill in repo.list_fills(order_id=order.id)) == 0.5


def test_gateway_cancel_ack_without_complete_private_evidence_stays_pending(monkeypatch):
    from aletheia.execution import bitget_broker, gateway
    from aletheia.uta import manager
    from aletheia.uta.models import ReadCompleteness

    plan, order = _persist_ordered_plan()
    monkeypatch.setattr(
        bitget_broker,
        "cancel_entry_order",
        lambda **_kwargs: {"cancelled": True, "order_id": "exchange-1"},
    )

    async def incomplete_order(*_args, **_kwargs):
        return SimpleNamespace(
            completeness=ReadCompleteness.UNAVAILABLE,
            found=None,
        )

    monkeypatch.setattr(manager, "query_uta", incomplete_order)
    result = gateway.cancel_entry_plan_order(plan.plan_id, reason="price_invalidated")
    assert result["status"] == "cancel_pending"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "ordered"
    assert repo.get_order(order.id)["status"] == "submitted"


def test_gateway_cancel_waits_when_order_quantity_leads_fill_history(monkeypatch):
    from aletheia.execution import bitget_broker, gateway, reconciliation
    from aletheia.uta import manager
    from aletheia.uta.models import OrderLifecycle, ReadCompleteness

    plan, order = _persist_ordered_plan()
    monkeypatch.setattr(
        bitget_broker,
        "cancel_entry_order",
        lambda **_kwargs: {"cancelled": True, "order_id": "exchange-1"},
    )

    async def terminal_order(*_args, **_kwargs):
        return SimpleNamespace(
            completeness=ReadCompleteness.COMPLETE,
            found=SimpleNamespace(
                state=OrderLifecycle.CANCELED,
                filled_quantity=0.5,
            ),
        )

    async def lagging_fills(self, **_kwargs):
        return []

    monkeypatch.setattr(manager, "query_uta", terminal_order)
    monkeypatch.setattr(
        reconciliation._UTAReconciliationProjection,
        "get_order_fills",
        lagging_fills,
    )

    result = gateway.cancel_entry_plan_order(plan.plan_id, reason="price_invalidated")

    assert result["status"] == "cancel_pending"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "ordered"
    assert repo.get_order(order.id)["status"] == "submitted"


@pytest.mark.parametrize(
    "materialized_state,starting_status,expected_reason",
    [
        ("evidence_missing", "submitted", "ioc_zero_fill_terminal"),
        ("partially_filled", "partially_filled", "ioc_remainder_terminal"),
    ],
)
def test_reconciliation_terminalizes_ioc_remainder_from_exact_order_evidence(
    monkeypatch,
    materialized_state,
    starting_status,
    expected_reason,
):
    from aletheia.execution import reconciliation
    from aletheia.uta.models import OrderLifecycle

    plan, order = _persist_ordered_plan()
    repo.update_order(
        order.id,
        source="native",
        order_tactic="OPPOSING_BBO_IOC",
        time_in_force="ioc",
        status=starting_status,
    )

    async def no_rows(self, **_kwargs):
        return []

    async def terminal_order(self, **_kwargs):
        return SimpleNamespace(
            state=OrderLifecycle.CANCELED,
            filled_quantity=0.5 if materialized_state == "partially_filled" else 0,
        )

    def materialize_ioc(order_id, *_args, **_kwargs):
        if materialized_state == "partially_filled" and not repo.list_fills(order_id=order_id):
            repo.insert_fill(
                Fill(
                    order_id=order_id,
                    fill_price=100,
                    fill_quantity=0.5,
                    fee_source="reconciled",
                )
            )
        return SimpleNamespace(state=materialized_state, new_fill_count=0)

    monkeypatch.setattr(reconciliation._UTAReconciliationProjection, "get_open_orders", no_rows)
    monkeypatch.setattr(reconciliation._UTAReconciliationProjection, "get_fills", no_rows)
    monkeypatch.setattr(reconciliation._UTAReconciliationProjection, "get_order_fills", no_rows)
    monkeypatch.setattr(
        reconciliation._UTAReconciliationProjection,
        "get_order_status",
        terminal_order,
    )
    monkeypatch.setattr(
        reconciliation,
        "materialize_bitget_entry",
        materialize_ioc,
    )

    reconciliation.reconcile()
    reconciliation.reconcile()

    stored = repo.get_order(order.id)
    assert stored["status"] == "cancelled"
    assert stored["error_reason"] == expected_reason
    assert repo.get_entry_plan(plan.plan_id)["status"] == "cancelled"


def test_reconciliation_waits_for_lagging_ioc_fill_history(monkeypatch):
    from aletheia.execution import reconciliation
    from aletheia.uta.models import OrderLifecycle

    plan, order = _persist_ordered_plan()
    repo.update_order(
        order.id,
        source="native",
        order_tactic="OPPOSING_BBO_IOC",
        time_in_force="ioc",
    )

    async def no_rows(self, **_kwargs):
        return []

    async def terminal_before_fills(self, **_kwargs):
        return SimpleNamespace(
            state=OrderLifecycle.CANCELED,
            filled_quantity=0.5,
        )

    monkeypatch.setattr(reconciliation._UTAReconciliationProjection, "get_open_orders", no_rows)
    monkeypatch.setattr(reconciliation._UTAReconciliationProjection, "get_fills", no_rows)
    monkeypatch.setattr(reconciliation._UTAReconciliationProjection, "get_order_fills", no_rows)
    monkeypatch.setattr(
        reconciliation._UTAReconciliationProjection,
        "get_order_status",
        terminal_before_fills,
    )
    monkeypatch.setattr(
        reconciliation,
        "materialize_bitget_entry",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="evidence_missing",
            new_fill_count=0,
        ),
    )

    reconciliation.reconcile()

    assert repo.get_order(order.id)["status"] == "submitted"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "ordered"
