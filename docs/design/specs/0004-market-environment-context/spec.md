---
id: 0004-market-environment-context
title: Market environment context for the Entry Planner
status: draft
depth: D1
prefix: ME
issue: 3930
contracts:
  - ../../contracts/entry-plan-contract.md
  - ../../data/data_utilization.md
supersedes: []
superseded_by: []
---

# 0004 — Market environment context for the Entry Planner

## Summary

A discretionary trader checks the whole market before an entry: is the market falling,
is sentiment greedy or fearful, is this coin leading or lagging, is funding crowded
across venues. Aletheia already collects all of that every few minutes, and nothing in
the decision path reads it. This spec adds one bounded, read-only **market environment**
section to the Entry Planner input, built from data already stored. The AI may use it in
its qualitative judgement; it cannot block, size or override anything. Whether it helps is
measured by randomisation, with an automatic off switch on harm.

## Problem and evidence

- The Entry Planner's model-visible input is only the Candidate feature fields, the exact
  order book, 15m/1h/4h structure windows and `entry-learning-context/v1`
  (`trading/entry_analysis.py::EntryAnalysisContext`). It has no field for market-wide
  state.
- The data is collected and fresh. In the Fedora runtime over 24h on 2026-10-10
  (read-only query): `macro_snapshots` 168 rows (BTC dominance, total market cap 24h
  change, VIX, DXY, fear/greed, `risk_regime`); `coingecko_top_snapshots` 8112 rows (top
  coins 1h/24h/7d change); `funding_rate_snapshots` 14583 rows across bitget, okx,
  binance, hyperliquid, hyperliquid_predicted; `open_interest_snapshots` 6019 rows across
  okx, binance, hyperliquid; `onchain_snapshots` 501 rows. `liquidation_events` and
  `perp_structure_snapshots` have 0 rows.
- The previous consumers are retired: `data/context_builder.py::render_enriched_context_md`
  and `trading/market_context.py::build_market_context` have no production caller
  ([data_utilization.md](../../data/data_utilization.md)).
- The environment is informative. Over 81,734 settled 4h candidate outcomes (36 days,
  `filter_candidate_outcomes`, pooled across code versions) joined to the latest macro row
  at entry: shorts averaged −0.03% net when the total market cap was flat over 24h versus
  −0.19% when it fell more than 2%; longs averaged −0.43% net in `neutral` regime versus
  −0.31% in `risk_off`. The spread between cells (~0.4 pp) exceeds the round-trip cost
  (~0.24%). These are pooled, unadjusted observations: evidence that the context carries
  information, not a rule.

## Goals and non-goals

Goals:

1. The Entry Planner sees the same market-wide facts a human trader would check, with
   their age, and says whether they shaped the decision.
2. Collected data has a decision consumer, or is declared observation-only with a reason.
3. The effect on decision quality is measured against a randomised control; harm switches
   the section off.

Non-goals:

- No deterministic rule derived from these facts (no "do not long when the market falls"):
  that is factor mining, which the owner excludes.
- No change to sizing, leverage, stops, Risk checks, Live or credentials.
- No new data source, API key or collector. Liquidation and perp-structure data stay
  empty until the owner configures them.
- On-chain (DefiLlama) daily aggregates stay observation-only: they move on a daily scale
  and have no stated link to a 4h entry.

## Glossary

| Term | Definition |
|---|---|
| Environment section | The `market_environment` field of `EntryAnalysisContext`, frozen into the Entry Plan input hash |
| Fact | One named value with its source table, `observed_at` and the `data/freshness.py::FreshnessState` verdict (`fresh` / `missing` / `invalid` / `future` / `stale`) |
| Arm | `environment_on` or `environment_off` assignment of a planning thread |
| Breadth | The share of the top coins that rose over 1h and 24h, and this instrument's rank within them |

## Requirements

### ME-R01 One environment section

`When the Entry Planner builds an EntryAnalysisContext, the system shall include a market_environment section containing market regime, BTC dominance, total market cap 24h change, fear/greed value and class, market breadth, and this instrument's cross-venue funding and open-interest comparison, each with its source table, observed_at and freshness state.`

#### Scenarios

- WHEN every source row is fresh THEN all facts are `fresh` with their `observed_at`.
- WHEN the section is built twice from the same stored rows and cutoff THEN it is byte-identical.

### ME-R02 Causal and bounded

