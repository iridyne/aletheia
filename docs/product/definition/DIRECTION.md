# Aletheia Trading OS - Product Direction

> **Role:** L1 product direction. This document defines the durable direction
> of travel and the order in which Aletheia should earn greater capability.
> It is not a delivery plan, release schedule, implementation status report,
> or list of current defects.
>
> This is the single canonical product-definition document. For supported
> scope, see [Product Scope](../SCOPE.md) and
> [ACTIVE_RUNTIME_SCOPE](../ACTIVE_RUNTIME_SCOPE.md). Measurable admission and
> promotion decisions remain governed by
> [PHASE_GATES](../gates/PHASE_GATES.md) and
> [EXPERIMENT_PROTOCOL](../experiments/EXPERIMENT_PROTOCOL.md).
>
> Last updated: 2026-07-30.

---

## North star

Aletheia is becoming a personal, autonomous trading research and decision
system that can improve through experience without becoming opaque or unsafe.

The central product bet is that AI trading capability is constrained less by
model intelligence than by the quality of the apparatus around it: evidence,
feedback, controlled exposure, memory, and hard safety boundaries. Aletheia
exists to test that bet honestly and, if it holds, to let one owner delegate
the work of developing and operating a durable trading edge.

Its long-term advantage should not come from one model, indicator, strategy, or
market regime. It should come from a durable learning apparatus:

```text
observe -> reason -> act within limits -> measure -> learn -> improve
```

The loop matters only when each transition is attributable, economically
truthful, and capable of being challenged by later evidence. More activity,
more artifacts, and more automation are not progress by themselves.

## Definition of success

Aletheia succeeds as a product when it can demonstrate attributable
improvement while keeping failure survivable, authority bounded, and its
economic record trustworthy.

Its longer-term human outcome is a system that can produce durable economic
performance across changing market conditions within owner-defined risk
limits. Exact promotion thresholds and evaluation windows belong to the
current gates and experiment protocol, not to this direction document.

## Strategic sequence

The direction is an order of earned capabilities, not a calendar. Later
directions must not outrun the foundations beneath them.

### 1. Establish a trustworthy operating core

The system must first become dependable enough that its records can be treated
as evidence. Market identity, decisions, risk judgments, orders, fills,
positions, outcomes, and runtime actions must remain causally connected and
honest about uncertainty or failure.

The aim is not the absence of faults. It is a system whose faults are bounded,
visible, recoverable, and unable to silently rewrite financial or operational
truth.

### 2. Complete the evidence-to-learning loop

Aletheia must turn lived market experience into evidence that a later decision
can actually use. Outcomes need stable identity, economic completeness,
comparable cohorts, and explicit provenance. Learning must distinguish signal
from noise, preserve contrary evidence, and retire conclusions that no longer
hold.

The product should prefer an honest "not enough evidence" over premature
learning authority. A memory, review, verdict, or recommendation is an
intermediate artifact until its downstream contribution is demonstrated.

### 3. Prove improvement under controlled exposure

Once the loop is trustworthy, Aletheia should demonstrate that it makes later
decisions better across time and market conditions. Progress is measured by
attributable improvement against a credible baseline, not by retrospective
storytelling or isolated profit.

Experiments should remain bounded, reversible, and comparable across policy
epochs. Failure to find durable alpha is a valid result; it should narrow the
system's beliefs rather than encourage wider or less controlled searching.

### 4. Earn autonomy progressively

Autonomy should expand only after the preceding capability has been observed
to work. The system may move from recommendation, to bounded paper influence,
to supervised exchange interaction, and eventually to narrowly limited live
authority only through explicit human-controlled gates.

Greater autonomy never includes authority to redefine its own hard risk,
credential, audit, execution, or live-unlock boundaries. Risk Engine approval
and the Execution Gateway remain structural constraints, not temporary
scaffolding.

### 5. Broaden the system's field of understanding

After the core loop is proven, Aletheia may widen the evidence it can reason
about: more market structures, deeper real-world research, and additional
forms of portfolio context. Breadth should strengthen the same decision and
learning loop rather than create disconnected agents, pages, data collections,
or order paths.

Expansion is justified only when the new evidence has a named decision
consumer, a measurable contribution claim, and a safe failure mode.

## Enduring product boundaries

- Aletheia remains local-first and owner-controlled.
- Safety, auditability, and economic truth are prerequisites for learning, not
  competing priorities.
- The System Operator is the top-level decision role; supporting agents produce
  evidence and bounded assistance, not independent execution authority.
- Human ownership remains over capital exposure, hard safety boundaries,
  credentials, irreversible actions, and product direction.
- The product favors explicit contracts and simple infrastructure over breadth
  that cannot be operated or understood.
- Current support must never be inferred from this roadmap. Product Ground
  Truth, current contracts, executable evidence, and runtime facts determine
  what works today.

## Directional decision test

A substantial proposal aligns with this direction only if it advances at least
one strategic direction without weakening an earlier one. In practice, ask:

1. Does it make the operating truth more reliable?
2. Does it close or strengthen the evidence and learning loop?
3. Does it produce a measurable improvement claim?
4. Has the system earned the additional autonomy or breadth?
5. Can it fail visibly, reversibly, and within existing safety boundaries?

If the answer is no, the work is likely maintenance, a local capability, or
premature expansion. Track it in GitHub or the appropriate contract or
proposal; do not enlarge the product direction to accommodate it.

## What does not belong here

Issue status, implementation checklists, release targets, named bug fixes,
individual UI changes, parameter tuning, and machine-local runtime state are
deliberately excluded. They change faster than product direction and belong in
GitHub, current contracts, operations procedures, or measured runtime evidence.
