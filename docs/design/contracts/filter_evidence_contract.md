# Opportunity 筛选证据与负反馈契约

> 状态：Stage E 证据基础已实施；交易行为调参仍关闭。
> 目标：让每个筛选关卡的放行、拒绝和后续净结果可被真实比较，而不是用整个策略族的总收益猜测某个关卡应该放宽还是收紧。
> 不变边界：本契约不自动改参数、不改风控上限、不启用实盘、不绕过 Execution Gateway。

## 1. 为什么不能只记“最后被拒绝”

Opportunity 主链上的“过滤”实际包含四种不同问题：

1. **市场能力门**：交易所是否支持、数据是否新鲜、点差和成交额是否可执行。这些门不是 alpha 预测器。
2. **方向假设门**：scanner、Observer 或 System Operator 是否给出做多/做空假设。没有事前方向就没有可归因净收益。
3. **质量/排序门**：置信度、TTL、factor、guard、批次上限和冷却时间。这些才是主要负反馈对象。
4. **安全/执行门**：风控、实盘锁、交易所精度和下单失败。这些只能报告，不允许因“错过收益”自动放宽。

只保存终点拒绝会丢失前面已放行的关卡；只保存放行会没有对照组。因此必须记录一个候选在当次评估中实际经过的每一关，到首个拒绝为止。

## 2. 候选身份与分叉

### 2.1 `candidate_key`

`candidate_key` 表示“在某一决策时点的一个交易假设”，不是纯 symbol：

- Universe 选币：`universe_member:<member_id>`。
- Universe 无方向前置门：以 `scanner_run_id + asset_id/feature_snapshot_id`
  在 `filter_decision_batches` 中保存，不伪造交易方向。
- Observer 单次评估：`observer:<run_id>:<venue>:<symbol>:<market_type>`。
- Ranker 单次机会：`opportunity:<opportunity_id>`。
- System Operator：`pi_decision:<decision_id>`。
- Risk：`proposal:<proposal_id>`。

同一 symbol 的现货和永续合约必须是不同候选。新一次 Observer 评估也是新候选，因为它的入场时间和行情已经改变。

### 2.2 必备身份字段

每条筛选记录必须尽可能携带：

- `candidate_kind` / `candidate_key` / `parent_candidate_key`
- `run_id` / `opportunity_id` / `universe_snapshot_id` / `universe_member_id` / `proposal_id`
- `venue` / `symbol` / `market_type`
- `direction` / `scanner_family` / `regime_tag`
- `evaluated_at`

`direction` 允许为 NULL，但 NULL 意味着它只能衡量“市场后来是否有大幅波动”，不能衡量可赚取的净 alpha。

## 3. 不可变筛选记录

`filter_decisions` 每行表示某一候选在某一关的一次事前判断：

- `filter_name` / `stage` / `sequence_no`
- `decision`: `passed | rejected | not_applicable | error`
- `reason_code`：稳定、不带动态金额的机器码
- `reason_detail`：用于人工排查的简短说明
- `input_snapshot`：该关真正消费的最小输入，不保存整段 prompt/context
- `parameter_snapshot`：当时门槛和开关
- `filter_version`：由 schema 版本 + filter 名 + 参数快照的规范 JSON 计算
- `code_commit`：Gateway 启动时代码提交，不在热路径 shell out
- `outcome_candidate_key`：可与同一事前时点的净结果相连

`passed` 只表示该关真实执行且未阻断。以 `guard_candidate_block` 为例：Learning/Guard
关闭时必须写 `not_applicable`，配置或运行异常必须写 `error`，不得把“未评估”冒充为
`passed/guard_not_blocking`。

