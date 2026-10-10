# Market Structure Derived Data Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `MS` · **Owner code:** `core/aletheia/data/market_structure.py::derive_market_structure`

## Scope

Aletheia derives neutral, causal Swing, structure-state, BOS and CHOCH facts (and, on
request, FVG zones) from exact-instrument closed OHLCV. On the trading mainline these facts
are inputs to the Entry Planner: they become the confirmed-swing and broken-structure
anchors from which the deterministic Entry Plan Compiler places entry and invalidation, so
unavailable structure makes a hypothesis fail rather than compile without anchors. The Fast
Trigger carries a 1m projection of the same kernel as shadow evidence. Market structure grants
no approval authority: it never admits a Candidate, approves Risk or submits an order. It owns
no durable event ledger, incremental service, multi-timeframe aggregation, Order Block or
Liquidity model, and makes no Alpha claim. Compiler geometry itself belongs to the
[Entry Plan Contract](entry-plan-contract.md).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/data/market_structure.py::derive_market_structure` | pure kernel (bounded closed-bar walk) | `entry_causal_windows`, `evaluate_market_structure` |
| `core/aletheia/data/market_structure.py::canonical_market_structure_facts` | call (fact ids) | `core/aletheia/trading/entry_analysis.py::entry_causal_windows` |
| `core/aletheia/trading/entry_analysis.py::entry_causal_windows` | call | `core/aletheia/trading/entry_analysis.py::build_entry_analysis_context` ← `core/aletheia/agent_shell/entry_planner.py` |
| `core/aletheia/trading/entry_analysis.py::structural_anchors` | call | `build_entry_analysis_context` |
| `core/aletheia/universe/market_structure.py::evaluate_market_structure` | call (`market-structure/v2` projection) | `core/aletheia/trading/momentum_trigger.py::MomentumTriggerEngine` (Fast Trigger, started by `server/app.py` lifespan task `fast_trigger_watch`) |
| `core/aletheia/data/market_structure.py::MarketStructureProvider.get_snapshot` | call (one bounded provider read) | none in production; tests only |
| `core/aletheia/data/smc_enrichment.py::derive_fvg_zones` | call (optional FVG enrichment) | `MarketStructureProvider.get_snapshot` only (no production consumer) |

## Durable state

Market structure adds no table, cache or scheduler. Its facts persist only inside consumer
records:

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `candidate_planning_generations.planner_input_json` (`market_structure` section) | Entry Planner (frozen `EntryAnalysisContext`) | Compiler, requalification (`core/aletheia/storage/repositories.py` via `planning_structure_material`) | immutable per Planning Generation |
| `entry_trigger_activations.activation_json` (`snapshot.market_structure`) | `core/aletheia/storage/repositories.py` activation insert ← `fast_trigger_watch` | audit/inspection only | append-only |

## Requirements

### Observable result

#### MS-C01 Facts are known at their causal bar close

`The kernel shall mark a strict K=2 Swing as known exactly at the close of its second right-side confirmation bar, a BOS/CHOCH at the close of the breaking bar, and an FVG at the close of its third bar.`

- WHEN only the pivot's first right-side bar has closed THEN the Swing is absent; WHEN the second right-side bar closes THEN `known_at_ms` equals that close, for 1m and for any canonical non-1m duration.
- WHEN an FVG is later mitigated THEN `mitigated_at_ms` is the first eligible later bar close and the zone identity is unchanged.
- `known_at_ms` is causal market-data time, never proof of network receipt; `computed_at_ms` is provenance only and never enters fact identity.

Evidence: `core/aletheia/data/market_structure.py::derive_market_structure`, `core/aletheia/data/smc_enrichment.py::derive_fvg_zones`.

#### MS-C02 Historical reads exclude post-cutoff closes and are replay-stable

`When MarketStructureProvider.get_snapshot is called with as_of_ms, the provider shall admit only closed bars whose close time is at or before as_of_ms and shall return the same semantic facts and source fingerprint for an unchanged source prefix after later bars arrive.`

- WHEN a new bar arrives after the cutoff THEN Swings, events and `source_candle_fingerprint` are identical to the earlier read.
- WHEN the admitted tail ends one or more full bars before `as_of_ms` THEN the snapshot is unavailable with `historical_tail_gap`.
- Because the read window is 120 bars, this is not a durable historical event ledger.

Evidence: `core/aletheia/data/market_structure.py::MarketStructureProvider.get_snapshot`.

#### MS-C03 Latest reads need provider-closed and elapsed-close evidence

`When MarketStructureProvider.get_snapshot is called without as_of_ms, the provider shall admit a bar only if Candle.is_closed is true and Candle.timestamp + timeframe_ms(timeframe) <= computed_at_ms.`

- WHEN a provider flags a bar closed whose close time is still in the future THEN that bar is excluded.

Evidence: `core/aletheia/data/market_structure.py::MarketStructureProvider.get_snapshot`.

### Invariants and trust boundaries

#### MS-C04 Only an exact continuous venue series is admitted

`If any admitted bar is invalid, non-finite, economically impossible, duplicated, out of order, discontinuous at timeframe_ms(), or from a venue other than the requested instrument's, then the kernel or provider shall return an explicit unavailable result with a reason and never interpolate, reorder, deduplicate or substitute another venue.`

