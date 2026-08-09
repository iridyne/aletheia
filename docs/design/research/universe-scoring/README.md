# Universe Scoring Design and Research

This directory is the durable reference pack for the minimum-money-loop
Universe score redesign. It collects dated research, exact curve evidence,
adversarial review, references, and implementation lessons without creating a
second source of behavioral authority.

## Authority boundary

- The accepted future design is the
  [Universe continuous scoring safety Proposal](../../proposals/universe_continuous_scoring_safety.md).
- Current Learning behavior remains owned by the
  [Learning contract](../../contracts/learning_contract.md).
- Current runtime authority remains `universe-step-v1`. Continuous point and
  pessimistic scoring now have a prospective Shadow comparison window, but no
  document here claims factor Learning, Paper influence, positive contribution,
  or Fedora deployment is active.

## Reading order

| File | Purpose |
| --- | --- |
| [01_current_state_and_research_2026-08-07.md](01_current_state_and_research_2026-08-07.md) | Current scorer audit, runtime evidence boundary, external research, and staged plan |
| [02_continuous_curve_specification_2026-08-07.md](02_continuous_curve_specification_2026-08-07.md) | Exact factor anchors, transforms, caps, hard gates, missing semantics, and property requirements |
| [03_adversarial_review_2026-08-07.md](03_adversarial_review_2026-08-07.md) | Implementer-versus-risk-reviewer challenge, corrections, residual risks, and task-2 verdict |
| [04_references_and_practical_notes.md](04_references_and_practical_notes.md) | Annotated primary references, public industry evidence boundaries, and reusable engineering lessons |
| [05_task3_pure_implementation_review_2026-08-07.md](05_task3_pure_implementation_review_2026-08-07.md) | Pure scorer boundary, safeguard coverage, adversarial self-review, verification evidence, and task-3 verdict |
| [06_task4_twin_wiring_review_2026-08-07.md](06_task4_twin_wiring_review_2026-08-07.md) | Step-authoritative in-memory Twin wiring, isolation proof, unavailable fields, performance baseline, and task-4 verdict |
| [07_task5_persistence_replay_review_2026-08-08.md](07_task5_persistence_replay_review_2026-08-08.md) | Bounded hourly Twin evidence, rejected high-volume draft, replay identities, capacity guards, and task-5 verdict |
| [08_task6_prospective_shadow_review_2026-08-08.md](08_task6_prospective_shadow_review_2026-08-08.md) | Prospective policy epoch, scanner-cluster assignment, full-candidate 4h outcomes, read-only evaluation, blockers, and task-6 verdict |

Read the Proposal first for the accepted direction, then file 02 when
implementing exact curves. Files 01 and 03 explain why the constraints exist;
file 04 is a reusable research and experience index. File 05 records what the
task-3 implementation proves and, equally importantly, what remains unavailable.
File 06 records the original task-4 wiring and authority-isolation proof. File
07 records the separately reviewed diagnostic persistence and replay layer.
File 08 records the prospective Shadow window and the evidence still required
before any Paper proposal.

## Maintenance rule

Do not silently rewrite dated evidence after runtime observations change. Add a
new dated report and update this router. When future behavior becomes current,
move its normative guarantees into the applicable Contract and leave this
directory as evidence and design history.
