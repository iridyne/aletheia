"""Task-4 invariants for the step-authoritative Universe twin scorer."""

from __future__ import annotations

import asyncio
import ast
import copy
from dataclasses import replace
import inspect
import json
from pathlib import Path
import random
import textwrap

import pytest

from aletheia.config.schema import UniverseConfig
from aletheia.universe.allocator import compute_total_score
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


def test_allocator_audit_is_observational_and_records_realized_multiplier() -> None:
    cfg = UniverseConfig(scanner_weights={"momentum": 1.2})
    feat_without_audit = _feature()
    feat_with_audit = _feature()
    scanner = _step_result()
    expected = compute_total_score(feat_without_audit, scanner, cfg)
    audit: dict[str, object] = {}
    actual = compute_total_score(
        feat_with_audit,
        scanner,
        cfg,
        scoring_audit_out=audit,
    )
    assert actual == expected
    assert audit["contract"] == "universe-authority-score-audit/v1"
    assert audit["calibration_effective_multiplier"] == 1.0
    assert audit["operator_multiplier"] == 1.2
    assert audit["effective_opportunity_multiplier"] == 1.2


@pytest.mark.parametrize("invalid_score", [None, "55"])
def test_allocator_audit_does_not_coerce_invalid_authority_score(
    invalid_score: object,
) -> None:
    cfg = UniverseConfig()
    scanner = _step_result(opportunity_score=invalid_score)
    with pytest.raises(TypeError):
        compute_total_score(_feature(), scanner, cfg)
    with pytest.raises(TypeError):
        compute_total_score(
            _feature(),
            scanner,
            cfg,
            scoring_audit_out={},
        )


def test_allocator_audit_records_realized_learning_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aletheia.learning.experiments import InfluenceDecision

    monkeypatch.setattr(
        "aletheia.learning.experiments.apply_numeric_treatment",
        lambda **kwargs: InfluenceDecision(
            value=float(kwargs["treatment"]),
            applied=True,
            assignment="treatment",
            reason="authorized",
            exposure_id="exposure-1",
        ),
    )
    audit: dict[str, object] = {}
    compute_total_score(
        _feature(),
        _step_result(),
        UniverseConfig(),
        calibration={"momentum:long:perp": 1.2},
        scoring_cohort_key="run-1",
        scoring_audit_out=audit,
    )
    assert audit["calibration_effective_multiplier"] == pytest.approx(1.1)
    assert audit["calibration_authority_applied"] is True
    assert audit["calibration_assignment"] == "treatment"
    assert audit["effective_opportunity_multiplier"] == pytest.approx(1.1)


def test_zero_score_shadow_treatment_persists_blocked_authority_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aletheia.config.schema import AletheiaConfig
    from aletheia.learning import authority, experiments
    from aletheia.learning.authority import AuthorityDecision
    from aletheia.learning.ledger import get_record

    cfg = AletheiaConfig()
    monkeypatch.setattr(experiments, "load_config", lambda: cfg)
    monkeypatch.setattr(experiments, "_learning_gate_reason", lambda: None)
    monkeypatch.setattr(experiments, "stable_assignment", lambda *_args: "treatment")
    monkeypatch.setattr(
        authority,
        "resolve_learning_authority",
        lambda _path, experiment, _epoch: AuthorityDecision(
            False,
            "experiment_stage_not_paper",
            experiment.stage,
            experiment.allocation_pct,
            experiment.activation_verdict_id,
            None,
        ),
    )
    feature = _feature(feature_snapshot_id="zero-score-feature")
    audit: dict[str, object] = {}

    compute_total_score(
        feature,
        _step_result(opportunity_score=0.0),
        cfg.universe,
        calibration={"momentum:long:perp": 1.2},
        scoring_cohort_key="zero-score-run",
        scoring_audit_out=audit,
    )

    exposure = get_record("learning_exposures", feature["learning_exposure_id"])
    manifest = json.loads(exposure["manifest_json"])
    assert audit["calibration_assignment"] == "treatment"
    assert audit["calibration_authority_applied"] is False
    assert audit["calibration_reason"] == "baseline:experiment_stage_not_paper"
    assert exposure["before_json"] == exposure["after_json"]
    assert manifest["authority_applied"] is False
    assert manifest["authority_reason"] == "experiment_stage_not_paper"
    assert manifest["behavior_changed"] is False
    assert manifest["behavior_change_reason"] == "authority_not_applied"


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


def test_shadow_threshold_contract_is_locked_to_current_authority() -> None:
    from aletheia.universe import allocator, signal_emitter

    assert twin_scoring._SCORE_THRESHOLDS == (
        ("explore_admission", allocator._EXPLORE_MIN_SCORE),
        ("opportunity_emission", signal_emitter._MIN_SCORE),
        ("high_label", signal_emitter._HIGH_CONF_THRESHOLD),
    )


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
        "aletheia.learning",
        "aletheia.risk",
        "aletheia.execution",
        "aletheia.universe.allocator",
        "aletheia.universe.signal_emitter",
    )
    assert not any(module == prefix or module.startswith(f"{prefix}.") for module in imported for prefix in forbidden)


