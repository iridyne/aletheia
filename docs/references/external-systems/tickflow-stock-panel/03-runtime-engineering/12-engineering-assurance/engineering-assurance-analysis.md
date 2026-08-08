# Engineering-Assurance Analysis

## Executive Finding

TickFlow's assurance profile is uneven in a revealing way:

- its feature-local engineering is often excellent;
- its operator-facing workflows are unusually thoughtful for a small local
  application;
- its packaging breadth is impressive;
- its system-wide trust, verification, and recovery contracts lag behind the
  sophistication of its UI and data engine.

The project is strongest when the failure is already known and located: a
pipeline stage, a backtest process, a provider capability, a cache build, or a
desktop startup. It is weakest when the question crosses boundaries: which
request caused this, whether a credential can ever be read back, whether a
release was tested before publication, whether an upgrade preserved data, or
whether a migration can be rolled back after partial failure.

That distinction matters for Aletheia. We should borrow the reference project's
small, concrete feedback loops and performance instrumentation. We should not
trade away Aletheia's stronger trust and recovery infrastructure for a simpler
“desktop app” model.

## Assurance Model

A useful way to interpret the reference project is as six concentric layers:

1. **User feedback** — loading, progress, status, toast, reconnect, and visible
   configuration.
2. **Domain telemetry** — rows, stages, durations, cache hits, RSS, provider
   capabilities, and data coverage.
3. **Runtime containment** — locks, queues, subprocesses, timeouts, rate limits,
   and memory bounds.
4. **Trust boundaries** — authentication, credential handling, outbound
   destinations, plugin authority, and custom code.
5. **Verification gates** — tests, static analysis, CI, scans, and artifact
   smoke checks.
6. **Recovery chain** — version identity, migration ledger, backup, restore,
   signed update, health soak, and rollback.

TickFlow is strong in layers 1 and 2, mixed in layer 3, and substantially less
complete in layers 4 through 6. Aletheia is stronger in layers 4 through 6 but
can learn from TickFlow's clarity and economy in layers 1 and 2.

## High-Value Practices to Extract

### 1. Instrument the expensive domain boundary, not every line

TickFlow's backtest path measures the facts an operator actually needs:

- market-data loading;
- matrix preparation and cache use;
- compute time;
- result serialization;
- child-process exit;
- peak/resident memory;
- payload size.

This is more useful than indiscriminate debug logging. Aletheia already has
generic request timing, event-loop lag, storage performance tests, and scheduler
metrics. The reference project suggests adding similarly compact phase
measurements at a few economically important boundaries: market collection,
proposal evaluation, risk decision, execution submission, reconciliation, and
operator-loop evaluation.

The measurement should travel with the run or result record, not live only in a
log line. That makes it comparable, queryable, and visible in the UI.

### 2. Treat data coverage as a first-class health fact

The reference project does not reduce data health to “file exists.” Its status
surfaces show earliest/latest dates, row coverage, last synchronization, next
schedule, provider availability, and indicator readiness. This creates an
operator mental model close to the real data pipeline.

Aletheia already has provenance, collector health, freshness, venue health, and
doctor diagnostics. The borrowable idea is presentation: one compact coverage
envelope per authoritative dataset, consistently containing:

- source;
- observed/as-of time;
- earliest/latest coverage where meaningful;
- rows or entities;
- freshness state and threshold;
- last successful commit/run id;
- last failure code;
- next expected refresh;
- whether the value is cached, persisted, derived, or unavailable.

### 3. Make performance architecture visible in source

TickFlow's page-level lazy imports, explicit chart chunks, virtualized watchlist,
Polars hot cache, Parquet scans, DuckDB cold views, and matrix-cache limit make
the performance design easy to discover. This helps future maintainers preserve
the intended fast path.

Aletheia can improve by making equivalent contracts equally legible. A fast
path should name:

- authoritative source;
- cache key and invalidation event;
- maximum age;
- memory/row bound;
- fallback source;
- provenance header or response field;
- expected latency budget.

The reference project's `X-Aletheia-Data-Source` analogue already exists in
Aletheia for selected routes; it should become a consistent response envelope
rather than an isolated convention.

