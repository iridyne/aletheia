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
    replay = evaluate_hard_gate(
        dict(reversed(list(_feature().items()))),
        cfg,
        candidate_key="candidate-1",
        evaluated_at="2026-09-02T00:00:00+00:00",
    )
    assert replay.input_fingerprint == available.input_fingerprint
    assert replay.policy_fingerprint == available.policy_fingerprint

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


@pytest.mark.parametrize(
    ("feature", "config", "expected_status"),
    [
        (_feature(symbol="USDC/USDT"), UniverseConfig(), "ineligible"),
        (_feature(), UniverseConfig(blacklist=["COIN0/USDT"]), "ineligible"),
        (_feature(registry_status="inactive"), UniverseConfig(), "ineligible"),
        (_feature(last_price=float("nan")), UniverseConfig(), "ineligible"),
        (_feature(quote_volume_24h=1.0), UniverseConfig(), "ineligible"),
        (_feature(feature_snapshot_ts="not-a-time"), UniverseConfig(), "unavailable"),
        (
            _feature(feature_snapshot_ts="2026-09-03T00:00:00+00:00"),
            UniverseConfig(),
            "unavailable",
        ),
        (_feature(feature_source="unknown/v1"), UniverseConfig(), "unavailable"),
    ],
)
def test_hard_gate_rejects_invalid_physical_or_provenance_facts(
    feature, config, expected_status,
):
    from aletheia.universe.candidate_eligibility import evaluate_hard_gate

    decision = evaluate_hard_gate(
        feature,
        config,
        candidate_key="candidate-physical-gate",
        evaluated_at="2026-09-02T00:00:00+00:00",
    )
    assert decision.status == expected_status


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

    invalid = build_opportunity_evidence(
        _feature(return_24h=float("nan")),
        candidate_key="candidate-evidence-invalid",
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    )
    assert all(item.applicability == "unavailable" for item in invalid.mechanisms)

    conflict = build_opportunity_evidence(
        _feature(relative_strength_24h=8.0, return_4h=-5.0, return_24h=-4.0, rsi_14=50.0),
        candidate_key="candidate-evidence-conflict",
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    )
    continuation = next(item for item in conflict.mechanisms if item.mechanism_id == "continuation")
    assert continuation.applicability == "unavailable"
    assert continuation.reason_codes == ("mechanism_direction_conflict",)


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
            "venue": "okx",
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
        venue="okx",
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
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=list(reversed(_outcomes())),
        source_cutoff_at="2026-09-02T00:00:00Z",
    )
    assert reordered.source_outcome_fingerprint == estimate.source_outcome_fingerprint

    future = {
        **_outcomes()[0],
        "outcome_candidate_key": "candidate-selection-utility:v1:future:long",
        "scanner_run_id": "future-run",
        "entry_ts": "2026-09-03T00:00:00+00:00",
        "settled_at": "2026-09-03T04:00:00+00:00",
        "net_return_pct": 99.0,
    }
    wrong_cost_version = {
        **_outcomes()[0],
        "outcome_candidate_key": "candidate-selection-utility:v1:wrong-cost:long",
        "scanner_run_id": "wrong-cost-run",
        "cost_model_version": "legacy-cost/v0",
        "net_return_pct": 99.0,
    }
    frozen = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=[*_outcomes(), future, wrong_cost_version],
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    )
    assert frozen.expected_net_return_pct == estimate.expected_net_return_pct
    assert frozen.source_outcome_fingerprint == estimate.source_outcome_fingerprint

    other_venue = [
        {**row, "venue": "bitget", "net_return_pct": 99.0}
        for row in _outcomes()
    ]
    exact_venue = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=[*_outcomes(), *other_venue],
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    )
    assert exact_venue.expected_net_return_pct == estimate.expected_net_return_pct
    assert exact_venue.source_outcome_fingerprint == estimate.source_outcome_fingerprint

    exact_duplicates = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=[*_outcomes(), *_outcomes()],
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    )
    assert exact_duplicates.expected_net_return_pct == estimate.expected_net_return_pct
    conflicting = [dict(row) for row in _outcomes()]
    conflicting[0]["net_return_pct"] = 99.0
    conflict = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=[*_outcomes(), conflicting[0]],
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    )
    assert conflict.estimate_status == "unavailable"
    assert conflict.economic_utility_pct is None

    missing_tail = [dict(row) for row in _outcomes()]
    missing_tail[0]["mae_pct"] = None
    unavailable_tail = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=missing_tail,
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    )
    assert unavailable_tail.estimate_status == "unavailable"
    assert unavailable_tail.tail_evidence.status == "unavailable"

    catastrophic = [dict(row) for row in _outcomes()]
    catastrophic[0]["net_return_pct"] = -20.0
    harmful_tail = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=catastrophic,
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    )
    assert harmful_tail.tail_evidence.status == "harm"
    assert harmful_tail.economic_utility_pct is None


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
        venue="okx",
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
    priority_keys = repo.list_open_universe_twin_outcome_candidate_keys()
    assert {
        f"{outcome.outcome_candidate_key}:{outcome.horizon}"
        for outcome in bundle.outcomes
    } <= priority_keys
    stored = repo.list_universe_twin_shadow_records("candidate-selection-run")
    payload = json.loads(stored[0]["compact_output_json"])
    assert payload["candidate_selection"]["version"] == "candidate-selection-utility/v1"
    assert payload["evidence_contract"]["shadow_authority_applied"] is False
    assert load_candidate_selection_outcomes()


