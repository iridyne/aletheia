# TickFlow 后续合并候选完整清单 — 2026-08-09

## 文档定位

本文是在 TickFlow Stock Panel 十二领域研究、Aletheia 第一优先阶段实施结果，以及合并后 `master` 的源码复核基础上，形成的后续候选目录。

- Aletheia 基线：`8fe56011`（PR #805 已合并）。
- TickFlow 完整研究快照：`920e1547`。
- 第一阶段已完成的 7 项不再计入下面 60 项。
- 这是一份研究与决策参考，不是任务状态表；Git、PR 和运行证据才是实施状态来源。
- “合并”表示把适配后的实践合入 Aletheia，不表示照搬 TickFlow 源码。

## 已完成基线

第一阶段已经完成：OHLCV fallback 缓存身份、PriceChart/PnLChart 生命周期、结构化 `ApiError`、实时传输退化状态、懒加载/404/旧路由重定向、可见 PageHeader role、DataTable 排序与过滤可访问性。详见：

- `TICKFLOW_FIRST_BATCH_SELECTION_2026-08-09.md`
- `TICKFLOW_FIRST_BATCH_IMPLEMENTATION_EVIDENCE_2026-08-09.md`

## 判断标尺

| 标记 | 含义 | 默认动作 |
|---|---|---|
| M1 | 可直接形成小批 PR | 边界清楚、低权限风险、可逆，补一个聚焦验证后可合并 |
| M2 | 适配后可合并 | 先明确 Aletheia 的类型、身份、完整性或生命周期契约 |
| E | 先做有界实验 | 必须先用真实 workload、fixture 或 telemetry 证明收益/正确性 |
| S | 安全专项 | 涉及凭据、认证、Live、审计或网络权限，禁止普通功能 PR 顺手修改 |
| D | 暂缓 | 当前没有足够 Alpha、性能或平台证据支持成本 |

成本：`S` 为小，`M` 为中，`L` 为大。优先级遵循：状态一致性 > 安全 > 可审计性 > 语义诚实 > 测试 > 能力 > 自治。

---

## 领域一：前端 UI、信息架构与数据组件（1–10）

| ID | 子功能 | 当前事实与缺口 | 建议最小改动 | 级别 | 成本 | 依赖与移植判断 |
|---:|---|---|---|---|---|---|
| 1 | 共享 Dialog 行为内核 | `ConfirmModal.tsx:20-65` 与 `DetailDrawer.tsx:13-43` 分别实现 focus/Escape/backdrop；Drawer 没有恢复 opener | 抽一个小型 focus/escape/restore 内核，让 Confirm 与 Drawer 复用，保留各自视觉壳 | M2 | M | 直接借鉴行为契约，不复制 TickFlow 组件；先定义嵌套弹层策略 |
| 2 | 语义状态组件族 | `DataTable.tsx:70-72` 只有单一空态；`Universe.tsx:155,294` 等页面自行区分 unavailable/partial | 建立 `empty / stale / partial / unavailable / not-configured / permission-limited` 共享呈现，状态必须来自后端事实或明确的客户端年龄策略 | M2 | M | 依赖各调用方能提供真实状态；不能只换文案 |
| 3 | DOM/Canvas 图表色彩权威 | `AccountEquityChart.tsx:70-73` 仍硬编码颜色，而 `charts/theme.ts` 已是主题入口 | 把剩余图表硬编码迁入 chart theme，增加语义名称而非页面色值 | M1 | S | 可直接合并；保留市场涨跌色与应用状态色两套语义 |
| 4 | AccountEquity 容器 resize | `AccountEquityChart.tsx:80-84` 仍监听 `window.resize` | 复用已在 Price/PnL/ProbabilityChart 使用的 `ResizeObserver` 与对称 cleanup | M1 | S | 可直接移植仓内既有模式 |
| 5 | 单一 route metadata manifest | `App.tsx` 同时维护 NAV、lazy routes、aliases；`PageHeader.tsx:1-4,21-23` 反向导入 App 的 NAV | 抽 data-only manifest：canonical path、alias、label、group、icon、role；页面仍用动态 import | M2 | M | 先保持懒加载，不让 manifest 静态导入页面；是 IA 单一来源，不承载业务权限 |
| 6 | AppShell/StatusBar 边界拆分 | `App.tsx` 约 700 行，混合侧栏、StatusBar 查询、权限按钮与路由 | 先提取 StatusBar 与 Shell/outlet，不重写状态管理 | M2 | M | 建议在 #5 后做；只降低耦合，不创建新框架 |
| 7 | 路由渲染错误恢复 | `App.tsx:106-114` 已有 404，`Suspense` 只有 loading；没有保留 shell 的 render/chunk error boundary | 在 route outlet 外增加 ErrorBoundary，显示目标、重试、返回入口；保留现有 `vite:preloadError` | M1 | S–M | 可直接合并；404 与运行时渲染失败必须保持两种语义 |
| 8 | DataTable 完整状态契约 | `DataTable.tsx:15-26,70-72` 只接收 `data[]`，空数组无法区分 loading/error/stale/partial | 增加显式 state/render props；TanStack sorting/filter 保持不变 | M2 | S–M | 依赖 #2 的状态词汇；不能自动从空数组推断失败 |
| 9 | 可见昂贵列驱动可选 I/O | `DataTable.tsx:91-123` 的 column toggle 只改 localStorage/可见性，不影响查询成本 | 只选一个真实昂贵分析列，声明 capability、batch cap 和 hidden 时不请求 | E | M | 必须先量出不可见列的实际 I/O；不做通用查询规划器 |
| 10 | 高基数阈值与虚拟化 | `DataTable.tsx:56-69` 总是构建普通 DOM row model；尚无页面级 cardinality 基线 | 记录 source/result/render 行数，在一个真实大表上测内存、滚动和可访问性后再设阈值 | E | M | 只有超过实测阈值才引入 virtualization；小表继续普通 DOM |

