# PI Agent Context Enhancement — 设计报告

> 状态：设计讨论稿（2026-06-20）。需用户确认后实施。
> 目标：让 PI Trading Decision Agent 拿到足够丰富的上下文做出高质量的自适应决策，同时保留 LLM 自由推理空间。

---

## 一、研究发现（学术 + 竞品）

### 1.1 学术论文关键结论

| 论文 | 核心发现 | 对 Aletheia 的启示 |
|---|---|---|
| **Multi-Agent Crypto Portfolio** (arXiv:2501.00826) | 三 agent 分工（Market Dynamics + Sentiment + Trading）→ 133.5% 年化收益、1.5 Sharpe；去掉 Market Agent 收益降 42.57% | **结构化市场数据分析是最关键的 context 维度** |
| **FinAgent** (arXiv:2402.18485) | 多模态（数值+文本+K线图）输入，36% 利润提升；双层 reflection 模块快速适应 regime 变化 | **reflection/反馈循环比堆更多数据更有效** |
| **Multi-Agent + Verbal Feedback** (arXiv:2510.08068) | Reflect agent 提供自然语言日/周批评注入 prompt → 31% 总体提升、10% 熊市减亏；**无需微调** | **经验教训作为自然语言反馈直接注入是性价比最高的提升手段** |
| **LLM Trading Benchmark** (arXiv:2512.10971) | 通用智能 ≠ 交易能力；**风控能力才是跨市场稳健性的决定因素** | **risk control 层必须独立于 LLM，不能让 LLM 直接管风控** |
| **Rational Agent Architecture** (arXiv:2311.13743) | Profiling + Memory + Decision 三模块；分层记忆模拟人类认知；可调"认知跨度"增强表现 | **分层 context：环境快照 + 短期记忆 + 长期经验** |
| **LLM Finance Survey** (arXiv:2507.01990) | 多模态整合 > 单维度；分层多 agent 优于单 agent；CVaR 风控作为独立 guardrail 而非 prompt 约束 | **风控在 LLM 外部执行，不写进 prompt 里限制推理** |
| **Two-Layer Memory** (arXiv:2605.19337) | Layer A = 只读确定性状态（仓位/余额/风控限制）；Layer B = 生成式上下文（可淘汰）；LLM 不应写 Layer A | **确证我们 "Guard 链在外、LLM 管决策" 的方向正确** |

### 1.2 竞品实践

| 系统 | 做法 | 关键特点 |
|---|---|---|
| **Valory/Olas Trader** | 不给 LLM 喂原始市场数据；只发一个"问题+选项"的 prompt 给 AI Mech；决策是 FSM + 阈值规则 | 极度保守——LLM 只做概率估计，不做交易决策 |
| **Almanak** | StrategyState 对象（组合、实时价格、技术指标、gas）；意图架构（高层 intent → 编译执行） | 结构化 state 对象 + 执行抽象分离 |
| **OpenAlice** | Workspace + 真 agent CLI；MCP 工具暴露给 agent；Guard 链独立 | LLM 自主研究 + 规则风控分离 |

### 1.3 综合结论

> **最优 context 不是"越多越好"，而是"分层 + 可衰减 + 给 LLM 留推理空间"。**

关键原则：
1. **结构化市场数据**是信息增益最高的维度（去掉它收益降 40%+）
2. **Verbal feedback（经验教训自然语言注入）**是零成本提升最大的手段（+31%）
3. **更长 context ≠ 更好决策**——有"注意力稀释"效应，需要精炼
4. **风控必须在 LLM 输出之后执行**（独立 guardrail），不在 prompt 里限制
5. **分层记忆**：快照（当前态）+ 短期回顾（近7天）+ 长期经验（lessons）

---

## 二、现状差距分析

### PI Agent 当前 context（`_build_assessment_prompt`）

