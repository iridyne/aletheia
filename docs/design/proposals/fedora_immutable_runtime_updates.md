---
id: fedora-immutable-runtime-updates
status: draft
related_contracts:
  - docs/product/ACTIVE_RUNTIME_SCOPE.md
  - docs/operations/deployment/DEPLOYMENT.md
supersedes: []
---

# Fedora immutable Runtime updates

## Summary

Consider replacing the owner-triggered Fedora source-checkout updater with a
signed, immutable Linux Runtime and a headless activation host. This is a
cross-domain trust-boundary change spanning release authorization, frontend and
backend identity, SQLite migration, service ownership, and rollback. It is not
implemented or accepted by this draft.

## Current fact

Fedora is the owner-selected long-running Paper runtime. Deployments use the
human-triggered source-checkout SOP in
[`DEPLOYMENT.md`](../../operations/deployment/DEPLOYMENT.md). `UpdateConfig`
explicitly forbids daemon use. The current updater has no cross-process update
lock, CI-candidate authorization, automatic provenance and settlement gate, or
safe Git-only recovery after an applied schema migration. The macOS
`aletheia-tray` is not a Linux service host.

## Goals and non-goals

Goals:

- bind backend code and built `dashboard_static` to one exact release manifest;
- accept only an owner-approved, signed exact commit;
- preserve one service owner across stage, quiesce, migrate, activate, verify,
  and rollback;
- fail closed while leaving durable, operator-visible failure evidence;
- prove commit, schema, provider, scheduler, settlement, Paper mode, and Live-off
  state after activation.

Non-goals:

- enabling Live or changing Risk, provider, trading authority, or policy epochs;
- opening an Observation window automatically;
- treating a merge, download, or process start as a successful deployment;
- modifying source code or building frontend assets on the stable runtime.

## Proposed design

1. A release workflow builds a `linux-x86_64` artifact only for an exact commit
   that passed existing CI and the owner release gate. Backend code and
   `dashboard_static` share one manifest and Ed25519-signed feed entry.
2. A headless Linux stable host runs under `systemd --user` and uniquely owns
   stop, select, migrate, start, and rollback. The host records the selected
   release and every activation result durably.
3. A timer performs signed-feed check and staging only. It verifies manifest,
   hash, signature, disk capacity, and a restorable database backup; then it
   preflights migrations on a copy and atomically writes `update-request.json`.
   Network, signature, capacity, or preflight failure leaves the current Runtime
   untouched.
4. The stable host quiesces new work, selects the staged immutable release,
   applies the preflighted migration to the authoritative database, starts the
   Runtime, and runs a bounded health soak.
5. Activation succeeds only when the observed commit, migration ledger,
   provider, scheduler, outcome settlement, `paper_internal`, and
   `live_disabled` facts all match the approved release contract. Otherwise the
   host restores the previous release and, where schema recovery requires it,
   the verified pre-update backup.
6. Timer execution uses a single-instance lock, `Persistent=true`, randomized
   delay, and bounded backoff. Rollout begins check-only, advances to canary
   staging, and permits automatic activation only after recovery drills pass.

## Trust and complexity review

The proposal creates a new release-signing authority, durable activation
request, Linux service owner, and database recovery path. Splitting these across
independent scripts would permit frontend/backend drift and competing lifecycle
owners. Reusing the current source-checkout updater as a daemon is insufficient:
Git cannot reverse an applied schema migration, and its human-triggered contract
has no signed candidate or post-activation economic-health gate.

Credentials remain write-only and outside artifacts, logs, manifests, and Git.
The stable host receives no authority to change Live, Risk, provider, or trading
policy.

## Verification contract

Before this design can become Operations or Product Truth, executable evidence
must prove:

- signature and exact-commit rejection paths;
- one manifest for backend and dashboard assets;
- single-owner exclusion across concurrent timer and activation attempts;
- backup restoration and migration-copy preflight;
- crash recovery before selection, during migration, during startup, and during
  health soak;
- exact commit and schema provenance after restart;
- provider, scheduler, settlement, `paper_internal`, and `live_disabled` health;
- unchanged Runtime on check, download, signature, capacity, and preflight
  failures;
- bounded rollback to the previous immutable release and required database
  backup.

A Fedora canary must complete the same recovery drills before automatic
activation is enabled.

## Rollout and rollback

Keep the current owner-triggered SOP authoritative while the proposal is draft.
Introduce artifact production and check-only feed verification first, then
canary staging, then supervised activation. Automatic activation remains off
until the verification contract passes on the owner-selected Fedora runtime.
Any failed gate returns selection to the prior immutable release and restores
the pre-update database backup when schema state is not backward-compatible.

## Resolution

Draft. If accepted and implemented, move the final operating procedure to
`docs/operations/deployment/DEPLOYMENT.md`, the release and recovery invariants
to their owning Contracts or Decision, and mark this proposal `implemented`.
