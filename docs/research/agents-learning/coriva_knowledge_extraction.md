# Coriva 量化知识提取报告 — Aletheia 框架开发指南

本报告将外部量化研究文献（FactorMiner、AlphaAgent、QuantaAlpha、Alpha 101、CTA 文献、Kelly 准则）的核心工程洞见映射到 Aletheia Trading OS 的具体模块与代码位置，供开发团队直接用于 Round 2 迭代。所有改进按优先级分级，附可落地的代码片段和 SDD claim 指引。

---

## P0 关键改进（立即实施）

### P0-1. Score 输入量纲归一化：confidence_score 主导问题

**适用模块**：`ranker`（`paper_trader_ranker.py`）

**描述**：`_compute_trade_score()` 中 `confidence_score`（范围 0–100）直接 `score += float(conf_score)`，量纲远大于 regime（±15–20）、factor（±5–20）、lesson（±25）。第 332 行起，`conf_score` 最多贡献 100 分，而 regime 只能贡献 ±20 分。Round 1 short 集中 17/23 的根因之一——即使 bear regime 给 short +15，只要 long opp 的 `confidence_score` 高几分就能反超，regime 信号实际上失效了。

**实现方案**：在 `paper_trader_ranker.py` 第 331 行替换为：

```python
_clip_norm = lambda v, lo, hi: (min(max(float(v or 0), lo), hi) - lo) / (hi - lo + 1e-8)

conf_score = opp.get('confidence_score')
if conf_score is not None:
    score += 50 * _clip_norm(conf_score, 0, 100)  # 最大贡献 50 分（原来 100 分）
else:
    score += _CONFIDENCE_SCORE_DEFAULT.get(confidence, 20)

# universe total_score 同样 clip（原 0.5 系数范围不明确）
if universe_member:
    score += 25 * _clip_norm(universe_member.get('total_score') or 0, 0, 100)
    score += 10 * _clip_norm(universe_member.get('tradability_score') or 0, 0, 100)
```

同步将 regime 加分从 ±15/±20 提高到 ±20/±25，确保 regime 信号能有效对抗高 confidence 的反方向机会。在 `spec_operator_loop.py` 补 `SpecClaim(id='OL-SCORE-NORM', ...)` 锁死量纲上限。

---

### P0-2. Sideways Regime 对趋势跟踪类 Scanner 缺乏惩罚

**适用模块**：`ranker`（`paper_trader_ranker.py`）

**描述**：`sideways` regime 分支完全缺失（第 380 行后只有 `volatile` 分支）。CTA 文献实测：sideways 是趋势跟踪策略的最差 regime，假信号密度高于 volatile，净亏损更严重。`breakout`/`momentum` 类 scanner 在 sideways 继续被正常评分并执行，这是已知代码遗漏。

**实现方案**：在 `_compute_trade_score()` 第 380 行后追加：

```python
elif regime.state == 'sideways' and regime.confidence > 0.6:
    TREND_FAMILIES = {'breakout', 'momentum', 'ma_crossover', 'trend'}
    scanner = opp.get('scanner_family', '')
    if any(t in (scanner or '').lower() for t in TREND_FAMILIES):
        score -= 25  # trend_tax in sideways：趋势跟踪策略在横盘被惩罚
    elif 'reversion' in (scanner or '').lower() or 'arb' in (scanner or '').lower():
        score += 15  # 均值回归策略在横盘受益
```

在 `spec_operator_loop.py` 补 `SpecClaim(id='OL-SIDEWAYS-TREND-TAX', status='missing')` 锁死该行为，防止误删。

---

### P0-3. Factor IC 加权：以历史胜率作为 Factor 贡献的动态权重

**适用模块**：`ranker`（`paper_trader_ranker.py`）

**描述**：当前 `factor_status` 是分类变量（`watch`/`active`/`preferred`/`blocked`），评分为离散跳跃（±5/±20/±50）。`win_rate=0.42`（`active`，+5）与 `win_rate=0.54`（刚好 `preferred`，+20）之间存在 15 分的断崖，实际质量差距极小。`lookup_factor_signal` 已返回 `win_rate` 字段，可直接用 `(win_rate - 0.5) × 2` 作为 IC 代理实现连续加权。

**实现方案**：替换第 345–353 行的离散评分：

```python
factor_status = factor_signal.get('status', 'watch')
factor_win_rate = factor_signal.get('win_rate')  # None 或 0–1 float
trade_count = factor_signal.get('trade_count', 0)

if factor_status == 'blocked_candidate':
    score -= 50
else:
    # IC 代理：(win_rate - 0.5) * 2，范围 [-1, +1]
    # 乘以 20 -> 最大 ±20 分贡献
    # 样本量 < 5 时衰减到 50%，< 3 时不使用
    if factor_win_rate is not None and trade_count >= 3:
        ic_proxy = (factor_win_rate - 0.5) * 2
        sample_weight = min(1.0, trade_count / 10)  # 10 笔才达到全权重
        score += 20 * ic_proxy * sample_weight
    else:
        score -= 5  # 无历史，轻微惩罚（不再是 0 分中性）
```

解决两个问题：① 连续平滑替代断崖；② 样本量 < 10 时自动缩小权重（paper trade count 意识）。

---

### P0-4. Learning Loop Policy 生成缺乏最小样本量门槛（右偏分布幻觉）

**适用模块**：`learning_loop`（`aletheia/analytics/factor_feedback.py`）

**描述**：`_classify_factor()` 中 `trade_count >= min_trades=3` 就允许状态晋升 `preferred`。3 笔的 `win_rate` 在数学上完全不可信（置信区间横跨 0–1）。更危险的是：一笔异常大盈利（+30%）加两笔小亏损，`expectancy` 为正且 `profit_factor > 1.2`，factor 被标记 `preferred` 并在 ranker 中得到 +20 分。这是 0DTE 散户失败的根本结构：单笔异常大盈利产生虚假的策略优势信号。

**实现方案**：

```python
MIN_TRADES_FOR_PREFERRED = 8    # 从 3 提高到 8
MIN_TRADES_FOR_BLOCKED = 10     # blocked 需要更多证据

def _classify_factor(trade_count, min_trades, expectancy, win_rate, profit_factor):
    if trade_count < min_trades:
        return 'watch', f'insufficient_data({trade_count}/{min_trades})'

    # Outlier 检测：PF > 5.0 且 n < 15 时拒绝晋升 preferred（单笔异常大盈利代理）
    if profit_factor > 5.0 and trade_count < 15:
        return 'active', f'possible_outlier_driven(pf={profit_factor:.1f},n={trade_count})'

    if expectancy < 0 and win_rate < 0.40 and trade_count >= MIN_TRADES_FOR_BLOCKED:
        return 'blocked_candidate', f'wr={win_rate:.2%}_n={trade_count}'

    if (expectancy > 0 and profit_factor >= 1.2
            and win_rate >= 0.45 and trade_count >= MIN_TRADES_FOR_PREFERRED):
        return 'preferred', f'wr={win_rate:.2%}_n={trade_count}'

    return 'active', 'meets_minimum_criteria'
```

