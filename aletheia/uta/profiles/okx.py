"""OKX Demo UTA profile."""

from datetime import datetime, timedelta
from decimal import Decimal

from aletheia.uta.errors import UTAError, UTAErrorCode
from aletheia.uta.models import CommandEvidence
from aletheia.uta.models import AccountBalance
from aletheia.uta.profiles.base import CCXTProfile, decimal_value


class OKXDemoProfile(CCXTProfile):
    expected_venue = "okx"
    expected_execution_mode = "okx_demo"
    expected_environment = "demo"
    expected_profile = "okx_demo"
    exchange_name = "okx"
    sandbox = True
    open_order_requests = ({"type": "swap"}, {"type": "spot"})
    closed_order_requests = open_order_requests
    fill_requests = open_order_requests
    supported_market_types = ("spot", "usdt_futures")
    mutation_market_order = True
    mutation_limit_order = True
    mutation_modify_order = True
    mutation_cancel_order = True
    mutation_reduce_only = True
    mutation_leverage = True
    mutation_post_only = True
    mutation_client_order_id = True
    mutation_trigger_order = True
    native_tpsl = True
    protection_replace = True
    protection_cancel = True
    integer_leverage = True

    def funding_ledger_requests(self, *, start_at: datetime, received_at: datetime) -> tuple[dict[str, object], ...]:
        requests = ({},)
        if start_at < received_at - timedelta(days=7):
            requests += ({"method": "privateGetAccountBillsArchive"},)
        return requests

    def __init__(self, *, api_key: str, secret: str, passphrase: str) -> None:
        super().__init__(
            apiKey=api_key,
            secret=secret,
            password=passphrase,
            options={"defaultType": "swap"},
        )

    @staticmethod
    def _client_order_id(value: str | None) -> str | None:
        normalized = "".join(character for character in str(value or "") if character.isalnum())[:32]
        return normalized or None

    external_client_order_id = _client_order_id

    @staticmethod
    def _venue_quantity(command, *, instrument=None) -> Decimal:
        selected = instrument or command.instrument
        quantity = Decimal(command.quantity)
        if selected.market_type == "spot":
            return quantity
        contract_size = selected.contract_size
        if contract_size is None or contract_size <= 0:
            raise UTAError(
                UTAErrorCode.INVALID_ORDER,
                "contract_size_unavailable",
                command_evidence=CommandEvidence.NOT_SENT,
            )
        contracts = quantity / contract_size
        if contracts != contracts.to_integral_value() or contracts <= 0:
            raise UTAError(
                UTAErrorCode.INVALID_ORDER,
                "quantity_not_exact_contract_multiple",
                command_evidence=CommandEvidence.NOT_SENT,
            )
        return contracts

    async def prepare_submit(self, exchange, command):
        market = (exchange.markets or {}).get(command.instrument.venue_symbol) or {}
        contract_size = decimal_value(market.get("contractSize"))
        if command.instrument.market_type != "spot" and contract_size is None:
            raise UTAError(
                UTAErrorCode.INVALID_ORDER,
                "contract_size_unavailable",
                command_evidence=CommandEvidence.NOT_SENT,
            )
        instrument = command.instrument.model_copy(
            update={
                "venue_instrument_id": str(market.get("id")) if market.get("id") else None,
                "contract_size": contract_size,
                "linear": market.get("linear"),
                "inverse": market.get("inverse"),
            }
        )
        prepared = command.model_copy(update={"instrument": instrument})
        self._venue_quantity(prepared)
        if prepared.instrument.market_type != "spot" and prepared.leverage is not None and not prepared.reduce_only:
            await exchange.set_leverage(
                int(prepared.leverage),
                prepared.instrument.venue_symbol,
                {"mgnMode": prepared.margin_mode or "cross"},
            )
        return prepared

    def submit_request(self, command):
        params: dict[str, object] = {}
        client_order_id = self._client_order_id(command.client_order_id)
        if client_order_id:
            params["clOrdId"] = client_order_id
        if command.instrument.market_type == "spot":
            if command.order_type == "market" and command.side == "buy":
                params["tgtCcy"] = "base_ccy"
        else:
            params["tdMode"] = command.margin_mode or "cross"
            if command.reduce_only:
                params["reduceOnly"] = True
        if command.position_side:
            params["posSide"] = command.position_side
        if command.time_in_force:
            params["timeInForce"] = command.time_in_force
        if command.post_only:
            params["postOnly"] = True
        if command.trigger_price is not None:
            if command.purpose == "protective_stop":
                params["stopLossPrice"] = command.trigger_price
            elif command.purpose == "take_profit":
                params["takeProfitPrice"] = command.trigger_price
            else:
                raise UTAError(
                    UTAErrorCode.NOT_SUPPORTED,
                    "trigger_purpose_unsupported",
                    command_evidence=CommandEvidence.NOT_SENT,
                )
        return (
            command.instrument.venue_symbol,
            command.order_type,
            command.side,
            self._venue_quantity(command),
            command.limit_price,
            params,
        )

    def modify_request(self, command, current):
        if command.trigger_price is not None or command.time_in_force is not None:
            raise UTAError(
                UTAErrorCode.NOT_SUPPORTED,
                "modify_field_unsupported",
                command_evidence=CommandEvidence.NOT_SENT,
            )
        order_id = command.target_venue_order_id or command.target_client_order_id
        params = {}
        if not command.target_venue_order_id and command.target_client_order_id:
            params["clOrdId"] = self._client_order_id(command.target_client_order_id)
        quantity = (
            self._venue_quantity(command, instrument=current.instrument)
            if command.quantity is not None
            else current.original_quantity / (current.instrument.contract_size or Decimal(1))
        )
        return (
            order_id,
            command.instrument.venue_symbol,
            current.order_type,
            current.side,
            quantity,
            command.limit_price or current.price,
            params,
        )

    def cancel_request(self, command):
        order_id = command.target_venue_order_id or command.target_client_order_id
        params = {}
        if not command.target_venue_order_id and command.target_client_order_id:
            params["clOrdId"] = self._client_order_id(command.target_client_order_id)
        if command.purpose in {"protective_stop", "take_profit", "protection_child"}:
            params["trigger"] = True
        return order_id, command.instrument.venue_symbol, params

    def account_values(self, balances):
        values, normalized, reason = super().account_values(balances)
        details = []
        total_equity = None
        unrealized = Decimal(0)
        for raw in balances:
            info = raw.get("info") or {}
            for account in info.get("data", []) if isinstance(info, dict) else []:
                parsed_equity = decimal_value(account.get("totalEq"))
                if parsed_equity is not None:
                    total_equity = parsed_equity
                initial_margin = decimal_value(account.get("imr"))
                maintenance_margin = decimal_value(account.get("mmr"))
                if initial_margin is not None:
                    values["margin"] = initial_margin
                if maintenance_margin is not None:
                    values["maintenance_margin"] = maintenance_margin
                details.extend(account.get("details") or [])
        if total_equity is not None:
            values["equity"] = total_equity
            reason = None
        for item in details:
            unrealized += decimal_value(item.get("upl")) or Decimal(0)
        values["unrealized_pnl"] = unrealized if details else None
        return values, tuple(normalized), reason

    def funding_events(self, rows):
        from aletheia.uta.funding import normalize_okx_funding_rows

        return normalize_okx_funding_rows(row.get("info", row) if isinstance(row, dict) else {} for row in rows)


class OKXLiveProfile(OKXDemoProfile):
    expected_execution_mode = "okx_live_limited"
    expected_environment = "live"
    expected_profile = "okx_live"
    sandbox = False
    mutation_market_order = False
    mutation_limit_order = False
    mutation_modify_order = False
    mutation_cancel_order = False
    mutation_reduce_only = False
    mutation_leverage = False
    mutation_post_only = False
    mutation_client_order_id = False
    mutation_trigger_order = False
    native_tpsl = False
    protection_replace = False
    protection_cancel = False
