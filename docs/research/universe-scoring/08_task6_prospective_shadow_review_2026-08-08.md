# Task 6 Prospective Shadow Window Review

> Date: 2026-08-08
>
> Verdict: PASS for prospective Shadow collection and fail-closed diagnostics
>
> Causal/Paper verdict: NOT AVAILABLE
>
> Authority scorer: `universe-step-v1`
>
> Runtime/Fedora validation: pending

## 1. Accepted scope

Task 6 opens the first prospective identity for comparing the frozen step
allocation with the continuous pessimistic allocation. It does not change which
candidates are published or traded.

The registered contract is deliberately narrow:

| Field | Frozen value |
| --- | --- |
| path identity | `universe_continuous_shadow_comparison` |
| stage | Shadow only |
| assignment unit | complete scanner run |
| assignment | dedicated SHA-256 stream over policy hash + scanner-run id |
| treatment probability | `0.5` |
| control | `universe_step_v1` |
| treatment | `continuous_pessimistic` |
| point score | diagnostic only |
| primary outcome | `4h net_return_pct`, cost complete |
| outcome rows | one per complete candidate |
| authority applied | `false` |
| promotion eligible | `false` |

The `4h` horizon reuses the current canonical cost-aware settlement path and
avoids creating the rejected four-horizon matrix. This choice belongs only to
this Shadow comparison; it does not choose a future factor-suppression horizon.

## 2. Implementation result

The v9 additive migration extends the existing Twin tables with experiment,
policy epoch, behavior hash, assignment, propensity, coefficient, primary
horizon, outcome contract, assigned-policy selection, and exact outcome-key
fields.

When the 55-minute capture is due, the non-fatal post-publication worker:

1. resolves a secret-free prospective policy epoch;
2. assigns the whole scanner run to one arm without touching shared RNG state;
3. builds one compact record and one `4h` pending outcome for every candidate;
4. writes batch, records, and outcomes in one short transaction;
5. rejects incomplete clusters and conflicting immutable identities.

Unsettled Twin outcome keys are included in the existing bounded settlement
priority slice, including the final pending rows of a just-closed policy epoch.
This closes a concrete starvation risk from a large historical canonical backlog
without increasing the row or exchange-request budget.

The existing Task-5 diagnostic builder remains usable without experiment fields
or outcomes. Its compact payload bound and legacy batch-hash material remain
compatible.

## 3. Read-only evaluation

`evaluate_twin_shadow_epoch()` verifies epoch and behavior identity, deterministic
assignment, candidate order, record/batch replay hashes, fixed-horizon linkage,
selection identity, entry time, settlement eligibility, and cost version.

For complete clusters it reports:

- paired step, point, and pessimistic allocation utility;
- a logged-propensity augmented estimate;
- arm counts, inverse-propensity ESS, and consecutive eligible UTC dates;
- loss rate, absolute MAE p95, net CVaR5, estimated costs, turnover, coverage,
  missingness, and concentration;
- the existing 15-percentage-point loss-rate and 0.25-percentage-point MAE p95
  outer harm stops.

The augmented estimate is not an independent doubly robust corroboration. Full
candidate replay supplies both potential policy utilities, so its residual is
zero when linkage is correct. The report marks this field
`not_independent_full_potential_replay` and therefore keeps
`independent_dr_outcome_model_not_available` as a blocker even when its sign
matches direct replay.

## 4. Adversarial self-review

| Failure attempt | Protection | Residual conclusion |
| --- | --- | --- |
| Count candidate rows as independent trials | assignment and ESS use complete scanner-run clusters | closed statically |
| Advance authority RNG or infer its unseen draw | experiment assignment uses a separate hash stream; authority RNG lineage remains outside causal claims | closed for experiment assignment |
| Observe only selected winners | every persisted candidate receives one outcome; dropped values remain zero in the cluster denominator | closed statically |
| Let point imputation become treatment | treatment id is fixed to pessimistic; point is reported separately | closed statically |
| Mix versions or epochs | exact epoch/behavior/source/outcome/cost identities and replay hashes; mismatch is invalid | closed statically |
| Retry with changed evidence | immutable identity comparison rolls back the bundle | closed statically |
| Treat arm assignment as an actual intervention | explicit `shadow_authority_applied=false` and `no_actual_authority_intervention` blocker | causal claim blocked |
| Manufacture confidence through repeated checks | no positive conclusion exists; anytime-valid lower bound remains unavailable | promotion blocked |
| Hide feed recovery or missingness shift | source fingerprint is frozen; availability profile remains explicitly unavailable | promotion blocked |
| Feed the result back into scoring | no Learning exposure/verdict/lifecycle or factor consumer is written | self-reinforcement blocked |

## 5. Evidence still unavailable

Task 6 intentionally does not invent the evidence needed for a Paper proposal:

- no seven-date/50-cluster frozen availability reference profile;
- no exact per-observation provider lineage;
- no independent outcome model for a true DR comparison;
- no preregistered material-cohort non-inferiority margins;
- no anytime-valid lower confidence sequence;
- no actual continuous scorer intervention;
- no settled Fedora observation window.

Until these exist, a fully sampled epoch can be `negative`, `invalid`,
`insufficient_data`, or `not_available`; it cannot be `positive` or
promotion-eligible.

## 6. Verification

The focused Task-6 and compatibility suites cover:

- stable epoch and deterministic arm replay;
- declared `0.5` propensity and complete-cluster assignment;
- exactly one `4h` outcome per candidate;
- atomic/idempotent persistence and outcome identity conflict;
- Task-5 and Task-6 canonical replay;
- direct/propensity-aware diagnostic availability and explicit blockers;
- no Learning contribution-verdict write;
- additive/idempotent v13 migration after the preserved v9-v12 migration
  history;
- non-fatal post-publication service placement.

Final command counts are recorded in the implementing PR rather than treated as
durable product state. No large macOS pytest suite and no Fedora runtime restart
were performed in this review.

## 7. Final verdict and stop gate

**Task 6 passes for code-level prospective Shadow collection and honest
diagnostics.** It completes the approved six-task continuous-scoring sequence
without changing authority.

The post-review reconciliation with the current Learning contract also passes:
assigned treatment is not represented as permission or effective behavior.
Every Twin record remains explicitly non-authoritative, so the evaluator keeps
`no_actual_authority_intervention` as a hard blocker.

**It does not pass causal contribution, positive effect, Paper, or
profitability.** Factor suppression, curve adjustment, Learning measurement
support, Paper trial admission, or deployment of this branch requires a new
owner-reviewed decision after real runtime evidence is available.
