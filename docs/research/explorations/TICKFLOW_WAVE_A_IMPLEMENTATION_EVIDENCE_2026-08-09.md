# TickFlow 建议波次 A 实施证据 — 2026-08-09

## 结论

建议波次 A 的 9 组小边界已经在 `codex/tickflow-next-catalog` 上实现。最终集成基线为 `7c0afc22`；本轮没有改变 Risk→Execution 顺序、Live 默认值、交易权限、SQLite 权威或引入新基础设施。

本文只证明源码边界与聚焦检查成立，不把静态检查、单元测试或前端 build 表述为生产 soak、Paper 收益或盈利证明。

## 实施清单

| 波次 A 项 | 对应 ID | 已实现边界 | 关键证据 | 保留不变 |
|---|---:|---|---|---|
| 剩余图表主题与容器生命周期 | 3、4 | Account Equity 原始、调整后和资金流色彩进入 `chartTheme`；容器改用 `ResizeObserver` 并在卸载时断开 | `frontend/src/charts/theme.ts`、`AccountEquityChart.tsx` | 图表数据查询、金融语义和 220px 布局不变 |
| Route render error boundary | 7 | lazy chunk 或页面 render 异常留在 shell 内，显示目标路径、Retry 和返回 Overview | `RouteErrorBoundary.tsx`、`App.tsx` | 现有 Vite preload reload、Suspense loading、404 和路由表不变 |
| Realtime parser 与单帧去重 | 13、14 | 无依赖 runtime parser 限制帧类型、大小和 category 数；重复 category/query prefix 每帧只 invalidation 一次 | `useRealtimeUpdates.ts` | REST polling 继续是退化权威；未增加 cursor 或跨帧队列 |
| OHLCV 通用质量与 outcome | 24、25 | 共享边界拒绝非 finite、错误 OHLC geometry、负 volume；空结果继续 fallback；兼容 list 的结果携带 attempts/accepted/rejected/complete-partial-empty；全失败保留原异常类型并附 `ohlcv_attempts` | `data/providers.py`、CCXT/Binance/CoinGecko/chain | exact venue/market identity、forming-bar 与持久化规则不变；partial 不自动换 venue |
| Cache bounds 与 telemetry | 28 | 进程级缓存改为 256 项 LRU；报告 size/max/hit/miss/expired/evicted，并进入 `/api/status` | `data/cache.py`、`routes/health.py` | TTL、cache key 和 SQLite 权威不变；未做 single-flight/coverage reuse |
| Serenity retry idempotency | 39 | 新重试使用稳定 parent/count 身份；数据库 v15 条件唯一索引覆盖新手动/自动 retry；竞态输家返回既有 run 且不重复入队 | `migrations.py`、`repositories.py`、Serenity route/worker | 旧 retry 记录不重写；取消与 worker 执行语义不变 |
| Scheduler runtime facts | 45 | 同时报告配置 enabled、真实 running/state/job_count 与最后一次 job executed/error/missed 时间 | `scheduler.py`、`routes/health.py` | 不把 collector tick 冒充 scheduler tick；调度周期和 pause 行为不变 |
| Startup failure-policy map | 48 | 现有 fail-startup、warn-continue、pause-autonomy 与 readiness effect 作为只读事实进入 `/api/status` | `server/startup_policy.py` | 不改变 lifespan 的异常处理与 gate 行为 |
| Final-artifact 属性断言第一片 | 51 | 打包前阻止 runtime-home/secret 路径与高置信 private-key 内容；最终 archive 解压后验证 manifest identity、文件集合和 hash，篡改 fail closed | `build_packaged_runtime.py`、`test_packaged_runtime_contract.py` | 不读取真实凭据；真实 PyInstaller boot、签名安装与跨平台 soak 留给后续 release wave |

## 迁移契约

- Schema 从 `aletheia-sqlite-v14` 前进到 `aletheia-sqlite-v15`。
- 新索引只约束带 `serenity-retry:` 身份的新 retry 行，因此不会删除、改号或重写历史重复记录。
- 手动 API 与自动 rate-limit retry 共用 `insert_serenity_research_retry()`，避免只修一个调用方。
- 重复请求返回同一个 `runId`、`deduplicated=true`，只由赢得插入的一方调用 enqueue。

## 聚焦验证

以下命令在 macOS 本地运行；未运行全量 pytest：

```text
cd frontend
npm test -- --run src/components/RouteErrorBoundary.test.tsx src/lib/useRealtimeUpdates.test.ts
# 2 files / 3 tests passed
npm run build
# TypeScript + Vite production build passed

uv run pytest tests/test_ohlcv_provider_boundary.py tests/test_ohlcv_cache.py tests/test_ccxt_provider_async_persistence.py -q
# 8 passed
uv run pytest tests/test_crypto_fallback.py -q -k 'get_ohlcv or FallbackChainProvider'
# 8 passed, 11 deselected
uv run pytest tests/test_serenity_workbench.py -q -k 'retry_failed_run or retry_completed_run_rejected'
# 3 passed, 59 deselected
uv run pytest tests/test_migration_fast_path.py -q -k 'startup_dataset_boundary_is_a_versioned_one_time_migration or production_ledger_advances_through_v15 or serenity_retry_idempotency_v15'
# 3 passed, 25 deselected
uv run pytest tests/test_scheduler_runtime_facts.py tests/test_startup_policy.py tests/test_status_routes.py -q -k 'scheduler_snapshot or startup_policy or status_reports_actual_scheduler_and_health'
# 3 passed, 6 deselected
uv run pytest tests/test_packaged_runtime_contract.py -q -k 'manifest_and_archive_are_deterministic or rejects_secret_paths_and_private_key_material'
# 2 passed, 10 deselected
uv run pytest tests/test_status_routes.py tests/test_packaged_runtime_contract.py tests/test_runtime.py tests/test_frontend_runtime_comm.py -q
# 38 passed

uv run ruff check <本轮修改的 Python 文件>
# passed
git diff --check
# passed
```

## 明确保留的后续天花板

- Realtime 没有 durable sequence/cursor/replay、per-client queue 或 symbol-scoped payload；这些仍属于实验波次。
- OHLCV partial 现在可见但不会自动换来源；consumer-specific fallback policy 仍需单独定契约。
- Cache 没有 single-flight、bytes budget 或 coverage-aware reuse；先用本轮 telemetry 证明需要。
- Scheduler `last_tick` 是本进程观察到的 job event，不是持久化 heartbeat；重启后为 `null` 是诚实状态。
- Startup policy 是现状目录，不是新的生命周期框架，也不把 interface readiness 扩大成 operational readiness。
- Final-artifact 第一片不等价于真实 Windows/macOS clean install、签名验证、启动 soak 与 rollback；这些仍属于 release assurance 后续波次。
