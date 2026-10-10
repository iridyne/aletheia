# 0004 — Market environment context for the Entry Planner: design

Answers [spec.md](spec.md). Every section cites the requirement ids it serves.

## Overview

```text
stored rows (macro_snapshots, market_regimes, coingecko_top_snapshots,
             funding_rate_snapshots, open_interest_snapshots)
   └─► trading/market_environment.py::build_market_environment(instrument, cutoff)   ME-R01..R03
          reads via existing repositories + data/freshness.py::assess_freshness
   └─► EntryAnalysisContext.market_environment (frozen, hashed)                      ME-R01, R02
   └─► arm(planning_identity) ── environment_on  → rendered in prompt                ME-R06
                               └ environment_off → stored, not rendered
   └─► EntryHypothesis.environment_considered                                         ME-R05
   └─► Compiler / Risk unchanged                                                     ME-R04
   └─► per-arm process metrics + harm stop (spec 0001 machinery)                     ME-R06, R07
```

No collector, table or job is added. The provider is one pure read function over rows the
system already writes.

## Data model

No new table (ME-R01). The section is a frozen JSON object inside the existing
`EntryAnalysisContext`, so it is already covered by the Entry Plan input hash and the
prompt fingerprint (`trading/entry_contract.py::entry_prompt_fingerprint`). The arm is
stored on the planning generation's existing exposure record used by spec 0001's
randomisation (ME-R06).

Section shape (each fact: `value`, `observed_at`, `source`, `state`):

| Fact | Source | Freshness domain |
|---|---|---|
| `regime` | `market_regimes` latest at cutoff | `regime` (90 min) |
| `btc_dominance`, `total_market_cap_change_24h_pct`, `fear_greed` (value + class) | `macro_snapshots` latest at cutoff | `macro` (30 min) |
| `breadth` (share of top-50 up over 1h and 24h; instrument's 24h-change rank) | `coingecko_top_snapshots` latest batch at cutoff via `latest_coingecko_top` | `macro` (30 min) |
| `funding_cross_venue` (this instrument per venue: bitget, okx, binance, hyperliquid; spread vs trading venue) | `funding_rate_snapshots` via `latest_funding_rate` | `cross_venue_funding` (30 min) |
| `oi_cross_venue` (per venue `open_interest_usd`, `oi_change_1h_pct`) | `open_interest_snapshots` via `latest_open_interest` | `cross_venue_open_interest` (30 min) |

## Components and flow

### Provider (ME-R01, R02, R03)

`trading/market_environment.py::build_market_environment(instrument, cutoff)` reads each
source's latest row with `observed_at <= cutoff`, runs `assess_freshness` with the domain
above, and emits the fact with its state. A non-fresh verdict drops the number and keeps
the state. Breadth is computed from the batch, never a per-coin list. Output is sorted
and canonical-JSON, so the same rows and cutoff give identical bytes.

### Context and prompt (ME-R01, R04, R05)

`EntryAnalysisContext` gains `market_environment`. The Planner prompt renders it as one
short block only in `environment_on`. `EntryHypothesis` gains `environment_considered:
list[{fact, effect, why}]` (empty allowed). A validator rejects a citation of a fact whose
state is not `fresh`. The Compiler, sizing and Risk do not read either field.

### Arm and measurement (ME-R06, R07)

The arm is a stable hash of `planning_identity`, salted separately from spec 0001's
Playbook arm so the two can run as a 2×2 factorial (pending the open question). Metrics
and the anytime harm stop reuse spec 0001's design (LL-R16–R18): judgment R per decision,
calibration of `p_target_first`, clustered by planning thread.

### Cleanup (ME-R08)

Delete `data/context_builder.py::render_enriched_context_md`,
`trading/market_context.py::build_market_context` and their tests; update
`data_utilization.md` consumer cells to `market_environment`. This also satisfies spec
0003 FC-R05 for these rows.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| Source table empty or row older than domain bound (ME-R03) | fact carries state, no value | section state; doctor `market_environment` item | `stale`/`missing`, never `0` |
| Only the trading venue reports funding/OI (ME-R03) | comparison omitted | `unavailable: single_venue` | no spread value |
| Provider raises (ME-R03) | whole section `unavailable` with error class; plan proceeds | section + error log | no values |
| AI cites a non-fresh fact (ME-R05) | hypothesis format failure | existing compile-invalid path | n/a |
| Harm test fires (ME-R07) | section off for all threads | doctor + audit event | n/a |

## Trust and complexity review

Read-only over existing rows; no authority added (ME-R04). The one new module replaces two
dead renderers (ME-R08), so the number of LLM context implementations stays one.
Smaller alternatives were rejected below; the randomised arm is the minimum needed to know
whether a new prompt section helps or harms.

## Alternatives considered

- **Deterministic macro gate in Risk or Compiler.** Rejected: factor rule, excluded by the
  owner; the pooled table in spec.md is not causal evidence.
- **Feed everything (on-chain, news, prediction markets).** Rejected: on-chain is daily,
  news is Serenity-only and prediction markets are out of scope; more context dilutes the
  prompt without a stated mechanism.
- **Add the facts to `universe_feature_snapshots` columns.** Rejected: that ties
  market-wide facts to per-instrument feature rows and their refresh cadence; the provider
  reads at the planning cutoff instead.
- **Ship without randomisation.** Rejected: no way to tell help from harm (spec 0001
  rationale).

## Traceability

| Requirement | Design section | Verification | Evidence type |
|---|---|---|---|
| ME-R01 | Provider; Context and prompt | `tests/specs/spec_market_environment.py::ME-R01` (all facts present, deterministic bytes) | deterministic test |
| ME-R02 | Provider | `tests/specs/spec_market_environment.py::ME-R02` (row after cutoff ignored; size bound) | deterministic test |
| ME-R03 | Provider; Failure semantics | `tests/specs/spec_market_environment.py::ME-R03` (stale/missing/single-venue carry no number) | deterministic test |
| ME-R04 | Context and prompt | `tests/specs/spec_market_environment.py::ME-R04` (Compiler/Risk output identical with and without section) | deterministic test |
| ME-R05 | Context and prompt | `tests/specs/spec_market_environment.py::ME-R05` (empty in off arm; non-fresh citation rejected) | deterministic test |
| ME-R06 | Arm and measurement | `tests/specs/spec_market_environment.py::ME-R06` (stable arm; stored both arms, rendered on only) | deterministic test |
| ME-R07 | Arm and measurement | `tests/specs/spec_market_environment.py::ME-R07` (harm threshold switches off) + runtime per-arm report | deterministic test + runtime observation |
| ME-R08 | Cleanup | AST search for removed renderers + `spec_data_utilization.py` consumer rows | deterministic test |

## Rollout and rollback

Land the provider and context field with the arm forced to `environment_off` (stored,
never shown), verify the section on stored Fedora rows, then enable randomisation.
Rollback: force `environment_off`; the stored section remains as evidence. Enabling the
arm changes Planner input, so it starts a new observation window by the experiment
protocol.
