# ADR-0001: Source-first Python Runtime Convergence

```yaml
id: ADR-0001
status: accepted
created: 2026-08-19
domain: runtime
supersedes: []
superseded_by: []
related_issues:
  - "https://github.com/iridyne/aletheia/issues/657"
  - "https://github.com/iridyne/aletheia/issues/686"
related_contracts: []
```

## Context

An earlier architecture assessment (baseline `504b215f853d6d78b180c492dd3370e9a5a8e903`)
considered replacing the Rust/Python host layers with a TypeScript/Node.js Guardian that
would own process supervision, lifecycle, and front-door routing. The assessed surface
included approximately 7.6k direct LOC across native host, PyInstaller construction, and
experimental Runtime-continuity paths.

The recommendation was predicated on assumptions that changed after owner clarification:

- The product is operated by one technical owner, not a consumer desktop audience.
- A source checkout with pinned developer tooling is acceptable.
- `uv run aletheia gateway start/status/logs/stop/restart` already covers the essential
  operator path.
- Cross-platform packaging, native tray affordances, app signing, self-update, and
  zero-downtime Runtime promotion do not improve trading outcomes.
- Node would remain an additional prerequisite (Dashboard and Operator Shell already use
  TypeScript, while Python is still required for the Core).
- Migrating the supervisor introduces a compatibility period and a new proxy/process owner
  before any old owner can be deleted.

The recurring maintenance burden comes from the number of lifecycle/distribution modes and
ownership edges, not from Python as a language in isolation. Issue #686 is the durable
superseding conclusion for this decision.

## Decision

**Converge on a source-first Python Runtime. Do not implement a TypeScript/Node.js Guardian
for process supervision or lifecycle ownership.**

The higher-value move is to delete unsupported distribution/lifecycle modes first, keep the
already-proven Python Runtime owner, and reconsider Node only if the smaller remaining Python
lifecycle becomes a measured source of recurring defects.

### Selected stable host

| Role | Platform | Background operation |
|---|---|---|
| Development / control | macOS (developer machine) | Interactive login; `uv run aletheia gateway start` |
| Stable production | Fedora Linux (owner-selected remote) | Systemd user session or explicit start; pre-login `LocalService` deferred (see below) |

The Windows pre-login `LocalService` path is conditionally retained until one of the following
is explicitly accepted by the owner:

1. login-after-reboot is acceptable for the selected host;
2. Windows is no longer the unattended stable host; or
3. another already-proven owner supplies the required reboot recovery.

### Source-distribution prerequisites

| Prerequisite | Version | Source |
|---|---|---|
| Python | 3.12 | `uv` managed |
| uv | pinned in `.python-version` / `pyproject.toml` | `astral-sh/uv` |
| Git | any recent | OS package manager |
| Node.js | CI only (Dashboard compile) | Not required on a running host |

Consumer installers, signed app bundles, self-update channels, and guaranteed macOS/Linux/Windows
three-OS parity are **not** current distribution promises and must not be implied in docs or UI.

### Authority boundaries (unchanged)

- Python remains the only business-state implementation authority.
- API and collector retain the current cross-process writer permit.
- TypeScript is appropriate for Dashboard and Operator Shell only.
- No Node/Rust/browser process opens SQLite or writes config, secrets, audit, trading,
  accounting, Risk, or Execution state.
- Stable-instance pinning: merge or movement of `master` must never imply deployment,
  promotion, or restart.

### Candidate retirement surface (deferred to R1–R6 under #657)

The following paths are candidates for retirement but must not be deleted before reachability,
replacement, rollback, and target-runtime proof per Issue #686 work packets R1–R6:

- Rust tray/native CLI host
- PyInstaller and packaged-app construction
- Packaged signing, update feed/manifest, activation, automatic update, and rollback
- Experimental candidate/shadow/hot Runtime promotion and dual-backend continuity paths
- Obsolete docs, tests, dependencies, and workflows supporting only those capabilities

Deletion is gated on explicit activation under #657. Newly discovered defects are classified
separately through the bug-triage protocol and must not broaden a deletion PR.

## Alternatives Considered

**TypeScript/Node.js Guardian:** Assessed as adding prerequisite surface and a compatibility
transition period without proportional benefit for a single-owner, source-checkout deployment.
Node would remain a runtime prerequisite even after migration because Dashboard and Operator
Shell already use TypeScript; Python would still be required for the Core. Rejected unless
the post-deletion baseline reveals the surviving Python supervisor as a measured, recurring
dominant defect source.

**Docker/container-first distribution:** Not considered for the current MVP; introduces its
own orchestration surface without reducing trading-state ownership complexity.

**PyInstaller consumer distribution (status quo before convergence):** Retains signing,
update-feed, and multi-OS complexity without matching operational need. Scheduled for
retirement under R3–R4.

## Consequences

- Future agents must not re-propose a TypeScript/Node.js Guardian without evidence from the
  post-deletion baseline and explicit owner authorization.
- Capability Ground Truth entries for packaged runtime capabilities (RT-01, RT-13) remain
  unchanged until the code cutover that retires each path.
- PR and issue work targeting Runtime surface retirement proceeds one work packet at a time
  under #657.
- The `uv run aletheia gateway` CLI is the canonical operator path for all documentation,
  setup instructions, and health-check references.

## Revisit Conditions

Reconsider Node Runtime management only if at least one real requirement appears:

- After peripheral deletion, the remaining Python supervisor is a measured, recurring
  dominant defect source.
- A supported non-technical audience or public distribution channel requires a different
  installer/runtime envelope.
- Multiple stable operating systems become a supported contract rather than best-effort
  portability.
- A measured front-door/supervision bottleneck cannot be fixed locally.
- Node can demonstrably delete more ownership surface than it adds, with a bounded
  compatibility window.
- A new availability requirement cannot be satisfied safely by the surviving Python/source
  Runtime.

Any future proposal must compare against the post-deletion baseline, keep Python as the
durable state/trading authority, prove real-host process behavior, and earn explicit owner
authorization.
