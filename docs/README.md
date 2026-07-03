# docs/ — Aletheia Trading OS Documentation

## 4-Layer Documentation Pyramid

```
Layer 1: WHY (产品定义)          — What we're building and why
  └─ product/PRD.md              ← Product requirements, scope, glossary
  └─ product/METRICS.md          ← North-star metric + phase KPIs + Go-Live Scorecard
  └─ product/PHASE_GATES.md      ← Pass/fail checklists for each phase gate

Layer 2: WHAT (系统设计)          — How the system is built
  └─ SYSTEM_ARCHITECTURE_GUIDE.md ← 8-chapter architecture overview
  └─ design/data_model.md        ← Complete SQLite schema (83 tables, 52+ models)
  └─ design/api_reference.md     ← REST API complete reference (~293 endpoints)
  └─ design/agent_contracts.md   ← 14 agents: trigger, reads, writes, LLM usage, chains
  └─ design/*.md                 ← Subsystem designs (operator, data layer, UI, etc.)

Layer 3: HOW (实施规范)           — Rules that must hold while building
  └─ governance/implementation_guide.md ← Product identity, design principles, red lines
  └─ governance/trust_and_change_control.md ← Change control boundaries
  └─ plans/*.md                  ← Step-by-step implementation plans

Layer 4: WHERE (状态追踪)         — Current progress
  └─ status/ROADMAP.md           ← Phased development plan (Phase 0–7+)
  └─ status/PIPELINE_STATUS.md   ← Per-stage implementation status
  └─ status/WORLD_MAP.md         ← Trading cognitive loop + module map
  └─ issues/*.md                 ← Active problem lists (backend/frontend)
```

---

## Quick Start

**New to the project?** Read in this order:
1. [PRD](product/PRD.md) — what we're building (5 min)
2. [ARCHITECTURE](ARCHITECTURE.md) — tech stack, directory map, forbidden zones (5 min)
3. [DEV_SETUP](DEV_SETUP.md) — install, configure, run (5 min)
4. [SYSTEM_ARCHITECTURE_GUIDE](SYSTEM_ARCHITECTURE_GUIDE.md) — deep architecture (15 min)
5. [Implementation Guide](governance/implementation_guide.md) — what rules to follow (10 min)

**Starting a session?** (AI agents: always do this)
1. `git pull --ff-only && aletheia doctor`
2. Read [CURRENT_TASK](_ai/CURRENT_TASK.md) + [AI_HANDOFF](_ai/AI_HANDOFF.md)
3. Check [ROADMAP](status/ROADMAP.md) for current phase

**Making a decision?**
- Feature scope: [PRD §5](product/PRD.md#5-functional-scope) (MVP in / deferred / never)
- Architecture: [Implementation Guide](governance/implementation_guide.md)
- Phase readiness: [PHASE_GATES](product/PHASE_GATES.md)
- Safety boundaries: [Trust & Change Control](governance/trust_and_change_control.md)

---

## Directory Map

| Directory | Purpose | When to add here |
|---|---|---|
| `product/` | Product definition: PRD, metrics, phase gates | Changes to product scope or success criteria |
| `_ai/` | AI session meta-docs (handoff, devlog, decisions, protocol) | Every session reads/updates these |
| `governance/` | Boundaries that must hold while the system changes | Rarely — new constitutional constraints only |
| `operations/` | How to run, maintain, and recover the system | New runbooks or SOPs |
| `status/` | Where we are and where we're going (roadmap, pipeline, world map) | Updates to progress/direction |
| `design/` | Architecture and subsystem design docs | New subsystem designs |
| `issues/` | Active problem lists (backend/frontend) + `archive/` | Append findings; archive when superseded |
| `facts/` | One-time, machine-independent fact records with evidence | Audits/analyses that won't change |
| `references/` | External framework comparisons and reference analyses | New external system deep-dives |
| `plans/` | Implementation plans (step-by-step, for agentic workers) | New feature/subsystem plans |
| `research/` | First-hand research materials and knowledge bases | New research corpora |
| `archive/` | Expired/superseded snapshots (kept for provenance) | Files that are no longer active |

---

## Key Documents

**Product definition (Layer 1):**
- [PRD](product/PRD.md) — what/why/who, functional scope, glossary
- [METRICS](product/METRICS.md) — north-star metric, phase KPIs, Go-Live Scorecard criteria
- [PHASE_GATES](product/PHASE_GATES.md) — pass/fail checklists for each phase

**System design (Layer 2):**
- [SYSTEM_ARCHITECTURE_GUIDE](SYSTEM_ARCHITECTURE_GUIDE.md) — 8-chapter architecture
- [Data Model](design/data_model.md) — all 83 tables, schema, concurrency model
- [API Reference](design/api_reference.md) — all ~293 REST endpoints
- [Agent Contracts](design/agent_contracts.md) — all 14 agents + 30 scheduler jobs

**Context Engineering（AI 可读工程环境）:**
- [ARCHITECTURE](ARCHITECTURE.md) — tech stack, directory map, forbidden zones
- [API_CONTRACT](API_CONTRACT.md) — endpoint conventions, compatibility rules
- [DEV_SETUP](DEV_SETUP.md) — install, configure, commit conventions, DB migration rules
- [TESTING](TESTING.md) — SDD flow, test commands, mock boundaries
- [DEPLOYMENT](DEPLOYMENT.md) — gateway lifecycle, Task Scheduler, health checks

**Governance (Layer 3):**
- [Implementation Guide](governance/implementation_guide.md) — product identity and red lines
- [Trust & Change Control](governance/trust_and_change_control.md) — change-controlled boundaries
- [AGENTS.md](../AGENTS.md) — red lines for AI agents specifically
- [DEV_PROTOCOL](_ai/DEV_PROTOCOL.md) — two-machine intent vs fact separation

**Operations:**
- [RUNBOOK](operations/RUNBOOK.md) — running the system unattended

**Status (Layer 4):**
- [ROADMAP](status/ROADMAP.md) — phased plan with observation gates
- [PIPELINE_STATUS](status/PIPELINE_STATUS.md) — per-stage implementation status
- [WORLD_MAP](status/WORLD_MAP.md) — trading cognitive loop and module map
- [North Star](references/north_star.md) — long-term vision reference
