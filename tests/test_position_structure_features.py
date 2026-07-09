from __future__ import annotations

import pytest

from aletheia.data.position_x import (
    build_position_x_agent_context,
    compute_position_x_composite_z,
    compute_position_x_features,
)


def test_compute_position_x_features_from_holder_rows():
    rows = [
        {
            "side": "long",
            "entry_price": 110.0,
            "liquidation_price": 97.0,
            "current_notional": 1_000_000.0,
            "unrealized_pnl": -120_000.0,
        },
        {
            "side": "long",
            "entry_price": 112.0,
            "liquidation_price": 95.0,
            "current_notional": 1_500_000.0,
            "unrealized_pnl": -180_000.0,
        },
        {
            "side": "short",
            "entry_price": 96.0,
            "liquidation_price": 104.0,
            "current_notional": 600_000.0,
            "unrealized_pnl": -60_000.0,
        },
        {
            "side": "short",
            "entry_price": 94.0,
            "liquidation_price": 105.0,
            "current_notional": 400_000.0,
            "unrealized_pnl": -40_000.0,
        },
    ]

    features = compute_position_x_features(
        rows,
        mark_price=100.0,
        liq_band_pct=0.15,
        value_area_ratio=0.70,
    )

    assert features["short_liq_above_usd"] == 1_000_000.0
    assert features["long_liq_below_usd"] == 2_500_000.0
    assert features["long_overhang_pnl_usd"] == -300_000.0
    assert features["short_overhang_pnl_usd"] == -100_000.0
    assert features["support"] == 96.0
    assert features["resistance"] == 110.0
    assert features["poc"] == 112.0
    assert features["value_area_low"] == 110.0
    assert features["value_area_high"] == 112.0
    assert features["cascade_vuln_pct"] == 1.0
    assert features["lfa_direction"] == "bearish"
    assert features["wp_direction"] == "bearish"


def test_compute_position_x_composite_matches_research_prior_v1():
    dims = {
        "cgo": 13.333333333333334,
        "vap": 16.666666666666664,
        "skw": 20.0,
        "lfa": 60.0,
        "wp": 10.0,
    }

    composite = compute_position_x_composite_z(dims)
    assert composite == pytest.approx(-0.87, abs=0.15)


def test_build_position_x_agent_context_marks_missing_or_stale_data():
    fresh = build_position_x_agent_context(
        {
            "symbol": "BTC/USDT",
            "as_of_time": "2026-07-07T00:00:00+00:00",
            "observed_at": "2026-07-07T00:00:00+00:00",
            "data_quality": "degraded",
            "structure_bias": "bearish",
            "composite_z": -0.9,
            "structure_rank": 25,
            "universe_size": 30,
            "dimension_scores_json": (
                '{"cgo":{"direction":"bearish","strength":"medium","percentile":13.3},'
                '"lfa":{"direction":"bearish","strength":"flat","percentile":60.0}}'
            ),
            "risk_context_json": '{"convexity":{"detail":"23% of band fuel"},'
            '"cascade":{"detail":"0.4% of OI within ±5%"}}',
        },
        now_iso="2026-07-07T00:10:00+00:00",
    )
    stale = build_position_x_agent_context(
        {
            "symbol": "BTC/USDT",
            "as_of_time": "2026-07-07T00:00:00+00:00",
            "observed_at": "2026-07-07T00:00:00+00:00",
            "data_quality": "complete",
        },
        now_iso="2026-07-07T03:00:00+00:00",
    )
    missing = build_position_x_agent_context(None, now_iso="2026-07-07T00:10:00+00:00")

    assert fresh["status"] == "ok"
    assert fresh["advisory_only"] is True
    assert fresh["bias"] == "bearish"
    assert fresh["caveats"] == ["degraded_data"]
    assert "cgo" in fresh["dimension_reads"]
    assert stale["status"] == "insufficient_data"
    assert missing["status"] == "insufficient_data"
