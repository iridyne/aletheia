# REST API Reference

> Endpoint index for the Aletheia backend (FastAPI on :8788, proxied via :8787). Paths are
> relative to `/api/` unless noted. **The route list is decided by code**, not by this file:
> the registered decorators in `core/aletheia/server/` and `extensions/` (mounted in
> `core/aletheia/server/app.py`) are the authority, and `scripts/ci/export_openapi_schema.py`
> exports the live schema. This index routes readers; a row here grants no capability, and a
> route missing here is a documentation gap, not a missing feature.

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

- **Response schema authority**: field-level truth is the route's Pydantic response model in
  code, not this index. `uv run python scripts/ci/export_openapi_schema.py --output docs/_generated/openapi.json`
  exports the current FastAPI schema offline for frontend type generation (`npm run check:api-types`).
- **Symbol format**: request/response bodies use canonical symbols (`BTC/USDT`); venue
  adapters convert at the execution boundary; the frontend displays symbols only through
  `fmtSymbol()` (`frontend/src/lib/format.ts`).

### Status code semantics

| Code | Meaning in this API |
|------|---------------------|
| 200 | OK |
| 400 | Malformed request / invalid parameter |
| 403 | Blocked by permission mode or a safety gate (e.g. live action without unlock) |
| 404 | Entity not found |
| 409 | State conflict (e.g. unlocking live when already unlocked; rejecting an already-executed proposal) |
| 422 | Pydantic validation failure (FastAPI default) |
| 503 | Backend dependency unavailable (DB/scheduler/exchange/model) — per [UI truthfulness](../../governance/policies/trust_and_change_control.md), surface as `not available`, never as healthy |

> **Response schemas are NOT mirrored here.** Response bodies are defined by the Pydantic models in [data_model §4](../data/data_model.md) and the route handlers; field-level truth lives in code to avoid drift.

### Trust-boundary endpoints (extra requirements)

These endpoints cross a [trust boundary](../../governance/policies/trust_and_change_control.md) and carry obligations beyond a normal route. Mutating endpoints MUST write an audit event; the read-only preflight must not mutate state.

**Authentication (applies to every row).** `server/app.py::_tunnel_auth_guard` is default-deny:
when `tunnel.enabled` or `remote_control.require_token` is true, every `/api/*` and `/_infra/*`
path except `_PUBLIC_PATHS` (`/api/ready`, `/_infra/livez`, `/_infra/readyz`) requires the
owner `X-Aletheia-Token` header; a missing configured token fails closed with 401. Unsafe
methods (POST/PUT/PATCH/DELETE) additionally require a same-origin or loopback-dashboard
browser origin, else 403. With both modes off the API is loopback-only and unauthenticated.
The explicit `_PROTECTED_PATHS` / `_PROTECTED_METHOD_PREFIXES` lists are a checklist that the
named paths never move to `_PUBLIC_PATHS`; none of the rows below performs an agent
permission-mode check unless its row says so.

| Endpoint | Requirement | Contract |
|----------|-------------|----------|
| `POST /positions/{id}/close` | Owner manual close; places a real exchange close order in demo modes; `*_live_limited` returns 409 `live_position_requires_exchange_close`; writes `actor=human` attribution. Owner token when remote access is enabled (`_PROTECTED_METHOD_PREFIXES` POST `/api/positions/`) | [execution_contract](../contracts/execution_contract.md) |
| `POST /positions/{id}/recover-parked-close` | Owner release of an owner-unlock-only parked close claim (`execution/pending_close_recovery.py::release_rate_limited_close_claim`). Requires a non-empty `reason` (400 `reason_required`); a claim that is not parked returns 409 with the stable code; releases the claim only (no fill/price/PnL written) and audits `pending_close_owner_authorized_release` with `actor=human`. Owner token when remote access is enabled (POST `/api/positions/` prefix); no permission-mode check | [execution_contract](../contracts/execution_contract.md) |
| `POST /settings/trading-mandate` | Replaces the observe-only Trading Mandate (`trading/trading_mandate.py`); invalid mandate returns 422 `trading_mandate_invalid`; audits `trading_mandate_changed` with before/after. The mandate has no authority to change a trade. Listed in `_PROTECTED_PATHS` | [configuration_contract](../contracts/configuration_contract.md) |
| `POST /settings/fast-trigger` | Sets Fast Trigger `mode` (`off`/`shadow`/`execute`), `max_setups`, `authorization_ttl_minutes`; `execute` grants bounded trigger authority. Validated through `FastTriggerConfig`, audits `fast_trigger_config_changed`, reconfigures the watcher immediately. Owner token when remote access is enabled (default-deny) | [configuration_contract](../contracts/configuration_contract.md) |
| `POST /settings/live-preflight` | private read-only preflight for the selected live broker; no raw credentials returned and no state mutation | [live_unlock_contract](../contracts/live_unlock_contract.md) |
| `POST /settings/live-unlock` | explicit `confirm=true`; first unlock needs `acknowledge_first_unlock`; selected-account evidence and scorecard required | [live_unlock_contract](../contracts/live_unlock_contract.md) |
| `POST /risk/kill-switch` | body `{"active": true\|false, "confirm_release"?: bool}`; human-only. **Engage** (`active=true`): no extra confirm. **Release** (`active=false`): requires `confirm_release=true` — else the route returns 422. | [risk_engine_contract §4](../contracts/risk_engine_contract.md) |
| `POST /settings/exchange-credentials` (+ binance/okx) | write-only; GET never returns raw secret | trust §6.3 secrets |
| `POST /settings/permission-mode` · `/risk` | writes audit; cannot grant `FORBIDDEN_INTENTS` | [live_unlock_contract §6](../contracts/live_unlock_contract.md) |
| `PUT /settings/risk-policies/{mode}` · `POST /settings/risk-policies/copy` | explicit confirmation, optimistic revision check and target-scoped audit; never copies live authority or runtime state | [risk_engine_contract §1.1](../contracts/risk_engine_contract.md) |
| `POST /risk/brakes/{global\|demo\|live}` | engage is immediate; release requires `confirm_release=true`; mode brake never releases global brake | [risk_engine_contract §1.1](../contracts/risk_engine_contract.md) |
| `POST /proposals/{id}/reject` | preserves the original proposal ID; commits rejection, reservation release and audit together; already submitted/executed orders cannot be rejected through this route | [execution_contract §1](../contracts/execution_contract.md) |
| `POST /gateway/restart` · `/update/apply` | May control machine-level gateway/code state only outside preview mode; preview returns a stable `403` before side effects | [Development preview](../../operations/development/DEVELOPMENT_PREVIEW.md) |

