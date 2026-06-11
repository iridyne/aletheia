# Aletheia Trading OS — Phased Development Roadmap

> Last updated: 2026-06-11
> Sources: PIPELINE_STATUS.md + deep research (102 agents, 20 sources) + WORLD_MAP.md (full architecture vision)
> Nature: **Guiding document, not automatic authorization** — each phase requires explicit confirmation before starting

---

## Design Philosophy

### Verified Industry Consensus

1. **Experience loop tiers**: No learning -> Memory accumulation -> Walk-forward validated edits -> End-to-end RL.
   Skipping tiers is the #1 failure mode. We are at tier 1->2 transition.

2. **Structured signal output**: Strategies must be Pydantic schema / signal+confidence pairs, not free-text.

3. **Agent coordination patterns**:
   - Fan-out / Fan-in (ai-hedge-fund: parallel analysis -> Risk Manager -> Portfolio Manager gate)
   - Adversarial debate (TradingAgents: bull/bear + max_debate_rounds)
   - Chain-based + hard risk override (TradingGroup: chain flow + dynamic risk module hard-stop)

4. **Walk-forward validation (SHARP)**: Any "learned improvement" must prove itself on held-out data before adoption.

5. **Honest positioning**: This is a research scaffold, not a money printer.

### Red Lines (never do)

- Agent generates executable Python strategy code
- Un-gated automatic strategy modification
- End-to-end RL (insufficient data, adds opacity)
- Remove human hard control (Risk Engine is always final gate)
- Introduce heavy frameworks (LangGraph etc.) — use lightweight state machines in orchestrator
- Expand beyond BTC/ETH/SOL in MVP trading scope

---

## Phase Overview

```
Phase 0   Stabilize & Observe       — Fill infrastructure gaps, prove 72h+ stability
Phase 1   Learning Quality           — Prove memory/review output is signal, not noise
Phase 2A  Structured Signals         — Strategy becomes executable rules, not text
Phase 2B  Agent Coordination         — Chain-based workflow (observer -> critic -> trader)
Phase 3A  Experience Closes Loop     — Memory actively influences decisions
Phase 3B  Walk-Forward Evolution     — Self-revision with statistical validation gate
Phase 4   Adversarial Hardening      — Debate, retirement automation, strategy ranking
Phase 5   Intelligence Expansion     — News scanner, synthesizer, multi-domain sensing
Phase 6   Live Readiness             — Capital gates, reconciliation, human confirmation
Phase 7+  Advanced (requires separate authorization per item)
```

Each phase has an **observation gate** — hard criteria that must pass before proceeding.
Phases are **not time-locked**. Move as fast or slow as reality permits.

---

## Cross-Cutting Track: Operator Layer (顶层 LLM 操作员)

> **正交于 Phase 0–7。** Phase 轴衡量*交易智能成熟度*;本 track 衡量*操作/控制面*
> ——用户能否从 Command Center 问 "现在在干什么 / 为什么失败 / 帮我修这个低风险问题"。
> 概念设计见 [OPERATOR_LAYER_DESIGN.md](OPERATOR_LAYER_DESIGN.md);
> repo-specific 双进程详细蓝图见
> [superpowers/specs/2026-06-10-pi-operator-agent-dual-process-design.md](superpowers/specs/2026-06-10-pi-operator-agent-dual-process-design.md);
> 参考文件覆盖矩阵见
> [superpowers/specs/2026-06-10-pi-operator-agent-design-crosswalk.md](superpowers/specs/2026-06-10-pi-operator-agent-design-crosswalk.md);
> 设计完成审计见
> [superpowers/specs/2026-06-10-pi-operator-agent-design-audit.md](superpowers/specs/2026-06-10-pi-operator-agent-design-audit.md)。
> 外部参考源文件为 `C:\Users\Administrator\Downloads\aletheia_pi_agent_implementation_plan.md`。
> **该文的 `Stage 1–6` 是本 track 内部编号,与 Phase 0–7 是两套独立编号,不可互映。**
> **2026-06-10 工作约定**:权威的 Phase 0 观察窗可在另一台观测机上继续跑;本仓库允许在
> `worktree-dev` 并行推进 Track B 的设计冻结后实现,但**合并 / 上线 / 宣称完成**仍受
> Phase 0 门全绿 + 显式授权双重约束。

本 track **拆两层**(见 [AI_HANDOFF.md](AI_HANDOFF.md) 2026-06-09 判断):

| 层 | 内容 | 状态 | 前置 |
|---|---|---|---|
| **A — 确定性自愈 supervisor** | 无 LLM。code-drift/stale→重启、端口清理、限频持久化。这是稳定性根基,不是操作员。 | **DONE** (`8a6e008` + `abd7773`) | — |
| **B — LLM Apex Agent Shell (Pi Agent)** | 顶层 LLM 系统操作员。下文展开。 | **IN PROGRESS on `worktree-dev`** — Python control plane, Command Center, shell supervision, operator event/task loop 已落地；更深的 PI runtime / delegation / lifecycle 仍在后续 track | **合并 / 上线仍需 Phase 0 观察门全绿 + 显式授权** |

### Track B 核心架构(§8)

```text
┌─ User / Dashboard / Command Center ─┐
└──────────────────────────────────────┘
                ↓
┌─ Aletheia Agent Shell ───────────────┐   ← 本 track 的产物
│  App context assembly                 │
│  Conversation/session state           │
│  Command intent generation            │
│  Specialist agent delegation          │
└──────────────────────────────────────┘
                ↓
┌─ Command Registry + Permission Layer ┐   ← 安全边界(§10)
│  command schemas / permission modes   │
│  risk level classification            │
│  audit logging / safe routing         │
└──────────────────────────────────────┘
                ↓
┌─ Aletheia Trading Core ──────────────┐   ← 已存在
│  agents / risk / execution / knowledge│
└──────────────────────────────────────┘
```

### Track B 安全不变式(§6/§7, 以 AGENTS.md 为权威)

