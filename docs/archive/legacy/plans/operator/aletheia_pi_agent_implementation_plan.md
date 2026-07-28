# Aletheia System Operator Implementation Plan

> **[历史归档 — 2026-07-01]** 原标题"Pi Agent Implementation Plan"，"PI Agent"已更名
> 为"System Operator"。文件名保留。当前架构权威文档：`docs/design/operator/operator_layer.md`。

> Version: v1.0  
> Target System: **Aletheia Trading OS**  
> Scope: Pi Agent / Operator Agent / Agent Shell / Command Center / 24-7 Runtime Integration  
> Intended Reader: Claude Code / Codex / Cursor / AI Coding Agent  
> Primary Goal: 将 Pi Agent 式顶层智能体能力，稳定、安全、可审计地融入 Aletheia 的 Agentic Trading 框架。

---

## 0. Executive Summary

Aletheia 当前已经具备较完整的本地 Agentic Trading OS 形态：

- 本地 Gateway
- Web UI / Dashboard
- 市场扫描模块
- 策略、机会、Proposal、Knowledge Base、Dreaming 等概念
- 多个底层 Specialist Agents
- Trading as Git / file-based / local-first 的产品方向

但当前存在一个核心结构缺口：

> 系统底部有很多执行型 Agent，中间有平台级 harness，但顶部缺少一个持续运行、理解全局上下文、能调度/诊断/修复/复盘/进化整个系统的 Operator Agent。

本实施计划的目标是引入一个 **Pi Agent based Aletheia Operator Agent**，作为整个 Aletheia 金字塔架构的顶层智能体。

该顶层 Agent 的定位不是普通聊天机器人，也不是直接接管交易执行的黑箱，而是：

> 一个常驻的系统级智能操作员，负责观察系统、调度子 Agent、诊断异常、生成修复方案、管理策略生命周期、驱动 Knowledge Base / Dreaming，并通过受控的 Command Registry 与整个 Aletheia 系统交互。

最终结构：

```text
L3: Aletheia Operator Agent / Pi Agent Shell
    - observe, diagnose, delegate, repair, review, dream, evolve

L2: Aletheia Platform Harness
    - Gateway, scheduler, dashboard, storage, command registry, risk engine, execution gateway

L1: Specialist Worker Agents / Tools
    - market scanner, news agent, strategy researcher, risk critic, portfolio reviewer, dreaming agent
```

关键原则：

```text
Pi Agent 负责判断、规划、调度、自我扩展。
Aletheia 负责交易对象、安全边界、审计、风控和执行控制。
Risk Engine / Command Registry 是硬边界。
Pi Agent 不直接拥有真实资金权限。
```

---

## 1. Product Intent

### 1.1 What We Are Building

我们要构建的是一个 **trading-native top-level AI operator**。

它应具备类似“设施中枢 AI”的感觉：

- 持续观察整个交易系统
- 知道所有 Agent、任务、策略、市场和 Proposal 的状态
- 可以接受用户自然语言指令
- 可以主动发现问题并修复低风险问题
- 可以调度底层 Agent 完成任务
- 可以解释系统为什么这样做
- 可以在 Command Center 中与用户进行长期对话
- 可以通过 Dreaming / Knowledge Base 让系统逐步成长

它不是简单 ChatBot。

它不是外部 Claude Code。

它不是直接下单的交易员。

它是：

> **Aletheia 的顶层 Operating Intelligence。**

---

### 1.2 Why Pi Agent

Pi Agent / Pi-like runtime 的价值在于：

- 轻量 Agent core
- 可扩展工具机制
- Session / message log / stateful interaction
- 适合作为本地 Agent Shell 的底层
- 可以天然支持“Agent 自己维护工作空间、生成工具、读写文件、调用命令”的工作模式

但在 Aletheia 中不能裸用 Pi Agent。

原因：

- 交易系统涉及真实资金
- API key / credential / execution gateway 必须被隔离
- Pi-like Agent Shell 如果拥有普通系统权限，可能造成越权、误执行、错误修改
- 自修复、自进化必须有硬边界

所以本方案采用：

```text
Pi Runtime / Pi-like Core
        ↓
Aletheia Agent Shell
        ↓
Command Registry
        ↓
Aletheia Core Services
        ↓
Risk / Execution / Knowledge / Dashboard
```

---

### 1.3 Relationship with OpenAlice

OpenAlice 可以作为产品形态和架构参考，但不直接复制。

OpenAlice 的关键启发：

- local-first
- file-driven workspace
- Trading as Git
- Agent chat as product core
- Trading harness around an agent shell
- broker / news / OpenBB / guard / tool configuration as harness engineering

Aletheia 的目标比 OpenAlice 更 trading-native：

```text
OpenAlice:
  agent shell + trading harness

Aletheia:
  trading-native agent shell + hard risk control + strategy lifecycle + knowledge/dreaming loop
```

