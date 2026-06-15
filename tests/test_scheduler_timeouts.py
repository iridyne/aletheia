import asyncio
from types import SimpleNamespace


def _cfg(*, paused: bool = False):
    return SimpleNamespace(runtime=SimpleNamespace(paused=paused))


def test_market_data_job_is_timeboxed(monkeypatch, caplog):
    from aletheia.agent_runtime import scheduler
    from aletheia.trading import market_data

    async def slow_collect():
        await asyncio.sleep(1)

    monkeypatch.setattr(scheduler, "load_config", lambda: _cfg())
    monkeypatch.setattr(market_data, "collect_market_snapshots", slow_collect)
    monkeypatch.setitem(scheduler._ASYNC_JOB_TIMEOUT_SEC, "market_data", 0.01)

    scheduler._run_market_data()

    assert "market_data job timed out" in caplog.text


def test_data_collectors_job_is_timeboxed(monkeypatch, caplog):
    from aletheia.agent_runtime import scheduler

    async def slow_collectors(_cfg_obj, _tick):
        await asyncio.sleep(1)

    monkeypatch.setattr(scheduler, "load_config", lambda: _cfg())
    monkeypatch.setattr(scheduler, "_async_data_collectors", slow_collectors)
    monkeypatch.setitem(scheduler._ASYNC_JOB_TIMEOUT_SEC, "data_collectors", 0.01)
    monkeypatch.setattr(scheduler, "_DC_TICK", 0)

    scheduler._run_data_collectors()

    assert "data_collectors job timed out" in caplog.text
