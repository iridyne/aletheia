# TickFlow Wave B-06：剩余五项整合证据

## 范围与不变量

本波次只实现五个有界切片：Provider 只读能力事实、既有 EventStore JSONL mirror 的 durable retry、OHLCV source/as-of/freshness、FastAPI lifespan task 事实，以及前端导航/dialog 收敛。SQLite 仍是事件权威，REST 仍是市场数据权威，Provider 健康不会授予权限，后台任务 registry 不负责启动任务，交易和 Risk/Execution Gateway 边界均未改变。

## 1. Provider capability/health descriptor（候选 21、22）

`aletheia/data/venue_health.py` 在既有 cooldown/last success 事实上生成只读快照，明确分开 `configured`、`entitled`、`healthy`、`permitted`，并附 `last_success_at`、`age_sec`、cooldown 和最后错误。`/api/status.market_data_providers` 只暴露这些观察事实；未改变 provider 选择、fallback 顺序、凭据或执行权限。

聚焦证据：`tests/test_market_data_venue_health.py::test_provider_capability_snapshot_keeps_policy_and_health_separate`。

## 2. EventStore JSONL durable mirror（候选 34、35）

选择了已有的 `ps_event_log -> events/event_log_YYYY-MM.jsonl` 双写，而没有建立通用 outbox 平台。Schema v22 给事件行增加 `pending/failed/mirrored`、attempt count、next attempt、last error class 和 mirrored time。DB commit 后 JSONL 写失败不会丢掉权威事件；同一 idempotency key 的后续调用或启动时 bounded reconcile 会重试。月度 JSONL 以 `event_id` 去重；历史行在迁移时标为已镜像，避免整库误重放。

重试采用 60 秒起、最多 1 小时的 bounded exponential backoff。错误只保留异常类别，不持久化可能含敏感值的全文。启动 reconcile 最多处理 100 条，失败不阻止 Gateway 进入可诊断状态。

聚焦证据：`tests/specs/spec_persistence.py::test_event_store_reconciles_failed_jsonl_mirror`，以及 v22 migration fast-path 两项验证。

## 3. OHLCV source/as-of/freshness（候选 16、17）

后端原有 `X-Aletheia-Data-Source` 和 `X-Aletheia-Data-Instrument` 不再在共享 fetch 边界被丢弃。新增的 `getOhlcvWithMeta` 只供 PriceChart 使用，并在运行时验证 candle 必须为有限数值。结果保留 source/instrument，以最后一根 candle 形成 `asOf` 和 age；当年龄超过两个 requested timeframe 时标为 stale。PriceChart 显示 source、年龄和 stale，但 historical scroll-back 与 Sparkline 仍沿用旧数组契约，避免扩大迁移面。

聚焦证据：`frontend/src/api/client.test.ts` 的 OHLCV metadata/decoder fixture；前端生产构建通过。

## 4. FastAPI lifespan task registry（候选 47、50）

`aletheia/server/app.py` 登记现有 direct lifespan task 与 module-owned recovery loop 的 owner、failure policy、ownership 和当前 state；`/api/status.lifecycle_tasks` 只读展示。Registry 不创建任务、不改变取消顺序、不添加 DI，也没有虚构 shutdown timeout：当前不存在统一 timeout 的任务明确返回 `stop_timeout_sec: null`。

聚焦证据：`tests/test_status_routes.py::test_lifecycle_task_registry_separates_direct_and_module_owned_work`。

## 5. 前端结构收敛（候选 1、5；候选 6 保留后续）

导航数据从 `App.tsx` 移入 `routeManifest.ts`，`PageHeader` 不再反向导入 App；lazy route components 仍由 App 持有，避免 manifest 静态拉入页面 bundle。ConfirmModal 与 DetailDrawer 复用 `useDialogFocus`，统一 Escape、Tab focus trap 和关闭后 opener focus restore，视觉壳与 alertdialog/dialog 语义保持不变。

本波次没有机械移动整个 StatusBar/AppShell。该工作主要减少文件长度，行为收益低且 diff 大；应作为后续纯结构 PR 单独完成，避免掩盖本波次的数据真实性和 durable-state 改动。

## 验证边界

- Provider snapshot：1 个 focused Python test。
- Event mirror：1 个 failure/reconcile/idempotency test；2 个 migration fast-path tests。
- Lifecycle registry：1 个 focused status test。
- OHLCV decoder：1 个 focused Vitest fixture；`npm run build`。
- Python touched files：Ruff check；全仓 `git diff --check` 在提交前执行。

这些结果证明本地代码契约与聚焦回归，不代表 Fedora Runtime 已部署，也不重开任何 Observation/qualification window。
