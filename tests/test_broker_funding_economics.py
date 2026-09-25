from __future__ import annotations

from tests.position_fixture import force_update_position

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


@pytest.fixture(autouse=True)
def configured_funding_retention(monkeypatch):
    from extensions.exchanges.composition import funding_history_retention_contract

    monkeypatch.setattr("aletheia.uta.manager._retention_contract", funding_history_retention_contract)


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
    force_update_position(position_id, **{field: value})
    before = _broker_funding_state(position_id)
    from aletheia.portfolio.broker_funding_settlement import (
        materialize_exchange_close_costs,
    )

    with pytest.raises(ValueError, match="snapshot changed"):
        materialize_exchange_close_costs(
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
        materialize_exchange_close_costs,
    )

    with pytest.raises(ValueError, match="snapshot changed"):
        materialize_exchange_close_costs(
            stale,
            funding_cost=funding.funding_cost,
            funding_evidence=funding.evidence,
            funding_ledger=ledger,
        )

    assert _broker_funding_state(position_id) == before


@pytest.mark.parametrize("fee_field", ["entry_fee", "exit_fee"])
def test_deferred_finalization_rejects_matching_invalid_fee_snapshots(tmp_path, monkeypatch, fee_field):
    position_id, _, funding, ledger = _seed_pending_broker_funding_close(tmp_path, monkeypatch)
    from aletheia.portfolio.broker_funding_settlement import materialize_exchange_close_costs

    force_update_position(position_id, **{fee_field: -1.0})
    repo.get_db().execute(
        f"UPDATE position_close_events SET {fee_field}=? WHERE position_id=?", (-1.0, position_id),
    )
    row = repo.get_position(position_id)
    before = _broker_funding_state(position_id)
    with pytest.raises(ValueError, match="realized economics"):
        materialize_exchange_close_costs(
            row, funding_cost=funding.funding_cost, funding_evidence=funding.evidence, funding_ledger=ledger,
        )
    assert _broker_funding_state(position_id) == before


def test_initial_and_deferred_economics_use_one_commit_owner(tmp_path, monkeypatch):
    from aletheia.portfolio import pnl, broker_funding_settlement as settlement

    calls = []
    commit = pnl._commit_close_economics

    def record_commit(current, fields, **kwargs):
        assert repo.get_db().in_transaction
        calls.append(current["status"])
        return commit(current, fields, **kwargs)

    monkeypatch.setattr(pnl, "_commit_close_economics", record_commit)
    monkeypatch.setattr(settlement, "_commit_close_economics", record_commit)
    position_id, incomplete, funding, ledger = _seed_pending_broker_funding_close(tmp_path, monkeypatch)
    assert calls == ["open"]
    first = settlement.materialize_exchange_close_costs(
        incomplete, funding_cost=funding.funding_cost,
        funding_evidence=funding.evidence, funding_ledger=ledger,
    )
    assert first["status"] == "settled"
    assert calls == ["open", "closed"]
    state = _broker_funding_state(position_id)
    assert len(state["close_events"]) == 1
    assert state["position"]["realized_pnl"] == state["close_events"][0]["realized_pnl"]
    assert state["position"]["realized_pnl"] is not None
    repeated = settlement.materialize_exchange_close_costs(
        incomplete, funding_cost=funding.funding_cost,
        funding_evidence=funding.evidence, funding_ledger=ledger,
    )
    assert repeated["status"] == "already_current"
    assert calls == ["open", "closed"]
    assert _broker_funding_state(position_id) == state


def test_deferred_commit_rolls_back_money_and_event_if_hook_enrollment_fails(tmp_path, monkeypatch):
    from aletheia.analytics import outcome_integrity
    from aletheia.portfolio import outcome_hooks, broker_funding_settlement as settlement

    position_id, incomplete, funding, ledger = _seed_pending_broker_funding_close(tmp_path, monkeypatch)
    before = _broker_funding_state(position_id)
    monkeypatch.setattr(outcome_integrity, "outcome_write_eligibility", lambda *a, **kw: (False, "attribution_read_failed"))

    def fail_enrollment(candidate):
        assert repo.get_db().in_transaction
        assert repo._select_one("positions", position_id)["realized_pnl"] is not None
        raise RuntimeError("hook enrollment failed")

    monkeypatch.setattr(outcome_hooks, "enqueue_outcome_hook_jobs", fail_enrollment)
    with pytest.raises(RuntimeError, match="hook enrollment failed"):
        settlement.materialize_exchange_close_costs(
            incomplete, funding_cost=funding.funding_cost,
            funding_evidence=funding.evidence, funding_ledger=ledger,
        )
    assert _broker_funding_state(position_id) == before


