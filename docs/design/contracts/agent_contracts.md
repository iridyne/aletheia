# Agent Contracts

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `AC` (operator runtime: `OP`) · **Owner code:** `core/aletheia/agents/__init__.py` (registry), the agent modules named below

## 1. Scope

This Contract owns the input/output and authority boundary of each registered agent
function — what triggers it, what it reads, whether it calls a model, what it writes and
what it may never do — plus the Gateway OS-service lifecycle and the operator runtime.
Job cadence, executor, timeout, scope and layered-pause membership are owned only by the
[Scheduler Contract](scheduler_contract.md) job registry. Entry Plan geometry is owned by the
[Entry Plan contract](entry-plan-contract.md); Serenity research by the
[Serenity contract](serenity_contract.md); model-call identity, fallback and validation by the
[LLM Inference Harness Contract](llm_inference_harness_contract.md). Learning agents are
described as they run today in one line each; their replacement is
[spec 0001](../specs/0001-learning-loop/spec.md).

## 2. Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/agents/__init__.py::register_agent` / `get_agent_fn` | registry | `core/aletheia/main.py` and `core/aletheia/server/app.py` composition (register or `unregister_agent` each optional producer) |
| `core/aletheia/server/routes/data.py::start_agent_run` (`POST /api/agent-runs/start`) | API route | Dashboard and operator-shell manual runs |
| `core/aletheia/server/routes/data.py::get_agents_catalog` (`GET /api/agents/catalog`) | API route | Dashboard agent catalog |
| `core/aletheia/agent_shell/entry_planner.py::run_entry_planner_batch` | call | `core/aletheia/agent_runtime/pi_daemon.py` and the `pi_trading_decision` job |
| `core/aletheia/portfolio/position_manager.py::evaluate_open_positions` | call | `position_manager` job |
| `core/aletheia/gateway/session_service.py`, `core/aletheia/gateway/service_control.py`, `core/aletheia/gateway/managed_service.py` | CLI | `aletheia gateway start|stop|enable|disable|service …` |
| `core/aletheia/_gateway_runner.py` supervisor loop | process | the OS service / `gateway start` |

## 3. Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `agent_runs` | `core/aletheia/agent_runtime/run_logger.py` | run views, health, dashboard | monotonic status (`queued`/`running` → terminal) |
| `knowledge_items` (`research_note`, `post_trade_review`, …) | research agents via `extensions/knowledge/manager.py` | knowledge readers, Learning | append + archive |
| `opportunities` (observation-only rows) | `equity_observer`, `prediction_observer`, `divergence_scanner` | research readers | append-only, `execution_eligible=False` |
| `prediction_runs`, `prediction_markets`, prediction claims | `prediction_observer` | Prediction page | append / replace snapshot |
| `entry_plans`, `candidate_planning_generations`, planning outcomes | Entry Planner / Compiler | Plan execution, workbench | immutable Plan; monotonic planning status |
| `agent_shell_heartbeats` (`agent_id=operator`) | operator-shell | status surface, `OperatorShellMonitor` | replaceable heartbeat |

## 4. Requirements

### Registry and manual start

#### AC-C01 Absent optional producer is unavailable, not failed

`If an optional extensions producer cannot be imported, then composition shall unregister its agent name, the catalog shall mark it runnable=false with blocked_reason agent_unavailable, and the scheduler shall omit its job.`

- WHEN `extensions.research` is absent THEN `equity_observer`, `research_agent`, `risk_critic`, `post_trade_reviewer`, `prediction_observer`, `divergence_scanner`, `dreaming_agent` and `lesson_reviewer` are unregistered and no failed AgentRun is written.
- WHEN an import fails for a module outside the expected optional set THEN the error propagates (a defect is not masked as absence).

#### AC-C02 Manual start is allowlisted

`When a manual agent run is requested, the server shall queue it only for agents in _MANUAL_AGENT_ALLOWLIST and, for the operator service identity, only for agents also in _OPERATOR_SERVICE_AGENT_ALLOWLIST.`

- WHEN `agent_name` is `stock_observer` (retired) or any other unlisted name THEN the response is HTTP 403 `agent_not_in_manual_allowlist`; `get_agent_fn("stock_observer")` raises `ValueError`.
- WHEN a run for the same agent is already queued or running THEN the response is 409 with the in-flight `run_id`.

### Trading agents

#### AC-C03 Canonical Entry Planner wake

