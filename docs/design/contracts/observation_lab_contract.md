# Observation Lab Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `OL` · **Owner code:** `extensions/observation_lab/` (`canonical.py` resolver and
> transport, `runner.py` cycle, `checkpoint.py` joint checkpoint)

## Scope

Observation Lab is the evidence store for bounded prospective observations. On the trading
mainline it does one thing every planning pass: when the Entry Planner builds an entry
analysis context, the Lab resolver decides whether the optional entry-learning treatment
applies and appends one delivery receipt to `app.db`; a one-minute bridge job moves those
receipts into the physically separate `observation-lab/lab.db` and proves the two stores
still agree. The same joint checkpoint gates source updates. Lab grants no Risk, Gateway,
credential, order, Position or Live authority. Experiment authority (`legacy` vs `lab`) is
owned by [observation_experiment_ownership_v2.md](observation_experiment_ownership_v2.md);
counterfactual evidence rules by [counterfactual_contract.md](counterfactual_contract.md);
learning semantics by [spec 0001](../specs/0001-learning-loop/spec.md), which reuses the Lab
assignment/receipt machinery (LL-R16).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `extensions/learning/entry_context.py::resolve_entry_learning_treatment` → `extensions/observation_lab/canonical.py::resolve_observation_treatment` | call | `core/aletheia/trading/entry_analysis.py::build_entry_analysis_context` via `core/aletheia/trading/entry_learning.py::resolve_entry_learning_context` (loader bound in `core/aletheia/main.py`) |
| `extensions/learning/experiments.py::apply_numeric_treatment` / `apply_boolean_treatment` → `resolve_observation_treatment` | call | learning/knowledge helpers (`extensions/knowledge/guards.py`, `extensions/learning/sizing_evidence.py`); learning scope belongs to spec 0001 |
| `core/aletheia/agent_runtime/scheduler.py::_run_observation_lab_bridge` → `extensions/observation_lab/runner.py::settle_observation_continuity` | scheduler job `observation_lab_bridge`, every 1 min | scheduler |
| `core/aletheia/agent_runtime/scheduler.py::_run_filter_controller_observe` → `extensions/observation_lab/runner.py::run_lab_windows` | scheduler job `filter_controller_observe` | scheduler, registered only while `observation.execution_owner=main` |
| `extensions/observation_lab/runner.py::observation_settlement_barrier` | call | `ops/update/auto.py`, `ops/update/continuity.py` (source update) |
| `extensions/observation_lab/kernel.py::invalidate_active_for_restore` | call | `ops/update/auto.py` (update rollback restore) |
| `core/aletheia/cli/observe_cmd.py::observation_overview` | CLI `aletheia observe overview` | operator |
| `core/aletheia/cli/observe_cmd.py::observation_checkpoint` | CLI `aletheia observe checkpoint [--verify-only] [--acknowledge]` | operator |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `app.db` `observation_delivery_outbox` | `canonical.py::append_delivery_receipt` (inside `resolve_observation_treatment`) | `canonical.py::import_delivery_receipts`, `checkpoint.py::compute_joint_checkpoint` | append; `acknowledged_at` set once; acknowledged rows pruned by `prune_observation_delivery_outbox` |
| `lab.db` `observation_exposures`, `observation_import_cursors` | `canonical.py::import_delivery_receipts` | evaluator, kernel, checkpoint | append-only exposures; cursor advances monotonically |
| `lab.db` `execution_manifests`, `manifest_revocations` | `manifest.py::publish_manifest` (CLI `observe publish-manifest`) | `resolve_observation_treatment`, checkpoint | immutable; revocation is a new row |
| `lab.db` `observation_windows`, `observation_lifecycle_events`, `observation_evaluations`, `observation_results` | `kernel.py`, `canonical_lifecycle.py` | overview, learning projections | append-only results (update/delete triggers) |
| `lab.db` `joint_checkpoints` | `checkpoint.py::save_joint_checkpoint` | `verify_joint_checkpoint` | append-only (update/delete triggers) |
| `lab.db` `filter_shadow_frozen_units` | `units.py::insert_frozen_units` (no production caller) | `extensions/counterfactual/filter_replay.py` | legacy-authority compatibility surface |
| `lab.db` `ufc_shadow_equivalence`, `lab_exposures` | none current | overview (`ufc_shadow_equivalence`) | historical, read-only |

