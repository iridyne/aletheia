# Domain 9 Lessons and Reflections

## Reuse Directly

### Same-directory temporary publication

Write a temporary neighbor that active readers do not match, validate it, close
all handles, then replace the target. Clean stale temporaries on supervised
startup or before the next publication.

### Refuse shrinking full rebuilds without explanation

Compare expected and rebuilt coverage before overwriting an existing dataset.
Treat missing prior partitions/scopes as a failure unless an explicit retention
or deletion decision authorizes them.

### Preserve old data for failed acquisition chunks

Do not replace a failed symbol/date/provider scope with empty data. Retain the
last known canonical rows and expose the failed scope as partial/stale.

### Pending and running both count as active

Close the creation/start race by reserving single-flight ownership before
scheduling work. A second caller reuses the active identity instead of starting
another writer.

### One writer permit follows actual mutation lifetime

Do not release writer authorization because a status timer expired. If a thread
cannot be stopped, fail closed and require supervised process restart before a
new overlapping writer begins.

### Commit business state before retryable mirrors

Use a durable outbox row and stable ID. Retry the mirror independently and never
replay the business transition to repair a file/notification gap.

### Distinguish attempt, publication, and source time

Record separately:

```text
attempt_started_at / attempt_finished_at
source_as_of
published_at
complete_at
requested / succeeded / failed scopes
previous and new coverage
```

### Keep Aletheia's definitive write outcome

After an authoritative SQLite write enters the writer lane, wait for commit or
rollback. Do not abandon the future and leave the caller unsure whether a state
transition landed.

### Verify state movement before marking complete

Stop source writers, checkpoint databases, stage copies, verify checksums and
semantic validators, publish, write the completion marker last, and preserve the
source until the new runtime is accepted.

## Reuse After Adaptation

### Dataset generation publication

For future analytical mirrors, stage a complete generation and publish one
manifest/pointer only after all partitions validate. Bind every reader and cache
to that generation.

The publisher must be exclusive. A generation bumped by only some writers is
worse than partition fingerprints because it can falsely validate stale cache.

### Completeness envelope for every sync

Return a typed result such as:

```text
requested_scope
succeeded_scope
failed_scope and safe reasons
accepted/rejected rows
previous/new coverage
partial / complete / empty / failed
publication_id
```

Do not let a bare integer row count authorize status or replacement.

### Full-replacement admission gate

Before replacing instruments or financial tables, require all expected source
partitions or apply an explicitly versioned merge policy. Compare counts by
exchange/table/symbol and quarantine implausible shrinkage.

### Atomic typed convenience documents

Preferences and small JSON state can remain files if they use:

- one typed schema/version;
- one lock shared by all setters;
- read-modify-write under that lock;
- same-directory temporary replacement;
- corruption backup/quarantine rather than silent `{}`;
- optional cross-process lock where multiple runtimes write.

### Progress separate from terminal truth

Represent traversal percentage, terminal task outcome, and publication
completeness as separate fields. “100 percent traversed” may still be partial or
failed.

### View/cache rebuild after canonical commit

Treat caches as disposable followers. If rebuild fails, keep the canonical
commit, mark the read model unavailable/stale, and retry idempotently. Never
roll back trading truth to make a cache agree.

### Durable job recovery only where needed

Persist job identity, requested scope, owner process/session, heartbeat,
publication IDs, terminal outcome, and interrupted-on-restart status for
long-running data mutations. Progress logs can remain bounded.

### Fsync by authority class

Atomic replace protects readers from torn visibility. Add file and parent-
directory fsync when loss after an acknowledged return would violate the
artifact's recovery contract. Do not impose it on disposable caches without a
measured need.

### Cross-process file permit

If gateway, collector, frontdoor, or desktop helper can write the same file,
use an OS-level permit. A `threading.Lock` only coordinates callers in one
interpreter.

## Aletheia-Specific Questions

- Which file artifacts are canonical, which are mirrors, which are caches, and
  which are diagnostic exports?
- Does any canonical state transition write SQLite and a file/notification
  without a durable outbox status?
