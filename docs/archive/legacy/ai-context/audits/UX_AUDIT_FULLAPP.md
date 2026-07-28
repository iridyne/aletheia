# Aletheia Trading OS — 全应用 UX 审计报告

> 2026-06-20 | 产品经理视角 + UI 第一性原理 | 覆盖全部 14 个页面 + 全局 Shell  
> 方法论：三组并行审计 Agent，各自独立读取源码，交叉验证，无预设结论。

---

## 执行摘要

全应用共发现 **85 个 UX 问题**（High 28 / Medium 36 / Low 21），其中 6 个跨页面系统性缺陷覆盖了 10+ 个页面，是当前最高优先级的修复目标。

### 六大系统性缺陷（跨页面，优先级最高）

| # | 缺陷 | 影响页面 |
|---|---|---|
| SYS-1 | **无实时轮询** — 所有关键 query 无 refetchInterval，数据不自动刷新 | 全部 14 页 |
| SYS-2 | **交易模式不透明** — paper/live 模式不在决策页面 ambient 展示 | Proposals / AgentActivity / Trades / Overview |
| SYS-3 | **确认模式不一致** — 最高危操作用最弱的 confirm()，部分完全无确认 | 全部 |
| SYS-4 | **Loading 状态缺失** — 首次渲染展示虚假空状态而非 skeleton | 10+ 页 |
| SYS-5 | **Mutation 错误无处理** — useMutation 普遍无 onError，静默失败 | 全部 |
| SYS-6 | **重复控制 Pause/Resume** — 两个页面相同按钮指向不同子系统 | CommandCenter / RiskControls |

---

## 第一部分：全局 Shell（StatusBar + MarketTicker + Nav）

### StatusBar

**[HIGH-G1] Permission Mode 循环按钮无确认**
单击即循环 Observe → Paper → Full。Full 模式允许真实交易。误触一次即可把权限从 Paper 升级到 Full，无任何障碍。这是整个应用风险最高的 UI 元素，确认摩擦必须与后果匹配。

**[HIGH-G2] 离线状态文字与颜色语义脱钩**
`operatorStatus === 'offline'` 时圆点变红，但文字显示的是 operatorMode（可能是"Observing"）而非"Offline"。用户看到红色"Observing"无法理解系统实际已宕机。

**[MEDIUM-G3] 三个状态圆点无永久标签**
位置是唯一区分方式，标签只在 hover popover 里。视觉语言依赖 user 记住顺序而非读标签。

**[MEDIUM-G4] Heartbeat 计时器精度不足**
每 60 秒更新一次，导致最长近 2 分钟显示 "<1m"。交易系统中 2 分钟心跳失联是有意义的。

**[MEDIUM-G5] 代码漂移第四个圆点视觉不可辨**
与其他三点同一视觉 weight，用户容易误读为 live 点。

**[LOW-G6] live 启用状态用黄色（警告语义），正常禁用状态用 muted（消失）**
语义反直觉：live 启用应是绿色（正常运行），降级才用黄色。

### MarketTicker

**[LOW-G7] 代码注释 "Indices (pending)" — 功能不存在**
承诺的 SPX/VIX 占位符在代码中完全缺失。注释描述不存在的 UI，误导开发者。

**[LOW-G8] 无数据新鲜度指标**
市场数据每 15s 刷新，但如果后端 feed 断了，价格静默变陈旧。tick-flash 只检测价格变化，不检测数据停止更新。

### Nav

**[MEDIUM-G9] Approval Desk 导航项无待办数量 badge**
用户在任何非 Overview 页面时，无 ambient 信号提示有待批准的交易提案。proposals query 已全局存在，加 badge 是一行代码。

**[LOW-G10] News Intel 在 Operate 组，应在 Observe 组**
新闻是观察性输入，被分在系统操作组（与 Risk Controls、Settings 并列），IA 错位。

---

## 第二部分：Overview + PnLZone（驾驶舱）

**[HIGH-O1] "Today PnL" 标签歧义且模式混用**
`today_pnl` 显示 `${fmtSigned(dayPct)}% of managed`——"managed" 对用户没有定义。demo 模式下这是虚拟资金百分比；live 模式下分母可能是陈旧的同步数据。交易模式 qualifier 与 PnL 数值分离，用户聚焦 tile 时看不到。

