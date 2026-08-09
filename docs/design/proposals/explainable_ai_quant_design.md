---
id: explainable-ai-quant-design
status: draft
related_contracts: []
supersedes: []
---

# Aletheia 可解释 AI 量化系统设计报告

> 编制日期: 2026-08-04
> 状态: 设计提案（讨论阶段，未实施）
> 目标: 将 LLM 黑盒决策升级为可解释、可审计、可复盘的 AI 投资委员会

---

## 1. 问题定义

### 1.1 当前状态

Aletheia 的决策链路:

```
市场数据 → 扫描器(确定性) → LLM观察者(黑盒) → 排名器(确定性) → 风险引擎 → 执行
```

**核心矛盾**: LLM 是唯一不可解释的组件。它能输出 `direction=long, score=65`，但:

- 不知道为什么是 long 而不是 short
- 不知道 65 分里每个指标贡献了多少
- 不知道历史上类似判断对了多少
- 无法区分"有证据的判断"和"随机猜测"

### 1.2 已实施的改进（2026-08-04）

| 改进 | 效果 | 仍有的局限 |
|------|------|-----------|
| ≥3 指标引用门控 | 强制 LLM 引用数据 | LLM 仍可能"凑"引用 |
| Chain-of-thought reasoning | 暴露推理过程 | 推理可能是事后合理化 |
| 降低 LLM confidence 权重 | 减少黑盒影响 | 仍是单一 LLM 判断 |
| 证据质量乘数 | 奖励有证据的判断 | 不解决"为什么买"的根本问题 |

**结论**: 当前优化是增量改良，不是架构升级。需要系统性设计。

---

## 2. 外部项目分析

### 2.1 QuantDinger

**架构**: Market Data → Indicator → LLM Research → Strategy → Backtest → Risk → Execution

**优点**:
- 完整的端到端管道
- MCP 协议集成（标准化工具调用）
- Docker 一键部署（工程成熟度高）

**可解释性缺陷**:
- LLM Research 是单点黑盒
- 无法追溯"为什么这个策略被选中"
- Agent 输出不可分解为因子贡献

**可借鉴点**:
| 技术 | 适配方案 |
|------|---------|
| MCP 工具协议 | 将扫描器/排名器封装为 MCP Tool，LLM 通过结构化调用获取信号 |
| 多 Agent 流水线 | 将单一 LLM Observer 拆分为多个专职 Agent |
| 回测集成 | 每个 Agent 输出附带回测证据 |

---

### 2.2 LLM-TradeBot（推荐研究）

**架构**: 市场状态识别 Agent → 风险 Agent → 策略 Agent → Decision Core

**核心创新**: **每个 Agent 输出结构化意见**

```
Trend Agent:   上涨概率 72% | 原因: MACD金叉, EMA20>EMA60, Volume增加
Risk Agent:    波动率偏高 | 建议: 降低仓位
Decision Core: BUY | Confidence: 84%
```

**可解释性优势**:
- 每个 Agent 独立可审计
- 最终决策可追溯至子 Agent 意见
- 冲突可检测（Trend 看多 vs Risk 警告）

**可借鉴点（优先级最高）**:

| 技术 | 适配方案 | 预期效果 |
|------|---------|---------|
| 多 Agent 委员会 | 将单一 LLM Observer 拆分为 Trend/Macro/Risk/News Agent | 决策来源清晰化 |
| 结构化意见格式 | 每个 Agent 输出 `{direction, confidence, evidence[], citation[]}` | 可量化对比 |
| Decision Core 综合 | 加权投票机制替代单一 LLM 判断 | 减少单点黑盒 |
| 全程日志 | 每个 Agent 的输入/输出全部持久化 | 事后可复盘 |

---

### 2.3 QuantMuse

**架构**: 多因子模型 → LLM 解释 → 人工确认

**核心思想**: **LLM 不直接下单，只解释因子信号**

**优点**:
- 因子模型是完全可解释的（线性权重）
- LLM 只负责"翻译"因子信号为人话
- 人工确认作为最终安全阀

