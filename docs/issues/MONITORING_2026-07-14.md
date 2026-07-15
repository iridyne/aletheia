# 系统监控观测报告 — 2026-07-14

> 数据窗口：过去 48 小时（2026-07-12 ～ 2026-07-14）
> 数据源：`data/app.db` 直查，无 Dashboard
> 触发：第 1 次（~08:00Z）+ 第 2、3、4、5 次（手动触发）
>
> **图例**：🔴 阻断/严重异常 ｜ 🟠 数据质量降低 ｜ 🟡 效率/覆盖率问题
>
> ---
> ⚠️ **系统于 2026-07-13T23:03Z 停止（已多次修正，最终确认），
> 今日（07-14）零产出，HYPE/USDT + LIT/USDT 两笔 short 无监控。
> MON-007 signal_pending 归零 ✅ | MON-024 NEW: market_data_stale + Degraded + 双心跳异常**

---

## §1 Opportunity 产出数量

| 窗口 | 数量 |
|---|---|
| 最近 24h | 1,949 |
| 最近 48h | 2,510 |
| 全量累计 | 26,155 |

**Scanner 分布（48h）：**

| scanner_family | count | 占比 |
|---|---|---|
| momentum | 1,434 | 57.1% |
| mean_reversion | 483 | 19.2% |
| short_bias | 312 | 12.4% |
| volatility | 247 | 9.8% |
| NULL | 20 | 0.8% |
| event | 11 | 0.4% |
| breakout | 3 | 0.1% |

**观察：** 产出量稳定（24h=1,949）。NULL scanner_family 20 条，MON-016 问题仍在。
但注意：最后一笔 opportunity 在 2026-07-13T16:00:51Z——系统停止后无新机会产出，
24h 数字全部来自 07-13T～16:00Z 之前，**今日（07-14）实际产出为 0**。

---

## §2 confidence/ranker/filter 各层通过率（48h）

| 阶段 | passed | rejected | 通过率 |
|---|---|---|---|
| observer_pre_filter | 2,343 | 78 | 96.8% |
| universe_allocation | 3,147 | 2,550 | 55.2% |
| paper_ranker | 10,274 | 2,234 | 82.1% |
| paper_trader_selection | 373 | 1,147 | **24.5%** |
| proposal_submission | 105 | 5 | 95.2% |
| system_operator | 120 | — | — |
| system_operator_debate | 56 | — | — |
| risk | 19 | 36 | **34.5%** |

**观察：** 各层通过率与 07-13 基本持平：
- `paper_trader_selection` 24.5% 持续低位（与 MON-016 equity 泄漏无直接关系，更可能是策略筛选逻辑）
- `risk` 34.5% 通过率：下节详解拒绝原因

---

## §3 System Operator trade/skip 分布（48h，不含 signal_pending）

| action | count |
|---|---|
| skip | 538 |
| watch | 98 |
| trade | 44 |
| scale_in | 16 |
| close | 1 |

**每小时交易率（最近活跃时段）：**

| 小时 | trades | skips |
|---|---|---|
| 07-13T06:00Z | 8 | 8 |
| 07-13T14:00Z | 5 | 9 |
| 07-13T15:00Z | 1 | 19 |

**🔴 最后一条 pi_decision：2026-07-13T16:01:27Z**
今日（07-14）零 trade/skip/watch 决策。Operator 自 ~16:01Z 停止运行。

---

## §4 Proposal 和 Risk approve/reject（48h）

| 类型 | 数量 |
|---|---|
| trade_proposals approved | 19 |
| trade_proposals rejected | 36 |
| risk_decisions approved | 19 |
| risk_decisions rejected | 36 |

**48h 拒绝原因 Top 7：**

| 原因 | 次数 | 备注 |
|---|---|---|
| `sector_concentration:momentum:100%>60%` | 20 | ⚠️ 全部在 07-12T03:03Z 之前（fix 前） |
| `account_loss_unavailable:legacy_unverified_close` | 9 | 全部在 07-13T06:58Z 之前（fix 前） |
| `symbol_already_open:BTC/USDT` | 8 | 历史孤儿订单（MON-009） |
| `symbol_already_open:HYPE/USDT` | 3 | 正常行为（scale_in 被正确拦截） |
| `incomplete_entry_plan:stop_loss+take_profit` | 2 | MON-017，LIT/USDT |
| `exceeds_demo_position_notional` | 1 | 单笔提案超 size 限制 |
| `account_leverage_ratio:4.08x>3.0x` | 1 | 杠杆超限 |

**重要确认：** `correlation_guard_enabled=false` 和 `account_loss` 修复均已持续生效：
- 07-13T12:04Z 后零 `sector_concentration` violation ✅
- 07-13T12:04Z 后零 `account_loss_unavailable` violation ✅

**今日（07-14）：零提案，零 risk decision。**

---

## §5 Order / Fill / Position 完整性

**订单状态（全量）：**

| status | execution_mode | count | 最旧 | 最新 |
|---|---|---|---|---|
| filled | paper_internal | 25 | 2026-06-09 | 2026-07-13T14:26Z |
| filled | okx_demo | 462 | 2026-06-22 | 2026-07-06 |
| filled | bitget_demo | 117 | 2026-06-10 | 2026-06-20 |
| error | okx_demo | 228 | 2026-06-24 | 2026-07-02 |
| error | bitget_demo | 28 | 2026-06-19 | 2026-06-20 |
| cancelled | okx_demo | 34 | 2026-06-20 | 2026-06-21 |
| cancelled | bitget_demo | 48 | 2026-06-16 | 2026-06-19 |
| **pending** | bitget_demo | **1** | — | 2026-06-21 |
| **pending** | okx_demo | **2** | — | 2026-07-01 |
| **submitted** | okx_demo | **6** | — | 2026-07-06 |

**孤儿订单（>1 天，未变化）：9 笔**

| id | symbol | status | mode | 悬挂时长 |
|---|---|---|---|---|
| 4502b2e31a38 | BTC/USDT | pending | bitget_demo | 23 天 |
| c6df5df8fc56 | ETH/USDT | pending | okx_demo | 16 天 |
| 320d83b19803 | NEAR/USDT | pending | okx_demo | 13 天 |
| a0ccd0a8274a | XLM/USDT | submitted | okx_demo | 10 天 |
| 6463a60b4ba9 | MON/USDT | submitted | okx_demo | 8 天 |
| 4b24249ed3a0 | MON/USDT | submitted | okx_demo | 8 天 |
| b9ce59bb7631 | MON/USDT | submitted | okx_demo | 8 天 |
| 1e3439796175 | MON/USDT | submitted | okx_demo | 8 天 |
| 0dd0abb6c025 | MON/USDT | submitted | okx_demo | 8 天 |

无新孤儿产生（系统已停止）。

**Fills：** 480 条，最新 2026-07-13T14:26:21Z（HYPE/USDT 入场 fill，与订单 1:1 对应）✅

**Open positions：**

| id | symbol | direction | qty | entry_price | mode | opened_at |
|---|---|---|---|---|---|---|
| d13627b0fee7 | HYPE/USDT | short | 1.174321 | 64.0050 | paper_internal | 2026-07-13T14:26Z |

**🟠 唯一 open position 已持仓超 16 小时，处于无监控状态（系统已停）。**

---

## §6 Position 关闭质量与 genuine outcome

**全量 close_category / outcome_eligible：**

| close_category | outcome_eligible | count |
|---|---|---|
| administrative | 0 | 125 |
| unknown | 0 | 116 |
| maintenance | 0 | 6 |
| strategy | 1 | **5** |

**close_reason 分布（全量）：**

| close_reason | count |
|---|---|
| reconciled_gone | 125 |
| take_profit | 62 |
| stop_loss | 56 |
| units_bug_cleanup | 3 |
| max_hold | 3 |
| retry_after_fix | 2 |
| demo_validation_cleanup | 1 |

**outcome_eligible=0 细分：**

| outcome_exclusion_reason | close_category | count |
|---|---|---|
| administrative_close | administrative | 125 |
| unclassified | unknown | 116 |
| maintenance_close | maintenance | 6 |

**近 48h 关闭详情：**

| id | symbol | close_category | close_reason | eligible | exit_fill_verified | exit_price_source | closed_at |
|---|---|---|---|---|---|---|---|
| 54fa48db24b2 | BTC/USDT | strategy | stop_loss | ✅ 1 | ✅ 1 | local_fill | 07-13T03:56Z |
| 61ee496c4857 | UNI/USDT | strategy | stop_loss | ✅ 1 | ✅ 1 | local_fill | 07-11T16:40Z |
| 29cb2c76d09e | UNI/USDT | strategy | take_profit | ✅ 1 | ✅ 1 | local_fill | 07-11T15:46Z |
| 40335fb3f27c | BTC/USDT | administrative | reconciled_gone | 0 | 0 | unavailable | 07-11T04:38Z |
| … | … | administrative | reconciled_gone | 0 | 0 | unavailable | 07-11T03-04Z |
| ff7bcb0b4b8c | HYPE/USDT | strategy | stop_loss | ✅ 1 | ✅ 1 | local_fill | 07-11T02:56Z |

**观察：**
- `strategy` + `outcome_eligible=1` 的 5 笔均为 07-13 SQL 修复的历史数据，或 pnl.py 代码修复前的手动补丁。
- `unknown` 116 笔（close_reason 为 take_profit/stop_loss/max_hold）：这是 pnl.py 修复前积累的遗留数据，策略关闭却无 outcome 字段。
- 首笔由新代码关闭的 position（HYPE/USDT d13627b0fee7）**尚未关闭**，仍在 open 状态。

---

## §7 每笔成本后 PnL 与 scanner attribution

**全量（所有已关闭）：**

| scanner_family | 笔数 | total_pnl | avg_pnl | wins | losses |
|---|---|---|---|---|---|
| NULL/no_opp | 206 | -50.1999 | -0.2437 | 83 | 123 |
| mean_reversion | 26 | -2.6340 | -0.1013 | **0** | **26** |
| momentum | 19 | -2.5139 | -0.1323 | 5 | 14 |
| short_bias | 1 | -0.0131 | -0.0131 | 0 | 1 |

**全量总计：** 252 笔已关闭，**全部 scanner 均为负 PnL，mean_reversion 0% 胜率**

**近 48h（仅 scanner 有 opp 的笔）：**

| scanner_family | 笔数 | total_pnl | avg_pnl |
|---|---|---|---|
| NULL/no_opp | 2 | +1.7109 | +0.8555 |
| mean_reversion | 9 | -1.3326 | -0.1481 |
| momentum | 7 | -5.9516 | **-0.8502** |

**🔴 异常：mean_reversion 全量 0% 胜率（26 笔，全败）。**
**🔴 异常：momentum 近 48h avg -0.85/笔，明显恶化（历史均值 -0.13）。**

**NULL/no_opp 说明：** 206 笔中，125 笔为 `reconciled_gone`（admin close，realized_pnl 多为小数），
59+52 笔为 take_profit/stop_loss 但 `opportunity_id=NULL`（策略关闭无 opp 归因，MON-006 相关）。

---

## 根因分析

### RC-018 ｜ MON-018 P0：系统进程停止，今日零产出

**现象：** 自 2026-07-13T16:02:50Z 起无任何系统活动：
- 最后 heartbeat：2026-07-13T16:02:50Z（mode=Repairing，permission_mode=ReadOnly）
- 最后 pi_decision：2026-07-13T16:01:27Z（skip）
- 最后 opportunity：2026-07-13T16:00:51Z
- 今日（07-14）零提案、零 risk decision、零 pi_decision

**最可能原因：** 系统进程被手动停止（用户为代码迭代/调试停机，其他 Agent 正在进行代码修复）。
停机时 Operator 处于 Repairing/ReadOnly 状态（MON-015 遗留）。

**影响：**
1. HYPE/USDT short position（d13627b0fee7）持续 open，无止损/止盈监控
2. scanner/opportunity 管道完全停止，无新信号
3. signal_pending 491 条，prune_signal_pending_stale 代码已部署但从未执行过

**建议：** 重启系统后，立即确认 HYPE/USDT position 状态和当前 unrealized PnL，
以及 Repairing 模式是否仍然阻塞 Operator（见 RC-015）。

---

### RC-019 ｜ MON-019：mean_reversion 全量 0% 胜率（26 笔全败）

**现象：** mean_reversion scanner 关联的 26 笔历史 position 全部亏损，0 笔盈利，合计 -2.634 USDT。

**已知背景（与 MON-006 交叉）：**
这 26 笔 position 均有 `opportunity_id` 非 NULL（mean_reversion opp 关联）。但关闭时
`close_category='unknown'`（pnl.py 修复前的遗留），意味着这些都是在代码修复前由旧
`close_position()` 关闭的历史数据。

**潜在根因（需进一步验证）：**
1. **策略逻辑问题**：mean_reversion scanner 信号在当前市场环境下方向判断持续错误
   （26 次连续失败超出统计随机范围，胜率期望通常 ≥40%）
2. **fee 侵蚀**：若 mean_reversion 开仓 notional 较小（如 $25），双向 fee ≈ $0.03，
   毛利润只需轻微负就转为净亏损
3. **数据归因问题**：部分 take_profit/stop_loss 关闭但 `opportunity_id=NULL`（见 §7 NULL/no_opp），
   这些盈利的 paper 交易可能实际来自 mean_reversion 信号但归因断裂，使 mean_reversion 胜率虚低

**代码关联：**
- mean_reversion scanner 逻辑：待定位（需搜索 `scanner_family='mean_reversion'` 相关代码）
- opportunity_id 断裂已记录：MON-006，`aletheia/agents/paper_trader.py` 中 signal 生成路径

---

### RC-020 ｜ MON-020：momentum 近 48h avg PnL 恶化至 -0.85/笔

**现象：** momentum scanner 近 48h 7 笔，合计 -5.95 USDT，均值 -0.85/笔。
历史均值为 -0.13/笔（全量 19 笔），近期显著恶化。

**注意：** 近 48h 内 HYPE/USDT 相关交易占比高（多次 scale_in 被拒，仅 1 笔 executed 但仍 open），
momentum 的 7 笔中可能含多个止损出场。需系统重启后查看完整明细再判断是否为结构性问题。

---

## 问题汇总（第 1 次观测，2026-07-14T~08:00Z）

| 编号 | 严重度 | 描述 | 状态 |
|---|---|---|---|
| MON-001 | ✅ | `close_position()` 不写 outcome 字段 | 已修复 |
| MON-002 | 🔴 | 3 笔 pending 孤儿订单（23/16/13 天） | ⏳ 未修复 |
| MON-003 | 🔴 | 所有 scanner 历史 PnL 为负 | — 策略问题 |
| MON-005 | 🟠 | 116 笔历史 `unknown` close_category | — 遗留，不可追 |
| MON-006 | 🟠 | 部分 position `opportunity_id=NULL` | ⏳ 未修复 |
| MON-007 | 🟡 | signal_pending 491 条（涨 57 条），prune 代码已部署但进程停止未运行 | ✅ 代码已修复，等系统重启 |
| MON-009 | 🔴 | 6 笔 okx_demo submitted 孤儿订单（8-10 天） | ⏳ 未修复 |
| MON-011 | ✅ | paper_internal 执行通道封锁 | 已修复 |
| MON-012 | ✅ | 遗留数据封锁 | 已修复 |
| MON-013 | ✅ | sector_concentration 封锁 | 已修复 |
| MON-015 | 🔴 | Operator Repairing/ReadOnly 僵局 | ⚡ 需人工干预，系统重启后仍存在 |
| MON-016 | 🟠 | 美股 symbol 泄入 pipeline（NULL scanner=20 条/48h） | ⏳ 代码校验缺口 |
| MON-017 | 🟠 | `incomplete_entry_plan` LIT/USDT（`trading_decision.py:518` None check） | ⏳ 高敏感路径，需测试 |
| **MON-018** | **🔴 P0** | **系统进程于 07-13T17:16Z 停止（已修正），今日零产出** | **⚡ 需重启系统** |
| **MON-019** | **🟠** | **HYPE/USDT short 无监控持仓 16h+** | **⚡ 重启后立即确认** |
| **MON-020** | **🟠** | **mean_reversion 全量 0% 胜率（26 笔全败），近 48h momentum avg -0.85/笔** | ⏳ 待量化分析 |
| **MON-021** | **🟡** | **proposal_submission direction_overheated 31 次，Operator 持续重试被拒标的** | ⏳ Operator 无拒绝记忆 |
| **MON-022** | **🟠 NEW** | **XAG/USDT（白银）泄入 pipeline，Operator 生成 12 次无效 trade 决策** | ⏳ `research_worker.py:_is_valid_symbol()` 同 MON-016 |

---

## 第 2 次观测增量（2026-07-14，手动触发）

### 对比第 1 次（~08:00Z）的主要变化

| 指标 | 第 1 次 | 本次 | 变化 |
|---|---|---|---|
| opportunities 24h | 1,949 | 2,061 | +112（全为昨日积压，今日仍 0） |
| opportunities 48h | 2,510 | 2,622 | +112 |
| pi_decisions 最新 | 17:10:15Z（07-13） | **17:10:15Z（07-13）** | 无变化 |
| heartbeat 最新 | 17:16:07Z（07-13） | **17:16:07Z（07-13）** | 无变化 |
| Operator mode | Repairing | **Reviewing** | ✅ 已切换 |
| open positions | d13627b0fee7（HYPE short） | 同上 | 无变化 |
| signal_pending | 491 | **499** | +8 |
| proposal_submission rejected | 5 | **14** | +9 |

### MON-015 更新：Repairing → Reviewing

**上次观测时** Operator 为 `Repairing/ReadOnly`，本次 heartbeat 显示已切换为 `Reviewing/ReadOnly`。
该切换发生在 07-13T16:02Z～16:40Z 之间（16:40Z 起恢复了 scale_in 决策流）。

切换后活跃时段（07-13T16:40Z ～ 17:10Z）：

| 时间 | action |
|---|---|
| 16:40Z | skip |
| 16:47-16:57Z | scale_in × 5 |
| 16:59Z | trade |
| 17:01Z | trade |
| 17:08Z | scale_in |
| 17:10Z | trade（最后一条） |

说明 `Reviewing` 模式下 Operator 可正常做 trade/scale_in 决策，**不像 Repairing 模式全量 skip**。

**当前 intent：** `"Closed strategy 54c1ee4ba5b8 still lacks a recent post-trade review."`
Operator 处于 `Reviewing` 模式，等待对某笔已关闭策略进行 post-trade review，但 `is_busy=0` 且无 heartbeat——系统已停止（非锁死，是进程级停止）。

### MON-018 修正

第 1 次观测报告的停止时间（16:02:50Z）**有误**——当时 heartbeat 刚好记录到 Repairing→Reviewing 切换的边界，实际进程持续运行至 **17:16:07Z**。修正停机时长为 **~16h**（17:16Z 到当前）。今日（07-14）零产出结论不变。

### 无新增异常（第 2 次）

§1-§7 全部数据与第 1 次观测一致，无新增 positions 关闭，无新增 proposals，PnL 无变化。

---

## 第 3 次观测增量（2026-07-14，手动触发）

### 系统状态更新

| 指标 | 第 2 次 | 第 3 次 | 变化 |
|---|---|---|---|
| heartbeat 最新 | 17:16:07Z（07-13） | **18:15:33Z（07-13）** | 系统实际运行更晚 |
| latest opportunity | 17:11Z | **18:15:22Z** | +64min |
| latest pi_decision | 17:10:15Z | **18:14:07Z**（trade, XAG/USDT） | +64min |
| proposal_submission rejected | 14 | **31** | +17 |
| trade decisions（48h） | 46 | **60** | +14 |
| scale_in decisions（48h） | 24 | **27** | +3 |
| signal_pending | 499 | **509** | +10 |
| Operator intent | post-trade review | **"Open positions exist, snapshot 18min old"** | 切换 |

