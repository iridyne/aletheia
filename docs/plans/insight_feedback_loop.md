# Insight Feedback Loop — 分析结果驱动开仓决策优化

> 设计文档 | 创建 2026-06-21
> 目标：将 TradeInsights 可视化分析的多维度结果结构化，供给 AI agent，闭环优化开仓方向、频道选择和时机判断。

---

## 1. 现状全景（As-Is）

### 1.1 已有的分析维度（前端 TradeInsightsPanel）

| # | 维度 | 数据来源 | 给谁看 |
|---|------|---------|--------|
| 1 | Equity Curve | positions.realized_pnl 累加 | 人（前端图表） |
| 2 | Drawdown Curve | 峰值回撤 | 人 |
| 3 | Profit Factor + Avg Win/Loss | 正/负 PnL 聚合 | 人 |
| 4 | Long vs Short 胜率/期望 | direction 分组 | 人 |
| 5 | PnL Distribution 直方图 | 百分比桶 | 人 |
| 6 | Day-of-week 盈亏 | 按星期几分组 | 人 |
| 7 | Hour-of-day 盈亏 | 按 UTC 小时分组 | 人 |
| 8 | Regime Radar | regime_tag 分组 | 人 |
| 9 | Hold Time vs PnL Scatter | holding_hours + pnl_pct | 人 |
| 10 | Streak Summary | 连胜/连败 | 人 |

**问题：这些数据只给人看。AI agent 目前不消费这些可视化层面的统计结果。**

### 1.2 已有的反馈闭环

| Loop | 机制 | 影响决策的方式 | 局限 |
|------|------|--------------|------|
| **L1** post_trade_reviewer | LLM 写 200 字 review → case study → guard candidate | 被动防守（guard 拒绝开仓） | 不主动优化"什么时候应该开" |
| **L2** strategy_feedback | kill 原因 + winning templates 注入 strategy_designer prompt | 影响下一批策略设计 | 不影响 market_observer 的方向判断 |
| **L3** universe_forward_outcomes | 4h 前瞻结算 → guard_candidate | Tier 1 自动守卫 | 只看单次 loss，不看 pattern |
| **L4** lifecycle_automation | exit_policy_advisor → auto-retire/pause | 淘汰坏策略 | 不改善好策略的进入时机 |
| **L5** lesson_constraints | 人工 accept 的 PSLesson → risk engine block | 最强硬约束 | 需要人工介入 |

### 1.3 关键缺口

```
         已有                                       缺失
┌─────────────────────┐                 ┌────────────────────────────┐
│ 多维度统计分析结果   │ ──── GAP ────→ │ 结构化 Insight 供 AI 消费   │
│ (只给前端渲染)       │                 │ (注入 agent prompt context) │
└─────────────────────┘                 └────────────────────────────┘
         已有                                       缺失
┌─────────────────────┐                 ┌────────────────────────────┐
│ 开仓判断             │ ──── GAP ────→ │ 历史 pattern-aware 的       │
│ (market_observer     │                 │ direction/timing/symbol     │
│  只看当前快照)       │                 │ 偏好注入                    │
└─────────────────────┘                 └────────────────────────────┘
         已有                                       缺失
┌─────────────────────┐                 ┌────────────────────────────┐
│ 归因只到 factor_key  │ ──── GAP ────→ │ 市场 context-aware 归因     │
│ (scanner:sym:dir:    │                 │ (regime序列/波动率位置/     │
│  conf:late)          │                 │  相关性/宏观/时段)          │
└─────────────────────┘                 └────────────────────────────┘
```

---

## 2. 设计目标

将系统从 **"被动防守型"反馈闭环**（发现亏钱 pattern → 拦截）升级为 **"主动进攻型"智能决策**（发现盈利 pattern → 主动寻找并优先执行）。

具体可度量目标：
1. market_observer 的 direction_bias 准确率（4h 前瞻）提升 5%+
2. paper_trader 的开仓胜率和期望值提升（通过更精准的 timing/symbol 选择）
3. 系统自主发现并利用 "特定 regime + 特定时段 + 特定 scanner family" 的盈利组合

---

## 3. 架构设计（全模块展开）

### 3.0 总体数据流

