# 平台文档定义 & 整体设计审查（合伙人视角）

> 范围：从数据采集 → 数据处理 → System Operator / PI Agent 决策整合（开/平仓）→ 风控，端到端审查"文档定义"与"整体设计"的不足，并给出尊重 MVP scope 的建议。
> 方法：5 路并行深读（开仓脊柱 / 平仓退出 / 风控信任边界 / 监管+学习控制面 / 文档金字塔元审计），每项带 file:line 证据；承重结论作者本人复核。
> Date: 2026-06-30 · 作者：Claude (Opus 4.8) · 性质：L4 研究产物（非契约，供后续 L2 契约工作引用）

---

## 0. 一句话结论

**你的文档金字塔本身是强项，不是弱项。问题不在"缺文档"，而在文档的切分维度：现在所有契约都是按子系统"竖切"的（风控 / 执行 / agent / 知识…），而你这次问的"端到端流"是"横切"的——而横切的那条线（跨子系统的接缝）几乎无人定义。** 真正的失败面就在这些接缝上。

附带一个高危设计发现：**系统 `paused` 时，确定性止损/止盈循环整个停摆，而 PI 平仓路径却不停**——这个不对称今天只影响 paper/demo（低实盘风险），但它是一个未定义的不变量，会在接上实盘平仓路径的那一刻变成地雷。

---

## 1. 先肯定：这套体系真正强的地方（避免推倒重来）

作为合伙人，先说清楚哪些**不要动**，否则建议会变成破坏：

1. **四份信任边界契约（risk / execution / live_unlock / promotion_gates）是真金标准。** 每份有 file:line 锚点、不变式声明、可机器验证的 spec claim，细到照着能实现——这在同类 MVP 里极罕见。
2. **`execution_path_honesty.md` 的"三分诚实"**（LLM 决定方向 / 确定性管控下单 / 死字符串策略层）主动揭露语义不诚实，比藏着问题强得多。
3. **`DOC_SYSTEM_PLAN.md §6` 记录了"哪些审计建议被拒绝及理由"**（过度结构化 / 改名破链接）——把"防过度工程化"这件事本身文档化了，是元认知层面的正确做法。
4. **`SYSTEM_MAP` 的管道断裂符号 + A1–C2 断裂表**：系统不假装管道完整，断裂被显式标注、每条有根因和修复指针。
5. **审计链双链设计**（JSONL 文件级 SHA-256 + TaG 行级 hash chain）是契约→代码→spec 三角闭合的最佳范例。
6. **Module J（`memo_effectiveness.evaluate_prior_memo`）是你的中心赌注真正在跑的那一环**：每次合成新 memo 前先给上一份打分，`overturned_rules` 透传进下一份的 `prior_corrections`，形成"可被后续证据推翻"的 refutable learning loop。这是"harness 而非智力"假设的活体现，必须保护。

---

## 2. 一个能解释一切的组织模型："两个环 + 一本账 + 一堵墙"

你问的"数据→处理→Operator/PI→风控"如果硬画成一条直线，永远会和现实打架，因为现实里有**两个节奏完全不同的控制环**，它们被混在"PI / Operator"一个名字下，这正是历次审计反复误报、`operator_layer.md` 顶部要挂"别混三个组件"警告框的根因。

把它拆成下面四个一等对象，整个系统就清楚了：

### 环 1 — 交易决策环（分钟级，像 OODA：观察-定向-决策-行动）
> OODA = 战斗机飞行员的决策循环：Observe（观察）→ Orient（定向）→ Decide（决策）→ Act（行动）。

```
数据采集(Observe)            collectors/* → market_snapshots …
  → 上下文+排序(Orient)      market_observer → opportunities → paper_trader_ranker(纯确定性打分)
  → PI LLM 定夺(Decide)      trading_decision.py:153  conviction≥55 才 trade
  → 提案+执行(Act)           gateway.submit_proposal → 风控 → order → fill → position
```
**决策权落点已核实：在 `trading_decision.py:153` 的 LLM**（conviction≥55 阈值 `_MIN_CONVICTION_TO_TRADE`）。`paper_trader` 在 Phase 4a 后只写 `signal_pending` 信号，**不再自己开仓**——PI 是唯一开仓决策者。这一点代码做得干净，但文档还停在旧描述（见 §3）。