对有精确 `signal_pending` 身份的终局 System Operator `skip`，PiDecision、其本轮
`filter_trace` 与可识别候选的固定 horizon outcome seed 必须在同一 SQLite 提交边界中写入。
direct model `skip` 不能因未进入 debate/conviction 分支而缺少 `operator_action_assessment`。
在 `system_operator_skip_remediation/b1.2` 中，该 assessment 只有经过一次有界反事实确认，且
`skip_kind=adverse_candidate_evidence`、typed adverse reason 与候选特定 evidence 均非空时才可
成为 terminal reject。`insufficient_evidence`、rank priority 单值、unsupported action 或 malformed
conviction 都不是拒绝事实；二次仍无效时只写去重的
`operator_decision_deferred_unchanged_context` observation，保持 pending 且不生成 outcome cohort。
任一必需 evidence 写入失败时，整个 final claim 回滚，pending 保持可重试；不得留下 `reviewed`
Opportunity 或无证据的 skip。若决策时没有完整方向、市场、venue 或精确入场价，可保留非收益诊断，
但禁止伪造 outcome 或事后补入场价。对于有精确 queued Opportunity 身份的 skip，若
决策时实时快照已过期，允许且只允许复用该 Opportunity 在入队时已完整登记的四个
固定 horizon outcome cohort（相同 venue、symbol、market、direction、entry price 与
entry timestamp）；trace 必须标注 `queued_candidate_fixed`。不完整或身份不一致的 cohort
仍必须回滚，不能以旧快照、别的市场或新造价格代替。

`doctor` 必须只读报告近期 candidate skip 的 action-trace 覆盖、四个 outcome seed 覆盖、legacy 无 target
skip、`operator_evaluation_events` 的 no-candidate heartbeat/unchanged-context defer、pending 与 proposal 计数。若存在活跃的
`system_operator_skip_remediation` policy epoch，raw 24h 计数必须与该 epoch 的诊断计数同时展示：
raw 路径还必须分列 direct model skip、trade-intent 后门控 skip、经过 b1.2 确认的 adverse skip；
重载前的缺口仍可审计，但不得污染当前 epoch 的 finding。无 target 记录
不得混入 candidate effectiveness cohort；coverage 缺失是 `recording_incident` 的诊断输入，不授予自动
放行、自动调参或执行权限。新 zero-pending heartbeat 只能写 observation event，不能再写无 target skip。
`skip_halt` 报告必须同时给出版本化 finding：`not_applicable`、`awaiting_outcomes`、
`recording_incident`、`protective_skip_observed`、`overrestriction_suspect`、
`operator_liveness_incident` 或 `diagnosis_unavailable`。后两种 suspect/liveness 输出至多推荐
不可执行 replay/shadow 草案；它们不是 Proposal、Risk 或配置 mutation 的权限。
`skip-halt-v2` 将窗口明确分成两层：最近 24h candidate 用于立即检查 identity/trace/四个 seed
完整性和当前流量；效果判断只使用其前一段、已经跨过 24h primary horizon 的固定 cohort。
最近尚未成熟的 skip 必须显示为 `immature_candidate_skips`，但不得阻塞成熟 cohort 得出
protective/suspect finding；成熟 cohort 自身仍有 overdue outcome 时才返回 `awaiting_outcomes`。
`signal_source=system_cascade` 的生命周期清理行不是 Operator 对候选的模型判断，必须单独报告为
administrative exclusion，不得进入 candidate skip、trace/seed 缺口或效果 cohort。

生产者在拒绝输入时，该原因是唯一解释事实：Observer、Universe emitter、Divergence scanner
若无法写入终态记录，必须返回可重试错误，不能以“零 Opportunity、零原因”的成功结束。
生产者放行并新建 Opportunity 时也必须留下精确的 `opportunity_emitted` 事实；Universe emitter
和 Market Observer 都把新 Opportunity 与该事实放在同一回滚边界内，证据失败不得留下孤儿
Opportunity，也不得增加本轮成功创建数。Observer 的 pre-filter 事实只说明“曾评估/曾放行”，
不能替代真正创建 Opportunity 的 producer 事实。Universe emitter 的该类失败不是可忽略分析错误：
当前没有 snapshot/member 级独立重试队列，因此 `UniverseService.refresh` 必须把 scanner run 标为
failed 并返回失败，禁止继续发布 completed。
Opportunity 的 open/queued TTL 过期也必须与 `ttl_expired` 记录同事务提交；证据失败则保留原状态。
这条规则同样覆盖 System Operator 的两次权限重查：若 Opportunity 在 LLM 评估期间或
Proposal preflight 之后才越过 `valid_until`，preflight/claim 必须共用同一个仓储 CAS，写且只写一条
`opportunity_ttl / ttl_expired` 事实；证据失败时不得单独提交 `expired`、最终 PiDecision 或 TradeProposal。

