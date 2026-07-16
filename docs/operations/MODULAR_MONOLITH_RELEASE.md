# Modular monolith release bundle

Aletheia is deployed as one local product even though development uses Python and
TypeScript.  Node.js exists only in CI to compile the React dashboard.  The
runtime bundle contains the Python application plus the compiled static dashboard;
UM790 runs one Python gateway, one scheduler and one SQLite state root.

## Runtime shape

```text
C:\Aletheia\
├── releases\<git-sha>\        immutable extracted release bundles

C:\Users\<user>\.aletheia\     default durable state for the running user
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
values fail closed. Preview instances remain isolated below
`~/.aletheia/instances/dev-preview` and ignore the primary-runtime override.

## Bundle contract

The CI artifact is named:

```text
aletheia-dogfood-<12-char-git-sha>-win-x64.zip
```

It contains only:

- `aletheia/`, including the already-built `dashboard_static/`;
- `scripts/runtime/`;
- `pyproject.toml` and `uv.lock`;
- optional top-level README/license files;
- `VERSION` and `release-manifest.json`.

It deliberately excludes Git history, tests, docs sources, frontend sources,
`node_modules`, source maps, caches and all runtime state.  The manifest records
the exact Git SHA, Python requirement, data-directory contract, and SHA-256 plus
size for every shipped source file.  The ZIP is produced with sorted paths and
fixed metadata so the same inputs generate the same bytes.

## Size policy

- soft warning: 60 MiB;
- hard failure: 100 MiB (104,857,600 bytes).

`dist/bundle-size.json` records compressed/uncompressed size and file count.
The hard limit is enforced by `scripts/release/build_bundle.py`; a bundle above
100 MiB is not published.

## Build locally

First build the dashboard, then the bundle:

```powershell
cd frontend
npm ci
npm run lint
npm run build
cd ..
uv run pytest tests/test_release_data_dir.py tests/test_release_bundle.py -q
uv run python scripts/release/build_bundle.py --output-dir dist --warn-mib 60 --max-mib 100
```

The resulting ZIP and `.sha256` sidecar are under `dist/`.

## Run an extracted release

UM790 needs Python 3.12 and `uv` installed once.  Node.js is not required at
runtime.

```powershell
pwsh -File scripts/runtime/start-release.ps1 `
  -ReleaseRoot C:\Aletheia\releases\<git-sha>

pwsh -File scripts/runtime/health-check.ps1
```

`start-release.ps1` performs a locked production-only dependency sync and starts
the gateway without opening a browser. `-DataDir C:\absolute\override` is
optional; when omitted, Python selects the running user's `~/.aletheia`. The health probe requires both
`/_infra/readyz` and `/api/health` to respond.

## Current automation boundary

`.github/workflows/build-dogfood-release.yml` currently proves the release input:

1. compile and lint the dashboard;
2. run focused release tests;
3. create the deterministic ZIP;
4. extract it into a clean directory;
5. install production dependencies from the lockfile;
6. bootstrap once with the default per-user `~/.aletheia` root and once with an
   explicit absolute override;
7. upload the ZIP, checksum and size report.

## Migrate a legacy checkout

An existing repository `data/` directory is never adopted silently. With the
gateway and operator shell stopped, inspect the migration first:

```powershell
uv run aletheia runtime migrate-home --from C:\path\to\checkout\data --dry-run
uv run aletheia runtime migrate-home --from C:\path\to\checkout\data --apply
```

Migration copies into a staging directory, verifies file hashes, config schema,
SQLite integrity and the audit chain, then writes the completion marker last.
PID/lock/WAL/SHM files are regenerated instead of copied. The source directory
is preserved for rollback and requires a separate owner-approved cleanup. When
the source is a legacy checkout `data/` directory, its adjacent repository-root
`.env` is also copied into the runtime home without printing its contents.

Automatic UM790 promotion, database backup, process drain and rollback are the
next phase.  They must consume this immutable bundle rather than pull `master`
directly.
