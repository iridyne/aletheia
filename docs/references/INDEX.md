# 参考实现总览（INDEX）

> 状态：参考索引（2026-06-21 建立 / 维护）。机器无关、长期有效的*意图/参考*，**非运行态事实，非施工授权**。
> 本文件是 `docs/references/` 的入口：一眼看清"我们参考了哪些系统、本地克隆在哪、对应哪份深拆、能借什么、红线是什么"。
>
> **治理纪律（贯穿全目录）**：参考 ≠ 授权。每个借鉴项动工前需单独显式授权 + explore→plan + 焦点回归。
> 触及 `risk/`、`execution/`、`config/secrets.py` 等 change-controlled 边界的借鉴，按 CLAUDE.md 红线处理。

---

## 0. 一句话结论

> 交易侧的"形"（OpenAlice 那一类：local-first / Trading-as-Git / 多角色 agent / guard pipeline）**我们已做齐且往往更硬**。
> 真正缺的、且 Hermes / ALMA / AlphaQuanter / TradingAgents 共同指向的是**同一件事——经验闭环**：写进记忆的东西要能反哺下一次决策。
> 当前已推进到"软闭环"（FTS5 检索注入 LLM prompt），剩余差距是**参数化反馈**与**跨 agent 协作流**。详见 [north_star.md](external-systems/north_star.md)。

---

## 1. 本地克隆清单

