# Agent Contracts

> Input/output contracts for all registered agent functions and scheduler jobs.
> Each agent has defined triggers, data inputs, outputs, and side effects.
> Source of truth: `aletheia/agent_runtime/scheduler.py`, `aletheia/agents/*.py` and optional `extensions/research/*.py`
> Last updated: 2026-09-13 (D2 canonical entry; other domains unchanged)

---

## 1. Scheduler Job Overview

| # | Job ID | Interval | Executor | Description |
|---|--------|----------|----------|-------------|
| 1 | market_data | 5min | io | Collect market snapshots (price, volume, OHLCV) |
| 2 | market_observer | 10min | observer | Add advisory structured facts to existing triggered opportunities |
| 3 | equity_observer | cron 工作日 09:30-16:00 America/New_York /30min | agents | xStocks/equity market scan (replaces stock_observer) |
| 4 | prediction_observer | 30min | agents | Prediction market scan |
| 6 | pi_trading_decision | ~15min + committed Candidate wake | agents | Canonical Planner heartbeat (wakes daemon or runs the same Planner inline) |
| 7 | position_manager | 30s | io | Manage open positions (trailing stop, exit) |
| 7a | position_path_observer | 5min | io | Recover and materialize the oldest expected complete 5m Position bucket |
| 8 | tpsl_fill_check | 90s | io | Check exchange TP/SL order fills |
| 9 | position_reconcile | 5min | io | Reconcile local vs exchange positions |
| 10 | post_trade_reviewer | 4h | agents | Review closed trades for lessons |
| 11 | risk_critic | 6h | agents | Evaluate risk exposure and write an advisory research note |
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
| 24a | data_collector_context | 10min | io | volume ranking、Hyperliquid、DefiLlama、CoinGecko、macro、Coinalyze、稀疏 derivatives history；独立 55s 时间盒 |
| 25 | okx_demo_symbols | 4h | io | Refresh OKX demo symbol list |
| 26 | guard_promotion | cron 04:00 CST | io | Learned-guard TTL expiry only; persisted job identity retained, automatic promotion/deprecation by historical loss rate deleted |
| 27 | universe_refresh | 10min | io | Universe composition refresh |
| 28 | ohlcv_nightly_backfill | cron (configurable) CST | io | Historical OHLCV gap fill |
| 29 | ohlcv_active_backfill | interval `historical_data.active_universe_backfill_interval_minutes` (optional) | io | Bounded exact-instrument repair for active members below the OHLCV minimum; durable backoff |
| 30 | filter_controller_observe | interval `controller.observe_interval_hours` (optional) | io | Shadow-only pass/reject evidence report; never changes config |
| 31 | persona_rollout | 8h | io | Rechecks durable exact Persona/policy/step evidence; advances at most one parameter or refreezes, never changes unrelated config |
| 32 | connectivity_monitor | interval `health.exchange_probe_interval_sec` | fast_io | Probe exchange connectivity; auto-pause on outage; auto-resume when recovered |
| 33 | shadow_resolver | 6h | io | Resolve shadow trades against realised outcomes |
| 34 | outcome_hook_replay | 1min | io | Replay durable eligible-close feedback jobs; revalidate the closed Position and retry incomplete shadow/Serenity delivery |
| 35 | news_fetch | interval `news.fetch_interval_minutes` | io | Poll news providers into `news_items` |
| 35a | news_analyze | 5min | agents | Independently analyze at most 3 news rows; each item and the whole job are bounded, and failures remain retry/failed rather than fabricated analyzed facts |
| 36 | outcome_settler | 10min | io | Settle outstanding prediction / universe forward outcomes |
| 36b | broker_funding_settler | 30min | io | Recover verified exchange closes with separate bounded UTA-retention classification and broker-I/O budgets; rotate a durable keyset cursor and terminalize only typed retention expiry without inventing economics |
| 39 | serenity_startup_catchup | once, 4min after gateway boot | agents | Recover a missed 02:00 CST Serenity batch using a durable per-day key; skips fresh/active/already-attempted batches |
| 40 | learning_startup_catchup | once, 5min after gateway boot | agents | Run only the latest missed dreaming/memo/reviewer date-window; a durable success watermark prevents restart duplicates |
| 42a | funding_audit_reconciler | every 5min | audit | Retry at most 100 opt-in persisted funding audit JSONL mirrors by stable event id; never replay accounting and continue during layered pause |

