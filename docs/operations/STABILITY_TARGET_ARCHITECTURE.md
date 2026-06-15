# Aletheia stability target architecture

This plan turns the current gateway/UI/operator infra into a bounded,
observable, self-healing local runtime. It is intentionally local-first: no
Docker, Redis, Postgres, remote deployment, or multi-user control plane is added.

## Boundary

This track only touches local runtime stability:

- gateway lifecycle, supervisor, watchdog, health/readiness probes
- single-port frontend/backend operation on `127.0.0.1:8787`
- operator-shell process hygiene and diagnostics
- SQLite contention mitigation that preserves existing storage semantics
- user-visible alerts when the local UI/API cannot be trusted

It must not change trading execution semantics, exchange signing, credentials,
live unlock paths, or hard risk limits.

## Target end state

`http://127.0.0.1:8787/` is the only daily-use entrypoint. The system is allowed
to restart internals, but failures must be:

1. **bounded** — no silent dead process, duplicate operator shell, or ghost port;
2. **observable** — probes distinguish process liveness, UI readiness, and
   business/trading health;
3. **self-healing where deterministic** — restart stale/crashed internals and
   clean our own orphans without touching trading state;
4. **fail-loud to the user** — the dashboard, Windows toast, Telegram, JSONL
   audit/error logs, or `doctor` must show the fault and next action;
5. **verified** — each phase has focused regression tests plus a final smoke and
   full-suite gate before being called stable.

## Probe contract

The stable architecture uses three layers of health signal:

| Probe | Purpose | May read DB/config? | Failure meaning |
| --- | --- | --- | --- |
| `/_infra/livez` | Process + HTTP event loop is alive | No | restart/reconnect this process |
| `/_infra/readyz` | API is serving and dashboard static exists | No DB; static check only | do not route users to this worker |
| `/api/health` | Business/runtime health roll-up | Yes | show degraded state; restart only for deterministic self-heal cases |

The legacy `/api/ready` remains as a cheap dashboard reconnect endpoint.

## Phase plan

### Phase 0 — Stabilize the current single-port MVP

Goal: keep the existing `8787` gateway shape alive while removing known silent
failure windows.

Implemented/required controls:

- supervised inner uvicorn restarts on crash and on lost listening port;
- startup verification requires pidfile, listening port, `/_infra/livez`, and
  HTML root readiness, not just a successful `Popen`;
- `vite build --watch` writes into `dashboard_static/` without emptying the
  served directory;
- frontend uses `8787` as the maintained dev/daily entrypoint;
- dashboard connection guard fails loud and offers browser notifications;
- lifecycle failures emit audit/error records and Windows/Telegram notifications
  where configured;
- `doctor --fix` remains the bootstrap repair surface.

Exit checks:

```powershell
uv run pytest tests/test_lifecycle.py tests/test_supervisor.py tests/test_status_routes.py tests/test_frontend_build_config.py -q
cd operator-shell; npm test; npm run build
cd ../frontend; npm run build
uv run aletheia gateway restart --no-browser
uv run aletheia gateway status
Invoke-WebRequest http://127.0.0.1:8787/_infra/readyz
Invoke-WebRequest http://127.0.0.1:8787/api/status
```

### Phase 1 — Deterministic self-heal and single-owner background services

Goal: ensure the gateway and operator shell converge to exactly one current
owner after restarts, code changes, and watchdog ticks.

Controls:

- current-runtime PID/lock discipline for gateway and operator shell;
- duplicate current operator shells are killed or adopted deterministically;
- shadow/test runtimes cannot write current operator heartbeat;
- stale code and stale market-data self-heal are rate-limited and audited;
- lifecycle route failures are non-fatal to operator startup.

Exit checks:

```powershell
uv run pytest tests/test_self_heal.py tests/test_operator_shell_supervision.py tests/test_operator_routes.py -q
cd operator-shell; npm test
```

### Phase 2 — Stable local front door

Goal: make `8787` stay bound even while the backend worker restarts.

Implemented controls:

- a minimal local front-door process owns `8787`;
- backend worker binds an internal loopback port (`dashboard_port + 1` by
  default; currently `8788` for the default `8787` dashboard);
- front door serves the last known-good static dashboard and `/_infra/*`;
- `/api/*` proxies to the ready backend worker and returns structured `503`
  JSON instead of connection-refused when the worker is down;
- startup verification waits for `/api/ready` through the front door before
  declaring the gateway started;
- dev build-watch remains single-port from the user's perspective.

Exit checks:

```powershell
uv run pytest tests/test_frontdoor.py tests/test_lifecycle.py tests/test_status_routes.py -q
uv run aletheia gateway restart --no-browser
# kill/restart the backend worker; 8787 must keep returning an infra page/status
Invoke-WebRequest http://127.0.0.1:8787/_infra/status
Invoke-WebRequest http://127.0.0.1:8787/
```

### Phase 3 — Transactional restart / runtime promotion

Goal: eliminate bad handoffs when code changes during heavy development.

Implemented/required controls:

- start a candidate backend on an internal port;
- require `/_infra/readyz` and targeted `/api/health` smoke before promotion;
- keep the front-door backend target in an atomic local route file
  (`data/frontdoor-route.json`) so `:8787` can switch workers without rebinding
  the user-facing port;
- switch the front door only after the candidate backend reports ready;
- keep rollback metadata and old-worker drain state;
- keep shadow runtime side effects disabled.

Exit checks:

```powershell
uv run pytest tests/test_runtime_continuity.py tests/test_frontdoor.py -q
Invoke-WebRequest http://127.0.0.1:8787/_infra/status
```

### Phase 4 — SQLite/write-path hardening

Goal: remove lock storms that can degrade operator heartbeat and route writes.

Implemented/required controls:

- keep WAL and `busy_timeout`;
- isolate long-running work from write transactions;
- wrap every scheduled job with a SQLite guard that rolls back leaked
  per-thread transactions and closes the thread-local connection at job end, so
  one failed job cannot pin the WAL writer lock for later jobs or heartbeats;
- centralize high-contention writes behind narrow retry/backoff helpers or a
  single local writer lane where needed;
- never hold DB transactions across network/LLM/exchange calls.

Exit checks:

```powershell
uv run pytest tests/test_storage.py tests/test_operator_store.py tests/test_operator_routes.py -q
```

### Phase 5 — Soak and release gate

Goal: prove the local machine can run unattended.

Controls:

- `doctor` all-clear except expected operator/business warnings;
- `/_infra/readyz` fresh, `/api/status` fresh, operator heartbeat fresh;
- no duplicate gateway/operator processes;
- no recurring `database is locked`, `gateway_request_failed:404`, or API-only
  dashboard startup windows in logs;
- full backend suite, operator-shell tests/build, frontend build all pass.

Final gate:

```powershell
uv run pytest -q
cd operator-shell; npm test; npm run build
cd ../frontend; npm run build
uv run aletheia doctor --fetch
uv run aletheia gateway restart --no-browser
uv run aletheia gateway status
```
