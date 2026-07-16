# 实施方案：市场状态机 + 策略池 + 信号方向性重构

> 基于 `RESEARCH_SHORT_AND_STRATEGY.md` 研究结论的具体实施计划  
> 日期：2026-06-17

---

## 现有基础设施（已验证可复用）

| 已有组件 | 位置 | 复用方式 |
|---------|------|---------|
| 简版 regime 检测 | `aletheia/data/collectors/macro.py:_classify_risk_regime()` | 扩展为完整状态机 |
| regime_tag 字段 | `Position.regime_tag`, `PaperTradeCandidate.regime_tag` | 已有存储槽位 |
| StrategyRecord 模型 | `aletheia/storage/models.py:187` | 扩展 direction_affinity 字段 |
| 策略生命周期分析 | `aletheia/analytics/lifecycle_automation.py` | 已有 auto-pause/retire |
| strategy_evidence | `aletheia/analytics/strategy_evidence.py` | 已有绩效快照 |
| signal_emitter | `aletheia/universe/signal_emitter.py` | P1 接入 regime |
| market_context | `aletheia/trading/market_context.py` | 指标已全部计算，可复用 |
| MacroSnapshot | `aletheia/data/models.py` | VIX/DXY/market_cap 已采集 |
| scheduler 定时任务 | `aletheia/agent_runtime/scheduler.py` | data_collectors 每2min已跑 macro |

---

## Phase 1: Market Regime Detector（市场状态机）

### 目标
让信号流"知道当前是什么市场环境"，从而让做空信号在适当时机自然涌现。

### 1.1 新增文件：`aletheia/regime/detector.py`

```python
"""Market regime detection — deterministic, no LLM.

Consumes: BTC OHLCV (1d), MacroSnapshot, market-wide funding rate.
Produces: MarketRegime dataclass written to DB + snapshot metadata.
"""

@dataclass
class MarketRegime:
    state: str          # "bull" | "bear" | "sideways" | "volatile"
    confidence: float   # 0.0 - 1.0
    long_bias: float    # 0.0 - 1.0 (建议多头权重)
    short_bias: float   # 0.0 - 1.0 (建议空头权重)
    signals: dict       # 各指标值快照 {"btc_vs_ma200": +5.2%, "vix": 22, ...}
    detected_at: str    # ISO timestamp


def detect_regime() -> MarketRegime:
    """综合多信号检测当前市场状态。"""
    # Signal 1: BTC price vs MA200 (趋势)
    # Signal 2: BTC 30d momentum (方向)
    # Signal 3: VIX level (恐慌)
    # Signal 4: 全市场平均 funding rate (拥挤度)
    # Signal 5: Total crypto market cap 7d change (资金流)
    # → 加权投票 → state + bias 输出
```

### 1.2 检测规则（具体）

| 信号 | Bull | Bear | Sideways | Volatile |
|------|------|------|----------|----------|
| BTC > MA200 | +2 | -2 | 0 | 0 |
| BTC 30d momentum > 5% | +1 | 0 | 0 | 0 |
| BTC 30d momentum < -10% | 0 | +2 | 0 | 0 |
| VIX > 25 | 0 | +1 | 0 | +1 |
| VIX < 15 | +1 | 0 | 0 | 0 |
| Avg funding > 0.05% | 0 | +1(拥挤) | 0 | 0 |
| Avg funding < -0.03% | 0 | 0 | 0 | +1 |
| ATR 突增（7d vs 30d > 2x） | 0 | 0 | 0 | +2 |
| BTC 30d 在 ±5% 内 | 0 | 0 | +2 | 0 |

投票最高的状态获胜；bias 按比例分配：
- Bull: long_bias=0.75, short_bias=0.25
- Bear: long_bias=0.25, short_bias=0.75
- Sideways: long_bias=0.50, short_bias=0.50
- Volatile: long_bias=0.35, short_bias=0.35 (两端受限)

### 1.3 存储与接入

