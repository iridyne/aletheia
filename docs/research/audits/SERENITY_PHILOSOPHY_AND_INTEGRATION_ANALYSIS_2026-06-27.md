# Serenity 交易范式：哲学与系统整合深度分析报告

> 2026-06-27 | 9-agent 工作流深度审计（6 维 reader + synthesis + 2 红队对抗验证）
> 状态：**分析报告（待 owner 审阅）** —— 这是「report → 你审阅 → 生成 impl plan」流程的第一步，本文件不含任何代码改动。
> 范围：判断 Serenity 作为一个**完全独立的长期/产业链基本面交易流派**能否、以及如何在 Aletheia 中被完整体现。

---

## 0. 执行摘要（先读这一页）

**你的诉求**：Serenity 是一个区别于「价格行为学短线」和「消息面/情绪」的**第三种、完全独立的交易流派**——通过对现实世界产业链（工厂↔工厂、供给↔需求的第一性原理 + 新闻）做深度研究，判断**完整工业链上哪家公司被市场低估**，在确凿研究与复杂推理基础上形成**长期持仓的开仓 entry point**。你希望这个理念在系统里被**完整地、作为独立路径**实现。

**系统现状的一句话真相**：Serenity 今天**不是**一个交易流派，而是一个**研究工作台（Research Workbench）**。它的全部「交易影响力」= 把最多 5 条研究结论，作为**纯文本软建议**，注入 PI Agent prompt 的 Layer 3（"长期智慧"层）。`serenity_scoring.py` 算出的 0–100 分**只用于前端展示，从不参与任何交易决策**。点 "Promote" 只生成一个**永远不会被激活的死记录**（`StrategyRecord(state='draft')`）。

**README 下单流程图的答案**（你不确定是否存在的那张图）：存在。`README.md:73-75` 嵌入 `trading_flow_pi_sole_decision_maker.png`。但在全部 9 张流程图里，**没有任何一张画了 Serenity 的独立交易入口**——它只在 2 张图里作为 PI Agent 的 Layer-3 输入节点出现。所以你记忆中"流程图上能看到更多 Serenity"——图是有的，但 Serenity 在图里只是个旁支输入，不是独立路径。

**两个对抗红队挖出的、比"代码没写"更深的问题**（这是本报告最重要的部分，请务必看 §5 和 §7）：

1. **🔴 标的现实致命伤**：Bitget 上唯一适合长持的标的是 10 个 xStock 代币——但它们全是**美股大盘消费/科技股**（AAPL/TSLA/NVDA/MSFT/AMZN/GOOGL/META/NFLX/GOOG/SPY）。真正的产业链上游名字（ASML、LRCX、安森美、村田、SK海力士）**在 Bitget 根本买不到**。一个"半导体设备商相对 NVDA 被低估"的产业链论点，在这套基础设施上**只能表达成一个对 NVDA 的单边方向押注**——那不是产业链相对价值交易。**Serenity 的"工业链"内核与可用标的之间存在结构性错配。**

2. **🔴 证据质量致命伤**：研究链的"证据"是 LLM 凭参数知识生成的自由文本，`tier`（disclosed / high_confidence）由**同一个编造证据的 LLM 自评**。scoring 公式允许纯靠 LLM 自评字段就把分数顶到 96 分、进入 `candidate_position` 桶。结合本项目 Round 1 已证实的幻觉史（池子 1890 条机会:16 条真信号，学习闭环被确认喂幻觉），**一个自动盯 `candidate_position` 开仓的 executor = 在 LLM 虚构的"研究"上自动下单。**

3. **🔴 死记录根因**：promote 端点产出 `state='draft'`，而 strategy_orchestrator 只激活 `paper_running`/`paper_candidate`。**这是所有执行路径（包括最保守的 Option A）的共同根堵点**，必须第一个修。

**结论与建议路线**：理念本身极有价值，但"完全独立执行路径"在今天的基础设施上**有真实的可行性前提没满足**。诚实的推进顺序不是"直接开干独立路径"，而是：

