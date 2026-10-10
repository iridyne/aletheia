# Data Retention Policy

> **Role:** Contract — current retention behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `RET` · **Owner code:** `core/aletheia/agent_runtime/scheduler.py::_run_cleanup`, `core/aletheia/storage/repositories.py` prune functions, `core/aletheia/maintenance/`

## Scope

Retention decides how long telemetry and market-data rows and runtime files live in
`app.db`, `tasks.db` and the runtime data directory, and how freed pages are reclaimed. It never
touches trust-bearing records ([data_model.md §7](data_model.md)). Which tables are prunable at
all is a storage rule ([DM-C11](data_model.md#dm-c11-prune-only-through-the-allowlist)); this
contract owns the windows, guards, cascade order and compaction.

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/agent_runtime/scheduler.py::_run_cleanup` | scheduler job `cleanup` (daily) | scheduler |
| `core/aletheia/cli/data_cmd.py` `compact` | CLI `aletheia data compact [--apply]` | owner, Gateway stopped |
| `ops/maintenance/data_retention_backfill.py` | one-shot CLI | owner, Gateway stopped |

## Requirements

### RET-C01 Daily cleanup with an audit record

`When the daily cleanup job runs, the scheduler shall apply every prune listed in §3 and §4 with its configured window and write one data_pruned audit event carrying the per-target counts.`

- WHEN one prune raises THEN the job records a failure; counts already gathered are not reported as success.

### RET-C02 Telemetry only

`The cleanup job shall prune only tables allowed by DM-C11 and shall never delete from the never-pruned set in data_model.md §7.`

### RET-C03 Transactional, children first

`Each prune shall run inside a transaction and delete child rows before their parent.`

- WHEN `agent_shell_sessions` is pruned THEN its messages, events, artifacts, commands and tasks go first; WHEN Candidate-selection evidence is pruned THEN records go before batches; `perp_structure_shadow_outcomes` is pruned before `perp_structure_snapshots`.

### RET-C04 Experiment exposure guard

`While legacy experiment authority is active, the prunes of filter_decisions, filter_candidate_outcomes and universe_snapshots shall keep every row at or after the earliest durable exposure of an active open experiment, and if that floor cannot be read, the cleanup job shall skip those three prunes and emit cleanup_experiment_state_read_failed.`

- WHEN an experiment is only `registered` with no exposure THEN it retains nothing.
- WHEN Observation Lab authority is active THEN each prune resolves exact source claims (`observation_retention_claims`) instead of the global floor.

### RET-C05 Funding history floor

`The funding_rate_snapshots prune shall keep at least max(retention.funding_rate_snapshots_days, 2 × the longest open-position hold) of history.`

### RET-C06 Candidate-selection evidence prefix

`The universe_twin_shadow_* prune shall delete rows older than retention.universe_scanner_runs_days except, for a protected Candidate-selection epoch, rows through its first admission audit and at or before the global frozen training cutoff, and shall retain every Candidate-selection row when the Candidate policy state is unreadable.`

### RET-C07 Filesystem retention by database join

`When runtime files are pruned, the cleanup job shall select agent-shell session logs and task notes by joining their database rows rather than by file mtime, keep the main operator session, keep the newest retention.backups_keep_recent backups, and delete the pre-migration database copy only after its window when integrity_check returns ok.`

### RET-C08 Bounded page reclamation

`When the cleanup job finishes, it shall reclaim freelist pages with bounded PRAGMA incremental_vacuum (16 MiB, or 64 MiB once the database reaches 80% of retention.app_db_capacity_mb), run a PASSIVE WAL checkpoint, and record size and checkpoint backlog to logs/database_capacity.jsonl.`

- WHEN the database exceeds the capacity budget THEN an operational warning is emitted; no write is refused.

### RET-C09 Offline compaction only

`The system shall run full VACUUM and the market_ohlcv WITHOUT ROWID rebuild only through aletheia data compact --apply, which shall refuse a running Gateway, create and verify a pre_compact backup, check integrity and foreign keys before and after, and delete no rows.`

- WHEN `compact` runs without `--apply` THEN it prints a read-only plan.
- WHEN `market_ohlcv` has an unexpected owner index THEN the rebuild fails closed.

## Durable state

### 2. Never-pruned set

The audit-chain, Learning-ledger, trading-integrity and archived-evidence tables listed in
[data_model.md §7](data_model.md). `knowledge_items` and `pi_decisions` are prunable only for
filtered rows (`system_action` items, transient decisions; 7 days).

### 3. Database windows (defaults, `config/schema.py::RetentionConfig`)

| Table | Window | Function |
|---|---|---|
| `agent_runs`, `agent_tool_calls` | 90d | `prune_agent_runs` (cascades `agent_run_artifacts`), `prune_tool_calls` |
| `agent_run_steps` | 14d | `prune_run_steps` |
| `agent_shell_messages` | 3d context / 14d other | `prune_agent_shell_messages` |
| `agent_shell_events`, `agent_shell_commands`, `agent_shell_sessions` | 14d | own prune; sessions cascade children |
| `agent_shell_heartbeats` | 2d | `prune_agent_shell_heartbeats` |
| `agent_shell_artifacts` | 7d strategy_evaluation / 30d proposal, report, daily_summary / 14d other | `prune_agent_shell_artifacts` |
| `agent_shell_tasks` | 14d completed / 30d failed or cancelled; running and pending exempt | `prune_agent_shell_tasks` |
| `runtime_sessions` | 90d | `prune_runtime_sessions` |
| `market_snapshots` | 30d | `prune_market_snapshots` |
| `portfolio_snapshots` | 90d | `prune_portfolio_snapshots` |
| `market_ohlcv` | 1m 2d / 5m 7d / 15m 30d / 1h 90d / 4h 180d / 1d 730d; `legacy_unknown` exchange dropped | `prune_market_ohlcv` |
| `orderbook_snapshots`, `liquidation_events` | 7d | own prune |
| `funding_rate_snapshots` | 7d with the RET-C05 floor | `prune_funding_rate_snapshots` |
| `open_interest_snapshots`, `perp_price_snapshots` | 7d | own prune |
| `perp_structure_snapshots`, `perp_structure_shadow_outcomes` | 30d | own prune |
| `options_market_structure_snapshots`, `options_volatility_snapshots` | 30d | own prune |
| `onchain_snapshots` | 30d | `prune_onchain_snapshots` |
| `macro_snapshots`, `fred_source_batches` | 90d | own prune |
| `coingecko_top_snapshots` | 3d | `prune_coingecko_top_snapshots` |
| `prediction_probability_snapshots` | 90d | own prune |
| `universe_feature_snapshots` | 14d | `prune_universe_feature_snapshots` |
| `universe_snapshots` (+ `universe_members`) | 30d with RET-C04 | `prune_universe_snapshots` |
| `universe_scanner_runs` | 30d | `prune_universe_scanner_runs` |
| `universe_twin_shadow_batches` / `_records`, `universe_relative_rank_shadow_batches` | 30d with RET-C06 | `prune_universe_twin_shadow_evidence` |
| `ohlcv_backfill_state` | 90d since update | `prune_ohlcv_backfill_state` |
| `market_regimes` | 60d | `prune_market_regimes` |
| `alpha_control_observations` | 60d | `prune_alpha_control_observations` |
| `filter_decision_batches` | 30d | `prune_filter_decision_batches` |
| `filter_decisions`, `filter_candidate_outcomes` | 45d with RET-C04 | own prune |
| `controller_recommendations` | 30d | `prune_controller_recommendations` |
| `filter_shadow_experiments` (+ evaluations) | 90d; `collecting` exempt | `prune_filter_shadow_experiments` |
| `shadow_trades` | 90d | `prune_shadow_trades` |
| `operator_evaluation_events` | 30d | `prune_operator_evaluation_events` |
| `serenity_evidence`, `serenity_run_events` | 60d | own prune |
| `serenity_claims` | 90d | `prune_serenity_claims` |
| `news_items` | `news.retention_days` (7d) | `extensions/data/news_collector.py::prune_old_news` |
| `opportunities` (open) | expire after 48h (status change, not delete) | `expire_stale_opportunities` |
| `agent_task_claims` (`tasks.db`) | `expires_at` | `_prune_agent_task_claims_tasks_db` |

### 4. Filesystem retention

| Path | Window | Function |
|---|---|---|
| `agent_shell/sessions/` | 14d by DB join; main session exempt | `agent_shell/file_artifacts.py::prune_session_logs_db_join` |
| `agent_shell/tasks/` | 7d completed / 30d failed or cancelled by DB join | `agent_shell/file_artifacts.py::prune_task_notes_db_join` |
| `backups/` | newest 7 kept | `maintenance/filesystem_retention.py::prune_backups` (also after each backup publication in `ops/backup/service.py`) |
| `diagnostics/` | 14d by mtime | `maintenance/filesystem_retention.py::prune_diagnostics_dir` |
| `app.db.pre-migration-backup` | 30d after the runtime-home migration, only when `integrity_check` is ok | `maintenance/filesystem_retention.py::prune_pre_migration_backup` |
| `logs/errors.log` | rotated at 50 MiB | `gateway/logs.py::rotate_if_needed` |
| `*.jsonl` | rotated at `retention.jsonl_max_mb` (50 MB); rotated files kept; `audit.jsonl` hash chain continues across rotations | `files/jsonl.py` |

### 5. Write-side guardrails

Writers that keep volume bounded at the source (pinned by `tests/test_write_guardrails.py`
unless noted): context snapshots are not persisted on `agent_shell_messages` and only the latest
context row per session is kept; `log_tool` does not double-write `agent_run_steps`;
`controller_recommendations` skips insufficient × insufficient rows; agent-shell task notes are
skipped for telemetry task types and task creation is idempotent with backpressure; heartbeats
update `last_seen_at` when their semantic hash is unchanged; `funding_rate_snapshots` and
`market_regimes` skip unchanged rows within their heartbeat window; `universe_feature_snapshots`
skips equivalent rows; `market_ohlcv` keeps at most `historical_data.max_bars_per_pair` bars per
pair; failed `strategy_evaluation` artifacts with an `Unknown command:` summary are rejected.

### 6. Compaction sequence (`aletheia data compact --apply`)

1. Hold the writer permit; create and verify a WAL-consistent `pre_compact` backup.
2. `wal_checkpoint(TRUNCATE)`, `integrity_check`, `foreign_key_check`; abort on any failure.
3. Switch to rollback-journal mode for the rebuild.
4. Rebuild `market_ohlcv` as `WITHOUT ROWID` when its schema and indexes match exactly; verify row count and every value before replacing.
5. `auto_vacuum = INCREMENTAL`, then `VACUUM` (needs at least 2 × `app.db` + 512 MiB free).
6. Restore WAL, set `journal_size_limit = 67108864`, `PRAGMA optimize`, re-run integrity and foreign-key checks.

New databases set `auto_vacuum=INCREMENTAL` before their first schema object; an existing
`auto_vacuum=0` database reclaims file bytes only after one compaction.

### 12. One-shot backfill runbook

`ops/maintenance/data_retention_backfill.py` applies the same windows once to an existing
database.

#### 12.1 Preconditions

Gateway stopped (`uv run aletheia gateway stop`); free disk at least 2 × `app.db`; backup space
under `backups/`.

#### 12.2 Dry run

`uv run python -m ops.maintenance.data_retention_backfill --dry-run` prints per-table eligible
rows and estimated MB and deletes nothing.

#### 12.3 Apply

`uv run python -m ops.maintenance.data_retention_backfill --apply --vacuum [--yes] [--archive-dir PATH] [--tables T1,T2]`:

1. preflight: Gateway not running, disk sufficient, integrity and foreign-key checks clean,
   baseline row counts of the trust-bearing tables saved;
2. archive rows of archive-listed tables to `data_retention_archive_YYYYMMDD/<table>.jsonl.gz`
   before deleting; filter tables use the RET-C04 floor;
3. checkpoint, integrity check, compaction (§6), then re-check integrity, foreign keys and the
   trust-table baseline, restoring the backup on any break;
4. filesystem prunes (§4) and `tasks.db` claims;
5. strip `content.context` from surviving recent `agent_shell_messages`.

A second run with nothing eligible is a no-op. Afterwards: `aletheia gateway start`, then
`uv run aletheia doctor`.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| experiment floor unreadable | guarded prunes skipped | `cleanup_experiment_state_read_failed` infra event | rows kept, count `0` |
| Candidate policy state unreadable | all Candidate-selection rows kept | prune count | kept, never guessed |
| a prune raises | job failure recorded | job health, log | — |
| database over capacity budget | warning only | infra event, `database_capacity.jsonl` | writes continue |
| compaction preflight fails | abort before any change | CLI error | nothing deleted |

## Authority

May delete telemetry rows and runtime files inside the windows above; may never delete
trust-bearing rows, block writes for capacity, or run compaction while the Gateway serves.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| RET-C01, RET-C03 | `tests/test_retention.py`, `tests/test_data_retention.py` |
| RET-C02 | `tests/test_architecture_fitness.py`; `tests/specs/spec_data_model.py::DM-B05` |
| RET-C04 | `tests/test_data_retention.py`; `tests/audit/test_alpha_cohort_reachability.py` |
| RET-C05, RET-C06 | `tests/test_performance_indexes_retention.py`, `tests/test_data_retention.py` |
| RET-C07 | `tests/test_backup_retention_owner.py`, `tests/test_data_retention.py` |
| RET-C08, RET-C09 | `tests/test_database_capacity.py`, `tests/test_database_capacity_cli.py` |
| §12 runbook | `tests/test_retention_backfill.py` |
| §5 guardrails | `tests/test_write_guardrails.py` |

### Known conformance gaps

- **Raw external errors are not retained in telemetry.** The owner convention (#3880,
  [SAFETY_AND_TRUST](../../operations/development/agent-guides/SAFETY_AND_TRUST.md)) keeps the
  bounded reason token and the redacted, capped raw error. Of the retained telemetry tables only
  `orders.raw_error` carries a raw error; `collector_health_state.last_error_code` keeps the
  exception class name and `ohlcv_backfill_state.last_error` a token, and the retired
  collector-health text stated that stored values are never exception messages.
- **Knowledge directory retention is not wired.** The former policy set per-subdirectory
  windows under `knowledge/` (daily_reviews 90d, dreaming_logs 60d, guard_candidates 30d,
  evidence 90d, post_trade_reviews 180d, deprecated 365d);
  `maintenance/filesystem_retention.py::prune_knowledge_subdirs` exists but has no production caller.
- **Outcome window.** Earlier text gave `filter_candidate_outcomes` 30 days; the configured
  default is 45 days, aligned with `filter_decisions` so a decision never outlives its outcome
  (`tests/audit/test_alpha_cohort_reachability.py`). The 45-day value is current.

## Decision references

- Phase 3 retention pass and Phase 4 backfill (2026-07-17); dated measurements, open-question
  resolutions and follow-ups: [archived follow-ups](../../archive/design-superseded/data/RETENTION_POLICY_followups_2026-07-17.md).
- Former text, including the dropped `backtest_runs` / `backtest_trades` rows (tables dropped in
  v91), file:line guardrail references, the 200 MiB log-directory cap and artifacts/reports caps
  that were never implemented, and the #2737 phase note:
  [history snapshot](../history/retention_policy_2026-10-10.md).
- `filter_decisions` / `filter_candidate_outcomes` both 45 days: comment in `config/schema.py::RetentionConfig`.
