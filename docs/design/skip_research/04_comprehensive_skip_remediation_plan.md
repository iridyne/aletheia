# 04 — Skip 专项完整修复实施计划

> 状态：**待 owner 审阅的 SDD 计划，不是行为变更授权。**
> 建立日期：2026-07-15
> 适用范围：System Operator（PI）候选级 `skip` 与其输入、证据、恢复诊断。
> 非范围：universe generation / 选币行为、Risk、Execution、交易所、Live、额度、凭据。

本文把已审计的七个问题逐项转换为可实现、可测试、可回滚的工作包。它取代
[02_implementation_plan.md](02_implementation_plan.md) 中对本专项的实现顺序；尤其是**不**新增
`decision_evidence_epochs` 或任何“为数据库重载而生”的运行时 feature。重载的历史边界继续由
[数据库重载恢复 SOP](../../operations/DATABASE_RELOAD_RECOVERY_SOP.md) 在操作时处理：清除或归档不应
进入实时 PI 的遗留事实，并保留研究档案。行为版本隔离仍使用现有 `learning_policy_epochs`。

## 0. 目标、非目标与不可谈判边界

### 0.1 目标

把当前的单向控制链：

```text
聚合低胜率 → prompt 中的保守措辞 → LLM 全局 skip → pending 被终结
```

替换为两条隔离链：

```text
候选决策链：精确 pending 候选 → trade 或 candidate_skip → 证据 / 固定时点结果
运行诊断链：聚合异常或零 proposal → diagnosis event → 分层归因 / shadow 研究
```

低质量结果必须推动可审计的归因和下一项受控实验；它不能自行成为“所有候选都 skip”的权力。
该目标不等于强制交易、降低 conviction、绕过 proposal 或放宽任何下游硬门。

### 0.2 非目标

- 不以追求更多 proposal 为理由制造交易，真实 Risk 决定、执行和 Live 语义完全不变。
- 不把固定时点反事实结果说成账户 PnL、策略 alpha 或因果证明。
- 不以数据重载为由加入常驻 epoch 表、隐式回收历史或自动修改数据库。
- 不自动修改 scanner、ranker、prompt、conviction、sizing 或配置；任何此类改变都是新的 B 类
  policy epoch。
- 不把 universe generation / membership 的因果研究并入本分支；共同方法见
  [全框架应用指引](../causal_counterfactual_research_guidance.md)。

### 0.3 成功定义

完成后必须同时满足：

1. 每一个终局 PI `skip` 都有唯一、精确的 pending 候选身份；无身份 heartbeat 不再伪装成
   交易决定。
2. 每一个可结算 candidate skip 在同一提交边界留下机器 reason、输入快照、版本、精确入场
   快照和共享的 15m/1h/4h/24h outcome seed；失败时候选不被消费。
3. 聚合 signal health 只驱动诊断，不再以“skip low conviction”语言或隐含权限进入 PI 的单候选
   决策面。
4. family/direction 的统计先经过样本质量和不确定性判定；`n >= 1`、同一短批次的伪大样本，
   不能被呈现为确定负 edge。
5. pending 有流量且 proposal 为零时，系统给出可归因的 `skip_halt_incident` 或
   `protective_skip_observed`，而非无限重复终局 skip。
6. “继续 skip 有证据”与“PI 疑似过度限制”可区分；只有后者才可进入固定回放和前瞻实验。
7. 所有行为改动都在关闭旧观察窗、建立新 `learning_policy_epoch` 后，以一个预登记 treatment
   实施；旧新数据不得混作同一结论。

## 1. 当前事实与问题—工作包映射

