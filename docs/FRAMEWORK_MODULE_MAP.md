# Aletheia Framework Module Map

> Module-level architecture of the Aletheia Agentic Trading Framework.
> Last updated: 2026-06-14

## Design Principles

- Aletheia is a **local-first Agentic Trading Framework** — not a single strategy, chatbot, or live-trading agent.
- The primary training loop is **forward-time paper trading**, not historical backtesting.
- Agents are **workers inside modules**, not top-level modules.
- UI pages are **observation/control surfaces**, not core modules.
- Every important action is **persisted** as event log or structured state.
- Gateway restart must **never lose** agent runs, paper orders, positions, market snapshots, decisions, reviews, or lessons.

## Primary Training Loop

```
Market Data Adapter
  → Market State / World Model
    → AI Trader Decision (DecisionRecord)
      → Paper Trading / Simulation (simulated orders)
        → Settlement (fills, PnL)
          → Review & Learning (post-trade review, lessons)
            → Proposal & Promotion (if gates pass)
              → Optional small live execution (after human approval)
```

Natural language trade advice **never** becomes a live order directly. Only approved proposals that have passed promotion gates may reach live execution.

---

## Module Index

| # | Module | Priority | Primary Package |
|---|--------|----------|-----------------|
| 1 | Runtime Gateway | Infra | `gateway/`, `server/` |
| 2 | Scheduler & Agent Runtime | Infra | `agent_runtime/` |
| 3 | Persistence & Recovery | P0 | `storage/`, `files/`, `backup/` |
| 4 | Market Data Adapter | P0 | `data/` |
| 5 | Evidence & Claim | P1 | `agents/research_worker.py`, `trading/serenity_scoring.py` |
| 6 | Market State / World Model | P0 | `trading/market_data.py`, `trading/market_context.py`, `trading/indicators.py` |
| 7 | AI Trader Decision | P0 | `agents/market_observer.py`, `agents/strategy_designer.py` |
| 8 | Paper Trading / Simulation | P0 | `execution/paper_broker.py`, `portfolio/` |
| 9 | Review & Learning | P0 | `knowledge/`, `learning/`, `agents/post_trade_reviewer.py`, `agents/dreaming_agent.py` |
| 10 | StrategySpec / DSL / Validation | P1 | `trading/strategy_lifecycle.py`, `trading/playbooks.py` |
| 11 | Proposal & Promotion | P1 | `trading/strategy_lifecycle.py` (promotion gates) |
| 12 | Risk / Execution / Governance | P2 (live parts) | `risk/`, `execution/gateway.py`, `execution/bitget_broker.py` |

---

## Module 1: Runtime Gateway

**Priority:** Infrastructure (keep stable)

### Purpose
Process lifecycle management — start, stop, pause, resume, recover. Single entry point that serves the HTTP API and static dashboard.

### Responsibilities
- Gateway state machine (STOPPED → STARTING → RECOVERY_MODE → RUNNING → DEGRADED → SAFE_FREEZE → STOPPING)
- Preflight checks (Python version, venv, static assets, config)
- Process tracking (pidfile, orphan cleanup)
- Graceful shutdown & restart with state recovery
- Static dashboard serving (pre-built Vite assets)
- HTTP API routing (FastAPI)

### Inputs
- Operator commands (start/stop/pause/resume)
- Config changes requiring restart

### Outputs
- Gateway status events
- HTTP API endpoints
- Audit events on lifecycle transitions

### Persistent State
- `gateway/state_store.py` — gateway status, last-known state
- `gateway/pidfile.py` — process ID for orphan detection
- `gateway/session.py` — session metadata

### Main Services/Classes
- `gateway/lifecycle.py`: `do_start()`, `do_stop()`, `do_pause()`, `do_resume()`
- `gateway/state_machine.py`: `GatewayStatus` enum, `_FREEZE_BLOCKED_ACTIONS`, `_FREEZE_ALLOWED_ACTIONS`
- `gateway/recovery.py`: graceful restart + state recovery
- `gateway/preflight.py`: pre-flight validation
- `server/app.py`: FastAPI application lifespan

