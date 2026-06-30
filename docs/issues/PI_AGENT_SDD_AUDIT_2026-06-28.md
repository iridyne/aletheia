# PI Agent — SDD ↔ 代码 对照审计（2026-06-28）

> 7 路并行审计（pi_agent_sole_decision_maker / operator_layer / PI_OPERATOR_DESIGN_DECISIONS + context_enhancement / agent_contracts + scheduler / promotion_gates / implementation_plan / spec 覆盖）逐条断言对照实际代码。
> 原始结构化结果：`.claude/jobs/.../pi_audit.json`。总计 **76 ok / 14 missing / 32 incomplete = 46 个问题项**。

---

## ⚠️ 关键定性修正（读结论前必看）

`operator_layer.md` 顶部白纸黑字：本文是 **Operator Layer 概念设计参考、不是授权**；铁律——**(B) LLM Apex Agent Shell「尚未开始」，前置依赖 Phase 0 观察门全绿**；§16 Stage 1-6 是 operator track 内部里程碑（与交易 Phase 0-7 独立）。此外存在一套 **TS `operator-shell/`**（被 gateway 引用）与 Python `agent_shell/operator_loop.py` 并存。

→ **因此审计里大量"未实现/不完整"其实是 `operator_layer.md` 有意 deferred 的概念愿景（8 modes、多级 cadence、stuck-task、Auto Repair、Research-mode shell…），不是 MVP 缺陷。** 盲目实现 = 偏离 scope + 过度工程。真正该修的是**代码声称做了、实际是桩/死写/no-op/drift/绕过安全门**的地方。下面按此重新分类。

> ### 🔴 审计可信度修正（2026-06-28 亲核中）
> audit 跑完后亲核发现 **A4、A2 两条均为误报**，根因相同：audit agent **系统性混淆了三套组件** —— ① **TS operator-shell**（`operator-shell/` node 子进程，gateway spawn，正在跑、写 "operator" 心跳）② Python `operator_loop.py`（4h job）③ Python `pi_daemon.py`（交易决策 daemon）。
> **重大后果**：C 类"概念愿景未实现"很可能**大半是"TS operator-shell 已实现、Python 没有"**（8 modes/多级 cadence/stuck-task/Auto Repair 的实现载体疑似是 `operator_loop.ts`，非 Python）。即"未实现"清单严重高估。**正派 agent 正在厘清 TS 实现度，清单待修正后才能定 P2 真实工作量** —— 在此之前不在 Python 重复造 TS 已有的东西。

> **✅ RECONCILED 2026-06-30**（见 [REMAINING_WORK_2026-06-30 §3](../status/REMAINING_WORK_2026-06-30.md)）：
> - **§A5 cadence drift 已修**：`pi_trading_decision` 实为 `~15min fallback`（`scheduler.py:398`，interval=`_pi_fallback_min`；`300.0` 是 timeout 非 interval），不再算成 1min。
> - **§C 两套 operator 边界已形式化**：本次会话补 `PI_OPERATOR_DESIGN_DECISIONS §问题2` 的 `pi_decisions.action` 命名空间不变式 + spec `OL-S12`（AST 锁 operator_loop 只写 `recommend_*`）。两环判别键 = `action` 命名空间（非 signal_source）已成可执行契约。
> - **仍 OPEN**：§B PI 三层 context 注入的行为 spec（需 LLM mock fixture）、recommend_* consume 端 spec、operator paused/Level-2 artifact spec。

---

## A. 确凿真缺陷（代码与已落地契约不符，方向明确，建议修）

