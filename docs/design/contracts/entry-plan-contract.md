# Entry Plan Contract

## Scope and authority

This Contract owns the current Candidate planning boundary from an
execution-eligible Universe or Serenity Candidate through immutable `EntryPlan` activation
and Fast Monitoring. It implements the accepted
[Entry Plan proposal](../proposals/entry-plan-contract.md).

The production entry points are Universe and Serenity publication,
`run_entry_planner_batch()`, canonical Entry Plan execution, and Fast
Monitoring. Durable authority remains in `candidate_planning_generations`,
`entry_plans`, canonical Proposals/Risk Decisions, and Orders. Risk is the only
approval authority and Gateway is the only order/cancel authority.

Historical planning retains its numeric format labels; canonical JSON and hashes
are not rewritten. Current Grounded planning writes no numeric Candidate, Planner,
Hypothesis, Profile, TriggerSpec, or Plan format label. It requires exact context,
hypothesis, selected Anchor, Profile, TriggerSpec, and compiled geometry identities.

## Single entry mainline and source ownership

Universe and `publish_serenity_candidates()` publish durable Candidates and ensure
one current PlanningGeneration. Snapshot `source_kind` and required
`reconcile_candidate_attention(producer_scope=...)` scope producer refresh and
admission loss; global TTL still bounds both sources. Exact
`venue + symbol + market_type` retains one global current owner. Cross-source
active attention, active Plan or unresolved execution blocks publication with
`instrument_owned`; a source cannot evict the other or gain a second entry owner.

Candidate context is born with hash-covered `source.kind`, `source.id` and
`source.trade_school`, source cutoff and evidence identity. Universe uses its real
member ID and `short_term`; Serenity uses its real research opportunity ID and
`serenity`. Publication/attention renewal never rewrites a generation's birth
context or hash. Serenity uses `serenity_research_v1` / `research_authority`, not
Universe Utility or a legacy momentum identity. Research direction and confidence
are not PA capability: the shared real-instrument PA evaluator, compatible Profile
and `prepare_candidate_requalification` must qualify timing; missing evidence or
a direction conflict blocks publication without changing the research direction.
See [Serenity contract](serenity_contract.md).

Planner → EntryHypothesis → Compiler → immutable EntryPlan → Plan execution /
watcher → Risk → Gateway is the only entry implementation. Resting plans may
execute immediately through that same implementation; triggered plans wait for
the registered TriggerSpec. Idle/unavailable Planner work never falls back to
TradingDecision, Ranker, PaperTrader, `signal_pending`, PiDecision entry claims,
Setup authorization/materialization or old pending defer/wake recovery. Those
entry facts and `pi_reflection.json` remain historical, not current entry truth.
Shared position review, close/reduce/adjust actions and their audit remain separate.
Unknown submissions and existing Orders/Fills/Positions retain Gateway
reconciliation, cancellation and settlement; retirement does not invent terminal
exchange outcomes or reconstruct a Proposal from an orphan legacy claim.

Current analytics expose `candidate_attention`, `entry_plans`, `planning_history`
and the workbench summary. Old ranker/funnel cohorts are read-only history, not a
current queue, capacity, admission or defer authority. Planning refresh uses
`list_candidate_planning_refresh_assets`; old pending alone adds no market demand.

## Timing, holding and source attribution

The Compiler freezes `source_kind`, `source_id`, `trade_school` and positive finite
`holding_horizon_hours` in Plan JSON/hash. Order-intent creation carries source
fields in Proposal attribution, sets Serenity `opportunity_id` to the real source
ID, and sets `Proposal.expected_horizon_hours = Plan.holding_horizon_hours`.
Risk checks source/school and holding against the immutable Plan before Gateway.
Position attribution and Serenity thesis/close feedback retain that source.

`Plan.expected_horizon_hours` remains timing (4h INTRADAY, 24h SWING, schema bound
≤48h); `expires_at` remains the 60/240-minute entry opportunity lifetime. Actual
maker Order timeout remains its separate 60-second clock. Short-term holding
copies the timing horizon; Serenity holding freezes `cfg.serenity.max_hold_days *
24` (90 days gives 2160h). A 24h SWING Plan can therefore expire for entry after
4h without shortening a filled Serenity Position to four or 24 hours. Structural
invalidation is not holding-period expiry. Serenity keeps its thesis/max-hold
exit rules and is not subjected to the short-term 72h cap. Compiler geometry keeps
the real structural stop and sizes risk against the final stop, not an earlier
shorter stop. The former Serenity fixed-percent entry-stop setting is retired; no school setting overrides that geometry.
Historical Plans may read missing holding as their original expected horizon;
missing current source/fingerprint authority never grants a new submission.

