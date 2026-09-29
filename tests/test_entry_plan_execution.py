from aletheia.trading.serenity_publication import publish_serenity_candidates
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

# Import the Gateway module up front: it binds repository functions at import time, so
# a test that string-patches `aletheia.execution.gateway.*` would otherwise trigger the
# first import of that module while a repository fake is installed, and the fake would
# leak into every later test in the session.
from aletheia.execution import gateway as _gateway_module
from aletheia.execution.entry_plan_execution import build_entry_plan_order_intent
from aletheia.execution.entry_plan_execution import _require_ai_entry_authority as _actual_ai_entry_authority
from tests.uta.test_execution_coordinator import _identity
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
from aletheia.trading.entry_activation import require_entry_trigger_activation as _actual_activation_gate

@pytest.mark.parametrize("kind,school,source_id,holding", [
    ("serenity", "serenity", "research-opportunity", 2160),
    ("universe", "short_term", "c1", 24),
])
def test_compiled_holding_is_separate_from_swing_timing(monkeypatch, kind, school, source_id, holding):
    from tests.test_entry_analysis import _anchor, _context_v2, _risk_intent
    from tests.helpers.venue_margin_fixture import seed_venue_margin_envelope
    from aletheia.config.loader import load_config
    from aletheia.trading.entry_analysis import EntryAnalysisContext, EntryHypothesis
    from aletheia.trading.entry_contract import entry_contract_fingerprints
    from aletheia.trading.entry_plan_compiler import compile_entry_plan

    monkeypatch.setattr("aletheia.risk.engine.account_equity_for_execution", lambda *_args: 5000.0)
    # The per-symbol ceiling seam reads equity through _account_equity
    # (_resolve_capital_base); give it the same fixture equity so the ceiling
    # resolves instead of failing closed on COMPILER_RISK_BUDGET_UNAVAILABLE.
    monkeypatch.setattr("aletheia.risk.engine._account_equity", lambda *_args, **_kwargs: 5000.0)
    # The compiled size is bounded by the cached *available* balance
    # (entry_plan_compiler -> account_available_for_execution), which no test data
    # dir can supply.
    monkeypatch.setattr("aletheia.risk.engine.account_available_for_execution", lambda *_args, **_kwargs: 5000.0)
    seed_venue_margin_envelope()
    cfg = load_config()
    cfg.agent.permission_mode = "Full"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    cfg.serenity.max_hold_days = 90
    primary = _anchor("candidate_reference", 100, "candidate:c1")
    invalidation = _anchor("confirmed_swing", 95, "swing:s1")
    data = _context_v2((primary, invalidation)).model_dump(exclude={"context_fingerprint"})
    behavior, schema = entry_contract_fingerprints()
    generation = {"id": "g1", "last_candidate_id": "c1", "candidate_family": "mean_reversion",
        "venue": "bitget", "symbol": "BTC/USDT", "market_type": "perp", "direction": "long",
        "entry_behavior_fingerprint": behavior, "schema_fingerprint": schema,
        "producer_commit": "isolated-test", "planning_identity": "a" * 64, "cycle_number": 1}
    data["planning_generation"].update(entry_behavior_fingerprint=behavior, schema_fingerprint=schema)
    data["candidate"]["candidate_price"] = 100
    data["candidate_opportunity_context"]["source"] = {"kind": kind, "id": source_id, "trade_school": school}
    data["market_structure"]["15m"].update(atr14=2, break_tolerance=0.1)
    data["execution_context"].update(bid=99.9, ask=100.1, spread_bps=2, depth_10bps_usd=100000, estimated_costs=0.1)
    context = EntryAnalysisContext.model_validate(data)
    hypothesis = EntryHypothesis(decision="ENTRY_PLAN", reason_code="OK", mechanism="reversal", direction="long",
        primary_anchor_ref=primary.anchor_id, invalidation_anchor_ref=invalidation.anchor_id,
        entry_profile_id="grounded-anchor-direct", capture_priority="PRICE_FIRST", capture_reason="fixture current evidence", capture_evidence_refs=(invalidation.anchor_id,), risk_tier="LOW",
        horizon="SWING", supporting_fact_refs=(invalidation.anchor_id,), risk_intent=_risk_intent(invalidation.anchor_id))
    plan = compile_entry_plan(generation, context, hypothesis, cfg)
    assert plan.expected_horizon_hours == 24
    assert plan.holding_horizon_hours == holding
    assert datetime.fromisoformat(plan.expires_at) - datetime.fromisoformat(plan.approved_at) == timedelta(hours=4)
    assert (plan.source_kind, plan.source_id, plan.trade_school) == (kind, source_id, school)
    assert plan.stop_price == pytest.approx(94.9)
    # The same supplied geometry becomes impossible under the existing Risk floor.
    old_target = cfg.trading.vol_target_pct
    cfg.trading.vol_target_pct = 6.0
    from aletheia.trading.entry_plan import stop_distance_below_min_floor
    assert stop_distance_below_min_floor(100, 94.9, execution_mode="bitget_demo", cfg=cfg)
    with pytest.raises(ValueError, match="stop_distance_below_vol_target"):
        compile_entry_plan(generation, context, hypothesis, cfg)
    cfg.trading.vol_target_pct = old_target
    now = datetime.fromisoformat(plan.approved_at)
    proposal = build_entry_plan_order_intent(plan, bid=99.9, ask=100.1,
        observed_at=now.isoformat(), source_observed_at=now.isoformat(), now=now)
    assert proposal.expected_horizon_hours == holding
    assert proposal.trade_school == school
    assert proposal.opportunity_id == (source_id if kind == "serenity" else None)
    assert proposal.stop_price == plan.stop_price
    assert proposal.quantity * (proposal.price - proposal.stop_price) <= (
        5000.0 * plan.max_risk_pct / 100 + 1e-10
    )
    with pytest.raises(ValueError, match="entry_plan_expired"):
        build_entry_plan_order_intent(plan, bid=99.9, ask=100.1,
            observed_at=now.isoformat(), source_observed_at=now.isoformat(), now=now + timedelta(hours=4))
    return plan


@pytest.mark.parametrize("legacy_version", [None, "entry-plan/v1"])
def test_historical_plan_readable_but_cannot_submit(monkeypatch, legacy_version):
    from aletheia.trading.entry_compat import decode_entry_plan
    monkeypatch.setattr("aletheia.execution.entry_plan_execution._require_ai_entry_authority", _actual_ai_entry_authority)
    payload = _plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT").model_dump(mode="json")
    for field in ("source_kind", "source_id", "trade_school", "holding_horizon_hours"):
        payload.pop(field)
    if legacy_version:
        payload["version"] = legacy_version
        payload.pop("behavior_fingerprint")
        payload.pop("schema_fingerprint")
    historical = decode_entry_plan(json.dumps(payload))
    assert historical.holding_horizon_hours == historical.expected_horizon_hours == 4
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="ai_entry_running_contract_mismatch"):
        build_entry_plan_order_intent(historical, bid=99.9, ask=100.1,
            observed_at=now.isoformat(), source_observed_at=now.isoformat(), now=now)


@pytest.mark.parametrize("holding", [0, -1, float("inf"), float("nan")])
def test_holding_horizon_requires_positive_finite_value(holding):
    payload = _plan().model_dump()
    payload["holding_horizon_hours"] = holding
    with pytest.raises(ValueError, match="holding_horizon_hours"):
        EntryPlan.model_validate(payload)


