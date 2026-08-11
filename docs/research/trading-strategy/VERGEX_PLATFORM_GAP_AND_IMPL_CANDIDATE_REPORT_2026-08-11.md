# Vergex Platform Gap and Implementation Candidate Report

Date: 2026-08-11

Role: point-in-time research evidence and candidate contract

Base repository snapshot: `4309bda0`

Decision state: P0, P1, the bounded manual P2 path, and the P3 read-only
evaluation capability are approved and implemented in the current worktree;
no prospective P3 cohort has been opened or qualified, and P4 remains unapproved

## Executive conclusion

Aletheia has already implemented the skeleton of Vergex-style position
structure intelligence: a seven-dimension vocabulary, aggregate structure
schema, minimal offline calculations, a reconstructed composite prior, compact
Agent advisory context, provenance, freshness handling, and fixed-horizon
shadow settlement.

It still has no trusted automatic holder/position source, scheduled
cross-sectional producer, prospective structure history, or economic proof.
The implemented path is deliberately manual and observation-only so formula
and source discrepancies remain separable.

The highest-value path is therefore:

1. freeze exact metric definitions and correct misleading approximations;
2. establish deterministic offline benchmarks and coverage semantics;
3. evaluate a bounded, observation-only source path;
4. collect prospective structure/outcome history;
5. add risk-bin, flow, and visualization consumers only after the underlying
   facts are reliable.

Vergex's marketplace, copy-trading, user-Agent, wallet, billing, and arbitrary
code-strategy layers are multi-user SaaS packaging. They are not current
Aletheia gaps and should not be imported by implication.

## P0 implementation evidence

The owner approved P0 on 2026-08-11. The current worktree implements the frozen
offline research semantics in `aletheia/data/position_x.py` and focused
regressions in `tests/test_position_structure_features.py`.

| P0 boundary | Implemented result |
|---|---|
| Holder concentration | Top10/Top50 now rank holder notionals rather than entry-price buckets; HHI and effective holder count are also returned |
| CGO | Loss-dollar asymmetry is frozen as `ln((short_loss + 1 USD) / (long_loss + 1 USD))`; missing finite losses remain unavailable |
| Cost Basis Profile | Entry prices use configurable logarithmic bins with a 0.25% default and deterministic POC tie-breaking |
| Value Area | The selected region expands contiguously from POC until it covers the configured 70% default; coverage and bin counts are returned |
| Cost walls | Individually qualified adjacent bins merge into walls; centroid, notional, share, distance, strength, SKW raw and WP raw are returned |
| Convexity | Nearby liquidation prices use the same stable bins before the densest-bin share is calculated |
| Cascade semantics | Market-relative Cascade requires an explicit valid market-OI denominator; sample-relative Cascade has a separate field |
| Availability | Missing cost, holder, wall, CGO, liquidation, or market-OI inputs return typed unavailable states rather than zero |

The change remains offline research code. It adds no source, scheduler,
database migration, API, UI, Agent prompt, Risk, Execution, credential, or live
authority. Six focused tests pass, covering the original feature fixture,
composite prior, advisory freshness, holder-versus-price-bucket concentration,
stable price/liquidation bins and clustered walls, and unavailable/invalid
denominator behavior. Ruff static checks pass for the changed Python files.

## P1 and P2 implementation evidence

The owner approved P1 and P2 on 2026-08-11. P1 adds an offline, atomic
multi-market benchmark with a stable batch hash, exact universe, maximum
source-time skew, minimum coverage, average-rank ties, explicit availability
strata, and all five raw dimensions. Each aggregate row retains individual
factor values and percentiles, the reconstructed research prior, an equal-weight
score, and a zero no-structure baseline. The benchmark suite covers the
declared bin-width, liquidation-band, Value Area, and wall-share sensitivity
ranges.