### 4. Verify the packaged native dependency, not only the build command

The Windows release workflow does not assume that installing a compatibility
extra means PyInstaller collected it. It inspects the produced artifact for both
Polars runtimes. That is an excellent packaging principle:

> verify the property required at runtime inside the final artifact.

Aletheia should apply this principle to each packaged target:

- native host executable exists and reports the intended version;
- dashboard assets and index are present;
- SQLite and crypto native modules import;
- trusted update public key is embedded;
- runtime entrypoint and manifest hashes agree;
- no source checkout or development-only secrets are bundled;
- package starts in an isolated temporary home and reaches readiness;
- a minimal backup/restore and rollback fixture passes in the packaged runtime.

### 5. Preserve explicit legal/capability gates

The Dockerfile keeps a potentially problematic third-party market-data plugin
out of the default image and requires an explicit build argument. The capability
layer also prevents unavailable provider features from becoming generic 500s.

This aligns with Aletheia's semantic-honesty rule. Optional or legally sensitive
capabilities should be:

- absent by default;
- discoverable as unavailable, not simulated;
- enabled through an explicit owner action;
- attributed to a source and policy;
- observable without exposing its credential;
- unable to silently change the authority of the core execution path.

## Critical Boundary Findings

### A. Webhook secrets violate the application's own secret model

Severity: high for confidentiality; medium for trading-state integrity.

The application correctly masks TickFlow and AI keys, yet it stores other
bearer-equivalent values as ordinary preferences and returns them verbatim.
This creates multiple consequences:

- any settings-reading client receives third-party bot credentials;
- browser state and component props contain raw secrets;
- API captures, support screenshots, or developer tools can expose them;
- the file lacks the permission treatment even the plaintext key store uses;
- future code can log or serialize the broad preferences object without knowing
  that it contains credentials.

The repair pattern is architectural, not field-by-field:

- create one credential service;
- accept raw values only on write;
- return only `{configured, last4}` or an opaque reference;
- never require the UI to echo an existing secret to preserve it;
- atomically replace credential files and apply owner-only permissions;
- separate credential deletion from “empty value means keep/clear” ambiguity;
- add a test that enumerates every settings response and rejects known secret
  fields or recognizable stored values.

Aletheia should retain its current write-only secret API pattern and extend
contract tests so new configuration fields cannot regress into TickFlow's split
classification.

### B. Custom outbound HTTP is more powerful than its configuration UI suggests

Severity: high in a remotely exposed or compromised-session deployment; lower
in a strictly single-owner loopback deployment.

The configuration appears to be a benign mapping editor, but it grants the
backend authority to:

- contact an arbitrary destination;
- attach an environment-derived credential;
- use arbitrary HTTP methods;
- place the token in headers or query parameters;
- parse the full response into memory;
- show a preview to the caller.

This authority needs an outbound request policy. A robust contract would:

1. require `https` except explicit loopback development;
2. resolve and reject loopback, link-local, multicast, metadata, and disallowed
   private ranges unless the owner selects a named private-destination mode;
3. re-resolve and revalidate every redirect target;
4. cap redirects, response bytes, rows, nesting, and parse time;
5. prevent query-string credentials by default;
6. redact URL userinfo/query and credential headers from every error;
7. record destination origin, status, bytes, duration, and policy decision
   without recording secrets or row contents;
8. distinguish saved configuration from a one-time tested destination;
9. bind the destination origin to the credential so changing origins requires
   explicit credential re-entry.

Aletheia has similar arbitrary model-provider endpoint needs, so this is a shared
lesson rather than a one-sided criticism.

### C. AST validation is a lint gate, not code isolation

Severity: high if AI-generated/custom code is treated as untrusted; medium when
only a trusted local owner supplies code.

The reference implementation is better than naive `exec`: it uses a module
allowlist, rejects common escape patterns, validates twice, contains file paths,
and restores failed edits. Its own comment nevertheless states the correct
boundary: an AST allowlist is not a sandbox.

The server imports the module with full process authority. An allowed scientific
library can expose far more operations than the AST rule describes, module
top-level statements run immediately, and a static syntax walk cannot impose
CPU, memory, filesystem, network, or process limits.