@pytest.mark.real_db
@pytest.mark.parametrize("mechanism,family,direction,market_type,school", [
    ("continuation", "momentum", "long", "spot", "short_term"),
    ("reversal", "mean_reversion", "short", "perp", "short_term"),
    ("continuation", "momentum", "long", "spot", "serenity"),
])
def test_controlled_grounded_candidate_compiles_exchange_plan(monkeypatch, request, tmp_path, mechanism, family, direction, market_type, school):
    import asyncio
    import math
    from tests.test_candidate_selection_utility import _controlled_admitted_target_candidate
    from aletheia.config.loader import load_config, save_config
    from aletheia.data.providers import Candle
    from extensions.data.collectors.microstructure import _parse_orderbook
    from aletheia.agent_shell.entry_planner import plan_next_candidate
    from aletheia.trading.entry_analysis import EntryHypothesis
    from tests.test_entry_analysis import _risk_intent
    from tests.helpers.venue_margin_fixture import seed_venue_margin_envelope

    from tests.test_sqlite_writer import _setup_db
    _setup_db(tmp_path, monkeypatch)
    monkeypatch.setattr("aletheia.execution.entry_plan_execution._require_ai_entry_authority", _actual_ai_entry_authority)
    cfg = load_config()
    cfg.exchange.name = "bitget"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    cfg.agent.permission_mode = "Full"
    monkeypatch.setattr("aletheia.risk.engine.account_equity_for_execution", lambda *_args: 5000.0)
    monkeypatch.setattr("aletheia.risk.engine._account_equity", lambda *_args, **_kwargs: 5000.0)
    # The compiled size is bounded by the cached *available* balance
    # (entry_plan_compiler -> account_available_for_execution), which no test data
    # dir can supply.
    monkeypatch.setattr("aletheia.risk.engine.account_available_for_execution", lambda *_args, **_kwargs: 5000.0)
    cfg.market_data.ccxt_exchange = "bitget"
    cfg.universe.max_candidates = 3
    save_config(cfg)
    cfg = load_config()
    from aletheia.agent_runtime.runtime_session import start_runtime_session, end_runtime_session
    start_runtime_session(cfg)
    request.addfinalizer(end_runtime_session)
    research_id = None
    if school == "serenity":
        from tests.test_candidate_opportunity_persistence import _serenity_publication_source
        from aletheia.storage.models import UniverseMember
        theme, opportunity = _serenity_publication_source()
        research_id = opportunity.id
        [candidate_id] = publish_serenity_candidates(theme.id, [research_id])
        candidate = UniverseMember.model_validate(repo.get_candidate(candidate_id))
    else:
        candidate = _controlled_admitted_target_candidate(cfg, monkeypatch, mechanism=mechanism, direction=direction, market_type=market_type)
    cfg = load_config()
    now = datetime.now(timezone.utc)
    symbol = candidate.symbol
    seed_venue_margin_envelope(symbol=symbol, market_type=market_type)
    repo.upsert_universe_asset(UniverseAsset(id=candidate.asset_id, venue="bitget", symbol=symbol, market_type=market_type,
        min_notional=1, price_precision=4, amount_precision=6))
    interval = 900_000
    end = int(now.timestamp() * 1000) // interval * interval
    candles = []
    for i in range(96):
        price = 100 + 2 * math.sin(i * math.pi / 6)
        candles.append(Candle(end - (96 - i) * interval, price, price + .5, price - .5, price, 1000))
    repo.upsert_ohlcv_candles(symbol, "15m", candles, exchange="bitget", market_type=market_type)
    book = _parse_orderbook(symbol, "bitget", {"bids": [[99.99, 10000]], "asks": [[100.01, 10000]]}, 0, market_type=market_type)
    repo.insert_orderbook_snapshot(book.model_dump())
    if market_type == "perp":
        repo.insert_funding_rate_snapshot(dict(symbol=symbol, exchange="bitget", funding_rate=0,
            funding_rate_annualized=0, next_funding_time=(now + timedelta(hours=8)).isoformat(), mark_price=100,
            observed_at=now.isoformat(), source_observed_at=now.isoformat(), acquired_at=now.isoformat(), source_time_status="provided"))
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    contexts = []
    async def model(context):
        assert context["learning_context"]["support"]["independent_n"] == 0
        contexts.append(context)
        primary = next(a for a in context["anchors"] if a["kind"] == "candidate_reference")
        invalidation = next(a for a in context["anchors"] if a["directional_role"] == "invalidation_candidate"
            and (a["price"] < 100 if direction == "long" else a["price"] > 100))
        hypothesis = EntryHypothesis(decision="ENTRY_PLAN", reason_code="OK", mechanism=mechanism, direction=direction,
            primary_anchor_ref=primary["anchor_id"], invalidation_anchor_ref=invalidation["anchor_id"],
            entry_profile_id="grounded-anchor-direct", capture_priority="PRICE_FIRST", capture_reason="fixture current evidence", capture_evidence_refs=(invalidation["anchor_id"],), risk_tier="LOW",
            horizon="SWING" if school == "serenity" else "INTRADAY", supporting_fact_refs=(invalidation["anchor_id"],),
            risk_intent=_risk_intent(invalidation["anchor_id"]))
        from aletheia.agents import helpers
        from aletheia.model_provider.base import ModelResponse
        from aletheia.agent_shell.entry_planner import system_operator_entry_planner
        async def complete(_request, **_kwargs):
            return ModelResponse(content=hypothesis.model_dump_json(), model="controlled-model", raw={"usage": {}})
        async def provider(_name):
            return object()
        monkeypatch.setattr(helpers, "complete_with_fallback", complete)
        monkeypatch.setattr(helpers, "get_provider_async", provider)
        return await system_operator_entry_planner(context, "test")
    result = asyncio.run(plan_next_candidate(model, cfg=cfg))
    stored = repo.get_candidate_planning_generation(generation["id"])
    assert result["status"] == "planned", (stored["result_json"], [c["execution_context"] for c in contexts])
    plan = EntryPlan.model_validate_json(repo.get_entry_plan(result["entry_plan_id"])["plan_json"])
    _actual_ai_entry_authority(plan)
    assert stored["model_invoked"] == stored["compiler_invoked"] == 1
    assert plan.execution_account_mode == "bitget_demo"
    assert plan.trade_school == school
    assert plan.holding_horizon_hours == (2160 if school == "serenity" else 4)
    assert plan.candidate_id == candidate.id
    assert stored["planner_input_json"] and stored["planner_input_hash"]


@pytest.fixture(autouse=True)
def _isolate_execution_contract(monkeypatch):
    monkeypatch.setattr(
        "aletheia.execution.entry_plan_execution._require_ai_entry_authority",
        lambda _plan: None,
    )
    # These unit tests own order geometry, Risk envelopes and reconciliation.
    # Durable trigger authority is exercised against the real engine/store in
    # test_entry_activation; the bypass regression below restores this gate.
    monkeypatch.setattr("aletheia.trading.entry_activation.require_entry_trigger_activation",
                        lambda *args, **kwargs: None)
    # `_plan()` now freezes a real financing (#3729) via apply_entry_financing,
    # which reads live account authority. These unit tests own order/gateway/
    # reconciliation logic, not account state, so pin a fixed pool here; a test
    # that cares about a specific balance overrides it after this fixture runs.
    monkeypatch.setattr("aletheia.risk.engine.account_equity_for_execution", lambda *_a, **_k: 5000.0)
    monkeypatch.setattr("aletheia.risk.engine._account_equity", lambda *_a, **_k: 5000.0)
    monkeypatch.setattr("aletheia.risk.engine.account_available_for_execution", lambda *_a, **_k: 5000.0)