Agent Shell **绝不直接**:
- 开 live 交易 / 改凭证 / 改硬风控限额 / 绕过 Risk Engine
- 下 live 单 / 改 Execution Gateway 或凭证逻辑 / 硬删知识库

高风险操作 → 只产生 Proposal → 等人在 Dashboard/CLI 显式确认。

### Track B 必需模块(§24 — 8 个)

1. **AppContextProvider** — 按需组装系统/市场/Agent/交易/策略/风控/知识的紧凑摘要
2. **AgentShellRuntime** — LLM agent 运行循环:context→意图分类→命令执行→合成回答→审计
3. **CommandRegistry** — 命令注册表 + risk_level + 所需权限
4. **PermissionPolicy** — 权限模式层(Read Only / Research / Paper Auto / Allow Editing / Auto)
5. **SessionStore** — 会话持久化(command_session / diagnostic / dreaming / strategy / review)
6. **CommandAudit** — 每个命令/查询落审计表 + JSONL
7. **Command Center API** — FastAPI route,接管现有 `POST /api/command` 的 LLM 路径
8. **Dashboard 集成** — Shell 状态(idle/thinking/running/failed)、command trace、Agent Activity

### Track B 初始命令清单(§24 — 11 个)

| 类别 | 命令 | Stage 准入 |
|------|------|-----------|
| Read | `get_system_status` | Stage 1 |
| Read | `get_agent_activity` | Stage 1 |
| Read | `get_market_snapshot` | Stage 1 |
| Read | `get_positions` | Stage 1 |
| Read | `explain_agent_run` | Stage 1 |
| Read | `explain_proposal` | Stage 1 |
| Operational | `pause_agents` | Stage 1 (需 Paper Auto 权限) |
| Operational | `resume_agents` | Stage 1 (需 Paper Auto 权限) |
| Low-risk Write | `create_research_note` | Stage 1 (需 Research 权限) |
| Low-risk Write | `create_diagnostic_todo` | Stage 1 (需 Research 权限) |
| Low-risk Write | `create_improvement_proposal` | Stage 1 (需 Research 权限) |

**默认权限模式 = Read Only**;非读命令在 PermissionPolicy 层被拦截直到人工升级权限。

### Track B 内部分阶(§16 — 6 Stage)

| Stage | 名称 | 交付 |
|-------|------|------|
| **1** | **Read-Only Supervisor + 完整架构** | 全 8 模块骨架 + 32 条已注册 typed commands(非读命令权限拦截) + 审计 + Session。用户可问"系统在做什么/为什么提案被拒/Operator 在做什么/现在有什么建议",得到基于 DB 数据的确定性回答；也可以生成 promotion / prompt / schedule / live-limited / risk-boundary / command-center-behavior proposal artifacts，并对 recommendation / strategy review / paper-strategy pause 做真实 backend write-back。Definition of done = §24。 |
| 2 | Low-Risk Write | 解锁 Research 权限:create_research_note / create_todo / create_improvement_proposal 实际执行 |
| 3 | Operational Control | 解锁 Paper Auto:pause/resume、rerun failed task、run market scan、create paper experiment |
| 4 | Specialist Delegation | Shell 委托 specialist agents 执行并跟踪进度（`dreaming_agent` / `post_trade_reviewer` / strategy evaluation 已落地；更深 runtime 恢复与更长历史折叠仍待补齐） |
| 5 | Pi Runtime Deep Integration | 评估嵌入 pi-agent-core 或维护 Aletheia 自有 runtime |
| 6 | Self-Extension Sandbox | Agent 可提议新 extension → sandbox 测试 → 人工批准 → 启用 |

### Track B 当前实现落点(2026-06-11)

