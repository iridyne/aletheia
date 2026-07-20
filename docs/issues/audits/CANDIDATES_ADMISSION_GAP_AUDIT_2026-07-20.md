# Candidates 准入闸门缺口审计（Operator 空闲却 Queue Full）

> **触发**：2026-07-20 07:13 UTC，用户在 Candidates 面板观察到 5 个 "waiting for facts" +
> **8 个 preview 显示 "queue full / Signal Queue Capacity"**，同时 Operator 完全空闲
> （5 个 signal_pending 全部在 defer wait，最早 defer_until 到 07:14，最晚 08:48）。
> 用户提问：这是否正常？代码层面是否已经做了最大努力，公平且及时地处理每一个到达的 opportunity？
>
> **上游**：PR #144（`dcf212ef fix(operator): eliminate defer-loop re-evaluation and queue slot leakage`）
> 昨日已合并到 master，仍复现此问题——说明 PR #144 修的是"重复评估浪费"，**没有解决准入语义错误**。
>
> **方法**：5 路并行 subagent 深度审计 + 对抗验证（adversarial refute）+ 综合评分。
> 全部逐行核对 `~/.aletheia/app.db` 实况；仅只读，未改任何代码或数据。
> 详见 §附录 A（workflow 输出）与 §附录 B（DB 现场快照）。
>
> **审计人**：Aletheia Dev · 审计日 2026-07-20
>
> **一句话结论**：**面板"queue full"是真实缺陷（defect_present），不是设计意图。**
> 5 槽准入闸门只数"未终结的 signal_pending 载体"，完全不感知 `defer_until` — 5 个已 defer 候选
> （Operator 早已判等、无算力占用）依旧把 5 个槽全部占死，任何评分更高的新 Ranker 候选一律以
> `signal_queue_capacity` 拒绝，直到每个 defer 对应的 Opportunity `valid_until` 自然过期。
> 更严重的是 `valid_until` 一经写入永不因 defer 而延长 —— defer 时长若超过 scanner_family TTL
> （momentum 0.5h / mean_reversion 1h / volatility 2h），候选会在 defer 窗口未到之前就被
> `expire_stale_queued_opportunities` 静默判为 `ttl_expired`，Operator 从头到尾看不到第二眼。

---

## 0. 与已有文档 / 已合并 PR 的关系

| 文档 / PR | 时间 | 与本审计的关系 |
|---|---|---|
| PR #144 `dcf212ef` | 2026-07-19 | 修了 (A) 特征刷新 wake whitelist、(B) market-quiet 快路径、(C) 全 defer 时不重 arm、(D) 排序 demote、(1-A) universe_member 回退。**未触碰 admission 计数**。本审计验证：PR #144 的 defer 粘稠性修得干净，但**同时把"defer 占槽"这一潜藏缺陷显性化**（defer 现在真的粘住了，槽也就真的被占死了）。 |
| PR #139 `baf4d9a7 feat(defer): accelerate reopen path` | 2026-07-19 | 加了 feature-snapshot wake + Layer-1 观测。关注 defer 后能被及时重开，**不改 admission**。 |
| `b4af73cb fix(operator): non-terminal defer` | 2026-07-02 | 引入 defer 机制的起点。**从此 signal_pending 生命周期不再简单终结**——第一次让"1 opportunity = 1 signal_pending 行"与"1 admission slot = 1 待处理单"的两个假设产生错位。 |
| `65a91675 fix(profit-loop)!: PT-1 signal_pending 去重` | 2026-06-24 | 建立"每个 opportunity 至多 1 行 signal_pending"的物理保证。是"5 槽 = 5 opportunity"这条硬假设的**唯一源头**——之前从未有人挑战它。 |
| `docs/plans/trading-pipeline/defer_reopen_acceleration_2026-07-19.md` | 2026-07-19 | Phase B/C 尚未启动。本审计的 5 个 gap 应作为 Phase B 的输入。 |

本审计不改代码、不做修复，只**登记 gap + 量化影响面 + 追根溯源"为什么之前多次修复都没暴露"**。

---

## 1. 严重性定级

