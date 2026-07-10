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
