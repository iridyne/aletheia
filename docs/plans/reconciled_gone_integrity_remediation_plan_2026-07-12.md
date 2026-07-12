# Reconciled-gone 结果完整性整改计划

> 日期：2026-07-12  
> 状态：待按阶段实施  
> 对应审计：`docs/issues/RECONCILED_GONE_INTEGRITY_AUDIT_2026-07-12.md`  
> 本机运行证据：`data/observation/reconciled_gone_integrity_audit_2026-07-12.md`
> （不进入 Git）

## 1. 目标和边界

目标不是删除 `reconciled_gone`，而是把它恢复为诚实的行政性状态事件，并保证它不能伪装成
策略交易结果。完成后，系统必须同时满足：

1. 一次外部仓位视图缺失不能关闭本地持仓；
2. 不同 broker、Demo/Live、外部账户与 `paper_internal` 互不解释；
3. 只有得到真实退出证据的关闭才能产生可学习的经济结果；
4. 行政性关闭永久保留审计，但不进入胜率、Profit Factor、校准、Operator、Learning、
   策略排名或自动仓位建议；
5. Risk 使用权威账户损失，不把未验证的 fallback PnL 当风险事实；
6. CLI、API 和 UI 同时展示 raw ledger 与 genuine outcome，名称和排除数量清楚；
7. 历史原始记录不删除、不静默改写，修复通过追加分类、supersede、archive 和可审计补偿完成；
8. 代码、可执行设计断言、文档、迁移、单元测试和端到端测试同步闭环。

本计划不授权以下行为：

- 不开启 live trading，不下单，不修改风险阈值；
- 不自动改写历史仓位、成交、审计或现金流水；
- 不自动追加历史现金补偿；补偿必须由 owner 单独批准；
- 不在单一任务中同时修改多个受控边界并用一组宽泛测试代替逐边界证明；
- 不因修复统计口径而重新启动实验或宣称策略已经盈利。

## 2. 目标架构

```text
broker-scoped position observation
        │
        ▼
disappearance candidate ── single/incomplete view ──► keep open + audit/alert
        │
        ▼
durable confirmation OR direct broker verification
        │
        ├─ verified exit fill ──► verified strategy close
        │                          ├─ economic fields
        │                          ├─ account settlement
        │                          └─ eligible outcome gate
        │
        └─ no verified fill ─────► administrative close
                                   ├─ state convergence + audit
                                   ├─ no fabricated economics
                                   └─ outcome_eligible = false

raw account/audit readers ─────────────► all immutable events
judgement/behavior readers ────────────► eligible strategy outcomes only
operator-facing reporting ────────────► raw + genuine, explicitly labelled
```

统一结果判断应位于 analytics/data 侧的中立模块，例如
`aletheia/analytics/outcome_integrity.py`。Risk、Execution、Portfolio 和 Learning 都可以依赖该
中立语义；禁止让 Risk 反向依赖 Learning。`aletheia/learning/outcomes.py` 负责组合 Learning 特有的
干净窗、最小样本和归因条件，不再独立维护 close reason 黑名单。

## 3. 数据模型和兼容规则

### 3.1 追加字段

最终字段名在第一阶段的设计断言中定稿，至少要表达以下事实：

| 字段语义 | 示例 | 规则 |
|---|---|---|
| close category | strategy / administrative / maintenance | 不从单一字符串临时猜测 |
| exit price source | broker_fill / local_fill / mark_fallback / unavailable | source 与值分开 |
| exit fill verified | true / false | 只有 broker/local execution 原件可为 true |
| outcome eligible | true / false | judgement 消费者的唯一入口之一 |
| exclusion reason | administrative_close / unverified_fill / pre_clean_window 等 | 必须可解释 |
| reconciliation account identity | broker + environment + account fingerprint | 不存凭证，不跨账户解释 |
| confirmation evidence | view sequence/direct-query result/version | 可跨重启、可审计 |
| semantics version | 整数或稳定版本串 | 允许将来重算而不改原值 |

迁移必须是 additive：旧 `close_reason`、`realized_pnl`、`exit_price`、fees、slippage 和 audit 原值
保持不动。新字段描述“这些旧值能否被信任和用于何处”，不能用回填制造不存在的 fill。

### 3.2 历史回填

1. 中央 close-reason 分类能明确识别的 maintenance/synthetic 行回填为
   `administrative + outcome_eligible=false`；
