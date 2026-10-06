# Aletheia Trust and Change Control

This document defines the implementation control layer for Aletheia Trading OS.
It complements `docs/governance/policies/implementation_guide.md`.

The implementation guide routes current product and subsystem authority. This
document defines what must remain true while the system changes.

For agentic workers: if this document and a task request conflict, stop and ask
before editing code. Do not resolve safety or trading-state conflicts by guessing.

---

## 1. Trust Invariants

### 1.1 Causal Record Chain

The native entry record chain is:

```text
trade_proposals.id
  -> risk_decisions.proposal_id
  -> orders.proposal_id + orders.risk_decision_id
  -> fills.order_id
  -> positions derived from executed orders/fills
```

The native close record chain is deliberately different:

```text
open positions.id
  -> orders.position_id + orders.close_reason
  -> fills.order_id
  -> the same position projected to closed
```

A close reduces an already-authorized open Position. It does not replay the
entry RiskDecision or invent a second RiskDecision that pretends to approve the
exit. Native close Orders therefore keep `risk_decision_id=NULL`; their durable
authority is the matching open Position plus `position_id`, `close_reason`, and
the close client identity. Adopted exchange observations remain explicitly
`source='adopted'` rather than claiming native decision authority.

Rules:

- A `trade_proposals` row is created exactly once for a proposed trade.
- Approving, rejecting, risk-checking, or executing an existing proposal must not
  create a second proposal row.
- Every `risk_decisions.proposal_id` must reference the original proposal.
- Every `orders.proposal_id` must reference the original proposal.
- Every native entry `orders.risk_decision_id` must reference the authoritative
  risk decision that approved execution for that proposal.
- Every native close Order must reference the open Position it reduces, match
  that Position's immutable instrument/proposal identity and quantity, and must
  not claim an entry `risk_decision_id` as close authority.
