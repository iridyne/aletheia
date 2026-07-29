from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aletheia.execution.broker_funding import (
    compute_broker_funding,
    make_complete_ledger,
    normalize_binance_funding_rows,
    normalize_bitget_funding_rows,
    normalize_okx_funding_rows,
)
from aletheia.storage import repositories as repo
from aletheia.storage.models import Position
from tests.test_demo_trading import (
    _demo_config,
    _fresh_snapshot,
    _patch_place_order,
    setup_env,
)


@pytest.mark.parametrize(
    ("normalizer", "row", "event_id"),
    [
        (
            normalize_bitget_funding_rows,
            {
                "billId": "bg-1", "symbol": "BTCUSDT", "amount": "-2.5",
                "businessType": "contract_settle_fee", "coin": "USDT",
                "cTime": "1784044800000",
            },
            "bg-1",
        ),
        (
            normalize_okx_funding_rows,
            {
                "billId": "okx-1", "instId": "BTC-USDT-SWAP", "pnl": "-2.5",
                "type": "8", "subType": "173", "ccy": "USDT",
                "ts": "1784044800000", "posSide": "long",
            },
            "okx-1",
        ),
        (
            normalize_binance_funding_rows,
            {
                "tranId": "bn-1", "symbol": "BTCUSDT", "income": "-2.5",
                "incomeType": "FUNDING_FEE", "asset": "USDT",
                "time": "1784044800000",
            },
            "bn-1",
        ),
    ],
)
def test_private_funding_normalizers_preserve_identity_and_cost_sign(
    normalizer, row, event_id,
):
    events = normalizer([row, dict(row)])
    assert len(events) == 1
    assert events[0]["event_id"] == event_id
    assert events[0]["symbol"] == "BTC/USDT"
    assert events[0]["cash_change"] == -2.5
    assert events[0]["funding_cost"] == 2.5


@pytest.mark.parametrize(
    ("mode", "environment"),
    [
        ("bitget_demo", "demo"),
        ("bitget_live_limited", "live"),
        ("okx_demo", "demo"),
        ("okx_live_limited", "live"),
        ("binance_testnet", "testnet"),
        ("binance_live_limited", "live"),
    ],
)
def test_complete_empty_private_ledger_proves_zero_funding(mode, environment):
    position = {
        "id": f"p-{mode}",
        "symbol": "BTC/USDT",
        "market_type": "usdt_futures",
        "direction": "long",
        "execution_mode": mode,
        "opened_at": "2026-07-14T00:00:00+00:00",
    }
    ledger = make_complete_ledger(
        broker_id=mode,
        account_environment=environment,
        source_endpoint="/private/funding",
        history_start_ms=1783987200000,
        history_end_ms=1784073600000,
        events=[],
    )
    result = compute_broker_funding(
        position=position,
        closed_at="2026-07-14T12:00:00+00:00",
        ledger=ledger,
    )
    assert result.complete is True
    assert result.funding_cost == 0.0
    assert result.funding_source == "broker_ledger"
    assert result.evidence["events"] == []


def test_private_history_retention_cannot_fabricate_complete_zero_funding():
    from aletheia.execution.broker_funding import (
        BrokerFundingIntegrityError,
        require_retained_history,
    )

    now_ms = 1_800_000_000_000
    ninety_one_days_ms = 91 * 24 * 60 * 60 * 1000
    with pytest.raises(BrokerFundingIntegrityError, match="cannot prove"):
        require_retained_history(
            now_ms - ninety_one_days_ms,
            retention_days=90,
            source="private ledger",
            now_ms=now_ms,
        )

    require_retained_history(
        now_ms - 89 * 24 * 60 * 60 * 1000,
        retention_days=90,
        source="private ledger",
        now_ms=now_ms,
    )


