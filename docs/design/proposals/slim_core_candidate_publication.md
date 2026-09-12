---
id: slim-core-candidate-publication
status: accepted
github_issue: 3134
related_contracts:
  - ../contracts/decision_lifecycle_contract.md
  - ../contracts/serenity_contract.md
---

# Slim Core Candidate publication ownership

Status: accepted within the owner-approved [#3134](https://github.com/iridyne/aletheia/issues/3134) D2 convergence.

## Boundary and evidence

Universe refresh and Serenity promotion both persist `universe_snapshots` and
`universe_members` before the same `ensure_candidate_planning_generation` owner.
The existing public snapshot, single-member, batch-member and `insert_candidate`
alias expose separate write surfaces. Serenity source preparation/orchestration
also lives inside the generic repository and imports extension scoring there.

Their admission semantics are not interchangeable. Universe can retain analytical
members that do not acquire planning authority. Serenity requires exact research
and PA evidence and rolls the entire theme back on any failed publication. This
change does not turn either behavior into the other.

## Accepted bounded change

One Core repository entry point atomically persists a supplied snapshot and its
members, rejects mismatched snapshot membership, and joins the caller's atomic
unit through the existing transaction/savepoint mechanism. Universe, Serenity and
explicit development seed construction use this entry point. Delete the old
public insertion functions and Candidate alias; do not leave forwarding shims.
Tests that deliberately construct historical/partial rows use explicit low-level
fixtures rather than a production compatibility API.

Move Serenity source preparation and theme-level orchestration to its existing
logical trading-extension area, with no repository forwarding alias. Preserve
ordered request deduplication, complete source records and their stable hash,
outside-writer PA preparation, inside-writer source/owner recheck, score admission,
new-only counts, rollback, idempotency and post-commit wake semantics. Source
rules remain outside the Core persistence function. No plugin loader, callback
registry, alternate planning owner or execution path is introduced.

This is a bounded write-surface convergence, not proof that every Candidate
mutation or Core/Extension import in the repository has been classified.
PlanningGeneration remains owned by its existing exact-instrument implementation;
the surrounding source transaction still couples publication to its evidence.

## Verification and non-goals

Prove snapshot/member atomic rollback both standalone and nested, exact snapshot
membership, and absence of old production insertion surfaces. Exercise both
source callers, theme rollback/retry, source drift, deduplication, ineligible
Universe members and current planning ownership. Architecture fitness must name
the sole current snapshot/member creation function and reject bypass callers.

No schema rewrite, historical-row migration, selection/score/attention policy
change, new Core authority, physical order, model call, runtime mutation,
deployment or qualification is authorized by this batch. Scheme A physical roots
and complete #3134 fitness remain subsequent program work.