全部克隆在仓库**之外**：`C:\Users\ollama\Projects\_refs\`（不污染 git，文档价值不依赖它存在）。
克隆方式：除 OpenAlice/prism（完整历史，早期克隆）外，其余为 `--depth 1` 浅克隆。

| 项目 | 本地目录 | 体量 | 仓库地址 | HEAD（克隆时） |
|---|---|---|---|---|
| **OpenAlice** | `_refs/OpenAlice` | 17M | github.com/TraderAlice/OpenAlice（fork: iridite/OpenAlice） | `f4f6b17` |
| **prism** | `_refs/prism` | 1.4M | github.com/iridyne/prism（**计划删库**，本地是唯一留存） | `c80ce83` |
| **TradingAgents** | `_refs/TradingAgents` | 9.2M | github.com/TauricResearch/TradingAgents | `c15200d` |
| **nautilus-practice** | `_refs/nautilus-practice` | 4.4M | github.com/iridite/nautilus-practice（用户自有） | `649d889` |
| **AlphaQuanter** | `_refs/AlphaQuanter` | 6.9M | github.com/AlphaQuanter/AlphaQuanter（arXiv:2510.14264 配套） | `e909ed4` |
| **hermes-agent** | `_refs/hermes-agent` | 175M | github.com/NousResearch/hermes-agent（NousResearch 官方） | `5aec00f` |
| **RD-Agent** | `_refs/RD-Agent` | 21M | github.com/microsoft/RD-Agent（含 RD-Agent(Q) 量化研发闭环，与 self-evolution 对照） | 浅克隆 |

> **未克隆（无公开代码，仅论文/营销页，参考价值停留在范式层）**：
> - **Sophia**（arXiv:2512.18202）— 持久数字生命体研究框架，研究级远期。
> - **ALMA**（arXiv:2602.07755）— meta-learning agentic memory，研究级远期。
> - 《Agent Framework Research Report 2025–2026》里的 **OpenClaw / Lobster / Hermes**（报告用 "-like"/"-style" 措辞，多为范式原型而非可核实开源产品；其 "Hermes" 与上表 hermes-agent 同源）。

---

## 2. 各系统定位 + 对应深拆文档

| 系统 | 一句话定位 | 对我们的贡献 | 深拆文档 |
|---|---|---|---|
| **OpenAlice** | 本地优先的通用 AI 交易 agent（"one-person Wall Street"） | 交易侧范式答案：local-first / Trading-as-Git / 凭证隔离 / guard pipeline | [openalice_prism_review.md](external-systems/openalice_prism_review.md) · [north_star.md](external-systems/north_star.md) §3 |
| **prism** | 多 agent + async task 的中国公募基金研究 MVP | **反面教材**：LLM multi-agent 是死代码，后台实跑纯规则算术（与 Aletheia 同病）；另含可内联的工程片段 | [openalice_prism_review.md](external-systems/openalice_prism_review.md) · [prism_extraction.md](external-systems/prism_extraction.md) |
| **TradingAgents** | 多 agent 投研框架（分析师/交易员/风控 + 多空辩论） | 多角色 + 反方 + **交易前辩论**的结构化数据流 | [north_star.md](external-systems/north_star.md) §5 |
| **nautilus-practice** | 用户自有，基于 NautilusTrader 的三模式（回测/沙盒/实盘）+ LangGraph 多 agent 策略生成 | 工程侧：成熟撮合内核 + 三模式同心圆 + 资金费率熔断 + agent **造策略**而非**做决策** | [north_star.md](external-systems/north_star.md) §10 |
| **hermes-agent** | 自我改进的常驻 agent（skill from experience + 持久记忆 + 三层 prompt） | 自学习侧范式答案：FTS5 可检索记忆 + skill 注入 + 记忆反哺决策 | [north_star.md](external-systems/north_star.md) §4 |
| **AlphaQuanter** | 工具编排式 agentic RL 交易框架（verl 强化学习栈） | 透明工具调用 + 从交易反馈优化策略（经验闭环的 RL 版，远期） | [north_star.md](external-systems/north_star.md) §5 |
| **Sophia** | 持久数字生命体（叙事记忆/自我模型/长程适应） | 长程身份 + dreaming/consolidation + 自我解释（研究级远期） | [north_star.md](external-systems/north_star.md) §5 |
| **ALMA** | 让 agent 学习更好的记忆设计（meta-learning） | 记忆不是死库；schema 可进化、影响未来检索（研究级远期） | [north_star.md](external-systems/north_star.md) §5 |

> 另有一份外部综述的对照分析（非系统、是报告）：[agent_framework_research_2025_2026.md](papers-and-benchmarks/agent_framework_research_2025_2026.md)
> —— 结论：那份报告是验证镜而非新需求源，其推荐的重型栈（Postgres/向量库/Redis/LangGraph）恰好命中我们写死的红线，已显式拒绝。

---

## 3. `docs/references/` 文件地图

| 文件 | 性质 | 一句话 |
|---|---|---|
| [INDEX.md](INDEX.md) | 入口 | 本文件——参考实现总览 |
| [north_star.md](external-systems/north_star.md) | 方向地图 | 六系统全景 + 范式 vs 现状映射 + 排序（含 §10 nautilus-practice 深拆） |
| [openalice_prism_review.md](external-systems/openalice_prism_review.md) | 深拆（2026-06-10） | OpenAlice（升级对照）vs prism（反面教材）逐项对照 + §5 施工蓝图；P0 borrow 状态见 ../research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md |
| [../research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md](../research/agents-learning/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md) | 深拆（2026-06-29，升级版） | 博客 + 真实 TS 源码 + Aletheia 对照；已采纳结论进入当前 data/audit/trust 契约，历史取舍见 [`TAG_BORROW_BACKLOG`](../archive/legacy/issues/backlog/TAG_BORROW_BACKLOG.md) |
| [prism_extraction.md](external-systems/prism_extraction.md) | 代码清仓 | prism 删库前的工程片段留存（ProgressHub / TaskStore / WS endpoint 等） |
| [agent_framework_research_2025_2026.md](papers-and-benchmarks/agent_framework_research_2025_2026.md) | 验证镜 | 外部综述报告对照——确认而非启发，显式拒绝其重型栈 |
| [competitors_and_papers.md](papers-and-benchmarks/competitors_and_papers.md) | 竞品+论文 | 已克隆之外的同领域竞品（TradingAgents/RD-Agent/Qlib/FinRL…）与参考论文（arXiv 编号已核实） |
| [FRAMEWORK_MODULE_MAP.md](external-systems/FRAMEWORK_MODULE_MAP.md) | 自身架构 | Aletheia 12 模块地图（非外部参考，但与本目录互参） |

---

## 4. 最高价值借鉴清单（按性价比 + 红线排序）

> 完整 file:line 蓝图见各深拆文档；此处只做导航。

| 优先级 | 借鉴点 | 来源 | 落位 | 红线 |
|---|---|---|---|---|
| **不采纳** | Guard 责任链 + 注册表（拆 `_evaluate_proposal_locked` 巨函数） | OpenAlice `guards/` | 纯结构重排不改善产品能力或 Alpha 证据链，当前不进入 roadmap | 如未来因真实维护故障必须重构，仍需保持 risk 行为与 violation token 不变；历史评估见 [`TAG_BORROW_BACKLOG`](../archive/legacy/issues/backlog/TAG_BORROW_BACKLOG.md) |
| **P0** | proposal 引入 `commit_hash` + `state_after` 快照 + reject 作一等审计记录 | OpenAlice `TradingGit` | 现有 SQLite 列 + JSONL，不引新库 | ✅ `commit_hash` 链已落地（P0-1 `b8ed990` + P1-2 `31eb1a9`，2026-06-29，不触 trust）；`state_after` 快照 + reject 同构待 owner 批准（触 `execution/gateway.py`） |
| **P1** | 决策 prompt 注入相关教训/复盘（已部分实现，扩展到更多 agent） | hermes-agent 三层 prompt | `market_observer`/`strategy_designer` 已通，扩展面 | 先证学习质量；注入"建议"非"指令"，不得改硬限/解锁 live |
| **P1** | 金额统一 `Decimal` 不落 float | OpenAlice | notional/PnL 边界 | —— |
| **P1** | risk_critic 改"统计型"（过滤器命中率/拒绝原因 top-5） | nautilus-practice 过滤器诊断 | `risk_critic` 输出形态 | 只读统计，不改控制 |
| **P2** | agent state-driven 编排（market_observer→strategy_designer→risk_critic 工作流） | TradingAgents / nautilus-practice LangGraph | `orchestrator.py` 加 ~100 行轻量状态机 | **不引入 LangGraph**；辩论不得绕过 Risk Engine |
| **P2** | 长任务 WebSocket 进度推送 | prism | `TaskStore` 必须落 SQLite | prism 是纯内存重启即丢，别照抄 |

### 🔴 绝不借鉴（红线）
- **agent 自改代码权**（OpenAlice Evolution mode）。
- **agent 生成可执行 Python 策略代码落 DB/strategy 目录**（nautilus-practice LangGraph 那条线）——即使有 validator 评分。最多让 agent 优化**参数**（受限数值搜索空间），绝不写代码。
- **agent 自生成可解锁 live / 改风控 / 改凭证的 skill**（hermes "closed loop" 的激进解读）；最多做"人审后生效的草案"。
- **market-state 检查（如资金费率熔断）进 `risk/engine.py`**——增加外部数据依赖会削弱可审计性；正确落位是 playbooks 层。
- **重型栈**：Postgres / 向量库 / Redis / NATS / LangGraph（CLAUDE.md 写死）。

---

## 5. 维护约定

- 新增参考系统：克隆到 `_refs/`（浅克隆优先）→ 在本 INDEX 第 1、2 节登记 → 视价值决定是否写独立深拆。
- 论文类（无公开代码）：登记在第 1 节"未克隆"块，不强行找代码。
- 本目录只承载**机器无关的意图/参考**；当前 bug/contract 偏移进入 GitHub Issues，历史证据进入 `docs/archive/legacy/`，不在 references 中维护状态台账。
