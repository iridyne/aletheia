# REST API Reference

> Complete endpoint index for Aletheia Trading OS backend (FastAPI on :8788, proxied via :8787).
> All endpoints prefixed with `/api/` unless noted. Auth: none (local-only by design).
> Last updated: 2026-07-03 (Learning SDD/API alignment)

---

## Conventions

- **Base URL**: `http://localhost:8787/api/` (via frontdoor proxy)
- **Auth**: None (local network only; tunnel requires separate Cloudflare auth)
- **Error format**: `{"detail": "message"}` with appropriate HTTP status
- **Pagination**: `?limit=N&offset=M` where supported
- **Timestamps**: ISO 8601 UTC strings in request/response bodies

### Status code semantics

| Code | Meaning in this API |
|------|---------------------|
| 200 | OK |
| 400 | Malformed request / invalid parameter |
| 403 | Blocked by permission mode or a safety gate (e.g. live action without unlock) |
| 404 | Entity not found |
| 409 | State conflict (e.g. unlocking live when already unlocked; approving an already-executed proposal) |
| 422 | Pydantic validation failure (FastAPI default) |
| 503 | Backend dependency unavailable (DB/scheduler/exchange/model) — per [UI truthfulness](../governance/trust_and_change_control.md), surface as `not available`, never as healthy |

> **Response schemas are NOT mirrored here.** Per "docs follow code", response bodies are defined by the Pydantic models in [data_model §4](data_model.md) and the route handlers. This file is the authoritative **endpoint index**; field-level truth lives in code to avoid drift.

### Trust-boundary endpoints (extra requirements)

These endpoints cross a [trust boundary](../governance/trust_and_change_control.md) and carry obligations beyond a normal route — each MUST write an audit event:

| Endpoint | Requirement | Contract |
|----------|-------------|----------|
| `POST /settings/live-unlock` | explicit `confirm=true`; first unlock needs `acknowledge_first_unlock` | [live_unlock_contract](live_unlock_contract.md) |
| `POST /risk/kill-switch` | body `{"active": true\|false, "confirm_release"?: bool}`; human-only. **Engage** (`active=true`): no extra confirm. **Release** (`active=false`): requires `confirm_release=true` — else the route returns 422. | [risk_engine_contract §4](risk_engine_contract.md) |
| `POST /settings/exchange-credentials` (+ binance/okx) | write-only; GET never returns raw secret | trust §6.3 secrets |
| `POST /settings/permission-mode` · `/risk` | writes audit; cannot grant `FORBIDDEN_INTENTS` | [live_unlock_contract §6](live_unlock_contract.md) |
| `POST /proposals/{id}/approve-paper` · `/reject` | must preserve original proposal ID (no duplicate row) | [execution_contract §1](execution_contract.md) |

---

## Core / Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness check (returns `{"status":"ok"}`) |
| GET | `/ready` | Readiness check (DB, scheduler, exchange) |
| GET | `/status` | Full system status (gateway, scheduler, exchange, live state, permission) |
| GET | `/version` | Build version info |
| GET | `/stability` | Uptime and restart history |
| GET | `/infra/events` | Infrastructure event log (config changes, restarts, errors) |
| POST | `/gateway/pause` | Pause scheduler (requires permission >= Operator) |
| POST | `/gateway/resume` | Resume scheduler |

---

## Trading Pipeline

| Method | Path | Description |
|--------|------|-------------|
| GET | `/opportunities` | List recent opportunities (scanner output) |
| GET | `/proposals` | List trade proposals |
| GET | `/proposals/{id}` | Get one proposal by id (404 when absent) |
| POST | `/proposals/{id}/approve-paper` | Approve proposal for paper execution |
| POST | `/proposals/{id}/reject` | Reject proposal with reason |
| GET | `/proposals/{id}/risk-decision` | View risk engine decision for proposal |
| GET | `/positions` | List positions (open/closed, filterable) |
| GET | `/positions/reconciliation` | Local vs exchange position comparison |
| POST | `/positions/reconcile` | Trigger position reconciliation |
| GET | `/trades` | List completed trades (closed positions) |
| GET | `/trades/export` | CSV export of trade history |

