import asyncio
import threading
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


def test_symbol_independent_context_collectors_run_without_instruments(monkeypatch):
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

    asyncio.run(scheduler._async_data_collector_context(cfg, tick=1))

    assert bounded_labels == ["defillama", "coingecko_top", "macro"]
    assert ("volume_scanner", "not_configured") in health
    assert ("hyperliquid", "not_configured") in health
    assert ("coinalyze", "not_configured") in health


def test_shadow_context_lane_does_not_inherit_exhausted_core_budget(monkeypatch):
    """Shadow the reported incident without network or a real scheduler.

    The core lane consumes 38 seconds of its own 40-second budget.  In the old
    combined implementation, Hyperliquid and DefiLlama were reached only after
    that work and were recorded ``skipped_budget``.  The context lane must start
    with a new deadline, so both collectors still run in this deterministic
    reproduction.
    """
    from aletheia.agent_runtime import scheduler
    from aletheia.data.collectors.coingecko_top import CoinGeckoTopCollector
    from aletheia.data.collectors.defillama import DefiLlamaCollector
    from aletheia.data.collectors.derivatives import DerivativesCollector
    from aletheia.data.collectors.hyperliquid import HyperliquidCollector
    from aletheia.data.collectors.macro import MacroCollector
    from aletheia.data.collectors.microstructure import MicrostructureCollector
    from aletheia.data.collectors.volume_scanner import VolumeScannerCollector

    class _Clock:
        now = 1_000.0

        def monotonic(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds

    clock = _Clock()
    calls: list[str] = []
    health: list[tuple[str, str]] = []

    async def _core_micro(self, instruments):
        calls.append("microstructure")
        clock.advance(15)
        return {"snapshots": [], "errors": []}

    async def _core_derivatives(self, symbols):
        calls.append("derivatives")
        clock.advance(15)
        return {"funding": [], "open_interest": [], "errors": []}

    async def _volume(self, instruments):
        calls.append("volume_scanner")
        clock.advance(8)
        return {"top_symbols": [], "observed_at": "shadow", "errors": []}

    async def _coingecko(self, top_n):
        calls.append("coingecko_top")
        return {"rows": [], "movers_summary": None, "errors": []}

    async def _macro(self, symbols):
        calls.append("macro")
        return {"snapshot": None, "errors": []}

    async def _hyperliquid(self, symbols):
        calls.append("hyperliquid")
        return {"funding": [], "open_interest": [], "errors": []}

    async def _defillama(self):
        calls.append("defillama")
        return {"snapshots": [], "errors": []}

    monkeypatch.setattr("time.monotonic", clock.monotonic)
    monkeypatch.setattr(
        scheduler,
        "_get_observed_instruments",
        lambda _cfg: [SimpleNamespace(symbol="BTC/USDT")],
    )
    monkeypatch.setattr(
        scheduler, "_get_derivative_symbols", lambda _instruments: ["BTC/USDT"]
    )
    monkeypatch.setattr(
        scheduler,
        "_record_collector_health",
        lambda label, status, **_kwargs: health.append((label, status)),
    )
    monkeypatch.setattr(MicrostructureCollector, "collect", _core_micro)
    monkeypatch.setattr(DerivativesCollector, "collect", _core_derivatives)
    monkeypatch.setattr(VolumeScannerCollector, "collect", _volume)
    monkeypatch.setattr(CoinGeckoTopCollector, "collect", _coingecko)
    monkeypatch.setattr(MacroCollector, "collect", _macro)
    monkeypatch.setattr(HyperliquidCollector, "collect", _hyperliquid)
    monkeypatch.setattr(DefiLlamaCollector, "collect", _defillama)

    cfg = SimpleNamespace(
        market_data=SimpleNamespace(
            enable_coingecko_top=True,
            coingecko_top_n=10,
            enable_coinalyze=False,
            enable_hyperliquid=True,
            enable_defillama=True,
        )
    )

    asyncio.run(scheduler._async_data_collectors(cfg, tick=1))
    asyncio.run(scheduler._async_data_collector_context(cfg, tick=1))

    assert calls[:3] == ["microstructure", "derivatives", "volume_scanner"]
    assert "hyperliquid" in calls
    assert "defillama" in calls
    assert ("hyperliquid", "empty") in health
    assert ("defillama", "empty") in health
    assert ("hyperliquid", "skipped_budget") not in health
    assert ("defillama", "skipped_budget") not in health


def test_async_timeout_worker_reuses_one_thread(monkeypatch):
    """Repeated cooperative timeouts do not allocate disposable worker threads."""
    from aletheia.agent_runtime import scheduler

    monkeypatch.setitem(scheduler._ASYNC_JOB_TIMEOUT_SEC, "market_data", 0.01)
    scheduler._async_timeout_worker.close()

    async def slow():
        await asyncio.sleep(1)

    try:
        for _ in range(5):
            with pytest.raises(TimeoutError):
                scheduler._run_async_with_timeout("market_data", slow())

        workers = [
            thread
            for thread in threading.enumerate()
            if thread.name == "aletheia-scheduler-async"
        ]
        metrics = scheduler.scheduler_timeout_metrics_snapshot()
        assert len(workers) == 1
        assert metrics["async_pending"] == 0
        assert metrics["abandoned_threads"] == 0
    finally:
        scheduler._async_timeout_worker.close()


def test_insight_synthesizer_uses_owned_async_timeout_loop(monkeypatch):
    """The memo job must not create and close a disposable event loop."""
    from aletheia.agent_runtime import scheduler
    from aletheia.analytics import insight_synthesizer, reasoning_feedback

    submitted = []
    successes = []

    async def synthesize():
        return {"sample_size": 2}

    def run_bounded(job_id, awaitable):
        submitted.append(job_id)
        return asyncio.run(awaitable)

    monkeypatch.setattr(scheduler, "load_config", _cfg)
    monkeypatch.setattr(scheduler, "_run_async_with_timeout", run_bounded)
    monkeypatch.setattr(scheduler, "_record_job_success", successes.append)
    monkeypatch.setattr(insight_synthesizer, "synthesize_trading_memo", synthesize)
    monkeypatch.setattr(reasoning_feedback, "build_reasoning_feedback", lambda: None)

    scheduler._run_insight_synthesizer()

    assert submitted == ["insight_synthesizer"]
    assert successes == ["insight_synthesizer"]


def test_insight_synthesizer_preserves_timeout_failure_and_feedback(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.analytics import insight_synthesizer, reasoning_feedback

    failures = []
    feedback_runs = []

    async def synthesize():
        return None

    def time_out(job_id, awaitable):
        assert job_id == "insight_synthesizer"
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(scheduler, "load_config", _cfg)
    monkeypatch.setattr(scheduler, "_run_async_with_timeout", time_out)
    monkeypatch.setattr(scheduler, "_record_job_failure", lambda *args: failures.append(args))
    monkeypatch.setattr(
        scheduler,
        "_record_job_success",
        lambda *_args: pytest.fail("a timed-out memo must not be recorded as successful"),
    )
    monkeypatch.setattr(insight_synthesizer, "synthesize_trading_memo", synthesize)
    monkeypatch.setattr(
        reasoning_feedback,
        "build_reasoning_feedback",
        lambda: feedback_runs.append("ran"),
    )

    scheduler._run_insight_synthesizer()

    assert failures == [("insight_synthesizer", "timeout")]
    assert feedback_runs == ["ran"]


def test_async_timeout_worker_rejects_when_capacity_is_exhausted(monkeypatch):
    """An unresponsive loop cannot build an unbounded scheduler queue."""
    from aletheia.agent_runtime import scheduler

    worker = scheduler._PersistentAsyncTimeoutWorker()
    monkeypatch.setattr(scheduler, "_TIMEOUT_WORKER_CAPACITY", 1)
    release = threading.Event()

    async def blocked():
        await asyncio.to_thread(release.wait)

    first = worker.submit(blocked())
    try:
        extra = blocked()
        with pytest.raises(RuntimeError, match="queue is full"):
            worker.submit(extra)
        assert extra.cr_frame is None
    finally:
        release.set()
        first.cancel()
        worker.close()


def test_async_timeout_worker_rejects_submissions_once_stopping():
    from aletheia.agent_runtime import scheduler

    worker = scheduler._PersistentAsyncTimeoutWorker("stopping-worker")
    worker._stopping = True
    coroutine = asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="stopping"):
        worker.submit(coroutine)
    assert coroutine.cr_frame is None


def test_sync_timeout_worker_reuses_one_daemon_thread():
    from aletheia.agent_runtime import scheduler

    for _ in range(5):
        assert scheduler._run_sync_with_timeout("portfolio_snapshot", lambda: "ok") == "ok"

    workers = [
        thread
        for thread in threading.enumerate()
        if thread.name == "aletheia-scheduler-sync"
    ]
    metrics = scheduler.scheduler_timeout_metrics_snapshot()
    assert len(workers) == 1
    assert workers[0].daemon is True
    assert metrics["sync_pending"] == 0
    assert metrics["abandoned_threads"] == 0


def test_sync_timeout_worker_rejects_when_capacity_is_exhausted(monkeypatch):
    from aletheia.agent_runtime import scheduler

    monkeypatch.setattr(scheduler, "_TIMEOUT_WORKER_CAPACITY", 1)
    worker = scheduler._PersistentSyncTimeoutWorker()
    started = threading.Event()
    release = threading.Event()

    def blocked():
        started.set()
        release.wait(timeout=2)

    first = worker.submit(blocked)
    assert started.wait(timeout=1)
    with pytest.raises(RuntimeError, match="queue is full"):
        worker.submit(lambda: None)
    release.set()
    assert first.result(timeout=1) is None


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


def test_derivatives_history_uses_bounded(monkeypatch):
    """derivatives_history backfill routes through _bounded(), not bare asyncio.wait_for."""
    from aletheia.agent_runtime import scheduler

    bounded_labels = []

    async def fake_bounded(coro, label, cap, deadline):
        bounded_labels.append(label)
        coro.close()
        return None

    monkeypatch.setattr(scheduler, "_bounded", fake_bounded)
    monkeypatch.setattr(
        scheduler,
        "_get_observed_instruments",
        lambda _cfg: [SimpleNamespace(symbol="BTC/USDT")],
    )
    monkeypatch.setattr(scheduler, "_record_collector_health", lambda *a, **kw: None)

    cfg = SimpleNamespace(
        market_data=SimpleNamespace(
            enable_coingecko_top=False,
            enable_hyperliquid=False,
            enable_defillama=False,
            enable_coinalyze=False,
            derivatives_history_hours=1,
        )
    )

    asyncio.run(scheduler._async_data_collector_context(cfg, tick=1))

    assert "derivatives_history" in bounded_labels


def test_bounded_slow_cancel_does_not_stall_caller(monkeypatch):
    """A coroutine that delays CancelledError cleanup cannot stall _bounded() past its cap."""
    import time
    from aletheia.agent_runtime import scheduler

    monkeypatch.setattr(scheduler, "_record_collector_health", lambda *a, **kw: None)

    async def slow_to_cancel():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(2)  # simulate CCXT cleanup delay
            raise

    async def _run():
        import time as _t

        return await scheduler._bounded(
            slow_to_cancel(), "test_slow_ccxt", 0.05, _t.monotonic() + 60.0
        )

    start = time.monotonic()
    result = asyncio.run(_run())
    elapsed = time.monotonic() - start

    assert result is None
    assert elapsed < 0.5, f"_bounded stalled {elapsed:.2f}s; slow-cancel must not block caller"
