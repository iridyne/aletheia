"""Task-5 contracts for durable, replayable, non-authoritative twin evidence."""

from __future__ import annotations

import asyncio
import ast
from datetime import datetime, timedelta, timezone
import inspect
import json
import logging
import random
import textwrap
from types import SimpleNamespace

import pytest

from aletheia.config.schema import UniverseConfig
from aletheia.storage import repositories as repo
from aletheia.universe import service
from aletheia.universe.continuous_scoring import ContinuousScoringPolicy
from aletheia.universe.twin_allocation import replay_twin_shadow_allocations
from aletheia.universe.twin_persistence import (
    TWIN_SHADOW_CAPTURE_INTERVAL_SEC,
    TWIN_SHADOW_MAX_INPUT_BYTES,
    TWIN_SHADOW_MAX_OUTPUT_BYTES,
    build_twin_shadow_bundle,
    replay_twin_shadow_bundle,
)
from aletheia.universe.twin_scoring import (
    TwinShadowInput,
    annotate_authority_selection,
    build_twin_shadow_batch,
)


def _feature(**overrides: object) -> dict[str, object]:
    feature: dict[str, object] = {
        "feature_snapshot_id": "feature-1",
        "asset_id": "asset-1",
        "symbol": "BTC/USDT",
        "market_type": "perp",
        "venue": "binance",
        "last_price": 50_000.0,
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
        "api_key": "must-not-be-persisted",
    }
    feature.update(overrides)
    return feature


def _input() -> TwinShadowInput:
    return TwinShadowInput(
        feature=_feature(),
        authority_scanner_result={
            "scanner_family": "momentum",
            "scanner_direction": "long",
            "opportunity_score": 55.0,
            "reasons": ["step evidence"],
            "untrusted_extra": "must-not-be-persisted",
        },
        authority_total_score=50.0,
        authority_score_audit={
            "contract": "universe-authority-score-audit/v1",
            "effective_opportunity_multiplier": 1.0,
            "calibration_authority_applied": False,
            "provider_secret": "must-not-be-persisted",
        },
    )


def _bundle(
    items: tuple[TwinShadowInput, ...] | None = None,
    *,
    scanner_run_id: str = "run-1",
    captured_at: str = "2026-08-08T00:00:00+00:00",
):
    inputs = items or (_input(),)
    policy = ContinuousScoringPolicy()
    universe_config = UniverseConfig(max_candidates=1, explore_ratio=0.0)
    random_state = random.getstate()
    records = build_twin_shadow_batch(
        inputs,
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
        inputs,
        scanner_run_id=scanner_run_id,
        universe_config=universe_config,
        app_config=app_config,
        family_tolerances={},
        regime_state="normal",
        allocator_random_state=random_state,
        dedupe=service._dedupe_by_symbol_preferred_market,
    )
    records = annotate_authority_selection(
        records,
        fine_filtered=tuple(item.feature for item in inputs),
        allocated=(inputs[0].feature,),
    )
    return build_twin_shadow_bundle(
        records,
        inputs,
        scanner_run_id=scanner_run_id,
        authority_snapshot_id="snapshot-1",
        policy=policy,
        allocator_random_state=random_state,
        universe_config=universe_config,
        enabled_markets={"spot": True, "usdt_futures": True},
        preferred_market_type="perp",
        family_tolerances={},
        regime_state="normal",
        provider_id="ccxt:binance",
        captured_at=captured_at,
    )


