"""Read-only native Bitget protection history and exact Fill evidence."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable

from aletheia.uta.compat import instrument_from_local
from aletheia.uta.models import FillReport, UTAAccountIdentity
from aletheia.net import close_ccxt, make_ccxt
from extensions.exchanges.bitget.profile_config import _KNOWN_PROFILES
from aletheia.config.secrets import get_secret
from aletheia.data.symbols import to_canonical


def _bitget_decimal(value, *, positive: bool = False) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Bitget fill decimal is malformed") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError("Bitget fill decimal is not finite and positive")
    return result


def _bitget_fill_fee(row: dict) -> tuple[Decimal | None, str | None]:
    direct = row.get("fee")
    if direct not in (None, ""):
        fee = _bitget_decimal(direct)
        return abs(fee), str(row.get("feeCoin") or "") or None
    details = row.get("feeDetail")
    if isinstance(details, dict):
        details = [details]
    if details in (None, []):
        return None, str(row.get("feeCoin") or "") or None
    if not isinstance(details, list):
        raise ValueError("Bitget fill fee detail is malformed")
    if any(not isinstance(item, dict) for item in details):
        raise ValueError("Bitget fill fee detail is malformed")
    currencies = {str(item.get("feeCoin") or "").strip().upper() for item in details}
    if len(currencies) != 1:
        raise ValueError("Bitget fill fee currency is ambiguous")
    values: list[Decimal] = []
    missing = False
    for item in details:
        raw_fee = item.get("totalFee") or item.get("fee")
        if raw_fee in (None, ""):
            missing = True
            continue
        parsed = _bitget_decimal(raw_fee)
        values.append(abs(parsed))
    currency = next(iter(currencies)) or None
    return (None if missing else sum(values, Decimal(0))), currency


def normalize_private_fill_reports(
    rows: Iterable[dict] | dict,
    *,
    symbol: str,
    market_type: str,
    identity: UTAAccountIdentity,
    received_at: datetime | None = None,
) -> list[FillReport]:
    """Normalize native Bitget fill rows before they cross into Core."""
    if isinstance(rows, dict):
        rows = rows.get("fillList") or rows.get("list") or []
    if not isinstance(rows, list):
        raise ValueError("Bitget private fill response is malformed")
    received = received_at or datetime.now(timezone.utc)
    if received.tzinfo is None or received.utcoffset() is None:
        raise ValueError("Bitget fill receipt time must be timezone-aware")
    instrument = instrument_from_local(to_canonical(symbol), market_type)
    normalized: dict[str, FillReport] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        fill_id = str(row.get("tradeId") or row.get("execId") or row.get("fillId") or "").strip()
        order_id = str(row.get("orderId") or row.get("order_id") or "").strip()
        client_order_id = str(row.get("clientOid") or row.get("clientOrderId") or "").strip() or None
        price = _bitget_decimal(
            row.get("fillPrice") or row.get("fillPx") or row.get("price") or row.get("execPrice"),
            positive=True,
        )
        quantity = _bitget_decimal(
            row.get("fillQuantity") or row.get("fillQty") or row.get("fillSz")
            or row.get("baseVolume") or row.get("execQty") or row.get("size")
            or row.get("fillSize"),
            positive=True,
        )
        timestamp = row.get("cTime") or row.get("createdTime") or row.get("execTime") or row.get("timestamp")
        try:
            timestamp_ms = int(timestamp or 0)
            executed_at = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError) as exc:
            raise ValueError("Bitget fill timestamp is malformed") from exc
        if not fill_id or not order_id or price is None or quantity is None or timestamp_ms <= 0:
            raise ValueError("Bitget fill identity or economics is incomplete")
        fee, fee_currency = _bitget_fill_fee(row)
        raw_liquidity = str(
            row.get("tradeScope") or row.get("execType") or row.get("liquidity") or ""
        ).strip().lower()
        liquidity = {"m": "maker", "maker": "maker", "t": "taker", "taker": "taker"}.get(raw_liquidity)
        info = row.get("info") if isinstance(row.get("info"), dict) else {}
        position_side = row.get("positionSide") or row.get("posSide") or info.get("positionSide")
        trade_intent = row.get("tradeSide") or row.get("trade_side") or info.get("tradeSide")
        report = FillReport(
            identity=identity,
            instrument=instrument,
            client_order_id=client_order_id,
            venue_order_id=order_id,
            venue_fill_id=fill_id,
            side=str(row.get("side") or "unknown"),
            position_side=str(position_side) if position_side else None,
            trade_intent=str(trade_intent) if trade_intent else None,
            quantity=quantity,
            price=price,
            fee=fee,
            fee_currency=fee_currency,
            liquidity_side=liquidity,
            executed_at=executed_at,
            received_at=received,
        )
        prior = normalized.get(fill_id)
        if prior is not None and prior != report:
            raise ValueError(f"conflicting Bitget fill identity: {fill_id}")
        normalized[fill_id] = report
    return sorted(normalized.values(), key=lambda item: (item.executed_at, item.venue_fill_id))


class BitgetClient:
    """Native read-only escape hatch; all trading mutations use the UTA adapter."""

    def __init__(self, mode: str = "demo", profile: str = "classic_v2"):
        if profile not in _KNOWN_PROFILES:
            raise ValueError(f"unknown Bitget profile {profile!r}; expected one of {sorted(_KNOWN_PROFILES)}")
        self.mode = mode
        self.profile = profile
        self.demo = mode == "demo"
        self._uta = profile == "uta_v3"

    def _make_exchange(self):
        prefix = f"bitget.{self.mode}"
        api_key = get_secret(f"{prefix}.api_key")
        api_secret = get_secret(f"{prefix}.api_secret")
        passphrase = get_secret(f"{prefix}.passphrase")
        if not all([api_key, api_secret, passphrase]):
            raise RuntimeError(
                f"Bitget {self.mode} credentials not configured. Use: aletheia secrets set {prefix}.api_key"
            )
        exchange, session = make_ccxt(
            "bitget",
            apiKey=api_key,
            secret=api_secret,
            password=passphrase,
            options={"uta": self._uta},
        )
        if self.demo:
            exchange.set_sandbox_mode(True)
        return exchange, session

    async def _uta_plan_history(self, symbol, order_id):
        """Return UTA strategy children in the classic plan-history shape.

        UTA v3 exposes terminal strategy children through
        ``strategy-sub-orders``.  The parent strategy id is the scheduler's
        plan id; a filled ``subOrderId`` is the execution order id used for
        the existing account-proof and fill-materialization flow.
        """
        del symbol  # the v3 child endpoint is keyed by the parent order id
        order_id = str(order_id or "").strip()
        if not order_id:
            raise ValueError("Bitget UTA strategy history requires order_id")

        exchange, session = self._make_exchange()
        try:
            cursor: str | None = None
            rows: list[dict] = []
            for _ in range(100):
                params: dict[str, object] = {"orderId": order_id, "limit": "100"}
                if cursor:
                    params["cursor"] = cursor
                data = await exchange.privateUtaGetV3TradeStrategySubOrders(params)
                if data.get("code") != "00000":
                    raise RuntimeError("Bitget UTA strategy sub-order history rejected")
                payload = data.get("data") or {}
                page = payload.get("list") if isinstance(payload, dict) else None
                if not isinstance(page, list):
                    raise ValueError("Bitget UTA strategy sub-order history malformed")
                for child in page:
                    if not isinstance(child, dict):
                        continue
                    child_id = str(child.get("subOrderId") or "").strip()
                    status = str(child.get("status") or "").strip().lower()
                    if not child_id:
                        continue
                    row = dict(child)
                    row["orderId"] = order_id
                    row["executeOrderId"] = child_id if status == "filled" else ""
                    row["planStatus"] = "executed" if status == "filled" else status
                    rows.append(row)
                if len(page) < 100:
                    return rows
                next_cursor = str(payload.get("cursor") or "").strip()
                if not next_cursor or next_cursor == cursor:
                    raise RuntimeError("Bitget UTA strategy sub-order pagination incomplete")
                cursor = next_cursor
            raise RuntimeError("Bitget UTA strategy sub-order history incomplete")
        finally:
            await close_ccxt(exchange, session)

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
        if self.profile == "uta_v3":
            return await self._get_order_fills_uta_v3(order_id=order_id, symbol=symbol)

        exchange, session = self._make_exchange()
        try:
            cursor: str | None = None
            fills: list[dict] = []
            for _ in range(100):
                params: dict[str, object] = {
                    "productType": "USDT-FUTURES",
                    "symbol": symbol,
                    "orderId": order_id,
                    "limit": "100",
                }
                if cursor:
                    params["idLessThan"] = cursor
                payload = await exchange.privateMixGetV2MixOrderFills(params)
                if payload.get("code") != "00000":
                    raise RuntimeError("Bitget exact private fill history rejected")
                data = payload.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("fillList"), list):
                    raise ValueError("Bitget exact private fill history malformed")
                page = [
                    row for row in data["fillList"]
                    if isinstance(row, dict) and str(row.get("orderId") or "") == order_id
                ]
                fills.extend(page)
                if len(data["fillList"]) < 100:
                    return fills
                next_cursor = str(data.get("endId") or "")
                if not next_cursor or next_cursor == cursor:
                    raise RuntimeError("Bitget exact private fill pagination incomplete")
                cursor = next_cursor
            raise RuntimeError("Bitget exact private fill history incomplete")
        finally:
            await close_ccxt(exchange, session)

    async def get_order_fill_reports(
        self,
        *,
        order_id: str,
        symbol: str,
        market_type: str = "usdt_futures",
        identity: UTAAccountIdentity,
        received_at: datetime | None = None,
    ) -> list[FillReport]:
        """Return exact fills as normalized Core DTOs."""
        rows = await self.get_order_fills(
            order_id=order_id,
            symbol=symbol,
            market_type=market_type,
        )
        return normalize_private_fill_reports(
            rows,
            symbol=symbol,
            market_type=market_type,
            identity=identity,
            received_at=received_at,
        )

    async def _get_order_fills_uta_v3(self, *, order_id: str, symbol: str) -> list[dict]:
        """Fetch fills via UTA v3 /api/v3/trade/fills, filtering to this order_id."""
        params: dict[str, object] = {
            "category": "USDT-FUTURES",
            "symbol": symbol,
            "orderId": order_id,
            "limit": "100",
        }
        exchange, session = self._make_exchange()
        try:
            payload = await exchange.privateUtaGetV3TradeFills(params)
        finally:
            await close_ccxt(exchange, session)
        if payload.get("code") != "00000":
            raise RuntimeError("Bitget UTA exact private fill history rejected")
        data = payload.get("data") or {}
        raw = data.get("list") or []
        return [row for row in raw if isinstance(row, dict) and str(row.get("orderId") or "") == order_id]

    async def get_plan_order_history(self, symbol: str | None = None, order_id: str | None = None) -> list[dict]:
        """Query triggered/filled TPSL order history for USDT-FUTURES.

        Returns list of historical plan orders (triggered, filled, cancelled).
        Exact executed rows expose ``planStatus`` plus ``executeOrderId``. Plan
        prices are trigger metadata, not private Fill economics.
        """
        if self.profile == "uta_v3":
            return await self._uta_plan_history(symbol=symbol, order_id=order_id)
        params: dict[str, object] = {"productType": "USDT-FUTURES", "planType": "profit_loss"}
        if symbol:
            params["symbol"] = symbol
        if order_id:
            params["orderId"] = order_id
        exchange, session = self._make_exchange()
        try:
            data = await exchange.privateMixGetV2MixOrderOrdersPlanHistory(params)
        finally:
            await close_ccxt(exchange, session)
        if data.get("code") != "00000":
            raise RuntimeError(f"Bitget API error: {data.get('msg')}")
        return data.get("data", {}).get("entrustedList", []) or []
