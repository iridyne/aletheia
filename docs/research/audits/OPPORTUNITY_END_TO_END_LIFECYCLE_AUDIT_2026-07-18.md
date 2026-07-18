# Opportunity 端到端生命周期审计（2026-07-18）

## 结论

“最近一小时 79 discovered / 69 expired / 0 proposed”不是 69 个机会都自然超时。
运行库核验的 69 条全部带
`universe_snapshot_status / universe_snapshot_superseded`，其中 49 条从 `open`
结束、20 条从 `queued` 结束；自然 `valid_until`、人工取消均为 0。它暴露的是一个
架构冲突：每约 10 分钟换代的 Universe 分析 snapshot 同时控制了声明可存活
30 分钟至 4 小时的 Operator 工作载体。

修复后的边界是：

- `open Opportunity` 是每个 snapshot 的分析代次；父 snapshot supersede 可以结束它。
- `queued Opportunity + signal_pending` 是已认领的 Operator 工作载体；父 snapshot
  id 继续作为不可变因果来源，但载体只由自身 `valid_until` 或最终决定结束。
- UI/API 同时报告 unique setup 与 snapshot generation；supersede 不再显示为真实 TTL
  失败。

本变更不调整 Ranker 分数、交易阈值、conviction、仓位、Risk、Execution、Exchange、
Live 或凭证逻辑。

## 全链路与所有出口

| 阶段 | 入口/权威数据 | 继续条件 | 非终局等待 | 终局出口 |
| --- | --- | --- | --- | --- |
| Universe/其他 producers | Universe member、Market/Prediction/Divergence/Equity observer；Serenity 使用独立表 | 产生带方向、market、family、validity 的 Opportunity | 无 | emission/filter 拒绝只留下证据；未生成 Opportunity |
| Opportunity `open` | 每 snapshot 分析代次 | Ranker deterministic gates 通过 | batch limit、queue capacity、cooldown、已有 active carrier 时保持 open 等新事实/下一轮 | 永久 Filter 拒绝→`reviewed`；Ranker hard TTL/global retention/snapshot supersede→`expired`；入队 CAS→`queued` |
| Ranker handoff | `open→queued` 与 `signal_pending`、enqueue evidence 同事务 | queue capacity 5、batch 3、无持仓/冷却/重复载体 | backpressure/cooldown/active carrier | 写入失败回滚，不能产生孤儿 queued/pending |
| System Operator queue | queued source + exact pending identity + candidate technical facts | source `valid_until` 未到、身份完整、上下文可用 | 技术事实缺失/不完整/陈旧、未验证 direct skip、未变化 context defer；载体不被消费 | `skip`/`trade` 最终 PiDecision 与 Opportunity `reviewed`；TTL→`expired` |
| Proposal | immutable prepared Proposal；claim 与 Proposal 原子落库 | System Operator `trade` 且 preflight/claim 再次通过 | provider/price 等不可用时 retryable，不写假终局 | Proposal 持久化后进入 Risk |
| Risk | Proposal 与完整因果 id | Risk approved | 无 | rejected；或 approved→Execution |
| Execution | approved Proposal | gateway/exchange adapter | pending/reconciliation | order/fill/position；或明确 execution failure |
| Outcome/Learning | proposal/order/position、shadow、forward outcomes | 完整归因与 settled evidence | pending outcome | eligible outcome 或明确不可用于学习 |

## 发现的系统性问题

### P0：snapshot cascade 销毁 queued 工作载体

旧实现对 superseded snapshot 的 `open` 和 `queued` 一起写 `expired`。实际 snapshot
间隔中位数约 10.08 分钟，而被终结记录仍剩余的 `valid_until` 中位数约 49.81 分钟，
最长约 231.27 分钟。因此 Operator carrier 的声明有效期从未成为权威。

修复：cascade 只处理 `open`；queued 保留因果来源并按自身有效期继续。原子 evidence
与回滚语义不变。

### P0：四套互相冲突的时钟

旧链路同时存在 snapshot 约 10 分钟、pending 固定 30 分钟、Opportunity 30 分钟至
4 小时、Operator defer 120 分钟。queue builder、queue capacity、Proposal preflight、
atomic claim 和 daemon liveness 使用的又不是同一个子集。

修复：市场 queued carrier 统一以 `Opportunity.valid_until` 为权威。固定 30 分钟只处理
没有可解析 deadline 的历史兼容行；queue read、capacity、expiry、preflight、claim、daemon
probe 使用同一语义。Serenity 保持独立长周期规则。

