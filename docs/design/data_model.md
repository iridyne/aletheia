# Data Model Reference

> Complete index of Aletheia Trading OS data layer.
> ~95 SQLite table declarations, 58 Pydantic models, WAL mode, additive-only migrations.
> Source of truth: `aletheia/storage/migrations.py` (DDL) + `models.py` (types).
> Last updated: 2026-07-12

---

## 1. Architecture Overview

- **Engine**: SQLite 3, WAL mode, `busy_timeout=30000ms` (30s), `synchronous=NORMAL`, `wal_autocheckpoint=1000`, `isolation_level=None` (true autocommit, no implicit BEGIN) — `storage/sqlite.py:32-44`
- **Concurrency**: one connection per thread via `threading.local`; scheduler capped at `max_workers=3`
- **Transaction safety**: `was_in_tx = db.in_transaction` captured BEFORE every write; commit only `if not was_in_tx`
- **Migrations**: additive-only (new tables, new columns with defaults); never DROP/RENAME in production
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

### A. Trading Core (14 tables)

| Table | Purpose | Key columns | Written by |
|-------|---------|-------------|------------|
| `opportunities` | Scanner output -- trading signals | symbol, direction, confidence, key_observations (JSON) | market_observer |
| `trade_proposals` | Actionable trade candidates | symbol, direction, entry_price, stop_price, target_price, **`agent_rationale.entry_parameters`** (final entry/stop/target/notional/leverage reasons + value sources), **commit_hash**, **parent_hash** (TaG, P0-1) | paper_trader / System Operator |
| `risk_decisions` | Risk engine approval/rejection | proposal_id, approved, reasons (JSON) | risk engine |
| `approvals` | Human approval records | proposal_id, decision, decided_by | frontend |
| `orders` | Execution orders | symbol, side, quantity, order_type, status | execution gateway |
| `fills` | Order fill confirmations | order_id, fill_price, fill_quantity, fee | broker adapter |
| `positions` | Open/closed positions | symbol, direction, entry_price, quantity, status, realized_pnl, **exit_reasoning** (actual close actor/source/trigger; NULL for historical absence) | position_manager / outcome closure |
| `position_disappearance_candidates` | Durable account-scoped position-disappearance confirmation state; never authorizes close by itself | position_id, identity_key, broker_id, account_environment, non-secret account_fingerprint, instrument identity, confirmation_count, direct_query_state, semantics_version | position reconciler (OI-B01 target) |
| `position_disappearance_observations` | Immutable idempotent evidence for a disappearance candidate | candidate_id, observation_hash, view_identity, completeness/absence flags, direct_query_state, evidence_json | position reconciler (OI-B01 target) |
| `reconciliation_integrity_incidents` | Restart-safe close/reopen circuit breaker and explicit recovery evidence | scoped fingerprint, status, source mix, detected/clean timestamps, clean count, resolution actor/reason, semantics version | position reconciler + Learning control |
| `shadow_trades` | What-if trades for comparison | opportunity_id, venue, market_type, outcome_candidate_key, gross_return_pct, net_pnl_pct, estimated costs | analytics |
| `portfolio_snapshots` | Periodic equity snapshots | total_value_usdt, unrealized_pnl, execution_mode | scheduler job |
| `paper_account_events` | **Local `paper_internal` cash ledger only**; immutable starting funds, human adjustments, and realized-PnL settlements | action, amount_usdt, cash_balance_before_usdt, cash_balance_after_usdt, actor, source, position_id, note | paper account service |
| `market_regimes` | Detected market regimes | regime_type, confidence, indicators (JSON) | market_observer |

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
| `market_ohlcv` | Historical candlestick data; rows are isolated by full instrument identity | exchange, symbol, market_type, timeframe, ts, open, high, low, close, volume |

