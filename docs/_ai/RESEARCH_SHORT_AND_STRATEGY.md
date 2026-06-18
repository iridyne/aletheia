# 研究报告：做空与策略生命周期架构分析

> 调研日期：2026-06-17  
> 范围：竞品框架（Jesse/Freqtrade/FinRL/TensorTrade）+ Alpha Factory 模式 + LLM 策略生成 + Aletheia 现状对照

---

## 一、核心问题

Aletheia 管道全链路（signal_emitter → ranker → paper_trader → risk → execution）**代码层面已支持 short**，但实际几乎不产出空头信号。根因不在下游（执行/风控），而在**上游的信号生成层缺乏方向性框架**。

更深层的问题：系统没有"策略"的显式生命周期——策略怎么被发现、怎么学习进化、怎么决定什么时候做多/做空/观望，这些都没有明确的架构。

---

## 二、竞品如何解决这些问题

### 2.1 Jesse：做空作为一等公民

Jesse 的 Strategy 基类强制定义对称方法：

```python
class MyStrategy(Strategy):
    def should_long(self) -> bool: ...     # 做多条件
    def should_short(self) -> bool: ...    # 做空条件
    def go_long(self): ...                  # 做多入场参数
    def go_short(self): ...                 # 做空入场参数
    def should_cancel_entry(self) -> bool: ...
```

**关键设计**：
- `should_long()` 和 `should_short()` 是**并列的独立方法**，不是一个方法返回 +1/-1。这迫使策略作者必须显式思考两个方向
- **`qty` 符号即方向**：Position 没有独立方向字段，`qty > 0` = long，`qty < 0` = short。PnL 计算自动感知方向（short 时取反）
- **退出统一**：`broker.reduce_position_at()` 通过 `opposite_side(type_to_side(position.type))` 自动推断对手方向，SL/TP 代码路径多空共享
- **渐进式支持**：`should_short()` 默认返回 `False`，spot 策略零成本忽略；futures 策略才需要实现双向
- 两个方向同时为 True 抛 `ConflictingRules`，框架级防冲突

### 2.2 Freqtrade：四列分离 + ML 预测

从 v1（仅 `buy`/`sell`）演进到 v3（`enter_long`/`enter_short`/`exit_long`/`exit_short`）：
- 方向是信号列的**固有属性**，不是后处理推断
- FreqAI 回归模型输出连续预测值：正值→做多，负值→做空
- `can_short` 开关 + 交易模式校验 = 双重安全门控

### 2.3 FinRL/TensorTrade：市场状态机 + RL 动作空间

- **RL 动作空间**包含负值头寸 = 做空
- **湍流指数**（Turbulence Index）决定什么时候该减仓/观望
- **BSH 方案**（Buy/Sell/Hold）= 方向决策 + 信号强度解耦
- 市场状态 → 可执行策略池的筛选条件

### 2.4 WorldQuant BRAIN：Alpha Factory 模式

核心模式：Alpha = 数学公式，输入→预测→自动回测→通过门槛→部署

策略工厂的层次：
1. 参数空间搜索（固定结构，遍历参数）
2. 结构搜索（指标组合自动评估）
3. 集成筛选（多策略并行，按 Sharpe 筛选）
4. 适应性部署（市场状态驱动策略激活/停用）

---

## 三、Aletheia 现状诊断

### 3.1 信号流现状

```
sync_registry → collect_features → apply_tradability_filters
    → run_all_scanners (4 family: momentum/mean_reversion/volatility/derivatives)
    → allocate_universe
    → emit_deterministic_opportunities(_infer_direction)
    → paper_trader_ranker(score + gates)
    → paper_trader(create proposal)
    → risk_engine(approve/reject)
```

### 3.2 方向决策的问题

`_infer_direction()` 是一个 **after-the-fact 的推断函数**——先选出了"有机会的标的"，然后才问"该做多还是做空"。这是根本性的设计倒置：

- **Scanner 层是方向无关的**：`score_momentum()` 用 `abs(ret_1h)` 打分，不区分涨跌
- **方向决定被塞在 signal_emitter 的一个辅助函数里**：仅用 return 正负 + RSI 高低做粗糙判断
- **没有市场状态上下文**：不知道当前是牛市/熊市/震荡，每个标的独立判断方向

### 3.3 策略的"隐式"存在

