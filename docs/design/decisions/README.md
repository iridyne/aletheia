# Architectural Decisions

This folder contains durable architectural decisions (ADR-style).

## Purpose

Record **why** Aletheia made a long-lived design choice. Decisions preserve context,
alternatives, consequences, and evolution history.

This is not a replacement for contracts:

- `decisions/` answers **why this direction was chosen**.
- `contracts/` defines **what the system must do now**.
- `proposals/` describes a substantial future design without claiming it exists.
- GitHub Issues and PRs carry implementation progress.
- Code and executable specs prove local behavior; Product Ground Truth decides support.

## When to create a decision

Create a decision when changing:

- trust boundaries or agent authority
- subsystem ownership boundaries
- data authority or semantic definitions
- long-lived architecture direction
- experiment/learning methodology
- choices that future agents may otherwise reconsider repeatedly

Do not create one for routine bug fixes, refactors, renames, or ordinary implementation details.

## Lifecycle

Each decision should have:

- stable `ADR-NNNN` id
- status (`proposed`, `accepted`, `rejected`, `superseded`)
- links to affected contracts
- supersession links when replaced

Historical decisions are preserved. New decisions should not rewrite old reasoning.
