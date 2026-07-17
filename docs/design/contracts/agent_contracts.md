# Agent Contracts

> Input/output contracts for all registered agent functions and scheduler jobs.
> Each agent has defined triggers, data inputs, outputs, and side effects.
> Source of truth: `aletheia/agent_runtime/scheduler.py` + `aletheia/agents/*.py`
> Last updated: 2026-07-13 (durable Outcome hook replay)

---

## 1. Scheduler Job Overview (38 recurring jobs with optional jobs enabled + 3 startup one-shots)

| # | Job ID | Interval | Executor | Description |
|---|--------|----------|----------|-------------|
| 1 | market_data | 5min | io | Collect market snapshots (price, volume, OHLCV) |
| 2 | market_observer | 15min | observer | Scan for trading opportunities |
| 3 | equity_observer | cron 工作日 14-21UTC /30min | agents | xStocks/equity market scan (replaces stock_observer) |
| 4 | prediction_observer | 30min | agents | Prediction market scan |
| 5 | paper_trader | 3min + on-demand | agents | Collect signals → signal_pending PiDecisions (System Operator submits proposals — Phase 4a). Cadence tightened from 5min so momentum (30min TTL) and mean_reversion (60min TTL) carriers get enough review passes before the family window elapses. Additionally `scheduler.notify_paper_trader(source)` pulls the next fire time forward to now — called by `UniverseService.refresh` after a new snapshot is published or after change-suppression's TTL slide makes previously-expired opportunities eligible. Debounced 3s; APScheduler `max_instances=1` handles overlap. |
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
| 24 | data_collectors | 2min | io | 主交易所 orderbook、Funding rate、OI（串行 CCXT） |
| 24a | data_collector_context | 10min | io | volume ranking、Hyperliquid、DefiLlama、CoinGecko、macro、Coinalyze、稀疏 derivatives history；独立 40s 时间盒 |
| 25 | okx_demo_symbols | 4h | io | Refresh OKX demo symbol list |
| 26 | guard_promotion | cron 04:00 CST | io | Promote candidate trading guards to active (learning; re-homed from deleted backtest_orchestrator, Position C) |
| 27 | universe_refresh | 10min | io | Universe composition refresh |
| 28 | ohlcv_nightly_backfill | cron (configurable) CST | io | Historical OHLCV gap fill |
| 29 | ohlcv_active_backfill | interval `historical_data.active_universe_backfill_interval_minutes` (optional) | io | Bounded exact-instrument repair for active members below the OHLCV minimum; durable backoff |
| 30 | filter_controller_observe | interval `controller.observe_interval_hours` (optional) | io | Shadow-only pass/reject evidence report; never changes config |
| 31 | persona_rollout | 8h | io | Advance dynamic-sizing rollout ladder one health-gated step (no-op unless persona_auto_advance_enabled) |
| 32 | connectivity_monitor | interval `health.exchange_probe_interval_sec` | fast_io | Probe exchange connectivity; auto-pause on outage; auto-resume when recovered |
| 33 | shadow_resolver | 6h | io | Resolve shadow trades against realised outcomes |
| 34 | outcome_hook_replay | 1min | io | Replay durable eligible-close feedback jobs; revalidate the closed Position and retry incomplete shadow/Serenity delivery |
| 35 | news_fetch | interval `news.fetch_interval_minutes` | io | Poll news providers into `news_items` |
| 36 | outcome_settler | 10min | io | Settle outstanding prediction / universe forward outcomes |
| 37 | learning_contribution_evaluator | interval `learning.evaluation_interval_minutes` (default 60) | io | Measure only: append deterministic contribution verdicts and evaluator health even when autonomous Learning is off or runtime is paused |
| 38 | learning_lifecycle_controller | interval `learning.evaluation_interval_minutes` (default 60) | io | Apply at most one default-closed paper lifecycle action per current experiment; requires autonomous Learning, no pause/integrity incident, lifecycle automation, paper influence, and path allowlist |
| 39 | serenity_startup_catchup | once, 4min after gateway boot | agents | Recover a missed 02:00 CST Serenity batch using a durable per-day key; skips fresh/active/already-attempted batches |
| 40 | learning_startup_catchup | once, 5min after gateway boot | agents | Run only the latest missed dreaming/memo/reviewer date-window; a durable success watermark prevents restart duplicates |
| 41 | learning_measurement_startup_catchup | once, 3min after gateway boot | io | Re-run only the current missing contribution-measurement window; independent of autonomous Learning and paper influence |

Learning behavior jobs (rows 10–15, 26, 30–31, 38 and the Learning behavior catch-up) share
`agent.autonomous_learning` as one backend gate. When disabled, no automatic producer,
reviewer, promoter, controller, or learned rollout runs. Measurement-only
`outcome_settler`, `outcome_hook_replay`, `shadow_resolver`, and
`learning_contribution_evaluator` continue so disabling
Learning does not erase or delay outcome truth. The evaluator never changes stages or authority;
the lifecycle controller is a separate gated consumer. Successful/failed/skipped runs and reviewer
backlog health are persisted in `learning_job_health`.

