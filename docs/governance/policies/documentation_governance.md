# Documentation Governance

## Purpose

Aletheia documentation preserves knowledge whose useful lifetime is longer
than one task, branch, incident, or release cycle. It reduces ambiguity without
becoming another stateful project-management system.

GitHub Issues and PRs are the sole sources for ownership, priority, progress,
review, and closure. Runtime state comes from `aletheia doctor`, logs, durable
data, and the running system.

## Six document roles

Every current document has exactly one primary role:

| Role | Question answered | Canonical location |
|---|---|---|
| Product Truth | What does the product promise and support? | `docs/product/` |
| Contract | What behavior is currently approved and observable? | `docs/design/contracts/` |
| Decision | Why was a long-lived choice made? | `docs/design/decisions/` |
| Proposal | What substantial future change is being considered? | `docs/design/proposals/` |
| Operations | How is the current system operated or recovered? | `docs/operations/` |
| Evidence / Archive | What was observed or believed at a point in time? | `docs/research/`, `docs/archive/`, and frozen legacy trees |

A document may link across roles but must not silently combine their authority.

## Stable-content rule

Current documentation may contain:

- durable product scope and support classifications;
- current behavioral invariants, interfaces, failure semantics, and observable results;
- accepted architectural decisions and substantial designs;
- repeatable operating and recovery procedures;
- frozen research or audit evidence with its original context.

Documentation must not contain live task ownership, priority, progress
percentages, mutable checklists, sprint state, worker leases, handoffs, current
issue state, or session/runtime snapshots presented as current truth.

An exception requires an explicit owner decision naming its reason, authority,
and exit condition. The exception does not become precedent. Once its active
use ends, preserve it as dated evidence or remove it from the current
documentation path.

## Proposal threshold and lifecycle

Create a Proposal only for:

1. a major new capability;
2. a cross-domain change; or
3. a trust-boundary change.

Ordinary bugs, narrow refactors, documentation corrections, and routine
operations changes use a GitHub Issue when tracking is needed and a PR for
review. They do not require a Proposal.

A Proposal records a stable design and one of these states:
`draft`, `accepted`, `rejected`, `implemented`, or `superseded`.
These states describe the design decision, not execution progress. Acceptance
does not claim implementation. While work is active, GitHub carries the owner,
priority, checklist, and PR links. When the complete behavior becomes current,
move its normative content into Product Truth, Contract, Decision, or
Operations documents and mark the Proposal `implemented`. Rejected or
superseded proposals remain immutable evidence.

## Contract boundary

A Contract describes only current approved behavior. It states the actual
entry point, durable state, consumer, observable result, invariants, and failure
semantics. A test or implementation fragment without a production consumer is
not contract evidence.

Future targets, unwired components, and desired end states belong in a
Proposal. Known mismatches belong in GitHub Issues. A Contract may link to them,
but it must not express target behavior as an existing guarantee.

Executable SDD claims are a separate conformance axis. Their historical
`implemented` value means the individual executable claim conforms; it does
not mean the product capability is implemented or supported. Product support
is determined only by Product Capability Ground Truth.

## Authority and conflict resolution

Use this order when documents conflict:

1. repository governance;
2. Product Truth;
3. current Contracts;
4. accepted Decisions;
5. current Operations procedures;
6. Proposals;
7. research and frozen historical evidence.

Code, executable tests, durable data, and current runtime facts determine
whether the documented statement remains true. When they disagree, correct
the appropriate authority and track any remaining repair in GitHub.

Each fact has one canonical owner document. Other documents link to it instead
of copying it. Do not add commit-SHA binding, broad metadata registries,
generated documentation sites, or a second issue database unless a concrete
failure demonstrates that the additional mechanism is necessary.

## Change checklist

Before adding or changing documentation:

1. identify its single role and canonical owner;
2. confirm the content will outlive the current task;
3. link rather than duplicate;
4. keep execution tracking in GitHub;
5. update the nearest routing README;
6. run `uv run python scripts/check_docs.py`.
