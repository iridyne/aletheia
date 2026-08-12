# REST API Reference

> Complete endpoint index for Aletheia Trading OS backend (FastAPI on :8788, proxied via :8787).
> All endpoints prefixed with `/api/` unless noted. Auth: none (local-only by design).
> Last updated: 2026-07-22 (external-connectivity attribution)

---

## Conventions

- **Base URL**: `http://localhost:8787/api/` (via frontdoor proxy)
- **Remote Control**: The frontdoor listens only on `127.0.0.1` by default. Enabling
  Remote Control requires selecting one or more detected non-loopback IPv4 adapter addresses;
  after a gateway restart, only those addresses expose `:8787`. The internal backend `:8788`
  always remains loopback-only. This transport is independent from Cloudflare Tunnel (for example,
  it can use EasyTier).
- **Auth**: None while tunnel mode is disabled and Remote Control Token protection is off.
  Cloudflare tunnel always requires `X-Aletheia-Token`. Remote Control Token protection defaults
  off for MVP convenience; enabling it requires a write-only configured token and makes every
  private `/api/*` and `/_infra/*` route fail closed without it.
- **Error format**: `{"detail": "message"}` with appropriate HTTP status
- **Pagination**: `?limit=N&offset=M` where supported
- **Timestamps**: ISO 8601 UTC strings in request/response bodies
- **Timing**: API responses include `Server-Timing: app;dur=...`. Requests slower than
  `ALETHEIA_SLOW_REQUEST_MS` (default 500 ms) are logged without query strings or bodies;
  `ALETHEIA_HTTP_TIMING=1` logs every API request for a bounded diagnostic session.

### Status code semantics

| Code | Meaning in this API |
|------|---------------------|
| 200 | OK |
| 400 | Malformed request / invalid parameter |
| 403 | Blocked by permission mode or a safety gate (e.g. live action without unlock) |
| 404 | Entity not found |
| 409 | State conflict (e.g. unlocking live when already unlocked; approving an already-executed proposal) |
| 422 | Pydantic validation failure (FastAPI default) |
| 503 | Backend dependency unavailable (DB/scheduler/exchange/model) — per [UI truthfulness](../../governance/policies/trust_and_change_control.md), surface as `not available`, never as healthy |

> **Response schemas are NOT mirrored here.** Per "docs follow code", response bodies are defined by the Pydantic models in [data_model §4](../data/data_model.md) and the route handlers. This file is the authoritative **endpoint index**; field-level truth lives in code to avoid drift.

### Trust-boundary endpoints (extra requirements)

These endpoints cross a [trust boundary](../../governance/policies/trust_and_change_control.md) and carry obligations beyond a normal route — each MUST write an audit event:

| Endpoint | Requirement | Contract |
|----------|-------------|----------|
| `POST /settings/live-unlock` | explicit `confirm=true`; first unlock needs `acknowledge_first_unlock` | [live_unlock_contract](../contracts/live_unlock_contract.md) |
| `POST /risk/kill-switch` | body `{"active": true\|false, "confirm_release"?: bool}`; human-only. **Engage** (`active=true`): no extra confirm. **Release** (`active=false`): requires `confirm_release=true` — else the route returns 422. | [risk_engine_contract §4](../contracts/risk_engine_contract.md) |
| `POST /settings/exchange-credentials` (+ binance/okx) | write-only; GET never returns raw secret | trust §6.3 secrets |
| `POST /settings/permission-mode` · `/risk` | writes audit; cannot grant `FORBIDDEN_INTENTS` | [live_unlock_contract §6](../contracts/live_unlock_contract.md) |
| `PUT /settings/risk-policies/{mode}` · `POST /settings/risk-policies/copy` | explicit confirmation, optimistic revision check and target-scoped audit; never copies live authority or runtime state | [risk_engine_contract §1.1](../contracts/risk_engine_contract.md) |
| `POST /risk/brakes/{global\|demo\|live}` | engage is immediate; release requires `confirm_release=true`; mode brake never releases global brake | [risk_engine_contract §1.1](../contracts/risk_engine_contract.md) |
| `POST /proposals/{id}/approve-paper` · `/reject` | must preserve original proposal ID (no duplicate row); paper approval accepts only a `pending` proposal whose stored mode is already `paper_internal` and never rewrites exchange intent; isolated preview API returns `403 preview_execution_blocked` before mutation | [execution_contract §1](../contracts/execution_contract.md) |
| `POST /gateway/restart` · `/update/apply` · `/update/rollback` | May control machine-level gateway/code state only outside preview mode; preview returns a stable `403` before side effects | [Development preview](../../operations/development/DEVELOPMENT_PREVIEW.md) |

---

## Core / Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness check (returns `{"status":"ok"}`) |
| GET | `/ready` | Readiness check (DB, scheduler, exchange) |
| GET | `/status` | Full system status (gateway, scheduler, independent external connectivity, exchange, live state, permission) |

`GET /status` exposes `external_connectivity` as a normalized
`reachable|unavailable|recovering|unknown` state with severity, priority, message,
action, and observation time. This probe is independent from the configured
exchange, so a venue-only failure is not reported as a global outage. The route
also exposes `execution_exchange`, `market_data_exchange`,
`exchange_alignment` (`local_paper|aligned|diverged`), and `exchange_diverged` so
operators do not infer the real order target from the retained exchange preference.
| GET | `/version` | Build version info |
| GET | `/stability` | Uptime and restart history |
| GET | `/infra/events` | Infrastructure event log (config changes, restarts, errors); connectivity loss is one root-cause warning, recovery and dependent Collector effects are informational. |
| POST | `/gateway/pause` | Pause scheduler (requires permission >= Operator) |
| POST | `/gateway/resume` | Resume scheduler |

---

## Trading Pipeline

