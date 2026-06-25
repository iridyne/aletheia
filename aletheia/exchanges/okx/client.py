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
        # Do NOT create the aiohttp session / ccxt exchange here. __init__ runs in
        # a synchronous context (often a different event loop than the one that
        # will await our methods, e.g. okx_broker._run_coro spins a fresh
        # asyncio.run() in a worker thread). aiohttp binds its connector to the
        # CREATING loop, so an exchange built here then used under a different loop
        # raises "Future attached to a different loop" — which silently broke
        # load_markets()/contractSize lookup and re-exposed the contractSize bug
        # (orders mis-sized by contractSize). Defer creation to _ensure_exchange()
        # so the session is born in the same loop that awaits it (CLAUDE.md: CCXT
        # binds aiohttp to the creating event loop).
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase
        self._mode = mode
        self._session = None
        self._exchange = None

    def _ensure_exchange(self):
        """Lazily build the ccxt exchange + aiohttp session in the CURRENT loop.

        Must only be called from inside an async method (i.e. with a running loop
        that will also drive every subsequent await on this exchange).
        """
        if self._exchange is not None:
            return self._exchange
        from aletheia.net import aiohttp_session, ccxt_timeout_options
        self._session = aiohttp_session()
        self._exchange = ccxt.okx({
            **ccxt_timeout_options(),
            "apiKey": self._api_key,
            "secret": self._api_secret,
            "password": self._passphrase,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
            "session": self._session,
        })
        if self._mode == "demo":
            self._exchange.set_sandbox_mode(True)
        return self._exchange

    async def _contract_size(self, ccxt_symbol: str) -> float:
        """Return the contractSize for an OKX swap symbol (coins per contract).

        CCXT's `amount` for OKX swaps is denominated in CONTRACTS, not coins.
        Each contract represents `contractSize` coins (e.g. TRX=1000, ETH=0.1,
        SOL=1, BTC=0.01). We store/risk/PnL everything in coin quantity, so the
        order path divides coins→contracts and the read path multiplies back.
        Defaults to 1.0 when markets are unavailable (safe for contractSize==1).
        """
        try:
            ex = self._ensure_exchange()
            if not ex.markets:
                await ex.load_markets()
            m = ex.markets.get(ccxt_symbol) or {}
            cs = m.get("contractSize")
            return float(cs) if cs else 1.0
        except Exception as e:
            logger.warning("OKX contractSize lookup failed for %s: %s — defaulting to 1.0", ccxt_symbol, e)
            return 1.0

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        """Place a market or limit order on OKX.

        `order.quantity` is in COIN units (our canonical internal unit). OKX/CCXT
        expects CONTRACTS, so we convert coins→contracts via contractSize before
        submitting, and convert the filled contracts back to coins on the way out.
        """
        ccxt_symbol = f"{order.symbol}:USDT"  # CCXT perp format: BTC/USDT:USDT
        ex = self._ensure_exchange()
        contract_size = await self._contract_size(ccxt_symbol)
        amount_contracts = order.quantity / contract_size if contract_size else order.quantity

        params = {}
        if order.reduce_only:
            params["reduceOnly"] = True
        if order.client_oid:
            params["clOrdId"] = order.client_oid[:32]

        try:
            resp = await ex.create_order(
                symbol=ccxt_symbol,
                type=order.order_type,
                side=order.side,
                amount=amount_contracts,
                price=order.price if order.order_type == "limit" else None,
                params=params,
            )
        except ccxt.BaseError as e:
            logger.error("OKX order failed: %s", e)
            raise RuntimeError(f"OKX: {e}") from e

        status = self._map_status(resp.get("status", ""))
        order_id = resp.get("id", "")

        # OKX market orders fill instantly but CCXT create_order may return
        # status "open" before the exchange updates. Poll once to confirm fill.
        if status != "filled" and order.order_type == "market" and order_id:
            import asyncio as _aio
            await _aio.sleep(0.3)
            try:
                fetched = await ex.fetch_order(order_id, ccxt_symbol)
                status = self._map_status(fetched.get("status", status))
                if status == "filled":
                    resp = fetched
            except Exception as e:
                logger.debug("fetch_order after market order failed: %s", e)

        fill_price = None
        filled_qty = None
        if status == "filled":
            fill_price = float(resp["average"]) if resp.get("average") else None
            # resp["filled"] is in CONTRACTS — convert back to coin units to match
            # our internal/storage convention.
            if resp.get("filled"):
                filled_qty = float(resp["filled"]) * contract_size

        return OrderResponse(
            order_id=order_id,
            client_oid=resp.get("clientOrderId"),
            status=status,
            fill_price=fill_price,
            filled_quantity=filled_qty,
        )

    async def get_positions(self) -> list[dict]:
        """Fetch open positions.

        CCXT reports `contracts` (contract count); we expose `quantity` in COIN
        units (contracts × contractSize) so storage/PnL/adoption stay consistent
        with the rest of the system. CCXT's per-position `contractSize` is used
        directly (no markets lookup needed).
        """
        try:
            positions = await self._ensure_exchange().fetch_positions()
            result = []
            for p in positions:
                contracts = abs(float(p.get("contracts", 0) or 0))
                if contracts == 0:
                    continue
                contract_size = float(p.get("contractSize") or 1.0)
                qty = contracts * contract_size
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
            return await self._ensure_exchange().fetch_balance()
        except ccxt.BaseError as e:
            logger.error("OKX fetch_balance failed: %s", e)
            raise RuntimeError(f"OKX balance: {e}") from e

    async def fetch_time(self) -> int:
        """Fetch server time (connectivity test). Returns Unix ms."""
        try:
            return await self._ensure_exchange().fetch_time()
        except ccxt.BaseError as e:
            raise RuntimeError(f"OKX time: {e}") from e

    async def close(self):
        """Close the underlying CCXT session and owned aiohttp session.

        No-op if the exchange was never lazily created (e.g. constructed then
        closed without any awaited call).
        """
        if self._exchange is not None:
            await self._exchange.close()
        if self._session is not None:
            await self._session.close()

    @staticmethod
    async def load_demo_markets() -> frozenset:
        """Load supported symbols on OKX demo (sandbox) without credentials.

        Returns a frozenset of canonical symbols (e.g. {"BTC/USDT", "ETH/USDT"}).
        """
        from aletheia.net import aiohttp_session, ccxt_timeout_options
        session = aiohttp_session()
        exchange = ccxt.okx({
            **ccxt_timeout_options(),
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
            "session": session,
        })
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
            await session.close()

    @staticmethod
    def _map_status(raw: str) -> str:
        mapping = {
            "closed": "filled",
            "open": "submitted",
            "canceled": "cancelled",
            "expired": "cancelled",
        }
        return mapping.get(raw, raw)
