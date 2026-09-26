# Modular monolith release bundle

> **2026-09-26 update.** The auto-merge control plane and its release
> handoff (`auto-merge.yml`, `refresh-release-candidate.yml`,
> `release-ci-handoff.yml`) were retired in PR #3506. The owner now merges
> each release candidate by hand with `gh pr merge <n> --rebase`; the
> tag/Release follow-up and the Fedora Paper timer are unchanged. This
> document has been updated to describe the current path; a short section
> below records the retired candidate-refresh automation for archival
> reasons only.

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

Release preparation keeps the repository's operational-need batch gate. It does
not run on every `master` push or on an unconditional timer. An owner selects
`operational_need=true` in the `Prepare and tag releases` workflow when the
accumulated Conventional Commit history needs a Runtime update; Release Please
then computes one Ready-for-review release PR from the history since the
previous tag. Ordinary `feat`, `fix`, and `perf` changes therefore accumulate
instead of creating one product version per merge, and hidden `docs`, `ci`, and
`chore` changes remain accumulated.

The generated release PR is a normal PR. The owner merges it manually with
`gh pr merge <n> --rebase` (see the 2026-09-26 decision *"PR 合并方式 = 人工
`gh pr merge --rebase`"* in `docs/governance/decisions/DECISIONS.md`).

> ⚠️ **Tag follow-up currently has no automatic dispatcher.** The
> `release_candidate_merged` `repository_dispatch` used to be fired by the
> now-deleted `auto-merge.yml`; nothing else emits it. Merging the release
> PR manually will not automatically create the matching `vX.Y.Z` tag or
> GitHub Release. Until this gap is closed (a separate D1 decision — do we
> keep tagged releases at all under the rolling-update policy?), the tag
> must be produced by hand after merge. Two workable paths:
>
> ```sh
> # Path A: fire the same repository_dispatch by hand
> gh api repos/iridyne/aletheia/dispatches \
>   -f event_type=release_candidate_merged \
>   -f 'client_payload[merge_commit_sha]=<merge sha>' \
>   -f 'client_payload[expected_base_sha]=<parent sha>'
>
> # Path B: skip release-please and tag directly (loses the merge-parent audit)
> git tag v<X.Y.Z> <merge sha> && git push origin v<X.Y.Z>
> ```
>
> If the owner decides to remove tagged releases entirely (rolling update
> only), this whole subsection can go with the `release-please.yml`
> retirement.

Assuming a `v<X.Y.Z>` tag exists (created by either path above), the
`release-please.yml` `repository_dispatch` handler verifies the merge parent
against the authorized base and creates the matching immutable tag and
metadata-only GitHub Release. Failed identity checks leave the candidate
untagged. The tag follow-up does not publish a package, build an artifact,
deploy a Runtime, or open an Observation window; the Fedora Paper timer
consumes the stable tag as a separate downstream gate.

The workflow invokes the exact `release-please@17.6.0` CLI through the
GitHub-owned Node setup Action. This preserves the repository's restrictive
Action allowlist; it does not require a PAT or an allowlist exception for a
third-party Action.

### Retired candidate-refresh automation

Before 2026-09-26, `Refresh release candidate after master advances` observed
a completed `trusted auto-merge` run, confirmed that an active
`autorelease: pending` candidate had a stale branch merge-base, and dispatched
a refresh authenticated to that workflow run. That whole leg — the
`refresh-release-candidate.yml` workflow, the `trusted auto-merge` handoff,
the automatic hold-release — was retired in PR #3506 together with the
`auto-merge.yml` control plane.

If an ordinary PR advances `master` while a candidate is still open, the
owner reruns `Prepare and tag releases` (Release Please updates the same
`autorelease: pending` candidate without creating a second release PR). CI is
never dispatched automatically as part of this refresh.

## Release SOP（正常自动，故障手动恢复）

正常路径:

1. **owner 触发**：`Prepare and tag releases` workflow，`operational_need=true`。
   workflow 通过 GitHub API fail-closed 验证触发者是 repository admin，生成候选 PR。
2. **不会自动启动 CI**。只有 owner 在当前任务中明确要求远程 CI，才可在候选分支上
   手动 dispatch `Aletheia CI`。发布、部署或"完整验证"的表述本身不构成授权。
3. **owner 手工合并**候选 PR：`gh pr merge <n> --rebase`（2026-09-26 决策）。
4. **owner 手工触发 tag follow-up**（见上一节 ⚠️ 提示框）。合并本身**不会**
   自动触发——`release_candidate_merged` 事件原本由 `auto-merge.yml` dispatch，
   该 workflow 已在 PR #3506 中删除。走 `gh api ... dispatch` 路径可以让
   `release-please.yml` 验证 merge parent 后创建 tag；走 `git tag && git push`
   直接打 tag 则绕过这层验证。是否保留 tagged release 是独立的 D1 决策。
5. **Fedora Paper timer** 每 5 分钟检查稳定 Tag，按角色执行部署（`aletheia update
   auto run`）。资格运行时不参加这条链，必须保持 owner 选定的固定 SHA。

以上第 3 步是 2026-09-26 之后**唯一的合并入口**——`trusted auto-merge` /
`refresh-release-candidate` 那条自动链已废除（见上一节 "Retired candidate-refresh
automation"）。第 4 步是手工链上目前**未闭合的一步**，需要另做决定。无需手动
指定版本，也无需登录 Fedora 执行 `git pull`。

只有在确认 Runtime 更新确有 operational need 时才运行该批处理；失败或候选
状态损坏时可用同一入口重试。CLI 形式为：

```sh
gh workflow run release-please.yml --repo iridyne/aletheia --ref master \
  -f operational_need=true
```

The equivalent UI action is **Actions -> Prepare and tag releases -> Run
workflow**, with `master` selected and `operational_need` checked. Do not
hand-edit a target version: the Conventional Commit history and
`release-please-config.json` determine the next SemVer. In the current pre-1.0
policy, ordinary `feat`, `fix`, and `perf` release notes produce the next patch;
an explicit breaking change produces the next minor version.

The workflow rejects the release job for manual dispatches by non-admins. If
a later `master` merge makes the candidate stale, rerun `Prepare and tag
releases` — Release Please updates the same open candidate in place; do not
create a second release PR by hand.

The release is complete only after the generated `autorelease: pending` PR
has been merged manually (`gh pr merge <n> --rebase`), its matching
immutable `vX.Y.Z` tag exists, and the GitHub Release exists. A queued or
green CI run, an open or mergeable PR, or the mere existence of the tag
`repository_dispatch` follow-up run does not count as completion. Recover a
failed tag/GitHub Release follow-up by rerunning that failed
`repository_dispatch` workflow run in the Actions UI or with:

```sh
gh run rerun <run-id> --repo iridyne/aletheia
```

Do not start CI merely to clear the candidate hold. Only an explicit current
owner request authorizes remote CI; do not tag the commit manually.
Fedora Paper deployment and Observation-window admission remain separate
downstream gates; deployment success does not open an Observation window.

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

## Optional manual CI validation

There is no automatic pull-request or release-candidate CI. Only an explicit
current owner request may start the manual workflow, which runs release bundle
and packaged-runtime contract tests but does not distribute artifacts. There is
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
