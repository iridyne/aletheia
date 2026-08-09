"""Task-6 prospective, non-authoritative Universe curve comparison."""

from __future__ import annotations

import inspect
import json
import random
from types import SimpleNamespace

import pytest

from aletheia.config.schema import UniverseConfig
from aletheia.learning.registry import get_learning_path
from aletheia.storage import repositories as repo
from aletheia.universe import service
from aletheia.universe.continuous_scoring import ContinuousScoringPolicy
from aletheia.universe.twin_allocation import replay_twin_shadow_allocations
from aletheia.universe.twin_evaluation import evaluate_twin_shadow_epoch
from aletheia.universe.twin_experiment import (
    TWIN_EXPERIMENT_ASSIGNMENT_PROPENSITY,
    TWIN_EXPERIMENT_PATH_ID,
    TWIN_EXPERIMENT_PRIMARY_HORIZON,
    build_prospective_twin_shadow_bundle,
)
from aletheia.universe.twin_persistence import replay_twin_shadow_bundle
from aletheia.universe.twin_scoring import (
    TwinShadowInput,
    annotate_authority_selection,
    build_twin_shadow_batch,
)


def _input(
    *,
    feature_id: str,
    symbol: str,
    direction: str = "long",
    no_chase: bool = False,
    direction_available: bool = True,
) -> TwinShadowInput:
    signed = 1.0 if direction == "long" else -1.0
    scanner_result = {
        "scanner_family": "momentum" if direction == "long" else "short_bias",
        "opportunity_score": 55.0,
        "reasons": (
            ["step evidence", "15m candle >1.5 ATR; no chase"]
            if no_chase
            else ["step evidence"]
        ),
    }
    if direction_available:
        scanner_result.update(
            scanner_direction=None if no_chase else direction,
            proposed_direction=direction,
        )
    return TwinShadowInput(
        feature={
            "feature_snapshot_id": feature_id,
            "asset_id": f"asset-{feature_id}",
            "symbol": symbol,
            "market_type": "perp",
            "venue": "binance",
            "last_price": 100.0,
            "relative_strength_24h": signed * 4.0,
            "relative_strength_4h": signed,
            "return_1h": signed,
            "return_4h": signed * 2.0,
            "return_24h": signed * 3.0,
            "rsi_14": 60.0 if direction == "long" else 40.0,
            "rsi_4h": 55.0 if direction == "long" else 45.0,
            "atr_pct": 3.0,
            "atr_4h": 3.0,
            "bb_squeeze_ratio": 0.8,
            "bb_width_pct": 3.0,
            "bb_width_4h": 4.0,
            "vol_ratio": 1.5,
            "quote_volume_24h": 10_000_000.0,
            "spread_bps": 10.0,
            "depth_10bps_usd": 25_000.0,
            "funding_rate": 0.0,
            "oi_delta_1h": 0.0,
            "taker_buy_ratio": 1.0,
            "data_freshness_sec": 300.0,
            "tradability_score": 80.0,
        },
        authority_scanner_result=scanner_result,
        authority_total_score=50.0,
        authority_score_audit={
            "contract": "universe-authority-score-audit/v1",
            "effective_opportunity_multiplier": 1.0,
            "calibration_authority_applied": False,
        },
    )


def _bundle(
    scanner_run_id: str,
    *,
    captured_at: str = "2026-08-08T00:00:00+00:00",
    no_chase: bool = False,
    include_directionless: bool = False,
):
    inputs = [
        _input(feature_id=f"{scanner_run_id}-1", symbol="BTC/USDT"),
        _input(
            feature_id=f"{scanner_run_id}-2",
            symbol="ETH/USDT",
            direction="short",
            no_chase=no_chase,
        ),
    ]
    if include_directionless:
        inputs.append(
            _input(
                feature_id=f"{scanner_run_id}-3",
                symbol="BNB/USDT",
                direction_available=False,
            )
        )
    inputs_tuple = tuple(inputs)
    policy = ContinuousScoringPolicy()
    universe_config = UniverseConfig(max_candidates=1, explore_ratio=0.0)
    allocator_state = random.getstate()
    records = build_twin_shadow_batch(
        inputs_tuple,
        scanner_run_id=scanner_run_id,
        base_policy=policy,
    )
    app_config = SimpleNamespace(
        exchange=SimpleNamespace(
            enabled_markets={"spot": True, "usdt_futures": True},
            preferred_market_type="perp",
        )
    )
    records = replay_twin_shadow_allocations(
        records,
        inputs_tuple,
        scanner_run_id=scanner_run_id,
        universe_config=universe_config,
        app_config=app_config,
        family_tolerances={},
        regime_state="normal",
        allocator_random_state=allocator_state,
        dedupe=service._dedupe_by_symbol_preferred_market,
    )
    records = annotate_authority_selection(
        records,
        fine_filtered=tuple(item.feature for item in inputs_tuple),
        allocated=(inputs_tuple[0].feature,),
    )
    return build_prospective_twin_shadow_bundle(
        records,
        inputs_tuple,
        scanner_run_id=scanner_run_id,
        authority_snapshot_id=f"snapshot-{scanner_run_id}",
        policy=policy,
        allocator_random_state=allocator_state,
        universe_config=universe_config,
        enabled_markets={"spot": True, "usdt_futures": True},
        preferred_market_type="perp",
        family_tolerances={},
        regime_state="normal",
        provider_id="ccxt:binance",
        outcome_venue="binance",
        captured_at=captured_at,
    )


