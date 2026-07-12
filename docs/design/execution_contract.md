# Execution 执行通路契约 (L2 金标准)

> **角色**:定义订单从 proposal 到交易所的完整通路,及其原子性/幂等/对账保证。
> **基线**:代码为底(file:line),意图差距标 ⚠️;冲突时文档为准。
> **不变式来源**:[trust_and_change_control §1, §6.3](../governance/trust_and_change_control.md)。
> Last updated: 2026-07-12 · 维护者:Aletheia Dev

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
  → positions (从 orders/fills 投影;schema 现直接携带 `proposal_id` + `order_id` + `opportunity_id`,新 position 开仓即写,adopt 也走同一路径 — #30/#50)
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

`_is_live()` 判定:`execution_mode in (bitget/okx/binance)_live_limited`(`engine.py:35`)。

### 3.1 Adapter market_type 能力(交易所无关核心 + 边界适配)

核心逻辑(universe/opportunity/decision/risk)只认 canonical 类型 `spot` / `usdt_futures`,**不关心交易所**;能不能下某类型单是**各 adapter 的能力**,在 adapter 边界声明并路由(owner 原则 2)。

| Adapter | 支持 market_type | 路由要点 | 来源 |
|---------|-----------------|----------|------|
| okx | `spot` + `usdt_futures` | `SUPPORTED_MARKET_TYPES`;`okx_ccxt_symbol()` 按类型选符号(spot=canonical 无后缀 / perp=`:USDT`);spot 无杠杆/无 contractSize 换算/无 reduceOnly,市价买用 `tgtCcy=base_ccy` | `okx/client.py` |
| bitget | `usdt_futures`(demo 仅永续) | demo 模拟盘只提供 USDT 永续 | `bitget_broker.py:11` |

> **OKX demo 现货已实测**(2026-06-28):demo 沙盒列 522 现货标的,真下单 buy+sell 全程通过。详见 `docs/research/DEMO_LIVE_SPOT_PARITY_EVENT_2026-06-28.md`。spec:`tests/specs/spec_spot_parity.py`(SP-S01~S06)。
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

每个重要状态转换写 `data/logs/audit.jsonl`,最小字段见 [trust §1.4](../governance/trust_and_change_control.md):`timestamp/actor/source/event_type/entity_type/entity_id/message`,可选 `before/after`。`entity_id` 必须指向实际被改的行;审计消息不得含密钥/签名。

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
| 4 | market_aware_exit | **仅当 1–3 都未触发**且 `market_aware_exits.enabled`;`urgent_exit` 才平,`tighten_stop` 只收紧不平 | `exit_rules.py:27`,调用点 `position_manager.py:352` |

> (#105) 原优先级-4 的 `ruleset_exit` 触发器已随规则引擎(Position C)删除——`_decide_exit`
> 不再消费 `ruleset_exit_position_ids`,该触发器不再是活跃退出路径,请勿再加回。

**System Operator 退出路径独立**:`review_portfolio_state()`(`commands.py:1922`,LLM 决策)经**同一** `close_position_routed()` 入口平仓,**不进入** `evaluate_open_positions` 自动循环——两条路径汇合于路由中枢,不在触发器优先级链内。

### 10.2 close_position_routed 路由契约(唯一平仓中枢)

`close_position_routed(pos_id, reason, fallback_exit_price)`(`position_manager.py:28-79`)是**唯一**平仓出口,自动循环与 PI 路径都经此:

- 按 `execution_mode` 分派:`bitget_demo→close_demo_position` / `okx_demo→close_okx_position` / `binance_testnet→close_testnet_position` / `paper_internal→本地 close`。
- **live 守卫(不变式)**:`live_limited` 模式且 `allow_live=False` → `RaiseError`,阻止自动循环误平**真实资金**持仓(`position_manager.py:59`)。守卫在中枢层,不分散到各调用方。
- 交易所腿不可跳过:demo 模式仍走真实交易所 close;`paper_internal` 本地撮合。

### 10.3 paused / kill_switch 对平仓的语义(核心不变量)

> **`paused` 与 `kill_switch` 对持仓生命周期语义一致:都只挡"开新仓",都不挡"管理/平掉已有仓"。**
> 理由:平仓是**管理已有风险**(止损/止盈/对账),冻结它等于在最需要保护时撤掉保护;开仓才是**承担新风险**,应被暂停/熔断挡住。

| 控制 | 挡开仓 | 挡平仓 | 实现 |
|------|--------|--------|------|
| `kill_switch` | 是(风控 check#0,只拦 proposal=开仓) | **否** | `position_manager` 绕过;`test_position_manager.py:147` + `RUNBOOK.md:181` 锁定 |
| `paused` | 是(`_run_paper_trader` 早退 + 下游风控) | **否** | `_run_position_manager`/`_run_position_reconcile` **无 paused 门**;`spec PL-PM-07` 锁定 |

历史缺陷(已修 2026-06-30):`scheduler.py:_run_position_manager` 曾 `if cfg.runtime.paused: return`,导致暂停时止损循环停摆(仅 paper/demo;`paper_internal` 无交易所原生 TPSL 兜底,真失保护)。修复=移除 paused 门 + `PL-PM-07` 回归锁。

### 10.4 stop/take-profit 兜底

`_effective_stop` / `_effective_take_profit`(`position_manager.py:92-115`):优先用 position 行记录的 `stop_price`/`take_profit_price`;无记录时回退 config 默认百分比。**无 stop 的仓位仍有兜底保护**,不裸奔。
`_sync_exchange_closed`(`position_manager.py:311`,`248-296`):每轮先收敛交易所原生 TPSL(bitget demo bracket)已触发的平仓,避免本地-交易所长期 desync。

### 10.5 已知状态与边界

- ⚠️ **live 人工平仓路径未实现**:`allow_live=True` 全库无调用点(`position_manager.py:53` 仅文档注释);前端无 force-close 按钮,无 `/api/positions/{id}/close` 端点。**当前 `live_limited` 仓位须在交易所界面手动平仓。** 未来加 close 端点**触信任边界**,须走 [trust §6](../governance/trust_and_change_control.md) change-control + 人工确认(`allow_live=True`)。
- ⚠️ **live 对账仍受 paused**:`_run_reconciliation`(live-only,`scheduler.py:822`)仍有 paused 门,本次未改(live 默认禁用,且仅做收敛检测,优先级低)。若未来 live 常开,应一并对齐本节语义。

**Spec claims**:`PL-PM-01~06`(mark/stop/TP/max_hold/ATR/audit) + `PL-PM-07`(paused 不挡保护性平仓)。`spec_pipeline_position_manager.py`。

## 11. Position 关闭与结果完整性（OI-S01/S02/B01 implemented；其余行为待实现）

`positions.status='closed'` 只表示本地状态生命周期结束，不自动证明发生了策略退出成交。系统必须把
关闭分为两条不互相冒充的路径：

| 路径 | 必需证据 | 允许的副作用 | 结果资格 |
|---|---|---|---|
| verified strategy close | 本地 execution fill，或当前 broker/account/instrument 的可验证退出 fill | 真实 fee、slippage、PnL、cash settlement、outcome-only hook | 通过其余干净窗/归因门后可 eligible |
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
alert-only/fail-closed，不借本合同扩大自动权限。OI-B01 已实现到“ready for verified exit”为止；
ready 本身不触发关闭，等待 §11.2/§11.3 的后续任务。

### 11.2 Broker-neutral verified exit

状态：RG-T06 / OI-B02 已实现只读 protocol 与三家 adapter；尚未触发关闭或结算，调用编排由
RG-T07 接入。

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

administrative-unverified close 不得：

- 用 entry/mark/current fallback 伪造退出 fill；
- 新增手续费、滑点、已实现策略 PnL或 `paper_account_events` 结算；
- 触发 calibration、shadow resolution、strategy KPI、Learning case/reviewer 等 outcome-only hook；
- 被 Risk 当成权威账户损失。

它必须保留 causal ids、actor/source、确认依据和审计事件。原始历史值永久保留，新增结构化字段只描述
其 provenance 和 eligibility。对应 `OI-B01~B04`、`OI-C03`。

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
当前交付的是语义和读取能力；Operator、KPI、Risk、API/UI 等迁移仍由 `OI-C02~C04` 约束。

### 11.5 持久事故断路器（OI-B04/C01 implemented）

当前语义版本下的 administrative/maintenance close 之后，若同一完整账户与 instrument identity
出现任何 native 或 adopted open event，短窗内达到两个独立 identity 即形成系统性事故。探测器在
demo auto-adopt/orphan-close 之前运行；active 事故写入 `reconciliation_integrity_incidents`，使这两个
动作和所有会改变行为的 Learning 入口跨重启保持冻结。每次继续出现循环会重置干净计数；干净观察
必须相隔至少五分钟且累计两次，之后仍需明确 actor/reason 才能 resolve。live 继续只分类和告警，不
获得任何新增自动收口权限。
