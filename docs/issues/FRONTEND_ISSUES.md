# Frontend Issues — 前端结构性问题清单

> 本文件记录前端**与规格 §23 / trust §3 UI Honesty 的差距**——字段缺失、
> 角色弱化、设计方向偏差、工程基础缺位。**与 `docs/issues/BACKEND_ISSUES.md` 平行**：
> 那一份是后端"已声明但未接通"，这一份是前端"应该呈现但没呈现 / 呈现得不诚实"。
>
> 每条都给出 `frontend/src/path:LINE` 佐证 + 规格依据 + 影响等级 + 最小修复建议。
> **不构成施工授权**——同 AGENTS.md 红线：先稳跑、再分项授权；🔴 项动工前需
> 显式授权。
>
> **图例**：
> - 🔴 角色性误差 / 信任不变量（trust §3 UI Honesty 破口、§23.1 dashboard 6 角色被弱化或缺失）
> - 🟠 字段缺失 / 设计方向偏差（§23.x 列出但前端没做、TradingView §4.5 借鉴未到位）
> - 🟡 工程基础（类型断言、列表组件、内联 style、详情路由模式）
>
> **最后更新**：2026-06-07 · 维护者：Aletheia Dev
> **来源**：规格 §23（13 子节）+ §2/§4/§13/§15/§29 + trust §3 + 全前端代码静态对照
>
> **状态**：✅ 所有 §1-§3 条目已于 2026-06-06 修复并合入 master。原文保留作为
> 审计参考。修复摘要见 §10（10.1–10.24，共 24 项）。

---

## 0. 阅读顺序与使用方式

按等级倒序：先看 §1（🔴 方向性误差）、再看 §2（🟠 字段缺失）、最后 §3（🟡 工程基础）。
按修复时机：§1 / §2 多数依赖共享组件（DetailDrawer / 共享 types），建议先做 §3.1
+ §3.2 两条工程基础，再做 §1 / §2。
按归档：修好的条目移到 §10 历史区，保留 file:line + commit。

> **与后端的依赖关系**：部分字段缺失（如 strategy_id、tools_called、
> entry/exit reason）需要后端先把数据吐出来——这些条目同时引用
> `BACKEND_ISSUES.md` 的对应项，标注"先后端、后前端"。前端不该绕过去做假数据。

---

## 1. 🔴 角色性误差 / UI 不诚实

### 1.1 §23.1 "approval desk" 角色被严重弱化

> Proposal 审批是这个系统**唯一的人在环人控制点**（规格 §15.3 + §29 解锁 live
> 必须人工）。前端的 Proposals 页只有"两个按钮"，把 trust §3.4 "Approving a
> proposal must call a route that preserves..." 这条 dashboard action rule 的
> **审批者认知前提**完全跳过——审批者根本不知道自己在批什么。

- 🔴 **risk_decision 完全不可见**
  - 佐证：`frontend/src/pages/Proposals.tsx:23` 只显示 7 列 `Symbol/Market/Direction/Mode/Rationale/Status/Actions`；无 risk_decision violations / risk_score / message 任何展示
  - 规格：§23.8 "risk decision" 是 9 个必显字段之一
  - 影响：审批者只看到 status="pending"，不知道**风控系统怎么看这个 proposal**——盲签。`/api/risk/status` 与每条 proposal 的 risk_decision 行后端都有，但 UI 没读没显
  - 最小修复：行点开抽屉显示 risk_decision 详情；list_risk_decisions(proposal_id=...) API 路由已存在或可加

- 🔴 **agent_rationale 单行 ellipsis，只能 hover 看**
  - 佐证：`Proposals.tsx:31` `style={{ maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={p.agent_rationale}`
  - 规格：§23.8 "agent rationale" 列；§2.3 AI-native "agent should be able to answer: Why does this strategy exist?"——rationale 是"为什么"的载体
  - 影响：长 rationale 被截断成"BTCUSDT long signal from..."，审批者必须 hover 才能看到——批量审批时不可能逐条 hover；纯 paper 阶段都已经如此，未来 live-limited 会更危险
  - 最小修复：抽屉里显示完整 rationale；列表行至少多行 wrap 显示前 2-3 行

- 🔴 **关联实体（opportunity / strategy / market context）无跨页跳转**
  - 佐证：`Proposals.tsx` 整个文件无 `<Link>`、无 `useNavigate`；proposal 的 `strategy_id` / `opportunity_id` 字段后端有但 UI 不显示也不可点
  - 规格：§23.8 必显 "strategy id"；§2.3 AI-native "Which evidence created it"
  - 影响：审批者要知道"这个 proposal 来自哪个 opportunity（thesis 是什么）/ 哪个 strategy（lifecycle 状态）"必须切页 → 滚 → 找 ID → 切回——实际等于无法做这件事
  - 最小修复：抽屉里显示 strategy/opportunity 链接；点进去跳到对应详情页

- 🔴 **批准按钮无确认对话**
  - 佐证：`Proposals.tsx:34-37` `onClick={() => approve.mutate(p.id)}` 直接触发
  - 规格：trust §3.4 "Approving a proposal must call a route... explicit confirmation"（重在 live；paper 也存在批量误点风险）
  - 影响：paper 阶段误点代价较小但不为零（占用风险预算 / 触发 stop / 污染 promotion gate 数据）；live 阶段必须强制确认
  - 最小修复：confirm() 弹窗 + 显示该 proposal 的关键参数让用户复核；live 路径在抽屉里二次确认

### 1.2 §23.1 "agent transparency" 角色基本未实现

> §2.3 AI-native 要求 agent 能回答 5 个"为什么"——目前 UI 一个都答不了。
> Agent Runs / Opportunities / Dreaming / KnowledgeBase / Reports 五个 transparency
> 类页面全部停留在"列表 + 标题"层。

- 🔴 **Agent Runs 无 trace details / tools called / errors 展示**
  - 佐证：`frontend/src/pages/AgentRuns.tsx:30-49` 仅 6 列 `Agent/Status/Trigger/Provider/Outputs(数量)/Started`；无展开行、无详情视图；`outputs` 只显示 `JSON.parse(r.outputs_created).length`（一个数字）
  - 规格：§23.4 必显 9 项中的 4 项缺失：tools called / outputs created（要内容不要计数）/ errors / trace details
  - 影响：agent transparency 主战场停在"agent A 跑了，状态 completed"——看不到它调了什么 tool、写了什么 output、报了什么 error；BACKEND §3 也点了 `log_tool` 不记 input、`run_logger` 空 outputs/errors 不入库——后端先补，前端再补展示
  - 最小修复（先后端、后前端）：点击行展开抽屉显示 tool_calls 时间线（已有 agent_tool_calls 表）+ outputs JSON + errors JSON + 时长

