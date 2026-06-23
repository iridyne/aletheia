# 实验改动积压(Experiment Backlog)

> 观察期内发现的改进点记录在此。每轮结束后**只取一个**进入下一轮。
> 不要在观察期内实施任何条目。这不是 TODO,是"如果需要 Round N+1,该改什么"的预排序队列。

---

## 优先级排序(P1 = 下一轮首选)

### P1 — 加价格结构上下文

**诊断**: AI 当前只看到数字(RSI、return%、funding),看不到价格结构(支撑阻力、形态、均线位置)。人类相机交易员 80% 决策靠"看图"——前低支撑、double bottom、200MA 测试。AI 等于闭眼听数。

**假设**: ETH short 赚钱是因为趋势 regime 里纯数字够用;其他方向亏是因为震荡/反转点需要价格结构判断,AI 缺这个信息。

**具体改动**:
- 在 `trading_context.py` Layer 1 features 中加入:
  - 关键支撑/阻力位(近期 swing high/low)
  - 当前价格相对于 EMA20/50/200 的位置
  - 近 N 根 K 线的价格形态标签(如有)
- 预估工作量: 中等(需要写特征提取逻辑)
- 影响: 直接改变 AI 看到的信息 → 消耗轮间改动配额

**验证标准**: Round 2 vs Round 1 的 PF 差异 > 0.15,或非 ETH-short 方向 WR 提升 > 5pp

---

### P2 — 收窄到已验证方向

**诊断**: 如果 Round 1 确认只有 ETH short 有 edge,不需要改 AI 能力,只需要限制它的行动范围。

**具体改动**:
- 在 PI Agent 决策层加 allowlist:只允许在被验证有正期望的 symbol+direction 上开单
- 其他方向只能 "watch",不能 "trade"
- 预估工作量: 小

---

### P3 — 加 orderbook 微结构

**诊断**: 不知道买卖挂单分布、大单 wall。对于短线相机决策是关键信息。

**具体改动**:
- 通过 CCXT 获取 L2 orderbook snapshot
- 计算 bid/ask imbalance、spread、近价位大单
- 注入 Layer 1 features
- 预估工作量: 中(需要新的 data pipeline)

---

### P4 — 多时间框架共振

**诊断**: 只有 1h/4h/24h 动量数字,没有"周线趋势+日线回调+4h 反转"这种 MTF 共振判断。

**具体改动**:
- 在 features 中加入 weekly/daily trend 标签(above/below MA, higher high or lower low)
- 让 AI 能做 "高时间框架方向 + 低时间框架入场" 的判断
- 预估工作量: 中

---

### P5 — Market Observer 洞察力增强 + Confidence 动态校准

**诊断 (三层问题)**:

1. **Score 聚类退化**: `confidence_score` 高度聚类(62×2729, 68×1228, 78×900)。LLM 缺乏 anchoring 和 outcome feedback,"62" 退化为 "medium" 的数字别名,失去连续区分力。
2. **洞察信息断流**: Market Observer 产出的 `key_observations`(结构化市场证据)在 ranker 和 operator_loop 中**零消费**。`thesis` 被截断到 150 字符后传给下游 LLM,大量分析细节丢失。
3. **无动态适应**: scoring 只有 categorical gate (3 档位),不能按 family/symbol/regime 细粒度调节。

**核心矛盾**: 既要能动起来(适应不同标的/行情风格),又要有明确规则(不随市场沉浮丧失判断力)。

---

**设计方案分两部分:**

#### Part A: Market Observer 洞察力增强 (让产出真正有用)

**现状数据流**:
```
Market Observer → opportunities.thesis (截断150字) → trading_context → LLM
               → opportunities.key_observations → DB (无人消费)
               → opportunities.confidence_score → trade_score (直接相加,无校准)
```

**目标数据流**:
```
Market Observer → observations → 结构化 signal_tags (机器可消费)
               → confidence_score → × calibration_multiplier → calibrated_score
               → thesis → 完整传入 trading_decision context (不截断)
               → signal_tags → ranker 加分/扣分 + guard 触发
```

**具体改动 1 — 从 observations 提取 signal_tags** (新增 `aletheia/agents/observation_parser.py` ~80行):

