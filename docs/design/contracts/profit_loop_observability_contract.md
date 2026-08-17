# Profit-loop observability contract

## Purpose and current scope

Aletheia exposes read-only, versioned evidence for replaying one Position,
rendering its deterministic lifecycle story, reconstructing the complete candidate
denominator, evaluating a fixed exploratory counterfactual registry, and applying
seven-layer unique-winner gates.

The current Position counterfactual is deliberately an early-path experiment. Its
formal output always declares `counterfactual_scope = first_60_minutes` and
`counterfactual_horizon = 60_minutes_after_each_hypothetical_entry`: every arm
uses 60 one-minute bars after that arm's hypothetical entry to compare the
preregistered initial-Stop baseline with the five bounded candidate treatments.
A delayed arm also reads its delay prefix; therefore `delay_5_bars/v1` can use
the source open at minute 65 (66 source rows in total). The scope is not a
strict shared 60-row input window, does not describe a Position's full Holding /
Exit lifecycle, and must not be read as a rule that Positions should be closed
after one hour.

This capability has no trading authority. It does not change Ranker, System
Operator, Risk, Execution, position protection, Learning, Live, or an existing
experiment cohort.

## Entry points

The production entry point is `aletheia inspect`:

- `position-replay --id <position_id> --as-of <timestamp> --json` emits one
  `profit_loop_fact/v1` Position fact;
- `position-story --id <position_id> --as-of <timestamp> [--json]` renders one
  deterministic `position_lifecycle_story/v1` explanation. Its
  `structured_events` projection is machine-facing; human text is not a
  statistical input;

The `position_lifecycle_story/v1` machine-facing shape includes the
cutoff-visible `periodic_observations` projection and corresponding
`position_path_observed` entries in `structured_events`. Their `occurred_at`
is the durable observation time (`observed_at`), not the bucket opening time,
and their facts preserve `producer_commit` and `runtime_session_id` so a
consumer can reject evidence mixed across producer builds or Runtime Sessions.
These are additive v1 fields; an incompatible change to their meaning or
required shape requires a new Story contract version.
- `candidate-facts --horizon <15m|1h|4h|24h> --as-of <timestamp> --json` emits
  the complete `candidate_fact` denominator at one fixed horizon;
- `position-diagnosis` and `candidate-diagnosis` emit
  `profit_loop_diagnosis/v1` artifacts without changing their inputs;
- `profit-counterfactual --cost-model <json>` evaluates the fixed
  `profit_loop_counterfactual/v1` registry against one durable evidence epoch;
- `profit-gradient --evidence <json>` applies `profit_gradient/v3` gates to a
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
The lifecycle boundary is:

```text
Position Path Facts → Position Replay → Position Lifecycle Story
                                      → human review / dashboard / post-trade review
                                      → evidence compiler → statistical attribution
```

The Story exposes a deterministic readable view plus `structured_events`. The
future evidence compiler and gradient consume structured event facts, never
rendered prose or an LLM-generated narrative. No Story or replay artifact is
persisted by this contract.

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
and explicit unavailable reasons. Its `profit_loop_fact/v1` stages and exact
`holding.trade_path` / `exit` evidence are the Position Lifecycle Story: they
describe the actual `opened_at` → `closed_at` path, independently of the
first-60-minute counterfactual. Candidate replay preserves filtered, skipped,
rejected, unfilled, and traded terminal states.

The lifecycle Story reports the structured event vocabulary needed for
post-trade review: first meaningful favorable excursion, maximum favorable
excursion, hypothesis weakening and invalidation, expected horizon, management
decisions, ExitPlan revisions, protection activation, exit triggers, close fills,
and verified costs. It may also render opening thesis and review context for a
human; those narrative fields are not evidence-compiler inputs.

The gradient report always emits
`Selection / Decision / Risk / Execution / Holding / Exit / Costs`, quote harm,
basis points, per-100-candidate harm, per-capital-day harm, clustered interval,
concentration, exclusions, and gate reasons. Its verdict is either
`unique_max_direction` or `no_unique_direction`.

### Profit-gradient v3 comparison method

