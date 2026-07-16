# Aletheia 代码审计报告 — SDD 驱动的全面差距分析 (2026-06-23)

> 主控文档：本文是基于 16 份 L2 契约、114 个 spec claims、36 个 E2E 管道测试的完整代码审计产物。修复优先级见 §八（Part B）。
> Last updated: 2026-06-23

---

## 0. 执行摘要

本次审计覆盖全部 16 份 L2 契约（4 批次）+ 7 条管道全程追踪（2 批次）+ 36 个 E2E 管道测试（2 批次），共识别 **74 个 findings**（契约层 59 个 + 管道层 15 个额外 breaks）。严重度分布：🔴 critical **8 条**，🟠 moderate **30 条**，🟡 minor **36 条**。E2E 测试 36 个 claims，29 个通过（80.6%），1 个 xpassed（证伪了已记录 drift，实为已修），6 个 xfailed（其中 5 个严格捕获真实 drift）。最严重的单点：`insight_synthesizer._persist_memo` 将 memo 写入 `tags` 字段，但 `get_latest_memo()` 读 `content` 字段，导致整个统计反馈回路（memo → paper_trader_ranker → paper_trader 决策）**完全断开**，每轮 paper_trader 都在无回报基准情境下做决策。与第一性原理"失败可生还"的距离：信任硬墙（kill_switch / live_lock）结构完好，但学习装置的多个消费端已静默断路，系统在没有历史反馈的情况下持续运行，积累的风险无感知。

---

## 一、第一性原理总览

VISION 的中心赌注是"hard control → AI 敢边做边学 → 阿尔法涌现"。审计沿这条因果链寻找脆弱点，发现如下四个环节的具体裂缝。

**环节一：信任硬墙是否真正不可绕过？**  
kill_switch、live_lock 的结构性锁定是完好的：CR-RE-01 指出被拒绝提案的 `allowed_execution_mode` 被强制覆盖为 `"paper_internal"` 后写入 `RiskDecision`（`aletheia/risk/engine.py:252-256`），导致 `strategy_lifecycle._critical_risk_violations()` 的 live-mode 分支永久不可达——信任硬墙本身没有绕过，但记录机制错误，造成内部审计失真。CR-PG-01（`strategy_lifecycle.py:399-408`）直接依赖该字段，promotion gate 中的"live 提案被拒即计为 critical 违规"意图无法执行。CR-LU-01（`server/routes/live.py:17-45`）确认 live 解锁路由未检查 `operator.permission_mode`，任何本地 HTTP POST 可绕过操作员权限层（已在 live_unlock_contract BACKLOG 中记录，现代码层未关闭）。

**环节二：执行因果链是否真的不可破坏？**  
CR-EX-01（`aletheia/execution/reconciliation.py:21`）：对账器硬编码 `_LIVE_MODE = "bitget_live_limited"`，OKX/Binance live 订单永远无法对账，这些订单将永久停留在 `submitted` 状态，风险引擎的 check #12 因此低估实际 live 敞口。CR-EX-03（`bitget_broker.py:141-149`）：Position 行创建时未传入 `opportunity_id`，相关 check #16（sector 集中度守卫）对全部实盘/demo 仓位永久静默。管道追踪还发现执行事件（`paper_order_executed`、`proposal_rejected` 等）通过 `write_audit()` 写 JSONL 但不调用 `insert_audit_event()`，API `/api/audit` 的 SQLite `audit_events` 表对执行历史是空白的（EC-1，`gateway.py`）。

**环节三：学习装置是否真的在闭环？**  
`insight_synthesizer._persist_memo`（`insight_synthesizer.py:464`）使用 `tags=json.dumps(memo)`，而 `get_latest_memo()`（`insight_synthesizer.py:96`）读 `content=` 字段，字段错位导致 `memo_scoring` 永远返回 0.0，`_should_skip_synthesis()` 永远返回 False，synthesizer 每轮都重跑（KL-2）。`trading_context.py:367`（冻结区）过滤 `item_type in ('lesson','accepted_lesson','trading_guard')`，但前两种类型无任何 producer 在写，Layer 3 wisdom 对 PI Agent 永远是空列表（KL-3）。dreaming_agent 的 `_extract_lessons` 写入 `ps_lessons` 表，而 `retrieve_lessons(FTS5)` 只读 `knowledge_fts`，两者之间无桥接（Pipeline_GAPS §A1，已知）。这三个断路叠加，经验积累的最终消费端基本失效。

**环节四：失败是否真的可生还？**  
CR-DL-01（`aletheia/storage/repositories.py:1049`）：OHLCV 窗口默认 50,000 行而非契约声称的 500 行，100× 差距意味着在 30 个交易对×3 时间框架下，单 OHLCV 表可能达到 450 MB，WAL checkpoint 延迟和 `busy_timeout` 命中频率会随规模增加。CR-AU-01 揭示 `audit_chain_contract.md` 对应的 `spec_audit_chain.py` 完全不存在，防篡改链的全部不变量（append-only、`_prev_hash` SHA-256、跨文件旋转链接、双锁写入）完全暴露在重构风险下，若 `jsonl.py` 被意外修改，CI 无法捕获任何回归。CR-PI-01（`operator_loop.py:882`）：PI 操作员的首要调校杠杆（scanner family 权重）从不写入 config——每次调整在下次循环后自动消失，系统自治调校能力实际为零。

---

## 二、契约对账总览

| Contract | Findings | 🔴 | 🟠 | 🟡 |
|---|---|---|---|---|
| risk_engine_contract | 4 | 1 | 1 | 2 |
| execution_contract | 4 | 1 | 2 | 1 |
| live_unlock_contract | 3 | 0 | 1 | 2 |
| promotion_gates_contract | 5 | 1 | 2 | 2 |
| data_model | 4 | 1 | 1 | 2 |
| agent_contracts | 5 | 0 | 3 | 2 |
| api_reference | 5 | 1 | 3 | 1 |
| data_layer_contract | 4 | 1 | 2 | 1 |
| audit_chain_contract | 5 | 1 | 2 | 2 |
| operator_layer | 3 | 0 | 1 | 2 |
| PI_OPERATOR_DESIGN_DECISIONS | 4 | 1 | 1 | 2 |
| EQUITY_INTEGRATION_DESIGN | 3 | 0 | 2 | 1 |
| PREDICTION_MARKET_DESIGN | 2 | 0 | 0 | 2 |
| knowledge_contract | 5 | 0 | 2 | 3 |
| scheduler_contract | 4 | 0 | 1 | 3 |
| ui_design_system | 4 | 0 | 1 | 3 |
| **总计** | **64** | **8** | **25** | **31** |

