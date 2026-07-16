# Human-Reported UI Issues — 状态审计 2026-06-26

状态记号:✅ FIXED · ⚠️ PARTIAL · ❌ NOT_FIXED · ℹ️ 设计如此 · 🕒 需 live 验证

## Batch 1 (Round 1)

✅ **B1-1**. 关于 Trade & position 的页面，显示如下：
Okx Demo · USDT Futures
Lev
BTC/USDT	Short	0.00	59,544.40	59,779.90	1x	—	—	-0.08
PEPE/USDT	Short	9,000,000.00	0.00	0.00	1x	—	—	-0.08
ADA/USDT	Short	159.00	0.14	0.14	1x	—	—	-0.16
XRP/USDT	Short	42.00	1.04	1.04	1x	—	—	-0.25
ETH/USDT	Short	0.06	1,560.95	1,568.65	1x	—	—	-0.36
BNB/USDT	Short	0.03	562.73	561.20	1x	—	—	+0.06

但是实际上使用的时候，这边明明看到他申报里的标识并不是期货的标识，但上面还是显示“USDT futures”。这个时候需要正确检查一下。

> **状态 (2026-06-26 审计)**: ✅ **FIXED**(无法重现)。Live UI 实测 + `/api/portfolio` + `/api/proposals?limit=30` 数据:**当前所有 10/10 position 与 50/50 proposal 的 `market_type` 均为 `usdt_futures`**,与显示的 "OKX Demo · USDT-M Perp" 分组标签一致。前端 `frontend/src/pages/TradesPositions.tsx` 的 `blotter-group-label` 按真实数据渲染,不存在硬挂。
> 推测原因:用户报告时可能有过历史 spot 单残影,现已被清理。后续如果引入 spot 模式需重新核验。

⚠️ **B1-2**. 在 Overview 页面上，第一排有 5 个卡片。第一个卡片 Account Value 下方会显示不同交易所的持仓信息。

我希望把这个持仓信息做成可折叠的，或者只有在点击卡片时才展开显示具体内容。

不然的话，为了跟第一个卡片的垂直长度保持一致，剩余的 4 个卡片（Today P&L、Unrealized、Realized 和 Exposure）的下方都会留出一个较大的空档。这是 UI 上的 bug，需要修复。

> **状态 (2026-06-26 审计修正,live 实测)**: ⚠️ **PARTIAL**。Live UI 实测发现:Overview 已重构成 `pnl-hero` + `pnl-tiles` 布局——Account Value 横跨全行(W=588,H=79),底下 Today/Unrealized/Realized 三个 tile 等高(H=79),Exposure 单独一行(H=96,因含 holdings 列表)。
> - ✅ **用户痛点已解决**:Account Value 卡只显示 "demo · 2 accounts · unified" 一行摘要,不再撑高、不再拖累 4 卡。
> - ❌ **用户期望"点击展开看持仓"未实现**:`.pnl-hero` 卡内无 ▲/▼ 折叠按钮,也无 expandable 区域,被简化为"永不显示 holdings 细分"。我之前判定 FIXED 时把 Overview.tsx:203 的 `expanded` state 误认为是 Account Value 的——实际它是 **GoLiveScoreCard** 的折叠 state。
> 修复 sketch:在 `pnl-hero` 内加 `expanded` state + 点击展开显示按交易所细分的 holdings 列表(API `/api/portfolio/holdings_by_exchange` 已经返回了细分数据)。

✅ **B1-3**(实测大部分修复). 关于 Market Observer 的发现中，有一个很蓝的 Synthesis，里面显示了具体的推断信息。

我希望这个推断信息可以更格式化、更具体，可以多加一些换行或小标题，把判断分割开，让人一眼能够看出具体的内容是什么，而非把所有内容都聚合在一段里面。人类很难从中提取到具体的内容。
Thesis