| Method | Path | Description |
|--------|------|-------------|
| GET | `/opportunities` | List recent opportunities (scanner output) |
| GET | `/proposals` | List trade proposals |
| GET | `/proposals/{id}` | Get one proposal by id (404 when absent) |
| POST | `/proposals/{id}/approve-paper` | Approve a pending, already-`paper_internal` proposal; non-paper or unresolved proposals return 409 without mutation; isolated preview API returns 403 before mutation |
| POST | `/proposals/{id}/reject` | Reject proposal with reason |
| GET | `/proposals/{id}/risk-decision` | View risk engine decision for proposal |
| GET | `/positions` | List positions; closed rows expose only reproducible net PnL plus explicit completeness/reason fields |
| GET | `/positions/reconciliation` | Local vs exchange position comparison |
| POST | `/positions/reconcile` | Trigger position reconciliation |
| POST | `/positions/{id}/close` | Owner-initiated manual close of one open position. `paper_internal` uses a fresh configured-venue mark; `*_demo`/`*_testnet` places a real exchange close order. `*_live_limited` returns HTTP 409 `live_position_requires_exchange_close` — close live positions from the exchange UI. Body: `{ order_type: "market"|"limit", limit_price?: number, reason?: string }`. Writes `actor=human`, `close_reason=human_manual_close` or `human_limit_exit`, keeping the attribution separate from AI exit discretion. Guarded by `_PROTECTED_METHOD_PREFIXES POST /api/positions/` when Remote Control / tunnel is enabled. |
| GET | `/trades` | List completed trades (closed positions) |
| GET | `/trades/export` | CSV export of trade history; incomplete legacy PnL is blank and labelled, never exported as a valid tax/PnL number |

## Risk Policy and Brakes

| Method | Path | Description |
|---|---|---|
| GET | `/settings/risk-policies` | Demo/Live policy values, revisions and canonical hashes |
| POST | `/settings/risk-policies/compare` | Read-only field diff |
| POST | `/settings/risk-policies/copy` | Copy common fields with source/target revision guards and explicit confirmation |
| PUT | `/settings/risk-policies/{demo\|live}` | Replace one future-evaluation policy; target identity and Live eligibility remain target-owned |
| POST | `/settings/risk-policies/{demo\|live}/reset-defaults` | Reset only the selected policy with a revision guard and explicit confirmation |
| GET | `/risk/brakes` | Global, Demo and Live brake states |
| POST | `/risk/brakes/{global\|demo\|live}` | Engage or explicitly confirmed release of one brake scope |

---

## Portfolio

| Method | Path | Description |
|--------|------|-------------|
| GET | `/portfolio` | Aggregate portfolio summary (mark-to-market), with explicit realized-economics completeness |
| GET | `/exchange/account` | Raw exchange account state (cached) |
| GET | `/uta/state` | Unified Trading Account state (multi-broker) |
| GET | `/market/portfolio-history` | Portfolio snapshot time series |
| GET | `/market/account-equity-history` | One explicit account-equity snapshot series for the Overview; old local notional rows are excluded |

`GET /portfolio` never adds an economically incomplete close as zero. Completeness fields are scoped to the
same rows as their paired value:

| Field | Meaning |
|---|---|
| `realized_pnl_complete` / `realized_pnl_unavailable_count` | Whether every closed Position included in the returned aggregate has a usable `realized_pnl`, and how many do not |
| `all_realized_pnl_complete` / `all_realized_pnl_unavailable_count` | Same check across all execution modes |
| `today_realized_pnl_complete` / `today_realized_pnl_unavailable_count` | Same check for UTC-today closes in the requested mode |

When a paired `*_complete` field is false, the matching realized value is `null`; `today_pnl` is also `null` if
today's realized component is incomplete. `today_pnl_by_symbol` uses `null` for each affected symbol. Open-position
unrealized PnL remains separate and must not be used as a replacement for the missing realized result.

Closed rows returned by `GET /positions` add `realized_pnl_complete` and
`realized_pnl_unavailable_reason`. The database's original numeric field remains an audit/remediation fact, but the
operator read model re-computes the close from entry/exit price, quantity, fees, direction, and trusted funding.
If it cannot reproduce the stored number, `realized_pnl=null`. `/trades/export` applies the same rule and includes
the same two completeness columns; it writes an empty realized-PnL cell rather than exporting a legacy false value.

---

## Strategies

| Method | Path | Description |
|--------|------|-------------|
| GET | `/strategies` | List all strategies (read-only legacy attribution) |

> Rules engine removed (Position C, 2026-07-02): `/strategies/{id}/gates`,
> `/promote`, `/retire`, `/strategies/summary`, `/strategies/{id}` and
> `PATCH /strategies/{id}/venue` are all gone — strategies are no longer
> created/promoted and `strategy_records` is a read-only legacy table. Only
> `GET /strategies` remains for historical attribution display.

---

## Analytics