```
给了什么           | 缺了什么
━━━━━━━━━━━━━━━━━━━|━━━━━━━━━━━━━━━━━━━
5 个 open positions | 无关联度/组合风险
10 个 market snaps  | 无技术指标（BB/RSI/vol_ratio/funding）
5 个 ruleset signals| 无指标具体值、无该策略近期胜率
5 条 lessons 摘要   | 截断 80 字、无结构化统计
risk state         | 无当日已实现 PnL、无 drawdown
(空)               | 无 regime 判断
(空)               | 无近期同类交易 outcome 统计
(空)               | 无 reflection/回顾（上次决策的结果）
```

### 核心问题

LLM 有决策权但**情报不足**，就像一个交易员只看到价格跳动和几条模糊的提示，看不到图表、看不到历史、看不到自己的交易记录。

---

## 三、设计方案：三层 Context 架构

### 3.1 架构图

```
┌─────────────────────────────────────────────────────────┐
│               PI Trading Decision Agent                  │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │         LLM Prompt (structured context)          │    │
│  │                                                  │    │
│  │  Layer 1: Environment Snapshot (只读、确定性)     │    │
│  │  Layer 2: Short-term Memory (近 7 天回顾)        │    │
│  │  Layer 3: Long-term Wisdom (经验教训 + reflection)│    │
│  │                                                  │    │
│  │  + Free Reasoning Space (不限制推理方式)          │    │
│  └─────────────────────────────────────────────────┘    │
│                         ↓                               │
│              Structured Decision Output                  │
│     {action, symbol, direction, conviction, reasoning}  │
└─────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────┐
│          Guard Chain (Risk Enforcement Layer)             │
│  LLM 不可见/不可控 — 纯规则、独立执行                      │
│  kill_switch / position_limit / daily_loss / correlation │
└─────────────────────────────────────────────────────────┘
                         ↓
                   Execution Gateway
```

### 3.2 Layer 1: Environment Snapshot（当前态）

**原则**：LLM 只读，每次决策时新鲜组装，不缓存。

| 数据项 | 来源 | 格式 |
|---|---|---|
| **Portfolio state** | list_positions(open) | symbol, direction, qty, entry, current, unrealized, hold_duration_hours |
| **Account balance** | exchange_sync cache | equity_usdt, available_margin, used_margin_pct |
| **Today's P&L** | today_realized_pnl() | realized_today, best_trade_today, worst_trade_today |
| **Technical features** (top 10 universe) | price_feed + market_data collectors | 每币: price, change_1h/4h/24h, RSI_4h, BB_position, vol_ratio_7d, relative_strength_vs_BTC |
| **Funding rates** (top 5 by |rate|) | data_collectors 已采集 | symbol, rate, annualized |
| **Open interest change** | data_collectors | OI_change_24h_pct for top symbols |

**精炼原则**：只给 top 10 universe 币（已有 ranker 筛选），不 dump 全市场。每个数据点一行，人类可读。

### 3.3 Layer 2: Short-term Memory（近期回顾）

**原则**：7 天滑窗，按相关度衰减。对标论文中"dual-level reflection"。

| 数据项 | 来源 | 格式 |
|---|---|---|
| **Recent trade outcomes** (7 天) | positions(closed, last 7d) | 按 symbol 分组: win/loss 计数, avg return, 最大亏损 |
| **Pattern performance** | settled outcomes | "momentum-long 近 7 天: 2W/5L, avg=-1.2%" |
| **Active regime** | market_observer output | BTC trend(up/down/sideways), volatility_regime(low/mid/high), sector_rotation_signal |
| **Last decision reflection** | 上一轮 pi_trading_decision output | "上次: skip SOL long, conviction=40, 原因=XXX, 实际结果=price +3%（错过机会）" or "上次: trade ETH short, 结果: -2.1%（止损触发）" |

**"reflection" 是关键创新点**：论文实证 +31%，零 token 额外成本（只是把上次结果写回 prompt）。

