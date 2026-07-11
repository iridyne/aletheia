"""Executable claims for docs/design/filter_evidence_contract.md."""

from tests.specs.spec_meta import SpecClaim, spec_claim


_CONTRACT = "filter_evidence_contract"


@spec_claim(
    SpecClaim(
        "FE-S01", _CONTRACT, "§2 candidate identity", "L2a",
        "FilterDecision persists causal candidate, instrument, direction, filter version and decision fields.",
        status="implemented",
    )
)
def test_filter_decision_model_has_causal_identity():
    from aletheia.storage.models import FilterDecision

    required = {
        "candidate_key", "candidate_kind", "parent_candidate_key", "run_id",
        "opportunity_id", "universe_snapshot_id", "universe_member_id",
        "proposal_id", "venue", "symbol", "market_type", "direction",
        "scanner_family", "filter_name", "stage", "decision", "reason_code",
        "input_snapshot", "parameter_snapshot", "filter_version", "code_commit",
        "outcome_candidate_key", "evaluated_at",
    }
    assert required <= set(FilterDecision.model_fields)


@spec_claim(
    SpecClaim(
        "FE-S02", _CONTRACT, "§4 outcome identity", "L2a",
        "FilterCandidateOutcome carries exact instrument, fixed horizon and Stage-D net economics.",
        status="implemented",
    )
)
def test_filter_candidate_outcome_has_net_economics():
    from aletheia.storage.models import FilterCandidateOutcome

    required = {
        "outcome_candidate_key", "venue", "symbol", "market_type", "direction",
        "scanner_family", "horizon", "entry_price_usd", "entry_spread_bps",
        "funding_rate_at_entry", "entry_ts", "due_at", "gross_return_pct",
        "directional_return_pct", "estimated_fee_pct",
        "estimated_spread_slippage_pct", "estimated_funding_pct",
        "net_return_pct", "min_tradable_edge_pct", "mfe_pct", "mae_pct",
        "settlement_price_source", "settlement_lag_sec", "cost_model_version", "status",
    }
    assert required <= set(FilterCandidateOutcome.model_fields)


@spec_claim(
    SpecClaim(
        "FE-B01", _CONTRACT, "§3 append-only record", "L2b",
        "Recording the same candidate/filter/decision/version twice is idempotent and never updates the first fact.",
        status="implemented",
    )
)
def test_filter_decision_recording_is_append_only_and_idempotent():
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    kwargs = dict(
        candidate_key="opportunity:fe-idempotent",
        candidate_kind="opportunity",
        filter_name="ranker_confidence_gate",
        stage="ranker",
        decision="rejected",
        reason_code="confidence_below",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        parameters={"minimum": "medium"},
        inputs={"confidence": "speculative"},
    )
    first = record_filter_decision(**kwargs)
    second = record_filter_decision(**kwargs, reason_detail="retry must not overwrite")
    rows = get_db().execute(
        "SELECT * FROM filter_decisions WHERE candidate_key=?",
        (kwargs["candidate_key"],),
    ).fetchall()

    assert first == second
    assert len(rows) == 1
    assert rows[0]["reason_detail"] in (None, "")


@spec_claim(
    SpecClaim(
        "FE-B10", _CONTRACT, "§3 stage identity", "L2b",
        "Same-named gates at different pipeline stages persist as distinct evidence facts.",
        status="implemented",
    )
)
def test_filter_decision_stage_is_part_of_dedup_identity():
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    common = dict(
        candidate_key="pi_decision:fe-stage-identity",
        candidate_kind="pi_decision",
        filter_name="direction_bias_brake",
        decision="passed",
        reason_code="direction_not_overheated",
        symbol="BTC/USDT",
        parameters={"max_share": 0.95},
    )
    first = record_filter_decision(stage="system_operator", **common)
    second = record_filter_decision(stage="proposal_submission", **common)

    assert first != second
    assert get_db().execute(
        "SELECT COUNT(*) FROM filter_decisions WHERE candidate_key=?",
        (common["candidate_key"],),
    ).fetchone()[0] == 2


