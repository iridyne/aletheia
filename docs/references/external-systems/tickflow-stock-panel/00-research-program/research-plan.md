# TickFlow Research Plan

## Objective

Extract the practices that make TickFlow Stock Panel effective as a Node/React
frontend plus Python backend application, understand their real implementation
and limits, and determine which ideas are useful to Aletheia without weakening
Aletheia's trading-safety or state-consistency boundaries.

This is a research sequence, not an implementation plan or live progress
tracker.

## Research Sequence

### Lane 1: Frontend

1. **UI design system and primitives** — Tailwind configuration, semantic
   tokens, financial colour language, typography, spacing, reusable components,
   accessibility, and page-state presentation.
2. **Information architecture and loading** — navigation, shell, page grouping,
   route composition, lazy loading, authentication, onboarding, and bootstrap
   behavior.
3. **Data-dense interaction components** — tables, virtualisation, charts,
   filters, search, watchlists, responsive behavior, and large-result usability.
4. **Client state and cache model** — TanStack Query configuration, query-key
   registry, shared queries and mutations, invalidation, stale data, and local
   versus server state.
5. **API client and frontend contracts** — request envelope, domain types,
   error semantics, authorization, initialization failures, and module
   boundaries.

### Lane 2: Backend and Data

6. **FastAPI composition and lifecycle** — routes, services, repositories,
   application state, startup, shutdown, background warm-up, sync/async
   boundaries, and failure isolation.
7. **Providers and normalization** — data-source capabilities, fallback
   semantics, batch acquisition, rate limits, type normalization, quality
   checks, deduplication, and financial-data meaning.
8. **Analytical storage and read models** — Parquet partitioning, Polars eager
   and lazy execution, DuckDB views, hot caches, historical reads, derived data,
   and backtest matrices.
9. **I/O and consistency** — read-modify-write, atomic replacement, locks,
   cache/disk ordering, generation markers, invalidation, schema evolution, and
   crash windows.

### Lane 3: Runtime Engineering

10. **Realtime delivery** — quote refresh, SSE, per-client subscribers, event
    coalescing, bounded queues, reconnect behavior, missed-event recovery, and
    REST authority.
11. **Background jobs and scheduling** — job lifecycle, single-flight,
    progress, logs, history, timeouts, cancellation, zombie work, schedulers,
    and market-session control.
12. **Engineering assurance** — performance, memory, observability, test
    strategy, configuration, security, desktop packaging, upgrades, and local
    data migration.

### Synthesis

After the domain evidence is mature, produce a cross-domain matrix separating:

- practices Aletheia can reuse directly;
- practices that need adaptation to Aletheia's invariants;
- practices that solve problems Aletheia does not currently have;
- practices that should be rejected because of safety, consistency, recovery,
  or maintenance risks;
- the smallest Alpha-relevant experiments that could validate a promising idea.

## Domain Research Questions

For every mechanism, trace the real entry point, durable state, final consumer,
observable behavior, concurrency boundary, failure semantics, and recovery
path. Separate a library choice from the engineering practice built around it.
Do not infer correctness merely from naming, comments, screenshots, or a green
test.

## Change Boundary

Research does not authorize implementation. In particular, it does not change:

- SQLite as the authority for Aletheia's transactional trading state;
- Risk approval before execution;
- the Execution Gateway as the only route to an exchange;
- live trading being disabled by default;
- existing safety, audit, credential, and semantic-honesty requirements.
