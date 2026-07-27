# Modular monolith release bundle

Aletheia is deployed as one local product even though development uses Python and
TypeScript. Node.js exists only in CI to compile the React dashboard. The release
bundle contains the Python application plus the compiled static dashboard; the
running machine needs Python 3.12 and `uv`, but it does not need Node.js.

The same portable source artifact is verified on Windows and macOS. Runtime state
is never stored in the release directory.

## Stable runtime shape

### macOS

```text
~/Library/Application Support/Aletheia/
├── current -> releases/<git-sha>/
└── releases/
    ├── <git-sha-A>/            immutable release + locked .venv
    └── <git-sha-B>/

~/.aletheia/                    durable state for the current user
├── app.db
├── config.yaml
├── .env
├── secrets.enc
├── logs/
├── reports/
└── artifacts/
```

### Windows

```text
C:\Aletheia\
└── releases\<git-sha>\        immutable extracted release bundles

C:\Users\<user>\.aletheia\     durable state for the current user
├── app.db
├── config.yaml
├── secrets.enc
├── logs\
├── reports\
└── artifacts\
```

The release directory may be replaced or rolled back. The current user's
`~/.aletheia` directory must never be deleted or included in a release archive.
`ALETHEIA_DATA_DIR` may select a different absolute root explicitly; relative
values fail closed. The current isolated dev API helper remains below
`~/.aletheia/instances/dev-preview` and ignores the primary-runtime override;
it is not yet a Production-grade managed instance.

## Bundle contract

The CI artifact is named:

```text
aletheia-dogfood-<12-char-git-sha>.zip
```

It is a `portable-source` artifact and contains only:

- `aletheia/`, including the already-built `dashboard_static/`;
- `scripts/runtime/`, including Windows and Unix launchers;
- `pyproject.toml` and `uv.lock`;
- optional top-level README/license files;
- `VERSION` and `release-manifest.json`.

It deliberately excludes Git history, tests, documentation sources, frontend
sources, `node_modules`, source maps, caches and all runtime state. The manifest
records the exact Git SHA, Python requirement, supported platforms, entrypoints,
and SHA-256 plus size for every shipped source file. Unix `.sh` entrypoints are
stored with executable mode `0755`. The ZIP is produced with sorted paths and fixed
metadata so the same inputs generate the same bytes.

## Size policy

- soft warning: 60 MiB;
- hard failure: 100 MiB (104,857,600 bytes).

`dist/bundle-size.json` records the compressed/uncompressed size and file count.
The hard limit is enforced by `scripts/release/build_bundle.py`; a bundle above
100 MiB is not published.

## Build locally

First build the dashboard, then the bundle:

```bash
cd frontend
npm ci
npm run lint
npm run build
cd ..
uv run pytest tests/test_release_data_dir.py tests/test_release_bundle.py tests/test_macos_release_runtime.py -q
uv run python scripts/release/build_bundle.py --output-dir dist --warn-mib 60 --max-mib 100
```

The resulting ZIP, `.sha256` sidecar and size report are under `dist/`.

## Install and run on macOS

Download the GitHub Actions artifact and extract its outer archive. It contains the
portable ZIP, its SHA-256 sidecar, `bundle-size.json`, and a standalone installer.

Install into the default user application directory and atomically select it as
`current`:

```bash
bash install-release.sh \
  --archive aletheia-dogfood-<git-sha>.zip \
  --checksum aletheia-dogfood-<git-sha>.zip.sha256
```

The installer:

1. verifies the outer SHA-256 sidecar;
2. extracts into a staging directory under the release root;
3. validates the manifest target and every listed file hash/size;
4. installs into `~/Library/Application Support/Aletheia/releases/<sha>`;
5. runs `uv sync --locked --no-dev` inside that immutable release;
6. atomically updates `~/Library/Application Support/Aletheia/current`.

Start the stable instance:

```bash
bash "$HOME/Library/Application Support/Aletheia/current/scripts/runtime/start-release.sh" \
  --release-root "$HOME/Library/Application Support/Aletheia/current"
```

Probe it:

```bash
bash "$HOME/Library/Application Support/Aletheia/current/scripts/runtime/health-check.sh"
```

Stop it from the selected release:

```bash
cd "$HOME/Library/Application Support/Aletheia/current"
uv run --no-sync aletheia gateway stop
```

The stable launcher refuses a Git checkout, requires `release-manifest.json`, and
accepts only an absolute `--data-dir` override. Without an override it uses
`~/.aletheia`.

### Current stable versus development commands

Only the fixed release directory may control the primary gateway on port 8787:

```text
stable release: gateway start / stop / restart / autostart
Git checkout:   aletheia dev api --port 8791 (or another non-reserved port)
```

The isolated dev API helper receives its own state below
`~/.aletheia/instances/dev-preview/<checkout-id>/<port>` and runs in shadow mode.
It is a migration-era development tool, not the target multi-instance Runtime.
Do not run ordinary `gateway start` from the high-frequency development checkout
until instance-scoped lifecycle is implemented.

## Run an extracted release on Windows

```powershell
pwsh -File scripts/runtime/start-release.ps1 `
  -ReleaseRoot C:\Aletheia\releases\<git-sha>

pwsh -File scripts/runtime/health-check.ps1
```

`start-release.ps1` performs a locked production-only dependency sync and starts
the gateway without opening a browser. `-DataDir C:\absolute\override` is optional;
when omitted, Python selects the running user's `~/.aletheia`.

## CI validation

`.github/workflows/build-dogfood-release.yml` proves the release input in four
ways:

1. validate the per-user runtime-home contract on Ubuntu, macOS and Windows;
2. compile/lint the dashboard and build the deterministic artifact on Windows;
3. extract and bootstrap the artifact in a clean Windows directory using both the
   default home and an explicit absolute override;
4. download that exact artifact on `macos-latest`, install it under a temporary user
   application root, bootstrap a temporary `~/.aletheia`, start the stable gateway,
   pass readiness and health probes, stop it, and prove no LaunchAgent plist or
   repository-local runtime state was created.

## Migrate a legacy checkout

An existing repository `data/` directory is never adopted silently. With the
gateway and Operator Shell stopped, inspect the migration first:

```bash
uv run aletheia runtime migrate-home --from /absolute/path/to/checkout/data --dry-run
uv run aletheia runtime migrate-home --from /absolute/path/to/checkout/data --apply
```

Migration copies into a staging directory, verifies file hashes, config schema,
SQLite integrity and the audit chain, then writes the completion marker last.
PID/lock/WAL/SHM files are regenerated instead of copied. The source directory is
preserved for rollback and requires a separate owner-approved cleanup. When the
source is a legacy checkout `data/` directory, its adjacent repository-root `.env`
is also copied into the runtime home without printing its contents.

Automatic checkpoint promotion and rollback remain a later phase. They must
consume this immutable bundle rather than pull `master` directly.