`lab.db` is a separate SQLite file under the configured data root so Main migrations cannot
rewrite Lab snapshots; Lab never stores a second copy of market, Candidate, Position, cost,
Risk or execution truth.

## Requirements

### Entry-path treatment

#### OL-C01 Every planning pass leaves a receipt

`When the Entry Planner builds an entry analysis context for a Candidate with an opportunity context, the Observation resolver shall resolve the mechanism-aware-entry-learning/v1 treatment for that Planning Generation and append one delivery receipt to observation_delivery_outbox.`

- WHEN a Planning Generation is analysed THEN one receipt exists with `consumer=entry_planner`, `consumer_stage=entry_analysis_context` and `candidate_key` = the generation id.
- WHEN a caller passes its own connection (`db=`) THEN the receipt joins that transaction; the entry path passes none, so its receipt is its own write.
- WHEN no entry-learning loader is bound THEN the context is empty with reason `entry_learning_extension_unavailable` and no receipt is written.

#### OL-C02 Baseline unless Lab proves a treatment

`If the Experiment authority is not lab, the joint checkpoint is not proven, the outbox holds 10,000 or more unacknowledged receipts, lab.db is missing, or no eligible active manifest applies, then the resolver shall return the adapter's deterministic baseline with a typed reason.`

- WHEN authority is `legacy` THEN reason `authority_source_legacy`; WHEN the authority row is missing or malformed THEN `authority_source_unavailable`; the receipt is still written.
- WHEN `verify_joint_checkpoint(create_lab=False)` is not ok THEN `joint_checkpoint_unavailable`.
- WHEN the outbox is full under `lab` authority THEN `outbox_backpressure` and no further receipt is appended; WHEN `lab.db` is absent THEN `lab_unavailable` and no Lab file is created.
- WHEN the generation is observe-only or a Main deny tombstone exists THEN the baseline is returned (`observe_only_generation`, `main_deny_tombstone`).

#### OL-C03 Entry-learning adapter boundary

`The mechanism-aware-entry-learning/v1 adapter shall use the Planning Generation as assignment unit, the mechanism-aware-grounded-entry interference key, and {"include_learning_context": false} as both baseline and control payload.`

- WHEN a manifest's eligible population differs in mechanism, direction, market type, Candidate-selection policy or policy epoch THEN the manifest is not eligible and the baseline is returned.
- WHEN the treatment applies THEN the only behavior change is a bounded `entry-learning-context/v1` prompt context; Profile, compiler, model/prompt, Risk, sizing, Gateway and Live inputs are unchanged.

### Delivery transport

#### OL-C04 Contiguous import, acknowledge after commit

`When the bridge imports receipts, the importer shall consume present receipts in sequence order, insert each Lab exposure by source receipt id, and acknowledge Main only after the Lab transaction commits.`

- WHEN a present receipt is malformed THEN import stops at that sequence and it stays retryable.
- WHEN a numeric sequence gap exists without a proven source checkpoint THEN it is not classified as evidence loss.
- WHEN `lab.db` does not exist THEN the bridge job returns without creating it.

#### OL-C05 Bridge refuses to advance from a mismatched cut

`If the joint checkpoint at the start of a bridge pass is not ok, then the bridge shall import nothing, save no checkpoint and record the job failure reason.`

- WHEN the pass succeeds THEN it saves one checkpoint and requires strict `fingerprint_match` afterwards.
- WHEN the cycle lock is held by another owner THEN the result is `cycle_lock_contended` with no mutation.

### Cycle and windows

#### OL-C06 One cycle owner

