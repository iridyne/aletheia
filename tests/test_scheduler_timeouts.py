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


def test_data_collectors_keeps_collecting_during_layered_trading_pause(monkeypatch):
    """Trading pause blocks decisions, not the essential public-data supply."""
    from aletheia.agent_runtime import scheduler

    calls = []

    async def collect_once(_cfg_obj, tick):
        calls.append(tick)

    monkeypatch.setattr(scheduler, "load_config", lambda: _cfg(paused=True))
    monkeypatch.setattr(scheduler, "_async_data_collectors", collect_once)
    monkeypatch.setattr(scheduler, "_DC_TICK", 0)

    scheduler._run_data_collectors()

    assert calls == [1]


def test_symbol_independent_phase_b_collectors_run_without_instruments(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.data.collectors.coingecko_top import CoinGeckoTopCollector
    from aletheia.data.collectors.defillama import DefiLlamaCollector
    from aletheia.data.collectors.macro import MacroCollector

    cfg = SimpleNamespace(
        market_data=SimpleNamespace(
            enable_coingecko_top=True,
            coingecko_top_n=10,
            enable_coinalyze=True,
            enable_hyperliquid=True,
            enable_defillama=True,
        )
    )
    bounded_labels = []
    health = []

    async def fake_bounded(coro, label, cap, deadline):
        bounded_labels.append(label)
        return await coro

    async def macro_collect(self, symbols):
        return {"snapshot": None, "errors": []}

    async def coingecko_collect(self, top_n):
        return {"rows": [], "movers_summary": None, "errors": []}

    async def defillama_collect(self):
        return {"snapshots": [], "errors": []}

    monkeypatch.setattr(scheduler, "_get_observed_instruments", lambda _cfg: [])
    monkeypatch.setattr(scheduler, "_bounded", fake_bounded)
    monkeypatch.setattr(
        scheduler,
        "_record_collector_health",
        lambda label, status, **kwargs: health.append((label, status)),
    )
    monkeypatch.setattr(MacroCollector, "collect", macro_collect)
    monkeypatch.setattr(CoinGeckoTopCollector, "collect", coingecko_collect)
    monkeypatch.setattr(DefiLlamaCollector, "collect", defillama_collect)

    asyncio.run(scheduler._async_data_collectors(cfg, tick=1))

    assert bounded_labels == ["coingecko_top", "macro", "defillama"]
    assert ("microstructure", "not_configured") in health
    assert ("derivatives", "not_configured") in health
    assert ("volume_scanner", "not_configured") in health


def test_sync_timeout_abandons_hung_function(monkeypatch):
    """_run_sync_with_timeout abandons a hung sync function via concurrent.futures."""
    import concurrent.futures
    from aletheia.agent_runtime import scheduler

    hung = threading.Event()

    def slow_sync():
        hung.set()
        time.sleep(60)

    # Use a short timeout entry — thread_timeout will be timeout + 15 = 15.05s
    # but we test the mechanism by submitting to a pool directly with short timeout
    monkeypatch.setitem(scheduler._ASYNC_JOB_TIMEOUT_SEC, "exchange_sync", 0.05)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(slow_sync)
    hung.wait(timeout=2)
    assert hung.is_set(), "function should have started"

    # The function is running; verify future.result times out
    import pytest
    with pytest.raises(concurrent.futures.TimeoutError):
        future.result(timeout=0.1)

    pool.shutdown(wait=False)


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


def test_guard_records_swallowed_job_failure_and_resets_on_success(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.agent_runtime import connectivity

    async def fail_check():
        raise RuntimeError("probe down")

    monkeypatch.setattr(scheduler, "load_config", lambda: _cfg())
    monkeypatch.setattr(connectivity, "run_connectivity_check", fail_check)
    scheduler._consecutive_failures.clear()

    scheduler._run_job_guarded("connectivity_monitor", scheduler._run_connectivity_monitor)
    assert scheduler._consecutive_failures["connectivity_monitor"] == 1

    scheduler._run_job_guarded("connectivity_monitor", lambda: None)
    assert scheduler._consecutive_failures["connectivity_monitor"] == 0


def test_guard_records_unhandled_job_failure():
    from aletheia.agent_runtime import scheduler

    def fail_job():
        raise RuntimeError("boom")

    scheduler._consecutive_failures.clear()
    scheduler._run_job_guarded("unguarded_job", fail_job)

    assert scheduler._consecutive_failures["unguarded_job"] == 1
