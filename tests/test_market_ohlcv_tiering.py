import pytest
import sqlite3

from aletheia.storage.market_ohlcv_tiering import (
    ColdWriteReceipt,
    MemoryColdOHLCVBackend,
    OHLCVSeriesIdentity,
    OHLCVTierManager,
    OHLCVTieringPolicy,
)


def _rows(count: int) -> list[dict]:
    return [
        {
            "ts": index * 60_000,
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100.5 + index,
            "volume": 1000 + index,
            "provider": "ccxt",
        }
        for index in range(count)
    ]


def _identity() -> OHLCVSeriesIdentity:
    return OHLCVSeriesIdentity("bitget", "BTC/USDT", "perp", "1m")


def test_tiering_keeps_hot_tail_and_only_archives_complete_chunks() -> None:
    manager = OHLCVTierManager(OHLCVTieringPolicy(hot_bars=64, cold_chunk_bars=64))
    plan = manager.plan(_identity(), _rows(64 + 64 * 2 + 10))

    assert len(plan.hot_rows) == 74
    assert len(plan.cold_chunks) == 2
    assert plan.eligible_cold_rows == 128
    assert plan.deferred_cold_rows == 10
    assert plan.hot_rows[0][0] > plan.cold_chunks[-1].last_ts


def test_copy_is_verified_before_a_delete_cutoff_is_authorized() -> None:
    manager = OHLCVTierManager(OHLCVTieringPolicy(hot_bars=64, cold_chunk_bars=64))
    plan = manager.plan(_identity(), _rows(192))
    backend = MemoryColdOHLCVBackend()

    assert manager.deletion_cutoff((), plan) is None
    receipts = manager.copy_to_cold(plan, backend)
    assert manager.deletion_cutoff(receipts, plan) == plan.cold_chunks[-1].last_ts


def test_bad_backend_receipt_fails_closed() -> None:
    manager = OHLCVTierManager(OHLCVTieringPolicy(hot_bars=64, cold_chunk_bars=64))
    plan = manager.plan(_identity(), _rows(128))

    class BadBackend(MemoryColdOHLCVBackend):
        def put(self, chunk):
            super().put(chunk)
            return ColdWriteReceipt(chunk.chunk_id, "wrong", chunk.row_count, True)

    with pytest.raises(RuntimeError, match="receipt mismatch"):
        manager.copy_to_cold(plan, BadBackend())


def test_repository_exposes_read_only_tiering_plan(monkeypatch) -> None:
    from aletheia.storage import repositories as repo

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE market_ohlcv (exchange TEXT,symbol TEXT,market_type TEXT,timeframe TEXT,"
        "ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,provider TEXT)"
    )
    db.executemany(
        "INSERT INTO market_ohlcv VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("bitget", "BTC/USDT", "perp", "1m", *tuple(row.values()))
            for row in _rows(128)
        ],
    )
    monkeypatch.setattr(repo, "get_db", lambda: db)

    plan = repo.plan_market_ohlcv_tiering(
        exchange="bitget", symbol="BTC/USDT", market_type="perp", timeframe="1m",
        hot_bars=64, cold_chunk_bars=64,
    )
    assert len(plan.hot_rows) == 64
    assert plan.eligible_cold_rows == 64
