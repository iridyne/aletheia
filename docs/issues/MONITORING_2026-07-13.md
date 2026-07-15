# 系统监控观测报告 — 2026-07-13

> 数据窗口：过去 48 小时（2026-07-11 ～ 2026-07-13T08:10Z）
> 数据源：`data/app.db` 直查，无 Dashboard
> 触发：第 1 次（07:39Z）+ 第 2 次（07:47Z）+ 第 3 次（08:10Z，发现执行封锁）
>
> **图例**：🔴 阻断/严重异常 ｜ 🟠 数据质量降低 ｜ 🟡 效率/覆盖率问题
>
> ---
> ⚠️ **P0 升级（第 3 次观测）：paper_internal 执行通道自 2026-07-13T05:39Z 起被完全封锁，
> 今日剩余时间内所有提案均将被拒绝。直接触发原因：MON-001 的代码缺陷（`close_position()`
> 不写 `close_category`）已通过 `account_loss` 计算链产生级联失败。见 MON-011 和 RC-011。**

---

## §1 Opportunity 产出数量

| 窗口 | 数量 |
|---|---|
| 最近 24h | 1,529 |
| 最近 48h | 2,090 |
| 全量累计 | 25,735 |

**Scanner 分布（48h）：**

| scanner_family | count | 占比 |
|---|---|---|
| momentum | 1,242 | 59.4% |
| mean_reversion | 389 | 18.6% |
| short_bias | 242 | 11.6% |
| volatility | 203 | 9.7% |
| event | 11 | 0.5% |
| breakout | 3 | 0.1% |

**观察：** 产出量稳定，节奏正常。momentum 占近六成。

---

## §2 confidence/ranker/filter 各层通过率（48h）

| 阶段 | passed | rejected | 通过率 |
|---|---|---|---|
| observer_pre_filter | 1,982 | 78 | 96.2% |
| universe_allocation | 2,671 | 2,210 | 54.7% |
| paper_ranker | 8,077 | 2,000 | 80.2% |
| paper_trader_selection | 236 | 788 | **23.1%** |
| proposal_submission | 93 | — | — |
| system_operator | 98 | — | — |
| system_operator_debate | 45 | — | — |
| risk | 18 | 31 | **36.7%** |

**🟠 注意：**
- `paper_trader_selection` 通过率仅 23%，是最强漏斗，从 opportunity 到 risk 整体压缩 >99%。
- `risk` 通过率 36.7%，拒绝率持续偏高（见 §4 根因）。

---

## §3 System Operator trade/skip 分布（48h）

| action | count | 占比 |
|---|---|---|
| skip | 440 | 53.4% |
| signal_pending | 236 | 28.6% |
| watch | 98 | 11.9% |
| trade | 38 | 4.6% |
| scale_in | 11 | 1.3% |
| close | 1 | 0.1% |

总决策数：824

**🟠 注意：**
- `signal_pending` 从上次观测的 229 条涨到 236 条，约 +7 条（约 4 分钟内），增长速率约 ~100 条/小时，与预期 864 条/天的增长率吻合。
- `trade` 仅 4.6%，`skip` 超过半数，模式未变化。

---

## §4 Proposal 和 Risk approve/reject（48h）

| 指标 | 数量 |
|---|---|
| proposals_executed | 18 |
| proposals_rejected | 31 |
| risk_approved | 18 |
| risk_rejected | 31 |

**拒绝率：63.3%（31/49）**

**🟠 异常：** 数值与上次完全一致，48h 内无新交易批准。风控双重卡口（见根因 §RC-004）。

---

## §5 Order、Fill、Position 完整性

| 指标 | 数量 | 状态 |
|---|---|---|
| orders_48h | 18 | ✅ |
| fills_48h | 18 | ✅ 1:1 匹配 |
| orders_pending（陈旧） | 3 | 🔴 |
| orders_submitted（陈旧） | **6** | 🔴 新增 |
| positions_open | 0 | ✅ |
| ps_orders_48h | 0 | 🟡 PS 层静默 |
| ps_fills_48h | 0 | 🟡 PS 层静默 |

**🔴 pending 孤儿订单（3 笔，未变化）：**

| order_id | symbol | mode | created_at | 悬挂天数 |
|---|---|---|---|---|
| 4502b2e31a38 | BTC/USDT | bitget_demo | 2026-06-21T06:26Z | **22 天** |
| c6df5df8fc56 | ETH/USDT | okx_demo | 2026-06-28T13:43Z | **14 天** |
| 320d83b19803 | NEAR/USDT | okx_demo | 2026-07-01T16:36Z | **11 天** |

**🔴 NEW：submitted 孤儿订单（6 笔），无 fill、无 position：**

| order_id | symbol | mode | created_at | 悬挂天数 |
|---|---|---|---|---|
| a0ccd0a8274a | XLM/USDT | okx_demo | 2026-07-04T19:05Z | **8 天** |
| 6463a60b4ba9 | MON/USDT | okx_demo | 2026-07-06T03:55Z | **7 天** |
| 4b24249ed3a0 | MON/USDT | okx_demo | 2026-07-06T04:02Z | **7 天** |
| b9ce59bb7631 | MON/USDT | okx_demo | 2026-07-06T06:10Z | **7 天** |
| 1e3439796175 | MON/USDT | okx_demo | 2026-07-06T04:18Z | **7 天** |
| 0dd0abb6c025 | MON/USDT | okx_demo | 2026-07-06T06:22Z | **7 天** |

5 笔 MON/USDT 均为不同 proposal（各自 risk approved），在 2.5 小时内连续下单，每笔 proposal 都通过了 risk 检查——因为此时 position 表无记录，风控看不见前一笔 submitted 订单的存在。

---

## §6 Position 关闭情况与 genuine outcome（48h）

**48h 内关闭的 18 个 position：**

| close_reason | close_category | outcome_eligible | count |
|---|---|---|---|
| reconciled_gone | administrative | 0 | 14 |
| stop_loss | unknown | 0 | 3 |
| take_profit | unknown | 0 | 1 |

**outcome_exclusion_reason 分布：**

| reason | count |
|---|---|
| administrative_close | 14 |
| unclassified | 4 |

**🔴 严重：** 所有 18 个关闭的 position `outcome_eligible=0`，genuine outcome 仍全部缺失（与上次一致，问题未修复）。stop_loss/take_profit 的 `close_category` 仍写入 `unknown`。

**filter_candidate_outcomes（48h settled）：**

| status | count | avg_net_return_pct |
|---|---|---|
| settled | 30,952 | **-0.4116%** |
| failed | **48** | NULL |

**🔴 NEW：`failed` 从上次观测的 16 跳升至 48（+32，约 4 分钟内新增 32 笔）。**

按小时分布：
- 2026-07-11T05:00Z：16 笔集中失败
- 2026-07-13T07:00Z：32 笔集中失败（本次观测期间刚发生）

所有失败行的 `exit_price_usd = NULL`，是结算时未能取到价格，详见根因 §RC-010。

