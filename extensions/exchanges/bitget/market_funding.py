"""Public funding inputs already carried by Bitget ticker and contract metadata."""


async def read_funding(exchange, symbol, ticker):
    market = (getattr(exchange, "markets", {}) or {}).get(symbol) or {}
    hours = (market.get("info") or {}).get("fundInterval")
    return (
        {"fundingRate": ticker.get("funding_rate"), "markPrice": ticker.get("mark_price"),
         "timestamp": ticker.get("source_timestamp_ms")},
        f"{hours}h", "bitget.contracts.fundInterval",
        getattr(exchange, "_candidate_markets_acquired_at", None),
    )


async def read_comparison_funding(exchange, markets, fetch):
    """Bitget public funding batches must be homogeneous in settlement currency."""
    result = {}
    for settle in sorted({market["settle"] for market in markets.values()}):
        group = [market["symbol"] for market in markets.values() if market["settle"] == settle]
        result.update(await fetch(exchange.fetch_funding_rates, group,
            params={"method": "publicMixGetV2MixMarketCurrentFundRate"}) or {})
    return result
