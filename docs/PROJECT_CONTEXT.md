# Project Context

## Background

Aletheia Trading OS is a personal AI-native trading system built for solo automated trading on Bitget (BTC/ETH/SOL spot + USDT futures).

## Goals

- Automate market analysis and trade execution with AI agent oversight
- Maintain strict safety: risk limits, execution gateway, human approval for live mode
- Local-first architecture: all data in SQLite/JSONL/Markdown, no cloud dependencies

## Current Stage

MVP — functional prototype with 7 agents, 272 passing tests. Focus is trust hardening, not feature expansion.

## Core Modules

- `aletheia/agents/` — 7 AI agents (market observer, strategist, risk assessor, etc.)
- `aletheia/execution/` — Execution Gateway (sole path to exchange)
- `aletheia/risk/` — Risk Engine (approval gate)
- `aletheia/exchanges/` — Bitget API integration
- `aletheia/knowledge/` — Semantic knowledge base
- `aletheia/server/` — FastAPI backend
- `frontend/` — React/Vite dashboard

## Tech Stack

Python 3.12, FastAPI, Typer, APScheduler, SQLite, React/Vite, pytest

## How to Run

```bash
uv sync
python -m aletheia  # CLI
# or
uvicorn aletheia.server:app --port 18190
```

## How to Test

```bash
pytest tests/
```

## How to Deploy

Local only. No cloud deployment. Desktop runtime.

## Known Constraints

- Live trading disabled by default (requires explicit unlock)
- Bitget API rate limits apply
- All changes to trust boundary modules require focused regression tests
