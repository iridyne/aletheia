"""Bitget API client — ExchangeAdapter implementation."""

from dataclasses import dataclass
from datetime import datetime
import logging
import math
from urllib.parse import urlencode

from aletheia.net import httpx_client
from aletheia.exchanges.bitget.auth import build_headers
from aletheia.exchanges.submission_errors import (
    ClosePositionAbsentError,
    SubmissionResponseUnknownError,
)
from aletheia.config.secrets import get_secret, redact
from aletheia.data.symbols import to_canonical

logger = logging.getLogger("aletheia.exchanges.bitget")


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
    # Bitget supports exchange-native protection on the original USDT-futures
    # entry. These are approved absolute prices, never percentages or values
    # derived by this client. Spot and close requests deliberately ignore them.
    preset_stop_loss_price: float | None = None
    preset_take_profit_price: float | None = None
    # Hedge-mode close (USDT-futures only). The demo/live account runs in hedge
    # ("two-way") mode, where closing is NOT `reduceOnly` — it is `tradeSide:"close"`
    # plus `holdSide` (the side of the position being reduced), with `side` SAME as
    # the open side (close a long = side:"buy", holdSide:"long"). When hold_side is
    # set the mix order is built as a close; otherwise it is an open (tradeSide:"open").
    reduce_only: bool = False
    hold_side: str | None = None  # "long" | "short" — required for a hedge-mode close
    leverage: int | None = None   # USDT-futures: set on the exchange before an open


def _validated_preset_protection_price(value: float | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("preset protection price must be finite and positive")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            "preset protection price must be finite and positive"
        ) from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError("preset protection price must be finite and positive")
    return str(value)


class DemoFundingUnavailable(RuntimeError):
    """mix wallet short and cannot be topped up automatically."""




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
    ctime_ms: int | None = None  # exchange-reported position open time


