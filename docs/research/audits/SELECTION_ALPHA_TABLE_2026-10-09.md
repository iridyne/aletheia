# 选择 Alpha 表：哪些组合扣成本后赚钱（2026-10-09）

> 本文是**只读研究证据**，不是契约，也不批准任何语义改动。数据来自 Fedora `app.db`（`mode=ro` + `query_only`），
> 查询时间 2026-10-09 约 12:20Z。生成脚本：[`scripts/analysis/selection_alpha_table.py`](../../../scripts/analysis/selection_alpha_table.py)，
> 复跑命令见脚本头部。owner 决定（2026-10-09）：这份证据**跨代码版本累积**，不按部署重置或切分，
> 所以本表不按 `producer_commit`、运行会话或 policy epoch 过滤，把 116 个 `producer_commit` 的结果合在一起。

## 0. 结论先说

- **没有任何一格的 95% 置信区间下界大于 0。** 分组表和单币种表都是 “none”。这是有效结论，不是空结果：
  在当前成本模型下，到 2026-10-09 为止，系统产生的永续候选里还没有被证明扣成本后赚钱的组合。
- 4h 全分母（入选和未入选都结算）的平均净收益：未入选 −0.233%（n=51,325），入选 −0.337%（n=13,759）。
  毛方向收益约等于 0（未入选 +0.007%，入选 −0.100%），平均成本约 0.238% 一个来回（手续费 0.12% +
  点差滑点约 0.12% + 资金费）。也就是说：方向判断大致是抛硬币，成本把它压成负数；入选的反而比没入选的差。
- 点估计为正、但区间跨过 0 的格子（只能算线索，不是证据）：`reversal / short / bull / 4h` 均值 +0.434%，
  区间 [−0.188, +1.343]，n=5,958，13 天。
- 表里有约 35 格；按 95% 区间，即使全是噪声也预期有一两格碰巧为正。所以以后若出现单个“正格”，
  要看它是否在后续新数据上复现，而不是看一次。

## 1. 唯一权威账本

当前反事实结果的唯一权威是 `filter_candidate_outcomes`（下称 FCO）。依据：

- [counterfactual_contract.md](../../design/contracts/counterfactual_contract.md) §1 表 L1：“canonical
  `filter_candidate_outcomes`；历史 keyed `shadow_trades` 行只读投影，不再有结算器”；§3：`shadow_trades`
  “唯一生产写入者 `paper_trader` 已随 #2945 删除，写路径随 #3704 退役，当前没有写入者或结算器”；§6：
  “`shadow_trades` 表是等待 DROP 迁移的墓碑”。退役记录在
  [history/shadow_trades_write_path.md](../../design/history/shadow_trades_write_path.md)。
- [opportunity_outcome_contract.md](../../design/contracts/opportunity_outcome_contract.md) §2：
  “`filter_candidate_outcomes` 是唯一的固定持有期结果载体”。

Fedora 实测：`shadow_trades` 37,968 行，最后写入 2026-08-27T14:55Z；FCO 98,063 行，最后结算 2026-10-09。
`shadow_trades` 停写是已批准的退役，不应恢复。

**仍在读 `shadow_trades` 的读者（读到的只会是 2026-08-27 前的旧行，或空）**：合同 §3 把其中多数列为授权读者，
合同没有退役它们，所以本 PR 不删，只记录。`grep -rn "FROM shadow_trades\|JOIN shadow_trades\|list_deduped_shadows(\|build_calibration("`
在 `core/ extensions/ scripts/` 命中：
`extensions/counterfactual/{trades,calibration,estimators,propensity,advisories,family_scorecard}.py`、
`core/aletheia/analytics/{alpha_attribution,ranker_objective_audit,opportunity_flow,thesis_lineage}.py`、
`core/aletheia/diagnostics/probes/content.py`、`extensions/analytics/guard_impact.py`、
`scripts/analysis/shadow_threshold_calibration.py`（`shadow_propensity_report.py` 经 `propensity.py` 间接读）。
它们不会报错（表在，读法容错），只是永远停在 8 月的数据上；其中给“当前”结论的（`family_scorecard`、
`advisories`、`calibration`）实际上已经没有新证据可读。

