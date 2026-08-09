---
type: overview
title: Aletheia Trading OS — Quick Start
description: Local-first, AI-native agentic trading system. Paper by default, live behind explicit human unlock.
tags: [overview, quickstart]
---

# Aletheia Trading OS

A local-first, file-based, AI-native, self-learning agentic trading system. Paper trading by default; live trading is strictly gated behind an explicit human unlock.

## Core Architecture

Aletheia is organized around a **durable evidence loop** — market facts, decisions, risk verdicts, fills, outcomes, reviews, and learning evidence remain traceable in local state. Every order flows through the Risk Engine → Execution Gateway pipeline. The **System Operator** is the sole entry-decision authority.

```
┌─────────────┐  ┌───────────┐  ┌──────────────┐  ┌────────┐  ┌───────────┐
│ Market Data │→ │ Observers │→ │ Paper Trader │→ │ System │→ │ Risk +    │
│ Collectors  │  │ (LLM)     │  │ (ranking)    │  │ Operator│  │ Execution │
└─────────────┘  └───────────┘  └──────────────┘  └────────┘  └───────────┘
      ↑                                                                              │
      │                                                                              ▼
      └──────────────────────────────────────── Learning ← PnL ← Outcomes ←───────┘
```

## Key Subsystems

| Subsystem | What it does | Key entry point / source |
|---|---|---|
| **Gateway** | Background supervisor + FastAPI server + front-door proxy | `_gateway_runner.py`, `gateway/lifecycle.py`, `server/app.py` |
| **Scheduler** | 30+ APScheduled jobs across 5 executor pools | `agent_runtime/scheduler.py` |
| **Agents** | Market observer (LLM), paper trader, dreaming, equity/prediction observers, divergence scanner, post-trade reviewer, risk critic, lesson reviewer | `aletheia/agents/` |
| **System Operator** | Event-driven trading decision daemon (sole decision maker) | `agent_runtime/pi_daemon.py`, `agent_shell/trading_decision.py` |
| **Risk Engine** | §21.3 check matrix, kill switch, daily-loss auto-trip | `risk/engine.py`, `risk/kill_switch.py` |
| **Execution Gateway** | Proposal → Risk → Order → Fill (paper/live brokers) | `execution/gateway.py`, `execution/paper_broker.py` |
| **Universe** | Signal scanning, feature extraction, allocation, TTL enforcement | `universe/` |
| **Portfolio** | Position manager (autonomous protective closes), PnL, paper account | `portfolio/` |
| **Learning** | AT-01 measurement experiments, lifecycle, authority resolution | `learning/` |
| **Knowledge Base** | Experience-loop lessons, guards, FTS5 retrieval | `knowledge/` |
| **Model Provider** | Multi-provider LLM router with inference broker + fallback | `model_provider/` |
| **Storage** | SQLite WAL, 52KB models, 274KB repositories, versioned migrations | `storage/` |
| **Committee** | Multi-agent (Trend/Macro/News/Risk + LLM) evaluation system producing structured, auditable opinions with rule-gated explainability and quality-aware position sizing | `aletheia/agents/committee/` |
| **Frontend** | React/Vite dashboard (14 pages incl. AI Jury committee workbench) | `frontend/src/` |
| **Agent Shell** | TypeScript operator shell | `operator-shell/` |

## Task-Routing Table

From a change intent to the owning source, tests, and validation:

| Change area / intent | Source entrypoints/symbols | Focused tests | Validation |
|---|---|---|---|
| Start/stop/monitor gateway | `cli/gateway.py`, `gateway/lifecycle.py` | `test_autostart.py` | `uv run aletheia gateway status` |
| Run doctor / diagnostics | `cli/doctor_cmd.py`, `diagnostics/doctor.py` | `test_doctor.py`, `test_doctor_json.py` | `uv run aletheia doctor` |
| Modify risk policy | `config/schema.py` (ModeRiskPolicy), `risk/policy.py` | `test_risk_engine.py`, `test_risk_checks_full.py` | `uv run pytest tests/test_risk_` |
| Add a new exchange order path | `execution/gateway.py::submit_proposal`, `execution/{broker}_broker.py` | `test_ci_execution_contracts.py` | `uv run pytest -m 'not e2e' tests/test_*.py` |
| Change agent decision logic | `agent_shell/trading_decision.py`, `agent_shell/context.py` | `test_agent_shell.py` | `uv run pytest -m 'not e2e,not slow' tests/test_agent_shell.py` |
| Add a new scheduled job | `agent_runtime/scheduler.py::init_scheduler` | `test_scheduler_timeout_fix.py` | `uv run pytest tests/test_agent_runtime.py` |
| Modify market data collection | `data/collectors/`, `data/ccxt_provider.py` | `test_data_collectors.py` | `uv run pytest tests/test_data_collectors.py` |
| Change paper trading rules | `agents/paper_trader.py`, `agents/paper_trader_ranker.py` | `test_paper_trader_guards.py`, `test_ranker_*.py` | `uv run pytest -m 'not e2e' tests/test_paper_` |
| Add LLM provider | `model_provider/router.py` (PROVIDER_REGISTRY), `model_provider/{type}.py` | `test_model_provider.py`, `test_model_profiles.py` | `uv run pytest tests/test_model_provider.py` |
| Modify DB schema | `storage/migrations.py`, `storage/models.py` | `test_migration_fast_path.py`, `test_storage.py` | `uv run aletheia doctor && pytest tests/test_migration_fast_path.py` |
| Inspect runtime state | `cli/inspect_cmd.py` | `test_inspect.py` (if exists) | `uv run aletheia inspect --help` |
| Audit trail verification | `files/jsonl.py` (verify_audit_chain) | `test_audit_hashchain.py` | `uv run aletheia audit verify` |
| Frontend UI changes | `frontend/src/`, pages route via `App.tsx` | `frontend/src/pages/*.test.tsx` | `cd frontend && npm test` |