### 3.4 Layer 3: Long-term Wisdom（经验教训）

**原则**：精选、自然语言、可进化。对标 "verbal feedback loop"。

| 数据项 | 来源 | 格式 |
|---|---|---|
| **Active lessons** (accepted, top 5 by relevance) | knowledge_items | 完整自然语言（不截断 80 字）+ 来源（哪笔交易产生的）|
| **Guard candidates** (recent) | self-evolution Tier 1 output | "SOL long 在 BTC 下跌时连亏 3 次 → 待确认 guard" |
| **Strategy performance** | strategies(paper_running) | 每个活跃策略的 7 天胜率 + PnL |

### 3.5 Free Reasoning Space（LLM 自由区）

**关键设计**：prompt 末尾不给 "如果 X 则做 Y" 的硬规则，而是给**思考引导问题**：

```
## Your Reasoning Space

Consider freely — there are no fixed rules for what you must do:
- What is the dominant narrative right now? Is it priced in?
- Am I seeing confluence (multiple signals aligning) or conflict?
- Given my recent performance, should I be more/less aggressive?
- Is there an asymmetric risk/reward setup I'm missing?
- What would make me change my mind about this trade?

Return your decision with full reasoning. The guard chain handles risk limits
independently — you don't need to worry about position sizing or loss limits.
Just focus on: SHOULD this trade happen, in WHAT direction, with WHAT conviction?
```

**为什么这样设计**：
- 不给 if-else 规则 → LLM 可以发现规则覆盖不到的机会
- 给思考引导而非约束 → 保持 agentic 灵活性
- 明确说"guard chain 管风控" → LLM 不浪费推理能力在 position size / risk limit 上
- 要求 reasoning → 可审计、可学习（post_trade_reviewer 读这个）

---

## 四、Context 预算控制

基于论文 "longer context ≠ better decisions" 的发现，严格控制总量：

| Layer | 目标 token 数 | 策略 |
|---|---|---|
| System prompt (角色定义) | ~200 | 固定 |
| Layer 1: Snapshot | ~800 | 精炼数值，一行一个数据点 |
| Layer 2: Short-term | ~600 | 7 天 summary，不 dump 原始记录 |
| Layer 3: Wisdom | ~400 | Top 5 lessons 全文（avg 80 token/条） |
| Free space + schema | ~200 | 引导问题 + output schema |
| **Total** | **~2200 tokens** | 远低于 context window limit |

对比现在 `_build_assessment_prompt` 约 800 tokens → 增加 ~1400 tokens，但信息密度大幅提升。

---

## 五、"LLM 自控力" 机制

你提到要给 AI "留发挥空间"。具体机制：

### 5.1 Conviction-based Autonomy（已有）

conviction 0-100 已经是 LLM 自己的判断强度表达。保持不变。

### 5.2 Free-form Reasoning（新增）

LLM 在 reasoning 字段可以写任何长度的推理链，不限格式。这个 reasoning 会被：
- 记录到 proposal 的 `agent_rationale`
- 下一轮作为 "上次决策反思" 回注（reflection loop）
- post_trade_reviewer 读取用于事后分析

### 5.3 动态 Action 扩展（新增）

现有 action 只有 `trade/skip/close/reduce`。建议新增：

| Action | 含义 | LLM 自主性 |
|---|---|---|
| `watch` | 有兴趣但不够确定，标记观察 | LLM 可"存疑待观察"而非只能二选一 |
| `scale_in` | 已有仓位，加码（conviction 够高） | LLM 可表达"现有方向对了、要加" |
| `flip` | 反转方向（先平后开反向） | LLM 可表达"方向判断反了" |

### 5.4 Self-set Stop（已有，增强）

LLM 可在输出中指定 `stop_price`。如果不指定，用默认。这给 LLM 选择：
- 对自己有信心 → 给更宽的止损
- 不确定 → 让系统用默认值

---

