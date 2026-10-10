# Entry Plan Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `EP` · **Owner code:** `core/aletheia/agent_shell/entry_planner.py`,
> `core/aletheia/trading/entry_plan_compiler.py`, `core/aletheia/trading/entry_plan.py`,
> `core/aletheia/agent_runtime/fast_trigger_watch.py`, `core/aletheia/execution/entry_plan_execution.py`

**Standing rule for every requirement below:** historical rows, canonical JSON and hashes are
never migrated, backfilled or rewritten; historical labels, Plan states and profile literals stay
readable and never select current behavior or grant new submission authority. Current Grounded
planning writes no numeric Candidate/Planner/Hypothesis/Profile/TriggerSpec/Plan format label; it
relies on exact context, hypothesis, Anchor, Profile, TriggerSpec and compiled-geometry identities.

## Scope

This Contract owns the entry mainline from an execution-eligible Candidate to an immutable
`EntryPlan` and its execution attempt: Planner → `EntryHypothesis` → Compiler → immutable Plan →
Fast Monitoring / Plan execution → Risk → Gateway. Product scope is USDT-margined perpetuals on
`bitget_demo`; Live is off. Universe is the production Candidate source; Serenity publishes through
the same path and is Experimental ([Capability Ground Truth](../../product/CAPABILITY_GROUND_TRUTH.md)).
It does not own Risk limits ([Risk Engine](risk_engine_contract.md)), sizing/leverage
([Entry Sizing and Financing](entry_sizing_and_financing_contract.md)), order/cancel/reconciliation
([Execution](execution_contract.md)), Position review/close, Universe selection, or learning
(spec [0001](../specs/0001-learning-loop/spec.md)). It adds no second planning ledger, Risk
Engine, scheduler, reprice/taker fallback or autonomous live authority, and does not tune scanner
economics, ranking, Risk limits or trigger economics.

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/universe/service.py::_ensure_planning_generations` | call | Universe refresh in `UniverseService` |
| `core/aletheia/trading/serenity_publication.py::publish_serenity_candidates` | call | `extensions/research/research_worker.py` (Experimental) |
| `core/aletheia/agent_shell/entry_planner.py::run_entry_planner_batch` | scheduler job | `core/aletheia/agent_runtime/scheduler.py::_run_pi_trading_decision` |
| `core/aletheia/trading/entry_plan_compiler.py::compile_entry_plan` | call | `entry_planner.py::_compile_current_entry_plan` |
| `core/aletheia/agent_runtime/fast_trigger_watch.py::start_fast_trigger_watch` | runtime task | server lifecycle (`core/aletheia/server/app.py`) |
| `core/aletheia/execution/entry_plan_execution.py::execute_entry_plan` | call | `fast_trigger_watch.py::_submit_entry_plan` |
| `core/aletheia/execution/gateway.py::cancel_entry_plan_order` | call | Fast Monitoring and Plan execution |
| `core/aletheia/cli/inspect_cmd.py::momentum_geometry_replay` | CLI | `aletheia inspect momentum-geometry-replay` (read-only) |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `candidate_planning_generations` | `storage/repositories.py::ensure_candidate_planning_generation`, `bind_candidate_planner_input`, `complete_candidate_planning_with_plan` | Planner, Workbench, requalification | one row per cycle; bound input/hash immutable; monotonic stage/outcome |
| `entry_plans` | `repositories.py::complete_candidate_planning_with_plan`, `transition_entry_plan` | Fast Monitoring, Plan execution, Risk, Workbench | Plan JSON/hash immutable; monotonic status CAS |
| `entry_trigger_activations` | `repositories.py::bind_entry_trigger_activation` | Plan execution, Gateway | append-only |
| `trade_proposals.entry_leg_index` | Plan execution | Gateway, Risk, read models | immutable per Proposal (`trg_entry_leg_immutable`, migration v87) |
| audit event `entry_execution_stage` | `execution/gateway.py::_entry_execution_stage` | operators | append-only |

## Requirements

### EP-C01 Single entry mainline

`The system shall implement Candidate entry only through Planner → EntryHypothesis → Compiler → immutable EntryPlan → Plan execution / watcher → Risk → Gateway.`

- WHEN the Planner is idle or unavailable THEN no TradingDecision, Ranker, PaperTrader, `signal_pending`, PiDecision, Setup or legacy defer/wake path produces an entry; those facts and `pi_reflection.json` are history only.
- WHEN an Order, Fill or Position already exists from any path THEN Gateway reconciliation, cancellation and settlement still own it; no terminal exchange outcome or Proposal is invented.
- WHEN analytics read entry state THEN current reads are `candidate_attention`, `entry_plans`, `planning_history` and the workbench summary; ranker/funnel cohorts are read-only history, and planning refresh demand comes from `list_candidate_planning_refresh_assets`.

### EP-C02 One instrument owner across sources

`When a source publishes a Candidate, the system shall keep one global current planning owner per exact venue + symbol + market_type and block a cross-source publication with instrument_owned.`

- WHEN a Universe Candidate exists with active attention, an active Plan or unresolved execution THEN a Serenity publication for the same instrument is blocked with `instrument_owned` (`repositories.py`), and vice versa.
- WHEN `reconcile_candidate_attention(producer_scope=...)` runs THEN it refreshes and drops only that producer's attention; the global TTL still bounds both sources.

### EP-C03 Birth context is frozen

`When a PlanningGeneration is created, the system shall freeze hash-covered source.kind, source.id, source.trade_school, source cutoff and evidence identity, and publication or attention renewal shall never rewrite them.`

- WHEN the source is Universe THEN `source.id` is the real member ID and `trade_school` is `short_term`; WHEN Serenity THEN the research opportunity ID and `serenity`, with `serenity_research_v1` / `research_authority` instead of Universe Utility.
- WHEN a Serenity Candidate lacks qualifying real-instrument PA evidence (`prepare_candidate_requalification`) or its direction conflicts THEN publication is blocked without changing research direction ([Serenity contract](serenity_contract.md)).
- WHEN a refreshed Candidate reuses an existing cycle (including before input is bound) THEN publication records the Candidate/cycle association transactionally and idempotently and never retargets the birth-context owner.

### EP-C04 Timing is separate from holding

`The Compiler shall freeze source_kind, source_id, trade_school and a positive finite holding_horizon_hours in the Plan, separate from expected_horizon_hours and expires_at.`

- WHEN horizon is `INTRADAY` / `SWING` THEN `expected_horizon_hours` is 4 / 24 (schema bound ≤ 48) and `expires_at` is 60 / 240 minutes after approval (`entry_plan_compiler.py::_HORIZON_HOURS`, `_HORIZON_LIFETIME_MINUTES`).
- WHEN the source is Universe THEN holding copies the timing horizon; WHEN Serenity THEN holding is `cfg.serenity.max_hold_days * 24`.
- WHEN an order intent is created THEN `Proposal.expected_horizon_hours = Plan.holding_horizon_hours`, source fields go into Proposal attribution, and Risk checks source/school/holding against the Plan before Gateway (`entry_plan_execution.py`).
- WHEN geometry is compiled THEN risk is sized against the final structural stop; no school setting overrides geometry.

### EP-C05 Exact-venue execution eligibility

`If Fast Monitoring cannot consume a Candidate's exact physical (venue, symbol, market_type) stream for the selected execution mode, then the system shall treat the Candidate as not execution-eligible and shall not substitute another venue.`

