from __future__ import annotations

from dataclasses import replace

from aletheia.config.schema import UniverseConfig
from aletheia.storage import repositories as repo
from aletheia.universe.twin_scoring import TwinShadowInput


def _input(index: int, *, score: float, market_type: str = "spot") -> TwinShadowInput:
    feature = {
        "feature_snapshot_id": f"feature-{index}",
        "asset_id": f"asset-{index}",
        "symbol": f"COIN{index}/USDT",
        "venue": "binance",
        "market_type": market_type,
        "last_price": 100.0 + index,
        "quote_volume_24h": 6_000_000 + index * 100_000,
        "spread_bps": 20 - index * 0.1,
        "depth_10bps_usd": 30_000 + index * 1_000,
        "data_freshness_sec": 100 + index,
        "return_24h": score,
        "relative_strength_24h": score,
        "return_4h": score / 2,
        "return_1h": score / 4,
        "rsi_14": 60.0,
    }
    if market_type == "perp":
        feature.update(
            funding_rate=0.0001 + index * 0.000001,
            open_interest=1_000_000 + index * 10_000,
            oi_delta_1h=1.0 + index * 0.1,
            long_short_ratio=1.1 + index * 0.01,
            top_trader_position_ratio=1.2 + index * 0.01,
        )
    return TwinShadowInput(
        feature=feature,
        authority_scanner_result={
            "scanner_family": "momentum",
            "scanner_direction": "long",
            "opportunity_score": score,
        },
        authority_total_score=score,
        authority_score_audit={},
    )


def test_relative_rank_uses_actual_dynamic_n_and_average_ties():
    from aletheia.universe.relative_rank_shadow import build_relative_rank_batch

    inputs = [_input(index, score=5.0 + index) for index in range(20)]
    inputs[1].feature["relative_strength_24h"] = inputs[0].feature["relative_strength_24h"]
    batch = build_relative_rank_batch(inputs, scanner_run_id="run-1")

    assert batch.available is True
    assert batch.candidate_count == 20
    assert batch.eligible_count == 20
    assert all(record.relative_score is not None for record in batch.records)


def test_relative_rank_fails_closed_below_minimum_n():
    from aletheia.universe.relative_rank_shadow import build_relative_rank_batch

    batch = build_relative_rank_batch(
        [_input(index, score=5.0 + index) for index in range(19)],
        scanner_run_id="run-2",
    )
    assert batch.available is False
    assert batch.unavailable_reason == "minimum_candidate_count_not_met"


def test_perp_requires_public_positioning_inputs():
    from aletheia.universe.relative_rank_shadow import build_relative_rank_batch

    inputs = [_input(index, score=5.0 + index) for index in range(19)]
    perp = _input(20, score=25.0, market_type="perp")
    del perp.feature["top_trader_position_ratio"]
    batch = build_relative_rank_batch([*inputs, perp], scanner_run_id="run-3", min_candidate_count=19)
    record = next(item for item in batch.records if item.market_type == "perp")
    assert record.eligible is False
    assert record.invalid_reason == "missing_top_trader_position_ratio"


def test_relative_rank_persists_isolated_cluster_and_outcome():
    from aletheia.universe.continuous_scoring import ContinuousScoringPolicy
    from aletheia.universe.relative_rank_experiment import (
        build_prospective_relative_rank_bundle,
    )
    from aletheia.universe.relative_rank_shadow import build_relative_rank_batch

    inputs = [_input(index, score=5.0 + index) for index in range(20)]
    batch = build_relative_rank_batch(inputs, scanner_run_id="run-persist")
    batch = replace(
        batch,
        records=tuple(
            replace(
                record,
                authority_selected=index == 0,
                relative_selected=index == 0,
            )
            for index, record in enumerate(batch.records)
        ),
    )
    bundle = build_prospective_relative_rank_bundle(
        batch,
        inputs,
        authority_snapshot_id="snapshot-1",
        policy=ContinuousScoringPolicy(),
        universe_config=UniverseConfig(),
        enabled_markets={"spot": True, "perp": True},
        preferred_market_type="perp",
        family_tolerances={},
        provider_id="ccxt:binance",
        outcome_venue="binance",
        regime_state="sideways",
        captured_at="2026-08-10T00:00:00+00:00",
    )

    assert (
        repo.insert_relative_rank_shadow_bundle(
            bundle.batch,
            outcomes=bundle.outcomes,
            min_interval_sec=0,
        )
        == 1
    )
    assert (
        repo.insert_relative_rank_shadow_bundle(
            bundle.batch,
            outcomes=bundle.outcomes,
            min_interval_sec=0,
        )
        == 0
    )
    stored = repo.list_relative_rank_shadow_batches(bundle.batch.policy_epoch_id)
    assert len(stored) == 1
    assert stored[0]["promotion_eligible"] == 0
    assert len(bundle.outcomes) == 1
    assert bundle.outcomes[0].outcome_candidate_key.startswith(f"relative-rank:{bundle.batch.experiment_id}:")
    repo.get_db().execute(
        "UPDATE filter_candidate_outcomes SET status='settled', net_return_pct=1.25, settled_at=due_at WHERE id=?",
        (bundle.outcomes[0].id,),
    )
    from aletheia.universe.relative_rank_evaluation import (
        evaluate_relative_rank_shadow_epoch,
    )

    report = evaluate_relative_rank_shadow_epoch(bundle.batch.policy_epoch_id)
    assert report["complete_cluster_n"] == 1
    assert report["verdict"] == "insufficient_data"
    assert report["promotion_eligible"] is False
