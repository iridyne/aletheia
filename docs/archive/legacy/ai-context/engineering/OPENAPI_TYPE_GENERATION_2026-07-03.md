# OpenAPI Type Generation - 2026-07-03

## Scope

Branch: `fix/ts-type-generation`

This resolves D12 without migrating the backend to TypeScript:

- Python/FastAPI remains the backend source of truth
- OpenAPI JSON is exported offline from `aletheia.server.app`
- no backend route behavior changes
- no frontend behavior changes
- no generated schema snapshot is committed in this branch

## Why Not TypeScript Backend Migration

The earlier latency audit showed runtime bottlenecks are endpoint fan-out and
backend aggregation work, not Python-vs-TypeScript. Migrating the backend would
increase blast radius across trading, risk, execution, scheduler, persistence,
and credential boundaries without addressing the measured root cause.

## Added Tooling

Script:

```powershell
uv run python scripts/export_openapi_schema.py --output docs/_generated/openapi.json
```

Output:

- FastAPI OpenAPI JSON
- deterministic key order
- creates the output directory when needed
- prints path count and component schema count

The generated file can be consumed by a frontend type tool such as
`openapi-typescript` in a later frontend-only branch. This branch stops at schema
export because adding Node generation dependencies would be a separate package
management decision.

## Quantified Outcome

- Backend migration scope avoided: 0 route rewrites, 0 runtime semantics changed
- New reusable generation script: 1
- Regression test: 1
- Key API paths verified in generated schema: 3
  - `/api/status`
  - `/api/operator/recommendations`
  - `/api/serenity/status`

## Verification

- `uv run pytest tests/test_openapi_export.py -q`
  - exports a temporary OpenAPI JSON file
  - verifies OpenAPI version and key API paths
