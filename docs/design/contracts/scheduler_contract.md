# Scheduler Contract

**Contract path**: `docs/design/contracts/scheduler_contract.md`
**Spec**: `tests/specs/spec_agent_contracts.py` (AC-S01), `tests/specs/spec_pi_decision_chain.py` (PI-S06, PI-S07), `tests/specs/spec_pipeline_tpsl_private_materialization.py` (PL-TPSL-05)
**Impl**: `core/aletheia/agent_runtime/scheduler.py`
**Related**: `docs/design/contracts/agent_contracts.md` (§8 区别)

---

## §1 入口与生命周期

调度器通过 `init_scheduler()` 构建并启动，无单独的 `build_scheduler()` 函数。

| 函数（`core/aletheia/agent_runtime/scheduler.py`） | 说明 |
|---|---|
| `init_scheduler()` | 构造 `BackgroundScheduler`、注册全部 job、调用 `_scheduler.start()` |
| `stop_scheduler()` | `_scheduler.shutdown(wait=False)` → 置 `_scheduler = None` → 关闭并有界排空两条 sync lane；返回 `bool`（两条 lane 是否已排空，见 §6） |
| `pause_scheduler()` | APScheduler `pause()`；job 不再派发，但不销毁注册表 |
| `resume_scheduler()` | 若 `_scheduler is None` 则按需重建；否则 `_scheduler.resume()` |

**调用路径**：`core/aletheia/server/app.py` 的 FastAPI lifespan 在 `run_migrations()` + `init_tool_registry()` 之后调用 `init_scheduler()`；shutdown 阶段调用 `stop_scheduler()`。

**APScheduler 版本**：`3.11.2`（`pyproject.toml` 约束 `apscheduler>=3.10`，`uv.lock` 锁定），使用 `BackgroundScheduler`（`apscheduler.schedulers.background`）。

**启动时暂停**：`init_scheduler()` 在 `_scheduler.start()` 后检查 `cfg.runtime.paused`。当 `layered_pause_enabled=true` 时，只暂停开仓、观察者和 LLM/决策任务；下列 `_ESSENTIAL_JOBS` 仍派发，以维持市场价格、账户视图、保护性退出、对账、健康检查和数据维护。只有 `layered_pause_enabled=false` 的 blanket pause 才暂停整个 APScheduler。集合内任务不得再在函数体中因 `runtime.paused` 提前返回，这一一致性由 `tests/test_layered_pause.py` 锁定。

**暂停所有权**：`runtime.paused` 是活动 lease 的兼容投影，不再是可由任一恢复循环独占清除的单一事实；`pause_blockers` 才表示当前 execution pool 的有效 blocker。人工操作、Exchange Connectivity、Startup Behavior Gate 与 LLM Startup Gate 各持有带 `reason`、`generation`、`acquired_at`、`source` 的 durable lease；global/demo/live trading brakes 也进入同一 owner map，并保留各自 `scope`，但 Risk 的 mode 语义仍独立决定适用池。一个恢复路径只能释放自身在异步检查开始前观察到的匹配 generation；新 generation、其他 owner 或 required dependency 未验证都会 fail closed。仅最后一个有效 owner 成功释放时才调用一次 `resume_scheduler()`；公开 `resume_scheduler()` 会在同一进程级 pause-transition `RLock` 内重新读取 durable blockers，并在任一适用 owner/brake 存在时拒绝修改 APScheduler，因此 Settings 的 `scheduler_enabled=true` 也不能旁路所有权。若调度器恢复调用失败，原 lease 会恢复到配置中并保持 fail-closed。旧配置中的 `paused_by_connectivity`/generation/timestamp 会迁移为 `exchange_connectivity` owner，旧 brake boolean 会迁移为 scope owner；无法归因的旧 `paused=true` 则诚实迁移为时间与 generation 均未知的 `manual` owner。`GET /api/status`、`GET /api/risk/status`、`aletheia control status --json` 与 Doctor 的 `config.paused` 项是该状态的只读观测面；它们同时暴露 active owner 的 `reason`、`generation`、`acquired_at`、`age_seconds`、`source`、`scope`、blocked reason，并明确 exchange recovery 不等于 scheduler resumed。

