# Universe Edge 修复计划

> 目标：让 Universe Scanner 从"接近随机"变成"可度量优于随机"
> 原则：最小改动、复用现有基础设施、可逐步验证每一步效果
> 度量标准：各 family 的 forward outcome win_rate 在 1h 和 4h horizon 上显著优于 50%

---

## 一、问题诊断总结

### 6 个根因

| # | 问题 | 影响 | 所在文件 |
|---|------|------|----------|
| 1 | 所有 Feature 来自同一组 30 根 1h K 线 | return_4h 只是 1h 回看 4 根，无独立 MTF | `features.py:83-101` |
| 2 | Momentum scanner 用 `abs(return)` 计分 | 选的是"波动大"而非"有趋势" | `scanners.py:22-31` |
| 3 | `_infer_direction()` ≈ `sign(ret_1h)` | 1h 自相关≈0，等于抛硬币 | `signal_emitter.py:186-214` |
| 4 | 三个 Scanner 测量同一个东西 | momentum/volatility 选出同批币 | `scanners.py` 全文 |
| 5 | Calibration max ±5.25 分 | 无法从噪声中加权出信号 | `allocator.py:28-56` |
| 6 | OI/Funding 已有字段但未被主流 scanner 使用 | 领先指标被浪费 | `features.py:109-116` |

### 数据证据

```
momentum:        15m=49.0%  1h=48.5%  4h=44.1%  24h=17.1%  ← 24h反向信号
volatility:      15m=49.6%  1h=46.6%  4h=41.3%  24h=31.3%
mean_reversion:  15m=45.3%  1h=46.6%  4h=49.1%  24h=32.8%
```

---

## 二、外部实践参考

### VergeX AI500 关键设计

1. **AI500 是 Universe（候选池）**，不是预测模型
2. **数据维度**：Price + Volume + Funding + OI + Netflow（5维）
3. **方向由池属性决定**：Netflow Top pool = long，Netflow Low pool = short
4. **多信号对齐原则**：Price + OI + Funding 三者一致才入场，冲突 → WAIT
5. **多时间框架**：4H 定方向、1H 定结构、15m 定入场
6. **不追已跑币**：超过 1.5 ATR 的蜡烛不追

### Freqtrade Pairlist 关键设计

1. **管道式链式过滤器**：Generator → Filter → Filter → Final
2. **PerformanceFilter**：历史 profit_ratio 直接影响排序（差表现对被降权/剔除）
3. **VolatilityFilter**：log-return 标准差，设 min/max 波动率窗口
4. **RangeStabilityFilter**：价格区间稳定性评估
5. **PercentChangePairList**：可按涨跌幅 asc/desc 排序选币

### Breakout Scanner 关键设计

1. **六要素确认**：Relative Strength + New High + Volume Surge + Momentum + Trend
2. **Volume 确认是核心**：2x+ 均量才算有效突破
3. **逆向信号**：RSI 极端 + 放量 + BB 边界 = exhaustion

---

## 三、修复方案（4 Phase）

### Phase 0：立即止血（~2h 工作量）

**目标**：消除最差信号，win_rate 预期从 17%→40%+（momentum 24h）

#### 0.1 Momentum 24h 过度延伸保护

**文件**: `aletheia/universe/signal_emitter.py:186-194`

**改动**：在 momentum direction 推断前，如果 24h 已大幅运动则拒绝发信号

```python
if family == "momentum":
    # 24h 已过度延伸 → 均值回归区域，不追
    if ret_24h is not None and abs(ret_24h) > 10.0:
        return None

    # 原有逻辑保留但加强...
```

**为什么有效**：当前 momentum 24h win_rate=17% 的核心原因是选出了"24h涨/跌最多的币然后追"——这些币高概率均值回归。过滤掉这批即消除反向信号。

#### 0.2 方向冲突 → WAIT

**文件**: `aletheia/universe/signal_emitter.py:186-214`

**改动**：所有 family 的方向推断增加 ret_1h vs ret_4h 冲突检测

```python
if family == "momentum":
    if ret_24h is not None and abs(ret_24h) > 10.0:
        return None
    # 冲突检测：1h 和 4h 方向不一致 → 不发信号
    if ret_1h is not None and ret_4h is not None:
        if ret_1h * ret_4h < 0:  # 符号相反
            return None
    # ... 后续方向推断
```

