# Aletheia Comparison: I/O and Consistency

## 1. Aletheia Has a Stronger Canonical Write Model

Aletheia concentrates authoritative state in SQLite. TickFlow frequently treats
individual Parquet/JSON files as the unit of state. This gives Aletheia native
transactions across related rows and lets it separate business commit from
secondary mirrors.

The comparison should not conclude that every SQLite path is automatically safe
or every Parquet path unsafe. The meaningful difference is whether the system
has one enforceable commit boundary and can recover its secondary artifacts.

## 2. Writer Serialization Is Explicit and Measurable

Aletheia combines:

- one SQLite connection per OS thread;
- true autocommit outside explicit transactions;
- a bounded dedicated writer queue for short writes;
- a process-level writer permit;
- a cross-process OS file lock;
- definitive wait for commit or rollback after admission;
- `BEGIN IMMEDIATE` for short compare-and-write claims;
- queue depth, admission timeout, wait, lock, transaction, success, and failure
  metrics.

This is materially stronger than TickFlow's per-repository `threading.Lock`
because the permit covers all conforming database writers and can coordinate
multiple processes.

The design also encodes an important epistemic rule: once an authoritative
write is admitted, the caller waits for a definitive outcome. Timing out and
returning “unknown” could lead a caller to repeat an external side effect.

## 3. Transactions Are Kept Database-Only

`run_write` describes its operation as a short database-only closure. The
transaction helper emits slow warnings when waits or holds exceed thresholds.
Autocommit prevents an abandoned daemon thread from retaining a hidden implicit
transaction forever.

This makes a core invariant visible:

```text
network I/O and long computation happen before/after the transaction;
the transaction validates and commits only local state.
```

TickFlow's file read-modify-write naturally includes decoding and encoding
inside its lock. That is sometimes necessary, but it makes lock hold time larger
and recovery less structured.

## 4. Exact Upsert Beats Whole-File Replacement for Live Authority

Aletheia's OHLCV path batch-upserts exact closed bars into a composite-key table
and prunes only that exact instrument/timeframe scope. Failure to fetch one
instrument does not authorize replacement of another instrument's history.

TickFlow's date partitions are efficient for cross-sectional analytics but
require careful merge and complete-frame admission. For live canonical data,
Aletheia's row-level exact identity is the safer authority.

Any future columnar mirror should be generated from committed SQLite rows. It
must not become an independent merge authority.

## 5. Aletheia Represents Operational State Durably

Backfill state stores status, bar count, consecutive failures, last attempt,
next attempt, and safe error. Collector health separates success, partial,
empty, timeout, budget skip, and failure.

TickFlow's pipeline job state is mostly in memory and its last-sync timestamps
can overclaim completeness. Aletheia's state model gives recovery code facts to
continue from rather than infer from file mtimes.

## 6. Durable Outbox Is the Strongest Reusable Pattern

The learning lifecycle path commits an authoritative row with an audit mirror
status. It then attempts the JSONL hash-chain append using a stable ID. Failure
does not roll back the business transition; pending/failed rows remain selectable
for retry. Reconciliation deduplicates against the chain and never replays the
transition.

This solves the impossible “one transaction across SQLite and a file” problem
without pretending that two sequential writes are atomic:

```text
one database transaction:
  business transition + outbox/mirror status
after commit:
  idempotent file append
on failure/restart:
  retry pending stable IDs
```

TickFlow would benefit from this pattern for job publication, notifications,
review reports, and any non-authoritative file mirror that must eventually agree.

## 7. Audit JSONL Has Strong Cross-Process Semantics

Aletheia forbids generic append to the audit file. Its audit path:

- holds both process and OS file locks;
- re-reads the disk tail instead of trusting a process-local cached hash;
- chains each line to the hash of the previous line;
- repairs a uniquely provable incomplete tail by append only;
- deduplicates durable-outbox retries by stable event ID;
- exposes verification for migration/operations.

TickFlow's alerts JSONL is appropriate for a disposable convenience history but
not a model for audit. Aletheia should retain this distinction.

## 8. Aletheia Still Has a General Event Mirror Gap

`EventStore.append` commits SQLite and then appends a monthly JSONL file. If the
process stops between those operations, the database event exists but the file
line does not. On retry with the same idempotency key, `_write_db` returns the
existing row with `inserted=False`, and the caller skips JSONL again.

If JSONL is explicitly a best-effort diagnostic mirror, the database remains
correct and the gap should be documented. If the file is expected to be a
complete replay/export, this path needs the same durable-outbox status used by
learning lifecycle.

This is a useful warning against overgeneralizing from Aletheia's strongest
audit implementation to every dual-write path.

## 9. Configuration Publication Is Better but Not Fully Durable

Aletheia config is typed before save and uses a thread lock, same-directory
temporary file, and `os.replace`. This avoids TickFlow preferences' lost-update
and torn-target windows inside one process.

The save path does not visibly fsync the temporary file or parent directory. A
completed return therefore proves ordinary process-visible replacement, not the
strongest power-loss durability. High-value update/bootstrap and runtime-home
marker paths do fsync, demonstrating that the codebase already understands the
distinction.

Whether configuration requires fsync should be decided by recovery cost and
write cadence, not copied blindly.

## 10. Runtime-Home Migration Is a Model for State Movement

Aletheia refuses to start silently in an empty target while primary legacy state
exists without a completed migration. Its apply path:

- resolves source and target safely;
- requires source processes to be stopped;
- checkpoints WAL and runs SQLite integrity check;
- calculates needed space;
- copies into a private staging directory;
- verifies size and SHA-256 of each file;
- validates typed config and audit chain;
- publishes target children;
- writes an fsynced completion marker last;
- preserves the source;
- removes partially published target children on failure.