The safe execution architecture is:

- store strategy source as data, never import it in the API process;
- compile/validate in a disposable worker;
- run under a dedicated low-privilege OS identity or sandbox;
- give the worker only an immutable input dataset or narrowly scoped IPC;
- remove credentials and inherited environment variables;
- deny network and application-source writes;
- enforce CPU, wall-time, memory, output, and process-count limits;
- return a typed signal/result artifact with source hash and engine version;
- kill and replace the worker after timeout or policy violation.

The AST validator remains valuable as fast feedback, but it should be described
as admission lint before isolation, not the security boundary itself.

### D. Release publication is not gated by regression assurance

Severity: high for release reliability; indirect for security.

TickFlow's workflows can produce sophisticated packages, but build success is
the principal gate. A manual desktop release can publish even though backend
tests, lint, frontend lint/tests, security scans, and installed-artifact smoke
tests were never requested by the workflow.

The release workflow should consume, not duplicate, an immutable verification
result:

- source SHA must have passed required review checks;
- version fields must be generated from or equal to the release version;
- locked dependencies must resolve without fallback mutation;
- final artifacts must pass platform smoke and manifest checks;
- hashes, SBOM, provenance, and signatures must be emitted;
- release upload must use exactly the verified artifact digests.

This is a chain-of-custody problem. Running tests in some unrelated environment
is insufficient unless the release binds itself to their source and artifact
identity.

### E. Cross-platform data placement breaks the stated upgrade promise

Severity: high for macOS user data; medium for Linux/manual upgrades.

Putting data beside the executable is understandable on Windows and is carefully
handled by Inno Setup. Applying the same rule to a macOS `.app` puts mutable data
inside the bundle that the user replaces during upgrade. The product statement
“reinstall does not lose data” therefore does not have one implementation across
platforms.

The cross-platform invariant should be:

- immutable program files live in the application/package location;
- mutable user data lives in the platform application-support/data location;
- the data root is discoverable and exportable in the UI;
- upgrade never moves or deletes it implicitly;
- migration begins from a verified backup and records its version;
- uninstall defaults to preserving it and presents the exact path before
  deletion.

### F. Ad hoc migrations do not form a recoverable history

Severity: high when a destructive cleanup or partition rewrite runs on valuable
history.

TickFlow's migration functions solve real compatibility problems, and some use
atomic per-file writes or retain a `.bak`. The missing element is a ledger that
binds:

- migration id and source checksum;
- schema/data version before and after;
- start/finish time and result;
- affected datasets;
- backup/checkpoint identity;
- validation performed;
- retry/idempotence and rollback policy.

Continuing startup after migration failure can preserve availability, but it can
also run new code against mixed old/new data. A safe failure policy depends on
the dataset: cache migrations can invalidate and rebuild; authoritative user or
trading data should fail closed or run in a compatibility mode.

## Important but Non-Critical Findings

### Desktop single-instance ownership is race-prone

The PID file uses check-then-write rather than an exclusive create or OS lock.
Two simultaneous starts can both observe no live owner and both continue. The
result can be two schedulers, two writers, and two windows. A platform mutex or
atomic lock file with owner verification is the appropriate primitive.

### Cookie transport and CSRF assumptions are implicit

SameSite=Lax and wildcard CORS without credentials mitigate common cross-site
requests, but they do not replace an explicit origin/CSRF policy for a network-
reachable stateful application. `secure=False` also makes reverse-proxy HTTPS
configuration part of security, while the app cannot verify it.

### Session and settings files need one atomic-store primitive

Several JSON stores repeat a load-merge-direct-write pattern. This creates
corruption and lost-update risks and inconsistent permissions. Domain 9 covers
write consistency in depth; the assurance lesson is to supply one reusable
atomic JSON store with:

- temp write, flush/fsync where required, and replace;
- parent-directory durability where required;
- cross-process lock or single writer;
- schema version and validation;
- permission policy;
- corruption quarantine and recovery report.

### Version identity is manual

Three application version fields and a release input currently agree, but no
mechanism forces agreement. Version should be generated once into backend and
frontend build metadata, then verified in the artifact and health response.

