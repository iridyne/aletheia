---
id: slim-core-settlement-convergence
status: draft
related_contracts:
  - ../contracts/execution_contract.md
supersedes: []
---

# Slim Core settlement ownership evidence

Scope: #3134 / #3135. This is a bounded investigation record, not a new Contract,
phase gate, or proof that the whole production ownership inventory is complete.
Source baseline: `6e7919398e70e498efb8a90caa9bdaa5cc653c59` (2026-09-12).
The accepted [execution contract](../contracts/execution_contract.md) governs the
lifecycle, cost-completeness, audit and recovery boundaries below.

## Current owners and consumers

| Fact / mutation | Current owner | Independently necessary boundary |
| --- | --- | --- |
| Initial verified economic close | `portfolio.pnl.close_position` | Open-state CAS, economic snapshot reread, atomic Order/Fill callback, Position/close-event/audit/hook commit. `outcome_closure.close_position_verified` only normalizes evidence and supplies the observed-fill callback. |
| No-fill administrative close | `outcome_closure.close_position_administrative` | Absence evidence is not economic evidence; never generates final PnL or eligible Outcome. |
| Deferred funding completion | `broker_funding_settlement.materialize_broker_funding_economics` | Complete scoped stored ledger must reproduce amount, holding window and attribution under the writer lock. Existing final economics cannot be overwritten; identical replay returns already-current. |
| Funding retention terminalization | `_terminalize_funding_history_expired` | Stops automatic retry only after a typed UTA retention proof and unchanged closed snapshot; money remains unavailable. This is not successful settlement. |
| Recovery scheduling | `_select_funding_queue_rows` / `_advance_funding_queue_cursor` | Durable keyset cursor with wrap, separate scan/I/O budgets and cursor CAS prevents a stalled prefix from starving later positions. Cursor does not own Position economics. |
| Funding audit mirror | `funding_audit.insert_pending_funding_audit` / `reconcile_funding_audits` | SQLite audit enrollment shares the economic transaction. JSONL append/ack has its own stable-ID recovery, independent of financial replay. |
| Outcome delivery | `outcome_hooks.process_outcome_hook_jobs` | Durable leases, replay and invalidation fence protect downstream writes. Attribution-read-failed recheck changes eligibility only, not monetary values. |
| Operator economics | `pnl.position_realized_pnl_view` | Derived known components remain distinct from reproducible final PnL. Unknown costs cannot be promoted by a display consumer. |

The later funding finalizer is still distinct from the initial close writer;
there is not yet a proven single final-economics mutation path across both.
The attribution recheck is also a separate eligibility mutation path. Merely
routing both through repository field whitelists is not sufficient proof of
semantic single ownership.

### What the close-event field means

`position_close_events` is not wholly redundant. Partial reductions retain
quantity, allocated entry fee and realized economics while Position remains
open. `portfolio.account_loss._partial_close_realized_today` consumes only these
partial events for daily loss. Deleting the table would lose a money boundary.

Full-close event PnL is a mutable projection of the corresponding Position's
final close, not a separately obtained funding fact. The current funding writer
updates both under one transaction and verifies one matching full-close event.
`portfolio.position_path_observer` consumes event quantities and realized values
for historical reconstruction. The planning-execution batch reader also reads
full events, but its PnL/cash consistency comparison is specifically for archived
`paper_internal`; current exchange completion is evaluated from Position
economics and causal close Fill relations. This does not authorize deleting an
event or changing historical interpretation without adapting its actual readers.

### Checks that are not interchangeable

The caller snapshot detects a race while network funding is fetched. The stored
ledger recomputation under the transaction detects changed funding attribution.
The full-close identity/quantity/fee check detects a mismatched lifecycle fact.
The already-current fingerprint/evidence comparison prevents a different result
from overwriting final money. Audit append deduplication addresses a separate
append-before-ack crash window. None is replaced by another merely because all
appear in the same finalizer.

## Proven deletion and remaining convergence

The funding worker's `_UTAFundingProjection` / `_client_for_mode` compatibility
layer had one production caller, only forwarded fields to `_uta_funding_ledger`,
and had a no-op close method. It owned neither a transport nor a resource.
Deleting it lets the worker pass the complete selected Position directly to the
existing UTA reader. Tests replace that bounded read effect, not a legacy client
factory. Account identity still comes from the persisted entry Order; no current
configuration or secret is substituted for missing identity.

The duplicated funding arithmetic also admitted negative entry/exit fees when
both Position and close-event snapshots contained the same invalid amount.
Two regression cases reproduced final PnL publication despite those invalid
costs. Initial close, deferred settlement and read verification now share
`pnl.calculated_realized_pnl`, extracted from the existing complete-input read
predicate; deferred settlement refuses an unavailable calculation before writes.
Matching snapshots alone are not evidence that monetary values are valid.

Next convergence must address the final eligibility decision and the
initial-versus-deferred final-economic write boundary while
retaining the independently proven guards above. Audit mirror repair must never
become another reason to execute settlement. Moving functions between files or
renaming the two owners does not satisfy that requirement.

Late missing-fee completion is still absent: the funding queue selects
`incomplete_funding_economics`, while a missing-fee close carries
`incomplete_realized_economics`. A future completion path must prove newly
available costs against exact durable Fill identities, without rewriting known
history, estimating conversion or resubmitting an external close.

No runtime qualification follows from this source inventory. Exact deployment,
booted identity, health and a natural or otherwise owner-authorized Demo close
remain separate evidence gates. Strategy, Risk and exit policy must not be tuned
to manufacture the sample.