`When a durable Candidate planning wake occurs, the event-driven daemon shall run the canonical Entry Planner, and when the 15-minute pi_trading_decision heartbeat fires, the scheduler shall wake that daemon or run the identical run_entry_planner_batch() inline.`

- WHEN the daemon is unavailable THEN the same planner runs inline; there is no second evaluator, debate, PiDecision entry claim or pending-wake path.
- Heartbeat health identities: [Scheduler Contract](scheduler_contract.md) SCH-C11.

#### AC-C04 The model owns only the qualitative hypothesis

`The Entry Planner shall ask the model for one qualitative EntryHypothesis only, and the deterministic Compiler shall own every execution price, size, leverage, stop, target and trigger parameter.`

- WHEN exact facts or capability are missing or stale THEN the outcome is typed `unavailable`, not an economic `NO_PLAN`.
- The Plan freezes source kind/id/school and holding horizon; Proposal attribution preserves them.

#### AC-C05 Execution goes through Risk and Gateway only

`When an EntryPlan is released, the Plan watcher shall submit it only through Risk and then the Execution Gateway, and the watcher shall never invoke a model.`

- WHEN a registered TriggerSpec fires THEN release follows the canonical implementation; resting plans may execute immediately through the same path.
- Durable claim/retry/CAS and Plan execution uniqueness prevent duplicate model ownership and duplicate Proposal/Order submission.

#### AC-C06 Position manager protects existing risk

`The position_manager agent shall evaluate open Positions every 30 seconds for stop_loss, take_profit, trailing and max_hold exits and shall keep running while runtime.paused is set.`

- WHEN the Position is `trade_school="serenity"` THEN trailing is a no-op and the hold cap is `serenity.max_hold_days×24` hours.
- Market-aware signals only wake portfolio review; they never close or tighten directly ([execution_contract.md](execution_contract.md) §10).
- WHEN the result has fresh `errors` THEN the job fails; durable `escalated` findings do not (SCH-C10).

#### AC-C07 Portfolio snapshot has one implementation

`The portfolio_agent shall delegate to portfolio.pnl.compute_and_store_snapshot and shall not compute a second accounting algorithm or rebalance.`

Position path observations (`position_path_observer`) are owned by the
[Position Replay and Lifecycle Story Contract](profit_loop_observability_contract.md) PL-C14–PL-C17.

### Research agents (optional producers)

#### AC-C08 Risk critic writes an advisory note only

`When risk_critic runs, it shall write one medium-confidence research_note (metadata subtype risk_lesson) and shall not write risk_decisions or flag opportunities.`

#### AC-C09 Post-trade reviewer reads complete decision records

`When post_trade_reviewer runs, it shall review recently closed Positions from complete proposal-linked decision records, omitting whole Positions to fit its prompt budget and reporting the omitted count, and shall never truncate a selected Position's rationale.`

- Output: `knowledge_items` with `item_type=post_trade_review`.

#### AC-C10 Scheduler news analysis is a separate bounded job

`The news_analyze job shall analyze at most three pending news rows per run under its own time box, and news_fetch shall never call the analyzer.`

- WHEN an item fails THEN it stays retryable or failed and the job records `partial_item_failure` / `all_items_failed`; no analyzed fact is fabricated.
- WHEN news is associated with an instrument THEN only an exact ticker or asset name matches (`aletheia.data.news_entities.match_crypto_entities`); substrings do not.

#### AC-C11 Research agent and research worker stay advisory

`The research_agent shall write research notes only, and research_worker shall reach Candidates only through publish_serenity_candidates under the Serenity contract.`

### Frozen observers (outside instrument scope)

`equity_observer` (US equities / stock tokens) and `prediction_observer` + `divergence_scanner`
(prediction markets) are still registered and scheduled whenever `extensions.research` and the
data adapters are installed. Their removal is [spec 0003](../specs/0003-futures-only-convergence/spec.md) FC-R04.

#### AC-C12 Observer Opportunities are observation-only

`Every Opportunity constructed by equity_observer or prediction_observer shall carry execution_eligible=False and its scanner_family (equity_daily / prediction_market), and prediction rows shall carry asset_class='prediction' and a valid_until.`

#### AC-C13 Divergence direction fails closed

`If a prediction question's bullish/bearish polarity is unknown, negated or mixed, then divergence_scanner shall produce no crypto direction.`

- Probability direction alone never decides crypto direction (`polarity-v1`).

