---
id: universe-continuous-scoring-safety
status: accepted
related_contracts:
  - ../contracts/learning_contract.md
  - ../contracts/filter_evidence_contract.md
  - ../contracts/opportunity_outcome_contract.md
supersedes: []
---

# Universe Continuous Scoring Safety Proposal

> Proposal status: accepted design, not an implementation or runtime claim.
>
> Accepted scope: continuous Shadow scoring and the prerequisites for any
> later factor-level Learning experiment. The current step scorer remains the
> only authority until a separate owner-gated Paper admission.

## Summary

Universe factor values should reach ranking as bounded continuous evidence, but
continuity alone is unsafe. Repeated use of the same raw feature can manufacture
apparent corroboration, missing feeds can reward ignorance, selective exposure
can permanently starve a weak cohort, and repeated evaluation can turn noise
into a self-reinforcing policy.

This proposal adopts the curve specification in the dated
[continuous-curve evidence](../research/counterfactual/07_universe_continuous_curve_contract_proposal_2026-08-07.md)
and adds four fail-closed safeguards:

1. aggregate and constrain influence by raw evidence source, not function name;
2. compute a pessimistic missing-data score and rotate the evidence window when
   source availability changes;
3. permit only expiring, negative-only factor suppression while preserving a
   continual control and full-candidate observation stream;
4. require pessimistic, anytime-valid, cohort-aware promotion evidence with
   overlap and tail-harm gates.

The design deliberately uses transparent deterministic curves rather than an
online neural scorer. For Aletheia's small, non-stationary, high-consequence
sample, the frontier-relevant improvement is more honest uncertainty and safer
causal evaluation, not greater model complexity.

## Current fact

The normative [Learning contract](../contracts/learning_contract.md) currently
marks only `universe_family_calibration` as measurement-supported. Factor,
ranker, guard, and other Learning hooks retain static fallbacks. The frozen
`universe-step-v1` scorer remains authoritative; `universe-continuous-shadow-v1`
is reserved and is not active.

No current Learning path automatically edits an individual RSI, return,
volume, funding, OI, spread, depth, or penalty curve. This proposal does not
change that fact, enable Paper influence, or describe the Fedora Runtime as
updated.

## Goals and non-goals

Goals:

- preserve the declared continuous anchors, caps, signs, direction precedence,
  and hard safety/data gates;
- make repeated raw evidence, missingness, local sensitivity, and score
  concentration directly auditable;
- prevent unavailable information from improving an authority-eligible score;
- make suppression reversible and keep weak cohorts observable;
- admit a later factor treatment only when cost-aware benefit is positive under
  conservative, prospective, cohort-aware evidence.

Non-goals:

- new market signals, strategies, exchanges, data providers, or asset classes;
- online gradient updates, unrestricted learned curves, per-trade reward or
  punishment, or an LLM-written scoring formula;
- quota, sizing, Risk, Execution, credential, or Live changes;
- simultaneous continuous-score and factor-Learning treatment;
- probability calibration of the ordinal `0..100` ranking score.

## Proposed design

### 1. Authority stages

The stages are strictly ordered:

```text
frozen step-v1
  -> pure continuous curve library
  -> step-authoritative twin Shadow scorer
  -> prospective curve comparison in Shadow
  -> separately registered factor-suppression experiment
  -> owner-gated paper_10 -> paper_25 -> paper_50
```

`paper_active` is not an automatic destination. Each transition requires the
current Learning lifecycle, a new behavior contract and policy epoch where
required, and explicit owner admission. Continuousization and factor adaptation
must use separate cohorts and cannot share evidence as if they were one
treatment.

### 2. Safeguard one: raw-evidence aggregation and concentration

Every curve input has one stable `raw_feature_id`, one `evidence_group_id`, and
an explicit list of all consuming curve ids. The same observation does not
become independent evidence merely because scanner, tradability, allocator,
late-entry, or crowding code consumes it again.

The initial evidence groups are:

| Evidence group | Raw features |
| --- | --- |
| `price_path` | returns, relative strength, beta-adjusted relative strength, RSI, ATR, and Bollinger width/squeeze derived from the same price history |
| `market_activity_liquidity` | quote volume, volume ratio, spread, and depth |
| `positioning_flow` | funding, OI change, and taker ratio |
| `data_quality_cost` | freshness, observed/imputed state, and explicit cost inputs |

