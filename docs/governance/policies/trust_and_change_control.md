# Aletheia Trust and Change Control

This document defines the implementation control layer for Aletheia Trading OS.
It complements `docs/governance/policies/implementation_guide.md`.

The implementation guide defines what the system should become. This document
defines what must remain true while the system changes.

For agentic workers: if this document and a task request conflict, stop and ask
before editing code. Do not resolve safety or trading-state conflicts by guessing.

---

## 1. Trust Invariants

### 1.1 Causal Record Chain

The trading record chain is:

```text
trade_proposals.id
  -> risk_decisions.proposal_id
  -> orders.proposal_id + orders.risk_decision_id
  -> fills.order_id
  -> positions derived from executed orders/fills
```

Rules:

- A `trade_proposals` row is created exactly once for a proposed trade.
- Approving, rejecting, risk-checking, or executing an existing proposal must not
  create a second proposal row.
- Every `risk_decisions.proposal_id` must reference the original proposal.
- Every `orders.proposal_id` must reference the original proposal.
- Every `orders.risk_decision_id` must reference the risk decision that approved
  or rejected execution for that proposal.
- Every `fills.order_id` must reference the order that produced the fill.
- A `positions` row is a portfolio-state projection from orders/fills, but the
  schema NOW carries direct causal back-links (2026-07-07 update, #30):
  `positions.proposal_id`, `positions.order_id`, and `positions.opportunity_id`
  are all set at open time by every broker (paper + bitget + okx + binance),
  and by `adopt_position()` for reconciler-adopted rows (with a synthetic-but-
  honest proposal/order chain marked `source='adopted'`). Do not treat the
  fields as advisory — the risk engine's sector-concentration check (#16) and
  Serenity's post-mortem attribution both rely on them being non-null on live
  positions. Historical rows may still have NULL links (pre-schema-migration).
- The `trade_proposals` table additionally carries a `commit_hash` / `parent_hash`
  hash chain (TaG P0-1, 2026-06-29). This is a **tamper-evidence layer on top of
  the ID-foreign-key causal chain** — not a replacement. `commit_hash` is the
  16-char SHA-256 prefix over the proposal's immutable fields (model_dump excludes
  `status` and `commit_hash` itself); `parent_hash` links to the preceding row's
  `commit_hash`, forming a global single chain. Do not modify
  `_compute_proposal_hash()` in `repositories.py`, the `_ADDITIVE_COLUMNS` entries
  for `commit_hash` / `parent_hash`, or the hash-injection branch of
  `insert_trade_proposal()` without simultaneously updating
  `docs/design/contracts/audit_chain_contract.md §6.4` and the four SDD claims
  DM-S08 / DM-S09 / DM-B06 / DM-C02. Downstream `position_adopted` audit events
  copy both fields into `after_state` (PL-ADOPT-09); changes to that propagation
  must update `audit_chain_contract.md §6.3` table row as well.

### 1.2 Proposal State Transitions

Allowed proposal status flow:

```text
pending -> approved -> executing
pending ----------------> executing
pending / approved -----> rejected
approved ---------------> error
executing -> submitted -> partially_filled -> executed
executing -------------------------------> executed
executing / submitted / partially_filled -> error
executing / submitted / partially_filled -> submission_unknown
executing / submitted / partially_filled -> materialization_missing
submission_unknown / materialization_missing -> submitted / partially_filled / executed
```

Rules:

- `pending` means no human approval/rejection has been applied.
- `approved` means a human-controlled dashboard/API/CLI path approved paper/demo
  execution or a future explicitly allowed workflow approved execution.
- `executing` means a durable execution owner/lease exists; retries must recover
  that claim instead of creating a second submission.
- `submitted` / `partially_filled` preserve confirmed exchange progress while
  materialization or the remaining quantity is unresolved.
- `executed` means the required Order/Fill/Position materialization chain is
  durable, not merely that an order request was sent.
- `rejected` means a human action or risk check rejected the proposal.
- `error` means execution is confirmed not submitted or failed without an
  accepted venue handoff.
- `submission_unknown` means venue acceptance cannot be disproved; automatic
  resubmission is forbidden until reconciliation.
- `materialization_missing` means an Order or venue fill activity exists but the
  required local Fill/Position chain is incomplete; automatic resubmission is
  forbidden. The detailed recovery contract is authoritative in
  `docs/design/contracts/execution_contract.md`.
