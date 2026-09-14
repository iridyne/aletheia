"""Public Binance positioning enrichment; bounded, independent of primary OI."""

import asyncio
import logging
from datetime import datetime, timezone

from aletheia.net import httpx_client
from extensions.exchanges.ccxt.derivatives_metadata import provider_source_time
from aletheia.data.derivatives_facts import finite

logger = logging.getLogger("aletheia.data.collectors.derivatives")

# Binance Futures globalLongShortAccountRatio — public, no API key.
# Scope: USDT-margined linear perpetuals only (BTC/USDT → BTCUSDT). Inverse perps
# would need a different symbol form; deliberately out of scope this batch.
# Fed into OpenInterestSnapshot.long_short_ratio at write time; normalizer's
# compute_leverage_state already consumes long_short_ratio into crowding_score.
_BINANCE_LSR_URL = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
_BINANCE_TOP_POSITION_RATIO_URL = (
    "https://fapi.binance.com/futures/data/topLongShortPositionRatio"
)
# LSR enriches OI but is not allowed to consume the primary venue's live
# Funding/OI budget.  A partial result is honest and remains useful.
_LSR_BUDGET_SEC = 4.0


async def fetch_positioning(symbols: list[str], family: str) -> dict[str, dict]:
    url = {"global_account_ratio": _BINANCE_LSR_URL,
           "top_position_ratio": _BINANCE_TOP_POSITION_RATIO_URL}[family]
    return await _fetch_binance_ratio_batch(symbols, url)


async def _fetch_binance_ratio_batch(symbols: list[str], url: str) -> dict[str, dict]:
    results: dict[str, dict] = {}
    semaphore = asyncio.Semaphore(4)
    # Four requests per stream, eight combined; CCXT remains sequential.
    async with httpx_client(timeout=_LSR_BUDGET_SEC) as client:
        async def fetch(sym: str) -> None:
            async with semaphore:
                try:
                    resp = await client.get(
                        url,
                        params={"symbol": sym.replace("/", ""), "period": "1h", "limit": 1},
                    )
                    if resp.status_code != 200:
                        return
                    items = resp.json()
                    if isinstance(items, list) and items:
                        ratio = finite(items[0].get("longShortRatio"))
                        if ratio is not None and ratio > 0:
                            acquired = datetime.now(timezone.utc)
                            source, status = provider_source_time(items[0], acquired)
                            results[sym] = dict(value=ratio, provider="binance_public", venue="binance", source_observed_at=source,
                                source_time_status=status, acquired_at=acquired.isoformat(),
                                instrument=dict(canonical_symbol=sym, source_instrument_id=sym.replace('/', ''),
                                    market_type='perp', base_asset=sym.split('/')[0],
                                    quote_asset=sym.split('/')[-1], settle_asset='USDT', linear=sym.endswith('/USDT')))
                except Exception as exc:
                    logger.debug("Ratio fetch failed for %s: %s", sym, exc)

        tasks = [asyncio.create_task(fetch(sym)) for sym in dict.fromkeys(symbols)]
        try:
            if tasks:
                await asyncio.wait(tasks, timeout=_LSR_BUDGET_SEC)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    return results
