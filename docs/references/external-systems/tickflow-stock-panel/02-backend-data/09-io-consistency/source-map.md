# Domain 9 Source Map

## Reference Snapshot

- Repository: `/Users/yixian/Projects/references/tickflow-stock-panel`
- Git snapshot: `99bdec875d4bbf5c30ddc43534b81ada0f3b0f6b`
- Investigation type: static I/O and consistency trace

## TickFlow Market Publication

### `backend/app/services/kline_sync.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 28-39 | `_atomic_write_parquet` | Writes `part.parquet.tmp` and replaces the target in the same directory. |
| 48-88 | `_normalize_daily` | Casts permissively, removes halt rows, and selects available canonical columns. |
| 90-153 | `sync_daily_batch` | Continues after failed chunks and can report failed symbols through optional `failed_out`. |
| 155-229 | `sync_and_persist_daily_batch` | Calls `sync_daily_batch` without supplying `failed_out`, appends the returned successful subset, refreshes the view, and returns a row count. |
| 321-437 | adjustment-factor sync | Reads, merges, deduplicates, and atomically replaces a whole-market factor file. |
| 504-530 | `_write_minute_partition` | Publishes minute data per exact date partition using the atomic helper. |
| 614-731 | segmented minute fetch | Supports segment callbacks so large intervals can publish incrementally rather than accumulate all rows. |
| 934-991 | minute partition migration | Rewrites legacy symbol partitions into date partitions. |
| 994 onward | minute sync orchestration | Chooses range/segment behavior and streams each segment into partitions. |

### `backend/app/tickflow/repository.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 304-340 | repository locks | One lock serializes the shared DuckDB connection; `_write_lock` serializes repository Parquet read-modify-write inside the process. |
| 369-468 | cache refresh | Dimensions and derived frames refresh in a defined order; background warm-up adds a temporary stale/unavailable interval. |
| 469 onward | cache clear | Explicitly invalidates related in-memory frames before selected rebuilds. |
| 1791-1815 | matrix generation | Reads or atomically replaces a persistent random generation marker. |
| 1930-2000 | view rebuild | Re-registers physical and unified DuckDB views after publication. |
| 2001-2010 | `_atomic_write_parquet` | Temporary file plus same-directory replace for repository-owned files. |
| 2012-2037 | `_write_daily_partition` | Holds `_write_lock`, merges by symbol/date, replaces each date file, then bumps generation for selected enriched datasets. |
| 2041-2200 | live/enriched merge paths | Use the process-local write lock and atomic file replacement for repository-owned partition updates. |

### `backend/app/indicators/pipeline.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 1080-1160 | forward incremental writes | Computes new/affected rows and writes enriched date partitions directly with `write_parquet(out)`. |
| 1160-1255 | affected-symbol/full paths | Performs per-date read-filter-concat-write directly, outside `KlineRepository._write_lock`. |
| 1260-1300 | full rebuild publication | Refuses to overwrite when rebuilt dates omit an existing date, then overwrites each rebuilt date directly. |

These direct writes do not use the repository's atomic helper and do not visibly
bump its matrix generation in this code path.

## TickFlow Dimension and Financial Publication

### `backend/app/services/instrument_sync.py`

- Fetches SH, SZ, and BJ independently and continues after exchange failures.
- If at least one exchange returns rows, writes the assembled frame directly to
  `instruments.parquet`, replacing the prior full dimension.
- Quote name enrichment performs an in-place read-modify-write of the same file.
- No atomic helper or cross-process/process-shared publication lock is visible.

### `backend/app/services/financial_sync.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 55-125 | `_fetch_table` | Continues after batch exceptions and returns all successful records without a completeness envelope. |
| 128-139 | `_write_table` | Directly overwrites the single table Parquet file when the frame is non-empty. |
| 157-193 | share-history merge | Merges existing, missing-history, and latest frames, then overwrites the file. |
| 226-244 | `sync_all` | Publishes tables one by one; no dataset-wide transaction or manifest. |
| 285-506 | `FinancialScheduler` | Process-local mutual exclusion, persisted per-table last-sync times, and background daemon thread. |
| 408-438 | `_run_body` | Records per-table sync time after each function returns, including zero-row/partial outcomes that did not raise. |

### `backend/app/services/preferences.py`

- `load` reads `preferences.json` or returns `{}` on malformed content.
- `save` performs an unlocked read-update-direct-write cycle.
- Several setters repeat their own read-modify-direct-write sequence.
- There is no visible temporary-file replacement, file lock, schema version, or
  compare-and-swap generation.

## TickFlow Job and Operational Files

### `backend/app/services/pipeline_jobs.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 49-106 | terminal job files | Active jobs live in memory; terminal jobs are direct-written as one JSON file each and malformed files are skipped. |
| 109-143 | `create` | Treats pending and running as one active single-flight state. |
| 145-210 | start/succeed/fail/progress | Protects active dictionaries with a process-local lock; persists only terminal state. |
| 241-280 | `reap_stale` | Marks timed-out work failed and explicitly releases the heavy-run lock even though the executor thread cannot be interrupted. |
| 328-355 | heavy-run lock | Comments state the lock should follow the actual executor lifetime and block a new job while a zombie still runs. |

The explicit release in `reap_stale` conflicts with the later ownership
comment and permits a new task to overlap an uninterruptible old thread.