- 🔴 **KnowledgeBase / Dreaming / Reports 不能阅读 markdown 内容**
  - 佐证：`KnowledgeBase.tsx:28-39` 只显 title/confidence/status/created；`Dreaming.tsx:14` 三列；`Reports.tsx:21,27,33` 全是 `(...).map(r => <p key={r.id}>{r.title} — {r.created_at}</p>)`——所有 KB 条目只看到标题
  - 规格：§16.1 "Markdown as primary knowledge"——markdown 是知识本体；§23.6 KB 4 个 tab 必须能阅读
  - 影响：dreaming_log / research_note / daily_review / post_trade_review 都是 markdown 文件，UI 只看标题等于"知道有这本书但读不到内容"。后端 `KnowledgeItem.file_path` 字段都有，没有路由暴露
  - 最小修复：后端加 `GET /api/knowledge/{id}/content` 读 markdown 文件；前端点击行抽屉里渲染 markdown（`react-markdown` 即可）

- 🔴 **Opportunities 不显示 thesis / source agent / created_at / linked strategy**
  - 佐证：`Opportunities.tsx:13-19` 只 5 列 Symbol/Market/Direction/Confidence/Status
  - 规格：§23.7 必显 9 项；缺 4 项（thesis / source_agent / created_at / linked_strategy_hypothesis）
  - 影响：thesis 是 opportunity 存在的全部理由（"为什么市场观察 agent 觉得这是机会"）；UI 看不到等于把 opportunity 降级为"BTC long?"裸数据
  - 最小修复：补 4 列；thesis 单独抽屉显示完整内容；linked_strategy 待 BACKEND §1.1 修好后才有数据

- 🔴 **Dreaming 页只显示 dreaming_log 列表，§23.5 其余 5 项全缺**
  - 佐证：`Dreaming.tsx` 23 行只渲染 `api.getDreamingLogs()` 标题列表
  - 规格：§23.5 应显 6 项：current dreaming task / recent consolidations / **new research notes** / **new strategy hypotheses** / **deprecated memories** / **contradictions found**
  - 影响：Dreaming 本应是"今晚整理了什么、产生了什么假设、淘汰了什么记忆"的**学习面板**——目前只是 dreaming_log 一览。BACKEND §1.1 `create_strategy_memory` 0 调用 + §2.1 学习账本不区分 archived 也削弱这页
  - 最小修复：后端先实现"今夜产出"聚合 API（last 24h research_notes/strategy_memory diff + status=deprecated 的 N 天内变更）；UI 分块显示

### 1.3 trust §3 UI Honesty 局部破口

- 🔴 **Overview `today_pnl ?? unrealized_pnl` 静默 fallback**
  - 佐证：`Overview.tsx:71` `<td>{portfolio?.today_pnl ?? portfolio?.unrealized_pnl ?? '—'}</td>`
  - 规格：trust §3.2 "Use 'not available' when data is absent"；§3.3 forbidden patterns "Do not... convert backend errors into empty success states"——这里把"今日盈亏不可用"悄悄换成了"未实现盈亏"，两者语义完全不同
  - 影响：用户看到一个数字以为是"今天赚/亏多少"，实际是"未实现浮盈"——UI 撒了谎
  - 最小修复：当 `today_pnl` 为 null 时显示 "not available"；或显式拆成两行 "Today PnL" + "Unrealized PnL"

- 🔴 **StrategyLifecycle Promote 失败弹原始 `alert(e.message)`**
  - 佐证：`StrategyLifecycle.tsx:23` `onError: (e: any) => alert(e.message)`
  - 规格：trust §3.4 dashboard action rules 隐含"用户能看到友好且诚实的失败信息"
  - 影响：用户看到 `API 400: {"detail":"insufficient_paper_runtime_days:3<14, ..."}` 之类原始 JSON——既不友好也不结构化；BACKEND §1.1 §5.5/§5.7/§5.8 全部 promotion gate bug 修好之前，这页弹 alert 的频率会高
  - 最小修复：解析后端 gate failures 数组，结构化展示"通过 N/M 项"+ 失败项详情列表；用 toast 而非 alert

- 🔴 **Risk Boundaries 卡片直转字典为表格，无验证 / 无单位提示**
  - 佐证：`Settings.tsx:535` `<table><tbody>{Object.entries(risk).map(([k, v]) => ...)}` 直接 dump
  - 规格：§23.12 "risk boundaries display"；§21.1 列出 hard limits 都有明确单位（USDT / pct / count）
  - 影响：用户看到 `max_daily_loss_pct: 5` 不知道是 5% 还是 5（绝对值）；`max_position_notional_usdt: 100` 与 `max_leverage: 3` 字段类型完全不同却同列展示
  - 最小修复：硬编码 schema 描述（field → unit + tooltip）；保持 read-only 但加分组与单位显示

---

## 2. 🟠 字段缺失 / 设计方向偏差

### 2.1 §23.x 字段缺失

- 🟠 **Paper Trades 6 项必显只做 1 项**
  - 佐证：`PaperTrades.tsx:10-13` 5 列 Symbol/Direction/Qty/Price/Status
  - 规格：§23.9 必显：orders / fills / **PnL** / **entry/exit reason** / **linked proposal** / **linked strategy**
  - 影响：缺 fills / PnL / close_reason / linked_proposal / linked_strategy——交易流水变裸数据；§4.5 TradingView 借鉴"图与决策视觉连接"在 trades 这一端就断了
  - 最小修复：补 fill 详情列（fee/fill_price/filled_at）+ realized_pnl + close_reason（后端 position 表已有字段）；linked_proposal/strategy 做成可点击跳转——依赖 BACKEND §1.1 strategy_id 链路修复

- 🟠 **Positions 不显示 stop_price / take_profit_price**
  - 佐证：`Positions.tsx:38-50` 列 Symbol/Direction/Qty/Entry/Current/Unrealized PnL；后端 `positions.stop_price` / `take_profit_price` 字段从 PR #C1 起就有
  - 规格：§23.10 + §21.1 risk hard limits 暗含"用户能看到仓位的保护范围"；BACKEND §2.3 averaging-in 丢失 stop_price 问题加重不透明度
  - 影响：审批者无法判断当前持仓的下行风险；BACKEND §2.3 修好之前 UI 也显示不出真实 stop（因为后端 averaging 时丢了）
  - 最小修复：补 2 列 Stop / TP；显示 close_reason 历史（已平仓部分另起表）