**MON-018 再次修正：** 系统停机时间为 **18:15:33Z**（非 17:16Z），停机约 14+h（至 07-14 当前）。

---

### MON-021 根因确认：`direction_overheated`

proposal_submission 31 笔拒绝全部为 `reason_code='direction_overheated'`：

| symbol | 拒绝次数 | 时间段 |
|---|---|---|
| HYPE/USDT（scale_in） | 12 | 16:36Z ～ 17:29Z |
| XAG/USDT（trade） | 12 | 17:16Z ～ 18:14Z |
| LIT/USDT（trade） | 4 | 17:54Z ～ 18:00Z |
| BEAT/BSB/NES/USDT | 各 1 | 14:55Z ～ 15:02Z |

`direction_overheated` 是 proposal_submission 层的方向/频率限制——同 symbol 同方向短时间内提交过多提案时触发，属于正常防护行为。

但暴露了两个底层问题：

**1. Operator 持续重试被拒的决策**（不记得上次被拒）——HYPE/USDT scale_in、XAG/USDT trade、LIT/USDT trade 在同一小时内各触发 4-12 次。

**2. XAG/USDT 是贵金属期货，不是加密货币**——进入 opportunity 管道并被 Operator 反复选中（见 MON-022 新增根因）。

---

### MON-022 NEW ｜ XAG/USDT（白银）进入 opportunity 管道

**现象：** XAG/USDT 出现 12 次 trade 决策（07-13T17:10Z ～ 18:14Z），全部因 `direction_overheated` 被 proposal_submission 拦截。XAG 是白银的 ISO 4217 代码，是贵金属大宗商品，不是加密货币。

**影响：** 
- 12 次 Operator 决策被浪费在无法执行的标的上
- 若某次 `direction_overheated` 未触发，XAG/USDT 提案将进入 risk engine，然后可能进入 paper_internal 执行链；paper broker 无法交易该标的，会产生 fill 错误

**与 MON-016 同根：** AAPL/USDT、NVDA/USDT、XAG/USDT 等非加密资产进入 pipeline 的原因相同——`research_worker.py:_is_valid_symbol()` 仅过滤 `/USD` 后缀的 Forex pair，`/USDT` 后缀可通过。

---

### RC-021/022 ｜ `direction_overheated` 和非加密资产泄漏

**`direction_overheated` 根因：**
`aletheia/agents/paper_trader.py` 或 `aletheia/agent_shell/trading_decision.py` 中的提案提交路径存在同 symbol + 同方向的限频逻辑（具体文件待确认）。Operator 没有关于上次被拒原因的上下文，因此在每次调度周期都重新决策同一标的，持续触发限频。

**XAG/USDT 根因（与 MON-016 同）：**
`aletheia/agents/research_worker.py:33-79`：`_is_valid_symbol()` 的过滤条件不够宽泛，未覆盖 XAG、XAU、XPT 等大宗商品代码。这些标的进入 universe → scanner → Operator 全链路，只在 proposal_submission 的 `direction_overheated` 防线被拦截（偶然拦截，非设计意图）。

---

## 第 4 次观测增量（2026-07-14，手动触发）

### 系统状态更新

| 指标 | 第 3 次 | 第 4 次 | 变化 |
|---|---|---|---|
| heartbeat 最新 | 18:15:33Z（07-13） | **19:19:07Z（07-13）** | 系统实际运行更晚 |
| Operator mode | Reviewing/ReadOnly | Reviewing/ReadOnly | 无变化 |
| latest opportunity | 18:15:22Z | **19:02:24Z** | +47min |
| latest pi_decision | 18:14:07Z（trade, XAG/USDT） | **18:45:58Z（skip）** | +31min |
| proposal_submission rejected | 31 | **37** | +6（全部 direction_overheated） |
| trade decisions（48h） | 60 | **66** | +6 |
| scale_in decisions（48h） | 27 | **28** | +1 |
| signal_pending | 509 | **0** | ✅ prune 执行完毕 |
| fills | 480 | **481** | +1（LIT/USDT 入场） |
| open positions | 1（HYPE only） | **2（HYPE + LIT/USDT）** | ⚠️ 新增 |
| Operator intent | "snapshot 18min old" | "Open positions exist and the latest portfolio snapshot is about 36 minutes old. Refresh portfolio state for operator review." | — |

**MON-018 再次修正：** 系统真实停机时间为 **2026-07-13T19:19:07Z**（非 18:15Z），停机至今约 **13.5h+**。

---

### ✅ MON-007 确认完全解决：signal_pending 归零

前三次观测中 signal_pending 持续累积（491 → 499 → 509），本次首次归零（509 → **0**）。

`prune_signal_pending_stale()` 在系统继续运行至 19:19Z 期间执行，删除了全部 30 分钟以上的 signal_pending 行。代码修复已验证有效，此后的 signal_pending 将维持正常低水位（仅保留最近 30 分钟内的实时信号）。

**MON-007 状态：✅ 已完全解决**

---

### ⚠️ MON-023 NEW ｜ LIT/USDT short position 入场（18:35:03Z）

**现象：** 新增一笔 `paper_internal` LIT/USDT short position：

| 字段 | 值 |
|---|---|
| id | 479abe695e73 |
| symbol | LIT/USDT |
| direction | short |
| quantity | 45.793758 |
| entry_price | 2.3572208 |
| stop_price | 2.62 |
| close_category | unknown |
| outcome_eligible | 0 |
| execution_mode | paper_internal |
| opened_at | 2026-07-13T18:35:03Z |

**与 MON-017 的关系：** 第 3 次观测时 LIT/USDT 曾因 `incomplete_entry_plan:stop_loss+take_profit` 连续 4 次被 risk engine 拒绝（17:54Z～18:00Z）。本次 18:35Z 入场的提案有 `stop_price=2.62`，说明后续提案中 stop_loss 字段已正确填写（可能是 Operator 在 18:14Z 后调整了决策，或后续提案走了不同代码路径）。MON-017（`trading_decision.py:518` None check 问题）仍需确认修复，但对 LIT/USDT 本次入场无直接阻断。

**注意：** `close_category='unknown'`、`outcome_eligible=0` 说明入场时 `pnl.py` 修复**尚未运行**于此 position（同 HYPE/USDT 的情形），该 position 关闭时仍会走旧代码路径。

**当前状态：** LIT/USDT short 与 HYPE/USDT short 同处无监控状态。两笔 position 均已持仓超 13 小时，系统已停止。

---

### MON-021 更新：direction_overheated 31→38（+1 at 21:01Z）

21:01Z ALLO/USDT trade 决策 → 提案被 `direction_overheated` 拒绝，累计达 38 次。

---

## 第 5 次观测增量（2026-07-14，手动触发）

### 系统状态更新

| 指标 | 第 4 次 | 第 5 次 | 变化 |
|---|---|---|---|
| heartbeat 最新 | 19:19:07Z（07-13） | **23:03:24Z（07-13）** | 系统又运行了近 4 小时 |
| Operator 当前 mode | Reviewing/ReadOnly | **Working/ReadOnly** + Degraded flash | ⚠️ 新异常 |
| latest opportunity | 19:02:24Z | **~23:05Z**（23:00Z 桶有 3 条） | +4h |
| latest pi_decision | 18:45:58Z（skip） | **23:03:06Z（skip）** | +4h18min |
| direction_overheated（total） | 37 | **38** | ALLO/USDT at 21:01Z |
| signal_pending | 0 | 0 | ✅ 不变 |
| open positions | HYPE + LIT | 同上 | 无变化 |
| fills | 481 | 481 | 无变化 |
| 今日（07-14）活动 | 0 | 0 | 系统完全停止 |

**MON-018 最终修正：** 系统停机时间为 **2026-07-13T23:03Z**（最终确认，不再变动）。
今日 07-14 所有表零活动。

---

### ⚠️ MON-024 NEW P0：market_data_stale — 价格数据源中断

**心跳记录（23:02-23:03Z 窗口）：**

```
23:02:43Z  Degraded   | ReadOnly | is_busy=1 | "Operator shell process is alive but its internal heartbeat is delayed; supervisor is keeping status fresh."
23:02:44Z  Working    | ReadOnly | is_busy=1 | "Gateway health issue: market_data_stale:42min"
...
23:02:54Z  Working    | ReadOnly | is_busy=1 | "Gateway health issue: market_data_stale:42min"
23:02:54Z  Working    | ReadOnly | is_busy=1 | "Gateway health issue: market_data_stale:54min"   ← 突变
23:03:04Z  Working    | ReadOnly | is_busy=1 | "Gateway health issue: market_data_stale:54min"
23:03:24Z  Working    | ReadOnly | is_busy=1 | "Gateway health issue: market_data_stale:54min"  ← 最后记录
```

**现象：**
- 23:03Z 时，Operator 报告 `market_data_stale:54min` → 价格数据源（非 opportunity scanner）约在 **22:09Z** 停止更新
- Operator 处于 Working/ReadOnly 状态，`is_busy=1`，正在检测 gateway 健康问题
- 23:02:43Z 出现一次 Degraded 模式（1 秒内恢复为 Working）

**与 opportunity 产出的关系：** opportunities 表显示 23:00Z 桶有 3 条记录（scanner 仍产出）。"market_data_stale" 指的是底层价格推送服务（tick/bar feed），而非 opportunity scanner 输出——两条数据流可能来自不同的 gateway 组件。

**影响：** Operator 检测到价格数据源中断，保持 Working/ReadOnly 状态监控，最终可能因 ReadOnly 限制无法修复问题，于 23:03Z 后停止。

**待定位：** `aletheia/` 中处理 `market_data_stale` 告警的代码（搜索 `market_data_stale`），确认 gateway 数据源断连的根因。

---

### ⚠️ MON-025 NEW：双心跳写入（Duplicate Heartbeats within 100ms）

**现象：** 23:02-23:03Z 时段出现多对时间戳仅相差约 100ms 的心跳行：

| 时间对 | 间隔 |
|---|---|
| 23:03:04.828Z + 23:03:04.726Z | 102ms |
| 23:02:54.839Z + 23:02:54.724Z | 115ms |
| 23:02:44.992Z + 23:02:44.723Z | 269ms |

正常情况下心跳应每个 cycle 写入一次。100ms 内双写表明：
1. 多个 Operator 进程/线程并发写入同一心跳行；或
2. Supervisor 与 Operator 内部心跳在同一时刻各自写入（对应 23:02:43Z Degraded 记录中的 "supervisor is keeping status fresh"）

**影响：** 监控时间线可信度下降——真实状态推断需排除重复行干扰。`Degraded` 一秒内恢复至 `Working` 可能是 supervisor 和 Operator 竞争写入同一行所致，而非真实的模式切换。

**待定位：** `agent_shell_heartbeats` 写入路径（Operator 自身 vs supervisor 监控进程），确认两者是否会并发写入。

---

### ⚠️ MON-026 NEW：Degraded 模式瞬间触发（23:02:43Z）

**现象：** `Degraded|ReadOnly|1|23:02:43Z|"Operator shell process is alive but its internal heartbeat is delayed; supervisor is keeping status fresh."`

这是整个观测历史中第一次出现 **Degraded** 模式。触发原因是 Operator 内部心跳延迟，supervisor 接管了状态刷新。1 秒后恢复为 Working 模式。

**影响：**
1. `Degraded` 模式按 UI 设计应显示为 **Blocked/yellow**（见 `systemState.ts` 的 `_TRADE_BLOCKING_MODES`），会阻断新提案生成。虽然本次仅持续 1 秒，但若未来持续进入 Degraded 则会导致 Operator 无法交易。
2. `Degraded` 触发时 Recommendations panel 应展示具体违规，但本次原因是内部心跳延迟——与交易违规性质不同，UI blockedDetail 文案（"检查 Recommendations panel"）不准确。

**与 MON-024 关系：** Degraded 闪现、双心跳、market_data_stale 三者均在同一时间窗口（23:02-23:03Z）出现，最可能的场景是：市场数据长期中断（42-54min stale）→ Operator 内部调度循环阻塞 → 触发 supervisor 心跳接管 → Degraded 状态写入 → Operator 循环恢复 → Working 状态覆盖写入。

---

### ALLO/USDT 追踪（21:01Z 活动窗口）

21:01Z 出现一批 filter_decisions：
- universe_allocation: passed=38, rejected=20
- paper_ranker: passed=96, rejected=61
- system_operator: **passed=2**（仅 2 个 opp 到达 Operator 决策层）
- Operator 决策 ALLO/USDT → trade（`pi_decisions` 21:01:11Z）
- proposal_submission: **rejected=1**（direction_overheated）→ 无提案进入 risk engine

然后 21:41Z、22:26Z、23:03Z 各一次空 symbol skip——可能是 Operator 在没有合格 opportunity 的情况下的空轮询 skip。

---

---

### RC-024 ｜ market_data_stale 根因

**代码路径：**

- `aletheia/agent_runtime/health.py:66-92`：`_market_data_stale_minutes()` — 查询 `market_snapshots` 表最新快照时间，计算距今分钟数
- `aletheia/agent_runtime/health.py:167-169`：若超过 `cfg.health.market_data_max_staleness_min` 阈值，追加 `market_data_stale:Xmin` issue
- `aletheia/agent_runtime/health.py:426-441`：自愈路径 — `self_heal_market_data=True`（config 默认开启）时尝试重启。但条件：仅当 exchange 可达时才重启；若 exchange 不可达则 defer 至 connectivity_monitor（`health.py:432-434`）

**推断场景：**
~22:09Z 时，`market_snapshots` 最新快照停止更新（可能原因：exchange 价格推送断连、market data 采集服务异常、或 ReadOnly 权限模式限制了 snapshot 写入）。健康检查发现 42min stale，尝试自愈，但 exchange 不可达（或 ReadOnly 阻止重启），跳过重启，仅记录告警。后续 Operator 在 Working 模式下持续监测，直至 23:03Z 进程停止。

**config 参考：** `aletheia/config/schema.py:369` `self_heal_market_data: bool = True`

---

### RC-025 ｜ 双心跳写入根因

**代码路径：**

- `aletheia/gateway/operator_shell.py:607-581`：`_record_unavailable()` — supervisor 在检测到 Operator 内部心跳延迟时（`_operator_heartbeat_stale()` line 725）写入 Degraded 行，mode 硬编码为 `"Degraded"`（line 555）
- `_operator_heartbeat_stale()`（line 725-744）：仅写入 Degraded 行；Operator 进程本身也在并发写入 Working 心跳
- `agent_shell_heartbeats` 表：允许 INSERT 多行（非唯一约束），两个写入来源各自 INSERT，产生重复行

**根因：** supervisor 监控循环（`OperatorShellMonitor._operator_heartbeat_stale()`）与 Operator 进程内部心跳写入路径是**两个独立线程/进程**。它们通过相同的 `agent_shell_heartbeats` 表通信，但表没有 ON CONFLICT REPLACE 约束，所以同一时刻会产生两行。

---

### RC-026 ｜ Degraded 瞬闪根因

**代码路径：**

- `aletheia/gateway/operator_shell.py:725-744`：`_operator_heartbeat_stale()` — 每次 supervisor poll cycle（poll_interval=1s）检查 Operator 内部心跳年龄，若超过 `heartbeat_stale_after_sec` 则调用 `_record_unavailable(preserve_latest=True)`
- `operator_shell.py:552-555`：`_record_unavailable` 写入时 mode 强制设为 `"Degraded"`
- 注意：`_operator_heartbeat_stale()` 第 744 行 `return False`——该函数**不触发重启**，仅写入 Degraded 行

**触发序列：**
1. Operator 内部心跳延迟（可能因处理 market_data_stale 告警的 LLM 调用耗时超过 `heartbeat_stale_after_sec`）
2. Supervisor poll 检测到延迟 → 23:02:43Z 写入 Degraded 行
3. Operator 心跳恢复 → 23:02:44Z 写入 Working 行（1 秒内）
4. 外部观测者看到 Degraded 瞬现，随即被 Working 覆盖

**UI 影响：** `getOperatorDisplayStatus()` 取最新一行决定状态，Degraded 恢复后即显示 Running/Blocked（取决于 mode），不会持续显示为 Blocked。但若未来内部心跳频繁延迟超过阈值，Degraded 将持续出现，触发 UI blocked 状态。

---

## 问题汇总（截至第 5 次观测，2026-07-14）

| 编号 | 严重度 | 描述 | 状态 |
|---|---|---|---|
| MON-001 | ✅ | `close_position()` 不写 outcome 字段 | 已修复 |
| MON-002 | 🔴 | 3 笔 pending 孤儿订单（23/16/13 天） | ⏳ 未修复 |
| MON-003 | 🔴 | 所有 scanner 历史 PnL 为负 | — 策略问题 |
| MON-005 | 🟠 | 116 笔历史 `unknown` close_category | — 遗留，不可追 |
| MON-006 | 🟠 | 部分 position `opportunity_id=NULL` | ⏳ 未修复 |
| MON-007 | ✅ | signal_pending 积压 | 已完全解决（第 4 次确认归零） |
| MON-009 | 🔴 | 6 笔 okx_demo submitted 孤儿订单（8-10 天） | ⏳ 未修复 |
| MON-011 | ✅ | paper_internal 执行通道封锁 | 已修复 |
| MON-012 | ✅ | 遗留数据封锁 | 已修复 |
| MON-013 | ✅ | sector_concentration 封锁 | 已修复 |
| MON-015 | ✅ | Operator Repairing→Reviewing，交易恢复正常 | 自行恢复 |
| MON-016 | 🟠 | 美股/大宗商品 symbol 泄入 pipeline | ⏳ `research_worker.py:_is_valid_symbol()` |
| MON-017 | 🟠 | `incomplete_entry_plan` LIT/USDT（`trading_decision.py:518` None check） | ⏳ 高敏感路径，需测试 |
| MON-018 | 🔴 P0 | 系统于 **07-13T23:03Z** 停止（最终确认），今日零产出 | ⚡ 需重启系统 |
| MON-019 | 🔴 | HYPE/USDT short 无监控持仓 | ⚡ 重启后立即确认 |
| MON-020 | 🟠 | mean_reversion 全量 0% 胜率（26 笔全败） | ⏳ 待量化分析 |
| MON-021 | 🟡 | direction_overheated 38 次，Operator 持续重试被拒标的 | ⏳ Operator 无拒绝记忆 |
| MON-022 | 🟠 | XAG/USDT（白银）泄入 pipeline，同 MON-016 根因 | ⏳ `research_worker.py:_is_valid_symbol()` |
| MON-023 | 🟠 | LIT/USDT short 18:35Z 入场，无监控持仓，close_category=unknown | ⚡ 重启后确认 |
| **MON-024** | **🔴 P0 NEW** | **market_data_stale:54min，价格数据源于 ~22:09Z 中断，Operator Working/ReadOnly 检测异常后停止** | ⚡ 调查 gateway 数据源断连原因 |
| **MON-025** | **🟠 NEW** | **双心跳写入（100ms 内重复）：supervisor 与 Operator 并发写入竞争** | ⏳ 定位心跳写入路径 |
| **MON-026** | **🟠 NEW** | **Degraded 模式瞬间触发（23:02:43Z），内部心跳延迟，1 秒内恢复** | ⏳ 关注是否持续触发 |

---

## 第 6 次观测增量（2026-07-14T00:27Z，手动触发）