- **已落地的 Python truth-layer 基础**：`operator` 配置块、SQLite store、`/api/operator/*` 路由、CLI `agent status/pause/resume/wake/session`、兼容桥 `/api/operator/command`。
- **已落地的权限与真值来源**：worker/trading 权限与 operator 权限分离；Command Center 推荐面板与时间线现在读 backend `recommendations/events`，不再靠前端本地拼装。
- **已落地的 operator recommendation 真值对齐**：backend `build_recommendations()` 现在和 Node shell 一样会基于 backend 真状态给出 `dreaming_due / review_due / strategy_review_due / strategy_promotion_proposal_due / agent_prompt_change_proposal_due / agent_schedule_change_proposal_due / risk_boundary_change_proposal_due / command_center_behavior_change_proposal_due`，Command Center 推荐面板不再只看 offline/paused 这类基础告警。
- **已落地的 Node shell 最小运行面**：gateway supervision + heartbeat + health/status 拉取 + recommendation synthesis + diagnostic task/event 回写 + started/stopped lifecycle events + recovery backlog 载入(恢复 session/task 轮廓、recent failed tasks、unresolved proposal artifacts,且绝不恢复进 `Trading`)。
- **已落地的 loopback operator API 完整面**：backend 现在已有 `/api/operator/internal/context/query`、`/api/operator/internal/commands/execute`、`/api/operator/internal/decisions`、`/api/operator/internal/repair-decisions`、`/api/operator/internal/command-intents`，Node shell 不必再把 typed command / decision / intent 退化成自然语言桥。
- **已落地的 typed command 深化**：读命令已补到 `get_operator_status / get_strategy_lifecycle / get_failed_tasks / get_recommendations`；knowledge 与 operator write 侧已补 `create_daily_review / create_dreaming_log / create_strategy_draft / mark_task_done / archive_low_value_notification / mark_strategy_under_review / draft_extension_manifest / test_extension_sandbox`；Operational Control 已落地 `run_market_scan / rerun_failed_agent_task / set_symbol_observe_only / create_paper_experiment / pause_paper_strategy`；proposal-only command 层已落地 `propose_strategy_promotion / propose_prompt_change / propose_schedule_change / propose_live_limited_enablement / propose_risk_boundary_change / propose_command_center_behavior_change / propose_extension_enablement`，并会写 proposal artifact + event + gate preview。
- **已落地的 strategy/recommendation 真实语义**：strategy 现在有 `under_review` / `paper_trading_paused` 结构化标记；paused strategy 不再被 observer/trader 当作 active strategy，且 pause 命令会把仍然挂着该 strategy 的 open opportunities 收口为 `reviewed`；archived low-value recommendation 会真实从 backend recommendation 列表里被滤掉。
- **已落地的 Command Center 第二层深化**：页面已补 command-history filter、动态 quick actions、按筛选词联动 recommendations/tasks/timeline/message stream；事件流已经切到 **SSE-first**，且过滤词会走 backend `/api/operator/commands` 做跨 session full-history command search，不再只有当前 session 的本地筛选。
- **已落地的 operator memory 基础**：session compaction 现在既可由 CLI `aletheia agent compact` 手动触发，也会由 `operator-shell` 自动为前一 UTC 日补 `data/knowledge/operator_memory/daily_summary_YYYYMMDD.md`，并以 `operator_daily_summary` artifact + `session_compacted` event 持久化；summary artifact 现在还会带 `summary_excerpt + sections`，供 crash recovery 真正读回 compacted memory。
- **已落地的 inspectable working-memory 第一层**：`data/agent_shell/...` 已开始承载可直接检查的 operator 文件工件；当前 session 会写 append-only JSONL，operator task 会写 Markdown，`create_diagnostic_todo` 会产出 todo Markdown，extension sandbox 也会落 scratch Markdown 与 extension manifest JSON，因此 operator 工作不再只留在 SQLite 和 knowledge side effects 里。
- **已落地的 recovery/session runtime 第二层深化**：`operator-shell recoverState()` 现在不只会拉回 `operator-main` 下最近的 child sessions，把 latest branch summary 恢复进 session runtime；还支持 recovered in-flight `evaluate_strategy_state` / `create_repair_proposal` task 在 recommendation 已经消失时，仍可从 task payload 自动再进入，而不是被 `sync()` 提前清空。
- **已落地的 nested session-tree runtime 第一层**：public branch create 现在不再强制把所有新 branch 都挂回 `operator-main`；`/api/operator/branches` 已支持 `parent_session_id`，`/api/operator/active-branch` 也接受整个 operator session tree 内的 descendant session，而不再只接受 direct child。与此同时，`SessionRuntime.restore()` 与 `OperatorLoop.recoverState()` 现在会把 descendant branch detail 一起恢复进 runtime / recovery folding，所以 nested review/diagnostic/strategy branch 不再只是 UI 上的树状视觉效果，而开始成为真实可恢复的 operator state。
- **已落地的 long-history folding 第三层深化**：crash recovery 现在不再只读 latest `operator_daily_summary.summary_excerpt`，也不再只看 latest branch；它会把最近多条 child-session branch context 与多日 `operator_daily_summary` 的 `major_user_instructions / important_repair_decisions / proposal_outcomes / strategy_state_changes / unresolved_issues / follow_up_recommendations` 折叠进 recovery intent，并在有上限的 recovery intent budget 内继续吸收更多 recent branch / summary 线索，而不是硬编码只保留两条。
- **已落地的 recovery contract 收口**：Node shell 恢复时现在还会把 recent operator error events 一起折回 recovery intent，并在没有更“新鲜”的 failed task / unresolved proposal / compacted-memory 线索时，回收 latest heartbeat 的 `current_intent`。也就是说，本地 spec 里 recovery contract 的六项要求现在都已有真实接线，而不是只恢复 session/task 轮廓。
- **已落地的 task supervision truth 第一层深化**：backend `build_recommendations()` 与 Node `RecommendationEngine` 现在都会显式 surface `failed_tasks_present / stuck_tasks_present`，因此 Command Center 与 shell 本地 loop 都开始覆盖 spec 里的 failed/stuck task scan，而不只看 offline/paused/health。
- **已落地的 operator task queue 第二层深化**：`TaskRuntime` 现在不再只有一个 running task 壳；它会把额外 actionable recommendation 物化成 `queued` task，恢复 queued backlog，并在当前 task 清掉后优先提升 higher-priority queued task。backend 与 Node recommendation truth 现在都会显式 surface `queued_tasks_present`，并在 queued backlog 老化或堆积时给出分层 `queue_review_needed` 信号：`attention`（约 3 条 backlog / 15 分钟）与 `urgent`（更深 backlog、约 30 分钟 oldest age，或高优先级 queued task 老化），同时带回建议 review cadence 元数据，因此 queue review 不再只是设计术语。
- **已落地的 operator loop 1–5 分钟 cadence**：`operator-shell` 现在把 dreaming/review/strategy/risk/agent-runs 这类 subsystem review 输入从 10 秒 heartbeat 中拆出，按显式 5 分钟 cadence 缓存刷新；gateway status / health 仍保持每次心跳即时读取，session/task/command 真值仍保持 30 秒层刷新。也就是说，spec 里的 10 秒 / 30 秒 / 1–5 分钟三层 loop cadence 现在都已经有真实运行面，而不是还停在“所有数据都每 10 秒重扫”。
- **已落地的 operator mode truth 收口**：heartbeat 现在不再把 fresh short-lived task 在同一轮 loop 收尾时统一抹回 `Observing`。fresh `create_repair_proposal` 会显式报 `Repairing`；fresh `run_market_scan / rerun_failed_agent_task / evaluate_strategy_state / set_symbol_observe_only / create_paper_experiment / pause_paper_strategy` 会显式报 `Trading`；fresh review/dreaming task 也会保留 `Reviewing / Dreaming`。与此同时，recovered in-flight task 仍然只会在恢复轮次里回到 `Observing / Paused / Degraded`，因此 crash recovery 不直接恢复进 `Trading` 的红线保持不变。
- **已落地的 15–60 分钟层五条真实接线**：一条是 stale `market_observer` 会显式产出 `market_scan_refresh_due`，并在 `PaperAuto+` 下走 `run_market_scan` command-session task；一条是最近一次 failed/error/completed_with_errors agent run 若约 15 分钟后仍无恢复、且还没升级成 repeated-failure prompt proposal，会显式产出 `failed_agent_rerun_due`，并在 `PaperAuto+` 下走 `rerun_failed_agent_task` command-session task；另一条是当天 UTC 还没有 `daily_review`、但近 12 小时已有实际 agent activity 时，会显式产出 `daily_review_refresh_due`，并在 `Research+` 下走 `create_daily_review` review-session task，写回确定性的 non-critical operator summary report；这次又补上一条 risk-governance operational-control：当最近 24 小时同一 symbol 连续命中至少两次 paper-side risk rejection、且把它移出 `allowed/tradable` 不会把当前 symbol universe 清空时，backend `/api/risk/summary` 会显式产出 `needs_operator_observe_only_symbols`，backend 与 Node recommendation truth 会进一步产出 `symbol_observe_only_due`，并在 `PaperAuto+` 下走 `set_symbol_observe_only` command-session task，把该 symbol 调整为 observe-only；此外，`paper_running / paper_passed` strategy 若已被标为 `under_review` 但尚未 `paper_trading_paused`，backend 与 Node recommendation truth 也会显式产出 `pause_paper_strategy_due`，并在 `PaperAuto+` 下走 `pause_paper_strategy` command-session task，调用 typed pause 命令暂停 paper trading 并收口该 strategy 上仍然 open 的 opportunities。与此同时，task materialization 也开始按 operator permission mode 过滤，所以 `create_repair_proposal` / `run_market_scan` / `rerun_failed_agent_task` / `set_symbol_observe_only` / `pause_paper_strategy` 这类更高权限 task 不会再在 `ReadOnly/Research` 下被提前物化成注定卡死的 backlog，而 `create_daily_review` 这类 Research 权限动作也不会再在 `ReadOnly` 下误生成 backlog。
- **已落地的 specialist delegation 基础链路**：`operator-shell` 现在会读取 `/api/dreaming/summary`、`/api/reviews/summary`、`/api/strategies/summary` 与 `/api/risk/summary`。最近 24h 没有 dreaming run/log 时会生成 `dreaming_due` recommendation，创建 `run_dreaming` task，委托 `dreaming_agent` 执行；最近 24h 有 closed strategy 但缺 post-trade review 覆盖时会生成 `review_due` recommendation，创建 `generate_review` task，委托 `post_trade_reviewer` 执行；flagged strategy 缺 fresh evaluation 时会生成 `strategy_review_due` recommendation，创建 `evaluate_strategy_state` task，落到 `strategy_session` child session 执行 `get_strategy_lifecycle`；最近 research/hypothesis 输入存在但没有 active paper strategy、且没有 recent `strategy_designer` run 时，会生成 `strategy_design_due` recommendation，创建 `run_strategy_design` task，委托 `strategy_designer` 产出 fresh paper strategy；recent risk rejections 存在且没有 fresh `risk_critic` run 时，会生成 `risk_critique_due` recommendation，创建 `run_risk_critique` task，委托 `risk_critic` 写新的 risk lesson note。这些链路都会把 started/completed event、artifact 或 task completion 回写到 operator truth layer。
- **已落地的 session-kind / child-session 骨架**：`agent_shell_sessions` 现在开始承载 `kind / parent_session_id / title`；operator 内部也有了 `/api/operator/internal/sessions`。当前 dreaming / review delegation 会各自创建 `dreaming_session` / `review_session` 子会话，并把 delegation start/result 作为 message trace 写进去，主 session 继续保留 operator 总览。
- **已落地的 typed repair path 第一段**：backend 现在已有 `/api/operator/internal/commands/{command_name}` 与 `/api/operator/internal/artifacts`。`operator-shell` 在 `Research+` operator 权限下会把高严重度 health recommendation 物化成 `diagnostic_session` + `repair_decision` artifact，并执行第一条 low-risk repair `create_diagnostic_todo`，同时写 `repair_attempted / repair_executed / repair_failed` 事件。
- **已落地的 strategy-governance specialist 路径**：backend 新增 `/api/strategies/summary`；operator-shell 会把 `strategy_review_due` recommendation 物化为 `evaluate_strategy_state` task，创建 `strategy_session` child session，执行 typed command `get_strategy_lifecycle`，并把结果写成 `strategy_evaluation` artifact。
- **已落地的 proposal-only specialist 第一条真实路径**：`/api/strategies/summary` 现在也会识别 recent `strategy_evaluation` 中 promotion gates 已通过、但尚未生成 proposal 的 strategy；operator-shell 会把这类 recommendation 物化为 `create_repair_proposal` task，创建 `proposal_only` repair decision，并执行 typed proposal command，最终落出真实 proposal artifact + `proposal_created` event，而不是直接改 lifecycle state。
- **已落地的 strategy proposal ladder 三段真实路径**：`paper_running -> paper_passed` 与 `paper_passed -> live_candidate` 都会通过 typed `propose_strategy_promotion` 落 proposal；`live_candidate -> live_limited` 则会通过 typed `propose_live_limited_enablement` 落 proposal。backend recommendation truth、Node recommendation truth、repair decision materialization 与 task execution 现在都已接通这三段，而不是只支持第一段 promotion。
- **已落地的 prompt/playbook proposal specialist 第一条真实路径**：recent `agent_runs` 中若同一 agent 在 24h 内重复失败且尚无 recent `prompt_change_proposal`，backend recommendation 与 operator-shell 都会产出 `agent_prompt_change_proposal_due`；shell 会把它物化为 `create_repair_proposal` task，创建 `proposal_only` repair decision，并执行 typed `propose_prompt_change`，最终落出 `prompt_change_proposal` artifact + `proposal_created` event，而不是静默改 worker prompt。
- **已落地的 schedule proposal specialist 第一条真实路径**：recent `agent_runs` 中若同一 agent 在 24h 内重复出现 manual run，且尚无 recent `schedule_change_proposal`，backend recommendation 与 operator-shell 都会产出 `agent_schedule_change_proposal_due`；shell 会把它物化为 `create_repair_proposal` task，创建 `proposal_only` repair decision，并执行 typed `propose_schedule_change`，最终落出 `schedule_change_proposal` artifact + `proposal_created` event，而不是静默改 scheduler cadence。
- **已落地的 risk-boundary proposal specialist 第一条真实路径**：backend 新增 `/api/risk/summary`，会从 recent rejected `risk_decisions` 中识别“最近 24h 内同一可调整风险边界被重复命中，且最近尚无对应 `risk_boundary_change_proposal`”的情况；backend recommendation 与 operator-shell 都会产出 `risk_boundary_change_proposal_due`，shell 会把它物化为 `create_repair_proposal` task，创建 `proposal_only` repair decision，并执行 typed `propose_risk_boundary_change`，最终落出 `risk_boundary_change_proposal` artifact + `proposal_created` event，而不是静默改硬风控配置。
- **已落地的 Command Center behavior proposal specialist 第一条真实路径**：recent `command_session` command audit 中若同一命令在 24h 内重复被拒绝，且尚无 recent `command_center_behavior_proposal`，backend recommendation 与 operator-shell 都会产出 `command_center_behavior_change_proposal_due`；shell 会把它物化为 `create_repair_proposal` task，创建 `proposal_only` repair decision，并执行 typed `propose_command_center_behavior_change`，最终落出 `command_center_behavior_proposal` artifact + `proposal_created` event，而不是静默改 Command Center 行为。
- **已落地的 extension sandbox 第二层**：backend 现有 `extension_registry / extension_runs` 真表与 `data/agent_shell/extensions/*.json + scratch/*.md` 文件工件；typed command 已有 `draft_extension_manifest / test_extension_sandbox / propose_extension_enablement`；更关键的是 enabled extension 现在也真正进入运行面了：`AgentShellRuntime` 与 `/api/operator/command` 会先解析 slash command，再把已启用 extension 分发到对应 typed target command，并写 `extension_invoked` 事件；Command Center 也会直接展示 enabled extensions，不再要求用户靠记忆输入。
- **已落地的 context model 第一条真实消费链**：backend 新增 `/api/operator/internal/context/query`，会按 session kind 自动选择 `operational / repair / knowledge / trading` context profile，并返回安全的 `system / agents / trading / proposals / strategies / risk / errors / operator_state` curated snapshot；operator-shell 会把该 context snapshot 写入 child session，Command Center 也新增 `Context Inspector` 直接显示这条 backend 真值。
- **已落地的用户驱动 branch workspace**：backend 新增公开 `POST /api/operator/branches`，可直接创建 `command / diagnostic / dreaming / strategy / review` 五类 direct-child session，并同步写 `branch_created` 与 `active_branch_selected` 事件；新 branch 创建时还会 seed `context_snapshot` 与 opening user message；Command Center 也新增了显式的 branch title + kind 控件，所以“persistent workspace / session tree / branching side-task sessions”不再只依赖 operator loop 自动生成子会话。
- **已落地的 authoritative command preview**：backend 新增 `POST /api/operator/command-preview`，会在不执行命令的前提下返回当前输入的 `intent / classification / command metadata / parsed parameters / permission truth`；这份 truth 现在既覆盖 operator permission ladder，也覆盖 worker/global safety；Command Center composer 直接显示 `Ready / Blocked / Query / Forbidden`、risk class、operator/worker 两层 required permission、proposal/confirmation 标记与 parsed parameters，所以用户不再需要“先发出去才能知道会不会被拒绝”。
- **已落地的 structured result cards / command trace**：`/api/operator/command` 现在会把 `command / command_meta / risk_level / needs_confirmation / permitted / action` 一起写进 response message；Command Center 会对 structured `command` message 渲染 trace card，对 structured `response` message 渲染 result card，并明确区分 executed vs blocked，而不再把所有 operator 输出都压成普通文本。
- **仍未完成,因此不能声称 Track B Stage 1 完成**：更深的 PI-style session runtime 仍未完成；当前活跃缺口已进一步收窄到更广的 scheduled scan/review opportunities，以及更深的 PI-style session runtime。专门的 non-critical report refresh（system health / agent performance / risk / trade review）现在已经接上 typed command + artifact + report-file 全链路。

