from aletheia.data.cache import OHLCVCache


def test_ohlcv_cache_is_bounded_and_reports_lru_activity() -> None:
    cache = OHLCVCache(max_entries=2)
    cache.set("BTC/USDT", "1h", 1, ["btc"], exchange="one")
    cache.set("ETH/USDT", "1h", 1, ["eth"], exchange="one")
    assert cache.get("BTC/USDT", "1h", 1, exchange="one") == ["btc"]

    cache.set("SOL/USDT", "1h", 1, ["sol"], exchange="one")

    assert cache.get("ETH/USDT", "1h", 1, exchange="one") is None
    assert cache.stats() == {
        "size": 2,
        "max_entries": 2,
        "hits": 1,
        "misses": 1,
        "expired": 0,
        "evicted": 1,
    }


def test_ohlcv_cache_reports_expiration(monkeypatch) -> None:
    import aletheia.data.cache as cache_module

    clock = [0.0]
    monkeypatch.setattr(cache_module, "_now", lambda: clock[0])
    monkeypatch.setattr(cache_module, "_ttl_seconds", lambda: 60.0)
    cache = OHLCVCache(max_entries=1)
    cache.set("BTC/USDT", "1h", 1, ["btc"])
    clock[0] = 61.0

    assert cache.get("BTC/USDT", "1h", 1) is None
    assert cache.stats()["expired"] == 1
    assert cache.stats()["misses"] == 1
