# Position-path storage baseline

The reproducible read-only measurement is:

```sh
uv run python scripts/position_path_storage_baseline.py \
  --database /path/to/read-only-fedora-copy/app.db \
  --output position-path-storage-baseline.json
```

The script measures SQLite `dbstat` allocations for the hot observation table,
its indexes, deferred buckets, archive metadata, the database/WAL, row and
Position distributions, duplicate market bars, repeated-field cardinality,
and the `<=72h` versus `>72h` recovery split. It never treats compression as
deferred-bucket recovery, and it holds one explicit read-only SQLite snapshot
for the physical and logical measurements.

The benchmark must run against a read-only copy or read-only runtime connection;
the local control database is not a representative substitute for Fedora.

Owner-supplied runtime facts retained for the next read-only capture are: merged
#1009 is present on Fedora; acceptance Position `7d05e966c547` has 142/142
complete five-minute buckets with zero missing and duplicate buckets; the
deferred count is 294, with queue ages `<1h=0`, `1-6h=3`, `6-24h=291`; and the
underlying market-bucket ages are `<1h=0`, `1-6h=3`, `6-24h=2`, `1-3d=10`,
`>3d=279`. These are inputs, not independently verified measurements from this
checkout. The detailed Fedora capture below is the authoritative versioned
artifact for this run; a later read-only sanity check is recorded separately
because the live source table changes as the observer runs. No recovery issue
was opened because it did not establish a fresh-bucket convergence or resource
failure.

The acceptance Position was separately rechecked through the same read-only
runtime connection at `2026-08-15T16:54Z`: `7d05e966c547` is closed with 142
available rows, zero unavailable rows, zero duplicates, and zero deferred
buckets.

The acceptance Position was rechecked again against the latest explicit
read-only snapshot at `2026-08-15T17:25Z`: it remains closed with exactly
142 rows across 142 distinct buckets, all available, zero unavailable rows,
and zero deferred buckets. The archive table is still absent from Fedora.

## Fedora read-only capture

At `2026-08-15T16:20:53Z`, the same script ran over
`/home/admin/.aletheia/app.db` through a read-only SSH session. It measured
`app.db=1,586,610,176` bytes, WAL `35,172,472` bytes (database plus WAL
`1,621,782,648` bytes), 5,563 hot observation rows across 82 Positions, 61
eligible closed Positions, rows/Position p50=38, p95=218.8, max=316, including
one 142-row path. The observation table plus implicit PK/index was 1,941,504
bytes; including deferred-table/index allocations, the measured hot footprint
was 2,007,040 bytes, or 0.126499% of `app.db`. The table payload allocation was
1,687,953 bytes, table unused
allocation was 33,217 bytes, and table-plus-index payload/unused allocations
were 1,821,337 / 62,844 bytes. Weighted dbstat bytes/bucket p50/p95/max were
293.987648 / 293.987648 / 293.987648 bytes. Exact duplicate available
market-bar groups were 0. The archive table was not present, so no persisted
before/after or deployed Fedora shadow archive result exists. The complete
read-only JSON result, including dbstat objects and repeated-field cardinality,
is [POSITION_PATH_STORAGE_BASELINE.json](POSITION_PATH_STORAGE_BASELINE.json).

The recovery split now uses the underlying `bucket_open_ms` age rather than
the queue discovery timestamp: 13 buckets are hot (`<=72h`) and 253 are cold
(`>72h`), with the oldest hot bucket at `2026-08-12T18:35:00+00:00` and the
oldest cold bucket at `2026-08-08T09:55:00+00:00`. All 266 currently persisted
deferred rows were discovered within 72 hours, so this is a recently discovered
historical backlog, not evidence that fresh buckets continuously fail. No
convergence/resource issue was opened because the capture does not establish a
fresh-bucket failure or resource-convergence problem; the cold backlog remains
explicitly deferred and is not counted as cleared or healthy.

The table is below the specification's materiality gate, but the branch still
contains the requested additive codec, immutable shadow archive, and unified
reader implementation for exact merged-code validation. Production compaction
remains disabled: no hot rows were deleted, and no deployed Fedora
archive/read-model shadow result exists until the exact merged implementation is
deployed.