```
                    ┌──────────────────────────────────────────────────┐
                    │              Stage 1: Extended Attribution         │
                    │              (扩展归因维度)                        │
                    │                                                    │
                    │  trade_attribution.py 扩展:                        │
                    │  + regime_sequence (开仓前 regime 变化序列)         │
                    │  + volatility_percentile (ATR 相对位置)            │
                    │  + correlation_cluster (资产相关性分组)             │
                    │  + macro_regime (开仓时 DXY/VIX 状态)              │
                    │  + hour_bucket / day_bucket (时间维度)             │
                    │  + opportunity_density (同时段 open opp 数量)      │
                    │  + market_microstructure (orderbook imbalance)     │
                    └──────────────────────┬───────────────────────────┘
                                           │
                                           ▼
                    ┌──────────────────────────────────────────────────┐
                    │              Stage 2: Insight Synthesizer          │
                    │              (LLM 模式发现)                        │
                    │                                                    │
                    │  新模块 insight_synthesizer.py:                    │
                    │  - 定期(每日)读取扩展 attribution + trade_insights │
                    │  - LLM 结构化输出:                                 │
                    │    • regime_playbook (各 regime 下的偏好)           │
                    │    • timing_windows (最优时间窗口)                  │
                    │    • symbol_affinity (当前阶段的 symbol 偏好)       │
                    │    • anti_patterns (明确避免的组合)                 │
                    │    • direction_calibration (方向校准建议)           │
                    │  - 输出持久化为 TradingMemo knowledge item         │
                    └──────────────────────┬───────────────────────────┘
                                           │
                                           ▼
                    ┌──────────────────────────────────────────────────┐
                    │              Stage 3: Context Injection            │
                    │              (决策链注入)                          │
                    │                                                    │
                    │  两个注入点:                                       │
                    │  A. market_observer prompt (方向判断)              │
                    │     → "根据近 30 天数据，你在 trending regime 做多  │
                    │        BTC 胜率 72%，但 consolidation 做多只有 35%" │
                    │  B. paper_trader_ranker scoring (频道/时机选择)     │
                    │     → timing_bonus/penalty                        │
                    │     → symbol_affinity_bonus                       │
                    │     → regime_alignment_bonus                      │
                    └──────────────────────────────────────────────────┘
```

### 3.1 模块 A — Extended Attribution（扩展归因）

**文件**: `aletheia/analytics/trade_attribution.py` 扩展

**新增字段（在现有 38 字段基础上）**:

| 字段 | 来源 | 含义 |
|------|------|------|
| `regime_at_entry` | positions.regime_tag (已有) | 开仓时的 regime |
| `regime_sequence_3d` | 开仓前 3 天的 regime 变化 | e.g. "consolidation→breakout→trending" |
| `atr_percentile` | 开仓时 ATR vs 过去 90 天 | 波动率在历史中的相对位置 (0-100) |
| `volume_z_score` | 开仓时 24h volume vs 20-day mean | 放量/缩量程度 |
| `hour_bucket` | opened_at 提取 | UTC 小时（0-23） |
| `day_of_week` | opened_at 提取 | 0=Mon ... 6=Sun |
| `btc_correlation_30d` | universe_feature_snapshots.btc_beta | 与 BTC 的 30 天 beta |
| `macro_regime` | 开仓时 macro_snapshot (DXY/VIX/SPX 状态) | bull/bear/risk_off/risk_on |
| `opportunity_density` | 同 snapshot 中 open opportunities 数量 | 信号拥挤度 |
| `orderbook_imbalance` | 开仓时 bid_depth/ask_depth ratio | >1 买盘厚，<1 卖盘厚 |
| `funding_rate_at_entry` | repo.latest_funding_rate at opened_at | 资金费率（正=多头拥挤） |

**数据来源可行性分析**:

| 字段 | 是否已有原始数据 | 需要新增什么 |
|------|-----------------|-------------|
| regime_sequence_3d | ❌ 只存了开仓时的单点 regime | 需要 market_regime 历史表（或从 OHLCV 回算） |
| atr_percentile | ❌ ATR 只在 feature_snapshot 中存在 | 需要在 position 创建时快照 ATR percentile |
| volume_z_score | ❌ 24h volume 在 snapshot 中有但未关联到 position | 同上，position 创建时快照 |
| hour_bucket | ✅ 从 opened_at 计算 | 纯计算，无需新数据 |
| day_of_week | ✅ 从 opened_at 计算 | 同上 |
| btc_correlation_30d | ✅ universe_feature_snapshots.btc_beta 已有 | 需要在 position 创建时关联 |
| macro_regime | ⚠️ macro_snapshot 已收集但无 regime 分类 | 需要一个简单分类器 |
| opportunity_density | ✅ 可从 opportunities 表按 created_at 窗口聚合 | 纯 SQL 计算 |
| orderbook_imbalance | ✅ orderbook 已收集 | 需要在 position 创建时快照 |
| funding_rate_at_entry | ✅ funding_rates 已收集 | 需要在 position 创建时快照 |

