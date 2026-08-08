# Evidence Method

## Evidence Classes

Use the strongest available evidence and label weaker material explicitly.

| Class | Meaning | Typical source |
|---|---|---|
| `S1 implementation` | Current source directly implements the behavior | Function, class, configuration, schema |
| `S2 call path` | The implementation is connected to a production entry and consumer | CodeGraph path plus source inspection |
| `S3 durable structure` | A persisted schema, file layout, cache marker, or protocol supports the claim | Parquet layout, JSON record, SQLite schema, event type |
| `S4 project intent` | Documentation or comments state a design goal, but wiring may differ | CONTRIBUTING, README, comments |
| `S5 visual artifact` | A screenshot or static artifact shows a user experience at a point in time | Repository screenshots or captured pages |
| `S6 runtime observation` | A deliberately scoped run demonstrates current behavior | Local UI observation or minimal probe |

`S4` and `S5` never prove current implementation by themselves. `S6` proves
only the observed scenario, environment, and time; it does not replace source
analysis or prove recovery behavior.

## Default Investigation Loop

1. Ask one bounded domain question.
2. Use CodeGraph to locate symbols and production call paths.
3. Read the smallest relevant source set completely enough to understand the
   boundary.
4. Identify durable state, caches, events, and final consumers.
5. Search focused cross-references for bypasses or alternate paths.
6. Compare the same boundary in Aletheia.
7. Record strengths, risks, reflections, and evidence gaps.
8. Stop at the domain boundary; do not drift into implementation.

## Runtime-Validation Rule

Do not run pytest, a full frontend build, broad linters, or a completeness scan
as a routine research step. A minimal runtime probe is justified only when all
of the following are true:

- the answer materially affects the borrowing conclusion;
- static implementation and call-path evidence remain ambiguous;
- the probe is narrow, safe, and inexpensive;
- the report records the exact question and the limited fact demonstrated.

## Recording Uncertainty

Use one of these statements instead of filling gaps with assumptions:

- `Confirmed by implementation`
- `Confirmed by production call path`
- `Documented intent; implementation differs or is not yet traced`
- `Visible in a dated artifact only`
- `Inference from the listed evidence`
- `Not available`
- `Requires a bounded runtime observation`

## Comparative Rule

Do not rank an entire application from a single mechanism. Compare like with
like: analytical reads against analytical reads, transaction ledgers against
transaction ledgers, UI state contracts against UI state contracts, and job
recovery against job recovery. A practice may be excellent for reconstructible
market data and inappropriate for orders, fills, positions, or audit records.
