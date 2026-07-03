# Aletheia Trading OS — System Operator Layer Design

> **命名说明（2026-07-01）**：本文档历史文件名 / 原标题含"Pi Agent"，现统一更名为
> "System Operator"。代码层文件名（`pi_daemon.py`、`pi_runtime_adapter.ts`）因触 trust
> boundary 保持不变，但在文档、注释、会话交流中一律使用"System Operator"。
> earendil-works/pi（外部 npm 包）与本文无关——详见 `DECISIONS.md §2026-07-01`。

**Version:** 0.2 (2026-07-01: 重命名 System Operator → System Operator)  
**Purpose:** Define how Aletheia's System Operator layer works as the top-level system agent, so the product becomes a coherent self-managing Agentic Trading OS rather than a set of disconnected micro-agents and backend services.

---

> ## [必看] System Operator = 三个不同的组件,别混(2026-06-28)
>
> 历次审计反复把三者当成同一个(7 路审计 A2/A4 误报根因)。三个独立组件,语言/进程/职责不同:
>
> ```text
> +----------------------------------------------------------------------+
> | (1) operator-shell/  (TypeScript/Node 子进程, gateway spawn)          |
> |     = 真正顶层操作员 = 本文档描述的 Operator Layer 本体               |
> |     长驻/心跳/多会话/走 command registry; 管观察/AutoRepair/退役降级   |
> |     [!] 自己不跑 LLM, 经 HTTP 调 Python /api/operator/internal/llm     |
> |     [!] agent_shell_heartbeats 里 agent_id=operator 心跳是它写的       |
> +----------------------------------------------------------------------+
> | (2) aletheia/agent_shell/operator_loop.py (Python, 每 4h cron)        |
> |     = 旧治理脚本, 已收敛为只做动态 config 调参                        |
> |     [!] 不再产生 retire/demote/backfill, 收敛归 (1) command registry  |
> +----------------------------------------------------------------------+
> | (3) aletheia/agent_runtime/pi_daemon.py (Python daemon)              |
> |     = 交易决策 daemon, 与治理无关, 不写 operator 心跳(A2 误报源)      |
> +----------------------------------------------------------------------+
> ```
>
> **单一真相源铁律**: operator 行为权威记录统一落 Python SQLite 表; (1) 只编排+执行,
> 不许留独立真相: 内存态须有 Python 回写路径且重启可重建
> (spec spec_operator_single_source.py + spec_operator_contract_parity.py 守)。

---

> ## 落锚说明（2026-06-10 — 治理对接）
>
> 本文是 **Operator Layer（顶层 LLM 操作员）** 的设计参考，**不是自动授权**。它已在
> [ROADMAP.md](../status/ROADMAP.md) 挂为一条**贯穿性并行 track**（与 Phase 0–7 的交易成熟度轴正交）。
>
> **两条铁律先于本文一切内容：**
> 1. **拆两层**：(A) 确定性自愈 supervisor（无 LLM，稳定性根基）= **已交付**（`8a6e008` + `abd7773`）;
>    (B) 本文描述的 **LLM Apex Agent Shell** = 尚未开始,前置依赖 = **Phase 0 观察门全绿**。
>    地基(稳跑证明)不稳前不动 (B)。见 [CURRENT_TASK.md](../_ai/CURRENT_TASK.md) 战略冻结律。
> 2. **编号防混淆**：本文内部的 `Stage 1–6`（§16）指 **Operator track 内部里程碑**，
>    与 [ROADMAP.md](../status/ROADMAP.md) 的 `Phase 0–7`（交易成熟度）是**两套独立编号**，不可互相映射。
>    引用时一律说 "Operator Stage N",不说裸 "Stage N"。
>
> 本文的安全边界（§6 Pi 无权限系统、§7 能/不能做、§10 Command Registry 禁令）与
> [AGENTS.md](../../AGENTS.md) 红线一致;落地实现时以 AGENTS.md 为准。
>
> **2026-06-10 详细化补充**：本文继续承担**概念设计**角色。针对当前 repo 的**双进程实现转换蓝图**，
> 权威细化文档已移至：
> - [specs/2026-06-10-pi-operator-agent-dual-process-design.md](specs/2026-06-10-pi-operator-agent-dual-process-design.md)
> - 参考文件覆盖矩阵：[specs/2026-06-10-pi-operator-agent-design-crosswalk.md](specs/2026-06-10-pi-operator-agent-design-crosswalk.md)
>
> （原路径 `superpowers/specs/` 已被废弃；上述文件对应 `docs/design/specs/` 目录，
> 由 `docs/design/README.md` 中的 frozen-sweep 分类保留。
> 更高层权威文档：[PI_OPERATOR_DESIGN_DECISIONS.md](PI_OPERATOR_DESIGN_DECISIONS.md)。）