> **系统正在运行**：本次观测期间系统处于 Trading 模式（实时写入数据），部分计数反映查询执行瞬间的状态。

### 系统状态更新

| 指标 | 第 5 次 | 第 6 次 | 变化 |
|---|---|---|---|
| heartbeat 最新 | 23:03:24Z（07-13） | **00:27:10Z（07-14）** | ✅ 系统重启，重新上线 |
| Operator mode | Working/ReadOnly | **Trading/ReadOnly** | 模式变化 |
| Operator is_busy | 1（检测异常） | **0** | 空闲中 |
| latest opportunity | ~23:05Z（07-13） | **00:27:08Z（07-14）** | 今日有新产出 |
| opportunities 今日 | 0 | **~8**（实时写入中） | 系统开始扫描 |
| latest pi_decision | 23:03:06Z | **00:25:56Z（07-14）** | 活跃中 |
| pi_decisions 今日 | 0 | **6 skips** | 全为 skip |
| Operator intent | market_data_stale:54min | **"observer 76min old, refresh needed"** | 见下节 |
| signal_pending | 0 | 0 | ✅ 不变 |
| open positions | HYPE + LIT（无变化） | HYPE + LIT | 无变化 |
| fills | 481 | 481 | 无变化 |
| 新 position 关闭 | 0 | 0 | 无 |

**MON-018 已解决**：系统于 2026-07-14T00:22Z 恢复运行（Trading/ReadOnly 模式），距 07-13T23:03Z 停机约 **79 分钟**。

---

### ✅ MON-018 已恢复：系统重启至 Trading 模式

- 心跳从 23:03Z（Working/ReadOnly，market_data_stale）到 00:25Z（Trading/ReadOnly）之间共约 82 分钟的模式转换
- 00:22Z 起开始产出新 opportunities（+8 条，截至 00:27Z 仍在增加）
- MON-024 中描述的 `market_data_stale` 问题已自愈（Trading 模式表明价格数据源已恢复）
- 00:27:10Z 的最新心跳确认系统处于实时运行状态

**双心跳现象（MON-025）仍在**：Trading 模式下依然出现 100ms 内成对写入（例如 00:27:00.285Z + 00:27:00.228Z）。

---

### ⚠️ MON-027 NEW：Observer 产出不足（76min stale）

**现象：**
- Operator intent：`"Latest market observer activity is about 76 minutes old. Refresh market scan to repopulate opportunities."`
- 按此计算，observer 上次活跃时间约 **23:11Z**（00:27Z − 76min）
- 今日（07-14）新增 opportunities 仅 ~8 条（截至 00:27Z），正常运行时应为每批次数十至数百条

**与实际数据的矛盾：** `opportunities` 表最新记录为 00:27:08Z，距心跳时刻仅 2 秒，表明 scanner 确实在写入。"76 minutes old" 可能指的是**上次完整 observer scan cycle 完成**的时间，而非最新 opportunity 行的时间。当前扫描可能只产出了部分标的（8 条 vs 正常的数百条），未完整覆盖全市场。

**pipeline 过滤比例（今日 07-14）：**

| stage | passed | rejected |
|---|---|---|
| observer_pre_filter | 7 | — |
| universe_allocation | 21 | 20 |
| paper_ranker | 4 | 2 |
| system_operator | — | — |（尚未进入决策层） |

`paper_ranker` 仅 4 条通过，且没有一条到达 `system_operator` 决策层（正常情况下每批应有 10-50 条）。系统当前处于低 opportunity 密度状态。

**影响：** Trading 模式下 Operator 循环空转 skip，无新 trade 提案产生。

---

### §4 更新：48h Risk 通过率（10.5%）

| 类型 | 48h 数量 | 说明 |
|---|---|---|
| executed | 2 | HYPE/USDT + LIT/USDT（均为 07-13） |
| rejected | 17 | 见下表 |

**48h 拒绝原因：**

| 原因 | 次数 | 备注 |
|---|---|---|
| `account_loss_unavailable:legacy_unverified_close` | 9 | 全部为 07-13T06:58Z 之前（fix 前），fix 后零新增 ✅ |
| `symbol_already_open:HYPE/USDT` | 4 | HYPE 仓位已开，scale_in 被拦截 |
| `symbol_already_open:BTC/USDT` | 2 | 孤儿订单（MON-002）持续阻断 |
| `incomplete_entry_plan:stop_loss+take_profit` | 2 | MON-017 |
| `sector_concentration:momentum:100%>60%` | 1 | 07-13 早期（fix 前） |

**今日（07-14）0 笔 proposals**，当前 risk 通道实际无新提案进入。

---

### 问题汇总（截至第 6 次观测，2026-07-14T00:27Z）

| 编号 | 严重度 | 描述 | 状态 |
|---|---|---|---|
| MON-001 | ✅ | `close_position()` 不写 outcome 字段 | 已修复 |
| MON-002 | 🔴 | 3 笔 pending 孤儿订单（23/16/13 天） | ⏳ 未修复 |
| MON-003 | 🔴 | 所有 scanner 历史 PnL 为负 | — 策略问题 |
| MON-005 | 🟠 | 116 笔历史 `unknown` close_category | — 遗留，不可追 |
| MON-006 | 🟠 | 部分 position `opportunity_id=NULL` | ⏳ 未修复 |
| MON-007 | ✅ | signal_pending 积压 | 已完全解决 |
| MON-009 | 🔴 | 6 笔 okx_demo submitted 孤儿订单（8-10 天） | ⏳ 未修复 |
| MON-011 | ✅ | paper_internal 执行通道封锁 | 已修复（fix 后零新增 ✅） |
| MON-012 | ✅ | 遗留数据封锁 | 已修复 |
| MON-013 | ✅ | sector_concentration 封锁 | 已修复 |
| MON-015 | ✅ | Operator Repairing→Reviewing | 自行恢复 |
| MON-016 | 🟠 | 美股/大宗商品 symbol 泄入 pipeline | ⏳ `research_worker.py:_is_valid_symbol()` |
| MON-017 | 🟠 | `incomplete_entry_plan` LIT/USDT（`trading_decision.py:518`） | ⏳ 高敏感路径 |
| MON-018 | ✅ | 系统停机 | **✅ 已恢复，Trading 模式，00:22Z 重启** |
| MON-019 | 🔴 | HYPE/USDT short 无监控持仓 | ⚡ 系统已重启，待确认 HYPE/USDT 状态 |
| MON-020 | 🟠 | mean_reversion 全量 0% 胜率 | ⏳ 待量化分析 |
| MON-021 | 🟡 | direction_overheated 38 次 | ⏳ 持续监控 |
| MON-022 | 🟠 | XAG/USDT（白银）泄入 pipeline | ⏳ `research_worker.py:_is_valid_symbol()` |
| MON-023 | 🟠 | LIT/USDT short 无监控持仓 | ⚡ 待确认 |
| MON-024 | ✅ | market_data_stale，价格数据源中断 | **✅ 已自愈，系统切回 Trading 模式** |
| MON-025 | 🟠 | 双心跳写入（100ms 内重复） | ⏳ Trading 模式下仍持续 |
| MON-026 | 🟠 | Degraded 模式瞬间触发（23:02:43Z） | ⏳ 未再出现 |
| **MON-027** | **🟡 NEW** | **Observer 产出不足：76min stale，今日仅 ~8 条 opportunity vs 正常数百条** | ⚡ 观察是否自行恢复 |

---

## 第 7 次观测增量（2026-07-14T01:31Z，手动触发）

### 系统状态更新

| 指标 | 第 6 次（~00:27Z） | 第 7 次（01:31Z） | 变化 |
|---|---|---|---|
| heartbeat 最新 | 00:27:10Z（Trading） | **01:30:59Z（Repairing）** | ⚠️ 模式切换 |
| Operator mode | Trading/ReadOnly | **Repairing/ReadOnly** | 同 MON-015 模式 |
| Operator is_busy | 0 | 0 | — |
| latest opportunity | 00:27:08Z | **00:47:08Z** | +20min |
| opportunities 今日 | ~8 | **24**（00:00-00:47Z） | +16 |
| latest pi_decision | 00:25:56Z | **01:20:09Z** | +54min |
| pi_decisions 今日 | 6 skips | **10 skips + 2 scale_in** | +6 |
| filter_decisions 最新 | 00:25:15Z | **01:24:55Z** | 活跃至 01:24Z |
| signal_pending | 0 | 0 | ✅ 不变 |
| open positions | HYPE + LIT | HYPE + LIT | 无变化 |
| fills | 481 | 481 | 无变化 |
| 新 position 关闭 | 0 | 0 | 无 |

---

### 🔴 MON-028 NEW P0：`post_trade_reviewer` 失控循环（965 次/24h）

**现象：**

| 时间段 | 运行次数（均为 manual） |
|---|---|
| 07-13T01:00Z | 2 |
| 07-13T04:00Z | 28 |
| 07-13T10:00Z ～ 17:00Z | 92-93/h（峰值） |
| 07-14T00:00Z | 43 |
| 07-14T01:00Z | 14（截至 01:24Z） |
| **合计（近 24h）** | **965 次** |

正常调度：每 4 小时一次（`post_trade_reviewer: interval:4h`），24h 约 6 次。实际跑了 **965 次**，为正常量的 **160 倍**。每次间隔约 10 秒，全部 `trigger_type='manual'`、`status='completed'`。

**根因分析（代码追踪）：**

`aletheia/agents/post_trade_reviewer.py:54-56`：
```python
if not is_tool_permitted("create_post_trade_review", permission_mode):
    errors.append("create_post_trade_review not permitted in current mode")
    return {"outputs": outputs, "errors": errors}
```

在 ReadOnly 模式下，`create_post_trade_review` 权限不被允许，函数在第 56 行**立即返回**，不写入任何评审记录。

`aletheia/agents/post_trade_reviewer.py:82-93`（幂等性检查）：
```python
window_key = _fills_window_key(strategy_id, strat_fills)
if _review_already_written(strategy_id, window_key):
    # skip
```
幂等性检查在 line 82（权限检查之后），ReadOnly 模式下永远无法到达。

**循环机制：**
1. Operator 检测到未完成的 post-trade review（知识库无对应记录）
2. 手动触发 `post_trade_reviewer`
3. ReadOnly 模式 → 权限检查失败 → 立即返回，不写评审
4. 下次循环：知识库仍无记录 → Operator 再次手动触发
5. 每 10 秒重复，无限循环

**次要效应：** Operator 内部的 `operator_recommendations.py:1565-1593` 检测到 "agent 有 32+ 次 manual run"（`agent_schedule_change_proposal_due` recommendation），触发 `create_repair_proposal` 任务 → Operator 进入 Repairing 模式（~01:29Z）。但 ReadOnly 模式下同样无法写入修复提案 → Repairing 锁死。

**历史先例：** `post_trade_reviewer.py:84-85` 代码注释明确提及 "the historical 'post_trade_review flood' bug"，表明此类循环已发生过，当时通过幂等性 window_key 修复——但 ReadOnly 早返回绕过了幂等性检查。

**影响：**
1. Repairing 模式阻断所有新 trade 提案
2. 系统每 10 秒执行一次无效的 LLM 调用（实际因权限检查立即 return，但仍消耗调度资源）
3. 数据库 `agent_runs` 表高速写入，可能影响其他查询性能
4. 直至权限模式变为 ReadWrite 或人工清理 manual run 队列才能解除

---

### MON-027 更新：Observer 产出恢复至 24 条

相比第 6 次观测（~8 条），本次 observer 今日产出 **24 条**（00:00-00:47Z），scanner 覆盖正常（含 momentum/mean_reversion/short_bias）。但自 00:47Z 起无新 opportunities——可能因资源被 `post_trade_reviewer` 循环占用，或 observer 进入下次调度间隔。

`observer_pre_filter|rejected|2`（今日）：出现 2 条 opportunity 被预过滤拒绝，历史中不常见，需关注是否为新 symbol 类型（MON-016/022 相关）。

---

### §3 今日 scale_in 细节

Operator 在 Trading 模式（01:29Z 切换为 Repairing 之前）发起 2 次 LIT/USDT scale_in 决策：

| 时间 | action | symbol | proposal_submission 结果 |
|---|---|---|---|
| 00:31:42Z | scale_in | LIT/USDT | rejected / direction_overheated |
| 00:36:11Z | scale_in | LIT/USDT | rejected / direction_overheated |

两次均在 proposal_submission 层被拦截（未进入 risk engine，未写入 trade_proposals）。LIT/USDT short 已存在且方向相同，触发限频。

---

### 问题汇总（截至第 7 次观测，2026-07-14T01:31Z）

| 编号 | 严重度 | 描述 | 状态 |
|---|---|---|---|
| MON-001 | ✅ | `close_position()` 不写 outcome 字段 | 已修复 |
| MON-002 | 🔴 | 3 笔 pending 孤儿订单 | ⏳ |
| MON-003 | 🔴 | 所有 scanner 历史 PnL 为负 | — 策略问题 |
| MON-005 | 🟠 | 116 笔历史 `unknown` close_category | — 遗留 |
| MON-006 | 🟠 | 部分 position `opportunity_id=NULL` | ⏳ |
| MON-007 | ✅ | signal_pending 积压 | 已完全解决 |
| MON-009 | 🔴 | 6 笔 okx_demo submitted 孤儿订单 | ⏳ |
| MON-011–013 | ✅ | 执行通道封锁 × 3 | 已修复 |
| MON-015 | ✅ | Repairing→Reviewing，交易恢复 | 已自愈（但 MON-028 触发新一轮） |
| MON-016/022 | 🟠 | 非加密 symbol 泄入 pipeline | ⏳ `research_worker.py:_is_valid_symbol()` |
| MON-017 | 🟠 | `incomplete_entry_plan` LIT/USDT | ⏳ 高敏感路径 |
| MON-018 | ✅ | 系统停机 | 已恢复（00:22Z）|
| MON-019/023 | 🔴 | HYPE + LIT/USDT 无监控持仓 | ⚡ 持续 open，19h+ |
| MON-020 | 🟠 | mean_reversion 0% 胜率 | ⏳ |
| MON-021 | 🟡 | direction_overheated 40 次 | ⏳ |
| MON-024 | ✅ | market_data_stale | 已自愈 |
| MON-025 | 🟠 | 双心跳写入 | ⏳ Trading/Repairing 均出现 |
| MON-026 | 🟠 | Degraded 瞬闪 | ⏳ 暂未再现 |
| MON-027 | 🟡 | Observer 产出不足 | ⏳ 今日 24 条（恢复中），00:47Z 后停止 |
| **MON-028** | **🔴 P0 NEW** | **`post_trade_reviewer` 失控循环：965 次/24h，ReadOnly 模式下权限检查死循环，导致 Repairing 模式锁死** | **✅ 已修复（01:55Z）** |

---

## §8 MON-028 MVP 修复记录（2026-07-14T01:55Z）

### 问题根因

```
post_trade_reviewer.py:54-56  权限检查 FIRST → ReadOnly → 立即返回 error
                               ↓ 无法写入 review → Operator 看到 review 仍 pending
operator_recommendations.py   (Python 层 — 实际 UNUSED by TS Operator)
recommendation_engine.ts:746  `if (!snapshot.status.paused && repeatedManualAgent)`
                               → permissionMode 未传入 → ReadOnly 不守门
                               → 生成 create_repair_proposal 推荐
operator_loop.ts:2626          `deriveMode(snapshot, recommendations)`
                               → 使用未过滤的 recommendations
                               → taskType='create_repair_proposal' → mode='Repairing'
                               → 死循环：Repairing → 触发 post_trade_reviewer → 权限拒绝 → 继续 Repairing
```

### 已部署的三处修复

**修复 1（Python）`aletheia/agents/post_trade_reviewer.py:54-111`**
- 将 fills 加载 + 分组 + 幂等性检查移至权限检查之前
- 新增 bulk 预检：若所有 fill-window 均已 reviewed → 直接返回 success（无需写权限）
- 新增 anti-runaway 节流：5 min 内 ≥3 次被 ReadOnly 拒绝 → 返回 `throttled` 信号停止重试

**修复 2（Python）`aletheia/agent_shell/operator_recommendations.py:1579-1580`**
- 在 `build_recommendations()` 中为 `agent_schedule_change_proposal_due` 推荐添加 ReadOnly 守门
- *注：经验证此函数为 Python 独立路径，TypeScript Operator 实际使用 `recommendation_engine.ts`，该守门为防御性补丁*

**修复 3（TypeScript — 核心修复）`operator-shell/src/operator_loop.ts:2626`**
- 将 `deriveMode(snapshot, recommendations)` 改为 `deriveMode(snapshot, taskRecommendations)`
- `taskRecommendations` 是 `recommendationsAllowedForTaskSync()` 过滤后的版本：在 ReadOnly 模式下，`create_repair_proposal` 的 `taskType` 被清除为 `undefined`
- `deriveMode` 不再看到 `create_repair_proposal` → 不再返回 `Repairing` → 模式恢复为 `Observing`
- **gateway restart（01:52Z + 01:54Z）后生效**

### 修复后系统状态（01:55Z）

| 指标 | 修复前 | 修复后 |
|---|---|---|
| Operator 模式 | Repairing / ReadOnly | **Observing** / ReadOnly ✅ |
| `post_trade_reviewer` 运行速率 | 6 次/min（965/24h） | **0 次/min** ✅ |
| pi_decisions（30 min） | 0（被 Repairing 阻断） | 0（ReadOnly 配置，不执行交易） |
| Opportunities（30 min） | 0 | 0（ReadOnly 静默） |
| 待处理任务队列 | post_trade_reviewer 死循环 | 历史遗留 820 queued evaluate_strategy_state + 69 diagnose_system_health 等待清理 |

### 残余问题

- **permission_mode=ReadOnly** 持续自 2026-07-12T00:01Z，为配置选择，不自动切换；恢复 paper 交易需人工将 permission 提升至 Paper/Full
- **HYPE/USDT short（-11.5h）、LIT/USDT short（-7.4h）** 在 ReadOnly 模式下无法被自动管理，需手动干预
- **820 queued `evaluate_strategy_state` tasks** 为历史积压，系统重启后会逐步消化；在 ReadOnly 模式下大部分会直接 skip
- **幂等性语义错位（未修复）**：Operator 的 24h 新鲜度检查（`post_trade_review` knowledge item 是否在最近 24h 创建）与 `_review_already_written()` 的 fill-window hash 检查不同步；当 strategy 在 24h 内关闭但 fill-window 未变化时，仍会每 15 min 触发一次 generate_review 任务（幂等返回，无副作用，不造成循环）

---

## 第 8 次观测增量（2026-07-14T~02:07Z，MON-028 修复后标准 7 项）

> 本次为 MON-028 修复（01:55Z）并将 `permission_mode` 切换至 `Auto` 后的首轮标准 7 项观测。
> 数据窗口：48h（2026-07-12 ～ 2026-07-14T02:07Z）

### §1 Opportunity 产出（48h）

| 窗口 | 数量 |
|---|---|
| 最近 24h | 1,263 |
| 最近 48h | 2,319 |
| 全量累计 | 26,525 |

**Scanner 分布（48h）：**

| scanner_family | count | 占比 |
|---|---|---|
| momentum | 1,190 | 51.3% |
| mean_reversion | 448 | 19.3% |
| short_bias | 397 | 17.1% |
| volatility | 227 | 9.8% |
| NULL | 48 | **2.1%** |
| event | 9 | 0.4% |

