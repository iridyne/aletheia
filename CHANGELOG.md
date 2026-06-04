# Changelog

## [0.4.0] - 2026-06-04 — Go-live MVP: autonomous paper loop closes + long-run survival

Makes the system safe to run unattended for long stretches, accumulating real
paper PnL and strategy lessons. Strictly in-guide; only 3 change-controlled files
touched (all in execution/). 253 tests passing (was 217).

### C1 — Position management + automatic close (the keystone)
- New `portfolio/position_manager.py` closes open PAPER positions by stop-loss /
  take-profit / max-hold. This was the missing producer of closed positions — it
  activates realized PnL, the daily-loss breaker (risk §8), and the strategy
  promotion gates, which were all previously dead.
- positions table gains `stop_price` / `take_profit_price` / `close_reason`
  (additive migration). `close_position` now records the reason and writes a
  `position_closed` audit event (trust §1.4).
- **CHANGE-CONTROLLED** `execution/paper_broker.py`: carries the proposal's stop +
  a config-derived take-profit onto a newly opened position (averaging-in keeps
  the original stop). `execution/gateway.py`: wires `stop_price` into the paper
  call. Causal chain unchanged; no closing order is fabricated (paper close is a
  projection update + audit only).
- LIVE positions are NEVER auto-closed; the kill switch does NOT block closing
  (closing reduces risk); no latest price → skip (never fabricate an exit).
- New `TradingConfig` (stop% / TP% / max-hold / min-confidence).

### C2 — Entry logic upgrade (trades stop being noise)
- `market_observer` now uses the model provider's structured() to produce a real
  `{direction_bias, confidence}` (graceful fallback to neutral/speculative). The
  opportunity is ALWAYS recorded, even when not traded (kept for analysis).
- `paper_trader` skips neutral and below-`min_confidence_to_trade` signals (marks
  them `reviewed`, never discards) and sets a protective stop from config.
- **CHANGE-CONTROLLED** `execution/paper_tools.py`: optional `stop_price`
  passthrough; the paper-only hard-reject is unchanged.

### C3 — Data retention / cleanup (long-run survival)
- Whitelist-based pruning of high-volume telemetry only (agent_runs, run_steps,
  tool_calls, market/portfolio snapshots). Trust-bearing tables (proposals, risk
  decisions, orders, fills, positions), audit, and knowledge are NEVER pruned.
- JSONL size-cap rotation to a single `.1` generation (content never deleted).
- Daily `cleanup` scheduler job + `RetentionConfig`.

### C4 — Self-upgrade safety (a bad pull can't brick the box)
- `update/smoke.py`: post-pull import gate (fresh subprocess) + fast pytest subset.
- `apply_update` now verifies the pulled code and, on failure, performs a
  user-authorized controlled `git reset --hard` back to the pre-pull commit,
  restores deps + config, and writes an `update_rolled_back` audit. Never
  auto-restarts. Still not agent-callable.

### C5 — Process liveness + health visibility (safe to walk away)
- `_gateway_runner.py` is now a supervisor: it respawns the inner uvicorn child on
  abnormal exit with backoff, gives up after a bounded number of restarts (audited
  + alerted), and routes the inner server's stderr to `data/logs/errors.log`. Pid
  semantics preserved (written once; `gateway stop`/`status` unchanged).
- New `agent_runtime/health.py` + 10-min `health_check` job: honest signals
  (agent-failure ratio, kill switch) with transition-only, de-duped Telegram
  alerts. Never fabricates a positive "healthy" push. `HealthConfig` added.

## [0.3.0] - 2026-06-03 — Full MVP: risk hardening, live path, reconciliation

Closes the remaining functional gaps between 0.2.0 and a feature-complete MVP per
the implementation guide. Strictly in-guide scope (no roadmap §30 items). 178 tests
passing (was 137). Frontend rebuilt.

### Risk Engine — all 13 §21.3 checks (CHANGE-CONTROLLED, guide §31.3)
- Added the 7 missing checks: max_daily_loss (global; auto-trips kill switch),
  account_mode_match, max_leverage (futures), data_freshness (live),
  max_single_trade_risk (live, stop-based), strategy_lifecycle_state (live).
- New checks gate **live** primarily; paper/demo stays permissive. Daily-loss and
  kill-switch are global brakes across all modes.
- `TradeProposal` gained optional `leverage` / `stop_price` (additive migration).

### Kill Switch (§21.3, §2.4)
- `risk/kill_switch.py`: activate/deactivate/is_active, persisted in
  `runtime.kill_switch_active` (survives restart). Risk engine rejects every
  proposal when engaged. Auto-trips on daily-loss breach.
- Surfaces: CLI `aletheia risk kill|resume|status`, API `GET/POST /api/risk/kill-switch`,
  `GET /api/risk/boundaries`, dashboard Settings + Overview. All changes audited.

### Live order path (CHANGE-CONTROLLED, guide §31.3)
- `execution/bitget_broker.py`: approved live-limited proposals now actually place
  a Bitget order via `BitgetClient.place_order` and record order (with
  `exchange_order_id`) / fill / position. Gateway wires the `bitget_live_limited`
  branch to it; still blocked when `live_disabled` (defense in depth) + audited.

### Bitget adapter — full §19.3 interface
- Added get_account, get_positions, get_open_orders, cancel_order, get_fills
  (was 5/9 methods; now 9/9).

### Reconciliation (§7, §20.2)
- `execution/reconciliation.py`: compares local live orders vs exchange open
  orders, marks vanished orders filled, audits divergence. Never places/cancels.
  Runs on a scheduler interval (live mode only).

### Portfolio: position close + realized PnL
- `close_position` (realizes PnL, marks closed) and `today_realized_pnl` (source
  for the daily-loss check). Scheduler stores periodic portfolio snapshots.

### Optional Git versioning (§2.2, §9.4)
- `files/git.py`: opt-in (`runtime.git_enabled`, default off) versioning of
  knowledge/playbooks/reports; never stages secrets.enc/app.db/logs. Graceful
  when git is absent.

### Scheduler
- Added reconciliation (2 min, live-only) and portfolio-snapshot (10 min) jobs.

### Fix — `gateway stop` on Windows (§32 #3)
- `is_running()` used `os.kill(pid, 0)`, which TERMINATES on Windows rather than
  probing liveness, so the PID file was cleared while the server kept running and
  `stop` couldn't find it. Now uses a platform-aware `_pid_alive` (tasklist on
  Windows). Verified start→status→stop end-to-end.

---

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