@spec_claim(
    SpecClaim(
        "FE-B02", _CONTRACT, "§4 ex-ante direction", "L2b",
        "A directionless filter candidate records the decision but creates no learnable outcome rows.",
        status="implemented",
    )
)
def test_directionless_filter_candidate_does_not_create_outcomes():
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    key = "observer:run-directionless:okx:BTC/USDT:spot"
    record_filter_decision(
        candidate_key=key,
        candidate_kind="observer_evaluation",
        filter_name="observer_pre_filter",
        stage="observer_pre_filter",
        decision="rejected",
        reason_code="no_sufficient_signal",
        symbol="BTC/USDT",
        venue="okx",
        market_type="spot",
        direction=None,
        entry_price_usd=100.0,
    )

    assert get_db().execute(
        "SELECT COUNT(*) FROM filter_candidate_outcomes WHERE outcome_candidate_key=?",
        (key,),
    ).fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "FE-B04", _CONTRACT, "§4 fixed-horizon settlement", "L2b",
        "Due filter candidate outcomes settle through the shared exact-instrument Stage-D economics path.",
        status="implemented",
    )
)
def test_filter_outcome_settler_is_wired_into_universe_settlement():
    from pathlib import Path
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    source = (
        Path(__file__).parents[2] / "aletheia" / "universe" / "outcome_recorder.py"
    ).read_text(encoding="utf-8")
    assert callable(settle_due_filter_outcomes)
    assert "settle_due_filter_outcomes" in source


@spec_claim(
    SpecClaim(
        "FE-B05", _CONTRACT, "§4 shadow projection", "L2b",
        "New shadow rows link to shared candidate outcomes and persist net economics separately from legacy gross PnL.",
        status="implemented",
    )
)
def test_shadow_trade_has_shared_net_outcome_fields():
    from aletheia.storage.models import ShadowTrade

    required = {
        "venue", "market_type", "outcome_candidate_key", "gross_return_pct",
        "net_pnl_pct", "estimated_fee_pct", "estimated_spread_slippage_pct",
        "estimated_funding_pct", "min_tradable_edge_pct", "cost_model_version",
    }
    assert required <= set(ShadowTrade.model_fields)


@spec_claim(
    SpecClaim(
        "FE-C04", _CONTRACT, "§6 net alpha consumers", "L2c",
        "Ranker and System Operator alpha baselines consume net_pnl_pct rather than legacy shadow gross PnL.",
        status="implemented",
    )
)
def test_alpha_attribution_uses_shadow_net_pnl():
    from pathlib import Path

    source = (
        Path(__file__).parents[2] / "aletheia" / "analytics" / "alpha_attribution.py"
    ).read_text(encoding="utf-8")
    assert "SELECT batch_id, rank, net_pnl_pct FROM shadow_trades" in source
    assert "SELECT net_pnl_pct FROM shadow_trades" in source


@spec_claim(
    SpecClaim(
        "FE-C05", _CONTRACT, "§6 prospective controls", "L2c",
        "Prospective alpha controls link exact-instrument shared outcomes and legacy gross-only controls are excluded.",
        status="implemented",
    )
)
def test_alpha_controls_require_shared_net_outcomes():
    from pathlib import Path

    causal_source = (
        Path(__file__).parents[2] / "aletheia" / "analytics" / "causal_controls.py"
    ).read_text(encoding="utf-8")
    alpha_source = (
        Path(__file__).parents[2] / "aletheia" / "analytics" / "alpha_attribution.py"
    ).read_text(encoding="utf-8")
    assert "outcome_candidate_key" in causal_source
    assert "SELECT net_return_pct FROM filter_candidate_outcomes" in alpha_source


@spec_claim(
    SpecClaim(
        "FE-B03", _CONTRACT, "§2 direction continuity", "L2b",
        "UniverseMember persists scanner_direction so an Observer pre-filter result is judged by an ex-ante side.",
        status="implemented",
    )
)
def test_universe_member_persists_scanner_direction():
    from aletheia.storage.models import UniverseMember

    assert "scanner_direction" in UniverseMember.model_fields


@spec_claim(
    SpecClaim(
        "FE-B06", _CONTRACT, "§5 System Operator gates", "L2b",
        "System Operator decisions carry debate, conviction and direction-brake filter traces to persistence.",
        status="implemented",
    )
)
def test_system_operator_carries_filter_trace_to_reflection():
    from pathlib import Path
    from aletheia.agent_shell.trading_decision import TradingDecision

    assert "filter_trace" in TradingDecision.__dataclass_fields__
    command_source = (
        Path(__file__).parents[2] / "aletheia" / "agent_shell" / "commands.py"
    ).read_text(encoding="utf-8")
    reflection_source = (
        Path(__file__).parents[2] / "aletheia" / "agent_shell" / "reflection.py"
    ).read_text(encoding="utf-8")
    assert '"filter_trace": decision.filter_trace' in command_source
    assert "record_operator_filter_trace" in reflection_source


@spec_claim(
    SpecClaim(
        "FE-B07", _CONTRACT, "§5 risk authority", "L2b",
        "RiskDecision remains authoritative while a report-only risk filter fact is appended after persistence.",
        status="implemented",
    )
)
def test_risk_evidence_is_appended_after_authoritative_decision():
    from pathlib import Path
    from aletheia.analytics.filter_registry import filter_by_name

    source = (
        Path(__file__).parents[2] / "aletheia" / "risk" / "engine.py"
    ).read_text(encoding="utf-8")
    assert source.index("insert_risk_decision(decision)") < source.index(
        "record_risk_filter_decision"
    )
    spec = filter_by_name("risk_engine")
    assert spec is not None
    assert spec.auto_tunable is False


