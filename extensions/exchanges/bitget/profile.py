"""Independent Bitget Classic-v2 and UTA-v3 profiles."""

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from aletheia.uta.errors import UTAError, UTAErrorCode
from aletheia.uta.models import CommandEvidence
from extensions.exchanges.ccxt.profiles.base import CCXTProfile, decimal_value, venue_contract_quantity
from aletheia.uta.models import FillReport, UTAAccountIdentity


class _BitgetDemoProfile(CCXTProfile):
    expected_venue = "bitget"
    expected_execution_mode = "bitget_demo"
    expected_environment = "demo"
    exchange_name = "bitget"
    sandbox = True
    supported_market_types = ("spot", "usdt_futures")
    mutation_market_order = True
    mutation_limit_order = True
    mutation_modify_order = True
    mutation_cancel_order = True
    mutation_reduce_only = True
    mutation_leverage = True
    mutation_post_only = True
    mutation_client_order_id = True
    match_cap_close_fallback = True
    mutation_trigger_order = True
    native_tpsl = True
    protection_replace = True
    protection_cancel = True
    integer_leverage = True

    def __init__(self, *, api_key: str, secret: str, passphrase: str, uta: bool) -> None:
        self._uta = uta
        super().__init__(
            apiKey=api_key,
            secret=secret,
            password=passphrase,
            options={"defaultType": "swap", "uta": uta},
        )

    def close_position_absent(self, error: BaseException) -> bool:
        try:
            response = json.loads(str(error).removeprefix("bitget "))
        except (ValueError, TypeError):
            return False
        codes = {"22002", "25227", "45110"} if self._uta else {"22002", "25227"}
        return isinstance(response, dict) and str(response.get("code")) in codes

    _venue_quantity = staticmethod(venue_contract_quantity)

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
        if (
            prepared.instrument.market_type != "spot"
            and prepared.leverage is not None
            and not prepared.reduce_only
        ):
            if not self._uta and prepared.purpose == "entry" and prepared.reference_price is not None:
                balance = await exchange.fetch_balance({"type": "swap", "productType": "USDT-FUTURES", "uta": False})
                available = decimal_value((balance.get("USDT") or {}).get("free"))
                if available is None:
                    raise UTAError(UTAErrorCode.INCOMPLETE_READ, "classic_margin_balance_unavailable", command_evidence=CommandEvidence.NOT_SENT)
                need = prepared.quantity * prepared.reference_price / prepared.leverage - available
                if need > 1:
                    await exchange.transfer("USDT", format(need, ".2f"), "spot", "swap")
            await exchange.set_leverage(
                int(prepared.leverage),
                prepared.instrument.venue_symbol,
                {"uta": self._uta},
            )
        return prepared

    def submit_request(self, command):
        if (
            not self._uta
            and command.instrument.market_type == "spot"
            and command.order_type == "market"
            and command.side == "buy"
        ):
            raise UTAError(
                UTAErrorCode.NOT_SUPPORTED,
                "classic_spot_market_buy_base_quantity_unsupported",
                command_evidence=CommandEvidence.NOT_SENT,
            )
        params: dict[str, object] = {"uta": self._uta}
        if command.client_order_id:
            params["clientOrderId"] = command.client_order_id
        if command.instrument.market_type != "spot":
            params["hedged"] = True
            params["marginMode"] = command.margin_mode or "cross"
            if command.reduce_only:
                params["reduceOnly"] = True
        if command.time_in_force:
            params["timeInForce"] = command.time_in_force
        if command.post_only:
            params["postOnly"] = True
        if command.stop_loss_price is not None:
            params["stopLoss"] = {"triggerPrice": command.stop_loss_price}
        if command.take_profit_price is not None:
            params["takeProfit"] = {"triggerPrice": command.take_profit_price}
        if command.trigger_price is not None:
            if command.instrument.market_type == "spot":
                raise UTAError(
                    UTAErrorCode.NOT_SUPPORTED,
                    "spot_protection_unsupported",
                    command_evidence=CommandEvidence.NOT_SENT,
                )
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
        params: dict[str, object] = {"uta": self._uta}
        if not command.target_venue_order_id and command.target_client_order_id:
            params["clientOrderId"] = command.target_client_order_id
        quantity = (
            self._venue_quantity(command, instrument=current.instrument)
            if command.quantity is not None
            else None
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
        params: dict[str, object] = {"uta": self._uta}
        if not command.target_venue_order_id and command.target_client_order_id:
            params["clientOrderId"] = command.target_client_order_id
        if command.purpose in {"protective_stop", "take_profit", "protection_child"}:
            params["trigger"] = True
        return order_id, command.instrument.venue_symbol, params

    def fill(
        self,
        raw: dict[str, object],
        markets: dict[str, Any],
        identity: UTAAccountIdentity,
        received_at: datetime,
    ) -> FillReport:
        report = super().fill(raw, markets, identity, received_at)
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
        position_mode = str(
            raw.get("posMode") or info.get("posMode") or ""
        ).strip().lower()
        trade_intent = (report.trade_intent or "").strip().lower()
        if (
            report.position_side is None
            and position_mode in {"hedge_mode", "hedge", "two_way", "two-way"}
            and trade_intent in {"close", "exit", "reduce", "decrease"}
            and report.side.lower() in {"buy", "sell"}
        ):
            # Bitget Classic hedge fills keep the opening side on a close.  The
            # explicit close intent plus hedge mode makes this a canonical leg,
            # without weakening ambiguous or opening activity.
            return report.model_copy(update={
                "position_side": (
                    "long" if report.side.lower() == "buy" else "short"
                ),
            })
        return report


class BitgetClassicV2Profile(_BitgetDemoProfile):
    expected_profile = "classic_v2"
    balance_requests = (
        {"type": "spot"},
        {"type": "swap", "productType": "USDT-FUTURES"},
    )
    position_params = {"productType": "USDT-FUTURES"}
    open_order_requests = (
        {"type": "spot"},
        {"type": "swap", "productType": "USDT-FUTURES"},
    )
    closed_order_requests = open_order_requests
    fill_requests = open_order_requests

    def __init__(self, *, api_key: str, secret: str, passphrase: str) -> None:
        super().__init__(api_key=api_key, secret=secret, passphrase=passphrase, uta=False)

    def account_values(self, balances):
        values, normalized, reason = super().account_values(balances)
        mix_equity = Decimal(0)
        unrealized = Decimal(0)
        saw_mix = False
        for raw in balances:
            info = raw.get("info") or []
            for account in info if isinstance(info, list) else []:
                equity = decimal_value(account.get("usdtEquity") or account.get("accountEquity"))
                if equity is not None:
                    mix_equity += equity
                    saw_mix = True
                unrealized += decimal_value(
                    account.get("unrealizedPL") or account.get("crossedUnrealizedPL")
                ) or Decimal(0)
        if saw_mix:
            spot_usdt = Decimal(0)
            if balances:
                spot_usdt = decimal_value((balances[0].get("total") or {}).get("USDT")) or Decimal(0)
            values["equity"] = mix_equity + spot_usdt
            values["unrealized_pnl"] = unrealized
        return values, normalized, reason

    def funding_events(self, rows):
        from extensions.exchanges.bitget.funding import normalize_bitget_funding_rows

        return normalize_bitget_funding_rows(
            row.get("info", row) if isinstance(row, dict) else {} for row in rows
        )

    async def fetch_funding_rows(
        self,
        exchange,
        since,
        limit,
        history_params_fn,
        *,
        request,
        ledger_requests: tuple[dict[str, object], ...],
        venue_symbol: str | None = None,
    ):
        """Bitget classic_v2 uses fetch_funding_history per symbol.

        fetch_ledger("USDT", ...) always raises code 40020 on classic_v2 accounts;
        fetch_funding_history returns billId/businessType/coin/amount rows that
        normalize_bitget_funding_rows already handles.
        venue_symbol is required by the Bitget CCXT adapter.
        """
        if not venue_symbol:
            raise RuntimeError("private funding ledger unavailable")
        rows = []
        history_bound_reached = False
        try:
            page = await request(exchange.fetch_funding_history(venue_symbol, since, limit))
            if not isinstance(page, list):
                raise ValueError("invalid CCXT funding history")
            history_bound_reached = len(page) >= limit
            rows.extend(item for item in page if isinstance(item, dict))
        except (Exception, TimeoutError):
            raise RuntimeError("private funding ledger unavailable")
        return rows, "funding_history_bound_reached" if history_bound_reached else None


class BitgetUTAV3Profile(_BitgetDemoProfile):
    expected_profile = "uta_v3"
    balance_requests = ({"uta": True},)
    position_params = {"uta": True, "productType": "USDT-FUTURES"}
    open_order_requests = (
        {"uta": True, "type": "spot", "productType": "SPOT"},
        {"uta": True, "type": "swap", "productType": "USDT-FUTURES"},
    )
    closed_order_requests = open_order_requests
    fill_requests = open_order_requests

    def __init__(self, *, api_key: str, secret: str, passphrase: str) -> None:
        super().__init__(api_key=api_key, secret=secret, passphrase=passphrase, uta=True)

    def account_values(self, balances):
        values, normalized, _reason = super().account_values(balances)
        usd_equity = Decimal(0)
        saw_usd_value = False
        for raw in balances:
            info = raw.get("info") or []
            for asset in info if isinstance(info, list) else []:
                usd_value = decimal_value(asset.get("usdValue"))
                if usd_value is not None:
                    usd_equity += usd_value
                    saw_usd_value = True
        if saw_usd_value:
            values["equity"] = usd_equity
        return values, normalized, None if saw_usd_value else _reason

    funding_events = BitgetClassicV2Profile.funding_events


class BitgetClassicV2LiveProfile(BitgetClassicV2Profile):
    expected_execution_mode = "bitget_live_limited"
    expected_environment = "live"
    sandbox = False


class BitgetUTAV3LiveProfile(BitgetUTAV3Profile):
    expected_execution_mode = "bitget_live_limited"
    expected_environment = "live"
    sandbox = False
