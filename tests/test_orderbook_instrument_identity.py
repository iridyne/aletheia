"""Orderbook storage and collector identity regressions."""

import sqlite3


def _setup_db(tmp_path, monkeypatch):
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
    migrations.run_migrations()
    return conn


def _row(exchange: str, market_type: str, observed_at: str, spread: float) -> dict:
    return {
        "symbol": "BTC/USDT",
        "exchange": exchange,
        "market_type": market_type,
        "bid_price": 100.0,
        "ask_price": 100.1,
        "spread_bps": spread,
        "bid_depth_10bps_usd": 1000.0,
        "ask_depth_10bps_usd": 900.0,
        "total_depth_10bps_usd": 1900.0,
        "observed_at": observed_at,
    }


def test_latest_orderbook_scopes_venue_and_market(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage.repositories import insert_orderbook_snapshot, latest_orderbook

    insert_orderbook_snapshot(_row("binance", "spot", "2026-01-01T00:00:00+00:00", 2.0))
    insert_orderbook_snapshot(_row("okx", "perp", "2026-01-01T00:01:00+00:00", 5.0))

    spot = latest_orderbook("BTC/USDT", exchange="binance", market_type="spot")
    perp = latest_orderbook("BTC/USDT", exchange="okx", market_type="perp")
    assert spot and spot["spread_bps"] == 2.0
    assert perp and perp["spread_bps"] == 5.0
    assert latest_orderbook("BTC/USDT", exchange="okx", market_type="spot") is None


def test_orderbook_schema_marks_legacy_market_unknown(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(orderbook_snapshots)")}
    assert "market_type" in columns
    assert columns["market_type"]["dflt_value"] in ("'unknown'", '"unknown"')