**为什么有效**：借鉴 VergeX 的"If signals disagree → WAIT"原则。当 1h 和 4h 方向矛盾时，任何方向预测都是噪声。

#### 0.3 Funding Rate 作为方向确认扩散到全 family

**文件**: `aletheia/universe/signal_emitter.py:176-230`

**改动**：对 momentum 和 volatility，当有 funding_rate 数据时，用作方向过滤

```python
def _infer_direction(family, feat):
    ret_1h = feat.get("return_1h")
    ret_4h = feat.get("return_4h")
    rsi = feat.get("rsi_14")
    funding = feat.get("funding_rate")

    # 通用方向确认：funding 与 direction 冲突 → None
    def _funding_conflicts(direction: str) -> bool:
        if funding is None:
            return False  # 无数据不阻拦
        if direction == "long" and funding > 0.01:
            return True   # 想做多但多头拥挤（付高费）→ 冲突
        if direction == "short" and funding < -0.01:
            return True   # 想做空但空头拥挤 → 冲突
        return False
```

**为什么有效**：Funding rate 是领先指标（反映当前持仓拥挤度）。VergeX 策略在 Price/OI/Funding 冲突时强制 WAIT。

#### 0.4 Volatility scanner 方向从 `sign(ret_1h)` 改为 `sign(ret_4h)`

**文件**: `aletheia/universe/signal_emitter.py:210-214`

**改动**：

```python
if family == "volatility":
    # 用 4h 方向代替 1h（4h 自相关略高于 1h）
    if ret_4h is not None and ret_4h != 0:
        direction = "long" if ret_4h > 0 else "short"
        if _funding_conflicts(direction):
            return None
        return direction
    return None
```

**为什么有效**：`sign(ret_1h)` 的自相关在加密市场中几乎为零。`sign(ret_4h)` 略有持续性（尤其在高波动期），但仍需 funding 确认。

---

### Phase 1：特征维度升级（~4h 工作量）

**目标**：引入有预测力的信号，脱离"只看价格"的局限

#### 1.1 新增 Volume Ratio（成交量变化率）

**文件**: `aletheia/universe/features.py:96-102`

**改动**：在现有 volume_1h 计算后新增

```python
# 成交量比率：当前1h量 / 过去24h均量
vol_ratio = None
if volumes_1h and len(volumes_1h) >= 24:
    avg_vol_24h = sum(volumes_1h[-24:]) / 24
    if avg_vol_24h > 0:
        vol_ratio = volumes_1h[-1] / avg_vol_24h

feat["vol_ratio"] = vol_ratio
```

**注意**：`indicators.py:166` 已有 `volume_sma_ratio()`，可以直接复用！

```python
from aletheia.trading.indicators import volume_sma_ratio
vol_ratio = volume_sma_ratio(volumes_1h, 20) if len(volumes_1h) >= 20 else None
feat["vol_ratio"] = vol_ratio
```

**为什么有效**：放量是唯一在学术上被广泛证实有持续性的技术信号。VergeX/breakout scanner 都要求 2x-3x 均量确认。

#### 1.2 新增 Relative Strength（相对 BTC 强度）

**文件**: `aletheia/universe/features.py` + `aletheia/universe/service.py`

**设计**：在 `collect_features()` 开始时先拉取 BTC 的 24h/4h return，传入每个币的 feature 计算

```python
# 在 collect_features() 顶部
btc_ticker = tickers.get("BTC/USDT")
btc_ohlcv = await exchange.fetch_ohlcv("BTC/USDT", "1h", limit=30)
btc_closes = [float(c[4]) for c in btc_ohlcv]
btc_return_24h = _pct_return(btc_closes, 24)
btc_return_4h = _pct_return(btc_closes, 4)

# 在每个币的 feature dict 中
relative_strength_24h = (return_24h - btc_return_24h) if (return_24h is not None and btc_return_24h is not None) else None
relative_strength_4h = (return_4h - btc_return_4h) if (return_4h is not None and btc_return_4h is not None) else None
feat["relative_strength_24h"] = relative_strength_24h
feat["relative_strength_4h"] = relative_strength_4h
```

**为什么有效**：
- 消除大盘 beta：BTC 涨 5% 时所有币都涨 → 绝对回报无差异化能力
- 相对强度是 Fama-French cross-sectional momentum 的核心（学术上最持久的因子之一）
- 在加密市场中 BTC 是 beta 载体，剥离后剩下的才是个币 alpha

