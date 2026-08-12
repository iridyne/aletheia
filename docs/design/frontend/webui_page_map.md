# WebUI Page Map — per-page verdict + target IA

> 2026-06-07. Companion to OVERVIEW_REDESIGN.md. Decides, page by page, whether each
> current dashboard page is KEPT / MERGED / ELEVATED / REMOVED, with reason + reference
> object. Basis: spec §23.1 six roles + causal chain + the 4 design references
> (Bloomberg / TradingView / OpenAlice / Linear). Owner picked "merge to role workbenches,
> but justify each page individually" (2026-06-07).

## §23.1 six roles → which pages serve them
1. control room → Cockpit
2. approval desk → Blotter (2026-07-18; was Proposals)
3. trading cockpit → Positions, Paper Trades, Charts
4. agent transparency → Agent Runs, Opportunities
5. strategy lifecycle → Strategy Lifecycle
6. dreaming / learning → Learning workbench (target: Contribution, Experiments, Dreaming,
   Knowledge Base, Operations)
(+ ops, cross-role) → Settings, Risk & Controls
(+ out-of-spec) → News Intel

## Per-page verdicts

### Cockpit / Overview (/) — KEEP ✅
Role: control room. PnL/account truth remains primary, and “Why no position now” uses the
read-only `/analytics/opportunity-funnel.current` projection rather than inferring health from
unchanged equity. It shows the current limiting stage, next trigger, per-candidate retry/expiry,
and separates unique setups from per-snapshot generations. Snapshot supersede, true TTL,
Operator terminal actions and deterministic reviews are separate exit classes; raw `expired`
is never presented as “all candidates timed out”. Ref: Bloomberg PORT + UI honesty policy.

### Proposals (/proposals) — SUPERSEDED 2026-07-18 → /blotter 🗑️
Role: approval desk — the single human-in-loop control point (§15.3, §29 live unlock is human;
trust §3.4 approve must preserve proposal ID + explicit confirm). ELEVATED for two months
(2026-06-07 → 2026-07-18) as a standalone Approval Desk page (`pages/Proposals.tsx`), then
merged into the Blotter workstation below. The `/proposals` route now redirects to
`/blotter`; `pages/Proposals.tsx` has been deleted. The `ProposalRationaleDetail`
component moved to `components/ProposalRationaleDetail.tsx` so AgentFeed's drawer keeps
rendering it. See "Blotter (/blotter)" below and `blotter_design.md` for the merger.

### Blotter (/blotter) — NEW 2026-07-18 (standalone) ✨
Role: sell-side EMS-style workstation absorbing the former Approval Desk **plus** the four
decision-flow cards that had accumulated on the Overview right rail (EntryStatusCard,
HumanApprovalCard, OpportunityFunnelCard, DecisionHistoryCard). Three regions in one page:
five uppercase status-token strip at top; full-width Candidate Ledger (every terminal
outcome as a sibling row, filterable by outcome/side/time window) middle; Candidates
(7/12) + Awaiting Sign-off (5/12) band at bottom. Retains the trust §3.4 approve/reject
semantics — inline in the Sign-off panel with confirmation dialogs. Ref: Bloomberg EMSX
blotter + IBKR TWS + shadcn/TanStack Data Table. Design language: buy-side vocabulary
(Blotter / Candidates / Sign-off / Ledger) chosen over consumer-fintech phrasing. See
`blotter_design.md` for the full spec + V2 backlog + performance write-up.

### Positions (/positions) + Paper Trades (/paper-trades) — MERGE → "Trades & Positions" 🔗
Two ends of ONE causal chain: orders/fills (Paper Trades, §23.9) → positions (§23.10). Natural
single blotter: open positions on top, order/fill history below, closed positions/PnL. This is
the home of the CAUSAL-CHAIN INSPECTOR. Ref: Freqtrade trade table / TradingView account panel /
Bloomberg blotter. Honesty: keep stop/TP, close_reason, realized PnL; local position vs exchange
drift is a reconciliation note here, not hidden.