同时在 `lookup_factor_signal` 返回 dict 中加入 `sample_size` 字段，供 `doctor` 探针展示 preferred 因子的实际样本量。

---

### P0-5. Regime 评分连续化：消除 confidence > 0.5 断崖效应

**适用模块**：`ranker`（`paper_trader_ranker.py`）

**描述**：第 374 行 `if regime.confidence > 0.5:` 实现了硬阈值——`confidence=0.49` 时 regime 对评分零贡献，`0.51` 时突然贡献 ±15 分。在 regime 模糊期（confidence 在 0.5 附近振荡），候选排序会反复跳变。内存记录（`aletheia-ranker-regime-direction-fix.md`）已修过方向逻辑，但连续化问题未处理。

**实现方案**：将第 373–381 行硬 threshold 替换为连续函数：

```python
regime = get_current_regime()

# 平滑权重：confidence^1.5 在 0.5 时 ≈ 0.35，0.7 时 ≈ 0.59，0.9 时 ≈ 0.85
_BASE_REGIME_DELTA = {
    ('bear', 'short'): +20,
    ('bear', 'long'):  -25,
    ('bull', 'long'):  +20,
    ('bull', 'short'): -20,
}

if regime.state in ('bear', 'bull'):
    delta = _BASE_REGIME_DELTA.get((regime.state, direction), 0)
    weight = regime.confidence ** 1.5  # 连续平滑，避免 0.5 断崖
    score += delta * weight
elif regime.state == 'volatile':
    score -= 10 * (regime.confidence ** 1.5)
# sideways 由 P0-2 处理
```

让 regime 信号强度与探测器信心成正比，微小 confidence 波动不再造成排序跳变。

---

## P1 高优先改进

### P1-1. IV Rank + Vol Acceleration 注入 enriched_context

**适用模块**：`market_observer`（`aletheia/data/context_builder.py` 或 `trading/market_context.py`）

**描述**：当前 `enriched_context_md` 有 ATR% 但无波动率历史分位信息。LLM 无法判断「现在波动率是历史高位还是低位」，导致在 vol spike 后追入方向性交易（IV Crush 盲区）。两个指标均为纯算术计算，零新数据源依赖：
- `IVR = (rv_20d - min_rv_252d) / (max_rv_252d - min_rv_252d) × 100`
- `vol_acceleration = rv_5d / rv_20d`（短期 vs 中期 realized vol 比值）

**实现方案**：

```python
def _compute_vol_regime(symbol: str) -> dict:
    import numpy as np
    from aletheia.storage.repositories import get_ohlcv_candles
    bars = get_ohlcv_candles(symbol, interval='1d', limit=275)  # 252+20 buffer
    if len(bars) < 30:
        return {}
    closes = [b.close for b in bars]
    log_ret = np.diff(np.log(closes))
    # 252 日滚动 20d RV 序列
    rv_series = [
        np.std(log_ret[i:i+20]) * np.sqrt(252) * 100
        for i in range(len(log_ret) - 252, len(log_ret) - 20 + 1)
    ]
    if len(rv_series) < 20:
        return {}
    rv_5d  = np.std(log_ret[-5:]) * np.sqrt(252) * 100
    rv_20d = rv_series[-1]
    iv_rank = (rv_20d - min(rv_series)) / (max(rv_series) - min(rv_series) + 1e-9) * 100
    vol_accel = rv_5d / (rv_20d + 1e-9)
    label = 'HIGH' if iv_rank > 60 else ('LOW' if iv_rank < 30 else 'MID')
    return {'iv_rank': round(iv_rank, 1), 'vol_accel': round(vol_accel, 2), 'label': label}
```

输出追加到 context：`| Vol Regime | IVR {iv_rank:.0f} / Accel {vol_accel:.2f}x → {label} |`

ranker 读取快照中该值：IVR > 80 时 `score -= 20`（所有方向）；IVR > 50 时 short +10/long -15；IVR < 30 时 long +10。

---

### P1-2. Vol Crush 检测：事件后 vol 骤降期间拒绝新开仓

**适用模块**：`ranker`（`paper_trader_ranker.py`）

**描述**：24h RV 不足 72h RV 的 50% 说明刚刚经历了大幅 vol 收缩（类比 IV Crush）。此时行情已 priced in，追入是负期望。检测纯 Python，无额外数据源，只需已有 1h OHLCV。

**实现方案**：在 `_evaluate_candidate()` 第 267 行 factor_signal lookup 之后加入：

```python
if _detect_vol_crush(symbol):
    return _make_candidate(opp, skip_reason='vol_crush_detected',
                           direction=direction, strategy_id=strategy_id,
                           universe_member=universe_member)

def _detect_vol_crush(symbol: str) -> bool:
    import numpy as np
    try:
        bars = get_ohlcv_candles(symbol, interval='1h', limit=73)
        if len(bars) < 72:
            return False
        closes = [b.close for b in bars]
        log_ret = np.diff(np.log(closes))
        rv_24h = np.std(log_ret[-24:]) * (24 * 365) ** 0.5 * 100
        rv_72h = np.std(log_ret[-72:]) * (24 * 365) ** 0.5 * 100
        return rv_72h > 0 and rv_24h < rv_72h * 0.5
    except Exception:
        return False
```

性能注意：在 `build_paper_candidates()` 开头预计算所有 symbol 的 `vol_crush` 状态（批量查询缓存为 dict），传入 `_evaluate_candidate`，避免逐次 DB 开销。

---

### P1-3. Score Breakdown 落盘：子项分解记录供 Learning Loop IC 分析

**适用模块**：`learning_loop`（`paper_trader_ranker.py`、`trade_attribution.py`）

**描述**：当前 `trade_score` 是单一数字，无法拆解哪个子组件有预测力。这是 learning loop 无法改进 ranker 的结构性障碍——不知道是 regime 分越准越好还是 confidence 越高越准，就无法学到有效 policy。

**实现方案**：`_compute_trade_score()` 末尾改为返回 `(score, breakdown)`：

```python
breakdown = {
    'confidence_contrib': conf_contrib,
    'regime_contrib':     regime_contrib,
    'factor_contrib':     factor_contrib,
    'lesson_contrib':     lesson_contrib,
    'memo_contrib':       memo_contrib,
    'universe_contrib':   universe_contrib,
    'risk_flag_penalty':  risk_flag_penalty,
}
return round(score, 2), breakdown
```

调用处同步修改：

```python
trade_score, score_breakdown = _compute_trade_score(...)
```

