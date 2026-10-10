# 交易开平仓理由契约

> 角色：定义一笔交易的关键开仓参数与平仓行为如何留下可读、可核验且不伪造来源的理由记录。
> 范围：补充 `decision_lifecycle_contract.md` 与 `execution_contract.md`；不改变风险批准、订单路由或实盘权限。

## 1. 不变量

1. 当前入场理由的唯一来源是 Entry Planner 的 `EntryHypothesis`：它只给定性判断（决定、经济理由、机制/方向、锚点引用、profile、`capture_priority`、风险姿态、horizon、证据引用），从不给价格、数量、杠杆、止损或止盈数值；这些由确定性 Compiler 与 sizing 计算（[Entry Plan](entry-plan-contract.md)、[Entry sizing](entry_sizing_and_financing_contract.md)）。
2. 验证后的 hypothesis 以 `entry_hypothesis_json` / `entry_hypothesis_hash` 冻结在不可变 Entry Plan 中（`trading/entry_plan.py`）；Proposal 写 `agent_rationale = plan.thesis`，并在 hash 覆盖的 `attribution_snapshot` 中带 `entry_plan_id` 与 `entry_hypothesis_hash`（`execution/entry_plan_execution.py`）。Entry Plan 缺 hypothesis 或 source 时不得生成 Proposal。
3. 旧 PiDecision 路径写在 `trade_proposals.agent_rationale.entry_parameters` 的五项理由（entry/stop/take-profit/notional/leverage）只对历史行可读；当前没有任何 Python 写入方，不得为新交易合成。
4. 平仓理由只在关闭事实成功写入后保存到 `positions.exit_reasoning`。异步交易所平仓若已有真实 Operator 原话，必须先随 pending Close Order 保存同一版本的 `orders.close_reasoning` carrier，等待已验证成交后原子提升为最终事实；pending carrier 不等于已平仓理由。自动保护、人工、对账和 AI 复核必须使用真实 actor/source；没有 AI 原话时 `is_ai_judgment=false`。
5. 历史或外部记录缺少理由时，API/UI 只能显示 `not recorded`，不得从最终价格、PnL 或 close_reason 倒推故事。

## 2. 开仓快照

开仓快照即不可变 Entry Plan：冻结的 hypothesis JSON/hash、Compiler 得出的 entry/stop/target/
杠杆/数量及其 `capture_policy_id`，加上 Proposal 的 `attribution_snapshot`。数值的来源是 Compiler
与 sizing，不是模型；读取方不得把这些数值标为 "AI 决定"。历史 `entry_parameters` 行按原样显示，
缺失时显示 `not recorded`。

## 3. 平仓记录

`positions.exit_reasoning` 的 JSON 至少包括：

```json
{
  "version": "trade_decision_rationale.v1",
  "actor": "system_operator|system|human|position_reconciler",
  "source": "portfolio_review|position_lifecycle|verified_exit_protocol|...",
  "reason_code": "stop_loss|take_profit|...",
  "reason": "原始 AI 说明或规则触发的事实说明",
  "is_ai_judgment": false,
  "trigger": {"exit_price": 0}
}
```

- `stop_loss` / `take_profit` 记录进入时已承诺的保护价；说明明确为自动规则。
- `max_hold` 记录开仓时间；市场规则退出保存已有信号理由（若有）。
- System Operator 的组合复核保存其原始文本，并以 `is_ai_judgment=true` 标记。
- 已验证成交和行政收敛保留其对账来源；行政收敛不得伪造价格、PnL 或 AI 动机。
- pending Close Order 的非空 `close_reasoning` 必须匹配 `close_reason`，且在完整 private Fill 结算前不得写入 Position；损坏或身份不一致的 carrier 必须令 materialization fail closed。历史 `NULL` carrier 继续明确表示未记录。

## 4. Dashboard

Trades & Positions 的 Causal Chain 从 resulting position 解析 `exit_reasoning`。入场侧当前仍只解析历史 `entry_parameters`（`frontend/src/components/CausalChain.tsx`），因此新交易显示缺失；改为读取 Entry Plan hypothesis 是一个待修的 conformance gap，不是新语义。订单锚点依 `positions.order_id` 解析 resulting position；这只是读取已有因果链接，不创建关联或推导缺失成交。

## 5. 验证

- `tests/specs/spec_pipeline_risk_gate.py` (`PL-RG-12`)、`tests/specs/spec_pi_decision_chain.py` (`PI-S05`) 与 `tests/specs/spec_pipeline_entry_enrichment.py`（`PL-ENRICH-02`）：五项开仓事实（entry/stop/take-profit/notional/leverage 的理由与来源）在入场路径的缺项拦截、native limit 四要素门槛，以及持久化 rationale 回链。五项 JSON 的历史定义见 [退役入场生命周期](../history/decision_lifecycle_retired_entry.md)。
- `tests/test_exchange_close_confirmation.py`、`tests/specs/spec_pipeline_multibroker_reconciliation.py` 与 `tests/specs/spec_pipeline_exchange_materialization.py`：pending exchange close 的 durable rationale handoff、verified settlement、legacy NULL、损坏 carrier fail-closed 与 replay 幂等。
- `tests/test_trade_decision_rationale_ui.py`：侧栏必须读取持久字段、保留缺失态。
- 既有 proposal hash、entry enrichment、position-manager、execution-chain specs：保护原有链路与安全语义。
