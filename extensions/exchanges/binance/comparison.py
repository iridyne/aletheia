"""Binance public comparison OI uses history with proven USD notional."""


async def read_open_interest(exchange, markets, selected, fetch):
    result = {}
    for symbol in selected:
        wire_symbol = markets[symbol]["symbol"]
        rows = await fetch(exchange.fetch_open_interest_history, wire_symbol, "5m", limit=1)
        if rows:
            result[wire_symbol] = rows[-1]
    return result