> 注：契约层 64 个 findings。含管道追踪额外发现（KL-2、PM-1、OL-2 等）后总计 74 个。Git pattern mining 归纳 6 类重复模式（unit_precision、data_contract_violation、silent_drop、async_event_loop、sync_drift、timezone_or_unicode），记入 §五（Part B）。

---

## 三、🔴 Critical Findings 全列表

### CR-RE-01 — RiskDecision 丢失提案原始执行模式
- **契约锚点**: `docs/design/contracts/risk_engine_contract.md §3` — RiskDecision 持久化提案的 execution_mode
- **代码锚点**: `aletheia/risk/engine.py:252-256`
- **现象**: 被拒绝提案的 `allowed_execution_mode` 被强制写为 `"paper_internal"`，原始提案模式丢失。
- **第一性原理为什么严重**: `strategy_lifecycle._critical_risk_violations()` 依赖此字段判断 live 拒绝，字段错误导致该分支永久不可达，promotion 门控完整性受损（trust invariant 失效）。
- **最小修复**: `aletheia/risk/engine.py:271-278` — 在 `RiskDecision` 增加 `proposal_execution_mode` 字段持久化提案原始模式，`allowed_execution_mode` 保持被降级后的值。
- **E2E test**: 无对应 xfail 测试，CR-PG-01 共享根因。

---

### CR-EX-01 — Reconciliation 只覆盖 bitget_live_limited
- **契约锚点**: `docs/design/contracts/execution_contract.md §6` — reconciliation handles all live modes
- **代码锚点**: `aletheia/execution/reconciliation.py:21` — `_LIVE_MODE = "bitget_live_limited"`
- **现象**: OKX/Binance live 订单永远不被对账，状态永久停留 `submitted`。
- **第一性原理为什么严重**: 风险引擎 check #12 查询 positions 表计算 live 敞口，未对账订单不可见，live 敞口被系统性低估（state corruption / failure not survivable）。
- **最小修复**: `aletheia/execution/reconciliation.py:21` — 改为 `_LIVE_MODES = {"bitget_live_limited", "okx_live_limited", "binance_live_limited"}` 并以 `IN` 过滤。
- **E2E test**: 无 xfail 测试覆盖此场景。

---

### CR-PG-01 — `_critical_risk_violations` dead branch 永久不可达
- **契约锚点**: `docs/design/contracts/promotion_gates_contract.md §2 gate: Critical Risk Violations`
- **代码锚点**: `aletheia/trading/strategy_lifecycle.py:395-408`
- **现象**: 检查 `decision.get("allowed_execution_mode").startswith("live")` 的分支（line 400）因 CR-RE-01 根因永远返回 False，"任何 live 提案被拒即为 critical violation" 的意图未被执行。
- **第一性原理为什么严重**: promotion gate 的 critical violations 计数结构性错误，策略生命周期闸门完整性不可信（trust invariant 失效）。
- **最小修复**: 同 CR-RE-01（增加 `proposal_execution_mode` 字段）；在修复前于 `strategy_lifecycle.py:399-402` 添加注释说明分支不可达原因。
- **E2E test**: 无 xfail 测试；建议添加 spec claim `PG-B05`。

---

### CR-DM-01 — ID 格式：契约声明 UUID4 36 字符，代码生成 12 字符 hex
- **契约锚点**: `docs/design/data/data_model.md §5` — "ID generation: `str(uuid4())` at insert time (Python-side)"
- **代码锚点**: `aletheia/storage/models.py:9` — `return uuid4().hex[:12]`
- **现象**: 实际 ID 为 12 字符 hex（48 bit 熵），而非契约声明的 36 字符 UUID4（122 bit 熵）。
- **第一性原理为什么严重**: 12 char hex 在 ~1M 行时碰撞概率为 birthday bound 的 2×；FK 链碰撞会在不报错的情况下将审计记录指向错误祖先（state corruption / audit chain integrity）。
- **最小修复**: 方案 A：`aletheia/storage/models.py:9` 改为 `return str(uuid4())`（适用 trust-bearing 表）；方案 B：更新契约承认 12 char hex 并说明接受的碰撞风险（MVP 可行）。
- **E2E test**: 无 xfail 测试；spec_data_model.py 无 ID 格式 claim。

---

### CR-AR-01 — Runtime API 路径前缀：契约 `/api/runtime`，实际 `/api/operator/runtime`
- **契约锚点**: `docs/design/api/api_reference.md §Runtime` — 所有 runtime 端点在 `/api/runtime/...`
- **代码锚点**: `aletheia/server/routes/runtime.py:15` — `router = APIRouter(prefix="/api/operator/runtime", ...)`
- **现象**: 全部 8 个 runtime 端点（candidate/shadow/promote/drain/rollback/events 等）的真实路径与契约相差一级前缀。
- **第一性原理为什么严重**: rollback 和 drain 是安全关键的恢复操作；前端或外部工具按契约路径调用将得到 404，恢复能力形同虚设（failure survivable 原则受损）。
- **最小修复**: `docs/design/api/api_reference.md §Runtime` — 将所有路径从 `/runtime/...` 改为 `/operator/runtime/...`（文档修正，代码现状更符合语义）。
- **E2E test**: 无 xfail 测试；缺少 spec claim 验证路由前缀。

---

### CR-DL-01 — OHLCV 窗口：契约 500 行，代码默认 50,000 行（100× 差距）
- **契约锚点**: `docs/design/data/data_layer.md §9 Decision 3` — 每 (symbol, timeframe) 保留最近 500 bars
- **代码锚点**: `aletheia/storage/repositories.py:1049` — `_OHLCV_WINDOW_DEFAULT = 50_000`
- **现象**: OHLCV 窗口比契约大 100 倍；30 个交易对×3 时间框架预计 ~450 MB OHLCV 数据，契约预估约 4.5 MB。
- **第一性原理为什么严重**: SQLite WAL checkpoint 在大表下延迟增加，`busy_timeout` 命中率上升，威胁三柱并发安全（"failure survivable"——本地优先轻量级前提被破坏）。
- **最小修复**: 决策分叉：(a) `repositories.py:1049` 改 `_OHLCV_WINDOW_DEFAULT = 500` 并验证 `historical_data.max_bars_per_pair` 默认值一致；或 (b) 更新契约承认 50,000 并说明存储预算。两者选其一，不能并存。
- **E2E test**: 无 xfail 测试；spec_data_model.py 无 OHLCV 窗口 claim。