P2 reuses `aletheia data ingest-perp-structure PATH`. The command now accepts
either pre-aggregated `rows` or a `markets` batch containing sanitized holder
facts. A holder batch defaults to at least three perp markets, at least 80%
reported coverage per market, and at most 300 seconds of source-time skew. It
is computed on demand, reduced to the existing aggregate whitelist, and then
persisted in one transaction. Raw holder rows and identities are not stored;
recursive address/wallet/credential-shaped fields are rejected before any
write. Batch identity, source hash, coverage, universe size, and policy epoch
remain auditable.

The accepted P2 operating choices are: manual owner import rather than an
automatic source; aggregate-only storage; on-demand cadence; exact batch
membership; existing 30-day snapshot/outcome retention; and fail-closed
validation with transactional rollback. This adds no network collector,
scheduler, schema, API, UI, Agent prompt, Risk, Execution, credential, Paper,
or live authority. The focused verification command passes 35 tests, and Ruff
passes for every changed Python file.

## P3 evaluation implementation evidence

P3 adds the read-only `aletheia data evaluate-perp-structure` command over the
existing aggregate snapshots and settled 1h/4h/24h outcomes. It recomputes raw
market returns from entry/exit prices and independently evaluates the
reconstructed weighted prior, equal weight, each of the five factors, and a
zero-return no-structure baseline. Results include sample and score coverage,
mean/median directional gross return, hit rate, and Spearman IC per horizon.

The evaluator rejects retrospective or ambiguous evidence: snapshot/entry time
mismatch, import lag above 300 seconds, import after the outcome due time,
settlement before due time, missing price source, source-hash mismatch, mixed
policy epochs, missing batch provenance, and mixed feature contracts all fail
closed. Partial dimension rows can contribute only to available individual
factors; weighted and equal-weight comparisons require the complete five-factor
availability stratum.

`ready_for_research_review` is only a reporting gate: defaults are 30 eligible
samples per horizon, 14 calendar days, at least 80% eligible settlement
coverage, one policy epoch, and one feature contract. It never grants promotion
or decision authority. Cost-aware results remain unavailable because the
current structure outcome contract does not persist fee, spread, slippage, or
funding-cost evidence. No real prospective cohort was started, and no Fedora
runtime or observation-window state is claimed.

## Status semantics

| Label | Meaning in this report |
|---|---|
| Implemented | Current code has a durable state or production consumer |
| Partial | Some code exists, but fidelity, producer, consumer, or evidence is incomplete |
| Missing | No matching current implementation was found |
| Excluded | Current product or safety scope intentionally rejects the capability |
| Runtime unknown | Repository support exists, but current Fedora state was not queried |

## Multi-temporal capability assessment

| Capability | Vergex report snapshot | Historical Aletheia target | Current repository | Candidate future |
|---|---|---|---|---|
| Structure vocabulary | CGO, VAP, SKW, LFA, WP plus Convexity and Cascade | Normalize definitions and caveats | Implemented as research cards, schema fields, and context vocabulary | Freeze Aletheia-owned mathematical definitions |
| Holder inventory | Broad address/position coverage claimed | Prefer aggregate snapshots; avoid raw sensitive material | Missing as an automatic source | Decide whether complete holder coverage is feasible and necessary |
| Cost distribution | Entry-price structure, POC, Value Area, walls | Deterministic offline extractor | Partial minimal helper | Stable price bins, contiguous Value Area, wall significance |
| Liquidation distribution | Fuel above/below, concentration, cascade | Deterministic offline extractor | Partial minimal helper | Stable liquidation bins and coverage-aware denominators |
| Cross-sectional dimensions | Percentiles across a market universe | Five directional percentiles | Generic percentile helper exists; no scheduled full-universe producer | Atomic universe batch with exact membership and timestamp |
| Composite structure score | Reconstructed weighted composite | Research prior followed by local validation | Implemented as a fixed research prior | Re-estimate or reject only from prospective Aletheia outcomes |
| Agent explanation | Compact structure read and dimension evidence | Observer and Operator advisory context | Implemented, stale/missing data fails closed | Test whether context changes decision quality before influence |
| Position assessment | Support, warning, invalidation | Advisory-only assessor | Partial: bias/context exist, position-relative verdict does not | Exact-position advisory experiment |
| Forward outcomes | Historical moat emphasized by report | 1h/4h/24h shadow outcomes | Implemented infrastructure; current real cohort not established here | Coverage- and cost-aware prospective evaluation |
| Product visualization | Risk maps, structure panels, holder views | Deferred from first version | Missing dedicated structure UI/API | Add only after semantic and data-quality gates pass |
| Commercial platform | Marketplace, wallets, billing, user Agents | Not part of the target | Excluded by local-first single-owner scope | Reconsider only through a separate product decision |

