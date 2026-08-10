# Agent Contracts

> Input/output contracts for all registered agent functions and scheduler jobs.
> Each agent has defined triggers, data inputs, outputs, and side effects.
> Source of truth: `aletheia/agent_runtime/scheduler.py` + `aletheia/agents/*.py`
> Last updated: 2026-08-10 (structured System Operator trade motivation)

---

## 1. Scheduler Job Overview (40 recurring jobs with optional jobs enabled + 3 startup one-shots)

| # | Job ID | Interval | Executor | Description |
|---|--------|----------|----------|-------------|
| 1 | market_data | 5min | io | Collect market snapshots (price, volume, OHLCV) |
| 2 | market_observer | 10min | observer | Scan structured, exact-instrument facts for trading opportunities |
| 3 | equity_observer | cron 工作日 14-21UTC /30min | agents | xStocks/equity market scan (replaces stock_observer) |
| 4 | prediction_observer | 30min | agents | Prediction market scan |
| 5 | paper_trader | 3min + on-demand | agents | Collect signals → signal_pending PiDecisions (System Operator submits proposals — Phase 4a). `scheduler.notify_paper_trader(source)` coalesces committed Opportunity, eligibility-config, Guard-set, Universe refresh/TTL-slide, and Operator queue-slot-release events into an immediate run. The source list is persisted in `AgentRun.trigger_type`; 3min remains the safety fallback. |
| 6 | pi_trading_decision | ~15min (configurable) | agents | System Operator heartbeat (daemon fallback) |
| 7 | position_manager | 5min | io | Manage open positions (trailing stop, exit) |
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
| 26 | guard_promotion | cron 04:00 CST | io | Promote candidate trading guards to active (learning; re-homed from deleted backtest_orchestrator, Position C) |
| 27 | universe_refresh | 10min | io | Universe composition refresh |
| 28 | ohlcv_nightly_backfill | cron (configurable) CST | io | Historical OHLCV gap fill |
| 29 | ohlcv_active_backfill | interval `historical_data.active_universe_backfill_interval_minutes` (optional) | io | Bounded exact-instrument repair for active members below the OHLCV minimum; durable backoff |
| 30 | filter_controller_observe | interval `controller.observe_interval_hours` (optional) | io | Shadow-only pass/reject evidence report; never changes config |
| 31 | persona_rollout | 8h | io | Read-only safety hold; never changes Persona config without a prospective Persona/policy step verdict |
| 32 | connectivity_monitor | interval `health.exchange_probe_interval_sec` | fast_io | Probe exchange connectivity; auto-pause on outage; auto-resume when recovered |
| 33 | shadow_resolver | 6h | io | Resolve shadow trades against realised outcomes |
| 34 | outcome_hook_replay | 1min | io | Replay durable eligible-close feedback jobs; revalidate the closed Position and retry incomplete shadow/Serenity delivery |
| 35 | news_fetch | interval `news.fetch_interval_minutes` | io | Poll news providers into `news_items` |
| 35a | news_analyze | 5min | agents | Independently analyze at most 3 news rows; each item and the whole job are bounded, and failures remain retry/failed rather than fabricated analyzed facts |
| 36 | outcome_settler | 10min | io | Settle outstanding prediction / universe forward outcomes |
| 36a | paper_funding_settler | 30min | io | Recover complete local-paper futures funding from persisted live/history evidence and atomically converge Position, close event, cash, eligibility, audit, and durable outcome hooks; read source I/O never spans the SQLite writer transaction |
| 37 | learning_contribution_evaluator | interval `learning.evaluation_interval_minutes` (default 60) | learning_measurement | Sole scheduled contribution evaluator. Measure only: append deterministic cluster-based contribution verdicts and evaluator health even when autonomous Learning is off or runtime is paused |
| 38 | learning_lifecycle_controller | interval `learning.evaluation_interval_minutes` (default 60) | io | Apply at most one owner-gated paper lifecycle action per current experiment; requires autonomous Learning, no pause/integrity incident, lifecycle automation, paper influence, and path allowlist |
| 39 | serenity_startup_catchup | once, 4min after gateway boot | agents | Recover a missed 02:00 CST Serenity batch using a durable per-day key; skips fresh/active/already-attempted batches |
| 40 | learning_startup_catchup | once, 5min after gateway boot | agents | Run only the latest missed dreaming/memo/reviewer date-window; a durable success watermark prevents restart duplicates |
| 41 | learning_measurement_startup_catchup | once, 2min after gateway boot | learning_measurement | Before the first regular evaluator interval, re-run only the current missing contribution-measurement window through `learning_contribution_evaluator`; shares its serial non-starving lane and remains independent of autonomous Learning and paper influence |
| 42 | learning_lifecycle_audit_reconciler | every 5min | io | Retry bounded pending lifecycle JSONL mirrors by stable event id; never replay a transition and continue during layered pause |

