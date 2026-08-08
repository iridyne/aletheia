# Domain 6: FastAPI Composition and Lifecycle

This domain studies how the Python application is assembled and operated:
route registration, service and repository ownership, startup ordering,
background warm-up, scheduler wiring, readiness, failure isolation, and
shutdown.

## Included

- the FastAPI application composition root and lifespan;
- route-to-service and route-to-repository access patterns;
- `app.state`, module-global factories, and hidden dependency boundaries;
- startup ordering and optional-versus-required initialization;
- synchronous work inside async lifecycle code;
- background-task ownership, warm-up, cancellation, and shutdown order;
- liveness, infrastructure readiness, and business-health semantics;
- comparison with Aletheia's current gateway lifecycle.

## Excluded

- provider-specific normalization and quality, covered in Domain 7;
- Parquet/Polars/DuckDB mechanics, covered in Domain 8;
- write atomicity and cache/disk consistency, covered in Domain 9;
- detailed realtime and scheduler behavior, covered in Domains 10 and 11;
- live startup, endpoint calls, performance measurement, or implementation.

## Reports

- `source-map.md`
- `fastapi-lifecycle-analysis.md`
- `aletheia-comparison.md`
- `lessons.md`

## Evidence Boundary

The findings are based on current source, CodeGraph call paths, and bounded
source inventories. Neither application was started. No endpoint, browser
flow, scheduler, worker, compiler, or test suite was run.
