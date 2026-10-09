# Data Retention Policy

> Authoritative retention contract for the local SQLite (`~/.aletheia/app.db`),
> secondary SQLite (`~/.aletheia/tasks.db`), and filesystem artefact trees under
> `~/.aletheia/`.
> Last updated: 2026-10-09 (UFO table removed in v94; dated follow-ups archived).

> Issue #2737: the [Phase 1 Migration Contract](../contracts/observation_experiment_ownership_v2.md) preserves the current no-prune and earliest-exposure guards, while the [accepted Proposal](../proposals/observation_experiment_ownership_v2.md) owns the future exact-claim design. No retention authority changes until its serial proof and cutover gates are satisfied.

Two hard rules govern every retention decision here:

1. **Trust-bearing tables are permanent.** The 12 audit-chain tables and the
   7 Measured Learning Ledger tables MUST NEVER appear in `_PRUNABLE`
   (`core/aletheia/storage/repositories.py`) and MUST NEVER be
   dispatched from `_run_cleanup` (`core/aletheia/agent_runtime/scheduler.py`).
2. **All other retention is telemetry-only.** Every prune function is
   transactional (`was_in_tx` pattern from `_delete_old_rows` in
   `core/aletheia/storage/repositories.py`) and every parent-table prune cascade-deletes
   children FIRST (mirror `prune_agent_runs` in `core/aletheia/storage/repositories_research.py`).

---

## 1. Purpose and scope

Phase 2 audit (2026-07-17) put `app.db` at ~1031 MiB and
`~/.aletheia/agent_shell/` at ~278 MiB. 442 MiB of DB payload and ~250 MiB of
filesystem artefact are dead writes: rows and files no live reader consumes.
Phase 3 codifies:

- **Per-table time-based retention** with clean-window guards where verdict
  fingerprints depend on the window.
- **Write-side guardrails** that stop the dead writes at the source.
- **Filesystem retention** using DB-JOIN (not `mtime`) because the
  runtime-home migration (2026-07-17 11:25:17) rewrote every
  `agent_shell/sessions/*.jsonl` and `agent_shell/tasks/*.md` birth time.
- **VACUUM + incremental_vacuum strategy** so that pruned pages are
  reclaimed rather than only flagged free.
- **Operational capacity budget** — `app.db` default budget is 2048 MiB. This is
  an observability and maintenance threshold, not a write-denial limit: evidence
  writes never fail merely because the budget is crossed.

Phase 4 is the one-shot backfill; the recurring pass in `scheduler._run_cleanup`
keeps steady state after that.

---

## 2. Trust boundaries (NEVER prune)

### 2.1 Audit-chain (12 tables, permanent)

`audit_events`, `fills`, `capital_events`, `orders`, `positions`,
`trade_proposals`, `risk_decisions`, `approvals`, `knowledge_items`,
`ps_orders`, `ps_fills`, `ps_positions`.

Enforced by:
- Exclusion from `_PRUNABLE` in `core/aletheia/storage/repositories.py`.
- Architecture fitness assertion at `tests/test_architecture_fitness.py`.
- Pre/post baseline row-count invariant captured by the backfill script;
  any mismatch aborts and restores from backup.

### 2.2 Measured Learning Ledger (9 tables, permanent)

`learning_experiments`, `learning_exposures`, `learning_hypotheses`,
`learning_hypothesis_nominations`, `learning_policy_epochs`,
`learning_contribution_verdicts`, `learning_inference_decisions`,
`learning_lifecycle_events`, `learning_job_health`.

Deleting any supporting exposure silently mutates verdict replay identity.
Deleting a fixed-look plan or cutoff would also erase the proof that an
authority-eligible verdict was prospective and consumed once.
Deleting a nomination would erase rejected/advisory generation cost and many-to-one
behavior lineage used by Knowledge Yield.
`learning_lifecycle_events` are mirror-written to `audit_events`, so deletion
breaks the audit invariant.

---

## 3. Per-table retention (prunable)

Windows below are the defaults; each is wired through the new
`config.retention.*` section in `aletheia/config/schema.py`.

### 3.1 Agent runtime and shell telemetry

