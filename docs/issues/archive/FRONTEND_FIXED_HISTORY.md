# FRONTEND 已修复历史区（归档 2026-06-26）

> 从 `FRONTEND_ISSUES.md` 归档。§10（已修复历史区 10.1–10.24）· §5（Markets 页数据展示，2026-06-14 全修）· §4（OAuth Provider UI，2026-06-14）。保留 file:line + commit 供追溯。

## 10. 已修复（历史区）

> 修复后从 §1–§3 移到这里，保留 file:line 与修复 commit hash。

### 10.1 §3.1 共享 domain types — ✅ 已修复

- 新建 `frontend/src/types.ts`：TradeProposal / RiskDecision / Order / Fill / Position / Opportunity / StrategyRecord / KnowledgeItem / AgentRun / MarketSnapshot / PortfolioSnapshot / PortfolioSummary / RiskStatus / SystemStatus
- 全 7 个数据页面已从 `as any[]` 迁移到强类型

### 10.2 §3.2 DataTable 通用组件 — ✅ 已修复

- 新建 `frontend/src/components/DataTable.tsx`：基于 `@tanstack/react-table` v8（之前装了但没用）
- 支持排序、全局过滤、分页（>pageSize 时自动显示）、行点击回调
- 附带 `StatusBadge` 通用组件
- 已迁移页面：Proposals / AgentRuns / Positions / PaperTrades / Opportunities / KnowledgeBase / StrategyLifecycle

### 10.3 §3.3 DetailDrawer 通用组件 — ✅ 已修复

- 新建 `frontend/src/components/DetailDrawer.tsx`：侧滑抽屉 + Esc 关闭 + backdrop
- 附带 `DrawerSection` / `DrawerField` 布局辅助组件
- 所有 7 个数据页面已实装详情抽屉

### 10.4 §1.1 Proposals 审批缺失信息 — ✅ 已修复

- ✅ agent_rationale 完整显示（抽屉内 pre-wrap）
- ✅ strategy_id / opportunity_id 可见
- ✅ 确认对话框 confirm() 拦截批准操作
- ✅ risk_decision 展示：后端 `GET /api/proposals/{id}/risk-decision` + 前端 RiskDecisionSection 组件显示 decision/risk_score/message/violations

### 10.5 §1.2 Agent Runs trace details — ✅ 已修复

- ✅ 抽屉显示 tools_called / outputs_created / errors JSON 列表
- ✅ 后端 `GET /api/agent-runs/{run_id}/tool-calls` 提供结构化 tool_call 记录
- ✅ 前端 ToolCallsSection 组件：显示 tool_name / tool_class / permitted / time 表格；无记录时 fallback 到 agent_run.tools_called JSON

### 10.6 §1.3 Overview today_pnl 静默 fallback — ✅ 已修复

- `Overview.tsx:71` 不再用 `today_pnl ?? unrealized_pnl`；分成两行独立显示，今日 PnL 为 null 时显示 "not available"

### 10.7 §1.3 StrategyLifecycle alert(e.message) — ✅ 已修复

- 不再用 `alert()`；promote 错误结构化显示在抽屉内 `.promote-error` 区块
- 解析后端 gate failures JSON，分条展示

### 10.8 §2.1 Pipeline 视觉表达 — ✅ 已修复

- 新增 `PipelineStepper` 组件：横向步骤条 + done/active/future 三态样式
- 每个 strategy 在抽屉内显示当前所处管线位置

### 10.9 §3.4 / §3.6 内联 style + utility 类 — ✅ 已修复

- 新增 utility 类：flex / flex-wrap / gap-N / items-center / justify-between / text-muted / text-sm / mono / mt-N / mb-N
- 迁移页面中的 `style={{ display: 'flex', gap: '...' }}` 改为 utility 类

### 10.10 §2.1 字段补充

- ✅ Positions：新增 Stop / TP 两列
- ✅ Opportunities：新增 Source Agent / Created 列 + 抽屉显示 thesis 全文
- ✅ PaperTrades：新增 Mode / Created 列 + 抽屉显示 proposal_id / risk_decision_id
- ✅ KnowledgeBase：抽屉显示 item_type / tags / file_path / strategy_id

### 10.11 §1.2 KB markdown 内容阅读 — ✅ 已修复

- 后端 `GET /api/knowledge/{item_id}/content` 读取 markdown 文件内容
- 前端 `KnowledgeBase.tsx` 抽屉内渲染 markdown（MarkdownContent 组件 + 简易解析器）
- `index.css` 新增 `.markdown-body` 样式块（§3.9 一并修复）

### 10.12 §2.3 autonomous_paper_trading 可见性 — ✅ 已修复

- 后端 `POST /api/settings/autonomous-paper-trading` 端点（含 audit 审计）
- 前端 PermissionCard 新增 checkbox toggle + PaperAuto 模式联动警告
- 新增 EffectiveConfigCard 只读卡片：显示 paused / scheduler_enabled / permission_mode / autonomous_learning / autonomous_paper_trading / kill_switch_active 全集