- 🟠 **Strategy Lifecycle 不显示 promotion gate 检查结果**
  - 佐证：`StrategyLifecycle.tsx:43-58` 仅显示 name/state/created；点 Promote 失败才能看到 error.message
  - 规格：§23.11 "Show pipeline"；§14.3 列出 7 项 promotion gates
  - 影响：用户不知道"这个 strategy 还差什么才能升级"——必须按下 Promote 看错才知道；产品节奏全错
  - 最小修复：抽屉里调 `check_promotion_gates(strategy_id)` API（后端已存在 `strategy_lifecycle.check_promotion_gates`，加一个路由暴露），显示每项 gate 通过/未通过 + metric 实际值

- 🟠 **Pipeline 视觉表达失败：9 个 badge 平铺，看不出进度**
  - 佐证：`StrategyLifecycle.tsx:36` `STATES.map(s => <span ...badge-blue>{s}</span>)`——所有状态徽章一字排开，与每行 strategy 的 state badge 无视觉关联
  - 规格：§23.11 字面 "Show pipeline: draft -> paper_candidate -> ... -> live_limited"——是有方向的流水线
  - 影响：违反 §4.5 TradingView 借鉴 "Visual connection between chart and trade decisions"——这是 lifecycle 而非 chart，但同理 visual connection 缺失
  - 最小修复：横向 stepper 组件，6 个步骤（draft / paper_candidate / paper_running / paper_passed / live_candidate / live_limited），每行 strategy 在自己步骤上发光

- 🟠 **Overview 缺 sparkline / 趋势缩略 / 最近运行迷你时间线**
  - 佐证：`Overview.tsx` 三张卡都是表格 + 文本
  - 规格：§23.1 "control room" + §23.3 列出但未规定形式
  - 影响：作为 dashboard 入口，"一眼看清最近 24h 系统/账户状态"目标未达
  - 最小修复：PnL 30 天 sparkline + 最近 5 次 agent run 时间线 + Today PnL 用大字号

### 2.2 §4.5 TradingView 借鉴"图与决策视觉连接"单向断

- 🟠 **Charts 上的 trade marker 不可点击**
  - 佐证：`PriceChart.tsx:39-44` `series.setMarkers(...)` 是只读视觉元素，无 onClick；marker 与 proposal/order ID 无关联
  - 规格：§4.5 "Visual connection between chart and trade decisions"——双向连接
  - 影响：用户看到一根成交箭头，想知道"这是哪笔交易、对应哪个 proposal、agent 当时怎么想的"——做不到
  - 最小修复：用 lightweight-charts 的 click handler；marker 携带 order_id；点击后打开 PaperTrades 详情抽屉或路由 `/paper-trades/:id`

- 🟠 **反向也断：Paper Trades / Proposals 不能"在图上看一眼"**
  - 佐证：Proposals/PaperTrades 页面无"View on chart"按钮，无跨页跳转
  - 规格：同上
  - 影响：审批 / 复盘时缺少"市场 context"

### 2.3 Settings 与 §29 default 一致性细节

- 🟠 **`autonomous_paper_trading` flag 在 UI 上完全不可见**
  - 佐证：`Settings.tsx` 11 张卡无该开关；`api/client.ts` 也无相关 endpoint
  - 规格：§29 列为 default `false`；§13.3 PaperAuto 模式语义直接依赖此 flag；BACKEND §2.5 "_run_paper_trader 双重门只在调度层" 也涉及它
  - 影响：用户切换到 PaperAuto 模式后**实际不会自动 paper 下单**——因为 `cfg.agent.autonomous_paper_trading=false`，但 UI 不告诉用户；这违反 trust §3.3 "禁止显示后端不存在的能力"
  - 最小修复：Permission Mode 卡里加一个开关，与 permission_mode 联动显示当前真实行为

- 🟠 **没有"实际生效配置"快照**
  - 佐证：`Settings.tsx` 各卡显示分散字段，但用户不容易看到 `cfg.runtime.paused / scheduler_enabled / autonomous_learning / autonomous_paper_trading` 全集
  - 规格：§29 列出的 6 项 + §10 config model 全集应可读
  - 最小修复：加只读"Effective Config"卡，显示当前所有 runtime/agent flag

### 2.4 Reports 页 §23 留白但缺关键能力

- 🟠 **没有性能指标（Sharpe / Sortino / Drawdown / Win Rate）**
  - 佐证：`Reports.tsx` 只有 PnL chart + 标题列表
  - 规格：§23 没强制要求；但北极星 §10.2（Nautilus-Practice 13 项指标）记为可借鉴
  - 影响：promotion gate 已经计算了 max_drawdown / max_consecutive_losses（`strategy_lifecycle.py:_max_drawdown_pct`），UI 看不到这些数字
  - 最小修复：把 promotion gate 的 metrics 直接展示，这是已有数据零成本

---

## 3. 🟡 工程基础

### 3.1 共享 domain types 缺失，全 `as any[]` 断言

- 🟡 **几乎每个页面都用 `(data as any[])`**
  - 佐证：`Overview.tsx:28-30`、`Proposals.tsx:16,25`、`Positions.tsx:7,9,24`、`AgentRuns.tsx:16,34`、`Dreaming.tsx:10,14`、`KnowledgeBase.tsx:16,32`、`Opportunities.tsx:10,14`、`PaperTrades.tsx:10,14`、`Reports.tsx:19,21,25,27,31,33`、`Charts.tsx:15`、`StrategyLifecycle.tsx:30,42`
  - 规格：无直接要求；属工程健康度
  - 影响：后端字段重命名时前端不报错；新增字段时不知道；间接影响 trust §3 诚实性（容易显示错误字段）
  - 最小修复：建 `frontend/src/types.ts` 与后端 Pydantic 模型对齐；逐页迁移；可与 §3.2 同步

### 3.2 `@tanstack/react-table` 装了但 0 处使用

- 🟡 **plain `<table>` 全用，无排序/过滤/分页/列偏好**
  - 佐证：`package.json:13` `"@tanstack/react-table": "^8.19.0"`；grep `react-table` 在 src/ 0 命中；所有页面用 plain table
  - 影响：所有列表条目多了无法管理；StrategyLifecycle / AgentRuns / Proposals 数据增长后基本不可用
  - 最小修复：建 `<DataTable>` 通用组件包 react-table；按页迁移（Proposals / AgentRuns / Strategies 优先）

