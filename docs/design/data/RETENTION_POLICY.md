# Data Retention Policy (Phase 3)

> Authoritative retention contract for the local SQLite (`~/.aletheia/app.db`),
> secondary SQLite (`~/.aletheia/tasks.db`), and filesystem artefact trees under
> `~/.aletheia/`.
> Last updated: 2026-07-17 (Phase 3 workflow synthesis; user Phase 2 decisions locked).
> Source: `/tmp/RETENTION_SPEC.md` + `/tmp/RETENTION_SYNTHESIS.json` (workflow output).

Two hard rules govern every retention decision here:

1. **Trust-bearing tables are permanent.** The 12 audit-chain tables and the
   7 Measured Learning Ledger tables MUST NEVER appear in `_PRUNABLE`
   (`aletheia/storage/repositories.py:2860-2887`) and MUST NEVER be
   dispatched from `_run_cleanup` (`aletheia/agent_runtime/scheduler.py:1764`).
2. **All other retention is telemetry-only.** Every prune function is
   transactional (`was_in_tx` pattern from `_delete_old_rows` at
   `repositories.py:2900-2929`) and every parent-table prune cascade-deletes
   children FIRST (mirror `prune_agent_runs` at `:2929-2946`).

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
- **Numeric targets** — Day-0 (Phase 4 execution) `app.db ≤ 550 MiB` and
  `agent_shell/ ≤ 30 MiB`; Day-30 steady state `app.db ≤ 550 MiB` and
  `agent_shell/ ≤ 45 MiB`.

Phase 4 is the one-shot backfill; the recurring pass in `scheduler._run_cleanup`
keeps steady state after that.

---

## 2. Trust boundaries (NEVER prune)

### 2.1 Audit-chain (12 tables, permanent)

`audit_events`, `fills`, `capital_events`, `orders`, `positions`,
`trade_proposals`, `risk_decisions`, `approvals`, `knowledge_items`,
`ps_orders`, `ps_fills`, `ps_positions`.

Enforced by:
- Exclusion from `_PRUNABLE` at `aletheia/storage/repositories.py:2860-2887`.
- Architecture fitness assertion at `tests/test_architecture_fitness.py`.
- Pre/post baseline row-count invariant captured by the backfill script;
  any mismatch aborts and restores from backup.

### 2.2 Measured Learning Ledger (7 tables, permanent)

`learning_experiments`, `learning_exposures`, `learning_hypotheses`,
`learning_policy_epochs`, `learning_contribution_verdicts`,
`learning_lifecycle_events`, `learning_job_health`.

Deleting any supporting exposure silently mutates the verdict replay id at
`contribution_runner.py:96`. `learning_lifecycle_events` are mirror-written
to `audit_events` (`lifecycle.py:548-564`), so deletion breaks the audit
invariant.

---

## 3. Per-table retention (prunable)

Windows below are the Phase 3 defaults; each is wired through the new
`config.retention.*` section in `aletheia/config/schema.py`.

### 3.1 Agent runtime and shell telemetry

| Table | Window | Prune column | Function | Cascade | Archive? | Rationale (see §7 for MB) |
|-------|--------|--------------|----------|---------|----------|---------------------------|
| `agent_run_steps` | 14d | `created_at` | `prune_run_steps` (existing, `repositories.py:2947`; window shortened) | via `prune_agent_runs` | hard delete | Pure telemetry, no audit/learning binding. Combined with write-guardrail #2 (drop `log_tool` double-write) stabilises at ~15 MiB. |
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
| `runtime_sessions` | 30d | `started_at` | `prune_runtime_sessions` (NEW) | none | hard delete | Pure boot ledger; `runtime_id` is free-form TEXT, not FK. |

### 3.2 Market data (time-series)