### Learning agents

`dreaming_daily` (lesson extraction + Serenity enqueue), `lesson_reviewer`, `insight_synthesizer`,
`confidence_calibration` and `guard_promotion` (expires learned guards only) run today as listed in
the [Scheduler Contract](scheduler_contract.md); [spec 0001](../specs/0001-learning-loop/spec.md)
replaces them. Their former detailed IO text is in
[agent contract history](../history/agent_contracts_retired_sections.md).

#### AC-C14 Autonomous learning jobs pass the learning gate

`If a job is in AUTONOMOUS_LEARNING_JOBS and the learning control is absent or its gate returns a reason, then the scheduler shall skip it and record the skip without running the producer.`

- WHEN the learning control is absent THEN every autonomous learning job fails closed with `learning_control_unavailable`, and Core and measurement jobs still complete.

### Permissions and boundaries

#### AC-C15 Two permission ladders

`The system shall rank worker commands by agent.permission_mode (Observe < Paper < Full, default Paper) and operator commands by operator.permission_mode (ReadOnly < Research < PaperAuto < AllowEditing < Auto, default ReadOnly), independently.`

- `Paper` is the demo-account execution permission; there is no simulator behind it.

#### AC-C16 Forbidden intents in every mode

`The agent shell shall refuse every intent in FORBIDDEN_INTENTS (enable_live, modify_credentials, bypass_risk, place_live_order, modify_execution_gateway, delete_knowledge, change_risk_limits, deactivate_kill_switch, modify_permissions, modify_own_prompt, …) in every permission mode.`

#### AC-C17 Trading domains never import concrete venues

`Upper trading domains shall consume normalized capability facts and shall never import concrete exchange clients or concrete execution brokers.`

### Gateway OS-service lifecycle

#### AC-C18 On-demand session service

`When gateway start runs, the system shall create an on-demand user service (launchd on macOS, a systemd user transient service on Linux, an on-demand Task Scheduler task on Windows, S4U first and InteractiveToken only after an S4U access denial) that runs the foreground gateway runner with no login trigger, interval, KeepAlive or Restart policy.`

- WHEN `gateway stop` runs THEN the service is stopped and removed; `status()` queries the live service manager and distinguishes an absent registration from an unreadable manager state.
- The macOS definition carries no credential-bearing environment variables; the service bypasses no live, risk, permission or credential gate.

#### AC-C19 Installation, enablement and runtime are independent

`The gateway service commands shall treat installation, automatic enablement and current runtime as independent facts: start/stop change runtime only, enable/disable change activation only, and install/uninstall are ownership-safe and idempotent.`

- WHEN `gateway service status` runs THEN it reports each fact and never treats an installed or enabled definition as proof the Gateway runs.
- WHEN uninstall targets a running service THEN it refuses; runtime data, config, logs and secrets are preserved.
- The optional `windows-service` backend is explicit, UAC-elevated, LocalService-scoped and never asks for or stores an administrator password; service lifecycle commands are unavailable to LLM-driven agent tools.

#### AC-C20 Git never restarts the Gateway

`Git operations shall never start or restart the Gateway; applying new checkout code requires an explicit owner gateway restart, and legacy Aletheia-managed hooks are cleanup-only while foreign hooks are preserved.`

#### AC-C21 The supervisor types each backend exit

`When the backend exits, the supervisor shall stop supervising on 0, respawn at once and audit gateway_controlled_restart on RESTART_EXIT_CODE (42) without counting a crash, and apply the crash budget, backoff and give-up to any other exit.`

- Exit 42 is produced only by a dashboard restart (`POST /api/gateway/restart`) or a self-heal restart after the full lifespan cleanup ran; the persisted self-heal rate limit is the only throttle.
- WHEN a graceful shutdown hangs past the 15 s port-lost watchdog THEN it is killed and counted as a crash.

### E. Operator runtime

The TS operator-shell (`operator-shell/`) is the sole runtime Operator. Under the
owner-approved #3134 retirement, the Python `pi_operator_loop`, its scheduler/manual
trigger, config-action writer and fallback selector are removed. Disabling
operator-shell does not activate another Operator. Historical `operator_assessment`
and audit records remain readable; they grant no current execution authority.
Diagnosis and lifecycle detail: [The Operator runtime](../architecture/OPERATOR_SHELL.md).