| # | 已验证问题 | 当前实现锚点 | 修复工作包 | 分类 |
| --- | --- | --- | --- | --- |
| 1 | 24h 聚合 signal win rate 被 prompt 转译为可 Skip 的行为建议 | `trading_context.py` data health；`trading_decision.py` health alert；`doctor.py` | WP-2：监测与决策权限分离 | B |
| 2 | 全局市场诊断与单候选终局决定混用，出现无 target Skip | heartbeat → `evaluate_trading_opportunity`；`PiDecision` 可无 identity | WP-1：候选身份与状态机分面 | B（身份记录可先 A） |
| 3 | final skip 消费 pending；daemon 只重试异常，合法 skip 没有恢复路径 | `_build_pending_signal_queue`；`pi_daemon.py` | WP-1 + WP-5：终局语义与 liveness | B |
| 4 | direct LLM skip 没有 trace/outcome，且当前写入为 best effort | `record_operator_filter_trace()` 在 final 之后调用 | WP-3：原子证据与固定时点 outcome | A（链完整性） |
| 5 | WR 被当作充分目标，未区分净期望、根因、市场/方向/版本 | `doctor.py`、PI prompt、family edge 展示 | WP-2 + WP-4：指标语义与诊断归因 | B |
| 6 | `family_direction_edge` 以 `n >= 1` 直接向 PI 暴露样本，未表达 epoch/相关性/不确定性 | `repositories.py:family_direction_edge()` | WP-4：合格证据接口 | B |
| 7 | deadlock breaker 只覆盖方向 brake，不覆盖 health/low-conviction/direct skip | `_brake_streak()`；DCLS confidence 场景 | WP-5：通用可观测恢复控制 | A（检测）+ B（任何行为 treatment） |

`A` 只修复事实完整性、身份或可观测性，不能改变同一候选的决策。`B` 会令同一市场下的
PI 输入或决策不同，必须遵守 [实验协议](../../product/EXPERIMENT_PROTOCOL.md) 与
[Bug Triage Protocol](../../governance/BUG_TRIAGE_PROTOCOL.md)：关闭旧窗口、记录 baseline、
新开 policy epoch、收集全新观察窗。

## 2. 先决治理决定与交付次序

### 2.1 进入代码前必须记录的决定

在任务包中以 [03_decisions_to_resolve.md](03_decisions_to_resolve.md) 模板记录以下默认值；没有
owner 确认，不实施相应 B 工作包：

| ID | 建议默认值 | 必须防止的错误 |
| --- | --- | --- |
| G1 | 重载后的遗留 forward outcome/lesson/review 只作研究档案；SOP 已明确处理后才允许新数据进入实时 PI | 用旧负样本压制新候选，或为此新增隐藏 runtime feature |
| G2 | `risk`、`live`、credential、exchange 能力、精确价格不可用、TTL 等 hard/capability reject 永不进入“放宽 Skip”实验 | 用 missed-alpha 侵蚀安全边界 |
| G3 | candidate skip 的唯一证据写入失败即回滚 terminal claim，pending 保持可重试 | “已 skip、无证据”被误报为完整决策 |
| G4 | 新 policy epoch 或 SOP 后样本不足显示 `insufficient_evidence`，绝不替换成负 edge 或自动收紧 | 冷启动被误诊为低质量 |
| G5 | skip-halt 只创建诊断、审计和 shadow 任务；没有独立 B 实验授权时不自动改变配置或开仓 | liveness monitor 变成强制交易器 |
| G6 | 首个 B treatment 是单变量“移除聚合 health 对单候选 skip 的权限”；family-edge 改造为后续独立 epoch | 一次上线多个行为改变，无法归因 |

### 2.2 顺序与关卡

```text
P0 契约/RED specs
  → P1 A类身份、原子证据、只读诊断
  → P1 验收 + 基线报告
  → B1 新 epoch：候选/诊断分面 + 去除聚合 health veto 权限
  → B1 观察窗与报告
  → B2（如证据指向 family edge）新 epoch：合格证据接口
  → 固定回放
  → shadow/paper 单变量实验
  → 人工决定下一行为 epoch
```

不能把 B1、B2、ranker/universe 改动合并为“修 Skip”的一个大提交。每个阶段在当前 HEAD
记录验证后才完成 workflow task；任一步失败只回滚该阶段，不回滚交易事实。

## 3. WP-1 — 候选身份、状态机与无 target heartbeat

### 3.1 目标设计

