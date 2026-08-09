# Copilot Instructions for Aletheia

## Authoritative docs to read first

1. [AGENTS.md](/home/ubuntu/aletheia/AGENTS.md) (single root rule file for AI agents)
2. [README.md](/home/ubuntu/aletheia/README.md) (entrypoint + runtime model)
3. [docs/README.md](/home/ubuntu/aletheia/docs/README.md) (documentation routing and authority order)

`CLAUDE.md` and `CODEX.md` intentionally redirect to `AGENTS.md`. Prefer current contracts/runbooks under [docs/](/home/ubuntu/aletheia/docs/) and treat [docs/archive/](/home/ubuntu/aletheia/docs/archive/) as historical evidence only.

## Build, test, and lint commands

Run Python commands with `uv run` (not bare `python`).

### Backend (Python)

- Sync dependencies: `uv sync`
- Health check: `uv run aletheia doctor`
- Full tests: `uv run pytest tests/ -q --tb=line`
- Single test file: `uv run pytest tests/test_<area>.py -q`
- Single test case: `uv run pytest tests/test_<area>.py -q -k "<test_name_fragment>"`
- Lint: `uv run ruff check aletheia tests`

### Frontend (Vite/React/TypeScript)

- Install deps: `cd frontend && npm install`
- Dev server (HMR): `cd frontend && npm run dev` (default `http://127.0.0.1:5173`)
- Lint: `cd frontend && npm run lint`
- Full frontend tests: `cd frontend && npm run test`
- Single frontend test file: `cd frontend && npx vitest run src/<path>/<file>.test.ts`
- Frontend build (required after frontend source changes): `cd frontend && npm run build`

## High-level architecture

- **Core trading path**: market data -> agent analysis -> strategy/proposal -> Risk Engine -> Execution Gateway -> broker/exchange.
- **Backend shape**: Python 3.12 monolith with FastAPI + Typer CLI under [aletheia/](/home/ubuntu/aletheia/aletheia/).
- **Runtime topology**:
  - Stable gateway front door on `:8787`
  - Backend worker behind it (reserved stable backend port `:8788`)
  - Supervisor restarts backend worker and keeps front door available for infra diagnostics
- **Scheduler-driven system**: recurring jobs (market data, observers, paper trader flow, risk/execution support, learning/cleanup) are orchestrated through agent runtime scheduler contracts (see [docs/design/contracts/agent_contracts.md](/home/ubuntu/aletheia/docs/design/contracts/agent_contracts.md)).
- **Storage model**: local-first SQLite/JSONL/Markdown; runtime state belongs under `~/.aletheia/` (not repository-root `data/`).
- **Frontend serving model**:
  - Worktree UI iteration uses Vite `:5173`
  - Production/stable UI is built into [aletheia/dashboard_static/](/home/ubuntu/aletheia/aletheia/dashboard_static/) and served by gateway `:8787`

## Key repository conventions

- **Safety boundaries are strict**: changes in risk/execution/live/secrets/permissions/audit-chain modules are controlled and need focused regression proof.
- **Execution boundary**: orders must flow through execution gateway + risk evaluation; never add direct order placement from UI/agent paths.
- **Live defaults**: live trading is locked by default and must not be auto-unlocked.
- **Market data access rule**: use `get_market_data_provider()`; do not use `BitgetClient` outside execution context.
- **Symbol convention**:
  - Canonical internal symbol: `BASE/QUOTE` (e.g. `BTC/USDT`)
  - Convert to exchange-native form only at execution adapter boundary
  - Frontend symbol rendering must use `fmtSymbol(symbol, marketType)`
- **SQLite concurrency guardrails**: preserve the existing write-lock + transaction discipline; do not hold DB write transactions across network/await boundaries.
- **Config/UI parity rule**: new backend config-schema fields require corresponding Settings UI controls.
- **Preview mode contract**: Vite default proxy to `:8787` is read-only for `/api`; write-shaped UI flows require isolated preview API (`uv run aletheia dev api --port 879x`).
- **Do not treat docs as task ledger**: track active defects/work via GitHub Issues/PRs, not new tracked handoff/status markdown files.

## MCP servers to configure for this repo

- **Playwright MCP** (recommended): for dashboard flow validation on Vite preview and stable gateway UI paths.
- **GitHub MCP**: for issue/PR triage and review-thread workflows tied to this repository.