## Current implementation by layer

| Layer | Implemented now | Material ceiling |
|---|---|---|
| Public derivatives context | Hyperliquid funding/OI and true 1h OI delta; keyed liquidation/OI history through Coinalyze | Not a holder cost-basis inventory |
| Structure persistence | `perp_structure_snapshots`, dimension/risk JSON, source identity, quality and timestamps | No automatic trusted structure producer |
| Offline metrics | LFA aggregation, POC/Value Area approximation, support/resistance, concentration, cascade and dimension directions | Several definitions are simplified or semantically mismatched |
| Cross-sectional math | Generic percentile helper and reconstructed five-factor composite | No atomic scheduled universe calculation or local coefficient validation |
| Agent consumption | Market Observer and System Operator compact advisory context | No proven incremental decision value |
| Availability | Missing/stale rows become insufficient data; degraded quality is a caveat | Current Fedora availability was not inspected |
| Evaluation | 1h/4h/24h observation-only settlement and 14-day review maturity | No confirmed complete prospective cohort in this analysis |
| Authority | Decision influence disabled; no Risk/Execution authority | Correct boundary; should remain unchanged during research |

## Metric fidelity assessment

| Metric | Current calculation | Status | Why it is not yet a production benchmark |
|---|---|---|---|
| LFA | Sum short liquidation notional above mark and long liquidation notional below mark inside a band | Partial-high | Needs explicit market coverage, stable notional basis, and cross-sectional raw-score definition |
| CGO | Aggregate selected unrealized PnL and compare directions | Partial-low | Signed-loss semantics are not frozen and may not match the intended `gLong/gShort` pressure measure |
| POC | Entry price with greatest aggregated notional | Partial | Needs stable price bins; exact floating entry prices are not a robust profile |
| Value Area | Select largest price weights until 70%, then take minimum and maximum selected price | Partial | Selection can be discontiguous; this is not a contiguous expansion around POC |
| SKW | Compare distance to nearest qualifying support and resistance observation | Partial-low | A single nearest observation is not necessarily a statistically meaningful wall |
| WP | Compare notionals attached to nearest support and resistance candidates | Partial-low | No wall binning, significance threshold, or cluster ranking |
| Convexity | Largest exact liquidation-price bucket divided by in-band fuel | Partial-low | Exact floats fragment nearby liquidation levels; bin policy is missing |
| Cascade | Notional with liquidation price within 5% divided by input-sample notional | Partial | Denominator is sample coverage, not necessarily market OI |
| Top10/Top50 | Sum the largest entry-price buckets | Semantic mismatch | Vergex's name denotes largest holders; the current helper measures price-bucket concentration |
| Percentile | Sort values and assign linear ranks | Partial | Tie policy and atomic cross-sectional membership are not defined |
| Composite Z | Fixed five-factor research weights over centered percentiles | Research prior | Mathematically usable, economically unvalidated in Aletheia |

## Candidate benchmark contract

This section defines review candidates, not approved behavior. Every formula
must be accepted, rejected, or revised before implementation.

### Common input contract

Every calculation candidate should consume one atomic market snapshot with:

