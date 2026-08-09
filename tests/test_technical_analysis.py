from __future__ import annotations

import math

import pytest

from aletheia.trading.technical_analysis import (
    BatchTechnicalAnalyzer,
    IncrementalTechnicalAnalyzer,
    TECHNICAL_ANALYSIS_VERSION,
    TechnicalBar,
)


def _bars(count: int = 80, *, timeframe_ms: int = 3_600_000) -> list[TechnicalBar]:
    rows = []
    close = 100.0
    for index in range(count):
        opening = close
        close = opening + math.sin(index / 4.0) * 0.7 + 0.15
        rows.append(
            TechnicalBar(
                ts=1_700_000_000_000 + index * timeframe_ms,
                open=opening,
                high=max(opening, close) + 0.8,
                low=min(opening, close) - 0.6,
                close=close,
                volume=1_000.0 + index * 3.0,
            )
        )
    return rows


def _assert_value_maps_close(left: dict, right: dict) -> None:
    assert left.keys() == right.keys()
    for key in left:
        if left[key] is None or right[key] is None or isinstance(left[key], bool):
            assert left[key] == right[key], key
        else:
            assert float(left[key]) == pytest.approx(float(right[key]), rel=1e-11, abs=1e-11), key


def test_incremental_snapshot_matches_independent_batch_reference() -> None:
    bars = _bars()
    incremental = IncrementalTechnicalAnalyzer(
        exchange="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        timeframe="1h",
    )
    actual = incremental.extend(bars)
    expected = BatchTechnicalAnalyzer().compute(
        bars,
        exchange="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        timeframe="1h",
    )

    assert actual.algorithm_version == TECHNICAL_ANALYSIS_VERSION
    assert actual.input_fingerprint == expected.input_fingerprint
    assert actual.warmup_complete is True
    _assert_value_maps_close(actual.values, expected.values)
    assert incremental.verify()["ok"] is True


def test_normal_snapshot_path_does_not_call_batch_analyzer(monkeypatch) -> None:
    incremental = IncrementalTechnicalAnalyzer(
        exchange="bitget", symbol="ETH/USDT", market_type="spot", timeframe="1h"
    )
    incremental.extend(_bars())

    def forbidden(*args, **kwargs):
        raise AssertionError("incremental snapshot called the batch reference")

    monkeypatch.setattr(BatchTechnicalAnalyzer, "compute", forbidden)
    snapshot = incremental.snapshot()
    assert snapshot.values["rsi_14"] is not None


def test_steady_state_after_recovery_window_remains_append_only(monkeypatch) -> None:
    incremental = IncrementalTechnicalAnalyzer(
        exchange="bitget", symbol="BTC/USDT", market_type="perp", timeframe="1h",
        max_history=64,
    )
    incremental.extend(_bars(64))

    def forbidden(*args, **kwargs):
        raise AssertionError("steady-state append triggered a bounded rebuild")

    monkeypatch.setattr(incremental, "_rebuild", forbidden)
    snapshot = incremental.update(_bars(65)[-1])
    assert snapshot.bars_used == 65
    assert snapshot.history_bars_retained == 64
    assert incremental.revision_count == 0


def test_revision_rebuilds_bounded_state_and_remains_numerically_exact() -> None:
    bars = _bars()
    incremental = IncrementalTechnicalAnalyzer(
        exchange="bitget", symbol="SOL/USDT", market_type="perp", timeframe="1h"
    )
    incremental.extend(bars)
    original = bars[50]
    revised = TechnicalBar(
        ts=original.ts,
        open=original.open,
        high=original.high + 1.0,
        low=original.low,
        close=original.close + 0.5,
        volume=original.volume + 20.0,
    )
    snapshot = incremental.update(revised)
    expected_rows = [revised if bar.ts == revised.ts else bar for bar in bars]
    expected = BatchTechnicalAnalyzer().compute(
        expected_rows,
        exchange="bitget",
        symbol="SOL/USDT",
        market_type="perp",
        timeframe="1h",
    )

    assert "bounded_rebuild_after_revision" in snapshot.quality_reasons
    _assert_value_maps_close(snapshot.values, expected.values)


def test_forming_bar_is_ignored_and_gap_is_explicit() -> None:
    bars = _bars(20)
    incremental = IncrementalTechnicalAnalyzer(
        exchange="bitget", symbol="BTC/USDT", market_type="spot", timeframe="1h"
    )
    incremental.extend(bars)
    forming = TechnicalBar(
        ts=bars[-1].ts + 3_600_000,
        open=bars[-1].close,
        high=bars[-1].close + 1,
        low=bars[-1].close - 1,
        close=bars[-1].close + 0.2,
        volume=100,
        is_closed=False,
    )
    ignored = incremental.update(forming)
    assert ignored.bars_used == 20
    assert "forming_bar_ignored" in ignored.quality_reasons

    gap = _bars(1)[0]
    gap = TechnicalBar(
        ts=bars[-1].ts + 3 * 3_600_000,
        open=gap.open,
        high=gap.high,
        low=gap.low,
        close=gap.close,
        volume=gap.volume,
    )
    gapped = incremental.update(gap)
    assert gapped.data_quality == "degraded"
    assert "timeframe_gap_detected" in gapped.quality_reasons


def test_invalid_ohlc_is_rejected() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        TechnicalBar(ts=1, open=100, high=99, low=98, close=100, volume=1)
