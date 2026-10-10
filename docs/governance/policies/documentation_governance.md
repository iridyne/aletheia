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
| Spec (formerly Proposal) | What substantial change is approved or being considered, and how is it verified? | `docs/design/specs/` (format: [specs README](../../design/specs/README.md)); legacy `docs/design/proposals/` until revised |
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

Since 2026-10-10 a new substantial design is written as a Spec in
`docs/design/specs/NNNN-name/` using the fixed SDD format in the
[specs README](../../design/specs/README.md). The threshold below is unchanged; the Spec
lifecycle is `draft`, `approved`, `implemented`, `superseded`, `rejected`. A legacy
Proposal that is revised moves into that format; one that is replaced is marked `superseded`.


A Proposal is an optional communication tool, never a precondition. No change of
any depth requires one before it lands; what a change requires is that its gates
go green. Write one when a design is large enough that the reasoning will not
survive in a PR body — a major new capability, a cross-domain change, a
trust-boundary change — and skip it otherwise.

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

## Change Contract and SDD depth

A GitHub Issue is the default carrier for every change. It expresses why the
change is necessary, what the current system state is, what direct dependencies
materially gate it, and how completion is verified. It must not duplicate or
replace the canonical Contract.

### Issue-body discipline

An Issue is a **change contract, not a delivery roadmap**.

A well-scoped Issue should normally contain only:

- the concrete problem, violated invariant, or value;
- current code/runtime/data evidence;
- direct dependencies or activation conditions;
- the approved behavior delta or target invariant;
- hard non-goals / authority boundaries;
- executable and, when required, runtime/economic verification;
- a closure condition.

Issue bodies must **not** maintain numbered implementation stages, a prescribed
work order, a "current stage", or a future delivery sequence. If one change
depends on another, state the dependency directly (`A requires B`) rather than
turning the relationship into a roadmap.

Future ideas remain deferred until they are independently actionable. Open a
focused child Issue only when its concrete problem, evidence, authority and
acceptance can stand on their own. Umbrella Issues may name related capabilities
or possible follow-ups, but those lists do not imply execution order or active
work.

This rule also applies to closed Issues: once implementation is complete, keep
the durable final contract/result and remove stale development sequencing that
could cause a later Agent to restart obsolete work.

Every change has an SDD depth, independent of the A/B/C task-routing
classification:

| Depth | When | Contract Delta |
|---|---|---|
| D0 — Restore truth (Conformance Repair) | Existing Contract / invariant already defines required behavior; current code, docs, tests, or runtime is non-conformant | `NONE` — reference the existing Contract / invariant being repaired |
| D1 — Change Semantics | Behavior semantics, state transitions, API/schema, or result meaning changes within existing ownership | Explicit `ADDED / MODIFIED / REMOVED` semantic delta |
| D2 — Change Structure | State ownership, long-lived mainline, subsystem topology, authority, or product scope changes | Explicit `ADDED / MODIFIED / REMOVED` semantic delta, plus the gates that hold the new structure |

