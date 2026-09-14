"""Active Bitget Exchange Extension composition for the Core UTA port."""

from __future__ import annotations

from aletheia.data.symbols import to_bitget
from aletheia.uta.manager import (
    NativeProtectionHistory,
    NativeProtectionReader,
    configure_uta_provider,
)
from aletheia.uta.models import FillReport, UTAAccountIdentity
from extensions.exchanges.bitget import client as bitget_client
from extensions.exchanges.bitget.auth import (
    probe_candidate_credentials as _probe_candidate_credentials,
)
from extensions.exchanges import composition as ccxt_manager


class _BitgetNativeProtectionReader:
    def __init__(self, identity: UTAAccountIdentity):
        self.identity = identity
        self._client = bitget_client.BitgetClient(
            mode=identity.environment,
            profile=identity.profile,
        )

    async def get_plan_order_history(self, *, order_id: str) -> NativeProtectionHistory:
        rows = await self._client.get_plan_order_history(order_id=order_id)
        execute_order_ids = tuple(
            str(row.get("executeOrderId") or "").strip()
            for row in rows
            if isinstance(row, dict)
            and str(row.get("orderId") or "").strip() == order_id
            and str(row.get("planStatus") or "").lower() == "executed"
            and str(row.get("executeOrderId") or "").strip()
        )
        return NativeProtectionHistory(
            identity=self.identity,
            plan_order_id=order_id,
            execute_order_ids=execute_order_ids,
        )

    async def get_order_fill_reports(
        self,
        *,
        order_id: str,
        symbol: str,
        market_type: str,
        identity: UTAAccountIdentity,
    ) -> tuple[FillReport, ...]:
        if identity != self.identity:
            raise ValueError("uta_account_identity_mismatch")
        reports = await self._client.get_order_fill_reports(
            order_id=order_id,
            symbol=to_bitget(symbol),
            market_type=market_type,
            identity=identity,
        )
        if any(report.identity != identity for report in reports):
            raise ValueError("uta_account_identity_mismatch")
        return tuple(reports)

    async def close(self) -> None:
        await self._client.close()


def create_native_protection_reader(
    execution_mode: str,
    *,
    expected_account: UTAAccountIdentity | None = None,
    cfg=None,
) -> NativeProtectionReader:
    identity = expected_account or ccxt_manager.resolve_uta_identity(cfg, execution_mode)
    if identity.execution_mode != execution_mode or identity.venue != "bitget":
        raise ValueError("native_protection_unavailable")
    return _BitgetNativeProtectionReader(identity)


def register_uta_provider() -> None:
    """Register the active Bitget extension through the Core UTA port."""
    from aletheia.execution.order_constraints import configure_order_constraint_reader
    from aletheia.execution.venue_capabilities import configure_market_capability_reader
    from extensions.exchanges.ccxt.order_constraints import read_order_constraints
    from extensions.exchanges.bitget.profile_config import configuration_diagnostic, profile_accounts

    configure_order_constraint_reader(read_order_constraints)
    configure_market_capability_reader(
        ccxt_manager.fetch_market_restrictions, ccxt_manager.cached_market_restrictions
    )
    configure_uta_provider(
        registry_factory=ccxt_manager.build_uta_registry,
        funding_history_retention_contract=ccxt_manager.funding_history_retention_contract,
        identity_resolver=ccxt_manager.resolve_uta_identity,
        native_protection_reader_factory=create_native_protection_reader,
        candidate_credential_probe=_probe_candidate_credentials,
        configuration_diagnostic=configuration_diagnostic,
        credential_configuration=ccxt_manager.credential_configuration,
        profile_accounts=profile_accounts,
        public_connectivity_probe=ccxt_manager.probe_public_connectivity,
    )
