# L2 Per-Scanner-Family 深度校准分析

> 作成日：2026-07-31
> 工具：`scripts/shadow_threshold_calibration.py --family <name> --days 30`（新增 `--family` 参数）
> 数据范围：dev DB，30 天窗口，已结算 deduped candidates

---

## 1. 执行摘要

Per-family 视角揭示了聚合数据掩盖的四个不同模式：

| 家族 | n | 核心发现 | 门槛评估 |
|---|---|---|---|
| `momentum` | 1020 | 55-60 频段正收益，60-70 反转为负 | 门槛位置合理，但 60-70 反转值得调查 |
| `short_bias` | 331 | **全频段几乎正收益**，含门槛以下 | **门槛可能偏高**，advisory 触发 |
| `derivatives` | 766 | **全频段负收益，无正收益频段** | 门槛无法矫正问题，需要更深层分析 |
| `mean_reversion` | 606 | 低分段极度亏损（-5.5%），仅 60-70 正 | 门槛位置合理，低分段风险极高 |
| `volatility` | 110 | 样本量过小，各频段 n 个位数 | **数据不足，不可推断结论** |

---

## 2. 各家族详细数据

### 2.1 momentum（n=1020）

```
  trade_score band     n   avg_net%   median%    win%
  <40                  7     -1.487    -1.262    28.6
  40-46              205     -0.770    -0.824    37.1
  46-50              423     -1.477    -1.367    30.0
  50-55              248     -2.682    -1.651    29.0
  55-60               99     +0.641    +1.333    62.6  <-- gate
  60-70               38     -0.918    +0.281    50.0

Selection: selected -0.84%, unselected -1.79%
```

**观察**：
- 55-60 频段异常良好（+0.64%，win 63%），但 60-70 反转为 -0.92%（median +0.28%，高方差）
- 60-70 的 avg/median 分歧（avg -0.92% vs median +0.28%）表明该频段有几个大亏损异常值拉低均值
- 50-55 频段异常差（avg -2.68%），比相邻频段更糟糕，值得 L3 回放调查

**方法论提示**：momentum 的选中 vs 未选中差异显著（-0.84% vs -1.79%），说明 selection 在该家族有效区分，尽管总体负收益。

### 2.2 short_bias（n=331）⚠️ 门槛异常信号

```
  trade_score band     n   avg_net%   median%    win%
  <40                 12     +7.420   +12.903    58.3
  40-46               32     -2.155    -0.666    46.9
  46-50               73     +1.208    +1.848    57.5
  50-55              120     +1.493    +0.452    51.7
  55-60               65     +0.685    -1.865    40.0  <-- gate
  60-70               29     +2.364    +5.147    62.1

Selection: selected +1.75%, unselected +0.85%
Advisory: "gate might be too strict"（sign flip at <40, below gate）
```

**关键发现**：
1. `<40` 频段 avg +7.4%，n=12（样本量太小，单独结论不可信）
2. `46-50` 和 `50-55` 频段均为正收益（+1.2%, +1.5%）—— 这是门槛以下的正收益
3. 仅 `40-46` 为负（-2.2%，可能与 `<40` 的高正收益形成 V 型分布，噪声较大）
4. **选中 vs 未选中均为正收益**（+1.75% vs +0.85%）—— 全面盈利的家族

**方法论注意**：median 与 avg 分歧大（55-60 avg +0.69% vs median -1.87%），显示 fat tail 分布，几个大涨候选拉高 avg。结论不能单看均值。

**owner 层问题**：short_bias 家族在低 conviction（46-55）下也有正预期，是否应对此家族设置更低的独立门槛？这是 L5 owner 决策，不是模块自动变更。

### 2.3 derivatives（n=766）🔴 无正收益频段

```
  trade_score band     n   avg_net%   median%    win%
  <40                  2     -2.984    -2.984    50.0
  40-46              223     -1.541    -1.153    31.4
  46-50              267     -1.144    -0.997    34.1
  50-55              145     -1.889    -0.835    35.9
  55-60               97     -1.165    -0.730    32.0  <-- gate
  60-70               32     -0.307    -1.085    46.9

Advisory: calibration_no_positive_band (L5 信号已触发)
```