### Existing Agents
None (infrastructure only).

### Dependencies
- `config/` (load configuration)
- `storage/migrations.py` (schema initialization at startup)
- `agent_runtime/scheduler.py` (start scheduler after gateway boots)

### Must NOT Do
- Expand UI framework complexity beyond what supports the paper-training loop
- Add new exchange integrations
- Make trading decisions
- Modify risk controls

---

## Module 2: Scheduler & Agent Runtime

**Priority:** Infrastructure (keep stable)

### Purpose
Orchestrate agent execution on schedule, enforce permissions, log agent lifecycles.

### Responsibilities
- APScheduler job registration and lifecycle (3-worker ThreadPoolExecutor)
- Agent invocation wrapper with lifecycle logging (start_run → execute → end_run)
- Tool permission enforcement (Observe / Paper / Full modes)
- LLM provider health tracking and startup/resume gates
- Agent stability monitoring

### Inputs
- Cron/interval triggers from scheduler config
- Manual operator `run_agent` commands
- Permission mode from config

### Outputs
- `AgentRun` records (started_at, status, tools_called, outputs_created, errors, cost_estimate)
- `AgentRunStep` records (per-tool-call audit trail)
- Health/stability signals

### Persistent State
- `agent_runs` table (all agent executions)
- `agent_run_steps` table (per-step audit)
- `agent_tool_calls` table

### Main Services/Classes
- `agent_runtime/scheduler.py`: `init_scheduler()`, job registration (12+ jobs)
- `agent_runtime/orchestrator.py`: `run_agent(agent_name, trigger_type)`
- `agent_runtime/permissions.py`: `is_tool_permitted(tool, permission_mode)`
- `agent_runtime/tool_registry.py`: `OBSERVE_TOOLS`, `PAPER_TOOLS`, `SYSTEM_TOOLS`, `RESTRICTED_TOOLS`
- `agent_runtime/run_logger.py`: `start_run()`, `end_run()`
- `agent_runtime/health.py`, `stability_monitor.py`: LLM availability tracking

### Existing Agents Under This Runtime
All registered agents: `market_observer`, `stock_observer`, `prediction_observer`, `paper_trader`, `research_agent`, `research_worker`, `strategy_designer`, `portfolio_agent`, `post_trade_reviewer`, `risk_critic`, `dreaming_agent`.

### Dependencies
- `config/` (scheduler intervals, permission mode, kill_switch)
- `storage/` (write agent run records)
- `model_provider/` (LLM health checks)

### Must NOT Do
- Make trading decisions (agents do that)
- Hold market state (stateless orchestration)
- Bypass permission modes
- Raise `max_workers` above 3 (SQLite WAL contention)

---

## Module 3: Persistence & Recovery

**Priority:** P0

### Purpose
Durable storage of all framework state — SQLite for structured data, JSONL for event streams, Markdown for knowledge artifacts. Guarantee that gateway restart loses nothing.

### Responsibilities
- SQLite connection management (WAL mode, thread-local connections, busy_timeout)
- Schema migrations (idempotent CREATE TABLE IF NOT EXISTS)
- Repository CRUD layer (~150+ functions)
- JSONL event logging with rotation
- Markdown file persistence for knowledge items
- Backup/restore services
- Transaction correctness (was_in_tx check before commit)

### Inputs
- All modules writing structured data (opportunities, proposals, orders, fills, positions, strategies, knowledge, agent runs)
- Event streams (audit, trades, agent tool calls)

### Outputs
- Queryable state for all modules
- Point-in-time backup snapshots
- Recoverable state after crash

### Persistent State
- `data/app.db` — SQLite (60+ tables)
- `data/knowledge/` — Markdown research notes, reviews, dreaming logs
- `data/logs/*.jsonl` — Event streams with rotation
- `data/backups/` — Periodic backup archives