---

## Portfolio

| Method | Path | Description |
|--------|------|-------------|
| GET | `/portfolio` | Aggregate portfolio summary (mark-to-market) |
| GET | `/exchange/account` | Raw exchange account state (cached) |
| GET | `/uta/state` | Unified Trading Account state (multi-broker) |
| GET | `/market/portfolio-history` | Portfolio snapshot time series |

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
| GET | `/analytics/alpha-attribution` | Read-only five-layer alpha attribution audit (`?days=7/14/30`) |
| GET | `/analytics/thesis-lineage` | Read-only thesis lineage records layered over the trade causal chain (`?position_id=...&order_id=...&days=90`) |
| GET | `/analytics/round1-progress` | Historical Round 1 progress metrics |
| GET | `/analytics/pipeline-funnel` | Pipeline conversion funnel counts |
| GET | `/analytics/family-scorecard` | Scanner family performance |
| GET | `/analytics/shadow-trades` | Shadow trade comparison data |
| GET | `/analytics/strategy-performance` | Per-strategy KPIs (backtest summary removed with rules engine, Position C) |
| GET | `/analytics/learning-funnel` | Learning write/consume visibility, lesson status counts, conservative contradiction count |

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

---

## Knowledge

| Method | Path | Description |
|--------|------|-------------|
| GET | `/knowledge` | All knowledge items (paginated) |
| GET | `/knowledge/research-notes` | Research notes only |
| GET | `/knowledge/dreaming-logs` | Dreaming agent summaries |
| GET | `/knowledge/strategy-memory` | Strategy-specific memory |
| GET | `/knowledge/guards` | Guard rules (learned constraints) |
| GET | `/knowledge/guard-candidates` | Guard candidates awaiting review |
| POST | `/knowledge/guard-candidates/{guard_id}/promote` | Manually promote a guard candidate to active guard |
| GET | `/knowledge/guards/stats` | Guard activation statistics |
| GET | `/knowledge/case-studies` | Case study analyses |
| GET | `/knowledge/{item_id}/content` | Full content of knowledge item |
| GET | `/dreaming/summary` | Dreaming session summary |
| GET | `/reviews/summary` | Post-trade review summary |

---

## Agent Runtime

| Method | Path | Description |
|--------|------|-------------|
| GET | `/agent-runs` | List agent run history |
| GET | `/agent-runs/{id}` | Run detail (status, outputs, errors) |
| GET | `/agent-runs/{id}/tool-calls` | Tool calls made during run |
| GET | `/agent-runs/{id}/steps` | Step-by-step execution trace |
| POST | `/agent-runs/start` | Manually trigger an agent run |

---