---

## [产品定义] 监管/自愈契约（2026-06-30 owner 确认）

### 自愈档位

| 档位 | 行为 | 触发方式 |
|------|------|----------|
| L0 通知 | 发现异常，通知人类 | 已实现（Level-1 notify_operator） |
| L1 自动修数据 | 清理过期 heartbeat/日志、重启卡死 collector、重建 SQLite 索引 | PI 批准后执行 |
| L2 人工确认 | 清除 signal_pending 队列、修改 prompt 片段/配置 | PI 评估 → 推送通知 → 等人确认 |

#### L2 动作类型说明（`_LEVEL_2_ACTIONS` 注册要求）

L2 自愈档位包含三个专属动作类型，必须注册到 `operator_loop._LEVEL_2_ACTIONS`，dispatcher 才能将其路由到 `_create_proposal`，人工确认门控才能接管：

| 动作类型 | 含义 | 影响范围 |
|----------|------|----------|
| `clear_signal_pending` | 清除 signal_pending 队列中的悬挂信号 | trading loop 状态 |
| `modify_prompt_fragment` | 修改 PI 系统 prompt 中的某个片段 | LLM 决策行为 |
| `modify_config_fragment` | 修改运行配置中的某个字段 | 系统参数 |

**可执行 spec 引用**：`tests/specs/spec_operator_loop.py::test_l2_selfheal_action_types_registered` (SH-S01, L2a, status=missing — 未注册则 RED)

### 不变式

1. **PI 批准前置**：任何 L1/L2 自愈操作执行前，必须经 PI 读取诊断报告并输出"批准/拒绝 + 理由"。PI 不批准则不执行。
   - 实现要求：`_execute_level1_action` 对 `clean_stale_heartbeats` / `restart_collector` / `rebuild_sqlite_index` 三个自愈动作，必须在 dispatch 前调用含 `pi_approval` 或 `approve_l1` 字样的门控函数。
   - **可执行 spec 引用**：`tests/specs/spec_operator_loop.py::test_l1_selfheal_pi_approval_gate_in_execute` (SH-C02, L2c, xfail — 门控未实现时持续 RED；与 OL-C08 共享契约)
2. **结构化日志强制**：每次自愈操作必须写结构化日志，字段包含：`operation_type` / `trigger_reason` / `result` / `before_state` / `after_state`。
3. **交易时隔离例外**：自愈操作可在系统正在交易时执行，但 L2 操作（涉及 signal_pending 或 prompt/配置）即使 PI 批准也必须等人类确认。
   - 实现要求：`_create_proposal` 写入 `create_artifact` 的 content dict 必须含 `human_confirm_required` 键且值为 `True`，作为前端/通知路径的识别依据。
   - **可执行 spec 引用**：`tests/specs/spec_operator_loop.py::test_l2_proposal_artifact_carries_human_confirm_required` (SH-B03, L2b, xfail — 字段未实现时持续 RED)
4. **system_action 审计**：L1/L2 操作完成后在 `knowledge_items (item_type=system_action)` 写入审计记录（已有 self-heal 路径，继续复用）。

---

## 1. Executive Summary

Aletheia currently has a strong local-first trading product shell and a mostly working backend/frontend foundation, but the system still lacks a true **top-level agentic control layer**.

The current shape is close to a pyramid:

```text
Top:    mostly fixed code / dashboards / scheduled backend flows
Middle: orchestration services and deterministic modules
Bottom: many micro-agents / scanners / reviewers / task modules
```

