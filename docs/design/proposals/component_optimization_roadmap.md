---
id: component-optimization-roadmap
status: draft
related_contracts: []
supersedes: []
---

# Aletheia × 开源项目 — 全组件优化映射报告

> 编制日期: 2026-08-04
> 代码规模: 480 个 Python 文件，7 大层 18+42+15+19+17+14+4 = 129 个模块
> 评估维度: 可解释性提升空间 / 借鉴价值 / 实施难度 / 预期收益

---

## 总览：优化机会矩阵

| # | Aletheia 组件 | 当前状态 | 可借鉴项目 | 优化方向 | 收益 | 难度 |
|---|-------------|---------|-----------|---------|------|------|
| 1 | **market_observer** | LLM黑盒判断 | LLM-TradeBot + Chimera | 多Agent拆分+规则过滤 | ⭐⭐⭐⭐⭐ | 中 |
| 2 | **dreaming_agent** | 单LLM生成想法 | XAlpha | 假设-回测-反思循环 | ⭐⭐⭐⭐ | 中 |
| 3 | **research_agent** | 单次研究 | XAlpha | 研究记忆+假设检验 | ⭐⭐⭐⭐⭐ | 中 |
| 4 | **scanners (6家族)** | 确定性评分 | QuantMuse | 因子归因+LLM解释 | ⭐⭐⭐⭐ | 低 |
| 5 | **paper_trader_ranker** | 公式评分 | LLM-TradeBot | 结构化意见输出 | ⭐⭐⭐⭐ | 低 |
| 6 | **risk/engine.py** | 被动风控 | QuantDinger + Chimera | 主动风控+因果检查 | ⭐⭐⭐⭐⭐ | 中 |
| 7 | **sizing.py** | 固定公式 | QuantMuse | 动态仓位+因子权重 | ⭐⭐⭐⭐ | 中 |
| 8 | **outcome_recorder** | 被动记录 | XAlpha | 归因+学习记忆 | ⭐⭐⭐⭐⭐ | 中 |
| 9 | **guard_promotion** | Shadow only | Chimera | 因果验证+自动转正 | ⭐⭐⭐⭐ | 中 |
| 10 | **reconciliation** | 被动对账 | QuantDinger | 主动异常检测+报告 | ⭐⭐⭐ | 低 |
| 11 | **lesson_reviewer** | 单次回顾 | XAlpha | 研究记忆+Alpha衰减 | ⭐⭐⭐⭐ | 中 |
| 12 | **post_trade_reviewer** | 单次复盘 | QuantMuse | 因子归因+因子衰减 | ⭐⭐⭐⭐ | 中 |
| 13 | **backtest 能力** | 无内置 | XAlpha + QuantDinger | 假设→代码→回测→反思 | ⭐⭐⭐⭐⭐ | 高 |
| 14 | **MCP 集成** | 无 | QuantDinger | 标准化工具调用协议 | ⭐⭐⭐ | 高 |

---

## 1. market_observer → 多 Agent 委员会（已完成 Phase 1）

**当前**: 单一 LLM 读快照 → 输出 direction + confidence

**开源映射**:
- LLM-TradeBot: 每个 Agent 输出结构化意见
- Chimera: LLM → Rule Check → Causal Check → Decision

**已完成**:
- ✅ 4 Agent 委员会 (Trend/Macro/News/Risk)
- ✅ Decision Core 加权投票
- ✅ Rule Engine 7 条规则
- ✅ Decision Report 生成

**仍可深化**:

| 深化方向 | 来源 | 效果 |
|---------|------|------|
| Agent 分歧时自动触发 Chimera Causal Check | Chimera | 不只是投票，还要验证因果 |
| 历史相似行情检索 | XAlpha | "历史上类似形态后 5 天涨了 60%" |
| Agent 权重动态学习 | LLM-TradeBot | 根据历史准确率调整权重 |

---

## 2. dreaming_agent → 假设生成引擎

**当前**: LLM 生成想法 → 存入知识库

**开源映射**:
```
XAlpha:  Research Memory → Hypothesis → Code Gen → Backtest → Reflection → Memory Update
Aletheia:  dreaming_agent → 想法 → ??? → ??? → ???
```

**差距**: 缺少 **假设→验证→反思→记忆** 闭环

**优化方案**:

