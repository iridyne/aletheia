# Risk Engine 决策契约 (L2 金标准)

> **角色**:定义 Risk Engine 作为"agent 不可绕过的硬墙"的完整决策契约。
> 这是 trust 边界的核心——[VISION](../product/VISION.md) 把它定义为"给 AI 买到的犯错许可证"。
> **基线**:以代码现状为底(file:line 可核),意图差距标 ⚠️;冲突时文档为准,偏差落 [issues](../issues/BACKEND_ISSUES.md)。
> **不变式来源**:[trust_and_change_control §6.3](../governance/trust_and_change_control.md)。
> Last updated: 2026-06-21 · 维护者:Aletheia Dev

---

## 1. 入口与序列化

| 项 | 值 | 位置 |
|----|----|----|
| 主入口 | `evaluate_proposal(proposal: TradeProposal) -> RiskCheckResult` | `aletheia/risk/engine.py:67` |
| 并发保护 | 全局线程锁 `_eval_lock`,防 kill-switch TOCTOU | `engine.py:30, 70-71` |

**契约**:所有 proposal 必须且仅能通过 `evaluate_proposal` 取得放行;同一时刻只有一个 proposal 在评估(序列化)。

**唯一入口不变式(RE-B17)**:执行网关 `_execute_proposal` 中每一条调用 broker 下单函数(`execute_paper_order` / `execute_live_order`)的代码路径,必须在 `risk_result.approved` 检查通过后才能到达——即 risk evaluation 必须前置且其结果被显式消费。不存在绕过 `evaluate_proposal` 直接触达 broker 的路径。可执行测试:spec `RE-B17`。

---

## 2. 检查关卡(代码实际 20 关,含 6b 子关)

> ⚠️ **偏差**:[SYSTEM_ARCHITECTURE_GUIDE](../SYSTEM_ARCHITECTURE_GUIDE.md) 第五章称"五关审批",代码实际是 20 关(0–16 + 6b/17/18/19)。
> 以下 20 关为金标准;ARCH_GUIDE 的"五关"是简化叙事,待该文档批次3标注。
> 历史:本表曾止于 16 关;6b(跨 broker/池同标的去重)、17(账户杠杆比)、18(爆仓缓冲)、19(单标的累计名义)为后续追加,**2026-06-30 补齐契约**(代码↔spec 早已存在,FL-S08/S09 守 17/18;契约滞后已修)。
> **四个新增关卡的失败 token(spec RE-S04b 锁定,floor ≥ 20)**:
> - 6b → `symbol_already_open:{sym}`
> - 17 → `account_leverage_ratio:{r}x>{cap}x` / `account_leverage_check_error:internal_failure`
> - 18 → `liquidation_too_close:{g}%<{b}%`
> - 19 → `exceeds_per_symbol_notional:{sym}:{proj}>{cap}`