The problem is that there is no single system-level agent that can:

- understand the whole App context,
- inspect what all lower-level agents are doing,
- diagnose small runtime issues,
- coordinate sub-agents,
- open and manage tasks,
- interact naturally through the Command Center,
- update workspace notes, playbooks, and paper strategy drafts,
- explain the system state to the user.

This creates a control gap: the system is running, but the user cannot easily ask “what is happening?”, “why did this fail?”, “please fix this low-risk issue,” or “re-run this component with more context.”

The proposed solution is to introduce an **Aletheia Agent Shell** powered by a **Pi-like Agent Runtime**, positioned as the **top-level supervisor agent**.

The core idea:

```text
Pi-like Agent Runtime
    ↓
Aletheia Agent Shell
    ↓
Command Registry + Permission Layer
    ↓
Aletheia Trading Core
    ↓
Risk / Execution / Knowledge / Dashboard
```

Pi should not directly control trading execution. Instead, it should be embedded inside a trading-native safety shell.

---

## 2. Why This Is Needed

Aletheia is not just a trading bot. It is intended to be:

```text
Local-first
File-based
Trading-as-Git
AI-native
Self-learning
Self-evolving
Risk-controlled
Dashboard-visible
```

The system needs a central “mind” that can operate across the entire local workspace.

Without this top-level agent, the product tends to become:

```text
many agents
many logs
many panels
many tasks
but no unified operator
```

The result is:

- debugging requires the user to manually inspect internal files;
- the user has to use an external AI to understand local system state;
- micro-agents may run, but coordination feels weak;
- the Command Center cannot become a real control surface;
- self-learning exists as isolated reports rather than as a coherent loop.

The missing layer is not another trading strategy agent. It is a **system agent**.

---

## 3. What OpenAlice Gets Right

OpenAlice is the closest competitor and reference point. Its public positioning is local-first AI trading: it runs on the user’s machine because trading involves private keys and real money. Its docs describe major concepts such as Unified Trading Account, Trading-as-Git, guard pipeline, account snapshots, and AI interaction with unified account workspaces rather than raw brokers.

Important OpenAlice ideas to learn from:

### 3.1 Local-first trading core

OpenAlice emphasizes that the trading agent runs locally because trading credentials and real funds cannot be safely outsourced.

**Aletheia should keep this same principle.**

### 3.2 File-driven control

OpenAlice’s blog describes its File-Driven Agent design: humans and AI can control behavior by reading and modifying files. This makes the system inspectable, editable, backup-able, and version-control friendly.

**Aletheia should preserve this:**

```text
Markdown for research and reviews
JSON/JSONL for events and structured logs
SQLite for indexes and local state
Git optional for versioning
```

### 3.3 Trading-as-Git

OpenAlice’s model of staging orders, committing with a message, and pushing to execute is highly relevant.

**Aletheia should implement a trading-native version:**

```text
Opportunity
  ↓
Proposal
  ↓
RiskDecision
  ↓
TradingCommit
  ↓
Paper/Demo Execution
  ↓
Review
```

### 3.4 Guard pipeline

OpenAlice places guard checks before execution.

**Aletheia should keep a stricter form:**

```text
Agent can suggest.
Command Registry can route.
Risk Engine can reject.
Execution Gateway can execute only valid proposals.
```

### 3.5 Chat / Ask as product core

OpenAlice treats the AI interaction layer as central, not secondary.

Aletheia’s Command Center should do the same: it should not be a generic chat box. It should be the user’s primary interface for asking, commanding, diagnosing, and delegating.

Practical UI consequence: **do not treat `Ctrl+K` as the System Operator itself**. A command palette is a good
launcher, but the real Operator surface should be a dedicated Command Center workspace/page with persistent
sessions, evidence, trace, and state.

---

## 4. What OpenAlice Should Not Be Copied For

OpenAlice is useful as inspiration, but Aletheia should not copy it blindly.

### 4.1 Do not build only an external agent harness

OpenAlice appears to lean heavily into the idea of a local agent shell/harness around trading modules.

