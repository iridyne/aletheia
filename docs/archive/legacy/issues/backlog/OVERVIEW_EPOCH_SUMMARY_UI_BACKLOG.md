# Overview UI 六桶可视化 —— backlog 卡片

> **创建**: 2026-07-18 · **类型**: backlog（前端可选增强，非阻塞）
> **上游历史证据**: [`docs/archive/legacy/plans/trading-pipeline/proposal_flow_defer_semantics_2026-07-18.md`](../../plans/trading-pipeline/proposal_flow_defer_semantics_2026-07-18.md)
> **相关契约**: [`decision_lifecycle_contract.md`](../../../../design/contracts/decision_lifecycle_contract.md) DL-INV-2 / DL-INV-24 / DL-S47..50
> **状态**: 后端已就绪；前端未接入；owner 决定何时做

---

## 背景

Phase A（2026-07-18）新增统一聚合器 `aletheia/diagnostics/pipeline_progress.py::summarize`，
把 System Operator 12h 的决策去向按 `(producer_commit, policy_epoch_id)` 分成六桶：

| 桶 | 来源 filter_decisions / operator_evaluation_events |
|----|---------------------------------------------------|
| `direct_model_skip` | 模型直接返回 skip（DL-INV-21 已锁必须 candidate-specific adverse evidence） |
| `conviction_gate_skip` | 初始 conviction<55 未触发 debate（DL-S47 非终态 defer） |
| `debate_not_confirmed` | debate 后 conviction<55（DL-S48 非终态 defer） |
| `operator_defer` | `operator_evaluation_events.event_kind LIKE 'operator_decision_deferred_%'` |
| `context_unavailable` | 精确技术上下文缺失/不完整/过期（DL-S46） |
| `ranker_cooldown` | ranker stage cooldown 拒绝 |

**已接入的消费方**：
- CLI: `uv run aletheia doctor` → `loop.profit` 行显示六桶数字
- CLI: `uv run aletheia doctor --json` → JSON payload 里 `checks[].detail`
- Backend API: `analytics.opportunity_progress.build_current_entry_status()` 的返回
  多了一个 `epoch_summary` 字段（结构见 `pipeline_progress.summarize` 返回签名）

**未接入的消费方**：
- **Overview 前端页面** — 目前 UI 只展示 pipeline 阶段方块 + candidate 列表；
  即使后端已经返回 `epoch_summary`，React 组件不消费它。

---

## 拟做的事

在 Overview 页面加一个 "当前 epoch 决策去向" 小卡片，展示六桶实时数字。
定位：帮助 owner 一眼分辨 "阻点在 Operator 判词 vs. Operator 未唤醒 vs. 数据链路 vs. ranker"，
不必打开 CLI 跑 doctor。

**触碰边界**（AGENTS.md §Always-on safety boundaries 语义）：
- `frontend/src/pages/overview/*` — 新组件 + 数据消费
- `frontend/src/api/*` — TypeScript 类型定义（如果有的话）为 `epoch_summary` 加类型
- `cd frontend && npm run build` 写回 `aletheia/dashboard_static/`（generated & untracked，
  memory `feedback_frontend_deploy` 明示的部署方式）
- **不改后端**：后端已就绪；这一票是纯前端 PR

**明确不做**：
- 不改后端聚合语义；不改 `pipeline_progress.summarize` 签名
- 不改 `analytics.opportunity_progress.build_current_entry_status` 现有字段
- 不在 Overview 首屏加任何"建议下一步操作"的自动按钮 —— 六桶只是诊断，
  执行仍走 CLI / policy_epoch 流程
- 不引入新的图表库；用现有前端 UI kit 的卡片/数字组件

---

## 展示形式建议（可选）

```
┌─ 当前 epoch 决策去向 (12h · commit 11d435c · policy 355a963ac7bc) ─┐
│  direct_skip×0    conviction_gate×4    debate_reject×4          │
│  defer×8          context_unavailable×0    ranker_cooldown×0    │
│                                                       trade_intent×4 │
└─────────────────────────────────────────────────────────────────┘
```

关键：显示 commit + policy_epoch_id 前缀，避免读者误把跨 epoch 的历史数字当当前诊断。

---

## 何时应该做

- **owner 每天需要在 UI 上快扫一次运行健康度** → 值得做
- **owner 只在遇到问题时跑 doctor 排查** → 不做也无损失
- **想教其他协作者/新员工理解 pipeline** → 可视化辅助有价值

当前判断（2026-07-18）：doctor/CLI 已足够诊断，此项**不阻塞**任何工作，
放 backlog；未来自然浮现需求时再取。

---

## 参考

- 后端聚合器：`aletheia/diagnostics/pipeline_progress.py:summarize`
- Overview API 字段：`aletheia/analytics/opportunity_progress.py::_epoch_summary_snapshot`
- CLI 现有展示：`aletheia/diagnostics/doctor.py:_probe_profit_loop`
- 前端部署流程：memory `feedback_frontend_deploy`（`cd frontend && npm run build`，
  写回 `aletheia/dashboard_static/`）
