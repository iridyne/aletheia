# Project Context

## Background

Aletheia Trading OS is a personal AI-native trading system built for solo automated trading on Bitget (BTC/ETH/SOL spot + USDT futures). Local-first, file-based "Trading-as-Git" architecture where 7+ AI agents orchestrate the full trading pipeline from market observation to order execution.

## Goals

- Automate market analysis and trade execution with AI agent oversight
- Maintain strict safety: risk limits, execution gateway, human approval for live mode
- Local-first architecture: all data in SQLite/JSONL/Markdown, no cloud dependencies
- Self-evolving: experience loop feeds post-trade lessons back into strategy design

## Current Stage

MVP — functional prototype with 9 agents (crypto + equity + prediction), 777+ passing tests. Phase 0: proving the closed loop runs 72h unattended on Bitget demo. Focus is trust hardening, not feature expansion.

Recent milestones (2026-06-13):
- Persistence/Sync/Recovery subsystem fully implemented (EventStore, StateStore, Reconciliation)
- Account-wide position discovery + adoption (P1-P2 complete)
- Serenity Research Workbench (frontend research UI)

## Core Modules

- `aletheia/agents/` — 9 AI agents (see Agent Pipeline below)
- `aletheia/execution/` — Execution Gateway (sole path to exchange)
- `aletheia/risk/` — Risk Engine (approval gate, 13+ check matrix)
- `aletheia/exchanges/` — Bitget API integration (demo + live)
- `aletheia/knowledge/` — Semantic knowledge base (research notes, lessons, reviews)
- `aletheia/data/` — Market data layer (CCXT provider, yFinance, Polymarket)
- `aletheia/gateway/` — State machine, reconciliation, recovery, leader locks
- `aletheia/portfolio/` — PnL calculation, position reconciliation
- `aletheia/trading/` — Instruments, indicators, strategy lifecycle, playbooks
- `aletheia/learning/` — Lessons + skills persistence (experience loop)
- `aletheia/policies/` — Versioned policy store
- `aletheia/sync/` — Export/import, conflict detection
- `aletheia/backup/` — Backup/restore/verify
- `aletheia/server/` — FastAPI backend
- `frontend/` — React/Vite dashboard (Serenity Research Workbench)

## Tech Stack

Python 3.12, FastAPI, Typer, APScheduler, SQLite (WAL), JSONL, React/Vite, CCXT, pytest

## How to Run

```bash
uv sync
python -m aletheia gateway start  # Full gateway (scheduler + API + agents)
# or for development:
scripts/dev.ps1                   # Vite HMR :5173 proxying /api/* to :8787
```

## How to Test

```bash
uv run pytest -q  # 777+ tests
```

## How to Deploy

Local only. No cloud deployment. Desktop runtime (UM790Pro observation host).

## Known Constraints

- Live trading disabled by default (requires explicit unlock)
- Bitget API rate limits apply
- All changes to trust boundary modules require focused regression tests
- SQLite concurrency: WAL + busy_timeout + threading.local connections + max_workers=3
- Market data providers are per-call, never cached (aiohttp event-loop binding)
- Phase 0 observation window must not be interrupted by commits (code_drift restart)