### 10.13 §3.7 chart dark theme 颜色 — ✅ 已修复

- 新增 `frontend/src/charts/theme.ts`：统一 chart 颜色常量（对齐 index.css CSS variables）
- PriceChart / PnLChart 不再硬编码颜色，改用 chartTheme 对象

### 10.14 §3.8 chart marker 点击回调 — ✅ 已修复

- PriceChart 新增 `onMarkerClick(id)` prop + `TradeMarker.id` 字段
- Charts.tsx 传递 order ID，点击 marker 展示交易详情卡片

### 10.15 §3.9 markdown 渲染样式 — ✅ 已修复

- `index.css` 新增 `.markdown-body` 完整样式（h1-h3/p/ul/code/pre/blockquote/hr）

### 10.16 §2.2 chart marker 可点击 — ✅ 已修复

- lightweight-charts subscribeClick + marker 数据携带 order_id
- Charts.tsx 点击展开 trade detail card

### 10.17 §2.4 Reports promotion gate 指标展示 — ✅ 已修复

- Reports.tsx 新增 StrategyMetrics 组件：对 active strategies 调用 gates API 展示 paper_runtime_days / trades / drawdown / pnl 等指标
- StrategyLifecycle.tsx 新增 PromotionGates 组件：抽屉内展示所有 gate 通过/失败状态 + metrics + failures 列表

### 10.18 §1.3 Risk Boundaries 单位提示 — ✅ 已修复

- Settings.tsx RiskCard 新增 RISK_UNITS 映射：展示 %, USDT, x, min 等单位
- field name 用 replace(/_/g, ' ') 人性化显示

### 10.19 §2.2 Paper Trades / Proposals "View on Chart" 反向链接 — ✅ 已修复

- PaperTrades.tsx 抽屉内已填充订单显示 "View on Chart" 按钮，navigate 到 /charts 页
- Proposals.tsx 抽屉内显示 "View on Chart" 按钮
- Charts.tsx marker 已携带 order_id，支持双向连接

### 10.20 §1.2 Dreaming page 6-section layout — ✅ 已修复

- 后端 `GET /api/dreaming/summary` 提供聚合数据（24h research_notes / strategy_hypotheses / dreaming_logs + 7d deprecated memories + recent runs）
- 前端 Dreaming.tsx 重写为 5 个 section：Current Task / Recent Consolidations / New Research Notes / New Strategy Hypotheses / Deprecated Memories
- 点击任意 item 打开 DetailDrawer 显示 metadata + markdown 内容

### 10.21 §2.1 Overview sparkline + recent runs timeline — ✅ 已修复

- PnLSparkline 组件：SVG 折线图展示最近 30 个 portfolio snapshots 的总 PnL 趋势
- RecentRunsTimeline 组件：最近 8 次 agent run 以 badge 色块展示（green/red/yellow = completed/error/running）

### 10.22 §2.1 Strategy Lifecycle promotion gate 检查结果 — ✅ 已修复

- 后端 `GET /api/strategies/{id}/gates` 暴露 check_promotion_gates 结果
- 前端 PromotionGates 组件：抽屉内展示 passed/failed 状态 + metrics 表格 + failures 列表
- （同时被 10.17 覆盖，此处明确标注）

### 10.23 §3.5 跨页跳转 — ✅ 已修复

- Proposals.tsx: strategy_id / opportunity_id 可点击，navigate 到 /strategies / /opportunities
- PaperTrades.tsx: proposal_id 可点击，navigate 到 /proposals；"View on Chart" 跳 /charts
- Opportunities.tsx: linked_strategy_id 可点击，navigate 到 /strategies
- Charts.tsx: marker 点击展示 trade detail

### 10.24 §2.4 性能指标展示 — ✅ 已修复

- Reports.tsx StrategyMetrics 组件展示 promotion gate metrics：paper_runtime_days / paper_trades / closed_positions / realized_pnl / max_drawdown_pct / max_consecutive_losses / critical_risk_violations / post_trade_reviews
- 注：Sharpe/Sortino 不在后端计算范围内（需要日收益率时间序列），但已有的 drawdown / win-loss 指标充分满足 §23 + §14.3 要求

---


---

## 5. ✅ Markets 页数据展示缺陷（2026-06-08，全部修复 2026-06-14）

> 来源：用户反馈 + 数据层 CCXT 迁移后的对照检查。
> **状态：4/4 全部已修复**（2026-06-14 确认）。以下保留历史记录供参考。

### 5.1 🟠 无数据来源标注（UI Honesty breach）

**现状**：`Charts.tsx:8` 硬编码 `SYMBOLS = ['BTCUSDT', ...]`，watchlist card 显示
`{sym.replace('USDT', '')}/USDT`，页面完全不显示数据来自哪个交易所 / provider。
数据层迁移到 CCXT Binance 后，显示的价格是 Binance 的，但界面无任何标注。