TickFlow's desktop migration is availability-oriented and can continue startup
after failure. For trading state, Aletheia's fail-closed migration is the correct
standard.

## 11. WAL and `synchronous=NORMAL` Are a Deliberate Trade-Off

Aletheia enables WAL, foreign keys, auto-checkpoint, journal size limit, and a
busy timeout. `synchronous=NORMAL` improves throughput while retaining SQLite's
transactional consistency, but its precise power-loss durability depends on
SQLite/filesystem/platform behavior.

The research does not claim “no committed transaction can ever be lost under
power failure.” It does establish that application-visible partial transactions
are prevented and that backups/migration explicitly checkpoint and verify.

This evidence boundary matters when comparing to a temporary-file rename that
also lacks fsync.

## 12. Backup Is a Separate Consistency Product

Aletheia uses SQLite's online backup API for live WAL-consistent copies and has
managed archive verification/restoration. Diagnostic exports explicitly state
that they are not runtime backups.

TickFlow's file directory is superficially easy to copy, but copying while a
multi-partition pipeline writes can capture a mixed generation. A valid backup
must bind files to a stopped writer or a published generation manifest.

## 13. Aletheia Should Borrow TickFlow's Best File Techniques

Despite the stronger canonical database, Aletheia still creates many secondary
files and future analytical projections. TickFlow reinforces several techniques:

- temporary neighbor plus replacement for reconstructible files;
- temporary directory plus manifest-last publication for multi-file caches;
- file suffixes that active readers/globs ignore;
- one explicit in-process lock around read-modify-write;
- byte-bounded retention of derived artifacts;
- generation/fingerprint validation on cache open.

Aletheia should combine those with its existing cross-process permits, durable
outbox, typed status, and lifecycle ownership.

## 14. Recommended Publication Classes

### Class A: Canonical transactional state

Examples: orders, approvals, fills, positions, settlements, opportunities,
exposures, outcomes, audit-outbox rows.

- SQLite transaction is authority.
- exact identity and idempotency constraints apply.
- no file write participates in the transaction.
- secondary mirrors use durable outbox.

### Class B: Canonical append-only evidence file

Example: hash-chained audit JSONL where the file itself has a defined authority.

- process and cross-process lock;
- stable identity/deduplication;
- incomplete-tail recovery;
- verification and rotation contract;
- structured database mirror clearly classified.

### Class C: Reconstructible analytical projection

Examples: OHLCV columnar mirror, feature partitions, backtest matrices.

- built from an exact committed source watermark;
- staged and validated;
- generation/manifest published last;
- readers bind to one generation;
- safe to discard and rebuild;
- never written back into trading authority.

### Class D: Convenience state

Examples: UI preferences, cache profiles, recent job display.

- typed schema and defaults;
- locked atomic replacement;
- bounded retention;
- corruption is visible and recoverable;
- never used as proof of a business commit.

## 15. Aletheia Risks to Preserve in Later Work

### Bypassing the writer lane

The writer architecture is strong only for conforming mutations. Static analysis
found broad repository use, but did not globally prove that every direct
`db.execute` mutation is enclosed in an owned transaction or intercepted as
intended. Domain 12 can use focused structural scans to classify remaining
legacy direct writes without running a broad test suite.

### Long work inside transactions

The telemetry warns but cannot prevent a caller from performing file/network
work inside `transaction()`. Review must continue to treat a long holder as a
bug rather than increasing timeouts.

### Cross-process file writers outside audit

Generic JSONL and several artifact/config paths use only process-local locks or
no lock. Their authority class should be documented. Only files shared by
multiple runtime processes need the stronger permit.

### Secondary mirror ambiguity

Every dual write should state which side is authoritative, how gaps are detected,
and whether retry exists. The learning audit outbox is not automatically the
contract of `EventStore` or generic logs.

### Cache after commit

If a future in-memory read model updates after SQLite commit and fails, the
database remains correct but readers may be stale. Cache invalidation should be
idempotent and rebuildable from the database; readiness/status should expose the
gap rather than compensating with a second database write.

## 16. Adoption Guidance

### Preserve without change in principle

- SQLite as the only trading/business authority;
- exact composite identities;
- writer queue and process/cross-process permit;
- short database-only transactions;
- definitive commit/rollback results;
- durable operational/backfill state;
- learning lifecycle durable outbox;
- hash-chained audit writer;
- verified, staged runtime-home migration and backups.

### Extend selectively

- apply durable-outbox semantics to secondary mirrors that claim completeness;
- define atomic publication helpers by artifact class;
- add dataset manifests for future columnar projections;
- record partial/complete/failed independently from row count and attempt time;
- expose generation/readiness after publication;
- add entry/byte bounds and single-flight to non-authoritative caches.

### Reject from TickFlow

- partial non-empty frame as full replacement authorization;
- per-file atomic rename as proof of whole-dataset consistency;
- direct JSON read-modify-write without lock/replacement;
- job timeout as permission to start a new writer;
- last-sync updated after an empty/partial normal return;
- progress 100/done before terminal outcome;
- process-only lock presented as cross-process safety;
- file mtime as authoritative sync completion.

## 17. Comparison Conclusion

Aletheia's consistency architecture is substantially stronger where it matters
most: transactional trading state, writer serialization, durable retry facts,
audit identity, and migration/backup verification. TickFlow contributes useful
file-publication and dataset-shaping ideas, but also demonstrates how easily
local atomicity can coexist with global semantic inconsistency.

The combined principle is:

```text
commit canonical facts once;
publish derivatives from that commit;
record completeness and generation explicitly;
retry mirrors by stable identity;
never infer writer termination or dataset truth from UI status alone.
```
