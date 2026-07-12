# Reconciled-gone 结果完整性审计

> 日期：2026-07-12  
> 性质：跨 Position Reconciliation、PnL、Risk、Operator、Learning、CLI/API/UI 的系统性审计  
> 运行态证据：每台机器在 `data/observation/reconciled_gone_integrity_audit_2026-07-12.md`
> 保留，不进入 Git；当前状态必须重新运行 `aletheia doctor` 和只读查询确认。

## 1. 长期有效的结论

`reconciled_gone` 的合法语义是“本地持仓记录需要行政性收口，但系统没有可证明的策略退出
成交”。它可以描述状态收敛，不能自动成为胜率、Profit Factor、策略收益、校准、Learning 或
Operator 调参的交易结果。

以下现象一律视为故障，而不是正常对账：

- `paper_internal` 被外部账户视图判为消失；
- 同一 instrument + direction 在行政性关闭后短时间重新 open，不论新记录来源是
  `adopted` 还是 `native`；
- 未确认真实退出 fill，却写入确定的策略盈亏、手续费、滑点或本地现金结算；
- 任何会改变候选排序、仓位大小、风险判断、校准或自动 guard 的消费者直接统计合成关闭；
- UI/CLI 把 raw ledger 和 genuine strategy outcome 混成一个没有标签的“交易表现”。

现行 `paper_internal -> local_only` 和 active-execution-account 作用域修复已经阻止已知源头继续
制造本地假孤儿；这不等于历史污染和所有下游消费者已经修复。

## 2. 统一术语

| 术语 | 定义 | 是否可参与策略表现 |
|---|---|---:|
| raw account event | 系统实际记录过的状态/账本事件，永久保留 | 不一定 |
| genuine strategy outcome | 有真实退出语义、经济字段完整、通过干净窗和归因门的交易结果 | 是 |
| administrative close | 为收敛本地状态而关闭，没有可证明的策略退出 | 否 |
| verified external close | 交易所明确确认退出成交、价格和原因，能够恢复真实 fill | 满足其余门后可 |
| unverified exit economics | 只知道仓位不在快照里，价格来自 mark/current/entry fallback | 否 |
| synthetic close reason | `close_reasons.SYNTHETIC_CLOSE_REASONS` 中的行政/维护原因 | 否 |

## 3. 已确认的历史故障族

### 3.1 错 broker 缓存

非 Bitget Demo 账户在 unified view 不可用时曾回退到 Bitget-only legacy cache。该缓存可能
“新鲜但不属于当前账户”，从而把真实 OKX 仓位判为 orphan，产生
`close -> adopted reopen -> close` 循环。

### 3.2 本地模拟与外部对账未隔离

`paper_internal` 没有交易所对应物，曾被 unified 外部空仓视图解释成 orphan，产生
`close -> native reopen -> close` 循环。当前 execution-account scoping 已将它固定为
`local_only`，并有 `PL-ADOPT-22` 保护。

### 3.3 下游仍相信历史合成结果

源头修复后，raw positions 仍被多个表现消费者直接读取。历史合成关闭因此继续影响 CLI、API、
页面、Operator assessment、交易提示和校准；这属于仍在生效的结果完整性缺陷。

## 4. 问题总账

状态约定：`fixed-source` 表示已知生产源已修但仍需保留回归；`open` 表示仍需实现；
`decision` 表示必须由 owner 明确选择历史数据处理方式。

