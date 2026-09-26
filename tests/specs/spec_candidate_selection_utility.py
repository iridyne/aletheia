"""Executable SDD claims for Candidate Selection Utility V1."""

from __future__ import annotations

from aletheia.config.schema import UniverseConfig
from tests.specs.spec_meta import SpecClaim, spec_claim


_CONTRACT = "candidate_selection_utility"


def _feature(**overrides):
    value = {
        "asset_id": "asset-1",
        "feature_snapshot_id": "feature-1",
        "feature_snapshot_ts": "2026-09-02T00:00:00+00:00",
        "feature_source": "universe_feature_snapshot/v1",
        "registry_status": "active",
        "symbol": "BTC/USDT",
        "venue": "okx",
        "market_type": "spot",
        "last_price": 100.0,
        "quote_volume_24h": 10_000_000.0,
        "spread_bps": 5.0,
        "depth_10bps_usd": 30_000.0,
        "data_freshness_sec": 60.0,
        "return_4h": 3.0,
        "return_24h": 8.0,
        "relative_strength_24h": 5.0,
        "rsi_14": 60.0,
        "atr_pct": 2.0,
    }
    value.update(overrides)
    return value


@spec_claim(SpecClaim(
    "CSU-S01", _CONTRACT, "HardGateDecisionV1", "L2a",
    "Missing physical facts fail closed while a missing action route remains eligible observation only.",
    status="implemented",
))
def test_hard_gate_separates_physical_truth_from_actionability():
    from aletheia.universe.candidate_eligibility import evaluate_hard_gate

    missing = evaluate_hard_gate(
        _feature(depth_10bps_usd=None), UniverseConfig(),
        candidate_key="missing", evaluated_at="2026-09-02T00:00:00+00:00",
    )
    observe = evaluate_hard_gate(
        _feature(), UniverseConfig(), candidate_key="observe",
        evaluated_at="2026-09-02T00:00:00+00:00", action_route_reason="route_unavailable",
    )
    assert (missing.status, missing.actionability) == ("unavailable", "none")
    assert (observe.status, observe.actionability) == ("eligible", "observe_only")


@spec_claim(SpecClaim(
    "CSU-S02", _CONTRACT, "OpportunityEvidenceBundleV1", "L2a",
    "Six legacy labels project into four bounded mechanisms plus orthogonal context without score summation.",
    status="implemented",
))
def test_opportunity_evidence_has_four_non_summed_mechanisms():
    from aletheia.universe.opportunity_evidence import build_opportunity_evidence

    bundle = build_opportunity_evidence(
        _feature(), candidate_key="evidence", source_cutoff_at="2026-09-02T00:00:00+00:00",
    )
    assert {item.mechanism_id for item in bundle.mechanisms} == {
        "continuation", "reversal", "positioning_forced_flow", "information_catalyst",
    }
    assert bundle.context["volatility_state"]["role"] == "context_only"
    assert all(len(item.evidence_refs) == len(set(item.evidence_refs)) for item in bundle.mechanisms)


@spec_claim(SpecClaim(
    "CSU-S04", _CONTRACT, "CandidateSelectionUtilityV1", "L2a",
    "Target runtime uses exact semantic identity without economic admission; legacy is historical only.",
    status="implemented",
))
def test_target_policy_authority_is_explicit_and_fail_closed():
    from extensions.learning.registry import get_learning_path
    from aletheia.universe.candidate_selection_authority import (
        TARGET_CANDIDATE_POLICY,
        candidate_selection_authority_block_reason,
        candidate_selection_policy_identity,
    )

    path = get_learning_path("candidate_selection_utility")
    assert path.is_learning_treatment is False
    assert path.paper_authority_supported is False
    target = UniverseConfig(
        candidate_selection_policy=TARGET_CANDIDATE_POLICY,
        candidate_selection_policy_epoch="candidate-selection-utility:qualified",
    )
    from aletheia.universe.candidate_eligibility import canonical_fingerprint
    assert candidate_selection_authority_block_reason(target) == "candidate_selection_policy_epoch_mismatch"
    target.candidate_selection_policy_epoch = canonical_fingerprint(candidate_selection_policy_identity(target))
    assert candidate_selection_authority_block_reason(target) is None
    target.max_candidates += 1
    assert candidate_selection_authority_block_reason(target) == "candidate_selection_policy_epoch_mismatch"




@spec_claim(SpecClaim(
    "CSU-S05", _CONTRACT, "current Candidate bootstrap actionability", "L2c",
    "An empty Universe admits valid T0 dynamic Candidates and Planning without "
    "a slow market snapshot; downstream freshness retains its separate authority.",
    status="implemented",
))
def test_current_candidate_bootstrap_without_slow_cache(tmp_path, monkeypatch):
    from tests.test_candidate_selection_utility import test_empty_universe_bootstraps_candidate_and_planning_without_slow_cache
    test_empty_universe_bootstraps_candidate_and_planning_without_slow_cache(tmp_path, monkeypatch)