- **先做 Option C（最小诚实化，数小时）**：修死记录、移除污染 Serenity 表的 ICT 方法论模式、把哲学正式写进 VISION.md/agent_contracts.md、写下 RED spec 声明锁定意图。
- **同时你需要先拍板 4 个 §8 的决策**（尤其是：10 个 xStock 能表达哪 1–3 个你真正在意的产业链论点？是否接受 LLM 证据质量?）。
- **再做 Option A（持仓机制诚实化，不碰信任边界、不加新 agent）**：用 `trade_school='serenity'` 区分符，给 Serenity 仓位长持语义（解除 72h、禁用 ATR 追踪、论点失效退出）。
- **Option B（真正独立的 serenity_executor 自动开仓）只能在治理决策落到 DECISIONS.md、且每个信任边界改动都先有 RED spec 之后**，再分 2a/2b/2c 三个子阶段推进。

下面是完整论证。

---

## 1. Serenity 是什么：哲学与三流派的第一性区分

为避免后续实现走偏，先把你口述的哲学**固化成系统可对照的 ground truth**（这一节本身就是 Option C 要写进 VISION.md 的内容，因为现在 VISION.md 对 Serenity **零提及**）。

**Serenity 的内核（你的原话提炼）**：
> 对现实世界产业链做深度研究——基于新闻 + 基础生产关系/供需第一性原理，研究工厂与工厂之间、供给与需求之间的关系——判断在完整工业链上**哪家公司被市场低估**，在确凿研究基础和复杂逻辑推理之下，形成一个**长期持仓**的开仓点。

**三流派对照表**（这是判断"是否独立"的核心）：

| 维度 | 价格行为学（短线，系统现状） | 消息面/情绪 | **Serenity（产业链基本面）** |
|---|---|---|---|
| 入场依据 | 技术形态、ATR、动量、订单流 | 单条新闻/情绪信号触发 | **跨公司供需链推理 + 低估判断** |
| 时间尺度 | 小时—数天（72h 上限硬编码） | 事件窗口 | **数周—数月** |
| 退出依据 | stop_price / take_profit / 追踪止损 / 72h 时钟 | 事件消化 | **论点失效（thesis invalidation）/ 价值回归** |
| 标的 | crypto perp / spot | 任意 | **被低估的具体公司（股票）** |
| 仓位 | ATR-based quarter-Kelly | —— | **基于信念与估值锚（dca_fair_value）** |
| 决策主体 | PI Agent（sole decision-maker） | PI Agent | **应由研究完整度门控，而非短线框架** |

**6 个架构性区别**（synthesis 提炼，决定了它"不能塞进现有短线管道"）：入场触发、退出触发、时间地平线、资产域、仓位模型、持仓哲学——**六项全部与现有短线 crypto 系统不兼容**。这就是为什么这是**架构问题，不是增量问题**。

---

## 2. README 下单流程图的答案（你的具体问题）

你说"不确定 README 下单流程图里有没有 Serenity 的内容"。审计结论（reader6，flow-docs 维度）：

- `README.md:73-75` **确实嵌入了**下单流程图 `trading_flow_pi_sole_decision_maker.png`。
- 仓库共 **9 张流程图**（`01`–`08` + `trading_flow_pi_sole_decision_maker`）。
- **没有任何一张画了 Serenity 的独立交易入口流。**
- Serenity 只在 **2 张图**里作为 PI Agent 的上下文输入出现：
  - `04_pi_agent_decision.mmd:22-23` 标注为 Layer 3 里的"研究洞察 (Serenity output)"
  - `trading_flow_pi_sole_decision_maker.mmd:68-69` 在 Layer 3 Wisdom 子图里提了一句 "serenity"

