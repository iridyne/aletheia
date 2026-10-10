# Scheduler Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `SCH` · **Owner code:** `core/aletheia/agent_runtime/scheduler.py`

## Scope

The scheduler is the one in-process timer that drives every recurring job of the trading
mainline (Universe → Candidate → Entry Plan → Risk → Gateway → Position → verified close →
Outcome) and its supporting maintenance. It owns the job registry below, the executor-pool
topology, layered pause, per-job failure accounting, timeout lanes and the shutdown protocol.
It never places orders (Gateway only), never decides Risk, and does not own what a job does
inside its body: per-agent IO lives in [Agent Contracts](agent_contracts.md), SQLite write
serialization in [data_model.md](../data/data_model.md) §1/§9. Learning jobs are listed as
they run today; their replacement is [spec 0001](../specs/0001-learning-loop/spec.md).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/agent_runtime/scheduler.py::init_scheduler` | lifecycle | `core/aletheia/server/app.py` lifespan, after migrations and tool-registry init |
| `core/aletheia/agent_runtime/scheduler.py::stop_scheduler` | lifecycle | `core/aletheia/server/app.py` lifespan shutdown |
| `core/aletheia/agent_runtime/scheduler.py::pause_scheduler` / `resume_scheduler` | lifecycle | `core/aletheia/agent_runtime/pause_ownership.py` acquire/release |
| `core/aletheia/agent_runtime/scheduler.py::reconcile_historical_data_jobs`, `reconcile_filter_controller` | re-registration | `core/aletheia/server/routes/settings.py` on save (adds/removes/reschedules `ohlcv_*` and `filter_controller_observe` under the same ids) |
| `core/aletheia/agent_runtime/scheduler.py::get_pause_state`, `scheduler_timeout_metrics_snapshot` | read-only projection | `/api/status`, health and Doctor |

## Durable state

The registry itself is process memory (APScheduler `BackgroundScheduler`, no job store).

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `runtime.pause_owners` (config) | `pause_ownership.py::acquire_pause` / `release_pauses` | `resume_scheduler`, `/api/status`, `/api/risk/status`, `aletheia control status`, Doctor `config.paused` | replaceable lease map |
| infra events `job_consecutive_failure:<job_id>` | `scheduler.py::_record_job_failure` | dashboard status popover, health | deduplicated 300 s |
| `learning_job_health` | learning gate callbacks inside `_run_job_guarded` | learning health readers | current row + preserved last failure |
| `logs/database_capacity.jsonl` | `cleanup` job | capacity Doctor probe | append-only |

## Job registry

`init_scheduler()` registers 43 distinct ids; this table is the only job table in the
documentation set. Every `add_job` uses `coalesce=True`, `max_instances=1` (job defaults).
`timeout` is the `_ASYNC_JOB_TIMEOUT_SEC` entry (shared by `_run_async_with_timeout` and
`_run_sync_with_timeout`); `—` means the job runs only under `_guarded()` without a time box.
`E` marks a member of `_ESSENTIAL_JOBS` (runs during layered pause, SCH-C05).

Scope values: **mainline** drives or protects a mainline step; **support** keeps facts,
accounting, audit delivery, health or storage the mainline depends on; **optional-research**
is an optional producer off the mainline; **frozen** is outside instrument scope and still
registered (removal: [spec 0003](../specs/0003-futures-only-convergence/spec.md) FC-R04);
**on-hold** is paused by owner decision (removal: spec 0003 FC-R07);
**replaced-by-spec-0001** runs today and is replaced by [spec 0001](../specs/0001-learning-loop/spec.md).

| job_id | trigger | executor | timeout (s) | misfire (s) | E | scope | registration condition |
|---|---|---|---|---|---|---|---|
| `market_data` | interval 5 min (`MARKET_DATA_INTERVAL_MINUTES`) | io | 90 | 60 | E | mainline | always |
| `price_feed` | interval 30 s | fast_io | 45 | 15 | E | mainline | always |
| `universe_refresh` | interval 10 min | maintenance_io | 600 | 120 |  | mainline | always; body skips when `universe.enabled=false` |
| `venue_margin_facts` | interval 5 min (`VENUE_MARGIN_REFRESH_INTERVAL_MINUTES`) | io | 60 | 120 |  | mainline | always |
| `demo_capabilities` | interval 4 h | io | — | 600 | E | mainline | always |
| `pi_trading_decision` | interval 15 min (`_pi_fallback_min`) | agents | 300 | 120 |  | mainline | always; guard id `pi_trading_heartbeat` |
| `position_manager` | interval 30 s | protective | — | 60 | E | mainline | always |
| `tpsl_fill_check` | interval 90 s | io | 45 | 30 | E | mainline | always |
| `position_reconcile` | interval 5 min | io | — | 60 | E | mainline | always |
| `reconciliation` | interval 2 min | io | — | 60 | E | mainline | always (every configured identity, no Live gate) |
| `exchange_sync` | interval 5 min | io | 60 | 60 | E | mainline | always |
| `broker_funding_settler` | interval 30 min | io | 120 | 120 | E | mainline | always |
| `outcome_hook_replay` | interval 1 min | io | — | 60 | E | mainline | always |
| `data_collectors` | interval 2 min | io | 55 | 60 | E | support | always |
| `data_collector_context` | interval 10 min | io | 60 | 60 | E | support | always |
| `connectivity_monitor` | interval `health.exchange_probe_interval_sec` | fast_io | 15 | 15 | E | support | always |
| `portfolio_snapshot` | interval 10 min (`PORTFOLIO_SNAPSHOT_INTERVAL_MINUTES`) | default | 45 | 120 | E | support | always |
| `pricing_reference_refresh` | interval 6 h | io | — | 300 | E | support | always |
| `position_path_observer` | interval 5 min | io | 90 | 120 | E | support | always |
| `ohlcv_nightly_backfill` | cron `historical_data.nightly_backfill_hour_cst`:30 Asia/Shanghai | maintenance_io | 3600 | 600 | E | support | `historical_data.nightly_backfill_enabled` |
| `ohlcv_active_backfill` | interval `historical_data.active_universe_backfill_interval_minutes` (10) | maintenance_io | 300 | 120 | E | support | `historical_data.enabled` and `active_universe_backfill_enabled` |
| `health_check` | interval 10 min | io | — | 120 | E | support | always |
| `cleanup` | cron 03:17 Asia/Shanghai | io | — | 600 | E | support | always (catch-up, SCH-C17) |
| `outcome_settler` | interval 10 min | maintenance_io | 300 | 120 | E | support | always |
| `funding_audit_reconciler` | interval 5 min | audit | 15 | 120 | E | support | always |
| `universe_scanner_audit_reconciler` | interval 5 min | audit | 15 | 120 | E | support | always |
| `observation_lab_bridge` | interval 1 min | audit | 30 | 120 | E | support | always |
| `daily_summary` | cron `telegram.daily_summary_hour_cst` (8):00 Asia/Shanghai | io | 60 | 600 |  | support | always |
| `news_fetch` | interval `news.fetch_interval_minutes` | io | 120 | 120 | E | optional-research | always; body skips when `news.enabled=false` |
| `news_analyze` | interval 5 min | agents | 105 | 120 |  | optional-research | always; body skips when `news.enabled=false` or paused |
| `serenity_startup_catchup` | date, boot + 4 min | agents | — | none |  | optional-research | Serenity enqueue callback composed |
| `post_trade_reviewer` | interval 4 h | agents | 300 | 300 |  | optional-research | `get_agent_fn("post_trade_reviewer")` resolves |
| `risk_critic` | interval 6 h | agents | 300 | 300 |  | optional-research | `get_agent_fn("risk_critic")` resolves |
| `filter_controller_observe` | interval `max(1, controller.observe_interval_hours)` h | io | — | 600 |  | optional-research | `observation.execution_owner == "main"` |
| `equity_observer` | `_equity_observer_trigger()`: Mon–Fri 09:30–16:00 America/New_York, every 30 min | agents | 240 | 300 |  | frozen | `get_agent_fn("equity_observer")` resolves |
| `prediction_observer` | interval 30 min | agents | 240 | 120 |  | frozen | `prediction_observer` and `divergence_scanner` both resolve |
| `persona_rollout` | interval 8 h | io | — | 600 |  | on-hold | always |
| `dreaming_daily` | cron 02:00 Asia/Shanghai | agents | 300 (`dreaming_agent`) | 300 |  | replaced-by-spec-0001 | lesson extraction or Serenity enqueue callback composed |
| `insight_synthesizer` | cron 02:30 Asia/Shanghai | agents | 120 | 600 |  | replaced-by-spec-0001 | analytics memo builder composed |
| `lesson_reviewer` | cron 03:00 Asia/Shanghai | agents | 300 | 300 |  | replaced-by-spec-0001 | lesson reviewer composed |
| `guard_promotion` | cron 04:00 Asia/Shanghai | io | — | 600 |  | replaced-by-spec-0001 | always; body only expires learned guards |
| `confidence_calibration` | interval 6 h | io | 30 | 300 |  | replaced-by-spec-0001 | calibration report builder composed |
| `learning_startup_catchup` | date, boot + 5 min | agents | — | none |  | replaced-by-spec-0001 | always; skips absent children |

Jobs still dispatched while a layered pause is active (`_ESSENTIAL_JOBS`, 25 ids): `market_data`,
`data_collectors`, `data_collector_context`, `connectivity_monitor`, `portfolio_snapshot`,
`pricing_reference_refresh`, `price_feed`, `position_manager`, `position_path_observer`,
`ohlcv_nightly_backfill`, `ohlcv_active_backfill`, `exchange_sync`, `health_check`, `cleanup`,
`news_fetch`, `demo_capabilities`, `reconciliation`, `position_reconcile`, `tpsl_fill_check`,
`outcome_settler`, `broker_funding_settler`, `funding_audit_reconciler`, `outcome_hook_replay`,
`universe_scanner_audit_reconciler`, `observation_lab_bridge`. Every other job — all
LLM/decision jobs, `universe_refresh`, `venue_margin_facts` and `daily_summary` included — is paused.

## Requirements

### 1. Lifecycle and registry

#### SCH-C01 One construction path

`When the server lifespan starts with the scheduler enabled, the scheduler shall build one BackgroundScheduler from _EXECUTOR_WORKERS with job defaults coalesce=True and max_instances=1, register the job registry, and start it.`

- WHEN `init_scheduler()` runs THEN every boot-time `add_job` id inside it is unique (re-registration helpers may reuse an id only after `get_job()` shows it absent).
- WHEN `init_scheduler()` raises during start THEN the scheduler is shut down, the async timeout workers are closed, and the error propagates.

#### SCH-C02 Absent optional producer omits the job

`If an optional extensions producer is not composed, then the scheduler shall omit its job without creating a failed AgentRun or a failed job-health record, and Core jobs shall stay registered.`

- WHEN `get_agent_fn("equity_observer")` raises `ValueError` THEN `equity_observer` is not registered and an info log names the absent producer.
- WHEN the analytics builder for `insight_synthesizer` is absent THEN no cron is registered, and the startup catch-up marks that child `unavailable` without advancing a success watermark.

#### SCH-C03 Named executor pools

`The scheduler shall run each job on one of eight named pools — default 2, io 1, maintenance_io 1, audit 1, fast_io 1, protective 1, observer 1, agents 1 — and no pool shall exceed 3 workers.`

- WHEN `position_manager` is registered THEN it is the only job on `protective`.
- WHEN a long maintenance job (`universe_refresh`, `outcome_settler`, `ohlcv_*`) is registered THEN it is on `maintenance_io`, never on `io`.
- WHEN the audit reconcilers and `observation_lab_bridge` are registered THEN they are on `audit`.
- `observer` is reserved and has no job.

### 2. Pause

#### SCH-C04 Layered pause keeps the essential set

`While runtime.paused is set and health.layered_pause_enabled is true, the scheduler shall dispatch exactly the jobs in _ESSENTIAL_JOBS and pause every other registered job.`

- WHEN the scheduler starts paused in layered mode THEN each non-essential job is paused and recorded in `_selectively_paused_jobs`.
- WHEN `pause_scheduler(layered=False)` is called THEN APScheduler pauses its whole dispatch loop; the registry is unchanged in both modes.

#### SCH-C05 Essential jobs do not self-disable

`The jobs in _ESSENTIAL_JOBS shall not return early because runtime.paused is set.`

- WHEN `position_manager` runs while paused THEN it still evaluates protective exits.
- WHEN an LLM or decision job (`pi_trading_decision`, `news_analyze`, `guard_promotion`, `universe_refresh`) runs while paused THEN its body returns early (defence in depth for blanket pause).

#### SCH-C06 Resume is owner-aware and atomic

`When resume_scheduler is called, the scheduler shall, under the pause-transition lock, refuse to resume while any durable pause owner or applicable brake remains and shall otherwise resume the blanket and every selectively paused job.`

- WHEN an owner remains THEN `resume_scheduler()` returns `False` and writes `scheduler_resume_blocked` with the blocking owners; Settings `scheduler_enabled=true` cannot bypass it.
- WHEN one selectively paused job fails to resume THEN the jobs resumed in that attempt are re-paused and `scheduler_resume_partial:<ids>` is raised, so the releasing owner's lease is restored.
- WHEN a recovery path releases a lease THEN it releases only the generation it observed; a newer generation or another owner keeps the pause.

### 3. Failure accounting

#### SCH-C07 Guarded execution

`The scheduler shall run every job through _run_job_guarded, so that no job exception stops the scheduler thread and each run rolls back any leaked SQLite transaction before it starts and rolls back and closes the thread connection after it ends.`

- WHEN a job body raises THEN the wrapper records `_record_job_failure(job_id, …)` and logs `scheduler_job_result outcome=failed`.
- The cleanup holds `_SCHEDULER_JOB_LOCK` only around the rollback/close calls, never around the job body.

#### SCH-C08 Consecutive-failure alert

`When a job records its third consecutive failure, the scheduler shall emit a warning infra event job_consecutive_failure:<job_id> deduplicated for 300 seconds, and shall reset the counter only after a run that recorded no failure.`

- WHEN a body swallows an error but calls `_record_job_failure` THEN the wrapper does not count that run as success.

#### SCH-C09 Reported errors are failures

`If a job's producer returns a failure value or a non-empty errors list, then the scheduler shall record a job failure with a stable reason code while keeping work already completed in that run.`

