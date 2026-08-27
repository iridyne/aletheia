"""Shared CCXT normalization; venue profiles only declare real differences."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from aletheia.data.symbols import to_canonical
from aletheia.uta.models import (
    AccountBalance,
    FillReport,
    InstrumentIdentity,
    OrderLifecycle,
    OrderStatusReport,
    PositionStatusReport,
    UTAAccountIdentity,
)


def decimal_value(value: object) -> Decimal | None:
    """Convert a CCXT normalized scalar before it reaches a UTA model."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid decimal") from exc
    if not result.is_finite():
        raise ValueError("non-finite decimal")
    return result


def utc_from_milliseconds(value: object, received_at: datetime) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        result = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (OSError, OverflowError, TypeError, ValueError) as exc:
        raise ValueError("invalid venue timestamp") from exc
    if result > received_at:
        raise ValueError("future venue timestamp")
    return result


class CCXTProfile:
    """Static configuration plus raw-to-canonical venue translation."""

    profile_version = "1"
    expected_venue = ""
    expected_environment = ""
    expected_profile = ""
    expected_execution_mode = ""
    exchange_name = ""
    sandbox = False
    balance_requests: tuple[dict[str, object], ...] = ({},)
    position_params: dict[str, object] = {}
    open_order_requests: tuple[dict[str, object], ...] = ({},)
    closed_order_requests: tuple[dict[str, object], ...] = ({},)
    fill_requests: tuple[dict[str, object], ...] = ({},)
    ledger_requests: tuple[dict[str, object], ...] = ({},)
    supported_market_types: tuple[str, ...] = ("usdt_futures",)
    private_history_retention_days = 90
    mutation_market_order = False
    mutation_limit_order = False
    mutation_modify_order = False
    mutation_cancel_order = False
    mutation_reduce_only = False
    mutation_leverage = False
    mutation_post_only = False
    mutation_client_order_id = False
    integer_leverage = False

    def __init__(self, **client_options: object) -> None:
        credential_fields = (
            client_options.get("apiKey"),
            client_options.get("secret"),
            client_options.get("password"),
        )
        if any(
            value is not None
            and (not isinstance(value, str) or not value.strip())
            for value in credential_fields
        ):
            raise ValueError("UTA profile credentials are incomplete")
        self._client_options = dict(client_options)

    def validate_identity(self, identity: UTAAccountIdentity) -> None:
        expected = (
            self.expected_execution_mode,
            self.expected_venue,
            self.expected_environment,
            self.expected_profile,
        )
        actual = (
            identity.execution_mode,
            identity.venue,
            identity.environment,
            identity.profile,
        )
        if actual != expected:
            raise ValueError("UTA identity does not match the selected venue profile")

    def client_options(self) -> dict[str, object]:
        return deepcopy(self._client_options)

    def configure_before_request(self, exchange: Any) -> None:
        if self.sandbox:
            exchange.set_sandbox_mode(True)

    async def prepare_submit(self, exchange: Any, command: Any) -> None:
        """Apply profile-owned pre-send settings; the base profile is read-only."""
        raise NotImplementedError

    def submit_request(self, command: Any) -> tuple[str, str, str, object, object, dict]:
        raise NotImplementedError

    def modify_request(
        self, command: Any, current: Any
    ) -> tuple[object, str, str, str, object, object, dict]:
        raise NotImplementedError

    def cancel_request(self, command: Any) -> tuple[object, str, dict]:
        raise NotImplementedError

    def account_values(
        self, balances: list[dict[str, object]]
    ) -> tuple[dict[str, Decimal | None], tuple[AccountBalance, ...], str | None]:
        currencies: dict[str, dict[str, Decimal]] = {}
        for raw in balances:
            for field in ("total", "free", "used"):
                values = raw.get(field) or {}
                if not isinstance(values, dict):
                    raise ValueError("invalid CCXT balance map")
                for currency, value in values.items():
                    parsed = decimal_value(value)
                    if parsed is not None:
                        bucket = currencies.setdefault(str(currency).upper(), {})
                        bucket[field] = bucket.get(field, Decimal(0)) + parsed
        normalized = tuple(
            AccountBalance(
                currency=currency,
                total=values.get("total"),
                available=values.get("free"),
                used=values.get("used"),
                frozen=values.get("used"),
            )
            for currency, values in sorted(currencies.items())
        )
        settlement = next((item for item in normalized if item.currency == "USDT"), None)
        reason = None
        if any(
            item.currency != "USDT" and item.total not in {None, Decimal(0)}
            for item in normalized
        ):
            reason = "unpriced_asset_balance"
        if settlement is not None and settlement.total is None:
            reason = "settlement_total_missing"
        has_nonzero_total = any(
            item.total not in {None, Decimal(0)} for item in normalized
        )
        empty = None if has_nonzero_total else Decimal(0)
        values = {
            "equity": settlement.total if settlement else empty,
            "available": settlement.available if settlement else empty,
            "used": settlement.used if settlement else empty,
            "frozen": settlement.frozen if settlement else empty,
            "margin": None,
            "unrealized_pnl": None,
            "maintenance_margin": None,
        }
        return values, normalized, reason

    def instrument(self, raw: dict[str, object], markets: dict[str, Any]) -> InstrumentIdentity:
        venue_symbol = str(raw.get("symbol") or "").strip()
        if not venue_symbol:
            raise ValueError("missing venue symbol")
        market = markets.get(venue_symbol) or {}
        canonical = to_canonical(venue_symbol)
        base, separator, quote = canonical.partition("/")
        if not separator or not base or not quote:
            raise ValueError("invalid venue symbol")
        market_type = str(market.get("type") or raw.get("type") or "")
        canonical_type = (
            "usdt_futures"
            if market_type in {"swap", "future"} or ":" in venue_symbol
            else "spot"
        )
        raw_contract_size = raw.get("contractSize")
        contract_size = decimal_value(
            raw_contract_size
            if raw_contract_size not in {None, ""}
            else market.get("contractSize")
        )
        if contract_size is not None and contract_size <= 0:
            raise ValueError("invalid contract size")
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
        venue_id = market.get("id") or info.get("symbol") or info.get("instId")
        return InstrumentIdentity(
            canonical_symbol=canonical,
            venue_symbol=venue_symbol,
            venue_instrument_id=str(venue_id) if venue_id else None,
            market_type=canonical_type,
            base_currency=str(market.get("base") or base),
            quote_currency=str(market.get("quote") or quote),
            settlement_currency=str(market.get("settle") or quote),
            contract_size=contract_size,
            linear=market.get("linear"),
            inverse=market.get("inverse"),
            position_side=str(raw.get("side")) if raw.get("side") else None,
            position_mode=str(info.get("posMode")) if info.get("posMode") else None,
        )

    def position(
        self,
        raw: dict[str, object],
        markets: dict[str, Any],
        identity: UTAAccountIdentity,
        received_at: datetime,
    ) -> PositionStatusReport | None:
        instrument = self.instrument(raw, markets)
        contracts = decimal_value(raw.get("contracts")) or Decimal(0)
        quantity = abs(contracts) * (instrument.contract_size or Decimal(1))
        if quantity == 0:
            return None
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
        opened_at = utc_from_milliseconds(
            info.get("cTime") or info.get("openTime"),
            received_at,
        )
        source_at = utc_from_milliseconds(
            raw.get("timestamp") or info.get("uTime") or info.get("updateTime"),
            received_at,
        )
        position_id = raw.get("id") or info.get("posId") or info.get("positionId")
        leverage = decimal_value(raw.get("leverage"))
        if leverage is not None and leverage <= 0:
            leverage = None
        entry_price = decimal_value(raw.get("entryPrice"))
        mark_price = decimal_value(raw.get("markPrice"))
        liquidation_price = decimal_value(raw.get("liquidationPrice"))
        return PositionStatusReport(
            identity=identity,
            instrument=instrument,
            venue_position_id=str(position_id) if position_id else None,
            side=str(raw.get("side") or info.get("holdSide") or info.get("posSide") or "unknown"),
            quantity=quantity,
            entry_price=entry_price if entry_price and entry_price > 0 else None,
            mark_price=mark_price if mark_price and mark_price > 0 else None,
            unrealized_pnl=decimal_value(raw.get("unrealizedPnl")),
            leverage=leverage,
            margin_mode=str(raw.get("marginMode")) if raw.get("marginMode") else None,
            liquidation_price=(
                liquidation_price
                if liquidation_price and liquidation_price > 0
                else None
            ),
            opened_at=opened_at,
            opened_at_provenance="exchange_reported" if opened_at else None,
            source_at=source_at,
            received_at=received_at,
        )

    def order(
        self,
        raw: dict[str, object],
        markets: dict[str, Any],
        identity: UTAAccountIdentity,
        received_at: datetime,
    ) -> OrderStatusReport:
        instrument = self.instrument(raw, markets)
        multiplier = instrument.contract_size or Decimal(1)
        amount = (decimal_value(raw.get("amount")) or Decimal(0)) * multiplier
        filled = (decimal_value(raw.get("filled")) or Decimal(0)) * multiplier
        remaining_raw = decimal_value(raw.get("remaining"))
        if amount <= 0 or filled < 0 or (remaining_raw is not None and remaining_raw < 0):
            raise ValueError("invalid order quantities")
        state = {
            "new": OrderLifecycle.OPEN,
            "open": OrderLifecycle.OPEN,
            "partially_filled": OrderLifecycle.PARTIALLY_FILLED,
            "closed": OrderLifecycle.FILLED,
            "filled": OrderLifecycle.FILLED,
            "canceled": OrderLifecycle.CANCELED,
            "cancelled": OrderLifecycle.CANCELED,
            "expired": OrderLifecycle.EXPIRED,
            "rejected": OrderLifecycle.REJECTED,
        }.get(str(raw.get("status") or "").lower(), OrderLifecycle.UNKNOWN)
        if state is OrderLifecycle.OPEN and filled > 0:
            state = OrderLifecycle.PARTIALLY_FILLED
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
        venue_at = utc_from_milliseconds(raw.get("timestamp"), received_at)
        source_at = utc_from_milliseconds(
            raw.get("lastTradeTimestamp") or info.get("uTime") or raw.get("timestamp"),
            received_at,
        )
        price = decimal_value(raw.get("price"))
        trigger_price = decimal_value(raw.get("stopPrice") or raw.get("triggerPrice"))
        return OrderStatusReport(
            identity=identity,
            instrument=instrument,
            client_order_id=str(raw.get("clientOrderId")) if raw.get("clientOrderId") else None,
            venue_order_id=str(raw.get("id")) if raw.get("id") else None,
            side=str(raw.get("side") or "unknown"),
            order_type=str(raw.get("type") or "unknown"),
            original_quantity=amount,
            filled_quantity=filled,
            remaining_quantity=(remaining_raw * multiplier if remaining_raw is not None else None),
            price=price if price and price > 0 else None,
            trigger_price=(trigger_price if trigger_price and trigger_price > 0 else None),
            time_in_force=str(raw.get("timeInForce")) if raw.get("timeInForce") else None,
            post_only=raw.get("postOnly") if isinstance(raw.get("postOnly"), bool) else None,
            reduce_only=raw.get("reduceOnly") if isinstance(raw.get("reduceOnly"), bool) else None,
            state=state,
            venue_at=venue_at,
            source_at=source_at,
            received_at=received_at,
        )

    def order_reason(self, raw: dict[str, object]) -> str | None:
        """Preserve only stable, consumer-owned venue reason codes."""
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
        value = (
            raw.get("cancelReason")
            or raw.get("cancel_reason")
            or info.get("cancelReason")
            or info.get("cancel_reason")
        )
        reason = str(value or "").strip().lower()
        if reason == "trade_too_many_times_cancel":
            return reason
        return None

    def fill(
        self,
        raw: dict[str, object],
        markets: dict[str, Any],
        identity: UTAAccountIdentity,
        received_at: datetime,
    ) -> FillReport:
        instrument = self.instrument(raw, markets)
        multiplier = instrument.contract_size or Decimal(1)
        quantity = (decimal_value(raw.get("amount")) or Decimal(0)) * multiplier
        price = decimal_value(raw.get("price"))
        if quantity <= 0 or price is None or price <= 0:
            raise ValueError("invalid fill economics")
        fee = raw.get("fee") if isinstance(raw.get("fee"), dict) else {}
        executed_at = utc_from_milliseconds(raw.get("timestamp"), received_at)
        if executed_at is None:
            raise ValueError("fill timestamp missing")
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
        fill_id = raw.get("id") or info.get("tradeId") or info.get("fillId")
        if not fill_id:
            raise ValueError("fill identity missing")
        return FillReport(
            identity=identity,
            instrument=instrument,
            client_order_id=(
                str(raw.get("clientOrderId")) if raw.get("clientOrderId") else None
            ),
            venue_order_id=str(raw.get("order")) if raw.get("order") else None,
            venue_fill_id=str(fill_id),
            side=str(raw.get("side") or "unknown"),
            quantity=quantity,
            price=price,
            fee=decimal_value(fee.get("cost")),
            fee_currency=str(fee.get("currency")) if fee.get("currency") else None,
            liquidity_side=str(raw.get("takerOrMaker")) if raw.get("takerOrMaker") else None,
            executed_at=executed_at,
            received_at=received_at,
        )

    def funding_events(self, rows: list[dict[str, object]]) -> list[dict]:
        """Normalize private ledger rows; subclasses opt into venue semantics."""
        return []

    def account_authority(self, balances: list[dict[str, object]]) -> tuple[str | None, str]:
        """Return exchange account id and explicit trading permission when exposed."""
        account_id = None
        permission = "unknown"
        stack: list[object] = list(balances)
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, item in value.items():
                    normalized = str(key).lower().replace("-", "_")
                    if account_id is None and normalized in {
                        "uid", "user_id", "userid", "account_id", "accountid",
                    } and item not in {None, "", 0, False}:
                        account_id = str(item)
                    if normalized in {
                        "cantrade", "can_trade", "tradepermission", "order_permission",
                    }:
                        if isinstance(item, bool):
                            permission = "granted" if item else "denied"
                        else:
                            text = str(item).lower()
                            if text in {"true", "1", "yes", "granted"}:
                                permission = "granted"
                            elif text in {"false", "0", "no", "denied", "read_only"}:
                                permission = "denied"
                    stack.append(item)
            elif isinstance(value, (list, tuple)):
                stack.extend(value)
        return account_id, permission
