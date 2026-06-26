# 实验改动积压(Experiment Backlog)

> 观察期内发现的改进点记录在此。每轮结束后**只取一个**进入下一轮。
> 不要在观察期内实施任何条目。这不是 TODO,是"如果需要 Round N+1,该改什么"的预排序队列。

---

## ⭐ Round 2 启动决策(2026-06-26 最新证据排序 — 当 Round 1 收盘后读这里)

> **触发条件**: Round 1 跑满 14 天(→ 2026-07-06)或正式判 Kill 后,从下方**恰好选一个**
> 改动进 Round 2。下面的排序已纳入 6-23 + 6-26 干净窗实证,**取代**旧的"P1=首选"表述。
> 选定后:① 实施该项 ② 打 tag `experiment/round-2-start` ③ 在文末"已消耗"表登记 ④ 其余项保留。

**当前证据排序**(高→低):

1. **P2 — 收窄/约束方向(最强候选)**: 双实证支撑——6-23"PI 用组合平庸否决单 edge";
   6-26 干净窗"short 17T/long 6T 一边倒撞涨被集体止损"。直接对应协议 G2(允许窄域 edge)。
   工作量小、风险低。**默认首选**。
2. **P1 — 加价格结构上下文**: 信息价值高(支撑阻力/均线/形态),但工作量中等、无直接实证。
3. **P8 — Trading Session 注入**: ⚙️**已建好休眠件 + 测试(commit `c8780b3`),解冻接线仅一行**。
   但 6-26 实证**本样本不支持**(亏损在高流动性 overlap 窗,非亚盘)→ 成本最低但优先级低,
   留作后续轮次或专门验证"时段"单变量时用。
4. P3–P7 / 观察期新发现:见下方各条,信息价值/工作量各异,暂列后。

> **铁律**: 每轮只取一个;P1–P8 互斥竞争同一配额。不要一次塞多个。

---

## 候选明细(诊断 + 设计 + 接线)

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

**2026-06-23 实证强化(profit-loop 修复后实测)**:
修复 8 个 context-injection bug 后,PI Agent 拿到准确数据,但连续 6+ 轮仍主动 skip。
关键观察:LLM 面对它**历史最赚钱的交易**仍拒绝开仓——
- 当前最强信号 = ETH/USDT short(score 125.28),regime=bear 88.9%(支持做空),
  per_symbol 显示 **ETH short 43T 21W +19.66**(唯一稳定盈利方向)
- 但 LLM skip,理由 ="整体 46% 胜率(23W/27L) + structural headwinds + signal conflict"
- **机制发现:PI Agent 用组合层面的平庸表现(46% 总胜率)否决单个最优 edge(ETH short 正期望)**

这把 P2 从"如果确认只有 ETH short 有 edge"升级为**已实测确认的行为缺陷**:
当前 harness 下 PI Agent 不会单独押注分品种 edge,而是被组合表现拖住。
这直接对应协议 G2(最优子集 PF≥1.5 即可 Go,允许窄域 edge)——
**系统有 edge(ETH short),但 AI 的决策机制看不到/不押注它**。

P2 的 allowlist 是对症;更根本的修法是让 PI Agent 的 prompt 能识别"分品种 edge"
而非只看组合 aggregate。两者都是 Round 2 候选,P2 工作量更小、风险更低,优先。

**2026-06-26 干净窗实证再强化(P2 当前最强候选)**:
Round 1 干净窗(6-25 15:22 UTC 后,新 PI 引擎真实样本)23 笔:WR 13%、PF 0.063
(排除 reconciled_gone 后 14 笔 WR 7.1%、PF 0.005)。**注意 doctor 的混合 PF=0.769
是被 6/9-6/21 旧版盈利 ETH-short 数据粉饰的假象**,新引擎真实表现远差。
失效模式高度集中于**方向**:**short 17T / long 6T**,空头一边倒撞上一波上涨被集体止损
(ETH short -24.92 + XRP short -7.22 = 全部亏损 68%)。这正是 P2 描述的反面——不是
"否决单 edge",而是"铺一个全相关的一边倒空头书"。两者同根:PI 缺乏组合层面的
方向/相关性约束。**判决性质**:样本仅 23 笔/~1 天,K4(回撤)未触发($-47/$88k demo
= 0.05%),不构成提前 Kill,是强负面先行信号。Round 1 应跑满 14 天再判。

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

### Round 2 必修: paper_trader_ranker evidence query 硬编码 paper_internal

**文件**: `aletheia/agents/paper_trader_ranker.py:723`
**问题**: `WHERE execution_mode = 'paper_internal'` 忽略 bitget_demo/okx_demo 等所有其他 demo 模式的交易证据
**影响**: 策略在 OKX demo 上跑了 20 笔但 evidence = 0 → 被分类为"untested" → ranker 给惩罚分
**修复**: 改为 `WHERE execution_mode IN ('paper_internal','bitget_demo','binance_testnet','okx_demo')`
**为什么现在不修**: 文件在 EXPERIMENT FREEZE 冻结区内，改动会影响 AI 决策（evidence score 变化 → trade_score 变化）

