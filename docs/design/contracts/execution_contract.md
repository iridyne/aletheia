# Execution 执行通路契约 (L2 金标准)

> **角色**:定义订单从 proposal 到交易所的完整通路,及其原子性/幂等/对账保证。
> **基线**:代码为底(file:line),意图差距标 ⚠️;冲突时文档为准。
> **不变式来源**:[trust_and_change_control §1, §6.3](../../governance/policies/trust_and_change_control.md)。
> Last updated: 2026-07-14 · 维护者:Aletheia Dev

---

## 1. 入口与因果链

| 项 | 值 | 位置 |
|----|----|----|
| 入口 | `submit_proposal(...) -> dict` | `aletheia/execution/gateway.py:16` |
| 重执行 | `execute_existing_proposal(proposal_id, mode)` | `gateway.py:52` |

**因果链(不可断,[trust §1.1](../../governance/policies/trust_and_change_control.md)):**

```
trade_proposals.id
  → risk_decisions.proposal_id
  → orders.proposal_id + orders.risk_decision_id
  → fills.order_id
  → positions (从 orders/fills 投影;schema 现直接携带 `proposal_id` + `order_id` + `opportunity_id`,新 position 开仓即写,adopt 也走同一路径 — #30/#50)
```

**契约**:一个 proposed trade 只创建一次 `trade_proposals` 行。批准/拒绝/风控/执行**绝不**创建第二行。所有订单**必须**经 Execution Gateway——agent/dashboard/telegram 不得直接调交易所下单([trust §6.3](../../governance/policies/trust_and_change_control.md))。

---

## 2. 链路各环数据结构

| 环 | 结构 | 状态流 | 位置 |
|----|------|--------|------|
| Proposal | `TradeProposal(symbol, direction, quantity, price, execution_mode...)`; `agent_rationale.entry_parameters` carries the immutable final-value/source/reason snapshot for AI-originated entries | `pending/approved→executing→executed` / `→rejected` / `→error`; uncertain exchange submission uses `submission_unknown`, incomplete local paper materialization uses `materialization_missing` | `models.py:102` |
| RiskDecision | 见 [risk_engine_contract §3](risk_engine_contract.md) | 不可变(一次写) | `models.py:125` |
| Order | `Order(proposal_id, risk_decision_id, symbol, direction, qty, price, mode, status)` | `pending→submitted→filled` / `→error` | `models.py:136` |
| Fill | `Fill(order_id, fill_price, fill_quantity, fee)` | 不可变 | `models.py:155` |
| Position | `Position(symbol, direction, qty, entry_price, stop_price, take_profit_price, mode...)` + 入场环境(btc_entry_price/regime_tag) + `exit_reasoning` (actual close actor/source/trigger) | `open→closed` | `models.py:165` |

**Proposal 状态契约([trust §1.2](../../governance/policies/trust_and_change_control.md)):**
- `pending`=无人工批准 · `approved`=人工/允许的工作流批准 · `executing`=已有持久执行 owner · `executed`=Order/Fill/Position 完整成交链已落库 · `rejected`=人工或风控拒 · `error`=确认未提交的终态失败。
- `submission_unknown`=交易所边界可能已越过，必须先对账，禁止自动重发；`materialization_missing`=已有 Order 或交易所已报告成交活动，但本地 Fill/Position 物化未完整，禁止重新下单（适用于 local paper 与 exchange）。
- **不得**用状态变更替代缺失记录:执行发生则 order 必存在,风控发生则 risk_decision 必存在。

**Risk 与执行快照必须是同一份意图**：交易所数量规则可以生成 quantity-only 的执行副本，
authoritative RiskDecision 记录 `evaluated_quantity` 并绑定原 Proposal 的 `proposal_commit_hash`；broker
只能消费这份已批准副本。除 quantity 归一化外，任何 hash-covered 字段与存储 Proposal 不同都会在
Risk 前置失败，不得产生 authoritative RiskDecision 或 Order。

**paper TP 不得事后发明**：paper broker 创建新 Position 时只能原样写入 Risk 已检查的
`TradeProposal.take_profit`。Proposal 中为 `NULL`，Position 的 `take_profit_price` 也必须为 `NULL`；
broker 不得在批准后读取可变 config 补一个 TP。§10.4 的 position-manager 对原生 Proposal 同样只消费
持久化的批准值；Proposal 明确为 `NULL` 时，不得再用当前 config 生成新的平仓触发器。

---

## 3. Execution Mode 与 Broker

| 模式 | 真实单 | Broker | 位置 |
|------|--------|--------|------|
| `paper_internal` | 否(本地模拟) | paper_broker | `paper_broker.py:17` |
| `bitget_demo` | 是(demo 账户) | bitget_broker | `bitget_broker.py:53` |
| `okx_demo` | 是(demo) | okx_broker | `okx_broker.py:96` |
| `binance_testnet` | 是(testnet) | binance_broker | `binance_broker.py:40` |
| `bitget/okx/binance_live_limited` | 是(**真实资金**) | 同上 broker 同函数 | — |

`_is_live()` 判定:`execution_mode in (bitget/okx/binance)_live_limited`(`engine.py:35`)。

