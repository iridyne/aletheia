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
6. **AW-S06 — deterministic handoff and terminal state.** New task packets
   declare a `packet_type` — `standard` (default) requires the 8-heading
   handoff (`Purpose`, `Changes made`, `Uncommitted work`, `Verification`,
   `Remaining work`, `Exact next action`, `Blockers`, `Risks`); `tiny`
   requires only `Purpose`, `Changes made`, `Verification`. Packets without a
   `packet_type` field are treated as `standard` for backward compatibility.
   A done task cannot retain an implementation next action; it must send a
   new session back through workflow bootstrap.
7. **AW-S07 — enforced adoption.** Strict range validation and CI reject
   governed source/documentation changes unless the same change range updates
   both a task packet and its handoff.
8. **AW-S08 — base_commit rebase resilience.** A `base_commit` that no longer
   reaches HEAD (after a rebase, squash-merge, or history rewrite) surfaces as
   a warning, not an error, so long-lived branches can stay honest without
   fabricating history. Only a non-existent commit is an error. Owners
   explicitly re-anchor the packet with `agent workflow task rebase-base
   TASK-ID`, which resets `base_commit` to current HEAD and records
   `rebased_at`. `rebase-base` refuses tasks not in `in_progress` or
   `blocked`.
9. **AW-S09 — single handoff channel.** Per-task hand-offs live in
   `.agent/handoffs/TASK-*.md`. `docs/_ai/AI_HANDOFF.md` is frozen as of
   2026-07-12 and preserved as historical narrative only; the spec fails if
   AGENTS.md stops naming `.agent/handoffs/` or `AI_HANDOFF.md` loses its
   frozen banner. `DEVLOG.md` and `DECISIONS.md` remain append-only for
   change history and architecture decisions respectively.
10. **AW-S10 — squash-merge evidence tolerance.** A completed task's
    verification evidence is recorded against the feature-branch commit that
    ran it. A squash-merge (or rebase) rewrites that commit into a new SHA on
    the default branch, orphaning the recorded SHA even though the proof was
    real. Under `--strict`, evidence whose commit *still exists* in the repo
    but is no longer a HEAD ancestor is a **warning** (not an error); only
    evidence whose commit *does not exist at all* (fabricated / typo'd) is a
    hard error. This keeps `check --strict` green on the default branch after
    the normal squash-merge PR flow, mirroring the AW-S08 base_commit
    precedent. A completed task with no successful evidence at all remains an
    error.