在 `_make_candidate()` 追加 `score_breakdown` 字段，写入 `TradeProposal` 时 merge 进 `metadata_json`。向后兼容：`build_trade_attribution_rows()` 检查 `metadata_json` 中是否有 `score_breakdown` 键，有则解析，无则跳过。

---

### P1-4. Factor HARD_BLOCKED：连败因子升级为硬拒绝

**适用模块**：`learning_loop`（`factor_feedback.py`）+ `ranker`

**描述**：当前 `blocked_candidate` 在 ranker 中只扣 -50 分，非硬拒绝。理论上高 confidence（80+）的 blocked_candidate 仍可能 score 为正（80 - 50 = 30）并被执行。`win_rate < 30%` 且样本 ≥ 10 笔的因子应升级为 `HARD_BLOCKED`，同时设置可被证据推翻的降级通路（符合 owner 高维可推翻原则）。

**实现方案**：

```python
# factor_feedback.py：新增 hard_blocked 分支
if trade_count >= 10 and win_rate < 0.30 and expectancy < -0.5:
    return 'hard_blocked', f'hard_blocked:wr={win_rate:.2%}_n={trade_count}'

# 可推翻降级：最近 14 天干净窗 win_rate >= 45% 且 n >= 5 则降回 blocked_candidate
```

```python
# paper_trader_ranker.py：_evaluate_candidate() 中加前置硬拦截
if factor_signal.get('status') == 'hard_blocked':
    return _make_candidate(opp, skip_reason='factor_hard_blocked',
                           direction=direction, strategy_id=strategy_id,
                           factor_signal=factor_signal)
```

---

### P1-5. 样本量感知的 Kelly 分数：paper_trade_count < 10 时强制缩到十分之一 Kelly

**适用模块**：`risk_engine`（`aletheia/trading/sizing.py`）

**描述**：当前 `compute_position_size()` 使用固定 `kelly_fraction=0.25`，不管历史样本量多少。样本量 < 10 时期望收益的标准误差极大（`σ/√n`），代入 Kelly 等于 over-bet。修改后对样本充足策略行为不变，只对新策略前 10 笔自动降低仓位，风险极低。

**实现方案**：

```python
def vol_target_size(symbol, *, conviction=70, drawdown_pct=0.0,
                   atr_pct_override=None, paper_trade_count=0):
    cfg = load_config()
    base_kelly = cfg.trading.vol_kelly_cap_fraction  # 默认 0.25

    # 样本量感知 Kelly：线性从 0.1 爬升到 base_kelly
    if paper_trade_count < 10:
        effective_kelly = 0.1          # 十分之一 Kelly
    elif paper_trade_count < 30:
        t = (paper_trade_count - 10) / 20.0  # 0→1 线性插值
        effective_kelly = 0.1 + t * (base_kelly - 0.1)
    else:
        effective_kelly = base_kelly
    ...
```

调用方（`paper_trader.py` 的 `vol_target_size` 调用处）从 `_lookup_strategy_evidence()` 返回值中取 `paper_trade_count` 传入。

---

### P1-6. Trajectory-Level 失败归因：blame_step 写入 Lesson Category

**适用模块**：`learning_loop`（`aletheia/analytics/trade_attribution.py`）

**描述**：当前 `lesson.category = 'general'`，无法区分「ranker 评分高但 LLM 反向」、「market_observer 信号弱但 confidence 高」等不同失败路径，导致 lesson 无法指导具体模块改进。P1-3 的 `score_breakdown` 落盘后，可从各组件值与胜率高交易均值对比，找出最偏离的组件作为 `blame_step`。

**实现方案**：

```python
WINNING_BASELINES = {
    'confidence_contrib': 55,  # 胜率组均值（经验初始值，后续用数据校准）
    'regime_contrib':     10,
    'factor_contrib':      8,
    'lesson_contrib':     -3,
    'memo_contrib':        2,
}

def _build_blame_step(score_breakdown: dict) -> str:
    if not score_breakdown:
        return 'unknown'
    worst_step = min(
        WINNING_BASELINES.keys(),
        key=lambda k: score_breakdown.get(k, 0) - WINNING_BASELINES[k]
    )
    return worst_step.replace('_contrib', '')
```

`LessonStore.create()` 调用时传入 `category=blame_step`，使 lesson 可按 `blame_step='regime'/'factor'/'confidence'` 过滤，供 PI Agent 有选择性地注入上下文。

---

### P1-7. Market Observer 确定性预筛：跳过无方向性信号的 Symbol

**适用模块**：`market_observer`（`aletheia/agents/market_observer.py`）

**描述**：当前每个 symbol 无论指标是否有信号都完整调用 LLM，是 200s wall-clock 预算的主要耗尽原因。平静市场中 20–40% 的 symbol RSI 在 45–55 中位区、SMA 无明显偏离——LLM 大概率输出 neutral，是资源浪费。在 LLM 调用前加确定性 pre-filter，配置中提供 `force_full_scan` 旁路开关。

**实现方案**：在 `_analyze_symbol()` 中，`build_market_context()` 调用之后、`llm_structured()` 调用之前加入：

```python
def _has_sufficient_signal(context_str: str) -> bool:
    import re
    # RSI 偏离检测
    rsi_match = re.search(r'RSI[^|]*\|[^|]*?(\d+\.\d+)', context_str, re.IGNORECASE)
    if rsi_match and abs(float(rsi_match.group(1)) - 50) > 12:
        return True
    # ATR 占比检测
    atr_match = re.search(r'ATR[^|]*\|[^|]*?(\d+\.\d+)%', context_str, re.IGNORECASE)
    if atr_match and float(atr_match.group(1)) > 0.8:
        return True
    # 关键词检测
    if any(kw in context_str.lower()
           for kw in ['breakout', 'breakdown', 'oversold', 'overbought']):
        return True
    return False

if not cfg.observer.force_full_scan and not _has_sufficient_signal(context_str):
    logger.debug('skipping LLM for %s: no sufficient signal', symbol)
    return None, None
```

正则从已有 context markdown 提取数值，不需要新数据结构，直接节省 LLM 调用预算。

---

### P1-8. Sortino + Calmar 指标加入 Strategy Evidence 评分

**适用模块**：`evaluation`（`aletheia/analytics/strategy_evidence.py`）

**描述**：当前 `_classify_evidence_status()` 只用 `expectancy` 和 `profit_factor`，无法区分「频繁小赚偶尔巨亏」和「稳定双向收益」。Sortino 惩罚负向波动率（比 Sharpe 更适合非正态收益）；Calmar 衡量收益/最大回撤比（CTA 标准指标）。实现只需已有 `realized_pnl_pct` 列表。

**实现方案**：

