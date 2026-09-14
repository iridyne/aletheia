"""Credential-free CCXT public market metadata for the Exchange boundary."""

from aletheia.data.symbols import to_canonical


async def load_public_listing(venue: str, spot_only: bool) -> frozenset[str]:
    """Normalize one pinned venue's listing; spot-only and all-market scopes differ."""
    from aletheia.data.ccxt_provider import get_market_data_provider

    provider = get_market_data_provider(
        exchange=venue, native_ccxt=True, fallback_exchange=venue,
    )
    try:
        exchange = provider._ensure_primary()
        if spot_only:
            exchange.options["defaultType"] = "spot"
        markets = await exchange.load_markets()
        if not isinstance(markets, dict) or not markets:
            raise ValueError("listing_market_metadata_unavailable")
        listed = frozenset(
            to_canonical(str(symbol))
            for symbol, market in markets.items()
            if isinstance(market, dict)
            and ((market.get("spot") is True and market.get("active") is not False)
                 if spot_only else market.get("active") != False)  # noqa: E712
        )
        if not listed:
            raise ValueError("listing_scope_empty")
        return listed
    finally:
        await provider.close()


async def load_public_market_capabilities(
    exchange_name: str,
    *,
    sandbox: bool = False,
) -> dict[str, frozenset[str]]:
    """Load active USDT spot/swap symbols through the generic CCXT factory.

    This is public market metadata only: no credentials or private account
    endpoints are involved. The caller owns no client; this helper always closes
    the loop-local provider before returning or re-raising.
    """
    from aletheia.net import close_ccxt, make_ccxt

    exchange, session = make_ccxt(exchange_name, options={"defaultType": "swap"})
    try:
        if sandbox:
            exchange.set_sandbox_mode(True)
        await exchange.load_markets()
        symbols: dict[str, set[str]] = {"swap": set(), "spot": set()}
        for info in exchange.markets.values():
            if not info.get("active", True):
                continue
            market_type = info.get("type")
            if market_type not in symbols:
                continue
            if (info.get("quote") or "").upper() != "USDT":
                continue
            canonical = to_canonical(f"{info.get('base', '')}/USDT")
            symbols[market_type].add(canonical)
        return {key: frozenset(value) for key, value in symbols.items()}
    finally:
        if not await close_ccxt(exchange, session):
            raise RuntimeError("ccxt_cleanup_failed")