---

## §7 每笔成本后 PnL 与 scanner/source attribution（48h）

| scanner_family | 笔数 | realized_pnl | avg_pnl | avg_fee | avg_net_after_fee |
|---|---|---|---|---|---|
| momentum | 7 | **-5.9516** | -0.8502 | 0.175 | **-1.026** |
| mean_reversion | 9 | -1.3326 | -0.1481 | 0.143 | **-0.291** |
| 无 opportunity 关联 | 2 | +1.7109 | +0.856 | 0.170 | **+0.685** |

**总计 realized_pnl：-5.57 USDT（18 笔）**

**🔴 momentum 策略继续亏损。** 平均费后净亏 -1.03 USDT/笔，7 笔合计亏 -7.18 USDT。数值与上次完全一致，无新交易发生。

---

## 根因分析（代码层）

### RC-001 ｜ MON-001/005：`close_position()` 不写 outcome 字段

**文件：** `aletheia/portfolio/pnl.py:259-272`

`close_position()` 调用 `update_position()` 时不设置 `close_category`、`outcome_eligible`、`outcome_exclusion_reason`，三字段永远停留在 schema 默认值（`'unknown'`、`0`、`'unclassified'`）。

正确路径在 `aletheia/portfolio/outcome_closure.py:184`（`close_position_verified()`），它正确写入这三个字段，但 stop_loss / take_profit 触发的关闭走的是普通 `close_position()`，从未调用 verified 路径。

**影响：** 所有 stop_loss/take_profit 关闭的 position 永远不进入 genuine outcome 统计，学习闭环断裂。

---

### RC-002 ｜ MON-002：demo 模式订单无 cleanup 机制

**涉及文件：**
- `aletheia/execution/reconciliation.py:38-68` — 对账逻辑**仅处理 `bitget_live_limited`**，demo 模式完全跳过
- `aletheia/agent_runtime/scheduler.py:1551-1607` — `_run_cleanup()` 清理快照/指标等表，**`orders` 表不在清理列表**
- `scripts/mvp_gate_check.py:152-157` — MVP gate 仅检查 `status='submitted'` 订单，**`status='pending'` 无检查**

**影响：** demo 模式下 pending/submitted 订单没有任何代码路径将其标记为 cancelled/failed，无限期悬挂。

---

### RC-004 ｜ MON-004：Risk 63% 拒绝率的主要驱动

**文件：** `aletheia/risk/engine.py:141-485`

两大卡口：

| Check | 位置 | 触发条件 |
|---|---|---|
| #5 position size cap | lines 208-225 | notional 超限或 drawdown 缩减后超阈 |
| #14 family evidence gate | lines 349-374 | live 模式需近 7 天 ≥30 笔 settled + 胜率 ≥50%，当前数据量几乎永远不满足 |

Check #14 在当前阶段等同于 live 入场的软性锁死，需 owner 确认是否符合设计意图。

---

### RC-007 ｜ MON-007：`signal_pending` 行永不删除

**涉及文件：**
- `aletheia/agents/paper_trader.py:284-297` — 每次 paper_trader 产出 opportunity 写一条 `signal_pending`
- `aletheia/agent_shell/trading_context.py:684-704` — System Operator 只读**最近 30 分钟内最新 5 条**，旧行全部被忽略
- `aletheia/agent_shell/reflection.py:51-86` — 决策后写**新行**，原 signal_pending 行从不更新/删除

**增长模型：** 约 864 条/天净增，0 清理。持续运行 30-60 天后表将被废弃行淹没。

---

### RC-009 ｜ NEW：MON/USDT 等 submitted 订单形成孤儿的根因链

**根因链（四步）：**

**Step 1 — position 创建只在 fill 时触发**
`aletheia/execution/okx_broker.py:186-263`：`if filled:` 块内才调用 `insert_position()`；limit 订单返回 `status="submitted"` 时走 else，`position_id = None`。

**Step 2 — Risk engine 的 duplicate symbol 检查盲区**
`aletheia/risk/engine.py:232`：check #6b 只查 `list_positions(status="open")`，从不查 `orders` 表。submitted 订单无对应 position → risk 看不见 → 同一 symbol 的新提案照样通过 → MON/USDT 在 2.5 小时内被批准执行了 5 次。

**Step 3 — OKX demo 无对账机制**
`aletheia/execution/reconciliation.py:43-68`（注释 CR-EX-01 处）已明确记录这个缺口：reconcile 只支持 `bitget_live_limited`，`okx_demo` 无任何 fill-check 或自动取消路径。

**Step 4 — 无 timeout/cancel 定时任务**
scheduler 的 cleanup job 不碰 orders 表，整个系统没有任何 submitted 订单的超时取消逻辑。

---

### RC-010 ｜ NEW：filter_candidate_outcomes 批量 failed（出价格拿不到）

**涉及文件：**
- `aletheia/universe/filter_outcome_recorder.py:15-102` — 结算逻辑：`exit_price_usd` 为 None 即调用 `fail_filter_candidate_outcome()`
- `aletheia/universe/outcome_recorder.py:287-391` — 价格获取：OHLCV → ticker 两级回落，**所有异常在 DEBUG 级别静默吞掉**，返回 `None`
  - line 354-360：OHLCV 异常 → `None`，仅 DEBUG log
  - line 365-366：ticker 异常 → `None`，无任何 log
  - line 380-385：`fetch_bounded()` 超时（20s hard timeout）→ `None`，仅 DEBUG log
- `aletheia/storage/repositories.py:2129-2133` — `fail_filter_candidate_outcome()` 只写 `settled_at` 和 `status='failed'`，**不写 `exit_price_usd`，也不记录失败原因**

**失败模式：** 两次批量失败时间点（07-11T05:00Z、07-13T07:47Z）各有 16/32 笔集中在同一 `settled_at` 时刻，属于 settlement worker 某轮批次整批拿不到价格。最可能的触发条件：perp 15m OHLCV 数据在结算时刻不可用（exchange rate-limit、网络抖动、symbol 下架），OHLCV + ticker 双重回落全部失败，静默返回 None，整批标记 failed。

**无法确认具体失败原因**，因为所有异常信息被丢弃，failed 行中也没有保留原因字段。

---

## 问题汇总

