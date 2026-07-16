# Active Universe OHLCV Catch-up

## Problem Boundary

Universe composition changes every few minutes, while the bulk historical-data
job runs nightly. A newly selected exact instrument can therefore be active
before its local 1h/4h history reaches the analysis minimum. The repair must not
run inside `UniverseService.refresh()`: exchange history latency must never delay
snapshot publication, opportunity emission, or outcome registration.

This subsystem changes data availability only. It does not change Universe
ranking, filters, risk limits, sizing, execution, credentials, or live state.

## Design

`ohlcv_active_backfill` is an independent scheduler job. Each cycle:

1. Resolve all active members to `InstrumentId(exchange, symbol, market_type)`.
2. Compare exact 1h and 4h coverage with `min_bars_for_backtest`.
3. Exclude pairs whose durable `next_attempt_at` is still in the future.
4. Prioritize 1h, then member rank, and process at most the configured pair limit.
5. Reuse `history_downloader`, which locks fallback to the same venue and stores
   full instrument identity.
6. Persist `ready`, `partial`, or `failed` state. Partial history retries after
   one hour; failures use 15-minute exponential backoff capped at six hours.

Each pair has a 60-second timeout and the cycle has a 240-second internal
budget, both below the scheduler's 300-second outer guard. A slow venue request
therefore cannot starve every later pair or abandon the scheduler worker.

The nightly job remains the bulk completeness fallback. The active job is a
bounded freshness repair, not a second unlimited downloader.

## Failure Semantics

- Raw provider errors are not persisted. State stores only stable categories
  such as `provider_fetch_failed`, `download_exception`, or `no_data`.
- Three consecutive pair failures emit a deduplicated infrastructure warning.
- One failed pair cannot block other members: a future retry time removes it
  from planning until its backoff expires.
- A newly listed instrument with some, but fewer than the required bars, is
  `partial` rather than falsely `ready` or permanently failed.
- Restart safety comes from SQLite state, not an in-memory queue.
- Retry rows not touched for 90 days are pruned as operational telemetry.

## Operator Controls

Settings exposes:

- `active_universe_backfill_enabled`
- `active_universe_backfill_interval_minutes` (5–60)
- `active_universe_backfill_max_pairs_per_run` (1–10)

`doctor` reports exact active gaps and the associated catch-up states. Disabling
automatic catch-up restores the explicit manual `aletheia data backfill` path.

Coverage is deliberately independent from `execution_eligible`: historical data
may help an active member become diagnosable, but this job never writes or
overrides trading eligibility.