```python
import math
import numpy as np

def compute_sortino(pnl_pcts: list[float]) -> float | None:
    if len(pnl_pcts) < 10:
        return None
    returns = [p / 100 for p in pnl_pcts]
    neg = [r for r in returns if r < 0]
    if not neg:
        return float('inf')
    downside_std = np.std(neg) * math.sqrt(252)
    return np.mean(returns) / downside_std if downside_std > 0 else 0.0

def compute_calmar(pnl_pcts: list[float]) -> float | None:
    if len(pnl_pcts) < 10:
        return None
    cumulative = np.cumprod(1 + np.array(pnl_pcts) / 100)
    peak = np.maximum.accumulate(cumulative)
    mdd = np.min((cumulative - peak) / (peak + 1e-10))
    if mdd == 0:
        return 0.0
    annualized_ret = float(cumulative[-1]) ** (252 / len(pnl_pcts)) - 1
    return annualized_ret / abs(mdd)
```

在 `_compute_trade_score()` 中追加：

```python
sortino = strategy_evidence.get('sortino')
calmar  = strategy_evidence.get('calmar')
if sortino is not None:
    score += 10 if sortino > 1.0 else (-15 if sortino < 0.3 else 0)
if calmar is not None:
    score += 10 if calmar  > 1.0 else (-10 if calmar  < 0.3 else 0)
```

---

### P1-9. 量价背离信号 pv_divergence 写入 MarketSnapshot

**适用模块**：`market_observer`（`trading/market_context.py`）

**描述**：Alpha#3 量价背离：`pv_div = -corr(rank(open[-10:]), rank(volume[-10:]))`。正值 = 价格和成交量走势背离（做多信号），负值 = 同向加速（过热信号）。当前 market_observer 完全没有量价关系指标，LLM 只能从价格和 RSI 推断，缺少成交量结构信息。需要 10 根 bar 的 open + volume（已有 `get_ohlcv_candles`）。

**实现方案**：

```python
from scipy.stats import rankdata
import numpy as np

def compute_pv_divergence(bars) -> float | None:
    if len(bars) < 10:
        return None
    opens = np.array([b.open   for b in bars[-10:]])
    vols  = np.array([b.volume for b in bars[-10:]])
    corr = np.corrcoef(rankdata(opens), rankdata(vols))[0, 1]
    return round(-corr, 3)  # 正值=背离=做多信号
```

输出追加到 enriched_context：`| PV-Div | {pv_div:+.3f} |`

ranker 读取该值：`pv_div > 0.3` 且 `direction == 'long'`：+8；`pv_div < -0.3` 且 `direction == 'short'`：+8（量价同步加速，短期动量确认）。

---

### P1-10. Breakeven 胜率校验：盈亏平衡线内的 Confidence 降档

**适用模块**：`market_observer`（`market_observer.py`）

**描述**：盈亏平衡公式：`breakeven_win_rate = 1 / (1 + R:R ratio)`。止盈/止损比 2:1 时需要 >33.3% 历史胜率才能期望盈利。market_observer 输出 confidence 时并不校验历史胜率是否满足盈亏平衡条件，导致历史胜率不达标的信号以 `medium` confidence 执行。

**实现方案**：

```python
def _check_breakeven_gate(opp_dict: dict, symbol: str, direction: str) -> str:
    """返回可能被降档后的 confidence 字符串。"""
    from aletheia.analytics.factor_feedback import lookup_factor_signal
    tp    = opp_dict.get('take_profit')
    sl    = opp_dict.get('stop_loss')
    entry = opp_dict.get('entry_price')
    if not (tp and sl and entry and entry > 0):
        return opp_dict.get('confidence', 'speculative')

    rr = ((tp - entry) / (entry - sl + 1e-10) if direction == 'long'
          else (entry - tp) / (sl - entry + 1e-10))
    if rr <= 0:
        return 'speculative'

    breakeven = 1 / (1 + rr)
    signal = lookup_factor_signal({
        'scanner_family': opp_dict.get('scanner_family'),
        'symbol': symbol, 'direction': direction
    })
    hist_wr = signal.get('win_rate')
    if hist_wr is not None and signal.get('trade_count', 0) >= 5:
        if hist_wr < breakeven * 0.9:  # 历史胜率低于盈亏平衡的 90%
            orig = opp_dict.get('confidence', 'speculative')
            return 'speculative' if orig == 'medium' else orig  # 降一档
    return opp_dict.get('confidence', 'speculative')
```

---

### P1-11. Log-Return 替代 Simple Return：Strategy Evidence 聚合用复利计算

**适用模块**：`learning_loop`（`paper_trader_ranker.py`，`_lookup_strategy_evidence()`）

**描述**：当前使用 `avg(realized_pnl_pct)` 作为 `paper_expectancy`。Simple return 不可加：+10% 后 -10% = -1% 实际，但算术均值 = 0%。加密资产高波动率下误差可达 0.5–2%/轮次，长期累积后 `strategy evidence` 会系统性高估真实复利收益，影响 `drift_status` 判断。

**实现方案**：

```python
import math

# 替代原 avg(realized_pnl_pct) SQL 聚合
rows = db.execute(
    'SELECT realized_pnl_pct FROM positions '
    'WHERE strategy_id=? AND status="closed" '
    'AND execution_mode NOT LIKE "%live%" '
    'ORDER BY closed_at',
    (strategy_id,)
).fetchall()

pnl_pcts = [r[0] for r in rows if r[0] is not None]
if pnl_pcts:
    log_total = sum(math.log(1 + p / 100) for p in pnl_pcts)
    paper_expectancy        = (math.exp(log_total) - 1) * 100 / len(pnl_pcts)
    paper_expectancy_simple = sum(pnl_pcts) / len(pnl_pcts)  # 保留对比
else:
    paper_expectancy = 0.0
```

向后兼容：原字段 `paper_expectancy` 改为 log-return 版本，新增 `paper_expectancy_simple` 字段供对比诊断。

---

## P2 中优先改进

### P2-1. 机会候选池结构去重：Jaccard 相似度 > 0.8 只保留最高分

**适用模块**：`ranker`

同一 scan 周期内 BTC 和 ETH 可能产出结构高度相同的 short opportunity（相关性 > 0.8），ranker 视为独立信号——这是 Round 1 short 17/23 的机制性来源。在 `build_paper_candidates()` 返回后、`select_best_candidate()` 前加去重 pass：从 `key_observations` 解析 `(indicator_name, timeframe)` 特征集，Jaccard 相似度 > 0.8 则保留 `trade_score` 最高的，其余标记 `skip_reason='structural_duplicate'`。

---

### P2-2. 三段式回撤 Scale：深回撤线性趋零而非固定 0.5

**适用模块**：`risk_engine`（`sizing.py`）

Kelly 理论：深回撤时 over-bet 的长期增长率为负，应完全停止开仓。改为三段分段函数：

| 回撤区间 | dd_scale |
|----------|----------|
| 0–5%     | 1.0（不缩减） |
| 5–10%    | 线性 1.0 → 0.5 |
| 10–20%   | 线性 0.5 → 0.0 |

