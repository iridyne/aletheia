# Data Collector Health

## Purpose

The `data_collectors` scheduler job intentionally has a 40-second wall-clock
budget inside its 45-second job timeout. That bound prevents a stalled public
endpoint from abandoning scheduler threads, but it also means a later domain can
be skipped when earlier calls are slow. A running scheduler alone therefore does
not prove that every data domain is being supplied.

This design makes that distinction visible without increasing network budgets,
collector concurrency, or trading authority.

## State model

`collector_health_state` stores exactly one row per known domain. It is bounded by
the `domain` primary key and contains latest state plus cumulative counters. It is
operational telemetry, not trading audit or causal-chain state.

Latest statuses:

| Status | Meaning |
|---|---|
| `success` | The call completed and returned useful data. |
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

## Pause and no-exchange behavior

Trading pause must not stop public-data accumulation. `data_collectors` is an
essential layered-pause job, so its task entry no longer exits merely because
`runtime.paused=true`. A full scheduler pause still prevents job dispatch.

When no observed instruments exist, symbol-dependent domains are marked
`not_configured`; symbol-independent CoinGecko, macro, and DefiLlama collection
continues on the normal Phase B cadence. This preserves research supply on a
local-paper installation with no configured execution exchange.

## Operator surfaces

- `GET /api/market/collector-health`: full per-domain status and counters. It is a
  synchronous FastAPI route so SQLite work runs in the framework thread pool.
- `aletheia doctor`: `data.collectors` summarizes reporting, inactive, transient,
  repeated, and overdue domains.
- Overview / System / Collectors: 30-second read-only summary. Red means repeated
  or overdue degradation; yellow means a single transient problem; muted means
  initialization; green means no active operational problem.

Overdue means no scheduled update for more than three expected cadences. Disabled
and unconfigured domains are never declared overdue.

## Non-goals

- No automatic budget increase, retry storm, or concurrency increase.
- No change to market values, signal normalization, opportunity filters, risk, or
  execution.
- No append-only collector event history. Detailed failures remain in bounded logs
  and infrastructure events.
