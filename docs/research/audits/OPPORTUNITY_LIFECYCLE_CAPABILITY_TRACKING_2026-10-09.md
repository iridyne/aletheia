# 以 Opportunity / 持仓生命周期为线索的能力追踪 — 2026-10-09

## 使用范围与证据身份

本文是**日期化的只读审计证据**，不是 Contract、不是能力承诺、也不是任务看板。它回答三个问题：
沿着"一个交易机会从被发现到被复盘"这条线，框架现在**能干什么**、**应该能干什么**、**哪些还没实现或已经坏了**。

- 能力承诺以 [Capability Ground Truth](../../product/CAPABILITY_GROUND_TRUTH.md) 为准，运行准入以
  [Active Runtime Scope](../../product/ACTIVE_RUNTIME_SCOPE.md) 为准，已批准行为以
  [`docs/design/contracts/`](../../design/contracts/) 为准。本文与它们冲突时，以它们为准，并把冲突当作待修缺口。
- 找代码请用唯一代码地图 [SYSTEM_MAP](../../design/architecture/SYSTEM_MAP.md)。本文只按行名引用它（例如
  「SYSTEM_MAP：Planner 未运行或没有新 Plan」），不复制目录树。
- **代码基线**：各段审计时为 `master@origin 45a733bd`，能力表"代码"列的行号都按它。之后 #3850（`99a220664`，多周期锚点 +
  失效锚点方向侧过滤）改动了 `trading/entry_analysis.py`、`entry_contract.py`、`entry_profiles.py`、`agent_shell/entry_planner.py`、
  `storage/repositories.py`，这些文件的行号可能已偏移。修订时（`master@origin d4a7a61c`）与运行版本 `99a22066` 之间只有文档提交，
  代码只差一行注释。
- **修订后的 master（`cda9cfeaa`，2026-10-09T09:2xZ）已合入但未部署的相关修复**：PR #3879（#3848 PR-B，交易所止损原地修改、
  修改失败如实返回，跟踪止损改用修改结果并删除独立保护单死链）、PR #3881（#3880，发送前失败把原因码写进订单行与审计）、
  PR #3882（#3880，按亏损预算精确定仓的计划不再因浮点舍入被风控拒）、PR #3872（#3846，风控 #16 经 Entry Plan 解析现有持仓的族）。
  Fedora 在 09:23Z 仍运行 `99a22066`，所以下文"部署后"的运行事实都发生在这些修复之前；凡受影响的行另加"master 已修复、未部署"。
  其后 Fedora 于 09:24:16Z 换到 `1531758d`（session `c0ccc8e23754`），四个 PR 都是它的祖先；本文**没有**对该版本取证，下文"未部署"均指截至 09:23Z。
- **运行基线**：Fedora（u7p）在 2026-10-09T08:15:23Z 换到 `99a22066`，runtime session `e4013aa099a8`，schema `aletheia-sqlite-v96`，
  执行模式 `bitget_demo`。它包含 #3848 的入场回执确认止损（`45a733bd`）、交易所止盈止损默认对 bitget_demo 开启（`1c982f2a7`）和
  #3850 多周期锚点（`99a220664`）；这三个提交都已用 `git merge-base --is-ancestor` 核实是运行版本的祖先。
  **本文大部分数字查询于换版本之前**（旧运行 `aa09a87c`，session `8e2940290087`，06:49Z 启动）；换版本之后的事实单独标注
  "部署后"，查询时间 08:24Z（评审）与 08:48Z（修订复核）。1.1 里的"部署以来"指 2026-10-08T12:23Z 的 Universe 部署。
- **查询方式**：Fedora 数据库一律以只读 URI（`mode=ro`）打开；日志只读；`aletheia control status --json` 只读。查询时间在
  2026-10-09T07:2x–07:59Z（换版本前）与 08:24–08:48Z（换版本后）之间，每条证据都标了时间或窗口。日志行时间为北京时间（UTC+8），其余为 UTC。
- **数字纪律**：本文所有数字都来自本轮各段只读查询的原始结果。不同段用的统计窗口不同（24h、48h、7 天、自部署以来、全历史），
  引用时都写明窗口，**不同窗口的数字不能直接相加或互相比较**。