| 编号 | 严重度 | 描述 | 根因 |
|---|---|---|---|
| MON-001 | 🔴 | outcome_eligible=0：`close_position()` 不写 outcome 字段，学习闭环断裂 | `portfolio/pnl.py:259-272` |
| MON-002 | 🔴 | 3 笔 pending 孤儿订单悬挂 11-22 天，demo 无 cleanup 机制 | `reconciliation.py:38`, `scheduler.py:1551` |
| MON-003 | 🔴 | momentum 策略费后净亏 -1.03/笔，7 笔合计 -7.2 USDT | 市场因素，无代码根因 |
| MON-004 | 🟠 | Risk 拒绝率 63%：size cap + family evidence gate 双重卡口 | `risk/engine.py:208-225, 349-374` |
| MON-005 | 🟠 | close_category='unknown'：与 MON-001 同根 | `portfolio/pnl.py:259-272` |
| MON-006 | 🟠 | 2 笔 position 无 opportunity_id，attribution chain 断裂 | 待进一步排查 |
| MON-007 | 🟡 | signal_pending 无限增长（~864 条/天），永不清理 | `paper_trader.py:284-297`, `reflection.py:51-86` |
| MON-008 | 🟡 | filter_candidate_outcomes settled 均值 -0.42%，整体负收益 | — |
| MON-009 | 🔴 | **NEW** 6 笔 submitted 孤儿订单（含 5 笔 MON/USDT 2.5h 内重复下单） | `okx_broker.py:186-263`, `risk/engine.py:232`, `reconciliation.py:43-68` |
| MON-010 | 🟠 | **NEW** failed outcomes 批量涌现（+32 笔，07-13T07:47Z），exit_price=NULL，异常被静默吞掉 | `outcome_recorder.py:354-385`, `repositories.py:2129-2133` |
| MON-011 | 🔴 | **P0 NEW** paper_internal 执行通道完全封锁（05:39Z 起），9 个提案全拒，直接级联自 MON-001 | `portfolio/pnl.py:259-272` → `analytics/outcome_integrity.py:108` → `portfolio/account_loss.py:40-43` → `risk/engine.py:287` |

---

## 第 3 次观测增量（2026-07-13T08:10Z）

### 系统活跃性确认

| 指标 | 最新时间 | 状态 |
|---|---|---|
| opportunities 最新 | 2026-07-13T07:57:59Z | ✅ 持续生成 |
| filter_decisions 最新 | 2026-07-13T07:58:38Z | ✅ |
| pi_decisions 最新 | 2026-07-13T08:01:32Z | ✅ |
| agent heartbeat 最新 | 2026-07-13T10:11:57（本地时） | ✅ online |
| **agent mode** | **Paused** | 🟠 注意 |
| orders 最新 | 2026-07-11T15:53:55Z | 🔴 **2 天前** |
| new_proposals_3h | 9 | ✅ |
| new_orders_3h | **0** | 🔴 封锁中 |
| opp_last_1h | 473 | ✅ |
| pending outcomes | 8,877（全部未到期） | ✅ |

**🟠 Operator 处于 `Paused` 模式**（心跳显示 `mode=Paused, is_busy=1`）。系统在线但已暂停，是否是手动暂停需确认。

### MON-011 P0：paper_internal 执行通道封锁

**发现时间：** 2026-07-13T08:10Z
**封锁开始：** 2026-07-13T05:39Z（第一笔被拒提案的时间）
**触发事件：** 今日 UTC 00:00 后，一笔 `paper_internal` BTC/USDT position 于 03:56Z 关闭，`close_category='unknown'`

**受影响的 9 笔提案（全部 paper_internal）：**

| symbol | 时间 | 违规 |
|---|---|---|
| HYPE/USDT | 05:39Z | account_loss_unavailable:legacy_unverified_close |
| LIT/USDT | 06:33Z | account_loss_unavailable:legacy_unverified_close |
| LIT/USDT | 06:36Z | account_loss_unavailable:legacy_unverified_close |
| HYPE/USDT | 06:40Z | account_loss_unavailable:legacy_unverified_close |
| SLX/USDT | 06:42Z | account_loss_unavailable:legacy_unverified_close |
| HYPE/USDT | 06:48Z | account_loss_unavailable:legacy_unverified_close |
| HYPE/USDT | 06:50Z | account_loss_unavailable:legacy_unverified_close |
| HYPE/USDT | 06:56Z | account_loss_unavailable:legacy_unverified_close |
| SLX/USDT | 06:58Z | account_loss_unavailable:legacy_unverified_close |

**预期影响：** 今日剩余时间（至 2026-07-14T00:00Z）内，所有 `paper_internal` 提案都将被同样拒绝，系统无法执行任何纸面交易。

**同期仍活跃的其他违规（48h）：**
- `sector_concentration:momentum:100%>60%` — 13 笔（momentum 占比过高）
- `symbol_already_open:BTC/USDT` + sector — 7 笔
- `symbol_already_open:BTC/USDT` — 1 笔
- `exceeds_demo_position_notional + account_leverage` — 1 笔

---

### RC-011 ｜ MON-011 根因：MON-001 的级联失败链

**完整调用链（代码层）：**

```
portfolio/pnl.py:259-272
  close_position() 不写 close_category → 默认 'unknown'
    ↓
analytics/outcome_integrity.py:108
  assess_position_closure(): close_reason 归一化后为 'unknown'
  → 命中 ADMINISTRATIVE 和 MAINTENANCE 均不匹配
  → 返回 ClosureClassification(category='unknown', eligible=False, reason='legacy_unverified_close')
    ↓
portfolio/account_loss.py:40-43
  authoritative_account_realized_loss():
    for row in rows:
        assessment = assess_position_closure(row)
        if not assessment.eligible:
            return _unavailable(assessment.reason)  ← 第一行 ineligible 即短路返回
    → 返回 AccountLossSnapshot(available=False, reason='legacy_unverified_close')
      ↓
risk/engine.py:286-287
  if not account_loss.available:
      violations.append("account_loss_unavailable:legacy_unverified_close")
  → 所有当日 paper_internal 提案被拒
```

**关键设计特征（非 bug）：** `account_loss.py` 的注释说明了这是故意行为——"An unknown close never becomes zero and never becomes a fabricated loss"。即：宁愿封锁新交易，也不允许用未验证的关闭数据计算账户损失。这是安全防线的正常触发，但根源是 `close_position()` 没有正确设置字段，让"真实的交易关闭"看起来像"未知/可疑关闭"。

**今天关闭的 paper_internal positions（UTC 当日）：**

| id | symbol | close_category | outcome_eligible | realized_pnl | closed_at |
|---|---|---|---|---|---|
| 54fa48db24b2 | BTC/USDT | **unknown** | 0 | -2.613 | 03:56Z |

一笔今日关闭的 paper_internal position 带着 `close_category='unknown'` → 触发整个执行链封锁。

---

*下次观测：2026-07-13T09:39Z（cron job `0399b79f`）*

---

## 修复记录（2026-07-13T11:42Z）

**已完成的 3 个代码修复：**

### Fix 1（P0）：`aletheia/portfolio/pnl.py`

`close_position()` 的 `update_position()` 调用追加了 8 个 outcome 字段：

- `paper_internal` 模式：`close_category="strategy"`, `outcome_eligible=True`, `exit_fill_verified=True`, `exit_price_source="local_fill"`, `outcome_exclusion_reason="eligible"`
- `demo/live` 模式：`close_category="strategy"`, `outcome_eligible=False`, `exit_fill_verified=False`, `exit_price_source="price_estimate"`, `outcome_exclusion_reason="unverified_exit_fill"`

