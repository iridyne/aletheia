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
import math
from datetime import datetime, timezone
from typing import Any

from aletheia.net import httpx_client
from aletheia.data.models import FundingRateSnapshot, OpenInterestSnapshot

logger = logging.getLogger("aletheia.data.collectors.hyperliquid")

_HL_INFO_URL = "https://api.hyperliquid.xyz/info"

# Hyperliquid uses 1-hour funding intervals → ×24×365 for annualized rate.
_INTERVALS_PER_DAY = 24
_DAYS_PER_YEAR = 365
_OI_DELTA_SANITY_PCT = 300.0


class HyperliquidCollector:
    domain = "hyperliquid"

    def __init__(self, *, persist: bool = True) -> None:
        # Direct callers retain the documented collect-and-store behavior.
        # Scheduler ticks pass persist=False and publish all collector rows in
        # one short transaction after network work has completed.
        self.persist = persist

    async def collect(self, symbols: list[str] | None = None) -> dict[str, Any]:
        """Collect OI and funding rate for all Hyperliquid perpetuals.

        symbols: if provided, only include these canonical symbols (BTC/USDT etc.).
                 None = return all available assets.

        Returns {"funding": [...], "open_interest": [...], "errors": [...]}.
        """
        from aletheia.learning.opportunity_information_policy import (
            ensure_opportunity_information_policy_epoch,
        )

        ensure_opportunity_information_policy_epoch()
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

        if (
            meta_ctxs
            and isinstance(meta_ctxs, list)
            and len(meta_ctxs) >= 2
            and isinstance(meta_ctxs[0], dict)
            and isinstance(meta_ctxs[1], list)
        ):
            universe = meta_ctxs[0].get("universe", [])
            ctxs = meta_ctxs[1]

            # symbols filter set — canonical BTC/USDT, ETH/USDT, …
            symbol_filter: set[str] | None = None
            if symbols:
                symbol_filter = {s.upper() for s in symbols}

            for asset_meta, ctx in zip(universe, ctxs):
                if not isinstance(asset_meta, dict) or not isinstance(ctx, dict):
                    continue
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

        # Step 3: persist to DB for standalone use. Scheduler-owned collection
        # defers this phase so all domains share one commit.
        if self.persist:
            try:
                from aletheia.storage.repositories import insert_collector_snapshot_batch

                insert_collector_snapshot_batch(
                    funding=[snap.model_dump() for snap in funding],
                    open_interest=[snap.model_dump() for snap in oi],
                )
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
    mark_str = ctx.get("markPx")
    try:
        mark = float(mark_str)
    except (ValueError, TypeError):
        return None
    annualized = round(rate * _INTERVALS_PER_DAY * _DAYS_PER_YEAR, 6)
    if not all(math.isfinite(value) for value in (rate, mark, annualized)):
        return None
    if mark <= 0:
        return None

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
    if not math.isfinite(oi_coin) or not math.isfinite(mark):
        return None
    if oi_coin <= 0 or mark <= 0:
        return None

    oi_usd = oi_coin * mark
    if not math.isfinite(oi_usd) or oi_usd <= 0:
        return None

    # Compute 1h change only from a same-source 45-75 minute baseline.
    oi_change_1h_pct = None
    try:
        from aletheia.storage.repositories import open_interest_near
        prev = open_interest_near(
            symbol,
            "hyperliquid",
            reference_at=observed_at,
        )
        if prev and prev.get("open_interest_usd") is not None:
            prev_usd = float(prev["open_interest_usd"])
            if math.isfinite(prev_usd) and prev_usd > 0:
                delta = round((oi_usd - prev_usd) / prev_usd * 100, 2)
                if math.isfinite(delta) and abs(delta) <= _OI_DELTA_SANITY_PCT:
                    oi_change_1h_pct = delta
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
        if not isinstance(asset_name, str) or not isinstance(venues, list):
            continue

        canonical = f"{asset_name}/USDT"
        if symbol_filter and canonical.upper() not in symbol_filter:
            continue

        # Find HlPerp venue for Hyperliquid's own predicted rate
        for venue_entry in venues:
            if not isinstance(venue_entry, list) or len(venue_entry) < 2:
                continue
            venue_name, venue_data = venue_entry[0], venue_entry[1]
            if venue_name != "HlPerp" or not isinstance(venue_data, dict):
                continue
            rate_str = venue_data.get("fundingRate")
            if rate_str is None:
                continue
            try:
                rate = float(rate_str)
            except (ValueError, TypeError):
                continue
            try:
                interval_hours = float(venue_data.get("fundingIntervalHours", 1))
            except (ValueError, TypeError):
                continue
            if (
                not math.isfinite(rate)
                or not math.isfinite(interval_hours)
                or interval_hours <= 0
            ):
                continue
            intervals_per_year = (24 / interval_hours) * 365
            annualized = round(rate * intervals_per_year, 6)
            if not math.isfinite(annualized):
                continue

            next_ts = venue_data.get("nextFundingTime")
            next_str = None
            if next_ts is not None:
                try:
                    parsed_next_ts = float(next_ts)
                    if not math.isfinite(parsed_next_ts) or parsed_next_ts <= 0:
                        continue
                    next_str = datetime.fromtimestamp(
                        parsed_next_ts / 1000, tz=timezone.utc
                    ).isoformat()
                except Exception:
                    continue

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