- WHEN `universe/fast_stream.py::fast_market_data_unavailability_reason` returns a token THEN the Candidate stays visible in Universe and Workbench with `execution_eligible=false` and receives no new PlanningGeneration.
- WHEN an already-created generation becomes runtime-ineligible THEN the Planner records `PLANNING_BLOCKED` with `EXECUTION_VENUE_UNAVAILABLE:<token>` at `runtime_preflight` before any provider call, and the same cycle resumes when authority recovers.
- WHEN a live Plan becomes unmonitorable THEN Fast Monitoring invalidates it with `entry_plan_execution_venue_unavailable`; a Plan with an Order requests cancellation through Gateway reconciliation.

### EP-C06 AI Entry hypothesis authority is one canonical planner

`The system shall run every eligible typed Candidate through one canonical Planner → Compiler implementation authorized only by machine-derived schema/behavior fingerprints and shared runtime authority, with no feature flag, activation timestamp or cohort gate.`

- WHEN Plan execution starts THEN `entry_plan_execution.py::_require_ai_entry_authority` rechecks runtime authority and the bound `planner_input_json`, failing closed with `ai_entry_runtime_authority_unavailable`.
- WHEN a Plan is activated or executed THEN the current generation fingerprints, shared runtime authority, immutable hashes and canonical provenance are revalidated before Proposal or Risk.
- WHEN an activation contract fails THEN the reason is a bounded `entry_plan_*` code under `ENTRY_PLAN_ACTIVATION_INVALID`; unknown and schema failures stay explicitly unknown/schema-invalid without arbitrary exception text.

### EP-C07 Canonical hypothesis authority and failure semantics

`The Entry Planner model shall emit exactly one strict EntryHypothesis that owns only the qualitative decision, reason, mechanism/direction, supplied anchor refs, entry profile, capture priority, risk tier, horizon and evidence refs.`

- WHEN the schema is exported THEN every property is required, `additionalProperties` is false, and no price, quantity, notional, leverage, stop, target, expiry, slippage, timeout, TriggerSpec parameter or Risk policy field exists (`trading/entry_analysis.py::EntryHypothesis`).
- WHEN the decision is `NO_PLAN` THEN anchor/profile/capture/risk-intent fields are canonicalized to null, risk tier and horizon to `NONE`, a non-`OK` reason is required, and no Plan is produced.
- WHEN the decision is `ENTRY_PLAN` THEN `capture_priority`, non-empty `capture_reason` and `capture_evidence_refs` are required, and at least one capture ref and one supporting ref name a supplied current market fact (`validate_hypothesis_references`); learning refs alone never satisfy this.
- WHEN mechanism or direction differs from the Candidate opportunity THEN validation fails.