| 维度 | 判定 |
|---|---|
| Ranker 是否有货 | 🟢 有——面板上 8 个 preview 候选（AKE / 1000XEC / ACE / ATH / SKHYNIX / JTO / BABA / FIL preview），都是 Ranker 已经打分产出的 |
| Operator 是否有算力 | 🟢 完全空闲——DB 里 5 个 signal_pending 100% 处于 defer wait；`operator_evaluation_events` 最新 defer_until 分别到 07:14 / 07:24 / 07:45 / 07:55 / 08:48 |
| 准入语义是否公平 | 🔴 **不公平**——5 个已判等的 defer 占死 admission，8 个未被 Operator 看过的高分新候选一律被 `signal_queue_capacity` 拒绝 |
| 准入语义是否及时 | 🔴 **不及时**——最快也要等 SOXL 的 defer_until (07:14) 到期才有槽位。而任何 defer 时长超过 family TTL 的候选，会在等待中被静默 `ttl_expired`，永不进入第二次评估 |
| Defer 粘稠性（PR #144 目标） | 🟢 干净——workflow 的 defer_stickiness 维度检查显示 PR #144 已修好，无 wake source 能延长活跃 market-quiet defer_until |
| 修复必要性 | 🔴 必须修——用户观察到的现象与两个 confirmed root-cause 完全对应；不是设计意图 |

---

## 2. 现场证据（2026-07-20 07:13 UTC）

### 2.1 面板显示（用户截图，摘要）

```
Symbol           Side  Status                Countdown       Gate
SOXLUSDT         S     waiting for facts     retry in 8m     Direct Skip Not Confirmed
QQQUSDT          S     waiting for facts     retry in 53m    Direct Skip Not Confirmed
KORUUSDT         S     waiting for facts     retry in 48m    Direct Skip Not Confirmed
FILUSDT          L     waiting for facts     retry in 17m    Debate Not Confirmed
TRUMPUSDT        S     waiting for facts     retry in 38m    Direct Skip Not Confirmed
─────────── 上面 5 行占满 admission slots ───────────
ACEUSDTpreview   L     queue full           expires in 4h    Signal Queue Capacity
AKEUSDTpreview   S     queue full           expires in 59m   Signal Queue Capacity
ATHUSDTpreview   L     queue full           expires in 29m   Signal Queue Capacity
FILUSDTpreview   L     Ranker Carrier Active expires in 59m  Active Operator Carrier
SKHYNIXUSDTpreview S   queue full           expires in 4h    Signal Queue Capacity
1000XECUSDTpreview L   queue full           expires in 4h    Signal Queue Capacity
JTOUSDTpreview   L     queue full           expires in 29m   Signal Queue Capacity
BABAUSDTpreview  S     queue full           expires in 4h    Signal Queue Capacity
```

FILUSDTpreview 显示 "Ranker Carrier Active" 是**正确行为**（FIL/USDT 的 signal_pending 已经存在，
`_active_signal_setups()` 命中 `(FIL/USDT, long, ...)` mutex）。其余 7 个 preview 全部因 capacity 满被拒。

### 2.2 数据库快照

```
signal_pending 行数:     5
queued opportunities:    5
最早 defer_until:  2026-07-20 07:14:39 (SOXL, 距离审计时 +1m)
最晚 defer_until:  2026-07-20 08:48:41 (QQQ,  距离审计时 +1h35m)
5 个 defer_reason 分布:
  direct_skip_not_confirmed:*  ×4  (TRUMP / KORU / QQQ / SOXL)
  debate_not_confirmed         ×1  (FIL)
```

**全部 5 个都是 market-quiet defer（Operator 已判"暂不入场"）。** 没有一个是
`candidate_technical_snapshot_*` 数据缺口类的候选特定 defer。

---

## 3. 5 个已定位的 Gap

> 全部 5 个都经 5-agent workflow discovery + adversarial verify（对抗性 refute）双阶段确认。
> 完整原始 JSON 见 §附录 A。

### GAP-1 · Admission 5 槽 defer-blind（🔴 CRITICAL · root cause）