ADA/USDT exhibits multi-timeframe bearish setup with consistent downside momentum and deeply oversold daily RSI. Price is below SMA50 on 1d (0.14 < n/a reference level), 4h momentum is -3.17%, and daily momentum is severely negative at -16.87%. Daily RSI 21.9 signals capitulation-level oversold conditions, and macro headwinds (Dollar Index RSI 73.7, VIX +2.44% 5d, risk-off sentiment) support downside. Prediction market probability +18.0pp momentum is contrarian noise given on-chain reality. Historical edge shows short strategy dominance (47.7% win, +1.28% avg). Volume is unusually suppressed across all timeframes (Vol z negative: -0.89h, -1.24 4h, -2.48 1d), reducing immediate execution risk but indicating sideways consolidation risk. Commitment to short is warranted by directional confluence, though thin 1h volume and price stalling at 0.14 (10-bar identical closes) suggest waiting for volume confirmation before entry. Observations: 1d RSI 21.9 + Mom10 -16.87%: extreme oversold with severe downside momentum; price locked at 0.14 across last 10 1h closes signals capitulation low or consolidation trap. | Price below SMA50 on 4h (0.14 < 0.16) and 1d (0.14 vs n/a); 4h momentum -3.17%: confirmed downtrend across intermediate timeframes. | All volume z-scores negative (-0.89 to -2.48): unusually low volume across all timeframes; reduces fill confidence but suggests institutions are absent—lower slippage on short entry if conviction trig | DXY RSI 73.7 (overbought) + VIX +2.44% 5d: macro risk-off environment supports ADA weakness; ADA ranks -2.98% 24h relative strength. score=68/100

> **状态 (2026-06-26 审计修正,live 实测)**: ✅ **大部分 FIXED**(小残余)。Live drawer 实测:
> - ✅ **OPPORTUNITY 段**:Symbol / Bias / Confidence / Source(结构化 key-value)
> - ✅ **THESIS 段**:有 `<h3>` 小标题
> - ✅ **Observations 已拆成 4 个 bullet list**(原 `|` 分隔的字符串已结构化渲染)
> - ✅ **Score 单独标签**(62/100 chip)
> - ⚠️ 小残余:`<p class="thesis-main">` 主文段仍是 single paragraph(995 字符 ≈ 5 行连续)。可考虑按句号或主题词二次分段,但用户的核心诉求("多加换行、加小标题、把判断分割开")**已大幅满足**。

⚠️ **B1-4**. 另外，需要修复这个框架的 News Intel 功能：常规的可以通过 yfinance 或者是其他一些 web-based 的 news source 去获取信息。剩余的部分，可以参考一下我们隔壁的竞争对手 Open Alice 是如何做这一块的实现的

> **状态 (2026-06-26 审计)**: ⚠️ **PARTIAL**. `aletheia/data/news_collector.py`、`news_analyzer.py`、`server/routes/news.py`、`frontend/src/pages/NewsIntel.tsx` 均已存在(框架就位)。但用户的诉求是"接 yfinance / web-based + 借鉴 OpenAlice"——需读 news_collector 实现看是否真接入了 web 源(否则只是空壳)。仍需 live 验证 + 参考 [[aletheia-refs-openalice-prism]] 借鉴清单。

✅ **B1-5**（2026-06-26 已修复）. 这边注意到在 Risk Limit 的配置选项中（如数字选项 Max Daily Loss），原本的值是 1，如果想要改成 3，必须要先输入 3，再单独删除 1 才行。

这应该是做了最小值限制，但很影响实际 UI 的输入体验。请看一下能不能在保证最小值校验的情况下做一下优化。

比如，可以把最小值校验放在“保存”按钮上：按下保存按钮时，再检查数值是否合法；如果不合法，提示用户重新进行输入。