### OS Session-service Contract

- `gateway start` creates an on-demand user service on the current platform:
  launchd on macOS, a systemd user transient service on Linux, or an on-demand
  Task Scheduler task on Windows.
- The service runs the foreground gateway runner and has no login trigger,
  interval, `KeepAlive`, or `Restart` policy. It neither bypasses live, risk,
  permission, nor credential gates.
- `gateway stop` stops and removes that service. `status()` queries the live OS
  service manager and distinguishes an absent registration from an unreadable
  manager state.
- Git operations never start or restart the gateway. Applying new checkout code
  requires an explicit owner-controlled `gateway restart`; legacy
  Aletheia-managed auto-restart hooks are removed without touching foreign hooks.

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
- **Learning attribution**: advisory retrieval + memo + per-symbol learned guard context is one
  `observer_advisory_bundle` manifest stored in the causal Opportunity metadata. The bundle is
  rendered only for an exact `observer_advisory_bundle` treatment assignment whose path is
  allowlisted and currently authoritative; control, disabled/default, stale authority, missing
  candidate identity, or adapter error renders no learned bundle. Universe members use a stable
  member candidate and fixed-horizon outcome identity; standalone reads stay unlinked when no true
  outcome producer exists. Market and prediction context is non-Learning input and remains present.
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
- **TTL soft filter** (`paper_trader_ranker._ttl_soft_penalty_factor`): a carrier
  past its `valid_until` is no longer hard-rejected. Its `trade_score` is
  multiplied by `0.5 ** (expired_sec / family_ttl_sec)` — half-life = family
  TTL — so an aged signal ranks lower without disappearing. Only past
  `_TTL_SOFT_HARD_CAP_MULT × ttl_sec` (3× family TTL) does the ranker hard-reject
  with `skip_reason=ttl_expired` so `paper_trader._settle_permanent_skips` can
  clean it up. `filter_trace` records `passed_with_penalty` /
  `opportunity_ttl_soft_penalty` / `universe_member_ttl_soft_penalty` reason
  codes for the soft branch. Family TTL lookup: `allocator._FAMILY_TTL`.

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

- **Trigger**: scheduler every 3min + on-demand nudge via `scheduler.notify_paper_trader(source)` from producers (universe refresh, TTL slide). Debounced 3s. APScheduler `max_instances=1` handles overlap so a nudge during an in-flight run is a no-op. Escalation path to a wider notify surface (Tier 2) or a full event-driven daemon (Tier 3) is documented in `docs/plans/trading-pipeline/ranker_on_demand_upgrade_plan_2026-07-17.md` — do not upgrade without meeting the observation gates listed there.
- **Inputs**: opportunities, universe features, System Operator decisions
- **LLM**: No (pure rule evaluation)
- **Outputs**: signal_pending PiDecisions (Phase 4a: System Operator is the **sole** proposal submitter — paper_trader no longer creates proposals directly)
- **Side effects**: Writes signal_pending decisions for System Operator consumption. RuleSet exit auto-close was removed with the rules engine; paper_trader must not close positions directly.
- **Key invariant**: Never calls LLM; never executes orders directly; never submits proposals (signal collector only)
- **Learning authority**: factor history and raw case/memo scores are diagnostic proposals only. Each ranker component has a separate candidate-linked exposure; score/filter influence stays at the static baseline unless a durable prospective treatment/control verdict has enough linked net outcomes. Control candidates retain the same venue, eligibility, static-risk-flag, learned-guard, and downstream Risk Engine checks.

#### System Operator — Trading Decision Daemon (pi_trading_decision / pi_daemon)

- **Trigger**: Event-driven daemon (debounce 8s, cooldown 60s, heartbeat 5min)
- **Inputs**: 
  - Layer 1: market context (opportunities, factor_feedback, regime, positions), plus exact-instrument candidate technical snapshots (15m/1h/4h) and freshness-qualified Hyperliquid funding/OI evidence
  - Layer 2: strategy context (ranker candidates, pending signals, universe features)
  - Layer 3: reflection (previous decisions, outcomes, Serenity research). Each
    Serenity opportunity surfaces `id`/`score`/`bucket`/`evidence_count`/`key_evidence`
    + the independently `assessed_tier` (D2), annotated as long-horizon analyst input —
    NOT a same-day setup and NOT to be rubber-stamped (PI remains sole decision maker).
