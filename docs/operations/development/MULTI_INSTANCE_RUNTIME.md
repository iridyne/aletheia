# Production-grade multi-instance runtime

This document describes the target operating contract for running multiple
Aletheia instances on one host. It is the operational companion to
[`DEV_PROTOCOL.md`](../../_ai/workflow/DEV_PROTOCOL.md).

## One Runtime, different owner intentions

There is no Preview instance type. Every managed instance uses the same normal
Runtime path and can be kept for minutes, days, or longer. The owner records why
an instance exists:

- `feature_validation`: verify a bounded change from `master` or a PR;
- `long_running`: keep observing a target over time;
- `qualification`: collect frozen, admissible evidence.

Intent does not change Runtime capability or grant trading authority. A
short-lived instance is not a reduced server, and a long-running instance does
not bypass Risk, permission, credentials, or Live gates.

## Required per-instance resources

Before an instance starts, the manager must resolve and persist:

```text
instance_id
human alias
exact code commit
artifact or checkout
absolute data root
public/front-door port
backend port
execution mode
external account scope, if any
lifecycle owner
intent
```

Every field except intent is part of the instance's operational identity.
Changing the commit, data root, execution mode, or account scope is a controlled
runtime change, not a cosmetic relabel.

Target state layout:

```text
~/.aletheia/instances/
  <globally-unique-instance-id>/
    instance.yaml
    app.db
    config.yaml
    logs/
```

Each directory owns its own config, database, audit log, reports, artifacts,
cache, PID metadata, and local secrets namespace. Instances never share an
`app.db`.

The host also needs a minimal Runtime registry for instance aliases, active
service/PID identity, port reservations, lifecycle ownership, and same-host
external-account writer leases. This is host coordination state, not a
development task database and not a trading ledger.

## Typical host topology

```text
stable-main
  instance_id:  globally unique ID
  intent:       long_running
  commit:       pinned SHA
  ports:        8787 / 8788
  data:         ~/.aletheia/instances/stable-main

learning-qualification
  instance_id:  globally unique ID
  intent:       qualification
  commit:       pinned SHA
  ports:        independently assigned
  data:         ~/.aletheia/instances/learning-qualification

feature-check-<id>
  instance_id:  globally unique ID
  intent:       feature_validation
  commit:       current master or PR exact SHA
  ports:        independently assigned
  data:         ~/.aletheia/instances/feature-check-<id>
```

All three use the same Gateway, scheduler, storage, health and lifecycle code.
The host may run only the instances its CPU, memory, disk, model quota and
external API limits can safely support.

## Fast feature-validation workflow

1. Resolve the desired `master HEAD`, PR, or Worktree to an exact SHA.
2. Create a unique instance identity, data root and port pair.
3. Choose the minimum execution/account scope needed by the test.
4. Start it through the normal Production-grade lifecycle.
5. Run the written acceptance checks.
6. Stop it when the check is complete, unless the owner explicitly retains it.

The result is one of:

- continue development;
- keep PR/CI evidence and remove the instance;
- explicitly retain the same instance as `long_running`;
- explicitly designate it as `qualification` and start a new evidence window.

There is no automatic promotion based on uptime.

## Authority and account safety

Production-grade describes implementation quality, not authority.

- Live remains disabled by default on every instance.
- Every order still passes through Risk and the Execution Gateway.
- Short-lived validation normally uses `paper_internal` or no external writer.
- One `exchange account + environment + execution mode` has at most one writer.
- A second writer request fails before scheduler or order execution starts.
- Market-data reads may share a provider, but provenance and rate-limit state
  remain instance-visible.

The host registry can enforce this only among instances on the same machine.
Across machines, writable instances must use distinct exchange subaccounts or
credential scopes. Otherwise an external owner must designate exactly one
writer and configure every other instance without external write authority.

## Lifecycle isolation

Every lifecycle action must name one instance:

```text
create -> start -> status/doctor/logs -> stop -> optional remove
```

- starting one instance never restarts another;
- stopping one instance never kills a host-wide process by ambiguous PID;
- update selects an artifact/commit for one instance only;
- autostart/service names include instance ID;
- ports are reserved atomically and conflicts fail closed;
- removal requires a stopped instance and explicit confirmation;
- the default daily instance receives extra deletion protection.

## Evidence isolation

Runtime sessions, proposals, outcomes, Learning exposures and verdicts must be
attributable to:

```text
instance_id + runtime_session_id + producer_commit + policy_epoch
```

Analytics may compare instances but must not silently pool them. Changing intent
does not rewrite historical data. Qualification evidence starts at the explicit
designation boundary.

## Current implementation status

This is a target contract, not a claim that the commands already exist.

Today, the normal Gateway lifecycle is still mostly singleton-oriented.
`aletheia dev api` remains an API/shadow development helper and does not satisfy
this Production-grade instance contract. Its isolated state and read-only Vite
HMR on `5173` remain useful during migration, but they are not the target
multi-instance model.

Until instance-scoped lifecycle, service identity, status, doctor and account
ownership are implemented and validated, do not manually launch multiple full
Gateways and describe them as supported.

### Current development helper

For frontend-only work, Vite may continue to read the default `8787` Runtime
through its write-blocking proxy:

```sh
cd frontend
npm run dev
```

For backend API-shape work, the current isolated helper remains:

```sh
# terminal A
uv run aletheia dev api --port 8791

# terminal B
cd frontend
VITE_API_TARGET=http://127.0.0.1:8791 npm run dev
```

On Windows, terminal B can use:

```powershell
.\scripts\dev.ps1 -UiPort 5173 -ApiPort 8791
```

This helper uses separate state, blocks secrets and execution, and does not
start the normal background services. It can verify UI/API behavior only; it
cannot prove the target Production-grade multi-instance lifecycle, scheduler,
paper accounting, Learning contribution, or qualification economics.

## Implementation acceptance

The implementation is complete only after focused tests and a Windows/self-hosted
soak prove:

- two full instances remain healthy concurrently;
- each owns distinct ports, data, PID, logs and service identity;
- scheduler work and persistence remain isolated;
- one instance can restart or update without affecting the other;
- port/identity conflicts and duplicate account writers fail closed;
- `paper_internal` ledgers and Learning evidence never cross instances;
- the existing default single-instance workflow remains compatible.
