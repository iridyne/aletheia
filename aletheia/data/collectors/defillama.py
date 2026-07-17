"""DefiLlama collector — stablecoin market cap, chain TVL, DEX volume.

Data sources (all public, zero key):
  https://stablecoins.llama.fi/stablecoins?includePrices=true
      → total USD-pegged stablecoin supply, top issuers
  https://api.llama.fi/v2/chains
      → TVL per blockchain; summed for total DeFi TVL
  https://api.llama.fi/overview/dexs
      → total DEX trading volume (24h, 7d)

Written to: onchain_snapshots (new table).
source tag: "defillama"
metric_type values: "stablecoin_mcap" | "chain_tvl" | "dex_volume"
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from aletheia.net import httpx_client

logger = logging.getLogger("aletheia.data.collectors.defillama")

_DL_STABLECOINS_URL = "https://stablecoins.llama.fi/stablecoins?includePrices=true"
_DL_CHAINS_URL = "https://api.llama.fi/v2/chains"
_DL_DEXS_URL = "https://api.llama.fi/overview/dexs"

# Number of top chains to persist in top_entries JSON blob
_TOP_CHAINS_N = 20
# Number of top DEX protocols to persist in top_entries JSON blob
_TOP_DEXS_N = 10


class DefiLlamaCollector:
    domain = "defillama"

    async def collect(self) -> dict[str, Any]:
        """Collect stablecoin market cap, chain TVL, and DEX volume.

        Each of the three endpoints is isolated: one endpoint failure does not
        prevent the other two from writing (per spec DL-C01). All three run
        concurrently so a slow endpoint does not serialize the others.

        Returns {"snapshots": [...OnchainSnapshot dicts...], "errors": [...]}.
        """
        from aletheia.storage.models import OnchainSnapshot

        snapshots: list[OnchainSnapshot] = []
        errors: list[str] = []
        now = datetime.now(timezone.utc).isoformat()

        _LABELS = ("stablecoin_mcap", "chain_tvl", "dex_volume")
        results = await asyncio.gather(
            _fetch_stablecoin_mcap(now),
            _fetch_chain_tvl(now),
            _fetch_dex_volume(now),
            return_exceptions=True,
        )
        for label, result in zip(_LABELS, results):
            if isinstance(result, BaseException):
                errors.append(f"{label}: {result}")
                logger.warning("DefiLlama %s fetch failed: %s", label, result)
            elif result is not None:
                snapshots.append(result)

        # Persist to DB
        try:
            from aletheia.storage.repositories import insert_onchain_snapshot
            for snap in snapshots:
                insert_onchain_snapshot(snap.model_dump())
        except Exception as e:
            errors.append(f"db_write: {e}")
            logger.error("DefiLlama DB write failed: %s", e)

        logger.info(
            "DefiLlama collected: %d snapshots, %d errors",
            len(snapshots), len(errors),
        )
        return {"snapshots": snapshots, "errors": errors}


async def _fetch_stablecoin_mcap(observed_at: str):
    """Fetch total stablecoin market cap from DefiLlama.

    Returns an OnchainSnapshot with metric_type='stablecoin_mcap'.
    total_usd = sum of all pegged USD assets' circulating supply.
    """
    from aletheia.storage.models import OnchainSnapshot

    async with httpx_client(timeout=20) as client:
        resp = await client.get(_DL_STABLECOINS_URL)
        resp.raise_for_status()
        data = resp.json()

    assets = data.get("peggedAssets", [])
    if not assets:
        return None

    total_usd = 0.0
    prev_total = 0.0
    top_entries = []

    for asset in assets:
        circulating = asset.get("circulating", {})
        pegged_usd = circulating.get("peggedUSD", 0) or 0
        total_usd += pegged_usd

        prev_circulating = asset.get("circulatingPrevDay", {})
        prev_usd = prev_circulating.get("peggedUSD", 0) or 0
        prev_total += prev_usd

    # Top 10 stablecoins by market cap
    sorted_assets = sorted(
        assets,
        key=lambda a: a.get("circulating", {}).get("peggedUSD", 0) or 0,
        reverse=True,
    )[:10]
    for a in sorted_assets:
        circulating_usd = a.get("circulating", {}).get("peggedUSD", 0) or 0
        top_entries.append({
            "symbol": a.get("symbol", ""),
            "name": a.get("name", ""),
            "mcap_usd": round(circulating_usd, 0),
            "peg_type": a.get("pegType", ""),
            "peg_mechanism": a.get("pegMechanism", ""),
        })

    change_24h_pct = None
    if prev_total > 0:
        change_24h_pct = round((total_usd - prev_total) / prev_total * 100, 4)

    raw_summary = {
        "total_assets_count": len(assets),
        "total_usd": round(total_usd, 0),
        "prev_day_total_usd": round(prev_total, 0),
    }

    return OnchainSnapshot(
        source="defillama",
        metric_type="stablecoin_mcap",
        total_usd=round(total_usd, 0),
        change_24h_pct=change_24h_pct,
        top_entries=json.dumps(top_entries),
        raw_summary=json.dumps(raw_summary),
        observed_at=observed_at,
    )


async def _fetch_chain_tvl(observed_at: str):
    """Fetch total and per-chain TVL from DefiLlama.

    Returns an OnchainSnapshot with metric_type='chain_tvl'.
    total_usd = sum of all chains' TVL.
    """
    from aletheia.storage.models import OnchainSnapshot

    async with httpx_client(timeout=20) as client:
        resp = await client.get(_DL_CHAINS_URL)
        resp.raise_for_status()
        chains = resp.json()

    if not isinstance(chains, list):
        return None

    total_tvl = sum(c.get("tvl", 0) or 0 for c in chains)

    sorted_chains = sorted(
        chains,
        key=lambda c: c.get("tvl", 0) or 0,
        reverse=True,
    )[:_TOP_CHAINS_N]

    top_entries = [
        {
            "name": c.get("name", ""),
            "token_symbol": c.get("tokenSymbol", ""),
            "tvl_usd": round(c.get("tvl", 0) or 0, 0),
            "chain_id": c.get("chainId"),
        }
        for c in sorted_chains
    ]

    raw_summary = {
        "total_chains_count": len(chains),
        "total_tvl_usd": round(total_tvl, 0),
    }

    return OnchainSnapshot(
        source="defillama",
        metric_type="chain_tvl",
        total_usd=round(total_tvl, 0),
        change_24h_pct=None,   # /v2/chains does not provide prev-day TVL total
        top_entries=json.dumps(top_entries),
        raw_summary=json.dumps(raw_summary),
        observed_at=observed_at,
    )


async def _fetch_dex_volume(observed_at: str):
    """Fetch aggregated DEX trading volume from DefiLlama.

    Returns an OnchainSnapshot with metric_type='dex_volume'.
    total_usd = total 24h DEX volume across all protocols.
    """
    from aletheia.storage.models import OnchainSnapshot

    async with httpx_client(timeout=20) as client:
        resp = await client.get(_DL_DEXS_URL)
        resp.raise_for_status()
        data = resp.json()

    if not isinstance(data, dict):
        return None

    total_24h = data.get("total24h", 0) or 0
    total_7d = data.get("total7d", 0) or 0
    change_1d = data.get("change_1d")

    protocols = data.get("protocols", [])
    sorted_protocols = sorted(
        protocols,
        key=lambda p: p.get("total24h", 0) or 0,
        reverse=True,
    )[:_TOP_DEXS_N]

    top_entries = [
        {
            "name": p.get("name", ""),
            "volume_24h_usd": round(p.get("total24h", 0) or 0, 0),
            "volume_7d_usd": round(p.get("total7d", 0) or 0, 0),
        }
        for p in sorted_protocols
    ]

    change_24h_pct: float | None = None
    try:
        if change_1d is not None:
            change_24h_pct = round(float(change_1d), 4)
    except (ValueError, TypeError):
        pass

    raw_summary = {
        "total_24h_usd": round(total_24h, 0),
        "total_7d_usd": round(total_7d, 0),
        "protocols_count": len(protocols),
    }

    return OnchainSnapshot(
        source="defillama",
        metric_type="dex_volume",
        total_usd=round(total_24h, 0),
        change_24h_pct=change_24h_pct,
        top_entries=json.dumps(top_entries),
        raw_summary=json.dumps(raw_summary),
        observed_at=observed_at,
    )