def test_refresh_wires_twin_off_thread_and_never_feeds_authority_consumers() -> None:
    from aletheia.universe.service import UniverseService

    tree = ast.parse(textwrap.dedent(inspect.getsource(UniverseService.refresh)))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    to_thread_calls = [
        call
        for call in calls
        if isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "asyncio"
        and call.func.attr == "to_thread"
    ]
    assert not to_thread_calls

    helper_calls = [
        call for call in calls if isinstance(call.func, ast.Name) and call.func.id == "_compute_twin_shadow_nonfatal"
    ]
    assert len(helper_calls) == 1
    assert isinstance(helper_calls[0].args[0], ast.Name)
    assert helper_calls[0].args[0].id == "build_twin_shadow_batch"

    authority_consumers = {
        "allocate_universe",
        "emit_deterministic_opportunities",
        "_member_fingerprint",
        "_register_experiment_candidate_outcomes",
    }
    for call in calls:
        name = call.func.id if isinstance(call.func, ast.Name) else None
        if name not in authority_consumers:
            continue
        referenced_names = {node.id for node in ast.walk(call) if isinstance(node, ast.Name)}
        assert not any(value.startswith("twin_shadow") for value in referenced_names)


def test_shadow_batch_failure_cannot_fail_authority_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aletheia.universe import service

    async def fail_to_thread(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("batch failed")

    monkeypatch.setattr(service.asyncio, "to_thread", fail_to_thread)
    result = asyncio.run(
        service._compute_twin_shadow_nonfatal(
            object(),
            (_twin_input(),),
            scanner_run_id="run-1",
            base_policy=_policy(),
        )
    )
    assert result == ()


def test_shadow_batch_timeout_cannot_stall_authority_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aletheia.universe import service

    async def never_finishes(*_args: object, **_kwargs: object) -> object:
        await asyncio.Event().wait()

    monkeypatch.setattr(service.asyncio, "to_thread", never_finishes)
    monkeypatch.setattr(service, "_TWIN_SHADOW_TIMEOUT_SEC", 0.001)
    result = asyncio.run(
        service._compute_twin_shadow_nonfatal(
            object(),
            (_twin_input(),),
            scanner_run_id="run-1",
            base_policy=_policy(),
        )
    )
    assert result == ()


def test_shadow_allocation_failure_preserves_records_as_blocked() -> None:
    from aletheia.universe import service

    record = build_twin_shadow_record(_twin_input(), scanner_run_id="run-1", base_policy=_policy())

    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("allocation replay failed")

    marked = asyncio.run(
        service._compute_twin_shadow_allocation_nonfatal(
            fail,
            twin_scoring.mark_shadow_allocation_unavailable,
            (record,),
            (_twin_input(),),
        )
    )
    assert len(marked) == 1
    assert "shadow_allocation_replay_failed:RuntimeError" in marked[0].blocking_reasons
    assert marked[0].shadow_point_selected is None


def test_shadow_policy_failure_cannot_fail_authority_refresh() -> None:
    from aletheia.universe import service

    def fail_policy(**_kwargs: object) -> object:
        raise ValueError("invalid diagnostic policy")

    result = asyncio.run(
        service._compute_twin_shadow_nonfatal(
            object(),
            (_twin_input(),),
            scanner_run_id="run-1",
            policy_factory=fail_policy,
            policy_kwargs={"unused": True},
        )
    )
    assert result == ()


@pytest.mark.parametrize("failing_stage", ["annotator", "summarizer"])
def test_shadow_finalization_failure_cannot_fail_authority_refresh(
    failing_stage: str,
) -> None:
    from aletheia.universe import service

    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("diagnostic finalization failed")

    records = (build_twin_shadow_record(_twin_input(), scanner_run_id="run-1", base_policy=_policy()),)
    annotator = fail if failing_stage == "annotator" else annotate_authority_selection
    summarizer = fail if failing_stage == "summarizer" else summarize_twin_shadow_records
    finalized, summary = service._finalize_twin_shadow_nonfatal(
        records,
        annotator=annotator,
        summarizer=summarizer,
        fine_filtered=[_feature()],
        allocated=[_feature()],
    )
    assert finalized == ()
    assert summary is None


def test_external_multiplier_policy_is_bounded_and_observable() -> None:
    base = evaluate_continuous_candidate(_feature(), _policy())
    boosted = evaluate_continuous_candidate(_feature(), replace(_policy(), external_opportunity_multiplier=1.2))
    assert boosted.point_score > base.point_score
    with pytest.raises(ValueError):
        replace(_policy(), external_opportunity_multiplier=-0.1)