- **新表**：`market_regime`（id, state, confidence, long_bias, short_bias, signals_json, detected_at）
- **写入时机**：每次 `data_collectors` 跑完 macro 后追加一次 detect（~2min 周期）
- **读取点**：
  1. `signal_emitter._infer_direction()` — 读 regime 调整阈值
  2. `paper_trader_ranker` — 用 regime.short_bias 调整做空候选的权重
  3. `risk_engine` — 用 regime 动态调整多空容量上限
  4. `Position` 开仓时标记（替代当前的 `risk_on/risk_off/unknown`）

### 1.4 signal_emitter 改造

```python
# Before (current)
def _infer_direction(family, feat) -> str | None:
    if family == "momentum":
        if ret_1h > 0:  # 固定阈值
            ...

# After (regime-aware)
def _infer_direction(family, feat, regime: MarketRegime | None = None) -> str | None:
    if family == "momentum":
        # Bear regime 下降低做空门槛
        long_threshold = 0.0 if (regime and regime.state != "bear") else 0.5
        short_threshold = 0.0 if (regime and regime.state != "bull") else -0.5
        if ret_1h > long_threshold:
            ...
        if ret_1h < short_threshold:
            ...
```

### 1.5 Config 新增

```python
class RegimeConfig(BaseModel):
    enabled: bool = True
    # 手动覆盖（用于测试/紧急情况）
    manual_override: str | None = None  # "bull" | "bear" | "sideways" | "volatile" | None
    # 状态切换的最小持续周期（防抖）
    min_hold_periods: int = 3  # 至少连续 3 次检测相同才切换
    # Bias 缩放因子（保守用户可以降低影响）
    bias_strength: float = 1.0  # 0-1, 乘到 long_bias/short_bias 上
```

### 1.6 前端 UI

- Settings 页新增 Regime 区块（显示当前状态 + 手动覆盖开关）
- Dashboard 顶部新增 Regime 指示器（bull=绿/bear=红/sideways=黄/volatile=紫）

---

## Phase 2: Strategy Pool（策略池重构）

### 目标
给策略一个显式的"方向亲和性"和"适用 regime"，让系统知道什么策略该在什么环境下激活。

### 2.1 StrategyRecord 扩展

```python
class StrategyRecord(BaseModel):
    # ... existing fields ...
    
    # ─── 新增字段 ───
    direction_affinity: str = "both"   # "long" | "short" | "both"
    applicable_regimes: str = "all"    # JSON list: ["bull","bear"] 或 "all"
    strategy_type: str = "scanner"     # "scanner" | "funding_arb" | "trend_follow" | "mean_revert" | "custom"
    
    # 性能追踪（aggregated from paper trades）
    win_rate_long: float | None = None
    win_rate_short: float | None = None
    avg_pnl_long: float | None = None
    avg_pnl_short: float | None = None
    last_regime_at_entry: str | None = None
```

### 2.2 策略激活逻辑

新增 `aletheia/regime/strategy_selector.py`：

```python
def get_active_strategies(regime: MarketRegime) -> list[dict]:
    """根据当前 regime 返回应激活的策略列表。"""
    all_strategies = list_strategies(state="active")
    active = []
    for s in all_strategies:
        regimes = json.loads(s["applicable_regimes"]) if s["applicable_regimes"] != "all" else None
        if regimes and regime.state not in regimes:
            continue
        # 方向过滤：Bear regime 下不激活纯多头策略（除非 confidence 低）
        if regime.state == "bear" and s["direction_affinity"] == "long" and regime.confidence > 0.7:
            continue
        if regime.state == "bull" and s["direction_affinity"] == "short" and regime.confidence > 0.7:
            continue
        active.append(s)
    return active
```

### 2.3 与现有流程对接

| 现有模块 | 改造 |
|---------|------|
| `dreaming_agent` | 生成策略时必须声明 direction_affinity + applicable_regimes |
| `paper_trader_ranker` | 过滤阶段检查策略是否在当前 regime 下激活 |
| `lifecycle_automation` | 按 regime 分别统计策略绩效（同一策略在不同 regime 下表现可能不同） |
| `strategy_evidence` | 拆分为 regime-aware 的证据（bull 下的 win_rate vs bear 下的 win_rate） |

### 2.4 内置做空策略种子

系统初始化时植入以下策略记录（state="active"）：