---

### CR-AU-01 — audit_chain_contract 无对应 spec 文件
- **契约锚点**: `docs/design/contracts/audit_chain_contract.md`（完整文档，L2 trust-boundary 级别）
- **代码锚点**: `aletheia/files/jsonl.py`（整个模块）
- **现象**: `tests/specs/` 目录下不存在 `spec_audit_chain.py`，防篡改链的全部不变量（append-only、`_prev_hash`、跨旋转链接、双锁写入）完全无 spec 保护。
- **第一性原理为什么严重**: 审计链是系统的防篡改证据机制，是"failure survivable"的最终兜底。任何对 `jsonl.py` 的重构若破坏哈希链语义，`pytest tests/specs/` 不会报任何错误（trust invariant 失去 spec 盾牌）。
- **最小修复**: 新建 `tests/specs/spec_audit_chain.py`，最少 5 个 claims：AUDIT-01 append-only、AUDIT-02 `_prev_hash` SHA-256、AUDIT-03 `verify_audit_chain()` 新链返回 valid、AUDIT-04 append-jsonl("audit.jsonl") 被阻止调用、AUDIT-05 dual-lock 写入序列化。
- **E2E test**: 无 xfail 测试（spec 文件本身不存在）。

---

### CR-PI-01 — Scanner family 权重调整不持久化
- **契约锚点**: `docs/design/operator/PI_OPERATOR_DESIGN_DECISIONS.md §3` — Level 1 操作："adjust scanner family multiplier: ±0.2 per cycle; writes config or memory override"
- **代码锚点**: `aletheia/agent_shell/operator_loop.py:882` — `_action_adjust_scanner_weight()`
- **现象**: `_action_adjust_scanner_weight` 只记录 audit log，从不调用 `save_config()` 或任何 multiplier 写入函数，调整在下轮循环后消失。
- **第一性原理为什么严重**: Scanner family multiplier 是 PI 操作员唯一的信号质量调校杠杆。若调整静默消失，系统自治校准完全无效——"self-governing" 前提被破坏，学习装置无法闭环（operator loop 实质为 no-op）。
- **最小修复**: `aletheia/agent_shell/operator_loop.py:888` — 在日志记录后调用 `save_config()` 并更新 `cfg.market_data` 中对应 family multiplier 字段；若该配置字段尚不存在，先在 `aletheia/config/schema.py` 增加 `family_multipliers: dict[str, float]` 字段，并同步在前端 Settings 页面添加对应控件（Config↔UI 规则）。
- **E2E test**: 无 xfail 测试；建议添加 spec claim `PI-01`。

---

---

## 四、7 条管道健康度评估

本节基于 `pipeline_trace_batch1_core.md` 与 `pipeline_trace_batch2_mgmt_learning.md` 的全程追踪结果。"断裂点"编号与 E2E xfail 测试 claim ID 对应。

---

### 4.1 market_data_acquisition

**producer → consumer**: `scheduler market_data 5min` → `CCXTProvider.get_ticker()` → `market_snapshots` 表 → `price_feed` 30s 缓存 → `risk_engine` 数据新鲜度检查 + `pnl._latest_price` + mark-to-market

**整体状态**: ✅ ok（1 个 minor 断裂）

**数据流概要**:
```
[scheduler 5min] → _run_market_data() → collect_market_snapshots()
  → CCXTProvider.get_ticker(instrument) per-call, never cached
  → insert_market_snapshot() → market_snapshots 表
  → append_jsonl("market_snapshots.jsonl")
  → price_feed 30s job → _price_cache[sym] = {price, ts, ...}
Consumers: risk_engine check #11 / pnl._latest_price / paper_broker._btc_price_now
```

**关键断裂点 MD-1**: xStock Bitget provider 连续失败时，错误被 `errors.append()` swallow，既不调用 `_record_job_failure()` 也不写 `infra_event`；连续故障在仪表盘无可见性，xStock 快照静默失效。

**E2E 测试**: `tests/specs/spec_pipeline_market_data.py` — 5 claims（PL-MD-01..05），4 passed，1 xfailed（MD-1，对应 PL-MD-05）。

---

### 4.2 paper_trade_decision

**producer → consumer**: `opportunities` 表（来自 market_observer）→ `paper_trader_ranker` 打分 → `PaperTrader` → `pi_decisions(signal_pending)` → PI Agent 守护进程 → `evaluate_trading_opportunity()` → `create_paper_proposal()` → Execution Gateway

**整体状态**: ⚠️ partial（2 个 moderate 断裂）

**关键断裂点**:
- **PT-1**: `signal_pending` 写入后，源 `opportunity` 状态不更新为 `"reviewed"`，下轮 5min 重复处理同一 opportunity，形成重复评分循环。
- **PT-2**: `PiDecision` 不携带 `opportunity_id`，PI Agent 提案与 opportunity 表脱节，`opportunities` 行永久留在 `open` 状态，表逐渐膨胀。

**E2E 测试**: `tests/specs/spec_pipeline_paper_trade.py` — 5 claims（PL-PT-01..05），3 passed，2 xfailed（PT-1 / PT-2，对应 PL-PT-04、PL-PT-05）。

---

### 4.3 risk_gate

**producer → consumer**: `submit_proposal()` → `_eval_lock` 串行化 → 16 项检查顺序执行 → `RiskDecision` 持久化 → 批准（路由到 broker）或拒绝（降级为 `paper_internal` / 全拒）

**整体状态**: ✅ ok（2 个 minor 断裂）

**关键断裂点**:
- **RG-1**: check #16 `_check_correlation_exposure` 任何内部异常都追加 `"correlation_check_error:internal_failure"` 违规，在 DB 降级状态下会静默封锁所有 paper trade（应 fail-open，不是 fail-closed）。
- **RG-2**: `_market_data_age_seconds()` per-symbol 查询在大表下无 `(symbol, rowid)` 复合索引，MVP 阶段可接受但需关注。

**E2E 测试**: `tests/specs/spec_pipeline_risk_gate.py` — 5 claims（PL-RG-01..05），5 passed，0 xfailed。

---

### 4.4 execution_chain

**producer → consumer**: 已批准的 `RiskDecision` → broker 路由（paper / bitget / okx / binance）→ `Order(pending)` 先行插入 → 交易所调用 → `Fill` + `Position` → JSONL 审计

**整体状态**: ⚠️ partial（3 个断裂，1 moderate + 2 minor）

