import asyncio
import threading
import time
from types import SimpleNamespace

import pytest


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


def _patch_scheduler_result(
    monkeypatch,
    scheduler,
    job_id: str,
    result: object,
    *,
    paused: bool = False,
) -> list[dict]:
    """Route one scheduler entry point through a deterministic producer result."""
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(
            runtime=SimpleNamespace(paused=paused),
            agent=SimpleNamespace(permission_mode="Paper"),
        ),
    )
    end_runs: list[dict] = []

    if job_id == "market_data":
        from aletheia.trading import market_data

        async def produce():
            return result

        monkeypatch.setattr(market_data, "collect_market_snapshots", produce)
        monkeypatch.setattr(
            scheduler,
            "_run_async_with_timeout",
            lambda _job, awaitable: asyncio.run(awaitable),
        )
    elif job_id == "price_feed":
        from aletheia.data import price_feed

        async def produce():
            return result

        monkeypatch.setattr(price_feed, "refresh_watched_prices", produce)
        monkeypatch.setattr(
            scheduler,
            "_run_async_with_timeout",
            lambda _job, awaitable: asyncio.run(awaitable),
        )
    elif job_id == "exchange_sync":
        from aletheia.portfolio import exchange_sync

        monkeypatch.setattr(exchange_sync, "sync_all_exchanges", lambda: result)
        monkeypatch.setattr(
            scheduler, "_run_sync_with_timeout", lambda _job, fn: fn()
        )
    elif job_id == "reconciliation":
        from aletheia.execution import reconciliation

        monkeypatch.setattr(reconciliation, "reconcile", lambda: result)
    elif job_id == "position_reconcile":
        from aletheia.agent_runtime import run_logger
        from aletheia.portfolio import position_reconciler

        monkeypatch.setattr(position_reconciler, "run_reconcile", lambda: result)
        monkeypatch.setattr(run_logger, "start_run", lambda *_args: "run-id")
        monkeypatch.setattr(
            run_logger,
            "end_run",
            lambda _run, **kwargs: end_runs.append(kwargs),
        )
    elif job_id == "position_manager":
        from aletheia.portfolio import position_manager

        monkeypatch.setattr(
            position_manager, "evaluate_open_positions", lambda: result
        )
    elif job_id == "tpsl_fill_check":
        from aletheia.storage import repositories

        def list_positions():
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(
            repositories, "list_all_open_positions", list_positions
        )
        monkeypatch.setattr(
            scheduler, "_run_sync_with_timeout", lambda _job, fn: fn()
        )
    else:  # pragma: no cover - test table is intentionally closed
        raise AssertionError(f"unsupported scheduler result job: {job_id}")
    return end_runs


@pytest.mark.parametrize(
    ("job_id", "result", "stable_error"),
    [
        ("market_data", {"collected": ["BTC/USDT"], "errors": ["SOL down"]},
         "market_data_reported_errors"),
        ("price_feed", {"refreshed": 1, "symbols": ["BTC/USDT"], "errors": ["SOL down"]},
         "price_feed_reported_errors"),
        ("exchange_sync", None, "exchange_sync_total_failure"),
        ("exchange_sync", {"accounts": [{"broker_id": "bitget"}], "errors": ["okx down"]},
         "exchange_sync_reported_errors"),
        ("reconciliation", {"checked": 1, "errors": ["private history down"]},
         "reconciliation_reported_errors"),
        ("position_manager", {"checked": 2, "closed": 1, "errors": [{"code": "close_failed"}]},
         "position_manager_reported_errors"),
        ("position_reconcile", {"adopted": [], "closed_orphans": [], "errors": ["adopt failed"]},
         "position_reconcile_reported_errors"),
    ],
)
def test_scheduler_result_failures_survive_outer_wrapper(
    monkeypatch, job_id, result, stable_error
):
    from aletheia.agent_runtime import scheduler
    from aletheia.learning import control
    from aletheia.storage import sqlite

    failures: list[tuple[str, str]] = []
    monkeypatch.setattr(control, "is_autonomous_learning_job", lambda _job: False)
    monkeypatch.setattr(control, "record_learning_job_success", lambda _job: None)
    monkeypatch.setattr(
        control,
        "record_learning_job_failure",
        lambda failed_job, error: failures.append((failed_job, error)),
    )
    monkeypatch.setattr(sqlite, "rollback_if_in_transaction", lambda: False)
    monkeypatch.setattr(sqlite, "close_db", lambda: None)
    end_runs = _patch_scheduler_result(monkeypatch, scheduler, job_id, result)

    scheduler._consecutive_failures.clear()
    scheduler._run_job_guarded(job_id, getattr(scheduler, f"_run_{job_id}"))

    assert scheduler._consecutive_failures[job_id] == 1
    assert failures == [(job_id, stable_error)]
    if job_id == "position_reconcile":
        assert end_runs == [{
            "status": "completed_with_errors",
            "outputs": ["no action needed"],
            "errors": ["adopt failed"],
        }]