**所以**：图是有的，但 Serenity 在所有图里的角色都是 **PI Agent 的 Layer-3 旁支输入**，不是独立路径。你记忆中的"流程图上 Serenity 内容"= 这两个输入节点。**要让它成为独立流派，需要新画一张 Path C 流程图**（这是任何方案都要补的文档交付物）。

---

## 3. 当前实现真相：端到端数据流追踪

研究管道本身**已substantially建好**，但它和交易决策之间只有一根细线。

**研究侧（已建）**：
- `research_worker.py:212-416` 跑 5 阶段 LLM 链（evidence → claims → themes → opportunities → summary）。
- 落库到 **14 张专用表**（`serenity_*`，见 `data_model.md:124-141`）。
- `serenity_scoring.py:162-247` 确定性打分 0–100；桶：watch / research_priority(≥52) / candidate_position(≥72)（`:250-258`）。
- cron：每天 02:00 Asia/Shanghai 经 `_run_dreaming_agent` 触发（`scheduler.py:369-372,714-724`）。

**交易侧的唯一连线（薄弱点）**：
- `trading_context.py:524-538`：取最多 5 条 active serenity_opportunities → `{symbol, direction, thesis[:200], confidence_score, evidence_tier}`。
- `trading_decision.py:706-709`：渲染成一段文本 `## Research-sourced Opportunities (from Serenity agent)`，塞进 PI prompt 的 Layer 3。
- **`serenity_score` 从未被传进交易侧**（只在 `server/routes/data.py` 的读路径用，参与展示）。
- `market_observer` / `paper_trader` **完全看不到 Serenity**。

**断头路（misrepresentation）**：
- promote 端点（`routes/data.py:1656-1694`）→ 创建 `StrategyRecord(state='draft')` → 而 `orchestrator.py:89` 的激活过滤只认 `paper_running`/`paper_candidate` → **永久惰性，PG-S01 spec 已确认**。UI 上点"Promote"给用户"已推进"的错觉，实际什么都没发生。
- `_ICT_METHODOLOGY_SYSTEM`（`research_worker.py:105-120`，`mode=='methodology'` 时启用）收集 ICT/SMC **价格行为学**概念写进 serenity_* 表——**与 Serenity 哲学完全相反的污染物**。

**一句话**：Serenity 今天是"软建议 Layer-3 文本输入"，`serenity_score` 是"只读展示数字"，promote 是"假动作"。

---

## 4. 完整 Gap 表（阻止"完整体现"的 12 项）

synthesis 产出的 12 维 gap 表（7 个 blocker / 5 个 major）。这是"理念 vs 代码"的逐项差距：

| # | 维度 | 现状 | 差距严重度 | 关键触点 |
|---|---|---|---|---|
| 1 | 哲学文档 | VISION.md 零提及，agent_contracts.md 无独立路径 | 🔴 blocker | VISION.md / agent_contracts.md |
| 2 | 入场触发 | 仅 PI Agent 短线框架；无研究完整度门控 | 🔴 blocker | 新 serenity_executor / promote 端点 |
| 3 | 退出触发 | 72h 时钟 + ATR 追踪硬编码，无 trade_school 区分 | 🔴 blocker | position_manager._decide_exit:214-234 |
| 4 | 时间地平线 | max_position_hold_hours=72h（schema.py:388） | 🔴 blocker | schema.py / position_manager |
| 5 | 标的域 | xStock 硬编码 execution_eligible=False（equity_observer.py:409） | 🔴 blocker | symbols.py:116-127 / OBS-S04 spec |
| 6 | 决策主体治理 | PI Agent sole decision-maker（agent_contracts.md 受控文档） | 🔴 blocker | agent_contracts.md / DECISIONS.md |
| 7 | promote 落地 | state='draft' 永久惰性 | 🔴 blocker | routes/data.py:1656-1694 |
| 8 | scoring 用途 | 仅展示，从不参与决策 | 🟠 major | serenity_scoring.py |
| 9 | 仓位携带研究身份 | TradeProposal/Position 无 trade_school / serenity_opportunity_id | 🟠 major | models.py:109-130 / migrations.py:132-153 |
| 10 | 风控止损语义 | check #13 强制 numeric stop_price（engine.py:216-222） | 🟠 major | risk/engine.py（信任边界） |
| 11 | 仓位生命周期 | _STALE_DAYS=14 自动退役（orchestrator.py:93-110）；promotion gates min 30 trades/14 天 | 🟠 major | strategy_lifecycle.py:55-63 |
| 12 | 日损刹车 | max_daily_loss_pct=1.0% 系统级 kill switch（engine.py:159-171） | 🟠 major | risk/engine.py（信任边界） |

