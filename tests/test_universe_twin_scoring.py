"""Task-4 invariants for the step-authoritative Universe twin scorer."""

from __future__ import annotations

import ast
import copy
from dataclasses import replace
import inspect
from pathlib import Path
import random

import pytest

from aletheia.config.schema import UniverseConfig
from aletheia.universe.continuous_scoring import (
    ContinuousScoringPolicy,
    evaluate_continuous_candidate,
)
from aletheia.universe.features import _relative_squeeze_state
from aletheia.universe.scanners import UNIVERSE_SCORER_BEHAVIOR_VERSION
from aletheia.universe import twin_scoring
from aletheia.universe.twin_scoring import (
    TWIN_SHADOW_RECORD_VERSION,
    TwinShadowInput,
    annotate_authority_selection,
    build_twin_shadow_batch,
    build_twin_shadow_record,
    rank_twin_shadow_records,
    summarize_twin_shadow_records,
)
from aletheia.universe.twin_allocation import replay_twin_shadow_allocations


def _feature(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "feature_snapshot_id": "feature-1",
        "asset_id": "asset-1",
        "symbol": "BTC/USDT",
        "market_type": "perp",
        "rs_beta_adj_24h": None,
        "relative_strength_24h": 4.0,
        "relative_strength_4h": 1.0,
        "return_1h": 1.0,
        "return_4h": 2.0,
        "return_24h": 3.0,
        "rsi_14": 60.0,
        "rsi_4h": 55.0,
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
    }
    result.update(overrides)
    return result


def _step_result(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "scanner_family": "momentum",
        "scanner_direction": "long",
        "opportunity_score": 55.0,
        "reasons": ["step evidence"],
        "all_scores": {"momentum": 55.0},
    }
    result.update(overrides)
    return result


def _twin_input(
    *,
    feature: dict[str, object] | None = None,
    step_result: dict[str, object] | None = None,
    total: float = 50.0,
    multiplier: float = 1.0,
) -> TwinShadowInput:
    return TwinShadowInput(
        feature=feature or _feature(),
        authority_scanner_result=step_result or _step_result(),
        authority_total_score=total,
        authority_score_audit={
            "contract": "universe-authority-score-audit/v1",
            "effective_opportunity_multiplier": multiplier,
            "calibration_authority_applied": False,
        },
    )


def _policy(**overrides: float) -> ContinuousScoringPolicy:
    return ContinuousScoringPolicy(
        min_volume_24h_usdt=overrides.get("min_volume_24h_usdt", 5_000_000.0),
        min_depth_10bps_usd=overrides.get("min_depth_10bps_usd", 25_000.0),
    )


def test_twin_applies_same_realized_external_multiplier_without_authority() -> None:
    feat = _feature()
    record = build_twin_shadow_record(
        _twin_input(feature=feat, multiplier=1.2),
        scanner_run_id="run-1",
        base_policy=_policy(),
    )
    expected = evaluate_continuous_candidate(
        feat,
        replace(_policy(), external_opportunity_multiplier=1.2),
    )
    assert record.shadow == expected
    assert record.external_opportunity_multiplier == 1.2
    assert record.shadow_authority_applied is False
    assert record.promotion_eligible is False
    assert record.authority_behavior_version == UNIVERSE_SCORER_BEHAVIOR_VERSION
    assert record.shadow_behavior_version == expected.behavior_version


def test_twin_rejects_multiplier_without_authority_audit_contract() -> None:
    item = replace(
        _twin_input(multiplier=1.8),
        authority_score_audit={
            "contract": "untrusted-contract",
            "effective_opportunity_multiplier": 1.8,
            "calibration_authority_applied": True,
        },
    )
    record = build_twin_shadow_record(
        item,
        scanner_run_id="run-1",
        base_policy=_policy(),
    )
    expected = evaluate_continuous_candidate(_feature(), _policy())
    assert record.shadow == expected
    assert record.external_opportunity_multiplier == 1.0
    assert record.external_multiplier_contract == "untrusted-contract"
    assert record.calibration_authority_applied is False
    assert "authority_score_audit_not_available" in record.blocking_reasons