**症状**：`_plan_signal_batch` 只比较 `len(selected)` 与 `available_queue_slots`，不感知 defer；
`count_unresolved_signal_pending` 的 SQL 谓词**根本没有 `defer_until` 字段**。5 个 defer 满员时
`available_queue_slots = 0`，所有高分新候选被拒。

**证据（逐行核对）**：

| 文件 | 行 | 关键语句 |
|---|---|---|
| `aletheia/agents/paper_trader.py` | 46 | `_PENDING_SIGNAL_QUEUE_CAPACITY = 5` |
| `aletheia/agents/paper_trader.py` | 413 | `queue_depth = count_unresolved_signal_pending(_PENDING_SIGNAL_MAX_AGE_MINUTES)` |
| `aletheia/agents/paper_trader.py` | 414 | `available_queue_slots = max(_PENDING_SIGNAL_QUEUE_CAPACITY - queue_depth, 0)` |
| `aletheia/agents/paper_trader.py` | 278-279 | `if len(selected) >= available_queue_slots: decisions[opp_id] = ("signal_queue_capacity", "rejected")` |
| `aletheia/storage/repositories.py` | 769-802 | SQL 谓词只看 `action='signal_pending'` + `source.status='queued'` + `valid_until`；**没有任何 defer 字段** |
| `aletheia/agent_shell/trading_context.py` | 451, 470 | Fix D（PR #144）只做**显示排序** demote，**不释放槽** |

**Refute 结果**：`refuted=false / still_severe_after_refutation=true`。所有引用逐行匹配源码。

**修复方向**：`count_unresolved_signal_pending` LEFT JOIN `operator_evaluation_events`，
排除 `event_kind LIKE 'operator_decision_deferred_%' AND defer_until > now()` 的活跃 defer 载体。

**预期影响**：用户观察到的场景下 `queue_depth` 从 5 直接掉到 0，5 个槽全部对新候选开放。
在真实"Operator 有 5 单在处理"场景下行为不变（无 defer 事件，谓词不生效）。

---

### GAP-2 · `valid_until` 永不因 defer 而延长（🔴 CRITICAL · 放大器）

**症状**：`valid_until` 由 scanner_family 固定 TTL 表在 Opportunity 发出瞬间一次性写入，
之后永不因 defer 而延长。Defer 时长若超过 family TTL，Opportunity 会被
`expire_stale_queued_opportunities` 静默判为 `ttl_expired`，**Operator 从未获得第二次评估机会**。

**证据（逐行核对）**：

| 文件 | 行 | 关键语句 |
|---|---|---|
| `aletheia/universe/allocator.py` | 39 | `_FAMILY_TTL = {"momentum": 1800, "mean_reversion": 3600, "volatility": 7200, "derivatives": 14400, "event": 43200, "core": 86400}` |
| `aletheia/universe/allocator.py` | 356 | `item["ttl_sec"] = _FAMILY_TTL.get(family, 3600)` |
| `aletheia/universe/service.py` | 693 | `member_expires = (now + timedelta(seconds=ttl_sec)).isoformat()` |
| `aletheia/universe/signal_emitter.py` | 435 | `valid_until=m.get("expires_at")` |
| `aletheia/agents/divergence_scanner.py` | 198 | `return (parsed + timedelta(hours=2)).isoformat()` |
| `aletheia/agents/market_observer.py` | 1355 | `Opportunity(...)` **未传 valid_until**（NULL） |
| `aletheia/storage/repositories.py` | 930-1000 | `expire_stale_queued_opportunities` — `datetime(valid_until)<=now()` CAS 到 `expired`，reason_detail `"source Opportunity valid_until elapsed while awaiting System Operator evaluation"` |
| `aletheia/storage/repositories.py` | 4387-4445 | `refresh_active_snapshot_ttls` — **唯一延长路径，仅 universe-reuse 触发**；不与 defer 事件挂钩 |
| `aletheia/agent_runtime/scheduler.py` | 1729 | `_run_paper_trader` 每 180s tick 都调 `expire_stale_queued_opportunities` |

**Refute 结果**：`refuted=false / still_severe_after_refutation=true`。
仓库范围内 grep `UPDATE opportunities SET valid_until` 只命中一处（`refresh_active_snapshot_ttls`），
无任何 defer 相关调用。

