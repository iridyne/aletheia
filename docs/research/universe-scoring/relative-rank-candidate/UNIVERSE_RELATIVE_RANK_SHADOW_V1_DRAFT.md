# `universe-relative-rank-shadow-v1` Design Draft

> Status: owner-approved; implemented as Shadow-only candidate
>
> Classification: B — new observation window if approved
>
> Authority: Shadow only; `universe-step-v1` remains the sole publisher

## 1. Hypothesis

Within the same complete scanner run, a pessimistic, non-duplicative,
cross-sectional ranking policy improves `4h` net allocation utility over
`universe-step-v1` without unacceptable turnover, cost, missingness, or tail
harm.

The score is an ordinal ranking value, not a probability of profit.

## 2. Candidate flow

```text
complete point-in-time scanner run
  -> hard eligibility and source-integrity gates
  -> freeze one pessimistic family and direction per candidate
  -> three non-overlapping component scores
  -> empirical percentile rank inside the run
  -> frozen weighted relative score
  -> existing fine filters / spot-perp dedupe / quotas / allocator
  -> Shadow-only selected 4h outcomes
```

The final Top-N remains discrete. The proposed continuity is in evidence and
ordering before that unavoidable capacity boundary.

## 3. Hard eligibility

A candidate is not scored unless all required facts are observed and current:

- exact symbol, venue, market type, point-in-time tradability/listing state;
- complete closed OHLCV for every required window;
- positive price and quote volume;
- spread, depth, freshness, units, provider, observed time, and available time;
- existing blacklist, stablecoin, extreme-spread, minimum-volume,
  critically-shallow-depth, no-chase, and market/family gates;
- at least 20 eligible candidates in the scanner-run cross-section.

Missing required evidence makes the record `invalid` or `not_available`; it
never receives a neutral default. Existing authority behavior is unaffected.

## 4. Freeze family and direction

Only the current continuous scorer's **pessimistic** family evidence may enter
the candidate. Point-imputed evidence is diagnostic only.

For each candidate:

1. select the highest pessimistic family score;
2. break ties by a frozen family order;
3. freeze that family and its direction for all components and replay;
4. reject a missing direction or invalid family.

Point and pessimistic paths may not choose different families for the treatment
score.

## 5. Raw-feature ownership

Each raw feature has exactly one scoring owner:

| Component | Owned evidence | Excluded duplicates |
| --- | --- | --- |
| Opportunity quality | pessimistic continuous family score plus the separately ranked perp positioning facts | no volume, spread, depth, freshness, or allocator penalties |
| Executability | quote volume, spread, depth, and absolute OI for perp capacity | no RSI, return, funding direction, or freshness |
| Data robustness | freshness and explicit observed/missing state | no alpha or liquidity evidence |

If one raw feature appears in more than one component, the candidate record is
invalid. This is simpler and more interpretable than trying to correct duplicate
weights after scoring.

Perp candidates additionally require funding rate, OI, OI delta 1h, Binance
global long/short account ratio, and Binance top-trader long/short position
ratio. Missing any one makes that perp record unavailable. The two ratios are
kept separate; the position ratio is public top-trader market data, not the
system's private account position.

## 6. Cross-sectional transform

For every complete scanner run and component:

```text
rank_i = 100 * (average_rank_i - 1) / (N - 1)
```

- ranks use only hard-gate-eligible candidates in the same run;
- ascending quality maps to `0`, best maps to `100`;
- ties receive their average rank;
- the exact denominator `N`, frozen inputs, component values, and ranks are
  stored so ties are reconstructible;
- no full-sample or future distribution is used;
- `N < 20` makes the run unavailable, not a broader adaptive fallback.

Empirical ranks are preferred over a z-score because they are bounded and do
not let one extreme coin set the scale of the entire run.

## 7. Frozen draft formula

The initial review candidate is intentionally simple:

```text
relative_score =
    0.50 * opportunity_quality_rank
  + 0.30 * executability_rank
  + 0.20 * data_robustness_rank
```

