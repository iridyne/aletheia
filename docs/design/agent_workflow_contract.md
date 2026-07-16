# Task Lite contract

## Scope

Task Lite coordinates development agents and sibling worktrees on one machine.
It is outside the trading runtime and outside Git-tracked project truth. It must
not change or claim facts about trading, execution, credentials, gateway state,
databases, balances, or positions.

Git branches/worktrees coordinate code changes. Pull requests and CI own review
and verification. Task Lite only prevents duplicated local work and preserves a
small amount of cold-session recovery context.

## Invariants

1. **AW-S01 — machine-local state.** Active task records live below
   `~/.aletheia/development/agent-tasks/`. Creating, claiming, updating, or
   completing a task does not modify the repository worktree.
2. **AW-S02 — Git owns proof.** Task records contain no command transcript,
   command hash, test result, commit SHA, base commit, or evidence ancestry.
   Focused tests are run directly; commits, PRs, and CI are durable proof.
3. **AW-S03 — one active task per branch.** The local SQLite store atomically
   prevents two active tasks from claiming the same branch.
4. **AW-S04 — no secrets or runtime facts.** Task metadata rejects secret-like
   values and runtime-state field names. Runtime facts are probed with
   `aletheia doctor`.
5. **AW-S05 — cold recovery.** Bootstrap reports branch, HEAD, dirty-worktree
   state, local task/store, next action, and the separate runtime probe. No
   active task is a valid recoverable state.
6. **AW-S06 — worktree sharing.** Sibling worktrees belonging to one Git clone
   resolve the same local task database through the shared Git common directory.
7. **AW-S07 — no Git adoption gate.** Code, test, documentation, and CI changes
   never require task-packet or handoff changes. CI does not validate local task
   records.
8. **AW-S08 — advisory trust boundaries.** Changed controlled paths may produce
   focused-test hints from `.agent/policy.yaml`, but task metadata never grants
   permission or satisfies proof requirements.
9. **AW-S09 — frozen tracked history.** `.agent/tasks/` and `.agent/handoffs/`
   remain historical archives only. Bootstrap and validation do not load them as
   active state.

## Minimal data model

A local task contains only:

```text
id, title, intent, status
worker_id, branch, claimed_at, expires_at
scope.boundary, scope.excluded
acceptance, blockers, next_action
updated_at
```

The allowed states are:

```text
ready → in_progress → blocked | done | cancelled
blocked → in_progress | ready | done | cancelled
```

Expired `in_progress` claims return to `ready` automatically. Terminal tasks
release their branch and worker ownership.

## Public commands

| Command | Behavior |
|---|---|
| `agent workflow bootstrap` | Read local task/worktree context; no task is an acceptable result |
| `agent workflow check` | Validate local records and optionally print advisory trust-boundary hints |
| `agent workflow task create` | Create a local task; writes no repository file |
| `agent workflow task claim` | Atomically claim a task for the current branch |
| `agent workflow task update` | Update minimal recovery context |
| `agent workflow task complete` | Release local ownership; does not run or record tests |
| `agent workflow task delete` | Remove obsolete local state |
| `agent workflow recovery-test` | Verify that local context is unambiguous and safe to inspect |
| `agent workflow check-staged` | Print focused-test hints; always advisory |

The former `verify`, `revalidate`, `rebase-base`, and tracked `handoff` behaviors
are intentionally retired. Compatibility functions fail with an explicit
message instead of recreating the old evidence ledger.

## Non-goals

- No Git-tracked task status, handoff, lease, test evidence, or completion commit.
- No replacement for GitHub issues, PRs, branch protection, or CI.
- No cross-clone or cloud task coordinator.
- No scheduler, dashboard, agent fleet, or generic project-management platform.
- No authority over Risk, Execution, Live, secrets, or trading behavior.