| Table | Window | Prune column | Function | Cascade | Archive? | Rationale (see §7 for MB) |
|-------|--------|--------------|----------|---------|----------|---------------------------|
| `agent_run_steps` | 14d | `created_at` | `prune_run_steps` (existing, `repositories_research.py`; window shortened) | via `prune_agent_runs` | hard delete | Pure telemetry, no audit/learning binding. Combined with write-guardrail #2 (drop `log_tool` double-write) stabilises at ~15 MiB. |
| `agent_shell_messages` (context) | 3d | `created_at` | `prune_agent_shell_messages` (existing, `:4549`; new type-tiered signature) | none (FK only to `agent_shell_sessions`) | hard delete | 91 % of message bytes are `content.context` payloads (~143 MiB) that no reader consumes. Paired with write-guardrail #1 to shrink each surviving context row from ~7.6 KiB to <500 B. |
| `agent_shell_messages` (other) | 14d | `created_at` | same function, `other` tier | none | archive to `agent_shell_messages_review.jsonl.gz` | Preserves delegation/review/diagnostic chain-of-thought that Lens-C flagged as post-mortem load-bearing. |
| `agent_shell_events` | 14d | `created_at` | `prune_agent_shell_events` (existing, `:4538`; unchanged) | none | archive to `agent_shell_events.jsonl.gz` | 7-14d incident-diagnosis window. Savings come from write-guardrail #10 (payload trim). |
| `agent_shell_heartbeats` | 2d | `created_at` | `prune_agent_shell_heartbeats` (existing, `:4527`; unchanged) | none | hard delete | Real savings come from write-guardrail #4 (UPDATE-not-INSERT dedup). |
| `agent_shell_artifacts` (strategy_evaluation) | 7d | `created_at` | `prune_agent_shell_artifacts` (existing, `:4560`; new type-tiered signature) | none | hard delete | 92 % of volume. Paired with write-guardrail #14 (reject `Unknown command:` payloads). |
| `agent_shell_artifacts` (proposal / report / daily_summary) | 30d | `created_at` | same function, `human_review` tier | none | archive to `agent_shell_artifacts_proposals.jsonl.gz` | Owners return to Level-2 human-review proposals 2-4 weeks later. |
| `agent_shell_artifacts` (other) | 14d | `created_at` | same function, `other` tier | none | hard delete | Middle bucket. |
| `agent_shell_tasks` | 14d completed / 30d failed+cancelled | `completed_at` | `prune_agent_shell_tasks` (NEW) | cascade `agent_shell_events` + `agent_shell_artifacts` referencing `task_id` first | archive metadata to `agent_shell_ledger.jsonl.gz` | Exempts `status IN ('running','pending')` or `completed_at IS NULL`. |
| `agent_shell_sessions` | 14d (30d if session has proposal/report/daily_summary artifact) | `created_at` | `prune_agent_shell_sessions` (NEW) | cascade `_messages`, `_events`, `_artifacts`, `_commands`, `_tasks` first | archive metadata to `agent_shell_ledger.jsonl.gz` | Parent window ≥ max child window; children pruned before parent. |
| `agent_shell_commands` | 14d | `created_at` | `prune_agent_shell_commands` (NEW) | none | hard delete | Aligns with agent_shell_events. |
| `runtime_sessions` | 90d | `started_at` | `prune_runtime_sessions` (NEW) | none | hard delete | Pure boot ledger; `runtime_id` is free-form TEXT, not FK. |

### 3.2 Market data (time-series)