@spec_claim(SpecClaim(
    "CSU-S06", _CONTRACT, "PerpFundingEvidence", "L2a",
    "Perp Funding contract cutover preserves Spot and historical rows while current incomplete evidence fails closed.",
    status="implemented",
))
def test_perp_funding_history_cutover():
    from tests.test_candidate_funding import test_scoped_cutover_keeps_spot_and_current_incomplete_denominator
    test_scoped_cutover_keeps_spot_and_current_incomplete_denominator()


@spec_claim(SpecClaim(
    "CSU-S07", _CONTRACT, "Candidate measurement acquisition identity", "L2c",
    "Legacy acquisition evidence is immutable and excluded; matching acquisition "
    "evidence pools across producer builds and runtime sessions.",
    status="implemented",
))
def test_measurement_cutover_excludes_stale_history_without_rewriting_it():
    import json

    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.universe.candidate_eligibility import canonical_fingerprint
    from aletheia.universe.candidate_selection_authority import candidate_selection_policy_identity
    from aletheia.universe.candidate_selection_shadow import (
        build_candidate_selection_shadow_bundle, load_candidate_selection_outcomes,
    )
    from aletheia.universe.economic_utility import build_economic_utility_cohort_index

    cfg = AletheiaConfig()
    policy = candidate_selection_policy_identity(cfg)
    clean_keys = set()
    for run, legacy, net in (
        ("stale", "candidate-feature-acquisition/v2", -50.0),
        ("missing", None, -50.0),
        ("clean-build-a", False, 1.0),
        ("clean-build-b", False, 3.0),
    ):
        feature = _feature()
        bundle = build_candidate_selection_shadow_bundle(
            [feature], scanner_run_id=run, captured_at="2026-09-02T00:00:00+00:00",
            universe_config=cfg.universe, action_route_reasons={}, historical_outcomes=(),
            selection_policy=policy,
        )
        for evidence in bundle.evidence:
            payload = json.loads(evidence.compact_output_json)
            if legacy is not False:
                payload["evidence_contract"]["feature_acquisition_version"] = legacy
                evidence.compact_output_json = json.dumps(payload)
                evidence.record_hash = canonical_fingerprint({
                    "input": json.loads(evidence.input_json), "output": payload,
                })
        for outcome in bundle.outcomes:
            outcome.producer_commit = "a" * 40 if run == "clean-build-a" else "b" * 40
            outcome.producer_runtime_session_id = run
            outcome.status = "settled"
            outcome.settled_at = "2026-09-02T04:00:00+00:00"
            outcome.net_return_pct = net
            outcome.mae_pct = -0.1
            outcome.cost_model_version = "v1"
            outcome.min_tradable_edge_pct = 0.05
            outcome.estimated_fee_pct = 0.12
            outcome.estimated_spread_slippage_pct = 0.1
            outcome.estimated_funding_pct = 0.0
            if legacy is False:
                clean_keys.add(outcome.outcome_candidate_key)
        assert repo.insert_universe_twin_shadow_bundle(
            bundle.batch, bundle.evidence, outcomes=bundle.outcomes, min_interval_sec=0,
        ) == 1

    db = repo.get_db()
    tables = ("universe_twin_shadow_batches", "universe_twin_shadow_records", "filter_candidate_outcomes")
    before = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
              for table in tables}
    diagnostics = {}
    loaded = load_candidate_selection_outcomes(
        selection_policy=policy, diagnostics_out=diagnostics,
        settled_through="2026-09-03T00:00:00+00:00",
    )
    assert diagnostics["feature_acquisition_excluded"] == 2
    assert {row["outcome_candidate_key"] for row in loaded} == clean_keys
    assert {row["producer_commit"] for row in loaded} == {"a" * 40, "b" * 40}
    assert {row["producer_runtime_session_id"] for row in loaded} == {"clean-build-a", "clean-build-b"}
    index = build_economic_utility_cohort_index(
        loaded, source_cutoff_at="2026-09-03T00:00:00+00:00",
        cost_assumptions={
            "execution_fee_pct": cfg.trading.execution_fee_pct,
            "slippage_bps": cfg.trading.slippage_bps,
            "fallback_spread_bps": cfg.universe.max_spread_bps,
        },
    )
    assert index.cohorts[("continuation", "long", "okx", "spot")].parent_values == (2.0,)
    assert before == {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                      for table in tables}