2. 旧行只有价格但没有退出原件时，不能因为价格看似合理就标为 verified；
3. 无法判断的行标为 `unknown/unverified`，默认不进入行为改变型消费者；
4. 回填记录 semantics version、执行批次和计数，重复运行结果完全一致；
5. dry-run 先输出每类数量和将变化的新字段，不修改原始经济字段；
6. 迁移失败必须回滚整个批次，不允许半分类状态；
7. 历史知识和报告只 archive/supersede，不 hard delete。

## 4. 分阶段实施

每个阶段都使用独立任务、独立分支和自己的 task packet。前一阶段没有通过验收，后一阶段不得
用临时兼容绕过。涉及受控模块时，先补对应可执行设计断言，再改行为。

### Phase 0 — 固化证据和计划（RG-T00，本任务）

范围：只写审计、计划、问题总账和任务交接。

产物：

- 完整问题清单 RG-01～RG-22；
- 本计划、依赖顺序、owner 决策门和完成定义；
- 单机运行快照放在 gitignored `data/observation/`；
- `BACKEND_ISSUES.md` 中的主入口。

验收：文档链接有效、22 项都有实施归属、`git diff --check` 和工作流严格检查通过。

### Phase 1 — 先写可执行设计和数据语义（RG-T01）

范围：设计文档与 `tests/specs/`，先得到预期 RED；不改生产行为。

工作：

1. 新建 `tests/specs/spec_outcome_integrity.py`，覆盖关闭分类、verified fill、结果资格、双口径
   read model、行为消费者、行政性关闭经济字段和事故 refreeze；
2. 更新 `docs/design/execution_contract.md`：区分 verified close 与 administrative close；
3. 更新 `docs/design/learning_contract.md` 和 `learning_evolution_contract.md`：统一结果入口、
   全局 refreeze、旧报告 staleness/supersede；
4. 更新 `docs/design/data_model.md`：追加字段、迁移和审计语义；
5. 如 API 响应改变，预先更新 `docs/design/api_reference.md`；
6. 扩充架构测试，枚举所有 behavior-changing closed-position consumer，禁止本地硬编码 reason set。

关键断言：

- `status=closed` 不等于 eligible outcome；
- 一次 view miss 永不关闭仓位；
- administrative close 不制造费用、滑点、现金结算或 outcome hook；
- Risk 不读取 unverified realized PnL；
- close 后任何 source 的同 identity reopen 都触发事故保护；
- Learning incident gate 是全局门，不是单个 consumer 自行过滤。

验收：新增断言因缺实现而按预期失败，已有 specs 不因文字/编号漂移失败；Conformance Matrix 能清楚
显示 missing 项。

### Phase 2 — 追加数据模型和中央结果语义（RG-T02、RG-T03）

#### RG-T02：schema 和 repository

状态：已实现并通过 focused migration/repository/spec tests；行为仍由后续任务负责。

范围：SQLite schema/migration/repository；不改变 reconciler 和 close 行为。

工作：

- 添加 §3 的结构化字段和必要索引；
- repository 写入保持向后兼容，旧调用不会把未知值误标为 eligible；
- 提供 dry-run/backfill 基础设施和迁移批次审计；
- 测试新库、旧库升级、事务回滚、幂等和并发写。

验收：旧库无损升级；原始列逐值不变；未知历史行 fail-closed；SQLite 三项并发不变量保持。

#### RG-T03：中央 read model 和架构护栏

状态：中央分类、三种读取口径及 Learning adapter composition 已实现；全消费者架构护栏和迁移继续由
RG-T08～T10 完成。

范围：analytics/data 中立层与查询；不迁移业务消费者。

工作：

- 实现 `ClosureSemantics` / `OutcomeAssessment`；
- 提供显式 `raw_account_events()`、`eligible_strategy_outcomes()`、
  `administrative_closures()`；
- 中央化 synthetic/maintenance 分类；
- `learning/outcomes.py` 组合中央判断与 clean-window 规则；
- 架构测试扫描禁止 judgement/behavior 模块直接读取无过滤 closed rows。

验收：同一 fixture 在所有入口得到一致分类；新增 close reason 只改中央注册和设计断言；没有循环依赖。

### Phase 3 — 事故检测和 Learning 全局冻结（RG-T04）

状态：已实现 native/adopted 通用探测、持久事故、显式恢复门、Learning 全局冻结和 doctor 展示；
等待本任务完整验证与合并。

范围：reconciliation incident detector、Learning control、doctor；不改正常下单和风险阈值。