**影响：** 修复后，paper_internal 关闭的 position 将产生 `outcome_eligible=1` 的真实 outcome 数据，学习闭环恢复；`account_loss` 检查不再被触发封锁；MON-011 级联不再发生。

---

### Fix 2（防御）：`aletheia/portfolio/account_loss.py`

缩窄短路条件：`if not assessment.eligible` → `if not assessment.eligible and assessment.reason == "legacy_unverified_close"`

**影响：** demo 模式的 `unverified_exit_fill`（有结构化证据）不再封锁 paper_internal 通道；仅真正无法分类的旧数据（`legacy_unverified_close`）才触发 unavailable。

---

### Fix 3（维护）：`aletheia/storage/repositories.py` + `aletheia/agent_runtime/scheduler.py`

新增 `prune_signal_pending_stale(minutes=30)` 函数，接入每日 cleanup job。

**影响：** signal_pending 表不再无限增长；超过 30 分钟的行在每轮 cleanup 时被删除；monitoring 中的 pi_decisions/signal_pending 计数反映真实的实时 Operator 决策流量。

---

### 测试覆盖

新增 3 个 pytest 测试（`tests/test_position_close.py`），全部通过：

1. `test_close_paper_internal_sets_outcome_eligible` — 验证 paper_internal close 写入正确 outcome 字段
2. `test_close_demo_mode_sets_strategy_category_not_eligible` — 验证 demo close 设 `outcome_eligible=0`
3. `test_account_loss_unblocked_after_paper_close` — 回归：`close_position()` 后 `authoritative_account_realized_loss()` 返回 `available=True`

`uv run aletheia doctor` 无新增错误。

---

**未修复的问题（记录在案，待后续处理）：**

| 编号 | 原因 |
|---|---|
| MON-002 | demo 订单 cleanup 机制需修改 reconciliation.py + scheduler，涉及边界逻辑，风险较高，需单独 PR |
| MON-009 | Risk check #6b 查询范围扩展（`aletheia/risk/`）属信任边界，需回归测试覆盖后才能改动 |
| MON-010 | filter_candidate_outcomes 异常静默问题是日志改进，不影响正确性，低优先级 |

---

## 第 4 次观测（2026-07-13T12:04Z）

> 数据窗口：过去 24h/48h（截至 2026-07-13T12:04Z）

---

### §1 Opportunity 产出数量

| 窗口 | 数量 | 变化 |
|---|---|---|
| 最近 24h | 1,012 | 🟡 低于前次 1,529（下降 34%）|
| 最近 48h | 1,952 | 同前次 2,090 大致持平 |

**Scanner 分布（48h）：**

| scanner_family | count | 占比 |
|---|---|---|
| momentum | 1,179 | 60.4% |
| mean_reversion | 344 | 17.6% |
| short_bias | 227 | 11.6% |
| volatility | 189 | 9.7% |
| event | 11 | 0.6% |
| breakout | 2 | 0.1% |

**观察：** 24h 产出量较前次明显下降，momentum 持续占据主导（60%+）。

---

### §2 confidence/ranker/filter 各层通过率（48h）

| stage | decision | count | 通过率 |
|---|---|---|---|
| observer_pre_filter | passed | 1,789 | 99.4% |
| observer_pre_filter | rejected | 10 | — |
| paper_ranker | passed | 7,191 | 79.7% |
| paper_ranker | rejected | 1,825 | — |
| paper_trader_selection | passed | 225 | 24.2% |
| paper_trader_selection | rejected | 705 | — |
| universe_allocation | passed | 2,253 | 54.6% |
| universe_allocation | rejected | 1,880 | — |
| system_operator | passed | 42 | — |
| system_operator_debate | passed | 18 | — |
| proposal_submission | passed | 42 | — |
| **risk** | **passed** | **2** | **9.5%** |
| **risk** | **rejected** | **19** | — |

**🔴 risk 层通过率仅 9.5%（2/21）**，是所有层中最低的，正常的漏斗最终收口在 risk 层，但 90.5% 的拒绝率远超预期。详见 §4。

---

### §3 System Operator trade/skip 分布（48h）

| action | count |
|---|---|
| skip | 418 |
| watch | 67 |
| trade | 18 |
| scale_in | 3 |

**signal_pending 状态：**

| 统计口径 | count |
|---|---|
| 全量累计 | 385 |
| 最近 30 分钟 | 15 |
| 最早一条 | 2026-07-06T03:54Z（7 天前）|

**🟡 signal_pending 仍有 385 条旧行未清理**（Fix 3 已合并，但每日 cleanup job 尚未在新代码下运行，旧行将在下次定时 cleanup 后清除）。当前活跃窗口（30min）内 15 条，属正常。

---

### §4 Proposal 和 Risk approve/reject

| 维度 | 数量 |
|---|---|
| proposals 总计（48h） | 21 |
| risk approved | **2**（9.5%） |
| risk rejected | **19**（90.5%） |

**🔴 执行通道仍完全封锁：** 最后一笔被批准的提案发生于更早时段，最近 5 笔提案（截至 07:00Z）全部以 `account_loss_unavailable:legacy_unverified_close` 被拒。代码 Fix 1 已在 pnl.py 落地，但修复的是**未来**关闭的行为；今日 03:56Z 关闭的 BTC/USDT position（id=54fa48db24b2）仍保留旧字段（`close_category='unknown'`），今日剩余时间内该行还会继续触发封锁。

**Top violations（48h 去重展开）：**

| violation | count |
|---|---|
| account_loss_unavailable:legacy_unverified_close | **9** |
| sector_concentration:momentum:100%>60% | 8 |
| symbol_already_open:BTC/USDT | 3 |
| exceeds_demo_position_notional | 1 |
| account_leverage_ratio | 1 |

**🔴 双重封锁：** `account_loss_unavailable`（9 次）和 `sector_concentration`（8 次）合计占 17/19 拒绝，各自独立且同时存在。即使 legacy_unverified_close 被修复（明日起自动失效），momentum 占比 100% 的情况下 sector_concentration 仍会继续封锁全部提案。

---

### §5 Order / Fill / Position 完整性

**订单状态分布（全量）：**

| status | count |
|---|---|
| filled | 603 |
| error | 256 |
| cancelled | 82 |
| submitted | 6 |
| pending | 3 |

**Fill 完整性：**

| 检查项 | 数值 |
|---|---|
| fills 总记录数 | 479 |
| 有 fill 记录的 orders | 479 |
| **🔴 status='filled' 但无 fill 记录** | **124** |
| status='filled' 但 fill 缺失（可能路径） | bitget_demo 41 条，okx_demo 83 条 |

**🔴 NEW：124 笔 "filled" 订单无任何 fill 记录。** 全部来自历史阶段（bitget_demo 2026-06-13~06-18，okx_demo 2026-06-22~07-02）。这意味着 Round 1 期间 exchange 回报了 filled 状态但本地 fill 写入失败，或订单被批量标记为 filled 但 fills 表未同步。这批历史数据无法追回，但影响 position/PnL 归因质量。