- **未核实事项**集中列在 §4。
- **本文只保留冻结的事实证据**（§0 总览、§1 逐段、§2 持仓管理专章、§3 端到端追踪、§4 未核实清单）。按已批准的
  [能力地图提案](../../design/proposals/lifecycle_capability_map.md) 和 [文档治理规则](../../governance/policies/documentation_governance.md#stable-content-rule)，
  带严重度的缺口总表、需要 owner 决定的事项和持仓管理补齐方案的推荐顺序属于优先级与待办，已移到路线图 issue
  [#3883](https://github.com/iridyne/aletheia/issues/3883) 的首次能力盘点评论里，不在本文。
- **Issue 列**是 2026-10-09 的引用快照：写"无对应 issue"表示当时没有 issue 跟踪；已关闭的 issue 标"已关闭"，PR 写作"PR #N"。
- 分段按能力地图的 11 个阶段：候选、规划、入场计划、触发、提案、风控、执行、持仓管理、平仓结算、结果度量、复盘学习
  （"反馈回下一轮"并入复盘学习）。观测面是横切，不算阶段。

### 术语（第一次出现时的定义）

| 术语 | 含义 |
|---|---|
| Candidate（候选） | Universe 每 10 分钟扫描后选出的一个"品种 + 方向 + 机制"组合，写在 `universe_members` |
| Startup 准入 / 实测准入（measured） | 实测准入：按"扣成本后 4 小时实测收益"排序选候选；Startup 准入：样本不够时退而按信号强度选。合同要求实测优先、Startup 只补空位 |
| Planning Generation（规划代次） | `candidate_planning_generations` 的一行：对一个 Candidate 做的一次规划尝试，有认领、租约、重试上限 |
| EntryHypothesis（入场假设） | AI（大模型）在规划时给出的受约束结构化输出：选锚点、Profile、捕获方式、风险姿态，不给价格和仓位 |
| 锚点（Anchor） | 系统从 K 线结构里算出的候选价位（摆动高低点、突破位等），AI 只能从中选入场点和失效点 |
| Compiler（编译器） | 确定性代码，把假设编译成止损/入场区/目标/仓位/杠杆 |
| Entry Plan（入场计划） | Compiler 产出的不可变开仓计划，`entry_plans` 表 |
| Proposal（交易提案） | 由 Entry Plan 生成的不可变下单意图，`trade_proposals` 表，`source=native` |
| Risk（风控） | `risk/engine.py` 的 `evaluate_proposal`，下单前唯一硬门；结果为 APPROVED（批准）/ DEFER（暂缓，稍后重试）/ REJECT（拒绝） |
| preflight / authoritative | 风控两段：preflight 是下单前的预检；authoritative 是交易所数量归一化之后、带幂等身份的正式审批 |
| Gateway / Coordinator / UTA | 执行三层：Gateway 是唯一执行入口；Coordinator 管订单提交、撤单、平仓 claim；UTA 是统一交易账户抽象（Bitget 用 `uta_v3`） |
| Post-Only / IOC | Post-Only：只挂单不吃单；IOC：立即成交否则撤销（吃单） |
| TPSL | Take-Profit / Stop-Loss，交易所侧挂的止盈止损单（"原生保护"） |
| horizon（预期时长） | 入场时冻结的"预期持有小时数"`expected_horizon_hours` |
| HORIZON_DUE | 持仓时长到达 horizon 时发给 AI 的唤醒事件 |
| 复查 | `review_portfolio_state`：AI 对被唤醒的持仓给出 hold（持有）/ adjust_position（调整）/ close（平仓；prompt 写作 close_one，库内记为 `close`） |
| ExitPlan | 持仓上的退出计划：止损、止盈、horizon、跟踪止损规格 |
| R | 一笔交易的初始风险单位（入场价到止损的距离 × 数量）；"净 R" = 扣成本后的盈亏 ÷ R |
| MAE / MFE | 持仓期间最不利 / 最有利的价格偏移 |
| 合格结果（eligible outcome） | 经 Outcome Integrity 核验（PnL = 毛利 − 开仓费 − 平仓费 − 资金费）的策略平仓 |
| D0 / D1 / D2 | 见 AGENTS.md：D0 = 合同已有定义、代码不符，修代码恢复；D1 = 改语义；D2 = 改结构/归属/范围 |
| 状态分类 | **LIVE** 生产接通且 Fedora 有实测；**WIRED_UNOBSERVED** 有生产调用方但近期无运行证据；**DORMANT** 代码在但开关关或无生产调用方；**MISSING** 合同/owner/竞品要求但无代码；**BROKEN** 存在但实测失败 |

---

## 0. 一页总览

一句话：**这条线的前半段（候选 → 规划 → 风控）每天都在转，后半段（下单 → 持仓 → 平仓 → 复盘）从 2026-09-29T06:20Z 起没有新持仓；
部署 `99a22066` 后终于有 3 张入场单走到交易所，但 3/3 失败。持仓管理即使在有仓位的时候，也只有"到时长唤醒 AI 一次"这一种在跑，
AI 几乎总是答"继续持有"。复盘产出的东西没有一条回到下一轮决策。**

评级含义：**通** = 这一段在 Fedora 上有近期产出，交接到下游；**部分通** = 能产出，但有已实测的主要缺陷或主要能力未生效；**断** = 这一段近期没有有效产出或交接不上。

```
候选 ──► 规划 ──► 入场计划 ──► 触发 ──► 提案 ──► 风控 ──► 执行 ──► 持仓管理 ──► 平仓结算 ──► 结果度量 ──► 复盘学习
                                                                (逐轮复查与新指令)                                   │
   ▲───────────────────────────────────────────────────────────────────────────────────────────────────────────────┘ 反馈回下一轮（目前断开）
```

| # | 段 | 评级 | 最关键的一个事实 |
|---|---|---|---|
| 1 | 候选（Universe） | 部分通 | 自 2026-10-08T12:23Z 部署以来 1070/1070 个 Candidate 都是 Startup 准入，实测准入 0 次；其中 467 个（44%）是被当成加密货币的股票/贵金属/指数永续 |
| 2 | 规划（Planner + AI） | 部分通 | 部署前 24h 341 代，锚点 4082/4082 只来自 15m，253 个 ENTRY_PLAN 假设里 61 个（24%）选了错侧失效锚点；部署后 13 代锚点为 15m 226、1h 77、4h 29，多周期已在运行（样本小） |
| 3 | 入场计划（Compiler 编译） | 部分通 | 近 7 天 462 代中 221 代（48%）死于固定 1.5% 止损下限 `stop_distance_below_vol_target`；部署后 13 代中仍有 6 代 |
| 4 | 触发（Fast Trigger） | 部分通 | 看守进程在跑；条件触发 `entry_trigger_activations` 全历史 0 行，AI 约 2000 次假设从未选 retrace-reclaim |
| 5 | 提案（Proposal） | 通 | 近 7 天 29 个 native Proposal，最新 2026-10-09T05:01:09Z |
| 6 | 风控（Risk） | 部分通 | rev 10（05:55:32Z）后 14 条决策：authoritative 批准 6、preflight 批准 6、preflight 拒绝 2；2 条拒绝都是重复的 demo 单笔风险检查因浮点误差（差 <1e-12 USDT）错拒已编译计划（PR #3882 已在 master 修复，未部署） |
| 7 | 执行（Gateway/UTA/Bitget） | 断（新出现 BROKEN） | 止盈止损开启后，部署后前 3 张入场单 3/3 失败：`command_not_sent` 2 张（原因码当时未记录，仍在调查；记录原因码的 PR #3881 已合入 master、未部署）、`venue_rejected_command_25206` 1 张（#3880 已查实：Bitget demo 现货盘口与主网报价脱钩）；此前 10-08 的 22 次批准死于 `uta_account_identity_mismatch` |
| 8 | 持仓管理（逐轮复查与新指令） | 部分通 | 41 笔交易所持仓里 20 笔从开到平 AI 一次没看过；46 次复查中 41 次是 hold，且 hold 会让 horizon 翻倍 |
| 9 | 平仓结算 | 部分通（历史通）| 41/41 平仓价来自交易所成交、资金费来自账单；但滑点 41/41 为 NULL，当前构建上零次执行 |
| 10 | 结果度量 | 部分通 | 合格胜率 23.1%（9/39），净 −17.008 USDT；系统自己算不出任何一笔的净 R（39/39 unavailable） |
| 11 | 复盘学习（含反馈回下一轮） | 部分通（反馈断） | 复盘在写；但 lesson 自 2026-09-07 起 32 天 0 条晋级，审查器每次都审同一批 40 条老 lesson；入场规划的 529 次学习上下文回执全部是"未分配的基线"，没有一条学习结果改变过选币、规划或风控 |
| 横切（非阶段） | 观测面（status/doctor/health/UI/告警） | 部分通 | 同一时刻 `/api/health` = healthy、`issues=[]`，doctor 报"7 天 36 个计划 0 个下单"；部署后入场 3/3 失败时（08:48Z）health 仍是 healthy、`issues=[]`；Telegram/Webhook 在 Fedora 上都关闭 |

---

## 1. 逐段章节

每段格式：目标 → 能力表 → 应该有但没有 → 交接。能力表的"代码"列给 `文件:行`（基线 `45a733bd`）；"证据"列给 Fedora 实测与时间。

### 1.1 候选：Universe 扫描与 Candidate 准入

**目标**：每 10 分钟从当前执行场馆（Bitget Demo）可交易的精确品种中，按"硬门槛 → 机制证据 → 4h 成本后实测效用（无样本时用 Startup 信号强度）"选出不超过 `max_candidates` 个 Candidate，原子发布成 Planning Generation 交给规划。代码入口见「SYSTEM_MAP：Candidate 有事实却未准入」。

| 能力 | 状态 | 证据（Fedora） | 代码 | 契约 | Issue |
|---|---|---|---|---|---|
| 场馆品种注册 + Demo 白名单（拿不到 profile 时 fail-closed） | LIVE | 07:21Z 日志 `Demo whitelist active: 54 symbols`；registry 中 bitget 活跃 41 永续 + 11 现货；24h 每轮 `assets_scanned=52`，completed 122 次 | `universe/registry.py:49,88,145-156`；`execution/venue_capabilities.py:176,250` | candidate_selection_utility §chain 1 | — |
| 非加密标的识别（股票/贵金属/原油/指数永续） | BROKEN | 部署以来 universe_members 1070 行中 467 行（44%）是 TradFi；Generation 273 行中 115 行；NVDA、XAU 永续 `asset_class=crypto, source=supported_market_default` | `universe/registry.py:26-46`（只拦 tokenized equity、杠杆币、测试币） | Ground Truth 只承认 crypto spot/perp | #3440（等 owner：allowlist 还是 isRwa） |
| allowlist / blacklist | DORMANT | Fedora 配置两个列表均为空 | `universe/candidate_eligibility.py:109-124`；`config/schema.py:1754,1762` | §chain 1 | #3440 |
| 成交量排名注入扫描顺序 | LIVE | volume_ranking_snapshots 24h 5 行，最新 07:25:33Z；池子仅 52 个、全量扫描，排序不影响准入 | `agent_runtime/scheduler.py:636-642,4469`；`universe/service.py:706-738` | — | — |
| 特征采集快照 | LIVE | 24h `universe_feature_snapshots` 3733 行，最新 07:21:21Z；最新一轮 ticker 51/52、prefilter 41 | `universe/service.py:743`；`universe/features.py:285-298,820-900` | — | — |
| OI / 多空比 / 主动买入比 | BROKEN | 24h 3733/3733 行这四列全 NULL；`open_interest_snapshots` 24h bitget 0 行（okx 2622）；日志 `bitget fetchOpenInterestHistory() is not supported yet` | `universe/features.py:294-298,829-831,867-881`；`continuous_scoring.py:826-850` | derivatives_economic_semantics | #3081/#3083/#3075 只部分相关；无对应 issue |
| 盘口与资金费率采集（频率见 SYSTEM_MAP `data_collectors` 行） | LIVE | 24h orderbook 8068 行、38 品种，最新 07:27:19Z；funding bitget 2937 行 | `scheduler.py:1609-1610`；`extensions/data/collectors/` | — | — |
| 上下文采集（宏观/CoinGecko/链上/期权，只作背景；频率见 SYSTEM_MAP `data_collector_context` 行） | LIVE | 24h macro 73、coingecko 3335、onchain 234、options 143 | `scheduler.py:1614-1615` | — | — |
| 爆仓流 | MISSING | `liquidation_events` 无任何行 | `extensions/data/collectors/coinalyze.py`（待重新校验） | — | #3109、#3075 |
| 硬门槛 `universe-hard-gate/v1` | LIVE | 最新一轮 eligible_actionable 40、ineligible 1（稳定币） | `universe/candidate_eligibility.py:65-205` | §chain 1 | — |
| 无快速行情时降为 observe_only | LIVE | 最新一轮无路由缺失；8 个 observe_only 来自现货做空等无可执行方向 | `universe/service.py:187-199` | — | — |
| 机制证据 v2（Continuation/Reversal/Positioning） | LIVE | 部署以来 reversal 668、continuation 402、positioning 0 | `universe/opportunity_evidence.py:93-116` | — | — |
| Information/Catalyst 机制 | MISSING | 代码恒返回 `external_event_provenance_unavailable` | `opportunity_evidence.py:114-116` | §chain 2 | #3110、#3778 |
| 同轮分位数（每桶 ≥20 行） | BROKEN（未触发，必然触发） | 部署以来有分位数 2253 行、缺 2910 行（56%）；最新一轮 9 桶只有 1 桶满 20，现货桶只有 1-3 行 | `opportunity_evidence.py:159-183`；`economic_utility.py:494-495,54` | §chain 2/5"成熟 parent 无分位数 = unavailable，不是 Startup" | 无对应 issue |
| 实测经济准入 | DORMANT | 部署以来 measured 0（08:48Z 复核：10-08T12:23Z 以来 1172/1172 为 `admission_basis=startup`）；#3786 记录 09-08→10-08 13840 行全部 startup；parent 独立样本永续 21-24/50、现货 5-14/50 | `economic_utility.py:441-447,484-497`；`candidate_selection_utility.py:101-131` | §chain 3-4 | #3786、#3483-#3486 |
| Startup 信号准入（不看成本） | LIVE | startup_reason：parent_sample_insufficient 949、settlement_overdue 121；最新一轮 admitted 12 | `economic_utility.py:40-46`；`candidate_selection_utility.py:137-157` | §chain 5-6 | #3786 |
| settlement_overdue 优先级与合同措辞 | WIRED_UNOBSERVED | 69 轮中 10 轮出现；合同 L81 与 L125 说法相反 | `economic_utility.py:29,266-268,486-487` | 合同自相矛盾 | #3786（PR #3790 只改了 L81） |
| 跨市场去重 + `max_candidates=30` | LIVE | 最新一轮 admitted=selected=12，名额不是瓶颈 | `candidate_selection_shadow.py:222-248` | — | — |
| 注意力租约与淘汰 | WIRED_UNOBSERVED | 14 天 quota_evicted 0、admission_lost 8、planning_generation_superseded 1858 | `storage/repositories.py:3343-3425` | — | — |
| 原子发布 + 变化抑制 | LIVE | 部署以来 completed 77 轮、snapshot 69 个，最新 07:21:26Z | `universe/service.py:68,871-948,291` | — | — |
| Candidate → Planning Generation 交接 | LIVE | 部署以来 273 个 Generation，18 个带 entry_plan_id，0 个持仓 | `universe/service.py:130-167,100` | — | — |
| 全量分母反事实结果（twin + 4h outcome） | LIVE | twin batch 69；`filter_candidate_outcomes` settled 4773、pending 2474 | `universe/service.py:891,1090` | — | — |
| 结果结算（outcome_settler） | LIVE | 4h 结算滞后 p50 314 秒；≥1h 只出现在停摆窗口 | `scheduler.py:1585` | — | — |
| 特征采集指纹 | LIVE | 最新一轮旧指纹排除 92427 行 | `features.py:34` | — | #3786 |
| Regime 注释 | LIVE（但恒为 bull） | 09-09 以来 bull 1422、sideways 1 | `universe/service.py:652-668,752` | — | #3663 |
| Discovery Benchmark 旁路 | WIRED_UNOBSERVED | 全历史 1 行，最后 2026-09-01 | `universe/service.py:515-562` | — | #2926、#2923 |
| 研究用 filter 记录 | LIVE | 10-08 以来 209 行 | `universe/filters.py` | — | — |
| Relative-Rank shadow / 旧 opportunities 生命周期 | DORMANT | relative_rank 0 行；`opportunities` 最后写入 2026-08-27 | `universe/relative_rank_shadow.py` | 合同 L22-30 写明无调用方 | #2906、#2931 |
| 注册阶段公共接口失败 | BROKEN | 24h failed 15 轮（13 轮在部署前后的 10-08T01:50-13:23Z） | `universe/service.py:687`；`registry.py:88-222` | — | #3432（相关不同） |

**应该有但没有**
- 实测准入真正生效，Startup 只补剩余名额（合同 §chain 4-6；#3786）。
- 按类别排除 RWA/TradFi（#3440；Ground Truth）。
- Startup 准入也要考虑成本（#3786 实测 selected short 均值 −0.398%）。
- 分位数分桶与 52 个品种的池子规模匹配，否则实测准入上线当天就会断供（合同 §chain 2/5 + 运行时规模）。
- Positioning 机制需要 Bitget 的 OI 与爆仓数据（derivatives_economic_semantics；#3083、#3109）。
- Information/Catalyst 需要有来源可查的外部事件（#3110、#3778）。
- 候选到持仓的 `opportunity_id` 身份链（#3644、#3283、#3290）。
- 注册接口失败时复用上一版注册表并显式标记。
- 合同 L81/L125 统一。

**交接**：上游（scheduler、采集器、Bitget 公共接口）→ 本段：接通。本段 → 规划：接通（273 代）。全量 4h 反事实回流为训练历史：接通。Candidate → 持仓的 `opportunity_id`：**断**（41/41 持仓为 NULL）。

### 1.2 规划：Candidate Planning Generation → AI Entry Planner → EntryHypothesis

**目标**：把每个可执行 Candidate 变成一代持久化规划：认领、冻结一份因果一致的市场上下文、调用一次大模型产出受约束的假设，再交给 Compiler；每种结果都带类型码落库、可重试且有上限。代码入口见「SYSTEM_MAP：Planner 未运行或没有新 Plan」。

| 能力 | 状态 | 证据（Fedora） | 代码 | 契约 | Issue |
|---|---|---|---|---|---|
| 规划代次生命周期（认领/租约 300s/CAS/重试 ≤3） | LIVE | 全历史 4849 代；24h 新建 348 代；model_invoked 372 行中 278 行到达 compiler | `storage/repositories.py:3956-4000,4435-4566`；`agent_shell/entry_planner.py:363-594` | entry-plan-contract L301-342 | 13 个当前行状态与 attention 矛盾，无对应 issue（低） |
| 唤醒：Candidate 提交事件 + 15 分钟兜底，共用一个批处理 | LIVE | errors.log `evaluation done` 1584 次（heartbeat 846+314、signal 213+211），最后 15:26:16（北京） | `entry_planner.py:707-760`；`agent_runtime/pi_daemon.py:582-646`；`scheduler.py:1402-1411` | agent_contracts row 6 | — |
| 冻结规划输入（多周期 K 线、结构、指标、订单簿） | LIVE | 24h 341 代 quant_context 可用 341/341；多周期 technical 267/341；订单簿不可用退避 9 次 | `trading/entry_analysis.py:30,83-141,424-434,738-1019` | entry-plan-contract L81-157 | #3750 |
| 锚点注册表 | LIVE（部署后，样本 13 代；部署前 BROKEN） | 部署前（`aa09a87c`，24h）：锚点 4082/4082 都是 15m；340/341 代被截到 12 个；253 个 ENTRY_PLAN 中 61 个（24%）错侧；compiler 记 WRONG_SIDE 58 次。部署后（08:15:23Z–08:48Z）13 代的冻结输入里锚点为 15m 226、1h 77、4h 29；13 代中 0 代 WRONG_SIDE，但 6 代仍死于 `stop_distance_below_vol_target` | `entry_analysis.py:56,562-593,844`（`45a733bd` 行号；截断在方向过滤之前，已由 `99a220664` 修改） | owner 决定 5/5b.5 | #3850/#3777/#3743 |
| 规划模型调用（结构化输出 + 语义校验） | LIVE | 24h success 255（llama-4-maverick 251、gpt-oss-120b 4），structured_schema_invalid 82（全是 gpt-oss-120b） | `entry_planner.py:597-686`；`agents/helpers.py:690-1000` | llm_inference_harness | #3751 |
| 结构化输出修复（同模型修复） | WIRED_UNOBSERVED | 06:50Z 后 8/8 success、0 schema_invalid；样本约 35 分钟 | `helpers.py:635,672-673,939-947` | 拒绝归因 A1（已批准） | #3751（当时样本只有约 35 分钟） |
| 规划尝试历史（只追加） | LIVE（今天开始） | attempt_outcomes 11 行 / 8 代，06:52:50Z→07:21:56Z | `repositories.py:4260-4279`；`storage/migrations.py:7831-7852` | 拒绝归因 A3 | 无保留路径，见 #3643 |
| 模型调用前的类型化阻断 | WIRED_UNOBSERVED | `planning_blocked` 全历史 0 行；`runtime_authority_recovered` 115 次 | `entry_planner.py:391-448` | entry-plan-contract L159-216 | — |
| 按机制规划（四种机制） | DORMANT（只有两种在用） | 24h reversal 222、continuation 119；positioning 最后 2026-09-05；information_catalyst 0 | `trading/entry_profiles.py:36-57` | L159-216 | #3083、#3100 |
| NO_PLAN（AI 拒绝） | LIVE（几乎不用） | 24h 255 个假设中 NO_PLAN 2 个（约 0.8%） | `entry_analysis.py:144-213` | L301-327 | #3858 |
| AI 风险姿态 BASELINE/REDUCED | LIVE（无区分度） | 24h 253/253 BASELINE | `entry_analysis.py:168-263`；`trading/risk_intent.py` | owner 工作摘要"AI 给风险判断" | 无对应 issue |
| 捕获优先级与 Profile 选择 | LIVE（无区分度） | 253 个中 MAKER_PREFERRED 251、PRICE_FIRST 2、FILL_FIRST 0；Profile 253/253 grounded-anchor-direct | `entry_analysis.py:162-164` | 合同表 L316 名称已过时（D0 文档） | 无对应 issue |
| Learning 证据进入规划上下文 | DORMANT | 全历史 3094/3094 冻结输入 `learning_context=unavailable` | `entry_analysis.py:399-421,943-953` | L196-212 | #3570、#3041 |
| 重新规划 | LIVE（无成本上限） | 48h 444 次模型调用 → 36 个计划；COIN/USDT long 48h 内被重新规划 27 次 | `repositories.py:3585-3676` | L218-278 | 次数上限属新 D1，无对应 issue |
| 把假设交给 Compiler | BROKEN（下游大面积失败） | 部署前 24h 止损下限 186、WRONG_SIDE 58、成功 32；48h 36 个计划 0 个拿到 order_id。部署后 13 代：止损下限 6、`VENUE_MARGIN_TIERS_STALE` 2、成功建计划 5 | `entry_planner.py:234-294` | L344-363 | #3777/#3743/#3850/#3859/#3757 |
| 规划数据保留 | MISSING | app.db 3386 MB；规划表 JSON 289 MB / 4849 行；每天 14.9-32.9 MB | 无 prune；`migrations.py:7844-7846` 禁 DELETE | STATE_AND_DATA | #3643（量级需更新） |

**应该有但没有**
- 多周期锚点 + 截取前剔除错侧；全部不可行时在调用模型前返回带原因的"不做"（B3 预检复用 compiler 同一函数）。来源：owner 决定 5b.5、拒绝归因 §5 B3。
- AI 只给方向与结构失效点，价格全由系统算；提示词参数从系统常量生成。来源：Phase1 几何研究 §3；竞品 AstraQuant/nofx/OpenAlice。
- 先证明方向判断有优势（随机入场基准）再调几何。来源：#3858。
- AI 风险判断要有信息量。来源：entry_order_risk_sizing owner 工作摘要。
- 拒单全程可复算，14 天死因计数脚本收进 `scripts/`。来源：owner 决定 2 H6、拒绝归因 §4.4。
- Learning 证据进入规划上下文。来源：entry-plan-contract L196-212。
- 规划表保留策略、同一机会重规划次数上限（后者无契约条款，D1）。

**交接**：上游 Universe → 本段接通（Fedora 只观察到 universe 来源 3166 行；Serenity 来源全历史 0 行，接线未在运行时出现过）。本段 → Compiler 接通，但 Compiler 之后几乎全断（见 1.3）。

### 1.3 入场计划：Entry Plan 编译、仓位与杠杆

**目标**：把假设确定性地编译成不可变 Entry Plan：先算止损/目标几何，再按"亏损预算 ÷ 止损距离"算目标名义仓位，最后在上限内选能撑起它的最低安全杠杆；Risk 用同一组事实复算必须得到同样结果。代码入口见「SYSTEM_MAP：Entry Plan 的仓位/杠杆与其意图不符」。

| 能力 | 状态 | 证据（Fedora） | 代码 | 契约 | Issue |
|---|---|---|---|---|---|
| 确定性几何（止损=失效锚点∓容差，目标=最差入场±rr×R，rr 固定 1.5/2.5） | LIVE | 近 7 天 36 个计划；v2（09-29T15:38Z）以来 79 个 | `trading/entry_plan_compiler.py:19,267-319,334-369` | entry-plan-contract | #3850、#3859、#3860 |
| 固定 1.5% 止损下限门 | LIVE（第一大死因） | 近 7 天 462 代中 221 代；rev 10 后 26 代中 18 代；全历史 1515 代 | `trading/entry_plan.py:88-161`；`entry_plan_compiler.py:306-313` | owner 同意"可放宽"，做法在 #3859 | #3777（PR #3780 暂缓）、#3743、#3859 |
| 失效锚点方向侧检查 | LIVE | 近 7 天 65 代 | `entry_plan_compiler.py:274-278` | 合同未定义方向侧语义 | #3850 |
| AI 风险姿态映射（REDUCED=0.5） | LIVE（REDUCED 从未出现） | 79/79 BASELINE；errors.log 1726 条"risk_intent 缺失→补 BASELINE" | `trading/risk_intent.py:41-61` | sizing Contract A | #3751 相关，无对应 issue |
| 自适应仓位（亏损预算 ÷ 止损） | LIVE | rev 10 计划 d4e7e4e54faa：capital 441.82，预算 13.2545（3%），止损 1.51%，target 878.06 | `trading/entry_sizing.py:93-151,226-381` | Contract B | — |
| 品种最小/最大名义约束 | BROKEN（代码确认，无运行样本） | 从不传 max；min 传的是 `tiers[0].min_notional`（≈0） | `entry_plan_compiler.py:414-425`；`leverage_resolution.py:259-286` | Contract C 第 5、7 步 | #3844 |
| 最低够用杠杆解析 | LIVE | 7 天 36 个：(5,5)×13、(10,10)×9 等；margin_budget 约束 13 次 | `trading/leverage_resolution.py:102-312` | Contract C；owner 决定 4 | — |
| 按币种分档杠杆上限 10/5/3 | LIVE | audit：05:33:23Z rev 8→9、05:55:32Z rev 9→10；DOT 上限 5、PEPE 上限 3 | `trading/instruments.py:107-144` | owner 决定 4 | futures_cap 与档位重复，无对应 issue（决定 4 要求另开） |
| 场馆保证金事实（24h TTL，5min 维护） | LIVE | settings 38 行，最新 PEPE 07:05:18 | `trading/venue_margin.py:54,170-211,289-487` | sizing 合同 | — |
| 保证金事实与事件唤醒的竞态 | BROKEN | 部署前 `VENUE_MARGIN_TIERS_STALE` 2 次（AVAX 10-08、NEAR 10-09）；部署后（重启后）13 代中又有 2 代 `ENTRY_PLAN_COMPILE_UNAVAILABLE:VENUE_MARGIN_TIERS_STALE` | `venue_margin.py:200-205,357-487` | fail-closed 合规，但丢机会 | 无对应 issue |
| isolated 声明、拒绝 cross | LIVE | 77 个 v2 perp 计划全部 isolated | `execution/margin_mode_policy.py` | Contract C、#1451 | — |
| H5：止损必须先于强平 | MISSING | 77/77 未违例，但只是算术巧合（r=3%、保证金=全部余额） | `leverage_resolution.py:275-277` 只查 5% 缓冲 | owner 决定 2 | 无对应 issue |
| 现货融资用现金封顶 | BROKEN | 2 个 DOGE 现货计划名义 4326.64 / 758.32，可用 442.22 | `leverage_resolution.py:136-153` | Contract C 第 7 步 | 无对应 issue（D0） |
| Risk 提交时复核融资 | LIVE | `SIZING_RESOLUTION_DRIFT` 4 次（最后 09-29T23:20Z） | `risk/engine.py:206-289,437-482` | Risk convergence | — |
| 重复的 demo 单笔风险检查（扁平别名、无容差） | BROKEN | v2 以来 3 次因 <0.001 USDT 浮点差拒已编译计划；部署后又 2 次：08:17:43Z PEPE 计划 `cc99445cbe14`（`exceeds_demo_single_trade_risk:13.2546701883>13.254670188299999`）、08:39:07Z MSTR 计划 `cb8d1eb8bf76`（`13.254652300500004>13.2546523005`）。部署后 5 个计划中 2 个被它拒在 preflight，另 3 个才进入执行。PR #3882（#3880）已在 master 修复，未部署 | `risk/engine.py:1545-1556` | Risk convergence；一功能一实现 | #3880；#2971 相关，D0 |
| 风控策略改版后 watcher 用旧版本撤销新计划 | BROKEN | DOT（rev 9）05:50:16Z 编译、05:50:17Z 撤销；DOGE（rev 10，hash 与当前一致）06:23:25→06:23:27 撤销；重启后 PEPE 未撤销；09-11 另有 10 次同类 | `agent_runtime/fast_trigger_watch.py:121-133,727-812,410-424` | 计划绑定策略身份 | 无对应 issue（D0；根因为推断，2 样本） |
| Demo 置信度门关闭（rev 10） | LIVE | rev 10（05:55:32Z）后 14 条决策，0 条 `regime_confidence_standdown` | `risk/demo_entry_quality.py:72-110` | owner 选项 B2 | #3663 |
| 计划作为下单权威（杠杆/名义） | WIRED_UNOBSERVED | 79 个 v2 计划 order_id 全 NULL；v2 后 0 持仓。部署后 3 个计划生成了订单行，但 3 张都没有成交（见 1.7） | `execution/entry_plan_execution.py:303-370` | sizing 合同 | #3757、#3798、#3848 |
| 保证金占用 / 并发持仓政策 | MISSING | 代码推导：rev 10 PEPE 878.06/L2≈439 USDT，占可用 442.22 的 99% | `entry_sizing.py:341-349,129` | owner 工作摘要（1-5%）与决定 4 张力；决定 8 未决 | 无对应 issue |
| 调用 AI 前的确定性可行性预检（B3） | MISSING | 近 7 天 286 代（221+65）都是先调模型再死在 Compiler | 无 | 拒绝归因 §5 B3 | 归 #3850 |
| 止损下限改推导值 / 加宽不拒 | MISSING（master） | 见止损下限门 | PR #3780 分支（暂缓） | #3777（D1） | #3777、#3859 |

**应该有但没有**：H5 显式判定（止损太宽先降杠杆，降到 1x 仍不行再放弃；决定 2、研究 §5.1）；H6 可复算；融资检查交易所最小名义（#3844）；现货现金封顶；每笔风险统一 3%（已执行 rev 9/10）；杠杆档位 + futures_cap 去重；保证金占用/同时持仓数/同方向合计风险护栏（决定 8；竞品 AstraQuant 单笔保证金 ≤ 余额 20%）；盈亏比在可成交价上计算（研究 §2.2）；B3 预检；收敛成"AI 判断 → 确定性计算 → 一次风控 → 执行"（拒绝归因 §5 C，D2）；风控策略改版即时传到所有消费者。

**交接**：Planner → 本段接通（`entry_planner.py:255`）。本段 → 触发/执行：代码接通，运行时**从未走完**（v2 以来到部署前 79 个计划 0 个订单；部署后 3 个订单全部失败），另有策略改版撤销与现货超额两个已观察断点。

### 1.4 触发：Entry Plan 监控、激活与捕获战术

**目标**：把不可变 Entry Plan 放到实时行情上看守，执行失效、过期、触发激活与捕获战术（Post-Only 被动单 → 60 秒超时 → 最多一次 IOC 补单），只在条件成立时交给提案段（1.5）。

| 能力 | 状态 | 证据（Fedora） | 代码 | 契约 | Issue |
|---|---|---|---|---|---|
| 看守进程生命周期（暂停/熔断时撤未成交计划） | LIVE | 07:27Z `/api/status` fast_trigger_watch running，watched_entry_plan_ids=[] | `fast_trigger_watch.py:97-176,389-415,650-680`；`server/app.py:676-679` | — | — |
| 每 2 秒重载时完整性与权限复核 | LIVE | risk_authority_revoked 13 次（最近 05:50:17Z、06:23:27Z）；incompatible_planning_generation 24 次 | `fast_trigger_watch.py:321-560` | — | 见 1.3 策略改版缺口 |
| 价格失效 | LIVE | price_invalidated 30 条，近 7 天 3 条；d4e7e4e54faa 创建 6 秒后终结 | `fast_trigger_watch.py:903-924`；`gateway.py:1794-1797` | — | — |
| 过期与带原因的过期分类 | LIVE | 09-25 以来 328 个已终结计划寿命中位数 15.8 秒（p90 504 秒），TTL 中位数 3600 秒 | `fast_trigger_watch.py:438-451,589-633` | — | — |
| 按计划记录监控证据 | LIVE | 763 个计划：ready 588、degraded 34、warming 23 | `fast_trigger_watch.py:864-889` | — | — |
| IMMEDIATE 激活直接提交 | LIVE | 09-25 以来 328 个 grounded-anchor-direct 计划终结；今天 05:01:09Z 仍有 Proposal | `fast_trigger_watch.py:925-927,683-716` | — | — |
| CONDITIONAL 触发（retrace-reclaim 状态机 + 激活绑定） | DORMANT | `entry_trigger_activations` 0 行；约 2000 条假设 0 次选它 | `trading/entry_profiles.py:46-52`；`fast_trigger_watch.py:501-557,937-961` | entry-plan-contract Trigger activation | #3860 C、#3850；"为何从不被选"无对应 issue诊断 |
| MAKER_PREFERRED：Post-Only → 60s 撤 → 腿 1 IOC | WIRED_UNOBSERVED | 历史 passive_window_elapsed 撤单 40 次（最后 09-29T02:11:52Z），腿 1 filled 16；09-29T02:13Z 后 0 笔入场订单 | `trading/entry_execution_projection.py:44-104`；`entry_plan_execution.py:461-685` | Capture compilation | 上游阻断 |
| PRICE_FIRST（被动单挂到计划结束） | WIRED_UNOBSERVED | 09-25 以来 12 个，无一靠持续挂单成交 | `trading/entry_capture.py:13` | — | #2901 |
| FILL_FIRST（腿 0 直接 IOC） | DORMANT | 假设中 0 次 | `entry_capture.py:14` | — | — |
| Risk DEFER 15 秒退避 + 确定性失败不重试 | LIVE | 10-02 后 internal_claim_acquired 291、waiting_risk 262；修复后 fast_trigger_watch 告警 0 条 | `fast_trigger_watch.py:41-45,759-782,256-281` | #3798 | #3798 |
| 看守事件循环在订阅变化时的健壮性 | BROKEN | errors.log 09-29→10-09 `Fast Trigger stream restarting: AttributeError` 10 次 | `fast_trigger_watch.py:806,825-835,853-856` | — | 无对应 issue（D0） |
| 按阶段入场延迟 p50/p95（附样本数） | MISSING | 67,170 条 `entry_execution_stage` 审计无读者 | 仅写入端 `gateway.py` | Pre-broker submission authority（契约来源 #3096，已关闭） | 无对应 issue |
| 遗留 fast_trigger 配置（mode/max_setups/ttl） | DORMANT | 只参与重载身份，不控制行为 | `config/schema.py:1856-1867` | 一功能一实现 | 无对应 issue（删除） |

**应该有但没有**：条件触发有运行证据与"模型为何从不选它"的诊断（#3860、#3850）；契约「Maker lifetime」写的 `entry_plan_maker_timeout` 与代码 `passive_window_elapsed` 不一致（D0 文档）；p50/p95 延迟读者；已成交计划如实标 filled；#2901 验收；提交前新鲜度/身份/先风控后执行一律不放宽（拒绝归因 §5C）；删除遗留配置。

**交接**：Compiler → 本段接通，但输入稀少且只有 direct + MAKER/PRICE_FIRST 两种形态。本段 → 提案接通。

### 1.5 提案：不可变下单意图与身份链

**目标**：每个计划的每条腿只生成一条不可变的 native Proposal，带完整的候选/机会身份和冻结的开仓假设，交给 Gateway 与 Risk；计划的终态如实反映各腿的结果。

| 能力 | 状态 | 证据（Fedora） | 代码 | 契约 | Issue |
|---|---|---|---|---|---|
| 生成 native Proposal（每计划每腿唯一） | LIVE | 601 个计划有 Proposal；近 7 天 29 个（error 22、rejected 7） | `entry_plan_execution.py:303-427,640-685` | — | — |
| Proposal 携带候选/机会身份 `opportunity_id` | BROKEN | 交易所路径持仓与提案 41/41 为 NULL（见 §3.7 第 1 条） | `entry_plan_execution.py:372` | ACTIVE_RUNTIME_SCOPE「Enforced boundaries」要求保留 opportunity 祖先 | #3644 |
| 开仓假设冻结 `position_hypothesis/v1` | BROKEN | 见 1.10（bitget 41 个 Proposal 中 0/41） | `storage/proposal_hypothesis.py:236-246` | profit_loop_observability | #3644（同根） |
| 计划终态如实反映"已成交" | BROKEN | 有成交入场订单的 42 个计划中 40 个标为 `cancelled/entry_capture_terminal`（步长向下取整后成交量永远 < target） | `entry_execution_projection.py:54-56,78-79`；`fast_trigger_watch.py:477-482`；`entry_plan_execution.py:580-583` | Plan authority and leg accounting | 无对应 issue（D0） |

**交接**：触发 → 本段接通。本段 → Gateway/Risk 接通（部署前 05:01Z 有 Proposal 并同秒得到 Risk 决定；部署后 08:17–08:39Z 5 个 Proposal 都得到了决定），端到端近 10 天断在下游。

### 1.6 风控：Risk Engine

**目标**：在一次带锁评估里对每个 Proposal 给出 APPROVED/DEFER/REJECT，持久化带策略身份的 RiskDecision 和下单前预留；批准之前任何订单都不能发出。代码入口见「SYSTEM_MAP：Risk 日损检查或禁用状态不符」。

| 能力 | 状态 | 证据（Fedora） | 代码 | 契约 | Issue |
|---|---|---|---|---|---|
| 单一入口 + 串行锁 + preflight/authoritative 两段 | LIVE | 全量 preflight 22172（批准 544）、authoritative 1176（批准 837）；近 7 天 preflight 291、authoritative 22 全批准，最后 10-08T14:28:30Z。rev 10（05:55:32Z）后到 08:48Z：authoritative 批准 6、preflight 批准 6、preflight 拒绝 2 | `risk/engine.py:949-1070,1886-1908`；`gateway.py:1554-1605` | risk_engine_contract §1、§3 | — |
| 策略身份（id/revision/hash）+ 未注册模式 fail-closed | LIVE | rev 10 之前的决策都是 rev 8；rev 10 后 14 条决策都带 `policy_revision=10` | `engine.py:1086-1101,1847-1858` | §1.1 | — |
| DEFER vs REJECT 分类 | LIVE | AVAX d886d4359ec0 114 秒内评估 264 次（263 次 stale DEFER）；10-09 的 7 次 regime 拒绝都是 REJECT | `engine.py:919-946,1022-1035` | §2 | — |
| DEFER 重试退避（固定 15s） | WIRED_UNOBSERVED | 10-08 部署后（换版本前）无 DEFER 型决策 | `fast_trigger_watch.py:45,762-771` | #3798 | #3798（15s 对持续数小时的 stale 仍约 240 次/小时） |
| #0 全局 kill switch + 模式刹车 | LIVE | audit 5 次人工启停（最近 09-12 激活、09-23 解除）；当前全 False | `engine.py:1110-1119`；`risk/kill_switch.py:47,116` | §4 | — |
| #8 日损 + 自动触发 | DORMANT | demo 与 live 都是 `max_daily_loss_pct=0.0`；历史 113 次（最后 09-24） | `engine.py:1429-1455` | owner 决定 2：demo 禁用 | live 也是 0，待决定 8/9 |
| #5 回撤缩放（对 demo 也生效，peak 只取最近 50 条快照） | WIRED_UNOBSERVED | 当前回撤 0.02%，缩放 1.0 | `engine.py:1276,2038-2122` | 与 owner 决定 2（demo 不启用 H2）冲突 | 无对应 issue |
| #1 品种过滤 / USDT | WIRED_UNOBSERVED | 相关 token 0 次 | `engine.py:1121-1132` | §2 #1 | — |
| #1a Entry Plan 权限复核 | LIVE | max_risk_exceeded 5、max_notional 5、expired 1 | `engine.py:292-486` | §2 #1a | — |
| #2 完整计划 + 几何 + R:R 包络 | LIVE（纵深防御） | incomplete 1（08-07），其余 0 | `engine.py:1134-1194` | §2 #2 | — |
| 止损下限复查（与 Compiler 同函数） | LIVE | 34 次（最后 09-11） | `engine.py:1196-1230` | 契约表漏列 | 随 #3850 |
| #3 Live 状态与身份 | WIRED_UNOBSERVED | live 评估 0 次 | `engine.py:1232-1262` | §2 #3 | — |
| #4 权限模式 | WIRED_UNOBSERVED | 0 次 | `engine.py:1264-1267` | — | — |
| #5 单仓保证金上限 | WIRED_UNOBSERVED | demo 上限 100000 不起约束 | `engine.py:1269-1297` | — | — |
| #6 持仓槽位 + 同标的去重 | LIVE | symbol_already_open 6 次（最后 09-24） | `engine.py:1299-1389` | — | — |
| 下单前预留 + 权限完整性 | LIVE | reservation_authority_invalid 9 次；当前 0 行 | `engine.py:1037-1067` | — | — |
| #7 做空开关 / 现货禁空 | LIVE | spot_short 6 次 | `engine.py:1391-1404` | — | — |
| #9 交易所白名单 + 账户模式 | WIRED_UNOBSERVED | 靠 `exchange.name=paper_internal` 才对上 | `engine.py:1457-1469` | — | 隐性陷阱，无对应 issue（随 paper_internal 退役） |
| #10 杠杆上限 | WIRED_UNOBSERVED | 0 次；rev 9 后无评估 | `engine.py:1471-1477` | 决定 4 | futures_cap 重复 |
| #11 数据新鲜度（demo 1800s） | LIVE | 全量 7523 次，近 3 天 263 次；当前 30 分钟内只有 12 个 perp 有快照 | `engine.py:885-906,1559-1566` | §2 #11 | 触发与风控看的新鲜度来源不一致，无对应 issue |
| #12 总敞口 | DORMANT | demo `max_total_exposure_pct=0`；live 5% | `engine.py:1490-1506,1568-1595` | §1.1 | owner 决定 9 |
| #13 单笔止损风险 | LIVE | 4 次（最后 09-30）；部署后另有 2 次来自重复实现的浮点错拒（见 1.3） | `engine.py:1508-1516,1545-1556` | 决定 2 | 两份实现，见 1.3 |
| #14 Live family 证据门（胜率 ≥50%） | WIRED_UNOBSERVED | live 0 次；实测胜率约 22% | `engine.py:1519-1543,2370-2437` | 与 owner 净期望口径冲突 | 决定 9 |
| #14b Demo 入场质量（置信度/sideways/反向） | LIVE | regime_confidence 57 次（最后 10-09T05:01Z），rev 10 起关闭；sideways 0 次 | `risk/demo_entry_quality.py:66-201` | owner B2 | #3663；`schema.py:826-827` 默认值仍 0.60（#2971） |
| #15 Lesson 约束 | WIRED_UNOBSERVED | lesson_* 全历史 0 次 | `engine.py:1613-1616,1916-2036` | §2 #15 | 见 1.11 |
| #16 组合相关性（行业 + beta） | LIVE | portfolio_beta 10745 次（09-29 重试风暴）；sector 134 次（最后 08-25） | `engine.py:2176-2333` | — | #3846（已关闭，PR #3872 已合入 master、未部署）、#3798 |
| #17 账户杠杆比 | LIVE | 2 次；equity_unavailable 289 次（09-25..27） | `engine.py:2460-2634` | — | — |
| #18 爆仓缓冲 ≥5% | WIRED_UNOBSERVED | 0 次（Compiler 先挡） | `engine.py:2636-2664` | H5 | — |
| #19 同标的累计名义（未融资计划） | DORMANT | 现在的计划都带融资，本关置 0 | `engine.py:1642-1695` | — | 应删除 |
| #20-#22 已融资计划一致性 | LIVE | DRIFT 4 次；其余 0 | `engine.py:161-289` | sizing 合同 | #3844 |
| 持仓期保证金告警/阻断比例 | DORMANT | 配置有值，无运行时消费者 | `config/schema.py:622,683,786` | — | #2846 |
| 账户级 H1/H2 真高水位/同方向合计风险 | MISSING | — | 无 | 决定 2/8（draft） | 无对应 issue（决定 8） |

**应该有但没有**：§2 全部关卡有 approved/rejected 测试；止损下限复查写进契约；H5 太宽时降杠杆；H6；demo 阶段 H1/H2/H3 全关、上实盘一起开（需要按模式的开关）；上实盘账户护栏（决定 8）；Live 上限按 500U 重标定（决定 9）；转实盘门槛（≥100 笔、净期望 95% 下界 >0）；DEFER 重试间隔与导致暂缓的事实刷新周期对齐（拒绝归因 B1）；安全底线一律不放宽（§5C）。

**交接**：上游 Proposal → 本段接通（7 天 291+22 次）。本段 → Gateway 接通，但下游断：部署前 22/22 批准后死于身份闸门（orders 0 行）；部署后 3 个批准的提案生成了订单，3/3 失败（见 1.7）。

### 1.7 执行：Gateway / Coordinator / UTA / Bitget

**目标**：把已批准的 Proposal 变成真实交易所订单，持久化 Order/Fill/Position 因果链，保证幂等、身份连续、每个非终态有有界出口，并让止损真实挂在交易所侧（#3848 owner D1/D1b）。代码入口见「SYSTEM_MAP：ACK / Fill / Position / Close 不一致」与「入场订单长期非终态 / 入场闸门拒绝全部新入场 / 计划多而无成交」。

| 能力 | 状态 | 证据（Fedora） | 代码 | 契约 | Issue |
|---|---|---|---|---|---|
| Gateway 单一入口 + 执行租约 | LIVE | 10-08 起 claim 291、preflight 291、authoritative 22；租约当前 0 行 | `execution/gateway.py:178,288,1535` | execution_contract §1、§5 | — |
| 交易所数量规则归一 + Risk 复核 | LIVE | quantity_normalization_completed 22 | `gateway.py:142,90` | §3.3 | — |
| 品种可用性预检 | WIRED_UNOBSERVED | `proposal_skipped` 0 行 | `gateway.py:836` | §3.2 | — |
| 提交前账户身份闸门 | 未核实（修复前 BROKEN） | 10-08 22 个 Proposal 死于 `uta_account_identity_mismatch`；修复 10-08T16:07 部署后 identity_blocked=[]。部署后 3 张入场单都写出了订单行；其中 2 张的 `command_not_sent` 是否来自身份或能力检查未核实，见下一行 | `uta/identity.py:39`；`execution/coordinator.py:568-591,251,196` | §3.0b | #3798、#3757；#3806（已关闭） |
| Bitget uta_v3 下单（post_only/IOC/杠杆/margin） | **BROKEN（新出现，调查中）** | 部署后止盈止损开启，前 3 张入场单 3/3 失败：`a69db25da1c7`（BNB，08:17:52Z）与 `5de22a0ebe27`（SNDK，08:19:43Z）为 `error/command_not_sent`，`b03c5299249f`（SOL，08:21:52Z）为 `submission_rejected/venue_rejected_command_25206`；对应计划 `e51d1c3acdec`、`501fba241950`、`b82c987b6ac1` 均为 `failed/demo_order_failed:*`；三张单的 `uta_command_applied` 审计都带 `"native_tpsl_enabled": true`。#3880 的结论：SOL 的 25206 是 Bitget 码"交易价格不能偏离超过 {1}%"，因为 demo 现货 SOL/USDT 盘口与主网脱钩（demo 约 30–44.5，主网约 110），而我们按主网报价定价——已查实；两张期货单的 `command_not_sent` 发生在 `place-order` 之前，适配器当时丢弃了异常，**原因仍未查实**（领先假设是 `set_leverage` 失败，未证明）。PR #3881 已在 master 让发送前失败保留原因码，未部署。此前：最后一张订单 2026-09-29T06:20:54Z，历史 taker 79、maker 3，身份都是 classic_v2 | `extensions/exchanges/bitget/profile.py:129,142,159`；`ccxt/adapter.py:1214` | §3.0a | #3880 |
| 入场撤单收敛 | WIRED_UNOBSERVED | entry cancelled 80（最后 09-29） | `gateway.py:553`；`coordinator.py:926,379` | §3.0a | — |
| submission_unknown 不重发 | WIRED_UNOBSERVED | 当前非终态 0；inconclusive 1752 条（最后 10-08T16:05） | `gateway.py:979,1434`；`coordinator.py:401` | §1、§5 | — |
| 订单对账 reconcile | LIVE | 连续 failed 17 次后恢复；order_reconciled 81 次（最后 09-29） | `execution/reconciliation.py:2087`；`scheduler.py:2348` | §6 | SYSTEM_MAP 写"仅live"，属文档漂移（已在同期文档修复中更正） |
| 入场侧 stale_order_cleanup 有界终止 | BROKEN（当前构建未观察） | 部署前两次运行都在 `stale_order_cleanup.py:155` 记 `exact UTA evidence unavailable for bitget_demo (ValueError)`（最后一次 10-09 北京时间 09:05:31，即 01:05Z）；仅有 4 次终止是 owner 手工退役。部署后到 08:48Z 未再出现，但这段时间不足以说明已修好 | `execution/stale_order_cleanup.py:122,220,339` | §6.1 | #3798、#3565；#3820（已关闭，fills 归属修复已部署）不覆盖这条 ValueError，属于残留，无对应 issue |
| 执行活性探针 | LIVE（覆盖不全） | 10-08T16:07 告警 overdue/identity；`funnel_is_dead=True` 只进 doctor 不进 health | `execution/liveness.py`；`agent_runtime/health.py:436-447` | §6.1 | #3087 |
| 入场附带 TPSL 发送前守卫 + ACK 确认（PR #3857） | WIRED_UNOBSERVED（已部署并开启） | 已部署；`~/.aletheia/config.yaml` 已无 `exchange_tpsl_enabled` 键，`config/schema.py:1640` 默认 None 表示只对 bitget_demo 开启；部署后 3 条 `uta_command_applied` 都带 `"native_tpsl_enabled": true`，但 3 张单都没成交，ACK 确认还没有一次成功样本 | `ccxt/adapter.py:1246-1293`；`coordinator.py:605-617` | §4 | #3848 |
| 入场 preset 回读记账 | WIRED_UNOBSERVED | 开关已开；部署后 0 笔成交，verified/absent 均 0 | `portfolio/native_protection.py:38` | §4 | #3848 |
| 原生 TPSL 触发成交识别 | WIRED_UNOBSERVED | 开关已开但无持仓；历史 exchange_tpsl_order_id 0/41，28 次止损全是本地轮询 + 市价单 | `scheduler.py:2634,2652` | §10.4 | #3848、#3619 |
| 收紧/追踪止损同步到交易所 | BROKEN（部署版本；PR #3879 已在 master 修复，未部署） | 跟踪止损路径调用 `replace_exchange_protective_stop` 时丢弃返回值、照样记 `exchange_synced=True`（`position_manager.py:776-780`）；入场 preset 止损在 #3848 PR-B 之前按设计不替换（`coordinator.py:1046-1052` 返回 False），所以原生止盈止损开启后，任何收紧都不会落到交易所。master 上 PR #3879 改为对交易所策略单原地修改（含入场 preset），修改失败保留原 id 并返回 False，跟踪止损改用修改结果 | `coordinator.py:1028-1084`；`position_manager.py:776-780` | §4 | #3848 PR-B |
| preset 原地修改（modify-strategy-order） | MISSING（部署版本；PR #3879 已在 master 实现，未部署） | — | 部署版本无；master 见 PR #3879 | #3848 PR-B | #3848 |
| 独立保护单链 | DORMANT（部署版本；PR #3879 已在 master 删除） | 无生产调用方 | `coordinator.py:930,981`；`gateway.py:814`（基线行号，master 已删） | #3848 PR-B 3 | #3848 |
| 统一平仓中枢 | WIRED_UNOBSERVED（历史 LIVE） | 平仓单 filled 83；另 3560 张 `uta_adapter_unavailable_before_submission`（09-26 前） | `coordinator.py:755,806,735` | §10.2 | #3507 |
| 消失确认 → verified exit | BROKEN | 14 条 pending 孤儿（对应仓位早已正常平仓） | `portfolio/disappearance_confirmation.py`；`disappearance_settlement.py:252-267` | §6、§11.1 | 无对应 issue |
| Live 执行 / Live 人工平仓 | MISSING（按设计） | Live 不在运行范围 | — | ACTIVE_RUNTIME_SCOPE | — |
| OKX/Binance profile | DORMANT | okx_demo_symbols 每 4h 仍在刷新 | `extensions/exchanges/okx/`、`binance/` | 冻结 | — |

**应该有但没有**：交易所原生止损作为主保护并在 Fedora 实证（决定 1、#3848）；ACK 确认、回读只记账（决定 1b）；收紧/追踪对 preset 原地修改，失败如实报 `exchange_synced=False`（#3848 PR-B）；删除或接通独立保护单死链；确认 Bitget 平仓后撤兄弟腿；每个非终态有不依赖被关闭闸门的有界出口（§6.1）；入场漏斗死亡在 health 可见（#3087）；平仓滑点实测（#3618）；按标的分级杠杆在交易所真实 set_leverage。

**交接**：上游 → 本段接通（291 次进入 claim；部署后 3 次进入提交）。本段 → 持仓：历史通（83 入场 filled），09-29 后无新成交，部署后 3/3 提交失败；保护 id 交接（exchange_tpsl_order_id）从未写过（0/41）。

### 1.8 持仓管理：开仓后每一轮做什么

概要见本表，**详细分析见 §2 专章**。

**目标**：开仓后持续执行 ExitPlan，并定期让 AI 重新复查这笔仓位、给出新指令（持有、调整、减仓、平仓），留下可审计记录，最终交给平仓/结算。

| 能力 | 状态 | 证据（Fedora） | 代码 | 契约 | Issue |
|---|---|---|---|---|---|
| 确定性持仓循环（不调 LLM） | LIVE（最后有仓位在 09-29） | 41 笔 bitget_demo 平仓：stop_loss 28、take_profit 7、max_hold 2、人工 3、外部 1；当前 open=0 | `scheduler.py:1429,2505`；`portfolio/position_manager.py:1247,891` | execution_contract §10 | — |
| Layer-0 灾难止损 / 强平距离 | WIRED_UNOBSERVED | bitget 0/41；paper 历史 2 次 | `position_manager.py:846,858,1398-1405` | — | — |
| HORIZON_DUE 唤醒（AI 复查主入口） | LIVE | 41/41 有 horizon；21/41 被唤醒；rearm 26 条；46 次复查中 45 次由它触发 | `position_manager.py:1155,1070,1227`；`wake_events.py:31,45` | §10、DL-INV-20 | — |
| Operator 复查 `review_portfolio_state` | LIVE | bitget 46 次：hold 41、adjust 4、close 1；全库 343 次 | `agent_shell/commands.py:2045,1884`；`pi_daemon.py:604-606` | agent_contracts | — |
| 不看时长的定期逐仓复查 | MISSING | bitget 41 笔中 20 笔 AI 从未看过 | `pi_daemon.py:451-455,604-612` | owner §5b-3"以后再讨论" | 无对应 issue |
| hold 自动滚动 horizon | BROKEN | 6 次 15 分钟内重复复查；HYPE fd7149cc1c7a 2 分钟内 64.3h→96.59h | `commands.py:1537-1568,1624-1700,2196-2231` | 与 prompt 及 §10 不一致 | 无对应 issue（D0） |
| AI 调整止损/止盈/时长 | LIVE | operator_adjust_position 164 条（全库）；bitget 4 次 adjust 中 2 次被拒（会放宽保护） | `position_manager.py:1747,1829-1840` | — | — |
| 改止损后同步交易所 | BROKEN | 8ddff135155c 收紧后本地生效、交易所未改。接口本身如实：`apply_operator_adjustment` 返回 `updated: True` 加上同步失败时为 False 的 `exchange_synced`（`position_manager.py:1945-1950`）；缺陷在调用方 `commands.py` 与 prompt 都不读这个字段，AI 以为止损已落地。PR #3879 修好了同步本身，但调用方仍不读 `exchange_synced`（master `cda9cfeaa` 复核） | `position_manager.py:1907-1950`；`agent_shell/commands.py:2187,2215` | — | #3619、#3848 |
| 原生保护回读 | WIRED_UNOBSERVED | 10-08 部署后与 `99a22066` 部署后都是 0 持仓 | `native_protection.py:38` | — | #3848 |
| 跟踪止损 / 保本 | DORMANT | trailing_json 0/41（paper 93/419） | `position_manager.py:638-662` | — | #3395 |
| 逆向市场信号唤醒 | BROKEN（输入断流） | `opportunities` 最后 2026-08-27；bitget 0/41 | `position_manager.py:1289-1320,1424-1520`；`market_signal_reader.py:211-226` | agent_contracts L310 | #2931、#2936；#3572 旁证 |
| 论点失效唤醒（仅 serenity） | DORMANT | 41/41 是 short_term | `position_manager.py:950-965,1048` | — | — |
| 接近止损/止盈/灾难三种唤醒 | DORMANT | 定义了但全仓无发射点 | `wake_events.py:28-48` | — | 无对应 issue |
| 硬时间上限 min(3×horizon,72h) | LIVE | max_hold 2 笔（fd7149cc1c7a 恰 72.0h；KORU 卡 pending close 115.18h） | `position_manager.py:921-947` | owner §5b-3 | — |
| 卡住的平仓 claim 升级 | LIVE | KORU d73ba8665eab：09-24 升级、09-27 转人工 + P1 唤醒 | `position_manager.py:113-293,1324-1367` | §10.2.4 | #3719、#3568、#3565 |
| 部分减仓 / 分批止盈 | DORMANT | 无调用方 | `position_manager.py:433` | §10"遗留 reduce_exposure 归一为 hold" | #3395、#3860 |
| AI 挂限价离场 | DORMANT（只开放给人工） | human_manual_close 3；operator_limit_exit 0 | `position_manager.py:1953`；`server/routes/data.py:1427` | — | — |
| 复查时给 AI 的上下文 | WIRED_UNOBSERVED（内容太少） | `position_path_observations` 24928 行未进 prompt | `commands.py:1958-1985,2019-2041` | — | 无对应 issue |

**交接**：执行 → 本段：历史通，近 10 天无输入；交易所 TPSL id 0/41。本段 → 平仓：通（`close_position_routed` 唯一入口，41/41 `exit_price_source=broker_fill`）。本段 → 复盘/规划：**断**（复查理由不回流；路径证据不进 prompt）。

### 1.9 平仓 / 对账 / 结算

**目标**：把每一次退出（本地保护、AI close_one、人工、原生 TPSL、外部消失）收敛成"已验证的平仓 + 成本完整的结果"；没证据绝不伪造成交或 PnL；每个平仓 claim 有界、可观测、由有权限一方结案。

| 能力 | 状态 | 证据（Fedora） | 代码 | 契约 | Issue |
|---|---|---|---|---|---|
| 原生平仓提交（唯一平仓中枢） | LIVE（当前构建零执行） | close Order native/market/filled 40 行，最近 09-29T06:20:54Z | `position_manager.py:362`；`coordinator.py:755,806` | §10.2 | 上游 #3798/#3757 |
| 本地退出触发器与原因分类 | LIVE | 41 笔：stop_loss 28、take_profit 7、人工 3、max_hold 2、verified_external 1 | `position_manager.py:858,891`；`analytics/outcome_integrity.py:24-30` | §10.1 | 无统一退出原因枚举，无对应 issue（低） |
| 私有 Fill 平仓物化 + 经济提交 | LIVE | 41/41 broker_fill、exit_fill_verified=1；position_closed 39 条 | `execution/exchange_materialization.py:1309,1556`；`portfolio/pnl.py:737,805` | §11.3/§11.6 | — |
| 订单对账 | LIVE | order_reconciled 78；inconclusive 13758（09-20 起） | `reconciliation.py:1956` | §6 | 身份族 #3798 |
| 4h stale 升级 | LIVE | 2 条 | `position_manager.py:113` | §10.2.4 | — |
| submission_unknown 24h 终态界 | LIVE | 1 条（KORU 69h） | `position_manager.py:293` | §10.2.4 | — |
| 人工终态结案工具 | WIRED_UNOBSERVED（被绕过） | `manual_close_claim_resolved` 0 条；实际靠 `repair_koru_stuck_order` 等脚本（actor=system） | `scripts/maintenance/manual_close_resolver.py:76,156` | §10.2.4 要求 actor=human | #3719、#3568 |
| absent stale close 自动释放 | WIRED_UNOBSERVED | 已部署（7a0eead0），09-29 后无 claim | `pending_close_recovery.py:767` | §6 | #3719 |
| submission_rejected 过期 claim 释放 | DORMANT | 无生产调用者 | `pending_close_recovery.py:919` | §10.2 | #3719 |
| Bitget 40725 停泊 + owner 解锁 | WIRED_UNOBSERVED | 无样本 | `close_convergence.py:30`；`server/routes/data.py:1467` | §10.2 | — |
| 提交前失败的平仓重试上界 | BROKEN | 3560 条 `uta_adapter_unavailable_before_submission`，只涉及 3 个仓位（09-25T20:30Z→09-26T11:47Z） | `coordinator.py:832-842`；`close_convergence.py:16-29` | §10.2 | #3507 |
| 仓位对账 | LIVE（空跑） | 10-08 起 completed 262 次、失败 0 | `portfolio/position_reconciler.py:218,1396` | §6 | SYSTEM_MAP:87 输出表名漂移 |
| 消失确认（两次独立完整视图） | LIVE | candidates 15、observations 221 | `disappearance_confirmation.py:30,137,239,266` | §6、§11.1 | — |
| 消失后三态直查 + 行政结算 | LIVE（防线有效，结果不可见） | 09-29 有 5 个 candidate ready 3 小时但仓位仍在，均未被误关；run 输出仍是"no action needed" | `disappearance_settlement.py:172`；`outcome_closure.py:58,148,243` | §11.1-§11.3 | 无对应 issue（可观测性） |
| 消失 candidate 生命周期收尾 | BROKEN | 14 个 pending 指向已关闭仓位；指纹变化导致 5 个仓位各两个 candidate | `disappearance_settlement.py:182-183`；`disappearance_confirmation.py:115` | §11.1 | 无对应 issue |
| 消失路径 verified_strategy 分支 | DORMANT | 无生产调用者 | `outcome_closure.py:243-296` | §11.3 | #3848（原生止损上线后会漏样本） |
| 原生 TPSL 成交桥 | WIRED_UNOBSERVED | exchange_tpsl_order_id 0/41 | `scheduler.py:1438,2609,2652` | §4、§10.4 | #3848、#3619、#3005 |
| 部分平仓 / 分片平仓物化 | WIRED_UNOBSERVED | is_partial=1 为 0 行 | `exchange_materialization.py:661` | §10.2 | — |
| 策略性部分减仓 | DORMANT | 按设计 fail-closed | `position_manager.py:433` | — | #3395 |
| Broker funding 结算 | LIVE | 41/41 broker_ledger；最近 09-28T14:45:54Z | `portfolio/broker_funding_settlement.py:61,729` | §11.6 | — |
| funding 历史过期终止 | WIRED_UNOBSERVED | 0 行 | `broker_funding_settlement.py:510` | §11.6 | — |
| 退出费用后补 | WIRED_UNOBSERVED | exit_fee NULL 0/41 | `broker_funding_settlement.py:198,275` | §11.6 | — |
| funding 审计 outbox 镜像 | LIVE | mirrored 11、NULL 4 | `portfolio/funding_audit.py` | §11.6 | — |
| Outcome Integrity 三口径 | LIVE | eligible 39、administrative 1、missing_attribution 1 | `outcome_integrity.py:389,526,646,654` | §11.4 | — |
| Outcome hook 重放 | LIVE（但 shadow 未写入，见 1.10） | shadow_trades 类 completed 457，最近 09-29T06:22:02Z | `portfolio/outcome_hooks.py:45` | §11.3 | #3572 |
| 已实现滑点成本 | BROKEN | slippage_cost NULL 41/41 | `portfolio/pnl.py:1031` | §11.4 | #3618、#3621 |
| 已知经济分量与最终 PnL 分离 | WIRED_UNOBSERVED | 41/41 完整，未起作用 | `pnl.py:1337` | §11.4 | #3135 |
| Live 消失自动结算 | DORMANT | live 禁用 | `position_reconciler.py:1586-1600` | §11.1 与 spec PL-LDS 矛盾 | #3836（D1） |
| Live 人工平仓 | MISSING（按设计） | — | — | §10.5 | — |

**应该有但没有**：原生止损触发的平仓按 strategy 物化并带正确 SL/TP 归因（决定 1/1b、#3848；竞品 OpenAlice/AstraQuant）；所有平仓尝试有上界（#3507）；终态歧义只经人工工具结案；滑点作为成本记录（#3618）；"敞口已关/已知 PnL/成本完整"分开（#3135）；策略性部分减仓（#3395）；Live 消失只告警或 owner 改合同（#3836）；消失 candidate 结案 + 结算结果进 job 输出与健康面；统一退出原因登记表。

**交接**：上游四路输入（平仓请求、交易所事实、入场身份、人工结案）在当前构建上都是空的。本段 → 结果/复盘：接通（hook 457 completed），但滑点恒 NULL、消失路径成交恒为 administrative、结算结果不进健康面。

### 1.10 结果度量与归因

**目标**：每个已平仓 Position 算出经核验、扣完成本的净结果（PnL、R、路径、管理动作），按候选、方向、几何、执行、持仓、退出、成本逐层归因，给 owner 可信的胜率/期望/放大门槛。本段只读，无交易权限。

| 能力 | 状态 | 证据（Fedora） | 代码 | 契约 | Issue |
|---|---|---|---|---|---|
| owner 定义的胜率（每 Position 合格净 PnL>0） | LIVE（数据停更 10 天） | 07:41Z go-live：23.1%（9/39），净 −17.008 USDT，PF 0.41，red | `analytics/outcome_integrity.py:172,226,646`；`go_live_scorecard.py:76,273` | ACTIVE_RUNTIME_SCOPE genuine outcome | — |
| 多套"胜率"并存 | BROKEN | `/round1-progress` 混合 paper 与 bitget 460 笔得 35.0%；Operator 看到的是候选 24h 口径 | `server/routes/analytics.py:1024-1050`；`agent_shell/trading_context.py:48-63` | owner 唯一定义；#1939（2026-08-25 已关闭，但混模式统计仍在） | #1939 已关闭，当时没有其他 issue 跟踪 |
| go-live 最大回撤 | BROKEN | 显示"4914.8% drawdown"（分母是累计 PnL 峰值不是权益） | `go_live_scorecard.py:288-312` | 研究 H2 按权益 | 无对应 issue |
| 每笔净 R / MFE_R / MAE_R | BROKEN | `opportunity audit --view entry-exit` 39/39 `proposal_chain_identity_or_hash_invalid`（2026-08 的 737 条提案哈希失败拖垮整条链）；手工估算 R 均值 −0.559 | `analytics/entry_exit_geometry.py:283-286,319-359` | Phase1 §4 主指标 | 无对应 issue（D0）；#1256 相关 |
| Position Story / Replay 经济结果 | BROKEN | 39 个合格仓位中 30 个 `economic_path=unavailable`（资金费审计事件只覆盖 11 个） | `position_lifecycle_story/readers.py:131`；`service.py:1201-1216` | profit_loop_observability | #3135 相邻，无对应 issue |
| 开仓假设冻结 `position_hypothesis/v1` | BROKEN | bitget 41 个 Proposal 中 0/41；10-01 后 0/29 | `storage/proposal_hypothesis.py:236-246` | profit_loop_observability | #3644（同根） |
| ExitPlan 修订历史 | DORMANT | `position_exit_plan_versions` 1303 行全是 paper（最后 09-06），bitget 0 行 | 无写入者；读取方 `position_replay.py:646` 等 | Story 词汇含 ExitPlan revisions；persistence 合同 :41 定为只读历史 | 无对应 issue |
| 持仓管理动作归因 | WIRED_UNOBSERVED | position_reviewed 45 节点；19 个仓位 `management_linkage_not_recorded` | `position_lifecycle_story/service.py` | — | 无对应 issue |
| Position Path Observer（5m 路径） | BROKEN | 07:34Z 覆盖 2474/6423 桶，available 43，滞后 1,143,838s；09-24 后 available 28、unavailable 2396；bitget 19/41 零观测 | `portfolio/position_path_observer.py:605,840-866` | 失败与恢复语义 | 无对应 issue |
| 有序 1m 路径与受保护保留 | MISSING | bitget 1m 只保留约 30 小时；Story 39/39 `ordered_1m_path_unavailable` | 无 | 合同的保留触发条件已出现 | #3123 相关 |
| 候选固定持有期净结果 | LIVE | 近 7 天 4h settled 5493（均值 −0.208%）、24h 460（均值 −2.84%）；24h 无未选对照 | `universe/outcome_recorder.py` | opportunity_outcome_contract | #3786、#3652、#3655 |
| 反事实账本 shadow_trades | DORMANT（墓碑仍被读） | 37968 行，最后 2026-08-27 | `alpha_attribution.py:755-810` | counterfactual_contract :48 与 :117 矛盾 | #3572 |
| 五层 alpha 归因 | WIRED_UNOBSERVED | 五层 insufficient_data，sample 0 | `analytics/alpha_attribution.py:72` | ALPHA_ATTRIBUTION_AUDIT | #3570、#3644、#2907 |
| profit-loop 30 天锁定窗口 | BROKEN | 227 个 epoch，最长 10.31 天 | `extensions/learning/evidence_epoch.py` | profit_loop :263 | #3041 |
| Exit breakeven shadow | DORMANT | 最后 2026-08-11 | `portfolio/outcome_hooks.py:20-42` | — | #782、#3395 |
| 平仓滑点 / 执行拖累 | BROKEN | cost-summary 滑点 39/39 缺失 | `outcome_closure.py:95`；`pnl.py:1031` | exactly-once 成本扣减 | #3618、#3621、#3619 |
| go-live scorecard | LIVE | composite 37.7，red | `go_live_scorecard.py:36,88` | live_unlock_contract | #3534、#1968（已关闭，PR 已合入 master、未部署） |
| Post-trade reviewer 写 knowledge | LIVE | 46 条，最后 09-29T06:27:53Z | `extensions/research/post_trade_reviewer.py:243` | agent_contracts | #269 |
| 结果回流入场决策 | WIRED_UNOBSERVED | 每 Position 的结果没进 Planner；filter_controller_observe 自 08-11 失败 | `main.py:364-373`；`agent_shell/reflection.py:74-100` | learning_influence_default | #3396、#3786 |
| LLM 推理成本归因到 Position | WIRED_UNOBSERVED | runtime_cost_events 7 天 1835 行，估算 $2.11，proposal_id 非空 0 | `economics/inference_calls.py` | llm_inference_harness | #3066 相关 |

**应该有但没有**：胜率只保留 owner 一套定义并按 execution_mode 隔离（#1939 已关闭，问题仍在）；系统自算每笔净 R 及其分布、Wilson 下界、按"族 × 方向 × 档位"拆开（Phase1 §4）；放大门槛自动判定（≥100 笔、净 R 95% 下界 >0；研究 §5.2）；按权益算回撤和日亏（研究 §5.1）；平仓滑点归到执行层（#3618）；每次复查/改单留可归因修订和反事实增量；持仓期间 1m/5m 路径受保护保留；随机入场基准（#3858）；Demo A/B 停止条件；证据窗口不因无关部署重置（#3041）。

**交接**：平仓 → 本段：接通，但滑点、ExitPlan 修订、开仓假设、资金费审计（11/41）、路径这几条输入断或缺。本段 → 下游：go-live 接通（red）；trade-stats/cost-summary 接通（round1 混模式）；对交易行为按合同**不自动反馈**；每 Position 的结构化结果没有交给 Planner 或 Operator 的接线。

### 1.11 复盘 / 学习 / 反馈回下一轮

**目标**：把每个已平仓位和每个候选的反事实结果，变成经证据检验的经验（lesson、guard、校准、上下文），并通过声明的权限闸门改变下一轮的选币、规划和风控。

| 能力 | 状态 | 证据（Fedora） | 代码 | 契约 | Issue |
|---|---|---|---|---|---|
| 平仓复盘 + 确定性 case | LIVE | post_trade_reviewer healthy，最后 06:54:18Z；41 笔中 39 笔有 case；最后复盘 09-29T06:27Z；复盘 0 笔可按 position 关联 | `extensions/research/post_trade_reviewer.py:243`；`extensions/analytics/trade_case_builder.py:102,232` | learning_contract §1.1 | — |
| dreaming_daily 经验提取 | LIVE | 09-11 起 proposed 66 条；19 条 recommendation 是 `guard_shadow_unavailable`；hypotheses 09-01 起只新增 1 条 | `extensions/research/dreaming_agent.py:28-38,470-487` | §1.1、§2.3 | category 枚举不含 'risk' |
| lesson_reviewer 证据门晋级 | BROKEN | 日志 `deferred=40 (reviewed 40/133 proposed, 93 remaining)`（08:48Z 库内 proposed 136 条，差值是时间差）；最后晋级 2026-09-07T19:01:56Z，32 天 0 条；09-05 后 96 条从未被审 | `lesson_reviewer.py:48,50,358-372`；`repositories_research.py:77` | §1.1 | 无对应 issue（D0）；#2865 方向相反 |
| lesson → 风控硬约束（#3828 的 block_scanner；#3828 已关闭，修复 PR #3829 已合入） | DORMANT | category='risk' 0 条；7 条 applied 全部过期；lesson_* 违规 0（16,915 条决策） | `risk/engine.py:1613-1616,1912-2023`；`extensions/learning/risk_evidence.py` | risk_engine §2 #15 | #3828 的残留：没有 owner/manual 写入入口，无对应 issue |
| applied lesson → Operator 诊断上下文 | DORMANT | 未过期 applied 0 条 | `agent_shell/context.py:53-58,109-154` | §2.4 | #3396 |
| lesson → policy → draft skill | WIRED_UNOBSERVED | 最后 2026-09-07；无行为消费者 | `extensions/learning/skills.py` | §1.2（只到产物生命周期） | — |
| FTS 知识检索 / advisory | DORMANT | build_advisory_context 无生产调用方 | `extensions/knowledge/router.py:20,42,100-123` | SYSTEM_MAP 89/97/138 写它是消费者 | 文档漂移，无对应 issue |
| insight_synthesizer memo | WIRED_UNOBSERVED | 7 条（09-25 起），无决策消费者 | `extensions/analytics/insight_synthesizer.py` | §1.1"memo 进 observer prompt"不成立 | #1976 |
| risk_critic | LIVE（只作建议） | 96 条，最新 06:59:18Z | `extensions/research/risk_critic.py` | SYSTEM_MAP:90 | — |
| learned guard 候选与 TTL | WIRED_UNOBSERVED | active 366 / deprecated 1402；guard_promotion 自 09-29 起未跑（cron 错过、启动不补跑） | `filter_outcome_recorder.py:495-498`；`extensions/knowledge/guards.py:335-400` | §7 | #1780、#2514 |
| 入场规划学习上下文（Lab 处理组） | DORMANT | lab 回执 529 条全部 unassigned、applied=0；windows/units/exposures 0 | `trading/entry_analysis.py:399-421`；`extensions/learning/entry_context.py:655-694` | §1.2、§12；ACTIVE_RUNTIME_SCOPE:85-89 | #3570、#2866、#2737 |
| UFC 族乘数校准选币 | DORMANT | `get_family_multipliers` 只有测试调用 | `universe/allocator.py:62` | 合同写"因果闭环完整"，与代码不符 | #2928、#2912 |
| persona_sizing | DORMANT | rollouts 0 行 | `extensions/learning/sizing_evidence.py:25-34` | §1.2 unsupported | #3551 |
| 策略 epoch / cohort 身份 | LIVE（每次部署轮换） | 735 行；evidence_anchor 各约 230 个 | `extensions/learning/policy_epochs.py` | §1.4 | #3041、#3074 |
| Alpha 五层 + Universe Twin | BROKEN | 五层 sample 0；Twin 0/548 cluster 完整；opportunity_id 41/41 NULL | `alpha_attribution.py:1122-1124`；`entry_plan_execution.py:372` | ALPHA_ATTRIBUTION_AUDIT | #3644、#3570；#3569（2026-10-08 已关闭） |
| filter_controller_observe | BROKEN | 连续失败 4 次，last_succeeded 2026-08-11；但 recommendations 最新行 10-09T01:04Z，健康记录与产出对不上 | `scheduler.py:3348` | SYSTEM_MAP 调度表 | 无对应 issue |
| 反事实 shadow_trades / exit shadow | BROKEN | hook 457 completed，表最后 08-27 | `outcome_hooks.py` → `extensions/learning/outcome_feedback.py` | counterfactual_contract | #3572 |

**应该有但没有**：已接受 lesson 经证据门后在下一轮作为可审计上下文或约束生效（learning_contract 开篇）；owner/manual 类 risk lesson 写入入口（#3828 正文）；lesson_reviewer 公平轮转 + proposed TTL；至少一条 owner 准入的 Learning 影响路径有 manifest/exposure（ACTIVE_RUNTIME_SCOPE:85-89、#3570）；复盘按 mechanism/family 归因（#3644）；Twin 报出违反的 integrity 条款；按 R 复盘（研究 H4、#3848、#3850）；SYSTEM_MAP / learning_contract 消费者描述与代码一致。

**交接**：上游 → 本段：平仓与候选结果接通（09-09 起 24h settled 2601、4h 70,507），但 09-29 后无新平仓、41/41 缺 opportunity_id。本段 → 下一轮：四条设计输出中两条接线但输入为 0，两条没有生产调用方。**结论：2026-10-09 没有任何一条复盘或学习反馈改变选币、规划或风控。**

### 1.12 横切（不是生命周期阶段）：Operator 观测面与全生命周期可观测性

**目标**：让 owner 不写 SQL 也能看到一个 Candidate 从发现到 Outcome 的全过程，断在哪里能看出来，并有告警推送。代码入口见「SYSTEM_MAP：Position 故事或诊断展示不符」。

| 能力 | 状态 | 证据（Fedora） | 代码 | 契约 | Issue |
|---|---|---|---|---|---|
| `aletheia control status --json` | LIVE（无入场漏斗计数） | 07:35:44Z（换版本前）：running aa09a87c、cached master 45a733bd、不一致；08:45Z：running 99a22066、session e4013aa099a8；profit_loop collecting | `cli/control_cmd.py:25,1223,504,861` | status_surface_contract | — |
| `aletheia doctor --json`（71 项） | LIVE | 07:44:43Z healthy=False，7 项 warn；execution_liveness"36 个计划 0 下单"；审计链 133860 条有效 | `diagnostics/probes/trading.py:601,430-452` | — | `reconciled_ratio` 文案写死"since 2026-06-22"，无对应 issue |
| `/api/health` 与 UI 健康头条 | BROKEN | 07:44Z healthy、issues=[]，同时 24h 22 个计划 demo_order_failed、0 订单；08:48Z（部署后入场 3/3 失败）仍是 healthy、issues=[] | `agent_runtime/health.py:279,441-445`；`frontend/src/components/health-issues.ts` | AGENTS.md 不伪造健康 | #3087、#3060 |
| Lifecycle Inspector CLI | BROKEN | 已 TP 平仓的 d3f105a1ed48 报 `fill_missing_for_leg_0`；demo_order_failed 的 e9b50b6faa62 报 waiting | `analytics/candidate_lifecycle/inspector.py:124-130,171-178,238-264` | profit_loop Lifecycle diagnosis | #3281（三项缺口仍在）、#3757 |
| Position Story / Replay CLI | LIVE | d3f105a1ed48 12 个事件；evidence_gaps 4 项 | `position_lifecycle_story/service.py`；`cli/inspect_cmd.py:2188-2206` | — | #3281 |
| 持仓管理决策记录 | LIVE（只能 CLI 看） | pi_decisions：hold 41、adjust 4、close 1，最新 09-29T02:13:41Z | `position_lifecycle_story/events.py:89,100,149` | — | — |
| 持仓管理决策在 Dashboard | MISSING | 前端与路由无 position_review 消费者 | `frontend/src/pages/TradesPositions.tsx`；`server/routes/data.py:1262` | — | 无对应 issue |
| ExitPlan 版本历史 | DORMANT | 见 1.10 | `portfolio/exit_plan.py:127-149` | persistence :41 | 无对应 issue |
| position_path_observer 告警 | BROKEN | 滞后约 13 天，bitget 可用率 1.7%，health/doctor 均无探针 | `position_path_observer.py` | — | 无对应 issue |
| Entry Workbench | LIVE | 07:43:53Z：50 个计划 failed 42、invalidated 7、expired 1 | `server/routes/universe.py:734,826` | — | — |
| Trades&Positions CausalChain | WIRED_UNOBSERVED | 前端自拼第三套 lineage；无管理/保护/成本/Outcome 节点 | `frontend/src/components/CausalChain.tsx` | 一功能一实现 | 无对应 issue（收敛） |
| Lifecycle Inspector 的 Dashboard 入口 | MISSING | 无路由 | — | — | #3281、#3087 |
| audit.jsonl 哈希链 + audit_events | LIVE（无成交/开平仓/止损修改事件） | 链有效；`risk_policy_replaced` actor 写死 human/dashboard | `files/jsonl.py:497`；`server/routes/settings.py:2030-2032` | audit_chain_contract §2 | actor 问题无对应 issue |
| Telegram / Webhook 推送 | DORMANT | Fedora 两者都关闭 | `core/aletheia/notifications.py:243,370` | — | — |
| 成交通知 `notify_trade_fill` | DORMANT（开关不起作用） | 无生产调用方，但 Settings 暴露开关 | `notifications.py:313`；`config/schema.py:1213,1227` | 语义诚实 | 无对应 issue |
| 入场漏斗唯一死因归因界面 | MISSING | owner 14 天归因靠手写 SQL | — | entry_order_risk_sizing「Gate inventory」 | #3087 部分 |
| NotificationCenter 前端组件 | DORMANT | 未挂载 | `frontend/src/components/notifications/NotificationCenter.tsx:16` | — | #3068 |

**应该有但没有**：任一锚点得到正确 first_break（#3281）；Story 词汇的管理决策、ExitPlan 修订、保护激活都有持久证据（profit_loop :127-133）；UI 看到交易循环卡点（#3087）；health 与 doctor 不矛盾（#3060）；lineage 只有一份实现；漏斗唯一死因归因；原生保护状态在持仓页可见（#3848）；审计 actor 真实；可送达告警与有效开关。

**交接**：CLI 接通但 Inspector 判定有错；Dashboard 部分接通；告警只写审计，外部推送未接通；`/api/health` 与 doctor 结论相反。

---

## 2. 持仓管理专章（owner 重点）

owner 的期望原话：**"针对开仓后的这个 position，对它进行一轮轮的扫描，并且给出新的指令。"**

### 2.1 一个开仓后的持仓，每一轮会发生什么

开仓后，同时有几条互不相同的"轮"在跑。**只有其中一条会让 AI 介入。**

各轮的频率、输入输出见 [SYSTEM_MAP 调度表](../../design/architecture/SYSTEM_MAP.md)（`position_manager`、`tpsl_fill_check`、
`position_reconcile`、`exchange_sync`、`position_path_observer`、`pi_trading_decision` 各行），这里只列"会不会叫 AI"和现状。

| 轮 | 做什么 | 会不会叫 AI | 现状 |
|---|---|---|---|
| `position_manager` 确定性循环 | 取精确 mark 价 → Layer-0 灾难止损（亏损 10%）/强平距离 → 跟踪止损 → `_decide_exit`（止损、止盈、限价离场、max_hold、论点失效）→ 检查是否发唤醒 | 只在满足唤醒条件时 | LIVE；暂停与熔断都不阻塞它 |
| 唤醒检查（在上面循环内） | HORIZON_DUE（到 horizon）、ADVERSE_SIGNAL（逆向信号）、THESIS（论点失效，仅 serenity）、卡住平仓的 P1 唤醒 | 是（这是 AI 介入的唯一入口） | 实际只有 HORIZON_DUE 在发；ADVERSE 输入断流；THESIS 不适用 |
| AI 复查 `review_portfolio_state` | 只由唤醒触发，失败按 300s 起、最长 1800s 退避；只看唤醒指向的仓位，输出 hold / adjust_position / close（库内动作值为 `close`） | — | LIVE |
| `tpsl_fill_check` | 回读交易所止盈止损 id、识别原生触发成交 | 否 | 部署后原生止盈止损已开，但还没有持仓，实际无事可做 |
| `position_reconcile` / `exchange_sync` | 本地与交易所仓位对账、消失确认 | 否 | LIVE |
| `position_path_observer` | 记录 5m 路径（供 MAE/MFE） | 否 | BROKEN（bitget 可用率 1.7%） |
| `pi_trading_decision` 心跳 | **没有唤醒批次时跑的是入场 Planner，不是持仓复查**（`pi_daemon.py:604-612`） | 否（对持仓） | 这是"定期复查"缺失的直接原因 |

所以，"多久一轮 AI 复查"的真实答案是：**持仓时间到 horizon（入场时 AI 选，实测 4h）时一次；如果 AI 答 hold，horizon 往后滚，下一次再到点时再一次；中间不看。**

### 2.2 AI 能下哪些新指令

| 指令 | 能否下 | 约束 / 现状 |
|---|---|---|
| hold（继续持有） | 能 | 会自动把 horizon 延长（见 2.4 缺陷） |
| adjust：改止损 | 能 | 只能收紧；必须在 mark 正确一侧；不能越过灾难止损线（`position_manager.py:1829-1840`）；返回 `updated: True` 和如实的 `exchange_synced`，但调用方不读后者；部署版本里入场 preset 止损不替换，所以原生止盈止损开启后每次收紧都是 `exchange_synced=false`；master 上 PR #3879（#3848 PR-B）改为原地修改，未部署 |
| adjust：改止盈 | 能 | 需回读风控已批准的计划 |
| adjust：改 horizon | 能 | 必须晚于当前持仓时长 |
| close（全平；prompt 里叫 close_one） | 能 | 走唯一平仓中枢 |
| 跟踪止损 / 保本 | **不能** | 复查 schema 无 `new_trailing` 字段；`apply_operator_adjustment` 的参数无调用方 |
| 部分减仓 / 分批止盈 | **不能** | `reduce_position_routed` 无调用方；遗留 `reduce_exposure` 被归一成 hold（#3395） |
| 限价离场 | **不能** | `proactive_exit(limit)` 只开放给人工（`data.py:1427`） |
| 加仓 | 不能 | 不在任何契约里 |

AI 复查时能看到的信息：每笔仓位的入场价、新鲜 mark、收益率、止损、止盈、开仓时间、持仓时长、horizon，加上唤醒证据和全局仓位数/未实现盈亏（`commands.py:1958-2041`）。**看不到** K 线、结构支撑阻力、ATR、MAE/MFE、`position_path_observations`、资金费率、入场论点、原计划理由。

### 2.3 Fedora 上真实发生过多少次（bitget_demo，2026-09-11 至 09-29）

- 41 笔交易所持仓全部有 horizon；**21/41 被 HORIZON_DUE 唤醒过，20/41 从开到平 AI 一次没看过**（都在 horizon 前被止损/止盈打掉）。
- 28 笔止损平仓里只有 13 笔曾收到时长唤醒。
- 共 **46 次复查**：hold 41、adjust_position 4、close 1；覆盖 21 笔仓位，单笔最多 6 次；45/46 由 horizon_due 触发。
- 4 次 adjust 中 2 次被拒（`stop_would_loosen_protection`）；真正改止损只有 1 次（8ddff135155c，93.658→91.8，`exchange_synced=false`）；止盈从未被改过。
- 逆向信号唤醒 0 次、接近止损/止盈唤醒 0 次（这三种类型无发射代码）、跟踪止损 0 次、部分减仓 0 次。
- 2026-09-29 之后没有新持仓，所以复查与新指令在 `aa09a87c` 和当前构建 `99a22066` 上**一次都没有发生**（部署后 3 张入场单都失败，0 持仓）。

端到端样本（详见 §3）：

| 持仓 | 序列 | AI 看了几次 |
|---|---|---|
| LTC 3d547d8d8cea（持有 8h23m） | 开仓 18:12:03 → 22:12:41 复查 hold，horizon→8.0106h → 两次 `protective_mark_unavailable`（这两轮没核对止损）→ 02:13:41 复查 hold，horizon→16.0378h → 02:35:36 止盈平仓 | 2 次，间隔各约 4h |
| HYPE d3f105a1ed48（6h15m） | 开仓 19:13:06 → 23:13:42 复查 hold → 01:28:40 止盈 | 1 次 |
| PEPE fc8de691ec90（3h51m） | 开仓 02:13:10 → horizon 未到 → 06:04:45 人工平仓 | 0 次（期间对账误报"消失" 42 次） |

### 2.4 已实测的缺陷（与 owner 期望无关、合同本身就不允许的）

1. **hold 让 horizon 翻倍并被重复复查（BROKEN，D0）**。`_horizon_hold_roll_hours` 用"当前持仓时长 + **当前（已延长过的）** horizon"，而 prompt 承诺的是"延长一个**原始**计划周期"，于是 4→8→16→32h；`_collect_review_scope` 不重新核对复查时是否仍然到期，确认前排队的陈旧 HORIZON_DUE 会在 1-5 分钟后再复查一次再滚一次（46 次中 6 次是 15 分钟内重复）。HYPE fd7149cc1c7a 在 09-25 19:30:12Z→19:32:00Z 两分钟内 64.3h→96.59h；3×horizon 硬上限随之失效，最后靠 72h 兜底平仓。另外 `horizon_due_allow_hold` 这个开关不在任何契约里，execution_contract §10 写的是"hold 保持未完成并重试"。代码：`commands.py:1537-1568,1624-1700,2196-2231`。无 issue。
2. **本地改了止损、交易所没改，AI 却不知道（BROKEN）**。`apply_operator_adjustment` 如实返回 `exchange_synced=false`（`position_manager.py:1945-1950`），但调用方 `commands.py` 和 prompt 不读这个字段；跟踪止损路径则直接丢弃同步结果（`position_manager.py:776-780`；PR #3879 已在 master 改为记录交易所真实结果，未部署）。历史持仓在交易所上也没有原生止损 id（0/41），实际保护只靠本地 30 秒轮询，网关停机即无保护（#3619、#3848）。
3. **逆向信号唤醒开着但永远不会触发（BROKEN）**：它读的 `opportunities` 表 2026-08-27 后再无新行。
4. **卡住的平仓会阻塞全部退出**：KORU d73ba8665eab 因 submission_unknown 卡 69h，实际持有 115.18h（#3719、#3568）。

### 2.5 与 owner 期望的差距

| owner 期望 | 现在 | 差距 |
|---|---|---|
| 开仓后**一轮轮**扫描 | 只在 horizon 到点时扫一次；20/41 的仓位一次都没被扫到 | 缺"不看时长的定期复查"（owner 已在 Phase1 研究 §5b-3 记为"以后再讨论"） |
| 每轮给出**新指令** | 41/46 是 hold；hold 还会自动翻倍延期 | AI 实际没在"管理"，并且 hold 有 D0 缺陷 |
| 指令丰富（移止损、减仓、止盈调整） | 只有收紧止损/改止盈/改时长/全平；无跟踪、无减仓、无限价离场 | 指令集太窄 |
| 指令落到交易所 | 本地改、交易所不同步；原生止盈止损部署后已默认开启，但部署版本里入场 preset 止损不能被收紧（PR #3879 已在 master 修复，未部署） | 保护分裂 |
| 能事后看出"这次管理是加分还是减分" | ExitPlan 修订表 bitget 0 行；Story 只能 CLI 看到管理决策；无反事实增量 | 管理动作不可归因 |

按依赖排序的补齐方案 A–G 及推荐顺序属于待办与优先级，已移到路线图 issue [#3883](https://github.com/iridyne/aletheia/issues/3883) 的首次盘点评论。

---

## 3. 端到端追踪：真实单位的逐跳链路与断点

### 3.1 已成交并平仓：LTC/USDT 做空（持仓 3d547d8d8cea，09-28→09-29）

| 跳 | ID / 时间（UTC） | 断点 |
|---|---|---|
| 候选 | universe_members `0c40af9880c6`，快照 6ea145e083a5，排名 6，momentum 做空 | — |
| 规划代次 | `4fa3c0918722`，第 17 轮，创建 18:08:55.38，调 AI 18:11:53.410 | — |
| 假设 | continuation，INTRADAY，失效锚点 70.8，risk_tier LOW；推理调用 inference_a527ccbb… | — |
| 计划 | `2936b63db3e1`：限价 69.33，止损 70.8325，止盈 67.0763，4h，10x，POST_ONLY | — |
| 提案 | `640856d38545` 18:11:58.59，数量 2.739，OPPOSING_BBO_IOC，`opportunity_id=NULL` | 身份链断 |
| 风控 | 4 条决策全部通过（18:12:00.72、18:12:01.29） | — |
| 开仓 | 订单 `36bba594d9cf` 18:12:01.37，IOC 限价 69.33；成交 18:12:03.657 **@71.38**（taker） | **成交价偏离计划约 296bps**；止损 70.83 落在开仓价之下 → 这笔空单实际无亏损上限 |
| 持仓管理 | 22:12:41 hold（8.0106h）；00:30 两轮 mark 不可用；02:13:41 hold（16.0378h） | horizon 翻倍；两轮未核对止损 |
| 平仓 | 本地轮询 ≤67.08 触发止盈；市价单 `8bf7f728cbb0` 02:35:36.997；成交 02:35:40.668 **@69.09** | 触发价与成交差大，滑点未记录（NULL） |
| 结算 | `position_closed` 02:39:49.98（晚成交 4m09s）；资金费 −0.634；已实现 +6.589；eligible | — |
| 复盘 | shadow 钩子 completed 但未写入；复盘 298cdbe1d9eb（批量覆盖 4 笔）；案例 960d56f2ebc9 标 win；经验 0 条 | 复盘把盈利归给"continuation 论点成立"，没识别成交偏差 |
| 计划终态 | `entry_plans.status = cancelled / entry_capture_terminal` | 已成交却标"取消" |

### 3.2 已成交并平仓：PEPE/USDT 做空（持仓 fc8de691ec90，09-29）

| 跳 | ID / 时间（UTC） | 断点 |
|---|---|---|
| 候选 → 代次 | `6f2a6f53b3b7` → `d1e75d6c15cc`（第 20 轮，`canonical_compatibility_cutover`），调 AI 02:09:50.234 | — |
| 计划 | `efb52194e1b9`：5x，最长等待 60 秒 | — |
| 腿 0 提案（挂单） | `6f74c4abe4fa`：02:09:55→02:11:44 被拒 **147 次**（stale_market_data_demo，年龄 104901s），对应 460 条阶段审计；02:11:48.9 批准；挂单 a70a2c5b129c 因 60s 超时撤 | 风控重试风暴（#3798） |
| 腿 1 提案（吃单） | `6d52721608b2` → 订单 `316296d78160` 02:13:07.08 → 成交 02:13:10.091 @4.1181e-06 | — |
| 持仓管理 | 4h 未到，AI 复查 0 次；02:45:03→05:56:06 对账报"消失" 42 次，至今 pending | 消失 candidate 孤儿 |
| 平仓 | 人工 `cd470420dde0` 06:04:41.45，原因 `manual_operator_migration_to_uta_v3`，已实现 −1.841 | — |
| 复盘 | 案例 6858e674 标 loss；**06:09:04 自动生成防护规则候选 27c1ef4dd9f0"Repeated loss on momentum:PEPE/USDT:short"** | 人为迁移平仓被当成策略亏损（`close_category=strategy`）喂给学习 |

### 3.3 今天没走到下单：MSTR/USDT 做多（停在风控）

候选 `edb11c8dac34` → 代次 `eb134a8a95d5`（05:00:48.55 创建，05:01:02.659 调 AI）→ 计划 `5c3380a60098` → 提案 `3f2c2caccb38`（05:01:09.86，14.27 @154.99，5x）→ **05:01:10.225 preflight 拒绝 `regime_confidence_standdown:conf=0.57<=0.60`** → 计划 `failed/canonical_risk_rejected`。之后 05:10-05:51 又有 5 次候选被并入同一已终止规划周期，这 1 小时没有任何新动作。同日 DOT aef3a7099ff2、MSTR 933663c4b6fa 同因而死。05:55:32Z owner 改 rev 10 关闭了这个门；到 08:48Z rev 10 下已有 14 条决策，没有一条是 `regime_confidence_standdown`。

### 3.4 今天没走到下单：SNDK/USDT 做空（停在编译器）

候选 `8d15a1aa5a66` → 代次 `e54ffcd9b6c1`（07:51:14.28，`confirmed_structure_requalified`）→ 第 1 次尝试 07:51:48.90 `ENTRY_ANALYSIS_EXECUTION_CONTEXT_UNAVAILABLE`（订单簿过期，#3750）→ 第 2 次 07:53:50.331 调 AI → **`stop_distance_below_vol_target`：1.0729% < 1.5000%** → 代次终止。AI 给的 `capture_reason` 是"Continuation opportunity in a bullish trend"，方向却是 short，没有任何检查拦下这个自相矛盾。

### 3.5 今天 04:00Z 之后的总体

新建 76 代：45 代死于止损下限、11 代错侧失效锚点、13 代 schema 无效；另有 DOT eee6544c94ec、DOGE 8cf9d99dac3b 被 `risk_authority_revoked`（1.3 推断为策略改版后 watcher 用旧版本比对），PEPE d4e7e4e54faa 价格失效。**0 个订单。**

### 3.6 部署 `99a22066` 之后（2026-10-09T08:15:23Z–08:48Z）

新建 13 代：6 代死于止损下限、2 代 `VENUE_MARGIN_TIERS_STALE`、5 代建出计划；0 代 WRONG_SIDE。5 个计划里：PEPE `cc99445cbe14`（提案 `2734ca481625`）和 MSTR `cb8d1eb8bf76`（提案 `9db748ebc571`）在 preflight 被重复的单笔风险检查以浮点误差拒掉；BNB `e51d1c3acdec`、SNDK `501fba241950`、SOL `b82c987b6ac1` 通过风控并生成订单，订单 `a69db25da1c7`、`5de22a0ebe27` 为 `command_not_sent`，`b03c5299249f` 为 `venue_rejected_command_25206`。**0 笔成交、0 个持仓。** #3880 后续查实 SOL 的 25206 来自 demo 现货盘口与主网脱钩；两张期货单的发送前原因未查实。两个 D0 缺陷（原因码丢失、浮点错拒）已由 PR #3881/#3882 在 master 修复，未部署（09:23Z 复核运行仍为 `99a22066`）。

### 3.7 数据链断点汇总

1. `opportunity_id` 在持仓与提案上全部为 NULL（交易所路径 41/41；见 1.5）；只能经 `orders.entry_plan_id → entry_plans.candidate_id` 绕道回溯；还让风控 #16 行业集中度对现有持仓失明（#3846 已由 PR #3872 在 master 修复、未部署；#3644）。
2. 计划对成交"失明"：成交的计划被标为 cancelled（40/42）。
3. 止损/止盈/时长修改历史只能从 `audit_events.operator_adjust_position` 和 `pi_decisions` JSON 拼回；`position_exit_plan_versions` 交易所路径 0 行，Story 恒显示 `exit_plan_changed=unavailable`。
4. `pi_decisions` 没有 position_id 列（只在 JSON 里）；`signal_source` 默认值仍是已退役的 `market_observer`（`models.py:1778`）。
5. 开仓论点下游读不到：读取方 `trade_attribution.py:96` 只认旧 PiDecision 记录且要求 `opportunity_id`。
6. 平仓单 `risk_decision_id` 为 NULL（合同只要求开仓过风控，但审计上看不到平仓是否被检查过）。
7. 路径观测、滑点、shadow_trades 都缺数据 → 扣完全部成本的净盈亏与 MAE/MFE 拿不到。
8. 规划尝试历史今天才开始写；更早的两段式失败无法还原。
9. 14 条"持仓消失"候选在持仓关闭后仍 pending。

**owner 现在能还原的**：CLI `aletheia inspect position-story` 可还原风控通过、开平仓成交、复查时间与原文、平仓原因、毛盈亏；API 能分别看代次、计划、复查决定。**不能还原的**：UI 上从候选一路到复盘；开仓论点与持仓的关联；修改历史（要翻审计）；扣全成本的净盈亏与 MAE/MFE；成交价相对计划价的偏差；"PEPE 那笔是人工迁移不是策略亏损"。

---

## 4. 本文未核实的事项

- `entry_plan_risk_authority_revoked` 的根因：1.3 给出"watcher 配置身份不含风控策略"的推断，有 2 个样本与代码事实支撑，但未做复现。
- #3751 结构化输出修复与 PR #3854 的尝试历史只有约 35 分钟样本，24h 尺度上的 schema_invalid 比例未核实。
- 2026-09-29 之后没有新持仓：#3848 原生止损、#3719/PR #3724 平仓 claim 恢复、身份闸门修复在运行时都**未被观测**。
- `position_path_observer` 失败根因只记到错误类型 `exact_5m_bar_unavailable` / `market_data_fetch_failed`，未深挖。
- 结果回流入场决策（`EntryLearningContext` 是否出现过 observational/experimental 状态）无运行证据。
- master `99a220664`（多周期锚点）和 `205810797` 未纳入本次代码审计；多周期锚点只有部署后 13 代的运行样本。
- 部署后两张期货入场单 `command_not_sent` 的具体原因：适配器当时丢弃了异常，审计只有 `command_evidence`。#3880 排除了 coins 端点、身份/能力拒绝和请求构造，领先假设是 `set_leverage` 失败，**未证明**；要等 PR #3881 部署后下一次失败的原因码。
- 部署后 WRONG_SIDE 是否真正归零：13 代 0 次，样本太小。
- 部署后 stale_order_cleanup 的 ValueError 是否仍会出现：08:15Z 之后未出现，观察时间太短。
