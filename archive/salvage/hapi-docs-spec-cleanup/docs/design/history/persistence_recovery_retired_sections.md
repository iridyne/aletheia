# Persistence Contract — retired and historical sections

> **History (moved 2026-10-10).** Retired or superseded design kept as evidence; not current authority. See [design history](README.md).

Moved verbatim from [`persistence_recovery_contract.md`](../contracts/persistence_recovery_contract.md)
when it was rewritten into the numbered Contract form. Current behavior lives only in that Contract.
The one relative link to the execution contract was re-pointed so it resolves from this folder.

## Former scope narrative

Schema v9 and v11 retain [archived simulation evidence](../history/local_simulated_protection.md).
Its tables remain available to historical readers; activation, simulation,
settlement and recovery producers are retired.

Schema v12 retains both immutable `ufc-fixed-look/v1` plans and the current
`ufc-fixed-look/v2` conditional-randomization plans. The migration rebuilds only
`learning_inference_decisions` under a savepoint, preserves its rows and storage
guards, and grants no Learning or trading authority.

The former PS recovery design is
[historical](../history/gateway_recovery.md), superseded by the accepted
[Slim Core retirement](../proposals/slim_core_legacy_gateway_retirement.md).
Its executable writer, recovery, mock-exchange, session and leader-lock modules
are removed. The corresponding `ps_*` rows and read-only history endpoints remain;
their old lifecycle states do not grant or deny current startup/trading authority.

## Former PS invariant

- Historical PS Order/Fill/Position, intent, session, lock and reconciliation
  tables have no active producer or recovery/authorization lifecycle. Startup
  readiness requires the canonical Core tables and evaluates current `orders`,
  `positions` and reconciliation incidents, not archived PS status values.

## Former retirement disclaimer

This retirement does not claim those additional guarantees. Current ambiguous
submissions and unresolved close claims retain the canonical Core reconciliation
and pause rules in the [execution contract](../contracts/execution_contract.md).
