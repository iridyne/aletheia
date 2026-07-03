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
from aletheia.data.symbols import to_canonical, to_ccxt_perp

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
    leverage: int | None = None   # USDT-futures: set on the exchange before an open


@dataclass
class OrderResponse:
    order_id: str
    client_oid: str | None = None
    status: str = "submitted"
    fill_price: float | None = None
    filled_quantity: float | None = None


# ---------------------------------------------------------------------------
# Adapter capability + market-type routing (owner principle 2: the core is
# exchange-agnostic; spot-vs-perp differences live here at the adapter boundary).
# ---------------------------------------------------------------------------

# Market types this adapter can route to an OKX order. Both confirmed on demo
# (docs/research/DEMO_LIVE_SPOT_PARITY_EVENT_2026-06-28.md).
SUPPORTED_MARKET_TYPES = frozenset({"spot", "usdt_futures"})


def is_leveraged_market(market_type: str) -> bool:
    """Spot carries no leverage / no contracts / no funding; usdt_futures (perp) does."""
    return market_type != "spot"


def okx_ccxt_symbol(market_type: str, symbol: str) -> str:
    """Resolve the CCXT symbol for an OKX order by market type.

    spot         → canonical pair, NO settle suffix   (BTC/USDT)
    usdt_futures → CCXT perp with settle suffix        (BTC/USDT:USDT)
    """
    if market_type == "spot":
        return to_canonical(symbol)
    return to_ccxt_perp(symbol)


