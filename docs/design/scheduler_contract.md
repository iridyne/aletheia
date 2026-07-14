# Scheduler Contract

**Contract path**: `docs/design/scheduler_contract.md`
**Spec**: `tests/specs/spec_agent_contracts.py` (AC-S01, AC-S08)
**Impl**: `aletheia/agent_runtime/scheduler.py`
**Related**: `docs/design/agent_contracts.md` (§8 区别)

---

## §1 入口与生命周期

调度器通过 `init_scheduler()` 构建并启动，无单独的 `build_scheduler()` 函数。

| 函数 | 文件:行 | 说明 |
|---|---|---|
| `init_scheduler()` | `scheduler.py:298` | 构造 `BackgroundScheduler`、注册全部 job、调用 `_scheduler.start()` |
| `stop_scheduler()` | `scheduler.py:494` | `_scheduler.shutdown(wait=False)` → 置 `_scheduler = None` |
| `pause_scheduler()` | `scheduler.py:501` | APScheduler `pause()`；job 不再派发，但不销毁注册表 |
| `resume_scheduler()` | `scheduler.py:508` | 若 `_scheduler is None` 则按需重建；否则 `_scheduler.resume()` |

**调用路径**：`aletheia/server/app.py:301` 的 FastAPI lifespan 在 `run_migrations()` + `init_tool_registry()` 之后调用 `init_scheduler()`；shutdown 阶段 `app.py:387` 调用 `stop_scheduler()`。

**APScheduler 版本**：`3.11.2`（`pyproject.toml:15`，`uv.lock:259`），使用 `BackgroundScheduler`（`apscheduler.schedulers.background`）。

**启动时暂停**：`init_scheduler()` 在 `_scheduler.start()`（`scheduler.py:484`）后立即检查 `cfg.runtime.paused`；为真则调用 `_scheduler.pause()`（`scheduler.py:487`）。各 job 函数体内也独立检查 `cfg.runtime.paused` 并提前返回，双重防护。

---

## §2 执行器池

`_EXECUTOR_WORKERS` 字典定义于 `scheduler.py:96–107`，由 `scheduler.py:320` 展开为 APScheduler executors：

```python
# scheduler.py:96
_EXECUTOR_WORKERS = {
    "default": 2,
    "io":      1,
    "fast_io": 1,
    "observer":1,
    "agents":  1,
}
```

| 池名 | max_workers | 用途 |
|---|---|---|
| `default` | 2 | 通用基础设施（portfolio_snapshot） |
| `io` | 1 | 短时 read/write 基础任务（market_data、reconciliation、cleanup 等）；单写防 WAL 碰撞 |
| `fast_io` | 1 | 延迟敏感的基础设施（price_feed，30s 周期） |
| `observer` | 1 | boot catch-up 市场观察者；独立隔离，防被 LLM job 饿死 |
| `agents` | 1 | LLM/agent 密集型 job（paper_trader、post_trade_reviewer 等）；串行防 SQLite 竞争 |

**总工人数**：2 + 1 + 1 + 1 + 1 = **6**。

注：各池 `max_workers` 全部 ≤ 3，满足 spec AC-S01（`spec_agent_contracts.py:18–33`）。`_run_async_with_timeout` / `_run_sync_with_timeout` 内部会为每次调用临时创建 `max_workers=1` 的 `concurrent.futures.ThreadPoolExecutor`（`scheduler.py:165`、`scheduler.py:253`），用完立即 `shutdown(wait=False)`，不持久。

---

## §3 任务注册表

全局 `job_defaults`（`scheduler.py:321`）：`coalesce=True`，`max_instances=1`（universe_refresh 在 job 级别显式重复声明 `max_instances=1`，`scheduler.py:438`）。

以下为 `init_scheduler()` 内全部 `_scheduler.add_job()` 调用，按行号排序。`ohlcv_nightly_backfill` 受配置开关保护（见注）。

