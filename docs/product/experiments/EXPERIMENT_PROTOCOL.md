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

The registry is the executable path inventory. `universe_family_calibration`,
`ranker_case_score`, and `momentum_entry_geometry` are measurement-supported.
UFC and Momentum geometry can receive owner-gated Paper authority. Momentum uses
one exact Candidate, a stable control/treatment assignment, and the exact 4h
`filter_candidate_outcomes` adapter; v2 execution additionally requires its
immutable applied-treatment exposure receipt. `ranker_case_score` remains
Shadow-only and grants no Paper authority.

### Shadow measurement

- assignment is stable at the complete candidate cluster;
- treatment and control both run the static baseline while stage=`shadow`;
- for UFC, first-trial integrity readiness requires 10 complete assignment units
  per arm inside at least one continuous eligible UTC market day, persisted under
  `ufc-shadow-trial-integrity/v1`; `ranker_case_score` deliberately emits no UFC
  trial-readiness metadata because it has no Paper authority;
- for UFC, the 50-per-arm and seven-consecutive-market-day contract is retained
  for each Paper stage's economic promotion evidence, not spent waiting on two
  identical Shadow baselines; ranker-case Shadow remains a separate candidate-
  level measurement window;
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
## 9. Universe Discovery Benchmark (#2556)

The Universe Discovery Benchmark is a read-only, prospective measurement window.
It does not change Universe scoring, winner selection, allocation, Risk,
Execution Gateway, Paper, Live, or promotion authority. Every result carries
promotion_eligible=false; a positive result can only motivate a separately
reviewed proposal.

### 9.1 Frozen contract

Before the first accepted sample, the runtime records a canonical contract and
SHA-256 hash containing the benchmark, baseline-integrity, scanner, allocator,
outcome/economics versions, exact boot commit, relevant Universe/Exchange config,
fee/slippage/fallback-spread values, cohort and matching rules, horizons,
cadence, completeness gates, stopping rule, statistical methods, and conclusion
rules. A changed commit or frozen field invalidates the active window; runtime
sessions on the same commit remain separately attributable and are all reported.

The population is the completed scanner run's unique_scored after the existing
asset filters and spot/perpetual de-duplication—the same population seen by the
allocator. T0 is that run's entry_ts and its captured last_price; future prices
are never backfilled. A window accepts at most one completed run per four hours
for fourteen days (at most 84 batches), never stops early on returns, then waits
through the 24-hour horizon plus a fixed six-hour grace. It cannot finalize
before start + 15d + 6h. Inference requires at least 60 batches across at least
10 UTC dates; otherwise the package is still emitted with unavailable.

### 9.2 Cohorts and outcomes

All cohorts come from the same full batch population, with stable identity tie
break (venue, market_type, symbol, asset_id):

1. production_selected: allocator-selected items with exact consumable venue,
   execution_eligible=true, valid T0, and a tradable direction. Allocated but
   ineligible items remain explicit unavailable evidence.
2. matched_non_selected: non-selected items with the same venue, market, and
   direction; exact volume/spread/ATR terciles; nearest no-replacement match by
   L1 percentile distance over volume, spread, ATR, and absolute 4h return.
   There is no cross-venue or relaxed fallback.
3. Each lossless #2601 family (momentum, mean_reversion, volatility,
   derivatives, event shown as Market Shock / Event Proxy, and short_bias)
   contributes eligible top-K items using its own score and direction. These are
   observational family cohorts, not production winners.
4. simple_momentum is the pure trailing-4h continuation baseline: score
   abs(return_4h) over positive-return spot assets and admit only spot long.

An asset/direction may belong to multiple cohorts but receives one shared outcome
key. The complete population fact, cohort membership, exact identity, T0 facts,
family evidence, production rank/eligibility, match status, commit, and contract
hash are retained. Cohort members receive 15m/1h/4h/24h outcomes; only 4h is
confirmatory and all other horizons are descriptive.

Batch facts and outcome intents are written atomically and idempotently. Outcome
economics are recomputed from settled entry/exit/path facts with the window's
frozen cost model, not the settlement-time configuration. Reports include counts,
coverage, net-return distribution, win rate, MFE/MAE, adverse p10/CVaR10,
settlement lag/source, direction/regime/market slices, and symbol/date
concentration (maximum share, HHI, and unique counts). Missing facts remain
unavailable; they are never converted to zero or healthy evidence.

### 9.3 Inference and terminal conclusions

Selected versus matched control uses asset-level paired differences with 10,000
capture-batch cluster-bootstrap replicates and 10,000 paired sign-flip replicates.
It requires outcome coverage at least 80%, matching coverage at least 70%, and at
least 40 complete paired batches. Family comparison computes per-batch top-K 4h
means for the six families plus simple_momentum, compares all 21 pairs with the
same paired procedures, and applies Holm correction; each comparison requires 40
complete batches.

useful requires complete integrity, positive selected-control 95% CI lower bound,
p < 0.05, and positive selected 4h mean net. not_useful requires complete
integrity and either a negative lift CI upper bound or a negative selected
mean-net CI upper bound. Otherwise complete evidence is neutral; identity,
window, coverage, matching, or contract failure is unavailable. The family label
is supported_best only when one family has positive 4h mean net and beats every
other family and the simple baseline after Holm correction; otherwise the report
is descriptive and says no_supported_winner. Negative, neutral, and unavailable
are valid final outcomes; the window cannot be rewritten by changing its horizon,
population, or stopping date.
