# Service Environment

> **Role: Operations.** The runtime runs on Fedora as a user systemd service
> ([Gateway session service](GATEWAY_SESSION_SERVICE.md)); macOS runs the same Gateway only for
> development. Windows is not maintained as a runtime or fallback; its former rules are in
> [deployment history](../../design/history/deployment_retired_sections.md).

The service manager's environment is authoritative for runtime dependencies: an interactive
terminal succeeding does not prove that `systemd --user` (or `launchd` on a development Mac) can
run the same command.

## Rules

1. Resolve required executables in this order: explicit project override, service `PATH`, then
   stable install locations.
2. Do not persist temporary shell-session paths; version-manager session directories (such as fnm
   multishell paths) disappear after logout or reboot.
3. Diagnose a child capability independently of its parent: a healthy Gateway does not prove the
   Node operator shell is online.
4. Historical failures and current faults are different states; only current or repeated faults
   make `aletheia doctor` unhealthy.

## Node operator shell

`core/aletheia/gateway/operator_shell.py::resolve_node_executable` uses:

1. `ALETHEIA_NODE_BINARY`
2. `node` on the service `PATH`
3. stable locations: fnm `aliases/default` (not a `fnm_multishells` path), Volta, asdf, mise,
   `/opt/homebrew/bin`, `/usr/local/bin`
4. literal `node`, so startup still produces an explicit audited error

`operator_shell_root()` honours `ALETHEIA_OPERATOR_SHELL_ROOT` (absolute path to an operator-shell
checkout) instead of `<repo>/operator-shell`. What that process is and how to diagnose it:
[The Operator runtime](../../design/architecture/OPERATOR_SHELL.md).

## Verification

- Start/restart only through `uv run aletheia gateway …` on the runtime checkout; never validate a
  workspace by starting its empty runtime directory.
- After merge and deploy, run `uv run aletheia doctor --json --verbose` on Fedora and confirm
  `process.gateway`, `process.backend` and `operator.shell` separately.