### EP-C08 Model-visible quantitative fields

`The entry model view shall pass quant_context and execution_context whole, computing each derived field on the entry path from inputs the Planner already loads, and shall list a field in its block's unavailable_fields instead of emitting 0, a neutral label or a fallback when its input is missing, too short or 0/0.`

- WHEN `quant_context.values` is built (cited as `feature:<id>`) THEN it carries the snapshot fields plus `rsi_14` (1h RSI14); `rsi_zone_15m|1h|4h` = `oversold` at RSI ≤ 30, `overbought` at ≥ 70, else `neutral`; `oi_price_quadrant` = `price_{up|down}_oi_{up|down}` from the signs of `return_1h` and `oi_delta_1h`, or `flat` when |return_1h| < 0.2 % or |oi_delta_1h| < 0.5 %.
- WHEN `quant_context.technical.<15m|1h|4h>` is built from that admitted closed-bar window at the cutoff (no forming bar; cited as `technical:<tf>:<window source_fingerprint>`, a current-market fact ref) THEN it carries `vwap_dev_pct` (close vs VWAP20 of typical price), `obv_normalized`, `ema_20`, `sma_20`, `bb_pct_b` (20, 2σ); MACD(12,26,9) `macd_line|signal|hist`, `macd_hist_delta`, `macd_hist_atr` (hist / ATR14), `macd_state` ∈ {`bull_expanding` (hist > 0, delta > 0), `bull_contracting` (hist > 0, delta ≤ 0), `bear_expanding` (hist < 0, delta < 0), `bear_contracting` (hist < 0, delta ≥ 0)}; Wilder `adx_14`, `plus_di_14`, `minus_di_14`.
- WHEN the timeframe is 15m or 1h THEN it also carries `ema_50`, `sma_50`, `ma_alignment` (`bullish` if close > EMA20 > EMA50, `bearish` if close < EMA20 < EMA50, else `mixed`) and `trend_regime` (`trend_up`/`trend_down` when ADX ≥ 25 and DI order and `ma_alignment` agree, `range` when ADX < 20, else `transition`); 4h (35 bars) defines none of them.
- WHEN the book is admitted THEN `execution_context.imbalance_ratio` = (bid − ask) / (bid + ask) size over the top 10 levels, in [−1, 1].
- WHEN any of these labels is present THEN it carries no execution authority and does not change Anchor, Profile, Compiler or Risk behavior; nothing is added to `UniverseFeatureSnapshot`.

### EP-C09 Entry anchor registry

`The Planner shall supply at most 26 canonical anchors, built per timeframe, side-filtered and band-merged, and shall assign an invalidation role only to a level that is on the correct side and unbroken.`

- WHEN anchors are built THEN `confirmed_swing` and `broken_structure_level` come from every admitted 15m, 1h and 4h window with their `timeframe`; `candidate_reference` and `causal_breakout` stay 15m.
- WHEN a level is not strictly below (long) / above (short) every reference price, the Candidate price and the latest closed 15m close, or a closed bar of its timeframe has closed beyond it since it became known THEN a swing low (long) / high (short) is removed and a broken-structure level keeps only `entry_candidate`; the opposite-kind swing stays `entry_candidate`; roles never come from swing type alone.
- WHEN surviving levels of one timeframe, kind and role lie within `0.5 × ATR14(timeframe)` of the band's lowest level THEN they merge into one anchor with `zone = {low, high, touches}`, `price` = `low` (long) / `high` (short) for invalidation and `high` (long) / `low` (short) for entry, newest member's `known_at_ms`, and all members' `source_fact_refs`; an unbanded anchor carries no `zone` key.
- WHEN the cap applies (after filtering and merging) THEN `candidate_reference` and `causal_breakout` are kept first, then structural anchors nearest the Candidate price (ties: newer first) up to 26 (`entry_analysis.py::_MAX_ANCHORS`).
- WHEN registry parameters change THEN `entry_behavior_fingerprint` (`anchor_registry` material) changes and one compatibility-cutover cycle opens.
- WHEN the Compiler runs THEN its `INVALIDATION_ANCHOR_WRONG_SIDE` check and stop/target geometry (reads `anchor.price` and 15m ATR tolerance) are unchanged by the registry.

### EP-C10 Required inputs before first bind

`Before binding planner_input_json, the Planner shall require 15m candles, anchors with at least one correct-side invalidation anchor, and an exact-instrument orderbook observed no later than the cutoff and at most 2 seconds earlier, and if any is missing it shall release the claim with a typed unavailable reason without consuming a model attempt.`

