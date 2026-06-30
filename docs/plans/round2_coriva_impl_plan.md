# Round 2 Coriva 知识转化 — SDD 实施计划

> 生成时间：2026-06-30  
> 信息来源：`docs/research/coriva_knowledge_extraction.md` + 第一性原理代码审查  
> 方法：每项改动先对照当前代码 ground truth 验证「确实未修」，再给出精确行号 + 完整替换代码 + 对应 SpecClaim

---

## 前置原则

1. **最小改动**：不改 trust boundary（`risk/`、`execution/`、`auth.py`）
2. **SDD 先行**：先写 claim（`status="missing"`），改完变 `status="implemented"`
3. **不破接口**：`_compute_trade_score` 暂维持返回 `float`；breakdown 作 optional 扩展
4. **先 P0，再 P1**：P0 之间互不依赖，可并行；P1-3(breakdown) 是 P1-6(blame_step) 的前置
5. **跑完必测**：每批改完 `uv run pytest tests/specs/ -q`（< 2s）确认 zero regression

---

## BATCH A — `paper_trader_ranker.py` (P0-1 / P0-2 / P0-3 / P0-5)

### A1. SDD Claims（先加，status=missing，测试 RED）

在 `tests/specs/spec_operator_loop.py` 文件末尾追加：

```python
# ---------------------------------------------------------------------------
# Round 2 Ranker Signal-Quality Claims (Coriva impl plan 2026-06-30)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "OL-SCORE-NORM", "operator_layer",
        "§ranker confidence_score normalization",
        "L2b",
        "confidence_score 贡献上限为 50 分（经 clip_norm 到 [0,1] 再乘 50）"
        "——防止 0-100 原始值直接主导 regime/factor 信号（Round 1 方向集中根因）。"
        "paper_trader_ranker._compute_trade_score 中 conf_score 直接加分路径必须被替换。",
        status="missing",
    ),
)
def test_score_norm_confidence_capped():
    """confidence_score=100 时贡献不超过 50 分。"""
    from aletheia.agents.paper_trader_ranker import _compute_trade_score
    from aletheia.regime.detector import MarketRegime

    import unittest.mock as mock
    neutral_regime = MarketRegime(state="sideways", confidence=0.3, source="test")
    with mock.patch("aletheia.agents.paper_trader_ranker.get_current_regime",
                    return_value=neutral_regime):
        high_conf_opp = {"confidence_score": 100, "symbol": "BTC/USDT",
                         "scanner_family": "test"}
        score = _compute_trade_score(high_conf_opp, None, {"status": "watch"}, "high")
    assert score <= 75.0, (
        f"confidence_score=100 贡献超出预期上限 75（实际={score}），"
        "regime/factor 信号将被压制 — 检查 clip_norm 是否正确"
    )


@spec_claim(
    SpecClaim(
        "OL-SIDEWAYS-TREND-TAX", "operator_layer",
        "§ranker sideways regime trend tax",
        "L2b",
        "sideways regime (confidence>0.6) + trend-family scanner → score -= 25。"
        "当前 sideways 分支完全缺失，CTA 文献实证其为趋势跟踪最差 regime。",
        status="missing",
    ),
)
def test_sideways_trend_tax_applied():
    """sideways + breakout scanner 应扣 25 分。"""
    from aletheia.agents.paper_trader_ranker import _compute_trade_score
    from aletheia.regime.detector import MarketRegime

    import unittest.mock as mock
    sideways = MarketRegime(state="sideways", confidence=0.75, source="test")
    with mock.patch("aletheia.agents.paper_trader_ranker.get_current_regime",
                    return_value=sideways):
        opp = {"confidence_score": 50, "symbol": "ETH/USDT", "scanner_family": "breakout"}
        score_trend = _compute_trade_score(opp, None, {"status": "watch"}, "medium",
                                           direction="long")
        opp2 = dict(opp, scanner_family="reversion")
        score_rev = _compute_trade_score(opp2, None, {"status": "watch"}, "medium",
                                          direction="long")
    assert score_trend < score_rev - 15, (
        "sideways + breakout 应比 reversion 低至少 15 分（trend_tax 未触发）"
    )


@spec_claim(
    SpecClaim(
        "OL-FACTOR-SMOOTH", "operator_layer",
        "§ranker factor IC smooth scoring",
        "L2b",
        "factor_status != blocked_candidate 时用 IC 代理连续加权（(win_rate-0.5)*2*20），"
        "替换离散 ±5/±20 跳跃。win_rate 已由 lookup_factor_signal 返回，无新依赖。",
        status="missing",
    ),
)
def test_factor_scoring_is_continuous():
    """win_rate=0.52 和 win_rate=0.60 的分差应 < 10（连续），而非离散跳 15。"""
    from aletheia.agents.paper_trader_ranker import _compute_trade_score
    from aletheia.regime.detector import MarketRegime

    import unittest.mock as mock
    flat = MarketRegime(state="sideways", confidence=0.1, source="test")
    with mock.patch("aletheia.agents.paper_trader_ranker.get_current_regime",
                    return_value=flat):
        base = {"confidence_score": 50, "symbol": "BTC/USDT", "scanner_family": "test"}
        sig_low  = {"status": "active", "win_rate": 0.52, "trade_count": 10}
        sig_high = {"status": "active", "win_rate": 0.60, "trade_count": 10}
        s_low  = _compute_trade_score(base, None, sig_low, "medium", direction="long")
        s_high = _compute_trade_score(base, None, sig_high, "medium", direction="long")
    delta = s_high - s_low
    assert 0 < delta < 10, (
        f"win_rate 0.52→0.60 分差 {delta:.1f} 超出连续范围 (0,10)；"
        "factor 评分仍为离散跳跃，检查 IC 代理实现"
    )


@spec_claim(
    SpecClaim(
        "OL-REGIME-SMOOTH", "operator_layer",
        "§ranker regime confidence smooth weighting",
        "L2b",
        "regime 加分乘以 confidence^1.5（连续），消除 >0.5 断崖。"
        "confidence=0.49 vs 0.51 的分差应 < 5（原差距 ±20）。",
        status="missing",
    ),
)
def test_regime_scoring_no_cliff():
    """regime.confidence=0.49 vs 0.51 分差应 < 5（无断崖）。"""
    from aletheia.agents.paper_trader_ranker import _compute_trade_score
    from aletheia.regime.detector import MarketRegime

    import unittest.mock as mock
    base_opp = {"confidence_score": 50, "symbol": "BTC/USDT", "scanner_family": "test"}

    with mock.patch("aletheia.agents.paper_trader_ranker.get_current_regime",
                    return_value=MarketRegime(state="bear", confidence=0.49, source="t")):
        s_below = _compute_trade_score(base_opp, None, {"status": "watch"}, "medium",
                                        direction="short")
    with mock.patch("aletheia.agents.paper_trader_ranker.get_current_regime",
                    return_value=MarketRegime(state="bear", confidence=0.51, source="t")):
        s_above = _compute_trade_score(base_opp, None, {"status": "watch"}, "medium",
                                        direction="short")
    assert abs(s_above - s_below) < 5, (
        f"confidence 0.49→0.51 分差 {abs(s_above-s_below):.1f} 超出 5 分，"
        "regime 评分仍有断崖效应"
    )
```

