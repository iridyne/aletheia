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
  `profit_loop_diagnosis/v1` artifacts without changing their inputs;
- `profit-counterfactual --cost-model <json>` evaluates the fixed
  `profit_loop_counterfactual/v1` registry;
- `profit-gradient --evidence <json>` applies `profit_gradient/v1` gates to a
  frozen evidence artifact.

All commands are synchronous, read-only inspection commands. The cost-model and
gradient evidence files are inputs only.

## Durable state and consumers

No replay, diagnosis, counterfactual, gradient, manifest, or protected-retention
table exists.

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
- Execution, Holding, Exit, and Costs use exact `position_id` and verified
  settled net economics.

Position replay reports exact causal IDs, evidence cutoff, economics residual,
runtime/behavior identity, ordered-path coverage, canonical OHLCV window hash,
and explicit unavailable reasons. Candidate replay preserves filtered,
skipped, rejected, unfilled, and traded terminal states.

The gradient report always emits
`Selection / Decision / Risk / Execution / Holding / Exit / Costs`, quote harm,
basis points, per-100-candidate harm, per-capital-day harm, clustered interval,
concentration, exclusions, and gate reasons. Its verdict is either
`unique_max_direction` or `no_unique_direction`.

## Invariants and trust boundaries

- Replay never selects a latest related record as a fallback. Every causal join
  uses an exact durable ID.
- Evidence created after `as_of_at` is excluded.
- Fixed-horizon candidate outcomes never mix with variable-horizon Position
  economics.
- Ordered OHLCV paths require exact instrument identity, closed one-minute bars,
  and complete coverage. Missing coverage is unavailable, not zero.
- MFE and MAE are bounded at zero; same-bar Stop/TP order remains ambiguous and
  uses pessimistic bounds.
- Gross-to-net reconciliation deducts entry fee, exit fee, funding, and
  slippage exactly once.
- Hypothesis state reads only the completed prefix supplied by its caller.
  Missing machine-evaluable inputs return `not_evaluable`.
- The counterfactual registry is finite and locked. Delayed entry uses the next
  bar open; thesis invalidation during the delay produces `no_trade`.
- Counterfactual output requires a cost model with a model ID, verification
  timestamp, source-evidence SHA-256, fee, funding, and slippage assumptions.
- Same-sample counterfactuals are permanently `exploratory` and
  `promotion_eligible=false`.
- Gradient pooling requires one behavior, policy, market-data, and cost epoch.
  Coverage, sample size, arm size, Risk-reject count, clustered interval,
  absolute effect, temporal stability, and Top-1/Top-5 concentration are hard
  gates.
- A winner exists only when exactly one layer clears every gate and its harm
  lower bound exceeds every runner-up upper bound.
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

The first post-deployment evidence cohort must accumulate before any empirical
winner or retention trigger can be claimed. GitHub issue #964 remains the
implementation and deployment record.
