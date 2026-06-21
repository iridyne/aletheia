# PI Agent 进化分析：从 Trader → System Operator

> 竞品调研 + 架构设计方案 | 2026-06-21

---

## 1. 竞品架构对比

### 1.1 三种主流模式

| 模式 | 代表项目 | 核心结构 | 优势 | 劣势 |
|------|----------|----------|------|------|
| **Flat/Peer (对等)** | Olas/Autonolas | 多 agent 通过 FSM + BFT 共识协调；无中心 | 去中心化、容错 | 决策慢、无法做全局优化 |
| **Pyramid (金字塔)** | virattt/ai-hedge-fund, FinRobot | 顶层 Manager 统管；下层专家 agent 汇报 | 全局视野、决策一致 | 单点瓶颈、上下文膨胀 |
| **Ensemble (集成)** | Numerai, OctoBot | 独立信号源产出 → meta-model 加权合成 | 多样性、可解释 | 权重需要大量历史数据 |

### 1.2 各竞品详情

**virattt/ai-hedge-fund** (最接近我们的金字塔模型)
- 19 个专业 agent：13 "投资风格" + 4 "分析维度" + 2 "运营"
- 数据流：Analysts → Risk Manager (约束) → Portfolio Manager (最终决策)
- Portfolio Manager = 唯一出单者，其他全是输入
- **关键启发**：多视角分析 + 单一决策点 + 风险独立门

**FinRobot** (学术级四层架构)
- 四层：Foundation Models → LLMOps/DataOps → Financial LLM → AI Agents
- Smart Scheduler 内有 **Director Agent** 负责任务分配
- Agent 遵循 Perception → Brain → Action 三阶段
- **关键启发**：Director 不替代专家做分析，只调度和分配

**Almanak** (DeFi 自治)
- 无 meta-agent；每个策略独立 `configure()` → `decide()` → intents
- 复杂性下沉到 "Intent Compiler" (编译+执行层)
- **关键启发**：简单策略不需要编排；复杂性在编译层解决

**Olas/Autonolas** (去中心化)
- 多 agent FSM + Tendermint BFT 共识
- Agent-to-Agent 交易市场 (Mech Marketplace)
- 13M+ A2A transactions
- **关键启发**：agent 间可以雇佣彼此的能力

**Numerai** (信号聚合)
- 千人模型独立提交 signal → meta-model 按历史表现加权
- 不控制子模型、只消费输出
- **关键启发**：对子系统用绩效加权而非硬编排

### 1.3 结论：Aletheia 应选哪种？

**推荐：Pyramid + Ensemble 混合**

理由：
1. 用户明确说"作为我的替代，帮我运营整个系统" → 需要单一最终决策者（Pyramid）
2. 系统已有 7 个专业 agent（market_observer, strategy_designer, paper_trader 等）→ 天然金字塔底层
3. 策略选择是信号竞争问题 → Ensemble 加权更适合（Numerai 模式）
4. 风控必须有单点否决权 → Risk Engine 独立于 PI，但 PI 尊重其裁决

---

## 2. PI Agent 当前 vs 目标

### 2.1 当前状态（Trader 角色）

```
┌─ Scheduler (30min tick) ──────────────────────┐
│                                                │
│  opportunity → trading_context → LLM → action  │
│      │              │                    │      │
│  market_observer  3-layer ctx      pass/open   │
└────────────────────────────────────────────────┘
```

PI 现在做的事：
- 每 30 分钟被唤醒
- 对一个 opportunity 做 pass/open 决策
- 有 reflection loop（记住上轮结果）
- 有 data_health 自愈（今天加的）
- **没有**对系统其他部件的控制力

### 2.2 目标状态（System Operator 角色）

```
┌─ PI Agent (System Operator) ──────────────────────────────┐
│                                                            │
│  ┌──────────┐   ┌──────────────┐   ┌─────────────────┐   │
│  │ Strategy │   │ Parameter    │   │ Universe/Scanner │   │
│  │ Lifecycle│   │ Optimization │   │ Weight Tuning    │   │
│  └─────┬────┘   └──────┬───────┘   └───────┬─────────┘   │
│        │                │                    │             │
│  ┌─────┴────┐   ┌──────┴───────┐   ┌───────┴─────────┐   │
│  │ Capital  │   │ Risk Policy  │   │ System Health &  │   │
│  │Allocation│   │ Adjustment   │   │ Self-Heal        │   │
│  └──────────┘   └──────────────┘   └─────────────────┘   │
│                                                            │
│  ═══════ + 原有 Trading Decision (保留) ═══════            │
└───────────────────────────────────────────────────────────┘
```

---

## 3. PI Agent Operator 六大职能

| # | 职能 | 频率 | 输入 | 输出 |
|---|------|------|------|------|
| 1 | **Strategy Lifecycle** | 日/周 | backtest 结果, trade 表现 | promote/demote/retire 策略 |
| 2 | **Parameter Optimization** | 日 | OHLCV 数据, strategy metrics | 触发 Optuna sweep, 采纳最优参数 |
| 3 | **Scanner/Universe Tuning** | 日 | forward outcomes, regime tag | 调整 scanner 权重, 增减 symbol |
| 4 | **Capital Allocation** | 实时/日 | portfolio PnL, VaR, 策略 Sharpe | 分配 per-strategy sizing cap |
| 5 | **Risk Policy Adaptation** | 日/触发 | drawdown, correlation, kill_switch | 临时收紧/放松 risk params |
| 6 | **System Health** | 持续 | doctor probes, data_health | 触发 backfill/restart/notify |