**[MEDIUM-O2] Approval Desk 截断到 4 条无披露**
Badge 显示 7，但只渲染 4 条。用户可能误以为只有 4 个待批。

**[MEDIUM-O3] PnLZone 无 skeleton，flash-of-empty 明显**
`status` resolve 后立即渲染 PnLZone，但 `portfolio`/`utaState` 尚未就绪，所有数字显示 `—`。渲染路径：skeleton → 部分空 → 逐步填入，看起来像 bug。

**[MEDIUM-O4] "Realized" PnL "all-time" 定义不明**
"all-time" 的起点（账户创建？gateway 首次运行？交易所凭证添加时间？）未定义。PnL 参考窗口对交易解读至关重要。

**[MEDIUM-O5] 资产饼图只响应 mouseEnter/Leave，触屏/键盘不可用**

**[LOW-O6] Risk state "not_available" 与 "warning" 颜色相同**
运行中发现问题（warning）与风险引擎未运行（not_available）是两个不同根因，不应用同一色调。

**[LOW-O7] Pause/Resume 在 Overview 的错误无处理**
`pauseMut`/`resumeMut` 无 onError，失败静默。StatusBar 同样操作有错误处理，Overview 副本没有。

---

## 第三部分：Charts（Markets 页）

**[HIGH-C1] WatchlistGrid 无 skeleton，首次加载全显示 `—`**
`snapshots` query 无 `isLoading` gate。12 张价格卡片在首次加载时全部显示 `—`，像一个已损坏的页面。

**[MEDIUM-C2] dataProvider 取 `snapshots[0]?.provider`，空列表时永久 "loading…"**
如果 universe 为空，数据源栏永远显示 "loading…"，应显示 "no data configured"。

**[MEDIUM-C3] 图表 Tab opportunities 截断 5 条无标注**

**[MEDIUM-C4] Signals tab 静默过滤 `asset_class !== 'crypto'` 行**
行为本身合理，但未披露，应显示 "Showing crypto signals only"。

**[LOW-C5] Empty state 混淆两种根因**
"observer hasn't run yet" 与 "observer ran and found nothing" 含义不同，用同一文本表达有误。

**[LOW-C6] PredictionsTab 与 PredictionLens 页内容重复，无差异化价值**

**[LOW-C7] 时间戳归一化逻辑在 5+ 个文件中重复**
`endsWith('Z') || includes('+') ? ts : ts + 'Z'` 应提取为 `lib/format.parseTs()`。

---

## 第四部分：Universe（宇宙板）

**[MEDIUM-U1] 无 loading state，首次加载显示虚假空状态**
"No universe data yet — trigger a refresh" 在数据加载前就显示，用户无法区分"正在加载"和"真的没有数据"。

**[MEDIUM-U2] 概况 tile 在 snapshot 为 null 时显示四个 `—`**
四个破折号加两个状态词看起来像解析错误，应换成统一的说明 callout。

**[MEDIUM-U3] Scanner Calibration 表格使用内联样式**
与全局 CSS 变量体系不一致，不响应主题变化。

**[LOW-U4] 抽屉中 TTL 显示原始秒数（"3600s"），表格显示小时**
同一数据两种格式，抽屉应复用表格的 humanize 逻辑。

**[LOW-U5] MemberDrawer 无 staleTime，每次打开都重新请求**

---

## 第五部分：PredictionLens

**[HIGH-P1] DisabledOverlay 硬编码中文文本，整个应用其他地方都是英文**
`"Prediction Market 已关闭"` 和 `"此功能当前未启用。前往 Settings → Market Data..."` — 语言一致性 bug。

**[MEDIUM-P2] Preferences tab 在功能禁用时仍可操作**
`isDisabled` 只 gate 了 market/run query，preferences tab 无 `enabled` 守卫，用户可修改偏好即使功能关闭。

**[MEDIUM-P3] Observer 状态卡 API 失败时永远显示 "Loading status…"**
无错误状态，无 retry 提示。此卡片在页面最上方，首屏永久 loading 会让用户立即怀疑系统状态。

**[MEDIUM-P4] freshnessLabel 硬编码 15 分钟阈值**
应使用 `status.observer_interval_minutes` 动态计算，否则与 observer 实际节奏脱钩。

**[MEDIUM-P5] Market Radar tab 无 loading state**
同 U1，首次加载显示虚假空状态。