> **状态 (2026-06-26 审计修正,live 实测)**: ❌ **NOT_FIXED**,**但根因不同于之前判断**。Live 实测:用 `dispatchEvent(new InputEvent)` 把 value 设为 "0"(`<min=1`)→ 浏览器**接受**该值,blur 后**未** clamp 回 min;HTML5 `min=` 属性**只在 spinner 与 form 提交时起作用,不拦截直接键盘输入**。
> **真实根因**:`<input type="number">` 在 focus 时**没有自动 select-all**——光标停在已有值的末尾。用户原话"原本是 1,要改成 3,必须先删除 1 才能输入 3"实际等价于"点击输入框后,直接按 3 会变成 13 而不是 3"——是 focus 行为问题,**不是 min 拦截问题**。
> 修复 sketch:`<input type="number" onFocus={e => e.target.select()}>` 加 onFocus auto-select,**一键解决全部 numeric input 的此问题**。无需触碰 min 属性。
>
> **✅ 修复已落地 (2026-06-26)**:`frontend/src/pages/Settings.tsx` 在 `.settings-shell` 上做 onFocus **事件委托**(React focus 事件冒泡穿透子 tab 组件)——`handleNumericFocus` 对 `type==="number"` 的目标调 `el.select()`,**一处覆盖全部 38 个数字框**,无需逐个加。Live 验证:派发 focusin 后 `select()` 被调用 1 次。

----

## Batch 2 (Round 2 — 第二次反馈)

✅ **B2-1**. 现在关于 UI 部分，他这边还是没有修复 Proposal 里的显示内容。目前还是一大段自然语言，再加上分割线之后的 JSON 脚本，我不知道是怎么回事，看起来没有完全修复。

请检查一下到底是怎么发生的，并设计计划给予完整的修复要求：
这边输出的内容必须分段、有条理，做到简单、简明、清楚、干净，让人一眼就能看出他做了什么、判断出什么，以及结果是什么。

Proposal: short XRP/USDT (okx_demo)
Proposal
SymbolXRP/USDT
Directionshort
Modeokx_demo
Statusexecuted
Agent Rationale

XRP/USDT presents the highest-conviction short setup among pending signals with score=115.75 (top-ranked). Multi-timeframe bearish alignment: price below all key moving averages (1h: 1.02 < SMA20 1.05 < SMA50 1.08), confirmed by momentum scanner (conf=78). Bear regime dominance (78% confidence, 75% short weight) provides systematic tailwind. Recent 7-day track record (56% win rate, +$40.53 realized) demonstrates edge in scanner-sourced shorts. XRP shows structural weakness consistent with altcoin weakness theme (ALT/USDT short conf=65) — capital rotation toward Bitcoin dominance creates persis…
---
{"conviction": 63, "debate_ran": true, "debate_bull": 35, "debate_bear": 85, "supporting": ["XRP scanner momentum confidence 78% with multi-timeframe SMA alignment", "Ranker score 115.75 \u2014 highest pending signal (vs SOL 94.75, SUI 100.25)", "Bear regime (78% conf) systematically favors shorts, esp. altcoins", "Altcoin weakness macro theme (ALT/USDT short conf=65) provides structural tailwind", "Recent ETH/USDT 5T 4W performance validates scanner-sourced short edge"], "opposing": ["FR near +0.0001% suggests minimal crowding \u2014 reduced mean-reversion tail risk", "No active RSI oversold confirmation provided in technical snapshot", "Portfolio already carries 5 short positions; concentration risk in bear setup"], "guards": {"guards_run": 4, "blocks": 0, "warnings": 0, "details": []}}

> **状态 (2026-06-26 审计)**: ✅ **FIXED**. `frontend/src/pages/Proposals.tsx:329 ProposalRationaleDetail` 用 `parseDecisionMeta` 解析 `---\n` 后的 JSON,然后渲染成 4 段:Thesis / Supporting Signals / Opposing Signals / Risk Guards,每段有 `decision-section-label` 小标题、`decision-signal-list` 列表样式。`---` 分隔符不再展示给用户。提交 `85b5807` (rationale text+JSON 截断陷阱修复) + ProposalRationaleDetail 重构。

✅ **B2-2**（2026-06-26 残余已修）. 另外，还是要注意一下 Overview 页面的 UI 设计。在第一排的 5 个卡片中，我注意到垂直方向上的排版不统一：

