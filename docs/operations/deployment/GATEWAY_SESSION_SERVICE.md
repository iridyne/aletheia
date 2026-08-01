# Gateway session service

`uv run aletheia gateway start` is an explicit, on-demand operation. It starts
the existing gateway runner under the current user's operating-system process
manager, verifies `/api/ready` and the dashboard, then returns. Closing the
terminal or ORCA session that ran the command does not stop that managed runner.

`uv run aletheia gateway stop` asks that same manager to stop the runner and
removes its transient definition. The gateway stays stopped until a human runs
`gateway start` again.

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
- No automatic restart or login startup. The separate historical watchdog is
  never registered by `gateway start`; an explicit stop also removes a legacy
  watchdog registration so it cannot resurrect the gateway.