| Job | Condition | Code |
|---|---|---|
| any `_async_run` agent | `completed_with_errors` or non-empty `errors` | `scheduled_agent_reported_errors:<agent>` |
| `universe_refresh` | `refresh()` returns `None` with `universe.enabled=true` | `universe_refresh_no_snapshot` |
| `market_data` / `price_feed` | non-empty `errors` | `market_data_reported_errors` / `price_feed_reported_errors` |
| `exchange_sync` | any account scope returns `None` or `errors` | `exchange_sync_reported_errors` |
| `reconciliation` / `position_reconcile` | non-empty `errors` | `reconciliation_reported_errors` / `position_reconcile_reported_errors` |
| `tpsl_fill_check` | any plan fails | `tpsl_private_reconciliation_failed` |
| `data_collectors` | funding/OI write fails | `derivatives_funding_persist_failed` / `derivatives_open_interest_persist_failed` (domain failed, fan-out partial) |
| `position_manager` | non-empty fresh `errors` | `position_manager_reported_errors` |
| `insight_synthesizer` / `confidence_calibration` | report package absent on direct call | `analytics_reports_unavailable` |

- WHEN the observation list is empty and `errors=[]` THEN `market_data` succeeds.

#### SCH-C10 Durable stable escalation is not a failure

`If a job result reports a finding whose durable escalation record is confirmed on this pass, then the scheduler shall report it under escalated, shall not increment or reset the failure counter for it, and shall re-emit its infra event on every pass.`