def _plan(
    *,
    mode="TRIGGERED",
    tactic="OPPOSING_BBO_IOC",
    account_mode="bitget_demo",
    provenance=True,
    executable_notional_cap_usdt=None,
) -> EntryPlan:
    from aletheia.trading.entry_analysis import EntryAnchor, EntryHypothesis, fingerprint
    from aletheia.trading.risk_intent import RiskIntent
    from tests.helpers.entry_plan_financing_fixture import apply_entry_financing

    now = datetime.now(timezone.utc)
    triggered = mode == "TRIGGERED"
    maker = tactic == "POST_ONLY_LIMIT"
    primary = EntryAnchor(
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        kind="causal_breakout" if triggered else "candidate_reference",
        price=100.0,
        directional_role="entry_candidate",
        known_at_ms=int(now.timestamp() * 1000),
        source_fact_refs=("candidate:candidate-1",),
        source_fingerprint="a" * 64,
    )
    invalidation = EntryAnchor(
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        kind="confirmed_swing",
        price=98.0,
        directional_role="invalidation_candidate",
        known_at_ms=int(now.timestamp() * 1000),
        source_fact_refs=("swing:test",),
        source_fingerprint="b" * 64,
    )
    hypothesis = EntryHypothesis(
        decision="ENTRY_PLAN",
        reason_code="OK",
        mechanism="continuation",
        direction="long",
        primary_anchor_ref=primary.anchor_id,
        invalidation_anchor_ref=invalidation.anchor_id,
        entry_profile_id=("grounded-anchor-retrace-reclaim" if triggered else "grounded-anchor-direct"),
        capture_priority="PRICE_FIRST" if maker else "FILL_FIRST", capture_reason="fixture current evidence", capture_evidence_refs=(primary.anchor_id,),
        risk_tier="LOW",
        horizon="INTRADAY",
        supporting_fact_refs=(primary.anchor_id,),
        risk_intent=RiskIntent(posture="BASELINE", reason="fixture baseline posture", evidence_refs=(primary.anchor_id,)),
    )
    hypothesis_json = hypothesis.model_dump_json()
    trigger_params = (
        {
            "breakout_level": 100.0,
            "sweep_low": 98.5, "sweep_high": 99.0, "atr_abs": 2.0,
            "geometry_version": "anchor-retrace-reclaim-geometry/v1",
            "geometry_fingerprint": "f" * 64,
            "pullback_low": 99.0,
            "pullback_high": 100.0,
            "entry_low": 100.0,
            "entry_high": 101.0,
            "invalidation_price": 98.0,
        }
        if triggered
        else {}
    )
    geometry = {
        "entry_zone": (100.0, 101.0),
        "invalidation_price": 98.0,
        "stop_price": 98.0,
        "target_price": 105.0,
        "trigger_params": trigger_params,
    }
    plan = EntryPlan(
        planning_generation_id="gen-1",
        candidate_id="candidate-1",
        candidate_family="momentum",
        source_kind="universe", source_id="candidate-1", trade_school="short_term",
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        direction="long",
        thesis="bounded continuation",
        scenario="retrace reclaim",
        execution_mode=mode,
        trigger_spec_id="anchor-retrace-reclaim" if triggered else None,
        trigger_params=trigger_params,
        trigger_data_requirements=("quote", "trade", "books5", "candle1m") if triggered else (),
        trigger_freshness_ms=2_000 if triggered else None,
        entry_zone=(100.0, 101.0),
        invalidation_conditions=("price_below_98",),
        invalidation_price=98.0,
        stop_price=98.0,
        target_price=105.0,
        expected_horizon_hours=4,
        approved_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        order_tactic=tactic,
        maker_required=maker,
        time_in_force="POST_ONLY" if maker else "IOC",
        limit_price=100.0 if maker else None,
        price_rule="EXACT_LIMIT" if maker else "OPPOSING_BBO",
        bbo_reference=None if maker else "ask",
        capture_policy_id=("exact-anchor-passive-until-plan-terminal/v1" if maker else "bounded-opposing-bbo-ioc/v1"),
        max_wait_sec=0,
        cancel_replace_policy="CANCEL_ONLY",
        max_requotes=0,
        partial_fill_policy="KEEP_PARTIAL_CANCEL_REMAINDER",
        max_slippage_bps=20.0,
        worst_price=101.0,
        max_notional=100.0,
        max_risk_pct=0.25,
        max_leverage=2,
        required_permission="Paper",
        execution_account_mode=account_mode,
        risk_policy_id="demo-v1",
        risk_policy_revision=1,
        risk_policy_hash="risk-hash",
        producer_commit="test",
        behavior_fingerprint="b" * 64,
        schema_fingerprint="s" * 64,
        planning_identity="p" * 64,
        cycle_number=1,
        analysis_context_fingerprint="c" * 64,
        entry_hypothesis_json=hypothesis_json,
        entry_hypothesis_hash=hashlib.sha256(hypothesis_json.encode()).hexdigest(),
        primary_anchor_snapshot=primary.model_dump(mode="json"),
        invalidation_anchor_snapshot=invalidation.model_dump(mode="json"),
        entry_profile_id=hypothesis.entry_profile_id,
        compiled_geometry_fingerprint=fingerprint(geometry),
    )
    plan = apply_entry_financing(
        plan, executable_notional_cap_usdt=executable_notional_cap_usdt
    )
    if provenance:
        return plan
    return plan.model_copy(
        update={
            "analysis_context_fingerprint": None,
            "entry_hypothesis_json": None,
            "entry_hypothesis_hash": None,
            "primary_anchor_snapshot": None,
            "invalidation_anchor_snapshot": None,
            "entry_profile_id": None,
            "compiled_geometry_fingerprint": None,
        }
    )


def _resolved_risk_policy() -> SimpleNamespace:
    """Minimal Risk-policy mock matching the real defaults `apply_entry_financing`
    freezes into a Plan, so recomputed financing cannot drift on fixture noise."""
    return SimpleNamespace(
        policy=SimpleNamespace(
            policy_id="demo-v1", revision=1,
            capital=SimpleNamespace(max_per_symbol_entries=3),
            margin=SimpleNamespace(min_entry_liquidation_buffer_pct=5.0),
        ),
        policy_hash="risk-hash",
    )


def _authority_violations(proposal, *, capital_base, resolved_policy) -> list[str]:
    from aletheia.config.loader import load_config
    from aletheia.risk import engine

    return engine._entry_plan_authority_violations(
        proposal, capital_base=capital_base, resolved_policy=resolved_policy, cfg=load_config(),
    )


def _with_geometry_hash(payload: dict) -> dict:
    from aletheia.trading.entry_analysis import fingerprint

    result = dict(payload)
    result["compiled_geometry_fingerprint"] = fingerprint(
        {
            "entry_zone": tuple(float(value) for value in result["entry_zone"]),
            "invalidation_price": float(result["invalidation_price"]),
            "stop_price": float(result["stop_price"]),
            "target_price": float(result["target_price"]),
            "trigger_params": result["trigger_params"],
        }
    )
    return result


@pytest.mark.parametrize("failure", ["entry_plan_unique", "storage", "check", "proposal_identity"])
def test_proposal_race_rebinds_receipt_only_for_the_entry_plan_unique_conflict(monkeypatch, failure):
    import sqlite3

    from aletheia.execution.entry_plan_execution import execute_entry_plan

    plan = _persist_waiting_plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT")
    insert = repo.insert_trade_proposal
    submitted = []
    winner_id = "concurrent-entry-proposal"

    def racing_insert(proposal):
        # Another caller publishes after the first read but before this insert.
        insert(proposal.model_copy(update={"id": winner_id}))
        if failure == "entry_plan_unique":
            insert(proposal)  # Real SQLite constraint, not a fabricated race exception.
        elif failure == "storage":
            raise sqlite3.OperationalError("disk I/O error")
        elif failure == "check":
            error = sqlite3.IntegrityError("CHECK constraint failed: proposal_invariant")
            error.sqlite_errorcode = sqlite3.SQLITE_CONSTRAINT_CHECK
            raise error
        else:
            raise ValueError("proposal commit hash does not match immutable content")

    def submit(proposal_id, _mode, *, execution_receipt, current_market=None):
        assert execution_receipt.hash_is_valid()
        assert execution_receipt.proposal_id == proposal_id == winner_id
        submitted.append(proposal_id)
        return {"status": "submitted"}

    monkeypatch.setattr(repo, "insert_trade_proposal", racing_insert)
    monkeypatch.setattr("aletheia.execution.gateway.execute_existing_proposal", submit)
    now = datetime.now(timezone.utc)
    kwargs = dict(bid=99.9, ask=100.1, observed_at=now.isoformat(),
        source_observed_at=now.isoformat(), stream_generation_id="stream-1",
        subscription_generation_id="subscription-1", quote_sequence=1, now=now)
    if failure == "entry_plan_unique":
        assert execute_entry_plan(plan.plan_id, **kwargs)["status"] == "submitted"
        assert submitted == [winner_id]
    else:
        expected = {"storage": sqlite3.OperationalError, "check": sqlite3.IntegrityError,
                    "proposal_identity": ValueError}[failure]
        with pytest.raises(expected):
            execute_entry_plan(plan.plan_id, **kwargs)
        assert submitted == []
    assert repo.get_trade_proposal_by_entry_plan(plan.plan_id)["id"] == winner_id


