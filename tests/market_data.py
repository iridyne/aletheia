"""Bind fake wire clients to the real primary-only normalization boundary."""

from functools import partial
from extensions.market_data import build_native_provider, decode_derivatives_metadata
from extensions.exchanges.ccxt.market_data import _parse_funding, _parse_oi

parse_funding = partial(_parse_funding, metadata_reader=decode_derivatives_metadata)
parse_oi = partial(_parse_oi, metadata_reader=decode_derivatives_metadata)


def primary_provider(exchange):
    provider = build_native_provider(exchange.id, exchange.id)
    provider._primary = provider._primary_perp = exchange
    return provider


def history_provider(exchange, venue):
    if not hasattr(exchange, "markets"):
        exchange.markets = {"BTC/USDT:USDT": {}}
    provider = build_native_provider(venue, venue)
    provider._primary = provider._primary_perp = exchange
    return provider


def collect_live_oi(monkeypatch, snapshot):
    """Exercise Core enrichment with a normalized provider snapshot."""
    import asyncio
    from types import SimpleNamespace
    from aletheia.config import loader
    from aletheia.data import ccxt_provider
    from aletheia.data.collectors.derivatives import DerivativesCollector

    class Provider:
        async def prepare_live_derivatives(self):
            return snapshot.exchange, False

        async def get_live_derivatives(self, target, **kwargs):
            return {"funding": [], "open_interest": [snapshot], "errors": []}

        async def close(self):
            pass

    monkeypatch.setattr(loader, "load_config", lambda: SimpleNamespace(
        market_data=SimpleNamespace(enable_long_short_ratio=False)))
    monkeypatch.setattr(ccxt_provider, "get_market_data_provider", lambda **kwargs: Provider())
    return asyncio.run(DerivativesCollector().collect([snapshot.symbol]))["open_interest"][0]
