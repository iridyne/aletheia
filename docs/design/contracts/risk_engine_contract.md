# Risk Engine 决策契约 (L2 金标准)

> **角色**:定义 Risk Engine 作为"agent 不可绕过的硬墙"的完整决策契约。
> 这是 trust 边界的核心——[VISION](../../product/definition/VISION.md) 把它定义为"给 AI 买到的犯错许可证"。
> **基线**:以代码现状为底(file:line 可核),意图差距标 ⚠️;冲突时文档为准,偏差落 [issues](../../issues/domain/BACKEND_ISSUES.md)。
> **不变式来源**:[trust_and_change_control §6.3](../../governance/policies/trust_and_change_control.md)。
> Last updated: 2026-07-14 · 维护者:Aletheia Dev

---

## 1. 入口与序列化

| 项 | 值 | 位置 |
|----|----|----|
| 主入口 | `evaluate_proposal(proposal: TradeProposal) -> RiskCheckResult` | `aletheia/risk/engine.py:67` |
| 并发保护 | 全局线程锁 `_eval_lock`,防 kill-switch TOCTOU | `engine.py:30, 70-71` |

**契约**:所有 proposal 必须且仅能通过 `evaluate_proposal` 取得放行;同一时刻只有一个 proposal 在评估(序列化)。

**唯一入口不变式(RE-B17)**:执行网关 `_execute_proposal` 中每一条调用 broker 下单函数(`execute_paper_order` / `execute_live_order`)的代码路径,必须在 `risk_result.approved` 检查通过后才能到达——即 risk evaluation 必须前置且其结果被显式消费。不存在绕过 `evaluate_proposal` 直接触达 broker 的路径。可执行测试:spec `RE-B17`。

### 1.1 Demo / Live policy identity（v2）

Risk Engine 仍只有一个入口和一套检查实现，但参数由 Proposal 已验证的
`execution_mode` 显式解析到 `risk.demo` 或 `risk.live`。未知 mode 必须
`unregistered_risk_execution_mode` fail closed，不能按 Demo 默认处理。

每条 RiskDecision 保存 `risk_mode/policy_id/policy_revision/policy_hash`；
authoritative 幂等 identity 包含 policy hash，因此 policy 修改后必须重新评估，
不得复用旧批准。Demo/Live policy 可由人类比较、复制或整体替换，但
`require_risk_check_for_all_orders`、live unlock、permission、credentials 和
运行时 brake 不属于可复制 policy。

---

## 2. 检查关卡(代码实际 20 个主关卡，另含本地纸面入场子关与 6b)

> ⚠️ **偏差**:[SYSTEM_ARCHITECTURE_GUIDE](../architecture/SYSTEM_ARCHITECTURE_GUIDE.md) 第五章称"五关审批",代码实际是 20 关(0–16 + 6b/17/18/19)。
> 以下 20 个主关卡及表内子关为金标准;ARCH_GUIDE 的"五关"是简化叙事,待该文档批次3标注。
> 历史:本表曾止于 16 关;6b(跨 broker/池同标的去重)、17(账户杠杆比)、18(爆仓缓冲)、19(单标的累计名义)为后续追加,**2026-06-30 补齐契约**(代码↔spec 早已存在,FL-S08/S09 守 17/18;契约滞后已修)。
> **四个新增关卡的失败 token(spec RE-S04b 锁定,floor ≥ 20)**:
> - 6b → `symbol_already_open:{sym}`
> - 17 → `account_leverage_ratio:{r}x>{cap}x` / `account_leverage_check_error:internal_failure`
> - 18 → `liquidation_too_close:{g}%<{b}%`
> - 19 → `exceeds_per_symbol_notional:{sym}:{proj}>{cap}`

