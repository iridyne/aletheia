# Data Model Contract

> **Role:** Contract + table index — current storage behavior and the one index of every
> SQLite table with its writer, readers and status. Behavior of a table's rows belongs to the
> contract that owns its writer (linked per table); this file owns only storage-layer rules.
> **Prefix:** `DM` · **Owner code:** `core/aletheia/storage/` (`sqlite.py`, `migrations.py`, `repositories*.py`)

## Scope

Aletheia persists runtime state in SQLite under the runtime data directory. This contract
owns how rows are identified, how connections and writes are serialized, how the schema
changes, which tables retention may touch, and how JSON columns are read. The table index
(Durable state §1–§11) says, for every table, who writes it, who reads it and whether it is
still live. Row semantics (Risk, execution, outcomes, Universe, Learning) are owned by the
contracts linked from each table group; retention windows are owned by
[RETENTION_POLICY.md](RETENTION_POLICY.md).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/storage/sqlite.py::get_db` | call | every repository module |
| `core/aletheia/storage/sqlite.py::run_write` / `execute_write` | call | repository writes outside an explicit transaction |
| `core/aletheia/storage/migrations.py::apply_migrations` | CLI / lifecycle | `cli/data_cmd.py` (`aletheia data migrate`), `gateway/lifecycle.py` (start/restart) |
| `core/aletheia/storage/migrations.py::assert_migrations_current` | call | `server/app.py` lifespan (read-only verification) |
| `core/aletheia/storage/repositories.py::_delete_old_rows` | call | `agent_runtime/scheduler.py::_run_cleanup` prune functions |

## Requirements

### DM-C01 Row identifiers and timestamps

`The repository layer shall identify new rows with storage/models.py::new_id, a 12-character lowercase hex string (uuid4().hex[:12]), and store timestamps as ISO 8601 UTC text.`

- WHEN `insert_trade_proposal` persists a Proposal without an id THEN the stored id matches `^[0-9a-f]{12}$`.

### DM-C02 Proposal hash chain

`When insert_trade_proposal persists a Proposal whose commit_hash is unset, the repository shall set parent_hash to the newest stored commit_hash and commit_hash to the 16-hex SHA-256 prefix of the Proposal's fields excluding status and commit_hash.`

- WHEN a Proposal moves `pending → executed` THEN its `commit_hash` is unchanged (status is excluded).
- WHEN a Proposal arrives with an explicit `commit_hash` THEN it is kept as given.
- WHEN the whole table is re-hashed in `rowid` order THEN every stored `commit_hash` recomputes and every `parent_hash` names the previous row.

Evidence: `storage/repositories.py::insert_trade_proposal`, `_PROPOSAL_HASH_EXCLUDES`;
`storage/repositories_execution.py::_compute_proposal_hash`.

### DM-C03 One connection per thread

`The storage engine shall give each thread its own SQLite connection in WAL mode with synchronous=NORMAL, isolation_level=None, busy_timeout=30000 ms as the last-resort fallback, wal_autocheckpoint=1000 and a 64 MiB journal_size_limit.`

- WHEN two threads call `get_db()` THEN they receive different connection objects.
- WHEN a DML statement fails outside an explicit transaction THEN the connection is not left `in_transaction` (no implicit BEGIN).

Evidence: `storage/sqlite.py::_local`, `_open_connection`.

### DM-C04 Writer permit before SQLite

`When a connection writes, the storage engine shall first hold the process _WRITE_LOCK and the cross-process app.write.lock file permit, and shall raise write_lock_timeout after 6 seconds rather than wait inside SQLite's busy handler.`

- WHEN another process holds the file permit longer than 6 s THEN the writer fails with `write_lock_timeout` before SQLite reports `database is locked`.

Evidence: `storage/sqlite.py::acquire_write_lock`, `_acquire_file_write_lock`, `_WRITE_LOCK_TIMEOUT_S`.

### DM-C05 Bounded writer lane with exact results

`When a repository write runs outside an explicit transaction, the storage engine shall execute it on one dedicated writer connection behind a 256-item queue, may reject it with write_queue_timeout only before admission, and shall return the exact commit or rollback result after admission.`

- WHEN the queue stays full for 6 s THEN the not-yet-admitted write is rejected; an admitted write never returns an ambiguous timeout.

Evidence: `storage/sqlite.py::run_write`, `execute_write`, `_WRITE_QUEUE_CAPACITY`, `writer_metrics_snapshot`.

### DM-C06 Explicit transactions stay on the caller connection

`While a caller holds a transaction() block, repository helpers shall execute on that caller's connection and commit only when they opened the transaction themselves.`

- WHEN a helper inserts inside an open `BEGIN` THEN the row is not committed until the outer `COMMIT` (`was_in_tx` pattern).

### DM-C07 No I/O inside a write transaction

`The system shall not hold a transaction() block or run_write() closure across an await or a network or file I/O call.`

