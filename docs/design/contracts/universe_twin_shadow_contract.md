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
instrument deduplication, quota allocator, and the allocator's captured pre-call
random state, then annotates the existing step selection facts.

Only the step path publishes `universe_members` and Opportunities. After that
authority publication succeeds and the scanner run is marked completed, a
separate non-fatal worker may persist one compact diagnostic sample:

- at most one `universe_twin_shadow_batches` row per 55 minutes;
- one `universe_twin_shadow_records` row per coarse-filter survivor in that
  sampled scanner run.

The persisted result supports integrity replay and selected/dropped diagnostic
comparison. It is not an experiment, calibrated confidence, policy epoch,
Learning input, fixed-horizon outcome, or trading decision.

## 2. Bounded identity and payload

Each sampled batch freezes the scanner run, authority snapshot, step and
continuous behavior versions, record version, feature-source fingerprint,
continuous policy, allocation configuration, allocator random state, replay
contract, metric version, record count, blockers, and a canonical batch hash.

Each sampled candidate freezes:

- scanner-run identity and a zero-based `candidate_index`;
- candidate, feature snapshot, asset, symbol, and market identity;
- step and continuous point/pessimistic scores, ranks, fine-filter facts, and
  allocation selections;
- compact threshold crossings, missingness, flags, group sensitivity, and the
  eight largest absolute local feature sensitivities;
- allowlisted replay input, compact output, and their canonical record hash.

Compact threshold and sensitivity arrays use positional schemas owned by this
contract; they avoid repeating field names on every high-volume candidate row.
`candidate_index` is evidence: the allocator is stable on input order when
scores tie, so replay requires unique contiguous indices.

Only source-controlled feature, scanner, and score-audit allowlists are written.
Unknown fields are discarded. The provider identity is included only in a
source-contract fingerprint; exact per-observation feature lineage remains
unavailable. Credentials are never persisted.

## 3. Explicitly excluded evidence

Twin persistence must not write or link `filter_candidate_outcomes`,
`universe_forward_outcomes`, Learning exposures, assignment arms, propensities,
or treatment coefficients. A fixed-horizon outcome contract requires separate
owner review and a declared primary horizon; it is not inferred from the three
diagnostic score/selection paths.

The following invariants are mandatory:

- `shadow_authority_applied=false`;
- `promotion_eligible=false`;
- `policy_epoch_id=NULL`;
- `availability_profile_id=NULL`;
- blockers state that assignment, propensity, exact observation lineage, and
  fixed-horizon outcome evidence are unavailable.

These rows cannot authorize Paper, Risk, Execution, Learning, scorer promotion,
or scorer switching.

## 4. Cadence, transaction, and recovery

The service checks cadence before building the durable bundle. The repository
checks it again inside `BEGIN IMMEDIATE`; concurrent scanner runs therefore
cannot bypass the minimum 55-minute interval. An identical retry is idempotent.
Reusing a batch or record identity with different canonical content fails
closed and rolls back the bundle.

The persistence call is outside `_atomic_universe_publication`. Construction,
timeout, SQLite failure, or replay failure logs only the exception type and
returns zero; it cannot fail, replace, or roll back the step snapshot. A timed
out worker may finish in the background, so transactional cadence, idempotency,
and hash conflict checks remain required.

Replay is read-only. It reconstructs the continuous policy and safe inputs,
validates candidate order, reruns scoring and both allocations, and compares
canonical candidate and batch hashes. The captured process-global random state
is an integrity aid, not proof of an isolated randomized experiment: another
concurrent random consumer can cause a replay mismatch, which blocks promotion
rather than being silently normalized.

## 5. Capacity and retention

Both twin tables use the existing `universe_scanner_runs_days` window, currently
30 days. Cleanup deletes records before batches. Because Twin rows are not
experiment evidence and have no Learning linkage, an open Learning clean window
does not extend this fixed retention.

Executable payload guards require representative candidate output to remain at
most 4 KiB and replay input at most 2 KiB. The current representative payload is
about 3.2 KiB total. At 240 candidates, 24 hourly captures, and 30 days, its
JSON body projection is about 568 MB before SQLite/index overhead. Any cadence,
retention, or payload expansion requires a new capacity review.

These diagnostic tables are not part of the permanent trade audit chain.

## 6. Executable evidence and evidence boundary

Focused tests cover additive/idempotent migration, input allowlisting, compact
payload bounds, explicit unavailable identities, the absence of outcome writes,
transactional cadence, atomic idempotency, identity conflicts, replay, equal-
score input order, tamper detection, retention ordering, clean-window
independence, and persistence isolation from authority publication.

Passing these checks proves the code contract. It does not prove Fedora runtime
operation, positive economic contribution, profitability, causal effect, or
safety of future factor Learning.
