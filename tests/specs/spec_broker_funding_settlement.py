"""Executable CAS contract for delayed exchange funding settlement."""

from __future__ import annotations

from tests.outcome_feedback import configured_outcome_feedback as configured_outcome_feedback

from tests.position_fixture import force_update_position

import asyncio

import pytest

from tests import test_broker_funding_economics as funding_tests
from tests.specs.spec_meta import SpecClaim, spec_claim

configured_funding_retention = funding_tests.configured_funding_retention


@spec_claim(
    SpecClaim(
        "EX-B19",
        "execution_contract",
        "§11.6 delayed broker funding CAS",
        "L2b",
        "A broker funding result collected across an await may settle only the exact "
        "Position and full-close snapshot that authorized the query; a stale contender "
        "writes no economics, audit, or outcome hook.",
        status="implemented",
    )
)
@pytest.mark.asyncio
async def test_delayed_broker_funding_rejects_stale_position_after_await(
    tmp_path, monkeypatch,
):
    position_id, stale, _funding, ledger = (
        funding_tests._seed_pending_broker_funding_close(tmp_path, monkeypatch)
    )
    from aletheia.files import jsonl
    from aletheia.portfolio import broker_funding_settlement as settlement
    from aletheia.portfolio import outcome_hooks
    from aletheia.storage import repositories as repo

    reader_started = asyncio.Event()
    release_reader = asyncio.Event()

    async def funding_ledger(_row, _start_ms, _end_ms):
        reader_started.set()
        await release_reader.wait()
        return ledger

    side_effect_calls = []
    monkeypatch.setattr(
        jsonl, "write_audit", lambda *_args, **_kwargs: side_effect_calls.append("audit"),
    )
    monkeypatch.setattr(
        outcome_hooks,
        "enqueue_outcome_hook_jobs",
        lambda *_args, **_kwargs: side_effect_calls.append("enqueue"),
    )
    monkeypatch.setattr(
        outcome_hooks,
        "process_outcome_hook_jobs",
        lambda *_args, **_kwargs: side_effect_calls.append("dispatch"),
    )
    monkeypatch.setattr(settlement, "_uta_funding_ledger", funding_ledger)
    task = asyncio.create_task(settlement.settle_unsettled_exchange_closes(limit=1))
    started = asyncio.create_task(reader_started.wait())
    try:
        done, _ = await asyncio.wait(
            {task, started}, timeout=10, return_when=asyncio.FIRST_COMPLETED,
        )
        assert started in done, task.result() if task.done() else "funding reader did not start"
        force_update_position(position_id, current_price=999.0)
        before = funding_tests._broker_funding_state(position_id)
        release_reader.set()
        result = await asyncio.wait_for(task, timeout=10)
    finally:
        release_reader.set()
        started.cancel()
        task.cancel()
        await asyncio.gather(started, task, return_exceptions=True)
    assert result["settled"] == 0
    assert result["still_pending"] == 1
    assert len(result["errors"]) == 1 and position_id in result["errors"][0]
    after = funding_tests._broker_funding_state(position_id)
    assert after == before
    assert after["position"]["realized_pnl"] is None
    assert after["position"]["funding_cost"] is None
    assert after["position"]["funding_source"] == "unavailable"
    assert after["close_events"][0]["realized_pnl"] is None
    assert side_effect_calls == []
    assert stale["current_price"] == 110.0


@spec_claim(
    SpecClaim(
        "EX-B20",
        "execution_contract",
        "§11.6 UTA identity resolved from position order_id",
        "L2b",
        "The broker funding settler passes order_id from the position row into "
        "_uta_funding_ledger so the UTA identity lookup never fails with "
        "uta_account_identity_unavailable for a position that has a bound order.",
        status="implemented",
    )
)
@pytest.mark.asyncio
async def test_broker_funding_settler_resolves_uta_identity_via_order_id(
    tmp_path, monkeypatch,
):
    """The direct UTA reader receives the whole Position, including its bound order."""
    from aletheia.execution.broker_funding import make_complete_ledger
    from aletheia.portfolio import broker_funding_settlement as settlement
    from aletheia.portfolio.pnl import close_position
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    from tests.test_demo_trading import _demo_config, _fresh_snapshot, setup_env
    from tests.uta.test_execution_coordinator import _insert_bound_position

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
        execution_mode="bitget_demo",
        opened_at="2026-07-14T00:00:00+00:00",
    )
    _insert_bound_position(position)

    close_position(
        position.id,
        exit_price=110.0,
        actual_exit_fee=0.1,
        closed_at="2026-07-14T12:00:00+00:00",
        verified_exit_evidence={
            "source": "private_fills",
            "fill_ids": ["fill-1"],
            "fill_quantity": 1.0,
        },
    )
    assert repo._select_one("positions", position.id)["realized_pnl"] is None

    ledger = make_complete_ledger(
        broker_id="bitget_demo",
        account_environment="demo",
        source_endpoint="uta_v1",
        history_start_ms=1783987200000,
        history_end_ms=1784073600000,
        events=[],
    )

    # Capture the exact Position handoff, not a reconstructed account stub.
    received_positions: list[dict] = []

    async def fake_uta_funding_ledger(pos, start_ms, end_ms):
        received_positions.append(dict(pos))
        return ledger

    monkeypatch.setattr(settlement, "_uta_funding_ledger", fake_uta_funding_ledger)

    result = await settlement.settle_unsettled_exchange_closes(limit=1)

    assert result["settled"] == 1, f"expected settled=1, got {result}"
    assert result["still_pending"] == 0
    assert result["errors"] == []
    assert len(received_positions) == 1
    assert received_positions[0].get("order_id") == position.order_id
    assert received_positions[0]["id"] == position.id
    assert received_positions[0]["market_type"] == position.market_type

    settled = repo._select_one("positions", position.id)
    assert settled["realized_pnl"] is not None
    assert settled["funding_cost"] == 0.0
    assert settled["funding_source"] == "broker_ledger"
