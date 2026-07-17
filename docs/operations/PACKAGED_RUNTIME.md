# Packaged Runtime Operations

Aletheia's installed product is browser-first. The browser at
`http://127.0.0.1:8787` remains the only product UI; the native component is a small
menu-bar/system-tray host and stable CLI dispatcher.

## Components

- `Aletheia.app`: macOS menu-bar application (`LSUIElement=true`, no Dock window);
- `aletheia-tray`: no-window runtime supervisor and update transaction owner;
- `aletheia`: stable CLI launcher which selects the current packaged payload;
- `ato`: alias of the stable CLI launcher;
- `aletheia-core`: PyInstaller one-folder Python runtime containing the Typer CLI,
  FastAPI gateway, migrations, production dependencies, and compiled Dashboard.

The native host and immutable Python payload are intentionally separate. Routine releases
replace the versioned payload. The host owns process supervision, pointer switching, and
rollback so a failed payload cannot replace the component required to recover it.
The Rust host dependency graph is fixed by the committed `Cargo.lock`; package builds and
tests use Cargo's `--locked` mode rather than resolving a new graph during release creation.

## Installation and first launch

The macOS distribution is:

```text
Aletheia-<version>-darwin-arm64.zip
└── Aletheia.app
```

Move `Aletheia.app` to `/Applications` and open it. First launch:

1. validates the bundled `darwin-arm64` packaged-runtime manifest;
2. rejects symbolic links and entrypoint/path escapes;
3. serializes concurrent first-launch writers with `bootstrap.lock`;
4. copies the immutable payload to the current user's host root;
5. atomically creates `current.json`;
6. starts the runtime without opening a terminal window;
7. exposes the Dashboard at `http://127.0.0.1:8787`.

Default host layout:

```text
~/Library/Application Support/Aletheia/
├── releases/<version+sha>/
├── downloads/
├── preflight/
├── current.json
├── update-state.json
├── update-request.json          # present only during a recoverable switch
├── update-public-key.pem        # optional until online updates are configured
└── update-feed-url.txt          # optional until online updates are configured
```

Durable trading state remains separate under `~/.aletheia` (or an absolute
`ALETHEIA_DATA_DIR` override). Updating or removing an application payload does not remove
`app.db`, config, secrets, logs, reports, artifacts, positions, or audit history.

## CLI

Development checkout:

```bash
uv run aletheia doctor
```

Installed product:

```bash
aletheia doctor
ato gateway status
aletheia update check
aletheia update apply
aletheia update rollback
```

The installed launchers do not resolve Python or `uv`. They validate `current.json` and
execute the selected self-contained payload directly. The app includes
`Contents/Resources/install-cli.sh`, which installs user-local links under
`~/.local/bin` without administrator privileges.

Internal recovery commands remain available:

```bash
aletheia __host status
aletheia __host activate <release-id>
aletheia __host rollback
aletheia __host ensure-started
aletheia __host smoke-test
```

Release IDs, directories, manifests, and entrypoints are fail-closed and cannot escape the
managed release root. A malformed existing `current.json` is reported; it is never treated
as a fresh installation and overwritten.

## Tray behavior

The tray controller starts or observes the selected Gateway and polls readiness and
business health. Its menu provides:

- current health state;
- Open Aletheia in the default browser;
- Check & Install Update;
- Restart Runtime;
- Stop Runtime;
- Quit Aletheia.

The icon distinguishes starting, healthy, updating, degraded, and stopped states. The tray
has no embedded WebView and does not duplicate Dashboard settings or trading controls.

## Signed update transaction

An installed update never modifies a Git checkout and never invokes `uv`.

```text
human Update action
→ verify Ed25519-signed feed
→ select exact platform artifact
→ background download
→ verify declared size and SHA-256
→ reject unsafe archive members/symbolic links
→ verify manifest file set, sizes, and hashes
→ install immutable candidate release
→ create live-consistent SQLite/workspace backup
→ restore backup into preflight data root
→ run candidate migration/integrity/audit checks on the copy
→ write atomic update-request.json
→ Tray pauses new work and stops old runtime
→ atomically select candidate
→ apply already-preflighted migration to durable data
→ start readiness/health checks and soak
→ commit, or restore backup and previous release
```

The browser polls `/api/update/progress`. Background stages remain usable; during the short
runtime switch it retains the last truthful state, reconnects automatically, and reloads
once when the selected frontend changes.

If the staging Python process dies, stale `checking/downloading/preflight` labels are not
permanent authority: a live cross-process lock/PID is required. A durable
`update-request.json`, however, remains recoverable by the Tray after a crash or login.

Manual rollback is available only for a short window after a committed update and only
when the exact pre-update backup is still present. It restores data before starting the
previous code version.

## Release feed and credentials

The feed envelope contains an exact JSON string plus an Ed25519 signature. The private key
must exist only in release infrastructure. The installed app contains only the public key.
The signed payload binds:

- release ID and version;
- Git SHA;
- publication time;
- platform-specific artifact URL;
- archive format, byte size, and SHA-256.

Set a stable feed URL and public key at build/bootstrap time, or use:

```text
ALETHEIA_UPDATE_FEED_URL
ALETHEIA_UPDATE_PUBLIC_KEY_PATH
ALETHEIA_UPDATE_BEARER_TOKEN   # optional private artifact host
```

Without a public key and feed URL, the installed runtime remains fully usable but reports
`update_feed_not_configured`; it does not silently fall back to Git or unsigned downloads.

### Owner dogfood publication channel