---

## Core / Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness check (returns `{"status":"ok"}`) |
| GET | `/ready` | Readiness check (DB, scheduler, exchange) |
| GET | `/status` | Full system status (gateway, scheduler, independent external connectivity, exchange, live state, permission) |
| GET | `/version` | Build version info |
| GET | `/infra/events` | Infrastructure event log (config changes, restarts, errors); connectivity loss is one root-cause warning, recovery and dependent Collector effects are informational. |
| POST | `/gateway/pause` | Pause scheduler (requires permission >= Operator) |
| POST | `/gateway/resume` | Resume scheduler |
| GET | `/ui/shell-bootstrap` | Read · `server/routes/ui.py::shell_bootstrap` — one bounded payload for the app shell (config, status, mode identity); a failed section is reported unavailable, not omitted |
| GET | `/ui/overview-bootstrap` | Read · `server/routes/ui.py::overview_bootstrap` — Overview cards in one request; resolves the selected execution mode once and scopes every aggregate to it |
| GET | `/ws/ticket` | Read · `server/routes/ws.py::issue_ws_ticket` — short-lived single-use WebSocket ticket; protected by the owner-auth middleware because browsers cannot attach `X-Aletheia-Token` to a WebSocket |
| WS | `/ws` | Push · `server/routes/ws.py::ws_realtime` — push-notification socket carrying category names only (no data payloads); `?ticket=` from `/ws/ticket` when remote access is enabled |
| GET | `/config/default-exchange` | Read · `server/routes/data.py::get_default_exchange_config` — preferred instrument exchange for UI ordering; never surfaces credentials |
| GET | `/agents/catalog` | Read · `server/routes/data.py::get_agents_catalog` — agents that may be triggered from the Dashboard (`_MANUAL_AGENT_ALLOWLIST`) |

`GET /status` exposes `external_connectivity` as a normalized
`reachable|unavailable|recovering|unknown` state with severity, priority, message,
action, and observation time. This probe is independent from the configured
exchange, so a venue-only failure is not reported as a global outage. The route
also exposes `execution_exchange`, `market_data_exchange`,
`exchange_alignment` (`aligned|diverged|not_configured|unavailable`), and `exchange_diverged` so
operators do not infer the real order target from the retained exchange preference.

---

## Trading Pipeline

| Method | Path | Description |
|--------|------|-------------|
| GET | `/opportunities` | List recent opportunities (scanner output) |
| GET | `/proposals` | List trade proposals |
| GET | `/proposals/{id}` | Get one proposal by id (404 when absent) |
| POST | `/proposals/{id}/reject` | Reject proposal with reason |
| GET | `/proposals/{id}/risk-decision` | View risk engine decision for proposal |
| GET | `/positions` | List positions (summary projection); closed rows expose only reproducible net PnL plus explicit completeness/reason fields. Detail-only narrative/evidence columns are omitted — see below |
| GET | `/positions/{id}` | One position, complete: every column of the durable row, including the detail-only columns `/positions` projects away. 404 when the id is unknown |
| GET | `/positions/reconciliation` | Local vs exchange position comparison |
| POST | `/positions/reconcile` | Trigger position reconciliation |
| GET | `/orders/{id}/risk-decision` | Read · `server/routes/data.py::get_order_risk_decision` — the exact authoritative RiskDecision an Order consumed; 404 `order_not_found`; `status=not_recorded` when the Order carries no `risk_decision_id` |
| POST | `/positions/{id}/recover-parked-close` | **Write (trust boundary)** · `server/routes/data.py::recover_parked_close_position` — owner release of a parked close claim; see the trust-boundary table |
| POST | `/positions/{id}/close` | Owner-initiated manual close of one open position. External demo/testnet modes place a real exchange close order; archived or unknown modes are unavailable. `*_live_limited` returns HTTP 409 `live_position_requires_exchange_close` — close live positions from the exchange UI. Body: `{ order_type: "market"|"limit", limit_price?: number, reason?: string }`. Writes `actor=human`, `close_reason=human_manual_close` or `human_limit_exit`, keeping the attribution separate from AI exit discretion. Guarded by `_PROTECTED_METHOD_PREFIXES POST /api/positions/` when Remote Control / tunnel is enabled. |
| GET | `/trades` | List orders/trades; optional `entry_plan_id` performs an exact server-side Entry Plan identity filter |
| GET | `/trades/export` | CSV export of trade history; incomplete legacy PnL is blank and labelled, never exported as a valid tax/PnL number |

## Risk Policy and Brakes