**[LOW-P6] "Clear all categories" 无确认**
一键清空手工维护的类别列表，无 undo。

**[LOW-P7] DirectionBadge "YES falling" 标签歧义**
与预测市场的 YES/NO 结算语言混淆，建议改为 "Bearish on YES"。

---

## 第六部分：Proposals（审批台）——资金最高风险页面

**[HIGH-PR1] Approve 按钮调用 `/approve-paper` 但确认文字说 "routes the order through the execution gateway"**
这是整个代码库中最严重的诚实性问题。确认对话框描述的是真实订单路径，但 API 只执行 paper 模拟。用户被告知他们正在执行真实交易，而实际并非如此——或者，若系统切换到 live 模式，同一段文字将变得准确，留下无法溯源的语义歧义。

**[HIGH-PR2] 页面无交易模式 ambient 指示器**
整个 Approval Desk 没有 page-level 的 paper/live 状态显示。用户打开浏览器，不知道自己是在批准虚拟交易还是真实交易。

**[HIGH-PR3] 批准/拒绝用 `window.confirm()`——可被肌肉记忆误触**
`window.confirm` 无样式、无键盘 trap、无应用上下文。用户连续操作时极易 "OK OK OK" 穿透。批准是不可逆的，拒绝是永久的，两个动作都需要 inline 确认行展示具体的 symbol/方向/数量/模式。

**[HIGH-PR4] busy 标志覆盖整个队列而非单个卡片**
一个审批 in-flight 时，所有卡片的按钮都变 disabled。时间敏感场景下无法并发处理多个提案。应改为按 proposal ID 追踪 pending state。

**[MEDIUM-PR5] 理由截断 180 字符无展开按钮**
高风险决策流程中截断决策理由会迫使用户点 Details 才能看全文，可能基于片段做出判断。

**[MEDIUM-PR6] proposals query 无 refetchInterval**
审批台是最需要实时刷新的页面——新提案随时可能到达，但用户不刷新就看不到。

**[MEDIUM-PR7] Opportunity/Strategy 链接跳转到列表页而非具体条目**
用户必须在表格中自行搜索，应传 query param 做深链。

**[MEDIUM-PR8] 抽屉内的 Approve/Reject 与卡片上的按钮维护两条独立代码路径**

**[LOW-PR9] History 表格无 Mode 颜色 badge，paper 和 live 历史记录外观相同**

**[LOW-PR10] RiskVerdictInline 最多显示 2 个违规，"+N" 无展开**
违规是拒绝提案的主要依据，应在决策卡片内全部可见。

**[LOW-PR11] 批准/拒绝 mutation 无 onError 处理**

---

## 第七部分：AgentActivity（Agent 运行 + 机会）

**[HIGH-AA1] "Run an agent now" 面板零摩擦，无模式指示，无后果说明**
`strategy_designer`、`risk_critic`、`portfolio_agent` 都直接在列表里，单击立即运行，无确认、无解释、无 live/paper 模式提示。在 live 配置下手动触发 portfolio_agent 是有实际后果的操作。

**[HIGH-AA2] 页面无交易模式 ambient 指示器**（同 PR2）

**[MEDIUM-AA3] Runs tab badge 显示总历史数量而非"活跃中"数量**
Badge 约定语义是"需要关注的数量"（待处理、进行中、错误），显示 2847 total runs 无任何信息价值。

**[MEDIUM-AA4] agent-runs 和 opportunities query 均无 refetchInterval**
用户触发 agent run 后不刷新页面永远看不到状态变化。

**[MEDIUM-AA5] Opportunities 表格缺少到期时间/陈旧程度列**
一个 6 小时前的 "open" 机会可能已不可操作，但用户无法知晓。

**[MEDIUM-AA6] Equity/Prediction 过滤 tab 按 count>0 条件渲染，数据到来时有布局跳动**
应改为静态 tab + disabled 状态，防止用户正在点击时布局位移。

**[MEDIUM-AA7] "Observation Only" 标签对股权/预测标注，但加密货币没有对称说明**
不对称的标注暗示加密货币机会总是可操作的，实际不一定。

**[MEDIUM-AA8] startAgentRun mutation 无 onError**

**[LOW-AA9] ReasoningStepsSection 即使 tool-calls 表格有内容也显示 "pending backend" 注释**
应只在 `toolCalls.length === 0` 时显示。