**关键设计决策**: 部分字段需要在 **开仓时刻** 快照到 position 记录（否则事后无法重建）。这意味着需要扩展 `position_manager` 的 `open_position()` 逻辑，在创建 position 时附带一个 `entry_context` JSON blob。

---

### 3.2 模块 B — Entry Context Snapshot（开仓上下文快照）

**文件**: 新增 `aletheia/analytics/entry_context.py`

**职责**: 在 position 创建时，快照当前市场微观状态，存入 position 的 metadata 字段。

```python
def capture_entry_context(symbol: str, direction: str) -> dict:
    """Capture market microstructure at the moment of entry."""
    return {
        "atr_percentile": _compute_atr_percentile(symbol),
        "volume_z_score": _compute_volume_z_score(symbol),
        "orderbook_imbalance": _get_orderbook_imbalance(symbol),
        "funding_rate": _get_funding_rate(symbol),
        "btc_beta": _get_btc_beta(symbol),
        "macro_regime": _classify_macro_regime(),
        "opportunity_density": _count_open_opportunities(),
        "regime_sequence_3d": _get_regime_sequence(symbol, days=3),
    }
```

**调用点**: `aletheia/trading/position_manager.py` 的 `open_position()` — 在创建 position row 之前调用，结果存入 `positions.entry_market_summary` JSON 字段（此字段已存在但目前只存 free-text）。

**向后兼容**: 老 position 没有结构化 entry_context，归因模块对缺失字段返回 None。

---

### 3.3 模块 C — Insight Synthesizer（洞察合成器）

**文件**: 新增 `aletheia/analytics/insight_synthesizer.py`

**职责**: 定期读取近 N 天的扩展归因数据 + trade_insights 统计结果，调用 LLM 进行结构化模式发现。

**输入（喂给 LLM 的结构化 context）**:

```python
def _build_synthesis_context(window_days: int = 30) -> str:
    """Assemble statistics for LLM pattern discovery."""
    # 1. 基础 KPI
    stats = build_trade_insights()  # equity curve, drawdown, etc.
    
    # 2. 扩展归因统计
    rows = build_trade_attribution_rows(limit=500)
    # → 按 regime 分组的胜率/期望值
    # → 按 hour_bucket 分组的胜率/期望值
    # → 按 day_of_week 分组的胜率/期望值
    # → 按 scanner_family 分组的胜率/期望值
    # → 按 direction 分组的胜率/期望值
    # → 按 atr_percentile 分桶(low/medium/high)的胜率/期望值
    # → 按 macro_regime 分组的胜率/期望值
    # → 交叉组合: regime × direction, regime × scanner, hour × direction
    
    # 3. 当前市场状态
    current_regime = get_latest_market_regime()
    current_macro = get_latest_macro_snapshot()
    
    # 4. 近期 streak/momentum
    recent_streak = stats["streaks"]
    
    return render_synthesis_prompt(...)
```

**LLM 输出 schema（结构化）**:

```python
class TradingMemo(TypedDict):
    generated_at: str  # ISO timestamp
    data_window_days: int
    sample_size: int  # 基于多少笔 trade
    
    regime_playbook: list[RegimeRule]
    # e.g. [{"regime": "trending", "direction": "long", "edge": "strong",
    #         "evidence": "32 trades, 72% win, +0.8% expectancy"},
    #        {"regime": "consolidation", "direction": "long", "edge": "weak",
    #         "evidence": "18 trades, 35% win, -0.3% expectancy"}]
    
    timing_windows: list[TimingRule]
    # e.g. [{"hour_range": "08-12 UTC", "quality": "best",
    #         "evidence": "avg +0.5% in this window vs -0.1% outside"},
    #        {"day": "Monday", "quality": "avoid",
    #         "evidence": "win_rate 28% on Mondays vs 55% rest of week"}]
    
    symbol_affinity: list[SymbolRule]
    # e.g. [{"symbol": "SOL/USDT", "status": "hot",
    #         "evidence": "5/6 wins last 14 days, momentum scanner family"},
    #        {"symbol": "DOGE/USDT", "status": "cold",
    #         "evidence": "1/7 wins, mostly consolidation regime"}]
    
    anti_patterns: list[AntiPattern]
    # e.g. [{"pattern": "long + consolidation + high funding",
    #         "loss_rate": 80, "sample_size": 10,
    #         "action": "skip or reduce size"}]
    
    direction_calibration: DirectionCal
    # e.g. {"overall_bias": "long_bias",
    #        "confidence": "medium",
    #        "reason": "trending regime + positive macro + 65% long win rate"}
    
    volatility_insight: VolatilityInsight
    # e.g. {"current_atr_percentile": 75,
    #        "historical_edge": "best entries at 30-60 percentile",
    #        "recommendation": "reduce_size_or_wider_stops"}
```

