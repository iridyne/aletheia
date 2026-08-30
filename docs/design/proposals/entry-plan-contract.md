---
id: entry-plan-contract
status: implemented
github_issue: 2288
related_contracts: [decision_lifecycle_contract.md, execution_contract.md]
supersedes: [universe_fast_trigger_watch.md]
---

# Entry Plan Contract (D2)

## Decision

Universe-linked entry converges on the owner-facing lifecycle **Candidate →
Entry Plan → Execution → Order → Fill → Position**. Trigger is not a universal
stage: it exists only inside a `TRIGGERED` Entry Plan. A `RESTING_MAKER` plan may
reserve Risk and place a true Post-Only Order immediately.

This proposal records the accepted direction for #2288. The normative behavior
now lives in `docs/design/contracts/decision_lifecycle_contract.md` and
`docs/design/contracts/execution_contract.md`.

## Accepted 2026-08-30 conformance amendment

Issue #2546 completes the accepted design without widening model, Risk, or
Gateway authority:

- execution eligibility requires a Fast Monitoring path for the Candidate's
  exact physical venue, symbol, and market type;
- `PlanIntent` selects one of Resting Maker, Triggered Maker, or Triggered
  Taker, while deterministic code retains every numeric execution and Risk
  value;
- every new Post-Only Order has a fixed 60-second cancel-only lifetime measured
  from its durable Order creation time;
- TriggerSpec compatibility is registry-owned and deterministic incompatibility
  is terminal after one physical model attempt; and
- Entry Planner admission and inference resolve the same configured deep-tier
  provider identity.

The owner accepted this D2 amendment before its normative content entered the
current Contract. The amendment adds no venue, exchange adapter, persistence
surface, live authority, Risk policy, or wire-version migration.

## Implemented boundaries

- A durable Candidate Planning Generation owns one reasoning request across
  snapshot, rank, score, price and Candidate-row churn. Duplicate wakes coalesce;
  durable No-Plan does not reopen on heartbeat; only incompatible thesis facts or
  explicit manual replan create a new generation.
- The on-demand Entry Planner reuses PI daemon scheduling. Each cycle uses 3 to
  15 worker slots to claim a rank-ordered batch concurrently; actual model work
  never exceeds eligible durable generations. Every result remains compare-and-set
  against its claimed generation, so stale results cannot activate a plan.
- `EntryPlan` is a generic immutable domain model. Momentum breakout, pullback
  and reclaim values live only in registered `TriggerSpec.params`.
- `RESTING_MAKER` and Triggered Maker require venue Post-Only semantics.
  Urgent execution is an explicitly authorized, opposing-BBO IOC Limit within a
  bounded price envelope. A plain Limit is never called Maker.
- V1 exchange execution capability is Bitget Demo only. `paper_internal` remains
  useful for deterministic lifecycle and Risk tests but does not claim queueing
  or maker-fill fidelity.
- Fast Monitoring consumes public exact market events and decides trigger,
  invalidation and expiry. It requests cancel through Gateway; it never calls an
  exchange client directly. Private Fill evidence wins a cancel race.
- Canonical Risk remains the only approval and reservation authority. There is
  no FastRisk, TriggerRisk or second Agent scheduler.
- Storage migration is additive: `candidate_planning_generations` and
  `entry_plans` are the single active planning/plan ledgers. Existing
  `universe_setups`, Opportunity, PiDecision and Proposal fields remain only for
  historical rows, independent producers, wire compatibility and recovery.

## Compatibility and non-goals

The migration does not destructively rename historical SQLite tables or rewrite
old rows. The old Momentum Setup adapter may read historical setup rows only
when no current Entry Plan owns the exact instrument and direction; it cannot
become a second owner beside a current plan.

#2288 does not tune Trigger economics, prove profitability, add venues or market
data, expand Learning/Profit authority, build a strategy DSL, matching engine,
second Risk Engine, second scheduler or HFT infrastructure.

## Resolution

Implemented atomically with #2288. Any wording in the Issue body or earlier
comments that conflicts with the 2026-08-27 Implementation Review Amendment is
superseded by that amendment and the normative contracts above.
