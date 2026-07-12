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
| RG-03 | P0 | fixed-reporting | `trade_stats`/family/strategy KPI 曾直接统计全部 closed rows | 判断指标只用已验证结果，接口同时解释原始与排除数量 | T12 保留跨消费者 E2E |
| RG-04 | P0 | fixed-core | Operator 7d WR/PF/streak 曾使用 raw closed rows | Operator、策略表现和 PI context 只消费 eligible cohort | T12 保留行为不变回归 |
| RG-05 | P0 | fixed-core | conviction calibration 曾接受未验证关闭和旧 active 报告 | 校准只用 eligible，报告带版本/时间且旧版或过期拒读 | T11 archive/supersede 历史报告并重建 |
| RG-06 | P0 | fixed-core | System Operator alpha observed leg 曾接受行政性关闭 | observed leg 已统一为 eligible realized outcome | T12 保留 genuine-vs-administrative E2E |
| RG-07 | P0 | fixed-core | 旧 detector 只认 `reconciled_gone -> adopted` | 已改为账户/instrument 作用域的 administrative close→any-open，source 仅说明 | T12 保留 adopted/native E2E |
| RG-08 | P0 | fixed-core | Learning 设计写“thrash 全局 refreeze”，旧实现未接入 | 持久 incident 已覆盖 scheduler、prompt、ranker、persona/experiment authority | T12 保留重启/恢复 E2E |
| RG-09 | P0 | fixed-core | 旧路径一次可信快照缺失即可行政性 close | 已改为 durable candidate、独立完整 observations；ready 后仍等待 T06/T07 | T12 覆盖重启/部分失败/双视图 E2E |
| RG-10 | P0 | fixed-core | `_try_get_tpsl_fill_price` 写死 Bitget | 三家只读 verified-exit protocol 已交付；尚未接入关闭编排 | RG-T07 使用三态结果分流 verified/admin close，不再调用 broker-specific fallback |
| RG-11 | P0 | fixed-core | 行政性 close 曾复用普通 `close_position` | verified/admin 入口已分流，行政关闭不再制造费用、滑点、PnL、现金或 outcome hook | RG-T08～T10 继续迁移读取/展示消费者，T12 做 E2E |
| RG-12 | P0 | fixed-risk | `today_realized_pnl` 曾同时用于账户、Risk、PI 和表现 | Risk 使用独立账户损失；PI、CLI 与页面使用已验证策略结果 | T12 保留分流 E2E |
| RG-13 | P0 | fixed-reporting | CLI/API/equity curve 默认只显示混合 raw 结果 | 原始账户事件与已验证策略结果已并列，排除数量和原因可见 | T12 保留混合样本 E2E |
| RG-14 | P1 | fixed-behavior | behavior consumers 已统一到中央 read model 并由枚举扫描禁止本地 reason set | 新原因只需更新中央分类；判断路径不会漏过滤 | T10 对合法 raw 展示读取增加双口径保护 |
| RG-15 | P1 | fixed-storage | data model 原缺 close category、fill provenance、eligibility、verification | 无法结构化证明一个结果是否可学习 | additive schema 与 durable observation storage 已交付；历史 backfill 仍由 RG-T11 完成 |
| RG-16 | P1 | fixed-core | post-close hooks 曾没有统一 eligibility gate | verified close gate 与其余 Learning/归因/策略消费者现均只接受 eligible outcome | T12 保留跨消费者回归 |
| RG-17 | P1 | open | doctor ratio 只手写排除部分 synthetic reason | 诊断自身与中央语义漂移 | doctor 复用中央分类，并分别报告历史比例/活动指纹 |
| RG-18 | P1 | partial-core | calibration/memo/reasoning active 报告现有版本与过期拒读；interval job 补跑水位仍缺 | 旧污染报告不再进入 prompt/policy，但可能尚未归档 | T11 完成 continuity 与 supersede |
| RG-19 | P1 | fixed-behavior | OI-C02 已枚举所有行为消费者并禁止本地 blacklist | 判断路径由同一可执行保护锁定 | T09/T10/T12 分别补 Risk、展示和 E2E |
| RG-20 | P1 | decision | 历史 paper cash ledger 含假关闭结算 | 本地模拟余额保留事故造成的虚减 | 保留原事件；owner 决定是否追加补偿事件 |
| RG-21 | P1 | partial-core | 旧 calibration/memo/reasoning knowledge 仍未归档，但缺版本或过期内容已拒读 | 旧污染内容不再影响新判断，历史状态仍需治理 | T11 archive/supersede 后重新生成，禁止硬删除 |
| RG-22 | P0 | open | 当前盈利判断仍混用污染窗 | “循环能跑”被误当“循环有 edge” | 版本化 clean-window anchor，只用 post-fix eligible outcomes |

## 5. 当前消费者矩阵

本表是实施时必须逐项关闭的清单。`raw` 不代表一定错误；只有标为 account/audit 的 raw 消费是
合法的。所有 judgement/behavior 消费必须使用 eligible cohort。

| Consumer | 当前角色 | 目标语义 | 审计结论 |
|---|---|---|---|
| `portfolio.pnl.close_position` | 状态、PnL、现金、hook | 按 closure semantics 分流 | fixed-core |
| `today_realized_pnl` | Risk + PI + dashboard | 拆成 account loss / strategy PnL | fixed — Risk 独立，PI/CLI/dashboard 使用 verified strategy cohort |
| `analytics.trade_stats` | CLI/API/UI/PI | eligible | fixed-behavior |
| `analytics.family_scorecard` | UI + PI | eligible | fixed-behavior |
| `build_strategy_kpis` | PI strategy context | eligible | fixed-behavior |
| `agent_shell.operator_loop` | 自动/建议调参 | eligible | fixed-behavior |
| `confidence_calibration` | PI prompt + go-live score | eligible + fresh | fixed-behavior |
| `alpha_attribution._system_operator_alpha` | contribution | eligible | fixed-behavior |
| `server.analytics.equity_curve` | operator display | raw/genuine 两条显式曲线 | open |
| `portfolio.compute_portfolio` | account display | raw account，字段名诚实 | review |
| `risk.engine` daily loss | safety | authoritative account loss，不能用 unverified fallback PnL | fixed-controlled |
| `factor_feedback` | signal gate | eligible | protected + enumerated |
| `guard_promotion/effectiveness` | hard-block authority | eligible + clean floor | protected + enumerated |
| `insight_synthesizer` | memo prompt | eligible + clean floor | fixed-behavior + fresh gate |
| `reasoning_feedback` | PI prompt | eligible + clean floor | fixed-behavior + fresh gate |
| `sizing_policy` | learned size | eligible | fixed-behavior + enumerated |
| `trade_case_builder` | knowledge corpus | eligible | protected + enumerated |
| `go_live_scorecard` performance criteria | readiness | eligible | fixed-behavior + enumerated |
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
