# Kill Conditions 契约 (L2 金标准)

> **角色**：定义 demo 阶段三条系统级 Kill 判决——防止系统在已证明无效或危险状态下持续运行。
> 每条 Kill 条件均有对应的可执行 spec claim 锁死，防止代码漂移。
> **不变式来源**：[Product Direction 的永久边界](../../product/definition/DIRECTION.md#enduring-product-boundaries)；[EXPERIMENT_PROTOCOL.md](../../product/experiments/EXPERIMENT_PROTOCOL.md)。
> Last aligned with code: 2026-08-06 · 维护者: Aletheia Dev

---

## K1：Demo 连续两轮 PF < 0.5 → Terminate Experiment

**判决标准**：go_live_scorecard 中 profit_factor criterion 连续两个有合格 outcome 的
14 日 trailing windows 低于 0.5，
触发实验终止——该方向已证明无法盈利，继续运行只消耗验证窗口和资金。

**Kill 阈值**：`PF < 0.5`

> **历史背景**：终止于 2026-06-27 的 Round 1 协议曾使用 0.85。当前
> [EXPERIMENT_PROTOCOL](../../product/experiments/EXPERIMENT_PROTOCOL.md) 不复制这个
> 历史数值；机器执行阈值以本契约和 `go_live_scorecard.py` 的 0.5 为准。

**结构与行为前提**：
- `_check_profit_factor()` 返回的 criterion `weight > 0`（D8-S01 锁死，已实现）
- 极低 PF（< kill 阈值）评为 `status='fail'` 且 `score=0`（D8-B01 锁死，已实现）

**当前机器保护**：
- `check_kill_condition_consecutive_rounds()` 按 14 日窗口计算连续低于 0.5 的轮次；
- 当前函数遇到空窗口即停止，未另设每窗最小样本量；调用方不得把它描述成样本充分的
  盈利性判决；
- `_check_profit_factor()` 对 PF < 0.5 返回 `status='fail'`、`score=0`。

**对应 Spec Claims**：D8-S01 · D8-S02 · D8-B01

---

## K2：连续 3 天零开仓 → Trading Loop Dead

**判决标准**：系统连续 3 个自然日没有新的 position open，
意味着决策链已经僵死——数据在流动，但系统无法将机会转化为行动。

**监测结构**（已实现）：
- `_probe_profit_loop()` 在 `aletheia/diagnostics/doctor.py` 中检测 opportunities
  流动而 proposals 为零的解耦状态，报告键 `loop.profit`（D8-S03 锁死，已实现）

**当前机器保护**：
- `count_consecutive_zero_fill_days()` 读取 `positions.opened_at` 并返回连续无新开仓天数；
- 函数名保留历史 `zero_fill` 用语，但当前统计单元是 position open，不是 `fills` 表行。

**对应 Spec Claims**：D8-S03 · D8-S04

---

## K3：Live Unlock 须 Scorecard Composite Score ≥ 70

**判决标准**：人类操作员点击"解锁 live"时，系统必须自动计算 go_live_scorecard 综合分，
`composite_score < 70` 时硬性阻断解锁请求——demo 表现是 live 的唯一入场券。

**不变式（D8-C01）**：`POST /api/settings/live-unlock` 在迁移至 `live_limited` 前
**必须**调用 `compute_scorecard()` 且 `composite_score >= 70`，任何绕过路径均违约。

**当前状态**：`server/routes/live.py` 在状态迁移前调用 `compute_scorecard()`；
低于 70、非 green、存在 blocker，或合格交易净已实现收益不为正都会阻断。
D8-B02/D8-C01 与负净收益、loss-first drawdown 回归锁定当前门禁。

**对应 Spec Claims**：D8-B02 · D8-C01

---

## 延伸：个人止损线（Personal Stop Line）— Deferred

个人/账户级别的止损线功能（如 `personal_stop_loss_usdt`）尚未纳入 `RiskConfig`，
属于显式 defer 状态。D8-B03 为 missing_tripwire：当该功能被悄悄加入代码时，
tripwire 变红，提醒必须补充完整 spec 后才能合并。

**对应 Spec Claims**：D8-B03

---

## 契约执行命令

```bash
# 运行 kill_conditions 所有 claims（含 xfail tripwires）
uv run pytest tests/specs/spec_kill_conditions.py -v

# 全量 spec（确认无回归）
uv run pytest tests/specs/ -q
```

---

## 与其他契约的关系

| 契约 | 关系 |
|------|------|
| [live_unlock_contract.md](live_unlock_contract.md) | K3 扩展 §2 解锁路径；D8-C01 是 §core_philosophy 的代码层不变式 |
| [risk_engine_contract.md](risk_engine_contract.md) | Kill 触发后下游风控继续拦截；两者独立分层 |
| [EXPERIMENT_PROTOCOL.md](../../product/experiments/EXPERIMENT_PROTOCOL.md) | K1/K2 来源于实验协议 §6 终止规则；K3 来源于产品定义 §8 |
