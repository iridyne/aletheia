from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from aletheia.config.schema import UniverseConfig


def _feature(index: int = 0, **overrides):
    value = {
        "asset_id": f"asset-{index}",
        "feature_snapshot_id": f"feature-{index}",
        "feature_snapshot_ts": "2026-09-02T00:00:00+00:00",
        "feature_source": "universe_feature_snapshot/v1",
        "registry_status": "active",
        "symbol": f"COIN{index}/USDT",
        "venue": "okx",
        "market_type": "spot",
        "last_price": 100.0,
        "quote_volume_24h": 10_000_000.0,
        "spread_bps": 5.0,
        "depth_10bps_usd": 30_000.0,
        "data_freshness_sec": 60.0,
        "return_1h": 1.0,
        "return_4h": 3.0,
        "return_24h": 8.0,
        "relative_strength_24h": 5.0,
        "rsi_14": 60.0,
        "atr_pct": 2.0,
    }
    value.update(overrides)
    return value


def test_hard_gate_is_fail_closed_and_action_route_is_observe_only():
    from aletheia.universe.candidate_eligibility import evaluate_hard_gate

    cfg = UniverseConfig()
    available = evaluate_hard_gate(
        _feature(), cfg, candidate_key="candidate-1", evaluated_at="2026-09-02T00:00:00+00:00"
    )
    assert (available.status, available.actionability) == ("eligible", "actionable")

    missing = evaluate_hard_gate(
        _feature(spread_bps=None),
        cfg,
        candidate_key="candidate-2",
        evaluated_at="2026-09-02T00:00:00+00:00",
    )
    assert missing.status == "unavailable"
    assert "spread_unavailable" in missing.reason_codes

    observe_only = evaluate_hard_gate(
        _feature(),
        cfg,
        candidate_key="candidate-3",
        evaluated_at="2026-09-02T00:00:00+00:00",
        action_route_reason="FAST_MARKET_DATA_VENUE_MISMATCH",
    )
    assert (observe_only.status, observe_only.actionability) == ("eligible", "observe_only")


def test_hard_gate_persists_depth_threshold_and_ignores_volatility():
    from aletheia.universe.candidate_eligibility import evaluate_hard_gate

    cfg = UniverseConfig(min_depth_10bps_usd=25_000)
    rejected = evaluate_hard_gate(
        _feature(depth_10bps_usd=2_499, atr_pct=999.0),
        cfg,
        candidate_key="candidate-depth",
        evaluated_at="2026-09-02T00:00:00+00:00",
    )
    check = next(item for item in rejected.checks if item.check_id == "depth")
    assert rejected.status == "ineligible"
    assert check.observed_value == 2_499
    assert check.threshold == 2_500
    assert all(item.check_id != "volatility" for item in rejected.checks)


def test_opportunity_evidence_maps_mechanisms_without_summing_short_bias():
    from aletheia.universe.opportunity_evidence import build_opportunity_evidence

    bundle = build_opportunity_evidence(
        _feature(return_24h=-10.0, relative_strength_24h=-8.0, return_4h=-4.0, rsi_14=35.0),
        candidate_key="candidate-evidence",
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    )
    mechanisms = {item.mechanism_id: item for item in bundle.mechanisms}
    assert set(mechanisms) == {
        "continuation", "reversal", "positioning_forced_flow", "information_catalyst"
    }
    continuation = mechanisms["continuation"]
    assert continuation.point_strength is None or 0 <= continuation.point_strength <= 100
    assert len(continuation.evidence_refs) == len(set(continuation.evidence_refs))
    assert mechanisms["information_catalyst"].applicability == "unavailable"
    assert bundle.context["volatility_state"]["role"] == "context_only"


