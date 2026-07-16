# Packaged Tray, CLI, and Runtime Plan

## Product contract

Aletheia remains a browser application. The installed desktop surface is deliberately
small:

- `aletheia-tray` is a background system-tray/menu-bar controller;
- `aletheia` and `ato` are stable command-line launchers;
- `aletheia-core` is the versioned, self-contained Python runtime and CLI payload;
- the only product UI is the dashboard served at `http://127.0.0.1:8787`;
- all durable state remains outside release directories under `~/.aletheia`.

The installed product must not require a system Python, `uv`, Node.js, a Git checkout,
or an editable Python environment.

## Runtime layout

### macOS

```text
/Applications/Aletheia.app/
└── Contents/MacOS/
    ├── aletheia-tray
    └── aletheia

~/Library/Application Support/Aletheia/
├── releases/
│   ├── <version-or-sha>/
│   │   ├── aletheia-core
│   │   ├── _internal/
│   │   └── release-manifest.json
│   └── ...
├── downloads/
├── current.json
└── update-state.json

~/.aletheia/
├── app.db
├── config.yaml
├── secrets.enc
├── logs/
├── backups/
└── artifacts/
```

Windows uses the same logical structure below `%LOCALAPPDATA%\Aletheia`, while durable
runtime state continues to use the selected per-user Aletheia data directory.

## Responsibilities

### Tray controller

The tray controller owns process and release lifecycle, not trading semantics. Its
minimum surface is:

- start the selected `aletheia-core` release;
- poll `/_infra/readyz` and reflect health in the tray icon;
- open the dashboard in the user's default browser;
- restart or stop the runtime without opening a terminal window;
- retain a single product instance;
- later consume signed update metadata, stage a candidate, switch atomically, and
  roll back when startup or migration validation fails.

It must never inspect or modify positions, strategies, credentials, risk limits, or
exchange state.

### Stable CLI launcher

`aletheia` and `ato` are stable launchers. They read `current.json`, locate the selected
release's `aletheia-core`, forward every argument, preserve stdout/stderr, and return the
payload's exact exit code. On Unix the launcher replaces itself with the payload process;
on Windows it waits and propagates the child's exit status.

This preserves the current Typer command contract:

```text
aletheia version
aletheia doctor
aletheia gateway status
aletheia runtime status
ato version
```

No installed command requires the development prefix `uv run`.

### Python payload

PyInstaller one-folder output is used instead of one-file output. The payload contains
one Python interpreter/dependency closure, the current Typer CLI, FastAPI gateway,
migrations, and the compiled React dashboard. One-folder packaging avoids per-launch
self-extraction and makes native-library failures diagnosable.

## Release selection contract

`current.json` is a small atomic pointer:

```json
{
  "schema_version": 1,
  "current": "0.6.0+<sha>",
  "previous": "0.5.0+<sha>"
}
```

A release is selectable only when:

- its directory is directly below the configured release root;
- `release-manifest.json` parses and declares a packaged runtime target;
- the declared runtime entrypoint is a relative path contained by that release;
- the entrypoint exists and is executable on Unix;
- the manifest platform/architecture matches the host.

Activation writes a temporary pointer and renames it atomically. Rollback exchanges
`current` and `previous`; it never edits a release directory.

## Update transaction target

The long-term button flow is one transaction shared by the browser, tray menu, and CLI:

1. download while the current runtime remains available;
2. verify transport hash and release signature;
3. unpack into a staging directory and validate every manifest file;
4. run the candidate executable and migration preflight against a copied database;
5. pause new autonomous work and stop the old runtime;
6. create a consistent pre-update database/config backup;
7. activate the candidate and run real migrations;
8. require readiness, health, and a short soak;
9. commit the pointer or automatically restore the backup and previous release.

The first implementation milestone proves the packaging and launch boundary. Network
release feeds, signing-key operations, destructive migration approval, and delta updates
are later increments on the same state model; they must not cause the first payload to
pull or overwrite its own executable.

## Validation gates

The packaged-runtime workflow must prove:

- the React dashboard is compiled before packaging;
- `aletheia-core` starts without system Python or `uv` in its command path;
- `version`, `--help`, `doctor --help`, and `gateway --help` work;
- the packaged gateway serves readiness and health from an isolated temporary HOME;
- the stable Rust launcher follows `current.json` and propagates output/exit codes;
- activation and rollback reject malformed or escaping paths;
- the tray smoke mode starts or observes the packaged runtime and reaches readiness;
- packaged files contain no repository-local runtime state.

Tests are concentrated on release selection, process handoff, failure recovery, and the
actual packaged CLI/gateway path. Cosmetic tray behavior is not expanded into brittle
mock-only tests.