**关键断裂点**:
- **EC-1**: `execution/gateway.py` 只调用 `write_audit()` 写 JSONL，从不调用 `insert_audit_event()`；`/api/audit` 的 SQLite `audit_events` 表对全部执行事件是空白的（已由 batch2 测试证伪，`write_audit()→_write_audit_row()→insert_audit_event()` 链路实际存在，EC-1 为误报，PL-EC-05 xpassed）。
- **EC-2**: `bitget_broker.close_demo_position()` 有重复 TPSL 取消代码块（copy-paste 债务，无安全风险）。
- **EC-3**: OKX symbol-filter 拒绝路径产生双重 audit 记录（broker 写一次，gateway catch 再写一次）。

**E2E 测试**: `tests/specs/spec_pipeline_execution_chain.py` — 5 claims（PL-EC-01..05），4 passed，0 xfailed，1 xpassed（EC-1 误报被证伪，说明 audit 链路完好）。

---

### 4.5 position_manager

**producer → consumer**: `scheduler 5min` → `evaluate_open_positions()` → `_sync_exchange_closed` + `_decide_exit` + `evaluate_market_exit_rules` → `close_position()` → `realized_pnl` 写入 → JSONL 审计

**整体状态**: ⚠️ partial（4 个断裂，全 moderate）

**关键断裂点**:
- **PM-1**: `_get_atr_pct()` 传入 `order_by="created_at DESC"` 但 `_select_all` 参数名为 `order=`，kwarg 静默忽略，ATR 自适应追踪止损永久失效，全部使用 flat `trail_pct`。
- **PM-2**: 原报告称 `close_position` 只写 JSONL；测试 PL-PM-06 证伪——`write_audit()→_write_audit_row()→insert_audit_event()` 链路完好，PM-2 为误报。
- **PM-3**: `_decide_exit` 缺少 regime-exit 分支（文档列出但未实现），仅靠 `market_aware_exits` 开关部分补偿。
- **PM-4**: `_sync_exchange_closed` 在 scheduler 线程内调用 `asyncio.run()`，若线程已有事件循环会产生 RuntimeError，exchange bracket sync 静默失败，demo 平仓的本地同步最多延迟 5min。

**E2E 测试**: `tests/specs/spec_pipeline_position_manager.py` — 6 claims（PL-PM-01..06），5 passed，1 xfailed（PM-1，对应 PL-PM-05）。

---

### 4.6 knowledge_learning_loop

**producer → consumer**: `post_trade_reviewer (4h)` + `risk_critic (6h)` + `dreaming_agent (daily)` + `insight_synthesizer (daily)` → `knowledge_items` + FTS5 → `retrieve_lessons()` → `paper_trader_ranker._lesson_penalty` / `strategy_designer.build_advisory_context` / `trading_decision Layer 3`

**整体状态**: ⚠️ partial（5 个断裂，1 critical + 4 moderate）

**关键断裂点（最严重管道）**:
- **KL-A1**（已知，已验证）: `dreaming_agent._extract_lessons` 写 `ps_lessons` 表，`retrieve_lessons()` 读 `knowledge_fts`；两表无桥接，结构化课程永久不可检索。
- **KL-B1**（已知，已验证）: `detect_contradictions()` 是永久 stub，返回 `[]`。
- **KL-1**: `create_daily_review` 无质量门控，LLM 失败时写入空串并索引 FTS（污染检索结果）。
- **KL-2**: `_persist_memo` 写 `tags=json.dumps(memo)`，但 `get_latest_memo()` 读 `content=` 字段，字段错位导致 `memo_scoring` 永远返回 0.0，insight_synthesizer 每轮重跑无限制。**这是整个系统最严重的静默断路**。
- **KL-3**: `_build_layer3_wisdom` 过滤 `item_type in ('lesson','accepted_lesson')`，但无任何 producer 写这两种类型；PI Agent Layer 3 wisdom 永远是空列表。

**E2E 测试**: 知识管道无独立 spec_pipeline 文件；KL-2 在 `spec_pipeline_paper_trade.py` 有间接覆盖（xfail）。

---

### 4.7 operator_loop

**producer → consumer**: `scheduler 4h:15` → `build_system_assessment()` → `decide_actions()` → Level 1 自动执行 / Level 2 _create_proposal（积压） / Level 3 封锁 → `audit_events` / `pi_decisions`

**整体状态**: ⚠️ partial（4 个新断裂 + 2 个已知确认）

**关键断裂点**:
- **OL-C03**（已知，已验证）: Level 2 proposals 写入 `operator_store` artifact，但无任何 UI 路由、无 PI daemon 消费、无自动过期；所有松弛类调校行动（loosen/widen/increase）永久积压不可见。
- **OL-C07**（已知，已验证）: `FORBIDDEN_INTENTS` 和 `_LEVEL_3_ACTIONS` 是两套独立清单，无 cross-reference 保证。
- **OL-1**: `_execute_level1_action` 含 `loosen_confidence/increase_sizing/widen_stop_loss` 死分支，这些都是 Level 2 行动；若分类逻辑出错会绕过 proposal queue。
- **OL-2**: `_action_trigger_optuna_sweep` 只调 `_record_action`，返回 `"executed"` 但未触发任何 Optuna job，是一个语义谎言。
- **OL-3**: `_persist_assessment` 把摘要写入 `tags` 字段，`content` 为 None，FTS 索引该 item 时 body 是空串。
- **OL-4**: `recent_streak` 计算遇到 `realized_pnl=None` 时被 `or 0` 截断，winning streak 统计偏低，Rule 5B 增仓提案触发频率不足。

**E2E 测试**: `tests/specs/spec_pipeline_operator_loop.py` — 5 claims（PL-OL-01..05），4 passed，1 xfailed（OL-2，对应 PL-OL-05）。

---

## 五、Git Log 错误模式分类学

基于 `audit_batch4_process_gitmine.md` 的 200 commit 分析（过去 60 天），识别出 6 类重复错误模式。

### 5.1 模式总表