### 3.0 `paper_internal` 入场真实性

- 本地纸面入场只允许 `order_type='market'`。Proposal builder、paper tool 和 broker 三层都拒绝
  `limit`，不得把“按报价立即模拟成交”伪装成有挂单/等待成交语义的限价单；交易所 demo/testnet
  仍可按各自 adapter 能力使用 limit。
- 入场价只来自配置行情 venue 的同 canonical symbol、同物理 `spot/perp` 新鲜精确 mark；内存
  mark 必须小于 90 秒，SQLite 精确回退不得超过 120 秒。跨 venue、跨 spot/perp、symbol-only
  或 stale row 都不能建立 Proposal，pending 留待后续重试。
- 每个本地 Position 只属于一个 immutable Proposal。相同 symbol/direction/strategy 的另一 Proposal
  不得在 broker 内并入已有 Position；正常 Gateway/Risk 路径会先拒绝重复暴露，broker 的独立
  lineage 是最后一道防御，避免费用、保护价和归因跨 Proposal 混合。

### 3.1 Adapter market_type 能力(交易所无关核心 + 边界适配)

核心逻辑(universe/opportunity/decision/risk)只认 canonical 类型 `spot` / `usdt_futures`,**不关心交易所**;能不能下某类型单是**各 adapter 的能力**,在 adapter 边界声明并路由(owner 原则 2)。

| Adapter | 支持 market_type | 路由要点 | 来源 |
|---------|-----------------|----------|------|
| okx | `spot` + `usdt_futures` | `SUPPORTED_MARKET_TYPES`;`okx_ccxt_symbol()` 按类型选符号(spot=canonical 无后缀 / perp=`:USDT`);spot 无杠杆/无 contractSize 换算/无 reduceOnly,市价买用 `tgtCcy=base_ccy` | `okx/client.py` |
| bitget | `usdt_futures`(demo 仅永续) | demo 模拟盘只提供 USDT 永续 | `bitget_broker.py:11` |

> **OKX demo 现货已实测**(2026-06-28):demo 沙盒列 522 现货标的,真下单 buy+sell 全程通过。详见 `docs/research/integrations/DEMO_LIVE_SPOT_PARITY_EVENT_2026-06-28.md`。spec:`tests/specs/spec_spot_parity.py`(SP-S01~S06)。
> **AI 决定 market_type**:opportunity 带 `market` 字段透出给 PI;PI 显式选 → 否则继承 opportunity.market → 否则按"空头必永续/其余现货"兜底(非固定偏向)。`trading_decision.py`。

### 3.2 Symbol Availability Pre-check（标的可用性预检）

风控批准后、Broker 下单前，gateway 对**所有真实交易所模式**做一次 symbol 可用性验证（`gateway.py:_check_symbol_availability`）。不可用时直接返回 `rejected`，写 `event_type=proposal_skipped` 审计记录，**不产生** `error` 状态。`paper_internal` 免检（虚拟撮合，与交易所标的无关）。

| 模式 | 标的来源 | 缓存 TTL |
|------|---------|---------|
| `okx_demo` | `okx_broker._get_demo_symbols()`（sandbox CCXT，OKX demo 专属列表） | 4h |
| `okx_live_limited` | `universe_registry`（venue=`"okx"`） | 4h |
| `bitget_demo` / `bitget_live_limited` | `universe_registry`（venue=`"bitget"`） | 4h |
| `binance_testnet` / `binance_live_limited` | `universe_registry`（venue=`"binance"`） | 4h |

**Fail-open**：缓存未就绪或 registry 为空时返回 `None`，不阻塞下单（避免首次启动 / registry 未同步时误伤）。

**新交易所接入方式**（e.g. Hyperliquid）：
1. 往 `_EXECUTION_MODE_TO_VENUE` 加 `"hyperliquid_demo": "hyperliquid"`
2. 确保 `sync_universe_registry()` 已写入 `venue="hyperliquid"` 行

**Spec claims**：`EX-S07`（结构）/ `EX-B10`（paper_internal 免检）/ `EX-B11`（OKX demo）/ `EX-B12`（registry 模式 + fail-open）。`gateway.py:85-165`。

### 3.4 Worktree preview runtime（fail-closed）

`uv run aletheia dev api --port <879x>` 是显式、前台、隔离的开发 API，
不是 gateway 的第二实例，也不得绑定主 gateway 的 `8787/8788` 端口。它只可
使用当前 Worktree 的 `data/dev-preview/<port>`。在该模式下，Execution Gateway
在写入 Proposal、claim、RiskDecision、Order 或调用 broker **之前**拒绝
`submit_proposal` 与 `execute_existing_proposal`；dashboard 的 paper approval
route 也在读取/更新 Proposal 前返回 `403 preview_execution_blocked`。

该限制同时覆盖 `paper_internal` 与所有交易所模式，且不改变正常 gateway 的
风险、执行、审计或 live 行为。凭据读取为空、凭据写入被拒绝；因此 preview
只能用于界面/API 开发，不能用作交易模拟或订单验收环境。Executable claim:
`EX-B13`。

