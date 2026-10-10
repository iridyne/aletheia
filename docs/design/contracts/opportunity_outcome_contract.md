# Opportunity Outcome Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `OO` · **Owner code:** `core/aletheia/universe/outcome_economics.py` (economics) and
> `core/aletheia/universe/filter_outcome_recorder.py` (settlement)

## 1. Scope

This contract owns the economics and settlement status of `filter_candidate_outcomes`
(FCO): the fixed-horizon, cost-aware counterfactual result of a candidate that had an
ex-ante direction at a decision time. It answers "given what was visible then, on the
exact target market and direction, did this candidate leave a tradable net edge?" — not
"did price go up". It supports Candidate selection measurement on the mainline
(Universe → Candidate → … → Outcome). It does not own realized Position PnL, Risk limits,
order execution, or any automatic tuning. Which gates create rows is owned by the
[Filter Evidence contract](filter_evidence_contract.md); this contract owns the row.

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/analytics/filter_evidence.py::build_candidate_outcomes` | call (row builder) | `core/aletheia/universe/candidate_selection_shadow.py::build_candidate_selection_shadow_bundle` (every Universe refresh) |
| `core/aletheia/analytics/filter_evidence.py::record_filter_decision` / `::ensure_candidate_outcomes` | call (row registration with direction guard) | Universe tradability gates, Risk (`record_risk_filter_decision`), `core/aletheia/analytics/causal_controls.py` |
| `core/aletheia/universe/filter_outcome_recorder.py::settle_due_filter_outcomes` | scheduler job `outcome_settler` (every 10 minutes) | `core/aletheia/universe/outcome_recorder.py::settle_due` |
| `core/aletheia/universe/outcome_economics.py::estimate_outcome_economics` | call | `filter_outcome_recorder.py::settle_due_filter_outcomes` |
| `core/aletheia/analytics/outcome_semantics.py::classify_counterfactual` | call | outcome readers (missed-alpha, filter pressure, Candidate win statistics) |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `filter_candidate_outcomes` | `storage/repositories_universe.py::insert_filter_candidate_outcomes_batch` (insert), `::settle_filter_candidate_outcome` / `::fail_filter_candidate_outcome` (settle) | Candidate Selection Utility estimator, calibration, filter feedback, alpha attribution, doctor | insert once per `outcome_candidate_key + horizon`; status moves `pending → settled` or `pending → failed` once; `allocation_selected` NULL may be resolved once |
| `perp_structure_shadow_outcomes` | optional Data structure-shadow settler (`configure_structure_shadow_settler`) | structure-shadow readers | same status vocabulary (OO-C14); owned by its own cohort |

## Requirements

### 2. Instrument identity and row creation

#### OO-C04 Exact instrument identity

`The outcome row shall store venue, canonical symbol, market_type and direction, and settlement shall resolve prices only through the full InstrumentId(venue, symbol, market_type).`

- WHEN a perpetual price fetch fails THEN the row is not settled from the same-named spot market.
- WHEN market identity is unknown THEN funding and net stay NULL (`tests/specs/spec_opportunity_outcomes.py::OO-B06`).
- Current Candidates are `perp`; `spot` rows exist only as historical records and residual code paths.

#### OO-C05 No directionless rows

`If the pre-decision direction is not long or short, then the recorder shall not create an outcome row.`

- WHEN a tradability gate rejects a feature with no scanner direction THEN only the filter fact is written.
- Historical `direction=unknown` rows stay immutable read-only audit evidence; they are not backfilled, migrated or deleted.

#### OO-C06 One result per candidate and horizon

`The outcome table shall hold at most one row per outcome_candidate_key and horizon, shared by every gate the candidate passed through.`

- WHEN two gates record the same candidate THEN one price path is settled once and is never counted as two samples.
- WHEN a row first written with `allocation_selected=NULL` is re-inserted with True/False THEN the definitive value is kept; a later NULL never overwrites it.

### 3. Fixed horizons and settlement price

#### OO-C07 Fixed horizon matrix

`The row builder shall support exactly the horizons 15m, 1h, 4h and 24h, writing all four by default and only the subset a caller declares.`

- WHEN a Candidate is unselected THEN only its `4h` row is written; a selected primary writes all four; an `entry-fill:<id>` outcome writes `4h`.

#### OO-C08 Horizon price, not runtime price

`When an outcome is due, the settler shall use the close of target-market OHLCV bars between entry_ts and due_at, preferring complete locally persisted bars (horizon_ohlcv_local) over a venue fetch (horizon_ohlcv).`

- WHEN local bars are incomplete for the window THEN the settler falls back to the venue.
- WHEN only a ticker is available and settlement lag ≤ 10% of the hold THEN the source is `near_due_ticker` and quality is accepted.
- WHEN the ticker is later than that THEN the source is `late_ticker`, quality fails, and the row becomes `failed` with the observed price recorded.

### 4. Return and cost fields

#### OO-C09 Return and cost formula

`The economics estimator shall compute, in percent, gross = (exit − entry) / entry × 100, directional = gross for long and −gross for short, fee = 2 × execution_fee_pct, spread/slippage = (entry_spread_bps + 2 × slippage_bps) / 100, and net = directional − fee − spread/slippage − funding.`

- WHEN entry spread is missing THEN `universe.max_spread_bps` is the fallback spread and `cost_model_version` is recorded (`v1`).
- `min_tradable_edge_pct = max(0.05, slippage_bps / 100)` is the estimation-error margin.
- `min_cost_covering_edge_pct = fee + spread/slippage + funding + min_tradable_edge_pct` is descriptive only and NULL when net is NULL.

#### OO-C10 Funding cost

`When the market is perpetual and the direction and entry funding rate are known, the estimator shall set funding = rate × 100 × hold_seconds / interval_seconds × (+1 long, −1 short), where interval_seconds is the T0-proved interval for scanner_family=candidate_selection_utility and 8 hours for every other producer.`

- WHEN a Candidate row's `candidate-perp-funding/v1` proof is missing or invalid THEN funding and net are NULL, never an 8h assumption.
- Positive funding is a cost to longs and income to shorts.

#### OO-C11 Unknown cost is never zero

`If entry funding is missing on a perpetual, the direction is unknown, or the settlement price quality is not accepted, then the estimator shall leave net_return_pct NULL.`

- WHEN a perpetual row has no entry funding THEN gross and directional are stored and net is NULL.

#### OO-C12 Canonical cost domain

`If a consumed cost is outside its canonical domain (fee per side finite 0..5 %, slippage finite 0..200 bps, fallback spread finite 1..200 bps, measured spread finite and ≥ 0), then settlement shall fail before writing.`

- WHEN fee is negative or NaN THEN no net is written and the row is not changed; costs are never clamped to 0.

### 5. Path quality

#### OO-C13 Directional excursions

`The estimator shall record mfe_pct ≥ 0 and mae_pct ≤ 0 as directional excursions over the hold, plus settlement_price_source and settlement_lag_sec.`

- MFE/MAE are gross path metrics; they do not replace net. A Candidate Selection Utility row whose MAE is unavailable stays pending.

### 6. Settlement status

#### OO-C14 One terminal status

`The settler shall use pending (registered, retryable), settled (target-horizon price obtained, economics written) and failed (terminal, never revisited) for filter_candidate_outcomes and perp_structure_shadow_outcomes.`

- `failed` rows have no `net_return_pct`, so no net consumer reads them.

#### OO-C15 Unreachable venue is not a measurement failure

`If a price request times out, is refused, is rate limited or the cycle budget runs out, then the settler shall leave the row pending, and when a whole wave produces no observation it shall stop the cycle and log that no row was written off.`

- WHEN the venue answers with no usable bar, a price ≤ 0, or a price more than 10× away from entry (either direction) THEN the row becomes `failed`.

#### OO-C16 Failed rows distinguish "never observed"

`When the settler writes failed for an observed but rejected price, it shall also store exit_price_usd and settlement_price_source, so failed with both NULL means the row was never observed.`

- WHEN maintenance requeue runs (`aletheia data requeue-unobserved-settlements`) THEN only never-observed failed rows are reclaimed.

### 7. Counterfactual verdict

#### OO-C17 Ordered verdict

`The classifier shall return missed_alpha_suspect when net > min_tradable_edge_pct, else positive_gross_move_only when directional > 0, else protective when net is complete, else insufficient_data.`

- WHEN gross direction is right but net does not clear the margin THEN the verdict is `positive_gross_move_only`, not missed alpha.
- The same single comparison (net above `min_tradable_edge_pct`, `net_clears_tradable_edge_sql`) defines "cleared its edge" for every win-rate, missed-alpha and filter-pressure statistic; nothing compares against `min_cost_covering_edge_pct`.

### 8. Consumers

#### OO-C01 Net-only consumers

`Every consumer that measures edge, win rate or calibration shall read only rows with net_return_pct IS NOT NULL and the current cost model.`

- WHEN a legacy gross-only row exists THEN it may explain price movement but never enters a net sample.

#### OO-C02 Cohort granularity

`Any consumer that aggregates outcomes shall keep at least scanner_family, direction, market_type and horizon separate, and shall stay neutral rather than merge opposite economics when it cannot consume that granularity.`

- WHEN the Universe UI shows calibration THEN it displays the backend cohorts without refolding them.

#### OO-C03 Learning use is scoped and out of this contract

`Where the optional Knowledge extension is installed, the settler shall pass settled 4h rows with net below −3% to the guard-candidate writer scoped by direction and market_type.`

- This is the current behavior only; learning consumers are defined by [spec 0001](../specs/0001-learning-loop/spec.md) (judgment labels in R), and FCO `net_return_pct` is selection-quality evidence.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| venue unreachable / budget exhausted | stay `pending`, stop wave | warning "unreachable … none were written off" | pending, never `failed` |
| answered but unusable price | `failed` with observed price/source | row status | net NULL |
| missing funding / direction / quality | economics written, net NULL | `net_return_pct IS NULL` | unavailable, never `0` |
| cost outside canonical domain | raise before write | settlement error log | no row change |
| Lab priority reader unavailable | continue in plain due order | warning `Lab priority unavailable` | ordering only |

## Authority

Observation only: it may write outcome rows and their settlement status; it never submits
orders, changes Risk, unlocks Live, or loosens a filter. Any change to ranking, thresholds
or sizing from these results needs a separate approved experiment.

## Verification

Requirement ids `OO-C01..C03` keep the meaning of the pre-existing executable claims with
those ids; new requirements continue from `OO-C04`.

| Requirement | Executable claim / focused test |
|---|---|
| OO-C01 | `tests/specs/spec_opportunity_outcomes.py::OO-C01` |
| OO-C02 | `tests/specs/spec_opportunity_outcomes.py::OO-C02` |
| OO-C03 | `tests/specs/spec_opportunity_outcomes.py::OO-C03` |
| OO-C04 | `tests/specs/spec_opportunity_outcomes.py::OO-B06`, `tests/test_universe_outcome_economics.py::test_perp_provider_fallback_never_downgrades_to_spot` |
| OO-C05 | `tests/specs/spec_filter_evidence.py::FE-B02` |
| OO-C06 | `tests/specs/spec_filter_evidence.py::FE-S23`, `::FE-B01` |
| OO-C07 | `tests/specs/spec_filter_evidence.py::FE-S02` |
| OO-C08 | `tests/test_filter_evidence.py::test_locally_answerable_rows_settle_without_any_exchange_request`, `tests/test_universe_outcome_economics.py::test_late_price_and_unknown_direction_do_not_create_net_result` |
| OO-C09 | `tests/specs/spec_opportunity_outcomes.py::OO-B01` |
| OO-C10 | `tests/specs/spec_candidate_selection_utility.py::CSU-S06`, `tests/test_candidate_funding.py::test_proved_interval_controls_candidate_cost` |
| OO-C11 | `tests/specs/spec_opportunity_outcomes.py::OO-B02` |
| OO-C12 | `tests/specs/spec_opportunity_outcomes.py::OO-B07` |
| OO-C13 | `tests/test_universe_outcome_economics.py::test_short_perp_funding_credit_and_directional_path` |
| OO-C14 | `tests/test_universe_outcome_economics.py::test_terminal_filter_settlement_is_first_writer_wins` |
| OO-C15 | `tests/specs/spec_opportunity_outcomes.py::OO-B04`, `tests/test_settlement_deferral.py::test_an_unreachable_venue_stops_the_cycle_instead_of_spending_the_budget` |
| OO-C16 | `tests/test_settlement_deferral.py::test_requeue_restores_only_the_rows_that_were_never_observed` |
| OO-C17 | `tests/specs/spec_opportunity_outcomes.py::OO-B03` |

## Decision references

- 2026-07-10: Stage D outcome design introduced cost-aware net, MFE/MAE and the ordered verdict.
- #3701 (migration `20260929_0094_drop_universe_forward_outcomes_v94`): `universe_forward_outcomes`
  dropped; FCO is the only fixed-horizon carrier. Its `forward_return_pct` compatibility field
  (equal to gross) existed only on that table; FCO has no such column.
- #3483 / #3486, owner decision 2026-10-09: admission and win statistics compare net with
  `min_tradable_edge_pct` only; `min_cost_covering_edge_pct` stays descriptive.
- #3086: Candidate rows use the T0-proved funding interval (merged into OO-C10; see the
  [Candidate Selection contract](candidate_selection_utility_contract.md)).
- #3073: near-due path and MAE semantics for Candidate rows.
- 2026-10-10 rewrite: removed the Ranker / System Operator alpha baseline and the
  `observer_pre_filter` diagnostic rules (no current writer; the Market Observer is retired,
  see [history](../history/filter_evidence_retired_chain.md)); unified the
  "no directionless rows" rule with the Filter Evidence contract per
  `analytics/filter_evidence.py::record_filter_decision`.
