"""Executable architecture claims for the canonical Entry Plan lifecycle."""

import inspect

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim(
        "EP-S01",
        "decision_lifecycle_contract",
        "§2.1a durable Candidate planning",
        "L2a",
        "Universe publication creates durable Candidate Planning Generations and does not emit a parallel executable Opportunity.",
        status="implemented",
    )
)
def test_universe_publication_has_one_entry_planning_owner():
    from aletheia.universe.service import UniverseService, _ensure_planning_generations

    source = inspect.getsource(UniverseService.refresh)
    helper_source = inspect.getsource(_ensure_planning_generations)
    assert "_ensure_planning_generations" in source
    assert "ensure_candidate_planning_generation" in helper_source
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
        "deterministically, then plans a bounded 3-15 generation batch concurrently "
        "without changing planning identity or single-owner CAS.",
        status="implemented",
    )
)
def test_candidate_planning_claim_uses_exact_rank_with_stable_fallback():
    from aletheia.agent_shell.entry_planner import (
        ENTRY_PLANNER_MAX_CONCURRENCY,
        ENTRY_PLANNER_MIN_CONCURRENCY,
        run_entry_planner_batch,
    )
    from aletheia.storage.repositories import claim_next_candidate_planning

    claim_source = inspect.getsource(claim_next_candidate_planning)
    batch_source = inspect.getsource(run_entry_planner_batch)
    assert "um.id=pg.last_candidate_id" in claim_source
    assert "CASE WHEN um.rank IS NULL THEN 1 ELSE 0 END, um.rank ASC" in claim_source
    assert "pg.created_at ASC, pg.id ASC" in claim_source
    assert ENTRY_PLANNER_MIN_CONCURRENCY == 3
    assert ENTRY_PLANNER_MAX_CONCURRENCY == 15
    assert "asyncio.TaskGroup" in batch_source
    assert "group.create_task" in batch_source


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
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.market_data.ccxt_exchange = "binance"
    assert fast_market_data_unavailability_reason(cfg, "binance") is None
    assert fast_market_data_unavailability_reason(cfg, "bitget") == "FAST_MARKET_DATA_VENUE_MISMATCH"
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
    from aletheia.trading.trigger_specs import compatible_trigger_specs

    schema = portable_json_schema_payload(EntryHypothesis)
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert schema["properties"]["entry_profile_id"]["type"] == ["string", "null"]
    encoded = json.dumps(schema)
    assert "$ref" not in encoded and "$defs" not in encoded and "anyOf" not in encoded
    assert set(schema["properties"]) == set(EntryHypothesis.model_fields)
    no_plan = EntryHypothesis.model_validate({
            "version": "entry-hypothesis/v2",
            "decision": "NO_PLAN",
            "reason_code": "CONFLICTING_SIGNALS",
            "mechanism": "continuation",
            "direction": "long",
            "primary_anchor_ref": "anchor:ignored",
            "invalidation_anchor_ref": "anchor:ignored-too",
            "entry_profile_id": "anchor-resting-maker",
            "entry_profile_version": "v1",
            "execution_urgency": "PASSIVE",
            "risk_tier": "LOW",
            "horizon": "INTRADAY",
            "supporting_fact_refs": [],
            "opposing_fact_refs": [],
            "learning_evidence_refs": [],
        })
    assert no_plan.primary_anchor_ref is None
    assert no_plan.invalidation_anchor_ref is None
    assert no_plan.entry_profile_id is None
    assert no_plan.entry_profile_version is None
    assert no_plan.execution_urgency is None
    assert no_plan.risk_tier == "NONE"
    assert no_plan.horizon == "NONE"
    assert [(spec.spec_id, spec.version) for spec in compatible_trigger_specs("momentum")] == [
        ("momentum-retrace-reclaim", "v1"),
    ]
    from aletheia.trading.trigger_specs import registered_trigger_specs
    assert compatible_trigger_specs("value") == ()
    v2 = next(spec for spec in registered_trigger_specs() if spec.version == "v2")
    assert v2.planner_visible is False


