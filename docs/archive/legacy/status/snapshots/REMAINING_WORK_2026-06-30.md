# 真实剩余工作 + Ledger 对账 — 2026-06-30

> **⚠️ STATUS: SUPERSEDED by [`REMAINING_WORK_2026-07-01.md`](REMAINING_WORK_2026-07-01.md) (2026-07-01)。**
> 本文 §1.1 列的"Phase 2A 头号缺口"已于 2026-07-01 收口（DSL 本体 + PG-B06 晋级门），
> §2.1 的 spot-parity 分支已 merge（commit `8bfb00c`）。当前状态请读 07-01 版；本文仅作历史归档。

> 性质：L4 状态快照（易腐）。用 **git + SDD conformance matrix + ROADMAP** 三个"不会撒谎"的源核出真实剩余，**不照搬 `docs/issues/` 与 `docs/archive/legacy/plans/` 的表面读数**（见 §0）。
> 上游：本次文档/设计审查（[报告](../../../../research/audits/PLATFORM_DOC_DESIGN_REVIEW_2026-06-30.md) + [实施计划](../../plans/strategy-research/doc_design_review_impl_plan_2026-06-30.md)）。

---

## 0. 为什么需要这份文档（元发现）

核对发现：**ledger 与 plan 文档严重滞后于代码——它们列为 "OPEN / 待 merge / 无代码" 的条目里，有相当一部分早已落地。** 根因：`docs/issues/` 和 `docs/archive/legacy/plans/` 是"只进不出"的——工作做完后没人回头把对应条目标 done。

后果很实际：**任何人（含 AI）想回答"还剩什么"，从 ledger 读起来都会被误导**，把已完成的事重新排进优先级、浪费时间在假待办上。本次一次性 triage 就踩了这个坑（把 Serenity 证据质量、负反馈闭环、TaG hash chain、cadence 修复都误报成待办）。

因此本文 §1 的"真实剩余"以可信源为准；§3 给出"已核实 done、建议收口"的清单；§5 提出收口纪律以防复发。

---

## 1. 真正还没做的（可信源核出）

### 1.1 〔头号 / 结构性〕Phase 2A：可执行策略 — 状态已更新（2026-06-30 核实）
- **最新核实**：`strategy_designer.py` 已实现完整的 `entry_conditions` → `compile_ruleset` 路径（:391-423），LLM prompt 要求 JSON 结构化条件，`_compile_ruleset` 编译为 `RuleSet` DSL，`scan_active_rulesets` 运行时求值产生信号，`paper_trader` Path A 已消费这些信号。**不再是"死字符串"状态。**
- **仍然有效的缺口**：`entry_rule/exit_rule`（:68-69）文字字符串是旧 advisory 字段，在 `compile_ruleset` 失败时的 fallback 路径仍会产出（:451-488）；晋级门是否验证了"RuleSet 真被求值过"（而非仅 direction_bias）尚未确认。
- **SDD 信号**：`spec_strategy_pool.py` docstring 把 Phase 2A+ 任务记为 `status="missing"` tripwire（针对 strategy_pool direction_affinity/applicable_regimes 整合，不是 DSL 本身）。
- 工作量评估更新：DSL 本体已 **DONE**；剩余的是晋级门"规则被跑过"校验 + short scanner + regime 引导，属 2A+ (**M**)。

### 1.2 〔决策链续〕建立在 2A 之上的 Phase
- **2A+ 方向性**：`direction_affinity`/`applicable_regimes` schema 已加（`2A+.1` DONE），但 scanner 原生 `direction` 输出、专用 short scanner、regime 主动引导未做。**M**。
- **2B Agent 协调链**：observer→risk_critic→trader 有向流 + risk_critic 硬否决（`risk_flagged`→trader skip）+ 事件触发链。现在是各自 cron。**M**。
- **3B Walk-Forward 进化**：`strategy_reviewer` + `StrategyEdit` 结构化改动 + 留出数据统计门（post-edit ≥ pre-edit − ε）。这是学习从"软"（改 prompt）到"硬"（改参数且被统计验证）的关键一跳。**L**。

### 1.3 〔SDD 机器信号〕xfail tripwire = 明确声明的未完成
**✅ 更新（2026-06-30 核实）**：原 4 个 xfail，现存 **1 个**（conformance 已达 100%）：
- **已修**：`PL-PT-04`（`spec_pipeline_paper_trade.py`）—— 代码早已修好（`paper_trader.py:165` 传 `opportunity_id`），是 spec 自己检查错误列（`proposal_id` 而非 `opportunity_id`），本次已修正 spec + 移除 xfail。
- **仍在 xfail**：`PL-OL-05`（`spec_pipeline_operator_loop.py`）—— `trigger_optuna_sweep` 只记日志无工作行（真未实现），需 durable optuna_jobs 队列，工作量 **M**。
- **已 GREEN**：`spec_insight_feedback_loop.py`、`spec_pipeline_position_manager.py` 原 xfail 均已转绿（上游代码已修）。

### 1.4 〔代码在、没接通〕Persistence/Sync/Recovery 上层协调
- 18 模块代码完整（ROADMAP 已列），运行时底层（SQLite / JSONL event log / reconciliation）**实际在跑**；但上层协调未接进 gateway：`RECOVERY_MODE` 状态机、`SAFE_FREEZE`（状态不一致时禁开仓）、Checkpoint 跨机迁移、Policy versioning。
- 属"文档比代码雄心大"。动它触 gateway 启动流程，**需显式授权**。**M–L**。