| ID | 级别 | 状态 | 问题 | 对最小赚钱循环的影响 | 目标结果 |
|---|---:|---|---|---|---|
| RG-01 | P0 | fixed-source | `paper_internal` 曾进入外部仓位对账 | 假平仓、重复开仓、退出链失真 | 保持 `local_only`，增加完整 E2E 和运行探针 |
| RG-02 | P0 | fixed-source | 非当前 broker 的新鲜缓存曾被当权威源 | close/adopt thrash，归因断裂 | 账户作用域和 view completeness 继续 fail-closed |
| RG-03 | P0 | open | `trade_stats`/family/strategy KPI 直接统计全部 closed rows | PI 和 owner 得到虚假 WR/PF/family edge | 所有 judgement 指标只走统一 eligible outcome read model |
| RG-04 | P0 | open | Operator 7d WR/PF/streak 使用 raw closed rows | 可自动减小 sizing 或提出错误调参 | Operator 只消费 genuine cohort，同时显示排除原因 |
| RG-05 | P0 | open | conviction calibration 不读 `close_reason` | 污染校准被注入交易决策 prompt | 用统一 outcome adapter，归档并重建旧报告 |
| RG-06 | P0 | open | System Operator alpha 接受仓与 shadows 比较时未排除行政性关闭 | contribution 判断把事故当决策效果 | observed leg 必须是 eligible realized outcome |
| RG-07 | P0 | open | detector 只认 `reconciled_gone -> adopted` | 漏掉 `reconciled_gone -> native` 本地循环 | 统一 close→any-open 指纹，保留 source 维度 |
| RG-08 | P0 | open | Learning 设计写“thrash 全局 refreeze”，实现未接入 | 事故期间仍可继续产出/应用 learned state | incident gate 接入统一 Learning control |
| RG-09 | P0 | open | orphan 一次可信快照缺失即可行政性 close | 瞬时 API/cache gap 可被误当真实消失 | 连续确认或 broker 直查，确认状态可跨重启 |
| RG-10 | P0 | open | `_try_get_tpsl_fill_price` 写死 Bitget | OKX/Binance 真实退出无法恢复，退化成假经济结果 | broker-neutral verified-exit protocol |
| RG-11 | P0 | open | 行政性 close 复用普通 `close_position` | 伪造手续费、滑点、PnL、现金结算和部分下游 hook | verified strategy close 与 administrative close 分流 |
| RG-12 | P0 | open | `today_realized_pnl` 同时用于账户、Risk、PI 和表现 | 一个含混数字跨越安全/判断语义 | 明确拆分 authoritative account loss 与 eligible strategy PnL |
| RG-13 | P0 | open | CLI/API/equity curve 默认只显示混合 raw 结果 | owner 无法判断真实策略表现 | raw 与 genuine 双口径并列、清楚标注排除数量 |
| RG-14 | P1 | open | central close-reason set 存在，但消费者仍有硬编码/部分集合 | 新原因加入后再次漏过滤 | 禁止消费者维护本地 reason set，架构测试锁定 |
| RG-15 | P1 | fixed-storage | data model 原缺 close category、fill provenance、eligibility、verification | 无法结构化证明一个结果是否可学习 | additive schema 与 durable observation storage 已交付；历史 backfill 仍由 RG-T11 完成 |
| RG-16 | P1 | open | post-close hooks 没有统一 eligibility gate | shadow/Serenity/后续新 hook 可能被行政性事件触发 | hook policy 明确 raw-only 与 outcome-only |
| RG-17 | P1 | open | doctor ratio 只手写排除部分 synthetic reason | 诊断自身与中央语义漂移 | doctor 复用中央分类，并分别报告历史比例/活动指纹 |
| RG-18 | P1 | open | confidence calibration/部分 interval Learning job 无启动补跑水位 | 旧污染报告可长期保持 active | interval continuity、staleness gate、supersede 语义 |
| RG-19 | P1 | open | SDD 只保护部分消费者，缺跨消费者一致性检查 | 局部 GREEN 掩盖系统仍污染 | 新建 outcome-integrity executable contract + consumer matrix |
| RG-20 | P1 | decision | 历史 paper cash ledger 含假关闭结算 | 本地模拟余额保留事故造成的虚减 | 保留原事件；owner 决定是否追加补偿事件 |
| RG-21 | P1 | open | 旧 calibration/operator/derived knowledge 仍 active | 修代码后旧污染内容仍可被读取 | archive/supersede 后重新生成，禁止硬删除 |
| RG-22 | P0 | open | 当前盈利判断仍混用污染窗 | “循环能跑”被误当“循环有 edge” | 版本化 clean-window anchor，只用 post-fix eligible outcomes |

## 5. 当前消费者矩阵

本表是实施时必须逐项关闭的清单。`raw` 不代表一定错误；只有标为 account/audit 的 raw 消费是
合法的。所有 judgement/behavior 消费必须使用 eligible cohort。

| Consumer | 当前角色 | 目标语义 | 审计结论 |
|---|---|---|---|
| `portfolio.pnl.close_position` | 状态、PnL、现金、hook | 按 closure semantics 分流 | open |
| `today_realized_pnl` | Risk + PI + dashboard | 拆成 account loss / strategy PnL | open |
| `analytics.trade_stats` | CLI/API/UI/PI | eligible | open |
| `analytics.family_scorecard` | UI + PI | eligible | open |
| `build_strategy_kpis` | PI strategy context | eligible | open |
| `agent_shell.operator_loop` | 自动/建议调参 | eligible | open |
| `confidence_calibration` | PI prompt + go-live score | eligible + fresh | open |
| `alpha_attribution._system_operator_alpha` | contribution | eligible | open |
| `server.analytics.equity_curve` | operator display | raw/genuine 两条显式曲线 | open |
| `portfolio.compute_portfolio` | account display | raw account，字段名诚实 | review |
| `risk.engine` daily loss | safety | authoritative account loss，不能用 unverified fallback PnL | open |
| `factor_feedback` | signal gate | eligible | protected |
| `guard_promotion/effectiveness` | hard-block authority | eligible + clean floor | protected |
| `insight_synthesizer` | memo prompt | eligible + clean floor | protected |
| `reasoning_feedback` | PI prompt | eligible + clean floor | protected |
| `sizing_policy` | learned size | eligible | protected |
| `trade_case_builder` | knowledge corpus | eligible | protected |
| `go_live_scorecard` performance criteria | readiness | eligible | protected |
| `doctor reconciled_ratio` | diagnosis | raw composition + central categories | partial |

