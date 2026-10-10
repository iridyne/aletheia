---
id: slim-core-options-data-compartment
status: implemented
github_issue: 3134
---

# Options Data compartment

Implement the accepted #3134 Data ownership boundary: Deribit acquisition, GEX
parsing and observation-only IV derivation belong to the optional Data Extension.
Move the existing collector and derivation functions there. Preserve the existing
`OptionsVolatilitySnapshot` DTO and version identifiers in Core because canonical
repository validation and historical export consume them without acquisition.

No forwarding function, alias, new Port or second implementation remains. The
existing scheduler composes the collector and retains persistence/cadence. Preserve
same-response IV/GEX reuse, calculation order, clocks, tenor/delta rules, finite
checks, source provenance, cutoffs and observation-only authority. No new API
request, schema migration, credentials or runtime permission is introduced.

Verify the existing calculations and GEX-equivalence tests, function-body/DTO AST
equivalence except import targets, scheduler persistence, context-lane absence and
fresh-process stored DTO/write/read behavior with all Extensions forbidden.
This physical boundary is not online Deribit qualification or full Scheme A.