| Method | Path | Description |
|---|---|---|
| GET | `/settings/risk-policies` | Demo/Live policy values, revisions and canonical hashes |
| POST | `/settings/risk-policies/copy` | Copy common fields with source/target revision guards and explicit confirmation |
| PUT | `/settings/risk-policies/{demo\|live}` | Replace one future-evaluation policy; target identity and Live eligibility remain target-owned; already-open Positions keep the exit guards of their entry policy (risk_engine_contract §1.1) |
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
Both the Position read model and CSV export expose signed `funding_cost`, its validated `funding_source`, and
`funding_status` (`complete`, `not_applicable`, or `unavailable`). Complete futures rows reconcile as
`gross - entry_fee - exit_fee - funding_cost = realized_pnl`; incomplete futures rows keep funding blank and
unavailable. Spot rows explicitly use `funding_cost=0` and `funding_source=not_applicable`. CSV `slippage_cost`
remains a diagnostic execution-drag field and is not subtracted a second time from fill-based realized PnL.
On a closed Position it is the round trip: every entry and exit leg adds `|fill VWAP - Order price| * quantity`;
it is blank when an exit leg has no Order price (for example an externally observed close).

`GET /positions` is a **summary** projection: it omits `decision_reasoning`, `reconciliation_evidence`,
`reconciliation_identity`, `entry_market_summary`, `last_market_signal_evidence`, and `trailing_json`. These are
per-position narrative/evidence blobs — `decision_reasoning` alone was ~59% of a 200-row payload — and no list
consumer renders them. `GET /positions/{id}` returns the complete row, so a caller drills from a summary row into its
full record using the same `id`. `exit_reasoning` stays on both: it is small, and the causal-chain view reads it from
the list row for an order anchor.

---

## Strategies

