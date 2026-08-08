# TickFlow I/O and Consistency Analysis

## 1. Consistency Has Several Independent Layers

TickFlow contains many comments and helpers labelled “atomic,” but the system's
observable truth depends on more than whether one target file is replaced in a
single rename.

```text
row validity
  -> batch completeness
  -> one partition publication
  -> multi-partition dataset publication
  -> view/cache invalidation
  -> job/result status
  -> restart/recovery interpretation
```

Success at a lower layer does not imply success above it. A perfectly formed
Parquet file can contain only one successful provider chunk and replace a prior
complete dataset. Every finding below uses this layered model.

## 2. TickFlow Has a Good Single-File Publication Primitive

The repository and K-line sync helper write a `.tmp` neighbor and replace the
target in the same directory. The temporary suffix does not match Parquet globs,
so readers should see the old complete target or the new complete target rather
than a half-encoded file.

This is directly reusable for reconstructible local files. It protects against:

- process termination while encoding the temporary file;
- a reader opening the target during encode;
- Parquet globs consuming the temporary artifact;
- many ordinary same-filesystem rename races.

It does not by itself provide:

- fsync/power-loss durability;
- validation before publication;
- a transaction across several partitions;
- cross-process writer exclusion;
- completeness of the new frame;
- coordinated cache/generation publication.

The helper should be described as atomic file visibility, not atomic dataset
sync.

## 3. The Primitive Is Not Used Consistently

Some high-value paths bypass it:

- the indicator pipeline writes enriched date files directly;
- instrument sync directly overwrites the universe dimension;
- quote-name enrichment directly rewrites the same dimension;
- financial sync directly overwrites one whole table file;
- backtest results and several user/config files are direct writes;
- alert pruning and deletion rewrite JSONL in place;
- terminal job history writes JSON directly to the final path.

This creates a split guarantee: a reader cannot know a file's crash behavior
from its dataset family alone. It must know which writer produced the current
version.

Centralizing publication is more valuable than adding more isolated atomic
helpers. The publisher should validate, lock, replace, update generation, and
emit a manifest/status from one path.

## 4. Repository Locking Does Not Cover the Indicator Pipeline

`KlineRepository._write_lock` serializes repository-owned Parquet read-modify-
write operations inside one process. The indicator pipeline performs its own
read-filter-concat-direct-write against the same enriched partitions.

Therefore the lock is not a dataset lock. A realtime merge and an indicator
recompute can both read the same old partition and publish different successors.
Whichever write wins can discard the other's rows. Direct `write_parquet(out)`
also exposes readers to a partially encoded target if the process terminates.

The daily pipeline's process-wide heavy-run slot prevents some competing full
jobs, but it does not statically prove exclusion from quote-service/realtime
writers. Nor does it cover another process.

This is one of the most important architectural weaknesses found: correctness
is distributed across caller knowledge rather than enforced by the only writer.

## 5. A Full Rebuild Is Still a Sequence of Visible Partitions

The indicator full path accumulates frames and checks that it has not omitted an
existing date. That guard is valuable: a transient input scan that produces a
smaller set of dates will not silently delete old date coverage.

After the guard, it overwrites each date partition sequentially. During the
loop, readers can observe a mixed generation: early dates from the new
computation and later dates from the old computation. A crash can leave that
hybrid indefinitely.

The later view refresh does not solve this because file globs already observe
the physical directory. A dataset-level publication boundary would stage an
entire generation and switch one manifest/root pointer after validation, or
make readers bind to an explicit generation.

For many UI reads, brief mixed versions may be acceptable. For reproducible
backtests and adjusted prices, it must be visible and versioned.

## 6. Matrix Generation Can Lag the Actual Enriched Files

Managed repository enriched writes bump the matrix generation after partition
publication. The indicator pipeline writes the same dataset directly and the
inspected path does not call `_bump_matrix_data_generation`.

Consequences:

- a backtest disk matrix may remain compatible according to generation while
  source partitions changed;
- the cache validator skips partition fingerprints when a source generation is
  present;
