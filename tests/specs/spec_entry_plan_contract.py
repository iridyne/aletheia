"""Executable architecture claims for the canonical Entry Plan lifecycle."""

import inspect

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


@pytest.fixture(autouse=True)
def _pin_account_authority(monkeypatch):
    """The canonical sizing path reads account authority a test root cannot have.

    `compile_entry_plan` → `aletheia.risk.engine.account_sizing_state` fails
    closed (`SIZING_CAPITAL_UNAVAILABLE`) when the cached exchange-account
    snapshot is missing, which is the correct production behaviour and the
    reason this module pins the same fixture capital the other entry tests use
    (#3729).
    """
    monkeypatch.setattr("aletheia.risk.engine.account_equity_for_execution", lambda *a, **k: 5000.0)
    monkeypatch.setattr("aletheia.risk.engine._account_equity", lambda *a, **k: 5000.0)
    monkeypatch.setattr("aletheia.risk.engine.account_available_for_execution", lambda *a, **k: 5000.0)


@spec_claim(
    SpecClaim(
        "EP-S01",
        "decision_lifecycle_contract",
        "§2.1a durable Candidate planning",
        "L2a",
        "Universe and Serenity share the Core snapshot/member writer and PlanningGeneration owner without a parallel executable Opportunity.",
        status="implemented",
    )
)
def test_universe_publication_has_one_entry_planning_owner():
    from aletheia.universe.service import UniverseService, _ensure_planning_generations
    from aletheia.trading.serenity_publication import publish_serenity_candidates

    source = inspect.getsource(UniverseService.refresh)
    helper_source = inspect.getsource(_ensure_planning_generations)
    assert "_ensure_planning_generations" in source
    assert "ensure_candidate_planning_generation" in helper_source
    serenity_source = inspect.getsource(publish_serenity_candidates)
    assert "repo.publish_candidate_snapshot(" in source
    assert "repo.publish_candidate_snapshot(" in serenity_source
    assert "repo.ensure_candidate_planning_generation(" in serenity_source
    assert "emit_deterministic_opportunities" not in source


@spec_claim(
    SpecClaim(
        "EP-S10", "entry_plan_contract", "AI Entry hypothesis authority", "L2a",
        "One canonical Entry planner fails closed on machine provenance or shared runtime authority mismatch.",
        status="implemented",
    )
)
def test_canonical_ai_entry_has_one_frozen_input_and_execution_gate():
    from aletheia.config.schema import AgentConfig
    from aletheia.execution.entry_plan_execution import _require_ai_entry_authority
    from aletheia.server.routes.settings import router as settings_router
    from aletheia.storage.repositories import (
        bind_candidate_planner_input,
        ensure_candidate_planning_generation,
    )
    from aletheia.trading.entry_plan_compiler import compile_entry_plan
    from aletheia.trading.entry_plan import EntryPlan

    assert "planner_input_hash" in inspect.getsource(bind_candidate_planner_input)
    authority_source = inspect.getsource(_require_ai_entry_authority)
    assert "ai_entry_runtime_authority_unavailable" in authority_source
    assert "require_entry_runtime_authority" in authority_source
    assert "planner_input_json" in authority_source
    assert "activation_identity" not in authority_source
    generation_source = inspect.getsource(ensure_candidate_planning_generation)
    assert "entry_contract_fingerprints" in generation_source
    assert "ai_entry_hypothesis" not in generation_source
    assert "ai_entry_hypothesis_enabled" not in AgentConfig.model_fields
    assert all(route.path != "/ai-entry-hypothesis" for route in settings_router.routes)
    assert "candidate" not in inspect.signature(compile_entry_plan).parameters
    assert "feature" not in inspect.signature(compile_entry_plan).parameters
    assert "analysis_context_fingerprint" in EntryPlan.model_fields


@spec_claim(
    SpecClaim(
        "EP-S02",
        "decision_lifecycle_contract",
        "§1 Triggered hot path",
        "L2a",
        "Fast Monitoring consumes local/stream facts without LLM or remote provider calls and routes cancellation through Gateway.",
        status="implemented",
    )
)
def test_fast_monitoring_is_deterministic_and_gateway_owned():
    from aletheia.agent_runtime.fast_trigger_watch import (
        _load_entry_plan_watchers,
        _prewarm_market_structure,
        _watch_loop,
    )

    source = "\n".join(
        inspect.getsource(value) for value in (_load_entry_plan_watchers, _prewarm_market_structure, _watch_loop)
    )
    assert "llm_" not in source.lower()
    assert "get_market_data_provider" not in source
    assert "cancel_entry_plan_order" in source
    assert "BitgetClient" not in source


@spec_claim(
    SpecClaim(
        "EP-S03",
        "execution_contract",
        "§3.0a Entry Plan Order Policy",
        "L2a",
        "Entry Plan execution creates one immutable internal Order Intent and delegates Risk and venue execution to the canonical Gateway.",
        status="implemented",
    )
)
def test_entry_plan_execution_reuses_canonical_gateway():
    from aletheia.execution import entry_plan_execution

    source = inspect.getsource(entry_plan_execution.execute_entry_plan)
    assert "get_trade_proposal_by_entry_plan" in source
    assert "execute_existing_proposal" in source
    assert "evaluate_proposal" not in source
    assert "BitgetClient" not in source


