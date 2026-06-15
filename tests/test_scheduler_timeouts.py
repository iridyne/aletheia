import asyncio
import threading
import time
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


def test_scheduler_guard_serializes_background_jobs():
    from aletheia.agent_runtime import scheduler

    entered = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def first_job():
        order.append("first_enter")
        entered.set()
        assert release.wait(timeout=1)
        order.append("first_exit")

    def second_job():
        order.append("second_enter")

    t1 = threading.Thread(target=scheduler._run_job_guarded, args=("first", first_job))
    t2 = threading.Thread(target=scheduler._run_job_guarded, args=("second", second_job))

    t1.start()
    assert entered.wait(timeout=1)
    t2.start()
    time.sleep(0.05)
    assert order == ["first_enter"]

    release.set()
    t1.join(timeout=1)
    t2.join(timeout=1)

    assert order == ["first_enter", "first_exit", "second_enter"]