Aletheia should go deeper:

```text
OpenAlice:
  agent shell + trading harness

Aletheia target:
  trading-native agent runtime + safety shell + knowledge lifecycle
```

### 4.2 Do not let the agent directly own trading authority

The top-level agent must not directly change live mode, hard risk limits, credentials, or execution gateway code.

### 4.3 Do not rely on one external CLI as the only brain

Claude Code / Codex / Pi-like tools can be powerful, but Aletheia should own its **Agent Runtime abstraction**.

Pi can be a reasoning/runtime engine, but the product should not be locked to any single CLI.

---

## 5. Why Pi Is Important

Pi is not just another coding agent. It is better understood as a minimal, extensible **agent harness**.

The public Pi repository describes a monorepo containing:

- `pi-coding-agent`: interactive coding agent CLI;
- `pi-agent-core`: agent runtime with tool calling and state management;
- `pi-ai`: unified multi-provider LLM API;
- `pi-tui`: terminal UI library.

Armin Ronacher’s writeup describes Pi as having a tiny core, four basic tools, an extension system, persistent session state, hot reloading, and session trees. This makes it useful not only as a coding agent, but as a foundation for building custom agentic systems.

The key insight:

> Pi is valuable because it treats the agent as something that can extend its own working environment.

This maps directly to Aletheia’s goal:

```text
Aletheia should be able to learn,
write research notes,
generate strategy drafts,
update paper playbooks,
review its own failures,
and evolve its operating knowledge.
```

---

## 6. The Critical Pi Safety Issue

Pi’s own repository states that Pi does **not** include a built-in permission system restricting filesystem, process, network, or credential access. By default, it runs with the permissions of the user/process that launched it.

This is acceptable for a coding agent in a sandbox. It is dangerous for a trading system.

Therefore:

```text
Never give raw Pi runtime direct access to:
- API keys
- live trading
- credential files
- execution gateway internals
- hard risk limit files
- wallet/private key material
```

Pi must be wrapped by Aletheia’s own safety shell.

---

## 7. Proposed New Layer: Aletheia Agent Shell

The Aletheia Agent Shell is the top-level system agent layer.

It is not a trading strategy agent. It is the **system operator agent**.

### Responsibilities

It should be able to:

- read system context;
- answer user questions from the Command Center;
- inspect agent activity;
- diagnose failed tasks;
- route tasks to specialist agents;
- create or update research notes;
- create paper strategy drafts;
- generate improvement proposals;
- trigger low-risk commands;
- request high-risk changes through proposals;
- maintain local to-do lists;
- summarize system health;
- consolidate knowledge after daily runs.

### It should not be able to:

- directly open live trading;
- directly modify API keys;
- directly modify hard risk limits;
- directly bypass Risk Engine;
- directly place live orders;
- directly rewrite execution gateway or credential logic;
- directly delete Knowledge Base content.

---

## 8. Core Architecture

```text
┌──────────────────────────────────────────────┐
│ User / Dashboard / Command Center             │
└──────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────┐
│ Aletheia Agent Shell                          │
│ - App context assembly                        │
│ - Conversation/session state                  │
│ - Task planning                               │
│ - Command intent generation                   │
│ - Workspace read/write                        │
│ - Specialist agent delegation                 │
└──────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────┐
│ Pi-like Reasoning Runtime                     │
│ - Pi agent core or compatible runtime         │
│ - extension/session concepts                  │
│ - model provider abstraction                  │
│ - structured outputs                          │
└──────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────┐
│ Command Registry + Permission Layer           │
│ - command schemas                             │
│ - permission modes                            │
│ - risk level classification                   │
│ - audit logging                               │
│ - safe routing                                │
└──────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────┐
│ Aletheia Trading Core                         │
│ - scanners                                    │
│ - opportunity pool                            │
│ - strategy lifecycle                          │
│ - risk engine                                 │
│ - execution gateway                           │
│ - ledger                                      │
│ - knowledge base                              │
└──────────────────────────────────────────────┘
```

---

## 9. Role in the Agent Pyramid

Aletheia should shift from:

```text
Many micro-agents + fixed backend
```

to:

```text
Apex Agent
  ↓
Specialist Agents
  ↓
Deterministic Trading Core
```

### Level 0 — Gateway Runtime

Runs the local system:

```text
ats gateway start
ats gateway stop
ats gateway status
ats gateway pause
ats gateway resume
```

### Level 1 — Apex Agent Shell

Top-level system manager.

It sees:

```text
system state
agent state
market state
knowledge state
strategy state
risk state
task state
```

### Level 2 — Specialist Agents

Examples:

```text
Crypto Core Agent
News Intelligence Agent
Risk Critic Agent
Strategy Architect Agent
Dreaming Agent
Review Agent
```

### Level 3 — Deterministic Services

Examples:

```text
Bitget adapter
market data store
risk engine
execution gateway
ledger
SQLite repositories
file workspace
```

### Level 4 — Hard-Control Safety Layer

Examples:

```text
live lock
risk hard limits
API key isolation
audit log
kill switch
permission mode
```

---

## 10. Command Registry

The Command Registry is the most important safety component.

The Agent Shell must never directly mutate critical state. It must issue typed commands.

### Command categories

```text
Read Commands
- get_system_status
- get_agent_activity
- get_market_snapshot
- get_positions
- get_strategy_lifecycle

Low-risk Write Commands
- create_research_note
- create_daily_review
- create_strategy_draft
- update_paper_playbook
- mark_task_done

Controlled Operational Commands
- run_market_scan
- rerun_failed_agent_task
- pause_agents
- resume_agents
- set_symbol_observe_only
- create_paper_experiment

Proposal Commands
- propose_strategy_promotion
- propose_risk_boundary_change
- propose_live_limited_enablement

Forbidden Direct Commands
- modify_api_key
- enable_live_directly
- bypass_risk_engine
- place_live_order_directly
- modify_execution_gateway
- hard_delete_knowledge
```

### retire_strategy / demote_strategy — Phase 4d 间接执行

> ⚠️ **实际实现注意**：§9 的 Level 1 分类（Apex Agent Shell 直接自动操作）对
> `retire_strategy` / `demote_strategy` 并不准确。
> 代码中这两个动作均通过 `pi_decisions` 表**间接执行**（Phase 4d 模式）：
> `_action_retire_strategy`（`operator_loop.py:552`）和
> `_action_demote_strategy`（`operator_loop.py:583`）只向表中写入
> `recommend_retire` / `recommend_demote` 记录，由 System Operator 消费后才真正执行策略生命周期变更。
> 直接退出/降级不发生；`_execute_level1_action` 调用这两个函数时，返回 `status: "recommended"` 而非 `status: "executed"`。

### loosen_confidence / increase_sizing / widen_stop_loss — Level 分类不一致（已知待清理项）

> ⚠️ **代码不一致**：`loosen_confidence`、`increase_sizing`、`widen_stop_loss` 三个动作
> 在 `_LEVEL_2_ACTIONS` 集合中定义（`operator_loop.py:265-267`，**权威分类**，需要 proposal），
> 但同时也出现在 `_execute_level1_action` 的分支中（`operator_loop.py:526-535`）。
> 以 `_LEVEL_2_ACTIONS` 为准——这三个动作属于 Level 2，不能由 Operator 直接自动执行。
> `_execute_level1_action` 中的对应分支属于历史遗留，应在后续清理时移除。
> 本文档 §9 中描述的 "Level 1 auto operations" 不包含这三个动作。

### Permission behavior

```text
Read Only:
  only read commands

Research:
  read + research notes + summaries

Paper Auto:
  research + paper experiments + paper strategy updates

Allow Editing:
  paper playbook and prompt drafts, code-change proposals

Auto:
  controlled automation, but still no direct live unlock
```

---

## 11. Command Flow

> ⚠️ **retire/demote 的实际流程**：对于 `retire_strategy` / `demote_strategy` 命令，
> Operator 执行后不立即修改策略状态，而是向 `pi_decisions` 表写入推荐记录（Phase 4d 间接执行，
> `operator_loop.py:553-578`）。System Operator 在下次轮询时消费该记录并完成实际生命周期变更。
> 命令返回 `status: "recommended"`，不是 `status: "executed"`。