def test_broker_funding_exact_replay_is_a_noop(tmp_path, monkeypatch):
    position_id, stale, funding, ledger = _seed_pending_broker_funding_close(
        tmp_path, monkeypatch,
    )
    from aletheia.portfolio.broker_funding_settlement import (
        materialize_exchange_close_costs,
    )

    first = materialize_exchange_close_costs(
        stale,
        funding_cost=funding.funding_cost,
        funding_evidence=funding.evidence,
        funding_ledger=ledger,
    )
    after_first = _broker_funding_state(position_id)
    second = materialize_exchange_close_costs(
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
        materialize_exchange_close_costs(
            stale,
            funding_cost=funding.funding_cost,
            funding_evidence=changed_evidence,
            funding_ledger=ledger,
        )
    with pytest.raises(ValueError, match="cannot be overwritten"):
        materialize_exchange_close_costs(
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
        materialize_exchange_close_costs,
    )

    result = materialize_exchange_close_costs(
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
    repeated = materialize_exchange_close_costs(
        incomplete,
        funding_cost=funding.funding_cost,
        funding_evidence=funding.evidence,
        funding_ledger=ledger,
    )
    assert repeated["status"] == "already_current"
    with pytest.raises(ValueError, match="cannot be overwritten"):
        materialize_exchange_close_costs(
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

    async def funding_ledger(row, start_time_ms, end_time_ms):
        broker_id = row["execution_mode"]
        return make_complete_ledger(
            broker_id=broker_id,
            account_environment="demo",
            source_endpoint="/private/funding",
            history_start_ms=start_time_ms,
            history_end_ms=end_time_ms,
            events=[event],
        )

    from aletheia.portfolio import broker_funding_settlement as settlement

    monkeypatch.setattr(settlement, "_uta_funding_ledger", funding_ledger)
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
        materialize_exchange_close_costs,
    )

    with pytest.raises(ValueError, match="broker funding attribution changed"):
        materialize_exchange_close_costs(
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
        materialize_exchange_close_costs,
    )

    real_writer = jsonl_mod.write_audits_deduplicated
    monkeypatch.setattr(
        jsonl_mod, "write_audits_deduplicated", lambda _events, **_kwargs: set(),
    )
    first = materialize_exchange_close_costs(
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
    repeated = materialize_exchange_close_costs(
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
    _patch_private_fill_history(monkeypatch, fill_price=61000.0,)

    async def _funding(self, *, broker_id, start_time_ms, end_time_ms, **kwargs):
        return make_complete_ledger(
            broker_id=broker_id,
            account_environment="demo",
            source_endpoint="/api/v2/mix/account/bill",
            history_start_ms=start_time_ms,
            history_end_ms=end_time_ms,
            events=[],
        )

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
    from tests.uta.test_execution_coordinator import _insert_bound_position
    _insert_bound_position(position)
    from aletheia.execution.coordinator import close_position as close_demo_position
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

    _patch_private_fill_history(monkeypatch, fill_price=61000.0,)

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
    from tests.uta.test_execution_coordinator import _insert_bound_position
    _insert_bound_position(target)
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
    from aletheia.execution.coordinator import close_position as close_demo_position
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
@pytest.mark.parametrize("venue,amount,cost", [("okx", "-1.25", 1.25), ("binance", "0.75", -0.75)])
async def test_uta_private_funding_projection_preserves_signed_cost(monkeypatch, venue, amount, cost):
    from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter
    from aletheia.execution import reconciliation
    from tests.uta.test_ccxt_adapter import FakeExchange, _identity, _install, _profile

    now = datetime.now(timezone.utc)
    start_ms = int((now - timedelta(hours=2)).timestamp() * 1000)
    end_ms = int((now - timedelta(hours=1)).timestamp() * 1000)
    timestamp = start_ms + 30 * 60 * 1000
    exchange = FakeExchange()
    if venue == "okx":
        raw = dict(billId="funding-1", instId="BTC-USDT-SWAP", pnl=amount,
                   type="8", subType="173", ccy="USDT", ts=str(timestamp))
    else:
        raw = dict(tranId="funding-1", symbol="BTCUSDT", income=amount,
                   incomeType="FUNDING_FEE", asset="USDT", time=timestamp)
    exchange.ledger = [{"info": raw}]
    _install(monkeypatch, exchange)
    profile = _profile(venue)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)

    async def query(mode, method, request, *, expected_account):
        assert mode == identity.execution_mode
        assert expected_account == identity
        assert method == "get_funding_ledger"
        return await adapter.get_funding_ledger(request)

    monkeypatch.setattr(reconciliation, "query_uta", query)
    try:
        ledger = await reconciliation._UTAReconciliationProjection(
            identity.execution_mode, identity,
        ).get_funding_ledger(
            broker_id=identity.execution_mode, symbol="BTC/USDT",
            start_time_ms=start_ms, end_time_ms=end_ms,
        )
        assert ledger["events"][0]["event_id"] == "funding-1"
        assert ledger["events"][0]["funding_cost"] == cost
        assert [event[0] for event in exchange.events].count("fetch_ledger") == 1
    finally:
        await adapter.close()


def _seed_exchange_close_for_recovery(
    *,
    position_id: str,
    mode: str,
    symbol: str,
    opened_at: datetime,
    closed_at: datetime,
) -> None:
    position = Position(
        id=position_id,
        symbol=symbol,
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        entry_fee=0.05,
        current_price=110.0,
        execution_mode=mode,
        opened_at=opened_at.isoformat(),
    )
    repo.insert_position(position)
    from aletheia.portfolio.pnl import close_position

    close_position(
        position.id,
        exit_price=110.0,
        actual_exit_fee=0.1,
        closed_at=closed_at.isoformat(),
        verified_exit_evidence={
            "source": "private_fills",
            "fill_ids": [f"fill:{position.id}"],
            "fill_quantity": 1.0,
        },
    )


@pytest.mark.asyncio
async def test_expired_funding_prefix_does_not_starve_recoverable_close(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expired_closed = now - timedelta(days=100)
    for index in range(51):
        _seed_exchange_close_for_recovery(
            position_id=f"expired-{index:02d}",
            mode="bitget_demo",
            symbol="BTC/USDT",
            opened_at=expired_closed - timedelta(hours=1),
            closed_at=expired_closed,
        )
    _seed_exchange_close_for_recovery(
        position_id="expired-current-day",
        mode="bitget_demo",
        symbol="XRP/USDT",
        opened_at=now - timedelta(days=100),
        closed_at=now - timedelta(minutes=1),
    )
    _seed_exchange_close_for_recovery(
        position_id="transient",
        mode="bitget_demo",
        symbol="ETH/USDT",
        opened_at=now - timedelta(hours=4),
        closed_at=now - timedelta(hours=3),
    )
    _seed_exchange_close_for_recovery(
        position_id="recoverable",
        mode="okx_demo",
        symbol="SOL/USDT",
        opened_at=now - timedelta(hours=2),
        closed_at=now - timedelta(hours=1),
    )
    _seed_exchange_close_for_recovery(
        position_id="recoverable-binance",
        mode="binance_testnet",
        symbol="ADA/USDT",
        opened_at=now - timedelta(hours=2),
        closed_at=now - timedelta(minutes=30),
    )

    network_calls: list[tuple[str, str]] = []

    async def funding_ledger(row, start_time_ms, end_time_ms):
        broker_id = row["execution_mode"]
        symbol = row["symbol"]
        network_calls.append((broker_id, symbol))
        if symbol == "ETH/USDT":
            raise TimeoutError("transient fixture")
        return make_complete_ledger(
            broker_id=broker_id,
            account_environment=(
                "testnet" if broker_id == "binance_testnet" else "demo"
            ),
            source_endpoint="/fixture/private/funding",
            history_start_ms=start_time_ms,
            history_end_ms=end_time_ms,
            events=[],
        )

    from aletheia.portfolio import broker_funding_settlement as settlement

    monkeypatch.setattr(
        settlement,
        "_uta_funding_ledger",
        funding_ledger,
    )
    result = await settlement.settle_unsettled_exchange_closes()

    assert result["checked"] == 55
    assert result["broker_io_attempted"] == 3
    assert result["history_expired"] == 52
    assert result["settled"] == 2
    assert result["still_pending"] == 1
    assert result["terminal_total"] == 52
    assert result["retry_pending_total"] == 1
    assert network_calls == [
        ("bitget_demo", "ETH/USDT"),
        ("okx_demo", "SOL/USDT"),
        ("binance_testnet", "ADA/USDT"),
    ]
    expired = repo._select_one("positions", "expired-00")
    assert expired["realized_pnl"] is None
    assert expired["funding_cost"] is None
    assert expired["funding_source"] == "unavailable"
    assert expired["outcome_eligible"] == 0
    assert expired["outcome_exclusion_reason"] == (
        "broker_funding_history_expired"
    )
    terminal = json.loads(expired["reconciliation_evidence"])[
        "funding_terminal"
    ]
    assert terminal["reason"] == "broker_funding_history_expired"
    assert terminal["retention_days"] == 90
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM audit_events WHERE "
        "event_type='exchange_position_funding_history_expired'"
    ).fetchone()[0] == 52
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM outcome_hook_jobs "
        "WHERE position_id LIKE 'expired-%'"
    ).fetchone()[0] == 0
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM paper_account_events "
        "WHERE position_id LIKE 'expired-%'"
    ).fetchone()[0] == 0
    assert repo._select_one("positions", "recoverable")["realized_pnl"] is not None
    assert repo._select_one(
        "positions", "recoverable-binance"
    )["realized_pnl"] is not None

    from aletheia.analytics.outcome_integrity import assess_position_closure
    from aletheia.portfolio.pnl import position_realized_pnl_view

    assert assess_position_closure(dict(expired)).reason == (
        "broker_funding_history_expired"
    )
    assert position_realized_pnl_view(dict(expired))[
        "realized_pnl_unavailable_reason"
    ] == "broker_funding_history_expired"

    replay = await settlement.settle_unsettled_exchange_closes()
    assert replay["history_expired"] == 0
    assert replay["terminal_total"] == 52
    assert replay["retry_pending_total"] == 1
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM audit_events WHERE "
        "event_type='exchange_position_funding_history_expired'"
    ).fetchone()[0] == 52
    return replay


