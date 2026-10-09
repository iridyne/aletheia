> 2026-10-09 迁入 history：原路径 docs/operations/deployment/MACOS_GATEWAY_AUTOSTART.md。只维护 Linux systemd user session（见 platform_specific_gateway_services.md）。

# Historical macOS gateway watchdog

> Current builds no longer expose or install this watchdog. It remains documented
> only to identify registrations created by older versions; the current
> `gateway enable` and `gateway stop` paths remove it.

## Architecture

Aletheia installs a user LaunchAgent at:

`~/Library/LaunchAgents/com.aletheia.trading-os.gateway.plist`

The agent runs the same gated command used by an operator:

`<absolute .venv python> -m aletheia.main gateway start --no-browser`

It runs once at login and every 60 seconds. `gateway start` is idempotent: when
the supervisor is healthy it records an already-running watchdog tick and exits;
when the supervisor is absent it starts and verifies the normal three-process
gateway. The LaunchAgent does not manage frontdoor/backend separately.

`StartInterval` is intentional. The command exits after checking/starting the
gateway, so `KeepAlive` would create a rapid relaunch loop. `AbandonProcessGroup`
allows the detached supervisor to remain alive after the check process exits.

## Safety

- User LaunchAgent only; no root, sudo, daemon domain, or privilege escalation.
- Plist contains no API keys, tokens, environment variables, or secret paths.
- Live trading remains disabled unless the existing human live-unlock path is used.
- Installation and removal are human-triggered and audit logged.
- The plist is validated, written atomically, mode 0644, then loaded with
  `launchctl bootstrap gui/<uid>` and primed with `launchctl kickstart`.

For the normal explicit start/stop lifecycle, use
[Gateway session service](../../operations/deployment/GATEWAY_SESSION_SERVICE.md). Do not recreate this
periodic job.

## Honest States

- `enabled=true`: `launchctl print gui/<uid>/<label>` confirms the job is loaded.
- `installed=true, enabled=false, reason=not_loaded`: an old plist exists but
  launchd is not supervising it; it can be removed safely.
- `config_valid=false`: loaded/file definition points at a stale interpreter or
  project path; reinstall after moving the checkout or rebuilding `.venv`.
- `primed=false`: launchd has not completed a successful check yet.
- `enabled=null`: launchctl state could not be read; the UI/doctor must not claim
  protection.

The periodic process normally reports `state = not running`: that means the
short check exited, not that autostart is broken. `runs > 0` and last exit code 0
are the successful periodic-job evidence.

## Migration

Run `uv run aletheia gateway enable` to migrate the old watchdog. The command
first installs and validates the current persistent session-service definition
and enables its login recovery. It then removes only a historical label whose
plist—and, when loaded, live `launchctl` definition—proves the active checkout's
expected Aletheia command and interpreter path.
If cleanup fails while a verified old recovery definition remains, a newly
enabled replacement is disabled again. If all verified old definitions were
already removed before the failure, or the replacement was already enabled,
the replacement stays enabled to avoid a recovery-owner gap. An ambiguous or
inaccessible definition is never deleted.

`gateway restart` performs this migration before stopping, so a legacy-only host
retains login recovery after the transient Gateway process restarts. To remove a
verified old watchdog and stop the current Gateway session, run
`uv run aletheia gateway stop`. Stop preserves the
current service's login-recovery setting; run `uv run aletheia gateway disable`
before stop when the Gateway must remain stopped after the next login.
If the runtime stop succeeds but legacy cleanup fails, the CLI still completes
PID, orphan, tunnel, and port teardown, then reports the recovery cleanup as a
partial-stop error.