**7 项 embodiment requirements**（要"完整体现"必须填的洞）：① 文档化哲学；② 研究完整度入场门控；③ 论点失效退出；④ 长持时间语义；⑤ 真实可交易的低估标的；⑥ 仓位携带研究审计链；⑦ Serenity 专属生命周期门槛。

---

## 5. 标的现实：crypto-vs-低估公司 的结构性张力

这是整个方案**最容易被乐观掩盖、但红队判定为 blocking 的部分**。

**张力来源**：Serenity 要交易"被低估的公司（股票）"，但系统接的是 Bitget（crypto 交易所）。
- **crypto perp**：长持有 ~11%/年的资金费率拖累 → **被红队判定不适合 Serenity 长持，应排除**。
- **crypto spot**（BTC/ETH 现货）：synthesis 一度提议作为"产业链主题的代理长持"二级载体 → **红队判定应直接砍掉**：它和非-Serenity 现货共用 72h 上限，引入 trade_school 门控的持仓时长二分极易出错，且"BTC 现货代表某产业链论点"没有清晰的失效判据，是无界的范畴拉伸。
- **xStock ON 系列**（10 个代币化美股现货）：唯一与长持兼容的载体，但 `execution_eligible=False` 硬编码（OBS-S04 spec 用 AST walk 强锁）。

**红队挖出的两个致命可行性问题**：

**🔴 5.1 — 10 个 xStock 不是产业链，是 S&P 名人堂。**
可用名单：AAPL、TSLA、NVDA、MSFT、AMZN、GOOGL、META、NFLX、GOOG、SPY——**全是美股大盘科技/消费**。产业链上游真正的名字（ASML、LRCX、安森美、村田、Foxconn、SK海力士、尼得科）**一个都没有**。
- 后果：一个"哪家晶圆设备商相对 AI capex 周期被低估"的论点，**在这套系统上完全无法表达**。最多只能做成对 NVDA 的单腿方向押注——那是大盘股方向赌，不是产业链相对价值交易。
- **这不是"owner 接受一下"的约束，而是定义范围的前提**：你必须先回答"用这 10 个名字，我真正在意的产业链论点里有哪 1–3 个是可表达的？"（很可能只有 AI 基础设施 NVDA/MSFT、消费电子 AAPL 这几条有覆盖）。**如果答案是"我在意的论点一个都表达不了"，那 Option B 就是为一个在此基础设施上不存在的用例造的精良系统。**

**🔴 5.2 — xStock 流动性/跟踪保真度未验证。**
代码和文档里**没有任何证据**表明 NVDAON/USDT 等代币在多周持仓尺度上有足够的盘口深度、价差、交易连续性。equity_observer 只在单快照层面监控偏离（>2% alert / >5% anomaly），没回答：盘口深度够不够建仓？多周持仓会不会在美股收/开盘出现明显脱锚？Bitget 是否在财报期暂停 xStock 交易？
- 若 Bitget 在财报期暂停交易（代币化股票平台常见做法），基于论点的持仓会变成**被动锁仓，退出机制直接失效**。
- **建议**：动手前先人工审计 NVDAON/USDT、AAPLON/USDT 的 24h 量、价差、财报期是否停牌。若价差 >0.5% 或日量 <$50k，xStock 现货对任何有意义的仓位都不是可行载体，instrument_strategy 整节作废。