#### 1.3 新增 OI Delta 实时化

**文件**: `aletheia/universe/features.py:109-116`

**当前问题**：OI 数据从数据库读取（`repo.latest_open_interest(symbol)`），可能过期数小时

**改动**：对有永续合约的币，在 OHLCV 拉取的同时实时取 funding + OI

```python
# 在 _fetch_single_feature 中，对 perp 可用的币
if is_perp or ccxt_sym + ":USDT" in exchange.markets:
    try:
        perp_sym = to_ccxt_perp(symbol)
        fr_data = await exchange.fetch_funding_rate(perp_sym)
        funding_rate = fr_data.get("fundingRate")
    except Exception:
        pass  # 回落到 DB 缓存
```

**成本**：每个币多一个 API call（可限制在 top-50 volume 的币上）

#### 1.4 新增 Bollinger Band Squeeze（波动率压缩检测）

**文件**: `aletheia/universe/features.py`

**依赖**: `indicators.py:96-106` 已有 `bollinger()`

```python
from aletheia.trading.indicators import bollinger

bb = bollinger(closes_1h, 20, 2.0) if len(closes_1h) >= 20 else None
bb_width = None
bb_squeeze = None
if bb:
    lower, mid, upper = bb
    bb_width = (upper - lower) / mid * 100 if mid > 0 else None  # 占比%
    # Squeeze: 当前宽度 vs 历史宽度
    if len(closes_1h) >= 30:
        # 用最近10根的 BB 宽度均值 vs 之前20根
        # 简化：当前 bb_width < 2.0% 视为压缩态
        pass

feat["bb_width_pct"] = bb_width
```

**为什么有效**：波动率压缩后的方向性突破是少数有统计优势的模式。VergeX/breakout scanner 都隐含这个逻辑（ATR expansion after consolidation）。

---

### Phase 2：Scanner 逻辑重写（~4h 工作量）

**目标**：从"选波动大的"变为"选有统计持续性特征的"

#### 2.1 Momentum Scanner → Trend Continuation Scanner

**文件**: `aletheia/universe/scanners.py:10-59`

**核心改变**：
1. 用相对强度代替绝对回报
2. 要求成交量确认
3. 方向内生于评分逻辑（不是后贴）

```python
def score_momentum(feat: dict) -> tuple[float, list[str], str | None]:
    """Score trend continuation: relative strength + volume confirmation.
    
    Returns (score, reasons, direction).
    direction 内生于评分逻辑 — None 表示无清晰方向。
    """
    score = 0.0
    reasons = []
    direction = None

    rs_24h = feat.get("relative_strength_24h")
    rs_4h = feat.get("relative_strength_4h")
    ret_4h = feat.get("return_4h")
    vol_ratio = feat.get("vol_ratio")
    oi_delta = feat.get("oi_delta_1h")
    funding = feat.get("funding_rate")

    # === 相对强度（核心信号）===
    if rs_24h is not None:
        if rs_24h > 5.0:
            score += 30.0
            direction = "long"
            reasons.append(f"RS24h +{rs_24h:.1f}% vs BTC")
        elif rs_24h > 2.5:
            score += 20.0
            direction = "long"
            reasons.append(f"RS24h +{rs_24h:.1f}% vs BTC")
        elif rs_24h < -5.0:
            score += 30.0
            direction = "short"
            reasons.append(f"RS24h {rs_24h:.1f}% vs BTC")
        elif rs_24h < -2.5:
            score += 20.0
            direction = "short"
            reasons.append(f"RS24h {rs_24h:.1f}% vs BTC")

    # === 4h 方向一致性 ===
    if rs_4h is not None and direction is not None:
        if (direction == "long" and rs_4h > 1.0) or \
           (direction == "short" and rs_4h < -1.0):
            score += 15.0
            reasons.append("4h RS confirms")

    # === 成交量确认 ===
    if vol_ratio is not None:
        if vol_ratio > 3.0:
            score += 20.0
            reasons.append(f"vol spike {vol_ratio:.1f}x")
        elif vol_ratio > 1.5:
            score += 10.0
            reasons.append(f"vol elevated {vol_ratio:.1f}x")

    # === OI 确认（新钱入场）===
    if oi_delta is not None and direction is not None:
        if direction == "long" and oi_delta > 5:
            score += 10.0
            reasons.append(f"OI +{oi_delta:.0f}%")
        elif direction == "short" and oi_delta < -5:
            score += 10.0
            reasons.append(f"OI {oi_delta:.0f}%")

    # === Funding 冲突检测（扣分）===
    if funding is not None and direction is not None:
        if direction == "long" and funding > 0.01:
            score -= 15.0
            reasons.append("⚠ funding crowded long")
        elif direction == "short" and funding < -0.01:
            score -= 15.0
            reasons.append("⚠ funding crowded short")

    # 无方向 → score 归零
    if direction is None:
        score = 0.0

    return max(0.0, min(100.0, score)), reasons, direction
```