两处需同步修改：`sizing.py` 第 57 行局部 `dd_scale` 和 `risk/engine.py` 的 `_portfolio_drawdown_scale()`。

---

### P2-3. ATR-Based 仓位上限：按 Symbol 波动率归一化 max_qty

**适用模块**：`risk_engine`

BTC ATR=3%、某 altcoin ATR=15%，同 $100 notional 的实际 1-ATR 风险相差 5 倍。当前 check #5 只检查 `notional > effective_cap`，不区分 symbol 波动率。在 `_evaluate_proposal_locked` check #13 附近加 ATR-based 备用 max qty 检查：

```
atr_risk_qty = capital_base × max_single_trade_risk_pct/100 / (price × atr_pct/100)
```

`proposal.quantity > atr_risk_qty × 2` 时触发 violation。`atr_pct` 已有 `_lookup_atr_pct` 函数可复用。

---

### P2-4. Alpha#35 底部放量三因子注入评分

**适用模块**：`ranker`

三因子乘积接近 1.0 = 成交量 32 日高位 + 日内结构 16 日低位 + 收益率 32 日低位 = 经典底部放量信号。对 long 方向 `score += 25 × signal`。需要 32 根 1h bar（`get_ohlcv_candles` 已有接口），加在 `_compute_trade_score()` 末尾。`ts_rank = (sorted(seq).index(seq[-1]) + 0.5) / len(seq)`。在高 IVR（> 50）时信号权重减半（与 P1-1 配合）。

---

### P2-5. 方向签名 + CLV 注入 MarketSnapshot

**适用模块**：`market_observer`

- **Alpha#30 方向签名**：`dir_sig = sum(sign(close[i]-close[i-1]) for i in [-3,-2,-1])`，取值 {-3,-1,+1,+3}
- **Alpha#101 CLV**：`CLV = (close-open)/(high-low)`，取最近 3 根均值

两个值直接从已有 OHLCV 计算，零额外数据源，注入 snapshot：`| Dir-3h | +3 | CLV-3 | +0.42 |`，让 LLM 从确定性签名读取方向信息，减少从连续价格序列推断产生的幻觉。

---

### P2-6. Funding Rate Z-Score 替换固定阈值：动态拥挤信号

**适用模块**：`market_observer`（`regime/detector.py`）

`regime/detector.py` signal 4 当前用 `avg_funding_rate > 0.0008` 硬阈值。改为 rolling 30d Z-score：`z = (current_funding - mean_30d) / std_30d`。`z > 2` → long 极度拥挤 → bear vote +1.5；`z < -2` → short 极度拥挤 → bull vote +1.0。`derivatives` 表已收集历史 funding 快照，可直接用。

---

### P2-7. Successful Pattern TTL：preferred 因子加 14 天过期机制

**适用模块**：`learning_loop`（`factor_feedback.py`）

`preferred` 状态一旦设置永不过期。牛市表现好被标记 preferred 的 scanner，市场切换到熊市后仍可能给错方向信号 +20 分。加 TTL：`preferred` 标记附带 `expires_at = now + 14d`；每次新数据到来满足续期条件则续期；过期后不满足续期条件降回 `active`。与 P0-4 的 HARD_BLOCKED 构成对称的「可推翻」设计。

---

### P2-8. Walk-Forward 验证标记：回测/Paper 比值异常时标记 likely_overfit

**适用模块**：`evaluation`（`strategy_evidence.py`）

`backtest_pf > 2.0` 且 `paper_exp < bt_exp × 0.4` 时标记 `drift_status='likely_overfit'`，在 ranker 中 -25 分惩罚。对 `_EVIDENCE_PENALTY_STATUSES` 增加 `'likely_overfit': -25` 条目，并在 `_lookup_strategy_evidence()` 末段加判断逻辑。

---

### P2-9. Holding Time 换手率惩罚：每日交易 > 3 笔的策略扣分

**适用模块**：`ranker`

高换手率策略在真实手续费和滑点下收益大幅缩水。在 `_evaluate_candidate` 中查过去 7 天策略成交笔数，`trades_per_day = count / 7`；> 3 笔/天时 `penalty = min(20, (trades_per_day - 3) × 5)`。只需一条 SQL 聚合计数，开销极小。

---

### P2-10. Regime Stability 趋势探针：Doctor 加 7 天滑动 PF 斜率

**适用模块**：`evaluation`（doctor 探针）

在 doctor 探针中加入 `check_regime_stability()`：从 `positions` 表查最近 30 天关仓数据，按 7 天滑动窗口计算 PF，用 `numpy.polyfit` 拟合趋势斜率；斜率 < -0.02/天时 WARN（正在经历 regime shift）。Round 1 的根因之一是未及时识别 regime shift，该探针作为早期预警机制。

---

### P2-11. Opportunity TTL 按 scanner_family 差异化

**适用模块**：`paper_trader`

反转类信号 alpha 衰减最快（Alpha 101 实证），不应与趋势类共用同一 TTL：

```python
TTL_MAP = {
    'reversal':    2,  # 天
    'mean_revert': 2,
    'momentum':    5,
    'breakout':    4,
    'default':     4,
}
```

在 ranker `_evaluate_candidate()` 的 TTL gate 段：若 `universe_member` 无 `expires_at` 且 `opp.created_at` 存在，用 `TTL_MAP` fallback 判断信号年龄是否超出。

---

### P2-12. Theta-Gamma 类比：Funding Cost Ratio > 0.5 时给 PI Agent Soft Exit 建议

**适用模块**：`risk_engine`

持仓时间越长，funding rate 累积成本（类比 Theta）侵蚀未实现盈亏的比例越高。实现 `funding_cost_ratio = 累积资金费 / 未实现盈亏`；> 0.5 时在持仓上下文中标记 `funding_drain_warning=True`，PI Agent 在下次 `review_portfolio_state` 时收到 soft exit 建议（不是强制关仓）。`funding_annualized` 已在 `LeverageState` 数据结构中存在。

---

### P2-13. Scenario Matrix：TradeProposal 通过后附加 ±10% 价格情景 PnL

**适用模块**：`risk_engine`

在 `_evaluate_proposal_locked()` 尾部，对已批准的 proposal 生成 5 档价格情景（±10%/±5%/0%）的预期 PnL，追加到 `RiskDecision.message` 字段。不改变 approved/rejected 逻辑，纯增量信息。让 PI Agent review 时能看到最坏情景数字而不只是布尔值。

---

### P2-14. 跳空缺口检测注入快照：三方向一致跳空才触发

**适用模块**：`market_observer`

Alpha#20 跳空三重比较：`gap_h=(cur.open-prev.high)/prev.high`；`gap_c=(cur.open-prev.close)/prev.close`；`gap_l=(cur.open-prev.low)/prev.low`。三者同为正且均值 > 0.3%：`key_observations` 追加「gap_up 回补概率高」。只需最近 2 根 bar 数据，已有 OHLCV，1h 级别 0.3% 阈值不会过频触发。