| # | 缺陷 | 证据 | 影响 |
|---|------|------|------|
| A1 | **`recommend_retire`/`recommend_demote` 是死写**：operator_loop 正确写入 `pi_decisions`，但 `trading_context.py:458` 待执行队列**只查 `action='signal_pending'`**，pi_daemon 只跑 evaluate_trading_opportunity → 无人消费、无人执行退役/降级 | operator_loop.py:567-626（写）; trading_context.py:458-463（只查 signal_pending）; pi_daemon.py:158-173 | 高：operator 想退役坏策略的指令被丢弃；运行态 recommend_demote 已有 1 条死在表里 |
| ~~A2~~ | **❌ 已亲核证伪**：`get_latest_heartbeat(agent_id="operator")` 读的是 **TS operator-shell** 的心跳（gateway/operator_shell.py:547 写 record_heartbeat；运行态 agent_id=operator 有 21577 条、最新就在刚才），**不是 pi_daemon**。pi_daemon 是独立的交易决策 daemon，本就不写 "operator" 心跳（它有内存态 daemon_status()）。operator liveness 检测正常工作。audit agent 把三组件混为一谈 | gateway/operator_shell.py:547；运行态 heartbeats fresh | 无（误报） |
| A3 | **Level-1 auto-action 是 phantom**：`adjust_scanner_weight` / `request_backfill` / `trigger_optuna_sweep` 都 `_record_action()` 记成"executed"（消耗每日预算）但**不写任何 config/DB/队列**，纯 no-op | operator_loop.py:684-703 / 906-915 / 918-923 | 中：operator 自以为在调权重/补数据/调参，实际无效果；假报 executed 误导 |
| ~~A4~~ | **❌ 已亲核证伪（保留备查）**：dispatcher（operator_loop.py:1148-1158）对 Level-2 动作**正确走 `_create_proposal`**（进 proposal 队列、不执行）；`loosen_confidence`/`neutral_drift` 均在 `_LEVEL_2_ACTIONS`，其函数体的 `save_config` 在正常 dispatch 下根本不被调用。**无审批门旁路** — audit agent 误读（看到函数体有 save_config 但未确认该函数在 Level-2 路径不被触发） | operator_loop.py:1148-1158（dispatcher 正确分级） | 无（误报） |
| A5 | **cadence / 默认值 drift**：(a) `pi_trading_decision` 在 daemon 启用时 interval 算成 **1min**（`heartbeat_sec/60`，默认 60s）而非契约的 ~15min；(b) pi_daemon debounce/cooldown/heartbeat 默认值（2/45/60s）与契约（8/60s/5min）不符 | scheduler.py:381-385; schema.py:527-529 | 中：PI 决策实际跑得比契约频繁得多（成本/行为偏差） |

## B. Spec 覆盖缺口（核心决策链无行为测试守卫，建议补）

- **PI 三层 context 注入无行为 spec**：AC-S02 只查 callable 存在，无 spec 真跑 `evaluate_trading_opportunity` 验证 Layer1/2/3 注入 → 决策链回归不会被测出。
- **recommend_promote 审批门无 spec**（PG-B05 只覆盖 recommend_retire 的写入）。
- **recommend_retire/demote consume 端无 spec**（只测了写、没测执行——其实执行端就是 A1 缺失）。
- operator paused/disabled 早退、Level-2 proposal artifact、L1→L2 escalation：均无 spec。
- `PL-OL-05`（trigger_optuna_sweep）已是 xfail RED tripwire（诚实标记 not_implemented）。

## C. 概念愿景 — ❌ 经亲核：几乎全部已在 TS operator-shell 实现并运行（audit 只看 Python = 系统性误报）

`operator-shell/src/`（Node/tsx，gateway spawn，**正在生产运行**）已完整实现 operator_layer.md 愿景，逐条核实：8 operator modes（`schemas.ts:1-9` + `operator_loop.ts:61,167-184`）✅、多级 cadence 4 层（heartbeat pump / supervision 30s / subsystem 5min / daily，`operator_loop.ts:42-43`）✅、stuck/failed-task 检测（`recommendation_engine.ts:18-31`）✅、Auto Repair 11 种（`repair_engine.ts`）✅、5 种 session 类型（`session_runtime.ts`）✅、specialist 委派 ✅、operational control（pause/scan/rerun/observe-only/experiment）✅、permission 模式层级（ReadOnly/Research/PaperAuto/AllowEditing/Auto）✅、Pi LLM 推理 session（→ `/api/operator/internal/llm-session`）✅、daily/periodic reports ✅、memory compaction ✅、startup state recovery ✅。

→ **C 类几乎全部是误报**：audit agent 只看 Python operator_loop.py，漏了 3000+ 行 TS。原"未实现"清单严重高估。**P2 不需要在 Python 重造。**

**真正不在 TS shell 的（真 gap）**：(1) 动态 config 参数调整（min_confidence/position size/stop-loss ladder）只在 Python operator_loop；(2) Python operator_loop 的 L1/L2/L3 直接写 config 机制是独立平行物。→ 核心问题变成 **两套 operator（TS shell 走 command registry + Python operator_loop 走 pi_decisions/config）并存未收敛**。