Learning behavior jobs (rows 10–15, 26, 30–31, 38 and the Learning behavior catch-up) share
`agent.autonomous_learning` as one backend gate. When disabled, no automatic producer,
reviewer, promoter, controller, or learned rollout runs. Measurement-only
`outcome_settler`, `paper_funding_settler`, `outcome_hook_replay`, `shadow_resolver`, and
`learning_contribution_evaluator` continue so disabling
Learning does not erase or delay outcome truth. The evaluator never changes stages or authority;
the lifecycle controller is a separate gated consumer. Successful, partial/degraded, failed and
skipped runs plus reviewer backlog health are persisted in `learning_job_health`. A controller cycle
with one failed experiment continues the others but cannot be recorded as healthy. There is no
scheduled
`learning_verdict_runner`; any historical health row under that id remains audit history only.
After recovery, `status` may be healthy and `last_error` clears, while `last_failed_at` and
`last_failure_reason` preserve the most recent failure.

`paper_funding_settler` is accounting maintenance, not an agent decision or exchange-account
collector. It runs during layered pause, keeps incomplete facts `NULL`, and may use public historical
settlements only as `local_simulation` evidence. It never labels age alone as structural failure.
When evidence becomes complete it calls the single atomic materializer; downstream feedback is
durably queued and independently retried by `outcome_hook_replay`.

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
- **Outputs**: opportunities table (symbol, direction, confidence, key_observations JSON)
- **Side effects**: Writes to `opportunities` table and requests an on-demand Ranker
  wake only after the Opportunity/evidence transaction commits. It does not wake the
  trading daemon directly; only Ranker admission plus committed `signal_pending` does.
- **Error behavior**: Degrades to basic technical-only signals when LLM unavailable
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
  member candidate and fixed-horizon outcome identity; standalone reads stay unlinked when no true
  outcome producer exists. Market and prediction context is non-Learning input and remains present.