```python
# 升级后的 dreaming_agent 输出
{
  "hypothesis": "BTC 在 F&G < 30 时做多，3 日内胜率 60%",
  "code": "def signal(fng): return 'long' if fng < 30 else 'neutral'",
  "backtest_result": {"wr": 0.58, "pf": 1.42, "n": 45},
  "reflection": "假设部分成立，但在高波动率环境下失效",
  "memory_update": "仅在 ATR < 3% 时启用此 Alpha"
}
```

| 收益 | 难度 | 优先级 |
|------|------|--------|
| ⭐⭐⭐⭐⭐ | 中 | **P0** |

---

## 3. research_agent → 研究记忆系统

**当前**: 单次研究 → 产出研究笔记

**开源映射**:
- XAlpha: Research Memory 积累 + Alpha 衰减追踪

**差距**: 研究是 **一次性的**，没有积累和衰减

**优化方案**:

```
当前:  研究 BTC ETF → 输出笔记 → 结束
升级:  研究 BTC ETF → 输出笔记 → 存入 Research Memory
       → 3 天后自动验证 → 正确 → 提升权重
       → 7 天后发现失效 → 衰减 → 归档
```

**具体新增组件**:

| 组件 | 功能 |
|------|------|
| ResearchMemoryStore | 持久化研究假设 + 验证状态 |
| AlphaTracker | 追踪每个 Alpha 因子的 IC 衰减 |
| AutoValidator | 自动在 N 天后验证假设 |
| HypothesisRanker | 按历史准确率排序假设 |

| 收益 | 难度 | 优先级 |
|------|------|--------|
| ⭐⭐⭐⭐⭐ | 中 | **P0** |

---

## 4. scanners → 因子归因 + LLM 解释

**当前**: 6 个扫描器家族输出分数，但 **不解释为什么**

**开源映射**:
- QuantMuse: 因子模型 → LLM 解释 → 人工确认

**优化方案**:

```python
# 当前输出
{"family": "momentum", "score": 72, "direction": "long"}

# 升级为
{
  "family": "momentum",
  "score": 72,
  "direction": "long",
  "factor_attribution": [
    {"factor": "Mom10", "value": 3.3, "weight": 0.4, "contribution": 29},
    {"factor": "RSI_slope", "value": 5.2, "weight": 0.3, "contribution": 22},
    {"factor": "Volume_z", "value": 1.8, "weight": 0.3, "contribution": 21},
  ],
  "llm_explanation": "Mom10 +3.3% 确认上行动量，成交量配合放大",
  "historical_ic": 0.042  # 该因子过去 30 天的信息系数
}
```

**可复用**: confidence_calibration.py 已有部分基础设施

| 收益 | 难度 | 优先级 |
|------|------|--------|
| ⭐⭐⭐⭐ | 低 | **P1** |

---

## 5. paper_trader_ranker → 结构化意见输出

**当前**: 公式计算 trade_score，但 **不解释每个分项的贡献**

**开源映射**:
- LLM-TradeBot: 决策核心输出每个 Agent 的投票明细

**优化方案**:

```python
# 当前输出
{"trade_score": 45.2, "symbol": "BTC/USDT"}

# 升级为
{
  "trade_score": 45.2,
  "symbol": "BTC/USDT",
  "score_breakdown": [
    {"component": "confidence", "raw": 65, "weight": 0.30, "contribution": 19.5},
    {"component": "universe_total", "raw": 78, "weight": 0.25, "contribution": 19.5},
    {"component": "regime_delta", "raw": 12, "weight": 0.15, "contribution": 1.8},
    {"component": "evidence_bonus", "raw": 1.15, "weight": 0.10, "contribution": +5.75},
    {"component": "ttl_decay", "raw": 0.92, "weight": "mult", "contribution": -3.2},
  ],
  "human_readable": "高置信度(65) × 高质量标的(78) + 多头环境(+12) + 证据充分(+15%)"
}
```

| 收益 | 难度 | 优先级 |
|------|------|--------|
| ⭐⭐⭐⭐ | 低 | **P1** |

---

## 6. risk/engine → 主动风控 + 因果检查

**当前**: 被动检查（止损/仓位/集中度）

**开源映射**:
- Chimera: Causal Engine — 上涨原因是什么？是否有基本面支撑？
- QuantDinger: Risk 层独立于 Strategy

**优化方案**:

```
当前:  策略信号 → 风险检查(被动) → 执行/拒绝
升级:  策略信号 → 风险检查(主动) → 因果验证 → 执行/拒绝 + 原因
```

**新增能力**:

