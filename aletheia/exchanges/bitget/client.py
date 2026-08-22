"""Bitget API client — ExchangeAdapter implementation."""

from dataclasses import dataclass
from datetime import datetime
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


class ProfileContractError(RuntimeError):
    """Raised when a BitgetClient operation is called on the wrong profile."""




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
    position_id: str | None = None  # exchange-reported position identity


def _bitget_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _bitget_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
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


_KNOWN_PROFILES = frozenset({"classic_v2", "uta_v3"})

# Error codes that mean "no open position" — profile-specific where they differ.
# classic_v2: 22002, 25227. uta_v3: uses the same codes per Bitget migration guide.
_CLOSE_ABSENT_CODES_CLASSIC_V2 = frozenset({"22002", "25227"})
_CLOSE_ABSENT_CODES_UTA_V3 = frozenset({"22002", "25227", "45110"})


def profile_close_absent_codes(profile: str) -> frozenset[str]:
    if profile == "uta_v3":
        return _CLOSE_ABSENT_CODES_UTA_V3
    return _CLOSE_ABSENT_CODES_CLASSIC_V2


def profile_pagination_param(profile: str) -> str:
    """Return the pagination cursor parameter name for a profile's fill endpoints."""
    return "cursor" if profile == "uta_v3" else "idLessThan"


class BitgetClient:
    """Native Bitget adapter supporting spot + USDT futures, demo + live.

    ``profile`` selects the API endpoint family and payload contract:
    - ``classic_v2``: /api/v2/ (marginCoin, marginMode, size, idLessThan pagination).
    - ``uta_v3``:     /api/v3/ Unified Trading Account (category, qty, posSide, cursor
                     pagination). Experimental — Demo qualification required.
    Endpoints from the other profile's family raise ``ProfileContractError``.
    """

    def __init__(self, mode: str = "demo", profile: str = "classic_v2"):
        if profile not in _KNOWN_PROFILES:
            raise ValueError(
                f"unknown Bitget profile {profile!r}; expected one of {sorted(_KNOWN_PROFILES)}"
            )
        self.mode = mode
        self.profile = profile
        self.demo = mode == "demo"
        self._base = BASE_URL

    def _assert_classic_v2(self, operation: str) -> None:
        if self.profile != "classic_v2":
            raise ProfileContractError(
                f"operation {operation!r} requires classic_v2 profile, "
                f"configured profile is {self.profile!r}"
            )

    def _assert_uta_v3(self, operation: str) -> None:
        if self.profile != "uta_v3":
            raise ProfileContractError(
                f"operation {operation!r} requires uta_v3 profile, "
                f"configured profile is {self.profile!r}"
            )

    def _assert_uta_v3_qualified(self) -> None:
        """UTA v3 Demo qualification is a prerequisite for live order operations."""
        raise NotImplementedError(
            "Bitget uta_v3 profile is Experimental: "
            "Demo end-to-end qualification required before order operations. "
            "See issue #1003."
        )

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

    async def set_leverage(self, exchange_symbol: str, leverage: int) -> None:
        """Set USDT-futures leverage (crossed margin) for a symbol on Bitget.

        ``exchange_symbol`` is the Bitget order format (e.g. BTCUSDT), matching what
        place_order sends. Fail-closed: any non-success response raises so the broker
        aborts the order rather than opening at the account-default leverage. Params
        per Bitget mix v2 /account/set-leverage — confirm against a live demo account.
        """
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
        """Place an order — gated by execution mode externally.

        Only classic_v2 profile is qualified for order placement. UTA v3
        raises NotImplementedError until Demo qualification completes (#1003).
        """
        import json
        if self.profile == "uta_v3":
            self._assert_uta_v3_qualified()

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
        if (order.market_type != "spot" and order.leverage is not None and order.leverage >= 1
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
            # Error code set for "no open position" is profile-specific:
            # classic_v2 uses 22002/25227; uta_v3 also adds 45110.
            # Scope this classification to hedge-mode closes.
            if order.hold_side and response_code in profile_close_absent_codes(self.profile):
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
        """Get open USDT-futures positions (§19.3). classic_v2 only."""
        self._assert_classic_v2("get_positions")
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
                position_id=str(p.get("posId") or p.get("positionId") or p.get("id") or "") or None,
            ))
        return positions

    async def get_mix_account(self, product_type: str = "USDT-FUTURES") -> dict:
        """Get futures/mix account summary (equity, available, unrealized PnL).

        classic_v2 only — v2 mix/account/accounts endpoint.
        For Bitget demo accounts this is the only endpoint that returns real
        balance — spot/account/assets returns empty for demo keys.
        """
        self._assert_classic_v2("get_mix_account")
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

        Only available with uta_v3 profile; classic_v2 raises ProfileContractError.
        Returns the full UTA account with all assets.
        """
        self._assert_uta_v3("get_uta_account")
        path = "/api/v3/account/assets"
        headers = self._headers("GET", path)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{self._base}{path}", headers=headers)
            data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget UTA API error: {data.get('msg')}")
        return data.get("data", {}) or {}

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
        Exact executed rows expose ``planStatus`` plus ``executeOrderId``. Plan
        prices are trigger metadata, not private Fill economics.
        """
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
