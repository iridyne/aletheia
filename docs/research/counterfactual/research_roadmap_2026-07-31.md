# 反事实分析升级路线图

> 作成日：2026-07-31
> 依据：`frontier_research_2026-07-31.md` — 学术前沿到当前实现的落地转化
> Issue 追踪：#511（umbrella）→ #512 #513 #514 #515（child）

---

## 0. 诊断结论（一句话）

当前 `build_calibration()` 使用朴素均值（naive empirical mean），在 `was_selected` 存在选择偏差的条件下是有偏估计。`was_selected`、`trade_score`、`scanner_family` 三个字段**已存在**，升级估计量所需的数据基础完整，可以立即开始。

---

## 1. 四个研究阶段

### Phase 0 — RDD 快速验证（当前数据，不改代码）

**核心问题**：Gate（score≥55）到底有没有真实的因果效应？

**方法**：Regression Discontinuity Design（断点回归）  
在 score 50-60 的"临界窗口"内，`was_selected=1` vs `was_selected=0` 几乎是准随机的，因为临界附近的分数差异主要是测量噪声而非真实质量差异。

**执行**：直接查询 `shadow_trades` 即可，无需任何代码变更：

```python
rdd_window  = [r for r in all_rows if 50 <= r["trade_score"] < 60]
rdd_treated = [r for r in rdd_window if r["was_selected"] == 1]
rdd_control = [r for r in rdd_window if r["was_selected"] == 0]
rdd_estimate = mean(r["net_pnl_pct"] for r in rdd_treated) - \
               mean(r["net_pnl_pct"] for r in rdd_control)
# > 0 → gate 有因果效应; ≈ 0 → gate 无因果效应，conviction score 对选择无实质区分
```

**预期输出**：一个单一数字 + 按 0.5 分区间画出的连续性图（score vs avg_net，看有没有 55 处的跳跃）。

**交付**：脚本 `scripts/rdd_gate_validity.py` + 结果写入 `05_rdd_gate_validity_2026-07-31.md`

**所需时间**：1-2 天  
**依赖**：无，现有数据即可  
**Issue**：#512

---

### Phase 1 — Propensity 模型（数据积累后，1-2 月）

**核心问题**：每个候选被选中的概率是多少？

**背景**：没有 propensity 估计，就无法做任何无偏 OPE 估计（IPS/DR）。当前系统是一个"隐性的 deterministic gate"——score≥55 且不超 position limit 就选中——但实际上还有 rank、portfolio 冲突、capital 限制等次级因素，导致即使 score≥55 也未必被选中。

**方法**：在历史 shadow 数据上拟合选择模型：

```
P(was_selected=1 | trade_score, scanner_family, direction, market_type, batch_rank)
```

```python
from sklearn.linear_model import LogisticRegression
from lightgbm import LGBMClassifier

# 特征：score, family, direction, batch_rank（在同一 batch 里的排名），hour_of_day
X = feature_matrix(shadow_rows)
y = [r["was_selected"] for r in shadow_rows]

# 第一步：logistic 作为 baseline（可解释）
propensity_model = LogisticRegression(C=1.0).fit(X, y)
propensity_scores = propensity_model.predict_proba(X)[:, 1]  # P(selected=1)
```

**关键约束**：
- propensity 必须 clip 到 [ε, 1-ε]（通常 ε=0.05），防止极端权重
- 使用 cross-fitting（k-fold）避免 overfitting 偏差
- 对于 deterministic gate 部分（score<55 几乎不选），propensity 会自然接近 0

**交付**：`aletheia/counterfactual/propensity.py` — 独立模块，输出 propensity scores  
**所需时间**：1 周（代码）+ 数据积累等待  
**依赖**：需要 ≥500 个已结算 shadows，最好跨多个 policy_epoch  
**Issue**：#513

---

### Phase 2 — DR 估计量替换朴素均值（Phase 1 完成后，2-4 月）

**核心问题**：用有偏的均值换成无偏的 Doubly Robust 估计量。

**方法**：Doubly Robust（DR）估计量，需要两个模型：
1. **Outcome model** `r̂(x, a)`：预测给定特征 x 和 action a 时的期望收益
2. **Propensity model** `π_b(a|x)`：Phase 1 的输出

```
DR(π_new) = (1/n) Σ [ r̂(x_i, a_i^new) + w_i · (r_i - r̂(x_i, a_i)) ]
```

其中 `w_i = π_new(a_i|x_i) / π_b(a_i|x_i)`，`a_i^new` 是新策略的选择。

**对 `build_calibration()` 的改动**：
```python
# 当前
def _summarize_band(rows):
    nets = [float(r["net_pnl_pct"]) for r in rows]
    return {"avg_net_pct": mean(nets), ...}

# 升级后
def _dr_estimate(rows, target_gate, propensity_model, outcome_model):
    # Doubly Robust 估计目标策略 target_gate 在这批 rows 上的期望收益
    # 如果 propensity 对 OR outcome model 对，估计量就是一致的
    ...
```

**好处**：
- `build_calibration(target_gate=50)` 可以估计"如果 gate 是 50，期望收益是多少"
- 不只是"score 50-55 的候选平均收益是多少"（后者有选择偏差）
- 置信区间更窄，结论更可信

**交付**：`aletheia/counterfactual/estimators.py` — DR 估计量；更新 `build_calibration()` 支持两种模式（naive 和 DR）  
**所需时间**：2 周  
**依赖**：Phase 1 完成（propensity 模型可用）  
**Issue**：#514，依赖 #513