- WHEN a qualified stored book is absent THEN one is acquired through the normalized Data provider orderbook capability with a 5-second total timeout, exact physical routing and no venue fallback; collection and provider close happen outside DB transactions and facts are persisted before being referenced; the cutoff is chosen after acquisition.
- WHEN the book is unavailable / required context is missing THEN the claim is released with `ENTRY_ANALYSIS_EXECUTION_CONTEXT_UNAVAILABLE` / `ENTRY_ANALYSIS_REQUIRED_CONTEXT_UNAVAILABLE` and a 60-second retry.
- WHEN no anchor has an invalidation role THEN the model is not called and the claim is released with `NO_INVALIDATION_STRUCTURE` and the 60-second retry; it is not an economic `NO_PLAN`.
- WHEN the bound input exists THEN it is bounded completed exact-instrument windows (`ENTRY_WINDOW_BARS` = 35×4h, 50×1h, 96×15m), causal Market Structure facts, anchors and explicit unavailable sections, and it is never refreshed in place.

### EP-C11 Market-quality retry before input binding

`If a fresh exact book is outside the Compiler's spread/depth/cost envelope, then the Planner shall release the claim as ENTRY_ANALYSIS_MARKET_QUALITY_UNAVAILABLE with a 60-second retry, binding no input and invoking no model or compiler.`

- WHEN the retry runs THEN it reads a new exact book within the same generation; attention lifetime and dependency recovery still apply.
- WHEN the Compiler runs THEN it applies the same predicate (`entry_plan_compiler.py::require_entry_market_quality`) as a backstop; thresholds and geometry are unchanged, and entry behavior identity includes `exact-envelope-before-input-bind/v1`.

### EP-C12 Capability and profile preflight

`Before market acquisition or inference, the Planner shall require Candidate capability supported and at least one compatible registered Profile, and shall otherwise record a typed planning block at runtime_preflight, never an economic NO_PLAN.`

- WHEN capability is `observe_only` or `unavailable` THEN the block is `CANDIDATE_CAPABILITY:<status>`; Positioning/Forced Flow is `observe_only` because its positioning facts are unavailable (`universe/candidate_opportunity.py`).
- WHEN the mechanism has no compatible Profile (currently Information/Catalyst) or the frozen context has no Profiles THEN the block is `NO_VALID_ENTRY_PROFILE`.
- WHEN the mechanism and direction come from the Candidate THEN they are upstream facts; planning permits `INTRADAY` or `SWING` and requires at least one current planning fact.
- WHEN a Startup Candidate (null `economic_utility_pct` with explicit `admission_basis`/`startup_reason`) is supported THEN it reaches the same Planner/Compiler with all the same checks; null is not zero return or economic qualification.

### EP-C13 Planner failure classes

`The Planner shall classify every non-plan outcome as a bounded retry, a terminal non-economic failure, or a terminal economic NO_PLAN, and only a valid model economic refusal shall produce NO_PLAN.`

- WHEN model references, JSON/schema or deterministic compiler validation fail THEN the outcome is terminal non-economic; compiler failure persists `ENTRY_PLAN_COMPILE_INVALID:<bounded-code>` after one model attempt.
- WHEN provider timeout, empty content, risk-policy, leverage or account-budget unavailability, or an unexpected internal exception occurs THEN the bounded unavailable retry applies with invocation facts retained (internal exceptions log a full traceback).
- WHEN a missing-input release happens THEN the attempt is refunded with a 60-second backoff; broker backpressure uses one second.
- WHEN cancellation arrives before dispatch THEN only the still-owned claim is released under shield/CAS; WHEN after dispatch THEN it defers with `PLANNER_CANCELLED_DISPATCH_UNKNOWN`, keeps the attempt and emits `model_dispatch_unavailable`, without inventing an invocation.

### EP-C14 Profiles and capture compilation

`The Compiler shall compile a validated ENTRY_PLAN hypothesis into exactly one registered Profile activation and one frozen capture_policy_id from trading/entry_capture.py::CAPTURE_POLICIES, and Plan validation shall reject a mismatched policy, tactic, timeout or price bound.`

| Profile (`trading/entry_profiles.py`) | Activation | `execution_mode` |
|---|---|---|
| `grounded-anchor-direct` | `IMMEDIATE` (Plan starts `active`) | `RESTING_MAKER` |
| `grounded-anchor-retrace-reclaim` | `CONDITIONAL`, TriggerSpec `anchor-retrace-reclaim`, freshness 2000 ms | `TRIGGERED` |

| `capture_priority` | Frozen `capture_policy_id` | Order tactic | `max_wait_sec` |
|---|---|---|---|
| `PRICE_FIRST` | `exact-anchor-passive-until-plan-terminal/v1` | Post-Only until the Plan is terminal | 0 (no passive timeout) |
| `MAKER_PREFERRED` | `exact-anchor-passive-then-bounded-ioc/v1` | one Post-Only attempt, then bounded IOC | 60 |
| `FILL_FIRST` | `bounded-opposing-bbo-ioc/v1` | bounded opposing-BBO IOC only | 0 |

