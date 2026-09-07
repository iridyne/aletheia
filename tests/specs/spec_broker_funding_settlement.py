"""Executable CAS contract for delayed exchange funding settlement."""

from __future__ import annotations

from tests.position_fixture import force_update_position

import asyncio

import pytest

from tests import test_broker_funding_economics as funding_tests
from tests.specs.spec_meta import SpecClaim, spec_claim


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

    class Client:
        async def get_funding_ledger(self, **_kwargs):
            reader_started.set()
            await release_reader.wait()
            return ledger

        async def close(self):
            return None

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
    monkeypatch.setattr(settlement, "_client_for_mode", lambda _mode: Client())
    task = asyncio.create_task(settlement.settle_unsettled_exchange_closes(limit=1))
    await reader_started.wait()
    force_update_position(position_id, current_price=999.0)
    before = funding_tests._broker_funding_state(position_id)
    release_reader.set()

    result = await task
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
