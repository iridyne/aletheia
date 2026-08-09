from aletheia.trading.technical_analysis import TechnicalBar
from aletheia.trading.technical_snapshot_service import TechnicalSnapshotService


def _bar(index: int, close: float | None = None) -> TechnicalBar:
    value = close if close is not None else 100.0 + index
    return TechnicalBar(
        ts=1_700_000_000_000 + index * 3_600_000,
        open=value - 0.25,
        high=value + 1.0,
        low=value - 1.0,
        close=value,
        volume=1000 + index,
    )


def test_service_reuses_identical_window_and_appends_only_new_bars() -> None:
    service = TechnicalSnapshotService()
    first = [_bar(i) for i in range(60)]
    snap1 = service.snapshot_from_bars(
        first, exchange="bitget", symbol="BTC/USDT", market_type="perp", timeframe="1h"
    )
    snap2 = service.snapshot_from_bars(
        first, exchange="bitget", symbol="BTC/USDT", market_type="perp", timeframe="1h"
    )
    snap3 = service.snapshot_from_bars(
        [*first, _bar(60)],
        exchange="bitget", symbol="BTC/USDT", market_type="perp", timeframe="1h",
    )

    assert snap1.input_fingerprint == snap2.input_fingerprint
    assert snap3.bars_used == 61
    assert service.stats() == {
        "series": 1,
        "cache_hits": 1,
        "incremental_bar_updates": 1,
        "bounded_rebuilds": 1,
    }


def test_service_rebuilds_on_revision_and_isolates_instrument_identity() -> None:
    service = TechnicalSnapshotService()
    rows = [_bar(i) for i in range(60)]
    service.snapshot_from_bars(
        rows, exchange="bitget", symbol="BTC/USDT", market_type="spot", timeframe="1h"
    )
    revised = [*rows]
    revised[40] = _bar(40, close=999.0)
    revised_snapshot = service.snapshot_from_bars(
        revised, exchange="bitget", symbol="BTC/USDT", market_type="spot", timeframe="1h"
    )
    service.snapshot_from_bars(
        rows, exchange="bitget", symbol="BTC/USDT", market_type="perp", timeframe="1h"
    )

    assert revised_snapshot.values["last_close"] == rows[-1].close
    assert service.stats()["series"] == 2
    assert service.stats()["bounded_rebuilds"] == 3


def test_fixed_size_provider_window_uses_suffix_overlap_not_rebuild() -> None:
    service = TechnicalSnapshotService(max_history=64)
    first = [_bar(i) for i in range(64)]
    second = [_bar(i) for i in range(1, 65)]
    service.snapshot_from_bars(
        first, exchange="bitget", symbol="BTC/USDT", market_type="perp", timeframe="1h"
    )
    snapshot = service.snapshot_from_bars(
        second, exchange="bitget", symbol="BTC/USDT", market_type="perp", timeframe="1h"
    )

    assert snapshot.bar_close_ts == second[-1].ts
    assert service.stats()["incremental_bar_updates"] == 1
    assert service.stats()["bounded_rebuilds"] == 1