**观察：**
- NULL scanner_family 48 条（2.1%），较第 1 次（20 条，0.8%）有所上升，MON-016/022（非加密资产泄入）仍在持续。
- 系统恢复 Trading/Auto 模式后（02:05Z PI/USDT 入场），今日 opportunity 产出已重新开始。

---

### §2 Filter 各层通过率（48h）

**拒绝原因明细：**

| 阶段 | reason_code | 次数 |
|---|---|---|
| observer_pre_filter | no_sufficient_signal | 2 |
| paper_ranker | confidence_below | 1,505 |
| paper_ranker | universe_member_expired | 325 |
| paper_ranker | execution_eligible_false | 65 |
| paper_ranker | equity_shadow_track | 48 |
| paper_ranker | guard_blocked | 46 |
| paper_trader_selection | duplicate_symbol | 380 |
| paper_trader_selection | signal_cooldown | 353 |
| paper_trader_selection | signal_batch_limit | 94 |
| paper_trader_selection | open_position_duplicate | 10 |
| proposal_submission | direction_overheated | 41 |
| risk | risk_rejected | 17 |
| universe_allocation | same_pool_control_not_selected | 1,930 |

**观察：**
- `paper_ranker.confidence_below` 1,505 条：ranker 层最主要过滤项，高频率意味着 scanner 产出信号中大多数未达到置信度阈值。
- `paper_trader_selection.duplicate_symbol` 380 条：同一 symbol 在同一 batch 内重复，正常去重行为。
- `proposal_submission.direction_overheated` 41 条（MON-021）：Operator 无拒绝记忆，持续对已被限频标的重新发起提案。
- `universe_allocation.same_pool_control_not_selected` 1,930 条：universe 层最主要过滤项，control_not_selected 为正常 portfolio 约束。

---

### §3 System Operator 决策分布（48h）

| action | count |
|---|---|
| skip | 455 |
| trade | 40 |
| scale_in | 21 |
| watch | 17 |
| signal_pending | 4 |

**`watch` 决策根因：**
所有 17 条 watch 决策的 reasoning 均包含 **"Signal degradation alert (24h accuracy 30.5%/32.9%, below 40% threshold)"** — 系统信号准确率低于阈值，Operator 正确降低风险偏好，持仓观望而非新开仓。这是预期行为，非异常。

**近期活动确认（MON-028 fix 后）：**
- 02:00Z：trade 决策 → PI/USDT short 入场（02:05Z fill）
- 系统已从 Repairing 锁死恢复至正常 Trading 决策流 ✅

---

### §4 Proposal 和 Risk approve/reject（48h）

| 类型 | 数量 |
|---|---|
| trade_proposals rejected | 17 |
| trade_proposals executed | 3 |
| risk_decisions approved | 3（15%） |
| risk_decisions rejected | 17（**85%**） |

**48h risk 拒绝原因：**

| violations | 次数 | 状态 |
|---|---|---|
| `account_loss_unavailable:legacy_unverified_close` | 9 | ✅ 全为 fix 前（07-13T06:58Z 前），fix 后零新增 |
| `symbol_already_open:HYPE/USDT` | 4 | 正常：HYPE 仓位已开，scale_in 被拦截 |
| `symbol_already_open:BTC/USDT` | 2 | MON-002：孤儿 pending 订单持续阻断 |
| `incomplete_entry_plan:stop_loss+take_profit` | 2 | MON-017：LIT/USDT 提案无 stop_loss |
| `sector_concentration:momentum:100%>60%` | 1 | ✅ fix 前（07-12T03:03Z 前），fix 后零新增 |

**观察：**
- 85% risk 拒绝率中，9/17 来自已修复的 `account_loss_unavailable` 遗留 violation（不会再新增）；
- 剩余 8 次中 4 次为正常 scale_in 拦截、2 次为孤儿订单阻断（MON-002）；
- MON-028 fix 确认：fix 后（01:55Z）zero 新增 `account_loss_unavailable` violation ✅。

---

### §5 Order / Fill / Position 完整性

**48h Fills：** 3 笔，全部 `paper_internal`，均为 filled（HYPE 07-13T14:26Z、LIT 07-13T18:35Z、PI 07-14T02:05Z）✅

**孤儿订单（pending/submitted >1 天，无变化）：** 3 笔（与第 5-7 次观测一致，无新增）

| symbol | status | mode | 悬挂时长 |
|---|---|---|---|
| BTC/USDT | pending | bitget_demo | ~23 天 |
| ETH/USDT | pending | okx_demo | ~16 天 |
| NEAR/USDT | pending | okx_demo | ~13 天 |

**Open positions（02:07Z）：**

| symbol | direction | age | entry_price | unrealized_pnl |
|---|---|---|---|---|
| HYPE/USDT | short | **11.9h** | 64.0050 | **+$0.90** |
| LIT/USDT | short | **7.7h** | 2.3572 | **-$2.58** |
| PI/USDT | short | 0.2h | 0.07284 | ~-$0.05 |

**观察：**
- PI/USDT short（02:05Z）确认系统恢复正常交易 ✅（MON-028 修复后首笔新入场）
- HYPE/USDT 持仓 11.9h（unrealized +$0.90）；LIT/USDT 持仓 7.7h（unrealized -$2.58），LIT 亏损仍可观，当前无止损触发迹象
- MON-019/023（无监控持仓）：系统已恢复 Trading 模式，两笔 position 现处于正常监控中 ✅

---

### §6 Position 关闭质量（48h）

48h 内 1 笔 position 关闭：

| symbol | close_category | close_reason | outcome_eligible | exit_fill_verified |
|---|---|---|---|---|
| 某 momentum strategy | strategy | stop_loss | ✅ 1 | ✅ 1 |

`close_category='strategy'`、`outcome_eligible=1`、`exit_fill_verified=1` — pnl.py 修复后代码路径正确写入 outcome 字段 ✅（MON-001 持续生效）。

---

### §7 PnL 与 scanner attribution（48h closed）

| scanner_family | 笔数 | total_pnl | avg_pnl | wins | losses |
|---|---|---|---|---|---|
| momentum | 1 | -2.6131 | -2.6131 | 0 | 1 |

**全量历史（参考）：** 所有 scanner 合计 252 笔，全部负 PnL（MON-003，策略层问题）。

**注意：** 近 48h 仅 1 笔已关闭 position（momentum stop_loss，PnL=-2.61），样本量过小，无法判断趋势变化。3 笔 open positions 的最终 PnL 尚未确定。

---

### 本轮新增异常

#### ⚠️ MON-029 新增 ｜ market_observer 86/118 errors in 4h（startup-stale artifact）

**现象：** `market_observer` 过去 4 小时内 118 次运行中有 86 次含 error，错误率 73%。
所有可见错误均为 `"marked stale by startup: gateway startup reconciled previous incomplete agent run"`。

**根因：** 非真实运行错误，而是 gateway 重启时批量标记——每次重启将所有"进行中"的 agent_run 行标记为 stale。
01:52Z 和 01:54Z 两次 gateway restart（MON-028 修复期间）标记了大量历史 runs。
`market_observer` 每约 2 分钟运行一次（118/240min ≈ 0.49/min），多数 stale 标记来自重启时在飞的 runs。

**影响：** 无真实影响。重启后 market_observer 运行正常，opportunities 持续产出。
**需注意：** 若后续非重启期间 market_observer 仍持续高错误率，则需深查真实原因。

#### ✅ MON-028 修复确认

- 系统于 02:05Z 成功开 PI/USDT short → Trading 模式工作正常 ✅
- Repairing 模式死锁已消除 ✅
- `post_trade_reviewer` 失控循环已停止（4h 窗口内 999 次全为 fix 前，fix 后零新增） ✅

#### 🟠 signal accuracy 持续低位（30.5%/32.9%，阈值 40%）

17 条 watch 决策均包含 signal degradation alert，表明过去 24h 信号准确率 30.5-32.9%，低于 40% 阈值。
Operator 在高不确定性环境下正确选择 watch 而非开仓（预期行为）。
但若此准确率持续走低，说明当前市场环境与历史训练信号偏离，需关注 scanner 信号质量趋势。

---

### 问题汇总（截至第 8 次观测，2026-07-14T~02:07Z）

| 编号 | 严重度 | 描述 | 状态 |
|---|---|---|---|
| MON-001 | ✅ | `close_position()` 不写 outcome 字段 | 已修复，持续生效 |
| MON-002 | 🔴 | 3 笔 pending 孤儿订单（BTC/ETH/NEAR，13-23 天） | ⏳ 持续阻断 BTC risk check |
| MON-003 | 🔴 | 所有 scanner 历史 PnL 为负（252 笔全亏） | — 策略层问题 |
| MON-005 | 🟠 | 116 笔历史 unknown close_category | — 遗留，不可追 |
| MON-006 | 🟠 | 部分 position opportunity_id=NULL | ⏳ |
| MON-007 | ✅ | signal_pending 积压（归零） | 已完全解决 |
| MON-009 | 🔴 | 6 笔 okx_demo submitted 孤儿订单 | ⏳ |
| MON-011–013 | ✅ | 执行通道封锁 | 已修复 |
| MON-015 | ✅ | Repairing 锁死 | 已自愈（MON-028 fix 后彻底消除） |
| MON-016/022 | 🟠 | 非加密 symbol 泄入 pipeline（NULL 48条/48h） | ⏳ `research_worker.py:_is_valid_symbol()` |
| MON-017 | 🟠 | `incomplete_entry_plan` LIT/USDT（`trading_decision.py:518`） | ⏳ 高敏感路径 |
| MON-018 | ✅ | 系统停机 79min | 已恢复 |
| MON-019/023 | ✅ | HYPE+LIT/USDT 无监控持仓 | **系统恢复 Trading 后已正常监控** |
| MON-020 | 🟠 | mean_reversion 全量 0% 胜率 | ⏳ 待量化分析 |
| MON-021 | 🟡 | direction_overheated 41 次，Operator 无拒绝记忆 | ⏳ |
| MON-024 | ✅ | market_data_stale | 已自愈 |
| MON-025 | 🟠 | 双心跳写入（supervisor + Operator 竞争） | ⏳ 仍持续 |
| MON-026 | 🟠 | Degraded 瞬间触发（1 秒恢复） | ⏳ 暂未再现 |
| MON-027 | 🟡 | Observer 产出不足（恢复中） | ⏳ 今日 24 条，正在恢复 |
| MON-028 | ✅ | post_trade_reviewer 失控循环（965×/24h） | **已修复（01:55Z），Trading 恢复确认** |
| **MON-029** | **🟡 NEW** | **market_observer 86/118 errors（全为 startup-stale artifact，非真实错误）** | ✅ 无实际影响，重启引起 |

---

## 第 9 次观测增量（2026-07-14T~03:21Z，标准 7 项）

> 数据窗口：48h（2026-07-12T03:21Z ～ 2026-07-14T03:21Z）
> 系统状态：Reviewing / Auto，is_busy=0，活跃中（最新 heartbeat 03:20:30Z）

### §1 Opportunity 产出（48h）

| 窗口 | 数量 |
|---|---|
| 最近 24h | 1,316 |
| 最近 48h | 2,372 |
| 全量累计 | 26,578 |

**Scanner 分布（48h）：**

| scanner_family | count | 占比 |
|---|---|---|
| momentum | 1,206 | 50.8% |
| mean_reversion | 468 | 19.7% |
| short_bias | 410 | 17.3% |
| volatility | 231 | 9.7% |
| NULL | **48** | **2.0%** |
| event | 9 | 0.4% |

- 最新 opportunity：03:15:09Z ✅（系统持续产出）
- NULL 48 条（2.0%）：MON-016/022 持续，`research_worker.py:_is_valid_symbol()` 未修复

---

### §2 Filter 各层通过率（48h）

| 阶段 | passed | rejected | 通过率 |
|---|---|---|---|
| observer_pre_filter | 1,967 | 2 | 99.9% |
| universe_allocation | 2,579 | 1,990 | 56.4% |
| paper_ranker | 8,314 | 2,026 | 80.4% |
| paper_trader_selection | 329 | 893 | 26.9% |
| proposal_submission | 40 | **41** | **49.4%** |
| system_operator | 122 | — | — |
| risk | 3 | **17** | **15.0%** |

**拒绝原因明细（Top）：**

| 阶段 | reason_code | 次数 |
|---|---|---|
| paper_ranker | confidence_below | 1,523 |
| paper_ranker | universe_member_expired | 342 |
| paper_trader_selection | duplicate_symbol | 407 |
| paper_trader_selection | signal_cooldown | 371 |
| proposal_submission | direction_overheated | **41** |
| risk | risk_rejected | **17** |
| universe_allocation | same_pool_control_not_selected | 1,990 |

- **proposal_submission 50% 拒绝率**：全为 `direction_overheated`（MON-021，Operator 对同 symbol 反复提案，无拒绝记忆）
- **risk 85% 拒绝率**：分项见 §4

---

### §3 System Operator 决策分布（48h）

| action | count |
|---|---|
| skip | 476 |
| trade | 40 |
| signal_pending | 25（活跃 prune 后留存） |
| scale_in | 21 |
| watch | 17 |

**最新 pi_decision：** 03:13:37Z（skip）✅

**今日（07-14）小时分布：**

| 小时 | trades | skips | signal_pending | scale_in |
|---|---|---|---|---|
| 00:xx | — | 9 | — | 2 |
| 01:xx | 1 | 22 | 4 | — |
| 02:xx | 1 | 35 | 14 | — |
| 03:xx（截至 03:13Z） | — | 25 | 7 | — |

`signal_pending` 今日累积中（01-03Z 各 4/14/7 条），每小时产生 4-14 条；prune 清理 30 分钟前的旧行，当前表内留存 25 条（均为最近 30 分钟内）。

**系统状态：Reviewing / Auto**，最新 intent：`"4 queued operator tasks are waiting in backlog review."`
Operator 正在消化历史积压任务（`evaluate_strategy_state` 等），不主动发起新 trade 决策。

---

### §4 Proposal 和 Risk approve/reject（48h）

| 类型 | 数量 |
|---|---|
| trade_proposals executed | 3 |
| trade_proposals rejected | 17 |
| risk_decisions approved | **3（15%）** |
| risk_decisions rejected | **17（85%）** |

**risk 拒绝原因分项：**

| violations | 次数 | 时间范围 | 状态 |
|---|---|---|---|
| `account_loss_unavailable:legacy_unverified_close` | 9 | 全为 07-13T06:58Z 前 | ✅ fix 后零新增 |
| `symbol_already_open:HYPE/USDT` | 4 | 07-13 | 正常 scale_in 拦截 |
| `incomplete_entry_plan:stop_loss+take_profit` | 2 | 07-13 | MON-017 |
| `symbol_already_open:BTC/USDT` | 1 | 07-12T03:03Z | 孤儿订单 MON-002 |
| `symbol_already_open:BTC/USDT` + `sector_concentration:momentum:100%>60%` | 1 | **07-12T03:03Z** | 历史遗留，非新增 |

**关键确认：** 复合违规 `BTC+sector_concentration` 时间戳为 07-12T03:03Z，早于所有 fix，**非今日新增** ✅。目前 3 笔 open positions（HYPE/LIT/PI）均为 momentum 方向，portfolio momentum 占比 100%，任何新 momentum 提案仍会触发 `sector_concentration`，但这是**正确的 portfolio 防护行为**（非 bug）。

---

### §5 Order / Fill / Position 完整性

**48h Orders（只有 filled，全 paper_internal）：** 3 笔，oldest 07-13T14:26Z，newest 07-14T02:05Z ✅

**孤儿订单（>1 天未变化）：9 笔（与历史一致，无新增）**

| symbol | status | mode | 悬挂时长 |
|---|---|---|---|
| BTC/USDT | pending | bitget_demo | ~23 天 |
| ETH/USDT | pending | okx_demo | ~16 天 |
| NEAR/USDT | pending | okx_demo | ~13 天 |
| XLM/USDT | submitted | okx_demo | ~10 天 |
| MON/USDT × 5 | submitted | okx_demo | ~8 天 |

**Open positions（03:21Z）：**

| symbol | direction | age | entry_price | unrealized_pnl | 变化（较上次） |
|---|---|---|---|---|---|
| HYPE/USDT | short | 12.9h | 64.0050 | **+$1.67** | ↑ +$0.77 |
| LIT/USDT | short | 8.8h | 2.3572 | **-$1.24** | ↑ +$1.34 |
| PI/USDT | short | 1.3h | 0.07284 | **+$1.79** | ↑ +$1.84 |

**观察：**
- 3 笔 open positions 全部处于正常监控中（Auto 模式，Operator 可自动管理） ✅
- PI/USDT 1.3h 已达 +$1.79，较入场快速盈利；HYPE 持续获利；LIT 亏损大幅收窄（-$2.58 → -$1.24）
- MON-019/023：HYPE+LIT 无监控状态已通过系统恢复解除 ✅

---

### §6 Position 关闭质量（48h）

| close_category | outcome_eligible | count |
|---|---|---|
| strategy | 1 | 1 |

**关闭明细：**

| symbol | close_category | close_reason | eligible | exit_fill_verified | exit_price_source | closed_at |
|---|---|---|---|---|---|---|
| BTC/USDT | strategy | stop_loss | ✅ 1 | ✅ 1 | local_fill | 07-13T03:56Z |

仅 1 笔 48h 关闭，`outcome_eligible=1`、`exit_fill_verified=1`、`exit_price_source=local_fill` ✅
MON-001（`close_position()` outcome 字段）持续生效。

---

### §7 PnL 与 scanner attribution（48h）

| scanner_family | 笔数 | total_pnl | avg_pnl | wins | losses |
|---|---|---|---|---|---|
| momentum | 1 | -2.6131 | -2.6131 | 0 | 1 |

**全量历史：**

| scanner_family | 笔数 | total_pnl | avg_pnl | wins | losses |
|---|---|---|---|---|---|
| mean_reversion | 26 | -2.634 | -0.1013 | **0** | **26** |
| momentum | 19 | -2.5139 | -0.1323 | 5 | 14 |
| short_bias | 1 | -0.0131 | -0.0131 | 0 | 1 |
| NULL/no_opp | 206 | -50.1999 | -0.2437 | 83 | 123 |

**异常持续：** mean_reversion 全量 0% 胜率（26 笔）；48h 仅 1 笔关闭样本不足。

---

### 本轮新增观察

#### ✅ post_trade_reviewer 失控循环确认平息

pre-fix 峰值 93/h → 修复后（01:55Z）快速下降：

| 小时 | 运行次数 |
|---|---|
| 07-14T01:xx | 45（含修复前 01:55Z 之前的遗留） |
| 07-14T02:xx | 8 |
| 07-14T03:xx（截至 03:21Z） | 4 |

02:xx=8/h 和 03:xx ≈ 12/h 均已接近正常（正常 0.25/h，但系统仍在消化积压 post-trade-review 请求，故略高）。
**MON-028 修复效果确认：循环已平息** ✅

#### ⚠️ Operator 处于 Reviewing（非 Trading），延迟新交易决策

最新 intent：`"4 queued operator tasks are waiting in backlog review."`
心跳交替显示 Working/Reviewing，说明 Operator 正在处理历史积压队列而非主动寻找新交易机会。
今日（03:xx）pi_decisions 全为 skip，无新 trade 决策。
预计积压清空后 Operator 将切回 Trading 模式。

---

### 问题汇总（截至第 9 次观测，2026-07-14T03:21Z）

