# 设计：System-Operator 集成的动态仓位决策 + 交易 Persona + Learning 策略域

> **STATUS: P0–P2 已有生产消费者；P3–P4 为实验或 helper（2026-07-28 能力事实复核）。**
> 金字塔当前只有经测试的纯 `pyramid_plan` helper，没有 Scheduler/Operator 生产调用者，也没有
> paper/demo/live 的 Risk→Order→Fill→Position materializer。Settings/API 已阻止新开启。
> 落地映射:P0=`3e8015f`(视图完整性门,修 #1/#2)· P1=`ad580a1`(persona 区间 schema+resolver)·
> P2=`9c27dc4`(Operator 规则式上下文决策)· P3=`3fbdd79`(regime 干净窗学习)· P4=`884e035`
> (渐进解冻+金字塔 policy)。specs:PS-S01..S12 + PL-ADOPT-19..21。所有 flag 默认 OFF。
>
> **2026-07-28 能力事实修正**:定时器间隔和宽泛健康检查不构成当前 Persona/策略步骤的前瞻证据。
> `persona_rollout` 现为只读 safety hold，不再自动打开 progressive unlock 或 persona learning；
> GitHub #409 跟踪真正的逐步授权证据缺口。
>
> **2026-08-11 sizing 语义校正**:当前实现中的 `kelly_fraction` /
> `vol_kelly_cap_fraction` 是历史命名的 conviction-policy scaling factor，不是
> Kelly 估计。它没有消费经校准的胜率、payoff odds 或收益分布。本文中涉及
> 现有字段的“Kelly”均应按历史名称理解；真正的 fractional Kelly 仍是未实现、
> 需要独立校准与样本外证据才能授权的未来策略。
>
> **owner 开启顺序建议**:先 `persona_sizing_enabled`(选 persona,静态 init)→ 跑一段干净窗 →
> 开 progressive_unlock(逐个解冻)→ 数据够了开 persona_learning → 最后(单独授权)接金字塔实盘。
> 每步先 demo、看 doctor `loop.sizing_policy` + 干净窗指标,退化则关回。

> (原始设计,已实施:)owner 定调:仓位/杠杆/加仓/单笔止损
> 不再是静态配置,而是在**风控硬边界 ∩ persona 区间**内由 **System Operator 按上下文动态决策**;并把
> 这个决策空间纳入 **Learning(Evolve)** 作为可学习策略域;用**渐进冻结/解冻**控制复杂度。
> 承接并升级 `docs/research/trading-strategy/POSITION_SIZING_AND_PERSONAS_2026-07-01.md`(那份的三档静态草案 → 本份的
> 区间+决策者+学习者三层)。相关:[[aletheia-operator-self-supervision]] / [[aletheia-self-evolution]] /
> [[aletheia-reconciler-false-orphan-thrash]] / [[aletheia-ai-native-bugfix-path]] / [[aletheia-vision-and-win-lines]]。

---

## 1. 核心原则:三层决策盒(区间 → 决策者 → 硬边界)

每次开仓,最终仓位由三层夹逼产生:

```
final = clamp(  Operator.decide(context)  ,  persona_bounds  ,  risk_envelope  )
                └── 决策者(动态)         └── 区间(可配)   └── 硬边界(不可越,信任基石)
```

- **风控引擎 = 外层硬边界(immutable)**。max notional / max leverage / 单笔·总亏损上限 / kill switch —
  Operator 与 Learning **永远不能越过或扩大**。这是 VISION"信任 > 智力"中心赌注的落点:给 AI 一个
  它无法撑破的盒子,再让它在盒内自由。
- **Persona = 区间(min / init / max)**。owner 配置的"档位边界",不是固定值。
- **System Operator = 决策者**。在 `[persona ∩ envelope]` 内,按上下文每次开仓给出**具体值**。
- **Learning = 策略**。把"上下文 → 选值"拟合成策略,用**干净窗**结果反馈优化。

> 关键:persona 给**范围**,Operator 给**当次的值**,风控给**永不可越的顶**,Learning 给**如何选值的策略**。
> 四者职责互不越界——这正是可控地把大自由度交给 AI 的方式。

---

## 2. 哪些变量变成"动态决策"(决策变量表)

| 变量 | 现状(静态) | persona 提供 | Operator 在区间内决 | 风控硬顶(不可越) |
|---|---|---|---|---|
| 基准仓位(占权益%) | `default_notional_usdt=$25` 固定 | {min%, init%, max%} | 当次 % | `max_*_position_notional_usdt` |
| 杠杆倍数 | 主5/中3/山寨2 固定 | {min, init, max} | 当次倍数 | `max_futures_leverage_*` |
| 历史 `kelly_fraction` 缩放因子 | 0.10–0.25 | {min, init, max} | 当次 scaling | notional 硬上限 |
| 加仓(金字塔)次数 | 无 | {0, init, **max_adds**} | 当次加几次 | 总敞口上限 |
| 每次加仓的浮盈滚入比 | 无 | {min, init, max} | 当次比例 | 总敞口上限 |
| 单笔最大亏损 | `default_stop_loss_pct=2%` | {init, **max_loss**} | 当次止损带 | 单笔·日亏 kill |
| 操作员收紧上限本身 | 现操作员单向收紧到 $20 | {init, max} | 收/放到某值 | floor/ceiling |

> 现有"操作员自动收紧 `max_demo_position_notional_usdt`(200→26.84)"= 本框架的一个**特例**:
> distress 时在区间内向保守端移动。纳入统一策略后,它不再是孤立的单向棘轮,而是策略的一个输出维度。

---

## 3. Operator 决策的输入(上下文特征)

owner 明确要求综合:**(a) 时段 (b) 标的+市场情绪 (c) BTC 大盘价值水平**。完整特征集:

- **(a) 时间/时段**:UTC 时段、周内日、是否临近资金费结算/宏观事件窗。
- **(b) 标的 + 情绪**:当前 regime(bull/bear/sideways/volatile)+ 置信;F&G 指数;该标的 ATR%/波动;
  funding/crowding;相对强弱。
- **(c) BTC 大盘**:趋势/估值档(如价格 vs 长期均线/已实现价格带、regime、主导地位)——决定整体"敢不敢放大"。
- **账户态(必须来自完整视图,见 §4)**:真实权益、当前总敞口/保证金占用、回撤、近期 WR/连胜连负。
- **学习到的策略(§7)**:在相似上下文下历史上什么档位/参数组合表现最好。

Operator 把这些映射到"当次的仓位/杠杆/加仓/止损",clamp 进 persona 区间与风控硬边界。

---

## 4. 安全地基(P0 前置——必须先于动态 sizing 落地)

动态 sizing 的前提是"账户态可信"。当前有一个已确认的脆弱点会同时毒害 sizing 与 reconciler:

### 4.1 活跃 broker 视图完整性门(一次修 #1 + #2)

**已确认证据(2026-07-01)**:UTA 层 `get_unified_state` 已分别记录
- `errors`:查询失败的 broker(实测 OKX 挂时 `errors=['okx_demo: ...50001...']`)
- `connected_brokers` / `accounts`:实际返回账户的 broker

OKX API 一挂(`50001`),unified 视图里**只剩 bitget,OKX 落入 errors**。于是:
- **#2(thrash 复发)**:`classify_positions` 用 unified 的 positions(此刻不含 OKX)→ 本地 OKX 持仓全被
  判 orphaned → 平掉(仅本地)→ OKX 恢复后重认领 → 又一轮 thrash。**这是我此前 P0 修复(处理"unified
  过期→读错 Bitget 缓存")未覆盖的变体:unified 新鲜但缺活跃 broker。**
- **#1(sizing 权益基准错)**:任何"权益 ×%"的 sizing/风控上限,此刻会拿 bitget 的 $167 而非真实 OKX 权益。

**统一修法(一个谓词)**:`active_broker_view_complete()` = 活跃 paper broker(okx_demo→broker_id 含 "okx")
是否在 `connected_brokers` 里。
- reconciler:若活跃 broker 不在 connected_brokers → 视图对该 broker 不完整 → **不得孤儿化其持仓**(bail,
  沿用现有"看不清就全 managed"安全分支)。这是 §我此前 P0 修复的自然延伸。
- sizing/风控:若活跃 broker 未连 → **不得放大 / 退回最保守基准 / 或暂停开新仓**,绝不拿残缺权益硬算。

> 这条是动态 sizing 的**硬前提**:Operator 敢按权益动态放大的前提,是它读到的权益一定是活跃账户的真值。

### 4.2 风控硬边界 immutable

`max_*_notional` / `max_*_leverage` / 单笔·日亏 kill / live 人工解锁 —— 这些是 Operator 与 Learning
**永不可写、永不可越**的外盒。任何动态/学习只在盒内。触信任边界的改动永远走人工。

---

## 5. Persona = 区间,而非固定值

重定义 persona:`{param: {min, init, max, operator_decidable: bool}}`。

| Persona | 定位 | 基准% 区间 | 杠杆区间 | 加仓 max | 单笔止损 max | Learning |
|---|---|---|---|---|---|---|
| **conservative**(默认) | ≈现状,窄区间 | 0.03–0.5% | 1–5x | 0 | 2% | 关(或最慢解冻) |
| **balanced** | 温和 | 0.5–2% | 1–5x | 0–1 | 2% | 部分解冻 |
| **roll(滚仓)** | 小本高杠杆博超额 | 0.2–1%(小本) | **5–20x** | **≤N 次金字塔** | **0.5–1%(极紧)** | 更快解冻 |

- 每档标注哪些参数 `operator_decidable`(交给 Operator 动态决),哪些锁死。
- 每档**必带、不可被 persona 关闭的硬护栏**:单笔最大亏损绝对值、总敞口上限、自动去杠杆/kill、
  §4.1 视图完整性门、live 人工解锁。
- owner 对每档只需给 `[init, max]`(部分给 min),**具体值由 Operator 在区间内决**(对应 owner 的
  "提供档位,最终由 Operator 综合判断")。

---

## 6. System Operator 集成(决策放在哪)

- 三组件复述(别混):operator-shell(TS,人机面)/ `operator_loop.py`(MAPE-K 控制面)/
  `pi_daemon.py`(交易执行触发)。
- **新增一个"sizing 策略"决策点**:开仓时(pi_daemon/paper_trader 触发前),调用 Operator 的
  sizing policy,输入 §3 上下文,输出当次决策值,再过 persona clamp + 风控硬边界。
- 现有 `operator_loop` 的参数收紧/放松 → 收敛为该 policy 的一个输出维度(distress 时向保守端移动),
  不再是孤立棘轮。
- **边界([[aletheia-ai-native-bugfix-path]])**:Operator 决策**数据面/运行态参数**(在盒内),**不改代码**;
  代码级仍"出计划、人把关"。

---

## 7. Learning 集成:把决策空间当可学习策略域

owner 要点:把 Operator 可抉择的**大参数值域**纳入 Learning,让它自行拟合/搜索/研究"什么情况用什么档位、
参数关系如何变化最好"。

- **框架**:contextual bandit / 策略搜索。
  - **state** = §3 上下文特征(regime/F&G/BTC 档/标的波动/时段/近期 WR)。
  - **action** = 在 persona 区间内选的一组参数值(基准%/杠杆/kelly/加仓/止损)。
  - **reward** = 该次交易的**风险调整后收益**(不是裸 PnL;计入回撤/方差)。
- **必须只用干净窗**([[aletheia-reconciler-false-orphan-thrash]] 的血泪):thrash/synthetic/adopted 碎片
  会毒化 reward → 学到噪音。Learning 的样本必须过 `close_reasons.is_synthetic_close` 过滤 + 要求真实归因
  (strategy_id/opportunity_id 齐全)。**没有干净数据,宁可不学。**
- **探索受硬边界约束**:探索(试更大仓位/杠杆)永远 clamp 在风控硬边界内;探索预算本身也是被 kill
  switch 保护的。
- **学到什么**:上下文→档位映射;参数间关系(如"高波动+bear 时,小基准+高止损纪律的滚仓期望更优");
  逐步逼近"在什么条件下滚仓值得、什么条件下必须收手"。

---

## 8. 渐进冻结/解冻(复杂度控制——owner 的核心要求)

一口气把所有参数交给学习必然混乱、无法收敛。采用**解冻阶梯**:

- **初始**:所有 sizing 参数**冻结**在保守静态值,Operator 决策 = persona 的 init 常量,**Learning 关**。
  (等价于"稍微放大版的现状",零学习风险。)
- **逐个解冻**:一次只解冻**一个参数(或一小组)**,其余保持冻结安全值。每次解冻的门槛:
  1. 干净窗样本足够(该 persona 下 ≥K 笔带完整归因的真实成交);
  2. 当前已解冻参数的策略已**稳定**(近 M 窗策略输出方差收敛、reward 不再退化);
  3. 无 active kill/distress。
- **建议解冻顺序**(从影响可控→影响大):① 基准仓位% → ② 杠杆 → ③ 单笔止损带 → ④ 加仓/金字塔 → ⑤ Kelly。
  每个参数解冻时先给**窄搜索区间**,随信心增长再放宽(区间本身也是渐进的)。
- **自动再冻结(auto-refreeze)**:某参数解冻后策略退化(reward 掉、回撤超阈)→ **自动冻回安全默认 + 通知**。
  这就是把本会话的"reconciler 断路器 / doctor 探针 + 自愈"模式**套用到 learning 回路**:
  监测策略健康 → 退化即熔断回退 → 报人。

---

## 9. 与既有自监管/自愈的衔接

- 复用本会话刚建的模式:doctor 探针(新增"sizing 策略健康"探针:解冻参数的 reward/方差/越界尝试)+
  运行态断路器(策略退化→auto-refreeze)+ Level-1 通知(operator self-supervision)。
- 复用 §4.1 视图完整性门:sizing 与 reconciler 共享同一"活跃 broker 是否可信"谓词。
- 边界不变:数据面自愈(冻结/回退参数)自动做;代码级出计划人把关。

---

## 10. 分期实施(每期 SDD + 干净窗观察后再进下一期)

- **P0(前置,必须先做)**:§4.1 活跃 broker 视图完整性门 —— 修 #2(reconciler 不孤儿化未连 broker)+
  #1(sizing 不用残缺权益)。**这是一切的地基**(owner:#1 肯定修,#2 先分析确认——已确认,见 §4.1)。
- **P1**:persona-as-bounds schema + Operator 读区间取 **init 常量**(无上下文、无学习)。只加 conservative
  → 零行为变化;加 balanced/roll 供切换。
- **P2**:Operator **规则式**上下文决策(regime/F&G/BTC 档/波动→在区间内选值),仍不学习。
- **P3**:Learning 策略上线,**只解冻 1 个参数**(基准%),干净窗门 + auto-refreeze。
- **P4**:按解冻阶梯逐个放开(§8)+ 金字塔加仓(仅 roll,受总敞口硬上限)。
- 全程:风控硬边界 immutable,live 人工解锁,demo 先行。

---

## 11. 风险与信任纪律(必须正视)

- 这是**显著扩大 Operator 对"影响真钱的仓位/杠杆"的自主权**——爆炸半径大。让它安全的四根柱子:
  ① 风控硬边界 immutable(盒子撑不破)② live 永远人工 ③ Learning 只吃干净窗(否则学噪音)
  ④ 渐进解冻 + auto-refreeze(一次只放一点,退化即回退)。
- **反面教材就在手边**:reconciler thrash 证明了"污染数据会毒化整条下游";若 Learning 在 thrash/adopted
  碎片上学 sizing 策略,会学出灾难。所以 §7 的干净窗门不是可选项,是先决条件。
- **诚实的复杂度账**:这套东西复杂度高。P0-P2(硬边界+区间+规则式上下文)已能满足"仓位随情况动态、
  由 Operator 决"的绝大部分价值,且风险可控;P3-P4(学习+解冻)是"自进化"的增量,应在前面跑出干净窗、
  确有必要时才推进(符合 [[aletheia-self-evolution]] 的分层节奏)。

---

## 12. 初步 max 数值(owner + operator 已定,2026-07-02)

依据:经典 1%/2% 单笔风控铁律、分数 Kelly(0.25-0.5)、零售永续杠杆规范、Turtle 式递减加仓+总量封顶。
owner 拍板三项高风险数字,其余按下表编入 `_default_personas`(commit 见 STATUS 头):

- ✅ **Roll 最大杠杆 = 10x(硬顶 15x)**(20x 强平缓冲太薄被否)。⚠️ 现风控 `max_futures_leverage_demo=5`
  仍会 clamp 到 5x —— 真要跑 10x 需 owner 单独抬这个信任边界配置(未动)。
- ✅ **Roll 单笔最大亏损 = 1.0%**(1% 铁律;`per_trade_loss_pct` max=1.0)。
- ✅ **金字塔总加仓 ≤ 1× 初始仓(总敞口 ≤ 2×)** + max_adds=3 + 单标的敞口硬顶 —— `pyramid_plan` 已实现双 cap。

已编入默认值:conservative 单笔 1%/杠杆≤3-5x;balanced 单笔 1.5%/杠杆≤5x;roll 见上 + Kelly≤0.5、
基准 $20-200(≈权益 0.02-0.2%,小本)。**仍待 owner**(非阻塞,后续按需):
- 各档基准若要改用"%-of-equity"而非 $ 名义(现为 $;设计倾向 % — 需一次单位切换)。
- BTC 大盘档 §3(c) 的专用度量(现用 regime 代理;可换 SMA 带/已实现价格)。
- 解冻门槛 K(现 persona_unlock_step_samples=25/persona_learning_min_samples=15,可调)。

---

## 13. 交叉引用
- 现状与三约束根因:`POSITION_SIZING_AND_PERSONAS_2026-07-01.md`
- sizing 代码:`trading/sizing.py`、`risk/engine.py:122-128`、`agent_shell/operator_loop.py:819-874`
- 视图完整性证据:`portfolio/uta/manager.py`(errors/connected_brokers)、`portfolio/exchange_sync.py`
- 自监管/自愈模式:`ENTRY_EXIT_ASYMMETRY_ANALYSIS` + `POSTMORTEM_reconciler_thrash_2026-07-01.md`
- 自进化节奏:`docs/archive/legacy/plans/learning/self_evolution_plan.md`([[aletheia-self-evolution]])
