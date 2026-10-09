# Data Model Reference

> Complete index of Aletheia Trading OS data layer.
> SQLite tables and Pydantic models, WAL mode, versioned additive migrations.
> Source of truth: `aletheia/storage/migrations.py` (DDL) + `models.py` (types).
> Last updated: 2026-09-10

> Issue #2737: the [Phase 1 Migration Contract](../contracts/observation_experiment_ownership_v2.md) freezes current legacy storage, while the [accepted Proposal](../proposals/observation_experiment_ownership_v2.md) owns the future Lab V2 target. Before Phase 8, existing legacy Experiment tables and authority remain active; before Phase 11, no physical legacy retirement is implied.

---

## 1. Architecture Overview

- **Engine**: SQLite 3, WAL mode, `busy_timeout=30000ms` (30s, fallback only), `synchronous=NORMAL`, `wal_autocheckpoint=1000`, per-connection `journal_size_limit=67108864`, `isolation_level=None` (true autocommit, no implicit BEGIN)
- **Concurrency**: one read/legacy connection per thread via `threading.local`; scheduler capped at `max_workers=3`
- **Write serialization**: transaction-external repository DML enters a bounded 256-item writer lane backed by one dedicated connection. Accepted jobs synchronously return an exact commit/rollback result; queue admission may time out before acceptance, but an accepted authoritative write never returns an ambiguous timeout. Existing multi-statement `transaction()` closures stay on their caller connection and share the same writer permit.
- **Cross-process safety**: the process `_WRITE_LOCK` is paired with an advisory `app.write.lock` file permit, automatically released by the OS on process death. Gateway, collector, and supervisor writers therefore contend before SQLite's writer slot; `busy_timeout` remains the last-resort fallback.
- **Legacy guard**: production connections subclass `sqlite3.Connection` and serialize direct autocommit DML that has not yet migrated to `execute_write()`; migrated modules have a static no-direct-DML regression test.
- **Transaction safety**: migrated writes use `run_write()`/`execute_write()`; legacy write paths still capture `was_in_tx = db.in_transaction` before writing and commit only when it was false.
- **Migrations**: stable migration ID + immutable checksum ledger; additive-only (new tables, new columns with defaults); never DROP/RENAME in production
- **ID format**: 12-char hex string — `uuid4().hex[:12]` (48-bit), via `new_id()` in `storage/models.py:8`. NOT a full 36-char UUID4. Collision risk is negligible at this scale (P≈2e-7 at 10⁴ rows, ≈1e-3 at 10⁶); the short form keeps IDs readable in logs/audit. (CR-DM-01: doc corrected to match code 2026-06-23 — code is the convention, full-UUID4 migration deemed unjustified.)
- **Timestamps**: ISO 8601 UTC strings (`2026-06-21T12:00:00+00:00`)
- **JSON columns**: TEXT with `json()` validation where applicable
- **Booleans**: INTEGER 0/1

---

## 2. Core Trading Pipeline (causal chain)

Current entries pass from market evidence and planning into an immutable
TradeProposal, then Risk approval, an exchange Order and exact private Fills.
Position closure records economic evidence and queues eligible feedback;
portfolio snapshots report current external exposure and available account equity.
Historical simulation records remain separately readable (§11).

---

## 3. Table Groups

### A. Trading Core