Aletheia 不应只是围绕外部 Agent CLI 包一层交易 UI，而应把 Pi-like Agent Shell 内化为顶层 Operator Runtime。

---

## 2. Current Problem Statement

当前系统可能已有以下问题：

1. 底层 Agent 分散，缺少顶层管理者。
2. 用户无法清楚知道 Agent 是否在持续运行。
3. 出现小 bug 时需要外部 AI 或人工进入代码/日志里排查。
4. Web UI 有 Command Center 入口，但还没有形成完整顶层 Agent 操作体验。
5. 多个 Specialist Agents 像金字塔底部工人，但没有一个上层智能体统管整个项目。
6. Agent 之间缺少统一调度、任务状态、修复机制、活动时间线。
7. 系统运行失败时，不知道是模型失败、任务失败、数据失败、工具失败还是权限失败。

本计划要解决：

> 让 Aletheia 从“多个 Agent + 一套平台”升级为“一个顶层 Operator Agent 管理的自运行交易操作系统”。

---

## 3. Core Architectural Model

### 3.1 Pyramid Model

```text
┌─────────────────────────────────────────────┐
│ L3: Aletheia Operator Agent                  │
│     Pi Agent / Pi-like Runtime               │
│     observe / diagnose / delegate / repair   │
│     review / dream / evolve                  │
└─────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────┐
│ L2: Aletheia Platform Harness                │
│     Gateway / Web UI / Command Registry      │
│     Risk Engine / Execution Gateway          │
│     Storage / Knowledge Base / Audit         │
└─────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────┐
│ L1: Specialist Worker Agents                 │
│     Market Scanner / Strategy Agent          │
│     News Agent / Risk Critic / Review Agent  │
│     Dreaming Agent / Data Quality Agent      │
└─────────────────────────────────────────────┘
```

### 3.2 Responsibilities by Layer

#### L1: Specialist Worker Agents

Specialist Agents do concrete work.

Examples:

- scan BTC / ETH / SOL
- classify market regime
- analyze Bitget futures funding / basis
- summarize news
- generate opportunities
- evaluate strategy performance
- review paper trades
- update research notes

They should not manage the whole system.

They should return structured objects such as:

- `MarketSignal`
- `ResearchNote`
- `Opportunity`
- `TradeProposal`
- `StrategyReview`
- `RiskAlert`
- `RepairFinding`

---

#### L2: Aletheia Platform Harness

The platform provides the environment:

- local storage
- file workspace
- SQLite / JSONL / Markdown
- Bitget adapters
- market data providers
- scheduler
- gateway process
- command registry
- permission system
- risk engine
- execution gateway
- dashboard events
- audit logs
- knowledge base

This layer is mostly deterministic code.

It should expose controlled commands to the top-level Agent.

---

#### L3: Aletheia Operator Agent

The Operator Agent is the top-level AI system manager.

It does not directly place orders or modify dangerous core state.

It can:

- observe system state
- inspect running tasks
- detect failure patterns
- trigger safe tasks
- restart safe components
- create repair proposals
- delegate to worker agents
- summarize system health
- review strategy lifecycle
- update knowledge through controlled commands
- respond to user commands in Command Center

It cannot directly:

- enable live trading
- change API key
- change hard risk limits
- bypass execution gateway
- modify credential logic
- directly execute live order
- directly patch production core without proposal

---

## 4. Pi Agent Role Definition

### 4.1 Product Role Name

Use the following naming convention:

```text
Product role: Aletheia Operator Agent
Technical shell: Aletheia Agent Shell
Underlying runtime: Pi Runtime / Pi-like Agent Core
```

Avoid naming the product-facing entity simply “Pi Agent”.

Reason:

- Pi is an implementation detail.
- Aletheia Operator Agent is the product role.
- This keeps future flexibility if Pi runtime changes.

---

### 4.2 Operator Agent Modes

The Operator Agent should expose a visible current mode:

```text
Observing      - passively monitoring system
Investigating  - inspecting an issue or opportunity
Repairing      - performing safe repair or generating repair proposal
Trading        - managing trade/proposal related workflows
Reviewing      - reviewing performance, trades, strategies
Dreaming       - consolidating knowledge and memories
Paused         - suspended by user or system
Degraded       - running with errors or disabled capabilities
```

These modes should be visible in:

- Top Status Bar
- Command Center
- Agent Activity Timeline
- Gateway status CLI

---

### 4.3 Operator Agent Capabilities

#### Observe

Read and summarize:

- gateway status
- model runtime status
- worker agent status
- current tasks
- failed tasks
- market scanner status
- Bitget connection status
- current portfolio state
- opportunities
- proposals
- strategy lifecycle
- risk state
- knowledge base updates
- recent errors