当前 essential 集合以 scheduler 的实际注册为准，覆盖行情、账户、持仓保护、对账、结算、健康、维护及 Lab 证据交付。旧 learning_contribution_evaluator、learning_measurement_startup_catchup、learning_lifecycle_audit_reconciler 已退役，不再属于在线集合。

---

## §2 执行器池

`scheduler.py` 的 `_EXECUTOR_WORKERS` 字典定义命名池，`init_scheduler()` 将其展开为 APScheduler executors：

```python
_EXECUTOR_WORKERS = {
    "default": 2,
    "io": 1,
    "maintenance_io": 1,
    "audit": 1,
    "fast_io": 1,
    "protective": 1,
    "observer": 1,
    "agents": 1,
}
```

| 池名 | max_workers | 用途 |
|---|---|---|
| `default` | 2 | 通用基础设施（portfolio_snapshot） |
| `io` | 1 | 短时 read/write 基础任务（market_data、reconciliation、cleanup 等）；单写防 WAL 碰撞 |
| `maintenance_io` | 1 | 长时有界维护（universe_refresh、outcome_settler、OHLCV backfill）；不占用短时 `io` 队列 |
| `audit` | 1 | hash-chain mirror 与 audit outbox（universe_scanner_audit_reconciler、funding_audit_reconciler、observation_lab_bridge）；与 operational `io` 队列完全隔离 |
| `fast_io` | 1 | 延迟敏感的基础设施（price_feed、connectivity_monitor） |
| `protective` | 1 | Position 保护检查（position_manager 是此池唯一 job）；不得排在长时数据/结算任务之后 |
| `observer` | 1 | 保留的隔离池；原 market_observer 已退役（#2933），当前没有 job 注册到此池 |
| `agents` | 1 | LLM/agent 密集型 job（pi_trading_decision、post_trade_reviewer 等）；串行防 SQLite 竞争 |

**总工人数**：2 + 1 × 7 = **9**。

注：各池 `max_workers` 全部 ≤ 3，满足 spec AC-S01（`tests/specs/spec_agent_contracts.py`）。timeout 层不计入上述 APScheduler 命名池：`_run_async_with_timeout` 复用一条进程级常驻 event-loop lane；普通同步 job 与 Universe recovery audit 分别复用独立的固定 1-worker daemon lane。三条 lane 的 outstanding 容量均固定为 16，不为每次调用创建新的 loop、executor 或替代线程（见 §4.3）。

---

## §3 任务注册表

全局 `job_defaults`（`init_scheduler()`）：`coalesce=True`，`max_instances=1`（universe_refresh 等在 job 级别显式重复声明 `max_instances=1`）。

下表由 `init_scheduler()` 的 `add_job` 调用整理（2026-10-09）；实际注册还受配置与 composition 准入约束（见注），不是所有任务无条件同时存在的保证。timeout 列来自 `_ASYNC_JOB_TIMEOUT_SEC`（`_run_async_with_timeout` / `_run_sync_with_timeout` 共用），`—` 表示该 job 不经该表计时。