### P1：active carrier 在 30 分钟后失去去重所有权

旧 cooldown 只看 pending 创建时间。一个仍有效的 4 小时 carrier 在第 31 分钟后可能允许
同 setup 再入队。

修复：新增 `active_operator_carrier` report-only filter；同
`symbol + direction + scanner_family` 的未完成 carrier 持续拥有 setup。它与 30 分钟
cooldown 分开报告，避免把“已有工作载体”误判成 cooldown。

### P1：defer 显示和调度可能越过 source deadline

旧 120 分钟 defer 不考虑 Opportunity validity，UI 可能显示晚于真实死亡时间的 retry。

修复：`defer_until=min(context_window_end, valid_until)`，已有 defer 查询同时校验该
截止时间。若相同 active snapshot 明确认可并前滑 queued validity，过期的 defer 可以重新评估。

### P1：daily cleanup 抢走 Ranker soft-TTL 权限

Ranker 规定 `valid_until` 后按 family TTL 半衰减，3× TTL 才 hard reject；daily cleanup
却在刚过 `valid_until` 时直接写 expired。

修复：global cleanup 只处理 `created_at` 超过保留上限的 open rows；family soft/hard TTL
重新由 Ranker 及其 filter evidence 决定。queued 仍在 `valid_until` 立即失去交易权限，二者
用途不同。

### P1：所有 expired 被 Trace 伪判为 TTL

旧 Opportunity Flow Ledger 只看 `status='expired'` 就生成 `ttl_expired`，直接造成错误判词。

修复：Trace 读取 append-only terminal fact，分别生成 `snapshot_superseded`、`ttl_expired`
或 `lifecycle_expired / terminal_reason_unavailable`。正常 supersede 的 final state 是
`superseded`，不再进入交易失败 drop 统计。

### P1：Overview 把 generation 当成独立机会

79 行代表 snapshot generations，不一定是 79 个独立 setup；69 expired 又没有原因拆分，
因此无法一眼判断“没开仓”是正常换代、Ranker、Operator、TTL、Risk 还是 Execution。

修复：`/analytics/opportunity-funnel` 新增：

- `setup_count` / `active_setup_count`：按
  `symbol + market + direction + scanner_family` 去重；
- `terminal_breakdown`：snapshot supersede、true TTL、other expiry、Operator
  trade/skip/watch、deterministic/other review；
- `current.counts`：generation、unique setup、active setup、supersede、TTL；
- Overview 和 `aletheia inspect entry-status` 使用同一 projection，并把 “Current blocker”
  改为中性的 “Current limiting stage / limiting reason”。

## 明确保留的行为

- Universe 仍可在成员、rank、方向、资格、TTL、risk flags、entry evidence 变化时发布新
  snapshot。该 generation history 是反事实与 outcome seed，不应为了降低计数而删除。
- 未被 Ranker 认领的旧 `open` generation 仍在父 snapshot supersede 时结束；否则 pool
  会无限增长。
- 已 queued carrier 不把父 snapshot 改写成“仍 active”；最新 feature refresh 仍按
  asset/instrument identity 进入 Operator material context，历史 origin id 保持可审计。
- Risk/Execution 的任何出口和硬限制均未改变。

## 验收判据

1. supersede 后 open generation 为 expired 且有唯一 evidence；queued carrier 仍 queued、
   Operator 可见。
2. pending 已 45 分钟但 `valid_until` 仍有 2 小时时，queue/capacity/preflight/claim 均认可；
   deadline 到达后原子写 TTL evidence 并拒绝 Proposal。
3. 缺少 `valid_until` 的历史 carrier 仍按 30 分钟兜底回收。
4. reused active snapshot 可前滑 linked queued validity；defer 不晚于该 validity。
5. 相同 active setup 的新 generation 得到 `active_operator_carrier`，不写第二个 pending。
6. open soft-TTL 不被 daily cleanup 提前终结。
7. API/UI/CLI 将 superseded generation 与 true TTL 分开；Trace 不再把 supersede 判成 TTL。
8. Risk、Execution、Exchange、Live 文件无修改。

该行为从 `opportunity_carrier_continuity/v1` 开始形成新的 B-class observation window，
不得与旧 snapshot-coupled carrier 数据合并比较 proposal rate、Operator latency 或 defer
resolution。