**持久化**: 输出作为 `trading_memo` knowledge item 存入 KB（`high_confidence`，进入 FTS 索引）。同时存入一个 dedicated SQLite 表 `trading_memos` 供快速读取（避免每次 FTS 查询）。

**调度**: 每日一次（02:30 CST，在 dreaming_agent 之后），确保有 dreaming 的 lesson 作为输入。额外：当 post_trade_reviewer 产生 3+ 新 case study 时也触发一次（event-driven）。

---

### 3.4 模块 D — Context Injection: market_observer 增强

**文件**: `aletheia/agents/market_observer.py` 修改

**当前 context 组装**:
1. multi-timeframe OHLCV 技术指标
2. orderbook/funding/OI/macro enrichment
3. prediction market signals
4. news
5. advisory memory (knowledge.router → FTS 检索)

**新增注入点**（在 advisory memory block 之后）:

```python
# 在 _analyze_symbol() 中:
trading_memo = get_latest_trading_memo()  # 读取最新 TradingMemo
if trading_memo:
    memo_block = render_memo_for_observer(trading_memo, symbol, current_regime)
    # 渲染为:
    # """
    # === HISTORICAL PERFORMANCE CONTEXT (last 30 days) ===
    # Your direction calls in the current regime (trending):
    #   Long: 72% win rate, +0.8% avg (32 trades) — STRONG EDGE
    #   Short: 41% win rate, -0.2% avg (12 trades) — WEAK
    # 
    # Current timing quality: GOOD (08-12 UTC window, historically +0.5%)
    # 
    # Symbol-specific: SOL/USDT is currently HOT (5/6 recent wins)
    # 
    # Anti-patterns to avoid:
    #   - Long in consolidation + high funding → 80% loss rate
    #
    # Volatility: ATR at 75th percentile → entries at this level historically underperform.
    #   Consider requiring higher confidence threshold.
    # ===
    # """
```

**LLM system prompt 修改**: 在已有的 direction_bias 校准指令后添加一段：

> "You also receive a HISTORICAL PERFORMANCE CONTEXT block summarizing your track record in similar conditions. Use this to calibrate your confidence: if your historical win rate for this direction+regime is below 40%, either flip direction or set confidence_score below 30. Never override a strong technical signal, but when signals are ambiguous, lean toward historically profitable directions."

**关键约束**:
- memo 是 soft guidance，不是 hard override
- LLM 仍然基于当前快照做独立判断
- memo 的 evidence 都带 sample size，LLM 可以判断统计显著性
- 如果 memo 和当前技术面矛盾，LLM 应该提高 confidence 门槛而非盲从 memo

---

### 3.5 模块 E — Context Injection: paper_trader_ranker 增强

**文件**: `aletheia/agents/paper_trader_ranker.py` 修改

**当前 scoring 逻辑（trade_score 计算）**:
```
base = confidence_score
+ universe_score
- late_entry_penalty - crowding_risk
+/- factor_feedback
+/- strategy_evidence
+/- regime_alignment
+ ruleset_bonus
- guard penalties
- lesson penalty
- risk_flags
```

**新增 scoring 维度**:

```python
def _apply_memo_scoring(score: float, opp: dict, memo: TradingMemo | None) -> float:
    """Apply trading memo insights as score adjustments."""
    if not memo:
        return score
    
    adjustments = 0.0
    
    # 1. Timing bonus/penalty (±10)
    hour = _current_utc_hour()
    timing_rule = _match_timing_rule(memo.timing_windows, hour)
    if timing_rule:
        if timing_rule.quality == "best":
            adjustments += 10
        elif timing_rule.quality == "avoid":
            adjustments -= 10
    
    # 2. Symbol affinity (±8)
    symbol_rule = _match_symbol_rule(memo.symbol_affinity, opp["symbol"])
    if symbol_rule:
        if symbol_rule.status == "hot":
            adjustments += 8
        elif symbol_rule.status == "cold":
            adjustments -= 8
    
    # 3. Regime × direction alignment (±12)
    regime_rule = _match_regime_rule(memo.regime_playbook, current_regime, opp["direction"])
    if regime_rule:
        if regime_rule.edge == "strong":
            adjustments += 12
        elif regime_rule.edge == "weak":
            adjustments -= 12
        elif regime_rule.edge == "contra":
            adjustments -= 20  # strong negative signal
    
    # 4. Anti-pattern penalty (-15)
    if _matches_anti_pattern(memo.anti_patterns, opp, current_regime):
        adjustments -= 15
    
    # 5. Volatility adjustment (±5)
    if memo.volatility_insight:
        if memo.volatility_insight.recommendation == "reduce_size_or_wider_stops":
            adjustments -= 5  # less favorable entry environment
    
    return score + adjustments
```

