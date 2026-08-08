# Domain 7: Providers, Normalization, and Data Quality

This domain follows external data from provider selection and entitlement
through batching, field mapping, canonical identity, normalization, quality and
freshness decisions, partial failure, fallback, and durable consumer semantics.

## Included

- provider protocols, registries, custom-source plugins, and preferences;
- capability probing, tier limits, caching, and rate-limit enforcement;
- stock, index, ETF, daily, minute, realtime, adjustment-factor, and financial
  acquisition paths;
- field mapping, type conversion, deduplication, provenance, and schema drift;
- empty, partial, invalid, stale, future, unsupported, and failed outcomes;
- provider fallback and semantic substitution;
- Aletheia's symbol/instrument identity, CCXT/provider chain, collector health,
  freshness, listing cache, and OHLCV coverage repair.

## Excluded

- Parquet/DuckDB layout and analytical query mechanics, covered in Domain 8;
- atomic replacement, transactions, and crash windows, covered in Domain 9;
- realtime transport to browsers, covered in Domain 10;
- full scheduler/job ownership, covered in Domain 11;
- credential storage and security review, covered in Domain 12;
- live provider calls, configuration changes, or implementation.

## Reports

- `source-map.md`
- `provider-normalization-analysis.md`
- `aletheia-comparison.md`
- `lessons.md`

## Evidence Boundary

The findings are based on current source, CodeGraph call paths, and bounded
source inventories. No provider, endpoint, collector, scheduler, migration,
compiler, or test suite was run.