| # | 关卡 | 判断 | config 字段 | 失败标签 | 位置 |
|---|------|------|------------|---------|------|
| 0 | Kill Switch | `runtime.kill_switch_active` | `runtime.kill_switch_active` | `kill_switch_active` | engine.py:80 |
| 1 | Symbol Filter | white/blacklist | `risk.symbol_filter_mode/symbol_list` | `symbol_blocked/not_allowed:{sym}` | :83 |
| 2 | Market Allowed | spot/futures 开关 | `exchange.enabled_markets` | `spot_market_disabled`（market_type=='spot' 且 enabled_markets['spot']==False）/ `futures_market_disabled`（market_type=='usdt_futures' 且 enabled_markets['usdt_futures']==False） | :93 |
| 3 | Live State | 非 `live_disabled` | `live.state` | `live_disabled` | :99 |
| 4 | Permission Mode | Observe 仅 paper_internal | `agent.permission_mode` | `permission_mode_insufficient:{mode}` | :104 |
| 5 | Max Position Notional | live/demo 上限 + 回撤缩放 | `risk.max_{live/demo}_position_notional_usdt` | `exceeds_max_position_notional:{n}>{cap}` (live path); `exceeds_demo_position_notional:{n}>{cap}` (demo path, engine.py:128) | :109 |
| 6 | Max Open Positions | live/demo 持仓数 | `risk.max_open_positions_{live/demo}` | `max_{live/demo}_positions_reached:{n}` | :127 |
| 6b | Cross-pool Symbol Dedup | 同 live/demo 池内同标的禁止重复开仓(防跨 broker 重复) | —(始终启用) | `symbol_already_open:{sym}` | :141 |
| 7 | Short Allowed | 方向合法 | `risk.allow_short_{live/demo}` | `short_not_allowed_{live/demo}` | :138 |
| 8 | Max Daily Loss | 日损突破→**自动触发 kill switch** | `risk.max_daily_loss_pct`, `kill_switch_enabled_{live/demo}` | `max_daily_loss_exceeded:{r}<=-{lim}` | :145 |
| 9a | Exchange Allowlist | 交易所匹配(self-contained 豁免) | `risk.allowed_exchange` | `exchange_not_allowed:{a}!={e}` | :162 |
| 9b | Account Mode Match | live↔mode=live / demo↔mode=demo | `exchange.mode` | `account_mode_mismatch:...` | :170 |
| 10 | Max Leverage | 期货杠杆上限 | `risk.max_futures_leverage_{live/demo}` | `exceeds_max_leverage:{l}>{cap}` | :181 |
| 11 | Data Freshness | live<120s / demo<1800s | `_MAX_DATA_AGE_SECONDS=120` | `stale_market_data:age={a}` (live path); `stale_market_data_demo:age={a}` (demo path for bitget_demo/okx_demo only, engine.py:238-241) | :188 |
| 12 | Total Live Exposure | 敞口+新单<上限 | `risk.max_total_live_exposure_pct` | `max_total_live_exposure_exceeded:{p}>{cap}` | :194 |
| 13 | Single Trade Risk | 止损距离×数量<上限 | `risk.max_{/demo_}single_trade_risk_pct` | `max_single_trade_risk_missing_stop_price` (live, no stop_price, engine.py:207); `exceeds_single_trade_risk:{r}>{max}` (live, over cap); `exceeds_demo_single_trade_risk:{r}>{max}` (demo) | :202 |
| 14 | Strategy Lifecycle | live 仅 `live_candidate/live_limited` | `_LIVE_ELIGIBLE_STATES` | `strategy_not_live_eligible:{state}` | :211 |
| 15 | Lesson Constraints | 已接受教训的 block_{symbol/direction/scanner} | `LessonStore.recommendation_json` | `lesson_block_{symbol/...}:{id}:{v}` | :240 |
| 16 | Portfolio Correlation | 部门集中度 + 组合 BTC beta | `risk.correlation_guard_enabled/max_sector_concentration_pct/max_portfolio_beta` | `sector_concentration/portfolio_beta:...` | :245,387 |
| 17 | Account Leverage Ratio | 期货:Σ(notional×lev)/equity ≤ 上限;**fail-closed**(异常即拒) | `risk.max_account_leverage_{live(1.5)/demo(3.0)}` | `account_leverage_ratio:{r}x>{cap}x` / `account_leverage_check_error:internal_failure` | :640 (FL-S08) |
| 18 | Liquidation Buffer | 期货(lev>1):估算爆仓价距入场 ≥ buffer% | `risk.min_liquidation_buffer_pct`(5.0) | `liquidation_too_close:{g}%<{b}%` | :695 (FL-S09) |
| 19 | Per-symbol Cumulative Notional | 同标的同池累计名义(含本单)≤ 上限(CUM-ADD 均价合并会绕过 #5 单仓上限) | `risk.max_per_symbol_notional_usdt` | `exceeds_per_symbol_notional:{sym}:{proj}>{cap}` | :285 |

> ⚠️ 关卡 #11 的 `_MAX_DATA_AGE_SECONDS` 是模块常量,非 config 字段。若要 config 化需迁移 + 测试。
>
> ⚠️ 关卡 #5 的回撤缩放(`_portfolio_drawdown_scale`, engine.py:356):缩放系数(0-5% DD→1.0, 5-10%→1.0→0.5, 10-20%→0.5→0.0, >20%→0)由组合回撤驱动。**回撤的数据源**:交易所模式(okx_demo/*_demo/live)读 `portfolio_snapshots` 的 `exchange_account` 行(真实账户 equity),**不读**每个 mode 自己的行——后者的 `total_value_usdt` 是持仓 notional(Σqty×price)而非 equity,平掉盈利持仓会让 notional 暴跌被误判成回撤、把 cap 缩到 $0 锁死开仓(2026-06-25 实证:okx_demo notional 272→16 平盈利单,真实账户全程 ~$88k,却拒了一整批 Short)。`paper_internal` 无交易所账户,其 mark-to-market `total_value_usdt` 即 equity,保持原路径。spec: RE-B05(分段)+ RE-B05a(数据源)。

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

**契约**:每次评估**必须**持久化一条 `RiskDecision`(`engine.py:267-277`),无论批准或拒绝——这是因果链 `proposal→risk_decision→order` 的一环([trust §1.1](../governance/trust_and_change_control.md))。

---

## 4. Kill Switch

| 行为 | 实现 | 位置 |
|------|------|------|
| 触发 | 手动 `activate(reason, actor)` 或日损突破自动触发(#8) | `kill_switch.py:21`, `engine.py:159` |
| 持久化 | `runtime.kill_switch_active=True` → `save_config` 落盘(重启不丢) | `kill_switch.py:25` |
| 恢复 | 仅 `deactivate(actor="human")`,**actor 硬编码 human,agent 无路径** | `kill_switch.py:39` |
| 审计 | 激活/恢复均写 JSONL | `kill_switch.py:27,45` |

**不变式**:kill switch 是 §2 第 0 关,激活后任何 proposal 必被拒;恢复是不可被 agent 触达的人工操作。详见 [daily-loss killswitch UTC 陷阱](../../记忆 aletheia-daily-loss-killswitch-utc)。

---

## 5. 仓位计算(两条路径,诚实标注)

> ⚠️ 当前有**两条并存的 sizing 路径**,这是 [execution_path_honesty §1.1](../governance/execution_path_honesty.md) 揭示的事实:

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

**[trust §6.3](../governance/trust_and_change_control.md) 红线**:不得在未经显式用户批准下削弱或移除任何 risk check;新增 check 必须持久化 RiskDecision 且对 API/UI 可见;测试须覆盖 approved + rejected 两条路径。

---

## 7. 已知缺陷

§7 已知缺陷: 截至 2026-06-23, 无已知缺陷待修。

---

## 8. 目标态差距(意图领先代码)

- [execution_path_honesty §1.2](../governance/execution_path_honesty.md):LLM 的 `direction_bias` 直接驱动下单方向,Risk Engine **不验证方向是否合理**——它只管账户/订单安全,不管"这个方向对不对"。这是有意的职责边界(risk 不依赖市场状态判断),但意味着"方向正确性"无硬约束。
- ⚠️ 不要把 market-state guard(如资金费率熔断)塞进 Risk Engine——见 [north_star §10.6](../references/north_star.md),那属策略层。

---

## serenity_isolation — Serenity 双赛道隔离现状与计划

> 产品定义(2026-06-30):采用 Option A PI-central,serenity 不建独立 executor;促进赛道隔离是 Phase defer 工作项。

### 当前现状

| 维度 | 当前实现 | 意图目标 | 差距 |
|------|---------|---------|------|
| 执行模块 | `serenity_executor.py` **不存在**；所有提案均通过主赛道 PI/gateway | serenity 有独立评估入口 | tripwire D2-S02 守护 |
| 头寸池 | check #5/#6 共用同一个池（serenity 占用主赛道配额） | 独立风险池 | ⚠️ 意图差距，defer；D2-C02 守护 |
| PnL 归因 | `analytics/` 和 `portfolio/` 中无 `trade_school` GROUP BY；serenity/主赛道 PnL 混合计算 | PnL 按 trade_school 分组隔离 | ⚠️ D2-S03/D2-B02 tripwire |

### Serenity 尚未实现声明（defer）

`serenity_executor.py` 尚未创建。**在创建该文件之前**，必须：

1. 补充完整的 L2b/L2c spec（D2-B01/C01 升级版）覆盖新执行路径
2. 更新本节 §serenity_isolation 的现状表
3. 决策 check #5/#6 是共用池还是独立池，并在 engine.py check #5/#6 附近用注释声明

Tripwire：`D2-S02`（spec_risk_engine.py）——文件出现时自动变红提示 review。

### evaluate_proposal 唯一入口不变式

**所有 TradeProposal**（无论来源是主赛道 PI 还是未来 serenity_executor）到达 `gateway.submit_proposal` 之前，**必须经过 `evaluate_proposal()`**。

- `gateway._execute_proposal` 是唯一通向 broker 下单的函数
- `evaluate_proposal` 在 `_execute_proposal` 内调用，`risk_result.approved` 守卫在所有 broker 调用之前
- 不存在绕过 risk engine 直接下单的代码路径

spec：`D2-C01`（L2c，spec_risk_engine.py）持续锁住此不变式。
