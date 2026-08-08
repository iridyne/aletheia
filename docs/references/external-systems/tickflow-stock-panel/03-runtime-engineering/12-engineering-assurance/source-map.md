# Source Map

## Research Questions

The source trace answers these questions:

1. Which signals distinguish process liveness, application readiness, business
   health, data freshness, and background-work health?
2. Can an operator connect a user-visible failure to one request, job, provider,
   or durable state transition without reading the whole source tree?
3. Which values are configuration, which are credentials, and can any credential
   be returned, logged, stored with weak permissions, or sent to an unsafe host?
4. What resource budgets are explicit, and which caches, queues, responses,
   logs, or subprocesses can grow without a declared limit?
5. What does the test inventory cover, and which checks actually gate merge,
   packaging, and release?
6. Are dependencies, artifacts, versions, data migrations, backups, updates,
   and rollback bound to verifiable identities?
7. Which TickFlow practices are useful to Aletheia, and which are attractive
   only at the UI level but lack a safety proof?

## TickFlow: Observability and Error Semantics

### Application logging

Primary sources:

- `backend/app/main.py`
- `backend/app/desktop.py`
- `backend/app/tickflow/repository.py`
- `backend/app/backtest/worker.py`
- `backend/app/backtest/strategy.py`

`backend/app/main.py` configures the standard Python logging stack with
`logging.basicConfig`. The normal format is human-readable text. Repository
cache refreshes and several pipeline stages log start, completion, elapsed time,
row counts, and exceptions. Backtest execution goes further: its result payload
can include load/compute/serialization timings, matrix-cache facts, process exit
code, RSS, and serialized-result size. These domain-local measurements are among
the reference project's strongest assurance practices.

The desktop launcher appends stdout/stderr to `data/desktop.log` and disables
Uvicorn access logging. It protects windowed PyInstaller startup from missing
`sys.stdout` and `sys.stderr`, polls the health endpoint before opening the
window, and presents a native Windows error dialog for early fatal failures.

No generic structured request log, request identifier, trace identifier,
correlation middleware, metrics endpoint, OpenTelemetry integration, or
machine-readable diagnostic bundle was found. `desktop.log` is opened in append
mode and no size or generation bound was found. The application can therefore
explain many known pipeline and backtest phases, but it has no uniform causal
envelope for an arbitrary HTTP failure.

### Health and status surfaces

Primary sources:

- `backend/app/api/routes.py`
- `backend/app/api/data.py`
- `backend/app/desktop.py`
- `backend/app/services/quote_service.py`
- `backend/app/services/pipeline_jobs.py`

Both `/health` and `/api/health` return a small payload containing status,
version, and deployment mode. They do not probe the data directory, repository,
DuckDB views, scheduler, provider capability, quote loop, or worker capacity.
The desktop readiness loop polls `/health`; because Uvicorn normally accepts
requests after lifespan startup, this is still useful startup evidence, but the
route itself is a liveness-style endpoint rather than a dependency-readiness
contract.

The richer operational facts live elsewhere:

- `/api/data/status` reports per-table coverage and last synchronization facts;
- pipeline job APIs expose stage, progress, logs, result, error, and timestamps;
- quote and capability status expose provider availability and update times;
- backtest results expose resource and phase measurements;
- realtime SSE lets the UI invalidate selected queries after state changes.

This separation is useful, but there is no single documented status taxonomy
that tells an operator whether the HTTP loop is live, the UI is ready, trading
data is fresh, a provider is degraded, or a writer slot is stuck.

### Backend errors

Primary sources:

- `backend/app/main.py`
- `backend/app/tickflow/capabilities.py`
- `backend/app/api/*.py`
- `frontend/src/lib/api.ts`

`CapabilityDenied` is normalized to HTTP 403 with a human suggestion rather
than leaking through as a generic 500. This is a good example of domain errors
receiving stable transport semantics. Many routes also use deliberate 400, 401,
403, 404, 409, and 429 responses.