Ranker 层的 `family_ttl` 关卡走**软过滤**语义：`valid_until` 到期后不再立即
硬拒，而是按 `trade_score *= 0.5 ** (expired_sec / family_ttl_sec)` 衰减（半衰期
= 家族 TTL，见 `allocator._FAMILY_TTL`），并在 `filter_trace` 里写入
`passed_with_penalty` + `opportunity_ttl_soft_penalty` /
`universe_member_ttl_soft_penalty` 事实。只有当过期时间超过
`_TTL_SOFT_HARD_CAP_MULT × ttl_sec`（默认 3 倍家族 TTL）后 Ranker 才回退到
硬拒 `skip_reason=ttl_expired`，交由 `paper_trader._settle_permanent_skips`
写入终态 `ttl_expired` 证据。软过滤路径的 `passed_with_penalty` 事实只是
分数决策的证据，不是候选被终结的终态——因此它不会导致 opportunities.status
从 open 迁出，也不参与 `filter_candidate_outcomes` 的终态归因。硬拒才终结。
无法解析的 TTL timestamp 不得按新鲜载体 fail-open；必须写
`family_ttl=error / ttl_timestamp_invalid`，以 `ttl_invalid` 收口为 reviewed，等待上游
数据修复产生新 identity。超过硬顶的 Opportunity/member 统一使用 `ttl_expired` reason code，
具体来源放在 parameter snapshot 中，避免同一终态出现多个不兼容机器码。

`UniverseService.refresh` 的 change-suppression 分支（fingerprint 未变且
`_SNAPSHOT_HEARTBEAT_SEC` 未到）在复用旧 snapshot 时，必须调用
`repositories.refresh_active_snapshot_ttls` **前滑**该 snapshot 的
`universe_members.expires_at` 和其 open `opportunities.valid_until`（
`WHERE expires_at < new_expires`，只前滑不回退）。这是把 "reuse snapshot"
真正做成 no-op 的必要一步：没有这条前滑，第一次分配后 30–60 分钟内 fingerprint
稳定的家族全部会在 Ranker 扫时集体 `ttl_expired`，与"依然被推荐"这个复用
判定相矛盾。前滑写属于 idempotent 前进操作，允许非事务性失败（降级为软过滤
兜底），但成功时必须在日志留下 `members=N opps=M` 计数便于回放。
Change-suppression fingerprint 必须覆盖会改变载体语义的字段：venue、market、rank、
scanner family/direction、execution eligibility、family TTL、risk flags 与精确入场证据价；
只允许排除不会改变这些语义的 ranking score 抖动。任一字段变化必须发布新 snapshot，
不得前滑旧方向、旧资格或旧 outcome seed 的 TTL。

snapshot 被 supersede 时，其 `open` 与 `queued` Opportunity 都必须在同一事务中转为
`expired` 并写 `universe_snapshot_status / universe_snapshot_superseded`。已有
`reviewed/expired` 终态不得改写。证据失败时整个 cascade 回滚；重复运行不得生成重复事实。
对应的 market-observer `signal_pending` carrier 可保留作历史，但来源已 `expired` 且没有
`proposal_status=submitting` 恢复 claim 时，Operator 工作集必须立即排除它。

业务写入只允许 INSERT，不回写历史判断。幂等键至少包含
`candidate_key + filter_name + decision + filter_version`，重试不制造重复证据，
但同一候选先被批次上限拒绝、后来真实放行时可分别保留两条事实。
每日数据治理允许整行删除超过保留期的分析证据，这不等于改写决策；
权威 `RiskDecision`、proposal、order、fill、position 和 audit 记录不在此清理范围。

API 密钥、Authorization、原始 LLM prompt/context 和新闻全文禁止写入。

