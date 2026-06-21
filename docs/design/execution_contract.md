# Execution 执行通路契约 (L2 金标准)

> **角色**:定义订单从 proposal 到交易所的完整通路,及其原子性/幂等/对账保证。
> **基线**:代码为底(file:line),意图差距标 ⚠️;冲突时文档为准。
> **不变式来源**:[trust_and_change_control §1, §6.3](../governance/trust_and_change_control.md)。
> Last updated: 2026-06-21 · 维护者:Aletheia Dev

---

## 1. 入口与因果链

| 项 | 值 | 位置 |
|----|----|----|
| 入口 | `submit_proposal(...) -> dict` | `aletheia/execution/gateway.py:16` |
| 重执行 | `execute_existing_proposal(proposal_id, mode)` | `gateway.py:52` |

**因果链(不可断,[trust §1.1](../governance/trust_and_change_control.md)):**

```
trade_proposals.id
  → risk_decisions.proposal_id
  → orders.proposal_id + orders.risk_decision_id
  → fills.order_id
  → positions (从 orders/fills 投影,当前 schema 不直接链 proposal)
```

**契约**:一个 proposed trade 只创建一次 `trade_proposals` 行。批准/拒绝/风控/执行**绝不**创建第二行。所有订单**必须**经 Execution Gateway——agent/dashboard/telegram 不得直接调交易所下单([trust §6.3](../governance/trust_and_change_control.md))。

---

## 2. 链路各环数据结构

| 环 | 结构 | 状态流 | 位置 |
|----|------|--------|------|
| Proposal | `TradeProposal(symbol, direction, quantity, price, execution_mode...)` | `pending→approved→executed` / `→rejected` / `→error` | `models.py:102` |
| RiskDecision | 见 [risk_engine_contract §3](risk_engine_contract.md) | 不可变(一次写) | `models.py:125` |
| Order | `Order(proposal_id, risk_decision_id, symbol, direction, qty, price, mode, status)` | `pending→submitted→filled` / `→error` | `models.py:136` |
| Fill | `Fill(order_id, fill_price, fill_quantity, fee)` | 不可变 | `models.py:155` |
| Position | `Position(symbol, direction, qty, entry_price, stop_price, take_profit_price, mode...)` + 入场环境(btc_entry_price/regime_tag) | `open→closed` | `models.py:165` |

**Proposal 状态契约([trust §1.2](../governance/trust_and_change_control.md)):**
- `pending`=无人工批准 · `approved`=人工/允许的工作流批准 · `executed`=订单成功创建 · `rejected`=人工或风控拒 · `error`=批准后执行失败。
- **不得**用状态变更替代缺失记录:执行发生则 order 必存在,风控发生则 risk_decision 必存在。

---

## 3. Execution Mode 与 Broker

| 模式 | 真实单 | Broker | 位置 |
|------|--------|--------|------|
| `paper_internal` | 否(本地模拟) | paper_broker | `paper_broker.py:17` |
| `bitget_demo` | 是(demo 账户) | bitget_broker | `bitget_broker.py:53` |
| `okx_demo` | 是(demo) | okx_broker | `okx_broker.py:96` |
| `binance_testnet` | 是(testnet) | binance_broker | `binance_broker.py:40` |
| `bitget/okx/binance_live_limited` | 是(**真实资金**) | 同上 broker 同函数 | — |

`_is_live()` 判定:`execution_mode in (bitget/okx/binance)_live_limited`(`engine.py:32`)。

---

## 4. TPSL 交易所挂单

| 项 | 行为 | 位置 |
|----|------|------|
| 时机 | 新仓首次成交后(叠仓不下) | `bitget_broker.py:152` |
| 内容 | `place_tpsl_order(plan_type=pos_loss/pos_profit, trigger_price, hold_side, size)` | `bitget_broker.py:315` |
| SL | `trigger=proposal.stop_price` | :313 |
| TP | `trigger=default_take_profit_price`(config/proposal) | `gateway.py:150` |
| 开关 | `trading.exchange_tpsl_enabled`(`schema.py:341`) | — |
| 失败 | 非致命,仅 warn,本地 stop/TP 仍生效 | :321 |
| 持久化 | `position.exchange_tpsl_order_id`(JSON) | :336 |
| 平仓 | 平仓前自动取消 pending TPSL(`_try_cancel_tpsl`) | :234,339 |

---

## 5. 原子性 / 幂等 / 失败语义

**下单序列(关键:交易所订单数 ≥ 本地记录,永不出现无本地 trace 的真实成交):**

```
1. Insert Order(pending)  —— 调交易所之前先写本地
2. 调交易所(可能失败/超时)
3. 回填 exchange_order_id + status
4. Insert Fill + Position(失败非致命,可恢复)
```
位置:`bitget_broker.py:87-99`(okx/binance 同构)。

| 场景 | 处理 | 位置 |
|------|------|------|
| 下单失败 | Order `status=error`+`error_reason`,proposal→`error` | `gateway.py:183` |
| 部分成交 | Order `status=submitted`,Fill 仅记已成量,position 按实成量 | `bitget_broker.py:113` |
| 重复提交 | `client_oid=proposal_id` 作幂等键,交易所去重 | `bitget_broker.py:106` |

**失败语义([trust §1.3](../governance/trust_and_change_control.md)):**
- 失败必须留下可检查状态,**不得删除部分创建的行来掩盖失败**。
- live 执行失败:保留 proposal 设 `error`,**除非交易所确认否则不推断 fill**。

---

## 6. 对账(Reconciliation)

| 项 | 行为 | 位置 |
|----|------|------|
| 逻辑 | 读交易所 live open orders → 本地 `status=submitted` 但不在交易所 open 的 → 标 `filled` | `reconciliation.py:38-85` |
| 红线 | **对账绝不下单/撤单**,只比对状态 + 写收敛/审计记录([trust §1.3](../governance/trust_and_change_control.md)) | — |
| 防伪 | 对账从不创建虚假 Fill,仅更新 `order.status` | `reconciliation.py:77` |

---

## 7. 审计要求

每个重要状态转换写 `data/logs/audit.jsonl`,最小字段见 [trust §1.4](../governance/trust_and_change_control.md):`timestamp/actor/source/event_type/entity_type/entity_id/message`,可选 `before/after`。`entity_id` 必须指向实际被改的行;审计消息不得含密钥/签名。

---

## 8. 目标态差距(意图领先代码)

- ⚠️ [execution_path_honesty §1.3](../governance/execution_path_honesty.md):strategy 的 `entry_rule/exit_rule` 是**从不被解析执行的死字符串**。交易方向实际来自 opportunity 的 LLM `direction_bias`,strategy 仅提供 `strategy_id` 供晋级门挂靠统计。**契约诚实声明**:当前"策略规则驱动交易"不成立;策略层是 advisory。诚实化三路径(命名/确定性 gate/真 DSL)见该文档 §7,均需显式授权。
- ⚠️ 审计链当前**无 hash 链**(2026-06-15 已补 `_prev_hash`,见 execution_path_honesty 顶部修正)——核实见批次5。