## 领域二：客户端状态、API 契约与实时同步（11–20）

| ID | 子功能 | 当前事实与缺口 | 建议最小改动 | 级别 | 成本 | 依赖与移植判断 |
|---:|---|---|---|---|---|---|
| 11 | Typed query-key factories | 前端约有数百个字面量 query key；如 `Blotter.tsx:113-127`、`PredictionLens.tsx:456-485` | 先为 positions/opportunities 一个域建立 key factory 与 prefix，不全量迁移 | M2 | M | React Query 保持权威；先证明能减少 identity drift |
| 12 | Query/change dependency graph | `useRealtimeUpdates.ts:18-111` 的 `CHANGE_MAP` 是 `Record<string, readonly unknown[][]>`，与 query key 没有类型耦合 | 由 #11 的 key family 派生 change map，并用聚焦测试检查漏边与未知事件 | M2 | S–M | 依赖 #11；不能把授权逻辑塞进图里 |
| 13 | Invalidation 合并与去重 | `useRealtimeUpdates.ts:188-197` 对每个 category/prefix 立即 invalidate，重复前缀会放大 refetch | 单帧内先去重 query prefix，再批量 invalidate；必要时用一个微任务窗口合并 | M1 | S | 可直接合并；保持事件到 REST 权威读取的模式 |
| 14 | WebSocket 帧运行时解析 | `useRealtimeUpdates.ts:176-196` 只做手写 shape check；帧没有 schema version/source/sequence | 先抽一个小型无依赖 parser，验证现有 `type + changed`；后端升级时再加 version | M1 | S | 不新增 schema 库；未知字段可兼容，未知类型必须诊断 |
| 15 | Poll fallback/freshness 策略注册 | hook 注释列出未覆盖类别；页面各自设置 interval，如 `PredictionLens.tsx:456-485` | 为一个域记录 push source、fallback cadence、stale threshold、hidden policy、停止条件 | M2 | M | 依赖 #11；连接状态不能代替数据 freshness |
| 16 | 关键成功响应 runtime decode | `client.ts:340-351` 对成功响应直接 `res.json()` 并依赖 TS 泛型 | 从 risk/status/portfolio/operator 中选择一个只读关键 endpoint，加入手写或既有库 decoder 与 wire fixture | E | M | 先做单一 contract slice；拒绝全 API 一次性生成/迁移 |
| 17 | Source/as-of/freshness 响应元数据 | 后端 `routes/data.py:1342-1458` 已返回 data source header，`fetchJson` 丢弃 headers | 只为市场读模型返回 `{data, meta}` 或专用方法，保留 source/as-of/observed/stale | M2 | M | 调用方迁移成本中等；不能把 header 当业务状态权威 |
| 18 | Committed change sequence/cursor/replay | `server/routes/ws.py:195-207` 与客户端只发送 change category，无 durable sequence/cursor | 用 correction、同 timestamp、多次断线 fixture 先证明 `MAX(timestamp)` 缺口，再设计 commit-after sequence 与 catch-up | E | L | 高价值但不是直接移植；序列只能标识已提交变化，不能成为业务权威 |
| 19 | Per-client bounded WS delivery | `server/routes/ws.py:109-115,146` 使用共享 `send_lock`，没有每客户端 bounded queue/drop/coalesce 指标 | 先测慢客户端是否阻塞；若存在，再加 per-client bounded channel 与 coalescing telemetry | E | M–L | 依赖 runtime metrics；不允许静默永久丢失有后果事件 |
| 20 | Symbol-scoped invalidation payload | `CHANGE_MAP` 的 market snapshot 会使全部 ticker/sparkline prefix 失效 | 后端 change payload 增加可选 symbol identity，客户端只刷新精确 family；未知 scope 仍退回 prefix | E | M | 依赖 #14 和后端事件契约；先量 refetch amplification |

