# Scheduler Contract

**Contract path**: `docs/design/contracts/scheduler_contract.md`
**Spec**: `tests/specs/spec_agent_contracts.py` (AC-S01, AC-S08), `tests/specs/spec_pi_decision_chain.py` (PI-S06, PI-S07), `tests/specs/spec_pipeline_tpsl_private_materialization.py` (PL-TPSL-05)
**Impl**: `aletheia/agent_runtime/scheduler.py`
**Related**: `docs/design/contracts/agent_contracts.md` (§8 区别)

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

**启动时暂停**：`init_scheduler()` 在 `_scheduler.start()` 后检查 `cfg.runtime.paused`。当 `layered_pause_enabled=true` 时，只暂停开仓、观察者和 LLM/决策任务；下列 `_ESSENTIAL_JOBS` 仍派发，以维持市场价格、账户视图、保护性退出、对账、健康检查和数据维护。只有 `layered_pause_enabled=false` 的 blanket pause 才暂停整个 APScheduler。集合内任务不得再在函数体中因 `runtime.paused` 提前返回，这一一致性由 `tests/test_layered_pause.py` 锁定。

当前 essential 集合：`market_data`、`data_collectors`、`data_collector_context`、`connectivity_monitor`、`portfolio_snapshot`、`price_feed`、`ohlcv_nightly_backfill`、`ohlcv_active_backfill`、`exchange_sync`、`health_check`、`cleanup`、`news_fetch`、`okx_demo_symbols`、`reconciliation`、`position_reconcile`、`tpsl_fill_check`、`outcome_settler`、`outcome_hook_replay`、`shadow_resolver`、`learning_contribution_evaluator`、`learning_measurement_startup_catchup`。

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
| 2 | `market_observer` | 328 | interval | minutes=10 | observer | 500 | 60 |
| 3 | `dreaming_daily` | 333 | cron | hour=2, minute=0, tz=Asia/Shanghai | agents | 300 | 300 |
| 4 | `insight_synthesizer` | 337 | cron | hour=2, minute=30, tz=Asia/Shanghai | agents | 120 | 600 |
| 5 | `confidence_calibration` | 341 | interval | hours=6 | io | 30 | 300 |
| 9 | `reconciliation` | 357 | interval | minutes=2 | io | — | 60 |
| 10 | `portfolio_snapshot` | 361 | interval | minutes=10 | default | 45 | 120 |
| 11 | `exchange_sync` | 366 | interval | minutes=5 | io | 60 | 60 |
| 12 | `paper_trader` | 371 | interval | minutes=5 | agents | 300 | 60 |
| 13 | `pi_trading_decision` | 376 | interval | minutes=15（固定安全网周期） | agents | 300 | 120 |
| 14 | `price_feed` | 381 | interval | seconds=30 | fast_io | 45 | 15 |
| 15 | `position_manager` | 385 | interval | minutes=5 | io | — | 60 |
| 16 | `position_reconcile` | 389 | interval | minutes=5 | io | — | 60 |
| 17 | `tpsl_fill_check` | 394 | interval | seconds=90 | io | 45 | 30 |
| 18 | `cleanup` | 399 | cron | hour=3, minute=17, tz=Asia/Shanghai | io | — | 600 |
| 19 | `health_check` | 403 | interval | minutes=10 | io | — | 120 |
| 20 | `equity_observer` | 411 | cron | day_of_week=mon-fri, hour=14-21, minute=*/30, tz=UTC | agents | 240 | 300 |
| 21 | `prediction_observer` | 417 | interval | minutes=30 | agents | 240 | 120 |
| 22 | `post_trade_reviewer` | 424 | interval | hours=4 | agents | 300 | 300 |
| 23 | `risk_critic` | 430 | interval | hours=6 | agents | 300 | 300 |
| 24 | `universe_refresh` | 436 | interval | minutes=10 | io | 600 | 120 |
| 25 | `data_collectors` | 444 | interval | minutes=2 | io | 45 | 60 |
| 25a | `data_collector_context` | 449 | interval | minutes=10 | io | 45 | 60 |
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

每个注册任务都通过 `_guarded(job_id, fn)` 进入 `_run_job_guarded()`。该最外层包装会捕获未处理异常、登记失败，并在前后清理线程局部 SQLite 状态；多数 `_run_xxx()` 还会就地捕获超时或生产函数已经报告的局部失败，以便保留同轮已完成工作。任何单个任务失败都不会终止 APScheduler 调度线程。

### 4.2 连续失败计数器 → infra_event（`scheduler.py:57–79`）

```python
# scheduler.py:57
_consecutive_failures: dict[str, int] = {}
_FAILURE_ALERT_THRESHOLD = 3            # scheduler.py:60

def _record_job_failure(job_id, error):  # scheduler.py:67
    _JOB_RUN_STATE.failure_recorded = True
    _consecutive_failures[job_id] = _consecutive_failures.get(job_id, 0) + 1
    count = _consecutive_failures[job_id]
    if count >= _FAILURE_ALERT_THRESHOLD:
        emit_infra_event(severity="warning", source="scheduler",
                         code=f"job_consecutive_failure:{job_id}", ...)
```

