## Linked Issue / Change Contract

<!-- Closes #N or References #N — every PR links a GitHub Issue. -->

## SDD Depth

<!-- D0 / D1 / D2 — state it; do not repeat the full Change Contract here. -->

## Contract Delta

<!--
D0: write `NONE` and reference the existing Contract/invariant being repaired.
D1/D2: summarize the approved semantic delta (`ADDED / MODIFIED / REMOVED`) and, if
applicable, structural ownership/mainline changes.
Do not repeat the full Change Contract from the linked Issue.
-->

## Does this change alter semantics or structure?

<!-- Yes (D1/D2) / No (D0) -->

## Contract convergence

<!--
Answer each that applies; delete inapplicable rows.
For D0, the canonical Contract / SpecClaim / Proposal rows are normally not applicable.
-->

- [ ] The implementation satisfies the Change Contract in the linked Issue.
- [ ] D1/D2: Canonical Contract in `docs/design/contracts/` is updated in this PR.
- [ ] D1/D2: Executable SpecClaims in `tests/specs/` are updated or added.
- [ ] D2: This change is covered by an accepted `docs/design/proposals/` Proposal.
- [ ] Product Capability Ground Truth updated — only if a production capability
      genuinely changed (test-level conformance alone does not qualify).
- [ ] Runtime / economic qualification required: _describe or link_ / Not required.
- [ ] `uv run python scripts/check_docs.py` passes.

## Non-goals / out-of-scope

<!-- Confirm scope did not drift from the Change Contract. -->

---

<!--
EXAMPLES (delete before submitting):

D0 — Conformance Repair:
  Existing Contract: "Incomplete realized economics MUST fail closed."
  SDD Depth: D0 | Contract Delta: NONE
  Fix: code returned partial numeric PnL; corrected + regression test added.
  Runtime qualification: Not required.

D1 — Semantics/Structure Change:
  SDD Depth: D1
  MODIFIED: condition A previously returned X; now returns `unavailable` per updated semantics.
  Contract / Proposal / SpecClaims updated together, backward-compatibility verified.
-->
