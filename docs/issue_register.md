# Aletheia Trading OS Issue Register

Last updated: 2026-06-06  
> **ARCHIVED SNAPSHOT** — superseded by BACKEND_ISSUES.md §1-§3 (all fixed 2026-06-06) and §10 archive. Test baseline here (351 passed) is stale; current baseline is ~424. Preserved as audit provenance only.

This file is the long-lived issue register for trust hardening work. It is a
planning and audit artifact only. Do not treat any item here as permission to
expand scope or weaken the safety invariants in `AGENTS.md` and
`docs/trust_and_change_control.md`.

## Executive Summary

Current posture: the project is a functional local-first trading-control MVP
with a running gateway and active market/agent telemetry, but this workspace has
not yet produced a paper-trading causal chain. The strongest immediate risks are
trust and state-honesty issues, not missing feature breadth.

Evidence snapshot from this audit:

- `git status --short`: only untracked `docs/ChatExport_2026-05-28.zip` and
  `docs/ChatExport_2026-05-28/`.
- `uv run aletheia gateway status`: gateway running at `127.0.0.1:8787`,
  exchange `bitget (demo)`, live state `live_disabled`, permission `Research`,
  autostart enabled.
- `uv run pytest -q`: `351 passed`, `1 failed`. The failure is
  `tests/test_lifecycle.py::test_do_stop_reports_when_port_stuck`.
- SQLite core counts: `agent_runs=97`, `agent_tool_calls=484`,
  `audit_events=111`, `knowledge_items=4`, `market_snapshots=918`,
  `opportunities=95`, `portfolio_snapshots=160`,
  `trade_proposals=0`, `risk_decisions=0`, `orders=0`, `fills=0`,
  `positions=0`, `strategy_records=0`.

Recommended fix order:

1. Restore a green test suite and resolve the gateway stderr/logger mismatch.
2. Fix knowledge/news semantics before any more dashboard-driven note creation.
3. Make risk and dashboard statuses more conservative and evidence-backed.
4. Prove the paper-trading causal chain with real local records.
5. Only after stable paper data exists, consider any learning-memory readback.

## Issue Register

### ISSUE-001 - Full backend test suite is not green

- Severity: `P1 trust`
- Boundary: `tests`, `gateway`
- Status: `open`
- Symptom: The current full backend suite fails one test.
- Evidence:
  - Command: `uv run pytest -q`
  - Result: `351 passed`, `1 failed`.
  - Failure: `tests/test_lifecycle.py::test_do_stop_reports_when_port_stuck`
    asserts that `"still in use"` appears in captured stderr.
  - Actual behavior logs a warning from `aletheia.gateway.lifecycle` instead:
    "Gateway processes signalled, but port 8787 is still in use..."
  - Relevant code: `aletheia/gateway/lifecycle.py` uses `_echo_safe()` and logs
    in headless mode; tests also cover `_echo_safe` behavior.
- Impact: The changelog/README confidence story is weaker while the full suite is
  red. This can hide later regressions because developers may normalize a failing
  baseline.
- Suggested Fix: Decide whether CLI stop warnings must always be stderr-visible
  in the tested path or whether the test should assert the logger warning for
  headless mode. Keep the Windows no-console-flash invariant intact.
- Verification:
  - `uv run pytest tests/test_lifecycle.py -q`
  - `uv run pytest -q`

### ISSUE-002 - News-to-research-note route bypasses Knowledge manager and appears schema-incompatible

- Severity: `P1 trust`
- Boundary: `knowledge`, `server routes`, `frontend`
- Status: `open`
- Symptom: The dashboard can ask the backend to create a research note from a
  news item, but the route constructs a plain dict instead of a `KnowledgeItem`
  and uses `type` rather than `item_type`.