### 3.1 B2 System Operator candidate evidence

System Operator 的 B2 debate evidence 不是全量策略族胜率。它只可由同一
`system_operator_b2_candidate_evidence` policy epoch 中、已写入
`signal_enqueue=passed` 的候选终态 `operator_action_assessment`，按
`scanner_family + direction + normalized market_type + score bucket + 24h` 聚合。
summary 的最小 trace（epoch、tier、bucket、样本/coverage、成本模型和状态）写入该 assessment
的 `input_snapshot`，不保存 prompt。样本少于 20、coverage 低于 70% 或身份不完整时必须为
`insufficient_evidence`：中性未知，不得被叙述为负向 alpha 或自动降低 conviction；成本模型不一致
则为 `data_quality_incident`。同一序列化 summary 必须输入 assessment、Bull 与 Bear。该行为不
改变 Ranker、Risk 的独立 `family_direction_edge()`、Execution 或 Live 权限。

PiDecision 的 `evidence_manifest_json.operator_rationale` 必须完整保存 `skip_kind`、adverse reason/evidence、
supporting/opposing signals 与 risk factors，权威 `reasoning` 保存全文。短 reflection 或 UI 可以截断显示，
但不得把 200/300 字 convenience cap 当作耐久审计事实。

对 registry/ticker/tradability 这类高频、无方向关卡，允许用
`filter_decision_batches` 压缩表达：每个 scanner run + filter version 一行，
`candidate_facts` JSON 内保留该关实际评估的每个候选键、标的身份、
pass/reject 和最小输入。这是存储压缩，不是抽样；候选数和各类决定数
必须与 JSON 事实一致。有方向、可结算的关卡仍必须使用逐候选
`filter_decisions`，不允许批次聚合后丢失 outcome 连接。
为避免 10 分钟批次重复存储每个 JSON 字段名，当前编码为
`{"fields":[...],"rows":[[presence_mask,...values]]}` 的无损行矩阵，再以
`zlib-base64:` 自描述前缀压缩为 SQLite TEXT。`decode_filter_decision_batch()`
同时支持当前压缩编码、未压缩行矩阵和旧的 plain-list JSON；
压缩不得改变候选数、字段值或缺失字段语义。

## 4. 过滤候选的固定时点结果

`filter_candidate_outcomes` 以 `outcome_candidate_key + horizon` 唯一，固定周期为 `15m / 1h / 4h / 24h`。经济公式、时点行情质量和现货/合约隔离完全复用 `opportunity_outcome_contract.md`。

只有同时满足以下条件才创建可学习结果：

1. 事前已有 `long` 或 `short`，禁止事后选更赚的方向。
2. `venue + symbol + market_type` 完整。
3. 入场价大于 0，且是该市场在决策时点可见的价格。
4. 永续合约缺入场资金费时可保存 gross/directional，但 net 必须为 NULL。

同一候选经过多个关卡时只结算一份结果，避免把同一个价格路径当成多个独立样本。

## 5. 现行关卡与证据等级

