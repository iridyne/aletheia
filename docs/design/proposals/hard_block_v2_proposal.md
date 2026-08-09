---
id: hard-block-v2
status: draft
related_contracts:
  - ../contracts/risk_engine_contract.md
  - ../contracts/filter_evidence_contract.md
  - ../contracts/execution_contract.md
supersedes: []
---

# Hard Block v2: Multi-Dimensional Factor Blocking

## Summary

当前 `factor_feedback` 的 `hard_blocked` 分类只使用两个维度（`win_rate < 30%` + `expectancy < -0.5%` + `n >= 10`），**无法识别"高胜率但连续巨亏"和"期望为负但胜率不低"的家族**。

7 日数据显示：
- `derivatives` family：n=48, win_rate=35.4%, expectancy=−0.61% → **卡在 `blocked_candidate`，不触发 hard_blocked**
- `short_bias` family：n=3, win_rate=0% → **卡在 `watch`，样本不足**
- `LINK/USDT`：5 连亏 → **没有任何维度能识别**
- `BNB/USDT`：4 连亏 → **同上**

当前 `blocked_candidate` 只扣 10 分（`factor_feedback.py:92`），不拦截。信号面标红、执行面照做。

本 Proposal 新增三个维度到 `hard_blocked` 门控：
1. **连续亏损阈值**（≥ 5 笔）
2. **cost-aware avg_net**（扣费后期望）
3. **30 天滚动窗口**（替代全量统计）

同时把 `blocked_candidate` 升级为**可拦截状态**（但只针对新入场，不追溯已持仓位）。

## Current fact

| 项 | 当前位置 | 当前行为 |
|---|---|---|
| 分类逻辑 | `aletheia/analytics/factor_feedback.py:214-248` (`_classify_factor`) | `watch` → `hard_blocked` → `blocked_candidate` → `active` → `preferred` |
| hard_blocked 门槛 | `factor_feedback.py:226-228` | `n >= 10 AND win_rate < 0.30 AND expectancy < -0.5` |
| 执行层拦截 | `aletheia/agents/paper_trader_ranker.py:531-541` | **只有** `hard_blocked` + `factor_block_authorized` 才拒绝；`blocked_candidate` 只扣分 |
| score_delta | `factor_feedback.py:91-94` | `blocked_candidate` / `hard_blocked` 都得 `-10.0`；`preferred` 得 `+5.0` |
| 连续亏损计算 | `factor_feedback.py:251-262` (`_max_consecutive_losses`) | **已计算但未被任何分类消费** |
| cost-aware avg_net | `aletheia/analytics/factor_feedback.py` 未使用 | 当前 expectancy 是扣费前的 `total_pnl_pct / trade_count`；calibration 表里的 `avg_net` 才是扣费后 |

**已验证的 7 日事实（来自 `inspect scorecard` + `inspect calibration`）**：
- `derivatives`: 48 笔, 35.4% WR, −0.61% expectancy, **alpha vs BTC −2.1%**
- `short_bias`: 3 笔, 0% WR, −3.01% expectancy, **alpha vs BTC −3.62%**
- `momentum`: 32 笔, 34.4% WR, 0.00% expectancy
- `mean_reversion`: 1 笔, 100% WR, +0.32% expectancy（样本不足）

**LINK 5 连亏 / BNB 4 连亏**：`_max_consecutive_losses` 已能算出这些值，但 `_classify_factor` 不消费它。

## Goals and non-goals

### Goals

1. 让 **连续亏损 ≥ 5 笔**的 factor 自动进入 `hard_blocked`，无论胜率高低
2. 让 **cost-aware avg_net < 0** 成为 hard_blocked 的必要条件之一（扣费后期望必须为正才能避免被 block）
3. 让统计窗口限制在 **最近 30 天**，避免"两年前的好交易"保护"现在的坏家族"
4. 让 `blocked_candidate` 可以拦截新入场（但**不追溯已开仓仓位**）

### Non-goals

- **不动** `paper_trader_ranker.py` 的 `authorize_candidate_block` 路径（trust boundary 已经正确）
- **不动** `risk/` 家族的 guard 体系（独立路径）
- **不动** 已开仓的仓位（`positions` 表里 `status=open` 的 1 笔 XRP 不追溯）
- **不动** `_MIN_TRADES_FOR_PREFERRED`（+5 分那边不改）
- **不引入** 新数据表、新 exchange、新基础设施

## Proposed design

### 1. 新增阈值常量（`factor_feedback.py:210-211` 旁）