| job_id | trigger | 参数 | executor | async timeout(s) | misfire_grace |
|---|---|---|---|---|---|
| `market_data` | interval | minutes=5（`MARKET_DATA_INTERVAL_MINUTES`） | io | 90 | 60 |
| `dreaming_daily` | cron | hour=2, minute=0, tz=Asia/Shanghai；至少一个 nightly Research callback 已组合 | agents | 300（`dreaming_agent`）| 300 |
| `serenity_startup_catchup` | date | — | agents | — | None（不限）|
| `lesson_reviewer` | cron | hour=3, minute=0, tz=Asia/Shanghai | agents | 300 | 300 |
| `insight_synthesizer` | cron | hour=2, minute=30, tz=Asia/Shanghai | agents | 120 | 600 |
| `learning_startup_catchup` | date | — | agents | — | None（不限）|
| `confidence_calibration` | interval | hours=6 | io | 30 | 300 |
| `pricing_reference_refresh` | interval | hours=6 | io | — | 300 |
| `reconciliation` | interval | minutes=2 | io | — | 60 |
| `portfolio_snapshot` | interval | minutes=10（`PORTFOLIO_SNAPSHOT_INTERVAL_MINUTES`） | default | 45 | 120 |
| `exchange_sync` | interval | minutes=5 | io | 60 | 60 |
| `position_path_observer` | interval | minutes=5 | io | 90 | 120 |
| `pi_trading_decision` | interval | minutes=15（固定安全网周期） | agents | 300 | 120 |
| `price_feed` | interval | seconds=30 | fast_io | 45 | 15 |
| `connectivity_monitor` | interval | seconds=cfg.health.exchange_probe_interval_sec | fast_io | 15 | 15 |
| `position_manager` | interval | seconds=30 | protective | — | 60 |
| `position_reconcile` | interval | minutes=5 | io | — | 60 |
| `tpsl_fill_check` | interval | seconds=90 | io | 45 | 30 |
| `cleanup` | cron | hour=3, minute=17, tz=Asia/Shanghai | io | — | 600 |
| `outcome_hook_replay` | interval | minutes=1 | io | — | 60 |
| `health_check` | interval | minutes=10 | io | — | 120 |
| `persona_rollout` | interval | hours=8 | io | — | 600 |
| `filter_controller_observe` | interval | hours=max(1, int(cfg.controller.observe_interval_hours)) | io | — | 600 |
| `post_trade_reviewer` | interval | hours=4；仅注册已组合的 producer | agents | 300 | 300 |
| `universe_refresh` | interval | minutes=10 | maintenance_io | 600 | 120 |
| `universe_scanner_audit_reconciler` | interval | minutes=5 | audit | 15 | 120 |
| `observation_lab_bridge` | interval | minutes=1 | audit | 30 | 120 |
| `outcome_settler` | interval | minutes=10 | maintenance_io | 300 | 120 |
| `funding_audit_reconciler` | interval | minutes=5 | audit | 15 | 120 |
| `broker_funding_settler` | interval | minutes=30 | io | 120 | 120 |
| `data_collectors` | interval | minutes=2 | io | 55 | 60 |
| `data_collector_context` | interval | minutes=10 | io | 60 | 60 |
| `guard_promotion` | cron | hour=4, minute=0, tz=Asia/Shanghai | io | — | 600 |
| `ohlcv_active_backfill` | interval | minutes=getattr(cfg.historical_data, 'active_universe_backfill_interval_minutes', 10) | maintenance_io | 300 | 120 |
| `demo_capabilities` | interval | hours=4 | io | — | 600 |
| `venue_margin_facts` | interval | minutes=5（`VENUE_MARGIN_REFRESH_INTERVAL_MINUTES`） | io | 60 | 120 |

已退役、不再注册的 job：`market_observer`（#2933）、`paper_trader`（#2945，2e69baae9）、`shadow_resolver`（shadow 写路径随 #3704 退役，384cd0cb2）。

Nightly Research availability 不改变上述 cadence：`dreaming_daily` 仅在 lesson
extraction 或 Serenity enqueue callback 至少一个已组合时注册；各自只执行可用子能力。
`lesson_reviewer` 保留 03:00 CST、agents pool、300s misfire，只在 registry 已组合时注册；
`serenity_startup_catchup` 保留启动后 4 分钟，仅在 enqueue callback 可用且 `serenity.enabled` 为真时注册。
5 分钟 Learning startup catch-up 在进入各 child guard 前跳过缺失 producer，不写
成功水位、失败 attempt 或为缺失 child 安排无限重试；独立 insight synthesis 不受影响。
已有 Serenity interrupted-run recovery 不依赖 producer 安装状态。

`universe_scanner_audit_reconciler` 是 essential、`max_instances=1`、`coalesce=True`
的非行为型修复任务。它只在 DB-authoritative startup recovery 已完成且 JSONL mirror
仍 pending 时重试稳定 audit identity；APScheduler 等待发生在专用 `audit` pool，
hash-chain lock/full-chain scan 则发生在专用、固定且队列有界的 sync timeout worker。
因此任一 audit filesystem 调用永久卡住时，不会占用 operational `io` pool 或共享
sync timeout worker，也不会重做 scanner DB recovery。