| Method | Path | Description |
|--------|------|-------------|
| GET | `/analytics/trade-insights` | Win rate, Sharpe, profit factor, drawdown |
| GET | `/analytics/trade-stats` | Rolling trade statistics |
| GET | `/analytics/strategy-kpis` | Per-strategy KPI breakdown |
| GET | `/analytics/exit-policy-advice` | Exit policy recommendations |
| GET | `/analytics/trade-paths` | Trade decision path visualization data |
| GET | `/analytics/go-live-scorecard` | 10-criteria composite go-live score |
| GET | `/analytics/cost-summary` | LLM and execution cost breakdown |
| GET | `/analytics/confidence-calibration` | ECE calibration by conviction bucket |
| GET | `/analytics/reasoning-feedback` | Per-bucket rejection/win rates |
| GET | `/analytics/filter-controller` | Shadow-only first-exposure cohorts, retry telemetry, bounded fixed-version replays, and preregistered prospective experiment status; never mutates config |
| GET | `/analytics/alpha-attribution` | Read-only five-layer alpha attribution audit (`?days=7/14/30`) |
| GET | `/analytics/thesis-lineage` | Read-only thesis lineage records layered over the trade causal chain (`?position_id=...&order_id=...&days=90`) |
| GET | `/analytics/round1-progress` | Historical Round 1 progress metrics |
| GET | `/analytics/pipeline-funnel` | Pipeline conversion funnel for the distinct Opportunities created inside the rolling `hours` window. Downstream signal, PI, authoritative Risk, and entry-fill counts remain linked to that Opportunity cohort; carrier generations and integrity exceptions are reported separately, and close fills are excluded from entry conversion. `signal_without_terminal` excludes Opportunities already terminal as `reviewed`/`expired`; those remain visible under `source_terminal_without_pi_terminal` and its status breakdown. Stored ISO/SQLite timestamps are parsed as instants rather than compared as text. |
| GET | `/analytics/opportunity-funnel` | Deterministic Opportunity → Operator funnel. The cumulative section is one causal cohort: Opportunities emitted inside the parsed UTC rolling window, split into open/queued/reviewed/expired, with `proposed_count` counting only linked Opportunities from that cohort. `setup_count`/`active_setup_count` deduplicate rows by `symbol + market + direction + scanner_family`; `emitted_count` remains the per-snapshot generation count. `terminal_breakdown` makes expired/reviewed exits explicit, including `snapshot_superseded`, true `ttl_expired`, Operator trade/skip/watch, deterministic review and unknown legacy remainder—raw `expired_count` is compatibility total only. The latest ranker snapshot is a separate point-in-time object with `freshness`, `age_sec`, `stale_after_sec`, executable_count and exclusions; stale/missing snapshots cannot be shown as current health. `current` is the authoritative read-only entry-status projection: it exposes the exact primary stage, stable reason/next trigger, per-candidate durable Ranker blocker, passed-filter count, run id and retry time; `ranker.last_run` exposes the persisted trigger/result and `ranker.wake` exposes bounded in-process wake telemetry. It never re-runs Ranker. Emits `Server-Timing` header with per-phase durations (cohort/snapshot/universe/build_current/total, 2026-07-18). See `/analytics/opportunity-funnel/current-snapshot` for a faster subset when only `current.*` is needed. |
| GET | `/analytics/opportunity-funnel/current-snapshot` | Fast subset of `/opportunity-funnel` returning only `{window_hours, current, config, family_ttl_sec}` — skips the queued/reviewed/expired cohort counts, the candidate-supply snapshot payload parse, and the universe-snapshot lookup. Backed by a 5s process-local TTL cache (`_cached_current_snapshot`) so back-to-back mounts share one compute. Opportunity/filter evidence reads use dedicated status/time and opportunity/run indexes. Emits `Server-Timing`: either `cache_hit;dur=0.0` on hit, or `cohort;dur=… build_current;dur=… total;dur=…` on miss. Used by the Blotter's Candidates panel + status token strip and Overview's Blotter summary strip. |
| GET | `/analytics/operator-decisions` | Final-stage decision log companion to the funnel. Uses parsed UTC decision/rejection timestamps for windowing and ordering, prefers the latest `proposal_rejected` audit time, and classifies only `human+dashboard` as `user_reject` and `system+execution_gateway` as `auto_reject`; missing/inconsistent attribution is `reject_unknown`. A rejected Proposal supersedes its earlier trade-intent row. Administrative `system_cascade` cleanup rows are not presented as Operator decisions. Each item has additive `attribution` for the `system_operator_skip_remediation` policy epoch and producing runtime session/commit; manifest attribution wins, while legacy decisions use the durable epoch time window. `current_context`, `current_policy_counts`, and `historical_policy_counts` prevent old-runtime decisions from being presented as current behavior. `counts` and `total_count` cover the FULL window; when the optional `outcome` (comma-separated) / `symbol` (canonicalized) filters are applied, filtered rows go into `items` while `matched_count` reports how many rows survived the filter pre-truncation. Filter never lies about totals (2026-07-18). Read-only. |
| GET | `/analytics/opportunity-trace/{opportunity_id}` | Full lifecycle trace for a single opportunity — emit → filter_trace → pi_decision → risk_decision → proposal → order → fill → position → close. Thin route over `analytics.opportunity_flow.trace_opportunity_flow`. Powers the Blotter's per-decision drill-down drawer (`DecisionTraceDrawer`, opened from Candidate Ledger row click and Awaiting Sign-off row click). Read-only. |
| GET | `/analytics/family-scorecard` | Scanner family performance |
| GET | `/analytics/shadow-trades` | Shadow trade comparison data |
| GET | `/analytics/strategy-performance` | Per-strategy KPIs (backtest summary removed with rules engine, Position C) |
| GET | `/analytics/learning-funnel` | Learning write/consume activity visibility. Optional PI windows, Lesson, Serenity and contradiction reads expose stable `query_errors`; failed optional counts are `null`/unchecked rather than genuine zero. This remains activity context, not contribution evidence. |
| GET | `/analytics/learning-contribution` | Additive `lco-v1` bounded read-only owner summary for current Learning experiments, identity-matched measurement, cohort integrity, evaluator health, lifecycle readiness and the existing resolver's effective treatment-authority decision. GET performs no authority mutation. `path_states` keeps each measurement-supported path's availability, empty/active state and cadence-aware freshness separate. Operations exposes its own availability/error map and preserves config plus whichever evaluator/audit health sections succeeded. Uses a 30-second monotonic process-local cache and emits `Server-Timing` (`ledger` or `cache_hit`, plus `total`). Legacy activity aggregates remain compatibility context, not contribution evidence; `legacy_query_errors` and nullable counts distinguish read failure from genuine empty or zero activity. No evaluation or mutation is run. |

### Future Learning Contribution API

Only the implemented endpoint indexed above is part of the current API
reference. Additional routes are design proposals in
[`learning_observability.md`](../proposals/learning_observability.md) and must
not appear in current UI, OpenAPI expectations, or owner instructions.
Proposed paths, hypothesis, experiment, and health routes remain specified there; the exposure
detail target is repeated below for API-family visibility.
| GET | `/learning/exposures/{entity_id}` | Bounded indexed influence explanation for an explicit entity type |

The targets are GET-only cached read projections. They run no evaluator or
lifecycle action and expose no trial-admission mutation. The existing
`/analytics/learning-funnel` remains activity-oriented and
`/analytics/alpha-attribution` remains an adjacent diagnostic; neither is the
prospective contribution verdict.

### Opportunity lifecycle projection semantics (`API-S09`)

`/analytics/opportunity-funnel` 的字段语义不是可互换的计数：

- `emitted_count` 是窗口内 per-snapshot generations；`setup_count` 按
  `symbol + market + direction + scanner_family` 去重；`active_setup_count` 只统计仍为
  `open/queued` 的 setup。
- `expired_count` 仅是兼容性状态总数。原因必须读 `terminal_breakdown`，其中
  `snapshot_superseded`、`ttl_expired`、Operator trade/skip/watch、deterministic review
  和 unknown legacy remainder 分开。
- `current` 是 Overview 与 `aletheia inspect entry-status` 的共同只读 projection；它不得
  重跑 Ranker，也不得把 unchanged equity 推断成 pipeline failure。
- candidate 的有效 defer/retry deadline 不得晚于其 Opportunity `valid_until`，包括修复前
  已落库的 legacy defer observation。
- `/analytics/opportunity-trace/{id}` 的 terminal reason 来自 append-only typed evidence；
  历史 `signal_pending` 不能覆盖后来的 snapshot supersede/TTL 终态。

### Analytics outcome-integrity responses（OI-C04, implemented）

The existing trade-statistics, strategy-KPI, family-scorecard, confidence-calibration, cost-summary,
and equity-curve endpoints remain present. Their response contract adds explicit cohort
metadata instead of silently changing one unlabelled number:

| Field | Meaning |
|---|---|
| `raw_event_count` | all matching immutable close/account events |
| `eligible_outcome_count` | genuine outcomes allowed in judgement metrics |
| `excluded_outcome_count` | raw events excluded from judgement |
| `outcome_exclusion_reasons` | stable reason → count map |
| `outcome_semantics_version` | classifier/read-model version used |
| `cohort_state` | `available` / `insufficient` / `unavailable` |
| `clean_window_anchor_id` | versioned identity of the earliest admissible judgement window |
| `clean_window_anchor_iso` | UTC boundary paired with that identity |

Judgement metrics default to the eligible cohort. `/analytics/equity-curve` returns
`raw_recorded_points` and `genuine_points` with stable labels; its compatibility `points` field is an
alias of `genuine_points`. Current unrealized PnL remains a separate field and is not folded into either
realized curve. Cost responses expose verified totals and a separate `raw_recorded_costs` object.
Strategy KPI and family scorecard responses wrap their rows under `strategies` and `families` so the
same cohort metadata can be returned without hiding its scope. Missing
or insufficient genuine data is not serialized as a zero return, zero win rate, or healthy state.

> Backtest endpoints (`/analytics/backtest/runs`, `/runs/{id}`, `/compare/{id}`,
> `/backtest-paper-drift`) and `/analytics/strategy-evidence` were deleted with the
> rules engine (Position C, 2026-07-02).

---

## Market Data

| Method | Path | Description |
|--------|------|-------------|
| GET | `/market/snapshots` | Recent market snapshots (price, volume) |
| GET | `/market/ohlcv` | OHLCV candlestick data |
| GET | `/market/macro` | Macro economic indicators |
| GET | `/market/collector-health` | Bounded per-domain collector status, cadence, timeout/budget-skip counters, overdue state, and attribution against the normalized external-connectivity state. Raw status remains available while `display_status`, `display_severity`, and `attributed_cause` distinguish dependent consequences from independent Collector warnings. |
| GET | `/market/data-coverage` | Cached persisted row/symbol/source coverage and freshness, separate from collector execution health. `Server-Timing` distinguishes cache wait/hit, each domain scan, total route work, and global app wall time. |

`GET /market/ohlcv` preserves its historical candle-array body. The response header
`X-Aletheia-Data-Source` is `memory_cache`, `provider_fetch`, `database_fallback`,
or `unavailable`; `X-Aletheia-Data-Instrument` records the exact requested scope.

---

## Knowledge

| Method | Path | Description |
|--------|------|-------------|
| GET | `/knowledge` | All knowledge items (paginated) |
| GET | `/knowledge/research-notes` | Research notes only |
| GET | `/knowledge/dreaming-logs` | Dreaming agent summaries |
| GET | `/knowledge/strategy-memory` | Strategy-specific memory |
| GET | `/knowledge/guards` | Active Guard storage rows plus separate lifecycle, origin, proposed effect, experiment, assignment, guard-level authority, and actual baseline-effect fields; current measurement-unsupported learned guards report no experiment or assignment. Read failure returns stable `503 guard_read_failed`, never HTTP 200 with an empty collection. |
| GET | `/knowledge/guard-candidates` | Guard candidates with the same authority projection; candidate status never implies enforcement. Read failure returns stable `503 guard_read_failed`, never HTTP 200 with an empty collection. |
| POST | `/knowledge/guard-candidates/{guard_id}/promote` | Manually move a candidate to active storage; a shadow learned Guard becomes `active_observe` and gains no enforcement authority |
| GET | `/knowledge/guards/stats` | Guard activation statistics; read failure returns stable `503 guard_stats_read_failed` rather than an empty success payload |
| GET | `/knowledge/case-studies` | Case study analyses |
| GET | `/knowledge/{item_id}/content` | Full content of knowledge item |
| GET | `/dreaming/summary` | Dreaming session summary; recent knowledge collections contain active items only, while archived/deprecated memories are returned separately |
| GET | `/reviews/summary` | Post-trade review summary |

---

## Agent Runtime

| Method | Path | Description |
|--------|------|-------------|
| GET | `/agent-runs` | List agent run history. `?days=1..90` applies a UTC started-at window; `limit` remains bounded. Preserves raw `outputs_created` and adds `output_events` for Paper Trader output semantics; those Ranker/queue/carrier events explicitly carry `is_operator_decision=false`. |
| GET | `/agent-runs/{id}` | Run detail (status, raw outputs, additive Paper Trader `output_events`, errors) |
| GET | `/agent-runs/{id}/tool-calls` | Tool calls made during run |
| GET | `/agent-runs/{id}/steps` | Step-by-step execution trace |
| POST | `/agent-runs/start` | Queue a manual agent run; returns HTTP 202 with durable `job_id`/`run_id` |

---

## Settings (all POST = save)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/settings` | Full config as JSON plus `learning_path_options`, the registry-backed supported/unsupported paper-influence inventory used by the existing Settings page |
| POST | `/settings/autonomous-learning` | Legacy master Learning-loop toggle. Protected by the tunnel owner token when a tunnel is enabled; missing tunnel secret fails closed and rejected authentication performs no config or audit mutation. |
| POST | `/settings/learning` | Paper-only Learning experiment controls: contribution measurement, lifecycle automation, registry-validated paper influence path allowlist, auto-refreeze, evaluation interval, and final-stage allocation. Unknown paths and paths without a wired consumer, Settings support, or prospective measurement are rejected (including when an old unsupported saved path is re-enabled through the master toggle). Lifecycle automation and the sole measured Paper path default on, but experiment authority still requires explicit owner admission; this endpoint cannot enable live trading. Protected by the tunnel owner token when a tunnel is enabled. |
| POST | `/settings/learning/experiments/{experiment_id}/admit-trial` | Explicit owner admission from a current shadow verdict into one `paper_10` successor. Requires `verdict_id`, `expected_policy_epoch`, bounded `reason`, and `confirm_admission=true`; the server derives the actual epoch, checks every paper gate, writes one deduplicated lifecycle event and audits `human/dashboard`. Protected by the tunnel owner token when a tunnel is enabled. |
| POST | `/settings/economics` | Default-off tokenomics harness controls. Disabling the harness also disables shadow observation and prompt secret-shape detection; this endpoint never enables budget enforcement. |
| POST | `/settings/bypass-distress-gate` | Explicit operator bypass for distress gate checks |

---

