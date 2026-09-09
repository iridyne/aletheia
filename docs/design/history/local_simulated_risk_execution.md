# Archived local simulated Risk and execution contracts

Paper Internal 的入场、撮合、账户现金、保护与恢复机制已退役。以下旧契约摘录仅解释历史记录，
不定义当前功能，不授予任何执行或经济资格；数据库历史行与 immutable identity 不作改写。

## Former Risk checks and decision schema

旧表包含共享风控关卡，保留上下文用于理解已经退役的本地子关；当前外部账户规则见
[Risk Engine 契约](../contracts/risk_engine_contract.md)。原 broker 入口 `execute_paper_order`、
`create_paper_proposal` 的 `_PAPER_MODES` 白名单和 `paper_trader` sizing helper 均已删除。

## 2. 检查关卡(代码实际 20 个主关卡，另含本地纸面入场子关与 6b)

> ⚠️ **偏差**:[SYSTEM_ARCHITECTURE_GUIDE](../architecture/SYSTEM_ARCHITECTURE_GUIDE.md) 第五章称"五关审批",代码实际是 20 关(0–16 + 6b/17/18/19)。
> 以下 20 个主关卡及表内子关为金标准;ARCH_GUIDE 的"五关"是简化叙事,待该文档批次3标注。
> 历史:本表曾止于 16 关;6b(跨 broker/池同标的去重)、17(账户杠杆比)、18(爆仓缓冲)、19(单标的累计名义)为后续追加,**2026-06-30 补齐契约**(代码↔spec 早已存在,FL-S08/S09 守 17/18;契约滞后已修)。
> **四个新增关卡的失败 token(spec RE-S04b 锁定,floor ≥ 20)**:
> - 6b → `symbol_already_open:{sym}`
> - 17 → `account_leverage_ratio:{r}x>{cap}x` / `account_leverage_check_error:invalid_position_exposure:position={id}:field={category}` / `account_leverage_check_error:internal_failure`
> - 18 → `liquidation_too_close:{g}%<{b}%`
> - 19 → `exceeds_per_symbol_notional:{sym}:{proj}>{cap}`