`PiDecision(action in {'trade','skip'})` 只表示对**一条精确、未过期、已持久化
`signal_pending`** 的候选决定。它必须有：

```text
signal_source + opportunity_id + symbol + direction + market_type
```

其中 `signal_source + opportunity_id` 是终局授权键；其余字段必须与 pending carrier 精确匹配。
`trade` 与 `candidate_skip` 均消费该身份。`signal_pending` 仍是唯一等待状态。

heartbeat 在 `unresolved_total == 0` 时不得调用 LLM 交易评估、不得写 `PiDecision(skip)`、不得
退休 Opportunity，也不得新增 Proposal。它只产生一个独立的操作事件：

```text
operator_evaluation_event
  event_kind = no_evaluable_candidate | queue_backlog | evaluation_unavailable
  trigger = heartbeat | signal
  pending_counts, oldest_age, policy_epoch, data_health_summary
  action = observed
```

推荐新增 append-only `operator_evaluation_events` 表与 model/repository，而不是向 `pi_decisions.action`
继续塞入无目标的伪动作。该表不代表交易意图，不参与 pending 排除，不产生 filter outcome；字段不保存
原始 prompt、凭据或 provider 异常文本。其唯一键为 `trigger window + event_kind + queue snapshot hash`，
用于限频而不是合并历史。

### 3.2 实现步骤

1. 在 `decision_lifecycle_contract.md` 固化 `DL-INV-SKIP-1`：final PI action 必须有精确 pending
   carrier；废止“zero pending heartbeat 可留下全局 skip”的例外。
2. 扩展 `storage/migrations.py`、`storage/models.py`、repository，增加上述 event 载体、索引和
   有界保留；同步 `data_model.md`、`data_utilization.md`、API reference（若暴露只读事件）。
3. 在 daemon 取队列摘要后短路：零可评估候选写 observed event 并返回 `pending_queue_remaining=0`；
   不构建交易 prompt、不调用 `_assess_opportunity`。
4. 在 `commands.evaluate_trading_opportunity` 与 `reflection.claim_decision` 的 claim 内重复校验 pending
   identity。任何缺失/不一致返回 `DecisionUnavailable` 或 observed event，绝不把它归一化成 skip。
5. 保持已存在的 TTL、重复 wake、并发 claim、Proposal 原子语义；新逻辑不得改变 Risk/Execution 链。

### 3.3 RED/验收

- 零 pending heartbeat：零 LLM call、零 final `pi_decisions`、零 Opportunity 状态变化、恰一条
  `no_evaluable_candidate` event。
- pending 候选：最终 skip 必须精确回填五元身份；同键重复 wake 只读原 final，不生成第二行。
- identity 缺失/冲突、LLM 或 debate 不可用：pending 不被消费，零 final / Proposal。
- 历史 global skip 仅作为 legacy audit，不可被新 report 当作 candidate skip 或 outcome cohort。

## 4. WP-2 — 将监测指标从交易权限中剥离，并建立根因诊断

### 4.1 目标设计

`data.signal` 与 `data_health.signal_status` 保留为运维可见的**描述性 observation**。它们不再在
PI 单候选 prompt 中携带任何“reduce / tighten / skip”的建议，也不作为 conviction 或 action 的
输入。doctor 的 remediation 改为：`run skip diagnosis; inspect cohort evidence`，不得建议直接调
scanner/Optuna。

PI 可消费的结果仅是候选匹配的、已合格的 evidence summary，且其语义只能是：

```text
qualified_positive | qualified_negative | insufficient_evidence | unavailable
```

它必须明确 `observation_not_a_veto=true`；在 B1 前，为避免模型把文字当命令，最小 B1 treatment
应直接从 PI prompt 移除全局 aggregate WR/health block，而不是只改措辞。

### 4.2 根因诊断而非“等待窗口”

新增只读 `skip_diagnosis_run`（可为事件的详细 report，不必让 daemon 做昂贵查询），针对一个固定
时间窗和 policy epoch 产生以下 mutually-exclusive primary finding：