### A2. 代码修改：`paper_trader_ranker.py`

**修改位置 1：L330-341（confidence + universe 量纲归一化）**

```python
# 替换原来 L330-341
_clip_norm = lambda v, lo, hi: (min(max(float(v or 0), lo), hi) - lo) / (hi - lo + 1e-8)

# Confidence contribution (P0-1: cap at 50, normalize 0-100)
conf_score = opp.get("confidence_score")
if conf_score is not None:
    score += 50 * _clip_norm(conf_score, 0, 100)
else:
    score += _CONFIDENCE_SCORE_DEFAULT.get(confidence, 20)

# Universe metadata (P0-1: clip normalize)
if universe_member:
    score += 25 * _clip_norm(universe_member.get("total_score") or 0, 0, 100)
    score += 10 * _clip_norm(universe_member.get("tradability_score") or 0, 0, 100)
    score -= float(universe_member.get("late_entry_penalty") or 0)
    score -= float(universe_member.get("crowding_risk") or 0)
```

**修改位置 2：L345-353（factor IC 代理连续加权，P0-3）**

```python
# 替换原来 L345-353
factor_status = factor_signal.get("status", "watch")
factor_win_rate = factor_signal.get("win_rate")
factor_trade_count = factor_signal.get("trade_count", 0)

if factor_status == "blocked_candidate":
    score -= 50
else:
    # P0-3: IC proxy — continuous (win_rate-0.5)*2 * 20, with sample weight
    if factor_win_rate is not None and factor_trade_count >= 3:
        ic_proxy = (factor_win_rate - 0.5) * 2
        sample_weight = min(1.0, factor_trade_count / 10.0)
        score += 20 * ic_proxy * sample_weight
    else:
        score -= 5  # no history: mild penalty
```

