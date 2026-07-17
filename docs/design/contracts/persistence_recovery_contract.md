# Persistence / Recovery 契约 (L2 — 🟡 部分实现)

> **角色**：定义"Gateway / Agent / Worker 任意重启后不丢单/仓/成交/经验，且重启后先对账、不一致即冻结"的不变量。
> **基线**：代码为底(file:line)；**本契约逐项标注 AS-BUILT(已接通 vs 代码在未接通)**——这是一个 code-ahead-of-wiring 的域，诚实区分比掩盖重要。
> **不变式来源**：上游计划 [`Aletheia_Persistence_Sync_Recovery_IMPLEMENTATION_PLAN`](../../plans/engineering/Aletheia_Persistence_Sync_Recovery_IMPLEMENTATION_PLAN.md)；与 [`execution_contract §5/§6`](execution_contract.md) + [`audit_chain_contract`](audit_chain_contract.md) 边界互补。
> Last updated: 2026-06-30 · 状态：🟡 契约(构件已实现，恢复 FSM 未接 runtime)

> ## ⚠️ 必看：招牌安全保证当前**未生效**
> 计划的核心承诺「Gateway 启动先进 `RECOVERY_MODE` 对账、状态不一致即 `SAFE_FREEZE` 禁开仓」**目前并未接进运行时**——`GatewayStateMachine` 作为类已实现(`gateway/state_machine.py`)，但 `gateway/lifecycle.py` / `startup_gate.py` **不调用它**，gateway 启动不进 recovery。
> **影响分级**：paper/demo 低风险(无真实资金一致性风险)；**接 live 前这是必须接通的前置**。本契约把它定义为"应然 + 当前 gap"，不假装已生效。

---

## §1 不可谈判原则（来自计划 §1，逐项标 AS-BUILT）

| # | 原则 | AS-BUILT |
|---|------|----------|
| 1.1 | **进程内存不是事实源**：gateway/agent/worker 内存变量只是缓存，事实源 = DB snapshot + append-only event log + artifact manifest + 交易所对账 | ✅ 部分：DB/event/artifact 三件套已实现并被消费(§2)；但"重启从这些重建"路径未全接(§4) |
| 1.2 | **订单必须幂等**：携带确定性 `client_order_id` + `idempotency_key`，重启后经它查交易所、查不到入 unresolved、无法确认即冻结 | ✅ 已实现：`make_client_order_id`/`make_idempotency_key`(`client_order_id.py:6/26`)，bitget broker 用 `client_oid=proposal_id`(`bitget_broker.py:111`)/`close-{position_id}`(:302)。⚠️ "查不到→unresolved→冻结"依赖 §4 未接 |
| 1.3 | **启动必先 recovery**：`STARTING → RECOVERY_MODE → RUNNING / SAFE_FREEZE`；recovery 完成前禁开仓/加仓/创建 live intent，允许查账/查单/撤单/补 fill | ⛔ **未接 runtime**：FSM 类在(§4)，但 lifecycle 不走它 |
| 1.4 | **状态不一致默认冻结**：本地与交易所不可解释差异 → `SAFE_FREEZE`，禁 Agent 猜测 | ⛔ **未接 runtime**：`FreezeBlockedError`(`state_machine.py:52`)定义在，但无运行时触发点 |

---

## §2 持久化构件

| 构件 | 实现 | 消费者 | AS-BUILT |
|------|------|--------|----------|
| Append-only Event Log | `EventStore.append/append_many`(`storage/event_store.py:33-43`)，`get_event_store()`，机器级 JSONL | reconciliation / lessons / skills / policies | ✅ 实现且被消费 |
| Artifact Store | `ArtifactStore`(`storage/artifact_store.py:20`)，`get_artifact_store()` | lessons / skills / policies / operator | ✅ 实现且被消费 |
| Metadata DB | SQLite（四柱：`_WRITE_LOCK` Python 级写序列化 + WAL + threading.local + was_in_tx，见 AGENTS.md） | 全系统 | ✅ |
| ps_* stores | `LessonStore`/`SkillStore`(`learning/`)、`PolicyVersion`(`policies/store.py`) | lessons:✅(LessonStore→System Operator prompt，见 knowledge_contract §6.A1)；skills:✅ draft producer(lesson_reviewer→SkillStore, no auto-activate)；policies:⚠️ | 🟡 |
| Persistence API | `/api/persistence/*`(`server/routes/persistence.py`，已注册 `app.py:477`) | 手工/CLI | ✅ 路由在 |
| Leader Lock | `gateway/locks.py:acquire(lock_name, owner_id)` | ⚠️ 多设备并发写防护，runtime 调用点待确认 | 🟡 |