### 现有基础设施复用(不重建)

- `aletheia/agents/helpers.py:llm_complete/llm_structured` — LLM 调用(已验证 Databricks)
- `aletheia/storage/repositories.py` — 全部读函数(list_agent_runs/positions/fills/proposals...)
- `aletheia/agent_runtime/health.py:compute_health()` — 健康状态
- `aletheia/config/loader.py:load_config()` — 配置读取
- `aletheia/server/routes/command.py` — 现有 Command Center endpoint(LLM 路径坏了,修复之)

### 数据模型新增(§20 — 仅 Stage 1 需要的)

```sql
agent_shell_sessions   — 会话元数据(type, started_at, ended_at)
agent_shell_messages   — 会话内消息(session_id, role, content, created_at)
agent_shell_commands   — 命令审计(query_id, input, intent, command, params, output, latency_ms)
```

### 为什么 B 在 Phase 0 之后

B 是 8 个新模块的能力扩张。Phase 0 战略冻结律(见 [CURRENT_TASK.md](CURRENT_TASK.md))明确:
门全绿前只做稳定性 + doctor 类减人工确定性工具。**B 是"盖楼",72h 稳跑证明是"地基"——地基不稳不盖楼。**

> **开发可提前**(`worktree-dev` 等 worktree 分支,不合并 `master`);**上线需门全绿 +
> 显式授权。** 这条允许的是"并行研发",不是"跳过观测门"。