### 3.3 交易所最小下单规则（开仓）

对会发到交易所的开仓（demo/testnet/live），不得用行情源或本地猜测来决定最小下单量。首轮风控通过后，gateway 必须从**实际接收订单的交易所**读取该标的规则；`paper_internal` 不适用。

- 必须同时处理最小数量、数量增量、最小成交额、最大数量，以及永续合约的“张数 ↔ 币数”换算；内部数量始终是币数。
- 数量须向上对齐到合法增量并满足最小成交额；对齐后的数量必须再经过 Risk Engine，才可调用 broker。
- 规则缺失、规则过期后无法刷新、市场已停用或精度类型无法安全换算时，proposal 设为 `rejected`，写审计记录，且**不得创建/发送 Order**。
- 规则缓存仅 5 分钟；过期缓存不能在刷新失败时继续使用。此规则是 fail-closed，与上节只为改善提示的标的可用性预检不同。

这道闸门防止系统主动发送低于交易所已公布门槛的开仓单；交易所仍可能因价格瞬时变动、余额、仓位模式或临时风控拒单，调用方必须保留交易所最终回执作为事实来源。

**Spec claim**：`EX-B05`；行为回归：`tests/test_order_constraints.py`、`tests/test_gateway_order_constraints.py`。
---

## 4. TPSL 交易所挂单

| 项 | 行为 | 位置 |
|----|------|------|
| 时机 | 新仓首次成交后(叠仓不下) | `bitget_broker.py:153-163` |
| 内容 | `place_tpsl_order(plan_type=pos_loss/pos_profit, trigger_price, hold_side, size)` | `bitget_broker.py:299-336` |
| SL | `trigger=proposal.stop_price` | :313 |
| TP | `trigger=proposal.take_profit`（仅使用已获 Risk 批准的值） | `gateway.py`, `bitget_broker.py` |
| 开关 | `trading.exchange_tpsl_enabled`(`schema.py:341`) | — |
| 失败 | 非致命,仅 warn,本地 stop/TP 仍生效 | :321 |
| 持久化 | `position.exchange_tpsl_order_id`(JSON) | :336 |
| 平仓 | 平仓前自动取消 pending TPSL(`_try_cancel_tpsl`) | :234,339 |

---

## 5. 原子性 / 幂等 / 失败语义

`submit_proposal()` 与 `execute_existing_proposal()` 必须汇合到同一执行入口，不能由新提案绕开持久租约直接调用 broker。执行入口在一个短 `BEGIN IMMEDIATE` 事务内写入独立的 `proposal_execution_claims` 行，并把 `pending`/`approved` Proposal 改为 `executing`；租约字段不写入 hash-covered Proposal，避免恢复元数据改写交易意图。

**执行租约与崩溃恢复：**

- 一个 Proposal 同时最多有一个 owner；新鲜租约返回 `already_claimed`，不得进入 Risk/broker 或创建 Order。
- 租约过期后，只有 `paper_internal` 且尚无 Order 时可以用 owner token + 原过期时间做 compare-and-set 抢回执行权。paper 的 owner 校验与 Order/Fill/Position 物化同处一个 `BEGIN IMMEDIATE` 事务，因此旧 owner 与恢复 owner 不可能各写一套成交。
- 交易所模式租约过期时，无论本地是否已有 Order，都不得推断“未发送”并自动重试；Proposal 转为 `submission_unknown`，返回 `reconciliation_required`，等待对账。
- `paper_internal` 已有 Order 但物化不完整时转为 `materialization_missing` 并等待修复，不重新创建订单。
- 正常完成或终态失败只允许当前 owner 以 token compare-and-set 释放自己的租约，旧 owner 不能删除新 owner 的租约。

可执行检查：`EX-B13`（新鲜租约不重复）、`EX-B14`（paper 崩溃后恰好一套 Order/Fill/Position）、`EX-B15`（交易所过期租约零重发）。

**下单序列(关键:交易所订单数 ≥ 本地记录,永不出现无本地 trace 的真实成交):**

```
1. Insert Order(pending)  —— 调交易所之前先写本地
2. 调交易所(可能失败/超时)
3. 回填 exchange_order_id + status
4. Insert Fill + Position(失败非致命,可恢复)
```
位置:`bitget_broker.py:87-99`(okx/binance 同构)。

第 1 步的 `pending` 不是“还没发生所以可忽略”。它代表真实提交是否发生仍未知，必须一直留在 Risk
工作集中：在对应 live/demo 池占用入场槽位和同符号权利，并把尚未由 open Position 表示的数量加入
名义敞口，直到后续状态或 Position 事实明确收敛。平仓 Order 不占新增入场容量。

| 场景 | 处理 | 位置 |
|------|------|------|
| 下单失败 | Order `status=error`+`error_reason`,proposal→`error` | `gateway.py:183` |
| 部分成交 | Order `status=submitted`,Fill 仅记已成量,position 按实成量 | `bitget_broker.py:113` |
| 并发重复提交 | gateway 持久租约先阻断；交易所仍以 `client_oid=proposal_id` 作第二层幂等保护 | `gateway.py`, `bitget_broker.py:106` |