- WHEN `position_manager` returns only `escalated` findings THEN the job succeeds and emits `position_manager_stable_escalation` (dedupe 600 s).
- WHEN the durable record can be neither written nor confirmed THEN the finding is returned under `errors` and the job fails.
- Exclusivity is per finding (`code` + `position_id`): one Position may contribute one `escalated` and one `errors` finding in the same pass.

#### SCH-C11 Planner heartbeat has its own health identity

`When the 15-minute pi_trading_decision safety net runs, the scheduler shall record delivery failures under pi_trading_heartbeat and evaluation outcomes under pi_trading_decision.`

- WHEN the daemon is enabled and running and `notify_pi_daemon(source="heartbeat")` succeeds THEN the job returns without clearing `pi_trading_decision` failures.
- WHEN delivery fails THEN `daemon_notify_failed` is recorded under `pi_trading_heartbeat` and the same `run_entry_planner_batch()` runs inline.
- WHEN `runtime.paused` is set or `agent.permission_mode` is not `Paper`/`Full` THEN the job returns without planning.
- The interval is the fixed 15 minutes, not derived from `pi_daemon.heartbeat_sec`.

### 4. Time boxes

#### SCH-C12 Fixed, bounded timeout lanes

`The scheduler shall execute time-boxed jobs on process-owned lanes created once per scheduler life — an async event-loop lane, a dedicated news_analyze async lane, an ordinary sync lane and a Universe-recovery-audit sync lane — each with outstanding capacity 16.`

