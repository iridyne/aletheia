# Universe Scanner 审计报告（完整版）

> 审计日期：2026-07-01  
> 审计范围：`scanners.py`、`features.py`、`service.py`、`filters.py`、`outcome_recorder.py`、`allocator.py`、`signal_emitter.py`、`registry.py`、`config/schema.py`  
> 数据来源：`universe_forward_outcomes` + `opportunities` + `universe_members` DB 实测 + 代码全量静态分析  
> 方法：主审 + 两路并行 Explore agent 交叉验证

---

## 0. 实施状态与核实订正（2026-07-01，分支 `fix/scanner-observer-audit-fixes`）

本报告 P0–P3 已按 `docs/plans/market-data/scanner_observer_audit_fix_plan_2026-07-01.md` 实施并通过 spec（UP-01~13）+ 回归测试。写报告后**逐条 grep + 读源码亲核**（宽 fan-out workflow 因 workspace rate-limit 全灭，反而逼出手动核实），发现以下根因偏差，特此订正：

- **4.1 `scanner_weights`**：非「无人读的死字段」。PI Operator 经 `_action_adjust_scanner_weight` 写入、`Settings.tsx` 展示、OL-C09 spec 锁定——**写入链完整，缺的是消费链**（`compute_total_score` 从不读它，只用自动 calibration）。已修（US-1：`opportunity *= scanner_weights.get(family, 1.0)`）。
- **4.10 event scanner**：结论「永远被竞争压制」**有误**。`score_event` 门槛 `ret_1h>8%→+45`、`>4%→+25`，配 vol×4(+30)+taker(+15) 可达 90 分，真触发能赢。0 条 forward outcome 真因 = 事件稀有 + `_infer_direction` **无 event 分支** → 赢竞争后 direction=None。已修（US-4）。
- **最高杠杆根因**：`service.py:256` 用 `_infer_direction` **重算**方向、丢弃 `scanner_direction`，一处同时造成 momentum 54% direction=None（4.4）+ event 0 条（4.10）+ forward outcome 方向与 opportunity 不一致（胜率统计双重失真）。已修（US-4，一处解三处）。
- **4.15 `all_scores`**：非「被丢弃」，`run_all_scanners` 返回且存于内存 `scored`，只是不入 `UniverseMember` 表。评估为**无消费者**，故 **不加列**（避免违反 data_utilization 契约「无消费者数据=负债」）。
- **4.6 `sync_registry` 失败**：`get_current_universe` 读侧**已 fallback 静态池** + `run.status="failed"` 已记录 → 报告担忧被现有机制覆盖，**无需额外代码**（US-7 降级为「已覆盖」，仅保留 US-14 的 debug 日志改进）。

各项最终处置见下方 §4/§5 与 plan 文档 §2/§3。

---

## 1. 当前行为总览

Universe Scanner 是一个 4 层流水线：

```
registry sync
  → feature collection (OHLCV + ticker → rs / rsi / atr / bb / vol_ratio / funding / OI)
  → tradability filter (volume / spread / freshness)
  → scanner scoring (5 family) → run_all_scanners 取最高分
  → allocator (配额 + explore/exploit)
  → signal_emitter → opportunities 表 + forward_outcomes 注册
```

**5 个 scanner family 及其核心信号：**

| Family | 主信号 | 次信号 |
|---|---|---|
| momentum | rs_beta_adj_24h / rs_4h | vol_ratio / taker_buy_ratio / oi_delta / RSI |
| mean_reversion | rsi_14（极值 ≥80 / ≤20）| ret_4h 过度延伸 / funding |
| volatility | atr_pct / bb_squeeze | vol_ratio / taker_buy_ratio |
| derivatives | funding_rate / oi_delta_1h | （无此二者时降级为 RSI proxy） |
| event | ret_1h（≥4% 或 ≤-4%） | vol_ratio / taker_buy_ratio |

---

## 2. 胜率实测数据与可信度评估

筛选条件：`|forward_return_pct| ≤ 50%`（排除历史污染），horizon=4h

