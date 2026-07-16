# Aletheia Trading OS — Product Requirements Document

> Last updated: 2026-07-12
> Status: Living document — authoritative source for "what we're building and why"
> 顶层愿景（赌什么 / 为谁 / 什么叫赢）见 **[VISION.md](VISION.md)**；本文件承接它，定义范围与边界。
> Companion docs: [VISION.md](VISION.md) · [DIRECTION.md](DIRECTION.md) · [METRICS.md](../gates/METRICS.md) · [PHASE_GATES.md](../gates/PHASE_GATES.md)

---

## 1. 产品定位

**一句话**：Aletheia 是一个本地优先的 AI 原生加密货币交易操作系统，让单人交易者拥有机构级的研究、决策、执行和学习能力。

**展开**：Aletheia 不是一个信号机器人，也不是一个量化回测平台。它是一个完整的交易 OS——从市场感知、信号发现、System Operator 判断、纸上验证、风控把关到最终执行，全流程由 AI agent 编排驱动。人类设定边界（风控限制、可交易资产、资金上限、是否允许实盘），AI 在这些边界内自主研究、学习和交易。

**哲学内核**：Aletheia（古希腊语"真理/揭示"）——系统必须使每一笔交易、每一个策略、每一次决策可追溯到证据。不存在黑盒决策。

---

## 2. 目标用户画像

| 维度 | 描述 |
|------|------|
| 身份 | Solo 加密交易者，同时是系统的唯一开发者/运维者 |
| 技术栈 | Python 熟练，有基础 DevOps 能力，能读 LLM prompt |
| 交易风格 | 中频（4h-1d 持仓），不做高频/scalping |
| 痛点 | 手动盯盘时间不足；交易决策受情绪影响；无法系统性积累经验 |
| 期望 | AI 24/7 监控+学习+提议，人类把关关键决策；逐步提升自动化程度 |
| 资金规模 | Demo/小额实验阶段（<$10K），验证后逐步加仓 |

---

## 3. 核心问题陈述

| 痛点 | 解决方案 |
|------|----------|
| 人无法 24/7 盯盘 | 7 类 scanner agent 持续扫描市场 |
| 交易决策受情绪/FOMO 驱动 | System Operator 结构化决策（Bull/Bear debate + conviction sizing） |
| 交易经验不积累 | Knowledge Base + FTS5 检索 + 自动 review + dreaming 整理 |
| 判断无法被经验纠正 | 有因果链的结果、复盘、知识检索和受控的 Learning 影响；没有可验证贡献就回退中性基线 |
| 风控依赖自律 | Risk Engine 硬限不可被 agent 绕过 |
| 无法追踪决策质量 | Confidence calibration + Reasoning feedback loop |

---

## 4. 产品原则

1. **安全 > 功能**：任何新能力如果可能绕过风控或泄露凭证，不做。
2. **可解释 > 高级**：宁可用简单的 FTS5 检索也不用不可解释的 embedding。
3. **数据积累 > 立刻赚钱**：当前阶段价值是证明学习闭环有效，不是 P&L。
4. **渐进信任 > 一步到位**：Paper -> Demo -> Live-limited，每步有明确门控。
5. **本地优先 > 云服务**：所有状态在本地 SQLite/文件，不依赖外部服务可用性。

---

## 5. 功能范围

### 5.1 MVP 内（已实现或正在实现）

| 领域 | 功能 | 状态 |
|------|------|------|
| 数据采集 | Crypto market data (BTC/ETH/SOL + universe), OHLCV, funding rate, taker ratio | Done |
| 信号发现 | Market Observer, Scanner chain, Universe forward outcomes | Done |
| 交易判断 | System Operator 是唯一开仓决策者；历史 strategy records 仅保留归因，不存在可执行 RuleSet/DSL/backtest 引擎 | Done |
| 决策 | System Operator (sole decision maker), Bull/Bear debate, conviction sizing | Done |
| 执行 | Paper broker, Bitget demo, Binance testnet, TPSL exchange orders | Done |
| 风控 | Risk Engine (hard limits), position sizing, correlation risk, daily-loss kill switch | Done |
| 学习 | Knowledge Base (FTS5), Dreaming agent, Post-trade review, Confidence calibration | Done |
| 前端 | Bloomberg-dense cockpit, 14-page Web UI, Go-Live Scorecard | Done |
| 运维 | Gateway supervisor, Watchdog, Doctor probes, Self-heal layer | Done |
| 操作员 | System Operator (event-driven daemon + Operator Loop 4-stage + Command Center) | Done |

### 5.2 延期（有设计，未授权实施）