- WHEN code needs remote data for a write THEN it fetches first and opens the transaction afterwards.

### DM-C08 Scheduler releases leaked transactions

`When a scheduled job starts or finishes, the scheduler shall roll back any transaction left open on that thread's connection and close it.`

Evidence: `agent_runtime/scheduler.py` job wrapper (`rollback_if_in_transaction`, `close_db`).

### DM-C09 Runtime-session provenance

`When a row of opportunities, pi_decisions, trade_proposals, risk_decisions, orders, fills or positions is inserted with unset provenance, the repository shall stamp runtime_session_id, producer_commit and behavior_fingerprint from the active runtime session and shall leave them NULL when no session is active.`

- WHEN a Position closes THEN `closed_runtime_session_id` / `closed_producer_commit` / `closed_behavior_fingerprint` record the closing session.
- WHEN a legacy row has no provenance THEN it stays NULL; history is never fabricated.

Evidence: `storage/repositories.py::_stamp_provenance`, `agent_runtime/runtime_session.py::current_provenance`.

### DM-C10 Read-but-never-written tables are declared

`If production code in core/, extensions/ or ops/ reads a table that no production code writes, then that table shall be listed with a reason in tests/audit/table_read_write_pairing_baseline.json.`

- WHEN a writer is retired but readers remain THEN the table is `retired-readable` in this index and in the baseline.

### DM-C11 Prune only through the allowlist

`The retention layer shall delete rows only from tables in repositories.py::_PRUNABLE, or from tables in _CONDITIONALLY_PRUNABLE with an explicit row filter, and no table in the never-pruned set (§7) shall appear in _PRUNABLE.`

- WHEN `_delete_old_rows` is called for a causal-chain or audit table THEN it raises `ValueError`.
- WHEN `knowledge_items` or `pi_decisions` is pruned THEN only rows matching the caller's filter (`system_action` items, transient decisions) are deleted.

### DM-C12 Migration ledger

`When a migration completes, the migration runner shall append one aletheia_schema_migrations row (migration_id, checksum, schema_version, applied_at, duration_ms), and shall never replay or overwrite an applied migration id.`

- WHEN a recorded checksum differs from the code's checksum THEN apply fails closed without replaying.
- WHEN the ledger holds a migration id the running code does not know THEN apply and verification fail closed.
- WHEN a migration is interrupted THEN no success row exists and the next apply retries it; the frozen legacy baseline behaves the same way.

Evidence: `storage/migrations.py::apply_migrations`, `MigrationStateError`.

### DM-C13 Who applies migrations

`The system shall apply migrations only through aletheia data migrate and gateway start/restart, holding the process and cross-process writer permit for the whole apply, and the backend lifespan shall only verify the ledger and fail with the migrate command when a migration is pending.`

- WHEN an API route, scheduler job, Telegram or supervisor lookup runs THEN no migration is applied.

Evidence: `cli/data_cmd.py` (`migrate`), `gateway/lifecycle.py`, `server/app.py` (`assert_migrations_current`).

### DM-C14 Additive-only schema

`The schema shall change only by adding tables, nullable or defaulted columns, indexes and versioned backfills, and shall not drop or rename an existing table or column.`

- WHEN an existing database is upgraded THEN new columns arrive through `_ADDITIVE_COLUMNS` or a new versioned migration; the frozen baseline is never edited.

See Known conformance gaps: several versioned migrations now drop retired tables.

### DM-C15 No implicit dataset-size work

`The system shall not run dataset-size-sensitive repair or physical repacking as a read or startup side effect; FTS rebuilds run only in a versioned migration or aletheia knowledge reindex, and market_ohlcv WITHOUT ROWID conversion and full VACUUM run only in aletheia data compact --apply with the Gateway stopped.`

### DM-C16 Bounded automatic reads

`Automatic readers shall query through indexed bounded keys or a declared retained window, and a user-requested export or backup shall stream through a cursor or disk-backed archive instead of materializing a table in memory.`

### DM-C17 Tolerant JSON reads

`When a stored JSON column cannot be decoded, the reader shall skip the value or return the raw string and shall not raise.`

- WHEN `_select_one` reads a row whose JSON column is malformed THEN it returns the row with the raw string.
- Writers serialize with `json.dumps` / `model_dump`; SQLite performs no JSON schema validation, so validity rests on the Pydantic model upstream.

## Durable state

Status legend: **current** — a production writer exists; **retired-readable** — no
production writer, production readers still read history; **residual-no-writer** — no
production writer and no production reader. Writers and readers were derived from SQL and
repository write helpers in `core/`, `extensions/` and `ops/` (the scanner of
`tests/audit/test_table_read_write_pairing.py`) and resolved to their production callers.
Module paths are relative to `core/aletheia/` unless they start with `extensions/` or `ops/`.

### 1. Storage files and connection model

