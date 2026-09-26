## Linked Issue / Change Contract

<!-- Closes #N or References #N — every PR links a GitHub Issue. -->

## SDD Depth

<!-- D0 / D1 / D2 — state it; do not repeat the full Change Contract here. -->

## Contract Delta

<!--
D0: write `NONE` and reference the existing Contract/invariant being repaired.
D1: summarize the approved semantic delta (`ADDED / MODIFIED / REMOVED`) and required
Contract/spec updates.
D2: summarize the approved structural delta; update affected Contracts and
SpecClaims where required. A Proposal is optional -- link one only if you wrote one.
Do not repeat the full Change Contract from the linked Issue.
-->

## Does this change alter semantics or structure?

<!-- Semantics (D1) / Structure (D2) / Neither (D0 conformance repair) -->

## Contract convergence

<!--
Answer each that applies; delete inapplicable rows.
For D0, the canonical Contract / SpecClaim rows are normally not applicable.
-->

- [ ] The implementation satisfies the Change Contract in the linked Issue.
- [ ] D1: Canonical Contract in `docs/design/contracts/` is updated in this PR.
- [ ] D1: Executable SpecClaims in `tests/specs/` are updated or added.
- [ ] D2: The gates that hold the new structure exist and are named above.
- [ ] Product Capability Ground Truth updated — only if a production capability
      genuinely changed (test-level conformance alone does not qualify).
- [ ] Runtime evidence — pick exactly one; an empty answer is not acceptable:
      - [ ] Observed: _Fedora cycle / order id / Outcome — the actual observation._
      - [ ] Not applicable because: _why this change cannot produce runtime evidence._
      - [ ] Blocked on: _exactly what is missing, and who can unblock it — file or link the Issue._
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
  Runtime evidence: Not applicable because — the correction is a pure function of
  inputs already covered by the regression; no runtime path changes.

D1 — Semantics Change:
  SDD Depth: D1
  MODIFIED: condition A previously returned X; now returns `unavailable` per updated semantics.
  Contract and SpecClaims updated as needed; obsolete callers removed.
-->