### Agent Runs (/agent-runs) + Opportunities (/opportunities) — MERGE → "Agent Activity" 🔗
Both are agent transparency (§23.4 runs, §23.7 opportunities). An Opportunity IS an agent output
(market observer's finding, with thesis) — it belongs with the run transparency, not as an orphan
list. Full-page version of the Cockpit feed's narrative/ops duality. Hosts the approved read-only
GET /api/agent-runs/{id}/steps drill-down (step-by-step reasoning + tool inputs/outputs). Ref:
OpenAlice reasoning log / AlphaQuanter transparent tool calls. Cross-link opportunities→charts.

### Dreaming (/dreaming) + Knowledge Base (/knowledge) + Reports (/reports) — MERGE → "Learning" (tabs) 🔗
All read knowledge_items, different temporal framing: Dreaming (§23.5) = live "what was
consolidated recently"; KB (§23.6) = searchable library (4 type tabs); Reports = periodic daily
reviews + post-trade reviews + performance metrics. One workbench, tabs by framing. CRITICAL
honesty: tabs must preserve the §4 knowledge-item taxonomy — no type laundering to fill a tab.
Performance metrics (drawdown/win-rate from promotion gates) may live as a Reports tab OR move to
Strategy Lifecycle (decide at build). Ref: OpenAlice memory surface / Hermes skills+memory /
Sophia narrative memory.

**2026-07-19 target extension — Learning Contribution Observability:** the current three-tab merge
is shipped, but Reports remains activity/context-oriented and cannot answer whether Learning caused
improvement. The canonical target keeps `/learning` and moves to five tabs:

1. Contribution (default measured `lco-v1` projection)
2. Experiments
3. Dreaming
4. Knowledge Base
5. Operations

Contribution follows the Blotter density pattern: five independent status tokens, full-width path
ledger, 7/5 current-cohort/read-only-recommendation band, append-only verdict history, and a bounded
detail drawer. Operations separates evaluator health, catch-up and durable last failure. Existing
knowledge taxonomy and actions remain under Knowledge; they do not appear as contribution authority.
Legacy `/dreaming`, `/knowledge` and `/reports` must select a deliberate tab rather than all opening
Dreaming. The broader API/UI, responsive, and error-state design is the
[`learning_observability`](../proposals/learning_observability.md) Proposal,
not current UI behavior.

### Strategy Lifecycle (/strategies) — KEEP ✅ (standalone)
Role: lifecycle (§23.11). Distinct pipeline view (draft→…→live_limited) with promotion gates.
Ref: Hummingbot strategy management.

### Charts (/charts) — KEEP ✅ (standalone, "Markets")
Market-data view: price chart + trade markers + watchlist + opportunities-on-chart (§23.13).
Self-contained, lightweight-charts. Distinct from the trade blotter. Ref: TradingView.

### Risk & Controls (/risk) — KEEP ✅ (standalone, de-dup vs Cockpit rail)
§23.12 nominally puts risk display in Settings, but a dedicated "brakes console" is defensible for
a trading system (slam-the-brakes surface): full risk boundaries + kill switch w/ reason + exposure
detail + gateway control. De-dup: Cockpit rail = glanceable + quick pause; Risk & Controls = deep.
Ref: standard trading risk console.

### Settings (/settings) — KEEP ✅ (standalone)
§23.12. 13 cards (model/exchange creds/live unlock/permission/autostart/update/telegram/risk
display…). Too heavy to fold elsewhere. Possible future split, not now.

### Usage Insights (/usage) — KEEP ✅ (standalone observability workstation)
Role: cross-phase consumption and evidence observability. Reading order is Consumption & Spend →
Token Composition → Usage & Cost Trend → Breakdown → Reliability & Budgets → Data Coverage &
Gaps → Research Yield. It defaults to 1D, progressively loads summary before operations/analysis,
keeps cost provenance explicit, and bounds every detail table. Coverage is supporting evidence,
not the first visual anchor or a synthetic radar score. See `usage_insights_design.md`.

### News Intel (/news) — KEEP + HONEST LABEL ⚠️
Out of MVP spec: §17.3 lists news ingestion as FUTURE; AGENTS.md bans news ingestion as scope
expansion. Already built (page + backend). Owner decision (2026-06-07): keep it, but label
honestly in-UI ("manual entry / ingestion pipeline not configured") — do not fake an auto news
feed. Lives under OPERATE (or a "Lab/Future" affordance), off the primary trading path.

## Target IA — 14 pages → 10 destinations, grouped by workflow phase

```
OBSERVE   Cockpit · Markets (Charts)
DECIDE    Blotter (Approval Desk + Overview decision cards) · Agent Activity
EXECUTE   Trades & Positions (Positions + Paper Trades)   ← causal-chain inspector
EVOLVE    Strategy Lifecycle · Learning (Contribution + Experiments + Dreaming + KB + Operations target)
OPERATE   Risk & Controls · Settings · News (labeled)
```

Historical Decide row (2026-06-07 → 2026-07-18):
`DECIDE  Approval Desk (Proposals) · Agent Activity (Agent Runs + Opportunities)`

Net merges: Positions+PaperTrades (2→1), AgentRuns+Opportunities (2→1), Dreaming+KB+Reports (3→1).
Could go further to ~7 (e.g. fold Charts into Trades, Risk into Settings) but not recommended —
those are genuinely distinct surfaces. Nav stays data-driven (App.tsx NAV array) so regroup is a
new array, not a markup rewrite.

## Causal-chain inspector (cross-cutting, not a page)
Lives primarily in Trades & Positions; reachable by clicking any proposal/order/position ID
anywhere. Shows the full proposal → risk_decision → order → fill → position chain. All causal IDs
already exist end-to-end — including `positions.proposal_id`, `positions.order_id`, and
`positions.opportunity_id` (2026-07-07 update, #30: the earlier "positions→proposal is the one gap"
note is stale; the schema now carries these fields and every broker writes them at open time,
including `adopt_position()` which builds a synthetic-but-honest chain marked `source='adopted'`).
No backend change required for the causal-chain inspector — read what's there, no gap to mark.

## Build order (proposed, after structure confirmed)
1. ✅ DONE (2026-06-07) Nav regroup (App.tsx NAV array → 5 phases: Observe/Decide/Execute/
   Evolve/Operate). Interim honesty: routes NOT yet merged — each existing route sits under
   its target phase; Charts relabeled "Markets". Merged destinations get built in steps 2-5,
   at which point nav items collapse. No dead links (nav points only at existing routes).
2. ✅ DONE (2026-06-07) Approval Desk (Proposals) redesign — `frontend/src/pages/Proposals.tsx`.
   Pending Queue = rich decision cards (dir tag + params + WHY rationale + inline risk verdict
   fetched per-card + Approve/Reject w/ confirm + links). Decision History = DataTable + drawer.
   Honest: risk verdict from /proposals/{id}/risk-decision, "no decision recorded" when absent;
   approve requires explicit confirm (trust §3.4). No backend change. tsc+build green.
3. ✅ DONE (2026-06-07) Trades & Positions merge + causal-chain inspector —
   `frontend/src/pages/TradesPositions.tsx` (Positions + PaperTrades deleted; legacy
   /positions + /paper-trades routes redirect to /trades). Open positions (grouped) →
   Orders & Fills ledger → Closed positions. New `components/CausalChain.tsx` embeds the
   trust §1.1 chain (opportunity→strategy→proposal→risk→order→fill→position) in the order
   AND position drawers. HONESTY: order anchor resolves upstream via proposal_id +
   risk_decision_id; position anchor only has strategy_id, so its upstream nodes render
   "not recorded" (yellow), never symbol-matched. Exposure carries the sync-drift caveat.
   No backend change. tsc+build green; strings verified in shipped bundle.
   Backend gap (documented, parked): Position has no proposal_id/order_id FK — the
   order→position direction of the causal chain can't be shown without a schema add.
4. ✅ DONE (2026-06-07) Agent Activity merge — `frontend/src/pages/AgentActivity.tsx`
   (AgentRuns + Opportunities deleted; legacy /agent-runs + /opportunities redirect to
   /agent-activity). Two tabs (Runs / Opportunities) via new `components/Tabs.tsx`. Runs
   tab keeps the trigger buttons + tool-call drill-down; Opportunities tab keeps thesis +
   strategy link. The run drawer's "Reasoning Steps" block is the stable mount point for
   the approved-but-unbuilt GET /api/agent-runs/{id}/steps — today it shows the tool-call
   ledger + an honest "step trace pending backend" note. Decide-phase nav now reads
   Approval Desk / Agent Activity. No backend change. tsc+build green; verified in bundle.
   Backend TODO (parked for backend agent): GET /api/agent-runs/{id}/steps read-only
   endpoint over the existing agent_run_steps table (per plan; non-controlled data.py).
5. ✅ DONE (2026-06-07) Learning merge — `frontend/src/pages/Learning.tsx` (Dreaming +
   KnowledgeBase + Reports deleted; legacy /dreaming + /knowledge + /reports redirect to
   /learning). Three top tabs (Dreaming / Knowledge Base / Reports) via `components/Tabs`.
   §4 taxonomy preserved: KB sub-tabs each query their real item_type (research_note /
   strategy-memory / daily_review / dreaming-logs) — no type laundering. Markdown renderer
   carried over verbatim. Evolve-phase nav now reads Strategies / Learning. No backend
   change. tsc+build green; verified in bundle. NET RESULT: 14 pages → 10 destinations
   complete (Cockpit · Markets · Approval Desk · Agent Activity · Trades & Positions ·
   Strategies · Learning · Risk & Controls · Settings · News).
6. ✅ DONE (2026-06-07) Per-page visual upgrade — new `components/PageHeader.tsx` (title +
   one-line ROLE DESCRIPTOR stating each page's place in the Observe→Decide→Execute→Evolve
   →Operate flow + causal chain). Applied to all 10 destinations. Directly answers the
   "content / transparency insufficient" pain point — every page now says what it's for.
   News Intel gained the spec-mandated honesty banner (`.notice-banner`: "Manual entry
   only · ingestion pipeline not configured", §17.3). RiskControls inline 2-col grid →
   responsive `.risk-grid`. Settings kept light-touch (header only; 638-line page, smallest
   change). Verified live in browser (preview MCP): nav 5-phase structure, tabs switch,
   honest empty states ("No orders yet" / "flat" / News banner), zero console errors,
   legacy redirects resolve. tsc+build green.

## STATUS: redesign structural pass COMPLETE (2026-06-07)
All 6 build steps done. 14 pages → 10 role-organized destinations. Net deletions: 5 page
files (Positions, PaperTrades, AgentRuns, Opportunities, Dreaming, KnowledgeBase, Reports
→ merged into TradesPositions / AgentActivity / Learning). New shared components:
CausalChain, Tabs, PageHeader. All frontend-only; backend gaps documented for the backend
agent (position→proposal FK, GET /api/agent-runs/{id}/steps). Remaining = polish/iteration
on real data once backend fills the parked gaps.

7. ✅ DONE (2026-07-18) Blotter workstation — new `pages/Blotter.tsx`. Merges the former
   Approval Desk (`pages/Proposals.tsx`, now deleted) with the four decision-flow cards
   that had accumulated on the Cockpit right rail (EntryStatusCard, HumanApprovalCard,
   OpportunityFunnelCard, DecisionHistoryCard). Three regions: five-column uppercase
   status-token strip → full-width **Candidate Ledger** (Bloomberg blotter — every
   terminal outcome as a sibling row; six-facet outcome filter with server-side push-down,
   client-side side/search filters) → **Candidates** (7/12) + **Awaiting Sign-off** (5/12)
   band with inline Approve/Reject actions and click-to-open trace drawer. Overview keeps
   a 1-row Blotter summary strip (Staged / Awaiting / Filled today / Rejected 24h) with
   `onMouseEnter` prefetching all three Blotter queryKeys. New shared component
   `components/DecisionTraceDrawer.tsx` (extracted from Overview).

   Vocabulary switched from consumer-fintech to sell-side EMS: "Why no position now" →
   *Pre-trade Checks*; "Human Approval" → *Awaiting Sign-off*; "Funnel" → *Candidates*;
   "Decision History" → *Candidate Ledger*; "Working set" → *Staged*. Full rationale +
   V2 backlog in `blotter_design.md`.

   Backend perf sweep (same day): new `GET /analytics/opportunity-funnel/current-snapshot`
   returns just `{current, config, family_ttl_sec}` with a 5s process-local TTL cache
   (`analytics.py:_cached_current_snapshot`). Cohort SQL consolidated from 7 counts →
   2 aggregates (`SUM(CASE WHEN status=…)` + `COUNT(DISTINCT CASE WHEN status IN…)`).
   Both funnel endpoints emit `Server-Timing` (cohort/build_current/snapshot/universe/total)
   so future regressions can be attributed by phase in Chrome DevTools Network. See
   `blotter_design.md#performance-backlog` — all 7 items shipped.

   Nav update: Decide row now reads `Blotter · Agent Activity`. `/proposals` route
   preserved as `<Navigate to="/blotter" replace />` for legacy bookmarks. `pages/
   Proposals.tsx` deleted; its `ProposalRationaleDetail` export moved to
   `components/ProposalRationaleDetail.tsx` (still consumed by AgentFeed's drawer).
   241 lines of orphaned CSS (`.entry-*`, `.human-approval-*`, `.funnel-*`,
   `.approval-list`, `.desk-*`, `.proposal-*`, `.dir-*`) purged from `index.css`. All
   related docs synced (webui_page_map / overview_redesign / api_reference /
   ui_design_system). tsc+build green; unit tests updated.