def test_task6_preregisters_stable_cluster_assignment_and_exact_identity() -> None:
    first = _bundle("run-stable")
    second = _bundle("run-stable")

    assert first.batch.policy_epoch_id
    assert first.batch.policy_epoch_id == second.batch.policy_epoch_id
    assert first.batch.experiment_id == second.batch.experiment_id
    assert first.batch.behavior_contract_hash == second.batch.behavior_contract_hash
    assert first.batch.assignment_unit_key == "run-stable"
    assert first.batch.assignment_arm == second.batch.assignment_arm
    assert first.batch.assignment_arm in {"control", "treatment"}
    assert first.batch.assignment_propensity == TWIN_EXPERIMENT_ASSIGNMENT_PROPENSITY
    assert first.batch.primary_horizon == TWIN_EXPERIMENT_PRIMARY_HORIZON == "4h"
    assert first.batch.metric_version == (
        "universe-continuous-pessimistic-cluster-itt-utility/v2"
    )
    assert first.batch.outcome_contract_version == (
        "fixed-horizon-assigned-selection-net/v2"
    )
    assert first.batch.treatment_policy == "continuous_pessimistic"
    assert first.batch.control_policy == "universe_step_v1"

    epoch = repo.get_db().execute(
        "SELECT * FROM learning_policy_epochs WHERE epoch_id=?",
        (first.batch.policy_epoch_id,),
    ).fetchone()
    assert epoch is not None
    values = json.loads(epoch["effective_values_json"])
    assert values["assignment"]["propensity"] == 0.5
    assert values["primary_horizon"] == "4h"
    assert "secret" not in epoch["effective_values_json"].lower()


def test_task6_registers_outcomes_only_for_assigned_selection() -> None:
    bundle = _bundle("run-complete")
    selected = [item for item in bundle.evidence if item.assigned_policy_selected]
    unselected = [item for item in bundle.evidence if not item.assigned_policy_selected]

    assert len(bundle.evidence) == 2
    assert len(bundle.outcomes) == len(selected)
    assert selected
    assert unselected
    assert {outcome.horizon for outcome in bundle.outcomes} == {"4h"}
    assert all(outcome.outcome_candidate_key for outcome in bundle.outcomes)
    assert all(item.policy_epoch_id == bundle.batch.policy_epoch_id for item in bundle.evidence)
    assert all(item.assignment_unit_key == "run-complete" for item in bundle.evidence)
    assert all(item.assignment_arm == bundle.batch.assignment_arm for item in bundle.evidence)
    assert all(item.assignment_propensity == 0.5 for item in bundle.evidence)
    assert all(item.metric_version == bundle.batch.metric_version for item in bundle.evidence)
    assert all(item.shadow_authority_applied is False for item in bundle.evidence)
    assert all(item.promotion_eligible is False for item in bundle.evidence)
    assert {outcome.outcome_candidate_key for outcome in bundle.outcomes} == {
        item.outcome_candidate_key for item in selected
    }
    assert all(item.outcome_candidate_key is None for item in unselected)
    for item in bundle.evidence:
        contract = json.loads(item.compact_output_json)["evidence_contract"]
        assert contract["fixed_horizon_outcomes_collected"] is bool(
            item.assigned_policy_selected
        )
        assert contract["assigned_policy_selected"] is item.assigned_policy_selected
        assert contract["authority_applied"] is False
        assert contract["promotion_eligible"] is False
        assert "assignment_not_available" not in contract["blocking_reasons"]
        assert "propensity_not_available" not in contract["blocking_reasons"]
        assert "feature_observation_lineage_not_available" in contract["blocking_reasons"]

    assert get_learning_path("ranker_factor_filter").measurement_supported is False
    with pytest.raises(KeyError):
        get_learning_path(TWIN_EXPERIMENT_PATH_ID)