def test_twin_record_preserves_identity_and_complete_continuous_audit() -> None:
    record = build_twin_shadow_record(_twin_input(), scanner_run_id="run-1", base_policy=_policy())
    assert record.record_version == TWIN_SHADOW_RECORD_VERSION
    assert record.candidate_key == "universe-twin:run-1:feature-1:perp:BTC/USDT"
    assert record.feature_snapshot_id == "feature-1"
    assert record.asset_id == "asset-1"
    assert record.shadow.valid is True
    assert record.shadow.sensitivities
    assert record.shadow.interaction_residual is None
    assert "interaction_residual_unavailable" in record.blocking_reasons
    assert all(item.unit for item in record.shadow.point_evidence)
    assert record.unavailable_promotion_identities
    assert "task4_diagnostic_only" in record.blocking_reasons


def test_shadow_failure_is_fail_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("untrusted detail")

    monkeypatch.setattr(twin_scoring, "evaluate_continuous_candidate", fail)
    record = build_twin_shadow_record(_twin_input(), scanner_run_id="run-1", base_policy=_policy())
    assert record.shadow.valid is False
    assert record.shadow.invalid_reason == "shadow_evaluation_failed:RuntimeError"
    assert "untrusted detail" not in str(record)
    assert record.authority_total_score == 50.0
    assert record.shadow_authority_applied is False


def test_twin_build_does_not_mutate_inputs() -> None:
    item = _twin_input()
    before_feature = copy.deepcopy(item.feature)
    before_step = copy.deepcopy(item.authority_scanner_result)
    before_audit = copy.deepcopy(item.authority_score_audit)
    build_twin_shadow_record(item, scanner_run_id="run-1", base_policy=_policy())
    assert item.feature == before_feature
    assert item.authority_scanner_result == before_step
    assert item.authority_score_audit == before_audit


def test_rank_deltas_compare_scores_but_do_not_select() -> None:
    first = build_twin_shadow_record(_twin_input(total=80.0), scanner_run_id="run-1", base_policy=_policy())
    second = build_twin_shadow_record(
        _twin_input(
            feature=_feature(
                feature_snapshot_id="feature-2",
                asset_id="asset-2",
                symbol="ETH/USDT",
            ),
            total=40.0,
        ),
        scanner_run_id="run-1",
        base_policy=_policy(),
    )
    first = replace(first, shadow=replace(first.shadow, point_score=30.0, pessimistic_score=20.0))
    second = replace(second, shadow=replace(second.shadow, point_score=70.0, pessimistic_score=60.0))
    ranked = rank_twin_shadow_records((first, second))
    assert ranked[0].authority_rank == 1
    assert ranked[0].shadow_point_rank == 2
    assert ranked[0].point_rank_delta == 1
    assert ranked[1].authority_rank == 2
    assert ranked[1].shadow_point_rank == 1
    assert ranked[1].point_rank_delta == -1
    assert all(record.shadow_point_selected is None for record in ranked)


def test_batch_selection_annotation_contains_only_authority_facts() -> None:
    second_feature = _feature(
        feature_snapshot_id="feature-2",
        asset_id="asset-2",
        symbol="ETH/USDT",
    )
    records = build_twin_shadow_batch(
        (_twin_input(), _twin_input(feature=second_feature)),
        scanner_run_id="run-1",
        base_policy=_policy(),
    )
    annotated = annotate_authority_selection(
        records,
        fine_filtered=(_feature(), second_feature),
        allocated=(_feature(),),
    )
    assert annotated[0].fine_filter_passed is True
    assert annotated[0].authority_selected is True
    assert annotated[1].fine_filter_passed is True
    assert annotated[1].authority_selected is False
    assert all(record.shadow_point_selected is None for record in annotated)
    assert all(record.shadow_pessimistic_selected is None for record in annotated)