| 能力 | 描述 | 来源 |
|------|------|------|
| 上涨归因 | "上涨是因为 ETF 流入还是散户 FOMO？" | Chimera |
| 基本面校验 | "PE 是否支撑当前估值？" | Chimera |
| 因果图 | "CPI→利率→美元→BTC 传导链是否成立？" | Chimera |
| 风险预警 | "当前仓位 + 相关性 = 风险集中度过高" | QuantDinger |

| 收益 | 难度 | 优先级 |
|------|------|--------|
| ⭐⭐⭐⭐⭐ | 中 | **P0** |

---

## 7. sizing → 动态仓位 + 因子权重

**当前**: 固定公式（ATR target %）

**开源映射**:
- QuantMuse: 多因子权重动态调整

**优化方案**:

```python
# 当前
position_size = account_balance * risk_pct / atr

# 升级
position_size = (
    account_balance
    * base_risk_pct
    * trend_strength_factor      # 趋势越强，仓位越大
    * confidence_factor          # 置信度越高，仓位越大
    * macro_alignment_factor     # 宏观一致时加仓
    * (1 - portfolio_heat)       # 仓位越满，新仓越小
    * historical_edge_factor     # 该币种历史胜率高，加仓
)
```

| 收益 | 难度 | 优先级 |
|------|------|--------|
| ⭐⭐⭐⭐ | 中 | **P1** |

---

## 8. outcome_recorder → 归因 + 学习记忆

**当前**: 记录结果 → 存入数据库

**开源映射**:
- XAlpha: 研究记忆 + Alpha 衰减 + 反思循环

**差距**: 结果 **只记录了，没有学习**

**优化方案**:

```
当前:  交易结束 → 记录 PnL → 归档
升级:  交易结束 → PnL 归因 → 因子有效性更新 → 研究记忆更新
```

**新增归因维度**:

| 维度 | 问题 | 动作 |
|------|------|------|
| 因子归因 | "这笔交易赚钱是因为趋势因子还是动量因子？" | 更新因子 IC |
| 时间归因 | "持仓 1h 赚的还是 24h 赚的？" | 更新最优持仓周期 |
| 环境归因 | "在 bull 市场还是 bear 市场赚的？" | 更新 regime 表现 |
| Agent 归因 | "Trend Agent 对还是 Risk Agent 对？" | 更新 Agent 权重 |

| 收益 | 难度 | 优先级 |
|------|------|--------|
| ⭐⭐⭐⭐⭐ | 中 | **P0** |

---

## 9. guard_promotion → 因果验证 + 自动转正

**当前**: Shadow → 积累证据 → 人工确认

**开源映射**:
- Chimera: Causal Engine 验证后才能转正

**优化方案**:

```
当前:  Shadow guard → min_matching_trades → 人工确认
升级:  Shadow guard → min_matching_trades → 因果验证 → 自动转正/拒绝
```

**因果验证检查**:
- "该 guard 拦截的交易，事后看确实是亏损的吗？"
- "该 guard 的规则是否有基本面支撑？"
- "该 guard 在不同市场环境下都有效吗？"

| 收益 | 难度 | 优先级 |
|------|------|--------|
| ⭐⭐⭐⭐ | 中 | **P1** |

---

## 10. reconciliation → 主动异常检测

**当前**: 被动对账 → 发现不一致 → 报告

**开源映射**:
- QuantDinger: 主动风险监控 + 异常预警

**优化方案**:

```
当前:  定时对账 → 不一致 → 记录错误
升级:  实时监测 → 异常模式检测 → 预警 → 自动修复建议
```

**新增能力**:

| 能力 | 描述 |
|------|------|
| 异常模式识别 | "过去 1 小时对账失败率从 0% 升至 15%" |
| 预测性维护 | "按当前失败率，预计 2 小时后触发风控" |
| 自动修复 | "已知模式 X 失败，自动应用修复 Y" |

| 收益 | 难度 | 优先级 |
|------|------|--------|
| ⭐⭐⭐ | 低 | **P2** |

---

## 11. lesson_reviewer → 研究记忆 + Alpha 衰减

**当前**: 单次回顾 → 产出教训

**开源映射**:
- XAlpha: Research Memory + Alpha 生命周期管理

**优化方案**:

```
当前:  交易结束 → 回顾 → 教训 → 存入知识库
升级:  交易结束 → 回顾 → 教训 → 关联到具体因子/Agent → 更新权重
       → N 天后自动验证教训是否仍然有效
       → 失效的教训自动衰减/归档
```

| 收益 | 难度 | 优先级 |
|------|------|--------|
| ⭐⭐⭐⭐ | 中 | **P1** |

---

