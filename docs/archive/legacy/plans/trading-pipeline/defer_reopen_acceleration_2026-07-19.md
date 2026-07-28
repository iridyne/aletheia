# Defer→Reopen 加速与后续阶段（2026-07-19）

> **上位目标**：让 Market Observer 探测到的每一个 opportunity 在其材料事实
> 变化时能被 Operator 立即重新评估，最大化机会捕获链条的效率，最小化
> 端到端延迟。
>
> **约束**：不得破坏 B1.5 policy epoch 声明的非终态 defer invariant
> (`skip_policy.py:45-58`)；不得放宽队列容量上限的对抗性验证
> 结论；每一步都要有数据触发条件而不是拍脑袋。

## 背景

一个基于 workflow (`wf_a6c461c0-2ce`) 的深度对抗性验证得出的三点核心事实：

1. **Pipeline 本身已经是事件驱动的**：`market_observer` insert →
   `notify_paper_trader()` → `paper_trader.py:543` `notify_pi_daemon()`。
   Happy-path 端到端 5–10 秒。
2. **真正的延迟来自 `candidate_feature_refresh_wakes_operator` 承诺没兑现**：
   `skip_policy.py:44` 声明"feature 刷新应该唤醒 Operator"；但实现只在
   `universe/service.py:670/836` 每次 `universe_refresh` 循环末端发一次
   wake（最长 10 分钟一次），`universe/features.py` 里的 batch insert
   完全没有 notify 调用。Defer→reopen 因此最长要等 10 分钟。
3. **`trading_context.py:346-357` 无差别把所有 deferred carrier 排到队尾**，
   即使 fingerprint 已经变化。在 fresh + deferred 混合队列下，refreshed
   defer 会被 truncated 出可见 top-N 切片。

## 已完成（Phase A — 2026-07-19）

一个 PR 一次性交付三块：

### A0. Layer-1 观测

新文件 `aletheia/analytics/defer_classifier.py` —— 纯函数分类器
`classify_defer_reason(reason) → market_quiet | candidate_specific | unknown`。
唯一权威分类点。

`_record_deferred_context_observation`（`commands.py:1873+`）在成功后
`logger.info('operator_defer', extra={event_kind, defer_reason, reason_class,
opportunity_id, symbol, direction, defer_until, defer_deadline_basis,
policy_epoch_id})`。之前 defer 路径完全静默。

`pipeline_progress.summarize()`（`pipeline_progress.py:_active_defer_stats`）
增加四项聚合：`operator_defer_market_quiet`、`operator_defer_candidate_specific`、
`operator_defer_unknown`（按 reason_class 分类，去重到 opportunity_id 的
DISTINCT 计数）、`defer_rate`（12h 窗口）、`oldest_active_defer_age_seconds`。

`doctor _probe_profit_loop` 归因串从 `/defer×N` 扩展为
`/defer×N(mq=X/cs=Y,age=Zm,rate=W%)`（`doctor.py:1402-1410`）。

`GET /opportunity-funnel/current-snapshot` 的 `counts` 块新增 5 字段并被
Blotter 面板顶部黄色 chip 的 native `title` tooltip 消费。

### A1. Feature snapshot commit 唤醒 pi_daemon

`universe/features.py:836+` 在 `insert_universe_feature_snapshots_batch`
成功后调用新函数 `_wake_pi_daemon_for_refreshed_defers(committed_asset_ids)`。
该函数用单次 SQL 查询短路：**仅当**存在 `pi_decisions.action='signal_pending'`
且对应 `operator_evaluation_events.event_kind LIKE 'operator_decision_deferred_%'`
且 `defer_until > now`、且 symbol 在本次 batch 覆盖的 `universe_registry.id`
集合中 —— 才发 `notify_pi_daemon("feature_snapshot_committed")`。

这是把 `candidate_feature_refresh_wakes_operator: True` 从空头承诺变成
实际路径。**Defer→reopen 延迟从"up-to-10min"降到"秒级"。**

### A2. 队列排序改成 fingerprint-refreshed 优先

`agent_shell/trading_context.py:346-361` 的排序 key 从 `is_deferred`
换成 `is_stale_defer`。stale 定义：deferred 且没有比 defer 时刻更新的
feature snapshot for 该 symbol。刷新过的 defer 保持 oldest-first 位置，
只有 stale defer 被降到队尾。

Python `list.sort` 是稳定排序，输入本身是 oldest-first，所以：
`[fresh, fresh, refreshed_defer, fresh, stale_defer, stale_defer]` 保持原序，
只有 stale_defer 会移到 tail。

**没有引入任何 invariant 变更**：命令侧 fingerprint 检查依然是权威，
这只是可见性/优先级的补齐。

## Phase A 之后：数据驱动的触发条件

Phase B 与 Phase C 只有在 **A 部署 1–2 周后** 的观测数据满足对应触发条件时
才启动。所有触发都基于 A0 引入的 metric。

### Phase B — 有界 defer 与停滞探针

**只在 A 部署后**如果观测数据显示以下之一，才启动对应改动：

#### B1. `defer_count` 每 carrier 上限（原 Layer 3 A）

**触发条件**：
- Layer 1 metric 显示存在同一 `opportunity_id` 上出现 ≥ 6 次
  `operator_decision_deferred_%` 事件的 carrier
- 或 `oldest_active_defer_age_seconds` 分布 P90 > 90 分钟持续 5 天以上

