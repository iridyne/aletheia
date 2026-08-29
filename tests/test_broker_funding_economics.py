from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aletheia.execution.broker_funding import (
    compute_broker_funding,
    compute_stored_broker_funding,
    load_broker_funding_candidates,
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


def _seed_pending_broker_funding_close(
    tmp_path,
    monkeypatch,
    *,
    mode: str = "bitget_demo",
    environment: str = "demo",
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
        entry_fee=0.05,
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
    return position.id, incomplete, funding, ledger


def _broker_funding_state(position_id: str) -> dict:
    db = repo.get_db()
    return {
        "position": repo._select_one("positions", position_id),
        "close_events": [
            dict(row)
            for row in db.execute(
                "SELECT * FROM position_close_events WHERE position_id=? ORDER BY rowid",
                (position_id,),
            ).fetchall()
        ],
        "audit_count": db.execute(
            """SELECT COUNT(*) FROM audit_events
               WHERE entity_id=? AND event_type='exchange_position_funding_settled'""",
            (position_id,),
        ).fetchone()[0],
        "hook_count": db.execute(
            "SELECT COUNT(*) FROM outcome_hook_jobs WHERE position_id=?",
            (position_id,),
        ).fetchone()[0],
        "cash_event_count": db.execute(
            "SELECT COUNT(*) FROM paper_account_events WHERE position_id=?",
            (position_id,),
        ).fetchone()[0],
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "ETH/USDT"),
        ("market_type", "spot"),
        ("direction", "short"),
        ("execution_mode", "okx_demo"),
        ("entry_price", 101.0),
        ("current_price", 111.0),
        ("quantity", 2.0),
        ("entry_fee", 0.2),
        ("exit_fee", 0.3),
        ("opened_at", "2026-07-14T00:01:00+00:00"),
        ("closed_at", "2026-07-14T13:00:00+00:00"),
        ("close_reason", "owner_changed"),
        ("exit_price_source", "owner_changed"),
        ("exit_fill_verified", 0),
        ("reconciliation_identity", '{"position_id":"other"}'),
        ("reconciliation_evidence", '{"source":"other"}'),
        ("outcome_exclusion_reason", "owner_changed"),
    ],
)
def test_broker_funding_rejects_stale_position_snapshot(
    tmp_path, monkeypatch, field, value,
):
    position_id, stale, funding, ledger = _seed_pending_broker_funding_close(
        tmp_path, monkeypatch,
    )
    repo.update_position(position_id, **{field: value})
    before = _broker_funding_state(position_id)
    from aletheia.portfolio.broker_funding_settlement import (
        materialize_broker_funding_economics,
    )

    with pytest.raises(ValueError, match="snapshot changed"):
        materialize_broker_funding_economics(
            stale,
            funding_cost=funding.funding_cost,
            funding_evidence=funding.evidence,
            funding_ledger=ledger,
        )

    assert _broker_funding_state(position_id) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "ETH/USDT"),
        ("direction", "short"),
        ("execution_mode", "okx_demo"),
        ("close_reason", "owner_changed"),
        ("exit_price", 111.0),
        ("closed_qty", 2.0),
        ("entry_fee", 0.2),
        ("exit_fee", 0.3),
        ("closed_at", "2026-07-14T13:00:00+00:00"),
        ("realized_pnl", 123.0),
    ],
)
def test_broker_funding_rejects_stale_full_close_snapshot(
    tmp_path, monkeypatch, field, value,
):
    position_id, stale, funding, ledger = _seed_pending_broker_funding_close(
        tmp_path, monkeypatch,
    )
    close_event = repo.get_db().execute(
        "SELECT * FROM position_close_events WHERE position_id=?",
        (position_id,),
    ).fetchone()
    repo._update("position_close_events", close_event["id"], {field: value})
    before = _broker_funding_state(position_id)
    from aletheia.portfolio.broker_funding_settlement import (
        materialize_broker_funding_economics,
    )

    with pytest.raises(ValueError, match="snapshot changed"):
        materialize_broker_funding_economics(
            stale,
            funding_cost=funding.funding_cost,
            funding_evidence=funding.evidence,
            funding_ledger=ledger,
        )

    assert _broker_funding_state(position_id) == before


