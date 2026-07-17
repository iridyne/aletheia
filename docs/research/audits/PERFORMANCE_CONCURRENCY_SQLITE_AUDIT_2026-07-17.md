# Performance、并发与 SQLite 静态审计

> 日期：2026-07-17
> 代码快照：`31535cfc2d07529f7675f20f34cfc6aee2cb8bc6`
> 状态：整改已实现，正在完成全量回归与发布证据
> 范围：LLM entry point、Provider 请求控制、Agent 并发、Scheduler、FastAPI
> 事件循环、SQLite 写入与索引、数据采集和 retention。
> 初始审计方法：当前源码和设计文档静态核对；当时没有执行负载测试、SQL
> `EXPLAIN QUERY PLAN`、生产流量采样或数据库写入。本文第 7、8 节持续记录后续实现和
> 代表性数据库副本验证，避免把初始静态结论与整改证据混为一谈。
> 边界：本文不构成 Live、Risk、Execution、Exchange、凭据或硬风险限制的变更授权。

## 1. 结论

当前架构适合小规模、本地、串行为主的 Agent 运行方式，但没有形成能够安全承载大量
并发 LM Agent 的统一控制面。扩展 Agent 数量时，最先出现的瓶颈预计不是单纯 CPU
不足，而是以下三条放大链：

1. 每个逻辑推理请求都会重新发现远程模型并创建新的 HTTP client；模型、网络和
   structured-output 多层重试叠加，故障时会放大远程请求量。
2. 定时 Agent 有局部串行保护，但手工 API、PI daemon、健康检查和其他入口没有统一
   Provider 级准入、配额、优先级和背压。
3. SQLite 本身采用了适合 local-first 的 WAL 配置，但业务写入同时存在逐行 autocommit
   事务风暴和长时间持有 `BEGIN IMMEDIATE` writer slot 两种相反问题；同步 SQLite 又被
   直接放进部分 `async def` 路由，使数据库等待扩大为整个 API 事件循环延迟。

因此不建议通过增加 Scheduler worker、提高 SQLite 并发或更换数据库来先行掩盖问题。
应先减少远程请求放大、建立统一推理准入，再压缩 SQLite 事务数量和 writer-lock
持有时间。

## 2. 当前已有的有效保护

以下设计应保留，后续整改不得破坏：

- SQLite 使用 thread-local connection、WAL、`synchronous=NORMAL`、30 秒
  `busy_timeout` 和有界自动 checkpoint；见
  [`storage/sqlite.py`](../../../aletheia/storage/sqlite.py)。
- Scheduler 将 IO、Observer 和 Agent 分到小线程池，Agent executor 为 1，任务默认
  `coalesce=True`、`max_instances=1`；见
  [`agent_runtime/scheduler.py`](../../../aletheia/agent_runtime/scheduler.py)。
- Market Observer 的 LLM 主循环是串行的，并有全局 wall-clock budget 和公平轮转。
- Universe OHLCV 外部请求使用并发上限 10；见
  [`universe/features.py`](../../../aletheia/universe/features.py)。
- CoinGecko 等少数 writer 已经使用单事务批量写入，证明仓库内已有可复用模式。
- Universe publication 的原子性保障了 snapshot、member 和 Opportunity 的状态一致性；
  后续只能缩短锁持有时间，不能直接拆掉原子边界。

## 3. 已确认问题登记

### PERF-01：每次推理前远程发现模型，并重复创建 HTTP client

- **等级**：高
- **静态确定性**：已确认
- **位置**：
  - [`model_provider/fallback.py`](../../../aletheia/model_provider/fallback.py)：
    `_candidate_models()` 每次执行 `provider.list_models()`；
    `complete_with_fallback()` 每次推理都进入该路径。
  - [`model_provider/openai_compatible.py`](../../../aletheia/model_provider/openai_compatible.py)
    和
    [`model_provider/anthropic_compatible.py`](../../../aletheia/model_provider/anthropic_compatible.py)：
    `/models` 与 completion 都使用新的 `async with httpx_client(...)`。
  - [`net.py`](../../../aletheia/net.py)：`httpx_client()` 每次构建新的 transport 和
    `AsyncClient`。
- **影响**：模型目录控制面请求和真实 inference 请求一一耦合；DNS、TLS、代理连接与
  connection pool 无法跨调用复用，增加延迟、网络唤醒、CPU 和功耗。
