from __future__ import annotations

from dataclasses import replace

import pytest
from aletheia.analytics.signal_replay import (
    Signal,
    block_bootstrap_mean_ci,
    random_entry_baseline,
    replay_signal,
)


def _bars(*ohlc: tuple[float, float, float, float], flat: int = 20) -> list[dict[str, float]]:
    """Hand-built 1m bars; ``flat`` quiet bars at 100 follow the scripted ones."""
    rows = list(ohlc) + [(100.0, 100.1, 99.9, 100.0)] * flat
    return [
        {"ts": float(i * 60_000), "open": o, "high": h, "low": low, "close": c} for i, (o, h, low, c) in enumerate(rows)
    ]


LONG = Signal(instrument="BTC-USDT-SWAP", direction="long", decision_ts=0, stop_price=99.0, target_price=102.0)


def test_stop_first_when_stop_and_target_share_one_bar():
    bars = _bars((100.0, 102.5, 98.5, 100.0))

    result = replay_signal(LONG, bars)

    assert result.exit_reason == "stop"
    assert result.exit_price == 99.0
    assert result.gross_r == pytest.approx(-1.0)
    assert result.ambiguity == ("stop_target_same_bar",)
    assert result.mfe_r == 0.0  # the stop bar's favourable extreme is not credited


def test_gap_through_stop_fills_at_the_bar_open():
    bars = _bars((100.0, 100.5, 99.5, 100.2), (97.0, 97.5, 96.5, 97.2))

    result = replay_signal(LONG, bars)

    assert (result.exit_reason, result.exit_price, result.held_bars) == ("stop", 97.0, 2)
    assert result.gross_r == pytest.approx(-3.0)
    assert result.mae_r == pytest.approx(3.0)
    assert result.ambiguity == ()


def test_limit_touched_but_not_crossed_does_not_fill_and_expires():
    signal = replace(
        LONG, entry_type="limit", entry_price=99.5, stop_price=98.5, entry_lifetime_bars=3, time_barrier_bars=5
    )
    bars = _bars(*[(100.0, 100.2, 99.5, 100.0)] * 3)  # low == limit: touched, never traded through

    result = replay_signal(signal, bars)

    assert (result.status, result.exit_reason, result.filled) == ("evaluated", "expired", False)

    crossed = replay_signal(signal, _bars((100.0, 100.2, 99.4, 99.8)))
    assert crossed.filled and crossed.entry_price == 99.5


def test_limit_gap_below_fills_at_open_and_stop_breach_before_fill_cancels():
    signal = replace(
        LONG, entry_type="limit", entry_price=99.5, stop_price=98.5, entry_lifetime_bars=5, time_barrier_bars=5
    )

    gapped = replay_signal(signal, _bars((99.2, 99.6, 99.0, 99.4)))
    assert gapped.entry_price == 99.2

    stop_entry = replace(LONG, entry_type="stop", entry_price=101.0, entry_lifetime_bars=5)
    cancelled = replay_signal(stop_entry, _bars((100.0, 100.3, 98.9, 99.5)))
    assert (cancelled.exit_reason, cancelled.filled) == ("unfilled", False)


def test_stop_entry_fills_at_trigger_and_gap_above_fills_at_open():
    signal = replace(
        LONG, entry_type="stop", entry_price=100.5, entry_lifetime_bars=5, target_price=104.0, time_barrier_bars=5
    )
    bars = _bars((100.0, 100.3, 99.8, 100.1), (100.2, 100.8, 100.1, 100.6))

    result = replay_signal(signal, bars)

    assert result.filled
    assert (result.entry_ts, result.entry_price) == (60_000, 100.5)

    gapped = replay_signal(signal, _bars((100.0, 100.3, 99.8, 100.1), (101.0, 101.2, 100.9, 101.1)))
    assert gapped.entry_price == 101.0