| # | job_id | 行 | trigger | 参数 | executor | timeout(s) | misfire_grace |
|---|---|---|---|---|---|---|---|
| 1 | `market_data` | 324 | interval | minutes=5 | io | 90 | 60 |
| 2 | `market_observer` | 328 | interval | minutes=15 | observer | 240 | 60 |
| 3 | `dreaming_daily` | 333 | cron | hour=2, minute=0, tz=Asia/Shanghai | agents | 300 | 300 |
| 4 | `insight_synthesizer` | 337 | cron | hour=2, minute=30, tz=Asia/Shanghai | agents | 120 | 600 |
| 5 | `confidence_calibration` | 341 | interval | hours=6 | io | 30 | 300 |
| 9 | `reconciliation` | 357 | interval | minutes=2 | io | — | 60 |
| 10 | `portfolio_snapshot` | 361 | interval | minutes=10 | default | 45 | 120 |
| 11 | `exchange_sync` | 366 | interval | minutes=5 | io | 60 | 60 |
| 12 | `paper_trader` | 371 | interval | minutes=5 | agents | 300 | 60 |
| 13 | `pi_trading_decision` | 376 | interval | minutes=cfg.pi_daemon.heartbeat_sec/60 (min 1, default 15) | agents | 300 | 120 |
| 14 | `price_feed` | 381 | interval | seconds=30 | fast_io | 45 | 15 |
| 15 | `position_manager` | 385 | interval | minutes=5 | io | — | 60 |
| 16 | `position_reconcile` | 389 | interval | minutes=5 | io | — | 60 |
| 17 | `tpsl_fill_check` | 394 | interval | seconds=90 | io | — | 30 |
| 18 | `cleanup` | 399 | cron | hour=3, minute=17, tz=Asia/Shanghai | io | — | 600 |
| 19 | `health_check` | 403 | interval | minutes=10 | io | — | 120 |
| 20 | `equity_observer` | 411 | cron | day_of_week=mon-fri, hour=14-21, minute=*/30, tz=UTC | agents | 240 | 300 |
| 21 | `prediction_observer` | 417 | interval | minutes=30 | agents | 240 | 120 |
| 22 | `post_trade_reviewer` | 424 | interval | hours=4 | agents | 300 | 300 |
| 23 | `risk_critic` | 430 | interval | hours=6 | agents | 300 | 300 |
| 24 | `universe_refresh` | 436 | interval | minutes=10 | io | 600 | 120 |
| 25 | `data_collectors` | 444 | interval | minutes=2 | io | 45 | 60 |
| 26 | `guard_promotion` | — | cron | hour=4, minute=0, tz=Asia/Shanghai | io | — | 600 |
| 27 | `ohlcv_nightly_backfill` | 457 | cron | hour=cfg.historical_data.nightly_backfill_hour_cst, minute=30, tz=Asia/Shanghai | io | 3600 | 600 |
| 28 | `okx_demo_symbols` | 464 | interval | hours=4 | io | — | 600 |
| 29 | `daily_summary` | 471 | cron | hour=cfg.telegram.daily_summary_hour_cst (default 8), minute=0, tz=Asia/Shanghai | io | 60 | 600 |
| 30 | `pi_operator_loop` | 477 | cron | hour=0,4,8,12,16,20, minute=15, tz=Asia/Shanghai | agents | 120 | 600 |

**注**：`ohlcv_nightly_backfill`（#27）仅当 `cfg.historical_data.nightly_backfill_enabled` 为真时注册（`scheduler.py:456`）。无论该开关状态，总 `add_job` 调用数为 30，其中条件分支使实际运行时 job 数为 29 或 30，均满足 AC-S08 ≥28 的下限。

**timeout 列说明**：`—` 表示该 job 没有在 `_ASYNC_JOB_TIMEOUT_SEC` 中声明超时，直接调用 `_guarded()` 包装，不经过 `_run_async_with_timeout` / `_run_sync_with_timeout` 的时间盒逻辑。

