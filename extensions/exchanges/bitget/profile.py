"""Bitget Extension profile: the Unified Trading Account (uta_v3) face, demo and live."""

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from aletheia.uta.errors import UTAError, UTAErrorCode
from aletheia.uta.models import CommandEvidence
from extensions.exchanges.ccxt.profile import CCXTProfile, decimal_value, venue_contract_quantity
from aletheia.uta.models import FillReport, UTAAccountIdentity


def _amount_step_or_none(exchange: Any, market: dict[str, Any]) -> Decimal | None:
    """The venue's published quantity step, or None when it is not expressible.

    Reuses the same converter the pre-trade constraint reader uses so both grids
    stay identical. A venue on significant-digit precision has no fixed
    increment, so None keeps the caller on the stricter whole-contract rule
    rather than inventing a step.
    """
    from extensions.exchanges.ccxt.order_rules import _amount_step, _precision_mode_name

    try:
        return _amount_step((market.get("precision") or {}).get("amount"), _precision_mode_name(exchange))
    except Exception:
        return None


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
    # Measured on the live demo venue (2026-09-30, #3564):
    # a fill request for more than 100 rows answers `40020 Parameter limit error`.
    fill_page_limit = 100
    # CCXT's bitget fetchMyTrades raises ArgumentsRequired for an account-wide
    # (symbol-less) read (measured against the pinned ccxt 4.5.84).
    account_wide_fills_supported = False

    def __init__(self, *, api_key: str, secret: str, passphrase: str) -> None:
        super().__init__(
            apiKey=api_key,
            secret=secret,
            password=passphrase,
            options={"defaultType": "swap", "uta": True},
        )

    def _response_code(self, error: BaseException) -> str | None:
        """Bitget's numeric `code` from the error body, or None if unreadable."""
        try:
            response = json.loads(str(error).removeprefix("bitget "))
        except (ValueError, TypeError):
            return None
        if not isinstance(response, dict) or response.get("code") is None:
            return None
        return str(response["code"])

    def close_position_absent(self, error: BaseException) -> bool:
        return self._response_code(error) in {"22002", "25227", "45110"}

    def venue_rejection_code(self, error: BaseException) -> str | None:
        code = self._response_code(error)
        if code is None:
            return None
        # Bitget codes are numeric; keep only what the reason-code pattern admits.
        token = "".join(char for char in code.lower() if char.isalnum())
        return token[:32] or None

    def acknowledgement_order_id(self, raw: object) -> str:
        order_id = super().acknowledgement_order_id(raw)
        info = raw.get("info")
        if isinstance(info, dict) and "orderId" in info:
            # CCXT stringifies native IDs, including containers and booleans.
            native_id = super().acknowledgement_order_id({"id": info["orderId"]})
            if native_id != order_id:
                raise ValueError("inconsistent acknowledgement identity")
        return order_id

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
        # Bitget publishes contractSize=1 for every perpetual and expresses the
        # real size grid as sizeMultiplier, which CCXT exposes as
        # precision.amount. Carry it so the contract-quantity check validates
        # against the venue's own grid instead of whole contracts; without it a
        # venue-legal 2.5 HYPE is rejected as a fractional contract.
        instrument = command.instrument.model_copy(
            update={
                "venue_instrument_id": str(market.get("id")) if market.get("id") else None,
                "contract_size": contract_size,
                "quantity_precision": _amount_step_or_none(exchange, market),
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
            await exchange.set_leverage(
                int(prepared.leverage),
                prepared.instrument.venue_symbol,
                {"uta": True, **({"marginMode": prepared.margin_mode} if prepared.margin_mode else {})},
            )
        return prepared

    def submit_request(self, command):
        params: dict[str, object] = {"uta": True}
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
        # CCXT reads these unified trigger params with `safe_string`, which accepts
        # only str/float/int: a Decimal is read as None and the entry is acknowledged
        # with its approved protection silently dropped (#3777). Serialized here, at
        # the boundary that owns the request body.
        if (
            command.instrument.market_type != "spot"
            and command.purpose == "entry"
            and not command.reduce_only
        ):
            if command.stop_loss_price is not None:
                params["stopLoss"] = {"triggerPrice": str(command.stop_loss_price)}
            if command.take_profit_price is not None:
                params["takeProfit"] = {"triggerPrice": str(command.take_profit_price)}
        if command.trigger_price is not None:
            if command.instrument.market_type == "spot":
                raise UTAError(
                    UTAErrorCode.NOT_SUPPORTED,
                    "spot_protection_unsupported",
                    command_evidence=CommandEvidence.NOT_SENT,
                )
            if command.purpose == "protective_stop":
                params["stopLossPrice"] = str(command.trigger_price)
            elif command.purpose == "take_profit":
                params["takeProfitPrice"] = str(command.trigger_price)
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

    def modify_request(self, command, current, exchange=None):
        if command.trigger_price is not None or command.time_in_force is not None:
            raise UTAError(
                UTAErrorCode.NOT_SUPPORTED,
                "modify_field_unsupported",
                command_evidence=CommandEvidence.NOT_SENT,
            )
        order_id = command.target_venue_order_id or command.target_client_order_id
        params: dict[str, object] = {"uta": True}
        if not command.target_venue_order_id and command.target_client_order_id:
            params["clientOrderId"] = command.target_client_order_id
        # Populate the current instrument with the same quantity_precision fact
        # used by prepare_submit; without it a venue-legal fractional quantity
        # (e.g. HYPE 2.5 with a 0.01 step) is rejected by modify after being
        # accepted by submit (#3446).
        instrument = current.instrument
        if exchange is not None and command.quantity is not None:
            market = (exchange.markets or {}).get(instrument.venue_symbol) or {}
            quantity_precision = _amount_step_or_none(exchange, market)
            if quantity_precision is not None:
                instrument = instrument.model_copy(update={"quantity_precision": quantity_precision})
        quantity = (
            self._venue_quantity(command, instrument=instrument)
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

    async def protection_amend_request(self, exchange, command):
        """Build the UTA modify-strategy-order body for a tightened stop (#3848 PR-B).

        The stored id names a strategy (TPSL) order, which the ordinary order read
        cannot see, so the venue's own open strategy orders are read first. The
        body resends every leg that order carries: a take-profit sharing the order
        is restated unchanged, so the amend cannot drop it. CCXT's ``edit_order``
        routes ``stopLossPrice`` to the same endpoint (ccxt 4.5.84) but omits every
        take-profit param while doing so, which is why the body is built here.
        """
        def refuse(reason):
            return UTAError(UTAErrorCode.INVALID_ORDER, reason, command_evidence=CommandEvidence.NOT_SENT)

        if command.purpose != "protective_stop" or command.instrument.market_type != "usdt_futures":
            raise refuse("protection_amend_unsupported")
        symbol = command.instrument.venue_symbol
        market_id = exchange.market(symbol)["id"]
        payload = await exchange.privateUtaGetV3TradeUnfilledStrategyOrders(
            {"category": "USDT-FUTURES", "symbol": market_id}
        )
        rows = payload.get("data") if isinstance(payload, dict) and payload.get("code") == "00000" else None
        if not isinstance(rows, list):
            raise UTAError(UTAErrorCode.VENUE_REJECTED, "protection_amend_read_unavailable", command_evidence=CommandEvidence.NOT_SENT)
        target = str(command.target_venue_order_id or "")
        matches = [row for row in rows if isinstance(row, dict) and str(row.get("orderId") or "") == target]
        if len(matches) != 1 or str(matches[0].get("symbol") or "") != market_id:
            raise refuse("modify_target_unavailable")
        row = matches[0]
        current = str(row.get("stopLoss") or "").strip()
        side = str(row.get("posSide") or "").strip().lower()
        qty = str(row.get("qty") or "").strip()
        if not current or side not in {"long", "short"}:
            raise refuse("modify_target_unavailable")
        if not qty:
            raise refuse("protection_amend_quantity_unavailable")
        if str(row.get("slOrderType") or "market").lower() != "market":
            # A limit stop's execution price would be left behind by the new trigger.
            raise refuse("protection_amend_limit_stop_unsupported")
        new_stop = exchange.price_to_precision(symbol, str(command.trigger_price))
        # Stops only move in the favourable direction. An equal price (the trail
        # rounded onto the venue's current tick) is resent, which is idempotent.
        looser = Decimal(new_stop) < Decimal(current) if side == "long" else Decimal(new_stop) > Decimal(current)
        if looser:
            raise refuse("protection_amend_loosens")
        request: dict[str, object] = {
            "orderId": target,
            "qty": qty,
            "stopLoss": new_stop,
            "slTriggerBy": str(row.get("slTriggerBy") or "mark"),
            "slOrderType": "market",
        }
        take_profit = str(row.get("takeProfit") or "").strip()
        if take_profit:
            request["takeProfit"] = take_profit
            for key in ("tpTriggerBy", "tpOrderType", "tpLimitPrice"):
                if str(row.get(key) or "").strip():
                    request[key] = str(row[key])
        return request

    async def amend_protection(self, exchange, request):
        response = await exchange.privateUtaPostV3TradeModifyStrategyOrder(request)
        data = response.get("data") if isinstance(response, dict) and response.get("code") == "00000" else None
        if not isinstance(data, dict):
            raise ValueError("protection amend acknowledgement malformed")
        if str(data.get("orderId") or "") != request["orderId"]:
            raise ValueError("inconsistent acknowledgement identity")
        return request["orderId"]

    def cancel_request(self, command):
        order_id = command.target_venue_order_id or command.target_client_order_id
        params: dict[str, object] = {"uta": True}
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
            # Bitget hedge-mode fills keep the opening side on a close.  The
            # explicit close intent plus hedge mode makes this a canonical leg,
            # without weakening ambiguous or opening activity.
            return report.model_copy(update={
                "position_side": (
                    "long" if report.side.lower() == "buy" else "short"
                ),
            })
        return report


class BitgetUTAV3Profile(_BitgetDemoProfile):
    expected_profile = "uta_v3"
    # uta=True routes load_markets to fetch_uta_markets, a different symbol set.
    listing_options = {"uta": True}
    balance_requests = ({"uta": True},)
    position_params = {"uta": True, "productType": "USDT-FUTURES"}
    open_order_requests = (
        {"uta": True, "type": "spot", "productType": "SPOT"},
        {"uta": True, "type": "swap", "productType": "USDT-FUTURES"},
    )
    closed_order_requests = open_order_requests
    fill_requests = open_order_requests
    # UTA v3 financial-records endpoint: one request for USDT-FUTURES funding.
    # The `uta=True` flag routes fetchLedger to /v3/account/financial-records.
    ledger_requests: tuple[dict[str, object], ...] = (
        {"uta": True, "productType": "USDT-FUTURES"},
    )

    def fill(
        self,
        raw: dict[str, object],
        markets: dict[str, Any],
        identity: UTAAccountIdentity,
        received_at: datetime,
    ) -> FillReport:
        report = super().fill(raw, markets, identity, received_at)
        # ccxt negates every Bitget fee (its `deduction` test never fires). The v2 mix endpoint
        # reports a fee as a negative number, so that yields a positive cost; UTA v3 already
        # reports the cost as a positive number, so ccxt turns it into a negative cost, which the
        # Fill validator rejects and no uta_v3 fill could ever be materialized. Use the venue's value.
        info = raw.get("info")
        details = info.get("feeDetail") if isinstance(info, dict) else None
        if isinstance(details, list) and len(details) == 1 and isinstance(details[0], dict):
            detail: dict[str, Any] = details[0]
            cost = decimal_value(detail.get("fee"))
            if cost is not None:
                coin = detail.get("feeCoin")
                return report.model_copy(update={
                    "fee": cost,
                    "fee_currency": str(coin) if coin else report.fee_currency,
                })
        return report

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

    def funding_events(self, rows):
        from aletheia.uta.funding import normalize_bitget_uta_funding_rows

        return normalize_bitget_uta_funding_rows(
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
        """Bitget UTA v3 uses fetch_ledger with uta=True and productType.

        Routes to /v3/account/financial-records which supports UTA perpetual
        funding settlement rows.  The venue_symbol is passed as ``symbol``
        when provided so the query is scoped to one instrument.
        """
        page_limit = min(limit, 100)
        rows = []
        history_bound_reached = False
        failed_calls = 0
        for params in ledger_requests:
            call_params = dict(params)
            if venue_symbol:
                call_params["symbol"] = venue_symbol
            try:
                page = await request(exchange.fetch_ledger(
                    "USDT",
                    since,
                    page_limit,
                    history_params_fn(call_params),
                ))
                if not isinstance(page, list):
                    raise ValueError("invalid CCXT ledger")
                history_bound_reached = history_bound_reached or len(page) >= page_limit
                rows.extend(item for item in page if isinstance(item, dict))
            except (Exception, TimeoutError):
                failed_calls += 1
        if failed_calls == len(ledger_requests):
            raise RuntimeError("private funding ledger unavailable")
        reason = (
            "funding_history_bound_reached" if history_bound_reached
            else "funding_ledger_partial" if failed_calls else None
        )
        return rows, reason


class BitgetUTAV3LiveProfile(BitgetUTAV3Profile):
    expected_execution_mode = "bitget_live_limited"
    expected_environment = "live"
    sandbox = False
