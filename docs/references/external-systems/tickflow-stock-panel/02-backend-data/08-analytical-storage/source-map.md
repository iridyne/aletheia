# Domain 8 Source Map

## Reference Snapshot

- Repository: `/Users/yixian/Projects/references/tickflow-stock-panel`
- Git snapshot: `99bdec875d4bbf5c30ddc43534b81ada0f3b0f6b`
- Primary storage composition: Parquet + Polars + in-memory DuckDB
- Primary backtest composition: projected Arrow dataset scan + NumPy matrix +
  disk-backed memory mapping

## TickFlow Storage Entry and Views

### `backend/app/tickflow/repository.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 50-152 | `DataStore` | Creates dataset directories, performs desktop legacy-path migration, opens an in-memory DuckDB connection, then registers file-backed views. |
| 154-214 | `_register_views` | Mounts Parquet globs with `read_parquet(..., union_by_name=true)`; missing datasets are logged at debug level and omitted. |
| 216-295 | `_register_unified_views` | Builds stock/index/ETF convenience unions and injects `asset_type` plus a hard-coded `source='tickflow'`. |
| 298-367 | `KlineRepository.__init__` | Owns the shared DuckDB connection locks, write lock, Polars caches, memoized symbol sets, history cache, and warm-up state. |
| 355-367 | `execute_all`, `execute_one` | Serializes use of the single DuckDB connection because it is not treated as thread-safe. |
| 369-468 | `refresh_cache` and warm-up path | Refreshes dimensions synchronously, optionally computes the expensive enriched cache in a daemon thread, and exposes completion callbacks. |
| 469 onward | `clear_cache` and refresh helpers | Invalidates a family of related caches and rebuilds selected layers. |
| 980-1120 | latest enriched/live aggregate paths | Reads a narrow persisted enriched model, recomputes indicators, and maintains the latest-day and live aggregate read models. |
| 1121-1180 | `get_enriched_history`, `get_enriched_range` | Reuses a full enriched-history cache, verifies requested coverage, and slices by actual trading dates rather than calendar-day subtraction. |
| 1182-1226 | `get_live_agg` | Uses a cheap date fast path and only scans for the latest disk date after a day boundary. |
| 1227-1240 | `get_historical_shares` | Invalidates a financial share-history cache using the Parquet file's nanosecond mtime. |
| 1430-1506 | minute-range query | Uses lazy scan, column projection, symbol/date predicates, sorting, and streaming collection. |
| 1507-1550 | `get_minute_by_dates` | Resolves exact `date=.../part.parquet` files for sparse trigger dates instead of scanning a wide interval. |
| 1680-1789 | read/merge helpers | Combines cached and scanned frames, aligns common columns, and casts mismatched types before concatenation. |
| 1791-1815 | matrix generation | Reads a persistent UUID generation or atomically writes a new generation file. |
| 1817-1840 | `symbols_lagging` | Computes latest date per symbol so a healthy global max cannot hide lagging instruments. |
| 1930-2009 | view rebuild paths | Re-registers views after data appears or changes. |
| 2012-2041 | `_atomic_write_parquet`, `_write_daily_partition` | Writes a temporary Parquet file, atomically replaces the partition, and bumps matrix generation for managed enriched datasets. |

## TickFlow Derived Data and Indicators

### `backend/app/indicators/pipeline.py`

This module is the computational authority used to expand narrow enriched
storage into indicator and signal columns. Its importance to storage is that
many reads are not merely file reads: they are file projection followed by a
version-sensitive recomputation.

### `backend/app/parquet.py`

Contains general Parquet helpers used by data pipelines. Domain 9 examines the
exact write and replacement semantics; this domain treats its outputs as inputs
to analytical read models.

### `backend/app/services/screener.py`

Consumes latest enriched and instrument read models for interactive filtering.
It demonstrates why the repository precomputes a roughly one-row-per-instrument
hot frame rather than scanning full history per request.

## TickFlow Backtest Panel and Matrix

### `backend/app/backtest/engine.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 187-275 | `PanelCache` | Process-shared LRU + TTL cache, size 2 and TTL 180 seconds by default, protected by a lock. |
| 215-267 | `get_or_compute` | Implements single-flight: one leader scans while followers wait and reuse the result; computation remains outside the global lock. |
| 269 onward | cache statistics | Records actual compute count/time, ordinary hits, and follower reuse. |
| 400-475 | direct matrix load | Selects enriched Parquet root, fields, instruments, disk-cache budget, profile generation, and source generation before invoking the matrix loader. |
| 475 onward | panel load | Prefers the repository's recent enriched-history cache when coverage permits, otherwise uses a file scan path. |

