from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_realtime_hub_shares_one_sampler_pair_across_clients(monkeypatch):
    from aletheia.server.routes import ws as ws_routes

    release = asyncio.Event()
    starts = {"alert": 0, "regular": 0}

    async def fake_alert(_hub, _loop):
        starts["alert"] += 1
        await release.wait()

    async def fake_regular(_hub, _loop):
        starts["regular"] += 1
        await release.wait()

    monkeypatch.setattr(ws_routes, "_alert_loop", fake_alert)
    monkeypatch.setattr(ws_routes, "_regular_loop", fake_regular)

    hub = ws_routes._RealtimeHub()
    first = object()
    second = object()

    await hub.add(first)
    first_tasks = (hub.alert_task, hub.regular_task)
    await hub.add(second)
    await asyncio.sleep(0)

    assert starts == {"alert": 1, "regular": 1}
    assert (hub.alert_task, hub.regular_task) == first_tasks

    await hub.remove(first)
    assert hub.alert_task is first_tasks[0]
    assert hub.regular_task is first_tasks[1]

    release.set()
    await hub.remove(second)
    assert hub.alert_task is None
    assert hub.regular_task is None
