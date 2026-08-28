"""Binance Futures Testnet UTA profile."""

from decimal import Decimal

from aletheia.uta.errors import UTAError, UTAErrorCode
from aletheia.uta.models import CommandEvidence
from aletheia.uta.profiles.base import CCXTProfile, decimal_value


class BinanceTestnetProfile(CCXTProfile):
    expected_venue = "binance"
    expected_execution_mode = "binance_testnet"
    expected_environment = "testnet"
    expected_profile = "binance_testnet"
    exchange_name = "binanceusdm"
    sandbox = True
    mutation_market_order = True
    mutation_limit_order = True
    mutation_modify_order = True
    mutation_cancel_order = True
    mutation_reduce_only = True
    mutation_leverage = True
    mutation_post_only = True
    mutation_client_order_id = True
    integer_leverage = True

    def __init__(self, *, api_key: str, secret: str) -> None:
        super().__init__(
            apiKey=api_key,
            secret=secret,
            options={"defaultType": "future"},
        )

    def account_values(self, balances):
        values, normalized, reason = super().account_values(balances)
        unrealized = Decimal(0)
        saw_unrealized = False
        for raw in balances:
            info = raw.get("info") or {}
            if not isinstance(info, dict):
                continue
            initial_margin = decimal_value(info.get("totalInitialMargin"))
            maintenance_margin = decimal_value(info.get("totalMaintMargin"))
            if initial_margin is not None:
                values["margin"] = initial_margin
            if maintenance_margin is not None:
                values["maintenance_margin"] = maintenance_margin
            for asset in info.get("assets", []) or []:
                if str(asset.get("asset") or "").upper() != "USDT":
                    continue
                parsed = decimal_value(asset.get("unrealizedProfit"))
                if parsed is not None:
                    unrealized += parsed
                    saw_unrealized = True
        if saw_unrealized:
            values["unrealized_pnl"] = unrealized
        return values, normalized, reason

    def funding_events(self, rows):
        from aletheia.uta.funding import normalize_binance_funding_rows

        return normalize_binance_funding_rows(
            row.get("info", row) if isinstance(row, dict) else {} for row in rows
        )

    async def prepare_submit(self, exchange, command) -> None:
        if command.leverage is not None:
            await exchange.set_leverage(
                int(command.leverage), command.instrument.venue_symbol
            )

    @staticmethod
    def _params(command) -> dict:
        params: dict[str, object] = {}
        if command.client_order_id:
            params["newClientOrderId"] = command.client_order_id
        if command.reduce_only:
            params["reduceOnly"] = True
        if command.position_side:
            params["positionSide"] = command.position_side
        if command.time_in_force:
            params["timeInForce"] = command.time_in_force
        if command.post_only:
            params["postOnly"] = True
        return params

    def submit_request(self, command):
        return (
            command.instrument.venue_symbol,
            command.order_type,
            command.side,
            command.quantity,
            command.limit_price,
            self._params(command),
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
            params["origClientOrderId"] = command.target_client_order_id
        return (
            order_id,
            command.instrument.venue_symbol,
            current.order_type,
            current.side,
            command.quantity or current.original_quantity,
            command.limit_price or current.price,
            params,
        )

    def cancel_request(self, command):
        order_id = command.target_venue_order_id or command.target_client_order_id
        params = {}
        if not command.target_venue_order_id and command.target_client_order_id:
            params["origClientOrderId"] = command.target_client_order_id
        return order_id, command.instrument.venue_symbol, params


class BinanceLiveProfile(BinanceTestnetProfile):
    expected_execution_mode = "binance_live_limited"
    expected_environment = "live"
    expected_profile = "binance_live"
    sandbox = False
    mutation_market_order = False
    mutation_limit_order = False
    mutation_modify_order = False
    mutation_cancel_order = False
    mutation_reduce_only = False
    mutation_leverage = False
    mutation_post_only = False
    mutation_client_order_id = False