**与现有 scoring 的关系**:
- memo scoring 是 **additive**，不替代现有逻辑
- 权重设计：单项最大 ±20（anti_pattern），总计最大 ±45
- 对比：现有 factor_feedback 影响 ±20，guard penalty 最大 -50
- memo scoring 的影响力在 factor_feedback 和 guard 之间，是 "soft preference" 而非 "hard gate"

---

### 3.6 模块 F — Regime History Service（Regime 历史服务）

**文件**: 新增 `aletheia/data/regime_history.py`

**问题**: 当前 `market_regime` 只存最新一条（`repo.get_latest_market_regime()`）。要计算 `regime_sequence_3d`，需要 regime 变化历史。

**方案**: 利用已有的 `market_regime` 表，确保每次 market_observer 分析时都 INSERT 一条新记录（目前可能是 UPDATE）。然后回查历史：

```python
def get_regime_sequence(symbol: str | None = None, days: int = 3) -> list[str]:
    """Get regime transitions in the last N days."""
    rows = db.execute("""
        SELECT state, recorded_at FROM market_regime
        WHERE recorded_at >= datetime('now', ?)
        ORDER BY recorded_at ASC
    """, (f"-{days} days",)).fetchall()
    # Deduplicate consecutive same-state entries
    return _dedupe_transitions([r["state"] for r in rows])
```

**依赖**: 需要确认 `market_regime` 表是否保留历史（还是只存最新一条）。如果只存最新，需要改为 append-only。

---

### 3.7 模块 G — Macro Regime Classifier（宏观 Regime 分类器）

**文件**: 新增 `aletheia/analytics/macro_classifier.py`

**输入**: `macro_snapshots` 表中的 DXY, VIX, SPX, BTC dominance

**输出**: 简单规则分类器（不用 ML）：

```python
def classify_macro_regime(macro: dict) -> str:
    """Classify current macro state into actionable regime."""
    vix = macro.get("vix")
    dxy_change = macro.get("dxy_change_5d")
    spx_change = macro.get("spx_change_5d")
    
    if vix and vix > 25:
        return "risk_off"
    if dxy_change and dxy_change > 1.5:
        return "dollar_strength"  # crypto headwind
    if spx_change and spx_change > 2:
        return "risk_on"
    return "neutral"
```

**消费者**: entry_context snapshot, insight_synthesizer 的交叉分析维度

---

### 3.8 模块 H — Performance Monitor Dashboard（前端扩展）

**文件**: 前端 `TradeInsightsPanel.tsx` 扩展 + 新 API endpoint

**新增可视化（给人看 + 验证 AI 的 memo 质量）**:

| 图表 | 用途 |
|------|------|
| Regime × Direction 热力图 | 验证 regime_playbook 是否合理 |
| Hour-of-day × Direction 热力图 | 验证 timing_windows |
| TradingMemo 命中率 追踪 | memo 生成后 N 天内按 memo 方向开仓的胜率 vs 逆 memo 方向 |
| Anti-pattern 拦截统计 | 被 anti_pattern penalty 降低排名的 opportunity 如果执行了会怎样 |

**API endpoint**: `GET /analytics/memo-effectiveness` — 回溯最近 memo 的准确率。

---

### 3.9 模块 I — Feedback Quality Gate（反馈质量门）

**问题**: 如果 insight_synthesizer 基于太少的数据生成了错误的 memo，可能导致系统性偏差（如"只做多"导致在转熊时全部亏损）。

**设计**:

```python
class MemoQualityGate:
    MIN_TRADES_FOR_REGIME_RULE = 10  # 一个 regime rule 至少需要 10 笔 trade
    MIN_TRADES_FOR_TIMING_RULE = 15  # timing 规则更需要样本
    MIN_TRADES_FOR_SYMBOL_RULE = 5   # symbol-level 可以少一些
    MAX_MEMO_AGE_HOURS = 72          # memo 超过 3 天自动失效
    ANTI_PATTERN_MIN_SAMPLE = 5      # anti-pattern 需要至少 5 笔证据
    
    # 安全阀: memo 的 direction_calibration 不允许是 extreme
    # （不允许 "never go short" 或 "never go long"）
    MAX_DIRECTION_BIAS_STRENGTH = 0.75  # 最多 75% 偏向一个方向
```

