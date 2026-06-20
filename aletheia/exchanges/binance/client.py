"""Binance Futures Testnet client — thin CCXT wrapper.

Only implements the minimal surface the broker needs:
- place_order(OrderRequest) -> OrderResponse
- get_positions() -> list of exchange Position dicts
- close() for session teardown

Uses ccxt.binanceusdm with sandbox mode enabled (testnet.binancefuture.com).
"""

import logging
from dataclasses import dataclass

import ccxt.async_support as ccxt

from aletheia.config.secrets import get_secret

logger = logging.getLogger("aletheia.binance_client")


@dataclass
class OrderRequest:
    symbol: str  # canonical BTC/USDT (converted to CCXT perp format internally)
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


class BinanceTestnetClient:
    """Binance USDT-M Futures testnet via CCXT sandbox mode."""

    def __init__(self):
        api_key = get_secret("binance.testnet.api_key")
        api_secret = get_secret("binance.testnet.api_secret")
        if not api_key or not api_secret:
            raise RuntimeError(
                "Binance testnet credentials not configured. "
                "Set binance.testnet.api_key and binance.testnet.api_secret "
                "via Settings → Credentials."
            )
        self._exchange = ccxt.binanceusdm({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        self._exchange.set_sandbox_mode(True)

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        """Place a market or limit order on Binance Futures Testnet."""
        ccxt_symbol = f"{order.symbol}:USDT"  # CCXT perp format: BTC/USDT:USDT

        params = {}
        if order.reduce_only:
            params["reduceOnly"] = True
        if order.client_oid:
            params["newClientOrderId"] = order.client_oid[:36]

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
            logger.error("Binance testnet order failed: %s", e)
            raise RuntimeError(f"Binance testnet: {e}") from e

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
        """Fetch open positions from testnet."""
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
            logger.error("Binance testnet get_positions failed: %s", e)
            raise RuntimeError(f"Binance testnet positions: {e}") from e

    async def fetch_balance(self) -> dict:
        """Fetch account balance from testnet. Returns CCXT unified balance."""
        try:
            return await self._exchange.fetch_balance()
        except ccxt.BaseError as e:
            logger.error("Binance testnet fetch_balance failed: %s", e)
            raise RuntimeError(f"Binance testnet balance: {e}") from e

    async def fetch_time(self) -> int:
        """Fetch server time (connectivity test). Returns Unix ms."""
        try:
            return await self._exchange.fetch_time()
        except ccxt.BaseError as e:
            raise RuntimeError(f"Binance testnet time: {e}") from e

    async def close(self):
        """Close the underlying CCXT session."""
        await self._exchange.close()

    @staticmethod
    def _map_status(raw: str) -> str:
        mapping = {
            "closed": "filled",
            "open": "submitted",
            "canceled": "cancelled",
            "expired": "cancelled",
        }
        return mapping.get(raw, raw)
