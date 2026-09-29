# Alpha 来源代码审计 — 2026-09-25

- 分析日期：2026-09-25
- Aletheia 基线：master（当日）
- 审计范围：两个宣称的 Alpha 来源的代码实质
- 相关文档：`COMPETITIVE_ANALYSIS_R20_2026-09-24.md`、`R20_MIGRATION_DECISION_2026-09-24.md`
- 分析者：Claude Sonnet 5（代码直读，所有引用均为实测）

---

## 一、结论速览

| Alpha 来源 | 宣称 | 代码实质 | 当前权限状态 |
|---|---|---|---|
| Universe 选币池 | 选出"更值得交易的币" | 真实的方向性假设，有6个 mechanism 打分函数 + 历史净收益校准 | 已接入选币权限；measured 路径需 ≥50 parent clusters 才触发 |
| Price Action / SMC/ICT | 用 SMC/ICT 确认 entry/exit | BOS/CHOCH/FVG 原语已正确实现，**但明确为 observation-only，不参与任何交易决策** | 实际在跑的入场几何是 ATR 突破回踩 + order-flow 确认，是另一套技术 |

**最重要的发现**：第二 alpha 来源存在**名实错位**——代码里的 SMC/ICT 实现是正确的，但 `market_structure_contract.md` 明确锁定了"observation authority only"；实际产生交易信号的是 `momentum_entry_geometry.py` 的 ATR 突破回踩几何，这是一套合法但与 SMC/ICT 语汇不同的技术。

---

## 二、Alpha 来源 #1：Universe 选币池

### 2.1 架构分层

Universe pipeline 分两层，语义完全不同：

**第一层：Hard Gate**（`core/aletheia/universe/filters.py:36-115`）
- 只回答"能不能交易"——volume/spread/depth/freshness/stablecoin/blacklist 七项
- `_compute_tradability_score()`（line 60-115）输出 0-100 可交易性分数
- **无方向信号，不是 alpha 来源**

**第二层：Opportunity Evidence + Economic Utility**（`core/aletheia/universe/continuous_scoring.py`、`economic_utility.py`）
- **真实的方向性假设层**，这才是选币 alpha 的来源

### 2.2 六个 mechanism 打分函数（`continuous_scoring.py`）

每个 mechanism 对应一种独立的市场假说，用 PCHIP 单调曲线（`shape_preserving_curve`, line 171-232）把特征映射成 0-100 分：

| Mechanism | 假说 | 核心输入 | 代码位置 |
|---|---|---|---|
| `momentum` | 价格延续 | rs_beta_adj_24h / return_4h / RSI / OI delta / taker_buy_ratio / funding | `_score_momentum`, line 449 |
| `mean_reversion` | 过度延伸后回归 | RSI (oversold/overbought) / return_4h/24h 幅度 | `_score_mean_reversion`, line 543 |
| `derivatives` | 期货/现货结构背离 | funding_rate / oi_delta_1h / return 方向分歧 | `_score_derivatives`, line 819 |
| `event` | 短期催化剂 | return_1h + taker_buy_ratio + OI + RSI + vol_ratio | `_score_event`, line 924 |
| `short_bias` | 持续下行 | return_4h 跌幅 / return_24h / relative_strength / funding / RSI | `_score_short_bias`, line 1013 |
| `volatility` | 波动率 regime | atr_pct / bb_squeeze / vol_ratio | （同文件内） |

特征全景（`continuous_scoring.py:235-285`）：20 个原始特征，分三组——`price_path`（RSI/return/RS/ATR/BB）、`market_activity_liquidity`（volume/spread/depth）、`positioning_flow`（funding/OI/taker）。

Momentum scorer 公式示例（`_score_momentum`, line 465-539）：
- 核心：`rs_beta_adj_24h` 经 PCHIP 曲线 `((1.5,0),(3.5,20),(4.0,20),(6.0,30))` 映射（±6σ RS → 0-30分）
- 附加：OI delta `_curve(directional * oi, ((0,0),(5,5),(10,10)))`；taker_buy_ratio；vol_ratio
- funding rate 用于**惩罚**高正资金费的多头（`((0.00005,0),(0.00010,-7.5),(0.00020,-15.0))`, line 536）——有实际经济意义

### 2.3 Economic Utility 层：历史净收益校准（`economic_utility.py`）

这是与 R20 最大的架构差异。`economic_utility.py` 把 mechanism 分数转成**实测的、扣成本后 4 小时净收益**估计（docstring line 1: "Transparent 4h cost-after Candidate utility on independent T0 clusters"）。