def test_within_run_percentile_requires_twenty_and_average_ranks_ties():
    from aletheia.universe.opportunity_evidence import (
        annotate_within_run_percentiles,
        build_opportunity_evidence,
    )

    nineteen = tuple(
        build_opportunity_evidence(
            _feature(index, return_24h=5.0 + index),
            candidate_key=f"candidate-{index}",
            source_cutoff_at="2026-09-02T00:00:00+00:00",
        )
        for index in range(19)
    )
    assert all(
        mechanism.within_run_percentile is None
        for bundle in annotate_within_run_percentiles(nineteen)
        for mechanism in bundle.mechanisms
    )

    twenty = (*nineteen, build_opportunity_evidence(
        _feature(19, return_24h=24.0), candidate_key="candidate-19",
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    ))
    ranked = annotate_within_run_percentiles(twenty)
    continuation = [
        next(item for item in bundle.mechanisms if item.mechanism_id == "continuation")
        for bundle in ranked
    ]
    assert any(item.within_run_percentile is not None for item in continuation)


def _outcomes(count: int = 60):
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        rows.append({
            "outcome_candidate_key": f"candidate-selection-utility:v1:run-{index}:long",
            "scanner_run_id": f"run-{index}",
            "mechanism_id": "continuation",
            "direction": "long",
            "market_type": "spot",
            "strength_quintile": 5 if index < 25 else 4,
            "horizon": "4h",
            "status": "settled",
            "entry_ts": (start + timedelta(days=index % 12, hours=index)).isoformat(),
            "settled_at": (start + timedelta(days=index % 12, hours=index + 4)).isoformat(),
            "symbol": f"COIN{index % 6}/USDT",
            "net_return_pct": 1.0 if index < 25 else 0.2,
            "mae_pct": -0.5,
            "cost_model_version": "v1",
            "min_tradable_edge_pct": 0.05,
        })
    return rows


def test_economic_utility_uses_exact_4h_clustered_shrunk_economics():
    from aletheia.universe.economic_utility import estimate_economic_utility

    estimate = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=_outcomes(),
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    )
    assert estimate.estimate_status == "available"
    assert estimate.strength_quintile == 5
    assert estimate.expected_net_return_pct == pytest.approx((25 * 1.0 + 50 * (25 + 7) / 60) / 75)
    assert estimate.cluster_standard_error_pct == pytest.approx(0.0)
    assert estimate.tail_evidence.mae_p95_pct == pytest.approx(0.5)
    assert estimate.tail_evidence.status == "clear"

    reordered = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=list(reversed(_outcomes())),
        source_cutoff_at="2026-09-02T00:00:00Z",
    )
    assert reordered.source_outcome_fingerprint == estimate.source_outcome_fingerprint


def test_candidate_selection_does_not_fill_and_direction_conflict_fails_closed():
    from aletheia.universe.candidate_eligibility import evaluate_hard_gate
    from aletheia.universe.candidate_selection_utility import (
        build_candidate_selection_utility,
        select_admitted_candidates,
    )
    from aletheia.universe.economic_utility import estimate_economic_utility
    from aletheia.universe.opportunity_evidence import build_opportunity_evidence

    cfg = UniverseConfig(max_candidates=30)
    gate = evaluate_hard_gate(
        _feature(), cfg, candidate_key="candidate-select", evaluated_at="2026-09-02T00:00:00+00:00"
    )
    evidence = build_opportunity_evidence(
        _feature(), candidate_key="candidate-select", source_cutoff_at="2026-09-02T00:00:00+00:00"
    )
    long = estimate_economic_utility(
        candidate_key="candidate-select", mechanism_id="continuation", direction="long",
        market_type="spot", strength_percentile=0.9, outcomes=_outcomes(),
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    )
    decision = build_candidate_selection_utility(
        hard_gate=gate, opportunity_evidence=evidence, economic_estimates=(long,),
        legacy_control={"primary_family": "momentum", "total_score": 80.0, "authority_selected": True},
    )
    assert decision.admission_status == "admitted"
    assert select_admitted_candidates((decision,), max_candidates=30) == (decision,)

    short = replace(long, direction="short", economic_utility_pct=long.economic_utility_pct - 0.01)
    conflict = build_candidate_selection_utility(
        hard_gate=gate, opportunity_evidence=evidence, economic_estimates=(long, short),
        legacy_control={"primary_family": "momentum", "total_score": 80.0, "authority_selected": True},
    )
    assert conflict.admission_status == "direction_conflict"