| finding | 必要证据 | 下一步权限 |
| --- | --- | --- |
| `outcome_insufficient` | outcome 覆盖、成熟度、独立样本或成本模型不足 | 修数据/继续观察；不改 PI |
| `upstream_supply_gap` | 没有合格 pending 或 producer/ranker 链断 | 修相应链；不是 PI skip |
| `universe_or_ranker_quality_suspect` | 同 cohort 的 pre-PI 候选结果已差 | 另开 universe/ranker 研究任务 |
| `pi_overrestriction_suspect` | 合格 candidate skips 的结果相对可比 pass 明显较好 | 只允许固定 replay，再申请 B treatment |
| `protective_skip_observed` | 合格 candidate skips 的结果不支持暴露且覆盖充分 | 维持当前 policy；不可声称永久正确 |
| `recording_incident` | identity/trace/outcome/epoch/价格任一断裂 | 修 A 类证据链；不解释 alpha |

诊断要报告成本后净期望、median、tail loss、结果覆盖、未成熟数、候选数、时间跨度、市场/方向/
scanner 分层和 cohort 可比性。胜率只是一项辅助计数；它不能单独触发 finding。

### 4.3 实现步骤

1. 把 `_data_health_summary()` 的字段改为明确的 observation schema：计算窗口、样本数、成本模型、
   coverage 和 `status`；不再用 `degraded` 表示“候选应跳过”。同步 doctor wording 与 tests。
2. 从 `trading_decision` prompt builder 移除 aggregate alert；保留在 doctor/operations API 中。不得以
   新的自然语言免责声明替换一个仍会影响 LLM 的命令性 block。
3. 审计 `operator_loop.py` 的 `tighten_confidence` 规则：它同样使用 `<40%` 触发自动配置收紧，
   会与 PI Skip 形成第二个正反馈回路。B1 中将该规则改为只创建 diagnosis event；任何恢复或配置
   mutation 必须在独立、已授权的 B experiment 中处理。
4. 新增 repository/report service，先只读地产生 diagnosis；在 hot PI path 禁止重复聚合 SQL 或
   自动写 config。
5. dashboard/API 只展示真实 finding、证据不足和样本状态；不可用时显示“未诊断”，不能显示
   “市场不宜交易”。

### 4.4 RED/验收

- `signal_win_rate_24h=37%` + 合格候选：PI 输入中不存在 aggregate WR/`skip` 建议；其行动必须由
  候选证据与模型本身决定。
- 同样的候选在 health healthy/degraded 两个 fixture 下，B1 对照的结构化候选 context 相同；仅运维
  diagnosis 有差异。
- 低 WR 但 outcome 不成熟：finding 必为 `outcome_insufficient`。
- WR 低但中位/净期望/分层证明 protective：不发起“解锁”实验。
- operator loop 不再因 aggregate low WR 写 `tighten_confidence` config patch；必须可见 diagnosis event。

## 5. WP-3 — direct Skip 的原子证据、reason taxonomy 与反事实闭环

### 5.1 必须写入的事实

对每一个 candidate final skip，LLM 原始选择、debate 降级、conviction gate、方向刹车和 proposal
确定性拒绝都产生顺序化 `filter_decisions`。最少必须有：

| 事实 | 规定 |
| --- | --- |
| identity | `candidate_key=pi_decision:<id>`；parent 与 exact pending carrier 一致 |
| action trace | `operator_action_assessment`，记录模型 action、initial/final conviction 与 schema version |
| reason code | 机器枚举，不从 reasoning 文本解析；reasoning 仅人工说明 |
| input snapshot | 候选 score、regime、合格 evidence status/摘要、queue age、policy epoch；不存原 prompt |
| parameters | conviction threshold、debate 参数、filter version；health 不可作 veto parameter |
| outcome seed | exact venue/symbol/market/direction、决策时可见 entry snapshot、成本假设、四个 horizon |
| terminal relation | `outcome_candidate_key=pi_decision:<id>`，四 horizon 共享，不按 trace 行重复 |

reason taxonomy 必分层，不能用 `low_win_rate` 或任意自然语言作为 terminal code：