| File | Contents | Owner |
|---|---|---|
| `<data dir>/app.db` | every table below unless stated otherwise | `storage/sqlite.py` |
| `app.db` (lazy) | LLM economics tables, created by `economics/schema.py::ensure_economics_schema` on first use | `economics/` |
| `~/.aletheia/observation-lab/lab.db` | Observation Lab units, windows, exposures, results, manifests | [Observation Lab Contract](../contracts/observation_lab_contract.md) |
| `~/.aletheia/tasks.db` | `agent_task_claims` | `agent_runtime/scheduler.py` |

Connection, writer and transaction rules: DM-C03 – DM-C08. Row models are the Pydantic classes in
`storage/models.py`. Booleans are INTEGER 0/1; JSON
columns are TEXT; `knowledge_fts` is an FTS5 virtual table with its five shadow tables.

### 2. Trading causal chain

Row semantics: [Entry Plan](../contracts/entry-plan-contract.md), [Risk Engine](../contracts/risk_engine_contract.md),
[Execution](../contracts/execution_contract.md), [Exit and Close](../contracts/exit_and_close_contract.md),
[Outcome Integrity](../contracts/outcome_integrity_contract.md), [Audit Chain](../contracts/audit_chain_contract.md).

| Table | Purpose | Writer | Readers | Status |
|---|---|---|---|---|
| `candidate_planning_generations` | thesis-level planning identity, claim lease and result | `universe/service.py`, `trading/serenity_publication.py` (create); `agent_shell/entry_planner.py` (claim/result) | Entry Planner, `server/routes/ws.py`, lineage readers | current |
| `candidate_planning_attempt_outcomes` | append-only history of every planning outcome | `storage/repositories.py::_write_planning_outcome` (inside Entry Planner outcome writes) | repository planning reads | current |
| `entry_plans` | immutable approved plan plus monotonic execution lifecycle | `agent_shell/entry_planner.py`; `agent_runtime/fast_trigger_watch.py`, `execution/entry_plan_execution.py`, `execution/gateway.py`, `execution/stale_order_cleanup.py` (transitions) | `execution/liveness.py`, `server/routes/ui.py`, lineage readers | current |
| `entry_trigger_activations` | trigger activation bound to an Entry Plan | `agent_runtime/fast_trigger_watch.py` | `execution/entry_plan_execution.py`, `trading/entry_activation.py` | current |
| `trade_proposals` | immutable Proposal intent with hash chain (DM-C02) | `execution/entry_plan_execution.py`, `execution/gateway.py`, `portfolio/position_reconciler.py` (adoption); status updates by `risk/engine.py`, Gateway, materialization | Risk, analytics, routes (24 modules) | current |
| `proposal_execution_claims` | one expiring execution owner per Proposal | `execution/gateway.py` | Gateway, `execution/entry_plan_execution.py`, `agent_runtime/fast_trigger_watch.py` | current |
| `risk_decisions` | Risk approval/rejection with policy identity | `risk/engine.py` | analytics, inspectors (11 modules) | current |
| `risk_policy_snapshots` | immutable policy content per `policy_hash`; UPDATE rejected by trigger | `risk/engine.py` (INSERT OR IGNORE) | `risk/policy.py::resolve_entry_risk_policy` | current |
| `entry_risk_reservations` | durable pre-Order allowance acquired with authoritative Risk | `risk/engine.py` | Risk, `execution/gateway.py`, `agent_runtime/fast_trigger_watch.py` | current |
| `orders` | exchange Orders; `error_reason` token plus nullable `raw_error` (v99) | `execution/coordinator.py`, `execution/exchange_materialization.py`, `portfolio/position_reconciler.py`; transitions by Gateway, reconciliation, close recovery | 31 modules | current |
| `fills` | exact private fills | `execution/exchange_materialization.py` | 22 modules | current |
| `positions` | open/closed Positions; unknown economics stay NULL (§5) | `execution/exchange_materialization.py`, `portfolio/position_reconciler.py` (open); `portfolio/pnl.py`, `portfolio/outcome_closure.py`, `execution/coordinator.py` (close); `ops/maintenance/*` repair | 37 modules | current |
| `position_close_events` | one economic row per close event (full or partial) | `portfolio/pnl.py`, `execution/exchange_materialization.py` | close recovery, funding settler, path observer | current |
| `outcome_hook_jobs` | eligible-close feedback outbox with leased replay | `portfolio/outcome_hooks.py` | `portfolio/outcome_hooks.py` | current |
| `broker_funding_settlement_cursor` | singleton scan cursor for funding recovery | `portfolio/broker_funding_settlement.py` | same | current |
| `position_disappearance_candidates` / `_observations` | account-scoped disappearance confirmation and immutable evidence | `portfolio/disappearance_confirmation.py`, `portfolio/disappearance_settlement.py` | same | current |
| `reconciliation_integrity_incidents` | restart-safe reopen-after-close circuit breaker | `portfolio/reconciliation_integrity.py` | startup gate, reconciler, doctor, `extensions/learning/control.py` | current |
| `position_path_observations` / `position_path_observation_deferred_buckets` | ordered path evidence and its recovery queue | `portfolio/position_path_observer.py` | `analytics/position_lifecycle_story/`, `cli/control_cmd.py` | current |
| `position_realtime_revision` | singleton change counter for the WebSocket | SQLite triggers on `positions` | `server/routes/ws.py` | current |
| `portfolio_snapshots` | periodic account snapshots; only `account_equity_usdt` feeds the equity curve | `portfolio/pnl.py::insert_portfolio_snapshot` | `risk/engine.py`, `server/routes/data.py`, `cli/inspect_cmd.py` | current |
| `capital_events` | deposits/withdrawals | `server/routes/capital.py`, `portfolio/exchange_sync.py` | same, `server/routes/data.py` | current |
| `pi_decisions` | Operator decisions with evidence manifest and debate trace fields (NULL when absent) | `agent_shell/commands.py` | 21 modules | current |
| `opportunities` | independent-producer carriers; Universe Candidates live in `universe_members` | `extensions/research/equity_observer.py`, `extensions/research/prediction_observer.py` (out-of-scope producers, [spec 0003 FC-R04](../specs/0003-futures-only-convergence/spec.md)); expiry by `universe/service.py` and cleanup | 20 modules | current |
| `approvals` | human approval records | none | none | residual-no-writer |