## 领域三：Provider 能力、身份与数据质量（21–27）

| ID | 子功能 | 当前事实与缺口 | 建议最小改动 | 级别 | 成本 | 依赖与移植判断 |
|---:|---|---|---|---|---|---|
| 21 | Provider descriptor | `data/providers.py:48-68` Protocol 只有 get/close，没有 datasets、venue、resolution、limits 等描述 | 增加只读 descriptor dataclass 或可选 property；不改变选择与执行权限 | M2 | S–M | 可借鉴 TickFlow 字段思想，不能把 capability 变成 permission |
| 22 | Versioned capability snapshot | CCXT cooldown 与 chain availability 分散在 `ccxt_provider.py:216-229`、`sources/chain.py:140-159` | 持久或缓存 observation time、last success、probe error、age；区分 configured/entitled/healthy/permitted | M2 | M | 依赖 #21 的词汇；probe failure 不等于 entitlement denial |
| 23 | Requested/actual resolution envelope | `coingecko.py:117-140` 的不同 timeframe 可能共享聚合来源；Candle 未表达 actual resolution/quality | 在 provider result 中保留 requested 与 actual timeframe、aggregation/quality、observed_at | M2 | M | 下游必须能拒绝不兼容 resolution；不能只写日志 |
| 24 | OHLCV 通用质量校验 | `providers.py:20-39` 与 `ccxt_provider.py:235-247` 直接接受 float OHLCV，缺 finite/geometry/volume 约束 | provider boundary 检查 finite、high/low/open/close、volume，并记录 rejected count | M1 | S–M | 可直接小批合并；坏行不能静默转换成空成功 |
| 25 | Fallback outcome/partial 语义 | `sources/chain.py:140-159` 第一个返回 list 的 provider 即结束，空结果与完整结果没有 envelope | 空列表继续兼容 fallback；返回 attempts、accepted/rejected、complete/partial/empty/failed | M1 | S–M | 先兼容旧调用方；保持 exact venue/market identity |
| 26 | Provider health/limits 中央事实 | 请求预算、cooldown、last success 分散且偏进程内 | 先做只读聚合：configured、healthy、last_success、rate/page/history budget；不承诺跨进程全局限流 | E | M | 依赖 #21/#22；只有测出账户/IP 争用才做共享 quota owner |
| 27 | Consumer-specific fallback matrix | dashboard、研究、保护性定价对 stale/partial/fallback 的容忍度不同；当前 chain 主要按 provider 顺序 | 建立少量明确 consumer class，记录允许的 venue/resolution/age 与拒绝原因 | M2 | M | 风险/执行路径需单独安全审查；拒绝 universal silent fallback |

