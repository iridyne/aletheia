
# governance/ - Safety And Change Control

Use this directory before touching controlled modules, trust boundaries, live trading, execution, secrets, permissions, or audit-chain behavior.

## Subfolders

| Folder | Use |
|---|---|
| `policies/` | Trust boundaries, implementation guide, execution honesty, machine-readable boundary policy |
| `protocols/` | Bug triage and repair classification protocols |
| `decisions/` | Durable architecture and product-boundary decisions |

Read [Documentation Governance](policies/documentation_governance.md) before
creating a new documentation family or changing document authority.

Use the [Architecture Decision Log](decisions/DECISIONS.md) for durable choices
that explain why the current contracts and boundaries exist.
For repository work boundaries and handoff discipline, use the
[AI Development Workflow](protocols/AI_DEVELOPMENT_WORKFLOW.md).
For bug investigation and integrity audits, use the
[Debug & Audit Playbook](protocols/DEBUG_AUDIT_PLAYBOOK.md).

## Agent Rule

Governance outranks plans, research, and status docs. If a task conflicts with governance, stop and ask unless the user explicitly approves the boundary change.
