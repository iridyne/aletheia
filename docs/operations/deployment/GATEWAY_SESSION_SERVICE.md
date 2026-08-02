# Gateway session service

`uv run aletheia gateway start` is an explicit, on-demand operation. It starts
the existing gateway runner under the current user's operating-system process
manager, verifies `/api/ready` and the dashboard, then returns. Closing the
terminal or ORCA session that ran the command does not stop that managed runner.

`uv run aletheia gateway stop` asks that same manager to stop the runner and
removes its transient definition. The gateway stays stopped until a human runs
`gateway start` again.

An operator may install a persistent user service without administrator rights:

```sh
uv run aletheia gateway service install --backend session
uv run aletheia gateway enable
uv run aletheia gateway start
```

Installation, enablement, and runtime are independent. `enable` does not start;
`disable` does not stop; `stop` preserves an installed definition and its enabled
state. `gateway service status` reports all three facts.

## Platform mapping

| Platform | Owner created by `gateway start` | Removed by `gateway stop` |
| --- | --- | --- |
| macOS | on-demand user `launchd` job | `launchctl bootout` and its transient plist |
| Linux | user `systemd-run` transient service | `systemctl --user stop` |
| Windows | on-demand Task Scheduler task; S4U first, InteractiveToken after an S4U access denial | `schtasks /End` and `/Delete` |

Every definition runs the normal `_gateway_runner.py` in the foreground. None
has a login trigger, interval, `KeepAlive`, or `Restart` policy. This is
intentionally not a replacement for the runner's existing coordination of its
frontdoor and backend children; it only gives the gateway root process a stable
owner outside the terminal host.

## Daily operations

```sh
uv run aletheia gateway start --no-browser
uv run aletheia gateway status
uv run aletheia gateway stop
```

### Windows LAN dashboard access

When `app.bind_host` is `0.0.0.0` or `::`, a Windows host can still reject LAN
clients at Windows Defender Firewall. An interactive `gateway start` or
`gateway restart` checks this once and uses the Windows `runas` secure desktop
to request UAC approval when provisioning is needed. A scheduled or otherwise
non-interactive start never attempts hidden elevation; `aletheia doctor` reports
the missing rule instead.

The explicit, reversible commands are:

```powershell
uv run aletheia gateway firewall status
uv run aletheia gateway firewall enable-lan
uv run aletheia gateway firewall disable-lan
```

The owned rule permits only the configured dashboard TCP port (normally
`8787`) from `LocalSubnet`, for Private and Public profiles, and is scoped to
the actual gateway listener executable. It never permits the supervised backend
port `8788`. If Windows has generated a broad inbound Python block rule, setup
narrows that rule to every TCP port except the dashboard port; removal restores
the original block filter. More specific or centrally managed block rules fail
closed and require administrator review rather than being disabled.

`gateway status` reports both the running PID and whether the current platform
manager owns an on-demand service. On Windows, an interactive logged-in user
can still receive a Task Scheduler-owned service when host policy denies S4U;
`gateway start` never silently falls back to an unowned detached process.

Git operations never start or restart the gateway. A pull, merge, checkout, or
rebase may make the running process report `code_updated_pending_restart`; the
owner applies the new code explicitly with `gateway restart`. Gateway startup and
`doctor --fix` remove obsolete Aletheia-managed auto-restart hooks left by older
checkouts, while leaving unrelated user hooks untouched.

## Deliberate non-goals

- No gateway configuration field, settings toggle, database state, or new API.
- No tmux/screen dependency: those preserve a terminal session, not a durable
  process owner across all supported developer platforms.
- A transient `gateway start` does not install login startup. Persistent login
  or failure recovery requires the explicit service install/enable commands.

## Optional Windows machine service

Unattended operation before user login is an explicit packaged-runtime mode:

```powershell
aletheia gateway service install --backend windows-service
aletheia gateway enable
aletheia gateway start
```

Windows presents its own UAC secure desktop. Aletheia never accepts an
administrator password. The service runs as LocalService with a service SID and
an isolated `%ProgramData%\Aletheia\runtime`; it does not copy the interactive
user's config or credentials. Source checkouts return
`packaged_runtime_required` because granting LocalService access to a personal
repository, venv, and home directory is not a supported deployment.

WinSW 2.12.0 is downloaded before elevation from its official GitHub release and
accepted only when its pinned SHA-256 matches. Elevated installation performs no
network access. `service uninstall` requires the service to be stopped and
preserves the machine runtime data directory.
