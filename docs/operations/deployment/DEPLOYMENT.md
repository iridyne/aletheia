# Deployment Guide

> **Role: Operations manual.** How to run, deploy, verify and maintain the one runtime.
> The runtime is a Fedora source checkout (SSH host `u7p`); macOS is development and control
> only; Windows is never a runtime target or a fallback. Remote CI is retired: verification is
> local (`scripts/ci/ci_local.py`) plus pytest on CASI WSL (`casi-pytest` skill). This page states
> intent; current runtime truth is `aletheia doctor` and `/api/status`, never this text.
> Retired sections (Windows watchdog, Release-Tag/remote-CI release batch, experiment-runtime
> cutover) live in [deployment history](../../design/history/deployment_retired_sections.md).

## 1. Gateway processes

```
supervisor (core/aletheia/gateway/lifecycle.py)
    ├── frontdoor :8787  ← user entry (reverse proxy + SSE; app.dashboard_port)
    └── backend   :8788  ← FastAPI + APScheduler + SQLite
```

The supervisor's process owner on Linux is a user `systemd-run` transient service
([Gateway session service](GATEWAY_SESSION_SERVICE.md)). Do not kill processes by hand; use the CLI.

## 2. Daily commands

Run on the Fedora checkout (`ssh u7p 'cd ~/aletheia && …'`).

```sh
uv run aletheia gateway start --no-browser
uv run aletheia gateway stop
uv run aletheia gateway restart --no-browser   # required after a code change
uv run aletheia gateway status
uv run aletheia gateway logs                   # redacted multi-log tail

uv run aletheia data migrate                   # apply pending migrations explicitly (no-op when current)
uv run aletheia data capacity --deep --history 30
uv run aletheia data venue-margins --venue bitget --symbol BTC/USDT --show
```

`venue_margin_facts` (scheduler job) maintains venue margin tiers for current Planner demand;
`data venue-margins` without `--show` refreshes one instrument by hand and is never a
precondition for entry.

## 3. Deploy a specific commit to Fedora and verify

`aletheia update` (`core/aletheia/cli/update_cmd.py::update` →
`ops/update/continuity.py::apply_update_with_continuity` → `ops/update/applier.py::apply_update`)
fast-forwards the checkout to the exact SHA it fetched for `origin/<update.branch>` (default
`master`; `ops/update/git_ops.py::pull`), syncs dependencies when locks changed, preflights a
schema change on a backup copy, builds the frontend into staging, runs the smoke check, restarts
the Gateway (startup applies migrations) and rolls back on failure. It holds a cross-process
deployment lock (`deployment_busy` when another update runs) and refuses `dirty_worktree`,
`wrong_branch` and `local_ahead`. It deploys the **tip** of `origin/master`; there is no command
that deploys an arbitrary older SHA.

Steps:

1. **Fix the target.** The commit `<SHA>` is merged and is the tip of `origin/master`:
   `jj git fetch && jj log -r master@origin -T commit_id --no-graph` prints `<SHA>`. If master has
   moved past it, stop: the updater would deploy the newer tip.
2. **Check nothing else is deploying** (a parallel session may be):
   `ssh u7p 'ps -eo args | grep "[b]in/aletheia update"'` prints nothing.
3. **Record the pre-deploy state** for the comparison:
   `ssh u7p 'cd ~/aletheia && uv run aletheia control status --json'` (SHA, open windows, epochs).
4. **Deploy** in one synchronous call; do not background it and poll:
   `ssh u7p 'cd ~/aletheia && uv run aletheia update --verbose'`.
   Exit 0 = applied; exit 1 = not applied (read `reason`, then `uv run aletheia update status`
   before any retry); exit 2 = applied but Observer continuity UNPROVEN
   ([Update Observer continuity](UPDATE_OBSERVER_CONTINUITY.md)). A gate failure does not change
   the deployed commit; do not re-run "in case it did not execute".
5. **Verify** with the acceptance checklist below. Health may read `degraded` for about a minute
   after restart; re-read before concluding.

On Linux `aletheia update check` and `update auto status` report the Release-Tag path
(`ops/update/auto.py::check`); on this runtime they answer `qualification_runtime_pinned` because
no `ALETHEIA_RUNTIME_ROLE=paper` is configured. That is expected and is not a deploy check.

### Acceptance checklist

| # | Claim | Command (on `u7p`, in `~/aletheia`) | Pass |
| --- | --- | --- | --- |
| 1 | Running commit | `git rev-parse HEAD`; `curl -s http://127.0.0.1:8787/api/status` fields `boot_commit`, `current_commit` | all three equal `<SHA>` |
| 2 | Schema version | `uv run aletheia doctor --json` check `infra.schema` | `ok`, `schema current at <version>` (no pending migrations) |
| 3 | Model provider | `/api/status` `model_provider`, `llm_model_health`; doctor check `llm.inference` | expected provider; `llm.inference` = `ok` |
| 4 | Settlement health | doctor check `content.signal_settlement` | `ok` or `info` (never `warn` / probe error) |
| 5 | Runtime health | `/api/health` `status`; `/api/status` `scheduler_running` | `healthy`; `true` |
| 6 | Trading scope unchanged | `/api/status` `paper_execution_mode`, `execution_target`, `live_state`, `permission_mode` | `bitget_demo`, `external_exchange`, `live_disabled`, `Full` |

