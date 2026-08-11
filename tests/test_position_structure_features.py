from __future__ import annotations

import json

import pytest

from aletheia.data.position_x import (
    build_position_x_agent_context,
    compute_cross_sectional_percentiles,
    compute_position_x_batch,
    compute_position_x_composite_z,
    compute_position_x_features,
)


def _position_x_markets():
    return [
        {
            "symbol": symbol,
            "venue": "hyperliquid",
            "market_type": "perp",
            "as_of_time": f"2026-08-11T00:0{index}:00+00:00",
            "mark_price": 100.0,
            "total_market_oi_usd": 1_000.0,
            "coverage_ratio": coverage,
            "holders": holders,
        }
        for index, (symbol, coverage, holders) in enumerate([
            ("BTC/USDT", 0.95, [
                {"side": "long", "entry_price": 110.0, "liquidation_price": 95.0,
                 "current_notional": 100.0, "unrealized_pnl": -10.0},
                {"side": "short", "entry_price": 98.0, "liquidation_price": 103.0,
                 "current_notional": 300.0, "unrealized_pnl": -100.0},
            ]),
            ("ETH/USDT", 0.90, [
                {"side": "long", "entry_price": 105.0, "liquidation_price": 96.0,
                 "current_notional": 200.0, "unrealized_pnl": -50.0},
                {"side": "short", "entry_price": 95.0, "liquidation_price": 104.0,
                 "current_notional": 200.0, "unrealized_pnl": -50.0},
            ]),
            ("SOL/USDT", 0.85, [
                {"side": "long", "entry_price": 102.0, "liquidation_price": 97.0,
                 "current_notional": 300.0, "unrealized_pnl": -100.0},
                {"side": "short", "entry_price": 90.0, "liquidation_price": 105.0,
                 "current_notional": 100.0, "unrealized_pnl": -10.0},
            ]),
        ])
    ]


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
        total_market_oi_usd=5_000_000.0,
    )

    assert features["short_liq_above_usd"] == 1_000_000.0
    assert features["long_liq_below_usd"] == 2_500_000.0
    assert features["long_overhang_pnl_usd"] == -300_000.0
    assert features["short_overhang_pnl_usd"] == -100_000.0
    assert features["cgo_raw"] == pytest.approx(-1.0986, abs=0.001)
    assert features["cgo_direction"] == "bearish"
    assert features["support"] == pytest.approx(96.0, rel=0.0025)
    assert features["resistance"] == pytest.approx(110.0, rel=0.0025)
    assert features["poc"] == pytest.approx(112.0, rel=0.0025)
    assert features["value_area_low"] == pytest.approx(110.0, rel=0.0025)
    assert features["value_area_high"] == pytest.approx(112.0, rel=0.0025)
    assert features["value_area_covered_notional_ratio"] >= 0.70
    assert features["top10_concentration"] == 1.0
    assert features["top50_concentration"] == 1.0
    assert features["holder_hhi"] == pytest.approx(0.307755, abs=0.000001)
    assert features["effective_holder_count"] == pytest.approx(3.249337, abs=0.000001)
    assert features["cascade_vuln_pct"] == 0.7
    assert features["sample_cascade_vuln_pct"] == 1.0
    assert features["availability"]["market_cascade"] == "available"
    assert features["lfa_direction"] == "bearish"
    assert features["wp_direction"] == "bearish"


def test_holder_concentration_ranks_holders_not_entry_price_buckets():
    rows = [
        {
            "side": "long",
            "entry_price": 100.0,
            "current_notional": float(notional),
        }
        for notional in range(1, 13)
    ]

    features = compute_position_x_features(rows, mark_price=100.0)

    assert features["cost_profile_bin_count"] == 1
    assert features["top10_concentration"] == pytest.approx(75 / 78, abs=0.000001)
    assert features["top50_concentration"] == 1.0


def test_cost_and_liquidation_bins_form_contiguous_profile_and_clustered_walls():
    rows = [
        {
            "side": "short",
            "entry_price": 97.0,
            "liquidation_price": 104.0,
            "current_notional": 10.0,
            "unrealized_pnl": -1.0,
        },
        {
            "side": "short",
            "entry_price": 97.2,
            "liquidation_price": 104.2,
            "current_notional": 10.0,
            "unrealized_pnl": -1.0,
        },
        {
            "side": "long",
            "entry_price": 103.0,
            "liquidation_price": 96.0,
            "current_notional": 10.0,
            "unrealized_pnl": -1.0,
        },
        {
            "side": "long",
            "entry_price": 103.2,
            "liquidation_price": 96.2,
            "current_notional": 10.0,
            "unrealized_pnl": -1.0,
        },
    ]

    features = compute_position_x_features(
        rows,
        mark_price=100.0,
        price_bin_width_pct=0.01,
        min_wall_share=0.10,
        total_market_oi_usd=40.0,
    )

    assert features["cost_profile_bin_count"] == 2
    assert features["value_area_bin_count"] == 7
    assert features["value_area_covered_notional_ratio"] == 1.0
    assert features["support_wall_count"] == 1
    assert features["resistance_wall_count"] == 1
    assert features["support_wall_notional_usd"] == 20.0
    assert features["resistance_wall_notional_usd"] == 20.0
    assert features["support_wall_share"] == 0.5
    assert features["resistance_wall_share"] == 0.5
    assert features["skw_raw"] is not None
    assert features["wp_raw"] == 0.0
    assert features["vap_raw"] == pytest.approx(0.5, abs=0.02)
    assert features["convexity"] == 0.5


