# Agent Contracts

> Input/output contracts for all 12 registered agent functions and 32 scheduler jobs.
> Each agent has defined triggers, data inputs, outputs, and side effects.
> Source of truth: `aletheia/agent_runtime/scheduler.py` + `aletheia/agents/*.py`
> Last updated: 2026-06-23 (频率全量修正 + 5 新 agent 补充)

---

## 1. Scheduler Job Overview (30 jobs)

| # | Job ID | Interval | Executor | Description |
|---|--------|----------|----------|-------------|
| 1 | market_data | 5min | io | Collect market snapshots (price, volume, OHLCV) |
| 2 | market_observer | 15min | observer | Scan for trading opportunities |
| 3 | equity_observer | cron 工作日 14-21UTC /30min | agents | xStocks/equity market scan (replaces stock_observer) |
| 4 | prediction_observer | 30min | agents | Prediction market scan |
| 5 | paper_trader | 5min | agents | Execute paper trades from pending signals |
| 6 | pi_trading_decision | ~15min (configurable) | agents | PI Agent heartbeat (daemon fallback) |
| 7 | position_manager | 5min | io | Manage open positions (trailing stop, exit) |
| 8 | tpsl_fill_check | 90s | io | Check exchange TP/SL order fills |
| 9 | position_reconcile | 5min | io | Reconcile local vs exchange positions |
| 10 | post_trade_reviewer | 4h | agents | Review closed trades for lessons |
| 11 | risk_critic | 6h | agents | Evaluate risk exposure, flag violations |
| 12 | dreaming_daily | cron 02:00 CST | agents | Synthesize daily knowledge summary |
| 13 | strategy_designer_daily | cron 01:00 CST | agents | Generate new strategy proposals |
| 14 | strategy_designer_eu | cron 09:00 CST | agents | EU session strategy generation |
| 15 | strategy_designer_us | cron 17:00 CST | agents | US session strategy generation |
| 16 | insight_synthesizer | cron 02:30 CST | agents | Cross-agent insight synthesis |
| 17 | confidence_calibration | 6h | io | Compute ECE calibration metrics |
| 18 | pi_operator_loop | cron 每4h:15 CST | agents | High-level operator assessment loop |
| 19 | health_check | 10min | io | Doctor health probe |
| 20 | cleanup | cron 03:17 CST | io | Data retention cleanup |
| 21 | exchange_sync | 5min | io | Refresh exchange account cache |
| 22 | portfolio_snapshot | 10min | default | Store portfolio equity snapshot |
| 23 | reconciliation | 2min (live only) | io | Full state reconciliation |
| 24 | price_feed | 30s | fast_io | In-memory price cache refresh |
| 25 | daily_summary | cron 08:00 CST (configurable) | io | Generate daily performance summary |
| 26 | data_collectors | 2min | io | Funding rate, OI, liquidation, orderbook |
| 27 | okx_demo_symbols | 4h | io | Refresh OKX demo symbol list |
| 28 | backtest_orchestrator | cron 04:00 CST | agents | Run pending backtests |
| 29 | universe_refresh | 10min | io | Universe composition refresh |
| 30 | ohlcv_nightly_backfill | cron (configurable) CST | io | Historical OHLCV gap fill |

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

#### equity_observer (replaces stock_observer)

- **Trigger**: cron 工作日 14:00-21:00 UTC, every 30min
- **Inputs**: yfinance daily bars + Bitget xStocks real-time price cross-validation
- **LLM**: Yes
- **Outputs**: opportunities table (equity-linked symbols)
- **Side effects**: Same as market_observer but for equity-linked tokens
- **Condition**: config.market_data.observed_equities non-empty

#### stock_observer (removed 2026-06-17 — superseded by equity_observer)

#### prediction_observer

- **Trigger**: scheduler every 30min
- **Inputs**: prediction market APIs (Polymarket/Kalshi), existing prediction_markets table
- **LLM**: No (structured data parsing)
- **Outputs**: prediction_runs, prediction_markets, prediction_probability_snapshots
- **Side effects**: Chains divergence_scanner on completion

#### divergence_scanner (chained from prediction_observer)

- **Trigger**: runs after prediction_observer completes
- **Inputs**: prediction market prices vs spot prices
- **LLM**: No
- **Outputs**: divergence alerts → opportunities table
- **Side effects**: None

#### paper_trader_ranker (internal to paper_trader)

- **Trigger**: called within paper_trader job
- **Inputs**: opportunities + universe_scores + trading_guards + PiDecision
- **LLM**: No (deterministic ranking, 8-dimension scoring)
- **Outputs**: ranked + filtered opportunity list → PI daemon signal
- **Side effects**: None
- **Contract**: Must NEVER call LLM directly

#### research_agent / research_worker

- **Trigger**: enqueued by dreaming_agent or manual
- **Inputs**: topic query + knowledge context
- **LLM**: Yes (research_worker makes LLM calls)
- **Outputs**: knowledge_items(research_note), serenity_profile_snapshots
- **Side effects**: FTS index update

#### portfolio_agent