- `position_absence_unverified` is limited to `Position.pending_close_status`:
  the venue rejected a close because it reported no matching exposure, while the
  local Position is still open. It retains the original close claim, forbids
  resubmission, and requires exact reconciliation; it is not a Proposal/Order
  success state and is never fill or outcome evidence.
- Do not use status changes as a substitute for missing records. If execution
  occurs, an order must exist. If risk evaluation occurs, a risk decision must
  exist.

### 1.3 Failure Semantics

Failures must leave inspectable state.

- Risk rejection: keep the original proposal row, set proposal status to
  `rejected`, insert a `risk_decisions` row with `approved=false`, and write an
  audit event.
- Human rejection: keep the original proposal row, set proposal status to
  `rejected`, and write an audit event.
- Paper execution failure before order creation: keep the proposal row, set
  status to `error`, and write an audit event.
- Paper execution failure after order creation: keep the order row, record the
  best known order status, and write an audit event. Do not delete partially
  created rows to hide the failure.
- Live execution failure: keep the proposal row, set status to `error`, do not
  infer fills unless the exchange confirms them, and write an audit event.
- Reconciliation must never place or cancel orders. It may only compare exchange
  state to local state and write convergence/audit records.

### 1.4 Audit Requirements

Every important state transition must write to the selected runtime home's
`logs/audit.jsonl` (normally `~/.aletheia/logs/audit.jsonl`).

Minimum audit event fields:

```json
{
  "timestamp": "...",
  "actor": "human|agent|system|telegram|cli",
  "source": "dashboard|api|cli|scheduler|agent_runtime|telegram|execution_gateway",
  "event_type": "...",
  "entity_type": "...",
  "entity_id": "...",
  "message": "..."
}
```

When available, include:

```json
{
  "before": {"status": "..."},
  "after": {"status": "..."}
}
```

Rules:

- `entity_id` must point to the actual row being changed or created.
- Do not audit a duplicated replacement row as if it were the original entity.
- Audit messages must not contain API keys, secrets, request signatures, full
  authorization headers, or raw model-provider credentials.
- Audit events must be truthful even when the UI displays a simplified state.

---

## 2. Agent Change Protocol

### 2.1 Before Editing

Before editing code, the agent must state:

- The boundary being touched, such as `execution`, `risk`, `knowledge`,
  `dashboard`, `telegram`, `secrets`, `model_provider`, or `storage`.
- The invariant being protected or changed.
- The non-goals for the change.
- The focused verification command that will prove the change.

Example:

```text
Boundary: execution.
Invariant: approving an existing proposal must preserve the original proposal ID.
Non-goals: no live-order behavior changes, no schema migration, no new broker.
Verification: uv run pytest tests/test_workflow_routes.py -q
```

### 2.2 When Not To Edit

Do not edit code when:

- The user asked for analysis, review, explanation, or planning only.
- The requested behavior conflicts with a trust invariant.
- The implementation guide is ambiguous and the ambiguity affects trading,
  secrets, live mode, risk, or audit behavior.
- The change would require a new market, exchange, database service, deployment
  mode, or autonomous live behavior not explicitly requested in the current task.
- The current code already satisfies the request and the remaining work is
  documentation or explanation.

### 2.3 Test-First Requirements

Write or update a focused regression test before implementation for changes that
touch:

- Risk approval/rejection.
- Execution Gateway behavior.
- Proposal/order/fill/position state.
- Live unlock or live execution.
- Secret storage, redaction, or API responses.
- Agent permission enforcement.
- Strategy promotion gates.
- Knowledge item type semantics.
- Dashboard safety status display.
- Telegram authorization.

For trivial copy or documentation edits, tests are not required.

### 2.4 Scope Discipline

Make the smallest change that satisfies the requested behavior.

Do not:

- Bundle unrelated cleanup with a safety fix.
- Convert a bug fix into a subsystem redesign.
- Add abstractions without reducing real complexity.
- Add future roadmap capabilities while fixing current MVP behavior.
- Touch generated frontend assets unless frontend source changed or the task is
  explicitly about build output.

---

## 3. UI Truthfulness Rules

The dashboard is an operational control surface. It must not reassure the user
with invented positive states.

### 3.1 Source Rule

Every displayed operational status must come from a backend value.

Examples:

- Risk state must come from a risk/status API or computed backend summary.
- Live readiness must come from config live state, exchange mode, permission
  mode, and risk state.
- Knowledge tabs must query real `knowledge_items.item_type` values.
- Strategy lifecycle displays must reflect stored strategy records and gate
  results.

### 3.2 Unknown State Vocabulary