---

### P2-15. Pairwise Correlation 组合集中度检查：新开仓与现有持仓相关性 > 0.85 时 Flag

**适用模块**：`risk_engine`

当前 `_check_correlation_exposure()` 用 BTC-beta 和 `scanner_family` 作为相关性代理。加密市场压力时相关性趋向 1.0，sector proxy 失效。扩展方案：对 open positions 的每个 symbol，查 `universe_feature_snapshots` 的 30 天 closes，计算 `np.corrcoef`，与 proposal symbol 相关性 > 0.85 则 violation。新增 `symbol_correlations` 表缓存（由 `market_observer` 定期刷新）。

---

## 架构洞见：从 AI 因子论文学习

### FactorMiner / AlphaAgent 三阶段流水线 ↔ Aletheia 三层架构

**论文核心**：FactorMiner 和 AlphaAgent 共同揭示了 AI 因子挖掘的高效工程范式——Fast IC Screening（快速预筛）→ Batch Dedup（结构去重）→ LLM Deep Read（深度判断）。Stage 1 用毫秒级确定性计算拒绝 60–70% 的无效信号，让 Stage 3 的 LLM 只处理「有料且不重复」的候选。

**Aletheia 映射**：

| 论文阶段 | Aletheia 模块 | 对应改进 |
|----------|---------------|----------|
| Fast IC Screening | `market_observer` pre-filter | P1-7 确定性预筛（RSI 偏离/ATR/关键词） |
| Batch Dedup | `ranker` 候选池去重 | P2-1 Jaccard 结构去重 |
| LLM Deep Read | `PI Agent` + `market_observer` LLM 调用 | P0-1 量纲归一化确保评分不被 confidence 淹没 |

三者联合让 LLM 只对「有料且不重复」的信号做深度判断，保护 200s wall-clock 预算不被低质量信号消耗殆尽。

**关键工程原则**：AlphaAgent 明确区分「数值计算由确定性 Python 完成，LLM 只做符号操作和方向判断」。当前 Aletheia 让 LLM 自填 `confidence_score` 数值（0–100），违反了这条原则——LLM 倾向于高分自我肯定（calculation hallucination）。长期方向是用 P1-1 的 IVR + P1-9 的 pv_divergence + P2-5 的 dir_sig 等确定性指标替代 LLM 自填的数值分数，让 LLM 只判断「方向和理由」。

---

### QuantaAlpha Crossover + Mutation 进化原语 ↔ Aletheia Learning Loop 结构化记忆

**论文核心**：QuantaAlpha 实测显示，没有结构化记忆复用的 LLM 因子挖掘在 iter 3 的 IC 反而低于 iter 1——放大了噪音而非累积信号。解决方案是 Crossover（从成功案例提取有效片段）+ Mutation（从失败中学习）的对称机制，两者都要有显式保留通路。

**Aletheia 现状诊断**：

- `lesson/memo` 体系接近 Mutation（从失败学习），但缺乏有效的 Crossover（从成功提取可复用片段）
- `reflection` 模块有 SyntaxError（5be7826）导致静默失败，Mutation 通路本身就不完整
- 每次 operator loop 无法从历史 trajectory 中继承有效知识，等同于每轮从头重新采样

**Aletheia 映射与改进路径**：

| QuantaAlpha 机制 | Aletheia 对应点 | 改进优先级 |
|-----------------|-----------------|-----------|
| Mutation（失败学习） | lesson 生成 + blame_step | P1-6（blame_step 是 Mutation 的关键前提） |
| Crossover（成功复用） | PI Agent context 中的 relevant memo 检索 | 长期：Jaccard 相似度 top-3 成功案例注入 |
| Structural Memory | score_breakdown 落盘 | P1-3（基础建设，Crossover 的数据来源） |

当前最紧迫的是完成 P1-3（score breakdown 落盘）→ P1-6（blame_step category）这条基础链。有了 blame_step 分类，才能在下一轮 PI Agent context 构建时选择性注入「同类失败的相关历史案例」，实现最简化版 Crossover。

---

### FactorMiner Forbidden Regions / Successful Patterns 对称性 ↔ Aletheia Factor 状态机

**论文核心**：FactorMiner 发现，Forbidden Regions（被证明失效的因子空间）和 Successful Patterns（被验证有效的因子模式）必须以对称方式处理：两者都需要 TTL 防止过时，两者都需要「可被新证据推翻」的机制，否则系统会陷入路径依赖。

**Aletheia 映射**：

- **Forbidden Regions** → `blocked_candidate` / `hard_blocked` 状态（P0-4 + P1-4）
- **Successful Patterns** → `preferred` 状态（P2-7 添加 14 天 TTL）

对称性设计原则：

```
hard_blocked → 干净窗 win_rate >= 45% && n >= 5 → 降回 blocked_candidate  （可推翻）
preferred    → 14天内无续期证据               → 降回 active              （自动过期）
```

这直接对应 owner 的「高维可推翻进化」原则（`aletheia-negfeedback-high-dimensional-evolve.md`）：被证明失效的路径可以被封锁，但封锁必须可以被后续高质量证据解除，而不是永久烙印。

---

### Kelly 准则对小样本的极度敏感性 ↔ 样本量感知仓位管理

**论文核心**：Kelly 准则对参数估计误差极其敏感——`f* = (bp - q) / b`，当 `p` 的估计误差达到 ±10%（3 笔样本的正常误差范围），最优 Kelly 分数偏离真值可超过 50%，over-bet 导致长期破产风险大幅上升。学术界的实用建议是样本量 < 30 时使用 Fractional Kelly（全凯利的 1/4 到 1/10）。

**Aletheia 现状**：固定 `kelly_fraction=0.25` 对所有策略一视同仁，对样本量 3 笔的新策略实际上是 over-bet。

**双重保护设计**（P0-3 + P1-5 联动）：

```
小样本策略（n < 10）的两重保护：
  ① 评分不确定：factor IC 权重衰减到 50%（P0-3 sample_weight）
  ② 仓位缩小：强制十分之一 Kelly（P1-5）

小样本 preferred 晋升阻断（P0-4）：
  n < 8 不允许 preferred → ranker 不给 +20 分奖励
```

三层联动确保新策略前 10 笔始终处于「低评分 + 小仓位」的保守状态，避免单笔异常大盈利产生的虚假优势信号驱动后续大仓位操作。

---

### CTA 文献 Sideways Regime ↔ 趋势跟踪策略失效