def test_task6_selected_outcome_uses_the_assigned_arm_identity() -> None:
    arms: set[str] = set()
    for index in range(100):
        bundle = _bundle(f"run-identity-{index}")
        arms.add(str(bundle.batch.assignment_arm))
        outcomes = {
            item.outcome_candidate_key: item for item in bundle.outcomes
        }
        for evidence in bundle.evidence:
            if not evidence.assigned_policy_selected:
                continue
            output = json.loads(evidence.compact_output_json)
            contract = output["evidence_contract"]
            source = (
                output["shadow"]
                if bundle.batch.assignment_arm == "treatment"
                else output["authority"]
            )
            direction_field = (
                "pessimistic_direction"
                if bundle.batch.assignment_arm == "treatment"
                else "direction"
            )
            family_field = (
                "pessimistic_family"
                if bundle.batch.assignment_arm == "treatment"
                else "family"
            )
            outcome = outcomes[evidence.outcome_candidate_key]
            assert contract["assigned_policy_direction"] == source[direction_field]
            assert contract["assigned_policy_family"] == source[family_field]
            assert outcome.direction == contract["assigned_policy_direction"]
            assert outcome.scanner_family == contract["assigned_policy_family"]
        if arms == {"control", "treatment"}:
            break
    assert arms == {"control", "treatment"}


def test_task6_keeps_unselected_no_chase_candidate_without_fake_outcome() -> None:
    bundle = _bundle("run-no-chase", no_chase=True)

    assert len(bundle.evidence) == 2
    assert bundle.batch.assignment_unit_key == "run-no-chase"
    evidence = next(item for item in bundle.evidence if item.symbol == "ETH/USDT")
    assert evidence.assigned_policy_selected is False
    assert evidence.outcome_candidate_key is None
    assert all(item.symbol != "ETH/USDT" for item in bundle.outcomes)
    assert evidence.shadow_authority_applied is False
    blockers = json.loads(evidence.compact_output_json)["evidence_contract"][
        "blocking_reasons"
    ]
    assert "authority_no_chase_gate" in blockers


def test_task6_persists_unselected_candidate_without_direction_identity() -> None:
    bundle = _bundle("run-directionless", include_directionless=True)
    evidence = next(item for item in bundle.evidence if item.symbol == "BNB/USDT")

    assert evidence.assigned_policy_selected is False
    assert evidence.outcome_candidate_key is None
    assert all(item.symbol != "BNB/USDT" for item in bundle.outcomes)
    assert repo.insert_universe_twin_shadow_bundle(
        bundle.batch,
        bundle.evidence,
        outcomes=bundle.outcomes,
    ) == 3


def test_task6_bundle_persistence_is_atomic_with_outcome_registration() -> None:
    bundle = _bundle("run-persist")

    with pytest.raises(ValueError, match="outcome_count_mismatch"):
        repo.insert_universe_twin_shadow_bundle(
            bundle.batch,
            bundle.evidence,
        )
    wrong_arm = (
        "control" if bundle.batch.assignment_arm == "treatment" else "treatment"
    )
    inconsistent_evidence = (
        bundle.evidence[0].model_copy(update={"assignment_arm": wrong_arm}),
        *bundle.evidence[1:],
    )
    with pytest.raises(ValueError, match="record_experiment_identity_mismatch"):
        repo.insert_universe_twin_shadow_bundle(
            bundle.batch,
            inconsistent_evidence,
            outcomes=bundle.outcomes,
        )
    assert repo.get_universe_twin_shadow_batch("run-persist") is None

    inserted = repo.insert_universe_twin_shadow_bundle(
        bundle.batch,
        bundle.evidence,
        outcomes=bundle.outcomes,
    )

    assert inserted == 2
    rows = repo.get_db().execute(
        "SELECT * FROM filter_candidate_outcomes ORDER BY outcome_candidate_key"
    ).fetchall()
    selected = [item for item in bundle.evidence if item.assigned_policy_selected]
    assert len(rows) == len(selected)
    assert {row["horizon"] for row in rows} == {"4h"}
    pending_keys = {
        f"{item.outcome_candidate_key}:4h" for item in selected
    }
    assert repo.list_open_universe_twin_outcome_candidate_keys() == pending_keys
    repo.get_db().execute(
        "UPDATE learning_policy_epochs SET ended_at=? WHERE epoch_id=?",
        ("2026-08-08T01:00:00+00:00", bundle.batch.policy_epoch_id),
    )
    assert repo.list_open_universe_twin_outcome_candidate_keys() == pending_keys
    assert all(bool(row["allocation_selected"]) for row in rows)
    stored_batch = repo.get_universe_twin_shadow_batch("run-persist")
    stored_records = repo.list_universe_twin_shadow_records("run-persist")
    assert stored_batch is not None
    assert replay_twin_shadow_bundle(stored_batch, stored_records).matches is True
    assert repo.insert_universe_twin_shadow_bundle(
        bundle.batch,
        bundle.evidence,
        outcomes=bundle.outcomes,
    ) == 0

    conflicting = list(bundle.outcomes)
    conflicting[0] = conflicting[0].model_copy(update={"entry_price_usd": 101.0})
    with pytest.raises(ValueError, match="universe_twin_shadow_outcome_identity_conflict"):
        repo.insert_universe_twin_shadow_bundle(
            bundle.batch,
            bundle.evidence,
            outcomes=tuple(conflicting),
        )


