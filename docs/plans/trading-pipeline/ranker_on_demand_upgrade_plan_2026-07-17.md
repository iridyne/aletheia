# Paper Trader Ranker — On-demand 升级计划（档 2 / 档 3）

> 来源：2026-07-17 会话讨论；档 1 已在提交 `216d57b3` 落地；档 2 于
> 2026-07-18 以 `opportunity_ranker_delivery/v2` 落地，进入独立观察窗
> 原则：先测量档 1 效果，再决定是否升档；不为未观察到的问题做架构改动
> 不变边界：SQLite writer 竞争 ≤ 3；不绕过 Execution Gateway；不新增未审计的写入者

## 背景

当前状态（档 1 落地后）：

- `paper_trader` scheduler cadence = 3min，配合 `_FAMILY_TTL` 让 momentum(30min TTL)
  有 10 次 review、mean_reversion(60min TTL) 有 20 次
- `scheduler.notify_paper_trader(source)` 从 `UniverseService.refresh` 两个位置调用：
  新 snapshot 发布后 / change-suppression 前滑 valid_until 之后
- 触发路径 debounce 3s；APScheduler `max_instances=1` 处理重入；纯 in-process modify_job

档 1 覆盖了 "新 opportunity → ranker review" 的 90% 场景，剩下的 10% 是**非 universe 路径产生
的评估工作**（配置变化、guard 更新、prediction/equity observer 独立触发等）——这些是档 2 的范围。

档 3 的合理性只有在观察到"档 2 仍然不够"的具体证据时才成立。

## 升档判定标准（Definition of Done for 档 1 观察窗）

Overview 页面的 Funnel 卡观察 24-48h，同时满足以下条件即认定档 1 足够，**不升档**：

1. `accept_rate` ≥ 60% 稳定（Discovered → Proposed）
2. `executable_count` 中位数 ≥ `floor`（默认 3）
3. `ttl_expired` 占 rejection 的比例 < 20%
4. Funnel 顶栏诊断多数时间为 `Healthy` 或 `Low supply`（confidence gate 主导），
   而不是 `Idle: all N open signals expired`

若观察后任一条件长期不满足，且分析确认根因是 **ranker 触发延迟**（不是 filter 阈值、
不是 confidence 阈值、不是上游数据缺失），则考虑升档 2。

档 2 落地后同样观察一个周期。只有在**档 2 之上仍有具体延迟证据**（例如 config 变化后
若干分钟内 executable 未更新、guard 上下线未被下游看到）才进档 3。

---

## 档 2：扩大 notify 触发面 + 独立 debounce 状态机

**状态：已实施。** 实际触发面覆盖四类 Opportunity producer、Universe
snapshot/TTL slide、影响 Ranker eligibility 的 settings、active Guard set 变化和
Operator queue slot release。`WakeState` 暴露 coalesced sources、burst count、最近
dispatch/completion/result；每次实际运行的来源同时写入 durable
`AgentRun.trigger_type=on_demand:<sources>`。3min fallback 未变。

### 目标

把档 1 的两个触发点扩展到 5-6 个覆盖 event-driven 全场景，仍保持"无 daemon 线程"。
把当前 `_LAST_PAPER_TRADER_NUDGE_MONO` 单一时间戳升级为一个更完善的
`WakeCoalescer`（可参考 `agent_runtime/wake_events.py` 的现有实现），让触发原因和
计数进 audit trail。

### 触发点扩展（新增）

| # | 位置 | 场景 | 优先级 |
|---|------|------|--------|
| 1 | `signal_emitter.emit_deterministic_opportunities` 内部 | 单条 opp commit（当前只在 refresh 整批之后） | P1 |
| 2 | `server/routes/settings.py` config save 后 | `min_confidence_to_trade` 或 `max_demo_position_notional_usdt` 改动 | P1 |
| 3 | `knowledge/guards.py` guard promote/demote | active guard 集合变化 | P2 |
| 4 | `agents/prediction_observer.py` / `equity_observer.py` 结束时 | 非 universe 路径生成的 opportunity | P2 |
| 5 | `agent_shell/operator_loop.py` 的 `loosen_confidence` 执行后 | 阈值放宽后立刻重评 | P2 |
| 6 | `universe/service.py` 现有两个（保留） | 保留档 1 已有触发点 | - |

