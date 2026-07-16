# 退出系统权威分析：从「AI 入场 + 规则出场」走向「AI 自主交易员」

> 研究报告 · 2026-07 · 针对 Aletheia 退出（平仓）决策架构
> 目的：厘清退出系统现状，做出产品裁决——哪些退出应是确定性安全边界，哪些应交由 System Operator（AI）自主决策，并给出分阶段优化路线。

---

## 0. 结论先行（TL;DR）

1. **现状定性准确**：入场由 AI（System Operator）决策，但绝大多数出场行为由确定性规则引擎驱动。Aletheia 目前确实是一套 **hybrid strategy system**，而非纯粹的 AI discretionary trader。

2. **但有两个重要修正**：
   - **RuleSet exit 已于 2026-07-02 彻底移除**（spec PL-PM-08 锁定）。它曾是唯一「绕过 AI 自主权」的自动平仓通道，但活体触发 0/208，属于「已布线未引爆」，现已拆除。
   - **market-aware exit 规则引擎默认是关闭的**（`market_aware_exits.enabled = False`）。用户描述的「反向信号紧急退出 / 收紧止损 / confidence collapse」三条规则在默认配置下**并不生效**。它是一个已实现但需显式开启的可选层。

3. **真正 always-on 的规则性出场**是这四类，它们才是「规则替 AI 做了策略决策」的主体：
   - `stop_loss` / `take_profit`（`_decide_exit`）——当 AI 未在入场时给出价位，则回落到 config 默认百分比；
   - `max_hold`（固定 config 计时器：short_term 72h / serenity 90d，**不是** AI 选择的 horizon）；
   - `trailing_stop` + `breakeven`（`_maybe_update_trailing_stop`，ATR 驱动，规则自主移动止损）；
   - `thesis_invalidated`（读 `opportunity.status != active` 的**代理信号**自动平仓，非 AI 判断）。

4. **根本架构约束（这是全部问题的技术根因）**：System Operator 跑在**分钟级的守护进程节律**上（周期 + 事件唤醒），而止损/止盈需要 **tick 级**反应。AI 在结构上无法实时执行出场——这正是出场被做成确定性规则的真实原因，不只是「安全」考量。

5. **裁决框架建议采用三层模型**：
   - **Layer 0 — 硬安全边界**（AI 不可改写）：kill switch、账户级日损、爆仓缓冲、交易所异常减险、对账完整性冻结、**+ 新增 catastrophic stop-loss**。
   - **Layer 1 — AI 授权的执行意图**（声明式，机器速度执行）：AI 在入场/每次评估时**授权** stop / target / horizon / trailing 意图，确定性执行器在两次 AI 评估之间以 tick 级执行。
   - **Layer 2 — AI 自主策略**（System Operator 节律）：修订止损/目标/horizon、提前止盈或让利润奔跑、反向信号是否退出、减仓/加仓/翻转/继续持有、thesis 是否失效。

6. **核心差距**：当前 trailing/breakeven/market-aware/固定 max-hold/默认 TP 是**规则自主的策略行为**（规则引擎在用自己的逻辑做决策），应改造为 **AI 授权、执行器执行** 的模式。同时 AI 目前**没有「修订止损」这个决策动作**——它只能在入场时设 stop/target，之后只能 close/reduce，中途移动止损完全由规则接管。这是把策略权交还 AI 的最大工程缺口。

---

## 1. 现状地图：完整退出来源清单

`position_manager.evaluate_open_positions()` 是每周期的主循环，出场判定优先级如下（数字越小越先检查）：

| # | Exit reason | 判定位置 | 决策者 | 性质 | 默认启用 |
|---|---|---|---|---|---|
| 0 | `exchange_bracket_triggered` | `_sync_exchange_closed` (position_manager.py:282) | 交易所 | 执行同步 | 仅 `exchange_tpsl_enabled` |
| — | trailing / breakeven（移动止损，不平仓） | `_maybe_update_trailing_stop` (177) | **规则** | **策略** | ✅ always-on |
| 1 | `stop_loss` | `_decide_exit` (266) | 规则执行 AI/默认价位 | 混合 | ✅ |
| 2 | `take_profit` | `_decide_exit` (269) | 规则执行 AI/默认价位 | 混合→**策略** | ✅ |
| 3 | `max_hold` | `_decide_exit` (264) | **规则**（固定计时器） | **策略** | ✅ |
| 4 | `thesis_invalidated` | `_decide_exit` (273) | **规则**（代理信号） | **策略** | ✅ (serenity) |
| 5 | `market_signal_exit` / tighten_stop | `evaluate_market_exit_rules` (exit_rules.py:27) | **规则** | **策略** | ❌ 默认关闭 |
| — | AI `close` / `reduce` / `scale_in` / `flip` | `trading_decision.py:1609` | **AI（System Operator）** | 策略 | ✅ |
| — | `verified_exit` / `reconciled_gone` | disappearance_settlement / outcome_closure | 系统对账 | 行政（非交易） | ✅ |
| — | `manual_bulk_close` / `manual_review` | server routes | 人工 | 人工 | — |

