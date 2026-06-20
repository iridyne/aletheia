"""Bitget API client — ExchangeAdapter implementation."""

from dataclasses import dataclass
from datetime import datetime

from aletheia.net import httpx_client
from aletheia.exchanges.bitget.auth import build_headers
from aletheia.config.secrets import get_secret, redact


BASE_URL = "https://api.bitget.com"
DEMO_BASE_URL = "https://api.bitget.com"  # Same URL, demo distinguished by header


@dataclass
class Ticker:
    symbol: str
    last_price: float
    volume_24h: float
    change_pct_24h: float


@dataclass
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Balance:
    currency: str
    available: float
    frozen: float


@dataclass
class OrderRequest:
    symbol: str
    market_type: str  # spot | usdt_futures
    side: str  # buy | sell
    order_type: str  # market | limit
    quantity: float
    price: float | None = None
    client_oid: str | None = None  # idempotency key — dedups retried submissions
    # Hedge-mode close (USDT-futures only). The demo/live account runs in hedge
    # ("two-way") mode, where closing is NOT `reduceOnly` — it is `tradeSide:"close"`
    # plus `holdSide` (the side of the position being reduced), with `side` SAME as
    # the open side (close a long = side:"buy", holdSide:"long"). When hold_side is
    # set the mix order is built as a close; otherwise it is an open (tradeSide:"open").
    reduce_only: bool = False
    hold_side: str | None = None  # "long" | "short" — required for a hedge-mode close


@dataclass
class OrderResponse:
    order_id: str
    client_oid: str | None = None
    status: str = "submitted"
    # Real fill data resolved from the exchange (mix market orders fill synchronously;
    # populated by a follow-up order/detail query). None when the exchange has not
    # confirmed a fill yet — callers must NOT fabricate a fill from request values.
    fill_price: float | None = None
    filled_quantity: float | None = None


@dataclass
class AccountSummary:
    user_id: str
    status: str
    raw: dict | None = None


@dataclass
class Position:
    symbol: str
    side: str  # long | short
    quantity: float
    entry_price: float
    unrealized_pnl: float = 0.0
    leverage: int | None = None
    mark_price: float = 0.0  # current exchange mark price (for honest valuation)