def test_candidate_selection_shadow_persists_same_t0_full_denominator():
    import json

    from aletheia.storage import repositories as repo
    from aletheia.universe.candidate_selection_shadow import (
        build_candidate_selection_shadow_bundle,
        load_candidate_selection_outcomes,
    )

    population = [_feature(index, return_24h=5.0 + index) for index in range(20)]
    legacy_scored = [
        {**item, "scanner_family": "momentum", "scanner_direction": "long", "total_score": 50.0 + index}
        for index, item in enumerate(population)
    ]
    bundle = build_candidate_selection_shadow_bundle(
        population,
        scanner_run_id="candidate-selection-run",
        captured_at="2026-09-02T00:00:00+00:00",
        universe_config=UniverseConfig(max_candidates=3),
        legacy_scored=legacy_scored,
        legacy_allocated=legacy_scored[-3:],
        action_route_reasons={
            (item["feature_snapshot_id"], item["venue"], item["market_type"], item["symbol"]): None
            for item in population
        },
        historical_outcomes=(),
    )
    assert bundle.batch.candidate_count == 20
    assert len(bundle.evidence) == 20
    assert len(bundle.outcomes) >= 20
    assert repo.insert_universe_twin_shadow_bundle(
        bundle.batch, bundle.evidence, outcomes=bundle.outcomes, min_interval_sec=0,
    ) == 20
    stored = repo.list_universe_twin_shadow_records("candidate-selection-run")
    payload = json.loads(stored[0]["compact_output_json"])
    assert payload["candidate_selection"]["version"] == "candidate-selection-utility/v1"
    assert payload["evidence_contract"]["shadow_authority_applied"] is False
    assert load_candidate_selection_outcomes()