### Main Services/Classes
- `storage/sqlite.py`: `get_db()`, `close_db()`, WAL+busy_timeout setup
- `storage/migrations.py`: `run_migrations()` (idempotent schema)
- `storage/repositories.py`: `insert_*`, `update_*`, `get_*`, `list_*` (~150 functions)
- `storage/models.py`: All Pydantic models (`AuditEvent`, `AgentRun`, `Opportunity`, `TradeProposal`, `RiskDecision`, `Order`, `Fill`, `Position`, `StrategyRecord`, `KnowledgeItem`, etc.)
- `storage/event_store.py`: `get_event_store()`, `make_event()`
- `files/jsonl.py`: `append_jsonl()` with rotation
- `files/markdown.py`: `write_research_note()`, `write_post_trade_review()`
- `backup/service.py`: Backup/restore

### Dependencies
- None (leaf dependency — other modules depend on this)

### Must NOT Do
- Interpret or transform data (pure storage layer)
- Make decisions based on stored data
- Delete data without explicit operator command
- Cache connections across threads (threading.local only)

---

## Module 4: Market Data Adapter

**Priority:** P0

### Purpose
Ingest real-time and historical market data from external sources. Single protocol for all asset classes.

### Responsibilities
- Fetch OHLCV candles, tickers, orderbooks via CCXT (crypto: Binance/Bybit default, Bitget for xStocks)
- Fetch US equity data via yfinance
- Fetch prediction market data via Polymarket Gamma API
- Symbol canonicalization (always `BTC/USDT` internally)
- OHLCV caching with configurable TTL
- Proxy-aware HTTP (trust_env=True for aiohttp)
- Provider-per-call pattern (never cache CCXT instances across event loops)

### Inputs
- Observed symbol list from config (`market_data.observed_symbols`, `observed_stocks`, `observed_prediction_markets`)
- Scheduler triggers (5-min OHLCV refresh)

### Outputs
- `Ticker` dataclass (bid, ask, last, volume, timestamp)
- `Candle` dataclass (open, high, low, close, volume, timestamp)
- Cached OHLCV series

### Persistent State
- In-memory OHLCV cache (TTL-based, lost on restart — acceptable, re-fetched immediately)
- Market data freshness tracked in `storage/` for risk engine staleness checks

### Main Services/Classes
- `data/providers.py`: `MarketDataProvider` protocol (get_ticker, get_ohlcv, close)
- `data/ccxt_provider.py`: `CCXTProvider` — async CCXT wrapper
- `data/yfinance_provider.py`: `YFinanceProvider` — US equity adapter
- `data/polymarket_provider.py`: `PolymarketProvider` — prediction market adapter
- `data/cache.py`: OHLCV cache (TTL = `ohlcv_cache_ttl_min`)
- `data/symbols.py`: `to_canonical()`, `to_bitget()`, `InstrumentId`

### Existing Agents Using This Module
- `market_observer` (crypto OHLCV + tickers)
- `stock_observer` (yfinance)
- `prediction_observer` (Polymarket)

### Dependencies
- `config/` (symbol lists, exchange selection, cache TTL)
- External: Binance/Bybit/Bitget APIs, Yahoo Finance, Polymarket Gamma API

### Must NOT Do
- Interpret market data (no bias, no signals — raw data only)
- Place orders (execution-context only in `exchanges/bitget/`)
- Cache provider instances across event loops
- Use `BitgetClient` for market data (it rejects canonical symbols)

---

## Module 5: Evidence & Claim

**Priority:** P1

### Purpose
Multi-stage research pipeline — collect evidence from market data, form claims (hypotheses), score themes for confidence/novelty/actionability. Feeds structured intelligence to the AI Trader Decision module.

### Responsibilities
- Evidence collection from multiple data sources
- Claim formation (structured hypotheses with citations)
- Theme scoring (confidence, novelty, actionability dimensions)
- Serenity research runs (multi-stage: evidence → claims → themes)
- Citation tracking (every claim links back to evidence)

### Inputs
- Market data snapshots (from Module 4)
- Existing knowledge base (from Module 9)
- Agent-generated observations

### Outputs
- `SerenityEvidence` records (raw data points with source attribution)
- `SerenityClaim` records (structured hypotheses)
- `SerenityTheme` records (scored, actionable research themes)
- `SerenityRunEvent` records (audit trail of research process)

