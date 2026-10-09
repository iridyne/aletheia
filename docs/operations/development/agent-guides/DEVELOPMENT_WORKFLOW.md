# Agent Guide: Development Workflow

## Tools and workspace

- Use normal filesystem, shell, and jj tools for search, reads, edits, patches,
  builds, tests, and repository inspection.
- Use Orca only when Orca owns the state: managed workspace/worktree lifecycle,
  Orca terminals, embedded browser tabs, automations, card state, or handoffs.
  When needed, Orca is mandatory; do not bypass it with raw `git worktree`,
  ad-hoc PTYs, or separate browser automation. Before the first Orca call, run
  `orca skills get orca-cli`; read [the Orca workflow](../ORCA_CLI_WORKFLOW.md)
  before mutating Orca state.
- External orchestration owns agents, panes, worktrees, task state, and handoffs.
  Do not build repository task databases, worker leases, handoff ledgers, or an
  orchestration CLI. An inert `~/.aletheia/development/agent-tasks/` directory left
  by old installations is never read by current code and may be removed manually.
- Use a dedicated worktree/branch for non-trivial changes. In an explicitly
  requested shared checkout, re-check `jj st` before changing VCS state, commit
  only named files (`jj commit <paths>`), preserve unrelated edits, and never
  `jj abandon`, rewrite others' changes (`jj squash`/`jj rebase` over them), or
  `jj op restore` without approval.

- Some machines have a CodeGraph index (`.codegraph/`), but no checkout is promised one or
  an index that matches the current HEAD. Probe first: use `codegraph explore "<symbols or
  question>"` only if the command and index exist and the index is not obviously behind;
  otherwise fall back to text search / language-server lookups. Do not build an index as a
  prerequisite for ordinary work, and read the file before editing — the index can lag.

## Durable checkpoints

Issue comments and progress checkpoints are evidence, not durable source state.
Once one independently coherent bounded mutation batch has passed its focused
verification and self-review, commit that scoped batch before starting the next
independent mutation batch.

- An uncommitted checkpoint must say `UNCOMMITTED` explicitly and remains
  incomplete until committed or reverted. Do not accumulate several already-
  verified checkpoints in one growing dirty worktree.
- A completed-batch checkpoint references the resulting commit SHA and, after
  publication, its PR. Without a commit SHA, do not describe source convergence
  as completed, landed, or durable.
- In long-running owner-approved work, open or update a normal reviewable PR once
  a coherent slice exists; do not wait for the entire program or several unrelated
  ownership domains to accumulate. Split PRs by independently reviewable root
  cause / ownership boundary, not by session order or arbitrary file count.
- A PR may contain several tightly related commits. Do not create micro-commits or
  micro-PRs for trivial edits merely to satisfy this rule.
- A batch may remain temporarily uncommitted only when the same accepted change
  requires an atomic cross-file/package cutover to stay buildable or testable.
  Record that exact reason; this exception never carries completed unrelated work.
- Commit, PR, merge, deployment, and runtime proof remain separate claims; see the
  PR Review Lifecycle guide for merge readiness.

### Version control: jj

The checkout is jj-colocated (`.jj` beside `.git`). Use `jj` for all day-to-day
version control: `jj st`, `jj diff`, `jj log`, `jj describe`, `jj new`, `jj commit`,
`jj bookmark`, `jj git fetch`, `jj git push`, `jj rebase`, `jj workspace`. There is
no staging area and jj runs no git hooks, so the cheap gate is
`uv run python scripts/ci/dev_check.py` on the working copy `@` (`--base <revset>`,
e.g. `master@origin`, covers the branch since its fork point); it also carries the
`.env`/secret scan the retired pre-commit hook used to run. `jj undo` and `jj op log`
replace reflog and stash as the safety net.

In the checkpoint rules above, "commit" means `jj describe` + `jj new` (or
`jj commit`). A checkpoint is not complete while the work sits only in `@`: git
plumbing reads git HEAD, which in a colocated checkout is `@-`. Isolated workspaces
live under `.claude/worktrees/<name>` (see the
[workspace playbook](../GIT_WORKTREE_PLAYBOOK.md)).

