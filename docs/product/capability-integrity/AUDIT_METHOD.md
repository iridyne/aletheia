# Capability Integrity Audit Method

## Unit of review

A capability is a user- or system-observable outcome. It is not a source file,
table, endpoint, scheduler job, or config field.

Merge multiple entry surfaces when they promise the same final result. Split
identically named features when their final results differ.

## Required path

Every fact card follows:

```text
product promise
→ user or scheduler entry
→ config/API boundary
→ core service
→ durable write
→ downstream consumer
→ final behavior
→ owner-visible result
→ error and recovery path
```

The card must explicitly say `none` when a stage is intentionally absent. A
missing consumer may not be hidden by stopping the trace at persistence.

## Evidence levels

| Level | Meaning |
|---|---|
| E1 Static | Reviewed code contains the necessary entry, data flow, consumer, and error path. |
| E2 Executable | A focused test or bounded local reproduction crosses the important seams and asserts the final result. |
| E3 Runtime observation | The capability occurred naturally and produced the expected durable/user-visible result. This increases operational confidence but is not a support gate. |

Runtime observations must not compensate for a missing static consumer or a
failing executable acceptance path. Conversely, absence of a natural occurrence
does not downgrade a capability whose contract is complete and executable.

## Status

| Status | Definition |
|---|---|
| Supported | Complete necessary path, E1 and focused E2 acceptance, inside the declared product support surface, with no known contradiction. |
| Implemented | Complete E1/E2 implementation exists, but the owner has not included the capability in the declared support surface. |
| Partial | Meaningful implementation exists but one or more necessary stages are absent or unproved. |
| Experimental | Intentionally limited experiment; it is not a reliable product promise. |
| Broken | Public promise exists and normal use cannot reach the promised final result. |
| Deprecated | Intentionally unsupported and should be hidden or removed from active surfaces. |

`UI-only`, `Backend-only`, `Consumer-missing`, `Trigger-missing`, and
`Evidence-only` are **gap types**, not competing maturity statuses. This avoids
impossible choices such as whether a feature is both `Broken` and `UI-only`.

## Fact-card schema

Each card records:

- Capability ID and name
- Product promise and promise source
- Entry points
- Runtime path
- Durable state
- Final consumer
- End result
- Owner-visible result
- Error and recovery path
- E1/E2/E3 evidence
- Status
- Gap types
- Supported Core candidate
- Issue candidate
- Reviewer challenge

## Reviewer challenge

Before accepting a card, answer:

1. Did the card mistake a producer for a consumer?
2. Did it mistake a unit test for a production trigger?
3. Did it mistake a page or route for effective behavior?
4. Did it mistake an unrelated runtime observation for proof of the reviewed path?
5. Did it ignore failure visibility or recovery?
6. Does the final consumer actually use the produced value?
7. Does the test assert the final behavior rather than an intermediate object?

## Completion gate

The review is complete only when:

- every inventory item maps to exactly one reviewed fact card;
- every card has an explicit status and evidence level;
- every public surface is represented in the coverage ledger;
- every `Broken` or core-worthy `Partial` item has a scoped issue candidate;
- every `Supported` claim has E1/E2 evidence and no known contradiction;
- no issue candidate proposes a new architecture beyond the broken path.