Use these labels when data is absent:

- `not configured`: required user configuration is missing.
- `not available`: backend cannot currently compute or fetch the value.
- `no data`: the system has no records for the requested view.
- `blocked`: a safety gate prevents the action.
- `warning`: a non-fatal condition requires attention.

Do not use positive labels such as `within limits`, `ready`, `healthy`, or
`connected` unless the backend explicitly supports that state.

### 3.3 Forbidden UI Patterns

Do not:

- Hardcode `within limits`, `healthy`, `ready`, or `connected`.
- Display a tab for a data type unless the backend can return that type.
- Display live-trading affordances without showing live state and unlock status.
- Hide risk violations behind a generic success badge.
- Convert backend errors into empty success states.

### 3.4 Dashboard Action Rules

Actions in the dashboard must map to backend routes with audited behavior.

- Approving a proposal must call a route that preserves the original proposal ID.
- Rejecting a proposal must write a real proposal rejection state.
- Changing permission mode must write an audit event.
- Unlocking live mode must require explicit confirmation and write an audit event.
- Kill switch changes must write an audit event and affect subsequent risk
  decisions.

---

## 4. Knowledge Item Taxonomy

Knowledge items are persistent project memory. Their type must match their origin
and intended use.

### 4.1 Allowed Item Types

The allowed `knowledge_items.item_type` values and their authoritative sources
are:

| item_type | Primary source | Purpose |
|---|---|---|
| `research_note` | Research Agent, Market Observer, human import | Evidence, hypotheses, market observations, counterarguments |
| `strategy_memory` | Strategy Designer, strategy lifecycle updates | Strategy descriptions, parameters, lifecycle context, rationale |
| `dreaming_log` | Dreaming Agent | System-level consolidation, pattern extraction, suggested focus |
| `daily_review` | Dreaming Agent or scheduled daily review | Daily operational summary, trading review, system health notes |
| `post_trade_review` | Post-trade Reviewer or human review | Execution quality, what worked, what failed, improvement notes |
| `trade_case_study` | Trade case study builder (closed positions) | Structured per-trade analysis for learning and strategy review |
| `trading_guard` | Guard promotion flow (human-confirmed or seeded) | Active behavioral rules enforced at decision time |
| `guard_candidate` | Dreaming Agent or operator review | Provisional guard pattern pending human confirmation |
| `system_action` | Self-heal layer (`commands._emit_self_heal_action`) | Audit trail for automated system-corrective actions |
| `operator_assessment` | Operator loop (`operator_loop.py`) | Periodic operator review summaries of agent decisions and execution |
| `trading_memo` | Insight synthesizer (`analytics/insight_synthesizer.py`) | Rolling tactical memo synthesized from recent trade signals |
| `reasoning_feedback` | Reasoning feedback tracker (`analytics/reasoning_feedback.py`) | Calibration records comparing predicted vs actual outcomes |
| `calibration_report` | Confidence calibration engine (`analytics/confidence_calibration.py`) | Periodic confidence scoring accuracy reports |

Adding a new item type requires 5 synchronised updates:

1. `aletheia/knowledge/manager.py` — add a `create_<type>()` function.
2. `aletheia/storage/` — confirm the type is accepted by `insert_knowledge_item`
   (no enum constraint; the column is free-text, so validation is by convention).
3. API route (if dashboard-queryable) — add or extend a `/knowledge/...` route in
   `aletheia/server/routes/data.py`.
4. `tests/specs/spec_data_model.py` — declare a `SpecClaim` for the new type.
5. This document — add a row to the table above.

### 4.2 Type Definitions

`research_note`

- Source: Research Agent, Market Observer research output, human-imported
  research.
- Purpose: evidence, hypotheses, market observations, counterarguments.
- Storage directory: `data/knowledge/research_notes/`.
- Dashboard: Knowledge Base / Research Notes.
- Must not contain: API keys, exchange credentials, prompt-hidden secrets.

`strategy_memory`

- Source: Strategy Designer, strategy lifecycle updates, strategy review records.
- Purpose: strategy descriptions, parameters, lifecycle context, rationale.
- Storage directory: `data/knowledge/strategies/`.
- Dashboard: Knowledge Base / Strategy Memory, Strategy Lifecycle.

`dreaming_log`

- Source: Dreaming Agent.
- Purpose: system-level consolidation, pattern extraction, lessons, suggested
  focus.
- Storage directory: `data/knowledge/dreaming_logs/`.
- Dashboard: Dreaming / Learning, Knowledge Base / Dreaming Logs.

