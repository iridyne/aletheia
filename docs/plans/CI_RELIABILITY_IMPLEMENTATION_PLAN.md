# Aletheia CI Reliability Implementation Plan

## 1. Goal

Make the pull-request and `master`-push checks truthful, independently reproducible, and aligned with the current Aletheia runtime surface without turning CI into a second product roadmap.

The target is not “run every possible test on every commit.” The target is:

```text
PR or master change
→ every affected critical implementation surface is independently checked
→ critical trading-path contracts cannot pass by skipping their own preconditions
→ cancelled runs cannot create an integrity gap
→ one stable aggregate result represents PR admission
→ one stable aggregate result represents final master verification
→ slow full-repository regression remains available with a realistic timeout
```

Ordinary pushes to non-`master` feature branches deliberately do not trigger CI. The repository spends Actions time only when a change is proposed for `master`, lands on `master`, or a full regression is explicitly requested.

## 2. Evidence reviewed

The assessment used the current `master` workflow definitions, the latest merged PR runs, uploaded JUnit artifacts, current pytest tiering, the Operator Shell package scripts, Windows-specific runtime tests, known E2E fixture drift, and observed GitHub concurrency behavior.

Current strengths retained:

- `pull_request` and `push` to `master` triggers;
- locked Python and Node installs;
- Gitleaks;
- architecture, executable specs, DCLS, fast and integration suites;
- frontend lint/type-check/build;
- uploaded JUnit reports;
- full Git history for Agent workflow validation;
- read-only workflow permissions.

## 3. Confirmed defects and gaps

### CI-01 — Operator Shell is outside GitHub Actions

`operator-shell/package.json` defines both `npm test` and `npm run build`, and recent PRs change Operator Shell behavior. The previous workflow validated only `frontend/`, so Operator Shell regressions could merge while CI remained green.

Severity: **P1**.

### CI-02 — `master` push runs can cancel each other

The previous workflow grouped runs by `github.ref` and set `cancel-in-progress: true`. A later `master` push could cancel an earlier run. The push-side workflow integrity check used only `github.event.before → HEAD`; therefore the cancelled range could lose its only completed integrity validation.

Severity: **P1**.

### CI-03 — Full regression was structurally unreachable

The optional full suite was the last step of the 15-minute backend job, after dependency sync, lint, architecture, specs, DCLS, workflow tests and integrity checks. The integration suite alone has exceeded that budget in Actions artifacts. The manual flag existed, but the job timeout made the path operationally unreliable.

Severity: **P1**.

### CI-04 — Critical trading-path coverage was narrower than the repository contract

PR CI ran fast, integration, specs and one active-mainline Golden Path test. The repository already defined a broader `trade_chain_active_paper` command covering observer/universe/operator, spot/futures entry, close and feedback paths. That command was not an independent CI gate.

Severity: **P1**.

### CI-05 — Some execution contracts could pass through `pytest.skip`

Several execution tests first attempted a local-paper proposal and skipped when Risk rejected it. After Risk prerequisites became stricter, these tests could stay green without exercising idempotency, causal linkage or fee provenance.

Severity: **P1** for test honesty.

### CI-06 — Windows-sensitive runtime behavior was untested in Actions

All previous jobs ran on Ubuntu. Windows-specific SelectorEventLoop tests were skipped off Windows, although the long-running local deployment path is Windows-sensitive.

Severity: **P2**.

### CI-07 — Required status-check enforcement cannot be proven from workflow YAML

The available repository connector exposes workflow and run facts but not the active branch Ruleset / required status-check configuration.

Severity: **configuration verification gap**, not a code defect.

### CI-08 — A same-SHA push can replace pending manual validation

GitHub keeps at most one pending run per concurrency group. Grouping every non-PR run only by SHA allows a duplicate push of the current `master` SHA to cancel and replace a pending `workflow_dispatch` full-regression run, even when `cancel-in-progress` is false.

Severity: **P1**.

### CI-09 — Requiring eight individual check names is operationally brittle

The eight implementation jobs are the correct diagnostic surfaces, but binding a Ruleset directly to every individual name makes protection sensitive to routine job renames and future matrix refactors. A missing required-check update could silently reduce enforcement.

Severity: **P1 configuration durability risk**.

## 4. Implementation scope

### Phase A — Workflow semantics

