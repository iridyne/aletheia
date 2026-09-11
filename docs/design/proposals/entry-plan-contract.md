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
- the original PlanIntent tactic selection is superseded by the canonical
  `EntryHypothesis` profile/urgency contract under #2851; deterministic code
  retains every numeric execution and Risk value;
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
- V1 exchange execution capability is Bitget Demo only. Deterministic lifecycle
  and Risk tests use external-account fixtures and faked UTA responses; they do
  not require a local simulator or prove exchange queueing/maker-fill fidelity.
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

## Superseded 2026-09-02 AI Entry amendment (#2725)

New eligible Momentum Candidates use `entry-planner/v3` by default. Those
generations freeze `entry-analysis-context/v1`, accept only strict
`entry-hypothesis/v1` references, and compile deterministic `entry-plan/v2`
facts under `ai-entry-hypothesis-compiler/v1`. No independent feature flag,
activation timestamp, activation identity, fresh cohort, or economic experiment
governs this path. Historical V1 plans, Positions, non-Momentum planning, Risk,
Gateway, and Live authority remain unchanged.

The generic Universe Discovery Benchmark Observation Window continues across
unrelated Entry/Main Runtime deployments. Deployment does not wait for #2556;
only a change to that window's frozen scanner, allocator, outcome, or cost
semantics rotates its affected generation under its own contract.

The numeric V3 amendment is retained as historical rationale. The accepted
[canonical Entry lifecycle cutover](canonical_entry_lifecycle.md) supersedes its
runtime version selection without rewriting historical plans or evidence.

## Accepted 2026-09-05 planning recovery amendment (D2)

The approved Candidate lifecycle completion preserves implemented status and
historical decisions. Missing registry or frozen-context Profiles are runtime
blocks, never synthetic NO_PLAN. Dependency/broker releases refund only the
current pre-dispatch attempt and record unavailable without erasing invocation
facts. Cancellation after dispatch retains the attempt and uses bounded provider
defer with unknown dispatch; before dispatch it releases only the still-owned
claim under shield/CAS. Compiler geometry, causal generation rules, historical
JSON/hashes, Legacy qualification/owner admission, Risk and Gateway are unchanged.
No scheduler, activation gate or writable Opportunity entity is introduced.

## Accepted 2026-09-05 execution lineage amendment (D2)

The logical opportunity is a read-only view across existing attention, planning,
execution and economics owners, not a new identity, ledger or writable state.
Cycles retain their original Candidate/input/hash/Plan and follow only persisted
predecessor/association links. A shared batched snapshot read follows exact
Plan→Proposal/Order→Fill→Position and Position→close Order→close Fill relations.
Missing/conflicting historical links are unavailable, never inferred by symbol
or time. Candidate attention never owns an activated Plan, Order or Position.

Execution is projected separately from planning: not-started, working/cancel
pending, confirmed zero-fill termination, open/closing Position, closed pending
economics, closed, excluded or unavailable. Fill without Position is unavailable;
FILLED alone is not completion. Existing economics validators and settlement
facts prove cost-complete closure, including paper-account net cash consistency
within 1e-8. GET performs no settlement writes. Position realized economics and
exact Fill-T0 four-hour Entry outcomes remain distinct; Candidate forward
returns cannot substitute for either. Risk/Gateway and accounting permissions
remain unchanged. Existing detail/list consumers share this read owner.

## Accepted 2026-09-05 business progress amendment (D2)

Background health samples the canonical Candidate ledgers, never a qualification
evaluator or provider. Current frozen-epoch Twin outcome keys and exact Entry
Fill outcome keys include debt from previous processes, excluding other epochs.
Scanner pending Shadow capture is reconciled against its committed batch in the
read projection; health does not repair or rewrite either ledger.

The health job atomically replaces one in-process progress cache before health
evaluation. Workbench and health share it; the default 600-second cadence yields
a 1200-second TTL, derived from the actual job interval when overridden. Missing,
failed, expired, or unconfigured probes cannot establish healthy business state.
Disabled and paused modes remain explicit. Refresh failure/stall, ready planning
stall and overdue outcomes are alert-only, never restart or authority signals.
Normal empty selection, observe-only capability, future retries, natural Trigger
or Fill waits and terminal qualification capture remain normal explanations.
The normative clocks and projection fields are in the decision lifecycle contract.

## Accepted #3111 capture convergence amendment

The owner accepted serial implementation of the bounded capture model in
[#3111](https://github.com/iridyne/aletheia/issues/3111). It supersedes the universal
Maker timeout and direct urgency-to-tactic mapping above. Profile-owned Immediate
or Conditional activation is independent of PriceFirst, MakerPreferred or
FillFirst capture. One Plan retains aggregate authority; at most two immutable
Proposal/Risk/Order legs use the existing Gateway, claims and reconciliation.

The normative contract is [Entry Plan Contract](../contracts/entry-plan-contract.md).
Historical JSON/hashes remain immutable. Live, Alpha, Candidate selection, Risk
limits and accounting/cost integrity are unchanged. Deployment and natural
Bitget Demo qualification remain separate from implementation availability.