**修改位置 3：L372-381（regime 连续化 + sideways 分支，P0-2 + P0-5）**

```python
# 替换原来 L372-381
regime = get_current_regime()

_BASE_REGIME_DELTA = {
    ("bear", "short"): +20,
    ("bear", "long"):  -25,
    ("bull", "long"):  +20,
    ("bull", "short"): -20,
}

if regime.state in ("bear", "bull"):
    # P0-5: smooth weighting — avoids 0.5 cliff
    delta = _BASE_REGIME_DELTA.get((regime.state, direction), 0)
    weight = regime.confidence ** 1.5
    score += delta * weight
elif regime.state == "volatile":
    score -= 10 * (regime.confidence ** 1.5)
elif regime.state == "sideways" and regime.confidence > 0.6:
    # P0-2: trend-family scanners perform worst in sideways (CTA literature)
    _TREND_FAMILIES = {"breakout", "momentum", "ma_crossover", "trend"}
    scanner = (opp.get("scanner_family") or "").lower()
    if any(t in scanner for t in _TREND_FAMILIES):
        score -= 25
    elif "reversion" in scanner or "arb" in scanner:
        score += 15
```

**验证命令（改后立即跑）：**
```
uv run pytest tests/specs/spec_operator_loop.py -q -k "SCORE_NORM or SIDEWAYS or FACTOR_SMOOTH or REGIME_SMOOTH"
```

---

## BATCH B — `factor_feedback.py` (P0-4 / P1-4)

### B1. SDD Claims

在 `tests/specs/spec_pipeline_risk_gate.py` 或新建 `tests/specs/spec_factor_feedback.py` 末尾追加：

> **注意**：新建 spec 文件时须在 `tests/test_spec_conformance.py` 的 hardcoded import 列表中加入，否则 pytest 不收集（见 memory `aletheia-sdd-spec-collection-trap`）。

```python
@spec_claim(
    SpecClaim(
        "FF-MIN-TRADES", "factor_feedback",
        "§classify_factor min_trades_for_preferred",
        "L2b",
        "preferred 状态需要 trade_count >= 8（原 3）；"
        "3 笔的 win_rate 置信区间横跨 0-1，不可信。",
        status="missing",
    ),
)
def test_factor_preferred_requires_8_trades():
    from aletheia.analytics.factor_feedback import _classify_factor
    status, _ = _classify_factor(7, 3, 0.5, 0.55, 2.0)
    assert status != "preferred", (
        "7 笔不应晋升 preferred（需要 >= 8）"
    )
    status8, _ = _classify_factor(8, 3, 0.5, 0.55, 2.0)
    assert status8 == "preferred"


@spec_claim(
    SpecClaim(
        "FF-OUTLIER-GATE", "factor_feedback",
        "§classify_factor outlier_driven_pf_check",
        "L2b",
        "PF > 5.0 且 n < 15 → 返回 active 而非 preferred（单笔异常大盈利代理）。",
        status="missing",
    ),
)
def test_factor_outlier_pf_not_preferred():
    from aletheia.analytics.factor_feedback import _classify_factor
    status, reason = _classify_factor(10, 3, 0.8, 0.60, 6.5)
    assert status == "active", (
        f"PF=6.5,n=10 应返回 active（outlier gate），得到 {status!r}"
    )
    assert "outlier" in reason


@spec_claim(
    SpecClaim(
        "FF-HARD-BLOCKED", "factor_feedback",
        "§classify_factor hard_blocked status",
        "L2b",
        "trade_count >= 10 且 win_rate < 0.30 且 expectancy < -0.5 → 返回 hard_blocked。",
        status="missing",
    ),
)
def test_factor_hard_blocked_status():
    from aletheia.analytics.factor_feedback import _classify_factor
    status, reason = _classify_factor(12, 3, -0.8, 0.25, 0.3)
    assert status == "hard_blocked", (
        f"应返回 hard_blocked，得到 {status!r}"
    )
```

