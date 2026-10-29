## Linked Issue / Change Contract

Closes #3391. References #3134 and post-merge #3390 finding4070984420.

## SDD Depth

D0 — restore the existing machine-readable projection of controlled paths.

## Contract Delta

NONE. The canonical Safety guide and trust/change-control policy already designate core/aletheia. Add that physical prefix to the same eight YAML entries; retain boundary IDs, focused commands, two Exchange Extension paths and every restriction.

## Does this change alter semantics or structure?

Neither. This is one-file conformance repair after the accepted physical cutover, not a new structural decision.

## Contract convergence

- [x] Existing TB-S03/TB-S04/TB-S05 reproduced 3 failed / 3 passed before repair.
- [x] Unchanged trust-boundary specs plus architecture guards: 173 passed in 15.64s after repair.
- [x] Documentation governance and diff check passed; independent exact-diff review found no blockers.
- [x] No test or policy criterion weakened.
- [ ] Runtime/economic proof remains tracked by #3134; no such proof is claimed here.

## Non-goals / out-of-scope

No executable source, runtime, credentials, Risk/economics, release/tag, deployment or qualification-window changes. Local focused validation only; remote CI is retired. This corrective PR does not reopen merged #3390.