SDD depth is a semantic classifier only; it does not replace owner approval for
a new D1/D2 decision. [Root AGENTS.md](../../../AGENTS.md#bounded-engineering-delegation)
is the single development-delegation authority: the owner selects outcomes and
material decisions; the AI derives technical root cause, relevant boundary cases,
implementation and verification within the authorized task. An already accepted
delta does not need repeated approval per commit or checkpoint. Recording a task
in an Issue preserves existing authorization; it does not create new authority.

**Contract update rule.** Code changing does not require a Contract change.
Update a Contract only when an approved D1/D2 change is accepted.
A D0 repair that brings code back into conformance with an existing Contract
MUST NOT modify that Contract. Internal refactors, algorithm replacements,
race-condition repairs, and performance optimizations do not change Contracts.
D1 and D2 may update Contract and executable SpecClaims as part of the accepted
change. No depth requires a Proposal document first; see
[2026-09-26 — 提案文档不再是任何改动的前置条件](../decisions/DECISIONS.md).

### D0 / D1 / D2 walkthrough examples

- **D0 — stale tests:** [PR #2999](https://github.com/iridyne/aletheia/pull/2999)
  realigned specs after the approved cutover without changing production code. Inside
  that approved scope, AI may repair conformance with Delta `NONE`, without a new decision.
- **D0 — externally visible repair:** [PR #3003](https://github.com/iridyne/aletheia/pull/3003)
  restored Entry monitoring liveness after an ordering fault, retaining the watcher as
  lifecycle owner and requiring fresh evidence. Observable recovery is not new semantics.
- **D1 — new lifecycle rule:** [#2720](https://github.com/iridyne/aletheia/issues/2720)
  specifies planner-version-based generation supersession within the existing owner.
  That new reuse/terminal rule needs owner acceptance; implementation commits do not
  each need reapproval.
- **Investigation and D1/D2 boundary:** [#3009](https://github.com/iridyne/aletheia/issues/3009)
  records silence after compiler refusal, not a proven recovery policy. If investigation
  requires a new retry rule within existing ownership, stop for D1 acceptance; if it
  requires a new durable recovery owner/mainline, stop for a D2 owner decision.
  The symptom and Issue do not authorize either implementation or a Gate B promotion.
- **D2 — authority convergence:** [#2299](https://github.com/iridyne/aletheia/issues/2299)
  makes UTA the sole private external-account boundary and assigns coordinator/UTA/storage
  responsibilities. This structural direction requires an owner decision, not autonomous
  selection disguised as mechanical cleanup.

These are classification walkthroughs, not authorization to reopen or implement the
referenced work; the human decision boundary is inherited from root `AGENTS.md`.

**Convergence.** After implementation, verify:

1. Does the implementation satisfy the Change Contract in the Issue?
2. Do executable tests prove the changed invariants?
3. Does the current Contract need to change?  If yes, are executable SpecClaims
   updated in the same change?
4. Does Product Capability Ground Truth need to change?  Update it only if a
   production capability genuinely changed — not merely because a test passes.
5. Is runtime or economic qualification required, or is repository-level
   conformance sufficient?

The separation between executable SpecClaim conformance and product capability
support is absolute. `SpecClaim.status = "implemented"` means that individual
executable claim conforms; it does not mean the capability is implemented or
supported. Product Capability Ground Truth is the sole authority for product
support status. A passing test suite without a production consumer, runtime
path, and economic outcome is not a product capability.

## Authority and conflict resolution

First identify the question; different kinds of truth are not interchangeable:

| Question | Canonical authority / evidence |
|---|---|
| What development work is authorized, and who decides? | The owner's explicit task/accepted decision, bounded by [root AGENTS.md](../../../AGENTS.md), safety policies and applicable review/release rules |
| Which trading route is admitted now? | [ACTIVE_RUNTIME_SCOPE.md](../../product/ACTIVE_RUNTIME_SCOPE.md), the single machine-readable active-admission declaration |
| What capabilities are currently promised, frozen or optional? | [CAPABILITY_GROUND_TRUTH.md](../../product/CAPABILITY_GROUND_TRUTH.md) |
| What behavior is approved? | The narrowest current Contract; accepted Decisions explain why |
| What exists and works in this code revision? | Current source and applicable executable evidence, with their actual limitations |
| What is deployed or economically qualified? | Exact-runtime identity, observations and required external/economic evidence, not repository status |
| What work is open, reviewed or closed? | GitHub Issues/PRs; their status is not implementation, deployment or qualification proof |

Within the same subject, repository governance bounds Product Truth, current
Contracts, accepted Decisions, Operations, Proposals and finally historical
references. The active admission and capability declarations narrow broader
`DIRECTION.md`, `SCOPE.md`, architecture overviews and inventories; those broader
documents must link to, not expand, current support. README files are routers,
not competing policy or capability registries.

Use code/tests/data/runtime to verify factual claims, not to authorize changed
semantics. If code disagrees with an approved Contract, record a conformance gap;
do not weaken the Contract to match a bug. If an overview or README disagrees
with a current authority, correct the overview. If current authorities genuinely
conflict and accepted owner decisions/current evidence cannot resolve them,
escalate that specific decision under root AGENTS.md. Keep historical evidence
intact with its original scope. Closure of an old task neither proves all its
claims nor automatically authorizes restarting it.

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
6. run `uv run python scripts/ci/check_docs.py`.
