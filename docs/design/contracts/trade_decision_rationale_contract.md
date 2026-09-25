# 交易开平仓理由契约

> 角色：定义一笔交易的关键开仓参数与平仓行为如何留下可读、可核验且不伪造来源的理由记录。
> 范围：补充 `decision_lifecycle_contract.md` 与 `execution_contract.md`；不改变风险批准、订单路由或实盘权限。

## 1. 不变量

1. System Operator 唯一可执行的入场意图 `trade` 必须带有入场价、止损、止盈、名义价值和杠杆五项理由；少任何一项都不得创建 proposal，也不得把 pending 终结成 skip，而是返回稳定的可重试不可用原因。`scale_in` / `flip` 不属于当前活跃能力。
2. 入场价由后端的新鲜精确 mark 决定，名义价值与杠杆仍由后端 sizing/上限约束；新 LLM `trade` 的止损/止盈则必须保留模型显式给出的绝对价格。相对推理前 mark 的反向几何先走一次有界修复；提交时刷新 mark 若过期/不可用或穿越保护位则 typed defer。缺失或错误的模型保护价不得用配置默认值改写。每项都要标出实际来源，AI 不得因市场快照、系统 sizing 或杠杆上限而获得虚假的“决定者”身份。
3. 开仓理由保存在 `trade_proposals.agent_rationale` 的 `entry_parameters` 元数据中，属于 proposal 的不可变内容并受既有 hash chain 保护。
4. 平仓理由只在关闭事实成功写入后保存到 `positions.exit_reasoning`。异步交易所平仓若已有真实 Operator 原话，必须先随 pending Close Order 保存同一版本的 `orders.close_reasoning` carrier，等待已验证成交后原子提升为最终事实；pending carrier 不等于已平仓理由。自动保护、人工、对账和 AI 复核必须使用真实 actor/source；没有 AI 原话时 `is_ai_judgment=false`。
5. 历史或外部记录缺少理由时，API/UI 只能显示 `not recorded`，不得从最终价格、PnL 或 close_reason 倒推故事。

## 2. 开仓快照

`entry_parameters` 是 JSON object，固定含以下五个 key：

| key | 最终事实 | `value_source` 示例 |
|---|---|---|
| `entry_price` | `final_value` | `market_snapshot` |
| `stop_loss` | `final_value`、距离 | 新 LLM `trade` 为 `ai_requested`；`default_config` 仅兼容 programmatic/历史对象 |
| `take_profit` | `final_value`、距离 | 新 LLM `trade` 为 `ai_requested`；`default_config` 仅兼容 programmatic/历史对象 |
| `notional_usdt` | requested/actual 名义价值、按止损估计损失 | `ai_requested` / `default_config` |
| `leverage` | requested/final、最大允许值、估计保证金 | `ai_requested` / `risk_cap_clamped` / `default_1x` / `not_applicable_spot` |

每项带 `reason`。新 LLM `trade` 任一理由为空即为可重试的不完整输出；programmatic/历史对象中的空理由只能原样表示“未记录”，不能由服务端生成主观补充。`estimated_loss_at_stop_usdt` 是数量与入场/止损价的算术结果，不能称为风险引擎的批准额度。

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

Trades & Positions 的 Causal Chain 从 proposal 解析 `entry_parameters`，从 resulting position 解析 `exit_reasoning`。订单锚点依 `positions.order_id` 解析 resulting position；这只是读取已有因果链接，不创建关联或推导缺失成交。

## 5. 验证

- `tests/specs/spec_pipeline_risk_gate.py` (`PL-RG-12`)、`tests/specs/spec_pi_decision_chain.py` (`PI-S05`) 与 `tests/specs/spec_pipeline_entry_enrichment.py`（`PL-ENRICH-02`）：五项开仓事实（entry/stop/take-profit/notional/leverage 的理由与来源）在入场路径的缺项拦截、native limit 四要素门槛，以及持久化 rationale 回链。五项 JSON 结构见 `decision_lifecycle_contract.md` §2.3 `parameter_rationale`。
- `tests/test_exchange_close_confirmation.py`、`tests/specs/spec_pipeline_multibroker_reconciliation.py` 与 `tests/specs/spec_pipeline_exchange_materialization.py`：pending exchange close 的 durable rationale handoff、verified settlement、legacy NULL、损坏 carrier fail-closed 与 replay 幂等。
- `tests/test_trade_decision_rationale_ui.py`：侧栏必须读取持久字段、保留缺失态。
- 既有 proposal hash、entry enrichment、position-manager、execution-chain specs：保护原有链路与安全语义。