当某 job 连续失败 ≥3 次，调用 `emit_infra_event`（`aletheia/infra/events.py`），事件去重窗口 300s。`_record_job_success()` 将计数器归零。每次 `_run_job_guarded()` 开始时会把本轮 `failure_recorded` 置为假；生产函数若捕获异常或看到错误返回值，必须调用 `_record_job_failure()` 将它置真。包装层只有在本轮没有失败记录时才清零计数，因此“异常已吞掉”“部分工作已完成”都不能再被外层伪装成成功。

System Operator 使用两个互不覆盖的健康身份：

- `pi_trading_decision` 表示真正的交易机会评估。daemon 的 300 秒超时或异常分别登记 `daemon_evaluation_timeout` / `daemon_evaluation_failed`，返回内部重试标记，并在 cooldown 后重新置位事件；只有一次真实评估完成才清零该计数。
- `pi_trading_heartbeat` 只表示 15 分钟 APScheduler 安全网是否成功把唤醒请求投递给 daemon。成功投递不能清零 `pi_trading_decision`；投递失败登记 `daemon_notify_failed`，并立即落到原有 inline 评估路径，避免 pending 队列因事件环不可达而继续断路。

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
- `data_collectors` 只承担主交易所实时 orderbook/Funding/OI；`data_collector_context` 有独立的 40 秒时间盒，
  负责较慢的跨源上下文。两个 job 共享单线程 `io` executor，因此主交易所 CCXT 仍串行；context job 内仅独立
  公共 HTTP 请求可并发等待。LSR 和稀疏 derivatives history 都不得占用实时 Funding/OI 的时间盒。
- `market_data` 与 `price_feed` 即使保留了同轮成功抓取，只要返回 `errors` 非空，就分别以
  `market_data_reported_errors` / `price_feed_reported_errors` 登记任务失败；空观察列表且 `errors=[]` 仍是合法成功。
- `exchange_sync.sync_all_exchanges()` 的公开契约规定 `None` 是所有账户视图均失败，scheduler 以
  `exchange_sync_total_failure` 登记失败；非空统一视图若带有账户级 `errors`，则保留已成功账户并以
  `exchange_sync_reported_errors` 登记部分失败。只有没有错误的统一视图（包括合法空结构）才可清零健康计数。
- execution `reconcile()` 与 position `run_reconcile()` 返回的 `errors` 非空时，分别登记
  `reconciliation_reported_errors` / `position_reconcile_reported_errors`。Position 任务同时把 agent run 写成
  `completed_with_errors`，已完成的 adoption/settlement 事实不回滚，但调度健康不得显示绿色。
- protective `position_manager.evaluate_open_positions()` 会逐仓继续处理并返回汇总；只要 `errors` 非空，scheduler
  以 `position_manager_reported_errors` 登记失败，同时保留同轮已完成的其它平仓。无持仓或 `errors=[]` 才是健康成功。
- `tpsl_fill_check` 对每个计划和账户隔离错误并继续核验其它计划，返回 `materialized`、`failed_count`、`errors`
  汇总。调用线程看到任一失败后以 `tpsl_private_reconciliation_failed` 登记红灯；下轮全部成功才可清零，已完成的
  私有成交材料化不会因另一计划失败而回滚。

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

`pause_scheduler(layered=True)` 只暂停非 essential job；`resume_scheduler()` 恢复这些选择性暂停项。`layered=False`
才暂停/恢复 APScheduler 整体派发循环。两种模式都不修改已注册的 job 表。

---

## §7 不变量

| ID | 不变量 | 来源 | Spec |
|---|---|---|---|
| INV-SCH-1 | 所有命名池 `max_workers` 均 ≤ 3；临时池也 ≤ 1 | `_EXECUTOR_WORKERS`（`scheduler.py:96`）；`_run_async_with_timeout`（`scheduler.py:253`） | AC-S01 |
| INV-SCH-2 | `init_scheduler()` 注册 ≥ 28 个 job | `scheduler.py:324–481`，当前 30 个（条件分支最少 29） | AC-S08 |
| INV-SCH-3 | 任何 job 函数抛出的异常不得终止调度器线程 | 各 `_run_xxx()` 顶层 `try/except Exception`；APScheduler 内部也兜底捕获 | — |
| INV-SCH-4 | job 执行前后，本线程泄漏的 SQLite 事务必须被回滚 | `_run_job_guarded()`（`scheduler.py:110`）；async/sync timeout wrapper `finally` 块 | — |
| INV-SCH-5 | 任务抛异常或显式报告失败时，连续失败 ≥ 3 次必须触发 infra_event；外层不得在同轮清零 | `_run_job_guarded()` + `_record_job_failure()` | — |
| INV-SCH-6 | layered pause 只停非 essential job；20 个 essential 入口及其委托 body 不得用 `runtime.paused` 自我禁用 | `_ESSENTIAL_JOBS` + `pause_scheduler()` | `test_layered_pause.py` |

---

## §8 边界：与 agent_contracts.md 的区别

`docs/design/contracts/agent_contracts.md` 管理**每个 agent 的 IO 契约**：输入 schema、输出 schema、权限模式、禁止意图、promotion gates。

本文档（`scheduler_contract.md`）管理**调度引擎本身**：执行器池拓扑、job 注册表（trigger/executor/timeout 三元组）、失败计数与 infra_event 触发、SQLite 并发清理包装、关停协议。

两份契约通过 `job_id` 字段对齐：agent_contracts 的 "§1 调度频率" 表中的 `job_id` 与本文档 §3 任务注册表的 `job_id` 一一对应。
