# Aletheia CI Reliability Implementation Plan

## 1. Goal

Make the pull-request and `master`-push checks truthful, independently reproducible, and aligned with the current Aletheia runtime surface without turning CI into a second product roadmap.

The target is not “run every possible test on every commit.” The target is:

```text
PR or master change
→ every affected critical implementation surface is independently checked
→ critical trading-path contracts cannot pass by skipping their own preconditions
→ cancelled runs cannot create an integrity gap
→ slow full-repository regression remains available with a realistic timeout
```

## 2. Evidence reviewed

The assessment used the current `master` workflow definitions, the latest merged PR runs, uploaded JUnit artifacts, current pytest tiering, the Operator Shell package scripts, Windows-specific runtime tests, and known E2E fixture drift.

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

`operator-shell/package.json` defines both `npm test` and `npm run build`, and recent PRs change Operator Shell behavior. The current workflow validates only `frontend/`, so Operator Shell regressions can merge while CI remains green.

Severity: **P1**.

### CI-02 — `master` push runs can cancel each other

The workflow groups runs by `github.ref` and sets `cancel-in-progress: true`. A later `master` push can cancel an earlier run. The push-side workflow integrity check uses only `github.event.before → HEAD`; therefore the cancelled range can lose its only completed integrity validation.

Severity: **P1**.

### CI-03 — Full regression is structurally unreachable

The optional full suite is the last step of the 15-minute backend job, after dependency sync, lint, architecture, specs, DCLS, workflow tests and integrity checks. The integration suite alone has exceeded that budget in recent Actions artifacts. The manual flag exists, but the job timeout makes the path operationally unreliable.

Severity: **P1**.

### CI-04 — Critical trading-path coverage is narrower than the repository contract

PR CI runs fast, integration, specs and one active-mainline Golden Path test. The repository already defines a broader `trade_chain_active_paper` command covering observer/universe/operator, spot/futures entry, close and feedback paths. That command is not an independent CI gate.

Severity: **P1**.

### CI-05 — Some execution contracts can pass through `pytest.skip`

Several execution tests first attempt a local-paper proposal and skip when Risk rejects it. After Risk prerequisites became stricter, these tests can stay green without exercising idempotency, causal linkage or fee provenance.

Severity: **P1** for test honesty.

### CI-06 — Windows-sensitive runtime behavior is untested in Actions

All current jobs run on Ubuntu. Windows-specific SelectorEventLoop tests are skipped off Windows, although the long-running local deployment path is Windows-sensitive.

Severity: **P2**.

### CI-07 — Required status-check enforcement cannot be proven from workflow YAML

The available repository connector exposes workflow and run facts but not the active branch ruleset / required status-check configuration.

Severity: **configuration verification gap**, not a code defect.

## 4. Implementation scope

### Phase A — Workflow semantics

1. Keep superseded PR runs cancelable.
2. Give every `master` push a SHA-scoped concurrency group and do not cancel it.
3. On PRs, run workflow integrity against the PR base SHA.
4. On `master` push, run full strict workflow integrity rather than an incremental range.
5. Move full regression into its own manual-only job with a 45-minute timeout.

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
5. Add a workflow contract test that parses `.github/workflows/ci.yml` and prevents accidental removal of the new gates or reintroduction of cancellable `master` runs.

## 5. Explicit non-goals

This task does not:

- change Risk or Execution production behavior;
- weaken fail-closed market-data or Opportunity identity requirements;
- add live exchange tests or credentials;
- introduce coverage-percentage gates;
- add Python-version or dependency-version matrices;
- add broad SAST tooling beyond the existing secrets scan;
- run the full repository suite on every PR;
- redesign DCLS or the Agent workflow system.

## 6. Validation plan

PR checks expected after implementation:

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

Manual validation:

```text
workflow_dispatch(full_regression=true)
→ Full Python regression audit
→ independent 45-minute timeout
→ JUnit artifact uploaded even on failure
```

Acceptance evidence:

- each job starts and completes on the PR merge ref;
- Operator Shell test and build execute in Actions, not only in local notes;
- the active paper chain passes without external network or credentials;
- Windows-only event-loop tests run rather than skip;
- the deterministic execution test creates exactly one durable causal chain and no duplicate Order on replay;
- workflow integrity passes on the governed diff;
- all reports are retained for 14 days.

## 7. Failure handling and rollback

- If the new active-paper gate exposes a genuine existing red path, keep the gate and fix only the proven test fixture or production defect; do not weaken Risk or remove the test.
- If Windows exposes a platform-only production bug, split the minimal runtime fix into a dedicated follow-up task; do not hide it with `continue-on-error`.
- If workflow runtime becomes excessive, first remove duplicated test selection between the backend and active-paper jobs; do not remove the independent Operator Shell or Windows gates.
- Rollback is a single revert of the CI hardening PR because no production trading file is modified.

## 8. Repository settings follow-up

After the PR introduces stable check names, verify the `master` ruleset requires at least:

- Secrets scan
- Python safety and contract gate
- Fast regression suite
- Integration test suite
- Active paper trading chain
- Dashboard build
- Operator Shell test and build
- Windows runtime smoke

This settings action is outside the repository diff and cannot be verified through the currently available connector.