def test_bundle_is_complete_sanitized_and_explicitly_non_authoritative() -> None:
    bundle = _bundle()

    assert bundle.batch.candidate_count == 1
    assert bundle.batch.policy_epoch_id is None
    assert bundle.batch.availability_profile_id is None
    assert bundle.batch.promotion_eligible is False
    assert len(bundle.evidence) == 1
    evidence = bundle.evidence[0]
    assert evidence.shadow_authority_applied is False
    assert evidence.promotion_eligible is False
    assert "must-not-be-persisted" not in evidence.input_json
    assert "api_key" not in evidence.input_json
    output = json.loads(evidence.compact_output_json)
    contract = output["evidence_contract"]
    assert "sampled_diagnostic_only" in contract["blocking_reasons"]
    assert "task4_diagnostic_only" not in contract["blocking_reasons"]
    assert contract["unavailable_identities"] == [
        "policy_epoch",
        "availability_profile",
        "authority_allocator_random_lineage",
        "feature_observation_lineage",
        "fixed_horizon_outcome",
        "assignment",
        "propensity",
    ]
    assert contract["fixed_horizon_outcomes_collected"] is False
    assert len(output["top_sensitivities"]) <= 8
    assert len(evidence.compact_output_json.encode()) <= TWIN_SHADOW_MAX_OUTPUT_BYTES
    assert len(evidence.input_json.encode()) <= TWIN_SHADOW_MAX_INPUT_BYTES


def test_oversized_replay_identity_fails_closed_before_database_write() -> None:
    base = _input()
    oversized = TwinShadowInput(
        feature={**base.feature, "symbol": "X" * 3000},
        authority_scanner_result=base.authority_scanner_result,
        authority_total_score=base.authority_total_score,
        authority_score_audit=base.authority_score_audit,
    )

    with pytest.raises(ValueError, match="twin_replay_input_too_large"):
        _bundle((oversized,))


def test_bundle_persistence_is_atomic_idempotent_and_replayable() -> None:
    bundle = _bundle()

    inserted_records = repo.insert_universe_twin_shadow_bundle(
        bundle.batch,
        bundle.evidence,
    )
    assert inserted_records == 1
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM filter_candidate_outcomes"
    ).fetchone()[0] == 0
    retry_records = repo.insert_universe_twin_shadow_bundle(
        bundle.batch,
        bundle.evidence,
    )
    assert retry_records == 0

    batch = repo.get_universe_twin_shadow_batch("run-1")
    records = repo.list_universe_twin_shadow_records("run-1")
    assert batch is not None
    report = replay_twin_shadow_bundle(batch, records)
    assert report.matches is True
    assert report.matched_count == 1

    tampered = [dict(records[0], record_hash="0" * 64)]
    tampered_report = replay_twin_shadow_bundle(batch, tampered)
    assert tampered_report.matches is False
    assert tampered_report.mismatched_candidate_keys == (records[0]["candidate_key"],)


def test_multi_candidate_replay_is_stable_across_database_row_order() -> None:
    first = _input()
    second = TwinShadowInput(
        feature=_feature(
            feature_snapshot_id="feature-2",
            asset_id="asset-2",
            symbol="ETH/USDT",
            return_1h=-1.0,
            return_4h=-2.0,
            return_24h=-3.0,
        ),
        authority_scanner_result={
            "scanner_family": "short_bias",
            "scanner_direction": "short",
            "opportunity_score": 48.0,
            "reasons": ["step evidence"],
        },
        authority_total_score=47.0,
        authority_score_audit={
            "contract": "universe-authority-score-audit/v1",
            "effective_opportunity_multiplier": 1.0,
            "calibration_authority_applied": False,
        },
    )
    bundle = _bundle((first, second))
    rows = [item.model_dump() for item in reversed(bundle.evidence)]

    report = replay_twin_shadow_bundle(bundle.batch.model_dump(), rows)

    assert report.matches is True
    assert report.candidate_count == 2


def test_persistence_failure_is_nonfatal_to_authority(caplog) -> None:
    bundle = _bundle()

    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("sqlite unavailable")

    result = asyncio.run(
        service._persist_twin_shadow_nonfatal(
            build_twin_shadow_bundle,
            fail,
            tuple(),
            tuple(),
            policy_factory=ContinuousScoringPolicy,
            policy_kwargs={},
        )
    )
    assert result == 0

    with caplog.at_level(logging.WARNING, logger="aletheia.universe"):
        result = asyncio.run(
            service._persist_twin_shadow_nonfatal(
                lambda *_args, **_kwargs: bundle,
                fail,
                (object(),),
                (object(),),
                policy_factory=ContinuousScoringPolicy,
                policy_kwargs={},
                scanner_run_id="run-log",
            )
        )
    assert result == 0
    assert "RuntimeError: sqlite unavailable" in caplog.text
    assert "scanner_run_id=run-log" in caplog.text


