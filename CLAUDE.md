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
- **Tests**: `tests/` — 272 tests (unit, integration, security, acceptance)

## Development Rules

- Smallest change principle — MVP, not greenfield
- No uncontrolled expansion: no new exchanges, no Docker/Postgres/Redis, no vector search, no web ingestion
- Trust boundaries: risk/, execution/, auth.py, client.py, secrets.py, permissions.py, live.py are change-controlled
- Trading state invariants: Orders flow through Execution Gateway only; live trading disabled by default
- Market data: always go through `get_market_data_provider()` (`aletheia/data/ccxt_provider.py`) — never import `BitgetClient` for market data (it's execution-context only, and its REST v2 rejects canonical `BTC/USDT` symbols). Providers are per-call, never cached (CCXT binds aiohttp to the creating event loop; the scheduler uses a fresh loop per job). Only exception: Bitget-only ON-series xStocks, which construct `CCXTProvider(exchange="bitget", fallback_exchange="bitget")` directly (see comments at the call sites).
- Priority: State consistency > Safety > Auditability > Semantic honesty > Tests > Capability > Agent autonomy

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