Raw `git` remains only where the surface is git by construction: the production
self-updater and Fedora/tarball runtimes (`ops/update/`, `aletheia.update.git_ops`,
`core/aletheia/files/git.py`), `scripts/ci/ci_plan.py`, `ci_local.py`,
`check_change_contract.py` and `.github/workflows`, Orca-managed worktrees, and
git-only tools such as `gh`.

## Change and proof

When safe for the checkout, start with:

```sh
jj git fetch
jj new master@origin   # start new work on the latest master (or `jj rebase -d master@origin` to move existing work)
uv sync
uv run aletheia doctor
```

Before changing code:

1. **Identify the SDD depth** (D0 / D1 / D2, defined in root
   [AGENTS.md](../../../../AGENTS.md#human-decision-sovereignty); D0 is
   `Contract Delta: NONE`).
2. **Identify the changed boundary** and what remains unchanged.
3. **State current facts**: existing code paths, tests, durable data, runtime
   observations, or relevant Contract.
4. **Determine Contract Delta or `NONE`**: if D0, reference the existing
   Contract and do not modify it; if D1/D2, name what changes with old→new
   semantic description.
5. **Identify Non-goals**: what this change explicitly does not modify.
6. **Determine Verification**: executable tests, runtime observations, or
   economic evidence required to prove the change.
7. **Name the single owner of every value the change introduces.** For each new
   column, field, or resolution, say which aggregate owns it and who only reads
   it, and write that into the Contract before the first edit. A mirrored copy of
   a value another aggregate already owns is a second authority, not a
   convenience: adding one costs a migration and a later deletion, and the two
   copies can disagree while both look correct. Decide the owner once, then add
   no mirror.

SDD depth is a semantic classifier, not an approval process. Under root
[AGENTS.md](../../../../AGENTS.md#bounded-engineering-delegation), the AI derives
the engineering facts, failure cases and technical verification above within the
owner-authorized outcome. Routine implementation and checkpoint decisions do not
require owner handoff. Escalate only at the decision boundaries defined there;
resolvable technical uncertainty is investigated, while unapproved product or
structural choices are returned as facts plus one recommended decision. This
does not grant merge, release, deployment or shared-runtime restart authority.

For D1/D2 or an explicitly selected high-risk D0, an optional pre-change shadow
audit may be run before step 1 to surface semantic gaps early. Run convergence
against the exact candidate head after implementation. Both phases are advisory only
and never a required gate. If selected, follow
[SDD_SHADOW_WORKFLOW.md](SDD_SHADOW_WORKFLOW.md).

For each commit, include concise metadata at a minimum: SDD depth, changed
boundary, and Contract Delta (`NONE` for conformance repair). Use non-goals and
extra verification notes only when materially relevant. A batch of commits that
shares one investigation should still keep metadata scoped to each commit’s actual
change. Problem statement, accepted root cause/semantic decision, and success evidence
stay in the linked Issue / Change Contract; do not regenerate them per commit. Human
approval follows root `AGENTS.md`, independently of commit metadata.

Categorize commits by concern, not by session order.

## Fast feedback loop (mandatory first step)

Every routine edit starts with the changed-file gate, not with a suite run:

```sh
uv run python scripts/ci/dev_check.py                       # working copy (@) vs its parent
uv run python scripts/ci/dev_check.py --base master@origin  # branch since fork_point(@|master@origin)
```

jj has no staging area, so there is no `--staged` mode. It runs, in order and only
against what this change touched: a trailing-whitespace / conflict-marker scan of
added lines plus the `.env` / credential-shaped-line scan; then
`ruff check` with `B,ASYNC,RUF` enabled **reported on changed lines only**; the
test skip/xfail census, data-guarded silent-pass audit and assertion presence
(`scripts/ci/check_test_hygiene.py`, #3471, `check_docs.vacuous_test_failures`); the
cached previous-failure set, selected by pytest within explicit existing cached
test files. The gate does not parse test definitions or rewrite failure evidence;
stale selectors may retry their file but cannot widen collection to the whole suite.
If cached files collect no tests (pytest exit 5), that phase is reported skipped,
not passed, and focused checks still run; all other failures remain blocking.
Next come focused tests that name or import a changed module, then `basedpyright`
in `basic` mode, error-level, changed lines only.

Why changed lines only: the repository carries roughly a thousand pre-existing
`B/ASYNC/RUF` findings and 1152 files `ruff format` would rewrite, so a
whole-file gate would fail on every edit and be ignored. The enforced contract is
"this change adds no new problem", not "the repository is clean". Do not add
`ruff format --check` to this loop without first landing a repository-wide
formatting migration.

**Where tests run.** pytest does not run on the Mac. `scripts/dev/remote_pytest.sh <pytest args>`
rsyncs the working tree, uncommitted edits included, to `~/remote-test/<repo>-<checkout>` on Fedora (`u7p`).
There it runs pytest in a sandbox:
- a systemd user scope caps CPU at 800%, throttles memory at 2G (`MemoryHigh`) and kills at 4G. The host also runs the live runtime, so the script refuses to start when under 1.5 GB is available (exit 75): retry later. Split big runs by `-m domain_*` with `-n 3` rather than raising the cap;
- user, net and pid namespaces give the run a private loopback, no network, and no view of the live runtime;
- `ALETHEIA_DATA_DIR` and `TMPDIR` point at a fresh directory.

Each checkout gets its own remote directory and lock, so parallel workspaces don't collide. With
`ALETHEIA_TEST_HOST=u7p` exported, `dev_check` sends its pytest steps there too. Pass `-n 4` for
anything bigger than a handful of files. The remote tree is a throwaway git repo with one snapshot commit, so
code that reads the commit or `git ls-files` works; tests that need the local jj workspace, such as
`tests/test_dev_check.py`, still run locally.

Repair order — stop at the first red and fix it before widening:

1. `dev_check` (seconds) — ruff/hygiene/whitespace/previous red set.
2. Focused tests for the module you changed (`dev_check` selects these).
3. `uv run python scripts/ci/dev_check.py --fast` — the `-m fast` default feedback
   set.
4. The full suite only through the isolated Fedora process below, and only when
   explicitly requested.

Do not re-run the whole suite after every edit. Do not treat a green `dev_check`
as PR-level evidence: `scripts/ci/ci_preflight.py` / `scripts/ci/ci_local.py` still own
that, and neither grants merge or runtime authority.

jj runs no git hooks, so nothing runs implicitly at commit time: `dev_check` is the
only place the cheap subset runs, including the `.env` / credential-shaped-line scan
the former pre-commit hook enforced. Use `--no-secrets-scan` only for a verified
false positive and say so in the commit description.

## Measuring test results honestly

- **Never compare failure counts across a changed denominator.** Establish the
  collected count alongside the failure count. #2945 deleted 875 test functions
  repo-wide (319 under `tests/specs/`), so "42 failed of 1407" and "29 failed of
  1728" describe different suites and the delta between them is meaningless.
- **Measure the baseline yourself; do not inherit a number from a document.**
  A recorded baseline goes stale as `master` advances (2026-09-07: a plan
  document's `141 failed` was `125 failed` by the time it was used). Use a
  throwaway worktree:
  ```sh
  jj workspace add /tmp/baseline -r master@origin
  (cd /tmp/baseline && uv run pytest tests -q -p no:randomly --tb=no) > /tmp/base.txt
  jj workspace forget baseline && rm -rf /tmp/baseline
  ```
- **Never run two full suites concurrently.** They share the data directory and
  crash each other; the crash looks like a test failure. Run them in sequence.
- **Diff the failure lists, not the counts.** `comm -13 before.txt after.txt`
  proves "no new failures"; a smaller total does not.
- Redirect full-suite output to a file. `| tail -n` discards the `FAILED` list
  you need for the diff.
- Some specs are run-to-run unstable. If a count moves with no code change,
  re-run before attributing it, and report the stable value plus the observed
  spread rather than the single most favourable number.
- Before claiming a failure is pre-existing, prove it on the parent revision:
  `jj workspace add /tmp/parent -r @-`, run the same tests there, then
  `jj workspace forget parent && rm -rf /tmp/parent`. Same reds in both ⇒ pre-existing.

## Verifying a seam, not a stub

A test that stubs the seam proves the stub, not the seam. When a change adds or
rewires an entry point that crosses a real boundary — a CLI command, an event
loop, a live venue or provider API, an environment-specific path — exercise that
real path once, in the environment that will run it, before the merge. Doubles
cannot see a constructor bound outside its loop, a request sent to the wrong
target, or a config value that never reaches the code; those defects surface only
on the real path, and each one found after the merge costs a PR plus a deployment.
Cheap real-path checks (a public API call, one CLI invocation, one smoke run) are
always worth less than the round trip they prevent.

For the same reason, do not treat deployment as a discovery loop: collect the
known failures first (focused tests, real-path smoke, runtime preflight), fix
them together, and deploy one coherent batch instead of a fix-deploy-discover
chain.

## PR metadata preflight (Phase 1)

For PR review budgets, feedback validity, lifecycle states, and merge readiness,
use the single-authority [`PR_REVIEW_LIFECYCLE.md`](PR_REVIEW_LIFECYCLE.md).

Before the first PR create/update and before pushing an exact-head repair,
prepare the intended PR body from
[`.github/PULL_REQUEST_TEMPLATE.md`](../../../../.github/PULL_REQUEST_TEMPLATE.md)
and run the repository-native deterministic preflight:

```sh
uv run --no-sync python scripts/ci/ci_preflight.py --pr-body-file /path/to/pr-body.md
```

When `scripts/ci/ci_local.py` is available, it may validate the PR head locally:

```sh
uv run python scripts/ci/ci_local.py --pr-body-file /path/to/pr-body.md
```

These scripts are local validators, not authorization to start or wait for CI.
If only `SDD Depth` / `Contract Delta` metadata is invalid after a PR exists,
fix the PR body on the **same head SHA** and rerun the local preflight. Never
add a no-op, retrigger, or metadata-only source commit. Existing successful
deep evidence for that head remains unchanged.

- Inspect first; add focused failure-path coverage for safety, lifecycle, audit,
  secrets, and trading changes; implement the smallest fix; broaden tests only
  as warranted.
- Keep the Golden Path frozen unless explicitly changed. Update documented
  contracts and executable specs together. Register a data source and DU claim
  before collection code.
- Product truth belongs in design/decision/operations docs; ownership, priority,
  progress, and defects belong in GitHub Issues/PRs. Create a Proposal only for
  major, cross-domain, or trust-boundary changes. Follow
  [documentation governance](../../../governance/policies/documentation_governance.md).
- Commit only scoped files and publish through the normal PR workflow.
- Run focused local regression tests proportional to the change. If the owner
  explicitly requests a complete suite and the result cannot wait, use an
  isolated Fedora CI/Test process in a
  dedicated Worktree with a temporary data root, no secrets, and no execution
  authority; never use the stable Runtime checkout or primary data. Windows is
  not a validation dependency or fallback.

  Run the Fedora fallback only inside a dedicated credential-free CI identity
  or container which cannot read the stable Runtime root, OS keyring, provider
  credentials, or execution credentials:

  ```sh
  export ALETHEIA_DATA_DIR="$(mktemp -d /var/tmp/aletheia-ci.XXXXXX)"
  trap 'rm -rf "$ALETHEIA_DATA_DIR"' EXIT
  uv run pytest tests/ -q --tb=line -n 2
  ```

  `-n 2` is the full-proof default; the serial / `-n 2` / `-n 4` measurements and
  the one-selection rule are in
  [PYTEST_OPTIMIZATION_GUIDE.md §六](../PYTEST_OPTIMIZATION_GUIDE.md). Re-run serially
  if a parallel run fails, before attributing any failure to the change.

  A Worktree alone does not provide this isolation. Pytest rejects explicit
  data roots inside the default production root or checkout before collection,
  resolving symlinks first. Dedicated external roots such as the /var/tmp path
  above are accepted; they need not be below Python's default temporary directory.

  Keep the run's evidence in the candidate directory: the startup probe output,
  the pytest log, and the JUnit XML. Judge failures per case from the XML rather
  than from aggregate counts — "0 failures" next to a large skip count does not
  prove that a specific file passed (some files carry their own skip escapes).

- Release tagging and deployment follow [DEPLOYMENT.md](../../deployment/DEPLOYMENT.md);
  qualification runtimes stay pinned unless an owner explicitly selects a deployment.
- The gateway is global: do not restart it casually or from competing worktrees.
- Owner updates lead with outcome, consequence, and current state.

### Reusing a full-proof receipt instead of re-running it

One isolated Fedora full run is the receipt for one tree. Before scheduling a
second run for a later head, classify the delta against the receipt's tree:

| Delta since the receipt | What to do |
|---|---|
| Documentation, comments, docstrings, or test-only changes | **Reuse** the receipt for the production change and verify the delta with focused tests (a test-only commit does not invalidate a production receipt). State this explicitly in the PR/commit, naming the receipt's candidate and the extra commit. |
| Production code, schema/migrations, Risk, execution, Gateway, trading authority, config defaults, or schedulers | **Re-run** the isolated full proof on the new head. |
| Merge commits whose content equals the receipt's tree | **Reuse**, and say the receipt covers the content rather than the merge-commit SHA. |

Merging and deploying may share one receipt: merge the verified content, deploy
that merged head, and reserve a new full run for a genuine source change. A new
run is still required whenever the merged head contains anything from the
"re-run" row above.

If the merged head contains production changes only outside `tests/` (for
example TypeScript operator-shell surfaces, which the Python suite does not
execute), the Python receipt is unaffected: verify that change with its own
suite and, where it drives a deployed process, with the runtime evidence of
that process.

One full run covers the whole `tests/` tree — it is not narrowed to "the part
the local loop does not run". The per-marker Fedora wall times and the reason
(the locally-covered tier is ~13% of the run, while narrowing would split the
no-collection-gap proof across two receipts on two operating systems) are
recorded in
[`CONVERGENCE_FINDINGS_2026-09-28.md` § 1j](../../../research/audits/CONVERGENCE_FINDINGS_2026-09-28.md).

## Common commands

- Changed-file gate (first step of every edit loop):
  `uv run python scripts/ci/dev_check.py`
- Focused test: `uv run pytest tests/test_<area>.py -q`
- Environment self-check: `uv run aletheia doctor`; lint: `uv run ruff check core extensions ops tests`
- Local validation plan / PR body preflight: `uv run python scripts/ci/ci_plan.py`;
  `uv run --no-sync python scripts/ci/ci_preflight.py --pr-body-file <file>`
- Owner/Agent runtime truth: `uv run aletheia control status --json` (the JSON
  snapshot is authoritative for Runtime identity, UFC, profit-loop, Position
  Path observer, and durable pause owners; it is read-only and may report
  explicit `unavailable` reasons). The command uses SQLite `mode=ro`; a missing
  database remains unavailable and is never created by inspection.
- Full suite: run it only when explicitly requested, through the isolated Fedora process above;
  never run it on macOS or use Windows as a validation fallback.
- Gateway: `uv run aletheia gateway <start|stop|restart|status|logs>`
- Daily/release UI: `http://127.0.0.1:8787/`
- Worktree HMR: Vite `http://127.0.0.1:5173/`; read
  [development preview](../DEVELOPMENT_PREVIEW.md) before an isolated dev API.
