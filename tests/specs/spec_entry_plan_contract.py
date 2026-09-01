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
        "PlanIntent authority and failure semantics",
        "L2a",
        "PlanIntent exposes a strict wire matrix and canonicalizes non-authoritative NO_PLAN execution fields.",
        status="implemented",
    )
)
def test_plan_intent_and_trigger_registry_expose_complete_authority():
    import json

    from aletheia.agent_shell.entry_planner import (
        PlanIntent,
        _PlanIntentWireSchema,
        _canonicalize_no_plan,
    )
    from aletheia.model_provider.base import portable_json_schema_payload
    from aletheia.trading.trigger_specs import compatible_trigger_specs

    schema = portable_json_schema_payload(_PlanIntentWireSchema)
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert schema["properties"]["order_tactic"]["type"] == ["string", "null"]
    encoded = json.dumps(schema)
    assert "$ref" not in encoded and "$defs" not in encoded and "anyOf" not in encoded
    assert set(schema["properties"]) == set(PlanIntent.model_fields)
    assert _canonicalize_no_plan(
        {
            "decision": "NO_PLAN",
            "reason_code": "CONFLICTING_SIGNALS",
            "execution_mode": "RESTING_MAKER",
            "order_tactic": "POST_ONLY_LIMIT",
            "trigger_spec_id": None,
            "trigger_spec_version": None,
            "risk_tier": "LOW",
            "horizon": "INTRADAY",
        }
    ) == {
        "decision": "NO_PLAN",
        "reason_code": "CONFLICTING_SIGNALS",
        "execution_mode": None,
        "order_tactic": None,
        "trigger_spec_id": None,
        "trigger_spec_version": None,
        "risk_tier": "NONE",
        "horizon": "NONE",
    }
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