`The Observation cycle shall run under exactly one owner selected by observation.execution_owner (main, the default, or worker), serialised by the lab worker.lock.`

- WHEN the owner is `worker` THEN Main does not register `filter_controller_observe` and `_run_filter_controller_observe` fails closed with `worker_owns_cycle`.
- WHEN config cannot be read or the value is unrecognised THEN the owner is `unknown` and neither side runs the cycle.
- WHEN two passes overlap THEN the second fails fast on the lock.

The Worker mode (`deploy/systemd/aletheia-observer.*`) is residual: the deployed runtime is one Main instance.

#### OL-C07 Isolated window pass

`When run_lab_windows executes, the runner shall run every registered window in deterministic order, isolate each window's exception into its summary, and save a post-pass checkpoint only if the pre-pass checkpoint was ok.`

- WHEN one window raises THEN the other windows still run and the failing one reports `status=error`.
- WHEN the pre-pass checkpoint mismatches THEN windows still run observe-only and `save_skipped=unresolved_checkpoint_mismatch`.
- The registered windows are `filter_shadow_evaluate` and `durable_observation_windows`; historical UFC equivalence rows are readable but no window recomputes them.

#### OL-C08 Filter-shadow is observe-only and off by default

`Where controller.shadow_enabled is true and authority is lab, filter-shadow registration shall create observe-only Lab roots that cannot publish a behavior-changing manifest.`

- WHEN `shadow_enabled` is false (default) THEN only `run_lab_windows` runs.
- WHEN authority is not `lab` THEN registration raises `observation_lab_authority_required`.
- Adapters `observer_pre_filter` and `ranker_confidence_gate` are residual: their producers (Market Observer, Ranker) are retired.

### Manifests and invariants

#### OL-C09 Immutable manifests and append-only evidence

`The Lab shall keep execution manifests, joint checkpoints and observation results immutable, re-hashing each manifest from content before it enters the active set.`

- WHEN a stored manifest payload or hash is altered THEN it is an integrity failure, not an active manifest.
- WHEN an update or delete is attempted on `joint_checkpoints` or `observation_results` THEN the trigger aborts it.

#### OL-C10 Causal identity

`When a filter-shadow adapter's declared causal identity hash no longer matches its collecting cohort, the Lab shall invalidate that cohort with reason adapter_causal_identity_changed and keep its evaluation history.`

- WHEN only the source commit changes and the declared identity is unchanged THEN the cohort continues.
- WHEN an adapter declares no causal identity (`extensions/counterfactual/filter_experiments.py::_ADAPTER_CAUSAL_IDENTITY`) THEN its filter-version and parameter-snapshot checks apply.

### Joint checkpoint and update cut

#### OL-C11 Joint fingerprint

`The joint checkpoint shall fingerprint the app migration head and count, the Lab schema head and count, the Lab state digest, the prior checkpoint ledger digest, the sorted active manifest hashes, and the Main outbox high watermark, pending count and pending-identity digest.`

- WHEN acknowledged receipts are pruned THEN the fingerprint is unchanged (the pending transport frontier is the identity, not the retention cache).
- WHEN a pending receipt is lost or altered, the cursor rolls back, or an acknowledged suffix lacks matching Lab progress THEN verification mismatches.

#### OL-C12 Verify outcomes

`When verify_joint_checkpoint runs, it shall return ok with first_checkpoint for an empty ledger, ok with fingerprint_match for no drift, and not ok with the mismatched components otherwise.`

- WHEN called with `allow_main_outbox_progress=True` (only the scheduled runner and the updater diagnostic) and only later unacknowledged receipts were appended THEN ok with `main_outbox_append_only`.
- WHEN any behavior-bearing consumer sees not-ok THEN it returns its baseline (OL-C02).

#### OL-C13 Update activation cut

`While receipt producers are stopped and worker_cycle_exclusion is held through backup and source activation, update settlement shall verify the checkpoint, import the remaining contiguous receipts, and require zero pending receipts, no retries, a Lab cursor equal to the Main watermark, and a strict fingerprint_match.`

