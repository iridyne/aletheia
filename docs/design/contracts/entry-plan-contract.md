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
the existing normalized Data provider orderbook capability with a five-second total
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

## Transient market-quality preflight — #3090 / #2886 B2/B4

Before binding `planner_input_json` or invoking a model, canonical Planner uses
Compiler's exact spread/depth/cost envelope. A fresh book outside that envelope
releases the existing claim as `ENTRY_ANALYSIS_MARKET_QUALITY_UNAVAILABLE`, with
60-second retry, no bound input, no model/compiler invocation and no consumed
model attempt. Retry reads a new exact book within the same generation. Existing
attention lifetime and dependency-recovery rules still apply. Compiler uses the
same predicate as its deterministic backstop; thresholds and geometry do not
change. Entry behavior identity includes `exact-envelope-before-input-bind/v1`,
so old bound inputs remain historical through the normal identity cutover.

## Executable static feasibility — Gate B / #2899

Planner blocks `spot_short_not_allowed` before market acquisition or inference;
Compiler and canonical Risk use the same instrument predicate. No market or venue
substitution occurs. Compiler applies `stop_distance_below_min_floor()` to the
closest permitted entry and structural stop before issuing an immutable Plan.
The existing configuration and published-regime semantics are unchanged; Risk
rechecks its current floor at execution. Neither entry/stop geometry nor Risk
limits are widened to manufacture a Plan.

### DRAFT: ATR-relative stop-floor policy (decision issue #3743, owner-requested, 2026-09-30)

A prior investigation replayed 164 real planning attempts through the compiler's
own arithmetic: the effective floor (`trading.vol_target_pct` = 1.5% of price)
is ~4.1x the median 15m ATR (0.097–1.233% of price, median 0.365%), and every
rejected/accepted attempt separated perfectly at the 1.5% line. Modelled
plan-rate under a `k x ATR` floor: k=0.5 -> 91%, k=1.0 -> 77%, k=1.5 -> 62%,
k=2.0 -> 46%, k=3.0 -> 26%, k=4.0 -> 9%, versus 16% today.

The owner accepted, as a **draft**, a config switch
`trading.stop_floor_policy` (`"vol_target_pct"` default | `"atr_multiple_draft"`)
with `trading.stop_floor_atr_multiple_draft` (default `1.0`) as the `k`
multiplier. The default preserves today's approved semantics exactly — same
floor, same rejection strings, same rechecked-at-execution behavior described
above. Under the draft policy the floor becomes `k * <15m ATR%>`, falling back
to the static `vol_target_pct` floor whenever ATR is unavailable, non-finite,
or non-positive; the floor never loosens without a measurement. The
published-sideways doubling still applies to the resulting draft floor.