- a reader-created generation after missing marker can bless the current state
  without knowing its publication history.

This turns the optimization in Domain 8 into a consistency hazard. A generation
is authoritative only if the data publisher and generation publisher are the
same exclusive operation.

## 7. Daily Partial Failure Is Designed but Disconnected

`sync_daily_batch` explicitly supports a `failed_out` collection and logs the
failed symbols. It continues returning successful frames so existing data for
failed chunks remains on disk.

`sync_and_persist_daily_batch` does not pass a `failed_out` list. The daily
pipeline receives only the count of successfully returned rows and emits a
completion message for the requested range. Those failures do not enter
`stage_errors`, so the terminal job may be `succeeded` while part of the universe
was not refreshed.

The later lagging-symbol check helps only when lag exceeds its threshold and can
also include legitimate halt/delist cases. It is not an operation-specific
completeness proof.

The design insight is strong—retain old data for failed chunks and surface
partiality—but the final call path does not carry the fact to job truth.

## 8. Instrument Sync Can Replace Complete State With a Partial Universe

SH, SZ, and BJ fetches are independent and exceptions are swallowed. If one
exchange succeeds, `all_rows` is non-empty and the direct write replaces the
entire instrument file.

This can shrink the universe after a partial upstream failure. Because the
pipeline syncs instruments before resolving the universe, the partial dimension
then controls which symbols receive daily data. It is a cascading consistency
problem, not merely a missing metadata card.

Safe full-replacement admission needs expected source scopes and completeness:

```text
requested exchanges: SH, SZ, BJ
completed exchanges: ...
failed exchanges: ...
previous/new counts by exchange
minimum/maximum plausibility bounds
replacement allowed: true/false
```

Without that envelope, “non-empty” is too weak to authorize replacement.

## 9. Financial Sync Has the Most Severe Replacement Semantics

Financial batches continue after exceptions. The successful records are merged
into one frame and directly overwrite the prior whole table. There is no failed
symbol count in the returned contract, no merge with old rows for ordinary
tables, and no requirement that all requested batches complete.

The scheduler then records `last_sync` whenever the sync function returns. A
zero-row result, a partial frame, or a custom-provider empty response can all
return normally. As a result, the UI timestamp can mean “an attempt returned”
rather than “the requested table is complete as of this time.”

The shares path preserves more history by merging existing rows, missing-symbol
history, and latest observations, but its component fetches still erase failure
scope before the merge.

This is precisely why attempt time, source-as-of, successful coverage, and
complete publication time must be separate facts.

## 10. Multi-Table Sync Is Not One Snapshot

Financial `sync_all` writes tables sequentially. The daily pipeline also
publishes instruments, daily bars, adjustment factors, enriched files, index/ETF
files, minute files, and regime files in stages.

This is not inherently wrong. A local analytical product may intentionally
allow independent freshness. The problem is that a single “pipeline complete”
or a set of per-table timestamps can be read as one coherent market snapshot.

Consumers need either:

- independent dataset versions and explicit compatible-as-of selection; or
- a batch manifest published after all required members are validated.

The required group depends on the consumer. A quote card needs less than an
adjusted backtest.

## 11. Cache and View Refresh Are Post-Publication Best Effort

TickFlow commonly performs this sequence:

```text
publish file(s)
  -> re-register DuckDB view
  -> invalidate status cache
  -> refresh/rebuild Polars cache
```

If the process stops between steps, files may be current while the in-memory
read model is stale. Restart reconstructs many views/caches, so this is usually
recoverable. During the running process, different APIs can observe different
generations.

View refresh exceptions are often warnings. A write can be counted complete
even if the current query facade was not updated. This is acceptable only when
the status exposes “published but read model unavailable” and consumers can
retry or fall back explicitly.

## 12. Preferences Have Lost-Update and Corruption Windows

Preferences use an unlocked read-update-direct-write cycle. Two threads can
read the same old object, apply different keys, and let the later write erase
the earlier key. A crash during `write_text` can leave malformed JSON; the next
load logs a warning and returns `{}`, which makes every setting appear default.