class OKXClient:
    """OKX via CCXT — USDT-M perpetual swaps AND spot. Supports demo and live modes."""

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
                assert ex.markets is not None
            m = ex.markets.get(ccxt_symbol) or {}
            cs = m.get("contractSize")
            return float(cs) if cs else 1.0
        except Exception as e:
            logger.warning("OKX contractSize lookup failed for %s: %s — defaulting to 1.0", ccxt_symbol, e)
            return 1.0

    async def set_leverage(self, ccxt_symbol: str, leverage: int) -> None:
        """Set leverage for an OKX swap symbol before opening. Raises on failure.

        Uses cross margin to match place_order's swap defaults. Fail-closed so the
        broker aborts rather than opening at the account-default leverage. The exact
        params (cross vs isolated, posSide in hedge mode) should be confirmed against
        a live OKX demo account.
        """
        ex = self._ensure_exchange()
        await ex.set_leverage(int(leverage), ccxt_symbol, params={"mgnMode": "cross"})

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        """Place a market or limit order on OKX.

        `order.quantity` is in COIN units (our canonical internal unit). Routing
        depends on order.market_type (owner principle 2 — adapter-boundary split):
        - usdt_futures (perp): OKX/CCXT `amount` is in CONTRACTS, so convert
          coins→contracts via contractSize, set leverage first (fail-closed), and
          convert filled contracts back to coins on the way out. Symbol carries a
          settle suffix (BTC/USDT:USDT) derived from the quote currency.
        - spot: `amount` is the coin quantity directly (no contracts, no leverage,
          no funding). A spot MARKET BUY uses tgtCcy=base_ccy so `amount` stays in
          base coins; reduceOnly does not exist for spot — a spot close is a plain
          market SELL of the held base amount.
        """
        spot = not is_leveraged_market(order.market_type)
        ccxt_symbol = okx_ccxt_symbol(order.market_type, order.symbol)
        ex = self._ensure_exchange()

        params = {}
        if order.client_oid:
            # OKX clOrdId: alphanumeric only, max 32 chars. Strip hyphens from UUIDs
            # and "close-<uuid>" prefixes before truncating.
            params["clOrdId"] = order.client_oid.replace("-", "")[:32]

        if spot:
            # No leverage, no contract conversion: amount == coin quantity.
            contract_size = 1.0
            amount = order.quantity
            # OKX spot MARKET BUY otherwise sizes `amount` in quote (cost); force
            # base-ccy sizing so it is interpreted as coins, matching our unit.
            if order.order_type == "market" and order.side == "buy":
                params["tgtCcy"] = "base_ccy"
            # NOTE: reduceOnly intentionally omitted — derivatives-only concept.
        else:
            # Futures open: set intended leverage first (skip reduce-only closes). Fail-closed.
            if order.leverage and order.leverage > 1 and not order.reduce_only:
                await self.set_leverage(ccxt_symbol, order.leverage)
            contract_size = await self._contract_size(ccxt_symbol)
            amount = order.quantity / contract_size if contract_size else order.quantity
            if order.reduce_only:
                params["reduceOnly"] = True

        try:
            resp = await ex.create_order(
                symbol=ccxt_symbol,
                type=order.order_type,
                side=order.side,
                amount=amount,
                price=order.price if order.order_type == "limit" else None,
                params=params,
            )
        except ccxt.BaseError as e:
            logger.error("OKX order failed: %s", e)
            raise RuntimeError(f"OKX: {e}") from e

        status = self._map_status(resp.get("status", ""))
        order_id = resp.get("id", "")

        # OKX market orders fill instantly but CCXT create_order may return
        # status "open" before the exchange updates. Poll once to confirm fill;
        # adopt the refreshed order unconditionally so we also capture a partial
        # fill's price/qty detail (not just the fully-filled case).
        if status != "filled" and order.order_type == "market" and order_id:
            import asyncio as _aio
            await _aio.sleep(0.3)
            try:
                fetched = await ex.fetch_order(order_id, ccxt_symbol)
                status = self._map_status(fetched.get("status", status))
                resp = fetched
            except Exception as e:
                logger.debug("fetch_order after market order failed: %s", e)

        # Detect a partial fill: CCXT reports it as status "open" with a non-zero
        # `filled`, which _map_status flattens to "submitted" — the broker would then
        # treat a real partial position as unfilled and never record it. Bitget's
        # client already emits "partially_filled"; mirror it here.
        if status != "filled" and float(resp.get("filled") or 0) > 0:
            status = "partially_filled"

        fill_price = None
        filled_qty = None
        if status in ("filled", "partially_filled"):
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

    async def place_algo_order(
        self,
        symbol: str,
        market_type: str,
        close_side: str,
        quantity: float,
        stop_price: float | None = None,
        take_profit_price: float | None = None,
    ) -> dict:
        """Place reduce-only protective trigger orders (SL and/or TP) for an EXISTING position.

        Unlike place_order (which opens), this places standalone conditional/algo
        orders that close the position when a trigger price is hit — the OKX
        equivalent of Bitget's place_tpsl_order. `close_side` is the side that
        REDUCES the position (sell for a long, buy for a short). `quantity` is in
        COIN units; for perps it is converted to contracts via contractSize, same
        as place_order. SL and TP are placed as two separate reduce-only trigger
        orders (mirrors bitget_broker._try_place_tpsl). Returns {"sl": id, "tp": id}
        for whichever legs were placed. Non-fatal per leg — a failed leg is logged
        and omitted, never raised, so a partial bracket still protects.

        Perp only: OKX spot has no reduce-only / position concept, so protective
        trigger orders are not placed for spot (caller must not rely on them).
        """
        if is_leveraged_market(market_type) is False:
            logger.info("OKX spot has no reduce-only protective orders — skipping for %s", symbol)
            return {}

        ccxt_symbol = okx_ccxt_symbol(market_type, symbol)
        ex = self._ensure_exchange()
        contract_size = await self._contract_size(ccxt_symbol)
        amount = quantity / contract_size if contract_size else quantity

        result: dict = {}
        # Two separate reduce-only market-trigger orders. CCXT-okx maps the unified
        # stopLossPrice / takeProfitPrice params to OKX algo (slTriggerPx/tpTriggerPx)
        # orders with market execution.
        legs = []
        if stop_price:
            legs.append(("sl", {"reduceOnly": True, "stopLossPrice": float(stop_price)}))
        if take_profit_price:
            legs.append(("tp", {"reduceOnly": True, "takeProfitPrice": float(take_profit_price)}))

        for leg, params in legs:
            try:
                resp = await ex.create_order(
                    symbol=ccxt_symbol, type="market", side=close_side,
                    amount=amount, price=None, params=params,
                )
                oid = resp.get("id") or ""
                if oid:
                    result[leg] = oid
            except ccxt.BaseError as e:
                logger.warning("OKX %s trigger placement failed for %s: %s", leg, symbol, e)
            except Exception as e:  # noqa: BLE001 — protective placement must never raise
                logger.warning("OKX %s trigger placement error for %s: %s", leg, symbol, e)
        return result

    async def cancel_algo_order(self, symbol: str, market_type: str, algo_id: str) -> None:
        """Cancel a pending algo/trigger order. Non-fatal on failure."""
        ccxt_symbol = okx_ccxt_symbol(market_type, symbol)
        try:
            ex = self._ensure_exchange()
            await ex.cancel_order(algo_id, ccxt_symbol, params={"trigger": True})
        except Exception as e:  # noqa: BLE001
            logger.warning("OKX cancel algo %s (%s) failed: %s", algo_id, symbol, e)

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
    async def load_demo_markets() -> dict:
        """Load supported symbols on OKX demo (sandbox) without credentials.

        Returns {"swap": frozenset, "spot": frozenset} of canonical symbols.
        Keeping the sets separate lets the pre-flight check be market-type-aware:
        a symbol available only as spot (e.g. WLD) must not pass a perp order.
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
            swap_symbols: set = set()
            spot_symbols: set = set()

            for key, info in exchange.markets.items():
                if not info.get("active", True):
                    continue
                mtype = info.get("type")
                if mtype not in ("swap", "spot"):
                    continue
                quote = (info.get("quote") or "").upper()
                if quote != "USDT":
                    continue
                canonical = to_canonical(info.get("base", "") + "/USDT")
                if mtype == "swap":
                    swap_symbols.add(canonical)
                else:
                    spot_symbols.add(canonical)
            return {"swap": frozenset(swap_symbols), "spot": frozenset(spot_symbols)}
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