The repository's `Build Packaged Tray CLI Runtime` workflow is also the release publisher.
Run it manually from the `master` branch with `publish=true`. A publishing run:

1. checks out the exact `master` SHA selected by the workflow run;
2. builds and smokes the self-contained `darwin-arm64` Runtime, stable CLI, and Tray app;
3. signs the Runtime metadata with the repository secret
   `ALETHEIA_UPDATE_SIGNING_PRIVATE_KEY_PEM`;
4. embeds only the matching public key and the stable Feed URL in `Aletheia.app`;
5. deploys the signed Feed, immutable Runtime archive, checksums, and fresh-install App ZIP
   to the public GitHub Pages endpoint.

Publishing uses the repository's dedicated Apple Silicon self-hosted Runner labels:

```text
self-hosted, macOS, ARM64, aletheia-release
```

This keeps manual owner releases available when GitHub-hosted Actions minutes are exhausted.
Pull-request builds do not select this Runner; only an explicit `workflow_dispatch` on
`master` with `publish=true` can run release code on the owner machine.

Stable endpoints:

```text
Feed:       https://iridite.github.io/aletheia/update-feed.json
Public key: https://iridite.github.io/aletheia/update-public-key.pem
Artifacts:  https://iridite.github.io/aletheia/<versioned archive name>
```

The source repository remains private. Pages is used because an installed product cannot
download assets from a private GitHub Release without holding a GitHub credential. The
public channel contains compiled release files and signed metadata only; it does not expose
the private repository or grant repository access.

The signing secret must be an unencrypted Ed25519 PKCS#8 PEM. Generate and store it outside
the repository, then add it under **Repository Settings → Secrets and variables → Actions**.
Never reuse the private key as an application secret, commit it, print it, or copy it into
`~/.aletheia`. Rotating this trust root requires distributing a new installer containing the
new public key; an existing signed Feed cannot authorize its own replacement key.

After a successful publishing run, verify from an installed package:

```bash
aletheia update check
aletheia update apply --yes
aletheia update status --json
```

`update check` verifies the signed envelope before reporting a release. `update apply`
downloads and verifies the archive, preflights it, and leaves the short activation and
rollback transaction to the Tray.

Installing a Feed-enabled `Aletheia.app` over an earlier owner-dogfood build seeds a
missing public key and Feed URL on the next Tray/CLI start without replacing
`current.json`, the selected Runtime, or any existing trust root. This makes the first
online-update-capable installer an in-place upgrade for current local installations.
The new candidate also treats the legacy pre-request validation command as isolated
preflight, so the previously installed Runtime can stage this first signed update safely;
after the request is written, the same command retains its activation-only semantics.

### Private one-shot local release session

For owner dogfooding, GitHub Actions and a permanent release server are optional. The
development checkout provides a local-only release session that builds one fresh payload,
signs a Feed with the existing owner key, serves it on `127.0.0.1` only while staging, and
then waits for the normal Tray transaction to finish. The temporary server always stops on
success, failure, or interruption.

Run this only from a clean, intended release worktree and only while the installed Tray is
running:

```bash
uv run python scripts/release/local_release_session.py \
  --cli "$(command -v aletheia)"
```

The command uses the owner key at
`~/.aletheia/release-signing/update-signing-private-key.pem` by default. Supply an absolute
`--private-key` path only when the key is intentionally stored elsewhere. It first compares
the derived public key with the installed App's trust root and refuses to stage if they do
not match. It does not print, copy, or commit the private key.

Each invocation writes an auditable session directory under
`dist/local-release-sessions/` containing the immutable archive, checksum, signed Feed,
and public key. Its Feed URL is passed only to the invoking installed CLI through
`ALETHEIA_UPDATE_FEED_URL`; the permanent installed Feed configuration is not modified.
The Tray receives an already staged `update-request.json`, so it does not need the local
server after staging begins. A local build can use a distinct `.local-*` release label,
but the updater treats an equal immutable `git_sha` as already current. For a different
SHA, it only accepts a normal Feed whose signed `published_at` is later than the local
release timestamp; an older public Feed cannot silently downgrade a newer local dogfood
build. A later signed Feed remains eligible. The helper refuses a dirty worktree, so the
payload code always matches the Git SHA recorded in its manifest.

This mode is intentionally local to one machine. A `127.0.0.1` Feed cannot update UM790 or
another Mac. Cross-machine private distribution needs a separately authenticated private
artifact endpoint or a deliberate signed-file transfer; do not expose this helper on a
LAN interface by changing its bind address.

## Distribution status and external prerequisites

The repository builds an unsigned deterministic macOS arm64 app ZIP and records that fact
in `macos-app-report.json`. The signed owner-dogfood update channel is implemented through
GitHub Pages, but a publication run still requires Actions capacity and the external
Ed25519 signing secret described above. Distribution without Gatekeeper warnings additionally
requires repository-external Apple credentials:

- Apple Developer ID Application signing certificate;
- Apple notarization credentials;
- an Ed25519 update-signing private key configured as the Actions secret;
- GitHub Pages enabled by the publishing workflow for the stable HTTPS channel.

These secrets must not be committed. Until configured, the output is appropriate for
owner dogfooding through explicit macOS approval, not public release.

The first release keeps the Rust host intentionally stable. Routine no-touch updates cover
Aletheia's Python runtime, CLI command tree, migrations, dependencies, and Dashboard. A
future host-protocol version bump may require replacing `Aletheia.app`; a candidate
manifest must not claim compatibility with a host version it requires but cannot update.
