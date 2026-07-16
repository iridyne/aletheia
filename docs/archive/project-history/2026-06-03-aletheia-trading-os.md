# Aletheia Trading OS Implementation Plan

> **For agentic workers:** Implement milestone-by-milestone. Each milestone ends with passing tests + CHANGELOG update. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build a local-first, file-based, Trading-as-Git, AI-native self-learning agentic trading OS for BTC/ETH/SOL on Bitget, paper/demo first, live strictly gated.

**Architecture:** Single Python 3.12 package `aletheia` providing a Typer CLI (`aletheia`/`ato`) that runs a gateway daemon. The gateway boots a FastAPI server (localhost:8787) which serves a static React/Vite dashboard and a localhost JSON API, plus an APScheduler-driven agent runtime. State lives in SQLite (`app.db`), JSONL event logs, and Markdown knowledge files under a portable `data/` dir. Secrets live in OS keyring with an encrypted-file fallback and never touch logs/prompts. All orders flow Strategy/Agent → Risk Engine → Permission → Live-state check → Execution Gateway → Paper Broker | Bitget adapter → Fill → Ledger → Audit. The agent can never reach the exchange directly and can never edit risk limits, credentials, or the execution gateway.

**Tech Stack:** Python 3.12 (managed via `uv`), FastAPI, Typer, Pydantic v2, SQLite (stdlib `sqlite3` + thin repos), APScheduler, httpx, cryptography + keyring, pytest. Frontend: Vite + React + TypeScript + TanStack Query/Table + lightweight-charts, built to `aletheia/dashboard_static/`.

---

## Non-negotiable invariants (from guide §0)

1. No Docker/Postgres/Redis in MVP — local CLI gateway + local dashboard only.
2. Live trading is OFF by default (`live_state: live_disabled`); first activation requires explicit human action.
3. Secrets never enter agent prompts, logs, KB, reports, or error messages (redacted).
4. Agent autonomy is bounded: it may write research/paper configs/playbook drafts/promotion proposals; it may NOT modify hard risk limits, credentials, execution-gateway logic, or signer code.
5. Every important state transition writes a JSONL audit event + (where relevant) a SQLite row.
6. `agent → exchange`, `dashboard → exchange`, `telegram → exchange` direct paths are forbidden; everything goes through the backend execution gateway.

## Tech decisions locked

- **Python env:** `uv` with `uv venv --python 3.12` (dev box default Python is 3.10; uv supplies 3.12). `uv run` for all commands.
- **Storage:** stdlib `sqlite3` with a small migration runner (idempotent `CREATE TABLE IF NOT EXISTS`) rather than SQLModel/Alembic, to stay light per §0. Pydantic v2 models for domain objects; thin repository functions map rows↔dicts.
- **Async:** FastAPI async routes; the agent runtime + model providers are async (httpx.AsyncClient). The CLI uses `asyncio.run` where needed.
- **Gateway daemon (Windows-first):** `gateway start` spawns a detached background process (pythonw/`subprocess` with `CREATE_NEW_PROCESS_GROUP|DETACHED_PROCESS`) running uvicorn; writes `data/gateway.pid`. `--foreground` runs inline. stop/status/restart use the PID file. logs tail `data/logs/gateway.log`. pause/resume flip `runtime.paused` in config + signal the scheduler.
- **Secrets:** `keyring` first; fallback to `data/secrets.enc` encrypted with a Fernet key derived from a machine-local key file (`data/.secret.key`, chmod best-effort). Never logged.

## File structure

Follows guide §7 exactly (`aletheia/cli`, `server`, `gateway`, `config`, `storage`, `files`, `model_provider`, `agent_runtime`, `agents`, `knowledge`, `trading`, `exchanges/bitget`, `execution`, `risk`, `portfolio`, `telegram`, `dashboard_static`) plus `frontend/` and runtime `data/`. Tests under `tests/` mirroring the package.

## Milestones (build order = guide §27)

- [ ] **M0 — Repo skeleton:** pyproject (uv, py3.12), package layout, config schema/loader/defaults, data-dir bootstrap, CLI skeleton with `--help` + `gateway status`. Tests: config load, data bootstrap.
- [ ] **M1 — Gateway + server:** FastAPI app, health/status routes, static serving, gateway start/stop/status/restart/logs/pause/resume, PID, browser auto-open. Tests: gateway lifecycle, pause/resume, health.
- [ ] **M2 — Storage:** all SQLite tables, JSONL writer, Markdown writer, repositories, audit-event helper. Tests: repos CRUD, audit append, redaction.
- [ ] **M3 — Dashboard MVP:** Vite/React app, all MVP pages, API client, build to dashboard_static. Settings can change permission mode; pause/resume works. Tests: build succeeds; backend serves index.
- [ ] **M4 — Model providers:** base abstraction, openai_compatible (chat_completions+responses), anthropic_compatible, codex_cli (graceful), router, healthcheck; CLI model commands. Tests: routing, structured parsing, healthcheck (mocked httpx).
- [ ] **M5 — Agent runtime:** scheduler, run logger, tool registry + permission enforcement, prompts, Market Observer, Research Agent, Dreaming Agent light. Tests: permission gating (restricted tools blocked), run record, research-note+opportunity creation.
- [ ] **M6 — Knowledge Base:** evidence/research/strategy/agent memory + dreaming logs, confidence levels, archive/deprecate (no hard delete), SQLite index, contradiction stub. Tests: no-hard-delete enforced, index updated.
- [ ] **M7 — Bitget adapter:** HMAC auth + signer helper, symbols, spot + futures market data, demo `paptrading:1` header, account/balances/positions, gated place_order, errors with redaction. Tests: signer determinism, demo header present, live blocked when disabled.
- [ ] **M8 — Risk Engine + Paper Broker:** hard limits (immutable to agent), all risk checks, RiskDecision output, paper broker, orders/fills/positions, PnL snapshots, execution gateway. Tests: each risk check, agent cannot mutate limits, full proposal→fill→position chain.
- [ ] **M9 — Strategy Lifecycle:** strategy records, states, forward-only transitions, promotion gates, reports. Tests: illegal transitions rejected, gate evaluation, experiment updates.
- [ ] **M10 — Telegram light:** /status /pause /resume /latest /help, allowed_chat_ids enforcement, audit. Tests: unauthorized chat rejected, command handlers, audit written. (Polling adapter, disabled by default.)
- [ ] **M11 — Live-limited gated path:** live unlock UI/CLI, first_live_confirmation flag, live_limited risk path, restricted unlock_live tool. Tests: live blocked by default, unlock requires human action + audit, live order needs risk approval.
- [ ] **M-Final:** full suite green; verify the 16 acceptance criteria (§32); CHANGELOG + README.

## Testing strategy (guide §28)

- **Unit:** config loading, secret redaction, permission checks, risk checks, lifecycle transitions, JSONL writing, SQLite repos, model routing, Bitget signing.
- **Integration:** gateway start/status/stop, agent run→opportunity, proposal→risk decision, paper order→fill→position, permission switch, pause/resume.
- **Security:** secrets absent from logs + agent context, restricted tools unreachable by LLM, live-disabled blocks live, risk limits immutable by agent, dashboard permission changes audited.

Run with `uv run pytest`. Each milestone must be green before the next starts.
