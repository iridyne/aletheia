# Changelog

## [0.2.0] - 2026-06-03 — Functional MVP completion

Completion pass after an audit found the initial "MVP" was ~35% of the dashboard
spec with several non-functional core pieces. This release closes those gaps so the
system is configurable and usable entirely from the dashboard.

### P1 — Backend config + secrets surface
- `ProviderConfig.api_key_secret_ref`; router injects API key from secret storage
- Real secrets CLI (`set` hidden prompt, `list`, `test bitget`); `model set` CLI
- Routes: `GET/POST /api/settings/model`, `GET /api/model/health`,
  `POST /api/bitget/test-connection`, `POST /api/settings/exchange-credentials`,
  `GET /api/bitget/credentials-status`
- Write-only secrets: GET endpoints never return raw keys (configured + last4 only)

### P2 — Dashboard configuration UX (OpenAlice-style)
- Settings page with all 8 §23.12 controls: model provider + write-only API key,
  Bitget demo/live credentials + test connection, exchange-mode switch,
  live unlock/lock, risk display, telegram, permission, pause/resume
- SetupBanner onboarding nudge; Overview shows account/PnL/positions/latest activity

### P3 — Runtime (the system now actually runs)
- FIX: scheduler now starts on gateway boot (was never invoked)
- Real pause/resume (halts scheduler jobs); market-data loop writes market_snapshots
- Agents call the LLM with graceful degradation; tool calls logged
- 4 new agents: strategy_designer, risk_critic, portfolio_agent, post_trade_reviewer
- portfolio/pnl.py: mark-to-market PnL + portfolio snapshots

### P4 — Trading workflow + dashboard actions
- §25 routes: proposal approve/reject, strategy get/promote/retire, portfolio
- FIX: gateway passed empty risk_decision_id (FK violation); now passes real id
- Dashboard action buttons (approve/reject, promote/retire, run-agent); KB tabs;
  Positions grouped + exposure; 5 seed playbooks on bootstrap

### P5 — Charts
- lightweight-charts: candlestick price chart + trade markers, PnL line chart
- Charts page, Reports page renders real summaries

### P6 — Test matrix + acceptance hardening
- Security tests: secrets never in logs/config/GET responses; restricted tools
  unreachable; live blocked by default; permission/live changes audited
- FIX: tool registry now initialized in orchestrator (agents were silently no-op
  when run before the app lifespan registered tools)
- §32 acceptance tests through the API/CLI
- 137 tests passing (was 84)

---

## [0.1.0] - 2026-06-03

### Initial milestones M0–M11 (function-level)

#### M0 — Repo Skeleton
- Python 3.12 project with uv, pyproject.toml
- CLI skeleton (aletheia/ato) with Typer
- Config schema + loader + YAML persistence
- Secrets handling (keyring + encrypted file fallback)

#### M1 — Gateway + Local Server
- FastAPI server on localhost:8787
- Gateway start/stop/status/restart/pause/resume
- PID file management, browser auto-open
- Health and status API endpoints

#### M2 — Storage
- SQLite schema (17 tables)
- JSONL event stream writer/reader
- Markdown writer for knowledge documents
- Repository CRUD functions
- Audit event system

#### M3 — Dashboard MVP
- React/Vite/TypeScript frontend (11 pages)
- Overview, Agent Runs, Dreaming, Knowledge Base, Opportunities, Proposals, Paper Trades, Positions, Strategy Lifecycle, Reports, Settings
- Dark theme, sidebar navigation
- Built to static files served from FastAPI

#### M4 — Model Provider Layer
- OpenAI-compatible (chat_completions + responses)
- Anthropic-compatible (Messages API)
- Codex CLI (graceful when unavailable)
- Router with caching and healthcheck

#### M5 — Agent Runtime
- APScheduler-based scheduler
- Tool registry with 5-level permission enforcement
- Agent orchestrator with full lifecycle logging
- Market Observer, Research Agent, Dreaming Agent

#### M6 — Knowledge Base
- Research Notes, Strategy Memory, Dreaming Logs
- Confidence levels (high/medium/speculative/deprecated)
- Archive/deprecate (no hard delete enforced)
- SQLite index

#### M7 — Bitget Adapter
- Native HMAC-SHA256 auth (no ccxt dependency)
- Demo mode header support (paptrading: 1)
- Spot + USDT futures market data
- Account/balance/ticker/candle/order endpoints

#### M8 — Risk Engine + Paper Broker
- Hard limits (immutable to agents)
- 7+ risk checks (symbol, market, live state, permission, position size, etc.)
- Paper broker with simulated fills
- Execution gateway (full pipeline)

#### M9 — Strategy Lifecycle
- 10 lifecycle states (draft -> live_limited -> retired)
- Forward-only transitions enforced
- Promotion gates for live candidacy

#### M10 — Telegram Light Adapter
- /status /pause /resume /latest /help commands
- Allowed chat IDs enforcement
- Audit events for all actions

#### M11 — Live-Limited Gated Path
- live_disabled by default
- Explicit human confirmation required for unlock
- Risk engine blocks all live orders when disabled
- Lock/unlock via API and dashboard

### Test Coverage
- 84 tests passing (unit + integration + security)
- Config, secrets, permissions, risk, lifecycle, API, gateway, telegram
