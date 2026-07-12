# Outcome Integrity Final Audit — Automated Evidence

## Result

The deterministic repair chain is covered end to end in isolated databases. Verified
exits and administrative lifecycle convergence now keep distinct meanings through
position state, paper cash, Risk, Learning and owner-facing reporting. No automated test
places a real order, unlocks live mode, changes a risk limit, applies historical cleanup
to the owner database, or applies a cash compensation candidate.

## Automated coverage

| Lifecycle area | Proof | Result |
|---|---|---|
| local paper vs external empty view | `spec_pipeline_adopted_position.py` account-scope tests | local-only; no orphan candidate/close |
| one miss, regressed/wrong account, restart | `test_disappearance_confirmation.py` | durable, scoped and idempotent |
| incomplete/absent/verified exit routing | `test_verified_exit.py`, `test_outcome_closure.py` | inconclusive stays open; absent is administrative; fill is genuine |
| Bitget / OKX / Binance protocol | `test_verified_exit.py` | broker-neutral normalized evidence |
| administrative reopen incident | `test_reconciliation_integrity_incident.py` plus full-lifecycle E2E | native and adopted both refreeze Learning |
| cash / Risk / Learning / reporting | `test_outcome_integrity_full_lifecycle.py` | one cross-layer meaning, repeat-safe |
| history classification/supersession | `test_outcome_history_remediation.py` | dry-run/apply parity, rollback and repeat safety |
| contract inventory | OI-C06 in `spec_outcome_integrity.py` | executable final guard |

An administrative close does not fabricate a daily loss or trip the kill switch. Because
its account economics are unknown, Risk becomes explicitly unavailable and blocks new
proposals fail-closed. This is the precise safety behavior; “Risk unchanged” must not be
misread as “pretend the account is healthy.”

## Not run or not authorized in this task

- Real paper-only runtime soak across multiple scheduler reconciliation cycles: **not run**.
- Real gateway/process restart during a soak: **not run**.
- Maintenance dry-run or apply against the owner's runtime database: **not run**.
- Paper-cash compensation: **not approved and not applied**.
- Live unlock/order placement: **not authorized; live remains disabled by policy**.

These are operational facts and must be established from the target machine, not inferred
from Git. A later soak should use the main checkout/runtime data, keep live disabled, avoid
manual database edits, cover multiple reconciliation cycles plus one controlled process
restart, and record its observation outside Git's intent documents.
