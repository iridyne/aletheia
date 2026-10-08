"""Normalize the required Bitget parent/child and exact per-order Fill reads."""

from aletheia.data.symbols import to_bitget
from aletheia.uta.models import FillReport, NativeProtectionHistory, UTAAccountIdentity


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
