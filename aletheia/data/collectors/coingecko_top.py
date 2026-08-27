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
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from aletheia.net import httpx_client

logger = logging.getLogger("aletheia.data.collectors.coingecko_top")

_CG_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
_MAX_SOURCE_AGE = timedelta(minutes=30)
_MAX_SOURCE_FUTURE_SKEW = timedelta(minutes=5)


class CoinGeckoTopCollector:
    domain = "coingecko_top"

    async def collect(self, top_n: int = 100) -> dict[str, Any]:
        """Pull top_n coins ranked by market cap. Returns {rows, errors, movers_summary}."""
        rows: list[dict] = []
        errors: list[str] = []
        movers_summary: str | None = None
        rejected_count = 0
        batch_id: str | None = None
        source_times: list[str] = []

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
            acquired_at = datetime.now(timezone.utc)
            if isinstance(data, list):
                for d in data:
                    source_dt = _parse_source_observed_at(d.get("last_updated"), acquired_at)
                    if source_dt is None:
                        rejected_count += 1
                        continue
                    source_observed_at = source_dt.isoformat()
                    source_times.append(source_observed_at)
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
                        "source_observed_at": source_observed_at,
                        "acquired_at": acquired_at.isoformat(),
                        "source_age_sec": max(0, int((acquired_at - source_dt).total_seconds())),
                        "observed_at": source_observed_at,
                    })
                if rejected_count:
                    errors.append("source_time_rejected")
                if rows:
                    batch_id = _batch_id(rows)
                    for row in rows:
                        row["batch_id"] = batch_id
                    movers_summary = _build_movers_summary(rows)
        except Exception as e:
            errors.append(f"coingecko_top: {e}")
            logger.debug("CoinGecko top fetch failed (non-fatal): %s", e)

        return {
            "rows": rows,
            "movers_summary": movers_summary,
            "errors": errors,
            "batch_id": batch_id,
            "source_oldest_at": min(source_times) if source_times else None,
            "source_newest_at": max(source_times) if source_times else None,
            "rejected_count": rejected_count,
        }


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
    summary = f"Top Gainers: {g_str} | Top Losers: {l_str}"
    source_times = [
        str(r["source_observed_at"])
        for r in with_change
        if r.get("source_observed_at")
    ]
    batch_id = next((str(r["batch_id"]) for r in with_change if r.get("batch_id")), "")
    if source_times and batch_id:
        summary += (
            f" | CoinGecko batch {batch_id[:12]} "
            f"source {min(source_times)[:16]}..{max(source_times)[:16]}"
        )
    return summary


def _parse_source_observed_at(value: Any, acquired_at: datetime) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if parsed > acquired_at + _MAX_SOURCE_FUTURE_SKEW:
        return None
    if acquired_at - parsed > _MAX_SOURCE_AGE:
        return None
    return parsed


def _batch_id(rows: list[dict]) -> str:
    payload = [
        {
            "coin_id": row.get("coin_id"),
            "symbol": row.get("symbol"),
            "market_cap_rank": row.get("market_cap_rank"),
            "source_observed_at": row.get("source_observed_at"),
        }
        for row in rows
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
