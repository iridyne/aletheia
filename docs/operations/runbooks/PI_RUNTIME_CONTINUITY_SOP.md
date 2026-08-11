# PI Runtime Continuity SOP

This SOP records the operational path for the PI runtime continuity mechanism.
The feature is experimental and remains disabled by default.

## Boundary

Runtime continuity lives in the gateway/operator control plane only. It must not
place orders, unlock live trading, change risk limits, mutate credentials, or
change execution gateway semantics.

The current single-machine architecture has no front proxy capable of zero-loss
traffic handoff. Promotion therefore makes the selected runtime the persisted
`current` runtime and marks `activation.restart_required=true`; the supervisor
loads that runtime artifact on the next gateway respawn.

`GET /api/operator/runtime` is the source of truth for current state, guidance,
metrics, active locks, candidate/shadow/draining slots, and rollback readiness.

## Enablement

Set `runtime.runtime_continuity_enabled=true` only on a development or controlled
operator machine. Leave it `false` for the legacy update path.

Relevant settings:

- `runtime_continuity_enabled`: enables real shadow gateway launch from API/CLI.
- `runtime_continuity_lock_ttl_sec`: promote/rollback lock TTL.
- `runtime_continuity_shadow_port_offset`: shadow gateway port offset from the
  dashboard port.

## Candidate

Preferred path:

```powershell
aletheia update status
```

Then create the candidate through the API or Command Center. The continuity
update endpoint materializes a detached candidate git worktree under
`data/runtime_bundles/<runtime_id>` and stores that path in `runtime_registry`.

The unified `update` command still pulls into a source worktree; packaged runtimes select the signed-release path.

## Shadow

Start shadow validation from Command Center, API, or CLI. When the feature flag is
enabled, the shadow gateway starts from the candidate artifact path with:

- `ALETHEIA_RUNTIME_ID=<candidate>`
- `ALETHEIA_RUNTIME_MODE=shadow`
- `ALETHEIA_RUNTIME_STATE=shadowing`
- `ALETHEIA_RUNTIME_PORT=<dashboard_port + offset>`

Shadow FastAPI processes skip pidfile writes, scheduler startup, LLM startup
gate, and Telegram polling. The Node operator shell also suppresses side-effect
tasks in shadow mode.

## Promote

Promotion requires the runtime to be `ready_to_promote`. The service acquires the
database promote lock, writes a `pre_promote` checkpoint, marks the previous
runtime as `draining`, marks the candidate as `current`, records an event, and
returns activation metadata.

After promote, restart the gateway only when a supervisor or autostart path is
available. The inner server respawns from the promoted runtime artifact because
the supervisor reads the persisted current runtime before launch.

## Drain

Drain records retirement progress for the previous runtime. Use it after the
promoted runtime has been activated and the previous runtime no longer needs to
serve operator continuity.

Available paths:

```powershell
aletheia runtime drain --force
```

or `POST /api/operator/runtime/drain`.

Without `--force`, the runtime remains `draining` and records observation. With
`--force`, it is marked `legacy_position_manager`.

## Rollback

Rollback restores the latest `pre_promote` checkpoint, marks the failed promoted
runtime as `rolled_back`, clears active locks, emits a rollback event, and returns
activation metadata. Restart the gateway through the same supervised path to load
the restored runtime.

## Verification

Minimum verification before relying on the path:

```powershell
uv run pytest tests/test_runtime_continuity.py -q
uv run pytest -q
```

For UI or operator-shell changes also run:

```powershell
cd operator-shell; npm test; npm run build
cd ../frontend; npm run build
```

## Safety Checks

Before using promote or rollback:

- Confirm `GET /api/operator/runtime` reports the expected current and candidate.
- Confirm `guidance.action` matches the next intended step.
- Confirm `metrics.active_lock_count` and `metrics.failed_runtime_count` are
  understood before continuing.
- Confirm the candidate has `artifact_path` set.
- Confirm shadow validation reached `ready_to_promote`.
- Confirm no active promote/rollback lock is owned by another operator.
- Confirm a supervisor/autostart path exists before activating the promoted or
  rolled-back runtime with a gateway restart.
