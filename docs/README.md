
# docs/ - Aletheia Agent Documentation Map

This directory is organized for AI agents first: read the smallest routing file, then jump to the narrowest authoritative folder. Do not bulk-read `docs/`.

## Agent Reading Protocol

1. Read `../AGENTS.md` first, then use this file as a navigation map.
2. Pick exactly one task lane below.
3. Read that lane's `README.md` before opening individual documents.
4. Prefer authoritative contracts and runbooks over old plans, audits, or session notes.
5. Treat `archive/` and `research/` as evidence, not current truth. Legacy AI context,
   issue, plan, and status collections are consolidated under `archive/legacy/`.
6. Runtime truth still comes from `aletheia doctor`, logs, DB state, and code inspection.

## Authority Order

The [governance authority map](governance/policies/documentation_governance.md#authority-and-conflict-resolution)
is canonical. For a new task, distinguish:

- Development permission and owner escalation: [AGENTS.md](../AGENTS.md).
- Active trading admission: [ACTIVE_RUNTIME_SCOPE.md](product/ACTIVE_RUNTIME_SCOPE.md).
- Current capability promises and evidence limits: [CAPABILITY_GROUND_TRUTH.md](product/CAPABILITY_GROUND_TRUTH.md).
- Approved subsystem behavior: the narrowest current `design/contracts/` document.
- Actual code, tests and deployed behavior: inspect the exact source/runtime;
  an architecture overview, accepted target or GitHub closure is not proof.

`product/definition/DIRECTION.md` owns long-term intent. Broader scope, gates,
proposals and historical inventories cannot expand the active declaration.
GitHub Issues/PRs track work and evidence, not a second runtime truth.

## Task Lanes

| Task | Read first | Then narrow to |
|---|---|---|
| New agent session | `../AGENTS.md` | choose the narrow task lane below; external orchestrator owns session/worktree state |
| Product definition or long-term direction | `product/definition/DIRECTION.md` | The single canonical direction document |
| Product scope, evaluation, or admission | `product/README.md` | `product/ACTIVE_RUNTIME_SCOPE.md`, `product/CAPABILITY_GROUND_TRUTH.md`, then only the relevant contract/gate/experiment |
| Architecture or subsystem behavior | `design/README.md` | `design/architecture/SYSTEM_MAP.md` (task → owner → source → tests), then `design/contracts/`, `design/data/` |
| API or schema work | `design/README.md` | `design/api/`, `design/data/`, `design/contracts/` |
| Risk, execution, live unlock, audit chain | `governance/README.md` | `governance/policies/`, `design/contracts/` |
| Local setup, preview, tests, deployment | `operations/README.md` | `operations/development/`, `operations/deployment/`, `operations/runbooks/` |
| Bug triage or known problems | `governance/protocols/BUG_TRIAGE_PROTOCOL.md` | GitHub Issues; use `archive/legacy/issues/` only for historical evidence |
| Substantial design proposal | `design/proposals/README.md` | one proposal only when its threshold is met |
| Historical implementation intent | `archive/README.md` | one file under `archive/legacy/plans/`; never use it as current authority |
| External inspiration or papers | `references/README.md` | `references/external-systems/`, `references/papers-and-benchmarks/` |
| Research/audit evidence | `research/README.md` | one domain folder under `research/` |
| Historical progress snapshot | `archive/README.md` | `archive/legacy/status/` |
| Diagrams | `diagrams/README.md` | `diagrams/source/`, `diagrams/rendered/` |

## Directory Map

| Directory | Agent purpose | Write policy |
|---|---|---|
| `product/` | Product identity, scope, metrics, experiment rules | Durable product decisions only |
| `governance/` | Safety, trust boundaries, protocols | Rare; requires high confidence |
| `design/` | System contracts and architecture | Update with executable specs when contract changes |
| `operations/` | Runbooks, setup, deployment, recovery | Update when operator procedure changes |
| `research/` | Audits, investigations, domain research | Evidence store; not a source of current truth by itself |
| `references/` | External systems, papers, captured reference material | External reference only; never implementation authorization |
| `diagrams/` | Diagram sources and rendered assets | Keep source and rendered output paired |
| `archive/` | Superseded project history and consolidated legacy AI context, issues, plans, and status | Read only for provenance |

## Naming Rules For New Docs

- Use a domain folder instead of adding files to a crowded parent directory.
- Prefer lowercase kebab-case for new filenames unless the existing family uses uppercase.
- Add or update the nearest `README.md` when a new subfolder is created.
- Put current behavior in `design/contracts/`; put qualifying substantial designs in `design/proposals/`; put investigation evidence in `research/<domain>/`; put current defects in GitHub Issues.
- Never create a Markdown file to track owners, priorities, checklists, completion percentages, or live work status.