1. **funding_rate_short**
   - direction_affinity: "short"
   - applicable_regimes: ["bull", "sideways"] (多头拥挤时资金费率套利)
   - 触发：funding_rate > 0.08% 持续 2+ 期
   - scanner_family: "derivatives"

2. **mean_reversion_short**  
   - direction_affinity: "short"
   - applicable_regimes: ["bear", "sideways"]
   - 触发：RSI > 70 + BB% > 0.9 + ret_4h > 5%
   - scanner_family: "mean_reversion"

3. **breakdown_short**
   - direction_affinity: "short"
   - applicable_regimes: ["bear", "volatile"]
   - 触发：ret_1h < -2% + ret_4h < -4% + volume_zscore > 1.5
   - scanner_family: "momentum"

---

## Phase 3: Signal Directionality 重构

### 目标
方向成为信号的固有属性，而非 `_infer_direction()` 的后处理结果。

### 3.1 Scanner 层改造

**现状**：Scanner 用 `abs()` 打分，方向无关  
**目标**：每个 scanner 输出 `(score, direction, reasons)`

```python
# Before
def score_momentum(feat: dict) -> tuple[float, list[str]]:
    if abs(ret_1h) > 3.0:
        score += 30.0
    ...

# After
@dataclass
class ScannerSignal:
    score: float
    direction: str | None  # "long" | "short" | None
    reasons: list[str]

def score_momentum(feat: dict, regime: MarketRegime | None = None) -> ScannerSignal:
    # 多头信号
    long_score = 0.0
    short_score = 0.0
    reasons = []
    
    if ret_1h is not None and ret_1h > 1.5:
        long_score += 25.0
        reasons.append(f"1h up {ret_1h:+.1f}%")
    if ret_1h is not None and ret_1h < -1.5:
        short_score += 25.0
        reasons.append(f"1h down {ret_1h:+.1f}%")
    
    # Regime 加权
    if regime:
        long_score *= regime.long_bias
        short_score *= regime.short_bias
    
    # 输出方向明确的信号
    if long_score > short_score and long_score >= 20:
        return ScannerSignal(long_score, "long", reasons)
    if short_score > long_score and short_score >= 20:
        return ScannerSignal(short_score, "short", reasons)
    return ScannerSignal(0, None, reasons)
```

### 3.2 signal_emitter 简化

方向不再需要 `_infer_direction()`，因为 scanner 直接输出方向：

```python
# Before: emit → _infer_direction() → maybe None
# After:  scanner already says direction → emit directly

def emit_deterministic_opportunities(members, snapshot_id, scored_features, regime):
    for m in members:
        # direction 已经在 scanner 阶段确定了
        direction = m.get("direction")  # from run_all_scanners_v2()
        if direction is None:
            continue
        ...
```

### 3.3 `run_all_scanners` 改造

```python
def run_all_scanners_v2(feat: dict, regime: MarketRegime | None = None) -> dict:
    """Run all scanner families — each outputs direction-aware signals."""
    signals = {
        "momentum": score_momentum(feat, regime),
        "mean_reversion": score_mean_reversion(feat, regime),
        "volatility": score_volatility(feat, regime),
        "derivatives": score_derivatives(feat, regime),
    }
    
    # 选出最强信号（不再只看分数，还看方向匹配度）
    best = max(signals.values(), key=lambda s: s.score)
    
    return {
        "scanner_family": ...,
        "opportunity_score": best.score,
        "direction": best.direction,  # ← 关键变化
        "reasons": best.reasons,
        "all_signals": signals,
    }
```

### 3.4 Risk Engine 动态容量

```python
# aletheia/risk/engine.py — 新增 regime-aware 容量检查

def _check_regime_capacity(proposal, regime: MarketRegime) -> list[str]:
    violations = []
    cfg = load_config().risk
    
    # 动态容量 = 基础容量 × regime bias
    if proposal.direction == "short":
        max_short = cfg.max_open_positions_demo * regime.short_bias
        current_short = count_open_positions(direction="short")
        if current_short >= max_short:
            violations.append("regime_short_capacity_full")
    
    if proposal.direction == "long":
        max_long = cfg.max_open_positions_demo * regime.long_bias
        current_long = count_open_positions(direction="long")
        if current_long >= max_long:
            violations.append("regime_long_capacity_full")
    
    return violations
```

