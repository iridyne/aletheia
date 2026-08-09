# Data Model Reference

> Complete index of Aletheia Trading OS data layer.
> ~100 SQLite table declarations, 63 Pydantic models, WAL mode, versioned additive migrations.
> Source of truth: `aletheia/storage/migrations.py` (DDL) + `models.py` (types).
> Last updated: 2026-08-08

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

```
market_snapshots --> opportunities --> trade_proposals --> risk_decisions
                                                              |
                                              approved -------+------- rejected
                                                |
                                            orders --> fills --> positions
                                                                    |
                                                              close --> portfolio_snapshots
                                                                    |
                                                              knowledge_items (reviews)
```

---

## 3. Table Groups

### A. Trading Core (20 tables)
| Table | Purpose | Key columns | Written by |
|-------|---------|-------------|------------|
| `opportunities` | Scanner output -- trading signals; lifecycle is `open→queued→reviewed` or `open/queued→expired`; expired never regresses to reviewed | symbol, direction, confidence, key_observations (JSON), last_ranked_at, universe snapshot/member identity | market_observer |
| `trade_proposals` | Actionable trade candidates | symbol, direction, entry_price, stop_price, target_price, **`agent_rationale.entry_parameters`** (final entry/stop/target/notional/leverage reasons + value sources), **commit_hash**, **parent_hash** (TaG, P0-1) | paper_trader / System Operator |
| `proposal_execution_claims` | One durable, expiring execution owner per Proposal; kept outside the hash-covered Proposal intent and removed only by owner-token compare-and-set | proposal_id, owner_token, claimed_at, lease_expires_at | execution gateway |
| `entry_risk_reservations` | Durable pre-Order allowance acquired atomically with authoritative Risk; retained after a worker crash and superseded by the first durable Order | proposal_id, proposal_commit_hash, symbol, market_type, execution_mode, quantity, price, leverage | risk engine / execution gateway |
| `risk_decisions` | Risk engine approval/rejection | proposal_id, approved, reasons (JSON), decision_role, evaluated_quantity, proposal_commit_hash, risk_mode, policy_id, policy_revision, policy_hash | risk engine |
| `approvals` | Human approval records | proposal_id, decision, decided_by | frontend |
| `orders` | Execution orders; native exchange entry `pending` is an unresolved Risk reservation, not zero exposure | symbol, side, quantity, order_type, status | execution gateway |
| `fills` | Order fill confirmations | order_id, fill_price, fill_quantity, fee | broker adapter |
| `positions` | Open/closed positions; a closed future may keep net economics unavailable. Delayed local funding materialization stores its versioned evidence fingerprint in reconciliation evidence. Market-aware evaluation remains advisory: its structured evidence records the exact venue/symbol/physical-market identity and source rows used for an Operator wake. | symbol, direction, entry_price, quantity, status, nullable realized_pnl, funding_cost, funding_source, **exit_reasoning** (actual close actor/source/trigger; NULL for historical absence), **exit_limit_source** ('human'|'system_operator'; author of a resting limit exit — selects human_limit_exit vs operator_limit_exit in close_reason for attribution honesty), **exit_plan_effective_at** (nullable projection to the current immutable ExitPlan; NULL means the dormant barrier has not been activated), **last_market_signal_evidence** (versioned JSON; exact position/signal identities, Opportunity/member/asset/Feature provenance, evaluation and delivery result) | position_manager / outcome closure / paper funding settler |
| `position_exit_plan_versions` | Retained, append-only resolved ExitPlan history. `(position_id, version)` is contiguous and immutable; insert atomically advances the nullable Position projection. | position_id, version, effective_at, resolved stop/take-profit/exit-limit, plan_json, plan_hash, provenance, created_at | protection repository + new/forward-adopted Paper runtime |
| `position_protection_state` | One retained row per newly protected local-paper Position. It freezes exact instrument identity and monotonic cursors, preserves gap history, and projects first-trigger plus exact close linkage. | position_id, venue, canonical symbol, physical market_type, timeframe=1m, activated_at, cursor/gap/status, trigger_id, close Order/Fill linkage | protection repository + protective scheduler |
| `position_protection_triggers` | Append-only first-barrier evidence. One immutable row per protected Position contains the exact 1-minute bar, resolved ExitPlan and deterministic collision policy. | trigger_id, position_id, exact instrument/bar/provider/hash, ExitPlan identity/barriers, hit flags/reason | protective scheduler + native paper close |
| `exit_breakeven_shadow_assignments` | Prospective immutable assignment for the read-only profit-capture experiment. The fixed +1.5% trigger and entry-price shadow stop are frozen with exact instrument, ExitPlan, cost and build provenance before any eligible bar. | position_id, treatment/rule hash, assigned_at, exact instrument, entry economics, trigger/stop, ExitPlan and runtime provenance, behavior_authority=0 | new protected local-paper activation |
| `exit_breakeven_shadow_outcomes` | One immutable paired result after an eligible actual close. Missing exact 1m coverage, identity, ordering or funding remains unavailable; available rows contain actual, shadow and incremental net PnL under the frozen cost model. | position_id, disposition/reason, trigger/exit bars, shadow fill/costs, actual/shadow/incremental net PnL, evidence hash | durable outcome-hook replay |
| `position_close_events` | **Per-close economic ledger** (exit-intent R1) — one row per close EVENT (full close = 1 row; partial reduce = 1 row per reduction) so realized PnL sums from close events, unifying full + partial closes. Delayed funding finalization atomically converges the unique full-close projection with Position and paper cash. The daily-loss breaker adds partial-close realized via this ledger (`is_partial=1`) on top of the verified full-close read | position_id, close_reason, exit_price, closed_qty, realized_pnl, entry_fee, exit_fee, is_partial, closed_at | `pnl.close_position` / `pnl.reduce_position` / paper account service |
| `outcome_hook_jobs` | Durable eligible-close feedback outbox; one row per Position and hook, with leased replay after failure | position_id, hook_type, status, attempts, next_attempt_at, claim_token, last_error | outcome close transaction / outcome_hook_replay |
| `position_disappearance_candidates` | Durable account-scoped position-disappearance confirmation state; never authorizes close by itself | position_id, identity_key, broker_id, account_environment, non-secret account_fingerprint, instrument identity, confirmation_count, direct_query_state, semantics_version | position reconciler (OI-B01 target) |
| `position_disappearance_observations` | Immutable idempotent evidence for a disappearance candidate | candidate_id, observation_hash, view_identity, completeness/absence flags, direct_query_state, evidence_json | position reconciler (OI-B01 target) |
| `reconciliation_integrity_incidents` | Restart-safe close/reopen circuit breaker and explicit recovery evidence | scoped fingerprint, status, source mix, detected/clean timestamps, clean count, resolution actor/reason, semantics version | position reconciler + Learning control |
| `shadow_trades` | What-if trades for comparison | opportunity_id, venue, market_type, outcome_candidate_key, gross_return_pct, net_pnl_pct, estimated costs | counterfactual |
| `portfolio_snapshots` | Periodic portfolio/account snapshots; `account_equity_usdt` is the only field eligible for the Overview account-equity curve. Legacy local rows with only `total_value_usdt` are position notional and excluded from that curve. Realized PnL may be NULL when included close economics are incomplete. | total_value_usdt, account_equity_usdt, unrealized_pnl, nullable realized_pnl, execution_mode | scheduler job |
| `paper_account_events` | **Local `paper_internal` cash ledger only**; immutable starting funds, human adjustments, economically complete realized-PnL settlements, and owner-approved correction deltas. Unknown futures funding creates no close-settlement event. Corrections reuse `manual_credit`/`manual_debit` with position/source/fingerprint evidence rather than rewriting history. | action, amount_usdt, cash_balance_before_usdt, cash_balance_after_usdt, actor, source, position_id, note | paper account service |
| `market_regimes` | Detected market regimes | regime_type, confidence, indicators (JSON) | market_observer |

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
| `knowledge_items` | Research notes, reviews, lessons | item_type, content (TEXT), tags, confidence |
| `news_items` | External news signals | source, headline, sentiment, symbols (JSON) |
| `market_ohlcv` | Historical candlestick data; rows are isolated by full instrument identity. Explicit offline compact may cluster the composite PK as `WITHOUT ROWID`; the logical schema is unchanged. | exchange, symbol, market_type, timeframe, ts, open, high, low, close, volume |