def test_broker_funding_exact_replay_is_a_noop(tmp_path, monkeypatch):
    position_id, stale, funding, ledger = _seed_pending_broker_funding_close(
        tmp_path, monkeypatch,
    )
    from aletheia.portfolio.broker_funding_settlement import (
        materialize_broker_funding_economics,
    )

    first = materialize_broker_funding_economics(
        stale,
        funding_cost=funding.funding_cost,
        funding_evidence=funding.evidence,
        funding_ledger=ledger,
    )
    after_first = _broker_funding_state(position_id)
    second = materialize_broker_funding_economics(
        stale,
        funding_cost=funding.funding_cost,
        funding_evidence=funding.evidence,
        funding_ledger=ledger,
    )
    assert first["status"] == "settled"
    assert second["status"] == "already_current"
    assert _broker_funding_state(position_id) == after_first

    changed_evidence = {**funding.evidence, "source_endpoint": "/other"}
    with pytest.raises(ValueError, match="cannot be overwritten"):
        materialize_broker_funding_economics(
            stale,
            funding_cost=funding.funding_cost,
            funding_evidence=changed_evidence,
            funding_ledger=ledger,
        )
    with pytest.raises(ValueError, match="cannot be overwritten"):
        materialize_broker_funding_economics(
            stale,
            funding_cost=funding.funding_cost + 1.0,
            funding_evidence=funding.evidence,
            funding_ledger=ledger,
        )
    assert _broker_funding_state(position_id) == after_first


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


