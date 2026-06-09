# Aletheia Trading OS

## Project Overview

AI-native agentic trading system. Local-first, file-based "Trading-as-Git" platform.
- **Target users**: Solo trader (project owner) running automated trading strategies
- **Core function**: 7 AI agents orchestrate market analysis, strategy generation, risk management, and order execution on Bitget
- **Stage**: MVP — working prototype, needs trust hardening, not feature expansion

## Architecture

- **Tech stack**: Python 3.12 | FastAPI | Typer CLI | APScheduler | SQLite + JSONL | React/Vite frontend
- **Core modules**: `aletheia/` — agents/, execution/, risk/, exchanges/, trading/, knowledge/, server/, gateway/
- **Data flow**: Market data → Agent analysis → Strategy proposal → Risk Engine approval → Execution Gateway → Bitget API
- **External deps**: Bitget API (spot/futures), LLM providers (OpenAI-compatible, Anthropic-compatible)
- **Frontend**: `frontend/` — React/Vite dashboard (localhost control surface). For UI iteration, use `scripts/dev.ps1` (Vite HMR on :5173 proxying `/api/*` to the persistent gateway on :8787) — do NOT rebuild `aletheia/dashboard_static/` in a tight loop. Production path (`:8787`) only needs `vite build` once before commit.
- **Tests**: `tests/` — 515 tests (unit, integration, security, acceptance). Run `uv run pytest -q`.

## Development Rules

- Smallest change principle — MVP, not greenfield
- No uncontrolled expansion: no new exchanges, no Docker/Postgres/Redis, no vector search, no web ingestion
- Trust boundaries: risk/, execution/, auth.py, client.py, secrets.py, permissions.py, live.py are change-controlled
- Trading state invariants: Orders flow through Execution Gateway only; live trading disabled by default
- Market data: always go through `get_market_data_provider()` (`aletheia/data/ccxt_provider.py`) — never import `BitgetClient` for market data (it's execution-context only, and its REST v2 rejects canonical `BTC/USDT` symbols). Providers are per-call, never cached (CCXT binds aiohttp to the creating event loop; the scheduler uses a fresh loop per job). Only exception: Bitget-only ON-series xStocks, which construct `CCXTProvider(exchange="bitget", fallback_exchange="bitget")` directly (see comments at the call sites).
- Symbol format: canonical `BTC/USDT` everywhere internally (storage, risk, PnL, opportunities); the concatenated `BTCUSDT` exists ONLY at the execution adapter boundary via `to_bitget()`. Converters/`InstrumentId` live in `aletheia/data/symbols.py`. A mismatch silently drops opportunities at `paper_trader`'s filter.
- SQLite concurrency (do not break): APScheduler's `ThreadPoolExecutor` and uvicorn async handlers race on one `data/app.db`. Three pillars must all hold — (1) WAL + `busy_timeout` (`storage/sqlite.py`); (2) one connection per thread via `threading.local` (never cache a connection across threads); (3) `was_in_tx = db.in_transaction` captured **before** every write in `repositories.py`, committing only `if not was_in_tx` — sqlite3 auto-opens a tx on INSERT/UPDATE/DELETE, so checking *after* the write never commits and hangs the WAL lock (the historical `database is locked` bug). Scheduler is capped at `max_workers=3` on purpose; don't raise it or add a 4th writer pattern.
- Priority: State consistency > Safety > Auditability > Semantic honesty > Tests > Capability > Agent autonomy

## Repo Setup

- **`aletheia/data/` is the source package** for the data layer (`symbols.py`, `ccxt_provider.py`, `providers.py`, `cache.py`, `polymarket_provider.py`, `yfinance_provider.py`) — imported by `risk/`, `execution/`, `trading/`, and the agents. It is distinct from the repo-root `data/` runtime dir (`get_data_dir()` → `<repo>/data`, holding `config.yaml`/`app.db`/logs, which IS gitignored). `.gitignore` must keep the runtime rule root-anchored as `/data/`; a bare `data/` also matches `aletheia/data/` and silently excludes the source package from git, breaking every clone. After cloning, sanity-check with `uv run python -c "import aletheia.risk.engine"` and `uv run pytest -q`.

## AI Collaboration Rules

- Start each session by reading: CLAUDE.md, AGENTS.md, docs/PROJECT_CONTEXT.md, docs/CURRENT_TASK.md, docs/AI_HANDOFF.md
- End each session by updating: docs/AI_HANDOFF.md, docs/DEVLOG.md, docs/CURRENT_TASK.md
- Log architectural decisions in: docs/DECISIONS.md
- AGENTS.md contains detailed governance rules — always defer to it for safety boundaries
- **Backend issues** (specific bugs / contract drift / dead code with file:line): `docs/BACKEND_ISSUES.md`. Append new findings here, not in `north_star_references.md` (which is the long-term direction map).
- **Frontend issues** (§23 spec gaps / UI honesty breaches / engineering basics with file:line): `docs/FRONTEND_ISSUES.md`. Same severity legend as backend issues; do not bypass backend gaps with fake frontend data — entries that depend on backend fixes are explicitly cross-referenced.

## Safety Rules

- Never read or output API keys, exchange secrets, or credentials
- Never commit .env, .secret.key, secrets.enc, or token files
- Never enable live trading without explicit user confirmation
- Never modify risk limits, credentials, or execution paths without user approval
- Never place live orders or unlock live mode autonomously
