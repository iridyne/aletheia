# Persistence Contract

## Purpose and current scope

Aletheia durably records local metadata, events, artifacts, order identities,
and selected policy/learning state. These stores survive a normal process
restart. This Contract does not claim automatic Gateway recovery, exchange
reconciliation before startup, or `SAFE_FREEZE` enforcement.

The future safety design is
[`gateway_recovery.md`](../proposals/gateway_recovery.md).

## Entry points

- SQLite repositories and model stores provide durable application state.
- `EventStore` appends machine-local JSONL events.
- `ArtifactStore` persists immutable artifacts and manifests.
- `/api/persistence/*` exposes the currently registered persistence API.
- Exchange adapters send deterministic client order identifiers where their
  current implementation supports them.

## Durable state and consumers

| State | Current consumer |
|---|---|
| SQLite application tables | runtime services, API routes, analytics, and reconciliation |
| Append-only event log | reconciliation and learning/policy readers |
| Artifact store | lessons, skills, policies, and operator-facing readers |
| Deterministic client order ids | supported exchange adapter submission paths |

An in-memory object is never promoted to durable fact merely because a class or
test exists.

## Observable result

After a normal application restart, committed SQLite rows, appended events, and
stored artifacts remain readable by their existing consumers. Repeating an
order intent through a currently wired adapter uses its deterministic identity.

## Invariants and trust boundaries

- SQLite is the authoritative local metadata store.
- Event records are append-only.
- Artifact manifests identify immutable persisted content.
- Secrets and dirty runtime state are excluded from sync/export artifacts.
- A recovery class with no production caller is not a recovery guarantee.

## Failure and recovery semantics

Storage failures must remain visible to their caller and must not be converted
into a successful write. Current startup does not enter a mandatory
`RECOVERY_MODE`, does not guarantee exchange reconciliation before new work,
and does not enforce `SAFE_FREEZE` from the dormant recovery state machine.

Those absent guarantees are a product gap, not current Contract behavior. Their
approved target and rollback boundary are recorded in the Gateway Recovery
Proposal.

## Executable evidence

`tests/specs/spec_persistence.py` verifies the current stores, deterministic
identities, wired adapter behavior, and negative tripwires that prevent dormant
recovery components from being presented as active runtime guarantees.