| 模式 | 60天内次数 | 例 SHA | 根因 | 预防性 spec claim |
|------|-----------|--------|------|-----------------|
| **unit_precision** — 数值单位混淆（contractSize / fee_rate / decimals）| 3 | `650f608` (OKX contractSize×position), `3377d48` (fee_rate mode), `45250c9` (OKX compat) | 交易所 API 返回合约数 vs 币数 vs USDT 三种单位，无规范化层；fee_rate 在 % 和小数之间转换随意 | `spec_risk_engine.py`: assert `trade_proposal.quantity` 用规范化币单位；assert `fee_rate < 0.01`（小数，非百分比）|
| **data_contract_violation** — 跨模块字段语义漂移 | 4 | `e68b7bf` (paper-trader symbol mismatch), `3377d48` (direction_bias normalize), `a9b2d93` (portfolio mode filter vs equity curve), `d7c2570` (PnLZone realized tile) | SQLite 黑板字段随各 agent 迭代漂移，下游读方使用旧语义 | `spec_data_model.py`: assert `opportunities.symbol` 符合 `BTC/USDT` canonical 格式；assert portfolio endpoint 返回全模式 PnL |
| **silent_drop** — 数据静默消失无审计 | 3 | `5691bc7` (ranker kills non-target symbols), `78b3a9f` (paper-trader early-return on price miss), `831599c` (prediction fallback to top-volume) | guard/filter 逻辑 early-return 无结构化 event，最多 `logger.debug` | assert `paper_trader` 对每个被过滤 candidate 写 `candidate_skipped` event |
| **async_event_loop** — aiohttp/asyncio/scheduler 交互 | 3 | `d6995ec` (aiohttp session leak), `3541681` (IOCP drain §17), `da0d045` (SQLite lock storm from ProactorEventLoop) | Windows ProactorEventLoop IOCP drain 挂住 `asyncio.run()` 拆卸；per-call provider 规则未强制 | `spec_agent_contracts.py`: assert CCXT provider 每次调用新建；assert 无 session 跨 `asyncio.run()` 复用 |
| **sync_drift** — 文档/前端 vs 代码不同步 | 3 | `baec297` (scheduler job count in Overview.tsx), `63f691a` (§17 stale observer), `b6f6635` (scanner §16 doc vs code lag) | UI 和文档各自维护，scheduler job 数量以 magic number 硬编码在前端 | `spec_agent_contracts.py` AC-S08 已有；需补前端 `scheduler.status_snapshot().job_count >= 28` |
| **timezone_or_unicode** — UTC/tz 后缀漂移 | 2 | `9880fcb` (timestamps not UTC-aware), `0303907` (datetime tz parsing) | SQLite `datetime('now')` 存 naive UTC；Python 代码时而 aware 时而 naive；浏览器解析不一致 | `spec_data_model.py`: assert REST API 所有 `*_at` 字段结尾为 `Z` 或 `+00:00` |

### 5.2 高频文件修复频次表

| 文件 | 60天 fix 次数 | 根本性结构问题 | 建议 |
|------|-------------|--------------|------|
| `aletheia/portfolio/pnl.py` | **5** | PnL 须同时处理 paper/live/demo 三模式 + 交易所专属单位转换；无权威 spec | 提取 `PnLContext(mode, exchange)` dataclass；新建 `tests/specs/spec_pnl_contract.py` |
| `aletheia/agents/paper_trader.py` | 4 | Symbol 格式 + opportunity filter 随上游 schema 演化反复漂移 | spec_pipeline_paper_trade.py xfail 已锚定 PT-1/PT-2，修复后升为 pass |
| `aletheia/risk/engine.py` | 4 | paper/live mode 条件逻辑隐式，新增模式必触发回归 | spec_risk_engine.py claims 已有基础覆盖；缺 check #8 zero-capital 和 check #16 empty-positions 两个 claim |
| `frontend/src/pages/Settings.tsx` | 4 | 全配置 UI 单文件，随功能叠加无界增长 | 按 contract 分组拆分；lint spec 检测硬编码 px/hex |
| `aletheia/server/routes/news.py` + `settings.py` | 3 | 多 feature branch 叠加 field additions，diff 污染 | 已在当前 unstaged diff 中，需随 batch commit 一并提交 |
| `aletheia/agent_runtime/scheduler.py` | 3 | 以 accretion 增长；job 注册正确性无机器检验 | AC-S08 claim 已有；补 `_record_job_failure` 覆盖率 spec |

> **结论**: `pnl.py` 是 60 天内修复次数最多的文件（5 次），强烈建议在下一 sprint 提取 `PnLContext` 并为其增加 `spec_pnl_contract.py`，这将消除同一类根因导致的整类回归风险。

---

## 六、补强 Spec Claim / Fitness Function 清单

以下 20 条 actionable spec 补强，优先级从高到低排列。每条标注锚定契约节、不变量陈述和最低测试级别（L2a=结构/AST；L2b=行为/单元；L2c=持久性/集成；E2E=管道级）。

