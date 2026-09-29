# Agent Contracts

> Input/output contracts for all registered agent functions and scheduler jobs.
> Each agent has defined triggers, data inputs, outputs, and side effects.
> Source of truth: `core/aletheia/agent_runtime/scheduler.py`, `core/aletheia/agents/*.py` and optional `extensions/research/*.py`
> Last updated: 2026-09-13 (D2 canonical entry; other domains unchanged)

---

## 1. Scheduler Job Overview

| # | Job ID | Interval | Executor | Description |
|---|--------|----------|----------|-------------|
| 1 | market_data | 5min | io | Collect market snapshots (price, volume, OHLCV) |
| 2 | equity_observer | cron 工作日 09:30-16:00 America/New_York /30min | agents | xStocks/equity market scan (replaces stock_observer) |
| 3 | prediction_observer | 30min | agents | Prediction market scan |
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

#### market_observer (retired 2026-09-24 — removed from the runtime topology)

Retired under the accepted proposal
[`docs/design/proposals/retire_market_observer.md`](../proposals/retire_market_observer.md)
(#2933). The agent, its scheduler job, its registration, and the diagnostic surfaces
that reported its liveness were deleted because its admitted workset had no producer:
`source_agent="universe_scanner"` no longer exists, so nothing could ever reach its
`signal_emitter` admission gate. The deterministic Universe → Opportunity mainline
is the sole candidate supply path. Historical `market_observer` runs and
`metadata.observer_assessment` rows remain readable as audit-only evidence; they
grant no current entry or enrichment authority.

#### equity_observer (replaces stock_observer)

- **Owner**: `extensions/research/equity_observer.py`, registered by CLI/Web
  composition in the existing Core registry. An absent Research package makes
  the manual catalog unavailable and omits this scheduler job, not a failed run.
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

- **Owner**: `extensions/research/prediction_observer.py`. CLI/Web composition
  binds the Prediction/Divergence pipeline separately from other Research agents;
  absent Research or its Data adapter makes both unavailable before manual queue
  writes and omits the scheduler job. Internal dependency defects propagate.
- **Trigger**: scheduler every 30min
- **Inputs**: prediction market APIs (Polymarket/Kalshi), existing prediction_markets table
- **LLM**: Yes (structured event interpretation; the chained divergence scanner is deterministic)
- **Outputs**: prediction_runs, prediction_markets, prediction_probability_snapshots,
  and observation-only prediction Opportunities
- **Side effects**: Scheduler chains divergence_scanner after the observer;
  manual Prediction refresh runs only this observer. Research Opportunities do
  not wake a retired entry queue. Existing Serenity evidence insertion also
  refreshes same-theme active research opportunity grades; it does not publish Candidates.

#### divergence_scanner (chained from prediction_observer)

- **Owner**: `extensions/research/divergence_scanner.py`, composed with the
  Prediction Observer. Core retains the existing registry and scheduler lifecycle.
- **Trigger**: runs after prediction_observer completes
- **Inputs**: prediction market prices vs spot prices
- **LLM**: No
- **Outputs**: divergence alerts → opportunities table
- **Side effects**: Retains the shared information-policy epoch check and terminal
  filter facts. A created observation-only Opportunity and its terminal fact share
  the existing transaction/savepoint; a divergence Opportunity is not a Candidate entry owner.
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
  `extensions/research/research_worker.py` owns the distinct optional Serenity pipeline.
- **Trigger**: enqueued by dreaming_agent or manual
- **Inputs**: topic query + knowledge context; Serenity publication reads exact research opportunity IDs and evidence, independent of display limits
- **LLM**: Yes (research_worker makes LLM calls)
- **Outputs**: knowledge_items(research_note), serenity_profile_snapshots
- **Side effects**: FTS index update; eligible Serenity research can publish durable Candidates via `publish_serenity_candidates`, sharing the manual publication implementation. Auto-promotion retains its score gate; manual publication still requires real PA/eligibility evidence. Source identity is frozen at birth, not inferred from pending. See [Serenity contract](serenity_contract.md).

#### portfolio_agent

- **Owner**: Core `core/aletheia/agents/portfolio_agent.py`, a delegate of the canonical
  `portfolio.pnl.compute_and_store_snapshot`, not an optional Research producer.
- **Trigger**: manual and the scheduled `portfolio_snapshot` job
- **Inputs**: configured execution identity and current open Positions
- **LLM**: No
- **Outputs**: persisted portfolio snapshot and its artifact reference
- **Side effects**: The canonical calculation persists available exact open-Position
  marks and unrealized-PnL provenance before filtering the snapshot to the configured
  execution mode. It neither creates a second accounting algorithm nor rebalances.

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
Current entry is Candidate → PlanningGeneration → Planner/Compiler → EntryPlan. Legacy `strategy_records` table is kept read-only for historical attribution (not
written to). `backtest_runs` and `backtest_trades` were dropped in schema v91
(issue #3703, 2026-09-29) — they had zero rows on Fedora at retirement audit. The deterministic `guard_promotion`
job was re-homed from the deleted daily backtest cycle. Its retained schedule now
only performs learned-guard TTL expiry; candidate promotion is explicit owner review
(see scheduler_contract §job table).

---

### D. Learning Agents

#### post_trade_reviewer

- **Owner**: `extensions/research/post_trade_reviewer.py`, explicitly composed in
  the existing Core registry; missing Research omits its job and rejects manual
  enqueue before persistence. Core retains central Outcome eligibility.
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

- **Owner / availability**: Dreaming and Lesson Reviewer have one implementation each in
  `extensions/research`; CLI/Web compose their manual functions into the Core agent
  registry. Scheduler receives only the existing async lesson-extraction helper and
  synchronous Serenity enqueue callback. Missing producers are unavailable in the
  catalog and omitted from cron/startup catch-up before Learning guards, without
  failed attempts, success watermarks or endless retries for absent work. Each
  available nightly sub-capability can still run independently.
- **Manual versus scheduled**: Full manual Dreaming writes knowledge summaries,
  daily reviews when its existing trigger permits, lessons and the optional Git
  artifact snapshot. The nightly wrapper enqueues Serenity and extracts lessons;
  it does not invoke full manual Dreaming. Lesson Reviewer retains scoped evidence
  floors, LessonStore CAS, immutable policy-history transaction and draft-skill
  creation/recovery; it neither activates skills nor writes execution facts.
- **Trigger**: daily 02:00 CST; the shared Learning catch-up checks the latest missed lesson-extraction window 5 minutes after boot; `serenity_startup_catchup` separately repairs the research batch at 4 minutes
- **Inputs**: All knowledge_items from past 24h, closed positions, agent run summaries
- **LLM**: Yes (synthesis)
- **Outputs**: knowledge_items (item_type=dreaming_log, daily_review), serenity research runs/themes/evidence/opportunities
- **24h trading-performance block**: Every `daily_review` begins with a deterministic block (`_build_daily_trading_performance`) reading the central `eligible_strategy_outcomes()` cohort from the last 24h; raw lifecycle flags are not sufficient evidence. Labels: `PROFIT_DAY` / `LOSS_DAY` / `FLAT_DAY` / `NOT_AVAILABLE`. Excluded, unavailable, truncated, inconsistent, or invalid outcome evidence remains `NOT_AVAILABLE` and is reported with the raw/excluded counts; no P&L is coerced to zero. The block is also included in the LLM prompt so the narrative reflects the day's verified cost-complete P&L.
- **Side effects**: The Serenity portion uses `serenity:scheduled:YYYY-MM-DD` idempotency and successful-run freshness. Restart-interrupted queued/running rows are marked failed before a catch-up is considered; lesson extraction uses `learning_job_health.input_watermark` to catch up one missed date-window without duplicate restart runs

#### insight_synthesizer

- **Owner**: optional `extensions/analytics/insight_synthesizer.py`; CLI/Web binds the existing scheduler callback. Without this report package, no report cron is registered and startup catch-up records unavailable without advancing a success watermark.
- **Trigger**: daily 02:30 CST (= 18:30 UTC previous day) + durable shared startup catch-up after 5min
- **Inputs**: knowledge_items, opportunities, positions, strategy performance
- **LLM**: Yes
- **Outputs**: Synthesized insights; reasoning_feedback piggyback (per-bucket statistics)
- **Side effects**: Writes insight items; updates reasoning feedback cache

#### confidence_calibration

- **Owner**: optional `extensions/analytics/confidence_calibration.py`; CLI/Web binds the scheduler builder and Core go-live scorecard readers. Missing reports retain the existing zero-score readiness blocker; score thresholds and economic eligibility are unchanged. The two report API handlers belong to the same optional package.
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

### E. Operator runtime

The TS operator-shell is the sole runtime Operator. Under the owner-approved
#3134 retirement, the Python `pi_operator_loop`, its scheduler/manual trigger,
config-action writer and fallback selector are removed. Disabling operator-shell
does not activate another Operator. Historical `operator_assessment` and audit
records remain readable; they grant no current execution authority.

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
- `agents.helpers.llm_structured()` 对非对象/不可解析 JSON、schema 校验失败和空输出最多尝试 2 次：第一次使用原始请求，第二次只在这些结构化失败后追加一次随机 nonce 修复消息，并保持同一输出预算；不再把上一轮坏输出或递增预算带回第三次尝试。全部尝试共享 180 秒总墙钟预算，不继承 provider 面向长研究输出的 300 秒上限。当前 HTTP provider 使用 portable strict JSON Schema；不支持该字段时由适配器执行有界 schema fallback。`fallback_to_defaults` 默认关闭；只有非关键调用方显式开启时，失败才返回 schema 派生默认值，并在 error 字符串保留 `(fallback)` 标记。当前生产调用方均未开启该选项。
- **Cooldown**: a model with ≥2 consecutive failures is benched 300s (`_record_model_failure:116`).
- Non-transient errors or last-candidate failure → raised. Each agent then catches and degrades (e.g. equity_observer → technical-only signals).

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