### C-bis. Measured Learning Ledger (7 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `learning_hypotheses` | Canonical scoped Learning hypothesis | hypothesis_id, fingerprint, path_id, scope/action JSON, status, version |
| `learning_experiments` | One isolated shadow or paper stage | experiment_id, hypothesis_id, parent experiment, activation verdict, stage generation, assignment salt, allocation, status |
| `learning_exposures` | Immutable record of one actual/control influence; ranker score components and persona sizing store baseline/proposed/actual values here | dedup_key, assignment, entity ids, before/after/manifest, outcome_candidate_key, experiment stage, policy epoch |
| `learning_contribution_verdicts` | Append-only measured verdict window | stage, unique arm counts, delta/interval, harm metrics, measurement verdict |
| `learning_lifecycle_events` | Durable transition or blocked-promotion audit record | event_id, dedup key, experiment/successor/verdict ids, from/to stage, action, reason, policy epoch |
| `learning_job_health` | Durable expected-run/backlog health | schedule, watermarks, failures, status |
| `learning_policy_epochs` | Non-sensitive effective-policy attribution boundary | effective-values hash/JSON, affected paths, safety flag, interval |

These tables were additive/observational in Phase 1. Current universe, factor, ranker, and sizing
consumers now write refrozen prospective exposures; only an active, stage-isolated paper
experiment with enough linked cost-aware outcomes and separately validated lifecycle authority may
apply a treatment. The ledger grants no risk, execution, credential, or live authority.

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
| `perp_structure_snapshots` | Observation-only perp positioning map | symbol, venue, source, as_of_time, data_quality, poc, value_area_low/high, liquidation walls, concentration, `structure_bias`, `composite_z`, `structure_rank`, `universe_size`, `dimension_scores_json`, `risk_context_json`; consumed by market_observer advisory prompt block, System Operator context, and doctor freshness only |
| `macro_snapshots` | Macro economic indicators | indicator, value, source; **fng_value / fng_classification** (Round 2 Step 2 — Alt.me Fear & Greed) + **top_movers_summary** (Round 2 Step 3 — CoinGecko top movers, pre-formatted string folded into LLM macro block) |
| `coingecko_top_snapshots` (Round 2 Step 3) | Top-N coins by market cap pulled every Phase B tick | coin_id, symbol, price_usd, market_cap_usd, market_cap_rank, volume_24h_usd, price_change_1h_pct, price_change_24h_pct, price_change_7d_pct, observed_at |
| `onchain_snapshots` (datasrc overhaul) | DefiLlama on-chain regime (zero-key); folded into the LLM macro block | source, metric_type (`stablecoin_mcap` / `chain_tvl` / `dex_volume`), total_usd, change_24h_pct, top_entries (JSON), raw_summary (JSON), observed_at |

### E. Agent Runtime (10 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `agent_runs` | Agent execution records | agent_name, trigger_type, status, outputs (JSON) |
| `agent_run_steps` | Step-by-step agent traces | run_id, step_number, action, result |
| `agent_tool_calls` | Tool invocation log | run_id, tool_name, arguments (JSON), result |
| `audit_events` | System-wide audit trail | actor, source, event_type, entity_id, before/after (JSON) |
| `settings` | Runtime configuration store | key, value |
| `runtime_registry` | Service/process registry | service_name, pid, started_at, health |
| `runtime_checkpoints` | Long-running task progress | task_id, checkpoint_data (JSON), progress_pct |
| `runtime_events` | Event bus persistence | event_type, payload (JSON), emitted_at |
| `runtime_exclusive_lock` | Distributed lock (single-writer) | lock_name, holder, acquired_at, expires_at |
| `runtime_ownership` | Task ownership tracking | task_id, owner, claimed_at |
| `runtime_sessions` | One row per Gateway PROCESS lifecycle (session_id under a runtime_id). Core trading facts reference `session_id` via `runtime_session_id` | session_id, runtime_id, boot_commit, config_hash, behavior_fingerprint, schema_version, active_scope_id, execution_mode, started_at, ended_at, shutdown_clean |

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
| `agent_shell_commands` | All commands issued | session_id, command, response, latency_ms |
| `agent_shell_messages` | Chat-style messages | session_id, role, content |
| `agent_shell_tasks` | Background tasks | session_id, task_type, status, result |
| `agent_shell_artifacts` | Generated artifacts | task_id, artifact_type, content |
| `agent_shell_heartbeats` | Liveness pings | session_id, timestamp |
| `agent_shell_events` | Structured events | session_id, event_type, payload (JSON) |