当前系统里**没有显式的策略对象**。"策略"散落在：
- Scanner family（机会类型 ≈ 策略类型的代理）
- `_infer_direction()`（方向逻辑硬编码）
- Risk engine 的 lesson 机制（经验学习 ≈ 策略反馈）
- `dreaming_agent`（策略提案 ≈ 策略雏形）

缺乏统一的策略生命周期管理。

---

## 四、做空应该从哪里植入？

### 4.1 不应该做的事

❌ 在 `_infer_direction()` 里调阈值让 short 更容易触发  
❌ 新增一个"空头 scanner family"  
❌ 在 LLM prompt 里鼓励输出 "short"  

这些都是在错误的层面打补丁。

### 4.2 正确的架构层次（由底到顶）

```
┌─────────────────────────────────────────────────────────────────┐
│ Layer 0: Market Regime Detector（市场状态机）                      │
│   输出: Bull / Bear / Sideways / Volatile                        │
│   影响: 全局 long_bias / short_bias 系数                         │
└───────────────────────────┬─────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│ Layer 1: Strategy Pool（策略池）                                   │
│   每个策略有: direction_affinity (long/short/both)                │
│   市场状态 → 激活匹配方向的策略子集                                │
└───────────────────────────┬─────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│ Layer 2: Signal Generation（信号生成）                             │
│   策略自带方向逻辑, 不再由 signal_emitter 后推断                   │
│   每个策略输出: {symbol, direction, confidence, entry_rules}      │
└───────────────────────────┬─────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│ Layer 3: Risk Gate（风控门控）                                     │
│   市场状态 + 组合敞口 → 动态调整多空仓位上限                       │
│   Bear regime: 放宽 short 额度, 收紧 long 额度                   │
└─────────────────────────────────────────────────────────────────┘
```

### 4.3 Layer 0: 市场状态机（最高优先级）

**为什么是起点**：没有市场状态，所有方向判断都是"瞎子摸象"。一个标的 RSI=75 在牛市中可能只是正常回调前的强势，但在熊市中可能是最后一波反弹。

**实现方案**：
```python
class MarketRegime:
    state: Literal["bull", "bear", "sideways", "volatile"]
    confidence: float           # 0-1
    long_bias: float            # 0-1, 多头倾向系数
    short_bias: float           # 0-1, 空头倾向系数
    detected_at: datetime
    
# 检测方法（由简到复杂）：
# Phase 1: 价格 vs MA200 + 趋势斜率 + BTC 主导地位
# Phase 2: + 湍流指数（历史回报协方差矩阵）
# Phase 3: + LLM 宏观解读 + 全市场资金费率均值
```

**嵌入点**：在 `UniverseService.refresh()` 开头执行一次，结果写入 snapshot metadata，下游所有环节可读取。

### 4.4 Layer 1: 策略池（中期目标）

参考 WorldQuant 的 Alpha Factory，Aletheia 需要一个显式的策略生命周期：

```
策略发现 → 回测验证 → 模拟部署 → 绩效监控 → 退化检测 → 退休/进化
```

**与现有架构的对接**：
- `dreaming_agent` 的策略提案 → 策略池的"候选"状态
- `experience_loop` 的绩效反馈 → 策略的"退化检测"
- `scanner_family` → 策略的"信号类型标签"之一

**做空在策略池中的体现**：
- 每个策略声明 `direction_affinity: "long" | "short" | "both"`
- Bear regime 时，系统优先激活 `affinity in ("short", "both")` 的策略
- 策略可以是纯空头的（如"资金费率套利"、"RSI>80 超买做空"）

### 4.5 Layer 2: 信号生成重构

**现状**：方向是 scanner 评分后的附属推断  
**目标**：方向是信号生成的内在属性

参考 Freqtrade 的四列模型，但适配 Aletheia 的 agent 架构：

```python
# 现在的 Opportunity
class Opportunity:
    direction_bias: str  # 后推断出来的

# 重构后的思路
class TradingSignal:
    symbol: str
    direction: Literal["long", "short"]       # 信号固有属性
    strategy_id: str                          # 产生此信号的策略
    regime_context: MarketRegime              # 当时的市场状态
    entry_condition: dict                     # 具体入场条件
    confidence: float
    # 不再有 "neutral" — 没有方向就不产出信号
```

