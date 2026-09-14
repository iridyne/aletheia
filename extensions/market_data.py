"""Existing public spot fallback composition; fresh adapters per factory call."""

from aletheia.data.providers import MarketDataProvider


def register_market_data() -> None:
    from aletheia.trading.market_context import configure_macro_context_fetch
    try:
        from extensions.observation.equity_observer import configure_equity_data
    except ModuleNotFoundError:
        configure_equity_data = None

    try:
        from extensions.serenity.research_worker import configure_equity_fundamentals_fetch
    except ModuleNotFoundError:
        configure_equity_fundamentals_fetch = None

    try:
        from extensions.yahoo.market_data import (
            fetch_macro_context, fetch_equity_fundamentals,
            fetch_equity_indicators, fetch_live_price,
        )
    except ModuleNotFoundError:
        configure_macro_context_fetch(None)
        if configure_equity_fundamentals_fetch is not None:
            configure_equity_fundamentals_fetch(None)
        if configure_equity_data is not None:
            configure_equity_data(None, None)
    else:
        configure_macro_context_fetch(fetch_macro_context)
        if configure_equity_fundamentals_fetch is not None:
            configure_equity_fundamentals_fetch(fetch_equity_fundamentals)
        if configure_equity_data is not None:
            configure_equity_data(fetch_equity_indicators, fetch_live_price)

    from aletheia.data.ccxt_provider import (
        configure_native_provider_factory, configure_spot_fallback_factory,
    )

    configure_spot_fallback_factory(build_spot_fallbacks)
    from aletheia.data.collectors.derivatives import configure_positioning_reader

    try:
        from extensions.exchanges.binance.positioning import fetch_positioning
    except ModuleNotFoundError:
        configure_positioning_reader(None)
    else:
        configure_positioning_reader(fetch_positioning)
    from aletheia.data.market_catalog import configure_market_catalog

    try:
        from extensions.exchanges.ccxt.registry_catalog import load_registry_catalog  # noqa: F401 - availability probe
    except ModuleNotFoundError:
        configure_market_catalog(None)
    else:
        configure_market_catalog(fetch_registry_catalog)
    from aletheia.data.collectors.microstructure import configure_orderbook_collector

    try:
        from extensions.exchanges.ccxt.orderbook import MicrostructureCollector
    except ModuleNotFoundError:
        configure_orderbook_collector(None)
    else:
        configure_orderbook_collector(lambda symbols, **kwargs: MicrostructureCollector().collect(symbols, **kwargs))
    from aletheia.data.venue_listing_cache import configure_listing_fetch

    try:
        from extensions.exchanges.ccxt.market_capabilities import load_public_listing
    except ModuleNotFoundError:
        configure_listing_fetch(None)
    else:
        configure_listing_fetch(load_public_listing)
    try:
        from extensions.exchanges.ccxt.market_data import CCXTProvider  # noqa: F401 - availability probe
    except ModuleNotFoundError:
        configure_native_provider_factory(None)
    else:
        configure_native_provider_factory(build_native_provider)


def decode_market_background(*, symbol: str, venue: str, market_type: str, market_info: dict) -> dict:
    venue = venue.strip().lower()
    if venue == "bitget":
        from extensions.exchanges.bitget.instrument_background import decode_instrument_background
    elif venue == "binance":
        from extensions.exchanges.binance.instrument_background import decode_instrument_background
    elif venue == "okx":
        from extensions.exchanges.okx.instrument_background import decode_instrument_background
    else:
        from aletheia.data.symbols import build_instrument_background
        return build_instrument_background(
            symbol=symbol, venue=venue, market_type=market_type,
            asset_class="unknown",
            classification={"source": "unresolved", "underlying_type": None,
                            "contract_type": None, "underlying_subtypes": []},
        )
    return decode_instrument_background(
        symbol=symbol, market_type=market_type, market_info=market_info,
    )


def decode_catalog_market(*, symbol: str, venue: str, market_type: str, market_info: dict):
    background = decode_market_background(
        symbol=symbol, venue=venue, market_type=market_type, market_info=market_info,
    )
    excluded = False
    if venue.strip().lower() == "okx":
        from extensions.exchanges.okx.instrument_background import excluded_from_crypto_registry
        excluded = excluded_from_crypto_registry(market_info)
    return background, excluded


async def fetch_registry_catalog(venue: str, fallback_venue: str):
    from extensions.exchanges.ccxt.registry_catalog import load_registry_catalog
    return await load_registry_catalog(
        venue, fallback_venue, decode_market=decode_catalog_market,
    )


def decode_derivatives_metadata(symbol, venue, market, raw, **kwargs):
    from extensions.exchanges.ccxt.derivatives_metadata import ccxt_metadata

    if venue == "okx":
        from extensions.exchanges.okx.derivatives_metadata import OI_VALUE_FIELDS, FUNDING_TIMESTAMP_FIELD
        kwargs.update(oi_value_fields=OI_VALUE_FIELDS, funding_timestamp_field=FUNDING_TIMESTAMP_FIELD)
    elif venue == "binance":
        from extensions.exchanges.binance.derivatives_metadata import OI_VALUE_FIELDS
        kwargs["oi_value_fields"] = OI_VALUE_FIELDS
    return ccxt_metadata(symbol, venue, market, raw, **kwargs)


def build_native_provider(exchange: str, fallback_exchange: str):
    from extensions.exchanges.ccxt.market_data import CCXTProvider

    options = {"metadata_reader": decode_derivatives_metadata}
    if "binance" in (exchange, fallback_exchange):
        from extensions.exchanges.binance.public_market_data import PUBLIC_PERP_EXCHANGE
        if exchange == "binance":
            options["primary_perp_exchange"] = PUBLIC_PERP_EXCHANGE
        if fallback_exchange == "binance":
            options["fallback_perp_exchange"] = PUBLIC_PERP_EXCHANGE
    if exchange == "bitget":
        from extensions.exchanges.bitget.market_funding import read_funding, read_comparison_funding
        options.update(funding_reader=read_funding,
                       comparison_funding_reader=read_comparison_funding)
    elif exchange == "binance":
        from extensions.exchanges.binance.market_taker import read_taker
        from extensions.exchanges.binance.comparison import read_open_interest
        options.update(taker_reader=read_taker, comparison_oi_reader=read_open_interest,
                       comparison_prices_from_funding=True)
    elif exchange == "okx":
        from extensions.exchanges.okx.market_taker import read_taker
        options["taker_reader"] = read_taker
    return CCXTProvider(exchange, fallback_exchange, **options)


def build_spot_fallbacks() -> list[MarketDataProvider]:
    providers = []
    try:
        from extensions.coingecko.market_data import CoinGeckoProvider
    except ModuleNotFoundError:
        pass
    else:
        providers.append(CoinGeckoProvider())
    try:
        from extensions.exchanges.binance.public_market_data import BinancePublicProvider
    except ModuleNotFoundError:
        pass
    else:
        providers.append(BinancePublicProvider())
    return providers
