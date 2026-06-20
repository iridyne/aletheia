"""OKX exchange client — thin CCXT wrapper for demo and live modes.

Supports:
- place_order(OrderRequest) -> OrderResponse
- get_positions() -> list of position dicts
- fetch_balance() -> CCXT unified balance dict
- fetch_time() -> Unix ms (connectivity probe)
- load_demo_markets() -> frozenset of canonical symbols supported on demo
- close() for session teardown

Demo mode: CCXT sandbox mode injects `x-simulated-trading: 1` header.
"""

import logging
from dataclasses import dataclass

import ccxt.async_support as ccxt

from aletheia.config.secrets import get_secret
from aletheia.data.symbols import to_canonical

logger = logging.getLogger("aletheia.okx_client")


@dataclass
class OrderRequest:
    symbol: str  # canonical BTC/USDT
    market_type: str  # usdt_futures
    side: str  # buy | sell
    order_type: str  # market | limit
    quantity: float
    price: float | None = None
    client_oid: str | None = None
    reduce_only: bool = False


@dataclass
class OrderResponse:
    order_id: str
    client_oid: str | None = None
    status: str = "submitted"
    fill_price: float | None = None
    filled_quantity: float | None = None


class OKXClient:
    """OKX USDT-M perpetual swaps via CCXT. Supports demo and live modes."""

    def __init__(self, mode: str = "demo"):
        api_key = get_secret(f"okx.{mode}.api_key")
        api_secret = get_secret(f"okx.{mode}.api_secret")
        passphrase = get_secret(f"okx.{mode}.passphrase")
        if not api_key or not api_secret or not passphrase:
            raise RuntimeError(
                f"OKX {mode} credentials not configured. "
                f"Set okx.{mode}.api_key, okx.{mode}.api_secret, and okx.{mode}.passphrase "
                "via Settings → Credentials."
            )
        self._exchange = ccxt.okx({
            "apiKey": api_key,
            "secret": api_secret,
            "password": passphrase,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        if mode == "demo":
            self._exchange.set_sandbox_mode(True)

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        """Place a market or limit order on OKX."""
        ccxt_symbol = f"{order.symbol}:USDT"  # CCXT perp format: BTC/USDT:USDT

        params = {}
        if order.reduce_only:
            params["reduceOnly"] = True
        if order.client_oid:
            params["clOrdId"] = order.client_oid[:32]

        try:
            resp = await self._exchange.create_order(
                symbol=ccxt_symbol,
                type=order.order_type,
                side=order.side,
                amount=order.quantity,
                price=order.price if order.order_type == "limit" else None,
                params=params,
            )
        except ccxt.BaseError as e:
            logger.error("OKX order failed: %s", e)
            raise RuntimeError(f"OKX: {e}") from e

        status = self._map_status(resp.get("status", ""))
        fill_price = None
        filled_qty = None
        if status == "filled":
            fill_price = float(resp["average"]) if resp.get("average") else None
            filled_qty = float(resp["filled"]) if resp.get("filled") else None

        return OrderResponse(
            order_id=resp.get("id", ""),
            client_oid=resp.get("clientOrderId"),
            status=status,
            fill_price=fill_price,
            filled_quantity=filled_qty,
        )

    async def get_positions(self) -> list[dict]:
        """Fetch open positions."""
        try:
            positions = await self._exchange.fetch_positions()
            result = []
            for p in positions:
                qty = abs(float(p.get("contracts", 0) or 0))
                if qty == 0:
                    continue
                result.append({
                    "symbol": p.get("symbol", ""),
                    "side": p.get("side", ""),
                    "quantity": qty,
                    "entry_price": float(p.get("entryPrice", 0) or 0),
                    "unrealized_pnl": float(p.get("unrealizedPnl", 0) or 0),
                    "mark_price": float(p.get("markPrice", 0) or 0),
                })
            return result
        except ccxt.BaseError as e:
            logger.error("OKX get_positions failed: %s", e)
            raise RuntimeError(f"OKX positions: {e}") from e

    async def fetch_balance(self) -> dict:
        """Fetch account balance. Returns CCXT unified balance."""
        try:
            return await self._exchange.fetch_balance()
        except ccxt.BaseError as e:
            logger.error("OKX fetch_balance failed: %s", e)
            raise RuntimeError(f"OKX balance: {e}") from e

    async def fetch_time(self) -> int:
        """Fetch server time (connectivity test). Returns Unix ms."""
        try:
            return await self._exchange.fetch_time()
        except ccxt.BaseError as e:
            raise RuntimeError(f"OKX time: {e}") from e

    async def close(self):
        """Close the underlying CCXT session."""
        await self._exchange.close()

    @staticmethod
    async def load_demo_markets() -> frozenset:
        """Load supported symbols on OKX demo (sandbox) without credentials.

        Returns a frozenset of canonical symbols (e.g. {"BTC/USDT", "ETH/USDT"}).
        """
        exchange = ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        exchange.set_sandbox_mode(True)
        try:
            await exchange.load_markets()
            symbols = set()
            for key, info in exchange.markets.items():
                if not info.get("active", True):
                    continue
                if info.get("type") != "swap":
                    continue
                quote = (info.get("quote") or "").upper()
                if quote != "USDT":
                    continue
                canonical = to_canonical(info.get("base", "") + "/USDT")
                symbols.add(canonical)
            return frozenset(symbols)
        finally:
            await exchange.close()

    @staticmethod
    def _map_status(raw: str) -> str:
        mapping = {
            "closed": "filled",
            "open": "submitted",
            "canceled": "cancelled",
            "expired": "cancelled",
        }
        return mapping.get(raw, raw)
