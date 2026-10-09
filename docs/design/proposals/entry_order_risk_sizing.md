---
id: entry-order-risk-sizing
status: draft
github_issue: 3777
related_contracts:
  - ../contracts/entry-plan-contract.md
  - ../contracts/entry_sizing_and_financing_contract.md
  - ../contracts/risk_engine_contract.md
  - ../contracts/execution_contract.md
supersedes: []
---

# Entry, Stop, Target and Position Sizing — Original Design

> Proposal status: **draft**. This proposal records the owner's original intent
> and the design selected from evidence. It does not claim that any of it is
> implemented, deployed, or profitable.

## Summary

This proposal is the single design record for how Aletheia turns an Entry Plan into
an order. It covers where the stop goes, where the target goes, how long a position
may be held, how large the position is, what leverage it uses, and how the stop is
executed.

It does two things:

- It keeps the owner's original requirements verbatim, so that later changes can be
  checked against them.
- It records the design chosen from evidence: competitor and reference code, industry
  practice, Fedora runtime data, and a Monte Carlo of a 500 USDT starting account.

## Owner's original requirements (verbatim intent, 2026-10-09)

1. **Wider stops are acceptable.** The owner agrees stops may be widened rather than
   rejecting a plan whose structural stop is too close.
2. **Starting capital, not position size.** The first live deployment starts with about
   **500 USDT of available capital**. That is the account, not the size of one
   position.
3. **Goal: grow it fast.** The system should balance itself so that the 500 USDT can
   **double quickly**.
4. **Derive the numbers, don't guess them.** Starting from the target, and from
   published data and practice, work out:
   - what **risk per trade** (% of equity) should be;
   - the **leverage** that follows;
   - the resulting **notional position**.

   All three must be consistent with Aletheia's entry point, exit point, take-profit
   and stop-loss habits.
5. **Learn from others.** Study how the main competitors and known reference systems
   place stops: R20/AstraQuant (`_refs/r20-quantum-trader`), OpenAlice
   (`_refs/OpenAlice`), nofx, Vibe-Trading, and the VergeX platform the owner knows
   well. Adopt what is closest to best practice.
6. **The owner accepts the overall direction and wants the best version of it.** The
   overall direction is #3777 / PR #3780: structural support/resistance stops, a floor
   that widens the stop instead of rejecting the plan, and the target placed at the
   nearest opposing structure.
7. **Write it down.** The design must live in `docs/`. It must record both the owner's
   ideas and the final engineering choice, as at least one complete original design.

### Owner's working summary (2026-10-09, second statement — **not a final design**)

The owner clarified that this is a **summary** of their own idea combined with the
interim answer. It is not a final scheme. **What "best" means is not defined yet**, and
it must come from broad reference to competitors and established quant platforms. In
the owner's words, the reference set is AstraQuant, VergeX, and other platforms that
did AI-automated trading earlier.

The owner also noted that **leverage must vary**:
- BTC and ETH can take higher leverage.
- Altcoins may also take higher leverage, to capture their upside.
- Other cases should use lower leverage.

So the flat "10x" below is a placeholder, not a rule.

The summary, in standard R terms:

- **Stop.** Take a price-action reference point from minutes, 1h or 4h structure. It is
  a support line or a similar level. Add an ATR buffer to it. The distance from the
  entry point to the stop is **1R**.
- **Target.** Trade at **1:3** reward:risk, so the take-profit sits **3R** from entry.
- **Size.** The quantity per order is set by the **margin it actually occupies**: **1%
  or up to 5% of available capital**. This leaves enough chances to start over.
- **Leverage.** The owner envisages **10x**.
- **Roles.**
  1. The **AI gives a risk judgment** as a reference value.
  2. The **system computes** the actual values and runs a consolidated risk
     assessment.
  3. The system **executes** the result.
- **Problem statement.** Each piece seemed reasonable on its own. Combined, they made
  the system so complex that many plans never pass risk control. The owner asks for a
  complete system design that solves this and fits the model above.

