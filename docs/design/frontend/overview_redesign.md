# Overview / Cockpit — Redesign Spec (IMPLEMENTED 2026-06-07)

> Conversational redesign with the owner, 2026-06-07. Stack locked: React + hand-written
> CSS (no Tailwind/shadcn). Design language: Bloomberg density + TradingView chart link +
> OpenAlice agent-native + Linear polish. This doc captures agreed decisions as we go.

## Anchors (owner-defined)
1. **PnL / account value / position value** — first-screen.
2. **What agents recently did — as an information FEED.**
3. Rest: Claude-led, referencing the 4 platforms above.

## Decision 1 — PnL / Account zone (DECIDED)

Layout = OpenAlice card-cluster (top) + TradingView large equity curve (bottom):

```
[ ACCOUNT VALUE hero ] [Today][Unreal][Realized][Exposure]   <- tile row
[ ===== equity curve (full width, ~30d, time-window toggle) ===== ]
```

- **Hero card:** account value big mono number + honest tag `demo · managed`.
  - In local paper mode, `/api/portfolio.total_value_usdt` is the local cash
    ledger plus the current marked open PnL. It is unavailable, never replaced
    by position notional, when close economics are incomplete.
  - In exchange modes, the source is the named fresh exchange/UTA account
    equity as before.
- **4 tiles, each with one line of context (OpenAlice style):**
  - Today PnL — `today_pnl` + day % , green/red.
  - Unrealized — `unrealized_pnl` ("holding").
  - Realized — `realized_pnl` ("all-time").
  - Exposure — `exposure_by_symbol` (BTC/ETH/SOL share).
- **Equity curve:** full-width, larger than a sparkline, from
  `/api/market/account-equity-history`; optional time-window toggle. It draws
  one explicit account-equity snapshot series. Raw and verified strategy-PnL
  cohorts remain in Analytics and never become extra Overview lines.

## Decision 2 — Agent activity feed (DECIDED)

A switchable feed with **3 modes**; user preference persisted (localStorage, later config):
1. **Narrative (思考流, OpenAlice-style)** — "Market Observer found BTC broke SMA20 →
   generated long opportunity BTCUSDT (72%)". Reads agent **outputs** (opportunities w/
   thesis, proposals w/ rationale, research notes, dreaming logs). Click → thesis/rationale.
   Surfaces "what the AI is thinking".
2. **Ops (运行日志)** — "market_observer ✓ completed · scheduled · 3 tools · 1 output · 4.2s".
   Reads `agent_runs` metadata. Surfaces who ran / status / duration.
3. **Both (合一)** — each row carries run metadata AND output narrative.

Toggle control: small 3-segment switch (`Narrative | Ops | Both`) in the feed card header.

### Initial version (no backend changes)
Modes interleave two tracks by **timestamp**, NOT strict run→output causal binding (backend
has no direct run→output association query yet). This is honest — we do not fabricate
causality. Data sources already available:
- `GET /api/agent-runs` → run metadata (agent_name, status, trigger_type, model_provider,
  started_at/ended_at, tools_called, outputs_created, errors).
- `GET /api/agent-runs/{id}/tool-calls` → per-tool input_data/output_data.
- `GET /api/opportunities` → symbol, direction_bias, thesis, confidence, source_agent, created_at.
- `GET /api/proposals` → agent_rationale, symbol, direction, status, created_at.
- `GET /api/dreaming/summary` → recent research notes / hypotheses / dreaming logs / runs.

### Backend TODO to upgrade Narrative → strict causal chain (parked, needs authorization)
Goal: a row "this run at 09:32 produced THIS opportunity/proposal". Requirements:
- **run→output association.** Today `agent_runs.outputs_created` is a JSON list of output
  IDs/titles with no reverse index. Options: (a) ensure each created entity stores its
  originating `run_id` (opportunities/proposals/knowledge_items gain a `run_id` column, or a
  join table), then expose `GET /api/agent-runs/{id}/outputs` returning the real rows; or
  (b) add `GET /api/agent-runs/{id}/steps` (read-only, reads existing `agent_run_steps`
  table — already approved in principle) so the feed can show the run's step-by-step actions
  + inputs/outputs as the narrative.