| Field | Candidate requirement |
|---|---|
| Instrument identity | Exact venue, base, quote, and market type; spot and perp never share structure |
| `as_of_time` | UTC source time for every included position |
| Reference price | Finite, positive mark price from the same venue and time envelope |
| Position side | Normalized `long` or `short` |
| Position notional | Positive USD notional with one declared conversion rule |
| Entry price | Finite and positive when used in cost metrics |
| Liquidation price | Finite and positive when used in liquidation metrics |
| Unrealized PnL | Signed USD value with an explicit source definition |
| Coverage | Included-holder count, estimated market coverage, completeness state, and collection errors |
| Universe identity | Immutable list/hash of instruments included in one percentile batch |

Fail-closed rules:

- missing market identity invalidates the row;
- missing entry price excludes only cost-derived metrics;
- missing liquidation price excludes only liquidation-derived metrics;
- coverage below an accepted threshold makes the metric unavailable rather
  than zero;
- different source times outside the accepted skew cannot share one
  cross-sectional batch;
- unknown values remain unavailable and are never rendered as zero.

### Price-bin benchmark

Candidate default:

```text
bin_width_pct = 0.25%
bin(price) = round(log(price / reference_price) / log(1 + bin_width_pct))
```

Required sensitivity check:

| Parameter | Required comparison |
|---|---|
| Cost/liquidation bin width | 0.10%, 0.25%, 0.50%, 1.00% |
| Liquidation band | 5%, 10%, 15%, 20% around mark |
| Value Area coverage | 68%, 70%, 80% |
| Minimum wall share | 0.5%, 1%, 2%, 5% of covered notional |

No default should enter Agent context until the result is stable across nearby
parameter choices or its instability is explicitly surfaced.

### Cost Basis POC and Value Area

Naming rule: this is a **Cost Basis Profile**, not an exchange Volume Profile.
It weights holder entry prices by current position notional; it does not claim
to reconstruct traded volume at price.

Candidate calculation:

1. Bin valid entry prices using the frozen price-bin policy.
2. Sum current notional per bin, separately retaining long and short weights.
3. Define POC as the maximum-total-notional bin with deterministic tie-breaking
   toward the bin nearest the mark price.
4. Starting at POC, add the larger adjacent bin on either side until cumulative
   weight reaches the accepted Value Area ratio.
5. Return POC, Value Area low/high, covered notional, excluded notional, bin
   width, and coverage state.

Benchmark invariants:

- POC must lie inside Value Area;
- Value Area must be contiguous;
- selected notional must reach the requested ratio unless the metric is
  unavailable;
- multiplying every notional by a positive constant must not change levels;
- permuting input rows must not change the result.

### Holder concentration

Candidate definition:

```text
TopKConcentration = sum(K largest holder notionals) / total covered notional
```

Required outputs:

- Top1, Top5, Top10, and Top50 concentration;
- holder count and covered notional;
- long-only and short-only concentration where sample size permits;
- Herfindahl-Hirschman Index as a scale-independent companion:

```text
HHI = sum((holder_notional / total_notional)^2)
effective_holder_count = 1 / HHI
```

This candidate explicitly replaces the current price-bucket interpretation of
Top10/Top50.

### LFA: Liquidation Fuel Asymmetry

Candidate primitives:

```text
short_fuel_above = sum(short notional with mark < liq_price <= mark * (1 + band))
long_fuel_below  = sum(long notional with mark * (1 - band) <= liq_price < mark)
lfa_raw = ln((short_fuel_above + epsilon) / (long_fuel_below + epsilon))
```

Interpretation candidate:

- positive: more forced-buy fuel above, directionally bullish if triggered;
- negative: more forced-sell fuel below, directionally bearish if triggered;
- near zero: balanced fuel;
- LFA describes potential fuel, not trigger probability.

Required benchmark outputs include both dollar amounts, band, raw log ratio,
coverage, and cross-sectional percentile. Dollar values must never be hidden
behind the percentile during research evaluation.

