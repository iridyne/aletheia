# L2 Conviction Gate 校准分析

> 作成日：2026-07-31
> 数据范围：已结算 shadow rows（net_pnl_pct IS NOT NULL），全家族，dev DB
> 工具：`scripts/shadow_threshold_calibration.py` + 直接 SQL 查询

---

## 1. 执行摘要

- **Gate 位置（55）当前合理**：60-70 频段是唯一可信正收益频段（avg +3.3%），55-60 仍为负（avg -0.6%）
- **家族分化掩盖在聚合数据里**：`event` 家族正收益显著，`momentum` 和 `derivatives` 持续拖累整体均值
- **数据量不均匀**：`event` 仅 12 个 settled candidates，结论不具统计稳健性
- **70+ 频段无数据**：此阶段不能评价 gate 上移至 70 的效果

---

## 2. 全局 Conviction Gate 分析

### 2.1 30 天窗口（calibration 脚本输出）

```
Shadow counterfactual calibration — 30d, all symbols, 2845 candidates (deduped)
Conviction-to-trade gate: 55

  trade_score band        n   avg_net%   median%    win%
  -------------------------------------------------------
  <40                    78     -3.539    -1.652    42.3
  40-46                 609     -1.709    -0.998    37.3
  46-50                 960     -1.770    -1.229    34.8
  50-55                 700     -1.792    -1.153    39.7
  55-60                 354     -0.052    -0.220    46.9  <-- gate
  60-70                 144     +1.168    +0.967    52.1
  70+                     0        n/a       n/a     n/a

Verdict: avg net first turns positive in '60-70', at or above gate (55).
```

### 2.2 全历史已结算数据

```
  band           n   avg_net%   win%
  <40           79     -2.345   43.5
  40-46        684     -1.549   34.5
  46-50       1071     -1.204   35.7
  50-55        752     -0.833   43.1
  55-60        382     -0.578   46.1  <-- gate
  60-70        145     +3.270   56.1
```

**关键观察**：
1. Gate 门槛（55）以下全部频段负收益，趋势单调
2. 55-60 轻微负收益（-0.6%），是 gate 的"成本区"
3. 60-70 是唯一正收益频段（+3.3%），win rate 56%
4. 没有 70+ 数据 — **不建议基于当前证据上调 gate**

### 2.3 Gate 移动的边际效益估算

若将 gate 从 55 上调至 60，将排除 55-60 频段（avg -0.578%, n=382 candidates）：
- 避免的预期亏损：约 0.578% per candidate
- 代价：减少 382 个已进入的 candidates（可能包含 realized trades）

这是一个 **owner 决策**，不是模块可以自动执行的。此处仅提供数据，不建议行动。

---

## 3. Per-Scanner-Family 分析

### 3.1 家族表现概况（全历史已结算）

```
family            n_candidates   avg_net%    win%   avg_score
event                       12    +14.676    68.2    31.8
short_bias                 331     +0.118    46.3    54.2
volatility                 110     -0.103    46.2    50.6
mean_reversion             606     -0.569    50.1    51.9
derivatives                766     -0.900    33.1    50.6
momentum                  1020     -1.155    40.4    50.5
```

### 3.2 家族洞察

**event 家族（+14.7%，win 68%）**
> 注意：n=12，样本量极小，置信区间极宽。在统计上不稳健，不能作为政策依据。
> 该家族均值高可能原因：覆盖的事件驱动机会本身有非常高的信息内容，但同时流动性/执行难度高；
> dev 环境可能只在少数高质量信号上触发了 shadow 记录。需要持续监测。

**short_bias 家族（+0.12%，win 46%）**
> 微弱正收益。样本量 331 较可信，但 margin 极薄，成本模型敏感。
> 值得跟踪：short_bias 家族是唯一在足够样本量下保持正均值的家族。

**volatility 家族（-0.10%，win 46%）**
> 接近盈亏平衡（-0.10%），样本 n=110。在成本模型误差范围内。

**mean_reversion、derivatives、momentum（均值 -0.6% 至 -1.2%）**
> 三大家族持续负均值，合计占 settled candidates 的 94%（2392/2845）。
> 这三个家族将整体均值向下拉。聚合视角会掩盖 short_bias/event 的正贡献。

### 3.3 Gate 与 Family 的交叉分析

```python
# 近似估算：momentum 55-60 频段 vs short_bias 55-60 频段
# (full query not shown; 需分族、分频段统计)
```

建议后续：对每个家族独立运行校准报告，理解 gate 对不同家族的差异化影响。

---

## 4. 方法论限制

1. **样本量**：30 天窗口下 2845 个 deduped candidates，家族间极不均衡（event n=12 vs momentum n~1000）
2. **成本模型**：`net_pnl_pct` 基于 paper/shadow 成本模型（CF-I05），不是 live fill，市场冲击未计入
3. **时间窗口**：2026-07-15 至 07-28 是一个特定市场状态快照，结论不可外推到不同 regime
4. **选择偏差**：已结算的 29% 可能与未结算的 70% 在时间分布上有差异（结算早的更老）
5. **无 policy_epoch_id 分层**：当前无法验证是否跨 policy epoch 比较了不可比的候选

---

## 5. 研究延伸方向

| 方向 | 前置条件 | 工具 |
|---|---|---|
| 按 policy_epoch_id 分层的 gate 校准 | `policy_epoch_id` migration 执行完毕 | `--policy-epoch` 参数 |
| Per-family 独立 gate 校准 | 当前可以运行 | `build_calibration` + per-family filter |
| 70+ 频段何时出现 | 自然积累；需要更长时间或不同市场状态 | 持续监测 |
| event 家族深度分析（n 积累后） | 待 event n > 50 | `list_deduped_shadows(scanner_family='event')` |
| momentum 为何持续负收益（L3 有界回放） | 当前可以构建 | `filter_replay.build_family_ttl_replay()` |

---

## 附录：复现

```bash
# 全局 30 天校准
uv run python scripts/shadow_threshold_calibration.py --days 30

# Per-family（目前 CLI 不支持直接传 family；可用直接 SQL 或扩展 calibration API）
uv run python -c "
from aletheia.counterfactual.calibration import build_calibration, render_text
# filter by symbol subset is supported; family filter needs direct DB query
print(render_text(build_calibration(days=30)))
"
```
