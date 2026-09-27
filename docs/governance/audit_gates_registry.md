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
| `tests/specs/spec_agent_contracts.py::test_registered_scheduler_jobs_record_swallowed_failures` | [`scheduler_contract.md`](../design/contracts/scheduler_contract.md) / `INV-SCH-5`; `AC-B05` | Registered guarded wrappers retain failure telemetry across declared scheduler re-export seams; errors identify the owning file/line and job/callback. Reuses the existing spec instead of duplicating it in audit/. |

## Gate semantics

All gates here are **static**. They parse source with `ast` and never import the
scanned module, open a database, or touch the network, so they run in the
changed-file gate in well under a second and cannot be perturbed by runtime
state.

Placement-entry-point gates share
[`tests/audit/_segment_guard.py`](../../tests/audit/_segment_guard.py). Adding a
new order-placement entry point to `execution/gateway.py` or
`execution/coordinator.py` means adding its name to the gates' target sets —
the scan cannot guess which names place orders.

## Related

- [Documentation Governance](policies/documentation_governance.md) — which
  document is the authority for a given claim.
- [Trust-boundary coverage](../operations/development/TRUST_BOUNDARY_COVERAGE.md) —
  the other half of the same idea: measured coverage floors on the paths where a
  wrong answer misleads a decision.