Risk rechecks the draft floor at execution against the instrument's *current*
measured 15m ATR% (`aletheia.trading.sizing._lookup_atr_pct`, the same
`universe_feature_snapshots` table the Compiler's `quant_context.atr_pct`
reads, keyed by the Plan's frozen venue/symbol/market_type) — consistent with
the unchanged sentence above that Risk rechecks its *current* floor at
execution, not a value frozen at compile time. An earlier version of this
draft instead froze the evaluated ATR% into a new `EntryPlan.stop_floor_atr_pct`
field; that was rejected because `EntryPlan.model_json_schema()` is part of
the material `entry_contract_fingerprints()` hashes into the entry behavior
and schema fingerprints, so adding any field to `EntryPlan` moves both
fingerprints for every outstanding plan/generation on deploy — an identity
cutover this draft must not cause while its policy defaults off.

**Rollback:** flip `trading.stop_floor_policy` back to
`"vol_target_pct"` (no deploy or code change needed); or `git revert` the DRAFT
commit whose SHA is recorded on decision issue #3743.


Candidate refresh associates the new Candidate with its existing cycle; it never
retargets the birth-context owner, including before model input is bound.
Activation contract failures carry a bounded `entry_plan_*` code under
`ENTRY_PLAN_ACTIVATION_INVALID`; unknown exceptions and schema failures remain
explicitly unknown/schema-invalid, without copying arbitrary exception text.

Gateway quantity normalization for an EntryPlan rounds down to executable venue
steps within the approved intent. A quantity below the venue minima is rejected;
it must not be rounded above the immutable Plan ceiling. The normalized snapshot
still passes authoritative Risk and retains original Proposal identity.


## Pre-broker submission authority (#3096)

Trigger evidence freshness, current submission-market eligibility and attempt integrity
have separate owners. RESTING_MAKER has no Trigger TTL: its receipt is bounded by
Plan expiry. Triggered receipts remain bounded by canonical activation expiry; a new
quote cannot extend an old activation. Receipt and immutable Proposal hashes remain
unchanged during an attempt.

The watcher continues consuming market events while one execution task per Plan does
internal preparation. The existing execution claim fences that work; Entry Proposal
status remains pending/approved. After both Risk passes and adapter/account preparation,
Gateway checks the latest exact-instrument quote using existing quote-channel readiness,
source/receive freshness and stream identity. This is a submission-market check, not a
replacement global receipt TTL. Maker price stays fixed; IOC price is never rebound.

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
Owner-approved #3096 permits `pending → error` for terminal Entry preparation
failures only while Broker handoff is proven absent (no Order or external execution
state). Temporary market or dependency failures remain WAIT and release pre-Order
reservation/claim for a later natural attempt. Possible handoff evidence takes the
existing reconciliation path and cannot be overwritten by preparation error.


Stage timestamps and attempt identity are retained as entry_execution_stage audit facts:
market receipt, claim, both Risk passes, quantity normalization, final market check,
broker start and response/unknown. Natural-window p50/p95 observations must report
sample counts and missing stages; test timing is not runtime qualification.

### Bounded execution resolver (#3111)

EntryPlan is the sole Entry intent/authority aggregate. The pure resolver reads
snapshots supplied by the existing activation, claim, Proposal, Order, Fill and
market owners. It performs no I/O, clock reads or persistence. Its finite
`WAIT / COMMAND / STOP / RECONCILE / DONE` directive is not submission permission:
Gateway retains both Risk passes, fresh market validation and the fenced Broker
boundary.

PriceFirst leaves its exact passive Order working until Plan authority ends.
MakerPreferred permits one 60-second passive attempt, exact cancellation and
at most one IOC remainder. If the initial anchor crosses before a Proposal is
created, it may use bounded IOC as leg 0. FillFirst uses bounded IOC as leg 0.
An existing Proposal's tactic is immutable. No policy reprices or creates a third
leg. Periodic watcher reload can cancel a timed-out passive Order without quotes.

Unknown submission/materialization/cancel outcomes remain reconciliation-owned.
Cancellation uncertainty persists on the existing Order; later wakes only query
and reconcile that Order rather than repeating cancellation. Complete private
fills and the exact verified Position determine the remainder; missing evidence
never means zero fills. A full-fill race prevents another leg.

IOC price is a frozen Plan bound derived from the structural anchor, slippage
and worst-price envelope. Current opposing BBO and depth establish eligibility;
BBO movement never widens the limit or requires equality with an old quote.
For current Direct/Immediate FillFirst and MakerPreferred IOC legs, improving
opposing prices may lie outside the frozen single-point zone (long ask below
anchor; short bid above anchor). The immutable IOC limit, worst price, slippage,
current invalidation, depth, both Risk passes and aggregate leg limits still
apply. Conditional keeps its two-sided entry-zone requirement; PriceFirst is
unchanged. This owner-accepted #3111 D1 exception participates in the Entry
behavior fingerprint: existing Plans do not acquire it across the cutover,
and historical JSON/hashes remain unchanged.
Conditional activation must be fresh at the first Broker handoff. A second leg
reuses the exact original activation and immutable first handoff receipt while
requiring fresh current execution evidence; it never renews Trigger authority.

### Capture compilation contract (#3111 Stage 2)

Current `EntryHypothesis` uses `capture_priority`, nonempty `capture_reason` and
`capture_evidence_refs`; tactic-shaped `execution_urgency` is retired. At least one
capture reference must name a supplied current market fact. Learning alone cannot
supply that authority. `NO_PLAN` clears all three fields. AI still supplies no
price, quantity, timeout or slippage values.

`grounded-anchor-direct` owns Immediate activation; retrace/reclaim owns
Conditional activation. Both may compile PriceFirst, MakerPreferred or FillFirst.
The Compiler freezes one `capture_policy_id` in EntryPlan. PriceFirst has no
passive timeout, MakerPreferred has one 60-second passive attempt, and FillFirst
has no passive attempt. Existing `max_wait_sec` stores that policy value and
`worst_price` stores the already-derived geometry bound; no second numeric envelope
is introduced. The policy registry participates in machine behavior fingerprints.
Plan validation rejects a mismatched policy/tactic, timeout or geometry bound.

Historical rows and hashes are not migrated. The old profile literal remains
readable in stored Plans; it is absent from the current model-facing registry.
The current fingerprint boundary prevents historical hypotheses from acquiring
new-policy authority. The resolver and canonical per-leg Gateway path implement
the compiled policy; compilation alone is never execution or qualification proof.

### Plan authority and leg accounting (#3111 Stage 3)

Current Plan authority is `waiting` before Conditional activation and `active`
after activation (Immediate starts active). Risk reservations and Order/Fill
progress remain with their existing owners; they no longer write parallel
`risk_reserved`, `ordered` or `partially_filled` Plan states. The watcher owns
aggregate terminalization. Gateway cancellation returns the exact Order result
without terminalizing its parent Plan. Historical state rows remain readable.

One immutable `entry_leg_index` (0 or 1) on Proposal replaces the one-Proposal-per-
Plan uniqueness constraint with one-Proposal-per-Plan-leg uniqueness. Historical
NULL occupies leg 0 and preserves its original hash representation. The v87
migration changes schema atomically without rewriting historical rows or hashes.

A current capture Plan's fixed target quantity is its frozen max notional divided
by the highest permitted entry-zone price, so both long and short legs fit the
notional ceiling throughout the zone. Every leg accounts for existing same-Plan
fills and all unresolved Order remainder before its quantity/notional/risk checks.
Closing a Position does not erase historical entry fills from that accounting.
Only a confirmed cancelled native passive leg of a MakerPreferred Plan can admit
a second leg; any existing Position must be the exact verified, still-open,
unchanged-quantity Position and have no pending close. This completion reuses its
existing position slot; unrelated same-symbol positions and all exposure limits
remain binding. Price/quantity intent remains immutable per leg.

Risk owns the `APPROVED` / `DEFER` / `REJECT` disposition derived from its explicit
violation codes. Temporary market/account/capacity conditions, and authority
read/write or internal-check failures that leave a gate unevaluated rather than
refused, keep Bitget Demo Entry Proposal
pending and release preparation ownership; hard or unknown violations reject.
A failure whose violation prefix also covers durable corruption is not temporary.
Gateway consumes this disposition without classifying freeform Risk messages.
The original RiskDecision and violations remain the persisted evidence.

### Entry sizing and financing authority (#3729)

A current Plan carries an explicit, frozen financing:
`risk_intent_posture` / `risk_intent_multiplier`, `sizing_resolution`,
`leverage_resolution`, `policy_leverage_cap`, `execution_leverage`,
`target_notional_usdt`, `execution_notional_usdt`, and (futures only)
`margin_mode`. `max_leverage`, `max_notional` and `max_risk_pct` remain as legacy
mirrors so historical readers keep working, but only `execution_leverage` and
`execution_notional_usdt` grant submission authority. A Plan without them is
historical: it stays decodable and executable-to-completion, and the
order-intent boundary refuses to start a new intent from it
(`entry_plan_submission_financing_unavailable`).

Quantity is derived from the financed exposure
(`execution_notional_usdt / max(entry_zone)`), not from a policy ceiling, and
`TradeProposal.leverage = EntryPlan.execution_leverage`. The Plan remains the one
authority for the submission `margin_mode`: the Gateway reads it from the frozen
Plan and puts it on the venue command, so a Proposal cannot carry a different one. The authoritative semantics,
failure codes and the `policy_leverage_cap != execution_leverage` rule live in
[Entry Sizing and Financing](entry_sizing_and_financing_contract.md); Risk's
re-verification (drift, financed leverage/margin-mode match, per-symbol risk
units) is [Risk Engine](risk_engine_contract.md) checks #20/#21/#22.

### Entry execution read models (#3111)

Workbench exposes all ordered entry legs through `orders`, aggregating their
Fill IDs/quantity and the one exact Position. Duplicate or missing leg identities
remain unavailable; close Orders are not entry legs. Position accounting, private
fill evidence, cost completeness and historical settlement checks retain their
existing owners. Activation and capture labels describe Plan policy separately
from each actual Order tactic.

Entry Learning and cohort resolution use the same cutoff-bounded read of all
entry legs. Each Plan remains one statistical unit. A zero-fill second leg does
not erase the first leg; each filled leg contributes its exact fixed-4h Outcome,
weighted by fill notional. Missing or incomplete outcomes keep the unit
unavailable. The denominator still includes no-plan/no-trigger/no-fill outcomes;
policy identity is reported independently of measured profitability.