#### Diagnose

Determine:

- what failed
- why it likely failed
- whether it is safe to repair
- whether it needs user attention
- whether it affects trading safety

#### Delegate

Start or request tasks from worker agents:

- scan markets
- review proposal
- run paper experiment
- summarize news
- evaluate strategy
- update research note
- run dreaming consolidation

#### Repair

Low-risk repairs may execute directly.

Medium-risk repairs generate proposal.

High-risk repairs are forbidden or require explicit approval.

#### Review

Generate:

- system health report
- strategy review
- trade review
- agent performance review
- risk review
- daily review

#### Dream

Consolidate:

- old agent runs
- failed proposals
- strategy performance
- risk lessons
- contradicted beliefs
- new hypotheses

#### Evolve

Suggest:

- prompt/playbook improvements
- paper strategy experiments
- knowledge base reorganizations
- dashboard UX improvements
- task scheduling improvements

Evolve should not directly change live execution logic.

---

## 5. 24/7 Runtime Design

### 5.1 Important Principle

The Operator Agent should be 24/7 available, but the LLM should not think 24/7.

Correct model:

```text
Gateway runs 24/7.
Operator Loop runs continuously.
Pi Agent is invoked only when reasoning is needed.
```

Wrong model:

```text
LLM process continuously generates thoughts forever.
```

Reason:

- saves token cost
- reduces drift
- prevents unnecessary self-modification
- improves stability
- makes actions event-driven and auditable

---

### 5.2 Main Runtime Components

```text
Aletheia Gateway
  - starts backend
  - starts dashboard
  - starts Agent Shell service
  - manages lifecycle
  - monitors heartbeat

Agent Shell Service
  - wraps Pi Runtime
  - provides context to Pi
  - executes operator loop
  - exposes local API

Operator Loop
  - checks system state
  - processes events
  - wakes Pi when needed
  - schedules recurring tasks

Command Registry
  - validates commands
  - enforces permissions
  - routes commands
  - writes audit log

Session Store
  - durable Pi message log
  - command history
  - reasoning trace

Watchdog
  - detects stuck agent shell
  - restarts safe services
  - enters degraded mode if needed
```

---

### 5.3 Process Model

Preferred initial implementation:

```text
Python Backend / Gateway
    ↔ local HTTP / WebSocket / JSON-RPC
Node/TypeScript Agent Shell Service
    ↔ Pi Runtime / Pi-like SDK
```

Do not force Pi into the Python process if Pi integration is easier in TypeScript/Node.

Recommended:

- keep process boundary explicit
- communicate over localhost API
- log everything
- avoid shared mutable memory

---

### 5.4 Gateway Responsibilities

The Gateway should provide CLI commands:

```bash
ats gateway start
ats gateway stop
ats gateway restart
ats gateway status
ats gateway logs
ats gateway pause
ats gateway resume
ats gateway open-dashboard
```

Additional Pi-specific commands:

```bash
ats agent status
ats agent pause
ats agent resume
ats agent wake
ats agent logs
ats agent session list
ats agent session show <id>
ats agent repair <task_id>
```

The gateway should:

1. Start all required services.
2. Start Agent Shell service.
3. Check Agent Shell heartbeat.
4. Restart Agent Shell if it crashes.
5. Enter degraded mode after repeated failure.
6. Notify dashboard and logs.
7. Never automatically enable live trading.

---

### 5.5 Heartbeat Model

Agent Shell should update heartbeat periodically.

Suggested interval:

```text
heartbeat interval: 10 seconds
stale threshold: 45 seconds
restart threshold: 90 seconds
critical threshold: 3 repeated failures
```

Heartbeat fields:

```json
{
  "agent_id": "operator",
  "status": "online",
  "mode": "Observing",
  "is_busy": false,
  "current_task_id": null,
  "current_intent": "monitoring system state",
  "last_seen_at": "2026-06-10T12:00:00Z",
  "last_success_at": "2026-06-10T11:59:30Z",
  "last_error": null,
  "queue_size": 3,
  "model_provider": "openai-compatible",
  "session_id": "operator-main"
}
```

Dashboard should use heartbeat to show whether the Operator Agent is alive.

---

### 5.6 Operator Loop Schedule

Suggested baseline loop:

```text
Every 10 seconds:
  - update heartbeat
  - check pause state
  - check critical events

Every 30 seconds:
  - check task queue
  - check stuck tasks
  - check failed tasks

Every 1-5 minutes:
  - inspect worker agent health
  - inspect model runtime health
  - inspect Bitget/data source health

Every 15-60 minutes:
  - trigger scheduled market scans
  - trigger opportunity refresh
  - trigger report updates

Daily:
  - run review tasks
  - run dreaming consolidation
  - update strategy memory
  - update knowledge summaries
```

