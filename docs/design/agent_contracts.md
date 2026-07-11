# Agent Contracts

> Input/output contracts for all registered agent functions and scheduler jobs.
> Each agent has defined triggers, data inputs, outputs, and side effects.
> Source of truth: `aletheia/agent_runtime/scheduler.py` + `aletheia/agents/*.py`
> Last updated: 2026-07-12 (Learning enable gate and durable catch-up)

---

## 1. Scheduler Job Overview (35 recurring jobs with optional jobs enabled + 2 startup one-shots)

| # | Job ID | Interval | Executor | Description |
|---|--------|----------|----------|-------------|
| 1 | market_data | 5min | io | Collect market snapshots (price, volume, OHLCV) |
| 2 | market_observer | 15min | observer | Scan for trading opportunities |
| 3 | equity_observer | cron 工作日 14-21UTC /30min | agents | xStocks/equity market scan (replaces stock_observer) |
| 4 | prediction_observer | 30min | agents | Prediction market scan |
| 5 | paper_trader | 5min | agents | Collect signals → signal_pending PiDecisions (System Operator submits proposals — Phase 4a) |
| 6 | pi_trading_decision | ~15min (configurable) | agents | System Operator heartbeat (daemon fallback) |
| 7 | position_manager | 5min | io | Manage open positions (trailing stop, exit) |
| 8 | tpsl_fill_check | 90s | io | Check exchange TP/SL order fills |
| 9 | position_reconcile | 5min | io | Reconcile local vs exchange positions |
| 10 | post_trade_reviewer | 4h | agents | Review closed trades for lessons |
| 11 | risk_critic | 6h | agents | Evaluate risk exposure, flag violations |
| 12 | dreaming_daily | cron 02:00 CST | agents | Synthesize daily knowledge summary |
| 13 | lesson_reviewer | cron 03:00 CST | agents | Overturn/renew accepted lessons after outcome review |
| 14 | insight_synthesizer | cron 02:30 CST | agents | Cross-agent insight synthesis |
| 15 | confidence_calibration | 6h | io | Compute ECE calibration metrics |
| 16 | pi_operator_loop | cron 每4h:15 CST + boot 2min catch-up | agents | High-level operator assessment loop; persisted assessment freshness prevents restart duplicates |
| 17 | health_check | 10min | io | Doctor health probe |
| 18 | cleanup | cron 03:17 CST | io | Data retention cleanup |
| 19 | exchange_sync | 5min | io | Refresh exchange account cache |
| 20 | portfolio_snapshot | 10min | default | Store portfolio equity snapshot |
| 21 | reconciliation | 2min (live only) | io | Full state reconciliation |
| 22 | price_feed | 30s | fast_io | Exact-instrument (venue + spot/perp) in-memory price cache refresh |
| 23 | daily_summary | cron 08:00 CST (configurable) | io | Generate daily performance summary |
| 24 | data_collectors | 2min | io | Funding rate, OI, liquidation, orderbook, exact-instrument volume ranking |
| 25 | okx_demo_symbols | 4h | io | Refresh OKX demo symbol list |
| 26 | guard_promotion | cron 04:00 CST | io | Promote candidate trading guards to active (learning; re-homed from deleted backtest_orchestrator, Position C) |
| 27 | universe_refresh | 10min | io | Universe composition refresh |
| 28 | ohlcv_nightly_backfill | cron (configurable) CST | io | Historical OHLCV gap fill |
| 29 | ohlcv_active_backfill | interval `historical_data.active_universe_backfill_interval_minutes` (optional) | io | Bounded exact-instrument repair for active members below the OHLCV minimum; durable backoff |
| 30 | filter_controller_observe | interval `controller.observe_interval_hours` (optional) | io | Shadow-only pass/reject evidence report; never changes config |
| 31 | persona_rollout | 8h | io | Advance dynamic-sizing rollout ladder one health-gated step (no-op unless persona_auto_advance_enabled) |
| 32 | connectivity_monitor | interval `health.exchange_probe_interval_sec` | fast_io | Probe exchange connectivity; auto-pause on outage; auto-resume when recovered |
| 33 | shadow_resolver | 6h | io | Resolve shadow trades against realised outcomes |
| 34 | news_fetch | interval `news.fetch_interval_minutes` | io | Poll news providers into `news_items` |
| 35 | outcome_settler | 15min | io | Settle outstanding prediction / universe forward outcomes |
| 36 | serenity_startup_catchup | once, 4min after gateway boot | agents | Recover a missed 02:00 CST Serenity batch using a durable per-day key; skips fresh/active/already-attempted batches |
| 37 | learning_startup_catchup | once, 5min after gateway boot | agents | Run only the latest missed dreaming/memo/reviewer date-window; a durable success watermark prevents restart duplicates |