---

## 6. 三个架构选项（含信任边界 / scope 分析）

synthesis 给出 3 个选项，从最小到最大。

### Option C —— 最小诚实化：先修谎言，推迟架构（effort: S，数小时）
**做什么**：不建执行路径。修三个最严重的失真（死 promote 端点、误导性桶标签、污染的 methodology 模式），把哲学正式写进 VISION.md/agent_contracts.md，写 `spec_serenity_independence.py` 的 RED 声明（status='missing'），并写 `docs/archive/legacy/plans/trading-pipeline/serenity_execution_path.md` 作为 Option B 的实现指南。
- **信任影响**：无（不碰任何执行代码）。
- **优点**：零风险；移除最伤人的架构谎言；产出让 Option B 变成有界、spec-first 实现的规格与计划工件；数小时完成。
- **缺点**：Serenity 仍是观察台，理念完全没被体现——**这正是你明确不想停留的状态**。

### Option A —— 诚实桥接：Serenity 作为结构化一等上下文，无独立执行（effort: M）
**做什么**：不新增 agent、不让 Serenity 自动开仓。但把 Serenity 从"软文本"升级为**结构化一等上下文**喂给 PI Agent，并用 `trade_school='serenity'` 区分符给由此产生的仓位**正确的长持机制**（解除 72h、禁用 ATR 追踪、加论点失效退出、Serenity 专属生命周期）。入场决定**仍由 PI Agent 做**。
- **信任影响**：低。改动是增量的；现有风控检查保留；Serenity 专属 override 全部门控在 `trade_school='serenity'` 后面；OBS-S04 对 scanner 信号继续成立。需更新 `spec_serenity_execution.py` + 跑 `aletheia doctor`。
- **优点 / 缺点**：诚实、安全、不碰信任边界核心。**但红队的核心反对**：入场仍由一个同时权衡资金费率、7 日胜率、ATR regime 的短线框架 agent 做——**这只在"持仓机制"上忠于哲学，在"入场决策逻辑"上并不忠于**。

### Option B —— 独立 Serenity 路径：自有入场逻辑，共享执行网关（effort: L）
**做什么**：新增 `serenity_executor.py`（类似 paper_trader 但读 serenity_opportunities）。当某 SerenityOpportunity 进入 `candidate_position`（分≥72、零 fail_gate），executor 直接造 `TradeProposal(trade_school='serenity', ...)`，**绕过 PI Agent 做入场决定**，仍走共享风控引擎和执行网关（带 Serenity 专属 override）。PI Agent 保留短线路径的唯一决策权；新 Path C 并行运行。
- **优点**：哲学的独立性被真正忠实体现——入场由**研究完整度**而非价格行为框架决定；短线与长线路径解耦；xStock 现货执行变得有意义；审计链完整（仓位 → serenity_opportunity_id → theme → 证据链）。
- **缺点 / 信任影响（中）**：**必须放松"PI Agent 是唯一决策者"约束**（改受控文档 agent_contracts.md，是治理变更不是代码变更）；新增 agent + 风控分支 + 仓位生命周期路径，改动面明显比 A 大；证据质量缺口仍在——**在 LLM 幻觉证据基础上自动 paper 开仓 = 审计完善的虚构**。

**synthesis 的推荐**：Option B，分两阶段（先 C 后 B core）。
**但两个红队都不同意"直接奔 B"**——见 §7。

---

## 7. 对抗验证：两个红队视角的 blocking issues

我用两个独立 Opus 视角对上面的方案做了对抗审查。它们的判决**收敛到同一个结论：synthesis 诊断准确，但处方过早**。

### 7.1 信任边界 & scope 纪律视角

