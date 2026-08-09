# Persistence Contract

## Purpose and current scope

Aletheia durably records local metadata, events, artifacts, order identities,
and selected policy/learning state. These stores survive a normal process
restart. This Contract does not claim automatic Gateway recovery, exchange
reconciliation before startup, or `SAFE_FREEZE` enforcement.

Schema v9 also retains the protective-barrier substrate defined by
[`data_model` §11](../data/data_model.md#11-protective-barrier-contract-schema-v9-storage-new-paper-runtime-active).
The consumer is wired for newly opened `paper_internal` Positions that have a
resolved protective level. Its periodic cycle also forward-adopts an already
open, unprotected local-paper Position from the adoption instant; it never
backdates a plan/cursor or replays pre-adoption candles. Storage does not claim
protection for level-less entries or external-exchange protection.

Schema v11 additionally retains prospective, observe-only exit experiment facts
in `exit_breakeven_shadow_assignments` and `exit_breakeven_shadow_outcomes`.
Assignment is best-effort and cannot block a valid Position open; settlement is
replayed by the existing durable outcome-hook outbox after an eligible close.
Neither table is a recovery command or trading authority.

Schema v12 retains both immutable `ufc-fixed-look/v1` plans and the current
`ufc-fixed-look/v2` conditional-randomization plans. The migration rebuilds only
`learning_inference_decisions` under a savepoint, preserves its rows and storage
guards, and grants no Learning or trading authority.

The future safety design is
[`gateway_recovery.md`](../proposals/gateway_recovery.md).

## Entry points

- SQLite repositories and model stores provide durable application state.
- `EventStore` appends machine-local JSONL events.
- `ArtifactStore` persists immutable artifacts and manifests.
- `/api/persistence/*` exposes the currently registered persistence API.
- Exchange adapters send deterministic client order identifiers where their
  current implementation supports them.

## Durable state and consumers

| State | Current consumer |
|---|---|
| SQLite application tables | runtime services, API routes, analytics, and reconciliation |
| Append-only event log | reconciliation and learning/policy readers |
| Artifact store | lessons, skills, policies, and operator-facing readers |
| Deterministic client order ids | supported exchange adapter submission paths |
| `position_exit_plan_versions` / `position_protection_state` / `position_protection_triggers` | new and forward-adopted local-paper activation, exact-1m protective scheduler, existing native local close |
| `exit_breakeven_shadow_assignments` / `exit_breakeven_shadow_outcomes` | new protected local-paper assignment and eligible-close shadow settlement |

An in-memory object is never promoted to durable fact merely because a class or
test exists.

## Observable result

After a normal application restart, committed SQLite rows, appended events, and
stored artifacts remain readable by their existing consumers. Repeating an
order intent through a currently wired adapter uses its deterministic identity.
Explicitly written protection rows survive restart without being inferred or replayed.

## Invariants and trust boundaries

- SQLite is the authoritative local metadata store.
- Event records are append-only.
- Artifact manifests identify immutable persisted content.
- Secrets and dirty runtime state are excluded from sync/export artifacts.
- Protective-barrier storage obeys the immutable/CAS/retention invariants in
  `data_model` §11.
- Exit shadow assignments/outcomes obey the immutable, observe-only and
  fail-closed economics invariants in `data_model` §12.
- A recovery class with no production caller is not a recovery guarantee.
- Backup evidence has three distinct levels: archive created, archive integrity
  verified, and an isolated restore successfully opened and passed the relevant
  application checks. Only the third level proves recoverability. A checksum or
  manifest check alone must not be reported as a successful recovery.

## Failure and recovery semantics

Storage failures must remain visible to their caller and must not be converted
into a successful write. Current startup does not enter a mandatory
`RECOVERY_MODE`, does not guarantee exchange reconciliation before new work,
and does not enforce `SAFE_FREEZE` from the dormant recovery state machine.

Schema migration never initializes old Positions or invents historical trigger
evidence. The wired periodic consumer may explicitly forward-adopt an open,
unprotected local-paper Position by freezing its currently resolved plan at the
adoption instant and starting at the first complete 1-minute bar whose open is
not earlier than that instant. It never evaluates a pre-adoption candle, and a
mid-hold adoption is excluded from the prospective exit-shadow cohort. The
consumer fails closed on a gap, a pre-existing immutable plan without state, or
an unknown physical market and never reinterprets missing trigger evidence as
proof that no barrier was crossed. A recorded trigger may retry the existing
native local close after restart; mismatched close evidence becomes
`integrity_blocked`. If another native path closes an untriggered protected
Position first, the retained cursor becomes terminal `superseded` and is never
evaluated against post-close candles.

Those absent guarantees are a product gap, not current Contract behavior. Their
approved target and rollback boundary are recorded in the Gateway Recovery
Proposal.

Managed SQLite backup/restore must be used for a live WAL database. Copying an
active `app.db` file is not a supported backup or restore path. Restore rehearsal
must target an isolated data root and must not overwrite the active runtime merely
to test the archive.

## CSV export is diagnostic, not migration

`/api/export` exposes two bounded CSV presets:

- `analysis` contains selected trading tables for external analysis.
- `diagnostic` adds selected causal rows, the three physical Agent trace sources
  (`agent_runs`, `agent_run_steps`, `agent_tool_calls`), SQLite audit history, and
  a parsed/redacted config copy.

Neither preset is dependency-closed, transactionally quiesced, or accepted as
restore input. The former `migration` preset is removed and returns
`migration_preset_removed`; silently treating it as a compatibility alias would
preserve the false instance-transfer claim. Operators must use
`POST /api/persistence/backups`, verify the resulting managed archive through
`POST /api/persistence/backups/verify`, and use `BackupService`/`RestoreService`
with an isolated rehearsal for recovery evidence.

Every CSV ZIP contains `export_manifest.json` with its manifest/preset version,
producer and SQLite schema versions, creation time, exact exported row counts,
per-file byte counts and SHA-256 checksums, source classifications, consistency
limit, and explicit excluded-state categories. The manifest marks
`restore.supported=false`. Its exclusions cover unlisted required durable state,
optional history, rebuildable caches, process/lock authority, and credentials.
This list is an honesty boundary for a bounded diagnostic artifact, not a claim
that the export catalog inventories the whole runtime.

Agent step/tool tables are one logical best-effort trace. A diagnostic preset
includes all three physical sources; a custom request that selects only part of
the trace is rejected. Within a run, consumers merge steps and tool calls by
`created_at`, use CSV order as the intra-source tiebreaker, and order a reasoning
step before a tool call on an exact timestamp tie. Retention or producer drops
mean absent telemetry never proves that no reasoning/tool action occurred. Agent
input/output fields may contain private runtime data, so a diagnostic archive
must not be shared blindly even though configuration credentials are redacted.

## Executable evidence

`tests/specs/spec_persistence.py` verifies the current stores, deterministic
identities, wired adapter behavior, and negative tripwires that prevent dormant
recovery components from being presented as active runtime guarantees.

`tests/test_export_routes.py` verifies the removed migration claim, Agent-trace
closure, versioned manifest, row counts/checksums, explicit exclusion classes,
redacted configuration, and the managed backup/restore pointer.

`tests/test_position_protection_storage.py`, `tests/test_protective_barrier_runtime.py`
and the v8-to-v9 migration checks verify storage atomicity, exact-instrument
evaluation, stop-first collision handling, native close linkage and explicit gap
behavior. They do not constitute long-running restart-soak or profitability proof.
