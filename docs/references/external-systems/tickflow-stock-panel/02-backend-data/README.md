# Backend and Data Research Lane

| Domain | Future folder | Focus |
|---|---|---|
| 6 | [`06-fastapi-architecture/`](06-fastapi-architecture/) | Route/service/repository boundaries, application state, lifecycle, and async behavior |
| 7 | [`07-provider-normalization/`](07-provider-normalization/) | Provider capabilities, acquisition, normalization, data quality, and financial semantics |
| 8 | [`08-analytical-storage/`](08-analytical-storage/) | Parquet, Polars, DuckDB, hot caches, history, derived datasets, and backtests |
| 9 | [`09-io-consistency/`](09-io-consistency/) | Atomic writes, locks, cache/disk ordering, generation, schema evolution, and crash recovery |

Create each domain folder only when that investigation begins. Keep analytical
and reconstructible data separate from transactional, audited trading state in
every comparison.
