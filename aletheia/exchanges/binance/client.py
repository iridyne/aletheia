"""Binance Futures client — thin CCXT wrapper supporting testnet and live modes.

Implements the minimal surface the broker needs:
- place_order(OrderRequest) -> OrderResponse
- get_positions() -> list of exchange Position dicts
- fetch_balance() -> CCXT unified balance dict
- fetch_time() -> Unix ms (connectivity probe)
- close() for session teardown

Testnet: ccxt.binanceusdm with sandbox mode (testnet.binancefuture.com).
Live: ccxt.binanceusdm production (fapi.binance.com).
"""

import logging
from dataclasses import dataclass

import ccxt.async_support as ccxt

from aletheia.config.secrets import get_secret
from aletheia.data.symbols import to_ccxt_perp

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
    leverage: int | None = None   # USDT-futures: set on the exchange before an open


@dataclass
class OrderResponse:
    order_id: str
    client_oid: str | None = None
    status: str = "submitted"
    fill_price: float | None = None
    filled_quantity: float | None = None


class BinanceClient:
    """Binance USDT-M Futures via CCXT. Supports testnet and live modes."""

    def __init__(self, mode: str = "testnet"):
        self._mode = mode
        api_key = get_secret(f"binance.{mode}.api_key")
        api_secret = get_secret(f"binance.{mode}.api_secret")
        if not api_key or not api_secret:
            raise RuntimeError(
                f"Binance {mode} credentials not configured. "
                f"Set binance.{mode}.api_key and binance.{mode}.api_secret "
                "via Settings → Credentials."
            )
        # Do NOT build the ccxt exchange here. __init__ runs in a sync context (often a
        # different event loop than the one awaiting our methods — binance_broker spins
        # a fresh asyncio.run() in a worker thread). aiohttp binds its connector to the
        # CREATING loop, so an exchange built here then used under another loop raises
        # "Future attached to a different loop" and silently breaks load_markets()/
        # contractSize. Defer to _ensure_exchange() (mirrors OKXClient; §17.4 BIN-XLOOP).
        self._api_key = api_key
        self._api_secret = api_secret
        self._exchange = None
        self._session = None

    def _ensure_exchange(self):
        """Lazily build the ccxt exchange in the CURRENT loop. Call only from inside an
        async method (running loop that drives every subsequent await)."""
        if self._exchange is not None:
            return self._exchange
        # Build via the managed factory so the aiohttp session carries the
        # configured proxy — a bare ccxt.binanceusdm() would bypass it (proxy leak).
        from aletheia.net import make_ccxt
        self._exchange, self._session = make_ccxt(
            "binanceusdm",
            apiKey=self._api_key,
            secret=self._api_secret,
            options={"defaultType": "future"},
        )
        if self._mode == "testnet":
            self._exchange.set_sandbox_mode(True)
        return self._exchange

    async def set_leverage(self, ccxt_symbol: str, leverage: int) -> None:
        """Set leverage for a Binance Futures symbol before opening. Raises on failure
        (fail-closed) so the broker aborts rather than opening at the account default.
        Confirm against a live Binance testnet account."""
        await self._ensure_exchange().set_leverage(int(leverage), ccxt_symbol)

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        """Place a market or limit order on Binance Futures."""
        # CCXT perp format with settle suffix derived from the quote currency
        # (BTC/USDT:USDT, BTC/USDC:USDC) — not a hardcoded :USDT, which breaks any
        # non-USDT-quote instrument.
        ccxt_symbol = to_ccxt_perp(order.symbol)

        # Futures open: set intended leverage first (skip reduce-only closes). Fail-closed.
        if order.leverage and order.leverage > 1 and not order.reduce_only:
            await self.set_leverage(ccxt_symbol, order.leverage)

        params = {}
        if order.reduce_only:
            params["reduceOnly"] = True
        if order.client_oid:
            params["newClientOrderId"] = order.client_oid[:36]

        try:
            resp = await self._ensure_exchange().create_order(
                symbol=ccxt_symbol,
                type=order.order_type,
                side=order.side,
                amount=order.quantity,
                price=order.price if order.order_type == "limit" else None,
                params=params,
            )
        except ccxt.BaseError as e:
            logger.error("Binance %s order failed: %s", self._mode, e)
            raise RuntimeError(f"Binance {self._mode}: {e}") from e

        status = self._map_status(resp.get("status", ""))
        order_id = resp.get("id", "")

        # Binance Futures create_order can return status "new"/"open" for a market
        # order that fills milliseconds later. Poll once to confirm (mirrors OKX) so
        # a real fill is not under-recorded as "submitted" and the position lost.
        if status != "filled" and order.order_type == "market" and order_id:
            import asyncio as _aio
            await _aio.sleep(0.3)
            try:
                fetched = await self._ensure_exchange().fetch_order(order_id, ccxt_symbol)
                status = self._map_status(fetched.get("status", status))
                resp = fetched
            except Exception as e:
                logger.debug("fetch_order after market order failed: %s", e)

        # Detect a partial fill (CCXT status "open" + filled>0) — mirror OKX/Bitget.
        if status != "filled" and float(resp.get("filled") or 0) > 0:
            status = "partially_filled"

        fill_price = None
        filled_qty = None
        if status in ("filled", "partially_filled"):
            fill_price = float(resp["average"]) if resp.get("average") else None
            filled_qty = float(resp["filled"]) if resp.get("filled") else None

        return OrderResponse(
            order_id=order_id,
            client_oid=resp.get("clientOrderId"),
            status=status,
            fill_price=fill_price,
            filled_quantity=filled_qty,
        )

    async def get_positions(self) -> list[dict]:
        """Fetch open positions."""
        try:
            positions = await self._ensure_exchange().fetch_positions()
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
            logger.error("Binance %s get_positions failed: %s", self._mode, e)
            raise RuntimeError(f"Binance {self._mode} positions: {e}") from e

    async def fetch_balance(self) -> dict:
        """Fetch account balance. Returns CCXT unified balance."""
        try:
            return await self._ensure_exchange().fetch_balance()
        except ccxt.BaseError as e:
            logger.error("Binance %s fetch_balance failed: %s", self._mode, e)
            raise RuntimeError(f"Binance {self._mode} balance: {e}") from e

    async def fetch_time(self) -> int:
        """Fetch server time (connectivity test). Returns Unix ms."""
        try:
            return await self._ensure_exchange().fetch_time()
        except ccxt.BaseError as e:
            raise RuntimeError(f"Binance {self._mode} time: {e}") from e

    async def close(self):
        """Close the underlying CCXT client and the injected proxy session."""
        if self._exchange is not None:
            await self._exchange.close()
        # ccxt won't close the session we injected (own_session=False) — do it here.
        if self._session is not None:
            await self._session.close()

    @staticmethod
    def _map_status(raw: str) -> str:
        mapping = {
            "closed": "filled",
            "open": "submitted",
            "canceled": "cancelled",
            "expired": "cancelled",
        }
        return mapping.get(raw, raw)


# Backward-compatible alias
BinanceTestnetClient = lambda: BinanceClient(mode="testnet")  # noqa: E731