```python
"""将 LLM 的自然语言 observations 解析为结构化 signal tags。

不用 LLM,用规则匹配。observations 格式已半结构化(含数字引用),正则够用且零延迟零成本。
"""

SIGNAL_TAGS = {
    "mtf_agreement": "多时间框架方向一致",
    "oversold_bounce": "RSI < 30 + 正 momentum = 超卖反弹",
    "overbought_reversal": "RSI > 70 + 负 momentum = 超买回落",
    "trend_continuation": "价格 > SMA50 + RSI 50-65 + 正 momentum",
    "breakdown": "价格 < SMA50 + RSI < 45 + 负 momentum",
    "volume_surge": "volume z-score > 2.0",
    "volume_dry": "volume z-score < -1.0 (缩量=假突破风险)",
    "bollinger_squeeze": "BB% 集中于 0.4-0.6 (盘整)",
    "bollinger_breakout": "BB% > 0.9 或 < 0.1 (波动突破)",
    "momentum_divergence": "价格新高/低 但 RSI 未确认",
}

def extract_signal_tags(observations: list[str], features: dict) -> list[str]:
    """从 observations + features snapshot 中提取信号标签。

    核心逻辑: 基于 features 数值做规则判定(不依赖 observations 文本解析)。
    observations 仅用于验证 — 如果 features 说 RSI<30 但 observations 没提,
    仍然打 tag(features 是 ground truth,observations 可能遗漏)。

    Returns: 0-5 个 signal_tag 列表
    """
    tags = []

    # Rule 1: MTF agreement — 1h/4h/1d momentum 同向
    mom_1h = features.get("momentum_1h", 0)
    mom_4h = features.get("momentum_4h", 0)
    mom_1d = features.get("momentum_1d", 0)
    signs = [mom_1h > 0, mom_4h > 0, mom_1d > 0]
    if all(signs) or not any(signs):
        tags.append("mtf_agreement")

    # Rule 2: Oversold bounce (仅对 long 有意义)
    rsi_1h = features.get("rsi_1h", 50)
    if rsi_1h < 30 and mom_1h > 0:
        tags.append("oversold_bounce")

    # Rule 3: Overbought reversal (仅对 short 有意义)
    if rsi_1h > 70 and mom_1h < 0:
        tags.append("overbought_reversal")

    # Rule 4: Volume
    vol_z = features.get("volume_z_score", 0)
    if vol_z > 2.0:
        tags.append("volume_surge")
    elif vol_z < -1.0:
        tags.append("volume_dry")

    # Rule 5: Bollinger
    bb_pct = features.get("bb_pct_1h", 0.5)
    if bb_pct > 0.9 or bb_pct < 0.1:
        tags.append("bollinger_breakout")
    elif 0.35 < bb_pct < 0.65:
        tags.append("bollinger_squeeze")

    # Rule 6: Trend structure
    price = features.get("close", 0)
    sma50 = features.get("sma50_1h", 0)
    if price and sma50:
        if price > sma50 and 50 < rsi_1h < 65 and mom_1h > 0:
            tags.append("trend_continuation")
        elif price < sma50 and rsi_1h < 45 and mom_1h < 0:
            tags.append("breakdown")

    return tags[:5]
```

**具体改动 2 — signal_tags 影响 trade_score**:

修改 `aletheia/agents/paper_trader_ranker.py` 的 `_compute_trade_score()`,
在 regime scoring 之后、risk flags 之前插入:

```python
# Signal tag scoring — 结构化市场证据对 trade_score 的加减分
signal_tags = opp.get("signal_tags") or []
if isinstance(signal_tags, str):
    signal_tags = json.loads(signal_tags) if signal_tags else []

_TAG_SCORES = {
    "mtf_agreement": +12,       # 多时间框架共振 = 最强确认信号
    "trend_continuation": +8,   # 顺趋势入场
    "oversold_bounce": +6,      # 超卖反弹 (仅 long 方向生效)
    "overbought_reversal": +6,  # 超买回落 (仅 short 方向生效)
    "volume_surge": +5,         # 放量确认方向
    "bollinger_breakout": +4,   # 波动突破
    "volume_dry": -8,           # 缩量 = 假突破/无力延续风险
    "bollinger_squeeze": -3,    # 盘整 = 方向不明
    "momentum_divergence": -10, # 背离 = 反转信号,做多/做空都危险
    "breakdown": +5,            # 仅对 short 方向 (在方向过滤后)
}

for tag in signal_tags:
    if tag == "oversold_bounce" and direction != "long":
        continue
    if tag == "overbought_reversal" and direction != "short":
        continue
    if tag == "breakdown" and direction != "short":
        continue
    if tag == "trend_continuation" and direction != "long":
        continue
    score += _TAG_SCORES.get(tag, 0)
```

