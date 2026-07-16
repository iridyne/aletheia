# Task Lite

Run `uv run aletheia agent workflow bootstrap` before non-trivial work. It reads
machine-local task context from `~/.aletheia/development/agent-tasks/` and probes
the current Git branch/worktree. Use `uv run aletheia doctor` separately for this
machine's trading runtime facts.

Task Lite has one purpose: keep local AI agents and sibling worktrees from
silently duplicating work. It stores only a small recovery packet:

- task ID and title;
- intent;
- scope boundary and exclusions;
- acceptance conditions and blockers;
- current branch/worker lease;
- one exact next action.

Create and claim a local task with:

```bash
uv run aletheia agent workflow task create TASK-ID \
  --title "..." --intent "..." --boundary "..." --excluded "..."
uv run aletheia agent workflow task claim TASK-ID --holder AGENT-NAME
```

Complete it with:

```bash
uv run aletheia agent workflow task complete TASK-ID
```

No Task Lite command writes task packets, handoffs, verification evidence, or
commit references into Git. Code history, review, and proof belong to commits,
pull requests, and CI. The existing `.agent/tasks/` and `.agent/handoffs/`
directories are frozen historical archives and are not loaded as active state.

Task metadata must never contain credentials, process state, gateway state,
database contents, balances, or positions.
