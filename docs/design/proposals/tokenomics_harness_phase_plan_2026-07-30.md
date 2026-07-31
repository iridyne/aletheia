---
id: tokenomics-harness-phase-plan
status: draft
github_issue: 475
related_contracts:
  - ../../references/ECONOMIC_VIABILITY_AUDIT.md
supersedes: []
---

# Tokenomics Harness — Phased Plan

> Proposal status: **draft**. Owner review pending.
> Feature id: `tokenomics_harness`.
> Umbrella tracking issue: [#475](https://github.com/iridite/aletheia/issues/475).
> Research inputs: `docs/research/tokenomics/` (nine dated notes,
> 2026-07-30).
> Last aligned with code: 2026-07-30 on branch `iridite/tokenomics`.

This proposal defines the phased plan for extending Aletheia's LLM
control surface from the measurement-only baseline established by
PR #110 (`aletheia/economics/`) into a full **harness**: a single
in-process layer that governs every LLM call through five thin,
independently-shippable phases. Phase 1 is specified concretely enough
to implement without further design work; Phases 2–5 are sketched so
the direction is legible but leave headroom for detail in follow-up
proposals if scale demands it.

The proposal is **cross-domain** (touches provider layer, server
routes, frontend Settings, tests) and therefore meets the Proposal
threshold from `docs/governance/policies/documentation_governance.md`.
It does not touch any trust-boundary module.

## 1. Summary

Aletheia is agent-native: many call sites in `aletheia/agents/`,
`aletheia/agent_shell/`, `aletheia/agent_runtime/`, `aletheia/data/`,
`aletheia/analytics/`, and `aletheia/server/routes/` end up in a
physical `ModelProvider.complete/structured/stream`. PR #110 already
records every physical attempt in the immutable `runtime_cost_events`
ledger and produces deterministic viability reports. What is missing
is a control plane: enforce budgets before the call happens, keep
prompts free of secrets, expose the ledger and the broker's live
metrics to operators, and let callers see a uniform environment
context. These are the five phases below.

## 2. Current fact

Verified against the code on branch `iridite/tokenomics` at commit
before this proposal:

- **Funnel.** Nearly all LLM traffic goes through three helpers in
  `aletheia/agents/helpers.py` (`llm_complete`, `llm_structured`,
  `llm_stream`). Two small verification bypassers exist:
  `agent_runtime/startup_gate.py` and `server/routes/model.py`. See
  `docs/research/tokenomics/llm_call_site_inventory_2026-07-30.md`.
- **Broker.** `aletheia/model_provider/broker.py` implements
  per-provider `max_concurrency`, `requests_per_minute`,
  `tokens_per_minute`, `queue_capacity`,
  `reserved_high_priority_slots`, and `timeout_sec` with priority
  admission and a sliding-window limiter. Config already reaches it
  via `ProviderConfig.inference_*` fields. See
  `docs/research/tokenomics/routing_and_fallback_2026-07-30.md`.
- **Fallback.** `aletheia/model_provider/fallback.py` runs
  request-level model fallback across
  `primary → fallback_models → detected` with per-model cooldown after
  two transient failures.
- **Attribution.** Two independent ContextVars (`run_id`,
  `economic_attribution`) reach the ledger via
  `_current_run_id()` and `_cost_metadata()`. Orchestrator binds
  `run_id`; `pi_daemon` binds `logical_agent="pi_trading_decision"`.
  See `docs/research/tokenomics/attribution_flow_2026-07-30.md`.
- **Metering.** `aletheia/economics/` (PR #110 + PR #462) provides
  immutable `pricing_snapshots`, `tool_cost_profiles`, append-only
  `runtime_cost_events`, `viability_reports`, and the
  `aletheia audit …` CLI. Kill switches: `ALETHEIA_COST_TRACING=0`,
  `ALETHEIA_VIABILITY_AUDIT=0`.
- **Absent.** No pre-flight budget check. No caller-scope rate
  limits (broker's are provider-scoped). No prompt/response
  redaction. No `/api/economics/*` HTTP surface. No Settings tile
  for spend or broker metrics. No uniform harness environment header
  in the system prompt. Config has no `economics` block.

## 3. Goals and non-goals

**Goals.**

- Give operators a live, honest view of LLM spend, broker headroom,
  and model cooldown state without dropping to a shell.
- Give operators a place to declare budgets and see would-block
  events before any enforcement is discussed.
- Surface declared budget crossings as loud, per-scope signals.
  Enforcement is operator-selectable; the default is warn-only so a
  budget declaration never becomes a hidden circuit breaker on a
  live trading loop.
- Add caller-scope rate limits (per-`AgentRun`,
  per-`logical_agent`) on top of the broker's existing per-provider
  ceilings. Load-management concern; independent of budget accounting.
- Give every LLM call access to a stable environment header (time,
  run identity, budget remaining, trading mode) **only after** an
  ablation study confirms it does not degrade agent output quality.

**Deferred goal.** Central secret scanning of outgoing prompts is
worth doing but is not scheduled by this proposal. False-positive
risk on real prompts is uncharacterised; scanning without a baseline
could break agents. Revisited when there is prompt-distribution data
worth acting on. See §4 Phase 4.

**Non-goals.**

- No live-trading unlock; no hard-risk-limit change; no touch of
  `aletheia/risk/`, `aletheia/execution/`, `aletheia/exchanges/bitget/auth.py`,
  or exchange credentials.
- No replacement of PR #110's ledger. This work extends it.
- No introduction of an external LLM gateway service. All harness
  logic stays in-process behind `aletheia/model_provider/`.
- No new provider or model catalogue capability. Provider addition
  remains via the existing Settings UI and `router.py` registry.
- No response caching in Phase 1–3. Considered in Phase 5's
  "deferred" bucket only if a measurement pass justifies it.
- No public-caller virtual keys. Aletheia's identity primitive is
  internal (`AgentRun` / `logical_agent`), so the virtual-key
  lifecycle machinery from LiteLLM does not apply.

## 4. Proposed design

The five phases below are ordered so each can ship independently.
Each preserves the "measurement never changes trading behaviour"
boundary PR #110 established: budget crossings surface as loud
signals, never silent prompt or routing mutations. Where a phase
does enforce (opt-in operator mode, rate limits), it does so through
an explicit error class the caller can distinguish from a provider
outage. Phases 4 and 5 have additional gating captured in their
sections.

### Phase 1 — Read-only harness observability

**Scope.** Add `aletheia/server/routes/economics.py` with the
following read-only GET endpoints (each calls existing functions —
no logic duplication):

| Path | Backing call |
|---|---|
| `/api/economics/status` | `tracing_enabled()`, `audit_enabled()` |
| `/api/economics/costs?days=&run_id=&include_events=&event_limit=` | `list_cost_summary` |
| `/api/economics/viability?scope=&days=&data_cost=&as_of=` | `build_*_viability(persist=False)` |
| `/api/economics/viability/reports?scope=&limit=&offset=` | direct query of `viability_reports` |
| `/api/economics/broker` | `inference_broker.snapshot()` |
| `/api/economics/model-health` | `fallback.get_model_health_snapshot()` |
| `/api/economics/pricing` | direct query of `pricing_snapshots`, `tool_cost_profiles` |

Register in `aletheia/server/app.py` alongside the existing
`app.include_router(...)` block.

Frontend: add a new `<Section title="LLM Cost & Broker Health" …>`
under the existing "Agent & Models" tab in
`frontend/src/pages/Settings.tsx`, rendered next to
`ModelProviderCard`. Renders the three snapshots in three small
tiles with a "last N days" selector for the cost tile and a status
banner when either kill switch is active. `frontend/src/api/client.ts`
gets six new thin fetch helpers.

**Not in Phase 1.**

- No writer endpoints. `pricing-set` and `tool-pricing-set` remain
  CLI-only.
- No config schema changes. `EconomicsConfig` lands in Phase 2.
- No behaviour change in `helpers.py`, `fallback.py`, or `broker.py`.

**Test plan.**

- New `tests/test_economics_routes.py` using the
  `httpx.AsyncClient` + `ASGITransport(app=app)` pattern already
  proven by `tests/test_status_routes.py`.
- Seed data with the same helpers `tests/test_economic_viability.py`
  uses (`ensure_economics_schema`, direct SQL INSERT, `add_pricing_snapshot`,
  optionally `insert_agent_run` for attribution).
- Assertions per
  `docs/research/tokenomics/economics_test_fixtures_2026-07-30.md`
  §"What Phase 1 tests should assert".
- Markers: `@pytest.mark.tier_integration`,
  `@pytest.mark.domain_runtime`, `@pytest.mark.fast`.

**Verification contract for Phase 1.**

- `GET /api/economics/costs?days=1` returns 200 and a dict with
  `event_count`, `known_cost_subtotal_usd`, `unknown_event_count`,
  `by_type` and (when `include_events=true`) an `events` array
  capped by `event_limit`.
- `GET /api/economics/viability` with kill switch active returns
  503 and an explicit reason string.
- Unknown-cost events (`estimated_cost_usd IS NULL OR
  cost_status='unknown'`) are counted in `unknown_event_count` and
  not in `known_cost_subtotal_usd`.
- Broker snapshot round-trips the per-provider metric block whose
  shape matches `broker.py::_Metrics.snapshot`.
- Frontend section renders without console errors against a fresh
  install (no data → "no events yet" honest state).

### Phase 2 — Budget policy declarations (shadow mode)

**Scope.** Introduce `EconomicsConfig` as a sibling on
`AletheiaConfig` (not on `ProviderConfig`, which stays focused on
provider identity + admission). Add `economics_budgets` table:

```sql
CREATE TABLE economics_budgets (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK(scope IN
        ('system','agent_run','logical_agent','provider','provider_tier')),
    subject TEXT,             -- run_id, logical_agent name, provider name, tier, or NULL for system-wide
    usd_cap REAL NOT NULL CHECK(usd_cap > 0),
    window TEXT NOT NULL CHECK(window IN ('daily','weekly','session')),
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
```

Extend `runtime_cost_events` with a `would_block_json TEXT DEFAULT '{}'`
column that Phase 2 populates when a call would breach a declared
budget. **No enforcement in Phase 2.** Every call still succeeds.
Operators can call a new
`GET /api/economics/budgets/would-block?days=` endpoint to review
what a hypothetical enforcement would have denied.

Settings gains a small "Budgets" table with add / edit / disable
controls. Every field on the table gets a UI control per the AGENTS.md
rule.

**Design constraint.** The would-block check runs inside
`complete_with_fallback` before candidate selection — one check per
logical call, never per attempt. It reads from a per-scope in-memory
cache of budget policies and today's spend, refreshed once per minute
against the ledger.

### Phase 3 — Budget crossing signals + caller-scope rate limits

**Scope.** Turn Phase 2's `would_block_json` observations into loud,
live signals in the ledger and the UI. Add
`enforcement_mode` on `EconomicsConfig` with three values; **the
default is `warn_only`** so a budget declaration never blocks a
live trading call by accident.

| Mode | Behaviour when a call would cross a declared budget |
|---|---|
| `warn_only` (**default**) | Call proceeds normally. Ledger row marked `would_block=true`; a broker metric increments; the Settings tile flags the scope. No error surface for the caller. |
| `fallback_to_defaults` | Call is denied at the harness layer. Callers already using `llm_structured(..., fallback_to_defaults=True)` receive schema-default values as usual with a `llm_budget_exhausted(fallback)` error string. This is the "fail-open with default response" behaviour discussed in the tracking issue. |
| `hard_block` | Call errors immediately with `llm_budget_exhausted`. Non-default; opt-in only for aggressive cost control on non-safety callers. |

**Safety-relevant callers stay warn-only regardless of mode.**
`pi_trading_decision`, `risk_critic`, `paper_trader`, and the
trading-decision paths in `agent_shell/trading_decision.py` maintain
fail-open behaviour so a budget declaration cannot silently freeze
the trading loop. Any future change that would let a stricter mode
affect those callers must go through the trust-and-change-control
review — this proposal explicitly does not authorise it.

**Caller-scope rate limits.** Broker gains a second sliding-window
limiter alongside the existing per-provider one, keyed by
`EconomicsConfig.rate_limits` scope
(`system` / `agent_run` / `logical_agent` / `provider` / `provider_tier`).
This is a load-management concern independent of budget accounting
and is enforced by default (the broker's per-provider limiter
already is). Rate-limit rejection continues to raise
`InferenceRateLimitExceeded`.

**Settings.** A new "LLM Budget → Enforcement" panel on the Agent &
Models tab exposes: the enforcement-mode dropdown, per-scope current
window spend and remaining budget, and an explicit "safety-relevant
callers stay warn-only" reminder so the operator does not expect a
stricter mode to affect trading paths.

Broker's error taxonomy already includes
`InferenceQueueFull` / `InferenceTimeout` /
`InferenceRateLimitExceeded`; helpers stop collapsing everything to
generic `llm_unavailable` and preserve the class in the error
string.

**Design constraint.** Budget accounting is a signal, not a safety
mechanism. The tracking issue #475 excludes trust-boundary changes;
hard-blocking a safety-relevant call on a spend limit would
functionally alter trading safety and belongs in a separate
proposal.

### Phase 4 — Prompt / response guardrail layer (DEFERRED)

**Status.** Deferred. Not scheduled by this proposal.

**Original scope.** A central pre-send secret scanner in
`aletheia/model_provider/guardrails.py` running deterministic regex
checks against every outgoing prompt, catching the same secret
patterns `aletheia/config/secrets.py` already refuses to serialise.

**Why deferred.** False-positive rate on real prompts is
uncharacterised. Turning on a scanner without a baseline could
break `market_observer`, `research_worker`, or any agent that
legitimately handles public-looking key-shaped tokens in market
data or documentation strings. A blocking scanner that fails closed
on its first false positive would be worse than the risk it
addresses.

**Revisit condition.** Once ledger data or a targeted audit
suggests a real leak surface, come back with:

1. A sample of representative prompts covering all agents.
2. A false-positive characterisation of candidate rules against
   that sample.
3. A detect-only observation window before any enforcement.

Until then, the AGENTS.md rule ("never put secrets in prompts")
remains caller-cooperative. No code changes in Phase 4.

### Phase 5 — Uniform harness environment header (RESEARCH-GATED)

**Status.** In plan, but rollout is gated on a prior ablation study.
Default-on is **not** authorised by this proposal.

**Scope.** Prepend a small structured block to every LLM system
prompt inside `aletheia/agents/helpers.py` (the same place
`/no_think` already lives). Header fields, honest by construction:

```
[Aletheia Harness v0.1]
Time: <UTC ISO>
Run: agent_run_id=<or absent> logical_agent=<or absent>
Trading mode: <paper_internal|…>  live_enabled=<bool>
LLM budget (scope=<key>): <remaining or "unmetered">
Tool permissions: <short list from agent_runtime.permissions>
```

Missing values omit their line rather than showing placeholders.

**Precondition — ablation study.** Before enabling the header by
default, produce evidence that it does not degrade agent output.
The study is Phase 5's first sub-deliverable. Minimum protocol:

1. Sample a representative slice of recent prompts across
   `market_observer`, `pi_trading_decision`, `risk_critic`,
   `research_worker`, and `dreaming_agent` (at least 100 per
   caller, drawn from the last 30 days of `runtime_cost_events`).
2. Re-run each prompt in two arms: **A** with the harness header
   prepended, **B** without. Same model, same temperature, same
   `max_tokens`. Record token usage and structured-schema
   compliance for both arms.
3. Compare arm A vs arm B on:
   - Schema-compliance rate (structured calls only).
   - Token-usage delta (a floor cost of ~30–50 tokens per call is
     expected; anything larger is a regression).
   - Human-scored decision quality on a sub-sample of at least 30
     PI trading decisions.
4. **Gate:** enable header default-on only if arms are
   statistically indistinguishable on schema compliance and
   decision quality within the sub-sample confidence interval.

The study's data lands in a new dated research note under
`docs/research/tokenomics/` before any default-on rollout PR is
opened.

**Rollout after the gate.** Feature flag
`harness_header_default_on` on `EconomicsConfig`, off until the
study clears it. Per-caller override via
`llm_complete(..., include_harness_header=False)` remains available
so individual callers can opt out without touching the global flag.

**Design constraint.** The header is context, not instruction. It
never contradicts the caller's role / input / output contract; it
only exposes state the caller could otherwise forget to convey.

## 5. Trust and complexity review

**Trust boundaries touched.** None. `aletheia/risk/`,
`aletheia/execution/`, `aletheia/exchanges/bitget/auth.py`,
`aletheia/config/secrets.py`,
`aletheia/agent_runtime/permissions.py`,
`aletheia/agent_runtime/tool_registry.py`,
`aletheia/agent_shell/registry.py`, and
`aletheia/files/jsonl.py` are untouched.

**New hidden state.** Two additions across five phases:

- Phase 2 introduces `economics_budgets` and a per-scope in-memory
  cache of budgets + today's spend (refreshed once per minute).
- Phase 3 adds a second sliding-window limiter in the broker
  keyed by scope. Same shape as the existing per-provider one.

Both are minimal, both are visible to `GET /api/economics/broker`
via a new metrics block.

**Coupling with existing systems.**

- Reads only pre-existing tables (`runtime_cost_events`,
  `pricing_snapshots`, `tool_cost_profiles`, `viability_reports`)
  and pre-existing snapshots (broker, model health).
- Phase 3's `BudgetExceededError` is a new error class; callers
  that already collapse LLM errors to `llm_unavailable` continue to
  work correctly (fail-closed on any LLM error, which is the
  existing default). Only callers that want distinct treatment need
  to opt in.

**Why a smaller change is insufficient.** A one-shot "add spend
tile" PR could ship Phase 1, but the tracking issue #475 lays out
five deliberate steps that together close the harness. Sequencing
them under one proposal keeps the design consistent (identity
scopes, error taxonomy, `EconomicsConfig` shape) across phases
without prejudging Phase 2–5 implementation detail.

## 6. Verification contract

Phase 1 acceptance is the executable contract in
`tests/test_economics_routes.py` plus a manual UI walk-through
against a fresh install with (a) no data, (b) a small seeded ledger,
(c) a kill switch active. Details in §4 "Test plan".

Each subsequent phase must land with:

- A focused test file per new surface
  (`test_budgets_would_block.py`, `test_budget_signals.py`,
  `test_harness_header.py`). No `test_guardrails_scan.py` unless
  Phase 4 is un-deferred.
- A named marker suite that the fast tier can run in isolation.
- A short "current-behaviour" delta line in this proposal's
  §"Resolution" section, so the boundary between phases stays
  audit-legible.

Phase 3 gates on both the tests **and** a two-week shadow-mode
review of Phase 2's would-block ledger data (small operator
sign-off, not automation). Because `enforcement_mode` defaults to
`warn_only`, "turning enforcement on" is an operator-visible
config-file change, not an automatic side effect of merging the
Phase 3 PR.

Phase 5 gates on the ablation study defined in §4 Phase 5 in
addition to its test suite. The study is executable evidence, not
narrative; its data lands in a dated research note under
`docs/research/tokenomics/` before any default-on rollout.

## 7. Rollout and rollback

**Rollout.**

- Phase 1 is a pure additive read surface; no rollout considerations
  beyond the usual PR-merge-then-observe cadence.
- Phase 2 ships with `economics_budgets` empty and the would-block
  probe **off** by default. Operators enable it via
  `EconomicsConfig.budgets_shadow_enabled` after reviewing the
  Settings tile.
- Phase 3 ships with `enforcement_mode = warn_only` **as the
  default and only recommended setting** for live systems. The
  `fallback_to_defaults` and `hard_block` modes exist for
  operator-initiated experimentation on non-safety callers; safety
  callers stay warn-only regardless.
- Phase 4 is deferred; no rollout considerations. Revisit only
  when the conditions in §4 Phase 4 "Revisit condition" are met.
- Phase 5 header defaults to **off** until the ablation study
  clears it. After the gate, `harness_header_default_on` flips to
  true; per-caller opt-out remains via
  `include_harness_header=False`.

**Rollback.**

- Every phase is behind either an env kill switch
  (`ALETHEIA_COST_TRACING`, `ALETHEIA_VIABILITY_AUDIT`, plus a
  future `ALETHEIA_HARNESS_HEADER` once Phase 5 clears its
  ablation gate) or a config toggle
  (`EconomicsConfig.enabled`, `EconomicsConfig.enforcement_mode`,
  `EconomicsConfig.harness_header_default_on`, per-policy `enabled`
  bool). Disabling the switch or reverting the toggle restores
  prior behaviour immediately without a code change.
- Ledger state (`runtime_cost_events`,
  `economics_budgets` history) is preserved across rollbacks; the
  ledger is append-only by contract.

## 8. Resolution

To be recorded once each phase completes. The stable decisions
this proposal encodes are:

- **Layered harness, one file per concern.**
  `aletheia/model_provider/` owns provider + broker + fallback and
  will gain `errors.py` (Phase 3). `aletheia/economics/` remains
  the metering module. `aletheia/agents/helpers.py` stays the
  caller-facing funnel and will gain the environment header once
  Phase 5 clears its ablation study.
- **Two-layer gate.** Once-per-logical-call gates (budget signal
  in Phase 3, and any future guardrail phase) live inside
  `complete_with_fallback`; per-attempt gates (rate limits) live
  inside the broker.
- **Base-class tracing decorator stays untouched.** PR #110's
  measurement-only boundary is preserved.
- **Config lives under a new `EconomicsConfig` sibling** on
  `AletheiaConfig`, not on `ProviderConfig`.
- **Budget is a signal, not a circuit breaker.** Default
  `enforcement_mode = warn_only`. Safety-relevant callers stay
  warn-only regardless of the operator's choice for other callers;
  moving them off warn-only requires a separate
  trust-and-change-control review.
- **Phase 4 (guardrail scanning) is deferred.** False-positive
  characterisation is a prerequisite. Not scheduled by this
  proposal.
- **Phase 5 (harness header) is research-gated.** An ablation
  study showing no measurable regression on agent output is a
  prerequisite for default-on rollout.

Execution progress and open defects for each phase live on the
umbrella issue #475 and its follow-up sub-issues, not in this
proposal.