`trade_proposals.trade_school` / `positions.trade_school` (`short_term` / `serenity`) and
`pi_decisions.signal_source` are discriminator columns; their consumers are owned by the
[Serenity Contract](../contracts/serenity_contract.md).

### 3. Table index by domain

#### Universe and Candidates — [Candidate Selection Utility](../contracts/candidate_selection_utility_contract.md)

| Table | Purpose | Writer | Readers | Status |
|---|---|---|---|---|
| `universe_registry` | known tradeable instruments | `universe/registry.py` | 15 modules | current |
| `universe_feature_snapshots` | per-instrument feature vectors (`asset_id`) | `universe/features.py` | Entry analysis, `portfolio/position_manager.py`, `portfolio/market_signal_reader.py`, doctor | current |
| `universe_scanner_runs` | refresh execution log | `universe/service.py`; scheduler crash recovery | scheduler, `agent_runtime/health.py` | current |
| `universe_snapshots` / `universe_members` | published Candidate snapshots; `universe_members` is the Candidate table (historical name) | `universe/service.py`, `trading/serenity_publication.py` | 17 modules | current |
| `universe_twin_shadow_batches` / `_records` | Candidate Selection Utility shadow evidence (historical physical name) | `universe/service.py` | `universe/candidate_selection_evaluation.py`, `analytics/alpha_attribution.py`, `extensions/analytics/twin_evaluation.py` | current |
| `universe_discovery_benchmarks` | discovery benchmark captures | `universe/discovery_benchmark.py` | same, `universe/filter_outcome_recorder.py`, `extensions/observation_lab/main_readers.py` | current |
| `market_regimes` | detected market regime | `universe/service.py` (refresh) | `universe/service.py` | current |
| `alpha_control_observations` | control cohort for Serenity opportunities | `analytics/causal_controls.py` (on Serenity opportunity insert) | `analytics/alpha_attribution.py` | current |
| `universe_relative_rank_shadow_batches` | former Relative Rank shadow | none (writer moved to test fixtures) | `analytics/alpha_attribution.py`, `universe/relative_rank_evaluation.py` | retired-readable |
| `universe_policies`, `universe_setups` | former selection rules / setups | none | none | residual-no-writer |

#### Filter evidence and outcomes — [Filter Evidence](../contracts/filter_evidence_contract.md), [Opportunity Outcome](../contracts/opportunity_outcome_contract.md)

