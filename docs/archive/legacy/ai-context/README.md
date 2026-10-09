
# ai-context/ - Historical Agent Context

<!-- HISTORICAL-NO-TRACKING -->

This directory contains historical agent-produced context. Reusable protocols
now live under `docs/governance/` and `docs/operations/`. It is lower authority than `AGENTS.md`, governance policies, product docs,
design contracts, code, and executable specs. The external development orchestrator owns
current session assignment, task state, handoff, worktree lifecycle, and completion state;
do not reconstruct those facts from this directory.

Read a file here only when its dated evidence is needed; start at the repository
root `AGENTS.md` for current rules.

## Subfolders

| Folder | Use |
|---|---|
| `session/` | Legacy task/handoff snapshots; never current orchestration authority |
| `workflow/` | Former agent conventions and project context (superseded by `AGENTS.md`) |
| `decisions/` | Historical agent-facing decision ledgers; current decisions belong in the affected product/design document |
| `audits/` | Point-in-time AI-produced audits and reviews |
| `engineering/` | Test, latency, OpenAPI, and tooling notes |
| `lessons/` | Lessons learned from incidents or runtime bugs |
| `discussions/` | Owner discussion breakdowns grouped by date |
| `status/` | Agent-facing status reports |
| `logs/` | Development logs |

## Write Guidance

Do not store runtime facts such
as local DB state, gateway process state, credentials, current task/handoff state, worker
leases, verification ledgers, or external orchestrator session state. Do not update legacy
`session/`, `status/`, `logs/`, `decisions/`, or `discussions/` files to
coordinate or report active work.