def test_frozen_training_view_keeps_late_settlement_as_pending_denominator():
    from aletheia.storage import repositories as repo
    from aletheia.universe.candidate_selection_shadow import (
        build_candidate_selection_shadow_bundle,
        load_candidate_selection_outcomes,
    )

    population = [_feature(index, return_24h=5.0 + index) for index in range(20)]
    legacy = [
        {**feature, "scanner_family": "momentum", "scanner_direction": "long", "total_score": 50.0}
        for feature in population
    ]
    bundle = build_candidate_selection_shadow_bundle(
        population, scanner_run_id="candidate-selection-late-settlement",
        captured_at="2026-09-02T00:00:00+00:00",
        universe_config=UniverseConfig(max_candidates=3), legacy_scored=legacy,
        legacy_allocated=legacy,
        action_route_reasons={
            (item["feature_snapshot_id"], item["venue"], item["market_type"], item["symbol"]): None
            for item in population
        },
        historical_outcomes=(),
    )
    repo.insert_universe_twin_shadow_bundle(
        bundle.batch, bundle.evidence, outcomes=bundle.outcomes, min_interval_sec=0,
    )
    after_cutoff = build_candidate_selection_shadow_bundle(
        population,
        scanner_run_id="candidate-selection-after-cutoff",
        captured_at="2026-09-02T13:00:00+00:00",
        universe_config=UniverseConfig(max_candidates=3),
        legacy_scored=legacy,
        legacy_allocated=legacy,
        action_route_reasons={
            (item["feature_snapshot_id"], item["venue"], item["market_type"], item["symbol"]): None
            for item in population
        },
        historical_outcomes=(),
    )
    repo.insert_universe_twin_shadow_bundle(
        after_cutoff.batch,
        after_cutoff.evidence,
        outcomes=after_cutoff.outcomes,
        min_interval_sec=0,
    )
    after_cutoff_keys = {
        outcome.outcome_candidate_key for outcome in after_cutoff.outcomes
    }
    key = load_candidate_selection_outcomes()[0]["outcome_candidate_key"]
    repo.get_db().execute(
        "UPDATE filter_candidate_outcomes SET status='settled', settled_at=?, "
        "net_return_pct=4.0, mae_pct=-2.0, cost_model_version='v1' "
        "WHERE outcome_candidate_key=?",
        ("2026-09-03T00:00:00+00:00", key),
    )
    repo.get_db().commit()

    frozen = load_candidate_selection_outcomes(settled_through="2026-09-02T12:00:00+00:00")
    assert frozen[0]["status"] == "pending"
    assert frozen[0]["settled_at"] is None
    assert frozen[0]["net_return_pct"] is None
    assert frozen[0]["mae_pct"] is None
    assert all(row["entry_ts"] < "2026-09-02T12:00:00+00:00" for row in frozen)
    assert not after_cutoff_keys.intersection(
        row["outcome_candidate_key"] for row in frozen
    )