### CGO: Trapped Cost Overhang

The report evidence links CGO to `gLong/gShort`, but the exact vendor formula is
not authoritative. Aletheia should own a falsifiable definition rather than
claiming numerical equivalence.

Candidate loss-based definition:

```text
underwater_long_loss  = sum(abs(min(unrealized_pnl, 0))) for long positions
underwater_short_loss = sum(abs(min(unrealized_pnl, 0))) for short positions
cgo_raw = ln((underwater_short_loss + epsilon) /
             (underwater_long_loss + epsilon))
```

Candidate interpretation:

- positive: trapped-short covering support exceeds trapped-long overhead;
- negative: trapped-long overhead exceeds trapped-short covering support.

Before acceptance, compare at least three definitions:

1. loss-dollar ratio;
2. underwater-notional ratio;
3. distance-weighted underwater notional.

The selected definition must be based on forward discrimination and stability,
not similarity to one reconstructed Vergex sample.

### Wall strength and SKW

Candidate cost-wall extraction:

1. Build long and short Cost Basis Profile bins.
2. Retain bins above the minimum wall share.
3. Merge adjacent qualifying bins into one wall.
4. Record wall centroid, total notional, width, side, and distance from mark.

Candidate wall strength:

```text
wall_strength = wall_notional_share / max(distance_pct, distance_floor)
```

Candidate SKW raw score:

```text
skw_raw = ln((nearest_resistance_distance_pct + epsilon) /
              (nearest_support_distance_pct + epsilon))
```

Positive means qualified support is closer than qualified resistance. Missing
one side must produce a typed one-sided state, not an arbitrarily extreme
number.

### WP: Break-even Wall Asymmetry

Candidate definition:

```text
support_wall_weight = sum(qualified trapped-short wall notionals below mark)
resistance_wall_weight = sum(qualified trapped-long wall notionals above mark)
wp_raw = ln((support_wall_weight + epsilon) /
            (resistance_wall_weight + epsilon))
```

The wall selection policy and maximum distance must be frozen with the score.
Changing those parameters creates a new feature version.

### VAP: Value Area Position

Candidate normalized position:

```text
vap_raw = (mark - value_area_low) /
          max(value_area_high - value_area_low, epsilon)
```

This number should not be treated as monotonically bullish without empirical
evidence. Candidate categorical states should be retained separately:

- below Value Area;
- inside lower half;
- near POC;
- inside upper half;
- above Value Area.

The first evaluation should test nonlinear/category behavior rather than force
one linear directional score.

### Convexity and Cascade

Candidate liquidation convexity:

```text
convexity = largest qualified liquidation-bin notional /
            total liquidation notional inside the declared band
```

Candidate cascade vulnerability:

```text
cascade_5pct = liquidation notional within +/-5% of mark /
               total covered market open interest
```

If total market OI is unavailable, a sample-relative value may be stored under
a different name. It must not be labelled market cascade vulnerability.

Both remain nondirectional risk context. They may change confidence or caution
in a future experiment, but they do not vote long or short.

### Cross-sectional percentiles

Candidate batch contract:

- one immutable universe membership list;
- one source/venue policy;
- maximum accepted source-time skew;
- minimum number and fraction of available instruments;
- deterministic tie handling using average rank;
- percentile definition frozen with a version;
- no mixing of complete and degraded metrics without an explicit availability
  stratum.

Candidate percentile:

```text
percentile = 100 * (average_rank - 1) / max(valid_count - 1, 1)
```

Percentiles from different batch identities are not directly comparable.

### Composite score

The reconstructed research prior is algebraically expressible as:

```text
composite_z =
    0.95136998 * ((CGO_pct - 50) / 100)
  + 0.64689392 * ((VAP_pct - 50) / 100)
  + 0.44539054 * ((SKW_pct - 50) / 100)
  + 1.28031713 * ((LFA_pct - 50) / 100)
  + 0.27750722 * ((WP_pct  - 50) / 100)
  - 0.06909579
```