## 领域四：缓存、读模型与条件性分析面（28–33）

| ID | 子功能 | 当前事实与缺口 | 建议最小改动 | 级别 | 成本 | 依赖与移植判断 |
|---:|---|---|---|---|---|---|
| 28 | Cache bounds 与 telemetry | `data/cache.py:33-75` 只有 TTL dict，没有 max entries/bytes、hit/miss/eviction 指标 | 加 max entries、LRU/简单 eviction、hit/miss/expired/reason 计数并暴露诊断 | M1 | S–M | 可直接合并；先限制 entry 数，不先造通用缓存框架 |
| 29 | Identical-read single-flight | `cache.py:37-70` 的锁只保护 dict；`ccxt_provider.py:207-225` 并发 miss 会重复请求 | 针对一个 event-loop 所有者实现 per-key in-flight future，聚焦测试异常/取消/清理 | E | M | 要先解决多 event loop 生命周期；不能用一个错误全局 asyncio lock |
| 30 | Coverage-aware cache reuse | `cache.py:40-45` 将 limit 放进 key，500-bar 结果不能服务 100-bar 请求 | 测命中/内存后允许同 identity 的更大覆盖截尾复用，并保留 coverage metadata | E | M | 依赖 #28 telemetry；forming/closed 与 ordering 必须等价 |
| 31 | Named OHLCV read models/投影 | `repositories.py:5719-5749` 主要返回完整 dict 列表，重复研究查询会解码/分配全字段 | 为一个真实 workload 增加 latest cross-section 或 projected range query | E | M | 先记录 SQLite baseline；只实现赢得 benchmark 的命名读模型 |
| 32 | Sparse event/date 读取实验 | 当前 indexed SQLite exact identity 强，但没有为离散事件日期设计 partition-pruned projection | 用事件驱动回测 fixture 比较 exact SQLite query 与稀疏列式读取 | D/E | L | 没有明确 workload 前暂缓；不能技术先行建分区体系 |
| 33 | Parquet/DuckDB/Polars/matrix 分析面 | `storage/market_ohlcv_tiering.py:1-7,69-75,157-178` 刻意保持 format-neutral；尚无获胜 workload | 仅当命名 Alpha workload、SQLite 瓶颈、exact watermark、可重建、等价检查、资源上限全部成立时再选一种表示 | D | L | 明确不直接合并；SQLite 继续是交易/业务权威，禁止反向写入 |

## 领域五：I/O 一致性与派生发布（34–38）

| ID | 子功能 | 当前事实与缺口 | 建议最小改动 | 级别 | 成本 | 依赖与移植判断 |
|---:|---|---|---|---|---|---|
| 34 | Durable outbox 覆盖剩余 mirror | learning lifecycle 已有稳健 DB→JSONL reconcile（`learning/lifecycle.py:976-1025`），其他 artifact/mirror 不一定同等 | 先清点 authority/derived 文件写入，仅为“业务 commit 后必须完成”的 mirror 接入 durable job/outbox | M2 | M | 不做全局抽象；每个域保留稳定 dedup identity |
| 35 | Mirror retry/backoff 事实 | `learning/ledger.py:270-315` 主要记录 pending/failed/mirrored，没有 attempt_count/next_attempt_at | 增加 bounded exponential retry、attempt count、last error class、next due | M2 | S–M | 可在 lifecycle mirror 先做；错误文本需脱敏 |
| 36 | Reconcile claim/lease | `reconcile_lifecycle_audits(limit=100)` 没有 claim token，多 scheduler 时可能重复处理 | 先确认是否存在多 worker；若有，加 SQLite claim/lease 与到期恢复 | E | M | 单进程部署未证明并发时不必增加租约复杂度 |
| 37 | Dataset completeness envelope | 当前不同采集/派生路径对 requested/succeeded/failed/coverage 的表达不统一 | 为一个 derived dataset 定义 requested scope、accepted/rejected、coverage、publication id；完整性授权 replacement | M2 | M | 非空不等于完整；失败 scope 保留旧数据 |
| 38 | Generation pointer-last 发布 | `market_ohlcv_tiering.py` 有 identity/content hash/read-back，但未形成多分区 generation/pointer | 用小 fixture 验证 staging→schema/count/hash→manifest→atomic pointer；崩溃保持旧 generation | E | M–L | 仅用于可重建派生数据；不能把 per-file atomic 当 dataset atomic |