工作：

- 将旧 `reconciled_gone -> adopted` 指纹升级为
  `administrative close -> any open`，identity 至少包含 account、broker、environment、instrument、direction；
- `native`、`adopted` 分别保留为说明字段，不作为触发限制；
- detector 在 destructive reconcile action 前执行；
- incident 持久化、去重，并接入 Learning 全局 refreeze；
- refreeze 时恢复上一个已验证 baseline，禁止应用新 learned state；
- doctor 使用中央分类，分别显示历史比例、近期循环、冻结原因和恢复条件；
- 恢复必须显式且有干净观察窗，不能因一次正常轮询自动解冻。

验收：adopted/native 两种 reopen 都阻止继续破坏；事故跨重启仍有效；Learning 所有入口共同冻结；
只读报告继续可运行。

### Phase 4 — 消失确认与跨 broker 真实退出证据（RG-T05、RG-T06）

#### RG-T05：durable disappearance confirmation

状态：已实现候选/观察证据、独立快照去重、过期/错账户拒绝、重连保留和重新出现结案；为避免在
RG-T06/T07 前继续制造伪经济，ready candidate 当前明确保持 open，不调用旧 fallback close。

范围：portfolio reconciler 和确认状态；不触碰 exchange signer/credentials。

工作：

- 一个 complete view miss 只创建 candidate，不关闭；
- 至少两个独立完整视图或一次 broker 直查明确 absent 才能进入收口判断；
- incomplete/stale/wrong-account view 不累计确认；
- 确认状态持久化，包含 view identity、时间、broker/account/environment 和 observation hash；
- 重新出现立即清除 candidate 并留审计；
- live 模式默认 alert-only，除非原有安全合同明确允许且专门设计断言通过。

验收：单 miss、cache 过期、broker 切换、Demo/Live 混合、重启、部分 API 失败均不误关；确认逻辑幂等。

#### RG-T06：broker-neutral verified-exit protocol

状态：已实现统一只读三态协议和 Bitget/OKX/Binance 直查/分页/标准化；错误与不完整历史 fail-closed。
本任务不接入 close/accounting，等待 RG-T07 分流。

范围：Execution/exchange client 受控边界；这是单独审批和单独回归任务。

工作：

- 定义类似 `get_verified_position_exit(...)` 的 broker-neutral protocol；
- Bitget、OKX、Binance 分别实现真实成交查询和标准化 provenance；
- 返回值区分 `verified_fill`、`confirmed_absent_without_fill`、`inconclusive`；
- symbol 转换只在 adapter 边界，查询必须带完整 venue/market/account identity；
- 网络错误、权限不足、分页不完整一律是 inconclusive，不能伪装 absent；
- 不记录或返回密钥、签名材料。

验收：三 broker contract fixture + focused client regression 通过；错误响应 fail-closed；原 Execution Gateway
下单不变量不变。开始本任务前需 owner 确认受控模块实施范围。

### Phase 5 — 平仓状态与经济结算分流（RG-T07）

状态：已实现 verified/admin 两入口、精确 fill/fee 结算、行政关闭无经济副作用、幂等 cash/hook、
ready demo candidate 三态路由和 live alert-only；Risk authoritative loss 仍由 RG-T09 迁移。

范围：portfolio close orchestration、PnL/cash、post-close hook；不修改开仓、仓位大小或风险阈值。

工作：

- 明确两个入口：verified strategy close 与 administrative close；
- verified close 使用真实 fill/provenance 计算费用、滑点、PnL 和结算；
- administrative-unverified close 可以收敛状态，但经济字段为 unavailable/不可信，不生成新的伪费用、
  伪滑点、伪 cash settlement；
- post-close hook 按 policy 分成 raw/audit hook 与 outcome-only hook；
- 所有事件保留原 causal IDs 和真实 actor/source；
- 拆分含混的 `today_realized_pnl`：账户损失和 eligible strategy PnL 使用不同命名/查询。

验收：行政关闭对现金和策略统计无副作用；verified fill 精确结算；重复调用不双重结算；审计链不断；
旧调用迁移期间 fail-closed。

### Phase 6 — 迁移所有消费者（RG-T08、RG-T09、RG-T10）

#### RG-T08：Operator、Learning 和 PI 判断消费者

