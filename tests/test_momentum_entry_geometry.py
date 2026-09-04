from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aletheia.trading.momentum_entry_geometry import (
    BAR_MS,
    GEOMETRY_VERSION,
    MomentumEntryGeometry,
    MomentumGeometryUnavailable,
    compile_momentum_entry_geometry,
)


def _instant(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _candidate(direction: str) -> dict:
    return {
        "id": f"candidate-{direction}",
        "snapshot_id": "snapshot-1",
        "asset_id": "asset-btc",
        "venue": "bitget",
        "symbol": "BTC/USDT",
        "market_type": "perp",
        "scanner_family": "momentum",
        "scanner_direction": direction,
        "entry_price_usd": 101.5 if direction == "long" else 98.5,
    }


def _bars(direction: str) -> tuple[list[dict], int]:
    cutoff = 21 * BAR_MS
    bars = []
    for index in range(20):
        bars.append(
            {
                "ts": index * BAR_MS,
                "open": 99.0 if direction == "long" else 101.0,
                "high": 100.0 if direction == "long" else 102.0,
                "low": 98.0 if direction == "long" else 100.0,
                "close": 99.0 if direction == "long" else 101.0,
                "volume": 10.0,
                "provider": "fixture",
                "exchange": "bitget",
                "symbol": "BTC/USDT",
                "market_type": "perp",
                "timeframe": "15m",
                "first_persisted_at": _instant(0),
                "last_mutated_at": _instant(0),
            }
        )
    bars.append(
        {
            "ts": 20 * BAR_MS,
            "open": 100.0,
            "high": 102.0,
            "low": 98.0,
            "close": 101.0 if direction == "long" else 99.0,
            "volume": 10.0,
            "provider": "fixture",
            "exchange": "bitget",
            "symbol": "BTC/USDT",
            "market_type": "perp",
            "timeframe": "15m",
            "first_persisted_at": _instant(0),
            "last_mutated_at": _instant(0),
        }
    )
    return bars, cutoff


def _compile(direction: str = "long", *, minimum_risk_reward: float = 1.5):
    bars, cutoff = _bars(direction)
    return compile_momentum_entry_geometry(
        candidate=_candidate(direction),
        feature={
            "id": "feature-1",
            "asset_id": "asset-btc",
            "ts": _instant(20 * BAR_MS),
            "atr_15m_pct": 2.0,
        },
        candles=bars,
        direction=direction,
        minimum_risk_reward=minimum_risk_reward,
        source_cutoff_at=_instant(cutoff),
        valid_until=_instant(cutoff + 60 * 60 * 1000),
    )


def test_long_geometry_uses_true_breakout_and_worst_entry_rr():
    geometry = _compile()

    assert isinstance(geometry, MomentumEntryGeometry)
    assert geometry.geometry_version == GEOMETRY_VERSION
    assert geometry.breakout_level == 100.0
    assert geometry.atr_abs == pytest.approx(30 / 14)
    assert geometry.entry_zone[0] == pytest.approx(100.0)
    assert geometry.invalidation_price == pytest.approx(100.0 - 30 / 14)
    assert len(geometry.source_candle_ids) == 21
    assert geometry.candidate_asset_id == "asset-btc"
    for entry in geometry.entry_zone:
        assert (geometry.target_price - entry) / (entry - geometry.stop_price) >= 1.5


def test_short_geometry_is_direction_symmetric():
    geometry = _compile("short")

    assert geometry.breakout_level == 100.0
    assert geometry.entry_zone[1] == pytest.approx(100.0)
    assert geometry.invalidation_price == pytest.approx(100.0 + 30 / 14)
    for entry in geometry.entry_zone:
        assert (entry - geometry.target_price) / (geometry.stop_price - entry) >= 1.5


def test_geometry_exports_exact_trigger_payload():
    geometry = _compile()
    params = geometry.trigger_params()

    from aletheia.trading.trigger_specs import require_registered_trigger_spec

    contract, normalized = require_registered_trigger_spec(
        "momentum-retrace-reclaim-geometry", params
    )
    assert contract.spec_id == "momentum-retrace-reclaim-geometry"
    assert normalized["breakout_level"] == geometry.breakout_level
    assert normalized["atr_abs"] == geometry.atr_abs
    assert normalized["geometry_version"] == GEOMETRY_VERSION
    assert normalized["geometry_fingerprint"] == geometry.geometry_fingerprint


def test_geometry_rejects_future_forming_or_stale_latest_bar():
    bars, cutoff = _bars("long")
    candidate = _candidate("long")
    feature = {"id": "feature-1", "asset_id": "asset-btc", "ts": _instant(cutoff)}
    kwargs = {
        "candidate": candidate,
        "feature": feature,
        "direction": "long",
        "minimum_risk_reward": 1.5,
        "source_cutoff_at": _instant(cutoff),
        "valid_until": _instant(cutoff + BAR_MS),
    }

    forming = [*bars]
    forming[-1]["is_closed"] = False
    with pytest.raises(MomentumGeometryUnavailable, match="bar_forming"):
        compile_momentum_entry_geometry(candles=forming, **kwargs)

    stale = [*bars]
    for bar in stale:
        bar["is_closed"] = True
    stale_kwargs = dict(kwargs)
    stale_kwargs["source_cutoff_at"] = _instant(22 * BAR_MS)
    stale_kwargs["valid_until"] = _instant(23 * BAR_MS)
    with pytest.raises(MomentumGeometryUnavailable, match="latest_completed_bar_missing"):
        compile_momentum_entry_geometry(candles=stale, **stale_kwargs)


def test_geometry_rejects_gap_identity_and_provenance_mismatch():
    bars, cutoff = _bars("long")
    kwargs = {
        "candidate": _candidate("long"),
        "feature": {"id": "feature-1", "asset_id": "asset-btc", "ts": _instant(cutoff)},
        "direction": "long",
        "minimum_risk_reward": 1.5,
        "source_cutoff_at": _instant(cutoff),
        "valid_until": _instant(cutoff + BAR_MS),
    }
    gap = [*bars]
    gap[10]["ts"] += BAR_MS
    with pytest.raises(MomentumGeometryUnavailable, match="bar_sequence_discontinuous"):
        compile_momentum_entry_geometry(candles=gap, **kwargs)

    wrong_instrument = [*bars]
    wrong_instrument[5]["exchange"] = "other-venue"
    with pytest.raises(MomentumGeometryUnavailable, match="bar_instrument_identity_mismatch"):
        compile_momentum_entry_geometry(candles=wrong_instrument, **kwargs)

    missing_provenance = [*bars]
    missing_provenance[5]["exchange"] = "bitget"
    missing_provenance[5]["last_mutated_at"] = None
    with pytest.raises(MomentumGeometryUnavailable, match="bar_availability_provenance_unavailable"):
        compile_momentum_entry_geometry(candles=missing_provenance, **kwargs)


def test_geometry_rejects_non_confirming_breakout_and_future_feature():
    bars, cutoff = _bars("long")
    kwargs = {
        "candidate": _candidate("long"),
        "feature": {"id": "feature-1", "asset_id": "asset-btc", "ts": _instant(cutoff)},
        "candles": bars,
        "direction": "long",
        "minimum_risk_reward": 1.5,
        "source_cutoff_at": _instant(cutoff),
        "valid_until": _instant(cutoff + BAR_MS),
    }

    non_confirming = [*bars]
    non_confirming[-1]["close"] = 100.0
    with pytest.raises(MomentumGeometryUnavailable, match="breakout_confirmation_missing"):
        compile_momentum_entry_geometry(**kwargs | {"candles": non_confirming})

    future_feature = dict(kwargs)
    future_feature["feature"] = {
        "id": "feature-1",
        "asset_id": "asset-btc",
        "ts": _instant(cutoff + 1),
    }
    with pytest.raises(MomentumGeometryUnavailable, match="feature_snapshot_after_cutoff"):
        compile_momentum_entry_geometry(**future_feature)


def test_geometry_trigger_is_registered_but_not_planner_visible():
    from aletheia.trading.trigger_specs import compatible_trigger_specs, registered_trigger_specs

    assert [spec.spec_id for spec in compatible_trigger_specs("momentum")] == [
        "momentum-retrace-reclaim"
    ]
    geometry = next(
        spec for spec in registered_trigger_specs()
        if spec.spec_id == "momentum-retrace-reclaim-geometry"
    )
    assert geometry.planner_visible is False
    with pytest.raises(ValueError, match="trigger_spec_not_planner_visible"):
        from aletheia.trading.trigger_specs import require_compatible_trigger_spec

        require_compatible_trigger_spec("momentum", geometry.spec_id)