def test_stored_funding_candidates_use_canonical_symbol_and_window(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    target = Position(
        id="funding-target",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="bitget_demo",
        opened_at="2026-07-14T00:00:00+00:00",
    )
    candidates = [
        Position(
            id="alternate-symbol",
            symbol="BTCUSDT",
            market_type="usdt_futures",
            direction="short",
            quantity=1.0,
            entry_price=100.0,
            execution_mode="bitget_demo",
            opened_at="2026-07-14T01:00:00+00:00",
        ),
        Position(
            id="other-symbol",
            symbol="ETH/USDT",
            market_type="usdt_futures",
            direction="short",
            quantity=1.0,
            entry_price=100.0,
            execution_mode="bitget_demo",
            opened_at="2026-07-14T01:00:00+00:00",
        ),
        Position(
            id="after-window",
            symbol="BTCUSDT",
            market_type="usdt_futures",
            direction="short",
            quantity=1.0,
            entry_price=100.0,
            execution_mode="bitget_demo",
            opened_at="2026-07-14T13:00:00+00:00",
        ),
    ]
    repo.insert_position(target)
    for candidate in candidates:
        repo.insert_position(candidate)

    rows = load_broker_funding_candidates(
        repo.get_db(),
        position=repo._select_one("positions", target.id),
        closed_at="2026-07-14T12:00:00+00:00",
    )

    assert [row["id"] for row in rows] == ["alternate-symbol"]
    repo.insert_position(Position(
        id="second-alternate-symbol",
        symbol="BTC-USDT",
        market_type="usdt_futures",
        direction="short",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="bitget_demo",
        opened_at="2026-07-14T02:00:00+00:00",
    ))
    from aletheia.execution.broker_funding import BrokerFundingIntegrityError

    with pytest.raises(BrokerFundingIntegrityError, match="safe bound"):
        load_broker_funding_candidates(
            repo.get_db(),
            position=repo._select_one("positions", target.id),
            closed_at="2026-07-14T12:00:00+00:00",
            max_candidates=1,
        )


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
    position_id, incomplete, funding, ledger = _seed_pending_broker_funding_close(
        tmp_path,
        monkeypatch,
        mode=mode,
        environment=environment,
    )
    from aletheia.portfolio.broker_funding_settlement import (
        materialize_broker_funding_economics,
    )

    result = materialize_broker_funding_economics(
        incomplete,
        funding_cost=funding.funding_cost,
        funding_evidence=funding.evidence,
        funding_ledger=ledger,
    )
    stored = repo._select_one("positions", position_id)
    close_event = repo.get_db().execute(
        "SELECT * FROM position_close_events WHERE position_id=?",
        (position_id,),
    ).fetchone()
    assert result["status"] == "settled"
    assert stored["funding_source"] == "broker_ledger"
    assert stored["realized_pnl"] is not None
    assert close_event["realized_pnl"] == stored["realized_pnl"]
    repeated = materialize_broker_funding_economics(
        incomplete,
        funding_cost=funding.funding_cost,
        funding_evidence=funding.evidence,
        funding_ledger=ledger,
    )
    assert repeated["status"] == "already_current"
    with pytest.raises(ValueError, match="cannot be overwritten"):
        materialize_broker_funding_economics(
            incomplete,
            funding_cost=funding.funding_cost,
            funding_evidence={
                **funding.evidence,
                "events": [{"event_id": "different-event-same-total"}],
            },
            funding_ledger=ledger,
        )


def test_delayed_funding_settlement_rejects_alternate_symbol_double_claim(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    from aletheia.portfolio.pnl import close_position

    positions = []
    for position_id, symbol, direction in (
        ("funding-long", "BTC/USDT", "long"),
        ("funding-short", "BTCUSDT", "short"),
    ):
        position = Position(
            id=position_id,
            symbol=symbol,
            market_type="usdt_futures",
            direction=direction,
            quantity=1.0,
            entry_price=100.0,
            entry_fee=0.05,
            execution_mode="bitget_demo",
            opened_at="2026-07-14T00:00:00+00:00",
        )
        repo.insert_position(position)
        close_position(
            position.id,
            exit_price=110.0,
            actual_exit_fee=0.1,
            closed_at="2026-07-14T12:00:00+00:00",
            verified_exit_evidence={
                "source": "private_fills",
                "fill_ids": [f"fill-{position.id}"],
                "fill_quantity": 1.0,
            },
        )
        positions.append(position)

    event = normalize_bitget_funding_rows([{
        "billId": "one-shared-event",
        "symbol": "BTCUSDT",
        "amount": "-1",
        "businessType": "contract_settle_fee",
        "coin": "USDT",
        "cTime": "1784030400000",
    }])[0]

    class Client:
        async def get_funding_ledger(
            self, *, broker_id, start_time_ms, end_time_ms, **_kwargs
        ):
            return make_complete_ledger(
                broker_id=broker_id,
                account_environment="demo",
                source_endpoint="/private/funding",
                history_start_ms=start_time_ms,
                history_end_ms=end_time_ms,
                events=[event],
            )

        async def close(self):
            pass

    from aletheia.portfolio import broker_funding_settlement as settlement

    monkeypatch.setattr(settlement, "_client_for_mode", lambda _mode: Client())
    result = asyncio.run(settlement.settle_unsettled_exchange_closes())

    assert result["checked"] == 2
    assert result["settled"] == 0
    assert result["still_pending"] == 2
    for position in positions:
        stored = repo._select_one("positions", position.id)
        assert stored["funding_cost"] is None
        assert stored["realized_pnl"] is None
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM audit_events "
        "WHERE event_type='exchange_position_funding_settled'"
    ).fetchone()[0] == 0


def test_delayed_funding_revalidates_candidates_inside_writer_transaction(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    target = Position(
        id="funding-revalidation-target",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        entry_fee=0.05,
        execution_mode="bitget_demo",
        opened_at="2026-07-14T00:00:00+00:00",
    )
    repo.insert_position(target)
    from aletheia.portfolio.pnl import close_position

    closed_at = "2026-07-14T12:00:00+00:00"
    close_position(
        target.id,
        exit_price=110.0,
        actual_exit_fee=0.1,
        closed_at=closed_at,
        verified_exit_evidence={
            "source": "private_fills",
            "fill_ids": ["fill-revalidation"],
            "fill_quantity": 1.0,
        },
    )
    row = repo._select_one("positions", target.id)
    event = normalize_bitget_funding_rows([{
        "billId": "revalidation-event",
        "symbol": "BTCUSDT",
        "amount": "-1",
        "businessType": "contract_settle_fee",
        "coin": "USDT",
        "cTime": "1784030400000",
    }])[0]
    ledger = make_complete_ledger(
        broker_id="bitget_demo",
        account_environment="demo",
        source_endpoint="/private/funding",
        history_start_ms=1783987200000,
        history_end_ms=1784073600000,
        events=[event],
    )
    expected = compute_stored_broker_funding(
        repo.get_db(),
        position=row,
        closed_at=closed_at,
        ledger=ledger,
    )
    assert expected.complete is True

    repo.insert_position(Position(
        id="funding-revalidation-conflict",
        symbol="BTCUSDT",
        market_type="usdt_futures",
        direction="short",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="bitget_demo",
        opened_at="2026-07-14T00:00:00+00:00",
    ))
    from aletheia.portfolio.broker_funding_settlement import (
        materialize_broker_funding_economics,
    )

    with pytest.raises(ValueError, match="broker funding attribution changed"):
        materialize_broker_funding_economics(
            row,
            funding_cost=expected.funding_cost,
            funding_evidence=expected.evidence,
            funding_ledger=ledger,
        )
    stored = repo._select_one("positions", target.id)
    assert stored["funding_cost"] is None
    assert stored["realized_pnl"] is None
    assert repo.get_db().execute(
        "SELECT realized_pnl FROM position_close_events WHERE position_id=?",
        (target.id,),
    ).fetchone()[0] is None
    assert repo.list_outcome_hook_jobs(position_id=target.id) == []
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM audit_events "
        "WHERE event_type='exchange_position_funding_settled' AND entity_id=?",
        (target.id,),
    ).fetchone()[0] == 0


def test_broker_funding_audit_failure_replays_without_reapplying_settlement(
    tmp_path, monkeypatch,
):
    position_id, incomplete, funding, ledger = _seed_pending_broker_funding_close(
        tmp_path, monkeypatch,
    )
    from aletheia.files import jsonl as jsonl_mod
    from aletheia.portfolio.broker_funding_settlement import (
        materialize_broker_funding_economics,
    )

    real_writer = jsonl_mod.write_audits_deduplicated
    monkeypatch.setattr(
        jsonl_mod, "write_audits_deduplicated", lambda _events, **_kwargs: set(),
    )
    first = materialize_broker_funding_economics(
        incomplete,
        funding_cost=funding.funding_cost,
        funding_evidence=funding.evidence,
        funding_ledger=ledger,
    )
    stored = dict(repo._select_one("positions", position_id))
    audit = dict(repo.get_db().execute(
        "SELECT * FROM audit_events WHERE entity_id=? AND "
        "event_type='exchange_position_funding_settled'",
        (position_id,),
    ).fetchone())
    assert first["status"] == "settled"
    assert first["audit_mirror_status"] == "pending"
    assert audit["jsonl_mirror_status"] == "failed"
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM paper_account_events WHERE position_id=?",
        (position_id,),
    ).fetchone()[0] == 0

    monkeypatch.setattr(jsonl_mod, "write_audits_deduplicated", real_writer)
    repeated = materialize_broker_funding_economics(
        incomplete,
        funding_cost=funding.funding_cost,
        funding_evidence=funding.evidence,
        funding_ledger=ledger,
    )
    records = [
        json.loads(line)
        for line in (tmp_path / "logs" / "audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and json.loads(line).get("id") == audit["id"]
    ]
    assert repeated["status"] == "already_current"
    assert repeated["audit_mirror_status"] == "mirrored"
    assert len(records) == 1
    assert records[0]["timestamp"] == audit["timestamp"]
    assert records[0]["after"]["funding_economics_fingerprint"] == (
        json.loads(stored["reconciliation_evidence"])[
            "funding_economics_fingerprint"
        ]
    )
    assert dict(repo._select_one("positions", position_id)) == stored
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM audit_events WHERE entity_id=? AND "
        "event_type='exchange_position_funding_settled'",
        (position_id,),
    ).fetchone()[0] == 1
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM paper_account_events WHERE position_id=?",
        (position_id,),
    ).fetchone()[0] == 0


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
    monkeypatch.setattr(
        "aletheia.execution.reconciliation._UTAReconciliationProjection.get_funding_ledger",
        _funding,
    )
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.001,
        entry_price=60000.0,
        entry_fee=0.03,
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


