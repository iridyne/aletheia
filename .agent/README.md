# Agent workflow

Run `uv run aletheia agent workflow bootstrap` before changing code. It reads tracked
task intent and probes the current Git worktree; use `uv run aletheia doctor`
separately for this machine's runtime facts.

Before completion, commit the implementation and run an approved command through:

```bash
uv run aletheia agent workflow task verify TASK-ID --command-key KEY
```

Then update the structured handoff, run `task complete TASK-ID`, and finish with
`uv run aletheia agent workflow check --strict`. Verification recorded by hand is
rejected for versioned task packets. A completed task always points back to
`workflow bootstrap`; an old implementation instruction is invalid.

Tracked records must never contain credentials, process state, gateway state,
database contents, balances, or positions. See `docs/plans/agent_session_independent_workflow.md`.