def test_shadow_keeps_legacy_selected_outcome_when_target_gate_is_unavailable():
    import json

    from aletheia.universe.candidate_selection_shadow import (
        build_candidate_selection_shadow_bundle,
    )

    feature = _feature(depth_10bps_usd=None)
    legacy = {
        **feature,
        "scanner_family": "momentum",
        "scanner_direction": "long",
        "total_score": 80.0,
    }
    bundle = build_candidate_selection_shadow_bundle(
        [feature],
        scanner_run_id="candidate-selection-legacy-unavailable",
        captured_at="2026-09-02T00:00:00+00:00",
        universe_config=UniverseConfig(max_candidates=1),
        legacy_scored=[legacy],
        legacy_allocated=[legacy],
        action_route_reasons={("feature-0", "okx", "spot", "COIN0/USDT"): None},
        historical_outcomes=(),
    )
    payload = json.loads(bundle.evidence[0].compact_output_json)
    assert payload["candidate_selection"]["hard_gate"]["status"] == "unavailable"
    assert set(payload["outcome_candidate_keys"]) == {"long"}
    assert len(bundle.outcomes) == 1


def test_target_selection_deduplicates_symbol_by_preferred_market(monkeypatch):
    import json
    import aletheia.universe.candidate_selection_shadow as shadow_module

    spot = _feature(symbol="SAME/USDT", market_type="spot", feature_snapshot_id="spot")
    perp = _feature(
        index=1, symbol="SAME/USDT", market_type="perp",
        feature_snapshot_id="perp", funding_rate=0.0,
    )
    population = [spot, perp]
    legacy = [
        {**item, "scanner_family": "momentum", "scanner_direction": "long", "total_score": 50.0}
        for item in population
    ]
    monkeypatch.setattr(
        shadow_module,
        "select_admitted_candidates",
        lambda decisions, **_kwargs: tuple(decisions),
    )
    bundle = shadow_module.build_candidate_selection_shadow_bundle(
        population,
        scanner_run_id="candidate-selection-market-dedup",
        captured_at="2026-09-02T00:00:00+00:00",
        universe_config=UniverseConfig(max_candidates=2),
        legacy_scored=legacy,
        legacy_allocated=legacy,
        action_route_reasons={
            (item["feature_snapshot_id"], item["venue"], item["market_type"], item["symbol"]): None
            for item in population
        },
        historical_outcomes=(),
        selection_policy={"preferred_market_type": "spot"},
    )
    selected = [
        row.market_type
        for row in bundle.evidence
        if json.loads(row.compact_output_json)["target_selected"]
    ]
    assert selected == ["spot"]


def test_shadow_retains_full_supported_multi_mechanism_record():
    historical = [
        {
            **row,
            "outcome_candidate_key": f"{row['outcome_candidate_key']}:{mechanism}:{direction}",
            "mechanism_id": mechanism,
            "direction": direction,
            "market_type": "perp",
        }
        for mechanism in ("continuation", "reversal", "positioning_forced_flow")
        for direction in ("long", "short")
        for row in _outcomes()
    ]
    population = [
        _feature(
            index,
            market_type="perp",
            return_24h=5.0 + index,
            rsi_14=75.0,
            funding_rate=0.001,
            oi_delta_1h=10.0,
        )
        for index in range(20)
    ]
    legacy = [
        {**item, "scanner_family": "momentum", "scanner_direction": "long", "total_score": 50.0}
        for item in population
    ]
    from aletheia.universe.candidate_selection_shadow import (
        build_candidate_selection_shadow_bundle,
    )

    bundle = build_candidate_selection_shadow_bundle(
        population,
        scanner_run_id="candidate-selection-multi-mechanism",
        captured_at="2026-09-02T00:00:00+00:00",
        universe_config=UniverseConfig(max_candidates=3),
        legacy_scored=legacy,
        legacy_allocated=legacy[:3],
        action_route_reasons={
            (item["feature_snapshot_id"], item["venue"], item["market_type"], item["symbol"]): None
            for item in population
        },
        historical_outcomes=historical,
    )
    assert len(bundle.evidence) == 20
    assert max(len(row.compact_output_json.encode()) for row in bundle.evidence) > 8 * 1024