- **请求放大**：fallback 最多尝试 6 个模型；transport 默认每次最多 3 次网络尝试；
  structured caller 最多 3 轮。理论故障上限约为
  `(1 次模型发现 + 6 次推理) × 3 次网络尝试 × 3 轮 = 63 次网络尝试`。
  这是极端上限，实际会被 wall-clock budget 截断，不代表正常请求量。
- **修复方向**：模型目录 TTL cache + 配置变更失效；相同 Provider discovery
  single-flight；每个 Provider 复用进程级 `AsyncClient` 并在 shutdown 时关闭。

### PERF-02：模型健康检查以 5 秒周期触发真实远程请求

- **等级**：高
- **静态确定性**：已确认
- **位置**：
  - [`frontend/src/pages/Overview.tsx`](../../../frontend/src/pages/Overview.tsx) 和
    [`frontend/src/pages/Settings.tsx`](../../../frontend/src/pages/Settings.tsx)：
    `model-health` 的 `refetchInterval` 为 5000 ms。
  - [`server/routes/model.py`](../../../aletheia/server/routes/model.py)：每次请求直接调用
    `healthcheck_all()`。
  - [`model_provider/router.py`](../../../aletheia/model_provider/router.py)：并发 fan-out
    全部 enabled Provider，没有 TTL、single-flight 或 Semaphore。
  - [`model_provider/openai_compatible.py`](../../../aletheia/model_provider/openai_compatible.py)：
    成功路径重复请求 `/models`；不支持 `/models` 时退化成一次真实 “Say OK” inference。
- **影响**：页面打开时每分钟执行 12 轮 Provider fan-out。多 Provider 或不支持模型目录
  的兼容网关会产生持续的远程 API、token、网络和功耗开销。
- **契约偏差**：
  [`ASYNC_PERFORMANCE_LESSONS.md`](../../_ai/workflow/ASYNC_PERFORMANCE_LESSONS.md)
  已规定 LLM 结果类轮询最小 30 秒，当前实现为 5 秒。
- **修复方向**：后端缓存健康结果 30–60 秒并 single-flight；前端只在可见页面启用
  30–60 秒轮询；周期 health 不执行 inference，真实 ping 仅由用户显式触发。

### PERF-03：跨入口缺少统一 LLM 准入、背压和 Provider 配额

- **等级**：高
- **静态确定性**：架构缺口已确认；实际饱和点待压测
- **位置**：
  - [`agent_runtime/scheduler.py`](../../../aletheia/agent_runtime/scheduler.py)：只约束
    Scheduler 内部任务，Agent executor 为 1。
  - [`server/routes/data.py`](../../../aletheia/server/routes/data.py)：
    `POST /agent-runs/start` 在 HTTP request 内直接 `await run_agent()`。
  - [`agent_runtime/orchestrator.py`](../../../aletheia/agent_runtime/orchestrator.py)：直接运行
    Agent，没有全局 lease、queue 或 Provider quota。
  - [`model_provider/router.py`](../../../aletheia/model_provider/router.py)：Provider cache
    没有并发锁，也不存在统一 `Semaphore`、RPM/TPM 或 bounded queue。
- **影响**：手工 Agent、Scheduler Agent、PI daemon、健康探测和研究任务可以在 Provider
  层重叠；Scheduler 的 `max_instances=1` 不能保护其他入口。大量 Agent 到来时会出现
  上游 rate-limit、重试放大和本地 SQLite 写入突发。
- **修复方向**：建立统一 Inference Broker，至少包含 Provider 级并发上限、RPM/TPM、
  优先级、有界队列、超时取消、queue wait 指标和 overload fail-closed；手工入口改为
  `202 + job_id`，不在 HTTP request 内运行完整 LLM 工作流。

### PERF-04：Universe registry refresh 是 N+1 SQL 和 N 次 autocommit

- **等级**：高
- **静态确定性**：已确认
- **位置**：
  - [`universe/registry.py`](../../../aletheia/universe/registry.py)：遍历所有 market，逐个调用
    `repo.upsert_universe_asset()`。
  - [`storage/repositories.py`](../../../aletheia/storage/repositories.py)：每个资产执行一次
    `SELECT` 和一次 `UPDATE/INSERT`，未包在外层 transaction。
  - [`storage/sqlite.py`](../../../aletheia/storage/sqlite.py)：connection 使用
    `isolation_level=None`，每条 DML 在无显式 transaction 时独立提交。