## 2. 为什么现有的两个“按族打分”从来给不出可用结果

### 2.1 `family_scorecard`（`core/aletheia/analytics/family_scorecard.py`）

它根本不读 FCO。它有两部分：

1. `trade_count / win_rate / expectancy` 来自已平仓的真实持仓（`eligible_strategy_outcomes`），是成交后的
   PnL，不是选择 Alpha。Fedora 最后一笔平仓在 2026-09-29。
2. `shadow_count` 经 `extensions/counterfactual/family_scorecard.py::read_shadow_cohort` 读 `shadow_trades`
   最近 90 天，并且只在 90 天里只有一个 policy epoch 时才给数。Fedora 这 90 天里有 3 个 epoch
   （`226de913875e` / `95c7c02395a5` / `a0ee9b3fe1ed`），状态是 `mixed`，所以 `shadow_count` 恒为 `None`；
   2026-11-25 之后 90 天窗口里将没有任何行。

另外，FCO 的 `scanner_family` 列对 97.7% 的行都是 `candidate_selection_utility`，不是机会族。
真正的机会族（`continuation` / `reversal` / `positioning_forced_flow`）只存在于
`universe_twin_shadow_records.compact_output_json` 的 mechanism 标签里。本表的脚本按 `outcome_candidate_key`
把它连回来。

### 2.2 measured admission（`core/aletheia/universe/economic_utility.py`，本 PR 不改，归 #3483/#3486）

最新一批（2026-10-09T12:07Z，scanner_run `f796496b7ebe`）的 66 个估计全部 `unavailable`，原因全是
`parent_sample_insufficient`，`parent_independent_n` 最大 25。门槛是每个 (mechanism, direction, venue,
market_type) 至少 50 个互不重叠的 4h 独立单位、10 个日期、5 个币种、覆盖率 ≥ 80%
（[candidate_selection_utility_contract.md](../../design/contracts/candidate_selection_utility_contract.md) 第 76 行附近）。

是否按版本切分了历史：

- **没有**按 `producer_commit`、运行会话或 policy epoch 过滤 FCO。合同明文允许：“Matching acquisition
  identities may pool across producer commits/sessions”。
- **有**按语义身份切分，且是合同要求的：`load_candidate_selection_outcomes` 只要
  `feature_acquisition_version` 等于当前值的记录（合同 “Acquisition and publication amendment — 2026-09-05”
  与 “Amendment — 2026-10-08”：“Training still requires equality”），永续还要求 `funding_evidence_json`
  版本等于当前 `perp_funding`（#3086 段落）；`build_economic_utility_cohort_index` 还要求
  `mechanism_behavior_version`、`outcome_contract_version`、`cost_assumptions`、`cost_model_version`
  等于当前值。
- 当前 acquisition 身份 `5c069275…` 从 2026-09-27 起生效（之前 09-26 一次、更早又一次）。在它之下，
  每个永续 cohort 最多 25 个独立单位、6 个日期（09-27、09-28、09-29、09-30、10-08、10-09）；
  2026-10-01 到 10-07 Fedora 没有任何扫描批次。**结构上不可能达到 10 个日期**，至少要再积累 4 个有数据的日子。
- 对照：如果把所有 acquisition 身份合在一起，每个永续 cohort 已有 64–81 个独立单位、18–20 个日期，
  全部过门槛。但本表显示合在一起也没有正格，所以即使合并，measured admission 大概率给出负 Utility，
  不会因此多放行。

**System Operator Alpha 按 commit 切分**：`core/aletheia/analytics/alpha_attribution.py::_system_operator_alpha`
只取最新一个 `producer_commit` 的 cohort（`WHERE fd.producer_commit=? AND fd.behavior_fingerprint=?`）。
合同 §3 只要求“同一行为指纹”，没有要求同一 commit；按 owner 决定，commit 切分是错的。
但它的输入 `pi_decisions`（`market_observer`）最后一行是 2026-09-29，这条路本身已不产出，本 PR 不改。

