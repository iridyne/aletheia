# Technical Analysis Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `TA` · **Owner code:** `core/aletheia/trading/technical_analysis.py`

## Scope

Aletheia computes versioned, point-in-time technical facts (SMA/EMA, Wilder RSI/ATR,
Bollinger position and width, momentum, volume statistics, rolling VWAP, Donchian levels,
normalized OBV) from closed OHLCV bars. On the trading mainline, Universe feature collection
computes them for 15m/1h/4h and persists their version and input identity with each feature
snapshot; the Entry Planner computes its own technical section from its causally admitted
windows and carries the feature snapshot's technical identity into its frozen input. Technical
analysis has no approval authority: it does not change scanner thresholds, Risk policy,
execution routing or Live authority. OHLCV hot/cold tiering and the Market Observer
`technical-context-v1` renderer are not current behavior
([history](../history/technical_analysis_retired_sections.md)).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/trading/technical_snapshot_service.py::TechnicalSnapshotService.snapshot_from_bars` | call (process-wide reuse boundary) | `core/aletheia/universe/features.py::_fetch_single_feature` (15m/1h/4h) |
| `core/aletheia/trading/technical_analysis.py::IncrementalTechnicalAnalyzer.update` | call (append-fast path) | `TechnicalSnapshotService` |
| `core/aletheia/trading/technical_analysis.py::BatchTechnicalAnalyzer.compute` | call (independent numerical reference) | `core/aletheia/trading/entry_analysis.py::technical_section` |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `universe_feature_snapshots.technical_algorithm_version`, `.technical_snapshot_manifest` | `core/aletheia/universe/features.py::_fetch_single_feature` | `core/aletheia/trading/entry_analysis.py::build_entry_analysis_context` (`quant_context`), `core/aletheia/portfolio/position_manager.py::_get_atr_observation` (1h manifest) | append-only rows (change-suppressed) |
| in-process series state (`TechnicalSnapshotService`) | `snapshot_from_bars` | same | replaceable, LRU-bounded |

The manifest records per timeframe: last closed-bar timestamp (`bar_close_ts`), bars used,
recovery history retained, warm-up state, SHA-256 input fingerprint, data quality and quality reasons.

## Requirements

#### TA-C01 One snapshot shape, two paths

`The batch and incremental analyzers shall expose one TechnicalSnapshot contract, and the batch path shall remain available for independent reconciliation of the incremental path.`

- WHEN `IncrementalTechnicalAnalyzer.verify()` compares with the batch result over the same bars THEN the values agree.

Evidence: `core/aletheia/trading/technical_analysis.py::BatchTechnicalAnalyzer`, `IncrementalTechnicalAnalyzer`.

#### TA-C02 Appends are incremental and windows are reused

`When snapshot_from_bars receives bars for an exact series, the service shall return the cached state for an identical window, extend the state with only the new suffix for a grown or rolled fixed-size window, and rebuild bounded state only otherwise.`

- WHEN a provider returns the same 200-bar window shifted by one closed bar THEN exactly one incremental update is counted and no rebuild.

Evidence: `core/aletheia/trading/technical_snapshot_service.py::TechnicalSnapshotService.snapshot_from_bars`.

#### TA-C03 Forming bars never alter confirmed facts

`If a bar is not closed, then the analyzers shall exclude it and record forming_bars_excluded or forming_bar_ignored rather than change confirmed technical facts.`

Evidence: `core/aletheia/trading/technical_analysis.py::_closed_canonical_bars`, `IncrementalTechnicalAnalyzer.update`.

#### TA-C04 Series identity is exact

`The service shall key technical state by venue, canonical symbol, market type and timeframe, so spot and derivative series never alias.`

Evidence: `core/aletheia/trading/technical_snapshot_service.py::TechnicalSeriesKey`.

#### TA-C05 bar_close_ts is the interval end

`The snapshot shall set bar_close_ts to the last closed bar's open timestamp plus the timeframe duration.`

Evidence: `core/aletheia/trading/technical_analysis.py::BatchTechnicalAnalyzer.compute`, `IncrementalTechnicalAnalyzer.snapshot`.

#### TA-C06 Gaps are degraded, never continuous