- WHEN the checkpoint already matches and the frontier is caught up THEN settlement is a read-only no-op (`fast_path=True`) with no importer, mirror, prune, compaction or checkpoint save.
- WHEN receipts are pending THEN only delivery import and checkpoint save run; audit mirroring, pruning and compaction stay ordinary maintenance.
- WHEN a retry sequence is pending THEN settlement refuses with `delivery_import_retry_pending`.

#### OL-C14 Bounded applied-delivery recovery

`If the strict checkpoint mismatches only by an applied Main receipt suffix, then update settlement shall admit the cut only when replaying the retained receipts reproduces the current cursor and removing the suffix in memory reproduces the trusted Lab fingerprint and checkpoint ledger.`

- WHEN any experiment, window, result or manifest evidence exists, a suffix receipt differs from Main's immutable fields, or retry state exists THEN recovery refuses without saving a checkpoint.
- WHEN preflight evaluates this proof THEN it reports settlement-required, never strict checkpoint health.

#### OL-C15 Restore and drift resolution

`If an update rollback restores data without a proven joint cut, then the updater shall invalidate every active Observation window by appending a restore_invalidate lifecycle event.`

- WHEN an operator runs `aletheia observe checkpoint --acknowledge <components>` THEN a new checkpoint is saved only if every actual mismatched component is named and the audit event `observation_checkpoint_drift_acknowledged` was written first.
- No other reset, re-anchor or fallback path exists ([runbook](../../operations/deployment/UPDATE_OBSERVER_CONTINUITY.md#acknowledging-a-known-checkpoint-drift)).

### Operator surface

#### OL-C16 Read-only overview

`When aletheia observe overview runs, it shall open app.db and lab.db physically read-only, never create a Lab directory, database or schema, and exit zero for healthy, degraded and unavailable status.`

- WHEN `lab.db` is missing THEN storage reports `unavailable` and nothing is created.
- WHEN JSON and text output are rendered THEN they come from the same `_collect_overview` projection (status, sorted reason codes, owner, storage, Worker systemd evidence, windows, manifests, bridge, checkpoint).
- `last_advanced_at` is evidence only; the command defines no freshness threshold and zero active windows is healthy when other evidence is healthy.

#### OL-C17 Physical read-only helper

`The storage layer shall provide open_readonly_snapshot, a SQLite mode=ro connection with PRAGMA query_only=ON, for paths that need a physical read-only boundary.`

- WHEN a write is attempted through it THEN SQLite rejects it.

## Invariants and authority

- Lab results (`ready_for_review`, positive verdicts) mean only that evidence is reviewable;
  they never change a Main behavior policy or grant Paper, Risk, Gateway, credential or Live authority.
- `filter_shadow_experiments.production_mutation_allowed` is storage-constrained to `0`.
- A real change to source population, outcome semantics, cost identity, required schema or
  treatment meaning terminates or rotates the affected evidence identity; Lab promises no
  continuity across it.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| `lab.db` missing | resolver baseline; bridge no-op; overview read-only | `lab_unavailable`; overview `unavailable` | never created to look healthy |
| authority `legacy` / unreadable | baseline | `authority_source_legacy` / `authority_source_unavailable` | not "no effect" |
| joint checkpoint mismatch | baseline for consumers; bridge refuses; runner observe-only, no save | `joint_checkpoint_unavailable`; scheduler job failure; `mismatch_components` | mismatch stays durable |
| outbox ≥ 10,000 pending | baseline, no new receipt | `outbox_backpressure` | backlog bounded, not dropped silently |
| malformed receipt | import stops at it | `failed_sequence` / retry list | retryable, never skipped |
| cycle lock contended | pass deferred | `cycle_lock_contended` | no mutation |
| one window raises | other windows run | `results[name].status=error` | not projected as success |
| restore without proven cut | all active windows invalidated | `restore_continuity_unproven` lifecycle event | history appended, never rewritten |

## Authority

Lab may write `observation_delivery_outbox` rows and its own `lab.db`; it may never change
Risk limits, credentials, Gateway submission, Live unlock, sizing, or a Main behavior policy.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| OL-C01 | `tests/test_entry_learning_context.py`; `tests/test_observation_canonical_bridge.py::test_receipt_is_atomic_with_caller_transaction` |
| OL-C02 | `tests/test_observation_canonical_bridge.py` (`test_missing_authority_row_fails_closed`, `test_lab_treatment_falls_back_when_joint_checkpoint_is_unproven`, `test_numeric_treatment_without_lab_authority_preserves_baseline`, `test_observe_only_and_deny_tombstone_never_change_baseline`) |
| OL-C03 | `tests/specs/spec_observation_experiment_ownership.py::OEO-S05` |
| OL-C04 | `tests/test_observation_canonical_bridge.py::test_lab_authority_resolves_and_imports_one_receipt`, `test_importer_retries_main_ack_for_already_imported_receipt` |
| OL-C05 | `tests/test_lab_runner_checkpoint.py::test_main_bridge_checkpoints_imported_receipts_and_refuses_existing_drift`, `test_settlement_reports_cycle_lock_contention_without_mutation` |
| OL-C06 | `tests/test_observation_execution_owner.py` |
| OL-C07 | `tests/test_lab_runner.py`; `tests/test_lab_runner_checkpoint.py::test_runner_isolates_checkpoint_error_from_window_execution` |
| OL-C08 | `tests/test_observation_execution_owner.py::test_reconcile_keeps_main_cycle_when_shadow_disabled` |
| OL-C09 | `tests/test_joint_checkpoint.py::test_execution_manifests_are_immutable`, `test_joint_checkpoint_ledger_is_append_only`; `tests/test_observation_canonical_bridge.py::test_checkpoint_and_manifest_identity_triggers_remain_append_only` |
| OL-C10 | `tests/test_filter_shadow_experiments.py::test_ranker_confidence_adapter_identity_change_invalidates` |
| OL-C11 | `tests/specs/spec_observation_experiment_ownership.py::OEO-S07`; `tests/test_joint_checkpoint.py` |
| OL-C12 | `tests/specs/spec_observation_experiment_ownership.py::OEO-S06`; `tests/test_joint_checkpoint.py::test_appended_main_receipt_requires_explicit_progress_mode` |
| OL-C13 | `tests/specs/spec_update_observation_settlement.py::OBC-UPD-01`, `OBC-UPD-02`, `OBC-UPD-03` |
| OL-C14 | `tests/specs/spec_update_observation_settlement.py::OBC-UPD-04` |
| OL-C15 | `tests/test_observation_window_kernel.py`; `tests/test_auto_update.py`; `tests/test_observe_overview.py::test_observe_checkpoint_command_saves_and_verifies` |
| OL-C16 | `tests/test_observe_overview.py` (`test_collect_overview_missing_databases_creates_nothing`, `test_overview_all_health_levels_exit_zero`, `test_observe_overview_json_matches_collect`) |
| OL-C17 | `tests/test_observe_overview.py::test_readonly_lab_db_physically_rejects_writes` |

Repository tests prove code semantics only, not that a treatment is economically positive.

## Decision references

- #1072 Main/Worker split and joint checkpoint; #2614 window/unit kernel; #2737 single Lab
  authority ([migration contract](observation_experiment_ownership_v2.md),
  [proposal](../proposals/observation_experiment_ownership_v2.md)); #3096 owner-approved
  bounded applied-delivery recovery; #1193 checkpoint mismatch authority.
- Superseded documents (moved 2026-10-10): [v1 design](../history/observation_lab_v1_design.md),
  [joint backup contract](../history/observation_lab_backup_contract.md),
  [retired Lab contract text](../history/observation_lab_retired_text.md).
- Frozen ownership evidence: [UFC ownership map](../../research/audits/UFC_OWNERSHIP_MAP_2026-08-18.md).
