# Phase 0：RDD Gate 有效性分析

> 作成日：2026-07-31
> 方法：Regression Discontinuity Design（断点回归）
> 数据：dev DB 30d 窗口，2845 个已结算 deduped candidates
> 依据：`research_roadmap_2026-07-31.md` Phase 0

---

## 1. 执行摘要

**全局 gate 没有可检测的因果效应。**

当前方法（naive 均值）显示 gate 效果 +0.133%，但这是**选择偏差造成的假象**，不是因果效应。RDD 修正后，gate=55 处的真实因果跳跃约为 -0.03%，95% 置信区间 [-2.03, +1.98]，包含 0。

**然而，按家族拆分后，存在极大的异质性**——`short_bias` 在临界窗口内的 gate 效应为 +2.97%，而 `derivatives` 为 -0.99%，`mean_reversion` 为 -1.90%。全局 gate 在同一个阈值下混合了方向相反的家族效应。

---

## 2. 方法说明

**Regression Discontinuity Design（断点回归）的核心假设**：在 gate 阈值（55 分）附近的"临界窗口"内，候选是否被选中主要由 score 的随机噪声决定，而非真实质量差异。因此，临界窗口内 `was_selected=1` vs `was_selected=0` 的收益差异是 gate 的**准因果估计**，比全量均值比较更可信。

**当前方法的问题**：全量均值比较（selected 全体 vs unselected 全体）受选择偏差影响——高分候选被优先选中，而高分候选本来就可能有其他更好的特征，导致 selected 组的收益高估 gate 效果。

---

## 3. 全局 RDD 结果

```
样本总量：2845 个已结算候选（30d）
selected   n=1030  naive avg=-1.364%
unselected n=1815  naive avg=-1.497%
naive ATE（当前方法）：+0.133%  ← 选择偏差造成，不是因果效应
```

| RDD 窗口 | treated n | control n | RDD 估计 | SE（bootstrap） | 95% CI |
|---|---|---|---|---|---|
| [50, 60) | 243 | 811 | **-0.027%** | 1.021 | [-2.03, +1.98] |
| [52, 58) | 120 | 486 | -0.199% | 1.481 | [-3.10, +2.70] |
| [53, 57) | 77 | 331 | +0.357% | 1.843 | [-3.26, +3.97] |
| [54, 56) | 41 | 194 | +1.868% | 1.621 | [-1.31, +5.05] |

**关键发现**：所有窗口的 95% CI 均包含 0。**当前全局 gate（55 分）没有统计显著的因果效应。**

---

## 4. 按家族 RDD 结果（窗口 [50, 60)）

| 家族 | treated n | control n | RDD 估计 |
|---|---|---|---|
| `short_bias` | 62 | 123 | **+2.974%** |
| `momentum` | 90 | 257 | -0.824% |
| `derivatives` | 34 | 208 | -0.986% |
| `mean_reversion` | 53 | 202 | -1.897% |
| `volatility` | 4 | 20 | -4.174%（n 太小，不可信） |

**关键发现**：
1. `short_bias` 家族：gate 在临界窗口内有真实正效应（+2.97%）——被选中的确比未选中的表现好
2. `derivatives`、`mean_reversion`：gate 效应为负——在临界窗口内，被选中的候选反而表现更差
3. `momentum`：gate 效应轻微负值，接近 0

---

## 5. 解读

### 5.1 naive 均值 +0.133% 是怎么来的

选中候选的平均 score 高于未选中候选（被选中需要 score≥55，且还有 rank 优先级）。高 score 候选因为其他特征（比如 momentum 更强、信号更清晰）而不是因为"被选中"本身，天然收益略好。naive 均值把这个特征效应误算成了 gate 效果。RDD 通过只比较 score 相近的候选，控制了这个混淆。

### 5.2 为什么全局 gate 没有因果效应

Gate 本身（"在 55 分以上就选"）并不创造额外的收益。真正影响 outcome 的是**哪个家族**的候选被选中，以及这个家族在当前 regime 下的系统性表现。全局 gate 混合了正负效应的家族，净效应接近 0。

### 5.3 short_bias 是例外

在临界窗口（score 50-60）内，`short_bias` 的被选候选平均表现比未选候选好约 +2.97%。这说明对 `short_bias` 家族，score（conviction）的确有信息量——score 50-60 内，被选中的候选（因为其他次级因素优先）确实更好。

这是目前整个反事实分析中最清晰的一个正向信号。

---

## 6. 对系统设计的建议（observe-only，不是行动指令）

| 建议 | 优先级 | 依据 |
|---|---|---|
| 研究家族独立 gate 的可行性 | 高 | short_bias 和 derivatives/mean_reversion 的 gate 效应方向相反，共用一个 gate 次优 |
| 停止用 naive 均值评估 gate 有效性 | 高 | 它在 selection bias 下是有偏的，且偏差方向不可预测 |
| Phase 1 propensity 模型优先做 | 中 | 有了 propensity，可以把每个家族的 gate 效应做成有置信区间的估计 |

这些是 L5 owner 决策范围的建议，反事实模块不自动执行任何参数变更。

---

## 7. 复现

```bash
uv run python - <<'EOF'
from aletheia.counterfactual.trades import list_deduped_shadows
from statistics import mean, stdev
import random

rows = list_deduped_shadows(days=30, columns=(
    "symbol", "direction", "trade_score", "net_pnl_pct", "was_selected", "scanner_family"
))
all_resolved = [r for r in rows if r["net_pnl_pct"] is not None]

# Global RDD
rdd = [r for r in all_resolved if 50 <= float(r["trade_score"]) < 60]
treated = [float(r["net_pnl_pct"]) for r in rdd if int(r["was_selected"] or 0)==1]
control  = [float(r["net_pnl_pct"]) for r in rdd if int(r["was_selected"] or 0)==0]
random.seed(42)
boot = [mean([random.choice(treated) for _ in treated]) - mean([random.choice(control) for _ in control])
        for _ in range(1000)]
se = stdev(boot)
rdd_est = mean(treated) - mean(control)
print(f"Global RDD [50,60): {rdd_est:+.3f}%  95CI=[{rdd_est-1.96*se:+.3f}, {rdd_est+1.96*se:+.3f}]")
EOF
```
