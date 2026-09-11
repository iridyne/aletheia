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
def test_target_policy_authority_is_explicit_and_fail_closed(monkeypatch):
    from aletheia.learning.registry import get_learning_path
    from aletheia.universe.candidate_selection_authority import (
        TARGET_CANDIDATE_POLICY,
        candidate_selection_authority_block_reason,
        candidate_selection_overlap_block_reason,
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
    monkeypatch.setattr(
        "aletheia.learning.ledger.list_open_experiments",
        lambda **_kwargs: [{"stage": "paper_10"}],
    )
    assert (
        candidate_selection_overlap_block_reason()
        == "candidate_selection_overlapping_learning_authority"
    )




@spec_claim(SpecClaim(
    "CSU-S05", _CONTRACT, "current Candidate bootstrap actionability", "L2c",
    "An empty Universe admits valid T0 dynamic Candidates and Planning without "
    "a slow market snapshot; downstream freshness retains its separate authority.",
    status="implemented",
))
def test_current_candidate_bootstrap_without_slow_cache(tmp_path, monkeypatch):
    from tests.test_candidate_selection_utility import test_empty_universe_bootstraps_candidate_and_planning_without_slow_cache
    test_empty_universe_bootstraps_candidate_and_planning_without_slow_cache(tmp_path, monkeypatch)