```text
User or scheduler asks:
  "Why did the ETH short proposal fail?"

Aletheia Agent Shell:
  1. loads app context
  2. reads proposal and risk decision
  3. reads related market state and agent logs
  4. summarizes cause
  5. optionally creates review note

Output:
  structured answer + linked objects
```

For an operational command:

```text
User:
  "Pause all agents for now."

Agent Shell:
  1. parse command intent
  2. classify as controlled operational command
  3. check permission mode
  4. call pause_agents
  5. write audit event
  6. update Dashboard state
```

For a risky command:

```text
User:
  "Enable live mode and let it trade."

Agent Shell:
  1. classify as high-risk
  2. refuse direct execution
  3. create LiveEnablementProposal
  4. require explicit user action in Dashboard/CLI
```

---

## 12. App Context Provider

The Agent Shell needs a clean way to understand the whole system.

Implement an `AppContextProvider` that returns compact, structured context.

### Context sections

```text
SystemContext:
  gateway state
  permission mode
  live lock
  pause state
  model provider
  current time

MarketContext:
  BTC/ETH/SOL snapshots
  recent volatility
  funding
  spot/futures mode

AgentContext:
  active agent runs
  failed agent runs
  recent tool calls
  current scheduled tasks

TradingContext:
  positions
  orders
  recent fills
  proposals
  paper/live state

StrategyContext:
  lifecycle states
  active paper strategies
  candidates
  retired strategies

RiskContext:
  daily loss
  exposure
  hard limits
  risk warnings

KnowledgeContext:
  latest research notes
  dreaming logs
  risk lessons
  deprecated beliefs
```

The Agent Shell should not dump the whole database into the prompt. It should load only summarized context, and retrieve details on demand.

---

## 13. Workspace Model

Aletheia remains file-based and local-first.

Recommended workspace:

```text
data/
  app.db
  config/
  logs/
  reports/
  knowledge/
  strategies/
  agent_shell/
    sessions/
    tasks/
    todos/
    scratch/
    extensions/
```

### Agent Shell writable areas

The Agent Shell may freely write:

```text
data/agent_shell/scratch/
data/agent_shell/todos/
data/reports/
data/knowledge/research_notes/
data/knowledge/dreaming_logs/
data/strategies/drafts/
```

The Agent Shell may not directly write:

```text
data/config/credentials.*
data/config/risk_hard_limits.*
execution gateway source files
live trading state files
```

---

## 14. Session Model

Borrow from Pi’s session concepts.

Aletheia sessions should support:

```text
conversation history
task-specific context
custom state messages
tool call history
branching or side-task sessions
summaries
```

Session types:

```text
command_session:
  user-driven Command Center interaction

diagnostic_session:
  debugging failed agent/system tasks

dreaming_session:
  offline consolidation and self-learning

strategy_session:
  strategy draft or paper experiment design

review_session:
  daily/trade/strategy review
```

This prevents one large global conversation from becoming polluted.

---

## 15. Extensions and Self-Improvement

Pi’s extension idea is useful, but Aletheia should restrict it.

### Allowed early extensions

```text
/todos
/review-agent-run
/summarize-market
/explain-proposal
/create-research-note
/create-paper-experiment
```

### Later extensions

```text
/news-research
/strategy-diff
/prompt-review
/risk-scenario
/knowledge-consolidate
```

### Forbidden extension behavior

Extensions must not:

```text
read credentials
place live orders
modify hard risk limits
disable live lock
disable audit logs
delete knowledge
```

---

## 16. Integration Strategy

Do not immediately replace all existing agents.

Use staged integration.

### Stage 1 — Agent Shell as Read-Only Supervisor

Implement:

```text
AppContextProvider
CommandIntent parser
Command Registry
read-only commands
Command Center integration
Agent Activity explanation
```

The Agent Shell can answer:

```text
What is running?
What failed?
What is the current risk state?
What did the system do today?
```

### Stage 2 — Low-Risk Write Capabilities