This is a **research prior**, not an Aletheia production index. Candidate
benchmark requirements:

- compare each individual dimension against the composite;
- report Spearman rank correlation and information coefficient by horizon;
- evaluate gross and cost-aware directional returns;
- report availability and turnover, not only predictive metrics;
- use walk-forward or prospective periods, never refit and evaluate on the same
  cross-section;
- compare with a no-structure baseline and simpler equal-weight composite;
- reject the weighted composite if it does not outperform simpler alternatives
  out of sample.

## Candidate feature catalog

| ID | Candidate | Value hypothesis | Dependency | Initial authority |
|---|---|---|---|---|
| C-01 | Cost Basis Profile and contiguous Value Area | Reveals participant break-even structure not present in candle OHLCV | Timestamped holder positions and coverage | Offline research |
| C-02 | Cost and liquidation risk bins | Makes walls, fuel and concentration reproducible and visualizable | C-01 bin contract | Offline research |
| C-03 | Correct holder concentration and HHI | Measures concentration and fragility without address display | Holder-level notional | Offline research |
| C-04 | LFA raw ratio and percentile | Separates upside squeeze fuel from downside flush fuel | Liquidation prices and OI coverage | Observation-only |
| C-05 | Aletheia-owned CGO | Tests whether trapped-loss asymmetry has forward value | Entry, side, PnL/notional | Offline research |
| C-06 | Qualified cost walls, SKW and WP | Converts individual positions into stable support/resistance structure | C-01/C-02 | Offline research |
| C-07 | Convexity and Cascade | Measures event amplification and near-liquidation fragility | Liquidation bins and total OI | Observation-only risk context |
| C-08 | Atomic cross-sectional structure batch | Makes percentiles and rank auditable | Complete multi-market snapshot | Observation-only |
| C-09 | Structure history and fixed-horizon outcomes | Tests persistence, decay and economic value | C-04 through C-08 | Observation-only |
| C-10 | Position-relative structure assessor | Tests support/warning/invalidation for exact open positions | Mature C-09 evidence | Separate Paper experiment |
| C-11 | Flow quadrants | Distinguishes opening/closing long/short pressure | Position deltas plus trade flow | Future research |
| C-12 | Whale activity time series | Detects durable large-holder accumulation/distribution | Stable holder identity and continuous sampling | Future research |
| C-13 | TWAP activity | Tests whether scheduled large execution adds predictive value | Reliable order/activity source | Defer |
| C-14 | Dedicated structure UI | Makes bins, history, coverage and outcomes inspectable | Stable C-01 through C-09 contracts | Owner visibility only |

## Priority and dependency review

Priority here means analytical dependency and safety, not approved execution
order or project tracking.

### P0: implemented semantic foundations

| Candidate | Required decision | Acceptance evidence |
|---|---|---|
| P0-1 Top concentration | Replace price-bucket Top10/Top50 with holder TopK; rename old metric if retained | Permutation, scaling, and hand-calculated fixture checks |
| P0-2 CGO definition | Select loss, notional, or distance-weighted formulation | Frozen sign convention plus forward-discrimination protocol |
| P0-3 Cost Basis Profile | Freeze log-price binning and contiguous Value Area algorithm | Deterministic fixture and parameter-sensitivity report |
| P0-4 Wall semantics | Replace nearest-position proxies with qualified clustered walls | Synthetic multi-wall fixtures with stable selected levels |
| P0-5 Convexity denominator | Freeze liquidation bins, band, and denominator | Neighboring-price cluster fixture and coverage failure cases |
| P0-6 Availability contract | Distinguish market-relative, sample-relative, degraded, and unavailable values | Missing/partial/stale fixtures never produce zero-like facts |

