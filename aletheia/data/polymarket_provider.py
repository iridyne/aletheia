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
    market_id: str = ""
    slug: str = ""
    category: str = ""
    liquidity: float = 0.0
    enable_orderbook: bool = False


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
        market_id = str(raw.get("id") or raw.get("conditionId") or "")
        slug = str(raw.get("slug") or "")
        category = str(raw.get("category") or "")
        liquidity = float(raw.get("liquidity") or 0.0)
        enable_ob = bool(raw.get("enableOrderBook", False))
        return PolymarketMarket(
            question=question,
            yes_probability=yes_prob,
            volume_usd=volume,
            prob_change_24h=change,
            resolution_date=end,
            market_id=market_id,
            slug=slug,
            category=category,
            liquidity=liquidity,
            enable_orderbook=enable_ob,
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
        from aletheia.net import httpx_client

        async with httpx_client(timeout=_TIMEOUT) as client:
            resp = await client.get(_GAMMA_URL, params=dict(p.split("=", 1) for p in params.split("&")))
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("data", [])
    except ImportError:
        return _fetch_raw_urllib(params)


async def fetch_active_markets(categories: list[str], limit: int = 50) -> list[PolymarketMarket]:
    """Fetch active markets matching any category keyword, sorted by 24h volume desc.

    Falls back to top-volume markets when no category matches are found,
    so the observer always has data to display.
    """
    # Gamma API caps at 100 per request; over-fetch to compensate for category filtering.
    fetch_count = min(limit * 3, 100)
    params = f"active=true&closed=false&limit={fetch_count}&order=volume24hr&ascending=false"
    raw = await _fetch_raw(params)
    matched: list[PolymarketMarket] = []
    all_parsed: list[PolymarketMarket] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        m = _parse_market(item)
        if m is None:
            continue
        all_parsed.append(m)
        if _matches_categories(item, categories):
            matched.append(m)
    if matched:
        matched.sort(key=lambda m: m.volume_usd, reverse=True)
        return matched[:limit]
    # Fallback: no category matches — return top-volume markets regardless
    logger.info("no markets matched categories %s, falling back to top-volume", categories)
    all_parsed.sort(key=lambda m: m.volume_usd, reverse=True)
    return all_parsed[:limit]


async def fetch_market_history(token_id: str, interval: str = "1h", fidelity: int = 60) -> list[dict]:
    """Fetch price history from Polymarket CLOB API for a market token.

    Returns list of {ts: str, yes_probability: float} sorted by time ascending.
    Falls back to empty list on any error (non-critical).
    """
    clob_url = "https://clob.polymarket.com/prices-history"
    params = f"market={token_id}&interval=all&fidelity={fidelity}"
    try:
        from aletheia.net import httpx_client
        async with httpx_client(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{clob_url}?{params}")
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                history = data.get("history", [])
            elif isinstance(data, list):
                history = data
            else:
                return []
            return [
                {"ts": str(pt.get("t", "")), "yes_probability": float(pt.get("p", 0))}
                for pt in history if isinstance(pt, dict)
            ]
    except Exception as e:
        logger.debug("fetch_market_history failed for %s: %s", token_id, e)
        return []


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