The CLI accepts an outer compiler artifact with contract
`profit_gradient_evidence/v1`; after source-bound verification, the selector
emits a `profit_gradient/v3` report with one `evidence_context.comparison_method`:

```json
{
  "contract_version": "profit_gradient_evidence/v1",
  "evidence_context": {
    "comparison_method":
    "prospective_randomized|paired_same_unit|deterministic_decomposition|directional_observational"
  }
}
```

The CLI rejects a missing or older compiler wrapper before evaluating samples;
`profit_gradient/v3` identifies the selector result, not the input file.

Version 3 also records the randomized estimand and cohort identity. Version 2 and
older results are historical and are not pooled with version 3.

A missing or unknown method is ineligible; v1 and v2 evidence are historical and are not
implicitly assigned a method, relabeled, pooled, or compared with version 3 evidence.
One artifact does not mix methods implicitly. A caller evaluating a different
method creates a separately identified evidence artifact.

All methods retain the existing coverage, epoch, unit-integrity, clustered
interval, practical-effect, temporal-stability, concentration, and Risk-layer
hard gates. The method-specific gates are:

| comparison method | additional method gate | unique winner |
| --- | --- | --- |
| `prospective_randomized` | at least 30 independent clusters, exact `control`/`treatment` arms with each cluster assigned to one arm, at least 20 clusters in the smaller arm, singular assignment/horizon/outcome/metric/statistical-unit/stage identities, and a positive quote contrast aligned with the basis-point direction | allowed |
| `paired_same_unit` | at least 30 independent resampling clusters and 30 unique units, with singular horizon/outcome/metric/statistical-unit/stage identities; no arm threshold or arm labels are required | allowed for selecting the next forward question, never automatic behavior change |
| `deterministic_decomposition` | at least 30 independent resampling clusters and 30 unique units, with singular horizon/outcome/metric/statistical-unit/stage identities; facts remain descriptive | prohibited; measured fact only |
| `directional_observational` | at least 30 independent resampling clusters and 30 unique units, with singular horizon/outcome/metric/statistical-unit/stage identities; association remains directional | prohibited; rank the next question only |

For `prospective_randomized`, the estimand is the equal-weight per-cluster
`treatment - control` outcome contrast; arm labels affect the estimate and the
cluster bootstrap. Quote harm converts the exposure-weighted treatment and control
means to the treatment-exposure cohort total before denominator normalization,
weighting each arm cluster's quote contribution by its own exposure count; both
the contrast and this total must be positive. Concentration uses
translation-invariant, centered per-cluster arm-effect contributions, not pooled
row sums. A pooled mean is not a randomized effect. For
`paired_same_unit`, the estimand is the unit-weighted mean across normalized
rows with a cluster bootstrap for dependence; each row contributes one complete, cost-aware
delta for one unique statistical unit (for the current Exit counterfactual,
one Position). Duplicate `unit_id` rows are excluded. The paired result is
temporal-stability-ineligible when its chronological midpoint is a timestamp tie
and is never made to satisfy the randomized arm gate by inventing `treatment` or
`control` labels. The report exposes the applied threshold profile, arm counts,
unassigned-arm counts for randomized evidence, and
`smaller_arm_count: null` when arms do not apply.

The current delayed-entry, breakeven, and hypothesis-invalidation registry is
an exploratory paired counterfactual and remains `promotion_eligible=false`.
If its deltas are adapted into gradient evidence, the adapter must declare
`paired_same_unit` and preserve the frozen Position, cost, epoch, cluster,
time, and concentration identities.

### Ownership and reconciliation (`profit_gradient/reconciliation/v1`)

Every admitted sample carries a machine-checkable component, ownership stage,
stage window, positive quote notional, harm/attribution identities, evidence
references, and a reconciliation declaration. `exclusive` declarations contain
one component and reconcile its quote delta exactly to the declared total.
`residual` declarations may contain several components, including components in
different layers, but all components must share one unit scope and sum exactly
to the declared total and its basis-point equivalent within
`1e-8` quote units. A shared harm or attribution event across layers is rejected
unless this residual contract proves that decomposition; a shared cost event is
always rejected. Missing or malformed ownership, alternative-cost, execution
isolation, or reconciliation evidence is unavailable/ineligible and never
converted to zero.

