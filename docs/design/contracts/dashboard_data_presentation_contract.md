# Dashboard Data Presentation Contract

## Purpose and current scope

This contract owns the Learning Knowledge vocabulary used to distinguish a
successful empty read from loading, an unavailable initial read, or a failed
refresh with retained data. It applies to the shared `DataTable` only through
that production consumer. It does not define backend business states.

## Entry points

- `frontend/src/components/DataState.tsx` defines the presentation vocabulary.
- `frontend/src/components/DataTable.tsx` accepts an explicit `state` and
  optional state-specific message.
- `frontend/src/pages/Learning.tsx` supplies Query-derived state to the
  Knowledge tables.

## Durable state and consumers

The presentation state is not durable. Learning Knowledge derives it from
TanStack Query's loading, error, and retained-data facts plus the successful
result length. The dashboard operator is the final consumer.

## Observable result

| State | Authorized source | Table behavior |
|---|---|---|
| `loading` | Initial request has no retained data | Show loading state; hide rows |
| `available` | Successful complete read | Show returned rows |
| `empty` | Successful read confirmed zero rows | Show honest empty copy; hide table |
| `stale` | Refresh failed with retained data | Show warning and retained rows |
| `unavailable` | Read failed with no retained data | Show alert; hide rows |

## Invariants and trust boundaries

- An empty array alone cannot prove `unavailable` or `stale`.
- Transport connection state does not prove data freshness.
- `DataTable` presents caller facts and does not infer backend availability or
  business health.
- Existing callers that omit `state` retain compatibility: zero rows render as
  `empty`, and non-zero rows render as `available`.

## Failure and recovery semantics

An initial read failure is `unavailable`, not `empty`. A refresh failure with
retained rows is `stale`; those rows remain visible with a warning. A later
successful read replaces the degraded presentation with `available` or
`empty`. No presentation state mutates backend data or query-cache authority.

## Executable evidence

- `frontend/src/components/DataTable.test.tsx` proves unavailable-versus-empty
  separation and stale-row retention.
- `frontend/src/lib/learningWorkbench.test.ts` proves Query facts map to
  loading, unavailable, stale, and available states.
- `frontend/src/pages/Learning.tsx` is the production caller that converts a
  successful zero-row read to `empty` and supplies the resulting state to the
  Knowledge tables.

## Current scope boundary

Only the Learning Knowledge tables currently supply the explicit shared state.
Other tables retain the compatibility behavior until their endpoint facts are
audited and approved separately. The component also contains presentation
branches for `partial`, `not-configured`, and `permission-limited`, but no
audited production `DataTable` caller supplies the facts required by those
branches. They are not guarantees of this current Contract.