**影响**：trust §3 UI Honesty 破口——用户看到数字不知道它从哪来，无法判断数据可信度。

**最小修复**：
1. `Charts.tsx` 的 `SYMBOLS` 改为 canonical 格式 `['BTC/USDT', 'ETH/USDT', 'SOL/USDT']`，
   `getOhlcv` / `getMarketSnapshots` 传 canonical symbol（后端 `to_canonical()` 已支持）
2. watchlist card 右下角加数据来源 chip：`via Binance (CCXT)`，从 `/api/market/ohlcv`
   响应头或新增 `/api/market/status` 端点读取（后端已有 `provider` 字段在 `market_ohlcv`）
3. 如 snapshot 来自 Bitget fallback，显示不同颜色

**文件**：`Charts.tsx:8,64`, `api/client.ts:155`

---

### 5.2 🟡 Watchlist card 数据量不足

**现状**：每个 symbol card 只显示 3 个字段：`last_price` / `change_pct_24h%` / `volume_24h`。
缺少：24h High/Low（或 spread from current）、市场状态 badge（涨/跌/横盘）。

**当前 API**：`/api/market/snapshots` 返回 `{price, volume_24h, change_pct_24h}`。
CCXT 的 `fetch_ticker` 返回 `high`, `low`, `bid`, `ask`, `percentage` 等字段，但
`CCXTProvider.get_ticker()` 目前只提取了 3 个字段（`providers.py:Ticker` dataclass）。

**最小修复**：
- `aletheia/data/providers.py`：`Ticker` dataclass 加 `high_24h`, `low_24h` 字段（nullable）
- `aletheia/data/ccxt_provider.py`：`_fetch_ticker()` 填充这两个字段
- `aletheia/storage/models.py`：`MarketSnapshot` 加 `high_24h`, `low_24h`（若想持久化）
- `Charts.tsx`：card 显示 H/L 范围

**文件**：`aletheia/data/providers.py:17`, `aletheia/data/ccxt_provider.py:62`,
`Charts.tsx:68-75`

---

### 5.3 🟡 数据不自动刷新

**现状**：`Charts.tsx:17-18` 的所有 `useQuery` 均无 `refetchInterval`，数据只在
页面首次加载时拉取一次。对于价格行情，这等同于永久过期数据。

**最小修复**：
```tsx
// Charts.tsx
const { data: snapshots = [] } = useQuery({
  queryKey: ['market-snapshots'],
  queryFn: () => api.getMarketSnapshots(undefined, 20),
  refetchInterval: 30_000,  // 30s
})
```
chart OHLCV 本身不需要轮询（由 PriceChart 内部管理），只需刷新 watchlist ticker。
同时在 watchlist card 右上角加 `last updated X sec ago` 指示（参照 §4.2 H11）。

**文件**：`Charts.tsx:17-18`

---

### 5.4 🟡 symbol 格式未跟进 CCXT 迁移

**现状**：`Charts.tsx:8` 仍用 `'BTCUSDT'` Bitget 格式；API 端点 `getOhlcv` 传的也是
`BTCUSDT`。`routes/data.py` 的 `get_market_ohlcv` 默认参数也改成了 `BTC/USDT`
但 chart 组件还在传旧格式，依赖 `to_canonical()` 后端转换（有效，但前端不一致）。

**最小修复**：
- `Charts.tsx` `SYMBOLS` 改 `['BTC/USDT', 'ETH/USDT', 'SOL/USDT']`
- chart symbol 选择器 `<select>` option 用 display 名和 value 分离，display 用 `BTC/USDT`，value 传 canonical
- `getOhlcv` 调用已支持 canonical，无需后端改动

**文件**：`Charts.tsx:8,84-88`, `api/client.ts:155`

---

**整体修复优先级**：5.3（自动刷新，一行代码）> 5.4（symbol 格式）> 5.1（来源标注，需后端 Ticker 扩展）> 5.2（H/L 字段，需 dataclass + 存储改动）

---


---

## §4. ✅ OAuth Provider UI — 已修复（2026-06-14）

> OAuth subscription provider 前端审计中发现的改进项。全部已修复。

### 4.1 ✅ 模型下拉显示 tier / context window 提示

- 修复：`frontend/src/pages/Settings.tsx` OpenAI + Claude OAuth panel 的 `<select>`
  现在从 `health.model_metadata` 读取结构化信息，`<option>` 格式为
  `"gpt-4o — Tier 1 · 128K ctx"` / `"claude-sonnet-4-20250514 — Tier 2 · 200K ctx"`
- 当 model_metadata 为空时 fallback 到裸 slug 列表或手动输入

### 4.2 ✅ OAuth panel 有 disconnect 操作

- 后端：`POST /model/openai-oauth/disconnect` + `POST /model/claude-oauth/disconnect`
  清除 oauth_token_ref / oauth_refresh_ref，重置 auth_type 为 api_key，写 audit 事件
- 前端：connected 状态下 Disconnect 按钮（danger 样式），confirm() 确认后调用 API