These are design priors, not fitted optimal weights. They must be accepted or
changed before the first replay. Once evidence is observed, the weights cannot
be tuned inside the same candidate identity.

For perp candidates, funding alignment, OI change, global account crowding,
and top-trader position alignment form one positioning rank inside opportunity
quality; absolute OI participates once in executability. Taker flow,
liquidation, on-chain, and news remain outside V1.

## 8. Allocation and churn

The candidate reuses the existing allocator unchanged:

- fine tradability filters;
- spot/perp dedupe;
- family quotas;
- explore/exploit behavior;
- `max_candidates`.

V1 does not add a new dwell or hysteresis rule. The Shadow report first measures
selection overlap, entry/exit count, turnover, and estimated cost against
Step-v1. A later buffer is justified only if observed churn is harmful; adding
one now would combine two treatments.

## 9. Observation design

If the draft is approved, evidence collection proceeds in two separate steps:

### Step A — deterministic offline replay

- replay frozen historical inputs without tuning on their outcomes;
- prove identity, ranks, ties, missingness, feature ownership, allocator parity,
  and bounded storage;
- use results for integrity and capacity checks only, not promotion.

### Step B — new prospective Shadow epoch

- control: `universe-step-v1`;
- treatment: `universe-relative-rank-shadow-v1`;
- assignment unit: complete scanner run;
- assignment: stable 50/50 with logged propensity;
- denominator: every hard-gate-eligible candidate;
- outcome: selected-candidate canonical `4h` net return with exact cost model;
- authority: no published Universe member, Opportunity, Paper, Risk, Execution,
  or Learning mutation.

The current Continuous Twin epoch is not reset or pooled. The owner approved
the two non-authoritative windows to run concurrently with independent policy
epochs, assignment streams, cadence, storage, and outcome keys.

## 10. Primary and safety metrics

Primary estimand:

```text
mean scanner-cluster net allocation utility difference
  = treatment - control
```

Required secondary evidence:

- complete clusters and effective sample size by arm;
- settled selected-candidate coverage;
- selection overlap and turnover;
- fees, spread/slippage, funding, and total net cost;
- cluster loss rate and zero-selection rate;
- net-return CVaR5 and selected-candidate absolute MAE p95;
- missingness and evidence-concentration rates;
- material family, direction, market-type, and liquidity cohorts;
- every exclusion and identity conflict.

No candidate-level IID inference is allowed; scanner runs are the clusters.

## 11. Minimum evidence gate

This draft inherits the existing conservative minimums rather than inventing a
faster promotion path:

- at least 50 complete clusters per arm;
- at least seven consecutive eligible UTC dates;
- propensity ESS at least 50 per arm and positive support;
- complete feature lineage and a frozen availability profile;
- preregistered non-inferiority margins and multiplicity allocation;
- no unresolved source, identity, cost, overlap, coverage, or harm blocker.

Passing a Shadow gate means the candidate deserves a separately reviewed Paper
proposal. It does not switch authority.

## 12. Stop and invalidation

Keep Step-v1 and mark the candidate insufficient, invalid, or negative when:

- required source coverage is absent or drifts beyond the frozen rule;
- any raw feature is scored twice;
- family/direction, policy, source, horizon, outcome, or cost identity changes;
- selected outcomes are incomplete or economically conflicting;
- turnover/cost or a preregistered harm margin fails;
- an overlapping behavior-changing treatment reaches the same population;
- the owner closes the comparison.

Historical evidence remains immutable and is never rewritten into a winner.

## 13. Owner decisions frozen for V1

1. weights `50/30/20` accepted;
2. observed spread/depth required before scoring;
3. perp positioning inputs activated with the semantics in §5;
4. Continuous Twin and Relative Rank may collect concurrently but never pool;
5. actual dynamic eligible `N` is used, with `N >= 20` as the minimum;
6. `50 clusters/arm` and seven consecutive eligible UTC dates remain the
   minimum evidence gate.

These decisions authorize Shadow observation only. Step-v1 remains the sole
Universe publisher.