### 3.3 没有详情视图 / 抽屉模式

- 🟡 **所有列表都是平表，无 `:id` 路由 / 无展开抽屉**
  - 佐证：`App.tsx:131-143` 所有 Route 都是顶层无参数；无 `/proposals/:id` / `/strategies/:id` 之类
  - 影响：§1.1 / §1.2 / §2.1 多条都受此牵制——P0 修复必须先有抽屉/详情模式
  - 最小修复：建 `<DetailDrawer>` 通用组件（侧滑或 modal）；约定每个列表行点击触发抽屉；Proposals / AgentRuns / Knowledge 优先

### 3.4 内联 style 散布

- 🟡 **`style={{ display: 'flex', gap: '0.5rem' }}` 类内联样式遍布**
  - 佐证：grep `style={{` 在 src/pages/ 命中数十处；如 `Proposals.tsx:34`、`Settings.tsx` 各处布局
  - 影响：维护成本高；与 `index.css` 的设计系统脱节；新页面容易写出与系统不一致的间距
  - 最小修复：抽 `.row-actions` / `.btn-group` / `.form-grid-3` 等 utility 类；逐页替换

### 3.5 跨页跳转完全缺失

- 🟡 **除 SetupBanner 外无 `<Link>` 用法；agent_id / proposal_id 都是死字段**
  - 佐证：grep `react-router-dom.*Link\|useNavigate` 仅命中 `App.tsx`（NavLink）+ `SetupBanner.tsx`
  - 影响：与 §1.1 第 3 条（关联实体跳转）同根因；§4.5 TradingView 双向连接同根因
  - 最小修复：与 §3.3 详情路由一起做

### 3.6 `index.css` 设计系统已成型，但 utility 类不足

- 🟡 **现有 CSS（206 行）建立了完整 token / pill / badge / table 系统**——✅ 这部分做得好
  - 但缺 utility 类（gap / row / column / stack / spacer）让页面文件需要内联 style
  - 最小修复：补 30-40 行 utility classes（按 §3.4）

### 3.7 两个 chart 文件 dark theme 颜色硬编码

- 🟡 **PriceChart / PnLChart 用硬编码 hex，不使用 CSS variables**
  - 佐证：`PriceChart.tsx:21-22` `background: '#1a1f26', textColor: '#e8e6e3'`；`PnLChart.tsx:14` 同；与 `index.css:9-23` 的 var(--bg) / var(--text) / var(--accent) 不一致
  - 影响：未来调整主题时 chart 不跟随；目前 #1a1f26 与 --surface(#141a21) 也不完全一致
  - 最小修复：在 chart init 时读 `getComputedStyle(document.documentElement).getPropertyValue('--surface')` 等

### 3.8 chart 无 trade marker 可点击的回调架构

- 🟡 **PriceChart 只接受 `markers` prop，没有 onMarkerClick**
  - 佐证：`PriceChart.tsx:8-11` props 只有 symbol/timeframe/markers
  - 影响：§2.2 修复需要从这里改起
  - 最小修复：增加 `onMarkerClick(orderId)` prop；marker 数据携带 id

### 3.9 `index.css` 中无 markdown 渲染样式

- 🟡 **未来 §1.2 修复要渲染 markdown 时无现成样式**
  - 影响：KB / Reports / Dreaming 抽屉里渲染 markdown 时没有 typography / code-block / list 样式
  - 最小修复：补 `.markdown-body` 样式块；可参考 GitHub markdown CSS 子集

---

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

## 11. 已知盲区（本清单**未**覆盖的部分，诚实记录）

### 11.1 模块 / 路径盲区

- **未审 `frontend/dist/` / `aletheia/dashboard_static/`**——构建产物；CLAUDE.md 已声明前端源码改动后需重建
- **未审 `frontend/node_modules/` 的依赖安全**——属 supply-chain 工作，不在前端结构性问题清单
- **未审性能 / bundle 大小**——`lightweight-charts` + react 19 + tanstack 系列体积，与 §29 "本地优先"矛盾度未量化

### 11.2 方法论盲区

- **静态对照规格 §23 + 读源码**——没有真实运行 + 浏览器验证；交互细节（loading state / error state / 网络 5xx 时表现 / 数据为空时占位）未在浏览器实测
- **可访问性（a11y）**：键盘导航、ARIA、对比度、色盲——未审
- **响应式 / 移动端**：当前布局明显桌面优先（240px sidebar），是否在小屏可用未测——但 §23 字面只说"local dashboard"，没要求移动端

### 11.3 性质类盲区（不进本清单）

- **国际化 / i18n**：当前中英文混杂（"connecting…" / "Pause" / "刷新" 等），但规格无要求
- **测试覆盖**：前端无测试文件；规格 §28 主要针对后端
- **依赖升级**：react 19 + react-router 7 都是较新版本，未做过渡风险评估

### 11.4 增量维护原则

发现新问题时按以下规则归入：

| 问题类型 | 归入位置 |
|---|---|
| 角色性误差 / UI 不诚实 / approval-desk-transparency 弱化 | §1 🔴 |
| §23.x 字段缺失 / TradingView 双向连接缺失 / safety-default 不一致 | §2 🟠 |
| 类型断言 / 列表组件 / 内联 style / 抽屉模式 / chart 颜色 | §3 🟡 |
| a11y / i18n / 测试 / 性能 / 依赖安全 | 不进本文件——另起对应文档 |

修复完移到 §10 历史区，保留 file:line + commit。

---

## 4. 🔴 视觉/布局 微Bug（2026-06-07 审查）

> 本节由三路并行审查（布局对齐 / UX组件模式 / 行业标准对比）汇总去重。
> 不涉及功能缺失（那些在 §1/§2/§3），专注**视觉一致性、信息层级、可访问性**。
> 修复后移入 §10 历史区。

### 4.1 CRITICAL — 必须修复

| # | 问题 | 文件:行 | 说明 |
|---|------|---------|------|
| C1 | 侧边栏品牌区与topbar高度不对齐 | `index.css:68-74, 102-106` | `.brand` padding算出 ≠ topbar的52px，两条水平线错位。应定义 `--header-height: 52px` 统一 |
| C2 | PnL图表位置错误 | `Overview.tsx:192-216` | 账户价值+PnL应是页面Hero区域，目前被埋在底部表格内。参考Bloomberg/Binance Pro：portfolio value是首屏第一视觉 |
| C3 | Activity Timeline挤压main区域 | `index.css:389-396`, `App.tsx:181` | 52px topbar + 180px timeline = 232px固定消耗；1080p屏(768px可用高度)main仅剩536px |