- WHEN two bars are one interval apart plus one millisecond THEN the result is unavailable with `candle_gap`.
- WHEN a candle has no exchange identity THEN the provider returns `source_venue_unproven`; a different exchange returns `source_venue_mismatch`.

Evidence: `core/aletheia/data/market_structure.py::_series_error`, `MarketStructureProvider.get_snapshot`.

#### MS-C05 History quality is bounded by bar count

`The kernel shall report fewer than 30 valid bars as unavailable (insufficient_closed_bars), 30 through 119 bars as partial history and 120 bars as complete history, and shall treat a valid empty event or FVG tuple as available.`

- WHEN 30 continuous closed bars are supplied THEN the snapshot is available with `history_quality="partial"`.

Evidence: `core/aletheia/data/market_structure.py` (`MIN_BARS`, `WINDOW_BARS`, `derive_market_structure`).

#### MS-C06 Structure state machine

`The kernel shall confirm Swings by independent strict symmetric K=2 tests, derive state only from confirmed Swings, let the newest same-kind Swing replace the active break level, consume each active level on its first qualifying cross, and classify a bar's BOS/CHOCH against the structure state as of the immediately preceding bar.`

- WHEN a Swing first becomes confirmed on the same bar that breaks an active level THEN it does not retroactively change that bar's break classification.

Evidence: `core/aletheia/data/market_structure.py::derive_market_structure`, `_apply_break`, `_structure_state`.

#### MS-C07 Break tolerance uses arithmetic ATR14

`The kernel shall compute ATR14 as the arithmetic mean of the latest 14 True Range values (not the shared Wilder ATR) and shall count a cross only when price exceeds the active level by more than 0.05 × ATR14.`

- WHEN a close lies exactly at the `0.05 * ATR14` boundary THEN no break is recorded.

Evidence: `core/aletheia/data/market_structure.py::_atr14_at`, `_apply_break` (`BREAK_TOLERANCE_ATR`).

#### MS-C08 Fact identity is a content hash

`The kernel shall derive fact IDs and fingerprints as full lowercase SHA-256 over canonical JSON, so source identity changes the source fingerprint while provider wrapping and calculation time change neither geometry nor fact identity.`

Evidence: `core/aletheia/data/market_structure.py::_sha256`, `_swing_id`, `_event_id`.

#### MS-C09 FVG is optional and isolated

`Where FVG enrichment is requested, the provider shall compute it after valid core derivation and, if it fails, keep the core snapshot available with FVG explicitly unavailable; FVG shall add no pandas/numba/SMC dependency and never enter the Fast Trigger or Entry Planner path.`