### Logs are useful but not bounded as a product contract

The desktop log can grow indefinitely. Logging also lacks a stable error code
taxonomy and request/run correlation. Rotation alone is insufficient; displayed
diagnostics should be redacted, tail-bounded, source-labeled, and tied to a run
or request id.

### Frontend failure containment is incomplete

The request helper offers consistent toasts, but render errors and promise
rejections have no common recovery UI. Route-level error boundaries should show
a stable error id, preserve navigation, offer retry, and never expose secrets or
raw backend traces.

## Test-Strategy Interpretation

### What the inventory proves

Static source proves that the backend authors encode many important domain
expectations, particularly around numerical correctness and data compatibility.
It also proves that build scripts invoke TypeScript during frontend production
builds.

### What the inventory does not prove

It does not prove:

- tests collect or pass;
- live/probe tests are safe or selected;
- the same tests run before merge or release;
- every platform receives equivalent coverage;
- browser behavior matches component intent;
- package installation works;
- migrations restore real data;
- security invariants cannot regress.

### A proportionate assurance pyramid

For this architecture, the useful layers are:

1. **Pure contract tests** — normalization, schema, capability, strategy math,
   error codes, URL policy, credential response shape.
2. **Storage transition tests** — atomic replace, concurrent writers, migration
   ledger, interruption, cache invalidation, backup verification.
3. **Route/service integration** — authentication, permissions, accepted versus
   completed semantics, SSE/REST authority, cancellation, failure recovery.
4. **Frontend behavior** — query keys, stale/fresh labels, reconnect, secret
   editing without readback, error boundaries, accessibility.
5. **Packaged smoke** — install/start/readiness/data path/upgrade/rollback on each
   platform.
6. **Small golden business paths** — one deterministic end-to-end workflow per
   authority boundary, without turning every pull request into a full production
   simulation.

This supports the user's “do not over-run pytest” principle: analysis identifies
the smallest evidence-bearing checks. It does not confuse a large suite with a
good research method.

## Performance Interpretation

TickFlow optimizes the visible interactive path well, but budget enforcement is
mostly component-specific. Aletheia should borrow the mechanisms while using a
system-wide budget vocabulary:

| Resource | Required contract |
|---|---|
| HTTP | timeout, retries, response bytes, parsed rows, destination policy |
| memory cache | owner, key, byte/row limit, age, eviction, rebuild cost |
| worker queue | capacity, admission result, service time, cancellation, recovery |
| process | CPU/wall/memory/output limits, exit reason, cleanup proof |
| log | max bytes, generations, redaction, correlation, retention |
| frontend bundle | route chunk budget, shared-vendor budget, regression gate |
| provider | account-wide quota identity, reservation, burst, retry-after |
| database | writer queue depth, lock wait, transaction duration, batch size |

Budgets should degrade visibly. “Skipped because budget exhausted,” “served
stale from cache,” and “rejected because worker capacity is full” are honest
states; silent fallback and indefinite waiting are not.

## Packaging Interpretation

TickFlow demonstrates two valuable product instincts:

- make installation accessible to non-developers;
- explain platform limitations and data placement in release notes.

The next maturity step is to make those promises machine-verifiable. A release
should be an immutable tuple:

`source SHA + dependency locks + build environment + artifact hashes + manifest
+ signature + schema range + tested upgrade path`.

The operator should never have to infer whether an artifact corresponds to a
tag, whether it can read the current data schema, or whether replacing it will
delete user data.

## Bottom Line

TickFlow is worth studying because it is pragmatic and frequently communicates
complex backend work to the user better than Aletheia. Its best ideas are small
and concrete: route lazy loading, focused virtualization, per-stage progress,
data coverage, cache/resource telemetry, compatibility packaging, and domain-
specific error semantics.

Its weaker areas show where UI polish can create false confidence. A green
progress bar, a downloadable installer, or an AST “safety” check is not itself a
recovery or security proof. Aletheia should extract the feedback and performance
patterns while preserving and extending its stronger default-deny authority,
secret handling, migration ledger, verified backup, signed update, and rollback
chain.
