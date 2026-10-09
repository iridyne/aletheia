# frontend/ — Dashboard (React 19 + Vite + Tailwind 4)

Local conventions for the dashboard. Root [`AGENTS.md`](../AGENTS.md) rules still apply.

## Commands (run from `frontend/`)

```sh
npm ci
npm run build                  # tsc -b → vite build → compress → perf check
npm run lint                   # eslint src/
npm test                       # vitest run (tests sit beside sources as *.test.ts[x])
npm run check:api-types        # drift check vs backend OpenAPI schema
npm run check:settings-types   # drift check vs backend settings model
```

Backend-side static checks of this tree: `uv run pytest tests -q -m domain_frontend`.

## Conventions

- `npm run build` writes into `core/aletheia/dashboard_static/`, which the backend serves:
  a UI change is live after a build plus a browser refresh. Do not start a dev server to check
  a change; the worktree HMR flow is in [development preview](../docs/operations/development/DEVELOPMENT_PREVIEW.md).
- `src/api/generated/*.d.ts` is generated: never hand-edit. After changing backend route or
  settings models, run `npm run generate:api-types` / `generate:settings-types`
  (`scripts/ci/export_openapi_schema.py` produces the schema) and commit the result.
- Symbol display has exactly one entry point, `fmtSymbol()` in `src/lib/format.ts`; never
  concatenate symbol text elsewhere.
- Never render healthy-looking state the backend did not report; show `not available` /
  `not configured` (root safety invariant). New config fields need Settings controls
  ([STATE_AND_DATA](../docs/operations/development/agent-guides/STATE_AND_DATA.md)).
- Presentation rules: [`dashboard_data_presentation_contract.md`](../docs/design/contracts/dashboard_data_presentation_contract.md).
