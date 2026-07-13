# Minimum Profit Loop Audit — 2026-07-13

**状态：** 已确认死锁 (deadlock) — 系统自锁约 34.5 小时，无法自动恢复。  
**影响：** 提案完全停止；`signal_pending` 进入 System Operator 后全部返回 skip；无新开仓。

---

## 根本原因链（已通过 CLI + DB 验证）

### Layer 1 — 触发点：24h 信号质量降级

```
24h universe signal win_rate = 37.2% (avg_net = -1.804%)
threshold = 40%
→ signal_status = "degraded"
```

代码位置：`aletheia/agent_shell/trading_context.py:961`  
`elif win_rate < 40: signal_status = "degraded"`

### Layer 2 — 自动收紧置信度阈值

`operator_loop.py` Stage 3 (line 1189) 规则：当 `signal_status == "degraded" and win_rate < 40`，
触发 `tighten_confidence` action，将 `min_confidence_to_trade` 从 `medium` 升至 `high`。

```
data/config.yaml: min_confidence_to_trade: high   (schema default: medium)
```

代码位置：`aletheia/agent_shell/operator_loop.py:1189-1196`

### Layer 3 — Confidence Gate 拒绝 89% 的候选

24h filter_decisions 统计：

| filter_name             | decision | reason_code       | count |
|------------------------|----------|-------------------|-------|
| ranker_confidence_gate | rejected | confidence_below  | 1039  |
| ranker_confidence_gate | passed   | confidence_at_or_above | 133 |

通过率仅 11.3%。当前 13 个 universe 成员中，只有 LAB/USDT 有 `confidence=high`。
其余所有机会均因 `confidence=medium` 在 ranker 被过滤。

代码位置：`aletheia/agents/paper_trader_ranker.py:184-195`

### Layer 4 — System Operator LLM 仍然 SKIP

唯一通过过滤的候选 LAB/USDT 进入 System Operator 后，LLM 在 prompt 中看到：

```
⚠️ SIGNAL DEGRADATION: 24h universe signal_accuracy_24h=37.2% (below 40%).
Consider: reducing position sizing, tightening stops, skipping low-conviction trades.
```

System Operator 连续选择 SKIP（最近 250 次 skip，仅 2 次 trade，last_decision = 1min ago: SKIP）：

```
"Multiple factors argue against opening new positions right now.
Signal accuracy has degraded to 37.2% (below the 40% threshold)..."
```

代码位置：`aletheia/agent_shell/trading_decision.py:1152`（health alert 注入）

### Layer 5 — 自动解锁 (Rule 5A) 被阻断，形成死锁

`operator_loop.py` Rule 5A（line 1214）会在无交易 ≥12h 时自动 loosen_confidence，
但条件要求 `win_rate_7d >= 40`：

```python
if (assessment.hours_since_last_trade >= 12          # ✓ ~34.5h 无交易
    and param_drift["min_confidence_to_trade"] == "tightened"  # ✓ 当前 tightened
    and (win_rate_7d is None or win_rate_7d >= 40)   # ✗ 7d WR = 9.5% < 40%
    and not kill_switch_active):
```

**实际 7d 交易胜率 = 9.5%**（21 个已关闭仓位中仅 2 个盈利，总 PnL = -$4.19）。  
loosen 触发条件永远为 False → 系统无法自动退出死锁。

---

## 完整死锁证明

```
信号质量差 (37.2%) 
  → Layer 2: tighten_confidence 触发 → min_confidence=high
  → Layer 3: 89% 候选被 confidence gate 过滤
  → Layer 4: System Operator 因同一 37.2% 信号质量 SKIP 仅剩候选
  → 无交易 → 无新 fills → WR 不变 (仍 9.5%)
  → Layer 5: loosen Rule 5A 要求 WR≥40%, 永远不满足
  → 回到 Layer 2: 每次 operator_loop 继续检测 37.2% < 40% ...
```

系统既不能开仓修复信号质量，又不能自动解除 tighten，完全自锁。

---

## 证据摘要（运行时数据，截至 2026-07-13T03:10 UTC）