| Entry point | Kind | Owner |
|---|---|---|
| `operator-shell/src/index.ts` → `OperatorLoop.start()` | supervised Node process | `core/aletheia/gateway/operator_shell.py::OperatorShellMonitor` |
| `/api/operator/internal/*` (GET/POST/PATCH) | backend bridge | `core/aletheia/server/routes/operator.py` |
| `GET /api/operator/status` | status projection | [Status Surface Contract](status_surface_contract.md) |

**OP-C01 Least-privilege surface.** The backend shall authenticate the operator
service credential only for the routes declared in
`server/operator_service_auth.py`: the fixed read set (`_OPERATOR_SERVICE_READ_PATHS`),
`GET /api/operator/sessions/*`, the internal bridge, and exactly one mutation,
`POST /api/agent-runs/start`.
- WHEN the shell calls any other route (settings, pause/resume, kill switch, gateway
  restart, order submission) THEN the service credential is refused
  (`tests/test_operator_service_auth.py`).

**OP-C02 No trading authority.** The operator-shell shall not hold Risk, execution,
credential or Live authority and shall not run its own model loop; reasoning goes
through `/api/operator/internal/llm-session` on the Python side.

**OP-C03 Heartbeat.** While running, the shell shall write an `agent_shell_heartbeats`
row with `agent_id=operator` every `ALETHEIA_OPERATOR_HEARTBEAT_SEC` seconds (default
10), carrying mode, permission mode, busy flag, current task and `current_intent`.

**OP-C04 Supervised restart.** If the latest operator heartbeat is older than
`operator.heartbeat_stale_after_sec` once the boot grace window (the same interval
after spawn) has passed and the backend API is ready, then `OperatorShellMonitor`
shall restart the process, record `operator_shell_restart`, and hold a
`operator.restart_after_sec` cooldown.
- WHEN the backend API is not ready THEN staleness is not judged (no restart loop
  during gateway start).

**OP-C05 Permission ladder.** The shell shall materialize a task only when the
current operator permission mode (`ALETHEIA_OPERATOR_PERMISSION_MODE`, default
`ReadOnly`; ladder `ReadOnly < Research < PaperAuto < AllowEditing < Auto`,
`agent_shell/permissions.py`) meets the task type's required level.
- WHEN the level is too low THEN the recommendation exists but no task is created;
  this is a permission fact, not a failure.

**OP-C06 Consumer paths stay live.** Every backend path the shell names shall exist in
the served route table and in the service allowlist, and the allowlist shall name no
dead path (`tests/test_api_consumer_paths.py`).
- WHEN a called route returns 404 THEN the shell records
  `gateway_request_failed:404:…` in `last_error` and degrades its mode; the status
  surface shows that mode rather than inventing health.

The operator permission ladder is independent of the trading permission mode
(`agent.permission_mode`, `Observe < Paper < Full`) that gates Entry Plans.

### Retired agents (market_observer, stock_observer, Ranker/PaperTrader, strategy agents, Python operator loop)

