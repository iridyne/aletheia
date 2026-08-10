---
id: serenity-research-attempt-truth
status: implemented
related_contracts:
  - serenity_contract
supersedes: []
---

# Serenity Research Attempt Truth Proposal

## Summary

Serenity research cancellation must describe the physical worker truth. A
running worker is not terminal merely because the API accepted a cancellation
request. This proposal adds a cooperative `cancel_requested` state, worker
acknowledgement, guarded terminal transitions, and restart reconciliation for
the existing Serenity run only.

## Current fact

`POST /api/serenity/runs/{id}/cancel` currently writes `cancelled` and
`finished_at` immediately for both queued and running runs. The background
worker checks the durable run status only between pipeline stages and LLM lens
calls. A running LLM or I/O call may therefore continue after the run is shown
as terminal.

The run already persists `stage`, `updated_at`, and ordered run events, but the
status summary and run-list responses do not expose the stage or last progress
time. Startup recovery marks queued and running rows failed; it does not
recognize a cancellation request waiting for worker acknowledgement.

## Goals and non-goals

Goals:

- queued cancellation is terminal only if the worker has not claimed the run;
- running cancellation remains active as `cancel_requested` until the worker
  acknowledges it;
- completion, failure, and rate-limit writes cannot overwrite a concurrent
  cancellation request;
- restart recovery acknowledges an outstanding cancellation after the old
  process and worker are gone;
- current stage and last progress time are visible without adding fake percent
  completion.

Non-goals:

- no thread killing, task interruption, process isolation, or cancellation of
  an in-flight model-provider request;
- no generic scheduler/job state machine;
- no schema migration, new progress table, or append-only attempt platform;
- no Learning-job, Risk, Execution Gateway, Live, or trading behavior change.

## Proposed design

The durable lifecycle is:

| Current state | Action | Next state | Terminal |
|---|---|---|---|
| `queued` | cancellation wins before worker claim | `cancelled` | yes |
| `queued` | worker compare-and-set claim | `running` | no |
| `running` | API cancellation request | `cancel_requested` | no |
| `cancel_requested` | worker reaches a cancellation checkpoint | `cancelled` | yes |
| `cancel_requested` | gateway restart proves old worker is gone | `cancelled` | yes |
| `running` | guarded pipeline outcome | existing terminal outcome | yes |

The repository owns compare-and-set transitions. A worker terminal write is
valid only while the row is still `running`. If a cancellation request won the
race, the worker records `cancelled` instead. Repeated cancel calls for
`cancel_requested` or `cancelled` are idempotent.

The existing run ID is the attempt identity and progress generation. Existing
`stage`, `updated_at`, and run events provide durable progress and replay facts;
API responses expose them as `stage` and `lastProgressAt`. No percentage is
claimed because stage durations vary and the pipeline has no measured unit
denominator.

## Trust and complexity review

The run row remains the lifecycle authority. A second in-memory cancellation
registry would be lost on restart and is unnecessary. SQLite compare-and-set
updates resolve claim, completion, and cancellation races without a new lock or
schema.

Cooperative cancellation can wait for the current provider call to return. The
UI must continue to show `cancel_requested` as active; the system must not claim
that thread capacity has been released. Hard interruption would require a
separately owned process boundary and is outside this proposal.

## Verification contract

Focused evidence must prove:

1. queued cancellation becomes terminal and prevents a later worker claim;
2. running cancellation returns `cancel_requested` without `finished_at`;
3. a guarded completion cannot overwrite `cancel_requested`;
4. worker acknowledgement writes terminal `cancelled` and its event;
5. restart recovery turns `cancel_requested` into `cancelled`, while genuinely
   interrupted queued/running work remains failed;
6. status/detail/list responses expose stage and last progress time;
7. the frontend keeps `cancel_requested` active and does not request terminal
   artifacts early.

## Rollout and rollback

This is an additive status value in an unconstrained TEXT column, so no data
migration is required. Existing queued, running, cancelled, and terminal rows
remain readable. Rollback may leave a `cancel_requested` row unknown to older
code; startup recovery or a one-row operator repair would be required before
running that older version.

## Resolution

The owner granted standing approval for the recommended low-risk TickFlow
integration sequence. The implementation is limited to Serenity research
attempt truth and passed its focused backend, restart, and frontend checks.
Normative behavior now lives in the Serenity Lens Contract.
