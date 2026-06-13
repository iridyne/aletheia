# docs/ — Aletheia Trading OS Documentation

## Directory Map

| Directory | Purpose | When to add here |
|---|---|---|
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
| `superpowers/` | Superpowers framework artifacts (plans + specs) | Superpowers-generated content |
| `archive/` | Expired/superseded snapshots (kept for provenance) | Files that are no longer active |

## Quick Links

**Start here:**
- [PROJECT_CONTEXT](_ai/PROJECT_CONTEXT.md) — what this project is
- [CURRENT_TASK](_ai/CURRENT_TASK.md) — what we're doing now
- [ROADMAP](status/ROADMAP.md) — where we're going

**Governance:**
- [Implementation Guide](governance/implementation_guide.md) — what the system should become
- [Trust & Change Control](governance/trust_and_change_control.md) — what must stay true
- [AGENTS.md](../AGENTS.md) — red lines for AI agents

**Operations:**
- [RUNBOOK](operations/RUNBOOK.md) — running the system unattended
- [DEV_PROTOCOL](_ai/DEV_PROTOCOL.md) — two-machine workflow (intent vs fact separation)

**Understanding the system:**
- [Execution Path Honesty](facts/execution_path_honesty.md) — where LLM is (and isn't) in the decision loop
- [North Star](references/north_star.md) — direction map vs reference frameworks
- [Pipeline Status](status/PIPELINE_STATUS.md) — per-stage implementation status