| 编号 | 严重度 | 描述 | 状态 |
|---|---|---|---|
| MON-001 | ✅ | `close_position()` outcome 字段 | 已修复，持续生效 |
| MON-002 | 🔴 | 3 笔 pending 孤儿订单（BTC/ETH/NEAR，13-23 天） | ⏳ BTC 孤儿持续阻断 risk |
| MON-003 | 🔴 | 所有 scanner 历史 PnL 为负 | — 策略层问题 |
| MON-006 | 🟠 | opportunity_id=NULL | ⏳ |
| MON-007 | ✅ | signal_pending 积压 | 已解决，prune 运行正常 |
| MON-009 | 🔴 | 6 笔 okx_demo submitted 孤儿订单 | ⏳ |
| MON-016/022 | 🟠 | 非加密 symbol 泄入 pipeline（NULL 48/48h） | ⏳ `research_worker.py:_is_valid_symbol()` |
| MON-017 | 🟠 | `incomplete_entry_plan` LIT/USDT（`trading_decision.py:518`） | ⏳ |
| MON-019/023 | ✅ | HYPE+LIT 无监控持仓 | 已恢复，正常监控 |
| MON-020 | 🟠 | mean_reversion 全量 0% 胜率 | ⏳ |
| MON-021 | 🟡 | direction_overheated 41 次，Operator 无拒绝记忆 | ⏳ |
| MON-025 | 🟠 | 双心跳写入（supervisor + Operator 竞争） | ⏳ |
| MON-028 | ✅ | post_trade_reviewer 失控循环 | **已确认平息（02-03:xx 恢复正常率）** |
| MON-029 | ✅ | market_observer errors（startup-stale artifact） | 无实际影响 |

---

## 第 10 次观测增量（2026-07-14T~04:15Z，标准 7 项）

> 数据窗口：48h（2026-07-12T04:15Z ～ 2026-07-14T04:15Z）
> 系统状态：Reviewing / Auto，is_busy=1，活跃（最新 heartbeat 04:15:30Z）

### §1 Opportunity 产出（48h）

| 窗口 | 数量 |
|---|---|
| 最近 24h | 1,371 |
| 最近 48h | 2,427 |
| 全量累计 | 26,633 |

**Scanner 分布（48h）：**

| scanner_family | count | 占比 |
|---|---|---|
| momentum | 1,221 | 50.3% |
| mean_reversion | 476 | 19.6% |
| short_bias | 438 | 18.1% |
| volatility | 233 | 9.6% |
| NULL | **48** | **2.0%** |
| event | 11 | 0.5% |

最新 opportunity：04:14:59Z ✅；NULL 48 条（MON-016/022 持续）。

---

### §2 Filter 各层通过率（48h）

| 阶段 | passed | rejected | 通过率 |
|---|---|---|---|
| observer_pre_filter | 2,028 | 2 | 99.9% |
| universe_allocation | 2,642 | 2,040 | 56.4% |
| paper_ranker | 8,697 | 2,045 | 80.9% |
| paper_trader_selection | 345 | 960 | 26.4% |
| proposal_submission | 40 | **45** | **47.0%** |
| system_operator | 130 | — | — |
| risk | 3 | **17** | **15.0%** |

`direction_overheated` 45 次（较 §9 +4），全部来自 proposal_submission 层（MON-021 持续）。
过去 3 小时内 direction_overheated 无新增（03:00Z 后 filter_decisions 中无相关记录），说明 +4 来自较早时段。

---

### §3 System Operator 决策分布（48h）

| action | count |
|---|---|
| skip | 488 |
| trade | 44 |
| signal_pending | 44（活跃 prune 后留存） |
| scale_in | 21 |
| watch | 17 |

最新 pi_decision：04:04:28Z（skip）✅

**今日小时分布（07-14）：**

| 小时 | trade | skip | signal_pending | scale_in |
|---|---|---|---|---|
| 00:xx | — | 8 | — | 2 |
| 01:xx | 1 | 8 | 4 | — |
| 02:xx | 1 | 17 | 14 | — |
| 03:xx | 4 | 16 | **23** | — |
| 04:xx（截至 04:04Z） | — | 2 | 3 | — |

**⚠️ signal_pending 03:xx 突增至 23 条（较 02:xx +9）**，活跃留存 44 条（30 分钟内）。
Operator 当前处于 Reviewing 模式（backlog 清理），未主动发起 trade 决策。

**今日 trade 分布：**
- 03:xx 出现 4 笔 trade 决策（02:00Z PI/USDT 入场后首次大批量 trade）
- 整体 trade 率正常（今日 6 笔 trade 决策）

---

### §4 Proposal 和 Risk（48h）

与 §9 完全相同：3 executed / 17 rejected；risk 3 approved（15%）/ 17 rejected（85%）。
各 violation 类型无新增。

---

### §5 Order / Fill / Position 完整性

**48h Fills：** 3 笔（全 paper_internal），最新 02:05Z（PI/USDT 入场）✅

**孤儿订单：** 9 笔（同历史，无新增）

**Open positions（04:15Z）：**

| symbol | direction | age | entry_price | unrealized_pnl | 较 §9（03:21Z）变化 |
|---|---|---|---|---|---|
| HYPE/USDT | short | 13.8h | 64.0050 | **+$0.94** | ↓ -$0.73 |
| LIT/USDT | short | 9.7h | 2.3572 | **-$1.07** | ↑ +$0.17 |
| PI/USDT | short | 2.2h | 0.07284 | **+$0.22** | ↓ **-$1.57** |

**⚠️ PI/USDT 在约 1 小时内 unrealized PnL 从 +$1.79 大幅回落至 +$0.22（-$1.57 变化）**，短期价格逆向走势。仍为盈利但回撤明显。HYPE 也小幅回落（-$0.73）。LIT 亏损持续收窄。

---

### §6 Position 关闭质量（48h）

与 §9 完全相同：1 笔关闭（BTC/USDT strategy stop_loss，eligible=1）✅

---

### §7 PnL 与 scanner attribution

与 §9 完全相同：48h 1 笔 momentum -2.61；全量数据无变化（无新关闭）。

---

### 本轮新增异常

#### 🟠 MON-030 NEW｜market_observer `loop_budget_exceeded`（每 10 分钟一次，已连续 3 次）

**现象（03:22Z ～ 03:42Z）：**

| 运行时间 | status | 内容 |
|---|---|---|
| 03:22Z | completed_with_errors | `loop_budget_exceeded: processed 12/14, skipped 2 (200s < 240s)` |
| 03:32Z | completed_with_errors | `loop_budget_exceeded: processed 12/15, skipped 3 (200s < 240s)` |
| 03:42Z | completed_with_errors | `loop_budget_exceeded: processed 12/13, skipped 1 (200s < 240s)` |

每次运行处理 12 个标的后触发 200s 墙钟上限，剩余 1-3 个标的被跳过。

**根因：**
`aletheia/agents/market_observer.py:42-43`：
```python
_MARKET_OBSERVER_JOB_TIMEOUT_S = 240.0
_MARKET_OBSERVER_LOOP_BUDGET_SEC = 200.0
```
`market_observer.py:855-871`：循环在 `_time.monotonic() > _loop_deadline`（200s）时 break，写入 `loop_budget_exceeded` error。

**设计意图（代码注释 L843-850）：** 这是一个**已知的 gateway 稳定性防护**——当 universe 过大时，LLM 调用累积时间超过 240s job timeout，导致 coroutine 被 `_run_async_with_timeout` 取消，SelectorEventLoop teardown 阻塞，daemon thread 泄漏（"abandoned threads 3→5"）。200s 上限确保 coroutine 正常返回，被跳过的标的在下一个 15min 周期补扫。

**当前影响：** universe 现有 13-15 个标的，每个标的约需 16-17s（LLM 结构化调用）。每次扫描跳过 1-3 个标的，意味着这些标的 15-30 分钟内无新 opportunity 产出。尚未导致交易质量下降，但随 universe 扩大会更显著。

**潜在问题（非紧急）：** 若 universe 继续增长到 16+ 个标的，12 个处理量不变，跳过比例上升。设计上下一周期会补扫，但若某个标的连续被跳过（总在末位），则可能存在结构性缺口。

---

#### 🟡 MON-031 NEW｜Operator "1 failed task" 短暂状态（03:45Z，已自愈）

**现象（心跳序列）：**
```
03:45:00Z  Reviewing/Auto is_busy=0  "1 failed operator task requires review..."
03:45:10Z  Reviewing/Auto is_busy=1  "1 failed operator task requires review..."
...（连续处理 ~45 秒）...
03:45:49Z  Working/Auto   is_busy=0  （任务已处理完毕）
03:45:55Z  Reviewing/Auto is_busy=0  "4 queued operator tasks are waiting in backlog review."
```

Operator 在 03:45Z 检测到 1 个失败任务，切换到 Reviewing 模式主动处理，45 秒后（03:45:55Z）任务清除，回到"4 backlog 队列"状态。

**与 market_observer 的关系：** 最可能的触发源是 03:42Z market_observer 返回 `loop_budget_exceeded` error（status=`completed_with_errors`）。Operator 将此视为"任务失败"，触发 review 流程，评估后判定为非严重错误（可继续），于 03:45:55Z 切回常规 backlog 处理。

**第三次 gateway restart（03:52Z）：** `agent_runs` 中 03:52Z 有一条 market_observer `stale`（"marked stale by startup"），说明 gateway 在 03:52Z 附近又进行了一次重启。原因不明（可能是 Operator 在处理失败任务后触发了 self-heal，或 supervisor 触发）。心跳记录连续，说明重启极快（<10 秒）。

---

### RC-030 ｜ market_observer loop_budget_exceeded 根因

**代码路径：**
- `aletheia/agents/market_observer.py:42-43` — 常量定义：`JOB_TIMEOUT=240s`，`LOOP_BUDGET=200s`
- `aletheia/agents/market_observer.py:855` — `_loop_deadline = _time.monotonic() + 200`
- `aletheia/agents/market_observer.py:858-871` — for 循环检查 deadline，触发时 break 并写入 error
- `aletheia/agents/market_observer.py:843-850` — 注释记录了设计意图（防 thread leak）

**触发条件：** universe ≥ 14 symbols 且每个 LLM 调用平均 ≥ 200/12 ≈ 16.7s → 第 13 个开始时超过 200s deadline。

---

### 问题汇总（截至第 10 次观测，2026-07-14T04:15Z）

| 编号 | 严重度 | 描述 | 状态 |
|---|---|---|---|
| MON-002 | 🔴 | 3 笔 pending 孤儿订单（BTC/ETH/NEAR） | ⏳ |
| MON-003 | 🔴 | 所有 scanner 历史 PnL 为负 | — 策略层问题 |
| MON-006 | 🟠 | opportunity_id=NULL | ⏳ |
| MON-007 | ✅ | signal_pending 积压 | 已解决 |
| MON-009 | 🔴 | 6 笔 okx_demo submitted 孤儿订单 | ⏳ |
| MON-016/022 | 🟠 | 非加密 symbol 泄入 pipeline | ⏳ |
| MON-017 | 🟠 | incomplete_entry_plan LIT/USDT | ⏳ |
| MON-020 | 🟠 | mean_reversion 全量 0% 胜率 | ⏳ |
| MON-021 | 🟡 | direction_overheated 45 次（+4），Operator 无拒绝记忆 | ⏳ |
| MON-025 | 🟠 | 双心跳写入（supervisor + Operator 竞争） | ⏳ |
| MON-028 | ✅ | post_trade_reviewer 失控循环 | 已确认平息 |
| **MON-030** | **🟠 NEW** | **market_observer loop_budget_exceeded：universe 14-15 symbols，每次跳过 1-3 个，每 10 分钟一次** | ⏳ 设计内但宽松覆盖缺口 |
| **MON-031** | **🟡 NEW** | **Operator "1 failed task" 03:45Z（45 秒内自愈），第三次 gateway restart ~03:52Z** | ✅ 已自愈 |

---

## 第 11 次观测增量（2026-07-14T~05:20Z，标准 7 项）

> 数据窗口：48h（2026-07-12T05:20Z ～ 2026-07-14T05:20Z）
> 系统状态：Reviewing / Auto，is_busy=1（backlog 处理中），最新 heartbeat 05:15:27Z

### §1 Opportunity 产出（48h）

| 窗口 | 数量 |
|---|---|
| 最近 24h | 1,433 |
| 最近 48h | 2,489 |
| 全量累计 | 26,695 |

**Scanner 分布（48h）：**

| scanner_family | count | 占比 |
|---|---|---|
| momentum | 1,236 | 49.7% |
| mean_reversion | 485 | 19.5% |
| short_bias | 471 | 18.9% |
| volatility | 236 | 9.5% |
| NULL | **48** | **1.9%** |
| event | 13 | 0.5% |

最新 opportunity：05:09:09Z ✅

---

### §2 Filter 各层通过率（48h）

| 阶段 | passed | rejected | 通过率 |
|---|---|---|---|
| observer_pre_filter | 2,082 | 2 | 99.9% |
| universe_allocation | 2,721 | 2,090 | 56.5% |
| paper_ranker | 9,120 | 2,090 | 81.4% |
| paper_trader_selection | 375 | 1,043 | 26.4% |
| proposal_submission | 42 | **53** | **44.2%** |
| system_operator | 148 | — | — |
| risk | 3 | **18** | **14.3%** |

`direction_overheated`：53 次（较 §10 +8）。**最近 3 小时内 direction_overheated 零新增**——说明系统在 04:17Z 后未再生成进入 proposal_submission 层的新提案（见 §4 leverage 阻断）。

---

### §3 System Operator 决策分布（48h）

| action | count | 较§10 |
|---|---|---|
| skip | 498 | +10 |
| signal_pending | 74（prune 后留存） | +30 |
| trade | 52 | **+8** |
| scale_in | 22 | +1 |
| watch | 17 | — |

最新 pi_decision：05:15:11Z（active ✅）

**今日小时分布（活跃期）：**

| 小时 | trade | skip | signal_pending | scale_in |
|---|---|---|---|---|
| 03:xx | 4 | 16 | 23 | — |
| 04:xx | **6** | 9 | 24 | — |
| 05:xx（截至 05:15Z） | 2 | 3 | 9 | 1 |

04:xx 出现 6 笔 trade 决策——今日最高单小时 trade 率。05:xx 前 15 分钟已有 2 trade + 1 scale_in，系统交易活跃。

**signal_pending 74（30 分钟活跃留存）**：较 §10（44）增长，与 trade 活跃度正相关。prune 在运行，无积压 ✅

---

### §4 Proposal 和 Risk（48h）

| 类型 | 数量 | 较§10 |
|---|---|---|
| trade_proposals executed | 3 | — |
| trade_proposals rejected | **18** | **+1** |
| risk_decisions approved | 3（14.3%） | — |
| risk_decisions rejected | **18（85.7%）** | +1 |

**新增 risk 拒绝（04:17:48Z）：**

| violations | 说明 |
|---|---|
| `exceeds_demo_position_notional:99.99999999286>6.74814873` | 提案 $100 notional > 可用余量 $6.75 |
| `account_leverage_ratio:3.98x>3.0x` | 账户杠杆已达 3.98x，超过 3.0x 上限 |

触发标的：**XLM/USDT short**（04:17Z）

**账户杠杆分析：** 3 笔 open positions（HYPE ~$75 notional + LIT ~$108 notional + PI 小额）使账户杠杆达 3.98x。当前无新 position 可被批准，直至现有 position 关闭释放 margin。`$6.74814873` 为剩余可用 margin，动态计算值。

**这是正确的风险管理行为，非 bug。** 但表明系统当前处于"杠杆满仓"状态，Operator 虽然继续生成 trade 决策，但所有新提案都会被 risk 层在 leverage check 阻断。

---

### §5 Order / Fill / Position 完整性

**48h Fills：** 3 笔，最新 02:05Z（PI/USDT）✅；孤儿订单 9 笔（同历史，无新增）

**Open positions（05:20Z）：**

| symbol | direction | age | entry_price | unrealized_pnl | 较§10（04:15Z）变化 |
|---|---|---|---|---|---|
| HYPE/USDT | short | 14.8h | 64.0050 | **+$0.29** | ↓ -$0.65 |
| LIT/USDT | short | 10.7h | 2.3572 | **-$0.36** | ↑ **+$0.71** |
| PI/USDT | short | 3.2h | 0.07284 | **+$1.77** | ↑ +$1.55 |

**总组合 unrealized：** +$1.70（HYPE 持续减少，LIT 明显改善，PI 强势回升）

**⚠️ HYPE/USDT 盈利持续收窄：** 从峰值 +$1.67（03:21Z）→ +$0.94（04:15Z）→ +$0.29（05:20Z），2 小时内下降 $1.38，需关注是否接近止损线（stop_price 未知）。

---

### §6 Position 关闭质量（48h）

与历次观测相同：1 笔（BTC/USDT strategy stop_loss，eligible=1）✅

---

### §7 PnL 与 scanner attribution

与历次相同（无新关闭）。

---

### 本轮新增异常

#### 🔴 MON-032 NEW｜账户杠杆 3.98x 超限，所有新提案被 risk 层阻断

**现象：** 04:17Z，XLM/USDT short 提案被 risk engine 拒绝，violations：
1. `account_leverage_ratio:3.98x>3.0x`：账户当前杠杆超出 3.0x 上限
2. `exceeds_demo_position_notional:99.99>6.75`：可用余量仅 $6.75，不足以支撑 $100 新仓

**后续影响：** 04:17Z 后 direction_overheated 零新增，这不是因为系统停止决策（05:xx 仍有 trade 决策），而是因为新提案在 **risk 层被拒（account_leverage_ratio）而非到达 proposal_submission 层**——risk 检查在 proposal_submission 之后，所以 direction_overheated 计数不再增加。Operator 继续 trade 决策但提案无法通过 risk。

**根因：** 无代码 bug。HYPE + LIT + PI 三仓合计 notional ≈ $183（HYPE ~$75 + LIT ~$108），账户 equity 不足以支撑更多仓位。需等待现有 position 关闭后 leverage 下降。

**与 MON-002 的关系：** BTC/USDT 孤儿 pending 订单（bitget_demo）在 `symbol_already_open` 检查中被计入，导致额外的隐性 notional 占用。

---

#### 🔴 MON-030 升级｜market_observer universe 增至 16，每次跳过 4 个，且出现 11/15 模式

**universe 大小趋势（今日）：**

| 时间 | 处理量 | 总量 | 跳过 |
|---|---|---|---|
| 03:22Z | 12 | 14 | 2 |
| 03:32Z | 12 | 15 | 3 |
| 03:42Z | 12 | 13 | 1 |
| 04:23Z | 12 | **16** | **4** |
| 04:45Z | **11** | 15 | **4** |
| 04:55Z | **11** | 15 | **4** |
| 05:05Z | 12 | 14 | 2 |

关键恶化：
1. **04:23Z universe 增至 16**，触发 4 个跳过（历史最高）
2. **04:45Z、04:55Z 仅处理 11 个**（而非 12 个），说明每个 LLM call 平均时长已从 ~16-17s 升至 ~18s，对应 200s / 11 ≈ 18s，budget 边界更紧张
3. **今日 gateway 累计 4 次 stale 事件**（03:52Z、04:40Z + 2 次更早）——多次重启表明 market_observer 的不稳定性在加剧

**2h 内 market_observer 运行状态分布：**

| status | count |
|---|---|
| completed | 14 |
| completed_with_errors | 7 |
| stale | 4 |
| timed_out | 3 |
| running | 1 |
| **total** | **29** |

29 次/2h（期望 12 次/2h），说明多次重启后补跑造成密集调度。3 次 timed_out 是新增（本轮才出现在 2h 窗口内），意味着有 3 次 market_observer 运行超过了 240s job timeout——说明 loop_budget_exceeded 保护在某些情况下未能有效阻止超时（可能是 LLM 单次调用本身超时，而非整体 budget 超限）。