**论文核心**：CTA Python 文章和经典 CTA 文献一致结论：sideways（横盘）是趋势跟踪策略表现最差的 regime，比 volatile（高波动）还差，原因是假突破密度极高。momentum/breakout 类策略在横盘的净亏损通常超过 volatile regime 下的净亏损，因为 volatile 至少有真实价格位移，而 sideways 产生的每次「突破」都是噪音。

**Aletheia 直接影响**：`breakout`/`momentum` scanner 在 sideways 继续被正常评分并执行，是 paper trade 持续亏损的结构性来源（P0-2 修复）。更深层含义：与 P2-11 差异化 TTL（反转类 2 天/动量类 5 天）配合，sideways 市场形成双重防御：

1. **P0-2**：sideways + trend scanner → -25 分惩罚（评分层过滤）
2. **P2-11**：reversal 类 TTL 缩短至 2 天（时间层过滤）

同时 sideways 给 reversion/arb 类 scanner +15 分，主动引导系统在横盘市场切换至均值回归策略——这是对 regime 的最优适配而非被动防御。

---

## 必须规避的反模式（Anti-Patterns）

### Anti-Pattern 1：LLM confidence_score 自填，无 Deterministic 验证层

**什么是这个问题**：LLM 在生成 `MarketRead` 时自行输出 0–100 的 `confidence_score` 数值，该值直接成为 ranker 最大评分组件（最高 100 分，远超其他项）。

**为何危险**：LLM 倾向于给自己的方向判断打高分（自我肯定偏差），且无法真正计算 RSI/ATR 数值（FactorMiner 称之为 calculation hallucination）。`confidence_score` 系统性放大 LLM 幻觉，使得 regime/factor 等确定性信号实际上失效。Round 1 short 17/23 集中的机制性来源之一。

**Aletheia 防守**：
- **短期**：P0-1 量纲归一化将 conf_score 贡献从最高 100 分收窄到 50 分
- **中期**：加 `_verify_alignment()` 检查 `direction_bias` 与 `key_observations` 文字方向是否一致，`alignment < 0.4` 时强制降低 `confidence_score` 30 分并标记 `ALIGNMENT_WARNING`
- **长期方向**：用 IVR（P1-1）+ pv_divergence（P1-9）+ dir_sig（P2-5）等确定性指标替代 LLM 自填的数值分数，LLM 只判断方向和理由

---

### Anti-Pattern 2：混合历史窗口 PF 掩盖干净窗真实表现

**什么是这个问题**：`doctor` 或 `strategy_evidence` 聚合了修复前和修复后的全部历史数据，旧盈利数据持续粉饰当前性能。

**为何危险**：Round 1 已犯：混合 PF=0.769 vs 干净窗 PF=0.06，相差 12 倍。系统自以为在正常运作，实际上决策链喂的是幻觉。QuantaAlpha 2023 A 股 regime shift 案例：Alpha158 IC 近乎归零，但混合历史数据看起来完全正常，导致策略继续运行数周。

**Aletheia 防守**：
- `_lookup_strategy_evidence()` 强制支持 `clean_window_start` 参数（= `max(last_major_fix_commit_ts, now-14d)`）
- doctor 的 PF 探针已有 `dry_run` 过滤（2026-06-29 修复），但 backtest vs paper drift 对比仍无时间戳过滤
- P2-10 的 regime_stability 趋势探针（7 天滑动 PF 斜率）作为 regime shift 的早期预警
- **红线规则**：任何「策略表现正常」的结论必须附注干净窗起始时间戳

---

### Anti-Pattern 3：每轮学习循环无结构化记忆复用，等同于从头重新采样

**什么是这个问题**：learning loop 的每次 operator loop 不继承上一轮验证过的有效知识片段，相当于每次从零开始采样。

**为何危险**：QuantaAlpha 实测：RD-Agent 在无结构化复用情况下，iter 3 的 IC 反而低于 iter 1——噪音随轮次累积而非有效信号累积。Aletheia 的 `reflection`（SyntaxError 静默失败）、`lesson` 生成、`guard` 调整各自独立，没有一条「被验证的决策片段被显式保留并在下一轮复用」的通路。

**Aletheia 防守**：
- **立即**：P1-3 score_breakdown 落盘（Crossover 的数据来源）
- **立即**：P1-6 blame_step lesson category（Mutation 的关键前提）
- **中期**：PI Agent context 构建时，基于当前信号特征的 Jaccard 相似度，只检索 top-3 历史成功案例注入 context（替代全量 memo），实现有选择性的 Crossover 复用
- **红线规则**：修复 `reflection` 的 SyntaxError 是所有 learning loop 改进的前提

---

### Anti-Pattern 4：小样本 preferred 标记——3 笔交易产生 +20 分策略偏好

**什么是这个问题**：`factor_feedback._classify_factor()` 以 `trade_count >= 3` 允许状态晋升 `preferred`，3 笔的胜率统计在数学上置信区间横跨 0–100%，完全无意义。

**为何危险**：一笔异常大盈利（+30%）+ 两笔小亏损 → `expectancy` 为正 → `profit_factor > 1.2` → 标记 `preferred` → ranker +20 分。这是 0DTE 散户「少数 10x 盈利掩盖 80% 全损」的算法版本。系统在极小样本上「学到」了一个实际上是随机噪音的「优势策略」。

**Aletheia 防守**（P0-4 + P1-5 联动）：
- preferred 门槛从 3 笔提高到 8 笔（P0-4）
- 加入 outlier 检测：PF > 5.0 且 n < 15 时拒绝晋升（P0-4）
- 小样本 Kelly 压缩到十分之一（P1-5）
- 两者联动：小样本的 preferred 不再产生 +20 分，仓位也被同步压缩

---

### Anti-Pattern 5：算术均值 Simple Return 系统性高估复利收益

**什么是这个问题**：`avg(realized_pnl_pct)` 作为 `paper_expectancy` 使用算术均值而非几何均值。

**为何危险**：Simple return 不可加，+10% 后 -10% = -1% 实际，但算术均值 = 0%。加密资产高波动率（日 ATR 5–15%）下，这个误差可达 1–3%/轮次，长期积累后 `strategy evidence` 会系统性高估真实复利收益 5–15%，导致本应触发 `drifting` 警告的策略显示为 `paper_promising`，延迟修复介入。

**Aletheia 防守**：P1-11 修复：`_lookup_strategy_evidence()` 使用 log-return 聚合（`paper_expectancy = (exp(sum(log(1+r/100))) - 1) * 100 / n`）。向后兼容保留 `paper_expectancy_simple` 字段供对比，过渡期 doctor 可同时展示两个值的差异作为诊断信号。

---

### Anti-Pattern 6：高 IVR（> 70）环境追入方向性交易，把高波动当趋势

**什么是这个问题**：高 IV 环境做多波动率是期权文献中最常见误区。高 IVR 代表市场对未来波动率定价过高，即使标的真的波动，vol crush（IV 均值回归）也会吃掉方向性收益。加密场景：BTC 暴跌 15% 后 IVR 飙到 85，RSI 读出超卖，`market_observer` 产生 long 信号，LLM 认为「超跌反弹机会」，但此时追入风险极高——vol 本身就是 priced in 的事件后状态。