- **影响**：一次 registry refresh 可形成数百至上千次 SELECT 和 WAL commit，是确定性的
  SQLite 写放大和磁盘唤醒热点。
- **修复方向**：使用 `INSERT ... ON CONFLICT(venue,symbol,market_type) DO UPDATE`，在一个
  短 transaction 中 `executemany`；stale/deactivation 使用 set-based 更新，只更新真正
  变化的字段。

### PERF-05：Universe publication 在 writer lock 内执行大规模循环和序列化

- **等级**：高
- **静态确定性**：已确认
- **位置**：
  - [`universe/service.py`](../../../aletheia/universe/service.py)：
    `_atomic_universe_publication()` 使用 `transaction(immediate=True)`；事务内逐成员构造、
    JSON 序列化、写 member、forward outcome、全部 scored candidate outcome、因果观测、
    Opportunity 和 snapshot 过期。
  - [`universe/outcome_recorder.py`](../../../aletheia/universe/outcome_recorder.py) 和
    [`analytics/filter_evidence.py`](../../../aletheia/analytics/filter_evidence.py)：每个候选
    构造四个 horizon outcome。
  - [`storage/repositories.py`](../../../aletheia/storage/repositories.py)：所谓 batch writer
    仍在 transaction 内逐行 `execute()`。
- **影响**：虽然只有一次 commit，但 SQLite 唯一 writer slot 的占用时间随 member 和
  candidate 数量线性增长，阻塞 collector、Agent telemetry 和 API write。
- **不变量**：不能为性能直接拆散 publication 原子性。
- **修复方向**：所有 Python 对象、ID、JSON 和参数矩阵在 transaction 外预计算；事务内
  使用 `executemany`/set-based SQL；只有在契约证明非权威 telemetry 可延后时，才允许
  将其移到 commit 后。

### PERF-06：异步 FastAPI handler 直接执行同步 SQLite 和文件读取

- **等级**：高
- **静态确定性**：已确认
- **位置**：[`server/routes/data.py`](../../../aletheia/server/routes/data.py) 中多个
  `async def` 直接调用 repository/get_db，包括 agent runs、steps、tool calls、proposals、
  portfolio history、account-equity history 和手工 Agent start；
  [`server/routes/model.py`](../../../aletheia/server/routes/model.py) 也在 async handler 中
  读取同步配置/secret 状态。
- **影响**：SQLite `busy_timeout=30000`。一旦出现 writer contention，同步 DB 调用可能在
  Uvicorn event-loop thread 上等待，拖慢同进程全部 HTTP 和异步任务。
- **修复方向**：纯同步 DB endpoint 改成 FastAPI 同步 `def`，或显式
  `await asyncio.to_thread(...)`；完整 Agent run 必须进入后台有界队列。

### PERF-07：Collector、feature 和 Agent telemetry 存在逐行 autocommit

- **等级**：中高
- **静态确定性**：已确认
- **位置**：
  - [`agent_runtime/scheduler.py`](../../../aletheia/agent_runtime/scheduler.py)：网络 gather
    完成后逐行写 orderbook、funding、OI 和 liquidation。
  - [`universe/features.py`](../../../aletheia/universe/features.py)：feature coroutine 内直接
    同步插入 snapshot。
  - [`agents/helpers.py`](../../../aletheia/agents/helpers.py)：每个 tool call 和 reasoning
    step 立即写一条 telemetry。
- **影响**：大量小事务与 Universe publication 竞争唯一 writer slot；Agent 数量增大时，
  telemetry 本身会成为负载放大器。
- **修复方向**：网络阶段只收集数据，单 tick/单 run 末尾批量写；Agent telemetry 使用
  有界 buffer，按条数或短时间窗口 flush，失败仍需诚实记录并有 drop counter。

### PERF-08：部分热点查询和 retention 条件缺少匹配索引

- **等级**：中
- **静态确定性**：索引缺口已确认；收益需 `EXPLAIN QUERY PLAN` 和代表性 DB 验证
- **位置**：[`storage/migrations.py`](../../../aletheia/storage/migrations.py) 与
  [`storage/repositories.py`](../../../aletheia/storage/repositories.py)。