## 领域六：后台任务、进度、取消与调度（39–45）

| ID | 子功能 | 当前事实与缺口 | 建议最小改动 | 级别 | 成本 | 依赖与移植判断 |
|---:|---|---|---|---|---|---|
| 39 | Serenity retry idempotency | `routes/data.py:2183-2208` 创建 child run，但缺稳定 attempt idempotency key/唯一约束 | 用 `(parent_run_id, retry_count)` 或显式 attempt key 防双击/重放重复 | M1 | S | 可直接小批合并；同时暴露 attempt chain |
| 40 | Truthful cancellation state machine | `routes/data.py:2211-2225` 可立即把 Serenity run 标成 cancelled，而 worker 可能仍执行 | 改为 cancellation_requested→acknowledged/stopping→cancelled，terminal 只在 worker 确认后写入 | M2 | M | 状态不能释放物理容量；强杀需要独立 process owner |
| 41 | Durable two-scale progress | `_run_summary`（`routes/data.py:2025-2047`）主要是 timestamps/counts，没有 stage/overall/stage_pct/generation | 为一个长任务持久化 stage_code、overall_pct、stage_pct、event_seq、last_progress_at | M2 | M | 进度可压缩，terminal/publication identity 不可丢 |
| 42 | Learning attempt history | `learning/control.py:296-417` 与 `ledger.py:114-132` 有 job health，但缺 attempt UUID/history | 增加 append-only 或有界 attempt rows，连接 input watermark、duration、outcome | M2 | M | 不做 generic scheduler；先选一个 mutating learning job |
| 43 | Durable schedule window/catch-up | `scheduler.py:1080-1087` 承认 cron store 不记 missed fire，部分任务只做 startup catch-up | 为一个任务加入 canonical window key、scheduled/attempted/succeeded、catch-up idempotency | E | M–L | 先选会改变持久状态的任务；timezone/coalesce/misfire 必须显式 |
| 44 | Shutdown interruption reconciliation | `scheduler.py:1318-1325` 使用 `shutdown(wait=False)`，in-flight work 未必得到 terminal truth | 停止 admission、标记 queued/active、协作取消、等待有界时间、下次启动 reconcile unfinished | M2 | M | 不声称线程 timeout 等于终止；重任务可升级 process isolation |
| 45 | Scheduler enabled/running/last-tick 事实 | `scheduler.py:940-955` disabled 返回 None；UI/health 难区分 configured、enabled、actually running | 在 runtime read model 增加 enabled/running/last_tick/last_dispatch/owner session | M1 | S | 可直接合并；不把 scheduler 状态冒充任务成功 |

## 领域七：FastAPI 生命周期、Readiness 与资源可观测性（46–50）

