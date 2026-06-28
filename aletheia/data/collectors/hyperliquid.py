"""Hyperliquid collector — per-asset OI, funding rate, predicted funding.

Data source: Hyperliquid public info API (zero key, no authentication).
  POST https://api.hyperliquid.xyz/info
  - {"type": "metaAndAssetCtxs"}   → universe + per-asset market context
  - {"type": "predictedFundings"}  → cross-venue predicted funding rates

OI is returned in base-asset units (BTC, ETH, …) and converted to USD via
markPx. Funding rate is the per-interval rate; we annualize it as:
  rate × intervals_per_day × 365
Hyperliquid uses 1-hour intervals → annualized = rate × 24 × 365.

Written to: funding_rate_snapshots + open_interest_snapshots (existing tables).
exchange tag: "hyperliquid"
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from aletheia.net import httpx_client
from aletheia.data.models import FundingRateSnapshot, OpenInterestSnapshot

logger = logging.getLogger("aletheia.data.collectors.hyperliquid")

_HL_INFO_URL = "https://api.hyperliquid.xyz/info"

# Hyperliquid uses 1-hour funding intervals → ×24×365 for annualized rate.
_INTERVALS_PER_DAY = 24
_DAYS_PER_YEAR = 365


class HyperliquidCollector:
    domain = "hyperliquid"

    async def collect(self, symbols: list[str] | None = None) -> dict[str, Any]:
        """Collect OI and funding rate for all Hyperliquid perpetuals.

        symbols: if provided, only include these canonical symbols (BTC/USDT etc.).
                 None = return all available assets.

        Returns {"funding": [...], "open_interest": [...], "errors": [...]}.
        """
        funding: list[FundingRateSnapshot] = []
        oi: list[OpenInterestSnapshot] = []
        errors: list[str] = []

        # Step 1: fetch market context (OI + current funding)
        meta_ctxs: list | None = None
        try:
            async with httpx_client(timeout=15) as client:
                resp = await client.post(
                    _HL_INFO_URL,
                    json={"type": "metaAndAssetCtxs"},
                )
                resp.raise_for_status()
                meta_ctxs = resp.json()
        except Exception as e:
            errors.append(f"metaAndAssetCtxs: {e}")
            logger.warning("Hyperliquid metaAndAssetCtxs failed: %s", e)

        now = datetime.now(timezone.utc).isoformat()

        if meta_ctxs and isinstance(meta_ctxs, list) and len(meta_ctxs) >= 2:
            universe = meta_ctxs[0].get("universe", [])
            ctxs = meta_ctxs[1]

            # symbols filter set — canonical BTC/USDT, ETH/USDT, …
            symbol_filter: set[str] | None = None
            if symbols:
                symbol_filter = {s.upper() for s in symbols}

            for asset_meta, ctx in zip(universe, ctxs):
                asset_name = asset_meta.get("name", "")
                if not asset_name:
                    continue

                canonical = f"{asset_name}/USDT"
                if symbol_filter and canonical.upper() not in symbol_filter:
                    continue

                fr_snap = _parse_funding(canonical, ctx, now)
                if fr_snap:
                    funding.append(fr_snap)

                oi_snap = _parse_oi(canonical, ctx, now)
                if oi_snap:
                    oi.append(oi_snap)

        # Step 2: predicted fundings (best-effort; does not block Step 1 writes)
        try:
            predicted = await _fetch_predicted_fundings(symbols, now)
            # Extend funding list with predicted entries (exchange tag differs)
            funding.extend(predicted)
        except Exception as e:
            errors.append(f"predictedFundings: {e}")
            logger.debug("Hyperliquid predictedFundings failed: %s", e)

        # Step 3: persist to DB
        try:
            from aletheia.storage.repositories import (
                insert_funding_rate_snapshot,
                insert_open_interest_snapshot,
            )
            for snap in funding:
                insert_funding_rate_snapshot(snap.model_dump())
            for snap in oi:
                insert_open_interest_snapshot(snap.model_dump())
        except Exception as e:
            errors.append(f"db_write: {e}")
            logger.error("Hyperliquid DB write failed: %s", e)

        logger.info(
            "Hyperliquid collected: %d funding, %d OI, %d errors",
            len(funding), len(oi), len(errors),
        )
        return {"funding": funding, "open_interest": oi, "errors": errors}


def _parse_funding(
    symbol: str, ctx: dict, observed_at: str
) -> FundingRateSnapshot | None:
    """Parse funding rate from a metaAndAssetCtxs context row."""
    rate_str = ctx.get("funding")
    if rate_str is None:
        return None
    try:
        rate = float(rate_str)
    except (ValueError, TypeError):
        return None

    annualized = round(rate * _INTERVALS_PER_DAY * _DAYS_PER_YEAR, 6)
    mark_str = ctx.get("markPx")
    mark = float(mark_str) if mark_str else None

    return FundingRateSnapshot(
        symbol=symbol,
        exchange="hyperliquid",
        funding_rate=rate,
        funding_rate_annualized=annualized,
        next_funding_time=None,   # not provided in this endpoint
        mark_price=mark,
        observed_at=observed_at,
    )


def _parse_oi(
    symbol: str, ctx: dict, observed_at: str
) -> OpenInterestSnapshot | None:
    """Parse open interest from a metaAndAssetCtxs context row.

    Hyperliquid reports OI in base-asset units (e.g. BTC).
    USD OI = openInterest × markPx.
    """
    oi_str = ctx.get("openInterest")
    mark_str = ctx.get("markPx")
    if oi_str is None or mark_str is None:
        return None
    try:
        oi_coin = float(oi_str)
        mark = float(mark_str)
    except (ValueError, TypeError):
        return None

    oi_usd = oi_coin * mark

    # Attempt to compute 1h change from previous snapshot
    oi_change_1h_pct = None
    try:
        from aletheia.storage.repositories import latest_open_interest
        prev = latest_open_interest(symbol, exchange="hyperliquid")
        if prev and prev.get("open_interest_usd") and float(prev["open_interest_usd"]) > 0:
            prev_usd = float(prev["open_interest_usd"])
            oi_change_1h_pct = round((oi_usd - prev_usd) / prev_usd * 100, 2)
    except Exception:
        pass

    return OpenInterestSnapshot(
        symbol=symbol,
        exchange="hyperliquid",
        open_interest_usd=oi_usd,
        open_interest_coin=oi_coin,
        oi_change_1h_pct=oi_change_1h_pct,
        long_short_ratio=None,
        observed_at=observed_at,
    )


async def _fetch_predicted_fundings(
    symbols: list[str] | None, observed_at: str
) -> list[FundingRateSnapshot]:
    """Fetch predicted funding rates from Hyperliquid.

    Returns FundingRateSnapshot entries with exchange='hyperliquid_predicted'
    sourced from the 'HlPerp' venue in the predictedFundings response.
    """
    results: list[FundingRateSnapshot] = []

    symbol_filter: set[str] | None = None
    if symbols:
        symbol_filter = {s.upper() for s in symbols}

    async with httpx_client(timeout=15) as client:
        resp = await client.post(
            _HL_INFO_URL,
            json={"type": "predictedFundings"},
        )
        resp.raise_for_status()
        data = resp.json()

    if not isinstance(data, list):
        return results

    for item in data:
        if not isinstance(item, list) or len(item) < 2:
            continue
        asset_name = item[0]
        venues = item[1]
        if not isinstance(asset_name, str):
            continue

        canonical = f"{asset_name}/USDT"
        if symbol_filter and canonical.upper() not in symbol_filter:
            continue

        # Find HlPerp venue for Hyperliquid's own predicted rate
        for venue_entry in venues:
            if not isinstance(venue_entry, list) or len(venue_entry) < 2:
                continue
            venue_name, venue_data = venue_entry[0], venue_entry[1]
            if venue_name != "HlPerp":
                continue
            rate_str = venue_data.get("fundingRate")
            if rate_str is None:
                continue
            try:
                rate = float(rate_str)
            except (ValueError, TypeError):
                continue

            interval_hours = venue_data.get("fundingIntervalHours", 1)
            intervals_per_year = (24 / interval_hours) * 365
            annualized = round(rate * intervals_per_year, 6)

            next_ts = venue_data.get("nextFundingTime")
            next_str = None
            if next_ts:
                try:
                    next_str = datetime.fromtimestamp(
                        next_ts / 1000, tz=timezone.utc
                    ).isoformat()
                except Exception:
                    pass

            results.append(FundingRateSnapshot(
                symbol=canonical,
                exchange="hyperliquid_predicted",
                funding_rate=rate,
                funding_rate_annualized=annualized,
                next_funding_time=next_str,
                mark_price=None,
                observed_at=observed_at,
            ))
            break   # only HlPerp per asset

    return results
