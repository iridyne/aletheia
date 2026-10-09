---
id: canonical-entry-lifecycle
status: implemented
github_issue: 2851
related_contracts:
  - ../contracts/entry-plan-contract.md
  - ../contracts/decision_lifecycle_contract.md
  - ../contracts/learning_contract.md
supersedes: []
---

> 2026-10-09 状态改为 implemented：#2851 已关闭；规范内容在 docs/design/contracts/entry-plan-contract.md 的 Canonical Entry hypothesis authority 一节。

# Canonical Entry Lifecycle Proposal

> Owner-accepted design from Issue #2851. Acceptance is not an implementation,
> deployment, qualification, or profitability claim.

## Decision

Candidate planning has one current Grounded Entry implementation. Source history
is identified by Git provenance, machine compatibility by automatically derived
schema and behavior fingerprints, and repeated attempts for one stable planning
thread by an immutable `cycle_number`. Historical numeric format labels remain
read-only compatibility data and do not select current Planner, Compiler,
Execution, Learning, API, or Workbench behavior.

The existing `candidate_planning_generations` and `entry_plans` tables remain the
only planning ledgers. A generation records its cycle lineage, reached stage,
model and Compiler invocation facts, and a typed outcome. Runtime/configuration
blocks resume the same cycle without consuming a model attempt; only a validated
economic refusal is `NO_PLAN`. A later completed Universe requalification may
atomically create one successor cycle after Plan expiry when no unresolved
Order, Fill, reconciliation ambiguity, or Position remains.

## Runtime authority

One resolver separates worker capability from the configured execution route.
The current Entry-supported route is `bitget_demo`, with minimum permission
`Full`. `Observe` and `Paper` cannot execute it; unknown, retired and effective
Live routes remain unavailable. Planner preflight, post-inference validation,
Fast Monitoring and Execution consume the same result while Risk and Gateway
retain exclusive approval/submission authority. The former local-simulator
permission path is [archived](../history/local_simulated_risk_execution.md).

## Compatibility, rollback, and non-goals

Legacy rows, JSON, hashes, and provenance are never rewritten. A narrow reader
classifies historical formats, including false
`AI_ENTRY_RUNTIME_UNSUPPORTED / no_plan`, without granting current execution
authority or changing Learning evidence. Rollback stops new cycle creation via
existing pause authority and rolls code back while retaining every new row.

This design adds no V5 label, feature flag, Candidate/Plan ledger, scheduler,
Risk owner, Gateway, exchange, Live authority, strategy economics, synthetic
Trigger, or cooldown redesign.

## Accepted single-mainline convergence: entry implementation

The owner-approved convergence extends the same durable Candidate admission to
Serenity research. Universe and Serenity publish source-scoped snapshots and
freeze real source kind/ID/trade_school, evidence hash and cutoff in planning
context at birth; renewals do not rewrite that identity. Exact venue, symbol and
physical market retain one global current owner; refresh cannot evict another
producer, and an unresolved order prevents transfer of ownership.

Serenity reuses grounded price-action timing, not a second evaluator or a new
catalyst profile. EntryPlan freezes source kind, source ID and trade school.
Timing remains bounded to the existing intraday/swing windows; the separate
holding horizon preserves Serenity long-term research and thesis exits. The
Proposal.expected_horizon_hours inherits holding_horizon_hours (Serenity
max_hold_days×24; short_term copies timing), and source/school remain in Plan and
Proposal attribution. Plan entry expiry remains 60/240 minutes, distinct from
holding and actual maker Order timeout. Risk verifies these identities against
the immutable Plan. Structural stops and actual-stop risk sizing remain intact.

Legacy Opportunity/PaperTrader pending admission, TradingDecision entry
evaluation, PiDecision entry claims, Setup authorization/materialization and
pending defer recovery no longer own current entry. Shared position actions and
audit remain separate. Historical facts remain readable. Pending
work is retired with explicit evidence, never converted to a fabricated Plan;
unknown submissions retain Gateway reconciliation rather than a false terminal
state. Current analytics expose candidate_attention, entry_plans and planning_history
from the workbench, not old queues; ranker/funnel cohorts remain read-only history.

## Approved future portions (not delivered by this entry cutover)

The same approved design will converge external private accounts on UTA/coordinator,
Learning on the one-way proven Lab migration, and Linux source deployment on
the existing auto transaction. It adds no coordinator framework, source plugin
registry, second queue or migration database. Each boundary is verified before
the next cutover. Production deployment and qualification remain separately
authorized, including same-SHA standard-directory migration before experimental
runtime-registry retirement. Acceptance does not claim any of these cutovers
has been deployed.