状态：已实现。表现统计、Operator/PI 上下文、校准、System Operator alpha、归因、
memo/reasoning、persona sizing、promotion、go-live 和 guard 判断统一读取当前版本的 verified
strategy outcomes；旧版/过期派生报告在进入 prompt 或 policy 前拒读。Risk、双口径展示和历史归档
仍分别由 RG-T09、T10、T11 完成。

本任务中的每一项都是整体修复的强制完成门，不是“有空再做”的分析优化。只要任一路仍直接使用
未过滤 closed rows，RG-01～RG-22 就不能整体关闭。

2026-07-12 再核对确认：Operator、conviction calibration、System Operator alpha、Risk account
loss，以及 trade stats / family scorecard / strategy KPI 等多条表现统计当前仍各自读取。这些不是
同一个修复的重复项，而是彼此独立的污染入口，全部纳入本 goal。新增 OI-C02/OI-C03 枚举式可执行
断言中 OI-C02 已转 GREEN；OI-C03/C04 和历史修复断言继续保持欠账：

- RG-T08A：trade stats、family scorecard、strategy KPI 等全部表现统计；
- RG-T08B：Operator 7d 指标、trading context、calibration active report、alpha observed leg；
- RG-T08C：Learning/output hooks、旧报告 freshness/supersede 和跨消费者架构扫描；
- RG-T09：Risk authoritative account loss（受控模块，单独审批和测试，不与策略表现混用）。

按行为影响优先迁移：

1. `analytics.trade_stats`；
2. family scorecard / strategy KPI；
3. `agent_shell.operator_loop` 的 7d WR/PF/streak 和策略表现；
4. conviction calibration 及注入 PI prompt 的 active report；
5. `_system_operator_alpha` observed leg；
6. trading context 中所有 trade performance；
7. post-close outcome hooks；
8. 已有 factor feedback、promotion/effectiveness、insight、reasoning、sizing、trade case、
   go-live consumer 加跨模块回归，防止退化。

规则：判断型默认只返回 eligible；样本不足要显示 insufficient，不退回 raw 填数；旧 active 报告先因
semantics version/staleness 被拒读，不能等历史清理后才安全。

验收：合成样本加入 fixture 前后，Operator 建议、校准、alpha、Learning 产物和 PI prompt 完全不变；
加入 genuine outcome 后按预期改变。

#### RG-T09：Risk daily-loss 单独整改

状态：已实现。Risk check #8 现读取独立 authoritative account snapshot；本地 paper close 必须
与现金账本逐笔一致，exchange-backed close 必须具备当前版本的 verified exact economics。未知或
不一致的账户结果显式阻断新 proposal，但不伪造日损 breach、不自动触发 kill switch。阈值、资本基数
和 check 顺序未改变。

范围：`aletheia/risk/` 受控边界；不得顺带改风险限额。

工作：

- 定义 authoritative account loss 来源及缺失行为；
- unverified exit economics 不得增加 daily realized loss 或触发基于该值的风险状态；
- 来源缺失时 fail-closed/不可用，不回退到策略表现统计；
- 保留账户安全与策略表现两个数字的明确分离。

验收：focused risk specs 证明 genuine account loss 仍触发原保护、administrative/unverified 不伪触发、
来源缺失不会显示虚假健康。开始本任务前需 owner 确认受控模块实施范围。

#### RG-T10：CLI/API/UI 诚实双口径

状态：✅ 2026-07-12 完成。CLI、分析接口和现有绩效组件默认展示已验证策略结果，
同时并列原始关闭事件、排除数量与原因；权益图保留两条明确标注的曲线，未实现新页面或改变交易行为。

范围：inspect/performance CLI、analytics API、Overview/相关表现页面；不改变交易行为。

工作：

- 默认 judgement 指标使用 genuine；
- 同屏/同响应提供 raw event count、eligible count、excluded count 和主要排除原因；
- equity curve 明确分为 account/raw ledger 与 genuine strategy curve，禁止用一条无标签曲线混合；
- API schema 和 docs 同步；
- UI 缺数据时显示 unavailable/insufficient，不用 0 或绿色状态代替。

验收：CLI/API/UI 对同一 fixture 的数量一致；小 viewport 和普通 viewport 均不隐藏口径标签；API
性能符合 async handler 规则。

### Phase 7 — 历史污染治理（RG-T11）

状态：✅ 2026-07-12 代码与操作流程完成。维护命令默认 dry-run，apply 仅做确定性分类、
归档和 supersession 标记；可安全重建的无 LLM 报告按新窗口重算。paper cash 只输出候选，
没有执行补偿的入口；是否追加补偿事件仍由 owner 另行决定。Git 中不记录本机是否已 apply。