@spec_claim(
    SpecClaim(
        "FE-C01", _CONTRACT, "§6 feedback isolation", "L2c",
        "Filter feedback groups complete net outcomes by filter version, family, direction, market and horizon.",
        status="implemented",
    )
)
def test_filter_feedback_uses_stratified_net_outcomes():
    from aletheia.analytics.filter_evidence import filter_feedback_stats

    assert callable(filter_feedback_stats)
    source_names = set(filter_feedback_stats.__code__.co_names)
    assert "forward_return_pct" not in source_names


@spec_claim(
    SpecClaim(
        "FE-C02", _CONTRACT, "§7 selection order", "L2c",
        "Signal selection backfills lower-ranked candidates when a higher-ranked candidate is already open or cooling down.",
        status="implemented",
    )
)
def test_signal_selection_backfills_after_early_rejection():
    from aletheia.agents.paper_trader import _plan_signal_batch

    candidates = [
        {"opportunity_id": "a", "symbol": "AAA/USDT", "direction": "long"},
        {"opportunity_id": "b", "symbol": "BBB/USDT", "direction": "long"},
        {"opportunity_id": "c", "symbol": "CCC/USDT", "direction": "long"},
        {"opportunity_id": "d", "symbol": "DDD/USDT", "direction": "long"},
    ]
    plan = _plan_signal_batch(
        candidates,
        open_symbols={"AAA/USDT"},
        recent_signal_pairs={("BBB/USDT", "long")},
        limit=2,
    )

    assert [row["opportunity_id"] for row in plan.selected] == ["c", "d"]
    assert plan.decisions["a"] == ("open_position_duplicate", "rejected")
    assert plan.decisions["b"] == ("signal_cooldown", "rejected")


@spec_claim(
    SpecClaim(
        "FE-C03", _CONTRACT, "§3 secret exclusion", "L2c",
        "Filter evidence schema has no credential, authorization, raw prompt or raw context columns.",
        status="implemented",
    )
)
def test_filter_evidence_schema_excludes_sensitive_payloads():
    from aletheia.storage.models import FilterDecision

    forbidden = {"api_key", "secret", "authorization", "prompt", "raw_prompt", "raw_context"}
    assert not (forbidden & set(FilterDecision.model_fields))


@spec_claim(
    SpecClaim(
        "FE-S03", _CONTRACT, "§3 high-volume batches", "L2a",
        "Directionless high-volume filters persist complete per-run candidate facts in immutable compressed batches.",
        status="implemented",
    )
)
def test_filter_decision_batch_has_complete_counted_payload():
    from aletheia.storage.models import FilterDecisionBatch

    required = {
        "run_id", "candidate_kind", "filter_name", "candidate_count",
        "passed_count", "rejected_count", "not_applicable_count", "error_count",
        "candidate_facts", "parameter_snapshot", "filter_version", "code_commit",
    }
    assert required <= set(FilterDecisionBatch.model_fields)


@spec_claim(
    SpecClaim(
        "FE-B08", _CONTRACT, "§5 directionless tradability gates", "L2b",
        "Ticker and tradability gates persist every evaluated candidate without inventing a post-hoc direction.",
        status="implemented",
    )
)
def test_universe_directionless_filters_accept_scanner_run_identity():
    import inspect

    from aletheia.universe.features import collect_features
    from aletheia.universe.filters import apply_tradability_filters

    assert "scanner_run_id" in inspect.signature(collect_features).parameters
    assert "scanner_run_id" in inspect.signature(apply_tradability_filters).parameters


@spec_claim(
    SpecClaim(
        "FE-B09", _CONTRACT, "§5 asset classification", "L2b",
        "Gold-backed RWA tokens remain eligible for ordinary tradability checks while USD stablecoins are excluded.",
        status="implemented",
    )
)
def test_gold_backed_assets_are_not_in_stablecoin_exclusion_set():
    from aletheia.universe.filters import _STABLECOIN_BASES

    assert "USDC" in _STABLECOIN_BASES
    assert not ({"XAUT", "PAXG"} & _STABLECOIN_BASES)


@spec_claim(
    SpecClaim(
        "FE-C06", _CONTRACT, "§5 capability gates", "L2c",
        "Directionless exchange and tradability gates are registered as report-only and never auto-tunable.",
        status="implemented",
    )
)
def test_directionless_universe_filters_are_report_only():
    from aletheia.analytics.filter_registry import filter_by_name

    names = {
        "ticker_availability",
        "ticker_volume_prefilter",
        "tradability_stablecoin",
        "tradability_blacklist",
        "tradability_volume",
        "tradability_spread",
        "tradability_freshness",
    }
    for name in names:
        spec = filter_by_name(name)
        assert spec is not None
        assert spec.feedback_source == "none"
        assert spec.auto_tunable is False


