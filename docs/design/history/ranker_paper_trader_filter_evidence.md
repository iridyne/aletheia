# Retired Ranker / PaperTrader filter-evidence rules

> 2026-10-09 迁入 history：原为 `docs/design/contracts/filter_evidence_contract.md` 的
> §7（旧 Ranker/PaperTrader 批次选择与 signal_pending 入队规则）、§9（Stage E 实施记录）
> 与 §11（当时的完成标准）。对应的选择器、入队与 Ranker 已随 #2945 / #3134 退役，
> 本页只用于解释存量证据。当前规则见
> [filter_evidence_contract.md](../contracts/filter_evidence_contract.md)。

## §7 历史批次选择规则（已退役）

本节记录旧 Ranker/PaperTrader 的批次与 signal_pending 原子入队语义，仅用于解释存量证据；
当前不再运行该选择器、enqueue、claim 或 pending 恢复。当前供给与阻塞只读 §3 的
workbench generation/Plan 投影，不把 signal_enqueue/was_selected 重标为 Candidate admission。

`open_position -> active_operator_carrier -> cooldown -> batch_limit` 是不同关卡，不得先截取 Top-3 再检查前置关卡。否则 Top-1 被占用/冷却拒绝后，Top-4 永远没有补位机会，候选供给会被静默浪费。

正确顺序：

1. 按分数从高到低遍历；
2. 同一 `opportunity_id` 已有最终 Operator 决策则记 `operator_already_evaluated=rejected`，不重复入队；
3. 已持仓则记 `open_position_duplicate=rejected`，继续看下一个；
4. 同一物理 `symbol + market + direction + scanner_family` 已有未完成 queued carrier，则记 `active_operator_carrier=rejected`，继续看下一个；它表示复用既有工作载体，不得伪装成 30min cooldown；spot 与 perp（及规范化别名）不可互相占用；
5. 冷却中则记 `signal_cooldown=rejected`，继续看下一个；
6. 未满 limit 则放行；已满后其余候选记 `signal_batch_limit=rejected`。

生产端 admission 还受一个独立的 unresolved queue capacity（当前 5）约束。容量在
open-position/cooldown 后、单轮 batch limit 前检查；满载候选写
`signal_queue_capacity=rejected` 并保持 Opportunity `open`，让后续轮次重试，不得提前
`queued` 后等待兼容性超时。旧 market-observer pending carrier 只可在来源已经
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

## §9 Stage E 历史实施记录

下列“已完成”描述当时实现，不声明旧 Ranker/Operator/队列仍可执行；当前入场以本文开头的切换边界及 §3 为准。

1. 已完成：新表、model、repository、只追加/幂等写入与有界保留。
2. 已完成：`UniverseMember.scanner_direction` 持久化，Observer 和 Ranker 顺序轨迹。
3. 已完成：open-position/cooldown/batch-limit 补位顺序与 shadow 真实 selected 语义。
4. 已完成：固定时点净结果，shadow/control 新行的 instrument 和成本迁移。
5. 已完成：Universe ticker/tradability 批次证据、allocation 对照、System Operator 和 Risk 统一索引。

6. 已完成：只读 filter feedback 使用分层收缩净期望，未达样本门只显示证据不足。
7. 已完成：ranker 历史漏斗和 proposal submission 原因直接来自已落库证据；无目标市场价格返回稳定失败原因，不再静默 `None`。
8. 已完成：反馈统计按真实 stage 分组并展示事发参数；同名同参数关卡跨阶段不再因幂等键碰撞丢证据。
9. 已完成：可回放单调阈值的前瞻 shadow 登记、严格起始边界、固定停止门和版本失效保护；只观察，不自动应用。`ranker_confidence_gate` 的版本失效保护自 #1072 起改为 adapter-declared causal identity vector — 无关 `filter_version` 变化不再重置 cohort，详见 [observation_lab_contract.md](../contracts/observation_lab_contract.md)。
10. 已完成：Universe 已选成员在机会生成阶段逐个保存终态；同一 snapshot、symbol、market 的已评估机会仍占有原身份，迟到的 Observer 不再重复创建或事后改写。
11. 待独立实验：合并方向刹车、修改 ranker 权重/经济门和任何自动调参。
12. 已完成：Ranker 白名单漏项与黑名单命中不再静默 `return None`；拒绝事实与终态原子提交，且独立有界拒绝工作集不会被 top-N 执行上限截掉。
13. 已完成：Universe emitter 新建 Opportunity 时同步原子写入 `opportunity_emitted`；Observer 只 enrich 已有且通过 exact emitter attestation 的 carrier，pre-filter/assessment 不再被误当成生产成功证明。
14. 已完成：direct skip 一次有界反事实确认、neutral/invalid defer、120 分钟物质上下文去重，以及完整 operator rationale 持久化；不改 55、Risk、Execution 或 Ranker 门。
15. 已完成：b1.3 单候选上下文、确定性 adverse fact validation、候选级 defer rotation、真实 B2 fingerprint、unknown 数值格式和 operator-candidate-v2 evidence manifest；不改 55、Ranker 权重、Risk、Execution、Exchange 或 Live。
16. 已完成：Overview/CLI 共用 durable Opportunity progress 投影，区分未评估、冷却、队列背压、批次上限与其他真实 Ranker blocker，并公开 on-demand wake 来源/结果；诊断不重跑 Ranker。
17. 已完成（`DL-S52..56`）：snapshot supersede 只终结 open analysis generations；queued carrier 由自身 `valid_until` 继续，active carrier 去重与 cooldown 分开留痕，global cleanup 不抢占 Ranker soft-TTL。
18. 已完成（`FE-S21` / `API-S09`）：Opportunity Trace 和 Funnel 从 typed terminal facts 区分 snapshot supersede、true TTL、Operator/deterministic review 与 unknown legacy remainder；raw `expired` 不再充当原因。

## §11 完成标准

- 相关关卡 pass/reject 都有真实事前记录，不从最终状态反推。
- 无方向候选不会生成可学习净收益。
- 拒绝候选和放行候选使用同一时点/成本模型结算。
- 反馈统计去重候选，按 family+direction+market+horizon+version 分层，优先收缩净期望。
- 未达样本/覆盖门或属于安全硬门时，不产生自动调参建议。
- 所有新证据写入不影响原决策返回值。本阶段明确的行为修正只有：
  批次补位顺序、shadow 真实 selected 标记，以及将 `XAUT/PAXG`
  从错误的美元稳定币分类中移出。

## §3 Ranker family_ttl soft penalty (excerpt)

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