The error contract is not fully centralized. Routes return a mixture of plain
`detail`, custom dictionaries, nested accepted/started results, and exception
strings. The frontend `request()` helper extracts common FastAPI error shapes,
globally redirects on 401, and emits a toast for other failures. No React error
boundary, global `window.onerror`, or `unhandledrejection` handler was found, so
render-time or non-request failures can fall outside the visible error model.

## TickFlow: Configuration and Credential Boundaries

### Settings model

Primary source: `backend/app/config.py`.

Pydantic Settings provides environment injection, `.env` loading, absolute
data-path normalization, frozen-resource awareness, and validation for positive
matrix-cache size and prewarm years. The application version is duplicated in:

- `backend/app/__init__.py`;
- `backend/pyproject.toml`;
- `frontend/package.json`.

All three contained `0.1.88` at the baseline, but no generation or consistency
gate was found. Release input is a fourth version identity.

The default backend bind host is `0.0.0.0`; the Vite development server also
binds `0.0.0.0`. Broad binding is an intentional self-hosting feature, so
authentication and credential response behavior are part of the primary safety
boundary, not optional hardening.

### Access password and sessions

Primary sources:

- `backend/app/services/auth.py`
- `backend/app/api/auth.py`
- `backend/app/main.py`

Positive mechanisms include:

- PBKDF2-HMAC-SHA256 with a random salt and 200,000 iterations;
- constant-time password comparison;
- random 32-byte URL-safe session tokens;
- password-change invalidation of every existing session;
- an HttpOnly, SameSite=Lax cookie;
- a five-failure, five-minute per-IP login throttle;
- blocking public requests before initial password setup;
- `0600` permission attempts on `auth.json`.

Important limitations:

- raw bearer-equivalent session tokens are persisted in `auth.json`;
- the session lifetime is 30 days;
- writes go directly to the final JSON path without an atomic replace or file
  lock;
- rate-limit state is process memory and resets on restart;
- `secure=False` is used for the cookie;
- there is no explicit CSRF token or Origin/Referer validation;
- any direct peer on a private network is treated as a trusted proxy for
  `X-Forwarded-For`, so a same-LAN client can supply a forged forwarded address;
- before a password is configured, all local/private-network API requests are
  admitted by design.

The CORS comment says authentication uses a header/API key, but the actual
access-control mechanism is a cookie. `allow_origins=["*"]` and
`allow_credentials=False` prevent normal cross-origin credentialed browser
requests, but the stale comment is evidence of trust-boundary documentation
drift and should not be relied upon as a CSRF argument.

### API and webhook credentials

Primary sources:

- `backend/app/secrets_store.py`
- `backend/app/services/preferences.py`
- `backend/app/api/settings.py`
- `frontend/src/pages/settings/Monitoring.tsx`

TickFlow and AI keys are stored in `data/user_data/secrets.json`, are masked in
settings responses, and receive a best-effort `0600` permission. The file is
plaintext, directly rewritten, and not locked. This is weaker at-rest protection
than an OS credential store, but the response boundary at least recognizes
those values as credentials.

Webhook credentials do not follow that boundary:

- the Feishu webhook URL and signature secret;
- the WeCom webhook URL, whose query `key` is a bearer credential;
- the WeCom Bot ID and Bot Secret

are stored in `preferences.json`, whose module explicitly says it does not apply
`0600` because preferences are considered non-sensitive. The general settings
GET response returns all of these raw values. The Feishu and WeCom Bot update
responses also return the saved secret. The frontend binds those values back
into editable fields.

This is not merely an encryption preference. It is a broken credential
classification: the API lacks a uniform write-only secret contract. Any client
with settings-read authority receives reusable third-party credentials.

### Custom HTTP sources and outbound trust

Primary sources:

- `backend/app/data_providers/custom/config.py`
- `backend/app/data_providers/custom/loader.py`
- `backend/app/data_providers/custom/provider.py`
- `backend/app/api/settings.py`