@pytest.mark.parametrize("tactic", ["POST_ONLY_LIMIT", "OPPOSING_BBO_IOC"])
def test_triggered_plan_cannot_submit_from_caller_authored_geometry(monkeypatch, tactic):
    from aletheia.execution.entry_plan_execution import execute_entry_plan

    monkeypatch.setattr("aletheia.trading.entry_activation.require_entry_trigger_activation", _actual_activation_gate)

    params = {**_plan().trigger_params, "geometry_fingerprint": "f" * 64,
              "geometry_version": "anchor-retrace-reclaim-geometry/v1",
              "sweep_low": 98.5, "sweep_high": 99.0, "atr_abs": 2.0}
    plan = _persist_waiting_plan(tactic=tactic, plan_updates={
        "trigger_spec_id": "anchor-retrace-reclaim", "trigger_params": params,
    })
    reached_gateway = []
    monkeypatch.setattr("aletheia.execution.gateway.execute_existing_proposal",
        lambda *args, **kwargs: reached_gateway.append(args) or {"status": "submitted"})
    now = datetime.now(timezone.utc)
    try:
        execute_entry_plan(plan.plan_id, bid=100, ask=100.05,
            observed_at=now.isoformat(), source_observed_at=now.isoformat(), now=now,
            trigger_activation_id="caller-authored-activation",
            trigger_activation_hash="f" * 64,
            **_execution_receipt_kwargs(now, depth=True))
    except ValueError as exc:
        assert str(exc) == "entry_trigger_activation_required"
    assert reached_gateway == [], "caller-authored geometry reached Gateway without activation"
    assert repo.get_trade_proposal_by_entry_plan(plan.plan_id) is None
    assert repo.get_entry_plan(plan.plan_id)["status"] == "waiting"


@pytest.mark.parametrize("exposure_id", [None, "historical-treatment"])
def test_historical_treatment_never_grants_current_execution(monkeypatch, exposure_id):
    from aletheia.trading.entry_compat import decode_entry_plan
    payload = _plan().model_dump(mode="json")
    payload.update(version="entry-plan/v1", trigger_spec_version="v2",
                   trigger_spec_id="momentum-retrace-reclaim", treatment_exposure_id=exposure_id)
    historical = decode_entry_plan(json.dumps(payload))
    monkeypatch.setattr("aletheia.execution.entry_plan_execution._require_ai_entry_authority", _actual_ai_entry_authority)
    def no_experiment_authority(*args):
        raise AssertionError("Historical experiment ledger cannot authorize current execution")
    monkeypatch.setattr("extensions.learning.ledger.get_exposure", no_experiment_authority)
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="ai_entry_running_contract_mismatch"):
        build_entry_plan_order_intent(historical, bid=100, ask=100.05,
            observed_at=now.isoformat(), source_observed_at=now.isoformat(), now=now)


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


def test_urgent_entry_uses_frozen_limit_with_current_opposing_bbo():
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
    assert proposal.price == plan.ioc_limit_price
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
    with pytest.raises(ValueError, match="bbo_worst_price_exceeded"):
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
    direction,
    invalidation,
    stop_price,
    target_price,
    error,
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
        EntryPlan(**_with_geometry_hash(payload))


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
        EntryPlan(**_with_geometry_hash(payload))


def test_realized_liquidity_comes_only_from_private_fill_evidence():
    from tests.helpers.fill_reports import reports

    base = {
        "orderId": "EX-1",
        "tradeId": "fill-1",
        "fillPrice": "100",
        "fillQuantity": "1",
        "fee": "0.01",
        "feeCoin": "USDT",
        "cTime": "1787788800000",
    }
    assert reports([{**base, "tradeScope": "M"}])[0].liquidity_side == "maker"
    assert reports([base])[0].liquidity_side is None


def test_canonical_risk_enforces_the_bound_entry_plan_envelope(monkeypatch):
    from types import SimpleNamespace

    from aletheia.risk import engine

    plan = _plan()
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "active"},
    )
    monkeypatch.setattr(
        engine,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 1},
    )
    exact = build_entry_plan_order_intent(
        plan,
        bid=100,
        ask=100.05,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source_observed_at=datetime.now(timezone.utc).isoformat(),
    )
    # A proposal beyond BOTH frozen ceilings: the financed notional ceiling and
    # the frozen loss budget the AI Risk Intent governs (#3729).
    assert plan.sizing_resolution is not None
    loss_budget = plan.sizing_resolution.effective_loss_budget_usdt
    assert loss_budget is not None and isinstance(exact.price, (int, float))
    worst_quantity = 2.0 * float(loss_budget) / abs(float(exact.price) - plan.stop_price)
    proposal = exact.model_copy(update={"quantity": worst_quantity})
    resolved = _resolved_risk_policy()

    violations = _authority_violations(
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
def test_canonical_risk_rejects_proposal_execution_fields_outside_entry_plan(monkeypatch, field, value, violation):
    from types import SimpleNamespace

    from aletheia.risk import engine

    plan = _plan()
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "active"},
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
    resolved = _resolved_risk_policy()

    violations = _authority_violations(
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
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "active"},
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
    resolved = _resolved_risk_policy()

    assert "entry_plan_worst_price_exceeded" in _authority_violations(
        proposal, capital_base=1_000, resolved_policy=resolved
    )


@pytest.mark.parametrize("school", ["short_term", "serenity"])
def test_canonical_risk_accepts_exact_intent_and_bounded_size_reduction(monkeypatch, school):
    from types import SimpleNamespace

    from aletheia.risk import engine

    plan = _plan()
    if school == "serenity":
        plan = EntryPlan.model_validate({**plan.model_dump(), "source_kind": "serenity",
            "source_id": "serenity-opportunity", "trade_school": school, "holding_horizon_hours": 2160})
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "active"},
    )
    monkeypatch.setattr(
        engine,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 1},
    )
    resolved = _resolved_risk_policy()
    proposal = build_entry_plan_order_intent(
        plan,
        bid=100,
        ask=100.05,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source_observed_at=datetime.now(timezone.utc).isoformat(),
    )

    assert _authority_violations(proposal, capital_base=1_000, resolved_policy=resolved) == []
    # Leverage is frozen by the financing resolution (#3729 §10): only the size
    # may shrink, and a smaller size is still verified rather than re-financed.
    assert proposal.quantity is not None
    reduced = proposal.model_copy(update={"quantity": proposal.quantity / 2})
    assert _authority_violations(reduced, capital_base=1_000, resolved_policy=resolved) == []
    for forged in (proposal.model_copy(update={"trade_school": "forged"}),
                   proposal.model_copy(update={"opportunity_id": "other-source"})):
        assert "entry_plan_order_identity_mismatch" in _authority_violations(
            forged, capital_base=1_000, resolved_policy=resolved
        )