**失败语义([trust §1.3](../../governance/policies/trust_and_change_control.md)):**
- 失败必须留下可检查状态,**不得删除部分创建的行来掩盖失败**。
- live 执行失败:保留 proposal 设 `error`,**除非交易所确认否则不推断 fill**。
- broker 返回 accepted result 且对应 native Order 已持久化后，交易所边界已经跨过；后续本地读取、
  Proposal 状态同步或审计故障都不得再把 Proposal/API 改成 `error`。网关优先从 identity-linked
  durable Order 恢复 `submitted`/`partially_filled`；只有 `status=filled` 的同一 Order 同时拥有唯一、
  非零且 ID 匹配的 Fill 与 Position，才可恢复 `executed`。证据不可读、不完整或 Proposal 状态写入
  中断时，必须降级为 `submission_unknown`/`materialization_missing`，明确禁止自动重发，且日志/API
  不得包含原异常正文。连续读取失败是“证据不可用”，不得误当成“已读且无 accepted Order”再写
  `error`。可执行检查：`PL-PN-06`～`PL-PN-10`。

---

## 6. 对账(Reconciliation)

| 项 | 行为 | 位置 |
|----|------|------|
| 逻辑 | 读交易所 live open orders → 本地 `status=submitted` 但不在交易所 open 的 → 标 `filled` | `reconciliation.py:38-85` |
| 红线 | **对账绝不下单/撤单**,只比对状态 + 写收敛/审计记录([trust §1.3](../../governance/policies/trust_and_change_control.md)) | — |
| 防伪 | 对账从不创建虚假 Fill,仅更新 `order.status` | `reconciliation.py:77` |

**Spec claim**：`EX-C04`（对账模块不得导入下单函数）。

Position reconciler 的匹配身份必须包含执行账户作用域：`execution_mode + broker_id +
account_environment + canonical symbol + market_type + direction`；双方都带非敏感 account fingerprint
且值不同时禁止匹配。`source=native/adopted` 只解释仓位如何重新出现，不得限制事故探测。
外部 Demo/Live 只比较当前执行账户对应的本地仓；其他账户仓位不进入本次分类，不能被当前账户关闭。
`paper_internal` 是纯本地模拟，没有交易所对应物，始终返回 `sync_status=local_only`，任何 unified/legacy
外部缓存都不得把它分类为 `orphaned`。可执行检查：`PL-ADOPT-22`。
Live 作用域从 `exchange.name` 派生对应的 `*_live_limited`，不得硬编码某一家交易所（`PL-ADOPT-23`）。
统一 Demo 快照中的每条非零仓位必须有 `broker_id`，且只匹配当前执行账户；账户标签缺失时必须降级为
不可用，不能据此推断本地仓已从交易所消失（`PL-ADOPT-24`）。统一快照当前不包含 Live 账户，禁止用
Demo 聚合结果解释 Live 仓位；Live 继续只告警、不自动收敛。

一次新鲜快照里缺少本地持仓，只能创建 `position_disappearance_candidates` 和一条不可变 observation，
不能直接调用 fallback-price close。重复读取同一个 cache、时间倒退、stale/incomplete/wrong-account
view 不累计；第二个更新且完整的 view 只把 candidate 标为 ready for verified exit。交易所行在新鲜完整
view 中重新出现时，pending candidate 以 `reappeared` 结案并写审计。当前逻辑账户 fingerprint 只由
broker/environment/execution-mode 这类非秘密配置身份生成，绝不读取或散列 credential。T06/T07 交付
真实退出证据与经济分流前，scheduler 不会把 ready candidate 本地平仓。对应 `OI-B01`。

---

## 7. 审计要求

每个重要状态转换写 `data/logs/audit.jsonl`,最小字段见 [trust §1.4](../../governance/policies/trust_and_change_control.md):`timestamp/actor/source/event_type/entity_type/entity_id/message`,可选 `before/after`。`entity_id` 必须指向实际被改的行;审计消息不得含密钥/签名。

执行边界跨过后，SQLite 中已提交的 Order/Fill/Position/Proposal 或禁止重发的
`submission_unknown`/`materialization_missing` 是权威事实；此后的 JSONL 审计属于次要写入。
审计写失败只能记录不含异常正文的 warning（仅异常类型），不得把已提交的
`submitted`/`executed`/未决状态改写成 `error`，也不得把失败抛给调用方触发重发。
三家 exchange broker 的 entry 与 close 路径统一使用
`aletheia.execution.post_commit_audit.write_post_commit_audit`；该 helper 只包裹 JSONL sink，DB 写与
exchange 调用必须留在 helper 外并保持原失败语义。close 的审计故障必须保留 Order 与 Position 的
`pending_close_status`，重复 close 由 durable claim 直接返回，不得再次触达 exchange。
可执行检查：`PL-PN-03`～`PL-PN-05`、`PL-RX-19`、`PL-RX-20`。

---

## 8. 已交付