- Evidence:
  - `frontend/src/pages/NewsIntelligence.tsx` calls
    `api.createResearchNoteFromNews`.
  - `aletheia/server/routes/news.py` defines
    `create_research_note_from_news`.
  - The route builds `note = {..., "type": "research_note", "content": ...}`
    and then calls `repo.insert_knowledge_item(note)`.
  - `knowledge_items` schema requires `item_type`, `title`, `file_path`,
    `confidence`, `status`, `tags`, `created_at`, `updated_at`.
  - `repo.insert_knowledge_item` expects a `KnowledgeItem` model and calls
    `k.model_dump()`.
  - The existing semantic path is `knowledge.manager.create_research_note()`.
- Impact: The route likely fails at runtime, and even if adjusted casually it
  risks type laundering, missing Markdown files, invalid tags serialization, or
  broken KB display.
- Suggested Fix: Route all news-derived research creation through
  `create_research_note(title, content, metadata=...)`. Persist news linkage in a
  shape compatible with existing storage. Add a regression test for the API route.
- Verification:
  - `uv run pytest tests/test_knowledge.py -q`
  - Add and run a focused route test for
    `POST /api/news/{news_id}/create-research-note`.

### ISSUE-003 - Risk status defaults to `within_limits` without a full risk evaluation

- Severity: `P1 trust`
- Boundary: `risk`, `server routes`, `frontend`
- Status: `open`
- Symptom: `/api/risk/status` initializes status as `within_limits` even when no
  proposal has been evaluated and `risk_decisions=0`.
- Evidence:
  - `AGENTS.md` explicitly says not to hardcode risk state as "within limits".
  - `aletheia/server/routes/risk.py` sets `status = "within_limits"` before
    checking limited warning/blocking signals.
  - Current DB snapshot has `risk_decisions=0`.
  - `frontend/src/pages/Overview.tsx` renders the returned risk status.
  - `frontend/src/pages/RiskControls.tsx` renders the returned risk status.
- Impact: The dashboard can reassure the operator before the system has evidence
  from a real risk decision. This violates UI honesty expectations.
- Suggested Fix: Change the backend status vocabulary to conservative states such
  as `not_evaluated`, `no_active_violations`, `warning`, `blocked`, and update UI
  color mapping accordingly. Keep hard risk checks in `risk/engine.py` unchanged.
- Verification:
  - Add focused route tests for empty risk history, latest rejected decision, kill
    switch active, and exchange-live/live-disabled warning.
  - `uv run pytest tests/test_risk_routes.py -q`
  - `cd frontend && npm run build`

### ISSUE-004 - RiskControls UI expects `ok`, but backend returns `within_limits`

- Severity: `P2 correctness`
- Boundary: `frontend`, `risk`
- Status: `open`
- Symptom: The frontend green state is keyed on `riskStatus.status === 'ok'`,
  while the current backend success-ish value is `within_limits`.
- Evidence:
  - `frontend/src/pages/RiskControls.tsx` checks `riskStatus.status === 'ok'`.
  - `aletheia/server/routes/risk.py` returns `within_limits`.
- Impact: Even the current optimistic backend status renders as yellow, creating
  a confusing state: the backend overclaims and the UI does not classify it as
  intended.
- Suggested Fix: Resolve this together with ISSUE-003 by defining one canonical
  risk-status vocabulary and mapping it in shared frontend status utilities.
- Verification:
  - `uv run pytest tests/test_risk_routes.py -q`
  - `cd frontend && npm run build`
  - Browser-check Risk & Controls with empty history, warning, and blocked states.

### ISSUE-005 - Dashboard agent state overstates runtime activity

- Severity: `P2 correctness`
- Boundary: `frontend`, `runtime`
- Status: `open`
- Symptom: Dashboard labels agents as `Active` or `Running` based mainly on
  `paused=false`, without showing whether scheduler is disabled or whether
  autonomous paper trading is actually off.