Execution uses gross fill/routing deviation against a compatible execution
alternative; fees and funding are explicitly excluded. Costs are admitted only
when a persisted, realizable alternative identifies an avoidable extra cost and
its cost event/model identity. A production gross-minus-net ledger by itself is
not an actionable Costs comparison, so it remains unavailable rather than being
counted beside Execution.

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
  slippage exactly once in settled economics; attribution layers must then use
  either one exclusive component or an explicit residual decomposition, never
  the same fee/cost delta twice.
- Hypothesis state reads only the completed prefix supplied by its caller.
  Missing machine-evaluable inputs return `not_evaluable`.
- The counterfactual registry is finite and locked. Its formal scope is
  `first_60_minutes` with a per-arm horizon of
  `60_minutes_after_each_hypothetical_entry`; delayed entry uses the next bar
  open, and thesis invalidation during the delay produces `no_trade`.
- `counterfactual_scope = first_60_minutes` is an evidence boundary, not an
  exit recommendation. A better early-path treatment cannot establish that all
  Positions should exit within one hour.
- Position Lifecycle Story evidence and first-60-minute counterfactual results
  are separate populations and must not be pooled or used as substitutes for
  one another.
- If complete lifecycle evidence repeatedly identifies a later holding-period
  problem such as horizon overrun or post-entry giveback, register a new
  horizon-aligned Holding / Exit experiment with its own registry identity and
  evidence window. Do not edit or extend this preregistered 60-minute contract.
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
- Gradient pooling requires one behavior, policy, market-data, and cost epoch.
  Coverage, sample size, arm size, Risk-reject count, clustered interval,
  absolute effect, temporal stability, and Top-1/Top-5 concentration are hard
  gates.
- A winner exists only when exactly one layer clears every gate and its harm
  lower bound exceeds every runner-up upper bound.
- Lifecycle Story rendering is deterministic and has no LLM/model call.
- `structured_events` is the only Story projection suitable for a future
  evidence compiler; rendered/free-text story content cannot enter Learning or
  statistical attribution.
- No analytics result authorizes an automatic behavior or parameter change.

## Failure and recovery semantics

Identity, cutoff, accounting, OHLCV, cost-model, or sample evidence failures are
returned as explicit unavailable or ineligible reasons. They do not create a
fallback fact, cache a terminal error, mutate a Position, or alter an experiment.
Rerunning against unchanged durable facts and the same cutoff produces the same
canonical fact and OHLCV hash.

Position Path Observer uses a recoverable expected-bucket contract. For every
open or eligible closed Position it derives the first complete post-entry 5m
bucket through the latest completed bucket (capped at `closed_at` for a closed
Position), registers only the oldest not-yet-materialized bucket per bounded
pass, and preserves each registration durably before provider I/O. A healthy
observer cycle makes at most two bounded oldest-first passes per
Position, so recovery converges without a backfill storm; exact
venue/symbol/spot-perp identity and provider `is_closed` proof remain mandatory.
Recovered buckets do not reuse current mutable funding/protection state: funding
is unavailable, and the effective durable ExitPlan is used when present; otherwise
`exit_plan_version=0` plus nullable levels explicitly signal unavailable plan
history.

Transient Position path-provider failures stay in the durable deferred queue for
bounded retries. Once the retry limit is reached, the bucket is materialized as
an append-only `unavailable` fact and removed from the queue; it cannot remain
pending forever. `aletheia control status` exposes the oldest missing bucket,
durable deferred count, observer lag, and trailing 24h available/unavailable/
skipped counts.

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
- `tests/test_position_lifecycle_story.py`
- `tests/test_stop_loss_path_attribution.py`

## Known gaps

The first post-repair deployment evidence cohort must accumulate before any
empirical winner or retention trigger can be claimed. Its cutoff is established
only after deploying the exact merged commit and recording the new durable epoch;
the historical 2026-09-12 cutoff is not reused. GitHub issue #964 is the parent
scope and #974 records the producer-registration repair. This remains exploratory
with no promotion authority and does not modify or reset UFC.
