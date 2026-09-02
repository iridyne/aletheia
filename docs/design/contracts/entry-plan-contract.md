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

Historical and legacy planning retains `entry-plan/v1`; its canonical JSON and
hash are not rewritten. New eligible Momentum planning uses `entry-plan/v2`,
which additionally requires exact context, hypothesis, selected Anchor,
Profile, and compiled geometry identities.

## AI Entry hypothesis authority

Every newly produced eligible Momentum Candidate receives `entry-planner/v3`.
There is no independent feature flag, activation timestamp, cohort gate, or
activation identity. Planner and execution retain the global Paper account-mode
and Live-disabled authority checks.

Planner V3 binds one immutable canonical `planner_input_json/hash` per generation.
It contains bounded completed exact-instrument windows (30×4h, 48×1h, 96×15m),
existing causal Market Structure facts, at most 12 canonical 15m Anchors, and
explicit unavailable sections. The model emits `entry-hypothesis/v1`: controlled
enums and supplied references only, with no price, size, leverage, ATR multiplier,
slippage, or other numeric execution authority. `NO_PLAN` clears every execution
field and requires a stable non-OK reason.

`anchor-resting-maker/v1` and `anchor-retrace-reclaim/v1` are deterministic
compiler Profiles. The latter reuses the existing retrace/sweep/reclaim engine
and 0.25 ATR semantics under its own identity; Fast Monitoring never invokes AI.
Plan activation and execution revalidate the V3 generation, Paper/Live gate,
immutable hashes, and V2 provenance before Proposal or Risk.

## Planner contract epoch

`planner_contract_version` is an explicit, manually maintained semantic epoch
for PlanIntent validation, deterministic compilation, and planning outcome
meaning. It is not a commit, release, or Universe Snapshot version. A current
generation is reusable only when both its thesis identity and Planner contract
version match.

On an epoch mismatch, the old generation is retained as non-current with
`planning_contract_superseded`. Its active Entry Plan is fail-closed invalidated;
terminal plans remain terminal. An execution-eligible Candidate receives one new
generation for the current epoch, and subsequent Snapshot churn reuses it.

## Exact-venue execution eligibility

A Candidate is execution-eligible only when the configured Fast Monitoring path
can consume its exact physical `(venue, symbol, market_type)` stream for the
selected execution mode. No configured venue may be silently substituted.
For `paper_internal`, normalized `market_data.ccxt_exchange` is the public
fast-data authority: Bitget and Binance are supported. Binance uses public Spot
and USD-M quote, aggregate trade, one-minute Kline, and depth streams; Spot depth
is unavailable until its REST snapshot and diff stream form a continuous local
book. External execution modes retain their execution venue as the authority.

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

After the model selects `NO_PLAN`, execution-only fields are non-authoritative
and are canonicalized to the null/`NONE` row above before semantic validation.
The model-owned decision, reason, thesis, scenario, and evidence remain unchanged.

The TriggerSpec registry is the only source of Candidate-family compatibility,
data requirements, freshness, and parameter validation. Planner context lists
only specs compatible with the Candidate family. An unsupported or absent
family/spec combination is terminal `NO_PLAN` after at most one physical model
attempt; it is not provider unavailability and cannot consume attempts two or
three.

Provider timeout, empty content, invalid JSON, and schema failure retain the
bounded `planning_unavailable` retry policy. Model economic refusal remains a
terminal `NO_PLAN` with an allowed economic reason.

Deterministic compiler validation failure is terminal after one model attempt
and persists `ENTRY_PLAN_COMPILE_INVALID:<bounded-code>`. Unexpected internal
exceptions retain bounded unavailable retry and full internal traceback logging.

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

## Read-only Momentum geometry measurement

`momentum-geometry-measurement/v2` is the preregistered, read-only measurement
contract for #2556. It pairs one exact Momentum Candidate occurrence and
immutable Entry Plan under the same direction, PlanIntent-derived Risk policy,
plan T0 cutoff, cost identity, and four-hour outcome identity.

The control identity is `momentum-retrace-reclaim/v1`. Its compiled
`breakout_level` is the Candidate `entry_price_usd`; this is baseline evidence,
not a claim that Candidate price is a causal breakout. The treatment identity is
`momentum-retrace-reclaim/v2`: bars 1–20 define the directional breakout, bar 21
must be completed and close strictly beyond it, ATR is the frozen
`atr-true-range-sma-14/v1`, and subsequent candle evidence records bounded
pullback, explicit sweep, and reclaim geometry.

Every T0 bar must be continuous, exact-instrument, provider-identified, completed,
first persisted no later than plan approval, and unchanged after that cutoff.
Missing provenance, forming/future data, a non-confirming bar, or identity mismatch
is `unavailable`; no fallback may relabel Candidate price as treatment breakout.
No-trigger and unavailable units remain in the intent-to-treat denominator with
the frozen no-entry utility.

Historical Candle Geometry never fabricates quote, trade, Books5, fee, funding,
or execution evidence. Without those retained facts, Trigger status and
cost-after Geometry Lift remain explicitly unavailable. The inspect report has
no production write, Risk, Gateway, or activation authority.

`tests/specs/spec_entry_plan_contract.py::test_momentum_geometry_measurement_is_read_only_and_fail_closed`
is the executable claim for this boundary.
## Momentum geometry v2 controlled Paper boundary

The planner continues to request `momentum-retrace-reclaim/v1`. For an exact
Momentum Candidate with 21 completed causal bars, the existing Learning allocator
may assign the same PlanIntent to `momentum-retrace-reclaim/v2`. Control remains
v1. Missing or invalid geometry records an unavailable denominator unit and keeps
the v1 behavior.

A v2 Entry Plan persists its immutable treatment-exposure receipt, exact geometry,
and source fingerprints. Execution fails closed unless that receipt proves an
applied `momentum_entry_geometry` treatment. Only `paper_internal` and
`bitget_demo` are eligible; Live remains unavailable. The existing sizing formula,
Risk policy, Gateway, quantity-at-trigger calculation, and protective exit path are
unchanged. Fast Monitoring uses the same v2 state machine and forwards a trigger
only after this authority check.
## Non-goals

This Contract does not add an execution exchange, database table,
migration, provider type, frontend page, reprice/taker fallback, second Risk
Engine, second scheduler, or autonomous live authority. It does not tune
scanner economics, ranking, Risk limits, or trigger economics.
