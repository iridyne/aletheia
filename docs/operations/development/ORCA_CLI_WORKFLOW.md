# Orca CLI Development Workflow

This runbook covers repeatable repository work performed from an
Orca-managed session. Orca owns workspace identity, worktree lifecycle,
terminal placement, and workspace status. Git, GitHub PRs, and CI remain the
durable proof of the change.

Use this runbook together with the
[Git Worktree Playbook](GIT_WORKTREE_PLAYBOOK.md). Do not implement Orca
session or task state inside Aletheia.

## Load the live CLI contract first

Orca's installed CLI is the versioned source of truth. Do not guess subcommands
from memory.

1. Resolve the command once. In an Orca-managed Windows terminal this is
   normally `orca`. Respect `ORCA_CLI_COMMAND` or the development-build command
   when Orca provides one.
2. Read the installed guide:

   ```powershell
   orca skills get orca-cli
   ```

3. Confirm the runtime and current context:

   ```powershell
   orca status --json
   orca worktree current --json
   orca repo list --json
   ```

Keep using the same resolved executable for the whole task. Copy opaque repo
and worktree IDs exactly from JSON responses.

## Create an independent worktree

For a new top-level change based on the repository default branch:

```powershell
orca worktree create `
  --repo id:<repo-id> `
  --name <task-name> `
  --no-parent `
  --setup skip `
  --json
```

`--no-parent` controls Orca lineage, not the Git base. Omitting
`--base-branch` lets Orca use the repository's configured default base. Verify
the response contains the expected `baseRef`, branch, path, and HEAD before
editing.

Use `--setup skip` only when the acting agent will run the repository startup
commands itself. For a full ownership handoff, prefer Orca's agent-first
creation form from the live CLI guide so the receiving agent owns the first
terminal and receives a prompt with goal, allowed paths, excluded paths,
acceptance checks, and expected report.

## Initialize without confusing worktree state

Run startup commands from the new worktree path, not the old checkout.

An Orca-created branch may not have an upstream until its first push. In that
case, bare `git pull --ff-only` reports that tracking information is missing.
Do not set the feature branch to track `origin/master`. Verify the intended
base explicitly instead:

```powershell
git pull --ff-only origin master
```

Then initialize and inspect runtime truth:

```powershell
uv sync
uv run aletheia doctor
```

On Windows, a machine-level `uv` cache may be readable but not writable. If
`uv` reports access denied while persisting its cache, use a task-specific
user cache for that shell:

```powershell
$env:UV_CACHE_DIR="$env:LOCALAPPDATA\uv-cache-<task-name>"
uv sync
uv run aletheia doctor
```

Do not repurpose `HOME`, `CODEX_HOME`, or another common system variable.
`doctor` can legitimately report runtime or generated-static warnings in an
isolated worktree; record them accurately and do not restart the global gateway
from the worktree.

## Keep Orca status useful

Update the workspace card at meaningful checkpoints:

```powershell
orca worktree set `
  --worktree id:<repo-id>::<absolute-worktree-path> `
  --comment "<current concise outcome>" `
  --workspace-status in-review `
  --json
```

Workspace comments are ephemeral coordination. Do not copy them into tracked
task ledgers or repository status documents.

## Publish and hand off to GitHub

Before publishing:

1. run the focused checks directly;
2. stage only named task files;
3. confirm the branch and clean scope;
4. commit and push with upstream:

   ```powershell
   git push -u origin <branch>
   ```

5. verify the remote SHA with `git ls-remote`;
6. create the PR.

Repository permission to use auto-merge does not itself queue every PR.
Auto-merge is represented by a per-PR request. Ready, non-draft PRs targeting
`master` are queued by `.github/workflows/auto-merge.yml`; the
`do-not-auto-merge` label removes an existing request and prevents a new one.
Removing the label queues the PR again.

For a PR created while changing that automation, or whenever the request must
be guaranteed before the workflow is available, explicitly queue it:

```powershell
gh pr merge <pr-number> --auto --merge
gh pr view <pr-number> --json autoMergeRequest,statusCheckRollup,mergeStateStatus
```

Do not claim the handoff is complete until `autoMergeRequest` is non-null.
Required checks still control whether GitHub may merge.

Stacked PRs need special attention. A PR initially targeting another feature
branch does not run the `master` workflow. When its base later changes to
`master`, the workflow's `edited` event queues it. Verify the request and CI
instead of assuming retargeting triggered both.

Once the commit is pushed, the PR exists, required checks are running, and the
auto-merge request is present, GitHub owns the remaining work. The local Orca
worktree can then be removed without cancelling CI or auto-merge.

## Fast failure map

| Symptom | Meaning | Recovery |
| --- | --- | --- |
| `git pull --ff-only` has no tracking information | New feature branch has not been pushed | Use `git pull --ff-only origin master` for the base check; use `git push -u` when publishing |
| `uv` cannot persist its cache | Machine cache permissions are unsuitable | Set a task-specific `UV_CACHE_DIR` under `LOCALAPPDATA` |
| PR is green but remains open | No per-PR auto-merge request exists | Inspect `autoMergeRequest`; run `gh pr merge --auto --merge` |
| Stacked PR has no CI after its base merged | Retargeting did not produce the expected workflow run | Verify the new base and workflow event; reopen only when a fresh PR event is deliberately required |
| Worktree deletion would lose the only copy | Change is not durably published | Push, verify the remote SHA, create the PR, and verify its auto-merge request first |
