# Domain 9: Data I/O, Consistency, and Synchronization Semantics

This domain follows a data change from acquisition through normalization,
publication, cache/view invalidation, task status, restart, and recovery. It
separates single-file atomicity from dataset completeness and business truth.

## Included

- Parquet read-modify-write, temporary files, replacement, and partition scope;
- whole-dataset versus per-file publication and generation changes;
- process-local locks, cross-process assumptions, single-flight, and zombie work;
- partial provider batches, overwrite policy, completeness, and last-sync truth;
- view/cache refresh ordering and stale-read windows;
- JSON, JSONL, preferences, alerts, job history, and result persistence;
- TickFlow pipeline status and crash/restart semantics;
- Aletheia's SQLite writer lane, transactions, WAL, durable outbox, audit chain,
  config writes, backup/migration, and remaining dual-write gaps.

## Excluded

- analytical query/caching mechanics, covered in Domain 8;
- realtime quote and browser transport behavior, covered in Domain 10;
- full job scheduling/ownership design, covered in Domain 11;
- security and complete quality-assurance review, covered in Domain 12;
- implementation, migration execution, fault injection, or tests.

## Reports

- `source-map.md`
- `io-consistency-analysis.md`
- `aletheia-comparison.md`
- `lessons.md`

## Evidence Boundary

Findings are based on current source, CodeGraph relationships, and focused
inventories. No sync, provider, pipeline, write, crash injection, database
mutation, server, benchmark, or test suite was run.