- ✅ **审计链 `_prev_hash` hash 链**:每条 audit 记录写入前追加前一行的 SHA-256(`_prev_hash`),验证逻辑在同文件。实现位置:`aletheia/files/jsonl.py:182-200`(写入)、`jsonl.py:259-320`(校验)。历史 ⚠️ 注记已失效,此条目已交付。

## 9. 目标态差距 — 当前活跃

- ✅ **已由 Position C 解决(2026-07-02)**:此前 strategy 的 `entry_rule/exit_rule`/`RuleSet` 是"从不被解析执行的死字符串",strategy 层名义上驱动交易实则 advisory——这个诚实性缺口现已**通过删除整个规则引擎彻底消除**(`aletheia/strategy/` 整包 + `strategy_designer.py` + `ruleset_scanner.py` 删除)。交易方向唯一来自 System Operator 对 opportunity 的决策(`evaluate_trading_opportunity`);`strategy_id` 仅作可空历史归因标签(legacy)。不再存在"策略规则驱动交易"的名实不符。

---

## 10. 平仓/退出通路 (Exit Path)

> §1–§6 定义**开仓**通路;本节定义**平仓/退出**通路,达到与开仓对等的严谨度。
> 入口:`evaluate_open_positions()`(`portfolio/position_manager.py:299`),由 `position_manager` job 每 5min 触发。

### 10.1 退出触发器优先级

`_decide_exit(p, price, cfg)`(`position_manager.py:347`)按**固定顺序**判定,先命中先平:

| 优先级 | 触发器 | 判定 | 位置 |
|--------|--------|------|------|
| 1 | stop-loss | `price` 越过 `stop_price`(多头跌破/空头升破) | `_decide_exit` |
| 2 | take-profit | `price` 越过 `take_profit_price` | `_decide_exit` |
| 3 | max-hold | 持仓时长 > `max_position_hold_hours`(serenity school 用 `max_hold_days`) | `_decide_exit` |
| 4 | market-aware review | **仅当 1–3 都未触发**且 `market_aware_exits.enabled`;评估 `urgent_exit` / `tighten_stop`,但只唤醒 System Operator 复核，不直接平仓或修改止损 | `exit_rules.py:27`,调用点 `position_manager.py:352` |

> 当前实现不授予 market-aware evaluator 自动执行权限。action 名称是复核建议，
> 最终行为是 durable Operator wake；在 Operator 明确作出并物化决定前，Position、
> Order 和 stop 均不变化。Settings 中仍声称自动平仓/收紧的文案属于已知产品偏差
>（Capability EX-09 / GitHub #417），不能以 evaluator 单元测试判定为已支持。

