# Entry Plan Contract: retired and superseded passages

> **History (moved 2026-10-10).** Moved verbatim from
> `docs/design/contracts/entry-plan-contract.md` when it was rewritten into numbered
> requirements. Each passage is an archived measurement protocol, wording superseded by a later
> section of the same Contract (capture policies, `waiting`/`active` Plan authority), or
> learning detail whose design authority is now [spec 0001](../specs/0001-learning-loop/spec.md).
> None of it is Contract authority; the current rules are the `EP-C*` requirements.

## Read-only Momentum geometry measurement (protocol detail)

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
## Archived Momentum geometry experiment

The former controlled geometry experiment is [archived](../history/local_simulated_risk_execution.md).
Current Grounded profiles and fingerprints above own entry; the archived profile
is not a selector or fallback.

## Deterministic tactic compilation (pre-capture-policy wording)

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

## Maker lifetime (pre-capture-policy wording)

Post-Only crossing is a current-market wait, not deterministic invalid geometry.
The Compiler retains the validated fixed limit and all geometry/Risk bounds even
when the frozen context quote would cross it. It still rejects invalid execution
context. Submission rechecks the unchanged strict passive-price condition against
fresh exact-instrument BBO; no crossing order, repricing or IOC fallback is allowed.
Resting Plans remain waiting within their existing Plan expiry; Candidate attention
expiry does not cancel an active Plan.
Monitoring records current passive eligibility and whether an eligible quote has
ever been observed. Workbench projects `WAITING_MARKET` while a ready stream reports
crossing. For a resting Plan with ready monitoring and no observed eligible quote, the expiry reason is
`passive_entry_never_became_eligible`; monitoring gaps retain their unavailable
reasons. Expiry still requires a later completed requalification for a successor.
Triggered Plans must additionally earn canonical trigger activation; quote
eligibility alone grants no trigger or submission authority.

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

## Pre-broker submission authority (pre-Stage-3 Plan states)

Before any UTA submit call (including profile transfer/leverage preparation), the current
claim owner is fenced and Proposal executing plus pending Order plus Plan ordered are
committed in one short transaction. No network operation spans that transaction.
External execution then follows existing UTA evidence/reconciliation semantics.

Temporary crossing, stale/unavailable current market, or expired activation before this
boundary returns to existing market/trigger waiting. The claim and pre-Order Risk allowance
are released; risk_reserved returns to waiting (resting) or triggered (conditional).
A later natural event can earn a new attempt on the same immutable Plan/Proposal and
must pass both Risk evaluations. Expired pre-broker claims can be reclaimed; stale
owners cannot mark submission. A pending local Order or an executing/unknown Proposal
prevents automatic repetition. Expiry, identity/hash, policy, permission and immutable
intent failures remain terminal. Historical terminal Plans are never revived.

## Exact-venue execution eligibility (pre-Stage-3 Plan states)

If a plan becomes unmonitorable, Fast Monitoring makes the loss explicit:
pre-order states are invalidated with a reason; `ordered` and
`partially_filled` states request cancellation through Gateway reconciliation.
Fast Monitoring status exposes the bounded IDs it actually watches.

## Entry learning context (detail; design authority moved to spec 0001)

`entry-learning-context/v1` uses planning generation as its independent unit.
NO_PLAN, no-trigger, and no-fill terminal units contribute zero ITT utility only
after the 4h look; filled units use cost-complete `filter_candidate_outcomes`
registered at each exact Fill T0 and aggregated by fill notional. Candidate-T0
forward outcomes are context only and are never re-anchored as Entry outcomes.
The cohort must match the current source provenance, behavior/schema fingerprints,
mechanism, provider, model, prompt, profile-registry, market, and policy identities.
Runtime blocks and validation/compiler failures are excluded rather than counted
as economic refusals. Profile results
are explicitly an exact profile or the declared `mechanism-aware-grounded/v1`
parent summary. Any unavailable or mixed evidence is replaced by the deterministic
empty context before inference. Observation Lab measures this path under
`mechanism_entry_learning` with the same planning-generation outcome key.
An unsupported mechanism, currently Information/Catalyst, is blocked without a
model call. Positioning remains observe_only. Missing compatible registry or
frozen-context Profiles produce `planning_blocked` / `NO_VALID_ENTRY_PROFILE`
at `runtime_preflight`, never economic NO_PLAN. Registry preflight precedes
market acquisition and inference.
