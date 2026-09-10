# Archived Paper Internal development unblock runbook

> **Archived — do not execute.** This records retired simulator operations. Its
> commands, configuration advice and repair paths are historical and do not
> authorize current account, Risk or database changes. Current operations use
> [RUNBOOK.md](../../operations/runbooks/RUNBOOK.md).

> 适用场景：开发/迭代阶段 paper_internal 提案被批量拒绝，需快速恢复交易流水。
> 来源：2026-07-13 MON-011/012/013 事件，RC-011/012/013 根因分析。

---

## 快速诊断

先查当日 risk_decisions 的拒绝原因，确认属于哪类封锁：

```bash
sqlite3 data/app.db "
SELECT json_each.value as violation, COUNT(*) as cnt
FROM risk_decisions, json_each(risk_decisions.violations)
WHERE date(created_at) = date('now')
  AND approved = 0
GROUP BY violation ORDER BY cnt DESC;
"
```

常见封锁原因及处置方式见下方各节。

---

## 封锁类型 A：`account_loss_unavailable:legacy_unverified_close`

### 触发条件

今日（UTC）内有一笔 `paper_internal` 的 closed position 的 `close_category = 'unknown'`
（在 2026-07-13 修复 pnl.py 之前，`close_position()` 不写 outcome 字段，所有旧行均为此状态）。

`analytics/outcome_integrity.py:classify_closure()` 对该行返回 `legacy_unverified_close`，
`portfolio/account_loss.py:44` 对此原因短路返回 `available=False`，
`risk/engine.py:check #8` 记录 `account_loss_unavailable` violation，拒绝所有后续 paper_internal 提案。

### 检查哪些 position 需要修复

```bash
sqlite3 data/app.db "
SELECT id, symbol, close_category, outcome_eligible, closed_at
FROM positions
WHERE execution_mode = 'paper_internal'
  AND status         = 'closed'
  AND date(closed_at) = date('now')
  AND (close_category IS NULL OR close_category = 'unknown');
"
```

若有结果，**不要** 通过 SQL 批量修改 outcome 字段。

> ⛔ **原手册在此处包含设置 `outcome_eligible=1, exit_fill_verified=1` 的批量 UPDATE，
> 已违反 `execution_contract.md §17`（不能用 UPDATE 覆盖未经验证的 outcome eligibility），
> 并于 2026-09-08 移除（#2995）。**
>
> `close_category='unknown'` 的历史行确实没有经过验证的 fill 原件，应保持
> `outcome_eligible=false`。若需为有完整 fill 记录的历史行重建 outcome，
> 使用 `maintenance/historical_accounting.py`（条件满足才重建，不会盲目赋值）。

### 根本原因（已修复）

`portfolio/pnl.py:close_position()` 自 2026-07-13 起已写入正确 outcome 字段，
新产生的关闭行不再触发此封锁。若仍出现 `close_category='unknown'` 的新行，
说明有代码回归，应排查 `pnl.py:close_position()`，而非修改数据库。

---

## 封锁类型 B：`sector_concentration:momentum:XX%>60%`

### 触发条件

当前 open positions 中某单一 scanner_family 占比超过 `max_sector_concentration_pct`（默认 60%）。
在开发阶段 momentum scanner 经常独占信号池，占比 100%，导致所有提案被拒。

`risk/engine.py:check #16`（`correlation_guard_enabled = true` 时生效）。

### 临时禁用（开发阶段）

```bash
# 确认当前值
grep "correlation_guard_enabled" data/config.yaml

# 禁用
sed -i '' 's/correlation_guard_enabled: true/correlation_guard_enabled: false/' data/config.yaml

# 或直接编辑 data/config.yaml 第 253 行
```

config 改动无需重启，gateway 会在下次 config 读取时生效（通常 <1min）。

### 恢复（生产/正式测试时）

```bash
sed -i '' 's/correlation_guard_enabled: false/correlation_guard_enabled: true/' data/config.yaml
```

---

## 封锁类型 C：kill switch 已激活

### 检查

```bash
aletheia risk status
# 或
sqlite3 data/app.db "SELECT value FROM runtime_config WHERE key='kill_switch_active';"
```

`aletheia risk status` 显示全局 kill switch、demo 和 live 制动器的状态，以及当前风险边界。

### 解除

```bash
aletheia risk resume
```

kill switch 由人工激活，只能由人工解除（`actor="human"` 强制要求）。
不会因 paper_internal 亏损自动激活（`kill_switch_enabled_demo = false`）。

> **注意**：`aletheia risk` 只有 `status`、`kill`、`resume` 三个子命令，
> 没有 `kill-switch` 嵌套命令组。全局制动器与 demo/live 风险边界共享同一
> `risk status` 视图。

---

## 封锁类型 D：`symbol_already_open:XXX/USDT`

### 触发条件

同一 symbol 已有 open position 或悬挂的 submitted 订单（risk check #6b）。
在有孤儿订单（MON-009）的情况下，历史重复提交造成多个 submitted 订单，
封锁后续所有同 symbol 提案。

### 检查悬挂订单

```bash
sqlite3 data/app.db "
SELECT id, symbol, status, execution_mode, created_at
FROM orders
WHERE status IN ('pending', 'submitted')
ORDER BY created_at;
"
```

### 临时清理（谨慎：会丢弃这些订单）

```bash
sqlite3 data/app.db "
UPDATE orders SET status='cancelled'
WHERE status IN ('pending','submitted')
  AND datetime(created_at) < datetime('now', '-1 day');
SELECT changes() || ' orders cancelled';
"
```

---

## 一键快速恢复（综合）

如果以上几种封锁同时存在，按顺序执行：

```bash
# 1. 查当日封锁原因
sqlite3 data/app.db "
SELECT json_each.value as violation, COUNT(*) as cnt
FROM risk_decisions, json_each(risk_decisions.violations)
WHERE date(created_at) = date('now') AND approved=0
GROUP BY violation ORDER BY cnt DESC;"

# 2. 封锁类型 A 已退役 — 见上方说明，不执行 outcome 字段批量修改

# 3. 关闭 sector concentration 检查（开发阶段）
grep -q "correlation_guard_enabled: true" data/config.yaml && \
  sed -i '' 's/correlation_guard_enabled: true/correlation_guard_enabled: false/' data/config.yaml && \
  echo "correlation_guard disabled"

# 4. Kill switch（如果激活）
aletheia risk status
# aletheia risk resume  # 仅在 kill switch active 时执行
```

---

## 注意事项

- 步骤 2 的 SQL 仅修改 `paper_internal` 且 `close_category='unknown'` 的行，排除了真正的管理性关闭（`reconciled_gone` 等），不会影响 outcome_integrity 审计链。
- `correlation_guard_enabled = false` 在正式测试或上线前应恢复为 `true`，避免过度集中于单一 scanner。
- Kill switch 解除需人工确认，不应写入自动化脚本。
- 上述改动对 live 模式同样生效，开发阶段使用时确认 `live.state = live_disabled`。