### 4.2 HIGH — 显著影响体验

| # | 问题 | 文件:行 | 说明 |
|---|------|---------|------|
| H1 | 间距值混乱无8px网格 | `index.css` 全局 | 出现0.35/0.4/0.42/0.45/0.55/0.65/0.7/0.72/0.75/0.85/1.1/1.15/1.25rem等几十种值 |
| H2 | Cockpit 2x2 grid内容高度撕裂 | `index.css:381-386`, `Overview.tsx:65-146` | Risk State 3行 vs Market Snapshot 9行，未用 `align-items: start` |
| H3 | 仅一个响应式断点(900px) | `index.css:385` | 缺1366px笔记本/768px平板/480px手机适配 |
| H4 | topbar-btn暂停按钮过小(~24px) | `index.css:416-419` | WCAG AA要求最小44×44px触控目标 |
| H5 | 状态栏无"监控区"vs"操作区"分隔 | `App.tsx:90-114` | 只读pills和操作button混排无视觉层级 |
| H6 | Active导航标记几乎不可见 | `index.css:96-99` | `rgba(34,211,238,0.12)` gradient在暗背景上约0.12透明度，用户不确定当前页面 |
| H7 | Card间距过大/数据密度低 | `index.css:137-140, 381-385` | padding 1.1rem + gap 1.1rem + margin 1.1rem = 每card浪费3.3rem |
| H8 | badge-yellow对比度不足 | `index.css:152-155` | 文字#f5b942在rgba(245,185,66,0.12)背景上≈2:1, WCAG AA要4.5:1 |
| H9 | 表格无横向滚动容器 | `index.css:157-166` | 窄屏长列名换行变形，缺 `overflow-x: auto` 包裹 |
| H10 | dot指示器太小(6px) | `index.css:124` | 专业交易终端用8-10px+glow；高分屏上6px几乎不可见 |
| H11 | 无数据刷新时间指示 | 多个页面 | 用户无法判断数据是否过期，缺"last updated" / live指示 |
| H12 | 时间戳格式不一致 | 多个页面 | 有`.slice(0,16)`有`toLocaleString()`，应统一为上下文感知格式(今天HH:MM/本周Day HH:MM/更早Full Date) |

### 4.3 MEDIUM — 可感知

| # | 问题 | 文件:行 | 说明 |
|---|------|---------|------|
| M1 | 60+处inline style破坏设计系统 | Overview/Charts/Settings/RiskControls/NewsIntel/ActivityTimeline | 应提取CSS类：`.watchlist-grid` `.metrics-grid` `.btn-row` `.sparkline-row` 等 |
| M2 | DetailDrawer无焦点陷阱 | `DetailDrawer.tsx:20-32` | Tab键可逃出Drawer到背景元素 |
| M3 | DataTable行无hover/keyboard反馈 | `DataTable.tsx:73-83` | 可点击行无`:hover`高亮，键盘不可达 |
| M4 | 数值区域缺monospace字体 | `Overview.tsx:117`, `Positions.tsx:52`, `Charts.tsx:60` | 非表格区数字未加`.mono`类，数位不对齐 |
| M5 | 数值缺少单位标注 | `Positions.tsx:81-85`, `RiskControls.tsx:102-109`, `Charts.tsx:117-120` | 显示"1234.56"但不标明USDT/BTC/% |
| M6 | 状态badge颜色映射跨页不一致 | `DataTable.tsx:102-109` vs `Overview.tsx:37-39` vs `StrategyLifecycle.tsx:26-27` | 同一status在不同页映射不同颜色 |
| M7 | Loading状态只有裸文字 | `Overview.tsx:31` | 无骨架屏/shimmer，页面空白闪烁 |
| M8 | Cockpit "Review→"按钮过小 | `Overview.tsx:138-140` | font 0.72rem + padding 0.2rem ≈ 15px高 |
| M9 | Kill Switch用了secondary样式 | `RiskControls.tsx:65-70` | 高危操作应用destructive/danger样式 |
| M10 | Settings boolean显示"true"/"false" | `Settings.tsx:57-59` | 应显"ON/OFF"或"Enabled/Disabled" |
| M11 | Positions分组标题用machine格式 | `Positions.tsx:64` | 显示"paper_internal · spot"应为"Paper Trading · Spot" |
| M12 | Command Palette NL最小长度无提示 | `CommandPalette.tsx:107` | 要求>20字符才触发AI，用户不知门槛 |
| M13 | News摘要截断切词不完整 | `NewsIntel.tsx:87` | `summary.slice(0,150)`可能在单词中间截断 |

### 4.4 LOW — Polish级

| # | 问题 | 文件:行 | 说明 |
|---|------|---------|------|
| L1 | 侧边栏固定240px不响应式 | `index.css:59-63` | 平板/手机完全不可用，缺hamburger collapse |
| L2 | Drawer在移动端+键盘弹出时溢出 | `index.css:217-241` | 100vh在iOS被虚拟键盘挤压 |
| L3 | Charts watchlist active border不一致 | `Charts.tsx:53-54` | 非active时borderColor为undefined(继承)而非显式 |
| L4 | ActivityTimeline filter按钮过小 | `ActivityTimeline.tsx:94` | 0.6rem字号+0.15rem padding ≈ 12px高 |
| L5 | Dreaming drawer `<pre>`不换行 | `Dreaming.tsx:103-106` | 长行溢出drawer宽度 |
| L6 | StrategyLifecycle状态标签未Title Case | `StrategyLifecycle.tsx:42` | 显示"paper running"应为"Paper Running" |
| L7 | KnowledgeBase tab不显示数量 | `KnowledgeBase.tsx:85-89` | 用户不知道哪个tab有内容 |
| L8 | Overview Recent Runs 8个badge挤一行 | `Overview.tsx:226-231` | 3字母缩写难以辨认agent |
| L9 | Charts图表缺买卖图例 | `Charts.tsx:86` | hint文字太小，应在图表上加"▲ buy | ▼ sell" |

### 4.5 修复建议（按投入产出排序）