Arithmetic that follows directly from the model, with 500 USDT starting capital:

    loss at stop (% of equity) = margin% × leverage × stop%
    e.g. 5% × 10 × 2% = 1% of equity (5 USDT); target 3R = +3% (15 USDT)
    break-even win rate at 1:3 = 25% before costs (≈30% after measured friction)

### Owner decision 1b（2026-10-09）：止损是否挂上，以下单回执为准，不再轮询后平仓

Owner 的原话：开仓时同时提交 TPSL，交易所会返回执行回执，靠这个回执就能确认；"每 90 秒读回一次，3 分钟读不到止损就平仓"太莽撞，没有必要；只要保证 TPSL 真的挂上了就可以。

落地方式：
1. **发送前检查**：系统先组装下单请求，确认请求里确实带着已批准的止损价和止盈价，再发给交易所。请求里缺了止损或止盈，就**不发这笔单**。这一步是为了防 #3783 那类问题：参数被静默丢掉，交易所却照常接单。
2. **以交易所回执为准**：交易所接受了带 TPSL 的开仓单，就视为止损已经挂上。开仓单被拒，就没有持仓，也就不存在需要保护的仓位。
3. **读回只用来记账**：读回到的止损单 id 记下来，供以后改单和识别成交使用。读不到时只发告警，**从不平仓**。

这条决定取代 #3849 中"读回后平仓"的行为。

### Owner decision 1 (2026-10-09): exchange-native stops first

Protective stops **must be exchange-native orders** and take **top priority**. A local
poller that detects the trigger and then sends a market order loses money, and loses a
lot of it. The measured evidence: on Fedora's 41 closed demo trades, the average
losing trade lost **1.41R instead of 1R**, and the worst lost 4.77R.

Consequences for every later section:
- R-based sizing is only meaningful once the stop executes on the venue.
- Taking the competitor patterns that protect this guarantee, a position must never
  be left without venue-side protection:
  - OpenAlice refuses to open a position whose protection did not attach;
  - nofx closes immediately when the protective order fails;
  - AstraQuant uses native OCO.

## Current fact (2026-10-09, master `1cea7366`, Fedora read-only)

- **Stop placement.** The Compiler derives the stop as anchor ∓ ≈0.05 × ATR(15m). It
  then **rejects** any plan whose stop is below a fixed 1.5% floor (Demo only).
- **Floor rejections in the last 7 days.** 187 of 405 planning attempts (46%) died on
  this floor. Their median stop was 0.56%.
- **Sizing.** Position size is loss-budget based:
  `notional = loss_budget / stop_fraction`. The Demo policy uses
  `max_single_trade_risk_pct = 20`. On the Bitget demo account (≈442 USDT equity)
  that is ≈88 USDT of loss budget per trade.