| 关卡 | 当时是否有方向 | 要记录的事实 | 可否用于净收益负反馈 |
|---|---:|---|---:|
| registry/交易所能力 | 否 | 支持/不支持、数据缺失 | 否，只报能力覆盖 |
| ticker 成交额预筛 | 否 | volume、threshold、market | 否，只报可执行性/波动遗漏 |
| tradability（稳定币/黑名单/volume/spread/freshness） | 否 | 按顺序记每关放行到首个拒绝 | 否，黑名单永不自动调参 |
| scanner 假设 | 是 | family、direction、各 family score | 是 |
| quota/explore-exploit | 是 | selected/control、rank、quota、regime | 是，但必须同池比较 |
| Universe 机会生成 | 可能否 | 每个已选成员最终是已生成、复用、拒绝或写入错误 | 否，只衡量覆盖和身份完整性 |
| Observer 前置判断 | Universe 路径是；其他路径可能否 | RSI/ATR/keyword 实际值和命中原因 | 仅有事前方向的行 |
| Observer 机会生成 | 是 | 新 Opportunity 的精确 id、方向、市场与 `opportunity_emitted`；和 Opportunity 原子提交 | 否，只衡量生产链完整性 |
| LLM neutral/无效输出 | 可能否 | schema 结果、方向、置信度，不写 prompt | 仅有事前方向的行 |
| Ranker symbol policy/execution/direction/confidence/TTL/factor/guard | 是 | 白/黑名单拒绝也必须写 `symbol_filter/symbol_not_allowed` 并终结 Opportunity；永久拒绝扫描批次与 top-N 执行短名单分离，状态与拒绝事实同事务提交 | 是（guard 只观测；symbol policy 不自动放宽） |
| open-position/cooldown/batch limit | 是 | 当时占用、冷却截止、rank/limit | 是 |
| System Operator action/conviction/debate/direction brakes | 是 | 仅活跃 `trade/skip`、原始/辩论后 conviction、实际暴露快照；direct model `skip` 有一次确认、typed adverse evidence 与 `operator_action_assessment`；遗留假动作是 retryable unavailable | 是，但不在有对照前改门槛 |
| Proposal submission 方向/取价 | 是 | 方向集中度，最终 market_type，目标市场价格是否可用 | 是；用于解释 trade 意图为何没有 proposal，不自动放宽 |
| Risk | 是 | `RiskDecision` 为权威，filter 记录只做统一索引 | 报告用，永不自动放宽 |
| Execution adapter | 是 | order/error/venue reason | 用于成交率/实现率，不是方向 alpha |

`XAUT/PAXG` 是价格会波动的黄金 RWA，不是美元稳定币，因此不得在
`tradability_stablecoin` 中提前排除。它们仍必须通过交易所 ticker、成交额、
点差、新鲜度、后续风控和执行能力检查；分类修正不等于授权实盘。

## 6. 负反馈统计契约

统计单元至少为：

`filter_name + stage + filter_version + scanner_family + direction + market_type + horizon`

`stage` 是关卡身份的一部分。同名过滤器如果分别发生在 System Operator 和
proposal submission，必须分别留痕、分别统计；幂等键至少包含
`candidate_key + filter_name + stage + sequence + decision + filter_version`。
控制器展示的当前值优先使用证据行自己的 `parameter_snapshot`，不能用注册表中的
概括值覆盖历史上真正执行过的参数。

regime 样本足够后再增加 `regime_tag`，禁止小样本无限切片。

学习目标不是胜率，而是净期望。每个 pass/reject cell 保存：

- `n_total / n_net / outcome_coverage`
- `avg_net / median_net / profit_factor / MFE / MAE`
- 按同 cohort 总体均值收缩后的 `shrunk_avg_net`
- 相关重复样本按 `outcome_candidate_key` 去重，不按关卡行数数多次

MVP 收缩公式：

`shrunk_mean = (n * sample_mean + k * cohort_prior_mean) / (n + k)`，默认 `k=20`。

一个关卡只有在以下条件全部满足时才可以输出 `overrestrictive_suspect`：

1. pass/reject 两边都至少 20 个完整 net 样本；
2. 两边 outcome coverage 都不低于 70%；
3. 只使用一个 `filter_version`；
4. reject 的收缩净期望高于 pass 至少 5bp，且 reject 自身大于 0；
5. 关卡不是 risk/live/credential/exchange-capability 硬门。

观察数据不是随机实验，因此结论只能是 `suspect`，不能直接改配置。自动收紧也不能只因 pass 亏损就触发；它需要门槛附近的反事实或独立回放。

同一候选可能跨调度轮次重复经过同一关卡。统计必须按
`filter_name + stage + filter_version + outcome_candidate_key` 选择最早一次持久化决定，
把候选唯一归入 first-exposure pass 或 reject cohort；后续重试只用于轨迹排障，不再次贡献
收益样本。时间窗在首次暴露排序完成后应用，禁止把窗口外首次放行遗忘后，将窗口内后续拒绝
冒充首次决定。