### 环 2 — 系统监管环（小时级，像 MAPE-K：监测-分析-计划-执行）
> MAPE-K = 自管理系统的标准控制环（IBM 自主计算）：Monitor → Analyze → Plan → Execute，共享一份 Knowledge。

```
心跳/健康(Monitor)      operator-shell/src/operator_loop.ts: heartbeat + loadHealthStatus
  → 推荐引擎(Analyze)    recommendation_engine.ts (纯 TS, 无 LLM)
  → 计划(Plan)          repair_engine.ts: auto_execute | proposal_only
  → 自愈/退役建议(Execute) 经 HTTP 调 Python operator_loop.py / pi_decisions
                         共享 Knowledge: 知识库 + 学习产物
```
这个环负责"机器健康吗、要不要自愈、策略要不要退役、学习有没有在产出"。它**不该碰**"交易什么"。

### 账 — 共享账本（Trading-as-Git / 事件溯源）
```
opportunity → PiDecision → TradeProposal → RiskDecision → Order → Fill → Position → Exit → Review
```
用 hash chain 串成不可变审计链。这是两个环唯一正当的交汇点（都往 `pi_decisions` 写，靠 `signal_source` 区分）。

### 墙 — 风控引擎（饱和块 / 硬控）
> 控制论里有"控制器"（连续调节）和"饱和块/限幅器"（把信号夹死在边界内）。**风控引擎是限幅器，不是控制器**——它不调节策略，只在 Decide 和 Act 之间立一堵不可绕过的墙。

这正是 VISION 说的"犯错许可证"的物理实现：因为有这堵夹死边界的墙，AI 才敢边做边学。`drawdown_scale`（按回撤缩小仓位上限）才是那个"控制器"，它和墙是两回事，不要混。

**这套模型直接给出文档该怎么切：让"两个环、那本账、那堵墙"各自成为一等文档对象。** 下面的差距全部可以挂回这四个对象。

---

## 3. 现状 vs 应然：文档定义层面的差距

### 3.1【高】整条交易决策环（环 1）没有单一治理文档 —— 核心缺口
- 现在要重建这条链，必须同时读 `SYSTEM_MAP §II` + `agent_contracts` + `execution_contract` + `risk_engine_contract` + `PI_OPERATOR_DESIGN_DECISIONS` + 2 个 spec，**还拼不全**：3 层 Context 构建、debate 触发区间 [40,80]、conviction 阈值 55、`_attach_opportunity_context` 归因不变量，全都不在任何单一文档里。
- `execution_contract §1` 的因果链**只从 `trade_proposals.id` 开始**；`trust_and_change_control §1.1` 明确声明因果链从 proposals 开头、**不覆盖 opportunity 阶段**。也就是说账本的上游半截（opportunity→PiDecision→TradeProposal）在文档上是空白。
- 接缝上的关键中间载体**全无契约**，只有代码约定：
  - `Opportunity` 字段语义（`models.py:45`，20+ 字段；`direction_bias` 枚举、`confidence` 到底是 0–1 还是 0–100、`market` 字段如何变成 `market_type`）——无文档。
  - `TradingDecision` dataclass（`trading_decision.py:56`，LLM→proposal 的唯一载体，3 个模块消费它）——无文档。
  - `signal_pending → PI Layer2 pending_signals` 接缝（`trading_context.py:459-472` 用 raw SQL 读）——无文档。
> 历史上"symbol 格式不匹配静默丢机会""rationale 拼串截断"这类 bug，根因都是**接缝无契约**：上游写入方和下游消费方对字段含义的约定只活在代码里。

### 3.2【高】"两个环"没有形式化边界 —— PI Agent 混称的文档根因
- `PI_OPERATOR_DESIGN_DECISIONS §问题2` 描述了两种节奏，但**无不变式**；`operator_layer.md:517-531` 有两处 Level 分类**自带免责声明**（"以 `_LEVEL_2_ACTIONS` 为准，另一处是历史遗留"）。
- 两个环都往 `pi_decisions` 表写，靠 `signal_source` 区分（trading 写一种值、operator 写另一种），但**没有任何 spec 锁死 `signal_source` 的合法值域**——下游"必须按来源区分"这个不变量无人守。
- `operator_layer.md` 顶部的"PI Agent = 三个组件，别混"警告框，本质是**文档在替你喊"这里的对象边界划错了"**。