- WHEN a lane is at capacity THEN `submit()` raises and `async_rejected` / `sync_rejected` is counted.
- WHEN `scheduler_timeout_metrics_snapshot()` is read THEN it reports submitted/completed/timed_out/cancelled/rejected/detached counts, pending work per lane and `abandoned_threads=0`.

#### SCH-C13 Timeout expiry

`When a time-boxed job exceeds its timeout, the scheduler shall raise TimeoutError to the caller, cancel the coroutine (async) or the not-yet-started future (sync), and keep lane capacity accounted until the work actually ends.`

- WHEN the async loop does not respond within 5 s after the deadline THEN the exact future is cancelled and counted.
- WHEN `universe_refresh` (`drain_on_timeout=True`) times out THEN the caller waits up to `_ASYNC_DRAIN_GRACE_SEC` (30 s), then detaches; the next tick skips while the prior worker drains.
- A running synchronous call cannot be interrupted; it occupies only its own lane.

#### SCH-C14 Collector deadlines sit inside the job timeout

`The data_collectors job shall bound its primary-venue lane by a 50-second internal deadline inside its 55-second job timeout, and data_collector_context shall bound its cross-source lane by a 55-second internal deadline inside its 60-second job timeout.`

- WHEN one collector exceeds its cap THEN `_bounded()` returns `None` at once, records the collector `timed_out`, and cancels the task in the background.
- Both jobs share the single `io` worker, so primary-venue CCXT calls stay serial; only independent public HTTP calls in the context lane wait concurrently.