```text
operator_model_skip
candidate_sideways_no_directional_edge
candidate_score_insufficient
conviction_below_minimum
debate_below_minimum
direction_bias_brake
qualified_family_evidence_negative       # 仅当 WP-4 status=qualified
family_evidence_insufficient             # observation，不是 veto
proposal_direction_concentration         # 已有确定性门
hard_capability_* / hard_safety_*        # 仅报告，永不进入放宽研究
```

### 5.2 原子提交要求

当前 `finalize_decision()` 在 terminal row 写完后才 best-effort 调 `record_operator_filter_trace()`；这必须
替换为 repository 级的单一短事务：

```text
验证 exact pending → 插入 final PiDecision → 插入 FilterDecision(s)
→ 插入四个 FilterCandidateOutcome seed → queued→reviewed → COMMIT
```

任一步失败则整体回滚：没有 final PiDecision、没有 Opportunity `reviewed`、没有 Proposal，pending 可重试。
结算后的价格/资金费缺失仍按既有 outcome contract 诚实标记 `pending / unavailable / stale_overdue`，不回写
决策事实。`trade` 的 Proposal 原子 claim 维持现有独立语义；不得借这项工作调整 Risk 或 execution
事务。

实现应把现有 `record_operator_filter_trace` 改为 transaction-aware primitive（调用者传入 db/transaction，
默认不得吞错），或移入 repository。反射层不能再捕获异常后只 warning。旧缺 trace 的 skip 不得通过
事后推断回填；报告将其列为 `recording_incident`。

### 5.3 RED/验收

- 模型直接 `skip`、没有 debate 的 fixture，仍有 action assessment trace 和四个 seed。
- debate/conviction/brake skip 有完整 sequence，且同一 candidate 只有四个 outcome 行，不是每 trace 四个。
- 强制 filter trace 或 outcome seed insert 失败：整个终局 claim 回滚、pending 仍可评估。
- retry 与并发 wake：一份 final、一组 trace、四个 outcome；reason/输入不可被第二次 LLM 改写。
- 无方向、无精确市场、无决策时可见价格：不是 candidate skip；写 `recording_incident` / unavailable，
  不创造伪反事实。

## 6. WP-4 — family edge 的样本质量、不确定性与合格接口

### 6.1 目标设计

`family_direction_edge()` 不再是“所有 n>=1 的 WR/avg return”。保留它或替换为一个只读的
`family_direction_evidence()`，返回不可被误读的结构化结果：

```text
cohort: scanner_family, direction, market_type, horizon, policy_epoch
status: qualified | insufficient_evidence | stale | invalid_cost_data
n_raw, n_effective, distinct_entry_buckets, observation_span, max_bucket_share
net_expectation_summary: mean, median, shrinkage_estimate, interval, tail_loss
win_rate: auxiliary only
exclusion_reasons, cost_model_version, data_boundary_note
```

合格条件必须预登记、版本化、在 report 中显示。它至少检查：完整 net economics、固定 horizon、同一
policy epoch、足够的 distinct entry-time buckets、最短观察跨度、最大单批贡献上限、最小有效样本和
成本模型一致性。有效样本不得简单等于行数；同一短周期密集产生的 100 条 outcome 可因相关性被判为
`insufficient_evidence`。具体阈值写入 B2 experiment packet 和 `filter_version`，不能隐含在 SQL 或
由 LLM 选择。

估计必须同时展示不确定性（例如预登记的 shrinkage prior + interval），而不是把 33% WR/-2.39% 当成
确定性 edge。`qualified_negative` 可以作为候选上下文的一项证据，仍不能单独构成系统级 veto；
`insufficient_evidence` 必须中性，不得被渲染为负面。

### 6.2 实现步骤

1. 给 outcome/family evidence 查询增加 `policy_epoch` 的可追溯来源；历史无法归属时只报告，不能输入
   新 B2 PI context。这里不新建 database-reload epoch，重载边界由 SOP 和现有数据清理保证。