def test_task6_read_only_evaluation_reports_estimates_but_cannot_promote() -> None:
    bundles = []
    arms = set()
    index = 0
    while arms != {"control", "treatment"} and index < 100:
        bundle = _bundle(
            f"run-eval-{index}",
            captured_at=f"2026-08-08T{index % 24:02d}:00:00+00:00",
        )
        bundles.append(bundle)
        arms.add(bundle.batch.assignment_arm)
        index += 1
    assert arms == {"control", "treatment"}

    epoch_id = bundles[0].batch.policy_epoch_id
    for bundle in bundles:
        assert bundle.batch.policy_epoch_id == epoch_id
        repo.insert_universe_twin_shadow_bundle(
            bundle.batch,
            bundle.evidence,
            outcomes=bundle.outcomes,
            min_interval_sec=0,
        )
        for outcome in bundle.outcomes:
            repo.settle_filter_candidate_outcome(
                outcome.id,
                exit_price=101.0 if outcome.direction == "long" else 99.0,
                settled_at=outcome.due_at,
                economics={
                    "gross_return_pct": 1.0,
                    "directional_return_pct": 1.0,
                    "estimated_fee_pct": 0.1,
                    "estimated_spread_slippage_pct": 0.1,
                    "estimated_funding_pct": 0.0,
                        "net_return_pct": (
                            0.8 if bundle.batch.assignment_arm == "treatment" else -0.2
                        ),
                    "min_tradable_edge_pct": 0.2,
                    "mfe_pct": 1.2,
                    "mae_pct": -0.3,
                    "cost_model_version": "v1",
                },
                settlement_price_source="test_fixed_horizon",
                settlement_lag_sec=0.0,
            )

    report = evaluate_twin_shadow_epoch(str(epoch_id))

    assert report["authority"] == "shadow_only"
    assert report["promotion_eligible"] is False
    assert report["verdict"] in {"insufficient_data", "not_available"}
    assert report["complete_cluster_n"] == len(bundles)
    assert report["treatment_cluster_utility_mean"] is not None
    assert report["control_cluster_utility_mean"] is not None
    assert report["observed_cluster_utility_delta"] > 0
    assert report["direct_replay_delta"] is None
    assert report["point_replay_delta"] is None
    assert report["propensity_aware_delta"] is None
    assert report["propensity_estimator_status"] == (
        "not_computed_observed_arm_itt_only"
    )
    assert report["estimator_sign_agreement"] is None
    assert report["assignment_propensity"] == 0.5
    assert "availability_profile_not_available" in report["blocking_reasons"]
    assert "feature_observation_lineage_not_available" in report["blocking_reasons"]
    assert "independent_dr_outcome_model_not_available" in report["blocking_reasons"]
    assert "anytime_valid_lower_bound_not_available" in report["blocking_reasons"]
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM learning_contribution_verdicts"
    ).fetchone()[0] == 0

    repo.get_db().execute(
        "UPDATE universe_twin_shadow_records SET behavior_contract_hash=? "
        "WHERE scanner_run_id=? AND candidate_index=0",
        ("0" * 64, bundles[0].batch.scanner_run_id),
    )
    tampered = evaluate_twin_shadow_epoch(str(epoch_id))
    assert tampered["verdict"] == "invalid"
    assert "record_behavior_contract_hash_mismatch" in tampered["invalid_reasons"]


def test_task6_runtime_wiring_stays_on_the_nonfatal_post_publication_seam() -> None:
    refresh_source = inspect.getsource(service.UniverseService.refresh)
    persistence_source = inspect.getsource(service._persist_twin_shadow_nonfatal)

    assert refresh_source.count("build_prospective_twin_shadow_bundle") == 3
    assert "outcome_venue=cfg.market_data.ccxt_exchange" in refresh_source
    assert "outcomes=bundle.outcomes" in persistence_source
    assert "_atomic_universe_publication" not in persistence_source
