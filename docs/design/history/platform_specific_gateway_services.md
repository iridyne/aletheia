# Archived: platform-specific Gateway service backends

> Status: archived 2026-09-28. These modules are **retained in the tree, not
> deleted**, and are **not maintained**. Nothing in the Linux/Fedora runtime
> reaches them. This page is an inventory and a boundary statement — it is not
> deployment instruction, not a capability promise, and not proof that any of it
> works on its platform.

## Why this exists

The runtime that is actually operated is a Linux source checkout (Fedora Paper).
A set of Gateway service backends only ever runs on macOS or Windows, so it costs
review and verification attention while being unexercised by the runtime that
matters. The owner's decision was to keep the code — deleting it would remove a
working path that may return — but to stop treating it as live.

Each module carries the marker in its first docstring line:

```sh
grep -rl "ARCHIVED (platform-specific" core/
```

## Archived modules

| Module | Platform | Why the Linux/Fedora runtime never reaches it |
|---|---|---|
| `core/aletheia/gateway/autostart.py` | macOS, Windows | Every call site is guarded by `platform.system() == "Darwin"` and performs only legacy launchd cleanup (`service_control._cleanup_legacy_macos`, the recovery branches in `lifecycle`). |
| `core/aletheia/gateway/windows_firewall.py` | Windows | Reachable only through the Windows-only CLI firewall commands and `windows_machine_service`. |
| `core/aletheia/gateway/windows_machine_service.py` | Windows | Windows-only, and its single creation path is already gone: a machine service could be installed only from a frozen payload, which is no longer built. Inspecting, controlling and removing an *existing* install remains. |
| `core/aletheia/gateway/windows_identity.py` | Windows | Imported at module level by `autostart`, `managed_service` and `session_service` (so it must stay importable), but every use sits in a Windows-only branch. |
| CLI `gateway firewall …` | Windows | Its Typer help text says so; the handlers refuse off Windows. |
| CLI `gateway service elevated-apply` | Windows | Hidden command; the UAC side of the removed machine-service install. |

## Deliberately NOT archived

These carry a Linux path and are the reason the archived set stops where it does:

- `core/aletheia/gateway/session_service.py` — issues the **systemd user unit** that
  Fedora installs with `aletheia gateway service install --backend session`, and
  reads back its enabled/running state. It also builds a launchd plist and a
  Task Scheduler XML; those two branches are platform-specific, the systemd branch
  is the live one.
- `core/aletheia/gateway/managed_service.py` — on-demand ownership of the gateway
  supervisor, created by `gateway start` and removed by `gateway stop` on all three
  platforms. It is also the shared runner-command and identity layer that
  `session_service` imports, so archiving it would strand the live path.
- `core/aletheia/gateway/service_control.py` and the machine-ownership branches of
  `lifecycle.py` — the routing and truthful status surface over the above.

## What a reader must not conclude

- **Not verified.** The modules keep their tests (`tests/test_autostart.py`,
  `test_windows_firewall.py`, `test_windows_machine_service.py`, `test_service_control.py`),
  and those tests still run — that is the evidence the code remains intact. It is
  **not** evidence that the path works on its platform, because no current runtime
  exercises it.
- **Not removed.** If a macOS or Windows deployment returns, this table is the
  inventory to start from; the marker is the only thing to lift once a path is
  exercised again.
- **Not a scope expansion.** Archiving is a statement about maintenance state. It
  grants no new authority and removes none.