def test_candidate_selection_fixed_look_can_be_positive_without_authorizing():
    import json
    from datetime import datetime, timedelta, timezone

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import (
        FilterCandidateOutcome,
        UniverseTwinShadowBatch,
        UniverseTwinShadowEvidence,
    )
    from aletheia.universe.candidate_selection_evaluation import (
        evaluate_candidate_selection_policy,
    )
    from aletheia.universe.candidate_selection_shadow import (
        CANDIDATE_SELECTION_METRIC_VERSION,
        CANDIDATE_SELECTION_OUTCOME_VERSION,
        CANDIDATE_SELECTION_SHADOW_RECORD_VERSION,
    )

    start = datetime(2026, 9, 3, tzinfo=timezone.utc)
    cutoff = start.isoformat()
    policy_epoch = "candidate-selection-utility:fixed-policy"
    behavior_hash = "f" * 64
    for index in range(50):
        captured = start + timedelta(hours=index * 4)
        run_id = f"fixed-look-{index:02d}"
        records = []
        outcomes = []
        for candidate_index, (target_selected, legacy_selected, net) in enumerate(
            ((True, False, 1.0), (False, True, 0.2))
        ):
            symbol = f"COIN{index % 5}{candidate_index}/USDT"
            candidate_key = f"candidate-selection-utility:v1:{run_id}:{candidate_index}"
            outcome_key = f"{candidate_key}:long"
            payload = {
                "candidate_selection": {
                    "primary_direction": "long",
                    "legacy_control": {
                        "primary_direction": "long",
                        "authority_selected": legacy_selected,
                    },
                },
                "target_selected": target_selected,
                "outcome_candidate_keys": {"long": outcome_key},
                "evidence_contract": {
                    "estimator_training_cutoff": cutoff,
                    "behavior_contract_hash": behavior_hash,
                    "outcome_contract_version": CANDIDATE_SELECTION_OUTCOME_VERSION,
                },
            }
            records.append(UniverseTwinShadowEvidence(
                id=f"fixed-{index:02d}-{candidate_index}",
                scanner_run_id=run_id,
                candidate_key=candidate_key,
                candidate_index=candidate_index,
                symbol=symbol,
                market_type="spot",
                experiment_id="candidate-selection-fixed-look",
                policy_epoch_id=policy_epoch,
                behavior_contract_hash=behavior_hash,
                primary_horizon="4h",
                outcome_contract_version=CANDIDATE_SELECTION_OUTCOME_VERSION,
                metric_version=CANDIDATE_SELECTION_METRIC_VERSION,
                outcome_candidate_key=outcome_key if target_selected else None,
                authority_total_score=50.0,
                authority_selected=legacy_selected,
                shadow_pessimistic_selected=target_selected,
                shadow_valid=True,
                compact_output_json=json.dumps(payload, sort_keys=True),
                input_json="{}",
                record_hash=f"{index:02d}{candidate_index}".ljust(64, "0"),
                captured_at=captured.isoformat(),
            ))
            due = captured + timedelta(hours=4)
            gross = net + 0.2
            outcomes.append(FilterCandidateOutcome(
                outcome_candidate_key=outcome_key,
                venue="okx",
                symbol=symbol,
                market_type="spot",
                direction="long",
                scanner_family="candidate_selection_utility",
                horizon="4h",
                allocation_selected=target_selected,
                entry_price_usd=100.0,
                entry_spread_bps=5.0,
                entry_ts=captured.isoformat(),
                due_at=due.isoformat(),
                settled_at=(due + timedelta(minutes=1)).isoformat(),
                    exit_price_usd=100.0 * (1.0 + gross / 100.0),
                    gross_return_pct=gross,
                    directional_return_pct=gross,
                estimated_fee_pct=0.1,
                estimated_spread_slippage_pct=0.1,
                estimated_funding_pct=0.0,
                net_return_pct=net,
                min_tradable_edge_pct=0.05,
                    mfe_pct=gross + 0.2,
                mae_pct=-0.4,
                settlement_price_source="horizon_ohlcv",
                settlement_lag_sec=60.0,
                cost_model_version="v1",
                status="settled",
            ))
        batch = UniverseTwinShadowBatch(
            scanner_run_id=run_id,
            experiment_id="candidate-selection-fixed-look",
            policy_epoch_id=policy_epoch,
            behavior_contract_hash=behavior_hash,
            record_version=CANDIDATE_SELECTION_SHADOW_RECORD_VERSION,
            authority_behavior_version="universe-step-v3",
            shadow_behavior_version="candidate-selection-utility/v1",
            allocation_replay_contract="candidate-selection-utility-allocation/v1",
            primary_horizon="4h",
            outcome_contract_version=CANDIDATE_SELECTION_OUTCOME_VERSION,
            feature_source_fingerprint="source-v1",
            metric_version=CANDIDATE_SELECTION_METRIC_VERSION,
            allocator_random_state_json="null",
            policy_json=json.dumps({"universe": {"max_candidates": 2}}),
            allocation_context_json="{}",
            candidate_count=2,
            blocking_reasons_json="[]",
            batch_hash=f"batch-{index:02d}".ljust(64, "0"),
            captured_at=captured.isoformat(),
        )
        assert repo.insert_universe_twin_shadow_bundle(
            batch, records, outcomes=outcomes, min_interval_sec=0,
        ) == 2

    report = evaluate_candidate_selection_policy(
        as_of_at=(start + timedelta(days=10)).isoformat(),
    )
    assert report["verdict"] == "positive", report
    assert report["qualification_eligible"] is True
    assert report["promotion_eligible"] is False
    assert report["owner_admission_required"] is True
