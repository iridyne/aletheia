# 13 · 补记录 → 测试 → 结论：regime 门禁其实已经存在，但从未生效

> 日期：2026-09-28
> 数据：Fedora 只读直读 `~/.aletheia/app.db`（`mode=ro`），96,992 行「账本 JOIN 决策时点 regime」
> 代码基线：`origin/master` `0d41760f5`
> **性质：分析与汇报。第 1 节是对上一轮计划的重大修正 —— 不需要补记录。**

---

## 1. 重大修正：不需要补记录，数据早就有了

上一轮我说「前瞻账本没有 regime 字段，所以先补记录（#3656）」。**查完发现这个判断是错的。**

`universe_forward_outcomes` 本身确实没有 regime 列，**但它能 JOIN 到已经记了 regime 的表**：

```
universe_members  28,419 行，有 regime_tag 列
market_regimes     5,013 行，有 state / confidence / long_bias / short_bias
opportunities     13,499 行，有 regime_tag / direction_bias

JOIN 实测：账本 99,676 行成本完整样本中，96,992 行（97.3%）能取到 member 的 regime_tag
```

**而且 `regime_tag` 是决策时写入的**（`universe/service.py:974` `global_regime_tag = current_regime.state`，在候选入库的同一事务里），**不是结算时回填 —— 无 look-ahead 风险。**

**所以 #3656 的前置工作不需要做。** 可以直接进入测试阶段。

---

## 2. 更重要的发现：R20 那道门我们早就有，只是没接线

我们**已经有一个完整的 regime detector**：`extensions/regime/detector.py`

```python
REGIME_BULL = "bull"          # :28
REGIME_BEAR = "bear"          # :29
REGIME_SIDEWAYS = "sideways"  # :30
REGIME_VOLATILE = "volatile"  # :31

_BIAS_TABLE: dict[str, tuple[float, float]] = {   # :36-41
    REGIME_BULL:     (0.75, 0.25),   # (long_bias, short_bias)
    REGIME_BEAR:     (0.25, 0.75),
    REGIME_SIDEWAYS: (0.50, 0.50),
    REGIME_VOLATILE: (0.35, 0.35),
}
```

**这就是 R20 `01_macro_trend_filter` 的等价物**——甚至更细（它只有多/空两态，我们有四态且带 confidence）。

### 但 `long_bias` / `short_bias` 从未被任何门消费

全仓 grep `long_bias|short_bias`（排除同名的 `_score_short_bias` 打分族）：

| 位置 | 用途 |
|---|---|
`storage/models.py:779-780` | 模型字段定义 |
`storage/migrations.py:2673-2674` | 表列定义 |
`storage/repositories.py:6516` | 读写 |
`cli/inspect_cmd.py:1322,1331` | **CLI 展示** |
`repositories_market_data.py:1212` | 读取 |

**没有任何一处把它用于准入、方向否决或仓位调整。** 它被计算、被持久化、被显示——**然后被丢弃。**

`regime` 在 scanner 里唯一的耦合点（`universe/scanners.py:654-658`）：

```python
# short_bias is explicitly coupled to the global crypto regime. The
# stock-linked profile keeps the same instrument-local scanners and simply
# marks that one family inapplicable.
if (feat.get("analysis_context") or {}).get("kind") == "stock_linked_perp":
    results["short_bias"] = (0.0, [], None)
```

**这只是对"股票挂钩永续"禁用 short_bias 族，与 bull/bear 方向无关。** 即：**我们有 detector、有 bias 表、有持久化，但没有那道门。**

---

## 3. 致命问题：detector 从未输出过 bear

这是本域最关键的发现。`market_regimes` 表 5,013 次检测的全部输出：

| state | n | confidence | long_bias | short_bias | 时间跨度 |
|---|---:|---:|---:|---:|---|
| **bull** | 3,249 | 0.809 | 0.750 | 0.250 | 2026-08-19 → 2026-09-28 |
| **sideways** | 1,764 | 0.540 | 0.500 | 0.500 | 2026-08-07 → 2026-08-20 |

**`bear` 出现 0 次，`volatile` 出现 0 次。** 而且 `sideways` 在 2026-08-20 之后就再没出现过——**detector 自 8 月 20 日起连续 39 天恒定输出 `bull`。**

对应到候选侧：`universe_members.regime_tag` 分布 = `bull` 27,421 / `None` 998，**同样零 bear、零 sideways、零 volatile**。

### 这意味着什么

1. **即使把门接上，当前也不会拦住任何东西** —— 门的判据恒为 bull，而 bull 下唯一会被拦的是 short。
2. **detector 本身可能有缺陷**：39 天恒定单态、且从未产出 bear/volatile，这在一个有真实回撤的市场里不太可能。**需要独立排查 detector 的判据与阈值。**
3. 我上一轮用「前一日全候选 gross 均值」做代理，反而**比真实 detector 更有区分度**（它至少会随日变化）。这解释了为什么代理测出的效果比真实 regime 更强。