#### 2.2 Volatility Scanner → Breakout Scanner

**文件**: `aletheia/universe/scanners.py:113-152`

**核心改变**：
1. BB squeeze → expansion 检测
2. 必须有 volume spike 确认
3. 方向由突破方向决定（4h 级别）

```python
def score_volatility(feat: dict) -> tuple[float, list[str], str | None]:
    """Score volatility breakout: squeeze→expansion + volume + direction.
    
    Returns (score, reasons, direction).
    """
    score = 0.0
    reasons = []
    direction = None

    atr = feat.get("atr_pct")
    ret_4h = feat.get("return_4h")
    vol_ratio = feat.get("vol_ratio")
    bb_width = feat.get("bb_width_pct")

    # === ATR expansion ===
    if atr is not None:
        if atr > 5.0:
            score += 30.0
            reasons.append(f"ATR {atr:.1f}% breakout")
        elif atr > 3.0:
            score += 20.0
            reasons.append(f"ATR {atr:.1f}% expanding")

    # === Volume spike（必要条件）===
    if vol_ratio is not None and vol_ratio > 2.0:
        score += 25.0
        reasons.append(f"vol {vol_ratio:.1f}x avg")
    elif vol_ratio is not None and vol_ratio > 1.5:
        score += 10.0
    else:
        # 无放量的"波动"不算真突破 → 大幅降分
        score = score * 0.5

    # === BB width（squeeze → expansion 加分）===
    if bb_width is not None and bb_width < 2.0:
        score += 15.0
        reasons.append("BB squeeze")

    # === 方向：由 4h 级别趋势决定 ===
    if ret_4h is not None and abs(ret_4h) > 2.0:
        direction = "long" if ret_4h > 0 else "short"
    
    if direction is None:
        score = 0.0  # 无方向的波动没有交易价值

    return max(0.0, min(100.0, score)), reasons, direction
```

#### 2.3 Mean Reversion Scanner 加强

**文件**: `aletheia/universe/scanners.py:62-110`

**核心改变**：
1. RSI 门槛更严（30→25, 70→75）减少噪声
2. 必须有成交量枯竭确认（vol_ratio < 0.7）
3. 方向保持内生

```python
def score_mean_reversion(feat: dict) -> tuple[float, list[str], str | None]:
    """Score mean-reversion: RSI extreme + volume exhaustion + overextension.
    
    Returns (score, reasons, direction).
    """
    score = 0.0
    reasons = []
    direction = None

    rsi_val = feat.get("rsi_14")
    ret_4h = feat.get("return_4h")
    ret_24h = feat.get("return_24h")
    vol_ratio = feat.get("vol_ratio")
    funding = feat.get("funding_rate")

    # === RSI 极端（门槛收紧）===
    if rsi_val is not None:
        if rsi_val >= 80:
            score += 35.0
            direction = "short"
            reasons.append(f"RSI {rsi_val:.0f} extreme overbought")
        elif rsi_val >= 75:
            score += 20.0
            direction = "short"
            reasons.append(f"RSI {rsi_val:.0f} overbought")
        elif rsi_val <= 20:
            score += 35.0
            direction = "long"
            reasons.append(f"RSI {rsi_val:.0f} extreme oversold")
        elif rsi_val <= 25:
            score += 20.0
            direction = "long"
            reasons.append(f"RSI {rsi_val:.0f} oversold")

    # === 过度延伸 ===
    if ret_4h is not None:
        if ret_4h > 8.0:
            score += 25.0
            if direction is None: direction = "short"
            reasons.append(f"4h overextended +{ret_4h:.1f}%")
        elif ret_4h < -8.0:
            score += 25.0
            if direction is None: direction = "long"
            reasons.append(f"4h overextended {ret_4h:.1f}%")

    # === 成交量枯竭确认（加分）===
    if vol_ratio is not None and vol_ratio < 0.7:
        score += 15.0
        reasons.append("volume exhaustion")

    # === Funding 确认（拥挤侧=均值回归更可能）===
    if funding is not None and direction is not None:
        if direction == "short" and funding > 0.005:
            score += 15.0
            reasons.append("crowded longs (funding)")
        elif direction == "long" and funding < -0.005:
            score += 15.0
            reasons.append("crowded shorts (funding)")

    if direction is None:
        score = 0.0

    return max(0.0, min(100.0, score)), reasons, direction
```