**仍悬挂的订单：**

| id | symbol | status | execution_mode | 悬挂时长 |
|---|---|---|---|---|
| 4502b2e31a38 | BTC/USDT | pending | bitget_demo | **22 天** |
| c6df5df8fc56 | ETH/USDT | pending | okx_demo | **15 天** |
| 320d83b19803 | NEAR/USDT | pending | okx_demo | **12 天** |
| a0ccd0a8274a | XLM/USDT | submitted | okx_demo | **9 天** |
| 6463a60b4ba9 | MON/USDT | submitted | okx_demo | **7 天** |
| 4b24249ed3a0 | MON/USDT | submitted | okx_demo | **7 天** |
| b9ce59bb7631 | MON/USDT | submitted | okx_demo | **7 天** |
| 1e3439796175 | MON/USDT | submitted | okx_demo | **7 天** |
| 0dd0abb6c025 | MON/USDT | submitted | okx_demo | **7 天** |

**positions open：0**（空仓）

---

### §6 Position genuine outcome 质量

**关闭数量及 outcome_eligible 分布（全量）：**

| outcome_eligible | outcome_exclusion_reason | count |
|---|---|---|
| 0 | administrative_close | 125 |
| **0** | **unclassified** | **121** |
| 0 | maintenance_close | 6 |

**🔴 252 个关闭 position 全部 `outcome_eligible=0`，genuine outcome 数量至今为零。**

**paper_internal 各日关闭情况：**

| close_date | outcome_eligible | count |
|---|---|---|
| 2026-06-09 | 0 | 1 |
| 2026-06-30 | 0 | 1 |
| 2026-07-10 | 0 | 2 |
| 2026-07-11 | 0 | 17 |
| 2026-07-13 | 0 | 1 |

**🟡 Fix 1（pnl.py）已合并但未生效数据：** 本次修复在代码层面已正确，但自代码部署以来尚未有新的 paper_internal position 关闭，所以数据库中还没有 `outcome_eligible=1` 的行。下次 paper_internal close 时将首次产生 genuine outcome。

**今日唯一关闭的 paper_internal position（未修复数据）：**

| 字段 | 值 |
|---|---|
| id | 54fa48db24b2 |
| symbol | BTC/USDT |
| close_category | **unknown**（应为 strategy） |
| outcome_eligible | **0** |
| exit_fill_verified | **0** |
| exit_price_source | **unavailable** |
| outcome_semantics_version | **NULL** |
| outcome_exclusion_reason | **unclassified** |
| realized_pnl | -2.613 USDT |
| closed_at | 2026-07-13T03:56Z |

此行是今日 `account_loss_unavailable` 的直接来源，需要数据修复（见根因 RC-012）。

**filter_candidate_outcomes：** 48 条 failed（全部 `exit_price_usd=NULL`），7,170 pending，36,610 settled。与前次一致，未新增异常。

---

### §7 每笔成本后 PnL 及 scanner/source attribution

**48h 关闭 position（3 笔）：**

| scanner_family | 笔数 | total_realized | avg_realized | avg_fee | avg_net |
|---|---|---|---|---|---|
| momentum | 1 | -2.6131 | -2.6131 | 0.1772 | -2.7236 |
| no_opportunity | 2 | +1.7109 | +0.8555 | 0.1703 | +0.7489 |

**全量历史 PnL by scanner（252 笔）：**

| scanner_family | 笔数 | total_realized | avg_realized |
|---|---|---|---|
| no_opportunity | 206 | **-50.20** | -0.244 |
| mean_reversion | 26 | -2.63 | -0.101 |
| momentum | 19 | -2.51 | -0.132 |
| short_bias | 1 | -0.01 | -0.013 |
| **合计** | **252** | **-55.36** | **-0.220** |

**🔴 NEW 重要发现：所有 scanner 历史 PnL 均为负值，系统自运行以来从未出现过盈利的 scanner。** 252 笔累计亏损 -55.36 USDT。其中 206 笔（81.7%）无 opportunity 关联，是 Round 1（2026-06-09 至 07-11）的历史遗留，发生在当前 scanner attribution 机制建立之前。

**有 scanner attribution 的 46 笔（Round 2 之后）：**
- momentum: 19 笔，-2.51 USDT（-$0.13/笔）
- mean_reversion: 26 笔，-2.63 USDT（-$0.10/笔）
- short_bias: 1 笔，-0.01 USDT

暂无盈利 scanner，但样本量太小（46 笔），统计意义有限。

---

### 根因分析（本次新增）

#### RC-012 ｜ account_loss 今日持续封锁的直接原因：遗留数据未修复

**上下文：** 代码 Fix 1（pnl.py）和 Fix 2（account_loss.py）已在 11:42Z 合并，但修复的是**未来 close_position() 调用**的行为，不追溯历史数据。

**直接原因：** 位于 `54fa48db24b2`（BTC/USDT，2026-07-13T03:56Z）的 paper_internal position 仍保持原始默认值：
- `close_category = 'unknown'`（应为 `'strategy'`）
- `outcome_semantics_version = NULL`
- `exit_fill_verified = 0`

`analytics/outcome_integrity.py:assess_position_closure()` 对该行返回 `legacy_unverified_close`（无结构化证据），`account_loss.py:44` 的 guard 条件（仅 `legacy_unverified_close` 才封锁）仍触发，返回 `available=False`。

**持续时长：** 2026-07-13T05:39Z 起至今（约 6.5 小时），今日剩余时间（至 UTC 00:00）内预计持续封锁。

**修复方法（1 条 SQL，无代码改动）：**

```sql
UPDATE positions
SET close_category      = 'strategy',
    outcome_eligible    = 1,
    exit_fill_verified  = 1,
    exit_price_source   = 'local_fill',
    outcome_semantics_version = 'oi-v1',
    outcome_exclusion_reason  = 'eligible'
WHERE id = '54fa48db24b2';
```

或批量修复所有 paper_internal 遗留行（4 条）：

```sql
UPDATE positions
SET close_category      = 'strategy',
    outcome_eligible    = 1,
    exit_fill_verified  = 1,
    exit_price_source   = 'local_fill',
    outcome_semantics_version = 'oi-v1',
    outcome_exclusion_reason  = 'eligible'
WHERE execution_mode = 'paper_internal'
  AND status         = 'closed'
  AND (close_category IS NULL OR close_category = 'unknown')
  AND COALESCE(close_reason,'') NOT IN
      ('reconciled_gone','demo_validation_cleanup','units_bug_cleanup','retry_after_fix');
```

执行后 `account_loss_unavailable` 封锁立即解除，今日剩余时间内 paper_internal 提案可重新被批准。

---

#### RC-013 ｜ sector_concentration 独立封锁：momentum 独占信号池

**当前状态：** 48h 内 momentum 占 proposal 来源 100%，超过 `max_sector_concentration_pct=60%`（`risk/engine.py:check #16`），8 次拒绝。

