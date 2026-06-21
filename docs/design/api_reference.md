# REST API Reference

> Complete endpoint index for Aletheia Trading OS backend (FastAPI on :8788, proxied via :8787).
> All endpoints prefixed with `/api/` unless noted. Auth: none (local-only by design).
> Last updated: 2026-06-21

---

## Conventions

- **Base URL**: `http://localhost:8787/api/` (via frontdoor proxy)
- **Auth**: None (local network only; tunnel requires separate Cloudflare auth)
- **Error format**: `{"detail": "message"}` with appropriate HTTP status
- **Pagination**: `?limit=N&offset=M` where supported
- **Timestamps**: ISO 8601 UTC strings in request/response bodies

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
| GET | `/strategies` | List all strategies |
| GET | `/strategies/summary` | Condensed strategy status overview |
| GET | `/strategies/{id}` | Strategy detail with parameters |
| GET | `/strategies/{id}/gates` | Promotion gate status for strategy |
| PATCH | `/strategies/{id}/venue` | Change strategy venue (paper/live) |
| POST | `/strategies/{id}/promote` | Promote strategy to next lifecycle stage |
| POST | `/strategies/{id}/retire` | Retire a strategy |

---

## Analytics

| Method | Path | Description |
|--------|------|-------------|
| GET | `/analytics/trade-insights` | Win rate, Sharpe, profit factor, drawdown |
| GET | `/analytics/trade-stats` | Rolling trade statistics |
| GET | `/analytics/strategy-kpis` | Per-strategy KPI breakdown |
| GET | `/analytics/strategy-evidence` | Evidence for strategy decisions |
| GET | `/analytics/backtest-paper-drift` | Backtest vs paper performance drift |
| GET | `/analytics/exit-policy-advice` | Exit policy recommendations |
| GET | `/analytics/trade-paths` | Trade decision path visualization data |
| GET | `/analytics/go-live-scorecard` | 10-criteria composite go-live score |
| GET | `/analytics/cost-summary` | LLM and execution cost breakdown |
| GET | `/analytics/confidence-calibration` | ECE calibration by conviction bucket |
| GET | `/analytics/reasoning-feedback` | Per-bucket rejection/win rates |
| GET | `/analytics/family-scorecard` | Scanner family performance |
| GET | `/analytics/shadow-trades` | Shadow trade comparison data |
| GET | `/analytics/backtest/runs` | List backtest runs |
| GET | `/analytics/backtest/runs/{id}` | Backtest run detail with trades |
| GET | `/analytics/backtest/compare/{strategy_id}` | Compare backtest vs paper |

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
| POST | `/settings/exchange-markets` | Observed symbols list |
| POST | `/settings/primary-exchange` | Primary exchange selection |
| POST | `/settings/runtime` | Scheduler intervals, timeouts |
| POST | `/settings/app` | App-level settings (timezone, locale) |
| POST | `/settings/pi_daemon` | PI daemon config (debounce, cooldown) |
| POST | `/settings/operator` | Operator loop config |
| POST | `/settings/health-config` | Health check thresholds |
| POST | `/settings/retention` | Data retention policies |
| POST | `/settings/telegram` | Telegram bot config |
| POST | `/settings/webhook` | Webhook notification config |
| POST | `/settings/update-config` | Auto-update settings |
| POST | `/settings/permission-mode` | Permission mode (read_only/operator/full) |
| POST | `/settings/exchange-mode` | Exchange mode (paper_internal/paper_demo/live) |
| POST | `/settings/risk` | Risk limit parameters |
| POST | `/settings/symbols` | Crypto symbol config |
| POST | `/settings/equities` | xStocks / equity config |
| POST | `/settings/trading` | Trading parameters (slippage, sizing) |
| POST | `/settings/market-data` | Market data collection config |
| POST | `/settings/universe` | Universe pipeline config |
| POST | `/settings/strategy_factory` | Strategy generation config |
| POST | `/settings/historical_data` | Historical data backfill config |
| POST | `/settings/event_triggers` | Event-driven trigger config |
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

---

## Universe Pipeline

| Method | Path | Description |
|--------|------|-------------|
| GET | `/universe/current` | Current universe composition |
| GET | `/universe/snapshots` | Universe snapshot history |
| GET | `/universe/snapshots/{id}` | Snapshot detail with members |
| GET | `/universe/members/{id}` | Member detail (features, outcomes) |
| GET | `/universe/members/{id}/outcomes` | Forward outcome history |
| POST | `/universe/refresh` | Trigger universe refresh |
| GET | `/universe/policy` | Active universe policies |
| GET | `/universe/calibration` | Universe scanner calibration data |
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
| POST | `/serenity/themes/{id}/promote` | Promote to opportunity |
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

---

## Risk / Kill Switch

| Method | Path | Description |
|--------|------|-------------|
| GET | `/risk/status` | Current risk state (ok/warning/blocked) + violations |
| GET | `/kill-switch` | Kill switch state |
| POST | `/kill-switch/activate` | Activate kill switch |
| POST | `/kill-switch/release` | Release kill switch (requires confirmation) |

---

## Live Trading (Trust Boundary)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/live-unlock` | Unlock live trading (requires explicit confirmation) |
| POST | `/live-lock` | Lock live trading |

---

## Operator / Command Center

| Method | Path | Description |
|--------|------|-------------|
| POST | `/command` | Natural language command (Agent Shell) |
| GET | `/operator/status` | Operator loop status |
| POST | `/operator/trigger` | Manually trigger operator loop |

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

| Method | Path | Description |
|--------|------|-------------|
| GET | `/runtime` | Current runtime state |
| POST | `/runtime/candidate` | Register candidate version |
| POST | `/runtime/shadow` | Start shadow execution |
| POST | `/runtime/promote` | Promote candidate to active |
| POST | `/runtime/drain` | Drain current version |
| POST | `/runtime/rollback` | Rollback to previous version |
| GET | `/runtime/events` | Runtime event log |
| GET | `/runtime/events/stream` | SSE stream of runtime events |

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
