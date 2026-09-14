"""Binance public taker ratio normalization."""

from aletheia.data.derivatives_facts import finite


async def read_taker(exchange, instrument):
    rows = await exchange.fapipublic_get_takerlongshortratio({
        "symbol": instrument.symbol.replace("/", ""), "period": "1h", "limit": 1,
    })
    value = finite(rows[0].get("buySellRatio")) if rows else None
    return value if value is not None and value > 0 else None