A later read-only sanity capture at `2026-08-15T16:40:13Z` found the expected
live-source drift: 5,649 observation rows, 62 eligible closed Positions, 266
deferred rows, and no archive table. The hot footprint including deferred-table
and index allocations represented `0.128154%` of `app.db`; the underlying
recovery split remained 13 buckets at
`<=72h` and 253 at `>72h`, with the same oldest hot/cold bucket timestamps. The
versioned JSON artifact above intentionally remains the complete 16:20 dbstat
capture rather than mixing two snapshots.

A newer explicit read-only sanity capture at `2026-08-15T16:53:40Z` measured
5,738 observation rows across 83 Positions, 63 eligible closed Positions, 265
deferred rows, and no archive table. The hot footprint including deferred-table
and index allocations was 2,076,672 bytes, or `0.130557%` of `app.db`; rows per
Position were p50=40, p95=229.4, max=328, and exact duplicate market-bar groups
remained 0. The underlying backlog split was 13 buckets at `<=72h` and 252 at
`>72h`, with the oldest cold bucket at
`2026-08-08T10:30:00+00:00`. The detailed JSON artifact remains the earlier
complete dbstat snapshot; this paragraph records the later live-source sanity
capture without mixing snapshots.

The next explicit read-only snapshot at `2026-08-15T16:57:40Z` measured 5,764
rows across 83 Positions, 66 eligible closed Positions, and 265 deferred rows.
Including deferred-table and index allocations, the hot footprint was 2,084,864
bytes, or `0.131069%` of `app.db`; rows per Position were p50=40, p95=231.2,
max=330. The underlying recovery split remained 13 buckets at `<=72h` and 252
at `>72h`, with no archive table. This was the newest sanity capture at that
time; the versioned detailed artifact remains the earlier complete dbstat
snapshot.

The latest separate explicit read-only physical snapshot at
`2026-08-15T17:10:47Z` measured 5,833 rows across 84 Positions with rows, 67
eligible closed Positions, and 264 deferred rows. Including deferred-table and
index allocations, the hot footprint was 2,105,344 bytes, or `0.132272%` of
`app.db`; rows per Position were p50=39, p95=235.4, max=336. The underlying
recovery split was 13 buckets at `<=72h` and 251 at `>72h`, with oldest hot
bucket `2026-08-12T18:35:00Z` and oldest cold bucket `2026-08-08T11:05:00Z`.
This is a separate live-source sanity snapshot; the versioned detailed dbstat
artifact remains the earlier complete capture.

The newest explicit read-only physical snapshot at
`2026-08-15T17:23:42Z` measured 5,877 rows across 85 Positions with rows, 73
eligible closed Positions, and 263 deferred rows. The observation table plus
its primary-key/index allocations was 2,056,192 bytes; including the deferred
table and indexes the hot footprint was 2,121,728 bytes, or `0.133096%` of
`app.db` (database 1,594,134,528 bytes; WAL 38,402,552 bytes). Rows per
Position were p50=38, p95=236.6, max=340; exact duplicate market-bar groups
remained 0. The underlying recovery split was 13 buckets at `<=72h` and 250
at `>72h`, with oldest hot bucket `2026-08-12T18:35:00Z` and oldest cold bucket
`2026-08-08T11:20:00Z`. The machine-readable latest summary is stored under
`latest_read_only_snapshot` in `POSITION_PATH_STORAGE_BASELINE.json`; the
original complete dbstat capture remains unchanged for reproducibility.

## Fixture benchmark (not an acceptance result)

The reproducible benchmark was also run against a local migrated fixture with
three archived segments and 433 rows. It includes short/142/long paths,
unavailable evidence, multiple sessions/commits, ExitPlan revisions, quantity
variation, and late recovery. The fixture status was `passed`; these numbers
still must not be used as Fedora proof, and its acceptance status remains
`incomplete` because write-latency, a real Fedora 2048-bucket sample, and a
deployed Fedora archive/read-model shadow are still pending.