### Persistent State
- `serenity_evidence` table
- `serenity_claims` table
- `serenity_themes` table
- `serenity_run_events` table
- `serenity_opportunities` table

### Main Services/Classes
- `agents/research_worker.py`: Multi-stage Serenity research pipeline
- `trading/serenity_scoring.py`: Theme scoring (confidence, novelty, actionability)
- `agents/research_agent.py`: Single-pass market structure analysis → `KnowledgeItem`

### Existing Agents
- `research_worker` (scheduled every 20 min)
- `research_agent` (on-demand)

### Dependencies
- Module 4: Market Data Adapter (raw data input)
- Module 3: Persistence & Recovery (store evidence/claims/themes)
- Module 9: Review & Learning (knowledge retrieval for context)

### Must NOT Do
- Make trading decisions (claims inform decisions, they don't make them)
- Execute orders
- Modify risk parameters
- Bypass confidence gating (speculative claims must not reach high-confidence retrieval)

---

## Module 6: Market State / World Model

**Priority:** P0

### Purpose
Transform raw market data into a coherent market state representation suitable for LLM-based decision making. Provides technical indicators, macro context, and structured snapshots.

### Responsibilities
- Compute technical indicators (SMA, RSI, ATR, Bollinger, momentum)
- Assemble market context (SPY, VIX, DXY for macro regime)
- Produce structured market snapshots for LLM prompts
- Maintain current-state view across observed instruments

### Inputs
- OHLCV candles and tickers (from Module 4)
- Config: observed symbol list

### Outputs
- `latest_snapshot()` — structured market state for LLM consumption
- Technical indicator values per symbol
- Macro context (regime classification aids)
- Formatted prompt-ready market summaries

### Persistent State
- Opportunity records in SQLite (the output of observer agents consuming this module's data)
- Market snapshots referenced by agent runs

### Main Services/Classes
- `trading/market_data.py`: `latest_snapshot()` — current market state
- `trading/market_context.py`: Macro context assembly (SPY, VIX, DXY)
- `trading/indicators.py`: `sma()`, `rsi()`, `atr()`, `bollinger()`, `momentum()`
- `trading/instruments.py`: Symbol/market parsing

### Existing Agents Consuming This Module
- `market_observer` (crypto analysis)
- `stock_observer` (equity analysis)
- `prediction_observer` (prediction market analysis)
- `strategy_designer` (hypothesis formation)

### Dependencies
- Module 4: Market Data Adapter (raw OHLCV/tickers)
- Module 3: Persistence & Recovery (store snapshots, opportunities)

### Must NOT Do
- Make trading decisions (provides state, not judgment)
- Place orders
- Filter opportunities by risk (that's Module 12)
- Persist raw market data long-term (cache layer only)

---

## Module 7: AI Trader Decision

**Priority:** P0

### Purpose
LLM-powered agents that analyze market state and produce structured DecisionRecords (opportunities, strategy hypotheses). The "brain" of the paper-training loop.

### Responsibilities
- Per-symbol market analysis → `Opportunity` records (direction_bias, confidence, confidence_score, rationale, key_observations)
- Strategy hypothesis generation → `StrategySpec` records (entry/exit rules, timeframe, rationale)
- Confidence gating (only sufficient-confidence opportunities proceed)
- Structured output enforcement via Pydantic models (`MarketRead`, `StockRead`, `PredictionMarketRead`, `StrategySpec`)

### Inputs
- Market state snapshots (from Module 6)
- Existing strategies and knowledge (from Modules 9, 10)
- Research themes and claims (from Module 5)
- Playbook templates (from Module 10)

### Outputs
- `Opportunity` records (inserted into storage, consumed by paper_trader)
- `StrategyRecord` records (strategy hypotheses in lifecycle)
- Agent run audit trail

### Persistent State
- `opportunities` table (all generated opportunities)
- `strategies` table (strategy hypotheses)
- `agent_runs` / `agent_run_steps` (decision audit trail)

### Main Services/Classes
- `agents/market_observer.py`: `MarketRead` → `Opportunity` (crypto, scheduled 15 min)
- `agents/stock_observer.py`: `StockRead` → `Opportunity` (equity)
- `agents/prediction_observer.py`: `PredictionMarketRead` → `Opportunity` (prediction markets)
- `agents/strategy_designer.py`: `StrategySpec` → `StrategyRecord` (daily cron)
- `agents/helpers.py`: `llm_complete()`, `llm_structured()`, `log_tool()`, `record_step()`

### Existing Agents
- `market_observer` (15 min)
- `stock_observer` (scheduled)
- `prediction_observer` (scheduled)
- `strategy_designer` (daily 1:00 UTC)

### Dependencies
- Module 6: Market State / World Model (input data)
- Module 5: Evidence & Claim (research context)
- Module 10: StrategySpec / DSL (playbook templates)
- Module 2: Scheduler & Agent Runtime (orchestration)
- `model_provider/` (LLM API calls)

### Must NOT Do
- Place orders (outputs DecisionRecords only)
- Bypass confidence gating
- Modify risk controls or execution parameters
- Self-promote strategies without gate checks
- Access exchange APIs directly

---

## Module 8: Paper Trading / Simulation

**Priority:** P0

### Purpose
Convert AI-generated DecisionRecords into simulated paper orders, manage paper positions, compute PnL, and settle trades — the core execution environment for the training loop.

### Responsibilities
- Convert open `Opportunity` records into `TradeProposal` (confidence gating, notional sizing)
- Submit proposals through execution gateway (risk check → paper execution)
- Simulate order fills (paper_internal mode: instant fill at last price)
- Track paper positions (entry, exit, unrealized PnL)
- Auto-close positions via stop-loss / take-profit / max-hold-time
- Portfolio snapshot computation (periodic PnL aggregation)
- Position reconciliation (sync adopted exchange positions)

### Inputs
- `Opportunity` records from Module 7 (direction_bias, confidence, symbol)
- Risk decisions from Module 12 (approve/reject proposals)
- Market prices for mark-to-market PnL

### Outputs
- `TradeProposal` records (symbol, direction, execution_mode, status)
- `Order` records (paper fills)
- `Fill` records (simulated execution)
- `Position` records (open/closed, entry/exit prices, PnL)
- `PortfolioSnapshot` records (aggregate PnL snapshots)

### Persistent State
- `trade_proposals` table
- `orders` table
- `fills` table
- `positions` table
- `portfolio_snapshots` table

### Main Services/Classes
- `agents/paper_trader.py`: Converts opportunities → proposals (fixed `_PAPER_NOTIONAL_USDT = 25.0`, confidence gating)
- `execution/gateway.py`: `submit_proposal()` — central entry point (proposal → risk → permission → execution)
- `execution/paper_broker.py`: `execute_paper_order()` — simulates fills
- `execution/paper_tools.py`: `create_paper_proposal()` — agent-facing API
- `portfolio/position_manager.py`: Auto-close by SL/TP/max-hold
- `portfolio/pnl.py`: PnL computation
- `portfolio/position_reconciler.py`: Sync exchange positions as `source="adopted"`
- `agents/portfolio_agent.py`: `compute_and_store_snapshot()` (10-min schedule)

### Existing Agents
- `paper_trader` (scheduled, converts opportunities to proposals)
- `portfolio_agent` (10-min snapshots)

### Dependencies
- Module 7: AI Trader Decision (opportunity input)
- Module 12: Risk / Execution / Governance (proposal approval)
- Module 4: Market Data Adapter (current prices for PnL)
- Module 3: Persistence & Recovery (store proposals, orders, fills, positions)

### Must NOT Do
- Place live orders (paper_internal mode only in this module)
- Bypass risk engine evaluation
- Auto-promote strategies to live (that's Module 11)
- Modify position sizing beyond configured limits

---

## Module 9: Review & Learning

**Priority:** P0

### Purpose
Close the experience loop — review completed trades, extract lessons, consolidate knowledge, and feed learnings back into future decisions. The module that turns paper trading into skill acquisition.

### Responsibilities
- Post-trade review of recent fills by strategy
- Risk decision critique (was the risk engine correct?)
- Daily consolidation ("dreaming" — strategy counts, knowledge ledger, pattern synthesis)
- Knowledge item management (research notes, reviews, lessons) with confidence gating
- Full-text retrieval indexing for high-confidence knowledge
- Skill and lesson persistence (agent-learned capabilities)

### Inputs
- Completed trades and fills (from Module 8)
- Risk decisions (from Module 12)
- Strategy performance metrics
- Agent observations and research output

### Outputs
- `KnowledgeItem` records (research notes, post-trade reviews, daily reviews, dreaming logs)
- `PSSkill` records (learned agent capabilities)
- `PSLesson` records (extracted lessons: general, market, execution, risk)
- `PostTradeReview` records
- `DreamingLog` records (daily consolidation)
- High-confidence items indexed for retrieval (experience loop)

### Persistent State
- `knowledge_items` table
- `ps_skills` table (learned skills)
- `ps_lessons` table (extracted lessons)
- `data/knowledge/` directory (Markdown artifacts)
- Full-text retrieval index (high-confidence items only)

### Main Services/Classes
- `knowledge/manager.py`: `create_research_note()`, `create_post_trade_review()`, `create_daily_review()`, `create_dreaming_log()`, `create_strategy_memory()`
- `knowledge/retrieval.py`: `index_knowledge_item()` — full-text indexing for experience loop (§6)
- `learning/skills.py`: `SkillStore` (create, list; `FORBIDDEN_LAYERS` enforcement)
- `learning/lessons.py`: `LessonStore` (create with category/severity/evidence)
- `agents/post_trade_reviewer.py`: Reviews recent fills → `PostTradeReview`
- `agents/risk_critic.py`: Critiques risk decisions → `ResearchNote(type="risk_lesson")`
- `agents/dreaming_agent.py`: Daily consolidation → `DreamingLog`

### Existing Agents
- `post_trade_reviewer` (scheduled)
- `risk_critic` (scheduled)
- `dreaming_agent` (daily 2:00 UTC)

### Dependencies
- Module 8: Paper Trading / Simulation (completed trade data)
- Module 12: Risk / Execution / Governance (risk decisions to critique)
- Module 3: Persistence & Recovery (store knowledge, skills, lessons)
- `model_provider/` (LLM for review generation)

### Must NOT Do
- Place orders (observation/reflection only)
- Modify hard risk controls (can suggest via lessons, never enforce)
- Bypass confidence gating (speculative → medium → high_confidence progression)
- Write to `FORBIDDEN_LAYERS` (risk_limits, execution_gateway, credentials, live_mode, order_submission)

---

## Module 10: StrategySpec / DSL / Validation

**Priority:** P1

### Purpose
Define the structure, lifecycle states, and validation rules for trading strategies. Provides playbook templates and ensures strategies follow a rigorous lifecycle from hypothesis to production.

### Responsibilities
- Strategy lifecycle state machine (draft → research_note → strategy_hypothesis → paper_candidate → paper_running → paper_passed → live_candidate → live_limited → retired/deprecated)
- Playbook template management (seed hypotheses for agent context)
- StrategySpec schema enforcement (name, timeframe, direction_bias, entry_rule, exit_rule, rationale)
- Strategy creation and state transitions
- Promotion gate definitions (min_paper_runtime_days, min_paper_trades, positive_expectancy, max_drawdown, reviews_required)

### Inputs
- Strategy hypotheses from Module 7
- Performance metrics from Module 8
- Review outcomes from Module 9

### Outputs
- `StrategyRecord` with validated lifecycle state
- Promotion gate check results (pass/fail with reasons)
- Playbook templates for agent consumption

### Persistent State
- `strategies` table (all strategies with state + parameters)
- `data/playbooks/` (5 seed hypothesis templates, Markdown)

### Main Services/Classes
- `trading/strategy_lifecycle.py`: `LIFECYCLE_STATES`, `DEFAULT_PROMOTION_GATES`, `create_strategy()`, `promote_strategy()`, `is_active_paper_strategy()`
- `trading/playbooks.py`: `load_playbooks()` — seed hypothesis templates (5 playbooks: regime observation, trend following, volatility, funding pressure, breakout)
- `agents/strategy_designer.py`: Consumes playbooks, outputs `StrategySpec`

### Dependencies
- Module 3: Persistence & Recovery (store strategy records)
- Module 9: Review & Learning (review data for gate checks)

### Must NOT Do
- Execute trades (defines strategy structure, not execution)
- Bypass promotion gates
- Auto-promote without explicit gate validation
- Modify hard risk limits

---

## Module 11: Proposal & Promotion

**Priority:** P1

### Purpose
Gate-check paper-proven strategies for live promotion. Enforce minimum evidence thresholds before any strategy reaches live execution. Only approved proposals cross the paper→live boundary.

### Responsibilities
- Evaluate promotion gates (min 14 days paper runtime, min 30 trades, positive expectancy, max 5% drawdown, 0 critical risk violations, post-trade reviews completed)
- Walk strategies through ungated state transitions automatically
- Block gated transitions until all evidence thresholds pass
- Emit promotion audit events
- Present promotion candidates for human approval

### Inputs
- Strategy records with `paper_passed` state (from Module 10)
- Trade statistics from Module 8 (trade count, win rate, drawdown)
- Review records from Module 9 (required reviews completed)
- Risk violation history from Module 12

### Outputs
- Strategy state transitions (paper_passed → live_candidate → live_limited)
- Promotion gate evaluation results (with specific pass/fail per gate)
- Audit events for every promotion attempt

### Persistent State
- Strategy `promotion_status` field
- `audit_events` table (promotion decisions)

### Main Services/Classes
- `trading/strategy_lifecycle.py`: `promote_strategy()`, `DEFAULT_PROMOTION_GATES`
  - `min_paper_runtime_days`: 14
  - `min_paper_trades`: 30
  - `positive_expectancy_required`: True
  - `max_drawdown_pct_allowed`: 5%
  - `critical_risk_violations_allowed`: 0
  - `post_trade_reviews_required`: True

### Dependencies
- Module 10: StrategySpec / DSL (strategy lifecycle states)
- Module 8: Paper Trading / Simulation (trade statistics)
- Module 9: Review & Learning (review completion)
- Module 12: Risk / Execution / Governance (violation history)

### Must NOT Do
- Auto-execute live trades (presents candidates, doesn't act)
- Bypass promotion gates for any reason
- Weaken gate thresholds without operator confirmation
- Promote without post-trade review evidence

---

## Module 12: Risk / Execution / Governance

**Priority:** P2 (live execution parts) — risk evaluation for paper trades is P0

### Purpose
Hard safety boundary — enforce risk limits, evaluate proposals, gate live execution, and govern the paper→live transition. The only module authorized to touch live exchange APIs.

### Responsibilities
- Evaluate every trade proposal against risk rules (kill switch, daily-loss limit, data freshness, position sizing)
- Kill switch management (auto-trip on daily loss threshold, UTC-day reset)
- Hard risk controls enforcement (max position size, max account risk, API key permissions)
- Live execution via Bitget API (demo and live modes)
- Permission mode enforcement (Observe / Paper / Full)
- First-live-confirmation ceremony
- Exchange position reconciliation

### Inputs
- `TradeProposal` from Module 8 (for risk evaluation)
- Market data freshness (from Module 4)
- Config: risk limits, execution mode, live state
- Operator commands (unlock live, change limits)

### Outputs
- `RiskDecision` records (approved/rejected, risk_score, violations)
- `Order` records (for live execution only)
- `Fill` records (live fills from Bitget)
- Kill switch state changes
- Audit events for every risk boundary interaction

### Persistent State
- `risk_decisions` table
- `kill_switch` state (in config + runtime)
- `orders` / `fills` tables (live execution records)
- Daily loss tracking

### Main Services/Classes
- `risk/engine.py`: `evaluate_proposal()` — serialized proposal evaluation (threading lock)
  - Checks: kill_switch_active, daily_loss_limit, data_freshness (120s live / 1800s demo), position_sizing
  - Returns: `RiskDecision(approved, risk_score, violations)`
- `risk/kill_switch.py`: Auto-trip on daily loss threshold
- `execution/gateway.py`: `submit_proposal()` — central flow (proposal → risk → permission → execute)
- `execution/bitget_broker.py`: Real Bitget API calls (demo/live)
- `execution/reconciliation.py`: Sync exchange positions
- `exchanges/bitget/client.py`: Low-level Bitget REST API adapter
- `exchanges/bitget/auth.py`: API key management

### Existing Agents
- `risk_critic` (reviews risk decisions — read-only critique, cannot modify controls)

### Dependencies
- Module 3: Persistence & Recovery (store decisions, orders, fills)
- Module 4: Market Data Adapter (freshness checks)
- `config/` (risk limits, live state, execution mode)
- External: Bitget API (demo/live)

### Must NOT Do (Hard Boundaries)
- **Never** enable live trading without explicit operator confirmation
- **Never** modify hard risk limits autonomously (live switch, max position size, max account risk, API key permissions, withdrawal permissions, kill switch)
- **Never** place live orders without passing all risk checks
- **Never** allow AI to modify these controls (AI may suggest via lessons, never enforce)
- **Never** bypass kill switch
- **Never** weaken daily-loss limits without human approval

---

## Agent-to-Module Mapping

Agents are **workers** inside modules. They are not modules themselves.

| Agent | Primary Module | Role |
|-------|---------------|------|
| `market_observer` | 7 (AI Trader Decision) | Crypto opportunity generation |
| `stock_observer` | 7 (AI Trader Decision) | Equity opportunity generation |
| `prediction_observer` | 7 (AI Trader Decision) | Prediction market opportunity generation |
| `strategy_designer` | 7 (AI Trader Decision) | Strategy hypothesis formation |
| `paper_trader` | 8 (Paper Trading) | Opportunity → proposal conversion |
| `portfolio_agent` | 8 (Paper Trading) | PnL snapshot computation |
| `research_agent` | 5 (Evidence & Claim) | Single-pass market research |
| `research_worker` | 5 (Evidence & Claim) | Multi-stage Serenity pipeline |
| `post_trade_reviewer` | 9 (Review & Learning) | Trade outcome analysis |
| `risk_critic` | 9 (Review & Learning) | Risk decision critique |
| `dreaming_agent` | 9 (Review & Learning) | Daily consolidation |

---

## Boundary Rules (Invariants)

1. **Natural language → live order is FORBIDDEN.** AI produces DecisionRecords. Paper Trading converts them to simulated orders. Only promotion-gated, human-approved proposals reach live execution.

2. **AI cannot modify hard risk controls.** Hard controls: live switch, max position size, max account risk, API key permissions, withdrawal permissions, kill switch. AI may suggest policy updates via lessons — never enforce them.

3. **Every important action persists.** Agent runs, decisions, proposals, risk evaluations, orders, fills, positions, knowledge items, lessons — all stored in SQLite or JSONL event logs.

4. **Gateway restart loses nothing.** All state survives in `data/app.db` and `data/` directory. In-memory caches (OHLCV) are reconstructed on next scheduled fetch.

5. **Forward-time paper trading is the primary training loop.** Historical backtesting and DSL validation may exist, but the loop that builds agent skill is: observe → decide → paper-trade → settle → review → learn → repeat.

6. **Confidence gating at every transition.** Speculative observations don't become trades. Unreviewed strategies don't promote. Unproven strategies don't go live.

7. **Agents are workers, not framework boundaries.** Adding a new agent does not add a new module. Agents operate within the permission and persistence boundaries of their owning module.

---

## Anti-Expansion Guardrails

This framework does **NOT** need:
- New exchange integrations (Bitget only)
- Docker/Postgres/Redis infrastructure
- Vector search / RAG pipelines
- Web scraping / news ingestion agents
- Social trading / multi-user features
- Complex backtesting frameworks (forward-time paper is primary)
- Additional UI frameworks or SPA complexity

New capabilities should only be added if they directly strengthen the paper-training loop:
1. Better market state representation
2. Better decision quality (agent prompts, structured output)
3. Better simulation fidelity
4. Better review depth
5. Better lesson extraction and retrieval
6. Clearer promotion evidence