| Table | Window | Prune column | Function | Cascade | Archive? | Rationale |
|-------|--------|--------------|----------|---------|----------|-----------|
| `options_volatility_snapshots` | 30d | `acquired_at` | `prune_options_volatility_snapshots` | none | hard delete | Observation-only IV summaries and selected input lineage; no raw-chain history or decision authority. |
| `market_ohlcv` | 1m=2d / 5m=7d / 15m=30d / 1h=90d / 4h=180d / 1d=730d + hard drop `exchange='legacy_unknown'` | `ts` (integer ms) | `prune_market_ohlcv` (NEW) | none | hard delete | No FK, no audit binding. Every reader is bounded LIMIT or ≤90d horizon. Also removes 27,554 legacy_unknown rows. |
| `position_path_observations` | lifetime of referenced Position | none | no prune | FK to `positions` | retain append-only | Ordered path evidence is immutable and must remain available for Position replay; the observer writes only exact available/unavailable facts. |
| `position_path_observation_deferred_buckets` | until materialized or terminally unavailable | none | no prune | FK to `positions` | retain while pending | Durable recovery queue; bounded retries remove a row by writing an append-only terminal fact, so a pending row is never used as indefinite history. |
| `funding_rate_snapshots` | `max(7d, 2 × max_open_position_hold_hours)` hard floor | `observed_at` | `prune_funding_rate_snapshots` (existing, `:4436`; window shortened) | none | hard delete | Redundant to `positions.funding_evidence` after `Position.close_position()`. Runtime floor prevents forced `funding_source='unavailable'` on future long-hold positions. |
| `open_interest_snapshots` | 7d | `observed_at` | `prune_open_interest_snapshots` (existing, `:4446`; window shortened) | none | hard delete | Latest-row / 45-75-min-back readers only. Write-guardrail #5 halves growth. |
| `coingecko_top_snapshots` | 3d + top_n default 50 (was 100) | `observed_at` | `prune_coingecko_top_snapshots` (existing, `:4320`; window shortened + wired through `cfg.retention.coingecko_top_snapshots_days`) | none | hard delete | 144× the 30-min doctor freshness threshold; movers summary uses top_k=5. |
| `market_regimes` | 60d | `detected_at` | `prune_market_regimes` (NEW) | none | hard delete | Preventive. Paired with write-guardrail #19 (skip-if-unchanged). |

### 3.3 Universe and features