**为何危险**：事件（清算、监管消息）发生后，价格已经反映了信息，高 IVR 恰恰说明市场刚刚经历了最大的不确定性；此后 vol 回归正常是大概率，但方向不确定。追入方向性交易是把「已发生的风险溢价」当「未来方向信号」。

**Aletheia 防守**：
- P1-1 IVR 计算 + ranker 权重（IVR > 80 时 score -= 20）
- P1-2 vol_crush 检测（24h RV < 72h RV 的 50% 时直接拒绝新开仓）
- 两者配合：高 IVR 环境方向性信号被压分，vol_crush 环境直接硬拦截

---

### Anti-Pattern 7：快过期的 Opportunity 执行——信号可能已完全反转

**什么是这个问题**：`trade_score` 在 opportunity 创建时一次性计算，TTL 内排序不变。一个 58 分钟前创建的 high-score opportunity 在即将到期时仍以原始高分参与排序。

**为何危险**：类比 0DTE 末期 Gamma 爆炸——快过期时原始信号的条件可能已经完全不成立。加密市场 1h 内 BTC 可移动 3–5%，RSI 可从超卖区完全回归。继续执行基于旧市场状态的信号是以过时信息做决策。

**Aletheia 防守**：
- P2-11 差异化 TTL（反转类 2 天，显著缩短高风险类型的有效窗口）
- 额外建议：在 `_evaluate_candidate()` 中对剩余 < 15% TTL 的 opportunity 加 -10 分惩罚，系统主动偏向新鲜信号

---

### Anti-Pattern 8：股票文献参数直接移植至加密市场，未经参数敏感性验证

**什么是这个问题**：Alpha 101 的参数（窗口 10/20/60 天等）在美股 T+0、日均 ATR 0.5–2% 的环境下机器优化得出。加密市场日 ATR 5–15%，价格反转周期更短，24h 无隔夜积累。

**为何危险**：直接套用相当于用错误的时间尺度测量信号。在美股「10 天相关性」捕获的是 2 周趋势，在加密市场 2 周可能经历了 3–4 次完整 regime cycle，该窗口产生的是纯噪音而非 alpha。这也是 Walk-Forward Validation（P2-8）检测的过拟合根源之一。

**Aletheia 防守**：所有从文献借鉴的窗口参数（Alpha#3 的 10 日相关性、Alpha#35 的 32 日 ts_rank、vol_regime 的 20 日 RV）在 SDD claim 中标注 `status='experimental'`，待积累 20 笔以上干净窗数据后做参数扫描。加密市场经验调整方向：10 日窗口 → 3–5 日，60 日窗口 → 14–21 日。

---

## 快速参考：加密期货适用的 Alpha 101 因子

以下因子从 WorldQuant Alpha 101 中筛选，适合加密永续合约的高波动、24h 连续、无 T+1 限制环境。加密市场参数调整原则：窗口除以 3–5（价格反转更快）；所有公式均使用 rank() 归一化避免量纲问题。

| Factor ID | 名称 | 公式（加密适配版） | 信号含义 | Aletheia 使用模块 | 实施状态 |
|-----------|------|--------------------|----------|-------------------|----------|
| **Alpha#3** | 量价背离 | `-corr(rank(open[-5:]), rank(volume[-5:]))` | 正值=价格量背离=做多信号；负值=量价同步=过热 | `market_observer` → `MarketSnapshot.pv_div` | **P1-9**（待实施） |
| **Alpha#20** | 跳空缺口 | `gap_h = (open-prev.high)/prev.high`；三方向均正且均值 > 0.3% | 跳空高开=回补概率高；反向开空 | `market_observer` → `key_observations` | **P2-14**（待实施） |
| **Alpha#30** | 方向签名 | `sum(sign(close[i]-close[i-1]) for i in [-3,-2,-1])`，取值 {-3,-1,+1,+3} | 连续 3 根同向=趋势确认；-3/+3 为极端值 | `market_observer` → `enriched_context` | **P2-5**（待实施） |
| **Alpha#35** | 底部放量 | `ts_rank(vol,16) × (1-ts_rank(strength,8)) × (1-ts_rank(return,16))`（加密缩窗至 16/8） | 乘积 > 0.8=成交量高位+价格结构低位=底部放量做多 | `ranker` → `_compute_trade_score()` | **P2-4**（待实施） |
| **Alpha#101** | CLV（收盘位置） | `(close-open)/(high-low+1e-9)`，取最近 3 根均值 | +1=完全上涨收盘；-1=完全下跌收盘；均值衡量短期买压 | `market_observer` → `enriched_context` | **P2-5**（待实施） |
| **IVR**（衍生） | 历史波动率百分位 | `(rv_20d - min(rv_series)) / (max(rv_series) - min(rv_series)) × 100`；`rv_series` 为 252 日滚动 20d RV | > 60=高波动环境；< 30=低波动环境；高 IVR 时压制方向性做多 | `ranker` → IVR 权重调整 | **P1-1**（待实施） |
| **Vol Accel**（衍生） | 波动率加速度 | `rv_5d / rv_20d`；> 1.5=波动率急剧上升；< 0.5=骤降（vol crush 信号） | 与 IVR 配合：高 IVR + 高加速 = 极端避险信号 | `ranker` + `market_observer` | **P1-1 / P1-2**（待实施） |
| **PV-Div**（衍生） | 量价背离指数 | `-corr(rank(open[-5:]), rank(volume[-5:]))`（Alpha#3 加密参数版） | 正值做多确认；负值做空确认 | `ranker` → score += 8 | **P1-9**（待实施） |
| **dir_sig**（衍生） | 3h 方向签名 | `sum(sign(Δclose) for last 3 bars)`（Alpha#30 小时级） | 用于替代 LLM 从价格序列自行推断方向，减少幻觉 | `market_observer` → `enriched_context` | **P2-5**（待实施） |
| **fund_z**（衍生） | 资金费率 Z-score | `(fund_rate - mean_30d) / std_30d` | > 2=long 极度拥挤（bear vote）；< -2=short 极度拥挤（bull vote） | `regime/detector.py` signal 4 | **P2-6**（待实施） |

**参数调整备注**：

- Alpha#3 原始窗口为 10 天，加密适配改为 5 天（价格反转周期更短）
- Alpha#35 原始窗口 32/16，加密适配改为 16/8（减半）
- 所有因子在 SDD claim 中标注 `status='experimental'`，待 20 笔以上干净窗数据后做参数敏感性扫描
- `rank()` 使用 `scipy.stats.rankdata`，保持 [0,1] 归一化，避免量纲问题（Alpha 101 核心工程原则）