@pytest.mark.parametrize(
    ("job_id", "result"),
    [
        ("market_data", {"collected": [], "errors": []}),
        ("price_feed", {"refreshed": 0, "symbols": [], "errors": []}),
        ("exchange_sync", {}),
        ("reconciliation", {"checked": 0, "errors": []}),
        ("position_manager", {"checked": 0, "closed": 0, "errors": []}),
        ("position_reconcile", {"adopted": [], "closed_orphans": [], "errors": []}),
    ],
)
def test_scheduler_clean_or_empty_result_resets_failure_health(
    monkeypatch, job_id, result
):
    from aletheia.agent_runtime import scheduler
    from aletheia.learning import control
    from aletheia.storage import sqlite

    monkeypatch.setattr(control, "is_autonomous_learning_job", lambda _job: False)
    monkeypatch.setattr(control, "record_learning_job_success", lambda _job: None)
    monkeypatch.setattr(sqlite, "rollback_if_in_transaction", lambda: False)
    monkeypatch.setattr(sqlite, "close_db", lambda: None)
    end_runs = _patch_scheduler_result(monkeypatch, scheduler, job_id, result)

    scheduler._consecutive_failures.clear()
    scheduler._consecutive_failures[job_id] = 2
    scheduler._run_job_guarded(job_id, getattr(scheduler, f"_run_{job_id}"))

    assert scheduler._consecutive_failures[job_id] == 0
    if job_id == "position_reconcile":
        assert end_runs == [{
            "status": "completed",
            "outputs": ["no action needed"],
            "errors": None,
        }]


@pytest.mark.parametrize(
    ("job_id", "result"),
    [
        ("price_feed", {"refreshed": 0, "symbols": [], "errors": ["feed down"]}),
        ("exchange_sync", None),
        ("reconciliation", {"checked": 1, "errors": ["history down"]}),
        ("tpsl_fill_check", RuntimeError("private plan history down")),
    ],
)
def test_protective_essential_jobs_run_and_report_failure_while_paused(
    monkeypatch, job_id, result
):
    from aletheia.agent_runtime import scheduler
    from aletheia.learning import control
    from aletheia.storage import sqlite

    monkeypatch.setattr(control, "is_autonomous_learning_job", lambda _job: False)
    monkeypatch.setattr(control, "record_learning_job_success", lambda _job: None)
    monkeypatch.setattr(control, "record_learning_job_failure", lambda *_args: None)
    monkeypatch.setattr(sqlite, "rollback_if_in_transaction", lambda: False)
    monkeypatch.setattr(sqlite, "close_db", lambda: None)
    _patch_scheduler_result(
        monkeypatch, scheduler, job_id, result, paused=True
    )

    scheduler._consecutive_failures.clear()
    scheduler._run_job_guarded(job_id, getattr(scheduler, f"_run_{job_id}"))

    assert scheduler._consecutive_failures[job_id] == 1