| Family | direction=None 比例 | 方向正确的胜率（4h）| 数据量 |
|---|---|---|---|
| derivatives | 0.8% | ~67% | 118 条 |
| volatility | 0.7% | ~51% | 2906 条 |
| mean_reversion | 28% | ~51%（可信度中） | 1735 条 |
| momentum | **54%** | 49%（**可信度极低**） | 17694 条 |
| event | — | — | 0（从未入库） |

⚠️ **胜率数据严重警告**：

momentum 54%（9525/17694 条）的 forward outcome 是 `direction=None`——这些记录进入了 universe 但没有方向，win/loss 判断中分子永远 0。这意味着 momentum 的「49%胜率」统计基础被大量无方向记录稀释，实际有方向样本的胜率不可直接从当前数据读出。mean_reversion 也有 28% direction=None。

---

## 3. 历史污染问题（已有防护）

历史数据中存在 895,957% 级别异常返回（INJ/USDT entry≈5.58，exit≈50,000）。`service.refresh()` 在 settlement 前调用 `mark_outlier_outcomes_failed()`，统计时通过 `|return| ≤ 50%` 过滤。已有双重防护，不影响当前评估。

---

## 4. 发现的问题（全量）

### 4.1 ❌【P0-Critical】`scanner_weights` 是死字段——UI 调权重完全无效

**位置**：`aletheia/config/schema.py:483`

```python
scanner_weights: dict[str, float] = Field(default_factory=dict)
# 注释: operator-adjustable via adjust_scanner_weight
```

整个 `scanners.py` 和 `allocator.py` 里**没有任何代码读取该字段**。`compute_total_score`（`allocator.py:123`）、`run_all_scanners`（`scanners.py:424`）均不引用 `scanner_weights`。前端 Settings 页面如有对应控件，调整结果对实际评分完全无效。

---

### 4.2 ❌【P0-Critical】`family_quota` 缺 volatility 条目——volatility 可无限制占额

**位置**：`aletheia/config/schema.py:473-476`

```python
family_quota: dict[str, float] = Field(default_factory=lambda: {
    "momentum": 0.35, "mean_reversion": 0.25,
    "derivatives": 0.35, "event": 0.05,
})
```

`allocator.py:229`：volatility 的配额 fallback 是 `max(1, int(max_total * 0.3))`，可占 30% 额度无上限约束。已知 volatility 有 35 条 guard_candidates 失败记录（仅次于 momentum 51 条），质量有问题却可无约束占额。

---

### 4.3 ❌【P0-High】perp opportunity.market 被错标为 "spot"（DB 实测确认）

**位置**：`aletheia/universe/signal_emitter.py:70` + `service.py:251-260`

`service.py` 构建 `inserted_members` 时不传 `market_type` 字段：

```python
# service.py:251-260 — market_type 字段不在这里
inserted_members.append({
    "id": member.id,
    "snapshot_id": snap.id,
    "symbol": item["symbol"],
    "scanner_family": ...,
    "direction": ...,
    "entry_price_usd": ...,
    # ← 无 market_type
})
```

`signal_emitter.py:70`：`market_type = m.get("market_type", "spot") or "spot"` → 全部 fallback 到 `"spot"`。

**DB 实测**：`market=spot` 中有 **34 条 derivatives family opportunity**，而 derivatives 标的本质上是 perp。这些 opportunity 被 paper_trader 按现货路径执行，可能导致下单失败或交易所拒绝（perp 和 spot 接口不同）。

---

### 4.4 ❌【P1-High】momentum 54% direction=None 进入 universe——胜率统计不可信

**位置**：`aletheia/universe/features.py`（特征计算），`aletheia/universe/scanners.py:424-463`

`run_all_scanners` 在所有 family 都无方向时 fallback 返回 `scanner_direction=None`。`signal_emitter.py:82-88` 有 `continue` 跳过逻辑，但这发生在 `emit_deterministic_opportunities` 内——若跳过，这些成员不会生成 opportunity。

实测 `universe_forward_outcomes` 里 momentum 有 9525 条 direction=None（已结算 4h horizon）——这意味着这些成员**确实生成了 forward outcome 注册**（`register_pending` 在 `signal_emitter` 之前调用），但 scanner_direction 在 outcome 记录时也是 None。胜率计算中这些行分子永远 0，严重拉低表面胜率统计可信度。

