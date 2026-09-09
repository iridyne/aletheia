> Archived OpenWiki snapshot from `b7ec0d670`. This page is historical context,
> not current code, configuration, execution authority or acceptance criteria.

---
type: scratch
title: OpenWiki Update Plan
description: Plan to fix hallucinated pages, create missing pages, and repair broken links.
tags: [planning, skeleton]
---

# OpenWiki Update Plan

## Critical Fixes (hallucinated content)

### DELETE
- `runtime/scheduler.md` — duplicates architecture/scheduler.md, both inaccurate. Consolidate into architecture/scheduler.md.

### REPLACE (factually wrong)
1. `runtime/engine.md` — references non-existent `aletheia/runtime/` package. Real runtime is `agent_runtime/` + `agent_shell/`.
2. `runtime/system-operator.md` — same hallucination. Real System Operator is `agent_runtime/pi_daemon.py` + `agent_shell/trading_decision.py`.
3. `components/learning-system.md` — references non-existent `learning/stages.py`, `LearningStage` enum (L0-L4). Real lifecycle is `shadow → paper_10 → paper_25 → paper_50 → paper_active` in `learning/lifecycle.py`.
4. `components/learning-stages.md` — same issues.
5. `data-layer/overview.md` — references non-existent `files/schema.py`, fabricated tables. Real schema is in `storage/migrations.py`.
6. `data-layer/migrations.md` — references non-existent `python -m aletheia.data.migrate`, `Migration` class.

### FIX
- `quickstart.md` — broken links to `config.md`, `services/*.md`, `components/universe.md`, `components/portfolio.md`, `testing.md`.
- `testing/test-infrastructure.md` — fix marker descriptions to match conftest.py.

## New Pages to Create

### Architecture
- `architecture/agent-decision-loop.md` — pi_daemon event-driven daemon + wake events + trading_decision
- `architecture/execution-risk-flow.md` — proposal → risk §21.3 → broker routing

### Components
- `components/universe.md` — universe pipeline (scanners, features, allocator, signal emitter)
- `components/portfolio.md` — portfolio management (position manager, PnL, paper account, exit planning)

### Services
- `services/gateway.md` — gateway lifecycle, supervisor, frontdoor
- `services/api-server.md` — FastAPI server, routes
- `services/pi-daemon.md` — PI daemon service
- `services/data-collectors.md` — data collection subsystem

### Config & CLI
- `config/configuration.md` — config system (loader, schema, secrets)
- `cli/cli.md` — CLI reference

### Testing
- `testing/conftest-guards.md` — safety guards from conftest.py

## Verify (keep if accurate)
- `architecture/overview.md` — accurate
- `architecture/layers.md` — accurate
- `architecture/trading-loop.md` — accurate
- `architecture/scheduler.md` — accurate
- `components/risk-engine.md` — verify against real risk engine
- `components/execution-gateway.md` — verify against real gateway
- `components/knowledge-base.md`, `components/market-observer.md`, `components/position-manager.md`, `components/model-provider.md`, `components/serenity-lens.md`, `components/committee.md`, `components/investment-committee.md` — verify
- `data-layer/prediction-markets.md` — verify
- `knowledge-system/`, `regime/`, `frontend/`, `operations/` — verify

## Execution Order
1. Create new pages (architecture, components, services, config, cli, testing)
2. Replace hallucinated pages (runtime/engine, runtime/system-operator, learning-system, learning-stages, data-layer/overview, data-layer/migrations)
3. Delete runtime/scheduler.md
4. Fix quickstart.md links
5. Fix testing/test-infrastructure.md
6. Verify existing pages