对达到嫌疑门槛的可重放单调阈值，先执行固定版本、有界、只读的 gate-only replay。回放必须
显示新增放行样本、净结果覆盖、原始和收缩净期望及未覆盖的下游关卡；任何 `ready` 只允许
进入独立 shadow 实验，不得直接写配置。具体边界见 `docs/design/market-systems/filter_replay.md`。

独立 shadow 实验必须在回放后预登记固定 version/cohort/control/treatment/停止门槛，从登记
时刻之后的新 first-exposure 样本开始，禁止复用挑选阈值的历史样本做验证。control 与新增
shadow treatment 各自达到 20 个完整净结果和 70% 覆盖后才判定；生产 filter version 变化则
实验失效。同一个固定 version/stage/family/direction/market/horizon 只能登记一个 treatment，
后续回放波动不得并行追逐另一个阈值。`ready_for_review` 只表示可人工复核，任何状态都不得写
生产配置或改变候选放行。

## 7. 批次选择的特别规则

`open_position -> cooldown -> batch_limit` 是三个不同关卡，不得先截取 Top-3 再检查前两关。否则 Top-1 被冷却拒绝后，Top-4 永远没有补位机会，候选供给会被静默浪费。

正确顺序：

1. 按分数从高到低遍历；
2. 同一 `opportunity_id` 已有最终 Operator 决策则记 `operator_already_evaluated=rejected`，不重复入队；
3. 已持仓则记 `open_position_duplicate=rejected`，继续看下一个；
4. 冷却中则记 `signal_cooldown=rejected`，继续看下一个；
5. 未满 limit 则放行；已满后其余候选记 `signal_batch_limit=rejected`。

生产端 admission 还受一个独立的 unresolved queue capacity（当前 5）约束。容量在
open-position/cooldown 后、单轮 batch limit 前检查；满载候选写
`signal_queue_capacity=rejected` 并保持 Opportunity `open`，让后续轮次重试，不得提前
`queued` 后等待 30 分钟超时。旧 market-observer pending carrier 只可在来源已经
`reviewed/expired` 且不存在 `proposal_status=submitting` 恢复 claim 时删除；queued、Serenity
或 submitting carrier 必须保留。

Shadow 批次的 `was_selected` 必须对应真正写入 `signal_pending` 的候选，不能在冷却/持仓检查之前就标记“已选中”。
`signal_batch_limit=passed` 只代表被选中；真正写入队列另记
`signal_enqueue=passed|rejected|error`。`signal_pending` 与 Opportunity 的 `queued` 状态必须同一
事务提交，任一步失败都回滚，不得留下“已入队但来源仍 open”的孤儿状态。
真实入队异常还必须在本轮返回的 `errors` 中写入稳定的
`signal_enqueue_failed:<opportunity_id>`；即使整批一个都没写入，也不能只返回
`no_signals_written`，把故障伪装成健康的空批次。
入队时只允许条件迁移 `open→queued`;批次计划过期、来源已是 `reviewed/expired` 时记录
`signal_enqueue=rejected / opportunity_not_open`,不得写 pending,更不得把终态倒退。

Ranker 的 `limit=50` 只限制进入打分/入队选择的执行短名单，不限制本次有界扫描中必须收口的
永久拒绝。`open` 工作集按 oldest-first 推进，单次扫描仍有明确上限；因此旧拒绝不会在持续新流量下
饿死。永久拒绝必须先确认对应 rejected `filter_decisions` 已在同一事务内可见，再把 Opportunity 改成
`reviewed/expired`；任何证据写失败都回滚状态并返回稳定错误，留待下轮重试。

## 8. 失败与运维语义

- 一般观察证据写入失败不能改变原交易决定，但必须记 warning/doctor coverage，不得沉默当成完整证据。
  若某条证据正是 Opportunity 进入永久终态的唯一原因，则证据与状态必须同成同败；失败时保留 `open`。
- outcome 取价失败保持 `failed`/NULL，不用同名现货或过晚价格补齐。
- `doctor` 分开报告：记录覆盖率、可结算比例、到期未结算数、各 filter 是否达到最小对照样本。
- `outcome_settler` 每 15 分钟运行；刚到期的 filter outcome 在 20 分钟宽限内标为
  `awaiting_cadence`，不算故障。超过宽限的 `stale_overdue` 才参与积压告警，口径与主 forward
  outcome 诊断一致。
