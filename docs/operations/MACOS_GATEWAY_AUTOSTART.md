# macOS Gateway Autostart

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

## Commands

```bash
uv run aletheia gateway enable-autostart
uv run aletheia gateway status
uv run aletheia doctor
uv run aletheia gateway disable-autostart
```

`gateway start` also installs the watchdog automatically when
`runtime.autostart_on_start=true`.

## Honest States

- `enabled=true`: `launchctl print gui/<uid>/<label>` confirms the job is loaded.
- `installed=true, enabled=false, reason=not_loaded`: plist exists but launchd is
  not supervising it; reinstall with `enable-autostart`.
- `config_valid=false`: loaded/file definition points at a stale interpreter or
  project path; reinstall after moving the checkout or rebuilding `.venv`.
- `primed=false`: launchd has not completed a successful check yet.
- `enabled=null`: launchctl state could not be read; the UI/doctor must not claim
  protection.

The periodic process normally reports `state = not running`: that means the
short check exited, not that autostart is broken. `runs > 0` and last exit code 0
are the successful periodic-job evidence.

## Verification

After installation:

1. Confirm `doctor` reports `process.autostart` as `ok` via launchd.
2. Stop only the gateway using `uv run aletheia gateway stop`.
3. Wait at most 90 seconds and confirm the gateway returns on `:8787`.
4. Confirm `gateway status` and `doctor` still report live disabled unless a human
   explicitly changed it.

Disabling autostart intentionally prevents step 3; it does not stop an already
running gateway.