def test_shadow_allocation_replays_both_arms_without_authority_mutation() -> None:
    first_input = _twin_input()
    second_input = _twin_input(
        feature=_feature(
            feature_snapshot_id="feature-2",
            asset_id="asset-2",
            symbol="ETH/USDT",
        )
    )
    records = build_twin_shadow_batch(
        (first_input, second_input),
        scanner_run_id="run-1",
        base_policy=_policy(),
    )
    records = (
        replace(records[0], shadow=replace(records[0].shadow, point_score=30.0, pessimistic_score=80.0)),
        replace(records[1], shadow=replace(records[1].shadow, point_score=80.0, pessimistic_score=30.0)),
    )
    universe_config = UniverseConfig(max_candidates=1, explore_ratio=0.0)
    replayed = replay_twin_shadow_allocations(
        records,
        (first_input, second_input),
        scanner_run_id="run-1",
        universe_config=universe_config,
        app_config=object(),
        family_tolerances={},
        regime_state=None,
        allocator_random_state=random.getstate(),
        dedupe=lambda values, _cfg: values,
    )
    assert replayed[0].shadow_point_selected is False
    assert replayed[0].shadow_pessimistic_selected is True
    assert replayed[1].shadow_point_selected is True
    assert replayed[1].shadow_pessimistic_selected is False
    assert all(record.shadow_authority_applied is False for record in replayed)
    assert first_input.feature["symbol"] == "BTC/USDT"


def test_threshold_crossings_and_summary_are_diagnostic_only() -> None:
    record = build_twin_shadow_record(_twin_input(total=50.0), scanner_run_id="run-1", base_policy=_policy())
    record = replace(record, authority_selected=True)
    comparisons = {item.threshold_id: item for item in record.threshold_comparisons}
    assert set(comparisons) == {
        "explore_admission",
        "opportunity_emission",
        "high_label",
    }
    assert comparisons["opportunity_emission"].authority_passed is True
    assert comparisons["opportunity_emission"].shadow_point_passed is False
    assert comparisons["opportunity_emission"].point_crossed is True
    summary = summarize_twin_shadow_records((record,))
    assert summary.candidate_count == 1
    assert summary.valid_shadow_count == 1
    assert summary.authority_selected_count == 1
    assert summary.point_threshold_crossing_count == 1
    assert summary.pessimistic_threshold_crossing_count == 1
    assert summary.interaction_residual_unavailable_count == 1


def test_no_chase_authority_gate_remains_an_explicit_blocker() -> None:
    record = build_twin_shadow_record(
        _twin_input(
            step_result=_step_result(
                scanner_direction=None,
                opportunity_score=0.0,
                reasons=["15m candle >1.5 ATR; no chase"],
            )
        ),
        scanner_run_id="run-1",
        base_policy=_policy(),
    )
    assert "authority_no_chase_gate" in record.blocking_reasons
    assert record.shadow_authority_applied is False


def test_squeeze_ratio_exposes_degree_without_changing_boolean_contract() -> None:
    ratio, squeezed = _relative_squeeze_state(3.0, [10.0, 10.0])
    assert ratio == pytest.approx(0.3)
    assert squeezed is True
    zero_ratio, zero_squeezed = _relative_squeeze_state(3.0, [0.0, 0.0])
    assert zero_ratio is None
    assert zero_squeezed is False

    from aletheia.storage.models import UniverseFeatureSnapshot

    assert "bb_squeeze_ratio" not in UniverseFeatureSnapshot.model_fields


def test_twin_module_has_no_authority_consumer_or_persistence_imports() -> None:
    path = Path(inspect.getsourcefile(twin_scoring) or "")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported.update(node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
    forbidden = (
        "aletheia.storage",
        "extensions.learning",
        "aletheia.risk",
        "aletheia.execution",
        "aletheia.universe.allocator",
        "aletheia.universe.signal_emitter",
    )
    assert not any(module == prefix or module.startswith(f"{prefix}.") for module in imported for prefix in forbidden)


def test_external_multiplier_policy_is_bounded_and_observable() -> None:
    base = evaluate_continuous_candidate(_feature(), _policy())
    boosted = evaluate_continuous_candidate(_feature(), replace(_policy(), external_opportunity_multiplier=1.2))
    assert boosted.point_score > base.point_score
    with pytest.raises(ValueError):
        replace(_policy(), external_opportunity_multiplier=-0.1)
