# Aletheia PI Operator Agent Dual-Process Design

> **[历史归档 — 2026-07-01]** 本文档是 2026-06-10 设计阶段的归档文件。
> "PI Operator Agent"已统一更名为"System Operator"（见 `DECISIONS.md §2026-07-01`）。
> 当前权威设计文档：`docs/design/operator_layer.md`。

> For `worktree-dev` only. This is a design and implementation-conversion spec, not an authorization to merge to `master` or relax any trading safety invariant.
>
> Coverage proof for the external PI reference file lives in
> [2026-06-10-pi-operator-agent-design-crosswalk.md](./2026-06-10-pi-operator-agent-design-crosswalk.md).

## 1. Scope And Final Decision

This spec converts `C:\Users\Administrator\Downloads\aletheia_pi_agent_implementation_plan.md` into a repo-specific design for the current Aletheia Trading OS codebase.

Final decisions:

1. **Use a dual-process architecture.**
   - Python remains the trading system of record.
   - A new Node/TypeScript Operator Shell service owns the long-lived PI-style session runtime.
2. **Do not treat `Ctrl+K` as the PI agent product surface.**
   - `Ctrl+K` stays a quick launcher.
   - The real operator experience is a dedicated `Command Center` page.
3. **Do not rebuild the current system from scratch.**
   - Reuse the existing Python `aletheia/agent_shell` package where it already matches the design.
   - Reframe it from “complete shell” into “backend operator substrate + compatibility layer”.
4. **Do not couple this track to the 72h Phase 0 observation machine.**
   - Phase 0 observation continues elsewhere.
   - This checkout may implement the Operator Layer in parallel on `worktree-dev`.
5. **Implement the full reference design, but in Aletheia-native form.**
   - Every concept in the reference plan must land somewhere concrete:
     heartbeat, operator loop, sessions, tasks, command registry, permissions, command center, repair, knowledge/dreaming, strategy lifecycle, tests, and rollout.

## 2. Boundary Being Touched

This design touches the **Operator Layer only**:

- top-level operator runtime
- command surface
- operator-specific storage
- operator-specific UI
- gateway supervision of the operator service
- operator-facing permissions and audit

This design does **not** change:

- trade execution semantics
- live unlock flow
- risk engine authority
- exchange credential handling
- direct order path
- hard risk limits
- the deterministic self-heal already shipped in `agent_runtime/health.py` and gateway supervision

Those stay authoritative and unchanged.

## 3. Current-State Audit Of This Repo

### 3.1 What Already Exists

The repo is not starting at zero. The following pieces already exist and should be treated as base assets:

- Python gateway supervision:
  - `aletheia/gateway/lifecycle.py`
  - `aletheia/_gateway_runner.py`
- FastAPI backend and scheduler bootstrap:
  - `aletheia/server/app.py`
- Existing Command Center entrypoint:
  - `aletheia/server/routes/command.py`
  - `frontend/src/components/CommandPalette.tsx`
- Existing Python `agent_shell` package:
  - `aletheia/agent_shell/runtime.py`
  - `aletheia/agent_shell/registry.py`
  - `aletheia/agent_shell/permissions.py`
  - `aletheia/agent_shell/context.py`
  - `aletheia/agent_shell/sessions.py`
  - `aletheia/agent_shell/audit.py`
  - `aletheia/agent_shell/commands.py`
- Existing storage tables:
  - `agent_shell_sessions`
  - `agent_shell_commands`
- Existing tests:
  - `tests/test_agent_shell.py` already verifies 11 commands and the current permission gate

### 3.2 What The Current Python Agent Shell Actually Is

The current implementation is **not** a persistent PI-style operator runtime.

It is a single-turn request/response loop:

```text
input
  -> context snapshot
  -> intent classification
  -> permission check
  -> handler execution
  -> synthesized response
  -> audit
```

That is valuable, but it is only the seed of the final system.

### 3.3 Current Gaps Relative To The Reference Plan

The missing pieces are structural, not cosmetic:

1. No dedicated Operator Shell service.
2. No gateway-managed dual-process runtime.
3. No persistent operator loop.
4. No operator heartbeat model.
5. No operator task queue.
6. No `agent_shell_messages` table.
7. No `agent_shell_tasks` or `agent_shell_artifacts`.
8. No event stream for operator heartbeat, command, repair, and task events.
9. No dedicated Command Center page.
10. No inspector/recommendation/task panels in UI.
11. No operator-specific CLI commands.
12. No complete permission model from the PI plan.
13. No explicit repair decision framework.
14. No operator-specific recovery contract after crash.

### 3.4 Current Mismatch In Permission Semantics

Current repo state:

```text
Observe
Paper
Full
```

Reference PI plan:

```text
Read Only
Research
Paper Auto
Allow Editing
Auto
```

This mismatch cannot be solved by simple renaming because the current repo uses one global permission mode for broader agent behavior, while the new Operator Layer needs a more nuanced operator-only authority model.

## 4. Approaches Considered

### Approach A: Thin Node Operator Shell + Python Control Plane

Shape:

```text
Frontend -> Python Gateway -> Node Operator Shell
                          -> Python operator control APIs -> existing repositories / risk / execution / knowledge
```

Properties:

- Node owns long-lived PI-style runtime, sessions, streaming, and operator loop.
- Python owns all durable state mutation, audit, registry execution, and trading truth.
- Frontend talks only to Python.
- Node talks to Python over loopback HTTP.

Pros:

- best match for the reference PI runtime model
- keeps Python trading core authoritative
- avoids direct Node access to SQLite mutation and trading code
- clean separation between reasoning runtime and money/safety runtime
- easiest way to add streaming, sessions, and long-lived operator state

Cons:

- more moving parts than a Python-only approach
- requires process supervision work
- introduces API contracts that must be versioned carefully

### Approach B: Python-Only Expansion Of The Existing `aletheia/agent_shell`

Shape:

```text
Frontend -> Python Gateway -> expanded Python operator runtime
```

Pros:

- lower process complexity
- faster first delivery
- reuses more of the current code directly

Cons:

- drifts away from the reference PI runtime shape
- harder to build a clean long-lived shell without bolting many concerns into the backend
- raises the risk of mixing trading authority and operator reasoning inside one process
- makes future PI-compatible extension/session patterns less natural

### Approach C: Fully External Daemon With Minimal Gateway Involvement

Shape:

```text
Frontend -> external shell
Python Gateway -> separate trading core
```

Pros:

- strong process isolation
- very PI-like

Cons:

- wrong fit for Aletheia's current local product shell
- weakens dashboard honesty unless everything is proxied back into the gateway
- increases coordination burden
- makes operator state feel bolted on rather than product-native

### Recommendation

Use **Approach A**.

It is the only option that simultaneously:

- respects the reference PI design,
- preserves Aletheia's Python trading truth,
- keeps all safety-critical mutation in one authoritative backend,
- and gives the Operator Layer its own persistent runtime instead of turning the gateway into a giant mixed-responsibility shell.

## 5. Recommended Target Architecture

### 5.1 Product And Runtime Roles

Use three names consistently:

```text
Product-facing role: Aletheia Operator Agent
Runtime service: Operator Shell
Underlying reasoning adapter: PI Runtime Adapter
```

Do not expose “Pi Agent” as the product role name in the main UI.

### 5.2 Architecture Diagram

```text
┌──────────────────────────────────────────────────────────────┐
│ Dashboard / Command Center / Ctrl+K Launcher                 │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│ Python Gateway (authoritative local control plane)           │
│ - proxy/public API for frontend                              │
│ - operator state cache                                       │
│ - command registry execution                                 │
│ - audit + SQLite writes                                      │
│ - context assembly endpoints                                 │
│ - event stream fanout                                        │
└──────────────────────────────────────────────────────────────┘
                 ▲                                   │
                 │ loopback HTTP / SSE               │ repositories / services
                 │                                   ▼
┌──────────────────────────────────────────────────────────────┐
│ Node/TypeScript Operator Shell service                       │
│ - PI runtime adapter                                         │
│ - operator loop                                              │
│ - session orchestration                                      │
│ - task orchestration                                          │
│ - repair decisioning                                          │
│ - recommendation generation                                   │
└──────────────────────────────────────────────────────────────┘
                                                      │
                                                      ▼
┌──────────────────────────────────────────────────────────────┐
│ Existing Aletheia trading core                               │
│ - risk engine                                                │
│ - execution gateway                                          │
│ - scheduler / worker agents                                  │
│ - knowledge base / dreaming / strategy lifecycle             │
└──────────────────────────────────────────────────────────────┘
```