## 六、与现有系统的集成点

### 不改的部分（guard chain + execution）

- `risk/engine.py` — 不变，独立于 LLM
- `execution/gateway.py` — 不变
- `trust/guard_chain.py` — 不变（已在 submit_decision_as_proposal 中调用）
- Scheduler 15min 周期 — 保持

### 要改的部分

| 文件 | 改动 |
|---|---|
| `agent_shell/commands.py:evaluate_trading_opportunity()` | 扩展 context 构建（Layer 1-3） |
| `agent_shell/trading_decision.py:_build_assessment_prompt()` | 重写 prompt template |
| `agent_shell/trading_decision.py:_DECISION_SCHEMA` | 扩展 action enum + 新增 watch/scale_in/flip |
| `agent_shell/trading_decision.py:evaluate_trading_opportunity()` | 支持 watch/scale_in/flip 的后处理 |
| `agent_shell/context.py` | 新增 `build_enriched_trading_context()` 聚合三层数据 |
| （新文件）`agent_shell/reflection.py` | 管理 "上次决策回顾" 的存储和读取 |

### 数据流对比

```
现在:
  build_app_context(thin) → LLM → trade/skip → submit proposal

改后:
  build_enriched_trading_context() → LLM → trade/skip/watch/scale_in/flip
    ├─ trade/scale_in/flip → submit_decision_as_proposal → guard chain → execution
    ├─ watch → record_watchlist (后续轮次 context 里可见)
    └─ skip → 记录 reasoning (下轮 reflection 用)
```

---

## 七、实施路径（建议分 3 步）

### Step 1: Context 丰富化（最高 ROI）

- 改 `evaluate_trading_opportunity` 的 context 构建
- 增加 Layer 1 技术特征（scanner 已算好，只需传递）
- 增加 Layer 2 近期 outcome 统计
- 重写 `_build_assessment_prompt` 用新 template
- 预计效果：LLM 从"盲人摸象"变成"有仪表盘"

### Step 2: Reflection Loop（论文实证最高提升）

- 新建 `reflection.py`：记录每次决策 + 后续结果
- 下一轮 prompt 注入"上次决策回顾"
- 预计效果：LLM 学会从失败中调整（+31% 论文数据）

### Step 3: Action 扩展 + Free Reasoning

- 扩展 action schema（watch/scale_in/flip）
- 重写 prompt 末尾为"开放思考空间"
- 增加 reasoning 字段无限制输出
- 预计效果：LLM 从"只能 trade/skip" 变成有更丰富的决策表达

---

## 八、风险与约束

| 风险 | 缓解 |
|---|---|
| Context 增加 → LLM 成本上升 | ~2200 token，每 15min 一次，约 $0.01/call，可接受 |
| LLM 幻觉导致错误交易 | Guard chain 独立执行，不受 LLM 影响 |
| Reflection 可能强化错误模式 | 只反馈事实结果，不评价对错 |
| Scale_in/flip 增加复杂度 | 第一阶段不实施，Step 3 再加 |
| Context 信息泄露 | 不含凭证/API key，只含市场数据和仓位状态 |

---

## 九、参考来源

1. arXiv:2501.00826 — Multi-Agent Crypto Portfolio Management
2. arXiv:2402.18485 — FinAgent: Multimodal Financial Trading Agent
3. arXiv:2510.08068 — Multi-Agent Trading with Verbal Feedback
4. arXiv:2512.10971 — LLM Trading Agent Benchmark
5. arXiv:2311.13743 — Rational Agent Architecture for Finance
6. arXiv:2507.01990 — LLM Finance Applications Survey
7. arXiv:2605.19337 — Two-Layer Working Memory for Trading Agents
8. github.com/valory-xyz/trader — Olas/Valory autonomous trader
9. OpenAlice — Trading-as-Git + Guard chain architecture (local ref)
10. Prism — Anti-pattern reference (dead LLM code, local ref)
