---
name: Change Contract
about: Defines why a change is needed, what the current system state is, and how completion is verified.
labels: ""
assignees: ""
---

<!--
Read docs/governance/policies/documentation_governance.md before filling this out.

D0: implementation conformance repair — Contract Delta is NONE. Reference the existing Contract.
D1: externally observable behavior change — state ADDED / MODIFIED / REMOVED deltas.
D2: substantial design change — requires accepted docs/design/proposals/ Proposal first.

Delete sections that do not apply. For D0, delete the Contract Delta subsections.
-->

## Why

<!-- Describe the problem, violated invariant, or value. Do not describe the solution. -->

## Current Facts / Evidence

<!--
What current code path, failing test, log, DB/runtime observation, or Contract demonstrates
the problem? Do not substitute inference for evidence.
-->

## SDD Depth

<!--
Select one and explain:
- [ ] D0 — Implementation Conformance Repair (Contract Delta: NONE)
- [ ] D1 — Contract Behavior Change
- [ ] D2 — Substantial Design Change (requires accepted Proposal)
-->

## Contract Delta

<!-- D0: state the existing Contract / invariant this change repairs and write "Contract Delta: NONE". -->
<!-- D1/D2: complete all subsections below. -->

### ADDED

<!-- New approved behaviors. Delete if nothing added. -->

### MODIFIED

<!-- Changed behaviors: describe old → new semantic difference. Delete if nothing modified. -->

### REMOVED

<!-- Behaviors no longer approved. Delete if nothing removed. -->

## Non-goals

<!-- Explicitly list what this change does NOT modify. This is the hard scope boundary. -->

## Affected Authorities

<!--
List canonical authority documents that may need review or update:
- Contract(s) in docs/design/contracts/
- Product Truth / CAPABILITY_GROUND_TRUTH.md
- Decision(s) in docs/design/decisions/
- Proposal(s) in docs/design/proposals/
- Operations procedures
- Executable SpecClaims (tests/specs/)
-->

## Verification

### Executable Verification

<!-- Specific: test file, test case, claim ID, SpecClaim, static check, migration test. -->

### Runtime / Economic Verification

<!--
Only fill if runtime or economic qualification is required.
If not: "Not required — repository-level conformance change only."

Examples: paper observation, genuine outcome, restart/recovery, production consumer wiring,
realized PnL integrity, execution lifecycle, economic completeness, policy epoch qualification.
-->