Learning behavior producers remain gated by `agent.autonomous_learning`.
Outcome settlement, funding, durable outcome replay and the Observation Lab runner/bridge
retain their independent safety and measurement boundaries. The legacy contribution
evaluator, measurement catch-up and lifecycle controller/reconciler are retired.
Historical job-health rows are evidence only, never active schedules.
After recovery, `status` may be healthy and `last_error` clears, while `last_failed_at` and
`last_failure_reason` preserve the most recent failure.

The former local-simulator funding settler and its public-rate settlement path
are [archived](../history/local_simulated_risk_execution.md). They are not active
jobs. Current verified-close feedback is durably queued and independently
retried by `outcome_hook_replay`.

`broker_funding_settler` is exchange-accounting recovery, not trading authority. It runs during
layered pause and rotates a durable scheduling cursor so expired, transient, malformed, or
CAS-conflicting prefixes cannot starve later closes. Only the matching UTA profile's typed
`funding_history_retention_expired` result may write the terminal automatic-retry reason
`broker_funding_history_expired`; PnL/funding remain `NULL`, no cash or Outcome/Learning hook is
created, and credential/network/permission failures stay retryable.

`funding_audit_reconciler` is audit delivery maintenance, not an accounting or trading job. New
paper/broker funding settlements commit the authoritative SQLite audit with their economics and
enroll only that audit for JSONL delivery. The essential reconciler starts one minute after boot,
runs every five minutes on the isolated `audit` executor, and retries at most 100 oldest rows with a
15-second bounded worker. Failure degrades audit/job health but never changes accounting status,
replays cash/Position/hook facts, pauses scheduling, or changes infrastructure readiness.

### OS Session-service Contract

- `gateway start` creates an on-demand user service on the current platform:
  launchd on macOS, a systemd user transient service on Linux, or an on-demand
  Task Scheduler task on Windows (S4U first; InteractiveToken only after an
  S4U access denial).
- The service runs the foreground gateway runner and has no login trigger,
  interval, `KeepAlive`, or `Restart` policy. It neither bypasses live, risk,
  permission, nor credential gates.
- `gateway stop` stops and removes that service. `status()` queries the live OS
  service manager and distinguishes an absent registration from an unreadable
  manager state.
- Git operations never start or restart the gateway. Applying new checkout code
  requires an explicit owner-controlled `gateway restart`; legacy
  Aletheia-managed auto-restart hooks are removed without touching foreign hooks.

### OS Service-lifecycle Contract

- Installation, automatic enablement, and current runtime are independent facts.
  `gateway service status` reports each one and never treats an installed or
  enabled definition as proof that the Gateway is running.
- `gateway start|stop` changes runtime only. A persistent definition and its
  enabled state survive an explicit stop; the next documented login or boot
  trigger may start it again.
- `gateway enable|disable` changes automatic activation only. Enabling does not
  start the Gateway, and disabling does not stop it.
- `gateway service install|uninstall` is ownership-safe and idempotent. Uninstall
  refuses a running service and preserves runtime data, config, logs, and secrets.
- The default `session` backend is user-scoped. Windows uses a LeastPrivilege
  Task Scheduler task with S4U first and InteractiveToken only after explicit
  S4U access denial; macOS uses a user LaunchAgent; Linux uses user systemd.
- The optional `windows-service` backend is explicit, UAC-elevated, packaged-
  runtime-only, LocalService-scoped, and uses a dedicated ProgramData runtime.
  Aletheia never asks for or stores an administrator password and never copies
  the interactive user's credentials into the machine runtime.
- Service lifecycle commands remain unavailable to LLM-driven agent tools.

