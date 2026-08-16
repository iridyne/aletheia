# Modular monolith release bundle

Aletheia is deployed as one local product even though development uses Python and
TypeScript. Node.js exists only in CI to compile the React dashboard. The release
bundle contains the Python application plus the compiled static dashboard; the
running machine needs Python 3.12 and `uv`, but it does not need Node.js.

The same portable source artifact is verified on Windows and macOS. Runtime state
is never stored in the release directory.

## Version identity

Aletheia has one product version, expressed as SemVer `0.MINOR.PATCH` while the
product remains in initial development. `aletheia.__version__` is the only
hand-edited product-version source; Hatch package metadata, the CLI and health
surfaces, `VERSION`, release manifests, Git tags, and GitHub Releases derive
from it. Product tags use `v<version>`.

The immutable build identity is `<version>+<git-sha>`. Schema, prompt, policy,
experiment, inference-contract, and cost-model versions describe compatibility
or evidence boundaries and remain independent of the product version. The
private dashboard and Operator Shell packages and the stable native host also
retain independent component versions unless they become separately shipped
products.

Ordinary Ready-for-review PRs accumulate after the last stable tag. They do not
start Release Please or imply that Fedora should deploy. When the accumulated
changes justify updating the operational instance, an operator explicitly runs
the `Prepare and tag releases` workflow with `workflow_dispatch`. Release Please
then computes one Ready-for-review release PR from the complete Conventional
Commit history since the previous tag.

A release batch is justified by an operational need to update a Runtime, a
coherent behavior/evidence epoch, or a required migration or security fix.
Documentation, CI maintenance, and low-urgency fixes normally accumulate. A
merged issue or PR alone is not a reason to publish another product version.
Release Please keeps its `autorelease: pending` marker on the generated PR and
the workflow adds `do-not-auto-merge` while the candidate is unverified. Release
Please explicitly dispatches the existing read-only CI workflow on the generated
branch because its `GITHUB_TOKEN` cannot trigger the normal pull-request event.
After all required CI jobs succeed, a metadata-only job dispatches the exact
candidate branch, SHA, and merge-base SHA to the trusted auto-merge workflow.
If `master` advances, the candidate remains held until the operator reruns the
manual workflow, which refreshes and re-verifies the batch. Merging the exact
candidate emits a tag-only follow-up; that follow-up verifies the squash parent
against the authorized base before creating the matching tag and metadata-only
GitHub Release. It never opens the next release PR. Failed CI or identity checks
leave the candidate untagged. The workflow does not publish a package, build an
artifact, deploy a Runtime, or open an Observation window.

The workflow invokes the exact `release-please@17.6.0` CLI through the
GitHub-owned Node setup Action. This preserves the repository's restrictive
Action allowlist; it does not require a PAT or an allowlist exception for a
third-party Action.

## Operator release SOP

Run this only when the accumulated changes justify a Runtime update or a new
behavior/evidence epoch. Generating the candidate is one operator action;
Release Please owns the version, changelog, candidate PR, and CI handoff:

```sh
gh workflow run release-please.yml --repo iridite/aletheia --ref master
```

The equivalent UI action is **Actions -> Prepare and tag releases -> Run
workflow**, with `master` selected. Do not hand-edit a target version: the
Conventional Commit history and `release-please-config.json` determine the next
SemVer. In the current pre-1.0 policy, ordinary `feat`, `fix`, and `perf` release
notes produce the next patch; an explicit breaking change produces the next
minor version.

The release is complete only after the generated `autorelease: pending` PR is
observed as merged, its matching immutable `vX.Y.Z` tag exists, and the GitHub
Release exists. A queued or green CI run, an open or mergeable PR, or enabled
auto-merge is not completion. Before the candidate merges, rerun the initial
command if `master` advances or candidate preparation fails. After it merges,
recover a failed tag/GitHub Release follow-up by rerunning that failed
`repository_dispatch` workflow run in the Actions UI or with:

```sh
gh run rerun <run-id> --repo iridite/aletheia
```

Do not use the initial dispatch for post-merge recovery, bypass the candidate
hold or exact-SHA CI, or tag the commit manually. Fedora deployment and
Observation-window admission remain separate downstream gates.

The bootstrap manifest remains at the last released product version (`0.5.0`).
The first generated release PR promotes the version, changelog, and manifest
together to the reviewed `0.6.0` baseline; only its merge may create `v0.6.0`.

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
values fail closed. Preview instances remain isolated below
`~/.aletheia/instances/dev-preview` and ignore the primary-runtime override.

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

### Stable versus development commands

Only the fixed release directory may control the primary gateway on port 8787:

```text
stable release: gateway start / stop / restart / autostart
Git checkout:   aletheia dev api --port 8791 (or another non-reserved port)
```

The development preview receives its own state below
`~/.aletheia/instances/dev-preview/<checkout-id>/<port>` and runs in shadow mode.
Do not run ordinary `gateway start` from the high-frequency development checkout.

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

The active pull-request CI runs the release bundle and packaged-runtime contract
tests but deliberately does not build or distribute release artifacts. There is
currently no active artifact publication workflow. A future publication workflow
must prove the exact final digest on its target platform before distribution;
the version tag alone is not that proof.

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
