"""Volume scanner — top symbols by 24h quote volume via batch fetch_tickers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aletheia.data.collectors.volume_scanner")


class VolumeScannerCollector:
    domain = "volume_scanner"

    def __init__(self, top_n: int = 30):
        self._top_n = top_n

    async def collect(self, symbols: list[str]) -> dict[str, Any]:
        """Fetch all tickers and rank by volume. symbols param is the candidate pool."""
        from aletheia.data.ccxt_provider import get_market_data_provider

        provider = get_market_data_provider()
        errors: list[str] = []
        ranked: list[dict] = []

        try:
            exchange = provider._ensure_primary()
            await exchange.load_markets()

            tickers = await exchange.fetch_tickers(symbols[:200])

            for sym, t in tickers.items():
                vol = float(t.get("quoteVolume") or 0)
                if vol <= 0:
                    continue
                ranked.append({
                    "symbol": sym,
                    "exchange": provider._primary_name,
                    "volume_24h_usd": vol,
                    "last_price": float(t.get("last") or 0),
                    "change_pct_24h": float(t.get("percentage") or 0),
                })

            ranked.sort(key=lambda x: x["volume_24h_usd"], reverse=True)
            ranked = ranked[: self._top_n]

        except Exception as e:
            errors.append(f"volume_scanner: {e}")
            logger.warning("volume scanner failed: %s", e)
        finally:
            await provider.close()

        return {
            "top_symbols": ranked,
            "scanned_count": len(ranked),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "errors": errors,
        }
