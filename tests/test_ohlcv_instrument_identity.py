"""Regression tests for venue + market scoped OHLCV storage."""

import sqlite3
from datetime import datetime, timezone

from aletheia.data.providers import Candle


def _setup_db(tmp_path, monkeypatch, *, legacy: bool = False):
    import aletheia.config.loader as loader
    import aletheia.storage.migrations as migrations
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq.reset_db()
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    migrations._blueprint = None
    if legacy:
        conn.execute(
            "CREATE TABLE market_ohlcv ("
            "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, ts INTEGER NOT NULL, "
            "open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, "
            "volume REAL NOT NULL, provider TEXT NOT NULL DEFAULT 'ccxt', exchange TEXT, "
            "PRIMARY KEY(symbol,timeframe,ts))"
        )
        conn.execute(
            "INSERT INTO market_ohlcv VALUES "
            "('BTC/USDT','1h',1000,1,2,0.5,1.5,10,'ccxt','ccxt')"
        )
        conn.commit()
    migrations.run_migrations()
    return conn


def _candle(ts: int = 1000) -> Candle:
    return Candle(timestamp=ts, open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0)


def test_ohlcv_rows_coexist_by_full_instrument_identity(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    from aletheia.storage.repositories import get_ohlcv_candles, upsert_ohlcv_candles

    upsert_ohlcv_candles(
        "BTC/USDT", "1h", [_candle()], exchange="binance", market_type="spot"
    )
    upsert_ohlcv_candles(
        "BTC/USDT", "1h", [_candle()], exchange="okx", market_type="perp"
    )

    assert conn.execute("SELECT COUNT(*) FROM market_ohlcv").fetchone()[0] == 2
    spot = get_ohlcv_candles(
        "BTC/USDT", "1h", exchange="binance", market_type="spot"
    )
    perp = get_ohlcv_candles(
        "BTC/USDT", "1h", exchange="okx", market_type="perp"
    )
    assert len(spot) == 1 and spot[0]["market_type"] == "spot"
    assert len(perp) == 1 and perp[0]["market_type"] == "perp"

    # Compatibility readers that have not yet supplied identity still receive
    # one coherent instrument, never a concatenation of spot and perp rows.
    unscoped = get_ohlcv_candles("BTC/USDT", "1h", limit=10)
    assert len(unscoped) == 1
    assert {(row["exchange"], row["market_type"]) for row in unscoped} in (
        {("binance", "spot")},
        {("okx", "perp")},
    )


def test_legacy_ohlcv_migration_marks_ambiguous_venue_honestly(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch, legacy=True)

    info = list(conn.execute("PRAGMA table_info(market_ohlcv)"))
    columns = {row["name"] for row in info}
    pk = [row["name"] for row in sorted(info, key=lambda row: row["pk"]) if row["pk"]]
    row = conn.execute(
        "SELECT exchange,market_type FROM market_ohlcv WHERE symbol='BTC/USDT'"
    ).fetchone()
    assert "market_type" in columns
    assert pk == ["exchange", "symbol", "market_type", "timeframe", "ts"]
    assert dict(row) == {"exchange": "legacy_unknown", "market_type": "spot"}


def test_identical_ohlcv_refetch_preserves_mutation_provenance(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo

    class _Clock(datetime):
        current = datetime(2026, 1, 1, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz else cls.current.replace(tzinfo=None)

    monkeypatch.setattr(repo, "datetime", _Clock)

    repo.upsert_ohlcv_candles(
        "BTC/USDT", "1h", [_candle()], exchange="binance", market_type="spot"
    )
    original = repo.get_db().execute(
        "SELECT first_persisted_at, last_mutated_at, close, provider "
        "FROM market_ohlcv WHERE exchange='binance' AND symbol='BTC/USDT' "
        "AND market_type='spot' AND timeframe='1h' AND ts=1000"
    ).fetchone()

    _Clock.current = datetime(2026, 1, 2, tzinfo=timezone.utc)
    repo.upsert_ohlcv_candles(
        "BTC/USDT", "1h", [_candle()], exchange="binance", market_type="spot"
    )
    identical = repo.get_db().execute(
        "SELECT first_persisted_at, last_mutated_at, close, provider "
        "FROM market_ohlcv WHERE exchange='binance' AND symbol='BTC/USDT' "
        "AND market_type='spot' AND timeframe='1h' AND ts=1000"
    ).fetchone()

    assert identical["first_persisted_at"] == original["first_persisted_at"]
    assert identical["last_mutated_at"] == original["last_mutated_at"]

    _Clock.current = datetime(2026, 1, 3, tzinfo=timezone.utc)
    repo.upsert_ohlcv_candles(
        "BTC/USDT",
        "1h",
        [Candle(timestamp=1000, open=1.0, high=2.0, low=0.5, close=1.6, volume=10.0)],
        exchange="binance",
        market_type="spot",
    )
    changed_price = repo.get_db().execute(
        "SELECT first_persisted_at, last_mutated_at, close, provider "
        "FROM market_ohlcv WHERE exchange='binance' AND symbol='BTC/USDT' "
        "AND market_type='spot' AND timeframe='1h' AND ts=1000"
    ).fetchone()

    assert changed_price["first_persisted_at"] == original["first_persisted_at"]
    assert changed_price["last_mutated_at"] == _Clock.current.isoformat()
    assert changed_price["close"] == 1.6

    _Clock.current = datetime(2026, 1, 4, tzinfo=timezone.utc)
    repo.upsert_ohlcv_candles(
        "BTC/USDT",
        "1h",
        [Candle(timestamp=1000, open=1.0, high=2.0, low=0.5, close=1.6, volume=10.0)],
        provider="rest-refetch",
        exchange="binance",
        market_type="spot",
    )
    changed_provider = repo.get_db().execute(
        "SELECT first_persisted_at, last_mutated_at, close, provider "
        "FROM market_ohlcv WHERE exchange='binance' AND symbol='BTC/USDT' "
        "AND market_type='spot' AND timeframe='1h' AND ts=1000"
    ).fetchone()

    assert changed_provider["first_persisted_at"] == original["first_persisted_at"]
    assert changed_provider["last_mutated_at"] == _Clock.current.isoformat()
    assert changed_provider["provider"] == "rest-refetch"