#### 2.4 Scanner 接口变更

**文件**: `aletheia/universe/scanners.py:210-231`

**改动**：`run_all_scanners` 返回 direction（从 scanner 内生），供 signal_emitter 直接使用

```python
def run_all_scanners(feat: dict) -> dict:
    """Run all scanner families. Returns best result with direction.
    
    Scanner 现在返回 (score, reasons, direction)，direction 内生于评分逻辑。
    """
    results = {
        "momentum": score_momentum(feat),
        "mean_reversion": score_mean_reversion(feat),
        "volatility": score_volatility(feat),
        "derivatives": score_derivatives(feat),  # 保持原有3-tuple
        "event": (0.0, [], None),
    }

    # 只考虑有方向的 scanner
    valid = {k: v for k, v in results.items() if v[2] is not None}
    
    if not valid:
        # fallback: 原行为（选最高分但方向可能为 None）
        best_family = max(results, key=lambda k: results[k][0])
        best_score, best_reasons, best_dir = results[best_family]
    else:
        best_family = max(valid, key=lambda k: valid[k][0])
        best_score, best_reasons, best_dir = valid[best_family]

    return {
        "scanner_family": best_family,
        "opportunity_score": best_score,
        "reasons": best_reasons,
        "scanner_direction": best_dir,  # 新增：scanner 内生方向
        "all_scores": {k: v[0] for k, v in results.items()},
    }
```

#### 2.5 Signal Emitter 使用 Scanner 内生方向

**文件**: `aletheia/universe/signal_emitter.py:80`

**改动**：优先使用 scanner 返回的方向，fallback 到 `_infer_direction`

```python
# 在 emit_deterministic_opportunities() 中
scanner_direction = feat.get("scanner_direction")
direction = scanner_direction if scanner_direction else _infer_direction(family, feat)
```

这使得 `_infer_direction` 退化为 legacy fallback，新 scanner 的方向都是内生的。

---

### Phase 3：反馈增强（~2h 工作量）

**目标**：让差表现自动被降权/剔除，好表现被强化

#### 3.1 Factor Feedback 前置到 Signal Emitter

**文件**: `aletheia/universe/signal_emitter.py:64-101`

**当前状态**：factor_feedback 只在 `paper_trader_ranker.py` 中使用（信号已经发出后才检查）

**改动**：在 signal_emitter 发射信号前就检查 factor block

```python
from aletheia.analytics.factor_feedback import lookup_factor_signal

for m in members:
    # ... 现有逻辑 ...
    
    # 在 direction 确定后、insert_opportunity 前
    factor_signal = lookup_factor_signal(family, symbol, direction)
    if factor_signal and factor_signal.get("blocked"):
        logger.debug(
            "factor-blocked %s %s %s (losses=%d, wr=%.0f%%)",
            family, direction, symbol,
            factor_signal.get("loss_count", 0),
            factor_signal.get("win_rate", 0) * 100,
        )
        continue  # 不发射这个信号
```

**为什么有效**：
- 当前 factor_feedback 在 ranker 端降分，但信号仍然发出 → 占用 opportunity 槽位
- 前置检查让"已证明无效的 family×symbol×direction 组合"从源头被屏蔽
- 类似 Freqtrade 的 PerformanceFilter：差表现对直接从列表中移除

#### 3.2 Calibration 增强

**文件**: `aletheia/universe/allocator.py:12-56`

**改动**：

```python
# 1. 降低样本门槛：20 → 10（加速学习）
_MIN_CALIBRATION_SAMPLES = 10

# 2. 加大调整步长：±15% → ±40%（让 17% win_rate 的 momentum 被强惩罚）
def get_family_multipliers(max_step: float = 0.40) -> dict[str, float]:

# 3. 按 family 使用不同 horizon 校准
_FAMILY_CALIBRATION_HORIZON = {
    "momentum": "4h",       # momentum 看中期持续性
    "mean_reversion": "1h", # MR 看短期反转
    "volatility": "1h",     # 突破看短期
    "derivatives": "4h",    # 拥挤解除看中期
}
```