- Does every production mutating SQL statement pass through the writer lane or
  an explicitly owned transaction?
- Which direct `db.execute` mutations are legacy and which are safe inside a
  transaction?
- Can any scheduler callback hold `transaction()` across network, model, or file
  work?
- Which generic JSONL files can be written by more than one process?
- Should `EventStore` monthly JSONL be complete replay evidence or a best-effort
  mirror?
- Which configuration/artifact acknowledgements require fsync-level durability?
- What canonical watermark would a future OHLCV analytical export record?
- How would older-bar correction invalidate one published projection generation?

## Study Further

- Trace every enriched-partition writer in TickFlow and build an exact writer ×
  lock × atomic-helper × generation matrix.
- Verify whether quote-service live flush can overlap `run_pipeline` against the
  same enriched date partition.
- Trace `failed_out` from every daily/index/ETF/minute fetch to terminal job
  status; record where failure scope disappears.
- Compare requested and returned symbol/exchange coverage for instrument sync
  and define safe replacement criteria.
- Trace financial sync zero-row and partial outcomes through API status and UI
  labels.
- Inspect TickFlow preferences, secrets, auth, rule, strategy, and report writers
  for shared-file races and authority classification.
- Inspect indicator full rebuild crash recovery: mixed partitions, temporary
  cleanup, matrix generation, and next-run detection.
- Inspect backup/export behavior while TickFlow pipeline writers are active.
- Audit Aletheia direct mutating SQL structurally and distinguish writer-lane,
  explicit-transaction, migration, and unsafe legacy paths.
- Inventory Aletheia database-to-JSONL/notification dual writes and identify
  which have durable outboxes.
- Determine intended completeness of `EventStore` monthly JSONL and add an
  explicit status/reconciler only if it is trust-bearing.
- Compare Aletheia config, runtime marker, packaged update, backup, and artifact
  fsync policies by authority class.
- Examine WAL checkpoint, backup, restore, and migration behavior on the Windows
  validation host when implementation work is later authorized.
- Define crash-injection scenarios for future implementation work without
  turning this analysis phase into a broad test campaign.

## Do Not Reuse

### Partial frame as full replacement

A non-empty result proves only that something succeeded. It does not prove the
requested universe/table is complete.

### Atomic file as atomic dataset

Replacing each partition safely still exposes mixed generations and can leave a
half-published multi-file rebuild after a crash.

### Writers outside the dataset lock

A repository lock is not meaningful if another service writes the same target
directly. Enforce one publication boundary.

### Generation maintained by only some writers

Readers will skip stronger file fingerprints because the generation appears
authoritative, producing false cache validity.

### Attempt time labelled last sync

Do not update a success timestamp after a zero-row, partial, unsupported, or
failed-scope normal return.

### Timeout-based safety-lock release

Elapsed time changes status, not physical execution. Never start a replacement
writer until termination is proved or the owning process is restarted.

### Direct read-update-write preferences

It loses concurrent keys and can turn a crash into malformed JSON that silently
resets behavior to defaults.

### In-place JSONL prune for trust-bearing evidence

Rewrite/truncate can lose the entire file on crash and silently discard malformed
lines. Use append-only identity plus durable compaction/publication rules.

### File mtime as migration or sync proof

Mtime does not prove source completeness, schema compatibility, successful
publication, or which provider produced the current content.

### “Done” before terminal classification

Do not emit a success-looking terminal stage and only afterward discover that
soft failures require a failed job.

### Best-effort startup for primary trading-state migration

Do not continue with an empty new runtime when valuable old primary state exists
and migration has not been verified and marked complete.

## Final Reflection

The most useful realization from this domain is that consistency is a protocol,
not a filesystem trick. Atomic rename answers “can a reader see half of this
file?” It does not answer “did we obtain everything requested, are all files from
one generation, did the cache follow, did the job truly succeed, and can restart
prove what happened?”

Aletheia already answers more of those questions through transactions, exact
identity, durable status, outbox retry, and verified migration. TickFlow's best
file techniques can strengthen future analytical derivatives, but only when
placed underneath—not substituted for—that semantic protocol.