`data_quality_cost` is reported separately and is not counted as independent
alpha corroboration. RSI, return, ATR, and Bollinger transforms remain separate
raw feature ids for audit, but they share `price_path`; deriving several
indicators from one price series does not create several independent witnesses.

For each candidate, the twin scorer records:

- every raw feature's observed/imputed/invalid state and consumer curve ids;
- the total-score delta produced by moving that raw feature to its declared
  neutral value through every consumer at once;
- a dimensionless local perturbation delta and the resulting total-score
  sensitivity in score points;
- the same neutral delta and sensitivity after grouping raw features;
- observed signal-group count and
  `max_group_abs_sensitivity / sum_group_abs_sensitivity`;
- an explicit residual when interactions mean grouped deltas do not add to the
  total score. Group deltas are audit diagnostics, not causal attribution.

The perturbation is applied to the complete score function once per raw
feature. Summing per-function derivatives is forbidden because it would count
one observation multiple times. Its normalized coordinate is source-controlled:
the sorted union of all consuming curves' distinct raw-input anchors maps to
consecutive integer coordinates, the default perturbation is `±0.05` in that
coordinate, and endpoints use a one-sided perturbation. This makes sensitivity
comparable per declared transition interval instead of per arbitrary raw unit;
changing the anchor union or perturbation rule changes the behavior identity.

Each raw feature also has a frozen influence
budget equal to the maximum total-score movement that `universe-step-v1` can
produce from that feature across the golden boundary fixture. The continuous
scorer cannot exceed that budget without a new proposal decision.

Fail-closed policy:

- an undeclared duplicate consumer or missing raw-feature identity makes the
  Shadow record invalid;
- fewer than two observed signal groups is `single_source_evidence`;
- with at least two observed signal groups, a maximum sensitivity share above
  `0.50` is `evidence_concentrated`;
- either condition permits diagnostic Shadow calculation but makes the
  candidate and affected cohort ineligible for factor-treatment promotion.

The `0.50` threshold is an Aletheia project policy, not a universal research
constant. It is intentionally conservative and cannot be tuned on the same
outcomes used for promotion.

### 3. Safeguard two: missingness cannot improve authority

The Shadow scorer computes two totals from the same observed inputs:

- `point_score`: the explicit labelled imputation in the curve specification,
  retained for comparison with existing behavior;
- `pessimistic_score`: the lowest admissible total under the declared domain of
  every missing input while all observed inputs remain fixed.

For additive factors the lower value is the minimum declared curve
contribution. For multipliers it is the minimum admissible multiplier applied
to the affected subtotal. For declared interactions, the implementation uses
the finite anchor extrema of all missing inputs jointly. Missing core direction,
freshness, or another required hard-gate value remains invalid instead of being
bounded. Invalid, NaN, infinite, or wrongly typed input is never imputed.

The implementation must preserve:

```text
pessimistic_score <= point_score
pessimistic_score(missing x) <= score(observed x = v)
for every admissible observed v
```

`missingness_delta = point_score - pessimistic_score` and the responsible raw
feature ids are persisted. Point-score ranking remains diagnostic only. Any
future authority proposal must select and evaluate the pessimistic policy;
crossing a threshold only under the point score grants no eligibility.

For example, missing spread retains the curve specification's labelled
point-score prior, but its pessimistic bound uses the worst admissible
tradability contribution and cost within the hard spread gate. A display or API
must label the point result as diagnostic/imputed; it cannot present it as
confidence or an authority-eligible ranking fact.

Each behavior identity includes a secret-free feature-source fingerprint:
producer and provider semantics, schema/version, units, freshness contract,
imputation policy, cost model, and availability-profile id. A schema, semantic,
provider, unit, or imputation change immediately rotates the policy epoch and
starts evidence from zero.

The availability profile is frozen from a separate pre-treatment reference
window with at least seven complete UTC dates and 50 complete scanner clusters.
For every declared cohort and critical feature, coverage is reported against
that profile. An absolute coverage drift greater than 10 percentage points on
two consecutive complete UTC dates, with at least 50 complete clusters in each
compared window, sets `availability_drift`, blocks promotion, and requires a new
reference profile and epoch. A newly configured source never activates inside
an existing cohort even if its values look beneficial.

The 10-point/two-day rule is a project change detector, not evidence that
smaller shifts are harmless. Missing reference evidence is `not available` and
blocks promotion.

### 4. Safeguard three: reversible suppression and continual observation

