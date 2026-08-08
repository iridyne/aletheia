# Lessons and Extraction Register

## Decision Vocabulary

- **Adopt** — the mechanism fits Aletheia's invariants with little conceptual
  change.
- **Adapt** — the idea is valuable but needs Aletheia-specific authority,
  durability, or resource semantics.
- **Preserve** — Aletheia already has the stronger mechanism and should not
  regress while borrowing adjacent UX.
- **Reject** — the reference behavior conflicts with Aletheia's trust or state
  invariants.
- **Investigate** — static evidence identifies value or risk, but a design or
  runtime measurement is required before implementation.

## Extraction Register

| Ref | Decision | Extracted lesson | Aletheia boundary |
|---|---|---|---|
| EA-01 | Adopt | Store phase timing and resource facts with heavy-run results | backtest, collectors, operator loop |
| EA-02 | Adapt | Use one coverage envelope for every authoritative dataset | data/status APIs and dashboard |
| EA-03 | Adopt | Lazy-load major pages and isolate genuinely heavy vendor chunks | frontend build/router |
| EA-04 | Adapt | Virtualize measured high-cardinality views | tables/cards, not every list |
| EA-05 | Adapt | Add byte/age/identity facts to cache contracts | market, analytics, model caches |
| EA-06 | Adapt | Reserve below provider-advertised quota | account-wide, not process-only |
| EA-07 | Adopt | Verify native compatibility inside the final artifact | packaged release gates |
| EA-08 | Preserve | Keep separate liveness, readiness, health, status, stability, and doctor semantics | health routes |
| EA-09 | Adapt | Add request/run/causation correlation ids | HTTP, jobs, provider calls, audit |
| EA-10 | Preserve | Keep credential APIs write-only/status-only | settings and connector APIs |
| EA-11 | Reject | Never return Feishu/WeCom/bot secrets in general settings | all credential-bearing config |
| EA-12 | Adapt | Build one schema-level credential classification registry | config, exports, logs, API schemas |
| EA-13 | Adapt | Centralize outbound origin and response-budget policy | model, webhook, data, update clients |
| EA-14 | Reject | Do not treat AST source validation as a sandbox | any user/AI-authored code |
| EA-15 | Investigate | Run custom strategy logic only in a credential-free bounded worker | future strategy extensibility |
| EA-16 | Reject | Do not install mutable plugin dependencies into a live core environment | plugins and extensions |
| EA-17 | Preserve | Keep locked, fail-closed dependency resolution in verification paths | CI and release |
| EA-18 | Preserve | Keep pinned action identities and checksummed security tools | CI supply chain |
| EA-19 | Adapt | Bind source verification to exact release artifact digests | release orchestration |
| EA-20 | Preserve | Keep checksummed migrations and cross-process writer admission | SQLite/data schema |
| EA-21 | Preserve | Keep WAL-safe backup, strict manifest verification, and verified restore | backup/recovery |
| EA-22 | Preserve | Keep signed feed, preflight data copy, health soak, and rollback | packaged update |
| EA-23 | Adopt | Make platform/data-location constraints explicit in release notes | distribution UX |
| EA-24 | Reject | Never place macOS mutable user data inside a replaceable app bundle | packaged data root |
| EA-25 | Adapt | Add install/upgrade/rollback smoke per final platform artifact | release tier |
| EA-26 | Adapt | Generate one version identity into every component and manifest | Python, frontend, native host |
| EA-27 | Adapt | Publish checksum, SBOM, provenance, and platform signature | release artifacts |
| EA-28 | Adopt | Compare optimized paths against authoritative semantics | cache/vectorization tests |
| EA-29 | Preserve | Keep small local feedback loops; do not substitute broad pytest runs for analysis | development workflow |
| EA-30 | Adapt | Add route-level error boundaries with safe error ids and retry | frontend runtime |

## Lesson Cluster 1: Visible Work Is a Product Feature

### Extracted principle

The reference project treats long work as a user conversation. A pipeline is
not just “loading”; it has a name, stage, bounded log, elapsed time, result, and
next expected action. Backtest performance is not hidden in profiler output; it
travels with the result.

### Aletheia adaptation

Aletheia's durable run records and audit events provide a stronger base, but the
dashboard should translate them into one consistent operator grammar:

- accepted is not started;
- started is not committed;
- partial is not failed-with-no-effect;
- cancelled label is not execution stopped;
- stale is not unavailable;
- scheduled next fire is not guaranteed completion;
- cached is not current unless `as_of` and freshness say so.

The UI should never synthesize a reassuring state that the backend does not
provide. If progress total, cancellation effect, or next run is unknown, render
`Not available` rather than an invented percentage or ETA.

## Lesson Cluster 2: Performance Evidence Belongs at Semantic Boundaries

### Extracted principle

TickFlow's backtest instrumentation succeeds because it measures meaningful
phases. Its cache-equivalence test succeeds because it compares trades and
equity, not only execution time.

### Aletheia adaptation

For an economically meaningful Aletheia run, a compact performance record could
contain:

| Field | Meaning |
|---|---|
| `queue_ms` | admission delay before the run owned capacity |
| `input_load_ms` | authoritative inputs acquired and normalized |
| `model_ms` | model inference only, with provider/model identity |
| `risk_ms` | deterministic risk evaluation |
| `execution_ms` | gateway submission/ack boundary |
| `persistence_ms` | durable transaction/commit boundary |
| `total_ms` | accepted execution start through terminal state |
| `cache` | key, hit/miss, age, producer identity |
| `io` | calls, rows, bytes where known |
| `resource` | peak memory/process facts where meaningful |

These fields must not include prompts, secrets, order credentials, or raw
provider bodies. Missing measurements remain `Not available` rather than zero.

## Lesson Cluster 3: Coverage Is More Honest Than “Data Available”

### Extracted principle

TickFlow exposes dates, rows, readiness, and last sync. That makes gaps visible
before a strategy consumes them.

### Aletheia adaptation

Every data surface should answer:

1. What is the source and source mode?
2. What event or run committed this value?
3. What time does the value describe?
4. What time was it observed and persisted?
5. What is the coverage or entity count?
6. What freshness policy was applied?
7. Is it current, stale, partial, fallback, unavailable, or not configured?
8. What operator action is appropriate?

This aligns with Aletheia's provenance model and prevents a cached value from
being visually mistaken for live exchange evidence.

## Lesson Cluster 4: Credentials Are a Type, Not a Filename

### Extracted principle

TickFlow's split between `secrets.json` and `preferences.json` demonstrates why
credential safety cannot depend on which module happened to receive a setting.
A webhook URL with a query key is a credential even if its field name says URL.
A bot secret is a credential even if it appears in monitoring preferences.

### Aletheia adaptation

A credential registry should classify:

- exact config path;
- storage reference;
- response representation;
- deletion behavior;
- allowed destinations/capabilities;
- display masking;
- export/support-bundle exclusion;
- log-redaction patterns;
- whether previews or agents may access it;
- rotation state and last-four metadata.

Settings schema generation and tests can then reject a raw credential field in a
read model. This is more reliable than remembering to call `mask()` in every new
route.

## Lesson Cluster 5: Network Configuration Grants Authority

### Extracted principle

A URL editor can be an outbound network proxy with access to local resources and
credentials. The UI's friendliness does not reduce the backend authority.

### Aletheia adaptation

Use a shared destination-policy object with named modes:

- `fixed_vendor_https` — exact/pinned vendor origins;
- `compatible_provider_https` — arbitrary public HTTPS, origin change clears
  credential binding;
- `loopback_development` — HTTP allowed only on loopback;
- `private_owner_approved` — explicit selected private host/IP with visible
  warning;
- `update_feed` — pinned public key and strict redirect/origin policy.

Resolution must be checked after DNS and on redirects. Response size, parse
depth, duration, and row count are part of the same policy. Error objects and
logs must contain a redacted origin, never the full credential-bearing URL.

## Lesson Cluster 6: Validation and Isolation Are Different Controls

### Extracted principle

TickFlow's AST checker is useful. It catches obvious mistakes early, constrains
the intended programming model, and protects against direct file edits that
bypass the API. Its weakness appears only when it is asked to be a sandbox.

### Aletheia adaptation

Keep three layers distinct:

1. **Schema/lint** — source fits the supported strategy interface.
2. **Admission policy** — owner/agent has authority to register this source and
   its hash is recorded.
3. **Runtime isolation** — the process cannot exceed the allowed filesystem,
   network, credential, CPU, memory, time, or output boundary.

