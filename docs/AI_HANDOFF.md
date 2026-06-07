# AI Handoff

## Last Updated

2026-06-07 (later — strategy↔trade causal chain wired)

## Recent Change (feat/strategy-causal-chain)

Two passes on this branch (373 tests pass; branch not yet merged):

**1. Strategy↔trade causal chain (gap #2).** Connected the four-way break in the
`strategy_id` chain (the 2026-06-06 §1.1 "fix" had only wired the downstream fallback).
Now: `create_strategy` persists `parameters`; `strategy_designer` produces a parameter
spec, auto-advances a strategy to `paper_running` (ungated states only — live gates
untouched), and is scheduled daily; `market_observer` binds opportunities to an active
strategy. Promotion-gate `paper_trades` no longer stuck at 0. Makes paper evidence
trustworthy — prerequisite for north-star stage 1/2.

**2. Re-audit of BACKEND_ISSUES §10.** The "all fixed 2026-06-06" archive is ~75%
true, ~25% claimed-but-not. Eight falsely-archived issues really fixed (live-order
ordering, health-alert delivery, paper/demo risk bounds, clientOid, kill-switch
TOCTOU lock, paper_trader autonomous gate, commit_all scheduling, gateway log
rotation). §10 now carries a correction banner. **Lesson: never trust a "fixed"
claim without grepping current code** — see DEVLOG 2026-06-07.

Change-controlled modules touched (risk/, execution/, exchanges/): all changes are
hardening (tighter bounds, idempotency, serialization) — no live gate weakened, no
hard limit relaxed. Each has a focused regression test.

## Current State Summary

MVP trading system with 7 agents, 272 tests. Comprehensive bug-fix pass completed across all backend and frontend issues documented in BACKEND_ISSUES.md and FRONTEND_ISSUES.md.

## What Was Completed

- **All BACKEND_ISSUES §1 (trust invariants)** — Fixed: execution gateway transaction isolation, git pull ff-only, update confirm gate, strategy_id causal chain, promotion gates
- **All BACKEND_ISSUES §2 (data integrity)** — Fixed: ~25 items including PnL fees, knowledge status filtering, CORS, audit symmetry, risk evaluation, health endpoints, scheduler error handling, telegram bot hardening, model provider healthcheck, codex auth regex, markdown file safety, knowledge cleanup-on-failure
- **All BACKEND_ISSUES §3 (dead code/cosmetic)** — Fixed: ~15 items including orchestrator traceback, run_logger null fields, pidfile validation, redact format, market observer config, bitget orderId check
- **All FRONTEND_ISSUES §3 (engineering)** — Fixed: shared types, DataTable, DetailDrawer, chart theme, markdown styles, marker click callback
- **All FRONTEND_ISSUES §1-§2** — Fixed: proposal risk_decision display, KB markdown reading, autonomous_paper_trading visibility, effective config card, promotion gates display, chart-trade bidirectional linking, risk boundaries with units, dreaming 6-section layout, overview sparkline + run timeline, cross-page navigation, agent run structured tool calls, performance metrics in Reports

## What Is In Progress

Nothing currently in progress.

## Important Files

- `AGENTS.md` — Governance rules and safety boundaries (primary reference)
- `docs/BACKEND_ISSUES.md` — Backend issue tracker (§10 has fixed items)
- `docs/FRONTEND_ISSUES.md` — Frontend issue tracker (§10 has fixed items)
- `docs/aletheia_trading_os_implementation_guide.md` — Full design reference
- `docs/RUNBOOK.md` — Operations guide

## Important Decisions

See docs/DECISIONS.md and the existing AGENTS.md governance framework.

## Known Issues

Code-layer items in BACKEND_ISSUES.md and FRONTEND_ISSUES.md §1-§3 have been worked through. Two **architectural / north-star gaps** remain explicitly open and unresolved — they are not bugs to flip into §10, they are direction-of-travel work:

- **portfolio-sync-gap** — Portfolio dashboard does not reflect real exchange account state (only locally-created positions). Filed as BACKEND_ISSUES.md §2.3 last bullet. Touches change-controlled `aletheia/exchanges/bitget/client.py`; requires explicit authorization before work begins.
- **experience-loop-gap** — knowledge-write-only loop; agents rarely read prior research/reviews back into decisions. Tracked in `docs/north_star_references.md §6`. Stage-2 work per north-star sequencing; do NOT start before stage 0 (stable run) and stage 1 (learning quality) are proven.

Recent:
- 2026-06-07 cockpit merge resolved (`ace780a`); local 5-zone shell discarded after explicit review against AGENTS.md priorities. See DECISIONS.md and `docs/superpowers/specs/2026-06-07-merge-postmortem-design.md`.
- 2026-06-07 webui lightweight optimization: `.brand`/`.topbar` baseline aligned; `<AccountSnapshot/>` (Total Value with portfolio-sync-gap caveat / Today PnL / Open Positions / Pending Proposals) added to Overview + Paper Trades + Positions. Five-zone shell / Right Inspector / global Command Center deferred to stage 1+. See DECISIONS.md.

## Next Recommended Step

Per `north_star_references.md §7` we are in **stage 0: prove stable run**. Before starting any new feature work or closing the experience loop:

1. Run a 7–14 day unattended paper-trading session and confirm: gateway never crashes, no orphan orders, no lost state, autostart + watchdog actually self-heal (PR #7/#8 capabilities).
2. Confirm test baseline holds: `uv run pytest -q` should report 352 passed.
3. Re-confirm gateway health honestly: `uv run aletheia gateway status`.
4. Only after stage 0 evidence accumulates, consider stage 1 (validate learning-ledger output quality) before opening any north-star work.

## Commands to Run

```bash
uv sync
cd frontend && npm run build && cd ..
pytest tests/
uv run aletheia gateway start
```

## Warnings

- Live trading is disabled by default. Never enable without explicit user confirmation.
- Trust boundary modules (risk/, execution/, auth.py, secrets.py) require extra caution.
- Frontend must be rebuilt after source changes: `cd frontend && npm run build`

## Context for Next AI Session

All documented issues have been addressed. The system is ready for integration testing. Key architectural changes: execution gateway no longer wraps order placement in an outer transaction (§1.2 fix), git pull uses ff-only merge (§1.2 fix), promotion gates API exposed for frontend consumption.
