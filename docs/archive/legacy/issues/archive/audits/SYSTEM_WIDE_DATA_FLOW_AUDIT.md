> **⚠️ 已归档(2026-06-26)** — 本报告是 2026-06-19→06-23 的点对点快照。4-agent 对 HEAD 逐条核实:列举项绝大多数已修或已被 `FIX_PLAN_2026-06-26.md` / `frozen_optimization_backlog.md` 捕获;仍开放且未被别处记录的残余项已迁入 `../BACKEND_ISSUES.md` §19。当前开放 backlog 以 FIX_PLAN 为准。

# 全系统数据流审计报告（最小赚钱循环以外）

> 审计日期：2026-06-19
> 方法论：数据流追踪（producer→consumer 对齐 + None 路径分析 + SQL 字段验证）
> 范围：风控引擎、配置系统、Operator 层、持仓对账、知识库持久化

---

## 严重程度图例

| 级别 | 含义 |
|------|------|
| **P0** | 安全/风控边界失效 — 可能导致无约束损失 |
| **P1** | 功能性断裂 — 系统设计意图无法实现 |
| **P2** | 数据黑洞/可观测性缺失 — 不直接影响交易但浪费资源 |

---

## P0 — 风控边界失效

### 1. daily loss 混用所有 execution_mode 的 PnL

**位置**：`aletheia/risk/engine.py:150`

**问题**：`today_realized_pnl()` 无参数调用，paper/demo/live 三种模式 PnL 混合计算，
但 `capital_base` 只取 live 上限（100 USDT）。

**影响**：
- demo 模式的大亏损误触发 kill switch（paper 被意外停止）
- live 模式的亏损被 demo 盈利抵消，漏报真实风险

**修复**：传入 `execution_mode` 参数过滤，或分模式计算。

---

### 2. `closed_at` 格式不一致导致 daily loss 漏计

**位置**：`aletheia/portfolio/pnl.py:111-116`

**问题**：`startswith(today)` 做日期匹配，依赖 `closed_at` 字符串以 ISO date 开头。
但 `closed_at: str | None` 无格式约束，若写入格式有偏差则当日亏损统计为 0。

**影响**：daily loss 阈值检查失效，kill switch 永不自动触发。

**修复**：用 `datetime.fromisoformat()` 做真实日期比较而非字符串前缀匹配。

---

### 3. `allowed_exchange` 和 `require_risk_check_for_all_orders` 是死配置

**位置**：`aletheia/config/schema.py:198,201`

**问题**：两个字段定义在 schema 中，`engine.py` 完全未读取。用户以为设置了保护，实际无效。

**影响**：配置欺骗性，安全边界形同虚设。

**修复**：在 engine.py 中实现检查逻辑，或从 schema 中删除这两个字段。

---

## P1 — 功能性断裂

### 4. `trading_guard` 写入 FTS 但无角色白名单消费

**位置**：`aletheia/knowledge/router.py:22-26`

**问题**：`trading_guard` 类型创建时默认 `high_confidence`，经 `_index()` 写入 FTS，
但 `_ITEM_TYPES_BY_ROLE` 中没有任何角色包含 `trading_guard`。`retrieve_for_role()` 永远不会检索到它。

**影响**：用户精心编写的交易守则进了 FTS 但对所有 agent 不可见。guard 的"阻断"作用由 `evaluate_guards` 完成，但"知识检索"路径断裂。

---

### 5. Portfolio snapshot 口径不一致（paper 模式用 live equity）

**位置**：`aletheia/portfolio/pnl.py:193-196, :237`

**问题**：`compute_and_store_snapshot` 以 `execution_mode='paper_internal'` 调用，
但 `compute_portfolio()` 内部取的是 Bitget 真实账户的 exchange equity，快照的 `total_value_usdt` 实际是 live 值，但 `execution_mode` 字段标为 paper。

**影响**：前端图表展示的 portfolio 曲线混合了 paper 和 live 口径，用户无法分辨。

---

### 6. Adopted position 的 max_hold 保护失效

**位置**：`aletheia/portfolio/position_reconciler.py:243`

**问题**：`adopt_position()` 写入的 `opened_at` 是 adoption 时刻（`datetime.now()`），
而非 exchange 返回的真实开仓时间。`evaluate_open_positions()` 的 `max_hold_hours` 从 `opened_at` 计时。

**影响**：已持有多天的外部仓位被 adopt 后，max_hold 保护从零重新计时，超时平仓规则失效。

---

### 7. kill switch 激活后 paper_trader 不主动退出

