from __future__ import annotations

import json
from datetime import datetime, timezone

from aletheia.analytics.momentum_geometry_replay import (
    build_momentum_geometry_replay,
    CONTROL_VERSION,
    GEOMETRY_VERSION,
    measurement_contract,
    replay_momentum_geometry_pair,
)

BAR_MS = 15 * 60 * 1000


def _instant(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _bars(direction: str) -> tuple[list[dict], int]:
    cutoff = 21 * BAR_MS
    persisted_at = _instant(0)
    rows = []
    for index in range(20):
        if direction == "long":
            open_, high, low, close = 99.0, 100.0, 98.0, 99.0
        else:
            open_, high, low, close = 101.0, 102.0, 100.0, 101.0
        rows.append(
            {
                "ts": index * BAR_MS,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 10.0,
                "provider": "fixture",
                "first_persisted_at": persisted_at,
                "last_mutated_at": persisted_at,
            }
        )
    rows.append(
        {
            "ts": 20 * BAR_MS,
            "open": 99.0 if direction == "long" else 101.0,
            "high": 102.0,
            "low": 98.0,
            "close": 101.0 if direction == "long" else 99.0,
            "volume": 10.0,
            "provider": "fixture",
            "first_persisted_at": persisted_at,
            "last_mutated_at": persisted_at,
        }
    )
    return rows, cutoff


def _pair(direction: str = "long") -> tuple[dict, dict, list[dict], list[dict]]:
    bars, cutoff = _bars(direction)
    if direction == "long":
        candidate_price = 101.5
        control_zone = (101.0, 102.0)
        invalidation, target = 98.0, 108.0
        future = [
            {"ts": cutoff, "high": 100.4, "low": 99.4, "close": 100.2},
        ]
    else:
        candidate_price = 98.5
        control_zone = (98.0, 99.0)
        invalidation, target = 102.0, 92.0
        future = [
            {"ts": cutoff, "high": 100.6, "low": 99.6, "close": 99.8},
        ]
    candidate = {
        "id": f"candidate-{direction}",
        "scanner_family": "momentum",
        "scanner_direction": direction,
        "entry_price_usd": candidate_price,
        "spread_bps": 2.0,
    }
    plan = {
        "plan_id": f"plan-{direction}",
        "candidate_id": candidate["id"],
        "venue": "bitget",
        "symbol": "BTC/USDT",
        "market_type": "perp",
        "direction": direction,
        "execution_mode": "TRIGGERED",
        "trigger_spec_id": "momentum-retrace-reclaim",
        "trigger_spec_version": CONTROL_VERSION,
        "trigger_params": {
            "breakout_level": candidate_price,
            "pullback_low": control_zone[0],
            "pullback_high": control_zone[1],
        },
        "entry_zone": control_zone,
        "invalidation_price": invalidation,
        "stop_price": invalidation,
        "target_price": target,
        "approved_at": _instant(cutoff),
        "expires_at": _instant(cutoff + 4 * 60 * 60 * 1000),
        "risk_policy_id": "paper-policy",
        "risk_policy_revision": 1,
        "behavior_version": "entry-planner/v3",
        "max_slippage_bps": 3.0,
    }
    return candidate, plan, bars, future


def test_replay_proves_candidate_price_control_and_true_breakout_treatment():
    candidate, plan, bars, future = _pair()

    result = replay_momentum_geometry_pair(
        candidate=candidate,
        plan=plan,
        t0_bars=bars,
        post_t0_bars=future,
    )

    assert result["verdict"] == "candle_geometry_evaluable"
    assert result["control"]["breakout_level"] == candidate["entry_price_usd"]
    assert result["control"]["breakout_source"] == "candidate.entry_price_usd"
    assert result["treatment"]["geometry_version"] == GEOMETRY_VERSION
    assert result["treatment"]["breakout_level"] == 100.0
    assert result["treatment"]["pullback_seen_at"] == 21 * BAR_MS
    assert result["treatment"]["sweep_seen_at"] == 21 * BAR_MS
    assert result["treatment"]["reclaim_seen_at"] == 21 * BAR_MS
    assert len(result["source_candle_ids"]) == 21
    assert result["economics"]["status"] == "unavailable"


def test_replay_is_direction_symmetric():
    candidate, plan, bars, future = _pair("short")

    result = replay_momentum_geometry_pair(
        candidate=candidate,
        plan=plan,
        t0_bars=bars,
        post_t0_bars=future,
    )

    assert result["verdict"] == "candle_geometry_evaluable"
    treatment = result["treatment"]
    assert treatment["breakout_level"] == 100.0
    assert treatment["entry_zone"][1] == 100.0
    assert treatment["sweep_seen_at"] == 21 * BAR_MS
    assert treatment["reclaim_seen_at"] == 21 * BAR_MS


def test_replay_fails_closed_without_t0_availability_provenance():
    candidate, plan, bars, future = _pair()
    bars[10]["first_persisted_at"] = None

    result = replay_momentum_geometry_pair(
        candidate=candidate,
        plan=plan,
        t0_bars=bars,
        post_t0_bars=future,
    )

    assert result["verdict"] == "unavailable"
    assert result["reason"] == "t0_bar_availability_provenance_unavailable"


def test_replay_rejects_non_confirming_breakout_bar():
    candidate, plan, bars, future = _pair()
    bars[-1]["close"] = 100.0

    result = replay_momentum_geometry_pair(
        candidate=candidate,
        plan=plan,
        t0_bars=bars,
        post_t0_bars=future,
    )

    assert result["verdict"] == "unavailable"
    assert result["reason"] == "t0_breakout_not_confirmed"


def test_replay_rejects_forming_t0_bar():
    candidate, plan, bars, future = _pair()
    plan["approved_at"] = _instant(21 * BAR_MS - 1)

    result = replay_momentum_geometry_pair(
        candidate=candidate,
        plan=plan,
        t0_bars=bars,
        post_t0_bars=future,
    )

    assert result["verdict"] == "unavailable"
    assert result["reason"] == "t0_bar_forming_or_future"
def test_measurement_contract_keeps_full_denominator_and_missing_facts_unavailable():
    contract = measurement_contract()

    assert contract["no_entry_utility"] == 0.0
    assert contract["primary_metric"].startswith("treatment_cost_after_utility_per_eligible_candidate")
    assert "typed_no_trigger_and_unavailable_reasons" in contract["metrics"]
    assert contract["historical_microstructure"].startswith("unavailable")
    assert "not Candidate-linked" in contract["durable_fact_map"]["features"]
def test_builder_loads_exact_instrument_bars_without_writes(monkeypatch):
    from aletheia.analytics import momentum_geometry_replay as replay

    candidate, plan, bars, future = _pair()
    calls = []
    monkeypatch.setattr(
        replay.repo,
        "get_entry_plan",
        lambda plan_id: {
            "id": plan_id,
            "candidate_id": candidate["id"],
            "venue": plan["venue"],
            "symbol": plan["symbol"],
            "market_type": plan["market_type"],
            "plan_json": json.dumps(plan),
        },
    )
    monkeypatch.setattr(replay.repo, "get_universe_member", lambda _candidate_id: candidate)

    def candles(symbol, timeframe, limit, exchange, market_type, before_ts=None, **kwargs):
        calls.append((symbol, timeframe, limit, exchange, market_type, before_ts, kwargs))
        return bars if len(calls) == 1 else [*bars, *future]

    monkeypatch.setattr(replay.repo, "get_ohlcv_candles", candles)

    result = build_momentum_geometry_replay(plan["plan_id"])

    assert result["report"]["verdict"] == "candle_geometry_evaluable"
    assert len(calls) == 2
    assert all(call[:5] == ("BTC/USDT", "15m", 64, "bitget", "perp") for call in calls)
    assert all(call[6] == {"include_provenance": True} for call in calls)