### Track B Stage 1 观察门

- [ ] 用户在 Command Center 问 "what is the system doing?" 得到基于实时数据的智能回答
- [ ] 用户问 "why did this proposal fail?" 得到具体引用 risk_decision 的解释
- [ ] pause/resume 被权限层拦截(默认 Read Only)直到人工升级
- [ ] 每个查询/命令有审计记录(agent_shell_commands 表)
- [ ] Shell 降级不影响 Trading Core(LLM 不可用 → 关键词回退,不崩)
- [ ] 无 live 交易 / 无凭证访问(安全测试通过)

---

## Phase 0 — Stabilize & Observe

**Goal**: All 15 scheduler jobs run autonomously. System proves basic stability.

### Tasks

| ID | Task | Status |
|---|---|---|
| 0.1 | `post_trade_reviewer` scheduled (4h) | DONE |
| 0.2 | `risk_critic` scheduled (6h) | DONE |
| 0.3 | Test baseline green (488 passed) | DONE |
| 0.4 | Config: `permission_mode=Paper` (gates paper_trader via `is_tool_permitted`; the old `autonomous_paper_trading` flag was removed in `7117bdf` 5→3 mode refactor) | DONE |
| 0.5 | Start gateway for unattended run | DONE |
| 0.6 | Pure observation period (72h minimum) | RESET 2026-06-09 — first attempt was a false start (see note) |
| 0.7 | Deterministic self-heal (code-drift/stale→restart, port cleanup, LLM→alert) | DONE 2026-06-09 (`8a6e008` + rate-limit fix `abd7773`) |

