---
type: scratch
title: OpenWiki Skeleton Plan
description: Evidence-grounded wiki skeleton plan with accuracy findings and file plan.
tags: [skeleton, planning]
---

# OpenWiki Skeleton Plan for aletheia-trading-os

## Evidence basis (authoritative sources inspected)
- `pyproject.toml` — package def, CLI entry points (`aletheia`/`ato` → `aletheia.main:app`), pytest markers
- `AGENTS.md`, `CLAUDE.md` — repo-wide routing rules
- `README.md` — high-level pitch + runbook pointers
- `docs/operations/development/agent-guides/*.md` (SAFETY_AND_TRUST, STATE_AND_DATA, TASK_ROUTING, DEVELOPMENT_WORKFLOW)
- `docs/design/architecture/SYSTEM_ARCHITECTURE_GUIDE.md`, `docs/design/contracts/agent_contracts.md`, `docs/design/architecture/SYSTEM_MAP.md` — authoritative system map & job registry
- `docs/operations/runbooks/RUNBOOK.md`, `docs/operations/architecture/STABILITY_TARGET_ARCHITECTURE.md`
- Source: `aletheia/agent_runtime/scheduler.py`, `pi_daemon.py`, `wake_events.py`, `health.py`; `aletheia/agent_shell/{trading_decision,context,operator_loop,runtime,intent,executor,registry}.py`; `aletheia/learning/{authority,lifecycle,experiments,models,ledger,verdicts,control,cohorts,behavior_contracts,policy_epochs,registry,evidence}.py`; `aletheia/risk/engine.py`, `risk/kill_switch.py`, `risk/policy.py`; `aletheia/execution/gateway.py`, `paper_broker.py`; `aletheia/config/{loader,schema,secrets}.py`; `aletheia/storage/{sqlite,models,repositories,migrations,event_store,artifact_store,hashing}.py`; `aletheia/data/collectors/`, `data/collector_runner.py`, `news_collector.py`, `news_analyzer.py`; `aletheia/server/app.py`, `server/routes/*.py`; `aletheia/gateway/lifecycle.py`, `_gateway_runner.py`; `aletheia/model_provider/{router,broker,fallback,*.py}`; `aletheia/knowledge/{manager,retrieval,guards,seed_guards,frontmatter,maintenance}.py`; `aletheia/universe/{service,scanners,features,allocator,filters,signal_emitter}.py`; `aletheia/portfolio/*`; `aletheia/counterfactual/*`; `aletheia/economics/*`; `aletheia/files/{jsonl,markdown,git,paths}.py`
- Tests: `tests/conftest.py` (markers + exchange/LLM/DB guards), `tests/specs/*`, representative tests per domain

## Critical accuracy findings (existing wiki pages that are FACTUALLY WRONG and must be corrected)
1. `openwiki/runtime/engine.md` — references non-existent `aletheia/runtime/` package, `runtime/lifecycle.py`, `runtime/event_bus.py`, `EventBus`, `ComponentRegistry`. Real runtime lives in `aletheia/agent_runtime/` + `aletheia/agent_shell/`. **REPLACE content.**
2. `openwiki/runtime/system-operator.md` — same `runtime/` hallucination. System Operator is `agent_runtime/pi_daemon.py` + `agent_shell/trading_decision.py`. **REPLACE content.**
3. `openwiki/runtime/scheduler.md` — duplicates `architecture/scheduler.md` and is inaccurate. **DELETE** (consolidate into architecture/scheduler.md which is already accurate).
4. `openwiki/components/learning-system.md` and `learning-stages.md` — reference non-existent `learning/stages.py`, `LearningStage` enum (L0-L4). Real lifecycle is `shadow → paper_10 → paper_25 → paper_50 → paper_active` in `learning/lifecycle.py` + `learning/authority.py`. **REPLACE content.**
5. `openwiki/data-layer/overview.md` — references non-existent `files/schema.py` and fabricated tables (`market_data`, `market_data_oi`, etc.). Real schema is in `storage/migrations.py` (SCHEMA_SQL). **REPLACE content.**
6. `openwiki/data-layer/migrations.md` — references non-existent `files/schema.py`, `python -m aletheia.data.migrate`, `Migration` class. **REPLACE content.**
7. `openwiki/quickstart.md` — broken links to `config.md`, `cli.md`, `services/*.md`, `components/universe.md`, `components/portfolio.md`, `services/model-provider.md`, `services/runtime-continuity.md`, `services/diagnostics.md`. **Fix links** by creating missing pages.

## Proposed wiki structure

### Top-level
- `quickstart.md` — rewrite broken links + routing table to created pages; keep accurate content.

### `architecture/` (most accurate; ADD 2 missing pages)
- `overview.md` — KEEP/verify
- `layers.md` — KEEP/verify (note: no `aletheia/runtime/` package)
- `trading-loop.md` — KEEP/verify
- `scheduler.md` — KEEP (accurate vs source)
- `data-collector-subprocess.md` — keep/verify
- `agent-shell-command-cycle.md` — keep/verify
- `counterfactual-pipeline.md` — keep/verify
- `economics-tracing-pipeline.md` — keep/verify
- `regime-detection-pipeline.md` — keep/verify
- `runtime-continuity-workflow.md` — keep/verify
- [NEW] `architecture/agent-decision-loop.md` — pi_daemon event-driven daemon + wake events + trading_decision
- [NEW] `architecture/execution-risk-flow.md` — proposal → risk §21.3 → broker routing

### `components/`
- `risk-engine.md` — keep/verify
- `execution-gateway.md` — keep/verify
- `knowledge-base.md` — keep/verify
- `market-observer.md` — keep/verify
- `position-manager.md` — keep/verify
- `model-provider.md` — keep/verify
- `serenity-lens.md` — keep/verify
- `committee.md` / `investment-committee.md` — verify/dedupe
- [NEW] `components/universe.md`
- [NEW] `components/portfolio.md`
- `learning-system.md` — REPLACE (accurate AT-01 lifecycle)
- `learning-stages.md` — REPLACE (correct stages)

### `runtime/` (FIX — rewrite against real agent_runtime/agent_shell)
- `engine.md` — REPLACE → Agent Runtime overview
- `system-operator.md` — REPLACE → System Operator daemon
- (runtime/scheduler.md deleted)

### `data-layer/` (FIX)
- `overview.md` — REPLACE (real schema from migrations.py)
- `migrations.md` — REPLACE (storage/migrations.py)
- `prediction-markets.md` — keep/verify

### `knowledge-system/`, `regime/`, `frontend/`, `operations/` (verify)
- keep existing, verify against source

### `testing/` (fix)
- `test-infrastructure.md` — fix marker description (conftest defines them correctly)
- [NEW] `testing/conftest-guards.md` — safety guards

### [NEW] directories
- `config/configuration.md`
- `cli/cli.md`
- `services/gateway.md`, `services/api-server.md`, `services/pi-daemon.md`, `services/data-collectors.md`

## Skeleton TODO ledger
- [ ] Create missing pages: agent-decision-loop, execution-risk-flow, universe, portfolio, configuration, cli, services/{gateway,api-server,pi-daemon,data-collectors}, testing/conftest-guards
- [ ] Replace: runtime/engine, runtime/system-operator (delete runtime/scheduler), learning-system, learning-stages, data-layer/overview, data-layer/migrations
- [ ] Rewrite quickstart links + routing table
- [ ] Verify all existing pages against source
- [ ] Reconcile internal links
