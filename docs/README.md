
# docs/ - Aletheia Agent Documentation Map

This directory is organized for AI agents first: read the smallest routing file, then jump to the narrowest authoritative folder. Do not bulk-read `docs/`.

## Agent Reading Protocol

1. Start with this file.
2. Pick exactly one task lane below.
3. Read that lane's `README.md` before opening individual documents.
4. Prefer authoritative contracts and runbooks over old plans, audits, or session notes.
5. Treat `archive/`, `research/`, `_ai/audits/`, and `issues/archive/` as evidence, not current truth.
6. Runtime truth still comes from `aletheia doctor`, logs, DB state, and code inspection.

## Authority Order

1. `../AGENTS.md` - mandatory safety and repo rules.
2. `governance/policies/` and `governance/protocols/` - change-control and triage rules.
3. `product/definition/` and `product/gates/` - product scope and acceptance gates.
4. `design/contracts/`, `design/data/`, `design/api/`, `design/architecture/` - executable system intent.
5. `operations/runbooks/`, `operations/development/`, `operations/recovery/` - how to operate or recover.
6. `issues/` and `plans/` - work queues and implementation intent.
7. `research/`, `references/`, `_ai/`, `status/`, `archive/` - context and historical evidence.

## Task Lanes

| Task | Read first | Then narrow to |
|---|---|---|
| New agent session | `_ai/README.md` | `_ai/session/`, `_ai/workflow/` |
| Product direction or scope | `product/README.md` | `product/definition/`, `product/gates/`, `product/experiments/` |
| Architecture or subsystem behavior | `design/README.md` | `design/contracts/`, `design/architecture/`, `design/data/` |
| API or schema work | `design/README.md` | `design/api/`, `design/data/`, `design/contracts/` |
| Risk, execution, live unlock, audit chain | `governance/README.md` | `governance/policies/`, `design/contracts/` |
| Local setup, preview, tests, deployment | `operations/README.md` | `operations/development/`, `operations/deployment/`, `operations/runbooks/` |
| Bug triage or known problems | `issues/README.md` | `issues/domain/`, `issues/incidents/`, `issues/backlog/` |
| Implementation sequencing | `plans/README.md` | one domain folder under `plans/` |
| External inspiration or papers | `references/README.md` | `references/external-systems/`, `references/papers-and-benchmarks/` |
| Research/audit evidence | `research/README.md` | one domain folder under `research/` |
| Progress snapshot | `status/README.md` | `status/roadmap/`, `status/snapshots/`, `status/audits/` |
| Diagrams | `diagrams/README.md` | `diagrams/source/`, `diagrams/rendered/` |

## Directory Map

| Directory | Agent purpose | Write policy |
|---|---|---|
| `_ai/` | Agent workflow, handoff, decisions, session context | Update only when maintaining agent-facing context |
| `product/` | Product identity, scope, metrics, experiment rules | Durable product decisions only |
| `governance/` | Safety, trust boundaries, protocols | Rare; requires high confidence |
| `design/` | System contracts and architecture | Update with executable specs when contract changes |
| `operations/` | Runbooks, setup, deployment, recovery | Update when operator procedure changes |
| `issues/` | Known defects, incidents, backlog ledgers | Add evidence; close or archive when superseded |
| `plans/` | Implementation plans for future work | Keep plans domain-scoped and mark when superseded |
| `research/` | Audits, investigations, domain research | Evidence store; not a source of current truth by itself |
| `references/` | External systems, papers, captured reference material | External reference only; never implementation authorization |
| `status/` | Roadmaps and point-in-time progress | Snapshot carefully; can drift |
| `diagrams/` | Diagram sources and rendered assets | Keep source and rendered output paired |
| `archive/` | Superseded project history | Read only for provenance |
| `superpowers/` | Optional/experimental specialized specs | Do not treat as core contract unless linked from design |

## Naming Rules For New Docs

- Use a domain folder instead of adding files to a crowded parent directory.
- Prefer lowercase kebab-case for new filenames unless the existing family uses uppercase.
- Add or update the nearest `README.md` when a new subfolder is created.
- Put current contracts in `design/contracts/`; put implementation plans in `plans/<domain>/`; put investigation evidence in `research/<domain>/`; put known defects in `issues/<domain>/`.
