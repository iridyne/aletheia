# jj Workspace Playbook (colocated Git backend)

> **Role: Operations.** How to isolate write work in jj workspaces. Machine roles, stable Runtime
> ownership, qualification freeze and multi-preview rules are owned by the
> [Qualification Development Protocol](QUALIFICATION_DEVELOPMENT_PROTOCOL.md); commit/push recipes
> by [Development Workflow](agent-guides/DEVELOPMENT_WORKFLOW.md#durable-checkpoints). Retired
> 2026-06 concurrency defences are in [history](../../design/history/jj_workspace_playbook_retired_sections.md).

"Workspace" below always means a **jj workspace**. A **Git worktree** is what Orca or
`EnterWorktree` creates; it has no `.jj` of its own and appears only in §2.

## 1. Rule: non-trivial writes happen in a workspace

- Open one per task: `jj workspace add .claude/worktrees/<name> --name <name> -r master@origin`.
  Task workspaces live only under `.claude/worktrees/` (ignored by `.gitignore`), never in a sibling
  directory outside the repository.
- Exception: a one-shot, read-only baseline for test comparison may live in `/tmp`
  (`jj workspace add /tmp/baseline -r master@origin`, as in Development Workflow) and is forgotten
  and deleted as soon as the comparison ends.
- Read-only queries may run in place.
- Every agent prompt states goal, allowed paths, excluded paths and acceptance checks.
- Each workspace has its own `<name>@` working-copy commit; uncommitted changes are not shared.
  `jj workspace list` shows them all.

## 2. Git worktrees cannot run the local gates

Only a jj workspace can run `dev_check`, `check_docs` or `ci_preflight`: `scripts/ci/vcs.py`
requires `jj workspace root`, and in a Git worktree jj would walk up to the main checkout and read
the wrong change set. A task that must pass the gates does not use a Git worktree. Orca-specific
rules: [Orca CLI workflow](ORCA_CLI_WORKFLOW.md).

## 3. Runtime boundaries

- A workspace's `data/` is empty: never run `gateway start|stop|restart|update` from a workspace.
- A workspace may run its own `aletheia dev api --port 879x` and Vite preview; previews use
  `~/.aletheia/instances/dev-preview/<checkout-id>/<port>` and unique ports per process.
- jj runs no Git hooks; any machine-level hook needs a stable-owner / main-checkout guard.
- Git operations never restart the Gateway, and a merged PR is not a qualification deployment
  ([Deployment](../deployment/DEPLOYMENT.md#3-deploy-a-specific-commit-to-fedora-and-verify)).

## 4. Land a change

`jj git fetch` → `jj rebase -d master@origin` → `jj commit <paths> -m …` (only this task's paths)
→ `jj git push --bookmark <name>` → open the PR → the owner merges with `gh pr merge <n> --rebase`
(no remote CI gate, no auto-merge). Verify claims against VCS state (`jj log`, `jj op log`,
`jj bookmark list -a <name>`), not a single tool receipt.

## 5. Clean up

- After merge, `jj workspace forget <name>`, delete the directory and the local bookmark.
- Stop the workspace's preview API/Vite processes; delete preview state once no reproduction
  evidence is needed. Never delete the stable Runtime's `~/.aletheia`.
- Re-check runtime state with `doctor` on the runtime; do not record it in Git.