```python
# 现有
_MIN_TRADES_FOR_HARD_BLOCKED = 10
_MIN_TRADES_FOR_HARD_BLOCKED_V2 = 30   # NEW: 统计显著性更高的样本门槛

# 新增
_HARD_BLOCK_WR_THRESHOLD = 0.30         # 保留（win_rate < 30%）
_HARD_BLOCK_EXPECTANCY_THRESHOLD = -0.005  # 保留（expectancy < -0.5%）
_HARD_BLOCK_AVG_NET_THRESHOLD = 0.0      # NEW: cost-aware avg_net 必须 < 0
_HARD_BLOCK_MAX_LOSS_STREAK = 5         # NEW: 连续亏损 ≥ 5 自动 hard_blocked
_HARD_BLOCK_WINDOW_DAYS = 30            # NEW: 只看最近 30 天
```

### 2. 修改 `_classify_factor`（`factor_feedback.py:214-248`）

```python
def _classify_factor(
    trade_count: int,
    min_trades: int,
    expectancy: float,
    win_rate: float,
    profit_factor: float,
    cost_aware_avg_net: float = 0.0,   # NEW 参数
    max_consecutive_losses: int = 0,   # NEW 参数
    window_days: int = 30,             # NEW: 实际使用的观察窗口
) -> tuple[str, str]:
    """Classify a factor into watch/blocked_candidate/hard_blocked/active/preferred."""
    if trade_count < min_trades:
        return "watch", f"insufficient_data({trade_count}/{min_trades})"

    # NEW: 连续亏损 >= 5 且样本足够 → 直接 hard_blocked（无论胜率）
    if (max_consecutive_losses >= _HARD_BLOCK_MAX_LOSS_STREAK
            and trade_count >= 15):
        return "hard_blocked", (
            f"loss_streak={max_consecutive_losses}_n={trade_count}"
        )

    # P1-4: hard_blocked — 多维度门控
    if (trade_count >= _MIN_TRADES_FOR_HARD_BLOCKED
            and win_rate < _HARD_BLOCK_WR_THRESHOLD
            and expectancy < _HARD_BLOCK_EXPECTANCY_THRESHOLD
            and cost_aware_avg_net < _HARD_BLOCK_AVG_NET_THRESHOLD):  # NEW: 必须扣费后也亏
        return "hard_blocked", (
            f"hard_blocked:wr={win_rate:.2%}_n={trade_count}"
            f"_avg_net={cost_aware_avg_net:.4f}"
        )

    if expectancy < 0 and win_rate < 0.40:
        return "blocked_candidate", (
            f"negative_expectancy({expectancy:.4f})_low_winrate({win_rate:.2%})"
        )

    # ... 后续逻辑不变（preferred / active）
```

### 3. `blocked_candidate` 升级为可拦截（`paper_trader_ranker.py:531-541`）

当前：
```python
if factor_block_authorized and factor_signal.get("status") == "hard_blocked":
    # 拒绝
```

修改为：
```python
if factor_block_authorized and factor_signal.get("status") in {"hard_blocked", "blocked_candidate"}:
    # 拒绝（但只针对新入场，不追溯已开仓仓位）
```

**注意**：`positions` 表里已有的 open 仓位**不走这个路径**——它们走的是 `position_manager.py` 的 `pending_close` 链路。这条修改只影响 **ranker 阶段的新 candidate**，不影响已开仓的单子。

### 4. 计算 `cost_aware_avg_net` 和 `max_consecutive_losses`

在 `_compute_group_stats`（`factor_feedback.py:155-207`）里新增：

```python
cost_aware_avg_net = _cost_aware_avg_net(rows)  # 需要从 calibration 表或 outcome_economics 拿
max_consecutive_losses = _max_consecutive_losses(rows)  # 已存在，直接传
```

`cost_aware_avg_net` 的实现：
- 方案 A（推荐）：复用 `aletheia/analytics/confidence_calibration.py` 的 `avg_net`（按 family × side × market × horizon 切片）
- 方案 B：直接读 `positions.realized_pnl` 减去 `outcome_economics.funding_cost` + `fee`

### 5. 滚动窗口过滤

在 `build_factor_feedback`（`factor_feedback.py:31`）的 `rows` 入口处加时间过滤：

```python
cutoff = datetime.now(timezone.utc) - timedelta(days=_HARD_BLOCK_WINDOW_DAYS)
rows = [r for r in rows if _parse_iso(r.get("closed_at")) >= cutoff]
```

**注意**：这只影响 `_classify_factor` 的输入，不影响 `family_scorecard` 的 7 日/21 日窗口（scorecard 是独立的）。

## Trust and complexity review

### 受影响的 trust boundary

