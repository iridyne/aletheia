---
id: position-replay-profit-gradient
status: accepted
github_issue: 964
related_contracts: []
supersedes: []
---

# Position replay and profit-gradient evidence

## Summary

Aletheia needs reproducible evidence for why a Position was selected, approved,
filled, held, and closed, and for which one of Selection, Decision, Risk,
Execution, Holding, Exit, or Costs most harms net expectancy. This proposal adds
read-only evidence contracts; it grants no authority to change trading behavior.

## Current fact

Closed Position analytics already expose attribution, path, geometry, outcome
integrity, and Risk/Execution contribution evidence. They do not provide one
exact-identity replay, a complete non-traded candidate denominator, a frozen
machine-evaluable opening hypothesis, or a common winner gate. Existing
`position_path_observations` also has five blockers: missed observer cycles do
not register expected buckets, transient failures can remain pending without a
terminal bound, Stop Loss aggregation bypasses reportable-outcome eligibility,
MFE can be negative, and cost-complete semantics do not match the shared
accounting contract.

## Goals and non-goals

Goals:

- separate `candidate_fact` keyed by `outcome_candidate_key` from
  `position_fact` keyed by `position_id`;
- replay exact IDs and event times through discovery, decision, execution,
  holding, and exit;
- freeze `position_hypothesis/v1` before Proposal persistence;
- compute bounded prefix-only diagnostics and exploratory counterfactuals;
- select a maximum profit gradient only when fixed evidence gates identify one.

Non-goals:

- changing Ranker, System Operator, Risk, Execution, protection, Learning, Live,
  prompts, thresholds, stops, targets, sizing, or exchange behavior;
- resetting or pooling the current UFC cohort;
- adding a replay service, scheduler, dashboard, general indicator store,
  parameter search, or unconditional OHLCV cold storage.

## Proposed design

### Contracts

`profit_loop_fact/v1` contains immutable facts only: unit and exact entity IDs,
source table, event/evidence/cutoff times, producer and behavior identity,
policy/cost/market-data identity, availability, and raw economics. The Position
CLI is `aletheia inspect position-replay --id POSITION_ID [--json]`. Exact
linkage starts from `pi_decision.proposal_id`; it never substitutes a latest
Decision across retries.

`profit_loop_diagnosis/v1` consumes frozen facts and reports measured,
directional, insufficient, or unavailable evidence with denominators,
coverage, confidence intervals, concentration, epochs, and limitations. It is
stored and tested independently from facts.

`profit_loop_counterfactual/v1` permits only registered delayed entry at 1, 3,
or 5 completed one-minute bars, one versioned alternative protection policy,
one fixed horizon, and frozen-thesis invalidation exit. Evaluation reads only
the prefix available at each bar; entry uses the next available bar open.
Same-sample rules remain exploratory and cannot be promoted.

### Economics and path evidence

The accounting identity is:

```text
net_pnl = gross_pnl - entry_fee - exit_fee - funding_cost
```

Reference-to-fill price impact already affects gross PnL and is not deducted
again as Costs. Residuals beyond the shared outcome-integrity tolerance make
economic attribution unavailable.

Replay queries exact one-minute `market_ohlcv` identity and window, fills only
missing ranges, accepts only proven closed continuous bars, requires complete
coverage, and publishes a deterministic canonical window hash. MFE is at least
zero. Same-bar Stop/TP contact remains an interval or ambiguous result.

### Frozen hypothesis

Before Proposal persistence, `trade_proposals.attribution_snapshot` receives
`position_hypothesis/v1`: thesis, direction, horizon, supporting signals,
machine-evaluable invalidation conditions, initial risk, stop/target,
market identity, decision/build identity, and contract version. Historical
Positions without it remain `not_recorded`; no retrospective reconstruction is
allowed.

### Winner gate

All seven layers are converted to quote-currency harm, basis points, per-100
candidate contribution, and per-capital-day contribution. A unique direction
requires the same behavior/policy/market-data/cost epoch, candidate coverage at
least 80%, causal Position linkage at least 95%, verified settled economics at
least 95%, ordered path coverage at least 80%, at least 30 independent units,
comparison arms at least 20, at least 20 Risk rejects where relevant, clustered
analysis by batch/day, at least 5 bp practical effect, a 95% clustered-bootstrap
interval excluding zero, matching signs in chronological halves, and Top-1 /
Top-5 absolute contribution concentration no greater than 25% / 50%.

Exactly one layer must pass every gate and its harm lower bound must exceed the
runner-up upper bound. Otherwise the result is `no_unique_direction`; the only
permitted next action is filling the largest evidence gap.

### Retention

The first implementation adds no manifest or scheduler. Protected retention is
allowed only after evidence that a new Position loses its replay window within
the fixed analysis delay, a closed-bar hash changes, governance needs exceed the
rolling window without a production cold backend, or on-demand backfill exceeds
provider limits. Any later manifest protects deduplicated `market_ohlcv` ranges
and does not copy per-Position candles.

## Trust and complexity review

The change crosses audit, state, market-data, and analysis boundaries but remains
read-only except for freezing evidence already present at Proposal creation.
Candidate and Position units stay separate to prevent survivorship bias.
Facts, diagnoses, and counterfactuals stay separate to prevent conclusions from
rewriting evidence. Missing identity, coverage, accounting, or closed-bar proof
fails closed. Reports have no trading owner imports or calls.

A smaller Position-only report cannot assess Selection, Decision, or Risk.
Reusing the existing attribution, trade path, geometry, outcome-integrity, and
contribution modules avoids a second analytics framework.

## Verification contract

Focused regressions must prove exact identity and retry isolation, evidence
cutoff and anti-lookahead, one-minute completeness and deterministic hashing,
accounting residuals and cost non-duplication, candidate/Position denominator
separation, same-bar ambiguity, epoch and concentration gates, and no trading
behavior mutation. macOS runs only focused tests. PR CI on a repository
self-hosted runner is the regression gate, but its configured tiers exclude
`tests/simulation/`; a complete-suite claim therefore also requires an
isolated, credential-free Fedora CI/Test run of the omitted simulation modules
or an equivalent PR job. Windows is not a validation fallback.

## Rollout and rollback

Implement in ordered slices tracked by issue #964. New evidence cohorts begin
only after the frozen hypothesis contract exists; historical evidence is not
mixed. Any Observation window starts only after the exact merged commit is
deployed to Fedora Paper and runtime commit, schema, provider, Gateway, and
settlement health are verified. Rollback disables the read-only producer/CLI
and stops new hypothesis fields; existing immutable facts remain auditable.
No unrelated UFC cohort is reset.

## Resolution

Accepted by the Owner on 2026-08-13. Implementation is tracked in GitHub issue
#964. Permanent normative contracts will replace this proposal only after their
executable verification passes.
