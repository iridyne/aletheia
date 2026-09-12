# Persistence Contract

## Purpose and current scope

Aletheia durably records local metadata, events, artifacts, order identities,
and selected policy/learning state. These stores survive a normal process
restart. This Contract does not claim automatic Gateway recovery, exchange
reconciliation before startup, or `SAFE_FREEZE` enforcement.

Schema v9 and v11 retain [archived simulation evidence](../history/local_simulated_protection.md).
Its tables remain available to historical readers; activation, simulation,
settlement and recovery producers are retired.

Schema v12 retains both immutable `ufc-fixed-look/v1` plans and the current
`ufc-fixed-look/v2` conditional-randomization plans. The migration rebuilds only
`learning_inference_decisions` under a savepoint, preserves its rows and storage
guards, and grants no Learning or trading authority.

The former PS recovery design is
[historical](../history/gateway_recovery.md), superseded by the accepted
[Slim Core retirement](../proposals/slim_core_legacy_gateway_retirement.md).
Its executable writer, recovery, mock-exchange, session and leader-lock modules
are removed. The corresponding `ps_*` rows and read-only history endpoints remain;
their old lifecycle states do not grant or deny current startup/trading authority.

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
| `position_exit_plan_versions` / `position_protection_state` / `position_protection_triggers` | read-only historical protection and qualification reports |
| `exit_breakeven_shadow_assignments` / `exit_breakeven_shadow_outcomes` | read-only historical paired-exit reports |

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
- Archived simulation evidence retains its immutable storage and original identity;
  readers do not restore its retired execution or settlement authority.
- Historical PS Order/Fill/Position, intent, session, lock and reconciliation
  tables have no active producer or recovery/authorization lifecycle. Startup
  readiness requires the canonical Core tables and evaluates current `orders`,
  `positions` and reconciliation incidents, not archived PS status values.
- Backup evidence has three distinct levels: archive created, archive integrity
  verified, and an isolated restore successfully opened and passed the relevant
  application checks. Only the third level proves recoverability. A checksum or
  manifest check alone must not be reported as a successful recovery.

## Failure and recovery semantics

Storage failures must remain visible to their caller and must not be converted
into a successful write. Current startup does not enter a mandatory
`RECOVERY_MODE`, does not guarantee exchange reconciliation before new work,
and does not enforce `SAFE_FREEZE` from the retired PS state machine.

Schema migration never initializes old Positions or invents historical trigger
evidence. Archived protection and experiment records are read without resuming
their former consumers.

This retirement does not claim those additional guarantees. Current ambiguous
submissions and unresolved close claims retain the canonical Core reconciliation
and pause rules in the [execution contract](execution_contract.md).

Managed SQLite backup/restore must be used for a live WAL database. Copying an
active `app.db` file is not a supported backup or restore path. Restore rehearsal
must target an isolated data root and must not overwrite the active runtime merely
to test the archive.

Managed backups have two explicit profiles. The HTTP `POST /api/persistence/backups`
surface creates only an `online` non-recovery snapshot; it must not be described as
a coherent rollback point. An `authoritative` checkpoint may be created only after
the caller has quiesced runtime writers. It holds the audit writer lock while
capturing the WAL-safe SQLite snapshot and the complete rotated `audit.jsonl`
inventory, and records the SQLite identity, Lab DB identity when present, exact
audit head, and a derived checkpoint identity in `manifest.yaml`. Restore paths
used for update rollback reject an online, legacy, malformed, or mismatched
checkpoint instead of combining independently valid stores.

## Bundle imports do not create trading authority

Under the accepted [research bundle boundary](../proposals/slim_core_research_bundle_import.md),
`POST /api/persistence/import` admits only namespaced research: Serenity themes,
evidence, claims and opportunities, plus `ps_lessons` and `ps_skills`. Both
preview and merge reject Core trading tables and retired PS runtime tables;
checksums are integrity evidence, never permission to create Proposal, Order,
Fill, Position or recovery authority. Failed table preflight or transactional
insertion returns before copying bundle artifacts.

Historical Proposal and PS runtime snapshot exports/readers remain available,
without reciprocal import permission. Existing rows are not rewritten or
deleted. Research conflict/duplicate handling and safe staging remain; this
merge service is not an atomic filesystem/database restore. Full managed
backup/restore retains its separate explicit, isolated-verification workflow.

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
identities, wired adapter behavior, and negative containment checks preventing
retired recovery writers or Legacy startup authority from returning.

`tests/test_export_routes.py` verifies the removed migration claim, Agent-trace
closure, versioned manifest, row counts/checksums, explicit exclusion classes,
redacted configuration, and the managed backup/restore pointer.

`tests/test_exit_trailing_qualification.py` and `tests/test_exit_breakeven_shadow.py`
verify that archived experiment evidence remains readable without new observations
or writes. They do not prove current exchange protection or profitability.