| 功能 | 原因 | 参考文档 |
|------|------|----------|
| Live trading (实盘) | 需积累足够 paper 数据 + 用户显式授权 | ROADMAP Phase 6 |
| Self-evolution Tier 2-3 | 需 Tier 1 积累数据 | plans/self_evolution_plan.md |
| News intelligence scanner | 缺数据源 + 不是当前瓶颈 | ROADMAP Phase 5 |
| Cross-agent debate | 架构复杂度高, 当前 System Operator 辩论已覆盖 | ROADMAP Phase 4 |
| Trailing stop ML optimization | 需更多交易数据 | — |

### 5.3 永不做

| 功能 | 原因 |
|------|------|
| Agent 自动生成可执行 Python 策略代码 | 安全风险不可控 |
| 重建以 RuleSet/DSL/backtest 为实际交易决策源的策略工厂 | 与当前 agentic 决策路线冲突，且会重新制造名实不符的执行链 |
| End-to-end RL | 数据不足 + 不透明 |
| 多用户 SaaS | 违背 local-first 原则 |
| 高频交易 | 不是目标用户场景 |
| Docker/Postgres/Redis 在 MVP | 过度工程化 |
| Agent 自改硬风控限额 | 安全红线 |

---

## 6. 成功标准

详细的阶段性标准见 [PHASE_GATES.md](../gates/PHASE_GATES.md)。高层概括：

| 阶段 | 核心问题 | 标准 |
|------|----------|------|
| Phase 0 | 系统能稳定运行吗？ | Gateway uptime >99%, doctor 0 critical |
| Phase 1 | 学习在产出信号还是噪音？ | 记忆被引用率 >50%, ECE <0.15 |
| Phase 2 | 策略执行质量如何？ | Paper win rate + Sharpe 满足门控 |
| Phase 3+ | 系统能自我改进吗？ | 策略迭代 + 参数反馈闭环 |
| Phase 6 | 可以上实盘吗？ | Go-Live Scorecard composite >=70 |

---

## 7. 约束与假设

### 约束

- 单人操作（无团队/无运维轮班）
- 本地机器运行（Windows 11，无 GPU 要求）
- LLM 依赖外部 API（Databricks/OpenRouter），需网络
- Bitget API 作为主交易所（已有 Binance testnet / OKX demo 备选）
- 资金有限（demo 阶段 <$10K）

### 假设

- 中频交易（4h-1d）在加密市场有 edge 可被系统性发现
- LLM 决策质量可通过结构化 context + feedback loop 持续提升
- Paper trading 结果可作为实盘预期的合理代理（扣除滑点/流动性差异）
- 单人 solo 系统在 MVP 阶段不需要多租户/权限分离

---

## 8. 词汇表

| 术语 | 定义 |
|------|------|
| System Operator | Primary Intelligence Agent — 系统唯一的交易决策者 |
| Scanner | 市场扫描 agent，产出信号/机会，但不做决策 |
| Opportunity | Scanner 发现的潜在交易机会（未决策） |
| Proposal | System Operator 产出的交易提案（待 Risk Engine 审批） |
| Legacy strategy record | 历史归因记录；不驱动开仓、不包含可执行 RuleSet/DSL 引擎 |
| Guard Pipeline | 执行前的检查链（Risk Engine 硬限不可绕过） |
| Kill Switch | 紧急熔断：触发后所有交易活动停止直到人工恢复 |
| Conviction Sizing | 基于 System Operator confidence 的 fractional Kelly 仓位计算 |
| Dreaming | 每日知识整理 agent（灵感来自 Sophia/ALMA 论文） |
| Go-Live Scorecard | 10 项指标的综合评分，决定是否可安全上线实盘 |
| Operator Loop | 15-60min 级别的自动化运维循环（评估/调优/提议） |
| Experience Loop | 经验闭环：交易到评估到学习到注入下次决策 |
| UTA | Unified Trading Account — 跨所聚合层（只读） |

---

## 关联文档

- 远期愿景：[North Star](../../references/external-systems/north_star.md)（方向参考，非施工授权）
- 实施规则：[Implementation Guide](../../governance/policies/implementation_guide.md)
- 系统架构：[SYSTEM_ARCHITECTURE_GUIDE](../../design/architecture/SYSTEM_ARCHITECTURE_GUIDE.md)
- 路线图：[ROADMAP](../../status/roadmap/ROADMAP.md)
- 阶段门控：[PHASE_GATES](../gates/PHASE_GATES.md)
- 指标定义：[METRICS](../gates/METRICS.md)
- 稳定产品取舍：[Product Direction](DIRECTION.md)