**[LOW-AA10] outputs_created 直接 stringify JSON，不可读**

**[LOW-AA11] cost_estimate 显示 `$0.0000`，读起来像免费**

---

## 第八部分：TradesPositions（交易台账）

**[HIGH-T1] Paper 和 live 持仓用 `<h4>` 字符串分组，无颜色/边框/图标区分**
快速扫描时极易混淆 paper 和 live 持仓。唯一区分是组标签字符串，在只有一种模式时甚至不醒目。

**[HIGH-T2] 持仓抽屉的 PnL 数字无模式 qualifier**
`DrawerField label="Unrealized"` 显示原始数字，无货币上下文，无 paper/live 标记。一个 paper +$4200 和 live +$4200 看起来完全相同。

**[MEDIUM-T3] 已平仓 positions 用 `{closed.length > 0 &&...}` 条件渲染，无空状态占位**
新用户看不到这个 section 存在，无法知晓功能会在有平仓时出现。

**[MEDIUM-T4] 三个核心 query（positions/trades/portfolio）均无 refetchInterval**
实时策略运行中，用户看到的是陈旧数据。

**[MEDIUM-T5] Orders 表格含 error_reason 列，大多数行显示 `—`，视觉噪声**
应仅在当前视图有非空 error_reason 时显示该列，或移入抽屉。

**[MEDIUM-T6] Reconciliation 卡片列出待处理差异（adoption/orphan），但无 action 按钮**
用户看到风险敞口不匹配但无法操作，是高优先级功能缺口。

**[MEDIUM-T7] syncStatus 显示 "stale" 文本但无具体陈旧时长**

**[MEDIUM-T8] CausalChain 从持仓锚点出发时 7 节点中 5 个显示 "not recorded"**
显示成坏掉的功能而非文档化的数据缺口。应提示 "持仓记录不含订单/提案链接——打开关联订单查看完整链"。

**[LOW-T9] CausalChain Fill 节点显示 order.price 而非 fill_price**
有滑点的市价单中这是错误的。

**[LOW-T10] Analytics/Insights tab 无 skeleton，用裸 `<p>Loading…</p>`**

**[LOW-T11] AccountSnapshot "today figure unavailable" caveat 在 paper 模式下永久显示**
永久 caveat 会被用户忽略，应改为在 today_pnl 为 null 时隐藏整个 tile。

---

## 第九部分：StrategyLifecycle（策略流水线）

**[HIGH-SL1] Promote 单击即提升策略阶段，无任何确认**
从 `paper_passed` 提升到 `live_candidate` 是有真实后果的不可逆操作，无 confirm() 无 modal。

**[HIGH-SL2] Retire 单击即退役，无确认，且按钮样式与普通操作相同**
无 unretire 路径，不可逆操作应有 danger 样式 + 确认。

**[MEDIUM-SL3] 策略表格无 loading state，首次加载显示虚假空状态**

**[MEDIUM-SL4] 已 retired 策略在 stepper 中不显示，全流程显示为"完成"**
视觉上像 live 策略，语义上是死亡策略。

**[MEDIUM-SL5] 执行 Venue 下拉框 onChange 立即 mutate，无 loading/error surface**

**[MEDIUM-SL6] `deprecated` 状态在代码中有判断但不在 STATES 数组中**
后端返回 deprecated 时用户无法操作 Promote 也无法操作 Retire，卡死。

---

## 第十部分：Learning（知识库 + Dreaming）

**[HIGH-L1] DreamingTab 无 loading state，首次打开全显"无数据"空状态**

**[MEDIUM-L2] "Current Dreaming Task" 仅在 runs > 0 时渲染**
没有 dreaming 任务时，整个 section 消失，操作员无法判断 dreaming 是健康运行还是配置有问题。

**[MEDIUM-L3] Knowledge sub-tab 0 个条目时无 badge，与有条目的 tab 外观相同**

**[MEDIUM-L4] Reports tab PnLChart 无 loading/error 处理**
内部 fetch 失败时对用户完全不透明。

**[MEDIUM-L5] renderMarkdown 是手写解析器，有结构性 bug**
多行列表项的 `<li>` regex 不使用 `s` flag，`<p>` fallback 会双重包裹已在 `<ul>` 中的行，Knowledge Base 内容会出现可见乱码。

---

## 第十一部分：SerenityLens（深度研究引擎）