---

## 2. Agent Contracts (by function)

### A. Market Sensing Agents

#### market_observer

- **Trigger**: scheduler every 10min + event-driven wake
- **Inputs**: exact-instrument market snapshots, structured 1h RSI/ATR pre-filter facts,
  one `technical-context-v1` closed-bar bundle, freshness-qualified
  derivatives/macro/on-chain data, and successfully analyzed, relevance-qualified,
  exact-entity news
- **LLM**: Yes (analysis of market conditions)
- **Outputs**: namespaced `metadata.observer_assessment` on an existing open
  Opportunity; producer-owned direction, confidence, thesis, identity and expiry
  remain unchanged. Legacy `universe_scanner` Opportunity rows remain audit-only;
  setup-linked evidence never grants current entry authority.
- **Side effects**: Enriches only the exact snapshot/member/instrument carrier whose
  `signal_emitter` pass fact is durable. It does not create an Opportunity or gate the
  canonical Candidate admission. Historical emitter attestation authorizes only enrichment, not a new entry.
- **Error behavior**: Missing Universe state, missing/conflicting emitter evidence, or
  LLM unavailability skips enrichment; it never falls back to standalone signal creation
- **Prompt contract** (2026-07-01, spec UP-08): the per-symbol LLM prompt names the `scanner_family` that surfaced the symbol so the read VALIDATES that family rather than doing a generic scan (MO-4).
- **Technical context contract**: the model sees normalized EMA trend, RSI/momentum,
  ATR/Bollinger volatility, VWAP/Donchian location and volume/OBV participation.
  The Harness records `technical-context-v1` plus the exact artifact identity;
  degraded or insufficient-warmup timeframes render no partial indicator values.
- **Guard advisory contract** (spec UP-07, 断口F): guard advisory is built PER-SYMBOL inside `_analyze_symbol` with `{symbol, scanner_family, asset_class}` via `router.build_guard_advisory()` — NOT run-level with a symbol list (which never matched any guard). The run-level memory_block carries only symbol-agnostic lessons.
- **Learning attribution**: advisory retrieval + memo + per-symbol learned guard context is one
  `observer_advisory_bundle` manifest stored in the causal Opportunity metadata. The bundle is
  rendered only for an exact `observer_advisory_bundle` treatment assignment whose path is
  allowlisted and currently authoritative; control, disabled/default, stale authority, missing
  candidate identity, or adapter error renders no learned bundle. Universe members use a stable
  member candidate and fixed-horizon outcome identity. Market and prediction context is
  non-Learning input and remains present.
- **Downstream contract**: Observer rationale and observations remain advisory inside
  `metadata.observer_assessment`; they cannot establish execution eligibility or rewrite T0.
- **Bounded-loop fairness contract** (spec GS-S03): the 200-second safety budget remains below the scheduler timeout, but the next cycle begins at the durable successor of the last completed exact instrument. A cancellation or budget break never skips an in-flight or unprocessed instrument.
- **Pre-filter status contract** (spec OBS-PRE-FILTER-STATUS): `no_sufficient_signal` is a normal rejection recorded in filter evidence, not an agent execution error. It skips the LLM without changing the run to `completed_with_errors`; provider failures, unexpected exceptions, and loop-budget exhaustion remain errors.
- **Information provenance**: Opportunity metadata preserves the original T0 scanner
  identity and only the exact T1 source rows actually rendered to the Observer. It
  never infers historical news, macro, on-chain, prediction, Hyperliquid, or Position X
  evidence from whichever row is latest later.

#### equity_observer (replaces stock_observer)

- **Trigger**: cron 工作日 09:30-16:00 America/New_York, every 30min
- **Inputs**: yfinance daily bars + Bitget xStocks real-time price cross-validation
- **LLM**: Yes
- **Outputs**: opportunities table (equity-linked symbols)
- **Side effects**: Writes observation-only research evidence; does not wake Ranker or publish executable Candidate authority.
- **Condition**: config.market_data.observed_equities non-empty