- WHEN either Profile is used THEN it may compile any capture priority; the retired `execution_urgency` field and the `grounded-anchor-resting-maker` profile literal are readable in stored Plans only.
- WHEN the tactic is `POST_ONLY_LIMIT` THEN the Plan has an exact `limit_price` inside the entry zone, `maker_required=true`, `time_in_force=POST_ONLY`, `price_rule=EXACT_LIMIT`, no `bbo_reference`, `max_requotes=0`, `cancel_replace_policy=CANCEL_ONLY` (`trading/entry_plan.py`).
- WHEN the tactic is `OPPOSING_BBO_IOC` THEN `maker_required=false`, `time_in_force=IOC`, direction-correct `bbo_reference`, positive `max_slippage_bps`, a `worst_price`, `max_wait_sec=0` and no taker fallback.
- WHEN a Plan is compiled THEN the Compiler alone owns price/geometry, volatility floor, entry zone, stop/target, expiry, notional, leverage, Risk policy identity, TriggerSpec parameters and producer identity; Fast Monitoring never invokes AI.

### EP-C15 Static executable geometry and capability

`The Compiler shall apply the same stop-distance floor as Risk (stop_distance_below_min_floor) to the closest permitted entry and structural stop before issuing a Plan, and shall never widen geometry or Risk limits to manufacture a Plan.`

- WHEN the instrument is not `usdt_futures` THEN it never reaches the Planner (`universe/registry.py` admits perpetuals only); the residual `spot_short_not_allowed` check in `entry_plan.py::entry_instrument_violation` is unreachable on the mainline and is not a spot support claim.
- WHEN the floor is computed THEN `trading.stop_floor_policy` defaults to `vol_target_pct`; the `atr_multiple_draft` value is an unapproved default-off draft ([history](../history/entry_plan_stop_floor_draft.md)).
- WHEN Risk evaluates the Proposal THEN it rechecks its current floor at execution.

### EP-C16 Machine-derived Entry contract identity

`The system shall derive entry_behavior_fingerprint and schema_fingerprint (trading/entry_contract.py::entry_contract_fingerprints) from canonical schemas, prompt, ENTRY_WINDOW_BARS, profile registry, execution-route permissions and explicit compiler/trigger material, and shall reuse a generation only when both match.`

- WHEN compiler material is hashed THEN it contains the consumed risk-envelope tables, horizons, geometry fractions and tactic bounds; TriggerSpec material contains channels, freshness, parameter keys, geometry identity and confirmation/order-flow rules; the capture policy registry participates.
- WHEN source AST, callable names, Git SHA, file hashes or layout change THEN identity does not change; a semantics change updates the consumed material.
- WHEN fingerprints mismatch THEN one compatibility-cutover cycle opens and the old row and JSON stay unchanged.
- WHEN identity matches THEN first-input availability, reference validation, requalification, passive-price checks, Risk and Gateway are still enforced by their own owners.

<a id="contract-identity-and-planning-cycles"></a>

### EP-C17 Contract identity and planning cycles

`The system shall record cycle_number, predecessor, reason, model/compiler invocation facts, last stage and typed outcome as durable audit facts, and shall open a later cycle only on the conditions below.`

- WHEN a Plan expired THEN a later cycle opens only after a newer completed source requalification with a later source cutoff and no unresolved Order, Fill or Position.
- WHEN a runtime/config/capability block clears THEN the same cycle resumes; such blocks and validation/compiler failures never count as economic `NO_PLAN`.
- WHEN requalification needs a source time THEN it comes from the validated Candidate opportunity context, not its nullable DB projection.
- WHEN Universe planning identity (venue, symbol, market type, mechanism, direction, selection policy/epoch, mechanism version) changes THEN the old generation is superseded and its active pre-order Plan invalidated; rank, score, Utility, price and snapshot churn are excluded.

### EP-C18 Requalification on confirmed structure

`When a terminal generation without a Plan sees a new 15m fact confirmed after its frozen-input cutoff, the system shall allow exactly one successor under the same thread and behavior/schema, preserving the old input, outcome and attempt count.`

- WHEN the requalification fingerprint is computed THEN it projects the latest confirmed 15m high, low and structure break (≤ 3 facts) and excludes Candidate price/rank/score, rolling-window hashes, ATR-only changes and cutoff churn; a fact leaving the window is not an event.
- WHEN the successor is written THEN the transaction rechecks expected current generation, exact Candidate context and instrument, monotonic source time and unresolved execution before CAS; active claims, unknown/in-progress submissions and executed Proposals without Order block it; a waiting/active Plan is not superseded this way.
- WHEN the terminal outcome was a pre-model missing-book/required-context release THEN recovery may use a completed causal window plus an exact book persisted after terminalization and fresh at the new cutoff; a model-invoked or frozen failure cannot.
- WHEN the outcome was economic `NO_PLAN` THEN it stays final for the same material; the proof is stored in the generation's requalification source JSON.

### EP-C19 Plan authority states

`The system shall write only waiting (Conditional before activation), active (Immediate at creation, Conditional after activation) and the terminal states filled, invalidated, expired, cancelled and failed for a current Plan.`

- WHEN a Plan is created THEN status is `active` for `IMMEDIATE` and `waiting` for `CONDITIONAL` (`repositories.py::complete_candidate_planning_with_plan`).
- WHEN Risk reserves, an Order is placed or partially fills THEN those facts stay with their own owners; `risk_reserved`, `triggered`, `ordered` and `partially_filled` exist only for historical rows (`entry_plan.py::ENTRY_PLAN_STATUSES`).
- WHEN a terminal state is reached THEN it never revives (`transition_entry_plan` CAS); the watcher owns aggregate terminalization and Gateway cancellation returns the Order result without terminalizing the Plan.

