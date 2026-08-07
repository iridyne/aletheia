# Universe Twin Shadow Evidence Contract

> Current behavior as of 2026-08-08
>
> Authority scorer: `universe-step-v1`
>
> Diagnostic scorer: `universe-continuous-shadow-v1`
>
> Promotion authority: none

## 1. Production entry and end result

`UniverseService.refresh()` computes a continuous point and pessimistic twin for
every coarse-filter survivor, replays both arms through the current fine filter,
instrument deduplication, quota allocator, and the allocator's pre-call random
state, then annotates the existing step selection facts.

Only the step path publishes `universe_members` and Opportunities. After that
authority publication succeeds and the scanner run is marked completed, a
separate non-fatal worker persists:

- one `universe_twin_shadow_batches` identity row per scanner run;
- one `universe_twin_shadow_records` row per coarse-filter survivor;
- direction-specific canonical `filter_candidate_outcomes` rows at 15m, 1h,
  4h, and 24h.

The persisted result supports integrity replay and selected/dropped diagnostic
comparison. It is not an experiment, calibrated confidence, policy epoch,
Learning input, or trading decision.

## 2. Identity contract

Each batch freezes the scanner run, authority snapshot, step and continuous
behavior versions, record version, feature-source fingerprint, continuous
policy, allocation configuration, allocator random state, replay contract,
outcome contract, cost-model version, metric version, record count, blockers,
and a canonical batch hash.

Each candidate freezes:

- scanner-run cluster and zero-based `candidate_index`;
- candidate, feature snapshot, asset, symbol, and market identity;
- step and continuous point/pessimistic score, rank, fine-filter, and allocation
  facts;
- complete declared raw-factor evidence, units, neutral comparisons,
  sensitivities, group evidence, missingness, interaction residual, and flags in
  canonical JSON;
- separate authority, point, and pessimistic direction-outcome keys;
- canonical input and record hashes.

`candidate_index` is evidence, not decoration: the allocator is stable on input
order when scores tie. Replay requires unique contiguous indices and uses that
captured order instead of inventing a new tie-break.

Only source-controlled feature, scanner, and score-audit allowlists are written.
Unknown feature fields and provider configuration content are discarded. The
configured primary provider identity is included in a source-contract
fingerprint; exact per-observation feature lineage is not currently available
and remains an explicit promotion blocker. Credentials are never persisted.

## 3. Outcome meaning

One candidate can generate at most two direction identities, `long` and
`short`. Arms with the same direction share the same outcome key, so market
movement is measured once rather than duplicated per scoring arm. Each identity
uses the existing four-horizon `filter_candidate_outcomes` settlement path and
cost model.

`allocation_selected` remains `NULL` on these shared outcome rows because
selection differs by arm. Authority, point, and pessimistic selection facts live
on the twin candidate record. Missing direction or entry price produces no
outcome identity and is not converted to zero return.

## 4. Authority and unavailable facts

The following invariants are mandatory:

- `shadow_authority_applied=false`;
- `promotion_eligible=false`;
- `policy_epoch_id=NULL`;
- `availability_profile_id=NULL`;
- `assignment_arm=NULL`;
- `propensity=NULL`;
- `treatment_coefficient=NULL`.

The batch records explicit `not_available` blockers for those experimental
facts. Task 5 must not infer them from rank, selection, or historical return.
Until a separately reviewed later task supplies prospective assignment and
overlap evidence, these rows cannot authorize Paper, Risk, Execution, Learning,
or a scorer switch.

## 5. Transaction, failure, and recovery

Serialization occurs before taking SQLite's writer slot. Batch, candidate, and
outcome registration use one short `BEGIN IMMEDIATE` transaction. An identical
retry is idempotent. Reusing a batch or record identity with a different hash
fails closed and rolls back the bundle.

The persistence call is outside `_atomic_universe_publication`. Construction,
timeout, SQLite failure, or replay failure logs only the exception type and
returns no diagnostic count; it cannot fail, replace, or roll back the step
snapshot. A timed-out worker may finish in the background, so idempotency and
hash conflict checks remain required.

Replay is read-only. It reconstructs the continuous policy and safe inputs,
validates candidate order, reruns scoring and both allocations, and compares
canonical candidate and batch hashes. A mismatch is an integrity failure, not
permission to rewrite history.

## 6. Retention

Both twin tables use the existing `universe_scanner_runs_days` window, currently
30 days. The cleanup job deletes candidate records before batch rows. The
effective cutoff is extended back to the earliest durable exposure of an active
open experiment, matching the existing clean-window safety rule. Canonical
outcomes keep their independent `filter_candidate_outcomes` retention path.

These diagnostic tables are not part of the permanent trade audit chain.

## 7. Executable evidence and evidence boundary

Focused tests cover additive/idempotent migration, input allowlisting, explicit
unavailable identities, all-candidate persistence, direction-shared outcomes,
atomic idempotent writes, identity conflicts, random-state replay, equal-score
input-order replay, tamper detection, retention ordering, clean-window
protection, and persistence isolation from authority publication.

Passing these checks proves the code contract. It does not prove Fedora runtime
operation, settlement coverage, positive economic contribution, profitability,
or safety of Task 6 factor Learning.