---

## §4 失败隔离

### 4.1 每 job 的 try/except 包装

每个 `_run_xxx()` 函数体都有独立的 `try/except Exception` 块。异常被捕获后以 `logger.warning` 或 `logger.error` 记录，不会向上抛给 APScheduler 调度线程。APScheduler 本身也会捕获 job 函数抛出的异常（`BackgroundScheduler` 底层）并记录，但不会终止调度器。

### 4.2 连续失败计数器 → infra_event（`scheduler.py:57–79`）

```python
# scheduler.py:57
_consecutive_failures: dict[str, int] = {}
_FAILURE_ALERT_THRESHOLD = 3            # scheduler.py:60

def _record_job_failure(job_id, error):  # scheduler.py:67
    _consecutive_failures[job_id] = _consecutive_failures.get(job_id, 0) + 1
    count = _consecutive_failures[job_id]
    if count >= _FAILURE_ALERT_THRESHOLD:
        emit_infra_event(severity="warning", source="scheduler",
                         code=f"job_consecutive_failure:{job_id}", ...)
```

当某 job 连续失败 ≥3 次，调用 `emit_infra_event`（`aletheia/infra/events.py`），事件去重窗口 300s。`_record_job_success()` 将计数器归零。目前显式调用 `_record_job_success/failure` 的 job：`exchange_sync`（`scheduler.py:738/744/747`）和 `portfolio_snapshot`（`scheduler.py:759/765/768`）。其余 job 的失败只记日志，不计入失败计数（非信号路径，不触发 infra_event）。

### 4.3 超时隔离（`_run_async_with_timeout` / `_run_sync_with_timeout`）

对于异步或长时同步 job，每次执行在独立的 `concurrent.futures.ThreadPoolExecutor(max_workers=1)` 线程中运行（`scheduler.py:165`、`scheduler.py:253`）。超时后：

1. APScheduler executor 线程得到控制权（不被卡死）。
2. 内部 daemon 线程被放弃（`pool.shutdown(wait=False)`），`_abandoned_thread_count` 递增（`scheduler.py:267`）。
3. 对于异步 job，在 caller 线程中立即将 `agent_runs.status` 更新为 `'timed_out'`（`scheduler.py:279–291`），防止 dashboard 出现幽灵 'running' 行。

### 4.4 返回值也属于任务成败

调度完成不只等于 coroutine 没抛异常，还必须诚实解释生产函数的返回值：

- 通用 `_async_run()` 收到 `run_agent()` 的 `completed_with_errors` 或非空 `errors` 时，抛出稳定码
  `scheduled_agent_reported_errors:<agent>`，让原 job wrapper 计入失败；只有 `completed + errors=[]`
  才保持成功。
- `universe.enabled=true` 时，`UniverseService.refresh()` 的 `None` 表示 scanner run 已失败，不是“正常空结果”；
  scheduler 必须写 failed agent log 并以 `universe_refresh_no_snapshot` 计入 job failure。正常空 Universe
  仍由 service 创建并返回 snapshot id。
- `data_collectors` 中由 scheduler 负责落库的 derivatives 输出，必须等 funding/OI 写库完成后才记域成功；
  任一写入失败以稳定 `derivatives_*_persist_failed` 码把域标为 failed，并把整个 fan-out 记为 partial failure，
  但不阻断同轮其它数据域。

---

## §5 SQLite 并发协议

三支柱均在本调度器中落实：

**支柱 1 — WAL + busy_timeout**（`aletheia/storage/sqlite.py:37–43`）

每个新连接在首次打开时执行：
```sql
PRAGMA journal_mode=WAL
PRAGMA synchronous=NORMAL
PRAGMA busy_timeout=30000   -- 30s SQLite 级别重试
PRAGMA wal_autocheckpoint=1000
```

**支柱 2 — 每线程独立连接**（`sqlite.py:11`）