**具体伤害面**：
- momentum family (30 min TTL) + defer 45 min → 静默 ttl_expired
- mean_reversion (1h) + defer 2h → 静默 ttl_expired
- volatility (2h) + defer 4h → 静默 ttl_expired

**修复方向**：在 `_record_deferred_context_observation`（`aletheia/agent_shell/commands.py:2069, 2125`）
写 defer 事件的同一事务内追加
`UPDATE opportunities SET valid_until = MAX(valid_until, defer_until + margin) WHERE id = ?`。

**预期影响**：堵住 defer→ttl_expired 的静默通路。与 GAP-1 联动——单修 GAP-1 会让释放的槽指向已过期的
Opportunity，形式上有槽实际无候选。两个必须同时修。

---

### GAP-3 · Ranker 批次 cap=3 与队列 cap=5 不对齐（🟡 MEDIUM · 吞吐限制）

**症状**：`_plan_signal_batch(limit=3)` 每 180s 最多写 3 个 signal_pending。
Ranker 按 `trade_score DESC` 排序 + 半衰期 `0.5^(age/ttl)` 衰减。
持续 inflow > 3 winners / 3 min 时，中评分候选被反复挤掉，逐次 TTL 衰减直到静默终结。

**证据**：

| 文件 | 行 | 关键语句 |
|---|---|---|
| `aletheia/agents/paper_trader.py` | 427 | `limit=3,` |
| `aletheia/agent_runtime/scheduler.py` | 938 | `_guarded("paper_trader", _run_paper_trader), "interval", minutes=3` |
| `aletheia/agents/paper_trader_ranker.py` | 161 | `candidates.sort(key=lambda c: c.get("trade_score", -999), reverse=True)` |
| `aletheia/agents/paper_trader_ranker.py` | 83 | `factor = 0.5 ** (expired_sec / max(1, ttl_sec))` |
| `aletheia/storage/repositories.py` | 326-327 | `unranked_quota = (page_size + 1) // 2` + `retry_quota = page_size // 2` — **半配额缓解，让曾被跳过的候选有机会重新浮上**  |

**Refute 结果**：`refuted=false / still_severe_after_refutation=true`。
Starvation 是 throughput-dependent，被 retry 半配额部分缓解，但没有消除。

**修复方向（三选一，成本递增）**：
- (a) trivial：`limit=3` → `limit=5` 与队列 cap 对齐，避免空队列还被限流；
- (b) small：新增 `last_ranker_visit_count`，让 retry 半配额优先扫多次被跳过的候选；
- (c) medium：把批次 cap 暴露为 operator 可调参数。

**预期影响**：GAP-1 修复后队列不常满，本 gap 影响面显著下降。但真实高吞吐 burst 时依然是限制。

---

### GAP-4 · Defer 时未检测 `defer_until > valid_until`（🟢 LOW · 观测缺口 / 防御纵深）

**症状**：`_record_deferred_context_observation` 决定 defer 时，如果 `defer_until` 超出 Opportunity
剩余 TTL，代码不检测、不告警、不裁短、不显式 skip，直接落 defer 事件。等 TTL 到时被
`expire_stale_queued_opportunities` 静默清扫，成为统计尾部（无 filter_decision 说明 "defer 被 TTL 吃掉"）。

**修复方向**：写 defer 事件前比较 `defer_until` 与 `Opportunity.valid_until`，
超出则或裁短 `defer_until := valid_until - margin`，或立即落一条显式 `skip` 释放槽位并 emit
`defer_exceeds_opportunity_ttl` 观测事件。

**预期影响**：把静默尾部转为可观测事件；GAP-2 修好后本 gap 从"防御纵深"退化为"观测记账"。

---

### GAP-5 · `signal_cooldown` 与 `operator_already_evaluated` 的隐性作用域问题（🟢 LOW · DB 老化风险）

**症状**：

- `_recent_signal_pairs`（`aletheia/agents/paper_trader.py:148`）30-min cooldown SQL **不按 `signal_source` 过滤**，
  Serenity 侧发出的 signal_pending 会污染 market_observer 的 cooldown。跨生产者不公平。