- **Friction.**
  - Taker fee: 0.06% per side (measured over 79 fills).
  - Local-poll stop slippage: median ≈0.42%, n=28. This is a proxy measurement.
  - Exchange-native TP/SL is off (#3619).
- **Edge.** The 9-day replay on #3777 shows **no gross edge at any stop width**:
  every confidence interval contains 0. Wider stops only reduce the share of each
  loss that friction eats.
- **Live.** Live trading is disabled on Fedora. Live caps exist in the risk policy;
  see the resolved design for how they interact with the chosen sizing.

## Research and evidence

完整证据见 [入场、止损、止盈、仓位与杠杆调研（2026-10-09）](../../research/trading-strategy/ENTRY_STOP_SIZING_LEVERAGE_RESEARCH_2026-10-09.md)。
要点：

1. **止损执行优先于一切。** 本地轮询止损比计划平均多亏约 41%。亏损单平均是 −1.41R，而不是 −1R。
2. **仓位大小救不了没有 edge 的信号。** 现状下，任何 r 都会亏光。如果有 +0.15R 的 edge，r 取 1%–2%
   最合理，大约是半 Kelly。
3. **业界通行模式：**
   - 等风险定仓：仓位 = 权益 × r ÷ 止损距离；
   - 结构/ATR 止损，并设下限和上限；
   - 盈亏比限定在一个区间内；
   - 三重屏障：止损、止盈、时限；
   - 交易所原生保护，挂单失败就平仓；
   - 止损只往有利方向移动；
   - 日亏线和回撤减仓；
   - 组合热度上限。
4. **杠杆倍数 L 只决定保证金占用和强平距离，不决定风险。** "按币种分级"真正要分的是每一档的风险
   预算 r，或单币敞口上限。现有证据倾向于山寨币更低，但山寨币是否值得更大的 r，应当用分档的扣费后
   净期望来回答。
5. **分工上的共识：**
   - AI 决定：方向、失效点、目标依据、风险姿态；
   - 系统决定：最终止损价、仓位、杠杆、时限、保护单。

## Gate inventory and rejection attribution

从 AI 计划到交易所订单，中间一共有 **95 道检查**（2026-10-09 清点）。按 owner 模型给出的处理建议：

| 处理方式 | 数量 |
|---|---|
| 保留为硬不变量 | 47 |
| 并入统一计算 | 14 |
| 降级为 AI 输入 | 12 |
| 改为缩量 | 6 |
| 删除 | 7 |
| 需 owner 决定 | 9 |

14 天唯一死因归因见 [入场漏斗 14 天拒单归因（2026-10-09）](../../research/audits/ENTRY_REJECT_ATTRIBUTION_14D_2026-10-09.md)。
要点：

- 1877 代里只有 29 代成交，而且全部发生在身份修复之前。
- 修复后的正常期一共 34 个交易机会，0 成交。
- 结构性死因按交易机会计：
  - 1.5% 止损下限：26 个机会
  - 格式不合格：13 个机会
  - 锚点放错一侧：10 个机会
  - 行情状态置信度门：拒掉全部非均值回归提案

## Decisions under discussion (2026-10-09)

### 拒单归因之后的决定（2026-10-09）

- **行情状态置信度门：选项 A。** 模拟盘阶段关闭这道门。
  - 已执行：Fedora demo 策略升到 revision 10，`eligibility.min_regime_confidence_to_enter` 从 0.6 改为 0。由 AI 按 owner 的指示操作。
  - 相关的 `sideways_standdown`（震荡行情停手）**没有改动**。当前行情状态是 bull，这道门不起作用；是否关闭，等 owner 决定。
- **三项缺陷修复已批准，直接实施。** 分别是：格式校验收敛并记录字段路径、ccxt 品种元数据缓存、规划尝试只追加记录。实施进度见各自的 PR。
- **几何研究 #3850 第一阶段已启动。** 内容包括：交易理论三个方向、竞品代码、我们自己的几何实现，以及用 Fedora 数据做的 MAE/MFE 研究。之后会给出 2～3 个候选设计，并由独立审查代理复核。


下列事项按顺序与 owner 逐项讨论，结论记在各项下方。

- [x] **决定 1：交易所原生止损优先**（已决定，#3848，PR-A 实施中）
- [ ] **决定 2："最好"的定义**（部分决定，2026-10-09）
  - **阶段划分**：现在处于**模拟盘快速迭代期**，这个阶段不需要极度谨慎。要敢于多下单、快迭代，先把长期
    表现做上来，再进入实盘。
  - **H3 日亏线**：模拟盘阶段**禁用**，进入实盘时再启用。
  - **H4 交易所原生止损**：**必须**。所有 trade 都要带，模拟盘和实盘都一样。
  - **模拟盘视为资本无限**：H1（破产概率）、H2（回撤减仓/停机）、H3（日亏线）三条在模拟盘阶段**全部不启用**，
    上实盘时一起启用。
  - **H5 止损必须先于强平触发**：必须遵守。止损太宽时，系统自动降杠杆。
  - **H6 计划到下单可复算、每次拒单都有原因**：必须遵守。
  - **评价口径**：每笔交易按扣费后的**净盈亏**来评价。
  - **盈亏比的含义（owner 原话）**：在一笔订单/持仓里，止盈价离入场价的距离 ÷ 止损价离入场价的距离 = 3。
    这个比值逐笔计算，每笔都不同。
  - **胜率的定义**（owner 要求定义清楚；本文给出口径，供 owner 核对）：
    - 胜率 = 净盈亏 > 0 的已平仓笔数 ÷ 全部已平仓笔数。
    - 净盈亏 = 已实现盈亏 − 手续费 − 资金费。滑点已经体现在成交价里。
    - 已平仓包括所有离场方式：止损、止盈、超时、手动、外部平仓，一个都不排除。
    - 净盈亏 ≤ 0 记为亏。
    - 统计单位是**一个 position**（一次开仓到全部平掉）。分批平仓合并为一笔。
    - 报表同时列出：每笔平均净盈亏（R 和 USDT）、平均盈利、平均亏损，并按档位和方向拆分。
  - **模拟盘每笔风险**：按建议取 **3%**。现行 demo 策略是 `max_single_trade_risk_pct=20%`，这一条会把它下调；
    理由见讨论记录。
  - **转实盘门槛**：模拟盘累计 ≥ 100 笔，平均每笔净盈亏为正，且统计上站得住（95% 置信下界 > 0）。
    达标后启用 H1/H2/H3，每笔风险降到约 1%，再上实盘。
- [ ] 决定 2 原文条目：：硬约束 H1–H6 + 主目标 E[R_net]，以及放大规模的门槛
      （≥100 笔，且 95% 置信下界 > 0）
- [x] **决定 3：每笔风险是否按币种分**（2026-10-09，按建议）
  - 模拟盘阶段**不分档**，所有币统一每笔风险 **3%**。
  - 等模拟盘按档位 × 方向统计出净盈亏，再决定是否给某一档更大的风险。现在不知道哪类币有优势，提前
    分档等于猜。
- [x] **决定 4：杠杆上限分档**（2026-10-09，按建议）
  - 模拟盘上限：BTC/ETH ≤ **10x**，大山寨 ≤ **5x**，小币 ≤ **3x**。
  - 这些只是上限。系统在上限内选能撑起目标名义仓位的最低倍数，并且必须满足 H5（止损先于强平）。
  - 修正事实：讨论中曾说"模拟盘 BTC/ETH 被压到 2x"，那是 **live** 的默认值。Fedora 上 demo 策略
    `demo-migrated-v2`（revision 8）的实际值是 major 20 / mid 10 / alt 5、futures_cap 125、
    account_cap 10。所以本决定对 demo 而言是**下调**每档上限，同时把每笔风险从 20% 降到 3%。
  - 执行：通过已有的 `PUT /api/settings/risk-policies/demo` 修改，该接口带 revision 校验和审计。
    `futures_cap` 与各档上限重复的问题，按"一件事一份实现"另开 issue 处理。
  - **已执行（2026-10-09）**：Fedora 的 demo 策略从 revision 8 升到 **revision 9**。
    - `loss.max_single_trade_risk_pct`：20 → 3
    - `leverage.major/mid/alt_cap`：20/10/5 → 10/5/3
    - 其余字段未改动；变更后健康检查为 healthy。
    - 审计记录注意：audit.jsonl 里这条 `risk_policy_replaced` 记的是 `actor=human, source=dashboard`，因为接口把这两个值写死了。实际操作者是 AI，依据是 owner 在对话中的决定。
- [ ] 决定 3 原文条目：：山寨币是否给更大的 r；500U 起步时的默认值
- [ ] **决定 4：杠杆上限分档**：major/mid/alt 各档的 L 上限；`futures_cap` 与档位上限重复的问题
- [~] **决定 5：止损怎么定**（2026-10-09 按建议定下；同日由 owner **降级为临时工作假设**，见下方「入场/止损/止盈几何」，#3850）
  1. **AI 只给"失效点"**：从 15m / 1h / 4h 结构中选一个支撑位或阻力位，并附上理由。
  2. **系统负责算最终止损价**：
     - **缓冲**：在失效点外侧加 **0.5 × ATR14**，ATR 按该结构所在周期计算。模拟盘先用 0.5，之后用数据比较
       0.5 / 1.0 / 1.5 三档的净收益，再定。
     - **方向纠正**：AI 选的锚点在入场价的错误一侧时，系统**自动改用同周期、方向正确、离入场价最近的结构位**，
       不让整个计划作废。找不到合适的结构位，才不出计划。
     - **最窄**：止损距离 ≥ **1.2%**（这样往返手续费 0.12% 不超过 0.1R）。不够宽就向外推到 1.2%，不作废；
       仓位按每笔 3% 风险自动缩小。
     - **最宽**：必须满足 H5（止损先于强平触发）。太宽时先降杠杆；降到 1 倍仍不满足，才放弃。
  3. **止损挂在交易所**（决定 1，#3848）。
  - 和 PR #3780 的关系：#3780 已经实现了"结构带外沿 ± 0.5 × ATR(该周期)"和"加宽而不是拒绝"。
    要改的有两处：
    - 下限从"费用 ÷ `entry_stop_cost_share`（≈2.2%）"改成 **1.2%**；
    - 新增"方向纠正"。
- [ ] 决定 5 原文条目：：AI 给结构失效点；系统加 ATR 缓冲、下限（按费用 ≤ 0.1R 推出）和
      上限（按强平距离推出）
- [ ] **入场 / 止损 / 止盈几何：单独立项**（owner，2026-10-09，#3850）
  - Owner 的判断：入场价、止盈价、止损价这三个点怎么处理、怎么计算、最后给出什么结果，是我们**交易 alpha 的
    核心来源**。这部分既复杂又重要，所以不在本轮仓促决定，单独拿出来认真分析。
  - 分析要充分借鉴 Price Action 等交易理论和竞品的实现，从中提炼足够的经验和教训，再由 owner 确认实现方式。
  - 立项后的处理：
    - 决定 5 只保留为临时工作假设；
    - 决定 6（止盈）、决定 7（时间屏障）并入该项目；
    - **PR #3780 暂缓合并**，作为该项目的一个输入方案参与比较。
  - 本提案（仓位、杠杆、风险、护栏）与几何项目的边界：本提案只负责"给定入场价和止损价之后，下多大、
    开多少杠杆、有哪些护栏"；三个点位本身怎么定，全部由几何项目负责。
- [ ] 决定 6 原文条目（已并入几何项目）：：固定 3R、最近反向结构，或两者取近；盈亏比区间
- [ ] **决定 7：时间屏障**：是否设持仓时限；初值用回放数据中持仓时长的分位数确定
- [ ] **决定 8：账户级护栏**：日亏线 3%；回撤 10% 时减半、20% 时停机；同时持仓数与同方向合计风险
- [ ] **决定 9：Live 风控上限调整**：现行上限会卡死 500U 账户。具体是总敞口 5%（合计只能开 25U 名义）、
      山寨杠杆 1 倍、族准入胜率 ≥ 50%（实测胜率 22%）、日亏 0（等于关闭）

## Selected design

_Filled after the owner discussion. It will cover stop placement, floor and ceiling,
target and horizon, risk per trade, leverage, notional, concurrency, daily loss
stop, scale-up rule, and stop execution._

## Trust and complexity review

_To be completed with the selected design._

## Verification contract

_To be completed with the selected design._

## Rollout and rollback

_To be completed with the selected design._

## Resolution

_Pending owner decision._