## Canonical Entry hypothesis authority

Every newly produced eligible typed Candidate enters one canonical Planner →
Compiler implementation. Historical numeric planner labels remain stored only
for compatibility and never select current behavior. Reuse is authorized by
machine-derived schema and behavior fingerprints; Git provenance remains the
source identity. There is no independent feature flag, activation timestamp,
cohort gate, or activation identity.

The Planner binds one immutable canonical `planner_input_json/hash` per cycle.
It contains bounded completed exact-instrument windows (30×4h, 48×1h, 96×15m),
existing causal Market Structure facts, at most 12 canonical 15m Anchors, and
explicit unavailable sections. The model emits one canonical `EntryHypothesis`:
controlled enums and supplied references only, with no price, size, leverage, ATR
multiplier, slippage, or other numeric execution authority. `NO_PLAN` clears every
execution field and requires a stable non-OK reason.

Before the first bind, the Planner requires available 15m candles, anchors, and
an exact-venue/symbol/market orderbook observed no later than the cutoff and at
most two seconds earlier. It reuses a qualified stored book or acquires one via
the existing native public Microstructure collector with a five-second total
timeout, exact physical market routing, and no venue fallback. Collection and
provider close occur outside database transactions; acquired facts are persisted
before being referenced. The cutoff is selected after acquisition. Missing,
invalid, or stale inputs release the claim with a typed unavailable reason and
a bounded retry delay, without consuming a model attempt or economic NO_PLAN.
Already-bound JSON/hash is never refreshed in place.
Compiler risk-policy, leverage and account-budget unavailability use the existing
bounded unavailable retries with model/compiler invocation facts retained.
Frozen identity, geometry and execution-context failures remain terminal for
that input; no failure is converted into an economic NO_PLAN or unlimited retry.

`anchor-resting-maker` and `anchor-retrace-reclaim` are deterministic compiler
Profiles. The latter reuses the existing retrace/sweep/reclaim engine and 0.25 ATR
semantics under its stable identity; Fast Monitoring never invokes AI. Plan
activation and execution revalidate the current generation fingerprints, shared
runtime authority, immutable hashes, and canonical Entry Plan provenance before
Proposal or Risk.

## Mechanism-aware Grounded Entry

The canonical implementation consumes the typed Candidate opportunity context
through one `EntryAnalysisContext` and one `EntryHypothesis` path for
Continuation, Reversal, Positioning/Forced Flow, and Information/Catalyst. Mechanism and
direction are upstream facts and must match exactly.
Candidate capability must be `supported` before market acquisition or inference.
`observe_only` and `unavailable` are typed planning blocks, not economic NO_PLAN;
execution rechecks capability in the immutable planning context.
Candidate T0 evidence, Planning T1 facts and T1-cutoff Learning evidence remain separate. Planning permits
`INTRADAY` or `SWING`; the Candidate policy's 4h comparison horizon is not
holding-period authority. At least one current Planning fact is mandatory, and AI owns no price,
size, leverage, stop, target, slippage, trigger parameter, or Risk field.

Current planning identity is venue, symbol, market type, mechanism,
direction, selection policy/epoch, and mechanism version. Rank, score, Utility,
price, and ordinary snapshot churn are excluded. Any authority-dimension change
supersedes the old generation and invalidates its active pre-order plan; Fast
Monitoring and execution revalidate both the current generation and exact
Candidate context/hash before creating or cancelling an order.

Universe Demo Candidate selection uses one Utility-family path: measured returns rank
first and genuine sample scarcity may fill unused slots by conservative signal
strength. Startup carries explicit admission_basis/startup_reason and null
economic_utility_pct through the typed Context; null is not zero return or
economic qualification. A valid supported Startup Candidate may reach the same
Planner/Compiler without measured-history admission, but still requires all
planning-time facts, anchors, capability and profile checks. Invalid history,
overdue settlement, missing costs/MAE and known economic/tail rejection cannot
be relabelled Startup. Dynamic per-refresh cutoffs do not claim a frozen
qualification window. Historical Legacy JSON/hashes remain unchanged; Legacy
does not supply new Demo Candidates or fallback generations. Risk, Gateway and
Live-unlock authority are unchanged.

The generic `grounded-anchor-resting-maker` and
`grounded-anchor-retrace-reclaim` Profiles declare compatible mechanisms,
required facts/anchors/timeframes, compiler and TriggerSpec identity, and
freshness. They may reuse deterministic geometry because geometry is not the
Alpha source. Historical Momentum profile/experiment identities are unchanged.

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

## Contract identity and planning cycles

