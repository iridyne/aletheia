"""FRED HTTP acquisition; only the configured FRED credential is resolved."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from aletheia.net import httpx_client

logger = logging.getLogger("aletheia.data.collectors.fred")
_FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
# FRED series IDs for our macro indicators
_FRED_SERIES = {
    "vix": "VIXCLS",          # VIX close (daily)
    "dxy": "DTWEXBGS",        # Trade-Weighted Dollar Index (Broad)
    "spy": "SP500",           # S&P 500
    "nasdaq": "NASDAQCOM",    # Nasdaq Composite
}
# Six research-only attempt buckets prevent repeated HTTP after persistence
# failure/cancellation. Persisted batches carry the same throttle across restart.
_FRED_RESEARCH_ATTEMPTS: dict[str, str] = {}



def _load_api_key() -> str | None:
    try:
        from aletheia.config.secrets import get_secret
        key = get_secret("fred_api_key")
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("FRED_API_KEY") or None


def is_configured() -> bool:
    return bool(_load_api_key())


async def collect_macro() -> tuple:
    key = _load_api_key()
    if not key:
        return None, None, None, None
    return await _fetch_fred(key)


async def collect_research() -> list[dict]:
    return await _fetch_fred_research(_load_api_key())


async def _fred_observations(client, api_key, series_id, *, limit):
    resp = await client.get(_FRED_BASE_URL, params={
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "sort_order": "desc", "limit": limit,
    })
    resp.raise_for_status()
    return resp.json()


async def _fetch_fred_research(api_key):
    from aletheia.data.macro_liquidity import SERIES, clock, read_source_batches, refresh_bucket, source_batch

    now = datetime.now(timezone.utc)
    # Existing serial scheduler owns persistence. Failed/unconfigured attempts
    # consume the same cadence bucket, including across process restarts.
    previous = {b["series_id"]: b for b in read_source_batches(now.isoformat(), require_schema=True)}
    due = [sid for sid in SERIES if _FRED_RESEARCH_ATTEMPTS.get(sid) != refresh_bucket(sid, now)
           and (sid not in previous or refresh_bucket(sid, clock(previous[sid]["acquired_at"])) != refresh_bucket(sid, now))]
    if not due:
        return []
    # Update before the first await: concurrent invocations cannot spend twice.
    _FRED_RESEARCH_ATTEMPTS.update({sid: refresh_bucket(sid, now) for sid in due})
    if not api_key:
        return [source_batch(sid, None, now.isoformat(), reason="not_configured") for sid in due]

    async def one(client, sid):
        payload, reason = None, None
        try:
            async with asyncio.timeout(8):
                payload = await _fred_observations(client, api_key, sid,
                    limit=32 if SERIES[sid][1] == "Daily" else 4)
        except Exception:
            reason = "request_failed"
        return source_batch(sid, payload, datetime.now(timezone.utc).isoformat(), reason=reason)

    async with httpx_client(timeout=7) as client:
        return await asyncio.gather(*(one(client, sid) for sid in due))


async def _fetch_fred(
    api_key: str,
) -> tuple:
    """Fetch latest observations for SPY, VIX, DXY, Nasdaq from FRED.

    FRED returns daily data; we grab the most recent non-"." observation
    (FRED uses "." for missing values on holidays/weekends).
    Free tier: 120 requests/min — we use 4 per call (one per series).
    All four requests run concurrently on a shared client connection pool.
    """
    results: dict[str, tuple | None] = {"spy": None, "vix": None, "dxy": None, "nasdaq": None}

    async def _one(client, key: str, series_id: str) -> tuple[str, tuple | None]:
        try:
            observations = (await _fred_observations(client, api_key, series_id, limit=5)).get("observations", [])
            for obs in observations:
                val = obs.get("value", ".")
                if val != ".":
                    return key, (round(float(val), 2), {
                        "source": "fred", "series_id": series_id,
                        "source_observed_at": obs.get("date"), "precision": "date",
                        "acquired_at": datetime.now(timezone.utc).isoformat(),
                    })
        except Exception:
            # Request exceptions can contain the API key in the query string.
            logger.debug("FRED series %s failed", series_id)
        return key, None

    async with httpx_client(timeout=10) as client:
        pairs = await asyncio.gather(*[
            _one(client, key, series_id)
            for key, series_id in _FRED_SERIES.items()
        ])
    for key, value in pairs:
        results[key] = value

    return results["spy"], results["vix"], results["dxy"], results["nasdaq"]