### 5. Maintenance jobs

#### SCH-C15 Cleanup catch-up and bounded reclaim

`When the scheduler starts and the last due 03:17 Asia/Shanghai cleanup has no successful data_pruned record, the scheduler shall run the same guarded cleanup five minutes after readiness.`

- The catch-up never runs inside the Gateway pre-yield cold start.
- WHEN cleanup reclaims space THEN it reclaims at most 16 MiB of freelist, or 64 MiB once the physical database plus WAL reaches 80% of `app_db_capacity_mb`, holding the SQLite writer permit and changing no retention window.
- WHEN cleanup succeeds THEN a capacity snapshot is appended to `logs/database_capacity.jsonl`.

#### SCH-C16 Audit reconcilers stay off the operational lane

`The funding_audit_reconciler and universe_scanner_audit_reconciler jobs shall run on the audit pool with bounded sync work, shall only append or confirm stable audit identities, and shall never replay Position, PnL, close projection or Outcome hooks.`

- WHEN an audit filesystem call hangs THEN `io` and the ordinary sync lane stay available.
- WHEN a funding-audit backlog exists THEN job health and `/api/health` are degraded and Doctor warns; `/api/ready` is unchanged.

#### SCH-C17 Broker funding settlement rotates a durable cursor

`The broker_funding_settler job shall scan unresolved verified exchange closes through the durable broker_funding_settlement_cursor (closed_at, rowid) keyset, and only the matching profile's funding_history_retention_expired result shall terminalize automatic recovery as broker_funding_history_expired.`

- WHEN a prefix row is malformed, CAS-conflicting or transiently failing THEN later closes are still reached and the row stays retryable.
- PnL/funding stay `NULL` until evidence exists; no cash, Outcome or learning hook is fabricated.

### 6. Shutdown

#### SCH-C18 Bounded, honest shutdown

`When stop_scheduler is called, the scheduler shall stop dispatch, reject new sync work and cancel queued futures, close the async workers, wait at most _SYNC_LANE_DRAIN_SEC (5 s) for each sync lane's running call, and return True only if both sync lanes drained.`

- WHEN a lane still runs after 5 s THEN a warning is logged and `False` is returned; the lifespan then ends the runtime session with `clean=False`.
- WHEN `init_scheduler()` runs again THEN `_ensure_sync_lanes_ready()` replaces a closed lane only after its old thread ended, and raises otherwise (one owner per lane).

#### SCH-C19 The scheduler never places orders

