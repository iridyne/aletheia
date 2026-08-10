# TickFlow Wave B-05 Learning Attempt and Position Query Identity Evidence — 2026-08-10

## Scope

This slice completes catalog item 42 for one mutating Learning job and pilots
items 11, 12, and 15 for one frontend domain. It does not create a generic job
platform, change Learning authority, migrate all React Query keys, or change the
WebSocket/REST authority boundary.

## Learning attempt contract

`lesson_reviewer` was selected because it mutates durable lesson state and its
daily catch-up already has a canonical CST window. Each admitted run now writes
one `learning_lesson_reviewer_attempts` row with:

- a unique attempt ID and frozen daily input watermark;
- start and finish timestamps plus `running`, `succeeded`, or `failed` outcome;
- bounded error class/message on failure;
- `retry_of_attempt_id` linking the next run in the same window to the latest
  failed attempt.

Attempt start/finish and the existing `learning_job_health` snapshot are written
in the same SQLite transaction. A gate-level skip creates no attempt because no
work was admitted. Existing promotion gates, lesson review behavior, schedules,
and successful-window idempotency remain unchanged.

## Positions query/change contract

`positionKeys` is the sole frontend identity for the positions list and
reconciliation query. Existing positions queries and mutation invalidations now
reuse it. The `positions` and `fills` WebSocket categories are type-checked
against that identity while retaining the existing portfolio and equity-curve
fan-out.

WebSocket invalidation remains primary. Mounted positions views use a 30-second
REST polling fallback, 28-second stale threshold, and no background-tab polling.
Command Center retains its existing 10-second active-cockpit override. No other
query family was migrated in this pilot.

## Focused evidence

- `tests/specs/spec_learning_continuity.py` proves a failed reviewer attempt is
  classified and the next successful attempt in the same window links to it.
- `tests/test_migration_fast_path.py` proves the schema ledger advances through
  v21 and remains idempotent.
- `frontend/src/lib/useRealtimeUpdates.test.ts` proves positions/fills still
  deduplicate to one positions prefix and the fallback policy is bounded.
- The production frontend build proves all migrated call sites type-check and
  bundle without changing route behavior.

## Deliberate ceilings

Attempt history is intentionally reviewer-specific; another job must prove the
same fields and retry semantics before any shared scheduler abstraction exists.
The query registry is intentionally positions-only; the rest of the literal
keys remain candidates, not hidden work completed by this slice.