- Evidence:
  - `frontend/src/pages/Overview.tsx` displays `Agents: Active` when
    `status.paused` is false.
  - `frontend/src/pages/RiskControls.tsx` displays `Agents: Running` when
    `status.paused` is false.
  - `data/config.yaml` currently has `scheduler_enabled: true`, `paused: false`,
    and `autonomous_paper_trading: false`.
  - `aletheia/agent_runtime/scheduler.py` gates paper trading on both
    `autonomous_paper_trading` and permission mode.
- Impact: An operator may believe the autonomous trading loop is active when only
  observation/learning jobs are running. This matters because the current DB has
  opportunities but no proposals/orders.
- Suggested Fix: Split dashboard runtime state into separate facts: gateway,
  scheduler, observation jobs, autonomous paper trading, permission mode, and
  live state. Avoid one green "Agents Active" label for all modes.
- Verification:
  - Add/update frontend tests if available, or use browser checks for Research,
    PaperAuto with paper trading off, and PaperAuto with paper trading on.
  - `cd frontend && npm run build`

### ISSUE-006 - No paper-trading causal chain exists in current runtime data

- Severity: `P1 trust`
- Boundary: `runtime`, `execution`, `risk`, `portfolio`
- Status: `open`
- Symptom: The system has produced market snapshots and opportunities, but has
  not produced any trade proposals, risk decisions, orders, fills, positions, or
  strategy records in the current local DB.
- Evidence:
  - SQLite counts from this audit:
    `opportunities=95`, `trade_proposals=0`, `risk_decisions=0`, `orders=0`,
    `fills=0`, `positions=0`, `strategy_records=0`.
  - `data/config.yaml` has `permission_mode: Research` and
    `autonomous_paper_trading: false`.
  - `docs/RUNBOOK.md` describes the closed paper loop as an operational path.
- Impact: Claims about long-run paper PnL, position closure, promotion gates, and
  daily-loss breaker cannot be validated from current local state. The system is
  observing, not yet proving the full trading loop.
- Suggested Fix: Treat "paper loop proven locally" as an explicit acceptance
  milestone. Turn on PaperAuto and autonomous paper trading only deliberately,
  then collect evidence for proposal -> risk -> order -> fill -> position ->
  close. Do not enable live as part of this.
- Verification:
  - `uv run aletheia gateway status`
  - SQLite count query for the causal tables.
  - Focused route/API or scheduler test proving one paper proposal executes via
    the Execution Gateway.

### ISSUE-007 - Experience loop is still mostly write-only

- Severity: `P2 correctness`
- Boundary: `agents`, `knowledge`, `strategy lifecycle`
- Status: `open`
- Symptom: Agents write research notes, dreaming logs, reviews, and opportunities,
  but trading decisions do not meaningfully read historical knowledge back into
  prompts or rules.
- Evidence:
  - `docs/north_star_references.md` already identifies this as the core gap.
  - `aletheia/agents/dreaming_agent.py` counts knowledge items with
    `list_knowledge_items(..., limit=1000)` but does not read their content.
  - `aletheia/agents/paper_trader.py` trades from recent opportunities and rules,
    not from prior reviews or risk lessons.
  - `aletheia/knowledge/manager.py` defines `create_strategy_memory`.
  - `docs/north_star_references.md` notes `create_strategy_memory` is not used by
    agents and there is no FTS/vector retrieval.
- Impact: The product is not yet a self-learning trading loop. It is closer to a
  logging and consolidation system with paper-trading hooks.
- Suggested Fix: Do not jump to vector search. First prove learning quality and
  then add a minimal, read-only memory retrieval layer for selected agents, with
  tests showing memories cannot alter hard risk limits or live state.
- Verification:
  - Tests proving retrieval is read-only and injected only as advisory context.
  - Tests proving restricted tools remain uncallable.
  - Manual audit of generated prompts for absence of secrets and hard-limit
    mutations.

### ISSUE-008 - News Intelligence conflicts with current scope controls

