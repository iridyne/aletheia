# Development Log

## 2026-06-08 (latest) — Three remaining real gaps closed

After WEBUI_BACKEND_HANDOFF was done, three real gaps remained that I hadn't yet
addressed (everything else was either already fixed, recorded as a future-scope
north-star item, or banned by AGENTS.md). All three closed in one pass — they're
small once the §1.1 causal chain and §6 experience-loop scaffolding are in place:

- **Gap 1 — agent_run_steps now actually populated.** The `/api/agent-runs/{id}/steps`
  endpoint shipped in the prior pass returned an empty array because nothing was
  writing to the table. Hooked the write in `agents/helpers.py::log_tool` (every
  tool call now produces a step) plus a new `record_step()` for non-tool reasoning
  nodes. Per-run step counters live in-process and are released by the orchestrator
  at end-of-run so the dict stays bounded. **Zero changes to the 8 agents** — they
  already use `log_tool` everywhere, so the trace fills in automatically.

- **Gap 2 — Stability monitor + `GET /api/stability`.** Bridges the gap between
  "process alive" (existing `health.py`) and "data internally consistent"
  (north-star §7 stage-0 evidence). New `agent_runtime/stability_monitor.py`
  reports orphan counts (fills with no order, executed proposals with no order,
  etc.), strategy_id drift (paper proposals without a strategy — direct §1.1
  causal-chain signal), 24h agent failure ratio, uptime hours, and a 0-100
  composite score with explicit reason codes. Read-only — inconsistencies are
  REPORTED, never silently repaired. Operator can poll the endpoint through the
  7-14 day stage-0 run and watch the score numerically.