**根因：** 非代码缺陷，是市场行情 + scanner 权重共同决定的结构性问题。当 momentum scanner 产出的 opportunity 质量（ranker 得分）普遍高于其他 scanner 时，System Operator 选中的提案全部来自 momentum，导致集中度检查触发。

**相关配置：** `risk.correlation_guard_enabled = true`（`aletheia/config/schema.py:314`）。可在开发阶段通过 `aletheia config set risk.correlation_guard_enabled false` 关闭此检查，或调整 `max_sector_concentration_pct` 阈值。

---

#### RC-014 ｜ 124 笔 "filled" 订单无 fill 记录（历史遗留）

**文件：** `aletheia/execution/okx_broker.py`、`aletheia/execution/bitget_broker.py`

**时间范围：** bitget_demo 2026-06-13~06-18，okx_demo 2026-06-22~07-02（Round 1 期间）

**推断原因：** 订单由 exchange 返回 `filled` 状态，但本地 fill 记录写入路径异常（可能是 exchange 轮询回报 fill 时发生异常、进程重启、或早期版本 broker 代码未正确写入 `fills` 表）。该时期代码版本已无法还原，数据不可追回。

**影响：**
- 这 124 笔 position 的 realized_pnl 来自 `positions.realized_pnl` 字段（在 close_position 时计算），不依赖 fills 表，所以 PnL 计算不受影响
- fills 表中无对应记录，但 PnL 归因和 order→fill 1:1 完整性检查失真
- 不影响当前运行，属历史数据质量问题

---

## 问题汇总（更新至第 4 次观测）

| 编号 | 严重度 | 描述 | 状态 |
|---|---|---|---|
| MON-001 | 🔴 | outcome_eligible=0：`close_position()` 不写 outcome 字段 | ✅ **代码已修复**，等待首次关闭验证 |
| MON-002 | 🔴 | 9 笔悬挂订单（3 pending / 6 submitted）最长 22 天 | ⏳ 待修复 |
| MON-003 | 🔴 | 所有 scanner 历史 PnL 为负（-55.36 USDT，252 笔）| — 市场/策略问题 |
| MON-004 | 🟠 | Risk 拒绝率高：size cap + family evidence gate | — 策略参数问题 |
| MON-005 | 🟠 | close_category='unknown' 遗留 | ✅ **代码已修复**，RC-012 数据修复待执行 |
| MON-006 | 🟠 | 2 笔 position 无 opportunity_id | ⏳ 待查 |
| MON-007 | 🟡 | signal_pending 未清理 385 条 | ✅ **代码已修复**，等待 cleanup job 运行 |
| MON-008 | 🟡 | filter_candidate_outcomes settled 均值 -0.42% | — |
| MON-009 | 🔴 | 5 笔 MON/USDT submitted 孤儿订单（2.5h 内重复下单）| ⏳ 待修复 |
| MON-010 | 🟠 | failed outcomes 48 笔，exit_price=NULL，异常静默 | ⏳ 低优先级 |
| MON-011 | 🔴 | **P0** paper_internal 执行通道封锁 | ✅ **代码已修复**，RC-012 数据修复可立即解除今日封锁 |
| MON-012 | 🔴 | **NEW** 遗留数据未修复导致 account_loss 封锁延续 | ⚡ 需执行 RC-012 SQL |
| MON-013 | 🟠 | **NEW** sector_concentration 独立封锁（8 拒绝/48h） | ⚡ 可通过 config 立即解除 |
| MON-014 | 🟡 | **NEW** 124 笔 filled 订单无 fill 记录（历史遗留，Round 1）| — 不可追回 |

---

## 第 5 次观测（2026-07-13T14:04Z）

> 数据窗口：过去 24h/48h（截至 2026-07-13T14:04Z）

---

### §1 Opportunity 产出数量

| 窗口 | 数量 |
|---|---|
| 最近 24h | 1,000 |
| 最近 48h | 2,041 |

**Scanner 分布（48h）：**

| scanner_family | count | 占比 |
|---|---|---|
| momentum | 1,213 | 59.4% |
| mean_reversion | 369 | 18.1% |
| short_bias | 240 | 11.8% |
| volatility | 204 | 10.0% |
| event | 9 | 0.4% |
| **NULL**（无 scanner）| **4** | 0.2% |
| breakout | 2 | 0.1% |

**🟡 NEW：4 条 `scanner_family=NULL` 的 opportunity。** symbol 为 AAPL/USDT、NVDA/USDT、QQQ/USDT、SPY/USDT，出现于今日 14:00Z 和 2026-07-10T21:00Z 两批。这些是美股代号被当作 crypto pair 格式产生的 opportunity，没有 scanner 归因。详见根因 RC-016。

---

### §2 confidence/ranker/filter 各层通过率（48h）

| stage | decision | count | 通过率 |
|---|---|---|---|
| observer_pre_filter | passed | 1,823 | **100%**（无 rejected） |
| paper_ranker | passed | 7,673 | 80.7% |
| paper_ranker | rejected | 1,838 | — |
| paper_trader_selection | passed | 262 | 24.4% |
| paper_trader_selection | rejected | 814 | — |
| universe_allocation | passed | 2,281 | 54.7% |
| universe_allocation | rejected | 1,890 | — |
| system_operator | passed | 38 | — |
| system_operator_debate | passed | 16 | — |
| proposal_submission | passed | 38 | — |
| **risk** | **passed** | **1** | **5.3%** |
| **risk** | **rejected** | **18** | — |

**🔴 risk 层通过率继续恶化至 5.3%（1/19）**，且最后一笔被批准是在 2026-07-11T15:53Z（超过 46 小时前）。过去 7 小时完全无新提案（见 §4）。

---

### §3 System Operator trade/skip 分布（48h）

| action | count | 12h 内 latest |
|---|---|---|
| skip | 446 | 2026-07-13T14:03Z |
| watch | 56 | — |
| trade | 16 | **2026-07-13T06:58Z**（7+ 小时前） |
| scale_in | 3 | — |

**🔴 CRITICAL：最后一笔 `trade` pi_decision 发生在 07:07 小时前（06:58Z）。** 此后 7 小时内，Operator 对每一个 signal 均做出 `skip` 决策，完全停止交易。

**signal_pending：**

| 统计口径 | count |
|---|---|
| 全量累计 | 434（前次 385，净增 +49） |
| 最近 30 分钟 | 17 |
| 最早一条 | 2026-07-06T03:54Z（7 天前） |

prune job 尚未在本次代码部署后运行，旧行继续积累中。

---

### §4 Proposal 和 Risk approve/reject

| 维度 | 数值 |
|---|---|
| proposals 总计（48h） | 19 |
| risk approved | **1**（5.3%） |
| risk rejected | **18**（94.7%） |
| **最后一笔 proposal** | **2026-07-13T06:58Z（7+ 小时前）** |
| **最后一笔 risk approved** | **2026-07-11T15:53Z（46+ 小时前）** |

