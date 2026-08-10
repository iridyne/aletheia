# Agent 诊断与多源证据融合：CLQT、F²Agent 对 Aletheia 的方法论价值

> **角色：**外部论文研究证据，2026-08-10；不是当前产品事实、实现方案或施工授权。
> **研究对象：**[CLQT v2](https://arxiv.org/abs/2606.29771v2)、
> [F²Agent v1](https://arxiv.org/abs/2608.05668v1)。
> **辅助治理参考：**[AI Governance for Institutional Readiness in Finance v1](https://arxiv.org/abs/2608.02311v1)。
> **版本边界：**CLQT v1 提交于 2026-06-29，v2 修订于 2026-08-02；F²Agent
> 提交于 2026-08-06；治理论文提交于 2026-08-03。

## 1. 结论

两篇主论文都值得进入 Aletheia 方法论，但进入的层次不同。

- **CLQT 应作为 Agent 评估方法的高优先级参考。**最有价值的不是它的六 Agent
  结构、MCP 工具或三层记忆，而是“先诊断能力，再排名收益”：一次 Agent 更新必须说明改善了
  哪个过程能力，并把该能力与净经济结果、可靠性和风险纪律分别报告。
- **F²Agent 应作为多源证据组织和实验设计参考。**可借鉴的是“专用证据提取 → 显式融合
  → 对缺失/受扰模态做一致性测试”；当前不应照搬其训练式 adaptive fusion，也没有证据支持
  Aletheia 为此新增一组 Agent。
- **两篇论文都不证明 Aletheia 应改变交易权威边界。**研究结论不能绕过现有 Risk Engine、
  Execution Gateway、行为契约、policy epoch、成本结算或 owner admission。

它们对 Aletheia 的最大正向帮助，是把下一阶段研究问题从“收益有没有高一点”改成两个可证伪问题：

1. Agent 的分析、选择、配置、执行建议和复盘是否前后一致、稳定且可靠？
2. 某一类新增证据是否在缺失、噪声和行情变化下持续带来可归因的净改善？

## 2. CLQT：为什么价值最高

### 2.1 论文实际提出了什么

CLQT 把组合管理拆成 gather → synthesize → mandate-aware portfolio →
cost-aware execution → reflect。每轮生成一个 `DecisionRound`，把观察、信号、目标配置、订单、
成本、反思、记忆来源和模型调用可靠性封装起来，并通过可重新计算验证的 hash chain 串联。

它不把 Return 或 Sharpe 当成 Agent 能力本身，而从决策轨迹构造五个诊断轴：

| 轴 | 论文测量的核心问题 | 对 Aletheia 的可迁移解释 |
|---|---|---|
| Coherence | 配置是否由 Agent 自己陈述的分析与信号推出 | 理由、支持/反对证据、方向、仓位意图是否一致 |
| Acuity | 是否关注信息性信号而非显眼噪声 | 新增上下文究竟改变了相关判断，还是只增加注意力负担 |
| Composure | 波动或误导输入是否导致过度换手和策略漂移 | 相同政策下，Agent 是否因噪声产生不必要的方向/conviction 变化 |
| Discipline | 是否在硬裁剪前主动遵守 mandate | Proposal 到 Risk 前是否已自觉满足计划、风险和成本约束 |
| Reliability | 是否稳定完成有效 schema，避免 parse/truncation 失败 | 决策完成率、结构化输出失败、修复、延迟和调用成本 |

论文的关键实证发现支持这种拆分：名义 Sharpe 最高的配置只可靠完成了 26 轮中的 5 轮；
机械的信号—动作方向一致度又持续高于独立 judge 对整体分析—配置一致性的评价。这说明“方向看对”
不能推出“仓位配置与自身分析一致”，更不能推出可重复的 Agent 能力。

### 2.2 与 Aletheia 的现状对照

Aletheia 已经拥有 CLQT 所需的大部分底层事实，但尚未把它们组成统一的能力诊断：

| CLQT 原则 | Aletheia 已有基础 | 仍缺什么 |
|---|---|---|
| 点时、无前视证据 | closed-bar technical snapshot、输入 fingerprint、source/evidence manifest、freshness 语义 | 统一验证一次决定实际看见了哪些证据，而不是事后读取 latest row |
| 决策轨迹 | Opportunity → PiDecision → Proposal → RiskDecision → Order → Fill → Position → review 的耐久因果脊柱 | 面向研究的单轮只读投影；不需要再造一套交易账本 |
| 防篡改 | runtime audit JSONL 链与 Proposal 行级 hash chain | 不应复制 CLQT 的新链；只需让诊断投影引用现有 immutable IDs/hash，并可重算派生指标 |
| 成本后结果 | fee、spread/slippage、funding 和 `net_return_pct`；native Paper paired execution-cost evidence | 把过程能力与净经济结果并列，而不是互相替代 |
| 行为/实验身份 | behavior-contract hash、policy epoch、outcome/metric version、from-zero window | 明确每次 Agent/Prompt/上下文更新预期改善的能力轴 |
| 结构化理由 | Operator rationale、supporting/opposing signals、risk factors、entry-parameter reasons | 跨轮 strategy consistency 与 analysis → action → Risk clip 的聚合诊断 |
| 调用可靠性 | logical call/attempt/context manifest、结构化重试与 typed defer | 按行为版本聚合 completion/parse/repair/truncation/latency/cost；不可把重试成功隐藏成首轮成功 |

因此，CLQT 对 Aletheia 不是“新增 closed loop”的启发，而是**给现有闭环增加诚实的读法**。
Aletheia 的 Experiment Protocol 已明确 PnL alone 不是因果贡献证据；CLQT 补充的是 Agent
过程能力的可操作诊断词汇。

### 2.3 应借鉴与不应照搬

建议保留以下方法论：

1. **诊断先于排名。**任何 Agent、Prompt、上下文或模型版本比较，同时报告过程能力、可靠性、
   成本后结果和伤害指标；单期 Sharpe 不能授权升级。
2. **原始值优先于综合总分。**五轴可以作为问题分类器，但 Aletheia 不应直接复制 APM-CS
   百分位均值；它随 cohort 改变，且部分轴依赖作者自定义构造。
3. **把 reliability 当有效样本门，而非附注。**五轮成功、二十一轮失败的配置不能凭五轮收益胜出。
4. **区分方向正确与决策一致。**分别检查 evidence → thesis、thesis → direction、direction →
   allocation/size、计划 → Proposal、Proposal → Risk clip、执行 → net outcome。
5. **把模块消融读成能力变化。**收益落在运行噪声带内时，不能宣称模块有效；但可以检查它是否
   改变指定能力轴，再用独立、前瞻、成本后结果确认经济贡献。

暂不照搬六 Agent committee、三层记忆、19 个 MCP 工具、新的 `DecisionRound` 存储表或第二条
hash chain。Aletheia 已有单一 System Operator、受控工具/权限、因果链和 Learning stores；复制这些
机制会增加双重真相，且 CLQT 没有证明其整套架构适合 Aletheia。

### 2.4 证据边界

CLQT 仍是一篇 arXiv benchmark 论文，不是可直接采用的生产标准。其主要限制包括：单一且偏上涨的
回测行情（论文报告 SPY +27%）、回测成本主要为模型估计、live track 仅用于验证诊断一致性而非模型
收益迁移、部分维度在 structured/autonomous mode 间存在混杂，以及 Coherence 仍部分依赖 LLM
judge。五维框架适合做候选诊断本体，不适合未经本地构造效度验证就成为 Aletheia 的 promotion gate。

## 3. F²Agent：应借方法，不应搬模型

### 3.1 论文实际系统

F²Agent 用四个专用模块处理 OHLCV、技术指标、新闻推理和情绪，再将各模块 latent embedding
投入 modality-aware attention fusion。训练目标除方向分类外，还加入 modality prior 和一致性正则：
随机扰动一个模态后，要求预测 logits 保持接近，并禁止模型提高对受扰模态的注意力。

作者在 AAPL、AMZN、GOOG、MSFT、TSLA、BTCUSD 六个资产上，与 16 个 baseline 比较，
报告平均年化收益相对改善超过 20%，并补充更广资产和延长窗口实验。这些是**作者报告的历史回测
结果**，不是独立复现或 Aletheia 的预期收益。

### 3.2 对 Aletheia 真正有用的三点

第一，**先形成结构化 evidence packet，再融合**。Aletheia 已有三层 Operator context、技术快照、
新闻/宏观/衍生品来源和 evidence manifest，但最终仍由一个 System Operator 阅读异质材料。
研究上可以要求每个证据域先给出稳定字段，例如：

- source identity、as-of/freshness、availability 和 quality reason；
- direction、strength、horizon、instrument/market type；
- supporting facts、opposing facts、uncertainty 和 abstain reason；
- 与其他 evidence group 的相关/重复身份。

这不是要求新增 Agent。现有确定性计算器、collector normalizer 和当前受控 LLM producer 都可以输出
同一类证据对象；先统一可比较语义，比增加角色数量更重要。

第二，**把融合本身当成可观察步骤**。最终决定应能回答哪些 evidence packet 被采用、冲突如何处理、
哪个来源改变了方向/conviction/计划，以及某个来源缺失时系统是否 abstain。初期用显式规则或受控
schema 就足够，不需要 learned attention。

第三，**做模态缺失与扰动消融**。这是 F²Agent 最值得转化成 Aletheia 研究的问题：在冻结案例上，
分别遮蔽新闻、技术、宏观、衍生品或 Learning context，或注入明确的 stale/noisy 标记，检查决定的
稳定性、理由一致性和正确 abstention。只有某类证据在跨 case、跨 regime 的独立测试中稳定增加净信息，
才值得进入前瞻 Shadow；“prompt 更丰富”本身不构成价值。

### 3.3 为什么现在不应实现 learned fusion

论文主任务是日频二分类，并把相邻预测映射为 long-or-flat 操作；统一交易成本为 0.3%，不显式建模
slippage 或 market impact。主测试窗口只有 2025-04-01 至 2025-09-30；随机种子显著性主要在
AAPL/BTCUSD 三个 seed 上检验，作者也承认 BTCUSD 相对 Transformer 的 p=0.0535 不能按 5%
阈值称显著。其“Agent”输出还是 latent vector，和 Aletheia 中受审计的决策 Agent 不是同一抽象层。

引入 learned fusion 将新增训练数据、标签、GPU、embedding provenance、模型版本、校准、漂移和
回滚负担，同时改变决策输入与行为身份。没有本地消融证据前，这比结构化证据和显式融合更贵，也更难审计。

## 4. 辅助治理论文：保留原则，不抬高证据等级

《AI Governance for Institutional Readiness in Finance》提出 Policy / Engineering /
Composition / Systemic 四层治理。对 Aletheia 最可迁移的原则有三项：

1. 模型、Prompt、上下文、reward/目标或 Agent 组合更新属于 policy change，必须有版本身份和 attestation；
2. 单组件验证通过不代表组合后的完整管道安全，评估单位必须包括端到端决策链；
3. drift 和 crowding 应从实际 decisions/costs/exposures 监测，而不是只相信模型自述。

Aletheia 已通过 Proposal/Risk policy hash、behavior-contract hash、policy epoch、完整因果链、
Risk Engine、kill switch 和 owner admission 覆盖其中大部分原则。可补充的研究视角是：把 provider/
model/prompt/context 组合身份作为同一行为 attestation，并观察同源证据或同向策略暴露是否形成
composition/crowding 风险。

论文的定量说服力有限：88% 治理缺口来自 self-selected LinkedIn poll；regret-covariance 漂移检测和
双 Agent crowding 数字来自合成示例；作者也明确 taxonomy/ARS 只是 triage instrument，系统层模拟尚未
被充分实证。因此它适合作为治理检查表，不应直接导入其阈值、90 天路线或 crowding 概率。

## 5. 对 Aletheia 方法论的收敛建议

| 研究动作 | 判定 | 最小落点 | 进入生产前的证据门 |
|---|---|---|---|
| Agent 更新按能力轴 + reliability + cost-adjusted outcome 报告 | 现在吸收为方法论 | 扩展研究/实验报告模板；不改交易行为 | 本地指标定义和构造效度审查 |
| 从现有 IDs/hash 生成只读 decision trace | 值得后续研究 | 现有因果脊柱上的 projection，不建第二账本 | 可重算、缺失即 unavailable、与权威行一致 |
| analysis → action → Risk clip consistency | 高价值候选指标 | PiDecision/Proposal/RiskDecision 的只读派生 | 冻结案例人工复核 + 跨版本稳定性 |
| completion/parse/repair/truncation/latency/cost reliability | 高价值候选指标 | 现有 inference attempt/context 事实的聚合 | 不能把 retry 后成功冒充首轮成功 |
| 结构化 evidence packet | 值得研究 | 优先复用现有 producer/manifest，不新增 Agent | source/freshness/quality/abstention 完整 |
| 单模态遮蔽/扰动消融 | 值得离线优先 | frozen-case inference harness / Shadow | 跨 case、regime、模型重复，效果超过运行噪声 |
| learned adaptive fusion / consistency training | 暂缓 | 无 | 显式融合已证明不足，且有本地前瞻净贡献证据 |
| 新六 Agent committee、三层记忆或新 hash ledger | 不建议 | 无 | 只有现有边界出现已证实且无法局部解决的缺陷时重开 |

最终应坚持两条互相独立的证明链：**能力证明**回答 Agent 到底改善了什么；**经济证明**回答这种
改善在冻结身份、真实成本和前瞻结果下是否创造净价值。前者不能替代盈利，后者也不能掩盖不可靠、
不一致或依赖偶然行情的 Agent。


## 6. 参考文献

- Bo Qu, Mingguang Chen. [CLQT: A Closed-Loop, Cost-Aware, Strategy-Consistent Benchmark for Diagnostic Evaluation of LLM Portfolio-Management Agents, v2](https://arxiv.org/abs/2606.29771v2), 2026-08-02.
- Changshuo Liu et al. [F²Agent: Financial Fusion of Agentic Intelligence for Multimodal Trading, v1](https://arxiv.org/abs/2608.05668v1), 2026-08-06.
- Irene Aldridge, Steve Krawciw. [AI Governance for Institutional Readiness in Finance, v1](https://arxiv.org/abs/2608.02311v1), 2026-08-03.
- Aletheia current authority cross-checks: `docs/product/experiments/EXPERIMENT_PROTOCOL.md`,
  `docs/design/contracts/decision_lifecycle_contract.md`, `audit_chain_contract.md`,
  `opportunity_outcome_contract.md`, `learning_contract.md`, `technical_analysis_contract.md`,
  `agent_contracts.md`, and `docs/design/data/data_utilization.md`.
