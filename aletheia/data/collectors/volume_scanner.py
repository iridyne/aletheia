"""Volume scanner — top symbols by 24h quote volume via batch fetch_tickers."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from aletheia.data.symbols import InstrumentId, MARKET_SPOT

logger = logging.getLogger("aletheia.data.collectors.volume_scanner")


class VolumeScannerCollector:
    domain = "volume_scanner"

    def __init__(self, top_n: int = 30):
        self._top_n = top_n

    async def collect(self, symbols: list[InstrumentId | str]) -> dict[str, Any]:
        """Fetch exact-instrument tickers and rank by quote-currency volume."""
        from aletheia.config.loader import load_config
        from aletheia.data.ccxt_provider import get_market_data_provider
        from aletheia.universe.features import _quote_volume_24h

        errors: list[str] = []
        ranked: list[dict] = []
        default_exchange = load_config().market_data.ccxt_exchange or "binance"
        groups: dict[tuple[str, str], list[InstrumentId]] = defaultdict(list)
        for symbol in symbols[:200]:
            try:
                instrument = (
                    symbol
                    if isinstance(symbol, InstrumentId)
                    else InstrumentId.from_parts(default_exchange, symbol, MARKET_SPOT)
                )
                groups[(instrument.exchange, instrument.market_type)].append(instrument)
            except (TypeError, ValueError) as exc:
                errors.append(f"{symbol}: invalid_instrument:{exc}")

        for (venue, market_type), instruments in groups.items():
            provider = get_market_data_provider(exchange=venue, native_ccxt=True)
            try:
                exchange = provider._ensure_primary()
                if hasattr(exchange, "options"):
                    exchange.options["defaultType"] = (
                        "swap" if market_type == "perp" else market_type
                    )
                await exchange.load_markets()
                exact: list[str] = []
                by_ccxt_symbol: dict[str, InstrumentId] = {}
                for instrument in instruments:
                    ccxt_symbol = instrument.to_ccxt_symbol()
                    market = (exchange.markets or {}).get(ccxt_symbol)
                    if market is None:
                        errors.append(f"{instrument}: market_not_supported")
                        continue
                    if str((market.get("info") or {}).get("instCategory") or "") == "3":
                        continue
                    exact.append(ccxt_symbol)
                    by_ccxt_symbol[ccxt_symbol] = instrument
                if not exact:
                    continue

                tickers = await exchange.fetch_tickers(exact)
                for ccxt_symbol, ticker in tickers.items():
                    instrument = by_ccxt_symbol.get(ccxt_symbol)
                    if instrument is None:
                        continue
                    volume = _quote_volume_24h(exchange, ccxt_symbol, ticker)
                    if volume <= 0:
                        continue
                    ranked.append({
                        "symbol": instrument.symbol,
                        "market_type": instrument.market_type,
                        "exchange": venue,
                        "volume_24h_usd": volume,
                        "last_price": float(ticker.get("last") or 0),
                        "change_pct_24h": float(ticker.get("percentage") or 0),
                    })
            except Exception as exc:
                errors.append(f"volume_scanner:{venue}: {exc}")
                logger.warning("volume scanner failed for %s: %s", venue, exc)
            finally:
                await provider.close()

        ranked.sort(key=lambda x: x["volume_24h_usd"], reverse=True)
        ranked = ranked[: self._top_n]

        return {
            "top_symbols": ranked,
            "scanned_count": len(ranked),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "errors": errors,
        }
