# NNNN — Short title: design

Answers [spec.md](spec.md). Every section cites the requirement ids it serves.

## Overview

The flow in one diagram and a few sentences.

## Data model

Durable state: tables or files, keys, append-only rules, migrations (additive only).

## Components and flow

One subsection per component: input, output, owner module, requirement ids.

## Failure semantics

What happens on each failure; what is unavailable vs zero; what fails closed.

## Trust and complexity review

Trust boundaries touched, authority added or removed, what this deletes (one implementation
per capability), and why a smaller change is insufficient.

## Alternatives considered

Each alternative, including competitor approaches, and why it was not chosen.

## Traceability

| Requirement | Design section | Verification | Evidence type |
|---|---|---|---|
| XX-R01 | | `tests/specs/spec_<name>.py::XX-R01` | deterministic test |

## Rollout and rollback

How the change is adopted safely and the condition that returns the prior behavior.