- **Trigger**: manual (not scheduled)
- **Inputs**: current positions + portfolio metrics
- **LLM**: Yes
- **Outputs**: portfolio rebalancing recommendations
- **Side effects**: None (advisory only)

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
  - Layer 3: reflection (previous decisions, outcomes, Serenity research). Each
    Serenity opportunity surfaces `id`/`score`/`bucket`/`evidence_count`/`key_evidence`
    + the independently `assessed_tier` (D2), annotated as long-horizon analyst input —
    NOT a same-day setup and NOT to be rubber-stamped (PI remains sole decision maker).
- **LLM**: Yes (structured decision with Bull/Bear debate)
- **Outputs**: PiDecision (action, symbol, conviction, reasoning JSON, opportunity_id, signal_source)
- **Side effects**: Writes to pi_decisions table; triggers paper_trader signal consumption
- **Attribution**: `_attach_opportunity_context` backfills `opportunity_id` + `signal_source`
  (`market_observer` Layer 1 wins on tie; `serenity` Layer 3 is the fallback). `signal_source`
  disambiguates the two bare-uuid id spaces. Phase 2 of Serenity Option A.
- **Actions**: enter/exit/hold/watch/scale_in/flip
- **Safety**: Never executes orders; outputs decisions that paper_trader/position_manager consume

#### position_manager

- **Trigger**: scheduler every 5min
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

- **Trigger**: scheduler every 4h
- **Inputs**: Recently closed positions (no existing review), market context at entry/exit
- **LLM**: Yes (analysis of trade quality)
- **Outputs**: knowledge_items (item_type=post_trade_review)
- **Side effects**: Writes reviews; may trigger strategy_designer event

#### risk_critic

- **Trigger**: scheduler every 6h
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

- **Trigger**: daily 02:30 CST (= 18:30 UTC previous day)
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

All agents share the same SQLite DB. Invariants that MUST hold (full detail in [data_model §1, §9](data_model.md)):
1. WAL mode + `busy_timeout=30000ms` (30s) — `storage/sqlite.py:43`
2. One connection per thread via `threading.local`
3. `was_in_tx = db.in_transaction` captured BEFORE writes; commit only `if not was_in_tx`
4. Scheduler `max_workers=3` (do not raise)

### Job Timeouts (hard wall-clock bound, per-job — `scheduler.py:25-50`)

> Not a single 300s default — each job has its own cap. Timeout = listed value + 15s grace.

| Job | Timeout | Job | Timeout |
|-----|---------|-----|---------|
| market_data | 90s | dreaming_agent | 300s |
| market_observer | 240s | strategy_designer | 300s |
| paper_trader | 300s | pi_trading_decision | 300s |
| post_trade_reviewer | 300s | exchange_sync | 60s |

Async jobs use `_run_async_with_timeout` (`scheduler.py:204`), sync jobs `_run_sync_with_timeout` (`:141`). `misfire_grace_time` 60-300s per `add_job`.

### Error Handling & Failure Isolation

- **Per-job degradation**: each job wrapped in catch-all; one failure logs a warning and does NOT cascade (`scheduler.py:708-793`). Agents coupled only through shared SQLite tables (blackboard), never direct calls — so an upstream failure cannot crash a downstream agent.
- **Leaked-tx cleanup**: scheduler rolls back any leaked transaction before/after each job.
- **Consecutive-failure alert**: ≥3 consecutive failures → `infra_event` (dashboard-visible), `_record_job_failure` (`scheduler.py:67-79`). Non-cascading.
- **Stale-input expiry**: if paper_trader stalls, its opportunities expire after 48h rather than blocking reviewers (`repositories.py:1632`).

### LLM Fallback (request-level, unified — `model_provider/fallback.py:215-299`)

- `complete_with_fallback()`: on **transient** errors (429/502/503/504, rate-limit — `is_transient_model_error:64`), tries next candidate model (primary → config `fallback_models` → discovered).
- **Cooldown**: a model with ≥2 consecutive failures is benched 300s (`_record_model_failure:116`).
- Non-transient errors or last-candidate failure → raised. Each agent then catches and degrades (e.g. market_observer → technical-only signals).

### Permission Modes (two surfaces — see [live_unlock_contract §5](live_unlock_contract.md))

> ⚠️ Corrected: not a flat Read Only/Operator/Full. There are TWO ranked surfaces:
- **Worker** (Agent Shell): `Observe < Paper < Full` (default `Paper`)
- **Operator** (PI/Dashboard): `ReadOnly < Research < PaperAuto < AllowEditing < Auto` (default `ReadOnly`)

`FORBIDDEN_INTENTS` (enable_live / bypass_risk / change_risk_limits / ...) are uncallable in ANY mode — `agent_shell/registry.py:415`.

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
        |   opportunities   |------> risk_critic (6h)
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
                  trade_proposals              position_manager (5min)
                          |                               |
                          v                               v
                  risk engine                    close positions
                          |                               |
                          v                               v
                  orders -> fills            portfolio_snapshots
                          |                               |
                          v                               v
                     positions             post_trade_reviewer (4h)
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
