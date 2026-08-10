# Technical Analysis and OHLCV Tiering Contract

## Purpose and current scope

Aletheia computes versioned, point-in-time technical facts from closed OHLCV
bars. The technical-analysis layer has observation authority only: it does not
change scanner thresholds, Risk policy, execution routing, or live authority.

The current storage lifecycle keeps SQLite as the indexed hot authority and can
plan immutable cold chunks without choosing a cold physical format. Zstandard,
SQLite BLOB cold storage, and external cold files are not configured.

## Entry points

- `BatchTechnicalAnalyzer.compute()` is the independent numerical reference.
- `IncrementalTechnicalAnalyzer.update()` is the append-fast runtime path.
- `TechnicalSnapshotService.snapshot_from_bars()` is the process-wide reuse
  boundary for Market Observer and Universe feature collection.
- `repositories.plan_market_ohlcv_tiering()` creates a read-only plan for one
  exact `exchange + symbol + market_type + timeframe` series.
- `OHLCVTierManager.copy_to_cold()` coordinates a format-neutral cold backend
  when one is explicitly supplied.

## Durable state and consumers

`universe_feature_snapshots` persists `technical_algorithm_version` and a
`technical_snapshot_manifest`. The manifest records, per timeframe:

- last closed-bar timestamp;
- total bars used and recovery history retained;
- warm-up state;
- SHA-256 input fingerprint;
- data quality and quality reasons.

Market Observer consumes the shared snapshot directly and renders one bounded
`technical-context-v1` block. The block preserves exact instrument identity,
algorithm version and a bundle input fingerprint in its linked Harness artifact;
model-visible rows contain normalized trend, momentum, volatility, location and
participation facts. Insufficient-warmup or degraded timeframes expose only an
unavailable quality state, not partial indicator values. Bundle quality is
measured against every configured timeframe, and the venue comes from returned
candles; inconsistent venues fail closed. Universe scanners use
projected RSI/ATR/Bollinger/volume facts. System Operator consumes the persisted
feature row and includes technical version/input identity in its material
decision fingerprint.

## Observable result

Normal closed-bar appends update SMA/EMA, Wilder RSI/ATR, Bollinger position and
width, momentum, volume statistics, rolling VWAP, Donchian levels, and normalized
OBV without re-running the batch analyzer. Identical provider windows are cache
hits. Fixed-size rolling provider windows append only their new suffix.

The batch and incremental paths expose one `TechnicalSnapshot` contract. The
batch path remains available for independent reconciliation.

The Market Observer does not send recent raw close arrays. Correlated raw
SMA/EMA levels are compressed into one EMA trend state and spread; ATR, VWAP and
Donchian location are normalized so the model does not need to calculate them.

## Invariants and trust boundaries

- Forming bars never alter confirmed technical facts.
- Exact instrument identity includes venue, canonical symbol, market type, and
  timeframe; spot and derivatives states never alias.
- Missing intervals are reported as degraded data, never silently interpreted
  as a continuous series. The degraded state clears after the gap leaves the
  bounded retained window.
- `bar_close_ts` is the interval end (`CCXT open timestamp + timeframe`), not
  the candle-open timestamp.
- Duplicate, revised, or out-of-order bars trigger a bounded rebuild and an
  explicit quality reason.
- Algorithm-version changes and 1h input-fingerprint changes alter the Operator
  material decision fingerprint.
- Cold planning never deletes SQLite rows.
- A cold delete cutoff is unavailable until every complete chunk has a durable
  matching receipt and passes read-back equality.
- No compression or cold storage format is implied by the tiering contract.

## Failure and recovery semantics

Invalid or non-finite OHLCV is rejected. Insufficient history yields `None` for
the affected indicator and `warmup_complete=false`. Incremental state retains a
bounded recovery window; a revision rebuilds that window and exposes the event
in `quality_reasons`.

A cold backend receipt mismatch or read-back mismatch raises and fails closed.
Deferred rows that do not fill a complete cold chunk remain in the hot authority.

## Executable evidence

- `tests/test_technical_analysis.py`
- `tests/test_technical_snapshot_service.py`
- `tests/test_market_ohlcv_tiering.py`
- `tests/test_market_context.py`
- `tests/test_vergex_utilization_completion.py`
- `scripts/benchmark_technical_analysis.py`

## Known gaps

The owner intentionally deferred selection of SQLite BLOB versus external cold
files. Until that decision is made and a production `ColdOHLCVBackend` is
configured, tiering remains read-only planning and verified coordination; the
SQLite hot authority is never pruned through this path.
