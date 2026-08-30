# Entry Plan Contract

## Scope and authority

This Contract owns the current Candidate planning boundary from an
execution-eligible Universe Candidate through immutable `EntryPlan` activation
and Fast Monitoring. It implements the accepted
[Entry Plan proposal](../proposals/entry-plan-contract.md).

The production entry points are Universe publication,
`run_entry_planner_batch()`, canonical Entry Plan execution, and Fast
Monitoring. Durable authority remains in `candidate_planning_generations`,
`entry_plans`, canonical Proposals/Risk Decisions, and Orders. Risk is the only
approval authority and Gateway is the only order/cancel authority.

`ENTRY_PLAN_VERSION` remains `entry-plan/v1`. Existing immutable plan JSON is
not rewritten.

## Exact-venue execution eligibility

A Candidate is execution-eligible only when the configured Fast Monitoring path
can consume its exact physical `(venue, symbol, market_type)` stream for the
selected execution mode. No configured venue may be silently substituted.

An unmonitorable Candidate remains visible in Universe and Workbench, but is
persisted with `execution_eligible=false` and the
`fast_market_data_venue_unavailable` risk flag. It receives no new Candidate
Planning Generation. A legacy generation claimed from stale eligibility is
completed as terminal `NO_PLAN` with
`EXECUTION_VENUE_UNAVAILABLE:<stable-reason>` before any provider call.

If a plan becomes unmonitorable, Fast Monitoring makes the loss explicit:
pre-order states are invalidated with a reason; `ordered` and
`partially_filled` states request cancellation through Gateway reconciliation.
Fast Monitoring status exposes the bounded IDs it actually watches.

## PlanIntent authority and failure semantics

`PlanIntent` owns only qualitative judgment:

- `decision`, economic `reason_code`, thesis, scenario, evidence references,
  risk tier, and horizon;
- `execution_mode`; and
- `order_tactic` plus a registered compatible TriggerSpec identity when the
  mode is triggered.

It never owns prices, quantity, notional, leverage, stop, target, expiry,
slippage, timeout, TriggerSpec parameters, or Risk policy identity.

The allowed intent matrix is:

| Decision/mode | Tactic | Trigger |
|---|---|---|
| `NO_PLAN` | null | null |
| `RESTING_MAKER` | `POST_ONLY_LIMIT` | null |
| `TRIGGERED` | `POST_ONLY_LIMIT` | compatible registered spec |
| `TRIGGERED` | `OPPOSING_BBO_IOC` | compatible registered spec |

The TriggerSpec registry is the only source of Candidate-family compatibility,
data requirements, freshness, and parameter validation. Planner context lists
only specs compatible with the Candidate family. An unsupported or absent
family/spec combination is terminal `NO_PLAN` after at most one physical model
attempt; it is not provider unavailability and cannot consume attempts two or
three.

Provider timeout, empty content, invalid JSON, and schema failure retain the
bounded `planning_unavailable` retry policy. Model economic refusal remains a
terminal `NO_PLAN` with an allowed economic reason.

## Deterministic tactic compilation

Validated qualitative intent compiles to exactly one branch:

1. Resting Maker: `RESTING_MAKER + POST_ONLY_LIMIT`.
2. Triggered Maker: `TRIGGERED + POST_ONLY_LIMIT`.
3. Triggered Taker: `TRIGGERED + OPPOSING_BBO_IOC`.

Both Post-Only branches require an exact passive limit, `maker_required=true`,
`time_in_force=POST_ONLY`, `price_rule=EXACT_LIMIT`, no BBO/taker/slippage
fields, `max_requotes=0`, `cancel_replace_policy=CANCEL_ONLY`, and
`max_wait_sec=60`.

The opposing-BBO branch requires `maker_required=false`,
`time_in_force=IOC`, direction-correct bid/ask authority, deterministic bounded
slippage and worst-price fields, `max_wait_sec=0`, and no taker fallback.

The compiler continues to own Candidate price/geometry, volatility floor,
entry zone, stop/target, expiry, notional, leverage, Risk policy identity,
TriggerSpec parameters, producer identity, and final `EntryPlan` validation.

## Maker lifetime

Maker lifetime belongs to the actual Post-Only Order, not the plan mode, plan
approval time, or trigger-wait time. Fast Monitoring starts the timeout only
when an `ordered` or `partially_filled` plan has a durable Order and measures
from `orders.created_at`.

At the plan's positive `max_wait_sec` bound, Fast Monitoring calls
`Gateway.cancel_entry_plan_order(..., reason="entry_plan_maker_timeout")`.
Local timeout never claims cancellation success. Gateway reconciliation
preserves partial Fill/Position evidence and cancel-pending ambiguity when venue
truth is unavailable. A Triggered Maker waiting for its trigger has no running
maker clock.

## Deep-tier provider admission

One Entry Planner batch resolves `get_provider_for_tier("deep")` once. Capacity
admission and every physical inference worker use that exact alias. Direct
single-run calls resolve the deep tier when no alias is supplied.

Strict structured output remains provider-neutral. For an OpenAI-compatible
Responses provider, `reasoning_effort` maps to `reasoning.effort`. An endpoint
that explicitly rejects only this optional field may be retried once without
`reasoning`; the strict JSON Schema request is preserved unchanged. There is no
provider-specific fallback or prompt-embedded schema downgrade.

## Non-goals

This Contract does not add an exchange, market-data adapter, database table,
migration, provider type, frontend page, reprice/taker fallback, second Risk
Engine, second scheduler, or autonomous live authority. It does not tune
scanner economics, ranking, Risk limits, or trigger economics.