### 3.3【高】平仓/退出被当成二等公民，且藏着一个实盘地雷
- **文档严谨度**：开仓侧有完整 `§1–§6`（因果链/原子性/对账/审计），退出侧在 `execution_contract` 里**只有 `§4` 一行**（"平仓前取消挂单"）。`position_manager.py:59` 的 live 守卫、`:92-115` 的 stop/tp 兜底公式、退出触发器优先级，全无 L2 契约、无不变式编号。覆盖度差距约 60%。**钱是在平仓时真正赚到或亏掉的，这一侧反而最薄。**
- **实盘地雷（设计层，已复核）**：`scheduler.py:970-971` —— `paused=True` 时 `_run_position_manager` 和 `_run_position_reconcile` 直接 return。即**系统暂停时，确定性止损/止盈循环整个停摆**。而 PI 的 `review_portfolio_state` 平仓路径**不检查 paused**（`commands.py` 里 paused 只用于显示快照），所以暂停时 PI 还能平、确定性循环不能平——**行为不对称且无文档**。
  - 校准：今天只影响 paper/demo（`live_limited` 本就被自动循环过滤；bitget_demo 的交易所原生 TPSL 仍会在交易所侧触发，恢复后 `_sync_exchange_closed` 收敛）。`paper_internal` 模拟仓位则**真的失去保护**。
  - 风险点在未来：一旦接上实盘平仓路径，这个"暂停=止损失效"的未定义语义就是地雷。对照之下，`kill_switch` 不阻止平仓的设计是对的，且有代码注释+RUNBOOK+测试三处锁定——**`paused` 应该向 `kill_switch` 看齐，但现在没有**。
- **契约漂移**：`agent_contracts.md:151` 把"PI exit decision"列为退出触发器，但全库 grep 无此实现（PI 走独立的 `review_portfolio_state`，与自动循环解耦）。

### 3.4【中】风控墙：契约落后于代码，且有个"同名脱钩"的混淆点
- **16 vs 19 漂移（已复核）**：`risk_engine_contract.md` 标题白纸黑字写"代码实际 16 关"，表格止于第 16 关；但代码已到 19+（后加 `6b cross_broker_dedup`、`17 account_leverage_ratio`、`18 liquidation_buffer`、`19 per_symbol_cumulative_notional`，`engine.py:145/276/280/289`）。契约自己的断言已过时。
- **两个"guard"都脱钩，且都叫 guard（已复核）**：
  - `trading_decision.py:218` 的守卫链 → 写进 `decision_meta["guards"]`，**advisory，不硬拦**（spec PI-S02 锁）。
  - `trust/guard_chain.py` 的 `run_guards()` → **在 gateway.py 里零调用**（grep 确认），它的 `block` 裁决**完全没有执行力**。
  - 两个 advisory/脱钩的东西都叫"guard"，新人极易误以为它们是风控墙的前置硬关。唯一硬门是 `risk/engine.py:evaluate_proposal`——这句话没写进任何契约。
- **OKX/Binance 实盘无对账（CR-EX-01）**：`reconciliation.py:43-68` 只处理 `bitget_live_limited`，OKX/Binance 实盘敞口不进对账，会让风控 check #12/#17 的敞口算少。当前实盘只 Bitget，影响有限，但实盘扩展时是隐患。

### 3.5【中】投资组合构建 / 跨标的资金分配：一等对象缺失
- sizing 逻辑两路并存且无决策树：`paper_trader.py:124` 固定 $25 notional vs `trading/sizing.py:19-64` Kelly scaling。`execution_contract §5` 诚实标注了两条路径，但**没声明何时用哪条**。"如何在多个机会间分配资金"是个一等问题，现在无契约。