The loop should be configurable but should have safe defaults.

---

## 6. Agent Shell Design

### 6.1 Responsibilities

Aletheia Agent Shell wraps Pi Runtime and exposes Aletheia-specific context/tools.

Responsibilities:

- maintain Operator Agent session
- prepare app context for Pi
- route user commands from Command Center
- route system events to Pi
- call Command Registry
- store session logs
- manage tool call output
- validate structured results
- stream agent state to dashboard

---

### 6.2 Agent Shell Must Not Do

Agent Shell must not:

- directly call Bitget trading endpoints
- directly access API keys
- directly modify live mode
- directly modify hard risk limits
- directly patch core backend files without proposal
- bypass audit logs
- mutate trading state outside Command Registry

---

### 6.3 Internal Modules

Suggested directory:

```text
agent-shell/
  src/
    index.ts
    server.ts
    pi_runtime.ts
    operator_loop.ts
    context_builder.ts
    command_registry_client.ts
    session_store.ts
    heartbeat.ts
    event_router.ts
    safety_classifier.ts
    tool_adapter.ts
    schemas.ts
    config.ts
```

If implemented inside existing backend, keep equivalent modules:

```text
backend/agent_shell/
  runtime.py
  operator_loop.py
  context_builder.py
  command_registry.py
  session_store.py
  heartbeat.py
  safety_classifier.py
```

---

## 7. App Context Model

### 7.1 Context Builder

Pi Agent should not read random files freely by default.

Instead, provide curated context snapshots.

Context Builder should create:

```json
{
  "system": {
    "gateway_status": "running",
    "permission_mode": "Research",
    "live_mode": "disabled",
    "agent_mode": "Observing",
    "paused": false
  },
  "market": {
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "latest_snapshot_time": "...",
    "data_health": "ok"
  },
  "agents": {
    "running": 5,
    "failed": 1,
    "stuck": 0,
    "recent_runs": []
  },
  "strategies": {
    "active_paper": [],
    "under_review": [],
    "live_limited": []
  },
  "proposals": {
    "pending": [],
    "recent_approved": [],
    "recent_rejected": []
  },
  "risk": {
    "status": "normal",
    "daily_loss": 0,
    "exposure": {},
    "alerts": []
  },
  "knowledge": {
    "recent_notes": [],
    "recent_lessons": [],
    "contradictions": []
  },
  "errors": {
    "recent": []
  }
}
```

The context must be:

- compact
- structured
- current
- safe
- free of secrets

---

### 7.2 Context Types

Provide multiple context levels:

```text
minimal_context:
  for heartbeat, status, command parsing

operational_context:
  for diagnosing system/task issues

trading_context:
  for reviewing opportunities/proposals

knowledge_context:
  for dreaming and memory consolidation

repair_context:
  for auto repair decisions
```

Do not always send full context to Pi.

---

## 8. Command Registry

### 8.1 Purpose

Command Registry is the safety gate between Pi Agent and Aletheia Core.

Pi Agent should output intent.

Command Registry decides whether the intent is:

- directly executable
- requires proposal
- forbidden
- unsupported

---

### 8.2 Command Flow

```text
User/System Event
  ↓
Pi Agent interprets intent
  ↓
CommandIntent
  ↓
Command Registry
  ↓
Safety classification
  ↓
Execute / Proposal / Reject
  ↓
Audit Log + Activity Timeline
```

---

### 8.3 CommandIntent Schema

```json
{
  "intent_id": "cmd_20260610_0001",
  "source": "operator_agent",
  "user_visible_summary": "Restart failed market scanner",
  "command_name": "restart_safe_task",
  "target_type": "agent_task",
  "target_id": "task_market_scan_btc",
  "parameters": {
    "reason": "Task failed 3 times due to transient network error"
  },
  "risk_level": "low",
  "confidence": 0.84,
  "expected_effect": "Restart scanner task without touching trading state",
  "requires_proposal": false,
  "created_at": "..."
}
```

---

### 8.4 Command Classification

#### Low Risk: Auto Executable

Examples:

- read status
- trigger scan
- retry failed task
- restart non-critical scanner
- regenerate failed report
- repair malformed JSON output
- create research note
- archive stale notification
- pause malfunctioning paper strategy
- mark strategy under_review
- update non-critical dashboard note

#### Medium Risk: Proposal Required

Examples:

- modify prompt/playbook used by production agent
- change paper strategy configuration
- create new paper strategy
- change scan frequency
- promote paper strategy to live_candidate
- adjust scheduling policy
- patch non-critical backend logic
- change dashboard behavior that affects operations

#### High Risk: Forbidden or Manual Approval Required

Examples:

- enable live trading
- unlock live mode
- modify API key
- modify hard risk limits
- change execution gateway semantics
- change signer/credential logic
- directly place live order
- bypass risk engine
- delete audit logs
- hard delete knowledge base records