| # | 关卡 | 判断 | config 字段 | 失败标签 | 位置 |
|---|------|------|------------|---------|------|
| 0 | Trading Brakes | global brake 阻断全部；demo/live mode brake 只阻断对应 pool；三者同时以带 scope/generation 的 durable pause owner 投影，resume authority 仍先检查独立 brake | `runtime.kill_switch_active/demo_brake_active/live_brake_active` + `runtime.pause_owners` | `kill_switch_active` / `demo_brake_active` / `live_brake_active` | engine.py / pause_ownership.py |
| 1 | Symbol Filter | white/blacklist | `risk.symbol_filter_mode/symbol_list` | `symbol_blocked/not_allowed:{sym}` | :83 |
| 1a | Local Paper Market Authority | generic `native + paper_internal` 仍要求 exact fresh mark 与 Proposal.price 一致。Entry Plan Limit 改为验证短期 `EntryExecutionReceiptV1`：exact Plan/instrument/generation/channel、fresh BBO、Maker passive 或 IOC BBO+depth 边界；Proposal.price 是 order constraint，不是第二条 current-mark 声明 | `market_data.ccxt_exchange` + execution receipt | `paper_market_data_unavailable:*` / `proposal_price_not_current_mark`（generic）/ `entry_execution_receipt_*`（Entry Plan） | engine.py local-paper authority block |
| 1b | Local Paper Causal Identity | authoritative `native + paper_internal` 必须在 `opportunities` 与 `serenity_opportunities` 中恰有一个 durable carrier，且 canonical symbol、physical market、direction 完全一致 | —（始终启用） | `proposal_opportunity_required/not_found/ambiguous/symbol_mismatch/market_mismatch/direction_mismatch/check_error` | engine.py `_paper_opportunity_identity_violations` |
| 2 | Market Allowed | spot/futures 开关 | `exchange.enabled_markets` | `spot_market_disabled`（market_type=='spot' 且 enabled_markets['spot']==False）/ `futures_market_disabled`（market_type=='usdt_futures' 且 enabled_markets['usdt_futures']==False） | :93 |
| 2a | Local Futures Funding Ready | `paper_internal` 合约开仓前须有该 symbol 15 分钟内的主 venue 下一结算快照 | `funding_rate_snapshots` | `local_paper_futures_funding_unavailable` | engine.py check #2a |
| 2b | Complete Entry Plan | 每个 native 本地纸面 market 入场及 native exchange limit 入场必须在 Risk 前固定有限正数的 entry/stop/take-profit/risk_reward；多空几何正确，risk_reward 必须等于 `round(abs(tp-entry)/abs(entry-stop), 2)` 且该结果必须在包含边界的 `[1.00, 10.00]` 合理性包络内。exchange market close/reduce 与 adopted chain 豁免 | —（Proposal immutable fields） | `incomplete_entry_plan:{fields}` / `entry_plan_geometry:*` / `entry_plan_risk_reward_mismatch` / `entry_plan_risk_reward_outside_envelope` | engine.py check #2b/#2c |
| 3 | Live State | 非 `live_disabled` | `live.state` | `live_disabled` | :99 |
| 4 | Permission Mode | Observe 仅 paper_internal | `agent.permission_mode` | `permission_mode_insufficient:{mode}` | :104 |
| 5 | Max Position Margin | live/demo 配置上限与对应账户的本地可用 USDT 取较小值，再应用回撤缩放；`paper_internal` 必须读取本地纸面账户账本，读取失败不可退回配置资金。Demo 默认 100,000 USDT，设置 API 只保留正数下限、不施加应用层最大值；live 边界不变 | `risk.max_{live/demo}_position_margin_usdt` + local exchange-account cache / paper-account ledger | `exceeds_max_position_margin:{n}>{cap}` (live path); `exceeds_demo_position_margin:{n}>{cap}` (demo path); `paper_account_balance_unavailable` | :109 |
| 6 | Max Open Positions | live/demo 持仓数；demo 默认 200，设置 API 只保留正整数下限 | `risk.max_open_positions_{live/demo}` | `max_{live/demo}_positions_reached:{n}` | :127 |
| 6b | Cross-pool Symbol Dedup | 同 live/demo 池内同标的禁止重复开仓(防跨 broker 重复) | —(始终启用) | `symbol_already_open:{sym}` | :141 |
| 7 | Short Allowed | 方向合法 | `risk.allow_short_{live/demo}` | `short_not_allowed_{live/demo}` | :138 |
| 8 | Max Daily Loss | 只读 authoritative account snapshot；来源不可用→拒绝但不伪造 breach；真实日损突破→**自动触发 kill switch** | `risk.max_daily_loss_pct`, `kill_switch_enabled_{live/demo}` | `account_loss_unavailable:{reason}` / `max_daily_loss_exceeded:{r}<=-{lim}` | engine.py check #8 |
| 9a | Exchange Allowlist | 交易所匹配(self-contained 豁免) | `risk.allowed_exchange` | `exchange_not_allowed:{a}!={e}` | :162 |
| 9b | Account Mode Match | live↔mode=live / demo↔mode=demo | `exchange.mode` | `account_mode_mismatch:...` | :170 |
| 10 | Max Leverage | 期货杠杆上限；demo 默认 125x，仍受设置 API 的可执行上界约束 | `risk.max_futures_leverage_{live/demo}` | `exceeds_max_leverage:{l}>{cap}` | :181 |
| 11 | Data Freshness | live<120s / demo<1800s | `_MAX_DATA_AGE_SECONDS=120` | `stale_market_data:age={a}` (live path); `stale_market_data_demo:age={a}` (demo path for bitget_demo/okx_demo only, engine.py:238-241) | :188 |
| 12 | Total Account Exposure | 同一账户池的 open Position、未决 entry Order、pre-Order Risk reservation 与新单名义敞口之和不得超过该模式资本基数的配置比例；`paper_internal` 独立、每个 exchange demo mode 独立，live 保持合并池 | `risk.{demo/live}.capital.max_total_exposure_pct` | `max_total_live_exposure_exceeded:{p}>{cap}` / `max_total_demo_exposure_exceeded:{p}>{cap}` | engine.py check #12 |
| 13 | Single Trade Risk | 止损距离×数量<上限；demo 默认 20%，设置 API 只保留正数下限 | `risk.max_{/demo_}single_trade_risk_pct` | `max_single_trade_risk_missing_stop_price` (live, no stop_price, engine.py:207); `exceeds_single_trade_risk:{r}>{max}` (live, over cap); `exceeds_demo_single_trade_risk:{r}>{max}` (demo) | :202 |
| 14 | Scanner-Family Evidence | live 需该单 `scanner_family`(经 opportunity 解析)在干净窗有 ≥N 已结算证据且方向胜率≥门槛(取代已删的策略 FSM 准入,Position C 2026-07-02;fail-closed:无 family/无证据/样本不足/胜率不足均拒) | `risk.live_family_min_samples`(30)/`risk.live_family_min_win_rate_pct`(50.0) + `repo.family_direction_edge` | `family_not_live_eligible:{unknown/family:no_evidence/family:samples=n<N/family:wr=w<T}` | :211 |
| 14b | Exchange-demo entry quality | 仅 `bitget_demo`/`okx_demo`/`binance_testnet` 的**新开仓**：已发布 regime confidence ≤ 门槛（mean_reversion 豁免 confidence）、已发布 regime 为 sideways（**含 mean_reversion**）、family×direction 在干净窗 n≥门槛且 expectancy&lt;0、或同标的 24h 内 `stop_loss` 后反向，则拒。未发布 regime / 样本不足 fail-open；policy epoch、regime、family-edge、recent-stop authority 读取失败则 fail-closed 并保留来源 token。`paper_internal` 与 live #14 不变；不平已有仓 | `risk.demo.eligibility.min_regime_confidence_to_enter`(0.60) / `sideways_standdown`(true) / `family_min_samples`(20) / `family_min_expectancy`(0.0) | `regime_confidence_standdown:*` / `regime_sideways_standdown:*` / `family_not_demo_eligible:*` / `reverse_after_stop:*` / `demo_entry_quality_unavailable:<source>` | `aletheia/risk/demo_entry_quality.py` |
| 15 | Lesson Constraints | 已接受且未过期的 manual/owner/human block 规则保持静态硬约束；自动来源默认 advisory，只有 exact `risk_lesson_constraint` prospective treatment 可阻断该 Proposal，control/default/error 不得阻断 | `LessonStore.recommendation_json` + Learning path authority | `lesson_block_{symbol/...}:{id}:{v}` / `lesson_check_error:internal_failure` | engine.py `_check_lesson_constraints` |
| 16 | Portfolio Correlation | 部门集中度 + 组合 BTC beta；持仓数低于 `correlation.min_positions_for_concentration`(默认 4)时跳过，少于 4 个持仓时浓度百分比粒度不足以区分信号 | `risk.correlation_guard_enabled/max_sector_concentration_pct/max_portfolio_beta/min_positions_for_concentration` | `sector_concentration/portfolio_beta:...` | :245,387 |
| 17 | Account Leverage Ratio | 期货:同一账户池的 Position + 未决 entry Order + pre-Order reservation 的 Σ(notional)/同账户 equity ≤ 上限；不同 demo broker 不混算；外部 equity 必须来自新鲜健康的具名缓存；相关 Position 的 market type/数量/价格/杠杆非法时拒绝，不能当零敞口（spot 的 NULL leverage 按其结构语义计 1x，显式非 1x 反而拒绝）。Demo 默认 10x，设置 API 只保留正数下限；live 保持 1.5x | `risk.max_account_leverage_{live/demo}` + named local cache | `account_leverage_ratio:{r}x>{cap}x` / `account_equity_unavailable:{mode}` / `account_leverage_check_error:invalid_position_exposure:position={id}:field={category}` / `account_leverage_check_error:internal_failure` | engine.py check #17 (RE-B23~28) |
| 18 | Liquidation Buffer | 期货(lev>1):估算爆仓价距入场 ≥ buffer% | `risk.min_liquidation_buffer_pct`(5.0) | `liquidation_too_close:{g}%<{b}%` | :695 (FL-S09) |
| 19 | Per-symbol Cumulative Notional | 同标的同池累计名义(含本单)≤ 上限(CUM-ADD 均价合并会绕过 #5 单仓上限)；上限由 sizing 公式推导：`capital_base × risk_pct × leverage × max_per_symbol_entries`，永不与首单产生矛盾 | `risk.capital.max_per_symbol_entries`(无量纲次数，默认 3) | `exceeds_per_symbol_notional:{sym}:{proj}>{cap}` | :285 |

> ⚠️ 关卡 #11 的 `_MAX_DATA_AGE_SECONDS` 是模块常量,非 config 字段。若要 config 化需迁移 + 测试。
>
> **本地纸面入场不是“有一条新鲜行情就够了”**：generic Proposal 仍重新读取精确 venue+symbol+
> physical-market mark；Entry Plan 则只消费 hash-valid、未过期、exact instrument/generation/channel 的
> execution receipt，并按 Maker/IOC tactic 验证 immutable order constraint，避免两条异步价格权威。
> 报价/receipt 不一致、行情缺失/过期、两张 Opportunity 表发生同 ID
> 冲突，或入场计划缺字段/数学自相矛盾/RR 超出 `[1.00, 10.00]` 合理性包络，都会在 authoritative RiskDecision 中拒绝且保持零
> Order/Fill/Position。generic Proposal 获批后仍走既有 mark+slippage；Entry Plan 则由
> `paper-order-fill/v1` 仅按 receipt depth 或后续 strict trade-through 形成模拟 Fill。spec:
> `PL-RG-07~14`、`PL-EC-01~05`。
>
> ⚠️ 关卡 #5 的回撤缩放(`_portfolio_drawdown_scale`, engine.py:356):缩放系数(0-5% DD→1.0, 5-10%→1.0→0.5, 10-20%→0.5→0.0, >20%→0)由组合回撤驱动。**回撤的数据源**:交易所模式(okx_demo/*_demo/live)读 `portfolio_snapshots` 的 `exchange_account` 行(真实账户 equity),**不读**每个 mode 自己的行——后者的 `total_value_usdt` 是持仓 notional(Σqty×price)而非 equity,平掉盈利持仓会让 notional 暴跌被误判成回撤、把 cap 缩到 $0 锁死开仓。paper_internal 没有可信的模拟账户权益序列：本地快照是持仓名义金额，外部账户快照属于另一账户，因此它跳过回撤缩放并继续受固定上限、日损和持仓数限制。spec: RE-B05(分段)+ RE-B05a(交易所数据源)+ RE-B05b(本地模拟)。

> 关卡 #5 的账户余额收紧:不在下单时请求交易所，只读取本地缓存/账本的可用 USDT。Bitget demo/live 分别读各自模式缓存；OKX/Binance 读统一缓存中与 execution mode 同名的账户；`paper_internal` 读独立纸面现金账本。有效上限始终是 `min(configured_hard_cap, available_usdt)`，因此余额数据只能收紧、不能抬高配置上限。外部账户没有缓存时仍保留配置上限，因为缓存只是额外收紧信息；本地纸面账本本身就是资金权威，读取失败必须以 `paper_account_balance_unavailable` 拒绝，不能假装仍有配置资金。本地纸面账户若存在尚未补齐 funding/关闭成本的已关闭仓位，则返回可用余额 0 并阻止继续扩张。spec: RE-B18、RE-B29、PL-PCM-06。
>
> 关卡 #5 的比较量：比较的是 **pledged cash（保证金）= notional / leverage**，而非原始名义价值。原始名义价值与可用现金相比时，任何杠杆 > `1/risk_pct` 的单子都会被错误拒绝（20% 风险时为 5x）；以保证金比较后，有效杠杆上限由账户余额自然决定，不受 per_trade_risk_pct 的隐式 cap。spec: RE-B05c、RE-B05d。

> **Spot 杠杆身份**：原生 spot Proposal 的 leverage 只能为 `NULL` 或显式 `1x`；其他值在 Risk admission 以 `invalid_spot_leverage` 稳定拒绝，paper proposal helper 也必须在持久化前拒绝。历史矛盾 spot Position 即使带有非 1x leverage，本地 paper 账本仍按完整 spot 名义占用现金，不能因坏数据释放购买力。期货的合法杠杆与保证金计算不变。spec: RE-B32。

> **本地合约资金费前置门**：`paper_internal + usdt_futures` 只有在配置的 exact physical venue、
> canonical symbol、perp 上存在 source-time provided、近期采集且下一结算仍在未来的 schedule 时才可
> 开仓；跨 venue、aggregate/predicted、stale 或 malformed 行均不可满足。通过只代表本地模拟器已有
> 起始锚点；平仓仍逐事件验证完整时间窗，任何断档都关闭仓位但保持净收益不可用。spec: RE-B22、PL-LF-01~06。

> **账户杠杆身份**：check #17 使用同一个账户池判断 open Position、未决 entry Order 和 pre-Order reservation；`paper_internal` 独立、每个 demo execution mode 独立，live 保持合并池。分母只读同一具名账户的新鲜健康本地 equity，过期、异常或具名账户缺失时拒绝，不以 starting capital 代替；相关 Position 的 market_type/quantity/entry_price/current_price/leverage 只要缺失、非正数、非有限数或自相矛盾，也会稳定拒绝而不是从分子消失。拒绝 token 携带 sanitized Position id 与稳定 field category，避免一条坏仓位只产生无法定位的泛化重复告警。唯一结构化例外是 spot Position：spot 本身无杠杆，历史/当前模型以 `leverage=NULL` 表示 1x，因此按 1x 计入；若 spot 显式写入非 1x，则视为数据矛盾并拒绝。合约或未知 market_type 的 NULL leverage 仍拒绝。spec: RE-B23~28。

> **公共杠杆监控不得伪装安全**：`check_account_leverage()` 与 `/api/risk/account-leverage` 使用和 check #17 相同的账户池、Position 数值完整性与未决占用口径；除 open Position 外，还计入尚未形成 Position 的 unresolved entry Order 和 pre-Order Risk reservation。完整数据返回 `status=available`、`reason=null` 和布尔 `violation`；相关 Position/未决占用损坏、读取失败或具名 equity 不可用时返回 `status=unavailable`，同时 `ratio=null`、`violation=null` 并给出稳定 `reason`。当损坏来自 Position 时另返回 `invalid_position={position_id, field_category}`；不得包含账户凭证或原始交易所 payload。不能把未知结果表达成 `violation=false`，System Operator 上下文也只展示空比率。spot 的 `leverage=NULL` 仍是明确的 1x 例外。spec: RE-B28。

> **未决原生入场也占用 Risk 容量**：交易所入场 Order 在真正提交前会先以 `pending` 写入本地；
> 此时交易所是否已经接受仍未知，不能把它当成零敞口。`pending` 与其他未决状态一样，在对应
> live/demo 池中继续占用持仓槽位、保留同符号互斥，并把尚未由 open Position 表示的数量计入
> 名义敞口。若未决 Order 已有部分数量投影为 Position，只补计剩余数量，且不重复占一个槽位；
> 平仓 Order 不占新增入场容量。Demo 总敞口使用与资本基数相同的精确账户池，不把
> `paper_internal` 和 exchange demo 账户混算。spec: `PL-UEE-01~17`。

> **关卡 #8 的权威损失源**：Risk 不再读取 `today_realized_pnl` 或任何策略表现聚合。
> `paper_internal` 的 verified close 必须与 append-only `paper_account_events` 一一对应且金额一致；
> exchange-backed mode 只接受当前 outcome semantics 下经济字段完整的 broker-fill close。当天出现
> administrative、maintenance、legacy、费用/身份不完整或 paper ledger 不一致的 close 时，snapshot
> 返回 unavailable，Risk 以 `account_loss_unavailable:*` 拒绝新 proposal，但不会伪造
> `max_daily_loss_exceeded` 或自动触发 kill switch。无 closed account event 的完整空集为已知 0。
> spec: RE-B20 / OI-C03。

> 延迟 funding 恢复必须通过统一原子物化入口同时收敛 `positions.realized_pnl`、唯一 full
> `position_close_events.realized_pnl` 和 paper cash。Owner-approved numeric correction 不改写旧 cash
> event，而追加带 causal evidence 的 `manual_credit`/`manual_debit` 差额；authoritative loss 按该
> Position 的 settlement 加补偿净额核对。任一投影不一致继续返回 `paper_ledger_mismatch`，不得在
> 尚未收敛的中间状态释放新 Risk 容量。

> **唯一硬门 & guard 澄清(2026-06-30)**:订单通路(`submit_proposal`→执行)上,**`evaluate_proposal()` 是唯一硬门**。另有两套也叫 "guard" 的东西**不在执行通路上裁决**:
> (1) 历史 `trading_decision` 守卫链曾只写 `decision_meta['guards']`、提供 advisory；该旧入场消费者已删除，不是当前执行入口；
> (2) ~~`trust/guard_chain.py:run_guards()`~~ → **已于 2026-09-07 删除**。它从未被任何生产代码调用，因此并不观测任何东西，且其自带的 `max_daily_loss_pct` 默认 `5.0` 与 schema 的 `1.0` 矛盾。spec `EX-C03` 保留并继续生效：它扫的是 `gateway.py`，守的是「唯一硬门是 `risk/engine.py:evaluate_proposal`」这个不变式，而不是该文件的存在。
> 区分：knowledge `trading_guards` 曾在旧 Ranker 排序阶段剔除候选；该消费者现已退役（见 [knowledge_contract §4.5](../contracts/knowledge_contract.md)），历史 guard 命中不授予当前 Entry Plan 执行权，**不是**执行硬门。若未来要把某条 guard 升为硬拦,必须改本契约 + 接线 gateway + 改 EX-C03,不可静默接线。

---

## 3. 返回结构

```python
@dataclass  # engine.py:54-62, 镜像 storage/models.py:125
class RiskCheckResult:
    approved: bool = False
    risk_score: float = 0.0          # len(violations)*0.2, cap 1.0
    violations: list[str]            # 所有失败标签(见 §2)
    allowed_execution_mode: str = "paper_internal"  # approved=原模式; rejected=降级 paper_internal
    message: str = ""
    decision_id: str | None          # 持久化的 RiskDecision.id
```


## Former execution definitions

**paper TP 不得事后发明**：paper broker 创建新 Position 时只能原样写入 Risk 已检查的
`TradeProposal.take_profit`。Proposal 中为 `NULL`，Position 的 `take_profit_price` 也必须为 `NULL`；
broker 不得在批准后读取可变 config 补一个 TP。§10.4 的 position-manager 对原生 Proposal 同样只消费
持久化的批准值；Proposal 明确为 `NULL` 时，不得再用当前 config 生成新的平仓触发器。



| `paper_internal` | 否(本地模拟) | paper_broker | `paper_broker.py:17` |


### 3.0 `paper_internal` 入场真实性

- `trading.execution_fee_pct` 只接受有限的 `0..5` 百分点，`trading.slippage_bps`
  只接受有限的 `0..200` bps。canonical config load、Settings mutation 与 Paper
  broker 使用同一范围；非法 restored/manual config 必须在 Order、Fill、Position、
  cash event 或 ExitPlan 写入前失败，不能生成 favorable slippage、负 fee 或乐观零成本。
- 本地纸面入场通常只允许 `order_type='market'`。Proposal builder、paper tool 和 broker 三层都拒绝
  普通 `limit`，不得把“按报价立即模拟成交”伪装成有挂单/等待成交语义的限价单。唯一例外是
  `§3.0a` 的持久 Entry Plan → Proposal → Risk 权威链所拥有的 bounded Limit Order Intent；
  `paper-order-fill/v1` 保留 requested tactic：IOC 只消费同一 execution receipt 中的已知 opposing
  depth 并取消未知 remainder；Post-Only 先写 durable open Order，placement/touch 不成交，只在后续
  strict opposite-aggressor trade-through 时按该真实事件剩余 size 分配。模拟 Fill 的
  `realized_liquidity` 保持 unavailable，并单独持久化 simulated provenance。
- 入场价只来自配置行情 venue 的同 canonical symbol、同物理 `spot/perp` 新鲜精确 mark；内存
  mark 必须小于 90 秒，SQLite 精确回退不得超过 120 秒。跨 venue、跨 spot/perp、symbol-only
  或 stale row 都不能建立 Proposal，pending 留待后续重试。
- 每个本地 Position 只属于一个 immutable Proposal。相同 symbol/direction/strategy 的另一 Proposal
  不得在 broker 内并入已有 Position；正常 Gateway/Risk 路径会先拒绝重复暴露，broker 的独立
  lineage 是最后一道防御，避免费用、保护价和归因跨 Proposal 混合。



- `paper_internal` 新 Order 必须复用 exact active `universe_registry` 的 venue/symbol/physical-market、
  precision 与 min-notional 事实。缺失或不可表示时 fail closed，不增加第二套 rules store。


- open/partial Paper Order 在重启后保持 unresolved authority；旧 generation 不补造 downtime Fill。
  exact quote/trade readiness 建立新 matching generation 后才恢复消费新事件，并留下 monitoring-gap
  事实。一个真实 Trade event 在全部本地 eligible Orders 间只可消费一次，按价格后时间/id 排序。
- Central outcome eligibility 从 entry Fill 读取 `paper_fill_model_version`，只接纳
  `paper-order-fill/v1` 的 `paper_internal` Position。旧 instant-fill 或缺失 identity 的记录保留为
  raw event，并以 `unverified_paper_fill_model` 排除，不能与新模型汇总成同一经济 cohort。



- 租约过期后，只有 `paper_internal` 且尚无 Order 时可以用 owner token + 原过期时间做 compare-and-set 抢回执行权。paper 的 owner 校验与 Order/Fill/Position 物化同处一个 `BEGIN IMMEDIATE` 事务，因此旧 owner 与恢复 owner 不可能各写一套成交。


- `paper_internal` 已有 Order 但物化不完整时转为 `materialization_missing` 并等待修复，不重新创建订单。


`paper_internal` 是纯本地模拟，没有交易所对应物，始终返回 `sync_status=local_only`，任何 unified/legacy
外部缓存都不得把它分类为 `orphaned`。可执行检查：`PL-ADOPT-22`。


- 交易所腿不可跳过:demo 模式仍走真实交易所 close;`paper_internal` 本地撮合。


- **保护性平仓价格不可伪造**：触发判断和 `paper_internal` close Fill 只使用 Position 精确 `exchange + symbol + market_type` 的新鲜 mark。单仓路由即使收到 `fallback_exit_price`，本地分支也必须忽略它并在 manager 内重新取价。交易所仓由 `execution_mode` 固定 venue（OKX / Bitget / Binance），只有 `paper_internal` 使用配置的 market-data venue。内存 exact-instrument cache 必须小于 90 秒；SQLite 只能回退到同 venue、同 `spot` / `perp`（兼容旧 `usdt_futures` 拼写）且不超过 120 秒的 snapshot。不得用 `Position.current_price`、symbol-only、跨 venue、跨 spot/perp 或过期价格触发/生成退出成交。


- **dashboard 批量平仓先完整预检**：`close_local_positions_batch()` 先无上限枚举目标 `paper_internal` 仓位，并在 manager 内为每个仓位取得上述 exact mark；任一 mark 缺失或读取失败，在第一笔 close 前整体拒绝。预检全部通过后才逐仓物化；若之后出现非行情执行错误，响应必须同时列出 `closed_position_ids` 与逐仓 `failed`，不得把部分成功显示成 close-all 成功。


历史缺陷(已修 2026-06-30):`scheduler.py:_run_position_manager` 曾 `if cfg.runtime.paused: return`,导致暂停时止损循环停摆(仅 paper/demo;`paper_internal` 无交易所原生 TPSL 兜底,真失保护)。修复=移除 paused 门 + `PL-PM-07` 回归锁。


### 10.6 Protective Barrier（schema v9；仅新建 paper_internal）

Schema v9 提供保护屏障持久化契约；字段、身份、CAS 与 retention
不变量由 [data_model §11](../data/data_model.md#11-protective-barrier-contract-schema-v9-storage-new-paper-runtime-active)
统一定义。

具有已解析保护价位的新建 `paper_internal` Position 在原入场事务内冻结初始 ExitPlan、exact
venue/symbol/physical-market 与首个非回溯 1m cursor；没有保护价位的既有合法 Paper 入场不伪造
ExitPlan 或 cursor。protective executor 每 30 秒逐根读取已闭合的 exact
1m bar；若 durable cursor 所需 bar 尚未落库，同一 worker 会以该 cursor 作为 `since`
从冻结 venue 增量刷新，绕过通用 OHLCV TTL cache，并在有界预算内按时间顺序追平积压。
缺失数据不 fallback，同 bar 同触 stop/target 时固定 stop-first。bar 只授权触发，
成交仍通过既有 `close_position_routed` 重新取得 fresh mark 并应用 adverse slippage，随后把
真实 Order/Fill write-once 链回触发证据。迁移和重启不回填旧 Position；Risk、外部 Execution、
Live 权限与配置默认值均保持不变。



## Former daily cash-ledger membership

For paper_internal daily membership, the linked Position.closed_at defines the
UTC accounting day, not the later cash-event append time. A delayed settlement
for an older valid close is excluded from today only when its amount matches the
Position realized_pnl; a newly appended event without valid matching economics
remains an unavailable ledger mismatch.

## Former runtime exemptions

The retired runtime treated a missing or `paper_internal` account as a complete
exchange view without a broker. Fast streams could use the configured public-data
venue independently of the execution account, and public funding collection added
symbols from open simulated futures positions. Interrupted local execution could
release pre-Order authority after its claim expired. These exemptions are removed;
current stream/account selection requires a registered external account, and
uncertain execution remains owned by Gateway reconciliation.

## Retired executable Risk claims

`RE-B22`, `RE-B05b`, `RE-B29`, `RE-B30`, and `RE-C02` described simulated
funding anchors, local drawdown exemptions, cash-reader authority, delayed local
settlement and the deleted `paper_tools` mode set. These requirements are retired.
`RE-B32` retains native spot leverage validation without the local reservation
calculator. `RE-B18` retains external Demo/Live balance isolation and the existing
advisory-cache fallback. Current daily-loss and percentage-denominator checks use
external account identities; they do not require a simulated cash ledger.

## Historical Momentum geometry v2 controlled Paper boundary

The following records the former controlled experiment, not a current Planner
selector or fallback. Current Grounded profiles and fingerprints above own entry.

The planner continues to request `momentum-retrace-reclaim/v1`. For an exact
Momentum Candidate with 21 completed causal bars, the existing Learning allocator
may assign the same PlanIntent to `momentum-retrace-reclaim/v2`. Control remains
v1. Missing or invalid geometry records an unavailable denominator unit and keeps
the v1 behavior.

A v2 Entry Plan persists its immutable treatment-exposure receipt, exact geometry,
and source fingerprints. Execution fails closed unless that receipt proves an
applied `momentum_entry_geometry` treatment. Only `paper_internal` and
`bitget_demo` are eligible; Live remains unavailable. The existing sizing formula,
Risk policy, Gateway, quantity-at-trigger calculation, and protective exit path are
unchanged. Fast Monitoring uses the same v2 state machine and forwards a trigger
only after this authority check.

Former simulated tactic clause (retired): `paper_internal` 只以 `paper-order-fill/v1` 模拟：IOC 仅消费 receipt 中已知 opposing depth，Maker 先落 durable open Order，之后仅由同 instrument 的 later strict opposite-aggressor trade-through 按真实事件 size 有界成交；不宣称外部 queue position。