## 3. 结算有没有让本表失真

逐项在 Fedora 全表验证（只读），**没有发现结算缺陷**：

| 检查 | 结果 |
|---|---|
| 方向符号：`directional = gross`（long）/ `−gross`（short） | 93,817 行，0 不符 |
| 成本扣减：`net = directional − fee − spread/slippage − funding` | 87,913 行，0 不符 |
| 毛收益：`gross = (exit − entry) / entry × 100` | 87,913 行，0 不符 |
| 手续费恒为 `2 × 0.06 = 0.12` | 88,078 行全部 0.12 |
| 点差滑点 = `entry_spread_bps/100 + 0.10` | 88,021 行，0 偏离 |
| 永续资金费（#3086）：`rate × 100 × 持有秒 / 结算间隔秒 × 方向` | 65,634 行，0 不符 |
| MFE ≥ 0、MAE ≤ 0 | 0 违反 |
| `outcome_candidate_key + horizon` 唯一 | 98,063 行，98,063 个唯一键 |
| 只结算入选候选（幸存者偏差） | 4h 不是：未入选 51,419 行、入选 13,759 行都有净值 |

需要读表的人知道的两个口径限制（都是合同规定，不是缺陷）：

- **15m / 1h / 24h 只为入选的主候选写**（`candidate_selection_shadow.py` 注释 “Selected-primary
  15m/1h/24h extras”，#3701 Batch A），所以这几档 `sel%` = 100，是入选样本，不是全分母，不能拿来和 4h 比较。
- 4h 已结算但净值为空：未入选 5,452 行（9.6%），入选 272 行（1.9%）。几乎全部来自 2026-09-08 至 09-11、
  `funding_evidence_json` 为空的行；合同 §4 要求“永续缺少入场资金费率……`net_return_pct` 必须为 NULL”。
  2026-10-08 以后没有新增。这让早期未入选样本少了一块，不影响“没有正格”的结论。

## 4. 表（Fedora，2026-10-09）

口径：`market_type IN ('perp','futures')`，`status='settled'`，`net_return_pct IS NOT NULL`。单位都是百分比。
`CI95` 是按 UTC 入场日整天重抽样的 bootstrap（2000 次，种子 20261009）：同一天的行共享行情，逐行重抽会把区间
算得虚窄；少于 5 天的格子不给区间（n/a）。`win%` = `net_return_pct > min_tradable_edge_pct`（恒为 0.05）的比例。
`sel%` = 入选比例。一个候选在同一方向上有多个适用 mechanism 时，在每个 mechanism 格里各算一次，所以分组表各格
加起来（94,104）大于行数（74,342）；单币种表按结果去重。`regime=null` 是没有记录 regime 的行。

futures rows with net: 74342; producer_commits pooled: 116; entry 2026-09-03T02:39:09.962174+00:00 .. 2026-10-09T11:57:10.380346+00:00; latest settled 2026-10-09T12:17:18.137936+00:00; cost_model_version v1

Coverage (futures, all statuses):

| horizon | status | has_net | selected | n |
|---|---|---|---|---|
| 15m | failed | 0 | 1 | 8 |
| 15m | pending | 0 | 1 | 32 |
| 15m | settled | 0 | 0 | 27 |
| 15m | settled | 0 | 1 | 33 |
| 15m | settled | 1 | 0 | 94 |
| 15m | settled | 1 | 1 | 3366 |
| 1h | failed | 0 | 1 | 16 |
| 1h | pending | 0 | 1 | 88 |
| 1h | settled | 0 | 0 | 27 |
| 1h | settled | 0 | 1 | 33 |
| 1h | settled | 1 | 0 | 94 |
| 1h | settled | 1 | 1 | 3302 |
| 24h | pending | 0 | 1 | 1192 |
| 24h | settled | 0 | 0 | 27 |
| 24h | settled | 0 | 1 | 33 |
| 24h | settled | 1 | 0 | 94 |
| 24h | settled | 1 | 1 | 2214 |
| 4h | pending | 0 | 0 | 734 |
| 4h | pending | 0 | 1 | 298 |
| 4h | settled | 0 | 0 | 5452 |
| 4h | settled | 0 | 1 | 272 |
| 4h | settled | 1 | 0 | 51419 |
| 4h | settled | 1 | 1 | 13759 |