## LLM Harness telemetry (read-only)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/economics/status` | Token/cost tracing and viability-audit kill-switch state. |
| GET | `/economics/usage-insights?days=1\|7\|30\|90&group=summary\|operations\|analysis` | Aggregate-only, read-only Usage projection for one exact UTC window. Optional `group` enables independently requested progressive responses: summary (token/pricing/reliability), operations (trend/attribution/tool/budget), and analysis (research yield); omitting it preserves the complete response. The Usage client prioritizes summary, then loads operations and analysis in parallel. Daily trends expose input/output/total tokens plus known/estimated cost without combining provenance. No prompts, responses, raw errors, event limit, cache, or writes. `current_model_cooldown` is explicitly a current in-process fact, not window history. |
| GET | `/economics/pricing/refresh-status` | In-process status for the single-flight LiteLLM public price-map refresh. Never performs network or database work. |
| POST | `/economics/pricing/refresh` | Audited dashboard trigger for a non-blocking LiteLLM refresh. Returns HTTP 202; validated model-level medians create forward-only estimated snapshots and never replace manual pricing or rewrite historical events. |
| GET | `/economics/tokens` | Token-first LLM usage summary for a day window, optionally filtered by `run_id`. Reports provider-supplied input/output/cache/reasoning totals and field availability without requiring a USD price snapshot. |
| GET | `/economics/costs` | Windowed cost summary; USD remains nullable when no immutable price snapshot applies. |
| GET | `/economics/budgets` | Read-only declared budget policies. |
| GET | `/economics/budgets/would-block` | Read-only shadow observations; it does not enforce a budget. |

---
| POST | `/settings/exchange-markets` | Observed symbols list |
| POST | `/settings/primary-exchange` | Primary exchange selection |
| GET | `/settings/paper-account` | Local simulated cash, reserved margin, available balance, equity, economic-completeness state, and recent ledger events. Available only when `paper_internal` is active. |
| POST | `/settings/paper-account/credit` | Add local simulated USDT: `{"amount_usdt": number, "note"?: string}`. Human/audit logged; never touches an exchange. |
| POST | `/settings/paper-account/debit` | Remove local simulated USDT; cannot make cash negative. Human/audit logged. |
| POST | `/settings/paper-account/set-balance` | Set local simulated cash: `{"cash_balance_usdt": number, "note"?: string}`. An explicit zero remains zero; it is not automatically re-funded. |
| POST | `/settings/paper-account/close-spot` | Close every open local spot Position only after all candidates have a fresh exact configured-venue + symbol + spot mark. Never uses `Position.current_price`; preserves trade history and the human/dashboard actor. |
| POST | `/settings/paper-account/close-all` | Close every open local paper Position only after all candidates have a fresh exact configured-venue + symbol + physical-market mark. Never uses `Position.current_price`; preserves trade history and the human/dashboard actor. |
| POST | `/settings/runtime` | Scheduler intervals, timeouts |
| POST | `/settings/app` | App-level settings (timezone, locale) |
| POST | `/settings/pi_daemon` | PI daemon config (debounce, cooldown) |
| POST | `/settings/model` | **Deprecated compatibility adapter** for legacy model/provider settings. The canonical write surface is `/settings/model-profiles`, `/settings/model-integrations`, and `/settings/model-routing`; responses include deprecation and successor-link headers. |
| POST | `/settings/serenity` | Serenity research config (auto_promote, min_score_for_pi) |
| POST | `/settings/guard` | Guard / lesson-promotion config (`auto_promote_enabled`, thresholds) |
| POST | `/settings/operator` | Operator loop config |
| POST | `/settings/regime` | Regime classifier config |
| POST | `/settings/health-config` | Health check thresholds |
| POST | `/settings/retention` | Data retention policies. `agent_runs_days`, `tool_calls_days`, and `runtime_sessions_days` each accept 1–90 days (default 90); other retention fields keep their own bounds. |
| POST | `/settings/telegram` | Telegram bot config |
| POST | `/settings/webhook` | Webhook notification config |
| POST | `/settings/update-config` | Auto-update settings |
| POST | `/settings/permission-mode` | **Worker/agent permission mode only** (Observe/Paper/Full — writes to `cfg.agent.permission_mode`). Operator permission (ReadOnly/Research/PaperAuto/AllowEditing/Auto) is a SEPARATE surface at `cfg.operator.permission_mode` — this endpoint does not change it. See [live_unlock_contract §5](../contracts/live_unlock_contract.md). |
| POST | `/settings/exchange-mode` | Exchange mode (paper_internal/paper_demo/live) |
| POST | `/settings/risk` | Risk limit parameters |
| POST | `/settings/symbols` | Crypto symbol config |
| POST | `/settings/equities` | xStocks / equity config |
| POST | `/settings/trading` | Trading parameters (slippage, sizing) |
| POST | `/settings/market-data` | Market data collection config |
| POST | `/settings/universe` | Universe pipeline config |
| POST | `/settings/strategy_factory` | Legacy strategy-factory config retained for compatibility; rules engine is removed |
| POST | `/settings/historical_data` | Historical data config, including nightly and bounded active-Universe catch-up controls |
| POST | `/settings/event_triggers` | Event-driven trigger config |
| POST | `/settings/news` | News ingestion/settings config |
| POST | `/settings/network` | Network/proxy settings |
| POST | `/settings/network/test` | Test network connectivity |
| GET | `/settings/tunnel/status` | Cloudflare tunnel status |
| POST | `/settings/tunnel` | Tunnel config |
| POST | `/settings/tunnel/start` | Start tunnel |
| POST | `/settings/tunnel/stop` | Stop tunnel |
| GET | `/settings/remote-control/interfaces` | Enumerate local non-loopback IPv4 adapters for Remote Control selection. |
| POST | `/settings/remote-control` | Save selected listener addresses, enable/disable Remote Control, and optionally save/require a write-only owner API token; listener changes require gateway restart. |

`GET /settings/paper-account` adds:

| Field | Meaning |
|---|---|
| `economics_complete` | False when any closed local-paper Position still has unavailable net economics |
| `unsettled_closed_positions_count` | Number of those closed Positions |

Both bulk-close endpoints preflight the complete candidate set before the first
close. If any exact mark is missing, they return HTTP `409` with
`detail.code="bulk_close_mark_preflight_failed"`, an empty
`closed_position_ids`, and per-Position `failed` facts; the batch writes no close
Order, Fill, Outcome, or feedback job. If mark preflight succeeds but a later
materialization fails, the endpoint returns HTTP `500` with
`detail.code="bulk_close_partial_failure"` and both the durable
`closed_position_ids` and per-Position `failed` list, so a partial close is never
reported as all-success.