@spec_claim(
    SpecClaim(
        "EP-S07",
        "entry_plan_contract",
        "Deep-tier provider admission",
        "L2a",
        "Entry Planner resolves one deep-provider alias and passes it to both capacity admission and inference workers.",
        status="implemented",
    )
)
def test_entry_planner_batch_captures_one_deep_provider_identity():
    from aletheia.agent_shell.entry_planner import run_entry_planner_batch

    source = inspect.getsource(run_entry_planner_batch)
    assert source.count('get_provider_for_tier("deep")') == 1
    assert "available_capacity(\n        provider_name" in source
    assert "provider_name=provider_name" in source


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
        "EP-S11",
        "execution_contract",
        "§3.0a Entry Plan Order Policy",
        "L2a",
        "Legacy instant-fill Paper outcomes cannot enter the paper-order-fill/v1 economic cohort.",
        status="implemented",
    )
)
def test_paper_outcome_cohort_requires_the_current_entry_fill_model():
    from aletheia.analytics import outcome_integrity

    source = inspect.getsource(outcome_integrity.assess_reportable_outcome)
    read_source = inspect.getsource(outcome_integrity.raw_account_events)
    assert "CURRENT_PAPER_FILL_MODEL_VERSION" in source
    assert "_paper_fill_model_version" in read_source
    assert "unverified_paper_fill_model" in outcome_integrity.OUTCOME_EXCLUSION_REASONS


@spec_claim(
    SpecClaim(
        "EP-S13", "entry_plan_contract", "Mechanism-aware Grounded Entry", "L2a",
        "Planning V2 owns exact mechanism authority while ordinary Candidate churn cannot duplicate ownership.",
        status="implemented",
    )
)
def test_mechanism_planning_identity_and_runtime_revalidation_are_explicit():
    from aletheia.agent_runtime.fast_trigger_watch import _load_entry_plan_watchers
    from aletheia.execution.entry_plan_execution import _require_ai_entry_authority
    from aletheia.trading.entry_plan import candidate_planning_identity

    identity_source = inspect.getsource(candidate_planning_identity)
    for field in (
        "primary_mechanism", "direction", "policy", "policy_epoch", "mechanism_version",
    ):
        assert field in identity_source
    for excluded in ("rank", "total_score", "entry_price_usd"):
        assert excluded not in identity_source
    authority_source = inspect.getsource(_require_ai_entry_authority)
    assert "candidate_opportunity_context_hash" in authority_source
    assert "candidate_selection_policy_epoch" in authority_source
    assert "mechanism_version" in authority_source
    assert "require_entry_plan_runtime_authority" in inspect.getsource(
        _load_entry_plan_watchers
    )


@spec_claim(
    SpecClaim(
        "EP-S12",
        "entry_plan_contract",
        "Operator observability",
        "L2a",
        "The Workbench projects persisted per-Plan monitoring evidence and exact-venue funding readiness.",
        status="implemented",
    )
)
def test_workbench_projects_monitoring_and_exact_funding_readiness():
    from aletheia.server.routes.universe import _entry_plan_projection
    from aletheia.storage.models import EntryPlanRecord

    source = inspect.getsource(_entry_plan_projection)
    assert "local_funding_schedule_readiness" in source
    assert "venue=plan.venue" in source
    assert "monitoring_state" in EntryPlanRecord.model_fields
    assert "monitoring_evidence_json" in EntryPlanRecord.model_fields


@spec_claim(
    SpecClaim(
        "EP-S14", "entry_plan_contract", "Mechanism-aware Grounded Entry", "L2a",
        "One V2 hypothesis repeats upstream mechanism/direction, exposes no numeric execution authority, and sees only compatible Profiles.",
        status="implemented",
    )
)
def test_grounded_v2_is_one_bounded_schema_and_profile_registry():
    from aletheia.trading.entry_analysis import EntryHypothesisV2
    from aletheia.trading.entry_profiles import compatible_entry_profiles

    assert {
        "price", "size", "quantity", "leverage", "stop_price", "target_price",
        "slippage_bps", "trigger_params",
    }.isdisjoint(EntryHypothesisV2.model_fields)
    assert set(EntryHypothesisV2.model_fields["horizon"].annotation.__args__) == {
        "INTRADAY", "NONE",
    }
    for mechanism in ("continuation", "reversal", "positioning_forced_flow"):
        assert compatible_entry_profiles(mechanism)
    assert compatible_entry_profiles("information_catalyst") == ()