#### stock_observer (removed 2026-06-17 — superseded by equity_observer)

The retired name is absent from the manual agent catalog and is rejected with
HTTP 403 by `POST /api/agent-runs/start`, including Operator service calls.
Use `equity_observer`; historical runs remain readable. Historical
`observed_stocks` config is decoded into the sole `observed_equities` workset
under the [configuration contract](configuration_contract.md); no runtime alias
or writable legacy control remains.

#### prediction_observer

- **Trigger**: scheduler every 30min
- **Inputs**: prediction market APIs (Polymarket/Kalshi), existing prediction_markets table
- **LLM**: Yes (structured event interpretation; the chained divergence scanner is deterministic)
- **Outputs**: prediction_runs, prediction_markets, prediction_probability_snapshots,
  and observation-only prediction Opportunities
- **Side effects**: Chains divergence_scanner on completion; research Opportunities do not wake a retired entry queue.

#### divergence_scanner (chained from prediction_observer)

- **Trigger**: runs after prediction_observer completes
- **Inputs**: prediction market prices vs spot prices
- **LLM**: No
- **Outputs**: divergence alerts → opportunities table
- **Side effects**: Persists research evidence only; a divergence Opportunity is not a Candidate entry owner.
- **Direction contract**: YES probability change is combined with deterministic
  bullish/bearish question polarity. Unknown, negated, or mixed polarity fails closed;
  probability direction alone never decides crypto direction.

#### Historical Ranker / PaperTrader (retired entry)

The former deterministic ranker scored Opportunities, bounded a five-carrier
pending queue and applied family TTL penalties. PaperTrader then wrote
`signal_pending` PiDecisions and woke the Operator. These are historical semantics
only: neither job, queue admission, TTL writer nor on-demand notification remains
an executable entry path. Old ranker/funnel outcomes remain read-only evidence.

#### research_agent / research_worker

- **Owner**: `extensions/research/research_agent.py` owns advisory notes;
  `aletheia/agents/research_worker.py` retains the distinct Serenity pipeline.
- **Trigger**: enqueued by dreaming_agent or manual
- **Inputs**: topic query + knowledge context; Serenity publication reads exact research opportunity IDs and evidence, independent of display limits
- **LLM**: Yes (research_worker makes LLM calls)
- **Outputs**: knowledge_items(research_note), serenity_profile_snapshots
- **Side effects**: FTS index update; eligible Serenity research can publish durable Candidates via `publish_serenity_candidates`, sharing the manual publication implementation. Auto-promotion retains its score gate; manual publication still requires real PA/eligibility evidence. Source identity is frozen at birth, not inferred from pending. See [Serenity contract](serenity_contract.md).

#### portfolio_agent

- **Trigger**: manual (not scheduled)
- **Inputs**: current positions + portfolio metrics
- **LLM**: Yes
- **Outputs**: portfolio rebalancing recommendations
- **Side effects**: None (advisory only)

---

### B. Trading Agents

#### Canonical Entry Planner / daemon / heartbeat

- **Trigger**: durable Candidate planning wake through the event-driven daemon;
  the scheduled `pi_trading_decision` heartbeat wakes that daemon or calls the
  identical `run_entry_planner_batch()` inline when it is unavailable.
- **Inputs**: current active PlanningGeneration, immutable Candidate source
  context, exact causal market windows/Anchors and typed capability/Profile facts.
  Universe and Serenity publish the same Candidate/current-generation authority;
  producer refresh is scoped, exact instrument ownership is global.
- **LLM**: one qualitative EntryHypothesis path. The model owns no execution
  price, size, leverage, stop, target or trigger parameter. Compiler owns geometry.
- **Outputs**: durable typed planning outcome and immutable EntryPlan. The Plan
  freezes source kind/ID/school and holding horizon; Proposal attribution preserves
  them, and Serenity opportunity_id remains the real research source.
- **Execution**: Plan execution/watcher → Risk → Gateway only. Watcher never
  invokes AI; registered TriggerSpec determines triggered release. Resting plans
  may execute immediately through the same canonical implementation.
