"""Microstructure collector — orderbook spread, depth, and imbalance."""

from __future__ import annotations

import logging
import time
from typing import Any

from aletheia.data.models import OrderbookSnapshot
from aletheia.data import orderbook

logger = logging.getLogger("aletheia.data.collectors.microstructure")


class MicrostructureCollector:
    domain = "microstructure"

    async def collect(self, symbols: list[str], *, venue: str | None = None) -> dict[str, Any]:
        from aletheia.data.ccxt_provider import get_market_data_provider

        try:
            provider = get_market_data_provider(exchange=venue, native_ccxt=True)
        except Exception as exc:
            return {"snapshots": [], "errors": [f"provider_setup:{type(exc).__name__}"]}
        snapshots: list[OrderbookSnapshot] = []
        errors: list[str] = []

        try:
            from aletheia.data.symbols import InstrumentId

            for sym in symbols:
                phase = "provider_setup"
                try:
                    if isinstance(sym, InstrumentId):
                        if sym.exchange != provider._primary_name:
                            raise ValueError("orderbook venue mismatch")
                        fetch_sym = sym.to_ccxt_symbol()
                        canonical_sym = sym.symbol
                        market_type = sym.market_type
                    else:
                        fetch_sym = sym
                        canonical_sym = sym
                        market_type = "spot"

                    exchange = provider._ensure_primary(market_type)
                    with provider._market_type_context(exchange, market_type):
                        phase = "load_markets"
                        await exchange.load_markets()
                        phase = "market_metadata"
                        market = exchange.market(fetch_sym)
                        is_perp = market_type in {"perp", "futures"}
                        if (market.get("symbol") != fetch_sym
                                or bool(market.get("contract")) != is_perp):
                            raise ValueError("orderbook market identity mismatch")
                        contract_size = orderbook.contract_size_from_market(market)
                        t0 = time.time()
                        phase = "fetch_order_book"
                        ob = await exchange.fetch_order_book(fetch_sym, limit=20)
                    latency_ms = int((time.time() - t0) * 1000)

                    phase = "parse"
                    if ob.get("symbol") not in (None, fetch_sym):
                        raise ValueError("orderbook response identity mismatch")
                    snap = orderbook.parse_orderbook(
                        canonical_sym,
                        provider._primary_name,
                        ob,
                        latency_ms,
                        contract_size=contract_size,
                        market_type=market_type,
                    )
                    if snap:
                        snapshots.append(snap)
                    else:
                        errors.append("parse:invalid_orderbook")
                except Exception as exc:
                    errors.append(f"{phase}:{type(exc).__name__}")
        finally:
            await provider.close()

        return {"snapshots": snapshots, "errors": errors}
