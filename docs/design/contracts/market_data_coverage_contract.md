# Market Data Coverage and Collector Health Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `MC` · **Owner code:** `core/aletheia/data/data_coverage.py::get_data_coverage_report`, `core/aletheia/data/collector_health.py::record_collector_status`

## Scope

This contract owns the operator-facing distinction between collector execution health (did
each scheduled collector run, and how did it finish) and persisted market-data coverage
(what retained data is actually present and recent in SQLite). Both are diagnostic inputs
for operators and the Overview page around the trading mainline; neither defines trading,
business or infrastructure readiness, and neither authorizes a strategy, Risk decision, order
or Live operation. Ticker and `MarketSnapshot` value validity belong to the
[OHLCV Provider Result Contract](ohlcv_provider_result_contract.md#ticker-results).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/server/routes/data.py::get_market_collector_health` | API route `GET /api/market/collector-health` | Overview / System collector row |
| `core/aletheia/server/routes/data.py::get_market_data_coverage` | API route `GET /api/market/data-coverage` | Overview / System coverage row |
| `core/aletheia/data/collector_health.py::record_collector_status` | call | `core/aletheia/agent_runtime/scheduler.py` collector dispatch |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `collector_health_state` (one latest row per domain in `COLLECTOR_CADENCE_SEC`) | `core/aletheia/data/collector_health.py::record_collector_status` | `get_collector_health_report`, `core/aletheia/data/derivatives_facts.py::derivatives_readiness` | replaceable latest row; counters monotonic |
| `collector_health_state.derivatives_coverage_json` | `record_collector_status` (from the `derivatives_comparison` collector result; kept from the previous row when absent) | `derivatives_readiness` (per-venue listing/attempt facts) | replaceable |
| `orderbook_snapshots`, `funding_rate_snapshots`, `open_interest_snapshots`, `liquidation_events`, `macro_snapshots`, `onchain_snapshots`, `market_ohlcv` | their collectors | `get_data_coverage_report` (read-only census) | not written by this capability |
| in-process coverage cache | `get_data_coverage_report` | the coverage route | replaceable, 30-second TTL |

## Requirements

### MC-C01 Collector health reports execution facts only

`When a scheduled collector finishes a turn, the collector health writer shall record the latest raw status, cadence, attempt/success timestamps and problem counters for that domain, and the report shall expose overdue state and independently established connectivity attribution.`

- WHEN a collector completes with `empty` THEN the status is recorded as an honest completed outcome, not a failure.
- WHEN a domain is not scheduled for more than three cadences THEN it is `overdue`; WHEN it is scheduled but only records `skipped_budget` for more than three cadences THEN it is `starved` (from `last_attempt_at`), with raw status and counters unchanged.

Evidence: `core/aletheia/data/collector_health.py::record_collector_status`, `get_collector_health_report`.

### MC-C02 Connectivity attribution never rewrites raw facts

`While a confirmed shared external-connectivity incident is unavailable or recovering, the collector health report shall group dependent collector failures under that incident as presentation metadata without converting them into source success or rewriting the stored raw status.`

- WHEN connectivity is `unavailable` and a collector failed THEN the row is shown `blocked_by_connectivity` with `dependent_on="external_connectivity"` while `last_status` stays `failed`.

Evidence: `core/aletheia/data/collector_health.py::_infrastructure_incident`.

### MC-C03 Coverage classifies every domain

`The coverage report shall return, for each of orderbook, primary funding, primary open interest, long/short ratio, liquidations, macro, on-chain and OHLCV, the retained row, symbol and source counts, newest observation, age, freshness limit, criticality and exactly one state of fresh, stale, empty, invalid or idle.`

- WHEN a table has no rows THEN the state is `empty` (or `idle` for optional event domains).
- WHEN the newest timestamp is malformed THEN the state is `invalid`.
- WHEN decision-critical data is older than its limit THEN `stale`; optional event data (long/short ratio, liquidations, on-chain) that is old or absent is `idle`.

Evidence: `core/aletheia/data/data_coverage.py::DOMAIN_DEFINITIONS`, `_summarize_domain`.

### MC-C04 Overall status

`The coverage report shall set its overall status to empty when every domain is empty, attention when any domain is empty, invalid or stale, and healthy otherwise.`

Evidence: `core/aletheia/data/data_coverage.py::_build_report`.

### MC-C05 Primary derivatives exclude comparison sources

`The coverage report shall count as primary funding, open interest and long/short ratio only rows whose exchange is not one of the comparison sources hyperliquid, hyperliquid_predicted or coinalyze and whose metadata is not marked observation_only.`

- WHEN only Hyperliquid funding rows are fresh THEN `primary_funding` is not fresh.

Evidence: `core/aletheia/data/data_coverage.py::_PRIMARY_DERIVATIVE_WHERE`, `core/aletheia/storage/repositories.py::LEGACY_DERIVATIVES_WHERE`.

### MC-C06 Coverage is a retained-table census

`The coverage report shall describe retained rows only and shall not claim requested-scope completeness, publication generation, economic correctness or business readiness.`

- WHEN a collector call completed THEN that alone does not make a domain `fresh`; WHEN a row is fresh THEN that alone proves no economic correctness.

Evidence: `core/aletheia/data/data_coverage.py::_query_domain`.

### MC-C07 Reads are cached and timed

`When the coverage route is served, the system shall answer from the 30-second in-process cache when it is warm and shall expose scan timing in the Server-Timing response header.`

Evidence: `core/aletheia/data/data_coverage.py::_CACHE_TTL_SEC`, `core/aletheia/server/routes/data.py::get_market_data_coverage`.

### MC-C08 Reports are separate and grant no authority

`The collector health and data coverage reports shall remain separate API surfaces and shall not change /_infra/livez, /_infra/readyz, Risk, Execution Gateway or Live permissions.`

Evidence: `core/aletheia/server/routes/data.py` (two routes, no writes outside `collector_health_state`).

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| Collector failure or timeout | stays visible until a later scheduled result updates the domain | `last_status`, `consecutive_problems`, alert after 3 | failure, never success |
| Shared connectivity incident | dependent failures grouped | `blocked_by_connectivity` | raw status unchanged |
| Domain table has no rows | classified | `empty` / `idle` | `empty`, never `0`-row healthy |
| Newest timestamp malformed or in the future | classified | `invalid` | not fresh |

## Authority

Diagnostic only: may write `collector_health_state`; may never change readiness probes,
Risk, Gateway submission or Live unlock.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| MC-C01, MC-C02 | `tests/test_collector_health.py` |
| MC-C03 – MC-C06 | `tests/test_market_data_coverage.py` |
| MC-C07, MC-C08 | `tests/test_market_routes.py` |

### Known conformance gaps

- Coverage marks `macro` decision-critical (`critical=True` in
  `core/aletheia/data/data_coverage.py::DOMAIN_DEFINITIONS`), but the Entry Planner context
  (`core/aletheia/trading/entry_analysis.py`) reads no macro, on-chain or liquidation data; macro's
  current readers are Regime, persona sizing and Gateway attribution
  ([Macro Source-Time Contract](macro_source_time.md)).
- With `market_data.enable_long_short_ratio` (default `True`), long/short and top-trader ratios
  are fetched from Binance USD-M (`extensions/data/collectors/derivatives.py::DerivativesCollector.collect`),
  a frozen venue under the product scope.

## Decision references

- There is no generic requested/succeeded/failed coverage percentage, publication ID or generation
  pointer, because no named derived dataset requires one; such facts belong at the producing
  dataset boundary, not inferred from row counts.
- Budget-skip starvation warning: #3089 (`core/aletheia/data/collector_health.py`).

## Merged collector-health and observability rules (2026-10-10)

Rules carried over from the retired
[data_collector_health.md](../history/data_collector_health.md) and
[market_data_observability.md](../history/market_data_observability.md) that MC-C01 – MC-C08 do
not already state. Each was re-checked against the code named in its evidence line.

### MC-C09 Two collector lanes with separate deadlines

`The scheduler shall run primary-venue orderbook, funding and open interest in data_collectors every 2 minutes and the context collectors in data_collector_context every 10 minutes, each lane under its own wall-clock deadline (50 s and 55 s) that the other lane cannot consume.`

- WHEN 2 s or less of a lane's deadline remains THEN the next collector is not started and records `skipped_budget`.
- WHEN a collector raises THEN it records `failed` with the exception class name as its code; WHEN it times out THEN `timed_out` with `asyncio_timeout`.

Evidence: `core/aletheia/agent_runtime/scheduler.py::_bounded`, `_DC_DEADLINE_S`, `_DC_CONTEXT_DEADLINE_S`; cadences in `core/aletheia/data/collector_health.py::COLLECTOR_CADENCE_SEC`.

### MC-C10 Status vocabulary and problem counter

`The collector health writer shall accept only success, partial, empty, failed, timed_out, skipped_budget, not_configured and disabled, shall increment consecutive_problems only for failed and timed_out, and shall reset it for every other status.`

- WHEN a result's errors are exactly `["no_key"]` THEN the status is `not_configured`; WHEN errors come with payload THEN `partial`; WHEN errors come without payload THEN `failed`.
- WHEN a collector is turned off THEN `disabled`; WHEN it is on but has no symbols THEN `not_configured`; neither is ever overdue.

Evidence: `core/aletheia/data/collector_health.py::classify_collector_result`, `record_collector_status`; `core/aletheia/agent_runtime/scheduler.py::_schedule_collector`.

### MC-C11 Success only after persistence

`For collectors whose rows the scheduler persists, the scheduler shall record success only after those rows reach SQLite, and shall record failed with a <domain>_persist_failed code when the write fails.`

- WHEN the funding write fails THEN `derivatives` is `failed` with `derivatives_funding_persist_failed`, and a fetched-but-unpersisted row is never reported healthy.

Evidence: `core/aletheia/agent_runtime/scheduler.py::_PERSISTENCE_GATED_COLLECTOR_HEALTH`.

### MC-C12 Repeated-problem and starvation alerts

`When a domain reaches three consecutive problems, the collector health writer shall emit one infrastructure event deduplicated for 30 minutes, as warning collector_consecutive_problem, or as info collector_problem_dependent_connectivity while external connectivity is unavailable or recovering.`

- WHEN a `skipped_budget` turn lands on a domain with no real attempt for more than three cadences THEN one warning `collector_budget_starvation` is emitted (deduplicated for three cadences) without touching `consecutive_problems`.

Evidence: `core/aletheia/data/collector_health.py::record_collector_status`.

### MC-C13 Bounded recovery attribution

`After external connectivity becomes reachable again, the collector health report shall attribute failures attempted before the recovery to one external_connectivity_recovering incident for at most two context cadences.`

- WHEN the exchange probe succeeds while the neutral probe fails THEN external egress is treated as reachable.

Evidence: `core/aletheia/data/collector_health.py::_infrastructure_incident`, `_RECOVERY_GRACE_SEC`; `core/aletheia/agent_runtime/connectivity.py::_probe_exchange_and_external`.

### MC-C14 Collection continues under trading pause

`While trading is paused, the scheduler shall keep data_collectors, data_collector_context and connectivity_monitor running, and only a full scheduler pause shall stop them.`

Evidence: `core/aletheia/agent_runtime/scheduler.py::_ESSENTIAL_JOBS`.

### MC-C15 Coverage judges current targets and keeps an inventory

`The coverage report shall evaluate orderbook and OHLCV against the current collector instrument targets and primary funding, open interest and long/short against the current derivative targets, shall attach an inventory projection of every retained row, and shall report an empty target set as idle.`

- WHEN a required target has no row THEN `missing_target_count` is positive and fresh rows of other targets do not make the domain fresh.
- WHEN the latest OI row lacks a long/short ratio THEN that target counts as missing; an older non-null ratio is not used.

Evidence: `core/aletheia/data/data_coverage.py::_build_report`, `_query_domain`, `_summarize_domain`.

### MC-C16 Slow coverage refresh is logged without values

`When a coverage refresh takes 500 ms or more, the system shall log data_coverage_refresh_slow with only the three slowest domain durations and no SQL parameters or row values.`

Evidence: `core/aletheia/data/data_coverage.py::get_data_coverage_report`.

### MC-C17 OHLCV response provenance

`When GET /api/market/ohlcv answers, the route shall set X-Aletheia-Data-Source to memory_cache, provider_fetch, database_fallback or unavailable and X-Aletheia-Data-Instrument to exchange:symbol:market_type:timeframe, and a database fallback shall read only the exact requested exchange, symbol, market type, timeframe and before boundary off the event loop.`

Evidence: `core/aletheia/server/routes/data.py` (OHLCV route, `_database_fallback` via `asyncio.to_thread`).

### MC-C18 Diagnostics label stale rows

`When aletheia inspect microstructure runs, it shall report each funding, open-interest and orderbook row with its source, observation time and freshness state, keeping stale rows labelled rather than dropped.`

Evidence: `core/aletheia/cli/inspect_cmd.py::microstructure`.

### Verification of merged rules

| Requirement | Focused test |
|---|---|
| MC-C09 | `tests/test_scheduler_timeouts.py` |
| MC-C10 – MC-C13 | `tests/test_collector_health.py`; MC-C11 also `tests/specs/spec_gateway_stability.py` |
| MC-C14 | `tests/test_layered_pause.py` |
| MC-C15 | `tests/test_market_data_coverage.py` |
| MC-C16 | no focused test found |
| MC-C17 | `tests/test_market_provider_port.py` |
| MC-C18 | `tests/test_inspect_microstructure_limit.py` |

### Merged-rule conformance gaps

- Raw external errors: `collector_health_state.last_error_code` keeps only a sanitized code
  (exception class name, at most 120 characters) and the retired document required that stored
  values never contain exception messages; the owner convention (#3880, SAFETY_AND_TRUST)
  keeps the bounded token and the redacted, capped raw error side by side. The table has no
  raw-error column.