### C-bis. Measured Learning Ledger (8 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `learning_hypotheses` | Canonical scoped Learning hypothesis | hypothesis_id, fingerprint, path_id, scope/action JSON, status, version |
| `learning_experiments` | One isolated shadow or paper stage; a `registered` experiment's first exposure atomically/idempotently activates it and anchors `started_at` to earliest durable `exposed_at` | experiment_id, hypothesis_id, parent experiment, activation verdict, stage generation, assignment salt, allocation, status, started_at |
| `learning_exposures` | Immutable record of one actual/control influence; `assignment_unit_key` persists the randomized cluster independently from member candidate identity | dedup_key, assignment, assignment_unit_key, entity ids, before/after/manifest, outcome_candidate_key, experiment stage, policy epoch, behavior_contract_hash, provenance code_commit |
| `learning_contribution_verdicts` | Append-only measured verdict window; recurring monitoring is descriptive and only a consumed confirmatory row may be authority-eligible | stage, unique cluster-arm counts, delta/interval, harm metrics, metric_version, behavior_contract_hash, inference role/plan/cutoff identity, measurement verdict, created_at |
| `learning_inference_decisions` | Retained prospective fixed-look plan and once-only decision binding for each Paper experiment | inference family/contract, immutable plan/hash, frozen cutoff/fingerprint, authority alpha, consumed verdict id/time, status |
| `learning_lifecycle_events` | Durable transition/blocked-promotion record and JSONL audit outbox; historical rows default mirrored, new rows remain pending until stable-id append acknowledgement | event_id, dedup key, experiment/successor/verdict ids, from/to stage, action, reason, policy epoch, audit actor/source/status/mirrored_at/failure reason |
| `learning_job_health` | Durable expected-run/backlog health; current error may clear on recovery while last failure identity persists | schedule, watermarks, consecutive_failures, status, last_error, last_failed_at, last_failure_reason |
| `learning_policy_epochs` | Non-sensitive effective-policy attribution boundary | effective-values hash/JSON, affected paths, safety flag, interval |

