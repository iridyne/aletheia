"""Immutable, Decimal-safe canonical models for the UTA V1 boundary."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def _decimal_from_lossless(value: object) -> Decimal | None:
    if value is None or isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("UTA decimal fields reject raw binary floats")
    if isinstance(value, (str, int)):
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("invalid decimal value") from exc
        if not result.is_finite():
            raise ValueError("UTA decimal fields must be finite")
        return result
    raise ValueError("UTA decimal fields require Decimal, integer, or lossless string")


DecimalValue = Annotated[Decimal, BeforeValidator(_decimal_from_lossless)]


class ReadCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


class CapabilitySupport(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class UTAHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    DISABLED = "disabled"


class UTAReach(str, Enum):
    DOWN = "down"
    CONNECTED = "connected"
    READABLE = "readable"
    WRITABLE = "writable"


class OrderLifecycle(str, Enum):
    ACCEPTED = "accepted"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class CommandEvidence(str, Enum):
    NOT_SENT = "not_sent"
    VENUE_REJECTED = "venue_rejected"
    ACKNOWLEDGED = "acknowledged"
    AMBIGUOUS = "ambiguous"


class VerifiedExitState(str, Enum):
    POSITION_PRESENT = "position_present"
    VERIFIED_FILL_EXIT = "verified_fill_exit"
    CONFIRMED_ABSENT_WITHOUT_FILL = "confirmed_absent_without_fill"
    INCONCLUSIVE = "inconclusive"


class UTAModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SubAccountIdentity(UTAModel):
    sub_account_id: str = Field(min_length=1)
    wallet: str | None = None
    label: str | None = None


class UTAAccountIdentity(UTAModel):
    account_id: str = Field(min_length=1)
    execution_mode: str = Field(min_length=1)
    venue: str = Field(min_length=1)
    environment: Literal["sandbox", "demo", "testnet", "live", "local_paper"]
    profile: str = Field(min_length=1)
    account_fingerprint: str | None = None
    credential_generation: int = Field(ge=0)
    sub_accounts: tuple[SubAccountIdentity, ...] = ()
    adapter_implementation: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    ccxt_version: str | None = None


class InstrumentIdentity(UTAModel):
    canonical_symbol: str = Field(min_length=1)
    venue_symbol: str = Field(min_length=1)
    venue_instrument_id: str | None = None
    market_type: Literal["spot", "usdt_futures"]
    base_currency: str = Field(min_length=1)
    quote_currency: str = Field(min_length=1)
    settlement_currency: str | None = None
    contract_size: DecimalValue | None = None
    linear: bool | None = None
    inverse: bool | None = None
    position_side: str | None = None
    position_mode: str | None = None
    price_precision: DecimalValue | None = None
    quantity_precision: DecimalValue | None = None


class QueryScope(UTAModel):
    sub_account_id: str | None = None
    instrument: InstrumentIdentity | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    limit: int | None = Field(default=None, gt=0)
    cursor: str | None = None
    effective_start_at: datetime | None = None
    effective_end_at: datetime | None = None
    next_cursor: str | None = None


class UTACapabilities(UTAModel):
    account_read: CapabilitySupport = CapabilitySupport.UNKNOWN
    position_read: CapabilitySupport = CapabilitySupport.UNKNOWN
    order_read: CapabilitySupport = CapabilitySupport.UNKNOWN
    fill_read: CapabilitySupport = CapabilitySupport.UNKNOWN
    funding_ledger: CapabilitySupport = CapabilitySupport.UNKNOWN
    verified_exit: CapabilitySupport = CapabilitySupport.UNKNOWN
    market_order: CapabilitySupport = CapabilitySupport.UNKNOWN
    limit_order: CapabilitySupport = CapabilitySupport.UNKNOWN
    stop_order: CapabilitySupport = CapabilitySupport.UNKNOWN
    trigger_order: CapabilitySupport = CapabilitySupport.UNKNOWN
    modify_order: CapabilitySupport = CapabilitySupport.UNKNOWN
    cancel_order: CapabilitySupport = CapabilitySupport.UNKNOWN
    post_only: CapabilitySupport = CapabilitySupport.UNKNOWN
    reduce_only: CapabilitySupport = CapabilitySupport.UNKNOWN
    close_position: CapabilitySupport = CapabilitySupport.UNKNOWN
    one_way_mode: CapabilitySupport = CapabilitySupport.UNKNOWN
    hedge_mode: CapabilitySupport = CapabilitySupport.UNKNOWN
    leverage: CapabilitySupport = CapabilitySupport.UNKNOWN
    margin_mode: CapabilitySupport = CapabilitySupport.UNKNOWN
    native_tpsl: CapabilitySupport = CapabilitySupport.UNKNOWN
    protection_replace: CapabilitySupport = CapabilitySupport.UNKNOWN
    protection_cancel: CapabilitySupport = CapabilitySupport.UNKNOWN
    client_order_id: CapabilitySupport = CapabilitySupport.UNKNOWN
    trading_permission: CapabilitySupport = CapabilitySupport.UNKNOWN
    supported_market_types: tuple[str, ...] = ()
    supported_time_in_force: tuple[str, ...] = ()
    client_order_id_max_length: int | None = Field(default=None, gt=0)
    private_order_history_bounds: str | None = None
    private_fill_history_bounds: str | None = None
    sub_account_behavior: str | None = None
    environment_available: bool = False
    source: str = Field(min_length=1)
    fetched_at: datetime | None = None


class UTAHealthReport(UTAModel):
    identity: UTAAccountIdentity
    health: UTAHealth
    reach: UTAReach
    completeness: ReadCompleteness
    received_at: datetime
    source_at: datetime | None = None
    reason_code: str | None = None
    profile_version: str = Field(min_length=1)


class ReportEnvelope(UTAModel):
    identity: UTAAccountIdentity
    completeness: ReadCompleteness
    scope: QueryScope
    received_at: datetime
    source_at: datetime | None = None
    reason_code: str | None = None
    profile_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_non_complete_reason(self):
        if self.completeness is not ReadCompleteness.COMPLETE and not self.reason_code:
            raise ValueError("non-complete UTA reports require a sanitized reason_code")
        return self


class AccountSnapshot(ReportEnvelope):
    settlement_currency: str = Field(min_length=1)
    equity: DecimalValue | None = None
    available: DecimalValue | None = None
    used: DecimalValue | None = None
    frozen: DecimalValue | None = None
    margin: DecimalValue | None = None
    unrealized_pnl: DecimalValue | None = None
    realized_pnl: DecimalValue | None = None
    maintenance_margin: DecimalValue | None = None


class PositionQuery(UTAModel):
    scope: QueryScope = Field(default_factory=QueryScope)
    side: str | None = None
    include_closed: bool = False


class PositionStatusReport(UTAModel):
    identity: UTAAccountIdentity
    instrument: InstrumentIdentity
    venue_position_id: str | None = None
    side: str
    quantity: DecimalValue
    entry_price: DecimalValue | None = None
    mark_price: DecimalValue | None = None
    unrealized_pnl: DecimalValue | None = None
    leverage: DecimalValue | None = None
    margin_mode: str | None = None
    liquidation_price: DecimalValue | None = None
    opened_at: datetime | None = None
    opened_at_provenance: str | None = None
    source_at: datetime | None = None
    received_at: datetime


class PositionListReport(ReportEnvelope):
    positions: tuple[PositionStatusReport, ...] = ()


class OrderQuery(UTAModel):
    scope: QueryScope = Field(default_factory=QueryScope)
    client_order_id: str | None = None
    venue_order_id: str | None = None

    @model_validator(mode="after")
    def require_order_identity(self):
        if not self.client_order_id and not self.venue_order_id:
            raise ValueError("OrderQuery requires client_order_id or venue_order_id")
        return self


class OrderStatusReport(UTAModel):
    identity: UTAAccountIdentity
    instrument: InstrumentIdentity
    client_order_id: str | None = None
    venue_order_id: str | None = None
    side: str
    order_type: str
    original_quantity: DecimalValue
    filled_quantity: DecimalValue
    remaining_quantity: DecimalValue | None = None
    price: DecimalValue | None = None
    trigger_price: DecimalValue | None = None
    time_in_force: str | None = None
    post_only: bool | None = None
    reduce_only: bool | None = None
    state: OrderLifecycle
    venue_at: datetime | None = None
    source_at: datetime | None = None
    received_at: datetime


class OrderListReport(ReportEnvelope):
    orders: tuple[OrderStatusReport, ...] = ()


class OrderQueryReport(ReportEnvelope):
    found: OrderStatusReport | None = None


class FillQuery(UTAModel):
    scope: QueryScope
    client_order_id: str | None = None
    venue_order_id: str | None = None


class FillReport(UTAModel):
    identity: UTAAccountIdentity
    instrument: InstrumentIdentity
    client_order_id: str | None = None
    venue_order_id: str | None = None
    venue_fill_id: str = Field(min_length=1)
    side: str
    quantity: DecimalValue
    price: DecimalValue
    fee: DecimalValue | None = None
    fee_currency: str | None = None
    liquidity_side: str | None = None
    executed_at: datetime
    received_at: datetime


class FillListReport(ReportEnvelope):
    fills: tuple[FillReport, ...] = ()


class ReconciliationQuery(UTAModel):
    scope: QueryScope
    client_order_ids: tuple[str, ...] = ()
    venue_order_ids: tuple[str, ...] = ()


class ExecutionMassStatus(ReportEnvelope):
    orders: tuple[OrderStatusReport, ...] = ()
    fills: tuple[FillReport, ...] = ()
    positions: tuple[PositionStatusReport, ...] = ()
    unsupported_surfaces: tuple[str, ...] = ()
    unresolved_surfaces: tuple[str, ...] = ()


class FundingLedgerQuery(UTAModel):
    scope: QueryScope


class FundingLedgerEntry(UTAModel):
    identity: UTAAccountIdentity
    instrument: InstrumentIdentity
    settlement_at: datetime
    amount: DecimalValue
    currency: str = Field(min_length=1)
    rate: DecimalValue | None = None
    venue_ledger_id: str | None = None


class FundingLedgerReport(ReportEnvelope):
    entries: tuple[FundingLedgerEntry, ...] = ()


class VerifiedExitQuery(UTAModel):
    identity: UTAAccountIdentity
    local_position_id: str = Field(min_length=1)
    instrument: InstrumentIdentity
    direction: str
    expected_quantity: DecimalValue
    history_start_at: datetime
    history_end_at: datetime
    local_order_ids: tuple[str, ...] = ()


class VerifiedExitReport(ReportEnvelope):
    state: VerifiedExitState
    local_position_id: str = Field(min_length=1)
    exit_fills: tuple[FillReport, ...] = ()
    current_position: PositionStatusReport | None = None


class AccountAuthorityProbeReport(ReportEnvelope):
    authenticated_private_readable: bool
    trading_permission: CapabilitySupport
    account_fingerprint: str | None = None
    capability_version: str = Field(min_length=1)


OrderPurpose = Literal["entry", "exit", "protective_stop", "take_profit", "protection_child"]


class MutationCommand(UTAModel):
    command_id: str = Field(min_length=1)
    expected_account: UTAAccountIdentity
    instrument: InstrumentIdentity
    purpose: OrderPurpose
    proposal_id: str | None = None
    position_id: str | None = None
    parent_order_id: str | None = None
    sub_account_id: str | None = None


class SubmitOrderCommand(MutationCommand):
    client_order_id: str | None = None
    side: str
    quantity: DecimalValue
    order_type: str
    limit_price: DecimalValue | None = None
    trigger_price: DecimalValue | None = None
    time_in_force: str | None = None
    post_only: bool = False
    reduce_only: bool = False
    leverage: DecimalValue | None = None
    margin_mode: str | None = None
    position_side: str | None = None


class ModifyOrderCommand(MutationCommand):
    target_client_order_id: str | None = None
    target_venue_order_id: str | None = None
    quantity: DecimalValue | None = None
    limit_price: DecimalValue | None = None
    trigger_price: DecimalValue | None = None
    time_in_force: str | None = None

    @model_validator(mode="after")
    def require_target_and_change(self):
        if not self.target_client_order_id and not self.target_venue_order_id:
            raise ValueError("ModifyOrderCommand requires a target order identity")
        if all(value is None for value in (self.quantity, self.limit_price, self.trigger_price, self.time_in_force)):
            raise ValueError("ModifyOrderCommand requires an authorized replacement field")
        return self


class CancelOrderCommand(MutationCommand):
    target_client_order_id: str | None = None
    target_venue_order_id: str | None = None
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_target(self):
        if not self.target_client_order_id and not self.target_venue_order_id:
            raise ValueError("CancelOrderCommand requires a target order identity")
        return self


class CommandReport(UTAModel):
    identity: UTAAccountIdentity
    command_id: str = Field(min_length=1)
    evidence: CommandEvidence
    received_at: datetime
    client_order_id: str | None = None
    venue_order_id: str | None = None
    error_code: str | None = None
    reason_code: str | None = None
    profile_version: str = Field(min_length=1)


class SubmissionReport(CommandReport):
    order: OrderStatusReport | None = None