**[HIGH-SR1] "Promote" 主题单击无确认，且无任何说明 promote 会做什么**

**[MEDIUM-SR2] 详情面板 loading 用裸 `<p>Loading…</p>`**
应使用 skeleton 避免内容跳动。

**[MEDIUM-SR3] Tab 标签从 enum 字符串直接首字母大写**
"Evidence" 不能传达它是 claims-backing 原始数据；"Profile" 对研究偏好来说是异常术语。

**[MEDIUM-SR4] "Deep Dive" 按钮行为不透明**
用户不知道点击会立即发起 API 调用，还是排队，还是消耗配额。无确认无说明。

**[MEDIUM-SR5] Evidence tab 无 source URL 链接**
来源对研究分析至关重要，但点击 evidence 条目看不到原始链接。

**[MEDIUM-SR6] 限速错误消息截断到 100 字符**
关键的 retry-after 信息可能被截断。

---

## 第十二部分：CommandCenter（运营控制台）

**[HIGH-CC1] Runtime Drain 和 Rollback 用 `window.confirm()`，Rollback 确认文字无实质描述**
"Rollback to the latest pre-promote checkpoint?" 未说明会丢失什么数据、持仓是否受影响、checkpoint 包含什么内容。Rollback 是潜在数据破坏性操作，需要 "I understand" 型 modal。

**[HIGH-CC2] 与 RiskControls 页有重复的 Pause/Resume 但目标子系统不同**
CommandCenter 的 Pause/Resume 针对 operator agent（`api.pauseOperator`），RiskControls 针对执行调度器（`api.pause`）。两个按钮标签相同，操作员在紧急情况下无法判断用哪个。必须在标签中区分范围（如 "Pause Operator Agent" vs "Pause Scheduler"）。

**[HIGH-CC3] 5 种 session branch 类型无任何说明**
Command / Diagnostic / Dreaming / Strategy / Review 的区别完全不透明，新用户无法做出有意义的选择。

**[MEDIUM-CC4] 页面无 loading state，首次加载全显虚假空状态**

**[MEDIUM-CC5] active session 被 operator status 轮询自动切换，无通知**
用户在检查历史 session 时，UI 会静默跳走到新 session，体验混乱。

**[MEDIUM-CC6] Context Inspector 展示原始 JSON**
开发者数据暴露给操作员，上方的 `summary` 字段才是正确的信号来源。

**[MEDIUM-CC7] History 过滤器作用于 4 个列表但无每个列表的匹配数量**

---

## 第十三部分：RiskControls（风险控制）

**[HIGH-RC1] Kill Switch Release（解除暂停）无任何确认**
`releaseKill.mutate()` 直接执行。更讽刺的是：Engage（触发）有 `confirm()`，Release（解除）没有。恢复交易比停止交易风险更高，守卫方向反了。

**[HIGH-RC2] TopBar 提示指向错误位置**
`App.tsx` 紧急提示写的是 "Go to Settings → Risk → Kill Switch"，但实际路径是 `/risk`（Operate 组的顶级路由）。紧急情况下用户跟着错误提示走会找不到出口。

**[MEDIUM-RC3] Kill switch 数据加载前显示虚假 "INACTIVE"（绿色）**
`!!undefined?.active === false` → kill switch 加载中显示 "INACTIVE" 绿色。假安全信号比空状态更危险，必须在真实数据到达前显示 skeleton 或 "Loading…"。

**[MEDIUM-RC4] Risk state "unknown" 用 blue 色调**
未知风险状态不是"中性信息"，应用 yellow/muted 并加说明"状态未知，请检查后端"。

**[MEDIUM-RC5] Engage kill switch 只有 `window.confirm()` 守卫**
虽然确认文字准确，但 browser confirm 可被意外触发。考虑 inline "type HALT to confirm" 模式。

**[MEDIUM-RC6] 与 CommandCenter Pause/Resume 标签相同但针对不同子系统**（同 CC2）

---

## 第十四部分：NewsIntel（新闻情报）

**[HIGH-NI1] NewsIntel 出现在主导航 Operate 组，与 Risk Controls 和 Command Center 并列，但本身是明确的 stub**
页面自己承认是手动录入、无实时数据。将它放在最高优先级导航组会让用户在紧急操作时浪费注意力。建议移到导航末尾或加 "(stub)" 标注，直到自动接入实现。