| Table | Purpose | Writer | Readers | Status |
|---|---|---|---|---|
| `filter_decisions` | per-candidate pass/reject facts | `analytics/filter_evidence.py`, `universe/service.py` (expiry facts) | 16 modules | current |
| `filter_decision_batches` | compressed high-volume gate facts | `analytics/filter_evidence.py`, `universe/discovery_benchmark.py` | candidate facts, doctor | current |
| `filter_candidate_outcomes` | canonical fixed-horizon net outcome per exact instrument | `universe/service.py`, `analytics/filter_evidence.py`, `universe/filter_outcome_recorder.py`, `universe/discovery_benchmark.py`, `extensions/observation_lab/canonical.py` | 32 modules | current |
| `controller_recommendations` | append-only observational filter findings | `extensions/analytics/filter_controller.py` | same, `server/routes/analytics.py` | current |
| `shadow_trades` | former counterfactual trades | none (retired #3704) | 7 analytics modules | retired-readable |
| `filter_shadow_experiments` / `_evaluations` | former filter experiments (only on databases that predate the Lab cutover) | none | `storage/repositories.py` | retired-readable |
| `operator_evaluation_events` | former no-candidate Operator heartbeats | none | `analytics/opportunity_flow.py`, `analytics/trade_funnel.py`, doctor | retired-readable |

#### Market data — [Data Layer](data_layer.md), [Coverage and Collector Health](../contracts/market_data_coverage_contract.md), [Data Utilization](data_utilization.md)

| Table | Purpose | Writer | Readers | Status |
|---|---|---|---|---|
| `market_snapshots` | point-in-time ticker | `trading/market_data.py` | `data/price_feed.py`, `portfolio/pnl.py`, `data/context_builder.py`, health, inspect | current |
| `market_ohlcv` | candles keyed by exchange, symbol, market_type, timeframe, ts | `extensions/data/ccxt_provider.py` (best-effort), `data/history_downloader.py` | Entry Planner, fast trigger watch, replay/trade-path analytics, OHLCV route fallback | current |
| `ohlcv_backfill_state` | active-Universe catch-up/retry state | `data/active_ohlcv_backfill.py` | same, doctor | current |
| `orderbook_snapshots` | exact-instrument depth | scheduler collector batch, `universe/features.py`, `agent_shell/entry_planner.py` | Entry Planner, `universe/features.py`, `data/context_builder.py`, `server/routes/data.py` | current |
| `funding_rate_snapshots` | funding by exchange tag (Hyperliquid/Coinalyze rows are comparison sources) | scheduler collector batch, `extensions/data/collectors/derivatives.py` (history) | `universe/features.py`, `data/derivatives_facts.py`, `data/context_builder.py` | current |
| `open_interest_snapshots` | OI plus `long_short_ratio` / `top_trader_position_ratio` | scheduler collector batch, `extensions/data/collectors/derivatives.py` | `universe/features.py`, `data/context_builder.py`, `extensions/data/collectors/coinalyze.py`, research worker | current |
| `perp_price_snapshots` | mark/index per venue | scheduler (derivatives comparison) | `data/derivatives_facts.py`, `extensions/data/positioning_evidence.py` | current |
| `liquidation_events` | Coinalyze liquidations | scheduler collector batch | `data/context_builder.py`, `data/derivatives_facts.py`, research worker | current |
| `volume_ranking_snapshots` | volume ranking | scheduler | scheduler, doctor | current |
| `collector_health_state` | one latest row per collector domain | `data/collector_health.py` | collector health report, `data/derivatives_facts.py` | current |
| `macro_snapshots` | macro indicators, Fear & Greed, top-movers summary | scheduler (MacroCollector) | `trading/entry_snapshot.py`, `trading/persona.py`, `data/context_builder.py`, `extensions/regime/collector.py`, doctor | current |
| `coingecko_top_snapshots` | top-N coins | scheduler | doctor only | current |
| `onchain_snapshots` | DefiLlama stablecoin/TVL/DEX | scheduler | doctor only | current |
| `options_market_structure_snapshots` | Deribit GEX | scheduler | none | current |
| `options_volatility_snapshots` | Deribit IV summaries | scheduler | `cli/data_cmd.py` export | current |
| `perp_structure_snapshots` / `perp_structure_shadow_outcomes` | owner-imported positioning map and its shadow validation | `extensions/data/collectors/vergex_structure.py`, `extensions/data/perp_structure_shadow.py` | `extensions/data/perp_structure_shadow.py`, doctor | current |
| `fred_source_batches` | FRED source batches | scheduler | `data/macro_liquidity.py` | current |
| `news_items` | news and analysis | `extensions/data/news_collector.py`, `extensions/data/news_analyzer.py` | `extensions/research/research_worker.py`, news routes | current |
| `fast_trigger_market_snapshots` | former setup-keyed fast-trigger history | none | none | residual-no-writer |

#### Research producers outside product scope

| Table | Writer | Readers | Status |
|---|---|---|---|
| `prediction_observer_runs`, `prediction_events`, `prediction_markets`, `prediction_probability_snapshots`, `prediction_market_claims` | `extensions/research/prediction_observer.py` (registered; runs only with `prediction_enabled`, default off) | the observer, doctor, `server/routes/ws.py` | current |

#### Agent runtime, shell and extensions

| Table | Writer | Readers | Status |
|---|---|---|---|
| `agent_runs`, `agent_run_artifacts` | `agent_runtime/run_logger.py` | routes, doctor, usage insights | current |
| `agent_run_steps`, `agent_tool_calls` | `agents/helpers.py` (`insert_agent_telemetry_batch`) | `server/routes/data.py`, economics | current |
| `agent_shell_sessions`, `_commands`, `_messages`, `_tasks`, `_artifacts`, `_heartbeats`, `_events` | `agent_shell/operator_store.py`, `agent_shell/sessions.py`, `agent_shell/audit.py` via `server/routes/operator.py`, `agent_shell/commands.py`, `gateway/operator_shell.py` | `agent_shell/`, `server/routes/ws.py` | current |
| `extension_registry`, `extension_runs` | `agent_shell/operator_store.py` | same | current |
| `runtime_sessions` | `agent_runtime/runtime_session.py` | provenance readers (DM-C09), doctor | current |

#### LLM economics (lazy, `economics/schema.py`) — [LLM Inference Harness](../contracts/llm_inference_harness_contract.md)

| Table | Writer | Readers | Status |
|---|---|---|---|
| `llm_inference_calls` | `economics/inference_calls.py` via `agents/helpers.py`, `server/routes/model.py` | usage insights, `extensions/learning/entry_context.py` | current |
| `llm_structured_attempt_outcomes` | `economics/inference_calls.py` | none | current |
| `llm_context_artifacts` | `economics/context_artifacts.py` | `extensions/learning/entry_context.py` | current |
| `runtime_cost_events` | `economics/tracing.py::record_llm_attempt` (from `model_provider/base.py`); trigger on `agent_tool_calls` | budgets, usage insights, doctor | current |
| `pricing_snapshots` | `economics/pricing_sync.py` | `economics/tracing.py`, `server/routes/economics.py` | current |
| `economics_settings`, `economics_budgets` | `economics/tracing.py`; `cli/audit_cmd.py` | `economics/` | current |
| `tool_cost_profiles`, `llm_context_pair_reviews` | none | none | residual-no-writer |

#### Audit, settings, knowledge and Learning

Learning behavior is out of this contract: [spec 0001](../specs/0001-learning-loop/spec.md) and
[Learning Contract](../contracts/learning_contract.md) own it.

| Table | Writer | Readers | Status |
|---|---|---|---|
| `audit_events` | `storage/repositories.py::insert_audit_event*` from execution, reconciliation, funding audit, scheduler | 12 modules | current |
| `settings` | `portfolio/exchange_sync.py`, `trading/venue_margin.py`, `extensions/research/round_robin.py` | same | current |
| `aletheia_schema_migrations` | migration runner (DM-C12) | `extensions/observation_lab/checkpoint.py` | current |
| `knowledge_items`, `knowledge_fts` | `storage/knowledge_index.py`, `extensions/knowledge/`, `extensions/analytics/*` | retrieval, doctor, routes | current |
| `ps_event_log` | `storage/event_store.py` from `extensions/learning/lessons.py`, `skills.py`, `extensions/policies/store.py`, `extensions/analytics/guard_promotion.py` | `storage/event_store.py` | current |
| `ps_lessons`, `ps_skills`, `ps_policy_versions` | `extensions/learning/lessons.py`, `extensions/learning/skills.py`, `extensions/policies/store.py` | learning control, routes, doctor | current |
| `learning_hypotheses`, `learning_hypothesis_nominations`, `learning_job_health`, `learning_lesson_reviewer_attempts`, `learning_policy_epochs` | `extensions/learning/` | `extensions/learning/`, profit-gradient evidence | current |
| `learning_experiments`, `learning_exposures`, `learning_contribution_verdicts`, `learning_lifecycle_events` | none (`insert_record` refuses them); only on databases that predate the Lab cutover | `extensions/learning/ledger.py`, profit-gradient evidence | retired-readable |
| `learning_inference_decisions` | none; pre-cutover databases only | none | residual-no-writer |

#### Observation Lab authority (in `app.db`) — [Observation Lab Contract](../contracts/observation_lab_contract.md)

`observation_authority_state`, `observation_authority_cutover_proofs`,
`observation_delivery_outbox`, `observation_deny_tombstones`: writer
`extensions/observation_lab/canonical.py`; readers the Lab, `cli/observe_cmd.py`,
`server/routes/settings.py`, backup; current. All other `observation_*`, `lab_*`,
`execution_manifests`, `joint_checkpoints` and `ufc_shadow_equivalence` (no writer) live in `lab.db`.

#### Serenity — [Serenity Contract](../contracts/serenity_contract.md)

`serenity_themes`, `_evidence`, `_opportunities`, `_risk_gates`, `_replay_snapshots`,
`_post_mortems`, `_research_runs`, `_run_events`, `_user_profiles`, `_profile_snapshots`,
`_feedback_events`, `_claims`, `_theme_claims`, `_agent_locks`: writers `server/routes/data.py`,
`extensions/research/research_worker.py`, `trading/serenity_publication.py`,
`extensions/learning/outcome_feedback.py`; current (Serenity is off by default).

#### PS layer residue

| Table | Readers | Status |
|---|---|---|
| `ps_gateway_sessions`, `ps_orders`, `ps_positions`, `ps_reconciliation_reports` | `server/routes/persistence.py` | retired-readable |
| `ps_checkpoints`, `ps_execution_intents`, `ps_fills`, `ps_gateway_locks`, `ps_job_runs`, `ps_order_intents`, `ps_scheduled_jobs` | none | residual-no-writer |

#### Other residue

`conditional_entry_authorizations`, `opportunity_entry_validity_assessments`,
`paper_funding_revival_cursor` and `position_path_observer_cursor` (its writer function has no
production caller) have no production writer or reader: residual-no-writer.

### 4. Prunable whitelist

`storage/repositories.py::_PRUNABLE` maps each prunable telemetry table to its timestamp
column; `_CONDITIONALLY_PRUNABLE` holds `pi_decisions` and `knowledge_items`, prunable only
with a caller filter (DM-C11). Windows and cascade order: [RETENTION_POLICY.md](RETENTION_POLICY.md).

### 5. Position outcome-integrity columns

`positions` carries `close_category`, `exit_price_source`, `exit_fill_verified`,
`outcome_eligible`, `outcome_exclusion_reason`, `reconciliation_identity`,
`reconciliation_evidence`, `outcome_semantics_version`, `funding_cost` and `funding_source`.
Raw close fields are never rewritten; unknown economics stay NULL. Semantics:
[Outcome Integrity Contract](../contracts/outcome_integrity_contract.md).

### 6. Migration ledger

1. `aletheia_schema_migrations` is the append-only, per-database proof of applied migrations (DM-C12).
2. Only `aletheia data migrate` and `gateway start/restart` apply migrations (DM-C13).
3. The backend lifespan verifies the ledger read-only (DM-C13).
4. A checksum mismatch or an unknown ledger row fails closed; a code rollback needs a compatible backup (DM-C12).
5. An interrupted migration leaves no success row and is retried, so each step must be re-runnable after a partial autocommitted DDL (DM-C12).
6. The imported legacy schema is one frozen baseline migration; later changes are versioned steps and `_ADDITIVE_COLUMNS` (DM-C14).
7. Pytest clones an in-memory blueprint and records the same ledger rows; fixtures may call the `run_migrations()` alias.

### 7. Never-pruned set

Absent from `_PRUNABLE` (pinned by `tests/test_architecture_fitness.py`):

- audit chain: `trade_proposals`, `risk_decisions`, `orders`, `fills`, `positions`,
  `audit_events`, `capital_events`, `approvals`, `knowledge_items` (except filtered
  `system_action` rows, DM-C11), `ps_orders`, `ps_fills`, `ps_positions`;
- Learning ledger: `learning_experiments`, `learning_exposures`, `learning_hypotheses`,
  `learning_hypothesis_nominations`, `learning_policy_epochs`, `learning_contribution_verdicts`,
  `learning_inference_decisions`, `learning_lifecycle_events`, `learning_job_health`;
- trading integrity: `position_close_events`, `position_disappearance_candidates`,
  `position_disappearance_observations`, `reconciliation_integrity_incidents`,
  `risk_policy_snapshots`, `candidate_planning_attempt_outcomes`, `persona_rollouts`,
  `persona_rollout_events`;
- archived evidence (§11).

### 8. JSON columns

Stored as TEXT; read tolerantly (DM-C17). JSONL replay skips undecodable lines
(`files/jsonl.py`).

### 9. Persona rollout

`persona_rollouts` / `persona_rollout_events` (writer `trading/persona_rollout.py`, readers
`trading/persona.py`, `server/routes/settings.py`): current; the capability is on hold under
[spec 0003 FC-R07](../specs/0003-futures-only-convergence/spec.md).

### 10. Dropped tables

| Table | Removed by |
|---|---|
| `backtest_runs`, `backtest_trades` | `20260929_0090_drop_backtest_tables_v91` |
| `strategy_records` | `20260929_0092_drop_strategy_records_v92` |
| `universe_forward_outcomes` | `20260929_0094_drop_universe_forward_outcomes_v94` |
| `runtime_registry`, `runtime_checkpoints`, `runtime_events`, `runtime_exclusive_lock`, `runtime_ownership` | `migrations.py::retire_runtime_continuity_tables` (never installed on a fresh database) |
| `strategy_experiments`, legacy `learning_*` experiment tables, `filter_shadow_*` | `migrations.py::retire_legacy_experiment_tables` (fresh Lab install or explicit opt-in) |

### 11. Archived simulation evidence

No writer; rows keep their original meaning and never qualify external execution.
Definitions: [archived account controls](../history/local_paper_account_controls.md),
[archived protection](../history/local_simulated_protection.md).

| Table | Readers | Status |
|---|---|---|
| `paper_account_events` | `portfolio/account_loss.py`, `storage/repositories_execution.py`, `ops/maintenance/outcome_integrity.py` | retired-readable |
| `position_exit_plan_versions`, `position_protection_state`, `position_protection_triggers` | position replay, trade path, lifecycle story analytics | retired-readable |
| `exit_breakeven_shadow_assignments`, `exit_breakeven_shadow_outcomes` | `analytics/exit_breakeven_shadow.py`, `analytics/entry_exit_geometry.py` | retired-readable |
| `historical_accounting_reconciliations` | `maintenance/historical_accounting.py` | retired-readable |

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| writer permit not acquired in 6 s | write fails | `write_lock_timeout`, `writer_metrics_snapshot` | no partial write |
| writer queue full before admission | write rejected | `write_queue_timeout` | caller decides retry |
| pending, unknown or drifted migration | backend refuses to start | `assert_migrations_current` error naming `aletheia data migrate` | — |
| prune requested for a non-allowlisted table | `ValueError` | exception | nothing deleted |
| malformed JSON column | value skipped / raw string | reader output | never a fabricated value |
| legacy row without provenance | NULL kept | NULL columns | unknown, not current session |

## Authority

Storage may persist and prune telemetry; it may never rewrite applied migrations, prune the
never-pruned set, or change Risk, Gateway, credential or Live state on its own.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| DM-C01 | `tests/specs/spec_data_model.py::DM-C01` |
| DM-C02 | `tests/specs/spec_data_model.py::DM-S08`, `DM-S09`, `DM-B06`, `DM-C02` |
| DM-C03 | `tests/specs/spec_data_model.py::DM-S01`, `DM-S02`, `DM-S03`, `DM-B01`, `DM-B07` |
| DM-C04, DM-C05 | `tests/test_sqlite_writer.py` |
| DM-C06 | `tests/specs/spec_data_model.py::DM-B02`, `tests/test_sqlite_writer.py` |
| DM-C07, DM-C16 | review rule; no executable check |
| DM-C08 | no focused test found |
| DM-C09 | `tests/test_runtime_session_provenance.py` |
| DM-C10 | `tests/audit/test_table_read_write_pairing.py` |
| DM-C11 | `tests/specs/spec_data_model.py::DM-S04`, `DM-B05`; `tests/test_architecture_fitness.py` |
| DM-C12, DM-C13 | `tests/test_migration_fast_path.py`; `tests/specs/spec_data_model.py::DM-B04` |
| DM-C14 | `tests/test_migration_fast_path.py` (per-version additive tests) |
| DM-C15 | `tests/test_database_capacity.py` (offline compact) |
| DM-C17 | `tests/specs/spec_data_model.py::DM-B03` |

### Known conformance gaps

- **DM-C14 vs dropping migrations.** `migrations.py::_apply_drop_backtest_tables_v91`,
  `_apply_drop_strategy_records_v92`, the v94 `DROP TABLE IF EXISTS universe_forward_outcomes`,
  `retire_runtime_continuity_tables` and `retire_legacy_experiment_tables` drop tables, while
  the [2026-09-07 decision](../../governance/decisions/DECISIONS.md) says tables are never
  deleted. Owner decision needed: record these as approved exceptions (and the rule for future
  drops), or stop dropping.
- **Writerless tables still read.** `operator_evaluation_events` has three diagnostics readers
  and no writer, yet `tests/specs/spec_data_model.py::DM-S29` is `implemented` and checks only
  the DDL; the same holds for `shadow_trades`, `universe_relative_rank_shadow_batches` and the
  PS readers in `server/routes/persistence.py` (listed in the pairing baseline).
- **Raw external errors.** The convention in
  [SAFETY_AND_TRUST](../../operations/development/agent-guides/SAFETY_AND_TRUST.md) (#3880) keeps
  a bounded token and the redacted raw error; only `orders.raw_error` exists.
  `collector_health_state.last_error_code` stores the exception class name and
  `ohlcv_backfill_state.last_error` a bounded token, with no raw-error column.
- **Out-of-scope producers write live tables.** `equity_observer` (scheduled) writes
  `opportunities`; `prediction_observer` is registered and writes `prediction_*` when enabled
  ([spec 0003 FC-R04](../specs/0003-futures-only-convergence/spec.md)).
- **Write-only tables.** `options_market_structure_snapshots`, `llm_structured_attempt_outcomes`,
  and in practice `coingecko_top_snapshots` and `onchain_snapshots` (doctor only), have no
  decision reader ([Data Utilization](data_utilization.md) R1).

## Decision references

- ID format kept at 12 hex characters (CR-DM-01, 2026-06-23): collision risk is negligible at
  this scale and short ids stay readable in logs.
- Proposal hash chain: P0-1 (2026-06-29), borrowed from OpenAlice `TradingGit`.
- Writer permit timeout lowered from 32 s to 6 s (comment in `storage/sqlite.py`): a long wait
  means a transaction held across I/O, which is a caller bug.
- Policy A (2026-09-07, DECISIONS.md): additive-only applies to the data layer, not the code layer.
- Table drops: #3701 (`universe_forward_outcomes`), #3702 (`strategy_records`), #3703 (backtest),
  #2945 (Runtime Continuity), #2737 (legacy experiment tables); shadow-trade writer retired #3704.
- Former text (phase notes, per-version migration list, retention tables, Learning measurement
  narrative, Pydantic model index): [history snapshot](../history/data_model_reference_2026-10-10.md).
