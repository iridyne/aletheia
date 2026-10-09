
# governance/ - Safety And Change Control

Use this directory before touching controlled modules, trust boundaries, live trading, execution, secrets, permissions, or audit-chain behavior.

## Subfolders

| Folder | Use |
|---|---|
| `policies/` | Trust boundaries, implementation guide, execution honesty, machine-readable boundary policy |
| `protocols/` | Bug triage and debug/audit playbook |
| `decisions/` | Durable architecture and product-boundary decisions |

Read [Documentation Governance](policies/documentation_governance.md) before
creating a new documentation family or changing document authority.

Use the [Architecture Decision Log](decisions/DECISIONS.md) for durable choices
that explain why the current contracts and boundaries exist.
Use the [Audit Gates Registry](audit_gates_registry.md) to find which contract
clause an executable `tests/audit/` gate enforces, and to register a new one.
For repository work boundaries, use root [`AGENTS.md`](../../AGENTS.md) and its
matched [agent guides](../operations/development/agent-guides/).
For bug investigation and integrity audits, use the
[Debug & Audit Playbook](protocols/DEBUG_AUDIT_PLAYBOOK.md).

## Agent Rule

Governance outranks plans, research, and status docs. Escalation follows
[bounded engineering delegation](../../AGENTS.md#bounded-engineering-delegation).

For current machine-readable Runtime, UFC, profit-loop, observer, and pause-owner
facts in an Issue or review, use `uv run aletheia control status --json`; do not
copy dynamic values into governance prose.
