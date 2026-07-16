# B2 Debate 证据对齐 SDD

> 状态：**已授权实施；B2 policy epoch 的观察尚未成熟。**
> 日期：2026-07-15
> 前置：B1.1（Ranker `medium` 供给地板与 PI durable pending queue rearm）已实施；B2
> 只能作为新的 B 类 policy epoch 申请，不能与 B1.1 观察窗口混合。

## 1. 要解决的具体失配

经代码路径审计，当前运行时的直接来源不是 `filter_candidate_outcomes`，而是
`repositories.family_direction_edge()`：它从 `universe_forward_outcomes` 取近 7 天已结算的 24h
净结果，按 `scanner_family + direction + normalized market_type` 聚合。它未按 Ranker tier、
score bucket、evidence/policy epoch、outcome coverage、独立样本或 regime 隔离。它回答的是
“近期该策略族来料池怎样”，而不是“眼前这个已经通过 Ranker 的候选，在可比条件下表现怎样”。

该 aggregate 被加入 Layer 1 的 Open Opportunities 行，并由初始 PI assessment 消费；其格式为
`edge:nT WR avg_net`。当前 Bull/Bear prompt 只消费候选技术特征和 regime，并不直接消费该
`family_edge` 或 Ranker score。因此，负历史数值先影响初始 assessment，再由 Debate 将 conviction
调整至 55 以下；不应误写为 Bear prompt 对 `filter_candidate_outcomes` 的直接查询。

`filter_candidate_outcomes` 是独立的筛选候选固定结果档案。它与 `universe_forward_outcomes` 的总体
样本、时间范围和聚合语义不同；两张表的数值不能互相替代或用来验证 Prompt 是否拼错。

因此，负的全量均值可以先被初始 assessment 当作当前候选的负向 alpha，而后 Debate 的固有
opposing-role 评分再造成结构性负 adjustment。Bull/Bear 都缺少同一份候选条件化、可审计的证据对象。

这不是降低 conviction 门槛或强制开仓的问题。正确的修复是约束**证据语义与来源**；Debate、
candidate-specific 判断和 Risk/Execution 的硬边界仍保留。

`shadow_trades.resolved=0` 本身不构成 B2 的触发证据。固定结果的 24h horizon 尚未成熟时，
未结算是预期状态；不得为了“稀释负样本”提前结算、伪造结果或让未成熟样本进入 Debate。

## 2. B2 的治理决定

### 2.1 运行时只能消费 candidate-conditioned evidence

每个待辩论候选先构造一个版本化的 `b2_evidence` summary。其 cohort
identity 至少包含：

```text
policy_epoch（同一 B2 epoch；不回用 B1 或重载前 aggregate）
scanner_family
direction
normalized market_type
rank eligibility tier（已持久化 `signal_enqueue=passed`）/ score bucket（`<40`、`40–<50`、`>=50`）
outcome horizon
cost model version
```

`regime_tag` 只有在样本规模允许时才可加入；不得为了得到看似有利或不利的结果无限切片。候选的
rank tier/score bucket 必须来自 Ranker 当次已持久化的事实，不能由 Debate 事后推测。

summary 必须同时给出 `n_total`、`n_net`、outcome coverage、独立 candidate 数、成本模型版本、
成本后 `avg_net` 与零均值收缩期望；字段、收缩先验与样本资格复用
[filter evidence contract](../../contracts/filter_evidence_contract.md)，不得另造一套不兼容的“胜率”。

### 2.2 四种证据状态

| 状态 | 语义 | Debate 权限 |
| --- | --- | --- |
| `qualified_positive` | 可比 cohort 成熟且覆盖合格，收缩后期望为正 | 双方可如实引用，不自动 `trade` |
| `qualified_negative` | 可比 cohort 成熟且覆盖合格，收缩后期望为负 | 双方可如实引用，不自动 `skip` |
| `insufficient_evidence` | 样本、覆盖或独立性不足 | 中性未知；不得计入 Bear 分数或降低 conviction |
| `data_quality_incident` | identity、成本、epoch 或结果完整性异常 | 只输出可审计诊断；不伪造方向性证据 |