---

### 8.5 Command Registry Table

Maintain a declarative command registry file.

Example:

```yaml
commands:
  get_system_snapshot:
    risk: low
    permission_required: ReadOnly
    execution: direct

  restart_safe_task:
    risk: low
    permission_required: Research
    execution: direct

  create_research_note:
    risk: low
    permission_required: Research
    execution: direct

  create_paper_experiment:
    risk: medium
    permission_required: PaperAuto
    execution: proposal_or_direct_paper

  promote_to_live_limited:
    risk: high
    permission_required: Auto
    execution: proposal_required

  enable_live_mode:
    risk: forbidden
    permission_required: manual
    execution: reject

  change_hard_risk_limit:
    risk: forbidden
    permission_required: manual
    execution: reject
```

---

## 9. Permission Model

Aletheia already uses a Claude Code inspired permission model.

Recommended modes:

```text
Read Only
Research
Paper Auto
Allow Editing
Auto
```

Default mode:

```text
Research
```

---

### 9.1 Read Only

Can:

- read system state
- summarize logs
- answer questions
- inspect knowledge base

Cannot:

- create tasks
- create proposals
- modify files
- run scans

---

### 9.2 Research

Can:

- run read-only scans
- generate research notes
- generate opportunities
- summarize system status
- create review reports

Cannot:

- create paper trades automatically
- modify strategy configs
- execute trading action

---

### 9.3 Paper Auto

Can:

- create paper experiments
- trigger paper strategy runs
- pause failing paper strategies
- auto-generate paper proposals
- run evaluation loops

Cannot:

- touch live execution
- change hard risk limits
- change API key

---

### 9.4 Allow Editing

Can:

- create prompt/playbook drafts
- propose code patches
- edit non-critical notes/templates
- create patch proposals

Cannot:

- directly patch execution/risk/credential code
- directly deploy core changes

---

### 9.5 Auto

Can:

- perform low-risk repair
- manage paper lifecycle
- propose live_limited promotion
- coordinate strategy evolution

Cannot:

- unlock live automatically for first use
- change hard risk limits
- bypass risk engine
- change credentials

---

## 10. Auto Repair Design

### 10.1 Philosophy

Auto Repair should feel like Claude Code Auto mode:

- Agent estimates risk.
- Low-risk commands execute automatically.
- Medium-risk commands produce proposal.
- High-risk commands are blocked.

But unlike generic coding agent, Aletheia must have hard-coded trading safety boundaries.

---

### 10.2 Allowed Auto Repairs

Auto Repair may directly perform:

```text
- retry failed task
- restart failed scanner
- restart non-critical worker agent
- repair invalid JSON result
- regenerate failed report
- re-run failed model call
- clear temporary cache
- mark paper strategy under_review
- pause failing paper strategy
- archive stale low-value notification
- create research note from failed finding
- create repair proposal
```

---

### 10.3 Proposal-only Repairs

Must generate proposal:

```text
- modify strategy parameter used in paper
- modify prompt/playbook used by worker agent
- change scheduled task frequency
- add new scanner
- add new data source
- patch backend non-critical logic
- change Command Center behavior
- promote strategy lifecycle state
```

---

### 10.4 Forbidden Repairs

Never auto repair:

```text
- live mode
- API key
- API secret
- credential storage
- signer logic
- hard risk limits
- execution gateway
- Bitget live order path
- audit log deletion
- risk engine bypass
```

---

### 10.5 Repair Decision Object

```json
{
  "repair_id": "repair_20260610_001",
  "source_event_id": "evt_123",
  "diagnosis": "Market scanner failed due to transient HTTP timeout",
  "risk_class": "low",
  "proposed_action": "restart_safe_task",
  "auto_executable": true,
  "confidence": 0.81,
  "fallback": "create_repair_proposal_if_restart_fails",
  "created_at": "..."
}
```

---

## 11. Command Center Design

### 11.1 Purpose

Command Center is the full interface to the Operator Agent.

Ctrl+K is only a quick entry.

The independent Command Center page is the complete control room.

---

### 11.2 Command Center Should Display

#### 1. Operator State

- online/offline/degraded
- current mode
- current task
- current intent
- queue size
- last tool call
- last error
- model provider status

#### 2. Conversation / Command Stream

- user commands
- Pi Agent responses
- tool calls
- command results
- generated proposals
- failed command attempts

#### 3. Activity Timeline

- all system events
- agent runs
- repairs
- scans
- strategy lifecycle changes
- knowledge updates
- warnings

#### 4. Recommendation Panel

- repair suggestions
- strategy suggestions
- risk warnings
- improvement proposals
- pending user decisions

#### 5. Context Inspector

When an item is selected:

- summary
- evidence
- reasoning
- related objects
- audit trail
- available actions