Useful controls include:

- source-name regex validation and resolved-path containment;
- `yaml.safe_load` and `yaml.safe_dump`;
- authentication tokens referenced by environment-variable name rather than
  embedded in YAML;
- a bounded timeout validated against a maximum;
- declarative field mapping and a small transform vocabulary rather than `eval`.

The HTTP destination itself is only checked for presence. No centralized scheme
allowlist, HTTPS requirement, DNS/IP resolution policy, loopback/private/link-
local block, redirect policy, content-type rule, or response-byte budget was
found. The provider calls `resp.json()` and extracts rows after the entire body
has been materialized. Authenticated custom-source configuration and test flows
can therefore cause the backend to contact an arbitrary URL and can consume an
unbounded JSON response. Query-parameter authentication also places a token in
the request URL, where upstream and proxy logs commonly record it.

This is an authenticated-owner feature, not an unauthenticated SSRF claim. The
relevant threat cases are a compromised session, a malicious imported
configuration, operator error, and credential exfiltration through a chosen
destination.

### Plugin dependency installation

Primary sources:

- `backend/app/data_providers/custom/loader.py`
- `backend/app/api/settings.py`
- `backend/app/plugins/*/plugin.yaml`

The plugin API can run `npm install` or install a plugin `requirements.txt`
into the backend's current Python environment using `uv pip install` or
`python -m pip`. It has timeouts and captures a bounded error excerpt, but no
lockfile, hash requirement, signature, isolated environment, package allowlist,
or post-install integrity record was found. An install script from an npm or
Python dependency therefore executes with backend-process filesystem authority.

At the baseline, plugin manifests are repository/package content rather than
arbitrary uploaded manifests. That narrows who can select dependencies, but it
does not make mutable registry resolution a safe in-place production upgrade
mechanism.

### AI-generated and custom strategy code

Primary sources:

- `backend/app/strategy/ai_generator.py`
- `backend/app/api/strategy.py`
- `backend/app/strategy/engine.py`
- `backend/app/backtest/worker.py`

The reference project takes this boundary seriously:

- strategy source is checked before saving;
- source is checked again before module execution;
- imports use an allowlist;
- common dangerous builtins, dunder traversal, and dangerous string subscripts
  are rejected;
- strategy paths are contained within the intended source directory;
- reload failure restores the previous strategy file;
- spawned backtest workers isolate heavy backtest execution and support real
  cancellation.

The source comments correctly state that the AST policy is not a sandbox.
`importlib.exec_module()` executes top-level strategy code with the server
process's Python builtins and authority. Allowed libraries such as NumPy and
Polars expose broad APIs that an AST module-name allowlist does not constrain.
The main `StrategyEngine` imports strategy modules during load/reload, so the
spawned backtest process does not isolate the initial module execution.

This is primarily an AI-generated-code and authenticated-owner boundary, not an
unauthenticated code-execution surface. The safe long-term model is a restricted
worker process with a minimal data protocol, resource limits, no credentials,
and no writable application paths.

## TickFlow: Performance and Resource Budgets

### Strong explicit budgets

Primary sources:

- `frontend/src/router.tsx`
- `frontend/vite.config.ts`
- `frontend/src/pages/Watchlist.tsx`
- `backend/app/config.py`
- `backend/app/backtest/matrix.py`
- `backend/app/tickflow/rate_limits.py`
- `backend/app/services/pipeline_jobs.py`

The reference project has several practical optimizations worth retaining:

- every large route is React-lazy-loaded;
- ECharts and Lightweight Charts receive dedicated Rollup chunks;
- the watchlist card view uses TanStack Virtual;
- React Query centralizes cache/revalidation instead of duplicating fetch state;
- hot latest-day data is served from Polars memory while historical scans use
  Parquet predicate pushdown and DuckDB views;