`funding_audit_reconciler` 同样是 essential、`max_instances=1`、`coalesce=True` 的
非行为型修复任务。它在 boot 后一分钟开始，每轮 oldest-first 处理最多 100 条 opt-in
funding audit，复用专用 `audit` pool 与固定、有界的 audit sync timeout worker。
它只追加/确认 stable audit identity，不重放 Position、PnL、paper cash、close projection
或 Outcome hook。backlog 会使对应 job health 与 `/api/health` degraded，并触发 Doctor
warning；`/api/ready` 和前后端 `/_infra/readyz` 的基础设施就绪语义保持不变。

`cleanup` 的 cron 状态是进程内状态；Gateway 离线时错过的触发不会由 APScheduler
自动补回。初始化 scheduler 时读取最近一次 `data_pruned` audit event，并按上海时区判断
最近一个应执行的 03:17 是否已有成功记录；若没有，在 readiness 之后 5 分钟运行同一个
guarded cleanup。每次成功清理同时将数据库容量
快照写到 app.db 之外的 `logs/database_capacity.jsonl`。这些动作不得进入 Gateway
pre-yield 冷启动路径。

cleanup 的 incremental reclaim 是有界 maintenance write：正常最多 16 MiB；物理体积
达到配置预算 80% 后最多 64 MiB。它必须持有 SQLite process/cross-process writer permit，
只能回收 freelist，不得改变 retention window 或删除额外业务行。

**注**：`post_trade_reviewer` 仅在 CLI/Web composition 已将对应
`extensions/research` producer 绑定到既有 agent registry、`get_agent_fn()` 可解析时注册。
Research 缺失时省略对应 job，不创建失败 AgentRun 或调度失败健康记录；Core 任务保持注册。
任务总数随配置、已组合 producer 和实际职责变化，不设源码调用数量下限。

`insight_synthesizer` 与 `confidence_calibration` 仅在 CLI/Web composition 已绑定
`extensions/analytics` 的对应 builder 时注册；已组合时保留原 cadence、executor、timeout
和 misfire 参数。缺失报告包时，启动 catch-up 将 memo 子项标为 `unavailable`，不执行、
不推进成功 watermark，也不为该缺失子项排重试。直接调用缺失的报告 job 仍明确记录
`analytics_reports_unavailable`，不得伪造成功。Core 结算任务和其他 catch-up 子项不受影响。

### 3.1 Observation Lab experiment scheduling

Observation Lab is the sole online Experiment authority. The existing Lab runner and
delivery bridge retain their pause, checkpoint and outbox boundaries.
`learning_contribution_evaluator`, `learning_measurement_startup_catchup`,
`learning_lifecycle_controller` and `learning_lifecycle_audit_reconciler` are retired:
they are not registered and their historical health rows do not imply active jobs.
An unavailable Lab never starts a legacy evaluator or lifecycle writer. Legacy
evidence migration remains an explicit offline, proof-gated operation.

### 3.2 Archived local settlement

The local funding settlement job is retired. Its former cadence and atomic
handoff are recorded in [archived account controls](../history/local_paper_account_controls.md).
Historical job-health rows do not register or restart that job.

### 3.3 Broker funding settlement

`broker_funding_settler` runs every 30 minutes on `io`, starts four minutes after boot, is
essential during layered pause, has `max_instances=1`, and is bounded by a 120-second async
timeout. It scans unresolved verified exchange-futures closes through a durable
`(closed_at,rowid)` keyset cursor. A bounded credential-free UTA retention classification budget
is separate from the bounded broker-I/O budget: only the matching profile's
`funding_history_retention_expired` result may atomically terminalize automatic recovery as
`broker_funding_history_expired`; all other incomplete/error states remain retryable. Cursor
advancement changes scheduling position only, so malformed/CAS-conflicting or transient prefixes
cannot starve later recoverable closes without fabricating economics or changing execution facts.