| Table | Window | Prune column | Function | Cascade | Archive? | Rationale |
|-------|--------|--------------|----------|---------|----------|-----------|
| `market_ohlcv` | 1m=2d / 5m=7d / 15m=30d / 1h=90d / 4h=180d / 1d=730d + hard drop `exchange='legacy_unknown'` | `ts` (integer ms) | `prune_market_ohlcv` (NEW) | none | hard delete | No FK, no audit binding. Every reader is bounded LIMIT or ≤90d horizon. Also removes 27,554 legacy_unknown rows. |
| `funding_rate_snapshots` | `max(7d, 2 × max_open_position_hold_hours)` hard floor | `observed_at` | `prune_funding_rate_snapshots` (existing, `:4436`; window shortened) | none | hard delete | Redundant to `positions.funding_evidence` after `Position.close_position()`. Runtime floor prevents forced `funding_source='unavailable'` on future long-hold positions. |
| `open_interest_snapshots` | 7d | `observed_at` | `prune_open_interest_snapshots` (existing, `:4446`; window shortened) | none | hard delete | Latest-row / 45-75-min-back readers only. Write-guardrail #5 halves growth. |
| `coingecko_top_snapshots` | 3d + top_n default 50 (was 100) | `observed_at` | `prune_coingecko_top_snapshots` (existing, `:4320`; window shortened + wired through `cfg.retention.coingecko_top_snapshots_days`) | none | hard delete | 144× the 30-min doctor freshness threshold; movers summary uses top_k=5. |
| `market_regimes` | 60d | `detected_at` | `prune_market_regimes` (NEW) | none | hard delete | Preventive. Paired with write-guardrail #19 (skip-if-unchanged). |

### 3.3 Universe and features

