# Agent workflow contract

## Scope

This contract governs repository development continuity. It is outside the
trading runtime: no task record may change or claim facts about trading,
execution, credentials, gateway state, databases, balances, or positions.

## Invariants

1. **AW-S01 — durable task packet.** Every in-progress task has a schema-v1
   packet with a stable ID, scope, intent, acceptance conditions, branch claim,
   valid base commit, next action, and an existing per-task handoff.
2. **AW-S02 — honest completion.** Evidence for versioned task packets is
   written only after the workflow tool actually executes a registered command
   successfully. Completion requires that tool-produced evidence at the exact
   current implementation `HEAD`; a failed or older run cannot complete work.
   Historical evidence remains valid history after later unrelated work.
3. **AW-S03 — isolated ownership.** Two in-progress task packets cannot claim
   the same branch.
4. **AW-S04 — no tracked runtime or secret state.** Metadata rejects
   secret-like values and runtime-state field names. Runtime facts are probed
   locally with `aletheia doctor`.
5. **AW-S05 — cold recovery.** Bootstrap identifies an active task or labels
   the latest completed task as history; it reports whether evidence is
   current, historical, missing, or unreachable, includes dirty-worktree state,
   and never runs a command recorded in task metadata.
6. **AW-S06 — deterministic handoff and terminal state.** New task packets use
   a fixed handoff structure. A done task cannot retain an implementation next
   action; it must send a new session back through workflow bootstrap.
7. **AW-S07 — enforced adoption.** Strict range validation and CI reject
   governed source/documentation changes unless the same change range updates
   both a task packet and its handoff.
