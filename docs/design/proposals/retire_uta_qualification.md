---
id: retire-uta-qualification
status: implemented
github_issue: 3035
related_contracts:
  - docs/design/contracts/execution_contract.md
---

> 2026-10-09 状态改为 implemented：#3035 已关闭；现行规则在 docs/design/contracts/execution_contract.md（Demo/Testnet mutation 不要求 SHA-bound 资格证书）。

# Retire SHA-bound UTA mutation qualification

Owner accepted this scoped D2 in #3035 on 2026-09-10, as the sole focused batch
under #3031. Remove the local certificate's execution veto without introducing
a replacement artifact, fingerprint license, flag, ledger or startup certification.

The Coordinator constructs the configured adapter and compares the actual sending
instance against the expected account. Existing Risk/Gateway authority, durable
Order claim/CAS, ambiguity recovery, private Fill and cost completeness remain.
Live Trust remains independent. No schema, history, economics, tactic or Risk
parameter changes are part of this decision.

Delete the dedicated certificate reader, direct-adapter certification script and
certificate-only tests. Existing data-directory artifacts remain inert; do not
migrate, refresh or delete them. The archived concept is documented in
[history](../history/uta_qualification.md).

Validation covers certificate-free Demo/Testnet submission, unrelated repository
SHA changes, adapter construction failure, exact-account mismatch, authoritative
Risk, unresolved commands, private Fill, close/protection and Live authorization.
External qualification must be demonstrated through the normal business path;
tests, merge and healthy deployment alone do not prove a real round-trip.

After merge, inspect in-flight external state, deploy through the normal Fedora
update command and record exact runtime identity and preserved historical facts.
Return to #3031's first real runtime breakpoint. Without a naturally executable
Plan, report waiting_for_runtime_evidence; do not manufacture a qualification trade.

If a concrete unsafe defect appears, pause affected new execution through existing
controls, preserve/reconcile submitted commands and roll back compatible code if
needed. Do not rewrite historical data or restore the certificate permanently
without a new owner decision.