---

### 4.5 ❌【P1-High】signal_emitter `feat_by_symbol` 按 symbol 去重——spot/perp 同名覆盖

**位置**：`aletheia/universe/signal_emitter.py:61`

```python
feat_by_symbol: dict[str, dict] = {f["symbol"]: f for f in scored_features}
```

同一 symbol 有 spot 和 perp 两条 feature 记录时，后者覆盖前者，顺序无保证。被覆盖的 feature 中的 `funding_rate`、`oi_delta_1h`、`return_4h` 可能来自错误品种，导致方向推断可能反转。

---

### 4.6 ❌【P1-High】registry sync 失败无降级——整个 refresh cycle 空转

**位置**：`aletheia/universe/registry.py:80` + `service.py:141-142`

`sync_registry` 内有 `try/finally` 保证 provider 关闭，但若 `exchange.load_markets()` 超时，异常传播到 `service.refresh()` 顶层，整个 refresh 以 `status="failed"` 告终，**本次 snapshot 不产生**。`get_current_universe` 此时才 fallback 到静态池，但这已是下一次查询——当前 cycle 产生的 opportunities 为零。

---

### 4.7 ❌【P1-High】`price/amount_precision int()` 截断 tick-size 精度

**位置**：`aletheia/universe/registry.py:141-142`

```python
price_precision=int(price_prec) if price_prec is not None else None,
amount_precision=int(amount_prec) if amount_prec is not None else None,
```

CCXT 的 `precision.price` 有时是 tick size 浮点数（如 `0.001`）而非小数位整数（如 `3`），取决于交易所的 `precisionMode`。`int(0.001) = 0`，导致精度信息完全丢失。下游构建 limit order 时数量/价格不符合交易所要求，订单被拒绝。

---

### 4.8 ❌【P1-High】BB squeeze 计算是 O(n²) CPU 热路

**位置**：`aletheia/universe/features.py:167-175`

```python
for i in range(20, min(100, len(closes_1h))):
    h_bb = bollinger(closes_1h[:i], 20, 2.0)  # 每次 O(n)，最多 80 次
```

30 标的 = 2400 次 bollinger 调用，纯 CPU-bound，在异步调度线程里执行会阻塞其他 IO 协程。

**修复**：预先计算完整 BB width 序列（一次 O(n) 遍历，按 window 滑动），从 O(n²) 降到 O(n)。

---

### 4.9 ❌【P1-Medium】`_infer_direction` 与 `scanner_direction` 逻辑分裂

**位置**：`aletheia/universe/signal_emitter.py:82`, `service.py:256`

两处问题：
1. `signal_emitter.py:82` 优先用 `feat.get("scanner_direction")`，然后 fallback `_infer_direction`。但 `_infer_direction(momentum)` 要求 `ret_4h` 方向 + RSI 双重确认，而 `score_momentum` 用 `rs_24h` 作主信号——`rs_24h > 5` 成立但 `ret_4h=None` 时，`scanner_direction="long"` 但 `_infer_direction` 返回 `None`，造成有效信号丢失。

2. `service.py:256` 构建 `inserted_members` 时**单独调用** `_infer_direction`（不用 `scanner_direction`），而 outcome_recorder 用此方向计算胜率——`inserted_members["direction"]` 与 opportunity 的实际 direction 可能不同，胜率统计被额外污染。

---

### 4.10 ❌【P1-Medium】event scanner 在竞争下永远被压制

**根因**：`score_event` 触发需 `|ret_1h| > 4%`（25 分），而 momentum/volatility 日常就能达到 30-50 分。`run_all_scanners` 全竞争机制无保留位。

**影响**：DB 实测：`event` family 从未出现在 `universe_forward_outcomes` 中（0条记录），已存在多月。意外催化剂时 event scanner 失效。

---

### 4.11 ❌【P1-Medium】`short_enabled=False` 时高分 short 信号可绕过 regime gate

**位置**：`aletheia/universe/signal_emitter.py:199-200`