These tables were additive/observational in Phase 1. Only
`universe_family_calibration` currently writes measurement-supported exposures with a
path-specific causal outcome. Operator, factor, ranker,
learned-guard, automatic Risk-lesson, Observer-bundle and persona-sizing hooks
are consumer-wired but measurement-unsupported: they return static fallback and
write no formal exposure. Only an active, stage-isolated paper experiment with
enough linked cost-aware outcomes and separately validated owner/lifecycle
authority may apply a supported treatment. The ledger grants no risk,
execution, credential, or live authority.

For current universe contribution measurement, `filter_candidate_outcomes` is the canonical
full-arm source. `universe_forward_outcomes` is a selected-member legacy mirror/fallback; matching
copies are deduplicated and economic disagreement fails closed. Exact-key reads are batched. One
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

### E. Agent Runtime and schema control (16 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `agent_runs` | Agent execution records | agent_name, trigger_type, status, outputs (JSON) |
| `agent_run_steps` | Step-by-step agent traces | run_id, step_number, action, result |
| `agent_tool_calls` | Tool invocation log | run_id, inference_call_id, tool_name, arguments (JSON), result |
| `llm_inference_calls` | Content-free logical inference envelope | logical_call_id, caller, prompt/context/tool/schema identities and hashes, existing causal identities in environment metadata, validation outcome, retry/fallback summary |
| `runtime_cost_events` | Physical LLM/tool/data cost attempts | call_id, attempt_index, provider/model/tool, opportunity/decision/proposal IDs when explicitly known, usage, price snapshot, cost status |
| `llm_context_artifacts` | Content-free selected-context manifest | logical_call_id, caller, evaluation pair/arm/fixture, kind, stable_id, freshness, rendered_chars |
| `llm_context_pair_reviews` | Aggregate-only blinded Learning B review | pair/fixture IDs, control/treatment call IDs, quality/grounded-use/harm scores, verdict |
| `audit_events` | System-wide audit trail | actor, source, event_type, entity_id, before/after (JSON) |
| `settings` | Runtime configuration store | key, value |
| `runtime_registry` | Service/process registry | service_name, pid, started_at, health |
| `runtime_checkpoints` | Long-running task progress | task_id, checkpoint_data (JSON), progress_pct |
| `runtime_events` | Event bus persistence | event_type, payload (JSON), emitted_at |
| `runtime_exclusive_lock` | Distributed lock (single-writer) | lock_name, holder, acquired_at, expires_at |
| `runtime_ownership` | Task ownership tracking | task_id, owner, claimed_at |
| `runtime_sessions` | One row per Gateway PROCESS lifecycle (session_id under a runtime_id). Core trading facts reference `session_id` via `runtime_session_id` | session_id, runtime_id, boot_commit, config_hash, behavior_fingerprint, schema_version, active_scope_id, execution_mode, started_at, ended_at, shutdown_clean |
| `aletheia_schema_migrations` | Immutable per-database proof that one named migration implementation completed successfully | migration_id, checksum, schema_version, applied_at, duration_ms |

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
| `agent_shell_commands` | All commands issued | session_id, inference_call_id, command, response, latency_ms |
| `agent_shell_messages` | Chat-style messages | session_id, role, content |
| `agent_shell_tasks` | Background tasks | session_id, task_type, status, result |
| `agent_shell_artifacts` | Generated artifacts | task_id, artifact_type, content |
| `agent_shell_heartbeats` | Liveness pings | session_id, timestamp |
| `agent_shell_events` | Structured events | session_id, event_type, payload (JSON) |

