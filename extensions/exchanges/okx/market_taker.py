"""OKX public taker-volume normalization."""

from aletheia.data.derivatives_facts import finite


async def read_taker(exchange, instrument):
    response = await exchange.public_get_rubik_stat_taker_volume({
        "ccy": instrument.symbol.split("/")[0], "instType": "CONTRACTS", "period": "1H",
    })
    rows = (response or {}).get("data") or []
    if not rows:
        return None
    sell, buy = finite(rows[0][1]), finite(rows[0][2])
    if sell is None or sell <= 0 or buy is None or buy < 0:
        return None
    return finite(buy / sell)