| ID | 子功能 | 当前事实与缺口 | 建议最小改动 | 级别 | 成本 | 依赖与移植判断 |
|---:|---|---|---|---|---|---|
| 46 | Typed readiness vocabulary | `routes/health.py:152-192` 已分 livez/readyz，但 readyz 主要是接口与资产，不代表 data/operation/business readiness | 定义 alive/interface-ready/data-ready/operation-ready/healthy-degraded typed facts，并清楚命名 endpoint | M2 | M | 不改变现有 infra probe 含义；禁止 shallow 200 冒充业务就绪 |
| 47 | Lifecycle manifest/task registry | `server/app.py:339-417` 有 lifespan 阶段，但 module-owned loops/handles/dependencies/shutdown policy 不集中 | 先登记现有 task：owner、role gate、deps、start handle、failure policy、stop timeout | M2 | M–L | 不是 DI 重写；迁移、Risk、Execution 仍 fail closed |
| 48 | Startup failure-policy map | `app.py` 的 fail/warn/best-effort 决策散在 try/except 与注释 | 建立 data-only stage policy 表，并在 doctor/readiness 中显示 fail/warn/pause 后果 | M1 | S | 可直接先覆盖少量 stage；不能让表覆盖实际异常处理 |
| 49 | Per-dataset readiness catalog | 当前 deep health 与 repositories 有各自事实，但没有统一 ready/warming/partial/failed + coverage/generation | 先为 OHLCV/collector 一个 dataset 返回 state、coverage、age、generation、reason | M2 | M | 依赖 #37；optional analytics 不得阻塞 trading readiness |
| 50 | Phase/task/resource telemetry | `app.py:341-355` 有阶段日志，health 有 responsiveness；缺统一 duration/RSS/queue/cache/correlation 预算词汇 | 在少数昂贵边界记录 phase、duration、RSS delta、queue depth、cache result、error class | M2 | M | 先聚焦经济/重任务；避免 secret 与高基数标签 |

## 领域八：工程保证、制品与发布链（51–55）

| ID | 子功能 | 当前事实与缺口 | 建议最小改动 | 级别 | 成本 | 依赖与移植判断 |
|---:|---|---|---|---|---|---|
| 51 | Final-artifact 属性断言 | `package_integrity.py:141-215` 检查 manifest/hash/type，但缺目标平台关键 runtime import 与更强 secret exclusion | 对最终 digest 做 native host、dashboard、SQLite/crypto/numeric import、trusted key、secret/dev artifact exclusion | M1 | S–M | 可直接从一个平台制品开始；检查必须作用于最终产物而非 build dir |
| 52 | 统一 release assurance chain | 现有 build/sign/update/activation/rollback 脚本各自强，但没有一个 CI 链绑定“测试过的 digest=发布 digest” | 编排现有步骤：build→property→clean install→start/readiness→upgrade→rollback→publish digest | E | L | 不重写脚本；先证明一条平台链 |
| 53 | 单一生成式版本身份 | `build_packaged_runtime.py:99-114` 写 version/git_sha，Python/frontend/native/feed 仍可能漂移 | 从一个 release identity 生成各层版本并在 CI assert equality | M2 | M | 先不改变 SemVer 规则；禁止手写多权威 |
| 54 | 目标平台 install/upgrade/rollback smoke | `tests/test_packaged_update_activation.py:52-106` 强于参考系统，但仍不是每个平台的 clean install 与 oldest-supported-schema 证明 | 为一个目标平台做 isolated data root、clean install、旧 schema upgrade、health soak、failed activation rollback | E | L | 需要目标平台 runner；不在 macOS 本地循环替代 |
| 55 | SBOM/provenance 与平台签名 | update feed 已签名，但 macOS/Windows/Linux 的签名、notarization、SBOM/provenance 覆盖不统一 | 先产出 digest-bound SBOM/provenance；平台签名凭据与 notarization 另作 release security gate | D/E | L | 依赖外部凭据/runner；不把 unsigned build 宣称为 finished release |

## 领域九：凭据、Remote Control、网络与安全事务（56–60）

