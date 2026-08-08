# Domain Report Template

Use this structure for each completed domain report. Remove instructions that
do not apply; do not leave claims without an evidence label or source link.

## 1. Scope

State the question, included components, excluded components, and the boundary
that remains unchanged.

## 2. Source Map

List entry points, core symbols, durable state, final consumers, and important
cross-domain dependencies.

## 3. Mechanism

Explain the end-to-end workflow and state transitions. Prefer a small diagram
when it clarifies three or more relationships.

## 4. Valuable Practices

For each practice, record the problem solved, the actual implementation, the
reason it works, and its operating assumptions.

## 5. Trade-offs and Failure Modes

Record concurrency risks, stale-state windows, partial failure, restart
behavior, performance costs, maintenance costs, and misleading abstractions.

## 6. Aletheia Comparison

State what Aletheia already has, the real gap, which Aletheia invariants must
remain, and whether the two systems are solving the same problem.

## 7. Lessons

Classify each lesson as:

- `Reuse directly`
- `Reuse after adaptation`
- `Study further`
- `Do not reuse`

Include architectural observations and design reflections that may be useful
later, even when they do not justify immediate implementation.

## 8. Evidence and Gaps

List source locations, evidence classes, inferences, unavailable facts, and any
question that genuinely requires bounded runtime observation.
