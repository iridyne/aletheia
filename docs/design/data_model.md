# Data Model Reference

> Complete index of Aletheia Trading OS data layer.
> ~94 SQLite table declarations, 54 Pydantic models, WAL mode, additive-only migrations.
> Source of truth: `aletheia/storage/migrations.py` (DDL) + `models.py` (types).
> Last updated: 2026-07-10

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

### A. Trading Core (10 tables)

| Table | Purpose | Key columns | Written by |
|-------|---------|-------------|------------|
| `opportunities` | Scanner output -- trading signals | symbol, direction, confidence, key_observations (JSON) | market_observer |
| `trade_proposals` | Actionable trade candidates | symbol, direction, entry_price, stop_price, target_price, **commit_hash**, **parent_hash** (TaG, P0-1) | paper_trader / System Operator |
| `risk_decisions` | Risk engine approval/rejection | proposal_id, approved, reasons (JSON) | risk engine |
| `approvals` | Human approval records | proposal_id, decision, decided_by | frontend |
| `orders` | Execution orders | symbol, side, quantity, order_type, status | execution gateway |
| `fills` | Order fill confirmations | order_id, fill_price, fill_quantity, fee | broker adapter |
| `positions` | Open/closed positions | symbol, direction, entry_price, quantity, status, realized_pnl | position_manager |
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

### D. Market Data Collection (9 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `market_snapshots` | Point-in-time price/volume | symbol, price, volume_24h, change_pct_24h |
| `orderbook_snapshots` | Order book depth | symbol, bids (JSON), asks (JSON), spread |
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
| `universe_forward_outcomes` | Fixed-horizon Opportunity economics | venue, symbol, market_type, direction, horizon, gross_return_pct, directional_return_pct, estimated_fee_pct, estimated_spread_slippage_pct, estimated_funding_pct, net_return_pct, min_tradable_edge_pct, mfe_pct, mae_pct, settlement_price_source, cost_model_version |
| `universe_policies` | Universe selection rules | policy_name, filters (JSON), priority |

### G-bis. Opportunity Filter Evidence (3 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `filter_decisions` | Immutable per-candidate pass/reject facts for directional and low-volume gates | candidate_key, filter_name, decision, reason_code, parameter_snapshot, filter_version, outcome_candidate_key, evaluated_at |
| `filter_decision_batches` | Complete compressed facts for high-volume directionless ticker/tradability gates | run_id, filter_name, candidate_count, pass/reject/not-applicable/error counts, zlib-base64 lossless field/row-matrix candidate_facts, filter_version |
| `filter_candidate_outcomes` | Shared exact-instrument fixed-horizon net result for a candidate seen by multiple gates | outcome_candidate_key, venue, symbol, market_type, direction, horizon, gross/directional/cost/net/MFE/MAE, status |

Business writers only append these rows. Daily retention removes expired analytical rows
(30d for compressed batches; 90d for per-candidate decisions/outcomes). This retention
does not apply to authoritative proposals, RiskDecision, orders, fills, positions, or audit records.

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
| `pi_decisions` | Structured System Operator decisions | decision_type, action, symbol, conviction, reasoning (JSON), opportunity_id, **signal_source**, debate trace fields |

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

## 4. Pydantic Model Index (54 models)

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
