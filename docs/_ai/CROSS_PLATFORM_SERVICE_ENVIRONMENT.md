# Cross-platform service environment

This project runs as a long-lived background service on both Windows and macOS.
The service manager environment is authoritative for runtime dependencies; an
interactive terminal succeeding does not prove that Task Scheduler or launchd
can run the same command.

## Rules

1. Resolve required executables with this order: explicit project override,
   service `PATH`, then stable platform-specific install locations.
2. Do not persist temporary shell-session paths. Version-manager session paths
   such as fnm multishell directories can disappear after logout or reboot.
3. Keep Windows behavior covered when adding macOS support. Platform additions
   must not replace existing PATH discovery or Windows process handling.
4. Diagnose the child capability independently from its parent process. A
   healthy gateway does not prove the Node operator shell is online.
5. Historical failures and current faults are different states. Preserve both,
   but only current or repeated faults should make `aletheia doctor` unhealthy.

## Node operator shell

`aletheia.gateway.operator_shell.resolve_node_executable()` uses:

1. `ALETHEIA_NODE_BINARY`
2. `node` found on the service `PATH`
3. stable fnm, Volta, asdf, mise, Homebrew, or `/usr/local` locations
4. literal `node`, so process startup still produces an explicit audited error

The macOS fallback deliberately uses fnm's `aliases/default` path, not a
`fnm_multishells` path created for one terminal session.

## Verification checklist

- Run focused lifecycle, operator-shell supervision, and doctor tests.
- Start/restart through `uv run aletheia gateway ...`; never validate a worktree
  by starting its empty runtime directory.
- Run `uv run aletheia doctor --json --verbose` from the main checkout after
  merge and restart.
- Confirm `process.gateway`, `process.backend`, and `operator.shell` separately.
- On platform-specific changes, retain at least one test for the other platform's
  path/process convention.

## Known platform pressure points

- Service-manager environment and executable discovery
- Process-tree ownership, replacement launchers, and signal/exit-code semantics
- Path separators, quoting, case sensitivity, and executable suffixes
- File locking and atomic replace behavior
- Console encoding and Unicode-safe CLI output
- Login/reboot registration and watchdog status parsing

Add focused evidence as these areas are exercised; do not infer compatibility
from a successful interactive run.
