# Agent Contracts

> Input/output contracts for all 14 registered agents and 30 scheduler jobs.
> Each agent has defined triggers, data inputs, outputs, and side effects.
> Source of truth: `aletheia/agent_runtime/scheduler.py` + `aletheia/agents/*.py`
> Last updated: 2026-06-21

---

## 1. Scheduler Job Overview (30 jobs)

| # | Job ID | Interval | Executor | Description |
|---|--------|----------|----------|-------------|
| 1 | market_data | 5min | io | Collect market snapshots (price, volume, OHLCV) |
| 2 | market_observer | 15min (jitter 120s) | observer | Scan for trading opportunities |
| 3 | stock_observer | 4h | observer | xStocks/equity market scan |
| 4 | prediction_observer | 6h | observer | Prediction market scan |
| 5 | paper_trader | 5min | agents | Execute paper trades from pending signals |
| 6 | pi_trading_decision | 5min | agents | PI Agent heartbeat (daemon fallback) |
| 7 | position_manager | 10min | agents | Manage open positions (trailing stop, exit) |
| 8 | tpsl_fill_check | 2min | io | Check exchange TP/SL order fills |
| 9 | position_reconcile | 30min | io | Reconcile local vs exchange positions |
| 10 | post_trade_reviewer | 2h | agents | Review closed trades for lessons |
| 11 | risk_critic | 1h | agents | Evaluate risk exposure, flag violations |
| 12 | dreaming_daily | daily 02:00 UTC | agents | Synthesize daily knowledge summary |
| 13 | strategy_designer_daily | daily 03:00 UTC | agents | Generate new strategy proposals |
| 14 | strategy_designer_eu | daily 08:00 UTC | agents | EU session strategy generation |
| 15 | strategy_designer_us | daily 14:00 UTC | agents | US session strategy generation |
| 16 | insight_synthesizer | daily 04:00 UTC | agents | Cross-agent insight synthesis |
| 17 | confidence_calibration | 6h | agents | Compute ECE calibration metrics |
| 18 | pi_operator_loop | 1h | agents | High-level operator assessment loop |
| 19 | health_check | 2min | io | Doctor health probe |
| 20 | cleanup | daily 05:00 UTC | io | Data retention cleanup |
| 21 | exchange_sync | 30s | io | Refresh exchange account cache |
| 22 | portfolio_snapshot | 15min | io | Store portfolio equity snapshot |
| 23 | reconciliation | 6h | io | Full state reconciliation |
| 24 | price_feed | 30s | io | In-memory price cache refresh |
| 25 | daily_summary | daily 00:00 UTC | io | Generate daily performance summary |
| 26 | data_collectors | 2min | io | Funding rate, OI, liquidation, orderbook |
| 27 | okx_demo_symbols | daily | io | Refresh OKX demo symbol list |
| 28 | backtest_orchestrator | 4h | agents | Run pending backtests |
| 29 | universe_refresh | 15min | io | Universe composition refresh |
| 30 | ohlcv_nightly_backfill | daily 01:00 UTC | io | Historical OHLCV gap fill |

---

## 2. Agent Contracts (by function)

### A. Market Sensing Agents

#### market_observer

- **Trigger**: scheduler every 15min + event-driven wake
- **Inputs**: market_snapshots (recent), config.market_data.observed_symbols, universe features
- **LLM**: Yes (analysis of market conditions)
- **Outputs**: opportunities table (symbol, direction, confidence, key_observations JSON)
- **Side effects**: Writes to `opportunities` table; triggers PI daemon wake via `notify_pi_daemon()`
- **Error behavior**: Degrades to basic technical-only signals when LLM unavailable

#### stock_observer

- **Trigger**: scheduler every 4h
- **Inputs**: xStocks config, ON-series market data
- **LLM**: Yes
- **Outputs**: opportunities table (xStocks symbols)
- **Side effects**: Same as market_observer but for equity-linked tokens

#### prediction_observer

- **Trigger**: scheduler every 6h
- **Inputs**: prediction market APIs, existing prediction_markets table
- **LLM**: No (structured data parsing)
- **Outputs**: prediction_events, prediction_markets, prediction_probability_snapshots
- **Side effects**: Creates prediction_market_claims when probability crosses thresholds

---

### B. Trading Agents

#### paper_trader (refactored: signal collector)

- **Trigger**: scheduler every 5min
- **Inputs**: Active strategies (paper_running), opportunities, universe features, PI decisions
- **LLM**: No (pure rule evaluation)
- **Outputs**: trade_proposals, signal_pending PiDecisions
- **Side effects**: Creates proposals; writes signal_pending decisions for PI Agent consumption
- **Key invariant**: Never calls LLM; never executes orders directly

#### PI Agent (pi_trading_decision / pi_daemon)

- **Trigger**: Event-driven daemon (debounce 8s, cooldown 60s, heartbeat 5min)
- **Inputs**: 
  - Layer 1: market context (opportunities, factor_feedback, regime, positions)
  - Layer 2: strategy context (ranker candidates, pending signals, universe features)
  - Layer 3: reflection (previous decisions, outcomes, Serenity research)
- **LLM**: Yes (structured decision with Bull/Bear debate)
- **Outputs**: PiDecision (action, symbol, conviction, reasoning JSON)
- **Side effects**: Writes to pi_decisions table; triggers paper_trader signal consumption
- **Actions**: enter/exit/hold/watch/scale_in/flip
- **Safety**: Never executes orders; outputs decisions that paper_trader/position_manager consume

#### position_manager

- **Trigger**: scheduler every 10min
- **Inputs**: Open positions, current prices, strategy exit rules, PI exit decisions
- **LLM**: No
- **Outputs**: Position closes (status=closed, realized_pnl computed)
- **Side effects**: Calls close_position(); updates exchange TP/SL orders; writes audit events
- **Exit triggers**: stop_loss, take_profit, max_hold_time, trailing_stop, PI exit decision