| ID | 子功能 | 当前事实与缺口 | 建议最小改动 | 级别 | 成本 | 依赖与移植判断 |
|---:|---|---|---|---|---|---|
| 56 | Credential classification registry | `config/secrets.py:27-58` 同时维护 keyring 与 encrypted file，失败可形成双副本/分歧；缺字段级 registry | 建立 credential name、storage class、response/export/redaction/rotation policy 注册表，再决定单一写入结果 | S | M | 不直接合并普通 PR；先做 threat model 与现有 key 清单，不读取值 |
| 57 | Encryption key separation/rotation | `config/secrets.py:86-92` 的 `.secret.key` 与 `secrets.enc` 同在 data root，目录泄露可同时获得密文与钥匙 | 评估 OS keyring/硬件/用户输入 bootstrap，设计 migration、rotation、recovery 和 fail-closed 行为 | S | L | 不能仅移动文件名；需跨平台恢复方案与用户授权 |
| 58 | Remote Control 认证硬化 | `client.ts:5-33` token 在 localStorage；`server/app.py:787-825` 可在私网 tokenless 模式运行 | 明确威胁模型；非 loopback 默认强制 token；评估内存/session/HttpOnly bootstrap、rotation/revoke | S | M | 会改变远程工作流，必须单独审批和迁移；不能顺手移除便利模式 |
| 59 | Outbound destination/proxy policy | `net.py:220-235,260-279` 在非 enforce 模式可能退回直连并继承环境代理；缺 destination allowlist | 按数据/通知/模型/交易目的地分类，明确 proxy required/optional/deny 与响应大小、重定向、DNS policy | S | M | 先观察真实目的地；执行/凭据流量的 fail-open 改动需安全评审 |
| 60 | Live/安全配置的审计与状态原子性 | `routes/live.py:56-107` audit 与 config save 分步；`settings.py:2149-2175` 的 remote token 配置审计结果未统一约束 | 设计 idempotency key 与“审计失败/配置失败”补偿规则，确保 UI 不把半成功显示成成功 | S | S–M | 直接影响 Live/安全事实；需 SAFETY_AND_TRUST 审查和故障注入 fixture |

---

## 推荐合并建议

### 建议波次 A：下一批可直接实施的小边界

这些项目价值明确、权限风险低、可逆，并且已有仓内模式可以复用。建议拆成独立 PR，不把所有项目捆成一个大 PR。

| 推荐顺序 | 建议 PR | 对应 ID | 原因 |
|---:|---|---|---|
| 1 | 剩余图表主题与容器生命周期 | 3、4 | 第一阶段已经验证相同模式，diff 小，能继续消除 chart drift |
| 2 | Route render error boundary | 7 | 404 已完成，补 render/chunk 失败恢复即可，不需要重构 Router |
| 3 | Realtime 帧 parser 与单帧 invalidation 去重 | 13、14 | 不改 REST 权威、不改后端业务语义，能降低 fan-out 并保护未知帧 |
| 4 | OHLCV provider-boundary 质量与 outcome | 24、25 | 直接提高数据语义，防止坏行/空结果被当作成功；需要聚焦 fixture |
| 5 | Cache 容量上限与基础 telemetry | 28 | TTL-only 是已证实的资源上限缺口；先做简单 max entries，不引入新缓存库 |
| 6 | Serenity retry idempotency | 39 | 小型持久化一致性修复，可防止双击/重放重复 run |
| 7 | Scheduler runtime facts | 45 | 只增加 enabled/running/last-tick 真相，不改变调度行为 |
| 8 | Startup failure-policy map | 48 | 先把现有策略可见化，减少 best-effort/fail-closed 漂移 |
| 9 | Final-artifact 属性断言第一片 | 51 | 直接验证最终产物，收益高；从一个目标平台和关键 runtime 开始 |

建议波次 A 的共同约束：每个非平凡边界只留一个最小、可运行、会在回归时失败的检查；不要扩大为通用 UI、provider、cache、scheduler 或 release framework。

### 建议波次 B：需要先定契约、再合并

| 推荐顺序 | 主题 | 对应 ID | 前置决策 |
|---:|---|---|---|
| 1 | Semantic state + DataTable state | 2、8 | 确认哪些状态由后端提供，哪些只允许由 request age 推导 |
| 2 | Route manifest 与 AppShell 解耦 | 5、6 | manifest 必须 data-only，禁止破坏 lazy import |
| 3 | Typed query/change graph pilot | 11、12、15 | 只选一个域；定义 key identity、fallback cadence、stale threshold |
| 4 | Provider descriptor/capability | 21、22、23 | 明确 configured/entitled/healthy/permitted 四轴及 observation age |
| 5 | Consumer-specific fallback | 27 | 先列 dashboard/research/protective consumers，保护性路径单独安全审批 |
| 6 | Completeness 与 dataset readiness | 37、46、49 | 统一 requested/coverage/publication 与 infra/data/operation readiness 词汇 |
| 7 | Background attempt truth | 40、41、42、44 | terminal 状态、物理 worker、取消确认和 restart reconciliation 必须一起定义 |
| 8 | Lifecycle/resource facts | 47、50 | 先登记现有 owner/handle/stop policy，再决定 telemetry 字段 |