### G. Universe Pipeline (7 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `universe_registry` | All known tradeable assets | symbol, exchange, asset_class, metadata (JSON) |
| `universe_feature_snapshots` | Per-instrument feature vectors; identity is `asset_id → universe_registry` | asset_id, ts, 15m RSI/ATR/return/range-in-ATR, 1h RSI/ATR/returns, 4h RSI/ATR/BB width/volume ratio, bb_squeeze, derivatives fields |
| `universe_scanner_runs` | Scanner execution log | scanner_name, symbols_scanned, signals_emitted |
| `universe_snapshots` | Universe composition snapshots | snapshot_id, created_at |
| `universe_members` | Assets in a universe snapshot | snapshot_id, symbol, market_type, rank, score, scanner_direction |
| `universe_forward_outcomes` | Fixed-horizon selected-Universe-member economics; legacy mirror/fallback for contribution measurement, not the canonical full-arm source | outcome_candidate_key, venue, symbol, market_type, direction, horizon, gross_return_pct, directional_return_pct, estimated_fee_pct, estimated_spread_slippage_pct, estimated_funding_pct, net_return_pct, min_tradable_edge_pct, mfe_pct, mae_pct, settlement_price_source, cost_model_version |
| `universe_policies` | Universe selection rules | policy_name, filters (JSON), priority |
| `universe_twin_shadow_batches` | Non-authoritative continuous-score replay identity, sampled at most once per 55min | scanner_run_id, behavior/policy/source/metric identities, allocator random state, blockers, batch_hash |
| `universe_twin_shadow_records` | Compact complete-batch step/point/pessimistic comparison | scanner_run_id, candidate_index, candidate/feature/asset/instrument ids, ranks, selections, compact output JSON, allowlisted replay input JSON, record_hash |

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