| Claim ID | 锚定契约节 | 不变量 | 测试方法 |
|----------|----------|--------|---------|
| **AU-S01** | `audit_chain_contract §3` | `write_audit()` 总写入 `_prev_hash` 字段；genesis 值为 `"0"*64` | L2a: AST 扫描 `jsonl.py` 仅含 `open(path, "a")` 模式，无 `"w"` |
| **AU-S02** | `audit_chain_contract §3` | `_prev_hash` 值为前一行 SHA-256（hex 64字符）| L2b: 写入 2 条后验证 record[1]["_prev_hash"] == sha256(line0) |
| **AU-S03** | `audit_chain_contract §4` | `verify_audit_chain()` 对新写链返回 `{"valid": True, "breaks": 0}` | L2b: 写入 5 条后调用 verify |
| **AU-S04** | `audit_chain_contract §8` | `append_jsonl("audit.jsonl", ...)` 直接调用必须 raise `ValueError` | L2b: 结构防护守卫 |
| **AU-S05** | `audit_chain_contract §6.3` | 凭证保存路由写入 `secret_rotated` audit event | L2c: POST `/settings/exchange-credentials` 后 SQLite `audit_events` 有 `event_type="secret_rotated"` 行 |
| **RE-C03** | `risk_engine_contract §2 check #16` | `_check_correlation_exposure` 对零持仓返回 `[]`，非 exception | L2b: 零持仓列表传入，断言返回值为空列表 |
| **RE-C04** | `risk_engine_contract §2 check #8` | `capital_base=0` 时日损门控 fallback 用 `starting_capital_usdt` 而非 0 | L2b: 零 cap 配置下 check #8 不静默 bypass |
| **EX-C01** | `execution_contract §1` | 执行层创建的 `Position` 行必须携带非空 `opportunity_id` | L2c: paper_internal 执行后 SELECT position.opportunity_id IS NOT NULL |
| **EX-B01** | `execution_contract §3` | 交易所调用成功但后续 DB update 失败时，proposal 状态转为 `"error"` 而非留在 `"pending"` | L2b: mock `update_trade_proposal` raise，断言 proposal 最终 status="error" |
| **PG-B05** | `promotion_gates_contract §2` | `symbol_blocked` 拒绝的 live 提案**不**计入 critical violations 计数（当前实际行为） | L2b: 断言 `_critical_risk_violations` 对 `symbol_blocked` violation 返回 0 |
| **PG-B06** | `promotion_gates_contract §2` | `_max_drawdown_pct` 在 `capital_base<=0` 时返回 `float("inf")`（拒绝推广） | L2b: 零 capital 配置，断言 drawdown gate 失败（inf > threshold） |
| **KC-01** | `knowledge_contract §7.2` | `create_trading_guard()` 写入的 `trading_guard` item 可被 PI Agent 过滤器检索（不论 `lesson`/`accepted_lesson` 无 producer） | L2c: create guard → query item_type="trading_guard" 返回≥1 行 |
| **KC-04** | `knowledge_contract §3.2` | `retrieval.index_knowledge_item()` 对 non-`high_confidence` item 返回 False（不索引） | L2b: 传入 `confidence="speculative"` item，断言 FTS 无对应行 |
| **LU-B01** | `live_unlock_contract §2 BACKLOG-LU-01` | `operator.permission_mode="ReadOnly"` 时 POST `/live-unlock` 返回 HTTP 403 | E2E: TestClient POST with ReadOnly config |
| **LU-B02** | `live_unlock_contract §2` | 重复 POST `/live-lock`（已是 `live_disabled`）不写 before==after 的冗余 audit event | L2b: 断言幂等锁定不新增 audit 行 |
| **LU-C01** | `live_unlock_contract §5` | `RESTRICTED_TOOLS` ∩ `FORBIDDEN_INTENTS` — 危险命令不可在两套清单中只存一套 | L2a: 两组 frozenset 互为超集检查 |
| **AC-S10** | `agent_contracts §D` / scheduler | `_record_job_failure` 在全部 30 个 job 的失败路径中被调用（INV-SCH-5） | L2a: AST 扫描所有 `_run_*` 函数含 `_record_job_failure` 调用 |
| **DL-C01** | `data_layer_contract §9` | OHLCV 窗口配置与代码默认值一致（两者均为 500 或均为 50000，不可并存） | L2a: 读取 `_OHLCV_WINDOW_DEFAULT` 和 `historical_data.max_bars_per_pair` 默认值，断言相等 |
| **PT-C01** | `agent_contracts §2.A` (frozen) | `opportunity_id` 在 signal_pending → proposal 链路中保留不丢失 | E2E: xfailed PL-PT-04，pending frozen-zone resolution |
| **PL-C01** | `pnl.py` 高频修复防护 | `compute_and_store_snapshot()` 输出 `realized_pnl` 为 `float`（非 None）；`fee_rate < 0.01`（小数） | L2b: 新建 `spec_pnl_contract.py`，3 个 behavioral claims |

---

## 七、E2E 管道测试套件成果总结

| 管道 | spec 文件 | 总 claims | passed | xfailed（真实 drift）| xpassed（证伪误报）| LLM smoke | 捕获 drift IDs |
|------|----------|---------|--------|-------------------|-----------------|-----------|---------------|
| risk_gate | `spec_pipeline_risk_gate.py` | 5 | 5 | 0 | 0 | ❌ mock | — |
| execution_chain | `spec_pipeline_execution_chain.py` | 5 | 4 | 0 | 1 (PL-EC-05) | ❌ mock | EC-1 已修（误报） |
| live_unlock | `spec_pipeline_live_unlock.py` | 5 | 4 | 1 | 0 | ❌ mock | PL-LU-05: FORBIDDEN_INTENTS 缺 2 项 |
| market_data | `spec_pipeline_market_data.py` | 5 | 4 | 1 | 0 | ❌ mock | PL-MD-05: xStock 连续失败无 infra_event |
| paper_trade | `spec_pipeline_paper_trade.py` | 5 | 3 | 2 | 0 | ❌ mock | PL-PT-04 (opportunity_id 丢失), PL-PT-05 (opportunity 状态不更新) |
| position_manager | `spec_pipeline_position_manager.py` | 6 | 5 | 1 | 0 | ❌ mock | PL-PM-05: ATR kwarg 拼写错误 |
| operator_loop | `spec_pipeline_operator_loop.py` | 5 | 4 | 1 | 0 | ❌ mock | PL-OL-05: optuna sweep no-op |
| **合计** | **7 文件** | **36** | **29** | **6** | **1** | — | **5 个真实 drift + 1 误报证伪** |

**说明**：
- 36 claims 全部无 pytest failure（CI 安全）。
- 6 xfailed 均为 `strict=True`，严格捕获真实代码偏离（5 个 drift + 0 误报）；1 个 xpassed 是 PL-EC-05，证明 EC-1 的 audit gap 已由 `_write_audit_row` 链路修复，属有价值的误报识别。
- 全部 xfail drift 已在 §八 Top 25 中有对应修复项。

---

## 八、综合修复优先级 — Top 25 修复项

排序逻辑：`impact × drift_frequency / fix_cost`，critical 在前；frozen-zone 标记不可在当前 sprint 修。

