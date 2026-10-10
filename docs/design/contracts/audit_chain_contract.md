# Audit Chain Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `AU` · **Owner code:** `core/aletheia/files/jsonl.py` (file chain),
> `core/aletheia/storage/repositories.py` (Proposal row chain) · **Layer:** L2 trust boundary

## Scope

Aletheia keeps two independent tamper-evident chains: the runtime-home `logs/audit.jsonl`
file family (system event memory, hash-linked line by line across rotations) and the
`trade_proposals` row chain (every Proposal that can reach Risk is hash-linked to its
predecessor). This Contract owns how audit records are written, linked, deduplicated,
mirrored to SQLite `audit_events`, verified and reported. It does not own which business
transitions happen; it owns that the listed trust-boundary transitions leave a record.
Retired sections (former doctor description, retired Learning lifecycle evidence, the local
simulated-account funding rows, former defect narrative) are in
[history](../history/audit_chain_retired_sections.md).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/files/jsonl.py::write_audit` | call | trust-boundary writers, e.g. `core/aletheia/risk/kill_switch.py::activate` |
| `core/aletheia/files/jsonl.py::write_audits_deduplicated` | call | `core/aletheia/portfolio/funding_audit.py::reconcile_funding_audits`, `extensions/observation_lab/canonical.py::mirror_lab_audit_outbox` |
| `core/aletheia/files/jsonl.py::verify_audit_chain` | call | `core/aletheia/diagnostics/probes/storage_trust.py::_probe_audit_chain` (in `core/aletheia/diagnostics/doctor.py::build_report`) |
| `funding_audit_reconciler` | scheduler job, every 5 min, `audit` executor | `core/aletheia/agent_runtime/scheduler.py::_run_funding_audit_reconciler` |
| `core/aletheia/storage/repositories.py::insert_trade_proposal` | call | `core/aletheia/execution/entry_plan_execution.py` (Entry Plan → Proposal) |
| `core/aletheia/storage/repositories_execution.py::validate_trade_proposal_commit_hash` | call | `core/aletheia/risk/engine.py::_verified_original_proposal_commit_hash` |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `logs/audit.jsonl`, `audit.jsonl.1`, `.2`, … | `jsonl.py::write_audit`, `jsonl.py::write_audits_deduplicated` | `verify_audit_chain`, backup/restore, operators | append-only; rotated generations never deleted |
| `logs/.audit.lock` | `jsonl.py::_exclusive_file_lock` | every chain writer, `verify_audit_chain`, `ops/backup/service.py::BackupService.create_backup` | lock file only |
| `audit_events` | `jsonl.py::_write_audit_row` (default mirror); transactional callers via `core/aletheia/storage/repositories.py::insert_audit_event_once` | API/diagnostic readers, funding outbox | append-only rows; `jsonl_mirror_*` columns monotonic for funding outbox rows only |
| `trade_proposals.commit_hash` / `parent_hash` | `repositories.py::insert_trade_proposal` | Risk engine, `core/aletheia/analytics/entry_exit_geometry.py`, `core/aletheia/analytics/trading_mandate_compliance.py` | immutable per row |

## Requirements

`AU-C04` and `AU-C05` predate this numbering and keep their meaning; ids are never reused,
so the remaining ids follow document order around them.

### 1. File family and rotation

#### AU-C01 Size-based rotation that never deletes

`When the active audit.jsonl reaches retention.jsonl_max_mb, the writer shall rename it to the next numeric generation and keep every older generation.`

- WHEN the active file size is at or above the threshold (default 50 MiB if config cannot be read) THEN it is renamed to `audit.jsonl.<max+1>` before the append (`jsonl.py::_rotate_jsonl_if_needed`).
- WHEN the rename raises `OSError` THEN the append still proceeds to the active file (`jsonl.py::_append_jsonl_line`).
- WHEN rotation reads config THEN it does not persist config migrations (`jsonl.py::_max_bytes`).

### 2. Record form

Trust-boundary callers supply `actor`, `source`, `event_type`, `entity_type`, `entity_id`
and `message`; optional `before`/`after` (or `before_state`/`after_state`) carry the change.
`write_audit` injects `timestamp` (UTC ISO-8601) when absent and always injects `_prev_hash`.
It does not validate the caller fields (see Known conformance gaps).

### 3. Hash chain and append-only writes

#### AU-C02 Hash-linked append-only record

`When an audit record is written, the writer shall append exactly one line whose _prev_hash is the SHA-256 of the previous on-disk line across all generations, or 64 zeros for the first record.`

- WHEN two records are written THEN `line[1]._prev_hash == sha256(line[0] bytes)` and the digest is 64 lowercase hex (`jsonl.py::_hash_record`, `_GENESIS_HASH`).
- WHEN the line is hashed THEN the hashed bytes are exactly the written bytes, including `_prev_hash` (`json.dumps(event, default=str, ensure_ascii=False)`).
- WHEN any file in `jsonl.py` is opened for writing THEN the mode is append (`"a"`/`"ab"`), never truncating.
- WHEN a write runs THEN it holds the in-process `_audit_hash_lock` and the cross-process `.audit.lock` while re-reading the chain tail with `use_cache=False`, injecting `_prev_hash` and appending, so no two records share a parent.
- WHEN the JSONL append fails or the lock times out THEN `write_audit` logs a warning and returns `False`; it never reports a failed append as written.

#### AU-C03 Transactional SQLite mirror

`Where the caller has already committed the same AuditEvent inside its state transaction, the writer shall skip only the SQLite mirror and keep the append-only hash-chain write.`

- WHEN `write_audit(event)` runs with the default `mirror_db=True` THEN the event is also inserted into `audit_events`; a mirror failure is logged and does not undo the JSONL append (`jsonl.py::_write_audit_row`).
- WHEN `mirror_db=False` THEN the JSONL append, `_prev_hash` injection and both locks are unchanged and no second `audit_events` row is created.

### 4. Verify across rotation

#### AU-C06 Exact continuity verification

`When the chain is verified, the verifier shall walk every generation oldest-first and report valid=false for any record whose _prev_hash does not equal the hash of the exact preceding record.`

- WHEN 5 valid records exist THEN `verify_audit_chain()` returns `valid=True` and empty `errors`.
- WHEN a later generation's first record does not point at the previous generation's last record THEN the result is `valid=false`; file position or a repeated `_prev_hash` never resets the chain.
- WHEN a line is invalid JSON or carries a malformed `_prev_hash` THEN it is an error with `{file}:{line}` location; a record without `_prev_hash` (pre-chain history) re-anchors without error.
- The result shape is `{"valid", "records", "errors", "error_timestamps", "reanchors"}`; `error_timestamps` parallels `errors` with the record timestamp or `""`.

### 5. Doctor probes

#### AU-C07 Chain-break severity

`When doctor checks the audit chain, the probe shall report trust.audit_chain as ok, info or warn, and never as blocker.`

- WHEN there are no records THEN `info` "no audit records yet".
- WHEN the chain is valid THEN `ok` with the record count.
- WHEN every break has a timestamp at or before `EFFECTIVE_CHAIN_FIX_TS` (`2026-07-03T08:00:00+00:00`) THEN `info` naming the count of historical breaks and zero post-fix breaks.
- WHEN any break is after that timestamp THEN `warn` "chain BROKEN" with up to three locations.
- WHEN breaks are only undateable THEN `warn`; an undateable break is never classified as historical.
- WHEN funding outbox rows are `pending` or `failed` THEN `trust.audit_mirror_backlog` is `warn` with count and oldest timestamp; otherwise `ok` (`storage_trust.py::_probe_funding_audit_mirror`).

### 6. Trust-boundary event invariant

#### AU-C08 Trust-boundary transitions are audited

`When a listed trust-boundary transition completes, the owning code path shall write an audit.jsonl record through write_audit or write_audits_deduplicated.`

| Transition | `event_type` | Writer |
|---|---|---|
| Live unlock / lock | `live_unlocked` / `live_locked` | `core/aletheia/server/routes/live.py::live_unlock` / `live_lock` |
| Trading brake on / off (scope `global`/`demo`/`live`) | `trading_brake_activated` / `trading_brake_deactivated` | `core/aletheia/risk/kill_switch.py::activate` / `deactivate` (route `core/aletheia/server/routes/risk.py::set_kill_switch`) |
| Secret change (CLI) | `secret_rotated` | `core/aletheia/cli/secrets_cmd.py::set_cmd` |
| Agent Shell command, allowed or denied | `agent_shell_command` (`message` prefix `ALLOWED`/`DENIED`) | `core/aletheia/agent_shell/audit.py::record_command`; `FORBIDDEN_INTENTS` (`core/aletheia/agent_shell/registry.py`) are denied in `core/aletheia/agent_shell/commands.py` |
| External position adoption | `position_adopted` (`after_state` carries `commit_hash` + `parent_hash`) | `core/aletheia/portfolio/position_reconciler.py::adopt_position` |
| Exchange close-cost / funding settlement | `exchange_position_fee_completed` / `exchange_position_funding_settled` | `core/aletheia/portfolio/broker_funding_settlement.py::materialize_exchange_close_costs` |
| Exchange funding history expiry | `exchange_position_funding_history_expired` | `broker_funding_settlement.py::_terminalize_funding_history_expired` |
| Historical filled-Order chain recovery | `order_reconciled` / `order_reconciliation_inconclusive` | `core/aletheia/execution/reconciliation.py::_write_filled_gap_event` |
| In-place update rollback restored data | `update_rollback_restored` | `ops/update/auto.py::_restore_old_runtime` |
| Observation Lab lifecycle | Lab audit outbox events | `extensions/observation_lab/canonical.py::mirror_lab_audit_outbox` (event types owned by the [Observation Lab contract](observation_lab_contract.md)) |

- WHEN `routes/live.py::live_unlock`, `live_lock` or `routes/risk.py::set_kill_switch` is scanned THEN each calls `write_audit` directly or through `kill_switch`.
- `paper_position_funding_settled`, `paper_position_economics_repaired` and `paper_position_economics_corrected` are historical event types: `core/aletheia/portfolio/funding_audit.py::_OUTBOX_EVENT_TYPES` still lists them so existing rows can be read and mirrored, but no code path produces new ones.

#### 6.1 Append-only update rollback

#### AU-C09 Rollback keeps the live audit family

`When an in-place update rollback restores a checkpoint, the restore shall keep every live audit.jsonl generation and then append one update_rollback_restored record.`

- WHEN the target holds any audit generation THEN the whole live family is kept byte for byte and listed in `preserved_files`; no generation is filled from the checkpoint (`ops/backup/restore.py::RestoreService.restore`, `_is_audit_generation`).
- WHEN post-checkpoint records exist and a rotation happened THEN after restore the chain still verifies.
- `audit_events` returns to the checkpoint with `app.db` and is not rebuilt from JSONL; the appended `update_rollback_restored` record marks the boundary ([persistence contract PR-C12](persistence_recovery_contract.md#pr-c12-audit-family-is-all-or-nothing-and-the-rollback-is-recorded)).

#### 6.2 Partial-tail repair

#### AU-C10 Short writes are finished append-only or fail closed

`If the active audit.jsonl ends without a final newline, then the writer shall finish the tail only by appending, or refuse the write.`

- WHEN the tail is a complete JSON object THEN only `\n` is appended (`jsonl.py::_repair_incomplete_final_line`).
- WHEN a deduplicated retry's stable payload is the unique byte-extension of the tail THEN only the missing suffix is appended.
- WHEN neither holds (including any ordinary `write_audit`, which has no stable identity) THEN `OSError("…unrepairable partial record")`; nothing is truncated or guessed, and outbox rows stay pending.

#### 6.3 Durable lifecycle audit mirror

#### AU-C04 Stable dedup id makes retries idempotent

`Where a caller passes a stable dedup_id, the writer shall append the record at most once across all generations and report whether it is present in the chain.`

- WHEN `dedup_id` is given THEN `mirror_db` must be `False` and `event["id"] == dedup_id`, else `ValueError`.
- WHEN the id already exists in any generation (checked under the file lock) THEN `write_audit` returns `True` without appending.
- WHEN the append fails THEN `write_audit` returns `False` and a later retry can append it.
- WHEN `write_audits_deduplicated` receives a batch THEN ids must be unique and non-empty; the chain is scanned once and the returned set holds ids present or appended; a bounded `lock_timeout_sec` raises no exception to the caller and returns only completed ids.

#### 6.4 Funding audit outbox

#### AU-C05 Funding audits are durable first, mirrored later

`When a funding settlement, close-cost completion or funding-history expiry commits, the system shall insert its AuditEvent with a stable id and jsonl_mirror_status='pending' in the same SQLite transaction, and mirror it to JSONL later without replaying accounting.`

- The stable id is `funding-audit-v1:` + SHA-256 of `[position_id, event_type, accounting_version, fingerprint, transition_fingerprint, occurred_at]` (`funding_audit.py::funding_audit_id`); retry processing time never enters it.
- WHEN `insert_pending_funding_audit` runs outside a transaction THEN `RuntimeError`; WHEN the id exists with different content THEN `ValueError("funding audit identity collision")`.
- WHEN `funding_audit_reconciler` runs THEN at most 100 `pending`/`failed` rows are passed to `write_audits_deduplicated(lock_timeout_sec=1.0)`; appended or already-present ids become `mirrored`, the rest `failed` with only the error class stored.
- WHEN a record was appended but the process crashed before the ack THEN the next run finds the id in the chain and acks it without a second append.
- `jsonl_mirror_*` columns are `NULL` (unmanaged) for every non-funding and pre-outbox row; migration never guesses their JSONL state.
- WHEN the backlog is non-zero THEN `/api/health` reports `funding_audit_mirror_backlog:<n>` (`core/aletheia/agent_runtime/health.py`) and doctor warns (AU-C07); accounting results never change because of mirror failure.

#### 6.5 Filled-gap reconciliation identity

#### AU-C11 One record per observed filled-gap outcome

`When historical filled-Order chain recovery records an outcome, the reconciler shall derive a stable id from entity_id, event_type and reason, so repeated polling leaves one SQLite row and one JSONL record per distinct outcome.`

- The id is `filled-gap:` + the first 24 hex of SHA-256 of canonical JSON `{entity_id, event_type, reason}`.
- SQLite uses `insert_audit_event_once`; JSONL uses `write_audit(..., mirror_db=False, dedup_id=id)`; each sink fails independently and converges on a later run.
- WHEN the Order, result type or reason differs THEN the id differs. This identity never changes Order state, exchange queries, Risk or Gateway behavior.

### 7. Proposal row chain

#### AU-C12 Global Proposal hash chain

`When a Proposal is inserted, the repository shall link it to the newest hashed Proposal and store a commit_hash over its immutable fields, rejecting a precomputed hash that does not match or a stale parent.`

- `commit_hash` = first 16 hex of SHA-256 canonical JSON of the Proposal excluding `status` and `commit_hash` (`repositories_execution.py::_compute_proposal_hash`; `_PROPOSAL_HASH_EXCLUDES`), so `pending → executed/rejected` never invalidates it.
- Parent lookup and insert share one `transaction(immediate=True)`; WHEN the caller supplies `commit_hash` with a `parent_hash` that is no longer the tip THEN `ValueError("proposal parent hash is stale")`.
- `parent_hash = NULL` is valid only for the first hashed row or rows written before the chain existed.
- WHEN Risk evaluates a stored Proposal whose content no longer matches its `commit_hash` THEN `ProposalIntegrityError` (`risk/engine.py::_verified_original_proposal_commit_hash`).
- `position_adopted` audit records carry the adopted Proposal's `commit_hash` and `parent_hash`. Any future synthetic Proposal path reuses these field names.
- No doctor probe scans the whole Proposal chain; continuity is proven by DM-C02 only.

### 8. Change-control guard

#### AU-C13 No bypass of the chain writer

`If any caller attempts to append to audit.jsonl through append_jsonl, then the writer shall raise ValueError naming write_audit.`

`core/aletheia/files/jsonl.py` is a trust-boundary controlled module
([trust and change control §6](../../governance/policies/trust_and_change_control.md)). These changes are
forbidden: opening audit files in a truncating mode; removing `_audit_hash_lock` or
`_exclusive_file_lock`; removing `_prev_hash` injection; changing the bytes `_hash_record`
hashes; writing audit records through `append_jsonl`.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| JSONL append `OSError` / lock timeout | log warning; `write_audit` returns `False`; SQLite mirror still attempted | caller return value; WARN log | record absent, never reported written |
| SQLite mirror failure | log warning; JSONL record stands | WARN log | — |
| Unrepairable partial tail | `OSError`; no truncation | dedup batch returns fewer ids; funding rows `failed` | backlog count, not success |
| Chain break | doctor `info` (pre-fix) / `warn` (post-fix or undateable); startup not blocked | `trust.audit_chain` | `info` with zero records means no records, not valid history |
| Funding mirror backlog | rows stay `pending`/`failed`; retried every 5 min | `/api/health` issue, doctor `trust.audit_mirror_backlog` | count ≥ 1, never hidden |
| Proposal hash mismatch | `ValueError` at insert; `ProposalIntegrityError` at Risk | Risk rejection | — |

## Authority

The audit chain records trust-boundary transitions; it may never alter Risk limits,
credentials, Live unlock or Gateway submission, and an audit record grants no authority.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| AU-C01 | `tests/test_audit_hashchain.py::test_audit_rotation_does_not_run_config_migration` |
| AU-C02 | AU-S01, AU-S02 (`tests/specs/spec_audit_chain.py`); `tests/test_audit_hashchain.py::test_concurrent_audit_writes_preserve_single_hash_chain`, `::test_write_audit_ignores_stale_process_cache` |
| AU-C03 | AU-S06 |
| AU-C04 | AU-C04 (`tests/specs/spec_audit_chain.py::test_audit_writer_reports_failure_and_deduplicates_retry`); `::test_audit_batch_scans_chain_once` |
| AU-C05 | AU-C05 (`tests/specs/spec_audit_chain.py::test_funding_audit_reconciler_acks_append_before_crash_without_duplicate`); `::test_funding_audit_reconciler_persists_only_error_class`, `::test_funding_audit_reconciler_bounds_jsonl_file_lock` |
| AU-C06 | AU-S03; `tests/test_audit_hashchain.py::test_verify_audit_chain_spans_rotated_files`, `::test_verify_detects_tampered_rotated_tail` |
| AU-C07 | `tests/test_diagnostics_honesty_fixes.py::test_doctor_audit_chain_flags_undateable_breaks_as_warn` |
| AU-C08 | AU-S05 (live/brake routes only) |
| AU-C09 | AU-S07 |
| AU-C10 | `tests/specs/spec_audit_chain.py::test_audit_retry_repairs_partial_tail_by_appending_only`, `::test_ordinary_audit_does_not_guess_identity_of_partial_tail`, `::test_audit_retry_preserves_complete_record_missing_final_newline` |
| AU-C11 | PL-HCR-07 (`tests/specs/spec_pipeline_historical_chain_recovery.py`) |
| AU-C12 | DM-B06, DM-C02 (`tests/specs/spec_data_model.py`); PL-ADOPT-09 (`tests/specs/spec_pipeline_adopted_position.py`) |
| AU-C13 | AU-S04 |

## Known conformance gaps

- **Caller fields not enforced (AU §2).** `jsonl.py::write_audit` accepts any dict; the required
  `actor`/`source`/`event_type`/`entity_type`/`entity_id`/`message` set is caller discipline only.
  `jsonl.py::_write_audit_row` builds `AuditEvent(**payload)`, so a malformed event fails only the
  SQLite mirror (logged), not the JSONL append.
- **AU-C08 coverage is partial.** AU-S05 scans only `routes/live.py` and `routes/risk.py`; the
  secret, Agent Shell, adoption, funding, filled-gap and rollback writers are covered by their own
  focused tests, not by the trust-boundary scan.
- **No whole-chain Proposal probe (AU-C12).** Only per-row validation at Risk and DM-C02 exist; a
  `parent_hash` discontinuity in stored rows is not reported by doctor.

## Decision references

- Proposal row chain: TaG P0-1 / P1-2 (2026-06-29).
- Trading brake event names replace `kill_switch_*`: #361.
- Undateable breaks must not be classified historical: #179.
- Rollback keeps the live audit family: #1910; owner decision 2026-10-09 (#3765) that
  in-place rollback never deletes post-checkpoint history.