Cells: family x direction x regime x horizon (n >= 30; 94104 labelled rows)

| family | direction | regime | horizon | n | days | mean% | median% | CI95 low | CI95 high | win% | MFE% | MAE% | sel% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| positioning_forced_flow | short | bull | 4h | 29715 | 13 | -0.152 | -0.164 | -0.555 | +0.383 | 42.2 | +1.72 | -1.62 | 13 |
| continuation | short | bull | 4h | 13129 | 13 | -0.461 | -0.339 | -0.776 | -0.034 | 37.6 | +1.47 | -1.69 | 40 |
| continuation | long | bull | 4h | 11838 | 13 | -0.580 | -0.433 | -1.444 | -0.057 | 34.7 | +1.84 | -2.16 | 48 |
| reversal | long | bull | 4h | 8203 | 13 | -0.193 | -0.184 | -0.595 | +0.253 | 41.3 | +1.59 | -1.56 | 21 |
| reversal | short | bull | 4h | 5958 | 13 | +0.434 | +0.178 | -0.188 | +1.343 | 53.4 | +2.64 | -1.96 | 13 |
| positioning_forced_flow | long | bull | 4h | 4764 | 13 | -0.381 | -0.409 | -0.817 | +0.121 | 35.7 | +1.57 | -1.79 | 17 |
| positioning_forced_flow | short | null | 4h | 3198 | 8 | +0.140 | +0.203 | -0.876 | +0.451 | 55.2 | +2.13 | -1.50 | 5 |
| continuation | short | null | 4h | 2191 | 7 | +0.167 | +0.334 | -1.434 | +0.411 | 57.9 | +2.20 | -1.50 | 6 |
| continuation | long | bull | 15m | 1328 | 4 | -0.255 | -0.226 | n/a | n/a | 16.3 | +0.27 | -0.30 | 100 |
| continuation | long | bull | 1h | 1304 | 4 | -0.298 | -0.236 | n/a | n/a | 25.7 | +0.55 | -0.64 | 100 |
| continuation | long | null | 4h | 1276 | 7 | -0.368 | -0.384 | -1.005 | +0.400 | 39.4 | +2.24 | -2.41 | 18 |
| reversal | long | null | 4h | 1021 | 6 | -0.809 | -0.779 | -1.051 | +1.005 | 21.4 | +1.62 | -2.52 | 3 |
| continuation | long | bull | 24h | 1006 | 3 | -0.769 | -0.745 | n/a | n/a | 39.8 | +2.96 | -3.68 | 100 |
| reversal | long | bull | 15m | 995 | 4 | -0.251 | -0.237 | n/a | n/a | 13.0 | +0.21 | -0.25 | 100 |
| reversal | long | bull | 1h | 988 | 4 | -0.386 | -0.243 | n/a | n/a | 25.9 | +0.42 | -0.66 | 100 |
| continuation | short | bull | 15m | 926 | 4 | -0.217 | -0.224 | n/a | n/a | 14.9 | +0.24 | -0.22 | 100 |
| continuation | short | bull | 1h | 896 | 4 | -0.196 | -0.223 | n/a | n/a | 26.9 | +0.52 | -0.45 | 100 |
| reversal | short | null | 4h | 739 | 6 | -0.395 | +0.105 | -1.465 | +0.405 | 51.0 | +2.23 | -2.65 | 6 |
| positioning_forced_flow | long | null | 4h | 682 | 7 | +0.193 | -0.043 | -0.180 | +1.728 | 47.4 | +2.67 | -2.19 | 7 |
| continuation | short | bull | 24h | 603 | 3 | -0.355 | -0.105 | n/a | n/a | 47.1 | +2.50 | -2.96 | 100 |
| positioning_forced_flow | short | bull | 15m | 564 | 4 | -0.210 | -0.220 | n/a | n/a | 17.9 | +0.29 | -0.25 | 100 |
| positioning_forced_flow | short | bull | 1h | 545 | 4 | -0.121 | -0.184 | n/a | n/a | 31.2 | +0.62 | -0.47 | 100 |
| reversal | long | bull | 24h | 506 | 3 | -1.565 | -1.323 | n/a | n/a | 21.5 | +1.18 | -4.95 | 100 |
| positioning_forced_flow | short | bull | 24h | 390 | 3 | +0.041 | +0.279 | n/a | n/a | 53.8 | +2.74 | -2.61 | 100 |
| positioning_forced_flow | long | bull | 15m | 186 | 4 | -0.230 | -0.205 | n/a | n/a | 17.2 | +0.24 | -0.27 | 100 |
| positioning_forced_flow | long | bull | 1h | 186 | 4 | -0.254 | -0.286 | n/a | n/a | 30.6 | +0.50 | -0.57 | 100 |
| reversal | short | bull | 15m | 163 | 3 | -0.207 | -0.224 | n/a | n/a | 5.5 | +0.11 | -0.08 | 100 |
| reversal | short | bull | 1h | 160 | 3 | -0.130 | -0.192 | n/a | n/a | 14.4 | +0.26 | -0.16 | 100 |
| reversal | short | bull | 24h | 111 | 2 | +0.434 | +0.610 | n/a | n/a | 63.1 | +1.66 | -0.91 | 100 |
| positioning_forced_flow | long | bull | 24h | 106 | 3 | -2.080 | -2.029 | n/a | n/a | 12.3 | +1.21 | -5.26 | 100 |
| momentum | short | null | 4h | 44 | 7 | +0.117 | -0.007 | -0.611 | +0.582 | 50.0 | +1.56 | -1.28 | 57 |
| derivatives | short | null | 15m | 32 | 2 | -0.046 | -0.124 | n/a | n/a | 28.1 | +0.35 | -0.23 | 0 |
| derivatives | short | null | 1h | 32 | 2 | +0.141 | -0.021 | n/a | n/a | 43.8 | +0.73 | -0.40 | 0 |
| derivatives | short | null | 4h | 32 | 2 | +0.648 | +0.498 | n/a | n/a | 68.8 | +1.88 | -0.69 | 0 |
| derivatives | short | null | 24h | 32 | 2 | +3.199 | +1.554 | n/a | n/a | 93.8 | +4.83 | -0.93 | 0 |