`short_enabled=False` → `regime.short_bias=0.0`，但 `_passes_regime_gate` 对 `score >= _HIGH_CONF_THRESHOLD * 1.1 = 66` 的信号有豁免逻辑，高分 short 可绕过该限制，违背管理员意图。

---

### 4.12 ❌【P1-Medium】explore 候选含 0 分标的但 `execution_eligible=True`

**位置**：`aletheia/universe/allocator.py:240-243`

`random.sample(remainder)` 中的 `remainder` 包含被配额踢出的候选（分数可能很低甚至 0），这些 explore 候选全部被标记为 `execution_eligible=True`。

---

### 4.13 ❌【P1-Medium】`_has_sufficient_signal` 正则匹配非 RSI 列

**位置**：`aletheia/universe/features.py`（生成 context），`aletheia/agents/market_observer.py:157`

RSI 正则：`RSI[^|]*\|[^|]*\|[^|]*?(\d+\.?\d*)`——跳过两个 `|` 匹配数值，但 RSI 在表格中是第 5 列，正则实际抓到的是第 3 列（SMA20 附近），导致预过滤判断偏差。

---

### 4.14 ❌【P2-Medium】derivatives 和 mean_reversion 近期完全消失

**DB 实测**：最近 10 个 snapshot 仅有 momentum + volatility。

**derivatives 原因**：funding_rate/oi_delta_1h 为 None 时跌入 RSI proxy（`scanners.py:311`），proxy 信号弱（20 分）被压制。

**mean_reversion 原因**：需 RSI ≥80/≤20（横盘期极少触发）或 |ret_4h| > 8%，常规市场无法竞争。

---

### 4.15 ❌【P2-Medium】`all_scores` 5 家分数不写入 DB

**位置**：`aletheia/universe/scanners.py:456`

`run_all_scanners` 返回完整 5 家分数字典，但 `UniverseMember` 只存最高分者，其余 4 家分数被丢弃。无法做 ensemble 分析，无法检查第二名差距是否很小（模糊信号）。

---

### 4.16 ❌【P2-Medium】registry `demo_whitelist` 静默过滤 perp

**位置**：`aletheia/universe/registry.py:120`

`canonical = f"{base}/{quote}"` 格式为 `BTC/USDT`，demo whitelist 若用不同格式（如 OKX 的 `BTC/USDT:USDT`），perp 被静默排除（`continue` 无任何日志），demo 环境无 perp opportunity。

---

### 4.17 ❌【P2-Low】`_is_excluded` 的 `"_" in symbol_id` 过滤过于宽泛

**位置**：`aletheia/universe/registry.py:32`

KuCoin 等交易所原始格式为 `BTC_USDT`，会被该规则全量过滤。切换交易所时 registry 可能为空，且无任何警告日志。

---

### 4.18 ✅ 已有合理机制

- `ret_24h > 10% → score -= 20`（超延伸惩罚）
- `funding crowded long/short → score -= 15`（方向冲突惩罚）
- `mark_outlier_outcomes_failed()` 防止历史污染
- `_passes_regime_gate` 按市场 regime 过滤方向
- `calibration multipliers`（`get_family_multipliers`）动态调整 family 分数权重
- `explore/exploit` 机制防止全部收敛到一个 family

---

## 5. 改善建议（优先级排序）

### P0 — 立即修（配置层硬 bug）

**P0-A：`scanner_weights` 接入 `compute_total_score`**（`allocator.py:135-138`）
```python
weight = cfg.scanner_weights.get(family, 1.0)  # 读取 scanner_weights
opportunity *= calibration.get(family, 1.0) * weight
```

**P0-B：`family_quota` 补 volatility**（`schema.py:473`）
```python
"volatility": 0.15,  # 加入配额约束，建议 15%（最多 4-5 个名额）
```
同步调整其他 family 使总和 ≤ 1.0。

### P1 — 关键信号正确性

**P1-A：修 `service.py` 的 `inserted_members` 补 `market_type` 字段**
```python
inserted_members.append({
    ...
    "market_type": item.get("market_type", "spot"),  # 新增
})
```

