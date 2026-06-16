# Project Context

## Background

Aletheia Trading OS is a personal AI-native trading system for solo automated trading on Bitget (BTC/ETH/SOL USDT perpetuals + tokenized equities). Local-first, file-based "Trading-as-Git" architecture where 7+ AI agents orchestrate market observation through order execution.

## Goals

- Automate market analysis and trade execution with AI agent oversight
- Maintain strict safety: risk limits, execution gateway, human approval for live mode
- Local-first architecture: all data in SQLite/JSONL/Markdown, no cloud dependencies
- Self-evolving: experience loop feeds post-trade lessons back into strategy design

## Current Stage (2026-06-16)

MVP — functional prototype. 1295+ passing tests. Phase 0 observation runs continuously alongside development (no isolated time window). Focus remains trust hardening, not feature expansion.

Key milestones reached:
- Decision layer v3: RuleSet-driven trading + PI Agent conviction sizing + adversarial debate
- Deterministic self-heal layer (code_drift → auto-restart)
- Edge Lab (shadow trades, slippage, regime tags, family scorecard)
- 9-domain data collection (microstructure/derivatives/macro)
- Universe generator + signal emitter (deterministic opportunity path)
- Persistence/Sync/Recovery subsystem (EventStore, StateStore, Reconciliation)
- Account-wide position discovery + adoption (P1-P2)
- Operator Layer Track B (dual-process Pi Agent shell) — Stage 1 substantially complete

## Core Modules

- `aletheia/agents/` — 7+ AI agents (market_observer, paper_trader, position_manager, strategy_designer, post_trade_reviewer, risk_critic, dreaming_agent, plus scanners)
- `aletheia/execution/` — Execution Gateway (sole path to exchange)
- `aletheia/risk/` — Risk Engine (approval gate, 13+ check matrix)
- `aletheia/exchanges/` — Bitget API integration (demo + live)
- `aletheia/knowledge/` — Semantic knowledge base (FTS5 retrieval, research notes, lessons)
- `aletheia/data/` — Market data layer (CCXT multi-exchange, yFinance, Polymarket)
- `aletheia/gateway/` — State machine, reconciliation, recovery, self-heal, autostart
- `aletheia/portfolio/` — PnL calculation, position reconciliation, exchange sync
- `aletheia/trading/` — Instruments, indicators, strategy lifecycle, RuleSet scanner, sizing
- `aletheia/agent_shell/` — Operator Layer Python truth substrate (commands, context, sessions)
- `aletheia/server/` — FastAPI backend (API + static dashboard)
- `frontend/` — React/Vite dashboard (10 pages, Serenity Research Workbench)
- `operator-shell/` — Node/TypeScript Operator Shell (PI-style runtime, session tree, recommendations)

## Tech Stack

Python 3.12, FastAPI, Typer, APScheduler, SQLite (WAL), JSONL, React/Vite, CCXT, pytest, Node/TypeScript (operator shell)

## How to Run

```bash
uv sync
uv run aletheia gateway start     # Full gateway (scheduler + API + agents)
# Development:
scripts/dev.ps1                   # Single-port :8787 with Vite build-watch + backend reload
```

## How to Test

```bash
uv run pytest -q  # 1295+ tests
```

## How to Deploy

Local only. No cloud deployment. Desktop runtime with Windows Task Scheduler watchdog.

## Known Constraints

- Live trading disabled by default (requires explicit unlock)
- Bitget API rate limits apply
- All changes to trust boundary modules require user authorization
- SQLite concurrency: WAL + busy_timeout + threading.local connections + max_workers=3
- Market data providers are per-call, never cached (aiohttp event-loop binding)
- `code_drift` self-heal restart after commits is EXPECTED behavior, not a failure
- Facts (gateway state, credentials, loop activity) never go in git — use `aletheia doctor`