### 与现有 Trading Decision 的关系

Trading Decision **保留**为 PI Agent 的一项子任务（反应式、每 30min），但 Operator Loop 新增一个独立的**主动式**循环（较低频率 2-6h），负责上述六项。两者共存、互补。

---

## 4. 推荐实施路径

### Stage 1: Operator Loop 骨架 (最小可行)

新增 `aletheia/agent_shell/operator_loop.py`：
- 独立 scheduler job, 每 2h 执行
- 汇总系统状态（现有 doctor + trading metrics）
- 用 LLM 生成 "operational assessment"
- 输出 `OperatorAction` 列表（但 Stage 1 只 log，不执行）

**改动量**：1 新文件 + scheduler 注册 + 少量 context builder 扩展

### Stage 2: Strategy Lifecycle + Parameter Optimization

PI Agent 可以：
- 审查 strategy_records 的最近表现
- 对表现差的策略执行 demote/retire
- 对当前 regime 下无合适策略时，触发 strategy_designer agent
- 对有 RuleSet 的策略触发 Optuna sweep

**依赖**：需要 OHLCV 数据积累（Phase C 已完成）+ 有效策略模板

### Stage 3: Capital Allocation + Scanner Tuning

PI Agent 可以：
- 读取 portfolio 风险指标（VaR, correlation）
- 建议 per-strategy sizing cap 调整（通过 config patch）
- 调整 scanner weight（哪些 feature 权重更高）
- 基于 forward_outcomes 反馈调整 universe membership

### Stage 4: Risk Policy Adaptation

PI Agent 可以：
- 在 drawdown 接近阈值时主动收紧 params
- 在低波动期放宽 spread/entry 条件
- 建议（但不直接执行）kill_switch 操作

> Stage 4 触及 trust boundary (risk/)，需要显式授权后才实施。

---

## 5. 架构决策

### 5.1 单一 PI vs 多个 sub-agent

**决策：单一 PI Agent + 专家委派**

PI Agent 是唯一的全局决策者，但可以委派子任务给专家 agent：
- `strategy_designer` — 生成新策略（已有）
- `post_trade_reviewer` — 事后复盘（已有）
- `risk_critic` — 风险审查（已有）
- `parameter_optimizer` — Optuna 调度（新增，Stage 2）
- `universe_analyst` — scanner 分析（新增，Stage 3）

PI Agent 的 prompt 说"你是系统运营者"，子 agent 的输出是 PI 的输入。

### 5.2 Operator Loop vs Trading Decision 隔离

- Trading Decision = 反应式（有 opportunity → 做判断），保持 30min cadence
- Operator Loop = 主动式（审视全局 → 做调整），2-6h cadence
- 两者共享 context builder 但有独立 prompt template
- Operator Loop 不产生 trade，只产生 system_action（config patch / agent delegation）

### 5.3 Action 执行模型

```
OperatorAction:
  type: "strategy_promote" | "strategy_retire" | "trigger_sweep" |
        "adjust_scanner_weight" | "adjust_sizing_cap" | "request_backfill" |
        "delegate_strategy_design" | "notify_operator"
  params: dict
  requires_approval: bool  # true for trust-boundary changes
```

非 trust-boundary action 自动执行；trust-boundary action 写入 proposal queue 等用户审批。

---

## 6. 与竞品的差异化

| 竞品做法 | Aletheia 做法 | 原因 |
|----------|--------------|------|
| virattt: 13 个平行分析师 | 7 个串行专业 agent | Solo 系统资源有限；串行更可审计 |
| Numerai: meta-model 纯统计加权 | PI Agent LLM 推理 + 统计参考 | LLM 能处理 regime shift 等定性判断 |
| Olas: peer consensus | 单一 PI 决策 | 无需去中心化；用户要的是"替代我" |
| FinRobot: Director 只调度 | PI 既调度又决策 | 系统足够小，不需要调度/决策分离 |
| Almanak: 无 meta agent | 有 PI as meta | 用户要全局优化而非独立策略 |

---

## 7. 立即可做的第一步

**Stage 1 的最小实现**（不触 trust boundary、不改现有行为）：

1. 新建 `aletheia/agent_shell/operator_loop.py`
2. 实现 `run_operator_assessment()`：
   - 汇总：open positions、recent trades、strategy performance、data_health、doctor checks
   - 调 LLM 生成 JSON: `{assessment, recommended_actions[]}`
   - 写入 knowledge_items type="operator_assessment"
3. Scheduler 注册：每 2h 触发
4. 前端：Operator 面板展示最近的 assessment（只读）

这给出了 PI "看到全局 + 有想法" 的能力，但还不执行。执行能力分 Stage 2-4 逐步开放。