**位置**：`aletheia/agents/paper_trader.py`（入口无早退检查）

**问题**：scheduler 仍触发 paper_trader，每次产生 DB 写入后在 Risk Engine 逐一拒绝。

**影响**：kill switch 期间资源浪费、日志噪声，无法快速中止。

---

### 8. `market_data.*` 无 UI 控件 — 用户无法管理观察标的

**位置**：`aletheia/config/schema.py:65-90`，`frontend/src/pages/Settings.tsx`

**问题**：`observed_symbols` / `observed_stocks` / `ccxt_exchange` / `ccxt_fallback`
等决定 agent 观察哪些标的的核心配置，在 Settings UI 中无控件。只能手动改 YAML。

**影响**：违反"Web UI only"原则，用户无法通过 dashboard 调整观察列表。

---

### 9. `runtime.scheduler_enabled` 保存后不通知 APScheduler

**位置**：`aletheia/server/routes/settings.py:147-163`

**问题**：UI 修改 `scheduler_enabled = false` → 写 YAML → 返回成功。
但内存中的 APScheduler 不感知，继续运行所有 job。

**影响**：用户以为关闭了 scheduler，实际上 agents 仍在跑。

---

### 10. `sessions.py:create_session` 缺少 3 个字段

**位置**：`aletheia/agent_shell/sessions.py:44`

**问题**：直接 INSERT 到 SQLite 只含 `id, user_input, kind, created_at`，
缺少 `parent_session_id`、`title`、`runtime_id`。
而 `operator_store.create_session_record()` 才是完整写法。

**影响**：通过此路径创建的 session 在 DB 中缺少会话树信息，查询时 parent trace 断裂。

---

## P2 — 可观测性/资源浪费

### 11. `_check_lesson_constraints` 静默吞异常

**位置**：`aletheia/risk/engine.py:306-307`

`except Exception: pass`，DB 故障时 lesson 级约束无声失效。

### 12. Guard ctx None → 命中率统计失真

**位置**：`aletheia/knowledge/guards.py:239`

`_applies_to_context` 对 None 值返回 True，但 `_eval_predicate` 返回 False，
guard 命中计数偏低，auto_promote 阈值失真。

### 13. `dreaming_log` 在 market_observer 白名单但 confidence 门永远拦截

**位置**：`aletheia/knowledge/router.py:24`

死配置，白名单包含 `dreaming_log` 但 FTS 检索的 WHERE 条件要求 high_confidence。

### 14. audit.py result_summary 截断 500 字符，完整输出无 API 暴露

**位置**：`aletheia/agent_shell/audit.py:19`

前端 command 列表看不到完整输出，完整响应只在 JSONL 中。

### 15. runtime_continuity 未接入 lifecycle

**位置**：`aletheia/gateway/lifecycle.py`（无引用）

`RuntimeContinuityService` 有 API 路由但 gateway 启动不初始化，feature flag 默认 off。

---

## 修复优先级

| 优先级 | 编号 | 描述 | 预估 |
|--------|------|------|------|
| **P0-1** | #1 | daily loss 分 execution_mode 计算 | 15 min |
| **P0-2** | #2 | closed_at 用 datetime 比较 | 10 min |
| **P0-3** | #3 | 删除或实现死配置字段 | 10 min |
| **P1-1** | #4 | trading_guard 加入角色白名单 | 5 min |
| **P1-2** | #5 | snapshot 分模式存储或标记来源 | 30 min |
| **P1-3** | #6 | adopt 时写入 exchange 返回的真实 opened_at | 15 min |
| **P1-4** | #7 | paper_trader 入口加 kill_switch 早退检查 | 10 min |
| **P1-5** | #8 | Settings UI 增加 market_data 控件 | 2h（前端） |
| **P1-6** | #9 | scheduler_enabled 变更时暂停/恢复 APScheduler | 20 min |
| **P1-7** | #10 | 统一 session 创建路径 | 15 min |
| **P2** | #11-15 | 可观测性改善 | 各 5-15 min |

---

## 与上次审计的对比

| 维度 | 最小赚钱循环审计 | 本次全系统审计 |
|------|-----------------|--------------|
| P0 数量 | 3 | 3 |
| 最危险类型 | 数据流断裂（字段名错误） | 风控边界失效（PnL 混算） |
| 共同根因 | 静默失败 + 跨模块不对齐 | 同上 + 死配置欺骗性 |
| 新发现的盲区 | — | "配置定义但未实现"也是断裂 |