def test_immediate_close_revalidates_alternate_symbol_funding_candidate(
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

    async def _funding(self, *, broker_id, start_time_ms, end_time_ms, **_kwargs):
        event = normalize_bitget_funding_rows([{
            "billId": "immediate-shared-event",
            "symbol": "BTCUSDT",
            "amount": "-1",
            "businessType": "contract_settle_fee",
            "coin": "USDT",
            "cTime": str((start_time_ms + end_time_ms) // 2),
        }])[0]
        return make_complete_ledger(
            broker_id=broker_id,
            account_environment="demo",
            source_endpoint="/api/v2/mix/account/bill",
            history_start_ms=start_time_ms,
            history_end_ms=end_time_ms,
            events=[event],
        )

    monkeypatch.setattr(BitgetClient, "get_funding_ledger", _funding)
    monkeypatch.setattr(
        "aletheia.execution.reconciliation._UTAReconciliationProjection.get_funding_ledger",
        _funding,
    )
    target = Position(
        id="immediate-funding-target",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.001,
        entry_price=60000.0,
        entry_fee=0.03,
        execution_mode="bitget_demo",
        opened_at="2026-07-14T00:00:00+00:00",
    )
    conflict = Position(
        id="immediate-funding-conflict",
        symbol="BTCUSDT",
        market_type="usdt_futures",
        direction="short",
        quantity=0.001,
        entry_price=60000.0,
        execution_mode="bitget_demo",
        opened_at="2026-07-14T00:00:00+00:00",
    )
    repo.insert_position(target)
    initial = repo._select_one("positions", target.id)
    from aletheia.execution import broker_funding

    original_compute = broker_funding.compute_stored_broker_funding
    compute_calls = 0

    def _compute_with_racing_candidate(*args, **kwargs):
        nonlocal compute_calls
        result = original_compute(*args, **kwargs)
        compute_calls += 1
        if compute_calls == 1:
            repo.insert_position(conflict)
        return result

    monkeypatch.setattr(
        broker_funding,
        "compute_stored_broker_funding",
        _compute_with_racing_candidate,
    )
    from aletheia.execution.bitget_broker import close_demo_position
    from aletheia.execution.reconciliation import reconcile

    close_result = close_demo_position(target.id, reason="take_profit")
    assert reconcile()["filled"] == 0
    stored = repo._select_one("positions", target.id)
    order = repo.get_order(close_result["order_id"])
    assert stored["status"] == "open"
    assert stored["funding_cost"] is None
    assert stored["realized_pnl"] == initial["realized_pnl"]
    assert order["status"] == "materialization_missing"
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (order["id"],),
    ).fetchone()[0] == 0
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM position_close_events WHERE position_id=?",
        (target.id,),
    ).fetchone()[0] == 0
    assert repo.list_outcome_hook_jobs(position_id=target.id) == []
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM audit_events "
        "WHERE entity_id=? AND event_type='position_closed'", (target.id,),
    ).fetchone()[0] == 0

    assert reconcile()["filled"] == 1
    stored = repo._select_one("positions", target.id)
    assert stored["status"] == "closed"
    assert stored["funding_cost"] is None
    assert stored["realized_pnl"] is None
    assert stored["outcome_exclusion_reason"] == "incomplete_funding_economics"


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