## 4. Pydantic Model Index (62 models)

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
| PositionExitPlanVersion | position_exit_plan_versions | Trading protection storage |
| PositionProtectionState | position_protection_state | Trading protection storage |
| PositionProtectionTrigger | position_protection_triggers | Trading protection storage |
| ExitBreakevenShadowAssignment | exit_breakeven_shadow_assignments | Observe-only exit experiment |
| ExitBreakevenShadowOutcome | exit_breakeven_shadow_outcomes | Observe-only exit experiment |
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
| UniverseMember | universe_members | Universe |
| UniverseForwardOutcome | universe_forward_outcomes | Universe |
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
12. Schema v9 adds `positions.exit_plan_effective_at`, immutable `position_exit_plan_versions`, retained `position_protection_state`, and append-only `position_protection_triggers` through migration `20260808_0008_position_protection_state_v9`. It performs no Position backfill or activation and does not install an evaluator or close consumer.
13. Schema v11 adds retained `exit_breakeven_shadow_assignments` and `exit_breakeven_shadow_outcomes` through migration `20260808_0010_exit_breakeven_shadow_v11`. It performs no historical backfill and grants no trading or Learning authority.
14. Schema v12 rebuilds only `learning_inference_decisions` to admit immutable
    `ufc-fixed-look/v2` plans while retaining every v1 row, foreign key, index,
    write-once trigger, and no-delete trigger. Migration
    `20260808_0011_learning_randomization_inference_v12` changes no exposure,
    verdict, trading, Risk, Execution, or Live fact.
10. Physical packing is outside the migration ledger. In particular, `market_ohlcv` rowid-to-`WITHOUT ROWID` conversion runs only inside explicit offline `data compact --apply`, after a verified backup and with the Gateway stopped; fresh deploy and normal Gateway startup never perform it.
11. Schema v6 adds the content-free `inference_call_id` linkage columns through migration `20260806_0005_llm_inference_linkage_v6`. The LLM inference harness does not edit the frozen legacy baseline; databases that already received the columns from pre-ledger code are accepted idempotently.

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
| `agent_runs` / `agent_run_steps` / `agent_tool_calls` | 14d (`agent_run_steps`, was 30d) | `prune_agent_runs` (cascade), `prune_run_steps` |
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
| `universe_forward_outcomes` | 30d + earliest-exposure guard for active open experiments | `prune_universe_forward_outcomes` |
| `universe_snapshots` / `universe_members` | 30d + protected fallback-outcome guard | `prune_universe_snapshots` (cascade) |
| `universe_scanner_runs` | 30d | `prune_universe_scanner_runs` |
| `universe_twin_shadow_batches` / `universe_twin_shadow_records` | fixed 30d via `universe_scanner_runs_days`; ≤1 batch/55min, records before batches, no Twin-owned outcomes or Learning clean-window extension | `prune_universe_twin_shadow_evidence` |
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
| `runtime_sessions` | 30d | `prune_runtime_sessions` (new) |
| `serenity_evidence` / `serenity_run_events` | 60d | `prune_serenity_evidence` (new) / `prune_serenity_run_events` (new) |
| `backtest_runs` / `backtest_trades` | 60d (parent cascade) | `prune_backtest_runs` (new, cascade) |
| `opportunities` (open) | 48h → `expired` | existing |
| `agent_task_claims` (in `~/.aletheia/tasks.db`) | honour `expires_at` | new helper `_prune_agent_task_claims_tasks_db` in scheduler |

### 7.2 NEVER-pruned (trust-bearing) — enforced by `_PRUNABLE` allowlist (`repositories.py:_PRUNABLE`)

Audit chain (12): `trade_proposals` · `risk_decisions` · `orders` · `fills` · `positions` ·
`audit_events` · `capital_events` · `approvals` · `knowledge_items` · `ps_orders` · `ps_fills` ·
Protective-barrier evidence (3): `position_exit_plan_versions` ·
`position_protection_state` · `position_protection_triggers`. All are retained
and absent from `_PRUNABLE`.