#### 6. Quick Actions

- pause all
- resume all
- scan now
- run system health check
- run dreaming
- show latest opportunities
- review failed tasks

---

### 11.3 Command Types

Command Center should classify input as:

```text
Ask
  - question about system state

Command
  - directly executable low-risk command

Proposal
  - request to generate a proposal

Investigation
  - request for diagnosis / review

Workflow
  - multi-step task that may delegate to agents
```

Example user inputs:

```text
现在 Pi Agent 在干嘛？
暂停所有 Agent。
为什么 BTC scanner 最近失败？
重新评估最近的 ETH short proposal。
把 SOL 暂时设为 observe-only。
总结过去 24 小时所有 Agent 做了什么。
生成一个关于 BTC funding reversal 的 paper 实验。
```

---

### 11.4 UX Principle

Command Center should feel powerful but controlled.

It should not require the user to remember commands.

It should provide:

- natural language input
- suggested actions
- clickable cards
- auto history
- search
- filters
- command replay

History should be implicit and always available, not a complicated command-only feature.

---

## 12. Session Management

### 12.1 Durable Session Log

Every Operator Agent interaction should be saved.

Location:

```text
data/agent_sessions/operator/
  main.jsonl
  sessions/
    session_<id>.jsonl
```

Each line should include:

```json
{
  "timestamp": "...",
  "session_id": "operator-main",
  "turn_id": "turn_001",
  "role": "assistant",
  "type": "tool_call_result",
  "content": {},
  "related_event_id": "evt_123"
}
```

---

### 12.2 Session Compaction

Long sessions should be compacted.

Daily compaction should create:

```text
data/knowledge/operator_memory/daily_summary_YYYYMMDD.md
```

Include:

- important decisions
- errors
- repairs
- user instructions
- strategy changes
- unresolved issues

---

### 12.3 Context Recovery

On restart:

1. Load latest heartbeat state.
2. Load recent session summary.
3. Load active tasks.
4. Load unresolved proposals.
5. Load recent errors.
6. Resume in Observing or Degraded mode.

Never resume directly into Trading mode after crash.

---

## 13. Event and Activity Logging

### 13.1 Activity Event Schema

```json
{
  "event_id": "evt_20260610_0001",
  "timestamp": "...",
  "source": "operator_agent",
  "event_type": "repair_executed",
  "severity": "info",
  "title": "Restarted failed market scanner",
  "summary": "BTC scanner failed 3 times due to timeout; restarted successfully.",
  "related_objects": [
    {"type": "agent_task", "id": "task_btc_scanner"}
  ],
  "details": {},
  "audit_required": true
}
```

---

### 13.2 Event Types

```text
operator_started
operator_stopped
operator_degraded
heartbeat_stale
task_started
task_completed
task_failed
repair_attempted
repair_executed
repair_failed
proposal_created
proposal_approved
proposal_rejected
strategy_paused
strategy_promoted
knowledge_updated
dreaming_started
dreaming_completed
command_received
command_executed
command_rejected
```

---

### 13.3 Dashboard Timeline

All events should appear in the Activity Timeline.

Filters:

- All
- Operator
- Market
- Strategy
- Repair
- Risk
- Knowledge
- Trading
- Error

---

## 14. Data Models

### 14.1 AgentState

```json
{
  "agent_id": "operator",
  "display_name": "Aletheia Operator Agent",
  "status": "online",
  "mode": "Observing",
  "permission_mode": "Research",
  "current_task_id": null,
  "current_intent": "monitoring system health",
  "is_busy": false,
  "last_seen_at": "...",
  "last_error": null,
  "model_provider": "openai-compatible",
  "session_id": "operator-main"
}
```

---

### 14.2 OperatorTask

```json
{
  "task_id": "op_task_001",
  "task_type": "diagnose_failure",
  "status": "running",
  "priority": "normal",
  "created_by": "operator_loop",
  "assigned_to": "operator_agent",
  "input": {},
  "output": null,
  "started_at": "...",
  "completed_at": null,
  "error": null
}
```

---

### 14.3 CommandIntent

See Section 8.3.

---

### 14.4 RepairProposal

```json
{
  "proposal_id": "repair_prop_001",
  "title": "Patch market scanner retry behavior",
  "description": "Scanner fails after transient network errors. Suggest exponential backoff.",
  "affected_modules": ["market_scanner"],
  "risk_level": "medium",
  "requires_user_approval": true,
  "created_by": "operator_agent",
  "status": "pending"
}
```

---

### 14.5 OperatorDecision

```json
{
  "decision_id": "dec_001",
  "source": "operator_agent",
  "context_summary": "BTC scanner failed repeatedly",
  "decision": "restart_safe_task",
  "reasoning_summary": "Failure appears transient and does not affect execution state",
  "confidence": 0.78,
  "risk_class": "low",
  "command_intent_id": "cmd_001",
  "result": "executed"
}
```

