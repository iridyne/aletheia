# Agent workflow

Run `uv run aletheia agent workflow bootstrap` before changing code. It reads tracked
task intent and probes the current Git worktree; use `uv run aletheia doctor`
separately for this machine's runtime facts.

Tracked records must never contain credentials, process state, gateway state,
database contents, balances, or positions. See `docs/plans/agent_session_independent_workflow.md`.
