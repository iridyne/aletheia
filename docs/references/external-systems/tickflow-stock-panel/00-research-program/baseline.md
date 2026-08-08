# Source Baseline

This is a dated evidence baseline for the study. It prevents later reports from
presenting observations from an older checkout as current truth.

## Captured 2026-08-07

### TickFlow Stock Panel

- Repository: `/Users/yixian/Projects/references/tickflow-stock-panel`
- Branch: `main`
- Commit: `99bdec875d4bbf5c30ddc43534b81ada0f3b0f6b`
- Frontend package version: `0.1.88`
- Backend project version: `0.1.88`
- Local CodeGraph: 392 indexed files, 7,771 nodes, 20,310 edges
- CodeGraph database size: approximately 27.39 MB

### Aletheia Comparison Checkout

- Repository: `/Users/yixian/Projects/aletheia`
- Branch: `master`
- Commit before this documentation study:
  `45f56c4808995a4d42fa0ab3a3a2fbb7f9c0fdec`

## Initial Architecture Orientation

The reference application uses a React/TypeScript frontend with TanStack Query
and a Python/FastAPI backend. Its analytical data plane persists partitioned
Parquet, uses Polars for dataframe processing and hot snapshots, and registers
Parquet-backed views in an in-memory DuckDB connection. Realtime domain events
notify the frontend, which refetches canonical data through REST.

Aletheia uses SQLite as the authority for transactional trading state and a
bounded writer lane for serialized commits. These systems therefore should not
be compared as if DuckDB/Parquet and SQLite were interchangeable databases.

This orientation narrows later research; it is not yet a complete domain
conclusion.

## Prior Minimal Probe Disclosure

Before the research method was narrowed, 24 selected reference-backend tests
were run once and passed. That observation is retained for provenance but is
not the study method, does not establish product completeness, and will not be
repeated as a routine domain step.