Allow:

```text
create research note
create review report
create todo
create improvement proposal
create paper strategy draft
```

### Stage 3 — Operational Control

Allow:

```text
pause/resume agents
rerun failed tasks
run market scan
mark symbol observe-only
create paper experiment
```

### Stage 4 — Specialist Agent Delegation

Agent Shell delegates to:

```text
Crypto scanner
News scanner
Risk critic
Strategy architect
Dreaming agent
Review agent
```

The top-level Agent Shell tracks progress.

### Stage 5 — Pi Runtime Deep Integration

Evaluate whether to:

```text
embed pi-agent-core
reuse pi-ai provider layer
implement Pi-compatible extensions
or maintain a custom Aletheia runtime inspired by Pi
```

### Stage 6 — Self-Extension Sandbox

Allow the agent to propose new extensions in a sandbox.

Flow:

```text
extension idea
  ↓
write draft extension
  ↓
run tests in sandbox
  ↓
generate extension proposal
  ↓
human approval
  ↓
enable extension
```

---

## 17. Relationship to Existing Specialist Agents

Existing agents should not be deleted.

They should become tools or delegates under the Apex Agent.

```text
Apex Agent Shell:
  "Run a crypto scan and summarize risks."

Delegates:
  CryptoCoreAgent
  RiskCriticAgent
  OpportunitySynthesizer

Returns:
  structured result to Apex Agent Shell
```

This turns disconnected agents into a managed team.

---

## 18. Handling Small Problems Automatically

The user specifically wants the top-level agent to handle small issues by itself.

Allowed auto-fix examples:

```text
retry failed non-trading task
clear stale UI task state
regenerate malformed research note
re-run failed LLM structured output once
create diagnostic report
mark data source degraded
summarize stack trace
open todo item
```

Not allowed auto-fix examples:

```text
modify execution gateway behavior
alter risk limits
change live trading mode
change credentials
place live orders
silently disable failing safety checks
```

The rule:

```text
Auto-fix is allowed for observability, notes, reports, and paper workflow.
Auto-fix is not allowed for money, credentials, hard risk, or execution semantics.
```

---

## 19. Dashboard Changes Needed

The existing Command Center should connect to the Agent Shell.

`Ctrl+K` / command palette may open or prefill this surface, but should remain a secondary shortcut rather
than the primary long-form operating environment.

### Required UI elements

```text
Command Center:
  natural language input
  command preview
  tool/command call trace
  result cards

Agent Activity Timeline:
  shows top-level agent actions and delegated sub-agent tasks

Right Inspector:
  shows details for selected task, command, proposal, strategy, or report

System Status:
  shows Agent Shell state:
    idle
    thinking
    running command
    waiting for permission
    failed
    paused
```

### Important UX rule

The user should always know:

```text
Is the Agent Shell active?
What is it doing?
What did it just do?
Did it mutate state?
Was the action audited?
Can I undo or inspect it?
```

---

## 20. Data Model Additions

Add or extend local SQLite/JSONL entities.

### Tables / records

```text
agent_shell_sessions
agent_shell_messages
agent_shell_commands
agent_shell_tasks
agent_shell_artifacts
command_audit_events
extension_registry
extension_runs
```

### File artifacts

```text
data/agent_shell/sessions/*.jsonl
data/agent_shell/tasks/*.md
data/agent_shell/todos/*.md
data/agent_shell/scratch/*.md
data/agent_shell/extensions/*.json
```

---

## 21. Structured Outputs

All Agent Shell actions should use typed outputs.

Examples:

```json
{
  "type": "command_intent",
  "intent": "pause_agents",
  "risk_level": "low",
  "requires_confirmation": false,
  "arguments": {}
}
```

```json
{
  "type": "diagnostic_report",
  "title": "ETH scanner failed due to invalid Bitget response",
  "severity": "medium",
  "suggested_action": "retry_scan",
  "can_auto_fix": true
}
```

```json
{
  "type": "strategy_draft",
  "symbol": "BTCUSDT",
  "market": "bitget_futures_demo",
  "thesis": "short-term volatility compression breakout",
  "status": "draft",
  "allowed_next_step": "paper_experiment"
}
```

