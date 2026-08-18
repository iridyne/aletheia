# Universe Relative Rank Shadow Contract

Status: current for `universe-relative-rank-shadow-v1`.

## Authority boundary

`universe-step-v2` remains the only authority that publishes Universe members
and Opportunities. Relative Rank is observation-only: it cannot mutate Paper,
Risk, Execution, Learning, allocator configuration, or production scoring.
`promotion_eligible` is always false.

The existing Continuous Twin and Relative Rank experiments use independent
policy epochs, assignment hashes, cadence, tables, and namespaced outcome keys.
Their evidence must not be pooled.

## Input contract

The candidate consumes the same point-in-time coarse-survivor feature snapshot
as Step-v2. Every eligible record requires observed positive quote volume,
spread, depth at 10 bps, freshness, a valid pessimistic family, and direction.
Spread or depth missingness never receives a neutral score.

Perp records additionally require funding rate, open interest, one-hour OI
change, Binance global long/short account ratio, and Binance top-trader
long/short position ratio. The two ratios remain distinct public market
observations. Top-trader position ratio is not the Aletheia account's private
position or leverage. These positioning fields are masked while computing the
pessimistic family score, then enter only the positioning/executability ranks;
V1 does not count the same derivative fact twice.

## Score and assignment

Within each complete scanner run, the candidate uses the actual hard-eligible
cross-section. `N < 20` makes the run unavailable; the set is never padded or
selected after observing outcomes. Ties receive average empirical percentile
rank. The frozen score is:

```text
0.50 opportunity quality + 0.30 executability + 0.20 data robustness
```

One scanner run is one cluster and is assigned 50/50 by a deterministic hash:

- control: Step-v2 selection;
- treatment: Relative Rank selection through the unchanged fine filters,
  spot/perp dedupe, quotas, explore/exploit, and `max_candidates` allocator.

## Outcome and interpretation

Only assigned-policy selected candidates receive namespaced canonical `4h`
cost-aware outcomes. Cluster utility is selected net return summed over the
complete hard-eligible denominator, so every unselected candidate contributes
zero and a zero-selection cluster contributes zero. Candidates inside a scanner
run are correlated and are not IID samples.

The report remains `insufficient_data` until each arm has at least 50 complete
clusters and the epoch has seven consecutive eligible UTC dates. Reaching those
counts only makes the Shadow direction interpretable. A future authority change
still requires a separately reviewed Paper contract and explicit owner approval.

Research history and rationale live in
[`../research/universe-scoring/relative-rank-candidate/`](../research/universe-scoring/relative-rank-candidate/).