关键设计：
- **K=50 empirical Bayes shrinkage**（`_SHRINKAGE_K = 50`, line 23）：小样本向 neutral 收缩
- **非重叠 T0 cluster 去相关**（`select_non_overlapping_t0_clusters`, line 101-122）：每 4h 只取一个横截面，避免相邻扫描重复计算
- **tail harm 熔断**（`_TAIL_HARM_FLOOR_PCT = -15.0`, line 24）：实测最差单次损失 ≤ -15% 的 mechanism 直接禁止准入
- **fail-closed 缺失语义**（line 35-61）：`net = None` 时不当 0，不允许"未知 → 乐观零"

准入逻辑（`candidate_selection_utility_contract.md` 第3-5步）：
- `measured` 路径：parent ≥ 50 independent clusters + child ≥ 20 clusters → 用实测 `economic_utility_pct`
- `startup` 路径：`no_history` / `parent_sample_insufficient` / `child_sample_insufficient` → 用 ordinal signal strength（未校准启发式）
- `tail_harm` / `percentile_unavailable` / 完整性故障 → 无论样本多少，**不允许准入**

### 2.4 Verdict — Universe Alpha

**强处**：比竞品（R20 9 币静态手工列表，`env.example:90-92`）更严谨多个数量级。六个方向性假说 + 历史净收益校准 + empirical Bayes + 独立样本定义，是真正的量化 alpha 框架。

**弱处**（合理的当前限制）：`measured` 准入需要大量历史数据；样本不足时走 `startup` 路径，与"有先验理论"的 R20 实质上等价（均未校准）。限制是冷启动期的固有问题，不是设计缺陷。

`candidate_selection_utility_contract.md` 已明确写道（line 87）："Signal strength is an ordinal heuristic, not calibrated expected return or success probability"——文档对当前能力边界是诚实的。

---

## 三、Alpha 来源 #2：Price Action / SMC/ICT

### 3.1 已实现的 SMC/ICT 原语（代码层面正确）

**BOS/CHOCH + Swing 检测**（`core/aletheia/data/market_structure.py`）：
- Fractal swing：对称 K=2 测试（`market_structure.py:361-393`），pivot 点左右各 2 根bar 全部严格更低/更高才确认
- BOS/CHOCH 逻辑（`_apply_break`, line 276-323）：`close > active_high.price + 0.05*ATR14` 触发 BOS（顺势）或 CHOCH（逆势），ATR 容差防假突破
- 状态机（`_structure_state`, line 250-259）：HH+HL → bullish, LL+LH → bearish, 否则 range
- 已知时刻（`known_at_ms`）是确定因果的市场数据时间，不是计算时间（contract 明确，line 54-56）

**FVG（Fair Value Gap）**（`core/aletheia/data/smc_enrichment.py:20-76`）：
- 三根K线算法：`previous.high < third.low` → bullish FVG（`smc_enrichment.py:34-36`）
- 含 mitigation 追踪（line 44-52）：记录 FVG 被回填的时间
- 代码注释明确引用开源参考（line 18-19）：`joshyattridge/smart-money-concepts commit 1b62fd6c`

这些**不是"挂名 SMC"的通用指标**，是正确实现的市场结构几何原语。

### 3.2 关键：这些原语没有交易权限

`docs/design/contracts/market_structure_contract.md` 明确写道（line 6-9）：

> "This domain is a sibling of technical analysis and has **observation authority only**. It does not authorize Candidate admission, Trigger thresholds, Risk, execution, or Learning behavior."

更直接的（line 71-73）：

> "`HARD_GATE_ALLOWLIST` remains empty. Market structure alone **cannot block** Opportunity materialization, Risk approval, or execution."

已知缺口（line 99-103）：

> "Known gaps: There is no current behavior-authority, durable event ledger, incremental service, multi-timeframe aggregation, **Order Block/Liquidity model, or Alpha claim**. Those require separate approved work with a concrete consumer."

总结：BOS/CHOCH/FVG 是纯观察层，任何使用都不影响入场/出场决策。

### 3.3 实际在跑的入场/出场几何

真正产生交易信号的是 `momentum_entry_geometry.py` + `momentum_trigger.py`。

**几何编译**（`compile_momentum_entry_geometry`, `momentum_entry_geometry.py:352-501`）：
- 取最近 20 根 15m K 线的最高价（多）/最低价（空）作为 breakout level（line 397-401）
- 第 21 根 bar 收盘确认突破（line 403-408）
- ATR 参数：`PULLBACK_ATR = 0.25`（回踩带宽度）、`INVALIDATION_ATR = 1.0`（失效距离）（line 22-28）
- 入场区间（entry_zone）= breakout ± 0.25 ATR（line 412-422）
- 止损 = breakout - 1.0 ATR；目标 = entry + minimum_risk_reward × risk（line 416-425）