The completeness check covers the complete local close history and is not capped by a dashboard display limit.
When `economics_complete=false`, `cash_balance_usdt` remains the visible append-only ledger balance but is not
presented as spendable: `available_balance_usdt=0` and `equity_usdt=null`. No `position_closed` cash event is
invented for the missing result. Capacity remains frozen until the close economics and cash settlement are
reconciled.

---

## Exchange Credentials

| Method | Path | Description |
|--------|------|-------------|
| GET | `/bitget/credentials-status` | Bitget API key status (masked) |
| POST | `/settings/exchange-credentials` | Save Bitget credentials |
| POST | `/bitget/test-connection` | Test Bitget API connectivity |
| POST | `/bitget/test-candidate` | Test candidate credentials before saving |
| GET | `/binance/credentials-status` | Binance credentials status |
| GET | `/binance-testnet/credentials-status` | Binance testnet status |
| POST | `/settings/binance-credentials` | Save Binance credentials |
| POST | `/settings/binance-testnet-credentials` | Save Binance testnet credentials |
| POST | `/binance/test-connection` | Test Binance connectivity |
| POST | `/binance-testnet/test-connection` | Test Binance testnet |
| POST | `/binance/test-candidate` | Test candidate Binance credentials |
| GET | `/okx/credentials-status` | OKX credentials status |
| POST | `/settings/okx-credentials` | Save OKX credentials |
| POST | `/okx/test-connection` | Test OKX connectivity |
| POST | `/okx/test-candidate` | Test candidate OKX credentials |

---

## Model / LLM

| Method | Path | Description |
|--------|------|-------------|
| GET | `/model` | Sanitized current model config: stable profile IDs, display names, categories, enabled state, write-only credential status, and independent Default/Deep/Fast role assignments. Never returns API keys or OAuth tokens. |
| GET | `/model/health` | Per-profile health status; disabled profiles are reported without being invoked. |
| GET | `/model/broker` | Provider admission policy plus sanitized queue/concurrency/wait metrics. |
| POST | `/model/ping` | Ping a configured, enabled profile by stable ID. |
| GET | `/model/provider-types` | Registry-backed API, CLI, and OAuth integration definitions, supported protocols, and singleton metadata. |
| POST | `/model/detect-models` | Detect models for a saved profile or an unsaved API candidate. Candidate credentials are write-only. |
| POST | `/model/test-candidate` | Test an unsaved API-profile candidate before saving it. Candidate credentials are write-only. |
| POST | `/settings/model-profiles` | Create one API profile after server-side verification. Multiple profiles may share the same adapter type; `profile_id` is the stable identity and secret namespace. |
| PATCH | `/settings/model-profiles/{profile_id}` | Verify and update an API profile. The stable ID and adapter type are immutable; display name, connection fields, key, model, limits, and enabled state may change. |
| DELETE | `/settings/model-profiles/{profile_id}?confirm=true` | Confirmed API-profile deletion. Clears all Default/Deep/Fast references, deletes its credential, invalidates provider cache/broker state, and audits only sanitized metadata. |
| PATCH | `/settings/model-integrations/{integration_id}` | Configure or enable/disable one registry-approved singleton CLI or OAuth integration. Arbitrary commands and duplicate vendor connections are rejected. |
| POST | `/settings/model-routing` | Independently assign or clear `default_provider`, `deep_provider`, and `fast_provider`. Role assignment does not enable a profile; a disabled assignment is retained but runtime calls fail closed. |
| POST | `/settings/model` | Deprecated compatibility adapter. It remains during the dual-surface phase and emits `Deprecation` and successor `Link` headers; no new caller should use it. |
| POST | `/model/codex/login-start` | Start Codex OAuth flow |
| GET | `/model/codex/login-status` | Codex OAuth status |
| POST | `/model/codex/login-cancel` | Cancel Codex OAuth |
| GET | `/model/codex/models` | List detected Codex/OpenAI model options |
| POST | `/model/openai-oauth/start` | Start OpenAI OAuth flow |
| GET | `/model/openai-oauth/status` | OpenAI OAuth status |
| POST | `/model/openai-oauth/finalize` | Finalize OpenAI OAuth flow |
| POST | `/model/openai-oauth/cancel` | Cancel OpenAI OAuth flow |
| POST | `/model/openai-oauth/disconnect` | Disconnect OpenAI OAuth credentials |
| POST | `/model/claude-oauth/start` | Start Claude OAuth flow |
| POST | `/model/claude-oauth/complete` | Complete Claude OAuth flow |
| GET | `/model/claude-oauth/status` | Claude OAuth status |
| POST | `/model/claude-oauth/cancel` | Cancel Claude OAuth flow |
| POST | `/model/claude-oauth/disconnect` | Disconnect Claude OAuth credentials |

---

## Universe Pipeline

| Method | Path | Description |
|--------|------|-------------|
| GET | `/universe/current` | Current universe composition |
| GET | `/universe/snapshots` | Universe snapshot history |
| GET | `/universe/snapshots/{id}` | Snapshot detail with members |
| GET | `/universe/members/{id}` | Member detail (features, outcomes) |
| GET | `/universe/members/{id}/outcomes` | Market-aware gross/net outcome history, including estimated costs and MFE/MAE |
| POST | `/universe/refresh` | Trigger universe refresh |
| GET | `/universe/policy` | Active universe policies |
| GET | `/universe/calibration` | Cost-aware scanner calibration keyed by family + direction + market type + horizon (`by_cohort_horizon`, `by_cohort`) |
| GET | `/universe/registry/stats` | Registry statistics |

---

## Prediction Markets

| Method | Path | Description |
|--------|------|-------------|
| GET | `/prediction/opportunities` | Prediction-derived opportunities |
| GET | `/prediction/opportunities/{id}` | Opportunity detail |
| GET | `/prediction/status` | Observer status |
| POST | `/prediction/toggle` | Enable/disable observer |
| GET | `/prediction/markets` | Tracked prediction markets |
| GET | `/prediction/markets/{id}/detail` | Market detail |
| GET | `/prediction/markets/{id}/history` | Probability time series |
| GET | `/prediction/markets/{id}/claims` | Claims for market |
| GET | `/prediction/runs` | Observer run history |
| GET | `/prediction/events` | Prediction events |
| GET | `/prediction/events/{id}` | Event detail |
| GET | `/prediction/claims` | All claims |
| GET | `/prediction/preferences` | User preferences |
| PATCH | `/prediction/preferences` | Update preferences |
| POST | `/prediction/refresh` | Force refresh |