Evidence: `core/aletheia/data/market_structure.py::MarketStructureProvider.get_snapshot` (`enrichments`), `core/aletheia/trading/entry_analysis.py::entry_causal_windows` (`fvg_algorithm_version: None`).

#### MS-C10 Market structure holds no gate authority

`The hard-gate seam shall fail closed for every algorithm version while HARD_GATE_ALLOWLIST is empty, so market structure alone cannot block or approve Opportunity materialization, Risk approval or execution.`

- WHEN `hard_gate_decision` receives a `market-structure/v2` assessment THEN it returns `(False, "price_action_gate_unqualified:market-structure/v2")`.

Evidence: `core/aletheia/universe/market_structure.py::HARD_GATE_ALLOWLIST`, `hard_gate_decision`.

### Entry Planner consumption

#### MS-C11 Planner windows are admitted causally before structure is derived

`When build_entry_analysis_context assembles an Entry Planner context, entry_causal_windows shall derive market structure for each of the 4h/1h/15m stored windows (35/50/96 bars) only if the window has exactly that many bars, every bar closed at or before the analysis cutoff, consecutive at the timeframe interval, on the asset's venue and market type, from one provider, and persisted and last mutated at or before the cutoff; otherwise that timeframe's structure is unavailable with no facts.`

- WHEN one 15m bar's `last_mutated_at` is after the cutoff THEN `market_structure["15m"]["availability"] == "unavailable"` and its `fact_refs` is empty.
- WHEN a window is admitted THEN its section carries state, ATR14, Swings, events, geometry fingerprint and provenance with `history_quality="complete"`, and every fact id joins the context's citable `fact_refs`.

Evidence: `core/aletheia/trading/entry_analysis.py::entry_causal_windows` (`ENTRY_WINDOW_BARS`).

#### MS-C12 Structure facts become Compiler anchors

`When an admitted timeframe has available structure, structural_anchors shall turn its confirmed Swings into confirmed_swing anchors and its BOS/CHOCH broken levels into broken_structure_level anchors, granting an invalidation role only to a level on the correct side of every reference price that no later closed bar of its timeframe has crossed.`

- WHEN a long's swing low has been closed below since it became known THEN it is dropped; a crossed broken-structure level keeps only `entry_candidate`.
- WHEN same-class levels lie within the merge width (× ATR14) THEN they merge into one band anchor.
- WHEN the Compiler runs THEN it requires a finite positive 15m `atr14` (else `ATR_UNAVAILABLE`) and places the stop 0.05 × ATR14 beyond the invalidation anchor (Entry Plan Contract).

Evidence: `core/aletheia/trading/entry_analysis.py::structural_anchors`, `core/aletheia/trading/entry_plan_compiler.py` (`_GEOMETRY["fallback_tolerance_atr"]`).

#### MS-C13 Hypothesis fails when no profile timeframe has structure

`If a hypothesis's entry profile requires market_structure and none of the profile's supported timeframes has both an available candle window and available structure, then hypothesis validation shall reject it with hypothesis_profile_market_structure_unavailable.`

- WHEN all three windows are unavailable THEN no Entry Plan is compiled and the reason is `hypothesis_profile_market_structure_unavailable`.

Evidence: `core/aletheia/trading/entry_analysis.py` (hypothesis validation loop over `profile.required_fact_sections`), `core/aletheia/trading/entry_profiles.py::_REGISTRY`.

#### MS-C14 Only new 15m structure requalifies a thesis

`The planning requalification material shall consist only of the latest confirmed 15m Swing high, Swing low and break event, so rolling-window hashes, ATR and disappearing older facts are not new material.`

- WHEN the 15m window rolls forward without a new confirmed fact THEN the material fingerprint is unchanged.