@spec_claim(
    SpecClaim(
        "EP-S04",
        "decision_lifecycle_contract",
        "§4 DL-INV-37 Candidate planning priority",
        "L2b",
        "Entry Planner claims the current exact Candidate rank first and falls back "
        "deterministically to generation created_at then id; an empty queue reports "
        "idle with zero planning attempts. Does not exercise the 3-15 "
        "concurrent-worker batch claiming/planning itself, or NULL-rank ordering "
        "(`universe_members.rank` is NOT NULL, so it cannot occur).",
        status="implemented",
    )
)
def test_candidate_planning_claim_uses_exact_rank_with_stable_fallback(tmp_path, monkeypatch):
    """Claims in exact rank order, falling back to created_at then id on ties.

    Row-id order (P, S, R, Q), generation created_at order (R, Q, P, S), and the
    correct claim order (Q, P, S, R) are all mutually distinct, so neither a
    naive "insertion order" nor a naive "oldest first" claim implementation can
    pass by coincidence. Ranks: P=1, Q=1 (tie, broken by created_at — the
    "stable fallback" half of the claim), S=5, R=10 (worst, but created first —
    defeats naive oldest-first). A NULL rank is not included: the
    `CASE WHEN um.rank IS NULL` ordering branch is unreachable dead code given
    `universe_members.rank INTEGER NOT NULL` (verified: a direct
    `UPDATE ... SET rank=NULL` raises `sqlite3.IntegrityError` against this
    schema), so this test does not claim to exercise it.
    """
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env
    from tests.test_entry_planner import _typed_candidate

    setup_env(tmp_path, monkeypatch)

    from aletheia.agent_shell.entry_planner import (
        ENTRY_PLANNER_MAX_CONCURRENCY,
        ENTRY_PLANNER_MIN_CONCURRENCY,
        run_entry_planner_batch,
    )
    from aletheia.storage import repositories as repo

    assert ENTRY_PLANNER_MIN_CONCURRENCY == 3
    assert ENTRY_PLANNER_MAX_CONCURRENCY == 15

    base = datetime.now(timezone.utc)
    candidate_p = _typed_candidate(mechanism="continuation", family="momentum", symbol="BTC/USDT", asset_id="asset-p", rank=1)
    candidate_s = _typed_candidate(mechanism="continuation", family="momentum", symbol="ETH/USDT", asset_id="asset-s", rank=5)
    candidate_r = _typed_candidate(mechanism="continuation", family="momentum", symbol="SOL/USDT", asset_id="asset-r", rank=10)
    candidate_q = _typed_candidate(mechanism="continuation", family="momentum", symbol="XRP/USDT", asset_id="asset-q", rank=1)

    repo.ensure_candidate_planning_generation(candidate_p.id, now=base + timedelta(seconds=2))
    repo.ensure_candidate_planning_generation(candidate_s.id, now=base + timedelta(seconds=3))
    repo.ensure_candidate_planning_generation(candidate_r.id, now=base)
    repo.ensure_candidate_planning_generation(candidate_q.id, now=base + timedelta(seconds=1))

    claim_at = base + timedelta(seconds=10)
    claimed_ids: list[str] = []
    for index in range(4):
        claimed = repo.claim_next_candidate_planning(f"owner-{index}", now=claim_at)
        assert claimed is not None
        claimed_ids.append(claimed["last_candidate_id"])
    assert claimed_ids == [candidate_q.id, candidate_p.id, candidate_s.id, candidate_r.id]

    import asyncio

    idle_empty = asyncio.run(run_entry_planner_batch())
    assert idle_empty["status"] == "idle"
    assert idle_empty["planning_attempts"] == 0


@spec_claim(
    SpecClaim(
        "EP-S05",
        "entry_plan_contract",
        "Exact-venue execution eligibility",
        "L2a",
        "Execution eligibility fails closed when Fast Monitoring cannot consume the Candidate's exact physical venue.",
        status="implemented",
    )
)
def test_exact_venue_capability_is_one_shared_executable_invariant():
    from aletheia.config.loader import load_config
    from aletheia.universe.fast_stream import (
        fast_market_data_unavailability_reason,
    )

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "bitget_demo"
    cfg.market_data.ccxt_exchange = "binance"
    assert fast_market_data_unavailability_reason(cfg, "binance") == "FAST_MARKET_DATA_VENUE_MISMATCH"
    assert fast_market_data_unavailability_reason(cfg, "bitget") is None
    cfg.market_data.ccxt_exchange = "bitget"
    assert fast_market_data_unavailability_reason(cfg, "bitget") is None