`entry_behavior_fingerprint` and `schema_fingerprint` are derived from the
canonical schemas, prompt, profile registry, execution-route permissions, and
explicit compiler/trigger contract material. Compiler material contains the
consumed risk-envelope tables, horizons, geometry fractions and tactic bounds;
TriggerSpec material contains channels, freshness, parameter keys, geometry
identity and consumed confirmation/order-flow rules. Source AST, callable names,
Git SHA, file hashes and implementation layout are not runtime permission.
First-input availability, same-generation evidence, reference validation,
requalification, passive-price checks, Risk and Gateway remain enforced by their
existing runtime owners, independently of identity. Contract-conforming refactors
preserve compatibility; semantic changes update the consumed material and its
focused behavioral checks. The one-time removal of the old AST material causes
one compatibility cutover, not a historical rewrite. A mismatch
creates one compatibility-cutover cycle while retaining
the old row and JSON unchanged. `cycle_number`, predecessor, reason, model/compiler
invocation facts, last stage, and typed outcome are durable audit facts.

An expired Plan may open a later cycle only after a newer completed source
requalification with a later source cutoff and no unresolved Order, Fill, or
Position. Runtime/config/capability blocks resume the same cycle and never count
as economic `NO_PLAN`; validation/compiler failures are also non-economic.
Requalification source time comes from the validated Candidate opportunity
context, not its nullable database projection. New projections use that same
source. Material-evidence requalification requires a completed active snapshot
and a later context cutoff; unresolved Order/Fill/Position guards still apply.
Historical context bytes and hashes are not backfilled or rewritten.
When a replacement Candidate reuses a cycle, publication records its exact
Candidate/cycle association in the existing audit ledger, transactionally and
idempotently. Detail reads may follow this association without changing the
cycle's frozen Candidate or inferring historical links from symbol/time.

Planning requalification is separate from stable discovery ownership. Before
publication takes the writer, the existing Planner causal-window validator and
market-structure kernel project the latest confirmed 15m high, low and structure
break (at most three facts). Their canonical material fingerprint excludes
Candidate price/rank/score, rolling-window hashes, ATR-only changes, and cutoff
churn. A fact disappearing from the window is not a new event.

A terminal generation without a Plan may gain one successor only on a later
active completed snapshot, the same thread and behavior/schema, and a new fact
confirmed after its birth/frozen-input cutoff. The transaction rechecks the
expected current generation, exact Candidate context and instrument, monotonic
source time and unresolved execution before CAS. Active Proposal execution claims,
unknown/in-progress submissions and executed Proposals without their Order also
block replacement; a pending claim that merely expired does not grant execution
authority or permanently block recovery. A waiting/triggered/reserved
Plan is not superseded by this path. Missing material proof grants no recovery.
The proof and selected fact values belong to the generation's existing
requalification source JSON; attention renewals retain this birth proof.

Pre-model missing-book/required-context releases keep their existing backoff
and cycle while active. Once terminal, those exact operational outcomes may
instead use a completed causal window plus an exact persisted book observed
after terminalization and fresh at the new cutoff. A model-invoked or already
frozen failure cannot use this dependency-recovery path. Neither recovery edits
the old input/hash, result, or attempt count; economic NO_PLAN remains final for
the same material. No new ledger, scoring admission, or execution authority is
introduced.

## Exact-venue execution eligibility

A Candidate is execution-eligible only when the configured Fast Monitoring path
can consume its exact physical `(venue, symbol, market_type)` stream for the
selected execution mode. No configured venue may be silently substituted.
The selected external execution mode determines the Fast Monitoring venue.
An unconfigured or unsupported mode has no monitoring authority; the configured
public data venue does not substitute for an execution account.

An unmonitorable Candidate remains visible in Universe and Workbench, but is
persisted with `execution_eligible=false` and the
`fast_market_data_venue_unavailable` risk flag. It receives no new Candidate
Planning Generation. A runtime-ineligible generation is persisted as typed
`PLANNING_BLOCKED` before any provider call and resumes the same cycle when
authority recovers.

If a plan becomes unmonitorable, Fast Monitoring makes the loss explicit:
pre-order states are invalidated with a reason; `ordered` and
`partially_filled` states request cancellation through Gateway reconciliation.
Fast Monitoring status exposes the bounded IDs it actually watches.

## Entry hypothesis authority and failure semantics

`EntryHypothesis` is the sole current model-output contract. It owns the
qualitative decision and economic reason, mechanism/direction, supplied anchor
references, entry profile, urgency, risk tier, horizon and evidence references.
The Compiler consumes this validated hypothesis directly, including when
resuming from its immutable checkpoint; no intermediate PlanIntent protocol is
constructed.