`ps_positions`.

Measured Learning Ledger (8): `learning_experiments` · `learning_exposures` ·
`learning_hypotheses` · `learning_policy_epochs` · `learning_contribution_verdicts` ·
`learning_inference_decisions` · `learning_lifecycle_events` · `learning_job_health`.

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
| `exit_price_source` | `broker_fill` / `local_fill` / `mark_fallback` / `unavailable` | `unavailable` |
| `exit_fill_verified` | 是否有不可变 execution/broker fill 原件 | `false` |
| `outcome_eligible` | 是否允许 judgement/behavior consumer 使用 | `false` |
| `outcome_exclusion_reason` | 行政关闭、fill 未验证、旧干净窗、归因缺失等稳定原因 | `unclassified` |
| `reconciliation_identity` | 无凭证的 broker/environment/account/instrument identity JSON | `NULL` |
| `reconciliation_evidence` | observation/direct-query evidence 与确认版本 JSON；本地 funding 完成后含 `funding`、`funding_economics_fingerprint`、`funding_accounting_version=local-paper-funding-v2` | `NULL` |
| `outcome_semantics_version` | 生成 eligibility 的稳定语义版本 | `NULL` |
| `funding_cost` | `broker_ledger` 的账户实际 funding，或 `local_simulation` 按完整结算快照窗与结算前已成交 entry Fill 名义额分段计算的确定性模拟值；正数为成本、负数为返还 | `NULL` |
| `funding_source` | `not_applicable` / `broker_ledger` / `local_simulation` / `unavailable`；本地模拟的逐事件原件（active Fill ids/notional、事件成本和持有窗）在 `reconciliation_evidence.funding`，完整 handoff 由中央关闭入口复核 | `unavailable` |

`funding_economics_fingerprint` 必须对已持久化的完整 `reconciliation_evidence.funding` 原件本身做
canonical fingerprint，包括 close 时记录的 `seed_close_anchor`；历史 identity repair 不得重新计算或
替换该 nested evidence，也不得借机改变任何经济、ledger、eligibility 或 hook 投影。

`realized_pnl` 是原始关闭经济字段，不是缺失事实的默认值。open Position 可从 `0` 开始；closed futures
在没有可信 broker ledger 或完整 local-simulation 时间窗时必须保留 `NULL`。这种关闭仍有真实 close Order/Fill 和 closed 生命周期，
但固定为 `incomplete_funding_economics`、不得进入 eligible Outcome/outbox，也不得追加
`paper_account_events.position_closed`。spot 明确写 funding `0/not_applicable`。

`local-paper-funding-v2` 使用 `(exchange, canonical_symbol, UTC whole-second settlement)` 作为
会计事件身份，只归一严格小于一秒的表示偏差。完整历史结算可作为 `historical_ledger` 恢复
`paper_internal` 的本地模拟，但不能称为交易所账户 `broker_ledger`。完成时
`materialize_position_economics()` 原子写入 Position、唯一 full close event、首次
`position_closed` cash event、eligibility/outbox 和 SQLite audit。已有 numeric 结果只有 owner 明确
批准才能修正；差额以 append-only `manual_credit`/`manual_debit` 记录，并由 source、position ID、
fingerprint note 区别于普通人工调账。

Portfolio 和 paper-account 是从这些原件计算的读模型。只要所含关闭中有 `realized_pnl=NULL`：

- realized 总数及对应 PortfolioSnapshot 字段保持 `NULL`，并由 API 返回 complete=false 与缺失数量；
- paper 现金账本余额仍作为历史事实可见，但 `equity_usdt=NULL`、`available_balance_usdt=0`；
- `economics_complete=false`，直到缺失成本和对应现金结算都补齐，不能把未知行按 0 求和。

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
6. paper cash remediation 默认只生成 compensation candidate，owner 明确批准后才能另行追加补偿事件。