**具体改动 3 — thesis 传入下游不截断**:

修改 `aletheia/agent_shell/trading_context.py:155`:
```python
# 旧: "thesis": (o.get("thesis") or "")[:150],
# 新:
"thesis": (o.get("thesis") or "")[:400],
"signal_tags": json.loads(o.get("signal_tags") or "[]") if o.get("signal_tags") else [],
```

---

#### Part B: Confidence Score 动态校准 (让分数能动但有规则)

**校准公式**:
```
calibrated_score = raw_score × family_multiplier × regime_multiplier
    (clamped to [20, 95])
```

**新增 `aletheia/universe/score_calibration.py`** (~120行):

```python
"""Post-LLM confidence score calibration layer.

设计原则:
1. 规则约束: multiplier ∈ [0.7, 1.3], 日变动 ≤ 5%
2. 适应性: 基于 outcome 历史(family_scorecard) 自动调整
3. 不随波逐流: 回归弹力 + 最低样本量 + 静态 regime 先验
"""

_MULTIPLIER_FLOOR = 0.7
_MULTIPLIER_CEILING = 1.3
_DAILY_MAX_CHANGE = 0.05   # 每日最多变动 ±5%
_DECAY_RATE = 0.02          # 每日向 1.0 衰减 2%
_MIN_SAMPLE_SIZE = 10       # 不够样本不调

# 静态 regime 先验 (写死不学, 防漂移)
_REGIME_PRIOR = {
    "trending_up":   {"momentum": 1.10, "mean_reversion": 0.90, "volatility": 1.00},
    "trending_down": {"momentum": 1.10, "mean_reversion": 0.90, "volatility": 1.05},
    "ranging":       {"momentum": 0.90, "mean_reversion": 1.10, "volatility": 0.95},
}


def calibrate_score(raw_score, family=None, regime=None) -> float:
    """Apply calibration. Returns clamped [20, 95]."""
    score = float(raw_score or 50)
    if family:
        state = _get_state(family)
        score *= state.multiplier
    if regime and family:
        score *= _REGIME_PRIOR.get(regime, {}).get(family, 1.0)
    return max(20.0, min(95.0, round(score, 1)))


def refresh_multipliers() -> dict[str, float]:
    """每日从 family_scorecard 重算 multipliers。由 scheduler nightly job 调用。"""
    from aletheia.analytics.family_scorecard import build_family_scorecard
    scorecard = build_family_scorecard(days=30)
    results = {}
    for entry in scorecard:
        family = entry.get("family")
        if not family:
            continue
        trade_count = entry.get("trade_count", 0)
        if trade_count < _MIN_SAMPLE_SIZE:
            results[family] = 1.0
            continue
        win_rate = entry.get("win_rate", 0.5)
        pf = entry.get("profit_factor", 1.0)
        # WR component: 50% WR = 1.0, 每偏离 10pp = ±0.1
        wr_comp = 1.0 + (win_rate - 0.5) * 1.0
        # PF component: PF=1.0 = 1.0, 每偏离 0.5 = ±0.1
        pf_comp = 1.0 + (min(pf, 3.0) - 1.0) * 0.2
        target = max(_MULTIPLIER_FLOOR, min(_MULTIPLIER_CEILING, (wr_comp + pf_comp) / 2.0))
        # Smooth: 回归弹力 + 日变动限
        state = _get_state(family)
        current = state.multiplier + (1.0 - state.multiplier) * _DECAY_RATE
        delta = max(-_DAILY_MAX_CHANGE, min(_DAILY_MAX_CHANGE, target - current))
        state.multiplier = max(_MULTIPLIER_FLOOR, min(_MULTIPLIER_CEILING, round(current + delta, 4)))
        state.sample_count = trade_count
        results[family] = state.multiplier
    return results
```