> (#105) 原优先级-4 的 `ruleset_exit` 触发器已随规则引擎(Position C)删除——`_decide_exit`
> 不再消费 `ruleset_exit_position_ids`,该触发器不再是活跃退出路径,请勿再加回。

**System Operator 退出路径独立**:`review_portfolio_state()`(LLM 决策)暴露
`hold / adjust_position / close_one`。Position wake 直接进入该命令，而不是复用
entry-opportunity schema；提示只包含 wake 指向的完整 Position 和 adverse evidence。
`adjust_position` 复用 `apply_operator_adjustment()`，`close_one` 按明确 Position
经**同一** `close_position_routed()` 入口平仓，且不传调用方 fallback price；
manager 必须重新取得精确新鲜 mark。最终结果和 wake fingerprint 写入
`pi_decisions`；模型不可用也写入 `review_unavailable`。遗留 `reduce_exposure`
只能归一为 `hold`，不得静默执行整仓关闭。确定性 evaluator 仍只负责唤醒，
在 Operator 决定前不得直接修改 Position。

### 10.2 close_position_routed 路由契约(唯一平仓中枢)

`close_position_routed(pos_id, reason, fallback_exit_price)` 是单仓**唯一**平仓出口，自动循环与 Operator 路径都经此；`fallback_exit_price` 仅保留调用兼容，不能授权 `paper_internal` 成交：

- 按 `execution_mode` 分派:`bitget_demo→close_demo_position` / `okx_demo→close_okx_position` / `binance_testnet→close_testnet_position` / `paper_internal→本地 close`。
- **live 守卫(不变式)**:`live_limited` 模式且 `allow_live=False` → `RaiseError`,阻止自动循环误平**真实资金**持仓(`position_manager.py:59`)。守卫在中枢层,不分散到各调用方。
- 交易所腿不可跳过:demo 模式仍走真实交易所 close;`paper_internal` 本地撮合。
- **保护性平仓价格不可伪造**：触发判断和 `paper_internal` close Fill 只使用 Position 精确 `exchange + symbol + market_type` 的新鲜 mark。单仓路由即使收到 `fallback_exit_price`，本地分支也必须忽略它并在 manager 内重新取价。交易所仓由 `execution_mode` 固定 venue（OKX / Bitget / Binance），只有 `paper_internal` 使用配置的 market-data venue。内存 exact-instrument cache 必须小于 90 秒；SQLite 只能回退到同 venue、同 `spot` / `perp`（兼容旧 `usdt_futures` 拼写）且不超过 120 秒的 snapshot。不得用 `Position.current_price`、symbol-only、跨 venue、跨 spot/perp 或过期价格触发/生成退出成交。
- **逐仓失败必须可见**：一个仓位的 mark 查询、market-aware 评估或 close 异常不得中止其余仓位；`evaluate_open_positions()` 返回稳定的 per-position `errors`，close 异常另计入 `failed_close_count`。`closed=0` 且 close 全失败不能被调度层当作健康空跑。
- **dashboard 批量平仓先完整预检**：`close_local_positions_batch()` 先无上限枚举目标 `paper_internal` 仓位，并在 manager 内为每个仓位取得上述 exact mark；任一 mark 缺失或读取失败，在第一笔 close 前整体拒绝。预检全部通过后才逐仓物化；若之后出现非行情执行错误，响应必须同时列出 `closed_position_ids` 与逐仓 `failed`，不得把部分成功显示成 close-all 成功。

### 10.3 paused / kill_switch 对平仓的语义(核心不变量)

> **`paused` 与 `kill_switch` 对持仓生命周期语义一致:都只挡"开新仓",都不挡"管理/平掉已有仓"。**
> 理由:平仓是**管理已有风险**(止损/止盈/对账),冻结它等于在最需要保护时撤掉保护;开仓才是**承担新风险**,应被暂停/熔断挡住。

| 控制 | 挡开仓 | 挡平仓 | 实现 |
|------|--------|--------|------|
| `kill_switch` | 是(风控 check#0,只拦 proposal=开仓) | **否** | `position_manager` 绕过;`test_position_manager.py:147` + `RUNBOOK.md:181` 锁定 |
| `paused` | 是(`_run_paper_trader` 早退 + 下游风控) | **否** | `_run_position_manager`/`_run_position_reconcile` **无 paused 门**;`spec PL-PM-07` 锁定 |

历史缺陷(已修 2026-06-30):`scheduler.py:_run_position_manager` 曾 `if cfg.runtime.paused: return`,导致暂停时止损循环停摆(仅 paper/demo;`paper_internal` 无交易所原生 TPSL 兜底,真失保护)。修复=移除 paused 门 + `PL-PM-07` 回归锁。

### 10.4 stop/take-profit 兜底

`_effective_stop` / `_effective_take_profit` 优先使用 Position 行已经持久化的
`stop_price` / `take_profit_price`（包括获准的 trailing-stop 更新）。若原生 Position 的值为空，则回看
其不可变 Proposal：Proposal 有值才使用，Proposal 为 `NULL` 就保持无该触发器，绝不能读取后来变化的
config 发明保护。只有没有 Proposal 身份的历史仓位，或明确 `source=adopted` 的外部接管仓位，才保留
config 默认百分比兜底；接管流程会尽量在接管时一次性固化这些值，避免之后漂移。`PL-PM-09` 锁定原生
Proposal 的这一权限边界。
`_sync_exchange_closed`(`position_manager.py:311`,`248-296`):每轮先收敛交易所原生 TPSL(bitget demo bracket)已触发的平仓,避免本地-交易所长期 desync。

### 10.5 已知状态与边界

- ⚠️ **live 人工平仓路径未实现**:`allow_live=True` 全库无调用点(`position_manager.py:53` 仅文档注释);前端无 force-close 按钮,无 `/api/positions/{id}/close` 端点。**当前 `live_limited` 仓位须在交易所界面手动平仓。** 未来加 close 端点**触信任边界**,须走 [trust §6](../../governance/policies/trust_and_change_control.md) change-control + 人工确认(`allow_live=True`)。
- ⚠️ **live 对账仍受 paused**:`_run_reconciliation`(live-only,`scheduler.py:822`)仍有 paused 门,本次未改(live 默认禁用,且仅做收敛检测,优先级低)。若未来 live 常开,应一并对齐本节语义。

**Spec claims**:`PL-PM-01~06`(mark/stop/TP/max_hold/ATR/audit) + `PL-PM-07`(paused 不挡保护性平仓) + `PL-PM-10~13`(fresh exact venue/market mark / 逐仓 close 与 market-aware 失败可见) + `PL-PCM-05/11~15`(dashboard 全量枚举、mark 预检、单仓 fallback 隔离与部分失败诚实返回)。

## 11. Position 关闭与结果完整性（OI-S01/S02、OI-B01～B05、OI-C01～C06 implemented；自然 Runtime soak 待补）

`positions.status='closed'` 只表示本地状态生命周期结束，不自动证明发生了策略退出成交。系统必须把
关闭分为两条不互相冒充的路径：

| 路径 | 必需证据 | 允许的副作用 | 结果资格 |
|---|---|---|---|
| verified strategy close | 本地 execution fill，或当前 broker/account/instrument 的可验证退出 fill；期货还需可信 funding ledger 才有完整净经济结果 | 真实 fee、slippage；仅经济字段完整时才写净 PnL、cash settlement、outcome-only hook | 通过其余干净窗/归因门且 funding 完整后可 eligible |
| administrative close | 持久确认仓位已消失，但无可验证退出 fill | 状态收敛、审计、告警 | 永远 ineligible；不得制造经济字段 |

### 11.1 消失确认

一个 complete position view 的单次 miss 只能创建 durable disappearance candidate。只有以下任一条件
满足后，系统才可判断仓位确实不再存在：

1. 两次独立、完整、同 broker/account/environment 的视图连续 absent；或
2. broker 直查明确返回 `confirmed_absent_without_fill`；或
3. broker 直查返回 `verified_fill`。

stale、incomplete、缺 broker identity、wrong-account、分页不完整、网络错误和权限错误都是
`inconclusive`，不能累计确认。candidate 必须跨进程重启保留，同一 observation 不得重复计数；仓位重新
出现时把 candidate 结案为 `reappeared` 并留审计（不删除历史）。Live 的不确定消失保持
alert-only/fail-closed，不借本合同扩大自动权限。Demo ready candidate 会进入 §11.2 的三态直查，
再由 §11.3 分流为 verified close、administrative close 或保持 inconclusive；ready 本身从不直接关闭。

### 11.2 Broker-neutral verified exit

状态：RG-T06 / OI-B02 的只读 protocol 与三家 adapter、以及 RG-T07 的调用编排均已实现。

Execution 层定义统一 verified-position-exit protocol；Bitget、OKX、Binance adapter 都返回结构化三态：

- `verified_fill`：含 broker fill/order identity、价格、数量、fee、时间和 provenance；
- `confirmed_absent_without_fill`：确认已不存在，但找不到足以证明经济结果的 fill；
- `inconclusive`：无法证明，持仓保持 open/candidate，不关闭。

symbol 仍只在 adapter 边界转换，查询身份必须包含 venue、market type、account environment；不得用
Bitget-only TPSL 历史解释其他 broker。每次查询先直查同账户仓位，再按明确时间窗完整翻页读取私有成交：
Bitget 使用 mix fills cursor，OKX 使用 fills-history bill cursor，Binance 使用 futures user-trade id。
缺页游标、重复游标、保留时间窗不足、权限/网络错误、响应字段缺失、方向或数量歧义全部返回
`inconclusive`。只有当前仓位明确 absent、历史完整且反向平仓 fill 覆盖本地数量时才是
`verified_fill`；完整历史确实无 fill 才是 `confirmed_absent_without_fill`。原始私有响应不落库，标准化
结果不含 credential 或 signer material。

### 11.3 行政关闭副作用禁区

状态：RG-T07 / OI-B03 已实现。`portfolio.outcome_closure` 提供 verified 与 administrative 两个不可
互换的入口；ready demo disappearance candidate 只有经过 RG-T06 三态直查后才可调用。Live 仍是
alert-only，active reconciliation incident 仍先阻断收口。

administrative-unverified close 不得：

- 用 entry/mark/current fallback 伪造退出 fill；
- 新增手续费、滑点、已实现策略 PnL或 `paper_account_events` 结算；
- 触发 calibration、shadow resolution、strategy KPI、Learning case/reviewer 等 outcome-only hook；
- 被 Risk 当成权威账户损失。

它必须保留 causal ids、actor/source、确认依据和审计事件。原始历史值永久保留，新增结构化字段只描述
其 provenance 和 eligibility。当前实现把 realized PnL、exit fee、slippage 留为 unavailable，不改
current/entry price，不写 paper cash，不触发 shadow/Serenity outcome hook。verified close 要求完整 scoped
identity、精确数量、quote-currency fee 和 `verified_fill`，用成交加权价与实际 exit fee 结算一次；缺 fee、
错币种、数量不符或 identity 不符都保持 open。旧 `reconcile_close_orphan` 与 Bitget-only TPSL fallback 已
删除，旧 `close_position` 也拒绝 synthetic reason。对应 `OI-B01~B04`、`OI-C03`。

PnL 读取已拆出 `today_raw_closed_realized_pnl()` 与 `today_eligible_strategy_pnl()`；旧
`today_realized_pnl()` 暂保留 raw 兼容入口，但当天任一关闭缺少净经济结果时返回 unavailable，
不能把该行按 0 相加。Risk 已由 RG-T09 迁移到独立
`authoritative_account_realized_loss()`，不再消费该兼容指标。

### 11.4 中央关闭语义与三种读取口径（OI-S02 implemented）

`aletheia/analytics/outcome_integrity.py` 是不依赖 Learning、Risk、Execution、exchange 或 agent shell
的中央语义层，版本为 `oi-v1`。它提供三个不可互换的入口：

- `raw_account_events()`：所有 closed 状态事件，只用于账本、审计、诊断和明确标注的 raw 展示；
- `eligible_strategy_outcomes()`：仅当前版本、strategy category、真实 fill 已验证、价格来源可信、
  identity/时间/经济字段完整的结果；
- `administrative_closures()`：行政性和维护性关闭，永久保留但不进入 judgement。

已知 synthetic reason 的分类优先于行上其他字段，不能用 `outcome_eligible=1` 覆盖
`reconciled_gone` 或 maintenance reason。无结构化证据的旧行是 `legacy_unverified_close`，PnL 数值本身
不能证明退出。现有 `analytics.close_reasons` 只是兼容 facade，reason registry 只在中央模块维护。
Operator、KPI、Risk、API/UI 等行为消费者已迁移并由 `OI-C02~C04` 的枚举式架构测试约束；
新增消费者仍必须显式选择这三种读取口径之一。

### 11.5 持久事故断路器（OI-B04/C01 implemented）

当前语义版本下的 administrative/maintenance close 之后，若同一完整账户与 instrument identity
出现任何 native 或 adopted open event，短窗内达到两个独立 identity 即形成系统性事故。探测器在
demo auto-adopt/orphan-close 之前运行；active 事故写入 `reconciliation_integrity_incidents`，使这两个
动作和所有会改变行为的 Learning 入口跨重启保持冻结。每次继续出现循环会重置干净计数；干净观察
必须相隔至少五分钟且累计两次，之后仍需明确 actor/reason 才能 resolve。live 继续只分类和告警，不
获得任何新增自动收口权限。

### 11.6 期货资金费用的可证来源与诚实关闭

`paper_internal` 合约使用明确标为 `local_simulation` 的确定性模型：两分钟衍生品采集持续覆盖
所有未平仓本地合约；按 `opened_at < next_funding_time <= closed_at` 的每个结算事件，采用同一
主 venue 在结算前 15 分钟内的最后持久快照。每次结算的名义金额只来自该时刻之前已经成交、
且反链到同一 Position 的 entry Order/Fill；多腿加仓按真实成交时间分段，不能用最终 Position
名义金额回溯早期结算。Order、Proposal、Risk、Position 的币对、市场、方向、执行模式以及每单
与整仓数量必须闭合；首笔/primary Fill 必须与 Position `opened_at` 在一秒持久化写序容差内对齐，
更早且超出该容差的 Fill 视为损坏事实；Fill 恰好落在结算时刻也视为边界不明并 fail closed。完整事件按多头成本为
正、空头返还为负聚合。开仓锚点、每次结算后的下一 schedule 和平仓后的未来 schedule 必须完整，
事件快照随 close Order/Fill 在同一事务写入 `reconciliation_evidence.funding`。中央关闭入口再次
核对证据的 source、Position、symbol、持有时间窗和逐事件合计；不完整/异常结果必须显式覆盖旧
funding 为 `NULL/unavailable`。这只是可复现的本地模拟，不冒充交易所账户扣款。

资金费会计身份使用
`(exchange, canonical_symbol, canonical_settlement_at)`；解析后的时间统一为 UTC，并只把严格小于
一秒的序列化偏差归一到同一整秒边界。原始 snapshot ID 和时间戳继续保留在 evidence 中；同一
canonical bucket 的明确历史结算值优先于结算前 live observation，两个不同历史费率则
`conflicting_settlement_rates` fail closed，绝不静默选值或重复计费。

正常关闭使用 `live_coverage` 证明开仓锚点、逐次 schedule transition 和关闭锚点。延迟恢复可使用
`historical_ledger`：只有精确 entry Fill lineage 和持仓窗内完整、单调、被关闭后首个实际结算
bracket 的历史序列才可完成；公开历史费率在这里仍是本地模拟证据，不是 `broker_ledger`。
恢复成功后 `materialize_position_economics()` 在一个 SQLite writer transaction 中同步 Position、
唯一 full close event、paper cash、reconciliation fingerprint/version、eligibility、outcome-hook outbox
和权威 audit。相同 fingerprint/result 为 no-op；已有数字的 owner-approved 修正保留原 cash 事实，
以带 position/source/fingerprint 的 `manual_credit` 或 `manual_debit` 追加差额，不删除旧事件。
`aletheia data reconcile-paper-funding` 默认只读；apply 必须显式列出 Position IDs 并匹配当次 report
fingerprint。

exchange-backed futures 仍只接受对应账户私有账单的 `broker_ledger`；公开费率不得替代真实扣款。

真实 close Order/Fill 可以结束执行生命周期，但它不能证明持仓期间的 funding。期货没有可信账户
账单，或本地模拟的事件窗口不完整时，关闭仍保留真实 Order/Fill 并把 Position 标为 `closed`，同时必须写：

- `funding_cost=NULL`、`funding_source='unavailable'`；
- `realized_pnl=NULL`，不得把未知 funding 当成 0；
- `outcome_eligible=false`、`outcome_exclusion_reason='incomplete_funding_economics'`；
- 不写 `paper_account_events.position_closed`，不创建 eligible Outcome hook，也不进入 Attribution、
  Feedback 或 Learning。

因此本地 paper 账户继续以已结算的现金账本为权威，并明确返回
`economics_complete=false` 和 `unsettled_closed_positions_count`。`available_balance_usdt` 仍反映
已确认现金减预留资金，`equity_usdt` 仍反映已确认现金加未实现损益；两者都不补入这笔未完成的
关闭损益，也不得被解释为完整经济结果。Portfolio 的相关 realized 汇总同样返回 `NULL` 并附未完成
数量，而不是用 0 提供一个看似完整的数字。spot 不产生 funding，关闭时明确写 `funding_cost=0`、
`funding_source='not_applicable'`，不受此冻结影响。