2. 写一个纯 repository/analytics 聚合器，先供只读 report 使用；与现有 raw `family_direction_edge`
   并行运行，比较输出，不改变 PI。
3. 增加样本簇/时间 bucket、成本完整性、市场/方向隔离与 interval 测试；所有 exclusion 写入 report。
4. B2 获批后才把 `qualified_*` 结构化摘要接入 PI；移除 raw `WR/avg_net` 自由文本。B2 的唯一行为
   treatment 是此接口替换，不能顺便调 conviction、ranker 或 universe。

### 6.3 RED/验收

- `n=1`、短时间重复 n=100、跨市场、跨方向、缺 net/funding、跨 policy epoch 均不得为 `qualified`。
- 合格正/负 cohort 的样本数、时间跨度、成本模型和 interval 可复算；同输入结果稳定。
- `insufficient_evidence` 在 PI 展示为中性信息，不能生成 `family_edge_negative` reason。
- raw 与 qualified 影子报告差异可审计；B2 前运行时 PI 查询仍不变。

## 7. WP-5 — Skip-halt liveness、诊断与受控恢复

### 7.1 检测状态机

新增只读 `skip_halt_incident` 聚合，不能复用“连续 15 次方向 brake”的局部 bypass。它必须先确认：

```text
eligible pending flow > 0
AND terminal candidate skip ratio / proposal drought crosses a versioned observation threshold
AND a bounded diagnosis window is complete
```

随后只能输出以下状态之一：

| 状态 | 条件 | 自动行为 |
| --- | --- | --- |
| `not_applicable` | 无 pending 流量 | 记录供给状态，不报警 |
| `awaiting_outcomes` | 有 skip 但 horizon 未成熟 | 等待结算，禁止下结论 |
| `recording_incident` | identity、trace、seed、结算、policy 边界断裂 | doctor/API/audit 提醒，创建 A 类修复任务 |
| `protective_skip_observed` | 合格结果支持 skip | 保持 policy，不触发 bypass |
| `overrestriction_suspect` | 合格 skip cohort 相对可比 pass 有正向净结果且统计门满足 | 只创建 replay / experiment proposal |
| `operator_liveness_incident` | 反复 candidate terminal skip，但无证据支持/原因异常垄断 | 创建 diagnosis 和 shadow task，不强制交易 |

`reason_code` 的垄断、unscoped event 激增、连续 direct model skip、conviction 分布坍缩、queue age、
outcome overdue、proposal drought 都是诊断特征；任何一个都不能直接开仓。

### 7.2 “自愈”的正确含义

自愈不是自动降低门槛或绕过 Skip。它是一个有限状态控制器：

```text
detect → verify identity/outcomes → stratify root cause → freeze affected conclusion
      → create bounded shadow/replay study → obtain human-approved single treatment
      → open new policy epoch → measure → retain/revert treatment
```

因此它不会靠“静待计算窗口”停滞：一旦发生 drought，立即生成可复现诊断和固定的 shadow
candidate cohort；最终 outcome 到期后得到证据。若是数据断链，修链；若是 universe/ranker 问题，
转交独立范围；若 PI 过度限制，进入单变量实验。没有任何分支可以直接把候选送入 Risk。

### 7.3 实现步骤

1. 在 doctor 增加 read-only probe 和可序列化 report；记录 threshold version、窗口、最新 candidate
   时间、pending/proposal/skip counts、finding 和 evidence links。
2. 通过 audit 记录 incident created/resolved，不写 config、不调用 PI prompt、不触碰 Proposal。
3. 将现有 `_brake_streak` 保留为方向集中度的专属安全/业务规则；不把它泛化成“自动 bypass all skips”。
4. 在 DCLS 中建模 candidate 流、direct skip、无 target heartbeat、缺 evidence、真实 protective skip 和
   positive missed counterfactual 六种路径。DCLS 只能证明控制逻辑，不证明市场 alpha。
5. `overrestriction_suspect` 仅自动生成一个不可执行 experiment draft；owner 批准前只能 shadow。

### 7.4 RED/验收

