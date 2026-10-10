# Lifecycle Capability Map

This is the stable registry of what the trading mainline must be able to do,
organized along the lifecycle of one trade opportunity and the Position it
becomes. It was approved by the owner on 2026-10-09
([proposal](../design/proposals/lifecycle_capability_map.md); decision record in
[DECISIONS.md](../governance/decisions/DECISIONS.md), entry "2026-10-09 — Lifecycle
capability map").

**This file holds definitions only.** It never states which level a capability
has reached, which gap is open, or what to work on next. Those change weekly and
live in GitHub:

- Current levels and evidence: the dated `/capability-review` comments on the
  roadmap issue ([`.claude/commands/capability-review.md`](../../.claude/commands/capability-review.md)
  is the fixed procedure). A review comment is a point-in-time observation, not
  a support claim.
- Gaps: one GitHub Issue per gap, first body line
  `Capability: CAP-XXX-nn · Target: Ln`, labelled `cap/CAP-XXX-nn`.
- Priority: the existing `priority/*` labels; only the owner sets or removes
  `priority/now`.

Authority is unchanged by this registry. Admission comes from
[Active Runtime Scope](ACTIVE_RUNTIME_SCOPE.md), capability promises from
[Capability Ground Truth](CAPABILITY_GROUND_TRUTH.md), approved behavior from
the linked Contract, and code navigation from
[SYSTEM_MAP](../design/architecture/SYSTEM_MAP.md). A target level here is a
development aim, not a promise; reaching L4 does not change Ground Truth unless
the owner also updates it.

## Maturity ladder

A review assigns each capability the highest level whose criterion is fully met.
Evidence classes:

| Class | Meaning |
|---|---|
| E1 Static | Reviewed code contains the necessary entry, data flow, consumer, and error path. |
| E2 Executable | A focused test or bounded local reproduction crosses the important seams and asserts the final result. |
| E3 Runtime observation | The capability occurred naturally and produced the expected durable/user-visible result. This increases operational confidence but is not a support gate. |

| Level | Name | Criterion |
|---|---|---|
| L0 | Missing | No production code implements the outcome. |
| L1 | Dormant | Code exists, but it has no production caller, or it is off by default on the active route. |
| L2 | Wired | Entry point, durable state and downstream consumer are all connected, and a focused test crosses the seams and asserts the final result (E1 + E2). |
| L3 | Observed | The outcome occurred naturally on an exact deployed commit and left the expected durable or user-visible result (E3). |
| L4 | Qualified | The capability's own qualification criterion below is met on an exact deployed commit, **and the owner accepted it**. No script or reviewer may assign L4 alone. |

A capability observed to fail on the deployed runtime is reported at the
highest level still true, with the failure named as a gap. Levels never carry
over between commits without fresh evidence for L3/L4.

## Rules

- IDs have the form `CAP-<STAGE>-nn`. An ID, once assigned, is never reused or
  renumbered; a retired capability moves to [Retired IDs](#retired-ids).
- Adding or removing a capability, or changing a target level, is an owner D1
  decision. Targets below are **proposed** defaults pending owner review.
- Probes name where a reviewer starts looking; they are not the definition. If
  a probe symbol moves, update the probe in the same PR that moved it.
- v0 registers only mainline capabilities, two or three per stage.

## Stages

The eleven stages refine the `causal_chain` declared in
[ACTIVE_RUNTIME_SCOPE.md](ACTIVE_RUNTIME_SCOPE.md): `opportunity` covers
CAND; `system_operator_decision` covers PLAN, EPLAN and TRIG; `proposal` is PROP;
`risk_decision` is RISK; `order` and `fill` are EXEC; `position` covers POS and
CLOSE; `genuine_outcome` is OUT. LEARN closes the loop back to the next decision.
Operator observability is not a stage: each capability's L3 evidence must be
readable from an owner-facing surface.

| Key | Stage | Question the stage answers |
|---|---|---|
| CAND | Candidate | Which instruments and directions deserve planning right now? |
| PLAN | Planning | What does the model hypothesize about this Candidate, from frozen facts? |
| EPLAN | Entry Plan | What exact, executable, risk-bounded plan does that hypothesis compile to? |
| TRIG | Trigger | When do market conditions make the plan act, and how is the order captured? |
| PROP | Proposal | What immutable order intent, with full ancestry, enters the Gateway? |
| RISK | Risk | Is this intent allowed, under one authoritative evaluation? |
| EXEC | Execution | Does the intent become a real venue order and fill, with protection attached? |
| POS | Position management | While open, is the Position protected and actively reviewed? |
| CLOSE | Close and settlement | Does every exit become a verified close with complete costs? |
| OUT | Outcome | What did the trade earn, net of all costs, and why? |
| LEARN | Review and learning | Does what was learned change a later decision, through a declared gate? |

## Capabilities

Each entry lists: outcome, contract, proposed target, probes (code symbol,
production caller, focused test), and the L4 qualification criterion.

### CAND — Candidate

#### CAP-CAND-01 — Venue-scoped crypto instrument universe

- **Outcome:** only instruments that are exactly tradable on the active venue and
  are USDT-margined crypto perpetuals enter scanning; spot never enters the
  registry, and real-world-asset perps (venue `isRwa`: equity, metal, index) and
  unknown profiles are excluded fail-closed.
- **Contract:** [candidate_selection_utility_contract.md](../design/contracts/candidate_selection_utility_contract.md) (Boundary); [Capability Ground Truth](CAPABILITY_GROUND_TRUTH.md) instrument scope.
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/universe/registry.py` `_is_excluded`; caller `sync_registry` in the same module; test `tests/test_registry_market_boundary.py`.
- **L4 criterion:** n/a at target L3.

#### CAP-CAND-02 — Measured-economics admission

- **Outcome:** Candidates are admitted by cost-adjusted measured utility from
  settled history; Startup (signal-strength) admission only fills slots that
  measured evidence cannot, and the admission basis is recorded per Candidate.
- **Contract:** [candidate_selection_utility_contract.md](../design/contracts/candidate_selection_utility_contract.md); [opportunity_outcome_contract.md](../design/contracts/opportunity_outcome_contract.md) for the settled outcomes it reads.
- **Target (proposed):** L4.
- **Probes:** code `core/aletheia/universe/economic_utility.py` `estimate_economic_utility`, `core/aletheia/universe/candidate_selection_utility.py` `select_admitted_candidates`; caller `core/aletheia/universe/candidate_selection_shadow.py`; test `tests/test_candidate_selection_utility.py`.
- **L4 criterion:** on one exact deployed commit, for 7 consecutive days at least
  half of admitted Candidates carry a measured basis, and the admitted cohort's
  4h cost-adjusted mean outcome exceeds the non-admitted cohort's with a 95%
  lower bound above zero; owner accepts.

#### CAP-CAND-03 — Atomic Candidate publication and planning handoff

- **Outcome:** each scan publishes its Candidates atomically with a frozen entry
  reference, and every execution-eligible Candidate gets exactly one current
  Planning Generation carrying its identity forward.
- **Contract:** [candidate_selection_utility_contract.md](../design/contracts/candidate_selection_utility_contract.md) (Typed Candidate Opportunity handoff); [entry-plan-contract.md](../design/contracts/entry-plan-contract.md) (Contract identity and planning cycles).
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/storage/repositories.py` `ensure_candidate_planning_generation`; callers `core/aletheia/universe/service.py` (`_ensure_planning_generations`, from `UniverseService.refresh`) and `core/aletheia/trading/serenity_publication.py` `publish_serenity_candidates`; tests `tests/test_candidate_opportunity_persistence.py`, `tests/specs/spec_universe_pipeline.py`.
- **L4 criterion:** n/a at target L3.

### PLAN — Planning

#### CAP-PLAN-01 — Durable planning generation lifecycle

- **Outcome:** each Planning Generation is claimed, leased, retried within a
  bound and terminated with a typed outcome; no model call or plan is duplicated
  and no generation is left without an exit.
- **Contract:** [entry-plan-contract.md](../design/contracts/entry-plan-contract.md) (Contract identity and planning cycles; Entry hypothesis authority and failure semantics).
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/agent_shell/entry_planner.py` `run_entry_planner_batch`; caller `core/aletheia/agent_runtime/pi_daemon.py`; test `tests/test_entry_planner.py`.
- **L4 criterion:** n/a at target L3.

#### CAP-PLAN-02 — Frozen causal planning context and anchor registry

- **Outcome:** the model sees one causally consistent, frozen context
  (multi-timeframe bars, structure, indicators, order book) and may only choose
  entry and invalidation from system-computed anchors on the correct side.
- **Contract:** [entry-plan-contract.md](../design/contracts/entry-plan-contract.md) (Canonical Entry hypothesis authority; Entry anchor registry); [technical_analysis_contract.md](../design/contracts/technical_analysis_contract.md).
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/trading/entry_analysis.py` `build_entry_analysis_context`, `structural_anchors`; caller `core/aletheia/agent_shell/entry_planner.py`; test `tests/test_entry_analysis.py`.
- **L4 criterion:** n/a at target L3.

#### CAP-PLAN-03 — Constrained, validated model hypothesis

- **Outcome:** one model call returns a structured hypothesis (anchors, profile,
  capture priority, risk posture, or an explicit NO_PLAN) that is schema- and
  reference-validated before compilation; invalid output is a typed failure.
- **Contract:** [entry-plan-contract.md](../design/contracts/entry-plan-contract.md) (Entry hypothesis authority and failure semantics); [llm_inference_harness_contract.md](../design/contracts/llm_inference_harness_contract.md); [entry_sizing_and_financing_contract.md](../design/contracts/entry_sizing_and_financing_contract.md) (Contract A — AI Risk Intent).
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/trading/entry_analysis.py` `validate_hypothesis_references`; caller `core/aletheia/agent_shell/entry_planner.py`; test `tests/test_entry_analysis.py`.
- **L4 criterion:** n/a at target L3.

### EPLAN — Entry Plan

#### CAP-EPLAN-01 — Deterministic plan geometry

- **Outcome:** the compiler turns a hypothesis into an immutable Entry Plan whose
  stop, entry zone and target are computed deterministically from the chosen
  anchors, with reward:risk measured at the executable entry and the
  stop-distance rule applied once.
- **Contract:** [entry-plan-contract.md](../design/contracts/entry-plan-contract.md) (Deterministic tactic compilation; Executable static feasibility).
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/trading/entry_plan_compiler.py` `compile_entry_plan`; caller `core/aletheia/agent_shell/entry_planner.py`; tests `tests/specs/spec_entry_plan_contract.py`, `tests/test_entry_plan_min_stop.py`.
- **L4 criterion:** n/a at target L3.

#### CAP-EPLAN-02 — Risk-budget sizing

- **Outcome:** target notional equals the loss budget divided by stop distance,
  bounded by venue minimum and maximum notional, and Risk recomputes the same
  result from the same facts.
- **Contract:** [entry_sizing_and_financing_contract.md](../design/contracts/entry_sizing_and_financing_contract.md) (Contract B — Adaptive Sizing; Risk convergence).
- **Target (proposed):** L4.
- **Probes:** code `core/aletheia/trading/entry_sizing.py` `resolve_entry_sizing`; callers `core/aletheia/trading/entry_plan_compiler.py` and `core/aletheia/risk/engine.py`; test `tests/test_entry_sizing.py`.
- **L4 criterion:** over at least 30 filled entries on one exact deployed commit,
  every realized stop-out loss is within 10% of its planned loss budget after
  fees, and no Risk recomputation drift is recorded; owner accepts.

#### CAP-EPLAN-03 — Minimal safe leverage and financing

- **Outcome:** the plan uses the lowest leverage that finances its notional
  within per-asset caps and current venue margin tiers, in isolated margin, with
  the stop proven to trigger before liquidation.
- **Contract:** [entry_sizing_and_financing_contract.md](../design/contracts/entry_sizing_and_financing_contract.md) (Contract C; Margin mode and liquidation safety).
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/trading/leverage_resolution.py` `resolve_execution_financing`; callers `core/aletheia/trading/entry_plan_compiler.py` and `core/aletheia/risk/engine.py`; test `tests/test_leverage_resolution.py`.
- **L4 criterion:** n/a at target L3.

### TRIG — Trigger

#### CAP-TRIG-01 — Plan monitoring with authority re-check

- **Outcome:** every open Entry Plan is watched on live market data; integrity,
  risk-policy authority, price invalidation and expiry are re-checked, and each
  plan ends with a typed terminal reason.
- **Contract:** [entry-plan-contract.md](../design/contracts/entry-plan-contract.md) (Trigger activation authority; Pre-broker submission authority).
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/agent_runtime/fast_trigger_watch.py` `_load_entry_plan_watchers`; caller `_watch_loop` in the same module, started by `start_fast_trigger_watch`; test `tests/test_universe_fast_trigger.py`.
- **L4 criterion:** n/a at target L3.

#### CAP-TRIG-02 — Capture tactics and conditional activation

- **Outcome:** an activated plan is captured by its compiled tactic (passive
  Post-Only with a bounded lifetime, then at most one bounded IOC), and
  conditional triggers activate only through a recorded activation.
- **Contract:** [entry-plan-contract.md](../design/contracts/entry-plan-contract.md) (Maker lifetime; Capture compilation contract; Trigger activation authority).
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/execution/entry_plan_execution.py` `execute_entry_plan`; caller `core/aletheia/agent_runtime/fast_trigger_watch.py`; tests `tests/test_entry_plan_execution.py`, `tests/test_entry_activation.py`.
- **L4 criterion:** n/a at target L3.

### PROP — Proposal

#### CAP-PROP-01 — Immutable per-leg order intent

- **Outcome:** each plan leg produces exactly one immutable native Proposal, and
  the plan's terminal status truthfully reflects what its legs did (filled,
  failed, expired).
- **Contract:** [entry-plan-contract.md](../design/contracts/entry-plan-contract.md) (Plan authority and leg accounting); [execution_contract.md](../design/contracts/execution_contract.md) §1.
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/execution/entry_plan_execution.py` `build_entry_plan_order_intent`; caller `execute_entry_plan` in the same module; test `tests/test_entry_plan_execution.py`.
- **L4 criterion:** n/a at target L3.

#### CAP-PROP-02 — Proposal ancestry and frozen hypothesis

- **Outcome:** every native Proposal, and the Position it opens, retains its
  opportunity/Candidate identity and a frozen position hypothesis, so outcomes
  can be attributed back to the decision.
- **Contract:** [ACTIVE_RUNTIME_SCOPE.md](ACTIVE_RUNTIME_SCOPE.md) (Enforced boundaries: ancestry); [profit_loop_observability_contract.md](../design/contracts/profit_loop_observability_contract.md).
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/storage/proposal_hypothesis.py` `freeze_position_hypothesis`; caller `core/aletheia/storage/repositories.py`; test `tests/test_proposal_hypothesis.py`.
- **L4 criterion:** n/a at target L3.

### RISK — Risk

#### CAP-RISK-01 — Single authoritative evaluation

- **Outcome:** every Proposal is evaluated once per stage (preflight, then
  authoritative after venue normalization) under a serial lock and a recorded
  policy identity, returning APPROVED, DEFER or REJECT before any order exists.
- **Contract:** [risk_engine_contract.md](../design/contracts/risk_engine_contract.md) §1–§3.
- **Target (proposed):** L4.
- **Probes:** code `core/aletheia/risk/engine.py` `evaluate_proposal`; caller `core/aletheia/execution/gateway.py` `execute_existing_proposal`; test `tests/test_risk_engine.py`.
- **L4 criterion:** on one exact deployed commit, at least 100 authoritative
  decisions with zero orders lacking an approving decision, zero decisions
  without policy identity, and every REJECT/DEFER carrying a typed reason; owner
  accepts.

#### CAP-RISK-02 — Entry Plan authority re-verification

- **Outcome:** at submission Risk re-verifies the plan it came from (complete
  geometry, single-trade risk, financing and sizing recomputation) using one
  implementation per check, so a compiled plan is never rejected by a duplicate
  rule.
- **Contract:** [risk_engine_contract.md](../design/contracts/risk_engine_contract.md) §2 (#1a, #2, #13, #20–#22); [entry_sizing_and_financing_contract.md](../design/contracts/entry_sizing_and_financing_contract.md) (Risk convergence).
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/risk/engine.py` `_entry_plan_authority_violations`, `_entry_plan_financing_violations`; caller `evaluate_proposal` in the same module; tests `tests/test_entry_plan_aggregate_risk.py`, `tests/test_entry_plan_execution.py`.
- **L4 criterion:** n/a at target L3.

#### CAP-RISK-03 — Account-level guards per mode

- **Outcome:** kill switch, daily loss, total exposure, drawdown scaling and
  margin occupancy are evaluated against the selected account, with each guard's
  on/off state explicit per execution mode.
- **Contract:** [risk_engine_contract.md](../design/contracts/risk_engine_contract.md) §2, §4; [kill_conditions_contract.md](../design/contracts/kill_conditions_contract.md).
- **Target (proposed):** L3.
- **Probes:** code: the `kill_switch_active`, daily-loss, total-exposure and drawdown checks inside `evaluate_proposal` in `core/aletheia/risk/engine.py` (daily loss trips `core/aletheia/risk/kill_switch.py` `activate`); caller `core/aletheia/execution/gateway.py`; test `tests/test_risk_engine.py`.
- **L4 criterion:** n/a at target L3.

### EXEC — Execution

#### CAP-EXEC-01 — Venue submission to verified entry fill

- **Outcome:** an approved Proposal becomes exactly one venue order on the
  correct account identity, and proven private fills materialize one Position
  with no duplicate, overfill or fabricated state.
- **Contract:** [execution_contract.md](../design/contracts/execution_contract.md) §1, §3, §5.
- **Target (proposed):** L4.
- **Probes:** code `core/aletheia/execution/gateway.py` `execute_existing_proposal`; caller `core/aletheia/execution/entry_plan_execution.py` `execute_entry_plan`; tests `tests/e2e/test_pipeline_exchange_entry_to_position.py`, `tests/test_exchange_submission_integrity.py`.
- **L4 criterion:** on one exact deployed commit, at least 30 consecutive
  approved entries reach a venue order with a terminal state (filled, cancelled
  or rejected with a venue reason), zero `command_not_sent` without a typed
  cause, and every fill reconciles to one Position; owner accepts.

#### CAP-EXEC-02 — Exchange-native protection

- **Outcome:** every entry carries a venue-side stop-loss (and take-profit when
  planned), confirmed by the entry acknowledgement; read-back is bookkeeping
  only, and native triggers are recognized as fills.
- **Contract:** [execution_contract.md](../design/contracts/execution_contract.md) §4, §10.4.
- **Target (proposed):** L4.
- **Probes:** code `core/aletheia/execution/stops.py` `native_tpsl_enabled`, `core/aletheia/portfolio/native_protection.py` `verify_native_protection`; callers `core/aletheia/execution/gateway.py` (passes the flag into `coordinator.execute_order`) and the `tpsl_fill_check` job in `core/aletheia/agent_runtime/scheduler.py`; tests `tests/test_tpsl_native_protection_check.py`, `tests/specs/spec_pipeline_bitget_preset_protection.py`.
- **L4 criterion:** on one exact deployed commit, at least 30 consecutive filled
  entries each have a confirmed venue stop id, and every stop-out among them is
  recognized as a native-trigger fill; owner accepts.

#### CAP-EXEC-03 — Bounded exit from every non-terminal order

- **Outcome:** reconciliation resolves unresolved orders from exact venue
  evidence, and every non-terminal entry order reaches a terminal state within a
  bound, without fabricating cancellation from absence or outage.
- **Contract:** [execution_contract.md](../design/contracts/execution_contract.md) §6, §6.1.
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/execution/reconciliation.py` `reconcile`, `core/aletheia/execution/stale_order_cleanup.py` `cleanup_stale_orphan_orders`; caller `core/aletheia/agent_runtime/scheduler.py`; tests `tests/test_reconciliation.py`, `tests/test_stale_order_cleanup.py`.
- **L4 criterion:** n/a at target L3.

### POS — Position management

#### CAP-POS-01 — Deterministic exit-plan enforcement

- **Outcome:** every open Position's ExitPlan (stop, target, horizon, hard time
  cap, catastrophic stop) is enforced on a fixed loop independent of the model,
  pause state or policy edits made after entry.
- **Contract:** [execution_contract.md](../design/contracts/execution_contract.md) §10.1, §10.3.
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/portfolio/position_manager.py` `evaluate_open_positions`; caller `core/aletheia/agent_runtime/scheduler.py` (`position_manager` job); tests `tests/test_position_manager.py`, `tests/test_horizon_exit.py`.
- **L4 criterion:** n/a at target L3.

#### CAP-POS-02 — AI position review with new instructions

- **Outcome:** each open Position is periodically re-reviewed by the model with
  enough context to act, and each review yields an auditable instruction (hold,
  adjust, close) whose effect on the ExitPlan is recorded once.
- **Contract:** [agent_contracts.md](../design/contracts/agent_contracts.md) (B. Trading Agents); [execution_contract.md](../design/contracts/execution_contract.md) §10.
- **Target (proposed):** L4.
- **Probes:** code `core/aletheia/agent_shell/commands.py` `review_portfolio_state`; caller `core/aletheia/agent_runtime/pi_daemon.py`; tests `tests/test_pi_daemon.py`, `tests/test_agent_shell.py`.
- **L4 criterion:** on one exact deployed commit, at least 30 Positions are each
  reviewed at least once before exit, every instruction has a recorded ExitPlan
  revision, and management actions are attributable in the outcome; owner
  accepts.

#### CAP-POS-03 — Protection changes reach the venue

- **Outcome:** a tightened or trailing stop chosen by the system or the model is
  applied on the venue, or the change is reported as not applied; local and venue
  protection never silently diverge.
- **Contract:** [execution_contract.md](../design/contracts/execution_contract.md) §4.
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/portfolio/position_manager.py` `apply_operator_adjustment`, `core/aletheia/execution/stops.py` `replace_exchange_protective_stop`; caller `core/aletheia/agent_shell/commands.py`; test `tests/test_adjust_position.py`.
- **L4 criterion:** n/a at target L3.

### CLOSE — Close and settlement

#### CAP-CLOSE-01 — Single routed close with bounded claims

- **Outcome:** every exit request (stop, target, model, human, time cap) goes
  through one close router; each close claim has a bounded number of attempts and
  ends resolved or escalated to a human tool.
- **Contract:** [execution_contract.md](../design/contracts/execution_contract.md) §10.2.
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/portfolio/position_manager.py` `close_position_routed`; callers `core/aletheia/agent_shell/commands.py` and `evaluate_open_positions`; tests `tests/specs/spec_pipeline_position_manager.py`, `tests/e2e/test_pipeline_exchange_position_to_close.py`.
- **L4 criterion:** n/a at target L3.

#### CAP-CLOSE-02 — Verified close from venue evidence

- **Outcome:** every close, including native-trigger fills and disappearance,
  materializes from venue fills or a proven absence, with its exit reason; no
  fill or PnL is invented, and strategy exits are not misfiled as administrative.
- **Contract:** [execution_contract.md](../design/contracts/execution_contract.md) §11.1–§11.4.
- **Target (proposed):** L4.
- **Probes:** code `core/aletheia/portfolio/outcome_closure.py` `close_position_verified`; callers `close_position_from_verified_exit` in the same module (from `core/aletheia/portfolio/disappearance_settlement.py`); tests `tests/test_outcome_closure.py`, `tests/e2e/test_outcome_integrity_full_lifecycle.py`.
- **L4 criterion:** on one exact deployed commit, at least 30 consecutive closes
  each have a broker-fill exit price and a correct strategy/administrative class,
  with zero orphaned disappearance candidates; owner accepts.

#### CAP-CLOSE-03 — Complete cost settlement

- **Outcome:** each closed Position carries entry fee, exit fee, funding and
  realized slippage from provable sources, or is marked incomplete; cost
  completeness is separate from exposure closure.
- **Contract:** [execution_contract.md](../design/contracts/execution_contract.md) §11.4, §11.6.
- **Target (proposed):** L4.
- **Probes:** code `core/aletheia/portfolio/broker_funding_settlement.py` `settle_unsettled_exchange_closes`; caller `core/aletheia/agent_runtime/scheduler.py`; test `tests/test_broker_funding_economics.py`.
- **L4 criterion:** on one exact deployed commit, at least 30 consecutive closes
  have all four cost components populated from venue sources; owner accepts.

### OUT — Outcome

#### CAP-OUT-01 — Verified net outcome and owner metrics

- **Outcome:** every eligible close yields one cost-complete net PnL, and the
  owner's metrics (win rate = share of Positions with net PnL > 0, net
  expectancy, drawdown on equity) are computed from one complete, consistent
  cohort per execution mode.
- **Contract:** [execution_contract.md](../design/contracts/execution_contract.md) §11.4; [profit_loop_observability_contract.md](../design/contracts/profit_loop_observability_contract.md); [live_unlock_contract.md](../design/contracts/live_unlock_contract.md) for the readiness reader.
- **Target (proposed):** L4.
- **Probes:** code `core/aletheia/analytics/outcome_integrity.py` `eligible_strategy_outcomes`, `read_complete_outcome_cohort`; callers `core/aletheia/analytics/family_scorecard.py` and `core/aletheia/analytics/go_live_scorecard.py`; tests `tests/specs/spec_outcome_integrity.py`, `tests/test_go_live_scorecard_evidence.py`.
- **L4 criterion:** at least 100 eligible closes on the active route, with the net
  expectancy's 95% lower bound above zero and every owner surface reporting the
  same numbers; owner accepts.

#### CAP-OUT-02 — Per-trade R and path attribution

- **Outcome:** each eligible Position has a system-computed net R, MAE/MFE from a
  retained price path, and attribution of the result to entry, management, exit
  and cost.
- **Contract:** [profit_loop_observability_contract.md](../design/contracts/profit_loop_observability_contract.md); [profit_gradient_evidence_contract.md](../design/contracts/profit_gradient_evidence_contract.md).
- **Target (proposed):** L3.
- **Probes:** code `core/aletheia/analytics/entry_exit_geometry.py` `build_entry_exit_geometry_audit`, `core/aletheia/portfolio/position_path_observer.py` `observe_open_position_paths`; callers `core/aletheia/cli/opportunity.py` and the `position_path_observer` job; tests `tests/test_entry_exit_geometry.py`, `tests/test_position_path_observer.py`.
- **L4 criterion:** n/a at target L3.

### LEARN — Review and learning

#### CAP-LEARN-01 — Post-trade review

- **Outcome:** every eligible close gets a deterministic case and a review linked
  to its Position, distinguishing strategy losses from administrative closes.
- **Contract:** [learning_contract.md](../design/contracts/learning_contract.md) §1.1.
- **Target (proposed):** L3.
- **Probes:** code `extensions/research/post_trade_reviewer.py` `run_post_trade_reviewer`; caller `core/aletheia/agent_runtime/scheduler.py` (`post_trade_reviewer` job); test `tests/test_post_trade_reviewer_outcomes.py`.
- **L4 criterion:** n/a at target L3.

#### CAP-LEARN-02 — Evidence-gated lesson promotion

- **Outcome:** proposed lessons are reviewed fairly (no queue starvation) and
  promoted or overturned only by narrow-scope net evidence, with expiry.
- **Contract:** [learning_contract.md](../design/contracts/learning_contract.md) §2 (LessonStore).
- **Target (proposed):** L3.
- **Probes:** code `extensions/research/lesson_reviewer.py` `run_lesson_reviewer`; caller `core/aletheia/agent_runtime/scheduler.py`; test `tests/specs/spec_learning_pipeline.py`.
- **L4 criterion:** n/a at target L3.

#### CAP-LEARN-03 — Owner-gated influence on the next decision

- **Outcome:** an accepted learning result changes a later selection, planning
  or risk decision only after owner trial admission, through the declared
  authority gate, with a neutral fallback and recorded exposure.
- **Contract:** [ACTIVE_RUNTIME_SCOPE.md](ACTIVE_RUNTIME_SCOPE.md) (`learning_authority_gate`); [learning_contract.md](../design/contracts/learning_contract.md) §1.2.
- **Target (proposed):** L2.
- **Probes:** code `extensions/learning/experiments.py` `apply_numeric_treatment`, `core/aletheia/trading/entry_learning.py` `resolve_entry_learning_context`; callers `core/aletheia/trading/entry_analysis.py` and `extensions/knowledge/guards.py`; tests `tests/test_entry_learning_boundary.py`, `tests/test_observation_canonical_bridge.py`.
- **L4 criterion:** n/a at target L2.

## Retired IDs

None.
