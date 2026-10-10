# Orca CLI Development Workflow

This runbook covers repeatable repository work performed from an
Orca-managed session. Orca owns workspace identity, worktree lifecycle,
terminal placement, and workspace status. Git, GitHub PRs, and CI remain the
durable proof of the change.

Use this runbook together with the
[jj Workspace Playbook](JJ_WORKSPACE_PLAYBOOK.md). Do not implement Orca
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

Merging is manual since 2026-09-26. PR #3506 retired the auto-merge control
plane; the 2026-09-26 decision *"PR 合并方式 = 人工 `gh pr merge --rebase`,
无自动合并层"* in `docs/governance/decisions/DECISIONS.md` names the current
rule. The owner merges each PR by hand:

```powershell
gh pr merge <pr-number> --rebase
```

`--rebase` keeps every commit distinct on `master`. Do not use `--auto`
(no workflow listens for that request any more), and do not use `--squash`
unless a specific PR has been agreed to squash.

### Stacked PRs

This repository uses **plain feature-branch stacks** (PR B is created with
its base set to PR A's branch). GitHub does NOT automatically retarget PR B
when PR A merges — that automatic cascade only exists for GitHub's newer
[native stacked PR](https://docs.github.com/en/pull-requests/get-started/about-stacked-prs)
feature (`gh stack`), which we do not use.

For a plain stack, merge bottom-up. After each layer lands:

1. Rebase the next branch on the new `master`:

   ```powershell
   git fetch origin
   git checkout <next-branch>
   git rebase --update-refs origin/master
   ```

   `--update-refs` follows the stack and moves intermediate branch pointers
   in the same operation. Any patch already on `master` is skipped
   automatically (a `--rebase` merge on GitHub preserves patch identity).

2. Push with a lease, not a bare force. `--force-with-lease` refuses to
   overwrite the remote if someone else pushed since your last fetch:

   ```powershell
   git push --force-with-lease origin <next-branch>
   ```

3. Confirm on GitHub that the PR's diff is now just the branch's own
   change (base auto-retargets to `master` because its previous base
   branch was deleted on merge). Then merge that PR the same way.

Once the commit is pushed and the PR is created, the local Orca worktree
can be removed. Nothing runs remotely on the PR event; there is no CI or
auto-merge in flight that a worktree removal could cancel.

## Fast failure map

| Symptom | Meaning | Recovery |
| --- | --- | --- |
| `git pull --ff-only` has no tracking information | New feature branch has not been pushed | Use `git pull --ff-only origin master` for the base check; use `git push -u` when publishing |
| `uv` cannot persist its cache | Machine cache permissions are unsuitable | Set a task-specific `UV_CACHE_DIR` under `LOCALAPPDATA` |
| PR is green but remains open | No auto-merger exists any more (retired 2026-09-26) | Merge it manually: `gh pr merge <n> --rebase` |
| Stacked PR shows a huge unfamiliar diff after its base merged | Rebase-merge rewrites hashes; the top branch still carries the old ones | Rebase the top branch on the new `master` (`git rebase --update-refs origin/master`), `git push --force-with-lease` (never bare `--force`), verify the diff shrinks to the branch's real change |
| Worktree deletion would lose the only copy | Change is not durably published | Push and verify the remote SHA, create the PR, then the worktree can be removed |