- 有 pending、所有 direct skips 缺 trace：`recording_incident`；零 proposal 仍不打开 bypass。
- 完整且负向的 candidate outcome：`protective_skip_observed`；不误报故障、不强制 proposal。
- 合格正向 skip cohort：`overrestriction_suspect`；只创建不可执行 experiment draft。
- 无 pending 的 heartbeat：`not_applicable`，不计入 skip ratio 或 drought。
- incident 解析/查询失败：显示 `diagnosis_unavailable`，不得显示 healthy 或自行调参。

## 8. B 类行为实验与回滚

### 8.1 B1：候选/诊断分面（首个行为 change）

**单变量 treatment**：从 PI 单候选 decision context 移除 aggregate `signal_status` / `signal_win_rate`
及其“skip low-conviction”建议；零 pending heartbeat 不进入交易评估。其它候选特征、conviction=55、
debate、ranker、Risk、Execution、sizing 均冻结。

**control**：关闭 B1 前的 policy epoch，仅作历史基线，不与 B1 混合统计。

**assignment/安全**：先在 shadow/replay 验证输入隔离；授权 paper/demonstration 后才全量应用 B1。
不做混合随机 assignment，除非 owner 另行批准并固定 candidate hash、salt、allocation、伤害门。

**预注册指标**：candidate-scoped terminal skip ratio、proposal funnel、trace/outcome coverage、queue age、
net outcome cohort、最大回撤/PF/ECE 等实验协议既有指标。不得以 proposal 数量或 WR 单独判优。

**停止与回滚**：触发既有 Kill/安全门、identity 断链、证据覆盖降低或记录故障时，停止新行为、
关闭 B1 epoch、恢复上一已审计 context；不删除 B1 决策或 outcome。

### 8.2 B2：family evidence 接口（仅在 B1 观察后）

前提是 B1 report 明确显示 family-edge 输入是主要可解释因素，且 WP-4 shadow 结果稳定。treatment 仅为
“raw family WR/avg 文字 → qualified evidence status/summary”；不得同时改变 universe、ranker、
conviction 或 sizing。B2 有独立基线、epoch、停止门和验收报告。

### 8.3 后续 treatment 的路由

- diagnosis 指向 universe/ranker：创建独立任务；本专项只交付证据，不改其行为。
- diagnosis 指向 PI prompt/debate/conviction：一次只改一个可识别参数，重复 B1 的程序。
- diagnosis 指向 hard safety/capability：只修数据/能力表达，永不进入放宽实验。

## 9. 契约、代码面与测试清单

### 9.1 文件级变更面（实施时的 allowlist）

| 面 | 计划文件 | 不变量 |
| --- | --- | --- |
| PI context / decision | `agent_shell/trading_context.py`, `trading_decision.py`, `commands.py`, `reflection.py`, `pi_daemon.py` | 不直接下单；pending identity 和 Proposal/Risk 路径保持 |
| 证据与查询 | `analytics/filter_evidence.py`, 新的 skip diagnosis/evidence service, `storage/models.py`, `storage/repositories.py`, `storage/migrations.py` | append-only、同事务、无 prompt/secrets、固定 outcome |
| 运维 | `diagnostics/doctor.py`, `agent_shell/operator_loop.py`, 只读 API/report（如需） | 不因 diagnosis 改 config / 策略 |
| 契约 | `decision_lifecycle_contract.md`, `filter_evidence_contract.md`, `opportunity_outcome_contract.md`, `learning_contract.md`, data model/API/data utilization | 文档与 executable spec 同步 |
| 测试 | `tests/specs/`, `tests/test_filter_evidence.py`, `tests/test_operator_loop.py`, `tests/simulation/control_loop/` | RED 先行，DCLS 在孤立 DB 运行 |

禁止修改：`aletheia/risk/`、`aletheia/execution/`、交易所 auth/client、secrets、permissions、tool registry、
Live routes。若实施中发现这些路径必须动，停止并另行请求授权。

### 9.2 新增 executable claims（名称可在实现任务中定稿）