**3 个 blocking issues**：
1. **B 的 serenity_executor 在无 PI 批准下自动造 paper TradeProposal**——直接打破 agent_contracts.md 的"PI 唯一决策者"不变式。这是改受控文档，**没有 owner 书面签字（DECISIONS.md）不能动**。即便签了，新路径必须门控在 `serenity_execution_enabled: false`（默认关）后面，且先有 spec 覆盖。
2. **改 risk/engine.py 的 check #13（强制 stop_price）和日损 kill-switch 加 Serenity 豁免**——这是信任边界文件，必须 SDD：先写 RED spec 声明、owner 批准。今天两者都不存在。
3. **synthesis 称"prediction_observer 已经在写 SerenityEvidence 行"——未经代码核实**。若为假，"hybrid 证据门"静默失效，所有 Serenity paper 单都建在未核实的 LLM 幻觉上。

**其它高危点（节选）**：
- **别碰 OBS-S04**：serenity_executor 应读 serenity_opportunities（独立表），不经 equity_observer 的 Opportunity 写路径，因此根本不需要碰 OBS-S04；改它反而开审计洞。
- **拒绝 kill-switch 豁免**：豁免某类仓位会造永久审计缺口（失控 Serenity 仓位可绕过安全栏）。正解是给 Serenity 仓位配结构性止损（dca_fair_value_low），让单仓最坏日损本身就不击穿系统级 1% 限。
- **check #13 的最小绕法**：让 executor/promote 端点在提交前自己推导并填 `stop_price = dca_fair_value_low*(1-pct)`，**保持 check #13 不变，把推导逻辑放在信任边界外**。
- **Config↔UI 硬门**：4 个新 config 字段（serenity_max_hold_days 等）必须**同一个 PR 配齐 Settings 控件**，建议归到单一 "Serenity" 设置区。
- **promote 不应直接造 TradeProposal**（绕过研究完整度门）；应转 SerenityOpportunity `action_intent='awaiting_execution'`，由 executor 轮询，把研究门保留为代码不变式而非 UI 约定。
- **Phase 2 不是"smallest change"**：必须拆成 2a（仅 schema/model + migration + RED spec，零执行逻辑）→ 2b（仅 position_manager 持仓机制 override，不在信任边界内）→ 2c（serenity_executor + 风控 override + scheduler slot，信任边界邻接，每步后跑 doctor）。**2c 永不在 2a/2b GREEN 前上线。**

### 7.2 可行性 & 标的现实视角

**5 个 blocking issues**（§5 已展开前两个，这里补全）：
1. **无可执行的产业链论点**（§5.1）：owner 必须先做范围决策。
2. **xStock 流动性未验证**（§5.2）：动手前先实测。
3. **自动 executor + LLM 幻觉未被拦**：scoring 允许纯 LLM 自评字段顶到 96 分进 candidate_position；结合 Round 1 幻觉史（1890:16），自动开仓**epistemically 不安全**。**必须先定义一个强制人工复核门**（operator 确认的 SerenityRiskGate）才能谈自动 paper 入场。
4. **promote 的 state='draft' 死记录是所有路径（含 Option A）的根堵点**——必须作为**第一个代码改动**修成 `state='paper_running'`，否则任何 Serenity 仓位要么无 strategy_id（撞 risk check #14），要么挂惰性 strategy_id，PnL 归因/promotion 计数全断。
5. **论点失效退出路径全代码库不存在**（确认：position_manager 无 thesis_invalidat / serenity_opportunity_id 引用，Position model 无 serenity FK）。72h 时钟在 `_decide_exit()` 硬编码无 trade_school 区分。**不解决这个，任何方案都不能号称"Serenity-忠实"的持仓。**