---

### 问题汇总（截至第 11 次观测，2026-07-14T05:20Z）

| 编号 | 严重度 | 描述 | 状态 |
|---|---|---|---|
| MON-002 | 🔴 | 3 笔 pending 孤儿订单 | ⏳ |
| MON-003 | 🔴 | 所有 scanner 历史 PnL 为负 | — 策略层问题 |
| MON-006 | 🟠 | opportunity_id=NULL | ⏳ |
| MON-007 | ✅ | signal_pending 积压 | 已解决 |
| MON-009 | 🔴 | 6 笔 okx_demo submitted 孤儿订单 | ⏳ |
| MON-016/022 | 🟠 | 非加密 symbol 泄入 pipeline | ⏳ |
| MON-017 | 🟠 | incomplete_entry_plan | ⏳ |
| MON-020 | 🟠 | mean_reversion 全量 0% 胜率 | ⏳ |
| MON-021 | 🟡 | direction_overheated 53 次（leverage 阻断后已停增） | ⏳ |
| MON-025 | 🟠 | 双心跳写入 | ⏳ |
| MON-028 | ✅ | post_trade_reviewer 失控循环 | 已确认平息 |
| **MON-030** | **🔴** | **market_observer loop_budget_exceeded：universe 增至 16，每次跳 4，11/15 模式出现，4 次 stale + 3 次 timed_out 在 2h 内** | ⚠️ 升级，建议提高 timeout 配置 |
| MON-031 | ✅ | Operator "1 failed task" 03:45Z | 已自愈 |
| **MON-032** | **🔴 NEW** | **账户杠杆 3.98x>3.0x：HYPE+LIT+PI 三仓满仓，所有新提案被 risk 阻断（XLM/USDT 04:17Z）** | ⚡ 等待现有 position 关闭释放 margin |

---

## 第 12 次观测增量（2026-07-14T~06:20Z，标准 7 项）

### §1 Opportunity 产出

| 窗口 | 数量 |
|---|---|
| 最近 24h | **1,127** |
| 全量累计 | 26,768 |

**Scanner 分布（24h）：**

| scanner_family | count | 占比 |
|---|---|---|
| momentum | 376 | 33.4% |
| **short_bias** | **364** | **32.3%** |
| mean_reversion | 244 | 21.6% |
| volatility | 84 | 7.5% |
| NULL | 48 | 4.3% |
| event | 11 | 1.0% |

**新增异常**：short_bias（364）已超越 mean_reversion（244）升至第 2 位，与 momentum（376）差距仅 12 条。短偏策略主导信号流，而当前 3 笔持仓全部为 short 方向，方向热度（direction_overheated）压力可能上升。

---

### §2 提案漏斗（24h）

| stage | pass | reject | 备注 |
|---|---|---|---|
| observer_pre_filter | 972 | 3 | — |
| universe_allocation | 1,377 | 970 | — |
| paper_ranker | 5,382 | 786 | — |
| paper_trader_selection | 293 | 819 | — |
| **proposal_submission** | **38** | **70** | direction_overheated 主因（vs §11: 44p/69r） |
| risk | 3 | 16 | leverage 阻断（vs §11: 3p/19r，略有收窄） |
| system_operator | 178 | — | — |
| system_operator_debate | 87 | — | — |

proposal_submission 通过率从 §11 的 44/113=39% 降至 38/108=35%，方向过热压制加重。risk 拒绝从 19 降至 16，因为 06:04Z 后无新 leverage 拒绝（当前杠杆 2.79x，低于 3.0x 上限）。

---

### §3 决策节奏（pi_decisions）

**实时 30min 窗口（~05:47–06:17Z）：**

| action | count |
|---|---|
| signal_pending | 12 |
| trade | 7 |
| scale_in | 1 |

**最新记录**：06:17:24Z

**2h 小时分布：**

| 小时 | signal_pending | trade | skip | scale_in |
|---|---|---|---|---|
| 04xx | 18 | 3 | 7 | — |
| 05xx | 24 | **14** | 5 | 1 |
| 06xx（截至06:17Z） | 10 | 5 | — | 1 |

05xx 的 14 笔 trade 为 2h 内最高时段，与 market_observer 在 05xx 多次完整覆盖 13-16 个标的相符。

---

### §4 Risk 决策（24h）

| 类别 | 数量 |
|---|---|
| approved | 3 |
| rejected | **16** |

**最新拒绝（06:04:28Z）：**
```
["symbol_already_open:PI/USDT", "account_leverage_ratio:4.21x>3.0x"]
```

**MON-032 根因确认（本轮新发现）：**

`_account_equity()` 函数（`aletheia/risk/engine.py:948-961`）的权益分母来源：
1. 优先读 `exchange_sync.get_cached_state()` → `equity_usdt`（stale Bitget 余额，本轮约 167 USDT）
2. 降级回退 `cfg.risk.starting_capital_usdt = 88,000 USDT`

关键问题：**paper account 的现金余额（`paper_account_events` 表，目前 $398.25）从未被风控引擎读取**。`adjust_cash(+200)` 只写 `paper_account_events`，不更新 `exchange_sync` 缓存。

06:04Z 时 weighted_notional ≈ PI(100.16×1) + LIT(108.42×2) + HYPE(74.88×2) + 新提案PI(100.16×1) ≈ 566.9 USDT，equity ≈ 566.9 / 4.21 ≈ **134.7 USDT**（当时 exchange_sync 缓存值）。

**当前杠杆**（06:20Z live check）：PI(100.16) + LIT(216.83) + HYPE(149.77) = 466.76 / 167.37 equity = **2.79x**（低于 3.0x 上限）。新提案只要 notional > (3.0 × 167.37 - 466.76) = **35.4 USDT** 即会再次触发 leverage 拒绝。大多数提案 notional 在 $100+ 范围，**下一笔新仓提案必然被拒**。

修复方向：`aletheia/risk/engine.py:948-961` `_account_equity()` 在 `is_live=False` 时应优先读 `paper_account.get_current_balance()` 而非 exchange_sync 缓存。

---

### §5 持仓状态

| symbol | 方向 | 未实现 PnL | 持仓时长 | 趋势 |
|---|---|---|---|---|
| HYPE/USDT | short | **+$0.37** | 16.0h | 改善（§11: +$0.28） |
| LIT/USDT | short | **-$0.22** | 11.8h | 改善（§11: -$0.47） |
| PI/USDT | short | **-$0.50** | 4.3h | 恶化（§11: -$0.21） |

三仓合计未实现 PnL：**-$0.35**（vs §11 -$0.40，整体略有改善）

**孤儿订单（无变化）：**
- pending: BTC/USDT ×1, ETH/USDT ×1, NEAR/USDT ×1
- submitted: MON/USDT ×5, XLM/USDT ×1
- **合计 9 笔**（与 §11 相同）

---

### §6 近期关仓

24h 内：**无关仓**（BTC/USDT stop_loss 关仓发生在 >24h 前，已滑出窗口）

---

### §7 Agent 健康（2h）

| agent | runs | error_runs | timeouts | 最新 |
|---|---|---|---|---|
| position_reconciler | 24 | 0 | 0 | 06:20Z |
| paper_trader | 19 | 0 | 0 | 06:16Z |
| **market_observer** | **14** | **10** | **0** | 06:21Z |
| risk_critic | 4 | 0 | 0 | 05:36Z |
| portfolio_agent | 2 | 0 | 0 | 04:40Z |

**MON-030 market_observer 趋势（本轮新数据）：**

| 时间 | 处理 | 总量 | 状态 |
|---|---|---|---|
| 04:23Z | 12 | 16 | loop_budget_exceeded |
| 04:45Z | 11 | 15 | loop_budget_exceeded |
| 04:55Z | 11 | 15 | loop_budget_exceeded |
| 05:05Z | 12 | 14 | loop_budget_exceeded |
| 05:15Z | 12 | **16** | loop_budget_exceeded |
| 05:25Z | 12 | 13 | loop_budget_exceeded |
| 05:35Z | 12 | 14 | loop_budget_exceeded |
| 05:42Z | 11 | 13 | loop_budget_exceeded |
| 06:02Z | — | — | **completed ✅** |
| 06:12Z | — | — | **completed ✅** |
| 06:21Z | — | — | running（当前） |

**改善信号**：06:02Z 和 06:12Z 连续 2 次完整完成，无 loop_budget_exceeded。universe 似乎从峰值 16 收缩至较小范围，帮助 2 次运行顺利完成。不过整个 2h 窗口中 10/14 runs 仍有错误，若 universe 再次扩大将复发。

---

### 问题汇总（截至第 12 次观测，2026-07-14T06:20Z）

| 编号 | 严重度 | 描述 | 状态 |
|---|---|---|---|
| MON-002 | 🔴 | 3 笔 pending 孤儿订单 | ⏳ |
| MON-003 | 🔴 | 所有 scanner 历史 PnL 为负 | — 策略层问题 |
| MON-006 | 🟠 | opportunity_id=NULL | ⏳ |
| MON-007 | ✅ | signal_pending 积压 | 已解决 |
| MON-009 | 🔴 | 6 笔 okx_demo submitted 孤儿订单 | ⏳ |
| MON-016/022 | 🟠 | 非加密 symbol 泄入 pipeline | ⏳ |
| MON-017 | 🟠 | incomplete_entry_plan | ⏳ |
| MON-020 | 🟠 | mean_reversion 全量 0% 胜率 | ⏳ |
| MON-021 | 🟡 | direction_overheated 69 次，Operator 无拒绝记忆 | ⏳ |
| MON-025 | 🟠 | 双心跳写入 | ⏳ |
| MON-028 | ✅ | post_trade_reviewer 失控循环 | 已确认平息 |
| **MON-030** | **🔴** | **market_observer loop_budget_exceeded（10/14 runs in 2h）；06:02+06:12 连续完成，暂时缓解** | ⚠️ 升级，建议 timeout 420s/budget 380s |
| MON-031 | ✅ | Operator "1 failed task" | 已自愈 |
| **MON-032** | ✅ | **risk equity 与 paper account 脱节 → 已修复**：`_account_equity()` 改为读 `get_paper_account()["equity_usdt"]`（$396），BASED/USDT 06:51Z 成功开仓，当前杠杆 1.55x | **已修复 + 已部署** |

---

## 第 13 次观测增量（2026-07-14T~07:20Z，标准 7 项）

### §1 Opportunity 产出

| 窗口 | 数量 |
|---|---|
| 最近 24h | 1,093 |
| 最近 48h | 2,338 |
| 全量累计 | 26,780 |

**Scanner 分布（24h）：**

| scanner_family | count | 占比 |
|---|---|---|
| momentum | 371 | 33.9% |
| short_bias | 342 | 31.3% |
| mean_reversion | 238 | 21.8% |
| volatility | 83 | 7.6% |
| NULL | 48 | 4.4% |
| event | 11 | 1.0% |

最新：07:12:46Z ✅

---

### §2 提案漏斗（24h）

| stage | pass | reject | 备注 |
|---|---|---|---|
| **observer_pre_filter** | 913 | **61** | ⚠️ 全部 `no_sufficient_signal`（§12 仅 3 条，20× 跳增） |
| universe_allocation | 1,364 | 980 | — |
| paper_ranker | 5,098 | 772 | — |
| paper_trader_selection | 276 | 765 | — |
| proposal_submission | 30 | 74 | 全部 `direction_overheated` |
| risk | 4 | 11 | 见 §4 |
| system_operator | 178 | — | — |
| system_operator_debate | 87 | — | — |

**新增异常 — observer_pre_filter 61 次 `no_sufficient_signal`：**

在 24h 内从 3 条跳增至 61 条（20×），被拒绝的 symbol 包括：MEGA、CAP、LAB、AGLD、PI、BILL、BASED、CL、KITE、NEAR、TRIA、JTO、EDGE 等——其中 PI/USDT 和 BASED/USDT 均为当前持仓。

**根因分析：**
- 文件：`aletheia/agents/market_observer.py:175-221`
- `_evaluate_pre_filter()` 解析上下文字符串，检查三项条件：RSI 偏离 50 超过 `_PREFILTER_RSI_DEVIATION`、ATR% 超过 `_PREFILTER_ATR_PCT`、keyword hits；三项均未命中 → `no_sufficient_signal`
- `_has_sufficient_signal()` 在 `market_observer.py:215-221` 调用 `_evaluate_pre_filter()`，返回 False 即跳过 LLM 调用
- 61 条的增加说明过去 24h 内大量 symbol 的 RSI 接近 50（横盘状态）+ ATR% 低，属于**市场进入低波动整合期**导致的正常过滤，而非 bug

---

### §3 决策节奏（pi_decisions，48h）

| action | count | 占比 |
|---|---|---|
| skip | 460 | 69.3% |
| signal_pending | 106 | 16.0% |
| trade | 77 | 11.6% |
| scale_in | 21 | 3.2% |
| watch | 2 | 0.3% |

**近 6h 小时分布：**

| 小时 | trade | skip | signal_pending | scale_in |
|---|---|---|---|---|
| 07xx（截至07:13Z） | 3 | 1 | — | — |
| 06xx | 10 | — | 17 | 1 |
| 05xx | 14 | 5 | 24 | 1 |
| 04xx | 6 | 9 | 24 | — |
| 03xx | 4 | 16 | 23 | — |
| 02xx | 1 | 17 | 14 | — |

最新：07:13:34Z ✅

---

### §4 Proposal 与 Risk（24h）

| 类别 | 数量 |
|---|---|
| trade_proposals executed | 4 |
| trade_proposals rejected | 11 |
| risk approved | 4 |
| risk rejected | 11 |

**Risk 拒绝明细（24h）：**

| 时间 | 内容 |
|---|---|
| 07:07:43Z | `["incomplete_entry_plan:stop_loss+take_profit"]` KITE/USDT |
| 07:01:56Z | `["symbol_already_open:BASED/USDT"]` |
| 06:55:12Z | `["symbol_already_open:BASED/USDT"]` |
| 06:04:28Z | `["symbol_already_open:PI/USDT","account_leverage_ratio:4.21x>3.0x"]`（MON-032 修复前） |
| 04:17:48Z | `["exceeds_demo_position_notional","account_leverage_ratio:3.98x>3.0x"]` |

**MON-032 验证**：修复后 06:51Z BASED/USDT 成功开仓（无 leverage 拒绝）。07:01Z 和 06:55Z 的 BASED 拒绝原因是 `symbol_already_open`（仓位已存在，重复提案），属于正常行为。

**MON-017 持续复现 — `incomplete_entry_plan:stop_loss+take_profit`（07:07Z，KITE/USDT）：**

KITE/USDT 提案：`order_type=limit, source=native, stop_price=0.0, take_profit=0.0, risk_reward=1.0`

**根因分析：**
- 文件：`aletheia/risk/engine.py:172-185`（Check #2b）
- `not proposal.stop_price` 对 `0.0` 判定为 True（零值等同缺失），`not proposal.take_profit` 同理
- LLM 生成的提案 stop/take_profit 字段为 0.0 而非实际价格，触发拒绝
- `direction_overheated` 全量：**77 次**（§12 为 69，+8 in ~1h，持续增长）
- Operator 无法记忆被拒绝的方向偏好，每轮重新提交同类型提案

---

### §5 持仓状态

| symbol | 方向 | entry | current | 未实现 PnL | 持仓时长 | 趋势 |
|---|---|---|---|---|---|---|
| HYPE/USDT | short | 64.00 | 63.655 | **+$0.41** | 16.9h | 改善（§12: +$0.37） |
| LIT/USDT | short | 2.3572 | 2.4003 | **-$1.97** | 12.8h | ⚠️ 急剧恶化（§12: -$0.22，1h 内多亏 $1.75） |
| PI/USDT | short | 0.0728 | 0.0732 | **-$0.45** | 5.3h | 恶化（§12: -$0.50，略稳） |
| **BASED/USDT** | **short NEW** | **0.0878** | **0.0880** | **-$0.11** | **0.5h** | 新开仓（MON-032 修复后首笔） |

**四仓合计未实现 PnL：-$2.12**（vs §12 三仓 -$0.35，新增 BASED + LIT 恶化）

**LIT/USDT 急跌警告**：12.8h 内亏损从 -$0.22 扩大至 -$1.97（+$1.75）。entry 2.3572 → current 2.4003（+1.83%），short 方向逆势。2x leverage 放大亏损。

**孤儿订单（无变化）：**
- pending: BTC/USDT, ETH/USDT, NEAR/USDT ×3
- submitted: MON/USDT ×5, XLM/USDT ×1
- **合计 9 笔**

---

### §6 关仓状态与 outcome 完整性

**48h 内关仓：**
- BTC/USDT long stop_loss -$2.61（07-13T03:56Z）— 已滑出近边界

**全量 outcome 分布：**

| close_category | outcome_eligible | exit_fill_verified | count |
|---|---|---|---|
| administrative | 0 | 0 | 125 |
| unknown | 0 | 0 | 116 |
| maintenance | 0 | 0 | 6 |
| **strategy** | **1** | **1** | **5** |

252 笔关仓中只有 5 笔（2.0%）具备 `outcome_eligible=1`，余 247 笔均无法用于学习循环。全部 strategy 关仓只有 5 笔（BTC stop_loss 和近期数笔），其余历史 close 均属于 `administrative`/`unknown`/`maintenance` 类别（MON-006 根因：历史 position 关仓时未填写 outcome 字段）。

---

### §7 PnL 归因（scanner_family，全量已关仓）

| scanner_family | 笔数 | total_pnl | avg_pnl | 胜 | 败 |
|---|---|---|---|---|---|
| mean_reversion | 26 | **-$2.63** | -$0.10 | 0 | 26 |
| momentum | 19 | **-$2.51** | -$0.13 | 5 | 14 |
| short_bias | 1 | -$0.013 | -$0.013 | 0 | 1 |
| NULL | 1 | -$0.004 | -$0.004 | 0 | 1 |

所有 scanner 历史 PnL 均为负（MON-003）。mean_reversion 0% 胜率（26/26 亏损，MON-020）。momentum 26% 胜率（5/19）。

---

### Agent 健康（2h）

| agent | runs | error_runs | timeouts | 最新 |
|---|---|---|---|---|
| position_reconciler | 23 | 0 | 0 | 07:15Z |
| paper_trader | 16 | 0 | 0 | 07:15Z |
| **market_observer** | **13** | **4** | **0** | 07:11Z |
| risk_critic | 4 | 0 | 0 | 05:36Z |
| portfolio_agent | 4 | 0 | 0 | 06:43Z |

**MON-030 实质性缓解**：market_observer 最近 8 次运行（05:42Z → 07:11Z）连续 completed，无 loop_budget_exceeded。4 个 error_runs 均来自 05:42Z 之前（窗口内历史记录）。当前 universe 稳定在 ~10-13 个 symbol/run，fit within 200s budget。

**MON-032 修复效果确认**：
- 07:07Z：leverage check = equity $396 USDT，ratio 1.55x，violation = False
- BASED/USDT 06:51Z 成功开仓（首笔 MON-032 修复后新仓）

---

### 问题汇总（截至第 13 次观测，2026-07-14T07:20Z）