def test_candidate_selection_authority_requires_qualification_and_projects_target():
    import json

    from aletheia.storage import repositories as repo
    from aletheia.universe.candidate_selection_authority import (
        candidate_selection_policy_identity,
    )
    from aletheia.universe.candidate_selection_shadow import (
        build_candidate_selection_shadow_bundle,
        target_candidates_from_bundle,
    )

    population = [_feature(index, return_24h=5.0 + index) for index in range(20)]
    legacy_scored = [
        {**item, "scanner_family": "momentum", "scanner_direction": "long", "total_score": 50.0 + index}
        for index, item in enumerate(population)
    ]
    kwargs = {
        "population": population,
        "scanner_run_id": "candidate-selection-authority-run",
        "captured_at": "2026-09-02T00:00:00+00:00",
        "universe_config": UniverseConfig(max_candidates=3),
        "legacy_scored": legacy_scored,
        "legacy_allocated": legacy_scored[-3:],
        "action_route_reasons": {
            (item["feature_snapshot_id"], item["venue"], item["market_type"], item["symbol"]): None
            for item in population
        },
        "historical_outcomes": _outcomes(),
    }
    shadow = build_candidate_selection_shadow_bundle(**kwargs)
    assert shadow.batch.policy_epoch_id
    cost_bound = build_candidate_selection_shadow_bundle(
        **kwargs,
        selection_policy={
            "cost_assumptions": {"execution_fee_pct": 0.06, "slippage_bps": 5.0},
        },
    )
    cost_snapshot = json.loads(cost_bound.evidence[0].compact_output_json)[
        "evidence_contract"
    ]["cost_assumptions"]
    assert cost_snapshot == {
        "execution_fee_pct": 0.06,
        "fallback_spread_bps": kwargs["universe_config"].max_spread_bps,
        "slippage_bps": 5.0,
    }
    changed_policy = build_candidate_selection_shadow_bundle(
        **{**kwargs, "universe_config": UniverseConfig(max_candidates=4)},
    )
    assert changed_policy.batch.policy_epoch_id != shadow.batch.policy_epoch_id
    with pytest.raises(ValueError, match="qualification_invalid"):
        build_candidate_selection_shadow_bundle(**kwargs, authority_applied=True)

    authority = build_candidate_selection_shadow_bundle(
        **kwargs,
        authority_applied=True,
        qualification_evidence={
            "verdict": "positive",
            "promotion_eligible": True,
            "policy_epoch_id": shadow.batch.policy_epoch_id,
            "selection_policy": candidate_selection_policy_identity(
                kwargs["universe_config"]
            ),
            "blocking_reasons": [],
            "invalid_reasons": [],
        },
    )
    projected = target_candidates_from_bundle(authority, legacy_scored)
    assert 0 < len(projected) <= 3
    assert all(item["candidate_selection_policy"] == "candidate_selection_utility_v1" for item in projected)
    assert all(item["candidate_selection_utility_pct"] > 0 for item in projected)
    assert repo.insert_universe_twin_shadow_bundle(
        authority.batch,
        authority.evidence,
        outcomes=authority.outcomes,
        min_interval_sec=0,
    ) == 20
    columns = {
        row[1] for row in repo.get_db().execute("PRAGMA table_info(universe_members)")
    }
    assert {
        "candidate_selection_policy",
        "candidate_selection_policy_epoch",
        "candidate_selection_utility_pct",
    } <= columns


