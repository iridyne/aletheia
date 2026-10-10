# Scheduler Contract — retired, target and duplicated sections

> **History (moved 2026-10-10).** Retired or superseded design kept as evidence; not current authority. See [design history](README.md).

Moved verbatim from [`scheduler_contract.md`](../contracts/scheduler_contract.md) when it was
rewritten into the numbered Contract form. Current behavior lives only in that Contract.
Sections below are either retired mechanisms, a target design that belongs to the
[`learning_observability`](../proposals/learning_observability.md) Proposal, meta-commentary, or
text duplicated from the storage owner ([data_model.md](../data/data_model.md) §1, §9).

## Retired job list and Observation Lab legacy jobs

已退役、不再注册的 job：`market_observer`（#2933）、`paper_trader`（#2945，2e69baae9）、`shadow_resolver`（shadow 写路径随 #3704 退役，384cd0cb2）。

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


## Owner-facing projection (target, not current behavior)

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

## INV-SCH-11 delta classification (meta-commentary)

**Contract Delta classification.** This is **D1**: the meaning of `errors == []` changes from "the job observed no reportable condition" to "the job observed no *fresh unrecorded* reportable condition". It is not a strengthening of existing wording.

## Former startup-pause essential-set sentence

当前 essential 集合以 scheduler 的实际注册为准，覆盖行情、账户、持仓保护、对账、结算、健康、维护及 Lab 证据交付。旧 learning_contribution_evaluator、learning_measurement_startup_catchup、learning_lifecycle_audit_reconciler 已退役，不再属于在线集合。

## Former market_data venue/xStock persistence sentence (Binance frozen, xStock out of scope)

- `market_data` 与 `price_feed` 即使保留了同轮成功抓取，只要返回 `errors` 非空，就分别以
  `market_data_reported_errors` / `price_feed_reported_errors` 登记任务失败；空观察列表且 `errors=[]` 仍是合法成功。
  Binance 永续行情使用只访问 USD-M API 的 `binanceusdm` 物理客户端，但持久化 venue 仍为
  logical `binance`；相同 logical venue 不作为自己的 fallback 重复 bootstrap。主行情和 xStock
  各自在来源阶段结束时批量落库，因此后续来源失败或总任务取消不会丢弃已成功抓取的快照。
  `/api/health` 按当前调度目标的 `venue + market_type` 集合取最差持久化新鲜度，并把滞后来源编码为
  `market_data_stale:<age>:source=<venue>/<market_type>`；任一无关 provider 成功或其它来源的新行都不能掩盖它。

## SQLite concurrency protocol copy (owner: data_model.md §1, §9)

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

## Former boundary section with agent_contracts.md

## §8 边界：与 agent_contracts.md 的区别

`docs/design/contracts/agent_contracts.md` 管理**每个 agent 的 IO 契约**：输入 schema、输出 schema、权限模式、禁止意图、promotion gates。

本文档（`scheduler_contract.md`）管理**调度引擎本身**：执行器池拓扑、job 注册表（trigger/executor/timeout 三元组）、失败计数与 infra_event 触发、SQLite 并发清理包装、关停协议。

两份契约通过 `job_id` 字段对齐：agent_contracts 的 "§1 调度频率" 表中的 `job_id` 与本文档 §3 任务注册表的 `job_id` 一一对应。
