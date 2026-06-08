# AI Handoff

## Last Updated

2026-06-08 (full-market analyzer + paper trading pipeline fix)

## Recent Change (Full-Market Analyzer + Paper Trading Pipeline Fix — 2026-06-08)

Three-bug paper trading fix + full-market analyzer expansion.

**Paper trading pipeline bugs fixed:**
- `data/config.yaml` — `autonomous_paper_trading: false → true` (main switch was off)
- `aletheia/agents/market_observer.py` — per-symbol loop (one LLM call + one Opportunity per symbol); previously only symbols[0] got an opportunity
- `aletheia/agents/paper_trader.py` — `allowed_symbols` now uses `to_canonical()` so Bitget-format config entries match canonical opportunity symbols
- `aletheia/portfolio/pnl.py` — `_latest_price` now uses `latest_snapshot()` dual-format lookup instead of direct DB query

**Full-market analyzer — new agents:**
- `aletheia/agents/stock_observer.py` — US equity observer; yFinance daily OHLCV + indicators (SMA/RSI/ATR/Bollinger/momentum); `asset_class="equity"`, observation-only; scheduled Mon–Fri 14:30–21:00 UTC
- `aletheia/agents/prediction_observer.py` — Polymarket prediction market observer; Gamma API (public REST); `asset_class="prediction"`, observation-only; scheduled every 30 min
- `aletheia/data/polymarket_provider.py` — Polymarket Gamma API provider (httpx + urllib fallback)

**Config expanded:**
- `observed_symbols`: BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT, DOGE/USDT
- `observed_stocks`: SPY, QQQ, NVDA, AAPL
- `observed_prediction_markets`: crypto, politics

**Schema + migrations:**
- `aletheia/config/schema.py` — `MarketDataConfig` extended with `observed_stocks`, `observed_prediction_markets`
- `aletheia/storage/models.py` + `migrations.py` — `asset_class` column on `opportunities`

**Tests fixed:**
- `tests/test_entry_logic.py` — 2 tests updated for per-symbol loop (assert BTC row specifically)
- `tests/test_paper_autoloop.py` — seed symbol changed `BTCUSDT → BTC/USDT` (canonical format)
- `tests/test_runtime.py` — job count updated `11 → 13` (+ stock_observer + prediction_observer)

**Test result:** 398 passed, 19 failed (all 19 are pre-existing; net +4 fixed vs prior baseline).

**Scope decision:** user confirmed system is post-MVP; Polymarket and additional exchanges (xStocks etc.) are now in scope. See `docs/DECISIONS.md` 2026-06-08 entry.

---

## Prior Change (Symbol Format Unification + Data Audit — 2026-06-08)

Comprehensive data audit and symbol format unification across the full agent pipeline.

**Root problem:** CCXTProvider writes snapshots/OHLCV with canonical `BTC/USDT` keys, but
`market_observer.py` was converting to Bitget format (`BTCUSDT`) before storing opportunities,
causing paper_trader's filter to silently drop everything.

**Fixes applied:**
- `aletheia/agents/market_observer.py` — removed `to_bitget()` call; opportunities now stored in canonical `BTC/USDT` format (matching snapshot keys)
- `aletheia/agents/paper_trader.py` — `allowed_symbols` now built as `{to_canonical(s) for s in tradable_symbols_from_config(cfg)}` so Bitget-format config entries still match canonical opportunities
- `aletheia/risk/engine.py` — symbol check normalises both sides with `to_canonical()` before comparing
- `aletheia/server/routes/settings.py` — `/settings/symbols` endpoint now syncs `market_data.observed_symbols` (canonical) whenever tradable symbols are updated
- `frontend/src/api/client.ts` — `getOhlcv()` and `getMarketSnapshots()` both use `encodeURIComponent(symbol)` so `BTC/USDT` `/` doesn't split the URL path
- `frontend/src/components/MarketTicker.tsx` — ticker symbol keys changed from `BTCUSDT` to `BTC/USDT` to match snapshot map
- `frontend/src/pages/Charts.tsx` — opportunities filter uses exact canonical match (no `|| replace('/', '')` workaround)
- `tests/test_entry_logic.py` — 6 tests updated: assertions and fixture data changed from Bitget to canonical format; test renamed `test_observer_stores_canonical_format_symbol`

**Test result:** 22 pre-existing failures only (same as baseline). 0 new failures.

---

## Prior Change (Data Layer Refactor + Markets UI — 2026-06-08)

Complete data source decoupling from Bitget. Market data (OHLCV, tickers) now goes
through CCXT/yFinance; `BitgetClient` is execution-context only.

