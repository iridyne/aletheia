"""Normalized public market catalog; acquisition has no registry-write authority."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MarketCatalogEntry:
    symbol: str
    base: str
    quote: str
    market_type: str
    background: dict[str, Any]
    excluded_crypto: bool
    min_notional: float | None
    price_precision: int | None
    amount_precision: int | None


_catalog_fetch: Callable[[str, str], Awaitable[list[MarketCatalogEntry]]] | None = None


def configure_market_catalog(fetch: Callable[[str, str], Awaitable[list[MarketCatalogEntry]]] | None) -> None:
    global _catalog_fetch
    _catalog_fetch = fetch


async def fetch_market_catalog(venue: str, fallback_venue: str) -> list[MarketCatalogEntry]:
    if _catalog_fetch is None:
        raise RuntimeError("universe_market_catalog_extension_unavailable")
    return await _catalog_fetch(venue, fallback_venue)