---

## 4. 测试结果：在 detector 说 bull 时禁止做空

用真实的决策时点 `regime_tag='bull'`（96,992 行），套用 R20 的规则「bull 禁止开空」：

| horizon | 场景 | n | dir% | 聚类 t | net% |
|---|---|---:|---:|---:|---:|
| 15m | 全部候选 | 27,119 | −0.1121 | **−2.61** | −0.3132 |
| 15m | bull 禁做空（仅 long） | 15,317 | −0.1239 | −1.49 | −0.2989 |
| 1h | 全部候选 | 26,838 | −0.1432 | **−2.02** | −0.3054 |
| 1h | bull 禁做空 | 15,136 | −0.1562 | −1.27 | −0.2576 |
| 4h | 全部候选 | 25,310 | −0.3198 | −1.82 | −0.3985 |
| 4h | bull 禁做空 | 14,366 | −0.1433 | −0.48 | **−0.1089** |
| **24h** | 全部候选 | 17,725 | −0.1009 | −0.19 | −0.6898 |
| **24h** | **bull 禁做空** | 10,470 | **+0.9912** | +1.05 | **+0.4534** |

### 判读（严格）

**有效果，但达不到显著。**

- **24h 是唯一 net% 转正的**：−0.6898% → **+0.4534%**，改善 1.14 个百分点。dir% 从 −0.10 到 +0.99。
- **4h 的 net% 从 −0.3985 改善到 −0.1089**（改善 0.29pp），但仍为负。
- **15m / 1h 几乎无改善**，dir% 甚至略微变差（−0.1121 → −0.1239）。
- **所有场景的聚类 t 值都不显著**（|t| < 2）。24h 的 t=+1.05 远达不到。
- 反向观察：**全部候选在 15m/1h 上 t=−2.61/−2.02，显著为负** —— 即"候选池整体质量差"这一点是显著的，"禁做空能修好它"这一点不显著。

**结论：方向正确（长 horizon 上禁逆势做空确实改善），但幅度不足以在当前样本上证明。且它只对 4h/24h 有效，对 15m/1h 无效。**

---

## 5. 完整结论

### 我们与 R20 的真实差距（修订版）

**不是"我们缺那道门"，而是"我们有门但三重失效"：**

| 层 | R20 | 我们 | 状态 |
|---|---|---|---|
| regime 检测 | 4H 趋势通道判定 | `extensions/regime/detector.py` 四态 + confidence + bias 表 | **我们更完整** |
| regime 持久化 | — | `market_regimes` / `universe_members.regime_tag` / `opportunities` | **我们有，它可能没有** |
| **regime → 方向否决** | `01_macro_trend_filter` fail-closed 到 WAIT | **无任何消费者** | ❌ **缺失** |
| **detector 输出多样性** | 随行情变化 | **39 天恒定 bull，从未 bear/volatile** | ❌ **疑似缺陷** |

### 三条按依赖排序的工作

| 序 | 工作 | 为什么在这个位置 | 成本 |
|---|---|---|---|
| **1** | **排查 detector 为何 39 天恒定 bull、从未输出 bear/volatile** | **这是前置**。门接上去也没用，因为判据恒定。且这本身可能是真缺陷 | 读 `extensions/regime/detector.py` 的判据与阈值 |
| **2** | 若 detector 修好，**重跑本域测试** | 有了真实变化的 regime，才能判断门的价值 | 复用本文的 SQL |
| **3** | 只有 2 证明有效，**才接门** | 当前测试 t=+1.05 不显著，不足以支撑改动 | 一处准入判据 |

### 明确不要做的

- **不要现在就接 regime 门**。测试 t=+1.05 不显著，且判据恒为 bull —— 接上等于只砍掉所有 short，那是一个未经验证的大改动。
- **不要做 #3656 的补记录工作**。数据已经有了，97.3% 可 JOIN。
- 不要因为 24h net% 转正就下结论。单一 horizon、t 不显著、且 15m/1h 无效甚至略差。

---

## 6. 本域未验证

- **detector 恒定 bull 的成因**（第 5 节工作 1）。只确认了输出分布，未读判据逻辑。
- `sideways` 为何在 2026-08-20 之后消失 —— 是否与某次配置/代码改动相关，未做 git 二分。
- `opportunities.direction_bias` 列（13,499 行）的实际取值与是否被消费，未查。
- **R20 的 `01_macro_trend_filter` 实际拦截率**。仍然只读了代码，无其运行日志 —— 如果它实际很少触发，那它对 R20 结果的贡献也有限。
- 本测试只用了 `regime_tag='bull'` 的子集（96,992/99,676）。剩余 2,684 行（member 无 regime_tag）未纳入。
- 24h 的 net% 转正是否稳健 —— 未做时间分段或 bootstrap。