### B2. 代码修改：`factor_feedback.py`

**修改位置：L179-196（`_classify_factor` 函数全量替换）**

```python
_MIN_TRADES_FOR_PREFERRED = 8
_MIN_TRADES_FOR_HARD_BLOCKED = 10


def _classify_factor(
    trade_count: int,
    min_trades: int,
    expectancy: float,
    win_rate: float,
    profit_factor: float,
) -> tuple[str, str]:
    """Classify a factor into watch/blocked_candidate/hard_blocked/active/preferred."""
    if trade_count < min_trades:
        return "watch", f"insufficient_data({trade_count}/{min_trades})"

    # P1-4: hard_blocked — strong negative signal with sufficient evidence
    if (trade_count >= _MIN_TRADES_FOR_HARD_BLOCKED
            and win_rate < 0.30 and expectancy < -0.5):
        return "hard_blocked", f"hard_blocked:wr={win_rate:.2%}_n={trade_count}"

    if expectancy < 0 and win_rate < 0.40:
        return "blocked_candidate", (
            f"negative_expectancy({expectancy:.4f})_low_winrate({win_rate:.2%})"
        )

    # P0-4: outlier detection — high PF with small sample may be single-trade outlier
    if profit_factor > 5.0 and trade_count < 15:
        return "active", f"possible_outlier_driven(pf={profit_factor:.1f},n={trade_count})"

    # P0-4: require n >= 8 and win_rate >= 0.45 for preferred
    if (expectancy > 0 and profit_factor >= 1.2
            and win_rate >= 0.45
            and trade_count >= _MIN_TRADES_FOR_PREFERRED):
        return "preferred", (
            f"positive_expectancy({expectancy:.4f})_pf({profit_factor:.2f})"
            f"_wr={win_rate:.2%}_n={trade_count}"
        )

    return "active", "meets_minimum_criteria"
```

**同步修改 ranker：P1-4 hard_blocked 硬拦截**

在 `paper_trader_ranker.py` `_evaluate_candidate()` 中 factor_signal lookup 之后（约 L276）加入：

```python
# P1-4: hard_blocked is a hard reject (not just -50 penalty)
if factor_signal.get("status") == "hard_blocked":
    return _make_candidate(
        opp, skip_reason="factor_hard_blocked",
        direction=direction, strategy_id=strategy_id,
        factor_signal=factor_signal,
        universe_member=universe_member,
    )
```

**验证命令：**
```
uv run pytest tests/specs/spec_factor_feedback.py -q
```

---

## BATCH C — `sizing.py` (P1-5)

### C1. SDD Claims（加入 `tests/specs/spec_pipeline_risk_gate.py`）

```python
@spec_claim(
    SpecClaim(
        "SZ-SAMPLE-KELLY", "risk_sizing",
        "§vol_target_size sample_aware_kelly",
        "L2b",
        "vol_target_size 接受 paper_trade_count 参数；< 10 笔时 effective_kelly=0.1，"
        "10-30 笔线性爬升到 base_kelly。新策略自动降仓，成熟策略行为不变。",
        status="missing",
    ),
)
def test_vol_target_size_accepts_paper_trade_count():
    import inspect
    from aletheia.trading import sizing
    sig = inspect.signature(sizing.vol_target_size)
    assert "paper_trade_count" in sig.parameters, (
        "vol_target_size 缺少 paper_trade_count 参数 — SZ-SAMPLE-KELLY 未实现"
    )

@spec_claim(
    SpecClaim(
        "SZ-SAMPLE-KELLY-SCALE", "risk_sizing",
        "§vol_target_size sample_kelly_reduces_size",
        "L2b",
        "paper_trade_count=3 时尺寸应 <= paper_trade_count=50 时的 50%。",
        status="missing",
    ),
)
def test_vol_target_size_reduced_for_new_strategy():
    from aletheia.trading.sizing import vol_target_size
    try:
        sz_new = vol_target_size("BTC/USDT", conviction=70, paper_trade_count=3)
        sz_mature = vol_target_size("BTC/USDT", conviction=70, paper_trade_count=50)
        assert sz_new <= sz_mature * 0.5, (
            f"新策略(3笔) size={sz_new} 应 <= 成熟策略(50笔) size={sz_mature} 的 50%"
        )
    except Exception as e:
        pytest.fail(f"vol_target_size with paper_trade_count failed: {e}")
```