## Navigation

### By workflow
- [Trading Loop](architecture/trading-loop.md) — market data → signal → decision → risk → execution → PnL → learning
<!-- openwiki: broken internal link [architecture/agent-decision-loop.md] file "architecture/agent-decision-loop.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- [Agent Decision](architecture/agent-decision-loop.md) — paper trader ranks signals, System Operator decides
<!-- openwiki: broken internal link [architecture/execution-risk-flow.md] file "architecture/execution-risk-flow.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- [Execution](architecture/execution-risk-flow.md) — Risk Engine check matrix → broker routing
- [Scheduler](architecture/scheduler.md) — 30+ jobs, executors, pause semantics

### By domain
<!-- openwiki: broken internal link [components/universe.md] file "components/universe.md" does not exist. Fix the href or restore the target, then delete this comment. -->
<!-- openwiki: broken internal link [components/portfolio.md] file "components/portfolio.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- [Risk Engine](components/risk-engine.md) | [Execution Gateway](components/execution-gateway.md) | [Universe](components/universe.md) | [Portfolio](components/portfolio.md) | [Knowledge Base](components/knowledge-base.md) | [Learning System](components/learning-system.md) | [Committee](components/committee.md) | [Investment Committee System](components/investment-committee.md) | [Serenity Lens](components/serenity-lens.md) | [Position Manager](components/position-manager.md)

### By workflow
- [AI Jury Workbench](frontend/overview.md#ai-jury-committee-workbench) — committee evaluation surface

### By service

<!-- openwiki: broken internal link [services/gateway.md] file "services/gateway.md" does not exist. Fix the href or restore the target, then delete this comment. -->
<!-- openwiki: broken internal link [services/api-server.md] file "services/api-server.md" does not exist. Fix the href or restore the target, then delete this comment. -->
<!-- openwiki: broken internal link [services/scheduler-service.md] file "services/scheduler-service.md" does not exist. Fix the href or restore the target, then delete this comment. -->
<!-- openwiki: broken internal link [services/operator-shell.md] file "services/operator-shell.md" does not exist. Fix the href or restore the target, then delete this comment. -->
<!-- openwiki: broken internal link [services/pi-daemon.md] file "services/pi-daemon.md" does not exist. Fix the href or restore the target, then delete this comment. -->
<!-- openwiki: broken internal link [services/data-collectors.md] file "services/data-collectors.md" does not exist. Fix the href or restore the target, then delete this comment. -->
<!-- openwiki: broken internal link [services/deployment.md] file "services/deployment.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- [Gateway](services/gateway.md) | [API Server](services/api-server.md) | [Scheduler](services/scheduler-service.md) | [Operator Shell](services/operator-shell.md) | [PI Daemon](services/pi-daemon.md) | [Data Collectors](services/data-collectors.md) | [Deployment](services/deployment.md)

## Quick Start Commands

```bash
# Install (development checkout)
uv sync
uv run aletheia-install

# Start the gateway (dashboard at http://127.0.0.1:8787)
aletheia gateway start

# Diagnose the machine's real state
aletheia doctor

# Run focused tests (fast, local only — no network/LLM/real DB)
uv run pytest -m "not e2e and not integration and not slow" tests/test_risk_engine.py

# Run a specific spec (executable specification)
uv run pytest tests/specs/spec_risk_engine.py
```

## Backlog

- Committee page + rule engine updates: see [Committee](components/committee.md) and [Investment Committee System](components/investment-committee.md)
<!-- openwiki: broken internal link [architecture/agent-decision-loop.md] file "architecture/agent-decision-loop.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- Paper trader concurrency tuning (limit=15, max_concurrency=3, total_timeout=240s): see [Trading Loop](architecture/trading-loop.md) and [Agent Decision](architecture/agent-decision-loop.md)

## Configuration

<!-- openwiki: broken internal link [config.md] file "config.md" does not exist. Fix the href or restore the target, then delete this comment. -->
Everything (model provider, exchange credentials, agent runtime, live-trading unlock) is configured inside the dashboard's Settings page. Secrets are stored in the OS keyring or an encrypted file — never in `config.yaml` or logs. See [Configuration](config.md).

## Testing

<!-- openwiki: broken internal link [testing.md] file "testing.md" does not exist. Fix the href or restore the target, then delete this comment. -->
The test suite uses pytest with tiered markers. See [Testing](testing.md) for the full marker reference and how to select tests.

---

## Top-level map

| Concept | Page |
|---|---|
| System overview | [Architecture Overview](architecture/overview.md) |
| Architecture layers | [Architecture Layers](architecture/layers.md) |
| Trading cognition loop | [Trading Loop](architecture/trading-loop.md) |
| Scheduler job registry | [Scheduler](architecture/scheduler.md) |
| AI Jury workbench | [AI Jury](frontend/overview.md#ai-jury-committee-workbench) |
| Investment Committee | [Investment Committee](components/committee.md) |
| Investment Committee System | [Investment Committee System](components/investment-committee.md) |
<!-- openwiki: broken internal link [testing.md] file "testing.md" does not exist. Fix the href or restore the target, then delete this comment. -->
| Testing | [Testing](testing.md) |
