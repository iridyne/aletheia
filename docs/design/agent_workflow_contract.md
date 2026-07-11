# Agent workflow contract

## Scope

This contract governs repository development continuity. It is outside the
trading runtime: no task record may change or claim facts about trading,
execution, credentials, gateway state, databases, balances, or positions.

## Invariants

1. **AW-S01 — durable task packet.** Every in-progress task has a schema-v1
   packet with a stable ID, scope, intent, acceptance conditions, branch claim,
   valid base commit, next action, and an existing per-task handoff.
2. **AW-S02 — honest completion.** A done task has successful evidence linked
   to a registered command. In strict mode the evidence must be from the
   checked-out commit.
3. **AW-S03 — isolated ownership.** Two in-progress task packets cannot claim
   the same branch.
4. **AW-S04 — no tracked runtime or secret state.** Metadata rejects
   secret-like values and runtime-state field names. Runtime facts are probed
   locally with `aletheia doctor`.
5. **AW-S05 — cold recovery.** Bootstrap identifies exactly one task for the
   current branch or fails visibly; it includes dirty-worktree state and never
   runs a command recorded in task metadata.