### G. Universe Pipeline (7 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `universe_registry` | All known tradeable assets | symbol, exchange, asset_class, metadata (JSON) |
| `universe_feature_snapshots` | Per-symbol feature vectors | symbol, rsi, bb_width, vol_ratio, taker_buy_ratio |
| `universe_scanner_runs` | Scanner execution log | scanner_name, symbols_scanned, signals_emitted |
| `universe_snapshots` | Universe composition snapshots | snapshot_id, created_at |
| `universe_members` | Assets in a universe snapshot | snapshot_id, symbol, market_type, rank, score, scanner_direction |
| `universe_forward_outcomes` | Fixed-horizon Opportunity economics | outcome_candidate_key, venue, symbol, market_type, direction, horizon, gross_return_pct, directional_return_pct, estimated_fee_pct, estimated_spread_slippage_pct, estimated_funding_pct, net_return_pct, min_tradable_edge_pct, mfe_pct, mae_pct, settlement_price_source, cost_model_version |
| `universe_policies` | Universe selection rules | policy_name, filters (JSON), priority |

### G-bis. Opportunity Filter Evidence (6 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `filter_decisions` | Immutable per-candidate pass/reject facts for directional and low-volume gates | candidate_key, filter_name, decision, reason_code, parameter_snapshot, filter_version, outcome_candidate_key, evaluated_at |
| `filter_decision_batches` | Complete compressed facts for high-volume directionless ticker/tradability gates | run_id, filter_name, candidate_count, pass/reject/not-applicable/error counts, zlib-base64 lossless field/row-matrix candidate_facts, filter_version |
| `filter_candidate_outcomes` | Shared exact-instrument fixed-horizon net result for a candidate seen by multiple gates | outcome_candidate_key, venue, symbol, market_type, direction, horizon, gross/directional/cost/net/MFE/MAE, status |
| `controller_recommendations` | Append-only observational filter findings; legacy action fields remain conservative | filter/stage/version/cohort dimensions, pass/reject counts, shrunk net means, evidence_verdict, status |
| `filter_shadow_experiments` | Immutable prospective plans for replay-ready monotonic threshold candidates | experiment_key, fixed scope/version, control/treatment parameters, started_at, evidence gates, status, production_mutation_allowed=0 |
| `filter_shadow_evaluations` | Append-only evidence snapshots for a prospective plan | experiment_id, disposition, control/treatment samples and coverage, net means, edge delta |

Business writers only append these rows. Daily retention removes expired analytical rows
(30d for compressed batches; 90d for per-candidate decisions/outcomes). This retention
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
| `serenity_post_mortems` | Post-trade analysis |
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

### K. System Operator Decisions (1 table)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `pi_decisions` | Structured System Operator decisions | action, symbol, direction, **market_type**, conviction, reasoning, opportunity_id, **signal_source**, proposal_id, **proposal_status**, **proposal_reason**, learning_manifest_json, debate trace fields |

> `signal_source ∈ {'market_observer','serenity'}` (default `'market_observer'`) — discriminator for which table `opportunity_id` resolves against. Both `opportunities.id` (market_observer) and `serenity_opportunities.id` are bare `uuid4().hex[:12]` with no type tag, so the id alone is cross-table ambiguous. Stamped by `_attach_opportunity_context` (market_observer takes precedence). Phase 2 of Serenity Option A — see `docs/plans/serenity_execution_path.md` §3.2.