| # | 关卡 | 判断 | config 字段 | 失败标签 | 位置 |
|---|------|------|------------|---------|------|
| 0 | Trading Brakes | global brake 阻断全部；demo/live mode brake 只阻断对应 pool | `runtime.kill_switch_active/demo_brake_active/live_brake_active` | `kill_switch_active` / `demo_brake_active` / `live_brake_active` | engine.py |
| 1 | Symbol Filter | white/blacklist | `risk.symbol_filter_mode/symbol_list` | `symbol_blocked/not_allowed:{sym}` | :83 |
| 1a | Local Paper Market Authority | authoritative `native + paper_internal` 只接受主数据 venue、canonical symbol、physical market 完全匹配的新鲜 mark（内存≤90s，SQLite≤120s）；Proposal.price 必须以极窄 `math.isclose` 容差等于该 mark，broker 只在批准后施加滑点；本地 limit 不受支持 | `market_data.ccxt_exchange` | `paper_market_data_unavailable:{venue}:{market}` / `proposal_price_not_current_mark` / `paper_internal_limit_not_supported` | engine.py local-paper authority block |
| 1b | Local Paper Causal Identity | authoritative `native + paper_internal` 必须在 `opportunities` 与 `serenity_opportunities` 中恰有一个 durable carrier，且 canonical symbol、physical market、direction 完全一致 | —（始终启用） | `proposal_opportunity_required/not_found/ambiguous/symbol_mismatch/market_mismatch/direction_mismatch/check_error` | engine.py `_paper_opportunity_identity_violations` |
| 2 | Market Allowed | spot/futures 开关 | `exchange.enabled_markets` | `spot_market_disabled`（market_type=='spot' 且 enabled_markets['spot']==False）/ `futures_market_disabled`（market_type=='usdt_futures' 且 enabled_markets['usdt_futures']==False） | :93 |
| 2a | Local Futures Funding Ready | `paper_internal` 合约开仓前须有该 symbol 15 分钟内的主 venue 下一结算快照 | `funding_rate_snapshots` | `local_paper_futures_funding_unavailable` | engine.py check #2a |
| 2b | Complete Entry Plan | 每个 native 本地纸面 market 入场及 native exchange limit 入场必须在 Risk 前固定有限正数的 entry/stop/take-profit/risk_reward；多空几何正确，risk_reward 必须等于 `round(abs(tp-entry)/abs(entry-stop), 2)`。exchange market close/reduce 与 adopted chain 豁免 | —（Proposal immutable fields） | `incomplete_entry_plan:{fields}` / `entry_plan_geometry:*` / `entry_plan_risk_reward_mismatch` | engine.py check #2b/#2c |
| 3 | Live State | 非 `live_disabled` | `live.state` | `live_disabled` | :99 |
| 4 | Permission Mode | Observe 仅 paper_internal | `agent.permission_mode` | `permission_mode_insufficient:{mode}` | :104 |
| 5 | Max Position Notional | live/demo 配置上限与对应账户的本地可用 USDT 取较小值，再应用回撤缩放；`paper_internal` 必须读取本地纸面账户账本，读取失败不可退回配置资金。Demo 默认 100,000 USDT，设置 API 只保留正数下限、不施加应用层最大值；live 边界不变 | `risk.max_{live/demo}_position_notional_usdt` + local exchange-account cache / paper-account ledger | `exceeds_max_position_notional:{n}>{cap}` (live path); `exceeds_demo_position_notional:{n}>{cap}` (demo path); `paper_account_balance_unavailable` | :109 |
| 6 | Max Open Positions | live/demo 持仓数；demo 默认 200，设置 API 只保留正整数下限 | `risk.max_open_positions_{live/demo}` | `max_{live/demo}_positions_reached:{n}` | :127 |
| 6b | Cross-pool Symbol Dedup | 同 live/demo 池内同标的禁止重复开仓(防跨 broker 重复) | —(始终启用) | `symbol_already_open:{sym}` | :141 |
| 7 | Short Allowed | 方向合法 | `risk.allow_short_{live/demo}` | `short_not_allowed_{live/demo}` | :138 |
| 8 | Max Daily Loss | 只读 authoritative account snapshot；来源不可用→拒绝但不伪造 breach；真实日损突破→**自动触发 kill switch** | `risk.max_daily_loss_pct`, `kill_switch_enabled_{live/demo}` | `account_loss_unavailable:{reason}` / `max_daily_loss_exceeded:{r}<=-{lim}` | engine.py check #8 |
| 9a | Exchange Allowlist | 交易所匹配(self-contained 豁免) | `risk.allowed_exchange` | `exchange_not_allowed:{a}!={e}` | :162 |
| 9b | Account Mode Match | live↔mode=live / demo↔mode=demo | `exchange.mode` | `account_mode_mismatch:...` | :170 |
| 10 | Max Leverage | 期货杠杆上限；demo 默认 125x，仍受设置 API 的可执行上界约束 | `risk.max_futures_leverage_{live/demo}` | `exceeds_max_leverage:{l}>{cap}` | :181 |
| 11 | Data Freshness | live<120s / demo<1800s | `_MAX_DATA_AGE_SECONDS=120` | `stale_market_data:age={a}` (live path); `stale_market_data_demo:age={a}` (demo path for bitget_demo/okx_demo only, engine.py:238-241) | :188 |
| 12 | Total Live Exposure | 敞口+新单<上限 | `risk.max_total_live_exposure_pct` | `max_total_live_exposure_exceeded:{p}>{cap}` | :194 |
| 13 | Single Trade Risk | 止损距离×数量<上限；demo 默认 20%，设置 API 只保留正数下限 | `risk.max_{/demo_}single_trade_risk_pct` | `max_single_trade_risk_missing_stop_price` (live, no stop_price, engine.py:207); `exceeds_single_trade_risk:{r}>{max}` (live, over cap); `exceeds_demo_single_trade_risk:{r}>{max}` (demo) | :202 |
| 14 | Scanner-Family Evidence | live 需该单 `scanner_family`(经 opportunity 解析)在干净窗有 ≥N 已结算证据且方向胜率≥门槛(取代已删的策略 FSM 准入,Position C 2026-07-02;fail-closed:无 family/无证据/样本不足/胜率不足均拒) | `risk.live_family_min_samples`(30)/`risk.live_family_min_win_rate_pct`(50.0) + `repo.family_direction_edge` | `family_not_live_eligible:{unknown/family:no_evidence/family:samples=n<N/family:wr=w<T}` | :211 |
| 15 | Lesson Constraints | 已接受且未过期的 manual/owner/human block 规则保持静态硬约束；自动来源默认 advisory，只有 exact `risk_lesson_constraint` prospective treatment 可阻断该 Proposal，control/default/error 不得阻断 | `LessonStore.recommendation_json` + Learning path authority | `lesson_block_{symbol/...}:{id}:{v}` / `lesson_check_error:internal_failure` | engine.py `_check_lesson_constraints` |
| 16 | Portfolio Correlation | 部门集中度 + 组合 BTC beta | `risk.correlation_guard_enabled/max_sector_concentration_pct/max_portfolio_beta` | `sector_concentration/portfolio_beta:...` | :245,387 |
| 17 | Account Leverage Ratio | 期货:同一账户池的 Position + 未决 entry Order + pre-Order reservation 的 Σ(notional×lev)/同账户 equity ≤ 上限；不同 demo broker 不混算；外部 equity 必须来自新鲜健康的具名缓存；相关 Position 的数量/入场价/杠杆非法时拒绝，不能当零敞口（spot 的 NULL leverage 按其结构语义计 1x，显式非 1x 反而拒绝）。Demo 默认 10x，设置 API 只保留正数下限；live 保持 1.5x | `risk.max_account_leverage_{live/demo}` + named local cache | `account_leverage_ratio:{r}x>{cap}x` / `account_equity_unavailable:{mode}` / `account_leverage_check_error:{invalid_position_exposure/internal_failure}` | engine.py check #17 (RE-B23~27) |
| 18 | Liquidation Buffer | 期货(lev>1):估算爆仓价距入场 ≥ buffer% | `risk.min_liquidation_buffer_pct`(5.0) | `liquidation_too_close:{g}%<{b}%` | :695 (FL-S09) |
| 19 | Per-symbol Cumulative Notional | 同标的同池累计名义(含本单)≤ 上限(CUM-ADD 均价合并会绕过 #5 单仓上限) | `risk.max_per_symbol_notional_usdt` | `exceeds_per_symbol_notional:{sym}:{proj}>{cap}` | :285 |

> ⚠️ 关卡 #11 的 `_MAX_DATA_AGE_SECONDS` 是模块常量,非 config 字段。若要 config 化需迁移 + 测试。
>
> **本地纸面入场不是“有一条新鲜行情就够了”**：Risk 会重新读取精确 venue+symbol+physical-market
> mark，并核对 hash-covered Proposal.price。报价不一致、行情缺失/过期、两张 Opportunity 表发生同 ID
> 冲突，或入场计划缺字段/数学自相矛盾，都会在 authoritative RiskDecision 中拒绝且保持零
> Order/Fill/Position。合法 Proposal 以 mark 获批后，paper broker 才按配置施加不利滑点。spec:
> `PL-RG-07~13`、`PL-EC-01~05`。
>
> ⚠️ 关卡 #5 的回撤缩放(`_portfolio_drawdown_scale`, engine.py:356):缩放系数(0-5% DD→1.0, 5-10%→1.0→0.5, 10-20%→0.5→0.0, >20%→0)由组合回撤驱动。**回撤的数据源**:交易所模式(okx_demo/*_demo/live)读 `portfolio_snapshots` 的 `exchange_account` 行(真实账户 equity),**不读**每个 mode 自己的行——后者的 `total_value_usdt` 是持仓 notional(Σqty×price)而非 equity,平掉盈利持仓会让 notional 暴跌被误判成回撤、把 cap 缩到 $0 锁死开仓。paper_internal 没有可信的模拟账户权益序列：本地快照是持仓名义金额，外部账户快照属于另一账户，因此它跳过回撤缩放并继续受固定上限、日损和持仓数限制。spec: RE-B05(分段)+ RE-B05a(交易所数据源)+ RE-B05b(本地模拟)。

> 关卡 #5 的账户余额收紧:不在下单时请求交易所，只读取本地缓存/账本的可用 USDT。Bitget demo/live 分别读各自模式缓存；OKX/Binance 读统一缓存中与 execution mode 同名的账户；`paper_internal` 读独立纸面现金账本。有效上限始终是 `min(configured_hard_cap, available_usdt)`，因此余额数据只能收紧、不能抬高配置上限。外部账户没有缓存时仍保留配置上限，因为缓存只是额外收紧信息；本地纸面账本本身就是资金权威，读取失败必须以 `paper_account_balance_unavailable` 拒绝，不能假装仍有配置资金。本地纸面账户若存在尚未补齐 funding/关闭成本的已关闭仓位，则返回可用余额 0 并阻止继续扩张。spec: RE-B18、RE-B29、PL-PCM-06。

> **本地合约资金费前置门**：`paper_internal + usdt_futures` 只有在该 symbol 存在新鲜、非预测跨 venue 的下一结算快照时才可开仓。通过只代表本地模拟器已有起始锚点；平仓仍逐事件验证完整时间窗，任何断档都关闭仓位但保持净收益不可用。spec: RE-B22、PL-LF-01~06。

> **账户杠杆身份**：check #17 使用同一个账户池判断 open Position、未决 entry Order 和 pre-Order reservation；`paper_internal` 独立、每个 demo execution mode 独立，live 保持合并池。分母只读同一具名账户的新鲜健康本地 equity，过期、异常或具名账户缺失时拒绝，不以 starting capital 代替；相关 Position 的 quantity/entry_price/leverage 只要缺失、非正数或非有限数，也会稳定拒绝而不是从分子消失。唯一结构化例外是 spot Position：spot 本身无杠杆，历史/当前模型以 `leverage=NULL` 表示 1x，因此按 1x 计入；若 spot 显式写入非 1x，则视为数据矛盾并拒绝。合约或未知 market_type 的 NULL leverage 仍拒绝。spec: RE-B23~27。

> **公共杠杆监控不得伪装安全**：`check_account_leverage()` 与 `/api/risk/account-leverage` 使用和 check #17 相同的账户池、Position 数值完整性与未决占用口径；除 open Position 外，还计入尚未形成 Position 的 unresolved entry Order 和 pre-Order Risk reservation。完整数据返回 `status=available`、`reason=null` 和布尔 `violation`；相关 Position/未决占用损坏、读取失败或具名 equity 不可用时返回 `status=unavailable`，同时 `ratio=null`、`violation=null` 并给出稳定 `reason`。不能把未知结果表达成 `violation=false`，System Operator 上下文也只展示空比率。spot 的 `leverage=NULL` 仍是明确的 1x 例外。spec: RE-B28。

> **未决原生入场也占用 Risk 容量**：交易所入场 Order 在真正提交前会先以 `pending` 写入本地；
> 此时交易所是否已经接受仍未知，不能把它当成零敞口。`pending` 与其他未决状态一样，在对应
> live/demo 池中继续占用持仓槽位、保留同符号互斥，并把尚未由 open Position 表示的数量计入
> 名义敞口。若未决 Order 已有部分数量投影为 Position，只补计剩余数量，且不重复占一个槽位；
> 平仓 Order 不占新增入场容量。spec: `PL-UEE-01~09`。

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
> (1) `trading_decision` 守卫链 → 仅写 `decision_meta['guards']`,advisory(spec PI-S02 守);
> (2) `trust/guard_chain.py:run_guards()` → **gateway 从不 import/调用**(spec `EX-C03` 锁),其 `block` 裁决无执行力,属观测性基础设施。
> 区分:knowledge `trading_guards` 的 `blocking` 模式在**排序阶段**剔除候选(`paper_trader_ranker.py:412`,见 [knowledge_contract §4.5](knowledge_contract.md))——那是开仓前的 advisory-to-ranking,**不是**执行硬门。若未来要把某条 guard 升为硬拦,必须改本契约 + 接线 gateway + 改 EX-C03,不可静默接线。

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

**契约**:每次评估**必须**持久化一条 `RiskDecision`(`engine.py:267-277`),无论批准或拒绝——这是因果链 `proposal→risk_decision→order` 的一环([trust §1.1](../../governance/policies/trust_and_change_control.md))。

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
| 恢复 | 仅 `deactivate(actor="human")`,**actor 硬编码 human,agent 无路径** | `kill_switch.py:39` |
| 审计 | 激活/恢复均写 JSONL | `kill_switch.py:27,45` |

**不变式**:kill switch 是 §2 第 0 关,激活后任何 proposal 必被拒;恢复是不可被 agent 触达的人工操作。历史事故教训：daily-loss kill switch 的 UTC 日期边界不可按本地日期推断。

---

## 5. 仓位计算(两条路径,诚实标注)

> ⚠️ 当前有**两条并存的 sizing 路径**,这是 [execution_path_honesty §1.1](../../governance/policies/execution_path_honesty.md) 揭示的事实:

| 路径 | 公式 | 位置 |
|------|------|------|
| paper_trader(规则路径) | 固定 `notional=$25 / price` | `paper_trader.py:124` |
| System Operator(conviction 路径) | `compute_position_size(conviction, base, kelly, dd)` — conviction 0-100 映射 [0.3,2.5]× × quarter-Kelly × 回撤缩放 | `trading/sizing.py:19-64`, 调用于 `trading_decision.py:154` |

config:`trading.default_notional_usdt=25`, `vol_kelly_cap_fraction=0.25`, `vol_sizing_enabled=True`(`schema.py:329`)。

**关键契约**:sizing 计算结果**不是**最终下单量——仍须过 §2 第 5 关(Max Position Notional)裁剪。sizing 决定"想要多少",Risk Engine 决定"允许多少"。

---

## 6. 硬限不可绕过的保证

1. **入口白名单**:`create_paper_proposal()` 硬编码 `_PAPER_MODES`,非 paper 模式抛 ValueError(`paper_tools.py:11-35`)——agent 无法构造 live proposal。
2. **序列化锁**:`_eval_lock` 防 TOCTOU(§1)。
3. **纵深防御**:Risk Engine 放行后,Execution Gateway 再查一次 live/account mode(`gateway.py:201`)。
4. **持久化**:kill switch 落盘,重启保持。
5. **审计**:所有决策入 DB + JSONL。
6. **快照绑定**:authoritative Risk 只允许交易所数量归一化；其他 hash-covered Proposal 意图不得替换。

**[trust §6.3](../../governance/policies/trust_and_change_control.md) 红线**:不得在未经显式用户批准下削弱或移除任何 risk check;新增 check 必须持久化 RiskDecision 且对 API/UI 可见;测试须覆盖 approved + rejected 两条路径。

---

## 7. 已知缺陷

§7 已知缺陷: 截至 2026-06-23, 无已知缺陷待修。

---

## 8. 目标态差距(意图领先代码)

- [execution_path_honesty §1.2](../../governance/policies/execution_path_honesty.md):LLM 的 `direction_bias` 直接驱动下单方向,Risk Engine **不验证方向是否合理**——它只管账户/订单安全,不管"这个方向对不对"。这是有意的职责边界(risk 不依赖市场状态判断),但意味着"方向正确性"无硬约束。
- ⚠️ 不要把 market-state guard(如资金费率熔断)塞进 Risk Engine——见 [north_star §10.6](../../references/external-systems/north_star.md),那属策略层。

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
边界的变化，必须先建立 Proposal；它们不是本 Contract 的未来待办。

### evaluate_proposal 唯一入口不变式

**所有 TradeProposal**（无论来源是主赛道 PI 还是未来 serenity_executor）到达 `gateway.submit_proposal` 之前，**必须经过 `evaluate_proposal()`**。

- `gateway._execute_proposal` 是唯一通向 broker 下单的函数
- `evaluate_proposal` 在 `_execute_proposal` 内调用，`risk_result.approved` 守卫在所有 broker 调用之前
- 不存在绕过 risk engine 直接下单的代码路径

spec：`D2-C01`（L2c，spec_risk_engine.py）持续锁住此不变式。
For paper_internal daily membership, the linked Position.closed_at defines the
UTC accounting day, not the later cash-event append time. A delayed settlement
for an older valid close is excluded from today only when its amount matches the
Position realized_pnl; a newly appended event without valid matching economics
remains an unavailable ledger mismatch.
