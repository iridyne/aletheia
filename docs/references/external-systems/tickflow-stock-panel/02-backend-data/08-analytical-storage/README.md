# Domain 8: Analytical Storage, Read Models, and Caches

This domain studies how TickFlow turns persisted market files into interactive
screening, charting, indicator, and backtest read models, then compares that
design with Aletheia's SQLite-backed historical and evidence paths.

## Included

- physical Parquet layout and in-memory DuckDB views;
- Polars lazy scans, projection, predicate pushdown, and hot DataFrames;
- raw, enriched, live aggregate, history, and instrument read models;
- daily and minute partition selection;
- cache warming, invalidation, single-flight, TTL, LRU, and memory limits;
- direct Parquet-to-matrix loading, memory mapping, manifests, and generations;
- Aletheia's OHLCV rolling store, feature snapshots, query paths, and TTL cache;
- the authority boundary between transactional state and reconstructible
  analytical artifacts.

## Excluded

- provider acquisition and normalization, covered in Domain 7;
- full write atomicity, multi-file commit semantics, and crash recovery, covered
  in Domain 9;
- browser transport and live quote refresh, covered in Domain 10;
- scheduler ownership and job recovery, covered in Domain 11;
- implementation, migrations, benchmarks, or runtime validation.

## Reports

- `source-map.md`
- `analytical-storage-analysis.md`
- `aletheia-comparison.md`
- `lessons.md`

## Evidence Boundary

The findings are static: current source, CodeGraph relationships, focused source
inventories, and configuration declarations. No data pipeline, DuckDB query,
backtest, benchmark, migration, server, or test suite was run.