初始 assessment、Bull 与 Bear 必须拿到同一份序列化 summary 和同一状态。不允许任一方直接查询
全量 aggregate，也不允许将 `insufficient_evidence` 表述为“历史表现差”。

### 2.3 全量聚合的保留用途

全量 `universe_forward_outcomes` 和 `filter_candidate_outcomes` 都是重要研究档案：可用于诊断
scanner 来料、Ranker 的选择增益、universe/filter 的反事实研究，以及事后报告。它们不再是 PI
assessment 或 Debate 对单一 top-ranked 候选的直接方向性证据。

`family_direction_edge()` 还被 Risk 的独立 live-eligibility 检查消费。B2 只替换 System
Operator 的 evidence provider；不得改动该 Risk 调用、Risk 门、Execution 或其统计语义。

## 3. B2 的最小行为合同

B2 若获批，唯一 treatment 是：

```text
raw family edge / WR / avg-net prose
  -> versioned candidate-conditioned evidence status + summary
```

它不改变 Ranker、Universe、候选 conviction 基线、sizing、direction-overheated brake、Risk、
Execution、Live 或权限。`qualified_negative` 仍可让 Debate 提出反方理由，但不能变成新的全局 veto。
`insufficient_evidence` 和 `data_quality_incident` 都不授予自动开仓权限。

## 4. 实施前必须预登记的选择

1. rank eligibility tier / score bucket 的精确定义及其 `filter_version`；
2. 各 horizon 的成熟条件、最小独立样本和最小 outcome coverage；
3. 收缩先验、recency window 与 cost model version；
4. `qualified_negative` 可以影响 Debate 的最大方式；
5. baseline、观察长度、停止门、回滚版本和报告责任人。

禁止用“尽快恢复开仓数”来反推这些参数。proposal 数量是 liveness 指标，不是 B2 成功标准。

## 5. RED-first 验收与观察

实现任务必须先证明：

1. 任一 full-pool 统计为负、但可比 cohort 不足时，初始 assessment 与 Bear 都只能收到
   `insufficient_evidence`；
2. direction、venue/market、epoch、成本版本或 rank bucket 不匹配的行不得进入 cohort；
3. 初始 assessment、Bull 与 Bear 收到 byte-equivalent 的 evidence summary；
4. 相关重复、未成熟或未覆盖结果不能虚增 `n_net` 或成为负证据；
5. 该 summary 不能绕开 candidate identity、Proposal、Risk 或执行因果链；
6. full-pool analytics/report 查询仍可用，但不会被运行时 System Operator evidence provider 调用；
   Risk 的既有 `family_direction_edge()` 调用不在 B2 diff 中。

B2 观察报告按新 epoch 单独给出：候选数与队列年龄、初始/最终 action、Debate adjustment 分布、
evidence status 覆盖、每个状态的成熟 24h 净结果和 coverage，以及 proposal→Risk 因果链。
没有成熟结果时结论只能是“未决”，不是“修复有效”。

## 6. 明确非目标

- 不强制 proposal、不开仓保底、不修改 conviction threshold；
- 不提前或伪造 shadow/fixed-horizon 结算；
- 不删除历史 lesson、review 或全量 outcome；
- 不修改 Risk Engine、Execution Gateway、交易所、Live、额度、凭据或权限；
- 不在本专项实现 Universe/Ranker 的反事实调参。

## 7. 进入实现的门槛

B2 实施已经由 owner 授权。仍须满足：B1.1 的队列消费与 terminal evidence 稳定；实现任务包含
独立 spec、policy epoch、停止门和同 HEAD 的验证证据。新 epoch 的第一批 24h outcome 未成熟前，
只能输出 `insufficient_evidence`，不得借用重载前或全量 aggregate 补足样本。
