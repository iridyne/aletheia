from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest


def _paper_futures_row() -> dict[str, str]:
    return {
        "symbol": "BTC/USDT",
        "market_type": "usdt_futures",
        "execution_mode": "paper_internal",
    }


def test_sync_close_anchor_seed_drains_tasks_before_closing_its_loop(monkeypatch):
    from aletheia.portfolio import local_funding

    state: dict[str, object] = {"cancelled": False}

    async def fake_seed(*_args, **_kwargs):
        async def linger_until_cancelled() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise

        task = asyncio.create_task(linger_until_cancelled())
        state["task"] = task
        await asyncio.sleep(0)
        return {"inserted": False, "attempts": [], "skipped": None}

    monkeypatch.setattr(
        local_funding,
        "_seed_close_anchor_snapshot_async",
        fake_seed,
    )

    result = local_funding.try_seed_close_anchor_snapshot(
        _paper_futures_row(),
        datetime.now(timezone.utc).isoformat(),
    )

    task = state["task"]
    assert isinstance(task, asyncio.Task)
    assert result["inserted"] is False
    assert state["cancelled"] is True
    assert task.cancelled()


@pytest.mark.asyncio
async def test_sync_close_anchor_seed_still_skips_inside_running_loop(monkeypatch):
    from aletheia.portfolio import local_funding

    async def unexpected_seed(*_args, **_kwargs):
        raise AssertionError("nested close-anchor loop must not start")

    monkeypatch.setattr(
        local_funding,
        "_seed_close_anchor_snapshot_async",
        unexpected_seed,
    )

    result = local_funding.try_seed_close_anchor_snapshot(
        _paper_futures_row(),
        datetime.now(timezone.utc).isoformat(),
    )

    assert result == {
        "inserted": False,
        "attempts": [],
        "skipped": "running_loop_reentrancy",
    }
