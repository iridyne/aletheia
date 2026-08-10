# TickFlow Wave B-04 Serenity Attempt Truth Evidence — 2026-08-10

## Scope

This slice applies catalog items 40, 41, and the Serenity-specific part of 44.
It does not create a generic job platform or change Learning, Scheduler shutdown,
Risk, Execution Gateway, or Live behavior.

## Verified starting point

The cancel endpoint previously wrote running research as terminal `cancelled`
with `finished_at` immediately. The worker only observed that status between
pipeline stages and lens/provider calls, so physical work could continue while
the API and UI claimed it had ended.

Serenity already persisted run ID, stage, update time, artifact counts, and run
events. Startup recovery already reconciled queued/running rows after gateway
restart. The minimum truthful design therefore required status transitions and
CAS ownership, not a new table, queue, event bus, or progress framework.

## Implemented boundary

- Worker claim is `queued→running` compare-and-set.
- Queued cancellation is immediately terminal only when it wins that claim race.
- Running cancellation becomes active `cancel_requested` without `finished_at`.
- Worker completion, failure, and rate-limit writes require current `running`
  status and cannot overwrite a cancellation request.
- The outer worker crash path has a separate queued-only failure CAS, preventing
  a pre-claim database/bootstrap error from stranding an active queued row.
- Worker checkpoints acknowledge `cancel_requested→cancelled` and retain the
  last stage plus partial artifacts.
- Startup recovery acknowledges `cancel_requested` after the previous process is
  gone; genuinely interrupted queued/running rows retain their existing failed
  recovery outcome.
- Status, run-list, and detail responses expose existing durable `stage` and
  `updated_at` as `lastProgressAt`.
- The dashboard keeps `cancel_requested` active, shows its stage/elapsed time,
  and does not fetch terminal artifacts early.

## Focused evidence

- `tests/test_serenity_workbench.py`: queued cancellation blocks later claim;
  running cancellation remains unfinished and active; repeated requests are
  idempotent; completion CAS loses to cancellation; worker acknowledgement is
  terminal; stage and last-progress API facts remain visible.
- `tests/test_serenity_scheduler_continuity.py`: restart distinguishes an
  outstanding cancellation from interrupted queued/running work.
- Focused worker tests preserve normal success, generic failure, and rate-limit
  outcomes after introducing guarded terminal writes.
- Review regressions cover queued-only pre-claim failure and cancellation-event
  stage freshness after the accepted transition.
- The production frontend build proves the new active state is accepted by the
  existing Serenity page and shared status badge.

## Deliberate ceilings

No progress percentage was added: a durable stage name and last-progress time
are truthful, while the pipeline has no stable unit denominator for percentages.
No thread-kill mechanism was added: cooperative cancellation may wait for the
current provider call. Hard bounded cancellation belongs behind a future
process-owned worker only if measured stuck-provider behavior justifies it.

Catalog item 42, Learning attempt history, remains a separate domain. The
framework must not generalize Serenity state into all scheduled jobs before one
mutating Learning job is selected and its input watermark/outcome contract is
defined.