| Name | Replaced by | Detail |
|---|---|---|
| `market_observer` | deterministic Universe → Candidate mainline | [history](../history/agent_contracts_retired_sections.md#retired-agents), [proposal](../proposals/retire_market_observer.md) |
| `stock_observer` | none (rejected by AC-C02) | [history](../history/agent_contracts_retired_sections.md#retired-agents) |
| Ranker / PaperTrader entry queue | Candidate → PlanningGeneration → Planner/Compiler | [history](../history/agent_contracts_retired_sections.md#retired-agents) |
| TradingDecision / Momentum Setup entry chain | canonical Entry Planner (AC-C03) | [history](../history/agent_contracts_retired_sections.md#retired-agents) |
| `strategy_designer`, `backtest_orchestrator` | none | [history](../history/agent_contracts_retired_sections.md#retired-agents) |
| learning contribution evaluator / lifecycle controller | none (read-only `/api/analytics/learning-contribution`) | [history](../history/agent_contracts_retired_sections.md#retired-agents) |
| Python `pi_operator_loop` | operator-shell (§E) | [Operator runtime](../architecture/OPERATOR_SHELL.md) |

Historical runs and rows of these agents stay readable as evidence and grant no current entry,
enrichment or execution authority.

## 5. Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| optional producer absent | unregistered, job omitted | catalog `runnable=false`, `agent_unavailable` | no failed run |
| manual start of an unlisted agent | refused | HTTP 403 `agent_not_in_manual_allowlist` / `agent_not_in_service_allowlist` | — |
| Planner input missing or stale | typed unavailable | planning outcome | `unavailable`, never `NO_PLAN` |
| model call fails | caller degrades; logical call is `error` | harness row, AgentRun errors | no fabricated read |
| divergence polarity unknown | no direction | terminal scanner fact | — |
| learning control absent | learning job skipped / fails closed | `learning_control_unavailable` | Core jobs unaffected |
| backend crash vs controlled restart | crash budget vs immediate respawn | `gateway_controlled_restart` audit | — |

## 6. Authority

Agents may read market and account facts and write their own research, review, planning and
observation rows; they may never place orders outside Risk → Gateway, change Risk limits,
credentials, permissions or Live state, or edit their own prompt base layer.

## 7. Verification

| Requirement | Executable claim / focused test |
|---|---|
| AC-C01 | `tests/test_advisory_agent_composition.py`, `tests/test_optional_family_cutover.py` |
| AC-C02 | `tests/specs/spec_subsystem_observers.py` (OBS-S08); `tests/test_tunnel_auth_agent_runs.py` |
| AC-C03 | `tests/specs/spec_agent_contracts.py` (AC-S13); `tests/specs/spec_pi_decision_chain.py` (PI-S01, PI-S07) |
| AC-C04, AC-C05 | `tests/test_entry_plan_execution.py`; [Entry Plan contract](entry-plan-contract.md) claims |
| AC-C06 | `tests/specs/spec_serenity_holding.py` (SH-B01–B03) |
| AC-C10 | `tests/specs/spec_subsystem_observers.py` (OBS-NEWS-ANALYZER, OBS-NEWS-ENTITY) |
| AC-C12 | `tests/specs/spec_subsystem_observers.py` (OBS-S01–S07, OBS-A01, OBS-A02); `tests/specs/spec_agent_contracts.py` (AC-S06) |
| AC-C13 | `tests/test_divergence_scanner.py` |
| AC-C14 | `tests/test_scheduler_learning_absence.py`; knowledge/guard surfaces `tests/specs/spec_agent_contracts.py` (AC-S04, AC-S05) |
| AC-C15, AC-C16 | `tests/test_agent_shell.py`, `tests/test_architecture_fitness.py` |
| AC-C17 | `tests/specs/spec_agent_contracts.py` (AC-S15) |
| AC-C18 | `tests/specs/spec_agent_contracts.py` (AC-S11); `tests/test_gateway_session_service.py`, `tests/test_session_service.py` |
| AC-C19 | `tests/specs/spec_agent_contracts.py` (AC-S14); `tests/test_service_control.py` |
| AC-C20 | `tests/specs/spec_agent_contracts.py` (AC-S12) |
| AC-C21 | `tests/test_controlled_restart.py` |
| OP-C01–OP-C06 | as cited in §E |
| scheduler claims filed here | AC-S01, AC-S09, AC-B01, AC-B05 → [Scheduler Contract](scheduler_contract.md) SCH-C03, SCH-C07, SCH-C08 |
| model fallback claims filed here | AC-B02, AC-B02b → [LLM Inference Harness Contract](llm_inference_harness_contract.md) LH-C10 |

## 8. Known conformance gaps

- **Frozen observers still run.** `equity_observer` (yfinance + Bitget xStocks) and
  `prediction_observer` (Polymarket) are registered whenever `extensions.research` imports,
  outside the USDT-perpetual instrument scope; removal is spec 0003 FC-R04.
- **`news_analyze` has no mainline consumer.** It runs every 5 minutes on the single `agents`
  worker shared with the Entry Planner, while its only reader is the default-off Serenity
  research worker; spec 0003 FC-R06 makes it Serenity-conditional.
- **Operator-shell copies job cadence.** `operator-shell/src/recommendation_engine.ts` hard-codes
  the cadence of `post_trade_reviewer`, `risk_critic` and `dreaming_agent`, a second copy of the
  Scheduler Contract registry.

## 9. Decision references

- Retired agents, the former job table, cross-cutting copies and learning-agent detail:
  [agent contract history](../history/agent_contracts_retired_sections.md).
- Python operator loop retired: #3134. `market_observer` retired: #2933. Strategy agents removed:
  Position C, 2026-07-02. Supervisor exit typing: #1454, owner decision 2026-10-09. Operator
  service agent allowlist: #1100. Heartbeat decoupling: A5 fix.
