---
id: canonical-entry-lifecycle
status: accepted
github_issue: 2851
related_contracts:
  - ../contracts/entry-plan-contract.md
  - ../contracts/decision_lifecycle_contract.md
  - ../contracts/learning_contract.md
supersedes: []
---

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
`Paper` and `Full` may use `paper_internal`; only `Full` may use a registered and
Entry-supported exchange Demo route. `Observe`, unknown routes, and effective
Live remain fail-closed. Internal Paper plans persist minimum permission `Paper`;
Demo plans persist the minimum required by their registered route. Planner
preflight, post-inference validation, Fast Monitoring, and Execution consume the
same result while Risk and Gateway retain exclusive approval/submission authority.

## Compatibility, rollback, and non-goals

Legacy rows, JSON, hashes, and provenance are never rewritten. A narrow reader
classifies historical formats, including false
`AI_ENTRY_RUNTIME_UNSUPPORTED / no_plan`, without granting current execution
authority or changing Learning evidence. Rollback stops new cycle creation via
existing pause authority and rolls code back while retaining every new row.

This design adds no V5 label, feature flag, Candidate/Plan ledger, scheduler,
Risk owner, Gateway, exchange, Live authority, strategy economics, synthetic
Trigger, or cooldown redesign.