---

## 实施顺序与依赖关系

```
Phase 1a: RegimeConfig + detect_regime()          ← 独立，无依赖
Phase 1b: market_regime 表 + repository CRUD       ← 依赖 1a
Phase 1c: scheduler 接入 + signal_emitter 读取     ← 依赖 1b
Phase 1d: 前端 Regime 指示器                       ← 依赖 1b (API)
Phase 1e: risk_engine 读取 regime                  ← 依赖 1b

Phase 2a: StrategyRecord 扩展字段                  ← 依赖 Phase 1 完成
Phase 2b: strategy_selector + 激活逻辑             ← 依赖 2a
Phase 2c: 内置做空策略种子                         ← 依赖 2b
Phase 2d: dreaming_agent 输出 direction_affinity   ← 依赖 2a
Phase 2e: paper_trader_ranker regime-aware 过滤    ← 依赖 2b + 1c

Phase 3a: ScannerSignal dataclass + scanner 改造   ← 依赖 Phase 1 完成
Phase 3b: run_all_scanners_v2                     ← 依赖 3a
Phase 3c: signal_emitter 简化(移除 _infer_direction) ← 依赖 3b
Phase 3d: risk_engine 动态容量                     ← 依赖 1c + 3b
```

---

## 文件变动清单

### 新增文件
- `aletheia/regime/__init__.py`
- `aletheia/regime/detector.py` — 核心检测逻辑
- `aletheia/regime/strategy_selector.py` — 策略激活选择器
- `tests/test_regime_detector.py`
- `tests/test_strategy_selector.py`
- `frontend/src/components/RegimeIndicator.tsx`

### 修改文件
- `aletheia/config/schema.py` — 新增 RegimeConfig
- `aletheia/storage/models.py` — 新增 MarketRegimeRecord; StrategyRecord 加字段
- `aletheia/storage/migrations.py` — market_regime 表 + strategy 字段迁移
- `aletheia/storage/repositories.py` — regime CRUD + strategy 新字段查询
- `aletheia/agent_runtime/scheduler.py` — detect_regime 接入 data_collectors 后
- `aletheia/universe/scanners.py` — ScannerSignal + regime 参数
- `aletheia/universe/signal_emitter.py` — 读 regime、移除 _infer_direction
- `aletheia/universe/service.py` — 传递 regime 到 scanner + emitter
- `aletheia/agents/paper_trader_ranker.py` — regime-aware 策略过滤
- `aletheia/risk/engine.py` — 动态容量检查
- `aletheia/execution/paper_broker.py` — 用新 regime_tag 格式
- `aletheia/agents/dreaming_agent.py` — 输出 direction_affinity
- `frontend/src/pages/Settings.tsx` — Regime 配置区块
- `frontend/src/pages/Dashboard.tsx` — Regime 指示器

---

## 风险与约束

1. **向后兼容**：Phase 3 改造了 scanner 返回结构，必须让旧代码（`service.py` 读 `scanner_result["opportunity_score"]`）继续工作 → 使用 `run_all_scanners_v2` 新函数，旧函数保留过渡期
2. **数据连续性**：现有 `regime_tag = "risk_on/risk_off"` → 新的 `"bull/bear/sideways/volatile"` → 需要迁移脚本或双写过渡
3. **测试覆盖**：每个 Phase 完成后跑全量测试确认无回归
4. **MVP 原则**：不引入新外部依赖（CCXT BTC 数据已有、VIX via FRED/macro_collector 已有）

---

## 预期效果

- **Phase 1 完成后**：Bear 市场环境下 short 信号产出量增加 3-5x（阈值随 regime 调整）
- **Phase 2 完成后**：系统有专门的空头策略（funding_arb_short 等），不依赖通用 scanner 偶然触发
- **Phase 3 完成后**：方向是信号的固有属性，不再有"neutral bias 被跳过"的问题；风控动态平衡多空敞口
