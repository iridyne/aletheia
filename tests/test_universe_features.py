"""Focused regressions for Universe feature collection."""


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


def test_enrichment_falls_back_to_cross_venue_oi(tmp_path, monkeypatch):
    """When the primary venue (okx) has no fresh OI/funding for a symbol, the
    feature read falls back to a cross-venue feed (hyperliquid) instead of None."""
    from datetime import datetime, timezone
    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import repositories as repo
    from aletheia.storage import migrations as mig
    sq.close_db()
    mig.run_migrations()

    now = datetime.now(timezone.utc)
    # Only a hyperliquid OI row exists — no okx row for this symbol.
    repo.insert_open_interest_snapshot({
        "symbol": "BTC/USDT", "exchange": "hyperliquid",
        "open_interest_usd": 2.3e9, "observed_at": now.isoformat(),
    })

    from aletheia.universe.features import _load_fresh_enrichment_rows
    _ob, _fr, oi_row = _load_fresh_enrichment_rows(
        {"symbol": "BTC/USDT", "venue": "okx", "market_type": "perp"}, now=now,
    )
    assert oi_row is not None
    assert oi_row["open_interest_usd"] == 2.3e9  # cross-venue fallback used
    sq.close_db()