> **2026-06-09 false-start note**: A prior "observation period" was recorded as running, but on audit the gateway was a zombie — alive on stale code (`1aa1feb`, 7 commits behind HEAD), `/api/health` degraded with `code_updated_pending_restart` + `market_data_stale:1114min`. `paper_trader` had not fired in 11h; 294 opportunities produced **0** proposals/orders/fills. Restarted gateway → healthy, then built task 0.7 (deterministic self-heal) so this class of zombie auto-resolves within one 10-min health cycle. **Verified end-to-end 2026-06-09**: induced code drift → health flagged it → self-heal auto-restarted → supervisor respawned fresh code → drift cleared (audit `self_heal_restart: code_drift`). This was exactly the "False confidence" failure mode below — process alive ≠ system running.

### Observation Gate

> **Timer reset 2026-06-09 (cutover)** — paper trading moved from local `paper_internal`
> matching on **spot** to **Bitget demo** real-simulation orders on **USDT perpetuals**.
> The prior spot/local baseline is voided by this deliberate higher-fidelity form change
> (not by drift noise). This cutover commit is the intended exception to the no-commit rule;
> the new window resumes the rule: **no commits during the observation window** (drift
> compares booted commit vs `git HEAD`, not the working tree, so editing without committing
> is safe).

All must pass:
- [ ] Gateway 72h+ no crash (health = healthy/degraded, not dead) — and **no `self_heal_restart` from `code_drift`** in the window (those mean someone committed)
- [ ] market_observer producing opportunities every 15min (>70% non-error)
- [ ] paper_trader executed at least **5 fills** — each 15-min cycle that opens or adds counts as one trading action ("one open" by project convention); the `positions` table merges same-symbol/direction adds into one row, so count **fills**, not position rows
- [ ] position_manager closed at least 2 positions (demo closes place a real hedge-mode reduce order; the exchange must go flat too)
- [ ] post_trade_reviewer + risk_critic have output (knowledge_items grows beyond baseline)
- [ ] **demo orders visible on Bitget**: `get_open_orders`/`get_positions` show paper_trader's real demo orders; exchange_sync reflects demo equity/positions

> **Watch (NOT a gate)**: positions stay concentrated in one symbol because paper_trader re-picks the top opportunity each cycle and adds to the same side. **Accepted as fine for Phase 0** — the gate proves the loop *runs*. Symbol diversification is a Phase 2A concern, not a stability gate.

**If gate fails**: Diagnose (LLM unreachable / config error / data source down / demo API rejecting), fix, restart timer.
**Gate caveat**: "5 fills / 2 closes" measures that the *loop runs*, not that it's *profitable* — profitability is a separate Phase 1+ question (see "False confidence" failure mode).

---

## Phase 1 — Learning Quality Diagnosis

**Goal**: Prove that memory/review produces signal, not noise. Accumulate statistical baseline.

### Tasks

| ID | Task |
|---|---|
| 1.1 | Continue unattended run to 14 days total |
| 1.2 | Daily Learning Ledger check: `lessons_consulted`, `activation_ratio` trend |
| 1.3 | Manual audit: sample 10 opportunities for substantive key_observations |
| 1.4 | Manual audit: sample 5 post_trade_reviews for actionable insights |
| 1.5 | Compute paper trading stats: win rate / avg hold / max DD / expectancy |
| 1.6 | Diagnose and fix if quality is poor (prompt tuning, stronger model, richer data) |

### Observation Gate

- [ ] 14 days continuous run without manual restart
- [ ] `lessons_consulted` > 0 (FTS5 retrieval actually firing)
- [ ] Opportunity audit: >=60% have substantive key_observations
- [ ] Review audit: >=50% point to specific actionable improvements
- [ ] Paper trading: >=20 completed trades (sufficient sample)

---

## Phase 2A — Structured Signals & Executable Strategy

**Goal**: Strategy output becomes machine-readable. Paper_trader actually executes strategy rules.

### Tasks

| ID | Task | Reference |
|---|---|---|
| 2A.1 | Extend `MarketRead` -> `TradingSignal` with `entry_trigger: list[Condition]` | WORLD_MAP 4.5 |
| 2A.2 | Define `Condition` Pydantic schema (`indicator`, `op`, `value`, `timeframe`) | TradingAgents structured output |
| 2A.3 | `strategy_designer` outputs structured conditions (JSON), not free-text | WORLD_MAP 4.6 |
| 2A.4 | `paper_trader` reads and checks entry_trigger conditions against market data | Fixes gap C in PIPELINE_STATUS |
| 2A.5 | Add condition evaluator utility (compare indicator value to threshold) | — |
| 2A.6 | Strategy lifecycle: promotion gate now checks "strategy rules were actually tested" | WORLD_MAP 4.6 |

