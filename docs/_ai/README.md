
# _ai/ - Reusable Agent Workflow And Historical Context

<!-- HISTORICAL-NO-TRACKING -->

This directory contains reusable engineering playbooks and historical agent-produced
context. It is lower authority than `AGENTS.md`, governance policies, product docs,
design contracts, code, and executable specs. The external development orchestrator owns
current session assignment, task state, handoff, worktree lifecycle, and completion state;
do not reconstruct those facts from this directory.

## Read Order

1. Start at the repository root `AGENTS.md`.
2. Read only the topic-specific workflow file named by `AGENTS.md`.
3. Use `workflow/DEV_PROTOCOL.md` or `workflow/GIT_WORKTREE_PLAYBOOK.md` when their
   trigger applies.
4. Treat `session/`, `decisions/`, `status/`, `logs/`, and `discussions/` as historical
   evidence unless a current authoritative document links to a specific record.

## Subfolders

| Folder | Use |
|---|---|
| `session/` | Legacy task/handoff snapshots; never current orchestration authority |
| `workflow/` | Repeatable agent protocols, conventions, playbooks |
| `decisions/` | Historical agent-facing decision ledgers; current decisions belong in the affected product/design document |
| `audits/` | Point-in-time AI-produced audits and reviews |
| `engineering/` | Test, latency, OpenAPI, and tooling notes |
| `lessons/` | Lessons learned from incidents or runtime bugs |
| `discussions/` | Owner discussion breakdowns grouped by date |
| `status/` | Agent-facing status reports |
| `logs/` | Development logs |

## Write Guidance

Keep reusable workflow material navigational and concise. Do not store runtime facts such
as local DB state, gateway process state, credentials, current task/handoff state, worker
leases, verification ledgers, or external orchestrator session state. Do not update legacy
`session/`, `status/`, `logs/`, `decisions/`, or `discussions/` files to
coordinate or report active work.
