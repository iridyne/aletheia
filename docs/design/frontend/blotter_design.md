# Blotter — Trade Decision Workstation Redesign Spec

> Status: V1 in progress (2026-07-18). Merges four Overview cards + the existing
> `/proposals` Approval Desk into a single trade-blotter workstation at `/blotter`.
> Design language: Bloomberg EMSX blotter + TradingView density + shadcn/TanStack
> data-table toolbar patterns. Terminology derived from FIX Protocol, EMSX, and
> QuantConnect vocabulary — deliberately away from consumer-fintech copy.

## Motivation

The Overview page grew to host four dense cards that all answer variants of the
same question — *what's the state of the opportunity pipeline?*:

- `EntryStatusCard` ("Why no position now") — the current limiting pipeline stage
- `HumanApprovalCard` — proposals waiting for the operator
- `OpportunityFunnelCard` — funnel with rejection reasons
- `DecisionHistoryCard` — recent terminal decisions

Plus a separate `/proposals` page (labelled "Approval Desk" in the nav) that
duplicated the human-approval queue. Five surfaces, one workflow.

Two failure modes surfaced from operator feedback:
1. Overview lost its "single pane of glass" property; the decision-flow cards
   pushed PnL and system-health below the fold.
2. `DecisionHistoryCard` felt like it "only showed successful trades" — a UX
   design issue (see [Root cause](#root-cause-only-trades-are-visible)),
   not a filter bug.

## Naming

**Chosen name: `Blotter`** · route `/blotter`.

Rationale from the parallel research pass (Bloomberg EMSX, IBKR TWS, TradingView
alerts, crypto pro exchanges, QuantConnect vocabulary): *blotter* is the
canonical sell-side/EMS term for a single grid holding pending + working +
filled + rejected orders, with state as a column value and filters at the top.
Three independent research strands converged on it. The concept maps 1:1 onto
the four-card merge.

Rejected alternatives:
- `Decision Desk` (`/desk`) — buy-side flavour, less institutional grounding.
- `Opportunity Blotter` (`/opportunities`) — verbose; "Opportunity" alone drifts
  toward sales-pitch language flagged by the Bloomberg-terminology research.
- `Order Book` (`/orders`) — collides with the order-book concept in crypto UIs.

`Blotter` is generic enough that a future algorithmic-order sub-view (parent →
child slice routing) can nest under the same route without a rename.

## Terminology

Pro-terminology replacements ("adopt list" from research vs. current
consumer-fintech copy):

| Current phrase (consumer) | Replacement (pro) | Source |
|---|---|---|
| "Why no position now" | **Pre-trade Checks** | Buy-side compliance; also the FIX `REASON_CODE` field lineage. |
| "Human Approval" | **Awaiting Sign-off** | Buy-side desk vocabulary; less AI-hype than "human approval". |
| "Opportunity Funnel" / "Funnel" | **Candidates** | Growth-marketing word replaced by a single noun. Do not split into three lifecycle labels; use one term consistently for this panel. |
| "Decision History" | **Candidate Ledger** | The rows aren't orders (most were skipped / rejected before ever becoming one) — they're the history of the opportunity-scoring game. "Ledger" is buy-side account-book vocabulary; pairs naturally with the Candidates panel (current state) as its historical companion. Owner rename 2026-07-18. |
| "Working set" | **Staged** | Aligns with EMSX `STAGED` order state. |
| "AI made a proposal" | *no phrase* — use noun ("a new candidate on the blotter") | Bloomberg and Numerai deliberately avoid marketing the AI author. |
| "Signal generated" | **Indication** (IOI) | FIX Protocol — Indication of Interest. |
| "Missed trade" / "we skipped" | **Expired** / **Rejected** + reason code | FIX `OrdStatus` C/8; one line of state code replaces a sentence of explanation. |

## Layout blueprint

Vertical order (owner-revised 2026-07-18): ledger above the middle band. Ledger
is the higher-information surface (history, filterable); Candidates + Sign-off
are the current-state working panels underneath.

```
┌────────────────────────────────────────────────────────────────────────┐
│  BLOTTER                                                               │
├────────────────────────────────────────────────────────────────────────┤
│  STAGED   AWAITING SIGN-OFF   WORKING   FILLED (TODAY)   REJECTED (24H)│
│    3            2                1            4              12         │  ← 5 fixed status tokens
├────────────────────────────────────────────────────────────────────────┤
│  Candidate Ledger                                                      │
│  [Search…] [Filled 12] [Op Skip 3] [Watch 1] [User Reject 0] [Auto 2]  │
│                                     [All L S]    [1h 6h 24h 7D] [Reset]│
│  ─────────────────────────────────────────────────────────             │
│  Time↓        Symbol  Side  Outcome        Conv  Reason                │
│  14:22:03     BTC-U   L    FILLED          82   PASS                   │
│  14:19:41     ETH-U   L    USER REJECT     54   ttl_expired            │
│  14:15:07     SOL-U   S    OP SKIP         71   PASS                   │
│  ...                                                                   │
│  20 rows · page 1/N   ⏮ ◀ ▶ ⏭                                          │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  ┌── CANDIDATES  (7/12) ──────────────┐  ┌── AWAITING SIGN-OFF (5/12) ─┐│
│  │ Sym  Side Conv Age  Gate   →      │  │ Sym  Side  Conv  TTL         ││
│  │ BTC   L   82  3m   PASS           │  │ ETH  L    76   2m40s         ││
│  │ ETH   L   74  5m   [conv<θ] ⓘ    │  │ SOL  S    81   1m10s         ││
│  │ SOL   S   69  8m   PASS           │  │                              ││
│  │                                   │  │  [Approve]  [Reject]         ││
│  └───────────────────────────────────┘  └──────────────────────────────┘│
│    ↑ Gate is a column with hover popover expanding the full pre-trade  │
│      check trace. NOT a separate panel.                                │
└────────────────────────────────────────────────────────────────────────┘
```

### Why this satisfies "at-a-glance opportunity ecosystem + blockers + status"

- **Top 5-column status strip** (not a narrative one-liner banner). Uppercase
  labels, aligned numerics beneath — copies Bloomberg's "state as token, not
  sentence" convention. Eyes hit the pipeline inventory in the first second.
- **Ledger next**, at full width, right under the strip. It is the historical
  mass — the biggest information surface on the page — and it is where the
  operator answers "what has actually happened?" before drilling into current
  candidates. Every terminal outcome (Filled / Op-Skip / Watch / User Reject /
  Auto Reject / Reject-?) is a first-class row; nothing is hidden behind "click
  to expand".
- **Middle 2-panel band last** (not 3 — dropping a separate Gate Log panel per
  the adversarial-critique pass, which noted 3 panels at 4/12 columns would
  overflow at 1440 px). Candidates 7/12 + Awaiting Sign-off 5/12; Gate becomes a
  column on the Candidates table with a popover for the full pre-trade check
  trace. These panels are current-state working views, so they sit below the
  historical mass.

### Density targets

- Row height ~28 px, cell padding 6/10 (vertical/horizontal).
- Numerics in `IBM Plex Mono`; labels in `IBM Plex Sans`. All numeric columns
  right-aligned with `font-variant-numeric: tabular-nums`.
- ≥ 6 rows visible in each of the two mid-band tables at 1440 px viewport.
- Candidate Ledger defaults to 20 rows/page.

## Candidate Ledger table spec

Component: `frontend/src/components/DataTable.tsx` (existing wrapper over
`@tanstack/react-table`). This wrapper already supports client-side sort,
global filter, pagination, and persistent column visibility.

**Default filter state**: no outcome pre-filter — all six terminal outcomes
(`trade / operator_skip / operator_watch / user_reject / auto_reject /
reject_unknown`) render as sibling rows.

**Filter toolbar** (V1):
- `Search` — global text filter over symbol / notes.
- `Outcome` — multi-select faceted filter (chips show count).
- `Symbol` — multi-select.
- `Side` — L / S.
- `Time window` — 1D / 7D / 30D quick chips.
- `Reset` — visible only when at least one filter is active (shadcn convention).

**Sortable columns**: Time ↓ (default), Symbol, Conviction, Outcome, Reason.

**Row action**: click → open existing `DecisionTraceDrawer`
(`frontend/src/pages/Overview.tsx:651`) from the right; it already surfaces
pi_reasoning / gate trace / execution report.

## Root cause: "only trades are visible"

**Not a filter bug — a UX design issue.** Code investigation
(2026-07-18):

- Backend `/analytics/operator-decisions`
  (`aletheia/server/routes/analytics.py:797-968`) returns all six outcomes
  with per-outcome counts.
- Frontend `DecisionHistoryCard` (`frontend/src/pages/Overview.tsx:525-633`)
  iterates all items and renders every outcome.
- Neither side filters to trades.

What makes the card *feel* trade-only:
1. Detail list is collapsed by default (`useState(false)` at `Overview.tsx:527`).
2. The pulse header is trade-centric — appends `· ✓ trade {age}` or `· no
   trades {hours}h` (`Overview.tsx:554-569`). Other outcomes only appear as an
   icon in the "most recent" slot.
3. `analytics.py:844` excludes `signal_source = 'system_cascade'` from the
   `pi_decisions` query, which can suppress cascade-originated skips.

**Fix in V1**: replace the collapsed pulse UI with a full-blotter table where
every row is a first-class citizen. No sibling row is hidden behind a click.

## V1 scope — shipping now

- New page `pages/Blotter.tsx` with three regions in this vertical order:
  status-token strip → Candidate Ledger (full-width) → Candidates + Sign-off band.
- `App.tsx`: register `/blotter` route; rename sidebar entry to `Blotter` in
  the Decision group; `/proposals` legacy redirect → `/blotter?filter=awaiting_signoff`.
- Migrate `EntryStatusCard` → Candidates table + Gate column with popover.
- Migrate `OpportunityFunnelCard` → status counts feed the top strip; rejection
  reasons feed the Candidate Ledger Outcome filter facets.
- Migrate `HumanApprovalCard` → Awaiting Sign-off panel with inline
  Approve/Reject (logic ported from `pages/Proposals.tsx`).
- Migrate `DecisionHistoryCard` → Candidate Ledger table; also merge the existing
  `Proposals` decided-history table (both were duplicated views of the same
  data).
- Overview: delete migrated cards; add a single 1-row summary strip pointing to
  `/blotter`.
- Backend: `/analytics/operator-decisions` — add `outcome: str | None` and
  `symbol: str | None` query parameters. Filter items post-merge (after the
  rejection-supersedes-trade merge at `analytics.py:929-944`). Keep counts
  computed over the full window so the top status strip does not lie when the
  history table is filtered.

## V2 backlog — deferred

Items intentionally trimmed from V1 by the adversarial-critique pass. Ship when
V1 is stable and the operator asks for one.

| Item | Cost estimate | Notes |
|---|---|---|
| **Hotkey `1` / `2` for Approve / Reject** on the Sign-off panel | S | Linear Triage convention. Requires a keyboard-focus manager so hotkeys don't fire while a modal is open or an input is focused. |
| **Column visibility dropdown** (`Columns ▾`) on Candidate Ledger | XS | Already supported by `DataTable` — enable `columnToggle={true}` and give the table a `tableId` to persist to localStorage. |
| **CSV Export** on Candidate Ledger | S | Client-side blob download from the current filtered rows. |
| **Custom time-window picker** (date-range) on Candidate Ledger | M | Currently 1D/7D/30D quick chips. Adds a calendar UI + backend `since`/`until` params. |
| **`sort_by=conviction` server-side sort** | S | Client-side sort is sufficient at page-size 20; only needed when total history > ~200 rows and we go server-paginated. |
| **`since` cursor pagination** on `/analytics/operator-decisions` | M | Needed only when the operator wants a history explorer beyond the 200-row cap. |
| **`/analytics/blotter-summary` dedicated aggregation endpoint** | M | Feeds the top status strip in one small request instead of re-using the full `/operator-decisions` payload. |
| **Advanced faceted filters** (Scanner family, Gate reason facets) | M | Rejection-reason drill-down currently baked into Overview's funnel; a facet-per-reason on Candidate Ledger would let operators bulk-review "everything blocked by X". |
| **Awaiting Sign-off empty state upgrade** (hint: last time queue was non-empty) | XS | Empty state currently says "clear"; a "last proposal N min ago" mono line would help pattern recognition. |
| **Candidates panel: inline "run ranker now" action** | M | Currently the operator has to jump to Command Center for this; a one-click ranker kick from Candidates would close the loop. |
| **Bloomberg-style column header context menu** (Asc / Desc / Hide / Freeze) | M | Beyond `DataTable`'s current click-to-sort — needs a `DataTableColumnHeader` primitive per shadcn Tasks example. |
| **Filter presets** (`Rejected only`, `My conviction > 70`) as saved chips | M | Would be genuinely useful for review sessions; scope creep for V1. |
| **Server-side global search across full history** | M | Client-side text filter only searches the current page; a real search over `notes` + `symbol` needs a backend endpoint. |
| **Per-row micro-sparkline** (7D win rate for that symbol) | L | Useful for pattern recognition but requires precomputed per-symbol stats. |

### Performance backlog

Observed 2026-07-18: the Candidates panel visibly loads slower than the
Candidate Ledger on cold Blotter opens. **Root cause is not the frontend** —
`/analytics/opportunity-funnel` executes ~15 SQL statements + a full
`build_current_entry_status()` snapshot (Universe → Ranker → filters →
rejection reasons), while `/analytics/operator-decisions` is two SQL queries.
The funnel endpoint is inherently 5–10× the wall clock.

**Already done in V1** (context so future changes don't undo them):

- Blotter `CandidatesPanel` and `StatusTokenStrip` share the exact
  `['opportunity-funnel', 24]` queryKey, so react-query dedupes to one
  in-flight request and one warm cache. `current.candidates` is a snapshot
  and does not depend on the window hours, so the 24h key is loss-free for
  this panel.
- All three heavy queries (`opportunity-funnel`, `operator-decisions`,
  `proposals`) carry `staleTime: 10–20s`, so mounting from the Overview
  summary strip → Blotter route change reuses the warm cache instantly.
- `CandidatesSkeleton` renders shimmering row placeholders during the cold
  fetch so perceived latency stays low (honours `prefers-reduced-motion`).

**Completed 2026-07-18** (goal-driven sweep):

| Item | Impact | Where it landed |
|---|---|---|
| **Split `/analytics/opportunity-funnel` into fast/slow halves** | High | New route `GET /analytics/opportunity-funnel/current-snapshot` returns `{window_hours, current, config, family_ttl_sec}` only — ~6 base SQL + `build_current_entry_status` internals. The full route unchanged in shape. Frontend `CandidatesPanel`, Blotter `StatusTokenStrip`, and Overview `BlotterSummaryStrip` all switched to the fast route (queryKey `['opportunity-funnel-snapshot', 24]`, deduped across all three consumers). |
| **In-process TTL cache for the current-snapshot payload** | High | `_cached_current_snapshot(db, hours)` in `aletheia/server/routes/analytics.py` — 5 s TTL, keyed on `hours`, guarded by `threading.RLock`. Cache lookup runs under the lock; the actual compute runs outside the lock so concurrent misses don't head-of-line. Back-to-back mounts + the frontend's 30 s poll now serve from one compute. |
| **SQL consolidation inside `_funnel_cohort_inputs`** | Medium | Was 7 count queries over `opportunities` (`emitted`, `still_open`, `queued`, `reviewed`, `expired`, `setup_count`, `active_setup_count`); now 2 (`SUM(CASE WHEN status=...)` + `COUNT(DISTINCT CASE WHEN ...)`). Both endpoints benefit. The full endpoint no longer redoes 3 status counts locally. |
| **Prefetch on Overview summary strip hover** | Low-Medium | `BlotterSummaryStrip` in `frontend/src/pages/Overview.tsx` — `onMouseEnter` + `onFocus` fire `qc.prefetchQuery` for all three Blotter queryKeys (`opportunity-funnel-snapshot`, `operator-decisions`, `proposals`) with matching `staleTime`. Prefetch is a no-op when cache is fresh, so no wasted work. |
| **Server-Timing header on slow endpoints** | Diagnostic | Both funnel endpoints emit `Server-Timing`. Fast endpoint: `cache_hit;dur=0.0` OR `cohort;dur=... build_current;dur=... total;dur=...`. Full endpoint: `cohort;dur=... snapshot;dur=... universe;dur=... build_current;dur=... total;dur=...`. Chrome devtools "Timing" tab surfaces these without any client changes. |
| **Push-down of `outcome` filter to `/analytics/operator-decisions`** | Low | Blotter `CandidateLedger` now includes `outcomeParam` in its queryKey and passes `opts.outcome` when non-empty. Correctness under scale: when total history > 200, filtering server-side returns the most recent 200 rows OF THE SELECTED OUTCOME instead of clipping older Filled rows behind newer Skips. `side` / `search` deliberately stay client-side (semantics don't match the backend `symbol` exact-match param). |
| **hours=1 independent queryKey — no code change** | Marginal | Evaluated: `current.*` computed by `build_current_entry_status` does technically read `window_hours` (to build `window_start` for `_open_ranker_candidates`). But we saw no observable divergence between hours=1 and hours=24 in the ranker candidates set during the sweep, and the 24 h key is shared across all three consumers so switching one to hours=1 would double the funnel-cache footprint. Revisit only if a specific bug shows up. |

## Overview — what remains after migration

- `PnLZone` (unchanged) — Anchor 1.
- `AgentFeed` (unchanged) — Anchor 2.
- Right rail: `Risk State`, `System` panel, `Go-Live Scorecard`.
- **New**: a 1-row Blotter summary strip
  (`Staged 3 · Awaiting 2 · Working 1 · [→ open]`) with a link to `/blotter`.
  Preserves visibility of the pipeline state without hosting a full workflow.

## Backend changes needed for V1

`aletheia/server/routes/analytics.py` — `/analytics/operator-decisions`:

1. Add `outcome: str | None = None` — accept a comma-separated list; filter over
   `all_items` **after** the rejection-supersedes-trade merge at
   `analytics.py:929-944`. Applying the filter before the merge would break the
   dedup logic.
2. Add `symbol: str | None = None` — canonicalize via the already-imported
   `to_canonical` helper (`analytics.py:827`), then push down into both SQL
   queries so the DB does the filter cheaply.
3. Keep `counts` computed over the **full** window
   (`analytics.py:948-953`) — otherwise the top status strip would say
   different numbers when the history table is filtered.
4. Cross-check the `signal_source != 'system_cascade'` exclusion at
   `analytics.py:844`. If cascade skips should appear on the blotter, replace
   with an optional include-cascade flag (default off preserves current
   behaviour).

`limit` remains capped at 200 for V1; broader history uses the V2 `since` cursor.

## References

- Overview redesign spec (implemented 2026-06-07): `overview_redesign.md`
- WebUI page map: `webui_page_map.md`
- UI design system: `ui_design_system.md`
- Existing DataTable component: `frontend/src/components/DataTable.tsx`
- Existing Proposals page (source of Awaiting Sign-off logic): `frontend/src/pages/Proposals.tsx`
- Existing DecisionTraceDrawer (row action target): `frontend/src/pages/Overview.tsx:651`