#### Owner-facing projection (target, no scheduling change)

Learning Contribution Observability adds no job, trigger, timeout or executor. Its target
`GET /api/learning/health` read model combines three facts without conflating them:

1. APScheduler registration and next-run state;
2. durable `learning_job_health` current/recovery history;
3. evaluator success watermark and durable `next_due_at`, with the current display window separate.

The projection must preserve `null` for unrecorded `last_expected_at` and output counts. It derives
catch-up due/current from the success watermark and persisted cadence deadline; it does not invent a
durable health row for the one-shot catch-up job. A recovered current `healthy` state does not erase
the durable most recent failure. Historical `learning_verdict_runner` rows remain explicitly
unregistered history.

The full proposed response and bounded/cache rules are in
[`learning_observability.md`](../proposals/learning_observability.md) §6.6-7 (`LCO-B04`,
`LCO-C03`).

**timeout 列说明**：`—` 表示该 job 没有在 `_ASYNC_JOB_TIMEOUT_SEC` 中声明超时，直接调用 `_guarded()` 包装，不经过 `_run_async_with_timeout` / `_run_sync_with_timeout` 的时间盒逻辑。

---

## §4 失败隔离

### 4.1 每 job 的 try/except 包装

每个注册任务都通过 `_guarded(job_id, fn)` 进入 `_run_job_guarded()`。该最外层包装会捕获未处理异常、登记失败，并在前后清理线程局部 SQLite 状态；多数 `_run_xxx()` 还会就地捕获超时或生产函数已经报告的局部失败，以便保留同轮已完成工作。任何单个任务失败都不会终止 APScheduler 调度线程。

### 4.2 连续失败计数器 → infra_event（`_record_job_failure`）

```python
_consecutive_failures: dict[str, int] = {}
_FAILURE_ALERT_THRESHOLD = 3

def _record_job_failure(job_id, error):
    _JOB_RUN_STATE.failure_recorded = True
    _consecutive_failures[job_id] = _consecutive_failures.get(job_id, 0) + 1
    count = _consecutive_failures[job_id]
    if count >= _FAILURE_ALERT_THRESHOLD:
        emit_infra_event(severity="warning", source="scheduler",
                         code=f"job_consecutive_failure:{job_id}", ...)
```

当某 job 连续失败 ≥3 次，调用 `emit_infra_event`（`core/aletheia/infra/events.py`），事件去重窗口 300s。`_record_job_success()` 将计数器归零。每次 `_run_job_guarded()` 开始时会把本轮 `failure_recorded` 置为假；生产函数若捕获异常或看到错误返回值，必须调用 `_record_job_failure()` 将它置真。包装层只有在本轮没有失败记录时才清零计数，因此“异常已吞掉”“部分工作已完成”都不能再被外层伪装成成功。

**INV-SCH-11 (durable stable escalation).** A job result key reporting an already-escalated, durably-recorded, human-owned condition MUST NOT increment `_consecutive_failures` and MUST NOT be treated as job-health degradation. A job MUST determine "already escalated" by reconstructing it from durable storage on every pass. The return value of a conditional-insert helper is dedup status, **not** delivery acknowledgement, and MUST NOT be the sole evidence. If the durable record can neither be written nor confirmed, the finding MUST be reported under `errors` (fail-closed). Suppressing the alert MUST NOT suppress the condition: the condition MUST remain observable through the durable audit record, an outstanding infra event reconstructed on every pass, and the job result payload. Non-empty `errors` MUST still fail the job regardless of `escalated`.

**Contract Delta classification.** This is **D1**: the meaning of `errors == []` changes from "the job observed no reportable condition" to "the job observed no *fresh unrecorded* reportable condition". It is not a strengthening of existing wording.

System Operator 使用两个互不覆盖的健康身份：