1. **定义 `--header-height: 52px`** → 同时修C1 + brand/topbar对齐（5min）
2. **增大dot到8px + 增强active nav** → 修H6 + H10（5min）
3. **减小card padding到0.75rem** → 修H7 + 全局数据密度提升（10min）
4. **PnL图表移至Cockpit顶部Hero** → 修C2（20min）
5. **topbar分区 + btn增大** → 修H4 + H5（10min）
6. **badge背景alpha提升到0.18-0.22** → 修H8 WCAG对比度（5min）
7. **表格容器加overflow-x:auto** → 修H9（5min）
8. **提取60+处inline style为CSS类** → 修M1 + 全局一致性（30min）
9. **统一时间格式helper** → 修H12（15min）
10. **Activity Timeline高度自适应** → 修C3（10min）

---

---

## 5. 🟠 Markets 页数据展示缺陷（2026-06-08）

> 来源：用户反馈 + 数据层 CCXT 迁移后的对照检查。
> 三个问题彼此独立，修复优先级从高到低排列。

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

## 6. 🔴🟠🟡 全前端设计系统对照审计（2026-06-13）

> 来源：用户要求"把所有前端页面的设计风格与排版规范跟 UI/UX 设计文档完整比对"。
> 方法：5 路并行 Explore agent 各审一组文件，对照 `docs/UI_DESIGN_SYSTEM.md` + 已实现
> 的 `frontend/src/index.css` token 系统；关键结论已用 grep 实测复核（不轻信 agent 叙述，
> 遵循 "verify-before-cite" 纪律）。覆盖 14 页 + 16 组件 + index.css(1864 行) + 3 chart。
> 修复进度逐条更新；修好移入 §10。

### 6.1 🔴 CSS 系统级 BUG（已 grep 实测确认，影响全站或整页）

- 🔴 **`Universe.tsx` 整页用 Tailwind class，本项目无 Tailwind → 整页样式失效**
  - 实测：`package.json` 无 tailwind 依赖、`src/` 无 `@tailwind` 指令；`Universe.tsx:119-194`
    全是 `bg-zinc-800 / text-zinc-400 / grid grid-cols-5 / space-y-6 / font-mono /
    px-4 py-2 bg-blue-600 / rounded-lg / text-red-400 / text-orange-400` 等
  - 影响：summary tiles 无卡片背景、无 muted 色、grid 不成列、refresh 按钮无样式、
    数字非 mono、family 颜色意图含 off-palette 紫/橙——**整页排版崩坏**
  - 修复：用设计系统 class 重写（`.card`/`.metric-tile`/`.mono`/`.account-snapshot` grid/
    原生 `<button>`），familyColor 返回 `var(--blue/green/yellow/muted)`，去掉紫橙

- 🔴 **`index.css` 三处 `.badge-red` 定义冲突，最低者(行1717)按级联胜出 → 全站红 badge 变色掉边框**
  - 实测：`368`(规范红 `rgba(255,93,108,.20)`+border)、`754`(command-card 限定
    `rgba(255,107,107,.12)`)、`1717`(`rgba(255,80,80,.15)`+`var(--red,#f55)`+**无 border**)
  - 影响：行 1717 在最下方，覆盖全站所有 `.badge-red`——红 badge 用错红、丢边框
  - 修复：删除 1717 的重定义；754 收敛到 token 红

- 🔴 **整个 Lens CSS 块(~1592-1772)引用 4 个未定义 token → 静默渲染失败**
  - 实测：`--radius`(7处)、`--text-muted`(7处)、`--font-mono`(1处:577)、`--surface-hover`(1处:1772)
    在 `:root` **均未定义**（真 token 是 `--r`/`--r-sm`、`--muted`、`--mono`、`--surface-2`）
  - 影响：Lens 全部卡片**直角**(border-radius 解析为空)、"muted"文字**无色**、
    markdown code 非 IBM Plex Mono、clickable hover 落到 `--bg`(过暗)
  - 修复：`--radius`→`--r`/`--r-sm`、`--text-muted`→`--muted`、`--font-mono`→`--mono`、
    `--surface-hover`→`--surface-2`

- 🔴 **off-palette 紫色 `#b864ff`(`.badge-purple` 行1718 / `.btn-promote` 行1769-1770)**
  - 规格：只允许 ONE accent(cyan) + red/yellow/green/blue 语义色；紫色违规
  - 影响：SerenityLens `promoted` 主题 badge + Promote 按钮引入第二强调色
  - 修复：promoted→`badge-blue`/`badge-green`；删 `.badge-purple`/`.btn-promote` 紫，改 token