It never owns prices, quantity, notional, leverage, stop, target, expiry,
slippage, timeout, TriggerSpec parameters, or Risk policy identity. The profile
and urgency determine the Compiler's final execution fields:

| Profile | Urgency | Execution mode | Tactic |
|---|---|---|---|
| `grounded-anchor-resting-maker` | `PASSIVE` | `RESTING_MAKER` | `POST_ONLY_LIMIT` |
| `grounded-anchor-retrace-reclaim` | `PASSIVE` | `TRIGGERED` | `POST_ONLY_LIMIT` |
| `grounded-anchor-retrace-reclaim` | `BOUNDED_URGENT` | `TRIGGERED` | `OPPOSING_BBO_IOC` |

After the model selects `NO_PLAN`, anchor/profile/urgency fields are
non-authoritative and canonicalized to null, with risk tier and horizon `NONE`.
The decision, reason, mechanism, direction and evidence references remain
unchanged. No execution Plan is produced.

Profile/TriggerSpec registries own compatibility, required data and validation.
Missing capability/Profile blocks before inference; invalid model references,
JSON/schema and compiler validation are terminal non-economic failures. Only
valid model economic refusal produces terminal `NO_PLAN`. Provider timeout and
empty content retain bounded unavailable retries.

Missing input releases refund the current attempt with 60-second backoff; broker
backpressure uses one second. Releases record current planning_unavailable while
preserving stage/invocation history. Pre-dispatch cancellation releases only the
still-owned claim under shield/CAS. After entering the planner cancellation
defers with `PLANNER_CANCELLED_DISPATCH_UNKNOWN`, retains the attempt and emits
`model_dispatch_unavailable`, not `model_skipped`, without inventing invocation.
Post-inference cancellation retains observed invocation facts. Block/defer/fail
keep these facts monotonic; existing attempt limits and material recovery apply.

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

## Trigger activation authority

Only the canonical watcher may bind an `EntryTriggerActivationV1` before a
Triggered Plan enters execution. The immutable receipt records Plan id/hash,
instrument/direction, machine-derived TriggerSpec identity, compiled geometry and
validated parameter hashes, ordered pullback/sweep/reclaim/confirmation events,
and the exact quote/trade/depth source/receive times, sequences and stream identities.
The active TriggerSpec identity uses the same explicit trigger contract material;
receipt schema labels do not select another production implementation.

SQLite appends activation facts and CAS-binds the existing Plan to its current
activation. Plan JSON/hashes and old activations are never rewritten. Execution
accepts only a stored activation id/hash, and rechecks Plan ownership, current
monitoring generation, event order and freshness. Caller-authored trigger dictionaries
provide no execution authority. Gateway repeats the shared check before claiming
a new attempt and immediately before entering the broker; Risk still owns approval.

Activation expires at the earliest required market fact's freshness boundary,
bounded by Plan expiry. Expiry or a stream-generation change requires a fresh
state-machine observation; it cannot be repaired by attaching a new quote to the old
proof. A still-unsubmitted pending/approved Proposal may use newly earned activation
on the same Plan; its original JSON/hash remains unchanged and the execution attempt
receipt binds the new activation. Claims, Orders and unresolved exchange handoffs
prevent activation replacement. Their recovery remains Gateway/reconciliation-owned
and never requires a new trigger merely to observe an existing execution.

Workbench projects `WAITING_TRIGGER` while an unsubmitted Plan needs fresh
activation and `WAITING_MARKET` for an authorized passive Plan whose current quote
would cross. The cycle timeline retains the current activation creation time.

## Maker lifetime

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
## Archived Momentum geometry experiment

The former controlled geometry experiment is [archived](../history/local_simulated_risk_execution.md).
Current Grounded profiles and fingerprints above own entry; the archived profile
is not a selector or fallback.

## Non-goals

This convergence adds source/retirement migrations, but no execution exchange,
second planning ledger, provider type, frontend page, reprice/taker fallback, second Risk
Engine, second scheduler, or autonomous live authority. It does not tune
scanner economics, ranking, Risk limits, or trigger economics.

### Realized Outcome handoff (#3031)

Universe Entry preserves Candidate → Generation → EntryPlan → Proposal →
Order/Position as its realized-Outcome causal identity; `opportunity_id` remains
NULL. Serenity keeps its source-specific Opportunity identity. Both use the
shared source-specific ancestry rule in [Learning §11.1](learning_contract.md#111-source-specific-realized-outcome-ancestry)
and retain account, Fill, cost and clean-window gates. Historical records and
measurement windows are not rewritten or reopened by this handoff.
