---
name: Change Contract
about: Defines why a change is needed, what the current system state is, and how completion is verified.
labels: ""
assignees: ""
---

<!--
Read docs/governance/policies/documentation_governance.md before filling this out.

An Issue is a change contract, not a delivery roadmap. Keep the body focused on:
- the concrete problem or value;
- current facts/evidence;
- direct dependencies or activation conditions;
- approved behavior delta;
- hard scope boundaries;
- verification / closure.

Do not maintain numbered implementation stages, a prescribed work order, a "current stage",
or a future delivery sequence in the Issue body. If A depends on B, state that dependency
directly. Open a focused child only when future work becomes independently actionable.

D0: restore truth / conformance repair — existing Contract / invariant already
  defines target behavior; no semantic change.
D1: change semantics — behavior semantics change. State ADDED / MODIFIED / REMOVED
  deltas.
D2: change structure — authority ownership, mainline, subsystem topology, or product
  scope changes; requires accepted docs/design/proposals/ Proposal first.

Delete sections that do not apply. For D0, delete the Contract Delta subsections.
-->

## Why

<!-- Describe the problem, violated invariant, or value. Do not describe a multi-stage delivery plan. -->

## Current Facts / Evidence

<!--
What current code path, failing test, log, DB/runtime observation, or Contract demonstrates
the problem? Do not substitute inference for evidence.
-->

## Dependencies / Activation Conditions

<!--
List only direct prerequisites or conditions that materially gate this change.
Do not turn dependencies into a numbered roadmap or future-work sequence.
Delete this section when there are no material dependencies.
-->

## SDD Depth

<!--
Select one and explain:
- [ ] D0 — Restore truth / implementation conformance repair (Contract Delta: NONE)
- [ ] D1 — Change semantics
- [ ] D2 — Change structure (requires accepted Proposal)
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