def test_canonical_risk_accepts_execution_fields_from_a_new_approved_plan(monkeypatch):
    from types import SimpleNamespace

    from aletheia.risk import engine

    original = _plan()
    changed_payload = {
        **original.model_dump(),
        "plan_id": "new-plan-version",
        "target_price": 106,
        "expected_horizon_hours": 6,
    }
    changed = EntryPlan.model_validate(_with_geometry_hash(changed_payload))
    payload, digest = entry_plan_payload(changed)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "active"},
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
    resolved = _resolved_risk_policy()

    assert _authority_violations(proposal, capital_base=1_000, resolved_policy=resolved) == []


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
    resolved = _resolved_risk_policy()

    assert "entry_plan_limit_price_mismatch" in _authority_violations(
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
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "active"},
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
    resolved = _resolved_risk_policy()

    violations = _authority_violations(
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
    executable_notional_cap_usdt=None,
    historical_policy_v1=False,
) -> EntryPlan:
    from aletheia.universe.candidate_opportunity import CandidateOpportunityContext

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
    repo._insert("universe_snapshots", snapshot.model_dump())
    detected_at = datetime.now(timezone.utc).isoformat()
    mechanism = "reversal" if candidate_family == "mean_reversion" else "continuation"
    opportunity = CandidateOpportunityContext(
        candidate_id=candidate_id,
        candidate_selection={
            # #3708 Batch C: the canonical policy identity is unsuffixed; a
            # freshly built context must state it, not the retired v1 label.
            "policy": "candidate_selection_utility",
            "policy_epoch": "epoch-1",
            "source_record_hash": "a" * 64,
        },
        source={
            "kind": "universe", "id": candidate_id, "trade_school": "short_term",
            "detected_at": detected_at,
            "source_cutoff_at": detected_at,
            "feature_snapshot_id": "fixture",
        },
        opportunity={
            "primary_mechanism": mechanism,
            "mechanism_version": f"{mechanism}/v1",
            "mechanism_source": "target_authority",
            "source_component_id": candidate_family,
            "direction": "long",
            "horizon": "4h",
            "economic_utility_pct": 1.0,
            "capability_status": "supported",
            "capability_gaps": (),
            "primary_evidence_refs": (),
            "secondary_mechanisms": (),
        },
        context={"regime": "trending"},
    )
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
        candidate_selection_policy="candidate_selection_utility_v1",
        candidate_selection_policy_epoch="epoch-1",
        candidate_selection_utility_pct=1.0,
        opportunity_mechanism=mechanism,
        opportunity_mechanism_version=f"{mechanism}/v1",
        opportunity_source_component=candidate_family,
        opportunity_source_cutoff_at=detected_at,
        opportunity_evidence_hash="b" * 64,
        candidate_opportunity_context_version=opportunity.version,
        candidate_opportunity_context_json=opportunity.model_dump_json(),
        candidate_opportunity_context_hash=opportunity.context_hash,
    )
    payload = candidate.model_dump()
    if historical_policy_v1:
        # A frozen-schema fixture must write its rows the way that schema wrote
        # them: the v86 CHECKs predate the #3708 rename and the model boundary
        # normalizes the retired label on every write, so the historical values
        # are restored here instead of being re-derived.
        legacy_context = CandidateOpportunityContext(
            candidate_id=candidate_id,
            candidate_selection={
                **opportunity.candidate_selection,
                "policy": "candidate_selection_utility_v1",
            },
            source=opportunity.source,
            opportunity=opportunity.opportunity,
            context=opportunity.context,
        )
        payload["candidate_selection_policy"] = "candidate_selection_utility_v1"
        payload["candidate_opportunity_context_json"] = legacy_context.model_dump_json()
        payload["candidate_opportunity_context_hash"] = legacy_context.context_hash

    def _write(table: str, data: dict) -> None:
        if historical_policy_v1 and "candidate_selection_policy" in data:
            data = {**data, "candidate_selection_policy": "candidate_selection_utility_v1"}
        original_insert(table, data)

    original_insert = repo._insert
    try:
        if historical_policy_v1:
            repo._insert = _write
        repo._insert("universe_members", payload)
        generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    finally:
        repo._insert = original_insert
    repo.claim_next_candidate_planning("owner")
    plan = _plan(
        account_mode=account_mode,
        mode=mode,
        tactic=tactic,
        executable_notional_cap_usdt=executable_notional_cap_usdt,
    ).model_copy(
        update={
            "planning_generation_id": generation["id"],
            "candidate_id": candidate_id,
            "candidate_family": candidate_family,
            **(plan_updates or {}),
        }
    )
    from aletheia.storage.models import EntryPlanRecord
    payload, digest = entry_plan_payload(plan)
    # Execution fixture starts from an already admitted immutable Plan. Compiler
    # and planning admission are exercised independently with frozen contexts.
    repo._insert("entry_plans", EntryPlanRecord(id=plan.plan_id,
        planning_generation_id=generation["id"], candidate_id=candidate_id,
        planning_key=generation["planning_key"], venue=plan.venue, symbol=plan.symbol,
        market_type=plan.market_type, direction=plan.direction, execution_mode=plan.execution_mode,
        status="active" if plan.activation_policy == "IMMEDIATE" else "waiting",
        plan_json=payload, plan_hash=digest).model_dump())
    repo.execute_write("UPDATE candidate_planning_generations SET status='planned', entry_plan_id=? WHERE id=?",
                       (plan.plan_id, generation["id"]))
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
        ("bitget_demo", "active", "approved", False, False, "invalidated", True),
        ("bitget_demo", "active", "approved", True, False, "active_claim", False),
        ("bitget_demo", "active", "executing", False, False, "external_execution", False),
        ("bitget_demo", "active", "executed", False, False, "external_execution", False),
        ("bitget_demo", "active", "executing", False, True, "order_present", False),
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
        ((now + timedelta(seconds=60)).isoformat() if claim_active else (now - timedelta(seconds=1)).isoformat()),
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
    assert repo.get_entry_plan(plan.plan_id)["status"] == ("invalidated" if authority_released else plan_status)
    assert (repo.get_entry_risk_reservation(proposal.id) is None) is authority_released
    assert (repo.get_proposal_execution_claim(proposal.id) is None) is authority_released
    assert repo.has_order_for_proposal(proposal.id) is order_present


def test_execution_lineage_does_not_mix_concurrent_fill_commit(tmp_path, monkeypatch):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from aletheia.storage.models import Position
    from tests.test_sqlite_writer import _setup_db

    db = _setup_db(tmp_path, monkeypatch)
    plan, order = _persist_ordered_plan()
    position = Position(symbol=order.symbol, market_type=order.market_type, direction=order.direction,
                        quantity=1, entry_price=100, execution_mode=order.execution_mode,
                        proposal_id=order.proposal_id)
    repo.insert_position(position)
    path = db.execute("PRAGMA database_list").fetchone()[2]
    release = Event()

    def commit_fill():
        assert release.wait(timeout=5)
        with sqlite3.connect(path, timeout=5) as writer:
            writer.execute("UPDATE orders SET position_id=? WHERE id=?", (position.id, order.id))
            writer.execute("UPDATE positions SET order_id=? WHERE id=?", (order.id, position.id))
            writer.execute("INSERT INTO fills(id,order_id,fill_price,fill_quantity,fee,fee_currency,filled_at) VALUES(?,?,?,?,?,?,?)",
                           ("concurrent-fill", order.id, 100, 1, 0, "USDT", datetime.now(timezone.utc).isoformat()))

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(commit_fill)

        def between_order_and_fill_reads(sql):
            if "FROM fills" in sql and not release.is_set():
                release.set()
                future.result(timeout=5)

        db.set_trace_callback(between_order_and_fill_reads)
        try:
            before = repo.get_entry_plan_execution_lineage([plan.plan_id])[plan.plan_id]
            assert future.done(), "writer must commit before the reader continues"
        finally:
            db.set_trace_callback(None)
            release.set()
        future.result(timeout=5)
    assert before["execution_state"] == "ORDER_WORKING"
    assert before["fills"]["ids"] == []
    after = repo.get_entry_plan_execution_lineage([plan.plan_id])[plan.plan_id]
    assert after["execution_state"] == "POSITION_OPEN"
    assert after["fills"]["ids"] == ["concurrent-fill"]


def test_archived_execution_lineage_keeps_position_and_checks_cash_evidence(tmp_path, monkeypatch):
    from tests.test_sqlite_writer import _setup_db
    from aletheia.storage.models import Position

    _setup_db(tmp_path, monkeypatch)
    plan, order = _persist_ordered_plan()
    repo.execute_write("UPDATE orders SET execution_mode='paper_internal' WHERE id=?", (order.id,))
    position = Position(symbol=order.symbol, market_type=order.market_type, direction=order.direction,
                        quantity=1, entry_price=100, current_price=105, execution_mode="paper_internal",
                        order_id=order.id, proposal_id=order.proposal_id, entry_fee=0, exit_fee=0,
                        funding_cost=0, funding_source="local_simulation", realized_pnl=5)
    repo._insert("positions", position.model_dump())
    repo.execute_write("UPDATE orders SET position_id=? WHERE id=?", (position.id, order.id))
    missing_fill = repo.get_entry_plan_execution_lineage([plan.plan_id])[plan.plan_id]
    assert missing_fill["execution_state"] == "UNAVAILABLE"
    assert missing_fill["availability"]["reason"] == "entry_fill_relation_missing"
    fill = Fill(order_id=order.id, fill_price=100, fill_quantity=1)
    repo.insert_fill(fill)
    result = repo.get_entry_plan_execution_lineage([plan.plan_id])[plan.plan_id]
    assert result["execution_state"] == "POSITION_OPEN"
    assert result["fills"]["ids"] == [fill.id]
    close = Order(symbol=order.symbol, market_type=order.market_type, direction="short", order_type="market",
                  quantity=1, price=105, execution_mode="paper_internal", source="adopted",
                  position_id=position.id, close_reason="take_profit", status="filled")
    repo._insert("orders", close.model_dump())
    close_fill = Fill(order_id=close.id, fill_price=105, fill_quantity=1)
    repo.insert_fill(close_fill)
    repo.execute_write("UPDATE positions SET status='closed', closed_at=? WHERE id=?", (close_fill.filled_at, position.id))
    repo.execute_write("INSERT INTO position_close_events(id,position_id,realized_pnl,is_partial) VALUES(?,?,?,0)",
                       ("close-event", position.id, 5))
    before = repo.get_entry_plan_execution_lineage([plan.plan_id])[plan.plan_id]
    assert before["economics"]["status"] == "unavailable"
    repo._insert("paper_account_events", dict(id="archived-close-cash", action="position_closed", amount_usdt=4, cash_balance_before_usdt=100, cash_balance_after_usdt=104,
                                                     position_id=position.id))
    mismatch = repo.get_entry_plan_execution_lineage([plan.plan_id])[plan.plan_id]
    assert mismatch["economics"]["status"] == "unavailable"
    repo._insert("paper_account_events", dict(id="archived-manual-cash", action="manual_credit", amount_usdt=1, cash_balance_before_usdt=104, cash_balance_after_usdt=105,
                                                     position_id=position.id))
    settled = repo.get_entry_plan_execution_lineage([plan.plan_id])[plan.plan_id]
    assert settled["economics"]["status"] == "complete"
    assert settled["execution_state"] == "CLOSED"
    assert settled["close_fills"][0]["id"] == close_fill.id
    assert settled["position"]["id"] == position.id



