# Profit-loop observability contract

## Purpose and current scope

Aletheia exposes read-only, versioned evidence for replaying one closed Position,
reconstructing the complete candidate denominator, evaluating a fixed exploratory
counterfactual registry, and applying seven-layer unique-winner gates.

This capability has no trading authority. It does not change Ranker, System
Operator, Risk, Execution, position protection, Learning, Live, or an existing
experiment cohort.

## Entry points

The production entry point is `aletheia inspect`:

- `position-replay --id <position_id> --as-of <timestamp> --json` emits one
  `profit_loop_fact/v1` Position fact;
- `candidate-facts --horizon <15m|1h|4h|24h> --as-of <timestamp> --json` emits
  the complete `candidate_fact` denominator at one fixed horizon;
- `position-diagnosis` and `candidate-diagnosis` emit
  `profit_loop_diagnosis/v2` artifacts without changing their inputs;
- `profit-counterfactual --cost-model <json>` evaluates the fixed
  `profit_loop_counterfactual/v1` registry against one durable evidence epoch;
- `profit-gradient --evidence <json>` applies `profit_gradient/v2` gates to a
  frozen evidence artifact.

All commands are synchronous, read-only inspection commands. The cost-model and
gradient evidence files are inputs only.

## Durable state and consumers

No replay, diagnosis, counterfactual, gradient, manifest, or protected-retention
table exists.

Profit-loop registration reuses `learning_policy_epochs` with
`epoch_kind=evidence_anchor:profit_loop_observability`. Gateway startup creates
the anchor only after a durable Runtime Session exists. Its immutable identity
is the exact Runtime Session boot commit, behavior fingerprint, and canonical
SHA-256 of the five-treatment registry; the offline `evidence epoch register
--commit` override cannot supply or replace this identity. No migration,
configuration field, or Learning treatment is introduced.

Position replay joins existing immutable IDs across Opportunity, PI decision,
Proposal, authoritative Risk decision, Order, Fill, Position, exit plan,
protection state, settlement, runtime provenance, and `market_ohlcv`.

New native entry Proposals freeze `position_hypothesis/v1` inside the existing
immutable `trade_proposals.attribution_snapshot` before the Proposal commit hash
is computed. Historical Proposals and non-entry Proposals remain `not_recorded`;
they are never backfilled.

The final consumer is the Owner or an offline analysis process. No runtime
behavior owner imports these analytics modules.

## Observable result

`candidate_fact` and `position_fact` are separate statistical populations:

- Selection, Decision, and Risk use `outcome_candidate_key` and a fixed-horizon
  net outcome;
- Execution, Holding, Exit, and Costs use verified settled Position economics,
  but each sample also carries a distinct `harm_event_id` and causal stage
  window. `position_id` is not a global de-duplication key.

Position replay reports exact causal IDs, evidence cutoff, economics residual,
runtime/behavior identity, ordered-path coverage, canonical OHLCV window hash,
and explicit unavailable reasons. `positions` has no persisted `exit_price` in
this scope, so replay/attribution derives its exit price solely from verified
close-fill VWAP and is unavailable without it; no schema migration is implied.
Candidate replay preserves filtered, skipped, rejected, unfilled, and traded
terminal states.

The gradient report always emits
`Selection / Decision / Risk / Execution / Holding / Exit / Costs`, quote harm,
basis points, per-100-candidate harm, per-capital-day harm, clustered interval,
concentration, exclusions, residual state, and gate reasons. Its verdict is
`unique_max_direction` only when the declared comparison method allows a Winner;
otherwise it is `no_unique_direction`.

Every gradient evidence context must declare
`gradient_contract_version="profit_gradient/v2"`,
`evidence_integrity="verified"`, and one comparison method; unverified or old
v1 evidence is not reinterpreted. Every sample must contain
`harm_event_id`, `attribution_event_id`, `component`, `ownership`,
`stage_window`, non-empty `evidence_refs`, and a positive `notional_quote`
that reconciles `paired_net_delta_bp = paired_net_delta_quote / notional_quote *
10000`. Position layers additionally require `position_id`. A harm event occurring in
more than one layer is removed from every affected layer and retained only as an
unresolved residual; it cannot produce a Winner. `cost_event_id` ownership is
also global: Execution cannot carry Costs evidence, and one cost event cannot
cross layers. The same Position may still produce multiple samples when their
harm events and stage windows are distinct.

## Invariants and trust boundaries

- Replay never selects a latest related record as a fallback. Every causal join
  uses an exact durable ID.
- Evidence created after `as_of_at` is excluded.
- Fixed-horizon candidate outcomes never mix with variable-horizon Position
  economics.
- Ordered OHLCV paths require exact instrument identity, closed one-minute bars,
  valid OHLC invariants, an exact timestamp set, and complete coverage. Missing
  or malformed coverage is unavailable, not zero.
- MFE and MAE are bounded at zero; same-bar Stop/TP order remains ambiguous and
  uses pessimistic bounds. Holding/Exit path classification uses gross realized
  price return; net return remains a separate accounting field so fees cannot
  become apparent giveback. If gross return is unavailable, the giveback
  diagnosis is unavailable rather than falling back to net return.