### 1.5 〔未来 / gated〕Self-Evolution Tier 3
- 确定性 scanner 规则提炼（省 token）。`self_evolution_plan.md` 标 PLANNED **2026-08-17**，前置 = 每 pattern ≥20 OOS 样本 + Tier 2 已产有效 memo。**现在做过早。**（Tier 1/2/2.5 已 DONE。）

---

## 2. 待 owner 决策（核实后只剩这一件真的）

### 2.1 spot-parity 分支合并
`worktree-demo-live-spot-parity-event` 有 **2 个未入 master 的 commit**（`git branch --no-merged master` 确认，是唯一真未合并的分支）：
- `bd36de3` feat(execution): 现货走 OKX adapter；PI 决定 market_type
- `aa29ab6` feat(frontend): UTA-friendly Open Positions blotter
触执行 adapter 边界 → 需你单独审核决定是否 merge。

> 其余 worktree 分支（`openalice-tag-research` / `doctor-coverage-fixes`）内容**已在 master**，无未合并 commit。

---

## 3. Ledger 对账：核实为 DONE、建议收口的条目

> 下列均已用 git commit / conformance spec 核实为已落地；建议把对应 ledger/plan 条目标 `done`/归档。
> ⚠️ Serenity 类：spec 已注册且 conformance 绿 ⇒ **spec 编码的断言成立**；个别行为细节（如 thesis-invalidation 退出）在真正收口时再逐条复核。

| 文档（声称状态） | 实际 | 证据 |
|------|------|------|
| `TAG_BORROW_BACKLOG` P0-1/P1-2「待 merge」 | **DONE in master** | commits `b8ed990`(commit_hash) + `31eb1a9`(audit hash chain)，merge `b9a4ac5` |
| `round2_negative_feedback_loop_closure` Module J「待 FF merge」 | **DONE in master** | commit `dd559a7`「close the negative-feedback loop」；`spec_insight_feedback_loop` 绿 |
| `serenity_execution_path §2.1` tier 词表裂缝 / D2 证据质量「OPEN 无代码」 | **DONE** | `serenity_evidence.py`（TIER_LADDER + `verified→disclosed`/`credible→high_confidence` 归一化 + 生成者≠评测者）；`spec_serenity_evidence` 已注册 |
| `serenity_aiberkshire_integration_plan` S4/S2/S1「OPEN 有 plan 无码」 | **DONE（编码部分）** | `spec_serenity_verdict` / `spec_serenity_researchability` / `spec_serenity_multi_lens` 均已注册并绿 |
| serenity Phase 2/3/4/5「OPEN」 | **DONE（编码部分）** | `spec_serenity_attribution` / `spec_serenity_promote` / `spec_serenity_holding` / `spec_serenity_live_readiness` 均已注册并绿 |
| `PI_AGENT_SDD_AUDIT §A5` cadence「pi_trading_decision 算成 1min」 | **DONE** | `scheduler.py:398` 注释 "~15min safety-net fallback"，interval=`_pi_fallback_min`；`300.0` 是 timeout 非 interval |
| `frozen_optimization_backlog` / `SYSTEM_MAP` MarketAwareExitsConfig「0% 前端覆盖」 | **DONE（5/9）** | `Settings.tsx:1648`（本次已在 SYSTEM_MAP 同步标注） |

本次会话另已落地（不在上表，单独记）：paused 对齐 kill_switch、`decision_lifecycle_contract`、`OL-S12`、风控 16→20 关、`EX-C03`、SYSTEM_MAP §四/五/六 同步。

### 仍 OPEN、**不**收口的（区别于上表）
- `TAG_BORROW` P0-2(state_after 快照)/P1-1(reject 补 hash)/P0-3(Guard 责任链拆分) — 触 trust boundary，BLOCKED-待 owner。
- `MASTER_FIX_LEDGER §P3d` 一般 bug（universe POST /policy、volume_scanner 内存态、symbol_map KeyError、dreaming_log 孤儿指针等）+ UI 诚实性债 — **需逐条核实**（BACKEND_ISSUES 历史经验：约 25% 声称状态不准，两个方向都有），不可盲信，收口前必 grep。

---

## 4. 如果只挑 3 件事

1. **做一次 ledger/plan 对账收口**（½ 天，纯文档）：把 §3 上表条目标 done/归档，并逐条核实 §3 下半的 P3d bug 真伪。**元杠杆**——不做它，下一次"还剩什么"还会被同样误导。可顺接本次文档治理。
2. **Phase 2A 可执行策略**（§1.1）：唯一确认的结构性大缺口，VISION 赌注的核心未闭合点。先做最小版（声明式 `entry_conditions`）。
3. **决定 spot-parity 合并（§2.1）+ 清掉 xfail tripwire 那批（§1.3）**：前者一个待你点头的能力；后者几个 S–M 收尾，做完 conformance 100%。

---

## 5. 元建议：建立 ledger 收口纪律（防复发）

§0 的病根值得制度化修一下（与本次刚补的文档治理同源）：
- **完工即收口**：PR/commit 落地某 ledger/plan 项时，同一改动里把该项标 `done` 或移入 archive，禁止"只进不出"。
- **可加治理规则**（呼应 `README.md` 已加的"契约漂移标注须有 BACKEND_ISSUES 条目"）：`docs/archive/legacy/plans/` 的 plan 一旦其 spec 全绿，应在文件头标 `STATUS: IMPLEMENTED (spec_xxx)` 并移出 active 区。
- **单一真相**：`aletheia doctor` + conformance matrix 是状态真相源；ledger 是意图/历史记录，不应被当作当前状态读。
</content>