## D. 文档 drift（改文档，非代码）

- `agent_contracts.md §E` narrative 说 operator loop "every 1h"，实际 cron 每 4h:15（job table 行 18 是对的，narrative 错）。
- dreaming_daily 契约写 02:00 UTC，代码用 CST。
- command 计数：plan 说 11，registry 实际 41（超实现，非缺陷，文档数字过时）。
- scheduler job table 表头计数 vs 实际注册数。

## E. 设计判断题（需 owner 拍板方向）

- **E1 Guard 链：advisory vs hard-block**。sole-decision-maker 文档说 guard 对 PI 路径应"advisory"，但 `trading_decision.py:234-236` 是 `if blocks: return None` 硬静默拦截 + 调用方只拿到泛化 `proposal_error`。是改为 advisory（降分不拦），还是保留硬拦截（安全）+ 至少把 guard 原因透出？
- **E2 Phase 3a veto / 重复 signal_pending**：Path B 每周期对同 symbol 重复写 signal_pending，即使 PI 30min 内已 skip → PI 重复评估。是否加去重/veto。

---

## 修复实施计划（owner 2026-06-28 圈定：全量做）

owner 决定三类全做 + guard 改建议性+透出 + 补退役执行端，并**主动解除** operator_layer.md「Phase 0 全绿前不动 LLM Apex Shell」前置（见记忆 aletheia-pi-agent-full-build-decision）。按风险/依赖排序：

- **P1（确凿修复，方向明确，先做）**
  - ✅ P1a：A5 cadence 解耦（pi_trading_decision fallback 固定 ~15min，不再 heartbeat_sec/60=1min）。A2（heartbeat）已证伪无需做。spec PI-S01 锁。
  - ✅ P1c：E1 → guard 链改建议性（删硬拦截 `return None`，回归 guard_chain 自身的 observability 设计；guard 原因已在 rationale 透出；Risk Engine 仍是真安全门）。spec PI-S02 锁。
  - ✅ P1b：A1 收敛完成。`operator_loop.decide_actions` **不再产生** `retire_strategy`/`demote_strategy`（旧机制写 recommend_retire/demote 死数据、无消费）。策略退役/降级收敛到 TS operator-shell（command registry）。`_action_retire/demote_strategy` 函数暂留供 PG-B05 + TS 收敛完成前引用，不再被调度。spec PI-S03 锁「不产生」。契约 §6 已同步。
  - ✅ P1d：A3 收敛完成。`request_backfill` 不再由 decide_actions 产生（收敛到 TS Auto Repair）；`adjust_scanner_weight`/`trigger_optuna_sweep` 本就是 decide_actions 从不产生的死桩（PL-OL-05 已 xfail 标 trigger_optuna_sweep no-op）。spec PI-S03 锁 request_backfill「不产生」。
  - ✅ P1e（部分）：recommend_promote 审批门行为 spec PI-S04 已补（无 recommend_promote 则 paper_running 晋升被拦、有则放行）。**剩**：PI 三层 context 注入的完整行为 spec（真跑 evaluate_trading_opportunity）——需 LLM mock fixture，留后续；当前三层 context 由 sole-decision-maker 审计确认结构完整（Layer1/2/3 build + render 均在）。
- **Python operator_loop 收敛后职责**：只保留 TS 没有的**动态 config 调参**（tighten/loosen confidence、sizing、stop-loss ladder、neutral_drift）+ notify_operator + trigger_strategy_designer/research（活跃触发器，未点名收敛，保留）。退役/降级/backfill 已收敛到 TS。
- **P2（概念愿景大工程，需逐子系统设计 + 先厘清 TS operator-shell vs Python operator_loop 关系）**
  - 8 operator modes 状态机、多级 cadence loop、stuck/failed-task 检测、Auto Repair、event-triggered loop（kill_switch_trip/outcome_batch）。
  - ⚠️ 守 AGENTS.md 红线 / §6 PI 无权限系统 / §10 Command Registry 禁令。
- **P3（低成本收尾）**：D 类文档 drift 对齐（cadence narrative、dreaming UTC/CST、command 计数）。
