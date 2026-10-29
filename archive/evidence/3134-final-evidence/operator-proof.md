# #3134 final operator / Demo evidence protocol

Prepared by read-only source inspection of the current Core-layout worktree on
2026-09-22. No runtime request, browser action, source edit, or deployment was
performed for this protocol. Source facts are not runtime proof.

## Preconditions and limits

Run only after exact merged-head deployment/health checks and under the already
approved runtime authority. Use Fedora loopback, normal existing authorization,
and no credential reads/auth bypass. A 401/403 stops the affected check. Do not
print full response bodies, event contents, cookies, WebSocket tickets, or HARs.

Maximum 10 owned SSE holders; each lifetime at most 15 seconds; entire capacity
experiment at most 30 seconds. Preserve user streams: require initial
`proxy_capacity.stream_admissions == 0` and `streaming == 0`, otherwise postpone
or account explicitly for existing ownership rather than disconnecting it.
All cancellation targets must be owned handles, never global kill or PID guesses.

## Actual source owners

- `core/aletheia/gateway/frontdoor.py:197-217`: finite pool 20, stream pool 10,
  stream pool acquisition timeout 0.1 seconds; separate HTTP clients.
- `frontdoor.py:301-358`: rejected stream admission yields HTTP 503,
  `reason=frontdoor_stream_capacity_exhausted`, `retry_after_sec=3`,
  `Retry-After: 3`, `X-Aletheia-Degraded=frontdoor_stream_capacity_exhausted`.
- `frontdoor.py:374-393,469-481`: `GET /_infra/status` exposes
  `proxy_capacity.{max_connections,stream_max_connections,stream_admissions,
  pending_headers,streaming,non_sse,active_requests,truncated}`. Project only
  these counters; `requests` and unrelated events need not be emitted.
- `core/aletheia/server/routes/operator.py:1023-1049`: stream is
  `GET /api/operator/events/stream?limit=0`; `once` must remain false. SQL LIMIT 0
  produces no backlog/events (`agent_shell/operator_store.py:834-862`), with
  keep-alive comments still sustaining the stream. Discard the body regardless.
- `core/aletheia/server/routes/health.py:187-199`: `/api/ready` is event-loop
  liveness, not trading health. Frontdoor `/_infra/readyz` and backend
  `/_infra/readyz` are distinct readiness observations.
- `tests/test_frontdoor_stream_capacity.py::test_sse_pool_isolated_and_admission_reusable`
  is the existing real-loopback TCP/httpx regression (2-slot scaled fixture).

## Minimal live proof

1. Snapshot baseline counters and exact `/api/version` `boot_commit`, `pid`,
   `version`; compare boot commit with the independently observed merged target.
2. Open 10 owned `limit=0` streams, each with its own strict 15-second deadline.
   Wait within the total deadline for `stream_admissions=10`, `streaming=10`.
3. Request an eleventh stream with a two-second deadline. Require the exact 503
   reason and retry fields above. In parallel observe finite `/api/status`,
   frontdoor `/api/ready`, frontdoor `/_infra/readyz`, and backend
   `http://127.0.0.1:8788/api/ready`: HTTP 200 within the bounded two-second probe.
   Record status/duration only; 200 alone does not prove healthy trading state.
4. Cancel exactly one owned holder; observe admissions/streams fall to 9. Open
   one replacement within the same original overall deadline; observe 10 again.
5. Finally close every owned handle, regardless of failures; require counters
   return to baseline. Do not leave detached curl processes or browser tabs.

One safe finite-probe command (execute on Fedora, not macOS):

```sh
curl --silent --show-error --max-time 2 --output /dev/null --write-out 'status=%{http_code} elapsed=%{time_total}\n' http://127.0.0.1:8788/api/ready
```

## Actual browser reconnect, separate from the counter test

`frontend/src/pages/CommandCenter.tsx:514` owns
`connectEventStream('/api/operator/events/stream?limit=100', ...)`.
`frontend/src/lib/eventStream.ts` retries CLOSED sources with one timer at
3/6/12/24/30 seconds, resets to 3 seconds on successful open, leaves native
CONNECTING retry alone, and cancels on disposal. EventSource callbacks do not
expose HTTP status; observe status via safe browser network metadata.

