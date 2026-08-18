# Universe Twin Prospective Shadow Evidence Contract

> Current behavior as of 2026-08-09
>
> Authority scorer: `universe-step-v2`
>
> Shadow comparison: step control versus continuous pessimistic treatment
>
> Promotion authority: none

## 1. Production entry and authority boundary

`UniverseService.refresh()` computes continuous point and pessimistic scores for
every coarse-filter survivor, then replays both continuous allocations beside
the current step allocation. Only step-v2 publishes `universe_members` and
Opportunities.

After authority publication succeeds and the scanner run is marked complete,
the separate non-fatal Twin worker may persist one prospective Shadow cluster:

- at most one complete scanner-run cluster every 55 minutes;
- one compact `universe_twin_shadow_records` row per coarse-filter survivor;
- one `4h` `filter_candidate_outcomes` row per candidate selected by the
  assigned policy arm.

Failure, timeout, identity conflict, or an incomplete outcome bundle returns no
Twin result and cannot fail, replace, or roll back the step snapshot. The
comparison does not alter ranking, fine filters, quotas, Opportunity emission,
Paper, Risk, Execution, credentials, or Live.

## 2. Prospective identity and assignment

The code opens or resolves one secret-free
`decision_policy:universe_continuous_shadow_comparison` row in
`learning_policy_epochs` only when a sampled capture is due. Its effective
policy freezes:

- step and continuous behavior versions and the continuous policy;
- Universe allocation configuration and enabled/preferred markets;
- feature-source fingerprint and the explicit unavailable availability profile;
- one `4h` primary horizon, fixed-horizon net outcome contract, current cost
  model, and allocation-utility metric identity;
- complete-scanner-run assignment, SHA-256 assignment contract, and `0.5`
  treatment probability;
- step-v2 as control and continuous pessimistic as treatment.

The scanner run is the assignment unit. Assignment is derived from the policy
hash and scanner-run id without reading or advancing Python's process-global
random generator. Every member of one run has the same arm and logged
probability. The treatment coefficient is exactly `1.0` for treatment and
`0.0` for control.

Point-imputed continuous scoring remains diagnostic. Only the pessimistic
continuous allocation is the prospective treatment candidate.

The Shadow experiment identity is not a measured Learning treatment. It writes
no `learning_exposures`, contribution verdict, lifecycle event, factor override,
or Paper admission. The future `universe_factor_curve_learning` path remains
unimplemented and unsupported.

## 3. Complete assignment evidence and selected outcomes

For every candidate, the experiment stores the assigned-policy selection fact.
Only selected candidates receive an exact outcome key, entry instrument,
assigned-policy family and direction, entry price, spread, funding input, and a
single `4h` due time. Batch, complete scanner-run evidence, and the selected
pending outcomes are one short SQLite transaction.

`outcome_settler` later settles these rows through the existing no-lookahead
OHLCV window and cost model. Unselected candidates require no fabricated market
direction or outcome and contribute zero inside the full scanner-run denominator.
No result is inferred from symbol/time proximity, and no extra 15m/1h/24h rows
are created for this comparison.

Unsettled Twin outcome keys join the canonical settler's priority slice beside
the current measured Learning window. Pending outcome state remains the source
of truth across policy-epoch rotation, so a closing epoch's final 4h rows cannot
be stranded behind a historical backlog. This changes queue order only; it does
not change price observation, economics, assignment, or authority.

An identical retry is idempotent. Reusing a batch, candidate, or outcome
identity with different immutable input fails closed. Missing assignment facts
always prevent the complete cluster from being written. Missing direction,
family, venue, symbol, entry price, or primary horizon fails closed only when
the assigned policy selected that candidate.

## 4. Stored evidence

Each batch carries the experiment, epoch, behavior, source, assignment,
propensity, coefficient, policy-arm, horizon, outcome, metric, allocator-replay,
candidate-count, blocker, and canonical hash identities.

Each candidate carries the same prospective identity plus:

