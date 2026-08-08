# Aletheia Comparison

## Comparison Rule

This report compares source-established mechanisms, not product maturity by
reputation and not live runtime outcomes. “Stronger” means the current source
expresses a more complete invariant. It does not mean that the mechanism was run
successfully during this research.

## Executive Comparison

TickFlow and Aletheia have complementary strengths:

- TickFlow is generally clearer at communicating data work, stage progress,
  cache behavior, and backtest cost to the user.
- Aletheia is materially stronger at authentication defaults, write-only
  credentials, diagnostic separation, merge gates, migration identity, verified
  backups, signed updates, runtime activation, and rollback.
- Both still need a universal request/run correlation model, coherent resource
  budgets, stronger cross-platform packaged smoke, and a precise outbound-
  destination policy for configurable endpoints.

The correct direction is additive: bring TickFlow's operator feedback into
Aletheia without weakening Aletheia's safety and recovery chain.

## Capability Matrix

| Area | TickFlow source | Aletheia source | Direction |
|---|---|---|---|
| cheap liveness | one small `/health` | separate cheap live routes | keep Aletheia separation |
| readiness | desktop polls health; no dependency contract | static/API readiness plus liveness | extend Aletheia readiness carefully |
| business health | distributed across data/provider/job status | health rollup, status, stability, doctor | combine Aletheia taxonomy with TickFlow coverage cards |
| request latency | no generic middleware found | `Server-Timing` and slow-request logs | keep and expand Aletheia |
| request correlation | not found | not found generically | shared gap |
| structured operational events | job/stage records, otherwise text logs | bounded infra events plus structured error JSONL | Aletheia stronger; add durable/run linkage |
| log rotation/redaction | desktop append log, no bound found | bounded rotation, tail reads, redaction | Aletheia stronger |
| backtest performance telemetry | detailed timings, RSS, cache, serialization | no equivalent single trading-result envelope | borrow TickFlow pattern |
| data coverage UI | rich table coverage and sync facts | richer underlying provenance/health, less uniform presentation | borrow TickFlow presentation |
| default bind | backend and Vite use `0.0.0.0` | loopback inner API/front door by default | preserve Aletheia |
| remote auth | password/session cookie; unconfigured LAN trust | default-deny token when selected, loopback default | Aletheia stronger, but tokenless LAN mode remains |
| password handling | salted PBKDF2, long-lived raw sessions | remote owner token rather than password sessions | different threat models |
| secret API | some keys masked; webhook secrets returned raw | write-only/status-only credential APIs | Aletheia materially stronger |
| secret at rest | plaintext JSON with permission attempt | keyring plus encrypted file fallback | Aletheia stronger against accidents, not full local compromise |
| outbound error redaction | inconsistent | shared secret-header scrubbing and log redaction | Aletheia stronger |
| configurable HTTP destination | arbitrary custom source URL | arbitrary model/provider endpoints with some provider-specific validation | shared destination-policy gap |
| custom code admission | double AST validation and path containment | agent/model tools and authority are constrained; no equivalent strategy import surface | TickFlow has useful lint, but not isolation |
| code isolation | backtest process, but server imports strategy | subprocess/CLI providers restrict tools; broader runtime still needs per-boundary review | do not treat either as universal sandbox |
| frontend route splitting | all major pages lazy; chart vendor chunks | route/build architecture exists but less explicit in this trace | borrow TickFlow clarity |
| list virtualization | applied to a high-volume watchlist view | table architecture and tests; use varies by page | apply by measured need |
| cache budgets | matrix cache has byte limit; other frames mixed | multiple bounded lanes/retention policies; not universal | converge on one budget vocabulary |
| provider rate budget | central per-process 80% safety budget | provider/network timeouts and scheduler budgets | borrow explicit reserve, add account-wide ownership |
| backend test inventory | 74 files / 546 test functions | 498 files / 5,355 test functions including specs | inventory only; Aletheia much broader |
| frontend tests | no common test files found | 29 Vitest files | Aletheia stronger |
| PR CI | no PR workflow found | lint, contracts, docs, secret scan, frontend, Rust, Python tiers | Aletheia materially stronger |
| release gate | build and publish | packaged tools are strong; PR CI not itself a full release pipeline | both need artifact-chain proof |
| dependency reproducibility | lockfiles but fallback unlocked installs | locked CI; release paths still require separate audit | preserve locked, fail-closed installs |
| multi-platform packaging | Windows/macOS/Linux desktop plus multi-arch Docker | native host and packaged-runtime tooling, platform work in progress | learn from TickFlow breadth |
| final artifact property check | Windows Polars runtimes verified | immutable manifest and package integrity helpers | combine both |
| artifact signing | none configured | signed update feed; macOS app builder remains unsigned | Aletheia stronger but incomplete |
| version binding | three manual fields plus release input | package/update manifests bind SHA/version | keep manifest binding; add single generated version |
| data placement | beside executable on all frozen platforms | platform application-data root | preserve Aletheia |
| migrations | useful ad hoc startup conversions | checksummed immutable migration ledger | preserve Aletheia |
| backup/restore | no system-wide verified mechanism found | WAL-safe, checksummed backup and verified restore | preserve Aletheia |
| update | link to GitHub Releases | signed feed, preflight, atomic activation, health soak | preserve Aletheia |
| rollback | manual reinstall/operator behavior | backup-based automatic and manual rollback | preserve Aletheia |