@spec_claim(
    SpecClaim(
        "FE-C07", _CONTRACT, "§8 bounded retention", "L2c",
        "High-volume analytical filter evidence has bounded cleanup while trading causal records remain permanent.",
        status="implemented",
    )
)
def test_filter_evidence_cleanup_is_wired_and_analytics_only():
    import inspect

    from aletheia.agent_runtime import scheduler
    from aletheia.storage import repositories as repo

    assert {
        "filter_decision_batches",
        "filter_decisions",
        "filter_candidate_outcomes",
    } <= set(repo._PRUNABLE)
    source = inspect.getsource(scheduler._run_cleanup)
    assert "prune_filter_decision_batches(30)" in source
    assert "prune_filter_decisions(90)" in source
    assert "prune_filter_candidate_outcomes(90)" in source


@spec_claim(
    SpecClaim(
        "FE-C08", _CONTRACT, "§5 registry completeness", "L2c",
        "Every persisted ranker and selection hard gate is present in the unified registry as feedback-ready or report-only.",
        status="implemented",
    )
)
def test_persisted_ranker_and_selection_gates_are_registered():
    from aletheia.analytics.filter_registry import filter_by_name

    names = {
        "execution_capability",
        "execution_eligibility",
        "direction_present",
        "universe_execution_eligibility",
        "family_ttl",
        "venue_symbol_capability",
        "factor_hard_block",
        "guard_candidate_block",
        "duplicate_symbol",
        "open_position_duplicate",
        "signal_cooldown",
        "signal_batch_limit",
    }
    specs = {name: filter_by_name(name) for name in names}

    assert all(specs.values())
    assert specs["family_ttl"].feedback_source == "forward_return"
    assert specs["family_ttl"].feedback_ready is True
    for name in names - {"family_ttl", "signal_cooldown", "signal_batch_limit"}:
        assert specs[name].auto_tunable is False


@spec_claim(
    SpecClaim(
        "FE-C09", _CONTRACT, "§6 first-exposure cohorts and bounded replay", "L2c",
        "Filter feedback assigns each candidate to its first persisted filter exposure, "
        "and supported threshold replays are fixed-version, read-only and bounded.",
        status="implemented",
    )
)
def test_filter_feedback_first_exposure_and_replay_bounds_are_explicit():
    import inspect

    from aletheia.analytics import filter_evidence, filter_replay

    cte = filter_evidence.FIRST_EXPOSURE_CTE_SQL
    assert "ROW_NUMBER() OVER" in cte
    assert "fd.filter_name, fd.stage, fd.filter_version, fd.outcome_candidate_key" in cte
    assert "exposure_rank=1" in inspect.getsource(filter_evidence.filter_feedback_stats).replace(" ", "")
    replay_source = inspect.getsource(filter_replay)
    assert filter_replay._MAX_ALTERNATIVES <= 4
    assert filter_replay._MAX_CANDIDATES <= 5000
    assert "filter_version is required" in replay_source
    assert '"read_only": True' in replay_source
    assert "save_config" not in replay_source


@spec_claim(
    SpecClaim(
        "FE-C10", _CONTRACT, "§6 prospective shadow experiments", "L2c",
        "Replay-ready filter changes are preregistered prospectively, evaluated on future first exposures, and can never mutate production config.",
        status="implemented",
    )
)
def test_filter_shadow_experiments_are_prospective_and_observe_only():
    import inspect

    from aletheia.analytics import filter_shadow_experiments

    source = inspect.getsource(filter_shadow_experiments)
    assert "prospective_first_exposure" in source
    assert "production_mutation_allowed" in source
    assert "save_config" not in source
    assert "ready_for_review" in source
    from aletheia.storage import migrations

    assert "idx_filter_shadow_experiments_fixed_scope" in migrations._FILTER_SHADOW_EXPERIMENTS_DDL


@spec_claim(
    SpecClaim(
        "FE-C11", _CONTRACT, "§8 operations", "L2c",
        "Doctor distinguishes outcomes waiting inside the 15-minute settler cadence from genuinely stale settlement backlog.",
        status="implemented",
    )
)
def test_filter_evidence_health_respects_settler_cadence():
    import inspect

    from aletheia.diagnostics.doctor import _probe_filter_evidence

    source = inspect.getsource(_probe_filter_evidence)
    assert "awaiting_cadence" in source
    assert "stale_overdue" in source