| 边界 | 影响 | 说明 |
|---|---|---|
| `aletheia/agents/paper_trader_ranker.py` | ✅ 修改 | `blocked_candidate` 现在能拦截新入场 |
| `aletheia/analytics/factor_feedback.py` | ✅ 修改 | 新增参数、新增维度 |
| `aletheia/risk/` | ❌ 不改 | guard 路径独立 |
| `aletheia/execution/` | ❌ 不改 | Execution Gateway 不动 |
| `aletheia/portfolio/` | ❌ 不改 | 已开仓仓位不追溯 |

### 新增 hidden state / coupling

- **无新增持久化状态**：所有维度从现有 `positions` / `outcome_economics` / `shadow_trades` 表派生
- **新增 coupling**：`factor_feedback` 现在依赖 `confidence_calibration`（或 `outcome_economics`）的 cost-aware 数据；如果这个数据缺失，graceful fallback 到"不拦截"

### 为什么更小的改变不够

- 只改 `win_rate` 阈值（比如从 30% 降到 35%）→ 会误伤 `mean_reversion` / `event` 这些正期望家族
- 只加连续亏损维度 → 不解决"胜率 35% 但扣费后期望为负"的家族（如 derivatives）
- 只改 cost-aware → 不解决"期望还行但连续 5 笔都在亏"的情况（如 LINK）

三者必须**同时**到位，才能覆盖当前系统的盲区。

## Verification contract

本 Proposal 的 executable evidence（成为 current Product Truth 之前必须通过）：

1. **回归测试**：`tests/specs/spec_factor_feedback.py`（假设存在）或新建 `tests/specs/spec_hard_block_v2.py`
   - 用例 1：`n=30, wr=25%, expectancy=-0.6%, avg_net=-0.2%, max_streak=3` → `hard_blocked`
   - 用例 2：`n=30, wr=40%, expectancy=-0.3%, avg_net=-0.1%, max_streak=6` → `hard_blocked`（连续亏损触发）
   - 用例 3：`n=30, wr=25%, expectancy=-0.6%, avg_net=+0.1%, max_streak=3` → `blocked_candidate`（cost-aware 为正，不硬 block）
   - 用例 4：`n=20, wr=25%, expectancy=-0.6%, avg_net=-0.2%, max_streak=3` → `watch`（样本不足）

2. **30 天观察窗口验证**：在新 policy epoch 上跑 30 天，比较：
   - `hard_blocked` 触发次数 vs 当前 epoch
   - `derivatives` / `short_bias` 家族的**实际入场次数**应降为 0
   - `blocked_candidate` 拦截次数
   - 不应影响 `mean_reversion` / `event` 正期望家族的入场

3. **已开仓仓位不追溯**：验证 `positions` 表里 `status=open` 的仓位在 epoch 切换后**不触发**任何新的平仓（只走原有 stop/tp/pending_close 链路）

## Rollout and rollback

###  rollout（安全采纳）

1. **起新 policy epoch**：`policy_epoch_v2_hard_block`（不能在原 epoch 上改）
2. **先 shadow 跑 7 天**：新分类逻辑**只记录**，不拦截；比较 shadow 结果 vs 实际执行
3. **再正式拦截 7 天**：开启 `blocked_candidate` 拦截；监控 `candidate_supply.executable` 是否降到 0（干旱）
4. **全量生效**：30 天后评估

### rollback（回到旧行为）

- **触发条件**：
  - `candidate_supply` 连续 3 天 `executable=0`（所有家族都被 block，系统停摆）
  - `mean_reversion` / `event` 正期望家族被误 block（误伤率 > 10%）
  - 任何 `blocked_candidate` 拦截被 audit 为错误（trace 里出现 `skip_reason=factor_hard_blocked` 但事后证明应该入场）
- **回滚动作**：切回原 `policy_epoch`（不是代码 revert，是 epoch 切换）
- **回滚代价**：30 天观察窗口作废，需要重新攒数据

## Resolution

**本 Proposal 当前为 `draft`，等待 owner 审批。**

如果 accepted：
- 规范内容转入 `filter_evidence_contract.md`（第 3 节"质量/排序门"）
- `factor_feedback.py` 的具体实现进入 PR review
- 本 Proposal 保持 `accepted` 状态作为决策历史

如果 rejected：
- 记录 reject reason 到 `docs/governance/decisions/DECISIONS.md`
- 本 Proposal 改为 `rejected` 状态保留

---

**跟踪**：
- GitHub Issue: （未创建，待 owner 决定）
- PR: （未创建）
- Owner: kerwin
- Last updated: 2026-08-03