| 编号 | 严重度 | 描述 | 状态 |
|---|---|---|---|
| MON-002 | 🔴 | 3 笔 pending 孤儿订单 | ⏳ |
| MON-003 | 🔴 | 所有 scanner 历史 PnL 为负 | — 策略层 |
| MON-006 | 🟠 | opportunity_id=NULL + outcome 字段缺失（247/252 笔无学习价值） | ⏳ |
| MON-007 | ✅ | signal_pending 积压 | 已解决 |
| MON-009 | 🔴 | 6 笔 okx_demo submitted 孤儿订单 | ⏳ |
| MON-016/022 | 🟠 | 非加密 symbol 泄入 pipeline | ⏳ |
| MON-017 | 🟠 | `incomplete_entry_plan` 复现（KITE 07:07Z），LLM 发出 stop/tp=0.0 的提案 | ⏳ |
| MON-020 | 🟠 | mean_reversion 全量 0% 胜率（26 losses） | ⏳ |
| MON-021 | 🟡 | direction_overheated 77 次（+8 in 1h），Operator 无拒绝记忆 | ⏳ 持续增长 |
| MON-025 | 🟠 | 双心跳写入 | ⏳ |
| MON-028 | ✅ | post_trade_reviewer 失控循环 | 已平息 |
| **MON-030** | **🟡** | **market_observer loop_budget_exceeded 实质缓解：连续 8 次 completed（05:42–07:11Z）** | ✅ 临时缓解，建议提高 timeout |
| MON-031 | ✅ | Operator "1 failed task" | 已自愈 |
| **MON-032** | **✅** | **equity 与 paper account 脱节 → 已修复部署，BASED/USDT 成功开仓，杠杆 1.55x** | **已解决** |
| **MON-033** | **🟠 NEW** | **LIT/USDT short 持仓 1h 内损失扩大 $1.75（-$0.22→-$1.97），当前 -$1.97，2x leverage 放大亏损** | ⚠️ 监控中 |
| **MON-034** | **🟡 NEW** | **observer_pre_filter `no_sufficient_signal` 61 条/24h（§12 仅 3 条）**：市场低波动整合期导致更多 symbol 跳过 LLM，属预期行为但幅度需关注 | ℹ️ 正常但监控 |

---

## 第 14 次观测增量（2026-07-14T~08:20Z，标准 7 项）

### §1 市场信号量

| 窗口 | 数量 |
|---|---|
| 24h | 1,057 |
| 48h | 2,262 |
| 合计 | 26,792 |

最新信号：08:13:01Z。Scanner 分布（24h）：momentum 352、short_bias 338、mean_reversion 228、volatility 80、NULL 48、event 11。

---

### §2 过滤漏斗（24h）

| 阶段 | passed | rejected | 主要 reason_code |
|---|---|---|---|
| observer_pre_filter | 865 | **132** | no_sufficient_signal=132（§13 为 61，+71 in ~1h）|
| universe_allocation | 1,349 | 970 | same_pool_control_not_selected=970（见下方说明）|
| paper_ranker | 4,900 | 734 | confidence_below 411、universe_member_expired 249、equity_shadow_track 48、execution_eligible_false 26 |
| paper_trader_selection | 270 | 732 | duplicate_symbol 339、signal_cooldown 301、signal_batch_limit 61、open_position_duplicate 31 |
| proposal_submission | 30 | **84** | direction_overheated=84（§13 为 74，+10 in ~1h）|
| risk | 4 | 11 | 与 §13 相同，07:07Z 后无新增拒绝 |

**universe_allocation `same_pool_control_not_selected` 说明（非异常）**：

首次在漏斗统计中注意到此 reason_code，已定位至 `aletheia/analytics/causal_controls.py:292-295`。逻辑为：universe allocator 从同一 cohort pool 中选出部分 symbol 标记 `selected_by_allocator`，未被选中的同 pool symbol 则写入 `same_pool_control_not_selected`。这是**因果推断控制组**设计——记录"本轮同池竞争者"以便事后评估 allocator 决策质量，属正常分析日志，非拒绝异常。历史分布：7月10日 80 条、7月11日 920 条、7月12日 870 条、7月13日 970 条、7月14日（截至 08:20Z）420 条，与 universe 运行节奏一致。

---

### §3 Operator 决策（48h）

| action | 次数 |
|---|---|
| skip | 448 |
| signal_pending | 116 |
| trade | 87 |
| scale_in | 21 |
| watch | 2 |

最新决策：08:13:53Z。近 2h 节奏：08:xx 3 trade + 2 signal_pending + 1 skip，交易决策正常产出。

---

### §4 风控执行（24h）

- 执行：**4 笔**，拒绝：**11 笔**
- 07:07Z 之后无新增风控拒绝（与 §13 相同）
- 07:07Z 最后一笔拒绝：KITE/USDT `incomplete_entry_plan`（stop=0.0、tp=0.0）— MON-017 仍复现

---

### §5 持仓与孤儿订单

| Symbol | 方向 | 杠杆 | 入场 | 当前价 | 未实现 PnL | 持仓时长 | 状态 |
|---|---|---|---|---|---|---|---|
| HYPE/USDT | short | 2x | 64.0050 | 63.421 | **+$0.69** | 17.8h | ✅ 盈利中 |
| LIT/USDT | short | 2x | 2.3572 | 2.385 | **-$1.27** | 13.7h | 🟡 从 §13 -$1.97 回升 |
| PI/USDT | short | **NULL** | 0.07284 | 0.07429 | **-$1.98** | 6.2h | 🔴 快速恶化 |
| BASED/USDT | short | 2x | 0.08784 | 0.08991 | **-$1.69** | 1.4h | 🔴 快速恶化 |

**合计未实现 PnL：-$4.25**（§13 为 -$2.12，1h 内亏损增加 $2.13）

**MON-035（新）— PI/USDT leverage=NULL**：

PI/USDT 开仓提案 `leverage` 字段为 NULL，位于 `trade_proposals` 和 `positions` 表。Position 的 `market_type=usdt_futures`，但 leverage 列为空，等效为 1x 无杠杆。`unrealized_pnl` 验证：`(0.07429 - 0.07284) × 1372.12 qty = $1.99 ≈ -$1.98`，无杠杆放大一致。

这与 HYPE/LIT/BASED（均为 2x）不同，应为 Operator 生成该笔 trade 时未指定 leverage 字段。-$1.98 在 6.2h 内已接近 2% 价格偏移，若不恢复将持续扩大。

**孤儿订单**：9 笔，与 §13 相同，无变化（3 BTC/ETH/NEAR pending + 6 okx_demo submitted）。

---

### §6 平仓历史（48h 内）

- 48h 内仅 1 笔平仓：BTC/USDT `stop_loss`（与 §13 一致）
- 全量 outcome：admin 125、unknown 116、maintenance 6、strategy 5（eligible=1）

---

### §7 Scanner 历史 PnL（全量）

| Scanner | 胜次 | 败次 | 总 PnL |
|---|---|---|---|
| mean_reversion | 0 | 26 | -$2.63 |
| momentum | 5 | 14 | -$2.51 |
| short_bias | 0 | — | -$0.01 |

与 §13 完全一致，无新增平仓记录影响统计。

---

### Agent 健康（2h 窗口）

| Agent | 运行次数 | 错误 | 超时 | 最新 |
|---|---|---|---|---|
| market_observer | **12** | **0** | **0** | 08:11Z ✅ |

**MON-030 完全解决**：market_observer 2h 窗口 12 次运行全部 clean，无 loop_budget_exceeded、无 timeout。持续已超 3h 无复发。

---

### 问题汇总（截至第 14 次观测，2026-07-14T08:20Z）

| 编号 | 严重度 | 描述 | 状态 |
|---|---|---|---|
| MON-002 | 🔴 | 3 笔 pending 孤儿订单 | ⏳ |
| MON-003 | 🔴 | 所有 scanner 历史 PnL 为负 | — 策略层 |
| MON-006 | 🟠 | outcome 字段缺失，247/252 笔无学习价值 | ⏳ |
| MON-009 | 🔴 | 6 笔 okx_demo submitted 孤儿订单 | ⏳ |
| MON-016/022 | 🟠 | 非加密 symbol 泄入 pipeline | ⏳ |
| MON-017 | 🟠 | `incomplete_entry_plan` 复现（KITE 07:07Z），LLM 发出 stop/tp=0.0 的提案 | ⏳ |
| MON-020 | 🟠 | mean_reversion 全量 0% 胜率（26 losses） | ⏳ |
| MON-021 | 🟡 | direction_overheated 87 次（+10 in 1h），Operator 无拒绝记忆，加速趋势 | ⏳ |
| MON-025 | 🟠 | 双心跳写入 | ⏳ |
| MON-030 | ✅ | market_observer loop_budget_exceeded | 完全解决 |
| MON-032 | ✅ | equity 与 paper account 脱节 | 已解决（f7a88e81）|
| MON-033 | 🟠 | LIT/USDT short 持仓亏损，当前 -$1.27（从 -$1.97 略有回升）| ⏳ 监控中 |
| **MON-034** | **🟠** | **observer_pre_filter 132 条/24h（§13 为 61，+71 in 1h），市场低波动加剧** | ⏳ |
| **MON-035 NEW** | **🟠** | **PI/USDT leverage=NULL（usdt_futures 仓位未设杠杆），当前 -$1.98 / 6.2h** | 🔴 恶化中 |
| **MON-033b** | **🔴** | **BASED/USDT short -$1.69 in 1.4h（2x leverage，价格反向快速运动）** | 🔴 恶化中 |
| **合计未实现** | — | **-$4.25**（1h 前为 -$2.12，恶化 +$2.13）| ⚠️ |

---

## 第 15 次观测增量（2026-07-14T14:05Z，诊断性观测）

> 数据源：`uv run aletheia doctor`、`opportunity audit`、`inspect candidates`、`inspect regime`、`inspect pnl`、`inspect reflection`、`inspect calibration`
> 系统状态：Reviewing / Auto，is_busy=0，heartbeat 0min ago
> **注意：距 §14（08:20Z）已过去约 5h45min，是本日最长观测间隔**

---

### 系统总体状态

| 指标 | 当前值 | 较 §14 变化 |
|---|---|---|
| Operator 模式 | Reviewing / Auto | Trading → Reviewing（退后一步） |
| Operator intent | "1 failed operator task requires review before trusting background supervision" | ⚠️ 新异常 |
| 提案漏斗 | **干涸（drought=yes）** | 07:05Z 至今 7h 无新提案 |
| signal win_rate（24h） | **39.1%（2877 settled，avg_net=-1.460%）** | 低于 40% 阈值 |
| 最新 pi_decision | 35min ago（SKIP） | 仍在运行，但全量 skip |
| candidate_supply executable | **0** | drought=yes |
| open positions 合计 unrealized | **-$21.41** | §14 为 -$4.25，**恶化 $17.16** |

---

### §1 Opportunity 产出（24h）

| 指标 | 数量 |
|---|---|
| 24h total | 891 |
| 最新 opportunity | 4min ago ✅ |
| scanner 产出 | 正常（momentum/mean_reversion/short_bias/volatility） |

opportunitiy 产出本身正常（每 4min 新条目），但所有候选均未进入提案层。

**Audit 漏斗（24h window）：**

| 最终状态 | 数量 |
|---|---|
| pending | 174 |
| dropped | 23（ttl_expired/debate_downgraded/operator_skip） |
| rejected | 2 |
| **executed** | **1** |

**仅 1 笔执行（24h）**，且是 7h 前的历史记录——今日（14:05Z 起回推 7h = 07:05Z 后）**零新执行**。

---

### §2 提案漏斗状态

**candidate_supply 诊断：**

```
executable=0
main_reason=no_candidates
drought=yes
```

**候选评估（inspect candidates）：**

| symbol | direction | trade_score | confidence_score | **factor_status** |
|---|---|---|---|---|
| ZEC/USDT | long | 50.73 | 55 | **watch** |
| BASED/USDT | short | 49.16 | 53 | **watch** |
| CL/USDT | long | 46.28 | 49 | **watch** |
| BZ/USDT | long | 44.73 | 47 | **watch** |
| ALLO/USDT | short | 44.03 | 46 | **watch** |
| LIT/USDT | long | 44.01 | 46 | **watch** |

**所有候选 `factor_status=watch`（非 eligible），`guard_blocked=False`**。

分数 44-51 范围，并非低分——问题不在信号强度，而在资格标记。

---

### §3 提案干涸根因分析（MON-036）

**触发链：**

1. `data.signal` 警告：24h win_rate=39.1% < 40% 阈值（2877 settled，avg_net=-1.460%）
2. `paper_trader_ranker` 在 win_rate 低于阈值时将 `factor_status` 设为 `watch`
3. `factor_status=watch` → ranker 写入 `execution_eligible=False`
4. `paper_ranker` 层拒绝：`reason_code=execution_eligible_false`
5. `candidate_supply.executable=0` → 无候选进入 `paper_trader_selection`
6. 结果：proposal flow drought，Operator 空转 skip

**反馈锁死结构（Feedback Deadlock）：**
```
信号胜率 < 40%
  ↓
factor_status=watch → execution_eligible=False
  ↓
无新提案 → 无新交易 → 无新 closed position
  ↓
outcome 样本不增加 → win_rate 无法改善
  ↓
（循环）
```

当前 4 笔 open positions 都是亏损的 short，若不触及止损/止盈，它们将保持 open 状态持续累积亏损，同时系统因为 drought 无法开新仓调整仓位组合。

**市场状态（inspect regime）：**

| 指标 | 值 |
|---|---|
| regime | SIDEWAYS 56% confidence |
| long_weight | 50%, short_weight | 50% |
| btc_vs_ma200_pct | -13.62（BTC 低于 200MA 13.6%） |
| btc_momentum_30d_pct | -4.06%（负向动量） |
| vix | 16.5 |
| F&G | 22（Extreme Fear） |

宏观偏空，但 4 笔 short 仓位均亏损（个股逆势走强），说明 altcoin 与 BTC 近期出现相关性断裂，short bias 在此环境下持续失效。

---

### §4 Operator 状态

**最新决策（35min ago）：SKIP**
> 原文：Multiple compounding factors argue for inaction: (1) Signal accuracy is below threshold at 39.3%, triggering the degradation alert — this alone warrants skipping low-conviction setups. (2) All four ex. Counterfactual unavailable: no causally linked opportunity.

**inspect agent-status：**
```
mode: Reviewing
queue_size: 3
intent: "1 failed operator task requires review before trusting background supervision."
```

Operator 被一个失败任务锁在 Reviewing 模式，无法切换到 Trading。这可能是 MON-028 类似的循环，或某个 background agent 任务失败触发了 Repairing 前哨检查。

---

### §5 持仓状态（MON-037）

| symbol | 方向 | leverage | entry | 持仓 | unrealized PnL | §14（08:20Z）对比 | 变化 |
|---|---|---|---|---|---|---|---|
| HYPE/USDT | short | 2x | 64.005 | 23.7h | **-$0.94** | +$0.69 | ↓ **-$1.63**（翻负） |
| LIT/USDT | short | 2x | 2.3572 | 19.5h | **-$10.63** | -$1.27 | ↓ **-$9.36** |
| PI/USDT | short | **NULL** | 0.07284 | 12.0h | **-$8.68** | -$1.98 | ↓ **-$6.70** |
| BASED/USDT | short | 2x | 0.08784 | 7.2h | **-$1.16** | -$1.69 | ↑ +$0.53 |
| **合计** | — | — | — | — | **-$21.41** | **-$4.25** | ↓ **-$17.16** |

**5h45min 内合计亏损扩大 $17.16**（约每小时 +$3 亏损）。

LIT 和 PI 是主要出血点：
- LIT/USDT 2x 杠杆，price 从 2.3572 → ~2.4689（+4.7%，short 方向逆势）
- PI/USDT NULL leverage，price 从 0.07284 → ~0.0791（+8.6%，short 方向逆势）

MON-035（PI leverage=NULL）问题持续：PI 仓位无杠杆，亏损已 -$8.68（12h），若有 2x 杠杆实际已触及止损（入场 0.07284，2x 下 5% 逆势 = 止损；当前偏移 +8.6%）。

**最新 risk decision：** 07:07Z（KITE/USDT `incomplete_entry_plan` 拒绝）—— 距今 7h 无新提案进入 risk 层。

---

### §6 信号质量（MON-038）

**calibration 数据（全量已结算）：**

| scanner_family | side | market | horizon | n | win% | avg_net |
|---|---|---|---|---|---|---|
| momentum | long | perp | 4h | 842 | 42.5% | **+0.13%** ← 唯一正期望 |
| momentum | short | perp | 4h | 166 | 40.4% | -0.90% |
| mean_reversion | long | perp | 4h | 402 | 36.1% | -0.81% |
| short_bias | short | perp | 24h | 183 | 52.5% | **+0.37%** ← 正期望 |
| volatility | long | perp | 24h | 187 | 45.5% | +0.28% |
| event | short | perp | 4h | 11 | 90.9% | +3.86%（小样本） |

**scorecard（factor-feedback）：** 全部 scanner families 标记 `outcome_status=insufficient`——自 2026-07-12 clean window 开始以来，**zero 真实 closed trades** 在 clean window 内；系统学习完全依赖 shadow trades。

**24h net signal（doctor 数据）：** win_rate=39.1%，avg_net=-1.460%，settled=2877。这与 calibration 的结果一致：系统在大多数方向/时间框架上期望值为负。

**关键矛盾：** momentum long perp 4h（+0.13%）和 short_bias short perp 24h（+0.37%）有正期望，但当前 4 笔 open positions 全是 short 方向（short_bias/momentum short）且全部亏损——说明这些期望值在当前市场窗口内未能实现。

---

### §7 debate 影响（missed_alpha_suspect）

**overall verdict: missed_alpha_suspect**

6 个 ALLO/USDT、KITE/USDT、BEAT/USDT 机会被 debate 降低 conviction 后跳过，事后 forward outcome 显示部分为正向机会：
- ALLO/USDT：跳过后 4h 净收益 +10.8%（missed）
- KITE/USDT：多次跳过后 1h/4h 混合（部分 protective，部分 missed）

debate 在保护部分（防止了亏损）的同时也错过了 ALLO/USDT 这类真实 alpha。当前 drought 状态下 missed_alpha 代价更高。

---

### 问题汇总（截至第 15 次观测，2026-07-14T14:05Z）

| 编号 | 严重度 | 描述 | 状态 |
|---|---|---|---|
| MON-002 | 🔴 | 3 笔 pending 孤儿订单（BTC/ETH/NEAR） | ⏳ BTC 持续阻断 risk |
| MON-003 | 🔴 | 所有 scanner 历史 PnL 为负（252 笔，zero 验证期内真实 closed trade） | — 策略层 |
| MON-006 | 🟠 | outcome 字段缺失，247/252 笔无学习价值 | ⏳ |
| MON-009 | 🔴 | 6 笔 okx_demo submitted 孤儿订单 | ⏳ |
| MON-016/022 | 🟠 | 非加密 symbol（AAPL/XAG 等）泄入 pipeline，NULL 48 条/24h | ⏳ `research_worker.py:_is_valid_symbol()` |
| MON-017 | 🟠 | `incomplete_entry_plan` 复现（KITE 07:07Z，stop=0.0/tp=0.0）| ⏳ `risk/engine.py:172-185`，LLM 产出 0.0 字段 |
| MON-020 | 🟠 | mean_reversion 全量 0% 胜率（26/26 全亏） | ⏳ |
| MON-021 | 🟡 | direction_overheated 87+ 次，Operator 无拒绝记忆 | ⏳ |
| MON-025 | 🟠 | 双心跳写入（supervisor + Operator 并发）| ⏳ |
| MON-030 | ✅ | market_observer loop_budget_exceeded | §14 完全解决 |
| MON-032 | ✅ | equity 与 paper account 脱节 | 已修复（06:51Z 后已生效） |
| MON-033 | 🟠 | LIT/USDT short 亏损 -$10.63（19.5h hold，2x leverage） | 🔴 急剧恶化 |
| MON-034 | 🟠 | observer_pre_filter no_sufficient_signal 132 条/24h（市场低波动期） | ⏳ |
| MON-035 | 🟠 | PI/USDT leverage=NULL，-$8.68（12h）持续出血 | 🔴 恶化中 |
| **MON-036** | **🔴 P1 NEW** | **提案漏斗完全干涸（drought=yes）：7h 无新提案，所有候选 factor_status=watch，signal win_rate 39.1% < 40% 阈值触发 execution_eligible_false** | ⚡ 反馈死锁：无交易→无结果→win_rate 无法恢复 |
| **MON-037** | **🔴 P1 NEW** | **四仓合计未实现亏损 -$21.41（5.75h 内恶化 $17.16）：LIT -$10.63，PI -$8.68，HYPE -$0.94，BASED -$1.16** | ⚡ 持续出血，无止损触发迹象 |
| **MON-038** | **🟠 NEW** | **Operator 被"1 failed task"锁在 Reviewing 模式（queue_size=3），无法切换 Trading** | ⚡ 可能影响止损/止盈决策执行 |

