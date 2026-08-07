"""Task-5 contracts for durable, replayable, non-authoritative twin evidence."""

from __future__ import annotations

import asyncio
import ast
from datetime import datetime, timedelta, timezone
import inspect
import json
import random
import textwrap
from types import SimpleNamespace

from aletheia.config.schema import UniverseConfig
from aletheia.storage import repositories as repo
from aletheia.universe import service
from aletheia.universe.continuous_scoring import ContinuousScoringPolicy
from aletheia.universe.twin_allocation import replay_twin_shadow_allocations
from aletheia.universe.twin_persistence import (
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


def _bundle(items: tuple[TwinShadowInput, ...] | None = None):
    inputs = items or (_input(),)
    policy = ContinuousScoringPolicy()
    universe_config = UniverseConfig(max_candidates=1, explore_ratio=0.0)
    random_state = random.getstate()
    records = build_twin_shadow_batch(inputs, scanner_run_id="run-1", base_policy=policy)
    app_config = SimpleNamespace(
        exchange=SimpleNamespace(
            enabled_markets={"spot": True, "usdt_futures": True},
            preferred_market_type="perp",
        )
    )
    records = replay_twin_shadow_allocations(
        records,
        inputs,
        scanner_run_id="run-1",
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
        scanner_run_id="run-1",
        authority_snapshot_id="snapshot-1",
        policy=policy,
        allocator_random_state=random_state,
        universe_config=universe_config,
        enabled_markets={"spot": True, "usdt_futures": True},
        preferred_market_type="perp",
        family_tolerances={},
        regime_state="normal",
        provider_id="ccxt:binance",
        captured_at="2026-08-08T00:00:00+00:00",
    )


def test_bundle_is_complete_sanitized_and_explicitly_non_authoritative() -> None:
    bundle = _bundle()

    assert bundle.batch.candidate_count == 1
    assert bundle.batch.policy_epoch_id is None
    assert bundle.batch.availability_profile_id is None
    assert bundle.batch.promotion_eligible is False
    assert len(bundle.evidence) == 1
    evidence = bundle.evidence[0]
    assert evidence.assignment_unit_id == "run-1"
    assert evidence.assignment_arm is None
    assert evidence.propensity is None
    assert evidence.treatment_coefficient is None
    assert evidence.shadow_authority_applied is False
    assert evidence.promotion_eligible is False
    assert "must-not-be-persisted" not in evidence.input_json
    assert "api_key" not in evidence.input_json
    record_payload = json.loads(evidence.record_json)["record"]
    assert "task5_diagnostic_only" in record_payload["blocking_reasons"]
    assert "task4_diagnostic_only" not in record_payload["blocking_reasons"]
    assert record_payload["unavailable_promotion_identities"] == [
        "policy_epoch",
        "availability_profile",
        "assignment",
        "propensity",
    ]
    assert 4 <= len(bundle.outcomes) <= 8
    assert {outcome.horizon for outcome in bundle.outcomes} == {"15m", "1h", "4h", "24h"}
    assert all(outcome.allocation_selected is None for outcome in bundle.outcomes)


def test_bundle_persistence_is_atomic_idempotent_and_replayable() -> None:
    bundle = _bundle()

    inserted_records, inserted_outcomes = repo.insert_universe_twin_shadow_bundle(
        bundle.batch,
        bundle.evidence,
        list(bundle.outcomes),
    )
    assert inserted_records == 1
    assert inserted_outcomes >= 4
    retry_records, _retry_outcomes = repo.insert_universe_twin_shadow_bundle(
        bundle.batch,
        bundle.evidence,
        list(bundle.outcomes),
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


def test_persistence_failure_is_nonfatal_to_authority() -> None:
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
    assert result == (0, 0)

    result = asyncio.run(
        service._persist_twin_shadow_nonfatal(
            lambda *_args, **_kwargs: bundle,
            fail,
            (object(),),
            (object(),),
            policy_factory=ContinuousScoringPolicy,
            policy_kwargs={},
        )
    )
    assert result == (0, 0)


def test_twin_retention_deletes_children_before_batches_and_honors_clean_window() -> None:
    bundle = _bundle()
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    old_batch = bundle.batch.model_copy(update={"captured_at": old})
    old_evidence = tuple(
        item.model_copy(update={"captured_at": old}) for item in bundle.evidence
    )
    repo.insert_universe_twin_shadow_bundle(old_batch, old_evidence, list(bundle.outcomes))

    assert repo.prune_universe_twin_shadow_evidence(
        days=30,
        min_experiment_started_at=(datetime.now(timezone.utc) - timedelta(days=90)).isoformat(),
    ) == 0
    assert repo.get_universe_twin_shadow_batch("run-1") is not None

    deleted = repo.prune_universe_twin_shadow_evidence(
        days=30,
        min_experiment_started_at=None,
    )
    assert deleted == 2
    assert repo.get_universe_twin_shadow_batch("run-1") is None
    assert repo.list_universe_twin_shadow_records("run-1") == []


def test_task5_payload_does_not_invent_experiment_identity() -> None:
    bundle = _bundle()
    blockers = json.loads(bundle.batch.blocking_reasons_json)
    assert {
        "assignment_not_available",
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
        list(bundle.outcomes),
    )

    conflicting = bundle.batch.model_copy(update={"batch_hash": "0" * 64})
    try:
        repo.insert_universe_twin_shadow_bundle(
            conflicting,
            bundle.evidence,
            list(bundle.outcomes),
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