No amount of AST pattern growth replaces layer 3. The worker result should be a
data artifact, never a live Python object shared back into the gateway process.

## Lesson Cluster 7: Build Success Is Not Release Assurance

### Extracted principle

TickFlow's release workflow is good at constructing artifacts, but construction
and verification are separate concerns. A package can build while containing the
wrong version, missing a runtime module, failing at startup, losing user data on
upgrade, or carrying a vulnerable dependency.

### Aletheia adaptation

A release evidence record should bind:

- source commit;
- dirty-tree state;
- dependency lock hashes;
- builder/runner identity;
- toolchain versions;
- artifact SHA-256 and size;
- release manifest and embedded version;
- platform signature/notarization result;
- SBOM/provenance location;
- install/start/readiness result;
- fixture schema upgraded;
- backup/restore verification;
- failed-activation rollback result;
- final publish destination and digest.

This evidence belongs to the artifact. A workflow badge or release-note claim is
not a substitute.

## Lesson Cluster 8: Verify the Artifact's Runtime Property

### Extracted principle

The Windows release checks for the exact Polars compatibility binaries in the
PyInstaller output. This is a small but powerful habit.

### Aletheia adaptation

Final package checks should inspect and, where safe, execute the immutable
artifact:

- parse and verify manifest;
- compare embedded source/version identities;
- verify every declared file/hash and reject undeclared files;
- import native dependencies inside the package;
- launch with an isolated data root and loopback listener;
- query liveness, readiness, version, and business health;
- confirm no production secrets/config were copied;
- stop cleanly and verify no orphan process or locked data file remains.

Package checks should not reach exchanges or use real credentials.

## Lesson Cluster 9: User Data Must Outlive Program Files

### Extracted principle

TickFlow's Windows installer shows good intent: preserve data by default and ask
before deletion. The macOS layout shows why this intent needs a platform-
independent invariant.

### Aletheia adaptation

Keep mutable runtime state in the platform data root and immutable releases in a
separate release root. Show both paths in doctor/status. Backup manifests should
record the application version, data schema, created time, source runtime id,
and checksums. Uninstall and cleanup must never infer authority to delete data
from authority to remove program files.

## Lesson Cluster 10: Migrations Need Identity and Recovery

### Extracted principle

Ad hoc migrations are easy to add and hard to reason about later. Comments
describe intent but cannot prove what ran on one user's data.

### Aletheia adaptation

Aletheia already has the correct foundation: immutable migration ids/checksums,
writer admission, timings, backup/restore, and packaged preflight. Preserve it.

Future dataset migrations should additionally distinguish:

- authoritative versus rebuildable data;
- online versus offline migration;
- compatibility range;
- expected size/time budget;
- validation query/hash;
- resume cursor;
- rollback versus rebuild policy;
- operator-visible degraded state.

## Lesson Cluster 11: Test the Invariant That the Optimization Risks

### Extracted principle

TickFlow verifies that cached and uncached backtests produce the same trades,
equity, drawdown, and statistics. This is more precise than a generic “cache
works” test.

### Aletheia adaptation

When optimizing, identify the semantic risk:

| Optimization | Equivalence proof |
|---|---|
| cache | same authoritative value/provenance at same as-of |
| batch insert | same rows, ordering/identity, and transaction outcome |
| vectorization | same decisions and boundary rounding |
| parallel collectors | same dedupe, precedence, and commit identity |
| stale-while-revalidate | stale state visibly labeled; fresh state converges |
| precomputed summary | same included/excluded source population |
| alternate provider | same normalized schema plus explicit source difference |

Performance assertions should be coarse and stable unless running in a dedicated
benchmark environment. Semantic equivalence belongs in normal regression tests.

## Lesson Cluster 12: Small Checks Beat Ritual Test Running

### Extracted principle

The research did not run pytest, and that was correct for the task. Static
analysis exposed significant assurance gaps that a broad green suite would not
automatically reveal: raw secret readback, SSRF policy absence, release-gate
absence, and macOS data placement.

### Aletheia adaptation

Use the narrowest evidence that answers the question:

- CodeGraph/source trace for ownership and call paths;
- schema/source inventory for response and credential contracts;
- workflow inspection for actual gates;
- manifest/source comparison for version identity;
- a focused contract test when implementation changes;
- packaged smoke only when validating the final artifact;
- broad tiers in CI, not repeatedly during exploratory reading.

