# Persistence and Recovery Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `PR` · **Owner code:** `core/aletheia/storage/` (stores), `ops/backup/` (managed
> backup/restore), `extensions/sync/importer.py` (bundle import), `core/aletheia/server/routes/export.py` (CSV export)

## Scope

Durable local state is SQLite (`app.db`, the authoritative metadata store) plus the
append-only EventStore. This Contract owns what survives a restart, how a managed
checkpoint is taken and restored (including in-place update rollback), which research
bundles may be imported, and what a CSV export is (diagnostic, never restore input). It
does not own automatic Gateway recovery, exchange reconciliation before startup or
`SAFE_FREEZE`: the former PS recovery graph is [retired](../history/gateway_recovery.md);
current ambiguous submissions and close claims follow the [execution contract](execution_contract.md).
Former scope narrative is in [history](../history/persistence_recovery_retired_sections.md).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/storage/event_store.py::get_event_store` | call | `extensions/learning/lessons.py` (append); `core/aletheia/agent_runtime/scheduler.py` (mirror retry via `reconcile_jsonl`) |
| `POST /api/persistence/backups` (`core/aletheia/server/routes/persistence.py::create_backup`) | API route | operator; online profile only |
| `POST /api/persistence/backups/verify` (`persistence.py::verify_backup_endpoint`) | API route | operator |
| `ops/backup/service.py::BackupService.create_backup(profile="authoritative")` | call | `ops/update/auto.py` (pre-activation checkpoint), `ops/update/applier.py`, `ops/maintenance/database_capacity.py` |
| `ops/backup/restore.py::RestoreService.restore` | call | `ops/update/auto.py::_restore_old_runtime` (update rollback) |
| `POST /api/persistence/import` (`persistence.py::import_bundle`) | API route | operator; `extensions/sync/importer.py::ImportService` |
| `POST /api/export/download` (`core/aletheia/server/routes/export.py::download_export`) | API route | operator / Settings |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| SQLite application tables (`app.db`) | repositories under `core/aletheia/storage/` | runtime services, API routes, analytics, reconciliation | per-table contract |
| `ps_event_log` + `events/*.jsonl` mirror | `core/aletheia/storage/event_store.py::EventStore.append` / `append_many` | `/api/persistence/events`, learning/policy readers | append-only; `jsonl_mirror_status` monotonic |
| `orders.client_order_id` | `core/aletheia/execution/coordinator.py::execute_order`, `::_claim_close` | adapters, reconciliation | immutable per Order |
| `ps_gateway_sessions`, `ps_reconciliation_reports`, other `ps_*` runtime tables | none (retired) | `persistence.py::list_gateway_sessions`, `::list_reconciliation_reports` | read-only history |
| `position_exit_plan_versions`, `position_protection_state`, `position_protection_triggers`, `exit_breakeven_shadow_assignments`, `exit_breakeven_shadow_outcomes` | none (retired experiments) | historical protection / paired-exit reports | read-only history |
| `backups/*` archive + `manifest.yaml` | `ops/backup/service.py::BackupService.create_backup` | `core/aletheia/backup/verify.py::verify_backup`, `RestoreService` | immutable archive |

An in-memory object is never a durable fact because a class or test exists; the
consumer-less `ArtifactStore` is [retired](../history/artifact_store.md).

## Requirements

### Invariants — startup authority and identity

#### PR-C01 Legacy is not startup authority

`The startup and process lifecycle shall not import the retired PS state owners or use ps_* tables as admission evidence.`

- WHEN `core/aletheia/gateway/lifecycle.py` or `core/aletheia/agent_runtime/startup_behavior_gate.py` is read THEN it names none of `GatewayRecoveryService`, `GatewayStateMachine`, `GatewayStateStore`, `ps_order_intents`, `ps_orders`, `ps_positions`.
- WHEN `GET /api/persistence/gateway/status` is called THEN it returns `{"status": "not_available", "freeze_reason": null}` (`persistence.py::get_gateway_status`); it never reports a recovery or freeze state.
- Startup does not enter a mandatory `RECOVERY_MODE`, does not guarantee exchange reconciliation before new work, and does not enforce `SAFE_FREEZE`.

#### PR-C02 Retired PS history is read-only

`The persistence API shall expose archived ps_* rows only through read-only endpoints, and no code path shall write PS Order, Fill or Position rows.`

- WHEN `/api/persistence/gateway/sessions` or `/gateway/reconciliation-reports` is read THEN archived rows are returned unchanged and `ps_orders`, `ps_fills`, `ps_positions` stay empty.
- The retired PS writer, recovery, mock-exchange, session and leader-lock modules are absent.

#### PR-C03 Deterministic order identity

`When the Execution Gateway creates an Order, the coordinator shall derive client_order_id deterministically from durable state, so a repeated intent reuses the same exchange identity.`

- WHEN an entry Order is built THEN `client_order_id = proposal_id` (`coordinator.py::execute_order`).
- WHEN a close is claimed THEN `client_order_id` comes from `core/aletheia/execution/pending_close_recovery.py::next_close_client_oid` (`close-<position_id>` plus a retry suffix) inside `transaction(immediate=True)`.

### Durable state — SQLite and EventStore

#### PR-C04 Storage failures stay visible

`If a SQLite write fails, then the store shall surface the error to its caller and never report a successful write.`

- WHEN a normal restart happens THEN committed SQLite rows and appended events remain readable by their existing consumers; explicitly written protection rows are read, not inferred or replayed.
- Schema migration never initializes old Positions or invents historical trigger evidence.

#### PR-C05 EventStore append is idempotent and SQLite-first

`When an event is appended, the EventStore shall commit it to ps_event_log first, return the original event for a repeated idempotency_key, and mirror only newly inserted or still-pending rows to the events JSONL.`

- WHEN `append()` repeats an `idempotency_key` THEN the original `event_id` is returned, one SQLite row exists and one JSONL line is written.
- WHEN `append_many()` contains existing keys THEN existing events are returned and JSONL is written only for new rows.
- WHEN a primary-key clash occurs without an `idempotency_key` THEN `IntegrityError` propagates and no JSONL line is written (`EventStore._insert_row`).
- WHEN the append runs inside an outer transaction THEN the JSONL mirror is deferred; `EventStore.reconcile_jsonl` (scheduler and `core/aletheia/server/app.py`) retries `pending`/`failed` mirrors, at most 500 per call.

### Backup and restore

#### PR-C06 Three levels of backup evidence

`The system shall distinguish archive created, archive integrity verified, and isolated restore opened and checked, and shall treat only the third as proof of recoverability.`

- WHEN `verify_backup` passes THEN the result is integrity evidence only; a checksum or manifest check is never reported as a successful recovery.
- A restore rehearsal targets an isolated data root and never overwrites the active runtime to test an archive.
- Copying an active `app.db` is not a supported backup; `BackupService._snapshot_sqlite_db` uses the SQLite backup API.

#### PR-C07 HTTP backups are online, non-recovery snapshots

`When POST /api/persistence/backups is called, the route shall create only an online snapshot marked recovery_eligible=false and reject any other profile with HTTP 400.`

#### PR-C08 Authoritative checkpoint binds every store

`When an authoritative checkpoint is created, the backup service shall hold the SQLite write permit and the audit lock while snapshotting app.db and the full rotated audit.jsonl inventory, and record their identities in manifest.yaml.`

- The manifest records `sqlite_identity`, `lab_sqlite_identity` (when `observation-lab/lab.db` exists), `audit_head_hash`, `audit_inventory`, `checkpoint_identity`, `profile=authoritative`, `recovery_eligible=true`.
- WHEN `app.db` is missing THEN `RuntimeError("authoritative_backup_requires_app_db")`; Lab-authority requirements are owned by the [Observation Lab backup contract](observation_lab_backup_contract.md).
- The caller quiesces runtime writers first; the write permit (`acquire_write_lock`, 30 s) turns that claim into an executable boundary.

#### PR-C09 Rollback accepts only a verified recovery checkpoint

`When update rollback restores data, the restore shall require an authoritative checkpoint and reject an online, legacy, malformed or mismatched archive.`

- `_restore_old_runtime` calls `RestoreService().restore(..., require_recovery_checkpoint=True)`; WHEN `verify.recovery_eligible` is false or the checkpoint identity does not match THEN the restore fails before touching the target.

### Failure and recovery semantics — update rollback

#### PR-C10 Restore is append-only outside the rollback point

`When a checkpoint is restored, the restore shall replace only app.db and observation-lab/lab.db, and for every other archived file write it only if absent, never overwriting or deleting a target file.`

- WHEN `app.db`/`lab.db` is replaced THEN its `-wal`/`-shm` sidecars are set aside (`restore.py::_ROLLBACK_POINT`).
- WHEN a target file exists outside the rollback point THEN it is unchanged and listed in `preserved_files`; files created after the checkpoint are never deleted.
- WHEN a destination (or `logs/`) is a symlink THEN `RestoreSecurityError`; a failed restore rolls back its own writes and reports `data_restore_failed` or `data_restore_rollback_incomplete`.

#### PR-C11 Live configuration is never overwritten

`When a restore meets a config.yaml, the restore shall keep an existing live file byte for byte and never rehydrate redacted fields.`

- WHEN the target has `config.yaml` THEN `config_disposition=preserved_live`.
- WHEN the target has none THEN the archive's redacted copy is written: `restored_redacted`.
- WHEN the archive has none THEN `absent_in_checkpoint`.

#### PR-C12 Audit family is all-or-nothing and the rollback is recorded

`When the target already holds any audit.jsonl generation, the restore shall keep the whole live audit family, and the rollback shall then append one update_rollback_restored audit record.`

- The record carries backup name, `checkpoint_identity`, restored and preserved counts, and `config_disposition` (`ops/update/auto.py::_restore_old_runtime`).
- The restored `audit_events` table ends at the checkpoint and is not rebuilt from JSONL (see [audit chain AU-C09](audit_chain_contract.md#au-c09-rollback-keeps-the-live-audit-family)).

### Bundle import

#### PR-C13 Imports admit research only

`When a bundle is previewed or merged through POST /api/persistence/import, the importer shall accept only serenity_themes, serenity_evidence, serenity_claims, serenity_opportunities, ps_lessons and ps_skills, and abort before any mutation on a failed preflight.`

- WHEN a bundle contains Core trading tables or retired PS runtime tables THEN preview and merge reject them (`extensions/sync/importer.py::_IMPORT_ALLOWED_TABLES`).
- WHEN a row is not a JSON object, has unknown columns or an empty primary key, or checksums are missing THEN the import aborts without DB mutation and before copying artifacts.
- WHEN an archive path is outside `data_dir/backups` or `data_dir/exports` THEN it is rejected (`persistence.py::_resolve_archive_path`).
- Checksums are integrity evidence, never permission to create Proposal, Order, Fill, Position or recovery authority. The merge is not an atomic filesystem/database restore.
- The `ps_lessons`/`ps_skills` allowance changes when [spec 0001](../specs/0001-learning-loop/spec.md) replaces lessons with the Playbook.

### CSV export

#### PR-C14 CSV export is diagnostic, never restore input

`When a CSV export is requested, the export route shall produce only the analysis or diagnostic preset or an explicit dataset list, and reject the removed migration preset with migration_preset_removed.`

- WHEN `preset="migration"` THEN HTTP 400 with code `migration_preset_removed` and the managed backup pointer.
- WHEN both `preset` and `include_datasets` are given THEN `ambiguous_export_request`.
- `diagnostic` adds causal rows, the three Agent trace tables, `audit_events` and a redacted config copy.

#### PR-C15 Every export carries an honest manifest

`When a CSV ZIP is built, the export route shall include export_manifest.json with restore.supported=false, exact row counts, per-file byte counts and SHA-256, and the excluded-state classes.`

- The manifest names preset/version, producer and schema versions, creation time, `consistency=best_effort_table_by_table`, and points recovery to `/api/persistence/backups`, `/backups/verify` and `ops.backup.restore.RestoreService`.

#### PR-C16 Agent trace is exported whole

`If a custom export selects only part of agent_runs, agent_run_steps and agent_tool_calls, then the export route shall reject it with partial_agent_trace.`

- Consumers merge steps and tool calls by `created_at`, CSV order breaks intra-source ties, and a step precedes a tool call on an exact tie. Absent rows never prove no action occurred; trace fields may hold private runtime data.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| SQLite write error | propagate to caller | caller error | write absent, never success |
| EventStore JSONL mirror failure | SQLite row stands; mirror `pending`/`failed`, retried | `reconcile_jsonl` counts | — |
| Online or mismatched checkpoint used for rollback | restore refused | `data_restore_failed` + errors | — |
| Restore step failure | own writes rolled back | `data_restore_failed` / `data_restore_rollback_incomplete` | — |
| Symlinked restore destination | `RestoreSecurityError` | restore errors | — |
| Import preflight failure | abort before mutation | import result errors | — |
| Removed / ambiguous export request | HTTP 400 with code | `migration_preset_removed`, `ambiguous_export_request`, `partial_agent_trace` | — |
| `/gateway/status` | constant | `not_available` | never a fabricated recovery state |

## Authority

Persistence stores and restores state; a restore, import, checksum or export never creates
Proposal, Order, Fill, Position, Risk, Live-unlock or credential authority.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| PR-C01 | PR-C01 (`tests/specs/spec_persistence.py::test_legacy_recovery_does_not_control_startup`); `::test_gateway_status_does_not_invent_dormant_recovery_state` |
| PR-C02 | PR-S01; `tests/specs/spec_persistence.py::test_retired_gateway_history_remains_readable_without_lifecycle_writes` |
| PR-C03 | PR-S02 (generator existence only — see gaps) |
| PR-C04 | PR-S06 (`IntegrityError` propagates); `tests/test_exit_trailing_qualification.py`, `tests/test_exit_breakeven_shadow.py` (archived rows readable) |
| PR-C05 | PR-S03, PR-S05, PR-S06 |
| PR-C06 | `tests/test_backup_wal_safety_fix.py::test_backup_captures_wal_committed_rows` (snapshot half; the three-level reporting rule has no executable claim) |
| PR-C07 | `tests/test_backup_checkpoint_boundary.py::test_online_backup_is_explicitly_non_recovery` |
| PR-C08 | `tests/test_backup_checkpoint_boundary.py::test_authoritative_backup_records_lab_and_audit_identity` |
| PR-C09 | `tests/test_backup_checkpoint_boundary.py::test_restore_requires_authoritative_profile`; `tests/test_auto_update.py` (`require_recovery_checkpoint=True`) |
| PR-C10 | PR-S07 |
| PR-C11 | PR-S08 |
| PR-C12 | AU-S07 (`tests/specs/spec_audit_chain.py`); `tests/test_auto_update.py` (`update_rollback_restored`) |
| PR-C13 | PR-S01; `tests/test_io_honesty_fixes.py::test_import_missing_checksums_aborts_without_mutation` |
| PR-C14 | `tests/test_export_routes.py::test_removed_migration_preset_fails_with_supported_recovery_pointer`, `::test_download_rejects_ambiguous_preset_and_custom_dataset_request` |
| PR-C15 | `tests/test_export_routes.py::test_export_manifest_is_versioned_non_restorable_and_checks_every_payload` |
| PR-C16 | `tests/test_export_routes.py::test_custom_export_rejects_partial_agent_trace`, `::test_download_diagnostic_preset_contains_complete_physical_agent_trace` |

## Known conformance gaps

- **PR-C03 is unverified behaviorally; generators are dead code.** PR-S02 only asserts that
  `core/aletheia/gateway/client_order_id.py::make_client_order_id` and `::make_idempotency_key`
  exist; neither has a production caller (the Gateway uses `proposal_id` and
  `next_close_client_oid`). Converge: delete the generators and point PR-S02 at the real derivation.
- **Retired endpoints still mounted.** `persistence.py::get_gateway_status` is a constant, and
  `/gateway/sessions`, `/gateway/reconciliation-reports` read retired tables; `/lessons*`, `/skills`,
  `/policies*` serve the PS lesson lifecycle that spec 0001 replaces.
- **Second "backup" path.** `POST /api/persistence/export` with `mode="full-backup"`
  (`extensions/sync/exporter.py::ExportService._export_full`) copies `app.db` and `events/` into a
  sync bundle that is neither a managed checkpoint nor importable as Core state; two backup
  shapes exist for one capability.
- **CSV catalog residue.** `export.py::_DATASETS` still lists `paper_account_events`, `pi_decisions`
  and `opportunities` from retired scopes.

## Decision references

- PS recovery retirement: [Slim Core legacy Gateway retirement](../proposals/slim_core_legacy_gateway_retirement.md).
- Research-only import boundary: [Slim Core research bundle import](../proposals/slim_core_research_bundle_import.md).
- EventStore idempotency: #247. WAL-safe snapshot: #59, #287. Authoritative checkpoint requires `app.db`: #1444.
- Append-only restore #1441, live config preserved #1621, audit family preserved #1910, sidecars set aside #1598, symlink rejection #1618.
- Owner decision 2026-10-09 (#3765): in-place rollback never deletes post-checkpoint history.