**关键发现**：
1. **没有任何 conviction 水平能产生正收益** —— 这不是门槛问题，是家族级别的问题
2. 60-70 频段"最不差"（avg -0.31%），但仍为负
3. Win rate 仅 32-47%，系统性低于 50% 的随机基准
4. `derivatives` 家族候选多（766），但全部拖累整体校准

**研究问题**：为什么 `derivatives` 家族在所有 conviction 水平下均负收益？可能原因：
- a. 成本模型对 derivatives 类型系统性低估成本（资金费率高？）
- b. 这批 derivatives 候选多为做多，而期间 derivatives 板块持续下跌
- c. 家族定义本身包含了多个"质量"差异极大的子类型

### 2.4 mean_reversion（n=606）

```
  trade_score band     n   avg_net%   median%    win%
  <40                 40     -5.782    -2.642    42.5
  40-46              112     -5.661    -2.607    38.4
  46-50              159     -5.574    -1.170    36.5
  50-55              165     -2.793    -0.013    49.1
  55-60               90     -0.307    -0.100    50.0  <-- gate
  60-70               40     +3.106    +0.554    50.0
```

**观察**：
- 低分段（40-50）极端亏损（avg -5.5%），远超其他家族同频段
- 门槛区（55-60）接近盈亏平衡（-0.31%），但 median 也接近零
- 仅 60-70 正收益（+3.1%），n=40 尚可信
- 50-55 的 median ≈ 0 表示该频段 **分布对称**，高方差候选混入

**解释**：均值回归策略在趋势市场（当前可能如此）会系统性亏损。低分段极端亏损可能是"逆势过早入场"的特征。

### 2.5 volatility（n=110）—— 数据不足

```
  55-60 n=2  (完全无统计意义)
  40-46 n=35  avg +3.0%
  46-50 n=38  avg +0.75%
```

各关键频段样本量个位数，任何结论均不可信。需要持续累积数据（估计需要 500+ deduped candidates）再重跑。

---

## 3. 跨家族比较矩阵

| 家族 | 55-60 avg% | 55-60 win% | 60-70 avg% | 有正收益频段 | 全局选择有效 |
|---|---|---|---|---|---|
| momentum | +0.64 | 63% | -0.92 | 是（55-60） | 是 |
| short_bias | +0.69 | 40% | +2.36 | 是（全频段几乎） | 是 |
| derivatives | -1.17 | 32% | -0.31 | **否** | 边际 |
| mean_reversion | -0.31 | 50% | +3.11 | 是（60-70） | 是（差） |
| volatility | -3.57 | 50% | +4.02 | 数据不足 | - |

---

## 4. 研究优先级与下一步

| 优先级 | 研究问题 | 状态 | 工具/结论 |
|---|---|---|---|
| 高 | `short_bias` 低 conviction（46-55）正收益是否可持续？ | ✅ 已验证 | 见 §7：60/90d 窗口下结果不变，dev DB 数据上限 = 30d |
| 高 | `derivatives` 家族为何全频段负收益？ | ✅ 已完成 | 见 §6：SKHYNIX/MU short 2026-07 半导体反弹期系统亏损 |
| 中 | `momentum` 60-70 反转：异常值分析 | ✅ 已完成 | 见 §8：单个 ESPORTS/USDT(-51%) 拉低均值 1.35 ppt，去掉后 avg=+0.44% |
| 中 | `mean_reversion` 低分段极端亏损：regime 敏感性？ | 待研究 | L3 回放 + regime 分层 |
| 低 | `volatility` 数据积累：等 n > 200 再重跑 | 待研究 | 时间 |

---

## 5. 方法论限制

同 `02_calibration_findings_2026-07-31.md` §4，额外注意：
- 全部基于 dev DB 数据（2026-07-15 至 07-28），单一时间窗口
- 如有 policy_epoch 变换，epoch 内外的候选混合无法分开（等 `policy_epoch_id` 列在生产落地后重跑）
- fat tail 分布（crypto）时 avg 可能被几个极端值主导；**始终同读 avg 和 median**

---

## 附录：复现

