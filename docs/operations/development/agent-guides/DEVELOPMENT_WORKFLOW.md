# Agent Guide: Development Workflow

## Tools and workspace

- Use normal filesystem, shell, and Git tools for search, reads, edits, patches,
  builds, tests, and repository inspection.
- Use Orca only when Orca owns the state: managed workspace/worktree lifecycle,
  Orca terminals, embedded browser tabs, automations, card state, or handoffs.
  When needed, Orca is mandatory; do not bypass it with raw `git worktree`,
  ad-hoc PTYs, or separate browser automation. Before the first Orca call, run
  `orca skills get orca-cli`; read [the Orca workflow](../ORCA_CLI_WORKFLOW.md)
  before mutating Orca state.
- External orchestration owns agents, panes, worktrees, task state, and handoffs.
  Do not build repository task databases, worker leases, handoff ledgers, or an
  orchestration CLI.
- Use a dedicated worktree/branch for non-trivial changes. In an explicitly
  requested shared checkout, re-check status before Git state changes, stage
  only named files, preserve unrelated edits, and never amend or use destructive
  Git commands without approval.

## Change and proof

When safe for the checkout, start with:

```sh
git pull --ff-only
uv sync
uv run aletheia doctor
```

Before changing code:

1. **Identify the SDD depth**:
   - **D0 = restore truth** — existing Contract / invariant already defines the target
     behavior; current code, tests, docs, or runtime facts are non-conformant.
     Repairing that gap is `Contract Delta: NONE`.
   - **D1 = change semantics** — the target behavior semantics change while long-lived
     structure and ownership remain stable.
   - **D2 = change structure** — state ownership, mainline, subsystem topology,
     authority, or product scope changes.
2. **Identify the changed boundary** and what remains unchanged.
3. **State current facts**: existing code paths, tests, durable data, runtime
   observations, or relevant Contract.
4. **Determine Contract Delta or `NONE`**: if D0, reference the existing
   Contract and do not modify it; if D1/D2, name what changes with old→new
   semantic description.
5. **Identify Non-goals**: what this change explicitly does not modify.
6. **Determine Verification**: executable tests, runtime observations, or
   economic evidence required to prove the change.

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
uv run python scripts/dev_check.py          # working tree vs HEAD
uv run python scripts/dev_check.py --staged # staged only (what the hook runs)
```

It runs, in order and only against what this change touched: `git diff --check`;
`ruff check` with `B,ASYNC,RUF` enabled **reported on changed lines only**; the
test skip/xfail census, data-guarded silent-pass audit and assertion presence
(`scripts/check_test_hygiene.py`, #3471, `check_docs.vacuous_test_failures`); the
cached previous-failure set, run by
node id so a stale cache cannot escalate into a whole-suite run;
focused tests that name or import a changed module; then `basedpyright` in
`basic` mode, error-level, changed lines only.

Why changed lines only: the repository carries roughly a thousand pre-existing
`B/ASYNC/RUF` findings and 1152 files `ruff format` would rewrite, so a
whole-file gate would fail on every edit and be ignored. The enforced contract is
"this change adds no new problem", not "the repository is clean". Do not add
`ruff format --check` to this loop without first landing a repository-wide
formatting migration.

Repair order — stop at the first red and fix it before widening:

1. `dev_check` (seconds) — ruff/hygiene/whitespace/previous red set.
2. Focused tests for the module you changed (`dev_check` selects these).
3. `uv run python scripts/dev_check.py --fast` — the `-m fast` default feedback
   set.
4. The full suite only through the isolated Fedora process below, and only when
   explicitly requested.

Do not re-run the whole suite after every edit. Do not treat a green `dev_check`
as PR-level evidence: `scripts/ci_preflight.py` / `scripts/ci_local.py` still own
that, and neither grants merge or runtime authority.

`scripts/hooks/pre-commit` enforces the cheap subset (secrets/`.env`,
whitespace, ruff on staged lines, test hygiene) and never runs pytest; it is
installed by `scripts/dev/dev-init.sh`. Use `git commit --no-verify` only for a
verified false positive and say so in the commit.

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
  git worktree add /tmp/baseline origin/master
  (cd /tmp/baseline && uv run pytest tests -q -p no:randomly --tb=no) > /tmp/base.txt
  git worktree remove /tmp/baseline --force
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
- Before claiming a failure is pre-existing, prove it: `git stash push -- <files>`,
  re-run, `git stash pop`, re-run. Same reds both ways ⇒ pre-existing.

## PR metadata preflight (Phase 1)

For PR review budgets, feedback validity, lifecycle states, and merge readiness,
use the single-authority [`PR_REVIEW_LIFECYCLE.md`](PR_REVIEW_LIFECYCLE.md).

Before the first PR create/update and before pushing an exact-head repair,
prepare the intended PR body from
[`.github/PULL_REQUEST_TEMPLATE.md`](../../../../.github/PULL_REQUEST_TEMPLATE.md)
and run the repository-native deterministic preflight:

```sh
uv run --no-sync python scripts/ci_preflight.py --pr-body-file /path/to/pr-body.md
```

When `scripts/ci_local.py` is available, it may validate the PR head locally:

```sh
uv run python scripts/ci_local.py --pr-body-file /path/to/pr-body.md
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
- Commit only scoped files and publish through the normal PR workflow. Disable a broken
  workflow; do not archive the repository.
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
  uv run pytest tests/ -q --tb=line
  ```

  A Worktree alone does not provide this isolation.
- `Prepare and tag releases` runs only after an owner selects the explicit
  `operational_need` input for an accumulated batch. It may create or refresh a
  held release candidate, but it never starts any validation workflow. Agents must not wait for external validation to
  clear that hold without a separate explicit owner request.
  Qualification runtimes remain pinned unless an owner explicitly selects a
  deployment. See the
  [release contract](../../deployment/MODULAR_MONOLITH_RELEASE.md).
- When owner-requested external validation is pending, inspect the available checks, any event state,
  failing log once. Continue independent work and monitor non-blockingly at
  intervals up to 30 minutes; do not spend the work loop polling unchanged jobs.
- The gateway is global: do not restart it casually or from competing worktrees.
- Owner updates lead with outcome, consequence, and current state.

## Common commands

- Changed-file gate (first step of every edit loop):
  `uv run python scripts/dev_check.py`
- Focused test: `uv run pytest tests/test_<area>.py -q`
- CI Yield baseline (read-only GitHub facts):
  `uv run --no-sync python scripts/ci_yield.py --limit 20
  --json-output /tmp/ci-yield.json --markdown-output /tmp/ci-yield.md`.
  Historical failures remain `unknown` unless bounded evidence proves a unique
  actionable repair; batch-size correlations are not policy thresholds.
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