def test_execution_lineage_distinguishes_no_fill_from_missing_order(tmp_path, monkeypatch):
    from tests.test_sqlite_writer import _setup_db

    _setup_db(tmp_path, monkeypatch)
    plan, order = _persist_ordered_plan()
    repo.execute_write("UPDATE orders SET status='cancelled' WHERE id=?", (order.id,))
    lineage = repo.get_entry_plan_execution_lineage([plan.plan_id])[plan.plan_id]
    assert lineage["availability"]["available"] is True
    assert lineage["fills"]["quantity"] == 0
    assert lineage["execution_state"] == "NO_FILL"
    repo.execute_write("UPDATE entry_plans SET order_id=NULL WHERE id=?", (plan.plan_id,))
    repo.execute_write("DELETE FROM orders WHERE id=?", (order.id,))
    lineage = repo.get_entry_plan_execution_lineage([plan.plan_id])[plan.plan_id]
    assert lineage["availability"]["available"] is False
    assert lineage["execution_state"] == "UNAVAILABLE"


def _persist_ordered_plan() -> tuple[EntryPlan, Order]:
    from tests.uta.test_execution_coordinator import _identity
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
        uta_account_identity_json=_identity("bitget_demo").model_dump_json(),
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

def _cancel_ack(command):
    from aletheia.uta.models import CommandReport, CommandEvidence
    return CommandReport(identity=command.expected_account, command_id=command.command_id,
                         evidence=CommandEvidence.ACKNOWLEDGED, received_at=datetime.now(timezone.utc),
                         venue_order_id=command.target_venue_order_id, profile_version="1")


def test_order_transition_does_not_write_entry_plan_authority(monkeypatch):
    plan, order = _persist_ordered_plan()
    before = repo.get_entry_plan(plan.plan_id)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Order owner cannot write Plan authority")
    monkeypatch.setattr(repo, "transition_entry_plan", forbidden)
    repo.transition_order(order.id, status="filled")
    assert repo.get_order(order.id)["status"] == "filled"
    assert repo.get_entry_plan(plan.plan_id) == before



@pytest.mark.parametrize("clock", ["observed_at", "source_observed_at"])
def test_stale_market_cannot_advance_active_plan(clock):
    from aletheia.execution.entry_plan_execution import execute_entry_plan

    plan = _persist_waiting_plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT")
    now = datetime.now(timezone.utc)
    timestamps = dict(observed_at=now.isoformat(), source_observed_at=now.isoformat())
    timestamps[clock] = (now - timedelta(seconds=5)).isoformat()
    with pytest.raises(ValueError, match="entry_exact_state_stale"):
        execute_entry_plan(plan.plan_id, bid=100, ask=100.05, **timestamps,
            stream_generation_id="stream-1", subscription_generation_id="subscription-1", quote_sequence=1)
    assert repo.get_entry_plan(plan.plan_id)["status"] == "active"
    assert repo.get_trade_proposal_by_entry_plan(plan.plan_id) is None


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
    assert (
        repo.record_entry_plan_monitoring(
            plan.plan_id,
            {
                **evidence,
                "updated_at": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
            },
        )
        is False
    )
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




def test_gateway_cancel_rechecks_fill_and_fill_wins(monkeypatch):
    from aletheia.execution import coordinator, gateway

    plan, order = _persist_ordered_plan()

    def fill_during_cancel(command):
        repo.insert_fill(Fill(order_id=order.id, fill_price=100, fill_quantity=1, fee_source="reconciled"))
        repo.transition_order(order.id, status="filled")
        return _cancel_ack(command)

    monkeypatch.setattr(coordinator, "cancel_order", fill_during_cancel)
    result = gateway.cancel_entry_plan_order(plan.plan_id, reason="price_invalidated")
    assert result["status"] == "filled"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "ordered"
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
    repo.transition_order(order.id, status="filled")

    result = gateway.cancel_entry_plan_order(plan.plan_id, reason="price_invalidated")

    assert result["status"] == "filled"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "ordered"