### EP-C20 Trigger activation authority

`Only the canonical watcher shall bind an EntryTriggerActivationV1 to a Conditional Plan, and execution shall accept only a stored activation id/hash.`

- WHEN binding THEN the receipt records Plan id/hash, instrument/direction, TriggerSpec identity, geometry and parameter hashes, ordered pullback/sweep/reclaim/confirmation events and exact quote/trade/depth source/receive times, sequences and stream identities; SQLite appends it and CAS-binds the Plan (`bind_entry_trigger_activation`).
- WHEN a caller supplies a trigger dictionary THEN it grants nothing; Gateway repeats the shared check before claiming an attempt and before the broker.
- WHEN activation is created THEN it expires at the earliest required fact's freshness boundary, bounded by Plan expiry; expiry or a stream-generation change requires a fresh observation, never a new quote on the old proof.
- WHEN a still-unsubmitted Proposal exists THEN it may use a newly earned activation on the same Plan; claims, Orders and unresolved handoffs prevent replacement.

### EP-C21 Passive waiting and maker lifetime

`While a Post-Only Plan's limit would cross the current exact-instrument BBO, the system shall wait without submitting, repricing or falling back to IOC, and the passive timeout shall run only from the actual Order's orders.created_at.`

- WHEN the frozen context quote would cross THEN the Compiler still keeps the fixed limit; submission rechecks the strict passive-price condition on fresh BBO (`post_only_would_cross_bbo`).
- WHEN a `MAKER_PREFERRED` passive Order's age reaches `max_wait_sec` THEN the resolver commands `CANCEL_PASSIVE` with `passive_window_elapsed`; `PRICE_FIRST` has no timeout; a Conditional Plan waiting for its trigger has no maker clock.
- WHEN a resting Plan with ready monitoring expires without ever seeing an eligible quote THEN the expiry reason is `passive_entry_never_became_eligible`; monitoring gaps keep their unavailable reasons.
- WHEN cancellation is requested THEN local timeout never claims success; Gateway reconciliation keeps partial-fill and cancel-pending ambiguity. Candidate attention expiry does not cancel an active Plan.

### EP-C22 Pre-broker submission authority

`Before any UTA submit call, the Gateway shall fence the current claim owner and commit Proposal executing plus the pending Order in one short transaction with no network operation inside it, after both Risk passes and a final exact-instrument market check.`

- WHEN preparation runs THEN the watcher keeps consuming events, one execution task per Plan holds the claim, and the Entry Proposal stays pending/approved; the final market check uses quote-channel readiness, source/receive freshness and stream identity; maker price stays fixed and IOC price is never rebound.
- WHEN `before_submission` finds a lost or stale claim, a non-pending Proposal, an existing Order or a Plan not `active` THEN it raises (`entry_execution_owner_lost` / `entry_plan_authority_not_active`) and nothing is submitted.
- WHEN crossing, stale/unavailable market or expired activation is found before the boundary THEN the claim and pre-Order Risk allowance are released and the Plan keeps waiting for a later natural event that must pass both Risk evaluations again.
- WHEN a terminal preparation failure occurs while Broker handoff is proven absent THEN the Proposal may move `pending → error`; possible handoff evidence takes the reconciliation path; expiry, identity/hash, policy, permission and intent failures are terminal.
- WHEN a stage completes THEN an `entry_execution_stage` audit event records it (claim, both Risk passes, quantity normalization, final market check, broker start, response/unknown); test timing is not runtime qualification.

### EP-C23 Bounded execution resolver

`The pure resolver trading/entry_execution_projection.py::resolve_entry_execution shall map snapshots from existing owners to one WAIT / COMMAND / STOP / RECONCILE / DONE directive with no I/O, clock read or persistence, and a COMMAND shall never itself be submission permission.`

- WHEN capture is `PRICE_FIRST` THEN the passive Order works until Plan authority ends; `MAKER_PREFERRED` allows one passive attempt, exact cancellation and at most one IOC remainder (IOC as leg 0 if the anchor crosses before a Proposal exists); `FILL_FIRST` uses IOC as leg 0; no policy reprices or creates a third leg.
- WHEN submission, materialization or cancel outcomes are unknown or fill evidence is incomplete THEN the directive is `RECONCILE`; missing evidence never means zero fills, and later wakes query the existing Order instead of re-cancelling.
- WHEN an IOC leg is priced THEN its limit is the frozen Plan bound (`ioc_limit_price`) from structural anchor, slippage and `worst_price`; current BBO and depth only establish eligibility (`entry_ioc_outside_frozen_bounds`).
- WHEN the Plan is Immediate with `MAKER_PREFERRED`/`FILL_FIRST` THEN an improving opposing price may lie outside the single-point zone (long ask below, short bid above the anchor) while all other bounds apply; Conditional keeps the two-sided zone requirement (`entry_plan_execution.py`).
- WHEN a second leg runs on a Conditional Plan THEN it reuses the original activation and first-handoff receipt and requires fresh execution evidence; it never renews trigger authority.