- **已见缺口**：
  - `agent_run_steps(run_id, created_at)` 和 `agent_tool_calls(run_id, created_at)` 无索引；
  - `agent_runs.started_at` retention 无索引；
  - funding/OI 只有 `(symbol, observed_at)`，exact-exchange 查询还过滤 `exchange`；
  - filter retention 只按 `evaluated_at`/`entry_ts`，现有索引首列不匹配；
  - OHLCV retention 按 `(timeframe, ts)`，现有索引首列为
    `(exchange, symbol, market_type, timeframe, ts)`。
- **影响**：数据增长后，UI trace、near-time OI、batch funding 和 daily retention 可能执行
  大范围扫描。
- **修复方向**：先在数据库副本上保存 before/after query plan 和耗时，再增加最小复合
  索引；同时计算新增索引对高频写入的成本，不能只按读查询盲目加索引。

### PERF-09：Retention 和 OHLCV rolling prune 形成周期性 I/O 峰值

- **等级**：中
- **静态确定性**：SQL 形态已确认；峰值大小待测量
- **位置**：
  - [`agent_runtime/scheduler.py`](../../../aletheia/agent_runtime/scheduler.py)：daily cleanup
    连续调用数十个 prune。
  - [`storage/repositories.py`](../../../aletheia/storage/repositories.py)：通用 prune 使用单条
    无分块 `DELETE`；OHLCV 每次 upsert 后都执行 rolling-window DELETE。
- **影响**：数据库增长后会形成周期性 CPU、磁盘、WAL、checkpoint 和长 write transaction
  峰值。即使 OHLCV 尚未接近 50,000 行窗口，也会重复运行窗口保留查询。
- **修复方向**：按 rowid/主键分块删除，限制单批事务时长并记录进度；OHLCV 仅在超过
  `window + batch_margin` 时 prune，或移至低优先级周期任务。

### PERF-10：Scheduler timeout 会遗弃线程，不能真正终止任务

- **等级**：中高
- **静态确定性**：机制已确认；实际残留规模待监控
- **位置**：[`agent_runtime/scheduler.py`](../../../aletheia/agent_runtime/scheduler.py) 中
  `_run_sync_with_timeout()` 和 `_run_async_with_timeout()`。
- **影响**：每次任务创建新 executor、线程和 event loop；超时后
  `shutdown(wait=False)` 无法杀死仍在运行的 Python thread。Provider 拥塞、连接 teardown
  卡住或任务不响应取消时，线程和连接可能累积。
- **修复方向**：复用持久 async worker loop；确保 HTTP 和 Provider 响应 cancellation；对
  真正不可中断任务使用进程隔离；暴露 abandoned-thread gauge 和重启阈值。

## 4. 整改顺序

后续任务应保持以下顺序，避免先增加并发而放大现有写入和重试问题。

| 顺序 | 工作包 | 目标 | 主要验收证据 |
|---:|---|---|---|
| 1 | LLM discovery/client 去放大 | 缓存模型目录、复用 client、消除每次 inference 的控制面请求 | 一个正常逻辑 inference 只产生预期 completion 请求；连接可复用；focused tests |
| 2 | Model health 降噪 | 后端 TTL/single-flight，前端 30–60 秒且可见时轮询，周期 health 不做 inference | 1 分钟远程请求计数；API/UI regression；构建通过 |
| 3 | 统一 Inference Broker | Provider 并发、RPM/TPM、优先级、有界队列、overload fail-closed | 并发压测不超过配置上限；queue wait/drop/timeout 指标；手工入口返回 job ID |
| 4 | Registry/collector 批量事务 | 消除 N 次 commit 和逐行 collector transaction | 单 refresh/tick 的 transaction 数；SQLite statement/commit 计数；focused tests |
| 5 | 缩短 Universe writer lock | 保留 publication 原子性，将预计算移出锁并 bulk write | publication transaction 时长；并发 writer 等待；原子性回归 |
| 6 | 清理 async handler 同步 I/O | DB contention 不再阻塞 event loop | 后端 `:8788` 并发延迟；event-loop lag；route tests |
| 7 | 索引与 retention | query plan 对齐，清理任务分块和限时 | before/after `EXPLAIN`; cleanup 批时长、WAL 大小、删除行数 |
| 8 | Scheduler 取消语义 | 降低线程/连接残留 | timeout/cancellation tests；稳定线程数；abandoned gauge |

## 5. 统一测量指标

实现阶段不能只以“测试通过”声称性能修复完成。每个工作包至少记录：

