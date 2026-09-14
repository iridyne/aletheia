"""bitget public instrument metadata decoding; no clients or persistence."""

from aletheia.data.symbols import build_instrument_background, stock_token_to_ticker, to_canonical


def decode_instrument_background(*, symbol: str, market_type: str, market_info: dict) -> dict:
    canonical = to_canonical(symbol)
    base, _, quote = canonical.partition("/")
    raw = market_info.get("info") or {}
    underlying_type = str(raw.get("underlyingType") or "").upper()
    contract_type = str(raw.get("symbolType") or "").upper()
    subtypes = []
    raw_base = str(raw.get("baseCoin") or market_info.get("base") or base)
    raw_quote = str(market_info.get("quote") or quote)
    stock_ticker = stock_token_to_ticker(f"{raw_base}/{raw_quote}")
    default_crypto = market_type in {"spot", "perp"}
    if stock_ticker or underlying_type.endswith("EQUITY"):
        asset_class = "equity"
    elif underlying_type in {"COMMODITY", "INDEX"}:
        asset_class = underlying_type.lower()
    elif underlying_type:
        asset_class = "crypto" if underlying_type == "COIN" else "unknown"
    else:
        asset_class = "crypto" if default_crypto else "unknown"
    return build_instrument_background(
        symbol=symbol, venue="bitget", market_type=market_type,
        asset_class=asset_class, stock_ticker=stock_ticker,
        classification={
            "source": (
                "exchange_metadata"
                if underlying_type or stock_ticker
                else "supported_market_default" if default_crypto else "unresolved"
            ),
            "underlying_type": underlying_type or None,
            "contract_type": contract_type or None,
            "underlying_subtypes": subtypes,
        },
    )
