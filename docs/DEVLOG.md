# Development Log

## 2026-06-07 (latest) — Experience loop (north-star stage 2, minimal closed loop)

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