### 5.3 Authority Rule

The Operator Shell may reason, classify, recommend, and request.

The Python gateway remains the only layer that may:

- mutate structured system state
- write authoritative operator audit rows
- invoke command handlers that touch Aletheia runtime state
- persist operator objects in `app.db`
- bridge to risk, execution, knowledge, and strategy repositories

This is the core safety rule of the dual-process design.

## 6. Process Model And Supervision

### 6.1 Why Supervision Must Sit Above Both Services

If the Node service is spawned inside FastAPI lifespan, Windows orphan behavior becomes a real risk when the inner uvicorn process crashes or is restarted.

Therefore the Operator Shell should be supervised at the **gateway supervisor level**, not as an untracked child inside request-serving code.

### 6.2 Final Supervision Model

`aletheia/_gateway_runner.py` becomes the parent supervisor for two sibling processes:

1. Python uvicorn gateway
2. Node Operator Shell

The supervisor:

- starts both
- captures separate stderr logs
- monitors exit codes
- restarts the Node Operator Shell independently when it crashes
- records degraded state after repeated shell failures
- never restarts trading authority into a more permissive mode

### 6.3 Process Ownership Rules

```text
Supervisor owns lifecycle.
Python gateway owns state truth.
Node shell owns long-lived reasoning state.
Frontend never talks directly to Node.
```

### 6.4 New Config Block

Add a new operator-specific config section instead of overloading current `agent.permission_mode`:

```yaml
operator:
  enabled: true
  bind_host: 127.0.0.1
  port: 8791
  permission_mode: ReadOnly
  heartbeat_interval_sec: 10
  heartbeat_stale_after_sec: 45
  restart_after_sec: 90
  max_restart_failures: 3
  loop_enabled: true
  main_session_id: operator-main
```

Rationale:

- current `agent.permission_mode` already gates broader worker behavior
- operator permissions must become more granular without destabilizing the current trading loop

### 6.5 Global Permission Relationship

Two permission scopes must now coexist:

1. **Worker/Trading permission**
   - existing `agent.permission_mode`
   - continues to gate lower-level autonomous worker behavior
2. **Operator permission**
   - new `operator.permission_mode`
   - gates Command Center and PI operator actions

The operator may never exceed the effective safety ceiling of the system.

Example:

- `operator.permission_mode = Auto`
- `agent.permission_mode = Observe`

Result:

- operator can still investigate and create proposals
- operator cannot cause paper-autonomous worker behavior that the worker layer itself forbids

### 6.6 Operator CLI Surface

Add a dedicated CLI group instead of overloading gateway commands:

```text
aletheia agent status
aletheia agent pause
aletheia agent resume
aletheia agent wake
aletheia agent logs
aletheia agent sessions
aletheia agent session show <id>
```

These commands should read from the same operator status/task/session truth used by the dashboard. They are not separate side channels.

## 7. Python Control Plane Design

### 7.1 Reuse, Do Not Replace, The Existing `aletheia/agent_shell`

The current package should become the Python-side operator substrate.

Target role split:

- keep:
  - `registry.py`
  - `permissions.py`
  - `context.py`
  - `sessions.py`
  - `audit.py`
  - `commands.py`
- reframe:
  - `runtime.py` becomes a legacy single-turn compatibility runtime
- add:
  - operator-specific repositories
  - heartbeat persistence
  - task persistence
  - event fanout
  - HTTP routes for the Node shell

### 7.2 Backend API Surface

Public frontend-facing routes:

```text
GET  /api/operator/status
POST /api/operator/command
GET  /api/operator/sessions
GET  /api/operator/sessions/{id}
GET  /api/operator/tasks
GET  /api/operator/tasks/{id}
GET  /api/operator/recommendations
GET  /api/operator/events/stream
POST /api/operator/pause
POST /api/operator/resume
POST /api/operator/wake
```