### C2. 代码修改：`sizing.py`

**修改位置：L73-111（`vol_target_size` 函数签名 + Kelly 计算）**

```python
def vol_target_size(
    symbol: str,
    *,
    conviction: int = 70,
    drawdown_pct: float = 0.0,
    atr_pct_override: float | None = None,
    paper_trade_count: int = 0,    # P1-5: sample-aware Kelly
) -> float:
    """Unified volatility-target sizing with sample-aware Kelly fraction.

    paper_trade_count < 10: effective_kelly = 0.10 (one-tenth Kelly)
    paper_trade_count 10-30: linear ramp to base_kelly
    paper_trade_count >= 30: base_kelly unchanged
    """
    from aletheia.config.loader import load_config

    cfg = load_config()
    base = cfg.trading.default_notional_usdt
    base_kelly = cfg.trading.vol_kelly_cap_fraction

    # P1-5: sample-aware Kelly fraction
    if paper_trade_count < 10:
        effective_kelly = 0.1
    elif paper_trade_count < 30:
        t = (paper_trade_count - 10) / 20.0
        effective_kelly = 0.1 + t * (base_kelly - 0.1)
    else:
        effective_kelly = base_kelly

    if not cfg.trading.vol_sizing_enabled:
        return compute_position_size(conviction, base, drawdown_pct=drawdown_pct,
                                     kelly_fraction=effective_kelly)

    atr_pct = atr_pct_override
    if atr_pct is None:
        atr_pct = _lookup_atr_pct(symbol)

    if not atr_pct or atr_pct <= 0:
        return compute_position_size(conviction, base, drawdown_pct=drawdown_pct,
                                     kelly_fraction=effective_kelly)

    risk_budget = cfg.trading.vol_target_risk_usdt
    raw_size = risk_budget / (atr_pct / 100.0)

    kelly_cap = base * (effective_kelly * 4) * _MAX_MULTIPLIER
    sized = min(raw_size, kelly_cap)

    return compute_position_size(conviction, sized, drawdown_pct=drawdown_pct,
                                 kelly_fraction=effective_kelly)
```

**调用方修改：`paper_trader.py` 中 `vol_target_size` 调用处**

确认文件中有 `paper_trade_count` 传入。搜索 `vol_target_size` 调用位置后，补传：
```python
# 在 _lookup_strategy_evidence() 返回值中取 paper_trade_count
sz = vol_target_size(
    symbol,
    conviction=conviction,
    drawdown_pct=drawdown_pct,
    paper_trade_count=strategy_evidence.get("paper_trade_count", 0) if strategy_evidence else 0,
)
```

**验证命令：**
```
uv run pytest tests/specs/spec_pipeline_risk_gate.py -q -k "SAMPLE_KELLY"
```

---

## BATCH D — `market_observer.py` (P1-7)

### D1. SDD Claims（加入 `tests/specs/spec_subsystem_observers.py`）

```python
@spec_claim(
    SpecClaim(
        "OBS-PRE-FILTER", "market_observer",
        "§market_observer pre_filter_no_signal",
        "L2a",
        "_has_sufficient_signal() 函数存在，且在 LLM 调用前被调用。"
        "RSI 偏离 < 12 + ATR < 0.8% + 无关键词 → 跳过 LLM 节省预算。",
        status="missing",
    ),
)
def test_observer_has_pre_filter_function():
    import inspect
    import aletheia.agents.market_observer as mo
    assert hasattr(mo, "_has_sufficient_signal"), (
        "_has_sufficient_signal 函数不存在 — OBS-PRE-FILTER 未实现"
    )
    src = inspect.getsource(mo._analyze_symbol)
    assert "_has_sufficient_signal" in src, (
        "_analyze_symbol 未调用 _has_sufficient_signal — pre-filter 未接入"
    )
```