Learning mutation jobs (rows 10–15, 26, 30–31 and the Learning catch-up) share
`agent.autonomous_learning` as one backend gate. When disabled, no automatic producer,
reviewer, promoter, controller, or learned rollout runs. Measurement-only
`outcome_settler` and `shadow_resolver` continue so disabling Learning does not erase
or delay outcome truth. Successful/failed/skipped runs and reviewer backlog health are
persisted in `learning_job_health`.

### OS Watchdog Contract

- Windows: per-user Task Scheduler entry, logon trigger plus 1-minute repetition.
- macOS: per-user LaunchAgent `com.aletheia.trading-os.gateway`, `RunAtLoad` plus 60-second `StartInterval`.
- Both invoke the same idempotent `gateway start --no-browser`; neither bypasses live, risk, permission, or credential gates.
- The macOS plist uses an absolute venv interpreter and project working directory, stores no environment variables/secrets, and uses `AbandonProcessGroup` so the detached supervisor survives the short watchdog command.
- `status()` queries the live OS service manager. A plist file without a loaded job, a loaded job with stale paths, and an unreadable state are distinct results.

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
- **Prompt contract** (2026-07-01, spec UP-08): the per-symbol LLM prompt names the `scanner_family` that surfaced the symbol so the read VALIDATES that family rather than doing a generic scan (MO-4).
- **Guard advisory contract** (spec UP-07, 断口F): guard advisory is built PER-SYMBOL inside `_analyze_symbol` with `{symbol, scanner_family, asset_class}` via `router.build_guard_advisory()` — NOT run-level with a symbol list (which never matched any guard). The run-level memory_block carries only symbol-agnostic lessons.
- **Downstream contract** (spec UP-06): `key_observations` (the LLM's numeric citations) IS surfaced to the System Operator's layer1 snapshot; previously only a truncated thesis reached the decision.
- **Bounded-loop fairness contract** (spec GS-S03): the 200-second safety budget remains below the scheduler timeout, but the next cycle begins at the durable successor of the last completed exact instrument. A cancellation or budget break never skips an in-flight or unprocessed instrument.
- **Pre-filter status contract** (spec OBS-PRE-FILTER-STATUS): `no_sufficient_signal` is a normal rejection recorded in filter evidence, not an agent execution error. It skips the LLM without changing the run to `completed_with_errors`; provider failures, unexpected exceptions, and loop-budget exhaustion remain errors.

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
- **Inputs**: opportunities, universe features, System Operator decisions
- **LLM**: No (pure rule evaluation)
- **Outputs**: signal_pending PiDecisions (Phase 4a: System Operator is the **sole** proposal submitter — paper_trader no longer creates proposals directly)
- **Side effects**: Writes signal_pending decisions for System Operator consumption. RuleSet exit auto-close was removed with the rules engine; paper_trader must not close positions directly.
- **Key invariant**: Never calls LLM; never executes orders directly; never submits proposals (signal collector only)

#### System Operator — Trading Decision Daemon (pi_trading_decision / pi_daemon)

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
- **Inputs**: Open positions, current prices, market-aware exit signals
- **LLM**: No
- **Outputs**: Position closes (status=closed, realized_pnl computed)
- **Side effects**: Calls close_position(); updates exchange TP/SL orders; writes audit events
- **Exit triggers**: stop_loss, take_profit, max_hold_time, trailing_stop, market_aware_exit. RuleSet exit auto-close is removed. System Operator exits run **independently** via `review_portfolio_state`, NOT inside this loop — see `execution_contract.md §10`.
- **Pause semantics**: runs even when `runtime.paused` (manages existing risk; never blocks protective close — aligned with kill_switch, see `execution_contract.md §10.3`)

---

### C. Strategy Agents — REMOVED (Position C, 2026-07-02)

The rules engine (`strategy_designer` + `backtest_orchestrator`) was **deleted**.
Strategy generation / DSL / backtesting no longer exist; the System Operator
(`evaluate_trading_opportunity`) is the sole decision path. Legacy
`strategy_records` / `backtest_runs` / `backtest_trades` tables are kept read-only
for historical attribution (not written to). The deterministic `guard_promotion`
job (candidate trading guards → active; a learning task) was re-homed from the
deleted daily backtest cycle and now runs standalone (see scheduler_contract §job table).

---

### D. Learning Agents

#### post_trade_reviewer

- **Trigger**: scheduler every 4h; disabled when `agent.autonomous_learning=false`
- **Inputs**: Recently closed positions (no existing review), market context at entry/exit
- **LLM**: Yes (analysis of trade quality)
- **Outputs**: knowledge_items (item_type=post_trade_review)
- **Side effects**: Writes reviews (the review→strategy_designer event chain was removed with the rules engine, Position C)

#### risk_critic

- **Trigger**: scheduler every 6h; disabled when `agent.autonomous_learning=false`
- **Inputs**: Open positions, risk config, recent violations, portfolio exposure
- **LLM**: Yes (risk assessment)
- **Outputs**: knowledge_items (item_type=**research_note**, metadata.subtype=risk_lesson — `risk_critic.py:42` calls `create_research_note()`; `risk_lesson` is not a top-level item_type, see `knowledge_contract.md §2.1`), risk flag annotations on opportunities
- **Side effects**: May flag opportunities as risk_blocked; writes risk_decisions

#### dreaming_daily

- **Trigger**: daily 02:00 CST; the shared Learning catch-up checks the latest missed lesson-extraction window 5 minutes after boot; `serenity_startup_catchup` separately repairs the research batch at 4 minutes
- **Inputs**: All knowledge_items from past 24h, closed positions, agent run summaries
- **LLM**: Yes (synthesis)
- **Outputs**: knowledge_items (item_type=dreaming_log), serenity research runs/themes/evidence/opportunities
- **Side effects**: The Serenity portion uses `serenity:scheduled:YYYY-MM-DD` idempotency and successful-run freshness. Restart-interrupted queued/running rows are marked failed before a catch-up is considered; lesson extraction uses `learning_job_health.input_watermark` to catch up one missed date-window without duplicate restart runs

#### insight_synthesizer

- **Trigger**: daily 02:30 CST (= 18:30 UTC previous day) + durable shared startup catch-up after 5min
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

- **Trigger**: cron every 4 hours at `:15` CST plus a startup attempt after 2 minutes
- **Deduplication**: scheduled runs require the latest persisted `operator_assessment` to be at least 3h45m old; missing/malformed history fails open because action permission and daily rate limits remain authoritative
- **Inputs**: Portfolio state, strategy performance, market regime, system health
- **LLM**: No direct model call; deterministic assessment may enqueue a separate Serenity research run
- **Outputs**: `operator_assessment`, bounded config actions/proposals and notifications
- **Side effects**: 
  - Level 1: bounded tightening/self-heal/notification actions and strategy research trigger under existing daily limits
  - Level 2: loosening and unknown actions become proposals; no direct execution
  - Level 3: live, credentials, permissions and hard-risk changes are forbidden, not confirmable by this loop
- **Key invariant**: Level 1 cannot expand risk; Level 2 requires a proposal path; Level 3 never executes

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
| market_observer | 240s | risk_critic | 300s |
| paper_trader | 300s | pi_trading_decision | 300s |
| post_trade_reviewer | 300s | exchange_sync | 60s |

Async jobs use `_run_async_with_timeout` (`scheduler.py:204`), sync jobs `_run_sync_with_timeout` (`:141`). `misfire_grace_time` 60-300s per `add_job`.

### Error Handling & Failure Isolation

- **Per-job degradation**: each job wrapped in catch-all; one failure logs a warning and does NOT cascade (`scheduler.py:708-793`). Agents coupled only through shared SQLite tables (blackboard), never direct calls — so an upstream failure cannot crash a downstream agent.
- **Leaked-tx cleanup**: scheduler rolls back any leaked transaction before/after each job.
- **Consecutive-failure alert**: ≥3 consecutive failures → `infra_event` (dashboard-visible), `_record_job_failure` (`scheduler.py:67-79`). Non-cascading.
- **Stale-input expiry**: if paper_trader stalls, its opportunities expire after 48h rather than blocking reviewers (`repositories.py:1632`).

### LLM Fallback (request-level, unified — `model_provider/fallback.py:215-299`)

- `complete_with_fallback()`: on **transient** errors (429/502/503/504, rate-limit, connect/network/remote-protocol/proxy transport failures), tries the next candidate model (primary → config `fallback_models` → discovered). Authentication and other permanent 4xx failures do not retry. Timeout exceptions remain terminal so five sequential 60-second model attempts cannot overrun bounded agent jobs.
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
        | signal_pending    |--->| System Operator  |
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
                          System Operator decision context (evaluate_trading_opportunity)
                          [strategy_designer removed — Position C, 2026-07-02]
```