- 每个逻辑 inference 的远程请求数、fallback 次数、Provider active concurrency；
- inference queue depth、queue wait P50/P95、overload rejection、timeout 和取消完成率；
- HTTP connection reuse/新建连接数；
- SQLite 每任务 statement 数、transaction 数、writer transaction P50/P95/max；
- `SQLITE_BUSY`/busy wait、WAL 大小、checkpoint 时长；
- Uvicorn event-loop lag 和热点 endpoint 在 `:8788` 的 P50/P95；
- cleanup 每批删除行数、事务时长和总 wall-clock；
- Scheduler 活跃线程数和 abandoned-thread count。

测量必须在代表性数据库副本或明确授权的本地运行环境完成；不得为了测性能解锁 Live、
发送真实订单或读取/记录凭据。

## 6. 实施边界与拆分原则

- PERF-01/02/03 会改变 LLM 控制流，必须有 fallback、超时、overload 和优先级回归测试。
- PERF-04/05/07/08/09 会改变持久化形态，必须证明事务原子性、幂等、行数和语义不变。
- 不通过提高 Scheduler `max_workers` 或并行 SQLite writer 作为第一阶段修复。
- 不把 authoritative trading/audit 数据改成可丢队列。只有明确属于 telemetry 的数据才可
  讨论 buffer，并且必须有 drop/error 计数。
- 不在性能任务中修改 Risk、Execution、Live、Exchange signer、credentials 或硬风险限制。
- 每个工作包使用独立分支/工作树和 PR；完成后在本文状态表记录 commit/PR、测量证据和
  仍开放风险。

## 7. 实施状态

| ID | 状态 | 实现/PR | 验证证据 | 备注 |
|---|---|---|---|---|
| PERF-01 | 已实现 | `iridite/perf-inference-storage-hardening` | `test_model_provider.py`、`test_inference_broker.py` | 15 分钟目录 cache、跨 loop single-flight、每 Provider/loop 长连接 client，配置变更和 shutdown 关闭 |
| PERF-02 | 已实现 | 同上 | model provider focused tests、frontend build | health 30 秒 TTL/single-flight；UI 30 秒；周期 health 不再发 inference，显式 Ping 保留 |
| PERF-03 | 已实现 | 同上 | Broker 并发/RPM/TPM/优先级/满队列/取消测试；Agent job queue tests | 所有 complete 和 stream 入口统一准入；手工 Agent 返回 `202 + job_id`，后台有界串行执行 |
| PERF-04 | 已实现 | 同上 | `test_performance_storage_batches.py` | Registry `executemany UPSERT` + set-based deactivation；核心/context collector 单 tick 单 commit |
| PERF-05 | 已实现 | 同上 | Universe service/spec focused tests | publication 原子边界保留；member/outcome/候选参数在事务外预构建，事务内 `executemany`；记录事务时长 |
| PERF-06 | 已实现 | 同上 | `test_route_threadpool_boundaries.py`、route tests | data/model/settings 读取和高频 health/status 阻塞 handler 改为同步 `def`；完整 Agent run 脱离 request |
| PERF-07 | 已实现 | 同上 | storage batch tests、write guardrails | Agent trace 每 16 条/结束时批量 flush，有界 256 条和 drop/error 计数；feature snapshot/context collector 批量事务 |
| PERF-08 | 已实现 | 同上 | 557 MB 代表性 DB 副本 before/after `EXPLAIN`；index focused test | 增加 8 个最小热点索引；详见第 8 节 |
| PERF-09 | 已实现 | 同上 | retention batch/metrics/OHLCV margin tests | 通用 retention 按 rowid 5,000 行分批；Agent run 按 PK 1,000；OHLCV 超过 window+1,000 才滚动清理 |
| PERF-10 | 已实现 | 同上 | `test_scheduler_timeouts.py` | 一个长期 async loop + 一个固定 daemon sync lane；两者都有界 16，重复 timeout 不再创建/遗弃线程 |

## 8. 代表性 SQLite 副本验证

验证时间为 2026-07-17。Gateway 当时正在运行，因此没有停止、重启或迁移全局服务；使用
SQLite 在线 backup 从 `~/.aletheia/app.db` 创建 `/tmp` 一致性副本，所有 DDL、`ANALYZE`
和查询计划只在副本执行。副本约 557 MB，主要代表性行数如下：