### `backend/app/backtest/matrix.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 16-68 | context-local cache state | Uses `ContextVar` so deterministic operation caching belongs to the active backtest job/matrix rather than leaking across unrelated jobs. |
| 99-360 | `MatrixComputeCache` | Byte-bounded LRU; defaults to 512 MiB total and 256 MiB per item; records calls, hits, misses, evictions, skipped items, and fingerprint cost. |
| 680-833 | `load_market_data_matrix_from_parquet` | Validates range/schema, projects fields, derives partition coverage, finds a covering cache, builds when absent, prunes disk cache, then slices/projects the result. |
| 1035-1200 | cache build/publish | Builds into a temporary directory and binary file, flushes/closes mappings, writes a manifest, and atomically publishes the directory. |
| 1160-1178 | manifest | Stores loader version, root, coverage, requested fields/symbols, partition fingerprints, instrument fingerprint, profile/source generations, timings, axes, and array layouts. |
| 1239-1300 | memory-map helpers | Opens NumPy memory maps and explicitly flushes/closes them. |
| 1470-1555 | cache-key construction | Includes source scope, requested coverage/fields/symbols, instruments, profile generation, and source generation/partitions. |
| 1571-1620 | covering-cache validation | Requires compatible version/root/symbol request/instrument fingerprint/coverage/field superset and source evidence. |
| 1660-1735 | cache load | Reopens stored vectors and matrices through read-only mappings and reconstructs a `MarketDataMatrix`. |
| 1798-1845 | disk pruning | Applies byte budget and preferentially removes obsolete whole-universe generations. |

## Aletheia Analytical Inputs

### `aletheia/storage/migrations.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 552-565 | `market_ohlcv` schema | Exact composite primary key: exchange, symbol, market type, timeframe, timestamp; source provider remains a stored column. |
| 2307-2368 | identity migration | Rebuilds legacy symbol-only history behind a savepoint and quarantines ambiguous source as `legacy_unknown`. |
| 2606-2607 | timeframe/time index | Supports time-window access across OHLCV rows. |

### `aletheia/storage/repositories.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 4215-4287 | `upsert_ohlcv_candles` | Batch upserts exact-instrument closed bars and prunes each series to a bounded rolling window. |
| 4289-4310 | `_resolve_ohlcv_scope` | Provides a compatibility path for partially scoped readers by selecting one coherent identity; explicitly scoped access remains preferable. |
| 4311-4342 | coverage/newest queries | Computes bounded historical coverage for one instrument/timeframe. |
| 4344-4400 | durable backfill state | Persists exact-scope status, count, failures, attempt timestamps, retry time, and a safe error fact. |
| 4447-4480 | `get_ohlcv_candles` | Indexed newest-first query reversed to chronological order; supports a pagination boundary. |
| 7258-7273 | feature snapshot read | Joins stored feature observations to the instrument registry rather than guessing identity from a symbol. |

### `aletheia/data/cache.py`

- `OHLCVCache` is a process-global, thread-safe, monotonic-time TTL cache.
- Key: exchange, symbol, market type, timeframe, and requested limit.
- It has no size bound, per-key single-flight, source generation, or explicit
  negative/error entry.

### `aletheia/data/ccxt_provider.py`

- `get_ohlcv` checks the TTL cache when no `since` boundary is supplied.
- Network results are converted to candles, the trailing open bar is marked,
  confirmed bars are persisted on a worker thread, and the returned set is
  cached.
- Static inspection shows a fallback venue can produce the data while the cache
  key still uses the configured primary venue. Domain 7 records the provenance
  problem; Domain 8 treats it as a cache-identity hazard.

### `aletheia/universe/features.py`

- `_fetch_single_feature` fetches three timeframes, calculates indicators in
  process, combines fresh durable enrichment, and constructs a durable
  `UniverseFeatureSnapshot`.
- Change suppression avoids near-duplicate writes for 30 minutes and forces a
  heartbeat by 60 minutes, making the snapshot table an evidence timeline
  rather than a high-frequency columnar feature store.

### `aletheia/data/context_builder.py`

Builds a typed `MarketContextSnapshot` from bounded inputs. This is a semantic
decision snapshot, not a bulk reusable market panel.

### `aletheia/analytics/` and `aletheia/server/routes/analytics.py`

The analytics family derives trade, outcome, attribution, funnel, calibration,
and policy evidence from normalized SQLite records. These are causally valuable
and frequently non-reconstructible; they should not be classified with
replaceable market-data scan caches.

## Configuration and Runtime Evidence Not Available

- Actual Parquet row counts, compression ratios, query latencies, cache hit
  ratios, disk-cache sizes, and memory use: `Not available` from static analysis.
- Whether every TickFlow data mutation passes through the managed generation
  bump: not proved globally.
- Whether hard-coded unified-view source values currently disagree with a real
  user's configured provider: possible from the source path, but live occurrence
  is `Not available`.
- Aletheia production database size, query contention, and historical scan
  latency: `Not available` without runtime measurement.