- Every `fills.order_id` must reference the order that produced the fill.
- A native `positions` row is a projection of actual orders/fills with direct
  `proposal_id` and `order_id` backlinks. Universe Entry has the canonical
  Candidate → Planning Generation → EntryPlan → Proposal → Order/Position
  ancestry; its `opportunity_id` is NULL and must not be filled with a synthetic
  Opportunity. Serenity retains its source-specific Opportunity identity.
  Shared realized-Outcome eligibility verifies these relational links; non-null
  strings alone are not evidence. The accepted source rules and history boundary
  are defined in `docs/design/contracts/learning_contract.md §11.1` (#3031).
  Adopted observations retain their existing honest `source='adopted'` chain;
  historical missing links remain missing, never backfilled to claim native entry.
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
pending ----------------> error  (Entry terminal preparation only; handoff proven absent)
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
- For Entry Plans, `executing` begins at the persisted external submission boundary;
  internal preparation owns only a claim while the Proposal stays pending/approved.
  Other execution paths retain their existing owner/lease semantics. Once external
  submission may have begun, reconciliation must prevent a second submission.
- `submitted` / `partially_filled` preserve confirmed exchange progress while
  materialization or the remaining quantity is unresolved.
- `executed` means the required Order/Fill/Position materialization chain is
  durable, not merely that an order request was sent.
- `rejected` means a human action or risk check rejected the proposal.
- `error` means execution is confirmed not submitted or failed without an
  accepted venue handoff.
  The owner-approved #3096 Entry `pending → error` edge requires a terminal
  preparation failure with no Order and no possible Broker handoff. Temporary
  market/dependency conditions remain WAIT with the Proposal pending; any possible
  handoff remains reconciliation-owned. This edge never fabricates approval or
  broadens unknown/accepted execution into a retryable preparation failure.
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
| `operator_assessment` | Retired Python Operator; historical rows only | Read-only prior assessment summaries; no current producer |
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

- Source: historical Python Operator assessments; producer retired under #3134.
- Purpose: immutable inspection of prior decisions and execution outcomes.
- No current scheduling, config mutation or fallback authority.

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

## 5. Current Admission Authority

Current admission and progression follow the [Product Gates](../../product/gates/PHASE_GATES.md)
and the relevant subsystem contract. Historical strategy-phase gates were retired
with their RuleSet/DSL/backtest execution path. Their former runtime-day,
trade-count, expectancy and `paper_passed` promotion calculations are
[archived](../../design/history/local_simulated_product_gates.md#former-strategy-promotion-computation-rules).
They must not be reimplemented as a second current admission path.

A stored strategy label, historical simulator PnL or aggregate trade count does
not qualify an external account or a Learning intervention. Current evidence
must match its actual account, causal identity and approved observation contract.
Live admission remains subject to the [Live Unlock contract](../../design/contracts/live_unlock_contract.md),
Risk approval, execution authority and actor/source audit. First Live unlock
remains human-controlled.

---

## 6. Change-Controlled Module Policy

### 6.1 Controlled Modules

These modules are change-controlled:

```text
core/aletheia/risk/
core/aletheia/execution/
extensions/exchanges/credential_routes.py
extensions/exchanges/bitget/client.py
core/aletheia/config/secrets.py
core/aletheia/agent_runtime/permissions.py
core/aletheia/agent_shell/registry.py
core/aletheia/agent_runtime/tool_registry.py
core/aletheia/files/jsonl.py
core/aletheia/server/routes/live.py
```

- `core/aletheia/agent_shell/registry.py`: defines `FORBIDDEN_INTENTS` —
  the frozenset of command names that are always blocked regardless of permission
  mode. Modifying this set changes which agent intents are structurally
  unreachable.
- `core/aletheia/agent_runtime/tool_registry.py`: defines `RESTRICTED_TOOLS` —
  the list of tool names that are never callable by LLM-driven agents.
  Removing a name here grants agents live, credential, or risk-limit capabilities.
- `core/aletheia/files/jsonl.py`: the append-only JSONL writer backing
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
Change-controlled module: core/aletheia/execution/gateway.py
Invariant: existing proposal execution preserves proposal ID.
Live behavior: unchanged.
Secrets: unchanged.
Persisted state: no duplicate proposal rows.
Tests: uv run pytest tests/test_workflow_routes.py -q
```

### 6.3 Module-Specific Rules

`core/aletheia/risk/`

- Do not weaken or remove a risk check without explicit user approval.
- New risk checks must persist a `RiskDecision`.
- Risk rejection must be visible to API/UI consumers.
- Tests must cover both approved and rejected paths.

`core/aletheia/execution/`

- All order placement must pass through Execution Gateway.
- Do not let agents, dashboard routes, or Telegram call exchange clients directly
  for order placement.
- Do not create replacement proposals or orders to hide state transitions.
- Tests must assert proposal/order/risk IDs.

`extensions/exchanges/bitget/auth.py`

- Do not change signing logic without checking current official Bitget docs.
- Tests must cover deterministic signatures and secret redaction.
- Never log raw prehash strings if they contain sensitive material.

`extensions/exchanges/bitget/client.py`

- Market-data changes may use public endpoints.
- Account/order changes must use stored credentials and redaction.
- Demo-mode requests must preserve required demo headers.
- Live order methods must remain gated by execution/risk layers.

`core/aletheia/config/secrets.py`

- GET-style APIs must never return raw secrets.
- CLI/API errors must redact secret values.
- Tests must prove secrets do not appear in config, logs, agent context, or API
  GET responses.

Concrete Data adapters may resolve only the credential reference required by
their own provider. Data orchestration, Core, Research, UI, and Legacy code must
not receive raw provider credentials or generic Secret Store enumeration access.
Missing optional-provider credentials must leave that adapter unavailable
without provider network access or unrelated policy/data side effects.

`core/aletheia/agent_runtime/permissions.py`

- Restricted tools remain uncallable by LLM-driven agents.
- Permission mode expansion must be explicit and tested.
- Agents must not gain live unlock, credential mutation, risk-limit mutation, or
  execution-gateway mutation through registry mistakes.

`core/aletheia/server/routes/live.py`

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