**[MEDIUM-NI2] "Analyze Impact" 按钮不检查 LLM provider 配置状态**
mutation 无 onError，失败时静默。LLM 未配置时按钮看起来正常但点击无反应。

**[MEDIUM-NI3] analysis 状态在卡片切换时可能陈旧（item A 的分析结果在 item B 的 request pending 时残留）**

---

## 统计汇总

| 严重程度 | 数量 |
|---|---|
| High | 28 |
| Medium | 36 |
| Low | 21 |
| **合计** | **85** |

### 按主题分布

| 主题 | 条数 | 主要页面 |
|---|---|---|
| 无实时轮询（refetchInterval 缺失） | 10 | 全部关键页面 |
| 交易模式不 ambient 展示 | 4 | Proposals/AgentActivity/Trades/Overview |
| 危险操作确认不足/缺失 | 12 | 全部 |
| Loading/skeleton 缺失 | 11 | 10+ 页面 |
| Mutation 无 onError | 7 | 全部 |
| 信息诚实性（数据含义误导） | 8 | Proposals/PnLZone/PredictionLens |
| 导航/IA 问题 | 5 | Nav/RiskControls/NewsIntel |
| 代码质量（TypeScript any、重复逻辑） | 4 | 全局 |
| 功能性 bug（中文文本、Markdown 解析器） | 3 | PredictionLens/Learning |

---

## 建议修复优先级

### P0（立刻修——影响用户安全决策）
1. **PR1** — Proposals 确认文字改为实际描述 paper 执行
2. **RC1** — Kill Switch Release 加确认
3. **RC2** — TopBar 紧急提示路径改正
4. **RC3** — Kill switch loading 期间不显示假 INACTIVE
5. **G1** — Permission Mode 循环按钮加 confirm()
6. **SYS-2** — Proposals/AgentActivity/Trades 加 page-level 模式指示器

### P1（本周——影响关键操作流程）
7. **SYS-1** — Proposals/AgentActivity/Trades/Overview 四个页面所有关键 query 加 refetchInterval（proposals: 5s，runs: 5s，positions: 10s，portfolio: 15s）
8. **PR3/PR4** — Proposals 批准用 inline 确认行，per-proposal busy flag
9. **SL1/SL2** — Promote/Retire 加 confirm()
10. **CC1/CC2** — Rollback 改为 modal + 描述；Pause/Resume 标签区分子系统
11. **G2** — StatusBar offline 状态文字修正

### P2（本月——影响整体体验质量）
12. **SYS-4** — 补全 StrategyLifecycle/RiskControls/CommandCenter/Learning/Charts loading skeleton
13. **SYS-5** — 全应用 mutation 加 onError（至少 inline SaveMessage 展示错误）
14. **G9** — Approval Desk nav item 加 pending count badge
15. **P1** — PredictionLens 中文文本改英文
16. **L5** — renderMarkdown 改用库或修复 regex
17. **CC3** — Session branch 类型加 tooltip 说明

### P3（按需——体验提升）
- Universe/PredictionLens/Charts/AgentActivity loading state 补全
- T1/T2 paper/live 持仓视觉区分加强
- T8 CausalChain 从持仓锚点时显示友好提示
- NI1 NewsIntel 移出主导航或降权
- C7 时间戳归一化提取为 lib/format.parseTs()
- U3 Universe 内联样式改 CSS 变量

---

## 亮点：值得保留的好设计

- **CausalChain 组件**：从不捏造数据，明确区分 present/empty/missing，文档化 schema 缺口而非隐藏——这是全应用诚实度最高的组件
- **SerenityLens 整体**：状态卡 always above fold、空状态带因果说明、运行中的 ElapsedTimer、限速精确 retry 时间——是最 UX 完整的页面，应作为其他页面的模板
- **Proposals 的 ConvictionMeter + risk verdict live fetch**：不预烘焙风险结论，实时拉取；bull/bear debate 分数是真正有用的决策辅助
- **Overview 双栏 ResizeObserver 同步**：避免了 min-height CSS hack
- **PredictionLens "Observation only" 声明**：明确说明不路由到执行层，防止用户误解

---

> 本报告覆盖 2026-06-20 代码快照。建议在 P0/P1 修复落地后重新扫描确认。  
> 相关文件：[Settings 审计经验](UX_AUDIT_LESSONS.md) — 第一性原理与实施经验总结。