The future path name is `universe_factor_curve_learning`. It remains
`measurement_supported=False` until all verification in this proposal exists.

Its first admissible treatment class is deliberately one-sided:

```text
if baseline contribution > 0:
    treated contribution = baseline contribution * (1 - alpha)
else:
    treated contribution = baseline contribution

alpha in a preregistered finite set contained in [0.00, 0.10]
```

Thus Learning may shrink one positive factor bonus toward neutral by at most
10%. It cannot increase any bonus, reverse a sign, weaken a penalty, change a
hard gate, reduce an explicit cost, improve a missing-data bound, or modify more
than one factor/cohort/horizon in the same experiment. The baseline curve stays
immutable and independently replayable.

Only one Learning treatment may influence a candidate in this experiment. The
current family calibration multiplier, operator scanner weights, family quotas,
regime gates, and all other concurrent policy inputs must be frozen and applied
identically to both arms. Any overlapping Learning treatment or mid-window
change invalidates the factor cohort and rotates its epoch; observational
context from another path cannot be credited as factor contribution.

Shadow measurement records and settles 100% of hard-gate-eligible candidates,
including candidates that allocation drops. This preserves observation of a
suppressed cohort without requiring risky Paper trades. Assignment remains
stable at complete scanner-cluster level with declared propensities. Any Paper
stage retains a stable control holdout of at least 10%; no arm or eligible
cohort may be silently removed because its recent result is weak.

Treatment authority expires after 30 UTC days or immediately on policy-epoch,
behavior, source, outcome, or cost-contract change. Expiry restores the frozen
baseline, not the latest learned value. Renewal requires a new prospective
window, fresh data, and explicit owner admission; the old winning window cannot
renew itself. A negative, invalid, harm, or unavailable verdict restores the
baseline and preserves all evidence.

The 10% shrink, 10% control, and 30-day expiry are conservative Aletheia policy
bounds. They are not estimates of optimal market behavior and cannot be relaxed
from retrospective results.

### 5. Safeguard four: pessimistic promotion gate

Each prospective experiment preregisters before its first exposure:

- one factor, one coefficient candidate, one primary cohort, one horizon, and
  one cost-aware allocation-utility metric;
- the complete statistical unit and stable assignment propensity;
- a finite treatment set, evaluation schedule, total error budget, declared
  cohorts, non-inferiority margins, and invalidation conditions;
- behavior, policy, feature-source, availability, outcome, cost, and metric
  versions.

Curve discovery/tuning and confirmatory evaluation use disjoint time windows.
The confirmatory window cannot choose a different curve after seeing its
outcomes. With `N` preregistered primary, harm, and cohort claims, each
confidence sequence uses at most `0.05 / N` error so optional stopping and
multiple comparisons do not manufacture a winner.

Promotion is unavailable unless all of the following hold:

1. treatment and control each have at least 50 complete scanner-cluster units
   within at least seven consecutive eligible UTC dates;
2. inverse-propensity effective sample size is at least 50 per arm, every
   evaluated action has positive logged support, and no unresolved overlap or
   confounding warning remains;
3. direct full-candidate allocator replay and a propensity-aware doubly robust
   estimate agree on the sign of the effect;
4. an anytime-valid, variance-adaptive lower confidence bound for incremental
   net allocation utility is greater than zero;
5. the pessimistic missing-data policy, not the point-imputed policy, is the
   treatment being evaluated;
6. current loss-rate and MAE hard-harm limits remain clear, and preregistered
   non-inferiority bounds pass for loss rate, MAE p95, net-return CVaR at 5%,
   fees/turnover, evidence concentration, and coverage;
7. every preregistered material cohort with adequate support passes the same
   direction and harm rules after multiplicity control.

The current Learning hard-harm ceilings remain outer stops: a 15-percentage-
point absolute loss-rate increase or a 0.25-percentage-point MAE p95 worsening
is a determinate breach. They are not acceptable target margins. Each factor
experiment must preregister tighter non-inferiority margins; an absent margin is
`not configured` and blocks the experiment instead of defaulting to a reassuring
zero.

Material cohorts are declared before exposure from the relevant subset of
family, direction, market type, regime, asset class, liquidity tier, and data-
completeness tier. A cohort without adequate overlap or power is `not available`.
It blocks a global claim but may support a later, explicitly narrower proposal
that excludes it. Equal scores, equal candidate counts, and equal profits are
not required; equal evidence standards and protection from hidden cohort harm
are required.

