# Domain 11: Background Jobs, Scheduling, and Recovery

This domain reconstructs how TickFlow and Aletheia create, admit, execute,
observe, cancel, time-box, persist, and recover background work.

## Included

- manual data jobs, scheduled pipelines, independent polling loops, and worker
  processes;
- lifecycle ownership from FastAPI startup through shutdown;
- single-flight, queue bounds, executor isolation, and overlap prevention;
- progress, stage logs, history, result publication, and frontend reconnect;
- cooperative cancellation, hard termination, timeout, stale reconciliation,
  and restart behavior;
- scheduler timezone, misfire, coalescing, maximum-instance, pause, and runtime
  ownership semantics;
- Aletheia's APScheduler lanes, bounded timeout workers, durable Agent runs,
  manual Agent queue, layered pause, and startup reconciliation.

## Excluded

- file and dataset commit atomicity, covered in Domain 9;
- realtime invalidation transport, covered in Domain 10;
- complete observability, security, packaging, and test strategy, covered in
  Domain 12;
- starting either application, triggering jobs, provider calls, fault
  injection, performance tests, or implementation.

## Reports

- `source-map.md`
- `background-jobs-analysis.md`
- `aletheia-comparison.md`
- `lessons.md`

## Evidence Boundary

The findings are static: current source, CodeGraph call paths, focused source
inventories, and existing comments. No scheduler, server, task, worker process,
provider, browser, database mutation, or test suite was run.