| Table | Window | Prune column | Function | Cascade | Archive? | Rationale |
|-------|--------|--------------|----------|---------|----------|-----------|
| `universe_feature_snapshots` | 14d + drop always-empty `raw` column via new migration | `ts` | `prune_universe_feature_snapshots` (existing, `:4455`; window shortened) | none | hard delete | Lens-B expBounds floor. Write-guardrail #7 (change-suppression) holds this under 30 MiB steady. |
| `perp_structure_shadow_outcomes` | 30d | `entry_ts` | `prune_perp_structure_shadow_outcomes` | parent `perp_structure_snapshots` retained after child prune | hard delete | Observation-only 1h/4h/24h validation; 30d covers the required 14d review window. |
| `universe_snapshots` | 30d + protected-outcome guard | `created_at` | `prune_universe_snapshots` (NEW) | cascade `universe_members` | hard delete | A snapshot/member remains while an active experiment retains a referenced selected-member fallback outcome; otherwise write-guardrail #17 keeps volume bounded. |
| `universe_members` | 30d + protected-outcome guard | via parent | `prune_universe_members` (NEW) | none | hard delete | FK parents cannot be deleted while guarded fallback evidence still references the member. |
| `universe_scanner_runs` | 30d | `created_at` | `prune_universe_scanner_runs` (NEW) | none | hard delete | Aligns with parent-cohort granularity. |
| `universe_twin_shadow_batches` | fixed 30d, except admitted Candidate-selection trust prefix/training denominator | `captured_at` | `prune_universe_twin_shadow_evidence` | logical parent; records deleted first | hard delete | Prospective Shadow scorer-run identity; repository enforces at most one capture per 55min. For a protected epoch, rows through its first admission audit and rows at or before the global frozen training cutoff are retained; later telemetry expires at 30d. A protected epoch without an admission audit fails closed and retains all evidence, as does unreadable Candidate policy state. |
| `universe_twin_shadow_records` | via batch cutoff/protected Candidate prefix | parent batch `captured_at` | `prune_universe_twin_shadow_evidence` | deleted before batch | hard delete | Complete candidates within the sampled batch; record v2 removes reconstructible per-row duplicates while retaining raw input, selection, outcome linkage, cost, and audit identity. A 20-row fixture projects 77,016 payload bytes/run, 27 cadence-bounded runs/day, 1.983 MiB/day, and 59.493 MiB per rolling 30d window, plus the finite admission/training trust prefix. |
| `universe_relative_rank_shadow_batches` | fixed 30d | `captured_at` | `prune_universe_twin_shadow_evidence` | independent compact batch | hard delete | Relative Rank candidate records are compact JSON; namespaced selected outcomes follow canonical outcome retention. No Learning clean-window extension or authority is inferred. |
| `alpha_control_observations` | 60d | `entry_ts` | `prune_alpha_control_observations` (NEW) | none | hard delete | Matches alpha_attribution default 30d reader window + 7d horizon + settlement lag + margin. Sample cadence reduced 6× (write-guardrail #16). |

### 3.4 Filter evidence

| Table | Window | Prune column | Function | Cascade | Archive? | Rationale |
|-------|--------|--------------|----------|---------|----------|-----------|
| `filter_candidate_outcomes` | 30d **+ clean_window_cutoff guard** | `entry_ts` | `prune_filter_candidate_outcomes` (existing, `:2996`; `min_experiment_started_at` compatibility argument now receives an exposure timestamp) | none | hard delete | Effective cutoff = `min(now-30d, earliest durable exposure of active open experiments)`. Unexposed `registered` rows are ignored because no observation window exists. |
| `filter_decisions` | 45d **+ clean_window_cutoff guard** | `evaluated_at` | `prune_filter_decisions` (existing, `:2992`; same active-exposure guard) | none | hard delete | Preserves evidence back to the earliest durable exposure of an active open Learning experiment. Unexposed registrations do not hold telemetry indefinitely. Write-guardrail #6 remains the primary size lever. |
| `filter_decision_batches` | 30d | `evaluated_at` | `prune_filter_decision_batches` (existing, `:2988`; window unchanged) | none | hard delete | Contract-mandated at 30d. Heavy ticker/tradability facts use a 55-minute minimum interval per unchanged filter/stage/version, preserving an hourly heartbeat and immediate parameter-version changes. |
| `controller_recommendations` | 30d + skip insufficient×insufficient rows | `created_at` | `prune_controller_recommendations` (NEW) | none | hard delete | 91 % of rows are pure telemetry noise; write-guardrail #3 stops them at write. Aligns with sibling `filter_decision_batches`. |
| `filter_shadow_experiments` | 90d (exempt `status='collecting'`) | `created_at` | `prune_filter_shadow_experiments` (NEW) | cascade `filter_shadow_evaluations` first (FK has no ON DELETE CASCADE) | archive metadata | Preventive; both existing rows invalidated 2026-07-14. |
| `filter_shadow_evaluations` | cascade of parent (append-only kept per Q7) | via parent | `prune_filter_shadow_evaluations` (NEW) | none | hard delete | Append-only per user decision — no UPSERT-latest schema change. |

### 3.5 Serenity and backtest

| Table | Window | Prune column | Function | Cascade | Archive? | Rationale |
|-------|--------|--------------|----------|---------|----------|-----------|
| `serenity_evidence` | 60d | `created_at` | `prune_serenity_evidence` (NEW) | none | hard delete | Aligns with sibling `serenity_claims` (90d). |
| `serenity_run_events` | 60d | `created_at` | `prune_serenity_run_events` (NEW) | none | hard delete | Small today, unbounded before this. |
| `backtest_runs` | 60d | `started_at` | `prune_backtest_runs` (NEW) | cascade `backtest_trades` | hard delete | Diagnostic-only; legacy read-only tables kept for recent-quarter reference. |
| `backtest_trades` | cascade of parent | via parent | `prune_backtest_trades` (NEW) | none | hard delete | Cascade-driven. |

### 3.6 Secondary SQLite (`~/.aletheia/tasks.db`)

| Table | Window | Prune column | Function | Location | Rationale |
|-------|--------|--------------|----------|----------|-----------|
| `agent_task_claims` | honour `expires_at` | `expires_at` | `_prune_agent_task_claims_tasks_db` (NEW helper inside `scheduler.py`) | tasks.db | Round-1 missed this file. `DELETE FROM agent_task_claims WHERE expires_at < now`. |

---

## 4. Filesystem retention

| Path | Window | Strategy | Exemptions | Function |
|------|--------|----------|------------|----------|
| `~/.aletheia/agent_shell/sessions/` | 14d | DB-JOIN. `SELECT id FROM agent_shell_sessions WHERE created_at < :cutoff` → unlink `<id>.jsonl`. Orphan sweep for stems not in current session set. | `operator-main.jsonl`; any file whose `id == cfg.operator.main_session_id`; sessions whose id is a `parent_session_id` of a younger session; kinds `review_session` / `strategy_session` / `diagnostic_session` where the id matches an active `agent_shell_heartbeats.active_branch_id`. | `prune_session_logs_db_join` in `aletheia/agent_shell/file_artifacts.py` (replaces `prune_session_logs`; old name kept as deprecation wrapper). |
| `~/.aletheia/agent_shell/tasks/` | 7d completed / 30d failed+cancelled | DB-JOIN. `SELECT id FROM agent_shell_tasks WHERE status IN ('completed','failed','cancelled') AND completed_at < :cutoff` → unlink `<safe_name(id)>.md`. Orphan sweep. | `status IN ('running','pending') AND completed_at IS NULL`. | `prune_task_notes_db_join` in `aletheia/agent_shell/file_artifacts.py`. |
| `~/.aletheia/backups/` | keep_recent=7 (count-based) | Sort by mtime DESC, unlink 8th onwards. Fires from every backup write path AND from `scheduler._run_cleanup`. | The newest 7 `backup_pre_update_*.tar.gz` files. | `prune_backups` in `aletheia/maintenance/filesystem_retention.py`, also called after backup publication by `ops/backup/service.py`. |
| `~/.aletheia/diagnostics/` | 14d | Age-based unlink of `deferred_flow_audit_*` and `thesis_lineage_v1_1_*` older than 14d (mtime is safe here). | none | `prune_diagnostics_dir` in `aletheia/maintenance/filesystem_retention.py`. |
| `~/.aletheia/app.db.pre-migration-backup` | 30d after `migration.completed_at` | Read `~/.aletheia/.runtime-home-migration.json.completed_at`; if `now - completed_at > 30d` AND `PRAGMA integrity_check == 'ok'`, unlink. | keep while migration is within 30d. | `prune_pre_migration_backup` in `aletheia/maintenance/filesystem_retention.py`. |
| `~/.aletheia/knowledge/` | per-subdir (see below) | Age-based per subdir. | `operator_memory/`, `agent_memory/`, `research_notes/`, `strategies/` (durable). | `prune_knowledge_subdirs` in `aletheia/maintenance/filesystem_retention.py`. |
| `~/.aletheia/logs/` | total-directory cap 200 MiB (age-based fallback 30d for `.N` generations) | Enforce `sum(logs/**) ≤ 200 MiB` by deleting oldest `.N` generation across all families. | active generation files held open by the gateway; `.audit.lock`. | Cap enforcement lives in `aletheia/gateway/logs.py` (out of scope of Phase 3 unless owner adds it later). |
| `~/.aletheia/artifacts/`, `~/.aletheia/reports/` (20 pre-created subdirs) | 30d per subdir | Preventive age-based cap. | `release-signing/` (ed25519 keypair — NEVER touch). | Follow-up. |

Knowledge subdirs:

- `daily_reviews/` 90d
- `dreaming_logs/` 60d
- `guard_candidates/` 30d
- `evidence/` 90d
- `post_trade_reviews/` 180d
- `deprecated/` 365d
- `operator_memory/`, `agent_memory/`, `research_notes/`, `strategies/` — permanent.

---

## 5. Write-side guardrails

Time-based retention composes with the per-pair bar caps (row 9). A global
row-count hard cap must never prune inside an active experiment's data window.

Each guardrail stops a dead-write pattern at the source. File references
below name the writer touched by each guardrail (paths as of 2026-07-17; resolve by symbol).

| # | Target | File:line | Effect |
|---|--------|-----------|--------|
| 1 | `agent_shell_messages` content.context | `aletheia/server/routes/operator.py:512-524` (`_seed_branch_workspace`) + `operator-shell/src/operator_loop.ts:1023` (external repo — see §9) | Stop persisting `snapshot.context` on `message_type='context'`. Retro-strip surviving <3d rows via one-shot UPDATE inside the backfill script. |
| 2 | `agent_run_steps` double-write | `aletheia/agents/helpers.py:395-401` (`log_tool`) + `aletheia/server/routes/data.py:238-246` (`/api/agent-runs/{run_id}/steps` UNION at read) | Remove double-write. `agent_tool_calls` remains sole tool-call ledger. Endpoint UNIONs both at read. Frontend UNION follow-up is in the archived follow-ups (§7). |
| 3 | `controller_recommendations` insufficient×insufficient | `aletheia/analytics/filter_controller.py:244-272` (gate insert) + `:218-224` (freshness gate on `0.5 × observe_interval_hours`) | Skip 91 % of rows; write one summary row per run. Preserves the `test_filter_evidence.py:520` assertion (only both-insufficient dropped). |
| 4 | `agent_shell_heartbeats` UPDATE-not-INSERT | `aletheia/agent_shell/operator_store.py:222` | Hash semantic payload (status, mode, permission_mode, is_busy, current_task_id, current_intent, queue_size, last_error, model_provider, session_id, active_branch_id, active_branch_kind, active_branch_title). If identical to previous row, UPDATE `last_seen_at`. Never skip entirely — supervisor staleness detection at `gateway/operator_shell.py:296` depends on the bump. |
| 5 | `funding_rate_snapshots` skip-if-unchanged | `aletheia/storage/repositories.py:4027-4031` (`insert_funding_rate_snapshot`) | Skip INSERT if `funding_rate == prev.funding_rate AND next_funding_time == prev.next_funding_time AND long_short_ratio == prev.long_short_ratio`. Hourly floor. |
| 6 | `filter_decisions` (scanner_weights_quota) batching | `aletheia/analytics/causal_controls.py:260-312` | Route through `record_filter_decision_batch` (zlib-compressed matrix + one shared `parameter_snapshot`). Ship reader-side dual-decode first, then flip the writer. |
| 7 | `universe_feature_snapshots` change-suppression + drop `raw` | `aletheia/universe/features.py:559` + new migration `aletheia/storage/migrations/NNNN_drop_universe_features_raw.py` | Skip INSERT if all persisted numeric fields round identically (or within 0.5 % delta on atr_pct/rsi_14/return_1h/funding_rate/oi_delta_1h) AND newest existing row is younger than 30 min AND older than 60 min. Drops always-`{}` `raw` column via `ALTER TABLE ... DROP COLUMN raw` (SQLite ≥ 3.35). |
| 8 | `learning_contribution_verdicts` skip empty-cohort | `aletheia/learning/contribution_runner.py:65-70` | `if not cohort.samples: continue` before `_build_verdict`. Halves verdict growth. |
| 9 | `market_ohlcv` per-timeframe cap | `aletheia/storage/repositories.py:3111,3155-3167` | Per-timeframe `max_bars_per_pair` inside the inline rolling-window prune (1m=4320 / 5m=4032 / 15m=2880 / 1h=2160 / 4h=1080 / 1d=730). Composes with `prune_market_ohlcv`. |
| 10 | `agent_shell_events` payload trim + task-lifecycle coalesce | `aletheia/server/routes/operator.py:1043-1050, 1085-1100, 1121-1132, 1153-1165` (command_*), `:1249-1259, 1276-1286` (task lifecycle) | Truncate `input` / `response` to 200 chars + `len=N`. Coalesce `task_started + task_completed` into one `task_lifecycle` event or drop `task_started`. Test `tests/test_operator_events.py` updated lockstep. |
| 11 | `agent_shell_artifacts` reject failed strategy_evaluation writes | `aletheia/server/routes/operator.py:1324-1335` | Reject POST `/api/operator/internal/artifacts` when `artifact_type='strategy_evaluation' AND content.summary.startswith('Unknown command:')` with 400 + diagnostic body. |
| 12 | `agent_shell_tasks` idempotency + backpressure | `aletheia/agent_shell/operator_store.py:288-315` + `operator-shell/src/operator_loop.ts` (external repo) | Hash `(task_type, created_by, canonical(input_data + strategy_state_fingerprint))`. Short-circuit if a row with same hash exists in status `IN ('running','completed')` within 4h. Whitelist `pi_reasoning_session`, `create_repair_proposal`, `create_diagnostic_report`. Backpressure: refuse `evaluate_strategy_state` if pending count ≥ 50. |
| 13 | `agent_shell_tasks` .md skip agent-consumed types | `aletheia/agent_shell/file_artifacts.py:138-165` (`write_task_note`) | Skip file write for `task_type IN ('evaluate_strategy_state','generate_review','create_review_report','diagnose_system_health','set_symbol_observe_only')`. Failed/cancelled always writes regardless of type. |
| 14 | `coingecko_top_snapshots` top_n=50 | `aletheia/config/schema.py:126` | Lower `coingecko_top_n` default from 100 to 50. |
| 15 | `alpha_control_observations` sample cadence | `aletheia/universe/service.py:432-441` | Sample control-cohort every 6th refresh (once/hour) instead of every 10-min `universe_refresh`. |
| 16 | `agent_shell_messages` cap 1 context row per session | `aletheia/agent_shell/operator_store.py:380-405` (`append_message`) | On INSERT of `message_type='context'`, DELETE prior context rows for the same session. |
| 17 | `universe_snapshots` + `universe_members` change-suppression | `aletheia/universe/service.py:359,397,432-441` | Skip INSERTs if member-set fingerprint (sorted `asset_id` + ranks + regime tag) matches previous. Heartbeat write every 6 refreshes (60 min). |
| 18 | `filter_decision_batches` bounded heavy snapshots | `aletheia/analytics/filter_evidence.py` + `aletheia/universe/features.py` + `aletheia/universe/filters.py` | Lossless compressed facts, with at most one unchanged ticker/tradability batch per hour. A changed parameter version writes immediately. |
| 22 | Experiment outcome horizon | `aletheia/universe/allocator.py` + `aletheia/universe/service.py` | General filter evidence retains four canonical horizons; a Learning exposure persists only its declared target horizon for both complete arms. |
| 23 | Redundant SQLite indexes | migration `20260806_0003_capacity_guardrails_v4` | Drop two indexes covered by the outcome UNIQUE constraint and one covered by the OHLCV primary key; retain the same indexed query plans without duplicate write amplification. |
| 24 | Pending outcome partial index | migration `20260806_0004_pending_outcome_partial_index_v5` | Replace the full `(status,due_at)` index with `(due_at,id) WHERE status='pending'`. Settlement queries are unchanged while settled/failed rows stop occupying this queue index. CASI read-only evidence on 2026-08-06: 707,160 settled + 16 failed + 0 pending rows; the old index occupied 38,649,856 bytes, so its current replacement is effectively empty. |
| 19 | `market_regimes` skip-if-unchanged | `aletheia/storage/repositories.py:4672` (or wherever `INSERT INTO market_regimes` lives) | Skip INSERT if regime state is byte-identical to latest. Hourly floor. |
| 20 | Learning behavior identity excludes `code_commit` | `aletheia/learning/policy_epochs.py` + `aletheia/learning/behavior_contracts.py` | Strip `code_commit` from policy hashing and use `behavior_contract_hash` for cohort/verdict identity. Exposure commits remain provenance and do not multiply behaviorally identical cohorts. |
| 21 | `serenity_evidence` content_hash dedup | `aletheia/agents/research_worker.py:689` + `aletheia/agents/prediction_observer.py:434` + `aletheia/server/routes/data.py:390` | Use the existing `content_hash` column. Before INSERT, check `(theme_id, content_hash)`. Skip on match. |

---

## 6. VACUUM strategy

The explicit offline `aletheia data compact --apply` sequence is:

1. Hold the target database's process/cross-process writer permit, then create and
   verify a WAL-consistent `pre_compact` backup.
2. `PRAGMA wal_checkpoint(TRUNCATE)`, `integrity_check`, and `foreign_key_check`;
   abort if another user blocks the checkpoint or either check fails.
3. Temporarily use rollback-journal mode so the table rebuild does not create a
   second database-sized WAL.
4. If its logical schema and explicit indexes exactly match the owned contract,
   rebuild `market_ohlcv` as `WITHOUT ROWID`; verify copied row count and every
   value before atomically replacing the old table. Unexpected owner indexes fail closed.
5. `PRAGMA auto_vacuum = INCREMENTAL`, then `VACUUM` (one-shot full compact).
   This rebuilds the database in that mode and requires
   ≥ 2× current `app.db` size plus 512 MiB free.
6. Restore WAL when it was the original mode, set
   `PRAGMA journal_size_limit = 67108864`, run `PRAGMA optimize`, and repeat
   integrity and foreign-key verification.

After (4), the recurring `_run_cleanup` pass reclaims freelist pages via
bounded `PRAGMA incremental_vacuum(N)` at the tail: at most 16 MiB normally or
64 MiB after reaching 80% of the configured capacity budget. Reclamation runs in
64-page chunks, and each chunk separately holds the process/cross-process writer
permit. No more full VACUUM is needed during runtime unless page packing/fragmentation evidence justifies an offline window.

New databases set incremental mode before their first schema object. Existing
databases with `PRAGMA auto_vacuum=0` do not reclaim file bytes when
`incremental_vacuum` is called; they require exactly one explicit offline
`uv run aletheia data compact --apply`. The command defaults to a read-only plan,
refuses a running Gateway, creates and verifies a WAL-consistent `pre_compact`
backup, checks free disk, integrity and foreign keys before and after, and never
deletes rows or changes retention windows.

Normal runtime does not perform the table rebuild or full VACUUM. Every application
connection sets the connection-local 64 MiB `journal_size_limit`. The nightly cleanup
runs a non-blocking PASSIVE checkpoint and records `log_frames`,
`checkpointed_frames`, and `remaining_frames` in `database_capacity.jsonl`; a backlog
of at least 1000 remaining frames emits an operational warning. PASSIVE does not wait
for readers or writers, so this observation cannot extend Gateway readiness.

`~/.aletheia/tasks.db` (12 KiB): repeat steps (1)-(3) only. Trivial cost.

Do NOT run `VACUUM` while the gateway is running. The one-shot backfill
enforces gateway-stopped as a preflight check (`aletheia gateway status`).

---

## 7. Dated follow-ups (archived)

The 2026-07-17 capacity projection and 2026-08-06 measurements, the Phase 2
open-question resolutions, the cross-repo / frontend / deferred write-guardrail
follow-ups and the 2026-08-16 legacy source-tree removal note were moved to
[`RETENTION_POLICY_followups_2026-07-17.md`](../../archive/design-superseded/data/RETENTION_POLICY_followups_2026-07-17.md)
on 2026-10-09. Open items there need a GitHub Issue before work restarts.

---

## 12. Runbook: invoking the one-shot backfill

The Phase 4 backfill script is `ops/maintenance/data_retention_backfill.py`.
Invoke from the repo root with `uv run`.

### 12.1 Preconditions

- Gateway stopped: `uv run aletheia gateway stop`.
- Free disk ≥ 2× current `app.db` size (VACUUM staging).
- Backup capacity available under `~/.aletheia/backups/`.
- Working copy on `iridite/sprat` branch.

### 12.2 Dry-run (audit only, no writes)

```sh
uv run python -m ops.maintenance.data_retention_backfill --dry-run
```

Prints per-table `table | window | eligible_rows | est_mb` and totals.
Exit code always 0. Nothing is deleted.

### 12.3 Apply mode

```sh
uv run python -m ops.maintenance.data_retention_backfill --apply --vacuum
```

Additional flags:

- `--yes` — skip interactive confirmation before the first DELETE. Use only
  after a satisfying dry-run.
- `--archive-dir PATH` — override the default
  `~/.aletheia/backups/data_retention_archive_YYYYMMDD/` archive directory.
- `--tables T1,T2` — restrict the run to a subset (comma-separated).

The script:

1. Preflight: `aletheia gateway status` reports not-running; free disk
   sufficient; `PRAGMA integrity_check` and `PRAGMA foreign_key_check`
   both clean; captures baseline row counts for audit-chain (12 tables)
   and learning (7 tables) to `~/.aletheia/backups/retention_baseline_<ts>.json`.
2. For each retention target, archives rows to
   `~/.aletheia/backups/data_retention_archive_YYYYMMDD/<table>.jsonl.gz`
   BEFORE delete when the table is on the archive-list. Filter-family
   tables call the new `prune_filter_*` variants with the
   `min_open_experiment_started_at` compatibility argument populated from the earliest exposure of
   active open experiments. Registered experiments with no exposure are ignored.
3. After all DB deletes: `PRAGMA wal_checkpoint(TRUNCATE)`;
   `PRAGMA integrity_check`; `PRAGMA auto_vacuum=INCREMENTAL`; `VACUUM`;
   `PRAGMA journal_size_limit=67108864`; `PRAGMA optimize`. Repeats
   `integrity_check` + `foreign_key_check` + audit-chain baseline
   comparison. ABORTS + restores from `app.db.pre-retention-backup` on any
   invariant break.
4. Runs the filesystem prune: `prune_session_logs_db_join`,
   `prune_task_notes_db_join`, `prune_backups(keep_recent=7)`,
   `prune_diagnostics_dir(days=14)`, `prune_pre_migration_backup` (if
   migration older than 30d).
5. Runs on `~/.aletheia/tasks.db` — honours `agent_task_claims.expires_at`.
6. Retro-strips `content.context` from surviving `agent_shell_messages`
   `<3d` rows via one-shot UPDATE (per Q9).

Idempotent: a second run with no new eligible rows is a no-op.

### 12.4 Post-run verification

- `aletheia gateway start`
- `uv run aletheia doctor` — expect fully green.
- `uv run aletheia learning inspect --path universe_family_calibration` —
  expect at least one open experiment with non-empty cohort (proves the
  clean_window_cutoff guard held).

Retention windows and function assignments in this document are the
source of truth. If a runtime pass diverges from what is documented here,
this document is wrong — update it and reconcile the pass, not the other
way around.