- **Timing/holding**: INTRADAY/SWING timing and 60/240-minute entry expiry do not
  shorten Serenity's max_hold_days×24 holding; Proposal.expected_horizon_hours
  receives Plan.holding_horizon_hours. Structural stop geometry determines sizing.
- **Availability**: missing/stale exact facts or capability are typed unavailable,
  not an economic NO_PLAN. Idle remains idle. There is no old evaluator, debate,
  PiDecision entry claim, pending defer/wake or orphan-Proposal reconstruction.
- **Concurrency**: existing durable claim/retry/CAS and Plan execution uniqueness
  prevent duplicate model ownership and duplicate Proposal/Order submission.
  Urgent portfolio work may preempt planning without creating another entry owner.
- **Current truth**: `candidate_attention`, `entry_plans`, `planning_history` and
  summary come from the canonical workbench; old queues and ranker/funnel cohorts
  are history only. `pi_reflection.json` is historical entry evidence, not current state.
- **Position authority**: independent portfolio review retains explicit Position
  identity and hold/adjust_position/close_one behavior. HORIZON_DUE requires a
  real future extension or close; invalid labels cannot become a silent full close.
  Shared close/reduce/adjust and their audit remain supported, never as new entry.
- **Position X authority**: explicit owner aggregate-row or sanitized holder-batch imports and their 1h/4h/24h outcomes remain observation-only. Holder batches are reduced to aggregate snapshots and never persist holder identities. Read-only comparisons reject retrospective imports, mixed epochs and mixed feature contracts; cost-aware evidence stays unavailable. No automatic source/scheduler or promotion authority is introduced, and this evidence never changes Agent, Risk or Execution authority.
- **Safety/recovery**: Risk approval precedes Gateway. Existing Orders, partial
  Fills, Positions and unknown submissions keep canonical reconciliation/cancel/
  settlement; retirement records evidence and does not fabricate exchange outcomes.

See [Entry Plan contract](entry-plan-contract.md) and
[Decision lifecycle](decision_lifecycle_contract.md) for the current contract.

#### Historical TradingDecision / Momentum Setup (nonexecuting history)

The former Three-layer Context, Bull/Bear debate, final PiDecision and reflection
claim chain is retired for entry. Its old rationale/provenance remains readable;
new plans never write or recover that entry state. The former Setup arm/refuse,
ConditionalAuthorization and materialization path likewise has no adapter
exception when a current Plan is absent. Fast Monitoring watches EntryPlans, not
old Setups; historical arm/trigger rows cannot create a new Proposal. Existing
order identity is recovered by Gateway, not by reviving Setup execution.

#### position_path_observer

- **Trigger**: scheduler every 5min on the shared `io` executor
- **Inputs**: all open/eligible closed Positions, append-only Position-path observations,
  and the durable deferred-bucket queue
- **Outputs**: one append-only `available`/`unavailable` observation per resolved
  Position/5m bucket, or a bounded retry row while transient market-data evidence is
  unavailable
- **Expected-bucket contract**: the first bucket starts at the first complete 5m
  interval after `opened_at`; open Positions end at the latest completed bucket, and
  closed Positions end at the last bucket completed before `closed_at`. Each run
  registers only the earliest not-yet-materialized bucket per bounded pass, then
  consumes the oldest due bucket cohort. A healthy cycle may make at most two
  passes per Position so recovery converges without a backfill storm. A retry
  reaches terminal `unavailable` after the bounded attempt limit; it never remains
  pending forever.
- **Identity contract**: OHLCV lookup and deduplication use the exact physical
  `InstrumentId` (venue + canonical symbol + spot/perp market). A bar is available
  only with the exact timestamp and provider `is_closed` proof.
- **Historical snapshot contract**: a recovered bucket never attaches the current
  mutable funding/protection state as if it were historical. Funding remains
  unavailable; protection uses the durable plan effective at the bucket when one
  exists, otherwise `exit_plan_version=0` and nullable levels disclose that the
  historical plan is unavailable.
