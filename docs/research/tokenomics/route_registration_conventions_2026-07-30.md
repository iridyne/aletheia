# Route Registration Conventions — 2026-07-30

Frozen research evidence collected while scoping Phase 1 of the
`tokenomics` branch. Purpose: document how existing FastAPI routes
mount into the app so a new `aletheia/server/routes/economics.py`
follows the same pattern with no surprises.

Sources: `aletheia/server/app.py`, `aletheia/server/routes/health.py`,
`aletheia/server/routes/analytics.py`.

## Router-per-file pattern

Every route file exports a module-level `router = APIRouter(...)`.
`analytics.py` is the closest cousin to what economics will be:

```python
# aletheia/server/routes/analytics.py
from fastapi import APIRouter, Response

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

@router.get("/trade-stats")
def trade_stats(): ...
```

Two conventions worth noting:

- **Prefix lives on the router**, not on `include_router`. `analytics`
  declares `prefix="/api/analytics"`; `app.py` includes it with no
  additional prefix. The one exception is `ws_router`, which is
  included with `prefix="/api"` at include time — that file has
  bare paths.
- **Health owns two routers** in one file: `router` at `/api` for
  the public API surface and `infra_router` at `/_infra` for
  infrastructure-only endpoints. Both are imported and included
  individually.

## Import + include in `app.py`

`aletheia/server/app.py` imports each router explicitly:

```python
from aletheia.server.routes.analytics import router as analytics_router
```

and calls `app.include_router(analytics_router)` in the top-level
initialisation block (`app.py:743-763`). New routers are added by
appending the import at the top of the block and the include at the
bottom. Order between includes does not currently matter for
routing (FastAPI uses path matching), but keeping the same order in
both places makes diffs readable.

## Response conventions

- Return plain `dict` for JSON responses — FastAPI serialises. No
  need for `JSONResponse` unless setting an explicit status code
  or headers.
- Use `HTTPException(status_code=..., detail=...)` for well-defined
  errors. Health uses this for readiness failures.
- Use `Response(content=..., media_type=..., status_code=...)` when
  the body should not be JSON (rare).

## Tag values

The `tags=["..."]` value drives OpenAPI grouping. Existing tags
include `system` (health), `analytics`, `infra`, `settings`, `data`,
`live`, etc. A new `economics` tag is consistent with this taxonomy.

## Dependencies and Query params

- Path/query parameters are declared as function args with
  `Query(...)` defaults when validation is needed. `analytics.py` uses
  bare defaults (`hours: int = 24`) for simple cases.
- `Depends(...)` for injected dependencies. Not commonly used in the
  routes surveyed; most routes read `load_config()` and open DB
  connections directly.
- Async vs sync: both patterns exist. Route handlers that only touch
  SQLite can be sync; anything awaiting I/O (broker snapshot, HTTP
  probes) should be async.

## Concrete boilerplate for the Phase 1 module

Sketch, matches every convention above:

```python
# aletheia/server/routes/economics.py
"""Read-only economics API routes.

Mirrors the aletheia audit CLI (see docs/references/ECONOMIC_VIABILITY_AUDIT.md).
No writes: pricing mutations remain CLI-only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from aletheia.economics.tracing import list_cost_summary, tracing_enabled
from aletheia.economics.viability import (
    audit_enabled,
    build_operator_viability,
    build_system_viability,
)

router = APIRouter(prefix="/api/economics", tags=["economics"])


@router.get("/status")
def economics_status() -> dict:
    return {"tracing_enabled": tracing_enabled(), "audit_enabled": audit_enabled()}


@router.get("/costs")
def costs(
    days: int = Query(7, ge=1, le=365),
    run_id: Optional[str] = None,
    include_events: bool = False,
    event_limit: int = Query(200, ge=1, le=5_000),
) -> dict:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    report = list_cost_summary(
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        run_id=run_id,
    )
    if not include_events:
        report.pop("events", None)
    else:
        report["events"] = report["events"][:event_limit]
    return report
```

Register in `app.py` alongside the existing imports/includes.

## Auth surface

None of the surveyed routes wire authentication. The broader project
has known auth gaps on tunneled runtimes (see issues #395, #397, #398,
#404). Economics endpoints are read-only and consult only ledger data
(no secrets), so they inherit the same posture as `analytics`. If
Phase 2 adds writer endpoints (`POST /budgets/...`), the auth story
must be revisited alongside those existing open issues.