`daily_review`

- Source: Dreaming Agent or scheduled daily review process.
- Purpose: daily operational summary, trading review, system health notes.
- Storage directory: `data/knowledge/daily_reviews/`.
- Dashboard: Reports / Daily Reviews, Knowledge Base / Daily Reviews.

`post_trade_review`

- Source: Post-trade Reviewer or human post-trade review.
- Purpose: execution quality, what worked, what failed, improvement notes.
- Storage directory: `data/knowledge/post_trade_reviews/`.
- Dashboard: Reports / Post-Trade Reviews when present; may also inform Strategy
  Lifecycle gates.

`trade_case_study`

- Source: Trade case study builder, triggered on closed positions.
- Purpose: structured per-trade narrative for retrospective learning and strategy
  review gates.
- Storage directory: `data/knowledge/trade_case_studies/`.
- Dashboard: Knowledge Base / Trade Case Studies.

`trading_guard`

- Source: Guard promotion flow; requires human confirmation or explicit seeding.
- Purpose: active behavioral constraints retrieved at AI decision time.
- Storage directory: `data/knowledge/trading_guards/`.
- Dashboard: Guards panel.

`guard_candidate`

- Source: Dreaming Agent or operator review.
- Purpose: provisional guard pattern; `medium_confidence`; not retrieved by the
  active recall path until promoted to `trading_guard`.
- Storage directory: `data/knowledge/guard_candidates/`.
- Dashboard: Guards panel (pending section).

`system_action`

- Source: Self-heal layer (`aletheia/agent_shell/commands.py:_emit_self_heal_action`).
- Purpose: audit trail for automated corrective actions taken without human
  initiation.
- Storage: inline content only (no file path).
- Must not contain: order placement intent or risk bypass signals.

`operator_assessment`

- Source: Operator loop (`aletheia/agent_shell/operator_loop.py`).
- Purpose: periodic structured summary of operator-loop decisions and execution
  outcomes; used for drift detection and performance review.

`trading_memo`

- Source: Insight synthesizer (`aletheia/analytics/insight_synthesizer.py`).
- Purpose: rolling tactical context memo derived from recent signals; injected
  into AI decision context.
- Storage: inline content; one active memo at a time (older memos remain as
  history).

`reasoning_feedback`

- Source: Reasoning feedback tracker (`aletheia/analytics/reasoning_feedback.py`).
- Purpose: comparison of prediction vs actual outcome for calibration; drives
  confidence accuracy reporting.

`calibration_report`

- Source: Confidence calibration engine (`aletheia/analytics/confidence_calibration.py`).
- Purpose: periodic accuracy report on confidence scores; one active report
  replaced on each calibration run.

### 4.3 No Type Laundering

Do not store one type as another to make a page populate.

Examples:

- A post-trade review must not be stored as `research_note`.
- A daily review must not be queried from `/knowledge/research-notes`.
- A dreaming log must not be treated as a strategy memory item.
- A strategy note must not be written into generic research if it is the
  authoritative strategy record.

### 4.4 Lifecycle Rules

- Hard deletion is forbidden.
- Use `status="archived"` for items that should disappear from default views.
- Use `status="deprecated"` and `confidence="deprecated"` for items that remain
  visible as obsolete knowledge.
- Contradiction detection may be a stub in MVP, but the UI must not claim
  contradictions were analyzed unless the backend really computed them.

---

## 5. Promotion Gate Computation Spec

Strategy promotion gates must be computed from stored records, not from strategy
state labels alone.

### 5.1 Gate Source Tables

Use these data sources:

- `strategy_records`: current strategy state and timestamps.
- `trade_proposals`: strategy-linked proposals via `strategy_id`.
- `risk_decisions`: risk decisions linked through proposals.
- `orders`: paper/demo/live order records linked through proposals.
- `fills`: fills linked through orders.
- `positions`: realized PnL and closed position history via `strategy_id`.
- `knowledge_items`: post-trade review existence via
  `item_type="post_trade_review"`.

If the current schema cannot compute a metric honestly, fail the gate with an
explicit `insufficient_*` reason. Do not pass by assumption.

### 5.2 Paper Runtime Days

Default gate:

```text
min_paper_runtime_days = 14
```

Computation:

- Consider only orders linked to the strategy through
  `orders.proposal_id -> trade_proposals.id -> strategy_id`.