- `pi_trading_decision` 表示真正的交易机会评估。daemon 的 300 秒超时或异常分别登记 `daemon_evaluation_timeout` / `daemon_evaluation_failed`，返回内部重试标记，并在 cooldown 后重新置位事件；只有一次真实评估完成才清零该计数。
- `pi_trading_heartbeat` 只表示 15 分钟 APScheduler 安全网是否成功把唤醒请求投递给 daemon。成功投递不能清零 `pi_trading_decision`；投递失败登记 `daemon_notify_failed`，并立即落到原有 inline 评估路径，避免 pending 队列因事件环不可达而继续断路。

### 4.3 超时隔离（`_run_async_with_timeout` / `_run_sync_with_timeout`）

异步 job 统一提交到一个进程级常驻 event-loop worker；普通长时同步 job 与 Universe
recovery audit 分别提交到两条独立、固定 1-worker 的 daemon lane。三条 lane 的
outstanding 容量都固定为 16，不再为每次调用创建 executor
或 event loop。超时后：

1. 异步 job 由 `asyncio.wait_for` 取消实际 coroutine；如果 event loop 本身在额外 5 秒内仍
   无响应，则取消对应 cross-thread future，容量持续计数到 future 真正结束。
2. Python 无法安全中断正在执行的同步 thread，因此超时只取消尚未开始的 future；已经运行
   的非协作调用最多占住其所属固定 daemon lane。Universe recovery audit 有独立 lane，
   不会阻塞 ordinary sync-timeboxed operational jobs；每条 lane 的排队仍受 16 项上限约束。
3. caller 线程立即将相应 `agent_runs.status` 更新为 `'timed_out'`，避免 dashboard 出现幽灵
   `running` 行。
4. `scheduler_timeout_metrics_snapshot()` 暴露 submitted/completed/timed_out/cancelled/rejected、
   shared sync pending、Universe audit sync pending、worker 状态和兼容字段
   `abandoned_threads=0`。

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
- `data_collectors` 只承担主交易所实时 orderbook/Funding/OI；`data_collector_context` 有独立的 55 秒时间盒，
  负责较慢的跨源上下文。两个 job 共享单线程 `io` executor，因此主交易所 CCXT 仍串行；context job 内仅独立
  公共 HTTP 请求可并发等待。LSR 和稀疏 derivatives history 都不得占用实时 Funding/OI 的时间盒。
- `_bounded()` 辅助函数对所有 collector 强制执行 `min(cap, 时间盒剩余)` 时间上界。Python 3.12 起
  `asyncio.wait_for` 在超时后会等待内部 task 响应取消再抛出 TimeoutError，CCXT 的
  `load_markets`/`fetch_tickers` 对取消信号响应很慢（实测最多延迟 30+ 秒）。`_bounded()` 已改用
  `asyncio.shield(task)` + `task.cancel()` 不等待模式：超时后立即返回 `None`，后台 task 异步取消，
  lane 不再被挂起。各 collector 当前 cap：`microstructure` 18s、`derivatives` 25s（核心 lane，
  串行，deadline 50s）；`volume_scanner` 20s、`hyperliquid`/`defillama` 12s、`coingecko_top` 15s、
  `macro` 25s（context lane，wave 并发，deadline 55s）。
- `market_data` 与 `price_feed` 即使保留了同轮成功抓取，只要返回 `errors` 非空，就分别以
  `market_data_reported_errors` / `price_feed_reported_errors` 登记任务失败；空观察列表且 `errors=[]` 仍是合法成功。
  Binance 永续行情使用只访问 USD-M API 的 `binanceusdm` 物理客户端，但持久化 venue 仍为
  logical `binance`；相同 logical venue 不作为自己的 fallback 重复 bootstrap。行情在来源阶段结束时
  批量落库，因此总任务取消不会丢弃已成功抓取的快照。
  `/api/health` 按当前调度目标的 `venue + market_type` 集合取最差持久化新鲜度，并把滞后来源编码为
  `market_data_stale:<age>:source=<venue>/<market_type>`；任一无关 provider 成功或其它来源的新行都不能掩盖它。