### Round 2 必修: trading_context.py today_pnl 字段名歧义

**文件**: `aletheia/agent_shell/trading_context.py:75`
**问题**: 字段名 `today_pnl` 存的是 realized-only，但 /api/portfolio 的 `today_pnl` 是 realized+unrealized
**影响**: Agent 被告知的"今日 PnL"与用户在 Dashboard 看到的数字语义不同
**修复**: 字段改名为 `today_realized_pnl` 并在 prompt 中标注清楚
**为什么现在不修**: 文件在 EXPERIMENT FREEZE 冻结区内

### Round 2 候选: KL-3 — Layer 3 wisdom 类型不匹配(PI Agent 永远拿不到 lessons)

**文件**: `aletheia/agent_shell/trading_context.py:367`
**问题**: `_build_layer3_wisdom` 过滤 `item_type in ('lesson','accepted_lesson')`，但无任何 producer 写这两种类型 → PI Agent 的 Layer 3 wisdom 永远是空列表
**修复**: 把类型过滤扩展到实际有 producer 的类型(`post_trade_review`/`trade_case_study`)，补 E2E spec
**为什么现在不修**: 冻结区(改变 AI 看到的历史经验 → 改决策)。SDD 审计 §16.3 确认

### Round 2 候选: EQUITY §6.3 — equity_observer KB 写回缺失(§15.4)

**文件**: `aletheia/agents/equity_observer.py`
**问题**: 设计要求 confidence>0.7 时回写 research_note 到 KB，代码无此路径 → equity 学习既不写也不读 KB
**修复**: 加 KB 写回路径
**为什么现在不修**: 写 KB 会改变 ranker/PI 检索内容 → 改 AI 决策(实质冻结)。SDD 审计 §15.4 确认

### Round 2 候选: PI_OPERATOR 连续3次同向升级规则(§15.5)

**文件**: `aletheia/agent_shell/operator_loop.py`
**问题**: 设计规定连续 3 次同向调参应自动升级至 Level 2 人工确认，无此 direction-counting 逻辑
**修复**: 加方向计数 + 升级触发
**为什么现在不修**: 改 operator 自治调参升级行为 → 改 AI 决策路径(实质冻结)。SDD 审计 §15.5 确认

> **PI 组合否决单 edge(见上方 P2 实证强化)是本批次最高价值的 Round 2 候选** —
> 修好全部 context-injection bug 后,PI 仍因 46% 组合胜率否决 ETH short(历史最优 edge)。
> 这是当前 harness 的核心策略缺陷,直接对应协议 G2(允许窄域 edge)。

---

### P6 — Agent 自主选择 Spot / Perp

**诊断**: `trading_decision.py:244` 硬编码 `market_type="usdt_futures"`，OKXClient `defaultType: "swap"` 也固定为永续合约。系统架构上已支持 spot + usdt_futures 两条路径（config `enabled_markets`、risk engine 分流检查、Bitget client spot 分支均就绪），但决策层只输出一种。Agent 无法根据场景选择现货（无爆仓风险、适合长持）或永续（可做空、适合短线）。

**具体改动**:
- `trading_decision.py`: PI 决策结构体新增 `market_type` 字段,LLM 输出 spot/usdt_futures
- OKXClient: 增加 spot 下单能力(CCXT `defaultType` per-order 或 spot client 分支)
- Position reconciler: adopt 时根据交易所 instType 动态设 market_type(非硬编码)
- 预估工作量: 中(决策层 + 执行层 + reconciler,三处联动)
- 影响: 直接改变 AI 决策输出 → 消耗轮间改动配额

**前置条件**: Round 1 结束解冻后方可实施。

---

### P7 — News 信号注入 PI Agent Trading Context

**诊断**: RSS news 自动抓取 + LLM sentiment 分析已实现（`news_collector.py` + `news_analyzer.py`），Serenity 研究管道已注入新闻。但 PI Agent 的 `trading_context.py` Layer 1 尚未消费新闻信号——AI 做交易决策时看不到实时新闻上下文。

**具体改动**:
- `trading_context.py` `_build_layer1_snapshot()` 新增 `layer["news_signals"]`
- 只取 `relevance_score >= 0.7` + 包含当前持仓/候选 symbol 的新闻（限 5 条）
- `_build_assessment_prompt()` 渲染为 `## Recent News Context` section
- 预估工作量: 小（~20 行，数据管道已就绪）
- 影响: 直接改变 AI 看到的信息 → 消耗轮间改动配额

**前置条件**: Round 1 结束解冻后方可实施。需确认 news_analyzer 输出质量稳定。

---

### P8 — Trading Session(全球流动性时段)注入 PI Agent Context

**提出**: 2026-06-26(用户)。**状态**: 休眠件已建好 + 测试通过,等 Round 1 解冻一键接线。

**诊断**: PI Agent 当前对"现在是几点、哪个时段"完全无感——同一组 RSI/funding/momentum
数字,无论出现在亚盘凌晨(美欧休市、盘薄、易震荡假突破)还是 EU/US overlap(峰值流动性、
突破最有延续性),AI 看到的输入完全一样,因此决策也一样。但加密 24/7 的流动性/波动率
其实严格跟随 TradFi 时段钟摆(机构盘 + BTC↔美股相关性 + 美国宏观数据都集中在伦敦/纽约盘)。