| Rank | ID | 类型 | 文件 | 简述 | 严重度 | 工作量 (h) | 阻塞? | E2E xfail? |
|------|-----|------|------|------|-------|----------|-------|----------|
| 1 | CR-RE-01 | drift | `aletheia/risk/engine.py:252-256` | 拒绝提案的 `allowed_execution_mode` 写 `"paper_internal"`，原始模式丢失，导致 CR-PG-01 dead branch | 🔴 | 2 | trust | — |
| 2 | CR-PG-01 | drift | `aletheia/trading/strategy_lifecycle.py:395-408` | `_critical_risk_violations` dead branch；promotion gate 计数逻辑不可信 | 🔴 | 0.5\* | trust | — |
| 3 | KL-2 | drift | `aletheia/agents/insight_synthesizer.py:464` | `_persist_memo` 写 `tags=`，`get_latest_memo` 读 `content=`；memo_scoring 永久 0，学习反馈断路 | 🔴 | 0.5 | no | — |
| 4 | CR-AU-01 | drift | `tests/specs/` (缺失) | `spec_audit_chain.py` 完全不存在；信任边界模块零 spec 保护 | 🔴 | 2 | trust | — |
| 5 | CR-EX-01 | drift | `aletheia/execution/reconciliation.py:21` | reconciliation 只覆盖 `bitget_live_limited`，OKX/Binance live 订单永久停留 `submitted` | 🔴 | 2 | trust | — |
| 6 | CR-DL-01 | drift | `aletheia/storage/repositories.py:1049` | OHLCV 窗口 50,000 vs 契约 500（100×）；SQLite WAL 延迟风险 | 🔴 | 0.5 | trust | — |
| 7 | CR-DM-01 | drift | `aletheia/storage/models.py:9` | ID 12-char hex vs 契约 UUID4 36-char；百万行碰撞风险；至少更新文档 | 🔴 | 1 | trust | — |
| 8 | CR-AR-01 | drift | `docs/design/api/api_reference.md §Runtime` | 路由前缀 `/api/runtime` vs 实际 `/api/operator/runtime`；rollback/drain 404 | 🔴 | 0.5 | no | — |
| 9 | KL-3 | drift | `aletheia/agent_shell/trading_context.py:367` | Layer 3 过滤 `lesson/accepted_lesson` 无 producer；Layer 3 wisdom 永远空 | 🟠 | — | **frozen** | — |
| 10 | CR-PI-01 | drift | `aletheia/agent_shell/operator_loop.py:882` | scanner family 权重不持久化；自治调校 no-op | 🔴 | 2 | no | — |
| 11 | PM-1 | drift | `aletheia/portfolio/position_manager.py:72` | `_get_atr_pct` kwarg `order_by=` → `order=`；ATR 自适应追踪永久失效 | 🟠 | 0.5 | no | PL-PM-05 ✓ |
| 12 | PT-2 | drift | `aletheia/agents/paper_trader.py:138` | `PiDecision` 不携带 `opportunity_id`；opportunity 表 open 行堆积 | 🟠 | 1 | no | PL-PT-04 ✓ |
| 13 | PT-1 | drift | `aletheia/agents/paper_trader.py:137` | `signal_pending` 写入后 opportunity 状态不更新，重复评分 | 🟠 | 0.5 | no | PL-PT-05 ✓ |
| 14 | CR-LU-01 | drift | `aletheia/server/routes/live.py:17-45` | live unlock 未检查 `operator.permission_mode`；BACKLOG-LU-01 | 🟠 | 1 | trust | — |
| 15 | CR-EX-03 | drift | `aletheia/execution/bitget_broker.py:141-149` | `Position` 行缺 `opportunity_id`；sector 集中度 check #16 永久静默 | 🟠 | 1 | trust | — |
| 16 | OL-2 | drift | `aletheia/agent_shell/operator_loop.py:669-679` | `trigger_optuna_sweep` 只调 `_record_action`，返回 "executed" 但无任何 Optuna job | 🟠 | 1 | no | PL-OL-05 ✓ |
| 17 | CR-SCH-01 | implicit_invariant | `aletheia/agent_runtime/scheduler.py:771` | 24/30 个 job 无 `_record_job_failure`；持续失败不报警 | 🟠 | 2 | no | — |
| 18 | MD-1 | implicit_invariant | `aletheia/trading/market_data.py:122-132` | xStock 连续失败无 `infra_event`；静默降级 | 🟠 | 1 | no | PL-MD-05 ✓ |
| 19 | CR-AR-02 | drift | `aletheia/server/routes/settings.py:12` | `POST /settings/permission-mode` 拒绝全部 5 种 operator 模式 | 🟠 | 1.5 | no | — |
| 20 | CR-AU-03 | implicit_invariant | `aletheia/server/routes/bitget.py` | 凭证保存路由可能未写 `secret_rotated` audit event | 🟠 | 1 | trust | — |
| 21 | KL-1 | drift | `aletheia/knowledge/manager.py:118-133` | `create_daily_review` 无质量门控；空体 FTS 污染 | 🟠 | 0.5 | no | — |
| 22 | CR-RE-03 | implicit_invariant | `aletheia/risk/engine.py:154` | `or 0.0` fallback 静默 bypass 日损门控（zero-cap 场景） | 🟠 | 0.5 | no | — |
| 23 | EC-2 | drift | `aletheia/execution/gateway.py` | 执行事件 `insert_audit_event` 链路已验证完好（EC-1 误报）；EC-2 双 audit 噪声 | 🟡 | 0.5 | no | — |
| 24 | CR-AU-04 | implicit_invariant | `aletheia/files/jsonl.py:36` | `append_jsonl("audit.jsonl", ...)` 公开 API 无结构防护 | 🟡 | 0.5 | no | — |
| 25 | CR-DM-04 | drift | `aletheia/files/jsonl.py:191` | `datetime.utcnow()` → 缺 `+00:00` tz 后缀；一行 fix | 🟡 | 0.5 | no | — |

\* Rank 2 (CR-PG-01) 工作量依赖 Rank 1 (CR-RE-01) 完成后零成本 follow-up。

**frozen 项说明**: Rank 9 (KL-3) 根修复路径在 `trading_context.py`（冻结区），**当前 sprint 不可触碰**。需在实验期结束后 Round 2 处理。

---

## 九、与 SDD 第一性原理的契合度复盘

### 9.1 "Hard control 不可绕过"

**已落地的部分**: kill_switch（`aletheia/risk/kill_switch.py`）和 `live_disabled` 状态门控已有 spec 保护——`spec_pipeline_risk_gate.py PL-RG-02` 验证 kill_switch 路径，`spec_pipeline_live_unlock.py PL-LU-01~03` 验证解锁协议。`FORBIDDEN_INTENTS` 列表阻止 LLM shell 调用解锁端点，`RESTRICTED_TOOLS` 限制 tool-call 权限。结构性锁定是完好的。

**仍裸露的部分**: CR-LU-01 揭示服务端 live unlock 未校验 `operator.permission_mode`——任何本机 HTTP POST 可绕过操作员层。PL-LU-05 xfailed 确认 `deactivate_kill_switch` 和 `modify_permissions` 这两个高危命令**不在** `FORBIDDEN_INTENTS` 中，意味着 FORBIDDEN_INTENTS 的第一道防线存在漏洞。CR-AU-01 更为根本：`spec_audit_chain.py` 完全不存在，信任硬墙的最终审计证据（防篡改 hash 链）无任何 spec 盾牌保护。这是第一性原理层面最危险的暴露：**hard control 的 "不可绕过" 依赖 audit chain 为后验证据，但该证据机制本身无 spec 保护**。

### 9.2 "因果链不可破坏"

**已落地的部分**: 执行层原子性（Order 先于交易所调用写入 DB，`spec_pipeline_execution_chain.py PL-EC-01` 验证），风险引擎 16 项检查串行化（`_eval_lock`），PL-RG-03 验证并发安全。OKX contractSize 单位转换（`650f608`）已修复并纳入 spec 覆盖。