## Observability

### What Aletheia should borrow

TickFlow makes background data work feel tangible. The user sees stages, local
percentages, overall percentages, bounded logs, coverage, last completion, and
next schedule. Its backtest result contains performance evidence rather than
requiring a log search.

Aletheia should add a reusable run-presentation contract on top of its durable
run/event models:

```text
run identity
  state: accepted | queued | running | partial | succeeded | failed | cancelled
  phase: stable machine code + localized label
  progress: completed units / total units, when total is meaningful
  timing: queued, started, phase durations, finished
  resources: rows, bytes, calls, cache status, memory when relevant
  provenance: source, as_of, runtime session, producer commit
  error: stable code, safe message, operator action
  recovery: retryable, resume cursor, cancel effectiveness, next scheduled run
```

Percent alone should never be required. Some work has no honest total; those
runs should show phase and elapsed time rather than fabricated progress.

### What Aletheia should keep

Aletheia's endpoint taxonomy prevents common false conclusions:

- process alive is not static/UI ready;
- static/UI ready is not trading health;
- trading health is not runtime continuity;
- transient infra event is not durable audit evidence.

This is more rigorous than TickFlow's generic health response. Do not collapse
these surfaces into one “green” indicator when adopting coverage cards.

### What both should add

Both need a cross-layer correlation id. A practical design would create or
accept a bounded request id, then propagate it into:

- request log and response header;
- background job/run id and causation id;
- provider call metrics;
- state mutation/audit record;
- UI error details;
- diagnostic export.

It must not contain user text or credentials and must not be treated as
authorization.

## Security and Configuration

### Aletheia's stronger invariant

Aletheia already expresses a valuable contract:

- credential values are accepted on write;
- reads return configured/last-four status;
- previews cannot access or persist production secrets;
- remote access is loopback-first and can be default-deny;
- displayed logs are redacted;
- HTTP status exceptions scrub secret headers;
- privileged state changes retain server-derived actor/source semantics.

TickFlow's webhook response behavior is exactly the regression this architecture
should prevent. Aletheia should add a schema-level credential registry so every
new field is classified before it can enter config serialization or an API
response.

### Aletheia's secret-store caveat

Fernet ciphertext and its key live in the same data root. This is still useful:
it prevents casual plaintext discovery, accidental support-bundle inclusion,
and direct string search. It is not protection from an attacker who can read the
whole data directory.