Loopback control routes used by the Node service:

```text
POST /api/operator/internal/heartbeat
POST /api/operator/internal/events
POST /api/operator/internal/tasks
PATCH /api/operator/internal/tasks/{id}
POST /api/operator/internal/messages
POST /api/operator/internal/decisions
POST /api/operator/internal/repair-decisions
POST /api/operator/internal/command-intents
POST /api/operator/internal/context/query
POST /api/operator/internal/commands/execute
```

The backend remains loopback-only, but public and internal contracts must still be separated by route intent.

### 7.3 Storage Ownership

The Node shell must not directly write to `app.db`.

Reasons:

- SQLite multi-process writes across runtimes are fragile on Windows
- one system-of-record is easier to audit
- backend code already knows domain invariants
- command execution can stay inside Python repositories

Therefore:

- Node produces typed objects
- Python validates and persists them

### 7.4 Current `/api/command` Compatibility

Migration plan:

1. Keep `POST /api/command` working during transition.
2. Introduce `POST /api/operator/command` as the new authoritative operator route.
3. Once the dedicated Command Center page is live, make `/api/command` a compatibility proxy used only by `Ctrl+K`.

### 7.5 Config Migration Constraint

`aletheia/config/loader.py` currently contains a reverse-compatibility migration that maps newer names back into the old `Observe / Paper / Full` model.

That behavior is correct for the current repo state, but it is incompatible with the final operator design.

Therefore Track 0 must:

1. leave the existing worker permission field backward-compatible,
2. add a separate `operator` config block,
3. migrate only the new operator permission values in the new block,
4. avoid rewriting the worker permission field during operator migration.

## 8. Node Operator Shell Service Design

### 8.1 Why Node Exists

The Node service exists to give Aletheia a real PI-style shell:

- persistent sessions
- streaming turns
- tool orchestration
- operator loop
- stateful long-lived runtime
- future PI-compatible extension patterns

It is not a second trading backend.

### 8.2 Proposed Directory

Create a new top-level workspace:

```text
operator-shell/
  package.json
  tsconfig.json
  src/
    index.ts
    config.ts
    server.ts
    operator_loop.ts
    pi_runtime_adapter.ts
    backend_client.ts
    heartbeat.ts
    session_runtime.ts
    task_runtime.ts
    event_router.ts
    repair_engine.ts
    recommendation_engine.ts
    schemas.ts
```

Avoid putting this inside `frontend/`:

- it is not a UI concern
- it has a separate runtime lifecycle
- it should be supervised independently

### 8.3 Service Responsibilities

The Node service owns:

- the main operator session
- branching investigative sessions
- operator loop wake/sleep behavior
- multi-turn conversation state
- PI-compatible intent generation
- repair/recommendation reasoning
- mode transitions

The Node service does not own:

- trade execution
- direct DB mutation
- config truth
- command handler truth
- risk truth

### 8.4 Operator Modes

Operator mode becomes a first-class state machine:

```text
Observing
Investigating
Repairing
Trading
Reviewing
Dreaming
Paused
Degraded
```

Mode is reported in heartbeat and shown in UI.

### 8.5 Session Types

Support five explicit session kinds:

```text
command_session
diagnostic_session
dreaming_session
strategy_session
review_session
```

These map directly to the reference PI plan and should not be collapsed into one giant conversation stream.

### 8.6 Operator Loop Cadence

The operator loop must be event-driven, not “LLM thinking forever”.

Baseline cadence:

- every 10 seconds:
  - heartbeat update
  - pause/degraded check
  - critical event poll
- every 30 seconds:
  - failed task scan
  - stuck task scan
  - queue review
- every 1-5 minutes:
  - worker health review
  - model/runtime health review
  - data source health review
- every 15-60 minutes:
  - scheduled scan/review opportunities
  - non-critical report refresh
- daily:
  - review generation
  - dreaming consolidation
  - operator memory compaction

## 9. Context Model

### 9.1 Context Builder Strategy

Do not let the Node shell read arbitrary files as its default context source.

