"""CCXT batch ticker normalization; reuse the caller-owned market client."""

import logging
import math

from aletheia.data.providers import _nonnegative_float
from aletheia.data.derivatives_facts import finite

logger = logging.getLogger("aletheia.ccxt_tickers")
_TICKER_CHUNK_SIZE = 100


def _positive_float(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def quote_volume_24h(
    exchange, ccxt_symbol: str, ticker: dict,
) -> float | None:
    """Return 24h quote-currency volume for spot or contract tickers.

    OKX swap tickers leave ``quoteVolume`` empty and expose ``baseVolume`` as
    contract count. Convert contracts to base units with ``contractSize`` before
    multiplying by the last price. Keeping this conversion in one helper ensures
    the volume pre-filter and persisted feature use identical economics.
    """
    quote_raw = ticker.get("quoteVolume")
    if quote_raw is not None:
        return _nonnegative_float(quote_raw)

    base_raw = ticker.get("baseVolume")
    if base_raw is None:
        return None
    base_volume = _nonnegative_float(base_raw)
    if base_volume is None:
        return None

    last_raw = (
        ticker.get("last") if ticker.get("last") is not None
        else ticker.get("close")
    )
    if last_raw is None:
        return None
    last = _positive_float(last_raw)
    if last is None:
        return None

    markets = getattr(exchange, "markets", {}) or {}
    market = markets.get(ccxt_symbol) or {}
    if not isinstance(market, dict):
        return None
    if market.get("contract"):
        contract_size_raw = market.get("contractSize")
        if contract_size_raw is None:
            return None
        contract_size = _positive_float(contract_size_raw)
        if contract_size is None:
            return None
        base_volume *= contract_size
    elif not market.get("spot"):
        return None

    result = base_volume * last
    return result if math.isfinite(result) else None


def _normalize_ticker(exchange, symbol: str, ticker: dict) -> dict:
    info = ticker.get("info")
    if not isinstance(info, dict):
        info = {}
    return {
        "quote_volume_24h": quote_volume_24h(exchange, symbol, ticker),
        "bid_price": _positive_float(ticker.get("bid")),
        "ask_price": _positive_float(ticker.get("ask")),
        "funding_rate": finite(info.get("fundingRate")),
        "mark_price": _positive_float(info.get("markPrice")),
        "source_timestamp_ms": _nonnegative_float(ticker.get("timestamp")),
    }


async def _chunked_fetch_tickers(exchange, symbols: list[str]) -> dict:
    """Fetch tickers in chunks to stay within Binance URL length limits."""
    all_tickers: dict = {}
    valid = [s for s in symbols if s.isascii() and s in exchange.markets]
    for i in range(0, len(valid), _TICKER_CHUNK_SIZE):
        chunk = valid[i:i + _TICKER_CHUNK_SIZE]
        try:
            batch = await exchange.fetch_tickers(chunk)
            all_tickers.update({
                symbol: _normalize_ticker(exchange, symbol, ticker)
                for symbol, ticker in batch.items()
                if isinstance(ticker, dict) and ticker
            })
        except Exception as e:
            logger.warning("Ticker chunk %d-%d failed: %s", i, i + len(chunk), e)
    return all_tickers