def test_candidate_selection_epoch_identity_includes_execution_cost_assumptions():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.candidate_selection_authority import (
        candidate_selection_policy_identity,
    )

    cfg = AletheiaConfig()
    before = candidate_selection_policy_identity(cfg)
    cfg.trading.execution_fee_pct += 0.01
    cfg.trading.slippage_bps += 1.0
    after = candidate_selection_policy_identity(cfg)

    assert before["cost_assumptions"] != after["cost_assumptions"]


def test_candidate_selection_fixed_look_can_be_positive_for_owner_admission():
    import json
    from datetime import datetime, timedelta, timezone

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import (
        FilterCandidateOutcome,
        RuntimeSession,
        UniverseTwinShadowBatch,
        UniverseTwinShadowEvidence,
    )
    from aletheia.universe.candidate_selection_evaluation import (
        evaluate_candidate_selection_policy,
    )
    from aletheia.universe.candidate_selection_shadow import (
        CANDIDATE_SELECTION_METRIC_VERSION,
        CANDIDATE_SELECTION_OUTCOME_VERSION,
        CANDIDATE_SELECTION_RECORD_VERSION,
    )

    start = datetime(2026, 9, 3, tzinfo=timezone.utc)
    cutoff = start.isoformat()
    policy_epoch = "candidate-selection-utility:fixed-policy"
    behavior_hash = "f" * 64
    repo.insert_runtime_session(RuntimeSession(
        session_id="candidate-runtime",
        boot_commit="candidate-deployed-commit",
        behavior_fingerprint="candidate-runtime-behavior",
    ))
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
                    "shadow_authority_applied": False,
                    "promotion_eligible": False,
                    "estimator_training_cutoff": cutoff,
                    "behavior_contract_hash": behavior_hash,
                    "policy_epoch_id": policy_epoch,
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
                mae_pct=None if index == 0 and target_selected else -0.4,
                settlement_price_source="horizon_ohlcv",
                settlement_lag_sec=60.0,
                cost_model_version="v1",
                producer_runtime_session_id="candidate-runtime",
                producer_commit="candidate-deployed-commit",
                producer_behavior_fingerprint="candidate-runtime-behavior",
                settlement_runtime_session_id="candidate-runtime",
                settlement_producer_commit="candidate-deployed-commit",
                settlement_behavior_fingerprint="candidate-runtime-behavior",
                status="settled",
            ))
        batch = UniverseTwinShadowBatch(
            scanner_run_id=run_id,
            experiment_id="candidate-selection-fixed-look",
            policy_epoch_id=policy_epoch,
            behavior_contract_hash=behavior_hash,
            record_version=CANDIDATE_SELECTION_RECORD_VERSION,
            authority_behavior_version="universe-step-v3",
            shadow_behavior_version="candidate-selection-utility/v1",
            allocation_replay_contract="candidate-selection-utility-allocation/v1",
            primary_horizon="4h",
            outcome_contract_version=CANDIDATE_SELECTION_OUTCOME_VERSION,
            feature_source_fingerprint="source-v1",
            metric_version=CANDIDATE_SELECTION_METRIC_VERSION,
            allocator_random_state_json="null",
            policy_json=json.dumps({
                "universe": {"max_candidates": 2},
                "selection_policy": {
                    "runtime_identity": {
                        "producer_commit": "candidate-deployed-commit",
                        "behavior_fingerprint": "candidate-runtime-behavior",
                    },
                },
            }),
            allocation_context_json="{}",
            candidate_count=2,
            blocking_reasons_json='["owner_admission_required"]',
            batch_hash=f"batch-{index:02d}".ljust(64, "0"),
            captured_at=captured.isoformat(),
        )
        if index == 0:
            # A prior frozen policy remains immutable evidence but cannot
            # contaminate the latest epoch after selection settings rotate.
            old_records = []
            old_outcomes = []
            for candidate_index, record in enumerate(records):
                payload = json.loads(record.compact_output_json)
                old_candidate_key = f"candidate-selection-utility:v1:old-policy:{candidate_index}"
                old_outcome_key = f"{old_candidate_key}:long"
                payload["outcome_candidate_keys"] = {"long": old_outcome_key}
                payload["evidence_contract"]["policy_epoch_id"] = (
                    "candidate-selection-utility:old-policy"
                )
                old_records.append(record.model_copy(update={
                    "id": f"old-policy-{candidate_index}",
                    "scanner_run_id": "fixed-look-old-policy",
                    "candidate_key": old_candidate_key,
                    "outcome_candidate_key": old_outcome_key if candidate_index == 0 else None,
                    "policy_epoch_id": "candidate-selection-utility:old-policy",
                    "compact_output_json": json.dumps(payload, sort_keys=True),
                    "record_hash": f"old-policy-{candidate_index}".ljust(64, "0"),
                    "captured_at": (start - timedelta(days=1)).isoformat(),
                }))
                old_outcomes.append(outcomes[candidate_index].model_copy(update={
                    "id": f"oldoutcome{candidate_index:02d}",
                    "outcome_candidate_key": old_outcome_key,
                    "entry_ts": (start - timedelta(days=1)).isoformat(),
                    "due_at": (start - timedelta(hours=20)).isoformat(),
                    "settled_at": (start - timedelta(hours=19, minutes=59)).isoformat(),
                }))
            old_batch = batch.model_copy(update={
                "scanner_run_id": "fixed-look-old-policy",
                "policy_epoch_id": "candidate-selection-utility:old-policy",
                "batch_hash": "old-policy-batch".ljust(64, "0"),
                "captured_at": (start - timedelta(days=1)).isoformat(),
            })
            assert repo.insert_universe_twin_shadow_bundle(
                old_batch, old_records, outcomes=old_outcomes, min_interval_sec=0,
            ) == 2
        assert repo.insert_universe_twin_shadow_bundle(
            batch, records, outcomes=outcomes, min_interval_sec=0,
        ) == 2

    incomplete_tail = evaluate_candidate_selection_policy(
        as_of_at=(start + timedelta(days=10)).isoformat(),
    )
    assert incomplete_tail["verdict"] == "insufficient_data"
    assert incomplete_tail["complete_cluster_n"] == 49

    # A later complete cluster cannot substitute for missing evidence inside
    # the first outcome-blind T0 exposure prefix.
    substitute_captured = start + timedelta(days=11)
    substitute_records = []
    substitute_outcomes = []
    for candidate_index, record in enumerate(records):
        payload = json.loads(record.compact_output_json)
        candidate_key = f"candidate-selection-utility:v1:substitute:{candidate_index}"
        outcome_key = f"{candidate_key}:long"
        payload["outcome_candidate_keys"] = {"long": outcome_key}
        substitute_records.append(record.model_copy(update={
            "id": f"substitute-{candidate_index}",
            "scanner_run_id": "fixed-look-substitute",
            "candidate_key": candidate_key,
            "outcome_candidate_key": outcome_key if candidate_index == 0 else None,
            "compact_output_json": json.dumps(payload, sort_keys=True),
            "record_hash": f"substitute-{candidate_index}".ljust(64, "0"),
            "captured_at": substitute_captured.isoformat(),
        }))
        substitute_outcomes.append(outcomes[candidate_index].model_copy(update={
            "id": f"substitute-outcome-{candidate_index}",
            "outcome_candidate_key": outcome_key,
            "entry_ts": substitute_captured.isoformat(),
            "due_at": (substitute_captured + timedelta(hours=4)).isoformat(),
            "settled_at": (substitute_captured + timedelta(hours=4, minutes=1)).isoformat(),
            "mae_pct": -0.4,
        }))
    substitute_batch = batch.model_copy(update={
        "scanner_run_id": "fixed-look-substitute",
        "batch_hash": "substitute-batch".ljust(64, "0"),
        "captured_at": substitute_captured.isoformat(),
    })
    assert repo.insert_universe_twin_shadow_bundle(
        substitute_batch,
        substitute_records,
        outcomes=substitute_outcomes,
        min_interval_sec=0,
    ) == 2
    still_incomplete = evaluate_candidate_selection_policy(
        as_of_at=(start + timedelta(days=12)).isoformat(),
    )
    assert still_incomplete["verdict"] == "insufficient_data"
    assert still_incomplete["fixed_look_cluster_n"] == 49

    repo.get_db().execute(
        "UPDATE filter_candidate_outcomes SET mae_pct=-0.4 "
        "WHERE outcome_candidate_key=?",
        ("candidate-selection-utility:v1:fixed-look-00:0:long",),
    )

    report = evaluate_candidate_selection_policy(
        as_of_at=(start + timedelta(days=10)).isoformat(),
    )
    assert report["verdict"] == "positive", report
    assert report["policy_epoch_id"] == policy_epoch
    assert report["expected_cluster_n"] == 51
    assert report["qualification_eligible"] is True
    assert report["promotion_eligible"] is True
    assert report["owner_admission_required"] is True

    late_captured = start + timedelta(days=12)
    late_records = []
    late_outcomes = []
    for candidate_index, record in enumerate(records):
        payload = json.loads(record.compact_output_json)
        late_candidate_key = f"candidate-selection-utility:v1:late:{candidate_index}"
        late_outcome_key = f"{late_candidate_key}:long"
        payload["outcome_candidate_keys"] = {"long": late_outcome_key}
        late_records.append(record.model_copy(update={
            "id": f"late-policy-{candidate_index}",
            "scanner_run_id": "fixed-look-late-pending",
            "candidate_key": late_candidate_key,
            "outcome_candidate_key": late_outcome_key if candidate_index == 0 else None,
            "compact_output_json": json.dumps(payload, sort_keys=True),
            "record_hash": f"late-policy-{candidate_index}".ljust(64, "0"),
            "captured_at": late_captured.isoformat(),
        }))
        late_outcomes.append(outcomes[candidate_index].model_copy(update={
            "id": f"lateoutcome{candidate_index:02d}",
            "outcome_candidate_key": late_outcome_key,
            "entry_ts": late_captured.isoformat(),
            "due_at": (late_captured + timedelta(hours=4)).isoformat(),
            "settled_at": None,
            "gross_return_pct": None,
            "directional_return_pct": None,
            "net_return_pct": None,
            "mfe_pct": None,
            "mae_pct": None,
            "status": "pending",
        }))
    late_batch = batch.model_copy(update={
        "scanner_run_id": "fixed-look-late-pending",
        "batch_hash": "late-policy-batch".ljust(64, "0"),
        "captured_at": late_captured.isoformat(),
    })
    assert repo.insert_universe_twin_shadow_bundle(
        late_batch, late_records, outcomes=late_outcomes, min_interval_sec=0,
    ) == 2
    frozen = evaluate_candidate_selection_policy(
        as_of_at=(start + timedelta(days=13)).isoformat(),
    )
    assert frozen["verdict"] == "positive", frozen
    assert frozen["fixed_look_cluster_n"] == 50
    assert frozen["full_arm_outcome_coverage"] == 1.0

    repo.get_db().execute(
        "UPDATE filter_candidate_outcomes SET producer_commit='unattested-commit' "
        "WHERE outcome_candidate_key=?",
        ("candidate-selection-utility:v1:fixed-look-01:0:long",),
    )
    invalid = evaluate_candidate_selection_policy(
        as_of_at=(start + timedelta(days=10)).isoformat(),
    )
    assert invalid["verdict"] == "invalid"
    assert "runtime_provenance_unattested" in invalid["invalid_reasons"]