Test execution is evidence after a hypothesis, not a substitute for forming one.

## Suggested Assurance Architecture

The combined design can be expressed as five connected contracts:

```text
Request / Trigger
  -> Correlation + Authority Contract
  -> Bounded Run Contract
  -> Authoritative Commit + Provenance Contract
  -> Operator Status + Recovery Contract
  -> Verified Artifact + Upgrade Contract
```

### Correlation and authority

- server-derived actor/source;
- authentication mode and effective listener;
- stable request/causation/run ids;
- credential references only;
- destination-policy decision.

### Bounded run

- admitted/queued/running terminal states;
- capacity owner and queue bound;
- phase/resource measurements;
- cooperative cancellation and hard isolation where needed;
- honest partial and timeout semantics.

### Commit and provenance

- single authoritative writer or transaction;
- committed sequence/version;
- source/as-of/observed/persisted times;
- runtime session and producer commit;
- cache invalidation after commit.

### Status and recovery

- liveness/readiness/business health separated;
- coverage and freshness visible;
- safe error code/action and correlation id;
- resume/retry/rollback capability accurately stated;
- bounded redacted diagnostics.

### Artifact and upgrade

- locked source/dependencies/toolchain;
- immutable manifest and signature;
- final-artifact property tests;
- migration preflight on verified data copy;
- platform data separation;
- health soak and backup-based rollback.

## Research Reflections

### The polished part is not always the strongest boundary

The reference project's UI makes the application feel mature because it exposes
many controls and states. The deepest risks were found behind similarly polished
surfaces: a monitoring settings form returned bot secrets, a friendly custom-
source editor granted arbitrary outbound authority, and a desktop release flow
made a cross-platform data-preservation promise that only the Windows installer
implemented safely.

The practical reflection is to follow authority and durability, not visual
complexity. A small settings field can be a larger security boundary than an
entire charting subsystem.

### Comments are valuable evidence, including contradictory comments

TickFlow contains unusually candid comments about rate-limit scope, AST sandbox
limitations, optional-plugin legal risk, and packaging decisions. These comments
made the intended design discoverable. They also exposed drift: CORS comments
describe header auth while the app uses cookies, and a platform-neutral data
claim sits on platform-specific installer behavior.

Comments should name the invariant and its scope, then contract tests should
bind the statement to source. Otherwise a good explanation can become a stale
assurance claim.

### Aletheia should borrow simplicity, not simplification

TickFlow often solves user communication with a very small mechanism: a bounded
stage log, a coverage dictionary, a cache metric, a lazy import, a final-artifact
file assertion. Aletheia can become easier to operate by adopting those small
mechanisms.

It should not simplify away the controls demanded by a trading system:
permission gates, provenance, single-writer state, checksummed migrations,
verified backup, signed updates, and rollback. The desired result is simpler
operator understanding resting on stronger internal proof.

### Assurance quality is the distance between a claim and its proof

The most reusable metric from this domain is conceptual:

- “supports macOS” is weak until the package installs and starts;
- “data survives upgrade” is weak until replacement and restore are tested;
- “secret” is weak until read APIs cannot return it;
- “cancelled” is weak until execution and writes stop;
- “healthy” is weak until the endpoint declares which layer it measures;
- “tested” is weak until the exact source/artifact identity and result are bound.

Reducing that distance is the purpose of engineering assurance.

## Final Extraction Position

From TickFlow, Aletheia should take:

- visible stages and coverage;
- focused domain telemetry;
- discoverable hot-path design;
- measured lazy loading and virtualization;
- final-artifact compatibility assertions;
- accessible release communication;
- optimized-versus-authoritative semantic tests.

From Aletheia, the combined design must retain:

- loopback-first and default-deny authority;
- credential write-only semantics;
- redacted bounded diagnostics;
- durable provenance and audit;
- locked verification gates;
- checksummed migration history;
- verified backup/restore;
- signed immutable release identity;
- preflight, health soak, and rollback.

The reference project's assurance gaps are as valuable as its strengths. They
show exactly where a high-quality Node/Python application can look complete
while still lacking a system-wide proof. That boundary is now explicit and can
guide the final cross-domain synthesis.