---

## Serenity Research

| Method | Path | Description |
|--------|------|-------------|
| GET | `/serenity/status` | Pipeline status overview. Current/last run summaries expose durable `stage` and `lastProgressAt`; `cancel_requested` remains active until worker or restart acknowledgement. |
| GET | `/serenity/themes` | List research themes |
| POST | `/serenity/themes` | Create new theme |
| GET | `/serenity/themes/{id}` | Theme detail |
| GET | `/serenity/themes/{id}/overview` | Theme overview with evidence |
| GET | `/serenity/themes/{id}/detail` | Extended theme detail |
| GET | `/serenity/themes/{id}/claims` | Claims for theme |
| PATCH | `/serenity/themes/{id}/status` | Update theme status |
| POST | `/serenity/themes/{id}/feedback` | Submit feedback |
| POST | `/serenity/themes/{id}/deep-dive` | Trigger deep research |
| POST | `/serenity/themes/{id}/promote` | Promote theme opportunities to PI evaluation queue (Phase 3, Option A — writes signal_pending PiDecisions with signal_source='serenity'; no more state='draft' StrategyRecords) |
| GET | `/serenity/evidence` | List evidence |
| POST | `/serenity/evidence` | Create evidence |
| GET | `/serenity/opportunities` | Serenity opportunities |
| POST | `/serenity/opportunities` | Create opportunity |
| GET | `/serenity/opportunities/{id}` | Opportunity detail |
| GET | `/serenity/opportunities/{id}/research-cycle` | Research cycle data |
| GET | `/serenity/opportunities/{id}/score` | Opportunity score |
| GET | `/serenity/opportunities/{id}/risk-gates` | Risk gate results |
| POST | `/serenity/opportunities/{id}/risk-gates` | Run risk gates |
| GET | `/serenity/opportunities/{id}/replay` | Decision replay |
| POST | `/serenity/opportunities/{id}/replay` | Create replay snapshot |
| GET | `/serenity/opportunities/{id}/post-mortems` | Post-mortems |
| POST | `/serenity/opportunities/{id}/post-mortems` | Create post-mortem |
| POST | `/serenity/runs` | Start research run |
| GET | `/serenity/runs` | List runs with durable stage and last-progress time |
| GET | `/serenity/runs/{id}` | Run detail, lifecycle events, stage, and last-progress time |
| GET | `/serenity/runs/{id}/artifacts` | Run artifacts |
| POST | `/serenity/runs/{id}/retry` | Retry failed run |
| POST | `/serenity/runs/{id}/cancel` | Cancel queued work immediately or request cooperative cancellation of a running worker; retries are idempotent |
| GET | `/serenity/profile` | User research profile |
| PATCH | `/serenity/profile` | Update profile |
| POST | `/serenity/profile/parse` | Parse profile from text |
| GET | `/serenity/claims` | All claims |
| GET | `/serenity/pipeline` | Pipeline summary: recent research runs + active opportunity count (D7) |

---

## Risk / Kill Switch

### 风控查询端点

| Method | Path | Description |
|--------|------|-------------|
| GET | `/risk/boundaries` | 硬性风控限额（只读，`aletheia/server/routes/risk.py:17`） |
| GET | `/risk/status` | Current risk state (ok/warning/blocked) + violations (`risk.py:28`) |
| GET | `/risk/summary` | Operator-facing summary of recent risk rejections and pending boundary proposals. Boundary candidates are scoped to the exact Demo/Live policy identity (`policy_id`, revision, and hash); legacy or stale-policy decisions remain visible as history but cannot produce or suppress a current proposal (`risk.py:74`). |
| GET | `/risk/account-leverage` | Account leverage summary |

### Kill Switch

| Method | Path | Description |
|--------|------|-------------|
| GET | `/risk/kill-switch` | Kill switch current state `{"active": bool}` |
| POST | `/risk/kill-switch` | Engage or release the global trading brake; body: `{"active": true\|false, "reason": "", "confirm_release": bool}`. Releasing (`active=false`) requires `confirm_release=true` — else 422 (see ⚠️ note below). |

> ✅ release 路径要求 `confirm_release=True`，否则返回 HTTP 422。旧缺陷记录仅保存在 [`BACKEND_ISSUES.md`](../../archive/legacy/issues/domain/BACKEND_ISSUES.md) 作为历史证据。

---

## Live Trading (Trust Boundary)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/settings/live-unlock` | Unlock live trading (requires `confirm=true`; first unlock also requires `acknowledge_first_unlock=true`; go-live scorecard must be >=70, green, and have no blockers) |
| POST | `/settings/live-lock` | Lock live trading back to `live_disabled` |

---

## Operator / Command Center

| Method | Path | Description |
|--------|------|-------------|
| POST | `/command` | Natural language command (Agent Shell) |
| GET | `/operator/status` | Operator shell heartbeat status (`status_scope=operator_shell`) plus independent `trading_daemon` liveness. `trading_daemon.state` is `event_driven`, `scheduled_fallback`, `unavailable`, or `disabled`; shell heartbeat state never implies that the trading-decision daemon is running. |
| POST | `/operator/branches` | Create an operator branch/session branch |
| POST | `/operator/active-branch` | Switch active operator branch |
| GET | `/operator/sessions` | List operator sessions |
| GET | `/operator/sessions/{session_id}` | Operator session detail |
| GET | `/operator/sessions/{session_id}/messages` | Operator session messages |
| GET | `/operator/tasks` | List operator tasks |
| GET | `/operator/tasks/{task_id}` | Operator task detail |
| GET | `/operator/commands` | List available operator commands |
| GET | `/operator/extensions` | List operator extensions |
| GET | `/operator/extensions/{extension_id}` | Operator extension detail |
| POST | `/operator/extensions/{extension_id}/enable` | Enable an operator extension |
| GET | `/operator/recommendations` | Operator recommendations/advisory queue |
| GET | `/operator/loop/latest` | Latest operator loop assessment |
| POST | `/operator/loop/trigger` | Manually trigger operator loop |
| GET | `/operator/events` | Operator event feed snapshot |
| GET | `/operator/events/stream` | Operator SSE event stream |
| POST | `/operator/pause` | Pause operator loop |
| POST | `/operator/resume` | Resume operator loop |
| POST | `/operator/wake` | Wake operator loop |
| POST | `/operator/command-preview` | Preview operator command effects |
| POST | `/operator/command` | Execute operator command |
| POST | `/operator/internal/artifacts` | Internal: attach artifact to session |
| POST | `/operator/internal/command-intents` | Internal: record a classified command intent |
| POST | `/operator/internal/commands/execute` | Internal: execute a resolved command |
| POST | `/operator/internal/commands/{command_name}` | Internal: dispatch a typed command by name |
| POST | `/operator/internal/compact` | Internal: compact operator memory into an artifact |
| POST | `/operator/internal/context/query` | Internal: query operator context builder |
| POST | `/operator/internal/decisions` | Internal: record a repair decision |
| POST | `/operator/internal/events` | Internal: append an operator event |
| POST | `/operator/internal/heartbeat` | Internal: operator agent heartbeat |
| POST | `/operator/internal/llm-session` | Internal: multi-turn operator LLM session |
| POST | `/operator/internal/messages` | Internal: append a message to a session |
| POST | `/operator/internal/repair-decisions` | Internal: record a repair decision (typed) |
| POST | `/operator/internal/sessions` | Internal: create an operator session |
| POST | `/operator/internal/tasks` | Internal: create an operator task |
| PATCH | `/operator/internal/tasks/{task_id}` | Internal: update an operator task |