“protected”仍需纳入跨消费者 contract，防止未来回归；不能只依赖当前局部测试。

## 6. 代码架构缺陷

### 6.1 raw state 与 outcome evidence 没有类型边界

`positions.status='closed'` 只说明本地状态结束，不证明发生了可评价的真实交易退出。当前很多模块把
closed row 当 outcome，缺少编译期/结构化边界。

目标：建立单一 `ClosureSemantics` / `OutcomeAssessment`，要求调用方显式选择：

- `raw_account_events(...)`
- `eligible_strategy_outcomes(...)`
- `administrative_closures(...)`

禁止无标签的 `list_positions(status='closed')` 直接进入 judgement/behavior 模块。

### 6.2 reconciler 同时承担发现、确认、经济恢复、状态关闭

一次 cache classification 直接进入 close，且 fill recovery 是 Bitget 特例。目标架构必须拆成：

```text
broker-scoped observation
→ disappearance candidate
→ durable confirmation / direct broker verification
→ verified fill recovery OR administrative-unverified outcome
→ correct close path
```

### 6.3 consumer-side blacklist 不可扩展

硬编码 `{'units_bug_cleanup','reconciled_gone'}` 会遗漏新增加的 maintenance reason。判断必须来自
中央语义，而不是每个模块复制字符串。

### 6.4 事故探针绑定旧根因形状

adopted-only fingerprint 只保护上一轮事故。探针必须描述抽象不变量：“行政性关闭后同 identity
短时间重新 open”，然后把 source 作为解释字段，而不是触发条件。

## 7. SDD 缺口

### 已有且必须保留

- `PL-ADOPT-10~12`：非 Bitget degraded view 不产生 false orphan；
- `PL-ADOPT-17~18`：adopted flip-flop 探针和 reconcile breaker；
- `PL-ADOPT-22~24`：执行账户作用域、live derivation、broker identity；
- `AN-S03`：go-live performance 排除 synthetic；
- `LP-S08/LP-S15`：trade case / factor feedback 排除 synthetic；
- Learning shared outcome adapter 和部分最终消费者保护。

### 必须新增或升级

1. `paper_internal` 的 reconcile E2E：完整 scheduler cycle 中永不 close。
2. detector 覆盖 adopted/native 两类 reopen，并跨 restart 保留确认状态。
3. 单次 miss 不 close；连续确认/直查后才允许收口。
4. Bitget/OKX/Binance verified-fill 恢复的 broker contract。
5. administrative close 不产生交易费用、滑点、现金结算、策略 outcome hook。
6. raw/genuine 双 read model 的一致性与 API/CLI honesty。
7. Risk daily-loss 使用权威账户损失，unverified fallback 不得触发。
8. 所有 behavior-changing consumers 的枚举式架构测试。
9. confidence calibration 与 System Operator alpha 的 synthetic/clean-window 测试。
10. incident → global Learning refreeze → baseline restore 的 E2E。
11. 历史 backfill、archive/supersede、补偿事件的幂等和审计测试。

## 8. 对最小赚钱循环的影响

| 环节 | 当前结论 | 完成标准 |
|---|---|---|
| 机会发现 | 与本事故无直接根因 | 不受改动影响 |
| 决策 | 会读到污染 trade analytics/calibration | prompt 只含 eligible + fresh evidence |
| 风控 | daily-loss 语义与 unverified PnL 混合 | 用权威 account loss，测试 synthetic 不触发 |
| 下单 | 不在本事故主路径 | 不改变 gateway 不变量 |
| 持仓 | 已知 local-only 源头已修 | E2E + runtime detector 证明 |
| 退出 | external verified fill 恢复不完整 | 三 broker 统一、无 fill 时诚实 unavailable |
| 统计 | raw/genuine 混合 | 双口径、默认 judgement=genuine |
| Learning | 部分干净、部分漏网 | consumer matrix 全 GREEN |
| 下一轮决策 | 仍可能被旧派生内容带偏 | 旧内容 superseded + 新 clean cohort |

在上述闭环完成前，只能说交易循环有活动，不能据 raw closed rows 宣称存在或不存在盈利 edge。

## 9. 禁止的“快速修复”

- 不删除或直接改写历史 positions、audit、paper cash events；
- 不把 `reconciled_gone` PnL 改成 0 后继续当 outcome；0 仍是假样本；
- 不在十几个消费者里复制 `NOT IN (...)` 后宣称架构完成；
- 不用 UI 隐藏代替 API/Operator/Learning 修复；
- 不因为当前 mode 是 `paper_internal` 就跳过 OKX/Binance 退出恢复；
- 不用一次 cache miss 作为交易所仓位真实消失的证据；
- 不自动补偿本地现金或重置实验，没有 owner 决策就只生成 dry-run 报告；
- 不在一个提交里同时修改多个 trust boundary 而缺少各自 focused spec。

## 10. 实施入口

完整依赖、阶段、测试和验收命令见：

`docs/plans/reconciled_gone_integrity_remediation_plan_2026-07-12.md`
