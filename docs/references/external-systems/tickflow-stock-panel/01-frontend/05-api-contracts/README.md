# Domain 5: API Client and Frontend Contracts

This domain studies the boundary between the Node/React frontend and Python/
FastAPI backend: request transport, type ownership, validation, errors,
capabilities, authentication, initialization, permissions, and confirmation.

## Included

- frontend request helpers and API facades;
- handwritten TypeScript request/response types;
- Pydantic request models and FastAPI response declarations;
- error envelopes, validation errors, status/code preservation, and UI routing;
- capability and permission facts;
- authentication and remote-control access boundaries;
- destructive/safety confirmation parameters;
- comparison with Aletheia's current frontend/backend contract.

## Excluded

- route/service/repository composition, covered in Domain 6;
- provider normalization and data quality, covered in Domain 7;
- storage transactions and I/O consistency, covered in Domains 8 and 9;
- live HTTP calls, OpenAPI generation, contract tests, or security probes;
- any implementation change to either application.

## Reports

- `source-map.md`
- `api-contract-analysis.md`
- `aletheia-comparison.md`
- `lessons.md`

## Evidence Boundary

The findings are based on current source, CodeGraph call paths, and bounded
source inventories. No endpoint was called and no pytest, frontend test,
compiler, schema generator, or browser flow was run.