**额外的证据质量警告（已亲核代码，比红队推测更确凿）**：synthesis 想用 prediction_observer 行充当"非 LLM 证据"。我亲自核实了 `prediction_observer.py:388-431`，确认这条写入路径**真实存在**——但其质量恰好坐实了红队的担忧，且更糟：
- `source="prediction_market"`（`:416`）—— 所以一个**朴素的** hybrid 门（"至少一条 source != 'llm'"）会被**任意一条 prediction 行轻易满足**，等于把幻觉门静默降级为"LLM 证据可接受"。
- `tier` 只取 `"medium"` 或 `"speculative"`（`:424`），**永远到不了 `disclosed`/`high_confidence`**。所以若把门收紧成"tier ∈ (disclosed, high_confidence)"，prediction 行又**一条都满足不了**——门会变成永远 RED，自动入场永不可能。两头都不对。
- 匹配逻辑（`:402-408`）：按 **crypto/macro 关键词**（bitcoin/btc/eth/fed/cpi/fomc… 关键词集 `:390-392` **没有一个是产业链/工业词**）在预测市场问题与主题标题间求交集；**且当无主题匹配时直接 fallback 到 `themes[0]`（第一个 active 主题，完全不相关也照挂）**。
- 后果：一个"NVDA 半导体供需链被低估"的主题，会被挂上一条"Fed 加息"或"比特币"的预测市场情绪行。**这是把 crypto 宏观情绪数据洗成产业链证据**，是 reader 直接证实的代码行为，不是推测。

**结论**：要真正的产业链证据门，得要求 source ∈ (earnings_filing / supplier_report / channel_check) 或人工复核标记——**这些在当前代码库都不存在**。现有的唯一"非 LLM 证据源"（prediction_market）与 Serenity 的产业链内核无关。这把 D2 的选择压缩成二选一：**要么接受纯 LLM 证据质量（仅 paper、风险有界），要么把自动入场推迟到接入真实产业链证据源（超出当前 MVP scope）**——没有"用现成 prediction 行凑一个 hybrid 门"的中间捷径。

**两个红队的共同收敛结论**：
> 先 ship Option C（数小时），再做 Option A 持仓机制（无信任边界触碰、无新 agent），**Option B 的 serenity_executor 只能在治理决议落地 + 前序阶段 spec GREEN 之后**。且 Phase 2（serenity_executor）应在**至少一个手动 promote 的 paper 仓位被持有并平仓、证明持仓机制可用之后**才启动。

---

## 8. 需要 Owner 决策的问题（带推荐）

这些问题**必须你先拍板**，否则 impl plan 无法定型。前 3 个是 synthesis 提的；后 2 个是红队补的、且优先级更高。

**D1（红队补，最高优先）—— 产业链范围**：用这 10 个 xStock（全是美股大盘科技消费），你**真正在意的产业链论点里，哪 1–3 个是可表达的**？
- 选项：A) 列出可表达的具体论点（如 AI 基础设施 NVDA/MSFT、消费电子 AAPL），把它们定义为 Serenity 的实际操作域；B) 接受"产业链"= "一次 LLM call 能联想到这 10 个名字的任何东西"（语义稀释）；C) 承认我在意的论点都表达不了 → 重新考虑是否值得建独立执行路径。
- **若选 C，应停在 Option A/C，不建 Option B。**

**D2（红队补）—— 证据质量标准**：是否接受 LLM 参数知识（可能过时数月、自评 tier）作为 Serenity paper 单的依据？
- 推荐：**Hybrid，但门要真**。早期阶段（research/demand_wave）允许 LLM 当假设生成器；但 gate 实际开仓的 supply_chain 及以上阶段，要求至少一条 source ∉ ('llm','prediction_market') 且经人工复核的证据。**已亲核代码确认（见 §7.2）：当前唯一的"非 LLM 证据源"= prediction_observer 写的 prediction_market 行，但它是 crypto/macro 情绪、tier ≤ medium、按 crypto 关键词匹配 + `themes[0]` 任意 fallback——与产业链无关，不能充当真证据。** 所以现实只有二选一：要么接受纯 LLM 质量（仅 paper，风险有界），要么把自动入场推迟到接入真证据源（earnings_filing/supplier_report/channel_check，当前代码库都不存在 → 超出 MVP scope）。**没有"用现成 prediction 行凑 hybrid 门"的中间捷径。这一条决定了 Serenity 能否自动开仓。**

