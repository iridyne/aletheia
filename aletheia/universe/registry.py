"""Asset registry sync — fetches all tradable pairs from the exchange via CCXT."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aletheia.config.schema import AletheiaConfig
from aletheia.data.symbols import to_canonical
from aletheia.storage.models import UniverseAsset
from aletheia.storage import repositories as repo

logger = logging.getLogger("aletheia.universe.registry")

_LEVERAGED_SUFFIXES = ("3L", "3S", "5L", "5S", "2L", "2S", "UP", "DOWN", "BULL", "BEAR")
_EXCLUDED_BASES = {"BTTC", "LUNC", "LUNA", "UST", "FTT"}


def _is_excluded(symbol_info: dict) -> bool:
    """Filter out leveraged tokens, test symbols, and known-bad assets."""
    base = (symbol_info.get("base") or "").upper()
    symbol_id = (symbol_info.get("id") or "").upper()

    if any(base.endswith(s) for s in _LEVERAGED_SUFFIXES):
        return True
    if base in _EXCLUDED_BASES:
        return True
    if "TEST" in symbol_id or "_" in symbol_id:
        return True
    return False


async def sync_registry(cfg: AletheiaConfig) -> int:
    """Sync the universe_registry from the configured exchange.

    Uses CCXT load_markets() to get all active USDT-quoted pairs.
    Returns count of assets upserted.
    """
    from aletheia.data.ccxt_provider import CCXTProvider

    venue = cfg.market_data.ccxt_exchange
    provider = CCXTProvider(exchange=venue, fallback_exchange=venue)
    count = 0

    try:
        exchange = provider._ensure_primary()
        market_types = ["spot"]
        if cfg.exchange.enabled_markets.get("usdt_futures"):
            market_types.append("swap")
        exchange.options["defaultType"] = "spot"
        exchange.options["fetchMarkets"] = market_types
        await exchange.load_markets()
        markets = exchange.markets

        now = datetime.now(timezone.utc).isoformat()

        for symbol_key, info in markets.items():
            if not info.get("active", True):
                continue
            quote = (info.get("quote") or "").upper()
            if quote != "USDT":
                continue
            if _is_excluded(info):
                continue

            base = (info.get("base") or "").upper()
            if not base:
                continue

            canonical = f"{base}/{quote}"
            market_type = "spot"
            if info.get("swap") or info.get("future"):
                market_type = "perp"

            limits = info.get("limits") or {}
            cost_limits = limits.get("cost") or {}
            min_notional = cost_limits.get("min")

            precision = info.get("precision") or {}
            price_prec = precision.get("price")
            amount_prec = precision.get("amount")

            blacklisted = canonical in cfg.universe.blacklist

            asset = UniverseAsset(
                venue=venue,
                market_type=market_type,
                symbol=canonical,
                base_asset=base,
                quote_asset=quote,
                status="active",
                min_notional=min_notional,
                price_precision=int(price_prec) if price_prec is not None else None,
                amount_precision=int(amount_prec) if amount_prec is not None else None,
                first_seen_at=now,
                last_seen_at=now,
                is_blacklisted=blacklisted,
                blacklist_reason="config_blacklist" if blacklisted else None,
            )
            repo.upsert_universe_asset(asset)
            count += 1

        logger.info("Registry sync: %d assets from %s", count, venue)
    finally:
        await provider.close()

    return count