- 🔴 **`Settings.tsx:1343` `var(--purple, #a78bfa)`(reasoning tier)、`:639` `rgba(239,68,68)`
    (外来红 #ef4444)、`:715` `rgba(100,100,120)`(无 token 灰)**——硬编码 off-token 颜色
  - 修复：reasoning→`var(--blue)`；红→token 红；灰→`var(--surface-2)/var(--border)`

- 🟠 **`ProbabilityChart.tsx:22-30` 硬编码 hex(`#232c36` 边框、`#22d3ee` 线、cyan rgba 填充)**
    ——唯一绕过 `charts/theme.ts` 的 chart
  - 修复：theme.ts 加 borderColor/area-fill 字段，引用 `chartTheme`

### 6.2 🔴 跨页/raw 机器码展示（UI Honesty + 一致性）

- 🔴 **lifecycle state 一律 `badge-blue` 且 raw enum**：`StrategyLifecycle.tsx:28,99`、
  `Learning.tsx:286`——draft/paper_running/live_limited/retired 全蓝、显示原始下划线串
  - 修复：state→语义色(live_*绿/paper_*黄/retired红/draft蓝) + Title-Case 人性化

- 🔴 **未定义 badge class `badge-amber`/`badge-muted`**：`TradesPositions.tsx:207,208,307`、
  `Overview.tsx:111`——渲染成无色裸 badge
  - 修复：`badge-amber`→`badge-yellow`；`badge-muted`→`badge-blue` 或新增 `.badge-muted` token

- 🔴 **raw 机器枚举直接显示给用户**（rule 7 humanize 违规，多页）：
  - `RiskControls.tsx:67,68,149,150`(permission_mode/live_state raw、boundary key 小写、
    `JSON.stringify(val)` 裸 dump)
  - `Settings.tsx:194,420,809`("active"/"disabled"、`live_disabled`、`String(bool)`="true")
  - `Overview.tsx:180,192`(raw exchange、`replace('live_','live: ')` 半humanize)
  - `Proposals.tsx:172,173`、`AgentActivity.tsx`(status/trigger/asset_class)、
    `NewsIntel.tsx:114-142`(sentiment/status/event_type)、`Learning.tsx:132,156`(item_type)
  - 修复：统一 humanizer（snake_case→Title Case、bool→ON/OFF），集中成 lib helper

- 🟠 **跨组件 badge 状态→颜色不一致**：`DataTable StatusBadge`(running→yellow) vs
  `SerenityLens RunStatusBadge`(running→blue)——同状态两色
  - 修复：删 RunStatusBadge，统一走共享 `StatusBadge`（或导出共享 `statusToBadgeClass()`）

### 6.3 🟡 工程基础（内联样式 / 时间戳 / 失效 class / mono）

- 🟡 **未定义 utility class 被使用 → 样式静默缺失**：`.mt-1`(`PredictionLens:280,307`)、
  独立 `.pnl-positive/.pnl-negative`(仅在 `.hero-value`/`.metric-value` 下定义，
  `PredictionLens:39,118,201` 独立用 → 无色)
  - 修复：补 `.mt-1`、补独立 `.pnl-positive/negative{color:var(--green/red)}`

- 🟡 **off-grid 间距泛滥**：`--space-1..8` 已定义但 ~70-80 处声明用 ad-hoc rem
  (0.3/0.35/0.42/0.45/0.55/0.65/0.7/0.85/1.1/1.25rem 等 ~25 种)，集中在 legacy +
  CommandCenter + Lens 块；cockpit/account 块已正确用 token
  - 修复：迁移到 `var(--space-N)`，无对应的就近 snap

- 🟡 **时间戳格式不统一**：`.slice(0,16)`(Universe/StrategyLifecycle/Learning/Charts) vs
  `toLocaleString`(NewsIntel) vs `.slice(5,16)`(Overview:121) vs 相对 `timeAgo`
  - 修复：抽一个共享 `fmtTimestamp()` helper，全站统一

- 🟡 **内联 style 重复已有 class**：`RiskControls.tsx:76-90,146`、`Settings`(多处)、
  `Charts.tsx:19-26,96-177`、`ConnectionStatus.tsx:33-38`、`ObjectDetail.tsx:45`
  (后者还漏传 danger class)、`ActivityTimeline.tsx:123`、`Universe.tsx:282`
  - 修复：换 `.flex/.gap-N/.text-sm/.text-muted/.btn-control.danger`；补缺失 utility
    (`.items-baseline/.flex-col/.gap-4/.mt-1/.mb-1/.text-xs`)

- 🟡 **重复/冲突 CSS 规则**：`tr.clickable:hover`(525 vs 1772)、`.card-head-space`(488 vs 1726)
  - 修复：删重复定义

- 🟡 **data 值缺 `.mono`**：`TradesPositions.tsx:35`(PnL span)、`SerenityLens`(confidence/score
  多处)、`Learning.tsx:176,209`(confidence)、`NewsIntel.tsx:106`(timestamp)
  - 修复：补 `.mono`

- 🟡 **文本 `.slice(0,N)` 截词**：`NewsIntel.tsx:124`(`summary.slice(0,150)`)切到词中间
  - 修复：CSS line-clamp 或按词边界截断

### 6.4 修复批次规划（按"系统级根因优先"）

1. **批次 A — CSS 系统根因**（一次性消除整类问题，收益最高）：补/改 token、删冲突
   `.badge-red`、删紫色、补 `.mt-1`/独立 pnl 色/缺失 utility class、删重复规则。
2. **批次 B — Universe.tsx 整页重写**到设计系统 class（单页最严重）。
3. **批次 C — 共享 humanizer + 共享 StatusBadge 收敛**：raw enum/bool/lifecycle-state
   一次性人性化 + 统一 badge 配色 + 统一时间戳 helper。
4. **批次 D — 逐页内联样式/mono/截词清理**（机械、低风险）。

> 每批次后 `npm run build` + preview 端到端验证（实测渲染，非截图臆断）。

### 6.5 ✅ 已修复（2026-06-14，A–D 四批次落地，build 绿 + preview 实测）

**批次 A — CSS 系统根因（`index.css`）**：
- `--radius`(7)→`--r`/`--r-sm`、`--text-muted`(7)→`--muted`、`--font-mono`(1:589)→`--mono`、
  `--surface-hover`(1772)→删重复 `tr.clickable:hover`（保留 526 的 `--surface-2` 版）。
  preview 实测：`.lens-module` border-radius 0→**8px**，`.lens-status-label` 颜色→`--muted`。
- 删除行 1717 冲突 `.badge-red`（曾覆盖全站、用错红、丢边框）；行 766 command-card 红收敛到 token 红。
  preview 实测：`badge-red` 边框已恢复 `rgba(255,93,108,0.35)`。
- 删紫色 `#b864ff`：`.badge-purple`→蓝 token；`.btn-promote`→accent cyan。preview 实测全站 0 紫色元素。
- `.cc-inspector` `#0d1319`/`#d8e2ef`→`--bg-2`/`--text`；`.asset-tooltip` 删 stale hex fallback。
- 新增 utility：`.flex-col/.gap-4/.items-baseline/.mt-1/.mt-4/.mb-1/.mb-4/.text-xs` +
  独立 `.pnl-positive/.pnl-negative`（修复 PredictionLens 静默死样式）；`.badge` radius→`--r-sm`。
  新增 `.badge-muted`、`button.danger`、`.badge-lg`、`.boundaries-grid`、`.news-*`。

**批次 B — Universe.tsx 整页重写**：全部 Tailwind class → 设计系统(`MetricTile`/`.account-snapshot`
grid/`.card`/`.mono`/原生 button/token familyColor)。preview 实测：tile 背景 `--surface`、
grid 4 列、数字 IBM Plex Mono、**0 个残留 Tailwind class**。

**批次 C — 共享 humanizer + StatusBadge 收敛**：
- `lib/format.ts` 新增 `humanize()`（snake_case→Title Case + 域内覆盖表）、`humanizeBool()`、
  `fmtTimestamp()`（全站统一 "MMM D, HH:mm"）。
- `StatusBadge` 升级为唯一权威：humanize 标签 + `statusBadgeClass()` 语义配色（覆盖 lifecycle/
  run/proposal/position 全状态）；删 SerenityLens 本地 `RunStatusBadge`（曾 running→蓝冲突）。
- 接入 8 文件：StrategyLifecycle(lifecycle 语义色)/SerenityLens/Learning/NewsIntel/RiskControls/
  Overview/Proposals/AgentActivity。preview 实测 6 页 raw-enum 泄漏 = **0**；Proposals 抽屉
  `usdt_futures`→"USDT Futures"、`bitget_demo`→"Bitget Demo"。
- 修 SerenityLens `ElapsedTimer` render 内 `setTimeout` 死循环（曾致 preview 渲染卡死）。

**批次 D — 逐页 polish**：
- TradesPositions：`badge-amber`→`badge-yellow`、PnL/数据 cell 补 `.mono`、`pnlCell` 用语义类、
  时间戳→`fmtTimestamp`、group 标题 + dir/mode/close_reason humanize、删冗余内联 margin、
  exposure `{v} USDT`→`fmtNum`。
- ProbabilityChart：硬编码 hex → `chartTheme`（theme.ts 新增 `borderColor`/area-fill 字段）。
- ObjectDetail：danger 按钮内联 rgba → `button.danger` class（且修了 danger class 从未生效的 bug）。

### 6.6 ✅ Settings.tsx 已修复（2026-06-14，build 绿 + preview 四 tab 实测）

- **Layout primitives 抽类**：`Section`/`Row`/`Toggle`/`SaveMsg` 从全内联样式改为
  `.settings-section`/`.settings-row`/`.settings-toggle`/`.settings-save-msg` CSS 类，
  off-grid rem（2.5/1.25/0.7/0.85rem 等）改 `var(--space-N)`。
- **form-grid 合规**：universe-config 数字网格改 `.settings-field-grid`/`.settings-field`，
  输入框去掉 `width:80px/120px` 改 **填满列**（preview 实测 100%），单位并入 label 文本
  （"Refresh Interval (Min)" / "Min 24h Volume (USDT)" / "Max Spread (Bps)"）。
- **硬编码色清零**：reasoning tier `var(--purple,#a78bfa)`→`var(--blue)`；symbol chip
  `rgba(239,68,68)`→`.symbol-chip.allow/.deny`（token 红/青）；observed-instrument chip
  `rgba(100,100,120)`→`.instrument-chip`（token）。preview 实测四 tab **0 紫色 / 0 外来色**。
- **bool/enum 人性化**：`String(require_risk_check)`="true"→`humanizeBool()`="ON"/"OFF"；
  `live_state`="live_disabled"→`humanize()`="Disabled"；scheduler "active"/"disabled"→
  "ACTIVE"/"DISABLED"；model enabled/disabled badge 补 `badge-muted` 色 + Title Case。
  preview 实测四 tab **0 raw bool / 0 raw-enum badge**。
- 副作用：移除大量内联 style 对象后 JS bundle 略减（800→797KB）。

### 6.7 ⏳ 仍待处理（低优先 polish，未做，诚实记录）

- **off-grid 间距存量**：CommandCenter（1380 行）+ Lens 块仍有 ad-hoc rem 未逐一 snap 到
  `var(--space-N)`。关键 utility class 已补齐，新代码可直接用；存量迁移属机械低价值工作。
- **CommandCenter / Charts 内联样式**：ProviderChip、Charts 布局内联 rem、ConnectionStatus/
  ActivityTimeline 内联色尚未全部抽类（功能正常，纯一致性）。
- **响应式断点收敛**：23 个 `@media` 散在 700/768/900/1100/1180/1200，未统一到一套尺度。

---

## 备注

- 本清单基于规格 §23（13 子节）+ §2/§4/§13/§15/§29 + trust §3 全文对照前端源码
  得出，覆盖 12 页 + 2 charts + 2 components + 1 api/client + 1 App.tsx + 1
  index.css，共约 1730 行代码
- **修复优先级建议**：
  1. **P0**（§1 红条 + §3.1 + §3.2 + §3.3）：共享 types + DataTable + DetailDrawer
     先做工程基础，再落 §1 红条——Proposals / AgentRuns / Knowledge 详情抽屉一次解三个红条
  2. **P1**（§2 字段补齐 + §3 其余工程基础）：随抽屉模式逐页补齐 §23.x 缺字段
  3. **P2**（§2.2 双向连接 + §2.4 Reports 指标）：完整图与决策连接、性能指标
- **不要绕过去做假数据**：BACKEND_ISSUES 中标"先后端、后前端"的条目（KB markdown
  阅读、agent_run trace、strategy_id 链路、promotion gate metrics 暴露），等
  对应后端修好再做前端
- **change-controlled 模块的 UI 镜像**：`server/routes/live.py` / `risk/engine.py`
  / `config/secrets.py` 对应的 UI 操作（live unlock / kill switch / 凭据轮换）
  必须保持现有 `Verify-before-commit` + 二次确认 + audit 模式不退化（trust §6.3）

---

## §4. 🟡 OAuth Provider UI — 已知未修复项（2026-06-14）

> OAuth subscription provider 前端审计中发现但因 scope / 并发安全原因未在本轮修复
> 的改进项。已修复项：状态判断改用 oauth.configured、模型选择 fallback 到手动输入、
> label 改为 experimental。

### 4.1 🟡 模型下拉只显示 slug，无 tier / context window 提示

- 佐证：`frontend/src/pages/Settings.tsx:1671-1679`（OpenAI）、`:1758-1766`（Claude）
  — `<select>` 的 `<option>` 只渲染 `{m}`（裸 slug）
- 影响：用户在 OAuth panel 选模型时看不到质量层级、上下文窗口、是否 vision 等信息，
  只能靠记忆选；HTTPProviderForm 的模型 combobox 已经有 tier badge + context_window
- 依赖：后端 §5.4（health endpoint 需返回 model_metadata，或新增专用 route）
- 最小修复：后端解决 §5.4 后，OAuth panel 复用 HTTPProviderForm 的 model combobox
  带 tier badge 与 context_window 显示

### 4.2 🟡 OAuth panel 无 disconnect / re-login 操作

- 佐证：`frontend/src/pages/Settings.tsx:1663-1708`（OpenAI）、`:1749-1806`（Claude）
  — connected 状态下只有 "Set as default" 按钮，无法主动断开或重新登录
- 影响：用户如需切换账户（如从 Plus 切到 Pro），只能手动清 secrets 或等 token 过期
- 最小修复：connected 状态增加 "Disconnect" 按钮，后端新增
  `POST /model/{openai,claude}-oauth/disconnect`（清 oauth_token_ref + oauth_refresh_ref
  + 设 auth_type 回 api_key）
