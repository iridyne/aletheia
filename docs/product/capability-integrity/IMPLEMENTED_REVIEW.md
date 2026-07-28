# Implemented External-Exchange Capability Review

This review covers the four external-exchange capabilities classified
`Implemented`. Three form one recovery chain; credential verification is a
separate preflight fact.

| ID | Capability | Full-chain conclusion | Verdict |
|---|---|---|---|
| EX-06 | Exchange order materialization | An approved native exchange Order carries stable client/account identity; complete private fill evidence atomically creates namespaced Fill records and the linked Position or close state. Partial, conflicting, missing, or failed evidence remains `partially_filled`, `submission_unknown`, or `materialization_missing` instead of becoming a fabricated fill. | Implemented |
| EX-12 | Exchange order/position reconciliation | Two-minute order reconciliation and five-minute position reconciliation use account-explicit private views, durable audit/run state, and idempotent materializers. Fetch or integrity failures remain visible and inconclusive. External demo adoption is opt-in; Live is never auto-adopted. | Implemented |
| EX-13 | Stale order and disappearance handling | Daily cleanup acts only after age gates and refuses orders carrying Fill/open-Position economics. Position disappearance is classified as administrative, remains outcome-ineligible without verified economics, and preserves audit/recovery evidence. | Implemented |
| OS-03 | Exchange credential verification | Bitget, Binance, and OKX candidate/stored checks use private read-only account endpoints, close clients on success and failure, mask persisted values, and never treat a public time endpoint as credential proof. | Implemented |

## Executable evidence

The focused review reused existing tests:

```text
156 passed
```

Coverage included:

- exact private-fill identity and atomic entry/close materialization;
- Bitget, OKX, and Binance logical-account isolation;
- partial fill, duplicate fill, conflicting evidence, and retry behavior;
- historical local-chain recovery;
- adopted-position and TP/SL materialization;
- exchange-read failure and false-convergence protection;
- stale unresolved order cleanup and pending-close recovery;
- reconciliation-thrash detection and outcome contamination prevention.

Credential verification is additionally protected by `tests/test_security.py`
and executable API claim `API-S10`.

## Reviewer challenge

The old `_reconcile_ccxt_broker()` helper still has direct unit coverage, but
the current scheduler calls `reconcile()`, whose production multi-broker path
uses account-explicit private fill evidence and atomic materialization. A
directly tested legacy helper was therefore not mistaken for the active
runtime path.

Likewise, a passing mock/private-evidence contract is not proof that a real
venue is currently reachable. That distinction affects the support boundary,
not the implementation fact.

## First-principles result

No new exchange abstraction, compatibility layer, detector, or test harness is
needed. The existing chain is already broad enough for the owner's current
exchange scope. Promoting it would be a product-support decision with external
credential/venue acceptance, not a code-completeness repair.

The four rows therefore remain `Implemented`; no new Issue was created.