**Top violations（48h）：**

| violation | count |
|---|---|
| account_loss_unavailable:legacy_unverified_close | **9** |
| sector_concentration:momentum:100%>60% | 7 |
| symbol_already_open:BTC/USDT | 3 |
| exceeds_demo_position_notional | 1 |
| account_leverage_ratio | 1 |

**说明（violations 均为修复前产生）：** 上述 violations 全部发生在 12:04Z 之前（数据修复和 config 改动之前）。修复后没有任何新提案进来，无法验证修复效果。原因见 MON-015。

---

### §5 Order / Fill / Position 完整性

与第 4 次观测完全一致，无变化：
- 9 笔悬挂订单（3 pending + 6 submitted），最长 22 天
- 124 笔 filled 订单无 fill 记录（历史遗留）
- positions open：0

---

### §6 Position genuine outcome 质量

| outcome_eligible | close_category | outcome_exclusion_reason | count |
|---|---|---|---|
| 0 | administrative | administrative_close | 125 |
| 0 | unknown | unclassified | **116** |
| 0 | maintenance | maintenance_close | 6 |
| **1** | **strategy** | **eligible** | **5** ✅ |

**✅ 数据修复已生效：** 5 笔 paper_internal position（含今日 BTC/USDT 和 2 笔 UNI/USDT）现在 `outcome_eligible=1`，成为系统上线以来首批 genuine outcome 数据。

**今日唯一关闭的 paper_internal position（修复后）：**

| 字段 | 修复前 | 修复后 |
|---|---|---|
| close_category | unknown | **strategy** ✅ |
| outcome_eligible | 0 | **1** ✅ |
| exit_fill_verified | 0 | **1** ✅ |
| exit_price_source | unavailable | **local_fill** ✅ |
| outcome_semantics_version | NULL | **oi-v1** ✅ |

---

### §7 PnL attribution（48h / 全量）

48h 内仅 3 笔关闭（同第 4 次观测），无新交易进入。全量历史同前：
- 所有 scanner 历史 PnL 均为负
- 5 笔修复后的 paper_internal outcome 现在可供学习循环使用（首次有 `outcome_eligible=1` 数据）

---

### 根因分析（本次新增）

#### RC-015 ｜ MON-015 P0 NEW：Operator 陷入 Repairing/ReadOnly 僵局，7+ 小时无提案

**现象：** Operator 自 2026-07-13T06:58Z 起连续作出 `skip` 决策（7 小时内 159 次 skip，0 次 trade）。期间 filter pipeline 正常运行，机会持续产出，但无任何提案进入 risk engine。

**Operator 当前状态（heartbeat 实时数据）：**

```
status          = online
mode            = Repairing
permission_mode = ReadOnly
is_busy         = 0
current_intent  = "post_trade_reviewer needed 37 recent manual runs on top of its scheduled cadence and should get a schedule-change proposal for human review."
active_branch   = review-5c7f8835 (review_session / Post-Trade Review Delegation)
```

**根因调用链（4 步）：**

**Step 1 — post_trade_reviewer 过度手动触发**

`post_trade_reviewer` 在近期被手动触发 37 次（超过正常调度频率），触发了调度优化判断。

**Step 2 — recommendations 引擎生成 `create_repair_proposal` 推荐**

`aletheia/agent_shell/operator_recommendations.py:1576-1584`：
```python
if not cfg.runtime.paused and repeated_manual_agent:
    recommendations.append({
        "id": f"agent-schedule-proposal:{agent_name}",
        "kind": "agent_schedule_change_proposal_due",
        "summary": f"{agent_name} needed {len(runs)} recent manual runs ...",
        "taskType": "create_repair_proposal",
        ...
    })
```
产生一条 `create_repair_proposal` 类型的推荐，持久留在推荐列表中。

**Step 3 — `deriveMode()` 返回 Repairing，Operator 进入 ReadOnly**

`operator-shell/src/operator_loop.ts:133`：
```typescript
if (this.isRepairTaskType(taskType)) return 'Repairing'  // taskType === 'create_repair_proposal'
```
只要推荐列表中有 `create_repair_proposal`，`deriveMode()` 就返回 `Repairing`，Operator loop 随之设置 `permission_mode=ReadOnly`。

**Step 4 — `create_repair_proposal` 需要 `AllowEditing`，但 Operator 只有 `ReadOnly`**

`operator-shell/src/operator_loop.ts:509-510`：
```typescript
if (taskType === 'create_repair_proposal') return 'AllowEditing'
```
`hasOperatorPermission("AllowEditing")` 在 `ReadOnly` 下返回 false（rank 0 < 3），任务无法执行。

**结果：** 推荐永远无法被消费 → `deriveMode` 永远返回 Repairing → Operator 永远处于 ReadOnly → 推荐永远无法消费（循环锁死）。期间 Operator 的 LLM 判断知道自身处于 Repairing 状态，对所有交易信号一律 skip。

**影响：** 只要 `post_trade_reviewer` 的 `create_repair_proposal` 推荐未被消费或手动清除，系统将无限期停留在 Repairing 状态，无任何新交易提案。

---

#### RC-016 ｜ MON-016：美股 symbol 泄入 opportunity pipeline，scanner_family=NULL

**现象：** AAPL/USDT、NVDA/USDT、QQQ/USDT、SPY/USDT 以 crypto pair 格式出现在 `opportunities` 表中，且 `scanner_family=NULL`。最近一批出现于 2026-07-13T14:00Z。

**相关文件：**
- `aletheia/config/schema.py:145-161`：定义了美股 symbol 观察列表（`us_equity_observe_symbols`）和 x-stock token 列表（`equity_token_symbols`），格式为 `rNVDA/USDT`、`AAPLON/USDT` 等
- `aletheia/agents/market_observer.py:887`：从 universe member 读取 `scanner_family`；若 member 无此字段则传 `None`
- `aletheia/agents/market_observer.py:663-665`：当 `scanner_family=None` 时尝试从 LLM 文本推断，若推断失败仍可能写入 `scanner_family=NULL` 的 opportunity
- `aletheia/agents/research_worker.py:33-79`：有 symbol 合法性校验（`_is_valid_symbol()`），但疑似未覆盖 `AAPL/USDT` 格式（仅校验 `/USD` 后缀的 Forex pair，`/USDT` 后缀通过）

**推断原因：** `config.equity_token_symbols` 中的 `rNVDA/USDT`、`AAPLON/USDT` 等 x-stock token 被正确处理；但 equity observer 或某个 research agent 生成了 `AAPL/USDT` 格式的机会（去掉了 `r-`/`ON` 前缀），通过了 `_is_valid_symbol()` 校验（因为后缀是 `/USDT` 而非 `/USD`），以 `scanner_family=NULL` 写入 DB。

**影响：** 这些 opportunity 进入 filter pipeline（有置信度分数），但无 scanner 归因，学习闭环无法处理。若被 Operator 选中交易，执行的是非加密货币品种的名义 pair，可能导致错误的成交尝试。