- `exchange_sync.sync_all_exchanges()` 的公开契约规定 `None` 是所有账户视图均失败，scheduler 以
  `exchange_sync_total_failure` 登记失败；非空统一视图若带有账户级 `errors`，则保留已成功账户并以
  `exchange_sync_reported_errors` 登记部分失败。只有没有错误的统一视图（包括合法空结构）才可清零健康计数。
- execution `reconcile()` 与 position `run_reconcile()` 返回的 `errors` 非空时，分别登记
  `reconciliation_reported_errors` / `position_reconcile_reported_errors`。Position 任务同时把 agent run 写成
  `completed_with_errors`，已完成的 adoption/settlement 事实不回滚，但调度健康不得显示绿色。
- The `position_manager` job is considered failed when its result contains a non-empty `errors` list. `errors` carries only findings that are **fresh** on this pass: a finding whose durable escalation record this pass created, or whose durable record could not be confirmed. A finding whose escalation is confirmed durable in the audit table is returned under `escalated` instead. `escalated` never increments a failure counter and never resets one. Exclusivity is enforced per **finding** (`code` + `position_id`), not per position: one position may legitimately contribute one `escalated` finding and one `errors` finding in the same pass.
- `tpsl_fill_check` 对每个计划和账户隔离错误并继续核验其它计划，返回 `materialized`、`failed_count`、`errors`
  汇总。调用线程看到任一失败后以 `tpsl_private_reconciliation_failed` 登记红灯；下轮全部成功才可清零，已完成的
  私有成交材料化不会因另一计划失败而回滚。

---

## §5 SQLite 并发协议

四支柱均在本调度器中落实：

**支柱 1 — 进程级写锁 `_WRITE_LOCK`**（`core/aletheia/storage/sqlite.py`，`repositories.py:transaction()`）

`_WRITE_LOCK = threading.Lock()`（`sqlite.py`）是所有写事务的序列化点。`transaction()` 在每次
`BEGIN` 之前获取应用级 writer permit（6 秒超时），在 `COMMIT`/`ROLLBACK` 之后释放。所有写线程
在 Python 层排队，**`database is locked` 在正常运营下不可能出现**。应用级 6 秒超时专门用于
暴露"在事务内做了 I/O"这类 bug（会抛 `write_lock_timeout` `OperationalError`，而非无声重试）。

**支柱 2 — WAL + busy_timeout（最后防线）**（`core/aletheia/storage/sqlite.py::_open_connection`）

每个新连接在首次打开时执行：
```sql
PRAGMA journal_mode=WAL
PRAGMA synchronous=NORMAL
PRAGMA busy_timeout=30000   -- 30s，仅作为 _WRITE_LOCK 的最终兜底
PRAGMA wal_autocheckpoint=1000
```

**支柱 3 — 每线程独立连接**（`core/aletheia/storage/sqlite.py`）

`threading.local()` 保证每个 APScheduler worker thread 持有各自的 `sqlite3.Connection`，连接不跨线程共享。

**支柱 4 — `_run_job_guarded()` 清理包装**

每个 job 通过 `_guarded(job_id, fn)` 包装执行：

- **job 执行前**（在 `_SCHEDULER_JOB_LOCK` 保护下）：调用 `rollback_if_in_transaction()` 回滚上个 job 可能泄漏的事务。
- **job 执行后**（finally 块）：再次 `rollback_if_in_transaction()` + `close_db()`，释放线程局部连接，防止 WAL 写锁被空占。

注：`_SCHEDULER_JOB_LOCK`（`threading.RLock`）仅包裹 cleanup 调用（前/后各一次），**不**包裹 job 函数体本身，避免长时 job 持锁饿死其他线程。

对于带超时的 job，DB cleanup（`rollback_if_in_transaction` + `close_db`）在常驻异步 worker
协程的 `finally` 块中执行，或在固定同步 worker wrapper 的 `finally` 块中执行。异步取消会
执行 cleanup；同步非协作调用则在它最终返回时执行 cleanup，且不会再创建额外遗弃线程。

**不变量**：任何 `with transaction():` 块内部均不得出现 `await`、网络请求或 `time.sleep`——
否则 `_WRITE_LOCK` 将在 I/O 期间被持有，阻塞所有其他写线程。