- Consider only paper/demo execution modes: `paper_internal` and `bitget_demo`.
- Runtime starts at the earliest strategy-linked paper/demo order `created_at`.
- Runtime ends at current UTC time.
- Pass if elapsed full days is greater than or equal to 14.
- Fail with `insufficient_paper_runtime_days:<actual><14` if too short.
- Fail with `no_strategy_paper_orders` if no order exists.

### 5.3 Paper Trade Count

Default gate:

```text
min_paper_trades = 30
```

Computation:

- Count strategy-linked paper/demo filled orders, or fills if fill data is the
  more precise available source.
- Prefer fills when present.
- Pass if count is greater than or equal to 30.
- Fail with `insufficient_paper_trades:<actual><30`.

### 5.4 Positive Expectancy

Default gate:

```text
positive_expectancy_required = true
```

Computation:

- Use closed `positions` with matching `strategy_id`.
- Sum `positions.realized_pnl`.
- Pass if total realized PnL is greater than 0.
- Fail with `non_positive_expectancy:<pnl>` if total realized PnL is less than
  or equal to 0.
- Fail with `insufficient_closed_positions_for_pnl_checks` if no closed
  strategy-linked positions exist.

### 5.5 Max Drawdown

Default gate:

```text
max_drawdown_pct_allowed = 5.0
```

MVP computation:

- Use closed strategy-linked positions ordered by `closed_at`.
- Build an equity curve from cumulative `realized_pnl`.
- Resolve the Demo risk policy capital base through its configured source.
  The shipped and migrated default follows the matching paper/demo account
  equity; `custom` uses the explicitly stored `capital.base_usdt`.
- If account-equity mode has no fresh, positive authoritative snapshot, treat
  the capital base as unavailable and fail the dependent gate closed. Never
  silently fall back to the stored custom amount.
- Compute drawdown as peak-to-trough cumulative PnL decline divided by capital
  base.
- Pass if drawdown percentage is less than or equal to 5.0.
- Fail with `max_drawdown_exceeded:<actual>>5.0`.
- Fail with `insufficient_closed_positions_for_pnl_checks` if closed positions
  are absent.

The resolved base and source must remain auditable and covered by tests.

### 5.6 Max Consecutive Losses

Default gate:

```text
max_consecutive_losses_allowed = 5
```

Computation:

- Use closed strategy-linked positions ordered by `closed_at`.
- A loss is `realized_pnl < 0`.
- Count the longest run of consecutive losses.
- Pass if the longest run is less than or equal to 5.
- Fail with `max_consecutive_losses_exceeded:<actual>>5`.
- Fail with `insufficient_closed_positions_for_pnl_checks` if closed positions
  are absent.

### 5.7 Critical Risk Violations

Default gate:

```text
critical_risk_violations_allowed = 0
```

Computation:

- Read risk decisions linked to the strategy through proposals.
- Parse `risk_decisions.violations`.
- Any rejected live execution decision is critical.
- Any violation containing these tokens is critical:
  `live_disabled`, `kill_switch_active`, `account_mode_mismatch`,
  `exceeds_max_position_notional`, `exceeds_single_trade_risk`,
  `max_daily_loss_exceeded`, `stale_market_data`, `strategy_not_live_eligible`.
- Pass if critical violation count is 0.
- Fail with `critical_risk_violations:<actual>>0`.

### 5.8 Post-Trade Reviews

Default gate:

```text
post_trade_reviews_required = true
```

MVP computation:

- Require at least one active `knowledge_items` row with
  `item_type="post_trade_review"`.
- If the review is strategy-specific in metadata/tags, prefer matching the
  strategy ID.
- Until strategy-specific metadata is standardized, a global active
  `post_trade_review` satisfies the MVP gate.
- Fail with `post_trade_review_required` if no active review exists.

### 5.9 Live Candidate Promotion

Promotion from `paper_passed` to `live_candidate` passes only if:

- Current state is exactly `paper_passed`.
- All gates in this section pass.
- An audit event is written for the promotion.

Do not allow `paper_passed -> live_candidate` based only on state name.

### 5.10 Live Limited Promotion

Promotion from `live_candidate` to `live_limited` additionally requires:

- `live.state != "live_disabled"`.
- `live.first_live_confirmation_done == true`.
- Permission mode allows the promotion path.
- Risk Engine permits live-limited execution.
- Audit event records the actor and source.

First live unlock must remain human-controlled.

---

## 6. Change-Controlled Module Policy

### 6.1 Controlled Modules

These modules are change-controlled:

```text
aletheia/risk/
aletheia/execution/
aletheia/exchanges/bitget/auth.py
aletheia/exchanges/bitget/client.py
aletheia/config/secrets.py
aletheia/agent_runtime/permissions.py
aletheia/agent_shell/registry.py
aletheia/agent_runtime/tool_registry.py
aletheia/files/jsonl.py
aletheia/server/routes/live.py
```

- `aletheia/agent_shell/registry.py`: defines `FORBIDDEN_INTENTS` (line 415) —
  the frozenset of command names that are always blocked regardless of permission
  mode. Modifying this set changes which agent intents are structurally
  unreachable.
- `aletheia/agent_runtime/tool_registry.py`: defines `RESTRICTED_TOOLS` (line
  39) — the list of tool names that are never callable by LLM-driven agents.
  Removing a name here grants agents live, credential, or risk-limit capabilities.
- `aletheia/files/jsonl.py`: the append-only JSONL writer backing
  runtime-home `logs/audit.jsonl`. Any change to write semantics, append guarantees, or
  file-path routing directly affects the §1.4 audit chain invariant.

Changes here require stricter review and testing.

### 6.2 Required Change Note

Every change to a controlled module must state:

- Which invariant is protected or changed.
- Whether live behavior changes.
- Whether secret handling changes.
- Whether persisted state semantics change.
- Which focused tests prove the behavior.

Example:

```text
Change-controlled module: aletheia/execution/gateway.py
Invariant: existing proposal execution preserves proposal ID.
Live behavior: unchanged.
Secrets: unchanged.
Persisted state: no duplicate proposal rows.
Tests: uv run pytest tests/test_workflow_routes.py -q
```

### 6.3 Module-Specific Rules

`aletheia/risk/`

- Do not weaken or remove a risk check without explicit user approval.
- New risk checks must persist a `RiskDecision`.
- Risk rejection must be visible to API/UI consumers.
- Tests must cover both approved and rejected paths.

`aletheia/execution/`

- All order placement must pass through Execution Gateway.
- Do not let agents, dashboard routes, or Telegram call exchange clients directly
  for order placement.
- Do not create replacement proposals or orders to hide state transitions.
- Tests must assert proposal/order/risk IDs.

`aletheia/exchanges/bitget/auth.py`

- Do not change signing logic without checking current official Bitget docs.
- Tests must cover deterministic signatures and secret redaction.
- Never log raw prehash strings if they contain sensitive material.

`aletheia/exchanges/bitget/client.py`

- Market-data changes may use public endpoints.
- Account/order changes must use stored credentials and redaction.
- Demo-mode requests must preserve required demo headers.
- Live order methods must remain gated by execution/risk layers.

`aletheia/config/secrets.py`

- GET-style APIs must never return raw secrets.
- CLI/API errors must redact secret values.
- Tests must prove secrets do not appear in config, logs, agent context, or API
  GET responses.

`aletheia/agent_runtime/permissions.py`

- Restricted tools remain uncallable by LLM-driven agents.
- Permission mode expansion must be explicit and tested.
- Agents must not gain live unlock, credential mutation, risk-limit mutation, or
  execution-gateway mutation through registry mistakes.

`aletheia/server/routes/live.py`

- First live unlock must require explicit human confirmation.
- Live lock/unlock must write audit events.
- Routes must not place orders.
- Tests must cover live disabled by default and unlock audit behavior.

### 6.4 Stop Conditions

Stop and ask before editing if:

- A controlled-module change touches more than one safety boundary.
- A fix requires schema changes for trading-state traceability.
- Current tests pass but the UI would still display an untrue state.
- The guide suggests a feature but this document forbids the implementation path.
- The change would increase agent autonomy in live, risk, secret, or execution
  domains.

### 6.5 Verification Floor

Minimum verification for controlled-module changes:

```text
uv run pytest tests/<focused_test_file>.py -q
```

For cross-boundary changes, also run:

```text
uv run pytest -q
```

If frontend operational state is affected, also run:

```text
cd frontend
npm run build
```

Do not claim completion without reporting which verification commands passed.

---

## 7. Defaults For Ambiguous Cases

Use these defaults unless the user explicitly overrides them:

- Missing operational data is `not available`, not healthy.
- Empty Telegram `allowed_chat_ids` denies all chats.
- A missing strategy metric fails promotion rather than passing by assumption.
- A backend capability must exist before the dashboard displays it as available.
- A safety regression test is required before changing risk, execution, live,
  secrets, or permissions.
- Feature expansion is out of scope during trust-hardening work.