| Claim | 锁定的事实 |
| --- | --- |
| `SKIP-S01` | final candidate PI decision 必有 exact pending carrier；zero-pending 不创建 final |
| `SKIP-S02` | heartbeat 写 observed event，且不调用 LLM/不退休 Opportunity |
| `SKIP-S03` | direct LLM skip 也有 action assessment trace、稳定 reason taxonomy 和四 horizon seed |
| `SKIP-S04` | terminal claim、trace、outcome seed、queued→reviewed 同事务；任一失败完整回滚 |
| `SKIP-S05` | retry/并发 wake 幂等；第二次不能改写第一份 evidence 或加 outcome |
| `SKIP-S06` | aggregate health 不在 candidate PI decision input，也不能触发 operator config tightening |
| `SKIP-S07` | low WR 但不足样本/相关样本时只能是 insufficient，不能为 negative veto |
| `SKIP-S08` | family evidence 的市场、方向、成本、policy epoch 和时间 bucket 均隔离 |
| `SKIP-S09` | skip-halt state machine 对 no-flow、awaiting、recording、protective、suspect 可区分 |
| `SKIP-S10` | overrestriction finding 只生成 replay/shadow draft，零 Proposal/Risk/Execution 副作用 |
| `SKIP-S11` | B1/B2 版本、baseline、停止门和 policy epoch 可从报告复现，旧样本不混入 |

### 9.3 测试层次与命令

1. **契约 RED**：先向 `tests/specs/spec_decision_lifecycle.py` 和
   `tests/specs/spec_filter_evidence.py` 添加上述 claims，再让当前实现失败。
2. **repository/transaction**：临时 SQLite 测试强制每个 insert 点失败，断言整体 rollback。
3. **PI integration**：stub LLM 输出 direct skip、debate downgrade、zero pending、identity mismatch、
   price unavailable、retry/concurrency；证明没有匿名 final。
4. **diagnosis**：构造 complete protective、positive suspect、thin/correlated、missing outcome 和
   upstream supply fixtures；断言 finding 及其行为权限。
5. **DCLS**：扩展 `tests/simulation/control_loop/`，固定 virtual clock 和随机种子，验证低质量
   指标不会导致无证据全局 halt，且不产生自动配置 mutation 或交易副作用。
6. **回归**：

```sh
uv run pytest tests/specs/spec_decision_lifecycle.py -q
uv run pytest tests/specs/spec_filter_evidence.py -q
uv run pytest tests/test_filter_evidence.py tests/test_operator_loop.py -q
uv run pytest tests/simulation/control_loop/ -q
uv run aletheia doctor
uv run aletheia agent workflow check --strict
```

如增加 API，再执行对应路由测试与直接 backend latency 测量；如 migration 改动，验证旧 DB 升级、
新 DB 初始化、`PRAGMA integrity_check` 和 migration 幂等性。通过测试不代替 B epoch 的真实观察证据。

## 10. 分阶段完成审核

| 阶段 | 必须证明 | 不足时的结论 |
| --- | --- | --- |
| P0 | G1–G6 已记录；旧实验关闭或确认无活动窗口；RED claim 已提交 | 不进入行为实现 |
| P1 | S01–S05、事务失败、legacy 处理、doctor report 通过；无 PI 行为 diff | 只修 A 类，继续诊断 |
| B1 启动 | baseline commit/config/epoch/预注册指标与停止门完整；Risk/Execution diff 为零 | 不启用 B1 |
| B1 观察 | 候选输出、覆盖、队列、结果按 epoch 可复现；无 aggregate veto | 结论为证据不足或关闭 B1 |
| B2 申请 | B1 diagnosis 指向 family evidence，shadow 合格，owner 单独批准 | 不接入 family evidence |
| 任意完成 | 每个 WP 的 claim、测试、workflow evidence、handoff 与契约都在同一 HEAD | 不宣称“Skip 已修复” |

最终成功不是“恢复了开仓数”，而是系统能够在低表现时给出候选级、可验证的解释与下一步研究，
同时仍然不越过 Risk 和执行安全边界。