---

## §6 关停协议

`stop_scheduler() -> bool`（#1456，owner 2026-10-09 决定：停止接新任务 → 取消未开始的 →
等正在跑的（有界时间）→ 再记干净退出）：

1. APScheduler `shutdown(wait=False)`，置 `_scheduler = None`，不再派发新 job。
2. 两条 sync lane（ordinary 与 Universe audit）各自 `stop()`：此后 `submit()` 抛
   `RuntimeError("... closed")`；队列中尚未开始的 future 全部 `cancel()`，永不执行。
3. 关闭两条 async worker（行为同前：取消并 gather pending task、关闭 async generator，default
   executor 最多 4 秒收尾，owner thread 5 秒 join 上限）。
4. 两条 sync lane 共享一个 `_SYNC_LANE_DRAIN_SEC = 5` 秒的截止时间，等待正在运行的那一个调用
   自然结束。5 秒取自现有上限：async worker `close()` 的 join 上限，以及 POSIX supervisor
   在整机 stop 时从 SIGTERM 到 SIGKILL 给 backend 的宽限。Python 无法强杀运行中的线程，超时
   后该线程只能自行返回或随进程退出。
5. 返回值：两条 lane 都已排空才为 `True`；否则记 warning 并返回 `False`。

lifespan 只在 `reached_yield and stop_scheduler() 返回 True` 时调用
`end_runtime_session(clean=True)`；否则记 `shutdown_clean=0`。

重新 `init_scheduler()` 前先 `_ensure_sync_lanes_ready()`：已关闭的 lane 只有在旧线程确认结束
后才替换为新 lane；旧线程仍在运行时 fail closed（抛 `RuntimeError`），同一 lane 永远不会同时
有两个 owner。

`pause_scheduler(layered=True)` 只暂停非 essential job；`resume_scheduler()` 恢复这些选择性暂停项。`layered=False`
才暂停/恢复 APScheduler 整体派发循环。两种模式都不修改已注册的 job 表。

---

## §7 不变量

| ID | 不变量 | 来源 | Spec |
|---|---|---|---|
| INV-SCH-1 | 所有 APScheduler 命名池 `max_workers` 均 ≤ 3；timeout 层固定为 1 条 async lane + 2 条相互隔离的 sync lane（ordinary + Universe audit），每条 outstanding ≤ 16 | `_EXECUTOR_WORKERS`、`_PersistentAsyncTimeoutWorker`、`_PersistentSyncTimeoutWorker` | AC-S01；`test_scheduler_timeouts.py` |
| INV-SCH-3 | 任何 job 函数抛出的异常不得终止调度器线程 | 各 `_run_xxx()` 顶层 `try/except Exception`；APScheduler 内部也兜底捕获 | — |
| INV-SCH-4 | job 执行前后，本线程泄漏的 SQLite 事务必须被回滚 | `_run_job_guarded()`；async/sync timeout wrapper `finally` 块 | — |
| INV-SCH-5 | 任务抛异常或显式报告失败时，连续失败 ≥ 3 次必须触发 infra_event；外层不得在同轮清零 | `_run_job_guarded()` + `_record_job_failure()` | — |
| INV-SCH-6 | layered pause 只停非 essential job；essential 入口及其委托 body 不得用 `runtime.paused` 自我禁用 | `_ESSENTIAL_JOBS` + `pause_scheduler()` | `test_layered_pause.py` |

---

## §8 边界：与 agent_contracts.md 的区别

`docs/design/contracts/agent_contracts.md` 管理**每个 agent 的 IO 契约**：输入 schema、输出 schema、权限模式、禁止意图、promotion gates。

本文档（`scheduler_contract.md`）管理**调度引擎本身**：执行器池拓扑、job 注册表（trigger/executor/timeout 三元组）、失败计数与 infra_event 触发、SQLite 并发清理包装、关停协议。

两份契约通过 `job_id` 字段对齐：agent_contracts 的 "§1 调度频率" 表中的 `job_id` 与本文档 §3 任务注册表的 `job_id` 一一对应。