**假设**: 当前 Round 1 的部分亏损来自"在错误时段做对方向"——例如亚盘追突破(无量延续)、
late-US/周末薄盘被插针止损。给 AI 注入时段事实后,它能据此自适应:亚盘缩量/提高 conviction
门槛/偏均值回归,overlap/US 盘才放开追趋势。这正是用户的原话场景("当前是亚盘时间,
美股流动性低,要针对性处理")。

**冻结边界**: 命中冻结判据正中(改变 AI 看到的信息 → 改决策)。**消耗轮间唯一改动配额**。
属协议第 113 行明文合法改动("给 AI 新的数据维度")。**非 bug,走不了冻结例外通道**,
必须等 Round 1 收盘。

**已就绪(冻结安全,2026-06-26 建)**:
- `aletheia/trading/market_session.py`(非冻结纯函数,**当前零行为影响**——没有任何冻结文件
  import 它):`classify_session(now_utc) -> SessionInfo` + `render_for_prompt()`。
  时段划分(UTC,weekday): 00–07 asian(low) / 07–13 european(medium) / 13–16 overlap(high) /
  16–21 us(high) / 21–24 late_us(low) / 周末 weekend(low);`us_equities_open` 精确到
  NYSE 14:30–21:00 UTC(对齐 equity_observer 调度门)。
- `tests/test_market_session.py`:13 条单测全过(边界半开区间、NYSE 开闭分秒、周末覆盖、
  naive datetime 当 UTC、render 紧凑性)。

**Round 2 接线(仅当解冻,改 2 个冻结文件,各一处)**:

1. `agent_shell/trading_context.py` `_build_layer1_snapshot()` 末尾加:
   ```python
   from aletheia.trading.market_session import classify_session
   s = classify_session()
   layer["session"] = {
       "label": s.label, "name": s.name, "liquidity": s.liquidity,
       "us_equities_open": s.us_equities_open, "note": s.note,
   }
   ```
2. `agent_shell/trading_decision.py` `_build_assessment_prompt()` 的 `# Layer 1` 段(Market Regime 之后)插:
   ```python
   from aletheia.trading.market_session import render_for_prompt
   parts += ["## Trading Session", render_for_prompt(), ""]
   ```
   (render 逻辑刻意放在非冻结模块,接线就是一行 import + 一次 append → 改动面最小、最易审。)

**设计原则**: 注入**事实**(哪个时段/美股开闭/流动性档/一句特征描述),**不注入硬规则**
("亚盘禁止开仓")——与现有 prompt"Consider freely, there are no fixed rules" + health_alerts
的"Consider: ..."措辞一致,让 LLM 自主推理而非被规则锁死。

**预估工作量**: 极小(接线 ~6 行;分类器+测试已完成)。

**验证标准**:
- 主判据同 P1: Round(带 P8) vs 上一轮 PF 差异 > 0.15;
- 分时段 breakdown: 低流动性时段(asian/late_us/weekend)的开仓数下降或这些时段的亏损率下降
  (证明 AI 真在据时段自适应,而非无视该信息);
- Kill: 分时段成交分布与未注入时无统计差异(p>0.1)→ 说明 LLM 忽略该维度,回退。

**与其他候选的优先级**: P1(价格结构)/P2(分品种 edge)有 Round 1 实证支撑,信息价值更高;
P8 工作量最小、风险最低,且用户主动提出。可作为 P1/P2 之后的次选,或在某轮专门验证"时段"
单一变量时优先。**注意协议铁律:每轮只取一个**,P8 与 P1–P7 互斥竞争同一配额。

**⚠ 2026-06-26 实证检验(本样本不支持 P8,记录留痕)**:
用 `market_session.classify_session` 对 Round 1 干净窗(6-25 15:22 UTC 后,23 笔)按
**开仓时段**打标,假设若成立则亏损应扎堆低流动性时段(asian/late_us)。结果**相反**:

```
overlap (峰值流动性):  4T 0W  PnL=-33.78  ← 最差时段(两笔大止损恰开在此)
european            :  4T 1W  PnL= -6.66
us                  :  2T 0W  PnL= -3.04
asian (假设的弱时段) : 13T 2W  PnL= -3.58  ← 单笔亏损反而最小
```

亏损集中在**高流动性 overlap 窗**而非亚盘,"亚盘缩量"规则在此样本帮不上忙。真正失效模式
是**方向集中**(short 17T / long 6T,空头一边倒撞上涨)→ 指向 P2,不是 P8。
**结论**: P8 仍保留为低优先候选(成本低、已就绪),但**本样本不支持**抬升其优先级
(注意:样本仅 23 笔/~1 天,是"不支持"非"已证伪",分布可能随数据翻转)。

---

## 已消耗

| 轮次 | 使用的改动 | 来源 |
|------|-----------|------|
| Round 1 | (无,v5 原版) | — |
| Round 2 | _待定_ | — |
| Round 3 | _待定_ | — |