> All `/operator/internal/*` routes above are the operator-shell bridge —
> internal-only, not part of the public UI contract. Kept enumerated so the
> "complete endpoint index" claim (§0) actually holds (was previously
> collapsed to a single wildcard row that hid drift; see #80).

---

## News

| Method | Path | Description |
|--------|------|-------------|
| GET | `/news` | List news items (paginated) |
| GET | `/news/{id}` | News item detail |
| POST | `/news/{id}/analyze` | Trigger LLM analysis of news item |
| POST | `/news` | Create manual news item |

---

## Runtime (Canary/Shadow deployment)

> **Path prefix**: `/api/operator/runtime/`, **not** `/api/runtime/` — this router is mounted under the operator prefix. The paths below are correct; `/runtime/...` (without `operator/`) would 404.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/operator/runtime` | Current runtime state |
| POST | `/operator/runtime/candidate` | Register candidate version |
| POST | `/operator/runtime/shadow` | Start shadow execution |
| POST | `/operator/runtime/promote` | Promote candidate to active |
| POST | `/operator/runtime/drain` | Drain current version |
| POST | `/operator/runtime/rollback` | Rollback to previous version |
| GET | `/operator/runtime/events` | Runtime event log |
| GET | `/operator/runtime/events/stream` | SSE stream of runtime events |

---

## Gateway Management

| Method | Path | Description |
|--------|------|-------------|
| POST | `/gateway/restart` | Restart gateway processes |

---

## Telegram

| Method | Path | Description |
|--------|------|-------------|
| GET | `/telegram/health` | Telegram bot health |

---

## Update System

| Method | Path | Description |
|--------|------|-------------|
| GET | `/update/status` | Update availability |
| GET | `/update/progress` | Read durable packaged-update progress without repeating the network check |
| POST | `/update/apply` | Human-confirmed update request; installed runtimes stage a signed payload in the background |
| POST | `/update/rollback` | Human-confirmed rollback request within the recorded backup window |
| POST | `/update/continuity` | Development-checkout-only runtime candidate materialization; packaged runtimes use the signed update flow |

---

## Capital

| Method | Path | Description |
|--------|------|-------------|
| GET | `/capital/net-deposits` | Net deposit total |
| GET | `/capital/events` | List capital events (deposits/withdrawals) |
| POST | `/capital/events` | Record new capital event |

## Persistence / Data Management

> Internal data management endpoints. Not exposed in frontend UI — used via CLI/API.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/persistence/gateway/status` | PS gateway status |
| GET | `/persistence/gateway/sessions` | PS gateway sessions |
| GET | `/persistence/gateway/reconciliation-reports` | Reconciliation report history |
| GET | `/persistence/events` | Event log |
| GET | `/persistence/orders` | PS-layer orders |
| GET | `/persistence/positions` | PS-layer positions |
| GET | `/persistence/backups` | List backups |
| POST | `/persistence/backups` | Create backup |
| POST | `/persistence/backups/verify` | Verify backup integrity |
| POST | `/persistence/export` | Export data |
| POST | `/persistence/import` | Import data |
| GET | `/persistence/lessons` | List lessons (LessonStore) |
| GET | `/persistence/lessons/{id}` | Get lesson detail |
| POST | `/persistence/lessons/{id}/accept` | Accept a proposed lesson and stamp dry-run/expiry timestamps; requires `X-Aletheia-Token` when tunnel is enabled |
| POST | `/persistence/lessons/{id}/reject` | Reject a lesson; requires `X-Aletheia-Token` when tunnel is enabled |
| POST | `/persistence/lessons/{id}/archive` | Archive a superseded lesson without hard deletion; requires `X-Aletheia-Token` when tunnel is enabled |
| GET | `/persistence/skills` | List skills (SkillStore) |
| GET | `/persistence/policies` | List policies |
| GET | `/persistence/policies/{type}` | Get policy by type |
| GET | `/persistence/policies/{type}/versions` | Policy version history |
| GET | `/persistence/policies/diff` | Policy diff between versions |

## Audit

| Method | Path | Description |
|--------|------|-------------|
| GET | `/audit/events` | Query audit event trail |

## Analytics (additional)

> These endpoints exist in code but are not yet wired to frontend UI.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/analytics/equity-curve` | Portfolio equity curve data |
| GET | `/analytics/exit-policy-advice` | Exit strategy diagnostics |
| GET | `/analytics/trade-paths` | MFE/MAE path analysis |
| GET | `/analytics/family-scorecard` | Strategy family scorecard |
| GET | `/analytics/shadow-trades` | Shadow trade comparison |

## Infrastructure (internal)

> `/_infra/` routes — not proxied via frontend, direct backend access only.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/_infra/livez` | Liveness probe |
| GET | `/_infra/readyz` | Readiness probe |
| GET | `/_infra/doctor` | Full diagnostic report (JSON of doctor probes) |
| GET | `/_infra/status` | Infra-only status snapshot |
| GET | `/version` | Build version info |
| GET | `/stability` | System stability metrics |

### Dev-only (environment-gated)

The dev-reload endpoints below are mounted only when the dev-reload router is
enabled at startup (developer builds) — production deployments deliberately
exclude them. Do not depend on them from UI clients.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/dev/reload/status` | Dev hot-reload status (dev builds only) |
| GET | `/dev/reload/events` | Dev hot-reload event stream (dev builds only) |
