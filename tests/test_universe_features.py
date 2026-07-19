"""Focused regressions for Universe feature collection."""

from datetime import datetime, timezone


class _Exchange:
    def __init__(self, markets):
        self.markets = markets


def test_quote_volume_prefers_exchange_quote_volume():
    from aletheia.universe.features import _quote_volume_24h

    exchange = _Exchange({"BTC/USDT": {"spot": True}})
    ticker = {"quoteVolume": 123_456.0, "baseVolume": 999.0, "last": 100.0}

    assert _quote_volume_24h(exchange, "BTC/USDT", ticker) == 123_456.0


def test_quote_volume_converts_okx_contract_volume_to_quote_currency():
    """OKX swaps expose contracts in baseVolume and leave quoteVolume empty."""
    from aletheia.universe.features import _quote_volume_24h

    exchange = _Exchange({
        "BTC/USDT:USDT": {
            "contract": True,
            "contractSize": 0.01,
        },
    })
    ticker = {
        "quoteVolume": None,
        "baseVolume": 9_000_000.0,
        "last": 60_000.0,
    }

    assert _quote_volume_24h(exchange, "BTC/USDT:USDT", ticker) == 5_400_000_000.0


def test_quote_volume_converts_spot_base_volume_when_quote_is_missing():
    from aletheia.universe.features import _quote_volume_24h

    exchange = _Exchange({"ETH/USDT": {"spot": True}})
    ticker = {"quoteVolume": None, "baseVolume": 2_000.0, "last": 3_000.0}

    assert _quote_volume_24h(exchange, "ETH/USDT", ticker) == 6_000_000.0


def test_quote_volume_returns_zero_when_it_cannot_be_derived():
    from aletheia.universe.features import _quote_volume_24h

    exchange = _Exchange({"BAD/USDT:USDT": {"contract": True}})
    ticker = {"quoteVolume": None, "baseVolume": 100.0, "last": None}

    assert _quote_volume_24h(exchange, "BAD/USDT:USDT", ticker) == 0.0


def test_ticker_prefilters_keep_complete_sequential_candidate_facts():
    from aletheia.universe.features import _evaluate_ticker_prefilters

    assets = [
        {"id": "btc", "symbol": "BTC/USDT", "venue": "okx", "market_type": "spot"},
        {"id": "eth", "symbol": "ETH/USDT", "venue": "okx", "market_type": "perp"},
        {"id": "sol", "symbol": "SOL/USDT", "venue": "okx", "market_type": "spot"},
        {"id": "low", "symbol": "LOW/USDT", "venue": "okx", "market_type": "spot"},
    ]
    exchange = _Exchange({})
    tickers = {
        "BTC/USDT": {"quoteVolume": 20_000_000.0},
        "ETH/USDT:USDT": {"quoteVolume": 15_000_000.0},
        "LOW/USDT": {"quoteVolume": 100.0},
    }

    candidates, ticker_facts, volume_facts = _evaluate_ticker_prefilters(
        assets,
        tickers,
        exchange,
        lambda symbol: f"{symbol}:USDT",
        min_volume=1_000_000.0,
    )

    assert [asset["id"] for asset, _ticker in candidates] == ["btc", "eth"]
    assert [fact["decision"] for fact in ticker_facts] == [
        "passed", "passed", "rejected", "passed",
    ]
    assert [fact["decision"] for fact in volume_facts] == [
        "passed", "passed", "not_applicable", "rejected",
    ]
    assert volume_facts[2]["reason_code"] == "ticker_prerequisite_failed"


def test_spot_feature_enrichment_does_not_read_derivatives(monkeypatch):
    from aletheia.storage import repositories as repo
    from aletheia.universe.features import _load_fresh_enrichment_rows

    monkeypatch.setattr(repo, "latest_orderbook", lambda *args, **kwargs: None)

    def unexpected(*args, **kwargs):
        raise AssertionError("spot enrichment must not query derivatives")

    monkeypatch.setattr(repo, "latest_funding_rate", unexpected)
    monkeypatch.setattr(repo, "latest_open_interest", unexpected)

    ob, funding, oi = _load_fresh_enrichment_rows(
        {"symbol": "BTC/USDT", "venue": "okx", "market_type": "spot"},
        now=datetime.now(timezone.utc),
    )

    assert ob is None
    assert funding is None
    assert oi is None


def test_only_explicit_derivative_market_types_read_derivative_enrichment(monkeypatch):
    from aletheia.storage import repositories as repo
    from aletheia.universe.features import _load_fresh_enrichment_rows

    calls = []
    monkeypatch.setattr(repo, "latest_orderbook", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        repo, "latest_funding_rate", lambda *args, **kwargs: calls.append("funding")
    )
    monkeypatch.setattr(
        repo, "latest_open_interest", lambda *args, **kwargs: calls.append("oi")
    )

    for market_type in ("", "margin", "option", "unknown"):
        _load_fresh_enrichment_rows(
            {"symbol": "BTC/USDT", "venue": "okx", "market_type": market_type},
            now=datetime.now(timezone.utc),
        )
    assert calls == []
