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
from aletheia.data.symbols import to_canonical, to_ccxt_perp

logger = logging.getLogger("aletheia.binance_client")


def _binance_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _binance_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    stop_price: float | None = None  # carried for audit; Binance uses separate orders for TPSL
    take_profit_price: float | None = None


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
                    "position_id": str(
                        p.get("id")
                        or (p.get("info") or {}).get("positionId")
                        or ""
                    ) or None,
                    "quantity": qty,
                    "entry_price": float(p.get("entryPrice", 0) or 0),
                    "unrealized_pnl": float(p.get("unrealizedPnl", 0) or 0),
                    "mark_price": float(p.get("markPrice", 0) or 0),
                })
            return result
        except ccxt.BaseError as e:
            logger.error("Binance %s get_positions failed: %s", self._mode, e)
            raise RuntimeError(f"Binance {self._mode} positions: {e}") from e

    async def get_open_orders(self) -> list[dict]:
        """Currently-open orders for CR-EX-01 reconciliation. Each dict carries the
        exchange order id under 'id'. CCXT fetch_open_orders() with no symbol returns
        all open orders on Binance USDM futures; demo vs live is chosen by mode.

        NOTE (needs-demo confirmation): implemented from CCXT + Binance docs and covered
        by mock tests; confirm the response shape once against a Binance testnet account
        (capture as a fixture) — see PARTIAL_REDUCE_RESEARCH.md.
        """
        try:
            orders = await self._ensure_exchange().fetch_open_orders()
            return [
                {"id": str(o.get("id")), "symbol": o.get("symbol"), "status": o.get("status")}
                for o in (orders or [])
                if o.get("id")
            ]
        except ccxt.BaseError as e:
            logger.error("Binance %s get_open_orders failed: %s", self._mode, e)
            raise RuntimeError(f"Binance {self._mode} open_orders: {e}") from e

    async def get_funding_ledger(
        self,
        *,
        broker_id: str,
        symbol: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> dict:
        """Return complete signed USD-M funding income for one account window."""
        from aletheia.execution.broker_funding import (
            make_complete_ledger,
            normalize_binance_funding_rows,
            require_retained_history,
        )

        expected_mode = (
            "binance_testnet" if self._mode == "testnet"
            else "binance_live_limited"
        )
        if broker_id != expected_mode:
            raise ValueError("Binance funding account identity mismatch")
        start_ms = int(start_time_ms)
        end_ms = int(end_time_ms)
        if start_ms <= 0 or end_ms < start_ms:
            raise ValueError("Binance funding history window is invalid")
        require_retained_history(
            start_ms,
            retention_days=90,
            source="Binance income history",
        )

        exchange = self._ensure_exchange()
        exchange_symbol = to_canonical(symbol).replace("/", "")
        max_window_ms = 7 * 24 * 60 * 60 * 1000
        raw_rows: list[dict] = []
        chunk_start = start_ms
        while chunk_start <= end_ms:
            chunk_end = min(end_ms, chunk_start + max_window_ms - 1)
            page_number = 1
            for _ in range(100):
                params = {
                    "symbol": exchange_symbol,
                    "incomeType": "FUNDING_FEE",
                    "startTime": chunk_start,
                    "endTime": chunk_end,
                    "limit": 1000,
                    "page": page_number,
                }
                page = await exchange.fapiPrivateGetIncome(params)
                if not isinstance(page, list):
                    raise ValueError("Binance private funding history malformed")
                raw_rows.extend(row for row in page if isinstance(row, dict))
                if len(page) < 1000:
                    break
                page_number += 1
            else:
                raise RuntimeError("Binance private funding history incomplete")
            chunk_start = chunk_end + 1

        events = [
            event for event in normalize_binance_funding_rows(raw_rows)
            if event["symbol"] == to_canonical(symbol)
        ]
        return make_complete_ledger(
            broker_id=broker_id,
            account_environment=self._mode,
            source_endpoint="/fapi/v1/income",
            history_start_ms=start_ms,
            history_end_ms=end_ms,
            events=events,
        )

    async def get_verified_position_exit(self, request):
        """Verify one disappeared position from direct state and private trades."""
        from aletheia.data.symbols import to_canonical
        from aletheia.execution.verified_exit import (
            VerifiedExitFill,
            classify_verified_exit,
            inconclusive_verified_exit,
            private_history_window_available,
        )

        expected_mode = (
            "binance_testnet" if self._mode == "testnet" else "binance_live_limited"
        )
        if (
            request.broker_id != expected_mode
            or request.execution_mode != expected_mode
            or request.account_environment != self._mode
        ):
            return inconclusive_verified_exit(request, "account_identity_mismatch")
        if request.market_type != "usdt_futures":
            return inconclusive_verified_exit(request, "unsupported_market_type")
        if not private_history_window_available(request):
            return inconclusive_verified_exit(request, "history_window_unavailable")

        endpoint = "/fapi/v1/userTrades"
        try:
            positions = await self.get_positions()
            present = any(
                to_canonical(str(position.get("symbol") or "")) == request.symbol
                and str(position.get("side") or "").lower() == request.direction
                and float(position.get("quantity") or 0) > 0
                for position in positions
            )
            if present:
                return classify_verified_exit(
                    request, position_absent=False, history_complete=False,
                    close_fills=(), ambiguous_activity=False, pages=0,
                    source_endpoint=endpoint,
                )

            exchange = self._ensure_exchange()
            ccxt_symbol = to_ccxt_perp(request.symbol)
            raw_fills: list[dict] = []
            pages = 0
            complete = False
            from_id = None
            for _ in range(100):
                params = {"endTime": request.history_end_ms}
                since = request.history_start_ms if from_id is None else None
                if from_id is not None:
                    params["fromId"] = from_id
                page = await exchange.fetch_my_trades(
                    ccxt_symbol, since=since, limit=1000, params=params,
                )
                if not isinstance(page, list):
                    raise ValueError("malformed private trade history")
                pages += 1
                raw_fills.extend(page)
                if len(page) < 1000:
                    complete = True
                    break
                last_id = str(page[-1].get("id") or "")
                if not last_id.isdigit():
                    break
                next_id = str(int(last_id) + 1)
                if next_id == from_id:
                    break
                from_id = next_id

            close_fills: list[VerifiedExitFill] = []
            ambiguous = False
            expected_side = "sell" if request.direction == "long" else "buy"
            known_entry_order_ids = set(request.known_entry_order_ids)
            for raw in raw_fills:
                timestamp = _binance_int(raw.get("timestamp"))
                if not request.history_start_ms <= timestamp <= request.history_end_ms:
                    continue
                if to_canonical(str(raw.get("symbol") or "")) != request.symbol:
                    ambiguous = True
                    continue
                info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
                position_side = str(info.get("positionSide") or "").lower()
                side = str(raw.get("side") or info.get("side") or "").lower()
                closes_direction = side == expected_side and position_side in {
                    request.direction, "both",
                }
                remote_order_id = str(
                    raw.get("order") or info.get("orderId") or ""
                ).strip()
                if remote_order_id in known_entry_order_ids and not closes_direction:
                    continue
                if not closes_direction:
                    ambiguous = True
                    continue
                fill_id = str(raw.get("id") or info.get("id") or "")
                price = _binance_float(raw.get("price") or info.get("price"))
                quantity = _binance_float(raw.get("amount") or info.get("qty"))
                if not fill_id or not price or not quantity or timestamp < 1:
                    ambiguous = True
                    continue
                fee_data = raw.get("fee") if isinstance(raw.get("fee"), dict) else {}
                fee = _binance_float(fee_data.get("cost") or info.get("commission"))
                pnl = _binance_float(info.get("realizedPnl"))
                close_fills.append(VerifiedExitFill(
                    fill_id=fill_id,
                    order_id=str(raw.get("order") or info.get("orderId") or "") or None,
                    price=price,
                    quantity=quantity,
                    fee=abs(fee) if fee is not None else None,
                    fee_currency=str(
                        fee_data.get("currency") or info.get("commissionAsset") or ""
                    ) or None,
                    realized_pnl=pnl,
                    filled_at_ms=timestamp,
                ))
            return classify_verified_exit(
                request,
                position_absent=True,
                history_complete=complete,
                close_fills=tuple(close_fills),
                ambiguous_activity=ambiguous,
                pages=pages,
                source_endpoint=endpoint,
            )
        except Exception:
            return inconclusive_verified_exit(
                request, "broker_query_failed", source_endpoint=endpoint,
            )

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
