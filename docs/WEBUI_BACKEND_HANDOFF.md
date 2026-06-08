# WebUI → Backend Integration Handoff  [CLOSED — #1 #2 #3 done; #4 permanently banned]

> 2026-06-07. Written at the end of the **frontend-only** redesign session (see
> WEBUI_PAGE_MAP.md + OVERVIEW_REDESIGN.md). The UI redesign is structurally complete and
> ships honest fallbacks for every gap below. This file lists what the **backend agent**
> can build to light up the parts the UI already has stable mount points for. Nothing here
> blocks the frontend — each item is a capability the UI will use *if/when* it exists, and
> degrades honestly until then. Priority order is the recommended sequence.
>
> **Backend status (2026-06-07, branch `feat/strategy-causal-chain`):**
> - ✅ **#1 DONE** — `positions` now has nullable `proposal_id`/`order_id` FKs, populated
>   by both brokers when a fill opens a position (averaging-in keeps the original opener).
> - ✅ **#2 DONE** — `GET /api/agent-runs/{id}/steps` is live (read-only). NOTE: the
>   `agent_run_steps` table is **not yet written by any agent**, so it returns `[]` today —
>   honest empty. Populating it (agents recording step inputs/outputs) is a separate,
>   larger change touching every agent and was out of this handoff's "expose the table" scope.
> - ✅ **#3 DONE** (2026-06-08, owner authorized): new `portfolio/exchange_sync.py` pulls
>   real account state via the existing read-only Bitget client methods (no client.py
>   change needed). `compute_portfolio` now exposes `exchange_equity_usdt` +
>   `exchange_sync_status` + drift count; `GET /api/exchange/account` is live; scheduler
>   refreshes every 5 min. Read-only against the exchange; external positions are NEVER
>   written into the local positions table (would violate trust §1.1). 9 new tests.
> - 🚫 **#4 NOT DONE** — news ingestion is banned MVP scope (AGENTS.md); intentionally skipped.
>
> Tests: `tests/test_webui_backend_handoff.py` (6); full suite 387 passed.

## Conventions
- All new read endpoints go in the **non-controlled** `aletheia/server/.../data.py` layer
  (per the redesign plan), never in change-controlled modules (risk/, execution/, client.py,
  secrets.py, permissions.py, live.py).
- The UI never fakes a positive state. Each gap currently renders "not recorded" / "not
  available" / "pending backend" — search the cited component for the exact string.

---

## 1. Position → proposal/order causal link (schema gap)  ⭐ highest value  ✅ DONE
**What the UI does today:** `components/CausalChain.tsx` renders the full trust §1.1 chain
(opportunity → strategy → proposal → risk → order → fill → position). Anchored on an **order**
the chain is complete upstream (orders carry `proposal_id` + `risk_decision_id`). Anchored on
a **position**, everything above `strategy_id` shows **"not recorded"** in yellow — because
the `positions` row has no `proposal_id` or `order_id`.

**Backend ask:** add a nullable `proposal_id` (and/or `order_id`) FK to the `positions`
table, populated by the execution path when a fill opens/updates a position. This is the one
schema change that makes the position→upstream direction of the causal chain real.

**Until then:** the UI is honest — it explicitly says the position stores no proposal link.
Do NOT symbol-match to fake it (trust §1.1).

**Touch point:** `frontend/src/components/CausalChain.tsx` (the `anchor.kind === 'position'`
branches). Once the FK exists, resolve the proposal from it instead of rendering "not recorded".

---

## 2. GET /api/agent-runs/{id}/steps  (read-only reasoning trace)  ✅ DONE (endpoint live; table not yet populated)
**What the UI does today:** `pages/AgentActivity.tsx` → `ReasoningStepsSection` shows the
existing tool-call ledger (`/agent-runs/{id}/tool-calls`) plus an honest note: *"Step-by-step
reasoning trace (inputs/outputs per step) pending backend — GET /api/agent-runs/{id}/steps
not yet available."*

**Backend ask (already approved in the plan):** expose the existing `agent_run_steps` table
as a read-only endpoint `GET /api/agent-runs/{id}/steps` returning ordered steps with their
inputs/outputs/timestamps. Read-only, `data.py`.

**Touch point:** `frontend/src/pages/AgentActivity.tsx` — replace the "pending backend" note
with a step list rendered from the new endpoint. The drawer block is already the stable mount
point; no layout change needed.

---