---

## 15. API / IPC Design

### 15.1 Agent Shell Local API

Expose local endpoints:

```http
GET  /agent/status
POST /agent/wake
POST /agent/pause
POST /agent/resume
GET  /agent/sessions
GET  /agent/sessions/{id}
POST /agent/command
GET  /agent/tasks
GET  /agent/tasks/{id}
```

---

### 15.2 Backend Command Registry API

```http
POST /commands/intent
GET  /commands/registry
GET  /commands/history
POST /commands/{id}/approve
POST /commands/{id}/reject
```

---

### 15.3 Event Streaming

Use WebSocket or SSE.

```http
GET /events/stream
```

Events:

- heartbeat updates
- command events
- task events
- repair events
- strategy lifecycle events
- knowledge updates

---

## 16. Operator Loop Pseudocode

```python
while gateway_is_running:
    update_heartbeat()

    if system_paused:
        sleep(heartbeat_interval)
        continue

    critical_events = event_store.get_unhandled_critical_events()
    if critical_events:
        handle_critical_events(critical_events)
        continue

    failed_tasks = task_store.get_failed_tasks()
    if failed_tasks:
        for task in failed_tasks:
            if can_auto_diagnose(task):
                wake_operator_for_diagnosis(task)

    scheduled_jobs = scheduler.due_jobs()
    for job in scheduled_jobs:
        if job.requires_reasoning:
            wake_operator_for_job(job)
        else:
            execute_safe_job(job)

    maybe_run_periodic_health_check()
    maybe_run_dreaming_if_due()

    sleep(loop_interval)
```

---

## 17. Safety Invariants

These must never be broken:

```text
1. Pi Agent cannot directly access API secrets.
2. Pi Agent cannot directly enable live trading.
3. Pi Agent cannot bypass Risk Engine.
4. Pi Agent cannot directly execute live order.
5. Pi Agent cannot modify hard risk limits.
6. Pi Agent cannot hard delete audit logs.
7. Pi Agent cannot hard delete Knowledge Base records.
8. Failed Pi output cannot mutate trading state.
9. Invalid structured output must be rejected.
10. After crash, system resumes into Observing or Degraded, not Trading.
```

---

## 18. Implementation Roadmap

### Phase 0: Audit Existing System

Goal: understand current architecture before modification.

Tasks:

- inspect current backend gateway
- inspect frontend Command Center implementation
- inspect agent task system
- inspect existing logs/events
- inspect current permission model
- inspect current Knowledge Base and Dreaming modules
- identify where Pi Agent can plug in

Output:

```text
PI_INTEGRATION_AUDIT.md
```

Definition of Done:

- Clear map of current components
- Identified integration points
- No code changes yet

---

### Phase 1: Agent State + Heartbeat

Goal: make Operator Agent visible even before full Pi integration.

Implement:

- `AgentState` model
- heartbeat writer
- `/agent/status`
- Dashboard status display
- gateway status CLI integration

Definition of Done:

- Dashboard shows Operator online/offline
- CLI shows agent status
- heartbeat persists to JSONL/SQLite

---

### Phase 2: Agent Shell Service Skeleton

Goal: create a standalone Agent Shell service.

Implement:

- service startup/shutdown
- config loading
- heartbeat loop
- local API
- session log path
- basic no-op operator loop

Definition of Done:

- `ats gateway start` starts Agent Shell
- Agent Shell can be paused/resumed
- no LLM required yet

---

### Phase 3: Command Registry

Goal: create safe command gate.

Implement:

- command registry YAML/JSON
- command classification
- command validation
- command execution routing
- audit log
- low/medium/high/forbidden classification

Definition of Done:

- low-risk command executes
- medium-risk creates proposal
- forbidden command rejected
- all actions logged

---

### Phase 4: Pi Runtime Adapter

Goal: connect Pi-like runtime behind Agent Shell.

Implement:

- `pi_runtime.ts` or equivalent
- model provider config
- structured prompt wrapper
- command intent output parser
- JSON schema validation
- retry/failure behavior

Definition of Done:

- user command can be parsed into `CommandIntent`
- invalid output rejected
- session log records all turns

---

### Phase 5: Operator Loop

Goal: make Operator Agent continuously observe system.

Implement:

- periodic system snapshot
- failed task detection
- stuck task detection
- scheduled health check
- operator wake-up logic
- mode transitions

Definition of Done:

- Operator enters Observing mode
- failed task can trigger diagnosis
- Dashboard timeline shows operator events

---

### Phase 6: Auto Repair

Goal: allow safe repairs.

Implement:

- safe repair commands
- repair decision object
- retry/restart safe task
- invalid JSON repair
- report regeneration
- repair proposal path

