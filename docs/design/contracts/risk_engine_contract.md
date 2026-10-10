# Risk Engine 决策契约 (L2 金标准)

> **角色**:定义 Risk Engine 作为"agent 不可绕过的硬墙"的完整决策契约。
> 这是 trust 边界的核心——[Product Direction](../../product/definition/DIRECTION.md) 要求失败可生还且自主权只能逐级获得。
> **基线**：本契约描述当前权威行为；偏差进入 GitHub Issues。
> **产品范围**：只交易 USDT 本位加密永续，唯一获准新入场路由是 `bitget_demo`。下表中涉及现货、`okx_demo`、`binance_testnet` 的分支是主线不可达或仅服务冻结路由的残留，不是支持声明。
> **不变式来源**:[trust_and_change_control §6.3](../../governance/policies/trust_and_change_control.md)。

---

## 1. 入口与序列化

| 项 | 值 | 位置 |
|----|----|----|
| 主入口 | `evaluate_proposal(proposal: TradeProposal) -> RiskCheckResult` | `core/aletheia/risk/engine.py:790` |
| 并发保护 | 全局线程锁 `_eval_lock`,防 kill-switch TOCTOU | `engine.py:85` |

**契约**:所有 proposal 必须且仅能通过 `evaluate_proposal` 取得放行;同一时刻只有一个 proposal 在评估(序列化)。

**唯一入口不变式(RE-B17)**:执行网关 `_execute_proposal` 中每一条调用 broker 下单函数（Gateway → Coordinator → UTA）的代码路径,必须在 `risk_result.approved` 检查通过后才能到达——即 risk evaluation 必须前置且其结果被显式消费。不存在绕过 `evaluate_proposal` 直接触达 broker 的路径。可执行测试:spec `RE-B17`。

### 1.1 Demo / Live policy identity（v2）

Risk Engine 仍只有一个入口和一套检查实现，但参数由 Proposal 已验证的
`execution_mode` 显式解析到 `risk.demo` 或 `risk.live`。未知 mode 必须
`unregistered_risk_execution_mode` fail closed，不能按 Demo 默认处理。

每条 RiskDecision 保存 `risk_mode/policy_id/policy_revision/policy_hash`；
authoritative 幂等 identity 包含 policy hash，因此 policy 修改后必须重新评估，
不得复用旧批准。Demo/Live policy 可由人类比较、复制或整体替换，但
`require_risk_check_for_all_orders`、live unlock、permission、credentials 和
运行时 brake 不属于可复制 policy。

Risk Policy 的 schema/domain validator 是所有入口的唯一数值权威。持久化 nested
policy、flat legacy migration、局部 Settings 更新、整体替换、复制与 reset 均须在
生成 hash 或成为 Risk authority 前拒绝非有限数、空 identity、非正 revision 及字段级
结构非法值；Live policy 还必须应用同一套 mode-aware safety ceilings。拒绝不得改变
配置、policy hash/revision 或写入成功 audit。`demo.capital.max_total_exposure_pct=0`
与 `max_per_symbol_entries=0` 保留其明确的 disable 语义；Demo 超出 Live ceiling 的合法
容量值仍可存在，但复制到 Live 时必须失败。

**在途仓位守护冻结在开仓策略（#1911，owner 2026-10-09）**：已开 native Position 的 Layer-0
catastrophic stop（含 serenity 变体与开关）、runtime liquidation buffer 与 horizon hard backstop
一律按其入场 Order 的 RiskDecision `policy_hash` 所标识的策略评估；Risk Policy 替换、复制、reset
只作用于之后的新入场评估，不改变在途仓位的规则。策略内容按既有 `policy_hash` 不可变留存于
`risk_policy_snapshots`（RiskDecision 持久化时写入，不新增第二个 policy identity）。入场策略无法
解析（无入场 Order/RiskDecision、hash 为空、快照缺失或无效）时不得静默：该 Position 在
`evaluate_open_positions().errors` 中每轮报告 `entry_risk_policy_unresolved:<reason>`，并写一次
`entry_risk_policy_unresolved` 审计；守护在此期间按当前策略继续运行，绝不撤除保护。adopted
Position 没有入场策略，按当前策略守护。

---