def test_missing_position_inputs_stay_unavailable_instead_of_zero():
    features = compute_position_x_features(
        [{"side": "long", "current_notional": "not available"}],
        mark_price=100.0,
    )

    assert features["poc"] is None
    assert features["convexity"] is None
    assert features["cascade_vuln_pct"] is None
    assert features["sample_cascade_vuln_pct"] is None
    assert features["top10_concentration"] is None
    assert features["cgo_direction"] is None
    assert set(features["availability"].values()) == {"unavailable"}

    invalid_denominator = compute_position_x_features(
        [{"side": "long", "current_notional": 100.0, "liquidation_price": 99.0}],
        mark_price=100.0,
        total_market_oi_usd=50.0,
    )
    assert invalid_denominator["cascade_vuln_pct"] is None
    assert invalid_denominator["availability"]["market_cascade"] == "invalid_denominator"


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


def test_cross_sectional_percentiles_average_ties_and_keep_missing_unavailable():
    ranks = compute_cross_sectional_percentiles({
        "a": 1.0, "b": 2.0, "c": 2.0, "d": 4.0, "missing": None, "invalid": float("nan"),
    })

    assert ranks == {
        "a": 0.0, "b": 50.0, "c": 50.0, "d": 100.0, "missing": None, "invalid": None,
    }


def test_position_x_batch_builds_exact_universe_and_sensitivity_evidence():
    markets = _position_x_markets()
    baseline = compute_position_x_batch(markets)
    wider_bins = compute_position_x_batch(markets, price_bin_width_pct=0.01)

    assert baseline["universe_size"] == 3
    assert baseline["mean_coverage_ratio"] == 0.9
    assert baseline["batch_id"] == compute_position_x_batch(markets)["batch_id"]
    assert baseline["batch_id"] == compute_position_x_batch(list(reversed(markets)))["batch_id"]
    assert baseline["batch_id"] != wider_bins["batch_id"]
    assert {row["symbol"] for row in baseline["rows"]} == {"BTC/USDT", "ETH/USDT", "SOL/USDT"}
    assert all(row["universe_size"] == 3 for row in baseline["rows"])
    assert all(row["structure_rank"] >= 1 for row in baseline["rows"])
    for row, wider in zip(
        sorted(baseline["rows"], key=lambda item: item["symbol"]),
        sorted(wider_bins["rows"], key=lambda item: item["symbol"]),
        strict=True,
    ):
        dimensions = json.loads(row["dimension_scores_json"])
        risk = json.loads(row["risk_context_json"])
        wider_risk = json.loads(wider["risk_context_json"])
        assert set(dimensions) == {"cgo", "vap", "skw", "lfa", "wp"}
        assert risk["benchmark_version"] == "position-x-p1-v1"
        assert "equal_weight_score" in risk
        assert risk["no_structure_score"] == 0.0
        assert risk["availability_stratum"]
        assert risk["value_area_covered_notional_ratio"] >= 0.70
        assert risk["holder_hhi"] == wider_risk["holder_hhi"]
        assert risk["parameters"]["price_bin_width_pct"] == 0.0025
        assert wider_risk["parameters"]["price_bin_width_pct"] == 0.01


def test_position_x_batch_keeps_ties_and_missing_dimensions_auditable():
    markets = _position_x_markets()
    markets.append({**markets[1], "symbol": "XRP/USDT"})

    batch = compute_position_x_batch(markets)
    rows = {row["symbol"]: row for row in batch["rows"]}
    eth_dimensions = json.loads(rows["ETH/USDT"]["dimension_scores_json"])
    xrp_dimensions = json.loads(rows["XRP/USDT"]["dimension_scores_json"])
    btc_dimensions = json.loads(rows["BTC/USDT"]["dimension_scores_json"])

    assert eth_dimensions == xrp_dimensions
    assert rows["ETH/USDT"]["structure_rank"] == rows["XRP/USDT"]["structure_rank"]
    assert btc_dimensions["vap"]["raw"] is None
    assert btc_dimensions["vap"]["percentile"] is None
    assert json.loads(rows["BTC/USDT"]["risk_context_json"])["availability_stratum"] != (
        json.loads(rows["ETH/USDT"]["risk_context_json"])["availability_stratum"]
    )


@pytest.mark.parametrize(("parameter", "value"), [
    ("price_bin_width_pct", 0.001),
    ("price_bin_width_pct", 0.005),
    ("price_bin_width_pct", 0.01),
    ("liq_band_pct", 0.05),
    ("liq_band_pct", 0.10),
    ("liq_band_pct", 0.20),
    ("value_area_ratio", 0.68),
    ("value_area_ratio", 0.80),
    ("min_wall_share", 0.005),
    ("min_wall_share", 0.02),
    ("min_wall_share", 0.05),
])
def test_position_x_batch_declared_parameter_sensitivity(parameter, value):
    batch = compute_position_x_batch(_position_x_markets(), **{parameter: value})

    assert batch["parameters"][parameter] == value
    assert all(row["universe_size"] == 3 for row in batch["rows"])
    if parameter == "value_area_ratio":
        assert all(
            json.loads(row["risk_context_json"])["value_area_covered_notional_ratio"] >= value
            for row in batch["rows"]
        )


def test_position_x_batch_rejects_incomplete_or_skewed_universe():
    markets = _position_x_markets()
    markets[0]["coverage_ratio"] = 0.79
    with pytest.raises(ValueError, match="coverage_ratio below"):
        compute_position_x_batch(markets)

    markets = _position_x_markets()
    markets[2]["as_of_time"] = "2026-08-11T00:10:01+00:00"
    with pytest.raises(ValueError, match="exceed max_source_skew_seconds"):
        compute_position_x_batch(markets)


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