### EP-C24 Leg accounting

`The system shall allow at most one Proposal per Plan leg (entry_leg_index 0 or 1) and shall size every leg after accounting for existing same-Plan fills and all unresolved Order remainder.`

- WHEN the target quantity is fixed THEN it is frozen execution notional divided by the highest permitted entry-zone price.
- WHEN a second leg is considered THEN only a confirmed-cancelled native passive leg 0 of a `MAKER_PREFERRED` Plan admits it, and any existing Position must be the exact verified, open, unchanged-quantity Position with no pending close; it reuses that position slot and all exposure limits still bind.
- WHEN a Position closes THEN its historical entry fills stay in the accounting; historical NULL `entry_leg_index` occupies leg 0.

### EP-C25 Risk disposition consumption

`The Gateway shall consume Risk's APPROVED / DEFER / REJECT disposition without classifying freeform Risk messages.`

- WHEN the disposition is `DEFER` (temporary market/account/capacity, or an authority read/write or internal-check failure that left a gate unevaluated) THEN the Bitget Demo Entry Proposal stays pending and preparation ownership is released.
- WHEN a violation is hard, unknown, or its prefix also covers durable corruption THEN it rejects; the RiskDecision and violations remain the persisted evidence.

### EP-C26 Frozen financing and quantity normalization

`The Plan shall carry frozen financing (risk_intent_posture/multiplier, sizing_resolution, leverage_resolution, policy_leverage_cap, execution_leverage, target/execution_notional_usdt, margin_mode), and only execution_leverage and execution_notional_usdt shall grant submission authority.`

- WHEN a Plan lacks them THEN it stays decodable and executable-to-completion, but a new intent is refused with `entry_plan_submission_financing_unavailable`.
- WHEN the order intent is built THEN `TradeProposal.leverage = EntryPlan.execution_leverage`, quantity = `execution_notional_usdt / max(entry_zone)`, and Gateway reads `margin_mode` from the frozen Plan; `max_leverage`, `max_notional`, `max_risk_pct` are legacy mirrors only. Semantics live in [Entry Sizing and Financing](entry_sizing_and_financing_contract.md).
- WHEN Gateway normalizes quantity THEN it rounds down to venue steps (`_prepare_exchange_quantity(..., allow_increase=False)`); below venue minima it rejects, never rounding above the Plan ceiling; the normalized snapshot still passes authoritative Risk.

### EP-C27 Deep-tier provider admission

`When one Entry Planner batch runs, the system shall resolve get_provider_for_tier("deep") once and use that exact endpoint for capacity admission and every inference worker.`

- WHEN a direct single run has no alias THEN it resolves the deep tier.
- WHEN an OpenAI-compatible Responses endpoint explicitly rejects only the optional `reasoning` field THEN the request is retried once without it, keeping the strict JSON Schema; there is no provider-specific fallback or prompt-embedded schema downgrade (`extensions/model_endpoints/openai_compatible.py`).

### EP-C28 Operator observability

`The Workbench shall project persisted per-Plan monitoring evidence, immutable Plan integrity and every entry leg without inventing state.`

- WHEN a Plan row is projected (`server/routes/universe.py::_entry_plan_projection`) THEN `monitoring_state`/`monitoring_evidence_json` pass through unchanged and `contract_valid` is false on a hash mismatch.
- WHEN an unsubmitted Conditional Plan needs fresh activation THEN the state is `WAITING_TRIGGER`; WHEN an authorized passive Plan's quote would cross THEN `WAITING_MARKET` (`trading/entry_lifecycle.py`); the cycle timeline keeps the current activation creation time.
- WHEN entry legs are read THEN all ordered legs appear through `orders` with aggregated Fill IDs/quantity and the one exact Position; duplicate or missing leg identities are unavailable; close Orders are not entry legs; activation/capture labels are shown separately from each Order's tactic.
- WHEN Fast Monitoring status is read THEN it exposes the bounded Plan IDs it actually watches.

### EP-C29 Read-only Momentum geometry measurement

`The momentum-geometry-replay inspect report shall read only through a read-only DB connection and shall have no production write, Risk, Gateway or activation authority.`

- WHEN T0 bar provenance is missing, a bar is forming/future or identity mismatches THEN the result is `unavailable`; no-trigger and unavailable units stay in the denominator with no-entry utility 0 (`analytics/momentum_geometry_replay.py`).
- WHEN historical microstructure (quote, trade, Books5, fee, funding) is absent THEN Trigger status and cost-after lift are unavailable. Protocol detail is [history](../history/entry_plan_contract_superseded_passages.md); the archived experiment is in [history](../history/local_simulated_risk_execution.md) and is never a selector or fallback.

### EP-C30 Learning boundary

`The Planner shall read only the deterministic empty or exact-cohort entry-learning context (trading/entry_learning.py, entry-learning-context/v1), and learning shall never supply capture, execution or Risk authority.`

