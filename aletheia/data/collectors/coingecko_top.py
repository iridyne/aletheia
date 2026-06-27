"""CoinGecko Top-N collector — universe-scale price/change/volume snapshots.

Hits the public /coins/markets endpoint (no API key, ~30 req/min on shared IP).
Pulls top_n coins by market cap once per Phase B tick (~10 min) and stores one
row per coin per tick. A short "Top Gainers / Top Losers" summary string is
also computed and folded into the next MacroSnapshot.top_movers_summary field
so the LLM sees universe momentum in the enriched_context macro block.

Failure path (per spec MS-C03): any httpx error returns {rows: [], errors:[…]}
without raising — never poisons MacroSnapshot's independent collection paths.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from aletheia.net import httpx_client

logger = logging.getLogger("aletheia.data.collectors.coingecko_top")

_CG_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"


class CoinGeckoTopCollector:
    domain = "coingecko_top"

    async def collect(self, top_n: int = 100) -> dict[str, Any]:
        """Pull top_n coins ranked by market cap. Returns {rows, errors, movers_summary}."""
        rows: list[dict] = []
        errors: list[str] = []
        movers_summary: str | None = None

        try:
            async with httpx_client(timeout=15) as client:
                resp = await client.get(
                    _CG_MARKETS_URL,
                    params={
                        "vs_currency": "usd",
                        "order": "market_cap_desc",
                        "per_page": min(top_n, 250),  # CoinGecko hard ceiling
                        "page": 1,
                        "sparkline": "false",
                        "price_change_percentage": "1h,24h,7d",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            now = datetime.now(timezone.utc).isoformat()
            if isinstance(data, list):
                for d in data:
                    rows.append({
                        "coin_id": d.get("id") or "",
                        "symbol": (d.get("symbol") or "").upper(),
                        "name": d.get("name") or "",
                        "price_usd": d.get("current_price"),
                        "market_cap_usd": d.get("market_cap"),
                        "market_cap_rank": d.get("market_cap_rank"),
                        "volume_24h_usd": d.get("total_volume"),
                        "price_change_1h_pct": d.get("price_change_percentage_1h_in_currency"),
                        "price_change_24h_pct": d.get("price_change_percentage_24h_in_currency"),
                        "price_change_7d_pct": d.get("price_change_percentage_7d_in_currency"),
                        "observed_at": now,
                    })
                movers_summary = _build_movers_summary(rows)
        except Exception as e:
            errors.append(f"coingecko_top: {e}")
            logger.debug("CoinGecko top fetch failed (non-fatal): %s", e)

        return {"rows": rows, "movers_summary": movers_summary, "errors": errors}


def _build_movers_summary(rows: list[dict], top_k: int = 5) -> str | None:
    """Pre-format a short 'Top Gainers / Top Losers' string for the LLM macro block.

    Uses 24h pct change. Returns None when not enough data.
    """
    with_change = [r for r in rows if r.get("price_change_24h_pct") is not None]
    if len(with_change) < 4:
        return None
    sorted_desc = sorted(with_change, key=lambda r: r["price_change_24h_pct"], reverse=True)
    gainers = sorted_desc[:top_k]
    losers = sorted_desc[-top_k:][::-1]
    g_str = ", ".join(f"{r['symbol']} {r['price_change_24h_pct']:+.1f}%" for r in gainers)
    l_str = ", ".join(f"{r['symbol']} {r['price_change_24h_pct']:+.1f}%" for r in losers)
    return f"Top Gainers: {g_str} | Top Losers: {l_str}"