- `doctor` 同时校验批次里的候选总数是否等于 pass/reject/not-applicable/error 之和。
- Trade Funnel 优先从 `filter_decisions` 读取窗口内 ranker 历史候选，不在审计时重跑 ranker；
  `proposal_submission` 拒绝原因必须单列，不能统称为未知的 decision-to-proposal 丢失。
- `filter_decision_batches` 保留 30 天；`filter_decisions` 和
  `filter_candidate_outcomes` 保留 90 天。它们是分析证据，不是交易因果原件。

## 9. Stage E 实施状态

1. 已完成：新表、model、repository、只追加/幂等写入与有界保留。
2. 已完成：`UniverseMember.scanner_direction` 持久化，Observer 和 Ranker 顺序轨迹。
3. 已完成：open-position/cooldown/batch-limit 补位顺序与 shadow 真实 selected 语义。
4. 已完成：固定时点净结果，shadow/control 新行的 instrument 和成本迁移。
5. 已完成：Universe ticker/tradability 批次证据、allocation 对照、System Operator 和 Risk 统一索引。

6. 已完成：只读 filter feedback 使用分层收缩净期望，未达样本门只显示证据不足。
7. 已完成：ranker 历史漏斗和 proposal submission 原因直接来自已落库证据；无目标市场价格返回稳定失败原因，不再静默 `None`。
8. 已完成：反馈统计按真实 stage 分组并展示事发参数；同名同参数关卡跨阶段不再因幂等键碰撞丢证据。
9. 已完成：可回放单调阈值的前瞻 shadow 登记、严格起始边界、固定停止门和版本失效保护；只观察，不自动应用。
10. 已完成：Universe 已选成员在机会生成阶段逐个保存终态；同一 snapshot、symbol、market 的已评估机会仍占有原身份，迟到的 Observer 不再重复创建或事后改写。
11. 待独立实验：合并方向刹车、修改 ranker 权重/经济门和任何自动调参。
12. 已完成：Ranker 白名单漏项与黑名单命中不再静默 `return None`；拒绝事实与终态原子提交，且独立有界拒绝工作集不会被 top-N 执行上限截掉。
13. 已完成：Universe emitter 与 Market Observer 新建 Opportunity 时同步原子写入 `opportunity_emitted`；Observer pre-filter 不再被误当成生产成功证明。
14. 已完成：direct skip 一次有界反事实确认、neutral/invalid defer、120 分钟物质上下文去重，以及完整 operator rationale 持久化；不改 55、Risk、Execution 或 Ranker 门。

## 10. 诊断时间窗口与 sizing 口径

- `opportunity audit --hours N --view controller` 必须按
  `filter_decisions.evaluated_at` 截断证据；交互式近期报告不得暗中回退成全历史。
  scheduler 的长期 shadow controller 不传窗口，继续使用全历史积累。
- Opportunity overview 同时报告窗口内数据库总数、实际重建数和过滤后显示数。
  `--limit` 只是重建成本上限，不得冒充窗口样本量。
- sizing trace 的历史计划值来自持久化 proposal notional，实际成交优先来自该
  proposal 的 fills。按当前配置反算的值只能标为 current-policy projection，不得
  描述成历史决策事实；position 仅是缺 fill 旧链路的 fallback。

## 11. 完成标准

- 相关关卡 pass/reject 都有真实事前记录，不从最终状态反推。
- 无方向候选不会生成可学习净收益。
- 拒绝候选和放行候选使用同一时点/成本模型结算。
- 反馈统计去重候选，按 family+direction+market+horizon+version 分层，优先收缩净期望。
- 未达样本/覆盖门或属于安全硬门时，不产生自动调参建议。
- 所有新证据写入不影响原决策返回值。本阶段明确的行为修正只有：
  批次补位顺序、shadow 真实 selected 标记，以及将 `XAUT/PAXG`
  从错误的美元稳定币分类中移出。