The Python backend should provide curated snapshots at multiple granularity levels:

```text
minimal_context
operational_context
trading_context
knowledge_context
repair_context
```

### 9.2 Context Sections

Each full operational snapshot should be assembled from:

- system
- market
- agents
- trading
- proposals
- strategies
- risk
- knowledge
- errors
- operator_state

### 9.3 Current Reuse

`aletheia/agent_shell/context.py` already assembles parts of:

- system
- market
- agents
- trading
- risk
- knowledge
- strategies

That module should be extended, not discarded.

### 9.4 Redaction Rule

No context payload may contain:

- API keys
- raw secrets
- signer data
- credentials file content
- live-unlock secrets

This must be enforced in backend code, not assumed in prompts.

## 10. Command Registry And Safety Classification

### 10.1 Current Registry Baseline

The current registry already has 11 commands:

- read:
  - `get_system_status`
  - `get_agent_activity`
  - `get_market_snapshot`
  - `get_positions`
  - `explain_agent_run`
  - `explain_proposal`
- operational:
  - `pause_agents`
  - `resume_agents`
- low-write:
  - `create_research_note`
  - `create_diagnostic_todo`
  - `create_improvement_proposal`

This matches the original Stage 1 seed and should be preserved as the minimum command foundation.

### 10.2 Registry Expansion Required By The Reference Plan

The full design requires additional typed commands over time:

Read:

- `get_strategy_lifecycle`
- `get_operator_status`
- `get_failed_tasks`
- `get_recommendations`

Low-risk write:

- `create_daily_review`
- `create_strategy_draft`
- `mark_task_done`
- `archive_low_value_notification`
- `mark_strategy_under_review`

Controlled operational:

- `run_market_scan`
- `rerun_failed_agent_task`
- `set_symbol_observe_only`
- `create_paper_experiment`
- `pause_paper_strategy`

Proposal-only:

- `propose_strategy_promotion`
- `propose_prompt_change`
- `propose_schedule_change`
- `propose_live_limited_enablement`
- `propose_risk_boundary_change`

Forbidden:

- `enable_live_directly`
- `modify_credentials`
- `bypass_risk_engine`
- `place_live_order_directly`
- `modify_execution_gateway`
- `change_hard_risk_limits`
- `hard_delete_knowledge`

### 10.3 Risk Classes

Every command must have:

- `risk_class`
- `operator_permission_required`
- `requires_confirmation`
- `requires_proposal`
- `audit_event_type`
- `backend_handler`

### 10.4 The Critical Rule

The Node shell may classify intent.

Only the Python backend may decide whether that intent is:

- executable
- proposal-only
- forbidden
- denied by current permission mode

## 11. Permission Model

### 11.1 Final Operator Permission Enum

Canonical operator permission values:

```text
ReadOnly
Research
PaperAuto
AllowEditing
Auto
```

These should be stored canonically in config and surfaced to UI in human-readable form:

- `Read Only`
- `Research`
- `Paper Auto`
- `Allow Editing`
- `Auto`

### 11.2 Operator Permission Semantics

`ReadOnly`

- read status
- explain runs
- explain proposals
- inspect strategy lifecycle

`Research`

- `ReadOnly` plus research notes, daily reviews, review reports, non-trading summaries

`PaperAuto`

- `Research` plus paper experiments, paper strategy control, observe-only changes, low-risk paper-side operational commands

`AllowEditing`

- `PaperAuto` plus prompt/playbook draft edits, patch proposals, non-critical template edits

`Auto`

- full operator automation for low/medium-risk non-live workflows, but still no direct live unlock, no hard risk change, no credential mutation, no risk bypass

### 11.3 Migration Strategy

Current values must be migrated carefully:

```text
Observe -> ReadOnly
Paper   -> PaperAuto
Full    -> Auto
ReadOnly -> ReadOnly
Research -> Research
PaperAuto -> PaperAuto
AllowEditing -> AllowEditing
Auto -> Auto
```

This migration applies to **new `operator.permission_mode`** only.

Do not silently reinterpret the current worker permission field in a way that changes the running trading behavior.

### 11.4 Why Operator And Worker Permissions Must Be Split