**改动清单**：
- schema：`pi_decisions.defer_count INTEGER NOT NULL DEFAULT 0` + index
- `commands.py:1796-1877`：原子递增（**前置修**：`record_operator_evaluation_event`
  在 `repositories.py:522-546` auto-commit，需增加可选 connection 参数，
  用 `BEGIN IMMEDIATE` 包裹）
- `skip_policy.py:45-58`：新 invariant `defer_count_terminal_ceiling: int`
  （初值猜 6-8，具体等数据），bump `policy_epoch_id`
- 达上限时 `action='deferred_terminal_ceiling' + proposal_status='skip'`
- **合并前枚举审计**：`aletheia/reflection.py`、
  `aletheia/analytics/opportunity_flow.py`、以及所有 `action == 'skip'/'trade'`
  的下游 pattern-match 站点

**风险**：这是 B1.5 "非终态 defer" invariant 的显式松绑，必须走新的
policy_epoch_id。参考 workflow verdict：单独部署这一项、不做审计，
`reflection.py` / `opportunity_flow.py` 有极高概率把新 action 值当成
未知桶落地。

#### B2. `saturation_since` 停滞探针（原 Layer 2）

**触发条件**：
- Layer 1 metric 显示 `queue_depth == _PENDING_SIGNAL_QUEUE_CAPACITY` 时段
  占 24h 的 ≥ 15%（说明 backpressure 真实存在）
- 或 doctor `loop.profit` 在过去两周内至少两次因 defer 相关归因被 warn

**改动清单**：
- 新 singleton 表 `paper_trader_queue_health(id=1, saturation_since, updated_at)`：
  paper_trader 每次 tick 读队列深度时 upsert；**必须** monotonic 只前进
  （见 workflow verdict：用当前最老行 created_at 会因 fresh churn 重置）
- 新 doctor probe `_probe_defer_stall`（放 `_probe_profit_loop` 旁）：
  - INFO：`defer_rate > 0.7`（12h）—— regime tag，不告警
  - WARN：`saturation_since > now-30min` AND `market_quiet_ratio >= 0.5`
    AND `oldest_defer_age > 45min`
  - BLOCKER：`saturation_since > now-4h` AND 期间零 `operator_trade_intent`

### Phase C — 队列容量与准入策略（可能永远不做）

**触发条件（高严格度）**：
- Phase A + B 全部部署后 30 天，`defer_rate` 长期 > 0.6
  AND `saturation_since` 探针每天 warn 一次以上
- 同时 Layer 1 `market_quiet` 占比 > 70% —— 说明真的是"整体 regime
  就是不适合，多准入没意义"，此时才有理由动准入侧
- **明确禁止**在 Phase A 数据之前动这个 —— 之前 workflow 里 5 个准入侧
  方案全部被对抗性验证驳回，缺少数据仓促行动大概率坏事

**候选改动（择一或组合，均需先出独立设计文档）**：
- 桶容量拆分（fresh vs deferred）—— workflow verdict 明确指出会把 5 slot
  改为 3 fresh + 3 deferred 时反而饿死 Ranker 在 defer 低时段
- Adaptive cap based on Operator 吞吐 EMA —— workflow verdict 指出
  "缩 cap 时期"缺乏 reopen 因果杠杆
- Reason-gated 准入 —— workflow verdict 指出数学上不解决 starvation

三条候选**都被 workflow 的对抗性验证驳回过**，除非新出的 Phase A/B
数据能反驳原 verdict 的具体失败场景，否则不做。

## 契约（不动的东西）

- B1.5 policy_epoch invariant：非终态 defer 是权威决策，不得因队列压力
  被隐式提前终态化。**任何终态化 defer 的改动必须显式 bump epoch_id**。
- `active_operator_carrier` 唯一性：同一 (symbol, direction, scanner_family)
  最多一个活跃 carrier（`filter_registry.py:336-343`）。
- `signal_enqueue` 原子性（`filter_registry.py:322-327`）。
- `candidate_feature_snapshot_reopens_evaluation` /
  `candidate_feature_refresh_wakes_operator`（`skip_policy.py:42/44`）—— Phase A
  的补齐使这两个 invariant 从"声明"变为"实现"。

## 相关文件（当前 repo）

- `aletheia/analytics/defer_classifier.py`（新）
- `aletheia/agent_shell/commands.py:1796-1900` — defer 观测点 & log emit
- `aletheia/agent_shell/trading_context.py:257-410` — 队列构造 & 排序
- `aletheia/diagnostics/pipeline_progress.py` — `_active_defer_stats`
- `aletheia/diagnostics/doctor.py:1297-1435` — `_probe_profit_loop` 归因串
- `aletheia/universe/features.py:820-900` — batch insert & wake
- `aletheia/analytics/opportunity_progress.py:475-680` — API counts 块
- `frontend/src/pages/Blotter.tsx`、`frontend/src/api/client.ts` — 前端消费

## 判断依据（"不为了分级而分级"）

原设计把这三块拆成 Layer 1/2/3 三阶段部署。用户的反馈很直接：明显 bug
且 ROI 正向的改动没有理由等一周数据 —— 分层是手段不是目的。所以
`feature_snapshot commit wake`（原 Layer 3 前置）与`fingerprint-refreshed
优先排序`（原 Layer 3 B）被前移进 Phase A 一次性交付。

Phase B / C 保留分级，是因为它们**都会修改 invariant 或已被对抗性验证
驳回**，此类改动没有数据支撑贸然上是净负 ROI。