| Candidate | Ratio overall | Ratio p50/p95 | Bytes/bucket p50 | Bytes/bucket p95 | Encode p95 (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| JSON + Zstd 3 | 0.0208 | 0.0231 / 0.3685 | 6.27 | 148.00 | 0.076 |
| JSON + Zstd 6 | 0.0215 | 0.0239 / 0.3635 | 6.50 | 146.00 | 0.132 |
| JSON + Zstd 9 | 0.0207 | 0.0224 / 0.3635 | 6.08 | 146.00 | 0.136 |
| MessagePack rows + Zstd 3 | 0.0271 | 0.0287 / 0.3995 | 8.32 | 161.00 | 0.023 |
| MessagePack rows + Zstd 6 | 0.0268 | 0.0284 / 0.3945 | 8.24 | 159.00 | 0.122 |
| MessagePack rows + Zstd 9 | 0.0268 | 0.0284 / 0.3945 | 8.25 | 159.00 | 0.158 |
| MessagePack columns + Zstd 3 | 0.0297 | 0.0314 / 0.4489 | 9.03 | 184.33 | 0.017 |
| MessagePack columns + Zstd 6 | 0.0289 | 0.0305 / 0.4302 | 8.78 | 176.67 | 0.033 |
| MessagePack columns + Zstd 9 | 0.0289 | 0.0306 / 0.4286 | 8.80 | 176.00 | 0.039 |
| `position_path_segment/v1` | 0.1049 | 0.4784 / 0.8329 | 3.12 | 106.33 | 26.42 |

The candidate timing table is from the `2026-08-15T16:14:03Z` 100-repeat local fixture run;
candidate ratios and byte counts are deterministic for this fixture, while
wall-clock timings are host-dependent. The benchmark's peak traced memory was
9,442,138 bytes.

The same candidate benchmark is now recorded for the explicit Fedora
read-only stream at `2026-08-15T17:17:42Z` in the machine-readable shadow
artifact. The selected v1 payload was 144,285 bytes (44.25 / 134.75 bytes per
bucket at p50/p95; encode p95 6.667 ms), versus JSON + Zstd 9 at 154,475 bytes
(47.59 / 158.50; 0.319 ms) and MessagePack columns + Zstd 3 at 187,493 bytes
(59.00 / 193.00; 0.035 ms). Raw MessagePack columns were 1,243,126 bytes.
The ratio is not compared across candidates without their raw-layout size;
selection is based on absolute payload and bytes-per-bucket under the fixed
semantic contract. Full p50/p95/max and all levels remain in
`POSITION_PATH_ARCHIVE_FEDORA_SHADOW.json`.

The current SQLite row layout remains the physical baseline: the Fedora capture
measured 1,941,504 bytes for the observation table plus its primary-key/index
allocations; the full dbstat distribution is retained in the baseline artifact.

For this fixture, the persisted archive table plus indexes was 21.43% of the
hot table plus indexes; decode p95 was 12.89 ms, unified-read p95 21.81 ms,
as-of read p95 21.83 ms, and full-story replay p95 11.97 ms. A real 2048-bucket
decode sample was not available in this fixture. The benchmark now also reports
a deterministic synthetic 2048-bucket scaling sample (local p95 96.55 ms with
tracemalloc enabled, payload ratio 0.0206); it is not Fedora evidence and does
not close the target acceptance gate. The selected shadow candidate remains
MessagePack array-of-columns plus Zstandard at level 3 behind the versioned
`position_path_segment/v1` contract; the selection is provisional until a
representative real-Fedora persisted/read-model shadow run. Raw MessagePack is
implemented only as the tiny-segment fallback when level-3 Zstandard is larger;
Parquet and a shared Zstandard dictionary remain rejected in v1.

## Live Fedora read-only stream shadow (not a deployed archive)

The reproducible stream verifier is
`scripts/shadow_position_path_archive_fedora_stream.py`. A read-only SSH query
streams only Position timing identity, deferred Position IDs, and
`position_path_observations` rows into its JSON stdin; it never opens or writes
Fedora's database. An earlier current-format capture at
`2026-08-15T16:23:45+00:00` contained 5,563 source rows across 347
open/closed Positions. Exact closed/no-deferred/terminal checks admitted 61
Positions and 3,619 rows into 62 fixed segments; 265 Positions were deferred,
15 had incomplete/unexpected ranges, five had invalid time bounds, and one was
open. Production payloads were 134,181 bytes versus 278,189 raw bytes (48.23%
payload ratio). All 3,619 rows passed field-exact round-trip, deterministic byte
identity, and per-segment median `as_of` parity; unavailable observations were
0 in this capture.

A later read-only stream at `2026-08-15T16:43:41Z` used one explicit read-only
SQLite snapshot and observed the live source
moving to 5,678 rows, 62 eligible Positions, and 3,656 eligible rows across 63
segments. It verified 3,656 rows with deterministic and `as_of` parity, using
136,058 payload bytes versus 281,071 raw bytes (48.41%); the skip profile was
264 deferred, 15 incomplete/unexpected, five invalid-time, and one open. This
is a current sanity capture; the machine-readable artifact below now records
the newer stream while retaining the historical disposable persisted-copy
result.

This is real Fedora source-stream codec evidence, but not a persisted archive:
the report explicitly returns `archive_persisted=false` and
`read_model_parity=not_run`. The Fedora runtime still has no archive table, so
this does not close the deployed shadow/read-model gate or justify compaction.

The newest explicit read-only stream snapshot at `2026-08-15T17:17:42Z`
verified 3,919 eligible rows across 68 fixed segments from 5,856 source rows
and 67 eligible Positions. It used 301,886 raw bytes and 144,285 payload bytes
(47.79% payload ratio), with deterministic byte identity and median `as_of`
parity both true; the skip profile was 262 deferred, 12 incomplete/unexpected,
five invalid-time, and one open. Production codec timing was encode p95 6.60 ms
and decode p95 3.28 ms on this stream. It remains source-stream evidence only:
`archive_persisted=false` and `read_model_parity=not_run`.

The latest explicit read-only stream at `2026-08-15T17:24:58Z` verified 5,251
eligible rows across 74 fixed segments from 5,877 source rows and 73 eligible
Positions. It used 400,181 raw bytes and 173,161 payload bytes (43.27% payload
ratio), with deterministic byte identity and median `as_of` parity true. The
production codec timing was encode p95 10.39 ms and decode p95 5.02 ms. Source
coverage includes short paths, the 142-bucket acceptance path, long paths,
multiple sessions/commits, ExitPlan revisions, and late recovery; it does not
contain unavailable observations or quantity-variation/scale-in evidence, so
`representative_input_complete=false`. The stream remains read-only source
evidence: `archive_persisted=false` and `read_model_parity=not_run`.

A second read-only check at `2026-08-15T16:24:22Z` streamed the current Fedora
source population into a disposable SQLite copy, applied the current
migration, and persisted archive segments there. It covered 5,563 source rows,
created 62 segments for 3,619 eligible rows, and verified 3,619 rows through
both the archived reader and unified reader with `source_unchanged` true. The
full shadow parity projections also passed for the Position-path report,
Lifecycle Story, coverage counts, MFE/MAE/giveback, story events, as-of,
provenance, and evidence gaps. The shadow status remained `partial` only
because six Positions still had deferred buckets and fifteen had
incomplete/unexpected ranges. The copied-data payload was 134,181 bytes versus
278,189 raw bytes (48.23%); dbstat measured 233,472 archive table/index bytes
versus 1,945,600 hot table/index bytes (12.00%). This proves real-data
persistence/read parity in an isolated copy only; it does not establish
deployed Fedora state or the 2048-bucket and observer-write gates.
The machine-readable shadow result is
[POSITION_PATH_ARCHIVE_FEDORA_SHADOW.json](POSITION_PATH_ARCHIVE_FEDORA_SHADOW.json).

The separate write benchmark uses disposable SQLite clones rather than the
runtime database:

```sh
uv run python scripts/benchmark_position_path_write_latency.py \
  --database /path/to/read-only-copy/app.db --repeat 500 --json
```

The `2026-08-15T16:08:21Z` local-fixture run (500 samples) measured hot
insert/`BEGIN IMMEDIATE`/commit p95 at 0.266 ms with the archive schema and
0.285 ms without it (ratio 0.934); p50 was 0.209 ms and 0.211 ms. This is an
isolated SQLite comparison only; it is not proof that the Fedora observer's
write latency is unchanged, so the production gate remains open.

The latest focused storage/reader, CLI/control, Position Replay, and exact-trade-path
regression set in this checkout passed `248 passed, 2 skipped in 2.85s`. Ruff,
`uv lock --check`, Python bytecode compilation, and `git diff --check` also
passed; no full repository suite was run on macOS. Earlier replay/profit and
retention evidence remains historical and does not close the Fedora deployment
gates.

## Acceptance gate ledger

| Gate | Evidence in this branch | State |
| --- | --- | --- |
| Archived storage no more than 40% of the hot table plus indexes | Local migrated fixture: 24,576 / 114,688 = 21.43%; Fedora has no archive table | Fixture-only pass; production unverified |
| Exact field round-trip and semantic/compressed hashes | Focused archive tests and latest explicit-snapshot Fedora stream: 5,251 rows; persisted-copy artifact: 3,619 rows | Pass for tested codec/shadow stream |
| Deterministic bytes and corruption fail-closed | Focused tests plus Fedora stream byte identity | Pass |
| 2048-bucket decode p95 below 100 ms on Fedora | No real Fedora 2048-bucket path; local synthetic sample only | Open |
| Observer write latency unchanged | Disposable local SQLite clones only: p95 0.266 ms with schema vs 0.285 ms without | Open for Fedora observer |
| Real Fedora persisted archive/read-model parity | Disposable Fedora-data copy: source unchanged, all shadow parity projections true; deployed Fedora still has no archive table | Copy pass; deployed gate open |
| Materiality of the optimization | Detailed Fedora dbstat artifact: 0.126499%; latest read-only snapshot: 0.133096% including deferred allocations | Fails materiality case; do not enable production compaction |

The branch therefore stops at additive validation. It does not claim deployed
archive storage, does not delete source rows, and does not open a recovery issue
because the read-only backlog split did not establish a fresh-bucket convergence
or resource failure.

The segment boundary is the deterministic bucket-index window
`[floor(bucket_index / 2048) * 2048, ... + 2048)`, so ordinary paths remain in
one segment even when they cross a UTC day; only paths longer than 2048 buckets
split.

The SQLite metadata records Position/segment identity, fixed bounds and counts,
observed-at bounds, codec/schema/provenance, raw and compressed byte counts,
semantic (`content_hash`, computed from the canonical logical row array
independently of the wire codec) and compressed-payload hashes, seal time, and
the compressed BLOB. The payload is the fixed v1 array
`[schema, version, bucket_step, position_id, segment_start, segment_end,
first_bucket, row_count, null, observed_at_deltas, dictionaries, columns]`;
`row_count` and the single `bucket_step` derive the contiguous bucket sequence.
Columns preserve all v26 fields with dictionaries/enums, float64 values, and
NULL bitmaps.
The metadata codec is `msgpack+zstd` at `msgpack+zstd/v1`, level 3 by default,
or raw `msgpack` at `msgpack/v1`, level 0 only when Zstandard would be larger;
readers validate the recorded codec version before decoding.

The implementation path is deliberately additive: migration
`20260815_0029_position_path_archive_v30` in `aletheia/storage/migrations.py`
creates the archive table, range index, immutability triggers, and source-delete
guard; `aletheia/storage/position_path_archive.py` owns the v1 codec; and
`aletheia/storage/repositories.py` owns sealing, integrity checks, and the
unified reader. Analytics consumers route through that reader. The CLI scripts
are `scripts/shadow_position_path_archive.py` and
`scripts/compact_position_path_archive.py`; the latter is required to remain
dry-run-only in this phase.

All direct five-minute readers now use the repository unified boundary, including
the Position Replay holding projection. Position Replay still retains its
separate exact 1-minute `trade_path` contract; it additionally exposes the
5-minute `position_path` rows through the unified reader without changing the
#1007 trade-path semantics.

The compaction command is intentionally dry-run-only. A later focused PR may
add atomic archive-plus-delete after real Fedora shadow parity, but must retain
the dual reader, fail closed on coverage/integrity mismatch, report raw and
archived bytes/ratio, and use the repository's checkpoint/controlled-vacuum
strategy to reclaim pages. The current recommendation is therefore **do not
enable production compaction**.