Without a split, one field would simultaneously mean:

- can the paper trader act?
- can the operator write a research note?
- can the operator propose a patch?
- can the operator pause workers?

That is too ambiguous and will create false authority.

## 12. Heartbeat, Tasks, Sessions, And Events

### 12.1 Heartbeat Contract

Heartbeat interval:

- update every 10 seconds
- stale after 45 seconds
- restart candidate after 90 seconds
- degrade after 3 repeated failures

Heartbeat payload:

```json
{
  "agent_id": "operator",
  "status": "online",
  "mode": "Observing",
  "permission_mode": "ReadOnly",
  "is_busy": false,
  "current_task_id": null,
  "current_intent": "monitoring system state",
  "queue_size": 0,
  "last_seen_at": "2026-06-10T12:00:00Z",
  "last_success_at": "2026-06-10T11:59:40Z",
  "last_error": null,
  "model_provider": "openai-compatible",
  "session_id": "operator-main"
}
```

### 12.2 Task Model

The operator loop must create explicit tasks, not just free-form reasoning:

- diagnose failure
- investigate proposal
- generate review
- run dreaming
- create repair proposal
- evaluate strategy state

### 12.3 Session Persistence

Persist three layers:

1. session metadata
2. turn/message history
3. command/task/artifact links

### 12.4 Event Model

At minimum support:

- `operator_started`
- `operator_stopped`
- `operator_degraded`
- `heartbeat_stale`
- `command_received`
- `command_executed`
- `command_rejected`
- `task_started`
- `task_completed`
- `task_failed`
- `repair_attempted`
- `repair_executed`
- `repair_failed`
- `proposal_created`
- `knowledge_updated`
- `dreaming_started`
- `dreaming_completed`

### 12.5 Event Transport

Frontend event feed should use SSE first:

- easier with the existing local dashboard
- simpler than full-duplex WebSocket for this use case
- good match for heartbeat/timeline/recommendation updates

## 13. Storage Model

### 13.1 Keep Existing Table Names

Do not rename the existing `agent_shell_*` tables just to chase terminology purity.

Reason:

- they already exist
- tests already reference them
- renaming adds migration cost without product value

### 13.2 Required Structured Tables

Keep:

- `agent_shell_sessions`
- `agent_shell_commands`

Add:

- `agent_shell_messages`
- `agent_shell_tasks`
- `agent_shell_artifacts`
- `agent_shell_heartbeats`

### 13.3 File Artifacts

Add operator-only append-only artifacts under:

```text
data/agent_shell/sessions/
data/agent_shell/tasks/
data/agent_shell/todos/
data/agent_shell/scratch/
data/agent_shell/extensions/
```

These are not authoritative replacements for `app.db`; they are operator working memory and inspectable artifacts.

### 13.4 Recovery Contract

On restart:

1. load latest heartbeat
2. load main operator session summary
3. load active tasks
4. load unresolved proposals
5. load recent errors
6. resume into `Observing` or `Degraded`

Never resume directly into `Trading` after crash recovery.

### 13.5 Session Compaction

Long-running operator history must be compacted into daily summaries rather than kept as one unbounded active context.

Target artifact:

```text
data/knowledge/operator_memory/daily_summary_YYYYMMDD.md
```

Each compaction summary should include:

- major user instructions
- important repair decisions
- proposal outcomes
- strategy-state changes
- unresolved issues
- follow-up recommendations

## 14. Command Center Product Design

### 14.1 Product Rule

`Ctrl+K` is a launcher.

`Command Center` is the workspace.

### 14.2 Route Design

Add a first-class route:

```text
/command-center
```

Add a visible nav item in the `Operate` group or as a top-level item near `Overview`.

### 14.3 Page Areas

The page should include:

1. Operator state panel
2. conversation / command stream
3. activity timeline
4. recommendation panel
5. context inspector
6. quick actions

### 14.4 Current UI Changes Required

Current repo facts:

- `frontend/src/App.tsx` has no `Command Center` route
- `frontend/src/components/CommandPalette.tsx` is a popup-only surface
- topbar heartbeat currently reflects latest worker run age, not operator heartbeat
- `ActivityTimeline` is hidden

