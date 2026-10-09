"""Normalize the required Bitget parent/child and exact per-order Fill reads."""

from decimal import Decimal, InvalidOperation

from aletheia.data.symbols import to_bitget
from aletheia.uta.models import (
    FillReport,
    NativeProtectionHistory,
    NativeProtectionLeg,
    NativeProtectionOpenOrders,
    UTAAccountIdentity,
)


class BitgetNativeProtectionReader:
    def __init__(self, identity: UTAAccountIdentity):
        from extensions.exchanges.bitget.client import BitgetClient

        if identity.venue != "bitget" or identity.environment not in {"demo", "live"}:
            raise ValueError("native_protection_account_unsupported")
        self.identity = identity
        self._client = BitgetClient(mode=identity.environment)

    async def get_plan_order_history(self, *, order_id: str) -> NativeProtectionHistory:
        history = await self._client.get_plan_order_history(order_id=order_id)
        execute_ids = {
            str(row.get("executeOrderId") or "").strip()
            for row in history
            if isinstance(row, dict)
            and str(row.get("orderId") or "") == str(order_id)
            and str(row.get("planStatus") or "").lower() == "executed"
            and str(row.get("executeOrderId") or "").strip()
        }
        return NativeProtectionHistory(
            identity=self.identity, plan_order_id=str(order_id),
            execute_order_ids=tuple(sorted(execute_ids)),
        )

    async def get_open_protection(
        self, *, symbol: str, market_type: str,
    ) -> NativeProtectionOpenOrders:
        """Normalize every open venue SL/TP leg; any malformed row voids the read."""
        if market_type != "usdt_futures":
            raise ValueError("native_protection_market_unsupported")
        venue_symbol = to_bitget(symbol)
        legs: list[NativeProtectionLeg] = []
        for row in await self._client.get_open_tpsl_orders(symbol=venue_symbol):
            # A row we cannot attribute (not an object, or no symbol) voids the read: dropping
            # it could turn a protected position into "no stop found" and close it. A row that
            # names another instrument is simply not this symbol's protection.
            row_symbol = str(row.get("symbol") or "").strip() if isinstance(row, dict) else ""
            if not row_symbol:
                raise ValueError("native_protection_row_malformed")
            if row_symbol != venue_symbol:
                continue
            order_id = str(row.get("orderId") or "").strip()
            side = str(row.get("posSide") or "").strip().lower()
            if not order_id or side not in {"long", "short"}:
                raise ValueError("native_protection_row_malformed")
            raw_qty = str(row.get("qty") or "").strip()
            try:
                quantity = Decimal(raw_qty) if raw_qty else None
            except InvalidOperation:
                raise ValueError("native_protection_row_malformed") from None
            if quantity is not None and not (quantity.is_finite() and quantity > 0):
                raise ValueError("native_protection_row_malformed")
            for kind, field in (("sl", "stopLoss"), ("tp", "takeProfit")):
                trigger = str(row.get(field) or "").strip()
                if trigger:
                    legs.append(NativeProtectionLeg.model_validate({
                        "plan_order_id": order_id, "kind": kind, "position_side": side,
                        "trigger_price": trigger, "quantity": quantity,
                    }))
        return NativeProtectionOpenOrders(
            identity=self.identity, symbol=symbol, legs=tuple(legs),
        )

    async def get_order_fill_reports(
        self, *, order_id: str, symbol: str, market_type: str, identity: UTAAccountIdentity,
    ) -> tuple[FillReport, ...]:
        if identity != self.identity:
            raise ValueError("uta_account_identity_mismatch")
        return tuple(await self._client.get_order_fill_reports(
            order_id=order_id, symbol=to_bitget(symbol), market_type=market_type, identity=identity,
        ))

    async def close(self) -> None:
        """No persistent transport: the native client scopes HTTP per request."""
