
# _ai/ - Agent Operating Context

Use this directory for AI-agent workflow and session context. These files are useful but lower authority than `AGENTS.md`, governance policies, product docs, and design contracts.

## Read Order

1. `session/CURRENT_TASK.md` - current task pointer when maintained.
2. `session/AI_HANDOFF.md` - handoff context and active constraints.
3. `workflow/CONVENTIONS.md` and `workflow/AI_WORKFLOW.md` - agent work style.
4. `workflow/DEV_PROTOCOL.md` and `workflow/GIT_WORKTREE_PLAYBOOK.md` - development and worktree rules.
5. `decisions/DECISIONS.md` - durable agent-facing decisions.

## Subfolders

| Folder | Use |
|---|---|
| `session/` | Current task, handoff, session-specific summaries |
| `workflow/` | Repeatable agent protocols, conventions, playbooks |
| `decisions/` | Durable decision records and ledgers |
| `audits/` | Point-in-time AI-produced audits and reviews |
| `engineering/` | Test, latency, OpenAPI, and tooling notes |
| `lessons/` | Lessons learned from incidents or runtime bugs |
| `discussions/` | Owner discussion breakdowns grouped by date |
| `status/` | Agent-facing status reports |
| `logs/` | Development logs |

## Write Guidance

Keep this area navigational and concise. Do not store runtime facts such as local DB state, gateway process state, credentials, or external orchestrator session state.