**P1-B：event scanner 强制保留位**
```python
# run_all_scanners 内：若 event score > 30 且 vol_ratio > 2，不参与竞争
if results["event"][0] > 30 and feat.get("vol_ratio", 0) > 2.0:
    return {"scanner_family": "event", ...}
```

**P1-C：加最低分门槛 25 分**
```python
if best_score < 25:
    return {"scanner_family": None, "opportunity_score": 0, ...}
```

**P1-D：BB squeeze O(n²) → O(n)**
```python
# 预计算完整 width 序列
widths = []
for i in range(20, len(closes_1h)):
    bb = bollinger(closes_1h[i-20:i+1], 20, 2.0)
    if bb and bb[1] > 0:
        widths.append((bb[2] - bb[0]) / bb[1] * 100)
avg_width = sum(widths) / len(widths) if widths else None
bb_squeeze = (bb_width_pct < avg_width * 0.6) if avg_width else False
```

**P1-E：修 `price_precision` 精度截断**（`registry.py:141-142`）
```python
# 检测 precisionMode 决定是否直接用浮点数
price_precision = price_prec if isinstance(price_prec, float) and price_prec < 1 else int(price_prec) if price_prec else None
```

### P2 — 数据质量和可观测性

**P2-A：regime-aware scanner 权重**（sideways → momentum × 0.6，mean_reversion × 1.2）

**P2-B：`all_scores` 写入 `UniverseMember`**（便于 ensemble 和调试）

**P2-C：explore 候选加最低分过滤**（`total_score > 10` 才可进 explore pool）

**P2-D：`sync_registry` 失败时发 doctor warn 而非直接跳过**

---

## 6. 效率改善

| 问题 | 影响 | 建议 |
|---|---|---|
| BB squeeze O(n²) | CPU 热路，30 标的 = 2400 次 bollinger | **P1-D**（见上） |
| `settle_due()` 在 `refresh()` 关键路径 | 包含 OHLCV 调用，拖慢 snapshot 生成 | 拆为独立调度任务 |
| `get_family_multipliers` 每次 refresh 调用 | `compute_family_scorecard` 做多表 JOIN | 加 5 分钟 TTL 缓存 |
| `feat_by_symbol` dict comprehension | spot/perp 覆盖 | 改为 `(symbol, market_type)` 作复合 key |

---

## 7. 问题优先级总表

| 优先级 | 位置 | 描述 | DB 实测 |
|---|---|---|---|
| P0-Critical | `schema.py:483` | `scanner_weights` 死字段 | 配置无效 |
| P0-Critical | `schema.py:473` | `family_quota` 缺 volatility | 35 条失败 guard，无配额约束 |
| P0-High | `signal_emitter.py:70` + `service.py:251` | perp market_type 丢失→错标 spot | 34 条 derivatives 在 spot 市场 |
| P1-High | `features.py:139` + `forward_outcomes` | momentum 54% direction=None，胜率统计不可信 | 9525/17694 条 |
| P1-High | `signal_emitter.py:61` | feat_by_symbol spot/perp 同名覆盖 | — |
| P1-High | `registry.py:80` + `service.py:142` | sync_registry 失败无降级，refresh cycle 空转 | — |
| P1-High | `registry.py:141` | price/amount_precision int() 截断 tick-size | — |
| P1-High | `features.py:167-175` | BB squeeze O(n²)，2400 次 bollinger | — |
| P1-Medium | `signal_emitter.py:82` + `service.py:256` | _infer_direction 与 scanner_direction 逻辑分裂 | — |
| P1-Medium | `scanners.py` + `forward_outcomes` | event scanner 从未产出 | 0 条记录 |
| P1-Medium | `signal_emitter.py:199` | short_enabled=False 高分 short 绕过 regime gate | — |
| P1-Medium | `allocator.py:243` | explore 含 0 分标的 + execution_eligible=True | — |
| P2-Medium | `scanners.py` | derivatives 和 mean_reversion 近期消失 | 最近 10 snapshot 缺席 |
| P2-Medium | `scanners.py:456` | all_scores 不写 DB | — |
| P2-Medium | `registry.py:120` | demo_whitelist 静默过滤 perp | — |
