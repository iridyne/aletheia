# Aletheia Experiment Protocol

> **Role:** Product Truth for creating, changing, comparing, and terminating
> trading/Learning observation windows. Violating cohort identity or freeze rules
> invalidates the comparison; it never justifies weaker safety controls.
>
> This protocol is not a runtime-status page. Exact current window facts and
> progress live in runtime provenance/data and GitHub. The June 2026 "AI camera"
> Round 1 protocol was terminated on 2026-06-27 and is historical Git evidence,
> not a current active round.
>
> Last aligned with current Learning code/contracts: 2026-08-08.

## 1. What an experiment must prove

An experiment tests one named causal claim under bounded authority. It must name:

- hypothesis and treatment;
- decision consumer and allowed effect;
- statistical/assignment unit;
- treatment and control behavior, including static fallback;
- outcome source, horizon, costs, and integrity contract;
- sample, time, confidence, and harm gates;
- behavior-contract and policy-epoch identity;
- owner admission and termination rules;
- what evidence would refute or invalidate the claim.

Activity, retrospective correlation, artifact production, evaluator health, or
portfolio PnL alone is not causal contribution evidence.

## 2. Change classification

### A - repair now

Crash/hang, wrong persistence/accounting, false identity/outcome, or a safety
bypass. Repair the development line immediately. If the repair is deployed to a
qualification Runtime, the current build identity changes and the owner must
explicitly decide whether the window restarts; current admission defaults to a
fresh window unless an already-approved behavior-contract identity proves the
evidence is comparable.

### B - new observation window

Any change that can alter a decision, treatment, filter/ranker, prompt/context,
sizing/quota/allocation, guard, Risk policy, outcome economics, statistical unit,
sample/time/harm gate, or authority semantics. Close the old cohort, version the
behavior/policy identity, deploy deliberately, and start from zero. Never pool
pre/post evidence.

### C - defer

New agents, pages, assets, markets, data sources, self-evolution modes, broad
abstractions, or strategy features while the current trust/qualification gate is
unfinished. Track them in GitHub; they do not interrupt the window.

Safety tightening is never delayed for experiment purity. It is applied under its
own contract and the affected evidence window is then handled honestly.

### 2.1 A/B is not the default acceptance method

Use the least expensive evidence method that can answer the declared claim:

| Change or question | Required evidence | Randomized A/B |
|---|---|---|
| Documentation, UI, tests, refactoring, or report-only observability that cannot change a decision, outcome, identity, or cost | Review, focused regression, and contract checks | No |
| An A-class repair that restores the already-declared persistence, accounting, identity, or replay contract | Reproduction plus deterministic replay/hash/integrity regression; invalidate affected historical evidence | No |
| A frozen treatment and control can be evaluated on the same statistical unit with complete cost-aware outcomes | Prospective paired counterfactual evidence | No, unless the policy change alters future exposure and breaks the pairing |
| A policy changes future eligibility, selection, sizing, or treatment and the missing outcome cannot be reconstructed on the same unit | Pre-registered prospective treatment/control assignment | Yes |
| Promotion or behavior authority | The method-specific confirmatory, harm, and owner gates | Only when the causal claim itself requires A/B |

An observation window is a bounded evidence cohort. It is not automatically an
A/B test. Directional before/after or retrospective cohorts may prioritize the
next question, but they do not substitute for a required prospective control.

## 3. Candidate and window identity

Before an observation window starts, the owner records in runtime/GitHub evidence:

1. exact deployed commit and Runtime/session identity;
2. active data root and execution mode;
3. path/hypothesis/experiment/stage;
4. policy epoch and behavior-contract hash;
5. outcome contract, metric version, horizon, and assignment salt;
6. sample, time, allocation, and harm thresholds;
7. start time derived from the first eligible prospective exposure, not object
   registration time;
8. scheduler/outcome/evaluator continuity baseline;
9. known exclusions and unresolved integrity incidents.