### Historical local-paper accounting recovery

`historical_accounting_reconciliations` is an additive, one-row-per-Position
reconstruction ledger for old native `paper_internal` records that predate the
durable `orders.position_id` backlink.  It records either a deterministic
`reconstructed` amount or `unavailable` with the exact missing evidence reason,
method/version, and JSON evidence.  The command
`aletheia data reconcile-local-paper-accounting` is dry-run by default; `--apply`
may only restore an exact missing reverse backlink and write this table, inside a
transaction.  It never changes raw `positions.realized_pnl`, eligibility, Risk,
paper cash events, or exchange data.  Its amounts are exposed as historical
reconciled accounting, never combined with verified strategy PnL, Learning,
PortfolioSnapshot, or cash settlement.

disappearance confirmation 使用 `position_disappearance_candidates` 保存作用域身份和确认状态，使用
`position_disappearance_observations` 保存 observation hash 去重的不可变证据。两者属于 trust-bearing
state，不得进入 telemetry prune allowlist。`OI-B01` 已接入 reconciler：只有新鲜、完整、当前逻辑
账户的快照缺失才创建/累计；同一 view identity、时间倒退、过期、不完整、错 broker/账户的快照均不
累计。交易所重新出现会将 pending 行标为 `reappeared` 并保留审计。两次独立完整缺失或未来 adapter
提供的 direct `confirmed_absent` 只代表“可进入 verified-exit 调查”。RG-T07 现将调查结果分为两条
原子写入路径：`verified_fill` 且 identity/数量/fee 完整时写 strategy/eligible 与真实经济字段；
`confirmed_absent_without_fill` 只写 administrative/ineligible，realized PnL、exit fee、slippage 保持
`NULL`，不追加 paper cash。`inconclusive` 不改变 position/candidate。candidate 只在对应 position 收口
成功后标为 `settled_verified` 或 `settled_administrative`，重试不会重复结算。

`reconciliation_integrity_incidents` 保存系统性行政关闭后重新出现的事故。每种事故最多一个 active
行；重复探测只更新同一行并清零干净观察计数。恢复至少需要两次相隔五分钟的干净观察，再由明确
actor/reason 执行 resolution。进程重启、一次干净轮询或配置中 Learning 为 enabled 均不能绕过该行。
该表和两张 disappearance evidence 表一样属于不可裁剪的可信状态。
When local-paper funding recovery becomes economically complete,
positions.realized_pnl and the matching paper_account_events.position_closed
row commit in one SQLite transaction. The periodic settler also scans orphaned
rows even when no NULL-PnL close remains.

Every paper cash delta and resulting cash balance must be finite. The HTTP

## 11. Protective Barrier Contract (schema v9 storage; Paper runtime active)

Schema v9 provides the durable substrate. The runtime consumer activates it
inside the transaction that opens a new `paper_internal` Position with at least
one resolved protective level. Its periodic cycle also forward-adopts a still-open
local-paper Position that lacks protection: the current resolved plan becomes
effective at the adoption instant and the cursor starts at the first complete
1-minute bar whose open is not earlier than that instant. No pre-adoption candle
is replayed, and a mid-hold adoption is not assigned to the prospective exit
shadow. A level-less Paper entry does not fabricate an ExitPlan or cursor.
Migration never creates these facts. The protective scheduler evaluates only
the frozen exact instrument's completed 1-minute bar;
missing data remains a visible gap and a bar bisected by an ExitPlan change is
skipped as `exit_plan_boundary_unordered` rather than assigned to either plan.
Before evaluation, the same protective worker refreshes only missing due bars
from the frozen venue with an explicit `since` cursor, bypassing the general
OHLCV TTL cache and forbidding a fallback venue. Available backlog bars are then
consumed in chronological order under a bounded per-position catch-up limit.

The storage invariants are:

1. `positions.exit_plan_effective_at` is nullable. It advances atomically only
   when a contiguous immutable `position_exit_plan_versions` row is inserted;
   it cannot be cleared, fabricated, or regressed.