**微观确认状态机**（`MomentumTriggerEngine`，`momentum_trigger.py:68-366`）：
```
ARMED → PULLBACK（价格回到 breakout 带内）
      → SWEEP（价格短暂跌破/突破 breakout level）
      → RECLAIM（价格重新收回 breakout 上/下方 ≥ 300ms）
      → TRIGGERED（最终入场条件）
```
入场最终门禁（`evaluate`, line 262-316）：quote freshness ≤ 1000ms + depth ≥ `min_depth_10bps_usd` + taker buy ratio ≥ 0.60（多）/ ≤ 0.40（空）（`trigger_specs.py:25-26`）。

这是**"突破-回踩-order flow 确认"技术**，是合法的价格行为方法，但与 SMC/ICT 语汇（order block / liquidity pool / premium-discount zone / inducement）不同。

### 3.4 Verdict — SMC/ICT Alpha Source

**优先说清楚**：存在**名实错位**。
- "用 SMC/ICT 确认 entry/exit" 作为产品描述，**目前不对应运行时事实**
- 实际产生交易信号的是 ATR 突破回踩几何（momentum_entry_geometry/trigger），是有效的但不同的技术
- SMC/ICT 原语（BOS/CHOCH/FVG）实现正确，但被 contract 锁在观察层，不参与任何决策

**当前状态**：两套能力共存，各自有正当性：
1. ATR breakout-retest geometry → 已接入执行权限，是当前实际 alpha 来源
2. SMC/ICT 结构几何 → 已实现，observation-only，等待 D1/D2 提案接入权限

**与 R20 对比**：R20 没有任何 swing/BOS/CHOCH/FVG 实现，其"price action"是 RSI/KDJ/ADX 等指标换皮。Aletheia 在这一侧技术深度已经超过 R20，且部分能力处于闲置状态。

---

## 四、R20 对比摘要（本次分析新增视角）

本节补充昨日 `COMPETITIVE_ANALYSIS_R20_2026-09-24.md` 未覆盖的**代码层 alpha 技术对比**（昨日侧重开发效率和数据度量）。

| 维度 | Aletheia | R20 |
|---|---|---|
| 选币算法 | 6个 mechanism，PCHIP 曲线，历史净收益校准，empirical Bayes | 手工维护 9 币静态列表（`env.example:90-92`），无算法 |
| 延续信号 | `_score_momentum`：beta-adj RS + funding + OI + taker | ADX + RSI 固定门槛（`scoring.py`，ADX ≥22，RSI ≥50）|
| 反转信号 | `_score_mean_reversion`：RSI 极值 + return 幅度 PCHIP | RSI/KDJ 联合条件（`rsi<=45 AND kdj_j<=40`）|
| 期货结构信号 | `_score_derivatives`：funding/OI 方向背离 | Pillar 6 "calculus"：EMA(log-price) 的一二三阶差分，本质是 ROC/MACD 换名 |
| 概率估计 | `economic_utility_pct`：实测净收益 + empirical Bayes | `continuation_prob_pct = Φ(0.6v+0.4a)`：确定性 CDF 变换，未校准，不是真概率 |
| 市场结构几何 | 正确实现 BOS/CHOCH/FVG（`market_structure.py`，`smc_enrichment.py`）| 无任何 swing/结构实现 |
| 入场几何 | ATR breakout-retest + ARMED→TRIGGERED 状态机 + order flow 确认 | LLM 投委会直接输出 entry_price/stop/target（council 是主要信号源，非过滤层）|
| 反事实度量 | 有（`universe_forward_outcomes` + 独立单元定义） | 无（`R20_MIGRATION_DECISION_2026-09-24.md` §一 已详述） |
| Backtest 覆盖 | 前瞻账本覆盖全候选（含未成交），独立单元去相关 | `backtest_engine.py` 测的是 SMA(5)/SMA(15)，不测实盘信号 |

R20 的 "因果微积分"（velocity/acceleration/jerk = EMA(log-price) 一二三阶差分）和 Aletheia 的 Momentum scorer 比较：两者都主要是动量信号，但 Aletheia 的 PCHIP 曲线形状包含了更多非线性（如 funding_rate 对高正费率多头的惩罚），且与历史净收益挂钩，而 R20 的阈值是固定的未优化常数（`calculus/__init__.py` 注释："business semantics, not tunable parameters"）。

---

## 五、本文未覆盖的

- SMC/ICT 接入执行权限的可行性评估和 D1/D2 设计（属于 Proposal，不属于本 Evidence 文档）
- jerk/deviation_area 阻尼因子引入 ATR 几何的验证（需要历史回放，未进行）
- Fedora 运行时的实测 alpha 数字（参见 `ALPHA_MEASURABILITY_2026-09-24.md`）
- `momentum` mechanism cohort 的实际 `measured` vs `startup` 比例（需查 Fedora DB）

---

*报告日期：2026-09-25。所有代码引用为 master 当日状态，可通过对应文件:行号复现。*