| 指标 | 值 |
|------|-----|
| 24h signal win_rate | 37.2% (avg_net=-1.804%) |
| 7d position win_rate | 9.5% (2/21 wins) |
| 7d total PnL | -$4.19 |
| min_confidence_to_trade | high (被自动收紧) |
| 24h confidence_below rejections | 1039 / 1172 (89%) |
| 24h operator skip decisions | 250 |
| 24h trade decisions | 2 (scale_in on existing position, both risk_rejected) |
| 距上次成功开仓 | ~34.5h |
| 上次提案时间 | 2026-07-12T03:04 (BTC/USDT long, risk_rejected) |
| 活跃 guard 阻断 | 0 (guards 不是当前问题) |
| kill switch | inactive |
| regime | SIDEWAYS conf=56% |

---

## 次要问题（不是当前主因，但存在）

1. **Debate 系统持续下调 conviction**：22 个 debated skips，初始 conviction 52-62 被调整至 38-52，
   多个跌破 `_MIN_CONVICTION_TO_TRADE=55` 阈值。  
   大多数情况下 bear 方胜出（62-72 分 vs bull 18-42 分）。

2. **Serenity pipeline 最近一次运行失败**：`gateway_restart_interrupted`，距上次成功 12.8h。

3. **BZ/USDT universe member `execution_eligible=0`**（但不影响其他成员）。

4. **Scorecard 所有 family 显示 `outcome_status=insufficient`**：
   由于无新交易，scorecard 无法更新表现统计。

---

## 修复记录（2026-07-13）

以下修复已应用于 commit 待提交，gateway 已重启生效：

| 文件 | 改动 |
|------|------|
| `aletheia/agent_shell/operator_loop.py` | `loosen_confidence` 移入 `_LEVEL_1_ACTIONS`（对称性修复）；`_execute_level1_action` 增加 dispatch；Rule 5A 阈值 12h→4h，level=2→1；tighten 规则增加旱情抑制（`hours_since_last_trade >= 4h`） |
| `aletheia/agent_shell/trading_context.py` | `_get_data_health_summary` 返回 `min_confidence_to_trade` |
| `aletheia/agent_shell/trading_decision.py` | 信号降级警告区分"已收紧"和"未收紧"两种提示语，避免 LLM 对 high-confidence 候选反射性 SKIP |

**预期行为**：重启后 operator loop 下一次运行时 Rule 5A 自动触发 `loosen_confidence`，`min_confidence_to_trade` 回到 `medium`，候选供应量恢复，System Operator 可评估更多机会。

---

## 解锁路径（历史参考，修复后已不需要手动操作）

### 选项 A：人工放松 `min_confidence_to_trade` 回 `medium`（推荐）

```bash
# 通过 dashboard Settings 页面修改，或直接编辑 data/config.yaml
# 将 min_confidence_to_trade: high 改为 min_confidence_to_trade: medium
```

效果：立即释放被 confidence gate 阻挡的 ~89% 候选，让 System Operator 有更多候选可评估。  
风险：信号质量仍然降级，System Operator LLM 可能仍选择 SKIP（因 37.2% 警告仍在 prompt）。  
需配合：观察 System Operator 在候选丰富后是否愿意进行小仓位试探。

### 选项 B：Review 37.2% 信号质量降级的来源

查看 calibration 数据，哪个 scanner family 拖累整体：

```bash
uv run aletheia inspect calibration
```

当前最差：`momentum long perp 15m` WR=20.7%，`short_bias short perp 1h` WR=24.3%，
`volatility short perp 1h` WR=20.3%。考虑临时降低这些 family 的权重。

### 选项 C：修复 Rule 5A 的死锁条件（代码修改）

`operator_loop.py:1217` 的条件 `win_rate_7d >= 40` 在无交易时无法满足，
导致 loosen 从未触发。可以：
- 添加额外条件：无交易超过 N 小时时，忽略 win_rate 要求直接 loosen
- 或降低 loosen 的 win_rate 门槛（如改为 `win_rate_7d >= 30`）

代码位置：`aletheia/agent_shell/operator_loop.py:1214-1225`

---

## 可复现命令

```bash
# 验证 signal_status
uv run aletheia doctor 2>&1 | grep -E "data.signal|loop.profit"

# 验证 min_confidence_to_trade 设置
uv run aletheia inspect config | grep min_confidence

# 验证 candidate supply
uv run aletheia inspect candidates

# 验证 System Operator 最新决策
uv run aletheia inspect reflection

# 验证 filter 阻断分布
# (需要直接查询 app.db 的 filter_decisions 表)

# 完整 opportunity 漏斗
uv run aletheia opportunity audit
```