**可借鉴点**:

| 技术 | 适配方案 |
|------|---------|
| LLM-as-解释器 | 扫描器输出分数后，LLM 翻译为自然语言报告 |
| 人在回路 | 高置信度自动执行，低置信度生成报告等待确认 |
| 因子归因 | 明确每个因子对最终评分的贡献值 |

---

### 2.4 Project Chimera（重点推荐）

**架构**: LLM → Symbolic Reasoning → Rule Check → Causal Check → Final Decision

**核心创新**: **Neuro-Symbolic-Causal 三层过滤**

```
LLM: "建议买 Tesla"
  → Rule Engine: PE > 150 违反价值规则 → 标记风险
  → Causal Engine: 上涨原因是 AI 概念不是盈利 → 拒绝
  → Final: Reject
```

**可解释性最强**: 每个决策都有完整的规则/因果链证明。

**可借鉴点（架构参考价值最高）**:

| 技术 | 适配方案 | 难度 |
|------|---------|------|
| Symbolic Reasoning | 在 LLM 输出后增加确定性规则检查层 | 中 |
| Rule Engine | 将当前 guard 系统扩展为完整规则引擎 | 低（已有基础） |
| Causal Check | 新增因果验证：上涨是否有基本面支撑 | 高 |
| 三层过滤架构 | LLM → 规则检查 → 因果验证 → 决策 | 中 |

---

## 3. 推荐架构：AI 投资委员会

### 3.1 设计理念

将单一 LLM Observer 替换为 **多 Agent 委员会 + 决策核心**，融合上述项目的优点：

```
                    ┌─────────────────────────────────────┐
                    │          AI Investment Committee      │
                    │                                       │
  市场数据 ──────→  │  ┌──────────┐  ┌──────────┐          │
                    │  │ Trend    │  │ Macro    │          │
                    │  │ Agent    │  │ Agent    │          │
                    │  └────┬─────┘  └────┬─────┘          │
                    │       │              │                │
                    │  ┌────┴─────┐  ┌────┴─────┐          │
                    │  │ News     │  │ Risk     │          │
                    │  │ Agent    │  │ Agent    │          │
                    │  └────┬─────┘  └────┬─────┘          │
                    │       │              │                │
                    │  ┌────▼──────────────▼─────┐         │
                    │  │   Decision Core          │         │
                    │  │   (加权投票 + 冲突检测)  │         │
                    │  └────────────┬────────────┘         │
                    │               │                      │
                    │  ┌────────────▼────────────┐         │
                    │  │   Rule Engine            │         │
                    │  │   (确定性规则检查)       │         │
                    │  └────────────┬────────────┘         │
                    │               │                      │
                    │  ┌────────────▼────────────┐         │
                    │  │   Decision Report        │         │
                    │  │   (结构化决策报告)       │         │
                    │  └─────────────────────────┘         │
                    └─────────────────────────────────────┘
```

### 3.2 Agent 设计

#### Trend Agent（趋势判断）

输入: 价格/SMA/RSI/Momentum/Bollinger
输出:
```json
{
  "direction": "long",
  "confidence": 0.72,
  "evidence": [
    {"indicator": "SMA", "value": "price > SMA50", "contribution": +18},
    {"indicator": "RSI", "value": "RSI 64.7 bullish", "contribution": +12},
    {"indicator": "Mom10", "value": "+3.30% 4h", "contribution": +15}
  ],
  "reasoning": "价格在均线上方，RSI偏多未超买，动量确认"
}
```

#### Macro Agent（宏观判断）

输入: Fear&Greed, 美元指数, BTC ETF 流入, 链上数据
输出:
```json
{
  "regime": "risk_on",
  "confidence": 0.65,
  "evidence": [
    {"factor": "F&G", "value": "28 Fear", "contribution": "contrarian buy"},
    {"factor": "ETF", "value": "净流入 +120M", "contribution": +10}
  ]
}
```

#### News Agent（新闻情绪）