- `_operator_evaluated_opportunity_ids`（`:197`）返回所有历史非 signal_pending pi_decisions 的
  opportunity_id，**无时间窗、无 status 过滤**。任何生命周期竞态导致 opportunity 被重新 surface 为 open
  时会被静默拒绝，且集合无界增长。

**Refute 结果**：`refuted=true`——workflow 的 verify 阶段认为 `active_operator_carrier` 是有意 mutex
（不是循环 bug），且 `valid_until`-based 自然过期能兜底，隐性拒绝也有 `skip_already_evaluated` 日志。
本 gap 严重性从 high 降到 low。

**修复方向**：
- cooldown SQL 加 `AND COALESCE(signal_source, 'market_observer') = ?`；
- `operator_already_evaluated` 加 `AND datetime(created_at) >= datetime('now','-24 hours')`。

**预期影响**：DB 越用越老时不至于把 GAP-1 修复的收益吃回去。

---

## 4. 追根溯源：为什么之前多次修复都没暴露

### 4.1 关键 commit 时间线

| 日期 | Commit | 修复目标 | 遗留的隐藏假设 |
|---|---|---|---|
| **2026-06-24** | `65a91675 fix(profit-loop)!: PT-1 signal_pending 去重` | 用户授权冻结区例外。PI 因 20min 内看到同一 (symbol,direction) 重复 4 次 signal_pending 判定"数据损坏"、拒绝所有决策。修复：Path B 写 signal_pending 后标记 opportunity 为 reviewed | **"1 opportunity = 1 signal_pending 行"成为物理保证**——之后"5 槽 = 5 opportunity"的硬假设隐式坐实 |
| **2026-06-24** | `4cca08ab fix(data): signal_pending 去重清理 + prune_pi_decisions 防堆积` | 清理历史堆积 | 保留 30-min cooldown，作用域未收敛（GAP-5 潜伏） |
| **2026-07-02** | `b4af73cb fix(operator): non-terminal defer for sub-threshold conviction (v1.5)` | 引入 defer 机制：sub-threshold conviction 不再终结 skip，改 defer + defer_until。伴随 `spec_operator_low_conviction_defer.py` 355 行契约 | **打破了 signal_pending 生命周期的确定性**——但没有回到 admission 层把"待处理算力占用"从"物理行存在"里拆出来。第一次让两条假设错位，只是当时 defer 时长短 + 场景稀疏，没暴露 |
| **2026-07-17** | `8d7136e3 fix(universe): refresh valid_until in change-suppression reuse branch` | Universe 复用 snapshot 时刷新 `valid_until`，避免 wall-clock 悬崖 | **只覆盖 universe reuse，未覆盖 defer**（GAP-2 潜伏）——修复者显然想到了"TTL 需要延长"这个动机，但只按当时观察到的路径修 |
| **2026-07-17** | `f08adb98 feat(ranker): soft TTL filter with score half-life decay` | Ranker 侧改硬 TTL 拒为半衰期衰减，避免"新单一到就把老单强制淘汰" | **只作用于 Ranker 评分**，admission 侧未联动。让 TTL 过期候选在 Ranker 上还能排名，但 admission `valid_until` CAS 到 expired 后仍然被物理拿掉 |
| **2026-07-19** | `baf4d9a7 (PR #139) feat(defer): accelerate reopen path` | Phase A：feature-snapshot commit 处补 wake、refresh-aware sort、Layer-1 观测 | **关注 wake 频次和排序显示**，不动 admission 计数（GAP-1 潜伏） |
| **2026-07-19** | `dcf212ef (PR #144) fix(operator): eliminate defer-loop re-evaluation and queue slot leakage` | (A) 特征刷新 wake 白名单、(B) market-quiet 快路径、(C) 全 defer 不 arm、(D) 排序 demote、(1-A) universe_member 回退 | **让 defer 真正粘住**——反过来使 GAP-1 从"偶发问题"升级为"稳态问题"。修复方向对，但边界没触及准入层 |
| **2026-07-20** | **本审计** | 观察到"Operator 空闲 + Ranker 有货 + Queue Full"三个证据同时出现 | 揭开被 6 次修复层层压住的 GAP-1 与 GAP-2 |