class BitgetClient:
    """Native Bitget adapter supporting spot + USDT futures, demo + live."""

    def __init__(self, mode: str = "demo"):
        self.mode = mode
        self.demo = mode == "demo"
        self._base = BASE_URL

    def _cred_prefix(self) -> str:
        return f"bitget.{self.mode}"

    def _get_creds(self) -> tuple[str, str, str]:
        prefix = self._cred_prefix()
        api_key = get_secret(f"{prefix}.api_key")
        api_secret = get_secret(f"{prefix}.api_secret")
        passphrase = get_secret(f"{prefix}.passphrase")
        if not all([api_key, api_secret, passphrase]):
            raise RuntimeError(f"Bitget {self.mode} credentials not configured. Use: aletheia secrets set {prefix}.api_key")
        return api_key, api_secret, passphrase

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        api_key, secret, passphrase = self._get_creds()
        return build_headers(api_key, secret, passphrase, method, path, body, demo=self.demo)

    async def test_connection(self) -> dict:
        """Test API connectivity."""
        path = "/api/v2/spot/account/info"
        try:
            headers = self._headers("GET", path)
            async with httpx_client(timeout=15) as client:
                resp = await client.get(f"{self._base}{path}", headers=headers)
                data = resp.json()
                if data.get("code") == "00000":
                    return {"connected": True, "mode": self.mode}
                return {"connected": False, "error": data.get("msg", "unknown"), "mode": self.mode}
        except RuntimeError as e:
            return {"connected": False, "error": str(e), "mode": self.mode}
        except Exception as e:
            return {"connected": False, "error": str(e), "mode": self.mode}

    async def get_ticker(self, symbol: str, market_type: str = "spot") -> Ticker:
        """Get current ticker for a symbol.

        # execution-context only — use aletheia.data.ccxt_provider for market data.
        # Valid callers: exchange_sync (account valuation), reconciliation.
        """
        if market_type == "spot":
            path = f"/api/v2/spot/market/tickers?symbol={symbol}"
        else:
            path = f"/api/v2/mix/market/ticker?symbol={symbol}&productType=USDT-FUTURES"

        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}")
            data = resp.json()

        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget API error: {data.get('msg')}")

        tickers = data.get("data", [])
        if isinstance(tickers, list) and tickers:
            t = tickers[0]
        elif isinstance(tickers, dict):
            t = tickers
        else:
            raise RuntimeError(f"No ticker data for {symbol}")

        return Ticker(
            symbol=symbol,
            last_price=float(t.get("lastPr", t.get("last", 0))),
            volume_24h=float(t.get("baseVolume", t.get("vol24h", 0))),
            change_pct_24h=float(t.get("change24h", t.get("chgUtc", 0))),
        )

    async def get_ohlcv(self, symbol: str, timeframe: str = "1H", limit: int = 100, market_type: str = "spot") -> list[Candle]:
        """Get OHLCV candles.

        # execution-context only — use aletheia.data.ccxt_provider for market data.
        """
        if market_type == "spot":
            path = f"/api/v2/spot/market/candles?symbol={symbol}&granularity={timeframe}&limit={limit}"
        else:
            path = f"/api/v2/mix/market/candles?symbol={symbol}&productType=USDT-FUTURES&granularity={timeframe}&limit={limit}"

        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}")
            data = resp.json()

        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget API error: {data.get('msg')}")

        candles = []
        for c in data.get("data", []):
            candles.append(Candle(
                timestamp=int(c[0]),
                open=float(c[1]),
                high=float(c[2]),
                low=float(c[3]),
                close=float(c[4]),
                volume=float(c[5]),
            ))
        return candles

    async def get_balances(self) -> list[Balance]:
        """Get account balances."""
        path = "/api/v2/spot/account/assets"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget API error: {data.get('msg')}")
        return [
            Balance(
                currency=b.get("coin", ""),
                available=float(b.get("available", 0)),
                frozen=float(b.get("frozen", 0)),
            )
            for b in data.get("data", [])
        ]

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        """Place an order — gated by execution mode externally."""
        import json

        if order.market_type == "spot":
            path = "/api/v2/spot/trade/place-order"
            body_data = {
                "symbol": order.symbol,
                "side": order.side,
                "orderType": order.order_type,
                "size": str(order.quantity),
                "force": "GTC",
            }
        else:
            path = "/api/v2/mix/order/place-order"
            # Mix v2 requires marginCoin, and (in hedge mode) tradeSide. Determined
            # empirically against the live demo account — see DECISIONS.md 2026-06-09:
            #   open  → tradeSide:"open"  + side:buy|sell
            #   close → tradeSide:"close" + holdSide:long|short, side SAME as the open
            #           side (close a long = side:"buy"). reduceOnly is NOT the
            #           hedge-mode close mechanism; tradeSide+holdSide is.
            body_data = {
                "symbol": order.symbol,
                "productType": "USDT-FUTURES",
                "marginMode": "crossed",
                "marginCoin": "USDT",
                "side": order.side,
                "orderType": order.order_type,
                "size": str(order.quantity),
            }
            if order.hold_side:
                body_data["tradeSide"] = "close"
                body_data["holdSide"] = order.hold_side
            else:
                body_data["tradeSide"] = "open"

        if order.price and order.order_type == "limit":
            body_data["price"] = str(order.price)

        # Idempotency: a stable clientOid lets the exchange dedup a retried submission
        # (e.g. after a transient timeout where the order may already have landed).
        if order.client_oid:
            body_data["clientOid"] = str(order.client_oid)

        body = json.dumps(body_data)
        headers = self._headers("POST", path, body)

        async with httpx_client(timeout=15) as client:
            resp = await client.post(f"{self._base}{path}", content=body, headers=headers)
            data = resp.json()

        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget order error: {data.get('msg')}")

        order_id = data.get("data", {}).get("orderId", "")
        if not order_id:
            raise RuntimeError("Bitget returned success but no orderId in response")

        resp_obj = OrderResponse(
            order_id=order_id,
            client_oid=data.get("data", {}).get("clientOid"),
        )

        # Resolve the real fill status. Mix market orders fill synchronously, so a
        # follow-up detail query returns state=filled + priceAvg immediately (verified
        # against demo — DECISIONS.md 2026-06-09). We NEVER infer a fill from the
        # request: if the detail query is unavailable or the order isn't filled yet,
        # status stays "submitted" and reconciliation/operator resolves it later.
        if order.market_type != "spot":
            try:
                detail = await self._get_order_detail(order_id, order.symbol)
            except Exception:
                detail = None
            if detail:
                self._apply_fill_detail(resp_obj, detail)

        return resp_obj

    @staticmethod
    def _apply_fill_detail(resp: OrderResponse, detail: dict) -> None:
        """Map a mix order/detail payload onto an OrderResponse. Only marks a fill
        when the exchange reports one — never fabricates priceAvg/volume."""
        state = (detail.get("state") or detail.get("status") or "").lower()
        # Bitget mix states: live | partially_filled | filled | cancelled | ...
        if state in ("filled", "full_fill", "fullfill"):
            resp.status = "filled"
        elif state in ("partially_filled", "partial_fill", "partialfill"):
            resp.status = "partially_filled"
        else:
            return  # not filled yet — leave status as submitted, no fill data
        price_avg = detail.get("priceAvg") or detail.get("priceAvgFilled") or detail.get("fillPrice")
        base_vol = detail.get("baseVolume") or detail.get("accBaseVolume") or detail.get("filledQty")
        try:
            resp.fill_price = float(price_avg) if price_avg not in (None, "", "0") else None
        except (TypeError, ValueError):
            resp.fill_price = None
        try:
            resp.filled_quantity = float(base_vol) if base_vol not in (None, "") else None
        except (TypeError, ValueError):
            resp.filled_quantity = None

    async def _get_order_detail(self, order_id: str, symbol: str) -> dict | None:
        """Fetch a single mix order's detail (fill state + priceAvg)."""
        path = f"/api/v2/mix/order/detail?orderId={order_id}&symbol={symbol}&productType=USDT-FUTURES"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            return None
        d = data.get("data")
        if isinstance(d, list):
            return d[0] if d else None
        return d if isinstance(d, dict) else None

    async def get_account(self) -> AccountSummary:
        """Get account summary (§19.3)."""
        path = "/api/v2/spot/account/info"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget API error: {data.get('msg')}")
        d = data.get("data", {}) or {}
        return AccountSummary(
            user_id=str(d.get("userId", "")),
            status=d.get("accountStatus", "unknown"),
            raw=d,
        )

    async def get_positions(self) -> list[Position]:
        """Get open USDT-futures positions (§19.3)."""
        path = "/api/v2/mix/position/all-position?productType=USDT-FUTURES&marginCoin=USDT"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget API error: {data.get('msg')}")
        positions = []
        for p in data.get("data", []) or []:
            lev = p.get("leverage")
            # Bitget v2 mix all-position reports the average open price under
            # `openPriceAvg`. The pre-v2 field name `averageOpenPrice` does NOT
            # exist in this response, so the old mapping silently produced 0.0 —
            # kept as a defensive fallback only.
            entry = p.get("openPriceAvg") or p.get("averageOpenPrice") or 0
            positions.append(Position(
                symbol=p.get("symbol", ""),
                side=p.get("holdSide", ""),
                quantity=float(p.get("total", 0) or 0),
                entry_price=float(entry or 0),
                unrealized_pnl=float(p.get("unrealizedPL", 0) or 0),
                leverage=int(lev) if lev not in (None, "") else None,
                mark_price=float(p.get("markPrice", 0) or 0),
            ))
        return positions

    async def get_mix_account(self, product_type: str = "USDT-FUTURES") -> dict:
        """Get futures/mix account summary (equity, available, unrealized PnL).

        For Bitget demo accounts this is the only endpoint that returns real
        balance — spot/account/assets returns empty for demo keys.
        """
        path = f"/api/v2/mix/account/accounts?productType={product_type}"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget API error: {data.get('msg')}")
        accounts = data.get("data", []) or []
        if not accounts:
            return {}
        return accounts[0]

    async def get_open_orders(self, market_type: str = "spot") -> list[dict]:
        """Get unfilled (open) orders (§19.3)."""
        if market_type == "spot":
            path = "/api/v2/spot/trade/unfilled-orders"
        else:
            path = "/api/v2/mix/order/orders-pending?productType=USDT-FUTURES"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget API error: {data.get('msg')}")
        return data.get("data", []) or []

    async def cancel_order(self, order_id: str, symbol: str, market_type: str = "spot") -> dict:
        """Cancel an order (§19.3). Returns {cancelled, order_id, [error]}."""
        import json as _json
        if market_type == "spot":
            path = "/api/v2/spot/trade/cancel-order"
            body_data = {"symbol": symbol, "orderId": order_id}
        else:
            path = "/api/v2/mix/order/cancel-order"
            body_data = {"symbol": symbol, "orderId": order_id, "productType": "USDT-FUTURES"}
        body = _json.dumps(body_data)
        headers = self._headers("POST", path, body)
        async with httpx_client(timeout=15) as client:
            resp = await client.post(f"{self._base}{path}", content=body, headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            return {"cancelled": False, "order_id": order_id, "error": data.get("msg", "unknown")}
        return {"cancelled": True, "order_id": order_id}

    async def get_fills(self, symbol: str | None = None, market_type: str = "spot") -> list[dict]:
        """Get trade fills (§19.3)."""
        if market_type == "spot":
            path = "/api/v2/spot/trade/fills"
        else:
            path = "/api/v2/mix/order/fills?productType=USDT-FUTURES"
        if symbol:
            path += ("&" if "?" in path else "?") + f"symbol={symbol}"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget API error: {data.get('msg')}")
        return data.get("data", []) or []

    # ─── TPSL Trigger Orders ─────────────────────────────────────────────────

    async def place_tpsl_order(
        self,
        symbol: str,
        plan_type: str,
        trigger_price: float,
        hold_side: str,
        size: float,
        execute_price: float | None = None,
        client_oid: str | None = None,
    ) -> str:
        """Place a TP/SL trigger order attached to a position.

        plan_type: "pos_loss" (stop-loss) or "pos_profit" (take-profit).
        Returns the exchange plan order ID.
        """
        import json as _json
        path = "/api/v2/mix/order/place-tpsl-order"
        body_data: dict = {
            "symbol": symbol,
            "productType": "USDT-FUTURES",
            "marginMode": "crossed",
            "marginCoin": "USDT",
            "planType": plan_type,
            "triggerPrice": str(trigger_price),
            "triggerType": "fill_price",
            "holdSide": hold_side,
            "size": str(size),
        }
        if execute_price is not None:
            body_data["executePrice"] = str(execute_price)
        if client_oid:
            body_data["clientOid"] = client_oid
        body = _json.dumps(body_data)
        headers = self._headers("POST", path, body)
        async with httpx_client(timeout=15) as client:
            resp = await client.post(f"{self._base}{path}", content=body, headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget place_tpsl_order error: {data.get('msg')}")
        return data.get("data", {}).get("orderId", "")

    async def cancel_plan_order(self, symbol: str, order_id: str) -> dict:
        """Cancel a pending TP/SL trigger order. Returns {cancelled, order_id}."""
        import json as _json
        path = "/api/v2/mix/order/cancel-plan-order"
        body_data = {
            "symbol": symbol,
            "productType": "USDT-FUTURES",
            "orderIdList": [{"orderId": order_id}],
        }
        body = _json.dumps(body_data)
        headers = self._headers("POST", path, body)
        async with httpx_client(timeout=15) as client:
            resp = await client.post(f"{self._base}{path}", content=body, headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            return {"cancelled": False, "order_id": order_id, "error": data.get("msg", "unknown")}
        return {"cancelled": True, "order_id": order_id}

    async def get_pending_plan_orders(self, symbol: str | None = None) -> list[dict]:
        """List pending TP/SL trigger orders for USDT-FUTURES."""
        path = "/api/v2/mix/order/orders-plan-pending?productType=USDT-FUTURES"
        if symbol:
            path += f"&symbol={symbol}"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget API error: {data.get('msg')}")
        return data.get("data", {}).get("entrustedList", []) or []

    async def get_plan_order_history(self, symbol: str | None = None, order_id: str | None = None) -> list[dict]:
        """Query triggered/filled TPSL order history for USDT-FUTURES.

        Returns list of historical plan orders (triggered, filled, cancelled).
        Each dict contains at minimum: orderId, planType, status, triggerPrice,
        executePrice (the actual fill price when triggered).
        """
        path = "/api/v2/mix/order/orders-plan-history?productType=USDT-FUTURES"
        if symbol:
            path += f"&symbol={symbol}"
        if order_id:
            path += f"&orderId={order_id}"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget API error: {data.get('msg')}")
        return data.get("data", {}).get("entrustedList", []) or []