The next level is platform-bound key protection where available, strict file
permissions, atomic locked updates, and a clear degraded state when the secure
store cannot be accessed. Availability fallbacks must not silently turn into
plaintext storage.

### Remote Control caveat

Aletheia allows Remote Control without a token when the owner explicitly sets
`require_token=False`. That is semantically honest in code and narrower than
TickFlow's broad default bind, but it still makes every selected private adapter
a trust boundary. The UI should present the effective listener addresses and a
clear “private network without token” warning, and sensitive routes should be
considered for mandatory authentication even in convenience mode.

### Shared outbound policy

Aletheia's Databricks adapter correctly requires HTTPS for non-loopback hosts and
its HTTP helpers scrub credentials. Other configurable compatible-provider URLs
still need a unified origin/destination policy. The same component could serve:

- model providers;
- webhooks;
- data providers;
- update feeds;
- tunnel endpoints;
- future external research sources.

Policy must be per capability: an update feed may allow only a pinned HTTPS
origin; a local model provider may allow loopback HTTP; a webhook may allow a
small host allowlist; a custom data source may require explicit owner approval
for private destinations.

## Performance and Resource Engineering

### TickFlow practices worth adopting directly

1. Route-level lazy imports for non-shell pages.
2. Explicit vendor chunking for genuinely heavy libraries.
3. Virtualization only for measured high-cardinality views.
4. A domain result that reports phase timing, cache state, memory, and result
   size.
5. A configurable disk-cache byte ceiling with versioned entries.
6. A declared safety fraction below provider-advertised request limits.
7. Source comments that explain the hot path and invalidation event.

### Improvements required before copying

The TickFlow RPM scheduler is process-local and allows a first-batch burst. In
Aletheia, exchange/provider budgets may span scheduler jobs, manual runs,
recovery tasks, and more than one process. A reusable budget allocator should
key reservations by account/provider/capability and either persist or delegate
the single authority.

Likewise, a memory cache should be limited by bytes or a workload-derived bound,
not a comment estimating rows. The status surface should expose usage, limit,
age, hit/miss, and eviction/rebuild reason.

### Aletheia mechanisms to keep

Aletheia already has:

- bounded persistent manual Agent queue;
- bounded scheduler timeout workers;
- event-loop lag monitoring;
- slow-request `Server-Timing`;
- bounded log tails and rotation;
- SQLite writer admission metrics and cross-process writer permit;
- retention batch timing;
- performance-specific test modules.

The improvement is consolidation. These measurements should feed one operator
resource view and share stable metric names, rather than requiring source-level
knowledge of each subsystem.

## Tests and Verification

### Static inventory is not a score

Aletheia has roughly an order of magnitude more Python test functions in the
snapshot and a real frontend test layer. That indicates more encoded behavior,
but raw count can reward duplication and slow suites. The meaningful advantages
are structural:

- explicit test tiers and domains;
- architecture and CI workflow contract tests;
- isolated data roots;
- timeouts and parallel execution;
- frontend lint/test/typecheck;
- secret scanning;
- pinned action identities;
- documentation governance.

### What TickFlow's suite teaches Aletheia

TickFlow's numerical and cache-equivalence tests are focused. Tests that prove a
fast path produces the same trades, equity curve, and core statistics as the
uncached path are especially valuable. Aletheia should use the same equivalence
pattern whenever adding:

- a cache;
- vectorized computation;
- batch writes;
- derived-summary shortcuts;
- stale-while-revalidate behavior;
- alternate provider normalization.

The test should compare authoritative semantics, not just response shape or
execution time.

### What Aletheia should not copy

Do not rely on a manually invoked release workflow as the first full build/test
of a change. Do not publish after a fallback unlocked install. Do not infer
security coverage from general route tests. Do not add broad pytest execution to
this research workflow; use the source analysis to identify the narrow contract
tests that should exist in normal engineering work.

### Missing Aletheia release assurance