@spec_claim(
    SpecClaim(
        "EP-S06",
        "entry_plan_contract",
        "Canonical hypothesis authority and failure semantics",
        "L2a",
        "The canonical hypothesis exposes a strict wire matrix and canonicalizes non-authoritative NO_PLAN execution fields.",
        status="implemented",
    )
)
def test_canonical_hypothesis_and_trigger_registry_expose_complete_authority():
    import json

    from aletheia.model_provider.base import portable_json_schema_payload
    from aletheia.trading.entry_analysis import EntryHypothesis

    schema = portable_json_schema_payload(EntryHypothesis)
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert schema["properties"]["entry_profile_id"]["type"] == ["string", "null"]
    encoded = json.dumps(schema)
    assert "$ref" not in encoded and "$defs" not in encoded and "anyOf" not in encoded
    assert set(schema["properties"]) == set(EntryHypothesis.model_fields)
    no_plan = EntryHypothesis.model_validate({
            "decision": "NO_PLAN",
            "reason_code": "CONFLICTING_SIGNALS",
            "mechanism": "continuation",
            "direction": "long",
            "primary_anchor_ref": "anchor:ignored",
            "invalidation_anchor_ref": "anchor:ignored-too",
            "entry_profile_id": "anchor-resting-maker",
            "capture_priority": "PRICE_FIRST",
            "risk_tier": "LOW",
            "horizon": "INTRADAY",
            "supporting_fact_refs": [],
            "opposing_fact_refs": [],
            "learning_evidence_refs": [],
        })
    assert no_plan.primary_anchor_ref is None
    assert no_plan.invalidation_anchor_ref is None
    assert no_plan.entry_profile_id is None
    assert no_plan.capture_priority is None
    assert no_plan.risk_tier == "NONE"
    assert no_plan.horizon == "NONE"


@spec_claim(
    SpecClaim(
        "EP-S07",
        "entry_plan_contract",
        "Deep-tier provider admission",
        "L2a",
        "Entry Planner captures one deep endpoint/model pair; capacity uses its endpoint while inference retains the pair.",
        status="implemented",
    )
)
def test_entry_planner_batch_captures_one_deep_provider_identity():
    from aletheia.agent_shell.entry_planner import run_entry_planner_batch

    source = inspect.getsource(run_entry_planner_batch)
    assert source.count('get_provider_for_tier("deep")') == 1
    assert "available_capacity( provider_name," in " ".join(source.split())
    assert "provider_name = selection.endpoint_id" in source
    assert "provider_name=selection" in source


@spec_claim(
    SpecClaim(
        "EP-S09",
        "entry_plan_contract",
        "Machine-derived Entry contract identity",
        "L2a",
        "Candidate generations are reusable only under current schema and behavior fingerprints.",
        status="implemented",
    )
)
def test_candidate_planning_generation_binds_machine_contract_identity():
    from aletheia.storage.models import CandidatePlanningGeneration
    from aletheia.storage.repositories import ensure_candidate_planning_generation

    assert "entry_behavior_fingerprint" in CandidatePlanningGeneration.model_fields
    assert "schema_fingerprint" in CandidatePlanningGeneration.model_fields
    source = inspect.getsource(ensure_candidate_planning_generation)
    assert "entry_contract_fingerprints" in source
    assert "existing.get(\"entry_behavior_fingerprint\")" in source
@spec_claim(
    SpecClaim(
        "EP-S08",
        "entry_plan_contract",
        "Read-only Momentum geometry measurement",
        "L2a",
        "Paired Momentum replay proves Candidate-price control geometry, requires causal 20+1 bars, preserves the full denominator, and cannot grant production authority.",
        status="implemented",
    )
)
def test_momentum_geometry_measurement_is_read_only_and_fail_closed():
    from aletheia.analytics.momentum_geometry_replay import (
        CONTROL_IDENTITY,
        TREATMENT_IDENTITY,
        _build_momentum_geometry_replay,
        build_momentum_geometry_replay,
        measurement_contract,
    )

    contract = measurement_contract()
    assert contract["control_version"] == CONTROL_IDENTITY
    assert contract["treatment_version"] == TREATMENT_IDENTITY
    assert contract["no_entry_utility"] == 0.0
    assert contract["historical_microstructure"].startswith("unavailable")
    public_source = inspect.getsource(build_momentum_geometry_replay)
    load_source = inspect.getsource(_build_momentum_geometry_replay)
    assert "readonly_db" in public_source
    assert "get_ohlcv_candles" in load_source
    assert "include_provenance=True" in load_source
    assert "insert_" not in load_source
    assert "execute_write" not in load_source