def _bitget_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _bitget_float(value) -> float | None:
    """Parse an exchange-supplied number, rejecting non-finite results.

    Sibling helper ``_validated_preset_protection_price`` already guards with
    ``math.isfinite``; this one did not, so the two disagreed on what counts as
    a usable number. It matters because callers screen values with ``if not
    price`` -- that rejects 0.0 but NOT NaN (``not float("nan")`` is False), so
    a NaN price or quantity flowed straight into fill records and position
    maths. ``inf`` has the same hole. Booleans are excluded too: ``float(True)``
    is 1.0, which would silently become a price of 1.

    Args:
        value: Raw field from a Bitget JSON payload.

    Returns:
        The parsed float, or None when the value is missing, boolean,
        non-numeric, NaN or infinite.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _bitget_fill_closes_direction(raw: dict, direction: str) -> bool:
    trade_side = str(raw.get("tradeSide") or "").lower()
    side = str(raw.get("side") or "").lower()
    if direction in trade_side and (
        "close" in trade_side
        or trade_side.startswith(("reduce_", "burst_", "delivery_", "dte_sys_adl_"))
    ):
        return True
    if trade_side == "close":
        # Bitget hedge-mode fills retain the position's open side.
        return side == ("buy" if direction == "long" else "sell")
    if trade_side.startswith(("reduce_", "burst_", "delivery_", "dte_sys_adl_")):
        return side == ("sell" if direction == "long" else "buy")
    return False


def _bitget_verified_fill(raw: dict):
    from aletheia.execution.verified_exit import VerifiedExitFill

    fill_id = str(raw.get("tradeId") or raw.get("execId") or "")
    price = _bitget_float(raw.get("price") or raw.get("execPrice"))
    quantity = _bitget_float(
        raw.get("baseVolume") or raw.get("execQty") or raw.get("size")
    )
    timestamp = _bitget_int(raw.get("cTime") or raw.get("createdTime"))
    if not fill_id or not price or not quantity or timestamp < 1:
        return None
    details = raw.get("feeDetail") or []
    if isinstance(details, dict):
        details = [details]
    fee_values = []
    fee_currency = None
    for detail in details if isinstance(details, list) else []:
        fee = _bitget_float(detail.get("totalFee") or detail.get("fee"))
        if fee is not None:
            fee_values.append(abs(fee))
        fee_currency = fee_currency or detail.get("feeCoin")
    pnl = _bitget_float(raw.get("profit") or raw.get("execPnl"))
    return VerifiedExitFill(
        fill_id=fill_id,
        order_id=str(raw.get("orderId") or "") or None,
        price=price,
        quantity=quantity,
        fee=sum(fee_values) if fee_values else None,
        fee_currency=str(fee_currency) if fee_currency else None,
        realized_pnl=pnl,
        filled_at_ms=timestamp,
    )


class BitgetClient:
    """Native Bitget adapter supporting spot + USDT futures, demo + live."""

    def __init__(self, mode: str = "demo"):
        self.mode = mode
        self.demo = mode == "demo"
        self._base = BASE_URL
        self._unified = None  # lazy UTA detection

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

        # `t.get("lastPr", t.get("last", 0))` only falls back when the KEY is
        # absent -- Bitget returning `"lastPr": ""` reached the bare float() and
        # raised ValueError. Price is authoritative, so parse it defensively and
        # fail loudly; volume/change are advisory and may degrade to 0.0.
        last_price = _bitget_float(t.get("lastPr"))
        if last_price is None:
            last_price = _bitget_float(t.get("last"))
        if last_price is None or last_price <= 0:
            raise RuntimeError(
                f"Invalid Bitget last price for {symbol}: "
                f"lastPr={t.get('lastPr')!r} last={t.get('last')!r}"
            )
        volume_24h = _bitget_float(t.get("baseVolume"))
        if volume_24h is None:
            volume_24h = _bitget_float(t.get("vol24h"))
        change_pct_24h = _bitget_float(t.get("change24h"))
        if change_pct_24h is None:
            change_pct_24h = _bitget_float(t.get("chgUtc"))

        return Ticker(
            symbol=symbol,
            last_price=last_price,
            volume_24h=volume_24h if volume_24h is not None else 0.0,
            change_pct_24h=change_pct_24h if change_pct_24h is not None else 0.0,
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
        skipped = 0
        for c in data.get("data", []):
            # Indexing c[0]..c[5] blind raised IndexError on any short row the
            # exchange returned; a malformed row should cost one candle, not the
            # whole request.
            if not isinstance(c, (list, tuple)) or len(c) < 6:
                skipped += 1
                continue
            timestamp = _bitget_int(c[0])
            values = [_bitget_float(c[i]) for i in range(1, 6)]
            if timestamp < 1 or any(v is None for v in values):
                skipped += 1
                continue
            open_, high, low, close, volume = values
            candles.append(Candle(
                timestamp=timestamp,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
            ))
        if skipped:
            logger.warning(
                "Bitget %s %s: skipped %d malformed candle row(s)",
                symbol, timeframe, skipped,
            )
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

    async def set_leverage(self, exchange_symbol: str, leverage: int) -> None:
        """Set USDT-futures leverage (crossed margin) for a symbol on Bitget.

        ``exchange_symbol`` is the Bitget order format (e.g. BTCUSDT), matching what
        place_order sends. Fail-closed: any non-success response raises so the broker
        aborts the order rather than opening at the account-default leverage. Params
        per Bitget mix v2 /account/set-leverage — confirm against a live demo account.
        """
        if await self._is_unified():
            return await self._uta_set_leverage(exchange_symbol, leverage)

        import json

        path = "/api/v2/mix/account/set-leverage"
        body_data = {
            "symbol": exchange_symbol,
            "productType": "USDT-FUTURES",
            "marginCoin": "USDT",
            "leverage": str(int(leverage)),
        }
        body = json.dumps(body_data)
        headers = self._headers("POST", path, body)
        async with httpx_client(timeout=15) as client:
            resp = await client.post(f"{self._base}{path}", content=body, headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget set-leverage failed: {data.get('msg')}")

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        """Place an order — gated by execution mode externally."""
        if await self._is_unified():
            return await self._uta_place_order(order)

        import json

        # Validate before leverage setup or order submission so malformed
        # protection cannot leave any exchange-side effect behind. Validation is
        # unconditional when a value is supplied; spot/close isolation below
        # decides only whether a valid value is serialized.
        preset_stop_loss = _validated_preset_protection_price(
            order.preset_stop_loss_price
        )
        preset_take_profit = _validated_preset_protection_price(
            order.preset_take_profit_price
        )

        # Futures open: set the intended leverage on the exchange first (skip on a
        # hedge-mode close, which does not change leverage). Fail-closed — set_leverage
        # raises on failure, aborting the order rather than opening at the wrong size.
        if (order.market_type != "spot" and order.leverage and order.leverage > 1
                and not order.hold_side):
            await self.set_leverage(order.symbol, order.leverage)

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
                if order.market_type == "usdt_futures":
                    if preset_stop_loss is not None:
                        body_data["presetStopLossPrice"] = preset_stop_loss
                    if preset_take_profit is not None:
                        body_data["presetStopSurplusPrice"] = preset_take_profit

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
            status_code = int(getattr(resp, "status_code", 200) or 0)
            if status_code >= 500:
                raise SubmissionResponseUnknownError(
                    "Bitget create-order acknowledgement returned a server error"
                )
            try:
                data = resp.json()
            except (ValueError, UnicodeError) as exc:
                raise SubmissionResponseUnknownError(
                    "Bitget create-order acknowledgement was unreadable"
                ) from exc

        if not isinstance(data, dict):
            raise SubmissionResponseUnknownError(
                "Bitget create-order acknowledgement was not an object"
            )
        response_code = data.get("code")
        if not isinstance(response_code, str) or not response_code.strip():
            raise SubmissionResponseUnknownError(
                "Bitget create-order acknowledgement had no response code"
            )
        if response_code != "00000":
            response_message = data.get("msg")
            if (
                not isinstance(response_message, str)
                or not response_message.strip()
            ):
                raise SubmissionResponseUnknownError(
                    "Bitget create-order acknowledgement was not a recognizable rejection"
                )
            # Bitget documents both codes across its futures account families.
            # Scope this classification to hedge-mode closes: the response proves
            # only that this submission was rejected, not why local exposure and
            # the venue disagree or whether any earlier close filled.
            if order.hold_side and response_code in {"22002", "25227"}:
                raise ClosePositionAbsentError(
                    f"Bitget close rejected: no position available (code={response_code})"
                )
            raise RuntimeError(f"Bitget order error: {response_message}")

        response_data = data.get("data")
        if not isinstance(response_data, dict):
            raise SubmissionResponseUnknownError(
                "Bitget create-order success acknowledgement had invalid data"
            )
        raw_order_id = response_data.get("orderId")
        if not isinstance(raw_order_id, str) or not raw_order_id.strip():
            raise SubmissionResponseUnknownError(
                "Bitget create-order success acknowledgement had no order id"
            )
        order_id = raw_order_id.strip()

        resp_obj = OrderResponse(
            order_id=order_id,
            client_oid=response_data.get("clientOid"),
        )

        # Resolve the real fill status. Mix market orders fill synchronously, so a
        # follow-up detail query returns state=filled + priceAvg immediately (verified
        # against demo — DECISIONS.md 2026-06-09). We NEVER infer a fill from the
        # request: if the detail query is unavailable or the order isn't filled yet,
        # status stays "submitted" and reconciliation/operator resolves it later.
        if order.market_type != "spot":
            try:
                detail = await self._get_order_detail(order_id, order.symbol, order.market_type)
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

    async def _get_order_detail(self, order_id: str, symbol: str, market_type: str = "usdt_futures") -> dict | None:
        """Fetch a single mix order's detail (fill state + priceAvg)."""
        if await self._is_unified():
            return await self._uta_order_detail(order_id, symbol, market_type)
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
        if await self._is_unified():
            return await self._uta_positions()
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
            try:
                ctime_ms = int(p.get("cTime") or p.get("openTime") or 0) or None
            except (TypeError, ValueError):
                ctime_ms = None
            positions.append(Position(
                symbol=p.get("symbol", ""),
                side=p.get("holdSide", ""),
                quantity=float(p.get("total", 0) or 0),
                entry_price=float(entry or 0),
                unrealized_pnl=float(p.get("unrealizedPL", 0) or 0),
                leverage=int(lev) if lev not in (None, "") else None,
                mark_price=float(p.get("markPrice", 0) or 0),
                ctime_ms=ctime_ms,
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


    async def transfer(self, amount: float, coin: str = "USDT") -> dict | None:
        """Move `amount` of `coin` from unified/spot wallet into mix (USDT-FUTURES)."""
        if await self._is_unified():
            # Unified account: no separate mix wallet; margin is in the unified
            # USDT wallet, so the spot->mix transfer is a no-op.
            return None
        import json as _json
        try:
            mix = await self.get_mix_account("USDT-FUTURES")
            available = float(mix.get("available") or 0)
        except Exception:
            available = 0.0
        need = amount - available
        if need <= 1.0:
            return None
        path = "/api/v2/spot/wallet/transfer"
        body_data = {"coin": coin, "amount": f"{need:.2f}", "fromAccount": "spot", "toAccount": "mix_usdt"}
        body = _json.dumps(body_data)
        headers = self._headers("POST", path, body)
        async with httpx_client(timeout=20) as http:
            resp = await http.post(f"{self._base}{path}", content=body, headers=headers)
            data = resp.json()
        code = data.get("code")
        if code == "40404":
            raise DemoFundingUnavailable(
                f"mix wallet short by ${need:.2f}; Bitget transfer endpoint "
                f"unavailable on this key (40404). Seed the demo futures wallet manually."
            )
        if code != "00000":
            raise DemoFundingUnavailable(f"Bitget transfer failed: {data.get('msg')} (code {code})")
        return data.get("data", {})

    async def get_uta_account(self) -> dict:
        """Get UTA (Unified Trading Account) account assets — v3 API.

        Returns the full UTA account with all assets. Only works for unified
        accounts; classic accounts return an error (code != 00000).
        """
        path = "/api/v3/account/assets"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget UTA API error: {data.get('msg')}")
        return data.get("data", {}) or {}

    # ------------------------------------------------------------------
    # UTA (Unified Trading Account) support
    #
    # Bitget unified accounts have NO separate "mix" (contracts) wallet:
    # margin is drawn from the unified USDT wallet. The classic v2 mix
    # endpoints read an (empty) legacy mix wallet and the spot->mix transfer
    # endpoint is unsupported (40404), so they cannot be used for UTA. We
    # detect UTA once per client and route the affected calls to the v3
    # unified endpoints. Classic accounts keep the original v2 behaviour.
    # ------------------------------------------------------------------
    async def _is_unified(self) -> bool:
        if self._unified is None:
            try:
                data = await self.get_uta_account()
                assets = (data or {}).get("assets") or [] if isinstance(data, dict) else []
                self._unified = any(float(a.get("available") or 0) > 0 for a in assets)
            except Exception:
                self._unified = False
        return self._unified

    async def get_account_balance(self) -> dict:
        """Normalized USDT-Futures margin balance, UTA-aware.

        Returns {available, equity, unrealized_pnl, unified}. For UTA the
        available USDT of the unified wallet is the margin source.
        """
        if await self._is_unified():
            data = await self.get_uta_account()
            assets = (data or {}).get("assets") or [] if isinstance(data, dict) else []
            usdt = next((a for a in assets if a.get("coin") == "USDT"), None)
            avail = float(usdt.get("available") or 0) if usdt else 0.0
            equity = float(data.get("usdtEquity") or avail) if isinstance(data, dict) else avail
            return {"available": avail, "equity": equity, "unrealized_pnl": 0.0, "unified": True}
        mix = await self.get_mix_account("USDT-FUTURES")
        return {
            "available": float(mix.get("available") or 0) if mix else 0.0,
            "equity": float(mix.get("accountEquity") or 0) if mix else 0.0,
            "unrealized_pnl": float(mix.get("unrealizedPL") or 0) if mix else 0.0,
            "unified": False,
        }

    async def _uta_positions(self) -> list:
        path = "/api/v3/position/current-position?category=USDT-FUTURES"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget UTA API error: {data.get('msg')}")
        positions = []
        for p in (data.get("data") or {}).get("list") or []:
            positions.append(Position(
                symbol=p.get("symbol", ""),
                side=p.get("posSide", ""),
                quantity=float(p.get("total") or 0),
                entry_price=float(p.get("avgPrice") or 0),
                unrealized_pnl=float(p.get("unrealisedPnl") or 0),
                leverage=int(p.get("leverage") or 0) or None,
                mark_price=float(p.get("markPrice") or 0),
            ))
        return positions

    async def _uta_place_order(self, order: OrderRequest) -> OrderResponse:
        import json as _json
        preset_stop_loss = _validated_preset_protection_price(order.preset_stop_loss_price)
        preset_take_profit = _validated_preset_protection_price(order.preset_take_profit_price)
        if (order.market_type != "spot" and order.leverage and order.leverage > 1
                and not order.hold_side):
            await self.set_leverage(order.symbol, order.leverage)
        if order.market_type == "spot":
            category = "spot"
            extra = {}
        else:
            category = "USDT-FUTURES"
            extra = {"marginCoin": "USDT", "leverage": str(int(order.leverage)) if (order.leverage and order.leverage > 1) else "1", "marginMode": "crossed"}
        body_data: dict = {
            "category": category,
            "symbol": order.symbol,
            "side": order.side,
            "orderType": order.order_type,
            "qty": str(order.quantity),
            "timeInForce": "gtc",
        }
        body_data.update(extra)
        if order.market_type != "spot":
            if order.hold_side:
                body_data["posSide"] = order.hold_side
                # UTA v3 API 需要 tradeSide="close" 来标识平仓操作。
                # 缺失时默认是 "open"，导致出场订单变成开新仓（bug 根因）。
                body_data["tradeSide"] = "close"
                # UTA (v3) close requires `side` OPPOSITE the position (close
                # long = sell, close short = buy) — verified empirically
                # 2026-07-31 on bitget_demo: side SAME as the position +
                # tradeSide="close" was resolved by the exchange as an OPEN
                # (tradeSide=open_short/open_long), doubling the position
                # instead of closing it. The non-UTA v2 mix path keeps side
                # SAME as the open side (DECISIONS.md 2026-06-09).
                body_data["side"] = "sell" if order.hold_side == "long" else "buy"
            else:
                body_data["posSide"] = "long" if order.side == "buy" else "short"
        if order.order_type == "limit" and order.price:
            body_data["price"] = str(order.price)
        if order.client_oid:
            body_data["clientOid"] = str(order.client_oid)
        if preset_stop_loss is not None:
            body_data["presetStopLossPrice"] = preset_stop_loss
        if preset_take_profit is not None:
            body_data["presetTakeProfitPrice"] = preset_take_profit
        body = _json.dumps(body_data)
        headers = self._headers("POST", "/api/v3/trade/place-order", body)
        async with httpx_client(timeout=15) as client:
            resp = await client.post(f"{self._base}/api/v3/trade/place-order", content=body, headers=headers)
            status_code = int(getattr(resp, "status_code", 200) or 0)
            if status_code >= 500:
                raise SubmissionResponseUnknownError("Bitget UTA create-order returned a server error")
            try:
                data = resp.json()
            except (ValueError, UnicodeError) as exc:
                raise SubmissionResponseUnknownError("Bitget UTA create-order acknowledgement was unreadable") from exc
        if not isinstance(data, dict):
            raise SubmissionResponseUnknownError("Bitget UTA create-order acknowledgement was not an object")
        response_code = data.get("code")
        if not isinstance(response_code, str) or not response_code.strip():
            raise SubmissionResponseUnknownError("Bitget UTA create-order acknowledgement had no response code")
        if response_code != "00000":
            response_message = data.get("msg")
            if not isinstance(response_message, str) or not response_message.strip():
                raise SubmissionResponseUnknownError("Bitget UTA create-order acknowledgement was not a recognizable rejection")
            raise RuntimeError(f"Bitget order error: {response_message}")
        response_data = data.get("data")
        if not isinstance(response_data, dict):
            raise SubmissionResponseUnknownError("Bitget UTA create-order success acknowledgement had invalid data")
        raw_order_id = response_data.get("orderId")
        if not isinstance(raw_order_id, str) or not raw_order_id.strip():
            raise SubmissionResponseUnknownError("Bitget UTA create-order success acknowledgement had no order id")
        order_id = raw_order_id.strip()
        resp_obj = OrderResponse(order_id=order_id, client_oid=response_data.get("clientOid"))
        if order.market_type != "spot":
            try:
                detail = await self._uta_order_detail(order_id, order.symbol)
            except Exception:
                detail = None
            if detail:
                self._apply_fill_detail(resp_obj, detail)
        return resp_obj

    async def _uta_cancel_order(self, order_id, symbol, market_type):
        import json as _json
        body = _json.dumps({"orderId": order_id, "symbol": symbol, "category": "USDT-FUTURES", "marginCoin": "USDT"})
        path = "/api/v3/trade/cancel-order"
        headers = self._headers("POST", path, body)
        async with httpx_client(timeout=15) as client:
            resp = await client.post(f"{self._base}{path}", content=body, headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            return {"cancelled": False, "order_id": order_id, "error": data.get("msg", "unknown")}
        return {"cancelled": True, "order_id": order_id}

    async def _uta_open_orders(self, market_type):
        path = "/api/v3/trade/unfilled-orders?category=USDT-FUTURES&marginCoin=USDT"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget API error: {data.get('msg')}")
        return (data.get("data") or {}).get("list") or []

    async def _uta_fills(self, symbol, market_type):
        category = "spot" if market_type == "spot" else "USDT-FUTURES"
        path = f"/api/v3/trade/fills?category={category}"
        if symbol:
            path += f"&symbol={symbol}"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget API error: {data.get('msg')}")
        return (data.get("data") or {}).get("list") or []

    async def _uta_set_leverage(self, exchange_symbol, leverage):
        import json as _json
        path = "/api/v3/account/set-leverage"
        body_data = {
            "symbol": exchange_symbol,
            "category": "USDT-FUTURES",
            "marginCoin": "USDT",
            "leverage": str(int(leverage)),
            "posSide": "long",
        }
        body = _json.dumps(body_data)
        headers = self._headers("POST", path, body)
        async with httpx_client(timeout=15) as client:
            resp = await client.post(f"{self._base}{path}", content=body, headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget UTA set-leverage failed: {data.get('msg')}")

    async def _uta_order_detail(self, order_id, symbol, market_type="usdt_futures"):
        category = "spot" if market_type == "spot" else "USDT-FUTURES"
        path = f"/api/v3/trade/fills?category={category}&orderId={order_id}"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            return None
        lst = (data.get("data") or {}).get("list") or []
        return lst[0] if lst else None

    async def _uta_place_tpsl(self, symbol, plan_type, trigger_price, hold_side, size, execute_price, client_oid):
        import json as _json
        body_data: dict = {
            "category": "USDT-FUTURES",
            "symbol": symbol,
            "posSide": hold_side,
            "type": "tpsl",
        }
        if plan_type in ("pos_profit", "profit_plan"):
            body_data["takeProfit"] = str(trigger_price)
        else:
            body_data["stopLoss"] = str(trigger_price)
        if execute_price is not None:
            body_data["slLimitPrice"] = str(execute_price)
        if client_oid:
            body_data["clientOid"] = client_oid
        body = _json.dumps(body_data)
        path = "/api/v3/trade/place-strategy-order"
        headers = self._headers("POST", path, body)
        async with httpx_client(timeout=15) as client:
            resp = await client.post(f"{self._base}{path}", content=body, headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget UTA place_tpsl_order error: {data.get('msg')}")
        return data.get("data", {}).get("orderId", "")

    async def _uta_cancel_plan(self, symbol, order_id):
        import json as _json
        body = _json.dumps({"orderId": order_id, "symbol": symbol, "category": "USDT-FUTURES", "marginCoin": "USDT"})
        path = "/api/v3/trade/cancel-strategy-order"
        headers = self._headers("POST", path, body)
        async with httpx_client(timeout=15) as client:
            resp = await client.post(f"{self._base}{path}", content=body, headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            return {"cancelled": False, "order_id": order_id, "error": data.get("msg", "unknown")}
        return {"cancelled": True, "order_id": order_id}

    async def _uta_pending_plans(self, symbol):
        path = "/api/v3/trade/orders-plan-pending?category=USDT-FUTURES"
        if symbol:
            path += f"&symbol={symbol}"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            return []
        return (data.get("data") or {}).get("list") or []

    async def _uta_plan_history(self, symbol, order_id):
        path = "/api/v3/trade/orders-plan-history?category=USDT-FUTURES"
        if symbol:
            path += f"&symbol={symbol}"
        if order_id:
            path += f"&orderId={order_id}"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            return []
        return (data.get("data") or {}).get("list") or []

    async def get_open_orders(self, market_type: str = "spot") -> list[dict]:
        """Get unfilled (open) orders (§19.3)."""
        if await self._is_unified():
            return await self._uta_open_orders(market_type)
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
        if await self._is_unified():
            return await self._uta_cancel_order(order_id, symbol, market_type)
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
        if await self._is_unified():
            return await self._uta_fills(symbol, market_type)
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

    async def get_order_fills(
        self,
        *,
        order_id: str,
        symbol: str,
        market_type: str = "usdt_futures",
    ) -> list[dict]:
        """Return the complete private fill history for one exact futures Order."""
        if market_type != "usdt_futures":
            raise ValueError("exact Bitget order fills currently require usdt_futures")
        order_id = str(order_id or "").strip()
        symbol = str(symbol or "").strip()
        if not order_id or not symbol:
            raise ValueError("exact Bitget fill query requires order_id and symbol")

        endpoint = "/api/v2/mix/order/fills"
        cursor: str | None = None
        fills: list[dict] = []
        for _ in range(100):
            params = {
                "productType": "USDT-FUTURES",
                "symbol": symbol,
                "orderId": order_id,
                "limit": "100",
            }
            if cursor:
                params["idLessThan"] = cursor
            path = f"{endpoint}?{urlencode(params)}"
            headers = self._headers("GET", path)
            async with httpx_client(timeout=15) as client:
                response = await client.get(f"{self._base}{path}", headers=headers)
                payload = response.json()
            if payload.get("code") != "00000":
                raise RuntimeError("Bitget exact private fill history rejected")
            data = payload.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("fillList"), list):
                raise ValueError("Bitget exact private fill history malformed")
            page = data["fillList"]
            if any(
                not isinstance(row, dict)
                or str(row.get("orderId") or "") != order_id
                for row in page
            ):
                raise ValueError("Bitget exact private fill history mismatched")
            fills.extend(page)
            if len(page) < 100:
                return fills
            next_cursor = str(data.get("endId") or "")
            if not next_cursor or next_cursor == cursor:
                raise RuntimeError("Bitget exact private fill pagination incomplete")
            cursor = next_cursor
        raise RuntimeError("Bitget exact private fill history incomplete")

    async def get_funding_ledger(
        self,
        *,
        broker_id: str,
        symbol: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> dict:
        """Return a complete private funding-bill window for one futures account."""
        from aletheia.execution.broker_funding import (
            make_complete_ledger,
            normalize_bitget_funding_rows,
            require_retained_history,
        )

        expected_mode = "bitget_demo" if self.mode == "demo" else "bitget_live_limited"
        if broker_id != expected_mode:
            raise ValueError("Bitget funding account identity mismatch")
        start_ms = int(start_time_ms)
        end_ms = int(end_time_ms)
        if start_ms <= 0 or end_ms < start_ms:
            raise ValueError("Bitget funding history window is invalid")
        require_retained_history(
            start_ms,
            retention_days=90,
            source="Bitget account bills",
        )

        endpoint = "/api/v2/mix/account/bill"
        max_window_ms = 30 * 24 * 60 * 60 * 1000
        raw_rows: list[dict] = []
        chunk_start = start_ms
        while chunk_start <= end_ms:
            chunk_end = min(end_ms, chunk_start + max_window_ms - 1)
            cursor: str | None = None
            for _ in range(100):
                params = {
                    "productType": "USDT-FUTURES",
                    "businessType": "contract_settle_fee",
                    "startTime": str(chunk_start),
                    "endTime": str(chunk_end),
                    "limit": "100",
                }
                if cursor:
                    params["idLessThan"] = cursor
                path = f"{endpoint}?{urlencode(params)}"
                headers = self._headers("GET", path)
                async with httpx_client(timeout=15) as client:
                    response = await client.get(f"{self._base}{path}", headers=headers)
                    payload = response.json()
                if payload.get("code") != "00000":
                    raise RuntimeError("Bitget private funding history rejected")
                data = payload.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("bills"), list):
                    raise ValueError("Bitget private funding history malformed")
                page = data["bills"]
                raw_rows.extend(row for row in page if isinstance(row, dict))
                if len(page) < 100:
                    break
                next_cursor = str(data.get("endId") or "")
                if not next_cursor or next_cursor == cursor:
                    raise RuntimeError("Bitget private funding pagination incomplete")
                cursor = next_cursor
            else:
                raise RuntimeError("Bitget private funding history incomplete")
            chunk_start = chunk_end + 1

        canonical_symbol = to_canonical(symbol)
        events = [
            event for event in normalize_bitget_funding_rows(raw_rows)
            if event["symbol"] == canonical_symbol
        ]
        return make_complete_ledger(
            broker_id=broker_id,
            account_environment=self.mode,
            source_endpoint=endpoint,
            history_start_ms=start_ms,
            history_end_ms=end_ms,
            events=events,
        )

    async def get_verified_position_exit(self, request):
        """Read direct position + complete private futures fills; never mutate state."""
        from aletheia.data.symbols import to_bitget, to_canonical
        from aletheia.execution.verified_exit import (
            VerifiedExitFill,
            classify_verified_exit,
            inconclusive_verified_exit,
            private_history_window_available,
        )

        expected_mode = "bitget_demo" if self.mode == "demo" else "bitget_live_limited"
        if (
            request.broker_id != expected_mode
            or request.execution_mode != expected_mode
            or request.account_environment != self.mode
        ):
            return inconclusive_verified_exit(request, "account_identity_mismatch")
        if request.market_type != "usdt_futures":
            return inconclusive_verified_exit(request, "unsupported_market_type")
        if not private_history_window_available(request):
            return inconclusive_verified_exit(request, "history_window_unavailable")

        endpoint = "/api/v2/mix/order/fills"
        try:
            positions = await self.get_positions()
            present = any(
                to_canonical(position.symbol) == request.symbol
                and str(position.side).lower() == request.direction
                and float(position.quantity) > 0
                for position in positions
            )
            if present:
                return classify_verified_exit(
                    request, position_absent=False, history_complete=False,
                    close_fills=(), ambiguous_activity=False, pages=0,
                    source_endpoint=endpoint,
                )

            symbol = to_bitget(request.symbol)
            raw_fills: list[dict] = []
            cursor = None
            pages = 0
            complete = False
            for _ in range(100):
                path = (
                    f"{endpoint}?productType=USDT-FUTURES&symbol={symbol}"
                    f"&startTime={request.history_start_ms}"
                    f"&endTime={request.history_end_ms}&limit=100"
                )
                if cursor:
                    path += f"&idLessThan={cursor}"
                headers = self._headers("GET", path)
                async with httpx_client(timeout=15) as client:
                    response = await client.get(f"{self._base}{path}", headers=headers)
                    payload = response.json()
                if payload.get("code") != "00000":
                    raise RuntimeError("private fill history rejected")
                data = payload.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("fillList"), list):
                    raise ValueError("malformed private fill history")
                page = data["fillList"]
                pages += 1
                raw_fills.extend(page)
                if len(page) < 100:
                    complete = True
                    break
                next_cursor = str(data.get("endId") or "")
                if not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor

            close_fills: list[VerifiedExitFill] = []
            ambiguous = False
            known_entry_order_ids = set(request.known_entry_order_ids)
            for raw in raw_fills:
                timestamp = _bitget_int(raw.get("cTime") or raw.get("createdTime"))
                if not request.history_start_ms <= timestamp <= request.history_end_ms:
                    continue
                if to_canonical(str(raw.get("symbol") or "")) != request.symbol:
                    ambiguous = True
                    continue
                remote_order_id = str(raw.get("orderId") or "").strip()
                closes_direction = _bitget_fill_closes_direction(
                    raw, request.direction
                )
                if remote_order_id in known_entry_order_ids and not closes_direction:
                    continue
                if not closes_direction:
                    ambiguous = True
                    continue
                normalized = _bitget_verified_fill(raw)
                if normalized is None:
                    ambiguous = True
                else:
                    close_fills.append(normalized)
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
        if await self._is_unified():
            return await self._uta_place_tpsl(symbol, plan_type, trigger_price, hold_side, size, execute_price, client_oid)
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
        if await self._is_unified():
            return await self._uta_cancel_plan(symbol, order_id)
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
        if await self._is_unified():
            return await self._uta_pending_plans(symbol)
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
        Exact executed rows expose ``planStatus`` plus ``executeOrderId``. Plan
        prices are trigger metadata, not private Fill economics.
        """
        if await self._is_unified():
            return await self._uta_plan_history(symbol, order_id)
        path = (
            "/api/v2/mix/order/orders-plan-history"
            "?productType=USDT-FUTURES&planType=profit_loss"
        )
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