No overlap, incompatible estimators, incomplete clusters, economic-source
conflict, missing propensity, availability drift, source/epoch mismatch, or
underpowered harm produces a positive point claim. The result is bounded,
insufficient, invalid, or not available and cannot authorize.

### 6. Fairness meaning

Fairness in this trading scorer has four bounded meanings:

- **evidence fairness:** identical observed evidence, units, and policy identity
  receive identical transforms; intended long/short symmetry is property-tested;
- **missingness fairness:** ignorance cannot improve the authority-eligible score;
- **exposure fairness:** full eligible candidate outcomes and a continual
  control prevent a suppressed cohort from disappearing from measurement;
- **cohort safety:** the same prospective evidence and harm standards apply to
  every declared material cohort.

It does not mean equal outcomes across strategies or forcing capital into an
unsafe arm. It also does not treat correlation, SHAP, an LLM judgment, or a
retrospective scorecard as causal factor credit.

### 7. Required Shadow record

The later twin record must contain at least:

- scanner-run cluster, candidate, symbol, market, family, direction, and
  feature-snapshot identities;
- step score, continuous point score, continuous pessimistic score, rank delta,
  selected/dropped delta, and threshold crossings;
- curve/behavior/policy/source/availability/outcome/cost/metric identities;
- raw feature values, units, observed/imputed/invalid state, neutral value,
  perturbation delta, consumer ids, contribution, sensitivity, group, and
  missingness lower bound;
- group concentration, observed group count, interaction residual, and flags;
- assignment unit, arm, propensity, treatment coefficient, actual authority
  applied, and full outcome provenance.

Missing required fields invalidate the record for promotion; they never receive
a default value that implies evidence exists.

## Trust and complexity review

This design touches ranking and future Learning authority, so it is a B-class
behavior change and a cross-domain trust boundary. The smaller alternative of
only smoothing point values is insufficient because it retains hidden repeated
evidence, optimistic missingness, selective feedback, and repeated-testing
failure modes.

The proposal adds no order path, exchange client, credential access, Risk limit,
Live unlock, new data feed, database authority, or autonomous policy writer. It
requires deterministic source-controlled manifests and bounded arithmetic.
Persistent schema and consumer wiring belong to later tasks and cannot be
inferred from this accepted design.

## Verification contract

Before the continuous scorer can even enter twin Shadow:

- the curve and missingness property suite passes for anchors, continuity,
  bounds, symmetry, invalid inputs, pessimistic dominance, raw-feature identity,
  influence budgets, and concentration flags;
- the frozen step scorer and hard-gate golden fixture remain unchanged;
- pure implementation contains no import or call from an authority consumer.

Before factor Learning can become measurement-supported:

- the registry, behavior contract, policy/source identity, complete-cluster
  assignment, propensities, full-candidate settlement, replay, doubly robust
  estimate, confidence sequence, harm/cohort gates, expiry, refreeze, and static
  fallback are covered by focused specs and an isolated end-to-end test;
- an adversarial test proves no treatment can boost a bonus, weaken a penalty or
  gate, survive expiry, pool epochs, omit a control, or authorize on missing
  overlap/availability/margin evidence;
- current Learning, Risk, Execution, and active-mainline Golden Path regressions
  remain green on an approved non-macOS validation host.

Passing tests proves bounded behavior, not profitability or positive runtime
contribution.

## Rollout and rollback

1. Implement pure curve and pessimistic-bound functions with no production
   consumer.
2. Wire a twin Shadow record while keeping `universe-step-v1` authoritative.
3. open a new behavior version and policy epoch; collect all-candidate outcomes;
4. report distribution, turnover, missingness, concentration, cohort, cost, and
   tail-risk evidence;
5. only then consider a separately registered factor-suppression experiment.

Any invalid, negative, harm, identity, source, or availability condition leaves
or restores `universe-step-v1`. Rollback is removal of the non-authoritative
twin consumer or expiry/refreeze of a separately admitted treatment; it never
rewrites historical records.

## Resolution

Accepted on 2026-08-07 for staged, non-authoritative implementation. Acceptance
authorizes the next pure-curve task only; it does not claim implementation,
Fedora deployment, Paper influence, positive contribution, or profitability.
The four safeguards are mandatory prerequisites, not optional follow-up work.
The dated implementer/adversarial evidence is recorded in the
[task 2 review](../research/counterfactual/08_universe_continuous_scoring_adversarial_review_2026-08-07.md).
