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

## Distribution status and external prerequisites

The repository builds an unsigned deterministic macOS arm64 app ZIP and records that fact
in `macos-app-report.json`. Public distribution without Gatekeeper warnings additionally
requires repository-external credentials and release operations:

- Apple Developer ID Application signing certificate;
- Apple notarization credentials;
- an Ed25519 update-signing private key;
- a stable HTTPS feed/artifact location.

These secrets must not be committed. Until configured, the output is appropriate for
owner dogfooding through explicit macOS approval, not public release.

The first release keeps the Rust host intentionally stable. Routine no-touch updates cover
Aletheia's Python runtime, CLI command tree, migrations, dependencies, and Dashboard. A
future host-protocol version bump may require replacing `Aletheia.app`; a candidate
manifest must not claim compatibility with a host version it requires but cannot update.