### Observation Gate

- [ ] paper_trader decision logs show entry_trigger evaluation (not just direction+confidence)
- [ ] At least 3 strategies have machine-readable entry/exit conditions
- [ ] Promotion gate distinguishes "strategy rule validated" vs "random signal luck"

---

## Phase 2B — Agent Coordination Flow

**Goal**: Agents form directional data flow instead of independent cron jobs.

### Tasks

| ID | Task | Reference |
|---|---|---|
| 2B.1 | Define workflow in orchestrator: observer -> risk_critic -> paper_trader | WORLD_MAP 4.7 (Decision Layer) |
| 2B.2 | risk_critic can flag opportunity as `risk_flagged` (trader skips) | TradingGroup chain + hard override |
| 2B.3 | Event-driven trigger: observer completion triggers critic, critic triggers trader | WORLD_MAP 6.2 |
| 2B.4 | Retain cron as fallback (if event chain breaks, cron still runs) | Resilience |
| 2B.5 | Add `NoActionDecision` records when critic blocks an opportunity | WORLD_MAP 4.7.3 |
| 2B.6 | Dashboard shows workflow chain events in Activity Timeline | WORLD_MAP 4.14 |

### Observation Gate

- [ ] risk_critic-flagged opportunities are skipped by paper_trader (observable in logs)
- [ ] Workflow chain (observer->critic->trader) fires end-to-end
- [ ] NoActionDecision records explain why blocked opportunities weren't traded
- [ ] System still trades when event chain is delayed (cron fallback works)

---

## Phase 3A — Experience Closes the Loop

**Goal**: Memory actively influences trading decisions. All agents read relevant lessons.

### Tasks

| ID | Task | Reference |
|---|---|---|
| 3A.1 | `paper_trader` reads `retrieve_lessons()` (advisory gate) | Fixes gap A in PIPELINE_STATUS |
| 3A.2 | `risk_critic` reads lessons when evaluating opportunities | — |
| 3A.3 | `dreaming_agent` reads actual trade positions + PnL (not just agent_runs summaries) | WORLD_MAP 4.12 |
| 3A.4 | Playbooks fully activated (all agents inject relevant playbook blocks) | — |
| 3A.5 | `post_trade_reviewer` produces structured `TradeOutcome` (not just text) | WORLD_MAP 4.11 |
| 3A.6 | `TradeOutcome` auto-annotated on originating opportunity | Closes feedback loop |
| 3A.7 | `market_observer` prompt injects "last 5 outcomes for this symbol" | TradingGroup labeled decisions |
| 3A.8 | New metric: `outcome_coverage` (% opportunities with annotated result) | — |

### Observation Gate

- [ ] `lessons_consulted` steadily > Phase 1 baseline
- [ ] `activation_ratio` > 30% (strategies not all stuck in draft)
- [ ] `review_coverage` > 50% (most traded strategies have reviews)
- [ ] paper_trader logs show "skipped/adjusted due to lesson X"
- [ ] Strategies annotated with positive/negative expectancy

---

## Phase 3B — Walk-Forward Strategy Evolution

**Goal**: From "accumulate experience" to "experience changes behavior" — with statistical validation.

### Tasks

| ID | Task | Reference |
|---|---|---|
| 3B.1 | New `strategy_reviewer` agent (every 3 days, checks paper_running outcomes) | SHARP paper |
| 3B.2 | Attribution logic: entry problem vs exit problem vs sizing problem | — |
| 3B.3 | Produce `StrategyEdit` proposals (structured parameter changes) | — |
| 3B.4 | Walk-forward validation: compare pre-edit vs post-edit on held-out data | SHARP epsilon gate |
| 3B.5 | Acceptance threshold: post-edit expectancy >= pre-edit - epsilon (configurable, default 50bps) | — |
| 3B.6 | Validated edits auto-apply; rejected edits stored as negative knowledge | — |
| 3B.7 | All edits versioned (can revert) | Principle: every change is rollable |

### Observation Gate

- [ ] At least 3 StrategyEdit proposals generated
- [ ] At least 1 passes walk-forward validation and is applied
- [ ] Applied edit does not worsen performance over 14-day observation
- [ ] No strategy modified without walk-forward validation

---

## Phase 4 — Adversarial Hardening & Retirement

**Goal**: Not all "improvements" are real. Add robustness through adversarial challenge.

### Tasks

| ID | Task | Reference |
|---|---|---|
| 4.1 | risk_critic challenges each StrategyEdit ("find scenarios where this fails") | TradingAgents bull/bear debate |
| 4.2 | max_debate_rounds = 1 (statistics decide, not LLM persuasion) | — |
| 4.3 | Auto-retirement rule: N consecutive loss days + 3 failed edits -> retired | WORLD_MAP 4.6 lifecycle |
| 4.4 | Strategy leaderboard: Sharpe / win rate / max drawdown ranking | — |
| 4.5 | Retirement reasons auditable and visible in dashboard | — |
| 4.6 | Configurable thresholds: `max_consecutive_losses`, `min_expectancy_threshold` | — |

### Observation Gate

- [ ] At least 2 strategies experience full lifecycle (create -> run -> revise -> retire OR promote)
- [ ] Retirement reasons are traceable (not sudden disappearance)
- [ ] risk_critic successfully blocked at least 1 edit that would have lost money
- [ ] System runs 4+ weeks unattended

---

## Phase 5 — Intelligence Expansion

**Goal**: Multi-domain sensing — news, deeper market structure, cross-asset signals.

### Tasks