def test_overlapping_same_symbol_position_rejects_directionless_funding_event():
    event = normalize_bitget_funding_rows([{
        "billId": "bg-shared",
        "symbol": "BTCUSDT",
        "amount": "-1",
        "businessType": "contract_settle_fee",
        "coin": "USDT",
        "cTime": "1784030400000",
    }])[0]
    ledger = make_complete_ledger(
        broker_id="bitget_demo",
        account_environment="demo",
        source_endpoint="/api/v2/mix/account/bill",
        history_start_ms=1783987200000,
        history_end_ms=1784073600000,
        events=[event],
    )
    position = {
        "id": "p-long", "symbol": "BTC/USDT", "market_type": "usdt_futures",
        "direction": "long", "execution_mode": "bitget_demo",
        "opened_at": "2026-07-14T00:00:00+00:00",
    }
    overlapping = {
        "id": "p-short", "symbol": "BTC/USDT", "market_type": "usdt_futures",
        "direction": "short", "execution_mode": "bitget_demo",
        "opened_at": "2026-07-14T00:00:00+00:00",
        "closed_at": "2026-07-14T12:00:00+00:00",
    }
    result = compute_broker_funding(
        position=position,
        closed_at="2026-07-14T12:00:00+00:00",
        ledger=ledger,
        candidate_positions=[overlapping],
    )
    assert result.complete is False
    assert result.evidence["reason"] == "broker_funding_attribution_ambiguous"


@pytest.mark.parametrize(
    ("mode", "environment"),
    [
        ("bitget_demo", "demo"),
        ("okx_demo", "demo"),
        ("binance_testnet", "testnet"),
    ],
)
def test_closed_exchange_position_can_be_recovered_from_broker_funding(
    tmp_path, monkeypatch, mode, environment,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=110.0,
        execution_mode=mode,
        opened_at="2026-07-14T00:00:00+00:00",
    )
    repo.insert_position(position)
    from aletheia.portfolio.pnl import close_position

    closed_at = "2026-07-14T12:00:00+00:00"
    close_position(
        position.id,
        exit_price=110.0,
        actual_exit_fee=0.1,
        closed_at=closed_at,
        verified_exit_evidence={
            "source": "private_fills",
            "fill_ids": ["fill-1"],
            "fill_quantity": 1.0,
        },
    )
    incomplete = repo._select_one("positions", position.id)
    assert incomplete["realized_pnl"] is None

    ledger = make_complete_ledger(
        broker_id=mode,
        account_environment=environment,
        source_endpoint="/private/funding",
        history_start_ms=1783987200000,
        history_end_ms=1784073600000,
        events=[],
    )
    funding = compute_broker_funding(
        position=incomplete,
        closed_at=closed_at,
        ledger=ledger,
    )
    from aletheia.portfolio.broker_funding_settlement import (
        materialize_broker_funding_economics,
    )

    result = materialize_broker_funding_economics(
        incomplete,
        funding_cost=funding.funding_cost,
        funding_evidence=funding.evidence,
    )
    stored = repo._select_one("positions", position.id)
    close_event = repo.get_db().execute(
        "SELECT * FROM position_close_events WHERE position_id=?",
        (position.id,),
    ).fetchone()
    assert result["status"] == "settled"
    assert stored["funding_source"] == "broker_ledger"
    assert stored["realized_pnl"] is not None
    assert close_event["realized_pnl"] == stored["realized_pnl"]