- **Downstream contract** (spec UP-06): `key_observations` (the LLM's numeric citations) IS surfaced to the System Operator's layer1 snapshot; previously only a truncated thesis reached the decision.
- **Bounded-loop fairness contract** (spec GS-S03): the 200-second safety budget remains below the scheduler timeout, but the next cycle begins at the durable successor of the last completed exact instrument. A cancellation or budget break never skips an in-flight or unprocessed instrument.
- **Pre-filter status contract** (spec OBS-PRE-FILTER-STATUS): `no_sufficient_signal` is a normal rejection recorded in filter evidence, not an agent execution error. It skips the LLM without changing the run to `completed_with_errors`; provider failures, unexpected exceptions, and loop-budget exhaustion remain errors.
- **Information provenance**: Opportunity metadata preserves the original T0 scanner
  identity and only the exact T1 source rows actually rendered to the Observer. It
  never infers historical news, macro, on-chain, prediction, Hyperliquid, or Position X
  evidence from whichever row is latest later.

#### equity_observer (replaces stock_observer)

- **Trigger**: cron 工作日 14:00-21:00 UTC, every 30min
- **Inputs**: yfinance daily bars + Bitget xStocks real-time price cross-validation
- **LLM**: Yes
- **Outputs**: opportunities table (equity-linked symbols)
- **Side effects**: Same as market_observer but for equity-linked tokens; the
  committed observation-only Opportunity wakes Ranker so its terminal exclusion
  is not delayed.
- **Condition**: config.market_data.observed_equities non-empty

#### stock_observer (removed 2026-06-17 — superseded by equity_observer)

#### prediction_observer

- **Trigger**: scheduler every 30min
- **Inputs**: prediction market APIs (Polymarket/Kalshi), existing prediction_markets table
- **LLM**: Yes (structured event interpretation; the chained divergence scanner is deterministic)
- **Outputs**: prediction_runs, prediction_markets, prediction_probability_snapshots,
  and observation-only prediction Opportunities
- **Side effects**: Chains divergence_scanner on completion and wakes Ranker after
  each committed Opportunity so its exclusion is terminalized promptly.

#### divergence_scanner (chained from prediction_observer)

- **Trigger**: runs after prediction_observer completes
- **Inputs**: prediction market prices vs spot prices
- **LLM**: No
- **Outputs**: divergence alerts → opportunities table
- **Side effects**: A newly committed tradeable divergence Opportunity wakes Ranker.
- **Direction contract**: YES probability change is combined with deterministic
  bullish/bearish question polarity. Unknown, negated, or mixed polarity fails closed;
  probability direction alone never decides crypto direction.

#### paper_trader_ranker (internal to paper_trader)

- **Trigger**: called within paper_trader job
- **Inputs**: opportunities + universe_scores + trading_guards + PiDecision
- **LLM**: No (deterministic ranking, 8-dimension scoring)
- **Outputs**: ranked + filtered opportunity list → PI daemon signal
- **Side effects**: None
- **Contract**: Must NEVER call LLM directly
- **Admission/backpressure**: at most 5 unresolved market-observer carriers may be
  queued at once. Overflow remains `open` and is retried; stale carriers are
  pruned only after source expiry/review and never while proposal submission is
  recovering.
- **Carrier continuity** (`DL-INV-25`, `DL-S52..54`): Universe snapshot supersede expires only still-open
  analytical generations. Once `signal_pending` commits, the queued source remains
  Operator authority until its own `valid_until` or a terminal decision. A fixed
  30-minute queue age is legacy fallback only. New generations of the same
  symbol/direction/scanner-family record `active_operator_carrier` instead of
  creating another pending carrier.
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
- **Inputs**: topic query + knowledge context; Serenity promotion hydrates the exact
  pending opportunity by id rather than relying on the global active top-five display
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

- **Trigger**: scheduler every 3min plus a debounced on-demand nudge via
  `scheduler.notify_paper_trader(source)`. Sources are Universe snapshot/TTL-slide,
  Market/Equity/Prediction/Divergence Opportunity commit, Ranker eligibility config
  save, active Guard-set mutation, and Operator queue-slot release. Bursts within 3s
  produce one scheduler modification but retain all sources. Notifications arriving
  during a run arm one follow-up run. `AgentRun.trigger_type` is
  `on_demand:<sorted_sources>` for dispatched wakes and `scheduled` for the fallback.
  Tier 3 daemon remains deferred in the upgrade plan.
- **Inputs**: opportunities, universe features, System Operator decisions
- **LLM**: No (pure rule evaluation)
- **Outputs**: signal_pending PiDecisions (Phase 4a: System Operator is the **sole** proposal submitter — paper_trader no longer creates proposals directly)
- **Side effects**: Writes signal_pending decisions for System Operator consumption. RuleSet exit auto-close was removed with the rules engine; paper_trader must not close positions directly.
- **Inspection contract**: `/analytics/opportunity-funnel.current` and
  `aletheia inspect entry-status` read durable Opportunity, filter-decision,
  PiDecision and AgentRun facts. They never invoke `paper_trader_ranker`. The
  process-local wake state is diagnostic only and is labelled unavailable when
  the scheduler is outside the serving process.
- **Lifecycle accounting** (`FE-S21`, `API-S09`): the funnel reports unique setups separately from
  per-snapshot generated rows and splits `universe_snapshot_superseded` from true
  `ttl_expired`; raw `status='expired'` is never sufficient to claim TTL expiry.
- **Key invariant**: Never calls LLM; never executes orders directly; never submits proposals (signal collector only)
- **Learning authority**: factor history and raw case/memo scores are diagnostic proposals only. Their experiment hooks are currently measurement-unsupported, so they create no formal exposure and score/filter influence stays at the static baseline. Any future measured implementation must retain the same venue, eligibility, static-risk-flag, learned-guard, and downstream Risk Engine checks for control candidates.

#### System Operator — Trading Decision Daemon (pi_trading_decision / pi_daemon)

- **Trigger**: Event-driven daemon (debounce 8s, cooldown 60s, heartbeat 5min)
- **Inputs**: 
  - Layer 1: market context (opportunities, factor_feedback, regime, positions), plus exact-instrument candidate technical snapshots (15m/1h/4h) and freshness-qualified Hyperliquid funding/OI evidence. The pending candidate is hydrated by `asset_id` independently of the bounded global feature display, including its queued/reviewed source Opportunity context.
  - Layer 2: strategy context (ranker candidates, pending signals, universe features)
  - Layer 3: reflection (previous decisions, outcomes, Serenity research). Each
    Serenity opportunity surfaces `id`/`score`/`bucket`/`evidence_count`/`key_evidence`
    + the independently `assessed_tier` (D2), annotated as long-horizon analyst input —
    NOT a same-day setup and NOT to be rubber-stamped (PI remains sole decision maker).
- **LLM**: Yes (structured decision with Bull/Bear debate)
- **Outputs**: an exact-candidate PiDecision (action, symbol, conviction, reasoning JSON, opportunity_id, signal_source) with `evidence_manifest_json` preserving Opportunity T0, Observer T1 and System Operator T1 identities under `operator-candidate-v3`. Operator T1 direct identities are limited to the exact technical snapshot and Position X block rendered in its prompt; Observer news/macro/on-chain/prediction/Hyperliquid identities remain only in upstream `opportunity_t1` and are labelled as summary-mediated, never copied as direct Operator evidence. Serenity resolves its separate id namespace and records only the exact Layer 3 evidence ids that belong to its persisted theme. A zero-pending wake instead writes one non-trading `operator_evaluation_event` and does not call the LLM
- **Structured trade motivation**: every new entry decision builds one
  object with `schema_version=structured-trade-motivation/v1`, containing
  `action_intent` (`long`/`short` for entry, `hold` for no-trade, with
  `close`/`reduce` reserved for their existing actions), `thesis`,
  `observed_facts` with source refs, `interpretations`, supporting/opposing
  evidence, assumptions, invalidation conditions, expected horizon, confidence,
  uncertainty reason, and the exact evidence manifest. The identical object is
  stored in the Proposal `agent_rationale.decision_meta.trade_motivation` and in
  the owning PiDecision `evidence_manifest_json.trade_motivation`; Thesis Lineage
  reads PiDecision first and Proposal second. Missing fields remain
  `unavailable`, empty, or `null`; readers never derive a structured object from
  legacy prose. This reuses existing JSON carriers and requires no table migration.
  `observed_facts` retains only refs that resolve to identities in that exact
  decision manifest; facts with no valid ref are omitted rather than presented as observed.
  The B-class behavior is part of the Opportunity Information policy epoch via
  `structured_trade_motivation=structured-trade-motivation/v1`, so pre/post-change
  decisions cannot share an epoch.
- **Side effects**: Candidate path writes `pi_decisions`; zero-pending path appends only an observation event; neither path bypasses paper_trader queue authority
- **Attribution**: `_attach_opportunity_context` backfills `opportunity_id` + `signal_source`
  (`market_observer` Layer 1 wins on tie; `serenity` Layer 3 is the fallback). `signal_source`
  disambiguates the two bare-uuid id spaces. Phase 2 of Serenity Option A.
- **Inference attribution**: the daemon binds one content-free `evaluation_id`; after the
  queue selects one carrier, every assessment/reassessment/debate attempt also records that
  exact Opportunity. The existing `signal_pending` PiDecision is recorded only when that
  source identity resolves to exactly one row; zero, duplicate, or unavailable telemetry reads
  leave it absent. A not-yet-created final decision or Proposal remains absent, and a daemon
  call never fabricates an `AgentRun` link.
- **Learning attribution**: reflection, memo, reasoning, calibration, trade cases, and structured
  lessons are declared as one `operator_learning_bundle`, but the path is currently
  measurement-unsupported and the static no-Learning prompt baseline applies. The existing
  Opportunity 4h market outcome is created before Operator prompt exposure and cannot measure the
  resulting trade/skip decision utility. Mixed/Serenity queues also fail closed. A future measured
  path requires a decision-linked causal outcome contract before any exposure or authority.
- **Actions**: active entry decision only `trade` / `skip`. The separate portfolio review exposes only `hold` / `adjust_position` / `close_one` for an explicit Position. An open `HORIZON_DUE` review cannot terminate with `hold`; it must either close or set an absolute `new_horizon_hours` strictly beyond current position age.
- **Unsupported legacy outputs**: `close` / `reduce` / `watch` / `scale_in` / `flip` on the entry surface are normalized to an evidenced `skip` and never execute. `reduce_exposure` on portfolio review is treated as `hold`, never as a silent full close.
- **Availability semantics**: missing/incomplete/stale exact candidate technical context is detected before model inference. For `trade`, the structured schema conditionally requires an explicit `market_type`, `stop_price` / `take_profit`, and all five entry-parameter reasons; the parser further requires `market_type` to be `spot` or `usdt_futures` and both prices to be finite and positive. Schema failure uses bounded structured retries and remains a content-free typed output defer; semantic, prompt-mark geometry, or price-derived reward:risk outside the inclusive `[1.00, 10.00]` plausibility envelope uses the existing single repair. A repaired first draft is retained as `operator_entry_plan_output_repair/error`, outside passed/rejected learning cohorts; the final authoritative entry-plan exposure is therefore `passed`, while an unrepaired second draft remains a real `rejected`. The fresh exact local-paper mark remains entry authority and validates instrument identity, long/short geometry, and `round(abs(tp-entry)/abs(entry-stop), 2)` against that envelope; invalid model protection is never silently replaced by config. A new Operator `spot+short` cannot be relabelled downstream as futures: it must repair to a valid decision or defer. The legacy coercion remains only for programmatic/historical recovery objects. If the submission-time refresh becomes stale/unavailable, crosses a protection level, or moves reward:risk outside the envelope, Proposal preparation returns a typed entry-plan defer with its gate trace. No final PiDecision is written and no pending signal is consumed. A newly persisted exact feature snapshot or entry-mark availability change changes the candidate fingerprint and reopens evaluation.
- **Serenity availability semantics**: an exact Serenity carrier whose source row is
  missing or whose research hydration fails receives a typed unavailable reason and
  defers before model inference. Full thesis/evidence remain persisted and fingerprinted;
  only their prompt display is bounded.
- **Position X authority**: owner aggregate imports and their 1h/4h/24h outcomes are observation-only. No automatic source is claimed; a minimum 14-day shadow window is required before human review and the evidence never changes Risk/Execution authority.
- **Safety**: Never executes orders directly. A complete `trade` prepares one immutable Proposal for the Gateway; `close_one` uses the central close router with an exact fresh mark.

#### position_manager

- **Trigger**: scheduler every 5min
- **Inputs**: Open positions, current prices, market-aware exit signals
- **LLM**: No
- **Outputs**: Position closes (status=closed, realized_pnl computed)
- **Side effects**: Calls close_position(); updates exchange TP/SL orders; writes audit events
- **Exit triggers**: stop_loss, take_profit, max_hold_time, trailing_stop. Market-aware signals only wake System Operator for review; they do not directly close or tighten. RuleSet exit auto-close is removed. System Operator exits run **independently** via `review_portfolio_state`, NOT inside this loop — see `execution_contract.md §10`.
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
- **Input budget**: consumes complete proposal-linked decision records only. It may
  reduce the number of whole Positions to stay within the total prompt budget and
  reports the omitted count; it never truncates a selected Position's rationale.

#### risk_critic

- **Trigger**: scheduler every 6h; disabled when `agent.autonomous_learning=false`
- **Inputs**: Open positions, risk config, recent violations, portfolio exposure
- **LLM**: Yes (risk assessment)
- **Outputs**: knowledge_items (item_type=**research_note**, metadata.subtype=risk_lesson — `risk_critic.py:42` calls `create_research_note()`; `risk_lesson` is not a top-level item_type, see `knowledge_contract.md §2.1`)
- **Side effects**: Writes one medium-confidence advisory research note. It does not flag opportunities or write `risk_decisions`; those facts remain owned by the Risk Engine. Automatic Risk lessons remain advisory because `risk_lesson_constraint` is not measurement-supported.

#### dreaming_daily

- **Trigger**: daily 02:00 CST; the shared Learning catch-up checks the latest missed lesson-extraction window 5 minutes after boot; `serenity_startup_catchup` separately repairs the research batch at 4 minutes
- **Inputs**: All knowledge_items from past 24h, closed positions, agent run summaries
- **LLM**: Yes (synthesis)
- **Outputs**: knowledge_items (item_type=dreaming_log, daily_review), serenity research runs/themes/evidence/opportunities
- **24h trading-performance block**: Every `daily_review` begins with a deterministic block (`_build_daily_trading_performance`) reading `outcome_eligible=True` closed positions from the last 24h. Labels: `PROFIT_DAY` / `LOSS_DAY` / `FLAT_DAY` / `NOT_AVAILABLE`. NULL `realized_pnl` is never coerced to zero — any NULL on an eligible close causes `NOT_AVAILABLE`. The block is also included in the LLM prompt so the narrative reflects the day's actual P&L.
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

#### learning_contribution_evaluator

- **Trigger**: one recurring interval at `learning.evaluation_interval_minutes` (default 60);
  restart preserves a nearer durable future deadline, and an overdue deadline retries after 60s;
  startup catch-up remains a one-shot caller of this same evaluator and retries only while the
  current measurement watermark is still missing
- **Inputs**: prospective `universe_family_calibration` exposures plus exact-key settled outcomes
- **Statistical unit**: persisted `assignment_unit_key` cluster; complete-cluster allocation utility
  averages selected-member net return with zero utility for dropped members, and uses worst selected
  MAE; incomplete or legacy unit-less clusters are excluded
- **Outcome precedence**: `filter_candidate_outcomes` is canonical full-arm truth;
  `universe_forward_outcomes` is a selected-member mirror/fallback and disagreement fails closed
- **Outputs**: append-only monitoring and once-consumed confirmatory verdicts under
  `universe_family_calibration_cluster_allocation_utility_v10`; old metric verdicts remain history
- **Inference**: conditional randomization over the prospectively assigned complete clusters;
  overlapping 1h/4h price paths are allowed and never resampled as IID outcomes
- **Intervention provenance**: `authority_applied` records consistent treatment permission;
  finite before/after behavior change is separate, and an authorized cluster is an actual
  intervention only when at least one member changed
- **Time semantics**: a market day is a UTC date with at least one complete eligible exposure unit
  from either arm. Shadow integrity readiness uses one day plus 10 units per arm only to permit an
  owner-started Paper-10 trial. Seven consecutive days plus 50 units in each arm inside that run
  remain required for economic promotion;
  a missing day resets the current window, planned outages receive no implicit waiver, and verdict
  `created_at` remains evaluation time
- **Inference semantics**: recurring results are descriptive monitoring. A Paper stage registered
  before exposure freezes one outcome-blind assignment-unit cutoff, waits for those exact outcomes,
  and consumes one confirmatory look at authority alpha `0.025`; cutoff-external rows cannot change
  that decision, while current monitoring harm/negative/invalid evidence remains an anytime brake
- **Authority**: measurement only; cannot change experiment stage, multiplier, paper authority,
  Risk, Execution, exchange, or live state
- **Current owner projection**: the implemented bounded summary is read-only and
  cannot invoke this evaluator or lifecycle control from a GET request. The
  broader `lco-v1` design remains the
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