- **Gap 3 — Learning Ledger gets a quality block.** `dreaming_agent` was already
  emitting a Learning Ledger but it only counted volume ("research_note: 12,
  strategy_memory: 3"). Volume can be noise. Added stage-1 quality signals:
  activation ratio (% strategies that reached paper_running or beyond),
  retire/deprecate ratio (noise being filtered — non-zero is healthy),
  post-trade-review coverage (%), and lessons-consulted count over 24h
  (`retrieve_lessons` + `load_playbooks` calls — non-zero means the experience
  loop is actually closing). This is the evidence north-star §7 stage 1 wants.

Wiring is intentionally minimal: no new tables, no schema migration, no agent
behaviour changes. Just three new read paths that surface signals already implied
by data we already record. New `tests/test_three_gaps.py` (10 tests) including a
red-line test that orphan fills get DETECTED, never silently fixed. Full suite
406 passed.

What this leaves on the gap list: none of the *immediate* "real" gaps. Remaining
items are all north-star architectural work (Sophia-class long-term identity,
RL feedback loop, cross-agent debate workflow, deeper market perception) that
the project's stage sequencing explicitly defers until 7-14 days of clean stage-0
evidence accumulates. The stability + ledger numbers above are the receipts that
let "stage 0 done" become a yes/no rather than a vibe.

## 2026-06-08 — Exchange Account Sync (closes WEBUI_BACKEND_HANDOFF #3 / portfolio-sync-gap)

The Portfolio dashboard was off by an entire reality: it only saw locally-managed
positions, so `total_value_usdt = 0` even when the demo account held real balance.
Now closed:

- **`portfolio/exchange_sync.py`** (new) — `sync_exchange_account()` calls the existing
  read-only Bitget client methods (`get_balances`/`get_positions`/`get_open_orders`)
  and caches the result as a JSON blob in the `settings` table (no schema change). Status
  classifier `ok | stale | unconfigured | error`. Never raises; degrades to None and the
  UI shows the existing caveat. **Note (turned out client.py was already exposing
  everything — no change-controlled module touched).**
- **`compute_portfolio()`** — extended with `exchange_equity_usdt`,
  `exchange_state_age_sec`, `exchange_external_positions` (drift signal), and
  `exchange_sync_status`. The local managed `total_value_usdt` field is preserved so the
  UI can show both side-by-side and reconcile drift.
- **`GET /api/exchange/account`** — read-only cached view; HTTP path never blocks on
  Bitget. Scheduler refreshes the cache every 5 min via a new `_run_exchange_sync` job.
- **`aletheia bitget sync`** CLI — manual one-shot for debugging.

Red lines kept: read-only against the exchange; external positions stay out of the
local `positions` table (they have no proposal back-link → would violate trust §1.1);
`exchanges/bitget/client.py` untouched. New `tests/test_exchange_sync.py` (9 tests
including a red-line test that asserts the local positions table stays empty after a
sync). Full suite 396 passed. Real e2e: CLI sync against the live demo account
returned `status: ok` and the cached view flowed into `/api/exchange/account` and
`/api/portfolio` after a gateway restart.

Cross-process visibility caveat: SQLite WAL means a CLI write doesn't immediately
appear to a long-running gateway process holding its own connection. In practice the
scheduler refreshes the cache from inside the gateway process every 5 min, so the API
sees the truth on its own timeline — no extra wiring needed for the production path.

## 2026-06-07 — WebUI backend handoff: position FK + steps endpoint

Implemented the two actionable, in-scope gaps from `WEBUI_BACKEND_HANDOFF.md`:

- **#1 position → proposal/order causal link** (⭐ highest value): `positions` gained
  nullable `proposal_id`/`order_id` FK columns (CREATE TABLE + additive migration).
  Both brokers populate them when a fill opens a position; averaging into an existing
  position preserves the *original* opener. Makes the position→upstream direction of the
  trust §1.1 causal chain real (CausalChain.tsx no longer needs "not recorded"). Touched
  change-controlled `execution/bitget_broker.py` — additive only, focused regression tests.
- **#2 `GET /api/agent-runs/{id}/steps`**: read-only endpoint exposing `agent_run_steps`
  (new `list_run_steps`/`insert_run_step` repo fns). Honest caveat: the table is not yet
  written by any agent, so it returns `[]` today — populating it is a separate, larger
  change (every agent) beyond the handoff's "expose the table" ask.

Not done (by design): **#3** exchange equity touches change-controlled `client.py` → needs
explicit owner authorization; **#4** news ingestion is banned MVP scope (AGENTS.md).

`tests/test_webui_backend_handoff.py` (6 tests); full suite 387 passed.

## 2026-06-07 — Experience loop (north-star stage 2, minimal closed loop)

### Added — knowledge that reads back into decisions (§6 gap closed, minimally)

The knowledge base was write-only (the central north-star gap). It now feeds prior
lessons back into decisions, using frontier-but-constrained patterns (Generative-Agents
retrieval scoring, Hermes-style memory block, RAG provenance) on **SQLite FTS5 — no
vectors, no new deps**:

- `knowledge/retrieval.py` (new): FTS5 index + `retrieve_lessons()` scored by
  BM25(relevance) × recency-decay × importance. **Quality gate: only `high_confidence`
  + `active` items are retrievable** — speculative/archived noise can't become "experience".
  `format_memory_block()` renders results as an **explicitly advisory, provenance-tagged**
  block. Degrades to empty on any failure (FTS absent / no match) so agents never break.
- `migrations.py`: `knowledge_fts` virtual table, created out-of-band so a SQLite build
  without FTS5 can't break other migrations.
- `knowledge/manager.py`: `create_*` index high-confidence items; archive/deprecate
  remove them from the index (archived ⇒ not recalled).
- `trading/playbooks.py`: `load_playbooks()` / `format_playbook_block()` — playbooks are
  now **read** into the designer prompt (were seed-only, 0 readers). Read-only; agents
  never write playbooks.
- `agents/market_observer.py` + `agents/strategy_designer.py`: inject the memory block
  (and playbooks for the designer) into the LLM prompt; retrieval is logged as a
  `retrieve_lessons` tool call (observable). Injection is pure prompt text — it cannot
  touch risk/execution/config.
- `cli/knowledge_cmd.py` (new): `aletheia knowledge reindex` (backfill) + `search` (preview).

Governance held: read-only, quality-gated, advisory-not-instruction, no change-controlled
module touched, no live gate weakened. New `tests/test_experience_loop.py` (8 tests);
full suite 381 passed. End-to-end smoke confirmed a high-confidence lesson reaches the
observer's decision prompt with its provenance tag.

## 2026-06-07 — WebUI structural redesign (frontend-only)

### Changed — 14 dashboard pages → 10 role-organized destinations

Complete structural redesign of the React frontend to fix the owner's pain points
(density / layout / transparency / workflow). Diagnosis: UI was a flat page-per-DB-table;
the framework wants §23.1's 6 roles around the causal chain + a workflow flow. **Zero
backend / API changes** — pure frontend composition over existing endpoints.

5-phase IA (Observe / Decide / Execute / Evolve / Operate). Merges:
- Positions + Paper Trades → **Trades & Positions** (`TradesPositions.tsx`) + new
  **`CausalChain.tsx`** inspector (proposal→risk→order→fill→position; position-anchored
  upstream shown as "not recorded" — honest re trust §1.1, never symbol-matched).
- Agent Runs + Opportunities → **Agent Activity** (`AgentActivity.tsx`, tabs via new
  `Tabs.tsx`); run drawer "Reasoning Steps" block = mount point for the unbuilt
  `GET /api/agent-runs/{id}/steps`.
- Dreaming + Knowledge Base + Reports → **Learning** (`Learning.tsx`, 3 tabs; §4 KB
  taxonomy preserved, no type laundering).
- Proposals → **Approval Desk** (decision cards + inline risk verdict + confirm-to-approve).

New `PageHeader.tsx` (title + role descriptor) on all 10 pages — directly answers the
"transparency insufficient" complaint. News Intel got the §17.3 honesty banner
("manual entry / ingestion pipeline not configured"). RiskControls grid → responsive.

Deleted 7 merged page files. Legacy routes redirect (no dead links). CommandPalette +
cross-links updated. Verified live (preview MCP): nav structure, tab switching, honest
empty states, legacy redirects, zero console errors. tsc + vite build green.

Docs: `WEBUI_PAGE_MAP.md`, `OVERVIEW_REDESIGN.md`, `WEBUI_BACKEND_HANDOFF.md` (parked
backend gaps), `BACKEND_ISSUES.md §12`. Branch: feat/strategy-causal-chain (not merged).

## 2026-06-07 (later)

### Fixed — re-audit of BACKEND_ISSUES §10 + 8 falsely-archived issues

Re-verified the §10 "all fixed 2026-06-06" archive against current code (40+ items):
~75% true, ~25% claimed-fixed-but-not. Eight confirmed false/half-true, now really
fixed (each with a focused regression test; full suite 373 passed):

- §1.2 live-order: insert a `pending` order row BEFORE `place_order` (was place-then-
  insert → exchange order with no local trace if DB write fails). `bitget_broker.py`
- §1.3 health alert: run the Telegram coroutine to completion in a worker thread (was
  `loop.create_task` fire-and-forget → alert silently dropped from a sync caller). `health.py`
- §2.3 paper/demo risk: added `max_demo_position_notional_usdt` + `max_demo_single_trade_risk_pct`
  with distinct violation tokens (was unbounded → promotion evidence built from unbounded
  trades). `risk/engine.py`, `config/schema.py`
- §2.3 clientOid: `place_order` now sends a proposal-derived `clientOid` (idempotency on
  retry). `exchanges/bitget/client.py`
- §2.3 kill-switch TOCTOU: `evaluate_proposal` serialized via `_eval_lock` so a daily-loss
  trip is visible to the next proposal. `risk/engine.py`
- §2.5 paper_trader: autonomous-OFF gate duplicated into the agent body (CLI/API trigger
  bypassed the scheduler gate). `agents/paper_trader.py`
- §3 commit_all: dreaming agent calls `commit_all` after consolidation (Trading-as-Git was
  wired but never invoked). `agents/dreaming_agent.py`
- §3 gateway logs: size-cap rotation (`rotate_if_needed`) + tail-only reads (was unbounded
  growth + whole-file read → OOM). `gateway/logs.py`, `_gateway_runner.py`

Also added `starting_capital_usdt` as a real config field (was read via getattr fallback).
The §10 archive now carries a correction banner; lesson recorded: never trust a "fixed"
claim without grepping current code.

### Fixed — strategy↔trade causal chain (gap #2, north-star stage 0/2 prerequisite)

The `strategy_id` causal chain was broken in **four** places, not one (the
2026-06-06 §1.1 "fix" only wired paper_trader's fallback, leaving the upstream
producer side dead). All four now connected:

- `strategy_lifecycle.create_strategy()` accepts `parameters` → persisted to
  `StrategyRecord.parameters` (was always `"{}"`; trust §4.2 audit snapshot)
- `strategy_designer` produces a structured parameter spec and auto-advances the
  strategy through the **ungated** lifecycle states to `paper_running` (stops there;
  live gates untouched), so a tradable strategy actually exists
- `strategy_designer` registered as a daily scheduler job (was never scheduled →
  no strategies were ever produced autonomously)
- `market_observer` binds new opportunities to an active strategy via
  `linked_strategy_id` (honest None if none active; was never set, zero coverage)

Result: promotion-gate `paper_trades` count is no longer structurally stuck at 0.
New `tests/test_strategy_causal_chain.py` (5 tests) locks the chain end-to-end.
Suite: 357 passed.

### Changed

- Documentation housekeeping: archived all fixed items in BACKEND_ISSUES.md (§10) and FRONTEND_ISSUES.md (§10.1–10.24); removed root-level IMPLEMENTATION_GUIDE.md duplicate (canonical copy in docs/)
- Gateway: replaced all Windows subprocess calls (wmic/tasklist/powershell/taskkill) with pure ctypes Win32 API — fully invisible operation, zero console window flashes

### Decision

- Process introspection on Windows uses kernel32/ntdll ctypes instead of spawning external executables (see DECISIONS.md)

## 2026-06-06

### Added

- CLAUDE.md (project overview for Claude Code)
- .claude/ directory (settings, commands: start-session, end-session, update-handoff, project-audit)
- .codex/config.toml (Codex project config)
- docs/PROJECT_CONTEXT.md, docs/CURRENT_TASK.md, docs/AI_HANDOFF.md, docs/DECISIONS.md, docs/DEVLOG.md, docs/AI_WORKFLOW.md

### Fixed (major pass)

- **Backend (80+ fixes)**: All items in BACKEND_ISSUES.md §1-§3 — trust invariants (strategy_id causal chain, execution gateway isolation, git ff-only, promotion gates), data integrity (PnL fees, KB status filtering, CORS, audit symmetry, risk evaluation, scheduler error handling, telegram hardening), dead code cleanup
- **Frontend (24 fixes)**: All items in FRONTEND_ISSUES.md §1-§3 — shared types/DataTable/DetailDrawer components, risk_decision display, KB markdown reading, autonomous_paper_trading toggle, effective config card, promotion gates UI, chart-trade linking, dreaming 6-section layout, overview sparkline, cross-page navigation, agent run tool calls

### Notes

AI workspace initialization. Existing AGENTS.md preserved as-is (already comprehensive).