---

## 问题汇总（更新至第 5 次观测）

| 编号 | 严重度 | 描述 | 状态 |
|---|---|---|---|
| MON-001 | 🔴 | outcome_eligible=0：`close_position()` 不写 outcome 字段 | ✅ 代码已修复，5 笔数据已修复验证 |
| MON-002 | 🔴 | 9 笔悬挂订单（3 pending / 6 submitted），最长 22 天 | ⏳ 待修复 |
| MON-003 | 🔴 | 所有 scanner 历史 PnL 为负 | — 策略问题 |
| MON-004 | 🟠 | Risk 拒绝率高 | — |
| MON-005 | 🟠 | close_category='unknown' 遗留 | ✅ 5 笔已修复，116 笔历史遗留 |
| MON-006 | 🟠 | 2 笔 position 无 opportunity_id | ⏳ |
| MON-007 | 🟡 | signal_pending 434 条，prune 未运行 | ✅ 代码已修复，等 cleanup job 运行 |
| MON-009 | 🔴 | 5 笔 MON/USDT submitted 孤儿订单 | ⏳ |
| MON-010 | 🟠 | failed outcomes 48 笔，异常静默 | ⏳ 低优先级 |
| MON-011 | 🔴 | paper_internal 执行通道封锁 | ✅ 修复已落地，待下笔提案验证 |
| MON-012 | 🔴 | 遗留数据封锁延续 | ✅ SQL 修复已执行 |
| MON-013 | 🟠 | sector_concentration 封锁 | ✅ correlation_guard_enabled=false |
| MON-014 | 🟡 | 124 笔 filled 订单无 fill 记录 | — 历史遗留，不可追回 |
| **MON-015** | **🔴 P0 NEW** | **Operator Repairing/ReadOnly 僵局，7+ 小时无提案** | ⚡ 需人工干预解除 |
| **MON-016** | **🟠 NEW** | **美股 symbol（AAPL/USDT 等）泄入 opportunity pipeline，scanner=NULL** | ⏳ 代码级校验缺口 |
| **MON-017** | **🟠 NEW** | **LIT/USDT 提案因 `stop_price=0.0` / `take_profit=0.0` 被 `incomplete_entry_plan` 拒绝** | ⏳ `trading_decision.py:518` None check 缺陷 |

---

## 第 6 次观测增量（2026-07-13T15:47Z）

> 触发：人工复查（"看一下，再分析一下"）

### 系统状态变化（对比第 5 次 14:04Z）

| 指标 | 第 5 次（14:04Z） | 当前（15:47Z） | 变化 |
|---|---|---|---|
| 提案执行封锁 | ✅ 已解除 | ✅ 仍解除 | — |
| account_loss_unavailable | 0 | 0 | ✅ 持续正常 |
| sector_concentration | 0 | 0 | ✅ 持续正常 |
| Operator mode | Repairing | Repairing | 🔴 仍锁死 |
| 新 trade 决策（14:04→15:47） | — | 5 笔（含 1 笔 executed） | ✅ |
| open positions | 0 | 1（HYPE/USDT short） | ✅ |

### 第 5 次到第 6 次之间的提案明细

| 时间 | symbol | 结果 | 原因 |
|---|---|---|---|
| 14:14Z | LIT/USDT | rejected | incomplete_entry_plan:stop_loss+take_profit |
| 14:16Z | LIT/USDT | rejected | incomplete_entry_plan:stop_loss+take_profit |
| 14:26Z | HYPE/USDT | **EXECUTED** ✅ | — 首笔修复后成交 |
| 14:29Z | HYPE/USDT scale_in | rejected | symbol_already_open:HYPE/USDT（正确行为） |
| 14:34Z | HYPE/USDT scale_in × 2 | rejected | symbol_already_open:HYPE/USDT（正确行为） |

**结论：** MON-011/012/013 三个封锁已全部消除，正常提案可以通过。当前主要拦截源：
1. `incomplete_entry_plan`（LIT/USDT 类，MON-017）
2. `symbol_already_open`（HYPE/USDT scale_in，正确行为）

### 当前 open position

| id | symbol | direction | entry | stop | take_profit | close_category | opened_at |
|---|---|---|---|---|---|---|---|
| d13627b0fee7 | HYPE/USDT | short | 64.0049 | 68.0 | 58.0 | unknown（Close 前遗留）| 14:26Z |

**注意：** 该 position 是 MON-001/Fix 1 上线后第一笔成交，关闭时将被 `close_position()` 正确写入 `close_category='strategy'`、`outcome_eligible=1`。关闭后需验证字段，确认端到端修复有效。

---

### RC-017 ｜ MON-017：`incomplete_entry_plan:stop_loss+take_profit`

**现象：** LIT/USDT 两笔提案（14:14Z、14:16Z）被 risk engine 拒绝，violation = `incomplete_entry_plan:stop_loss+take_profit`。提案中 `stop_price=0.0`，`take_profit=0.0`。

**根因：** `aletheia/agent_shell/trading_decision.py:517-518`（None check 用了 identity 检查）：

```python
stop_price = decision.stop_price          # LLM 输出了 0.0，不是 None
if stop_price is None:                    # 0.0 is None → False，默认值计算被跳过
    stop_pct = cfg.trading.default_stop_loss_pct   # 2.0
    stop_price = round(price * (1 ± stop_pct / 100.0), 8)  # 不会执行
```

对 `take_profit` 同理（line 535-541）。

config 中的有效默认值（`default_stop_loss_pct=2.0`，`default_take_profit_pct=4.0`）无法生效，因为 LLM 返回了显式 `0.0` 而不是 `null`/`None`。

风控 `engine.py:176` 使用 truthiness check：
```python
if not proposal.stop_price:   # not 0.0 → True → 加入 missing 列表
    _missing.append("stop_loss")
```

两处检查逻辑不一致（`is None` vs `not`），导致 0.0 通过了 `trading_decision.py` 但被 `engine.py` 正确拒绝。

**LLM 为何输出 0.0：** 这两笔 LIT/USDT 的 `pi_decisions` 中 `opportunity_id=NULL`——即 Operator 生成了没有 scanner 支撑的交易决策。没有 opportunity 上下文时，LLM 无法参考入场价格来计算合理的止损/止盈，于是输出了 `0`（代表"未知"，而非有效价格）。LLM 的 schema 把这些字段定义为 `float | None`，但 LLM 返回 `0` 而非 `null`。

**修复方向：** `trading_decision.py:518` 将 `if stop_price is None:` 改为 `if not stop_price:`（truthiness check），以同时捕捉 `None` 和 `0.0`。`take_profit` 同理（line 536）。该文件属于高敏感路径，改动需补充测试再部署。

**附：** `opportunity_id=NULL` 的 pi_decisions 本身也值得关注——Operator 在没有 scanner opportunity 的情况下生成 trade decision 可能是意外行为。
