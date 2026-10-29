## Linked Issue / Change Contract

Closes #3393. References #3134 and #3135.

## SDD Depth

D0 — restore valid Bitget Classic funding-history requests.

## Contract Delta

NONE. Preserve the existing UTA COMPLETE-only economic-evidence contract and venue-specific request ownership. Bitget Classic page size is capped at100; saturation is judged against that actual page size and remains PARTIAL. Live Classic inherits valid request sizing but Live remains disabled; no authority or economic policy change.

## Does this change alter semantics or structure?

Neither (D0 conformance repair). No Core or other venue/profile change.

## Contract convergence

- [x] The implementation satisfies the bounded Change Contract in #3393.
- [x] Runtime / economic qualification required: after merge, deploy exact head and observe the existing scheduler; no manual settlement. Existing old closes never become new-build qualification evidence.
- [x] `uv run python scripts/check_docs.py` passes.

## Verification

Fedora03fa normal essential tick failed on two historical closed Demo Positions. Read-only diagnostic traced report incompleteness to CCXT BadRequest/venue40020. Same account/window with only request limit100 instead of1000 returned a funding event; no settlement write. Source fix is one profile-local cap with matching full-page completeness threshold, not a paginator.

Regression proof: before fix, expanded bound cases3failed/2passed; after fix adapter suite101passed, adapter/economics/funding/fairness163passed. The additional composition suite exposed two existing tests importing retired aletheia.maintenance paths; independently reproduced2/2 failures on untouched master03fa. A separate verification-only commit changes that one locator to actual ops.maintenance; all binding-before-help and exit-code assertions remain. Combined final verification179passed. Independent true-diff review found no blocker. Test interpreter explicitly binds current worktree Core/Extension roots; no local Gateway or remote CI run.

## Non-goals / out-of-scope

No new pagination framework, dependency, account/config/secret change, Risk/Live change, manual order/close, history rewrite, qualification reset, or removal of completeness checks. A full100-row page stays funding_history_bound_reached, not falsely COMPLETE.