### 3.6【中】学习闭环（环 2 的 K）：一个真闭环 + 几条需诚实归类的轨道
SYSTEM_MAP 的断裂表 ~30% 已过期，需按当前代码重新归类：
- **真闭环**：Module J（§1.6）+ `reasoning_feedback` → 注入 PI Layer2，`clean_window_cutoff` 隔离 Round 1 脏数据（IFL spec 守）。这条**活着且有效**。
- **A1 ps_lessons 双轨**：`ps_lessons` 有完整生命周期（accept→mark_applied，LP-S09/10/11 绿），但**不进 `knowledge_fts`**，`retrieve_lessons()` 检索不到它。这是"设计选择（人工策略层 vs 机器产出层）"还是"遗漏"？**文档没说**——需要在 `knowledge_contract` 明确二选一。
- **A2 ps_skills 空壳**：`SkillStore` CRUD 在，但全库**无任何生产者**（只有 GET 端点）。建议标 `aspirational-infrastructure`，别删（留给未来），但要写清"当前无生产者无消费者"以防审计误报。
- **B1 contradiction stub**：`manager.py:305` 永久 stub 返回 []，**既未实现也未计划**。二选一：删接口 / 加 tripwire spec 绑到 Tier 2 自演化。
- **TS operator 的 K 盲区（A4 最大缺口）**：监管环**调度**学习任务（dreaming due/promotion due），但只看产物**条数**，从不读产物**质量**（`memo_effectiveness` 的 `accuracy_score` 写回了 `knowledge_items.tags`，但 TS operator 无接口读它）。**它在管"学了没有"，却不管"学得好不好"**——而 VISION 的赌注恰恰是后者。
- **TS operator 完全无行为 spec**：现有 operator spec 全指向 Python 内层（`operator_loop.py`），3000+ 行的 `operator_loop.ts` 无任何 spec claim。

### 3.7【低】文档体系自身的几处卫生问题
- **"契约自带免责声明"现象 ≥5 处**（risk 16 vs 五关、execution §9 死字符串、knowledge §2.1 指出 agent_contracts:185 漂移、operator Level 分类×2）——建议加一条治理规则：契约内的漂移标注必须在 `BACKEND_ISSUES.md` 有对应条目，让它们有统一落点而非孤立堆积。
- **`data_layer.md` 层次错位**：内容是 L3 计划（OpenBB 重构分析），却在 README 被标为 L2 契约——应移到过程产物区。
- **`SYSTEM_MAP` / `PIPELINE_GAPS` 过期**：`MarketAwareExitsConfig` 标 0% 前端覆盖，实际 `Settings.tsx:1648` 已接入 5/9 字段。

---

## 4. 建议（按杠杆排序，尊重"不过度工程化"）

### 必须做（高杠杆、低成本、几乎纯文档）

1. **新建一份《决策生命周期契约》`decision_lifecycle_contract.md`（~60–80 行）。**
   只做一件事：把 `opportunity → PiDecision → TradeProposal → RiskDecision → Order → Fill → Position → Exit → Review` 这条横线上**每个接缝的前置/后置条件 + 守卫位置**列出来，**不重复**子系统细节（指向已有契约即可）。这一份同时关掉 §3.1 的脊柱缺口和所有"接缝无契约"。这是唯一值得**新建**的契约——因为它横跨所有子系统，没有现成的家。

2. **形式化两个控制环 + 锁死 `signal_source`。**
   给 `PI_OPERATOR_DESIGN_DECISIONS.md` 补一节"两环不变式"（~10 行）：环 1 写 `signal_source=trading_decision`，环 2 写 `signal_source=operator_assessment`，下游必须按来源区分。加 1 个 spec claim 锁死枚举值域。这是从文档层根除"PI Agent 混称"的最小动作。

3. **退出路径升为一等公民 + 钉死 `paused` 语义。**
   - `execution_contract.md` 补 `§10 平仓通路`：退出触发器优先级（SL/TP > ruleset > market_aware > max_hold；PI 路径独立）、`close_position_routed` 路由契约、live 守卫不变式（`position_manager.py:59`）、stop/tp 兜底公式。加 2–3 个 spec claim。
   - **先决定 `paused` 对平仓的语义**（推荐：`paused` 不阻止止损平仓，与 `kill_switch` 对齐），改 `scheduler.py:970` 把 paused 检查收窄到"只挡开仓不挡保护性平仓"，并加 spec `test_paused_does_not_block_stop_loss`。改 scheduler 不触信任边界，仅影响 paper/demo。

