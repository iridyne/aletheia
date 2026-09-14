"""okx public instrument metadata decoding; no clients or persistence."""

from aletheia.data.symbols import build_instrument_background, stock_token_to_ticker, to_canonical


def excluded_from_crypto_registry(market_info: dict) -> bool:
    """Category 3 stock/ETF contracts remain observation-only."""
    return str((market_info.get("info") or {}).get("instCategory") or "") == "3"


def decode_instrument_background(*, symbol: str, market_type: str, market_info: dict) -> dict:
    canonical = to_canonical(symbol)
    base, _, quote = canonical.partition("/")
    raw = market_info.get("info") or {}
    contract_type = str(raw.get("instType") or "").upper()
    raw_base = str(market_info.get("base") or base)
    raw_quote = str(market_info.get("quote") or quote)
    stock_ticker = stock_token_to_ticker(f"{raw_base}/{raw_quote}")
    category = str(raw.get("instCategory") or "")
    if stock_ticker or category == "3":
        asset_class = "equity"
    else:
        asset_class = "crypto" if category == "1" else "unknown"
    return build_instrument_background(
        symbol=symbol, venue="okx", market_type=market_type,
        asset_class=asset_class, stock_ticker=stock_ticker,
        classification={
            "source": (
                "exchange_metadata"
                if stock_ticker or raw.get("instCategory") is not None
                else "unresolved"
            ),
            "underlying_type": None,
            "contract_type": contract_type or None,
            "underlying_subtypes": [],
        },
    )
