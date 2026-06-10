# PI Operator Agent Design Crosswalk

Purpose: prove that the repo-specific dual-process design fully covers `C:\Users\Administrator\Downloads\aletheia_pi_agent_implementation_plan.md`, and identify the exact local design section that carries each requirement.

Primary local design sources:

- [2026-06-10-pi-operator-agent-dual-process-design.md](./2026-06-10-pi-operator-agent-dual-process-design.md)
- [../../OPERATOR_LAYER_DESIGN.md](../../OPERATOR_LAYER_DESIGN.md)

## Coverage Rule

“Covered” means one of the following is true:

1. the concept has an explicit section in the local dual-process spec,
2. the concept remains intentionally in the higher-level conceptual design doc and is referenced from the spec,
3. the concept is converted into a concrete repo track, API contract, storage contract, UI contract, or verification requirement.

Nothing in the reference plan is treated as implicitly understood.

## Section Crosswalk

| Reference section | Reference intent | Local coverage | Result |
|---|---|---|---|
| `0. Executive Summary` | define the top-level operator gap and target shape | dual-process spec `§1`, `§3`, `§5`, `§21`; conceptual framing in `OPERATOR_LAYER_DESIGN.md §1-§2` | Covered |
| `1. Product Intent` | define product role, not-chatbot positioning, PI rationale, OpenAlice relationship | dual-process spec `§1`, `§4`, `§5`, `§14`; conceptual framing in `OPERATOR_LAYER_DESIGN.md §3-§7` | Covered |
| `2. Current Problem Statement` | describe missing top-level operator in current system | dual-process spec `§3.2-§3.4` | Covered |
| `3. Core Architectural Model` | pyramid model and L1/L2/L3 responsibilities | dual-process spec `§4`, `§5`, `§7`, `§8`; conceptual layer model in `OPERATOR_LAYER_DESIGN.md §8-§9` | Covered |
| `4. Pi Agent Role Definition` | naming, modes, capabilities | dual-process spec `§5.1`, `§8.4`, `§12.1`, `§14`, `§15`, `§16` | Covered |
| `5. 24/7 Runtime Design` | always-on gateway, event-driven operator loop, heartbeat, gateway lifecycle, CLI | dual-process spec `§6`, `§8.6`, `§12.1`, `§19` | Covered |
| `6. Agent Shell Design` | shell responsibilities, prohibitions, internal modules | dual-process spec `§8.2-§8.5`, `§10.4`, `§15.4` | Covered |
| `7. App Context Model` | curated snapshots, safe context levels, no free file reads | dual-process spec `§9.1-§9.4` | Covered |
| `8. Command Registry` | typed intent, command flow, risk classes, registry table | dual-process spec `§10.1-§10.4`, `§15.5` | Covered |
| `9. Permission Model` | `ReadOnly/Research/PaperAuto/AllowEditing/Auto` | dual-process spec `§6.4-§6.5`, `§11.1-§11.4` | Covered |
| `10. Auto Repair Design` | low-risk auto repair, proposal-only repair, forbidden repair, repair decision object | dual-process spec `§15.1-§15.5` | Covered |
| `11. Command Center Design` | dedicated page, operator state, stream, timeline, recommendation panel, inspector, quick actions | dual-process spec `§14.1-§14.5`, `§17 Track 5-6` | Covered |
| `12. Session Management` | durable sessions, compaction, restart recovery | dual-process spec `§8.5`, `§12.3`, `§13.3-§13.5` | Covered |
| `13. Event and Activity Logging` | event schema, event types, dashboard timeline | dual-process spec `§12.4-§12.5`, `§14.4`, `§18.3` | Covered |
| `14. Data Models` | agent state, operator task, command intent, repair proposal, operator decision | dual-process spec `§10.3`, `§12.1-§12.3`, `§13.2`, `§15.5` | Covered |
| `15. API / IPC Design` | local operator API, backend command API, event streaming | dual-process spec `§7.2`, `§12.5` | Covered |
| `16. Operator Loop Pseudocode` | recurring cadence and wake-up logic | dual-process spec `§8.6` | Covered |
| `17. Safety Invariants` | no secret access, no live unlock, no risk bypass, safe recovery | dual-process spec `§2`, `§5.3`, `§7.3`, `§9.4`, `§10.4`, `§11`, `§15.4`, `§18.1` | Covered |
| `18. Implementation Roadmap` | phased realization from audit -> heartbeat -> shell -> registry -> PI runtime -> loop -> repair -> UI -> knowledge -> lifecycle -> tests | dual-process spec `§17`, `§19` | Covered |
| `19. Testing Matrix` | unit/integration/failure/safety tests | dual-process spec `§18.1-§18.4` | Covered |
| `20. Implementation Instructions for Claude Code / AI Agent` | sequencing guardrails for later implementation | dual-process spec `§17-§19`, plus next-step gate: spec review before implementation planning | Covered |
| `21. Non-Goals` | do not expand scope into unrelated subsystems | dual-process spec `§20` | Covered |
| `22. Final Product Behavior` | user-facing end-state experience | dual-process spec `§5`, `§14`, `§19`, `§21` | Covered |
| `23. One-Sentence Architecture` | concise architecture statement | dual-process spec `§21` | Covered |

## Repo Translation Summary

The reference file is now split into three local layers:

1. **Conceptual product intent**
   - `docs/OPERATOR_LAYER_DESIGN.md`
   - why the operator exists, how it sits over the trading core, and why `Ctrl+K` is not the full product surface

2. **Repo-specific dual-process architecture**
   - `docs/superpowers/specs/2026-06-10-pi-operator-agent-dual-process-design.md`
   - exact process model, APIs, permissions, storage, supervision, UI, recovery, and rollout

3. **Execution planning**
   - not written yet
   - starts only after the design spec is reviewed and approved

## Remaining Design Risks

These are not missing sections; they are the remaining design-phase risks to be checked before implementation planning:

1. The current docs outside the spec must point to the new local source of truth rather than stale external paths.
2. The older Stage 1 bootstrap note in `CURRENT_TASK.md` must not conflict with the final dual-process decision.
3. The next implementation plan must preserve the split between:
   - operator permission vs worker permission
   - Node reasoning runtime vs Python command execution authority
   - Command Center page vs `Ctrl+K` launcher

## Completion Standard For Design Phase

The design phase is only considered locally complete when all of the following are true:

1. the conceptual design exists,
2. the repo-specific dual-process spec exists,
3. the reference-plan crosswalk exists,
4. roadmap and handoff docs point to the final local design source,
5. there is no conflicting stale local design note that would cause implementation drift.