- **Side effects**: observation-only. It never mutates Position, ExitPlan, Proposal,
  RiskDecision, Order, Fill, Learning, or Live state. `aletheia control status` exposes
  oldest missing bucket, deferred count, observer lag, and trailing 24h available /
  unavailable / skipped counts.

#### position_manager

- **Trigger**: scheduler every 30s
- **Inputs**: Open positions, current prices, market-aware exit signals
- **LLM**: No
- **Outputs**: Position closes (status=closed, realized_pnl computed)
- **Side effects**: Calls close_position(); updates exchange TP/SL orders; writes audit events
- **Exit triggers**: stop_loss, take_profit, max_hold_time, trailing_stop. Market-aware signals only wake System Operator for review; they do not directly close or tighten. RuleSet exit auto-close is removed. System Operator exits run **independently** via `review_portfolio_state`, NOT inside this loop — see `execution_contract.md §10`.
- **Pause semantics**: runs even when `runtime.paused` (manages existing risk; never blocks protective close — aligned with kill_switch, see `execution_contract.md §10.3`)

---

### C. Strategy Agents — REMOVED (Position C, 2026-07-02)

The rules engine (`strategy_designer` + `backtest_orchestrator`) was **deleted**.
Strategy generation / DSL / backtesting no longer exist. The historical replacement
was `evaluate_trading_opportunity`; that entry evaluator is now also retired.
Current entry is Candidate → PlanningGeneration → Planner/Compiler → EntryPlan. Legacy
`strategy_records` / `backtest_runs` / `backtest_trades` tables are kept read-only
for historical attribution (not written to). The deterministic `guard_promotion`
job was re-homed from the deleted daily backtest cycle. Its retained schedule now
only performs learned-guard TTL expiry; candidate promotion is explicit owner review
(see scheduler_contract §job table).

---

### D. Learning Agents

#### post_trade_reviewer

- **Trigger**: scheduler every 4h; disabled when `agent.autonomous_learning=false`
- **Inputs**: Recently closed positions (no existing review), market context at entry/exit
- **LLM**: Yes (analysis of trade quality)
- **Outputs**: knowledge_items (item_type=post_trade_review)
- **Side effects**: Writes reviews (the review→strategy_designer event chain was removed with the rules engine, Position C)
- **Input budget**: consumes complete proposal-linked decision records only. It may
  reduce the number of whole Positions to stay within the total prompt budget and
  reports the omitted count; it never truncates a selected Position's rationale.

#### risk_critic

- **Owner**: `extensions/research/risk_critic.py`, registered by CLI/Web composition
  in the existing Core agent registry. An absent producer is unavailable in the
  manual catalog and omitted from the scheduler, not recorded as a failed run.
- **Trigger**: scheduler every 6h; disabled when `agent.autonomous_learning=false`
- **Inputs**: Open positions, risk config, recent violations, portfolio exposure
- **LLM**: Yes (risk assessment)
- **Outputs**: knowledge_items (item_type=**research_note**, metadata.subtype=risk_lesson — `extensions/research/risk_critic.py` calls `create_research_note()`; `risk_lesson` is not a top-level item_type, see `knowledge_contract.md §2.1`)
- **Side effects**: Writes one medium-confidence advisory research note. It does not flag opportunities or write `risk_decisions`; those facts remain owned by the Risk Engine. Automatic Risk lessons remain advisory because `risk_lesson_constraint` is not measurement-supported.

#### dreaming_daily