- Severity: `P2 correctness`
- Boundary: `frontend`, `server routes`, `docs/scope`
- Status: `open`
- Symptom: The app exposes a News Intelligence page and `/api/news` routes even
  though project instructions forbid adding news ingestion unless explicitly
  requested.
- Evidence:
  - `AGENTS.md` says not to add "news ingestion" without exact current-turn
    authorization.
  - `frontend/src/App.tsx` routes `/news` to `NewsIntelligence`.
  - `frontend/src/pages/NewsIntelligence.tsx` says "Add news manually or wait for
    agent ingestion."
  - `aletheia/server/routes/news.py` defines `/api/news` create/archive/link
    routes.
- Impact: Even if current ingestion is manual, the UI language advertises future
  agent ingestion and expands the product surface beyond the trust-hardening
  priority. This also creates the broken knowledge route in ISSUE-002.
- Suggested Fix: Either explicitly mark News Intelligence as manual-only and
  disabled/experimental, or remove/hide it until news ingestion is explicitly
  authorized. Fix ISSUE-002 before exposing note creation.
- Verification:
  - `cd frontend && npm run build`
  - Browser-check that no unsupported ingestion claim is visible.
  - Route tests for any retained manual news behavior.

### ISSUE-009 - Overview model/exchange status uses ambiguous `checking...`

- Severity: `P2 correctness`
- Boundary: `frontend`, `model_provider`, `exchange`
- Status: `open`
- Symptom: Overview shows `checking...` for unavailable model/exchange states
  rather than distinguishing not configured, unreachable, disabled, or unknown.
- Evidence:
  - `frontend/src/pages/Overview.tsx` displays model as
    `connected (${defaultProvider})` or `checking...`.
  - The same page displays exchange as `connected` or `checking...`.
  - Settings has more detailed state handling for providers and Bitget profiles.
- Impact: Overview can hide actionable setup failures behind a transient-sounding
  label. This weakens the dashboard as an operational control surface.
- Suggested Fix: Reuse the richer Settings status vocabulary on Overview:
  `not configured`, `not logged in`, `disabled`, `unreachable`, `connected`, and
  `checking` only during an actual pending request.
- Verification:
  - `cd frontend && npm run build`
  - Browser-check Overview with missing model, Codex not logged in, missing
    Bitget credentials, and failed Bitget test.

### ISSUE-010 - SetupBanner does not account for Codex CLI provider configuration

- Severity: `P3 cleanup`
- Boundary: `frontend`, `model_provider`
- Status: `open`
- Symptom: SetupBanner decides whether a model is configured by checking provider
  API-key presence, but the Codex CLI provider intentionally has no API key in
  Aletheia.
- Evidence:
  - `data/config.yaml` has `default_provider: codex_cli`.
  - `frontend/src/components/SetupBanner.tsx` computes `anyModelKey` from
    `p?.api_key?.configured`.
  - `frontend/src/pages/Settings.tsx` treats `codex_cli` separately and uses
    Codex login health.
- Impact: The onboarding nudge can ask the user to add "a model endpoint" even
  when the intended provider is a logged-in Codex subscription.
- Suggested Fix: Base setup readiness on model health/default provider status,
  not only API-key presence. Keep Bitget demo readiness separate.
- Verification:
  - `cd frontend && npm run build`
  - Browser-check SetupBanner with Codex available, Codex unavailable, and
    OpenAI-compatible provider configured.

### ISSUE-011 - Knowledge Base and Reports inspector type is semantically wrong

- Severity: `P2 correctness`
- Boundary: `frontend`, `knowledge`
- Status: `open`
- Symptom: Knowledge rows from multiple item types are sent to the inspector as
  `research_note`.
- Evidence:
  - `frontend/src/pages/KnowledgeBase.tsx` has tabs for Research Notes,
    Strategy Memory, Daily Reviews, and Dreaming Logs.
  - All rows call `setObject({ type: 'research_note', ... })`.
  - `frontend/src/pages/Reports.tsx` also sends daily review rows as
    `research_note`.
