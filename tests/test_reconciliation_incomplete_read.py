"""Typed UTA read incompleteness: ``IncompleteReadError`` and its logging (``INV-EX-1``).

The reconciliation projection raises ``IncompleteReadError`` instead of a bare
``RuntimeError(reason_code)`` so callers can log the venue-reported reason rather
than the opaque literal ``"RuntimeError"``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.test_reconciliation import ACCOUNT, setup_env

from aletheia.execution.errors import IncompleteReadError
from aletheia.storage import repositories as repo
from aletheia.storage.models import Order
from aletheia.uta.models import FillListReport, QueryScope, ReadCompleteness

REASON = "private_order_history_start_required"


def _fill_list_report(*, reason_code: str | None) -> FillListReport:
    return FillListReport(
        identity=ACCOUNT,
        completeness=ReadCompleteness.PARTIAL,
        scope=QueryScope(),
        received_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
        reason_code=reason_code,
        profile_version="classic_v2",
        fills=(),
    )


def _projection():
    from aletheia.execution import reconciliation as recon

    return recon._UTAReconciliationProjection(
        "bitget_live_limited", ACCOUNT
    )


def test_incomplete_read_error_carries_reason_code_and_endpoint() -> None:
    exc = IncompleteReadError(
        REASON,
        "get_fills",
        "bitget_demo",
        "usdt_futures",
        "BTC/USDT",
        completeness=ReadCompleteness.PARTIAL,
    )

    assert exc.reason_code == REASON
    assert exc.endpoint == "get_fills"
    assert exc.execution_mode == "bitget_demo"
    assert exc.market_type == "usdt_futures"
    assert exc.symbol == "BTC/USDT"
    assert exc.completeness is ReadCompleteness.PARTIAL
    assert str(exc) == (
        "get_fills(bitget_demo/usdt_futures/BTC/USDT) incomplete: "
        "private_order_history_start_required"
    )


async def test_get_fills_wraps_incomplete_report_as_typed_error(monkeypatch) -> None:
    from aletheia.execution import reconciliation as recon

    report = _fill_list_report(reason_code=REASON)

    async def fake_query_uta(*args, **kwargs):
        return report

    monkeypatch.setattr(recon, "query_uta", fake_query_uta)

    with pytest.raises(IncompleteReadError) as excinfo:
        await _projection().get_fills(symbol="BTC/USDT", market_type="usdt_futures")

    exc = excinfo.value
    assert exc.reason_code == REASON
    assert exc.endpoint == "get_fills"
    assert exc.execution_mode == "bitget_live_limited"
    assert exc.market_type == "usdt_futures"
    assert exc.symbol == "BTC/USDT"
    assert exc.completeness is ReadCompleteness.PARTIAL


async def test_last_resort_reason_code_applies_when_report_omits_one(monkeypatch) -> None:
    from aletheia.execution import reconciliation as recon

    report = SimpleNamespace(
        completeness=ReadCompleteness.PARTIAL,
        reason_code=None,
        fills=(),
    )

    async def fake_query_uta(*args, **kwargs):
        return report

    monkeypatch.setattr(recon, "query_uta", fake_query_uta)

    with pytest.raises(IncompleteReadError) as excinfo:
        await _projection().get_fills(symbol="BTC/USDT", market_type="usdt_futures")

    assert excinfo.value.reason_code == "uta_get_fills_incomplete"


def test_report_envelope_rejects_non_complete_without_reason_code() -> None:
    with pytest.raises(ValidationError):
        _fill_list_report(reason_code=None)


def test_backward_compat_isinstance_runtimeerror() -> None:
    exc = IncompleteReadError(
        REASON,
        "get_fills",
        "bitget_demo",
        "usdt_futures",
        completeness=ReadCompleteness.UNAVAILABLE,
    )

    assert isinstance(exc, RuntimeError)


def test_reconciler_logs_reason_code_on_incomplete_read(
    tmp_path, monkeypatch, caplog
) -> None:
    setup_env(tmp_path, monkeypatch)

    from aletheia.execution import reconciliation as recon

    async def fake_open_orders(self, market_type="spot"):
        return []

    async def fake_fills(self, symbol=None, market_type="spot", start_at=None):
        raise IncompleteReadError(
            REASON,
            "get_fills",
            "bitget_live_limited",
            market_type,
            symbol,
            completeness=ReadCompleteness.PARTIAL,
        )

    monkeypatch.setattr(
        recon._UTAReconciliationProjection, "get_open_orders", fake_open_orders
    )
    monkeypatch.setattr(recon._UTAReconciliationProjection, "get_fills", fake_fills)

    repo.insert_order(
        Order(
            source="adopted",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.001,
            price=60000,
            execution_mode="bitget_live_limited",
            uta_account_identity_json=ACCOUNT.model_dump_json(),
            exchange_order_id="EX-INCOMPLETE",
            status="submitted",
        )
    )

    with caplog.at_level(logging.WARNING, logger="aletheia.reconciliation"):
        result = recon.reconcile()

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "aletheia.reconciliation"
    ]
    assert any(
        "IncompleteReadError" in message and REASON in message
        for message in messages
    ), messages
    assert result["filled"] == 0
    assert repo.list_orders()[0]["status"] == "submitted"
