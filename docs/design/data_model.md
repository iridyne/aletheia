# Data Model Reference

> Complete index of Aletheia Trading OS data layer.
> ~80 SQLite tables, 52 Pydantic models, WAL mode, additive-only migrations.
> Source of truth: `aletheia/storage/migrations.py` (DDL) + `models.py` (types).
> Last updated: 2026-06-21

---

## 1. Architecture Overview

- **Engine**: SQLite 3, WAL mode, `busy_timeout=8000ms`
- **Concurrency**: one connection per thread via `threading.local`; scheduler capped at `max_workers=3`
- **Transaction safety**: `was_in_tx = db.in_transaction` captured BEFORE every write; commit only `if not was_in_tx`
- **Migrations**: additive-only (new tables, new columns with defaults); never DROP/RENAME in production
- **ID format**: UUID4 string (36 chars) for most entities
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
| `open_interest_snapshots` | Open interest data | symbol, open_interest, oi_change_pct |
| `liquidation_events` | Large liquidation events | symbol, side, quantity, price |
| `macro_snapshots` | Macro economic indicators | indicator, value, source |

### E. Agent Runtime (7 tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `agent_runs` | Agent execution records | agent_name, trigger_type, status, outputs (JSON) |
| `agent_run_steps` | Step-by-step agent traces | run_id, step_number, action, result |
| `agent_tool_calls` | Tool invocation log | run_id, tool_name, arguments (JSON), result |
| `audit_events` | System-wide audit trail | actor, source, event_type, entity_id, before/after (JSON) |
| `settings` | Runtime configuration store | key, value |
| `runtime_registry` | Service/process registry | service_name, pid, started_at, health |
| `runtime_checkpoints` | Long-running task progress | task_id, checkpoint_data (JSON), progress_pct |

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

### H. Serenity Pipeline (12 tables)

| Table | Purpose |
|-------|---------|
| `serenity_themes` | Research themes/hypotheses |
| `serenity_evidence` | Evidence supporting themes |
| `serenity_opportunities` | Theme-derived trading opportunities |
| `serenity_risk_gates` | Risk checks for Serenity trades |
| `serenity_replay_snapshots` | Decision replay for learning |
| `serenity_post_mortems` | Post-trade analysis |
| `serenity_research_runs` | Research execution records |
| `serenity_run_events` | Research run events |
| `serenity_user_profiles` | Researcher profiles |
| `serenity_profile_snapshots` | Profile state snapshots |
| `serenity_feedback_events` | Feedback loop events |
| `serenity_claims` | Falsifiable claims from research |

### I. PS (Production System) Layer (12 tables)

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

- **ID generation**: `str(uuid4())` at insert time (Python-side)
- **Timestamps**: Always UTC ISO 8601; `datetime.now(timezone.utc).isoformat()`
- **JSON columns**: Stored as TEXT; Python dict/list serialized via `json.dumps()`
- **Nullable**: Most columns allow NULL; defaults specified in DDL
- **Indexes**: Created on high-query columns (symbol, status, created_at)
- **FTS5**: Virtual table `knowledge_items_fts` for full-text search on knowledge

---

## 6. Migration Strategy

1. Fresh DB: full DDL from `SCHEMA_SQL` + domain-specific DDL blocks
2. Existing DB: `_ADDITIVE_COLUMNS` list applies `ALTER TABLE ADD COLUMN` idempotently
3. Never: DROP TABLE, DROP COLUMN, RENAME (breaks existing data)
4. Pattern: check `pragma_table_info` before ALTER to avoid "duplicate column" errors
5. Test: `run_migrations()` is called in every test setup; full schema always available