- Gross-to-net reconciliation deducts entry fee, exit fee, funding, and
  slippage exactly once. A missing or out-of-tolerance residual makes Position
  economics unavailable.
- Hypothesis state reads only the completed prefix supplied by its caller. A
  malformed hypothesis or candidate/Position fact is unavailable, not a root
  cause.
  Missing machine-evaluable inputs return `not_evaluable`.
- The counterfactual registry is finite and locked. Delayed entry uses the next
  bar open; thesis invalidation during the delay produces `no_trade`.
- A counterfactual resolves exactly one immutable profit-loop evidence anchor
  matching the complete opening/closing producer commit, behavior fingerprint,
  and current registry hash. Missing, mixed, conflicting, or duplicate identity
  fails closed; current HEAD, parent commits, raw Position rows, and fingerprint-
  only matches are never fallbacks.
- `learning_policy_epochs.started_at` is the immutable registration time and the
  only cutoff is `started_at + 30 days`. A restart with the same triple reuses
  the epoch. Any commit, fingerprint, or registry change ends the old epoch and
  starts a new 30-day window from zero. An epoch ended before its cutoff cannot
  be evaluated as a shortened cohort.
- The hard-coded registration opened on 2026-08-13 cannot accept the producer
  identity of the build that actually ran it. It remains immutable historical
  evidence with zero valid samples and is neither deleted nor reinterpreted.
- Counterfactual output requires a cost model with a model ID, verification
  timestamp, source-evidence SHA-256, fee, funding, and slippage assumptions.
- Same-sample counterfactuals are permanently `exploratory` and
  `promotion_eligible=false`.
- Gradient pooling requires one behavior, policy, market-data, and cost epoch,
  plus one declared comparison method. Missing or unknown methods fail closed.
  `prospective_randomized` requires two arms and the smaller-arm gate;
  `paired_same_unit` requires unique paired units without inventing arms;
  `deterministic_decomposition` and `directional_observational` are descriptive
  only and cannot produce a Winner.
- Holding evidence requires invalidated hypothesis or expired horizon and no new
  holding authorization. Exit evidence requires a valid thesis, reasonable
  continued holding, and a protection-geometry failure. Execution evidence
  requires an authorized exit decision, an execution baseline, and isolates
  fill/execution loss with fees and funding explicitly excluded.
- Costs evidence requires `avoidable=true`, a realizable baseline (`realizable_limit`,
  `spot_vs_perpetual`, `turnover`, or `venue`), a cost-model identity, and an
  explicit `cost_event_id`, verified evidence references, and
  `avoidable_extra_cost_quote` equal to the paired quote delta. `gross - net`, raw fees, funding, or a
  frictionless-only baseline are accounting observations, not Costs candidates.
- Coverage, sample size, the applicable arm rule, Risk-reject count, clustered
  interval, absolute effect, temporal stability, concentration, and cross-layer
  harm-event integrity are hard gates.
- A Winner exists only when exactly one layer clears every applicable gate and its
  harm lower bound exceeds every runner-up upper bound. Unresolved residuals
  force `no_unique_direction`.
- No analytics result authorizes an automatic behavior or parameter change.

## Failure and recovery semantics

Identity, cutoff, accounting, OHLCV, cost-model, or sample evidence failures are
returned as explicit unavailable or ineligible reasons. They do not create a
fallback fact, cache a terminal error, mutate a Position, or alter an experiment.
Rerunning against unchanged durable facts and the same cutoff produces the same
canonical fact and OHLCV hash.

Transient Position path-provider failures are deferred and retried by the
existing observer. They no longer create an append-only unavailable bucket that
blocks later recovery.

`candidate-diagnosis` and the `opportunity audit --view entry-exit` report root
both declare `read_only=true` and `behavior_changed=false`. These fields describe
the inspection surfaces only; their economics, eligibility, gates, and rows are
unchanged.

No protected OHLCV retention was added. The Fedora Paper evidence checked during
implementation contained 327 closed Positions, a maximum hold of 1,705.28
minutes, and only 1,414 retained ADA perpetual one-minute rows—well below the
50,000-row pruning cap. The missing older ADA window therefore does not prove
rolling-prune loss, overwrite, or an on-demand backfill performance boundary.
A manifest remains prohibited until one of those observable triggers occurs.

## Executable evidence

- `tests/test_position_replay.py`
- `tests/test_candidate_facts.py`
- `tests/test_proposal_hypothesis.py`
- `tests/test_profit_loop_diagnosis.py`
- `tests/test_profit_loop_counterfactual.py`
- `tests/test_profit_gradient.py`
- `tests/test_profit_loop_cli.py`
- `tests/test_position_path_observer.py`
- `tests/test_stop_loss_path_attribution.py`

## Known gaps

The first post-repair deployment evidence cohort must accumulate before any
empirical winner or retention trigger can be claimed. Its cutoff is established
only after deploying the exact merged commit and recording the new durable epoch;
the historical 2026-09-12 cutoff is not reused. GitHub issue #964 is the parent
scope and #974 records the producer-registration repair. This remains exploratory
with no promotion authority and does not modify or reset UFC.