## 12. post_trade_reviewer → 因子归因 + 因子衰减

**当前**: 单次复盘 → 产出报告

**开源映射**:
- QuantMuse: 因子归因 + 因子 IC 追踪

**优化方案**:

```python
# 升级后的复盘输出
{
  "trade_id": "xxx",
  "pnl": +45.2,
  "factor_attribution": {
    "trend": +32.1,      # 趋势因子贡献
    "momentum": +15.3,   # 动量因子贡献
    "volatility": -2.2,  # 波动率因子拖累
  },
  "agent_attribution": {
    "Trend": +28.5,      # Trend Agent 贡献
    "Macro": +16.7,      # Macro Agent 贡献
    "Risk": 0.0,         # Risk Agent 无贡献
  },
  "regime": "bull",
  "optimal_hold_time": "8h",  # 最优持仓时间
  "key_lesson": "在低波动率环境中，趋势因子表现最佳",
}
```

| 收益 | 难度 | 优先级 |
|------|------|--------|
| ⭐⭐⭐⭐ | 中 | **P1** |

---

## 13. backtest 能力 → 内置回测引擎

**当前**: 无内置回测，依赖外部脚本

**开源映射**:
- XAlpha: Hypothesis → Code Gen → Backtest → Reflection
- QuantDinger: 内置回测管道

**这是最大的架构缺口** — 没有回测就无法验证假设

**优化方案**:

```python
class BacktestEngine:
    """Built-in backtest for Alpha hypotheses."""

    def backtest_factor(
        self,
        factor_code: str,  # Python code defining the signal
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> dict:
        """Run a factor backtest and return performance metrics."""

    def backtest_agent_weights(
        self,
        weights: dict[str, float],  # agent_name -> weight
        start_date: str,
        end_date: str,
    ) -> dict:
        """Backtest a specific agent weight configuration."""
```

| 收益 | 难度 | 优先级 |
|------|------|--------|
| ⭐⭐⭐⭐⭐ | 高 | **P2** |

---

## 14. MCP 集成 → 标准化工具协议

**当前**: 无

**开源映射**:
- QuantDinger: MCP (Model Context Protocol) 集成

**价值**: LLM 通过标准化协议调用工具（扫描器、风险引擎、执行网关）

```python
# 当前: LLM 直接调用 Python 函数
result = scanner.score(features)

# MCP 升级: LLM 通过标准协议调用
result = await mcp.call_tool("scanner.score", {"features": features})
```

| 收益 | 难度 | 优先级 |
|------|------|--------|
| ⭐⭐⭐ | 高 | **P3** |

---

## 实施优先级排序

### P0（立即做，核心价值）

| # | 组件 | 改动 | 预期收益 |
|---|------|------|---------|
| 1 | dreaming_agent | 假设→回测→反思循环 | Alpha 发现能力 |
| 2 | research_agent | 研究记忆系统 | 知识积累 |
| 3 | risk/engine | 主动风控 + 因果检查 | 风控质量 |
| 4 | outcome_recorder | 归因 + 学习记忆 | 从错误中学习 |

### P1（短期，高回报）

| # | 组件 | 改动 | 预期收益 |
|---|------|------|---------|
| 5 | scanners | 因子归因 + LLM 解释 | 可解释性 |
| 6 | ranker | 结构化意见输出 | 可调试性 |
| 7 | sizing | 动态仓位 | 风险调整收益 |
| 8 | guard_promotion | 因果验证 | 自动化 |
| 9 | lesson_reviewer | 研究记忆 | 持续学习 |
| 10 | post_trade_reviewer | 因子归因 | 因子选择 |

### P2（中期，战略价值）

| # | 组件 | 改动 |
|---|------|------|
| 11 | backtest | 内置回测引擎 |
| 12 | reconciliation | 主动异常检测 |

### P3（长期，生态价值）

| # | 组件 | 改动 |
|---|------|------|
| 13 | MCP | 标准化工具协议 |

---

## 总结：最大的 3 个架构缺口

1. **没有回测引擎** — 无法验证假设，无法自动优化
   - 来源: XAlpha, QuantDinger
   - 影响: 所有因子/Agent 权重只能靠实盘验证

2. **没有研究记忆** — 知识不积累
   - 来源: XAlpha
   - 影响: 每次研究都是从零开始，无法站在"过去的肩膀"上

3. **没有归因系统** — 不知道盈亏来自哪里
   - 来源: QuantMuse, Chimera
   - 影响: 无法区分"运气"和"能力"，无法针对性改进