### 建议波次 C：先做实验，不建议立即合并生产机制

| 实验 | 对应 ID | 必须回答的问题 |
|---|---|---|
| Realtime correction/catch-up | 18、19、20 | timestamp fingerprint 是否实际漏 correction；慢客户端是否阻塞；symbol scope 能减少多少 refetch |
| Cache/read-model performance | 29、30、31 | repeated miss、superset reuse、projected query 在命名 Alpha workload 上的冷/热延迟与 RSS |
| Publication consistency | 36、38 | 是否存在并发 reconcile；读者能否永远只看到旧/新完整 generation |
| Durable scheduling | 43 | 哪个持久化任务实际需要 missed-window catch-up，canonical window key 是什么 |
| Release chain | 52、54 | 最终 digest 能否完成 clean install、schema upgrade、readiness soak、rollback，并保持同一身份 |

### 暂缓项

- #32 稀疏分区读取、#33 Parquet/DuckDB/Polars/matrix 分析面：只有真实 Alpha workload 和 SQLite 瓶颈证据成立后再启动。
- #55 全平台 SBOM/provenance/signing：先在已有 release chain 中验证一个 digest-bound 产物，再扩平台。
- 动态插件市场、通用策略 sandbox、Postgres/Redis/vector 基础设施、全 API schema 生成、全前端视觉系统重写，仍不在本目录中作为近期合并项。

### 安全专项建议

#56–#60 不应与普通功能 PR 混合：

1. 先写 threat model、当前承诺、迁移/恢复方案和失败语义；
2. 不读取、导出或记录任何凭据值；
3. Remote Control 便利模式的收紧必须明确告知 operator 并提供迁移路径；
4. Live、认证、出站网络和 credential storage 的实现必须单独审查；
5. 验证重点是 fail-closed、故障注入、审计一致性和回滚，而不是只看 happy path。

## 我的最终推荐

下一步不要从最大、最“架构化”的项目开始。最优顺序是：

```text
剩余小型 truth gaps
  -> typed identity 与状态词汇
  -> provider/completeness 契约
  -> attempt/publication 生命周期
  -> named Alpha workload benchmark
  -> 只引入赢得 benchmark 的分析表示
  -> 最终制品与恢复链证明
```

如果现在只选一个“第二优先阶段”，建议采用波次 A 的 9 个小 PR；其中优先级最高的是 #24/#25（数据质量与 outcome）、#13/#14（实时 invalidation/parser）、#28（缓存上限）、#51（最终制品属性）。它们比直接引入 Parquet、DuckDB、Polars 或通用后台任务平台更便宜、更可逆，也更接近 Aletheia 当前的真实收益边界。

## 明确保留的不变量

- SQLite 继续是交易和业务事实的唯一权威；
- Risk 批准始终先于 Execution Gateway，Live 默认关闭；
- exact exchange/symbol/market/timeframe identity 不得弱化；
- capability 是描述事实，不是执行权限；
- partial/non-empty/connected/build 都不能冒充 complete/fresh/healthy/verified；
- 派生分析数据只能单向、可重建，不得成为第二写入源；
- credential 必须 write-only/status-only，诊断和制品不得泄漏；
- 大型测试、目标平台 packaging 和回滚证据应留给对应 CI/runner，不在 macOS 分析阶段反复运行。

## 实施状态补记（2026-08-10）

- #11、#12、#15 已以 `positions` 域完成首个 typed identity/change/freshness
  pilot；没有扩成全站 query registry。
- #42 已以 mutating `lesson_reviewer` 完成首个 durable attempt history，包含
  frozen input watermark、outcome、错误分类和同窗口 retry linkage；#43 的通用
  durable scheduling 仍保持实验项。
- 实现与聚焦证据见
  `TICKFLOW_WAVE_B_05_LEARNING_ATTEMPT_AND_POSITION_QUERY_IDENTITY_EVIDENCE_2026-08-10.md`。