`If consecutive closed bars are more than one interval (plus a tolerance of max(1 s, 10% of the interval)) apart, then the snapshot shall report data_quality degraded with timeframe_gap_detected, and the degraded state shall clear once the gap leaves the bounded retained window.`

Evidence: `core/aletheia/trading/technical_analysis.py::_gap_reasons`, `IncrementalTechnicalAnalyzer._prune_retired_gaps`.

#### TA-C07 Revisions trigger a bounded rebuild

`If a duplicate, revised or out-of-order closed bar arrives, then the incremental analyzer shall rebuild its bounded recovery window and expose bounded_rebuild_after_revision in quality_reasons.`

Evidence: `core/aletheia/trading/technical_analysis.py::IncrementalTechnicalAnalyzer.update`, `_rebuild`.

#### TA-C08 Insufficient history is unavailable, not zero

`If history is insufficient for an indicator, then the snapshot shall report that indicator as None and set warmup_complete=false below 50 bars.`

Evidence: `core/aletheia/trading/technical_analysis.py` (`warmup_complete=len(canonical) >= 50`).

#### TA-C09 Feature snapshots carry technical identity

`When Universe feature collection persists a feature snapshot, it shall store the 1h algorithm version and a per-timeframe manifest of closed-bar time, bar counts, warm-up, input fingerprint and quality.`

- WHEN the Entry Planner builds its context THEN `quant_context` carries `technical_algorithm_version` and the manifest hash, and the frozen planner input preserves them.

Evidence: `core/aletheia/universe/features.py::_fetch_single_feature`, `core/aletheia/trading/entry_analysis.py::build_entry_analysis_context`.

#### TA-C10 Planner technical values exclude non-readings

`When the Entry Planner computes a technical section for an admitted window, it shall omit non-finite values and the analyzer's zero-width-band and zero-volume fallbacks, listing them as unavailable fields instead.`

- WHEN every bar has zero volume THEN `obv_normalized` is in `unavailable_fields`, not `0.0`.
- WHEN the window is not admitted THEN the section has no values and no fact refs.

Evidence: `core/aletheia/trading/entry_analysis.py::technical_section`.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| Invalid or non-finite OHLCV | rejected | exception / dropped bar | never a value |
| Insufficient history | indicator `None`, `warmup_complete=false` | manifest `warmup_complete` | `None`, never `0` |
| Gap in series | `degraded` | `timeframe_gap_detected` | not continuous |
| Revision / duplicate | bounded rebuild | `bounded_rebuild_after_revision` | — |
| Forming bar | excluded | `forming_bars_excluded` / `forming_bar_ignored` | — |

## Authority

Produces facts only; may never change scanner thresholds, Risk limits, execution routing,
Gateway submission or Live unlock.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| TA-C01, TA-C03, TA-C05 – TA-C08 | `tests/test_technical_analysis.py` |
| TA-C02, TA-C04 | `tests/test_technical_snapshot_service.py` |
| TA-C09 | `tests/test_vergex_utilization_completion.py`; `tests/test_universe_features.py` |
| TA-C10 | `tests/test_entry_analysis.py` |
| Performance | `scripts/analysis/benchmark_technical_analysis.py` |

### Known conformance gaps

- `core/aletheia/trading/market_context.py::build_market_context` (the `technical-context-v1`
  renderer) and `core/aletheia/storage/repositories.py::plan_market_ohlcv_tiering` /
  `core/aletheia/storage/market_ohlcv_tiering.py::OHLCVTierManager` have no production caller; they
  are kept alive only by `tests/test_market_context.py` and `tests/test_market_ohlcv_tiering.py`.
  `core/aletheia/analytics/position_replay.py` imports `_canonical_row` and `_content_hash` from the
  tiering module, so deleting it requires moving those helpers first.
- Technical version or input changes are not planning requalification material; only 15m market
  structure is ([Market Structure Contract](market_structure_contract.md) MS-C14).

## Decision references

- Market Observer retirement: [`retire_market_observer.md`](../proposals/retire_market_observer.md).
- The owner deferred the cold-storage format choice (SQLite BLOB vs external files); see
  [history](../history/technical_analysis_retired_sections.md#known-gaps-tiering).