4. **风控契约追平代码 + 消除 guard 混淆。**
   - 给 `risk_engine_contract.md` 表补 `6b/17/18/19` 四行，标题改 16→20 关。纯文档。
   - 加一句话定调：`trust/guard_chain` 与 `trading_decision` 守卫链都是**观测性 advisory，不参与执行裁决；唯一硬门是 `risk/engine.py:evaluate_proposal`**。可顺手加一个 `EX-DOC` spec claim 锁住"gateway 不导入 guard_chain"。

### 应该做（中杠杆）

5. **投资组合构建决策树**：`execution_contract §5` 补一节，声明两条 sizing 路径何时用哪条、Kelly cap 参数来源。不引入新算法。
6. **学习闭环诚实归类**：`ps_lessons` 双轨在 `knowledge_contract` 明确为设计选择（或补一条写回 FTS）；`ps_skills` 标 aspirational；`contradiction stub` 二选一。同步 `SYSTEM_MAP` 断裂表到当前状态。
7. **给监管环补"学习质量"信号**：在 `DreamingSummary` 接口加 `memo_accuracy_score` / `overturned_rule_count`，让 `recommendation_engine` 能在学习质量下滑时报警（轻量读路径，不改学习链）。这是把"管学了没有"升级到"管学得好不好"的最小动作。

### 暂不做（明确划线，防过度工程化）

- **不要**为 TS operator 大改、不要现在做真 DSL（`execution_path_honesty §1.3` 的死字符串）、不要现在实现真 contradiction engine——这些是 Round 2 之后 / 有数据后的事，现在做是过度工程。死字符串只需在 `execution_contract §9` 补一行"Round 2 前命名诚实化为唯一授权路径，B/C 路径需 owner 显式授权"。
- **不要**为补接缝而新建一堆契约文件——除《决策生命周期契约》外，全部复用现有契约补节。
- **OKX/Binance 实盘对账**进 backlog，不在当前 scope（实盘只 Bitget）。

---

## 5. 一个值得记住的元规律

**当一份契约需要"别混淆"警告框、或自带"代码实际是 X 而非 Y"的免责声明时，那不是瑕疵，是文档在替你标记"这里的对象边界划错了"。** 本次审计找到 ≥5 处这种自免责。不要消灭警告框——把它们当成"待修边界"的待办清单。本报告 §4 的前 4 条，本质就是在认领其中最贵的 4 处。

---

## 附录：参考模型与本系统的对应

| 模型 | 一句话 | 对应到 Aletheia |
|------|--------|----------------|
| OODA 环 | 飞行员决策循环 观察-定向-决策-行动 | 环 1 交易决策（分钟级） |
| MAPE-K | 自管理系统控制环 监测-分析-计划-执行+知识 | 环 2 系统监管（小时级，TS operator-shell） |
| 控制论：限幅器 vs 控制器 | 夹死边界 ≠ 连续调节 | 风控引擎=限幅器（墙）；drawdown_scale=控制器 |
| 事件溯源 / Trading-as-Git | 不可变事件流即真相 | 共享账本 + hash chain 审计链 |
| Kahneman 系统1/系统2 | 快而确定 vs 慢而深思 | 确定性风控(S1，无LLM) vs PI 深思(S2，LLM)；S1 护栏使 S2 敢探索 |
| 关注点分离 / 制度化交易台 | research/PM/execution/risk/中后台 各司其职 | 两环分离 + 风控独立否决 + 对账=中后台 |
| 机制 vs 策略分离（OS 设计） | 执行机制 ≠ 决策策略 | 风控=机制(enforce)，PI=策略(decide)；Command Registry 是这条线 |
| DIKW 金字塔 | 数据→信息→知识→智慧 | 采集=数据，快照/上下文=信息，lesson/guard=知识，进化策略=智慧；断点都是上行受阻 |
| 瑞士奶酪模型 | 多层防御，怕的是洞对齐 | 风控多层；查"对齐的洞"=单点失效 |
| Conway 定律 / 文档味道 | 结构会反映在产物上 | "别混三个组件"警告框 = 边界划错的味道 |