**Operator Loop 集成**:

修改 `aletheia/agent_shell/operator_loop.py` PARAM_REGISTRY 新增:
```python
"min_calibrated_score": ParamSpec(
    "min_calibrated_score", "trading",
    neutral=60, tighten_direction="up",
    floor=45, ceiling=80, step=5, ladder=None),
```

Ranker 新增 numeric gate (在 categorical confidence gate 之后):
```python
from aletheia.universe.score_calibration import calibrate_score
cal_score = calibrate_score(
    opp.get("confidence_score"),
    family=opp.get("scanner_family"),
    regime=regime_tag,
)
min_cal = getattr(cfg.trading, "min_calibrated_score", 60)
if cal_score < min_cal:
    return _make_candidate(opp, skip_reason=f"calibrated_score_{cal_score:.0f}<{min_cal}")
```

Operator loop 自调节:
- tighten: 7d calibrated bucket WR < 35% → `min_calibrated_score += 5`
- loosen: 无交易 ≥ 12h + 已 tightened + WR OK → `min_calibrated_score -= 5` (不低于 neutral)
- recalibrate: scheduler nightly 03:00 UTC → `refresh_multipliers()`

---

#### 集成全景 (改动文件汇总)

| 文件 | 改动类型 | 内容 |
|------|----------|------|
| `aletheia/agents/observation_parser.py` | 新增 | signal_tags 规则提取器 (~80行) |
| `aletheia/universe/score_calibration.py` | 新增 | calibration 层 (~120行) |
| `aletheia/agents/market_observer.py` | 修改 | 写入 opportunity 时调用 observation_parser |
| `aletheia/agents/paper_trader_ranker.py` | 修改 | signal_tags scoring + calibrated_score gate |
| `aletheia/agent_shell/trading_context.py` | 修改 | thesis 扩到 400 字 + 传入 signal_tags |
| `aletheia/agent_shell/operator_loop.py` | 修改 | 新增 min_calibrated_score param |
| `aletheia/agent_runtime/scheduler.py` | 修改 | nightly 调用 refresh_multipliers() |
| `aletheia/config/schema.py` | 修改 | 新增 min_calibrated_score 字段 |
| `aletheia/storage/models.py` | 修改 | Opportunity 新增 signal_tags 字段 |
| `aletheia/storage/migrations.py` | 修改 | 加列 signal_tags TEXT |

**冻结边界判断**: `trading_context.py` 改的是传递格式(不改决策逻辑); `market_observer.py` 只在 observations 写入 DB 后追加一个纯规则提取步骤(不改 LLM prompt 也不改解析逻辑)。严格来说这不改变"AI 看到什么信息"或"AI 怎么做决策",但 `paper_trader_ranker.py` 的 signal_tags scoring 会影响"哪些 opportunity 被选中执行"——这需要在 Round 1 结束后才能实施。

---

**防漂移机制 (5 道护栏)**:
1. **Multiplier 硬边界 [0.7, 1.3]** — 62 分最多变成 43 或 81
2. **日变动限 5%** — 一次爆亏不会立刻压死整个 family
3. **回归弹力 (2%/day toward 1.0)** — 极端偏移自然衰减
4. **最低样本 10 笔** — 不会被偶然结果带偏
5. **Regime multiplier 是硬编码先验** — 不从数据学, 不被误导

**验证标准**:
- calibrated_score std > raw_score std × 1.5
- 高 calibrated bucket (≥70) WR 比整体 WR 高 ≥ 10pp
- signal_tags 覆盖率: ≥ 80% opportunities 有至少 1 个 tag
- Kill: calibrated WR 与 raw 无统计差异 (p > 0.1) → 回退

**工作量**: 2-3 天。Part A (signal_tags) 可独立于 Part B 单独上线。

---

## 观察期内新发现(随时追加)

_Round 1 进行中遇到的新想法记在这里,不实施。_

---

## 已消耗

| 轮次 | 使用的改动 | 来源 |
|------|-----------|------|
| Round 1 | (无,v5 原版) | — |
| Round 2 | _待定_ | — |
| Round 3 | _待定_ | — |
