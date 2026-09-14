"""Binance Futures Testnet UTA profile."""

from decimal import Decimal

from aletheia.uta.errors import UTAError, UTAErrorCode
from aletheia.uta.models import CommandEvidence
from extensions.exchanges.ccxt.profiles.base import CCXTProfile, decimal_value


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
    client_order_lookup_param = "origClientOrderId"

    def __init__(self, *, api_key: str, secret: str) -> None:
        super().__init__(
            apiKey=api_key,
            secret=secret,
            options={"defaultType": "future"},
        )
    async def fetch_fills(self, exchange, symbol, since, limit, params, end_ms):
        # Binance forbids fromId with time bounds; empty windows do not end history.
        rows = []
        window_start = since
        while window_start <= end_ms:
            window_end = min(window_start + 7 * 86400 * 1000, end_ms)
            from_id = None
            for _ in range(100):
                # A point query still reads; keep returned evidence bounded by window_end.
                request = {**params, "startTime": window_start, "endTime": max(window_start + 1, window_end)} if from_id is None else {**params, "fromId": from_id}
                page = await exchange.fetch_my_trades(symbol, None, limit, request)
                if not isinstance(page, list):
                    raise ValueError("Binance private trade history malformed")
                rows.extend(row for row in page if not isinstance(row, dict) or int(row["timestamp"]) <= window_end)
                if len(page) < limit:
                    break
                ids = [str(row.get("id") or "") for row in page]
                if not all(value.isdigit() for value in ids):
                    return rows, True
                next_id = str(max(int(value) for value in ids) + 1)
                if from_id is not None and int(next_id) <= int(from_id):
                    return rows, True
                from_id = next_id
            else:
                return rows, True
            if window_end == end_ms:
                break
            window_start = window_end
        return rows, False


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
        from extensions.exchanges.binance.funding import normalize_binance_funding_rows

        return normalize_binance_funding_rows(
            row.get("info", row) if isinstance(row, dict) else {} for row in rows
        )

    async def prepare_submit(self, exchange, command) -> None:
        if command.leverage is not None:
            await exchange.set_leverage(
                int(command.leverage), command.instrument.venue_symbol
            )
        return command

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