- Impact: The inspector can mislabel daily reviews, dreaming logs, and strategy
  memory. This conflicts with the project's knowledge taxonomy rules.
- Suggested Fix: Pass the actual `item_type` or tab-specific entity type into the
  inspector. Ensure UI copy does not launder one knowledge type into another.
- Verification:
  - `cd frontend && npm run build`
  - Browser-check each Knowledge Base tab and Reports tab in the inspector.

### ISSUE-012 - Activity heartbeat uses a green dot without staleness semantics

- Severity: `P3 cleanup`
- Boundary: `frontend`, `runtime health`
- Status: `open`
- Symptom: TopStatusBar shows last agent activity with a green dot whenever a
  run exists, regardless of age or health.
- Evidence:
  - `frontend/src/components/TopStatusBar.tsx` computes `lastActivity` from the
    latest run and renders `dot-green`.
  - `aletheia/agent_runtime/health.py` already computes health/staleness issues
    such as `market_data_stale`.
- Impact: A stale system can still display a green activity signal. The timestamp
  is present, but the visual affordance implies freshness.
- Suggested Fix: Color the heartbeat by age and/or backend health. Use muted or
  warning when stale, and reserve green for recent activity within a configured
  freshness window.
- Verification:
  - `cd frontend && npm run build`
  - Browser-check recent, stale, and no-activity states.

### ISSUE-013 - Proposal approval audit hardcodes `before: pending`

- Severity: `P2 correctness`
- Boundary: `audit`, `server routes`, `execution`
- Status: `open`
- Symptom: The proposal approval route writes an audit event with
  `before.status = pending` regardless of the actual stored status.
- Evidence:
  - `aletheia/server/routes/data.py` writes `proposal_approved` with
    `"before": {"status": "pending"}`.
  - The route then calls `execute_existing_proposal`.
  - `execution/gateway.py` preserves proposal IDs and writes execution/rejection
    audit events.
- Impact: In ordinary pending proposals this is fine, but if an already-approved
  non-terminal proposal is retried, the audit log can misstate the real prior
  state. Audit truthfulness is a core project invariant.
- Suggested Fix: Use the stored proposal status as the audit `before` value and
  add a test for approving an already-approved proposal without duplicating
  proposal rows.
- Verification:
  - `uv run pytest tests/test_workflow_routes.py -q`
  - Confirm no duplicate proposals and audit before/after values are truthful.

### ISSUE-014 - Version and test-count documentation are stale

- Severity: `P3 cleanup`
- Boundary: `docs`, `packaging`, `tests`
- Status: `open`
- Symptom: Version and test-count claims differ across files and current test
  output.
- Evidence:
  - `aletheia/__init__.py` has `__version__ = "0.5.0"`.
  - `pyproject.toml` has `version = "0.1.0"`.
  - `docs/RUNBOOK.md` shows `uv run aletheia version` returning v0.4.0.
  - `README.md` says `uv run pytest` is 272 tests.
  - Current audit run saw 352 total tests, with one failure.
- Impact: Operators and future agents cannot rely on documentation to understand
  the shipped version or test baseline.
- Suggested Fix: Normalize package version, runtime version, README, RUNBOOK, and
  CHANGELOG test counts after the suite is green. Avoid exact test counts in docs
  unless they are automatically maintained.
- Verification:
  - `uv run aletheia version`
  - `uv run pytest -q`
  - Manual doc grep for stale version/test-count strings.

### ISSUE-015 - Untracked ChatExport assets pollute the working tree and searches

- Severity: `P3 cleanup`
- Boundary: `docs`, `workspace hygiene`
- Status: `open`
- Symptom: A large Telegram export directory and zip are untracked in `docs/`.
- Evidence:
  - `git status --short` reports:
    `?? docs/ChatExport_2026-05-28.zip`
    and `?? docs/ChatExport_2026-05-28/`.
  - Repository-wide `rg` searches return many hits from this export, including
    market-watch text unrelated to the current source.