**D3 —— PI 唯一决策者约束可否为 Serenity 放松**？（允许独立 serenity_executor 不经 PI 批准发 paper 提案）
- 推荐：**Yes，但作为治理澄清写进 DECISIONS.md**。约束本意是防短线路径多 agent 发冲突单；Serenity 不同时间尺度+资产类，paper-only executor 不与 PI 的短线 crypto 决策冲突。改成："PI 是短线 crypto perp/spot 的唯一决策者；serenity_executor 是长线 xStock 信念仓的唯一决策者"。**这是 Option B 的前置闸门——不签则只能走 Option A。**

**D4 —— Serenity 永久 paper-only，还是最终上 live xStock**？
- 推荐：**Paper first + 显式 live 晋升门**（如 ≥2 笔已平 paper Serenity 仓、持仓≥30 天、PnL>0 才解锁 live）。匹配现有 paper→live 模式，避免对证据质量不确定的研究管道过早暴露真金。

**D5 —— 自动开仓 vs 手动 promote**？（红队强烈建议）
- 推荐：**初期一律手动 operator-confirmed 入场**（强制 SerenityRiskGate manual_review），不自动盯 candidate_position 开仓。先用手动证明持仓机制和论点质量，再考虑自动化。这是对 Round 1 幻觉史最直接的防御。

---

## 9. 推荐路线与下一步

**诚实的分层推进路线**（每层独立可验证、可叫停）：

```
第 0 步（你来）：拍板 §8 的 D1–D5（尤其 D1 产业链范围 + D2 证据标准）
         ＋ 人工实测 NVDAON/AAPLON 流动性（§5.2）
              │
              ▼
Option C（数小时，零信任边界）：
  · 修 promote 死记录 state='draft' → 'paper_running'（根堵点，§7.2-4）
  · 移除/隔离污染的 _ICT_METHODOLOGY_SYSTEM 模式
  · 哲学写进 VISION.md / agent_contracts.md（§1 的内容）
  · 新画 Path C 流程图
  · 写 spec_serenity_independence.py RED 声明 + docs/archive/legacy/plans/trading-pipeline/serenity_execution_path.md
              │
              ▼
Option A（effort M，不碰信任边界、不加 agent）：
  · TradeProposal/Position 加 trade_school + serenity_opportunity_id（migration + data_model.md）
  · position_manager：trade_school='serenity' 解除 72h、禁 ATR 追踪、加论点失效退出
  · Serenity 专属生命周期门槛
  · 入场仍由 PI 做（诚实，安全）
              │
              ▼  （仅当 D3 治理决议落到 DECISIONS.md 后）
Option B（effort L，分 2a/2b/2c，每步 spec-first + doctor）：
  · 2a 仅 schema/model + RED spec
  · 2b 仅 position_manager 持仓机制 override
  · 2c serenity_executor + 风控 override（信任边界邻接）
  · executor 硬编码 execution_mode='paper_internal'，强制人工复核门
  · stop_price 在执行器侧推导（保 check #13 不变），拒绝 kill-switch 豁免
```

**下一步**：本报告就是你要的"完整分析研究报告"。**请你审阅，特别是 §8 的 5 个决策**。等你拍板（至少 D1/D2/D3）后，我再依据你的选择**生成对应的 impl plan**（spec-first，按上面分层）。

**在你审阅并给出方向之前，我不会写任何代码、不动任何文件（本报告除外）。**

---

### 附：本报告的证据基础（9-agent 工作流，729k tokens，零 rate-limit 失败）
- 6 维 reader：philosophy-intent / pipeline-reality / trading-integration / instrument-universe / risk-lifecycle / flow-docs
- 1 synthesis（lead architect）+ 2 对抗 verify（trust-boundary&scope / feasibility&instrument-reality）
- 所有 file:line 引用均来自 reader 对当前 worktree 代码的直接 grep/read，未经二手转述。