- Non-controlled module (`server/routes/data.py` + `storage/`); needs a focused test.
- Until then: time-interleaved feed is the honest stand-in.

## Build status (2026-06-07)
- ✅ Decision 1 PnL zone — `frontend/src/components/PnLZone.tsx` (+ MetricTile `context` prop).
- ✅ Decision 2 agent feed — `frontend/src/components/AgentFeed.tsx` (3 modes, localStorage `aletheia.feedMode`, time-interleaved/honest).
- ✅ Decision 3 page assembly — `frontend/src/pages/Overview.tsx` rewritten; `.cockpit-main` + `.cockpit-rail` CSS.
- tsc -b + vite build green; shipped to `aletheia/dashboard_static/`. Not committed (awaiting visual review).
- Browser MCP unavailable on this box → no automated screenshot; owner reviews at `:8787` or dev `:5180`.

## Decision 3 — Full-page skeleton (DECIDED)

```
┌─ PnL / Account zone (full width) ───────────────────────────┐  Anchor 1
└─────────────────────────────────────────────────────────────┘
┌─ Agent feed (main width) ──────────┐ ┌─ Right rail (narrow) ─┐  Anchor 2 left
│ [Narrative | Ops | Both]           │ │ Approval Desk (pending)│
│ 09:32 🔍 ...                       │ │ Risk state             │
│ 02:00 🌙 ...                       │ │ System / connections   │
└────────────────────────────────────┘ └───────────────────────┘
```

Reading order = money first (PnL) → what the AI is doing (feed, main width) → what needs
YOU (right rail: pending proposals = the one human-in-loop control point) + system health.
Bloomberg dense zoning + OpenAlice agent-narrative-primary, control secondary.

**2026-07-18 amendment**: the "Approval Desk (pending)" tile in the right-rail sketch,
plus the `EntryStatusCard` / `OpportunityFunnelCard` / `DecisionHistoryCard` that had
since accumulated on this page, all relocated to the new `/blotter` workstation
(`pages/Blotter.tsx`). Overview keeps its right-rail Risk state + System panel + Go-Live
Scorecard; the pending-proposals visibility is preserved as a single-row `.ov-blotter-strip`
between PnL and the agent feed (Staged / Awaiting Sign-off / Filled today / Rejected 24h
counts with a click-to-open link + `onMouseEnter` prefetch). See `blotter_design.md`.

### Owner clarification — viewport-bounded activity (2026-07-12)

The Agent Activity card is not content-height and is not tied to the right rail. Its visible
height is the browser viewport height minus the card's actual rendered top position and the
normal page-bottom gap. This keeps its bottom edge inside the current viewport even when
browser zoom, window height, the setup banner, or the PnL zone changes the space above it.

- The card header remains visible; only the activity rows scroll inside the card.
- Resize/zoom and asynchronous layout changes above the card trigger a remeasurement.
- When less than 240px remains, the card uses a 240px usability floor and the page scrolls.
- The existing latest-40 limit, three feed modes, polling, and detail drawer are unchanged.
- The right rail remains naturally sized and independent from the feed height.

## Backend tasks parked (need explicit authorization when we start)
1. **Real exchange balance** for hero account value (portfolio-sync-gap). `client.py` already
   has `get_balances()` / `get_account()` / `get_positions()` (read-only, implemented).
   Work = new read-only endpoint calling them + coins→USDT via ticker + demo/live label;
   + focused regression test. Decide spot vs futures equity scope.
   - **Owner clarification (2026-06-07):** the Bitget account is DEDICATED to aletheia, so
     exchange equity = the true account value and is the legitimate hero number (the current
     placeholder `total_value_usdt` = deployed notional only, which *understates* by ignoring
     idle cash). Once wired: hero = live exchange equity; caveat changes from "aletheia-managed
     only" → `demo/live · synced HH:MM`. Honest residual = local POSITION tracking can drift
     from exchange (manual trades / liquidation / funding) → that's a reconciliation concern
     for the Positions view, NOT the hero number (hero reads exchange live, bypassing drift).
2. **Agent run→output association** (see Decision 2 backend TODO above) — upgrades the
   Narrative feed from time-interleaved to strict causal.