`The section shall use only rows with observed_at at or before the analysis cutoff, and shall fit within a fixed size bound.`

#### Scenarios

- WHEN a newer macro row exists after the cutoff THEN it is not used.
- WHEN the top-coin list is long THEN breadth is summarised; no per-coin list beyond the instrument's own rank enters the prompt.

### ME-R03 Unavailable is never zero

`If data/freshness.py::assess_freshness does not return usable for a source row under its existing domain bound, then the fact shall carry that state and no numeric value, and the plan shall still proceed.`

#### Scenarios

- WHEN the newest `macro_snapshots` row is older than the `macro` bound (30 min) THEN dominance, market-cap change and fear/greed are `stale` with no number.
- WHEN only one venue reports funding for the instrument THEN the cross-venue comparison is `unavailable: single_venue`.

### ME-R04 Advisory only

`The environment section shall not block a candidate, alter risk_intent, capture_priority, leverage, size, stop or target, or bypass any Risk check.`

#### Scenarios

- WHEN the regime is `risk_off` and the AI still plans a long THEN the plan proceeds through the normal gates unchanged.

### ME-R05 Cited when used

`The Entry Planner shall emit environment_considered (a list of {fact, effect, why}, empty when unused) on every EntryHypothesis in both arms.`

#### Scenarios

- WHEN the arm is `environment_off` THEN `environment_considered` is `[]`.
- WHEN the AI cites a fact whose state is not `fresh` THEN the hypothesis fails validation as a format failure.

### ME-R06 Randomised measurement

`The system shall assign each planning thread to environment_on or environment_off by a stable hash, record the section for both arms, show it only in environment_on, and report decision quality per arm with the spec 0001 process metrics.`

#### Scenarios

- WHEN a planning thread is re-planned THEN it keeps its arm.
- WHEN the arm is `environment_off` THEN the section is computed and stored but absent from the prompt.

### ME-R07 Harm stop

`If the anytime-valid harm test on judgment R among planned units crosses its threshold, then the system shall switch the section off for all threads and record why.`

#### Scenarios

- WHEN the harm test fires THEN new plans have no environment section and the reason is visible in doctor.

### ME-R08 Dead renderers removed

`When the environment provider is in place, the system shall delete render_enriched_context_md and trading/market_context.py::build_market_context, and data_utilization.md shall name the provider as the consumer of every fact it reads.`

#### Scenarios

- WHEN data_utilization.md is checked THEN macro, fear/greed, top movers, cross-venue funding and OI rows name `market_environment` as consumer.

## Contract Delta

### ADDED

- `entry-plan-contract.md`: `EntryAnalysisContext.market_environment`, its causal and freshness rules, and `EntryHypothesis.environment_considered` (ME-R01–R05).
- `data_utilization.md`: `market_environment` provider as consumer of macro, breadth and cross-venue facts (ME-R08).

### MODIFIED

- `entry-plan-contract.md` model-visible fields: the prompt fingerprint includes the section and arm (ME-R01, R06).

### REMOVED

- `render_enriched_context_md` and `build_market_context` LLM renderers (ME-R08).

## Clarify

| Question | Answer |
|---|---|
| Error path | A missing or failing source makes that fact `unavailable` with a reason; the plan proceeds (ME-R03) |
| Empty state | All sources empty → every fact unavailable; the section is present, the AI is told so |
| Boundaries | Existing `DOMAIN_MAX_AGE` bounds only (macro 30 min, regime 90 min, funding/OI 15 min, cross-venue 30 min); breadth uses the `macro` bound; fixed size bound for the section |
| Concurrency | Read-only over stored rows inside the existing planning claim; no new writer |
| Idempotency | Same rows + same cutoff → identical section and hash (ME-R01, R02) |
| Authority | Advisory only (ME-R04); no Risk, sizing, Live or credential change |
| Observability | Section frozen in the Plan input; `environment_considered` per decision; arm and harm state in doctor |

## Open questions

- [NEEDS CLARIFICATION: share one randomisation with spec 0001's Playbook (factorial: playbook × environment) or run this experiment separately? Recommended: factorial, so neither invalidates the other.]
- [NEEDS CLARIFICATION: configure a Coinalyze key for liquidations, or keep liquidation data out of scope?]

## Decision log

- 2026-10-10 — Owner: unused collected data should be put to use rather than dropped.
- 2026-10-10 — Owner: the AI trades like a discretionary trader; no factor rules.