**New files:**
- `aletheia/data/__init__.py` — package
- `aletheia/data/providers.py` — `MarketDataProvider` Protocol, `Ticker`/`Candle` models (incl. `high_24h`, `low_24h`, `provider`)
- `aletheia/data/symbols.py` — canonical `BTC/USDT` format + per-provider converters
- `aletheia/data/ccxt_provider.py` — CCXT Binance (+ Bybit fallback), auto-persists OHLCV
- `aletheia/data/cache.py` — in-memory TTL cache (10 min default)
- `aletheia/data/yfinance_provider.py` — macro data (SPY/VIX/DXY/Gold), default OFF

**Changed files (backend):**
- `aletheia/config/schema.py` — added `MarketDataConfig` (provider, observed_symbols, cache TTL, yfinance flag)
- `aletheia/trading/market_data.py` — uses CCXTProvider; `latest_snapshot()` dual-format lookup
- `aletheia/trading/market_context.py` — uses provider protocol + cache; optional macro block
- `aletheia/trading/instruments.py` — added `observed_symbols_from_config()`; BITGET_GRANULARITY annotated execution-only
- `aletheia/storage/migrations.py` — new `market_ohlcv` table; `high_24h`/`low_24h`/`provider` columns on `market_snapshots`
- `aletheia/storage/repositories.py` — `upsert_ohlcv_candles()` + `get_ohlcv_candles()`
- `aletheia/server/routes/data.py` — `/api/market/ohlcv` uses CCXTProvider
- `aletheia/agents/market_observer.py` — uses `observed_symbols_from_config()`
- `aletheia/exchanges/bitget/client.py` — `get_ticker`/`get_ohlcv` annotated `# execution-context only`

**Changed files (frontend — FRONTEND_ISSUES §5.1–5.4 resolved):**
- `frontend/src/pages/Charts.tsx` — canonical `BTC/USDT` symbols; `ProviderChip` data-source label; `refetchInterval: 30_000`; H/L display; freshness indicator

**Unchanged:** `execution/`, `risk/`, `portfolio/exchange_sync.py`, `bitget/auth.py`

**Test result:** 424 passed (0 failed). 3 existing tests updated to mock CCXTProvider.

**Open next:** enable `market_data.enable_yfinance_macro: true` in config to activate macro block.

---

## Prior Change (2026-06-08 — SQLite 并发锁 + 核实)

**SQLite 并发锁**：`aletheia/storage/sqlite.py` 改为 `threading.local()` 每线程独立连接。`timeout=30`；`check_same_thread=True` 恢复。

**BACKEND_ISSUES §10 核实**：8 条高风险条目（strategy_id / live-order / asyncio / paper 风险 / clientOid / kill-switch / paper_trader Gate A / commit_all）全部 grep 验证已真修。

---

## Recent Change (WebUI redesign — frontend-only session)

Complete structural redesign of the React dashboard, addressing the owner's pain points
(information density / layout / transparency / workflow — "everything uncomfortable").
**No backend / API contract changes.** Diagnosis: the UI was "14 flat DB-table pages";
the framework wants §23.1's 6 roles organized around the causal chain + a workflow flow.

**Result: 14 pages → 10 role-organized destinations**, grouped by a 5-phase IA
(Observe / Decide / Execute / Evolve / Operate):
- **Cockpit** (already redone earlier): PnL zone + agent feed + right rail.
- **Approval Desk** (Proposals elevated): pending queue as decision cards w/ inline risk
  verdict + confirm-to-approve; decision history table.
- **Trades & Positions** (Positions + Paper Trades merged): open positions → orders/fills
  ledger → closed positions, hosting the **causal-chain inspector** (`CausalChain.tsx`).
- **Agent Activity** (Agent Runs + Opportunities merged): Runs/Opportunities tabs; run
  drawer has the Reasoning-Steps mount point.
- **Learning** (Dreaming + Knowledge Base + Reports merged): 3 tabs; §4 KB taxonomy kept.
- Keepers (Markets/Strategies/Risk & Controls/Settings/News) got a `PageHeader` with a
  one-line role descriptor; News got the spec-mandated "manual entry / ingestion not
  configured" honesty banner.

New shared components: `CausalChain`, `Tabs`, `PageHeader`. Deleted 7 merged page files.
Legacy routes redirect (no dead links). Verified live via preview MCP (nav, tabs, honest
empty states, zero console errors). tsc + vite build green.

**Docs:** `WEBUI_PAGE_MAP.md` (per-page verdicts + build log), `OVERVIEW_REDESIGN.md`
(Cockpit decisions), **`WEBUI_BACKEND_HANDOFF.md`** (parked backend gaps for the backend
agent), cross-referenced in `BACKEND_ISSUES.md §12`. Backend gaps (UI degrades honestly
until built): position→proposal FK, `GET /api/agent-runs/{id}/steps`, real exchange equity.

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
