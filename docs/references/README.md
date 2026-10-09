
# references/ - External Reference Material

Use this directory for external systems, papers, captured pages, and benchmark comparisons. References are not authorization to implement.

## Subfolders

| Folder | Use |
|---|---|
| `external-systems/` | Comparative systems study, OpenAlice/prism review, prism extraction |
| `papers-and-benchmarks/` | Papers, competitors, benchmark surveys |
| `coriva.eu.org/articles/` | Markdown captures from Coriva reference pages |
| `coriva.eu.org/captured-html/` | Raw captured HTML paired with article markdown |

## Agent Rule

References are not authorization to implement. To borrow from one, open a GitHub
Issue; write a `design/proposals/` document only when the change meets the
[proposal threshold](../governance/policies/documentation_governance.md#proposal-threshold-and-lifecycle).
Check governance boundaries first.

## System positioning (formerly INDEX §2)

| 系统 | 一句话定位 | 对我们的贡献 | 深拆文档 |
|---|---|---|---|
| **OpenAlice** | 本地优先的通用 AI 交易 agent（"one-person Wall Street"） | 交易侧范式答案：local-first / Trading-as-Git / 凭证隔离 / guard pipeline | [openalice_prism_review.md](external-systems/openalice_prism_review.md) · [comparative_systems.md](external-systems/comparative_systems.md) §3 |
| **prism** | 多 agent + async task 的中国公募基金研究 MVP | **反面教材**：LLM multi-agent 是死代码，后台实跑纯规则算术（与 Aletheia 同病）；另含可内联的工程片段 | [openalice_prism_review.md](external-systems/openalice_prism_review.md) · [prism_extraction.md](external-systems/prism_extraction.md) |
| **TradingAgents** | 多 agent 投研框架（分析师/交易员/风控 + 多空辩论） | 多角色 + 反方 + **交易前辩论**的结构化数据流 | [comparative_systems.md](external-systems/comparative_systems.md) §5 |
| **nautilus-practice** | 用户自有，基于 NautilusTrader 的三模式（回测/沙盒/实盘）+ LangGraph 多 agent 策略生成 | 工程侧：成熟撮合内核 + 三模式同心圆 + 资金费率熔断 + agent **造策略**而非**做决策** | [comparative_systems.md](external-systems/comparative_systems.md) §10 |
| **hermes-agent** | 自我改进的常驻 agent（skill from experience + 持久记忆 + 三层 prompt） | 自学习侧范式答案：FTS5 可检索记忆 + skill 注入 + 记忆反哺决策 | [comparative_systems.md](external-systems/comparative_systems.md) §4 |
| **AlphaQuanter** | 工具编排式 agentic RL 交易框架（verl 强化学习栈） | 透明工具调用 + 从交易反馈优化策略（经验闭环的 RL 版，远期） | [comparative_systems.md](external-systems/comparative_systems.md) §5 |
| **Sophia** | 持久数字生命体（叙事记忆/自我模型/长程适应） | 长程身份 + dreaming/consolidation + 自我解释（研究级远期） | [comparative_systems.md](external-systems/comparative_systems.md) §5 |
| **ALMA** | 让 agent 学习更好的记忆设计（meta-learning） | 记忆不是死库；schema 可进化、影响未来检索（研究级远期） | [comparative_systems.md](external-systems/comparative_systems.md) §5 |

> 另有一份外部综述的对照分析（非系统、是报告）：[agent_framework_research_2025_2026.md](papers-and-benchmarks/agent_framework_research_2025_2026.md)
> —— 结论：那份报告是验证镜而非新需求源，其推荐的重型栈（Postgres/向量库/Redis/LangGraph）恰好命中我们写死的红线，已显式拒绝。

## Never borrow (formerly INDEX §4 红线)
- **agent 自改代码权**（OpenAlice Evolution mode）。
- **agent 生成可执行 Python 策略代码落 DB/strategy 目录**（nautilus-practice LangGraph 那条线）——即使有 validator 评分。最多让 agent 优化**参数**（受限数值搜索空间），绝不写代码。
- **agent 自生成可解锁 live / 改风控 / 改凭证的 skill**（hermes "closed loop" 的激进解读）；最多做"人审后生效的草案"。
- **market-state 检查（如资金费率熔断）进 `risk/engine.py`**——增加外部数据依赖会削弱可审计性；正确落位是 playbooks 层。
- **重型栈**：Postgres / 向量库 / Redis / NATS / LangGraph（root `AGENTS.md` / governance）。

> 2026-10-09: `INDEX.md` was folded into this README. Its local-clone inventory
> (machine-specific paths) and P0/P1 borrow-status columns were dropped as stale
> runtime/progress state; the full former text is in Git history.

## Current paper analyses

- [CLQT and F²Agent methodology analysis (2026-08-10)](papers-and-benchmarks/agent-diagnostics-and-evidence-fusion-2026-08-10.md)
  separates reusable Agent diagnostics and evidence-fusion experiments from
  unproven architecture or implementation claims.
