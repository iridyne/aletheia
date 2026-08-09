# Dashboard Data Presentation Contract

## Purpose and current scope

This contract owns the dashboard vocabulary used to distinguish a successful
empty read from loading, degraded, unavailable, unconfigured, or
permission-limited data. It currently applies to the shared `DataTable` and its
Learning Knowledge consumer. It does not define backend business states.

## Entry points

- `frontend/src/components/DataState.tsx` defines the presentation vocabulary.
- `frontend/src/components/DataTable.tsx` accepts an explicit `state` and
  optional state-specific message.
- `frontend/src/pages/Learning.tsx` supplies Query-derived state to the
  Knowledge tables.

## Durable state and consumers

The presentation state is not durable. Callers derive it from authoritative
API fields, explicit configuration or permission facts, and TanStack Query's
request/cache facts. The dashboard operator is the final consumer.

## Observable result

| State | Authorized source | Table behavior |
|---|---|---|
| `loading` | Initial request has no retained data | Show loading state; hide rows |
| `available` | Successful complete read | Show returned rows |
| `empty` | Successful read confirmed zero rows | Show honest empty copy; hide table |
| `stale` | Backend stale fact, or refresh failed with retained data | Show warning and retained rows |
| `partial` | Explicit coverage or section failure fact | Show warning and available rows |
| `unavailable` | Read failed with no retained data, or backend says unavailable | Show alert; hide rows |
| `not-configured` | Explicit configuration/capability fact | Show configuration state; hide rows |
| `permission-limited` | Explicit permission fact | Show permission state; hide rows |

## Invariants and trust boundaries

- An empty array alone cannot prove `unavailable`, `stale`, `partial`,
  `not-configured`, or `permission-limited`.
- Client age may produce `stale` only when the caller owns an explicit age
  threshold; transport connection state is not freshness.
- `partial` requires an explicit coverage or failed-section fact; non-empty is
  not equivalent to complete.
- `DataTable` presents caller facts and does not infer backend availability,
  configuration, permission, or business health.
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
  loading, unavailable, stale, partial, and available states.

## Current scope boundary

Only the Learning Knowledge tables currently supply the explicit shared state.
Other tables retain the compatibility behavior until their endpoint facts are
audited and migrated separately.