def test_gateway_cancel_keeps_partial_fill_and_closes_remainder(monkeypatch):
    from aletheia.execution import coordinator, gateway
    from aletheia.execution import exchange_materialization, reconciliation
    from aletheia.uta import manager
    from aletheia.uta.models import OrderLifecycle, ReadCompleteness

    plan, order = _persist_ordered_plan()

    monkeypatch.setattr(coordinator, "cancel_order", _cancel_ack)

    async def exact_order(*_args, **_kwargs):
        return SimpleNamespace(
            identity=_identity("bitget_demo"),
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
        repo.transition_order(order_id, status="partially_filled")

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
    assert repo.get_entry_plan(plan.plan_id)["status"] == "ordered"
    assert repo.get_order(order.id)["status"] == "cancelled"
    assert materialized == [{"orderId": "exchange-1", "fillQuantity": "0.5"}]
    assert sum(fill["fill_quantity"] for fill in repo.list_fills(order_id=order.id)) == 0.5


def test_gateway_cancel_ack_without_complete_private_evidence_stays_pending(monkeypatch):
    from aletheia.execution import coordinator, gateway
    from aletheia.uta import manager
    from aletheia.uta.models import ReadCompleteness

    plan, order = _persist_ordered_plan()
    cancels = []
    monkeypatch.setattr(coordinator, "cancel_order", lambda command: cancels.append(command) or _cancel_ack(command))

    async def incomplete_order(*_args, **_kwargs):
        return SimpleNamespace(
            identity=_identity("bitget_demo"),
            completeness=ReadCompleteness.UNAVAILABLE,
            found=None,
        )

    monkeypatch.setattr(manager, "query_uta", incomplete_order)
    result = gateway.cancel_entry_plan_order(plan.plan_id, reason="price_invalidated")
    assert result["status"] == "cancel_pending"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "ordered"
    assert repo.get_order(order.id)["status"] == "submitted"

    assert repo.get_order(order.id)["error_reason"] == "entry_cancel_pending:price_invalidated"
    # A new invocation reads durable cancellation uncertainty and only queries.
    assert gateway.cancel_entry_plan_order(plan.plan_id, reason="retry")["status"] == "cancel_pending"
    assert len(cancels) == 1


def test_gateway_cancel_waits_when_order_quantity_leads_fill_history(monkeypatch):
    from aletheia.execution import coordinator, gateway, reconciliation
    from aletheia.uta import manager
    from aletheia.uta.models import OrderLifecycle, ReadCompleteness

    plan, order = _persist_ordered_plan()
    monkeypatch.setattr(coordinator, "cancel_order", _cancel_ack)

    async def terminal_order(*_args, **_kwargs):
        return SimpleNamespace(
            identity=_identity("bitget_demo"),
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

    from tests.uta.test_execution_coordinator import _identity
    monkeypatch.setattr(reconciliation, "build_uta_registry", lambda: SimpleNamespace(
        configured_identities=lambda: (_identity("bitget_demo"),)))
    plan, order = _persist_ordered_plan()
    repo.update_order_metadata(
        order.id,
        order_tactic="OPPOSING_BBO_IOC",
        time_in_force="ioc",
    )
    repo.execute_write("UPDATE orders SET source='native' WHERE id=?", (order.id,))
    repo.transition_order(order.id, status=starting_status)

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
    assert repo.get_entry_plan(plan.plan_id)["status"] == "ordered"  # historical Plan authority is unchanged


def test_reconciliation_waits_for_lagging_ioc_fill_history(monkeypatch):
    from aletheia.execution import reconciliation
    from aletheia.uta.models import OrderLifecycle

    from tests.uta.test_execution_coordinator import _identity
    monkeypatch.setattr(reconciliation, "build_uta_registry", lambda: SimpleNamespace(
        configured_identities=lambda: (_identity("bitget_demo"),)))
    plan, order = _persist_ordered_plan()
    repo.update_order_metadata(
        order.id,
        order_tactic="OPPOSING_BBO_IOC",
        time_in_force="ioc",
    )
    repo.execute_write("UPDATE orders SET source='native' WHERE id=?", (order.id,))

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


def test_resting_maker_receipt_has_plan_lifetime_not_trigger_ttl():
    from aletheia.execution.entry_plan_execution import build_entry_execution_receipt

    plan = _plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT")
    now = datetime.now(timezone.utc)
    plan = plan.model_copy(update={"approved_at": (now - timedelta(seconds=3)).isoformat()})
    facts = dict(bid=99.9, ask=100.1, observed_at=now.isoformat(), source_observed_at=now.isoformat())
    proposal = build_entry_plan_order_intent(plan, **facts, now=now)
    receipt = build_entry_execution_receipt(plan, proposal, **facts,
        stream_generation_id="stream", subscription_generation_id="subscription",
        quote_sequence=1, required_channels=("quote",))
    assert receipt.expires_at == plan.expires_at
    assert proposal.price == plan.limit_price


def test_current_capture_persists_two_immutable_legs_and_exact_remainder(monkeypatch):
    from aletheia.execution import gateway
    from aletheia.execution.entry_plan_execution import execute_entry_plan
    from aletheia.storage.models import Position, TradeProposal
    from tests.test_entry_execution_projection import plan_for

    from aletheia.storage.models import EntryPlanRecord
    from tests.test_entry_planner import _typed_candidate
    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    plan = plan_for(monkeypatch).model_copy(update={
        "planning_generation_id": generation["id"], "candidate_id": candidate.id})
    payload, digest = entry_plan_payload(plan)
    repo._insert("entry_plans", EntryPlanRecord(id=plan.plan_id,
        planning_generation_id=generation["id"], candidate_id=candidate.id,
        planning_key=generation["planning_key"], venue=plan.venue, symbol=plan.symbol,
        market_type=plan.market_type, direction=plan.direction, execution_mode=plan.execution_mode,
        status="active", plan_json=payload, plan_hash=digest).model_dump())
    before = repo.get_entry_plan(plan.plan_id)["plan_json"]
    receipts, orders = [], []

    def handoff(proposal_id, mode, *, execution_receipt, **kwargs):
        proposal = TradeProposal.model_validate(repo.get_trade_proposal(proposal_id))
        decision = RiskDecision(proposal_id=proposal_id, approved=True, allowed_execution_mode=mode,
            proposal_execution_mode=mode, decision_role="authoritative", evaluated_quantity=proposal.quantity,
            proposal_commit_hash=proposal.commit_hash, execution_receipt_json=execution_receipt.model_dump_json(),
            execution_receipt_hash=execution_receipt.receipt_hash)
        repo.insert_risk_decision(decision)
        order = Order(proposal_id=proposal_id, risk_decision_id=decision.id, symbol=proposal.symbol,
            market_type=proposal.market_type, direction=proposal.direction, execution_mode=mode,
            order_type="limit", price=proposal.price, quantity=proposal.quantity, client_order_id=proposal_id,
            entry_plan_id=plan.plan_id, order_tactic=proposal.order_tactic, post_only=proposal.post_only,
            status="submitted", exchange_order_id=f"native-{len(orders)}")
        repo.insert_order(order)
        receipts.append(execution_receipt)
        orders.append(order)
        return {"status": "submitted"}

    monkeypatch.setattr(gateway, "execute_existing_proposal", handoff)
    def wake(ask=100.01):
        now = datetime.now(timezone.utc)
        return execute_entry_plan(plan.plan_id, bid=99.99, ask=ask,
            observed_at=now.isoformat(), source_observed_at=now.isoformat(),
            **_execution_receipt_kwargs(now, depth=True))

    result = wake()
    assert result["status"] == "submitted", (result, plan.model_dump())
    first = orders[0]
    assert wake()["status"] == "waiting_market"
    assert len(orders) == 1
    fill = Fill(order_id=first.id, fill_price=first.price, fill_quantity=first.quantity*.4, fee_source="reconciled")
    repo.insert_fill(fill)
    position = Position(symbol=first.symbol, market_type=first.market_type, direction=first.direction,
        execution_mode=first.execution_mode, quantity=fill.fill_quantity, entry_price=first.price,
        order_id=first.id, proposal_id=first.proposal_id, entry_fill_verified=True, entry_price_source="broker_fill")
    repo.insert_position(position)
    repo.update_order_metadata(first.id, position_id=position.id)
    # Canonical reconciliation has confirmed cancellation and materialized all private fills.
    repo.execute_write("UPDATE orders SET status='cancelled' WHERE id=?", (first.id,))
    result = wake(ask=100.)
    assert result["status"] == "submitted", result
    second = orders[1]
    assert second.quantity == pytest.approx(plan.target_quantity*.6)
    assert second.price == plan.ioc_limit_price
    assert second.order_tactic == "OPPOSING_BBO_IOC" and not second.post_only
    assert first.proposal_id != second.proposal_id and first.risk_decision_id != second.risk_decision_id
    assert receipts[0].receipt_hash != receipts[1].receipt_hash
    assert [repo.get_trade_proposal(order.proposal_id)["entry_leg_index"] for order in orders] == [0, 1]
    assert wake()["status"] == "reconciliation_required"
    repo.execute_write("UPDATE orders SET status='cancelled' WHERE id=?", (second.id,))
    assert wake()["status"] == "cancelled"
    assert len(orders) == 2
    lineage = repo.get_entry_plan_execution_lineage([plan.plan_id])[plan.plan_id]
    assert lineage["availability"]["available"]
    assert [item["id"] for item in lineage["orders"]] == [first.id, second.id]
    assert lineage["fills"]["ids"] == [fill.id]
    assert lineage["position"]["id"] == position.id
    assert lineage["execution_state"] == "POSITION_OPEN"
    from pydantic import TypeAdapter
    from aletheia.server.routes.universe import EntryPlanLineage
    adapter = TypeAdapter(EntryPlanLineage)
    assert adapter.dump_python(adapter.validate_python(lineage), mode="json") == lineage
    assert repo.get_entry_plan(plan.plan_id)["plan_json"] == before


def test_cancel_handoff_crash_persists_uncertainty_before_external_effect(monkeypatch):
    from aletheia.execution import coordinator, gateway
    from aletheia.uta import manager
    from aletheia.uta.models import ReadCompleteness

    plan, order = _persist_ordered_plan()
    class ProcessExit(BaseException):
        pass
    calls = []
    def crash(command):
        assert repo.get_order(order.id)["error_reason"] == "entry_cancel_pending:passive_window_elapsed"
        calls.append(command)
        raise ProcessExit()
    monkeypatch.setattr(coordinator, "cancel_order", crash)
    with pytest.raises(ProcessExit):
        gateway.cancel_entry_plan_order(plan.plan_id, reason="passive_window_elapsed")
    async def unavailable(*args):
        return SimpleNamespace(completeness=ReadCompleteness.UNAVAILABLE, found=None)
    monkeypatch.setattr(manager, "query_uta", unavailable)
    assert gateway.cancel_entry_plan_order(plan.plan_id, reason="restart")["status"] == "cancel_pending"
    assert len(calls) == 1


@pytest.mark.parametrize("historical_eligible", [False, True])
def test_first_passive_eligibility_survives_flips_without_inventing_history(historical_eligible):
    plan = _persist_waiting_plan()
    if historical_eligible:
        historical = json.dumps({"state": "warming", "passive_entry": {"eligible": True, "ever_eligible": True}})
        repo.get_db().execute("UPDATE entry_plans SET monitoring_evidence_json=? WHERE id=?", (historical, plan.plan_id))
        repo.get_db().commit()
    first = "2026-09-11T15:00:00+00:00"
    expected = None if historical_eligible else first
    evidence = {"state": "ready", "passive_entry": {"eligible": True, "observed_at": first}}
    assert repo.record_entry_plan_monitoring(plan.plan_id, evidence)
    for eligible, at in ((False, "2026-09-11T15:00:01+00:00"), (True, "2026-09-11T15:00:02+00:00")):
        assert repo.record_entry_plan_monitoring(plan.plan_id, {"state": "ready", "passive_entry": {"eligible": eligible, "observed_at": at}})
        value = json.loads(repo.get_entry_plan(plan.plan_id)["monitoring_evidence_json"])["passive_entry"]
        assert value["ever_eligible"] is True
        assert value["first_eligible_at"] == expected
    assert repo.record_entry_plan_monitoring(plan.plan_id, {"state": "warming"})
    value = json.loads(repo.get_entry_plan(plan.plan_id)["monitoring_evidence_json"])["passive_entry"]
    assert value["first_eligible_at"] == expected


# --- Entry Plan cancel convergence (#3507) -------------------------------------
#
# A cancel that never lands used to leave the Order in `submitted` forever while
# the watcher re-read the venue every ~30s, rebuilding an adapter each time and
# re-earning the venue rate limit. The reconciling path now re-attempts the
# idempotent cancel when the venue still holds the Order, inside a backoff window,
# and re-anchors a credential-generation drift only after that venue proof.


def _venue_report(state, *, filled=0.0, identity=None):
    from aletheia.uta.models import ReadCompleteness

    return SimpleNamespace(
        identity=identity or _identity("bitget_demo"),
        completeness=ReadCompleteness.COMPLETE,
        found=SimpleNamespace(state=state, filled_quantity=filled),
    )


def _install_cancel_path(monkeypatch, *, report, cancels, queries):
    from aletheia.execution import coordinator, gateway, reconciliation
    from aletheia.uta import manager

    monkeypatch.setattr(coordinator, "cancel_order", lambda command: cancels.append(command) or _cancel_ack(command))

    async def exact_fills(self, **_kwargs):
        return []

    monkeypatch.setattr(reconciliation._UTAReconciliationProjection, "get_order_fills", exact_fills)

    async def exact_order(*_args, **_kwargs):
        queries.append(1)
        return report() if callable(report) else report

    monkeypatch.setattr(manager, "query_uta", exact_order)
    return gateway


def _expire_cancel_retry_window(order_id: str) -> None:
    repo.update_order_metadata(order_id, entry_cancel_retry_after="2026-01-01T00:00:00+00:00")


def test_gateway_cancel_reattempts_idempotent_cancel_until_venue_confirms(monkeypatch):
    """The venue still holding the Order must not strand the Plan in `pending`."""
    from aletheia.uta.models import OrderLifecycle

    plan, order = _persist_ordered_plan()
    cancels, queries = [], []
    state = {"value": OrderLifecycle.OPEN}
    gateway = _install_cancel_path(
        monkeypatch, report=lambda: _venue_report(state["value"]), cancels=cancels, queries=queries
    )

    assert gateway.cancel_entry_plan_order(plan.plan_id, reason="passive_window_elapsed")["status"] == "cancel_pending"
    assert len(cancels) == 1, "one submission per call: the venue has not processed the first cancel yet"
    pending_row = repo.get_order(order.id)
    assert pending_row is not None
    assert pending_row["entry_cancel_attempts"] == 1
    assert pending_row["entry_cancel_retry_after"] is not None
    assert pending_row["status"] == "submitted"

    _expire_cancel_retry_window(order.id)
    assert gateway.cancel_entry_plan_order(plan.plan_id, reason="passive_window_elapsed")["status"] == "cancel_pending"
    assert len(cancels) == 2, "the venue still holds the Order, so the cancel is re-attempted once the window elapses"
    reattempt_row = repo.get_order(order.id)
    assert reattempt_row is not None and reattempt_row["entry_cancel_attempts"] == 2

    _expire_cancel_retry_window(order.id)
    state["value"] = OrderLifecycle.CANCELED
    assert gateway.cancel_entry_plan_order(plan.plan_id, reason="passive_window_elapsed")["status"] == "cancelled"
    cancelled_row = repo.get_order(order.id)
    assert cancelled_row is not None and cancelled_row["status"] == "cancelled"
    assert len(cancels) == 2, "a venue-confirmed cancel is not submitted again"


def test_gateway_cancel_retry_window_skips_the_venue_read(monkeypatch):
    """Inside the backoff window the Watcher must not rebuild an adapter."""
    from aletheia.uta.models import OrderLifecycle

    plan, _order = _persist_ordered_plan()
    cancels, queries = [], []
    gateway = _install_cancel_path(
        monkeypatch,
        report=lambda: _venue_report(OrderLifecycle.OPEN),
        cancels=cancels,
        queries=queries,
    )

    gateway.cancel_entry_plan_order(plan.plan_id, reason="passive_window_elapsed")
    reads_after_first = len(queries)

    assert gateway.cancel_entry_plan_order(plan.plan_id, reason="passive_window_elapsed")["status"] == "cancel_pending"
    assert len(queries) == reads_after_first, "no venue read happens inside the backoff window"
    assert len(cancels) == 1


def test_gateway_cancel_reanchors_generation_drift_only_after_exact_order_proof(monkeypatch):
    """A rotated credential generation must not block the cancel forever."""
    from aletheia.uta.models import OrderLifecycle

    plan, order = _persist_ordered_plan()
    persisted = _identity("bitget_demo")
    repo.update_order_metadata(order.id, uta_account_identity_json=persisted.model_dump_json())
    drifted = persisted.model_copy(update={"credential_generation": persisted.credential_generation + 1})

    cancels, queries = [], []
    gateway = _install_cancel_path(
        monkeypatch,
        report=lambda: _venue_report(OrderLifecycle.OPEN, identity=drifted),
        cancels=cancels,
        queries=queries,
    )

    assert gateway.cancel_entry_plan_order(plan.plan_id, reason="passive_window_elapsed")["status"] == "cancel_pending"
    assert repo.list_audit_events(limit=50, event_type="uta_account_reanchored") == [], (
        "the first call only submits; the drift is re-anchored when a venue read authorises a re-attempt"
    )

    _expire_cancel_retry_window(order.id)
    assert gateway.cancel_entry_plan_order(plan.plan_id, reason="passive_window_elapsed")["status"] == "cancel_pending"

    reanchors = [event for event in repo.list_audit_events(limit=50, event_type="uta_account_reanchored")]
    assert len(reanchors) == 1
    assert reanchors[0]["entity_id"] == order.id
    assert cancels[-1].expected_account == drifted, "the re-attempt is fenced with the current generation"
    reanchored_row = repo.get_order(order.id)
    assert reanchored_row is not None
    assert json.loads(reanchored_row["uta_account_identity_json"])["credential_generation"] == (
        drifted.credential_generation
    )


def test_gateway_cancel_fails_closed_on_a_hard_identity_mismatch(monkeypatch):
    """A different account is never re-anchored, and no cancel is re-attempted."""
    from aletheia.uta.models import OrderLifecycle

    plan, order = _persist_ordered_plan()
    persisted = _identity("bitget_demo")
    repo.update_order_metadata(order.id, uta_account_identity_json=persisted.model_dump_json())
    other = persisted.model_copy(update={"account_id": "bitget_demo-other"})

    cancels, queries = [], []
    gateway = _install_cancel_path(
        monkeypatch,
        report=lambda: _venue_report(OrderLifecycle.OPEN, identity=other),
        cancels=cancels,
        queries=queries,
    )

    assert gateway.cancel_entry_plan_order(plan.plan_id, reason="passive_window_elapsed")["status"] == "cancel_pending"

    assert repo.list_audit_events(limit=50, event_type="uta_account_reanchored") == []
    assert len(cancels) == 1, "only the first, unanswered attempt was submitted"
    untouched_row = repo.get_order(order.id)
    assert untouched_row is not None and untouched_row["status"] == "submitted"


def test_gateway_cancel_backoff_does_not_hide_a_local_fill(monkeypatch):
    """A parked retry window must never outrank a locally materialized Fill."""
    plan, order = _persist_ordered_plan()
    repo.insert_fill(Fill(order_id=order.id, fill_price=100, fill_quantity=float(order.quantity), fee_source="reconciled"))
    repo.transition_order(order.id, status="submitted", error_reason="entry_cancel_pending:passive_window_elapsed")
    repo.update_order_metadata(order.id, entry_cancel_retry_after="2099-01-01T00:00:00+00:00")

    from aletheia.execution import gateway

    assert gateway.cancel_entry_plan_order(plan.plan_id, reason="passive_window_elapsed")["status"] == "filled"