**过期机制**: memo 带 `valid_until` 时间戳。超期后 market_observer 和 ranker 忽略它。每次新 memo 生成会覆盖旧 memo。

---

### 3.10 模块 J — Closed-Loop Validation（闭环验证）

**文件**: 新增 `aletheia/analytics/memo_effectiveness.py`

**职责**: 每次新 memo 生成时，回溯评估上一次 memo 的预测准确率。

```python
def evaluate_memo_effectiveness(memo_id: str) -> MemoReport:
    """Compare memo predictions against actual outcomes since it was active."""
    # 1. 取 memo 生效期间的所有 closed positions
    # 2. 对每条 regime_playbook rule: 实际 win_rate vs memo 预测的 edge
    # 3. 对每条 timing_window rule: 实际 pnl in-window vs out-of-window
    # 4. 对每条 symbol_affinity: actual performance of hot vs cold
    # 5. 计算 memo_accuracy_score (0-100)
    # 6. 如果连续 3 次 memo 准确率 < 40%, 自动降级 memo 的 scoring 权重
```

**这个模块防止系统陷入 "错误自信" 的正反馈循环。**

---

## 4. 模块交互图

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────┐
│ data_       │     │ position_   │     │ universe_       │
│ collectors  │     │ manager     │     │ refresh         │
│ (2min)      │     │             │     │ (10min)         │
└──────┬──────┘     └──────┬──────┘     └────────┬────────┘
       │                   │                      │
       │ orderbook         │ open_position()      │ feature_snapshot
       │ funding           │     │                │
       │ macro             │     ▼                │
       │ volume            │ ┌────────────┐       │
       ▼                   │ │ Module B   │       │
┌──────────────┐           │ │ entry_     │       │
│ SQLite       │           │ │ context    │       │
│ (raw market  │           │ │ snapshot   │       │
│  data)       │           │ └──────┬─────┘       │
└──────┬───────┘           │        │             │
       │                   │        ▼             │
       │                   │ positions.entry_market_summary (JSON)
       │                   │                      │
       ▼                   ▼                      ▼
┌──────────────────────────────────────────────────────┐
│                 Module A: Extended Attribution         │
│                 trade_attribution.py (扩展)           │
│                                                       │
│  输入: positions + orders + proposals + opportunities │
│        + entry_context + regime_history + macro       │
│  输出: 38 + 11 = 49 字段的 attribution rows          │
└──────────────────────────┬───────────────────────────┘
                           │
              ┌────────────┼────────────────┐
              │            │                │
              ▼            ▼                ▼
┌─────────────────┐ ┌────────────┐  ┌─────────────────┐
│ trade_insights  │ │ factor_    │  │ trade_path      │
│ (前端图表)      │ │ feedback   │  │ (MFE/MAE)       │
└────────┬────────┘ └─────┬──────┘  └────────┬────────┘
         │                │                   │
         └────────────────┼───────────────────┘
                          │
                          ▼
         ┌────────────────────────────────────┐
         │      Module C: Insight Synthesizer  │
         │      insight_synthesizer.py         │
         │                                     │
         │  调度: 每日 02:30 CST              │
         │        + event (3+ new case study)  │
         │                                     │
         │  输入: extended attribution stats    │
         │        + trade_insights             │
         │        + current regime/macro       │
         │        + factor_feedback summaries   │
         │  LLM: → TradingMemo 结构化输出      │
         │  输出: trading_memo KB item         │
         │        + trading_memos table row    │
         └─────────────────┬──────────────────┘
                           │
              ┌────────────┼────────────────┐
              │                             │
              ▼                             ▼
┌──────────────────────┐      ┌──────────────────────────┐
│ Module D:            │      │ Module E:                 │
│ market_observer      │      │ paper_trader_ranker       │
│ prompt injection     │      │ scoring injection         │
│                      │      │                           │
│ TradingMemo →        │      │ TradingMemo →             │
│ "HISTORICAL CONTEXT" │      │ timing_bonus ±10          │
│ block in LLM prompt  │      │ symbol_affinity ±8        │
│                      │      │ regime_alignment ±12      │
│ 影响: direction_bias │      │ anti_pattern -15          │
│        confidence    │      │ volatility ±5             │
└──────────┬───────────┘      └──────────────┬────────────┘
           │                                  │
           ▼                                  ▼
    Opportunity                         TradeProposal
    (更准确的                           (更优的 symbol/
     direction_bias)                     timing 选择)
           │                                  │
           └──────────────┬───────────────────┘
                          ▼
                   Position (open)
                          │
                          ▼
                   Position (close)
                          │
                          ▼
         ┌────────────────────────────────────┐
         │      Module J: Memo Effectiveness   │
         │      (闭环验证)                     │
         │                                     │
         │  比较 memo 预测 vs 实际结果          │
         │  如果连续不准 → 降权                 │
         └────────────────────────────────────┘
