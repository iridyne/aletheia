---
id: entry-plan-contract
status: accepted
github_issue: 2288
related_contracts: [decision_lifecycle_contract.md]
supersedes: []
---

# Entry Plan Contract (D2)

## Summary

Replace the overlapping Candidate/Setup/Decision/Proposal vocabulary with one
owner-facing chain: Candidate → EntryPlan → Trigger → Order → Fill → Position.
A durable `EntryPlan` is the Operator's committed entry, and an on-demand
Entry Planner wakes LLM work only for a new or materially changed Candidate
generation.

## Current fact

Verified on master `9d628ae2` before implementation:

- `aletheia/trading/entry_plan.py` holds stop-floor and RR helpers only. There
  is no `EntryPlan` type.
- `aletheia/agent_shell/entry_planner.py` does not exist. Operator planning is
  `evaluate_trading_opportunity(trading_context: dict)` in
  `trading_decision.py`, wrapped by `commands.py`.
- Universe refresh and reused snapshots both call `notify_pi_daemon`. Unchanged
  context can skip LLM via operator evaluation defer, but there is no durable
  EntryPlan object and no one-generation planning ownership.
- Fast Trigger (`aletheia/agent_runtime/fast_trigger_watch.py`) invalidates
  setups on fingerprint/auth mismatch. It does not cancel a resting maker.
- Bitget Mix v2 `place_order` omits TIF. UTA v3 hardcodes
  `"timeInForce": "gtc"`. Spot v2 hardcodes `"force": "GTC"`. There is no
  post-only or IOC path, so a Limit cannot be a true Maker.
- Risk already acquires `entry_risk_reservations` keyed by `proposal_id` inside
  the authoritative `evaluate_proposal` lock. That table is the compatibility
  reservation; it is not renamed.
- Physical SQLite names remain `universe_members`, `universe_setups`,
  `conditional_entry_authorizations`, `trade_proposals`, and
  `pi_decisions.action='signal_pending'`. Those names are wire/storage
  compatibility, not the active domain.

## Goals and non-goals

Goals:

- Canonical immutable `EntryPlan` with geometry, execution kind, invalidation,
  and explicit taker permission.
- On-demand planning: one material Candidate generation produces at most one
  LLM planning wake; refresh/TTL/price tick on an unchanged generation produce
  zero new LLM planning work.
- `RESTING_MAKER` uses true post-only. `TRIGGERED` uses post-only Maker or an
  EntryPlan-approved opposing-BBO IOC/marketable Limit.
- Fast Trigger owns continuous invalidation and timely cancel of resting makers.
- Trigger/Order hot-path Risk stays code-only. Resting orders reserve before
  the book; triggered orders reserve atomically before submit.
- Active-domain terms migrate to Candidate / EntryPlan / Trigger / Order /
  Fill / Position. Legacy storage names stay only with a per-site justification.
- Cover restart, duplicate wake, material replacement, invalidation, stale
  data, permission downgrade, reservation, maker-cancel race, partial fill,
  BBO taker, and Order/Fill/Position reconciliation.

Non-goals:

- No new SQLite tables and no destructive schema/migration.
- No Trigger alpha or strategy-threshold changes.
- No silent Maker → Taker fallback.
- No LLM, provider, web, or unbounded scan on the Trigger/Order hot path.

## Proposed design

`EntryPlan` lives in `aletheia/trading/entry_plan.py` and reuses the existing
RR/stop helpers. Persistence is namespaced JSON
(`entry_plan/v1`) inside the current `UniverseSetup.motive_evidence` column.
`insert_universe_setup` remains idempotent on
`(venue, symbol, market_type, direction, material_fingerprint)`.

`EntryPlanner` (`aletheia/agent_shell/entry_planner.py`) compares the current
material fingerprint to the last planned fingerprint in that JSON. Unchanged
→ return the existing plan, `model_invoked=False`. Changed or missing → call
`evaluate_trading_opportunity(trading_context: dict)` once, then persist.

Execution kinds on the plan, never inferred later:

- `RESTING_MAKER`: post-only Limit. Risk reservation before the order rests.
- `TRIGGERED`: post-only Maker, or `taker_allowed=true` opposing BBO IOC
  (long → ask, short → bid) inside `max_slippage_bps`. Failed Maker does not
  become Taker unless that fallback is already on the plan.

Fast Trigger keeps the existing stream loop and adds cancel of resting Orders
for the setup's `proposal_id` on invalidation, expiry, material replacement,
and permission downgrade. Cancel-vs-fill races reconcile; they do not escalate
to Taker.

Risk stays on `acquire_entry_risk_reservation(proposal_id=...)`. The
`proposal_id` is the compatibility key for the existing table. The EntryPlan
id is stored in `motive_evidence` and Proposal `attribution_snapshot`.

## Trust and complexity review

Controlled paths: `aletheia/exchanges/bitget/client.py`,
`aletheia/execution/bitget_broker.py`, `aletheia/risk/engine.py` (reservation
call sites only). TIF is explicit on `OrderRequest`; default remains today's
GTC/omitted behavior so existing callers do not change venue semantics.

No new hidden table. Planner state is namespaced JSON on an existing column.
Hot path stays deterministic.

A smaller rename-only change cannot express post-only vs GTC Limit, cannot
own resting-maker cancel, and cannot prove one-generation LLM ownership.

## Verification contract

Focused tests, not a full macOS suite:

- `EntryPlan` geometry/RR envelope and frozen identity.
- `should_invoke_planner`: first material fingerprint plans once; identical
  refresh/TTL/tick does not invoke the planner callback; a changed fingerprint
  plans again and invalidates the previous generation.
- RESTING_MAKER serializes Bitget `post_only` (Mix `force`, UTA `timeInForce`).
  Default market/GTC callers stay on current bodies.
- TRIGGERED BBO: long uses ask, short uses bid; envelope breach refuses.
- Failed Maker does not place a Taker unless `taker_allowed` is on the plan.
- Fast Trigger invalidation cancels resting orders; a fill-during-cancel race
  reconciles and does not submit a Taker.
- Restart reloads the plan from `motive_evidence` and does not re-call LLM.
- Existing `tests/test_entry_plan_min_stop.py` still passes.

## Rollout and rollback

Land behind existing paper/demo paths. Default TIF omission preserves current
Bitget bodies. Rollback is revert of the PR; JSON namespace is ignored by old
code and does not require a down-migration.

## Resolution

Accepted as the D2 direction for #2288. Implementation is **not** merged.
Normative content stays in this proposal until the behavior is current and
then moves to `decision_lifecycle_contract.md`.
