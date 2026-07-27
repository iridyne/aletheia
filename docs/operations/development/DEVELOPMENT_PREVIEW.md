# Temporary checkout validation

This document is the operational contract for briefly running the current
checkout to inspect a change while a stable version continues to run. “Preview”
is shorthand for that temporary process, not a separate Aletheia product role,
managed PR platform, or second production runtime.

For machine roles, qualification freeze, deployment ownership, and the current
multi-instance support boundary, read
[`DEV_PROTOCOL.md`](../../_ai/workflow/DEV_PROTOCOL.md).

## Choose the smallest mode that proves the change

| Need | Start | Open | Safety boundary |
| --- | --- | --- | --- |
| Frontend-only change or API read inspection | `cd frontend && npm run dev` | `http://127.0.0.1:5173` | The default `:8787` stable API is read-only through the Vite proxy. |
| Frontend plus backend change | `uv run aletheia dev api --port 8791`, then point Vite at `:8791` | Vite port chosen for that Worktree | The API owns `~/.aletheia/instances/dev-preview/<checkout-id>/8791`; secret reads return nothing and all proposal execution is blocked. |
| Release/operator acceptance | Normal gateway lifecycle only | `http://127.0.0.1:8787` | This remains the stable/qualification gateway and is the only daily-use endpoint. |

`8787` (front door) and `8788` (reserved stable-runtime backend) are reserved.
Do not run a Worktree API or Vite server on either port. The development CLI
runs a foreground reloadable process and intentionally has no start/stop/restart,
PID file, browser launch, autostart, update, or stable-gateway integration.

## Instance identity and data isolation

`uv run aletheia dev api --port <port>` derives its data root from both the
Worktree path and the API port:

```text
~/.aletheia/instances/dev-preview/<checkout-id>/<api-port>
```

`checkout-id` is a stable hash of the absolute checkout path. Consequences:

- different Worktrees never share Preview data, even if they use the same API
  port on different machines;
- two Preview APIs from one Worktree use different data when they use different
  API ports;
- on one host every running API and Vite process still needs a unique bound
  port;
- Preview mode rejects any data directory outside
  `~/.aletheia/instances/dev-preview`;
- Preview never reads the primary Runtime's `ALETHEIA_DATA_DIR` override.

Do not copy `app.db`, `config.yaml`, secrets, positions, or outcome state between
the stable Runtime and a Preview. A Preview is disposable development state,
not a qualification cohort.

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
starts, stops, restarts, or updates the stable gateway.

Choose another UI port when another Worktree already owns `5173`:

```powershell
.\scripts\dev.ps1 -UiPort 5174
```

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
# terminal A
uv run aletheia dev api --port 8791

# terminal B
.\scripts\dev.ps1 -UiPort 5173 -ApiPort 8791
```

The CLI sets Preview mode itself. Do not hand-set its environment variables to
point at a normal data directory. Preview mode has these fail-closed properties:

- Aletheia secret-store reads return no value and writes are rejected;
- `submit_proposal` and `execute_existing_proposal` reject before creating or
  changing durable execution state;
- the paper-approval route returns `403 preview_execution_blocked` before it
  changes a Proposal;
- gateway autostart/restart and code-update controls return `403` before they
  can create a machine-level side effect.

This is for UI and API development, not paper-trading simulation. Use focused
tests or the stable gateway's explicitly authorized paper workflow for trading
behavior verification.

Background scheduler/operator services are disabled by the shadow runtime
startup gate. The temporary process therefore does not autonomously collect
qualification evidence or invoke trading agents.

## Concurrent temporary validations

A qualification/CI host may briefly run one or more isolated test pairs while
the stable gateway remains pinned on `8787`. Multiple pairs are optional, not
the goal of the design. Each active pair must come from its own Worktree and use
unique host ports.

Typical case:

```powershell
# Stable qualification Runtime remains on 8787 / reserved 8788.
# Temporarily validate the current checkout:
uv run aletheia dev api --port 8791
.\scripts\dev.ps1 -UiPort 5173 -ApiPort 8791
```

The two commands run in separate terminals. If concurrent validations are
occasionally useful, repeat the same pattern with another Worktree and unused
ports. Do not treat a manually configured full Gateway as a temporary
validation process.

## Port convention

Use this convention unless a port is already occupied:

| Role | Port range |
| --- | --- |
| Stable front door | `8787` |
| Stable reserved backend | `8788` |
| Preview APIs | `8791`–`8799` |
| Vite previews | `5173`–`5181` |

The ranges are a human convention, not a dynamic allocator. The developer or
external orchestrator chooses an unused port only for the duration of the test.

Do not add a repository-owned task database, worker lease, or port allocator to
solve this convention. ORCA or another external development orchestrator owns
ephemeral assignment; Aletheia only validates its own bounded runtime contract.

## Finish and release acceptance

HMR is developer feedback, not release proof. Before merge, run the focused
backend tests for the changed contract and:

```sh
cd frontend && npm run build
```

The production build emits content-hashed assets into
`aletheia/dashboard_static`. The stable gateway on `:8787` remains the
acceptance surface; do not restart it from a Worktree just to make frontend
edits visible. If an owner-approved gateway restart is needed for a release,
perform it from the owning checkout and prove the current commit with the normal
runtime checks.

A merged PR is not a qualification deployment. The Windows qualification
Runtime remains pinned until the owner explicitly selects a new candidate. See
`DEV_PROTOCOL.md` for observation-window rules.

## Cleanup

Preview processes are foreground-only. Stop the matching API and Vite terminals
when the task ends. Preview data may be removed only after confirming that no
running process uses it and that no reproduction artifact is still needed:

```text
~/.aletheia/instances/dev-preview/<checkout-id>/<api-port>
```

Do not delete the primary `~/.aletheia` Runtime root as part of Preview cleanup.

## Troubleshooting

- **HMR page is stale:** verify the browser is on the Worktree's Vite port, not
  `:8787`; restart only that Vite process.
- **A form gets 403 in default mode:** expected. Start an isolated Preview API
  and point Vite at it if the flow needs a non-execution write.
- **Vite cannot bind its port:** choose an unused `-UiPort`; it does not affect
  the API safety policy.
- **The isolated API cannot start:** choose a free port other than `8787`/`8788`
  and run `uv sync`; do not kill a process that owns the stable gateway port.
- **Two previews show the same data:** verify they were started from different
  Worktrees or with different API ports, and inspect the printed Preview data
  directory at startup.
- **A Preview asks for credentials or can execute:** stop it immediately; that
  violates the Preview contract and must be treated as a safety defect.