- WHEN learning evidence is unavailable or mixed THEN the deterministic empty context is used before inference. Target learning behavior is spec [0001](../specs/0001-learning-loop/spec.md); realized-Outcome ancestry (Universe: Candidate → Generation → Plan → Proposal → Order/Position, `opportunity_id` NULL) is owned by the [Learning contract](learning_contract.md).

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| Book / required context / invalidation structure missing before bind | release claim, 60 s retry, no attempt consumed | `ENTRY_ANALYSIS_*_UNAVAILABLE`, `NO_INVALIDATION_STRUCTURE` | unavailable, never `NO_PLAN` |
| Thin book outside envelope | release claim, 60 s retry | `ENTRY_ANALYSIS_MARKET_QUALITY_UNAVAILABLE` | unavailable |
| Capability / Profile / venue / runtime authority missing | block at `runtime_preflight` | `CANDIDATE_CAPABILITY:*`, `NO_VALID_ENTRY_PROFILE`, `EXECUTION_VENUE_UNAVAILABLE:*` | blocked, never `NO_PLAN` |
| Schema / reference / compiler validation fails | terminal non-economic | `ENTRY_PLAN_COMPILE_INVALID:<code>` | not counted as refusal |
| Provider timeout / empty content / internal error | bounded retry | planning_unavailable, `PLANNER_INTERNAL` | unavailable |
| Market crosses / stale before broker boundary | WAIT, release claim and reservation | `WAITING_MARKET`, `post_only_would_cross_bbo` | no Order |
| Submission / cancel outcome unknown | RECONCILE via Gateway | `external_outcome_unresolved` | never zero fills |
| Plan unmonitorable | invalidate / cancel via Gateway | `entry_plan_execution_venue_unavailable` | — |

## Authority

The Planner and Compiler may create Plans and planning facts only; Risk alone approves and Gateway
alone submits or cancels. This Contract may never change Risk limits, credentials, Live unlock or
Gateway submission, and AI supplies no numeric execution value.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| EP-C01 | `tests/specs/spec_entry_plan_contract.py::EP-S01` (decision lifecycle), `EP-S03` |
| EP-C02, EP-C03 | `tests/test_entry_planner.py::test_unbound_candidate_refresh_preserves_birth_context_owner` (via `EP-S15`) |
| EP-C04 | `tests/test_entry_plan_execution.py::test_compiled_holding_is_separate_from_swing_timing` (via `EP-S15`) |
| EP-C05 | `EP-S05` |
| EP-C06 | `EP-S10` |
| EP-C07 | `EP-S06` |
| EP-C08 | `EP-B18` |
| EP-C09, EP-C10 | `tests/test_entry_planner.py`, `tests/test_entry_analysis.py` |
| EP-C11 | `EP-S14` (`entry_plan_contract`) |
| EP-C15 | `EP-S15` |
| EP-C16 | `EP-S09` |
| EP-C17, EP-C18 | `EP-S13` |
| EP-C21 | `EP-S02` (decision lifecycle), `tests/test_entry_plan_execution.py` |
| EP-C22 | `EP-B16` |
| EP-C23, EP-C24 | `EP-B17`, `tests/test_entry_plan_execution.py` |
| EP-C27 | `EP-S07` |
| EP-C28 | `EP-S12` |
| EP-C29 | `EP-S08` |
| EP-C12, EP-C13, EP-C14, EP-C19, EP-C20, EP-C25, EP-C26, EP-C30 | `tests/test_entry_planner.py`, `tests/test_entry_plan_execution.py`, `tests/test_entry_lifecycle_cutover.py` (no dedicated SpecClaim) |

## Known conformance gaps

- **EP-C05 visible reason.** The approved rule says an unmonitorable Candidate is persisted with
  the `fast_market_data_venue_unavailable` risk flag. No production code writes that flag (0 hits in
  `core/`, `extensions/`); the visible reason is the hard-gate check
  `action_route_unavailable:<token>` (`universe/candidate_eligibility.py`) and the Planner block
  `EXECUTION_VENUE_UNAVAILABLE:<token>`. `tests/test_universe_service.py` re-implements the removed
  flag loop inside the test, so it verifies nothing in production.

## Decision references

- Proposal: [Entry Plan proposal](../proposals/entry-plan-contract.md) (accepted).
- #3785 model-visible quantitative fields; #3850 item 5 anchor registry (owner D1, 2026-10-09).
- #3090 / #2886 B2/B4 market-quality preflight; Gate B / #2899 static feasibility; #3743 stop-floor
  draft (owner-requested, 2026-09-30, default off).
- #3096 pre-broker submission authority (owner-approved `pending → error`).
- #3111 bounded resolver, capture compilation (Stage 2), Plan authority and leg accounting
  (Stage 3, migration v87); owner-accepted D1 exception for improving Immediate IOC prices.
- #3729 entry sizing and financing; #3031 realized-Outcome handoff; #2556 Momentum geometry
  measurement (archived).
- Superseded wording and retired protocols: [history](../history/entry_plan_contract_superseded_passages.md).