**影响计算**：
- 当前 momentum 1h hit_rate ≈ 48.5%
- 旧公式：`(0.485-0.5) * (0.15/0.15) = -0.015` → multiplier=0.985（几乎无效）
- 新公式：`(0.485-0.5) * (0.40/0.15) = -0.04` → multiplier=0.96
- 对 24h momentum hit_rate=17%：`(0.17-0.5) * (0.40/0.15) = -0.88` → clamped to 0.60
- 效果：momentum 的 opportunity_score 打 6 折 → 在 total_score 排序中大幅降级

#### 3.3 新增 Family×Direction 粒度的 Calibration

**文件**: `aletheia/storage/repositories.py` (calibration_stats SQL)

**当前问题**：calibration 只按 `scanner_family` 分组，不区分 direction。但数据显示 momentum-long 和 momentum-short 的 win_rate 可能差异很大。

**改动**：calibration_stats 加 direction 维度

```sql
SELECT scanner_family, direction, horizon,
       COUNT(*) as n,
       SUM(CASE WHEN ... END) as win_count
FROM universe_forward_outcomes
WHERE settled_at IS NOT NULL
GROUP BY scanner_family, direction, horizon
```

---

### Phase 4：验证闭环（~1h 工作量）

**目标**：用数据证明每一步改进有效

#### 4.1 A/B Metrics 表

**文件**: 新增 `aletheia/universe/metrics.py`

```python
def compute_scanner_metrics() -> dict:
    """Compute win_rate by family×direction×horizon for current data."""
    # 从 universe_forward_outcomes 聚合
    # 返回结构化数据供 API 展示
    pass
```

#### 4.2 Forward Outcome Win Rate 监控

在每次 `outcome_recorder.settle_due()` 完成后，自动计算并 log 各 family 的 rolling win_rate：

```python
# outcome_recorder.py settle_due() 末尾
stats = repo.calibration_stats()
for row in stats:
    if row["n"] >= 10:
        wr = row["win_count"] / row["n"] * 100
        logger.info(
            "METRIC family=%s dir=%s horizon=%s n=%d win_rate=%.1f%%",
            row["scanner_family"], row.get("direction", "all"),
            row["horizon"], row["n"], wr,
        )
```

#### 4.3 逐 Phase 可度量目标

| Phase | 改动 | 预期 win_rate 变化 | 验证方法 |
|-------|------|-------------------|----------|
| 0.1 | 24h 过度延伸保护 | momentum 24h: 17%→35%+ | 过滤掉 abs(ret_24h)>10 的样本后重新统计 |
| 0.2 | 方向冲突检测 | 全 family 1h: 48%→52%+ | 过滤掉 ret_1h×ret_4h<0 的样本 |
| 0.3 | Funding 方向确认 | 有 funding 数据的子集 win_rate 提升 | A/B: 有funding确认 vs 无 |
| 1.1 | Volume ratio | vol_ratio>2 的子集应显著优于 <1 的 | 分桶统计 |
| 1.2 | Relative strength | RS>3 的子集 4h win_rate 应 >55% | 分桶统计 |
| 2.x | Scanner 重写 | 全局 1h win_rate >55%, 4h >52% | 部署后 forward outcomes |
| 3.x | 反馈前置 | 被 block 的信号 win_rate 确实 <40% | 对比 blocked vs passed |

---

## 四、实施顺序与依赖

```
Phase 0（止血）────────────────────────────────────────────
  ├─ 0.1 ret_24h 保护
  ├─ 0.2 方向冲突检测
  ├─ 0.3 funding 确认扩散          无依赖，可并行
  └─ 0.4 volatility 方向改 ret_4h

Phase 1（特征升级）────────────────────────────────────────
  ├─ 1.1 vol_ratio（复用 indicators.volume_sma_ratio）
  ├─ 1.2 relative_strength（需 BTC baseline）  ← Phase 2 依赖
  ├─ 1.3 OI 实时化（可选，有 DB fallback）
  └─ 1.4 BB squeeze（复用 indicators.bollinger）

Phase 2（Scanner 重写）───────────── 依赖 Phase 1 完成 ────
  ├─ 2.1 momentum → trend_continuation
  ├─ 2.2 volatility → breakout
  ├─ 2.3 mean_reversion 加强
  ├─ 2.4 接口变更（返回 direction）
  └─ 2.5 signal_emitter 使用内生方向

Phase 3（反馈增强）────────────────────────────────────────
  ├─ 3.1 factor_feedback 前置       ← 可在 Phase 0 后就做
  ├─ 3.2 calibration 增强
  └─ 3.3 family×direction 粒度

Phase 4（验证）─────────── 贯穿全程 ──────────────────────
  └─ 每个 Phase 完成后验证 forward outcome metrics
```