P0 changes would affect only research semantics unless separately approved to
reach Agent context. They should not change Risk, Execution, or live authority.
The current implementation preserves that boundary.

### P1: implemented offline benchmark suite

| Candidate | Minimum result |
|---|---|
| P1-1 Sanitized deterministic fixtures | Small hand-calculable holder sets covering balanced, squeeze, flush, concentrated, sparse, and missing-data cases |
| P1-2 Reference calculations | Expected bins, POC, Value Area, walls, LFA, CGO candidates, concentration, Convexity and Cascade |
| P1-3 Parameter sensitivity | Results across the declared bin, band, Value Area, and wall thresholds |
| P1-4 Cross-sectional batch fixture | At least one exact multi-market batch with ties, missing metrics, and immutable membership |
| P1-5 Composite baselines | Individual factors, equal weight, reconstructed prior, and no-structure baseline |

These gates are implemented in the focused offline suite. No continuous data
source is implied.

### P2: implemented bounded observation-only data path

| Candidate | Implemented decision |
|---|---|
| P2-1 Coverage strategy | Manual owner-authorized sanitized batch; default 80% per market, three markets, 300-second skew; no automatic source |
| P2-2 Address policy | Address/account fields rejected recursively; only aggregate snapshots persist |
| P2-3 Collection cadence | Explicit on-demand CLI only; no scheduler or freshness claim between imports |
| P2-4 Universe | Exact perp market membership and parameters are bound to a stable batch hash |
| P2-5 Retention | Existing 30-day aggregate snapshot and shadow-outcome policy; no raw holder retention |
| P2-6 Failure semantics | Coverage, identity, type, duplicate-market, and time-skew failures occur before writes; inserts are transactional |

The first accepted source path should write aggregate observation-only facts,
not raw addresses into Agent context and not direct decision authority.

### P3: evaluation capability implemented; prospective evidence pending

| Evaluation | Candidate baseline |
|---|---|
| Horizons | 1h, 4h, 24h plus one horizon matched to the target decision role |
| Outcomes | Directional gross return and cost-aware return where a trade interpretation exists |
| Availability | Row, market, batch and time coverage |
| Predictive statistics | Spearman IC, rank spread, calibration by score bucket, confidence intervals |
| Economic statistics | Mean/median net return, hit rate, downside tail, turnover and sample count |
| Stability | By regime, market family, direction, coverage class and feature version |
| Baselines | No structure, existing technical context, individual factor, equal weight |
| Leakage control | Snapshot time strictly precedes feature consumption and outcome window |

Passing a 14-day age check alone is not economic proof. Any later Agent-context
or Paper influence requires a separately approved treatment contract, policy
identity, failure semantics, and prospective comparison.

### P4: product consumers after evidence

| Candidate | When it becomes justified |
|---|---|
| Risk-bin chart | Stable bin contract and continuous coverage exist |
| Structure history chart | Time-series identity and missingness semantics are reliable |
| Position structure drawer | Exact-position assessor has an approved advisory contract |
| Flow quadrants | Position deltas can be separated from price and transfer effects |
| Whale activity | Stable identity and privacy policy are accepted |
| TWAP tracking | A trustworthy source and measurable use case exist |

### Do not implement from this report

| Vergex surface | Reason |
|---|---|
| Strategy marketplace and copy trading | Multi-user commercial platform, not part of the local single-owner mainline |
| Arbitrary code-strategy execution | Expands executable and trading authority beyond current safety scope |
| Agent wallets or Privy-style custody | Expands credential and signer boundaries |
| User-Agent asset visibility | Privacy risk and no market-alpha justification |
| AI credit and subscription billing | Aletheia already has owner economics; commercial tenant billing is unnecessary |
| Public prompt-template marketplace | Increases prompt/IP exposure and does not improve evidence quality |
| Exact Vergex numeric imitation | Similarity to vendor output is not Alpha or correctness proof |

## Implementation decision matrix

