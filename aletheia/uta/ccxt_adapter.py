"""Loop-owned CCXT implementation of the UTA V1 read-only shadow boundary."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
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
    FundingLedgerEntry,
    FillReport,
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


class _AmbiguousAcknowledgementError(RuntimeError):
    pass


class CCXTUTAAdapter:
    """Common loop-owned crypto driver for normalized UTA reads and commands."""

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
        self._connect_lock: asyncio.Lock | None = None
        self._ready = False

    @property
    def identity(self) -> UTAAccountIdentity:
        return self._identity

    @property
    def capabilities(self) -> UTACapabilities:
        profile = self._profile
        return UTACapabilities(
            account_read=CapabilitySupport.SUPPORTED,
            position_read=CapabilitySupport.SUPPORTED,
            order_read=CapabilitySupport.SUPPORTED,
            fill_read=CapabilitySupport.SUPPORTED,
            funding_ledger=CapabilitySupport.SUPPORTED,
            verified_exit=CapabilitySupport.SUPPORTED,
            market_order=self._support(profile.mutation_market_order),
            limit_order=self._support(profile.mutation_limit_order),
            modify_order=self._support(profile.mutation_modify_order),
            cancel_order=self._support(profile.mutation_cancel_order),
            reduce_only=self._support(profile.mutation_reduce_only),
            leverage=self._support(profile.mutation_leverage),
            post_only=self._support(profile.mutation_post_only),
            client_order_id=self._support(profile.mutation_client_order_id),
            trading_permission=CapabilitySupport.UNKNOWN,
            supported_market_types=self._profile.supported_market_types,
            private_order_history_bounds="90_days",
            private_fill_history_bounds="90_days",
            environment_available=True,
            source=f"{self._identity.venue}_{self._identity.profile}_profile_v1",
        )

    @staticmethod
    def _support(enabled: bool) -> CapabilitySupport:
        return CapabilitySupport.SUPPORTED if enabled else CapabilitySupport.UNSUPPORTED

    async def connect(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise UTAError(UTAErrorCode.CONFIG, "cross_loop_adapter_reuse")
        if self._ready:
            return
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            if self._ready:
                return
            await self._connect_on_current_loop(loop)

    async def _connect_on_current_loop(self, loop: asyncio.AbstractEventLoop) -> None:
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
            self._ready = True
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
        self._ready = False
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
    def _history_params(params: dict[str, object]) -> dict[str, object]:
        """Use CCXT's bounded deterministic pagination for private history."""
        return {**params, "paginate": True, "paginationCalls": 10}

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
        except UTAError as exc:
            if exc.code is UTAErrorCode.CONFIG:
                raise
            failed = len(self._profile.balance_requests)
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
        except UTAError as exc:
            if exc.code is UTAErrorCode.CONFIG:
                raise
            return PositionListReport(
                identity=self.identity,
                completeness=ReadCompleteness.UNAVAILABLE,
                scope=query.scope,
                received_at=received_at,
                reason_code="private_positions_unavailable",
                profile_version=self._profile_version,
            )
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
        except UTAError as exc:
            if exc.code is UTAErrorCode.CONFIG:
                raise
            failed_calls = len(self._profile.open_order_requests)
            exchange = None
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
        received_at = self._now()
        if query.scope.sub_account_id is not None:
            return OrderQueryReport(
                identity=self.identity,
                completeness=ReadCompleteness.UNSUPPORTED,
                scope=query.scope,
                received_at=received_at,
                reason_code="sub_account_read_unsupported",
                profile_version=self._profile_version,
            )
        if not query.venue_order_id and query.scope.start_at is None:
            return OrderQueryReport(
                identity=self.identity,
                completeness=ReadCompleteness.PARTIAL,
                scope=query.scope,
                received_at=received_at,
                reason_code="private_order_history_start_required",
                profile_version=self._profile_version,
            )
        if (
            not query.venue_order_id
            and query.scope.start_at < received_at - timedelta(
                days=self._profile.private_history_retention_days
            )
        ):
            return OrderQueryReport(
                identity=self.identity,
                completeness=ReadCompleteness.PARTIAL,
                scope=query.scope,
                received_at=received_at,
                reason_code="private_order_history_retention_expired",
                profile_version=self._profile_version,
            )
        try:
            exchange = await self._connected_exchange()
            symbol = query.scope.instrument.venue_symbol if query.scope.instrument else None
            rows: list[dict[str, object]] = []
            failed = False
            history_bound_reached = False
            if query.venue_order_id:
                try:
                    raw = await self._request(
                        exchange.fetch_order(query.venue_order_id, symbol)
                    )
                    if not isinstance(raw, dict):
                        raise ValueError("invalid CCXT order")
                    rows.append(raw)
                except Exception as exc:
                    if type(exc).__name__ != "OrderNotFound":
                        raise
            else:
                for method_name, requests in (
                    ("fetch_open_orders", self._profile.open_order_requests),
                    ("fetch_closed_orders", self._profile.closed_order_requests),
                ):
                    method = getattr(exchange, method_name)
                    for params in requests:
                        try:
                            result = await self._request(
                                method(
                                    symbol,
                                    int(query.scope.start_at.timestamp() * 1000),
                                    1000,
                                    self._history_params(dict(params)),
                                )
                            )
                            if not isinstance(result, list):
                                raise ValueError("invalid CCXT order history")
                            history_bound_reached = (
                                history_bound_reached or len(result) >= 1000
                            )
                            rows.extend(item for item in result if isinstance(item, dict))
                        except (Exception, TimeoutError):
                            failed = True
            matches = []
            reasons: dict[tuple[str | None, str | None], str] = {}
            parse_failed = False
            for raw in rows:
                try:
                    item = self._profile.order(
                        raw, exchange.markets or {}, self.identity, received_at
                    )
                except (TypeError, ValueError):
                    parse_failed = True
                    continue
                if query.client_order_id and item.client_order_id != query.client_order_id:
                    continue
                if query.venue_order_id and item.venue_order_id != query.venue_order_id:
                    continue
                matches.append(item)
                reason = self._profile.order_reason(raw)
                if reason:
                    reasons[(item.venue_order_id, item.client_order_id)] = reason
            unique = {
                (item.venue_order_id, item.client_order_id): item for item in matches
            }
            if len(unique) > 1:
                return OrderQueryReport(
                    identity=self.identity,
                    completeness=ReadCompleteness.PARTIAL,
                    scope=query.scope,
                    received_at=received_at,
                    reason_code="ambiguous_exact_order_identity",
                    profile_version=self._profile_version,
                )
            partial = failed or parse_failed or history_bound_reached
            found = next(iter(unique.values()), None)
            return OrderQueryReport(
                identity=self.identity,
                completeness=(
                    ReadCompleteness.PARTIAL if partial else ReadCompleteness.COMPLETE
                ),
                scope=query.scope,
                received_at=received_at,
                reason_code=(
                    "private_order_history_bound_reached"
                    if history_bound_reached
                    else "exact_order_surface_partial"
                    if partial
                    else reasons.get((found.venue_order_id, found.client_order_id))
                    if found
                    else None
                ),
                profile_version=self._profile_version,
                found=found,
            )
        except UTAError as exc:
            if exc.code is UTAErrorCode.CONFIG:
                raise
        except (Exception, TimeoutError):
            pass
        return OrderQueryReport(
            identity=self.identity,
            completeness=ReadCompleteness.UNAVAILABLE,
            scope=query.scope,
            received_at=received_at,
            reason_code="private_exact_order_unavailable",
            profile_version=self._profile_version,
        )

    async def get_fills(self, query: FillQuery) -> FillListReport:
        received_at = self._now()
        if query.scope.sub_account_id is not None:
            return FillListReport(
                identity=self.identity,
                completeness=ReadCompleteness.UNSUPPORTED,
                scope=query.scope,
                received_at=received_at,
                reason_code="sub_account_read_unsupported",
                profile_version=self._profile_version,
            )
        if query.scope.start_at is None:
            return FillListReport(
                identity=self.identity,
                completeness=ReadCompleteness.PARTIAL,
                scope=query.scope,
                received_at=received_at,
                reason_code="private_fill_history_start_required",
                profile_version=self._profile_version,
            )
        if query.scope.start_at < received_at - timedelta(
            days=self._profile.private_history_retention_days
        ):
            return FillListReport(
                identity=self.identity,
                completeness=ReadCompleteness.PARTIAL,
                scope=query.scope,
                received_at=received_at,
                reason_code="private_fill_history_retention_expired",
                profile_version=self._profile_version,
            )
        try:
            exchange = await self._connected_exchange()
            symbol = query.scope.instrument.venue_symbol if query.scope.instrument else None
            since = (
                int(query.scope.start_at.timestamp() * 1000)
                if query.scope.start_at
                else None
            )
            rows: list[dict[str, object]] = []
            failed_calls = 0
            history_bound_reached = False
            page_limit = query.scope.limit or 1000
            for params in self._profile.fill_requests:
                try:
                    page = await self._request(
                        exchange.fetch_my_trades(
                            symbol,
                            since,
                            page_limit,
                            self._history_params(dict(params)),
                        )
                    )
                    if not isinstance(page, list):
                        raise ValueError("invalid CCXT fills")
                    history_bound_reached = history_bound_reached or len(page) >= page_limit
                    rows.extend(item for item in page if isinstance(item, dict))
                except (Exception, TimeoutError):
                    failed_calls += 1
            if failed_calls == len(self._profile.fill_requests):
                raise RuntimeError("all private fill surfaces unavailable")
            fills: dict[str, FillReport] = {}
            failed_records = False
            for raw in rows:
                try:
                    item = self._profile.fill(
                        raw, exchange.markets or {}, self.identity, received_at
                    )
                except (TypeError, ValueError):
                    failed_records = True
                    continue
                if query.scope.end_at and item.executed_at > query.scope.end_at:
                    continue
                if query.client_order_id and item.client_order_id != query.client_order_id:
                    continue
                if query.venue_order_id and item.venue_order_id != query.venue_order_id:
                    continue
                previous = fills.get(item.venue_fill_id)
                if previous is not None and previous != item:
                    failed_records = True
                    continue
                fills[item.venue_fill_id] = item
            partial = bool(failed_calls or failed_records or history_bound_reached)
            return FillListReport(
                identity=self.identity,
                completeness=(
                    ReadCompleteness.PARTIAL if partial else ReadCompleteness.COMPLETE
                ),
                scope=query.scope,
                received_at=received_at,
                reason_code=(
                    "private_fill_history_bound_reached"
                    if history_bound_reached
                    else "private_fill_surface_partial"
                    if partial
                    else None
                ),
                profile_version=self._profile_version,
                fills=tuple(sorted(fills.values(), key=lambda item: (item.executed_at, item.venue_fill_id))),
            )
        except UTAError as exc:
            if exc.code is UTAErrorCode.CONFIG:
                raise
        except (Exception, TimeoutError):
            pass
        return FillListReport(
            identity=self.identity,
            completeness=ReadCompleteness.UNAVAILABLE,
            scope=query.scope,
            received_at=received_at,
            reason_code="private_fills_unavailable",
            profile_version=self._profile_version,
        )

    async def get_mass_status(self, query: ReconciliationQuery) -> ExecutionMassStatus:
        orders = []
        order_partial = False
        for client_order_id in query.client_order_ids:
            report = await self.get_order(
                OrderQuery(scope=query.scope, client_order_id=client_order_id)
            )
            order_partial = order_partial or report.completeness is not ReadCompleteness.COMPLETE
            if report.found:
                orders.append(report.found)
        for venue_order_id in query.venue_order_ids:
            report = await self.get_order(
                OrderQuery(scope=query.scope, venue_order_id=venue_order_id)
            )
            order_partial = order_partial or report.completeness is not ReadCompleteness.COMPLETE
            if report.found:
                orders.append(report.found)
        fills, positions = await asyncio.gather(
            self.get_fills(FillQuery(scope=query.scope)),
            self.get_positions(PositionQuery(scope=query.scope)),
        )
        unresolved = []
        if order_partial:
            unresolved.append("orders")
        if fills.completeness is not ReadCompleteness.COMPLETE:
            unresolved.append("fills")
        if positions.completeness is not ReadCompleteness.COMPLETE:
            unresolved.append("positions")
        return ExecutionMassStatus(
            identity=self.identity,
            completeness=(
                ReadCompleteness.COMPLETE
                if not unresolved
                else ReadCompleteness.PARTIAL
            ),
            scope=query.scope,
            received_at=self._now(),
            reason_code="mass_status_partial" if unresolved else None,
            profile_version=self._profile_version,
            orders=tuple({(item.venue_order_id, item.client_order_id): item for item in orders}.values()),
            fills=fills.fills,
            positions=positions.positions,
            unresolved_surfaces=tuple(unresolved),
        )

    async def get_funding_ledger(self, query: FundingLedgerQuery) -> FundingLedgerReport:
        received_at = self._now()
        if query.scope.instrument is None:
            return FundingLedgerReport(
                identity=self.identity,
                completeness=ReadCompleteness.PARTIAL,
                scope=query.scope,
                received_at=received_at,
                reason_code="funding_instrument_required",
                profile_version=self._profile_version,
            )
        if query.scope.start_at is None:
            return FundingLedgerReport(
                identity=self.identity,
                completeness=ReadCompleteness.PARTIAL,
                scope=query.scope,
                received_at=received_at,
                reason_code="funding_history_start_required",
                profile_version=self._profile_version,
            )
        if query.scope.start_at < received_at - timedelta(
            days=self._profile.private_history_retention_days
        ):
            return FundingLedgerReport(
                identity=self.identity,
                completeness=ReadCompleteness.PARTIAL,
                scope=query.scope,
                received_at=received_at,
                reason_code="funding_history_retention_expired",
                profile_version=self._profile_version,
            )
        try:
            exchange = await self._connected_exchange()
            since = (
                int(query.scope.start_at.timestamp() * 1000)
                if query.scope.start_at
                else None
            )
            rows = []
            failed_calls = 0
            history_bound_reached = False
            page_limit = query.scope.limit or 1000
            for params in self._profile.ledger_requests:
                try:
                    page = await self._request(
                        exchange.fetch_ledger(
                            "USDT",
                            since,
                            page_limit,
                            self._history_params(dict(params)),
                        )
                    )
                    if not isinstance(page, list):
                        raise ValueError("invalid CCXT ledger")
                    history_bound_reached = history_bound_reached or len(page) >= page_limit
                    rows.extend(item for item in page if isinstance(item, dict))
                except (Exception, TimeoutError):
                    failed_calls += 1
            if failed_calls == len(self._profile.ledger_requests):
                raise RuntimeError("private funding ledger unavailable")
            events = self._profile.funding_events(rows)
            entries = []
            for event in events:
                if event["symbol"] != query.scope.instrument.canonical_symbol:
                    continue
                settled = datetime.fromtimestamp(
                    int(event["occurred_at_ms"]) / 1000, tz=timezone.utc
                )
                if query.scope.end_at and settled > query.scope.end_at:
                    continue
                entries.append(
                    FundingLedgerEntry(
                        identity=self.identity,
                        instrument=query.scope.instrument,
                        settlement_at=settled,
                        amount=Decimal(str(event["cash_change"])),
                        currency=str(event["currency"]),
                        venue_ledger_id=str(event["event_id"]),
                        source="broker_ledger",
                        source_provenance="ccxt_private_ledger",
                    )
                )
            partial = bool(failed_calls or history_bound_reached)
            return FundingLedgerReport(
                identity=self.identity,
                completeness=(
                    ReadCompleteness.PARTIAL if partial else ReadCompleteness.COMPLETE
                ),
                scope=query.scope,
                received_at=received_at,
                reason_code=(
                    "funding_history_bound_reached"
                    if history_bound_reached
                    else "funding_ledger_partial"
                    if partial
                    else None
                ),
                profile_version=self._profile_version,
                entries=tuple(entries),
            )
        except UTAError as exc:
            if exc.code is UTAErrorCode.CONFIG:
                raise
        except (Exception, TimeoutError, ValueError):
            pass
        return FundingLedgerReport(
            identity=self.identity,
            completeness=ReadCompleteness.UNAVAILABLE,
            scope=query.scope,
            received_at=received_at,
            reason_code="private_funding_ledger_unavailable",
            profile_version=self._profile_version,
        )

    async def verify_position_exit(self, query: VerifiedExitQuery) -> VerifiedExitReport:
        if query.identity != self.identity:
            raise UTAError(
                UTAErrorCode.ACCOUNT_IDENTITY_MISMATCH,
                "account_identity_mismatch",
            )
        scope = QueryScope(
            instrument=query.instrument,
            start_at=query.history_start_at,
            end_at=query.history_end_at,
        )
        positions, fills = await asyncio.gather(
            self.get_positions(PositionQuery(scope=scope)),
            self.get_fills(FillQuery(scope=scope)),
        )
        direction = (
            "long" if query.direction.lower() in {"long", "buy"} else "short"
        )
        current_position = next(
            (
                position
                for position in positions.positions
                if position.side.lower() in (
                    {"long", "buy"} if direction == "long" else {"short", "sell"}
                )
            ),
            None,
        )
        if (
            positions.completeness is ReadCompleteness.COMPLETE
            and current_position is not None
        ):
            return VerifiedExitReport(
                identity=self.identity,
                completeness=ReadCompleteness.COMPLETE,
                scope=scope,
                received_at=self._now(),
                profile_version=self._profile_version,
                state=VerifiedExitState.POSITION_PRESENT,
                local_position_id=query.local_position_id,
                current_position=current_position,
            )
        if positions.completeness is ReadCompleteness.COMPLETE and any(
            position.side.lower() not in {"long", "buy", "short", "sell"}
            for position in positions.positions
        ):
            return VerifiedExitReport(
                identity=self.identity,
                completeness=ReadCompleteness.COMPLETE,
                scope=scope,
                received_at=self._now(),
                reason_code="position_leg_unresolved",
                profile_version=self._profile_version,
                state=VerifiedExitState.INCONCLUSIVE,
                local_position_id=query.local_position_id,
            )
        non_entry_fills = tuple(
            fill
            for fill in fills.fills
            if fill.client_order_id not in query.local_order_ids
            and fill.venue_order_id not in query.local_order_ids
        )
        close_side = "sell" if direction == "long" else "buy"

        def compatible_close(fill: FillReport) -> bool:
            if fill.side.lower() != close_side:
                return False
            position_side = (fill.position_side or "").lower()
            trade_intent = (fill.trade_intent or "").lower()
            if trade_intent in {"open", "entry", "increase"}:
                return False
            if trade_intent and trade_intent not in {
                "close", "exit", "reduce", "decrease",
            }:
                return False
            if position_side in {"long", "short"}:
                return position_side == direction
            return position_side in {"both", "net", "one_way", "oneway"}

        close_fills = tuple(fill for fill in non_entry_fills if compatible_close(fill))
        ambiguous = any(not compatible_close(fill) for fill in non_entry_fills)
        if (
            positions.completeness is ReadCompleteness.COMPLETE
            and fills.completeness is ReadCompleteness.COMPLETE
        ):
            state = (
                VerifiedExitState.VERIFIED_FILL_EXIT
                if not ambiguous
                and close_fills
                and sum((fill.quantity for fill in close_fills), Decimal(0))
                >= query.expected_quantity
                else (
                    VerifiedExitState.CONFIRMED_ABSENT_WITHOUT_FILL
                    if not non_entry_fills
                    else VerifiedExitState.INCONCLUSIVE
                )
            )
            if state is VerifiedExitState.INCONCLUSIVE:
                return VerifiedExitReport(
                    identity=self.identity,
                    completeness=ReadCompleteness.COMPLETE,
                    scope=scope,
                    received_at=self._now(),
                    reason_code="ambiguous_or_partial_exit_fills",
                    profile_version=self._profile_version,
                    state=state,
                    local_position_id=query.local_position_id,
                )
            return VerifiedExitReport(
                identity=self.identity,
                completeness=ReadCompleteness.COMPLETE,
                scope=scope,
                received_at=self._now(),
                profile_version=self._profile_version,
                state=state,
                local_position_id=query.local_position_id,
                exit_fills=close_fills if state is VerifiedExitState.VERIFIED_FILL_EXIT else (),
            )
        return VerifiedExitReport(
            identity=self.identity,
            completeness=ReadCompleteness.PARTIAL,
            scope=scope,
            received_at=self._now(),
            reason_code="verified_exit_evidence_incomplete",
            profile_version=self._profile_version,
            state=VerifiedExitState.INCONCLUSIVE,
            local_position_id=query.local_position_id,
        )

    async def probe_account_authority(self) -> AccountAuthorityProbeReport:
        received_at = self._now()
        try:
            exchange = await self._connected_exchange()
            balances = []
            failed = 0
            for params in self._profile.balance_requests:
                try:
                    raw = await self._request(exchange.fetch_balance(params=dict(params)))
                    if not isinstance(raw, dict):
                        raise ValueError("invalid CCXT balance")
                    balances.append(raw)
                except (Exception, TimeoutError):
                    failed += 1
            if failed == len(self._profile.balance_requests):
                raise RuntimeError("private account authority unavailable")
            account_id, permission = self._profile.account_authority(balances)
            fingerprint = self.identity.account_fingerprint
            if account_id:
                fingerprint = "sha256:" + hashlib.sha256(
                    f"{self.identity.venue}:{self.identity.environment}:{account_id}".encode()
                ).hexdigest()
            support = {
                "granted": CapabilitySupport.SUPPORTED,
                "denied": CapabilitySupport.UNSUPPORTED,
            }.get(permission, CapabilitySupport.UNKNOWN)
            partial = bool(failed)
            return AccountAuthorityProbeReport(
                identity=self.identity,
                completeness=(
                    ReadCompleteness.PARTIAL if partial else ReadCompleteness.COMPLETE
                ),
                scope=QueryScope(),
                received_at=received_at,
                reason_code="account_authority_partial" if partial else None,
                profile_version=self._profile_version,
                authenticated_private_readable=True,
                trading_permission=support,
                account_fingerprint=fingerprint,
                capability_version="1",
            )
        except UTAError as exc:
            if exc.code is UTAErrorCode.CONFIG:
                raise
        except (Exception, TimeoutError):
            pass
        return AccountAuthorityProbeReport(
            identity=self.identity,
            completeness=ReadCompleteness.UNAVAILABLE,
            scope=QueryScope(),
            received_at=received_at,
            reason_code="account_authority_unavailable",
            profile_version=self._profile_version,
            authenticated_private_readable=False,
            trading_permission=CapabilitySupport.UNKNOWN,
            account_fingerprint=self.identity.account_fingerprint,
            capability_version="1",
        )

    def _command_report(
        self,
        command,
        report_type,
        evidence,
        *,
        venue_order_id=None,
        error_code=None,
        reason_code=None,
        order=None,
    ):
        values = dict(
            identity=self.identity,
            command_id=command.command_id,
            evidence=evidence,
            received_at=self._now(),
            client_order_id=getattr(command, "client_order_id", None)
            or getattr(command, "target_client_order_id", None),
            venue_order_id=venue_order_id
            or getattr(command, "target_venue_order_id", None),
            error_code=error_code,
            reason_code=reason_code,
            profile_version=self._profile_version,
        )
        if report_type is SubmissionReport:
            values["order"] = order
        return report_type(**values)

    def _not_sent(
        self,
        command,
        report_type,
        *,
        reason="read_only_shadow_mutation_disabled",
        error_code=UTAErrorCode.NOT_SUPPORTED,
    ):
        identity_match = command.expected_account == self.identity
        return self._command_report(
            command,
            report_type,
            CommandEvidence.NOT_SENT,
            error_code=(
                error_code.value
                if identity_match
                else UTAErrorCode.ACCOUNT_IDENTITY_MISMATCH.value
            ),
            reason_code=(
                reason
                if identity_match
                else "account_identity_mismatch"
            ),
        )

    @staticmethod
    def _venue_error_code(exc: BaseException) -> UTAErrorCode:
        names = {item.__name__ for item in type(exc).__mro__}
        if "AuthenticationError" in names:
            return UTAErrorCode.AUTH
        if "PermissionDenied" in names:
            return UTAErrorCode.PERMISSION
        if "InsufficientFunds" in names:
            return UTAErrorCode.INSUFFICIENT_FUNDS
        if names & {"InvalidOrder", "BadSymbol"}:
            return UTAErrorCode.INVALID_ORDER
        if "RateLimitExceeded" in names:
            return UTAErrorCode.RATE_LIMIT
        return UTAErrorCode.VENUE_REJECTED

    @staticmethod
    def _command_error_kind(exc: BaseException) -> str:
        """Classify a driver error without importing that concrete driver."""
        current: BaseException | None = exc
        while current is not None:
            names = {item.__name__ for item in type(current).__mro__}
            lowered = {name.lower() for name in names}
            if (
                names & {
                    "BadResponse",
                    "NetworkError",
                    "RateLimitExceeded",
                    "_AmbiguousAcknowledgementError",
                }
                or any(
                    marker in name
                    for name in lowered
                    for marker in (
                        "timeout",
                        "connection",
                        "transport",
                        "network",
                        "brokenpipe",
                        "protocolerror",
                    )
                )
            ):
                return "ambiguous"
            if "ExchangeError" in names:
                return "rejected"
            current = current.__cause__ or current.__context__
        return "unknown"

    def _mutation_failure(self, command, report_type, exc, *, request_started):
        error_kind = self._command_error_kind(exc)
        if request_started and error_kind != "rejected":
            return self._command_report(
                command,
                report_type,
                CommandEvidence.AMBIGUOUS,
                error_code=UTAErrorCode.AMBIGUOUS_COMMAND.value,
                reason_code="command_acknowledgement_unknown",
            )
        if request_started:
            code = self._venue_error_code(exc)
            return self._command_report(
                command,
                report_type,
                CommandEvidence.VENUE_REJECTED,
                error_code=code.value,
                reason_code="venue_rejected_command",
            )
        code = (
            self._venue_error_code(exc)
            if error_kind == "rejected"
            else UTAErrorCode.TIMEOUT
            if isinstance(exc, TimeoutError)
            else UTAErrorCode.NETWORK
        )
        return self._command_report(
            command,
            report_type,
            CommandEvidence.NOT_SENT,
            error_code=code.value,
            reason_code="command_not_sent",
        )

    def _validate_mutation(self, command, report_type, capability: bool):
        if command.expected_account != self.identity:
            return self._not_sent(command, report_type)
        if command.instrument.market_type not in self._profile.supported_market_types:
            return self._not_sent(
                command, report_type, reason="instrument_market_type_unsupported"
            )
        if not capability:
            return self._not_sent(command, report_type)
        return None

    async def submit_order(self, command: SubmitOrderCommand) -> SubmissionReport:
        capability = (
            self._profile.mutation_market_order
            if command.order_type == "market"
            else self._profile.mutation_limit_order
            if command.order_type == "limit"
            else False
        )
        denied = self._validate_mutation(command, SubmissionReport, capability)
        if denied:
            return denied
        if command.order_type == "limit" and command.limit_price is None:
            return self._not_sent(
                command,
                SubmissionReport,
                reason="limit_price_required",
                error_code=UTAErrorCode.INVALID_ORDER,
            )
        if (
            command.leverage is not None
            and self._profile.integer_leverage
            and command.leverage != command.leverage.to_integral_value()
        ):
            return self._not_sent(
                command,
                SubmissionReport,
                reason="integer_leverage_required",
                error_code=UTAErrorCode.INVALID_ORDER,
            )
        exchange = None
        try:
            exchange = await self._connected_exchange()
            await self._request(self._profile.prepare_submit(exchange, command))
        except (Exception, TimeoutError) as exc:
            return self._mutation_failure(
                command, SubmissionReport, exc, request_started=False
            )
        request_started = False
        try:
            args = self._profile.submit_request(command)
            request_started = True
            raw = await self._request(exchange.create_order(*args))
            if not isinstance(raw, dict):
                raise _AmbiguousAcknowledgementError
            venue_order_id = str(raw.get("id") or "").strip()
            if not venue_order_id:
                raise _AmbiguousAcknowledgementError
            try:
                order = self._profile.order(
                    {
                        **raw,
                        "symbol": raw.get("symbol")
                        or command.instrument.venue_symbol,
                    },
                    exchange.markets or {},
                    self.identity,
                    self._now(),
                )
            except (TypeError, ValueError):
                # A stable venue id proves acknowledgement, but malformed
                # aggregate economics never become Fill or Order facts.
                order = None
            return self._command_report(
                command,
                SubmissionReport,
                CommandEvidence.ACKNOWLEDGED,
                venue_order_id=venue_order_id,
                order=order,
            )
        except (Exception, TimeoutError) as exc:
            return self._mutation_failure(
                command, SubmissionReport, exc, request_started=request_started
            )

    async def modify_order(self, command: ModifyOrderCommand) -> CommandReport:
        denied = self._validate_mutation(
            command, CommandReport, self._profile.mutation_modify_order
        )
        if denied:
            return denied
        try:
            exchange = await self._connected_exchange()
            exact = await self.get_order(OrderQuery(
                scope=QueryScope(instrument=command.instrument),
                client_order_id=command.target_client_order_id,
                venue_order_id=command.target_venue_order_id,
            ))
            if exact.completeness is not ReadCompleteness.COMPLETE or exact.found is None:
                return self._not_sent(
                    command, CommandReport, reason="modify_target_unavailable"
                )
        except (Exception, TimeoutError) as exc:
            return self._mutation_failure(
                command, CommandReport, exc, request_started=False
            )
        request_started = False
        try:
            try:
                args = self._profile.modify_request(command, exact.found)
            except UTAError as exc:
                if exc.command_evidence is CommandEvidence.NOT_SENT:
                    return self._not_sent(
                        command,
                        CommandReport,
                        reason=exc.reason,
                        error_code=exc.code,
                    )
                raise
            request_started = True
            raw = await self._request(exchange.edit_order(*args))
            if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
                raise _AmbiguousAcknowledgementError
            return self._command_report(
                command,
                CommandReport,
                CommandEvidence.ACKNOWLEDGED,
                venue_order_id=str(raw["id"]),
            )
        except (Exception, TimeoutError) as exc:
            return self._mutation_failure(
                command, CommandReport, exc, request_started=request_started
            )

    async def cancel_order(self, command: CancelOrderCommand) -> CommandReport:
        denied = self._validate_mutation(
            command, CommandReport, self._profile.mutation_cancel_order
        )
        if denied:
            return denied
        try:
            exchange = await self._connected_exchange()
        except (Exception, TimeoutError) as exc:
            return self._mutation_failure(
                command, CommandReport, exc, request_started=False
            )
        request_started = False
        try:
            args = self._profile.cancel_request(command)
            request_started = True
            raw = await self._request(exchange.cancel_order(*args))
            if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
                raise _AmbiguousAcknowledgementError
            return self._command_report(
                command,
                CommandReport,
                CommandEvidence.ACKNOWLEDGED,
                venue_order_id=str(raw["id"]),
            )
        except (Exception, TimeoutError) as exc:
            return self._mutation_failure(
                command, CommandReport, exc, request_started=request_started
            )