@spec_claim(
    SpecClaim(
        "EP-S13", "entry_plan_contract", "Contract identity and planning cycles", "L2b",
        "Stable discovery ownership permits one successor on newer confirmed structure, preserving the old failed input and outcome.",
        status="implemented",
    )
)
def test_legacy_terminal_failure_requalifies_on_confirmed_structure(_inmemory_db):
    from tests.test_entry_lifecycle_cutover import _legacy_structure_candidate, _seed_requalification_bars, _bind_test_context
    from aletheia.storage import repositories as repo
    from aletheia.trading.entry_plan import candidate_planning_identity

    first_at, _, later_at = _seed_requalification_bars()
    candidate = _legacy_structure_candidate(first_at.isoformat())
    first, _ = repo.ensure_candidate_planning_generation(candidate.id, now=first_at)
    _bind_test_context(repo, first)
    repo.execute_write("UPDATE candidate_planning_generations SET status='unavailable', "
        "result_kind='unavailable', outcome_class='planning_failed', result_json='{}', "
        "attention_status='terminal',attempt_count=3 WHERE id=?", (first["id"],))
    frozen = repo.get_candidate_planning_generation(first["id"])
    later = _legacy_structure_candidate(later_at.isoformat())
    assert candidate_planning_identity(candidate.model_dump()) == candidate_planning_identity(later.model_dump())
    second, created = repo.ensure_candidate_planning_generation(later.id, now=later_at)
    assert created, "New confirmed PA material must be expressed independently of discovery identity"
    assert second["planning_key"] == first["planning_key"]
    assert second["previous_generation_id"] == first["id"] and second["cycle_number"] == 2
    old = repo.get_candidate_planning_generation(first["id"])
    for field in ("planner_input_json", "planner_input_hash", "result_json", "attempt_count", "outcome_class"):
        assert old[field] == frozen[field]