输入: 新闻标题/摘要
输出:
```json
{
  "sentiment": "positive",
  "confidence": 0.60,
  "headlines": ["BTC ETF 持续流入", "美联储降息预期"],
  "impact_score": +8
}
```

#### Risk Agent（风险评估）

输入: ATR/波动率/当前仓位/相关性
输出:
```json
{
  "risk_level": "medium",
  "position_size_factor": 0.7,
  "concerns": ["ATR 偏高", "CPI 即将公布"],
  "max_position_size": "0.35%"
}
```

### 3.3 Decision Core（决策核心）

**加权投票机制**:

```
final_score = Σ(agent.confidence × agent.contribution × agent_weight)

权重配置:
  Trend Agent:    0.30  (技术分析)
  Macro Agent:    0.25  (宏观环境)
  News Agent:     0.15  (事件驱动)
  Risk Agent:     0.30  (风险控制, 可一票否决)
```

**冲突检测**:
- Trend 强烈看多 + Risk 警告 → 降级 confidence
- Macro 看空 + News 看多 → 标记分歧，等待更多证据
- 所有 Agent 一致 → 高置信度执行

### 3.4 Rule Engine（规则过滤）

在 LLM Committee 输出后，增加确定性规则检查:

```python
RULES = [
    Rule("ATR > 5%", max_position=0.5, reason="高波动限制仓位"),
    Rule("conflict_detected", require_manual=True, reason="Agent 分歧需人工确认"),
    Rule("single_agent_dominance > 0.8", flag="风险集中", reason="单一来源权重过高"),
    Rule("news_blacklist_match", reject=True, reason="黑名单新闻"),
    Rule("consecutive_losses >= 5", cooldown=3600, reason="连续亏损冷却"),
]
```

### 3.5 Decision Report（决策报告）

每个决策自动生成结构化报告:

```markdown
# Decision Report: BTC/USDT
## 2026-08-04 19:30 UTC

### 结论
**BUY** | Confidence: 78% | Position: 0.35%

### Agent 投票
| Agent | Direction | Confidence | Weight | 加权贡献 |
|-------|-----------|------------|--------|---------|
| Trend | long | 0.72 | 0.30 | +0.216 |
| Macro | long | 0.65 | 0.25 | +0.163 |
| News | long | 0.60 | 0.15 | +0.090 |
| Risk | medium | 0.70 | 0.30 | -0.050 |

### 关键证据
1. **MACD 金叉** (+18): 1h/4h 双周期金叉确认
2. **ETF 净流入** (+12): 连续 3 日净流入共 $320M
3. **RSI 64.7** (+10): 偏多但未超买

### 风险因素
1. **CPI 公布** (-8): 48 小时后 CPI 数据，不确定性高
2. **ATR 偏高** (-5): 波动率 5.2%，超出正常区间

### 规则检查
✅ 仓位限制: 0.35% < max 0.5% (ATR 规则)
✅ 无冲突: 3/4 Agent 方向一致
✅ 无黑名单: 无敏感新闻

### 执行
Paper trade executed: BUY 0.0035 BTC @ $105,200
```

---

## 4. 与当前系统的映射

### 4.1 现有组件复用

| 现有组件 | 新架构角色 | 改动 |
|---------|-----------|------|
| 扫描器 (6 families) | Trend Agent 的输入层 | 最小改动 |
| LLM Observer | 拆分为 4 个专职 Agent | 重构 |
| paper_trader_ranker | Decision Core 的评分组件 | 扩展 |
| Risk Engine | Risk Agent + Rule Engine | 增强 |
| audit.jsonl | Decision Report 存储 | 复用 |
| knowledge base | Agent 记忆/学习 | 扩展 |

### 4.2 新增组件

| 新组件 | 功能 | 依赖 |
|--------|------|------|
| Trend Agent | 趋势判断（替代部分 LLM Observer） | 现有扫描器 |
| Macro Agent | 宏观环境评估 | F&G, ETF, 链上数据 |
| News Agent | 新闻情绪分析 | news_collector |
| Risk Agent | 风险评估 | 波动率, 仓位数据 |
| Decision Core | 加权投票 + 冲突检测 | 所有 Agent |
| Rule Engine | 确定性规则过滤 | 配置化的规则列表 |
| Report Generator | 决策报告生成 | 模板引擎 |

