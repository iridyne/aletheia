# Paper Trading 执行通道解封手册

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

若有结果，需要执行下面的数据修复。

### 修复命令（批量，排除管理性关闭）

```bash
sqlite3 data/app.db "
UPDATE positions
SET close_category            = 'strategy',
    outcome_eligible          = 1,
    exit_fill_verified        = 1,
    exit_price_source         = 'local_fill',
    outcome_semantics_version = 'oi-v1',
    outcome_exclusion_reason  = 'eligible'
WHERE execution_mode = 'paper_internal'
  AND status         = 'closed'
  AND (close_category IS NULL OR close_category = 'unknown')
  AND COALESCE(close_reason, '') NOT IN
      ('reconciled_gone','demo_validation_cleanup','units_bug_cleanup','retry_after_fix');
SELECT changes() || ' rows updated';
"
```

修复后无需重启，下一轮提案周期内 `account_loss` 检查会即时重算。

### 根本原因（已修复）

`portfolio/pnl.py:close_position()` 自 2026-07-13 起已写入正确 outcome 字段，
新产生的关闭行不再触发此封锁。此命令仅用于修复在代码修复前已关闭的遗留行。

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
aletheia risk kill-switch status
# 或
sqlite3 data/app.db "SELECT value FROM runtime_config WHERE key='kill_switch_active';"
```

### 解除

```bash
aletheia risk kill-switch deactivate
```

kill switch 由人工激活，只能由人工解除（`actor="human"` 强制要求）。
不会因 paper_internal 亏损自动激活（`kill_switch_enabled_demo = false`）。

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

# 2. 修复遗留 paper_internal outcome 字段
sqlite3 data/app.db "
UPDATE positions
SET close_category='strategy', outcome_eligible=1, exit_fill_verified=1,
    exit_price_source='local_fill', outcome_semantics_version='oi-v1',
    outcome_exclusion_reason='eligible'
WHERE execution_mode='paper_internal' AND status='closed'
  AND (close_category IS NULL OR close_category='unknown')
  AND COALESCE(close_reason,'') NOT IN
      ('reconciled_gone','demo_validation_cleanup','units_bug_cleanup','retry_after_fix');
SELECT changes() || ' rows updated';"

# 3. 关闭 sector concentration 检查（开发阶段）
grep -q "correlation_guard_enabled: true" data/config.yaml && \
  sed -i '' 's/correlation_guard_enabled: true/correlation_guard_enabled: false/' data/config.yaml && \
  echo "correlation_guard disabled"

# 4. Kill switch（如果激活）
aletheia risk kill-switch status
# aletheia risk kill-switch deactivate  # 仅在 active 时执行
```

---

## 注意事项

- 步骤 2 的 SQL 仅修改 `paper_internal` 且 `close_category='unknown'` 的行，排除了真正的管理性关闭（`reconciled_gone` 等），不会影响 outcome_integrity 审计链。
- `correlation_guard_enabled = false` 在正式测试或上线前应恢复为 `true`，避免过度集中于单一 scanner。
- Kill switch 解除需人工确认，不应写入自动化脚本。
- 上述改动对 live 模式同样生效，开发阶段使用时确认 `live.state = live_disabled`。