@spec_claim(
    SpecClaim(
        "EP-S12",
        "entry_plan_contract",
        "Operator observability",
        "L2a",
        "The Workbench preserves persisted per-Plan monitoring evidence and reports immutable Plan integrity.",
        status="implemented",
    )
)
def test_workbench_projects_monitoring_and_plan_integrity():
    from aletheia.server.routes.universe import _entry_plan_projection
    from aletheia.trading.entry_plan import entry_plan_payload
    from tests.test_entry_plan_execution import _plan

    payload, digest = entry_plan_payload(_plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT"))
    row = {"plan_json": payload, "plan_hash": digest, "monitoring_state": "unavailable",
           "monitoring_evidence_json": '{"reason":"market_fact_stale"}'}
    projected = _entry_plan_projection(row)
    assert projected["contract_valid"] is True
    assert projected["monitoring_state"] == "unavailable"
    assert projected["monitoring_evidence_json"] == row["monitoring_evidence_json"]
    assert projected["plan_json"] == payload and projected["plan_hash"] == digest
    assert "contract_valid" not in row
    assert _entry_plan_projection({**row, "plan_hash": "0" * 64})["contract_valid"] is False



@spec_claim(SpecClaim(
    "EP-S14", "entry_plan_contract", "Market-quality retry before input binding", "L2a",
    "Thin exact books release the same generation without model/input consumption; recovery binds a new qualified book.",
    status="implemented",
))
def test_market_quality_retry_before_input_binding(monkeypatch):
    from aletheia.config.loader import load_config, save_config
    from tests.test_entry_planner import test_market_quality_wait_does_not_bind_and_recovers_same_generation
    cfg = load_config()
    cfg.market_data.ccxt_exchange = "bitget"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    cfg.agent.permission_mode = "Full"
    save_config(cfg)
    result = test_market_quality_wait_does_not_bind_and_recovers_same_generation(monkeypatch, "thin")
    assert result is not None, "market-quality retry delegation must return its stored generation"


@spec_claim(SpecClaim(
    "EP-S15", "entry_plan_contract", "Static executable geometry and capability", "L2a",
    "Compiler shares Risk stop floor; Spot short stops before inference; refresh retains birth context owner.",
    status="implemented",
))
def test_static_entry_feasibility_and_birth_owner(monkeypatch):
    from aletheia.config.loader import load_config, save_config
    from tests.test_entry_planner import (
        test_spot_short_blocks_before_market_model_and_compiler,
        test_unbound_candidate_refresh_preserves_birth_context_owner,
    )
    from tests.test_entry_plan_execution import test_compiled_holding_is_separate_from_swing_timing
    cfg = load_config()
    cfg.market_data.ccxt_exchange = "bitget"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    cfg.agent.permission_mode = "Full"
    save_config(cfg)
    test_unbound_candidate_refresh_preserves_birth_context_owner()
    test_spot_short_blocks_before_market_model_and_compiler(monkeypatch)
    monkeypatch.setattr("aletheia.execution.entry_plan_execution._require_ai_entry_authority", lambda _plan: None)
    plan = test_compiled_holding_is_separate_from_swing_timing(monkeypatch, "universe", "short_term", "c1", 24)
    assert plan is not None, "static feasibility delegation must return its compiled plan"


@spec_claim(SpecClaim("EP-B16", "entry_plan_contract", "Pre-broker submission authority (#3096)", "L2b",
    "Entry preparation waits without external effects; only a fenced final market check starts submission.", status="implemented"))
@pytest.mark.parametrize("scenario", ["slow", "crossing", "stale", "unavailable", "expired", "owner_lost", "duplicate_wake", "unknown", "venue_rejected", "boundary_rollback", "boundary_reason_message", "boundary_venue_text", "invalidated", "dependency_timeout", "adapter_timeout"])
def test_pre_broker_boundary_counterexamples(monkeypatch, scenario):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import RiskDecision
    from tests.test_entry_plan_execution import _persist_waiting_plan
    monkeypatch.setattr("aletheia.execution.entry_plan_execution._require_ai_entry_authority", lambda plan: None)
    monkeypatch.setattr("aletheia.trading.entry_activation.require_entry_trigger_activation", lambda *a, **kw: None)
    from aletheia.execution import gateway, coordinator
    from aletheia.execution.entry_plan_execution import execute_entry_plan
    from aletheia.universe.fast_stream import FastMarketEvent
    from aletheia.data.symbols import InstrumentId
    from aletheia.uta.models import SubmissionReport, CommandEvidence, CapabilitySupport
    from tests.uta.test_execution_coordinator import _identity

    plan = _persist_waiting_plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT")
    initial = datetime.now(timezone.utc)
    clock = [initial]
    calls, audits, roles = [], [], []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(gateway, "datetime", Clock)
    monkeypatch.setattr(gateway, "write_audit", audits.append)
    monkeypatch.setattr(gateway, "_check_symbol_availability", lambda *a: None)

    def risk(proposal, *, decision_role, execution_receipt):
        assert repo.get_trade_proposal(proposal.id)["status"] == "pending"
        assert not repo.has_order_for_proposal(proposal.id)
        roles.append(decision_role)
        if scenario == "duplicate_wake":
            duplicate = gateway.execute_existing_proposal(proposal.id, proposal.execution_mode, execution_receipt=execution_receipt)
            assert duplicate["status"] == "already_claimed"
        decision = RiskDecision(proposal_id=proposal.id, approved=True, allowed_execution_mode="bitget_demo",
            proposal_execution_mode="bitget_demo", decision_role=decision_role,
            evaluated_quantity=proposal.quantity, proposal_commit_hash=repo.get_trade_proposal(proposal.id)["commit_hash"],
            execution_attempt_id=execution_receipt.attempt_id, execution_receipt_hash=execution_receipt.receipt_hash)
        repo.insert_risk_decision(decision)
        return SimpleNamespace(approved=True, allowed_execution_mode="bitget_demo", decision_id=decision.id)

    original_prepare = gateway._prepare_exchange_quantity
    def prepare(*args, **kwargs):
        if scenario == "dependency_timeout":
            from aletheia.execution import order_constraints
            def unavailable(*args, **kwargs):
                try:
                    raise TimeoutError("temporary venue metadata read")
                except TimeoutError as exc:
                    raise order_constraints.ConstraintUnavailable("metadata refresh failed") from exc
            with monkeypatch.context() as dependency:
                dependency.setattr(order_constraints, "prepare_exchange_quantity", unavailable)
                return original_prepare(*args, **kwargs)
        clock[0] = initial + timedelta(seconds=3)
        return args[3], None

    monkeypatch.setattr(gateway, "evaluate_proposal", risk)
    monkeypatch.setattr(gateway, "_prepare_exchange_quantity", prepare)

    class Adapter:
        identity = _identity("bitget_demo")
        capabilities = SimpleNamespace(native_tpsl=CapabilitySupport.SUPPORTED)

        async def close(self):
            pass

        async def submit_order(self, command):
            calls.append(command)
            assert repo.get_trade_proposal(command.proposal_id)["status"] == "executing"
            assert repo.has_order_for_proposal(command.proposal_id)
            if scenario == "unknown":
                raise TimeoutError
            return SubmissionReport(identity=self.identity, command_id=command.command_id,
                client_order_id=command.client_order_id, received_at=clock[0], profile_version="1",
                evidence=CommandEvidence.VENUE_REJECTED if scenario == "venue_rejected" else CommandEvidence.ACKNOWLEDGED,
                reason_code="post_only_rejected" if scenario == "venue_rejected" else None,
                venue_order_id=None if scenario == "venue_rejected" else "demo-order-1")

    def create_adapter(*args):
        if scenario == "adapter_timeout":
            raise TimeoutError("temporary adapter dependency")
        return Adapter()
    monkeypatch.setattr(coordinator, "_create_bound_adapter", create_adapter)

    if scenario == "boundary_rollback":
        insert = coordinator.insert_order
        def interrupted_insert(order):
            insert(order)
            failure = RuntimeError("injected_order_insert_failure")
            # A real venue failure carries one of its own bounded codes; the audit
            # record must keep it, because the exception type alone cannot separate
            # "adapter unavailable" from "venue rejected the command" (#3757).
            failure.reason_code = "injected_boundary_failure_code"  # type: ignore[attr-defined]
            raise failure
        monkeypatch.setattr(coordinator, "insert_order", interrupted_insert)
    if scenario == "boundary_reason_message":
        def refused_insert(order):
            # The entry boundary's own refusals carry no `reason_code` attribute:
            # their bounded code IS the message (`ValueError("ai_entry_…")`). The
            # runtime recorded 21 of these as a bare "(ValueError)" (#3757).
            raise ValueError("ai_entry_generation_contract_mismatch")
        monkeypatch.setattr(coordinator, "insert_order", refused_insert)
    if scenario == "boundary_venue_text":
        def leaky_insert(order):
            raise RuntimeError('bitget {"code":"40020","msg":"Parameter limit error"}')
        monkeypatch.setattr(coordinator, "insert_order", leaky_insert)

    def market():
        if scenario == "expired":
            clock[0] = datetime.fromisoformat(plan.expires_at) + timedelta(seconds=1)
        proposal = repo.get_trade_proposal_by_entry_plan(plan.plan_id)
        if scenario == "owner_lost":
            repo.execute_write("UPDATE proposal_execution_claims SET owner_token='replacement' WHERE proposal_id=?", (proposal["id"],))
        event_time = initial if scenario == "stale" else clock[0]
        event = FastMarketEvent(event_type="quote", instrument=InstrumentId.from_parts("bitget", "BTC/USDT", "perp"),
            exchange_timestamp_ms=int(event_time.timestamp()*1000), received_timestamp_ms=int(event_time.timestamp()*1000),
            sequence=2, stream_generation_id="stream", subscription_generation_id="subscription",
            bid=plan.invalidation_price if scenario == "invalidated" else 99.8,
            ask=99.9 if scenario == "crossing" else 100.1)
        return event, {"state": "unavailable" if scenario == "unavailable" else "ready"}

    facts = dict(bid=99.9, ask=100.1, observed_at=initial.isoformat(), source_observed_at=initial.isoformat(),
        stream_generation_id="stream", subscription_generation_id="subscription", quote_sequence=1,
        current_market=market)
    if scenario == "expired":
        # Expiry occurs after preparation and before the final authority check.
        def prepare_expired(*args, **kwargs):
            clock[0] = datetime.fromisoformat(plan.expires_at) + timedelta(seconds=1)
            return args[3], None
        monkeypatch.setattr(gateway, "_prepare_exchange_quantity", prepare_expired)
    result = execute_entry_plan(plan.plan_id, **facts)
    proposal = repo.get_trade_proposal_by_entry_plan(plan.plan_id)
    first_roles = ["preflight"] if scenario == "dependency_timeout" else ["preflight", "authoritative"]
    assert roles == first_roles
    if scenario in {"crossing", "stale", "unavailable", "dependency_timeout", "adapter_timeout"}:
        assert result["status"] == "waiting_market"
        assert calls == [] and not repo.has_order_for_proposal(proposal["id"])
        assert proposal["status"] == "pending"
        assert repo.get_entry_plan(plan.plan_id)["status"] == "active"
        assert repo.get_proposal_execution_claim(proposal["id"]) is None
        assert repo.get_entry_risk_reservation(proposal["id"]) is None
        original_hash = proposal["commit_hash"]
        scenario = "slow"
        recovered = execute_entry_plan(plan.plan_id, **facts)
        assert recovered["status"] == "submitted"
        assert len(calls) == 1
        assert repo.get_trade_proposal_by_entry_plan(plan.plan_id)["commit_hash"] == original_hash
        assert roles == first_roles + ["preflight", "authoritative"]
    elif scenario == "invalidated":
        assert calls == [] and not repo.has_order_for_proposal(proposal["id"])
        terminal_plan = repo.get_entry_plan(plan.plan_id)
        assert result["reason_code"] == "entry_plan_price_invalidated"
        assert terminal_plan["status"] == "invalidated"
        assert terminal_plan["terminal_reason"] == "entry_plan_price_invalidated"
    elif scenario in {"expired", "owner_lost", "boundary_rollback", "boundary_reason_message", "boundary_venue_text"}:
        assert calls == [] and not repo.has_order_for_proposal(proposal["id"])
        assert result["status"] == ("already_claimed" if scenario == "owner_lost" else "error")
    else:
        assert len(calls) == 1
        assert calls[0].limit_price == plan.limit_price
        again = execute_entry_plan(plan.plan_id, **facts)
        assert len(calls) == 1
        if scenario == "unknown":
            assert result["status"] == "submission_unknown"
            assert again["status"] == "reconciliation_required"
            assert repo.get_trade_proposal(proposal["id"])["status"] == "submission_unknown"
        elif scenario == "venue_rejected":
            assert result["status"] != "submission_unknown"
            assert repo.get_db().execute("SELECT status FROM orders WHERE proposal_id=?", (proposal["id"],)).fetchone()[0] == "submission_rejected"
        else:
            assert result["status"] == "submitted"

    if calls:
        terminal_audits = [event for event in audits
            if event.get("event_type", "").startswith("demo_order_")
            or event.get("event_type") == "execution_unresolved"]
        assert terminal_audits
        assert all(event["before"]["status"] == "executing" for event in terminal_audits)
    elif scenario.startswith("boundary_"):
        errors = [event for event in audits if event.get("event_type") == "demo_order_error"]
        assert len(errors) == 1 and errors[0]["before"]["status"] == "pending"
        expected = {
            "boundary_rollback": "demo_order_failed: execution stopped (RuntimeError):injected_boundary_failure_code",
            "boundary_reason_message": "demo_order_failed: execution stopped (ValueError):ai_entry_generation_contract_mismatch",
            # Venue text is not a bounded code: only the type is recorded.
            "boundary_venue_text": "demo_order_failed: execution stopped (RuntimeError)",
        }[scenario]
        assert errors[0]["message"] == expected, errors[0]

    if scenario == "slow":
        from aletheia.storage.models import TradeProposal
        late = gateway._terminal_execution_error(
            TradeProposal.model_validate(repo.get_trade_proposal(proposal["id"])),
            reason_code="execution_precheck_failed", safe_message="late preparation failure",
        )
        assert late["status"] == "submitted"
        assert repo.get_trade_proposal(proposal["id"])["status"] == "submitted"


@spec_claim(SpecClaim("EP-B17", "entry_plan_contract", "Bounded execution resolver (#3111)", "L2b",
    "Immediate FillFirst accepts improving BBO with a frozen limit and retains both Risk passes and the Coordinator Broker fence.", status="implemented"))
def test_fill_first_immediate_reaches_gateway_risk_and_coordinator(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from tests.test_risk_engine import setup_test_env
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.config.loader import save_config
    from aletheia.execution import gateway
    from aletheia.execution.entry_plan_execution import execute_entry_plan
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import EntryPlanRecord, MarketSnapshot
    from aletheia.trading.entry_plan import entry_plan_payload
    from aletheia.trading.entry_plan_compiler import compile_entry_plan
    from tests.test_entry_capture import capture_input
    from tests.test_entry_planner import _typed_candidate
    from tests.test_exchange_submission_integrity import _prepare_broker
    from tests.uta.test_execution_coordinator import _ack, _Adapter
    from types import SimpleNamespace
    from aletheia.uta.models import CapabilitySupport
    monkeypatch.setattr(_Adapter, "capabilities", SimpleNamespace(native_tpsl=CapabilitySupport.SUPPORTED), raising=False)
    from aletheia.universe.fast_stream import FastMarketEvent
    from aletheia.data.symbols import InstrumentId

    args = capture_input(monkeypatch, "grounded-anchor-direct", "FILL_FIRST")
    # Risk check #18 (liquidation buffer) needs a feasible tier cap: the shipped
    # demo major_cap is 20x, whose ~4.6% liquidation distance is below the 5% floor,
    # so a plan at that cap is always rejected. This spec claims the FillFirst
    # submission path, not the leverage tier, so it states a feasible policy
    # explicitly instead of relying on unconfigured defaults.
    args[-1].risk.demo.leverage.major_cap = 5
    save_config(args[-1])
    candidate = _typed_candidate(mechanism="reversal", family="mean_reversion")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    plan = compile_entry_plan(*args).model_copy(update={
        "planning_generation_id": generation["id"], "candidate_id": candidate.id})
    payload, digest = entry_plan_payload(plan)
    repo._insert("entry_plans", EntryPlanRecord(id=plan.plan_id, planning_generation_id=generation["id"],
        candidate_id=candidate.id, planning_key=generation["planning_key"], venue=plan.venue,
        symbol=plan.symbol, market_type=plan.market_type, direction=plan.direction,
        execution_mode=plan.execution_mode, status="active", plan_json=payload, plan_hash=digest).model_dump())
    # Start after planning admission. All actual Gateway/Risk/Coordinator checks remain.
    monkeypatch.setattr("aletheia.execution.entry_plan_execution.require_entry_plan_runtime_authority", lambda _: None)
    monkeypatch.setattr(gateway, "_check_symbol_availability", lambda *args: None)
    commands = []
    async def submit(command):
        commands.append(command)
        return _ack(command)
    from aletheia.execution.order_constraints import prepare_exchange_quantity
    _prepare_broker(monkeypatch, submit)
    monkeypatch.setattr("aletheia.execution.order_constraints.prepare_exchange_quantity", prepare_exchange_quantity)
    repo.insert_market_snapshot(MarketSnapshot(
        symbol=plan.symbol, market_type="usdt_futures", exchange=plan.venue, price=100.,
    ))
    now = datetime.now(timezone.utc)
    ms = int(now.timestamp()*1000)
    quote = FastMarketEvent(event_type="quote", instrument=InstrumentId.from_parts(plan.venue, plan.symbol, plan.market_type),
        sequence=1, exchange_timestamp_ms=ms, received_timestamp_ms=ms,
        stream_generation_id="stream", subscription_generation_id="subscription", bid=99.98, ask=99.99)
    result = execute_entry_plan(plan.plan_id, bid=quote.bid, ask=quote.ask,
        observed_at=now.isoformat(), source_observed_at=now.isoformat(), stream_generation_id="stream",
        subscription_generation_id="subscription", quote_sequence=1, depth_bids=((99.98,1000.),),
        depth_asks=((99.99,1000.),), depth_exchange_timestamp_ms=ms, depth_received_timestamp_ms=ms, depth_sequence=1,
        current_market=lambda: (quote, {"state": "ready"}))
    assert len(commands) == 1, result
    proposal = repo.get_trade_proposal_by_entry_plan(plan.plan_id)
    assert proposal["entry_leg_index"] == 0 and proposal["order_tactic"] == "OPPOSING_BBO_IOC"
    assert proposal["price"] == plan.ioc_limit_price
    decisions = repo.get_db().execute("SELECT decision_role,approved FROM risk_decisions WHERE proposal_id=?", (proposal["id"],)).fetchall()
    assert {row["decision_role"] for row in decisions} == {"preflight", "authoritative"}
    assert all(row["approved"] for row in decisions)
    assert repo.get_order_for_entry_plan(plan.plan_id)["status"] == "submitted"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "active"


@spec_claim(
    SpecClaim(
        "EP-S14",
        "execution_contract",
        "§3.0a Entry Plan Order Policy (cancel convergence)",
        "L2a",
        "A cancel the venue has not honoured is re-attempted idempotently: one Order never sees two distinct cancels.",
        status="implemented",
    )
)
def test_entry_cancel_reattempt_reuses_the_client_order_identity(monkeypatch):
    """#3507: the reconciling re-attempt repeats the same Order identity."""
    from types import SimpleNamespace

    from aletheia.execution import coordinator, gateway, reconciliation
    from aletheia.storage import repositories as repo
    from aletheia.uta import manager
    from aletheia.uta.models import OrderLifecycle, ReadCompleteness
    from tests.test_entry_plan_execution import _cancel_ack, _persist_ordered_plan
    from tests.uta.test_execution_coordinator import _identity

    # Unit-level order geometry: the durable AI-entry authority gate is exercised
    # against the real store in tests/test_entry_activation.py.
    monkeypatch.setattr(
        "aletheia.execution.entry_plan_execution._require_ai_entry_authority",
        lambda _plan: None,
    )
    monkeypatch.setattr(
        "aletheia.trading.entry_activation.require_entry_trigger_activation",
        lambda *args, **kwargs: None,
    )

    plan, order = _persist_ordered_plan()
    cancels: list = []
    monkeypatch.setattr(
        coordinator,
        "cancel_order",
        lambda command: cancels.append(command) or _cancel_ack(command),
    )

    async def exact_fills(self, **_kwargs):
        return []

    async def still_working(*_args, **_kwargs):
        return SimpleNamespace(
            identity=_identity("bitget_demo"),
            completeness=ReadCompleteness.COMPLETE,
            found=SimpleNamespace(state=OrderLifecycle.OPEN, filled_quantity=0.0),
        )

    monkeypatch.setattr(reconciliation._UTAReconciliationProjection, "get_order_fills", exact_fills)
    monkeypatch.setattr(manager, "query_uta", still_working)

    gateway.cancel_entry_plan_order(plan.plan_id, reason="passive_window_elapsed")
    row = repo.get_order(order.id)
    assert row is not None
    repo.update_order_metadata(row["id"], entry_cancel_retry_after="2026-01-01T00:00:00+00:00")
    gateway.cancel_entry_plan_order(plan.plan_id, reason="passive_window_elapsed")

    assert len(cancels) == 2, "the still-working Order is re-attempted once the retry window elapses"
    identities = {(command.target_venue_order_id, command.target_client_order_id) for command in cancels}
    assert identities == {(order.exchange_order_id, order.client_order_id)}, (
        "the re-attempt must keep the same venue/client order identity, so the venue cannot see two cancels"
    )


@spec_claim(SpecClaim("EP-B18", "entry_plan_contract", "Model-visible quantitative fields (#3785)", "L2a",
    "Entry-path technical fields are present and citable when the closed window suffices, and absent plus listed "
    "in unavailable_fields (never defaulted) when the window or book input is missing.", status="implemented"))
def test_entry_quant_fields_are_present_or_listed_never_defaulted():
    from typing import Any

    from aletheia.trading.entry_analysis import ENTRY_WINDOW_BARS, entry_model_view, technical_fields
    from tests.test_entry_analysis import _technical_context

    full: dict[str, Any] = entry_model_view(_technical_context(ENTRY_WINDOW_BARS, imbalance=0.1, feature_extra={}))
    short: dict[str, Any] = entry_model_view(_technical_context({"15m": 96}, imbalance=None, feature_extra={}))
    for timeframe in ENTRY_WINDOW_BARS:
        assert set(full["quant_context"]["technical"][timeframe]["values"]) == set(technical_fields(timeframe))
        assert set(full["quant_context"]["technical"][timeframe]["fact_refs"]) <= set(
            full["reference_contract"]["current_market_fact_refs"])
    for timeframe in ("1h", "4h"):
        section = short["quant_context"]["technical"][timeframe]
        assert section["values"] == {} and section["unavailable_fields"] == sorted(technical_fields(timeframe))
    assert "imbalance_ratio" not in short["execution_context"]
    assert short["execution_context"]["unavailable_fields"] == ["imbalance_ratio"]