> **`trade_proposals.trade_school` / `positions.trade_school` ∈ {'short_term','serenity'}** (default `'short_term'`, NOT NULL) — EXECUTION-level discriminator. Distinct from `pi_decisions.signal_source` which lives at DECISION level. Derived in `trading_decision.submit_decision_as_proposal` from the upstream `signal_source`, then propagated through `paper_tools` → `gateway.submit_proposal` → all four brokers (paper/bitget/binance/okx) onto the `Position` row. `position_manager` reads `trade_school` to (a) skip trailing/breakeven entirely for serenity holdings, (b) gate `max_hold` between `cfg.trading.max_position_hold_hours` (short_term) and `cfg.serenity.max_hold_days * 24` (serenity). Phase 4 of Serenity Option A — see `docs/plans/serenity_execution_path.md` §5.

> **Debate trace fields** (`debate_initial_conviction`, `debate_bull_confidence`, `debate_bear_confidence`, `debate_adjustment`) are audit-only fields on `pi_decisions`. They preserve how a Bull/Bear debate changed the System Operator's conviction after the initial LLM decision. Legacy rows and non-debated rows keep these fields `NULL`; audit readers must treat NULL as missing data, not synthesize a value.

> **`trade_proposals.commit_hash` / `trade_proposals.parent_hash`** (both `TEXT`, both nullable on legacy rows) — TaG proposal hash chain (P0-1, 2026-06-29, borrow from OpenAlice `TradingGit`). `commit_hash` is the 16-hex-char SHA-256 prefix of the canonical-JSON dump of the proposal's **immutable** fields — i.e. `model_dump(exclude={"status", "commit_hash"})` — joined with `parent_hash`. `parent_hash` references the previous row's `commit_hash` (ordered by `rowid DESC, commit_hash NOT NULL`), forming a **global single chain** across all brokers / strategies / execution modes. Auto-filled by `repositories.insert_trade_proposal()` only when `p.commit_hash is None`; explicit hash on the model object is preserved (used by re-insert / replay paths). Indexed by `idx_proposals_commit_hash` for O(log n) single-node lookup; full-chain verification walks the table by `rowid`. Hash input deliberately excludes `status` because the lifecycle `pending → executed / rejected` is a normal state transition that **must not** invalidate the chain. The downstream `position_adopted` audit event copies both fields into `after_state` so the JSONL audit row is independently verifiable without a `SELECT trade_proposals WHERE id=?` round-trip — see `audit_chain_contract.md §6.4`. SDD anchors: `tests/specs/spec_data_model.py` DM-S08 / DM-S09 / DM-B06 / DM-C02; design reference `docs/research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md §五 P0-1`.

### L. Extensions (2 tables)

| Table | Purpose |
|-------|---------|
| `extension_registry` | Registered extension plugins |
| `extension_runs` | Extension execution history |

---

## 4. Pydantic Model Index (58 models)

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

1. Fresh DB: full DDL from `SCHEMA_SQL` + domain-specific DDL blocks
2. Existing DB: `_ADDITIVE_COLUMNS` list applies `ALTER TABLE ADD COLUMN` idempotently
3. Never: DROP TABLE, DROP COLUMN, RENAME (breaks existing data)
4. Pattern: check `pragma_table_info` before ALTER to avoid "duplicate column" errors
5. Test: `run_migrations()` is called in every test setup; full schema always available

---

## 7. Retention & Cleanup (data lifecycle)

> Cleanup job runs daily (`scheduler.py:_run_cleanup`); writes an audit event `data_pruned`.
> Two-class rule: **trust-bearing tables are NEVER pruned; only telemetry/market-data tables are.**

### 7.1 Prunable tables (telemetry only)

