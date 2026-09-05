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
   - D0 — implementation conformance repair (no externally observable behavior
     change; state the existing Contract being repaired)
   - D1 — externally observable behavior change (state explicit
     `ADDED / MODIFIED / REMOVED` deltas)
   - D2 — substantial design change (requires accepted
     `docs/design/proposals/` before target state enters current Contract)
2. **Identify the changed boundary** and what remains unchanged.
3. **State current facts**: existing code paths, tests, durable data, runtime
   observations, or relevant Contract.
4. **Determine Contract Delta or `NONE`**: if D0, reference the existing
   Contract and do not modify it; if D1/D2, name what changes with old→new
   semantic description.
5. **Identify Non-goals**: what this change explicitly does not modify.
6. **Determine Verification**: executable tests, runtime observations, or
   economic evidence required to prove the change.

For D1/D2 or an explicitly selected high-risk D0, an optional pre-change shadow
audit may be run before step 1 to surface semantic gaps early. Run convergence
against the exact candidate head after implementation. Both phases are advisory
only and never a required gate. See
[SDD_SHADOW_WORKFLOW.md](SDD_SHADOW_WORKFLOW.md).

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

These scripts are local validators, not authorization to start GitHub Actions.
Remote GitHub CI is manual-only: never dispatch, start, or rerun it unless the
owner explicitly requests remote CI in the current task. Release, deployment,
merge, or broad-verification intent does not imply that permission.

It validates the same Change Contract parser and changed-file exemption logic
as GitHub CI, needs no fabricated GitHub event payload, performs no network I/O
or Runtime startup, and prints bounded structured reason codes on failure.

If only `SDD Depth` / `Contract Delta` metadata is invalid after a PR exists,
fix the PR body on the **same head SHA** and rerun the local preflight. Never
add a no-op, retrigger, or metadata-only source commit. Existing successful
deep evidence for that head remains unchanged. Remote CI remains manual-only
and requires an explicit owner request, including after metadata repair.

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
- Commit only scoped files and publish through the normal PR workflow. GitHub
  Actions uses self-hosted runners only. Disable a broken workflow; do not
  archive the repository.
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
  held release candidate, but it never starts CI. Agents must not start CI to
  clear that hold without a separate explicit owner request for remote CI.
  Qualification runtimes remain pinned unless an owner explicitly selects a
  deployment. See the
  [release contract](../../deployment/MODULAR_MONOLITH_RELEASE.md).
- When owner-requested CI is pending, inspect checks, the registered runner/queue state, and any
  failing log once. Continue independent work and monitor non-blockingly at
  intervals up to 30 minutes; do not spend the work loop polling unchanged jobs.
- The gateway is global: do not restart it casually or from competing worktrees.
- Owner updates lead with outcome, consequence, and current state.

## Common commands

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
- Full suite: run it only when explicitly requested, through remote CI or the
  isolated Fedora process above; never run it on macOS or use Windows as a
  validation fallback.
- Gateway: `uv run aletheia gateway <start|stop|restart|status|logs>`
- Daily/release UI: `http://127.0.0.1:8787/`
- Worktree HMR: Vite `http://127.0.0.1:5173/`; read
  [development preview](../DEVELOPMENT_PREVIEW.md) before an isolated dev API.