### D2. 代码修改：`market_observer.py`

在 `_analyze_symbol()` 中 `build_market_context()` 调用之后、`llm_structured()` 之前加入：

```python
def _has_sufficient_signal(context_str: str) -> bool:
    """Deterministic pre-filter: skip LLM if no actionable signal detected.

    Checks RSI deviation from 50, ATR%, and momentum keywords.
    Must not import anything heavy — runs before every LLM call.
    """
    import re
    rsi_match = re.search(r'RSI[^|]*\|[^|]*?(\d+\.?\d*)', context_str, re.IGNORECASE)
    if rsi_match:
        try:
            if abs(float(rsi_match.group(1)) - 50) > 12:
                return True
        except ValueError:
            pass
    atr_match = re.search(r'ATR[^|]*\|[^|]*?(\d+\.?\d+)%', context_str, re.IGNORECASE)
    if atr_match:
        try:
            if float(atr_match.group(1)) > 0.8:
                return True
        except ValueError:
            pass
    keywords = ("breakout", "breakdown", "oversold", "overbought", "squeeze",
                "divergence", "spike")
    if any(kw in context_str.lower() for kw in keywords):
        return True
    return False
```

在调用 `llm_structured` 前插入检查（约 `_analyze_symbol` 函数内）：

```python
# pre-filter: skip LLM for symbols with no actionable signal
cfg = load_config()
force_full = getattr(getattr(cfg, "observer", None), "force_full_scan", False)
if not force_full and not _has_sufficient_signal(context_str):
    logger.debug("pre-filter: skipping LLM for %s — no sufficient signal", symbol)
    return None, None
```

**验证命令：**
```
uv run pytest tests/specs/spec_subsystem_observers.py -q -k "PRE_FILTER"
```

---

## BATCH E — P1 后续（依赖 Batch A 的 P1-3 / P1-6 / P1-8）

这批改动依赖 Batch A 落地，单独作第二 sprint。

| 编号 | 模块 | 动作 | 复杂度 | 前置 |
|------|------|------|--------|------|
| P1-1 | `context_builder.py` | 加 IVR + vol_acceleration 字段 | 小 | 无 |
| P1-2 | `paper_trader_ranker.py` | vol_crush 检测硬拒绝 | 小 | 无 |
| P1-3 | `paper_trader_ranker.py` | `_compute_trade_score` 返回 `(score, breakdown)` | 中 | Batch A 完成 |
| P1-6 | `trade_attribution.py` | `blame_step` 写入 LessonItem.category | 小 | P1-3 |
| P1-8 | `strategy_evidence.py` | Sortino/Calmar 计算写入 evidence | 小 | 无 |
| P1-9 | `context_builder.py` | PV-divergence 写入 enriched context | 小 | 无 |

> P2（Kelly full-Kelly drawdown floor 降到 0、score breakdown 历史 IC 分析）价值低、改动面广，推迟到 Round 2 末。

---

## 执行顺序

```
Batch A (ranker) → Batch B (factor_feedback) → Batch C (sizing) → Batch D (observer)
                                        ↓
                              Batch E (sprint 2)
```

Batch A~D 互不依赖，可在同一 sprint 逐批改完。每批：
1. 加 SpecClaim（RED）
2. 改代码
3. `uv run pytest tests/specs/ -q`（GREEN）
4. `aletheia doctor` 验证无新 WARN

---

## 检查清单（改完后对账）

- [ ] `uv run pytest tests/specs/ -q` — 全 GREEN，无 RED
- [ ] `uv run pytest tests/ -q --tb=line` — 全量无回归（后台跑）
- [ ] `aletheia doctor` — WARN count 不增加
- [ ] ranker score 分布检查：`confidence_score=100` 的候选最高分 ≤ 75
- [ ] bear regime 下 short 候选平均 score > long 候选平均 score
- [ ] `factor_feedback.build_factor_feedback()` 中无 `preferred` 出现于 n < 8 的 factor
