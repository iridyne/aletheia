# Domain 5 Source Map

## TickFlow Contract Sources

| Boundary | Current source | Role |
|---|---|---|
| request transport | `frontend/src/lib/api.ts` | one fetch wrapper, error extraction/toast, interfaces, and a monolithic endpoint object |
| query consumers | `frontend/src/lib/useSharedQueries.ts`, `useSharedMutations.ts`, pages/components | apply endpoint return types to cache and UI |
| authentication UI | `frontend/src/pages/Auth.tsx` | selects first-time setup or login from auth status |
| global auth redirect | `frontend/src/main.tsx` | converts selected query/mutation errors into Login navigation |
| API assembly and middleware | `backend/app/main.py` | mounts routers, enforces initialized/session state, and handles capability denial |
| authentication endpoints | `backend/app/api/auth.py` | password input validation, setup/login/change, cookie, and rate-limit semantics |
| capability authority | `backend/app/tickflow/capabilities.py`, `policy.py` | typed provider capabilities, limits, tier detection, caching, and denial exception |
| route modules | `backend/app/api/*.py` | Pydantic inputs, HTTP exceptions, and mostly dictionary responses |
| settings/capability responses | `backend/app/api/settings.py`, `routes.py` | masked configuration, preferences, detected capabilities, and limits |

## TickFlow Request Path

```text
page/hook
  -> api.domainMethod(input)
  -> request<T>(path, fetch options)
      -> fetch / JSON
      -> non-2xx: detail/message -> plain Error + optional toast
      -> success: unchecked JSON asserted as T
  -> React Query / page state

FastAPI route
  -> Pydantic request validation where declared
  -> auth middleware and capability require()
  -> service/repository
  -> dictionary/Pydantic-derived JSON response
```

## TickFlow Access State Machine

```text
non-/api or public auth/health path -> allow

/api request
  -> password not configured
       -> local/private client -> allow setup/application access
       -> public client -> 403 NOT_INITIALIZED
  -> password configured
       -> valid HttpOnly session cookie -> allow
       -> otherwise -> 401
```

Password setup is local/private only, login failures are rate-limited, password
change revokes all sessions, and missing capabilities become HTTP 403 through a
global `CapabilityDenied` handler.

## Static TickFlow Inventory

- frontend API facade: 2,534 lines;
- approximately 209 backend route-decorator occurrences;
- zero `response_model=` occurrences under `backend/app/api`;
- one explicit `request<any>` and 44 `any` tokens in the API facade;
- no OpenAPI TypeScript generation configuration found;
- no correctly named frontend test/spec files found.

Counts are source-pattern inventory, not semantic endpoint or defect counts.

## Aletheia Comparison Sources

| Boundary | Current source | Role |
|---|---|---|
| browser transport | `frontend/src/api/client.ts` | remote token header, error formatting, timeout/cancellation, interfaces, and endpoint facade |
| frontend domain types | `frontend/src/types.ts`, feature-local `types.ts`, API-local interfaces | manually mirror selected backend responses |
| transport tests | `frontend/src/api/client.test.ts` | verifies remote-token storage results do not echo the token |
| application/middleware | `aletheia/server/app.py` | route assembly, sanitized validation errors, remote-control authentication, lifespan |
| critical safety routes | `aletheia/server/routes/live.py`, `risk.py`, `runtime.py`, `settings.py`, `data.py` | backend confirmation, permission, audit, and structured denial semantics |
| system/status contract | `aletheia/server/routes/health.py` | runtime, exchange, permission, connectivity, and health representation |
| operator response model | `aletheia/server/routes/operator.py` | the one currently declared `response_model=` route under the server tree |

## Static Aletheia Inventory

- frontend API facade: 2,895 lines;
- approximately 337 backend route-decorator occurrences;
- one `response_model=` occurrence under `aletheia/server`;
- approximately 56 `fetchJson/post/put<any>` occurrences and 137 `any` tokens
  in the API facade;
- no active OpenAPI TypeScript generation configuration found; an archived
  design note discusses later adoption;
- backend RequestValidationError output is explicitly sanitized to omit rejected
  raw input values.

## Important Evidence Classification

The inventories demonstrate handwritten-contract scale and response-schema
coverage. They do not prove that a particular frontend type currently differs
from a live response. Specific mismatches are recorded only where both sides of
the current source path were inspected.
