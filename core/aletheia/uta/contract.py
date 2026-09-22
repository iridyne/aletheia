"""Frozen public UTA V1 protocol."""

from typing import Protocol, runtime_checkable

from aletheia.uta.models import (
    AccountAuthorityProbeReport,
    AccountSnapshot,
    CancelOrderCommand,
    CommandReport,
    ExecutionMassStatus,
    FillListReport,
    FillReport,
    FillQuery,
    FundingLedgerQuery,
    FundingLedgerReport,
    ModifyOrderCommand,
    NativeProtectionHistory,
    OrderListReport,
    OrderQuery,
    OrderQueryReport,
    PositionListReport,
    PositionQuery,
    ReconciliationQuery,
    SubmissionReport,
    SubmitOrderCommand,
    UTAAccountIdentity,
    UTACapabilities,
    UTAHealthReport,
    VerifiedExitQuery,
    VerifiedExitReport,
)

UTA_CONTRACT_VERSION = 1


class NativeProtectionReader(Protocol):
    """Read-only native escape hatch; Core still proves and materializes fills."""

    identity: UTAAccountIdentity

    async def get_plan_order_history(self, *, order_id: str) -> NativeProtectionHistory: ...

    async def get_order_fill_reports(
        self, *, order_id: str, symbol: str, market_type: str, identity: UTAAccountIdentity,
    ) -> tuple[FillReport, ...]: ...

    async def close(self) -> None: ...


@runtime_checkable
class UTAAdapter(Protocol):
    @property
    def identity(self) -> UTAAccountIdentity: ...

    @property
    def capabilities(self) -> UTACapabilities: ...

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def health(self) -> UTAHealthReport: ...

    async def get_account(self, sub_account_id: str | None = None) -> AccountSnapshot: ...

    async def get_positions(self, query: PositionQuery | None = None) -> PositionListReport: ...

    async def get_open_orders(self, query: OrderQuery | None = None) -> OrderListReport: ...

    async def get_order(self, query: OrderQuery) -> OrderQueryReport: ...

    async def get_fills(self, query: FillQuery) -> FillListReport: ...

    async def get_mass_status(self, query: ReconciliationQuery) -> ExecutionMassStatus: ...

    async def get_funding_ledger(self, query: FundingLedgerQuery) -> FundingLedgerReport: ...

    async def verify_position_exit(self, query: VerifiedExitQuery) -> VerifiedExitReport: ...

    async def probe_account_authority(self) -> AccountAuthorityProbeReport: ...

    async def submit_order(self, command: SubmitOrderCommand) -> SubmissionReport: ...

    async def modify_order(self, command: ModifyOrderCommand) -> CommandReport: ...

    async def cancel_order(self, command: CancelOrderCommand) -> CommandReport: ...
