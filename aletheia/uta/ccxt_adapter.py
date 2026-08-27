"""Loop-owned CCXT implementation of the UTA V1 read-only shadow boundary."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from importlib.metadata import version
from typing import Any

from aletheia.uta.errors import UTAError, UTAErrorCode
from aletheia.uta.models import (
    AccountAuthorityProbeReport,
    AccountSnapshot,
    CancelOrderCommand,
    CapabilitySupport,
    CommandEvidence,
    CommandReport,
    ExecutionMassStatus,
    FillListReport,
    FillQuery,
    FundingLedgerQuery,
    FundingLedgerReport,
    ModifyOrderCommand,
    OrderListReport,
    OrderQuery,
    OrderQueryReport,
    PositionListReport,
    PositionQuery,
    QueryScope,
    ReadCompleteness,
    ReconciliationQuery,
    SubmissionReport,
    SubmitOrderCommand,
    UTAAccountIdentity,
    UTACapabilities,
    UTAHealth,
    UTAHealthReport,
    UTAReach,
    VerifiedExitQuery,
    VerifiedExitReport,
    VerifiedExitState,
)
from aletheia.uta.profiles.base import CCXTProfile


class CCXTUTAAdapter:
    """Common crypto driver. Phase 1 exposes reads and refuses every mutation."""

    def __init__(
        self,
        identity: UTAAccountIdentity,
        profile: CCXTProfile,
        *,
        request_timeout: float = 15.0,
        close_timeout: float = 5.0,
    ) -> None:
        profile.validate_identity(identity)
        if identity.adapter_implementation != type(self).__name__:
            raise ValueError("UTA identity adapter implementation mismatch")
        if identity.adapter_version != "1":
            raise ValueError("UTA identity adapter version mismatch")
        if request_timeout <= 0 or close_timeout <= 0:
            raise ValueError("UTA timeouts must be positive")
        self._identity = identity
        self._profile = profile
        self._request_timeout = request_timeout
        self._close_timeout = close_timeout
        self._exchange: Any | None = None
        self._session: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def identity(self) -> UTAAccountIdentity:
        return self._identity

    @property
    def capabilities(self) -> UTACapabilities:
        return UTACapabilities(
            account_read=CapabilitySupport.SUPPORTED,
            position_read=CapabilitySupport.SUPPORTED,
            # Open-order lists are implemented in Phase 1; exact order evidence is
            # still Phase 3, so the aggregate capability cannot claim full support.
            order_read=CapabilitySupport.UNKNOWN,
            fill_read=CapabilitySupport.UNSUPPORTED,
            funding_ledger=CapabilitySupport.UNSUPPORTED,
            verified_exit=CapabilitySupport.UNSUPPORTED,
            market_order=CapabilitySupport.UNSUPPORTED,
            limit_order=CapabilitySupport.UNSUPPORTED,
            modify_order=CapabilitySupport.UNSUPPORTED,
            cancel_order=CapabilitySupport.UNSUPPORTED,
            trading_permission=CapabilitySupport.UNKNOWN,
            supported_market_types=self._profile.supported_market_types,
            environment_available=True,
            source=f"{self._identity.venue}_{self._identity.profile}_profile_v1",
        )

    async def connect(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise UTAError(UTAErrorCode.CONFIG, "cross_loop_adapter_reuse")
        if self._exchange is not None:
            return
        if self._identity.ccxt_version not in {None, version("ccxt")}:
            raise UTAError(UTAErrorCode.CONFIG, "ccxt_version_mismatch")
        from aletheia.net import make_ccxt

        exchange, session = make_ccxt(
            self._profile.exchange_name,
            **self._profile.client_options(),
        )
        self._loop = loop
        self._exchange = exchange
        self._session = session
        try:
            self._profile.configure_before_request(exchange)
            async with asyncio.timeout(self._request_timeout):
                await exchange.load_markets()
        except TimeoutError as exc:
            await self.close()
            raise UTAError(
                UTAErrorCode.TIMEOUT,
                "market_load_timeout",
                retryable=True,
            ) from exc
        except Exception as exc:
            await self.close()
            raise UTAError(
                UTAErrorCode.NETWORK,
                "market_load_unavailable",
                retryable=True,
            ) from exc

    async def close(self) -> None:
        if self._exchange is None and self._session is None:
            return
        if self._loop is not asyncio.get_running_loop():
            raise UTAError(UTAErrorCode.CONFIG, "cross_loop_adapter_close")
        exchange, session = self._exchange, self._session
        self._exchange = None
        self._session = None
        for resource in (exchange, session):
            if resource is None or getattr(resource, "closed", False):
                continue
            try:
                async with asyncio.timeout(self._close_timeout):
                    await resource.close()
            except (Exception, TimeoutError):
                # Teardown is bounded and cannot alter any report already returned.
                continue

    async def _connected_exchange(self) -> Any:
        await self.connect()
        assert self._exchange is not None
        return self._exchange

    async def _request(self, awaitable):
        async with asyncio.timeout(self._request_timeout):
            return await awaitable

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @property
    def _profile_version(self) -> str:
        return self._profile.profile_version

    async def health(self) -> UTAHealthReport:
        account = await self.get_account()
        if account.completeness is ReadCompleteness.COMPLETE:
            health, reach = UTAHealth.HEALTHY, UTAReach.READABLE
        elif account.completeness is ReadCompleteness.PARTIAL:
            health, reach = UTAHealth.DEGRADED, UTAReach.READABLE
        else:
            health, reach = UTAHealth.OFFLINE, UTAReach.DOWN
        return UTAHealthReport(
            identity=self.identity,
            health=health,
            reach=reach,
            completeness=account.completeness,
            received_at=account.received_at,
            source_at=account.source_at,
            reason_code=account.reason_code,
            profile_version=self._profile_version,
        )

    async def get_account(self, sub_account_id: str | None = None) -> AccountSnapshot:
        received_at = self._now()
        scope = QueryScope(sub_account_id=sub_account_id)
        if sub_account_id is not None:
            return AccountSnapshot(
                identity=self.identity,
                completeness=ReadCompleteness.UNSUPPORTED,
                scope=scope,
                received_at=received_at,
                reason_code="sub_account_read_unsupported",
                profile_version=self._profile_version,
                settlement_currency="USDT",
            )
        rows: list[dict[str, object]] = []
        successful = 0
        failed = 0
        try:
            exchange = await self._connected_exchange()
            for params in self._profile.balance_requests:
                try:
                    raw = await self._request(exchange.fetch_balance(dict(params)))
                    if not isinstance(raw, dict):
                        raise ValueError("invalid CCXT balance")
                    rows.append(raw)
                    successful += 1
                except (Exception, TimeoutError):
                    failed += 1
                    # Preserve request position: profile mappings such as Bitget
                    # Classic must not mistake a surviving mix response for spot.
                    rows.append({})
        except (Exception, TimeoutError):
            failed = len(self._profile.balance_requests)
        if not successful:
            return AccountSnapshot(
                identity=self.identity,
                completeness=ReadCompleteness.UNAVAILABLE,
                scope=scope,
                received_at=received_at,
                reason_code="private_account_unavailable",
                profile_version=self._profile_version,
                settlement_currency="USDT",
            )
        try:
            values, balances, reason = self._profile.account_values(rows)
        except (TypeError, ValueError):
            return AccountSnapshot(
                identity=self.identity,
                completeness=ReadCompleteness.UNAVAILABLE,
                scope=scope,
                received_at=received_at,
                reason_code="private_account_parse_failed",
                profile_version=self._profile_version,
                settlement_currency="USDT",
            )
        if failed:
            reason = "account_surface_partial"
        completeness = ReadCompleteness.PARTIAL if reason else ReadCompleteness.COMPLETE
        return AccountSnapshot(
            identity=self.identity,
            completeness=completeness,
            scope=scope,
            received_at=received_at,
            reason_code=reason,
            profile_version=self._profile_version,
            settlement_currency="USDT",
            balances=balances,
            **values,
        )

    async def get_positions(self, query: PositionQuery | None = None) -> PositionListReport:
        query = query or PositionQuery()
        received_at = self._now()
        if query.include_closed or query.scope.sub_account_id is not None:
            return PositionListReport(
                identity=self.identity,
                completeness=ReadCompleteness.UNSUPPORTED,
                scope=query.scope,
                received_at=received_at,
                reason_code="position_scope_unsupported",
                profile_version=self._profile_version,
            )
        try:
            exchange = await self._connected_exchange()
            raw_positions = await self._request(
                exchange.fetch_positions(params=dict(self._profile.position_params))
            )
            if not isinstance(raw_positions, list):
                raise ValueError("invalid CCXT positions")
        except (Exception, TimeoutError):
            return PositionListReport(
                identity=self.identity,
                completeness=ReadCompleteness.UNAVAILABLE,
                scope=query.scope,
                received_at=received_at,
                reason_code="private_positions_unavailable",
                profile_version=self._profile_version,
            )
        positions = []
        failed = False
        for raw in raw_positions:
            if not isinstance(raw, dict):
                failed = True
                continue
            try:
                mapped = self._profile.position(
                    raw, exchange.markets or {}, self.identity, received_at
                )
                if mapped is not None:
                    positions.append(mapped)
            except (TypeError, ValueError):
                failed = True
        if query.side:
            positions = [item for item in positions if item.side == query.side]
        if query.scope.instrument:
            expected = query.scope.instrument
            positions = [
                item
                for item in positions
                if (
                    item.instrument.canonical_symbol,
                    item.instrument.market_type,
                )
                == (expected.canonical_symbol, expected.market_type)
            ]
        source_at = max(
            (item.source_at for item in positions if item.source_at), default=None
        )
        return PositionListReport(
            identity=self.identity,
            completeness=(ReadCompleteness.PARTIAL if failed else ReadCompleteness.COMPLETE),
            scope=query.scope,
            received_at=received_at,
            source_at=source_at,
            reason_code="position_record_parse_failed" if failed else None,
            profile_version=self._profile_version,
            positions=tuple(positions),
        )

    async def get_open_orders(self, query: OrderQuery | None = None) -> OrderListReport:
        query = query or OrderQuery()
        received_at = self._now()
        if query.scope.sub_account_id is not None:
            return OrderListReport(
                identity=self.identity,
                completeness=ReadCompleteness.UNSUPPORTED,
                scope=query.scope,
                received_at=received_at,
                reason_code="sub_account_read_unsupported",
                profile_version=self._profile_version,
            )
        raw_orders: list[dict[str, object]] = []
        failed_calls = 0
        try:
            exchange = await self._connected_exchange()
            for params in self._profile.open_order_requests:
                try:
                    rows = await self._request(
                        exchange.fetch_open_orders(params=dict(params))
                    )
                    if not isinstance(rows, list):
                        raise ValueError("invalid CCXT orders")
                    raw_orders.extend(rows)
                except (Exception, TimeoutError):
                    failed_calls += 1
        except (Exception, TimeoutError):
            failed_calls = len(self._profile.open_order_requests)
            exchange = None
        if failed_calls == len(self._profile.open_order_requests):
            return OrderListReport(
                identity=self.identity,
                completeness=ReadCompleteness.UNAVAILABLE,
                scope=query.scope,
                received_at=received_at,
                reason_code="private_open_orders_unavailable",
                profile_version=self._profile_version,
            )
        orders = []
        failed_records = False
        seen: set[tuple[str | None, str, str]] = set()
        for raw in raw_orders:
            if not isinstance(raw, dict):
                failed_records = True
                continue
            try:
                item = self._profile.order(
                    raw, exchange.markets or {}, self.identity, received_at
                )
                if not item.venue_order_id and not item.client_order_id:
                    raise ValueError("open order has no stable identity")
                key = (
                    item.venue_order_id,
                    item.instrument.canonical_symbol,
                    item.instrument.market_type,
                )
                if key not in seen:
                    seen.add(key)
                    orders.append(item)
            except (TypeError, ValueError):
                failed_records = True
        if query.client_order_id:
            orders = [item for item in orders if item.client_order_id == query.client_order_id]
        if query.venue_order_id:
            orders = [item for item in orders if item.venue_order_id == query.venue_order_id]
        if query.scope.instrument:
            expected = query.scope.instrument
            orders = [
                item
                for item in orders
                if (
                    item.instrument.canonical_symbol,
                    item.instrument.market_type,
                )
                == (expected.canonical_symbol, expected.market_type)
            ]
        partial = bool(failed_calls or failed_records)
        return OrderListReport(
            identity=self.identity,
            completeness=(ReadCompleteness.PARTIAL if partial else ReadCompleteness.COMPLETE),
            scope=query.scope,
            received_at=received_at,
            source_at=max(
                (item.source_at for item in orders if item.source_at), default=None
            ),
            reason_code="open_order_surface_partial" if partial else None,
            profile_version=self._profile_version,
            orders=tuple(orders),
        )

    async def get_order(self, query: OrderQuery) -> OrderQueryReport:
        query.require_exact()
        return OrderQueryReport(
            identity=self.identity,
            completeness=ReadCompleteness.UNSUPPORTED,
            scope=query.scope,
            received_at=self._now(),
            reason_code="phase_three_evidence_unsupported",
            profile_version=self._profile_version,
        )

    async def get_fills(self, query: FillQuery) -> FillListReport:
        return FillListReport(
            identity=self.identity,
            completeness=ReadCompleteness.UNSUPPORTED,
            scope=query.scope,
            received_at=self._now(),
            reason_code="phase_three_evidence_unsupported",
            profile_version=self._profile_version,
        )

    async def get_mass_status(self, query: ReconciliationQuery) -> ExecutionMassStatus:
        return ExecutionMassStatus(
            identity=self.identity,
            completeness=ReadCompleteness.UNSUPPORTED,
            scope=query.scope,
            received_at=self._now(),
            reason_code="phase_three_evidence_unsupported",
            profile_version=self._profile_version,
            unsupported_surfaces=("orders", "fills", "positions"),
        )

    async def get_funding_ledger(self, query: FundingLedgerQuery) -> FundingLedgerReport:
        return FundingLedgerReport(
            identity=self.identity,
            completeness=ReadCompleteness.UNSUPPORTED,
            scope=query.scope,
            received_at=self._now(),
            reason_code="phase_three_evidence_unsupported",
            profile_version=self._profile_version,
        )

    async def verify_position_exit(self, query: VerifiedExitQuery) -> VerifiedExitReport:
        if query.identity != self.identity:
            raise UTAError(
                UTAErrorCode.ACCOUNT_IDENTITY_MISMATCH,
                "account_identity_mismatch",
            )
        return VerifiedExitReport(
            identity=self.identity,
            completeness=ReadCompleteness.UNSUPPORTED,
            scope=QueryScope(
                instrument=query.instrument,
                start_at=query.history_start_at,
                end_at=query.history_end_at,
            ),
            received_at=self._now(),
            reason_code="phase_three_evidence_unsupported",
            profile_version=self._profile_version,
            state=VerifiedExitState.INCONCLUSIVE,
            local_position_id=query.local_position_id,
        )

    async def probe_account_authority(self) -> AccountAuthorityProbeReport:
        return AccountAuthorityProbeReport(
            identity=self.identity,
            completeness=ReadCompleteness.UNSUPPORTED,
            scope=QueryScope(),
            received_at=self._now(),
            reason_code="phase_three_evidence_unsupported",
            profile_version=self._profile_version,
            authenticated_private_readable=False,
            trading_permission=CapabilitySupport.UNKNOWN,
            account_fingerprint=self.identity.account_fingerprint,
            capability_version="1",
        )

    def _not_sent(self, command, report_type):
        identity_match = command.expected_account == self.identity
        return report_type(
            identity=self.identity,
            command_id=command.command_id,
            evidence=CommandEvidence.NOT_SENT,
            received_at=self._now(),
            client_order_id=getattr(command, "client_order_id", None)
            or getattr(command, "target_client_order_id", None),
            venue_order_id=getattr(command, "target_venue_order_id", None),
            error_code=(
                UTAErrorCode.NOT_SUPPORTED.value
                if identity_match
                else UTAErrorCode.ACCOUNT_IDENTITY_MISMATCH.value
            ),
            reason_code=(
                "read_only_shadow_mutation_disabled"
                if identity_match
                else "account_identity_mismatch"
            ),
            profile_version=self._profile_version,
        )

    async def submit_order(self, command: SubmitOrderCommand) -> SubmissionReport:
        return self._not_sent(command, SubmissionReport)

    async def modify_order(self, command: ModifyOrderCommand) -> CommandReport:
        return self._not_sent(command, CommandReport)

    async def cancel_order(self, command: CancelOrderCommand) -> CommandReport:
        return self._not_sent(command, CommandReport)