## 3. Real exchange account equity  (portfolio-sync-gap)  ✅ DONE
**What the UI does today:** `components/AccountSnapshot.tsx` + `components/PnLZone.tsx` render
`portfolio.total_value_usdt` with the standing caveat **"aletheia-managed only · exchange sync
pending"**, and `today_pnl` → "—" / "not available" when null. The Trades & Positions exposure
table repeats the drift caveat.

**Backend ask:** an Exchange Account Sync layer that reads the real Bitget account balance/
equity (the account is dedicated to aletheia, so exchange equity is the legitimate hero
number). Surface it on `/api/portfolio` as a distinct field (e.g. `exchange_equity_usdt`) so
the UI can show true account value vs. local managed notional, and reconcile drift.

**Until then:** the caveat stays; the hero number is explicitly labeled as managed notional,
not account equity. (See memory `aletheia-portfolio-sync-gap`.)

**Touch point:** `frontend/src/components/AccountSnapshot.tsx`, `components/PnLZone.tsx`.

---

## 4. (Optional) News ingestion pipeline
**What the UI does today:** `pages/NewsIntel.tsx` shows a yellow `.notice-banner`: *"Manual
entry only. The automated news-ingestion pipeline is not configured (future scope per §17.3 /
AGENTS.md)."* News items only appear if added via `POST /api/news`.

**Backend status:** news ingestion is explicitly **future scope / banned as MVP scope
expansion** (AGENTS.md). This is listed only for completeness — do NOT build it without an
explicit scope decision from the owner. If it is ever enabled, remove the banner.

**Touch point:** `frontend/src/pages/NewsIntel.tsx` (the `.notice-banner` block).

---

## Frontend structures the backend should be aware of (no action needed)
- **Legacy route redirects:** `/positions`, `/paper-trades` → `/trades`; `/agent-runs`,
  `/opportunities` → `/agent-activity`; `/dreaming`, `/knowledge`, `/reports` → `/learning`.
  Old deep links still resolve. (`frontend/src/App.tsx`.)
- **No API contract changes were made** this session. All existing endpoints are consumed
  as-is; the merges are pure frontend composition. `api/client.ts` is unchanged except it
  already had every method used.
- **§4 knowledge-item taxonomy is preserved** in the Learning page's KB sub-tabs — each tab
  queries its real `item_type` (research_note / strategy-memory / daily_review /
  dreaming-logs). Do not collapse these types backend-side expecting the UI to re-separate.

---

## §13 — Indices ticker (SPX / VIX / DXY in topbar)

**Frontend status (already shipped):** `frontend/src/components/MarketTicker.tsx` renders
the topbar Bloomberg-style ticker. Crypto tokens (BTC/ETH/SOL) consume the existing
`/api/market/snapshots` endpoint with real Bitget data. **Indices slots (SPX, VIX) render
`—` with a "feed not wired" tooltip** until the backend lands the endpoint below — this is
deliberate UI Honesty (no fabricated quotes).

**What backend needs to add:** a single read-only endpoint in non-controlled `data.py`:

```
GET /api/market/indices
→ 200 [
    { "symbol": "SPX", "last": 5234.56, "change_pct": 0.42, "as_of": "2026-06-08T18:30:00Z" },
    { "symbol": "VIX", "last": 14.20,   "change_pct": -1.18, "as_of": "..." }
  ]
```

**Implementation notes:**
- Source: pick a free, no-auth quote API (Stooq CSV, Yahoo `query1.finance.yahoo.com`,
  or FRED for SPX). Stooq is the simplest (single GET, CSV).
- **Cache aggressively** — 30–60s TTL in-memory cache to stay polite to the upstream and
  avoid rate limits; the frontend polls every 15s but degrades gracefully if backend
  returns a stale-but-valid quote.
- **Do not block the gateway on upstream failures** — if the upstream is down, return the
  last cached value with `as_of` reflecting actual age, OR `null` for `last`/`change_pct`.
  The UI already handles `null`/missing fields.
- This is a **read-only display feed**. It must NEVER feed risk/execution paths or be
  consumed by an agent's decision logic without an explicit scope decision (those are
  trust-boundary additions; this endpoint is for the dashboard only).
- No new env var / no new credentials required (free APIs).
- No persistent table needed — this is ephemeral display data, in-memory cache only.

**Frontend revival when ready:** in `MarketTicker.tsx`, replace the `INDICES.map(...)`
placeholder block with a `useQuery` against `/api/market/indices` and render the same
`.ticker-token` markup. The CSS / layout / spacing already accommodate it. ~10-line diff.

**Touch point:** `frontend/src/components/MarketTicker.tsx` (search for "INDICES =" and
"NOT WIRED").