Commit SHA is provenance. The deterministic behavior-contract identity decides
whether unrelated code changes fragment a Learning cohort, but deploying any new
build still requires the Qualification protocol's owner decision.

### 3.1 Window scope and safe concurrency

A window belongs to one `path / hypothesis / experiment / stage`, not to the
entire repository. One pinned stable Runtime may collect multiple independent
Shadow, paired-counterfactual, and prospective path windows concurrently when
each has its own assignment unit, policy epoch, outcome identity, and harm gates.

Concurrency does not make interacting treatments identifiable. Unless a factorial
or interference contract is pre-registered, only one behavior-changing treatment
may claim attribution over the same decision population at a time. Other paths may
continue collecting non-authoritative evidence.

Every deployed SHA opens a new Runtime provenance segment. Evidence is neither
automatically pooled nor automatically discarded solely because that SHA changed.
An explicitly audited adapter may continue a behaviorally identical path only when
it treats `code_commit` as provenance and proves unchanged behavior, input lineage,
outcome, metric, horizon, assignment, and inference identities. Otherwise current
authority admission remains build-bound. Avoid unnecessary resets by leaving the
pinned qualification Runtime unchanged for documentation, UI, monitoring, and
unrelated development changes; comparability is never an operator assumption.

## 4. Observation freeze

During a qualification window, do not deploy changes to:

- decision inputs, prompts, System Operator/ranker/filter behavior;
- Learning treatment, assignment, cohort, verdict, lifecycle, or authority logic;
- sizing, quota, allocation, persona, guard, or Risk policy;
- outcome economics, costs, funding, venue identity, or eligibility semantics;
- dependency/runtime behavior that can change those facts.

Development may continue in separate Worktrees. Documentation, UI, monitoring, and
unrelated code may merge to `master`, but the pinned qualification Runtime does not
automatically upgrade. PR merge and qualification deployment are separate actions.

Only a P0/P1 repair that blocks or falsifies Startup -> Scheduler -> Outcome ->
Verdict, accounting, identity, or safety may interrupt the Runtime. Record the
interruption, deploy the exact repair deliberately, and reopen the window according
to its classification.

## 5. Current measured Learning protocol

The registry is the executable path inventory. Both
`universe_family_calibration` and `ranker_case_score` are currently
measurement-supported; UFC is the only Paper-authority-capable path.

`ranker_case_score` uses an exact candidate-level 4h
`filter_candidate_outcomes` contract and remains Shadow-only. It does not
emit UFC trial-integrity readiness or grant Paper authority.

### Shadow measurement

- assignment is stable at the complete candidate cluster;
- treatment and control both run the static baseline while stage=`shadow`;
- first-trial integrity readiness requires 10 complete assignment units per arm
  inside at least one continuous eligible UTC market day, persisted under
  `ufc-shadow-trial-integrity/v1`;
- the 50-per-arm and seven-consecutive-market-day contract is retained for each
  Paper stage's economic promotion evidence, not spent waiting on two identical
  Shadow baselines;
- canonical/fallback outcome disagreement invalidates evidence;
- all measurement identities must match the current cohort;
- underpowered harm is `indeterminate` and cannot authorize;
- monitoring and confirmatory efficacy use conditional randomization over the
  prospectively assigned complete clusters. Adjacent 10-minute decisions may
  share 1h/4h market paths; those outcomes are never resampled as IID evidence.
  This inference also requires that one cluster's immutable full-arm outcome is
  not changed by another cluster's assignment; a violated identity invalidates evidence;
- because no treatment is applied in shadow, an integrity-ready cohort produces
  `insufficient_data/trial_integrity_ready_no_actual_intervention`; a cohort that
  also matures the full historical window remains
  `insufficient_data/no_actual_intervention`. Neither is positive/negative.