- candidate index and candidate/feature/asset/instrument identities;
- step, continuous point, and continuous pessimistic score, rank, threshold,
  fine-filter, and selection facts;
- compact missingness, concentration, group, and top-eight sensitivity facts;
- the assigned-policy selection, family, direction, and optional fixed-horizon
  outcome key;
- allowlisted replay input and canonical record hash;
- `shadow_authority_applied=false` and `promotion_eligible=false`.

Credentials and unknown input fields are discarded. The provider contract is
fingerprinted, but exact per-observation feature lineage remains unavailable.

## 5. Read-only evaluation and fail-closed conclusion

`evaluate_twin_shadow_epoch()` performs no write. For each complete settled
cluster it reconstructs:

- observed-arm allocation utility as selected net return divided by the complete
  scanner-run candidate count, with unselected candidates contributing zero;
- treatment and control cluster-mean utility and their observed ITT delta;
- per-arm inverse-propensity effective sample size and consecutive eligible UTC
  dates;
- cluster loss rate, selected-candidate absolute MAE p95, cluster-utility CVaR5,
  mean estimated cost, selection rate, zero-selection clusters, selection
  turnover, settled coverage, missingness, and evidence concentration.

The evaluator does not reuse one candidate outcome to replay both policies.
Legacy direct, point-replay, and propensity-aware delta fields remain unavailable
rather than pretending that an unobserved policy arm was measured.

Evaluation replays the canonical Twin hashes, cluster assignment, behavior
hash, entry time, selection linkage, cost version, and policy epoch. Mixed or
tampered identities are `invalid`. Pending/incomplete outcomes, fewer than 50
complete clusters per arm, ESS below 50, missing arm support, or fewer than
seven consecutive eligible UTC dates are `insufficient_data`.

A sufficiently powered outer harm breach may produce a negative result. It
cannot produce a positive or promotion-capable result because the current
window deliberately lacks all of the following:

- a frozen seven-date/50-cluster pre-treatment availability profile;
- exact per-observation feature-source lineage;
- an independent outcome model for a true doubly robust comparison;
- preregistered tighter non-inferiority margins;
- an implemented anytime-valid lower confidence bound;
- an actual continuous authority intervention.

These are explicit blockers, not zero-valued evidence. The propensity field is
an assignment and effective-sample-size identity, not a second estimator or
causal proof of trading profitability.

## 6. Cadence, capacity, retention, and recovery

The service checks the 55-minute cadence before building; the repository checks
again inside `BEGIN IMMEDIATE`. Candidate output remains capped at 4 KiB and
replay input at 2 KiB. At 240 candidates and 24 captures per day, selected-only
primary outcomes have the same conservative upper bound of 172,800 rows over 30
days, and normally fewer, rather than the rejected four-horizon/per-refresh
expansion.

Twin batches and records use the fixed `universe_scanner_runs_days` retention,
currently 30 days. Their linked canonical candidate outcomes use the existing
`filter_candidate_outcomes` retention and settlement paths. Because this is not
a Learning exposure cohort, no Learning clean-window extension is inferred.

Rollback is removal of the non-authoritative prospective builder or closure of
its policy epoch. Existing step snapshots and historical evidence are not
rewritten.

## 7. Evidence boundary

Focused tests cover additive/idempotent v13 migration, stable assignment,
declared propensity, complete one-horizon outcomes, atomic/idempotent writes,
identity conflict, unselected directionless candidates, assigned-policy outcome
direction, Task-5 replay compatibility, Task-6 replay, observed-arm estimator
availability, hard blockers, absence of Learning verdict writes, and the
post-publication non-fatal service seam. In line with the current Learning
contract, assignment, permission, and effective behavior are separate facts:
this Shadow path records assignment but deliberately applies neither authority
permission nor an actual continuous intervention.

Passing these checks proves bounded static behavior only. Fedora runtime
operation, settled sample sufficiency, positive economic contribution,
profitability, Paper admission, and Live readiness remain not available until
separately observed and reviewed.