范围：一次性、幂等、默认 dry-run 的维护工具；不删除历史。

执行顺序：

1. 运行语义回填 dry-run，按 category/source/version 输出计数；
2. 写入 additive classification；
3. archive/supersede 旧 calibration、Operator assessment、derived knowledge 和其他被污染的 active 内容；
4. 记录 superseded_by、reason、semantics version 和批次；
5. 以版本化 clean-window anchor 重新生成允许重建的派生内容；
6. 生成 paper cash 补偿候选报告，但不写补偿；
7. owner 明确批准后，另开任务追加 compensating event；原 cash events 永不删除或改写。

验收：dry-run 与 apply 数量一致；重复 apply 无新增变化；旧内容不可再被 active query 读取但仍可审计；
补偿前后账本均能解释且总和可复核。

### Phase 8 — 端到端、浸泡和完成审计（RG-T12）

必须覆盖以下场景：

| 场景 | 期望结果 |
|---|---|
| `paper_internal` + 外部空缓存，完整 scheduler reconcile cycle | 本地持仓保持 open，不生成 candidate/close |
| 一个完整视图 miss | 只记录 candidate，不 close |
| 连续完整 miss 或 broker 直查 absent，但无 fill | administrative close，无经济结算、outcome=false |
| Bitget/OKX/Binance 有真实 exit fill | genuine close，价格/provenance/经济字段精确 |
| administrative close 后 adopted reopen | breaker 触发，无继续 destructive action，Learning refreeze |
| administrative close 后 native reopen | 与 adopted 同等保护 |
| 重启发生在第一次 miss 之后 | confirmation 继续且不重复累计同一 view |
| live mode 出现不确定消失 | alert-only/fail-closed，不自动制造退出 |
| broker/account/environment 交错快照 | 彼此完全隔离 |
| synthetic 行加入统计库 | CLI/API/UI、Operator、calibration、Risk、Learning、cash、genuine curve 不变 |
| 历史迁移重复执行 | classification、archive、supersede 和 dry-run 幂等 |

验证层级：

1. 每个任务的 focused unit/spec；
2. reconciliation + PnL + Learning + Operator + Risk 集成测试；
3. `tests/specs/` 和 Conformance Matrix；
4. architecture fitness；
5. 全量 `tests/`；
6. `aletheia doctor`；
7. paper-only 本机浸泡，观察至少覆盖多个 reconcile 周期和一次进程重启；
8. 只读复算 raw/genuine/排除数量，和迁移报告对账。

浸泡期间不启用 live，不放宽风险，不用手工修改 DB 来制造通过。

## 5. 任务依赖和审批门

```text
RG-T00 证据/计划
  └─ RG-T01 可执行设计
       └─ RG-T02 schema
            └─ RG-T03 中央结果语义
                 ├─ RG-T04 detector + Learning refreeze
                 ├─ RG-T05 disappearance confirmation
                 │    └─ RG-T06 broker verified exit 〔owner 受控边界确认〕
                 │         └─ RG-T07 close/accounting split
                 └────────────────┬─ RG-T08 Operator/Learning consumers
                                  ├─ RG-T09 Risk daily loss 〔owner 受控边界确认〕
                                  └─ RG-T10 CLI/API/UI
                                         └─ RG-T11 history remediation
                                              └─ RG-T12 E2E/soak/final audit
```

决策门：

1. **Gate A**：RG-T01 的语义和字段设计通过后才允许写迁移；
2. **Gate B**：RG-T06 涉及 execution/exchange client，开始前由 owner 确认任务边界；
3. **Gate C**：RG-T09 涉及 Risk，开始前由 owner 确认只改数据来源语义、不改限额；
4. **Gate D**：任何 paper cash 补偿必须先提交 dry-run，再由 owner 明确批准；
5. **Gate E**：全量测试和 paper soak 通过也不自动开启 live 或重启实验。

## 6. 问题到任务的完整映射