Definition of Done:

- low-risk failures repaired automatically
- medium-risk repair becomes proposal
- high-risk action rejected

---

### Phase 7: Command Center Full Page

Goal: complete UI for top-level Agent.

Implement:

- Operator state panel
- conversation stream
- command input
- activity timeline
- recommendation panel
- context inspector
- quick actions
- command history search/filter

Definition of Done:

- Ctrl+K opens quick command
- Full Command Center page shows persistent history
- Commands create visible events
- Selected event opens inspector

---

### Phase 8: Knowledge / Dreaming Integration

Goal: connect Operator Agent to self-learning loop.

Implement:

- daily compaction
- operator memory summaries
- proposal scoring
- trade idea review tags
- deprecated/contradicted knowledge markers
- dreaming task trigger

Definition of Done:

- Operator can summarize day
- Knowledge Base updated through controlled commands
- no hard delete allowed

---

### Phase 9: Strategy Lifecycle Integration

Goal: let Operator Agent manage strategy lifecycle safely.

Implement:

- lifecycle state inspection
- strategy pause command
- mark under_review
- create paper candidate
- paper evaluation request
- live_candidate proposal generation

Definition of Done:

- Operator can pause failing paper strategy
- Operator can create lifecycle proposal
- Operator cannot directly unlock live

---

### Phase 10: Hardening and Tests

Goal: make system reliable.

Implement tests for:

- command classification
- forbidden command rejection
- heartbeat stale detection
- watchdog restart
- invalid Pi output
- session recovery
- auto repair allowed cases
- auto repair forbidden cases
- dashboard event flow

Definition of Done:

- core test suite passes
- forbidden actions cannot execute
- recovery behavior works

---

## 19. Testing Matrix

### Unit Tests

```text
- command registry classification
- permission mode check
- context builder redaction
- heartbeat status calculation
- repair risk classification
- structured output validation
```

### Integration Tests

```text
- gateway starts agent shell
- agent shell writes heartbeat
- command center sends command
- command becomes CommandIntent
- registry executes low-risk command
- medium-risk command creates proposal
- forbidden command rejected
```

### Failure Tests

```text
- Pi runtime unavailable
- invalid JSON from Pi
- stuck task
- stale heartbeat
- repeated failed repair
- backend restart
- agent shell restart
```

### Safety Tests

```text
- attempt enable live through Agent
- attempt change API key through Agent
- attempt bypass risk engine
- attempt delete audit logs
- attempt modify execution gateway
```

All safety tests must fail safely.

---

## 20. Implementation Instructions for Claude Code / AI Agent

When implementing this plan:

1. Do not add new trading features first.
2. Do not modify live execution path except to add explicit safety checks.
3. Do not expose API keys or credentials to Pi Agent context.
4. Do not let Pi Agent run arbitrary shell commands against production workspace.
5. Start with heartbeat, shell service, and command registry before Pi runtime.
6. Keep all changes local-first and compatible with existing Dashboard.
7. Preserve existing dark financial terminal UI language.
8. Add Command Center functionality incrementally.
9. Write tests for every permission/safety boundary.
10. Document all new commands in registry.

Recommended first instruction to coding agent:

```text
Implement Phase 0 and Phase 1 only.
Do not integrate Pi Runtime yet.
Create the AgentState model, heartbeat persistence, /agent/status endpoint, and Dashboard status display.
Then stop and report changes.
```

---

## 21. Non-Goals

This implementation should not:

- rebuild the entire UI
- replace existing backend architecture
- directly fork OpenAlice
- directly embed Claude Code CLI as production runtime
- enable live trading
- implement RWA/xStocks/Prediction Market features
- implement full autonomous code modification
- implement unrestricted shell access
- implement cloud deployment

---

## 22. Final Product Behavior

After full implementation, the user should experience Aletheia like this:

```text
1. User starts gateway.
2. Web UI opens.
3. Operator Agent comes online.
4. Command Center shows Operator status: Observing.
5. Agent continuously monitors system health.
6. Market scanners and worker agents run under platform harness.
7. If a task fails, Operator diagnoses it.
8. Low-risk issue is repaired automatically.
9. Medium-risk issue becomes proposal.
10. High-risk issue is blocked.
11. User can ask Command Center natural language questions.
12. All actions appear in Activity Timeline.
13. Daily Dreaming updates Knowledge Base.
14. Strategies evolve through lifecycle, not random mutation.
15. Live remains locked unless explicitly enabled under rules.
```

The result should feel like:

> A local AI trading operating system with a persistent facility-level intelligence watching over it.

---

## 23. One-Sentence Architecture

```text
Pi-like Runtime gives Aletheia intelligence; Agent Shell gives it app context; Command Registry gives it boundaries; Gateway gives it persistence; Dashboard gives the user control.
```