Therefore the required changes are structural:

- add a new page component for `Command Center`
- keep `CommandPalette` as shortcut and page launcher
- move long-form interaction, history, trace, and inspector out of the palette
- replace topbar heartbeat source with operator heartbeat
- revive activity timeline as operator-aware stream, not duplicated run list

### 14.5 UX Honesty Rules

UI must always answer:

- is the operator online?
- what mode is it in?
- what is it currently doing?
- did it execute or only recommend?
- what evidence supports the answer?
- was state mutated?
- was the action audited?

No optimistic fake status is allowed.

## 15. Auto Repair Framework

### 15.1 Philosophy

Auto repair is allowed only for low-risk observability, reporting, and paper-workflow problems.

It is not a license for autonomous trading authority expansion.

### 15.2 Auto-Executable Repairs

Allowed:

- retry failed non-trading task
- restart failed scanner
- regenerate failed report
- repair malformed structured output
- summarize stack trace
- create diagnostic todo
- mark paper strategy under review
- pause malfunctioning paper strategy

### 15.3 Proposal-Only Repairs

Proposal required:

- change prompt/playbook used in production worker flows
- change schedule frequency
- patch non-critical backend logic
- change Command Center behavior
- change strategy lifecycle state toward promotion

### 15.4 Forbidden Repairs

Forbidden:

- change live mode directly
- mutate credentials
- change signer logic
- modify hard risk limits
- change execution gateway semantics
- delete audit logs
- delete knowledge records
- bypass risk engine

### 15.5 Repair Decision Object

Every repair attempt must materialize as a typed decision before execution or proposal generation.

That object is visible in the Command Center and auditable.

## 16. Knowledge, Dreaming, And Strategy Lifecycle Integration

### 16.1 Knowledge Base Integration

The operator may create and summarize knowledge, but only via typed backend commands that preserve `item_type` honesty:

- `research_note`
- `dreaming_log`
- `daily_review`
- `post_trade_review`
- `strategy_memory`

### 16.2 Dreaming Integration

The operator loop should trigger or request dreaming tasks on schedule, then:

- create a dreaming session
- collect recent runs, errors, trade outcomes, and contradictory beliefs
- write typed artifacts through backend commands

### 16.3 Strategy Lifecycle Integration

The operator may:

- inspect lifecycle state
- pause failing paper strategies
- mark a strategy `under_review`
- create paper candidate proposals
- request evaluation
- create live-candidate proposals

The operator may not:

- promote directly to live
- alter live envelope directly
- bypass human approval on live-limited transitions

### 16.4 Extension Sandbox Direction

The reference plan includes a later self-extension sandbox. Aletheia should preserve that direction, but only as a late-stage operator capability.

Allowed early extension shape:

- typed extension manifest
- explicit sandbox-only test run
- proposal artifact
- human approval before enablement

Forbidden extension capabilities:

- credentials access
- live execution access
- risk-limit mutation
- audit-log suppression
- knowledge hard deletion

## 17. Concrete Repo Translation Tracks

This section is the conversion from abstract PI design to concrete implementation areas in this repo.

### Track 0: Compatibility And Control Plane Preparation

Purpose:

- keep the current Python shell working
- introduce operator-specific config, tables, and routes
- avoid breaking the current dashboard

Primary files:

- `aletheia/config/schema.py`
- `aletheia/config/loader.py`
- `aletheia/storage/migrations.py`
- `aletheia/server/routes/command.py`
- new `aletheia/server/routes/operator.py`

### Track 1: Supervisor-Level Dual-Process Runtime

Purpose:

- make the gateway own the Operator Shell lifecycle

Primary files:

- `aletheia/_gateway_runner.py`
- `aletheia/gateway/lifecycle.py`
- new operator-shell launch helpers

### Track 2: Backend Operator APIs And Persistence

Purpose:

- backend endpoints for heartbeat, sessions, tasks, commands, events
- authoritative persistence

Primary files:

- `aletheia/agent_shell/`
- `aletheia/server/routes/operator.py`
- `aletheia/storage/repositories.py`
- `tests/` for new operator persistence and route coverage

### Track 3: Node Operator Shell Service