`threading.local()` 保证每个 APScheduler worker thread 持有各自的 `sqlite3.Connection`，连接不跨线程共享。

**支柱 3 — `_run_job_guarded()` 清理包装**（`scheduler.py:110–134`）

每个 job 通过 `_guarded(job_id, fn)` 包装执行（`scheduler.py:137`）：

- **job 执行前**（在 `_SCHEDULER_JOB_LOCK` 保护下）：调用 `rollback_if_in_transaction()` 回滚上个 job 可能泄漏的事务。
- **job 执行后**（finally 块）：再次 `rollback_if_in_transaction()` + `close_db()`，释放线程局部连接，防止 WAL 写锁被空占。

注：`_SCHEDULER_JOB_LOCK`（`threading.RLock`，`scheduler.py:18`）仅包裹 cleanup 调用（前/后各一次），**不**包裹 job 函数体本身（`scheduler.py:125–134`），避免长时 job 持锁饿死其他线程。

对于带超时的 job，DB cleanup（`rollback_if_in_transaction` + `close_db`）在异步协程的 `finally` 块中于 `asyncio.run()` teardown 前执行（`scheduler.py:225–231`），或在同步 wrapper 的 `finally` 块中执行（`scheduler.py:161–163`），确保即使线程被放弃，写锁也已释放。

---

## §6 关停协议

`stop_scheduler()`（`scheduler.py:494–498`）：

```python
def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)   # 不等待 running jobs 完成
        _scheduler = None
```

`wait=False`：立即返回，不阻塞 FastAPI lifespan 关停路径。已派发但未完成的 job 在各自的 executor thread 中继续运行直到完成或 Python 进程退出。对于异步 job，`_run_async_with_timeout` 内部的 `concurrent.futures.ThreadPoolExecutor` 以 `wait=False` shutdown，守护线程随进程退出。

`pause_scheduler()` / `resume_scheduler()` 不影响已注册的 job 表，仅暂停/恢复 APScheduler 的派发循环。

---

## §7 不变量

| ID | 不变量 | 来源 | Spec |
|---|---|---|---|
| INV-SCH-1 | 所有命名池 `max_workers` 均 ≤ 3；临时池也 ≤ 1 | `_EXECUTOR_WORKERS`（`scheduler.py:96`）；`_run_async_with_timeout`（`scheduler.py:253`） | AC-S01 |
| INV-SCH-2 | `init_scheduler()` 注册 ≥ 28 个 job | `scheduler.py:324–481`，当前 30 个（条件分支最少 29） | AC-S08 |
| INV-SCH-3 | 任何 job 函数抛出的异常不得终止调度器线程 | 各 `_run_xxx()` 顶层 `try/except Exception`；APScheduler 内部也兜底捕获 | — |
| INV-SCH-4 | job 执行前后，本线程泄漏的 SQLite 事务必须被回滚 | `_run_job_guarded()`（`scheduler.py:110`）；async/sync timeout wrapper `finally` 块 | — |
| INV-SCH-5 | 连续失败 ≥ 3 次的 job 必须触发 infra_event（仅显式调用路径） | `_record_job_failure()`（`scheduler.py:67`） | — |
| INV-SCH-6 | 调度器启动时若 `cfg.runtime.paused=True` 则立即暂停 | `scheduler.py:486–488` | — |

---

## §8 边界：与 agent_contracts.md 的区别

`docs/design/agent_contracts.md` 管理**每个 agent 的 IO 契约**：输入 schema、输出 schema、权限模式、禁止意图、promotion gates。

本文档（`scheduler_contract.md`）管理**调度引擎本身**：执行器池拓扑、job 注册表（trigger/executor/timeout 三元组）、失败计数与 infra_event 触发、SQLite 并发清理包装、关停协议。

两份契约通过 `job_id` 字段对齐：agent_contracts 的 "§1 调度频率" 表中的 `job_id` 与本文档 §3 任务注册表的 `job_id` 一一对应。