---

### C. Strategy Agents

#### strategy_designer

- **Trigger**: 3x daily (03:00, 08:00, 14:00 UTC) + event-driven from post_trade_reviewer
- **Inputs**: knowledge_items, market_regimes, closed positions performance, research notes
- **LLM**: Yes (strategy generation)
- **Outputs**: strategy_records (new strategies with parameters JSON, entry_conditions)
- **Side effects**: Writes strategies; notifies PI daemon; promotes to paper_candidate only
- **Key invariant**: Cannot promote beyond paper_candidate (requires walk-forward)

#### backtest_orchestrator

- **Trigger**: scheduler every 4h
- **Inputs**: Strategies needing validation, historical OHLCV data
- **LLM**: No (mechanical backtesting)
- **Outputs**: backtest_runs, backtest_trades
- **Side effects**: Updates strategy promotion gates based on results

---

### D. Learning Agents

#### post_trade_reviewer

- **Trigger**: scheduler every 2h
- **Inputs**: Recently closed positions (no existing review), market context at entry/exit
- **LLM**: Yes (analysis of trade quality)
- **Outputs**: knowledge_items (item_type=post_trade_review)
- **Side effects**: Writes reviews; may trigger strategy_designer event

#### risk_critic

- **Trigger**: scheduler every 1h
- **Inputs**: Open positions, risk config, recent violations, portfolio exposure
- **LLM**: Yes (risk assessment)
- **Outputs**: knowledge_items (item_type=risk_lesson), risk flag annotations on opportunities
- **Side effects**: May flag opportunities as risk_blocked; writes risk_decisions

#### dreaming_daily

- **Trigger**: daily 02:00 UTC
- **Inputs**: All knowledge_items from past 24h, closed positions, agent run summaries
- **LLM**: Yes (synthesis)
- **Outputs**: knowledge_items (item_type=dreaming_log)
- **Side effects**: Produces high-confidence lessons that influence future FTS5 retrieval

#### insight_synthesizer

- **Trigger**: daily 04:00 UTC
- **Inputs**: knowledge_items, opportunities, positions, strategy performance
- **LLM**: Yes
- **Outputs**: Synthesized insights; reasoning_feedback piggyback (per-bucket statistics)
- **Side effects**: Writes insight items; updates reasoning feedback cache

#### confidence_calibration

- **Trigger**: every 6h
- **Inputs**: PI decisions with conviction scores + actual outcomes (position P&L)
- **LLM**: No (pure statistical computation)
- **Outputs**: Calibration scores (ECE by bucket), grade (excellent/good/fair/poor)
- **Side effects**: Updates calibration cache; exposed via /analytics/confidence-calibration

---

### E. Operator Agent

#### pi_operator_loop

- **Trigger**: scheduler every 1h
- **Inputs**: Portfolio state, strategy performance, market regime, system health
- **LLM**: Yes (assessment + recommendations)
- **Outputs**: Operator recommendations (Level 1: assess-only, Level 2: propose, Level 3: with-confirmation)
- **Side effects**: 
  - Level 1: strategy_research trigger (daily limit 1)
  - Level 2: strategy retire/demote recommendations (not direct execution)
  - Level 3: risk policy proposals (require human approval)
- **Key invariant**: Never executes changes directly; all actions go through proposal flow

---

## 3. Cross-Cutting Concerns

### SQLite Concurrency

All agents share the same SQLite DB. Invariants that MUST hold:
1. WAL mode + `busy_timeout=8000ms`
2. One connection per thread via `threading.local`
3. `was_in_tx = db.in_transaction` captured BEFORE writes; commit only `if not was_in_tx`
4. Scheduler `max_workers=3` (do not raise)

### Error Handling

- Agents catch LLM failures and degrade gracefully (produce partial output or skip)
- Scheduler `_run_job_guarded()` wraps every job: rolls back leaked transactions, logs errors
- Consecutive failure threshold triggers infra events (visible in dashboard)
- Agent timeout: 300s default (configurable per-job)

### Permission Modes

- **Read Only**: no agent can execute trades or modify state
- **Operator**: can pause/resume, trigger manual runs
- **Full**: can approve proposals, unlock live

### LLM Provider Resolution

All agents use `llm_complete(system, user)` from `aletheia/llm/`. Provider is resolved from config priority:
1. Agent-specific override (if configured)
2. Default provider from settings
3. Fallback chain (local Ollama -> cloud providers)

---

## 4. Data Flow Summary

```
             market_data (5min)
                   |
                   v
        +-------------------+
        | market_snapshots  |
        +-------------------+
                   |
                   v
         market_observer (15min)
                   |
                   v
        +-------------------+
        |   opportunities   |------> risk_critic (1h)
        +-------------------+              |
                   |                       v
                   v               risk_decisions
         paper_trader (5min)              |
         (signal collection)              |
                   |                      |
                   v                      v
        +-------------------+    +------------------+
        | signal_pending    |--->| PI Agent (daemon)|
        +-------------------+    +------------------+
                                          |
                                          v
                                  +---------------+
                                  | pi_decisions  |
                                  +---------------+
                                          |
                          +---------------+---------------+
                          v                               v
                  trade_proposals              position_manager (10min)
                          |                               |
                          v                               v
                  risk engine                    close positions
                          |                               |
                          v                               v
                  orders -> fills            portfolio_snapshots
                          |                               |
                          v                               v
                     positions             post_trade_reviewer (2h)
                                                          |
                                                          v
                                                  knowledge_items
                                                          |
                                                          v
                                              dreaming_daily (02:00)
                                                          |
                                                          v
                                              strategy_designer (3x/day)
```