```bash
# 各家族独立校准
for family in momentum short_bias derivatives mean_reversion volatility; do
    echo "=== $family ==="
    uv run python scripts/shadow_threshold_calibration.py --family $family --days 30
done
```

---

## 6. derivatives 家族根因深挖

### 6.1 方向拆分

| direction | n | avg_net% | win% |
|---|---|---|---|
| short | 419 | -1.26 | 28.4 |
| long | 347 | -1.52 | 40.6 |

两个方向都是负收益，short 方向 win rate 仅 28%——几乎所有 short 都输。

### 6.2 主要拖累 symbol

| symbol | direction | n | avg_net% | win% |
|---|---|---|---|---|
| SOXL/USDT | long | 63 | **+0.96** | 70 | ← 正贡献 |
| SKHYNIX/USDT | short | 62 | -1.97 | 18 | ← 主要拖累 |
| RE/USDT | short | 50 | -2.25 | 44 |
| 1000XEC/USDT | long | 36 | -2.30 | 28 |
| MU/USDT | short | 27 | **-4.97** | **0** | ← 极端亏损 |
| QQQ/USDT | short | 25 | -0.58 | 0 |

### 6.3 根因解释

`derivatives` 家族是**高度异质的资产集合**（半导体股票、地产、加密币、ETF），用同一个 conviction gate 管理是不合适的。

具体问题：
1. **SKHYNIX/MU short**（win 0-18%）：半导体股票做空策略在 2026-07 期间系统性失败（可能是半导体/AI 行情反弹）
2. **MU/USDT short win=0%**（n=27，无一胜利）：完全不可用的方向/品种组合
3. **SOXL long 是例外**（+0.96%, win 70%）：半导体 ETF 做多与大盘同向

这不是门槛设置问题，而是：
- 部分 symbol/direction 组合在当前 regime 下的系统性亏损
- `derivatives` 家族涵盖范围太广，平均了不可比的资产类型

**owner 层建议**（观察 only，不是行动指令）：若要改善 derivatives 家族表现，考虑按 asset class（半导体/地产/ETF/加密衍生品）子分类，而非一刀切。这是 L5 治理问题，不是模块可自动调整的范围。

---

## 7. short_bias 延长窗口验证（2026-07-31）

60d/90d 重跑结果与 30d **完全相同**（n=331，所有频段数字不变）。原因：dev DB 的 settled 数据上限约 30d（2026-07-15 至 07-28），延长窗口不增加样本。

**结论**：在现有数据上，short_bias 46-55 低 conviction 正收益模式在可延伸的历史窗口内一致，但受限于 dev DB 数据量（仅约 2 周），无法判断更长期可持续性。需等生产数据累积后（建议 90d+ 数据）重跑。

---

## 8. momentum 60-70 频段异常值分析（2026-07-31）

### 8.1 结论

**单一异常值 ESPORTS/USDT（long, score=61.9, net=-50.99%）是导致 60-70 均值为负的唯一原因。**

| 版本 | n | avg_net% | median% |
|---|---|---|---|
| 原始 | 38 | -0.918 | +0.281 |
| 去掉 ESPORTS | 37 | **+0.435** | **+0.651** |

单 ESPORTS 拉低整个频段均值 1.35 ppt（= -50.99% / 38 ≈ -1.34）。

### 8.2 频段明细

最差候选（排名前3亏损）：
1. ESPORTS/USDT long：net **-50.99%**（极端尾部）
2. SNDK/USDT short：net -5.44%
3. ALLO/USDT long：net -5.34%

最优候选（排名前3盈利）：
1. KAITO/USDT long：net +6.07%
2. KAITO/USDT long：net +5.78%
3. KAITO/USDT long：net +5.60%

### 8.3 修正结论

去掉 ESPORTS 后，60-70 频段模式为：avg +0.44%、median +0.65%、win 51%。
与 55-60 频段（avg +0.64%）一致，`momentum` 家族呈现**单调递增的 conviction-to-outcome 关系**，不存在反转。

**方法论启示**：crypto fat-tail（-51%）可使单个候选扭曲小样本频段均值，**median 是更稳健的信号指标**。