---

## 22. Failure Handling

The Agent Shell must be robust.

### If LLM output is invalid

```text
retry once
then save failure artifact
show failure in Dashboard
do not mutate trading state
```

### If a command fails

```text
record command_failed
summarize error
create diagnostic task
do not loop infinitely
```

### If the Agent Shell itself fails

```text
system continues without shell
trading core remains safe
Dashboard shows shell degraded
```

The Agent Shell is powerful, but it must not be a single point of failure for safety.

---

## 23. Recommended First Implementation Milestone

Build only this first:

```text
Aletheia Agent Shell v0
- Read app context
- Answer Command Center questions
- Explain agent runs
- Explain proposals
- Pause/resume agents
- Create research notes
- Create diagnostic todos
- Audit every action
```

Do **not** implement yet:

```text
self-writing extensions
live trade management
code modification
automatic strategy promotion
direct execution gateway edits
```

---

## 24. Claude Code / AI Agent Implementation Prompt

Use this prompt to implement the first version.

```text
Implement Aletheia Agent Shell v0.

Goal:
Create a top-level system agent that can understand Aletheia app context, answer user questions, inspect agent activity, and issue safe commands through a Command Registry.

Do not add new trading strategies.
Do not modify execution gateway semantics.
Do not enable live trading.
Do not give the agent access to credentials.

Required modules:
1. AppContextProvider
2. AgentShellRuntime
3. CommandRegistry
4. PermissionPolicy
5. AgentShellSessionStore
6. AgentShellCommandAudit
7. Command Center API endpoints
8. Dashboard event integration

Initial commands:
- get_system_status
- get_agent_activity
- get_market_snapshot
- get_positions
- explain_agent_run
- explain_proposal
- pause_agents
- resume_agents
- create_research_note
- create_diagnostic_todo
- create_improvement_proposal

Permission rules:
- default mode: Research
- live mode cannot be changed by Agent Shell
- credentials cannot be read
- hard risk limits cannot be edited
- execution gateway cannot be modified
- all commands must be audited

LLM integration:
- use existing model provider layer if present
- otherwise implement minimal provider adapter
- all outputs must be structured JSON
- invalid outputs must not mutate trading state

UI integration:
- Command Center sends user message to Agent Shell
- show command preview
- show command results
- show command trace in Agent Activity Timeline
- Right Inspector can inspect Agent Shell sessions and commands

Definition of done:
- user can ask "what is the system doing?"
- user can ask "why did this proposal fail?"
- user can pause/resume agents from natural language
- agent can create a research note
- agent can create a diagnostic todo after a failed run
- every action is logged
- no live trading or credential access is possible
```

---

## 25. Long-Term Vision

After v0 is stable, Aletheia can evolve toward:

```text
A self-managing local trading OS
with a Pi-like top-level agent
that supervises specialist agents,
learns from market experience,
maintains its own knowledge base,
generates paper strategies,
reviews failures,
and evolves safely under hard risk boundaries.
```

The product difference versus OpenAlice:

```text
OpenAlice:
  file-driven trading agent harness

Aletheia:
  trading-native agent runtime with hard-control safety,
  strategy lifecycle,
  knowledge/dreaming loop,
  and top-level Pi-like system agent.
```

---

## 26. References

> **[2026-07-01 注]** earendil-works/pi 是 coding CLI（与本项目无代码关联），列于此仅供历史参考。
> 决策详见 `DECISIONS.md §2026-07-01`。

- earendil-works/pi (coding CLI, unrelated to Aletheia): https://github.com/earendil-works/pi
- Pi project website: https://pi.dev/
- Armin Ronacher, “Pi: The Minimal Agent Within OpenClaw”: https://lucumr.pocoo.org/2026/1/31/pi/
- OpenAlice repository: https://github.com/TraderAlice/OpenAlice
- OpenAlice docs: https://www.openalice.ai/docs
- OpenAlice blog, “We Open-Sourced the Core of TraderAlice”: https://traderalice.com/blog/openalice-we-open-source-traderalice
