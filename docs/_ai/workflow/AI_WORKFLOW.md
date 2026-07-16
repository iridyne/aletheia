# AI Development Workflow

## Responsibility split

- **External orchestrator (for example ORCA):** agent sessions, prompts, panes,
  worktree creation, parallel assignment, monitoring, and ephemeral completion state.
- **Aletheia repository:** project instructions, architecture contracts, code,
  tests, decisions, pull requests, and CI.
- **Local runtime:** probed with `uv run aletheia doctor`; runtime facts are never
  inferred from an orchestrator session or committed document.

Aletheia deliberately has no development-task database, task packet format,
worker lease, handoff ledger, or task-management CLI.

Old installations may still contain an inert
`~/.aletheia/development/agent-tasks/` directory. Current code never reads,
migrates, or recreates it; it may be removed manually after confirming no external
tool uses that path.

## Start of work

1. Read `AGENTS.md` and only the topic-specific documents it points to.
2. Use the external orchestrator to create/select an isolated worktree when work
   runs in parallel.
3. Run `git status` and `uv run aletheia doctor` before changing behavior.
4. State the boundary, invariant, exclusions, and focused verification command.

A good agent prompt contains:

```text
Goal:
Allowed paths:
Excluded paths:
Acceptance checks:
Final report:
```

## End of work

1. Run focused tests, then broader tests appropriate to the changed surface.
2. Review the diff and commit only intended source/documentation changes.
3. Push the branch and use the PR as the durable review and handoff surface.
4. Record long-lived decisions in `docs/_ai/decisions/DECISIONS.md` or the relevant
   design document. Do not commit external-orchestrator session state.

## SDD development cycle

See [SDD_METHODOLOGY](../../design/process/SDD_METHODOLOGY.md).

```text
1. Constitution  Confirm AGENTS.md constraints
2. Specify       Find or create the L2 contract
3. Clarify       Cover error, empty, boundary, concurrency, idempotency,
                 safety, and observability cases
4. Plan          Write the smallest implementation plan when needed
5. Implement     Add/update spec → RED → implementation → GREEN
6. Review        Diff, focused proof, PR, CI
```

Contract and executable spec must change together. CI failure means the
implementation or contract is incomplete; it is never repaired by changing a
development-task record.