Cells with CI95 lower bound > 0: none

Per symbol: top 15 symbols by 4h count, symbol x direction, 4h, all families

| symbol | direction | n | days | mean% | median% | CI95 low | CI95 high | win% | MFE% | MAE% | sel% |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ETH/USDT | short | 1355 | 20 | -0.170 | -0.183 | -0.362 | +0.039 | 34.4 | +0.82 | -0.75 | 0 |
| BTC/USDT | short | 1350 | 20 | -0.138 | -0.198 | -0.254 | -0.015 | 32.1 | +0.63 | -0.50 | 0 |
| DOGE/USDT | short | 1283 | 16 | -0.032 | -0.060 | -0.388 | +0.405 | 45.6 | +1.48 | -1.09 | 12 |
| LINK/USDT | short | 1275 | 16 | -0.351 | -0.182 | -0.853 | +0.210 | 44.2 | +1.51 | -1.62 | 23 |
| DOT/USDT | short | 1244 | 15 | -0.275 | -0.216 | -0.845 | +0.394 | 42.8 | +1.76 | -1.79 | 11 |
| LTC/USDT | short | 1241 | 17 | -0.310 | -0.030 | -0.920 | +0.134 | 46.8 | +1.43 | -1.41 | 26 |
| NEAR/USDT | short | 1238 | 17 | -0.254 | -0.082 | -1.056 | +0.606 | 48.1 | +2.85 | -2.90 | 13 |
| ADA/USDT | short | 1204 | 16 | -0.090 | -0.171 | -0.572 | +0.438 | 43.5 | +1.77 | -1.44 | 17 |
| BCH/USDT | short | 1193 | 14 | +0.185 | +0.011 | -0.135 | +0.604 | 48.3 | +1.78 | -1.19 | 37 |
| AVAX/USDT | short | 1165 | 15 | -0.195 | -0.133 | -0.658 | +0.337 | 46.3 | +1.70 | -1.67 | 14 |
| PEPE/USDT | short | 1156 | 14 | +0.095 | -0.012 | -0.346 | +0.682 | 48.3 | +1.88 | -1.43 | 19 |
| BNB/USDT | short | 1080 | 17 | -0.107 | -0.140 | -0.321 | +0.145 | 40.3 | +0.77 | -0.61 | 0 |
| CL/USDT | long | 1021 | 15 | -0.132 | -0.152 | -0.431 | +0.232 | 40.1 | +0.92 | -0.88 | 30 |
| NEAR/USDT | long | 917 | 16 | -0.375 | -0.739 | -1.485 | +0.591 | 39.4 | +2.91 | -3.04 | 38 |
| HYPE/USDT | short | 887 | 16 | +0.005 | -0.030 | -0.353 | +0.358 | 48.6 | +1.41 | -1.03 | 28 |
| CRCL/USDT | short | 870 | 13 | +0.007 | -0.181 | -0.314 | +0.359 | 41.1 | +1.16 | -1.10 | 21 |
| DOT/USDT | long | 769 | 15 | -0.364 | -0.225 | -1.192 | +0.196 | 43.3 | +1.84 | -1.81 | 76 |
| HYPE/USDT | long | 734 | 15 | -0.391 | -0.267 | -0.740 | -0.045 | 38.7 | +0.89 | -1.16 | 7 |
| LINK/USDT | long | 652 | 12 | +0.140 | +0.118 | -0.429 | +0.688 | 51.5 | +1.96 | -1.50 | 27 |
| PEPE/USDT | long | 612 | 12 | -0.758 | -0.656 | -1.345 | -0.117 | 34.0 | +1.33 | -1.91 | 41 |
| LTC/USDT | long | 609 | 11 | -0.287 | -0.581 | -0.997 | +0.538 | 31.7 | +1.64 | -1.77 | 26 |
| AVAX/USDT | long | 587 | 14 | -0.726 | -0.502 | -1.626 | -0.236 | 39.2 | +1.42 | -2.07 | 47 |
| ADA/USDT | long | 541 | 15 | -0.179 | -0.136 | -0.940 | +0.390 | 45.5 | +1.47 | -1.41 | 48 |
| DOGE/USDT | long | 484 | 13 | -0.718 | -0.449 | -1.397 | -0.252 | 34.7 | +0.91 | -1.71 | 55 |
| CRCL/USDT | long | 482 | 12 | -0.424 | -0.308 | -0.748 | -0.071 | 36.3 | +0.83 | -1.06 | 35 |
| CL/USDT | short | 447 | 11 | -0.140 | -0.177 | -0.634 | +0.283 | 39.6 | +1.01 | -0.83 | 38 |
| BCH/USDT | long | 409 | 12 | -1.116 | -0.790 | -2.197 | -0.485 | 29.3 | +1.17 | -2.62 | 14 |
| BNB/USDT | long | 343 | 13 | -0.469 | -0.235 | -0.771 | -0.128 | 33.2 | +0.53 | -0.98 | 33 |
| ETH/USDT | long | 227 | 11 | -0.737 | -0.502 | -1.137 | +0.028 | 33.9 | +0.73 | -1.33 | 32 |
| BTC/USDT | long | 169 | 8 | -0.420 | -0.277 | -0.695 | +0.193 | 39.6 | +0.54 | -0.82 | 45 |

Symbol cells with CI95 lower bound > 0: none

## 5. 复跑

```bash
ssh u7p 'cd ~/aletheia && uv run --no-sync python - --db /home/admin/.aletheia/app.db' \
    < scripts/analysis/selection_alpha_table.py
```

脚本只读：`mode=ro` 打开、`PRAGMA query_only=ON`、只有 `SELECT`。
