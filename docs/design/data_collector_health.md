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

Only `failed`, `timed_out`, and `skipped_budget` advance the consecutive problem
counter. A completed, partial, empty, disabled, or unconfigured result resets it.
At three consecutive problems the gateway emits a bounded in-process warning,
deduplicated for 30 minutes. Stored error values are short codes, never endpoint
responses, credentials, or exception messages.

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
  synchronous FastAPI route so SQLite work runs in the framework thread pool.
- `aletheia doctor`: `data.collectors` summarizes reporting, inactive, transient,
  repeated, and overdue domains.
- Overview / System / Collectors: 30-second read-only summary. Red means repeated
  or overdue degradation; yellow means a single transient problem; muted means
  initialization; green means no active operational problem.
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