---

## 5. 实施路线

### Phase 1: 最小可验证升级（1-2 周）

**目标**: 验证多 Agent 委员会是否比单一 LLM 好

1. **保留**当前 LLM Observer 作为 "Solo Agent"
2. **新增** Trend Agent（基于扫描器分数的结构化输出）
3. **新增** Risk Agent（基于波动率/仓位的结构化输出）
4. **新增** Decision Core（加权投票）
5. **A/B 对比**: 50% 走旧链路, 50% 走新链路
6. **指标**: 信号胜率、盈亏比、可解释性评分

**预期**: 胜率从 43% → 48-52%（因为减少了黑盒猜测）

### Phase 2: 完整委员会（2-4 周）

**目标**: 4 Agent 全覆盖 + Rule Engine

1. 新增 Macro Agent（F&G + ETF + 链上）
2. 新增 News Agent（新闻情绪）
3. 完整 Rule Engine（10+ 条规则）
4. Decision Report 生成
5. 可解释性评分系统

### Phase 3: Neuro-Symbolic 升级（1-2 月）

**目标**: Project Chimera 风格的三层过滤

1. Symbolic Reasoning 层（确定性规则推导）
2. Causal Engine（因果验证：上涨是否有基本面支撑）
3. Research Memory（Alpha 因子长期记忆）
4. Hypothesis → Backtest → Reflection 循环（XAlpha 风格）

---

## 6. 可解释性评分体系

为了量化改进效果，建议引入 **Explainability Score (ES)**:

```
ES = Σ(component_scores) / n

component_scores:
  - evidence_count: 每个决策的证据数量 (>=3 得满分)
  - agent_agreement: Agent 一致性 (完全一致=1.0, 分歧=0.5)
  - rule_compliance: 规则通过率 (全部通过=1.0)
  - historical_accuracy: 类似判断的历史准确率
  - citation_quality: 引用的数据是否直接支持结论 (LLM 自评)

ES >= 0.8: 高可解释，可自动执行
ES 0.5-0.8: 中等，建议人工复核
ES < 0.5: 低可解释，拒绝执行 + 标记复盘
```

---

## 7. 论文价值

如果实施本设计，以下方向具备论文发表潜力:

| 方向 | 创新点 | 目标会议/期刊 |
|------|--------|-------------|
| AI Investment Committee | 多 Agent 投票 + 结构化证据 | NeurIPS AI4Fin |
| Explainable LLM Trading | ES 评分体系 + 决策报告 | AAAI ExAI |
| Neuro-Symbolic Quant | 三层过滤架构 | IJCAI |
| Agent 分歧检测 | Trend vs Risk 冲突自动识别 | KDD |

---

## 8. 下一步

本报告为设计提案，**未实施任何代码修改**。

建议讨论:
1. Phase 1 的 A/B 测试方案是否可行？
2. Agent 权重如何初始设定？是否需要动态学习？
3. Rule Engine 的规则列表如何确定？
4. Decision Report 的存储格式（JSON / Markdown / 两者都要）？
5. 可解释性评分 ES 的阈值如何设定？

---

## 附录: 参考项目对比

| 项目 | 架构类型 | 可解释性 | 借鉴程度 | 理由 |
|------|---------|---------|---------|------|
| QuantDinger | 单 Agent 流水线 | ★★☆☆☆ | 中 | MCP 协议可借鉴 |
| LLM-TradeBot | 多 Agent 委员会 | ★★★★☆ | **高** | 结构化意见输出最直接可借鉴 |
| QuantMuse | 因子+LLM解释 | ★★★☆☆ | 中 | LLM-as-翻译思想 |
| Project Chimera | Neuro-Symbolic | ★★★★★ | **高** | 三层过滤架构 |
| XAlpha | 研究记忆循环 | ★★★★☆ | 中 | 长期 Alpha 记忆 |