Use an owned CommandCenter tab only after filling the 10 owned stream slots.
Observe its real stream HTTP 503, then release one owned holder before its first
three-second retry; require a new HTTP 200 stream without page reload. Dispose
the owned tab and all remaining holders within their limits. Keep Overview in
a separately owned tab to observe finite bootstrap responsiveness under load.
Never reclassify an existing user stream as an owned test holder.

**Overview does not own an EventSource.** It polls
`/api/ui/shell-bootstrap` (10 seconds) and `/api/ui/overview-bootstrap`
(15 seconds), with App-level `useRealtimeUpdates` WebSocket `/api/ws` separately.
Do not claim Overview exercised SSE reconnection or record its ticket query.

The full retry sequence requires at least 75 seconds and is incompatible with
the 15-second holder / 30-second experiment cap. Its complete schedule and
single-timer/disposal semantics are already tested with fake clocks in
`frontend/src/lib/eventStream.test.ts`; live proof here is only 503 → first
3-second retry → 200. Do not lengthen runtime saturation for cosmetic parity.
These checks prove present behavior, not the historical September 12 incident's
root cause or retroactive attribution.

## Natural Demo lineage and central outcome judgment

Use only naturally generated Bitget Demo work from the exact new boot session.
No manual orders, closes, parameter tuning, qualification resets, or writes.

- `GET /api/analytics/operator-decisions?hours=1&limit=50` exposes per-item
  `opportunity_id`, `proposal_id`, `decision_id`, `decided_at`, `action`, and
  `attribution`; its owner is `server/routes/analytics.py:1873-2196`.
  Trace exact IDs rather than treating a limited latest page as proof of absence.
- `GET /api/analytics/opportunity-trace/{opportunity_id}` delegates to
  `analytics/opportunity_flow.py::trace_opportunity_flow`; exposes persisted
  Risk/Proposal/Order/Position stages. Despite its route docstring mentioning
  fills, inspected reconstruction currently has no separate Fill event. Obtain
  actual Fill evidence separately; do not infer private fills from order status.
- `storage/models.py`: Proposal/Order/Fill/Position carry `runtime_session_id`
  and `producer_commit`; Order links `proposal_id`, `risk_decision_id`,
  `entry_plan_id`; Position opening identity is distinct from
  `closed_runtime_session_id`, `closed_producer_commit`,
  `closed_behavior_fingerprint`. A pre-deploy entry closed by the new process
  proves recovery only, not a complete new-build entry/exit cohort.
- Read SQLite using `mode=ro`, `PRAGMA query_only=ON`, explicit output fields,
  and bound exact IDs/session values. Do not emit full rows/reasoning/manifests.
  Prove Candidate/PlanningGeneration/EntryPlan → Proposal → approved Risk →
  Order → broker Fill → Position → natural close using existing persisted IDs;
  missing links remain unavailable, never backfilled during diagnosis.
- `analytics/outcome_integrity.py::raw_account_events` accepts `db=` and
  `runtime_session_id=`, filters before LIMIT with canonical instant comparison.
  `read_complete_outcome_cohort(..., db=ro_connection)` reads a bounded session
  cohort to exhaustion and returns `cohort_complete`, `cohort_state`, source
  and loaded counts, digest, rows/assessments. Project fields, not raw contents.
- `assess_reportable_outcome(row, db=ro_connection)` is the central eligibility
  predicate: verified entry/exit, complete fees/funding, PnL reconciliation,
  attribution and current clean-window restrictions. `outcome_eligible=true`
  or non-null PnL alone is insufficient. Return `eligible`, `reason`, outcome
  identity alongside exact opening/closing build identity. Existing helper
  internal SELECTs are not permission to print complete rows.

No naturally closed, cost-complete new-session outcome means runtime evidence
is still pending. Do not manufacture a code defect or start another refactor.
