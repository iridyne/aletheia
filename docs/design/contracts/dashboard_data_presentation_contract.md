# Dashboard Data Presentation Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `DP` · **Owner code:** `frontend/src/components/DataTable.tsx`, `frontend/src/components/DataState.tsx`

## Scope

This contract owns the shared table-data state vocabulary that separates a successful
empty read from loading, an unavailable initial read, and a failed refresh with retained
rows — the dashboard's landing point for "unavailable is never zero". It applies through
the shared `DataTable` to callers that supply an explicit `state`. It does not define
backend business states, and it does not own the system status headline
([Status Surface Contract](status_surface_contract.md)).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `frontend/src/components/DataState.tsx::DataStateMessage` | UI component | `DataTable`, page-level read messages |
| `frontend/src/components/DataTable.tsx::DataTable` (`state`, `stateMessage` props) | UI component | `frontend/src/pages/Learning.tsx` Knowledge tables; `frontend/src/pages/AgentActivity.tsx::RunsTab`, `AgentActivity.tsx::OpportunitiesTab` |
| `frontend/src/lib/learningWorkbench.ts::learningReadState` | call | `frontend/src/pages/Learning.tsx` |
| `frontend/src/pages/AgentActivity.tsx::resolveArrayReadState` | call | `AgentActivity.tsx` |

## Durable state

None. The presentation state is derived per render from TanStack Query's loading,
error and retained-data facts plus the successful result length; the operator is the
final consumer.

## Requirements

### DP-C01 `loading`

`While the initial request has no retained data, the DataTable shall show the loading state and hide rows.`

- WHEN the first query is pending THEN no rows and no empty copy are shown.

### DP-C02 `available`

`When a read succeeds with rows, the DataTable shall show the returned rows.`

- WHEN `state="available"` and rows exist THEN the table renders without a notice.

### DP-C03 `empty`

`When a read succeeds with zero rows, the DataTable shall show the caller's honest empty copy and hide the table.`

- WHEN `state="available"` is passed with zero rows THEN it is shown as `empty`.

### DP-C04 `stale`

`If a refresh fails while rows are retained, then the DataTable shall show a stale warning above the retained rows.`

- WHEN the query errors with cached data THEN rows remain visible under the warning.
- WHEN a later read succeeds THEN the state returns to `available` or `empty`.

### DP-C05 `unavailable`

`If a read fails with no retained data, then the DataTable shall show an unavailable alert and hide rows, never the empty copy.`

- WHEN the initial read errors THEN the text says the read failed and is not an empty result.

### DP-C06 Caller facts only

`The DataTable shall present only the state its caller supplies and shall not infer backend availability, freshness or business health from an empty array or from transport connection state.`

- WHEN the WebSocket is connected but the query failed THEN the state is not `available`.

### DP-C07 Implicit state for callers that omit it

`Where a caller omits state, the DataTable shall render zero rows as empty and non-zero rows as available.`

- WHEN `state` is undefined and `data=[]` THEN the empty copy is shown.

Evidence (all requirements): `frontend/src/components/DataTable.tsx::DataTable` (`resolvedState`, `visibleState`, `showTable`); `frontend/src/components/DataState.tsx::DataStateMessage`; `frontend/src/lib/learningWorkbench.ts::learningReadState`; `frontend/src/pages/Learning.tsx` (`tableState`).

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| initial read fails | hide rows | `unavailable` alert | `unavailable`, never `empty` |
| refresh fails with retained rows | keep rows | `stale` warning | retained rows are context, not fresh truth |
| later read succeeds | replace degraded state | `available` / `empty` | — |

No presentation state mutates backend data or query-cache authority.

## Authority

Presentation only; it changes no backend state and no query cache.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| DP-C03, DP-C04, DP-C05 | `frontend/src/components/DataTable.test.tsx` |
| DP-C01, DP-C04, DP-C05 | `frontend/src/lib/learningWorkbench.test.ts` |
| DP-C01..C05 (Agent Activity caller) | `frontend/src/pages/AgentActivity.readState.test.tsx` |
| DP-C06, DP-C07 | not covered by a dedicated test |

## Known conformance gaps

- **Agent Activity marks an in-flight refetch as `stale`** (DP-C04).
  `frontend/src/pages/AgentActivity.tsx::resolveArrayReadState` returns `stale` when
  `data && (isError || isFetching)`, so every successful background refetch briefly shows
  the stale warning although no refresh has failed.

## Current scope boundary

Only the Learning Knowledge tables and the Agent Activity runs/opportunities tables
supply an explicit state; trading pages (positions, orders, outcomes) use DP-C07 until
their endpoint facts are audited. The `partial`, `not-configured` and
`permission-limited` branches exist in `DataState.tsx` but no audited `DataTable` caller
supplies them, so they are not guarantees. The Learning Knowledge consumer will be
replaced by the Playbook view of [spec 0001](../specs/0001-learning-loop/spec.md).