Financial last-sync persistence uses the same file, so an unrelated preference
write can race with synchronization status. Process-local concurrency is enough
to trigger the design flaw; cross-process access broadens it.

The lesson is not to put every preference in a database. A small JSON document
is fine when it has a typed, locked, atomic, versioned update function.

## 13. Job Persistence Does Not Survive the Most Important State

Active jobs are only in memory. Terminal jobs are written directly to individual
JSON files. After restart:

- a previously running job has no durable record;
- an executor thread vanishes with the process, but there is no persisted
  interrupted terminal state;
- a torn terminal JSON file is skipped during scans;
- progress logs are lost until terminalization;
- job state cannot prove which partitions were published before failure.

For a convenience UI this is lightweight. For synchronization truth, job state
should be secondary to a durable publication manifest/ledger. A task status
alone must never authorize consumers to trust a dataset.

## 14. Stale Reaping Reopens the Zombie Race

The heavy-run lock is described as following actual executor lifetime so that a
timed-out but uninterruptible thread prevents a replacement job. This is a sound
fail-closed policy: restart is required to recover from a truly stuck writer.

`reap_stale` marks the job failed and explicitly releases `_heavy_run_lock` even
though it also states the thread cannot be interrupted. The next job can acquire
the slot while the old thread resumes later and continues writing.

That contradicts the adjacent ownership contract. A status timeout is not proof
that mutation stopped. Releasing a safety lock on elapsed wall time converts an
observability decision into writer authorization.

## 15. Progress Can Say Done Before Terminal Failure

The daily pipeline emits `done` at 100 percent, invalidates status caches, then
checks accumulated `stage_errors` and raises `PipelineStageError`. The job store
ultimately records failure, but a polling client can transiently observe a done
stage before the terminal transition.

Progress completion and outcome success should be distinct:

```text
work traversal: 100 percent
terminal outcome: succeeded / partial / failed
publication outcome: complete / incomplete / unknown
```

The current protocol overloads “done.”

## 16. JSONL Alerts Favor Convenience Over Durability

Append is serialized within the process, but prune, delete, and clear rewrite
the active file in place. A crash can truncate the history. Malformed lines are
silently excluded and the next rewrite drops them permanently. Timestamp is
used as deletion identity even though two events may share a millisecond.

For user-facing alert history this may be a deliberate lightweight trade-off.
It should not be copied for trading audit, idempotent notification delivery, or
facts that influence reconciliation.

## 17. Desktop Legacy Migration Is Availability-Oriented

`DataStore` moves legacy data when the new location appears empty and skips when
both contain data. It catches errors so startup can continue.

This avoids destructive overwrite, but it does not verify checksums, validate
Parquet, produce a durable migration manifest, or block silent startup on old
state when migration failed. A user can therefore see an empty/fresh product
while valuable legacy data remains elsewhere.

Aletheia's runtime-home migration provides a stronger contrasting model: stop
writers, stage, verify, publish, mark complete, preserve source, and refuse
ambiguous startup.

## 18. Overall Consistency Assessment

TickFlow demonstrates several strong local techniques:

- temporary-neighbor file replacement;
- partition-level merge/upsert;
- process-local read-modify-write locks;
- pending/running single-flight;
- successful-subset retention instead of deleting failed chunks;
- existing-date guard on full enriched rebuild;
- view/cache invalidation after publication;
- visible soft-stage failure for selected stages.

The architecture is weakened by inconsistent adoption and erased scope:

- not every writer uses the publication primitive;
- no exclusive dataset publisher spans indicator and realtime paths;
- multi-partition generations are visible incrementally;
- partial daily failure is not propagated by the primary caller;
- partial instruments/financial data can replace complete files;
- last-sync can describe an attempt rather than completeness;
- preferences and job files are direct read-modify-writes;
- timed-out job state can release a lock while the writer still exists.

The central lesson is that consistency must be carried as data. Locks and atomic
renames are necessary mechanics; scope, completeness, generation, authority,
and terminal outcome make them meaningful.