### 代码变更预估

**新增/修改文件**：
- `aletheia/agent_runtime/scheduler.py`：
  - `notify_paper_trader` 内部升级：从单一 monotonic 时间戳换成结构化 `WakeState`
    （包含 last_nudge_ts / burst_count / sources[] / last_result）
  - 新增 `paper_trader_wake_status() -> dict` 供 `/status` 或诊断路由查询
- `aletheia/agent_runtime/wake_events.py`（如果结构复杂化）：新增 `RankerWakeEvent` 类
- 5 处调用方各加一行 `notify_paper_trader("<source>")`

**大约代码量**：60–100 行（含测试）。

### 契约变更

- `agent_contracts.md`：paper_trader 章节列出所有 6 个 notify 源；标注每个源的调用方
- `filter_evidence_contract.md`：无变化（notify 不写 filter_evidence）
- 新增审计事件类型 `paper_trader_nudge`（optional），若认为诊断价值高

### 测试要求

- unit test：多个 source 3s 内连续 notify → 只触发一次 modify_job
- unit test：`_scheduler is None` → notify 返回 False 且不抛
- unit test：kill_switch active → notify 静默 skip
- integration test：universe refresh 完成 → 观察 job.next_run_time 被前拉

### 回滚

单一 commit revert 即可。旧的 3min 兜底 tick 保持不变，回滚后行为退回档 1。

### 已排除

- 不新增线程 / asyncio task
- 不新增 SQLite 写入
- 不绕过 max_instances=1
- 不做 priority queue（保持 FIFO by APScheduler）

---

## 档 3：独立 event-driven daemon（对齐 pi_daemon）

### 目标

`paper_trader_daemon` 独立线程，接管热路径；scheduler 的 3min job 退化为纯 heartbeat。
适用场景：档 2 仍观察到 modify_job 延迟（例如 APScheduler executor 被其它 job 饿住 > 30s）
或需要复杂的 wake priority（例如"config 变化"应比"universe refresh"优先级高）。

### 架构

参照 `agent_runtime/pi_daemon.py`：

```
Signal producers (sync APScheduler threads / server routes / operator loop)
    └─ notify_paper_trader_daemon(source, event?)  ← call_soon_threadsafe
          ↓
_paper_trader_daemon_loop  (asyncio.Task in uvicorn loop)
    ├─ debounce 5s
    ├─ cooldown 30s（比 pi_daemon 的 60s 短，因为无 LLM 成本）
    ├─ heartbeat 10min（TTL 清理保底）
    ├─ acquire _paper_trader_run_lock
    ├─ run build_paper_candidates + write signal_pending PiDecisions
    └─ release lock, log wake_event
```

### 代码变更预估

**新增文件**：
- `aletheia/agent_runtime/paper_trader_daemon.py`（~200 行，仿 pi_daemon.py）
- `tests/test_paper_trader_daemon.py`（~150 行）

**修改文件**：
- `aletheia/agent_runtime/scheduler.py`：3min job 检测 daemon 存活 → 只发 heartbeat，
  否则走完整 tick（兜底）
- `aletheia/server/main.py` / `agent_runtime/lifecycle.py`：daemon 启停挂到 uvicorn
  lifespan（参考 pi_daemon 挂法）
- 6 个 producer 位置：从 `notify_paper_trader` 迁移到 `notify_paper_trader_daemon`
  （前者保留兼容层）

**契约**：
- `agent_contracts.md` 新增 `paper_trader_daemon` 条目
- `scheduler_contract.md` 说明 3min tick 在 daemon 存活时的角色