### 4.2 为什么每次修复都错过了根因

**观察点驱动，缺"合同层复审"**：过去 6 次修复都由具体的用户可观察现象触发——
- 6-24："PI 拒绝所有单" → 修去重
- 7-02："小 conviction 直接 skip 太浪费" → 加 defer 分支
- 7-17："TTL 硬过滤太粗暴 / Universe reuse 后 TTL 悬崖" → 修 soft TTL / 修 reuse 分支
- 7-19 PR #139："defer 唤醒太慢" → 加 feature-snapshot wake
- 7-19 PR #144："defer loop 无休" → 修 wake whitelist + market-quiet fast-path
- **7-20 本审计**："Operator 空闲还 Queue Full" → **终于**看到 admission 语义错

每次修的都是**眼前的观察面**，但**没有回到合同层重新回答"一个 admission slot 应该等于什么"**：
- 是"Operator 当前正在评估的候选数"？（"CPU 占用"语义）
- 还是"存活的 signal_pending 物理行数"？（"内存占位"语义）

**引入 defer 前**（6-24 及以前）这两个语义等价——没有一个 signal_pending 是"名义占位不占用算力"的。
**引入 defer 后**（7-02 起）两者开始漂移，但因为 defer 一直不粘（`baf4d9a7` 前 wake 慢、多个 wake 源
反复重写 defer_until），槽位漂移很快就自愈，从未持续暴露。
**PR #144（7-19）把 defer 治好后**，漂移不再自愈——**第二天就复现成用户观察到的稳态阻塞**。

**核心教训**：每一次"局部治标"修复都可能在其他假设面上**放大**下一层的裂缝。
Defer 机制引入时应当**同时**回到 admission 合同重定义 slot 语义；6 次修复的每一次都有机会做，
都因为"当次观察面没让语义错位显性化"而错过。

### 4.3 契约文档缺口

grep `docs/contracts/decision_lifecycle_contract.md` 与 `docs/product/operator/PI_OPERATOR_DESIGN_DECISIONS.md`
均**未定义 "admission slot 的语义"**，也未定义 "defer 是否占 admission slot"。
GAP-1/GAP-2 的修复应当**同步补一节合同**，例如：

> **Admission slot invariant**：一个 admission slot 对应 Operator "当前尚未做出判断" 的 signal_pending
> 载体。已被 Operator 写入 defer 事件（`operator_evaluation_events` 中最新 event 是
> `operator_decision_deferred_*` 且 `defer_until > now()`）的候选**不消耗 slot**——
> 它已被判断，只是等待重评窗口到达。

有了合同，未来任何新的机制（如 pause / snooze / gate wait）就自然对齐，不用再靠观察触发才发现。

---

## 5. 修复顺序（建议）

| 序 | Gap | Effort | 依赖 | 交付状态 |
|---|---|---|---|---|
| 1 | GAP-1 admission defer-blind | small | 无 | ✅ **实现中**（本次 PR）：`count_unresolved_signal_pending` 加第二 `NOT EXISTS` 排除活跃 defer；新加 partial 索引 `idx_operator_evaluation_events_defer_active`；新契约 `DL-S57` / `DL-S58` |
| 2 | GAP-2 defer 时同步延长 `valid_until` | small | 与 GAP-1 同 PR | ✅ **实现中**（本次 PR）：反转 `_record_deferred_context_observation` 的 cap-down；`_DEFER_VALID_UNTIL_MARGIN_SEC = 300` + `extend_opportunity_valid_until_forward_only` helper；新契约 `DL-S59` |
| 3 | 契约文档补 admission slot invariant | trivial | 与 GAP-1/2 同 PR | ✅ **实现中**（本次 PR）：`docs/design/contracts/decision_lifecycle_contract.md` §2.3b "Admission slot 语义" |
| 4 | GAP-3 batch/queue cap 对齐 | trivial | 无 | ⏳ 未开始（GAP-1 修复后队列不常满，本 gap 影响面下降；单独 PR 交付） |
| 5 | GAP-4 defer_exceeds_opportunity_ttl 观测事件 | trivial | GAP-2 到位后自动无损 | ⏳ 未开始（GAP-2 修复后 defer 不再被截短，本 gap 从"防御纵深"退化为"观测事件"；单独 PR 交付） |
| 6 | GAP-5 cooldown 加 signal_source / already_evaluated 加时间窗 | trivial | 无 | ✅ **实现中**（本次 PR）：`_recent_signal_pairs` 加 `signal_source` 参数；`_operator_evaluated_opportunity_ids` 加 24h 时间窗；新契约 `DL-S60` / `DL-S61` |