| Table | Retention | Configurable | Source |
|-------|-----------|--------------|--------|
| `agent_runs` / `agent_run_steps` / `agent_tool_calls` | 30d | yes (`retention.*`) | `repositories.py:990-999` |
| `market_snapshots` | 30d | yes | `repositories.py:1002` |
| `portfolio_snapshots` | 90d | yes | `repositories.py:1006` |
| `orderbook_snapshots` / `liquidation_events` | 7d | hardcoded | `repositories.py:1539-1556` |
| `funding_rate_snapshots` / `open_interest_snapshots` / `perp_structure_snapshots` / `universe_feature_snapshots` | 30d | hardcoded | `repositories.py` prune functions |
| `ohlcv_backfill_state` | 90d since last update | hardcoded | `repositories.py:prune_ohlcv_backfill_state` |
| `macro_snapshots` | 90d | hardcoded | `repositories.py:1587` |
| `coingecko_top_snapshots` (Round 2 Step 3) | 14d | hardcoded | `repositories.py:prune_coingecko_top_snapshots` |
| `universe_forward_outcomes` | 30d | hardcoded | `repositories.py:prune_universe_forward_outcomes` |
| `filter_decision_batches` | 30d | hardcoded | `repositories.py:prune_filter_decision_batches` |
| `filter_decisions` / `filter_candidate_outcomes` | 90d | hardcoded | `repositories.py` filter-evidence prune functions |
| `agent_shell_heartbeats` / `_events` / `_messages` / `_artifacts` (operator telemetry, DB-SIZE-1) | 2d / 14d / 30d / 30d | hardcoded | `repositories.py` `_PRUNABLE` + `scheduler.py:_run_cleanup` |
| `opportunities` (open) | 48h → `expired` | yes | `repositories.py:1597-1612` |

### 7.2 NEVER-pruned (trust-bearing) — enforced by `_PRUNABLE` allowlist (`repositories.py:_PRUNABLE`)

`trade_proposals` · `risk_decisions` · `orders` · `fills` · `positions` · `audit_events` · `knowledge_items`

> **Invariant**: the causal record chain (§2) is permanent. Any new prune target MUST be added to `_PRUNABLE` explicitly and MUST NOT include a trust-bearing table.

### 7.3 JSONL rotation

- Trigger: file ≥ `retention.jsonl_max_mb` (default 50MB) → rename `x.jsonl` → `x.jsonl.1` → `.2`... (`files/jsonl.py:27-104`)
- Rotated files are **kept** (no auto-delete); new writes append to fresh file.
- `audit.jsonl`: SHA-256 `_prev_hash` chain across rotations, tamper-evident (`jsonl.py:182-207`).

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

> Three pillars (see [CLAUDE.md SQLite rule](../../CLAUDE.md)). Recovery specifics:

- **No explicit retry loop** for `database is locked` — relies on `busy_timeout=30000` (SQLite auto-retries 30s).
- **Transaction-leak cleanup**: scheduler calls `rollback_if_in_transaction()` before/after each job (`sqlite.py:56-69`, `scheduler.py:125-134`) — prevents a daemon thread from permanently holding the WAL writer lock (the historical `database is locked` root cause; see [db-lock memory](../../记忆 aletheia-db-lock-root-cause)).
- **`isolation_level=None`** is the structural fix: no implicit BEGIN means a failed DML does not leave the thread `in_transaction=True`.

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
| `reconciliation_evidence` | observation/direct-query evidence 与确认版本 JSON | `NULL` |
| `outcome_semantics_version` | 生成 eligibility 的稳定语义版本 | `NULL` |

迁移规则：

1. 只用 `_ADDITIVE_COLUMNS` / additive table，不 DROP、RENAME 或覆盖原列；
2. 历史 synthetic/maintenance 行可回填 `ineligible`，但不能因旧行有 `exit_price` 就猜为 verified；
3. 无法证明的历史行保持 `unknown + ineligible`；
4. backfill 按 semantics version 和 batch id 记录，事务化且幂等；
5. `positions`、`paper_account_events`、audit、knowledge 原件永不删除；污染派生内容只
   archive/supersede；
6. paper cash remediation 默认只生成 compensation candidate，owner 明确批准后才能另行追加补偿事件。

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
