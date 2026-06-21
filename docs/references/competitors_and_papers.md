# 竞品与参考论文（Competitors & Papers）

> 状态：参考索引（2026-06-21）。机器无关、长期有效的*意图/参考*，**非运行态事实，非施工授权**。
> 本文件补充 [INDEX.md](INDEX.md)：登记我们已克隆的参考实现**之外**的同领域竞品（开源项目）与参考论文。
> 所有 repo 地址 / arXiv 编号均已联网核实（2026-06-21）。star 数为核实时近似值，会漂移。
>
> **定位差异先说清**：本目录大多数竞品是**云端/多用户/重栈**的研究框架或平台；Aletheia 是**单用户本地优先 + change-controlled**。
> 所以下表的价值是"范式对照与选择性借鉴"，**不是**"我们该长成它们的样子"。重型栈类一律按 CLAUDE.md 红线处理。

---

## 1. 开源竞品 / 同领域项目

| 项目 | 仓库 | ⭐近似 | 一句话定位 | 对 Aletheia 的对照价值 | 是否建议克隆 |
|---|---|---|---|---|---|
| **TradingAgents** | TauricResearch/TradingAgents | 87.7k | 多 agent LLM 投研框架（分析师/交易员/风控 + 多空辩论） | 多角色 + **交易前辩论**结构化数据流（我们 §6 缺口） | ✅ 已克隆 |
| **Trading-R1** | TauricResearch/Trading-R1 | ~0.5k | RL 训练的交易模型（TradingAgents 同组新作） | "从交易反馈优化"的 RL 路线参考；arXiv:2509.11420 | 🟡 可克隆（小，关注 RL 训练范式） |
| **Microsoft Qlib** | microsoft/qlib | ~大 | AI 量化投资平台（监督/RL/市场动态建模） | 成熟量化基建对照；与 RD-Agent 集成 | 🟡 体量大、偏离 local-first，**先读不克隆** |
| **Microsoft RD-Agent** | microsoft/RD-Agent | 中 | LLM agent 自动化数据驱动 R&D；含 **RD-Agent(Q)** 自动化量化因子/模型协同优化 | **与我们 self-evolution 主题最相关**：agent 提想法(R)→实现(D)→反馈闭环；`fin_factor`/`fin_model`/`fin_quant` | ⭐ **最值得克隆**（与 [[aletheia-self-evolution]] 直接对照） |
| **FinRobot** | AI4Finance-Foundation/FinRobot | 7.3k | 开源金融分析 AI agent 平台（LLM） | agent 平台分层、工具编排对照 | 🟡 可克隆（agent 平台范式） |
| **FinGPT** | AI4Finance-Foundation/FinGPT | 20.6k | 开源金融 LLM（LoRA 微调/指令数据/情感模型，MIT） | 金融领域微调/情感分析参考（非 agent） | ⬜ 不必克隆（偏模型训练，离我们远） |
| **FinRL / FinRL-Meta / FinRL-X** | AI4Finance-Foundation/FinRL* | 15.5k / 1.9k / 3.3k | 金融强化学习框架 + 市场环境数据集 + AI-native 模块化量化基建 | RL 交易范式 + 市场环境抽象；远期"从反馈优化"的对照 | ⬜ 远期参考，暂不克隆 |

> 命名提醒：AI4Finance 是一个**生态组织**（FinRL/FinGPT/FinRobot 等同源），不是单一项目。

---

## 2. 参考论文（arXiv 编号已核实）

| 论文 | arXiv | 一句话 | 与 Aletheia 的关联 |
|---|---|---|---|
| **TradingAgents: Multi-Agents LLM Financial Trading Framework** | 2412.20138 | 仿交易公司的多 agent 框架，含多空辩论 + 风控团队 | 上表 TradingAgents 的论文；多角色 + 辩论范式 |
| **FinMem: Performance-Enhanced LLM Trading Agent with Layered Memory and Character Design** | 2311.13743 | 三模块（Profiling / 分层 Memory / Decision），可调"认知跨度" | **分层记忆**范式——对照我们 FTS5 单层记忆（[[north_star.md]] §4 经验闭环） |
| **A Multimodal Foundation Agent for Financial Trading (FinAgent)** | 2402.18485 | 工具增强 + 多模态（新闻/价格/K 线图）+ 双层反思 + 多样化记忆检索 | **双层反思 + 多样化记忆检索**范式对照；官方代码未见高星权威 repo（多为镜像/低星） |
| **Trading-R1** | 2509.11420 | RL 训练交易模型（TauricResearch） | 上表 Trading-R1 的论文 |
| **AlphaQuanter** | 2510.14264 | 工具编排式 agentic RL 交易框架（verl 栈） | 已克隆（[[INDEX.md]]）；透明工具调用 + 从反馈优化 |
| **Sophia**（持久数字生命体） | 2512.18202 | 叙事记忆/自我模型/长程适应 | 研究级远期（[[north_star.md]] §5） |
| **ALMA**（meta-learning agentic memory） | 2602.07755 | 记忆 schema 可进化、影响未来检索 | 研究级远期；与我们"固定 Pydantic schema"正相反 |

> 已收录的外部综述（非单一系统）：《Agent Framework Research Report 2025–2026》对照见 [agent_framework_research_2025_2026.md](agent_framework_research_2025_2026.md)。

---

## 3. 借鉴导航（接 INDEX §4，只列本文件新增的对照点）

| 关注点 | 来源 | 落位思路 | 红线 |
|---|---|---|---|
| **agent 提想法→实现→反馈闭环**（R&D 自动化） | RD-Agent(Q) | 对照我们 self-evolution Tier 2/3 设计（`docs/plans/self_evolution_plan.md`），看其"因子/模型协同优化"如何受控 | 🔴 agent 产出**不得**到达 change-controlled 路径；最多优化受限参数，绝不写可执行代码落 strategy/DB |
| **分层记忆 + 可调认知跨度** | FinMem | 对照我们 FTS5 单层 + recency×importance 加权；评估是否需"短/中/长期"分层 | 🟡 注入决策的记忆仍须 quality-gated、advisory，不改硬限 |
| **双层反思 + 多样化检索** | FinAgent | 对照 post_trade_reviewer / dreaming_agent 的反思深度 | 🟡 反思只产建议，不改风控 |
| **交易前多空辩论** | TradingAgents | 让 risk_critic 输出在 paper_trader 决策前被读到（state-driven 编排） | 🔴 辩论不得绕过 Risk Engine；**不引入 LangGraph** |

---

## 4. 维护约定

- 新增竞品：先在第 1 节登记（repo + star + 定位 + 是否克隆），克隆的归 `_refs/` 并同步到 [INDEX.md](INDEX.md) §1。
- 新增论文：第 2 节登记 arXiv 编号（**必须联网核实**，本目录历史上记错过编号），不下载 PDF（占空间、可随时取）。
- 重型栈/云端框架默认**只读不克隆**；只有与我们主题（经验闭环 / self-evolution / 信任硬化）直接对照的才克隆。