| 表 | 行数 |
|---|---:|
| `agent_run_steps` | 29,703 |
| `agent_tool_calls` | 99,841 |
| `funding_rate_snapshots` | 77,604 |
| `open_interest_snapshots` | 76,383 |
| `filter_decision_batches` | 3,891 |
| `filter_candidate_outcomes` | 127,104 |
| `market_ohlcv` | 427,042 |

`EXPLAIN QUERY PLAN` 对比：

| 查询形态 | 整改前 | 整改后 |
|---|---|---|
| Agent steps：`run_id` + `ORDER BY created_at` | 全表 `SCAN` + temporary B-tree sort | `idx_agent_run_steps_run_created` 精确查找，无临时排序 |
| Agent tool calls：`run_id` + `ORDER BY created_at` | 全表 `SCAN` + temporary B-tree sort | `idx_agent_tool_calls_run_created` 精确查找，无临时排序 |
| Funding latest：`symbol + exchange + observed_at DESC` | 只使用 `(symbol, observed_at)`，仍需过滤 exchange | `idx_funding_symbol_exchange_observed` 同时匹配 symbol/exchange/order |
| OI latest：`symbol + exchange + observed_at DESC` | 只使用 `(symbol, observed_at)`，仍需过滤 exchange | `idx_oi_symbol_exchange_observed` 同时匹配 symbol/exchange/order |
| Filter batch retention：`evaluated_at < cutoff` | 复合索引 skip-scan（首列不匹配） | `idx_filter_decision_batches_evaluated` covering range search |
| Candidate outcome retention：`entry_ts < cutoff` | 全表 `SCAN` | `idx_filter_candidate_outcomes_entry` covering range search |
| OHLCV retention：`timeframe + ts < cutoff` | 五列索引 skip-scan前三列 | `idx_market_ohlcv_timeframe_ts` covering range search |

副本创建 8 个索引和执行 `ANALYZE` 合计约 0.45 秒；最大单索引为 42.7 万行 OHLCV，创建
约 0.136 秒。该结果证明查询计划方向已对齐，但不等同于生产负载 P95；真实 writer wait、
WAL/checkpoint 和 inference queue P95 仍应在合并后的观察窗口持续采样。

## 9. 整改验证与已知基线

2026-07-17 在独立工作树 `iridite/perf-inference-storage-hardening` 完成以下验证：

- 在最新 `origin/master` 上的性能专项回归覆盖 Inference Broker、Agent job queue、Provider
  cache/client、Scheduler timeout、Registry/collector/Agent telemetry 批处理、Universe
  原子发布、同步数据库路由边界、热点索引和 retention 分块，并补跑同基线新增的
  VergeX/Universe 集成用例：`388 passed, 2 skipped`。
- 针对全量测试暴露的 Broker shutdown/restart 和 CoinGecko 事务契约补充回归后，相关聚焦
  组为 `98 passed`；另有 1 个与本分支无关的既有失败：Universe 当前配置默认开启，而旧
  测试仍断言默认关闭。
- 变更 Python 文件的 `ruff check` 通过，`git diff --check` 通过；Frontend production
  build 通过。

全量 `tests/` 当前不能作为可收敛证明：既有
`test_startup_behavior_gate.py::test_gate_passes_clears_blocker_and_returns_result` 在
`active_runtime_scope` 文档缺失或无法解析时进入持续复检，并重复执行 SQLite
`PRAGMA integrity_check`。本次只终止了由该验证启动的 pytest 进程，没有停止或重启全局
Gateway；该门禁/文档基线问题不在性能整改边界内，应单独修复后再补跑全量 suite。

本轮可以静态和专项证明请求准入、并发上限、事务数量、SQL 查询计划、线程数量上界及失败
语义；尚不能仅凭本地测试证明真实远程 Provider 与长期 SQLite 负载下的 P50/P95。合并后
观察窗口仍需采集第 5 节指标，并以 overload、queue wait、writer wait、WAL/checkpoint 和
retention wall-clock 判断是否需要调参，不应直接提高并发上限。

发布证据为 Draft PR [#123](https://github.com/iridite/aletheia/pull/123)，实现提交
`b4680998`，最终验证文档提交 `087e7683`（后续本文状态更新另有提交）。PR 首轮 GitHub
Actions 没有执行任何 step：所有 job 的统一 annotation 为账户近期付款失败或 Actions
spending limit 需要提高。这是 GitHub 账户级外部阻塞，不是测试失败；恢复 Billing/Actions
额度后必须 re-run checks，不能把当前红色状态视为代码回归，也不能在仓库内绕过门禁。
