from __future__ import annotations

from datetime import datetime, timezone

from aletheia.uta.models import (
    FillListReport,
    FillReport,
    FundingLedgerReport,
    InstrumentIdentity,
    OrderLifecycle,
    OrderListReport,
    OrderQueryReport,
    OrderStatusReport,
    QueryScope,
    ReadCompleteness,
    UTAAccountIdentity,
)


def patch_private_history(
    monkeypatch,
    reconciliation,
    fill_rows,
    *,
    open_ids=(),
    calls=None,
    unscoped_fill_rows=None,
):
    async def _query_uta(execution_mode, method, query, *, expected_account=None):
        if calls is not None:
            calls.append((method, execution_mode, query))
        identity = _identity(execution_mode)
        assert expected_account == identity
        now = datetime.now(timezone.utc)
        if method == "get_open_orders":
            return OrderListReport(
                identity=identity,
                completeness=ReadCompleteness.COMPLETE,
                scope=query.scope,
                received_at=now,
                profile_version="test",
                orders=tuple(_order(identity, query.scope, order_id, now) for order_id in open_ids),
            )
        if method == "get_order":
            rows = _matching_rows(fill_rows, query)
            return OrderQueryReport(
                identity=identity,
                completeness=ReadCompleteness.COMPLETE,
                scope=query.scope,
                received_at=now,
                profile_version="test",
                found=_order_from_row(identity, query.scope, rows[0], now) if rows else None,
            )
        if method == "get_fills":
            source_rows = (
                fill_rows
                if query.scope.instrument is not None
                else (fill_rows if unscoped_fill_rows is None else unscoped_fill_rows)
            )
            return FillListReport(
                identity=identity,
                completeness=ReadCompleteness.COMPLETE,
                scope=query.scope,
                received_at=now,
                profile_version="test",
                fills=tuple(_fill(identity, query.scope, row, now) for row in _matching_rows(source_rows, query)),
            )
        if method == "get_funding_ledger":
            return FundingLedgerReport(
                identity=identity,
                completeness=ReadCompleteness.COMPLETE,
                scope=query.scope,
                received_at=now,
                profile_version="test",
                entries=(),
            )
        raise AssertionError(f"unexpected UTA method: {method}")

    monkeypatch.setattr(reconciliation, "query_uta", _query_uta)


def _identity(execution_mode: str) -> UTAAccountIdentity:
    return UTAAccountIdentity(
        account_id=f"{execution_mode}-spec",
        execution_mode=execution_mode,
        venue="bitget",
        environment="live" if execution_mode == "bitget_live_limited" else "demo",
        profile="spec",
        credential_generation=0,
        adapter_implementation="SpecUTAAdapter",
        adapter_version="1",
    )


def _instrument(scope: QueryScope) -> InstrumentIdentity:
    if scope.instrument is not None:
        return scope.instrument
    return InstrumentIdentity(
        canonical_symbol="BTC/USDT",
        venue_symbol="BTCUSDT",
        market_type="spot",
        base_currency="BTC",
        quote_currency="USDT",
        settlement_currency="USDT",
    )


def _field(row, name, default=None):
    if isinstance(row, FillReport):
        return {
            "orderId": row.venue_order_id,
            "clientOid": row.client_order_id,
            "tradeId": row.venue_fill_id,
            "fillQuantity": row.quantity,
            "fillPrice": row.price,
            "fee": row.fee,
            "feeCoin": row.fee_currency,
            "cTime": row.executed_at,
            "tradeScope": row.liquidity_side,
        }.get(name, default)
    return row.get(name, default)


def _matching_rows(rows, query):
    result = []
    for row in rows:
        if query.venue_order_id and str(_field(row, "orderId") or "") != str(query.venue_order_id):
            continue
        if query.client_order_id and str(_field(row, "clientOid") or "") != str(query.client_order_id):
            continue
        result.append(row if isinstance(row, FillReport) else dict(row))
    return result


def _fill(identity, scope, row, now):
    if isinstance(row, FillReport):
        return row.model_copy(update={
            "identity": identity,
            "instrument": _instrument(scope),
            "received_at": now,
        })
    ms = int(row["cTime"])
    return FillReport(
        identity=identity,
        instrument=_instrument(scope),
        client_order_id=row.get("clientOid"),
        venue_order_id=row.get("orderId"),
        venue_fill_id=str(row["tradeId"]),
        side=str(row.get("side") or "buy"),
        quantity=str(row["fillQuantity"]),
        price=str(row["fillPrice"]),
        fee=str(row["fee"]) if row.get("fee") is not None else None,
        fee_currency=row.get("feeCoin"),
        liquidity_side=row.get("tradeScope"),
        executed_at=datetime.fromtimestamp(ms / 1000, tz=timezone.utc),
        received_at=now,
    )


def _order(identity, scope, order_id, now):
    return OrderStatusReport(
        identity=identity,
        instrument=_instrument(scope),
        venue_order_id=str(order_id),
        side="buy",
        order_type="limit",
        original_quantity="1",
        filled_quantity="0",
        state=OrderLifecycle.OPEN,
        received_at=now,
    )


def _order_from_row(identity, scope, row, now):
    return OrderStatusReport(
        identity=identity,
        instrument=_instrument(scope),
        client_order_id=_field(row, "clientOid"),
        venue_order_id=_field(row, "orderId"),
        side=str(_field(row, "side") or "buy"),
        order_type="limit",
        original_quantity=str(_field(row, "fillQuantity") or "1"),
        filled_quantity=str(_field(row, "fillQuantity") or "1"),
        price=str(_field(row, "fillPrice") or "1"),
        state=OrderLifecycle.FILLED,
        received_at=now,
    )
