---
id: slim-core-legacy-gateway-retirement
status: implemented
github_issue: 3134
related_contracts:
  - ../contracts/persistence_recovery_contract.md
  - ../contracts/execution_contract.md
supersedes:
  - gateway-recovery
---

> 2026-10-09 注：文中提到的 ArtifactStore 已由 #3838（cd093075c）另行退役，见 ../history/artifact_store.md。

# Retire the dormant persistence Gateway authority

This bounded D2 decision implements the owner-approved
[#3134 Slim Core boundary](https://github.com/iridyne/aletheia/issues/3134):
Legacy may retain historical decode/read/migration, but no current recovery,
authorization or second Order/Fill/Position writer. It does not introduce a new
startup policy, exchange framework or replacement recovery state machine.

## Source proof and accepted change

At `864ddf7dc25dc77c83bc97187506accbbd3fdff1`, production startup does not call
`GatewayRecoveryService`. Its exchange protocol has only a mock implementation.
The seven `gateway/` recovery/state/session/lock/adapter modules form a separate
`ps_*` write path. The sole external production consumer of their classes is
`startup_behavior_gate`, reading unresolved legacy intents; the same gate also
queries legacy unknown Order/Position rows directly.

Delete that dormant executable write/recovery graph. Remove the legacy rows'
authority over startup admission and use the existing canonical `orders`,
`positions` and reconciliation-incident checks. Preserve their current immediate
versus stale in-flight distinctions and layered-pause recovery behavior.
Schema readiness checks require current Core tables, not the retired PS ledger.

Keep every historical `ps_*` table, stored row, decode model, archive import/export
and read-only history endpoint unchanged. EventStore, ArtifactStore, active
configuration leases, Gateway process supervision, UTA, the execution Coordinator
and deterministic client-order identities are not retired by this decision.
Do not claim a new mandatory startup reconciliation or a `SAFE_FREEZE` guarantee.

The older dual-write/FSM design is superseded by this single-owner decision and
retained as history, not an activation target. A future runtime safety change
must use the canonical Core ledger and follow its own accepted contract.

## Verification

Prove the seven retired modules cannot be imported or wired back into startup;
legacy unknown/open/created rows remain readable and cannot block admission;
canonical unresolved entries, close claims and incidents still block; ordinary
in-flight closes and manual pause ownership remain unchanged. Preserve the
historical schema and API readers. Replace tests of the deleted producer with
negative containment checks; use current execution/materialization tests for
active Fill identity and replay, not a copied legacy reconciler.

No database repair, migration, external order, credential read, runtime restart
or qualification promotion is part of this source change.