**关键互斥性**：market-aware 规则仅在 `_decide_exit` 返回 `None` 时才评估（价格机械规则优先）。RuleSet exit 分支已删除。

### market-aware 三条规则的精确阈值（默认，全部可配置）

来自 `MarketAwareExitsConfig`（config/schema.py:432）与 `evaluate_market_exit_rules`（exit_rules.py:63-93）：

| 规则 | 触发条件 | 动作 |
|---|---|---|
| **Urgent exit** | 反向信号 且 `score ≥ 80` 且 `pnl% ≤ -0.3` | 立即市价平仓 |
| **Tighten stop** | 反向信号 且 `score ≥ 60` | 收紧止损（不平仓，等下周期 SL 触发） |
| **Confidence collapse** | 信号 neutral 且 `score ≤ 30` 且 `pnl% < 0` | 收紧止损 |

守护条件：`enabled=False`（默认）、`min_hold_hours < 0.5`、信号缺失、信号年龄 `> 35min` 均直接返回 none。设计取向注明为 *"Conservative: false negatives preferred over false positives"*。

---

## 2. 关键架构约束：为什么出场是 rule-based

这一节是**理解整个问题的关键**，也是任何优化方案必须先解决的前提。

- **System Operator 的节律**：`pi_daemon` 是分钟级的守护进程（周期性 + 事件唤醒），每次唤醒调用 `evaluate_trading_opportunity()` 做一次完整 LLM 推理（构建三层 context → 结构化输出）。这是**秒级到分钟级、有 LLM 成本**的操作。
- **出场执行需要 tick 级**：价格可以在几秒内穿过止损位。等 AI 下一次唤醒（可能几分钟后）再决定平仓，损失早已扩大。
- **结论**：这不是「工程师偷懒把出场写成规则」，而是**AI 的决策节律与市场的执行节律存在数量级差异**。纯 AI discretionary trader 在这个节律下无法保证风控。

**因此正确的目标不是「让 AI 实时决定每一次平仓」**（不可行），而是：

> **AI 授权声明式的出场意图（stop / target / horizon / trailing 规则），确定性执行器在 tick 级忠实执行，AI 在自己的节律上修订这些意图。**

规则引擎从「自主策略家」降级为「AI 意图的高速执行器 + 独立的硬安全网」。这才是既保留 AI 自主权、又满足机器速度风控的架构。

---

