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

from aletheia.data.derivatives_facts import hyperliquid_instrument, hyperliquid_metadata, price_snapshot

import logging
import math
from datetime import datetime, timezone
from typing import Any

from aletheia.net import close_ccxt, make_ccxt
from aletheia.data.models import FundingRateSnapshot, OpenInterestSnapshot

logger = logging.getLogger("aletheia.data.collectors.hyperliquid")

# Hyperliquid uses 1-hour funding intervals → ×24×365 for annualized rate.


class HyperliquidCollector:
    domain = "hyperliquid"

    async def collect(self, symbols: list[str] | None = None) -> dict[str, Any]:
        """Collect OI and funding rate for all Hyperliquid perpetuals.

        symbols: if provided, only include these canonical symbols (BTC/USDT etc.).
                 None = return all available assets.

        Returns {"funding": [...], "open_interest": [...], "errors": [...]}.
        """
        from extensions.learning.opportunity_information_policy import (
            ensure_opportunity_information_policy_epoch,
        )

        ensure_opportunity_information_policy_epoch()
        funding: list[FundingRateSnapshot] = []
        oi: list[OpenInterestSnapshot] = []
        prices: list[dict] = []
        errors: list[str] = []

        exchange, session = make_ccxt("hyperliquid")
        try:
            # Step 1: fetch market context (OI + current funding)
            meta_ctxs: list | None = None
            try:
                meta_ctxs = await exchange.publicPostInfo({"type": "metaAndAssetCtxs"})
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

                for asset_meta, ctx in zip(universe, ctxs, strict=False):
                    if not isinstance(asset_meta, dict) or not isinstance(ctx, dict):
                        continue
                    asset_name = asset_meta.get("name", "")
                    if not asset_name:
                        continue

                    canonical = f"{asset_name}/USDT"
                    if symbol_filter and canonical.upper() not in symbol_filter:
                        continue

                    physical = hyperliquid_instrument(canonical)
                    price = price_snapshot(canonical, 'hyperliquid',
                        {'markPrice': ctx.get('markPx'), 'indexPrice': ctx.get('oraclePx')},
                        instrument=physical, acquired_at=datetime.fromisoformat(now),
                        provider='hyperliquid_public', reference_kind='oracle')
                    if price:
                        prices.append(price)

                    fr_snap = _parse_funding(canonical, ctx, now)
                    if fr_snap:
                        fr_snap.derivatives_metadata_json = hyperliquid_metadata(canonical, now)
                        funding.append(fr_snap)

                    oi_snap = _parse_oi(canonical, ctx, now)
                    if oi_snap:
                        oi_snap.derivatives_metadata_json = hyperliquid_metadata(canonical, now, oi=True)
                        oi.append(oi_snap)

            # Step 2: predicted fundings (best-effort; does not block Step 1 writes)
            try:
                predicted = await _fetch_predicted_fundings(exchange, symbols, now)
                # Extend funding list with predicted entries (exchange tag differs)
                funding.extend(predicted)
            except Exception as e:
                errors.append(f"predictedFundings: {e}")
                logger.debug("Hyperliquid predictedFundings failed: %s", e)
        finally:
            await close_ccxt(exchange, session)

        logger.info(
            "Hyperliquid collected: %d funding, %d OI, %d errors",
            len(funding), len(oi), len(errors),
        )
        return {"funding": funding, "open_interest": oi, "prices": prices, "errors": errors}


def _parse_funding(
    symbol: str, ctx: dict, observed_at: str
) -> FundingRateSnapshot | None:
    """Parse funding rate from a metaAndAssetCtxs context row."""
    from aletheia.data.derivatives_facts import annualize_funding, finite
    rate, mark = finite(ctx.get('funding')), finite(ctx.get('markPx'))
    annualized = annualize_funding(rate, 3600)
    if annualized is None or mark is None or mark <= 0:
        return None

    return FundingRateSnapshot(
        symbol=symbol,
        exchange="hyperliquid",
        funding_rate=rate,
        funding_rate_annualized=annualized,
        next_funding_time=None,   # not provided in this endpoint
        mark_price=mark,
        observed_at=observed_at,
        derivatives_metadata_json=hyperliquid_metadata(symbol, observed_at),
    )


def _parse_oi(
    symbol: str, ctx: dict, observed_at: str
) -> OpenInterestSnapshot | None:
    """Parse open interest from a metaAndAssetCtxs context row.

    Hyperliquid reports OI in base-asset units (e.g. BTC).
    USD OI = openInterest × markPx.
    """
    from aletheia.data.derivatives_facts import finite, open_interest_usd, project_snapshot, _open_interest_change
    oi_coin = finite(ctx.get('openInterest'))
    oi_usd, reason = open_interest_usd({'openInterestAmount':ctx.get('openInterest')},
        instrument=hyperliquid_instrument(symbol), amount_unit='base', mark_price=ctx.get('markPx'))
    if reason or oi_usd is None:
        return None
    metadata = hyperliquid_metadata(symbol, observed_at, oi=True)
    row = dict(symbol=symbol, exchange='hyperliquid', open_interest_usd=oi_usd,
        observed_at=observed_at, derivatives_metadata_json=metadata)
    delta = _open_interest_change(row, project_snapshot(row, 'open_interest')[0], include_comparison=False)['value']
    oi_change_1h_pct = delta*100 if delta is not None else None

    return OpenInterestSnapshot(
        symbol=symbol,
        exchange="hyperliquid",
        open_interest_usd=oi_usd,
        open_interest_coin=oi_coin,
        oi_change_1h_pct=oi_change_1h_pct,
        long_short_ratio=None,
        derivatives_metadata_json=metadata,
        observed_at=observed_at,
    )


async def _fetch_predicted_fundings(
    exchange: Any, symbols: list[str] | None, observed_at: str
) -> list[FundingRateSnapshot]:
    """Fetch predicted funding rates from Hyperliquid.

    Returns FundingRateSnapshot entries with exchange='hyperliquid_predicted'
    sourced from the 'HlPerp' venue in the predictedFundings response.
    """
    results: list[FundingRateSnapshot] = []

    symbol_filter: set[str] | None = None
    if symbols:
        symbol_filter = {s.upper() for s in symbols}

    data = await exchange.publicPostInfo({"type": "predictedFundings"})

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
                interval_hours = float(venue_data["fundingIntervalHours"]) if venue_data.get("fundingIntervalHours") is not None else None
            except (ValueError, TypeError):
                continue
            if (
                not math.isfinite(rate)
                or (interval_hours is not None and (not math.isfinite(interval_hours) or interval_hours <= 0))
            ):
                continue
            from aletheia.data.derivatives_facts import annualize_funding
            annualized = annualize_funding(rate, interval_hours * 3600 if interval_hours is not None else None)

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
                derivatives_metadata_json=hyperliquid_metadata(
                    canonical, observed_at, value_kind='forecast',
                    interval_seconds=interval_hours * 3600 if interval_hours is not None else None),
            ))
            break   # only HlPerp per asset

    return results
