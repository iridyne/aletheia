# TickFlow Stock Panel Reference Study

This tree preserves a source-bound study of the external TickFlow Stock Panel
application and the lessons that may be useful to Aletheia. It is an evidence
collection, not a statement of Aletheia's current behavior and not permission to
change trading, risk, execution, or storage contracts.

## Reference Boundary

- Reference repository: `/Users/yixian/Projects/references/tickflow-stock-panel`
- Comparison repository: `/Users/yixian/Projects/aletheia`
- Baseline commits and versions are recorded in
  `00-research-program/baseline.md`.
- Static implementation and call-path evidence are primary. Screenshots,
  comments, and project documentation are labelled separately.
- Runtime tests, builds, and broad validation suites are not the research
  default. Use a minimal probe only when a material question cannot be answered
  from source and record why it was needed.

## Directory Map

| Directory | Research lane |
|---|---|
| `00-research-program/` | Stable scope, sequence, evidence method, baseline, and report template |
| `01-frontend/` | UI system, information architecture, data components, client state, and API contracts |
| `02-backend-data/` | FastAPI composition, providers, normalization, analytical storage, and I/O consistency |
| `03-runtime-engineering/` | Realtime delivery, background jobs, scheduling, performance, observability, safety, and packaging |
| `99-synthesis/` | Cross-domain borrowing matrix and staged recommendations after domain studies mature |

## Capture Rule

Each domain study records:

1. scope and exclusions;
2. source map and production call paths;
3. observed mechanism;
4. design strengths and likely motivation;
5. failure modes and trade-offs;
6. comparison with Aletheia;
7. direct, adapted, and rejected lessons;
8. useful observations and design reflections;
9. evidence classification and unresolved gaps.

Do not record owners, percentages, mutable task checklists, or current runtime
status here. GitHub remains the execution tracker. When research becomes an
approved Aletheia contract or design decision, promote it to the authoritative
documentation lane instead of silently treating this tree as current truth.

## Reading Order

Start with `00-research-program/research-plan.md` and
`00-research-program/evidence-method.md`, then open only the active domain's
README and reports. Do not bulk-read the entire study tree.