Evidence: `core/aletheia/trading/entry_analysis.py::planning_structure_material`, used by `core/aletheia/storage/repositories.py` requalification.

### Fast Trigger projection

#### MS-C15 The 1m projection is shadow evidence

`When the Fast Trigger reaches triggered, it shall evaluate the same kernel over its in-memory closed 1m bars (prewarmed from at most 120 local SQLite bars, never fetched remotely) as a market-structure/v2 projection over causal-market-structure/v1, and carry the result in TriggerReady.market_structure without any gate reading it.`

- WHEN the latest event is a CHOCH opposed to the plan direction THEN the projection verdict is `block`, and the trigger still proceeds.
- WHEN the final bar's event was already reported THEN it is not repeated.
- Historical `market-structure/v1` assessments are not rewritten.

Evidence: `core/aletheia/trading/momentum_trigger.py`, `core/aletheia/agent_runtime/fast_trigger_watch.py::_prewarm_market_structure`, `core/aletheia/universe/market_structure.py::evaluate_market_structure`.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| Caller misuse (no `InstrumentId`, unknown timeframe, invalid `as_of_ms`, unknown enrichment) | raise one stable `ValueError` | `market_structure_*` message | not a snapshot |
| Provider/network error, empty, invalid, discontinuous, insufficient, historical tail gap, unproven venue | explicit unavailable snapshot with provenance | `unavailable_reason` token | unavailable, never an empty success |
| Cancellation | propagate `CancelledError`; injected provider stays caller-owned | — | — |
| FVG failure after valid core | core available, FVG unavailable | `fvg_unavailable_reason` | core facts unaffected |
| Planner window not causally admitted | timeframe section unavailable | `availability="unavailable"`, empty `fact_refs` | no anchors, never zero-price anchors |
| No supported timeframe has structure | hypothesis rejected | `hypothesis_profile_market_structure_unavailable` | no Plan |

## Authority

Market structure may only produce facts and anchors; it may never admit a Candidate, change
Risk limits, approve or submit an order, or unlock Live.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| Scope (one dependency-light kernel) | `tests/specs/spec_market_structure.py::MS-S01` |
| MS-C01 | `tests/specs/spec_market_structure.py::MS-C01`; `tests/test_market_structure.py` |
| MS-C02 | `tests/specs/spec_market_structure.py::MS-C02` |
| MS-C03 | `tests/specs/spec_market_structure.py::MS-C03` |
| MS-C04 – MS-C09 | `tests/test_market_structure.py` |
| MS-C10, MS-C15 | `tests/test_universe_fast_trigger.py` |
| MS-C11 | `tests/test_market_fact_boundary.py`; `tests/test_universe_features.py` |
| MS-C12 – MS-C14 | `tests/test_entry_analysis.py` |

### Known conformance gaps

- The previous text said the Fast Trigger "persists the existing `check_type="market_structure"`,
  `authority="shadow"` assessment". No production code writes
  `opportunity_entry_validity_assessments` (the table exists only in
  `core/aletheia/storage/migrations.py`); the projection is persisted only inside
  `entry_trigger_activations.activation_json` (`core/aletheia/storage/repositories.py`, activation insert).
- `MarketStructureProvider.get_snapshot` and FVG enrichment have no production caller; both are
  reachable only from tests.
- `core/aletheia/trading/entry_plan_compiler.py` reads `structure["break_tolerance"]`, which
  `entry_causal_windows` never writes; the Compiler's own `fallback_tolerance_atr = 0.05` constant
  supplies the same value as `BREAK_TOLERANCE_ATR`, so two constants carry one rule.

## Decision references

- Entry anchor registry: #3850 item 5 (`core/aletheia/trading/entry_analysis.py::structural_anchors`).
- The learning loop's judgment label replays Compiler geometry built on these anchors
  ([spec 0001](../specs/0001-learning-loop/spec.md) LL-R02); determinism of MS-C01/C06/C08 is its
  upstream premise.