A filter that prints exactly these fields:

```sh
curl -s http://127.0.0.1:8787/api/status | python3 -c 'import json,sys; d=json.load(sys.stdin); print({k: d.get(k) for k in ("boot_commit","current_commit","model_provider","scheduler_running","paper_execution_mode","execution_target","live_state","permission_mode","health_status")})'
uv run aletheia doctor --json | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(c["name"], c["severity"], c["detail"]) for c in d["checks"] if c["name"] in ("infra.schema","llm.inference","content.signal_settlement","loop.execution_liveness","health.rollup")]'
```

Rows 2–4 are the same three facts the Release-Tag gate requires
(`ops/update/auto.py::_evaluate_runtime_diagnostics`); a manual `aletheia update` only proves
Gateway readiness, so the operator checks them.

### Observation windows

Before opening or resetting an Observation/qualification window after a merged change, that
exact commit must be deployed to Fedora by the steps above and pass checklist rows 1–4
(AGENTS.md). Only then anchor the window to the running commit
(`uv run aletheia evidence epoch register`, which takes the deployed SHA from the runtime;
`evidence epoch status` shows `sha_consistent`). Whether a deploy closes or restarts a window is an
owner decision under the [Experiment Protocol](../../product/experiments/EXPERIMENT_PROTOCOL.md);
never deploy or reset a window just to fill a template field. A merged PR is not a deployment.

## 4. Database migrations

Migrations are versioned, checksummed and applied once (`aletheia_schema_migrations` ledger);
`gateway start/restart` applies pending migrations before the readiness timer, and every other
runtime path only validates. Unknown migration IDs or checksum drift fail closed. Rules:
[data_model.md §6](../../design/data/data_model.md).

## 5. Database capacity and space reclaim

- The default capacity budget is 2048 MiB and only drives doctor/infra warnings; it never blocks
  audit or Learning writes.
- Nightly cleanup appends exact physical/logical sizes to `logs/database_capacity.jsonl`. A slope
  needs at least 3 samples across at least 7 days; production capacity judgement should still
  observe 30 days. `data capacity` uses a median pairwise slope and labels it
  provisional/established.
- Every SQLite connection sets a 64 MiB `journal_size_limit`. Nightly cleanup runs one
  non-blocking PASSIVE checkpoint and records log/checkpointed/remaining frames; persistent backlog
  usually means a long reader pinning an old snapshot — diagnose it, do not block the runtime.
- DELETE only moves pages to the freelist; an `auto_vacuum=NONE` database does not shrink. To
  return space to the OS:

```sh
uv run aletheia data compact             # read-only plan
uv run aletheia gateway stop
uv run aletheia data compact --apply     # verify, VACUUM, enable incremental reclaim
uv run aletheia gateway start --no-browser
uv run aletheia doctor
```

`--apply` refuses a running Gateway or insufficient temp disk, first creates and verifies
`backup_pre_compact_*.tar.gz`, holds the cross-process writer permit, checks integrity/foreign
keys, and rebuilds a schema-conforming `market_ohlcv` as `WITHOUT ROWID` before VACUUM. It deletes
no rows and shortens no retention; it is not a migration and never runs automatically.

Daily cleanup reclaims at most 16 MiB of freelist per run, 64 MiB above 80% of capacity, under the
writer permit, without deleting retained rows.

## 6. Frontend

`aletheia update` builds the dashboard in an unserved staging directory (running `npm ci` only when
the lock changed) and switches the static assets only after the smoke check passes. For a manual
rebuild on the runtime checkout:

```sh
cd frontend && npm run build && cd ..     # writes core/aletheia/dashboard_static/ (gitignored)
uv run aletheia gateway restart --no-browser
```

A browser holding an old service worker keeps the old UI until it is closed or the worker is
unregistered; the server cannot force it.

## 7. Health checks

```sh
uv run aletheia doctor                    # full diagnosis (single source of truth)
uv run aletheia doctor --json             # machine-readable
uv run aletheia doctor --verbose          # plus redacted log tails and next-log pointers
uv run aletheia doctor --bundle data/diagnostics/doctor.json
curl -s -o /dev/null -w '%{time_total}\n' http://127.0.0.1:8788/api/health   # backend latency, bypassing frontdoor
```

## 8. Common problems

| Symptom | Cause | Fix |
| --- | --- | --- |
| Web UI blank / JS 404 | `core/aletheia/dashboard_static/` not built | `cd frontend && npm run build && cd ..` |
| `doctor` reports `paused=True` | LLM provider unavailable (auto-pause) or daily-loss kill switch | restore provider / wait for UTC 0 / resume by hand |
| Old UI after a frontend change | PWA service-worker cache | reopen the browser or unregister the service worker |
| `database is locked` | more than one Gateway on the same data root | check `gateway status`; run one Gateway |

Development workspaces never start, stop, restart or update the runtime Gateway; see the
[jj workspace playbook](../development/JJ_WORKSPACE_PLAYBOOK.md).

## 9. Safety rules

- Never expose API keys or secrets in logs, docs, prompts or git.
- Live trading is off by default; enabling it is an explicit owner action in the Web UI.
- Risk parameter changes need owner confirmation; agents never change them on their own.
