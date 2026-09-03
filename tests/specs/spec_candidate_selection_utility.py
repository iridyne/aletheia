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
    "CSU-S03", _CONTRACT, "EconomicUtilityEstimateV1", "L2a",
    "Actionable economic estimates use only frozen 4h cost-after clustered evidence with uncertainty and tail status.",
    status="implemented",
))
def test_economic_utility_contract_is_frozen_and_cost_after():
    from aletheia.universe.economic_utility import ECONOMIC_ESTIMATOR_VERSION
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION

    assert ECONOMIC_ESTIMATOR_VERSION == "candidate-economic-utility-estimator/v2"
    assert COST_MODEL_VERSION == "v1"


@spec_claim(SpecClaim(
    "CSU-S04", _CONTRACT, "CandidateSelectionUtilityV1", "L2a",
    "Target authority requires a positive exact policy epoch and explicit owner admission; legacy remains the default and rollback.",
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
    positive = {
        "verdict": "positive",
        "promotion_eligible": True,
        "policy_epoch_id": target.candidate_selection_policy_epoch,
        "selection_policy": candidate_selection_policy_identity(target),
        "blocking_reasons": [],
        "invalid_reasons": [],
    }
    assert candidate_selection_authority_block_reason(
        target, positive,
    ) == "candidate_selection_admission_record_missing"
    assert candidate_selection_authority_block_reason(
        target, positive, require_admission_record=False,
    ) is None
    assert candidate_selection_authority_block_reason(
        target,
        {**positive, "verdict": "neutral"},
        require_admission_record=False,
    ) == "candidate_selection_qualification_not_positive"
    monkeypatch.setattr(
        "aletheia.learning.ledger.list_open_experiments",
        lambda **_kwargs: [{"stage": "paper_10"}],
    )
    assert (
        candidate_selection_overlap_block_reason()
        == "candidate_selection_overlapping_learning_authority"
    )
