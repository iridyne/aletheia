# Data Collector Health

## Purpose

There are two bounded scheduler lanes, each with a 40-second wall-clock budget
inside a 45-second job timeout. `data_collectors` runs primary-venue orderbook,
Funding and OI every two minutes. `data_collector_context` runs volume ranking,
Hyperliquid, DefiLlama, CoinGecko, macro, Coinalyze, and best-effort derivatives
history every ten minutes. The second lane starts with a new deadline, so a slow
primary source cannot consume the time reserved for Hyperliquid or DefiLlama.

The shared `io` executor remains single-threaded: primary-exchange CCXT work is
still serialized. Within the context lane, only independent public HTTP sources
wait concurrently; SQLite writes remain serial. This avoids a global concurrency
or budget increase while preventing the reported queue starvation.

## State model

`collector_health_state` stores exactly one row per known domain. It is bounded by
the `domain` primary key and contains latest state plus cumulative counters. It is
operational telemetry, not trading audit or causal-chain state.

Latest statuses:

| Status | Meaning |
|---|---|
| `success` | The call completed and every returned row owned by the scheduler was persisted. |
| `partial` | Useful data arrived together with source-level errors. |
| `empty` | The call completed honestly with no rows. |
| `failed` | The collector raised or reported only errors. |
| `timed_out` | Its per-domain timeout expired. |
| `skipped_budget` | It was not started because less than two seconds remained. |
| `not_configured` | A required key or observed instrument set is absent. |
| `disabled` | The owner disabled the source in settings. |

Only `failed` and `timed_out` advance the consecutive problem counter.
`skipped_budget` records a scheduling fact without blaming the source; any other
status resets the counter. At three consecutive problems the gateway emits a
bounded in-process event, deduplicated for 30 minutes. It is a warning when
external connectivity is reachable or unknown, but becomes an informational
`collector_problem_dependent_connectivity` consequence during an active or
recovering connectivity incident. Stored error values are short codes, never
endpoint responses, credentials, or exception messages.

The connectivity monitor probes neutral public endpoints independently from the
configured exchange. `GET /api/status` exposes the normalized state as
`external_connectivity`: `reachable`, `unavailable`, `recovering`, or `unknown`,
with operator severity, priority, message, action, and observation time. A failed
exchange probe does not declare a global outage when either neutral endpoint is
reachable; conversely, an exchange success proves egress if a neutral endpoint is
temporarily unavailable.

The Collector report consumes that normalized state rather than inferring a
network outage from a pattern of source failures. During `unavailable`, only
Collector failures observed around the current outage are attributed as dependent
consequences and receive `display_severity=info`. Failures predating the outage
remain independent warnings. Raw status and counters are never rewritten.

After a failed probe becomes reachable, pre-recovery Collector failures remain
attributed to one `external_connectivity_recovering` incident for at most two
context cadences. This tells the operator that no restart is required while the
slower domains wait for their next scheduled run. Raw `last_status`, counters,
and error codes remain unchanged for auditability; `display_status` and
`attributed_cause` are presentation fields only.

For the primary derivatives domain, collector-call success is deliberately
deferred until funding/OI rows reach SQLite. If either repository write fails,
the domain is `failed` with a stable `derivatives_*_persist_failed` code and the
`data_collectors` job records a partial failure. A fetched-but-unpersisted row
is never reported as healthy data.

Optional LSR enrichment has its own four-second local cap, and derivatives
history runs only at the end of the context lane. Neither may consume the live
Funding/OI collection budget.

## Pause and no-exchange behavior

Trading pause must not stop public-data accumulation. Both collector lanes are
essential layered-pause jobs, so their task entries do not exit merely because
`runtime.paused=true`. A full scheduler pause still prevents job dispatch.

When no observed instruments exist, symbol-dependent domains are marked
`not_configured`; symbol-independent CoinGecko, macro, and DefiLlama collection
continues on the normal context-lane cadence. This preserves research supply on a
local-paper installation with no configured execution exchange.

## Operator surfaces

- `GET /api/market/collector-health`: full per-domain status and counters. It is a
  synchronous FastAPI route so SQLite work runs in the framework thread pool. A
  fresh external-network cause is returned once as `infrastructure_incident`;
  affected domains are marked `blocked_by_connectivity` or `waiting_for_retry`
  and carry informational display severity.
- `aletheia doctor`: `data.collectors` summarizes reporting, inactive, transient,
  repeated, and overdue domains.
- Overview / System shows Connectivity before Exchange and Collectors. An external
  outage is the priority-one warning; dependent Collector rows are secondary
  informational consequences. When connectivity is reachable, independent
  repeated or overdue Collector degradation remains a warning. Recovery is a
  priority-two informational state while affected jobs wait for their normal retry.
- `GET /api/market/data-coverage` and Overview / System / Data coverage report
  what is actually present in SQLite: row count, symbol count, source count, newest
  observation, and freshness. This is intentionally separate: a successful empty
  collector call and a fresh populated table are different facts.

Overdue means no scheduled update for more than three expected cadences. Disabled
and unconfigured domains are never declared overdue.

## Non-goals

- No automatic global budget increase, retry storm, or CCXT concurrency increase.
- No change to market values, signal normalization, opportunity filters, risk, or
  execution.
- No append-only collector event history. Detailed failures remain in bounded logs
  and infrastructure events.