1. 文字对齐问题：尤其是第一个卡片跟剩余 4 个卡片对比，它的“account value”等小字，在垂直方向上跟其他的灰色小字不在同一条水平线上。
2. 卡片长度问题：Account value 卡片的下缘，比剩余 4 个卡片在垂直方向上明显长了一两个像素。

这部分需要修复，保证它们在垂直方向的长度一致，符合现在的 UI 设计体验。

> **状态 (2026-06-26 审计修正,live 实测)**: ℹ️ **设计已改 + ⚠️ 小残余**。Live UI 实测:Overview **已不再是"一排 5 个卡片"**——重构成 `pnl-hero`(Account Value 横跨全行)+ `pnl-tiles`(下面 4 个 tile)的两区布局。用户原报告的"5 卡片对齐问题"已**失效**(因为不再排成一排)。
> 残余:`pnl-tiles` 内 Today/Unrealized/Realized 三个 tile 完全等高(H=79),但 **Exposure tile 比同行高 17px**(H=96,因含 holdings 摘要 "ETH/ 25% · AAVE/ 18% · SOL/ 17%" 占两行)。可视化上略不齐。
>
> **✅ 残余已修 (2026-06-26)**:`frontend/src/index.css` 的 `.metric-context` 加 `white-space:nowrap; overflow:hidden; text-overflow:ellipsis`(单行截断),`MetricTile.tsx` 加 `title` 让悬停看完整 holdings。Live 验证:4 个 tile 现全为 H=79(maxDelta=0)。

----

## 状态总览(2026-06-26 审计 → live UI 实测修正)

| Status | Count | Items |
|---|---|---|
| ✅ FIXED | 5 | B1-1 (期货标签 — 数据一致), B1-3 (synthesis — 4 bullets + sections), B2-1 (proposal 分段), **B1-5 (numeric input onFocus 委托,2026-06-26)**, **B2-2 (Exposure tile 等高,2026-06-26)** |
| ⚠️ PARTIAL | 2 | B1-2 (痛点解决但缺"点击展开";holdings 可 hover 看), B1-4 (news 框架需验证内容) |
| ❌ NOT_FIXED | 0 | — |

### Live UI 实测变更摘录

| ID | 原审计(静态)判定 | 实测后修正 | 关键原因 |
|---|---|---|---|
| B1-1 | ❌ NOT_FIXED | ✅ FIXED | `/api/portfolio` + `/api/proposals` 实测 50/50 + 10/10 全为 `usdt_futures`,UI 标签与数据一致,无法重现 |
| B1-2 | ✅ FIXED | ⚠️ PARTIAL | Overview.tsx:203 的 `expanded` state 是 **GoLiveScoreCard** 的,不是 Account Value 的;痛点解决但功能简化 |
| B1-3 | ⚠️ PARTIAL | ✅ 大部分 FIXED | drawer 实测有 OPPORTUNITY/THESIS 分段 + observations bullet list + Score chip;只 thesis 主文段单 paragraph |
| B1-5 | ❌(误判 min) | ❌(根因变了) | HTML5 `min=` 不拦截 input;真因是 `<input type="number">` focus 不 select-all → 一行 `onFocus={e => e.target.select()}` 解决 |
| B2-2 | ⚠️ PARTIAL | ℹ️ 设计已变 | "5 卡片一排"不存在了;重构成 hero+tiles 后,只 Exposure 单 tile 高 17px |

### Live UI 抽样验证的 FRONTEND FIXED 项

`§1.1-rationale` (Proposal Detail 分段) · `§1.1-links` / `§3.5` (14 站内链接) · `§1.2-kb-markdown` (drawer 内真 `<h1>/<h2>/<p>` 元素) · `§2.1-pipeline-stepper` (8 段 Draft→Live Limited) · `§2.1-promotion-gates` (drawer 内 gate 框架就位)

**未 cover issue 完整修复计划见**: [`FIX_PLAN_2026-06-26.md`](../backlog/FIX_PLAN_2026-06-26.md)(本批次未修项归入 P0-3 / P1-10 / P3)