### 关键风险与设计约束

1. **SQLite writer 竞争** — 强制约束
   - Daemon 必须走同一个 `get_db()`（thread-local），不能持有独立连接
   - 写入路径（`mark_reviewed` / `mark_expired` / signal_pending insert）已经在
     `busy_timeout` 保护下，daemon 天然享受该保护
   - **不可**在 daemon 内部保留跨 wake 的 open transaction

2. **与 scheduler job 的互斥**
   - 采用 `_paper_trader_run_lock: threading.Lock`（不是 asyncio.Lock，因为
     scheduler tick 在同步线程）
   - Daemon 拿不到锁时立即让位并 skip 这次 wake（scheduler tick 会覆盖）
   - Scheduler tick 拿不到锁时 log skip 但**不重排**（daemon 会覆盖）

3. **可审计性**
   - 每次 wake 写一条 `agent_shell_events` 行，`event_type="paper_trader_wake"`，
     payload 含 `source / debounce_bucket / cooldown_waited_ms / candidates_evaluated /
     signals_written / duration_ms`
   - Funnel API 从这些 events 里派生"ranker wake rate"字段，UI 可选显示

4. **失败隔离**
   - Daemon 抛异常 → task 死掉；main.py 需要有自动重启逻辑（同 pi_daemon）
   - 死掉后 3min scheduler tick 兜底仍然工作（这是我们保留 heartbeat 的关键理由）

5. **Class C 判定**（`AGENTS.md`）
   - 属于 "broad new abstractions while core stability work remains"，默认 defer
   - 只有在档 2 观察后有明确证据支持才启动

### 测试要求

- Unit：daemon 独立启动/停止、锁抢占、debounce/cooldown 边界、心跳兜底
- Integration：daemon 存活时 scheduler tick 只发 heartbeat；daemon 死后 scheduler
  tick 承担完整评估
- Chaos：kill daemon 进程内部 task → 3min 内 scheduler 覆盖
- Regression：paper_trader 现有 e2e 测试都能通过（daemon 路径与 scheduler 路径行为等价）

### 回滚

Daemon 关掉的 feature flag（`cfg.runtime.paper_trader_daemon_enabled = False`）：
关闭后 producer 的 notify 自动 fallback 到档 2 的 modify_job 路径。**这是硬要求**：
不允许出现"daemon 关不掉、必须 revert commit"的路径。

### 已排除（即使做档 3 也不做）

- 不做 LLM 路径（paper_trader_ranker 是纯 rule 评估）
- 不做 priority queue（保持 wake 后一次性评估全量 open pool）
- 不做多 daemon 并行（max_instances=1 逻辑仍在，只是从 APScheduler 转移到 lock）
- 不重构 build_paper_candidates 内部逻辑

---

## 观察指标与判定路径

档 1 落地后的观察窗口（24-48h）应该收集以下数字：

| 指标 | 来源 | 门槛（不升档）| 门槛（升档 2）|
|------|------|--------------|--------------|
| accept_rate | `/analytics/opportunity-funnel` | ≥ 60% | < 40% 且持续 |
| executable 中位数 | funnel snapshot | ≥ floor | < floor 且 main_reason 非 confidence |
| ttl_expired 占比 | rejection reasons | < 20% | > 40% |
| ranker 空转率 | 待补 endpoint | < 30% | > 60% |
| 新 opp 到 review 平均延迟 | 待补埋点 | < 30s | > 90s |

后两个指标需要额外埋点，如果决定要升档 2，第一步就是先补埋点观察，别直接改代码。

## 备注

- 当前用户主路径是 paper 交易模式，daemon 在紧急止损/live trading 打开后风险面上升，
  届时任何 daemon 改动都要重新过 change-control 审查（`AGENTS.md` §Trust boundaries）
- 若未来 `paper_trader_ranker` 内部引入 LLM 调用（例如 case retrieval + LLM 评分），
  档 3 的价值会陡增；此时应重新评估
