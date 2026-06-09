"""Polymarket prediction-market provider — public Gamma API (no auth).

Observation-only data source for ``prediction_observer``. ``fetch_active_markets``
returns ``PolymarketMarket`` objects sorted by 24h volume descending;
``format_prediction_context`` renders them as a compact markdown block for the LLM.

Transport: httpx (async) preferred, with a urllib fallback so the agent still works
in environments where httpx is unavailable. Any hard failure propagates — the caller
(prediction_observer) catches it and records an error rather than crashing the run.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger("aletheia.polymarket")

_GAMMA_URL = "https://gamma-api.polymarket.com/markets"
_TIMEOUT = 10.0


@dataclass
class PolymarketMarket:
    question: str
    yes_probability: float  # 0..1 (NOT a percentage)
    volume_usd: float
    prob_change_24h: float = 0.0
    resolution_date: str = ""


def _parse_market(raw: dict) -> PolymarketMarket | None:
    """Map a Gamma API market dict to PolymarketMarket; None if unusable."""
    try:
        question = str(raw.get("question") or raw.get("title") or "").strip()
        if not question:
            return None
        # Gamma encodes outcome prices as a JSON-string list e.g. "[\"0.62\",\"0.38\"]".
        yes_prob = 0.0
        prices = raw.get("outcomePrices")
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except (ValueError, TypeError):
                prices = None
        if isinstance(prices, (list, tuple)) and prices:
            yes_prob = float(prices[0])
        elif raw.get("lastTradePrice") is not None:
            yes_prob = float(raw["lastTradePrice"])

        volume = float(raw.get("volume24hr") or raw.get("volume") or 0.0)
        change = float(raw.get("oneDayPriceChange") or 0.0)
        end = str(raw.get("endDate") or raw.get("end_date_iso") or "")
        return PolymarketMarket(
            question=question,
            yes_probability=yes_prob,
            volume_usd=volume,
            prob_change_24h=change,
            resolution_date=end,
        )
    except (ValueError, TypeError) as e:
        logger.debug("skipping unparseable market: %s", e)
        return None


def _matches_categories(raw: dict, categories: list[str]) -> bool:
    """True if the market's tags/slug mention any requested category keyword."""
    if not categories:
        return True
    hay = " ".join(
        str(raw.get(k, "")) for k in ("category", "slug", "question", "title")
    ).lower()
    tags = raw.get("tags")
    if isinstance(tags, (list, tuple)):
        hay += " " + " ".join(str(t).lower() for t in tags)
    return any(c.lower() in hay for c in categories)


def _fetch_raw_urllib(params: str) -> list[dict]:
    req = urllib.request.Request(f"{_GAMMA_URL}?{params}", headers={"User-Agent": "aletheia"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 — fixed https host
        data = json.loads(resp.read().decode("utf-8"))
    return data if isinstance(data, list) else data.get("data", [])


async def _fetch_raw(params: str) -> list[dict]:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_GAMMA_URL, params=dict(p.split("=", 1) for p in params.split("&")))
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("data", [])
    except ImportError:
        return _fetch_raw_urllib(params)


async def fetch_active_markets(categories: list[str], limit: int = 15) -> list[PolymarketMarket]:
    """Fetch active markets matching any category keyword, sorted by 24h volume desc."""
    # Over-fetch then filter+trim locally so category filtering is keyword-based.
    params = f"active=true&closed=false&limit={max(limit * 4, 40)}&order=volume24hr&ascending=false"
    raw = await _fetch_raw(params)
    markets: list[PolymarketMarket] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not _matches_categories(item, categories):
            continue
        m = _parse_market(item)
        if m is not None:
            markets.append(m)
    markets.sort(key=lambda m: m.volume_usd, reverse=True)
    return markets[:limit]


def format_prediction_context(markets: list[PolymarketMarket]) -> str:
    """Render markets as a compact markdown table for the LLM prompt."""
    if not markets:
        return ""
    rows = [
        "| Market | YES % | 24h Δ | Volume USD | Resolves |",
        "|---|---:|---:|---:|---|",
    ]
    for m in markets:
        q = (m.question[:60] + "…") if len(m.question) > 61 else m.question
        rows.append(
            f"| {q} | {m.yes_probability * 100:.1f} | {m.prob_change_24h * 100:+.1f} "
            f"| {m.volume_usd:,.0f} | {m.resolution_date or 'n/a'} |"
        )
    return "\n".join(rows)
