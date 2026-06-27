# Data Model Reference

> Complete index of Aletheia Trading OS data layer.
> ~80 SQLite tables, 52 Pydantic models, WAL mode, additive-only migrations.
> Source of truth: `aletheia/storage/migrations.py` (DDL) + `models.py` (types).
> Last updated: 2026-06-21

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
| `trade_proposals` | Actionable trade candidates | symbol, direction, entry_price, stop_price, target_price | paper_trader / PI agent |
| `risk_decisions` | Risk engine approval/rejection | proposal_id, approved, reasons (JSON) | risk engine |
| `approvals` | Human approval records | proposal_id, decision, decided_by | frontend |
| `orders` | Execution orders | symbol, side, quantity, order_type, status | execution gateway |
| `fills` | Order fill confirmations | order_id, fill_price, fill_quantity, fee | broker adapter |
| `positions` | Open/closed positions | symbol, direction, entry_price, quantity, status, realized_pnl | position_manager |
| `shadow_trades` | What-if trades for comparison | opportunity_id, predicted_entry, actual_outcome | analytics |
| `portfolio_snapshots` | Periodic equity snapshots | total_value_usdt, unrealized_pnl, execution_mode | scheduler job |
| `market_regimes` | Detected market regimes | regime_type, confidence, indicators (JSON) | market_observer |

### B. Strategy Management (4 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `strategy_records` | Strategy definitions | name, status, parameters (JSON), direction_affinity |
| `strategy_experiments` | A/B test variants | strategy_id, variant, metrics (JSON) |
| `backtest_runs` | Backtest execution records | strategy_id, timeframe, sharpe, profit_factor |
| `backtest_trades` | Individual backtest trades | run_id, entry_time, exit_time, pnl |

### C. Knowledge & Learning (3 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `knowledge_items` | Research notes, reviews, lessons | item_type, content (TEXT), tags, confidence |
| `news_items` | External news signals | source, headline, sentiment, symbols (JSON) |
| `market_ohlcv` | Historical candlestick data | symbol, timeframe, open, high, low, close, volume |

### D. Market Data Collection (5 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `market_snapshots` | Point-in-time price/volume | symbol, price, volume_24h, change_pct_24h |
| `orderbook_snapshots` | Order book depth | symbol, bids (JSON), asks (JSON), spread |
| `funding_rate_snapshots` | Perpetual funding rates | symbol, funding_rate, next_funding_time |
| `open_interest_snapshots` | Open interest data | symbol, open_interest, oi_change_pct, **long_short_ratio** (Round 2 Step 1 — first actually populated, Binance globalLongShortAccountRatio) |
| `liquidation_events` | Large liquidation events | symbol, side, quantity, price |
| `macro_snapshots` | Macro economic indicators | indicator, value, source; **fng_value / fng_classification** (Round 2 Step 2 — Alt.me Fear & Greed) + **top_movers_summary** (Round 2 Step 3 — CoinGecko top movers, pre-formatted string folded into LLM macro block) |
| `coingecko_top_snapshots` (Round 2 Step 3) | Top-N coins by market cap pulled every Phase B tick | coin_id, symbol, price_usd, market_cap_usd, market_cap_rank, volume_24h_usd, price_change_1h_pct, price_change_24h_pct, price_change_7d_pct, observed_at |

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
| `universe_members` | Assets in a universe snapshot | snapshot_id, symbol, rank, score |
| `universe_forward_outcomes` | Post-scan price performance | opportunity_id, pnl_15m, pnl_1h, pnl_4h, pnl_24h |
| `universe_policies` | Universe selection rules | policy_name, filters (JSON), priority |

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

### K. PI Agent Decisions (1 table)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `pi_decisions` | Structured PI Agent decisions | decision_type, action, symbol, conviction, reasoning (JSON) |

### L. Extensions (2 tables)

| Table | Purpose |
|-------|---------|
| `extension_registry` | Registered extension plugins |
| `extension_runs` | Extension execution history |

---

## 4. Pydantic Model Index (52 models)

All models in `aletheia/storage/models.py`. Key mapping:

| Model | Table | Domain |
|-------|-------|--------|
| AuditEvent | audit_events | Runtime |
| AgentRun | agent_runs | Runtime |
| Opportunity | opportunities | Trading |
| ShadowTrade | shadow_trades | Trading |
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
| PiDecision | pi_decisions | PI Agent |

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
| `funding_rate_snapshots` / `open_interest_snapshots` / `universe_feature_snapshots` | 30d | hardcoded | `repositories.py:1559-1585` |
| `macro_snapshots` | 90d | hardcoded | `repositories.py:1587` |
| `coingecko_top_snapshots` (Round 2 Step 3) | 14d | hardcoded | `repositories.py:prune_coingecko_top_snapshots` |
| `opportunities` (open) | 48h → `expired` | yes | `repositories.py:1597-1612` |

### 7.2 NEVER-pruned (trust-bearing) — enforced by `_PRUNABLE` allowlist (`repositories.py:1000-1006`)

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