---

### Phase 3 — Regime 分层 + Causal Forest（4-8 月）

**核心问题**：`derivatives` 家族为什么在 2026-07 全输？哪些 signals 是跨 regime 稳定的？

**方法**：两步走

**Step A — Regime 标签**：
```python
# 用 BTC rolling volatility / trend indicator 定义 regime
# 例：BTC 30d realized vol > 60% → regime="high_vol"
# BTC 20d trend > +15% → regime="bull"
# BTC 20d trend < -15% → regime="bear"
```

**Step B — Causal Forest（EconML）**：
```python
from econml.grf import CausalForest

# 输入：shadow 候选的 scanner features（信号值）
# Treatment：was_selected（0/1）
# Outcome：net_pnl_pct
# Heterogeneity covariate：regime, scanner_family, direction

cf = CausalForest(n_estimators=200, min_samples_leaf=10)
cf.fit(X_features, T=was_selected, Y=net_pnl_pct)
# 输出：每个候选的个体化处理效应（ITE）
# 关键分析：哪些特征在 regime 分层后仍有稳定的正 ITE？
```

**主要回答的研究问题**：
1. Gate 对不同家族的 CATE 是否显著不同？（不同家族是否应该有独立 gate？）
2. 哪些 scanner features 的预测效果是跨 regime 稳健的（IRM 意义上的不变量）？
3. `derivatives` 的负 CATE：是成本模型问题还是信号问题？

**交付**：`scripts/causal_forest_analysis.py` + 报告 `06_causal_forest_cate_analysis.md`  
**所需时间**：3-4 周（含数据准备）  
**依赖**：Phase 2 完成；需要生产数据（≥90d，多 regime 跨越）  
**Issue**：#515，依赖 #514

---

## 2. 依赖关系图

```
Phase 0 (RDD)          ─── 独立，现在可以做
        │
Phase 1 (Propensity)   ─── 等数据积累（现有 331 条 short_bias / 1020 条 momentum 可先试）
        │
Phase 2 (DR Estimator) ─── 依赖 Phase 1
        │
Phase 3 (Causal Forest) ── 依赖 Phase 2 + 90d 生产数据
```

---

## 3. 数据需求矩阵

| Phase | 最小样本 | 字段需求 | 当前满足？ |
|---|---|---|---|
| Phase 0 (RDD) | 200（score 50-60 窗口内） | trade_score, was_selected, net_pnl_pct | ✅ dev DB 可用 |
| Phase 1 (Propensity) | 500+ 已结算，跨多家族 | + scanner_family, direction, batch_rank | 🔶 dev DB 331+1020，够 pilot |
| Phase 2 (DR) | 1000+ | + outcome model features | 🔶 勉强够 pilot |
| Phase 3 (Causal Forest) | 2000+，跨多 regime | + regime label（需外部数据）| ❌ 需要生产 90d 数据 |

---

## 4. 快速赢：今天就能跑的 RDD

以下查询直接用现有 dev DB，**不依赖任何代码变更**：

```bash
# Phase 0 preview
uv run python - <<'EOF'
from aletheia.counterfactual.trades import list_deduped_shadows
from statistics import mean, stdev

rows = list_deduped_shadows(days=30, columns=(
    "symbol", "direction", "trade_score", "net_pnl_pct", "was_selected", "scanner_family"
))
all_resolved = [r for r in rows if r["net_pnl_pct"] is not None]

# RDD window: score [50, 60)
rdd = [r for r in all_resolved if 50 <= float(r["trade_score"]) < 60]
treated = [r for r in rdd if int(r["was_selected"] or 0) == 1]
control  = [r for r in rdd if int(r["was_selected"] or 0) == 0]

t_nets = [float(r["net_pnl_pct"]) for r in treated]
c_nets = [float(r["net_pnl_pct"]) for r in control]

print(f"RDD window [50,60): treated n={len(t_nets)}, control n={len(c_nets)}")
if t_nets and c_nets:
    rdd_est = mean(t_nets) - mean(c_nets)
    print(f"RDD estimate (gate causal effect): {rdd_est:+.3f}%")
    print(f"  treated avg: {mean(t_nets):+.3f}%  control avg: {mean(c_nets):+.3f}%")
    print(f"  interpretation: {'gate has causal effect ✓' if rdd_est > 0 else 'gate effect not detectable in this window'}")
EOF
```

---

## 5. Issue 追踪结构

```
#511 [umbrella] counterfactual OPE upgrade roadmap
  └─ #512 [Phase 0] RDD gate validity analysis
  └─ #513 [Phase 1] propensity model for shadow_trades selection
  └─ #514 [Phase 2] DR estimator for build_calibration()
  └─ #515 [Phase 3] Causal Forest + regime-stratified CATE analysis
```

现有 Issue 关联：
- #509（calibration findings）→ 被 #514 解决（DR 估计量替换 naive mean）
- #508（data audit）→ Phase 0 RDD 的数据完整性前提

---

## 6. 成功标准

| Phase | 可验证的成功指标 |
|---|---|
| Phase 0 | `rdd_estimate` 有数值输出，附 95% CI（bootstrap）；发现 gate 有效或无效 |
| Phase 1 | propensity 模型 AUC > 0.7；propensity 分布非退化（不全部 →0 或 1） |
| Phase 2 | DR 估计结果与 naive 均值差异可测量；`build_calibration()` 可接受 `method="dr"` 参数 |
| Phase 3 | 至少一个 scanner feature 的跨 regime CATE 显著正值（p<0.05） |