| Table | Window | Prune column | Function | Cascade | Archive? | Rationale |
|-------|--------|--------------|----------|---------|----------|-----------|
| `universe_feature_snapshots` | 14d + drop always-empty `raw` column via new migration | `ts` | `prune_universe_feature_snapshots` (existing, `:4455`; window shortened) | none | hard delete | Lens-B expBounds floor. Write-guardrail #7 (change-suppression) holds this under 30 MiB steady. |
| `universe_snapshots` | 30d | `created_at` | `prune_universe_snapshots` (NEW) | cascade `universe_members` | hard delete | Universe membership changes slowly; write-guardrail #17 suppresses ~90 % of writes. |
| `universe_members` | 30d (cascade of parent) | via parent | `prune_universe_members` (NEW) | none | hard delete | FK to `universe_snapshots`; must delete before parent. |
| `universe_scanner_runs` | 30d | `created_at` | `prune_universe_scanner_runs` (NEW) | none | hard delete | Aligns with parent-cohort granularity. |
| `alpha_control_observations` | 60d | `entry_ts` | `prune_alpha_control_observations` (NEW) | none | hard delete | Matches alpha_attribution default 30d reader window + 7d horizon + settlement lag + margin. Sample cadence reduced 6× (write-guardrail #16). |

### 3.4 Filter evidence

| Table | Window | Prune column | Function | Cascade | Archive? | Rationale |
|-------|--------|--------------|----------|---------|----------|-----------|
| `filter_candidate_outcomes` | 30d **+ clean_window_cutoff guard** | `entry_ts` | `prune_filter_candidate_outcomes` (existing, `:2996`; window shortened + new `min_experiment_started_at` argument) | none | hard delete | Effective cutoff = `min(now-30d, min_open_experiment_started_at)` computed from `SELECT MIN(started_at) FROM learning_experiments WHERE ended_at IS NULL`. Any row still needed by an open experiment is kept. |
| `filter_decisions` | 45d **+ clean_window_cutoff guard** | `evaluated_at` | `prune_filter_decisions` (existing, `:2992`; window shortened + new guard argument) | none | hard delete | Preserves `filter_shadow_experiments._production_version_changed` detection over multi-week collecting windows. Write-guardrail #6 (batch scanner_weights_quota) is the primary size lever. |
| `filter_decision_batches` | 30d | `evaluated_at` | `prune_filter_decision_batches` (existing, `:2988`; window unchanged) | none | hard delete | Contract-mandated at 30d (`docs/design/contracts/filter_evidence_contract.md:259`). Write-guardrail #18 (content-hash dedup + 5-into-1 tradability coalesce) supplies savings. |
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
| `~/.aletheia/backups/` | keep_recent=7 (count-based) | Sort by mtime DESC, unlink 8th onwards. Fires from every backup write path AND from `scheduler._run_cleanup`. | The newest 7 `backup_pre_update_*.tar.gz` files. | `retain_recent(keep_recent=7)` in `aletheia/backup/service.py` + `prune_backups` in `aletheia/maintenance/filesystem_retention.py`. |
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

Each guardrail stops a dead-write pattern at the source. File references
below are the Phase 3 target lines.

| # | Target | File:line | Effect |
|---|--------|-----------|--------|
| 1 | `agent_shell_messages` content.context | `aletheia/server/routes/operator.py:512-524` (`_seed_branch_workspace`) + `operator-shell/src/operator_loop.ts:1023` (external repo — see §9) | Stop persisting `snapshot.context` on `message_type='context'`. Retro-strip surviving <3d rows via one-shot UPDATE inside the backfill script. |
| 2 | `agent_run_steps` double-write | `aletheia/agents/helpers.py:395-401` (`log_tool`) + `aletheia/server/routes/data.py:238-246` (`/api/agent-runs/{run_id}/steps` UNION at read) | Remove double-write. `agent_tool_calls` remains sole tool-call ledger. Endpoint UNIONs both at read. Frontend UNION follow-up in §10. |
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
| 18 | `filter_decision_batches` content-hash dedup + 5-into-1 tradability coalesce | `aletheia/analytics/filter_evidence.py:236-255` + `aletheia/universe/filters.py:124-132` | Content-hash dedup for tradability_* filters; heartbeat every 6th tick; coalesce five tradability_* batches into one matrix-encoded batch. Requires contract addendum. |
| 19 | `market_regimes` skip-if-unchanged | `aletheia/storage/repositories.py:4672` (or wherever `INSERT INTO market_regimes` lives) | Skip INSERT if regime state is byte-identical to latest. Hourly floor. |
| 20 | `learning_policy_epochs` drop `code_commit` from hash | `aletheia/learning/policy_epochs.py:52-67` + `aletheia/learning/experiments.py:151-153` | Strip `code_commit` key from `effective_values` before hashing `build_policy_epoch`. Verdict `cohort_input_fingerprint` still includes `code_commit` separately via `cohort.key.code_commit`. |
| 21 | `serenity_evidence` content_hash dedup | `aletheia/agents/research_worker.py:689` + `aletheia/agents/prediction_observer.py:434` + `aletheia/server/routes/data.py:390` | Use the existing `content_hash` column. Before INSERT, check `(theme_id, content_hash)`. Skip on match. |

---

## 6. VACUUM strategy

Sequenced post-prune, in the Phase 4 backfill script and mirrored in the
recurring cleanup pass:

1. `PRAGMA wal_checkpoint(TRUNCATE)` — flush WAL into main DB, truncate `-wal`.
2. `PRAGMA integrity_check` — abort if not `ok`.
3. `VACUUM` (one-shot full compact). Requires ≥ 2× current `app.db` size free.
4. `PRAGMA auto_vacuum = INCREMENTAL` — persistent setting (takes effect
   after the VACUUM above).
5. `PRAGMA journal_size_limit = 67108864` — cap WAL at 64 MiB.
6. `PRAGMA optimize` — refresh query planner stats.

After (4), the recurring `_run_cleanup` pass reclaims freelist pages via
`PRAGMA incremental_vacuum(1000)` at the tail — no more full VACUUM needed
during runtime.

`~/.aletheia/tasks.db` (12 KiB): repeat steps (1)-(3) only. Trivial cost.

Do NOT run `VACUUM` while the gateway is running. The one-shot backfill
enforces gateway-stopped as a preflight check (`aletheia gateway status`).

---

## 7. Trajectory (Day 0, 7, 30, 60)

| Day | `app.db` (MiB) | `agent_shell/` (MiB) | Notes |
|-----|----------------|----------------------|-------|
| 0 (before Phase 4) | 1031 | 278 | Starting state 2026-07-17. `agent_shell_messages` 219 / `market_ohlcv` 103 / `agent_run_steps` 53 / `filter_candidate_outcomes` 49 / `universe_feature_snapshots` 60 / `controller_recommendations` 42 / `filter_decisions` 34 / `filter_decision_batches` 32 / `agent_shell_events` 32 / `funding_rate_snapshots` 47 / `open_interest_snapshots` 38 / others. Audit chain 15 MiB, learning 8 MiB. |
| 0 (immediately after Phase 4) | 530 | 25 | Post gateway-stop → backup → prune → VACUUM → restart. Expected reclaim ~442 MiB rows + ~50 MiB via VACUUM. Retro-strip of `content.context` on surviving <3d rows adds ~8 MiB. If landing lands at 550 MiB, run VACUUM again after `wal_checkpoint(TRUNCATE)` and `PRAGMA optimize`. |
| 7 | 520 | 30 | Steady state stabilises. Write-guardrails 1 / 3 / 14 / 18 / 15 / 5 deployed by Day 7 cut daily growth from ~25 MiB/day to ~5-8 MiB/day; nightly prune counterbalances. |
| 30 | 540 | 40 | Full steady state; retention windows fully populated. `filter_candidate_outcomes` @ 30d ≈ 200 MiB dominates. Add write-guardrail #6 (batch scanner_weights_quota) early to keep `filter_decisions` 45d plateau near ~150 MiB rather than 200 MiB. |
| 60 | 530 | 45 | Long-run steady state with all guardrails and clean_window_cutoff active. `filter_candidate_outcomes` holds at ~200 MiB (would blow past 500 MiB without retention). If future writers land in currently-empty `artifacts/` or `reports/` subdirs, per-subdir retention must be in place before those writers ship. |

---

## 8. Open-questions resolution (Phase 2 decisions)

All 11 open questions from workflow Phase 1 are resolved. Decisions below
match `/tmp/RETENTION_SPEC.md#user-confirmed-decisions-phase-2`.

| # | Question | Decision |
|---|----------|----------|
| 1 | Filter tables safety (`filter_candidate_outcomes` + `filter_decisions`) | **A** — implement 30d/45d TODAY with clean_window_cutoff guard. Effective cutoff = `min(now-Nd, min_open_experiment_started_at)`. |
| 2 | `content.context` write-fix coordination | Cross-repo coordinated. Python-side (`aletheia/server/routes/operator.py:512-524` + `aletheia/agent_shell/operator_store.py`) ships in this workflow. TypeScript peer (`operator-shell/src/operator_loop.ts:1023`) documented as TODO in §9 for the external repo but NOT implemented here. |
| 3 | `auto_vacuum` mode | **INCREMENTAL**. One-time `PRAGMA auto_vacuum=INCREMENTAL` in the backfill (takes effect after VACUUM), then rely on `PRAGMA incremental_vacuum(1000)` in `_run_cleanup`. |
| 4 | `agent_shell_messages` retention shape | **Type-tiered**. `message_type='context'`: 3d. All other message_types: 14d. |
| 5 | Backup rotation | **`keep_recent=7`** (count-based). |
| 6 | `filter_shadow_evaluations` schema | **Keep append-only.** No UPSERT-latest schema change. |
| 7 | `universe_snapshots` + `universe_members` + `universe_scanner_runs` | **30d retention.** No known learning consumer JOINs `universe_members` to `filter_candidate_outcomes`. |
| 8 | `learning_policy_epochs` hash | **Drop `code_commit` from `effective_values`.** `learning_exposures.code_commit` still recorded separately, so replay authority is preserved. |
| 9 | Retro-strip `content.context` | **Yes.** One-shot UPDATE strips `content.context` from surviving <3d rows inside the backfill script. Saves ~8 MiB immediately. |
| 10 | Legacy source tree removal | **NOT part of this workflow.** Schedule a `CronCreate` job for 2026-08-16. See §11. |
| 11 | Doctor threshold regression tests | **Yes.** Agent 10 writes `tests/test_doctor_thresholds.py` covering agent_shell_events counts, filter_candidate_outcomes registered/settled ratios, coingecko_top_snapshots 30-min freshness. |

---

## 9. Cross-repo TODO: `operator-shell/src/operator_loop.ts:1023`

The TypeScript peer to write-guardrail #1 lives in the external
`operator-shell` repository. In this workflow the Python server-side drop
of the `context` field ships (`aletheia/server/routes/operator.py:512-524`
in `_seed_branch_workspace`). Until the TS PR lands, the TS writer keeps
producing bloated `context` payloads on every re-entered session and the
Python server strips them at persistence.

Action for the external repo:

- File: `operator-shell/src/operator_loop.ts`, line 1023.
- Change the `createMessage` body from
  `{text, profile, session_kind, sections, context: snapshot.context}` to
  `{text, profile, session_kind, sections}`.
- Ship as a coordinated PR paired with the Python-side deploy.

Coordinated PR status: in flight; tracked separately from Phase 3.

---

## 10. Frontend follow-up: `AgentActivity.tsx` UNION

Agent 8 removed the `agent_run_steps` double-write inside `log_tool`
(write-guardrail #2). The server endpoint `/api/agent-runs/{run_id}/steps`
now UNIONs `agent_run_steps` and `agent_tool_calls` at read time
(`aletheia/server/routes/data.py:238-246`).

The frontend `AgentActivity.tsx` component still reads only
`/api/agent-runs/{id}/steps` today. Because the endpoint contract change is
a superset (steps + tool_calls appear as one merged list), the UI works
unchanged. Future frontend work should:

- Confirm the merged list renders correctly for legacy runs (pre-guardrail)
  where `agent_run_steps` contains duplicate tool-call rows.
- Optionally show tool-call rows and reasoning-step rows differently once
  the transition window has passed (~ 14d, when older duplicated
  `agent_run_steps` rows have been pruned).

Not in scope for Phase 3.

---

## 10.5. Deferred write-side guardrails

Phase 3 deployed the top-priority write guardrails (Section 4) but did not
close every writer path considered during the workflow. A running list of
unfinished items, with rationale, expected impact, and code touchpoints,
lives in **`RETENTION_WRITE_GUARDRAILS_TODO.md`** (companion doc). Read
that file before starting a "second-round retention" push so previously
analyzed context is not re-discovered.

Highest-leverage deferred items (see companion doc for details):

- **P0-1** `agent_shell/sessions/` strategy-stem file writes — the main
  reason `agent_shell/` sits at ~246 MiB instead of the 50 MiB synthesis
  target. Requires a new session_kind gate in `append_session_log`.
- **P0-2** Cross-repo `operator-shell/src/operator_loop.ts:1023` context
  sender — server strips the payload, TS side still generates it.
- **P0-3** `filter_decisions` scanner_weights_quota — flip writer to
  batch form (read-side dual-decode already shipped in PR #121).
- **P0-4** `market_ohlcv` legacy_unknown writer — one-time delete done;
  writer path still needs sealing to prevent regrowth.

---

## 11. Legacy source tree removal (2026-08-16)

The runtime-home migration (`.runtime-home-migration.json.completed_at =
2026-07-17T11:25:17`) preserved `/Users/yixian/Projects/aletheia/data/`
(~3 GiB) as a rollback source. Per user decision Q10 that path is NOT
touched by Phase 3.

Schedule a session-scope `CronCreate` job for **2026-08-16** that:

1. Verifies runtime-home stability — `aletheia gateway status` running,
   `aletheia doctor` fully green, `PRAGMA integrity_check` on `app.db`
   returns `ok`, `~/.aletheia/.runtime-home-migration.json` unchanged.
2. Prompts the user before running
   `rm -rf /Users/yixian/Projects/aletheia/data/`.
3. Records the removal (or the user's decline) in a durable audit event.

The scheduling lives outside code (session-scope, not durable). See
[claude scheduling section](#) for how to instantiate.

---

## 12. Runbook: invoking the one-shot backfill

The Phase 4 backfill script is `aletheia/maintenance/data_retention_backfill.py`.
Invoke from the repo root with `uv run`.

### 12.1 Preconditions

- Gateway stopped: `uv run aletheia gateway stop`.
- Free disk ≥ 2× current `app.db` size (VACUUM staging).
- Backup capacity available under `~/.aletheia/backups/`.
- Working copy on `iridite/sprat` branch.

### 12.2 Dry-run (audit only, no writes)

```sh
uv run python -m aletheia.maintenance.data_retention_backfill --dry-run
```

Prints per-table `table | window | eligible_rows | est_mb` and totals.
Exit code always 0. Nothing is deleted.

### 12.3 Apply mode

```sh
uv run python -m aletheia.maintenance.data_retention_backfill --apply --vacuum
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
   `min_open_experiment_started_at` argument.
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