```

---

## 5. 与现有 5 个 Feedback Loop 的关系

| 现有 Loop | 本设计的关系 | 是否冲突 |
|-----------|-------------|---------|
| L1 post_trade_reviewer | **互补**。PTR 产出 case_study → 本设计的 synthesizer 消费它们作为输入 | 无冲突 |
| L2 strategy_feedback | **互补**。kill/win patterns 影响 strategy_designer；本设计影响 market_observer 和 ranker | 无冲突（不同注入点） |
| L3 universe_forward_outcomes | **互补**。Tier 1 guard 是 hard block；本设计是 soft scoring | 无冲突 |
| L4 lifecycle_automation | **互补**。lifecycle 淘汰坏策略；本设计优化好策略的执行时机 | 无冲突 |
| L5 lesson_constraints | **需要协调**。PSLesson 是最强约束（risk engine block）；memo scoring 不应与 accepted lesson 矛盾 | 设计时检查: memo 不允许推荐被 lesson 明确 block 的方向/symbol |

---

## 6. 与 Self-Evolution Plan 的关系

| Self-Evolution Tier | 本设计对应 | 关系 |
|----|----|----|
| Tier 1 (已完成): guard_candidate 自动生成 | Module C anti_patterns | anti_patterns 是 soft 版本的 guard（不 block，只 penalize），两者共存 |
| Tier 2 (2026-07): LLM synthesis agent | Module C insight_synthesizer | **可合并**。Tier 2 的 EvolutionSynthesizerAgent 可以作为 Module C 的子功能 |
| Tier 3 (2026-08): scanner rule promotion | 无直接对应 | 本设计不修改 scanner 规则，只影响下游 scoring |

**建议**: 将 Tier 2 的实施并入本设计的 Module C，避免重复。

---

## 7. 风险与缓解

| 风险 | 后果 | 缓解 |
|------|------|------|
| 样本量不足导致虚假 pattern | memo 推荐错误方向 | Module I 质量门（MIN_TRADES），Module J 闭环验证 |
| Regime 快速切换导致 memo 过期 | 按旧 regime 指导在新 regime 亏损 | MAX_MEMO_AGE_HOURS=72，regime 变化时触发重新生成 |
| Over-fitting: 过度贴合历史数据 | 未来表现与历史不一致 | 交叉验证；memo 只做 soft guidance 不做 hard block |
| Positive feedback loop: 系统只做一个方向 | 单边风险暴露 | MAX_DIRECTION_BIAS_STRENGTH=0.75 硬限制 |
| LLM 产出格式错误 | synthesizer 失败 | 结构化输出 + 重试 + fallback to empty memo |
| 增加 position 创建延迟 | entry_context capture 太慢 | 异步 + 超时降级（capture 失败不阻塞开仓） |

---

## 8. 优先级排序

基于 **影响力 × 可行性 × 依赖最少** 排序：

| Priority | Module | 影响 | 工作量 | 依赖 | 理由 |
|----------|--------|------|--------|------|------|
| **P0** | C — Insight Synthesizer | ⭐⭐⭐⭐⭐ | 中 | A 部分字段 | 核心价值：把现有数据转化为 AI 可消费的 insight |
| **P0** | D — market_observer injection | ⭐⭐⭐⭐⭐ | 小 | C | 注入机制简单，但效果最直接 |
| **P1** | E — ranker scoring injection | ⭐⭐⭐⭐ | 小 | C | 与 D 并行，影响 timing/symbol 选择 |
| **P1** | I — Quality Gate | ⭐⭐⭐⭐ | 小 | C | 安全阀，防止系统性偏差 |
| **P2** | B — Entry Context Snapshot | ⭐⭐⭐ | 中 | 无 | 未来数据质量的基础（越早开始积累越好） |
| **P2** | A — Extended Attribution | ⭐⭐⭐ | 中 | B | 更多维度让 C 的输出更精准 |
| **P2** | J — Closed-Loop Validation | ⭐⭐⭐ | 中 | C | 验证系统是否真的在改善 |
| **P3** | F — Regime History Service | ⭐⭐ | 小 | 确认 schema | 为 A 的 regime_sequence 服务 |
| **P3** | G — Macro Classifier | ⭐⭐ | 小 | 无 | 简单规则分类器 |
| **P3** | H — Dashboard 扩展 | ⭐⭐ | 中 | C, J | 人看的验证面板 |

---

## 9. 建议实施路线

### Phase 1: MVP（1-2 天，用现有数据）

只做 Module C + D + I — 不扩展归因维度，基于 **现有 trade_insights + factor_feedback** 的统计数据就足以生成第一版 TradingMemo。

- `insight_synthesizer.py`: 读现有 `build_trade_insights()` + `build_factor_feedback_summary()` + `get_latest_market_regime()`
- 结构化 LLM 输出 TradingMemo
- 注入 market_observer prompt
- 添加 quality gate（min sample sizes）
- 调度：每日 02:30 + event-driven

这一步 **不需要任何 schema 变更或新数据采集**，纯粹是将已有分析结果结构化后注入 AI 决策链。

### Phase 2: 开仓优化（2-3 天）

- Module E: ranker scoring injection
- Module B: entry_context snapshot（开始积累数据）
- Module J: memo effectiveness tracking

### Phase 3: 深度归因（3-5 天，需要足够数据积累）

- Module A: extended attribution（依赖 Phase 2 积累的 entry_context）
- Module F + G: regime history + macro classifier
- 升级 insight_synthesizer 使用更丰富的输入维度

### Phase 4: 验证与可视化

- Module H: 前端 performance monitor
- 全面的 memo 准确率追踪
- A/B test: 有 memo 指导 vs 无 memo 指导的 shadow trade 比较

---

## 10. 开放问题（需要讨论确认）

1. **memo 的注入强度**：当前设计是 soft guidance（LLM 可以忽略）。是否需要更强的约束（如：regime_playbook 中 edge=contra 时直接 block 而非只降分）？

2. **数据积累期**：Phase 1 可以立即启动，但 memo 质量取决于已有 closed positions 的数量。如果当前 trade 数量 < 30，memo 的统计意义有限。是否先跑 shadow-only 模式（生成 memo 但只做 logging，不实际影响 scoring）？

3. **与 Tier 2 self-evolution 的合并**：Tier 2 原计划 2026-07-01，本设计的 Module C 功能基本覆盖了 Tier 2。是否直接替代 Tier 2，还是保留 Tier 2 作为独立的 guard-focused synthesizer？

4. **LLM 成本控制**：insight_synthesizer 每次调用需要把 30 天统计数据打包给 LLM（~2000 token input + ~800 token output）。每日一次成本可忽略，但 event-driven 触发频率需要限制（建议：最多每 6 小时一次）。

5. **多策略 vs 全局 memo**：当前设计是 **全局** TradingMemo（不区分策略）。是否需要 per-strategy memo（如 "momentum 策略在 trending 中好，mean_reversion 策略在 consolidation 中好"）？per-strategy 需要更多数据积累。

---

## 附录 A: 文件清单（预期新增/修改）

### 新增文件
```
aletheia/analytics/insight_synthesizer.py    # Module C — 核心
aletheia/analytics/entry_context.py          # Module B — 开仓快照
aletheia/analytics/macro_classifier.py       # Module G — 宏观分类
aletheia/analytics/memo_effectiveness.py     # Module J — 闭环验证
aletheia/data/regime_history.py              # Module F — Regime 历史
```

### 修改文件
```
aletheia/analytics/trade_attribution.py      # Module A — 扩展字段
aletheia/agents/market_observer.py           # Module D — prompt injection
aletheia/agents/paper_trader_ranker.py       # Module E — scoring injection
aletheia/trading/position_manager.py         # Module B — 调用 entry_context
aletheia/knowledge/router.py                 # 新增 trading_memo item type
aletheia/agent_runtime/scheduler.py          # 新增 insight_synthesizer job
aletheia/server/routes/analytics.py          # Module H — 新 endpoint
frontend/src/charts/TradeInsightsPanel.tsx    # Module H — 新图表
```

### 新增 Schema
```sql
-- trading_memos 表（快速读取，避免 FTS 查询开销）
CREATE TABLE IF NOT EXISTS trading_memos (
    id TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    data_window_days INTEGER NOT NULL,
    sample_size INTEGER NOT NULL,
    memo_json TEXT NOT NULL,  -- full TradingMemo as JSON
    accuracy_score REAL,      -- filled by Module J retroactively
    created_at TEXT DEFAULT (datetime('now'))
);
```
