# Audit Gates Registry

Machine-enforced contract invariants. A gate here turns red when the code stops
honouring a clause that the contract documents state in prose — the contract
text stays the authority, the gate is only its executable check.

Use this file for two things:

- **Adding a gate**: find the contract clause, add a row, add the test file under
  `tests/audit/`. A gate that cannot fail is worse than no gate.
- **Reading a red gate**: the failure message names the file, the line and the
  broken invariant; the row below names the clause to re-read.

A gate is deliberately narrow. Where the invariant admits a legitimate exception,
the gate carries an inline `# EXEMPT: <reason>` allowlist rather than being
loosened until it stops catching anything.

## Gates

| Gate | Contract clause | Invariant |
|---|---|---|
| `tests/audit/test_no_silent_except_pass.py` | [`trust_and_change_control.md`](policies/trust_and_change_control.md); R20 analysis §G1 | No new `except ...: pass` site. The frozen baseline only shrinks; every remaining site carries an explicit reason. |
| `tests/audit/test_minimum_profit_loop_chain.py` | `docs/product/CAPABILITY_GROUND_TRUTH.md` | The minimum profit loop (Universe → Candidate → EntryPlan → Exit → Outcome) stays wired end to end at D0 level. |
| `tests/audit/test_reconciliation_no_gateway_imports.py` | [`execution_contract.md` § 6](../design/contracts/execution_contract.md) / `EX-C04` | `execution/reconciliation.py` reaches no order-placement entry point. Reconciliation compares state and writes convergence/audit records; it never places or cancels an order. |
| `tests/audit/test_scheduler_no_order_submission.py` | [`scheduler_contract.md`](../design/contracts/scheduler_contract.md) | `agent_runtime/scheduler.py` and `scheduler_retention.py` reach no order-placement entry point. The scheduler registers jobs and records failures; placement goes through the Execution Gateway under Risk approval. |
| `tests/audit/test_doctor_no_execution_writes.py` | [`filter_evidence_contract.md`](../design/contracts/filter_evidence_contract.md); segment isolation in [`ARCHITECTURE.md`](../design/architecture/ARCHITECTURE.md) | `core/aletheia/diagnostics/` is read-only: no repository writer import, no `insert_*`/`update_*`/`transition_*` call, no `INSERT`/`UPDATE`/`DELETE` SQL. Reads are unrestricted. |
| `tests/audit/test_close_client_oid_transaction_guard.py` | [`execution_contract.md` § 10](../design/contracts/execution_contract.md) | `next_close_client_oid` is allocated only inside a writer transaction. |
| `tests/audit/test_alpha_cohort_reachability.py` | [`opportunity_outcome_contract.md`](../design/contracts/opportunity_outcome_contract.md) § 124; [`filter_evidence_contract.md`](../design/contracts/filter_evidence_contract.md) § 60/315 | Every alpha cohort anchor table has a row producer reachable from a production entry surface, every joined table is registered with its retention, and that registry matches what `scheduler.py` actually prunes. Table-level only — a row-subset producer gap is recorded as an explicit exemption, see its module docstring. |
| `tests/specs/spec_agent_contracts.py::test_registered_scheduler_jobs_record_swallowed_failures` | [`scheduler_contract.md`](../design/contracts/scheduler_contract.md) / `INV-SCH-5`; `AC-B05` | Registered guarded wrappers retain failure telemetry across declared seams; missing or ambiguous callback ownership fails with file/line and job identity. Reuses the existing spec. |
| `tests/audit/test_audit_events_have_actor_source.py` | [`audit_chain_contract.md` §2](../design/contracts/audit_chain_contract.md) | Positional or `event=` literal `write_audit` payloads have actor/source, including preceding direct local literal mappings. Dynamic payload builders and value enums are not checked. |
| `tests/audit/test_position_lifecycle_story_readonly.py` | [`profit_loop_observability_contract.md`](../design/contracts/profit_loop_observability_contract.md) | Story repository imports/access are limited to the currently required `get_db` reader, including import aliases; any new reader needs explicit review. Literal SQL-write checks reuse diagnostics; actual read-only tests remain necessary. |
| `tests/helpers/authority_writer_checks.py` with existing per-table owner tests | [`execution_contract.md` §11.6](../design/contracts/execution_contract.md); storage owner contracts | Existing generic-writer restrictions and table-specific writer ownership remain authoritative; no duplicate gate or blanket storage-directory SQL ban. |

## Gate semantics

These structural gates parse source without importing the scanned production
module, opening a database or using the network. Measure their runtime rather
than inferring speed from static analysis. Behavior and failure-path tests remain
necessary; static matching is not a whole-program proof.
The canonical pytest tier classifier owns selection; a gate must not force
`tier_unit` against that classification. All guards under `tests/audit/` remain
part of the changed-file audit step regardless of their tier.

Placement-entry-point gates share
[`tests/audit/_segment_guard.py`](../../tests/audit/_segment_guard.py). Adding a
new order-placement entry point to `execution/gateway.py` or
`execution/coordinator.py` means adding its name to the gates' target sets —
the scan cannot guess which names place orders.

## Current-contract reconciliation of #3561

Owner approved replacing the historical file-count target with valid current
invariants. Reuse existing gates rather than create ten duplicate files.

- The blanket ban on SQL outside storage is retired: the accepted settlement
  writer and the independently owned Lab database are legitimate owners.
- The local simulator paper boundary is retired with that runtime; an empty
  directory scan is not passing evidence.
- The proposed `insert_pi_decision(reason=...)` enum gate is retired: that is not
  the current API or decision Contract. Human-readable reasoning remains intact.
- Scheduler failure ownership, audit provenance and Story read-only checks above
  retain their actual scope and executable negative controls.

## Related

- [Documentation Governance](policies/documentation_governance.md) — which
  document is the authority for a given claim.
- [Trust-boundary coverage](../operations/development/TRUST_BOUNDARY_COVERAGE.md) —
  the other half of the same idea: measured coverage floors on the paths where a
  wrong answer misleads a decision.