def test_time_barrier_exits_at_the_next_bar_open_with_hourly_funding():
    signal = replace(
        LONG,
        target_price=None,
        time_barrier_bars=3,
        entry_fee_bps=6.0,
        exit_fee_bps=6.0,
        funding_bps_per_hour=1.2,
    )
    bars = _bars(
        (100.0, 100.4, 99.8, 100.2),
        (100.2, 100.6, 100.0, 100.4),
        (100.4, 100.9, 100.3, 100.8),
        (100.8, 101.0, 100.7, 100.9),
    )

    result = replay_signal(signal, bars)

    assert (result.exit_reason, result.held_bars, result.exit_ts, result.exit_price) == ("time", 3, 180_000, 100.8)
    assert result.gross_return_pct == pytest.approx(0.8)
    assert result.explicit_cost_pct == pytest.approx((12.0 + 1.2 * 3 / 60) / 100)
    assert result.net_r == pytest.approx(0.8 - (12.0 + 1.2 * 3 / 60) / 100)  # 1R == 1% here
    assert result.mfe_pct == pytest.approx(0.9)
    assert result.mae_pct == pytest.approx(0.2)


def test_target_hit_with_adverse_slippage_and_short_symmetry():
    bars = _bars((100.0, 100.5, 99.6, 100.4), (100.4, 102.3, 100.2, 102.1))

    result = replay_signal(replace(LONG, slippage_bps=10.0), bars)

    assert result.exit_reason == "target"
    assert result.entry_price == pytest.approx(100.1)
    assert result.exit_price == pytest.approx(102.0 * 0.999)
    assert result.gross_r == pytest.approx((102.0 * 0.999 - 100.1) / (100.1 - 99.0))

    short = Signal(instrument="x", direction="short", decision_ts=0, stop_price=101.0, target_price=98.0)
    mirrored = [
        {
            **bar,
            "open": 200 - bar["open"],
            "high": 200 - bar["low"],
            "low": 200 - bar["high"],
            "close": 200 - bar["close"],
        }
        for bar in bars
    ]
    assert replay_signal(short, mirrored).gross_r == pytest.approx(replay_signal(LONG, bars).gross_r)


def test_market_delay_and_missing_coverage_are_explicit():
    delayed = replay_signal(
        replace(LONG, delay_bars=2, decision_ts=30_000, time_barrier_bars=3),
        _bars((100, 100.1, 99.9, 100), (100, 100.1, 99.9, 100), (100.3, 100.4, 100.2, 100.3)),
    )
    assert delayed.entry_ts == 180_000  # decision bar is ts 60_000, plus two bars of delay

    short = replay_signal(replace(LONG, target_price=None), _bars(flat=5))
    assert (short.status, short.exit_reason) == ("not_evaluable", "path_coverage_ended_before_exit")


def test_random_baseline_keeps_geometry_and_is_seeded():
    bars = _bars(flat=200)
    bars[50]["open"] = 50.0
    signals = [replace(LONG, decision_ts=i * 60_000) for i in range(5)]

    first = random_entry_baseline(signals, {LONG.instrument: bars}, seed=7)
    again = random_entry_baseline(signals, {LONG.instrument: bars}, seed=7)

    assert first == again
    assert {s.direction for s in random_entry_baseline(signals * 20, {LONG.instrument: bars}, seed=1)} == {
        "long",
        "short",
    }
    for source, base in zip(signals, first, strict=True):
        ref = next(bar["open"] for bar in bars if bar["ts"] == base.decision_ts)
        sign = 1 if base.direction == "long" else -1
        assert (ref - base.stop_price) * sign / ref == pytest.approx(0.01)
        assert base.target_price is not None
        assert (base.target_price - ref) * sign / ref == pytest.approx(0.02)
        assert base.instrument == source.instrument

    mirror = random_entry_baseline(
        signals, {LONG.instrument: bars}, seed=7, direction_mode="mirror", randomize_time=False
    )
    assert [(s.direction, s.decision_ts, s.stop_price, s.target_price) for s in mirror] == [
        ("short", s.decision_ts, 101.0, 98.0) for s in signals
    ]


@pytest.mark.parametrize("stationary", [False, True])
def test_block_bootstrap_is_deterministic_with_a_seed(stationary):
    values = [((i * 37) % 11 - 5) / 10 for i in range(40)]

    first = block_bootstrap_mean_ci(values, block_length=5, seed=3, draws=500, stationary=stationary)
    again = block_bootstrap_mean_ci(values, block_length=5, seed=3, draws=500, stationary=stationary)
    other = block_bootstrap_mean_ci(values, block_length=5, seed=4, draws=500, stationary=stationary)

    assert first == again
    assert first["ci"] != other["ci"]
    assert first["ci"][0] < first["mean"] < first["ci"][1]
