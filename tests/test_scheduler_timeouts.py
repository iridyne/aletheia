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


def test_scheduler_guard_allows_concurrent_job_bodies():
    """Job bodies run concurrently (no global lock during fn()).

    The lock only covers pre/post DB cleanup. This prevents the deadlock where
    a slow network job pins the RLock and starves all other scheduler jobs.
    """
    from aletheia.agent_runtime import scheduler

    first_entered = threading.Event()
    second_entered = threading.Event()
    release = threading.Event()

    def first_job():
        first_entered.set()
        assert release.wait(timeout=2)

    def second_job():
        second_entered.set()

    t1 = threading.Thread(target=scheduler._run_job_guarded, args=("first", first_job))
    t2 = threading.Thread(target=scheduler._run_job_guarded, args=("second", second_job))

    t1.start()
    assert first_entered.wait(timeout=1)
    t2.start()
    # Second job should enter while first is still running (no serialization)
    assert second_entered.wait(timeout=1)

    release.set()
    t1.join(timeout=2)
    t2.join(timeout=2)