The rules engine was removed (Position C, 2026-07-02) and no `/strategies`
route remains; `strategy_records` was dropped in schema v92 (#3702/#3703).
`GET /analytics/strategy-performance` is the remaining per-strategy read.

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
| GET | `/analytics/cost-summary` | Verified economic and execution-cost breakdown, including signed funding and missing-component counts |
| GET | `/analytics/confidence-calibration` | ECE calibration by conviction bucket |
| GET | `/analytics/reasoning-feedback` | Per-bucket rejection/win rates |
| GET | `/analytics/filter-controller` | Shadow-only first-exposure cohorts, retry telemetry, bounded fixed-version replays, and preregistered prospective experiment status; never mutates config |
| GET | `/analytics/filter-controller/persistent` | Fast Learning-page projection of persisted cross-run recommendations, run count/time, schedule state, and an explicit empty reason; never recomputes cohorts, diagnostics, or replays and never mutates config |
| GET | `/analytics/alpha-attribution` | Read-only five-layer alpha attribution audit (`?days=7/14/30`) |
| GET | `/analytics/thesis-lineage` | Read-only thesis lineage records layered over the trade causal chain (`?position_id=...&order_id=...&days=90`) |
| GET | `/analytics/pipeline-funnel` | Pipeline conversion funnel for the distinct Opportunities created inside the rolling `hours` window. Downstream signal, PI, authoritative Risk, and entry-fill counts remain linked to that Opportunity cohort; carrier generations and integrity exceptions are reported separately, and close fills are excluded from entry conversion. `signal_without_terminal` excludes Opportunities already terminal as `reviewed`/`expired`; those remain visible under `source_terminal_without_pi_terminal` and its status breakdown. Stored ISO/SQLite timestamps are parsed as instants rather than compared as text. |
| GET | `/analytics/opportunity-funnel` | Deterministic Opportunity → Operator funnel. The cumulative section is one causal cohort: Opportunities emitted inside the parsed UTC rolling window, split into open/queued/reviewed/expired, with `proposed_count` counting only linked Opportunities from that cohort. `setup_count`/`active_setup_count` deduplicate rows by `symbol + market + direction + scanner_family`; `emitted_count` remains the per-snapshot generation count. `terminal_breakdown` makes expired/reviewed exits explicit, including `snapshot_superseded`, true `ttl_expired`, Operator trade/skip/watch, deterministic review and unknown legacy remainder—raw `expired_count` is compatibility total only. The latest ranker snapshot is a separate point-in-time object with `freshness`, `age_sec`, `stale_after_sec`, executable_count and exclusions; stale/missing snapshots cannot be shown as current health. `current` is the authoritative read-only entry-status projection: it exposes the exact primary stage, stable reason/next trigger, per-candidate durable Ranker blocker, passed-filter count, run id and retry time; `ranker.last_run` exposes the persisted trigger/result and `ranker.wake` exposes bounded in-process wake telemetry. It never re-runs Ranker. Emits `Server-Timing` header with per-phase durations (cohort/snapshot/universe/build_current/total, 2026-07-18). See `/analytics/opportunity-funnel/current-snapshot` for a faster subset when only `current.*` is needed. |
| GET | `/analytics/opportunity-funnel/current-snapshot` | Compatibility projection for the legacy Opportunity funnel. It is not the Entry Workbench Candidate or summary source. |
| GET | `/analytics/operator-decisions` | Terminal Candidate Ledger projection companion to the funnel. Uses parsed UTC decision/rejection timestamps for windowing and ordering, prefers the latest `proposal_rejected` audit time, and classifies only `human+dashboard` as `user_reject` and `system+execution_gateway` as `auto_reject`; missing/inconsistent attribution is `reject_unknown`. A rejected Proposal supersedes its earlier trade-intent row. Administrative `system_cascade` cleanup rows and non-terminal conviction deferrals are not presented as Operator decisions; defer lifecycle facts remain available from `/analytics/opportunity-trace/{opportunity_id}`. Each item has additive `attribution` for the `system_operator_skip_remediation` policy epoch and producing runtime session/commit; manifest attribution wins, while legacy decisions use the durable epoch time window. `current_context`, `current_policy_counts`, and `historical_policy_counts` prevent old-runtime decisions from being presented as current behavior. `counts` and `total_count` cover the FULL window; when the optional `outcome` (comma-separated) / `symbol` (canonicalized) filters are applied, filtered rows go into `items` while `matched_count` reports how many rows survived the filter pre-truncation. Filter never lies about totals (2026-07-18). Read-only. |
| GET | `/analytics/opportunity-trace/{opportunity_id}` | Compatibility lifecycle trace for a legacy opportunity — emit → filter_trace → pi_decision → risk_decision → proposal → order → fill → position → close. Read-only and not the canonical Candidate → Entry Plan detail path. |
| GET | `/analytics/strategy-performance` | Per-strategy KPIs (backtest summary removed with rules engine, Position C) |
| GET | `/analytics/learning-funnel` | Learning write/consume activity visibility. Optional PI windows, Lesson, Serenity and contradiction reads expose stable `query_errors`; failed optional counts are `null`/unchecked rather than genuine zero. This remains activity context, not contribution evidence. |
| GET | `/analytics/learning-contribution` | Additive `lco-v1` bounded read-only owner summary for current Learning experiments, identity-matched measurement, cohort integrity, evaluator health, lifecycle readiness and the existing resolver's effective treatment-authority decision. GET performs no authority mutation. `path_states` keeps each measurement-supported path's availability, empty/active state and cadence-aware freshness separate. Operations exposes its own availability/error map and preserves config plus whichever evaluator/audit health sections succeeded. Uses a 30-second monotonic process-local cache and emits `Server-Timing` (`ledger` or `cache_hit`, plus `total`). Legacy activity aggregates remain compatibility context, not contribution evidence; `legacy_query_errors` and nullable counts distinguish read failure from genuine empty or zero activity. No evaluation or mutation is run. |

### Future Learning Contribution API

Only the implemented endpoint indexed above is part of the current API
reference. Additional routes are design proposals in
[`learning_observability.md`](../proposals/learning_observability.md) and must
not appear in current UI, OpenAPI expectations, or owner instructions.
Proposed paths (hypothesis, experiment, health and exposure-detail routes) remain specified
there; none of them is a current route.

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

The existing trade-statistics, strategy-KPI, confidence-calibration, cost-summary,
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
For each projection, `total_funding_cost` preserves signed funding, `total_economic_costs`
(`entry_fee + exit_fee + funding_cost`) is the reconcilable total, and
`total_execution_drag` keeps the diagnostic `slippage_cost` separate. The legacy `total_costs`
field remains the execution-cost total (`entry_fee + exit_fee + slippage_cost`);
`missing_cost_counts` identifies incomplete
components, and any total requiring a missing component is `null` rather than an apparent zero.
Strategy KPI and family scorecard responses wrap their rows under `strategies` and `families` so the
same cohort metadata can be returned without hiding its scope. Missing
or insufficient genuine data is not serialized as a zero return, zero win rate, or healthy state.

### Current-account execution-mode identity（#2181）

Current-account analytics, System Operator context, CLI reports, Overview, and
tax/PnL export must carry one concrete `execution_mode` through the complete
read. Composite builders resolve the selected mode once and pass it to every
descendant; a failed resolution returns `availability=unavailable` with
`reason=execution_mode_unresolved` before any unscoped aggregate is read.
The selected mode is the effective account identity: a valid live unlock maps
the configured broker to its `*_live_limited` mode; otherwise the configured
paper/demo mode is retained.
`compute_order_stats` and historical accounting apply the same mode predicate
before aggregation, and export applies it before `LIMIT`. Persisted calibration
and reasoning artifacts rate-limit against their own stamped mode. Dashboard
analytics query keys include the mode, and Trades & Positions refreshes the
overview identity every 15 seconds, so a mode switch cannot reuse another
cohort's cached result. `inspect orders --stats` follows the same resolver and
supports `--execution-mode` for an explicit diagnostic override.

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
| GET | `/market/derivatives-facts` | Read · `server/routes/data.py::get_market_derivatives_facts` — per-symbol derivatives readiness (`data/derivatives_facts.py::derivatives_readiness`) |
| GET | `/market/microstructure` | Read · `server/routes/data.py::get_market_microstructure` — latest orderbook depth/spread, funding rate and open interest for one instrument |
| GET | `/market/opportunity-instruments` | Read · `server/routes/data.py::get_market_opportunity_instruments` — residual: instruments that have legacy Opportunity rows (the Opportunity lifecycle has no production producer) |
| GET | `/market/opportunities` | Read · `server/routes/data.py::get_market_opportunities` — residual: legacy Opportunity rows for one instrument (`symbol`, `market_type`, `limit<=500`) |

`GET /market/ohlcv` preserves its historical candle-array body. The response header
`X-Aletheia-Data-Source` is `memory_cache`, `provider_fetch`, `database_fallback`,
or `unavailable`; `X-Aletheia-Data-Instrument` records the exact requested scope.
Any latest view (`before` unset) is served through the shared TTL cache for **any**
`limit` — the cache is keyed on (exchange, symbol, market_type, timeframe, limit),
so two limits are two entries. Historical pages (`before > 0`) are always fetched
fresh, since each window is distinct.

---

## Knowledge

| Method | Path | Description |
|--------|------|-------------|
| GET | `/knowledge` | Knowledge items filtered by validated `type`; `type=all` accepts `offset`, `limit`, `q`, and `status=active/archived/deprecated/all`, returning `{items,total,limit,offset,returned,has_more}` plus an explicitly persisted producer run when available. Content remains detail-on-demand. |
| GET | `/knowledge/dreaming-logs` | Dreaming agent summaries |
| GET | `/knowledge/strategy-memory` | Strategy-specific memory |
| GET | `/knowledge/guards` | Active Guard storage rows plus separate lifecycle, origin, proposed effect, experiment, assignment, guard-level authority, and actual baseline-effect fields; current measurement-unsupported learned guards report no experiment or assignment. Read failure returns stable `503 guard_read_failed`, never HTTP 200 with an empty collection. |
| GET | `/knowledge/guard-candidates` | Guard candidates with the same authority projection; candidate status never implies enforcement. Read failure returns stable `503 guard_read_failed`, never HTTP 200 with an empty collection. |
| POST | `/knowledge/guard-candidates/{guard_id}/promote` | Manually move a candidate to active storage; a shadow learned Guard becomes `active_observe` and gains no enforcement authority |
| GET | `/knowledge/guards/stats` | Guard activation statistics; read failure returns stable `503 guard_stats_read_failed` rather than an empty success payload |
| GET | `/knowledge/{item_id}/content` | Full knowledge content; file-backed Markdown is authoritative when present, with persisted inline `content` used for DB-native knowledge types or missing legacy files |
| GET | `/dreaming/summary` | Dreaming session summary; recent knowledge collections contain active items only, `recent_knowledge_items/total/returned/truncated` cover every active produced type with an explicit 100-item bound, each recent run includes its persisted artifact relations, scheduler job health remains distinct from recorded agent sessions, and archived/deprecated memories are returned separately |
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
| POST | `/settings/learning/experiments/{experiment_id}/admit-trial` | Residual: always returns 409 ("canonical Lab authority requires the Observation owner-action surface"); admission goes through `.../owner-action` below. Listed under Known conformance gaps. |
| POST | `/settings/economics` | Default-off tokenomics harness controls. Disabling the harness also disables shadow observation and prompt secret-shape detection; this endpoint never enables budget enforcement. |
| POST | `/settings/bypass-distress-gate` | Explicit operator bypass for distress gate checks |
| GET | `/settings/learning/experiments` | Read · `server/routes/settings.py::list_learning_experiments` — Learning experiment summaries (path, stage, status); read from the Observation Lab when it is the selected experiment authority. Learning is owned by [spec 0001](../specs/0001-learning-loop/spec.md) |
| GET | `/settings/learning/experiments/{experiment_id}` | Read · `server/routes/settings.py::get_learning_experiment` — one experiment's lifecycle/operator view; 503 when the Lab is selected but unavailable |
| POST | `/settings/learning/experiments/{experiment_id}/owner-action` | Write · `server/routes/settings.py::queue_observation_owner_action` — queue one owner action (`withdraw`, `refreeze`, `invalidate`, `revoke`, `admit`, `close`, `promote`, `hold`, `finalize`, `adopt`) for asynchronous Lab application; requires `reason` and `confirm=true`; deduplicated by content hash; reports transport state, never Lab success; 409 when the Lab is not the selected authority |
| POST | `/settings/trading-mandate` | **Write (trust boundary)** · `server/routes/settings.py::set_trading_mandate` — replace the observe-only Trading Mandate; see the trust-boundary table |
| POST | `/settings/fast-trigger` | **Write (trust boundary)** · `server/routes/settings.py::update_fast_trigger_config` — Fast Trigger mode/authority; see the trust-boundary table |
| POST | `/settings/controller` | Write · `server/routes/settings.py::update_controller_config` — filter-controller shadow settings (`observe_interval_hours` 1–168, `min_samples_gate` 5–500, `max_step` 0.01–0.5, else 400); reschedules the controller job when shadow/interval fields change; audits `controller_config_changed` |
| GET | `/settings/universe/candidate-selection` | Read · `server/routes/settings.py::get_candidate_selection_status` — the configured Candidate selection authority (recovers an interrupted authority transition before reading) |
| POST | `/settings/exchange-markets` | Observed symbols list |
| POST | `/settings/primary-exchange` | Primary exchange selection |
| POST | `/settings/runtime` | Scheduler intervals, timeouts |
| POST | `/settings/app` | App-level settings (timezone, locale) |
| POST | `/settings/pi_daemon` | PI daemon config (debounce, cooldown) |
| POST | `/settings/serenity` | Serenity research config (auto_promote, min_score_for_pi) |
| POST | `/settings/guard` | Current guard config (`enabled`, `enforce_in_paper_ranker`, `max_score_penalty`). Retired `auto_promote_enabled` / `min_matching_trades` writes return 422; historical values remain readable. |
| POST | `/settings/regime` | Regime classifier config |
| POST | `/settings/health-config` | Health check thresholds |
| POST | `/settings/retention` | Data retention policies. `agent_runs_days`, `tool_calls_days`, and `runtime_sessions_days` each accept 1–90 days (default 90); other retention fields keep their own bounds. |
| POST | `/settings/telegram` | Telegram bot config |
| POST | `/settings/webhook` | Webhook notification config |
| POST | `/settings/update-config` | Auto-update settings |
| POST | `/settings/permission-mode` | **Worker/agent permission mode only** (Observe/Paper/Full — writes to `cfg.agent.permission_mode`). Operator permission (ReadOnly/Research/PaperAuto/AllowEditing/Auto) is a SEPARATE surface at `cfg.operator.permission_mode` — this endpoint does not change it. See [live_unlock_contract §5](../contracts/live_unlock_contract.md). |
| POST | `/settings/exchange-mode` | Sets `exchange.mode` to `demo` or `live`; Live unlock remains separate |
| POST | `/settings/risk` | Risk limit parameters |
| POST | `/settings/symbols` | Crypto symbol config |
| POST | `/settings/equities` | **Frozen** (equities out of scope, spec 0003 FC-R04): observed US equity tickers for the frozen `equity_observer`; observation-only, never traded |
| POST | `/settings/trading` | Trading parameters (slippage, sizing) |
| POST | `/settings/market-data` | Market data collection config |
| POST | `/settings/universe` | Universe pipeline config |
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

---

## LLM Harness telemetry (read-only)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/economics/status` | Token/cost tracing and viability-audit kill-switch state. |
| GET | `/economics/usage-insights?days=1\|7\|30\|90&group=summary\|operations` | Aggregate-only, read-only Usage contract v2 for one exact UTC window. `summary` returns the window, five-value overview, complete-prior-window comparison, observed token composition, and hour/day/week trend. `operations` returns fixed Agent/Model/Provider/Tool breakdowns, compact reliability, active cooldowns, and configured budgets. Unavailable numbers are `null`; token subtypes are omitted when never observed. Costs use provider-reported USD first, otherwise the immutable catalog snapshot effective at the event instant. No prompts, responses, raw errors, provenance/gap diagnostics, research yield, cache, or writes. |
| GET | `/economics/pricing/refresh-status` | In-process status for the single-flight LiteLLM public price-map refresh. Never performs network or database work. |
| POST | `/economics/pricing/refresh` | Audited dashboard trigger for a non-blocking refresh from LiteLLM's public GitHub price catalog. Returns HTTP 202; provider-independent model-level medians and measurable provider-native tool rates create forward-only estimated snapshots. This catalog is the only model/tool price source; no manual writer exists and historical events are not rewritten. |
| GET | `/economics/tokens` | Token-first LLM usage summary for a day window, optionally filtered by `run_id`. Reports provider-supplied input/output/cache/reasoning totals and field availability without requiring a USD price snapshot. |
| GET | `/economics/costs` | Windowed cost summary; USD remains nullable when no immutable price snapshot applies. |
| GET | `/economics/budgets` | Read-only declared budget policies. |
| GET | `/economics/budgets/would-block` | Read-only shadow observations; it does not enforce a budget. |
| GET | `/economics/costs/by-provider` | Read · `server/routes/economics.py::costs_by_provider` — per-provider token breakdown and UTC daily series over `runtime_cost_events` (`days` 1–365, optional `run_id`) |
| GET | `/economics/costs/by-tool` | Read · `server/routes/economics.py::costs_by_tool` — per-tool call rollup for the Usage dashboard |
| GET | `/economics/viability/reports` | Read · `server/routes/economics.py::viability_reports` — stored viability reports (`scope=system\|operator`), never recomputed |
| GET | `/economics/model-health` | Read · `server/routes/economics.py::model_health` — per-(provider, model) cooldown state from the fallback subsystem |
| GET | `/economics/context-artifacts` | Read · `server/routes/economics.py::context_artifacts` — residual: prompt-free context-consumption audit written for the retired Market Observer |
| GET | `/economics/pricing` | Read · `server/routes/economics.py::pricing` — catalog-derived model and provider-native tool pricing rows |

---

## Exchange Credentials

> Bitget is the only in-scope venue. The Binance and OKX rows are **frozen** residual surfaces:
> they remain registered and keep the write-only credential invariant, but they are not a
> supported venue.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/bitget/credentials-status` | Bitget API key status (masked) |
| POST | `/settings/exchange-credentials` | Save Bitget credentials |
| POST | `/bitget/test-connection` | Test Bitget API connectivity |
| POST | `/bitget/test-candidate` | Test candidate credentials before saving; rejected probes return a stable secret-safe error code |
| GET | `/binance/credentials-status` | Binance credentials status |
| POST | `/settings/binance-credentials` | Save Binance credentials |
| POST | `/binance/test-connection` | Test Binance connectivity |
| POST | `/binance/test-candidate` | Test candidate Binance credentials |
| GET | `/okx/credentials-status` | OKX credentials status |
| POST | `/settings/okx-credentials` | Save OKX credentials |
| POST | `/okx/test-connection` | Test OKX connectivity |
| POST | `/okx/test-candidate` | Test candidate OKX credentials |

---

## Model / LLM

| Method | Path | Description |
|--------|------|-------------|
| GET | `/model` | Sanitized current model config: stable profile IDs, display names, categories, configured/enabled state, write-only credential status, and independent Default/Deep/Fast role assignments. Never returns API keys or OAuth tokens. Existing config files migrate by defaulting the verification fields to unknown; no routing behavior changes. |
| GET | `/model/health` | Canonical per-profile fact projection. Rows are emitted for configured profiles and registry-backed unconfigured provider types, so clients never infer state from omission. `configured`, `enabled`, and `routing_roles` are independent facts; `reachable` is the latest probe result (`null` when unobserved), while `verified` is only set by an explicit inference verification persisted for the current `verification_identity`. `state` is one of `not_configured`, `disabled`, `unknown`, `stale`, `unavailable`, `reachable`, or `verified`; `reason_code` is stable (`missing_configuration`, `disabled`, `no_observation`, `verification_invalidated`, `reachable`, `verified`, `authentication_failure`, `network_failure`, `provider_rejection`, `cooldown`, `not_installed`, or `probe_failure`) and `error` retains diagnostic detail. `observed_at`, `age_seconds`, `freshness` (`fresh`/`stale`/`unknown`), `cached`, and server-owned `stale_after_seconds` (currently 30) remain available. The legacy `available` field aliases `reachable` for migration and must not be used as verification evidence. |
| GET | `/model/broker` | Provider admission policy plus sanitized queue/concurrency/wait metrics. |
| POST | `/model/ping` | Explicitly verify the current default profile with a bounded inference call; records a timestamped identity-bound success or failure without exposing credentials. |
| GET | `/model/provider-types` | Registry-backed generic HTTP API definitions and supported protocols. Retired CLI, native, subscription OAuth and proprietary workspace types are absent; current config/probe requests reject obsolete workspace fields. |
| POST | `/model/detect-models` | Detect models for a saved profile or an unsaved API candidate. Candidate credentials are write-only. |
| POST | `/model/test-candidate` | Test an unsaved API-profile candidate before saving it. Passive checks report reachability; `probe=true` records identity-bound explicit verification only when it matches a saved profile. Candidate credentials are write-only. |
| POST | `/settings/model-profiles` | Create one API profile after server-side verification. Multiple profiles may share the same adapter type; `profile_id` is the stable identity and secret namespace. |
| PATCH | `/settings/model-profiles/{profile_id}` | Verify and update an API profile. The stable ID and adapter type are immutable; display name, connection fields, key, model, limits, and enabled state may change. |
| DELETE | `/settings/model-profiles/{profile_id}?confirm=true` | Confirmed API-profile deletion. Clears all Default/Deep/Fast references, deletes its credential, invalidates provider cache/broker state, and audits only sanitized metadata. |
| POST | `/settings/model-routing` | Independently assign or clear `default_provider` (endpoint ID), `deep`, and `fast` (each `{endpoint_id, model_id}` or null). Tier assignment verifies the exact model and rejects concurrent endpoint identity changes. It does not enable a profile; disabled assignments remain unavailable at runtime. Old provider-only tier fields are rejected. |

---

## Universe Pipeline

| Method | Path | Description |
|--------|------|-------------|
| GET | `/universe/current` | Current universe composition |
| GET | `/universe/snapshots` | Universe snapshot history |
| GET | `/universe/snapshots/{id}` | Snapshot detail with members |
| GET | `/universe/workbench` | `current_candidates` projects every member of the current valid Snapshot independently of optional Planning Generation/Entry Plan state; bounded active Entry Plans include per-instrument `monitoring_state`, hash-bound channel/generation evidence, while `candidate_attention` remains a separate ledger. Each section is `null` plus unavailable on read failure and `[]` only after a successful empty read; fallback instruments never imply Candidates. (`API-S13`) |
| GET | `/universe/entry-plans/{plan_id}` | Exact active or terminal Entry Plan; validates its stored V1 contract and content hash before returning it |
| GET | `/universe/candidates/{candidate_id}` | Exact Candidate attention and planning evidence by identity; elapsed soft/hard attention deadlines are projected immediately as `ttl_expired` / `max_attention_age` without mutating Entry Plans or Orders |
| GET | `/universe/planning-history` | Stable `(updated_at,id)` cursor pagination across Candidate planning generations and Entry Plans (`limit`, optional `before`), using the same effective attention-expiry projection as Candidate detail |
| GET | `/universe/planning-generations/{generation_id}` | Read · `server/routes/universe.py::get_planning_generation` — one Candidate planning generation with its planner input, requalification and hypothesis blobs; 404 when unknown |
| GET | `/universe/members/{id}` | Member detail (features, outcomes) |
| POST | `/universe/refresh` | Trigger universe refresh |
| GET | `/universe/policy` | Config-derived universe policy |
| GET | `/universe/calibration` | Cost-aware scanner calibration keyed by family + direction + market type + horizon (`by_cohort_horizon`, `by_cohort`) |
| GET | `/universe/registry/stats` | Registry statistics |

---

## Prediction Markets

> **Frozen — out of product scope.** Prediction markets are outside the active instrument scope.
> These routes are residual code: `server/app.py` mounts `routes/prediction.py` unconditionally
> today. Target per [spec 0003 FC-R04](../specs/0003-futures-only-convergence/spec.md#fc-r04-out-of-scope-producers)
> (draft): not registered unless `research.frozen_observers` is enabled. Do not build on them.

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
| POST | `/serenity/themes/{id}/promote` | Write · `server/routes/data.py::promote_serenity_theme` — publishes the theme's active Serenity opportunities as Candidates through the canonical Candidate path (`trading/serenity_publication.py::publish_serenity_candidates`), then wakes the PI daemon. 404 unknown theme; 409 when the theme has no active opportunities or publication rejects the source (for example missing supporting evidence or a failed risk gate). Writes no PiDecision |
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
| GET | `/risk/status` | Current risk brake state (`blocked`/`warning`/`not_evaluated`) + violations; `latest_risk_decision` is immutable, explicitly historical evidence and does not establish current health (`risk.py:28`) |
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
| POST | `/settings/live-preflight` | Read-only private-account check for the selected live broker; returns sanitized identity, permission, and freshness evidence |
| POST | `/settings/live-unlock` | Unlock live trading (requires `confirm=true`; first unlock also requires `acknowledge_first_unlock=true`; selected-account evidence and go-live scorecard must pass) |
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
| GET | `/operator/loop/latest` | Read historical Python Operator assessments (producer retired) |
| GET | `/operator/events` | Operator event feed snapshot |
| GET | `/operator/events/stream` | Operator SSE event stream |
| POST | `/operator/pause` | Pause operator loop |
| POST | `/operator/resume` | Resume operator loop |
| POST | `/operator/wake` | Wake operator loop |
| POST | `/operator/command-preview` | Preview operator command effects |
| POST | `/operator/command` | Execute operator command |
| POST | `/operator/internal/artifacts` | Internal: attach artifact to session |
| POST | `/operator/internal/commands/execute` | Internal: execute a resolved command |
| POST | `/operator/internal/compact` | Internal: compact operator memory into an artifact |
| POST | `/operator/internal/context/query` | Internal: query operator context builder |
| POST | `/operator/internal/events` | Internal: append an operator event |
| POST | `/operator/internal/heartbeat` | Internal: operator agent heartbeat |
| POST | `/operator/internal/llm-session` | Internal: multi-turn operator LLM session |
| POST | `/operator/internal/messages` | Internal: append a message to a session |
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
| GET | `/update/status` | Read local update state without a network check |
| POST | `/update/check` | Explicitly check remote update availability |
| GET | `/update/progress` | Read durable packaged-update progress without repeating the network check |
| POST | `/update/apply` | Human-confirmed update request; installed runtimes stage a signed payload in the background |

---

## Capital

| Method | Path | Description |
|--------|------|-------------|
| GET | `/capital/net-deposits` | Net deposit total |
| GET | `/capital/events` | List capital events (deposits/withdrawals) |
| POST | `/capital/events` | Record new capital event |

## Export

| Method | Path | Description |
|--------|------|-------------|
| GET | `/export/catalog` | Read · `server/routes/export.py::get_export_catalog` — preset semantics, row counts and dataset classifications |
| POST | `/export/download` | Read-only side effect (temporary file) · `server/routes/export.py::download_export` — streams a ZIP of the requested CSV datasets and an optional redacted config copy; secrets are never included; ZIPs are analysis output, never restore input (400 when nothing is selected) |

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
| GET | `/persistence/positions/{position_id}` | Read · `server/routes/persistence.py::get_position` — one PS-layer position with its full `raw_json` payload |
| GET | `/persistence/backups` | List backups |
| POST | `/persistence/backups` | Create backup |
| POST | `/persistence/backups/verify` | Verify backup integrity |
| POST | `/persistence/export` | Export data |
| POST | `/persistence/import` | Preview or merge namespaced research only; Core trading and retired PS runtime tables are rejected before writes, including artifact copying. Proposal/runtime snapshot exports are not restore inputs. |
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

## Infrastructure (internal)

> `/_infra/` routes and the backend root `/` carry no `/api/` prefix. `/_infra/*` is not proxied via
> the frontend; direct backend access only.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/_infra/livez` | Liveness probe |
| GET | `/_infra/readyz` | Readiness probe |
| GET | `/_infra/doctor` | Full diagnostic report (JSON of doctor probes) |
| GET | `/_infra/status` | Infra-only status snapshot |
| GET | `/` (no `/api/` prefix) | `server/app.py::dashboard_unavailable` — mounted only when the built dashboard is missing (broken/direct-start case); returns a self-refreshing 503 page; excluded from OpenAPI |
| GET | `/version` | Build version info |

### Dev-only (environment-gated)

The dev-reload endpoints below are mounted only when the dev-reload router is
enabled at startup (developer builds) — production deployments deliberately
exclude them. Do not depend on them from UI clients.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/dev/reload/status` | Dev hot-reload status (dev builds only) |
| GET | `/dev/reload/events` | Dev hot-reload event stream (dev builds only) |

---

## Frozen and residual surfaces

Registered routes that serve a closed scope or a retired producer. They are listed so the index
matches code; none is a supported capability.

| Surface | State |
|---|---|
| `/prediction/*` (15 routes) | Frozen, out of scope; spec 0003 FC-R04 target is not registered by default |
| `POST /settings/equities` | Frozen with `equity_observer` (spec 0003 FC-R04) |
| `/binance/*`, `/okx/*`, `POST /settings/{binance,okx}-credentials` | Frozen venues; Bitget is the only in-scope venue |
| `/opportunities`, `/market/opportunities`, `/market/opportunity-instruments`, `/analytics/opportunity-funnel*`, `/analytics/operator-decisions`, `/analytics/opportunity-trace/{id}` | Residual reads of the legacy Opportunity lifecycle, which has no production producer; the Candidate → Entry Plan path is under `/universe/*` |
| `/economics/context-artifacts` | Residual audit for the retired Market Observer |
| `/settings/learning*`, `/analytics/learning-*`, `/knowledge/guard*` | Learning is owned by [spec 0001](../specs/0001-learning-loop/spec.md); rows describe current code only |

## Known conformance gaps

| Gap | Evidence |
|---|---|
| `POST /settings/learning/experiments/{id}/admit-trial` is a route that only returns 409 and points callers at `.../owner-action`; under the no-shim rule it should be deleted | `server/routes/settings.py::admit_learning_trial` |
| Prediction routes are mounted unconditionally; `research.frozen_observers` does not exist in config | `server/app.py` `app.include_router(prediction_router)`; `grep -rn frozen_observers core` has no hit |

## Route coverage check

The table rows above were diffed against every `@router.<method>` / `@app.<method>` /
`add_api_route` decorator under `core/aletheia/server/` and `extensions/` by a one-shot AST
script (router prefixes resolved, path parameters normalised); on 2026-10-10 it reported 316
code routes, 316 documented, zero missing in either direction.