An unexplained, planned, or partial outage resets the current qualification run
when it leaves a full UTC date with no complete eligible exposure in either arm;
there is no outage-waiver contract. Daily arm imbalance remains visible and is
handled by the independent per-arm gate. Pre-gap samples remain auditable history
but do not count toward the new run's sample, economic, confidence, or harm gates.
The verdict exposes bounded `market_day_coverage` reasons.

### Owner trial and progression

Crossing `shadow -> paper_10` requires the protected owner admission route and
every default-closed gate in [PHASE_GATES](../gates/PHASE_GATES.md). The scheduled
controller may recommend the action but may not perform it autonomously.
The integrity verdict grants no economic claim: it exists only to let a human
start 10% treatment with a 90% prospective control before spending the full
confirmatory window.

Later progression is one stage at a time (`paper_10`, `paper_25`, `paper_50`,
`paper_active`) only on the current stage's authority-eligible positive confirmatory
verdict consumed from its prospective fixed-look plan. Recurring monitoring-positive
results are descriptive and hold. Neutral/insufficient holds; monitoring or
confirmatory negative/harm refreezes; invalid evidence invalidates. A control
holdout remains.

After a valid owner admission or predecessor activation, an existing bounded Paper
allocation may continue while the current verdict is `insufficient_data` and harm is
indeterminate solely because the predeclared sample/continuity window is
underpowered. This collection-liveness exception cannot promote, increase allocation,
or survive negative/invalid evidence, determinate harm, identity/config failure, or a
global refreeze.

No result grants Risk, Execution, credential, live-unlock, or direct order authority.

## 6. Comparison and verdict rules

Compare only cohorts sharing the declared causal identity. Do not pool across:

- policy epochs or behavior contracts;
- stages or assignment salts;
- horizons, outcome contracts, metric/statistical-unit versions;
- canonical outcome conflicts or integrity windows;
- unexplained Runtime/coverage gaps;
- pre/post B-class changes.

Report treatment/control counts, exclusions, actual intervention count, effect and
confidence interval, inference role/plan/cutoff/consumption state, harm state, exact
window bounds, and mismatch reasons. If any
required fact is unavailable, the result is unavailable/invalid/insufficient, never
zero, healthy, or positive by inference.

The current v5 Universe primary estimand is randomized intent-to-treat: every
qualified assigned-treatment unit remains in the primary effect and harm comparison,
including an authority-refused baseline fallback. Actual intervention count and
effective behavior provenance form a separate positive/promotion gate: permission is
recorded independently, and a cluster counts as actual only when at least one member
has a finite before/after delta. Refused and all-no-op clusters stay in ITT and dilute
the policy effect; filtering the primary arm down to changed rows is prohibited.

## 7. Stop and invalidation rules

Stop or invalidate the current comparison when:

- a current negative/invalid verdict or determinate harm breach requires safety
  withdrawal;
- outcome/accounting/identity evidence is false or cannot be reconstructed;
- the qualification Runtime changes behavior/epoch without opening a new window;
- assignment, control holdout, or intervention provenance is lost;
- a material unexplained continuity gap occurs;
- the owner closes the experiment after the predeclared decision rule.

Stopping an experiment preserves all evidence and lifecycle history. It does not
delete artifacts or silently redefine the cohort.

## 8. Evidence and governance

- Product gates: [PHASE_GATES.md](../gates/PHASE_GATES.md)
- Metric meanings: [METRICS.md](../gates/METRICS.md)
- Current Learning behavior: [learning_contract.md](../../design/contracts/learning_contract.md)
- Runtime/deployment isolation: [Qualification Development Protocol](../../operations/development/QUALIFICATION_DEVELOPMENT_PROTOCOL.md)
- Current Learning roadmap and qualification progress: GitHub issue #470 and its
  child issues, including #447 and #692

Historical experiment plans, dates, tags, and backlogs are evidence only. They do
not create a current experiment or authorize a deployment.