The owner can assess the candidate scope using this matrix before any code is
authorized:

| Decision | Option A | Option B | Recommended default |
|---|---|---|---|
| Primary objective | Reproduce Vergex numerically | Build Aletheia-owned falsifiable structure factors | B |
| First delivery | Automatic source | Offline semantic corrections and benchmarks | Offline benchmarks |
| Raw storage | Persist holder identities | Persist aggregate bins/snapshots only | Aggregate only unless identity is proven necessary |
| Profile name | Volume Profile | Cost Basis Profile | Cost Basis Profile |
| Composite | Freeze vendor prior as final | Treat prior/equal weight/individual factors as competitors | Competing baselines |
| Agent use | Inject immediately | Keep offline/observation-only through evaluation | Observation-only |
| UI | Build dashboard first | Build after data and missingness contracts stabilize | After evidence |
| Thin layers | Copy marketplace/wallet/billing | Exclude from current scope | Exclude |

## Proposed implementation waves for review

P0 through P2 correspond to the completed parts of Waves A through C's manual
source subset. P3 implements the read-only part of Wave D, but no real cohort
has been opened or qualified. Later work remains candidate scope, not authority.

| Wave | Candidate contents | Explicitly unchanged | Completion gate |
|---|---|---|---|
| A | Correct TopK, freeze Cost Basis Profile/Value Area, CGO, wall and Convexity definitions; add deterministic offline benchmarks | No source, schema, prompt, Risk or Execution change | Hand-calculated fixtures and sensitivity report agree with frozen definitions |
| B | Add versioned aggregate bins and atomic cross-sectional batch computation in offline mode | No scheduler or Agent consumer | Reproducible multi-market batch with typed missingness and provenance |
| C | Add one approved observation-only aggregate source and retention path | No decision influence | Source freshness, coverage, failure and replay checks pass |
| D | Accumulate prospective history and compare factor/composite baselines | No Paper or Risk authority | Predeclared sample and economic evaluation gate reached |
| E | Add bounded owner visualization and, only if approved, a separate Agent-context experiment | Risk/Execution remain unchanged | UI truth contract or experiment acceptance evidence passes |

## Decisions still required before later implementation

1. Is the objective to reproduce Vergex output, or to develop Aletheia-owned
   structure factors? This report recommends the latter.
2. Is holder-level identity necessary, or are aggregate bins sufficient?
3. Which exact venue and market universe define the first experiment?
4. Which coverage threshold makes a structure metric usable rather than
   unavailable?
5. Which CGO definition should enter the benchmark comparison?
6. Which decision horizon is the primary target: opportunity selection,
   position review, or risk context?
7. Is the reconstructed composite allowed only as a benchmark, or also as an
   observation-only displayed index?
8. Whether a merged exact commit may be deployed to Fedora to open a real P3
   prospective cohort, and only later whether any P4 consumer is reviewed.

## Final assessment

The reusable value in Vergex is a measurable position-structure research
program, not its exposed prompts, account data, or SaaS shell. Aletheia already
has enough schema, audit, Agent-context, and outcome infrastructure to avoid
building a second platform. Its missing core is narrower: reliable structure
inputs, precise metric semantics, continuous batches, and economic proof.

P0 through P2 close the offline semantic, benchmark, and bounded manual
ingestion contracts. P3 can now evaluate a valid cohort without confusing
historical replay with prospective evidence, but the cohort itself does not yet
exist. No automatic source, scheduler, UI, deployment, observation window, or
trading authority was added.

## Related evidence

- [Analysis process evidence](VERGEX_PLATFORM_ANALYSIS_PROCESS_2026-08-11.md)
- [Earlier Position X research](position_x_deep_research.md)
- [Trading theory research map](../trading-theory/README.md)
- [Current data utilization](../../design/data/data_utilization.md)
- [Current product scope](../../product/SCOPE.md)
- [Current Agent contracts](../../design/contracts/agent_contracts.md)