The current PR CI is strong, but the source trace does not show a unified release
workflow that:

- builds every supported final package;
- installs each package on its target OS;
- starts with a clean isolated data root;
- upgrades a fixture from the oldest supported schema;
- verifies signed-feed and manifest identity;
- observes readiness and business health;
- simulates a failed activation and proves rollback;
- signs/notarizes the final platform artifact;
- publishes only the tested digest.

That should be treated as a separate release tier, not added to every local loop.

## Packaging and Upgrade

### What TickFlow does better

TickFlow currently offers a more direct multi-platform consumer packaging story:

- a Windows installer with shortcuts and uninstall flow;
- a conventional macOS DMG drag-install experience;
- a Linux tarball;
- a multi-architecture container image;
- explicit old-CPU Polars compatibility;
- understandable release notes.

Aletheia can learn from that productization and documentation. Its current
packaged runtime is safer internally but more operationally complex.

### What Aletheia must preserve

Aletheia's update chain is far more appropriate for a trading system:

1. verify a signed feed;
2. verify download size/hash and immutable manifest;
3. validate archive containment and symlinks;
4. create a consistent pre-update backup;
5. restore into an isolated preflight data copy;
6. apply migrations and validate config;
7. run SQLite quick check and audit-chain verification;
8. hand an atomic activation request to a stable native host;
9. switch an immutable release pointer;
10. require readiness/business health for a soak window;
11. restore the verified backup and old release on failure.

TickFlow's “open GitHub Releases and replace the program” should not replace any
part of this chain.

### What Aletheia still needs

- one generated version identity across Python, frontend, native host, manifest,
  and update feed;
- platform signing/notarization and Windows signing;
- final artifact SBOM/provenance;
- target-platform install/upgrade/rollback smoke;
- a clear minimum/maximum supported data-schema range;
- operator-visible data and backup locations;
- evidence that release credentials sign the exact smoke-tested digest.

## Prioritized Adoption Map

### Adopt into Aletheia soon

- uniform dataset coverage cards and envelopes;
- phase/resource metrics stored with economically important runs;
- explicit route/vendor chunk budgets and measured virtualization;
- final-artifact native dependency assertions;
- accessible multi-platform release notes with exact limitations;
- fast-path versus authoritative-path semantic equivalence tests.

### Preserve and strengthen in Aletheia

- loopback-first listeners and default-deny remote API mode;
- write-only credential APIs and preview isolation;
- redacted diagnostics;
- CI contract, secret scan, and locked dependency gates;
- checksummed migration ledger;
- WAL-safe verified backup and restore;
- signed immutable update feed;
- preflight, health soak, atomic activation, and rollback.

### Shared work, not direct copying

- request/run correlation ids;
- outbound origin/SSRF/response-size policy;
- universal resource-budget registry;
- strategy or user-code process sandbox;
- release artifact install/upgrade tests;
- platform signing and provenance;
- clear secret-store threat model and file hardening.

### Explicitly reject

- returning webhook or bot secrets to the frontend;
- storing bearer-equivalent values in general preferences;
- treating AST validation as a sandbox;
- unlocking dependency resolution after lock failure;
- publishing packages without source and artifact gate identity;
- storing macOS user data inside the replaceable `.app` bundle;
- destructive ad hoc migrations without checkpoint and ledger;
- using one shallow health endpoint as proof of system readiness.

## Final Position

TickFlow should influence how Aletheia explains work and performance, not how
Aletheia grants authority or recovers state. The reference project's strongest
contribution is a product-engineering sensibility: users can see stages, data
coverage, cache behavior, and platform constraints. Aletheia's strongest
contribution is a trust-engineering chain: source identity, permissions,
durable evidence, verified migration, backup, signed activation, health proof,
and rollback.

The combined target is a system that is both easy to understand and difficult
to corrupt: TickFlow's clarity on top of Aletheia's authority and recovery
invariants.