> ⚠️ `gateway/recovery.py` 与 `gateway/state_store.py` **无生产消费者**(全库 grep 仅自身+测试)——即下面 §4 的恢复链未接。

---

## §3 订单幂等（§1.2 细化，✅ 已实现）

- 生成：`make_client_order_id(...)` / `make_idempotency_key(...)`(`client_order_id.py`)，确定性(同一 order intent → 同一 key)。
- 落地：bitget broker 提交单带 `client_oid=proposal_id`、平仓带 `client_oid=close-{position_id}`(`bitget_broker.py:111/302`)，交易所侧据此去重(execution_contract §5「重复提交」行)。
- **不变量**：同一 proposal 重复提交 → 交易所幂等去重，不产生第二笔真实成交。

## §4 Gateway 恢复状态机（⛔ 代码在、未接 runtime）

`GatewayStateMachine`(`gateway/state_machine.py:58`) + `GatewayStatus` StrEnum(:6)：

```
STOPPED → STARTING → RECOVERY_MODE → {RUNNING | SAFE_FREEZE}
RUNNING → {DEGRADED | SAFE_FREEZE | STOPPING}
DEGRADED → {RUNNING | SAFE_FREEZE}
SAFE_FREEZE → RECOVERY_MODE          (只能经再对账退出冻结)
```
- 受控转换表 `_TRANSITIONS`(:17)；`InvalidTransitionError`(:45)；`FreezeBlockedError`(:52) 在 SAFE_FREEZE 下挡动作。
- **AS-BUILT gap**：`lifecycle.py`/`frontdoor.py`/`startup_gate.py` 均不引用此 FSM/recovery service → **启动不进 recovery，不一致不冻结**。§1.3/§1.4 当前是应然非实然。

## §5 多设备 Sync / Backup（🟡 代码在）

- Export/Import Bundle(`sync/exporter.py`+`importer.py`+`conflicts.py`)、Backup/Restore(`backup/service.py`+`verify.py`+`restore.py`)：代码完整(ROADMAP Persistence track 表)，集成/实战验证待补。
- 红线：**绝不同步密钥或脏运行态**(计划 §0.3)；live gateway 不可双设备无锁写(§2 leader lock)。

## §6 AS-BUILT 差距清单（= 接 live 前的待接通项，触信任边界须 owner 授权）

| 差距 | 现状 | 接通动作 | 边界 |
|------|------|----------|------|
| 恢复 FSM 未接 startup | gateway 启动不进 RECOVERY_MODE | `GatewayRecoveryService` 接入 `gateway/lifecycle.py` 启动流程 | 🔴 改 gateway 启动 |
| StateStore 双写未接 | paper_trader fill/position 不双写 GatewayStateStore | 为 fill/position 流程接双写 | 🔴 触 execution 路径 |
| Agent Checkpoint 未接 | 无 agent 调 `CheckpointStore` | 长任务接 checkpoint 实现可恢复 | 🟡 agent 层 |
| 集成测试缺 | 无重启/恢复 e2e 测试 | 写 recovery/SAFE_FREEZE 专用集成测试 | 🟡 测试 |

> 这 4 项与 ROADMAP「Persistence track 下一步(需显式授权)」一致。**未授权前不接** —— 但本契约确保它们是"明示的待办"而非"以为已生效的安全网"。

## §7 Spec 锚点

`spec_persistence.py`(结构 claim：event/artifact store callable、client_order_id 确定性、FSM 转换表 + SAFE_FREEZE 强制存在、recovery 未接 runtime 的诚实 tripwire)。注册于 conformance(非 REQUIRED——本域部分实现，行为 claim 随接通补)。
</content>