### `backend/app/jobs/daily_pipeline.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 108-125 | pipeline setup | Collects selected soft failures into `stage_errors`. |
| 129-240 | instruments and daily bars | Publishes instruments before resolving the universe; daily sync reports progress using requested range/row counts. |
| 245-360 | factors and enriched data | Selects full/incremental recomputation based on directory/date observations. |
| 380-485 | index/ETF stage | Catches the stage exception, records a soft failure, and continues. |
| 520-576 | completion | Refreshes views, emits `done`/100, builds result, then raises when `stage_errors` is non-empty. |
| 629-665 | tracked scheduled run | Converts an exception into a failed terminal job. |

### `backend/app/services/alert_store.py`

- Appends JSON lines under a process-local lock.
- Every twentieth write prunes by reading and rewriting the active file in place.
- deletion and clear also rewrite/truncate in place.
- malformed lines are silently skipped during reads and rewrites.
- no cross-process lock, temporary replacement, stable event ID, checksum, or
  repair protocol is visible.

### Other I/O patterns

- `services/depth_service.py` publishes depth snapshots with a temporary file
  and `os.replace`.
- `services/json_report_store.py` and `services/strategy_cache.py` use temporary
  JSON plus replacement under process-local locks.
- `services/backtest.py` writes each result Parquet directly.
- `services/pipeline_jobs.py`, strategy/config/preferences/auth/secrets, several
  user rules, and extension files include direct `write_text` or direct Parquet
  paths; atomicity is not centralized.

## Aletheia SQLite Authority

### `aletheia/storage/sqlite.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 38-160 | writer permit | Reentrant per-thread ownership over a process lock plus OS file lock; six-second admission timeout. |
| 270-299 | `_open_connection` | Per-thread SQLite connections, WAL, foreign keys, `synchronous=NORMAL`, 30-second busy timeout, auto-checkpoint, and WAL size limit. |
| 310-483 | `_SQLiteWriterLane` | Bounded queue (256), one dedicated writer connection, definitive future result, transaction/rollback, and queue/lock/transaction telemetry. |
| 503-532 | `run_write` | Runs short database-only closures atomically; joins an existing transaction or submits to the writer lane. |
| 543-579 | `execute_write`, `execute_many_write` | Routes mutations and homogeneous batches through the writer lane. |
| 637-658 | `get_db` | Uses true autocommit outside explicit transactions to prevent abandoned daemon threads retaining implicit transactions. |
| 672 onward | rollback/reset helpers | Rolls back leaked transactions on the current reusable worker thread and resets connections safely. |

### `aletheia/storage/repositories.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 157-214 | `transaction` | Supports savepoints, process/cross-process writer permit, normal or immediate begin, commit/rollback, and slow-transaction telemetry. |
| 217-250 | runtime/audit primitives | Persists lifecycle and structured audit records in SQLite. |
| 4215-4287 | OHLCV publication | Batch upsert plus bounded prune inside exact instrument scope. |
| 4344-4400 | backfill state | Durable status/retry facts use exact identity and canonical transaction pattern. |

### `aletheia/files/jsonl.py`

- General JSONL append is append-only with best-effort rotation.
- Direct generic writes to `audit.jsonl` are forbidden.
- Audit writes use process and cross-process locks, re-read the latest chain tail,
  include a previous-line hash, and can repair a provable incomplete final line.
- Deduplicated batch writes scan stable IDs and support retry from a durable
  database outbox.

### `aletheia/learning/lifecycle.py` and `aletheia/learning/ledger.py`

- The authoritative lifecycle row stores mirror status.
- JSONL append uses a stable audit ID.
- pending/failed mirrors can be selected and reconciled without replaying the
  business transition.
- mirror backlog is surfaced as an operational fact.

### `aletheia/storage/event_store.py`

- Commits an event to SQLite, then appends a monthly JSONL mirror.
- An idempotency-key conflict returns the existing database event and skips the
  JSONL append.
- A crash after the database commit but before JSONL append can therefore leave
  a missing mirror without an automatic outbox retry in this class.

### `aletheia/config/loader.py`

- Validates through the typed config model before save.
- Uses a thread lock, same-directory temporary file, and `os.replace`.
- It does not visibly fsync the file/directory in `save_config`; atomic visibility
  and power-loss durability are different guarantees.

### `aletheia/runtime_home_migration.py`

- Refuses silent empty-state startup when legacy primary state exists without a
  completion marker.
- Requires stopped runtime processes, WAL checkpoint, SQLite integrity check,
  staged copy, byte size and SHA-256 verification, typed config validation, and
  audit-chain verification.
- Publishes staged children with replacement and writes an fsynced completion
  marker last; the source remains preserved.

## Runtime Evidence Not Available

- Actual occurrence of power-loss torn writes, stale views, overlapping zombie
  pipelines, partial universe overwrite, or preferences corruption: `Not available`.
- Filesystem guarantees for the user's production volume/filesystem: `Not available`.
- Whether external processes ever write TickFlow data directories concurrently:
  `Not available`.
- Whether every Aletheia mutation is currently routed through the serialized
  writer path: not globally proved by this bounded trace.
- Full durability effect of SQLite `synchronous=NORMAL` on each target platform:
  not runtime-validated here.