---

## 五、不做的事情（明确边界）

| 不做 | 理由 |
|------|------|
| 上 ML 模型（LightGBM/XGBoost） | 数据量不够（forward outcomes < 500 条）、维护成本高、可解释性差 |
| 接入 Netflow 数据 | 无免费可靠数据源；OI+Funding 已覆盖"资金流向"大部分信息 |
| 复制 VergeX 7 策略分层 | 问题在 Universe 层不在策略层；策略层已有 strategy-factory |
| 加新的交易所 | CLAUDE.md 明确禁止 |
| 多 Universe 池（long pool / short pool） | 增加复杂度，Phase 2 的 scanner 内生方向已解决 |
| 链上数据 / 社交数据 | scope 膨胀，非核心信号 |
| Docker/Redis/外部依赖 | CLAUDE.md 明确禁止 |

---

## 六、涉及文件清单

| 文件 | Phase | 改动性质 |
|------|-------|----------|
| `aletheia/universe/signal_emitter.py` | 0, 2.5, 3.1 | 方向逻辑重写 + 前置过滤 |
| `aletheia/universe/features.py` | 1 | 新增 vol_ratio, relative_strength, bb_width |
| `aletheia/universe/scanners.py` | 2 | 全部 scanner 重写，接口从 2-tuple 变 3-tuple |
| `aletheia/universe/allocator.py` | 3.2 | calibration 参数调整 |
| `aletheia/storage/repositories.py` | 3.3 | calibration_stats SQL 加 direction |
| `aletheia/trading/indicators.py` | — | 无改动（已有 volume_sma_ratio, bollinger, relative_strength_rank） |
| `aletheia/universe/outcome_recorder.py` | 4.2 | settle 后自动 log metrics |
| `aletheia/universe/metrics.py` | 4.1 | 新文件：metrics 聚合工具 |
| `aletheia/config/schema.py` | — | 无改动（UniverseConfig 不需变） |
| `tests/test_scanners.py` | 全程 | 单元测试覆盖新逻辑 |

---

## 七、关键设计决策记录

### D1: 方向内生 vs 方向后贴

**决策**：方向从 Scanner 内生返回

**理由**：
- 后贴方向（当前 `_infer_direction`）导致选股逻辑和方向逻辑脱耦
- Scanner 选出"波动大的币"但不知道方向 → 只能猜 `sign(ret_1h)`
- 内生方向让"选什么"和"怎么交易"一致：momentum scanner 选"跑赢大盘的"自然 direction=long

### D2: 相对强度 vs 绝对回报

**决策**：momentum 核心评分用相对强度（vs BTC）

**理由**：
- 绝对回报选出的是 beta（大盘涨所有币都涨）
- 相对强度选出的是 alpha（独立于大盘的个币持续性）
- 学术证据：cross-sectional momentum 在股票/加密市场都有显著 alpha

### D3: 成交量确认作为必要条件

**决策**：volatility scanner 无放量时得分打 5 折

**理由**：
- 无量波动 = 流动性不足时的随机跳动，非真突破
- VergeX、breakout scanner 都要求 2x+ 均量
- 这是区分"真突破"和"假波动"最简单有效的过滤器

### D4: Calibration 步长从 ±15% 加大到 ±40%

**决策**：允许更大的惩罚力度

**理由**：
- momentum 24h win_rate=17% 是强烈反向信号，需要被强力压制
- ±15% 对应 total_score 最大变化 5.25 分（噪声范围内）
- ±40% 对应最大变化 14 分（足以改变排序）

### D5: 不上 ML 模型

**决策**：纯规则 + 线性组合 + calibration 反馈

**理由**：
- Forward outcomes 数据不到 500 条，ML 过拟合风险极高
- 规则系统可解释、可调试、可逐步验证
- 等数据积累到 5000+ 条后再考虑 ML ranking