- **Trigger**: daily 02:00 CST; the shared Learning catch-up checks the latest missed lesson-extraction window 5 minutes after boot; `serenity_startup_catchup` separately repairs the research batch at 4 minutes
- **Inputs**: All knowledge_items from past 24h, closed positions, agent run summaries
- **LLM**: Yes (synthesis)
- **Outputs**: knowledge_items (item_type=dreaming_log, daily_review), serenity research runs/themes/evidence/opportunities
- **24h trading-performance block**: Every `daily_review` begins with a deterministic block (`_build_daily_trading_performance`) reading the central `eligible_strategy_outcomes()` cohort from the last 24h; raw lifecycle flags are not sufficient evidence. Labels: `PROFIT_DAY` / `LOSS_DAY` / `FLAT_DAY` / `NOT_AVAILABLE`. Excluded, unavailable, truncated, inconsistent, or invalid outcome evidence remains `NOT_AVAILABLE` and is reported with the raw/excluded counts; no P&L is coerced to zero. The block is also included in the LLM prompt so the narrative reflects the day's verified cost-complete P&L.
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

#### Learning contribution measurement (retired scheduler)

The former recurring `learning_contribution_evaluator`, startup measurement
catch-up and lifecycle-controller jobs are not registered in the current
Scheduler. Their persisted exposures, outcomes and verdicts remain historical
evidence. The current owner surface is the bounded read-only
`/api/analytics/learning-contribution` projection; it cannot invoke an evaluator,
change experiment state, grant Paper influence, or affect Candidate, Risk,
Execution, exchange or Live state. The broader `lco-v1` design remains the
[`learning_observability`](../proposals/learning_observability.md) Proposal.

---

### E. Operator Agent

#### pi_operator_loop

- **Trigger**: cron every 4 hours at `:15` CST plus a startup attempt after 2 minutes
- **Deduplication**: scheduled runs require the latest persisted `operator_assessment` to be at least 3h45m old; missing/malformed history fails open because action permission and daily rate limits remain authoritative
- **Inputs**: Portfolio state, strategy performance, market regime, system health
- **LLM**: No direct model call; methodology research is not scheduled by this loop
- **Outputs**: `operator_assessment`, bounded config actions/proposals and notifications
- **Side effects**: 
  - Level 1: bounded tightening/recovery and notification actions
  - Level 2: loosening and unknown actions become proposals; no direct execution
  - Level 3: live, credentials, permissions and hard-risk changes are forbidden, not confirmable by this loop
- **Key invariant**: Level 1 cannot expand risk; Level 2 requires a proposal path; Level 3 never executes

---

## 3. Cross-Cutting Concerns

### SQLite Concurrency

All agents share the same SQLite DB. Invariants that MUST hold (full detail in [data_model §1, §9](../data/data_model.md)):
1. `_WRITE_LOCK` (`sqlite.py`) — process-wide `threading.Lock`, acquired by `transaction()` before every `BEGIN`, released after `COMMIT`/`ROLLBACK`. Primary write serialization; `database is locked` cannot occur in normal operation.
2. WAL mode + `busy_timeout=30000ms` (30s) as last-resort fallback — `storage/sqlite.py::_open_connection`
3. One connection per thread via `threading.local`
4. `was_in_tx = db.in_transaction` captured BEFORE writes; commit only `if not was_in_tx`
5. Scheduler `max_workers=3` (do not raise)
6. Never `await` or do network I/O inside a `with transaction():` block — `_WRITE_LOCK` is held for the full transaction duration

### Job Timeouts (hard wall-clock bound, per-job — `scheduler.py:25-50`)

> Not a single 300s default — each job has its own cap. Timeout = listed value + 15s grace.

| Job | Timeout | Job | Timeout |
|-----|---------|-----|---------|
| market_data | 90s | dreaming_agent | 300s |
| market_observer | 240s | risk_critic | 300s |
| pi_trading_decision | 300s | — | — |
| post_trade_reviewer | 300s | exchange_sync | 60s |

Async jobs use `_run_async_with_timeout` (`scheduler.py:204`), sync jobs `_run_sync_with_timeout` (`:141`). `misfire_grace_time` 60-300s per `add_job`.

### Error Handling & Failure Isolation