- matrix disk cache has a configurable positive size limit, versioned manifests,
  prewarm controls, and cache-hit instrumentation;
- provider batch/rpm limits are resolved centrally and reduced to an 80 percent
  process-local safety budget;
- job history, stage logs, SSE client queues, and several application caches are
  explicitly bounded;
- backtests use a spawned process and report RSS and serialization size.

### Budget gaps

The limits are not yet a coherent resource policy:

- the repository can retain an enriched-history Polars frame documented as
  roughly one million rows without a byte ceiling or eviction policy;
- generic custom-source JSON has no response-size or row-count limit;
- `desktop.log` has no rotation bound;
- most HTTP routes have no response-byte contract;
- no global request concurrency or admission budget was found;
- the process-local RPM slot table cannot coordinate multiple application
  processes or products and deliberately permits concurrent first-batch bursts;
- no generic request latency, event-loop lag, executor saturation, or memory
  pressure surface was found;
- frontend chunking is configured, but no bundle-size budget or regression gate
  is defined.

The right lesson is not to remove the targeted optimizations. It is to attach
measurable budgets and degradation semantics to the remaining unbounded paths.

## TickFlow: Test and CI Inventory

### Static inventory

Primary sources:

- `backend/tests/`
- `backend/pyproject.toml`
- `frontend/package.json`
- `.github/workflows/release.yml`
- `.github/workflows/docker.yml`

At the recorded reference commit, static inventory found:

- 74 Python files named `test_*.py`;
- 546 functions whose name starts with `test_`;
- 17 files under `backend/tests/backtest/`;
- zero frontend files matching common `*.test.*` or `*.spec.*` patterns.

These are source counts only. Collection status, skips, parameter expansion,
coverage, flakiness, duration, platform results, and pass/fail status are
`Not available` because no test suite was run.

The backend tests are especially rich around backtest correctness, matrix/cache
equivalence, optimizer behavior, market-data normalization, strategies,
realtime invalidation, rate budgeting, SQL safety, and data compatibility.
There are also live/probe-named tests, but filenames alone do not prove they run
in default CI or possess credentials.

Sparse or absent areas in the inventory include:

- password/session/cookie and proxy-trust behavior;
- secret response redaction and webhook credential storage;
- custom-source SSRF and response budgets;
- plugin install isolation;
- desktop single-instance and shutdown behavior;
- installer, migration, backup/restore, and update recovery;
- frontend component, hook, error, accessibility, and browser behavior.

### Tooling declarations

`backend/pyproject.toml` declares pytest, pytest-asyncio, Ruff, and mypy in the
development extra. Ruff has a substantive rule set. `frontend/package.json`
declares a `build` script that runs TypeScript before Vite and a `lint` script,
but it does not declare ESLint, an ESLint configuration, Vitest, Playwright, or
another frontend test runner at this baseline.

### Workflow gates

The Docker workflow builds and pushes multi-architecture images on `main`, tags,
or manual dispatch. The desktop workflow is manual and builds selected Windows,
macOS ARM64, and Linux artifacts. Both build the frontend. Neither workflow
invokes:

- backend tests;
- Ruff or mypy;
- frontend lint;
- frontend tests;
- a backend import/startup smoke check;
- an installer install/launch/uninstall smoke check;
- a secret scan, dependency audit, SAST scan, or container scan;
- a coverage or bundle-size threshold.

There is no pull-request CI workflow in the reference tree. Release construction
therefore provides buildability evidence but does not act as a regression or
security gate.

## TickFlow: Packaging, Release, and Upgrade

### Docker

Primary sources:

- `Dockerfile`
- `.dockerignore`
- `.github/workflows/docker.yml`

The multi-stage image keeps frontend tooling out of the runtime image, excludes
local data and `.env` files from the build context, supports amd64 and arm64, and
defaults the legally sensitive stock-sdk plugin to excluded. The Codex CLI
version is pinned and verified during the build.

Reproducibility is weakened by fallback behavior:

- `pnpm install --frozen-lockfile || pnpm install` permits lock drift;
- `uv sync --frozen ... || uv sync ...` permits lock drift;
- `uv` itself is installed without a pinned version;
- base image tags and GitHub Actions use moving tags rather than immutable
  digests/commit SHAs;
- the runtime runs as the image default root user;
- no container `HEALTHCHECK` is declared.

The image workflow publishes after build without a test or runtime-smoke gate.

### Desktop packages

Primary sources:

- `packaging/tickflow.spec`
- `packaging/tickflow.iss`
- `.github/workflows/release.yml`
- `backend/app/desktop.py`

Notable strengths:

- PyInstaller `onedir` avoids one-file extraction overhead and native-library
  surprises;
- dynamic dependencies and metadata are explicitly collected;
- heavy development/backtest packages are excluded from the desktop payload;
- Windows installs without administrator privileges;
- Windows data is excluded from the installer file list and deletion requires
  a second explicit uninstall choice;
- the release workflow checks that both standard and legacy-CPU Polars runtimes
  actually exist in the Windows artifact;
- the artifact matrix can build Windows, macOS ARM64, and Linux independently.

No Authenticode signing, Apple code-signing identity, Apple notarization,
artifact signature, SBOM, provenance attestation, or published checksum is
configured. The macOS spec explicitly uses `codesign_identity=None`; release
instructions tell users to bypass Gatekeeper with right-click Open.

The workflow's input version becomes the GitHub tag, while package metadata is
read separately from `frontend/package.json`. No equality check binds the
requested release version to the three source version fields, so a release tag
and the installed application's reported version can diverge.

### User-data placement

For every frozen platform, `config.py` puts writable data beside
`sys.executable` in a `data/` child. This is carefully supported by the Windows
Inno installer. It is not platform-neutral:

- in a macOS `.app`, the executable is inside the application bundle, so data
  becomes bundle content under `Contents/MacOS/data`;
- replacing the application bundle during a drag-install upgrade can replace
  that data with the old bundle;
- application-bundle write permissions and signing expectations differ from a
  user application-support directory;
- Linux tar replacement semantics are manual and not transactionally defined.

The release notes claim installation-directory data survives reinstall, but the
source establishes that guarantee only for the Windows Inno path. A platform
user-data directory plus explicit export/backup is a safer cross-platform
contract.

### Migration and recovery

Primary sources:

- `backend/app/tickflow/repository.py`
- `backend/app/services/kline_sync.py`
- `backend/app/services/ext_data.py`
- `backend/app/strategy/monitor_rules.py`

The project includes useful targeted compatibility code:

- old desktop data-directory relocation;
- minute-K symbol-partition to date-partition conversion;
- cleanup of unusable null-datetime minute data;
- extension-config fan-out with a `.bak` of the old JSON;
- monitor-rule and cache-version compatibility;
- Parquet union-by-name and schema-compatibility tests.

These migrations are ad hoc startup functions rather than a versioned,
checksummed migration ledger. Several continue startup after logging a warning;
the minute partition migration deletes old directories after writing new
partitions but has no dataset-level manifest, checkpoint, or verified rollback.
The legacy data-directory move can merge same-named directories in a way that
creates an extra nested directory, and it treats an existing file conflict by
deleting the legacy file in favor of the new path.

No application-wide backup service, manifest verification, SQLite/Parquet
integrity preflight, restore drill, transactional updater, automatic rollback,
or in-app update mechanism was found. “Check update” opens GitHub Releases and
leaves download, replacement, data protection, and rollback to the operator.

## Aletheia: Comparison Sources

### Health and observability

Primary sources:

- `aletheia/server/routes/health.py`
- `aletheia/server/responsiveness.py`
- `aletheia/server/app.py`
- `aletheia/logging_config.py`
- `aletheia/infra/events.py`
- `aletheia/gateway/logs.py`
- `aletheia/diagnostics/doctor.py`