| ID | Task | Reference |
|---|---|---|
| 5.1 | Intelligence Synthesizer: deduplicate + link scanner outputs before opportunity creation | WORLD_MAP 4.4 |
| 5.2 | News Intelligence Scanner: fetch, classify, link to assets, score impact | WORLD_MAP 4.3.4 |
| 5.3 | NewsEvent -> ResearchNote -> OpportunityCandidate pipeline | WORLD_MAP 6.4 |
| 5.4 | Regime classification signal (trending / ranging / volatile / quiet) | — |
| 5.5 | Funding rate / open interest integration (deeper crypto signals) | — |
| 5.6 | Cross-scanner consistency check (contradictions surfaced explicitly) | WORLD_MAP 4.4 Contradiction output |

### Observation Gate

- [ ] News events produce structured signals that influence opportunities
- [ ] Synthesizer deduplicates and prevents signal flooding
- [ ] Regime signal observable and correlated with real market behavior
- [ ] No news scanner directly creates orders (sensing only)

---

## Phase 6 — Live Readiness

**Goal**: Prepare (not rush) live-limited trading with maximum safety controls.

### Tasks

| ID | Task | Reference |
|---|---|---|
| 6.1 | Live lock state machine (disabled -> candidate -> limited) | WORLD_MAP 4.9.2 |
| 6.2 | Live unlock flow: CLI + Dashboard, requires explicit human confirmation | WORLD_MAP 7.3 |
| 6.3 | First-live confirmation record (audit event, cannot be automated) | Safety |
| 6.4 | Live capital envelope: strict notional cap per trade and total | WORLD_MAP 7.4 |
| 6.5 | Bitget live-read reconciliation (compare local state vs exchange) | WORLD_MAP 7.5 |
| 6.6 | Position mismatch -> kill switch trigger | WORLD_MAP 4.15 |
| 6.7 | Live-limited order path through Execution Gateway | WORLD_MAP 7.6 |
| 6.8 | Live audit report generator | — |

### Prerequisites (before even starting Phase 6)

- Phase 4 observation gate fully passed
- 12+ weeks of paper trading with positive expectancy
- Human explicitly authorizes beginning of Phase 6 work

### Observation Gate

- [ ] Live remains off unless explicitly unlocked
- [ ] First live_limited action requires confirmed human interaction
- [ ] Live uses strict capital/risk boundaries (cannot exceed configured caps)
- [ ] Position mismatch detection works and triggers kill switch

---

## Phase 7+ — Advanced (Requires Separate Authorization)

| Direction | Description | Prerequisites |
|---|---|---|
| Strategy parameter optimization | Agent tunes within bounded search space | Structured strategies + walk-forward |
| Multi-round adversarial debate | bull/bear + macro/micro cross-validation | Coordination flow stable |
| Multi-strategy portfolio | Portfolio Manager agent allocates weights | Multiple strategies independently profitable |
| Prediction market trading | Polymarket-style edge detection | WORLD_MAP 4.3.3 complete |
| RWA / xStocks trading | Tokenized equity with issuer risk model | WORLD_MAP 4.3.2 complete |
| Strategy Learning Scanner | Internet research -> hypothesis generation | WORLD_MAP 4.3.5 complete |
| Embedding retrieval | FTS5 -> vector search (more precise lesson matching) | FTS5 bottleneck proven |
| RL strategy optimization | AlphaQuanter-style end-to-end | 6+ months labeled data |
| Telegram deep integration | Approvals, alerts, control via bot | Core loop stable |

---

## Common Failure Modes (Must Prevent)

| Failure Mode | Symptom | Defense |
|---|---|---|
| Noise solidification | "Learns" fake patterns from losses, loses more next time | Walk-forward gate; only high_confidence lessons |
| Overfitting history | Perfect on backtest, fails live | No backtest optimization; real paper market validation |
| LLM hallucination in decisions | References non-existent data/patterns | key_observations must cite specific snapshot numbers |
| Single agent bottleneck | One agent dies, whole chain breaks | Cron fallback + health check + graceful degradation |
| Complexity creep | Too many features, impossible to debug | Observation gates; don't proceed until current phase stable |
| False confidence | "Ran 2 weeks without crash" != "can profit" | Separate stability and profitability verification |
| Strategy text-only trap | Beautiful text strategies that are never actually executed | Structured Condition schema (Phase 2A) |
| Feedback echo chamber | Agent reads own bad advice, amplifies mistakes | Adversarial critic + walk-forward validation |

---

## Principles (Apply Throughout)

1. **Observation gate per phase** — no "good enough" judgments; criteria are binary
2. **Risk Engine is always final** — nothing learned by agents bypasses hard limits
3. **Every change is reversible** — strategy edits have version history, can revert
4. **Live is a distant goal** — paper success does not mean live readiness
5. **One change at a time** — never combine new coordination + new format + new learning
6. **Honest positioning** — research scaffold, not deterministic profit tool
7. **Phases are not time-locked** — go fast when ready, pause when unsure
8. **World Map guides design, ROADMAP governs execution** — vision != schedule

---

## File Relationships

```
ROADMAP.md (this file)         — Phase-based execution plan with observation gates
WORLD_MAP.md                   — Full architecture vision (module specs, data contracts, workflows)
OPERATOR_LAYER_DESIGN.md       — Cross-cutting Operator Layer track design (LLM Apex Agent Shell, layer B)
superpowers/specs/2026-06-10-pi-operator-agent-dual-process-design.md
                              — Repo-specific dual-process implementation design for Operator Layer
superpowers/specs/2026-06-10-pi-operator-agent-design-crosswalk.md
                              — Coverage map from external PI plan -> local design sources
superpowers/specs/2026-06-10-pi-operator-agent-design-audit.md
                              — Design-phase completion audit for the dual-process Operator Layer package
PIPELINE_STATUS.md             — Current implementation status per pipeline stage
north_star_references.md       — Reference systems comparison + gap analysis
AGENTS.md                      — Governance red lines (constraints)
DECISIONS.md                   — Architectural decision records
AI_HANDOFF.md                  — Session handoff state
```
