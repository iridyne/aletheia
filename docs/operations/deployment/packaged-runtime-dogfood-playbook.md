# Packaged Runtime dogfood playbook

Use this runbook when an exact `master` commit must become a macOS Runtime Instance
without disturbing the user's existing Aletheia state. It is written for an operator or
later agent that needs to find the release path quickly. Read
[Packaged Runtime Operations](../PACKAGED_RUNTIME.md) for the complete implementation
and trust-model detail.

## First principle and boundary

The deployment unit is an immutable, platform-specific Runtime Payload. The installed
Rust Tray is the only owner of process lifecycle, version selection, migration, and
rollback. The browser remains the only product UI at `http://127.0.0.1:8787`.

```text
exact master SHA
→ signed immutable Runtime Payload
→ download/stage/preflight against a copy of durable state
→ short Tray-owned switch
→ readiness + health soak
→ commit, or restore the backup and previous payload
```

The code payload and durable state must remain separate:

```text
/Applications/Aletheia.app                         stable Tray and CLI host
~/Library/Application Support/Aletheia/releases/   immutable Runtime versions
~/.aletheia/                                       database, config, secrets, logs, artifacts
```

Never use any of the following as an installed Runtime deployment mechanism:

- `git pull` in an installed release directory;
- `uv run aletheia gateway start` from a development checkout;
- Docker Compose as a replacement for the installed Runtime;
- deleting `~/.aletheia` to make an update succeed;
- copying a release directory over the active version by hand.

Those paths bypass the Tray's migration, pointer-switch, health, and rollback contract.

## Choose the release lane

| Need | Use | Network/service requirement |
| --- | --- | --- |
| Dogfood one exact commit on this Mac | Private one-shot local session | None beyond the local machine; temporary `127.0.0.1` Feed only |
| Let installed Macs discover a signed update | Owner publication channel | GitHub Actions capacity, signing secret, GitHub Pages, temporary release Runner |
| Develop an unfinished change | Development preview | Separate preview port and preview state; not an installed Runtime update |

For a new `0.5.1` commit, always use the private one-shot session first. Promote to the
owner Feed only after that installed Runtime passes acceptance.

## Local `0.5.1` dogfood procedure

### 1. Select an exact, clean source

Create or use a dedicated release worktree at the exact merged `master` commit. Do not
release from a shared development checkout with local edits. The helper rejects a dirty
worktree because the payload must exactly match the Git SHA recorded in its manifest.

```bash
git fetch origin master
git worktree add ../aletheia-release-0.5.1 origin/master
cd ../aletheia-release-0.5.1
git status --short
uv sync
uv run aletheia doctor
```

`git status --short` must produce no output. `doctor` may report an intentional
operational gate such as `trading_paused`; do not clear a safety gate merely to release
test a Runtime.

### 2. Confirm the installed host is the control plane

The Tray must already be running. The command below reports the selected immutable
payload and its previous version without starting another Gateway:

```bash
aletheia __host status
```

If it is not running, launch `/Applications/Aletheia.app`; do not start `:8787` from the
release worktree.

### 3. Build, sign, stage, and activate locally

```bash
uv run python scripts/release/local_release_session.py \
  --cli "$(command -v aletheia)"
```

This single command builds the Dashboard and macOS arm64 self-contained payload, signs a
Feed with the owner's existing local signing key, and serves that Feed only on a random
`127.0.0.1` port. The installed CLI stages the normal signed update; the Tray performs
the actual short switch. The helper waits for a terminal update state and always stops the
temporary local server.

The session directory under `dist/local-release-sessions/` is retained as audit evidence.
It contains the archive, checksum, signed Feed, and public key, but never copies or logs
the private key.

### 4. Accept or reject the installed Runtime

```bash
aletheia update status --json
aletheia __host status
curl -fsS http://127.0.0.1:8787/_infra/readyz
curl -fsS http://127.0.0.1:8787/api/health
```

Accept only when all of the following are true:

- `update status` has `status: success` (or `up_to_date` for an identical payload);
- `__host status` names the expected `0.5.1+<sha>.local-...` Runtime and preserves a
  `previous` version;
- `/_infra/readyz` returns `ok: true`;
- the browser loads from `http://127.0.0.1:8787` after the switch;
- persistent configuration and trading history remain present.

`/api/health` can truthfully remain `degraded` when an existing safety gate such as
`trading_paused` is active. That is not a deployment failure. Do not resume trading as
part of package acceptance.

If the candidate fails after switching, the Tray restores the exact update backup and
previous payload. Preserve the session directory and inspect `aletheia update status
--json`; do not manually delete releases or databases.

## Promote the accepted `0.5.1` to the owner Feed

Use this only after local dogfood acceptance. In GitHub Actions, manually dispatch
**Build Packaged Tray CLI Runtime** from `master` with `publish=true`. It builds the exact
selected `master` SHA, signs the Feed, and publishes the immutable archive and Feed to
GitHub Pages.

Publication requires:

- `ALETHEIA_UPDATE_SIGNING_PRIVATE_KEY_PEM` configured as a repository Actions secret;
- GitHub Pages enabled for the release endpoint;
- an available temporary macOS Apple Silicon Runner bearing
  `self-hosted`, `macOS`, `ARM64`, and `aletheia-release` labels.

The Runner can be started for this manual publication and stopped immediately after it
finishes; it does not need to be a permanent CI worker. Never publish from a feature
branch: the workflow explicitly requires `master` for `publish=true`.

Then test the public path from an installed Mac:

```bash
aletheia update check
aletheia update apply --yes
aletheia update status --json
```

The public Feed must be newer than any current `.local-<UTC timestamp>` dogfood release.
An equal Git SHA is intentionally a no-op, and an older Feed is intentionally refused to
prevent a local dogfood build from being silently downgraded.

## Fast diagnosis

| Observation | Meaning and next safe action |
| --- | --- |
| Tray icon absent but `:8787` responds | Treat Tray visibility and Runtime reachability separately; inspect the installed App/Tray process before touching the Gateway. |
| `update_feed_not_configured` | The Runtime is usable; its installed Feed URL or public key is absent. Install a Feed-enabled App or use the private one-shot session. |
| `update check` fails with network/TLS error | Preserve the current Runtime. This is Feed reachability, not a reason to replace code or data manually. |
| `up_to_date` after a local session | Expected when the installed immutable payload already has the same Git SHA. |
| Candidate update fails | Read `aletheia update status --json`; the Tray should retain/restore `previous` and the pre-update backup. |
| 8787 reports `trading_paused` | Existing safety posture. Record it, but do not alter trading permissions during release acceptance. |

## Agent closure checklist

Before saying a Runtime release is complete, record:

1. exact `master` SHA and clean worktree proof;
2. payload version, SHA-256, and signed Feed source;
3. update status, selected/previous Runtime IDs, readiness, and health results;
4. whether the Feed was local-only or published;
5. any pre-existing health/safety gates left unchanged;
6. the targeted regression tests and their result.

Do not mark an update complete merely because an archive was built or a browser page still
loads. The release is accepted only after the installed Tray has selected the candidate
and the Runtime passes readiness with durable state intact.