**推荐一次 PR 交付**：`fix(admission): defer-aware slot accounting + TTL follows defer` — GAP-1 + GAP-2 +
契约补充是一个逻辑闭包，分开交付会出现"槽释放了但候选已 ttl_expired"的中间态。
GAP-3/4/5 可以在下一 PR 里合并。

---

## 附录 A：Workflow 深度审计原始输出

见 `/private/tmp/claude-501/-Users-yixian-Projects-aletheia/e47248a4-fe88-4c07-87a5-0c97c75dd807/tasks/wk9d1ydyg.output`
（session-scoped，会话结束后清理）。11 个 subagent，5 维度 discovery + adversarial verify + synthesis，
总 tokens 787,004、tool calls 168、duration 443s。

## 附录 B：DB 现场快照（2026-07-20 07:13 UTC）

```sql
-- signal_pending 全部 5 行都在 defer wait
SELECT symbol, direction, market_type, signal_source, opportunity_id, datetime(created_at)
  FROM pi_decisions WHERE action='signal_pending' ORDER BY created_at DESC;
  TRUMP/USDT|short|usdt_futures|market_observer|a7017670dd8a|2026-07-20 06:51:36
  FIL/USDT  |long |usdt_futures|market_observer|4029f3fdee36|2026-07-20 06:32:37
  KORU/USDT |short|usdt_futures|market_observer|d02560e14012|2026-07-20 05:55:18
  QQQ/USDT  |short|usdt_futures|market_observer|cf84dac3d15f|2026-07-20 04:52:52
  SOXL/USDT |short|usdt_futures|market_observer|025161a3e364|2026-07-20 03:21:48

-- 5 个 queued opportunity，valid_until 覆盖 1-4h
SELECT symbol, market, status, datetime(created_at), datetime(valid_until) FROM opportunities WHERE status='queued';
  TRUMP/USDT|usdt_futures|queued|2026-07-20 06:45:22|2026-07-20 07:45:22
  FIL/USDT  |usdt_futures|queued|2026-07-20 06:24:35|2026-07-20 07:24:35
  KORU/USDT |usdt_futures|queued|2026-07-20 05:55:16|2026-07-20 07:55:15
  QQQ/USDT  |usdt_futures|queued|2026-07-20 04:48:42|2026-07-20 08:48:41
  SOXL/USDT |usdt_futures|queued|2026-07-20 03:14:39|2026-07-20 07:14:39

-- 5 个 defer 全部是 market-quiet 类
SELECT json_extract(queue_snapshot_json,'$.opportunity_id'),
       json_extract(queue_snapshot_json,'$.defer_reason'),
       json_extract(queue_snapshot_json,'$.defer_until')
  FROM operator_evaluation_events
  WHERE event_kind LIKE 'operator_decision_deferred%'
    AND datetime(created_at) > datetime('now','-2 hours')
  ORDER BY created_at DESC LIMIT 5;
  a7017670dd8a|direct_skip_not_confirmed:unverified_adverse_reason:signal_conflict|2026-07-20T07:45:22
  4029f3fdee36|debate_not_confirmed                                               |2026-07-20T07:24:35
  d02560e14012|direct_skip_not_confirmed:unverified_adverse_reason:signal_conflict|2026-07-20T07:55:15
  cf84dac3d15f|direct_skip_not_confirmed:neutral_or_missing_skip_kind             |2026-07-20T08:00:00
  025161a3e364|direct_skip_not_confirmed:neutral_or_missing_skip_kind             |2026-07-20T07:14:39
```