| Table | Purpose | Key columns | Written by |
|-------|---------|-------------|------------|
| `opportunities` | Independent-producer carriers plus historical Universe compatibility rows. New Universe-linked opening work uses Candidate Planning Generation + Entry Plan and does not create executable Opportunity rows | symbol, direction, confidence, key_observations (JSON), historical setup/member/generation fields | independent producers / compatibility recovery |
| `candidate_planning_generations` | Durable thesis-level planning identity and single on-demand claim/result owner; normal snapshot/rank/score/price churn does not reopen planning | planning key, material hash, current flag, claim lease, No-Plan/Entry Plan/stale result | Universe publication + Entry Planner |
| `candidate_planning_attempt_outcomes` | Append-only history of every planning outcome (schema v96, #3852). The generation row keeps only the current outcome; each outcome write appends one immutable row in the same transaction, so a retried generation keeps every earlier reason. Never pruned | generation_id, per-generation attempt_index, outcome_class, stage, reason, bounded detail, created_at | Entry Planner outcome writers (`_write_planning_outcome`) |
| `entry_plans` | Immutable generic approved plan and monotonic execution lifecycle for the current Candidate planning generation | exact instrument, plan JSON/hash, execution mode, status, Order link, terminal reason | Entry Planner + Fast Monitoring + Gateway/reconciliation |
| `trade_proposals` | Actionable trade candidates | symbol, direction, entry_price, stop_price, target_price, **`agent_rationale.entry_parameters`** (final entry/stop/target/notional/leverage reasons + value sources), **commit_hash**, **parent_hash** (TaG, P0-1) | Gateway / Entry Plan / external position adoption |
| `proposal_execution_claims` | One durable, expiring execution owner per Proposal; kept outside the hash-covered Proposal intent and removed only by owner-token compare-and-set | proposal_id, owner_token, claimed_at, lease_expires_at | execution gateway |
| `entry_risk_reservations` | Durable pre-Order allowance acquired atomically with authoritative Risk; retained after a worker crash and superseded by the first durable Order | proposal_id, proposal_commit_hash, symbol, market_type, execution_mode, quantity, price, leverage | risk engine / execution gateway |
| `risk_decisions` | Risk engine approval/rejection | proposal_id, approved, reasons (JSON), decision_role, evaluated_quantity, proposal_commit_hash, risk_mode, policy_id, policy_revision, policy_hash | risk engine |
| `approvals` | Human approval records | proposal_id, decision, decided_by | frontend |
| `orders` | Execution orders; native exchange entry `pending` is an unresolved Risk reservation, not zero exposure | symbol, side, quantity, order_type, status | execution gateway |
| `fills` | Order fill confirmations | order_id, fill_price, fill_quantity, fee | broker adapter |
| `positions` | Open/closed positions; a closed future may keep net economics unavailable. Market-aware evaluation remains advisory: its structured evidence records the exact venue/symbol/physical-market identity and source rows used for an Operator wake. | symbol, direction, entry_price, quantity, status, nullable realized_pnl, funding_cost, funding_source, **sizing_manifest** (hash-checked Persona/policy/step assignment copied from the Proposal), **exit_reasoning** (actual close actor/source/trigger; NULL for historical absence), **exit_limit_source** ('human'|'system_operator'; author of a resting limit exit — selects human_limit_exit vs operator_limit_exit in close_reason for attribution honesty), **exit_plan_effective_at** (nullable historical barrier-plan projection; current broker protection does not activate the archived barrier), **last_market_signal_evidence** (versioned JSON; exact position/signal identities, Opportunity/member/asset/Feature provenance, evaluation and delivery result) | exchange materialization / position_manager / outcome closure / broker funding settler |
| `position_close_events` | **Per-close economic ledger** (exit-intent R1) — one row per close EVENT (full close = 1 row; partial reduce = 1 row per reduction) so realized PnL sums from close events, unifying full + partial closes. The daily-loss breaker adds partial-close realized via this ledger (`is_partial=1`) on top of the verified full-close read | position_id, close_reason, exit_price, closed_qty, realized_pnl, entry_fee, exit_fee, is_partial, closed_at | `pnl.close_position` / `pnl.reduce_position` / verified outcome closure |
| `outcome_hook_jobs` | Durable eligible-close feedback outbox; one row per Position and hook, with leased replay after failure | position_id, hook_type, status, attempts, next_attempt_at, claim_token, last_error | outcome close transaction / outcome_hook_replay |
| `position_disappearance_candidates` | Durable account-scoped position-disappearance confirmation state; never authorizes close by itself | position_id, identity_key, broker_id, account_environment, non-secret account_fingerprint, instrument identity, confirmation_count, direct_query_state, semantics_version | position reconciler (OI-B01 target) |
| `position_disappearance_observations` | Immutable idempotent evidence for a disappearance candidate | candidate_id, observation_hash, view_identity, completeness/absence flags, direct_query_state, evidence_json | position reconciler (OI-B01 target) |
| `reconciliation_integrity_incidents` | Restart-safe close/reopen circuit breaker and explicit recovery evidence | scoped fingerprint, status, source mix, detected/clean timestamps, clean count, resolution actor/reason, semantics version | position reconciler + Learning control |
| `broker_funding_settlement_cursor` | Singleton durable scheduling cursor for unresolved exchange-close funding recovery; it rotates retryable/conflicting prefixes without owning Position state | queue_id, last_closed_at, last_position_rowid, updated_at | broker funding settler |
| `shadow_trades` | What-if trades for comparison. A legacy batch derived from a later-invalidated Position remains retained but is marked invalid and excluded from decision reads. | opportunity_id, venue, market_type, outcome_candidate_key, gross_return_pct, net_pnl_pct, estimated costs, invalidated_at, invalidation_reason | counterfactual |
| `portfolio_snapshots` | Periodic portfolio/account snapshots; `account_equity_usdt` is the only field eligible for the Overview account-equity curve. Legacy local rows with only `total_value_usdt` are position notional and excluded from that curve. The schema admits NULL realized PnL; readers distinguish unknown economics from zero. | total_value_usdt, account_equity_usdt, unrealized_pnl, nullable realized_pnl, execution_mode | scheduler job |
| `market_regimes` | Detected market regimes | regime_type, confidence, indicators (JSON) | market_observer |
| `persona_rollouts` | Restart-safe progressive Persona authority state; one current identity per active rollout and immutable refreeze/step baseline | rollout_id, persona_id/fingerprint, policy_epoch/runtime_version, current_step/parameter, step_started_at, baseline_snapshot, evidence_watermark, status (`frozen`/`observing`/`eligible`/`unlocked`/`refrozen`/`completed`), authority/reason | Persona resolver / rollout scheduler |
| `persona_rollout_events` | Append-only audit/evidence transitions for each rollout | rollout_id, step/parameter, status, event_type, authority, reason, evidence_json, actor/source, created_at | Persona resolver / rollout scheduler |

Open `opportunities` are read through a bounded mixed workset: half of the normal
page is reserved for `last_ranked_at IS NULL` rows and half for the oldest non-NULL
retry rows, with unused capacity borrowed by the other half. The partial expression
indexes `idx_opportunities_trade_unranked_rotation` and
`idx_opportunities_trade_retry_rotation` provide the two ordered streams without a
full table scan or temporary sort.

The owner-facing current-entry projection uses
`idx_opportunities_status_created`, `idx_filter_decisions_opportunity_evaluated`,
and `idx_filter_decisions_opportunity_run`. These indexes keep the bounded candidate
view from repeating full `opportunities` or `filter_decisions` scans for every row;
they do not change ranker, queue, or decision semantics.

Queued market-observer work is admission-bounded to 5 unresolved identities. A
superseded parent snapshot atomically expires both open and queued children with an
immutable `universe_snapshot_superseded` filter fact. Old `signal_pending` rows are
transient carriers, but cleanup may remove them only after their source is terminal
and no `submitting` recovery claim exists. Until cleanup, an expired source is excluded
from the Operator working set unless a `submitting` claim requires crash recovery.

Market-aware Position evidence is fail-closed. An Opportunity can trigger an
advisory wake only when `opportunities.universe_member_id` resolves through
`universe_members.asset_id` to a `universe_registry` row matching the Position's
venue, canonical symbol, and physical market type (`usdt_futures` normalizes to
`perp`). The Opportunity must still be fresh and explicitly unexpired; Feature
data is read only from that same `asset_id`. Legacy symbol-only, cross-venue, or
spot/perpetual-ambiguous rows produce no wake. This evidence never authorizes an
automatic close or stop change.

### B. Strategy Management (4 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `strategy_records` | **Legacy read-only** (rules engine removed Position C 2026-07-02): historical strategy defs; no longer written. Kept for `strategy_id` attribution | name, status, parameters (JSON), direction_affinity |
| `strategy_experiments` | **Legacy read-only** (A/B variants; engine removed Position C) | strategy_id, variant, metrics (JSON) |
| `backtest_runs` | **Legacy read-only** (backtest engine removed Position C); historical only | strategy_id, timeframe, sharpe, profit_factor |
| `backtest_trades` | **Legacy read-only** (backtest engine removed Position C); historical only | run_id, entry_time, exit_time, pnl |

### C. Knowledge & Learning (3 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `knowledge_items` | Canonical rendered Knowledge documents and their advisory retrieval projection | item_type, data-root-relative file_path, complete content, content_format, content_sha256, metadata_json, symbol, direction, scanner_family, outcome, tags, confidence |
| `news_items` | External news signals | source, headline, sentiment, symbols (JSON) |
| `market_ohlcv` | Historical candlestick data; rows are isolated by full instrument identity. Explicit offline compact may cluster the composite PK as `WITHOUT ROWID`; the logical schema is unchanged. | exchange, symbol, market_type, timeframe, ts, open, high, low, close, volume |

### C-bis. Measured Learning Ledger (9 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `learning_hypotheses` | Canonical scoped Learning hypothesis | hypothesis_id, fingerprint, path_id, scope/action JSON, status, version |
| `learning_hypothesis_nominations` | Append-only generation/research lineage; many attempts may resolve to one canonical behavior hypothesis | nomination id, behavior fingerprint, source bundle hash, optional hypothesis id, research/publication status, immutable envelope |
| `learning_experiments` | One isolated shadow or paper stage; a `registered` experiment's first exposure atomically/idempotently activates it and anchors `started_at` to earliest durable `exposed_at` | experiment_id, hypothesis_id, parent experiment, activation verdict, stage generation, assignment salt, allocation, status, started_at |
| `learning_exposures` | Immutable record of one actual/control influence; `assignment_unit_key` persists the randomized cluster independently from member candidate identity | dedup_key, assignment, assignment_unit_key, entity ids, before/after/manifest, outcome_candidate_key, experiment stage, policy epoch, behavior_contract_hash, provenance code_commit |
| `learning_contribution_verdicts` | Append-only measured verdict window; recurring monitoring is descriptive and only a consumed confirmatory row may be authority-eligible | stage, unique cluster-arm counts, delta/interval, harm metrics, metric_version, behavior_contract_hash, inference role/plan/cutoff identity, measurement verdict, created_at |
| `learning_inference_decisions` | Retained prospective fixed-look plan and once-only decision binding for each Paper experiment | inference family/contract, immutable plan/hash, frozen cutoff/fingerprint, authority alpha, consumed verdict id/time, status |
| `learning_lifecycle_events` | Durable transition/blocked-promotion record and JSONL audit outbox; historical rows default mirrored, new rows remain pending until stable-id append acknowledgement | event_id, dedup key, experiment/successor/verdict ids, from/to stage, action, reason, policy epoch, audit actor/source/status/mirrored_at/failure reason |
| `learning_job_health` | Durable expected-run/backlog health; current error may clear on recovery while last failure identity persists | schedule, watermarks, consecutive_failures, status, last_error, last_failed_at, last_failure_reason |
| `learning_policy_epochs` | Non-sensitive effective-policy attribution boundary | effective-values hash/JSON, affected paths, safety flag, interval |

These tables were additive/observational in Phase 1. The currently
measurement-supported paths are `universe_family_calibration`,
`momentum_entry_geometry`, `mechanism_entry_learning`, and `ranker_case_score`,
each with a path-specific causal outcome contract.
`ranker_case_score` is Shadow-only and cannot apply a Ranker delta or Paper
authority. Operator, factor, ranker-memo, learned-guard, automatic Risk-lesson,
Observer-bundle and persona-sizing hooks are consumer-wired but
measurement-unsupported: they return static fallback and write no formal
exposure. Only an active, stage-isolated paper experiment with enough linked
cost-aware outcomes and separately validated owner/lifecycle authority may
apply a supported treatment. The ledger grants no risk, execution, credential,
or live authority.

For current universe contribution measurement, `filter_candidate_outcomes` is the sole
full-arm source (the selected-member `universe_forward_outcomes` mirror was dropped in v94,
#3701). Exact-key reads are batched. One
complete persisted `assignment_unit_key` cluster contributes one intent-to-treat allocation-utility
observation: selected members contribute net return, dropped members contribute zero, and worst
selected-member MAE is retained. `allocation_selected` is immutable and nullable only for legacy or
non-allocation outcomes. Incomplete clusters contribute no survivor sample. Legacy exposures without
an assignment unit remain permanent and auditable but are excluded, never inferred.

### C-ter. Learning Contribution projection boundary

The `lco-v1` projection itself adds no table, materialized projection, config
field, data source, retention rule, or migration. `lco-v1` is a bounded
read-only projection over the existing ledger and is implemented for summary
and the default dashboard contribution tab. Any broader derived model is
defined only in the
[`learning_observability`](../proposals/learning_observability.md) Proposal
until it becomes current behavior.

Projection output never rewrites or normalizes stored history in place. Old metric, policy epoch,
stage, horizon, outcome-contract and code-build verdicts remain append-only historical rows and are
classified as non-current by the read model.

The current metric version is
`universe_family_calibration_cluster_allocation_utility_v10`. Its existing `harm_metrics_json`
evaluation envelope also carries bounded `market_day_coverage` facts for the latest consecutive
eligible UTC exposure-day window, its per-arm sample counts, separate treatment-permission and
effective-intervention counts, bounded non-intervention reasons, and conditional-randomization
inference provenance. Effective intervention is
derived from finite exposure `before_json`/`after_json` values rather than inferred from permission.
A Shadow-only integrity result additionally records that one current market day and 10 complete
units per arm have exercised the prospective pipeline; it remains `insufficient_data` and is not a
contribution verdict.
A metric-version change appends new verdicts; it does not rewrite or delete older verdict history.

### D. Market Data Collection (11 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `market_snapshots` | Point-in-time price/volume | symbol, price, volume_24h, change_pct_24h |
| `ohlcv_backfill_state` | Durable active-Universe catch-up/retry state; operational metadata only, pruned after 90 days inactive | exchange, symbol, market_type, timeframe, status, bar_count, consecutive_failures, next_attempt_at, last_error |
| `collector_health_state` | Bounded operational coverage: exactly one latest/cumulative row per scheduled collector domain | domain, cadence_sec, last_status, last_scheduled_at, last_success_at, consecutive_problems, attempts, timeouts, budget_skips, failures |
| `orderbook_snapshots` | Order book depth with exact instrument identity | exchange, symbol, market_type, bid/ask, spread, depth, imbalance |
| `funding_rate_snapshots` | Perpetual funding rates (multi-venue via **exchange** tag) | symbol, exchange, funding_rate, next_funding_time; `exchange="hyperliquid"` / `"hyperliquid_predicted"` are cross-venue feeds **excluded from the primary per-symbol read** (see `latest_funding_rate(exchange=...)`) |
| `open_interest_snapshots` | Open interest data (multi-venue via **exchange** tag) | symbol, exchange, open_interest, oi_change_pct, **long_short_ratio** (Round 2 Step 1 — Binance globalLongShortAccountRatio); Hyperliquid writes cross-venue OI here, excluded from the primary per-symbol read |
| `liquidation_events` | Large liquidation events | symbol, side, quantity, price |
| `perp_structure_snapshots` | Observation-only perp positioning map | symbol, venue, source, source_ref/source_hash, as_of_time, reference_price, data_quality, structure fields; consumed by advisory context and doctor only |
| `perp_structure_shadow_outcomes` | Fixed-horizon validation of owner aggregate structure bias; no decision authority | snapshot_id, symbol, venue, market_type=perp, direction, 1h/4h/24h horizon, entry/exit price, directional return, status, policy_epoch_id, source_hash |
| `macro_snapshots` | Macro economic indicators | indicator, value, source; **fng_value / fng_classification** (Round 2 Step 2 — Alt.me Fear & Greed) + **top_movers_summary** (Round 2 Step 3 — CoinGecko top movers, pre-formatted string folded into LLM macro block) |
| `coingecko_top_snapshots` (Round 2 Step 3) | Top-N coins by market cap pulled every Phase B tick | coin_id, symbol, price_usd, market_cap_usd, market_cap_rank, volume_24h_usd, price_change_1h_pct, price_change_24h_pct, price_change_7d_pct, observed_at |
| `onchain_snapshots` (datasrc overhaul) | DefiLlama on-chain regime (zero-key); folded into the LLM macro block | source, metric_type (`stablecoin_mcap` / `chain_tvl` / `dex_volume`), total_usd, change_24h_pct, top_entries (JSON), raw_summary (JSON), observed_at |

### E. Agent Runtime and schema control (17 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `agent_runs` | Agent execution records | agent_name, trigger_type, status, outputs (JSON) |
| `agent_run_steps` | Step-by-step agent traces | run_id, step_number, action, result |
| `agent_tool_calls` | Tool invocation log | run_id, inference_call_id, tool_name, arguments (JSON), result, nullable `duration_ms` measured only for a real current operation |
| `agent_run_artifacts` | Typed current-run links to durable output records; never parsed from legacy output text | run_id, artifact_type, artifact_id, created_at |
| `llm_inference_calls` | Content-free logical inference envelope | logical_call_id, caller, prompt/context/tool/schema identities and hashes, existing causal identities in environment metadata, validation outcome, retry/fallback summary |
| `runtime_cost_events` | Physical LLM/tool/data cost attempts | call_id, attempt_index, provider/model/tool, opportunity/decision/proposal IDs when explicitly known, usage, price snapshot, cost status |
| `llm_context_artifacts` | Content-free selected-context manifest | logical_call_id, caller, evaluation pair/arm/fixture, kind, stable_id, freshness, rendered_chars |
| `llm_context_pair_reviews` | Aggregate-only blinded Learning B review | pair/fixture IDs, control/treatment call IDs, quality/grounded-use/harm scores, verdict |
| `audit_events` | System-wide SQLite audit authority plus opt-in funding/Candidate-authority JSONL delivery state | actor, source, event_type, entity_id, before/after (JSON), nullable jsonl_mirror_status/jsonl_attempt_count/jsonl_last_error_class/jsonl_mirrored_at |
| `settings` | Runtime configuration store | key, value |
| ~~`runtime_registry`~~ | **RETIRED** (Runtime Continuity control plane) — no longer installed on a fresh database | — |
| ~~`runtime_checkpoints`~~ | **RETIRED** (Runtime Continuity control plane) | — |
| ~~`runtime_events`~~ | **RETIRED** (Runtime Continuity control plane) | — |
| ~~`runtime_exclusive_lock`~~ | **RETIRED** (Runtime Continuity control plane) | — |
| ~~`runtime_ownership`~~ | **RETIRED** (Runtime Continuity control plane) | — |
| `runtime_sessions` | One row per Gateway PROCESS lifecycle (session_id under a runtime_id). Core trading facts reference `session_id` via `runtime_session_id` | session_id, runtime_id, boot_commit, config_hash, behavior_fingerprint, schema_version, active_scope_id, execution_mode, nullable `economics_tracing_enabled` boot snapshot, started_at, ended_at, shutdown_clean |

> **Runtime Continuity retirement.** `migrations.py` rebinds `SCHEMA_SQL` to slice
> the `-- Runtime Continuity (PI runtime lifecycle)` block out of the imported
> baseline, commented "never install its retired control plane", and
> `retire_runtime_continuity_tables` drops the five tables from existing
> databases. `runtime_sessions` is NOT part of that block and remains current.
> The DDL text still exists in the source file, so a textual grep finds it; only
> the runtime constant excludes it. Pinned by `spec_data_model.py` DM-S06, which
> asserts the five stay absent.

| `aletheia_schema_migrations` | Immutable per-database proof that one named migration implementation completed successfully | migration_id, checksum, schema_version, applied_at, duration_ms |

`audit_events` 的四个 JSONL delivery 字段是 nullable opt-in 状态：`NULL`
表示 unmanaged，不等于 `mirrored`。只有新发生的 funding settlement/correction 或
Candidate authority admission/rollback audit
在同一业务 transaction 内写入 `pending`；普通与历史 audit 保持 `NULL`，migration
不猜测旧 JSONL 是否已经存在。partial index 按
`(jsonl_mirror_status, timestamp, id)` 支持对 `pending/failed` 行进行有界、oldest-first
reconcile。成功后写 `jsonl_mirrored_at` 并清除 error class；`mirrored` 不得被迟到失败降级。
JSONL payload 由同一 audit 行重建，不复制 raw broker ledger、provider error 或 credential。

`broker_funding_settlement_cursor` stores only the next-scan keyset authority for the single
exchange funding-recovery queue. Cursor CAS and Position accounting transactions are independent:
advancing the cursor never proves settlement or changes economics, while restart/replay resumes
after the last inspected `(closed_at,rowid)` and wraps oldest-first within the bounded scan.

**Runtime Session provenance (expand-only, nullable).** The seven core trading-fact
tables (`opportunities`, `pi_decisions`, `trade_proposals`, `risk_decisions`,
`orders`, `fills`, `positions`) carry `runtime_session_id` / `producer_commit` /
`behavior_fingerprint`, stamped at insert from the producing `runtime_sessions` row.
`positions` additionally carry `closed_runtime_session_id` / `closed_producer_commit` /
`closed_behavior_fingerprint`, stamped at close, so a restart between open and close
keeps the opening and closing (outcome) sessions distinguishable. Legacy rows stay
NULL — history is never fabricated. See `aletheia/agent_runtime/runtime_session.py`
and spec `AR-S08`.

### F. Agent Shell / Command Center (7 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `agent_shell_sessions` | User interaction sessions | session_id, mode, started_at |
| `agent_shell_commands` | All commands issued | session_id, inference_call_id, command, response, nullable `duration_ms` |
| `agent_shell_messages` | Chat-style messages | session_id, role, content |
| `agent_shell_tasks` | Background tasks | session_id, task_type, status, result |
| `agent_shell_artifacts` | Generated artifacts | task_id, artifact_type, content |
| `agent_shell_heartbeats` | Liveness pings | session_id, timestamp |
| `agent_shell_events` | Structured events | session_id, event_type, payload (JSON) |

### G. Universe Pipeline (7 tables)

`universe_members` is the durable Candidate table. The physical name is kept for
compatibility; semantically these rows are Universe-selected Candidates, not
Opportunity rows and not an executable queue.

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `universe_registry` | All known tradeable assets | symbol, exchange, asset_class, metadata (JSON) |
| `universe_feature_snapshots` | Per-instrument feature vectors; identity is `asset_id → universe_registry` | asset_id, ts, 15m RSI/ATR/return/range-in-ATR, 1h RSI/ATR/returns, 4h RSI/ATR/BB width/volume ratio, bb_squeeze, derivatives fields |
| `universe_scanner_runs` | Scanner execution log | scanner_name, symbols_scanned, signals_emitted |
| `universe_snapshots` | Universe composition snapshots | snapshot_id, created_at |
| `universe_members` | Candidate assets in a universe snapshot; legacy physical name | snapshot_id, symbol, market_type, rank, total_score (legacy compatibility), candidate_selection_policy, candidate_selection_policy_epoch, candidate_selection_utility_pct, opportunity_mechanism/version/source_component/source_cutoff/evidence_hash, scanner_direction |
| ~~`universe_forward_outcomes`~~ | Dropped in v94 (`20260929_0094_drop_universe_forward_outcomes_v94`, #3701); `filter_candidate_outcomes` carries fixed-horizon outcomes | — |
| `universe_policies` | Universe selection rules | policy_name, filters (JSON), priority |
| `universe_twin_shadow_batches` | Versioned prospective comparison evidence; Candidate Selection Utility samples its complete denominator at a 4h cadence and stops confirmatory writes after a terminal fixed look. Retention preserves the immutable prefix through first admission plus the global frozen training denominator; separately labelled later telemetry uses the ordinary 30d window. | scanner_run_id, experiment/policy epoch/behavior/source/metric identities, 4h outcome contract, blockers, promotion/authority state, batch_hash |
| `universe_twin_shadow_records` | Compact complete-population alternate-policy evidence and exact outcome linkage | scanner_run_id, candidate_index, candidate/feature/asset/instrument ids, legacy/target/assigned selections, authority state, outcome_candidate_key, compact output JSON, allowlisted replay input JSON, record_hash |

### G-bis. Opportunity Filter Evidence (6 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `filter_decisions` | Immutable per-candidate pass/reject facts for directional and low-volume gates | candidate_key, filter_name, decision, reason_code, parameter_snapshot, filter_version, outcome_candidate_key, evaluated_at |
| `filter_decision_batches` | Complete compressed facts for high-volume directionless ticker/tradability gates | run_id, filter_name, candidate_count, pass/reject/not-applicable/error counts, zlib-base64 lossless field/row-matrix candidate_facts, filter_version |
| `filter_candidate_outcomes` | Canonical full-arm exact-instrument fixed-horizon net result, including selected and dropped Learning candidates | outcome_candidate_key, venue, symbol, market_type, direction, horizon, allocation_selected, gross/directional/cost/net/MFE/MAE, status |
| `controller_recommendations` | Append-only observational filter findings; legacy action fields remain conservative | filter/stage/version/cohort dimensions, pass/reject counts, shrunk net means, evidence_verdict, status |
| `filter_shadow_experiments` | Immutable prospective plans for replay-ready monotonic threshold candidates | experiment_key, fixed scope/version, control/treatment parameters, started_at, evidence gates, status, production_mutation_allowed=0 |
| `filter_shadow_evaluations` | Append-only evidence snapshots for a prospective plan | experiment_id, disposition, control/treatment samples and coverage, net means, edge delta |

Business writers only append these rows. Daily retention removes expired analytical rows
(30d for compressed batches, 45d for decisions, and 30d for candidate outcomes, with the
active-experiment exposure guard described in §7). This retention
does not apply to authoritative proposals, RiskDecision, orders, fills, positions, or audit records.
Controller recommendations and the small experiment/evaluation records are retained
as the audit history for why a threshold was or was not advanced; they are not
production trading state and cannot authorize a parameter change.

### H. Serenity Pipeline (14 tables)

| Table | Purpose |
|-------|---------|
| `serenity_themes` | Research themes/hypotheses |
| `serenity_evidence` | Evidence supporting themes. D2 generator/evaluator split: `tier`/`confidence` = generator's self-proposal; `assessed_tier`/`assessed_by` (additive) = authoritative grade from the independent deterministic evaluator (`trading/serenity_evidence.py`) that gates scoring |
| `serenity_opportunities` | Theme-derived trading opportunities. `evidence_tier` = self-graded proposal; `assessed_tier`/`assessed_by` (additive) = authoritative tier derived from strongest assessed supporting evidence (NULL → scored as `speculative`) |
| `serenity_risk_gates` | Risk checks for Serenity trades |
| `serenity_replay_snapshots` | Decision replay for learning |
| `serenity_post_mortems` | Post-trade analysis; automatic close feedback is idempotent by nullable unique `position_id` (manual/historical rows may remain NULL) |
| `serenity_research_runs` | Research execution records |
| `serenity_run_events` | Research run events |
| `serenity_user_profiles` | Researcher profiles |
| `serenity_profile_snapshots` | Profile state snapshots |
| `serenity_feedback_events` | Feedback loop events |
| `serenity_claims` | Falsifiable claims from research |
| `serenity_theme_claims` | Junction: themes ↔ claims mapping |
| `serenity_agent_locks` | Concurrency lock for Serenity agent runs |

### I. PS (Production System) Layer (14 tables)

| Table | Purpose |
|-------|---------|
| `ps_event_log` | Event sourcing log for PS |
| `ps_gateway_sessions` | PS gateway sessions |
| `ps_gateway_locks` | Distributed lock mechanism |
| `ps_reconciliation_reports` | State reconciliation reports |
| `ps_execution_intents` | Declared execution intents |
| `ps_order_intents` | Order placement intents |
| `ps_orders` | PS-layer order records |
| `ps_fills` | PS-layer fill records |
| `ps_positions` | PS-layer position records |
| `ps_checkpoints` | PS progress checkpoints |
| `ps_lessons` | PS-derived lessons |
| `ps_skills` | Learned trading skills |
| `ps_policy_versions` | Policy version history |
| `ps_scheduled_jobs` | Scheduled job definitions for PS layer |
| `ps_job_runs` | Job execution history |

### I-bis. Capital (1 table, additive migration)

| Table | Purpose |
|-------|---------|
| `capital_events` | Deposits/withdrawals/transfers tracking (detected_at, amount, event_type) |

### J. Prediction Markets (5 tables)

| Table | Purpose |
|-------|---------|
| `prediction_observer_runs` | Prediction scanning runs |
| `prediction_events` | Raw prediction events |
| `prediction_markets` | Active prediction markets |
| `prediction_probability_snapshots` | Probability time series |
| `prediction_market_claims` | Claims derived from predictions |

### K. System Operator Decisions and Observations (2 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `pi_decisions` | Structured System Operator decisions | action, instrument, conviction, reasoning, causal IDs/status, learning_manifest_json, **evidence_manifest_json** (actual technical/HL/Position X source identities), debate trace fields |
| `operator_evaluation_events` | Append-only non-trading observations when no candidate can be evaluated | event_kind, trigger, pending counts/oldest time, compact queue/data-health JSON, policy_epoch_id, dedup_key |

> `operator_evaluation_events` is intentionally not a `PiDecision`: a no-pending heartbeat has no
> candidate identity, trade intent, proposal authority, or filter outcome. Its dedup key scopes
> `event_kind + trigger + policy_epoch + queue snapshot` to a five-minute observation window so
> repeated heartbeats remain visible without becoming unbounded duplicates. It stores no raw prompt,
> provider exception, or credential and is consumed read-only by the skip-halt doctor probe. It is
> operational telemetry, retained for 30 days by the normal cleanup job rather than treated as a
> permanent trading fact.

> `signal_source ∈ {'market_observer','serenity'}` (default `'market_observer'`) — discriminator for which table `opportunity_id` resolves against. Both `opportunities.id` (market_observer) and `serenity_opportunities.id` are bare `uuid4().hex[:12]` with no type tag, so the id alone is cross-table ambiguous. Stamped by `_attach_opportunity_context` (market_observer takes precedence). Phase 2 of Serenity Option A — see `docs/archive/legacy/plans/trading-pipeline/serenity_execution_path.md` §3.2.

> **`trade_proposals.trade_school` / `positions.trade_school` ∈ {'short_term','serenity'}** (default `'short_term'`, NOT NULL) — EXECUTION-level discriminator. Distinct from `pi_decisions.signal_source` which lives at DECISION level. Derived in `trading_decision.submit_decision_as_proposal` from the upstream `signal_source`, then propagated through `paper_tools` → `gateway.submit_proposal` → all four brokers (paper/bitget/binance/okx) onto the `Position` row. `position_manager` reads `trade_school` to (a) skip trailing/breakeven entirely for serenity holdings, (b) gate `max_hold` between `cfg.trading.max_position_hold_hours` (short_term) and `cfg.serenity.max_hold_days * 24` (serenity). Phase 4 of Serenity Option A — see `docs/archive/legacy/plans/trading-pipeline/serenity_execution_path.md` §5.

> **Debate trace fields** (`debate_initial_conviction`, `debate_bull_confidence`, `debate_bear_confidence`, `debate_adjustment`) are audit-only fields on `pi_decisions`. They preserve how a Bull/Bear debate changed the System Operator's conviction after the initial LLM decision. Legacy rows and non-debated rows keep these fields `NULL`; audit readers must treat NULL as missing data, not synthesize a value.

> **`trade_proposals.commit_hash` / `trade_proposals.parent_hash`** (both `TEXT`, both nullable on legacy rows) — TaG proposal hash chain (P0-1, 2026-06-29, borrow from OpenAlice `TradingGit`). `commit_hash` is the 16-hex-char SHA-256 prefix of the canonical-JSON dump of the proposal's **immutable** fields — i.e. `model_dump(exclude={"status", "commit_hash"})` — joined with `parent_hash`. `parent_hash` references the previous row's `commit_hash` (ordered by `rowid DESC, commit_hash NOT NULL`), forming a **global single chain** across all brokers / strategies / execution modes. Auto-filled by `repositories.insert_trade_proposal()` only when `p.commit_hash is None`; explicit hash on the model object is preserved (used by re-insert / replay paths). Indexed by `idx_proposals_commit_hash` for O(log n) single-node lookup; full-chain verification walks the table by `rowid`. Hash input deliberately excludes `status` because the lifecycle `pending → executed / rejected` is a normal state transition that **must not** invalidate the chain. The downstream `position_adopted` audit event copies both fields into `after_state` so the JSONL audit row is independently verifiable without a `SELECT trade_proposals WHERE id=?` round-trip — see `audit_chain_contract.md §6.4`. SDD anchors: `tests/specs/spec_data_model.py` DM-S08 / DM-S09 / DM-B06 / DM-C02; design reference `docs/research/agents-learning/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md §五 P0-1`.

### L. Extensions (2 tables)

| Table | Purpose |
|-------|---------|
| `extension_registry` | Registered extension plugins |
| `extension_runs` | Extension execution history |

---

## 4. Pydantic Model Index

All models in `aletheia/storage/models.py`. Key mapping:

| Model | Table | Domain |
|-------|-------|--------|
| AuditEvent | audit_events | Runtime |
| AgentRun | agent_runs | Runtime |
| Opportunity | opportunities | Trading |
| ShadowTrade | shadow_trades | Trading |
| FilterDecision | filter_decisions | Analytics |
| FilterDecisionBatch | filter_decision_batches | Analytics |
| FilterCandidateOutcome | filter_candidate_outcomes | Analytics |
| TradeProposal | trade_proposals | Trading |
| RiskDecision | risk_decisions | Trading |
| Order | orders | Trading |
| Fill | fills | Trading |
| Position | positions | Trading |
| OutcomeHookJob | outcome_hook_jobs | Trading feedback |
| PositionDisappearanceCandidate | position_disappearance_candidates | Trading integrity |
| PositionDisappearanceObservation | position_disappearance_observations | Trading integrity |
| ReconciliationIntegrityIncident | reconciliation_integrity_incidents | Trading integrity |
| MarketRegimeRecord | market_regimes | Data |
| StrategyRecord | strategy_records | Strategy |
| KnowledgeItem | knowledge_items | Knowledge |
| MarketSnapshot | market_snapshots | Data |
| PortfolioSnapshot | portfolio_snapshots | Portfolio |
| SerenityTheme | serenity_themes | Serenity |
| SerenityEvidence | serenity_evidence | Serenity |
| SerenityOpportunity | serenity_opportunities | Serenity |
| SerenityRiskGate | serenity_risk_gates | Serenity |
| SerenityReplaySnapshot | serenity_replay_snapshots | Serenity |
| SerenityPostMortem | serenity_post_mortems | Serenity |
| SerenityResearchRun | serenity_research_runs | Serenity |
| SerenityRunEvent | serenity_run_events | Serenity |
| SerenityUserProfile | serenity_user_profiles | Serenity |
| SerenityProfileSnapshot | serenity_profile_snapshots | Serenity |
| SerenityFeedbackEvent | serenity_feedback_events | Serenity |
| SerenityClaim | serenity_claims | Serenity |
| PSEvent | ps_event_log | PS |
| PSArtifactRef | (transient) | PS |
| PSGatewaySession | ps_gateway_sessions | PS |
| PSGatewayLock | ps_gateway_locks | PS |
| PSReconciliationDiff | (transient) | PS |
| PSReconciliationReport | ps_reconciliation_reports | PS |
| PSExecutionIntent | ps_execution_intents | PS |
| PSOrderIntent | ps_order_intents | PS |
| PSOrder | ps_orders | PS |
| PSFill | ps_fills | PS |
| PSPosition | ps_positions | PS |
| PSCheckpoint | ps_checkpoints | PS |
| PSLesson | ps_lessons | PS |
| PSSkill | ps_skills | PS |
| PSPolicyVersion | ps_policy_versions | PS |
| UniverseAsset | universe_registry | Universe |
| UniverseFeatureSnapshot | universe_feature_snapshots | Universe |
| UniverseScannerRun | universe_scanner_runs | Universe |
| UniverseSnapshot | universe_snapshots | Universe |
| Candidate / UniverseMember compatibility alias | universe_members | Universe |
| UniversePolicy | universe_policies | Universe |
| PiDecision | pi_decisions | System Operator |
| OperatorEvaluationEvent | operator_evaluation_events | System Operator |

---

## 5. Schema Conventions

- **ID generation**: `new_id()` = `uuid4().hex[:12]` at model-construction time (Python-side, `storage/models.py:8`) — 12-char hex, not full UUID4 (see §1 ID format note / CR-DM-01)
- **Timestamps**: Always UTC ISO 8601; `datetime.now(timezone.utc).isoformat()`
- **JSON columns**: Stored as TEXT; Python dict/list serialized via `json.dumps()`
- **Nullable**: Most columns allow NULL; defaults specified in DDL
- **Indexes**: Created on high-query columns (symbol, status, created_at)
- **FTS5**: Virtual table `knowledge_fts` for full-text search on knowledge

---

## 6. Migration Strategy

1. Every production migration has a stable `migration_id`, immutable SHA-256 checksum, schema version, and one apply function. Applied evidence is append-only in `aletheia_schema_migrations`; an existing ID is never replayed or overwritten.
2. `uv run aletheia data migrate` is the explicit operator/deployment command. `gateway start/restart` invokes the same owner-controlled apply phase before creating the managed service and before starting its readiness timer.
3. Supervisor Runtime lookup, frontdoor/backend launch-context reads, API routes, schedulers, Telegram, and FastAPI lifespan never apply migrations. Backend lifespan performs only a read-only ledger verification and fails with the explicit migrate command when pending.
4. A checksum mismatch or a ledger row unknown to the running code fails closed. Code rollback must restore a compatible database backup or use code that knows every applied migration; it must not silently downgrade the schema.
5. The imported legacy schema is one frozen baseline migration. On a fresh database or a pre-ledger database it runs once, records its duration and checksum only after successful completion, and remains retryable without a success row after interruption.
6. Existing DB upgrades remain additive through `_ADDITIVE_COLUMNS` and versioned backfills. Future changes add a new migration step; they do not edit/replay the frozen baseline.
7. Production apply holds the process and cross-process SQLite writer permit across ledger recheck, migration execution, and success recording. This is an upgrade-only critical section; the Gateway must not be serving normal writers during it.
8. Pytest retains the in-memory blueprint optimization and records the same ledger rows in the cloned schema; test fixtures may continue to call the compatibility `run_migrations()` helper.
9. Dataset-size-sensitive repair is never an implicit read/startup side effect. Schema v3 rebuilds `knowledge_fts` once as migration `20260806_0002_startup_dataset_boundary_v3` and installs indexes for startup reconciliation and windowed telemetry reads. Normal knowledge writes maintain FTS incrementally; later full rebuilds require the explicit `knowledge reindex` maintenance command or a new versioned migration.
10. Physical packing is outside the migration ledger. In particular, `market_ohlcv` rowid-to-`WITHOUT ROWID` conversion runs only inside explicit offline `data compact --apply`, after a verified backup and with the Gateway stopped; fresh deploy and normal Gateway startup never perform it.
11. Schema v6 adds the content-free `inference_call_id` linkage columns through migration `20260806_0005_llm_inference_linkage_v6`. The LLM inference harness does not edit the frozen legacy baseline; databases that already received the columns from pre-ledger code are accepted idempotently.
12. Schema v9 adds `positions.exit_plan_effective_at`, immutable `position_exit_plan_versions`, retained `position_protection_state`, and append-only `position_protection_triggers` through migration `20260808_0008_position_protection_state_v9`. It performs no Position backfill or activation and does not install an evaluator or close consumer.
13. Schema v10 adds prospective fixed-look Learning inference decisions through migration `20260808_0009_learning_fixed_look_v10` without granting existing verdicts authority.
14. Schema v11 adds retained `exit_breakeven_shadow_assignments` and `exit_breakeven_shadow_outcomes` through migration `20260808_0010_exit_breakeven_shadow_v11`. It performs no historical backfill and grants no trading or Learning authority.
15. Schema v12 rebuilds only `learning_inference_decisions` to admit immutable `ufc-fixed-look/v2` plans while retaining every v1 row, foreign key, index, write-once trigger, and no-delete trigger. Migration `20260808_0011_learning_randomization_inference_v12` changes no exposure, verdict, trading, Risk, Execution, or Live fact.
16. Schema v13 adds prospective Universe Twin experiment, assignment, and canonical outcome-linkage columns through migration `20260809_0012_universe_twin_prospective_v13`. It preserves step-v1 authority, records no Learning exposure or verdict, and installs no Paper, Risk, Execution, or Live consumer.
17. Schema v20 adds nullable tool/shell `duration_ms`, nullable `runtime_sessions.economics_tracing_enabled`, and `agent_run_artifacts` through additive migrations. Historical values stay NULL and legacy output is not backfilled; current-run artifact links are removed before their pruned AgentRun parent.

Automatic readers must use indexed, bounded keys or a declared retained evidence window. User-requested export/backup may traverse a complete dataset, but it must stream through a cursor/disk-backed archive rather than materialize a table or archive in process memory.

---

## 7. Retention & Cleanup (data lifecycle)

> Cleanup job runs daily (`scheduler.py:_run_cleanup`); writes an audit event `data_pruned`.
> **Authoritative retention contract**: see [`RETENTION_POLICY.md`](RETENTION_POLICY.md) for the
> full per-table window, prune column, function name, cascade order, archive flag, write-guardrail
> file:line references, VACUUM strategy, and open-questions resolution (Phase 3, 2026-07-17).
> Two-class rule: **trust-bearing tables (audit chain + 8 learning tables) are NEVER pruned;
> only telemetry/market-data tables are.**

### 7.1 Prunable tables (telemetry only, Phase 3 windows)

Configurable via `cfg.retention.*` (see `aletheia/config/schema.py`). All windows below are
current defaults; see `RETENTION_POLICY.md` for rationale and cascade details.

| Table | Retention | Function |
|-------|-----------|----------|
| `agent_runs` / `agent_tool_calls` / `agent_run_artifacts` | 90d (configurable 1–90); artifact links are deleted before their AgentRun parent | `prune_agent_runs` (cascade), `prune_tool_calls` |
| `agent_run_steps` | 30d | `prune_run_steps` |
| `market_snapshots` | 30d | existing |
| `portfolio_snapshots` | 90d | existing |
| `orderbook_snapshots` / `liquidation_events` | 7d | existing |
| `funding_rate_snapshots` | max(7d, 2 × max_open_position_hold_hours) | `prune_funding_rate_snapshots` |
| `open_interest_snapshots` | 7d (was 30d) | `prune_open_interest_snapshots` |
| `perp_structure_snapshots` | 30d | existing |
| `perp_structure_shadow_outcomes` | 30d; pruned before parent snapshots | `prune_perp_structure_shadow_outcomes` |
| `universe_feature_snapshots` | 14d (was 30d); `raw` column dropped via migration | `prune_universe_feature_snapshots` |
| `ohlcv_backfill_state` | 90d since last update | existing |
| `macro_snapshots` | 90d | existing |
| `coingecko_top_snapshots` | 3d (was 14d); `coingecko_top_n` default 50 (was 100) | `prune_coingecko_top_snapshots` |
| `universe_snapshots` / `universe_members` | 30d + protected fallback-outcome guard | `prune_universe_snapshots` (cascade) |
| `universe_scanner_runs` | 30d | `prune_universe_scanner_runs` |
| `universe_twin_shadow_batches` / `universe_twin_shadow_records` | fixed 30d via `universe_scanner_runs_days`; admitted epochs additionally retain the prefix through first admission and global frozen training denominator, while post-admission telemetry expires normally; missing admission audit/unreadable policy fails closed; ≤1 batch/55min, records before batches | `prune_universe_twin_shadow_evidence` |
| `market_ohlcv` | 1m=2d / 5m=7d / 15m=30d / 1h=90d / 4h=180d / 1d=730d + hard drop `exchange='legacy_unknown'` | `prune_market_ohlcv` (new) |
| `market_regimes` | 60d + skip-if-unchanged dedup | `prune_market_regimes` (new) |
| `filter_decision_batches` | 30d (contract) | `prune_filter_decision_batches` |
| `filter_decisions` | 45d (was 90d) + earliest-exposure guard for active open experiments | `prune_filter_decisions` |
| `filter_candidate_outcomes` | 30d (was 90d) + earliest-exposure guard for active open experiments | `prune_filter_candidate_outcomes` |
| `controller_recommendations` | 30d + skip insufficient×insufficient rows | `prune_controller_recommendations` (new) |
| `filter_shadow_experiments` / `filter_shadow_evaluations` | 90d completed / cascade child; `status='collecting'` exempt | `prune_filter_shadow_experiments` (new, cascade) |
| `alpha_control_observations` | 60d + 6× cadence reduction | `prune_alpha_control_observations` (new) |
| `agent_shell_heartbeats` | 2d + UPDATE-not-INSERT dedup | `prune_agent_shell_heartbeats` |
| `agent_shell_events` | 14d + command_* payload trim + task_lifecycle coalesce | `prune_agent_shell_events` |
| `agent_shell_messages` | 3d for `message_type='context'` / 14d otherwise + cap 1 context row per session | `prune_agent_shell_messages` (type-tiered) |
| `agent_shell_artifacts` | 7d `strategy_evaluation` / 30d proposal+report+daily_summary / 14d other | `prune_agent_shell_artifacts` (type-tiered) |
| `agent_shell_tasks` | 14d completed / 30d failed+cancelled; exempt `running`/`pending` | `prune_agent_shell_tasks` (new, cascade) |
| `agent_shell_sessions` | 14d (30d if session has proposal/report/daily_summary artifact) | `prune_agent_shell_sessions` (new, cascade — children first) |
| `agent_shell_commands` | 14d | `prune_agent_shell_commands` (new) |
| `runtime_sessions` | 90d (configurable 1–90); rows referenced by retained Candidate qualification outcomes are protected | `prune_runtime_sessions` (new) |
| `serenity_evidence` / `serenity_run_events` | 60d | `prune_serenity_evidence` (new) / `prune_serenity_run_events` (new) |
| `backtest_runs` / `backtest_trades` | 60d (parent cascade) | `prune_backtest_runs` (new, cascade) |
| `opportunities` (open) | 48h → `expired` | existing |
| `agent_task_claims` (in `~/.aletheia/tasks.db`) | honour `expires_at` | new helper `_prune_agent_task_claims_tasks_db` in scheduler |

### 7.2 NEVER-pruned (trust-bearing) — enforced by `_PRUNABLE` allowlist (`repositories.py:_PRUNABLE`)

Audit chain (12): `trade_proposals` · `risk_decisions` · `orders` · `fills` · `positions` ·
`audit_events` · `capital_events` · `approvals` · `knowledge_items` · `ps_orders` · `ps_fills` ·
Archived protective-barrier evidence (3): `position_exit_plan_versions` ·
`position_protection_state` · `position_protection_triggers`. All are retained
and absent from `_PRUNABLE`.

Persona rollout authority (2): `persona_rollouts` · `persona_rollout_events`.
These append-only identity, verdict, and refreeze records are retained with the
trust-bearing chain and are absent from `_PRUNABLE`.

`ps_positions`.

Measured Learning Ledger (9): `learning_experiments` · `learning_exposures` ·
`learning_hypotheses` · `learning_hypothesis_nominations` · `learning_policy_epochs` ·
`learning_contribution_verdicts` · `learning_inference_decisions` ·
`learning_lifecycle_events` · `learning_job_health`.

> **Invariant**: the causal record chain (§2) + the learning ledger (§C-bis) are permanent.
> Any new prune target MUST be added to `_PRUNABLE` explicitly and MUST NOT include a
> trust-bearing table. Enforced by `tests/test_architecture_fitness.py`.

The filter-family retention guard anchors to `MIN(learning_exposures.exposed_at)` across active,
open experiments. A merely `registered` experiment with no exposure does not retain telemetry,
because it has no observation window yet.

### 7.3 JSONL rotation

- Trigger: file ≥ `retention.jsonl_max_mb` (default 50MB) → rename `x.jsonl` → `x.jsonl.1` → `.2`... (`files/jsonl.py:27-104`)
- Rotated files are **kept** (no auto-delete); new writes append to fresh file.
- `audit.jsonl`: SHA-256 `_prev_hash` chain across rotations, tamper-evident (`jsonl.py:182-207`).

### 7.4 Filesystem retention

Runtime artefact trees under `~/.aletheia/`. Full details in `RETENTION_POLICY.md` §4.

| Path | Window | Strategy |
|------|--------|----------|
| `~/.aletheia/agent_shell/sessions/` | 14d | DB-JOIN (mtime unreliable post-migration) + orphan sweep |
| `~/.aletheia/agent_shell/tasks/` | 7d completed / 30d failed+cancelled | DB-JOIN + orphan sweep |
| `~/.aletheia/backups/` | keep_recent=7 (count-based) | Rotate on every backup write path + daily cleanup |
| `~/.aletheia/diagnostics/` | 14d | mtime-based |
| `~/.aletheia/app.db.pre-migration-backup` | 30d after `migration.completed_at` | Conditional on `PRAGMA integrity_check == 'ok'` |
| `~/.aletheia/knowledge/` | per-subdir (30d – 365d; some permanent) | Per-subdir policy |

### 7.5 VACUUM strategy (Phase 3)

New databases set `PRAGMA auto_vacuum = INCREMENTAL` before creating schema objects.
An existing `auto_vacuum=NONE` database must set the pragma **before** a one-time
offline `VACUUM`; calling `incremental_vacuum` alone on it is a no-op.
The recurring `_run_cleanup` pass reclaims freelist pages via bounded `PRAGMA incremental_vacuum(N)`
at the tail with a bounded 16 MiB normal / 64 MiB capacity-pressure budget and records
the physical/logical size plus a non-blocking PASSIVE-checkpoint backlog to
`logs/database_capacity.jsonl`.
Full `VACUUM` runs only through explicit `aletheia data compact --apply` with the Gateway
stopped. The default 2048 MiB capacity budget is an alert threshold, not a reason to drop
authoritative writes. Compact can also rebuild an eligible `market_ohlcv` as
`WITHOUT ROWID`, after full equality verification, without changing retention or
market-data semantics. See `RETENTION_POLICY.md` §6 for the full sequence.

The due-settlement queue uses a partial index over pending rows only:
`(due_at,id) WHERE status='pending'`. Deep capacity inspection uses `dbstat` aggregate
mode and reports packing efficiency; historical ETA requires multiple exact nightly
samples and is labelled provisional until it spans at least seven days.

---

## 8. JSON Column Handling

- **Write**: bare `json.dumps()` / Pydantic `model_dump()` — **no explicit schema validation at the SQLite boundary**. Validity relies on the Pydantic model upstream.
- **Read**: tolerant — bad JSON is skipped or left as raw string, never raised:
  - Row JSON: `try: json.loads(); except (JSONDecodeError, TypeError): pass` (`repositories.py:1701-1702`)
  - JSONL replay: bad lines `continue`-skipped (`files/jsonl.py:116-119`)
- **Backward-compat**: field remap supported (e.g. `before/after` → `before_state/after_state`, `jsonl.py:220-227`).

> ⚠️ Target-state gap: JSON columns have no write-time schema enforcement. Acceptable for MVP (Pydantic guards upstream); revisit if untrusted writers appear.

---

## 9. Concurrency Recovery Protocol

> The four SQLite safeguards remain in force; the writer lane adds bounded admission and
> exact-result semantics without changing existing transaction boundaries.

- **Writer lane** (`sqlite.py`): transaction-external repository writes queue to one dedicated connection. Capacity is 256; a full queue waits at most 32 seconds before rejecting the not-yet-admitted operation. Once admitted, the caller waits for a definitive commit or rollback.
- **Write permit**: the dedicated lane, explicit `transaction()` closures, and guarded legacy autocommit statements share a re-entrant thread-owned `_WRITE_LOCK`; normal `app.db` processes additionally share the advisory `app.write.lock` permit. Both are released in `finally` blocks.
- **Transaction-leak cleanup**: scheduler calls `rollback_if_in_transaction()` before/after each job — prevents a daemon thread from permanently holding the WAL writer lock.
- **`isolation_level=None`** is the structural fix: no implicit BEGIN means a failed DML does not leave the thread `in_transaction=True`.
- **Metrics**: `writer_metrics_snapshot()` exposes capacity/depth, submitted/completed/failed counts, admission timeouts, guarded legacy writes, and maximum queue/lock/transaction times. SQL and bound values are never logged.
- **Invariant**: never open a `with transaction():` block or `run_write()` closure that spans an `await` or any network/I/O call — this would hold the global writer permit across I/O and starve all other writers.

## 10. Outcome Integrity Additive Schema（OI-S01 implemented）

`positions` 的原始关闭字段继续作为不可改写的账本事实；追加的结果完整性字段用于说明这些值是否有
真实退出证据、能否进入策略判断。已交付字段语义如下，DDL 与 `OI-S01` 保持一致：

| Field | Meaning | Default for legacy/unknown |
|---|---|---|
| `close_category` | `strategy` / `administrative` / `maintenance` / `unknown` | `unknown` |
| `exit_price_source` | 当前关闭使用 `broker_fill` / `unavailable`；归档来源值保留原样，见 §11 | `unavailable` |
| `exit_fill_verified` | 是否有不可变 execution/broker fill 原件 | `false` |
| `outcome_eligible` | 是否允许 judgement/behavior consumer 使用 | `false` |
| `outcome_exclusion_reason` | 行政关闭、fill 未验证、旧干净窗、归因缺失等稳定原因 | `unclassified` |
| `reconciliation_identity` | 无凭证的 broker/environment/account/instrument identity JSON | `NULL` |
| `reconciliation_evidence` | observation/direct-query evidence 与确认版本 JSON；历史 funding 原件保留，当前资金费来自匹配账户的私有 ledger | `NULL` |
| `outcome_semantics_version` | 生成 eligibility 的稳定语义版本 | `NULL` |
| `funding_cost` | `broker_ledger` 的账户实际 funding；正数为成本、负数为返还，未知保持 NULL，历史来源不重新分类 | `NULL` |
| `funding_source` | 当前为 `not_applicable` / `broker_ledger` / `unavailable`；历史来源与 evidence 原件保留，见 §11 | `unavailable` |

`realized_pnl` 是原始关闭经济字段，不是缺失事实的默认值。open Position 可从 `0` 开始；
closed futures 缺少可信 broker funding ledger 时保持 `NULL`。真实 close Order/Fill 和 closed
生命周期不能替代完整经济证据；`incomplete_funding_economics` 不进入 eligible Outcome/outbox。
spot 明确写 funding `0/not_applicable`。历史模拟值和证据不会被转换为 broker 来源。

只有 matching UTA profile 明确返回 `funding_history_retention_expired` 时，exchange-backed
Position 才可把自动重试状态改为 `broker_funding_history_expired`。该 terminal reason 仍保留
`funding_cost=NULL`、`realized_pnl=NULL`、`funding_source='unavailable'` 与
`outcome_eligible=false`；版本化 `reconciliation_evidence.funding_terminal` 和同 transaction
的 funding audit 记录 retention 边界。任何其他 unavailable/partial/error、malformed
evidence 或 identity/CAS conflict 都不得写这个 reason。

Portfolio 结构化汇总返回已知部分合计、完整性标识和缺失数量；缺失经济字段不能被解释为零。
`today_realized_pnl` 等没有完整性字段的标量接口在包含未知结果时返回 None。
账户净值来自可用的交易所账户快照，不能以未知成交损益或仓位名义额代替。

读模型还会重新核对历史 numeric `realized_pnl`，而不是只检查 NULL：方向、入场/退出价格、数量、
entry/exit fee 和可信 funding 无法重算出相同净值时，`GET /positions` 与 CSV export 返回
`realized_pnl=NULL`、`realized_pnl_complete=false` 和稳定缺失原因；数据库原值保留供审计/修复。
账户授权与 Portfolio 汇总检查完整 closed 历史，不使用 10,000 行之类的展示上限，否则最老坏账会在
新历史增长后错误释放资金。

迁移规则：

1. 只用 `_ADDITIVE_COLUMNS` / additive table，不 DROP、RENAME 或覆盖原列；
2. 历史 synthetic/maintenance 行可回填 `ineligible`，但不能因旧行有 `exit_price` 就猜为 verified；
3. 无法证明的历史行保持 `unknown + ineligible`；
4. backfill 按 semantics version 和 batch id 记录，事务化且幂等；
5. `positions`、`paper_account_events`、audit、knowledge 原件永不删除；污染派生内容只
   archive/supersede；
6. 历史模拟账户修复、补偿和资金费结算写入口均已退役；读取历史不会追加事件。

### Archived accounting records

`historical_accounting_reconciliations` is an additive, one-row-per-Position
reconstruction ledger for old native `paper_internal` records that predate the
durable `orders.position_id` backlink.  It records either a deterministic
`reconstructed` amount or `unavailable` with the exact missing evidence reason,
method/version, and JSON evidence. Historical reconciliation rows remain readable
with their original evidence.
The local simulated-account repair commands are retired; see
[the archived controls](../history/local_paper_account_controls.md).

### Current reconciliation records

disappearance confirmation 使用 `position_disappearance_candidates` 保存作用域身份和确认状态，使用
`position_disappearance_observations` 保存 observation hash 去重的不可变证据。两者属于 trust-bearing
state，不得进入 telemetry prune allowlist。`OI-B01` 已接入 reconciler：只有新鲜、完整、当前逻辑
账户的快照缺失才创建/累计；同一 view identity、时间倒退、过期、不完整、错 broker/账户的快照均不
累计。交易所重新出现会将 pending 行标为 `reappeared` 并保留审计。两次独立完整缺失或未来 adapter
提供的 direct `confirmed_absent` 只代表“可进入 verified-exit 调查”。RG-T07 现将调查结果分为两条
原子写入路径：`verified_fill` 且 identity/数量/fee 完整时写 strategy/eligible 与真实经济字段；
`confirmed_absent_without_fill` 只写 administrative/ineligible，realized PnL、exit fee、slippage 保持
`NULL`，不生成经济结算。`inconclusive` 不改变 position/candidate。candidate 只在对应 position 收口
成功后标为 `settled_verified` 或 `settled_administrative`，重试不会重复结算。

`reconciliation_integrity_incidents` 保存系统性行政关闭后重新出现的事故。每种事故最多一个 active
行；重复探测只更新同一行并清零干净观察计数。恢复至少需要两次相隔五分钟的干净观察，再由明确
actor/reason 执行 resolution。进程重启、一次干净轮询或配置中 Learning 为 enabled 均不能绕过该行。
该表和两张 disappearance evidence 表一样属于不可裁剪的可信状态。

## 11. Archived simulation evidence

The schema retains these tables for historical reads. Their construction models,
producer services and runtime activation are retired. Migration does not rewrite
the records or reactivate their producers.

| Retained table | Historical evidence |
|---|---|
| `paper_account_events` | Original simulated cash events and balance projections |
| `position_exit_plan_versions` | Immutable resolved plans used by the retired barrier |
| `position_protection_state` | Recorded activation, cursor and gap state |
| `position_protection_triggers` | Original first-barrier facts and close backlinks |
| `exit_breakeven_shadow_assignments` | Frozen exit-experiment assignments |
| `exit_breakeven_shadow_outcomes` | Persisted paired outcomes and unavailable reasons |

Archived `paper_internal`, `local_fill`, `local_simulation`, and funding-version
identities keep their original meaning and evidence; they do not qualify external
exchange execution. Former table and cash-account definitions are in
[archived account controls](../history/local_paper_account_controls.md); barrier
and exit-experiment definitions are in
[archived protection](../history/local_simulated_protection.md).