2. ExitPlan identity is `(position_id, version, effective_at, plan_hash)`. The
   stored JSON hash and resolved stop/take-profit/exit-limit values must agree,
   and an applied version cannot be updated or deleted.
3. Each activated Position has at most one retained
   `position_protection_state` row. Activation freezes lowercase venue,
   canonical `BASE/QUOTE` symbol, physical market (`spot` or `perp`), and
   `timeframe='1m'`. Only logical `spot -> spot` and
   `perp|usdt_futures -> perp` mappings are accepted; unknown market types fail
   closed. Activation is explicit; forward adoption never backdates its plan or
   cursor and never claims historical barrier coverage.
4. `next_bar_open_ms`, `last_inspected_bar_open_ms`, and
   `last_evaluated_bar_open_ms` are monotonic compare-and-set cursors. A gap
   retains its reason, detection time, count, and bounded recent error without
   silently advancing the evaluation cursor.
5. The first trigger is one complete row in the append-only
   `position_protection_triggers` table: UUID/detection time,
   frozen identity, exact 1-minute bar and provider/hash, exact ExitPlan
   identity and resolved barriers, hit flags/reason, and
   `collision_policy='stop_first_v1'`. Inserting it atomically projects
   `trigger_id` and `triggered` onto state. Neither repositories nor direct SQL
   may replace any trigger field after the first successful compare-and-set. A
   trigger bar must be fully covered by one authoritative ExitPlan: a stale
   version, a plan effective after bar open, or a later plan effective before
   bar close is rejected instead of retroactively applying either plan.
6. `close_order_id`, then the exact
   `close_fill_id`/price/time/final state, are write-once and must resolve to the
   same Position and native close chain. Trigger evidence authorizes only a call
   to the existing `paper_internal` routed close: the close re-reads a fresh mark,
   applies adverse slippage and writes its normal Order/Fill. Barrier prices are
   never fabricated as fills. This consumer grants no Risk, external Execution,
   or Live authority.

Status is restricted to `active`, `gap`, `triggered`, `materialized`,
`superseded`, or `integrity_blocked`; terminal and triggered states cannot
regress to an evaluable state. A native non-barrier close supersedes an
untriggered cursor before any post-close candle is evaluated; a recorded trigger
continues through its exact close-linkage recovery instead. All three new tables
are permanent trust-bearing evidence and are excluded from retention pruning.
schema and central ledger writer reject NaN, both infinities, and finite-input
arithmetic overflow before any append or audit event.

## 12. Exit Breakeven Shadow Contract (schema v11, observe-only)

Schema v11 adds a single-variable prospective experiment for the concrete MVP
failure mode "a trade was profitable, then exited at a loss." For each newly
protected native `paper_internal` short-term Position, activation best-effort
freezes one rule before the first eligible bar: after a completed exact 1-minute
bar reaches 1.5% favorable return, an additional shadow stop at the entry fill
becomes active from the next completed bar. The treatment has
`behavior_authority=0`; it never changes the Position, ExitPlan, RiskDecision,
Order, Fill, scheduler authority, or Learning authority.

Settlement runs through the existing durable eligible-outcome hook. It compares
the real net result with the paired shadow result only when exact instrument
identity and contiguous completed 1-minute bars are available. A same-bar
trigger/stop cannot activate retrospectively. A shadow stop touch uses that
completed bar's close as the observable detection mark, then applies the frozen
adverse Paper slippage and fee model. Perpetual rows additionally require a
complete local funding window through the shadow close time. Any missing identity,
bar, ordering, arithmetic, actual Outcome, or funding fact produces an immutable
`unavailable` row with no invented delta.

Assignments and outcomes are retained append-only evidence. The descriptive
report can total paired incremental net PnL, but no sample count or result grants
promotion or parameter-mutation authority; a later owner-reviewed behavior trial
requires a separate measured Learning contract.
