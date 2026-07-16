# Packaged Runtime Operations

This document describes the first packaged Aletheia host/runtime milestone. The browser
at `http://127.0.0.1:8787` remains the only product UI.

## Components

- `aletheia-tray`: no-window menu-bar/system-tray controller;
- `aletheia`: stable CLI launcher which selects the current packaged payload;
- `ato`: alias of the stable CLI launcher;
- `aletheia-core`: PyInstaller one-folder Python runtime containing the Typer CLI,
  FastAPI gateway, migrations, and compiled dashboard.

## Development versus installed commands

Development checkout:

```bash
uv run aletheia doctor
```

Installed product:

```bash
aletheia doctor
ato gateway status
```

The installed launchers do not resolve Python or `uv`. They read the host's atomic
`current.json` and execute the selected payload directly.

## Host root override

Normal installs use the platform-local application data directory. Tests and operators
may select an explicit host root:

```bash
export ALETHEIA_HOST_ROOT="$HOME/Library/Application Support/Aletheia"
```

The host root contains immutable release directories and version pointers. It is distinct
from `ALETHEIA_DATA_DIR`, which selects durable runtime state and defaults to
`~/.aletheia`.

## Internal host diagnostics

The stable launcher exposes deliberately hidden host commands for installation and
recovery tooling:

```bash
aletheia __host status
aletheia __host activate <release-id>
aletheia __host rollback
aletheia __host ensure-started
aletheia __host smoke-test
```

Release IDs and entrypoints are validated and cannot escape the configured release root.
Activation and rollback update only `current.json`; they do not edit immutable payloads.

## Tray behavior

The tray controller starts or observes the selected gateway and polls readiness and
business health. Its menu provides:

- current state;
- Open Aletheia (default browser, port 8787);
- Restart Runtime;
- Stop Runtime;
- Quit Aletheia.

The tray has no embedded web view and does not duplicate Dashboard settings or trading
controls.

## Current milestone boundary

This milestone proves the self-contained runtime and stable host boundary. It deliberately
does not yet claim production online-update completion. The next increment will add a
signed release feed, background download/staging, database-copy migration preflight,
short quiesce/switch, health soak, and automatic backup restoration/rollback using these
same immutable releases and pointer files.