- Impact: The export increases noise for audits and can accidentally be committed.
  It may also contain private conversation/media material.
- Suggested Fix: Decide whether the export is source material. If yes, document
  it as a private reference and ignore/archive appropriately. If no, move it out
  of the repo or add a targeted ignore rule. Do not delete it without explicit
  user approval.
- Verification:
  - `git status --short`
  - `rg <known source term>` should not be dominated by exported chat artifacts.

## Frontend / Dashboard Honesty

Frontend issues to prioritize for the next repair pass:

- ISSUE-003 and ISSUE-004: risk status vocabulary and color mapping.
- ISSUE-005: agent activity state needs to distinguish scheduler, observation,
  and autonomous paper trading.
- ISSUE-008: News Intelligence should not imply unsupported agent ingestion.
- ISSUE-009: Overview should use actionable provider/exchange states.
- ISSUE-010: onboarding should understand Codex CLI readiness.
- ISSUE-011: Knowledge/Reports inspector should preserve item type.
- ISSUE-012: heartbeat should not use green for stale activity.

Frontend verification floor for fixes:

- `cd frontend && npm run build`
- Confirm updated `aletheia/dashboard_static/` when frontend source changes.
- Browser-check Overview, Risk & Controls, Settings, Knowledge Base, Reports,
  News Intelligence, and TopStatusBar in at least one empty/not-configured state
  and one configured/running state.

## Backend Safety & State

Backend and state issues to prioritize:

- ISSUE-001: restore the green suite.
- ISSUE-002: repair news-to-knowledge semantics before exposing note creation.
- ISSUE-003: make risk status conservative when there is no risk decision.
- ISSUE-006: prove a paper causal chain before treating the MVP as operationally
  complete.
- ISSUE-007: defer memory readback until stable paper data and learning quality
  are proven.
- ISSUE-013: tighten approval audit truthfulness.

Safety verification floor for fixes touching controlled modules:

- Add or update focused regression tests first.
- Run `uv run pytest tests/<focused_test_file>.py -q`.
- Run `uv run pytest -q` for cross-boundary changes.
- If frontend operational state changes, also run `cd frontend && npm run build`.

## Docs & Roadmap Drift

Documentation drift to clean after functional fixes:

- README and CHANGELOG should not claim a green 272-test baseline while current
  suite is 352 tests with one failure.
- RUNBOOK should not show v0.4.0 as current when runtime reports v0.5.0.
- `pyproject.toml` package version and runtime `__version__` should be reconciled
  or explicitly documented.
- North-star learning-loop work should remain future direction, not current
  implementation authorization.
- News-related docs/UI should be explicit that current behavior is manual-only if
  retained at all.

## Verification Snapshot

Commands run during this audit:

```text
git status --short
uv run aletheia gateway status
uv run pytest -q
sqlite3 data/app.db "<core count union query>"
rg-based source scans across docs, backend, frontend, and tests
```

Observed results:

```text
git status:
?? docs/ChatExport_2026-05-28.zip
?? docs/ChatExport_2026-05-28/

gateway status:
Gateway: running (PID 28872)
Paused: False
Dashboard: http://127.0.0.1:8787
Exchange: bitget (demo)
Live state: live_disabled
Permission: Research
Autostart: enabled

pytest:
1 failed, 351 passed
failed: tests/test_lifecycle.py::test_do_stop_reports_when_port_stuck

SQLite:
agent_runs|97
agent_tool_calls|484
audit_events|111
knowledge_items|4
market_snapshots|918
opportunities|95
portfolio_snapshots|160
trade_proposals|0
risk_decisions|0
orders|0
fills|0
positions|0
strategy_records|0
```