1. Keep superseded PR runs cancelable.
2. Give every `master` push an event-and-SHA-scoped concurrency group and do not cancel it; isolate `workflow_dispatch` from same-SHA push runs.
3. On PRs, run workflow integrity against the PR base SHA.
4. On `master` push, run full strict workflow integrity rather than an incremental range.
5. Move full regression into its own manual-only job with a 45-minute timeout.
6. Add two event-specific aggregate jobs:
   - `PR CI gate` depends on all eight required PR surfaces;
   - `Master verification gate` depends on the same eight surfaces for the final `master` SHA.
7. Make either aggregate fail when any dependency is failed, cancelled, or skipped.

### Phase B — Missing implementation surfaces

1. Add `Operator Shell test and build` on Ubuntu / Node 22:
   - `npm ci`
   - `npm test`
   - `npm run build`
2. Add `Active paper trading chain` using the existing production-shaped test list from `.agent/commands.yaml`.
3. Add `Windows runtime smoke` on `windows-latest`:
   - Windows event-loop policy tests;
   - PID/process-state tests.

### Phase C — False-green prevention

1. Add a deterministic local-paper execution test that creates:
   - an exact Opportunity identity;
   - a fresh exact market mark;
   - a complete entry plan;
   - a small position under the configured cap.
2. Assert that the proposal executes and creates one Proposal → RiskDecision → Order → Fill → Position chain.
3. Assert replay returns `already_executed` without a second Order.
4. Assert Fill fee provenance is `estimated`.
5. Add workflow contract tests that parse `.github/workflows/ci.yml` and prevent:
   - removal of critical implementation surfaces;
   - reintroduction of cancellable `master` runs;
   - collision between manual and same-SHA push runs;
   - aggregate gates omitting any required dependency;
   - aggregate gates running on the wrong event.

## 5. Explicit non-goals

This task does not:

- add CI for ordinary pushes to non-`master` feature branches;
- change Risk or Execution production behavior;
- weaken fail-closed market-data or Opportunity identity requirements;
- add live exchange tests or credentials;
- introduce coverage-percentage gates;
- add Python-version or dependency-version matrices;
- add broad SAST tooling beyond the existing secrets scan;
- run the full repository suite on every PR;
- redesign DCLS or the Agent workflow system.

## 6. Validation plan

Required implementation checks for both PR CI and master verification:

```text
Secrets scan
Python safety and contract gate
Fast regression suite
Integration test suite
Active paper trading chain
Dashboard build
Operator Shell test and build
Windows runtime smoke
```

Event conclusions:

```text
pull_request to master
→ all eight implementation checks
→ PR CI gate

push to master
→ all eight implementation checks
→ Master verification gate
```

Manual validation:

```text
workflow_dispatch(full_regression=true)
→ Full Python regression audit
→ independent 45-minute timeout
→ JUnit artifact uploaded even on failure
```

Acceptance evidence:

- each implementation job starts and completes on the PR merge ref;
- `PR CI gate` does not complete successfully unless all eight dependencies succeed;
- the merged SHA runs the same matrix and produces `Master verification gate`;
- Operator Shell test and build execute in Actions, not only in local notes;
- the active paper chain passes without external network or credentials;
- Windows-only event-loop tests run rather than skip;
- the deterministic execution test creates exactly one durable causal chain and no duplicate Order on replay;
- workflow integrity passes on the governed diff;
- all reports are retained for 14 days.

## 7. Failure handling and rollback

- If the active-paper gate exposes a genuine existing red path, keep the gate and fix only the proven test fixture or production defect; do not weaken Risk or remove the test.
- If Windows exposes a platform-only production bug, split the minimal runtime fix into a dedicated follow-up task; do not hide it with `continue-on-error`.
- If workflow runtime becomes excessive, first remove duplicated test selection between the backend and active-paper jobs; do not remove the independent Operator Shell or Windows gates.
- If an aggregate gate fails, inspect the named dependency result; do not replace it with `continue-on-error`.
- Rollback is a single revert of the CI hardening PR because no production trading file is modified.

## 8. Repository settings follow-up

After the stable check name has completed successfully at least once, configure the `master` Ruleset to:

1. require changes to enter through a pull request;
2. require `PR CI gate` before merge;
3. block force pushes and branch deletion;
4. optionally allow an explicit administrator bypass for emergency recovery, while keeping normal merges gated.

The eight implementation jobs remain visible for diagnosis, but the Ruleset should bind to the single stable aggregate check. `Master verification gate` is a post-landing health verdict and should be monitored, not used as a pre-merge required check.

This settings action cannot be read or modified through the currently installed connector, so it requires one manual GitHub Settings action.