`The scheduler shall reach execution only through reconciliation, stale-order cleanup and venue-capability refresh entry points, never through an order-placement path.`

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| job body raises | caught by `_run_job_guarded`, failure counted | log + `job_consecutive_failure:<id>` after 3 | job health `failed`, never success |
| producer returns errors / `None` | failure counted, completed work kept | stable reason code (SCH-C09) | partial ≠ healthy |
| optional producer absent | job omitted | info log, catalog `unavailable` | no failed run |
| time box exceeded | `TimeoutError`, lane capacity kept | timeout metrics, job failure | — |
| durable pause owner present | resume refused | `scheduler_resume_blocked` audit, `/api/status` owners | — |
| sync lane still running at stop | shutdown not clean | `shutdown_clean=0` | — |

## Authority

The scheduler may start, pause, resume and time-box registered jobs; it may never place
orders, change Risk limits, credentials or Live state, or clear another owner's pause lease.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| SCH-C01 | `tests/specs/spec_agent_contracts.py::test_scheduler_job_ids_unique` |
| SCH-C02 | `tests/test_advisory_agent_composition.py`, `tests/test_optional_family_cutover.py`, `tests/test_scheduler_learning_absence.py` |
| SCH-C03 | `tests/specs/spec_agent_contracts.py` (AC-S01, AC-S09) |
| SCH-C04, SCH-C05 | `tests/test_layered_pause.py` |
| SCH-C06 | `tests/test_pause_ownership.py` |
| SCH-C07, SCH-C08 | `tests/specs/spec_agent_contracts.py` (AC-B01, AC-B05) |
| SCH-C09 | `tests/test_scheduler_timeouts.py`; `tests/specs/spec_pipeline_tpsl_private_materialization.py` (PL-TPSL-05) |
| SCH-C10 | `tests/test_scheduler_timeouts.py::test_scheduler_escalated_only_result_does_not_count_as_failure`, `::test_scheduler_errors_and_escalated_both_present_still_fails` |
| SCH-C11 | `tests/specs/spec_pi_decision_chain.py` (PI-S01, PI-S07); `tests/specs/spec_agent_contracts.py` (AC-S13) |
| SCH-C12, SCH-C13, SCH-C14 | `tests/test_scheduler_timeouts.py`, `tests/test_scheduler_timeout_fix.py` |
| SCH-C15 | `tests/test_retention.py` |
| SCH-C16, SCH-C17 | `tests/test_scheduler_timeouts.py` |
| SCH-C18 | `tests/test_scheduler_sync_lane_shutdown.py` |
| SCH-C19 | `tests/audit/test_scheduler_no_order_submission.py` |

## Known conformance gaps

- **Timed-out AgentRun status.** The previous contract text required the caller thread to set
  the AgentRun to `timed_out` immediately after a time-box expiry. No scheduler code writes
  `agent_runs.status='timed_out'`; `_cleanup_stale_agent_runs()` (from `health_check`) marks
  runs still `running` after 20 minutes as `stale`. A dashboard may show `running` for up to
  20 minutes after a timeout.
- **Lane count.** The previous text fixed the timeout layer at one async lane plus two sync
  lanes; code has a second async lane dedicated to `news_analyze`
  (`_news_analyzer_timeout_worker`). SCH-C12 states the current four lanes.
- **`guard_promotion` name.** The registration comment says "candidate guards → active", but
  `_run_guard_promotion` only expires learned guards.
- **Out-of-scope residue in `market_data`.** `trading/market_data.py` still collects configured
  stock tokens (`observed_stock_tokens`, xStock auto-removal); stock tokens and RWA perpetuals are
  outside instrument scope (spec 0003).

## Decision references

- Retired jobs, the learning-observability target projection, the SQLite protocol copy and
  the INV-SCH-11 delta note: [scheduler history](../history/scheduler_contract_retired_sections.md).
- Shutdown protocol: #1456, owner decision 2026-10-09. Planner heartbeat decoupling: A5 fix.
  `tpsl_fill_check` timeout: #90. `venue_margin_facts`: #3741. `market_observer` retired: #2933.
- Scope column: owner product scope 2026-10-10 (USDT perpetuals on `bitget_demo`); frozen observers
  and persona hold per [spec 0003](../specs/0003-futures-only-convergence/spec.md); learning jobs per
  [spec 0001](../specs/0001-learning-loop/spec.md).