---

### 根因深度分析（新增）

#### RC-036 ｜ execution_eligible_false 反馈死锁

**代码路径：**
- `aletheia/agent_shell/paper_trader_ranker.py`（待确认）或 `aletheia/agents/paper_trader.py`：
  当 scanner family 的 24h win_rate 低于 `_SIGNAL_DEGRADATION_THRESHOLD`（40%）时，
  ranker 写入 `execution_eligible=False` 并标记 `factor_status='watch'`
- `aletheia/analytics/filter_decisions.py`（待确认）：
  `paper_ranker` 层检查 `execution_eligible` 字段，`False` → `reason_code='execution_eligible_false'`
- 结果：全部候选被 paper_ranker 拒绝，`candidate_supply.executable=0`

**缓解方向：**
1. 检查 win_rate 下降是否与当前市场状态相关（如 SIDEWAYS + altcoin 逆势），而非系统性信号失效
2. 若是临时市场环境，可考虑临时降低 `execution_eligible` 阈值或启用 `force_eligible` bypass（**高风险，需授权**）
3. 等待 4 笔 open positions 关闭（产生 outcome data）→ win_rate 样本更新

#### RC-037 ｜ 四仓亏损加速原因

**市场因素：** regime=SIDEWAYS，但个股（LIT/PI）走势与 short direction 相反，说明 short bias scanner 在本市场窗口中失效。
- LIT +4.7%（19.5h），PI +8.6%（12h）——较大的逆势走势
- 止损未被触发说明 stop_price 设定较宽松（或 PI/USDT 的 stop_price 为 NULL/未设定）

**代码关联：** PI/USDT 的 `stop_price` 和 `leverage` 均为 NULL（MON-035），表明该仓位的风险管理配置不完整，无自动止损保护。

---

## 定时观测 2026-07-14T15:14Z

### 摘要

系统于本次观测窗口内（14:05Z → 15:14Z）**gateway 进程停止**，且 launchd 看门狗因指向过时路径无法自动拉起，系统处于无法自愈状态。这是今日最严重的新增问题。四仓持仓合计 -$21.21，与上次基本持平（LIT 略有回收，BASED 加速恶化）。提案干涸延长至 8.1h，信号胜率继续下滑至 38.9%。Operator 最后一条决策文本含"test reasoning"字样，疑似上下文损坏。⚡ 需要人工重启 gateway（`uv run aletheia gateway restart`）。

### 问题更新 / 新增

- **TUE-001 NEW 🔴 P0**：Gateway 进程停止，launchd 看门狗配置指向过时路径（`registration points at stale code/path`），自动拉起失败（`gateway_spawn_failed: no pid file / port not listening`）。日志确认 gateway 在 23:15Z 最后一次成功启动后某个时间点停止，目前无法自愈。需手动执行 `uv run aletheia gateway restart` 并重新注册看门狗 `uv run aletheia gateway enable-autostart`。

- **TUE-002 NEW 🟠**：Operator 最后一条决策（15:11Z，gateway 停止前 ~3min）内容为 `TRADE BTC/USDT long conviction=7, Reason: test reasoning`。两处异常：① BTC/USDT 不在当前 open positions 也不是正常决策标的；② "test reasoning" 是明显的占位文本，不是真实 LLM 推理结果。这通常发生在 Operator 上下文为空或 LLM 调用返回降级内容时。推测是 gateway 停机前内部上下文已损坏导致的最后一次异常决策，不会产生实际提案（无 causally linked proposal）。

- **MON-036**：提案漏斗干涸延长至 8.1h（14:05Z 为 7h）。所有候选仍为 `factor_status=watch`，无变化。信号胜率 38.9%，较上次（39.1%）继续下滑，avg_net=-1.495%（上次 -1.460%），趋势向坏。候选池新增 ALLO/USDT（score=55.23，top 候选），但因 watch 状态同样无法进入漏斗。

- **MON-037**：四仓合计 -$21.21（上次 -$21.41，净改善 $0.20）。结构上：LIT 从 -$10.63 回收至 -$8.41（改善 $2.22，市场短暂回撤），但 BASED 从 -$1.16 恶化至 -$2.42（1h 内翻倍，2x 杠杆下价格继续逆势）。PI 从 -$8.68 → -$9.33（继续出血，无止损），HYPE 微幅恶化。LIT 出现 long 候选信号（momentum，score=46），说明市场已开始反向走势，当前 short 仓位与市场信号矛盾。

- **MON-038**：Operator Reviewing 模式，`heartbeat stale`（2min 内无新心跳），与 TUE-001 gateway 停止相关联。失败任务问题尚未解决，gateway 重启后需观察是否自动恢复。

---

## 定时观测 2026-07-14T16:19Z

### 摘要

Gateway 已恢复（TUE-001 解决），autostart 重新注册正常。Operator 心跳正常，"test reasoning" 决策为一次性异常（TUE-002 自愈）。但提案干涸从 8.1h 延长到 9.2h，出现了一个新的阻塞层：Operator 在决策理由中明确报告"Available capital is $0"，这与 paper account 真实余额不符，判断为独立 bug。信号胜率 39.3% 微幅回升但仍在阈值以下。四仓合计亏损略有收窄（-$21.22 → -$20.12），无止损触发。

### 问题更新 / 新增

- **TUE-001**：✅ 已解决。Gateway 恢复运行（PID 99546），autostart 重新注册（runs=25，last_result=0），24h 内无重启事件。

- **TUE-002**：✅ 已自愈。Operator 恢复正常决策逻辑（35min 前 SKIP on AAVE/USDT，理由正常），"test reasoning" 为 gateway 停机前的一次性上下文损坏，无后续影响。

- **MON-036**：提案干涸延长至 9.2h（上次 8.1h）。但 `candidate_supply.main_reason` 从 `no_candidates` 变为 `equity_shadow_track`，说明候选标的本身已存在，但被 paper_ranker 以"可用权益不足"为由进入影子追踪模式而非执行。这与 Operator 报告的"Available capital is $0"吻合，是现在的主要阻断原因（不再是 no_candidates）。

- **MON-037**：四仓合计 -$20.12（上次 -$21.22，改善 $1.10）。BASED 回收 $0.97，HYPE 改善 $0.22，PI 基本持平，LIT 微幅恶化 $0.11。方向上 LIT 的 momentum long 候选信号升至 score=50.51（top 2 候选），与现持 short 仓位的方向矛盾进一步加剧，市场正在反向走势。无止损触发。

- **WED-001 NEW 🔴**：Operator 决策上下文报告"Available capital is $0"，与 paper account 真实余额（~$376，含浮亏后）不符。即便信号胜率回升或 factor_status 解除 watch，Operator 也会以"无可用资金"为由拒绝开新仓。这是提案干涸的第二层独立阻塞——与 MON-036 的 `execution_eligible_false` 是两个不同的卡点。根因疑似在 `aletheia/agent_shell/trading_context.py` 计算可用资金的逻辑：4 笔持仓的浮亏被计入后，available_capital 被算成负数或 0，但 paper_ranker 和 risk engine 读取的 equity 来自 `paper_account`（MON-032 修复路径），两条路径不同步。

- **WED-002 NEW 🟡**：`code.sync` 新警告：HEAD=348eac3（较上次 9c71e1f 已变更，有代码提交），15 commits 未推送，3 behind origin。有上游更新未合入。若涉及 paper_trader_ranker 或 trading_context 的修改，可能与 WED-001 的资金计算问题有关，建议先 `git pull --ff-only` 再 restart。

- **WED-003 NEW 🟡**：`content.signal_settlement` 积压从 12 条（14:05Z）跳升至 117 条（overdue）。判断为 gateway 停机期间（15:14Z 附近）outcome settler 暂停造成的批量积压，非结构性问题，预计在 settler 追上后自动清零。若 1h 内不下降则需关注。

---

## 定时观测 2026-07-14T17:15Z

### 摘要

LIT/USDT position 已关闭（系统自动止盈/止损），总未实现亏损从 -$20.12 跌至 -$11.81，最大出血点消除。但随之而来的是系统性数据管道停顿：6 个外部数据采集器在 ~16:38Z 同时冻结（全部 `success(overdue)`），市场快照过时、LSR 过时、observer 停顿、outcome settler 停顿，health.rollup 首次报告 `degraded`，警告数从 4 跳至 12。这是今日第一次大规模数据停顿，历史上 MON-024 有类似先例，均自愈。

### 问题更新 / 新增

- **MON-036**：candidate pool 从 13 个缩减至 2 个，原因是数据停顿期间候选 TTL 到期（main_reason 从 equity_shadow_track 变为 ttl_expired）。drought 持续，但此刻的阻塞原因已从 execution_eligible_false 转为数据层——数据恢复后候选评估可能恢复。

- **MON-037**：LIT 关闭后总未实现 -$11.81（HYPE -$1.21，PI -$7.62，BASED -$2.97）。PI 从 -$9.31 改善至 -$7.62，开始回撤，方向趋势有所缓解。BASED 继续恶化。LIT 关闭记录含 `incomplete_funding_economics` exclusion，关闭原因完整性需后续确认，但仓位已实际平掉。

- **WED-001**：Operator 决策理由仍包含"Available capital is $0"，与 LIT 关闭后应释放的保证金不符。LIT 关闭后 available_capital 应当增加，但 Operator 上下文未同步刷新，$0 资金卡点仍然存在。

- **WED-003**：settler 积压从 117 → 143 条（过期），filter_evidence stale_overdue 增至 319。settler 停顿与数据管道停顿同步发生，属连带效应。

- **WED-004 NEW 🔴**：~16:38Z 起，6 个外部数据采集器（derivatives、volume_scanner、coingecko_top、macro、hyperliquid、defillama）同时进入 `success(overdue)` 状态，市场快照和 LSR 各过时 37min，health.rollup 报告 `degraded: market_data_stale:37min`。observer 同步停顿（newest opportunity 33min ago）。从时间点和范围看，最可能是外部 API 网络访问短暂中断或限流，类似 MON-024（已自愈）。Operator 切换为 Working 模式主动检测健康状态（正常行为）。

---

## 定时观测 2026-07-14T20:31Z

### 摘要

总未实现亏损大幅收窄至 -$5.14：BASED 转为盈利（+$0.62），PI 从 -$7.62 回收至 -$4.17，LIT 已关闭。数据管道在 17:15Z 后短暂恢复，但在 ~19:40Z 发生第二轮停顿（当前 51min stale），所有 7 个采集器再次 overdue。提案漏斗因 MON-036 drought 已超 13h 无新提案，当前 candidate 池为空。新增 `loop.profit` P1 警告（proposal stalled 13.4h）和 `data.serenity_pipeline` 警告（dreaming_daily 错过计划运行）。Operator Working 模式但 queue_size=483，几乎全被数据健康问题消耗。信号胜率 39.8%，距 40% 阈值仅一步之遥。

### 问题更新 / 新增

- **MON-036**：candidate pool 降至 0（candidates 全部 TTL 到期），`inspect candidates` 无结果。但 opportunity audit 显示 `drought=no`，说明 drought 标志已在系统层面解除——只要数据管道恢复 + 新一批候选完成评分，提案应当重新出现。loop.profit 警告的"13.4h 无提案"本质上是 MON-036 drought 的另一视图，不是独立的新故障。当前唯一真正的阻断是 WED-001（$0 capital）+数据停顿（WED-004 第二轮）。

- **MON-037**：三仓合计 -$5.14（BASED +$0.62、PI -$4.17、HYPE -$1.59）。LIT 已关闭，最大亏损点解除。PI 大幅回收（-$7.62 → -$4.17，+$3.45），市场价格已明显回撤，short 仓位方向正确性在恢复。无止损触发，三仓仍 open。PI leverage=NULL（MON-035）风险在本轮回收中暂时缓解，但结构性风险未消除。

- **WED-001**：Operator 最新决策（0min ago）仍报告"Available capital is $0"，LIT 关闭后 13h+ 仍未刷新。这是目前除数据停顿外最主要的新仓阻断——就算 drought 解除、数据恢复，Operator 也会以"无资金"跳过一切候选。根因在 `trading_context.py` 的可用资金计算未与 LIT 关闭后的实际 paper_account 余额同步。

- **WED-003**：`content.signal_settlement` 在 20:31Z 状态为 ✅（settlement current），较上次的 117 条 overdue 有所改善。但 `data.filter_evidence` 的 stale_overdue 从 319 激增至 2829——这是第二轮数据停顿造成的：market data 缺失导致 filter evidence outcomes 无法定价结算，属数据停顿连带效应。

- **WED-004**：确认为今日第二轮数据停顿（第一轮 ~16:38Z，第二轮 ~19:40Z）。两轮停顿间隔约 3h，中间数据短暂恢复。第二轮在查询时已持续 51min，依历史规律应自愈，但 Operator 的 queue_size=483 说明健康响应循环已积压大量任务，可能延迟恢复时的系统正常化。

- **WED-005 NEW 🟠**：`data.serenity_pipeline` 出现新警告：Serenity 研究流水线 stale 1.1d（上次成功运行约 07-13T20:00Z），`dreaming_daily` 在 02:00 CST 的计划运行未执行，最新尝试状态为 running（在飞，未完成）。Serenity 提供 101 个 active opportunities，其缺失会减少 opportunity pool 多样性，加剧 drought 程度。若 running 的任务长期卡住需关注。

- **WED-002**：code.sync 警告持续（15 commits 未推，3 behind origin），无变化。

---

## 定时观测 2026-07-15T01:14Z

### 摘要

数据停顿（WED-004）已完全自愈：health.rollup 重回 healthy，filter_evidence stale_overdue 归零，market snapshot 和 orderbook 均新鲜，警告数从 13 降至 6。代码同步完成（git pull，HEAD 更新至 911ec6f，WED-002 消除）。但持仓亏损再度恶化：20:31Z 短暂改善后全面逆转，三仓合计 -$9.77（较 20:31Z 的 -$5.14 恶化 $4.63），PI 是主要出血点。WED-001（$0 capital）代码更新后依然存在，drought 重返 yes，提案漏斗停顿累计 18.1h。

### 问题更新 / 新增

- **WED-004**：✅ 已完全解决。数据停顿自愈，health.rollup healthy，主要数据源全部恢复（market/orderbook/coingecko/hyperliquid 均新鲜）。filter_evidence stale_overdue 从 2829 降至 0。3 个采集器（derivatives/hyperliquid:timed_out、defillama:skipped_budget）残留轻微降级，不影响 health rollup。data.lsr 3.8h stale 持续，与 Binance FAPI 可达性相关。

- **WED-003**：✅ 已完全解决。filter_evidence stale_overdue=0，signal_settlement current。

- **WED-002**：✅ 已解决。git pull 完成，HEAD 更新至 911ec6f，code.sync 不再警告。过去 24h 有 3 次 git_hook 触发的 gateway 重启，代码已正常落地。

- **WED-001**：仍然阻断。代码更新未修复 $0 capital 问题，Operator 最新决策（1min ago）仍包含"Available capital is $0"。这是提案干涸的首要原因——即便 drought 解除、胜率越过 40%，Operator 会持续跳过全部候选。根因依然是 `trading_context.py` 的可用资金计算未与实际 paper_account 余额同步。

- **WED-005**：escalated。attempt=failed (unknown_error)，dreaming_daily 失败（此前为 running）。Serenity 研究管道已 stale 1.3d，两个计划运行时间点均错过。需查 gateway 日志定位 unknown_error 根因。

- **MON-036**：drought 从 20:31Z 的短暂"no"重返 yes，executable=0。唯一候选 LAB/USDT（score=56.56，factor_status=watch）仍卡在 watch 状态，win_rate 39.7% 的封锁有效。提案停顿累计 18.1h。

- **MON-037**：三仓合计 -$9.77（BASED -$1.17、PI -$7.06、HYPE -$1.55），较 20:31Z 恶化 $4.63。PI 最关键：20:31Z 回收至 -$4.17 后再次逆转至 -$7.06，说明价格仅是短暂波动而非趋势反转，short 方向仍逆势。PI 无止损/无杠杆（MON-035），每轮逆势均无自动出场机制，风险持续累积。

---

## 定时观测 2026-07-15T02:14Z

### 摘要

信号胜率升至 39.9%，距 40% 阈值仅剩 0.1%——若下一个结算批次跨过阈值，factor_status 将自动从 watch 转为 eligible，候选漏斗将解锁。候选池从 1 个扩张到 13 个（LIT/KAITO/ZEC long、BILL/BASED short），管道健康度显著恢复。三仓合计亏损从 -$9.77 收窄至 -$8.11，PI 再次回收（-$7.06 → -$4.91），BASED 小幅恶化。系统发出多条 LIT long 信号，与上午关闭的 LIT short 方向相反，证实当时平仓时机正确。又一次 git_hook 重启（HEAD 升至 82e713e），WED-001 仍未修复。

### 问题更新 / 新增

- **MON-036**：候选池扩张至 13 个，管道重新活跃。信号胜率 39.9%，是今日最接近 40% 阈值的时刻。一旦胜率越过 40%，所有 watch 候选将获得 eligible 资格，提案流可能立即恢复——但 WED-001（$0 capital）是独立的第二层阻断，仍会阻止实际开仓。两个阻断必须同时解除才能出现新交易。

- **WED-001**：仍然阻断。最新决策（1min ago）理由包含"signal_accuracy_24h at 39.9%, just below the 40% threshold"——措辞已由"below"趋于临界，但 $0 capital 部分依然存在。代码更新（82e713e 部署）未修复此问题，dirty worktree 表明还有改动在进行中，可能正在处理中。

- **MON-037**：三仓合计 -$8.11（BASED -$1.62、PI -$4.91、HYPE -$1.57），较 01:14Z 改善 $1.66。PI 从 -$7.06 回收至 -$4.91，说明价格波动方向对 short 有利。HYPE 基本持平（-$1.55 → -$1.57）。PI 无止损（MON-035）风险在价格回撤期间暂缓，但结构性缺陷未消除。

- **WED-005**：Serenity attempt 仍为 failed (unknown_error)，stale 1.3d，无改变。

- **data.lsr**：4.8h stale（01:14Z 为 3.8h），持续老化，LSR 数据在今日已全天未刷新。Binance FAPI 可达性问题需关注，但当前 health.rollup 未受影响。

- **data.onchain**：6.6h stale，持续老化（DefiLlama），同时 defillama collector 进入 skipped_budget 状态——budget 限制主动跳过，说明该 collector 每次运行成本超过 40s 时间预算。不影响交易决策，属次要问题。