def test_capture_cadence_is_enforced_inside_repository_transaction() -> None:
    first = _bundle(scanner_run_id="run-1", captured_at="2026-08-08T00:00:00+00:00")
    too_soon = _bundle(scanner_run_id="run-2", captured_at="2026-08-08T00:10:00+00:00")
    due = _bundle(scanner_run_id="run-3", captured_at="2026-08-08T00:55:00+00:00")

    assert repo.insert_universe_twin_shadow_bundle(first.batch, first.evidence) == 1
    assert repo.insert_universe_twin_shadow_bundle(too_soon.batch, too_soon.evidence) == 0
    assert repo.get_universe_twin_shadow_batch("run-2") is None
    assert repo.universe_twin_shadow_capture_due(
        due.batch.captured_at,
        min_interval_sec=TWIN_SHADOW_CAPTURE_INTERVAL_SEC,
    ) is True
    assert repo.insert_universe_twin_shadow_bundle(due.batch, due.evidence) == 1


def test_hourly_compact_evidence_has_bounded_thirty_day_projection() -> None:
    evidence = _bundle().evidence[0]
    per_candidate = len(evidence.compact_output_json.encode()) + len(evidence.input_json.encode())
    projected_payload_bytes = per_candidate * 240 * 24 * 30

    assert projected_payload_bytes <= 600_000_000


def test_twin_retention_deletes_children_before_batches_on_fixed_window() -> None:
    bundle = _bundle()
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    old_batch = bundle.batch.model_copy(update={"captured_at": old})
    old_evidence = tuple(
        item.model_copy(update={"captured_at": old}) for item in bundle.evidence
    )
    repo.insert_universe_twin_shadow_bundle(old_batch, old_evidence)

    deleted = repo.prune_universe_twin_shadow_evidence(days=30)
    assert deleted == 2
    assert repo.get_universe_twin_shadow_batch("run-1") is None
    assert repo.list_universe_twin_shadow_records("run-1") == []


def test_task5_payload_does_not_invent_experiment_identity() -> None:
    bundle = _bundle()
    blockers = json.loads(bundle.batch.blocking_reasons_json)
    assert {
        "assignment_not_available",
        "authority_allocator_random_lineage_not_available",
        "availability_profile_not_available",
        "feature_observation_lineage_not_available",
        "policy_epoch_not_available",
        "propensity_not_available",
    } <= set(blockers)


def test_persistence_identity_conflict_fails_closed() -> None:
    bundle = _bundle()
    repo.insert_universe_twin_shadow_bundle(
        bundle.batch,
        bundle.evidence,
    )

    conflicting = bundle.batch.model_copy(update={"batch_hash": "0" * 64})
    try:
        repo.insert_universe_twin_shadow_bundle(
            conflicting,
            bundle.evidence,
        )
    except ValueError as exc:
        assert str(exc) == "universe_twin_shadow_batch_identity_conflict"
    else:
        raise AssertionError("identity conflict must fail closed")


def test_persistence_is_outside_authority_publication_transaction() -> None:
    source = textwrap.dedent(inspect.getsource(service.UniverseService.refresh))
    tree = ast.parse(source)
    persistence_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_persist_twin_shadow_nonfatal"
    ]
    assert len(persistence_calls) == 2
    relative_persistence_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_persist_relative_rank_shadow_nonfatal"
    ]
    assert len(relative_persistence_calls) == 2

    authority_transactions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "_atomic_universe_publication"
            for item in node.items
        )
    ]
    assert authority_transactions
    for transaction_node in authority_transactions:
        assert not any(call in set(ast.walk(transaction_node)) for call in persistence_calls)
        assert not any(
            call in set(ast.walk(transaction_node))
            for call in relative_persistence_calls
        )
