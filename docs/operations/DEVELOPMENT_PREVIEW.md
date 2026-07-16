# Worktree development preview

This document is the operational contract for previewing a Worktree change. It
separates fast feedback from the stable local gateway; it is not a managed PR
preview platform and does not create a second production runtime.

## Choose the smallest mode that proves the change

| Need | Start | Open | Safety boundary |
| --- | --- | --- | --- |
| Frontend-only change or API read inspection | `cd frontend && npm run dev` | `http://127.0.0.1:5173` | The default `:8787` master API is read-only through the Vite proxy. |
| Frontend plus backend change | `uv run aletheia dev api --port 8791`, then `VITE_API_TARGET=http://127.0.0.1:8791 npm run dev` | `http://127.0.0.1:5173` | The API owns `~/.aletheia/instances/dev-preview/<checkout-id>/8791`; secret reads return nothing and all proposal execution is blocked. |
| Release/operator acceptance | Normal gateway lifecycle only | `http://127.0.0.1:8787` | This remains the stable master gateway and is the only daily-use endpoint. |

`8787` (front door) and `8788` (its internal worker) are reserved. Do not run a
worktree API on either port. The development CLI runs a foreground reloadable
process and intentionally has no start/stop/restart, PID file, browser launch,
or autostart integration.

## Fast frontend loop

From the Worktree:

```sh
cd frontend
npm run dev
```

Vite serves the UI on `http://127.0.0.1:5173` with HMR. Its default API target
is `http://127.0.0.1:8787`. `GET`, `HEAD`, and `OPTIONS` requests can pass
through; every other `/api` method is rejected by the dev server with
`403 preview_readonly_master_api`. A visible in-app banner names this mode.

On Windows, the equivalent launcher is:

```powershell
.\scripts\dev.ps1
```

The launcher installs missing frontend packages and starts Vite only. It never
starts, stops, or restarts the gateway.

## Isolated backend loop

Use this only when the change needs backend code or a write-shaped UI flow:

```sh
# terminal A
uv run aletheia dev api --port 8791

# terminal B
cd frontend
VITE_API_TARGET=http://127.0.0.1:8791 npm run dev
```

PowerShell:

```powershell
uv run aletheia dev api --port 8791
$env:VITE_API_TARGET = 'http://127.0.0.1:8791'
cd frontend; npm run dev
```

The CLI sets preview mode itself. Do not hand-set its environment variables to
point at a normal `data/` directory: the config loader accepts only
`~/.aletheia/instances/dev-preview/<checkout-id>/<port>`. Preview mode has three
fail-closed properties:

- secret reads return no value and secret writes are rejected;
- `submit_proposal` and `execute_existing_proposal` reject before creating or
  changing durable execution state;
- the paper-approval route returns `403 preview_execution_blocked` before it
  changes a Proposal.
- gateway autostart/restart and code-update controls return `403` before they
  can create a machine-level side effect.

This is for UI and API development, not paper-trading simulation. Use focused
tests or the normal gateway's explicitly authorized paper workflow for trading
behavior verification.

## Finish and release acceptance

HMR is developer feedback, not release proof. Before merge, run the focused
backend tests for the changed contract and:

```sh
cd frontend && npm run build
```

The production build emits content-hashed assets into `aletheia/dashboard_static`.
The stable gateway on `:8787` remains the acceptance surface; do not restart it
from a Worktree just to make frontend edits visible. If an operator-approved
gateway restart is needed for a release, perform it from the owning checkout and
prove the current commit with the normal runtime checks.

## Troubleshooting

- **HMR page is stale:** verify the browser is on `:5173`, not `:8787`; restart
  only the Vite process.
- **A form gets 403 in the default mode:** expected. Start an isolated dev API
  and set `VITE_API_TARGET` if the flow needs a non-execution write.
- **Vite cannot bind `5173`:** choose an unused port with `npm run dev -- --port
  5174`; it does not affect the API safety policy.
- **The isolated API cannot start:** choose a port other than `8787`/`8788` and
  run `uv sync`; do not kill a process that owns the stable gateway port.