Aletheia separates cheap liveness (`/_infra/livez`, `/api/ready`), static/API
readiness (`/_infra/readyz`), domain health (`/api/health`), broad status,
stability, runtime continuity, and doctor diagnostics. It measures event-loop
responsiveness, adds `Server-Timing`, logs slow requests without request bodies,
keeps a bounded infra-event deque, mirrors errors to structured JSONL, rotates
gateway logs, tails only bounded bytes, and redacts common credentials before
diagnostic display.

The current implementation still lacks a generic request/correlation id, a
standard metrics export, and durable infra events. Its central stderr log is
human-readable rather than fully structured. Those remain comparison gaps.

### Secrets and remote access

Primary sources:

- `aletheia/config/secrets.py`
- `aletheia/server/app.py`
- `aletheia/gateway/network.py`
- `aletheia/net.py`
- `aletheia/server/routes/bitget.py`
- `aletheia/server/routes/model.py`

Aletheia defaults its inner API and front door to loopback, selects specific
remote IPv4 listeners, supports default-deny token protection for private API
and infra routes, uses constant-time token comparison, treats credential APIs
as write-only/status-only, blocks secret access in isolated previews, scrubs
secret headers before HTTP exceptions, and redacts displayed log tails.

Secrets are copied into an encrypted file fallback and optionally the OS
keyring. The Fernet key is stored next to the ciphertext, so this protects
against accidental plaintext disclosure more than a local attacker who can read
the entire data directory. File permission hardening, concurrency/atomicity for
all writes, and requiring a token whenever Remote Control is enabled remain
areas to improve. Aletheia explicitly allows tokenless private-network remote
control when the owner selects that mode.

### Tests and CI

Primary sources:

- `pyproject.toml`
- `frontend/package.json`
- `frontend/vitest.config.ts`
- `.github/workflows/ci.yml`
- `tests/`
- `frontend/src/**/*.test.ts{,x}`

At the Aletheia comparison baseline, static inventory found 498 Python files
matching the configured `test_*.py` or `spec_*.py` patterns, 5,355 test
functions, and 29 frontend test files. These are not pass results.

The pull-request CI definition pins third-party actions by commit, synchronizes
locked dependencies, runs Ruff, architecture/CI contract checks, documentation
governance, a pinned-and-checksummed Gitleaks scan, frontend lint/test/typecheck,
Operator Shell test/build, Rust tests, and isolated Python spec/unit/integration/
E2E tiers with timeouts and parallelism.

This is materially stronger merge assurance than TickFlow's reference tree, but
it is not proof of current green CI because no live check status was inspected.
The CI file does not itself build and install every production platform package,
and this research intentionally did not run the suites.

### Migration, backup, and packaged update

Primary sources:

- `aletheia/storage/migrations.py`
- `aletheia/backup/service.py`
- `aletheia/backup/verify.py`
- `aletheia/backup/restore.py`
- `aletheia/package_integrity.py`
- `aletheia/update/packaged.py`
- `aletheia/update/packaged_commands.py`
- `native/aletheia-host/src/update.rs`
- `scripts/release/sign_update_feed.py`

Aletheia has an immutable migration ledger with checksums and timings, a
cross-process writer permit, WAL-safe SQLite backup, checksummed strict backup
manifests, verified restore, atomic archive publication, immutable release
manifests, safe archive/symlink handling, Ed25519-signed update feeds, candidate
migration preflight against a restored data copy, SQLite `quick_check`, audit-
chain validation, atomic release-pointer activation, health soak, and automatic
backup-based rollback.

This is the strongest area where Aletheia should not copy the reference
project's simpler approach. Aletheia's remaining distribution caveat is that its
macOS app builder describes the output as unsigned and leaves production code
signing/notarization to a separate credentialed step. The repository's PR CI
also does not by itself prove released artifacts were built, signed, installed,
or recovery-tested on every supported OS.
