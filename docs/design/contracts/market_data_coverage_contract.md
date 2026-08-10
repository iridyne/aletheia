# Market Data Coverage and Collector Health Contract

## Purpose and current scope

This contract owns the operator-facing distinction between collector execution
health and persisted market-data coverage. It does not define trading, business,
or infrastructure readiness.

## Entry points

- `GET /api/market/collector-health` reports whether each scheduled collector
  ran and how its latest attempt finished.
- `GET /api/market/data-coverage` reports what retained data is actually present
  and recent enough in SQLite.
- Overview / System displays the two reports as separate rows.

## Durable state and consumers

Collector health uses the bounded `collector_health_state` SQLite table, with
one latest row per known collector domain. Data coverage reads the authoritative
market-data tables for orderbook, primary funding, primary open interest,
long/short ratio, liquidations, macro, on-chain, and OHLCV data.

The reports are diagnostic inputs for operators and the Overview page. They do
not authorize a strategy, Risk decision, order, or Live operation.

## Observable result

Collector health exposes the latest raw status, cadence, attempt/success facts,
problem counters, overdue state, and independently established connectivity
attribution.

Data coverage exposes retained row, symbol, and source counts; newest observation;
age; configured freshness limit; criticality; and one of `fresh`, `stale`,
`empty`, `invalid`, or `idle` for every covered domain. Its overall status is
`healthy`, `attention`, or `empty`.

## Invariants and trust boundaries

- A completed collector call does not prove that a dataset is populated or
  fresh.
- A fresh retained row does not prove economic correctness or business
  readiness.
- `empty` is an honest completed collector outcome; `idle` is an honest
  event-stream coverage state.
- Raw collector facts are not rewritten when a shared connectivity incident is
  identified; attribution is presentation metadata.
- Coverage is a retained-table census, not requested-scope completeness or a
  publication-generation guarantee.
- Neither report changes `/_infra/livez`, `/_infra/readyz`, Risk, Execution
  Gateway, or Live permissions.

## Failure and recovery semantics

Collector failures and timeouts remain visible until a later scheduled result
updates that domain. Confirmed shared connectivity incidents group dependent
failures without converting them into source success.

Coverage classifies missing rows as `empty`, malformed newest timestamps as
`invalid`, old decision-critical data as `stale`, and old or absent optional
event data as `idle`. Normal reads use the existing 30-second in-process cache;
the response exposes scan timing through `Server-Timing`.

## Executable evidence

- `tests/test_collector_health.py` proves bounded status, overdue, connectivity
  attribution, and recovery presentation.
- `tests/test_market_data_coverage.py` proves fresh, stale, empty, and idle
  coverage classification.
- `tests/test_market_routes.py` proves the two reports remain separate API
  surfaces and that data coverage exposes its timing facts.

## Known gaps

There is no generic requested/succeeded/failed coverage percentage, publication
ID, or generation pointer because no named derived dataset currently requires
that contract. Such facts must be added at the producing dataset boundary, not
inferred from retained row counts.