@pytest.mark.asyncio
async def test_funding_cursor_progress_survives_connection_reopen(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expired_closed = now - timedelta(days=100)
    for index in range(7):
        _seed_exchange_close_for_recovery(
            position_id=f"bounded-expired-{index}",
            mode="bitget_demo",
            symbol="BTC/USDT",
            opened_at=expired_closed - timedelta(hours=1),
            closed_at=expired_closed,
        )
    _seed_exchange_close_for_recovery(
        position_id="bounded-fresh",
        mode="okx_demo",
        symbol="SOL/USDT",
        opened_at=now - timedelta(hours=2),
        closed_at=now - timedelta(hours=1),
    )

    async def funding_ledger(row, start_time_ms, end_time_ms):
        broker_id = row["execution_mode"]
        return make_complete_ledger(
            broker_id=broker_id,
            account_environment="demo",
            source_endpoint="/fixture/private/funding",
            history_start_ms=start_time_ms,
            history_end_ms=end_time_ms,
            events=[],
        )

    from aletheia.portfolio import broker_funding_settlement as settlement
    from aletheia.storage.sqlite import close_db

    monkeypatch.setattr(
        settlement,
        "_uta_funding_ledger",
        funding_ledger,
    )
    first = await settlement.settle_unsettled_exchange_closes(scan_limit=3)
    assert (first["checked"], first["history_expired"], first["settled"]) == (
        3,
        3,
        0,
    )
    close_db()
    second = await settlement.settle_unsettled_exchange_closes(scan_limit=3)
    assert (second["checked"], second["history_expired"], second["settled"]) == (
        3,
        3,
        0,
    )
    close_db()
    third = await settlement.settle_unsettled_exchange_closes(scan_limit=3)
    assert (third["checked"], third["history_expired"], third["settled"]) == (
        2,
        1,
        1,
    )
    assert repo._select_one("positions", "bounded-fresh")[
        "funding_source"
    ] == "broker_ledger"
    return third


@pytest.mark.asyncio
async def test_funding_terminal_audit_retries_after_connection_reopen(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _seed_exchange_close_for_recovery(
        position_id="audit-expired",
        mode="bitget_demo",
        symbol="BTC/USDT",
        opened_at=now - timedelta(days=100),
        closed_at=now - timedelta(minutes=2),
    )
    _seed_exchange_close_for_recovery(
        position_id="audit-fresh",
        mode="okx_demo",
        symbol="SOL/USDT",
        opened_at=now - timedelta(hours=2),
        closed_at=now - timedelta(minutes=1),
    )

    async def funding_ledger(row, start_time_ms, end_time_ms):
        broker_id = row["execution_mode"]
        return make_complete_ledger(
            broker_id=broker_id,
            account_environment="demo",
            source_endpoint="/fixture/private/funding",
            history_start_ms=start_time_ms,
            history_end_ms=end_time_ms,
            events=[],
        )

    from aletheia.files import jsonl as jsonl_mod
    from aletheia.portfolio import broker_funding_settlement as settlement
    from aletheia.portfolio.funding_audit import (
        funding_audit_backlog,
        reconcile_funding_audits,
    )
    from aletheia.storage.sqlite import close_db

    real_writer = jsonl_mod.write_audits_deduplicated
    monkeypatch.setattr(
        jsonl_mod,
        "write_audits_deduplicated",
        lambda _events: set(),
    )
    monkeypatch.setattr(
        settlement,
        "_uta_funding_ledger",
        funding_ledger,
    )
    result = await settlement.settle_unsettled_exchange_closes()

    assert result["history_expired"] == 1
    assert result["settled"] == 1
    assert result["still_pending"] == 0
    assert result["audit_pending"] == 2
    assert funding_audit_backlog()["count"] == 2

    terminal_event = dict(repo.get_db().execute(
        "SELECT id, timestamp FROM audit_events "
        "WHERE entity_id='audit-expired' AND "
        "event_type='exchange_position_funding_history_expired'"
    ).fetchone())
    assert terminal_event["timestamp"] == (
        now - timedelta(days=10)
    ).isoformat()

    close_db()
    monkeypatch.setattr(
        jsonl_mod,
        "write_audits_deduplicated",
        real_writer,
    )
    reconciled = reconcile_funding_audits()
    assert reconciled["attempted"] == 2
    assert reconciled["mirrored"] == 2
    assert reconciled["pending"] == 0
    assert funding_audit_backlog()["count"] == 0


@pytest.mark.asyncio
async def test_expired_funding_terminalization_stale_owner_is_noop(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _seed_exchange_close_for_recovery(
        position_id="expired-owner-conflict",
        mode="bitget_demo",
        symbol="BTC/USDT",
        opened_at=now - timedelta(days=100, hours=1),
        closed_at=now - timedelta(days=100),
    )
    from aletheia.portfolio import broker_funding_settlement as settlement
    from aletheia.uta import manager as uta_manager

    real_contract = uta_manager.funding_history_retention_contract

    def change_owner(execution_mode, *, cfg):
        force_update_position(
            "expired-owner-conflict",
            outcome_exclusion_reason="manual_owner_state",
        )
        return real_contract(execution_mode, cfg=cfg)

    monkeypatch.setattr(
        uta_manager,
        "funding_history_retention_contract",
        change_owner,
    )
    result = await settlement.settle_unsettled_exchange_closes()
    stored = repo._select_one("positions", "expired-owner-conflict")

    assert result["checked"] == 1
    assert result["broker_io_attempted"] == 0
    assert result["history_expired"] == 0
    assert result["stale_noop"] == 1
    assert result["errors"] == []
    assert stored["outcome_exclusion_reason"] == "manual_owner_state"
    assert stored["realized_pnl"] is None
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM audit_events WHERE "
        "event_type='exchange_position_funding_history_expired'"
    ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_malformed_funding_window_remains_retryable_and_rotates(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _seed_exchange_close_for_recovery(
        position_id="malformed-window",
        mode="bitget_demo",
        symbol="BTC/USDT",
        opened_at=now - timedelta(hours=2),
        closed_at=now - timedelta(hours=1),
    )
    force_update_position("malformed-window", opened_at="not-a-timestamp")

    from aletheia.portfolio import broker_funding_settlement as settlement

    result = await settlement.settle_unsettled_exchange_closes()
    stored = repo._select_one("positions", "malformed-window")

    assert result["checked"] == 1
    assert result["broker_io_attempted"] == 0
    assert result["history_expired"] == 0
    assert result["still_pending"] == 1
    assert result["cursor_advanced"] is True
    assert result["errors"] == [
        "malformed-window: BrokerFundingIntegrityError"
    ]
    assert stored["outcome_exclusion_reason"] == "incomplete_funding_economics"
    assert stored["realized_pnl"] is None