**仍裸露的部分**: CR-EX-03 和 PT-2 形成一对相互增强的因果链断口——`Position` 行缺 `opportunity_id`（CR-EX-03），`PiDecision` 同样缺 `opportunity_id`（PT-2）。这使得从"市场观察 → 机会 → 信号 → 提案 → 仓位 → 损益"的完整溯源链在两个节点上断开。CR-RE-01 / CR-PG-01 的根因是 `RiskDecision` 字段语义歧义（`allowed_execution_mode` 被双重使用），导致 promotion gate 的 critical violations 统计分支永久死代码——因果链完整性在推广逻辑层被悄然破坏。

### 9.3 "学习装置闭环"

**现状最脆弱的环节**。三个独立断路叠加，构成系统能力退化的核心风险：

1. **KL-2**（最严重）: insight_synthesizer 写 memo 到错误字段，`get_latest_memo()` 永远返回 None → `_memo_scoring` 固定输出 0.0 → paper_trader_ranker 的 regime / timing 统计反馈**完全静默**。
2. **KL-3**（冻结区）: Layer 3 wisdom 类型名与实际 producer 类型不匹配，PI Agent 每次决策都在无历史 lessons 的真空中运作。
3. **KL-A1**（已知）: dreaming_agent 的结构化课程写入 `ps_lessons`，`retrieve_lessons()` 只读 `knowledge_fts`，两表无桥接。

三个断路相互独立——修复任意一个不会自动修复其他两个。最速的投入产出比是修 KL-2（1 行字段名修复，0.5h），它会立即恢复 insight_synthesizer 的速率限制 + paper_trader_ranker 的 memo 评分，是当前 sprint 里效益最高的单个修复。KL-3 因冻结区约束，须等实验期结束后处理。

### 9.4 距离 VISION 终点线 A（Scorecard ≥ 70）的差距

当前 Scorecard 推进的主要技术障碍（按影响 Scorecard 分项排序）：

1. **学习装置静默断路（KL-2 + KL-A1）**: Scorecard 的"经验积累"维度几乎无输入——每次决策等价于第一天。修复 KL-2 是最小成本最大收益的 Scorecard 改善项。
2. **promotion gate 完整性（CR-RE-01 + CR-PG-01）**: 策略进阶门控数据不可信；Scorecard 的"自治治理质量"维度数据失真。
3. **PI 操作员自治调校无效（CR-PI-01）**: scanner family 权重调整不持久化，Scorecard 的"自适应信号质量"维度的调校反馈回路是 no-op。
4. **audit chain 无 spec（CR-AU-01）**: 若出现监管或事后审查场景，audit chain 完整性无可执行测试背书；VISION 的"可信任"维度评分会被下调。
5. **execution reconciliation 覆盖不全（CR-EX-01）**: 若启用 OKX/Binance live，live P&L 与 Scorecard 的"实盘表现"维度数据失真，导致 terminal phase 评分不可信。

---

## 十、给开发者的下一步行动清单

以下 6 项行动，按"最大收益 / 最小冻结风险"顺序排列，全部在当前 sprint 内可执行（KL-3 除外，已标注）。

1. **修 `insight_synthesizer._persist_memo` 字段错位（§八 Rank 3，KL-2）**
   `aletheia/agents/insight_synthesizer.py:464` — 把 `tags=json.dumps(memo)` 改为 `content=json.dumps(memo)`（或在 `get_latest_memo` 中改读 `tags`，选一致方向）。此修复恢复 memo_scoring 和 `_should_skip_synthesis()` 速率限制，是整个审计报告效益最高的单行修复。

2. **修 `_get_atr_pct` kwarg 拼写错误（§八 Rank 11，PM-1）**
   `aletheia/portfolio/position_manager.py:72` — `order_by="created_at DESC"` → `order="created_at DESC"`。修复后 ATR 自适应追踪止损生效，同时 PL-PM-05 xfail 升为 pass。

3. **修 `paper_trader` PT-1 / PT-2（§八 Rank 12–13）**
   `aletheia/agents/paper_trader.py` — 写 `signal_pending` 后对源 opportunity 调用 `update_opportunity(opp_id, status="reviewed")`（PT-1）；向 `PiDecision` 传入 `proposal_id=candidate["opportunity_id"]`（PT-2）。两处 fix 均在 paper_trader.py 内，不接触冻结区。PL-PT-04、PL-PT-05 xfail 升为 pass。

4. **补 `aletheia/risk/engine.py:252-256` 持久化提案原始执行模式（§八 Rank 1，CR-RE-01）**
   在 `RiskDecision` 增加 `proposal_execution_mode` 字段（向后兼容列添加）；同步修复 `strategy_lifecycle.py:399` dead branch 注释（CR-PG-01 follow-up，Rank 2 零成本）。先阅读 `tests/specs/spec_risk_engine.py` 确认无 claim 会被影响。

5. **创建 `tests/specs/spec_audit_chain.py`（§八 Rank 4，CR-AU-01；含§六 AU-S01..AU-S05）**
   新建 spec 文件，最少 5 个 claims（AUDIT-01 append-only、AUDIT-02 `_prev_hash` SHA-256、AUDIT-03 verify 新链 valid、AUDIT-04 `append_jsonl("audit.jsonl")` raise guard、AUDIT-05 双锁写入）。全部 L2a/L2b，无生产代码改动。这是整个审计报告里风险最低、信任收益最高的独立补强。

6. **reconcile OHLCV 窗口（§八 Rank 6，CR-DL-01）+ 补 runtime 路由文档（§八 Rank 8，CR-AR-01）**
   做一个决策：`_OHLCV_WINDOW_DEFAULT = 500` 还是 50,000？选定后同步更新 `data_layer.md §9` 与代码（选 500 时同步验证 `historical_data.max_bars_per_pair` 默认值）。同时将 `api_reference.md §Runtime` 的路径前缀从 `/runtime/` 改为 `/operator/runtime/`（纯文档，5 分钟）。两项合并为单次 doc-fix commit。

> **冻结区说明（KL-3）**: Rank 9 的根修复在 `trading_context.py`（实验冻结区），当前 sprint **禁止触碰**。实验期结束后首要处理：将 `_build_layer3_wisdom` 类型过滤从 `('lesson','accepted_lesson')` 扩展到 `('post_trade_review','trade_case_study')`，并在 spec 中为该变更增加 E2E 验证。