## 2. 检查关卡（主关卡与 Entry Plan 子关）

> **四个新增关卡的失败 token(spec RE-S04b 锁定,floor ≥ 20)**:
> - 6b → `symbol_already_open:{sym}`
> - 17 → `account_leverage_ratio:{r}x>{cap}x` / `account_leverage_check_error:invalid_position_exposure:position={id}:field={category}` / `account_leverage_check_error:internal_failure`
> - 18 → `liquidation_too_close:{g}%<{b}%`
> - 19 → `exceeds_per_symbol_notional:{sym}:{proj}>{cap}`

| # | 关卡 | 判断 | config 字段 | 失败标签 | 位置 |
|---|------|------|------------|---------|------|
| 0 | Trading Brakes | global brake 阻断全部；demo/live mode brake 只阻断对应 pool；三者同时以带 scope/generation 的 durable pause owner 投影，resume authority 仍先检查独立 brake | `runtime.kill_switch_active/demo_brake_active/live_brake_active` + `runtime.pause_owners` | `kill_switch_active` / `demo_brake_active` / `live_brake_active` | engine.py / pause_ownership.py |
| 1 | Symbol Filter | white/blacklist | `risk.symbol_filter_mode/symbol_list` | `symbol_blocked/not_allowed:{sym}` | :83 |
| 1a | Entry Plan Authority | 绑定 immutable Plan、current generation、生命周期与有效期、exact instrument/account、价格/保护/tactic envelope、Risk policy identity 与资本上限；execution receipt 的类型、Proposal/Plan identity 与 hash 由 Gateway 验证并绑定 RiskDecision | Entry Plan + execution receipt | `entry_plan_*` / `entry_execution_receipt_*` | engine.py `_entry_plan_authority_violations` / gateway.py |
| 2 | Market Allowed | spot/futures 开关 | `exchange.enabled_markets` | `spot_market_disabled`（market_type=='spot' 且 enabled_markets['spot']==False）/ `futures_market_disabled`（market_type=='usdt_futures' 且 enabled_markets['usdt_futures']==False） | :93 |
| 2b | Complete Entry Plan | 每个 native exchange limit 入场必须在 Risk 前固定有限正数的 entry/stop/take-profit/risk_reward；多空几何正确，risk_reward 必须等于 `round(abs(tp-entry)/abs(entry-stop), 2)` 且该结果必须在包含边界的 `[1.00, 10.00]` 合理性包络内。exchange market close/reduce 与 adopted chain 豁免 | —（Proposal immutable fields） | `incomplete_entry_plan:{fields}` / `entry_plan_geometry:*` / `entry_plan_risk_reward_mismatch` / `entry_plan_risk_reward_outside_envelope` | engine.py check #2b/#2c |
| 3 | Live State | 非 `live_disabled` | `live.state` | `live_disabled` | :99 |
| 4 | Permission Mode | Observe 不允许提交订单 | `agent.permission_mode` | `permission_mode_insufficient:{mode}` | :104 |
| 5 | Max Position Margin | live/demo 配置上限与对应账户缓存的可用 USDT 取较小值，再应用回撤缩放；无可用缓存时保留配置上限。Demo 默认 100,000 USDT，设置 API 只保留正数下限；live 边界不变 | `risk.max_{live/demo}_position_margin_usdt` + local exchange-account cache | `exceeds_max_position_margin:{n}>{cap}` / `exceeds_demo_position_margin:{n}>{cap}` | engine.py `_effective_position_margin_cap` |
| 6 | Max Open Positions | live/demo 持仓数；demo 默认 200，设置 API 只保留正整数下限 | `risk.max_open_positions_{live/demo}` | `max_{live/demo}_positions_reached:{n}` | :127 |
| 6b | Cross-pool Symbol Dedup | 同 live/demo 池内同标的禁止重复开仓(防跨 broker 重复) | —(始终启用) | `symbol_already_open:{sym}` | :141 |
| 7 | Short Allowed | 方向合法 | `risk.allow_short_{live/demo}` | `short_not_allowed_{live/demo}` | :138 |
| 8 | Max Daily Loss | **默认关闭**：`risk.max_daily_loss_pct` 默认 `0`，此时整关不执行，也不读取 account snapshot（该快照仅服务本关）。配置正数百分比后启用，行为不变：只读 authoritative account snapshot；来源不可用→拒绝但不伪造 breach；真实日损突破→**自动触发 kill switch** | `risk.max_daily_loss_pct`（`0`=关闭，demo/live 默认均为 `0`）、`kill_switch_enabled_{live/demo}` | 关闭时不产生；启用时 `account_loss_unavailable:{reason}` / `max_daily_loss_exceeded:{r}<=-{lim}` | engine.py check #8 |
| 9a | Exchange Allowlist | `AccountConfigBinding.GLOBAL` 账户必须匹配；OKX/Binance 保留既有 `SELF_CONTAINED` 豁免 | `risk.allowed_exchange` + `EXECUTION_MODE_ACCOUNT_CONFIG_BINDINGS` | `exchange_not_allowed:{a}!={e}` | engine.py check #9a |
| 9b | Account Mode Match | `GLOBAL` 账户要求 live↔mode=live / demo↔mode=demo；`SELF_CONTAINED` 保留既有豁免 | `exchange.mode` + `EXECUTION_MODE_ACCOUNT_CONFIG_BINDINGS` | `account_mode_mismatch:...` | engine.py check #9b |
| 10 | Max Leverage | 期货杠杆上限；demo 默认 125x，仍受设置 API 的可执行上界约束 | `risk.max_futures_leverage_{live/demo}` | `exceeds_max_leverage:{l}>{cap}` | :181 |
| 11 | Data Freshness | 只接受 Proposal 对应 venue + physical market 的精确快照；同 symbol 的其他 venue/market 不得替代。live<120s / demo<1800s | `_MAX_DATA_AGE_SECONDS=120` / `_MAX_DEMO_DATA_AGE_SECONDS=1800` | `stale_market_data:age={a}` (live); `stale_market_data_demo:age={a}` (registered demo modes) | engine.py `_market_data_age_seconds` |
| 12 | Total Account Exposure | 同一账户池的 open Position、未决 entry Order、pre-Order Risk reservation 与新单名义敞口之和不得超过该模式资本基数的配置比例；每个 exchange demo mode 独立，live 保持合并池 | `risk.{demo/live}.capital.max_total_exposure_pct` | `max_total_live_exposure_exceeded:{p}>{cap}` / `max_total_demo_exposure_exceeded:{p}>{cap}` | engine.py check #12 |
| 13 | Single Trade Risk | 止损距离×数量<上限；`max_single_trade_risk_pct` 只有一种语义：资本基数中允许在止损处亏损的比例。已融资 Entry Plan 的上限是其冻结的 `sizing_resolution.effective_loss_budget_usdt`（已含 AI RiskIntent 倍率），不再是原始策略百分比；历史 Plan 保持原式。demo 默认 20%，设置 API 只保留正数下限 | `risk.max_{/demo_}single_trade_risk_pct` | `max_single_trade_risk_missing_stop_price` (live, no stop_price, engine.py:207); `exceeds_single_trade_risk:{r}>{max}` (live, over cap); `exceeds_demo_single_trade_risk:{r}>{max}` (demo); `entry_plan_max_risk_exceeded` (financed Plan over its frozen budget) | :202 |
| 14 | Scanner-Family Evidence | live 需该单 `scanner_family`(Entry Plan 单取 Plan `candidate_family`，其余经 opportunity 解析)在干净窗有 ≥N 已结算证据且方向胜率≥门槛(取代已删的策略 FSM 准入,Position C 2026-07-02;fail-closed:无 family/无证据/样本不足/胜率不足均拒) | `risk.live_family_min_samples`(30)/`risk.live_family_min_win_rate_pct`(50.0) + `repo.family_direction_edge` | `family_not_live_eligible:{unknown/family:no_evidence/family:samples=n<N/family:wr=w<T}` | :211 |
| 14b | Exchange-demo entry quality | 仅 `bitget_demo`/`okx_demo`/`binance_testnet` 的**新开仓**：已发布 regime confidence ≤ 门槛（mean_reversion 豁免 confidence）、已发布 regime 为 sideways（**含 mean_reversion**）、family×direction 在干净窗 n≥门槛且 expectancy&lt;0、或同标的 24h 内 `stop_loss` 后反向，则拒。未发布 regime / 样本不足 fail-open；policy epoch、regime、family-edge、recent-stop authority 读取失败则 fail-closed 并保留来源 token。live #14 不变；不平已有仓 | `risk.demo.eligibility.min_regime_confidence_to_enter`(0.60) / `sideways_standdown`(true) / `family_min_samples`(20) / `family_min_expectancy`(0.0) | `regime_confidence_standdown:*` / `regime_sideways_standdown:*` / `family_not_demo_eligible:*` / `reverse_after_stop:*` / `demo_entry_quality_unavailable:<source>` | `core/aletheia/risk/demo_entry_quality.py` |
| 15 | Lesson Constraints | 已接受且未过期的 manual/owner/human block 规则保持静态硬约束；自动来源默认 advisory，只有 exact `risk_lesson_constraint` prospective treatment 可阻断该 Proposal，control/default/error 不得阻断 | `LessonStore.recommendation_json` + Learning path authority | `lesson_block_{symbol/...}:{id}:{v}` / `lesson_check_error:internal_failure` | engine.py `_check_lesson_constraints` |
| 16 | Portfolio Correlation | 部门集中度(每笔已有敞口——持仓及两类预留——与新单用同一 family 解析器：经创建它的 Proposal 的 `entry_plan_id` 取 Plan `candidate_family`，否则经 opportunity；两者皆无则 family 未知) + 组合 BTC beta；持仓数低于 `correlation.min_positions_for_concentration`(默认 4)时跳过，少于 4 个持仓时浓度百分比粒度不足以区分信号 | `risk.correlation_guard_enabled/max_sector_concentration_pct/max_portfolio_beta/min_positions_for_concentration` | `sector_concentration/portfolio_beta:...` | :245,387 |
| 17 | Account Leverage Ratio | 期货:同一账户池的 Position + 未决 entry Order + pre-Order reservation 的 Σ(notional)/同账户 equity ≤ 上限；不同 demo broker 不混算；外部 equity 必须来自新鲜健康的具名缓存；相关 Position 的 market type/数量/价格/杠杆非法时拒绝，不能当零敞口（spot 的 NULL leverage 按其结构语义计 1x，显式非 1x 反而拒绝）。Demo 默认 10x，设置 API 只保留正数下限；live 保持 1.5x | `risk.max_account_leverage_{live/demo}` + named local cache | `account_leverage_ratio:{r}x>{cap}x` / `account_equity_unavailable:{mode}` / `account_leverage_check_error:invalid_position_exposure:position={id}:field={category}` / `account_leverage_check_error:internal_failure` | engine.py check #17 (RE-B23~28) |
| 18 | Liquidation Buffer | 期货(lev>1):估算爆仓价距入场 ≥ buffer% | `risk.min_liquidation_buffer_pct`(5.0) | `liquidation_too_close:{g}%<{b}%` | :695 (FL-S09) |
| 19 | Per-symbol Cumulative Notional | 同标的同池累计名义(含本单)≤ 上限(CUM-ADD 均价合并会绕过 #5 单仓上限)；上限由 sizing 公式推导：`capital_base × risk_pct × leverage × max_per_symbol_entries`，永不与首单产生矛盾。**已融资 Entry Plan 不走本关**：改为 #20 的 risk-unit 上限 | `risk.capital.max_per_symbol_entries`(无量纲次数，默认 3) | `exceeds_per_symbol_notional:{sym}:{proj}>{cap}` | :285 |
| 20 | Per-symbol Risk Units (financed Entry Plan) | 同标的同池累计**止损风险** ≤ `effective_loss_budget × max_per_symbol_entries`；`max_per_symbol_entries` 是整仓位风险单元计数，leverage 无法放大它（#3729 §6.3） | `risk.capital.max_per_symbol_entries` + Plan 冻结 `sizing_resolution.effective_loss_budget_usdt` | `exceeds_per_symbol_risk:{risk}>{cap}(entries={n})` | engine.py `_entry_plan_financing_violations` |
| 21 | Financing Drift | 已融资 Entry Plan：用提交时 fresh facts 重跑同一套纯函数；重算出的 `execution_notional` 小于 Plan 冻结值则拒；变大利不自动放大（保持原值）。Risk 永不改写 leverage/notional | Plan `sizing_resolution`/`leverage_resolution` + fresh account/portfolio/venue facts | `SIZING_RESOLUTION_DRIFT` / 重算的 typed 失败（`SIZING_CAPITAL_UNAVAILABLE`、`VENUE_MARGIN_TIERS_*` 等） | engine.py `_recomputed_entry_financing` |
| 22 | Financed Leverage & Margin Mode | 已融资 Entry Plan：Proposal 的 `leverage` 必须等于 Plan 的 `execution_leverage`（margin mode 的唯一 authority 仍是 Plan，Gateway 从冻结 Plan 读取后交给 venue，Proposal 不携带）；perp 的 margin mode 必须是 V1 已验证的 isolated，cross 一律拒（不得用 isolated 估算冒充 cross 安全性） | Plan `execution_leverage`/`margin_mode` + `aletheia.execution.margin_mode_policy` | `entry_plan_execution_leverage_mismatch` / `entry_plan_margin_mode_mismatch` / `CROSS_MARGIN_SAFETY_UNAVAILABLE` / `MARGIN_MODE_UNAVAILABLE` | engine.py `_entry_plan_financing_violations` |

关卡 #14b 的 epoch 写入由既有 Web/CLI 启动入口绑定
`learning.demo_entry_quality.ensure_demo_entry_quality_policy_epoch`，启动绑定不执行写入。
Core 在原质量检查时机调用它；组件缺失或写入失败仍返回
`demo_entry_quality_unavailable:policy_epoch`，不会跳过其他质量检查或改变 Live 行为。
epoch 的内容、哈希、历史保留与旋转规则不变。

> ⚠️ 关卡 #11 的 `_MAX_DATA_AGE_SECONDS` 是模块常量,非 config 字段。若要 config 化需迁移 + 测试。
> 历史展示仍可使用 symbol-only fallback；该兼容读取不构成 Risk freshness authority。spec: `PL-MD-07/09`。
>
> **Entry Plan 入场**：Risk 验证已持久化的 Plan 与 Proposal 意图、当前 generation、policy identity 和
> 价格/风险边界。Gateway 只接收 identity/hash 有效的 typed execution receipt；成交事实来自 UTA
> 私有交易所回执。旧本地入场门与撮合规则见[归档](../history/local_simulated_risk_execution.md)。
>
> ⚠️ 关卡 #5 的回撤缩放(`_portfolio_drawdown_scale`, engine.py:356):缩放系数(0-5% DD→1.0, 5-10%→1.0→0.5, 10-20%→0.5→0.0, >20%→0)由组合回撤驱动。**回撤的数据源**:交易所模式(okx_demo/*_demo/live)读 `portfolio_snapshots` 的 `exchange_account` 行(真实账户 equity),**不读**每个 mode 自己的行——后者的 `total_value_usdt` 是持仓 notional(Σqty×price)而非 equity,平掉盈利持仓会让 notional 暴跌被误判成回撤、把 cap 缩到 $0 锁死开仓。spec: RE-B05（分段）+ RE-B05a（交易所数据源）。

> 关卡 #5 的账户余额收紧：下单时只读本地交易所账户缓存。Bitget demo/live 分别读各自模式缓存；
> OKX/Binance 读统一缓存中与 execution mode 同名的账户。有效上限是
> `min(configured_hard_cap, available_usdt)`；没有可用缓存时保留配置上限。缓存只能收紧上限。
>
> 关卡 #5 的比较量：比较的是 **pledged cash（保证金）= notional / leverage**，而非原始名义价值。原始名义价值与可用现金相比时，任何杠杆 > `1/risk_pct` 的单子都会被错误拒绝（20% 风险时为 5x）；以保证金比较后，有效杠杆上限由账户余额自然决定，不受 per_trade_risk_pct 的隐式 cap。spec: RE-B05c、RE-B05d。

> **关卡 #15 与 spec 0001**：#15 现由 `LessonStore` 的 manual/owner block 规则驱动。已批准的
> [spec 0001-learning-loop](../specs/0001-learning-loop/spec.md) 删除 `ps_lessons` 生命周期，并规定
> 建议级规则不得阻断（LL-R13），阻断只能经 LL-R19 + owner 准入。spec 实施时 #15 必须改由
> Authority tier 驱动；该合同修改应列入 spec 0001 的 Contract Delta（当前遗漏）。

> **Spot 杠杆身份（残留）**：原生 spot Proposal 的 leverage 只能为 `NULL` 或显式 `1x`，其他值在 Risk admission
> 以 `invalid_spot_leverage` 拒绝。期货的合法杠杆与保证金计算不变。

> **账户杠杆身份**：check #17 使用同一个账户池判断 open Position、未决 entry Order 和 pre-Order reservation；每个 demo execution mode 独立，live 保持合并池。分母只读同一具名账户的新鲜健康本地 equity，过期、异常或具名账户缺失时拒绝，不以 starting capital 代替；相关 Position 的 market_type/quantity/entry_price/current_price/leverage 只要缺失、非正数、非有限数或自相矛盾，也会稳定拒绝而不是从分子消失。拒绝 token 携带 sanitized Position id 与稳定 field category，避免一条坏仓位只产生无法定位的泛化重复告警。唯一结构化例外是 spot Position：spot 本身无杠杆，历史/当前模型以 `leverage=NULL` 表示 1x，因此按 1x 计入；若 spot 显式写入非 1x，则视为数据矛盾并拒绝。合约或未知 market_type 的 NULL leverage 仍拒绝。spec: RE-B23~28。

> **公共杠杆监控不得伪装安全**：`check_account_leverage()` 与 `/api/risk/account-leverage` 使用和 check #17 相同的账户池、Position 数值完整性与未决占用口径；除 open Position 外，还计入尚未形成 Position 的 unresolved entry Order 和 pre-Order Risk reservation。完整数据返回 `status=available`、`reason=null` 和布尔 `violation`；相关 Position/未决占用损坏、读取失败或具名 equity 不可用时返回 `status=unavailable`，同时 `ratio=null`、`violation=null` 并给出稳定 `reason`。当损坏来自 Position 时另返回 `invalid_position={position_id, field_category}`；不得包含账户凭证或原始交易所 payload。不能把未知结果表达成 `violation=false`，System Operator 上下文也只展示空比率。spot 的 `leverage=NULL` 仍是明确的 1x 例外。spec: RE-B28。

> **未决原生入场也占用 Risk 容量**：交易所入场 Order 在真正提交前会先以 `pending` 写入本地；
> 此时交易所是否已经接受仍未知，不能把它当成零敞口。`pending` 与其他未决状态一样，在对应
> live/demo 池中继续占用持仓槽位、保留同符号互斥，并把尚未由 open Position 表示的数量计入
> 名义敞口。若未决 Order 已有部分数量投影为 Position，只补计剩余数量，且不重复占一个槽位；
> 平仓 Order 不占新增入场容量。Demo 总敞口使用与资本基数相同的精确账户池，不同 exchange demo 账户不混算。spec: `PL-UEE-01~17`。

> **关卡 #8 的权威损失源**：Risk 不读取 `today_realized_pnl` 或策略表现聚合。只接受当前
> outcome semantics 下经济字段完整的 broker-fill close；归档账户不参与当前账户损失。当天出现
> administrative、maintenance、legacy 或费用/身份不完整的 close 时，snapshot 返回 unavailable，
> Risk 以 `account_loss_unavailable:*` 拒绝新 proposal，不伪造日损突破或自动触发 kill switch。
> 无 closed account event 的完整空集为已知 0。spec: RE-B20 / OI-C03。

> **唯一硬门 & guard 澄清(2026-06-30)**:订单通路(`submit_proposal`→执行)上,**`evaluate_proposal()` 是唯一硬门**。另有两套也叫 "guard" 的东西**不在执行通路上裁决**:
> (1) 历史 `trading_decision` 守卫链曾只写 `decision_meta['guards']`、提供 advisory；该旧入场消费者已删除，不是当前执行入口；
> (2) ~~`trust/guard_chain.py:run_guards()`~~ → **已于 2026-09-07 删除**。它从未被任何生产代码调用，因此并不观测任何东西，且其自带的 `max_daily_loss_pct` 默认 `5.0` 与 schema 的 `1.0` 矛盾。spec `EX-C03` 保留并继续生效：它扫的是 `gateway.py`，守的是「唯一硬门是 `risk/engine.py:evaluate_proposal`」这个不变式，而不是该文件的存在。
> 区分：knowledge `trading_guards` 曾在旧 Ranker 排序阶段剔除候选；该消费者现已退役（见 [knowledge_contract §4.5](knowledge_contract.md)），历史 guard 命中不授予当前 Entry Plan 执行权，**不是**执行硬门。若未来要把某条 guard 升为硬拦,必须改本契约 + 接线 gateway + 改 EX-C03,不可静默接线。

---

## 3. 返回结构

```python
@dataclass  # engine.py:54-62, 镜像 storage/models.py:125
class RiskCheckResult:
    approved: bool = False
    risk_score: float = 0.0          # len(violations)*0.2, cap 1.0
    violations: list[str]            # 所有失败标签(见 §2)
    allowed_execution_mode: str = ""  # approved=原模式; rejected=空，无执行授权
    message: str = ""
    decision_id: str | None          # 持久化的 RiskDecision.id
```

**契约**:每次评估**必须**持久化一条 `RiskDecision`(`engine.py:1638-1700`),无论批准或拒绝——这是因果链 `proposal→risk_decision→order` 的一环([trust §1.1](../../governance/policies/trust_and_change_control.md))。

authoritative `RiskDecision` 的最终 Risk filter trace 与 canonical outcome seed 是同一事务中的必需证据；任一 evidence 写入失败必须回滚该 `RiskDecision` 及后续预留，Gateway 不得收到批准结果。preflight 仍是 advisory，不创建该最终 evidence。

**权威快照契约**：交易所最小数量/步长对齐可以让 authoritative Risk 评估使用不同于原 Proposal 的
`quantity`，并把实际评估量写入 `RiskDecision.evaluated_quantity`；除此之外，所有由原 Proposal
`commit_hash` 覆盖的字段都必须与存储行完全一致。Risk 会把评估副本的 quantity 还原成存储值后重新
计算 hash；symbol、market type、方向、价格、杠杆、止损、止盈、execution mode、来源身份等任一字段
漂移都会抛出 `ProposalIntegrityError`，且不得写 authoritative RiskDecision 或 Order。原始 Proposal
不因数量对齐而被改写，RiskDecision 仍保存它的 `proposal_commit_hash`。spec: `PL-RX-03/04/05/18`。


---

## 4. Kill Switch

| 行为 | 实现 | 位置 |
|------|------|------|
| 触发 | 手动 `activate(reason, actor)` 或日损突破自动触发(#8) | `kill_switch.py:21`, `engine.py:159` |
| 持久化 | `runtime.kill_switch_active=True` → `save_config` 落盘(重启不丢) | `kill_switch.py:25` |
| 恢复 | 仅 `deactivate(actor="human")`,**actor 默认 "human"，无已注册的 agent 可调用路径** | `kill_switch.py:116` |
| 审计 | 激活/恢复均写 JSONL | `kill_switch.py:27,45` |

**不变式**:kill switch 是 §2 第 0 关,激活后任何 proposal 必被拒;恢复是不可被 agent 触达的人工操作。历史事故教训：daily-loss kill switch 的 UTC 日期边界不可按本地日期推断。

---

## 5. 仓位计算(当前权威路径)

当前新入场的规划消费者为 System Operator，Entry Plan 经 Risk / Gateway 执行。

System Operator 的权威 sizing 路径是 **自适应 sizing + 确定性杠杆解析**，
实现于 [`entry_sizing_and_financing_contract.md`](entry_sizing_and_financing_contract.md)：

```text
baseline_loss_budget = min(hard_capital_base, fresh_available_balance) x max_single_trade_risk_pct/100
effective_loss_budget = baseline_loss_budget x risk_intent_multiplier   # AI 只能保持或降低
target_notional       = effective_loss_budget / stop_fraction           # 与 leverage 解耦
margin_budget         = min(fresh_available_balance, policy.max_position_margin_usdt)
execution_leverage    = 目标敞口的“最低必要且可证明安全”杠杆（不放大 target）
```

- `max_single_trade_risk_pct` 只有一种语义：资本基数中允许在止损处亏损的比例（见关卡 #13）。
- 保证金预算（margin budget）独立于亏损预算，不再由同一字段兼任。
- `policy_leverage_cap`（major/mid/alt 档位 + futures cap）只是上限；它**不等于**实际下单杠杆。
- policy version：`adaptive-risk-intent-sizing/v2`（换代用于隔离 learning evidence cohort）。

历史路径 `available_balance_risk_budget_size()`（`available x risk_pct x leverage`）已无生产
调用者，保留为 sizing analysis helper；`vol_target_size()`、`trading.default_notional_usdt=25`、
`vol_kelly_cap_fraction` 等同为旧版兼容字段，不影响当前权威路径。legacy conviction/Kelly/persona
sizing 不得接回 canonical Entry 路径。

**关键契约**：sizing 计算结果**不是**最终下单量；它仍须通过 §2 各关（#5 保证金上限、#12 总敞口、
#13 单笔风险、#17 账户杠杆比、#18 爆仓缓冲、#20 同标的 risk units）。Risk Engine 只能批准或拒绝，
不会把超限请求优化或裁剪成另一个仓位；已融资 Plan 的冻结果与冻结杠杆必须与提交时 fresh facts
重算结果一致（#21），否则 `SIZING_RESOLUTION_DRIFT` 拒绝而不改写。

---

## 6. 硬限不可绕过的保证

1. **执行模式**：native Proposal 仅接受六种已注册外部模式；Live 还需人工解锁及配置身份验证。
2. **序列化锁**:`_eval_lock` 防 TOCTOU(§1)。
3. **纵深防御**:Risk Engine 放行后,Execution Gateway 再查一次 live/account mode(`gateway.py:201`)。
4. **持久化**:kill switch 落盘,重启保持。
5. **审计**:所有决策入 DB + JSONL。
6. **快照绑定**:authoritative Risk 只允许交易所数量归一化；其他 hash-covered Proposal 意图不得替换。

**[trust §6.3](../../governance/policies/trust_and_change_control.md) 红线**:不得在未经显式用户批准下削弱或移除任何 risk check;新增 check 必须持久化 RiskDecision 且对 API/UI 可见;测试须覆盖 approved + rejected 两条路径。

---

## 7. 已知缺陷

当前偏差只在 GitHub Issues 中跟踪，本节不维护清单。

---

## 8. 目标态差距(意图领先代码)

- Planner 的入场方向通过 Entry Plan 进入订单。Risk 验证账户和订单安全边界，不证明方向具有收益优势；旧 Paper Trader 的调用链说明仅作为[历史分析](../history/execution_path_honesty_202606.md)保留。
- ⚠️ 不要把 market-state guard(如资金费率熔断)塞进 Risk Engine——见 [external systems comparison §10.6](../../references/external-systems/comparative_systems.md),那属策略层。

---

## serenity_isolation — Serenity 当前隔离边界

### 当前现状

| 维度 | 当前实现 |
|------|---------|
| 执行模块 | `serenity_executor.py` 不存在；所有提案均通过主赛道 System Operator / Gateway |
| 头寸池 | check #5/#6 共用同一个池，Serenity 占用主赛道配额 |
| PnL 归因 | `analytics/` 和 `portfolio/` 不按 `trade_school` 提供隔离的权威 PnL |

`D2-S02` 保持负向 tripwire，防止独立执行器在没有信任边界审查时
出现。任何独立执行入口、风险池或权威 PnL 隔离都是跨域且触及信任
边界的变化，必须先写成 [spec](../specs/README.md)；它们不是本 Contract 的未来待办。

### evaluate_proposal 唯一入口不变式

**所有 TradeProposal**（Entry Plan 执行经 `execute_existing_proposal`，调试 CLI 经 `submit_proposal`）在任何 broker 调用之前**必须经过 `evaluate_proposal()`**。

- `gateway._execute_proposal` 是唯一通向 broker 下单的函数
- `evaluate_proposal` 在 `_execute_proposal` 内调用，`risk_result.approved` 守卫在所有 broker 调用之前
- 不存在绕过 risk engine 直接下单的代码路径

spec：`D2-C01`（L2c，spec_risk_engine.py）持续锁住此不变式。