| 问题 | 主任务 | 验收落点 |
|---|---|---|
| RG-01 paper_internal 隔离 | T01/T05/T12 | scheduler E2E 永不进入外部对账 |
| RG-02 broker cache 隔离 | T01/T05/T12 | wrong-broker/incomplete view fail-closed |
| RG-03 trade/family/KPI | T03/T08 | judgement 只读 eligible |
| RG-04 Operator raw 指标 | T08 | 合成行不改变自动/建议动作 |
| RG-05 calibration 污染 | T08/T11 | 拒读旧版本并重建 |
| RG-06 System Operator alpha | T08 | observed leg 只含 eligible |
| RG-07 native reopen 漏检 | T04/T12 | adopted/native 同等触发 |
| RG-08 Learning 未全局 refreeze | T04/T12 | incident gate 覆盖所有 Learning 应用入口 |
| RG-09 一次 miss 即关闭 | T05/T12 | durable confirmation |
| RG-10 Bitget-only fill | T06/T12 | 三 broker protocol |
| RG-11 行政关闭伪经济 | T02/T07/T12 | 无伪费用/PnL/cash/hook |
| RG-12 today_realized_pnl 含混 | T07/T09/T10 | account 与 strategy 分离 |
| RG-13 CLI/API/equity 混合 | T10 | raw/genuine 双口径 |
| RG-14 本地 reason set 漂移 | T01/T03 | 中央注册 + 架构测试 |
| RG-15 schema 证据不足 | T01/T02 | additive structured fields |
| RG-16 hooks 无统一 gate | T01/T07/T08 | raw-only/outcome-only policy |
| RG-17 doctor 部分分类 | T03/T04 | 中央分类和真实 incident state |
| RG-18 interval/staleness | T01/T08/T11 | continuity + stale rejection + supersede |
| RG-19 SDD 消费者覆盖不足 | T01/T03/T12 | 枚举式可执行合同 |
| RG-20 paper cash 历史污染 | T11 + owner gate | dry-run 后可选追加补偿 |
| RG-21 旧派生内容 active | T08/T11 | 先拒读、后 archive/supersede |
| RG-22 盈利窗污染 | T03/T08/T11/T12 | versioned clean-window，只用 eligible |

## 7. 每个代码任务的最低验证要求

每个任务至少执行并登记：

```text
uv run pytest <focused test files> -q
uv run pytest tests/specs/spec_outcome_integrity.py -q
uv run pytest tests/test_architecture_fitness.py -q
uv run aletheia agent workflow task verify <TASK-ID> --command-key <registered-key>
uv run aletheia agent workflow check --strict
```

涉及 risk/execution/exchange/portfolio close 的任务还必须分别执行对应受控模块 specs；不能只用
`tests/specs/ -q` 的总通过掩盖缺少 focused regression。RG-T12 最终执行：

```text
uv run pytest tests/specs/ -q
uv run pytest tests/test_spec_conformance.py -v -s
uv run pytest tests/ -q --tb=line
uv run aletheia doctor
```

前端发生变化时额外执行 `cd frontend && npm run build`，并在 `:8787` 验证实际 production build。

## 8. 失败和回滚策略

- schema 迁移失败：事务回滚，旧读取路径保持可用，但新的 judgement consumer 不允许退回 raw；
- broker 查询失败：结果为 inconclusive，持仓保持，不行政关闭；
- outcome 分类失败：默认 ineligible，不默认 eligible；
- active 派生报告版本未知：拒绝注入决策，显示 stale/unavailable；
- Learning incident gate 状态不明：保持 refrozen，不自动解冻；
- UI/API 双口径之一不可计算：显示 unavailable 和原因，不用另一个数字冒充；
- 历史治理中途失败：原始事件不变，批次未完成部分不成为 active；
- 任何阶段发现设计与真实 broker 行为冲突：停止该受控任务，保留证据，回到 owner 决策门。

## 9. 最终完成定义

只有同时满足以下条件，RG-01～RG-22 才能整体关闭：

- 已知两个 false-orphan 根因有完整 E2E，不只是单元测试；
- 单次/错误账户/不完整快照无法关闭持仓；
- 三个 broker 的 verified exit 语义统一且 fail-closed；
- administrative close 不产生虚假经济副作用；
- 所有行为改变型消费者只读 eligible outcome，并由架构测试枚举保护；
- Risk account loss 与 strategy performance 语义分离；
- raw/genuine 在 CLI、API、UI 一致且诚实；
- 历史派生污染已经 archive/supersede，原始证据仍可审计；
- clean-window anchor 已版本化，样本不足时明确说不足；
- focused、spec、architecture、full suite、doctor 和 paper soak 均通过；
- task handoff、SDD、issue ledger、DEVLOG/决策记录按实际变更同步；
- 没有借整改自动开启 live、修改风险阈值或宣称盈利已经得到证明。