## Settings (all POST = save)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/settings` | Full config as JSON |
| POST | `/settings/autonomous-learning` | Learning loop toggles |
| POST | `/settings/bypass-distress-gate` | Explicit operator bypass for distress gate checks |
| POST | `/settings/exchange-markets` | Observed symbols list |
| POST | `/settings/primary-exchange` | Primary exchange selection |
| GET | `/settings/paper-account` | Local simulated cash, reserved margin, available balance, equity, and recent ledger events. Available only when `paper_internal` is active. |
| POST | `/settings/paper-account/credit` | Add local simulated USDT: `{"amount_usdt": number, "note"?: string}`. Human/audit logged; never touches an exchange. |
| POST | `/settings/paper-account/debit` | Remove local simulated USDT; cannot make cash negative. Human/audit logged. |
| POST | `/settings/paper-account/set-balance` | Set local simulated cash: `{"cash_balance_usdt": number, "note"?: string}`. An explicit zero remains zero; it is not automatically re-funded. |
| POST | `/settings/paper-account/close-spot` | Close all open local spot positions at their latest stored market prices. Preserves trade history and logs the human action. |
| POST | `/settings/paper-account/close-all` | Close all open local paper positions at their latest stored market prices. Preserves trade history and logs the human action. |
| POST | `/settings/runtime` | Scheduler intervals, timeouts |
| POST | `/settings/app` | App-level settings (timezone, locale) |
| POST | `/settings/pi_daemon` | PI daemon config (debounce, cooldown) |
| POST | `/settings/model` | Save model/provider config |
| POST | `/settings/serenity` | Serenity research config (auto_promote, min_score_for_pi) |
| POST | `/settings/guard` | Guard / lesson-promotion config (`auto_promote_enabled`, thresholds) |
| POST | `/settings/operator` | Operator loop config |
| POST | `/settings/regime` | Regime classifier config |
| POST | `/settings/health-config` | Health check thresholds |
| POST | `/settings/retention` | Data retention policies |
| POST | `/settings/telegram` | Telegram bot config |
| POST | `/settings/webhook` | Webhook notification config |
| POST | `/settings/update-config` | Auto-update settings |
| POST | `/settings/permission-mode` | **Worker/agent permission mode only** (Observe/Paper/Full — writes to `cfg.agent.permission_mode`). Operator permission (ReadOnly/Research/PaperAuto/AllowEditing/Auto) is a SEPARATE surface at `cfg.operator.permission_mode` — this endpoint does not change it. See [live_unlock_contract §5](live_unlock_contract.md). |
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
| GET | `/model` | Current model config (providers, default) |
| GET | `/model/health` | Per-provider health status |
| POST | `/model/ping` | Ping specific provider |
| GET | `/model/provider-types` | Available provider type definitions |
| POST | `/model/detect-models` | Auto-detect models on a provider |
| POST | `/model/test-candidate` | Test candidate model config |
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
| GET | `/serenity/status` | Pipeline status overview |
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
| GET | `/serenity/runs` | List runs |
| GET | `/serenity/runs/{id}` | Run detail |
| GET | `/serenity/runs/{id}/artifacts` | Run artifacts |
| POST | `/serenity/runs/{id}/retry` | Retry failed run |
| POST | `/serenity/runs/{id}/cancel` | Cancel running run |
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
| GET | `/risk/summary` | Operator-facing summary of recent risk rejections and pending boundary proposals (`risk.py:74`) |
| GET | `/risk/account-leverage` | Account leverage summary |

### Kill Switch

| Method | Path | Description |
|--------|------|-------------|
| GET | `/risk/kill-switch` | Kill switch current state `{"active": bool}` |
| POST | `/risk/kill-switch` | Engage or release the global trading brake; body: `{"active": true\|false, "reason": "", "confirm_release": bool}`. Releasing (`active=false`) requires `confirm_release=true` — else 422 (see ⚠️ note below). |

> ✅ **2026-07-07 更新（#32）**：release 路径的 confirm 要求已在代码里实现 —— `aletheia/server/routes/risk.py:85-101` 校验 `confirm_release=True` 才放行 release，否则 HTTP 422。旧的「已知缺陷」描述已过时；docs/issues/BACKEND_ISSUES.md 里 `kill-switch-release-missing-confirm` 条目相应标记为已解决。

---

## Live Trading (Trust Boundary)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/settings/live-unlock` | Unlock live trading (requires `confirm=true`; first unlock also requires `acknowledge_first_unlock=true`) |
| POST | `/settings/live-lock` | Lock live trading back to `live_disabled` |

---

## Operator / Command Center

| Method | Path | Description |
|--------|------|-------------|
| POST | `/command` | Natural language command (Agent Shell) |
| GET | `/operator/status` | Operator loop status |
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
| GET | `/gateway/autostart` | Autostart configuration |
| POST | `/gateway/autostart` | Toggle autostart |
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
| POST | `/update/apply` | Apply pending update |
| POST | `/update/continuity` | Report continuity after update |

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
| POST | `/persistence/lessons/{id}/accept` | Accept a proposed lesson and stamp dry-run/expiry timestamps |
| POST | `/persistence/lessons/{id}/reject` | Reject a lesson |
| POST | `/persistence/lessons/{id}/archive` | Archive a superseded lesson without hard deletion |
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