Purpose:

- long-lived PI-style shell runtime

Primary files:

- new `operator-shell/` workspace

### Track 4: Permission Split And Registry Upgrade

Purpose:

- separate operator permission from worker permission
- expand registry metadata

Primary files:

- `aletheia/config/schema.py`
- `aletheia/agent_shell/registry.py`
- `aletheia/agent_shell/permissions.py`
- `tests/test_agent_shell.py`
- new operator permission tests

### Track 5: Command Center Full Page

Purpose:

- move from palette popup to persistent control room

Primary files:

- `frontend/src/App.tsx`
- `frontend/src/api/client.ts`
- new `frontend/src/pages/CommandCenter.tsx`
- new command center components

### Track 6: Heartbeat, Timeline, Recommendations, Inspector

Purpose:

- real operator visibility

Primary files:

- topbar status components
- event stream client
- activity timeline components
- recommendation panel
- inspector components

### Track 7: Repair Framework

Purpose:

- typed repair decisions
- automatic low-risk repair path
- proposal-only medium-risk path

Primary files:

- Node repair engine
- Python execution classifiers
- operator event/audit persistence

### Track 8: Knowledge / Dreaming / Lifecycle Integration

Purpose:

- full reference-plan coverage of the self-learning, extension-sandbox, and strategy-governance parts

Primary files:

- operator commands
- knowledge routes / repositories
- strategy lifecycle read/write proposal handlers
- extension manifest / proposal persistence

### Track 9: Hardening, Recovery, And Verification

Purpose:

- prove the system fails safe

Primary files:

- route tests
- shell supervision tests
- failure recovery tests
- frontend integration tests where practical

## 18. Verification Plan

### 18.1 Backend Safety Tests

Must prove:

- forbidden commands cannot execute
- operator permissions cannot exceed worker/global safety
- invalid PI output cannot mutate state
- secrets never appear in context payloads
- crash recovery resumes into `Observing` or `Degraded`

### 18.2 Supervision Tests

Must prove:

- gateway starts the Node shell
- heartbeat is recorded
- stale shell is detected
- shell crash triggers restart
- repeated shell crash degrades operator state without enabling anything

### 18.3 Frontend Truthfulness Tests

Must prove:

- topbar heartbeat comes from operator heartbeat
- Command Center shows offline/degraded honestly
- repair suggestions and executed actions are visually distinct
- selected event opens real inspector detail

### 18.4 Reference-Plan Coverage Test

Before this track can be called complete, each major section of the reference file must be accounted for in one of:

- runtime supervision
- operator shell
- context model
- command registry
- permissions
- auto repair
- command center
- sessions
- activity logging
- data models
- API/IPC
- safety invariants
- knowledge/dreaming
- strategy lifecycle
- tests

No section may be silently dropped as “future maybe”.

## 19. Rollout Sequence

The correct rollout order is:

1. split config and storage first
2. add supervision and backend control plane
3. stand up Node shell skeleton with heartbeat only
4. proxy public operator APIs through the gateway
5. migrate UI to real Command Center
6. expand registry and permission model
7. enable repair framework
8. integrate knowledge/dreaming/strategy lifecycle
9. harden failure recovery and test matrix

This avoids building an elaborate UI on top of fake runtime signals.

## 20. Explicit Non-Goals

This spec does not authorize:

- new exchanges
- new live trading modes
- direct live trading by the operator
- remote/cloud deployment
- vector search or web ingestion expansion
- unrestricted shell/code editing by the operator
- broad dashboard redesign outside the Command Center and operator visibility surface

## 21. Final Design Summary

The correct implementation is not “make the current `/api/command` smarter”.

The correct implementation is:

1. keep Python as trading truth and command execution authority
2. add a supervised Node Operator Shell service for PI-style persistent runtime
3. split operator permission from worker permission
4. add a real Command Center page
5. persist heartbeat, sessions, tasks, messages, events, and repair decisions
6. route every mutation through the backend command registry
7. integrate knowledge, dreaming, and strategy lifecycle through typed commands
8. prove the whole thing fails safe under supervision, permission, and recovery tests

That is the repo-specific form of the full PI Agent design.