def test_bitget_reconciliation_materializes_zero_funding_pnl(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=61000.0)
    captured = []
    _patch_place_order(
        monkeypatch, captured, fill_price=61000.0, filled_quantity=0.001,
    )
    from tests.specs.spec_pipeline_stop_propagation import (
        _patch_private_fill_history,
    )
    _patch_private_fill_history(
        monkeypatch,
        "aletheia.execution.bitget_broker",
        "aletheia.exchanges.bitget.client",
        fill_price=61000.0,
    )
    from aletheia.exchanges.bitget.client import BitgetClient

    async def _funding(self, *, broker_id, start_time_ms, end_time_ms, **kwargs):
        return make_complete_ledger(
            broker_id=broker_id,
            account_environment="demo",
            source_endpoint="/api/v2/mix/account/bill",
            history_start_ms=start_time_ms,
            history_end_ms=end_time_ms,
            events=[],
        )

    monkeypatch.setattr(BitgetClient, "get_funding_ledger", _funding)
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.001,
        entry_price=60000.0,
        current_price=61000.0,
        execution_mode="bitget_demo",
        opened_at="2026-07-14T00:00:00+00:00",
    )
    repo.insert_position(position)
    from aletheia.execution.bitget_broker import close_demo_position
    from aletheia.execution.reconciliation import reconcile

    close_demo_position(position.id, reason="take_profit")
    assert reconcile()["filled"] == 1
    stored = repo._select_one("positions", position.id)
    assert stored["realized_pnl"] is not None
    assert stored["funding_cost"] == 0.0
    assert stored["funding_source"] == "broker_ledger"
    assert stored["outcome_exclusion_reason"] != "incomplete_funding_economics"


@pytest.mark.asyncio
async def test_okx_funding_reader_uses_private_account_bills():
    from aletheia.exchanges.okx.client import OKXClient

    now = datetime.now(timezone.utc)
    start_ms = int((now - timedelta(hours=2)).timestamp() * 1000)
    end_ms = int((now - timedelta(hours=1)).timestamp() * 1000)
    exchange = MagicMock()
    exchange.load_markets = AsyncMock()
    exchange.market.return_value = {"id": "BTC-USDT-SWAP"}
    exchange.private_get_account_bills = AsyncMock(return_value={
        "code": "0",
        "data": [{
            "billId": "okx-funding-1",
            "instId": "BTC-USDT-SWAP",
            "pnl": "-1.25",
            "type": "8",
            "subType": "173",
            "ccy": "USDT",
            "ts": str(start_ms + 30 * 60 * 1000),
            "posSide": "long",
        }],
    })
    with patch(
        "aletheia.exchanges.okx.client.get_secret", return_value="configured",
    ):
        client = OKXClient(mode="demo")
    client._exchange = exchange
    ledger = await client.get_funding_ledger(
        broker_id="okx_demo",
        symbol="BTC/USDT",
        start_time_ms=start_ms,
        end_time_ms=end_ms,
    )
    assert ledger["events"][0]["event_id"] == "okx-funding-1"
    assert ledger["events"][0]["funding_cost"] == 1.25
    exchange.private_get_account_bills.assert_awaited_once()


@pytest.mark.asyncio
async def test_binance_funding_reader_uses_signed_income_history():
    from aletheia.exchanges.binance.client import BinanceClient

    now = datetime.now(timezone.utc)
    start_ms = int((now - timedelta(hours=2)).timestamp() * 1000)
    end_ms = int((now - timedelta(hours=1)).timestamp() * 1000)
    exchange = MagicMock()
    exchange.fapiPrivateGetIncome = AsyncMock(return_value=[{
        "tranId": "binance-funding-1",
        "symbol": "BTCUSDT",
        "income": "0.75",
        "incomeType": "FUNDING_FEE",
        "asset": "USDT",
        "time": start_ms + 30 * 60 * 1000,
    }])
    with patch(
        "aletheia.exchanges.binance.client.get_secret", return_value="configured",
    ):
        client = BinanceClient(mode="testnet")
    client._exchange = exchange
    ledger = await client.get_funding_ledger(
        broker_id="binance_testnet",
        symbol="BTC/USDT",
        start_time_ms=start_ms,
        end_time_ms=end_ms,
    )
    assert ledger["events"][0]["event_id"] == "binance-funding-1"
    assert ledger["events"][0]["funding_cost"] == -0.75
    exchange.fapiPrivateGetIncome.assert_awaited_once()