### 4.6 Layer 3: 动态风控

现有 `allow_short_demo=True` 是静态开关。需要升级为动态调整：

```python
# 现在
if proposal.direction == "short" and not risk.allow_short_demo:
    violations.append("short_not_allowed_demo")

# 重构后
regime = get_current_regime()
short_capacity = regime.short_bias * config.max_short_exposure
current_short = portfolio.total_short_exposure()
if proposal.direction == "short" and current_short >= short_capacity:
    violations.append("short_capacity_exhausted")
```

---

## 五、做空策略具体来源

一旦架构就位，以下策略可以植入做空能力：

### 5.1 现有 Scanner 的做空增强

| Scanner | 做空信号 | 当前阈值 | 建议调整 |
|---------|---------|---------|---------|
| momentum | ret_1h < 0 & ret_4h < 0 | 都要同时为负 | Bear regime 下放宽为仅需 ret_1h < -1% |
| mean_reversion | RSI ≥ 70 | 固定 70 | 配合市场状态：Bull 时 80, Bear 时 65 |
| derivatives | funding > 0.005 | 固定 0.5% | 加入 OI 增量方向确认 |
| volatility | ret_1h < 0 | 已支持 | 不变 |

### 5.2 新增空头专属信号源

1. **资金费率套利**（最成熟、风险最低）
   - 触发：funding_rate > 0.1% 持续 3 期以上
   - 方向：做空合约 + 持有现货（delta neutral）
   - 盈利来源：收取资金费

2. **趋势跟踪做空**（Bear regime 专属）
   - 触发：BTC 跌破 MA200 + 该标的跌破 MA50 + 反弹乏力
   - 适用条件：仅在 Bear regime 激活

3. **鲸鱼流入做空**（Phase 2+）
   - 触发：CEX 大额存入检测（需接入链上数据 API）
   - 确认：配合技术面破位信号

### 5.3 LLM 在做空中的角色

**不是**让 LLM 决定方向（当前 market_observer 的问题）。  
**而是**让 LLM 担任：
- **市场状态解读者**：综合新闻/技术面/资金面判断宏观状态
- **策略批评者**：对空头信号的逻辑合理性做二次审查
- **策略进化建议者**：基于历史亏损模式提出策略调整方案

---

## 六、实施路径建议

### Phase 1: 市场状态机（最小可行）
- 新增 `aletheia/regime/detector.py`
- 基于 BTC 价格 vs MA200 + 全市场资金费率均值
- 输出写入 universe_snapshot metadata
- signal_emitter 读取 regime 调整方向阈值
- **预期效果**：Bear 时自然产出更多 short 信号

### Phase 2: 策略池雏形
- 将 scanner_family + direction + 参数 封装为"策略"对象
- 策略有显式的适用 regime 标签
- dreaming_agent 的输出流入策略池
- **预期效果**：系统有了"什么时候该做空"的上下文

### Phase 3: 信号方向性重构
- 参考 Freqtrade 的四列模型
- Opportunity 带上 strategy_id + regime_context
- Risk engine 用 regime 动态调整多空容量
- **预期效果**：做空从"偶尔触发"变为"有计划的策略行为"

---

## 七、关键参考资源

| 项目 | 做空架构亮点 | 借鉴价值 |
|------|------------|---------|
| Jesse | `should_short()` 独立方法 | 做空是一等方法，不是 if-else 分支 |
| Freqtrade | 四列信号 + `can_short` + FreqAI | ML 预测直接含方向 + 安全门控 |
| FinRL | 湍流指数 + RL 负值动作 | 市场状态机的量化实现 |
| TensorTrade | BSH 方案 | 方向与强度解耦 |
| WorldQuant | Alpha Factory 流水线 | 策略自动发现→评估→部署 |
| FinGPT | LoRA 微调金融模型 | LLM 做市场状态分类而非直接决策 |

---

## 八、结论

**做空不是一个"功能点"，而是一个架构层问题。**

正确的植入顺序是：
1. 先知道"现在是什么市场"（Regime Detector）
2. 再知道"这种市场该用什么策略"（Strategy Pool + direction affinity）
3. 最后让策略本身输出带方向的信号（不再后推断）

这三层到位后，做空会作为系统的自然能力出现，而不是人为"调参数让 short 更容易触发"的结果。