- **Per-job degradation**: each job wrapped in catch-all; one failure logs a warning and does NOT cascade (`scheduler.py:708-793`). Agents coupled only through shared SQLite tables (blackboard), never direct calls — so an upstream failure cannot crash a downstream agent.
- **Leaked-tx cleanup**: scheduler rolls back any leaked transaction before/after each job.
- **Consecutive-failure alert**: ≥3 consecutive failures → `infra_event` (dashboard-visible), `_record_job_failure` (`scheduler.py:67-79`). Non-cascading.
- **Entry stale-input expiry**: canonical attention deadlines, planning retry/claim and Plan expiry bound current work; old pending cannot extend market-data refresh demand or wake a retired evaluator.

### LLM Fallback (request-level, unified — `model_provider/fallback.py:215-299`)

- Every provider completion is admitted by the process-wide Inference Broker before
  fallback execution. Admission is Provider-scoped and enforces bounded priority
  queues, concurrency, RPM/TPM and one total queue+execution deadline. Manual agent
  HTTP requests create a durable queued AgentRun and return `202 + job_id`; a
  persistent single worker owns execution outside the Uvicorn event loop.

- `complete_with_fallback()`: on **transient** errors (429/502/503/504, rate-limit, connect/network/remote-protocol/proxy transport failures), tries the next candidate model from the explicit ordered `fallback_models` configuration only; discovery and model-name heuristics cannot supply candidates (see [Inference Harness failure semantics](llm_inference_harness_contract.md#failure-and-recovery-semantics)). Authentication and other permanent 4xx failures do not retry. Timeout exceptions remain terminal so five sequential 60-second model attempts cannot overrun bounded agent jobs.
- `agents.helpers.llm_structured()` 对非对象/不可解析 JSON、schema 校验失败和空输出最多尝试 2 次：第一次使用原始请求，第二次只在这些结构化失败后追加一次随机 nonce 修复消息，并保持同一输出预算；不再把上一轮坏输出或递增预算带回第三次尝试。全部尝试共享 180 秒总墙钟预算（小于 Observer 200 秒循环预算和 240 秒任务上限），不继承 provider 面向长研究输出的 300 秒上限。当前 HTTP provider 使用 portable strict JSON Schema；不支持该字段时由适配器执行有界 schema fallback。`fallback_to_defaults` 默认关闭；只有非关键调用方显式开启时，失败才返回 schema 派生默认值，并在 error 字符串保留 `(fallback)` 标记。当前生产调用方均未开启该选项。
- **Cooldown**: a model with ≥2 consecutive failures is benched 300s (`_record_model_failure:116`).
- Non-transient errors or last-candidate failure → raised. Each agent then catches and degrades (e.g. market_observer → technical-only signals).

### Permission Modes (two surfaces — see [live_unlock_contract §5](live_unlock_contract.md))

> ⚠️ Corrected: not a flat Read Only/Operator/Full. There are TWO ranked surfaces:
- **Worker** (Agent Shell): `Observe < Paper < Full` (default `Paper`)
- **Operator** (PI/Dashboard): `ReadOnly < Research < PaperAuto < AllowEditing < Auto` (default `ReadOnly`)

`FORBIDDEN_INTENTS` (enable_live / bypass_risk / change_risk_limits / ...) are uncallable in ANY mode — `agent_shell/registry.py:415`.

---

## 4. Data Flow Summary

```text
Universe discovery ──────┐
                        ├─ durable Candidate / current PlanningGeneration
Serenity research + PA ──┘               │
                                Entry Planner / Hypothesis
                                        │
                                Deterministic Compiler
                                        │
                                Immutable EntryPlan
                                        │
                              Plan execution / watcher
                                        │
                                    Proposal
                                        │
                                  Risk → Gateway
                                        │
                                 Order → Fill → Position
                                                    │
                              position_manager / portfolio review
                                                    │
                                       Gateway close / adjust
                                                    │
                                  closed-trade review → knowledge
```

Other observers add research evidence, not a second admission path. Research
feedback can inform later source publication but cannot bypass PA, Planner,
Compiler, Risk or Gateway. UTA-only external accounts, one-way Lab authority and
unique auto.run deployment are approved future portions, not delivered claims
of this entry-contract update; unrelated agent/measurement contracts above are
not changed by the entry cutover.