## 3. 产品裁决：三层权威模型

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 0 — 硬安全边界（DETERMINISTIC, AI 不可改写）            │
│   永远优先，任何 AI 意图都不能放松                            │
│   • kill switch（全局刹车，仅人/系统可解除）        ✅ 已实现  │
│   • 账户级日损 → 自动 trip kill switch              ✅ 已实现  │
│   • 爆仓缓冲（liquidation buffer, pre-trade）        ✅ 已实现  │
│   • 交易所异常 / 数据陈旧减险                        ⚠️ 部分   │
│   • 对账完整性冻结（reconciliation integrity）      ✅ 已实现  │
│   • catastrophic stop-loss（远端强制平仓兜底）      ❌ 缺失    │
├─────────────────────────────────────────────────────────────┤
│ Layer 1 — AI 授权的执行意图（声明式，机器速度执行）           │
│   AI 写意图，执行器 tick 级执行；两次 AI 评估之间生效         │
│   • 初始 stop / target / expected_horizon（入场时）          │
│   • stop 修订、trailing 规则、breakeven（AI 授权，非规则自主）│
├─────────────────────────────────────────────────────────────┤
│ Layer 2 — AI 自主策略（System Operator 节律，LLM 决策）       │
│   • 修订 stop / target / horizon                             │
│   • 提前止盈 / 让利润奔跑                                     │
│   • 反向信号后是否退出                                       │
│   • 减仓 / 加仓 / 翻转 / 继续持有                             │
│   • thesis 是否已失效                                        │
└─────────────────────────────────────────────────────────────┘
```

**判定原则**：一个出场行为若涉及「在给定风险约束内、对当下市场的策略判断」→ Layer 2（AI）。若是「不论 AI 怎么想都必须执行的资本/系统保护」→ Layer 0。Layer 1 是二者的桥梁——把 AI 的策略判断编码成执行器能高速执行的声明式参数。

---

## 4. 逐项归类：现在 owner vs 应该 owner vs 差距

| 退出行为 | 性质 | 现在 owner | 应该 owner | 差距 |
|---|---|---|---|---|
| kill switch | 安全 | 规则(hard) | **Layer 0** | ✅ 已对齐 |
| 账户级日损 | 安全 | 规则(hard, 自动 trip) | **Layer 0** | ✅ 已对齐 |
| 爆仓缓冲 | 安全 | 规则(pre-trade) | **Layer 0** | ⚠️ 仅入场检查，缺运行时监控 |
| 对账完整性冻结 | 安全 | 规则(断路器) | **Layer 0** | ✅ 已对齐 |
| 交易所异常/数据陈旧 | 安全 | 部分(仅告警/live 检查) | **Layer 0** | ⚠️ OKX/Binance 无对账；无断连恢复 |
| **catastrophic stop** | 安全 | ❌ 无 | **Layer 0** | ❌ **缺失**（把普通止损交给 AI 的前提） |
| 初始 stop / target | 混合 | AI 提议，缺省回落 config | Layer 1（AI 授权） | ⚠️ AI 省略时 config 静默接管、永不复议 |
| **expected_horizon** | 策略 | ❌ 无字段，用固定 max_hold | Layer 1/2（AI） | ❌ **缺失**（决策 schema 无 horizon） |
| take_profit | 策略 | 规则(AI target 或默认%) | **Layer 2** | ⚠️ 默认 TP 时是规则策略 |
| trailing stop | 策略 | **规则自主**(ATR) | **Layer 1(AI 授权)** | ❌ 规则在替 AI 做策略 |
| breakeven move | 策略 | **规则自主** | **Layer 1(AI 授权)** | ❌ 规则在替 AI 做策略 |
| 普通 max_hold | 策略 | **规则**(固定计时器) | **Layer 2(AI horizon)** | ❌ 非 AI 选择 |
| 市场反向退出 | 策略 | **规则**(market-aware, 默认关) | **Layer 2** | ⚠️ 默认关闭，但开启即越权 |
| **中途修订止损** | 策略 | **仅规则**(trailing) | **Layer 2** | ❌ **AI 无此决策动作** |
| thesis 失效 | 策略 | **规则**(代理信号自动平) | **Layer 2** | ❌ 应是 AI 判断，规则仅兜底 |
| reduce / scale / flip | 策略 | **AI** | Layer 2 | ✅ 已对齐 |
| close（主动平仓） | 策略 | **AI** | Layer 2 | ✅ 已对齐 |

**读表结论**：安全边界侧基本健全（缺 catastrophic stop + 运行时爆仓监控）。**策略侧的越权集中在 trailing/breakeven/max_hold/default-TP/market-aware/thesis-proxy**——这些是规则引擎在用自己的逻辑替 AI 做交易决策。而且 AI 连「中途移动止损」这个动作都没有，导致哪怕想把权交回去，也缺执行接口。

---

## 5. 差距的具体成因（代码层面）

1. **AI 决策 schema 缺 `expected_horizon`**：`TradingDecision` 有 `stop_price` / `take_profit`，但没有 horizon 字段。因此 max-hold 只能用全局 `cfg.trading.max_position_hold_hours`（72h）/ `cfg.serenity.max_hold_days`（90d）。→ AI 无法表达「我预期这笔持有 X」。

2. **AI 缺「修订持仓参数」动作**：System Operator 的动作集是 `open/close/reduce/scale_in/flip`。没有 `adjust_stop` / `revise_target` / `set_trailing` 之类的动作。`position_manager.adjust_stop()` 作为执行器原语存在，但**未暴露为 AI 决策动作**。→ 中途止损调整只能由规则（trailing/breakeven）自主完成。

3. **默认参数静默接管且永不复议**：AI 入场未给 stop/TP 时，`_effective_stop/_effective_take_profit` 用 config 默认百分比兜底（position_manager.py:126-149）。这本身合理，但**之后没有任何机制提醒 AI「这笔在用默认止损」**，config 就永久拥有了这笔的风控。

4. **thesis 失效是代理信号**：`_decide_exit` 读 `opportunity.status != "active"` 就平仓（position_manager.py:273）。这是把「研究机会被 dismiss/expire」当作「交易 thesis 失效」的**代理**，而非让 AI 判断「这笔交易的 thesis 现在还成不成立」。

5. **trailing/breakeven 是规则自主逻辑**：`_maybe_update_trailing_stop`（177-256）用 ATR / 固定百分比自主移动止损，AI 完全不在回路里。

---

## 6. 优化路线图（分阶段，风险递增）

### Phase 0 — 度量与厘清（无行为变更，先看清楚）
- **出场归因统计**：像当年 RuleSet 分析（0/208）那样，统计近 N 笔平仓中每个 reason 的占比（SL / TP / max_hold / thesis / market / AI-close 各多少）。这直接量化「现在多少出场是规则决定、多少是 AI 决定」。
- 确认线上配置里 `market_aware_exits.enabled` 的真实状态；确认 trailing/breakeven 的实际触发频率。
- **产出**：一张「谁在真正驱动出场」的实证分布图，作为后续裁决的事实基础。

### Phase 1 — 把 stop/target/horizon 变成 AI 的第一性意图
- `TradingDecision` schema 增加 `expected_horizon`（小时/天），入场时 AI 给出；用它作为该仓的 max-hold，全局计时器降级为**远端安全兜底**（例如 horizon 的 3× 或硬上限）。
- AI 省略 stop/TP 时，不再静默永久回落——标记该仓 `stop_source=default` 并作为「待 AI 复议」项注入下次 context。让 config 只做临时占位，不做永久所有者。

### Phase 2 — 新增 AI 的「修订持仓参数」动作（打通交还权的接口）
- 在 System Operator 决策动作集中新增 `adjust_position`（可改 stop / target / trailing 意图），复用已存在的 `adjust_stop()` 执行器。
- 把 trailing / breakeven 从「规则自主」改为「AI 授权的声明式意图」：
  - 方案 A（渐进）：保留 ATR trailing 作为**默认执行器行为**，但允许 AI 覆盖/关闭每仓的 trailing 参数；
  - 方案 B（彻底）：关闭规则自主 trailing/breakeven，改由 AI 每次评估时显式下发止损修订。
- **这是全案的枢纽**：没有这个动作，AI 就无法在不平仓的情况下管理持仓，权也就交不回去。

### Phase 3 — 市场反向 & thesis 交还 AI 判断
- **market-aware exit**：不再由规则引擎自动平仓/收紧。把「反向信号 score / confidence collapse」作为**高优先级唤醒事件 + context 输入**喂给 System Operator，由 AI 决定退出/减仓/持有。因默认关闭，风险低、可先做。
- **thesis invalidation**：`opportunity.status` 变化触发**优先唤醒** AI 复议该仓，由 AI 判断 thesis 是否真失效。保留代理自动平仓仅作为「AI 长时间未响应」的慢速兜底。

### Phase 4 — 补齐硬安全边界（交还策略权的安全前提）
- **catastrophic stop-loss（Layer 0）**：一个不论 AI 意图如何都强制平仓的远端硬线（例如 AI 止损距离的 2×，或固定 -15%）。**这是把普通止损自由交给 AI 的必要条件**——否则 AI 忘设/忘改止损 = 无界亏损。
- **运行时爆仓/保证金监控**：把爆仓检查从纯 pre-trade 扩展为持仓期实时监控（已有 `futures_margin_warning_ratio` / `force_close_ratio` 配置，需接入运行时）。
- 补 OKX/Binance live 订单对账路径（当前仅告警，BACKEND_ISSUES §16.2）。

### Phase 5 — 评估：AI 出场是否真的更好
- **Shadow 对比**：并行记录「规则会怎么出」vs「AI 怎么出」，度量两者的 realized PnL / 回撤 / 持有时长分布。把「AI discretionary exit 优于 rule exit」当作**可证伪的假设**来验证，而不是默认成立。
- 若某类出场（如纯 SL 执行）AI 并不能改进，就理直气壮地把它留在 Layer 1 作为纯执行——**目标是正确的权威归属，不是「AI 决定一切」**。

---

## 7. 风险与权衡

- **延迟**：AI 无法 tick 级反应——所以是「声明式意图 + 执行器执行」，不是「AI 实时决定每次平仓」。这条不能违背。
- **成本**：更频繁唤醒 LLM 有 token 成本。事件驱动唤醒（止损逼近、反向信号、thesis 变化、horizon 临近）是效率与响应度的折中，优于纯轮询。
- **安全**：把止损自由交给 AI，**必须先有 catastrophic stop 兜底**。Phase 4 是 Phase 2/3 的安全前提，实施顺序上可并行但不可省略。
- **回归风险**：RuleSet exit 的教训是「已布线未引爆的层会静默覆盖 AI 自主权」。任何新的「规则自主出场」都应有 spec 锁定其边界（参照 PL-PM-08 的做法）。
- **可证伪**：整个改造的价值假设是「AI 自主出场 > 规则出场」。Phase 0 与 Phase 5 的度量是防止「为了纯粹而纯粹」的护栏。

---

## 8. 一句话裁决

> **保留 Layer 0 硬安全边界（并补齐 catastrophic stop 与运行时爆仓监控）；把 trailing / breakeven / max-hold / 默认 TP / market-aware / thesis 这些「规则自主的策略行为」改造为 AI 授权、执行器高速执行的模式；为 System Operator 新增 `expected_horizon` 字段和 `adjust_position` 动作，作为交还策略权的两个工程前提。** 这样 Aletheia 才从「AI 入场 + 规则出场的 hybrid」真正变为「AI 授权、机器执行、AI 复议」的 discretionary trader。

---

## 附录 A — 精确代码位置索引

| 主题 | 文件:行 |
|---|---|
| 出场主循环 | `aletheia/portfolio/position_manager.py:346` `evaluate_open_positions` |
| 机械退出判定 (SL/TP/max_hold/thesis) | `position_manager.py:258-279` `_decide_exit` |
| trailing / breakeven | `position_manager.py:177-256` `_maybe_update_trailing_stop` |
| 默认 stop/TP 回落 | `position_manager.py:126-149` `_effective_stop` / `_effective_take_profit` |
| 交易所括号同步 | `position_manager.py:282` `_sync_exchange_closed` |
| 止损调整原语（未暴露为 AI 动作） | `position_manager.py:441-466` `adjust_stop` |
| market-aware 规则引擎 | `aletheia/portfolio/exit_rules.py:27-95` |
| market-aware 配置与默认阈值 | `aletheia/config/schema.py:432-442` |
| 市场信号来源 | `aletheia/portfolio/market_signal_reader.py:43-120` |
| System Operator 决策入口 | `aletheia/agent_shell/trading_decision.py:232` `evaluate_trading_opportunity` |
| AI 退出决策 (close/reduce) | `trading_decision.py:1609` |
| AI stop/TP 提议与回落 | `trading_decision.py:517-541` |
| pi_daemon 节律 | `aletheia/agent_runtime/pi_daemon.py:104-156` |
| RuleSet exit 移除 spec | `tests/specs/spec_pipeline_position_manager.py:441` (PL-PM-08) |
| 账户级日损 + 自动 kill-switch | `aletheia/risk/engine.py:269-294` |
| kill switch | `aletheia/risk/kill_switch.py:1-54` |
| 爆仓缓冲检查 | `aletheia/risk/engine.py:921-941` + `aletheia/trading/instruments.py:226-239` |
| 对账完整性冻结 | `aletheia/portfolio/reconciliation_integrity.py:60-150` |
| 学习全局冻结门 | `aletheia/learning/control.py:73-89` |

## 附录 B — 与用户初始判断的对照

| 用户判断 | 核实结论 |
|---|---|
| 出场仍相当 rule-based | ✅ 成立（always-on 规则出场是主体） |
| market-aware 是确定性规则引擎 | ✅ 成立，但**默认关闭**，需显式开启 |
| RuleSet exit 已删除 | ✅ 已于 2026-07-02 移除并 spec 锁定 |
| 保留了 SL/TP/max-hold/market-aware/PI 多个出场源 | ✅ 成立 |
| AI 可在入场提出初始 stop/target/horizon | ⚠️ 部分：stop/target 可提，**horizon 无字段** |
| 后续策略性调整应由 System Operator 作出 | ⚠️ 应然如此，但**当前 AI 无中途改止损的动作**，由规则接管 |
| Aletheia 是 hybrid strategy system 而非纯 AI discretionary trader | ✅ 成立 |