- **LLM**: Yes (structured decision with Bull/Bear debate)
- **Outputs**: an exact-candidate PiDecision (action, symbol, conviction, reasoning JSON, opportunity_id, signal_source) with `evidence_manifest_json` naming the actual technical snapshot, Hyperliquid rows, and Position X availability; a zero-pending wake instead writes one non-trading `operator_evaluation_event` and does not call the LLM
- **Side effects**: Candidate path writes `pi_decisions`; zero-pending path appends only an observation event; neither path bypasses paper_trader queue authority
- **Attribution**: `_attach_opportunity_context` backfills `opportunity_id` + `signal_source`
  (`market_observer` Layer 1 wins on tie; `serenity` Layer 3 is the fallback). `signal_source`
  disambiguates the two bare-uuid id spaces. Phase 2 of Serenity Option A.
- **Learning attribution**: reflection, memo, reasoning, calibration, trade cases, and structured
  lessons are one `operator_learning_bundle`. It is injected only for an exact allowlisted,
  authoritative treatment assignment; control/default/error leaves every learned component absent.
  The stable pending-queue candidate, assignment, hypothesis/experiment and exposure id are
  persisted on `pi_decisions.learning_manifest_json`. A single normal pending Opportunity links to
  its real fixed-horizon outcome; mixed/Serenity queues remain honestly unlinked. Opportunity and
  Proposal columns remain the causal anchors, and the observer bundle stays a separate upstream unit.
- **Actions**: active entry decision only `trade` / `skip`. The separate portfolio review exposes only `hold` / `close_one` for an explicit Position.
- **Unsupported legacy outputs**: `close` / `reduce` / `watch` / `scale_in` / `flip` on the entry surface are normalized to an evidenced `skip` and never execute. `reduce_exposure` on portfolio review is treated as `hold`, never as a silent full close.
- **Availability semantics**: missing assessment, a required debate side, a fresh exact local-paper mark, or complete entry reasons is retryable unavailability; no final PiDecision is written and no pending signal is consumed.
- **Position X authority**: owner aggregate imports and their 1h/4h/24h outcomes are observation-only. No automatic source is claimed; a minimum 14-day shadow window is required before human review and the evidence never changes Risk/Execution authority.
- **Safety**: Never executes orders directly. A complete `trade` prepares one immutable Proposal for the Gateway; `close_one` uses the central close router with an exact fresh mark.

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

All agents share the same SQLite DB. Invariants that MUST hold (full detail in [data_model §1, §9](../data/data_model.md)):
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
| learning_contribution_evaluator | 60s | learning_lifecycle_controller | 60s |

Async jobs use `_run_async_with_timeout` (`scheduler.py:204`), sync jobs `_run_sync_with_timeout` (`:141`). `misfire_grace_time` 60-300s per `add_job`.

### Error Handling & Failure Isolation

- **Per-job degradation**: each job wrapped in catch-all; one failure logs a warning and does NOT cascade (`scheduler.py:708-793`). Agents coupled only through shared SQLite tables (blackboard), never direct calls — so an upstream failure cannot crash a downstream agent.
- **Leaked-tx cleanup**: scheduler rolls back any leaked transaction before/after each job.
- **Consecutive-failure alert**: ≥3 consecutive failures → `infra_event` (dashboard-visible), `_record_job_failure` (`scheduler.py:67-79`). Non-cascading.
- **Stale-input expiry**: if paper_trader stalls, its opportunities expire after 48h rather than blocking reviewers (`repositories.py:1632`).

### LLM Fallback (request-level, unified — `model_provider/fallback.py:215-299`)

- Every provider completion is admitted by the process-wide Inference Broker before
  fallback execution. Admission is Provider-scoped and enforces bounded priority
  queues, concurrency, RPM/TPM and one total queue+execution deadline. Manual agent
  HTTP requests create a durable queued AgentRun and return `202 + job_id`; a
  persistent single worker owns execution outside the Uvicorn event loop.

- `complete_with_fallback()`: on **transient** errors (429/502/503/504, rate-limit, connect/network/remote-protocol/proxy transport failures), tries the next candidate model (primary → config `fallback_models` → discovered). Authentication and other permanent 4xx failures do not retry. Timeout exceptions remain terminal so five sequential 60-second model attempts cannot overrun bounded agent jobs.
- `agents.helpers.llm_structured()` 对非对象/不可解析 JSON 和推理预算耗尽的空输出最多尝试 3 次，输出预算依次为 `1x / 1.5x / 2x`；最后一次把上一轮坏输出带回修复提示。全部尝试共享 180 秒总墙钟预算（小于 Observer 200 秒循环预算和 240 秒任务上限），不继承 provider 面向长研究输出的 300 秒上限。`fallback_to_defaults` 默认关闭；只有非关键调用方显式开启时，失败才返回 schema 派生默认值，并在 error 字符串保留 `(fallback)` 标记。当前生产调用方均未开启该选项。
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
         paper_trader (3min)              |
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
